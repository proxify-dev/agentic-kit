# observer

What past sessions learned. An observer — a plugin agent, `agents/observer.md` — runs when a
session ends and writes what it saw as **observation rows** in the vault's store; the next session
opens on "since you were last here"; and a session asks the store before it re-derives.

**The shell is the door** — `observer search → timeline → get`, the plugin's own command (`bin/observer`, on
the Bash tool's PATH while the plugin is on; inside the kit also `ak observer …`), and every plain answer ends on a
`hint:` naming the next command. There is no MCP server: a shell-only arm matched the MCP arms on
every case (the kit's tool-routing eval).

```
   you                                        what comes back                                    door
   ───────────────────────────────────────────────────────────────────────────────────────────────────────
   open a terminal                             "since you were last here: 3 new" — shown to YOU; the model gets only the doors line    SessionStart hook
   "did we try this before?"                   dated rows, memdb:<id> to cite                     observer search → timeline → get
   "which sessions was X in?"                  one entry per session: uuid · started · asked      observer search --by-session
   read a file a past session worked on        "this file has prior observations: …"             PreToolUse Read hook
   end the session                             the observer writes rows (detached, never blocks)  SessionEnd hook
   "these three rows say the same thing"       one summary row stands for them; --undo restores   observer fold
   "what keeps coming up?"                     anchors seen ≥3× across ≥2 sessions                 observer patterns
   "this row is noise"                         preview, then hide for a reason; never deleted      observer forget … --confirm
   "is it recording here?"                     the writer, the agent, the store                    observer status · mute --here · show · try · types · store
```

## Why not claude-mem: no MCP server, nothing left running

The observer started from [claude-mem](https://github.com/thedotmack/claude-mem). It keeps the same store
layout and the same three-step recall (search → timeline → get). It drops two things:

```
                         claude-mem                              observer
   ──────────────────────────────────────────────────────────────────────────────────────────────
   how an agent asks     4 MCP tools, their schemas sent with    a shell command whose output names
                         every request                           the next one (hint:)
   what keeps running    a Bun worker, an HTTP server, a         nothing: a hook runs when the session
                         Chroma vector database                  ends, writes its rows, exits
```

- **No MCP server.** When agents had only the shell, they picked the right command first just as often
  as they picked the right MCP tool (an eval of tool routing, runs 5–6). A command costs one
  line at session start, not a tool list sent with every request. It also works the same for you,
  a subagent, `claude -p` or another harness.
- **Nothing left running.** There is no daemon to start, watch or restart. Both tools keep a vector
  index on disk (here about 1 GB, claude-mem's Chroma about 1.7 GB on the same machine). The saving
  is in the processes, not the disk.

## Install

```
ak setup                                      pick observer in the list, or:
/plugin marketplace add proxify-dev/agentic-kit
/plugin install observer@agentic-kit
```

Needs `uv`, python ≥ 3.10, and the `claude` CLI (the observer runs `claude -p` at
SessionEnd). The verbs run as `observer …` — `bin/observer` runs the console script through
`uv run --project <plugin root>`, which resolves `rich-click` and the dense channel's packages into the
plugin's own `.venv` on first use — and as `ak observer …` inside `ak`, where `observer search` and
`store embed` hop into that venv for the dense channel.

The dense channel of search is on by default: zvec 0.7 and fastembed (bge-small, 384-d)
are dependencies, `observer store embed` writes the collection once for an existing
store (~14 min for 20k rows), and every SessionEnd embeds the rows it just wrote. The model
lives in `~/.cache/fastembed` (`$FASTEMBED_CACHE_PATH` wins). Without a collection search
answers as `union` — nothing fails.

The store is ONE file, `~/.ak/db/brain.db` (`$AK_HOME` moves the kit home,
`$AK_STORE` its `db/` alone), whichever scope a session runs in: a repo's own `.ak/`
holds that project's notes, and its sessions are rows here, labelled by project, so one
search spans every project (`vault_root.db_dir()`, vendored from the vault plugin). The
mute dial (`db/capture.json`), the ledger (`db/objects-ledger.jsonl`) and the capture scratch
and digest cursor (`cache/sessions/`, `cache/digest-cursor.json`) live in the kit home too, never in a vault. The vault plugin is not required: this
plugin creates the store, and plants nothing else.

## The observer is a plugin agent

`agents/observer.md` is a standard Claude Code agent file: `name`, `description`, `model` in the
frontmatter, the prompt as the body — what counts, how to decide a verdict, how corrections are
filed. At SessionEnd `scripts/summarize-session.py` runs it headless:

```
   claude -p <the frame + the session> --bare --strict-mcp-config
          --agents '{"observer": {…the file…}}' --agent observer --tools "" --output-format json
```

`--bare` keeps the person's CLAUDE.md, hooks and plugins out of the run, and it also skips plugin
agents — so the file is handed over inline. On Windows it goes as a temp file's path instead
(`--agents` with `-p` takes one), and `claude` is started without a shell: `claude.exe` first; a
`claude.cmd` is the kit's shim (asked for the real binary with `CC_SHIM_PRINT_REAL=1`) or npm's
(seen through to its target), because cmd.exe cuts an argument at its first newline. With only an
sh stub on PATH the run logs `summarize: skipped — …` to the session's log and writes nothing.
The message it is sent is the FRAME (the JSON shape,
the type table, the ceiling, redaction), TODAY — the local day of the session's last message (a
session resumed over days is told its last day; a live run with no transcript, the day it ended) so
`until: tomorrow` resolves to a date — and the session. The types are one table,
`hooks/memory_schema.py`; the correction flag (`hooks/prompt-flag.py`) is one regex. Until
2026-09-25 all of this was records under the vault's `loadout/`, overridable per scope and
switchable per type; that folder was retired.

Before a row is stored, two things the model wrote are checked against the session itself.
`files_modified` keeps only files a Write or Edit event really touched, under that event's path
(21% of stored paths named none; claude-mem gated the same field in 13.12.2). And `redact()` masks
secrets in every row, prompt, summary and note: vendor key shapes, `Authorization: Bearer`,
and `<name>_token|_secret|_api_key|password` values (`tests/test_redact.py`).

Its rows are raw and unverified, so they are never injected into a model's context: an agent
searches them (`observer search`), and `ak memory harvest` (the vault plugin) turns
what keeps recurring into proposed project notes.

## What it ships

```
   hooks/                    SessionStart   → session-start.py    one ~40-token line naming the doors (the digest goes to the person's screen; a resume shows none)
                             UserPromptSubmit → session-register.py · prompt-flag.py   (a correction is flagged for the observer)
                             PreToolUse Read → file-context.py     prior observations about the file (not in a subagent)
                             PostToolUse    → capture-event.py     the session's scratch
                             SessionEnd     → session-end.py       dispatches scripts/summarize-session.py, detached
   agents/observer.md        the observer: a standard plugin agent, run headless at SessionEnd (never delegated to)
   _archive/skills/recall    the old /observer:recall skill, archived 2026-09-25 — the shell verbs replace it
   src/observer/       obs_index (the search backend) · curate (patterns/fold/forget)
                             capture_flag (the writer's dial) · cli · observer/store/memory (verbs)
   bin/observer, .cmd        the plugin's command: runs its console script in the plugin's uv env (a copy of the
                             kit's one launcher, scripts/seam/launcher)
   scripts/cli.py            the mount shim — `ak observer …` when the `ak` CLI is installed; its `brain_search`
                             gives `ak search` the rows `observer search` lists (union, no embedder); inside
                             `ak`, mute · unmute · prompts · fold · forget · store init · sync · backfill · embed carry ✎ (they write)
```

## The dial

```
   observer mute [--here]     the writer, vault-wide or for one folder — ~/.ak/db/capture.json
   observer prompts auto      copy a session's prompts into the store only while the tracer is off
                              (on · off pin it; same file, key "prompts")
   observer status            exit 0 recording here, 1 not, and why; whether prompts are copied
```

**Prompts are copied only without the tracer.** The store's `user_prompts` table came with
claude-mem's schema; the observer never reads it back — its agent reads the transcript, and the
session row keeps the first prompt (`asked`) and the count. The tracer reads the same prompts from
Claude Code's transcripts (`tracer sessions search --role user`), so while the tracer plugin is
enabled (Claude Code's settings, any marketplace) the summarizer skips the copy
(`capture_common.prompts_state`). Rows already stored stay. Two other readers of the copy see no
new rows while it is skipped: the brain app (a session's prompt count, its prompt search) and
memory's E4 alias rarity (experimental, off by default).

**Only people's sessions are recorded.** Claude Code hands every hook `CLAUDE_CODE_ENTRYPOINT`:
`cli` (a terminal), `claude-desktop` and `sdk-ts` (a desktop chat app) are people and are
recorded; `sdk-cli` (`claude -p`) and `sdk-py` (the Python SDK) are programs — the observer's own
summarizer, the evals, probes — and are not, nor do they get the SessionStart digest (they keep
the one doors line). Over 6,685 transcripts (2026-09-22) this kept all 617 human sessions and
skipped 6,050 of 6,068 automated ones; a session with no entrypoint is recorded as before.

```
   AK_CAPTURE=1           count an automated session as a person's: recorded, digest shown
   AK_CAPTURE_DISABLE=1   never record (wins over everything; the summarizer sets it on itself)
```

An app that drives Claude through the TypeScript SDK starts as `sdk-ts` too, like a person's chat in
it, so only the app can tell its own automation apart: it sets `AK_CAPTURE_DISABLE=1` on those runs.

## Recall, and what stays hidden

`search` and `timeline` skip rows that are **forgotten**, **folded** into a summary,
**superseded** by a newer row (`supersedes: obs:<id>`) or **expired** (`until:` in the past).
`get` by id never hides — that is the point of asking by id — and says what the
row is. `fold` and `forget` write one line each to `~/.ak/db/objects-ledger.jsonl`, the
same file another reader of this store can show as a ledger.

```
   observer patterns [--project P]                  anchors seen in ≥3 rows across ≥2 sessions
   observer fold <id> <id>… --title T --fact F…     one summary row stands for them; fold --undo <summary> restores
   observer forget <id>… --reason R                 a preview; --confirm hides them, --undo --confirm restores
   observer forget --query Q --reason R             a preview of what a search finds; it never writes
```

Nothing is forgotten without `--confirm`, and each preview's `hint:` is the command that writes
it. A refusal — fewer than two ids to fold, no `--fact`, a row already folded, rows of two
projects, a forget with no reason, an id not on record — is an `error:` on stderr and exit 64;
no store, exit 66. `curate.py` holds the three, a Python twin of the app's `store-write.ts`.

## Which session a row came from

Every row `search` and `timeline` return carries `session`: the Claude Code session uuid
(`sdk_sessions.content_session_id`) — the id tracer takes, so a row leads straight to
`ak sessions show <uuid>` or `/tracer:trace <uuid>`. `observer get` adds the session,
the prompt number and what the session was asked. A row whose session the store never
recorded (the claude-mem imports, about 10k rows) says `''` — unknown, never guessed. The
transcript behind an old uuid may be gone: Claude Code prunes them.

`observer search --by-session` answers "which sessions was X
in": the top 200 ranked rows vote for their session by reciprocal rank (k=60), so a session
with many hits and a session with the top hit both surface; each entry is
`{session, project, started, asked, did, hits, top: [{id, title}]}` — `asked` is how the session began, `did` what its latest summary says it completed — and `unlinked` counts the
rows with no session. `limit`/`offset` then count sessions.

## A trial: following an issue to its fix (`observer shadow`)

The observer files what a session found; it never follows a thing from "broken" to "fixed". This is a
trial, run beside the observer, of detecting open issues and when they are solved, silently, so you can check
on it periodically. It changes nothing the observer does:

```
 observer shadow on        <kit>/db/shadow/issues/ON: from now on every session end reads the session twice
 the second read           summarize-session.py --shadow: the same agent and message, plus the project's
                           OPEN ISSUES and an `issues` section: opened (fixed_here or not), resolved, progress
 what it writes            <kit>/db/shadow/issues/events.jsonl and runs/<session>.json, nothing else:
                           no store row, no scratch cleared, no embedding
 observer shadow run ID…   the same on past sessions, oldest first (to seed it)
 observer shadow report    still open · closed by a later session · against the issue notes in projects
                           with memory on (.agents/memory/issues/: who opened it, who closed it)
 observer shadow off       stops it; what it wrote stays
```

An id the model names that was not in the OPEN ISSUES it was shown is dropped and counted. It costs one more
observer run per session. The data: `hooks/shadow_issues.py`; the verbs: `src/observer/shadow.py`; tests:
`tests/test_shadow_issues.py` (the real prompt is byte for byte unchanged without `--shadow`).

## How a query becomes candidates

FTS5's `MATCH` is an implicit AND, so one word a row lacks used to return nothing. The
lexical rule, **union**, puts rows matching every term first (bm25 order) and, when fewer
than five do, rows matching any term after them. The default, **hybrid**, is union with the
dense channel fused into a query the every-term tier starved. `--mode` on
`observer search` and `AK_OBS_SEARCH_MODE` pick another:

```
   hybrid   the default — when the every-term tier alone fills the floor that answer
            stands; only a starved query is fused with vector by rank (RRF, k=60)
   union    every-term rows first, any-term rows when fewer than 5 match — no vectors
   and      every term only: the pre-2026-09 behaviour
   vector   the dense channel alone
```

```
   observer search <query> [--mode …]    the rows, from the shell; --json for a list
   observer search <query> --by-session  one row per session the topic came up in
   observer timeline <id> [--before N]   layer 2: the rows around one, the anchor marked ▶
   observer get <id> [<id>…]             layer 3: rows whole (37078, memdb:37078, a comma list)
   observer store embed [--rebuild]      build the dense channel (needs the extra)
```

The dense channel is one 384-d vector per observation (title · subtitle · facts) in a zvec
collection at `<store>/zvec/observations`, written by `observer store embed` and
computed locally by fastembed; without a collection `vector` and `hybrid` answer as
`union` rather than fail. The gate is why `hybrid` is gated rather than a plain fusion: an
exact-identifier query that the lexical channel already answered loses its row when older
semantic neighbours are allowed to vote — and because the gate runs first, such a query
never loads the model. zvec takes one LOCK per collection, read-only opens included, so a
search opens the collection per query (~5 ms) and closes it; SessionEnd's writer holds it
only to upsert, one embed runs at a time (`<store>/zvec/.embed.lock`), and a search that
cannot get in within a second answers that query as `union`. Every
filter and the hide rule stay in SQL; the dense channel only proposes ids.

A `--project` scope reads both spellings of this repo's pair, `ak` and `.ak`, through
`capture_common.labels_with_aliases` — the one table the hooks and the digest also read.

## Tests

```
uv run pytest -q
```

The summarizer and the capture gate read a transcript's chat rows only, by row type (`hooks/transcript_rows.py`):
since Claude Code 2.1.300 the transcript also saves every model call, and a saved message body carries
`message.role` too. `AK_TEST_REAL_TRANSCRIPTS=1 uv run pytest -q tests/test_request_rows_real.py` reads this
machine's 2.1.300+ transcripts with and without those rows and asserts the digest and the tool count come out
the same.

## Relationship to the others

Shares only the vault's data paths and imports no other plugin's code. It reads `projects/*.md`
bindings and the vault root through copies of the vault plugin's `vault_root.py`, `ladder.py` and
`project_match.py` in its hooks/ (with ak's `session_origin.py`, `transcript_rows.py` and `move/lineage.py`), and prints through
its own copies of ak's `ui.py` and `groups.py` (`src/observer/_ui.py`, `_groups.py`), mounted or not.
`scripts/copies.py` in the kit writes every copy and fails on a byte of drift (`where observer` lists them).
The vault plugin needs this one for nothing; another reader may use the same store and the same ledger.
