/**
 * What the TUI's quota bars say, and whether they can be believed.
 *
 * Born from a screenshot: every bar printed its reset time where the percent
 * should be, an account nobody had used for hours still showed the numbers it
 * had when traffic left it (the prober was off by default), and the request
 * list read 12:14 beside a log line reading 15:14 (UTC against local).
 *
 * The blocks are pure functions of what the daemon answered, so these read
 * them as text — no terminal, no daemon.
 */
import assert from "node:assert/strict"
import test from "node:test"

import { DEFAULT_PROBE_SECONDS, probeSecondsFor } from "../src/engine.ts"
import type { ProbeStatus } from "../src/engine/prober.js"
import type { AccountView } from "../src/quota.ts"
import { accountsBlock, activeBlock, compact, fallbackAfter, formatAgo, formatMs, meter, recentBlock, vw } from "../src/tui.ts"
import type { AccountsData } from "../src/tui.ts"
import type { UpstreamAccount } from "../src/upstream-status.ts"

const plain = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, "")
const NOW = Date.parse("2026-09-24T12:00:00Z")
const H = 3_600_000

const view = (name: string, disabled = false): AccountView => ({
  name,
  org: null,
  accountUuid: null,
  state: disabled ? "disabled" : "usable",
  priority: 0,
  expiresAt: null,
  quota: { session: null, week: null, weekSonnet: null, weekFable: null, sessionReset: null, weekReset: null, status: null },
})

const live = (name: string, quota: Record<string, number | null>, extra: Partial<UpstreamAccount> = {}): UpstreamAccount => ({
  name,
  type: "oauth",
  priority: 0,
  status: "active",
  sessions: 0,
  disabled: false,
  rateLimitedUntil: null,
  pausedUntil: null,
  totalRequests: 0,
  lastUsed: null,
  quota,
  ...extra,
})

const probe = (accounts: ProbeStatus["accounts"], over: Partial<ProbeStatus> = {}): ProbeStatus => ({
  enabled: true,
  intervalSeconds: 300,
  running: false,
  lastRunStartedAt: null,
  lastRunFinishedAt: null,
  nextRunAt: new Date(NOW + 3 * 60_000).toISOString(),
  accounts,
  ...over,
})

/** The screenshot's pool: one serving, one idle for hours, one switched off. */
function pool(): AccountsData {
  return {
    accounts: [view("serving@x"), view("idle@x"), view("off@x", true)],
    live: {
      currentAccount: "serving@x",
      switchThreshold: 0.96,
      sessionsActive: 4,
      sessionsKnown: 16,
      distributeSessions: false,
      at: NOW,
      accounts: [
        live(
          "serving@x",
          { unified5h: 0.07, unified5hReset: NOW + 4 * H + 56 * 60_000, unified7d: 0.34, unified7dReset: NOW + 75 * H, unified7dFable: 0.06, unified7dFableReset: NOW + 75 * H },
          { sessions: 4, lastUsed: new Date(NOW - 5_000).toISOString() },
        ),
        live("idle@x", { unified5h: 0, unified7d: 0.06, unified7dReset: NOW + 96 * H, unified7dFable: 0.05, unified7dFableReset: NOW + 96 * H }),
        live("off@x", { unified7d: 0.47, unified7dReset: NOW + 5 * H, unified7dFable: 0.73, unified7dFableReset: NOW + 5 * H }, { disabled: true }),
      ],
    },
    probe: probe([
      { name: "serving@x", status: "ok", lastProbedAt: new Date(NOW - 2 * 60_000).toISOString(), durationMs: 80, error: null },
      { name: "idle@x", status: "ok", lastProbedAt: new Date(NOW - 4 * H).toISOString(), durationMs: 80, error: null },
      { name: "off@x", status: "disabled", lastProbedAt: null, durationMs: null, error: null },
    ]),
  }
}

