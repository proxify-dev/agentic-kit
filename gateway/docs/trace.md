# The wire record

One row per model call, as sent: `~/.local/share/ak/gateway/trace/<UTC day>.ndjson` (`%LOCALAPPDATA%\ak\gateway\trace` on Windows)
(`AK_GATEWAY_DATA_DIR` moves the root). Bodies by hash: `blobs/<hash[0:2]>/<hash>.json`.

**For one session, read it joined to its transcript:** `ak sessions show <session>`.
`ak gateway tail --session` lists the raw rows.

## What readers rely on

tracer's `wire.py` reads these as data; a change here is a change there.

| field | contract |
|---|---|
| `v` | `2`: `usage` is each count's latest reported value. `1` + `stream` + a `stopReason`: input, `cacheRead`, `cacheCreation` are ×2, `output` high by a placeholder |
| `messageId` | the API's `msg_…` — equal to the `message.id` of the transcript entry the call produced. Absent before 2026-09-24 |
| `identity.session` | Claude Code's session id; subagent calls carry the parent's |
| `dryRun` | `true`: the request carried `x-brain-dry-run` (`ak xray`) — answered by the gateway, never forwarded; `usage` is null, `account` null, and `blobs.body` is kept at every capture level |
| `hints.requestClass` | `main` · `subagent` · `compaction` · `auxiliary`; absent on rows before hints |
| `blobs.system` · `.tools` · `.body` | content hashes. `body` is stored at `full`, and at `gaps` when `recorded` is false; a body can be gone once `blobDays` (7) pass — read it as optional |
| `blobs.settings` · `.safeguards` · `.tail` | settings = every top-level request key but model, system, tools, messages, metadata, safeguards; safeguards = the permission context sent in plan/auto mode; tail = a side call's newest message (`gaps`). Absent before 2026-09-25 |
| `recorded` | `gaps` only: a transcript held the session when the call arrived. `false`: the body is the one copy |
| `ttfbMs` · `durMs` | first upstream byte · whole reply |
| `account` · `accountSource` | who served it: `pinned` · `preferred` · `rotated` |

## Gotchas

- **Messages live in the transcript, not here** (`curl -s localhost:4747/_gateway/status | jq .capture`). At `gaps` the gateway keeps a body only when no transcript holds it (`recorded: false`); for everything else read the transcript — its `prompt_snapshot` attachment holds the system prompt and tools, each attachment's `rendered` the exact text sent.
- **Compare system prompts on `main` rows.** Subagent and `auxiliary` calls each carry their own.
- **Day files are UTC.** A session crossing midnight UTC spans two.
- **Match model calls by suffix.** An account-pinned call's `path` is `/tc-acct/<name>/v1/messages?beta=true` (or `/tc-prefer/…`); a reader testing `startswith("/v1/messages")` drops every `claude --account` session.
