# Operations

How the gateway is switched on and off, how it runs as a service on each OS, how it restarts without
dropping a request, and how to run or try one by hand.

## Install and upgrade

The gateway has no installer of its own. `ak setup` (pick `gateway`) runs it from the folder `gateway/`
in Claude Code's clone of the kit: no copy is unpacked, no `cc-gateway` launcher is written to
`~/.local/bin`, and the commands are `ak gateway …`. It needs node ≥ 22.7 on PATH.

```sh
ak gateway account login        # a browser, once per account
ak gateway enable               # the service, now and at every login
```

**Upgrade:** Claude Code updates its clone of the kit; then `ak gateway restart` — the service is
handed to the new code with no gap.

**Uninstall:**

```sh
ak gateway disable              # routing off, service stopped
ak gateway service uninstall    # remove the service unit
ak setup undo                   # take back what setup put down
```

Your accounts, config and trace stay; delete them yourself if you want them gone:
`~/.config/ak/gateway.json`, `~/.config/ak/accounts.json`, `~/.local/share/ak/gateway/`.

## Platform support

| | macOS | Linux | WSL | Windows |
|---|---|---|---|---|
| `ak setup` | yes | yes | yes | no — run `src/cli.ts` from a checkout |
| service | launchd | `systemd --user` | with `systemd=true` in `/etc/wsl.conf` | Task Scheduler |
| service tested against | the real launchd | a fake `systemctl` | — | a fake `schtasks` |
| CI | yes | yes (ubuntu) | — | — |
| `xray` of an interactive session | yes | yes (`script` from util-linux or busybox) | yes | no — `-p` only |

On Windows, from a checkout: `node --experimental-strip-types --no-warnings src/cli.ts <verb>`.

## On and off

```sh
cc-gateway              # one line: on or off, how many accounts, what to type next
cc-gateway enable       # on:  the service runs, and every new claude goes through it
cc-gateway disable      # off: routing removed, service stopped — claude uses its own login
```

```
   on    claude → :4747 → Anthropic      the gateway picks the account and injects its token
   off   claude → Anthropic              as if the gateway had never been here
```

