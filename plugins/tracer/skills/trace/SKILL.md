---
name: trace
description: >-
  Trace and recap a past Claude Code session by UUID, slug, date, or phrase.
  Prefetches dialogue + structure + delegated work + the observer's rows for
  grounded summaries. Use when user asks "what did we do in that session", "trace this
  work thread", "recap that session", "show session arc", or "link prior
  session context".
argument-hint: "<uuid | slug | phrase | date>"
---

# Trace — Session and Thread Recap

Target: `$ARGUMENTS`

The block below is pre-fetched at skill load (bash injection). Synthesize from
this block instead of re-querying tools.

<TRACE_CONTEXT>
!`[ -x "${CLAUDE_PLUGIN_ROOT}/.venv/bin/python" ] || rm -rf "${CLAUDE_PLUGIN_ROOT}/.venv"; uv run --project "${CLAUDE_PLUGIN_ROOT}" python "${CLAUDE_SKILL_DIR}/bin/fetch.py" "$ARGUMENTS"`
</TRACE_CONTEXT>

Template controls live in [`references/template-editing.md`](references/template-editing.md).

## Step 1 — Interpret the pre-fetched block

| Prefix in output | Meaning | Your action |
|---|---|---|
| `RESOLVE_NEEDED` | No session specified | Ask for UUID, slug, date, or topic phrase |
| `RESOLVE_QUERY=` + `CANDIDATES` | Phrase/slug search results | Show top matches, ask user to pick a UUID and re-run `/tracer:trace` |
| `SESSION_UUID=` (+ `TITLE=`, `INDEX=`, `CWD=`) | Session resolved | Build recap from Dialogue + Structure (+ Delegated, Artifacts, Observer when present) |
| `INDEX=no transcript on disk…` | The UUID has no transcript on this machine | Say so. Do not go looking in the indexer's source, the DB, or other project dirs — the check already covered every project dir |
| `ERROR` | Indexer unavailable | Report issue and provide manual fallback command |

## Step 2 — Present recap

Use this shape:

```
Conversation: <TITLE= if present, else the topic>
Date: <from dialogue timestamps>
Project: <cwd>
Branch: <from structure if present>
UUID: <session uuid>

Summary: 2–4 sentences about what was asked and what was decided.
Key tools/friction: notable tools, errors, or blockers from structure.
Next: what the session left open, if anything.
```

## Rules the last seventeen traces taught

- **Recap first.** The user asked what happened in *that* session. Do not
  debug, build, or copy files unless their question asks for it.
- **Quote the block, or say it is not there.** Never attribute to the trace a
  fact the block does not contain. No invented quotes, commands, or flags.
- **A missing section is not a mystery.** `INDEX=` says whether the transcript
  exists and was indexed just now. `sessions replay <uuid>` or
  `sessions show <uuid>` before any jq on a `.jsonl`.

What each section is good for:

- **Dialogue** — the last `ASST:` is the final answer *whole*; earlier ones are
  500-char previews and end in ` […]` when cut. Quote the final answer's
  conclusion rather than re-deriving it. `sessions replay <uuid> --full` for all.
  A turn that gave no answer says so and how: `ASST: — no final text;
  interrupted by the user after step 6 (Bash). last thinking: …` — that last
  thinking is where the turn was heading. A local command the user ran
  (`/export`, `/copy`) is one indented line under its turn, not a turn.
- **Structure** — per interaction: tools, `dur=` (how long the turn took —
  the answer to "why did this take so long"), `end=interrupted@N` /
  `end=tool@N` (no closing answer), `files:` (what it wrote and read, writes
  first), `err[…]:` (what the errors actually were).
- **Delegated** — subagents with their final result text, workflows with
  their journal path, background tasks (Bash `run_in_background`, Monitor)
  with what launched them and the tail of their output. A large foreground
  result Claude Code saved to disk is not a task and is not listed. This is
  where the results of work the session handed off live.
- **Observer** — the observer's live rows for this session,
  `#id · type · title` (forgotten and folded rows left out). "no record"
  is normal for a session that has not ended: the observer writes at
  session end. Supplementary — the dialogue outranks it.
- **Artifacts** — files the session wrote or edited, whether they still exist,
  and the first lines of each markdown deliverable. Read the rest of a file
  only if the question needs it.

## Step 3 — Offer deeper tracing on request

> Want deeper detail?
> - `sessions show <uuid>` — how the session went: time,
>   tokens, cost, signals (interrupted, errors, repeats, raw reads), and each step with a
>   one-line gist of what it returned
> - `trace turn <uuid>-NNN --open 1,4,5` — steps whole, in one call
> - `sessions search "<phrase>"` — sessions ranked by hits (add `--under <path>` to scope
>   to a project subtree)
> - raw JSONL read only when verbatim output is explicitly requested

## Anti-patterns

- Do not call mem-search tools for UUID recap when dialogue is already injected.
- Do not read raw JSONL just to summarize.
- Do not drill every interaction when dialogue section already provides the arc.
- Do not open a step to learn what the drill line already says (size, status, timing,
  the `↳` gist, a JSON result's keys). Open several at once when you must.