test("tui: every bar carries its percent and its reset time inside it", () => {
  // The numbers sit on the bar — percent at its left, reset at its right — so
  // the row holds no columns of its own for them.
  assert.equal(plain(meter(0.35, 20, 0.96, { reset: "3d3h" })), ` 35%${" ".repeat(11)}3d3h `)
  const rows = accountsBlock(pool(), { width: 165, now: NOW }).map(plain)
  assert.match(rows[1], /5-hour\s+resets in\s+week\s+resets in\s+Fable week\s+resets in\s+checked/)
  const serving = rows.find((r) => r.includes("serving@x"))!
  for (const want of ["7%", "4h56m", "34%", "3d3h", "6%"]) assert.ok(serving.includes(want), `${want} in: ${serving}`)
  assert.match(serving, /serving\s+4 sess/)
  assert.doesNotMatch(rows.find((r) => r.includes("idle@x"))!, /ready/, "the ordinary goes unsaid")
})

test("tui: columns line up under their header, whatever each account knows", () => {
  const rows = accountsBlock(pool(), { width: 165, now: NOW }).map(plain)
  const header = rows[1]
  const at = (s: string, label: string) => s.indexOf(label)
  // The Fable column is drawn for every row because one account has it; each
  // row's Fable percent ends where the header's column does.
  const fableCol = at(header, "Fable week")
  const checkedCol = at(header, "checked")
  assert.ok(fableCol > 0 && checkedCol > fableCol)
  for (const r of rows.slice(2)) {
    assert.equal(r.slice(checkedCol - 2, checkedCol), "  ", `aligned: ${r}`)
    assert.notEqual(r[checkedCol], " ", `aligned: ${r}`)
  }
})

test("tui: an account idle past two probe rounds says how old its numbers are", () => {
  const rows = accountsBlock(pool(), { width: 165, now: NOW })
  const idle = rows.find((r) => r.includes("idle@x"))!
  assert.ok(plain(idle).includes("4h ago"))
  assert.ok(idle.includes("\x1b[33m4h ago"), "stale is yellow")
  const serving = rows.find((r) => r.includes("serving@x"))!
  assert.ok(plain(serving).endsWith("now"), "a response seconds ago beats the probe two minutes ago")
  const off = plain(rows.find((r) => r.includes("off@x"))!)
  assert.ok(off.includes("disabled"))
  assert.doesNotMatch(off, /ago|now|never/, "a disabled account is not checked, and says nothing about it")
})

test("tui: a failed check is named on its account", () => {
  const data = pool()
  data.probe!.accounts[1] = { name: "idle@x", status: "error", lastProbedAt: new Date(NOW).toISOString(), durationMs: 50, error: "HTTP 403: OAuth authentication is currently not allowed" }
  const idle = plain(accountsBlock(data, { width: 165, now: NOW }).find((r) => r.includes("idle@x"))!)
  assert.ok(idle.includes("check failed (403)"), idle)
})

test("tui: the title says how often quota is checked — or that it is not", () => {
  assert.ok(plain(accountsBlock(pool(), { width: 165, now: NOW })[0]).includes("quota checked every 5m, next in 3m"))
  const off = pool()
  off.probe = probe([], { enabled: false, intervalSeconds: 0, nextRunAt: null })
  assert.ok(plain(accountsBlock(off, { width: 165, now: NOW })[0]).includes("quota check off"))
})

test("tui: a bar too narrow for both keeps the percent and drops the reset time", () => {
  assert.equal(plain(meter(0.35, 8, 0.96, { reset: "4h56m" })).trim(), "35%")
  for (const width of [165, 120, 100]) {
    const serving = accountsBlock(pool(), { width, now: NOW }).map(plain)[2]
    for (const want of ["34%", "3d3h"]) assert.ok(serving.includes(want), `${want} at ${width}: ${serving}`)
  }
})

test("tui: the bar's edge is cut in eighths, so a small number still shows", () => {
  assert.equal(vw(meter(0.03, 10, 0.96)), 10)
  assert.match(plain(meter(0.03, 10, 0.96)), /^[▏▎▍]3%/)
  assert.equal(plain(meter(null, 10, 0.96)), ` —${" ".repeat(8)}`)
  const over = meter(1.2, 10, 0.96)
  assert.ok(plain(over).startsWith(" 120%"), "the percent does not clamp")
  assert.ok(!over.includes("48;5;236"), "the fill does — no track left")
  assert.ok(meter(0.97, 10, 0.96).includes(";41m"), "red at the switch threshold")
  assert.ok(meter(0.5, 10, 0.96).includes(";42m"), "green below 70%")
})

