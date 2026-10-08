---
name: index
description: Index and query Claude Code conversation histories. Extract interaction records from JSONL, filter by plugin/tool/agent, detect patterns and failures. Search conversation content by keyword. Trace file provenance to find why a file was written.
triggers:
  - conversation index
  - conversation history
  - interaction index
  - look back at conversations
  - search plugin usage
  - how was * used
  - invocation patterns
  - conversation query
  - where did we debug
  - find where we
  - why was * written
  - who wrote * file
  - trace file
  - search conversations
---

# Index

Query, search, and trace Claude Code conversation history — the kit's
**session-record lobe**. (The vault lobe, semantic memory over `$AK_PATH`,
is `observer` / `/vault:*` — a separate surface.)

## Verbs — `sessions` and `trace`

Two commands on your PATH while the plugin is on: `sessions <verb>` (the conversations) and
`trace <verb>` (what was done in them). They are short for `tracer sessions` and `tracer trace`, this
plugin's own command, which works the same. A session is its UUID, its first 8 characters, or `latest`; a turn is `<session>-NNN`.
Every output ends on the command to run next (`next:` / `hint:`), spelled `tracer …`.

| Verb | Purpose |
|------|---------|
| `sessions show <session>` | **Start here.** A session at a glance (every step inline when small) |
| `trace turn <turn>` | One turn's steps, with what each returned |
| `trace turn <turn> --open 1-6` | **Steps whole** — several at once: input, result (JSON as its shape first), timing, tokens |
| `sessions replay <session>` | **Prompt+answer transcript**, oldest first; a turn with no answer says how it ended. `--role user`: only what the user typed |
| `sessions turns` | **Turns across sessions**, newest first, by `--plugin --agent-type --skill --tool --errors --since --until --branch`; this project, or `--all` |
| `sessions turns … --stats` | Totals over the same matches: turns, errors, tokens, top plugins, agent types, tools |
| `sessions turns … --patterns` | Tool sequences, failure clusters, agent mixes per plugin (over the newest 500 matches) |
| `sessions ls` · `sessions ls --all` | This project's sessions, oldest first · every indexed project with its counts |
| `sessions search <phrase>` | **Which sessions talked about X**, ranked — scans raw JSONL directly |
| `trace blame <path>` | **Which sessions read or changed a file, and what each change was** — subagent edits included |
| `trace` | Every tool call across sessions, newest first (a different store, `traces.db`) |
| `trace index` | Refresh both indexes (the Stop hook already does, after every turn) |

### Which verb

Match the shape of the question to the verb that answers it directly. Do NOT
add up rows by hand when a verb gives the total already.

- **"How many X in total?" / "What's the overall Y?"** → `sessions turns --all --stats`
  (one aggregate — never sum per-project counts by hand); narrowed: `--plugin observer --all --stats`
- **"What projects do I have?" / "Per-project breakdown?"** → `sessions ls --all`
- **"Which is the top/most-used X?"** → `sessions turns --all --stats` (already sorted)
- **"Where did we discuss X?" / content search** → `sessions search X`
- **"Which sessions edited X, and what did each change?" / file provenance** → `trace blame <path>` — one call; the change gists are in it
- **"What did we decide in session X?" / session recap** → `sessions replay <session>`
- **"Show me turns where…" (a plugin, a skill, an agent, a tool, errors, a date, a branch)** →
  `sessions turns --skill recall --errors` (add `--all` for every project)
- **"Which tool calls failed just now?" / "what did that Bash command say?" / any
  per-call lookup across sessions** → `trace` — a different store (`traces.db`,
  every tool call from every session), see `app/docs/traces-spec.md`
- **"How did session S go / perform / what went wrong?"** → `sessions show <session>` — START HERE
- **"What did turn ABC-003 do, step by step?"** → `trace turn ABC-003`
- **"Show me exactly what steps 1, 4 and 5 ran and returned"** → `trace turn ABC-003 --open 1,4,5` — one call

