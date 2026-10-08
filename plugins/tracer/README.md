# tracer

Which session did what. A structural index over Claude Code's own transcripts
(`~/.claude/projects/*.jsonl`), verbatim — no LLM pass, nothing summarised. What Claude
literally ran, cross-session, queryable.

## Why it exists

Claude can already answer "which session changed this file, and why?" on its own. Every session is a
JSONL file under `~/.claude/projects`, and Claude has Bash, `grep` and `jq`. The problem is the cost. After
a few months those files add up to gigabytes. One turn is spread over many lines, and a tool's result sits
on a different line from its call. A subagent's work is in another file. So the answer takes many tool
calls, minutes, and a large share of the context window. It is also easy to get wrong: a file name matches
every time the file was only read or mentioned, not just when it was edited.

The tracer does that work once, after every turn (its `Stop` hook), and keeps the result in an index. The
same question is then one command. What comes back is the answer, not the raw lines: `tracer trace blame
<file>` lists the sessions that edited the file and each change they made. Every view is built that way. It
names the next command, says what the record does not reach, and opens a step in full only when you ask.

Its `PostToolUse` hook applies the same rule while an agent works. When the agent reads
`~/.claude/projects` directly, the hook tells it which command answers the question for less.

**The shell is the door** — `tracer sessions <verb>` for the conversations, `tracer trace <verb>` for what was
done in them. `tracer` is this plugin's own command (`bin/tracer`, on the Bash tool's PATH while the plugin is
on), with or without the `ak` CLI; where `ak` is installed the same groups are also `ak sessions` and
`ak trace`. There is no MCP server: every output names the next command (`next:`, `hint:`), and a shell-only
arm matched the MCP arms on every routing case before the `convo_*` tools were removed (`evals/tool-routing`).

```
   you                                     what comes back                                  door
   ─────────────────────────────────────────────────────────────────────────────────────────────────
   "what's running right now?"             every open session: status, last write, first     tracer sessions live
                                           and last ask, its answer or the call it is on
   "what have we been doing here?"         every session in the project, oldest first:      tracer sessions
    (a timeline, "where do we stand")      what it began with → where it ended up, and
                                           what the record does not reach
   "how did session S go?" (start here)    asked · ended · time · tokens · cost · signals,   tracer sessions show <session>
                                           and every step with what it returned
   "which sessions edited runner.ts,       one block per session: each change as a line     tracer trace blame <file> · /tracer:blame
    and what did each change?"             (L<n> −a +b · old → new), subagent edits too
   "which sessions built plugins/x?"       one summary per session: its files under the     tracer trace blame plugins/x
                                           folder, the turns that changed them, each ask
   "what did the user ask in session S?"   every prompt the user typed, whole, from turn 000 tracer sessions replay <session> --role user
    (a backstory, the why behind a thing)  — task notices, teammates, re-sends left out
   "recap yesterday's session"             the arc: prompts, answers, tools, files          /tracer:trace <uuid|phrase>
   "which sessions talked about X?"        sessions ranked by hits, the turn that said most  tracer sessions search X
   "where did the user say X?"             the same, only hits in what the user typed        tracer sessions search X --role user
   "what did turn 3 actually do?"          its steps, then any one step whole               tracer trace turn <session>-003
   "where did S's subagents spend it?"     one row per subagent: type, the step that        tracer sessions agents <session>
                                           started it, calls, errors, time, tokens; the
                                           calls that ran none; files two agents touched
   "what did that agent actually do?"      its steps, as a turn's, then any one whole       tracer sessions agents <session> <agent>
   "which turns ran skill S / failed?"     turns across sessions, newest first, by plugin,  tracer sessions turns --skill S
                                           agent, skill, tool, errors, dates, branch
   "how often does X fail after Y?"        totals; tool sequences and failure clusters      tracer sessions turns --stats · --patterns
   "which tool calls failed just now?" /   every tool call across every session, newest      tracer trace
    "what did that Bash command say?"      first — filter by tool/server/outcome/time,
                                           open one call whole by id
   from a shell                            the same, as text                                tracer sessions … · tracer trace …
```