test("tui: recent requests read in local time, in the order they began", () => {
  const row = (ts: string, model: string) => ({ ts, status: 200, model, ttfbMs: 956, durMs: 4200, usage: { input: 2, output: 1745, cacheRead: 20196, cacheCreation: 0 }, identity: { agent: null, run: null, session: "d5a094d2-0000" }, account: "a@x", accountSource: "rotated", policy: [] })
  const lines = recentBlock([row("2026-09-24T12:14:24Z", "second"), row("2026-09-24T12:14:20Z", "first")], 10).map(plain)
  const body = lines.slice(2)
  assert.ok(body[0].includes("first") && body[1].includes("second"))
  const local = new Date("2026-09-24T12:14:20Z").toTimeString().slice(0, 8)
  assert.ok(body[0].includes(local), `local ${local} in: ${body[0]}`)
  for (const want of ["956ms", "4.2s", "20k", "1.7k", "sess:d5a094d2"]) assert.ok(body[0].includes(want), `${want} in: ${body[0]}`)
  assert.ok(!body[0].includes("rotated"), "rotation is the default; only a pin or a preference is named")
})

test("tui: active requests show how long they have been running, longest first", () => {
  const act = (id: string, ago: number) => ({ id, ts: new Date(NOW - ago).toISOString(), path: "/v1/messages", model: "claude-opus-5-5", who: `session:${id}`, account: null })
  const lines = activeBlock([act("young", 5_000), act("old", 90_000), act("mid", 20_000)], 3, { now: NOW, max: 2 }).map(plain)
  assert.match(lines[1], /^\s+2m\s+claude-opus-5-5\s+session:old/)
  assert.match(lines[2], /^\s+20s\s/)
  assert.ok(lines[3].includes("… 1 more"))
})

test("tui: small formatters", () => {
  assert.equal(compact(956), "956")
  assert.equal(compact(12_662), "13k")
  assert.equal(compact(1_745), "1.7k")
  assert.equal(formatMs(2602), "2.6s")
  assert.equal(formatMs(null), "-")
  assert.equal(formatAgo(30_000), "now")
  assert.equal(formatAgo(4 * H + 5), "4h ago")
})

test("quota probe: on by default against Anthropic, off against anything else, the file wins", () => {
  assert.equal(probeSecondsFor({}, "https://api.anthropic.com"), DEFAULT_PROBE_SECONDS)
  assert.equal(probeSecondsFor({}, "http://127.0.0.1:4000"), 0, "a fake upstream has no usage API to ask")
  assert.equal(probeSecondsFor({ quotaProbeSeconds: 0 }, "https://api.anthropic.com"), 0, "an explicit off stays off")
  assert.equal(probeSecondsFor({ quotaProbeSeconds: 120 }, "http://127.0.0.1:4000"), 120)
})

test("tui: s → Enter on the account already preferred changes nothing; only c clears", () => {
  const set = { fallback: { account: "proxify", mode: "prefer" as const }, rules: [] }
  const empty = { fallback: { account: "", mode: "prefer" as const }, rules: [] }
  assert.equal(fallbackAfter(set, { account: "proxify", mode: "prefer" }), null, "the same pick is not a clear")
  assert.deepEqual(fallbackAfter(set, { account: "proxify", mode: "pin" })?.fallback, { account: "proxify", mode: "pin" })
  assert.deepEqual(fallbackAfter(set, { account: "gmail", mode: "prefer" })?.fallback, { account: "gmail", mode: "prefer" })
  assert.deepEqual(fallbackAfter(set, null)?.fallback, { account: "", mode: "prefer" }, "c clears")
  assert.equal(fallbackAfter(empty, null), null, "nothing to clear")
})
