# xray: the exact prompt a launch sends

```sh
cc-gateway xray claude                                    # a person's interactive session here
cc-gateway xray claude -p "hi"                            # a program session (claude -p)
cc-gateway xray claude --plugin-dir ./my-plugin           # any claude flags: --bare, --model, --settings, --agent, …
cc-gateway xray [--full] [--part system|tools|messages] [--json] [-o NAME] [--vs NAME] claude …
```

xray shows the exact prompt a `claude` launch would send — system prompt, tools, messages. The launch
goes to the running gateway like any routed session, every request marked dry-run; the gateway
records each model call and answers it itself, so none leaves it. Everything from `claude` on is the
command, as you would type it; xray's flags go before it. The default is an outline, one row per part
with its sections and sizes (tokens ≈ chars/4):

```
  program session · claude-opus-5-5 · 1 model call dry-run · none left the gateway

  system       3 blocks     4.1K   … · "You are an interactive agen…" · # Harness 1.6K · … · appended prompt
  tools        15          51.0K   Workflow 22.4K · ScheduleWakeup 5.0K · Agent 4.1K · … +12   (+72 deferred)
  message 1    3 blocks     9.5K   CLAUDE.md ×2 7.5K · git status 2.0K · "hi"
  message 2    1 block     27.0K   as system: [tracer] · [wikilinks] · [memory] · … · skills 13.2K
  total                    91.6K chars ≈ 22.9K tokens
```

| flag | what |
|---|---|
| `--full` | the whole text, a separator per section |
| `--part P` | one part only (`system` · `tools` · `messages`), a line per section |
| `--json` | the request body as caught |
| `-o NAME` | save the run as `<dataDir>/xray/NAME.json` — the run, its trace row and the body's hash; the body stays in the gateway's blobs, kept past `blobDays` while the save exists |
| `--vs NAME` | read a saved run back and show only the sections added, removed or changed since, each change as a line diff (`−`/`+`) |

**Sections.** A section is cut at a line that looks like a header — `[plugin-tag] …`, `# Heading`,
`Contents of <path> (`, `The following …`, `Available agent types`, `<Event> hook additional
context:`, `<system-reminder>` — and named by that tag, heading or path, which is what `--vs` pairs
across runs. A CLAUDE.md's own headings stay inside it; a prompt appended by cc-shim and
`--append-system-prompt[-file]` are found by content.

## The dry run

Source: `src/dry-run.ts`, `src/xray.ts`.

xray launches the command on the normal route with the routed environment (`routedEnv`,
`src/claude-env.ts`): the config's host:port as `ANTHROPIC_BASE_URL`, the doorman pass,
`claudeEnvDefaults()`, no `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`, and `CC_SHIM_DISABLE=1`
(when `claude` on PATH is cc-shim, it still appends its system prompt, byte-identical to a
shim-routed launch). Three headers ride in `ANTHROPIC_CUSTOM_HEADERS`: `x-brain-origin: xray`,
`x-brain-run: <id>`, `x-brain-dry-run: 1`. cc-shim's `--account`/`--acct`/`--prefer` are taken out of
the command (under `CC_SHIM_DISABLE` its routed-or-refuse check would stop the launch), and the
header line says so: a dry run is answered before any account is chosen, so the account cannot
change the prompt.

In `server.ts`, before policy, routes, accounts or anything upstream, a request carrying
`x-brain-dry-run` gets its trace row written as usual — identity, hints, blobs, `dryRun: true`, the
body blob kept whatever the capture level — and then a canned reply from the gateway itself: SSE when
the body streams (`message_start` `msg_dryrun_<row>` with zero usage, one text block "ok",
`end_turn`, `message_stop`), else the JSON equivalent, and `{}` on any path but `/v1/messages`. It is
never forwarded, needs no account and spends none. A request without the header takes exactly the
path it always did, and the header is in `IDENTITY_HEADERS`, so it never leaves the machine.

