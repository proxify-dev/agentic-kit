# engine/ — teamclaude's credential plane, vendored

Copied verbatim from the `src/` of teocns/teamclaude (a local fork at `830870c`
"Merge upstream/master (v1.1.12)", plus that checkout's uncommitted soft-pin edits to
`account-manager.js`, `server.js`, `test/account-pin.test.js`). The tests came with it:
`../../test/engine/*.test.js`, import paths rewritten from `../src/` to `../../src/engine/`,
run by `npm test`. Since then, fixes from upstream up to v1.1.22 are **cherry-picked**, not
re-vendored — see *Ported from upstream* below for which, and why not the whole release.

**License.** teamclaude is MIT, © 2026 KarpelesLab — [`LICENSE`](LICENSE) beside this file is its
text, unchanged, and travels with the code wherever this folder goes.

**Why verbatim.** The engine is ~2,600 lines of semantics the audit found no equivalent for —
the quota-spent-vs-per-minute 429 split, the 10 s forced-refresh floor, the transient-vs-auth
refresh failure split, the serialized config write chain. Every one of those is a trap a clean
rewrite walks into; the tests (551 pass, 2 skipped) are what keep them honest. So the code moves
as-is and gets TS-ified one module at a time, each move carrying its tests.

**What the gateway imports** (everything else is dead weight until the TS-ify pass deletes it):

```
   used by ../engine.ts     account-manager  server (forwardRequest, resolveAccountPin)
                            config (atomicConfigUpdate, loadState, saveState)  identity
                            resolve-accounts  oauth (login, import, profile)  prober  sx (dormant)
   pulled in transitively   model  session-tracker  tool-pair-sanitize  account-uuid-rewrite
                            upstream-fetch  upstream-proxy  mitm  x509  request-log
                            json-format-stream  egress-guard  safe-text
   used by ../pool.ts       config (withConfigLock, writeJsonAtomic)  identity  oauth
   never imported           index (teamclaude's CLI)  tui  status-renderer  service  shim
                            claude-env  alias  updater  terminal-title  warmer  crash-log
```

**The one seam.** `config.js` finds its file through `TEAMCLAUDE_CONFIG`; `engine.ts` sets that
env to the gateway's own accounts file before the first call, so the engine reads and writes
`~/.config/ak/accounts.json` and nothing else. The file keeps the engine's schema, so an
accounts file written by the upstream project loads as it is.

**Local edits to vendored files** — keep this list exact so an upstream diff stays readable.
Each is marked `ak-gateway:` in place unless it says otherwise.

