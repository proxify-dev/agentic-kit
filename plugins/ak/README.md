# ak — the host

The command every kit plugin's verbs mount into, and the tooling around a session. It knows no plugin by
name: each one declares a `cli` block in its plugin.json — `{command, module | entry, attr, hidden?, hoist?}`,
one entry or a list — and `ak` mounts it at start (`src/ak/cli.py: _mount_plugin_clis`) when the plugin is
present AND enabled in Claude Code (`enabledPlugins`, user · project · local settings). The id read is the one Claude
Code has for that folder, whatever the marketplace (`plugin_inspect/installed.py: ids_for` — an installed_plugins.json
installPath that is the folder, else the marketplace in known_marketplaces.json whose clone holds it): `tracer@ak` from
the release clone, `tracer@agentic-kit` in the public kit. A folder nothing on disk names (a worktree) reads any
`tracer@…`, on when any is true. When enabledPlugins cannot be read — no settings naming a plugin,
`$AK_PLUGINS_DIR`, pytest — present is mounted. A plugin
switched off leaves no verb, no root Examples line and no `COMMAND_GROUPS` entry; its extras (`ws move` on the
vault's `workspaces`) go with it. A module may also expose `brain_status()` (a row on the bare-`ak` band),
`brain_boot(ctx)` (runs before any verb) and `brain_search(words, limit)` (its rows in `ak search`).

**`~/.ak` is ak's home** (`src/ak/home.py`): before any verb, and before any plugin's `brain_boot`, ak plants
the layout-2 skeleton — `vault/ db/ cache/ moves/` and the `layout` stamp — from the layout `move/plan.py`
declares. Never over anything: a folder is a mkdir, the stamp is written only when absent, and a home that
holds something but no stamp (layout 1) is left as it is for `scripts/migrate/4-home-layout.py`. The vault's
boot then plants only its notes into `~/.ak/vault`; with no vault plugin, `ak doctor` passes on the skeleton.

```
   ak search (s)                            every plugin that can search, at once (each one's brain_search)
   ak shim · gateway · xray · sysprompt     the `claude` you launch, the model-call wire, the prompt it sends
   ak hooks on|off|show                     hook tracing
   ak ws move · moves · doctor · home        moving a workspace and everything that points at it (src/ak/move/)
   ak plugin ls · inspect · log · rename     what your plugins put in front of your agent (src/ak/plugin_inspect/)
   ak sessions … · trace … · memory … …      mounted: each plugin's own verbs
```

`ak -h` groups the root in four sections — Everyday, Groups, Setup & state, Plumbing (`help.py: COMMAND_GROUPS`).
`src/ak/groups.py` holds what every group shares: `FuzzyGroup` (exact name → alias → unique prefix → a verb
typed one level too high, run from where it lives when it only reads: `ak replay x` prints `→ ak sessions replay
x`), `writes` (a verb that changes something: ✎ in every list, never run on a guess) and `hidden_alias` (an old
spelling that keeps working, out of help and suggestions).

## Setup

`ak setup` is the kit's one installer. It looks at the machine, lists the components it can install (the ones it can't are greyed out with the reason), previews every settings and shell-rc change as a diff, applies in dependency order, then checks the result.

A fresh machine has no `ak` yet, so it starts from the public kit, `proxify-dev/agentic-kit`:

```
uvx --from "git+https://github.com/proxify-dev/agentic-kit#subdirectory=plugins/ak" ak setup
```

