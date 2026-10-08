# `ak status` — live agent roster

Deterministic, stdlib-only companion to the recap path. Where `sessions replay <arg>`
summarizes a session's *dialogue* (via the indexer + jinja), `ak status`
reports the *fleet*: every teammate and workflow agent, and whether each is
running, idle, stalled, failed, or done.

## Usage

```
ak status <uuid|prefix|slug>          one-shot snapshot
ak status <id> --watch [-n 2]         refresh loop (top-style)
ak status <id> --all                  list finished agents (default collapses)
ak status <id> --json                 structured roster for piping
```

Target resolves by full UUID, bare hex prefix (`3de61f5f`), or slug. No target
= error (explicit by design).

## What it reads

No database. It walks the per-session transcript tree directly:

```
~/.claude/projects/<project>/<uuid>.jsonl            parent (team lead)
~/.claude/projects/<project>/<uuid>/
  subagents/agent-<id>.jsonl  + .meta.json           teammates
  subagents/workflows/<wf>/agent-<id>.jsonl          workflow fleet
  subagents/workflows/<wf>/journal.jsonl             started/result per agent
  workflows/<wf>.json                                workflow run state
```

## State inference

There is no PID registry — liveness is inferred. mtime freshness is the only
real-time "alive" signal; journal `result` and workflow `status` are the
authoritative terminal signals.

| State | Teammate | Workflow agent |
|---|---|---|
| 🟢 RUNNING | mtime < `RUNNING_WINDOW` (45s) | journal has only `started` + fresh mtime |
| 💤 IDLE | cold, last line `assistant/end_turn` (resumable) | — |
| 🟠 STALLED | cold, ended mid-tool (no terminal turn) | only `started`, cold, wf not done |
| ✅ DONE | — | journal has `result`, or wf `status: completed` |
| ❌ FAILED | — | journal `error`/`failed` |

Tunable via env: `CC_TRACE_RUNNING_WINDOW`, `CC_TRACE_STALL_WINDOW` (seconds).

## Design notes

- `lib/roster.py` owns discovery + classification (importable, returns a `Roster`
  dataclass). `bin/status.py` is render + `--watch` loop only.
- The `status` subcommand is dispatched by the `ak` shim before the jinja
  path, so it carries no jinja/indexer dependency.
- The same enumeration is the foundation for the recap-depth fix (teaching the
  indexer to descend into `subagents/**` + `workflows/**`), still pending.
