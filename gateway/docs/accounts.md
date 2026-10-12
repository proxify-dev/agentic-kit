# Accounts, rotation and quota

The gateway holds its own logins. Every model call is served by one account from the pool; the
gateway injects that account's token, learns its quota from the reply, and moves to another account
when one is spent.

```sh
cc-gateway account                          # the pool: name, type, org, priority, state, 5h/7d/F7, expiry
cc-gateway account login [--name N]         # browser login (OAuth PKCE) — the one that matters
cc-gateway account login --api [--name N]   # an Anthropic API key
cc-gateway account import [--from PATH]     # take Claude Code's own login (~/.claude/.credentials.json)
cc-gateway account enable|disable NAME
cc-gateway account priority NAME N          # lower = preferred, default 0
cc-gateway account remove NAME
cc-gateway account choose                   # pick one on the terminal, print its name
cc-gateway reload                           # the daemon re-reads the file (login/import/enable do this for you)
```

## The engine

The credential plane — quota model, rotation, the quota-spent-vs-per-minute 429 split, the
one-forced-refresh-on-401, the locked, fsync'd token write — is a vendored engine under
[`src/engine/`](../src/engine/) with its tests, booted by `src/engine.ts` on the gateway's accounts
file, `<configDir>/ak/accounts.json` (`~/.config` on macOS and Linux, `%APPDATA%` on Windows; the data
dir is `~/.local/share` or `%LOCALAPPDATA%`, both from `src/brand.ts`).
[`src/engine/README.md`](../src/engine/README.md) says what is vendored and why verbatim, and which
later upstream fixes were cherry-picked in. The engine is
[teamclaude](https://github.com/KarpelesLab/teamclaude), MIT ([`src/engine/LICENSE`](../src/engine/LICENSE)).

## Which account serves

```
   1  a pin or preference for this request     /tc-acct/ · /tc-prefer/ · the route table (TUI `s`)
   2  the lowest priority value                 `account priority NAME N`; a better tier takes over at once
   3  within a tier: the weekly window that     the current account is left for one whose shared weekly
      resets soonest                            resets at least 1 h sooner, even while it is healthy
```

Rule 3 spends the quota that would otherwise expire unused. It runs on every request, so an account
that comes back from a re-login or a reset is weighed on the next request. An account whose reset is
not known yet never draws traffic this way: the prober fills that in within minutes. Reset times stay
fixed until a window resets, so the order stays the same and does not flip back and forth. Each open
session moves once and pays one prompt-cache miss.

## When an account says no

What the engine does with each answer, and what the client sees:

```
   upstream answers                         the engine                                        the client gets
   429, quota spent (unified-*-status)      holds the account, rotates                         the next account's reply
   429, per-minute throttle                 pauses it; ONE hop to an idle sibling, else waits  the reply, or the 429
   429, no retry-after and no rate headers  about the request: no pause; one hop, one 2 s retry upstream's reason, no retry-after
   5xx / 529                                ONE hop to a sibling                               the reply, or the 5xx
   401                                      one forced refresh, then fails over like a 403     never the 401 — a 502 if all refuse
   DNS / EPIPE / all addresses refused      closes the connection at once                      a reset to retry, not a fake 429
   every account spent                      —                                                  429, retry-after = the real reset
```

A hop detours one request: the fleet's current account does not move. A `/tc-acct/` pin never hops
or fails over — it is that account or an error; `/tc-prefer/` ranges over the fleet.

## One refresher per login

Whoever refreshes an OAuth token owns the grant: a refresh rotates the refresh token, and every other
holder's copy dies with `invalid_grant`. The gateway refreshes what is in its file, so a login copied
in from anywhere else (`account import`) must stop being refreshed there first. A fresh
`account login` is always safe — it is a grant nobody else holds.

## Pinning a launch to an account

`cc-gateway run --account X -- claude` asks the gateway (`/_gateway/resolve?q=X`) which account X
means — exact name or uuid first, then a unique part of a name — and points that launch at
`/tc-acct/<name>`. No match, two matches or a disabled account and it refuses to launch rather than
spend another account's quota. `cc-gateway env --account X` prints the same environment.

With cc-shim, `claude --account X` does the same: cc-shim takes the flag off the command line, and its
`05-gateway.mjs` fragment resolves X and points the launch at `/tc-acct/<name>` (`--prefer X`:
`/tc-prefer/<name>`, which may rotate away). A bare `--account` lists the enabled accounts through
`cc-gateway account choose`, which prints the menu on stderr and only the chosen name on stdout.

A standing pin for every request is the config's `account` field (or `AK_GATEWAY_ACCOUNT` in the
daemon's environment); empty means rotate.

## Quota is checked, not only overheard

The engine learns an account's buckets from the headers of every reply through it — so an account
nobody is using keeps the numbers it had when traffic left it. The prober closes that gap: every
5 minutes (`DEFAULT_PROBE_SECONDS`, `src/engine.ts`) it reads each enabled account's buckets from
Anthropic's usage API, which spends nothing. The accounts file's `quotaProbeSeconds` overrides it,
`0` turns it off; a gateway aimed at any upstream but Anthropic's (a test's fake) does not probe unless
the file says so. A disabled account is never probed — a probe can refresh its token, and that would
take the grant from whoever holds it.

## Pool settings

These live in the accounts file beside the accounts, and can be changed live from the TUI (`g`) or
`PUT /_gateway/settings` ([control API](control-api.md)):

| field | default | what |
|---|---|---|
| `switchThreshold` | `0.98` | the share of a quota bucket used at which an account counts as spent and the engine moves on; live |
| `quotaProbeSeconds` | `300` | the prober's interval; `0` = off, at most 604800; live |
| `holdSeconds` | the config's `holdSeconds` (`0`) | when every account is spent, hold the connection this long before a 429; at restart |
| `distributeSessions` | `false` | keep each session on its account (cache reuse), but spread new sessions across equal-priority accounts by load instead of all onto the current one; at restart |

## Removing is live

`account remove X` (and the TUI's `x`) goes through the running daemon: the row leaves the file, the
engine stops serving it at once, and a route that named it goes back to rotation. The engine knows
accounts by position — a refresh under way reports back by index — so a removed account keeps its
place, disabled and hidden from every status, until the next boot reads a file without it; splicing
it out would let its last refresh land in the neighbour's row. Logging the same account in again
revives it in that place. With the daemon down, `remove` edits the file only.

## Every write takes the engine's lock

The daemon rotates refresh tokens into the file while it serves, so a CLI edit holds
`<accounts file>.lock` across its read and its write — the engine's own `withConfigLock` — and a
login fetches its profile before taking it, since the daemon waits 2 s at most. The file lands by
fsync'd temp file and rename, mode 0600.