`tracer trace` answers a different shape of question than `tracer sessions search`: search finds SESSIONS/turns
that mention something (a text scan); `tracer trace` lists individual CALLS across sessions and opens
one whole by id (a structured index over `traces.db`, the app's per-tool-call store — the contract
is `app/docs/traces-spec.md`). This plugin ships both halves: the engine (`traces/`, below) and
the verb, `tracer trace` (its released spelling `tracer calls` still runs). The store is kept current by
this plugin's own `Stop` hook (below).

The views below (`sessions show`, `sessions turns`, `sessions agents <session> <agent>`, `trace turn`, `replay`, `search`, `blame`) are the
engine's text. Piped or read by an agent they are those exact bytes; in a terminal
the same text has its landmarks coloured — turns, steps, `ok`/`ERROR`, flags, `did:` — and nothing
else changes (`src/tracer/cli.py: _run_engine_view`, `_VIEW_HL`).

## Session → turn → step → result

Start at the session; each level names the next one to open. Nothing is summarised on the
way down — a gist is the result's own first line, or a JSON result's own keys and counts.

```
   tracer sessions show <session>    START HERE. asked · ended · turns · steps · model vs tool time ·
                                     tokens and Claude Code's recorded cost (its cost-state record;
                                     "not recorded" when there is none — no price table is guessed) ·
                                     tool mix · signals. ≤3 turns and ≤40 steps: every step inline;
                                     bigger: one line per turn with its flags, and under it
                                       did: what its calls were for — each Bash/Agent/Monitor
                                            description once, in order, ✗ where the call failed
                                     A uuid prefix works.
   tracer trace turn <session>-NNN   one turn as steps:
                                       #N time (+model time) Tool <label> → size ok|ERROR tool-time
                                          label: the call's description (the line the model wrote
                                          before it ran, the one Claude Code draws as the row), else
                                          `$ command`, a path, a pattern — src/tracer/step_label.py
                                          ↳ what came back: first meaningful line · a JSON result's shape
                                            {count: 30, results: [30 × {id,date,title}]} · Bash's first
                                            line and line count · an error's line
                                     flags, all mechanical: [=#N] the same call again · [≈#N] a result
                                     ≥80% the same words as step N's · [large] ≥30 KB · [raw] reads
                                     transcripts or agentic kit stores directly · [miss] a full-price call
                                     (wrote more to the cache than it read). A turn over 60 steps lists its
                                     first and last 10, errors and large results; runs between are one
                                     counted line; `--steps 11-40` lists any run.
     … --open 1,4,5                  steps whole — 5, 1-6, 1,4,5 or all in one call:
                                     thinking, every input field, timing, tokens, and the result (a JSON
                                     one as shape + first rows first); 20k chars for one step, 4k each
                                     for several, then the file holding the rest
   tracer sessions agents <session>  its subagents, in the order they started: id · type · the step that
                                     started it (turn#step; a fork: its turn; a workflow's: its run) ·
                                     calls · errors · time · output tokens · reported or not · what it
                                     was asked; above, the same per type; under, the Agent calls that ran
                                     no agent (a refused parameter, in one line) and the files more than
                                     one agent changed, or read. Tokens count once per model message — a
                                     message written as several records repeats its usage on each
     … <agent> [--steps] [--open]    one agent as a turn: its brief (whole: the Agent step --open'd),
                                     tokens, signals, then its steps exactly as `trace turn` lists them
   tracer sessions replay <session>  prompt · answer per turn, or how it ended and the model's last words
```

