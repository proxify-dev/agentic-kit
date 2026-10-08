# Interaction Record Schema

Each interaction record represents one user intent → assistant action → outcome cycle.

## Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | `<session_id>-<sequence>` (e.g., `abc123-003`) |
| `session_id` | string | UUID of the source conversation |
| `session_file` | string | Absolute path to the source JSONL file |
| `byte_offset` | int | Byte offset of the user message that starts this interaction |
| `timestamp` | string | ISO 8601 timestamp |
| `git_branch` | string | Git branch active during interaction |
| `user_message_preview` | string | First 200 chars of user message (snippet only — full content is NOT duplicated) |
| `assistant_reply_preview` | string | Last assistant text block in the interaction, capped at 500 chars |
| `plugins` | string[] | Attributed plugin names |
| `agents_spawned` | object[] | `{type, prompt_preview}` |
| `skills_invoked` | object[] | `{name, args}` |
| `tools_called` | object[] | `{name, count}` |
| `files_touched` | object[] | `{path, operation}` |
| `error_signals` | object[] | `{type, preview}` |
| `intent` | string? | LLM-classified intent (null if structural-only) |
| `outcome` | string? | LLM-inferred outcome (null if structural-only) |
| `tokens_estimated` | int | Rough token usage estimate |
| `duration_seconds` | int | Wall-clock duration |

## Plugin Attribution

Interactions are attributed to plugins via:
1. Agent type prefix: `search:miner` → `search`
2. Skill name prefix: `meta:self` → `meta`
3. File path matching: `plugins/search/...` → `search`

## Error Signal Types

- `tool_error`: Bash non-zero exit, tool failure
- `user_correction`: Next user message starts with correction pattern

## Storage

- Index: `~/.claude/plugins/data/conversation-index/index.db` (SQLite, WAL mode)
- Manifest and project_meta tables live inside the same database
- The index holds **structural metadata, user/assistant previews, and filter keys**. Tool inputs, tool outputs, and mid-turn assistant chunks stay in the source JSONL; drill-down resolves them by seeking to `byte_offset` in `session_file`.
- Reads open the DB via `file:index.db?mode=ro` — N parallel read-only connections are lock-free under WAL. Writes are exclusive to the indexer (`trace index`, and the Stop hook that runs it after every turn).