xray reads the trace as it grows: the run's first row the hint headers mark `main` is the catch (with
no hints at all, the first model call carrying tools), its body blob is what is printed, and the
run's other model calls are counted as side calls. Only model calls are dry. Claude Code 2.1.281's
own background calls go out as they do from every routed session: `/api/hello` reaches the gateway
without the launch's custom headers and takes the normal path; telemetry, flags and settings go
straight to Anthropic, never through the gateway (see [security.md](security.md#what-still-goes-around-it)).
A model call of the launch's session that went upstream anyway — not dry — is named in a `WARNING`
line and fails the run.

## Preconditions

**The gateway has to be there and new enough.** Off, xray says so and names `cc-gateway enable`; it
never starts a gateway of its own. `/_gateway/health` says `dryRun: true` on a build that has it; a
build without it would forward the prompt, so xray launches nothing and says why.

- Run from inside a Claude Code session, that session's markers (`CLAUDECODE`,
  `CLAUDE_CODE_ENTRYPOINT`, its messaging socket, …) are dropped, so the launch looks as it would
  from a terminal.
- A settings file whose `env` sets `ANTHROPIC_BASE_URL` or a cloud provider would win over the
  launch's env and take the prompt past the gateway, so xray refuses before launching.
- `-p` gets `--no-session-persistence` and exits after the canned reply.
- To try a development checkout's gateway without touching the service, run its `src/daemon.ts` with
  `AK_GATEWAY_PORT`, `AK_GATEWAY_DATA_DIR`, `AK_GATEWAY_ACCOUNTS_FILE` (an empty pool is fine) and
  `AK_GATEWAY_CONFIG` pointed at scratch, and give xray the same env — not `start`, which goes
  through the service manager once the service is installed.

## A person's session

Without `-p`, xray launches an interactive session. It has caveats:

- No prompt given, it sends `"hi"` as the first prompt, ahead of the flags, and says so in the header.
- It runs under `script` in a pseudo-terminal (160×48). Its stdin is a pipe from a `sleep` that never
  writes: macOS `script` refuses a socket or a FIFO, and a closed stdin types ^D into the session.
  Windows has no `script`, so there xray refuses a person's session and says to use `-p`. Linux's
  is util-linux's (`script -qec`) or busybox's (Alpine: `script -q -c`, no `-e`), told apart by the
  applet it links to or its `--version`; with none on PATH xray says what to install.
- A program session's `claude` is found on PATH by xray itself (`src/procs.ts`): on Windows `.exe`
  before `.cmd`, never npm's extensionless sh script, and a `.cmd` is seen through to the script or
  exe it wraps, else run through `cmd.exe` quoted for it — spawn alone refuses a batch file. `try`
  and `probe` start claude the same way.
- At the catch the whole tree is killed (SIGKILL, found by a `/proc` walk on Linux and `ps` on macOS;
  `taskkill /T /F` on Windows — no SessionEnd hook runs), then what it started under Claude Code's
  config dir (`$CLAUDE_CONFIG_DIR`, else `~/.claude`) is removed — the transcript and its dir,
  `session-env/<sid>`, `tasks/` and `teams/session-<sid8>`, its live-session registry entry, a project
  dir it created — only what was created after launch. Its `"hi"` line in `history.jsonl` there stays
  (every live session appends there), and so does what plugins' prompt hooks record for a person's
  prompt.
- A resume (`-c`, `-r`, `--from-pr`, `--teleport`) is refused without `--fork-session`: xray's turn
  would land in that session's transcript. With `-p` nothing persists, so it runs.
- Nothing caught within 60 s (`AK_XRAY_TIMEOUT`, seconds), the tree is killed and xray names the
  likely cause — the folder-trust dialog in a folder never opened in claude, another dialog — with the
  last lines claude printed.
- Claude Code sends its first request without waiting for slow remote MCP servers; one still
  connecting is listed as such rather than as tools. A real launch races the same way, so two runs
  can differ there.