A turn opens at a prompt the user typed. An interrupt, a local command (`/export`,
`/copy`, `/reload-plugins`) and a compaction summary ride inside the turn they follow
(`src/tracer/turns.py`), so ids and step numbers mean the same thing on every
surface. Where a result's full text lives when the transcript holds less:
`<session>/tool-results/<random>.txt` (Claude Code's `<persisted-output>`),
`<session>/tool-results/<tool_use_id>.<tool>.*` (the tool-results plugin),
`<session>/subagents/agent-<id>.jsonl` (an Agent call's transcript; `tracer sessions agents` reads them
all, with each one's `.meta.json` and a workflow's under `subagents/workflows/<run>/` — `src/tracer/agents.py`).
An Agent step names the command that opens its agent: `· its steps: tracer sessions agents <session> <agent>`.

## A step's label: where it comes from

A step reads as its label (`src/tracer/step_label.py`; the index's `summary.label` is the same rule,
`traces/src/traces-blobs.ts: stepLabel`). The label is not a summary. It is an argument the model fills
in before the call runs, read as Claude Code's own tool rows read it:

```
   1  the tool's schema    Bash, Monitor, PowerShell and Agent take a `description`: "Clear, concise
                           description of what this command does in active voice…"
   2  the model's call     {"type":"tool_use","id":"toolu_01MKZ8x1…","name":"Bash","input":{
                             "command":"git stash push … && git rebase -q docs/api-reference …",
                             "description":"Stack the export branch on top of the docs commit"}}
   3  Claude Code's row    getToolUseSummary(input) → description, else the command cut short;
                           the spinner says "Running <description>". No second model is involved.
   4  the result           {"type":"tool_result","tool_use_id":"toolu_01MKZ8x1…",
                             "content":"Already up to date.…"} — same id, so `→ ↳` pairs meant with got
```

What that makes it: intent, not outcome (the call above meant to rebase and found nothing to do), so a
step line always shows what came back beside it. It is on 3 of 4 Bash calls (1279 of 1718, the week of
2026-10-05); Read, Edit and Write never carry one and their path says it instead.

Claude Code has two more model-written lines. Neither is a tool call's label we can read:

```
   tool_use_summary    a helper model's ~30-char label for a batch of calls ("Fixed NPE in UserService"),
                       {summary, preceding_tool_use_ids}. Made only with CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES
                       set, sent on the SDK stream (the mobile app's rows), never written to a transcript:
                       0 records in every .jsonl on this machine.
   system:away_summary a recap of the session when the person comes back ("We're drafting … nothing is
                       sent. Next: …"). Written to the transcript; the tracer does not read it yet.
```

Found in Claude Code 2.1.289 (2026-10-05). To check a later build, read its binary
(`~/.local/share/claude/versions/<version>`) with Python: `grep -oE '.{0,400}…'` and `ugrep` refuse
patterns that long, and `strings` splits the code into fragments.

```
   uv run python -c "import re,sys; d=open(sys.argv[1],'rb').read()
   for m in re.finditer(rb'tool_use_summary|getToolUseSummary\(', d): print(d[m.start()-300:m.end()+300])" \
     ~/.local/share/claude/versions/<version>
```

## The wire

A session that ran through the ak gateway (`~/agentic-kit/gateway`) has a second record: one row
per model call, as sent. `src/tracer/wire.py` reads it as data — the format is the
gateway's `docs/trace.md`, no gateway code imported — and joins each call to the transcript
message it produced.

```
   join      by message id (rows from 2026-09-24 on); older rows by exact input + cache counts in
             time order, a v1 streamed row halved first (that build summed two stream events)
   session   `tracer sessions show <session>` — wire: N calls · how many matched · subagent and side
             calls no transcript holds · first-byte median and slowest · accounts
   steps     [sysΔ] [toolsΔ] [acct] — what changed on the main thread since the call before,
             on the first step of the message; each is a cache miss
   a step    `tracer trace turn <session>-NNN --open N` — the call: row, first byte, duration, account,
             tools, bytes sent, how it was joined, and the blob files holding the system prompt,
             tools and (capture full) messages
```

Off the gateway there are no rows and the drill reads as before. `AK_GATEWAY_DATA_DIR`
points it elsewhere; the tests point it at an empty folder.

## Search

`tracer sessions search` scans the raw transcripts of every project (ripgrep, no index; `--under
<path>` for the projects that own a path) and ranks sessions by hits in conversation
text — prompts, assistant text and thinking, tool inputs and outputs — the latest hit
breaking ties. Hook output, attachments, titles and injected skill text are metadata,
counted only with `--include-meta`. `--limit` is the number of sessions
shown; every session is scanned. `--tool`, `--role`, `--outcome`, `--since`, `--until` narrow the hits.
It finds mentions, not topics: which sessions were ABOUT X is `ak observer search X --by-session`. Each session entry carries the
session's own title (its latest `ai-title`, or a custom title), its turns and the time
spent in them, what it began with, and the turn that said most — read in the one pass
that maps hits to turns — so what a session was about is judged without opening it. Subagent and
workflow transcripts (`<uuid>/subagents/…`) are sidechains of their parent, never listed
as sessions.

Which sessions are listed at all is one predicate, `search.leave_out`:

```
   left out                  why                                          to list it anyway
   the calling session       CLAUDE_CODE_SESSION_ID, set for every        --exclude-session ''
                             tool Claude Code launches
   automated sessions        transcript `entrypoint` not in cli ·          --entrypoint all
                             claude-desktop · sdk-ts (sdk-cli = claude -p,
                             sdk-py: summarizers, evals, probes)
```

A transcript with no `entrypoint` predates the field and counts as a person's; a temp-dir
cwd is not a signal. The default for automated sessions is one constant,
`search.INCLUDE_AUTOMATED`; the engine's `--include-automated` takes it from there.

## Install

```
/plugin marketplace add proxify-dev/agentic-kit     from the public kit (the marketplace is `agentic-kit`)
/plugin install tracer@agentic-kit

/plugin marketplace add teocns/cc-tracer            on its own (this folder is also the repo cc-tracer)
/plugin install tracer@cc-tracer

/plugin marketplace add ~/agentic-kit               or with the kit checkout (the marketplace is `ak`)
/plugin install tracer@ak
```

Needs `uv` and python ≥ 3.10. `bin/tracer` runs the plugin's console script through
`uv run --project <plugin root>`, which resolves its dependencies (jinja2, click, rich-click) into the
plugin's own `.venv` on first use — nothing global, nothing to install by hand. Claude Code puts an
enabled plugin's `bin/` on the Bash tool's PATH, so the verbs an agent types (`tracer sessions …`,
`tracer trace …`) answer in every session the plugin is on. It needs no other plugin.

## What it ships

```
   hooks/                    SessionStart · Stop → convo-index-stop.py (incremental index, backgrounded, never blocks;
                             people's sessions only — `claude -p` and the SDKs skip it unless AK_CAPTURE=1).
                             The same Stop hook also launches the tool-call indexer this plugin ships
                             (`node traces/bin/traces.mjs index`, detached and niced, before anything that needs
                             uv; $AK_CODE/plugins/tracer's instead when set) — the store `tracer trace` reads, kept
                             current with the app closed. Node >= 22.13 is PATH's, else nvm's newest, fnm, volta,
                             asdf, Homebrew, /usr/local, /usr/bin (Windows: Program Files, nvm-windows, volta);
                             none, and the hook logs "skipped" to stop-hook.log
                             SessionStart → session.py (one line naming the verbs above, `tracer trace` included;
                             and skills/index/bin first on the Bash tool's PATH through CLAUDE_ENV_FILE: `sessions`
                             and `trace`, the short names the skills write, each `bin/tracer <group> "$@"`. A plugin's
                             bin/ is appended after /usr/bin, whose macOS `trace` would win otherwise)
                             PostToolUse (Bash·Read·Grep·Glob) → raw_read.py: a direct read of what the verbs read
                             (~/.claude/projects outside a saved tool-results/ file, ~/.claude/sessions, or the
                             trace store — brain-traces/, the kit's db/traces, traces.db) is answered with the verbs;
                             a read under a session's subagents/ with `tracer sessions agents <that session>`;
                             people's sessions only (session_origin.py, a copy of ak's; scripts/copies.py
                             in the kit writes and checks it)
   bin/tracer, tracer.cmd    the plugin's command: runs its console script in the plugin's uv env (a copy of the
                             kit's one launcher, scripts/seam/launcher)
   skills/                   trace · blame · index (+ references, an arena, a behavioural eval)
   src/tracer/               the engine: turns · parser · storage · query · drill · search · live · sessions
                             · patterns · db · cli; ui.py · groups.py · help.py are copies of the ak CLI's
                             output door, command groups and help (scripts/copies.py in the kit writes them):
                             the same lines inside `ak` or on its own
   scripts/cli.py            the mount shim — `ak sessions …`, `ak trace …` (and the hidden `ak tracer …`)
                             when the `ak` CLI is installed: the same click objects as `tracer …`
   traces/                   the tool-call index (TypeScript, node ≥ 22.13, `node:sqlite`): src/traces*.ts,
                             bin/traces (launcher) + bin/traces.ts, dist/traces.mjs — ONE committed bundle, so
                             a user runs it with no npm install. Rebuild: `npm run build` there (esbuild through
                             its node_modules link, `npm run link:dev`); `app`'s `selftest:bundle-fresh` checks
                             the bytes. The app imports these sources too (its vault paths live here, once)
   evals/tool-routing/       (in the kit only: its cases and runs are a person's sessions, so cc-tracer leaves
                             it out, with the index skill's arena and the trace skill's eval)
                             do agents reach the right tool, in how many calls — the gate for a description
                             or output change (its README: arms, run, how to read a result). Every case
                             carries its story — STORY.md, the rule in plugins/EVALS.md
```

## Which plugin a call came from

A plugin's MCP tool is `mcp__plugin_<P>_<K>__<tool>` (every character outside
`A-Za-z0-9_-` becomes `_`), and the `_` between plugin and server cannot be split from the
string. `src/tracer/catalog.py` reads what this machine knows —
`~/.claude/plugins/installed_plugins.json`, every cached version under `cache/`,
marketplace checkouts and directory marketplaces, `plugin-catalog-cache.json` — and looks
each prefix `normalise("plugin:P:K")` up. Skills (`P:name`, or bare) and agents
(`P:sub:agent`) resolve against the session's own `skill_listing` / `agent_listing_delta`,
then the user's `~/.claude/{skills,commands,agents}`, then the catalog. Every credit is
stored with where it came from (`i_plugins.via`: mcp · skill · agent · file) and how sure it
is (`how`: exact, or inferred — split at the first `_` when the catalog never saw the
plugin); an ambiguous name credits no one. Checked against the `system/init` records of 61
headless runs: 3,101/3,101 MCP tool names, 1,449/1,449 skills, 349/349 agents.

