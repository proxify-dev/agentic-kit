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
