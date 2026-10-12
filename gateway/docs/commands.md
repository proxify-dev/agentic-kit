# Commands

`cc-gateway help` prints the same list. With no verb, `cc-gateway` is `status`.

| | command | what |
|---|---|---|
| **switch** | `status [--json]` | on or off, build, traffic, policy, any setting that overrides the routing |
| | `enable` · `disable` `[--json]` | turn it on (service + routing) or off |
| **accounts** | `account` | the pool: name, type, org, priority, state, 5h / 7d quota, expiry |
| | `account login [--api] [--name N]` | add one by browser login, or an API key |
| | `account import [--from PATH] [--name N]` | take Claude Code's own login (`~/.claude/.credentials.json`) |
| | `account enable\|disable NAME` · `account priority NAME N` | toggle one; lower priority is preferred (default 0) |
| | `account remove NAME` | out of the file, and out of rotation at once if the gateway runs |
| | `account choose` | pick one on the terminal, print its name |
| | `reload` | the daemon re-reads the accounts file |
| **launch** | `run [--account NAME] -- PROGRAM …` | one program through the gateway; refuses when it is off |
| | `env [--account NAME]` | what `run` sets, as `export` / `unset` lines: `eval "$(cc-gateway env)"` |
| | `env --json` · `env --plain` | for a program: the variables that route its model calls here, as one JSON object or `KEY=value` lines; none while the daemon is not running |
| | `try [--login] [--name N] [--port P] [--bin B] [-- claude args]` | one claude through a throwaway gateway on the next port |
| | `xray [--full] [--part system\|tools\|messages] [--json] [-o NAME] [--vs NAME] claude …` | the exact prompt a launch would send, dry-run ([xray.md](xray.md)) |
| | `probe [--model M] [--prompt P] [--bin B]` | one real headless `claude -p` through the gateway |
| **watch** | `tui` | live screen; every key is one control-API call ([control-api.md](control-api.md#the-tui)) |
| | `tail [--n N]` | the last requests from the trace |
| | `tail --session [ID]` | one session's requests, subagents included (this session's when run from claude) |
| **operate** | `restart [--now]` | run this copy's code, handed over with no gap |
| | `rollback` | back to the build before |
| | `start [--detach]` · `stop` | the process (through the service manager once installed) |
| | `service install\|uninstall\|status` | the service unit alone |
| | `shim on\|off\|status` | the cc-shim routing fragment alone |
| | `doctor` | can this machine run the service, and what to do if not |

Global flags: `--port P` and `--account NAME` override the config; `--trial` aims
`status · account · tail · tui · probe · env · reload` at a running `try` gateway.

## For a program

`status`, `enable`, `disable` and `env` with `--json` print one JSON document on stdout and nothing else
there (progress and errors go to stderr). Exit codes are the verbs' own.

- `status --json` — `{ running, pid, power, config }`, exit 0 on or off. `running` is whether the
  daemon answers; `pid` is its pid or `null`. `power` is the switch: `{ on, partial, service, shim, accounts }`
  (`service` and `shim` are the two halves' states, `accounts` the enabled accounts in the file).
  `config` is what a client needs to reach the daemon over HTTP: `{ host, port, upstreamUrl, capture, dataDir }`.
- `enable --json` · `disable --json` — `{ ok, steps, power }`: the verb's result, its lines as `steps`, and
  the switch as it stands after. Exit 0 when `ok`, else 1.
- `env --json` — an object of the environment variables to give a program so its model calls go through
  the gateway: `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` (the doorman placeholder) and the Claude Code
  defaults it does not already set ([claude-code.md](claude-code.md)). `{}` while the daemon is not running
  (judged by its pid file). No `x-*` identity headers: the caller adds its own. `--account NAME` pins the
  base URL to the account the running gateway resolves. `env --plain` prints the same as `KEY=value` lines.

How each of these behaves in depth: [operations.md](operations.md) (on/off, restart, try, the
service), [accounts.md](accounts.md) (the pool).