The index lives at `~/.claude/plugins/data/conversation-index/index.db`. Read-only
queries never index; the Stop hook and `tracer trace index` are the only writers. The
manifest records the parser version each transcript was indexed with (`PARSER_VERSION`
in `parser.py`); a lower one is stale, so a change to turn numbering re-derives every
transcript still on disk on the next index pass instead of mixing two numberings.

## The CLI

Two homes in `ak`, one question each: the conversations, and what was done in them. A session is
its UUID, its first 8 characters, or `latest` — one resolver (`drill.resolve_session`) for every verb;
a miss says what it looked for. ✎ marks a verb that writes.

```
   tracer sessions [ls] [PROJECT]       every session in a project, oldest first; a folder that is not on disk
        … --all [--refresh]                and has no sessions is refused (exit 64); --all: every indexed project
   tracer sessions live                 every open session (the registry, ~/.claude/sessions/<pid>.json,
                                           live pids only) joined to its transcript, newest activity first
   tracer sessions show <session>       start here: asked · ended · time · cost · its turns
        … [--turn N] [--open 1,4,5]        one turn's steps; steps whole
        … [--stats | --patterns]           the index's totals for it; its tool sequences and failure clusters
        … --as-skill                       the /tracer:trace prefetch block, byte for byte (a phrase works too)
   tracer sessions agents <session>     its subagents, one row each, totals per type; exit 1 when it started none
        … <agent> [--steps 11-40]          one agent's steps (its id or first characters; ambiguous exits 2)
        … <agent> --open 1,4,5 [--turn N]  steps whole; --turn for an agent sent more than one message
        … --json                           the rows, or one agent's steps, as one JSON document
   tracer sessions turns [PROJECT]      turns across sessions, newest first — this project (its worktrees and
        … [--plugin --agent-type --skill   folders inside, as ls counts them), or --all; a miss exits 1
           --tool --errors --since --until
           --branch --limit --all]
        … [--stats | --patterns]           the totals over the matches; tool sequences and failure clusters
                                           over the newest 500
   tracer sessions replay <session|phrase>  a session's dialogue, or the sessions matching a phrase
        … --role user                      only what the user typed: every turn, each prompt whole, a prompt
                                           typed mid-turn under its turn; task notices and teammate messages
                                           left out, a re-send folded into the copy that was answered
   tracer sessions search (s) <phrase>  sessions ranked by hits  [--limit N --since D --until D --tool T
                                           --role R --outcome error|ok --entrypoint all|NAME --include-meta
                                           --exclude-session UUID --under PATH]

   tracer trace                         every tool call, newest first  [--tool --server --session --origin
                                           --outcome --since --until --q --limit --cursor]
                                           what: the call's label (summary.label, the same rule as a
                                           step line) · came back: the result's first line
   tracer trace get <id> · stats        one call whole; totals
   tracer trace turn <session>-NNN      one turn's steps, with what each returned  [--steps 11-40] [--open 1,4,5]
   tracer trace blame <path>            which sessions touched a file; a folder (plugins/x, or a trailing /)
        … [--operation read,write,edit]    gets one summary per session
   tracer trace index ✎                 the session index, then the tool-call index; each reads only what changed
        … [--all|--rebuild|--session ID]   (the session index) · [--recent] (the tool calls) · --json: both
   tracer trace purge ✎                 delete old tool calls (a person's verb)
   tracer-engine …                   the argparse engine underneath (query|search|trace|projects|dialogue|index)
```