- **The soft pin** (the owner's, vendored with the base; not marked): `server.js` reads
  `ctx.pinSoft` — `/tc-prefer/<name>` biases selection to that account while it is eligible, then
  rotates; `/tc-acct/<name>` stays a hard pin. `account-manager.js` gains a public `isAvailable`
  for it. Tests: the soft-pin half of `test/account-pin.test.js`.
- `account-manager.js` **`_soonerResetPeer`**: in `_select`, a healthy current account is left
  for a same-priority one whose shared weekly window resets at least `SOONER_RESET_MARGIN_MS` (1 h)
  sooner; `previewRouteIndex` mirrors it. Upstream stays put within a tier until the current account
  is spent, so a re-login or a cleared route never re-ranks. Tests: the same-tier block of
  `test/engine/rotation-priority.test.js`.
- `server.js` **`hardPinned`** (`pinnedIndex` set and not `pinSoft`): every upstream port that
  asked "is this request pinned" asks this. Upstream v1.1.22 has no soft pin, and its failover
  hops outrank its hard pin (a 529 on the pinned account is served by another) — here a hard pin
  never hops, fails over, or leaks. `test/engine/pin-hop.test.js` pins the rule through every
  port below.
- `server.js` **the hop target**: `ctx.hopTo` is taken first but never over a hard pin nor onto an
  account disabled since it was picked; and after a hop, the sx and inline retries carry `hopTo`
  so re-selection cannot walk the fleet cursor onto the sibling (upstream re-selects there).
- `server.js` **`allRefused`** counts enabled accounts (a removed row stays in place, disabled,
  and made "every account was refused" unreachable); the 502 says `(401/403)`.
- `server.js` **`computeRetryAfter`** reads the clocks that block an OAuth account (5h, weekly and
  family buckets over the threshold) and skips disabled and errored ones; the exhausted 429 counts
  enabled accounts. A minimal take on upstream 189ec72. Test: `test/engine/exhausted-retry-after.test.js`.
- `server.js` **`ctx.failure`**: the catch records why upstream failed (`describeConnectError`),
  so `engine.ts` can trace a reset as no status instead of the 200 `res.statusCode` defaults to.
- `tool-pair-sanitize.js`: a `thread: {type: "continue"}` body is forwarded as sent — its leading
  tool_result answers a tool_use held server-side, and pruning it sent `messages: []`. Not fixed
  upstream as of v1.1.22.
- `config.js`: `writeJsonAtomic` is exported, so `../pool.ts` writes the accounts file the way the
  engine does, under the same `withConfigLock`. (Our own earlier tmp+rename edit is gone —
  upstream's writer replaced it.)
- `config.js` **`renameRetry`**: the temp+rename of `writeJsonAtomic` (and `mitm.js`'s
  `atomicWrite`) retries 5 x 50 ms on EPERM/EBUSY/EACCES on Windows, where replacing a file
  another process holds open fails for a moment. POSIX: one rename, as upstream.
- `oauth.js` **`openBrowser`**: no shell. Windows `rundll32 url.dll,FileProtocolHandler` (upstream's
  `start "url"` took the URL for a window title), Linux `wslview` then `xdg-open`; a missing
  opener is ignored — the URL is printed either way. `browserCommands` is exported for the test.
- `prober.js`: OAuth rows only, through upstream's `_probeable` (not disabled, no dead refresh
  token); a disabled account's status reads `disabled`. A probe can refresh the token, and a
  refresh takes the grant from whoever else holds that login.
- `test/server-listen-error.test.js`: one test skipped; it spawns `index.js` and fails upstream too.
- `test/connect-error-message.test.js`: the MITM tunnel case skipped — the gateway runs no MITM,
  and that one log site of 44477b1 is not ported.

**Ported from upstream** (after v1.1.12). Each port kept upstream's code and comments where it
fit this copy, and brought its tests. A full re-vendor of v1.1.22 was ruled out: its
`forwardRequest` calls about a dozen account-manager methods this copy lacks, reads a different
`ctx` (`pinKey`, `provider`, `signal`), has no soft pin, and statically imports the dashboard,
MCP and Codex modules.

```
   what                                              upstream                        adapted
   the relay test cannot hang the suite              814cdd7 (#162)                  —
   fsync'd temp+rename, symlink, advisory lock,      5fd6c91 1ca6761 ae92476         no ensureAccountIds
     a file with no accounts key                     007eea3
   a token response checked before it is stored      9cc0078 (oauth part)            no Codex
   a rejected refresh token is never re-sent         be9cb8d cd84152 f5955bc         —
   the probe interval capped, dead logins unprobed   4ccbe02                         merged with our edit
   family models gate on max(family, shared weekly)  2859fa9 (#191)                  single threshold
   DNS / EPIPE / all-addresses failures reset fast   18b0360 44477b1 f5955bc         hard pin only
   one bounded hop on a 429 or 5xx                   f603ee0 eed3f40                 hardPinned, hopTo carry
   a headerless 429 is about the request             e0c9e9d 8b9a54a                 no abort signal
   a second 401 fails over                           72c3943 (401 part)              allRefused count
   the exhausted 429's retry-after                   189ec72 (minimal)               ours
```

`safe-text.js` is new, verbatim from upstream master (for `safeLine`). Not taken, on purpose:
576ea54 (an API-key account back after a 401 — re-enabling is the way back), ea9864a (a 120 s
keep-alive would stretch the keeper's drain), 6412bb1 (needs a key and a reverse proxy), 34212e1
(this copy has no `unifiedStatus` gate), and everything Codex, dashboard, TUI, MCP or MITM.
