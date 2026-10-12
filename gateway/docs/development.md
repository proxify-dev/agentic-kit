# Development

The gateway runs from source on Node ≥ 22.7 (`--experimental-strip-types`) and has no runtime npm
dependencies. `npm ci` installs only what typechecking needs.

```sh
npm ci               # typescript and @types/node, for typecheck; nothing the gateway runs on
npm test             # both suites
npm run test:wire    # gateway · direct · front-door · pool · try · run · keeper · dry-run · xray · service — no network, ephemeral ports, launchctl/systemctl stubbed
npm run test:engine  # the vendored suite and its ports — 551 pass, 2 skipped (src/engine/README.md says which and why)
npm run typecheck    # tsc over src/ and test/, the vendored JS through its sibling .d.ts files
```

One case needs cc-shim: the routing fragment run by `cc-shim fragment` (`test/direct.test.ts`).
Without it the case is skipped, or `CC_SHIM_PATH=/path/to/cc-shim.mjs` runs it.

**Releases.** None of its own: a change reaches people with the kit (`RELEASING.md` at the repo root). The kit's
checks run `npm ci`, `npm run typecheck` and `npm run test:wire` here.

## What the tests keep honest

Every server a test boots gets a scratch accounts file: the engine refreshes and writes the file it
is given, and the default is the machine's real one. The CLI tests run `enable`/`disable`/`status`
with HOME in scratch and a `launchctl` and `systemctl` that only record, so no regression can load or
unload the real unit. `test/service.test.ts` drives each backend — launchd, systemd, Task Scheduler —
with a fake `run` on any OS: the file it writes, the commands in order, how it reads `launchctl print`,
`systemctl show` and `schtasks /Query` back; and the per-OS parsers (`/proc/net/tcp`, `netstat -ano`,
`/proc/<pid>/stat`) on each OS's real output. The keeper tests stop it through `control` and through
`POST /_gateway/stop`, no signal.

The ones worth keeping honest: identity headers never leaving the machine, the no-header-on-disk
assertion, a blocked rule never contacting upstream, the account's token going upstream and the
body's `account_uuid` following it, a quota-spent 429 rotating while a per-minute 429 hops once and
never to a third account, a pinned route never hopping, a 401 refreshing exactly once with the new
token landing in **our** file and a 401 the refresh cannot cure failing over, a web page refused at
the door, a reset traced as no status rather than 200, `enable` with nothing to serve touching
nothing — and the keeper's: a hand-over under six keep-alive clients refusing no connection and
cutting no reply, a broken build (or one that dies right after starting) rejected while the old one
serves, three crashes giving way to the build before, a restart during crash recovery leaving exactly
one build serving, a stop during a restart leaving nothing, and the token rules — no refresh before
`adopt`, none after going quiet, and a refresh already under way landing in the file.

## Not built yet

- `PostResponse` hooks.
- Policy rules read from anywhere but the config file.
- Non-Anthropic providers.
- MITM capture of clients that ignore `ANTHROPIC_BASE_URL` (codex, non-Node CLIs — the vendored
  `mitm.js` is there, unwired).
- Carrying session→account stickiness across a hand-over (a session may land on another account once —
  one turn that re-reads its context, not an outage).
- Trace retention (`prune`): rows are kept; only bodies and tails expire after `blobDays`.