Every old spelling still runs at the root, out of help: `tracer live|log|inspect|show|replay|grep|blame|index|calls`
(inside `ak`: `ak tracer …`) — the same click objects (`live`, `replay`, `blame`, `grep`) or the same bodies
(`inspect`, `show`), so old and new print the same lines; the trace skill and the Stop hook call some of them.
`tracer calls index` is the tool-call index alone, as it was. The old `sessions <project>` and `trace <uuid>`
leaves are `sessions ls <project>` and `sessions show <uuid> --as-skill`. `ak search` (ak) asks this plugin
too: `brain_search` in `src/tracer/cli.py`, the sessions `tracer sessions search` ranks first.

**A flag is named after the class the record already has, and takes the values the output
already prints.** grep's hit counts read `user 4, assistant 31, tool-output 52`, so the filter is
`--role user`; its `hidden:` line prints entrypoints (`sdk-cli 1628`), so the gate is
`--entrypoint all`; `--outcome` takes `tracer trace`'s words. One word means one thing on every verb.
The released spellings still work, hidden from help: `--errors` (= `--outcome error`),
`--include-automated` (= `--entrypoint all`), `show --step` (= `--open`), `--tool user`, `--op`.

```
   roles       user          what the user typed (not tool results, task notices or teammates)
               assistant · thinking · tool-output · notification (a background task) · teammate (another session)
```

