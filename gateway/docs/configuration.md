# Configuration

The defaults work; nothing needs a config file. Paths shown for macOS and Linux; on Windows the
config lives under `%APPDATA%\ak` and the data under `%LOCALAPPDATA%\ak`.

## Files

| path | what | override |
|---|---|---|
| `~/.config/ak/gateway.json` | config, hand-edited JSON, every field optional | `AK_GATEWAY_CONFIG` |
| `~/.config/ak/accounts.json` | accounts and their tokens (0600), pool settings | `AK_GATEWAY_ACCOUNTS_FILE` |
| `~/.local/share/ak/gateway/` | `trace/`, `blobs/`, `gateway.log`, releases, keeper state | `AK_GATEWAY_DATA_DIR` |
| `~/.local/share/ak/cc-gateway/<version>` | the install, `current` pointing at one | `XDG_DATA_HOME` |
| `~/.local/bin/cc-gateway` | the launcher | `CC_GATEWAY_BIN_DIR` |

`XDG_CONFIG_HOME` and `XDG_DATA_HOME` are honoured. The `ak` folder name is shared with the agentic
kit, which runs the same gateway.

## Config fields

Environment beats file beats default.

| field | default | env | what |
|---|---|---|---|
| `port` | `4747` | `AK_GATEWAY_PORT` | where it listens |
| `host` | `127.0.0.1` | — | bind address; there is no key, so anything that can reach it can spend your accounts |
| `upstreamUrl` | `https://api.anthropic.com` | — | where requests go |
| `account` | `""` (rotate) | `AK_GATEWAY_ACCOUNT` | pin every request to one account |
| `holdSeconds` | `0` | — | when every account is spent, hold the connection this long before a 429 |
| `capture` | `gaps` | `AK_GATEWAY_CAPTURE` | how much of a request body reaches disk: `none` · `meta` · `gaps` · `full` ([policy-and-trace.md](policy-and-trace.md#trace)) |
| `blobDays` | `7` | — | days a stored body is kept; `0` = forever |
| `policy` | `[]` | — | rules, evaluated in order ([policy-and-trace.md](policy-and-trace.md#policy)) |

A config file that is not valid JSON is an error, never a silent fallback to defaults.

[`example.config.json`](../example.config.json) has sample policy rules. Try it without installing
anything: `AK_GATEWAY_CONFIG=./example.config.json`.

Pool settings (`switchThreshold`, `quotaProbeSeconds`, `holdSeconds`, `distributeSessions`) live in
the accounts file: [accounts.md](accounts.md#pool-settings).

## Other environment variables

| variable | what |
|---|---|
| `AK_GATEWAY_NODE` | the node binary the service runs |
| `AK_GATEWAY_COMMAND` | how hints spell the command (default `cc-gateway`) |
| `AK_XRAY_TIMEOUT` | seconds xray waits for a launch's first model call (default 60) |