At a terminal it opens a picker with five screens, numbered 1/5 to 5/5: check (what is on this machine, held until `enter`: only the tools something in this kit runs on, and the size of your Claude Code history; a missing uv, git or claude is named with where to get it), choose (one row per plugin with its one-line `setup.summary`, under a heading that says where it acts: inside Claude Code, or around the `claude` you type; setup's own clone, marketplace, the `ak` command and the ak plugin are one `ak` line, always installed, never a row to untick; under the list, the row under the cursor: picked or left out, what you get (`setup.gives`), its first run with the size of your history, what it puts on your machine (`setup.changes`, else "nothing outside Claude Code") and what it brings along; an installed plugin is a ✓, not a box; `space` ticks, `enter` next, `d` the defaults; unticking something others need asks for a second `space`), confirm ("Ready to install": you get (each pick with its summary), then (a first run), on your machine (each pick's `changes`, and a settings.json diff), asks you to (a browser sign-in), left out (each row not picked, and why when it can't run here) — then `enter` to install; an Enter within half a second of arriving, the one that left choose, is ignored; `d` shows each step with its command or settings diff, `b` goes back), install (every step with its state beside the output of the one running; a step that needs the person, a sign-in, gets the terminal under a heading that says what it is and that setup comes back; a step that fails offers `r` retry, `s` skip, `u` undo; while a plugin's first run goes, its row shows its own count and a clock, and `enter` finishes setup and leaves it going) and done (the checks, setup's own and the ak plugin as one `ak` line while they pass; a pick that passes shows its first `gives` line, what you can do with it now; one a new terminal picks up (the shim, `ak` on PATH) is a `·`, not a warning; each first run: done, or still going; `r` goes back to confirm with what still fails, `enter` finishes, and the outcome, each pick's first `gives` line and what to run next stay in the terminal). Anywhere else (a pipe, an agent, `--dry-run`, `--json`, `--only`) it prints the plan and exits 0. Only `--yes` writes (looking at your `claude` can make Claude Code touch its own files, but setup does not).

```
ak setup --dry-run                 the plan and every diff; setup itself writes nothing
ak setup --yes                     the defaults, without asking
ak setup --yes --only memory       just memory, and what it needs (it says what it pulled in)
ak setup --json                    the same data as one JSON document
ak setup --repo URL                clone the kit from URL (else $AK_REPO_URL, the repo uvx built from, the clone's origin)
ak setup status                    what is installed, and the checks on it (exit 1 when one fails)
ak setup undo                      take the newest run back
```

The list is read from the kit on disk, never kept in setup: the checkout `ak` runs from (this workspace, or the release clone), else the release clone. Every plugin under `plugins/` is a component (its `plugin.json`: name, description, `visibility`, `dependencies`, and an optional `setup` block for what the rest can't say: `summary`, `gives` (what you get, one line each, led by the command that tries it), `changes` (what it puts on the machine outside Claude Code's plugin folder), `route` (a piece that changes how `claude` starts: the path it takes once that piece is picked), `needs`, `default`, `note`, `depends_on`, `first_run`; `src/ak/setup/catalog.py` has the shape), and every module in `kit/modules.json` (cc-shim, cc-gateway, pinned). Here every plugin is listed and the private ones say so; in the public kit (`proxify-dev/agentic-kit`) only the public ones exist. An `ak` with no kit on disk yet (the `uvx` line above, before the clone) reads `src/ak/setup/kit.json`, the public kit as `scripts/kit.py render` wrote it.

| component | what it is | needs |
|---|---|---|
| `release` | a clone of the kit at `<data dir>/ak/release`, the folder every plugin installs from | `git` |
| `marketplace` | the `ak` marketplace, pointing at that clone | `claude`; `release` |
| `ak-tool` | the `ak` command, as one uv tool (what `hooks/install-global.py` installs) | `uv`; `release` |
| `ak-path` | uv's tool folder on your shell PATH; skipped when it already is | `ak-tool` |
| `ak` `vault` `memory` | the host (with `ak plugin`), the link engine, memory | each on the one before it |
| `observer` `tracer` | what past sessions learned; which session did what. Each builds its venv now; the tracer then indexes your history (its first run) | `uv`; `ak` |
| `tool-results` `tag` | big results on disk; session tags | `marketplace`, `vault` |
| `notify` | a macOS popup when a session is done | macOS, `osascript` |
| `agentic-engineering` | seven skills (context, harness, skill and agent development, evals, sandboxing, fewer faster tests), most named by a hook at its moment, an agentic-engineer agent, /new-plugin, and /new-agent and /new-skill (they need `settings.function-hooks`, which comes with it) | `marketplace` | | `marketplace` |
| `feed` | off by default | `marketplace` |
| `settings.function-hooks` | `env.CLAUDE_CODE_ENABLE_FUNCTION_HOOKS` in `settings.json`: the commands of agentic-engineering run on it. Comes and goes with that plugin, no row of its own | |
| `settings.file-suggestion` | `fileSuggestion` in `settings.json`: `@` completes vault notes | `vault` |
| `shim` | the `claude` wrapper, cc-shim's own `install.sh` at its pinned version; comes with the gateway, no row of its own (`--only shim` alone) | macOS/Linux, `node` ≥ 22.13, `claude` |
| `gateway` | the model-call wire as a service: cc-gateway's `install.sh` at its pinned version, then `cc-gateway account login` (a browser, when it has no account) and `cc-gateway enable` (off by default) | macOS/Linux, `node` ≥ 22.7, a service manager; `shim` |