Every verb prints through this plugin's copy of the `ak` CLI's output door (`src/tracer/ui.py`): plain for
an agent or a pipe, `--json` on a read verb, colour in a terminal — the same bytes as `tracer …` on its own
and as `ak sessions …` / `ak trace …` inside `ak` (`tests/test_homes.py` compares the two). Every hint and the
SessionStart line name the plugin's own command (`src/tracer/verbs.py`: `tracer trace turn`); the skills write
its short names, `sessions …` and `trace …` (skills/index/bin, first on PATH; `tests/test_short_names.py`).

## Tests

```
uv run pytest -q
```

## Relationship to the others

Standalone. It needs no vault, no `ak` CLI and no other plugin, and imports none of their code (the indexer
under `traces/` needs only node). The trace skill reads
two things from a vault when one is there, read-only: project bindings (`projects/*.md`
with `cwd:`), and the observer's rows for the traced session (`$AK_STORE/brain.db`, else
`<kit>/db/brain.db`, the kit home's one store; `sdk_sessions.content_session_id` → `observations`, skipping
forgotten and folded rows). The vault is found through a copy of the vault plugin's
resolver (`hooks/vault_root.py` and `hooks/ladder.py`; `scripts/copies.py` in the kit writes and checks
them); no observer code is imported. The vault plugin needs this one for nothing.