Progressive disclosure: `sessions show <session>` → `trace turn <session>-NNN`
→ `trace turn <session>-NNN --open …`. Each output names the next command. Read the `↳`
gist and the flags before opening anything: most steps never need opening, and
a JSON result's keys are already on its gist line. Never Bash/jq a saved
result file — `--open` shows a JSON result's shape and first rows.

When the question says "total" or "across all projects" and asks for a single
number, the answer is `sessions turns --all --stats`, not a sum over `sessions ls --all`.

### sessions search
Use when the user asks "where did we debug X?", "find sessions where we discussed Y", "which conversation had that error?". Scans raw JSONL files of every project (no index needed). Sessions rank by hits in conversation text — prompts, assistant text and thinking, tool inputs and outputs — the latest hit breaking ties. Per session: its own title (the transcript's latest ai-title, or a custom title), date, turns and time spent in them, hit count by role; what it began with (when that is not the hit turn); the turn with most hits and its prompt; one snippet. Judge what a session was about from these lines — open it only to read what it concluded. The header says what was not counted (hook/metadata hits), hidden (automated sessions) and left out (the calling session). It finds mentions, not topics: which sessions were ABOUT X is `ak observer search X --by-session`.
- `<phrase>` (required): plain-text term (case-insensitive, literal) — quote a phrase
- `--limit N`: sessions shown (default 10; every session is scanned)
- `--under <path>`: only the projects whose folder owns that path; by default every project
- `--entrypoint all`: also list sessions a program started — transcript `entrypoint` other than cli / claude-desktop / sdk-ts (`claude -p` summarizers, evals, probes; most transcripts on a machine)
- `--tool T`: only hits in that tool's calls and results (`Bash`, or an MCP tool's short name)
- `--role user`: only hits in what the user typed (also assistant · thinking · tool-output · notification · teammate)
- `--outcome error|ok`: only hits in tool results that ended this way · `--since` / `--until`: ISO date or datetime, inclusive (a bare date keeps that whole day)
- `--include-meta`: also count hook output, attachments, titles, injected skill text
- `--exclude-session UUID`: default leaves out the caller (`CLAUDE_CODE_SESSION_ID`); `''` keeps it; a uuid leaves out that one

```
"retry" in all 412 projects: 138 session(s), 1214 hit(s) in conversation text · ranked by hits, then the latest
not counted: 987 hit(s) in hook/metadata records (122 session(s) had only those) — --include-meta counts them
hidden: 896 session(s) a program started that matched (sdk-cli 781, sdk-py 115) — --entrypoint all lists them
left out: this session 0a8db1e3 — --exclude-session '' keeps it

1. 28825be1-… · "Webhook retry with backoff" · 2026-09-12 · 17 turns · 45m44s · 170 hit(s): tool-output 45, thinking 41, Bash 41, …
   began: The webhook sender gives up after the first timeout. I want it to retry with backoff, but only on a 5xx and…
   turn 013 · 25 hit(s) (also turns 000, 001, 002, 003 +12) · asked: Ok but what happens when the queue is full and…
   user: …d be nice if each delivery showed its retry count in the dashboard, next to the status, if you see what I mean…

a turn's steps: tracer trace turn 28825be1-…-013 · what the user asked there: tracer sessions replay 28825be1-… --role user · 137 more session(s): --limit 138
```

### sessions show / trace turn
`sessions show f08edcd1` — the session view (a uuid prefix is enough):