**The `claude` you type: the gateway, and the shim that comes with it.** Choose shows one row, `gateway`, off by default: it changes how `claude` starts, so a person picks it. The shim comes with it and has no row of its own (`setup.comes_with`): it is how the `claude` you type reaches the gateway, and what reads `claude --account NAME`. The gateway's row says so (`comes with  claude shim`), lists the shim's perks among its own, and shows how claude starts, marked with the one the picks give; confirm says it again (`claude starts`):

| you pick | the `claude` you type | you get |
|---|---|---|
| nothing here | claude → Claude Code → Anthropic, on Claude Code's own login | Claude Code as it is |
| `gateway` (the shim with it) | claude → shim → Claude Code → gateway → Anthropic, on your accounts in turn | your Claude accounts in one pool, the next one answering when one runs out; `claude --account NAME` starts on one; every request recorded (`ak gateway`, `ak xray`); your own instructions on every start (`ak sysprompt`) and your own scripts before claude starts (the shim's `conf.d`). You sign in once, in a browser (or from a link you paste back, over ssh or in a container); Claude Code's own login is not used, and its banner says "API Usage Billing" |

The shim alone (your instructions and scripts, Claude Code's own login, and `claude --account` refusing: there is no account list to pick from) is `ak setup --yes --only shim`. A gateway with no shim reaches only `cc-gateway run -- claude`; setup does not offer it. `ak gateway disable` gives `claude` its own login back while the shim stays.

A module's `install.sh` is fetched at its tag (`v<version>`) through `gh` when it is logged in, else `curl`, as its README says; undo runs the same script with `--uninstall`. `tasks`, `routines` and `todo` are listed but not selectable: they are not in the marketplace.

**A plugin's first run.** `setup.first_run` (`{"title", "run", "reads"}`) is the plugin's own work once it is installed: the tracer's indexes your Claude Code history (`bin/tracer trace index --all`). It is the last step, started in its own session from the plugin's folder in the release clone, so it keeps going when the person finishes setup before it ends (the TUI's `enter`, or Ctrl-C at the plain door); `ak`'s tracer row says `indexing your history · 12/26 projects` meanwhile. `"reads": "history"` puts the size of `<claude home>/projects` beside the title; with no history there is no step, and the plan says why.

What it leaves alone. A component already in place makes no step, so a second run is a repair. It never pulls an existing clone and never re-points the `ak` marketplace: a marketplace that points elsewhere is a refusal that names the command to remove it. A `settings.json` key that already holds another value stays as it is. The observer's embed is not a step (minutes of work): run `ak observer store embed` later.

**The run folder.** Each `--yes` run is `<kit home>/db/setup/<YYYYmmdd-HHMMSS>/`: `run.json` (the steps and how to take each back), `backup/` (every file a step was about to write, as it was) and `logs/` (each step's output). `ak setup undo [RUN]` walks the newest run back newest step first, and takes back only what that run did: settings keys whose value is still ours, rc lines it added, a file nobody wrote since, the clone when it is clean, what it installed. It runs while Claude Code sessions are open, and a second undo does nothing. It leaves the venv builds (a cache) and the gateway account (`cc-gateway account remove <name>`). `--list` lists the runs, `--dry-run` shows what it would do.

Tested with fake `claude`, `uv`, `git` and `node` on PATH, and run end to end from the `uvx` line on a fresh Debian box with systemd and ~100 MB of real history (every public piece, the gateway's sign-in, then a `claude` call through the shim and the gateway). Not run on Windows, nor on a fresh macOS user.

## Install

`hooks/install-global.py` (SessionStart) puts `ak` on PATH as one uv tool, editable from the release clone:
`ak` with the vault and memory packages installed beside it (`--with-editable ../vault ../memory`,
each when it is on disk). It reinstalls when the source path or any of the three `pyproject.toml` changes,
takes out any other uv tool that puts `ak` on PATH, and never runs in a program's session. The vault's and
memory's hooks run on that tool's Python (`hooks/akpy.py`, canonical here). Inside a checkout, `ak` re-execs
into the checkout's `plugins/ak` (`uv run --project`). ak builds alone — its pyproject names no sibling (uv
locks every group, so a missing `../vault` would fail even an optional one) — and a `module` mount in a
checkout reaches its plugin's own `src/`, put last on `sys.path` (`cli.py: _reach_sources`).

**Which checkout runs.** The installed `ak` is an editable install of one checkout
(the release clone, `RELEASING.md`). Run inside a *different* checkout of this repo — a worktree — it hands off to
that tree's `plugins/ak` first (`uv run --project`, so that tree's own deps apply), and
says so on stderr in a terminal. `AK_NO_REEXEC=1` pins the installed one. `ak gateway`
(and `ak xray`) follows the same rule for the TypeScript CLI it hands off to: `$AK_CODE`, else the checkout
you stand in, else `~/agentic-kit`, else the release clone — so a worktree runs its own gateway verbs. `ak shim`
looks for `shim/cc-shim.mjs` in the same four places, so both work on a machine that has only the release clone. (Installing the service
and `restart` refuse a worktree: they would put its unmerged code on the wire every session goes
through.)

## Moving a workspace

`mv ~/old-name ~/new-name` detaches a folder's whole life: Claude Code files its chat history under
a folder named after the path (`~/.claude/projects/-Users-x-ak`), so `/resume` and `-c` come up
empty; git records every worktree by absolute path; the tracer, the trace store, `~/.claude.json`,
settings and the memory's caches all name it. `ak ws move OLD NEW` moves the folder and every
pointer to it, as one journaled, undoable move (`src/ak/move/`):

```
   ak ws move ~/old-name ~/new-name --dry-run    the plan, nothing changed
   ak ws move ~/old-name ~/new-name              do it
   ak ws move --adopt ~/old ~/new                you already ran mv: fix everything else
   ak moves undo <id>                            put it all back, byte for byte
```

**Pointers move, history stays.** A session folder's name, a `~/.claude.json` project key, a
settings path (`settings.json` and `settings.local.json`: a value that is the folder, and the folder
inside any string — `fileSuggestion.command`, a hook's `command`, an `env` value — at a path boundary:
not glued to a longer name before it, and followed by `/`, the end, whitespace, a quote, or one of
`; : ) | & , >` or a backtick, so `cd ~/agentic-kit;make` and `PATH=/x/ak:/usr/bin` move and
`~/agentic-kit-wt/x` is not under `~/agentic-kit`; a folder under `$HOME` is found as `~/rest` too and stays a
`~/` form, `"~/.ak"` → `"~/.ak"` — written absolute when the new place is outside `$HOME`; the dry run
names each key), the tracer's `project_hash`/`session_file`, the trace store's transcript pointers, the
memory registries (`~/.ak/db/workspaces.json`, `~/.ak/cache/names.json`) are rewritten
(`plugins/known_marketplaces.json` and `installed_plugins.json` only where a value is itself a path under
the folder). A transcript's recorded `cwd`, an observer row's label,
a ledger line, the tracer's `i_files` are history and are never touched: one line in `~/.ak/db/lineage.jsonl`
(`{"kind":"path"|"label","from","to"}`) lets each reader find them under the new name — the
observer's `labels_with_aliases` and harvest follow it. A worktree's venv (absolute shebangs) and
the folder's memory index are set aside to rebuild; a note at the old path says where it went
(`--link` leaves a symlink instead) until `ak moves finalize`.

**Safe by construction.** It refuses inside Claude Code (`CLAUDECODE`) and while any Claude Code
session is open (they write the same `~/.claude.json` and history), across disks (a move is a
rename, never a copy), onto a destination or a chat-history folder that already exists, and anywhere
near **the gateway**: its files, its launchd unit and the shim fragment that routes `claude` at it
are guarded in every op (`move/ops.py: protected`), and the check after the move undoes it if the
gateway stopped answering. The gateway runs from its own release snapshot under
`~/.local/share/ak/gateway/releases/`, never from the repo, so moving the repo cannot cut it.

**How it runs.** `plan.py` asks each handler what it touches and returns plain JSON steps; the CLI
checks the machine is quiet, writes `~/.ak/moves/<id>/plan.json`, copies the engine
(`runner.py`, `ops.py`, `lineage.py`, stdlib only) into that folder and runs it there, so the move
can relocate the checkout the planner ran from. Each step is journaled before and after it runs
(`journal.jsonl`), every file edited in place is clonefile-copied to `backup/` first, and a JSON
edit is undone by key — what Claude Code wrote after the move survives an undo. A step that fails
undoes the lot; a hard crash leaves a journal that `moves undo` or `moves resume` finishes, from any
step (`tests/test_move.py` crashes it after each one). Without the kit:
`python3 <move dir>/engine/runner.py undo <move dir>`.

**A plugin's own data** joins through `plugin.json`: `"relocate": {"entry": "scripts/relocate.py",
"attr": "handler"}` — a stdlib module whose `handler.plan(move)` adds steps (`move.step(handler,
name, phase, op, **args)` with an op from `ops.OPS`), findings (`move.fail/warn/info`) and lineage
rows (`move.lineage(kind, from, to)`), and whose `handler.detect(ctx)` feeds `ak doctor`. Doctor's own
`layout` check fails while the kit home is still on layout 1 (no `~/.ak/layout` saying `{"layout": 2}`, or
a `.store/` left at its root) and names `scripts/migrate/4-home-layout.py`.
tracer (its index) and observer (a label row, never a row rewrite) ship one.

### Two more kinds of move, same engine

```
   ak plugin rename tag@old tag@new --dry-run      one plugin id: enabledPlugins, installed_plugins.json,
                                                      its cache/<m>/<p> and data/<p>-<m> folders
   ak plugin rename --marketplace old new          a marketplace and every plugin id under it
                                                      (+ known_marketplaces.json, extraKnownMarketplaces)
   ak home move ~/.new-home                        the kit's own state home
   ak moves undo <id>                              either, byte for byte
```

Both plan into the same `Move` (`move/kinds.py`), journal the same way, refuse inside Claude Code and
with any session open, and write a lineage row. Registry edits are exact (`ops.json_edit`: a key
renamed in place, a path re-pointed; undone from the backup when the file is untouched since, else by
key). `home move` renames the folder on the same disk in one step that also leaves a symlink at the old
path (`ops.move_dir`) — the journal lives inside the home, and a straggler running old code would
otherwise re-plant an empty one; the link stays until `moves finalize`. Settings (`WIKILINKS_ROOTS` and
every other path, `~/` forms kept), `~/.claude.json`, chat-history folders of sessions run inside the
home, the registries inside it and Obsidian's vault list (`<home>/vault`) follow. A shell profile's
`$AK_HOME`, `$AK_PATH` or `$AK_STORE` is a process variable: the plan warns with the new value, you edit it. The gateway is not part of `home move` yet.

## OS support

**What has run where.** macOS is the only OS the kit has been *used* on. The test suites (memory, observer,
tracer, wikilinks, plugins, agentic-engineering, the gateway and the shim — before the split into core, vault and
memory) have also passed in a real Ubuntu 24.04 (arm64) container; no systemd unit, no live Claude Code session and no service ran there. **Native Windows has
never run anything**: its cells are backed by code that was read and by unit tests that fake the OS (paths, the
Task Scheduler commands, the registry PATH edit) — treat them as **untested on a real machine**, and the systemd
cells as untested against a real `systemd --user`. The one service manager measured for real is the gateway's
launchd backend (`gateway/docs/operations.md`). The CI matrix for Linux and Windows (`.github/workflows/ci.yml`) is
manual-only and has not been run. `notify` is macOS-only by design (a native popup): on other OSes its hooks
exit without doing anything.

| | macOS | Linux (systemd) | WSL | native Windows |
|---|---|---|---|---|
| **hooks** (every plugin) | `uv run` starts each one; the vault's and memory's through `hooks/akpy.py`, which execs ak's Python | same | same | same; Claude Code runs the command in Git Bash or PowerShell, and the string parses the same in sh, cmd and PowerShell; `akpy.py` runs ak's Python as a child and passes its exit code on |
| **`ak` CLI** | installed on SessionStart by `uv tool install --editable` (ak, vault, memory); `bin/ak` | same | same | same; `bin/ak.cmd` |
| **tracer, observer** | yes | yes | yes | yes; see the observer note below |
| **tool-call index** (`ak trace`) | `node` >= 22.13 | same | same | same |
| **vault** (the `[[name]]` Read hooks, `bin/suggest`) | yes | yes | yes | yes |
| **gateway service** | launchd (`~/Library/LaunchAgents`) | `systemd --user` unit | needs systemd switched on in the distro | Task Scheduler task `\ak-gateway`, at logon |
| **shim** (`ak shim install`) | `claude` sh stub; `.zshenv` and other rc files | same, plus `~/.config/environment.d` | same as Linux | `claude.cmd`; the shim dir goes first in the User Path; only `*.mjs` fragments run |
| **routines login** | Keychain, then `~/.claude/.credentials.json` | `~/.claude/.credentials.json` | same | `%USERPROFILE%\.claude\.credentials.json` |
| **`ak tasks`, `evals/run.py`** (tmux) | yes | yes | yes | no: both stop and say to use WSL |
| **`ak memory demo`** | yes | yes | yes | no: exits 64 |
| **`ak ws move`** | checks for open stores with `lsof` | reads `/proc`, else `lsof` | same as Linux | renames each store aside and back: a refusal means another process holds it |
| **`ak xray`** | `-p` and interactive | same | same | `-p` only; an interactive session is refused |

`CLAUDE_CODE_OAUTH_TOKEN` is read first by routines on every OS. Folders follow the OS: macOS and Linux keep
`~/.config` and `~/.local/share` (honouring `XDG_*`); Windows uses `%APPDATA%` and `%LOCALAPPDATA%`
(`scripts/BRAND.md`, "The platform seam").

What every OS needs, and where it stops short:

- **`uv` on PATH.** Every hook is `uv run ...`. With no `uv` a hook now fails loudly (non-zero, the shell's own
  "command not found") instead of doing nothing silently. A machine with no Python 3.10+ and no network cannot
  get one, and its hooks error (`plugins/HOOKS.md`, section 1).
- **`node` >= 22.13** for the tool-call indexer the tracer's Stop hook launches and `ak trace` reads (the tracer's own
  `traces/bin/traces.mjs`, over its committed bundle `traces/dist/traces.mjs`), for the shim and for
  `app/core/bin/brain-mcp.mjs`. The hook and `ak trace` look for it on PATH, then where
  nvm, fnm, volta, asdf, Homebrew and the Windows installers put it (`plugins/tracer/hooks/convo-index-stop.py`,
  `plugins/tracer/src/tracer/calls.py`). The gateway's own floor is 22.7 (`gateway/package.json`). With no node the hook
  logs "skipped" and the session carries on; `ak trace` then has nothing new. Both look past PATH: nvm's newest, fnm,
  volta, asdf, Homebrew, `/usr/local`, `/usr/bin`, and on Windows Program Files, nvm-windows and volta.
- **Where `ak trace`'s engine comes from.** `$AK_CODE/plugins/tracer/traces/`, else the tracer plugin's own
  `traces/` — the one shipped copy: the bundle is committed in the plugin, so a tracer installed alone reads and writes
  the index. `npm run build` in `plugins/tracer/traces/` refreshes it and the app's `selftest:bundle-fresh` fails when
  it lags. On macOS/Linux the bash door `traces/bin/traces` runs it (it also finds a node when PATH's is too old);
  on Windows `node traces/bin/traces.mjs`.
- **`tmux`** for `ak tasks` and the eval runners: POSIX or WSL only. On native Windows both say so and stop.
- **Intel Mac and the observer's dense search.** `zvec==0.7.0` and `fastembed` carry platform markers in
  `plugins/observer/pyproject.toml`, and `plugins/observer/uv.lock` has no macOS x86_64 wheel for them (wheels: macOS arm64,
  Linux x86_64 and aarch64, Windows amd64). The observer is not blocked there: its venv builds without them and
  search answers as `union` (keywords only). Only `ak observer store embed` is unavailable, and `ak setup` says so
  instead of running it. This is read from the lock file and the code; it was not run on an Intel Mac.
- **Native Windows hooks** run in whatever shell Claude Code picks there (Git Bash or PowerShell); `hooktrace.py`
  runs the hook it wraps through Git Bash too (`CLAUDE_CODE_GIT_BASH_PATH`, else `bash` on PATH, else Git for
  Windows' install folder), and through `cmd.exe` only when there is no bash at all.
- **The first session on a fresh Windows machine** downloads a Python for the hooks' `--python ">=3.10"`: a hook with
  a short timeout may time out that once. Run `uv python install 3.12` first.
- **Windows console encoding:** every `ak` entry point switches stdio to UTF-8 (and `bin/ak.cmd` sets
  `PYTHONUTF8=1`), so a cp1252 console or pipe prints `✓ → ⚠` and emoji titles instead of crashing.
- **WSL needs systemd** for the gateway service: set `[boot]` `systemd=true` in `/etc/wsl.conf`, then `wsl --shutdown`.
  `ak gateway doctor` says so; `ak gateway start --detach` runs the gateway with no service meanwhile.
- **Native Windows, what is left out:** `ak memory demo` (POSIX shell), `ak tasks` and the eval runners (tmux),
  an interactive `ak xray` session (no `script` to give it a terminal: use `claude -p`) and `*.sh` shim fragments.
  `ak ws move` tells whether a store is held by renaming it aside and back (Windows refuses to rename a file
  another process has open), and an npm-installed `claude` (a `node` process) is known by its command line.


## What your plugins put in front of your agent: `ak plugin`

When an agent does something odd, the cause is often text a plugin added to its context, or a hook that blocked it. That text lives in
scripts, is often built at runtime, and fires at moments you can't see. `ak plugin` reads it
back from Claude Code's own record, so you don't have to read every hook.

```
ak plugin ls                    every plugin, loudest first, over the last 20 sessions
ak plugin inspect <name>        one plugin: each hook next to how often it fired, what it
                                   cost, and what it says most, whole; its skills, agents, MCP
ak plugin log [<name>]          one session, turn by turn: everything a plugin printed,
                                   who read it (the model, or only you), and what it cost
```

`ak plugin` on its own is `ls` (`list`, its released name, still runs). Every verb takes `--json`. `ak plugin rename
OLD NEW` is a move, in the same group (see Moving a workspace). Until 4.0.0 this was its own plugin, `plugins@ak`; it is
`src/ak/plugin_inspect/` now, so it needs no vault and no other plugin.

**Nothing is cut.** What a hook said is the reason to run these verbs, so every text is shown
whole, in every mode. How it lands depends on who reads it:

| reader | `inspect` / `log` |
|---|---|
| a person (a terminal) | a heading per hook (per turn, in `log`), its whole text in a box under it, line breaks kept, the box coloured by who read it (model · you · a block). `list` and `inspect` gather the silent ones on one line, so the ones that spoke stand out |
| an agent (a pipe, `CLAUDECODE`) | header-then-TSV, one row a hook, every word of its text on that row |
| `--json` | the text exactly as printed |

`inspect` shows, per hook, its most frequent text; text built at runtime varies, so texts are
grouped by their opening and the newest one speaks for the group. `log` drops an interpreter's
path from the front of an error (`/opt/…/Python: can't open file …`); `--raw` keeps it.

### Where the answers come from

| question | read from |
|---|---|
| what is installed, on or off | `<claude>/plugins/installed_plugins.json`, `settings.json` `enabledPlugins` (user, then the project's) |
| what each plugin is wired to do | its `hooks/hooks.json` (and `plugin.json` `hooks`), `skills/*/SKILL.md`, `agents/*.md`, `commands/*.md`, `.mcp.json` |
| what it said | the session transcripts, `<claude>/projects/*/*.jsonl`: Claude Code writes every hook run as an `attachment` record (`hook_success` with command, stdout and `durationMs`; `hook_blocking_error`; `hook_non_blocking_error`; `hook_cancelled`). A PreToolUse block comes back as the tool's error result, `PreToolUse:Write hook error: [<command>]: <stderr>` |

`<claude>` is `CLAUDE_CONFIG_DIR`, else `~/.claude`. Nothing is run and nothing is written.

### What a line is

| kind | who reads it |
|---|---|
| `context` | the model (`additionalContext`, or plain stdout on SessionStart / UserPromptSubmit) |
| `deny` · `ask` · `block` | the model: a refusal and its reason |
| `stderr` | the model: exit 2 after the tool ran (a write-lint), which blocks nothing |
| `user` | only you (`systemMessage`) |
| `error` | no one: the hook failed, Claude Code carried on |
| `silent` | no one (`log --all` shows them) |

Tokens are characters ÷ 4, counted only for what reaches the model.

### Whose line was it

A hook record carries its command exactly as `hooks.json` wrote it, `${CLAUDE_PLUGIN_ROOT}`
unexpanded, so `(event, command)` names the plugin — looked up in every installed plugin and
every cached version, because a past session ran a past version. Two plugins can run the very
same command (`uv run … hooks/session-start.py`); then the text's opening words decide, the
candidate whose source holds them. When nothing decides, both are named (`alpha|beta`),
never guessed.

### Limits

- A hook that prints nothing is often not recorded at all: `inspect` says `no record`, which
  means silent or never fired. `ak hooks on` records every run with its timing.
- Only hooks are read back from transcripts. Skills, agents and MCP servers are listed from
  their files: what the model is told about them each session, not when it used them.

### The front door

Bare `ak` shows one line from it: plugins on, hooks wired, and any hook whose
script is missing (`broken hook in <plugin>`). That check reads files only.

## Shared with the other plugins

A plugin that cannot import ak carries a copy of what it needs, stamped and checked by `scripts/copies.py`
(`where ak` lists them): `src/ak/ui.py` the one output door and `src/ak/groups.py` the command groups (observer,
tracer: plugins/AGENTS.md §6), `src/ak/session_origin.py` the person-or-program rule, `src/ak/move/lineage.py`,
`hooks/akpy.py` the vault's and memory's hook launcher. `bin/ak` and its Windows twin `bin/ak.cmd` are themselves
copies of `scripts/seam/launcher`: a launcher runs the console script it is named for, so every plugin's `bin/<name>`
is the same file.

## Tests

`uv run pytest -q`: the output contract (`test_cli_output.py`), the mount contract, moves, trace, `ak plugin`
against a fake Claude Code home (`test_plugin_inspect.py`), the brand seam, the install hook against a fake `uv`.
