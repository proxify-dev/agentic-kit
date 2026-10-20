# Control API and the TUI

The daemon answers a small HTTP API under `/_gateway/*` on its own port (4747 by default). The CLI and
the TUI are clients of it; so is anything you write.

## Who may knock

No key: being on this machine is the credential — so a web page in your browser, which is on this
machine too, is refused at the door (`src/front-door.ts`): a `Sec-Fetch-Site` other than
`same-origin`/`none`, an `Origin`, or a `Host` that does not name this machine is a 403, before
anything reads the body or reaches an account. curl, Claude Code, Node's fetch and urllib send none
of those. `OPTIONS` is answered here with no CORS grant, and an unclaimed `/_gateway/*` path (or
`/health`) is a local 404 — neither goes upstream with an account's token. Refusals count in
`refusals`, logged once a minute, with no trace row.

## Endpoints

| endpoint | what |
|---|---|
| `GET /_gateway/health` | up, port, pid, how many accounts can serve, `build` and `keeper` (null outside a keeper), `dryRun: true` (this build answers `x-brain-dry-run` itself — `xray` checks it) |
| `GET /_gateway/status` | traffic counters, in-flight requests, policy rules, accounts summary |
| `GET /_gateway/accounts` | the pool with quota, the route table, `live` — the engine's status — and `probe` — the prober's interval, next run, and each account's last answer |
| `PATCH /_gateway/accounts/<name>` | `{disabled, priority, current}` — edits the file, reloads the engine |
| `DELETE /_gateway/accounts/<name>` | removes it — out of the file and out of rotation now; `routesCleared` names any route sent back to rotation |
| `GET /_gateway/resolve?q=X` | the one account name X means (exact name or uuid, then a unique part of a name); an error for none, several or a disabled one |
| `POST /_gateway/reload` | re-read the accounts file |
| `POST /_gateway/probe` | one run of the quota prober over every enabled OAuth account |
| `GET · PUT /_gateway/settings` | `switchThreshold`, `quotaProbeSeconds` (live; 0 = off, at most 604800 — a longer timer would fire at once) · `holdSeconds`, `distributeSessions` (at restart) · GET also `stripOverageHeaders` |
| `PUT /_gateway/routes` | the route table |
| `GET /_gateway/trace?n=` | last N trace rows |
| `GET /_gateway/sessions?n=&all=1` | sessions of the last 24 hours, working first, at most `n`: state, title, main model, subagents by type, tokens, calls; `counts` over all. One-shot jobs (two calls or fewer, no subagent) are only counted unless `all=1` |
| `GET /_gateway/sessions/<id>` | one session (an id or a unique prefix) and its agents: parent, kind, task, model, state, tokens, calls, last tools; 404 when none |
| `POST /_gateway/stop` | loopback only: under the keeper, asks it to drain and stop; a gateway started by hand stops itself |

```sh
curl -s localhost:4747/_gateway/health
curl -s localhost:4747/_gateway/status | jq .capture
```

## The TUI

`cc-gateway tui` is the engine's screen, made a **client** of the daemon — every key is one
`/_gateway/*` call:

```
   key                    what it does
   ───────────────────────────────────────────────────────────────────────────────────
   s   switch             route fallback → prefer / pin X · ←→ prefer/pin · c clears it (Enter on the ◆ one changes nothing)
   d   enable / disable   PATCH /_gateway/accounts/X
   P   priority           same path
   p   check quota now    POST /_gateway/probe — the prober, once, without waiting for its round
   R   reload             POST /_gateway/reload
   g   settings           PUT /_gateway/settings — threshold and quota check live, hold/distribution at restart
   a   add account        browser login into the pool on screen, then reload
   x   remove account     DELETE /_gateway/accounts/X — pick, then answer y; its login leaves the gateway
   Tab sessions           GET /_gateway/sessions — every session, Enter its agents (below)
   q   detach             the daemon keeps running
```

One row per account, one column per quota bucket — `5-hour`, `week`, and `Sonnet week` / `Fable
week` when any account has one:

```
                                           5-hour       resets in    week         resets in    checked
  ► alice@example.com    serving   4 sess  [15%              4h32m]  [36%▋             3d3h]  now
    bob@example.com                       [▌2%               4h32m]  [6%                 4d]  1m ago
```

Each bucket is one bar that carries its numbers: the percent used at its left, the time until the
window resets at its right, the fill under them — green, yellow from 70%, red at the switch
threshold. Where no text sits on the fill's edge it is cut in eighths of a cell, so 2% still shows; a
bar too narrow for both keeps the percent. Only what is not ordinary is said: `serving`, or a state
like `disabled` · `throttled` · `exhausted` — a ready account's status is blank. `checked` is how old
the numbers are — the later of the last reply through the account and the prober's last answer —
yellow once two probe rounds have passed without news, with `check failed (403)` when the prober
could not read it; a disabled account is not checked and says nothing there. The title line says how
often the prober runs, or that it is off. The engine's live numbers win over its state file: it
clears a bucket the moment its window passes.

`►` is the engine's current account; `◆` is the route fallback you set with `s` — they can differ,
because `prefer` steers requests without moving the engine's `currentIndex`.

Below the accounts, what only the gateway knows: requests in flight and how long each has run; then
the recent ones, in local time, sorted by when they began (a row is written when its reply ends) —
model, ttfb and total time, tokens in / cached / out, identity (run / agent / session), the account
that served it, named `pinned` or `preferred` when a route decided (rotation is the default and goes
unsaid), policy verdicts.

`cc-gateway tui --trial` shows the screen of a running `try` gateway instead.

## The sessions views

Tab in the TUI (or `cc-gateway tui --sessions`) shows what goes through the gateway by session
rather than by account — two views over `GET /_gateway/sessions`:

```
 Sessions  3 working · 15 your turn · 1 subagent running · 2 in flight   1445 one-shot jobs hidden — o shows them
  state       session                                           model      agents                      context  tokens calls  last
> ● working   Gateway sessions view  agentic-kit 73ccfa97       opus 5.5   1 running · Explore 2 · fork 1   44k    1.2M    47   now
  ● your turn proxify  0e85f8df                                 opus 5.5   custom 1 · general-purpose 1   189k   43.6M   186 5m ago
```

Enter opens one session as a tree, each agent under the one that started it:

```
  agent                                     model      state          context    out calls     time
  main                                      opus 5.5   ● calling         44k    12k    47      now
  ├─ fork  write the docs                   opus 5.5   ✓ done           120k     8k    30   5m ago
  │  └─ Explore  find the doc readers       haiku 5.5  ● tools           38k     2k    14   for 2m
  └─ general-purpose  research the format   sonnet 5.5 ● calling         61k     4k     9   for 1m
```

↑↓ picks an agent; under the tree, its models with their turn counts, its tokens (input, cache
read, cache write, output), its last tools and stop reason. Esc goes back, Tab to the accounts.

Where each part comes from:

| shown | from |
|---|---|
| the session, the main thread, a subagent, a fork | `x-claude-code-session-id`, `-request-class`, `-agent-type` |
| which subagent, under which agent | `x-claude-code-agent-id`, `-parent-agent-id` — sent to any base URL |
| model, tokens, calls | the request and the reply; `context` is the newest turn's input plus cache — how full its window is |
| state | the newest turn's stop: `calling` (a call in flight) · `tools` (stopped on `tool_use` less than 15 minutes ago) · `your turn` (the main thread ended its turn, less than an hour ago) · `done` (a subagent ended its turn, or its meta file says finished) · `idle` |
| title, task | Claude Code's files, never a message: the transcript's newest `custom-title` or `ai-title` line, and `<session>/subagents/agent-<id>.meta.json` |

Side calls (titles, classifiers, compaction) count in `side`, never in the state. Rows written
before the gateway kept the agent ids cannot tell two subagents of one type apart: they show as
one node per type, "earlier calls, not told apart". The daemon keeps the last 24 hours in memory
and rebuilds them from the day files on the first ask after a start.