```
f08edcd1-… · "Webhook retry discussions" · 2026-09-22 17:04 UTC · ~/code/app · main · claude 2.1.280
asked: Which sessions talked about the webhook retry policy?
ended: interrupted by the user after step 6 (Bash) — no answer
1 turn · 6 steps · 51s in turns: model 23s · tools 28s (Claude Code's count; calls running in parallel add up)
tokens: in 1.2k · cache read 288.8k · cache write 110.0k · out 2.2k (thinking 667) · $0.66 — Claude Code's own count
tools: Bash×2, notes.search, notes.get_notes, …
signals: interrupted after #6 · repeats #4≈#1 · large #3 31.6 KB, #5 130.4 KB · raw reads #5, #6
legend: …

── turn 000 · 17:04:47 · 6 steps · 51s
#2 17:04:50 ∥ notes.search webhook retry → 10.3 KB ok 3s
   ↳ {count: 30, results: [30 × {id,date,type,title,project,tags,status,merged_into,deleted_at,replaces}]}
#3 17:04:57 (+4s) notes.get_notes {"ids":[…]} → 31.6 KB ok <1s [large]
   ↳ {notes: [17 × {id,content}]}
…
#5 17:05:08 (+10s) Bash Find sessions where the user typed "retry" in a prompt → 130.4 KB ok 24s saved: k2m9qv7rd.txt [large] [raw]
   ↳ 2026-07-20 | d40178dd | -Users-me-code-app | n=1 | | The sender retried forever…
ended: interrupted by the user after step 6 (Bash) at 17:05:38
  /export (local command) → Conversation copied to clipboard
```

A step reads as its label: the call's own description (the line Claude Code draws as the row),
else `$ command`, a path, a pattern. `tokens`/`$` come from Claude Code's own `cost-state`
record when the transcript has one; without it the view sums the messages' usage and says the
cost was not recorded. A big session gets one line per turn, with a `did:` line under it, and
ends on `next: tracer trace turn <session>-NNN`.

`trace turn <turn> --open 1,4,5` — several steps in one call. Per step: the thinking/text
before the call in full, every input field whole (multi-line strings as raw blocks),
call → result timing, ok|ERROR, the token usage of that assistant message (once per
message), and the result: a JSON one as `shape:` and its first rows, then raw text up
to 20,000 chars for one step or 4,000 each for several, then the path holding the rest
(the saved file, else `<transcript>:<line>`). An Agent/Task step names its subagent
transcript. A turn over 60 steps lists its first and last 10 and every flagged step;
`--steps 11-40` lists others.

### trace blame
Use when the user asks "which sessions edited X and what did they change?", "why was this file written?", "trace the history of this file". One call answers it; do not follow up with git log/diff or a jq on transcripts.
- `<path>` (required): a path suffix (`plugins/x/src/f.py`), a basename, or an absolute path — an absolute path inside a repo is matched by its repo-relative tail, so the checkout, worktrees and the release clone all count; a folder (`plugins/x`) gets one summary per session
- `--operation read,write,edit` to narrow · `--limit N`: sessions shown (default 20); every project is read

```
"src/webhooks/sender.py" — 3 session(s) changed it, 0 only read it · newest first
matched paths:
  ~/code/app/.claude/worktrees/retry-backoff/src/webhooks/sender.py  (worktree retry-backoff · 2 sessions)
  ~/code/app/src/webhooks/sender.py  (checkout · 1 session)
same file name, other path — a different file, not counted above:
  ~/code/app/tools/replay/sender.py  (checkout · 2 session(s), 2026-09-21 → 2026-09-21)

a121fabd-… · 2026-09-22 16:50 · ~/code/app · worktree-retry-backoff · EDIT×8 · in worktree retry-backoff
  turn 004 asked: No, backoff has to be on by default, nobody is ever going to pass that flag
    #20 Edit L548 −2 +2 · `… or "fixed"` → `… or self.DEFAUL…`
    open: tracer trace turn a121fabd-…-004 --open 15-22

316c8159-… · 2026-09-22 12:50 · … · EDIT×8 READ×3
  via subagent sender-port — started at turn 037 #2 · ~/.claude/projects/…/subagents/agent-asender-port-….jsonl
    :346 Edit L532 −6 +14 · `def send_once(req, t):` → `def send(req, t=0):`
```