`enable` is two halves, in order: the service (installed the first time, loaded after — launchd,
systemd or Task Scheduler, see [One half at a time](#one-half-at-a-time)), then — once the daemon
answers — the cc-shim fragment that routes terminal `claude` to it. `disable` undoes them the other way
round: routing first, so nothing launched mid-switch lands on a gateway that is gone. It refuses to
turn on with no accounts to serve. `status` names a half-on state and the verb that settles it. Both
live in `src/power.ts`.

**Without cc-shim**, the fragment is written but nothing runs it: a plain `claude` still uses its own
login. `run [--account NAME] -- claude …` launches a program with the environment the fragment would
give it — the gateway as base URL, the doorman pass (`ANTHROPIC_AUTH_TOKEN=gateway-doorman`, a
placeholder: the gateway injects the real token), no inherited credential, `x-brain-origin: cli`
and `x-brain-project: <folder>`, and with `--account` a `/tc-acct/<name>` pin the gateway resolved.
It refuses when the gateway does not answer rather than fall back to claude's own login.
`env [--account NAME]` prints the same environment as `export` / `unset` lines, for a shell routed by
hand: `eval "$(cc-gateway env)"`. `env --json` is the program-facing form: just the variables that route
model calls (no identity headers), `{}` while the daemon is not running ([commands.md](commands.md#for-a-program)).

**Running sessions do not move.** Env is read at launch: only the next `claude` picks up a switch.
Sessions started while it was on still point at it after `disable` — restart them.

**Claude Code behaves differently behind any gateway** — it turns off MCP tool search (every tool's
full definition on every request: about half the prompt, measured) and its routing hints. The routing
turns both back on alongside the base URL (`src/claude-env.ts`), and a user's settings.json still
wins over it; `status` names any setting that does (`shadowed …`). The whole story, measured and
sourced: [claude-code.md](claude-code.md).

## Restarts without a gap

```sh
cc-gateway restart            # run this copy's code: handed over, nothing refused, nothing cut
cc-gateway rollback           # back to the build before, the same way
```

After Claude Code updates the kit's clone, `ak gateway restart` hands the service over to the new code.

```
   service manager ── keeper   (keeper.ts — holds :4747, never serves, never swapped under traffic)
                        ├─ build A   finishing the replies it has open, taking nothing new, then exits
                        └─ build B   every new connection from the moment it is up
```

The service manager runs the **keeper**, not the gateway. The gateway is the keeper's child — a
cluster worker that accepts straight off the shared socket (`SCHED_NONE`), so the keeper is not in
the request path: measured on the real gateway, 0.23–0.28 ms per request either way. `restart` copies
the code into a **release** (`<dataDir>/releases/<id>`, id = time + commit, `-dirty` for uncommitted
changes), names it in `<dataDir>/wanted` and asks the keeper to roll by writing `roll` into
`<dataDir>/control`.

**The control channel is a file, not a signal**, so it means the same on Windows, which has no SIGHUP
and whose `kill` is always a hard kill. The keeper polls `control` (a stat every 200 ms,
`fs.watchFile` — the same on every OS and filesystem) and reads its first word: `roll` starts what
`wanted` names, `stop` drains every worker and exits 0. Each request is a new file (temp + rename), so
two inside one mtime tick are both seen; a request left from an earlier keeper is never replayed.
`keeper.json` says `control: true`; a keeper from before it gets the SIGHUP it knows. On POSIX the
signals still work (SIGHUP rolls, SIGTERM and SIGINT stop). A worker's `POST /_gateway/stop` (loopback
only, and a web page is refused at the front door) asks the keeper to stop too; a gateway started by
hand stops itself on it.

The keeper starts the new build beside the one serving, lets both serve for a moment (1.5 s
probation), and only then does the old one stop accepting, finish every open reply (15 min cap) and
exit. The answer lands in `<dataDir>/keeper.json` once the swap is done — `restart` waits for it,
`status` shows it. Starts and swaps run one at a time, so a restart that lands during crash recovery,
or a stop during a restart, cannot leave two builds serving.

- **A broken build never replaces a working one.** One that exits or does not bind within 20 s, or
  dies during the probation, is reported with its last log lines; the old one never stopped serving,
  and `wanted` goes back to it so a later keeper boot does not try the broken one first.
- **A crash comes back.** The serving worker dying is restarted from the same release at once, then
  backing off; three deaths inside a minute and the build before takes over (and becomes `wanted`).
  Between the death and the restart the port is dark — the one gap a keeper cannot close.
- **A merge changes nothing live.** What runs is always a release, never the checkout — a branch
  switch, a half-saved file or a deleted worktree cannot reach the service until the next `restart`.
  The keeper itself runs from its copy at `<dataDir>/keeper.mts`, put there by `service install`.
- **Tokens change hands in order.** A worker under the keeper boots without the right to refresh or
  write a token and serves with what the file holds; the keeper hands it over (`adopt`) only after
  the outgoing one has stopped starting refreshes, let the ones under way land in the file, and said
  `quiet`. The file is written by rename, so neither ever reads half of it. Two engines never rotate
  the same grant.
- **Kept-alive connections are not cut.** The outgoing worker leaves them open: the next request on
  one is answered with `connection: close`, an unused one retires at the 5 s keep-alive timeout every
  reply announced. Closing them outright would race a client writing into one.

The keeper cannot hand over to itself. Code whose `keeper.ts` differs from the installed copy — and a
gateway from before the keeper — gets one real restart instead: `restart` boots the build once on a
spare port (a build that cannot start stops it there, nothing touched), waits for a moment with
nothing in flight — the serving build's replies and any older build still finishing — then
reinstalls the unit (on macOS waiting out launchd's own settling between unload and load;
`systemctl stop` is synchronous; on Windows a stop request, then `/End`). `--now` skips the wait.

Measured against real launchd on a throwaway unit (systemd and Task Scheduler are so far checked only
against a fake `run`, `test/service.test.ts`): a hand-over, a second one and a rollback each answered
every one of ~260 health checks, 0 ms dark; the one real restart was dark for ~0.4 s, which claude's
own retry covers. `restart` refuses a git worktree — it would put unmerged code on the wire; a main
checkout, and a clone Claude Code keeps up to date, are fine.

## Try a gateway without touching the real one

```sh
cc-gateway try                        # first run opens a browser login, then launches claude
cc-gateway try -- -p "say pong"       # anything after -- goes to claude
cc-gateway try --login                # add another account to the trial pool (rotation)
cc-gateway tui --trial                # while one runs: the trial's screen, bars, traffic
```

`try` runs one claude through a **second** gateway on the next port up (`:4748`), with its own
accounts file and trace under `<dataDir>/trial/`. It starts when `try` does and stops when that
claude exits, then says what went through, which account served it, and whether the token refreshed.
A second `try` while one runs joins it; a trial whose `try` was killed is adopted and stopped by the
next. Run from a development checkout, `try` uses that checkout's code — so it is how a change to the
gateway gets a real session before it reaches the service. `--port P` picks another port, `--bin B`
another claude, `--name N` names the login.

The trial's account is a **fresh browser login**, its own grant, so nothing the trial refreshes can
touch the real gateway's tokens. The claude it launches gets the routed env a terminal launch gets
(`routedEnv`, `src/claude-env.ts` — shared with `probe`, `run` and `xray`), with `CC_SHIM_DISABLE=1`
(when `claude` is cc-shim, its system prompt still applies, its routing fragments do not), and
carries `x-brain-origin: trial` plus a run id the summary counts by.

`--trial` aims `status · account · tail · tui · probe · env · reload` at the trial. The verbs that
manage the real gateway — `enable disable restart rollback start stop service shim` — refuse it: under
the service manager they would reach the real unit whatever port you named. The trial is stopped with
`POST /_gateway/stop`, a SIGTERM only when it does not answer.

## One half at a time

```
   service   the keeper's unit     restarted after a crash, started at login or boot
   terminal  cc-shim fragment 05-  claude → :4747, fails open when the gateway is down
```

```sh
cc-gateway service install|uninstall|status   # the unit alone (never from a worktree)
cc-gateway shim on|off|status                 # the routing alone
cc-gateway start [--detach] · stop            # the process — through the service manager once installed
cc-gateway doctor                             # can this machine run the service, and what to do if not
```

### One interface, three backends

`src/service.ts`, picked by the OS. What is common stays there — which node runs it
(`$AK_GATEWAY_NODE`, the running node, `~/.nvm`, Homebrew, PATH), the command, the port check, the
release the keeper runs; each backend writes one file and drives one tool, through an injectable
`run` so a test drives it with a fake:

```
   macOS     launchd          ~/Library/LaunchAgents/com.ak.gateway.plist   KeepAlive, ThrottleInterval 5
             (service-launchd.ts)   launchctl bootstrap · bootout · kickstart · print (the pid)
   Linux     systemd --user   <configDir>/systemd/user/ak-gateway.service    Restart=always, RestartSec=5,
             (service-systemd.ts)   KillMode=mixed (SIGTERM to the keeper alone; it drains), log appended
                              daemon-reload · enable --now · stop · show -p MainPID; enable-linger — refused
                              (no polkit: headless, ssh) it still installs, with a `warn:` naming
                              `sudo loginctl enable-linger <user>`, since without linger it stops at logout;
                              `doctor` repeats it
   Windows   Task Scheduler   \ak-gateway, XML kept at <dataDir>/ak-gateway.task.xml   at logon, restart on
             (service-schtasks.ts)  failure every 1 min ×999, no time limit, run only while logged on, under
                              `conhost --headless` (no window); /Create /XML · /Run · /End · /Delete · /Query.
                              The pid is the keeper's own pid file; its log and env are keeper flags
                              (`--log`, `--env`), since a task neither redirects output nor sets env.
```

**The unit's environment** is a minimal PATH and HOME (POSIX), plus whatever of this shell decides
where the gateway's files are, when set: `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`,
`CLAUDE_CONFIG_DIR`, and the config overrides `loadConfig` reads (`AK_GATEWAY_CONFIG`,
`AK_GATEWAY_ACCOUNTS_FILE`, `AK_GATEWAY_DATA_DIR`, `AK_GATEWAY_PORT`, `AK_GATEWAY_ACCOUNT`,
`AK_GATEWAY_CAPTURE`) — so the keeper launchd or systemd starts reads the files the CLI wrote
(`carriedEnv` in `src/service.ts`). Change one of them and `cc-gateway service install` rewrites the
unit with it.

**Stop is not a signal.** Under every manager an exit is a restart. `stop` (and `disable`) is the
manager's own stop — `bootout`, `systemctl --user stop`, or a `stop` in the keeper's `control` file
and then `schtasks /End` — and the keeper drains its worker (3 s cap), writes the quota state and
exits; stopped until `start`, `enable` or the next login. `service uninstall` is what removes it. A
gateway started by hand (`start`, `try`) is stopped with `POST /_gateway/stop`, a signal only when it
cannot answer.

**WSL** is Linux, and runs the service only with systemd switched on. Without it `doctor` and
`enable` say so and how to fix it — `[boot]` / `systemd=true` in `/etc/wsl.conf`, then
`wsl --shutdown` — and `cc-gateway start --detach` runs the gateway without a service meanwhile.

**Who holds the port** is asked per OS (`src/procs.ts`): `/proc/net/tcp` and each process's fds on
Linux, `netstat -ano` on Windows (a listener told by its foreign port 0, since the state word is
translated), macOS's own tool there; where none can name the holder, a bind on the port says whether
it is held. The holder's command line comes from `/proc/<pid>/cmdline`, CIM, or `ps`.

**Owner-only files on Windows** (`src/fsperm.ts`): the mode bits (0600 files, 0700 folders) do
nothing there, so the gateway's own folders under `%APPDATA%` and `%LOCALAPPDATA%` get an ACL that
grants their owner alone, inherited by every file made in them —
`icacls /inheritance:r /grant:r USER:(OI)(CI)F`, best effort. An accounts file pointed elsewhere gets
the ACL on the file.

### Fail-open and the shim fragment

Gateway down → the shim fragment exits without claiming → the next fragment, or claude's own login,
takes the launch. Nothing breaks; the gateway just isn't there. (`cc-gateway run` is the opposite on
purpose: it refuses rather than fall back.)

The fragment is `05-gateway.mjs` in cc-shim's conf.d (`<config>/cc-shim/conf.d`: `~/.config` or
`$XDG_CONFIG_HOME`, `%APPDATA%` on Windows), generated by `src/shim.ts` (rev 5) and native on every
OS: cc-shim runs it in a worker thread, it probes `/_gateway/health` with `fetch` (one second),
resolves a `--account` name through `/_gateway/resolve` (two), and leaves `ANTHROPIC_BASE_URL`, the
doorman token, the `x-brain-origin`/`x-brain-project` headers, CLAUDE_ENV's unset entries,
`CC_SHIM_UNSET` and the claim in `process.env` for the shim to import. It replaced rev 4's bash
`05-brain-gateway.sh` (/dev/tcp and curl): `shim on` removes that file, `shim off` removes both,
`shim status` reports a leftover one as stale. A cc-shim installed before its Node rewrite runs only
`*.sh` — reinstall it first.

## Run it by hand

From a checkout, with no install and no service:

```sh
node --experimental-strip-types --no-warnings src/cli.ts start --detach
node --experimental-strip-types --no-warnings src/cli.ts probe      # real headless claude through it
node --experimental-strip-types --no-warnings src/cli.ts tail
node --experimental-strip-types --no-warnings src/cli.ts stop
```

`npm run cli -- <verb>` is the same. `AK_GATEWAY_CONFIG=./example.config.json` loads the sample policy
without installing anything. `AK_GATEWAY_ACCOUNTS_FILE`, `AK_GATEWAY_PORT`, `AK_GATEWAY_ACCOUNT`,
`AK_GATEWAY_DATA_DIR`, `AK_GATEWAY_CAPTURE` override one field each; `upstreamUrl` in the config file
points the engine somewhere other than `https://api.anthropic.com`.

## Probing

`probe [--model M] [--prompt P] [--bin B]` runs one real headless `claude -p` through the running
gateway (default model `claude-haiku-4-5-20251001`, prompt "Reply with exactly one word: pong"),
tagged `x-brain-origin: probe` and `x-brain-agent: gateway-probe`. It sets `CC_SHIM_DISABLE=1`.
Without it the cc-shim fragments pick `ANTHROPIC_BASE_URL` themselves, and the probe measures
whatever they chose — an empty or suspiciously normal capture is almost always this.