Per change: `#step` (or `:line` in a subagent's transcript), `L<line>` where it landed
(Claude Code's own patch), `−removed +added`, the first line whose text changed on each
side; a Write says `created|overwrote, N lines`; a failed edit says `failed: <why>`.
`open:` is the one command that opens exactly those steps whole. Changes made through
Bash (sed -i, heredocs, git) are not seen — `sessions search <file name> --tool Bash`.

### sessions turns
Use for a question about turns across sessions: "which turns ran the recall skill?", "where did
Explore agents fail last week?", "how much did the observer plugin take this month?". One line a
turn, newest first: its id, plugins, tools, agents, tokens, duration, `end=interrupted@N` /
`end=tool@N` when it gave no closing answer, `ERR:n`, and what it asked. A miss exits 1.

```
2 interactions matched (2026-09-16 to 2026-09-21, 0 shown with errors)
  from 15143 total indexed interactions

[2026-09-21T08:15:10] id=2173d3bc-…-000 plugin=notes tools=[Bash(1),Read(2),Skill(2),…] ~52500tok dur=56s
  > On some branch we sketched a retry queue for the webhook sender. The plan was to

[2026-09-16T07:01:04] id=0147da61-…-000 plugin=notes tools=[Bash(7),Skill(1),…] ~61000tok dur=2m50s
  > Here's the situation. The sender often times out on the first call, and then it

next: tracer trace turn 2173d3bc-…-000 for its steps · tracer sessions show 2173d3bc-… for its session
```

Every filter narrows the list, `--stats` and `--patterns` alike (`--patterns` reads the newest 500
matches); `--stats` and `--patterns` end on `hint:` — the same question as a list:
- `[PROJECT]`: a folder (default: this project — the folder, its worktrees and the folders inside); `--all`: every indexed project
- `--plugin P`: turns credited to a plugin — they called its MCP tools, a skill or an
  agent of it, or touched a file under `plugins/<name>/` — as named by this machine's plugin
  catalog (installed, cached versions, marketplaces; `catalog.py`). An MCP tool name
  `mcp__plugin_<P>_<K>__…` can't be split by eye; the catalog looks it up
- `--skill S`: a skill by name — `recall` matches `observer:recall`
- `--agent-type A`: the subagent type a turn spawned (Explore, general-purpose, <plugin>:<agent>, …)
- `--tool T`: a tool by its full name (Bash, Agent, WebSearch, `mcp__GitHub__get_me`, …)
- `--errors`: only turns with an error signal
- `--since` / `--until`: ISO date or datetime, inclusive (a bare date keeps that whole day)
- `--branch B`: git branches that start with B (`feat/`)
- `--limit N`: the newest N (default 20)

One session's turns, in turn order with the files each touched: `sessions show <session>`;
its totals: `sessions show <session> --stats` (and `--patterns`).

## Freshness model

**Queries are read-only and never trigger indexing.** This eliminates write-lock
contention when parallel processes query the index concurrently.

**The index is kept fresh automatically by a Stop hook.** The ak plugin
registers `hooks/convo-index-stop.py` on the `Stop` event (end of every
assistant turn) and on `SessionStart` (belt-and-suspenders catchup). The hook
backgrounds the indexer and returns immediately, so session termination is
never blocked. By the time the next user turn starts, the new interactions
from the previous turn are already in the index. Automated sessions
(`CLAUDE_CODE_ENTRYPOINT` other than cli · claude-desktop · sdk-ts, i.e. `claude -p`
and the SDKs) start no indexer — `AK_CAPTURE=1` makes one count — and the next
person's session indexes what they wrote.

**The staleness warning is an invariant check, not a normal code path.** If
you ever see:

```
warn: index is 3 sessions behind (1 new, 2 modified). Run `tracer trace index` to refresh.
```

…during normal use, the Stop hook is broken — report it as a bug. The expected
state is that the index is always current, the warning never fires, and no
manual indexing is required. If you need to force a refresh (e.g. while
debugging the hook itself), run `trace index`.

## How it works

The indexer does a structural pass over conversation JSONL files:
1. Parses each message (user, assistant, system, tool results)
2. Segments into interactions at prompts the user typed — an interrupt, a local
   command (`/export`) or a compaction summary stays inside the turn it follows
3. Extracts tool calls, agent spawns, skill invocations, file operations
4. Attributes interactions to plugins via naming heuristics
5. Detects error signals (failed commands, user corrections)
6. Writes compact interaction records to an index file

No LLM calls required for the structural pass. The index is ~1-5% the size of raw conversations.
