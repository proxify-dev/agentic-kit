/**
 * ak-gateway tui — the terminal face of the wire.
 *
 * A CLIENT of the daemon: it polls `/_gateway/*` and acts through the same
 * endpoints, so `q` leaves and the daemon does not notice. (The engine's own
 * TUI ran inside the proxy, so closing the terminal closed the proxy.)
 *
 *   s     route fallback → prefer/pin X; c clears it (Enter on the ◆ one changes nothing)
 *   d P   PATCH /_gateway/accounts/X — enable/disable, priority
 *   x     DELETE /_gateway/accounts/X — remove, after a y/n
 *   p     POST /_gateway/probe — read every account's quota now (the prober also runs on its own)
 *   R     POST /_gateway/reload
 *   g     PUT /_gateway/settings — threshold and probe live, hold/distribution at restart
 *   a     browser login into the pool, then reload
 *
 * The blocks it draws — accounts, active, recent — are pure functions of what
 * the daemon answered, exported so a test reads them without a terminal.
 *
 * Hand-rolled ANSI, no dependencies.
 */
import { spawnSync } from "node:child_process"
import { fileURLToPath } from "node:url"
import type { Config } from "./config.ts"
import type { ProbeStatus } from "./engine/prober.js"
import * as pool from "./pool.ts"
import type { AccountView } from "./quota.ts"
import type { RouteMode, RouteTable } from "./routes.ts"
import type { UpstreamAccount, UpstreamStatus } from "./upstream-status.ts"
import { envName } from "./brand.ts"
import { command as gw } from "./command.ts"

const ESC = "\x1b["
const RESET = `${ESC}0m`
const bold = (s: string) => `${ESC}1m${s}${RESET}`
const dim = (s: string) => `${ESC}2m${s}${RESET}`
const rev = (s: string) => `${ESC}7m${s}${RESET}`
const fg = (c: number, s: string) => `${ESC}${c}m${s}${RESET}`
const green = (s: string) => fg(32, s)
const yellow = (s: string) => fg(33, s)
const red = (s: string) => fg(31, s)
const cyan = (s: string) => fg(36, s)
const gray = (s: string) => fg(90, s)
const magenta = (s: string) => fg(35, s)

/** Visible width: strip escapes. */
export const vw = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, "").length
const fit = (s: string, w: number) => {
  if (vw(s) <= w) return s
  let out = ""
  let n = 0
  for (const part of s.split(/(\x1b\[[0-9;]*m)/)) {
    if (part.startsWith("\x1b")) {
      out += part
      continue
    }
    const take = Math.max(0, w - 1 - n)
    out += part.slice(0, take)
    n += Math.min(part.length, take)
    if (n >= w - 1) return `${out}…${RESET}`
  }
  return out
}
const rpad = (s: string, w: number) => s + " ".repeat(Math.max(0, w - vw(s)))
const lpad = (s: string, w: number) => " ".repeat(Math.max(0, w - vw(s))) + s
const clip = (s: string, w: number) => (s.length > w ? `${s.slice(0, w - 1)}…` : s.padEnd(w))
const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n))
/** Local wall time — the trace stores UTC, the person reading this does not live there. */
const hhmm = (t = Date.now()) => new Date(t).toTimeString().slice(0, 8)

/** A length of time, short: 45s, 12m, 4h56m, 3d3h. */
export function formatSpan(ms: number): string {
  if (ms < 60_000) return `${Math.max(0, Math.ceil(ms / 1000))}s`
  const mins = Math.ceil(ms / 60_000)
  if (mins < 60) return `${mins}m`
  const hrs = Math.floor(mins / 60)
  const rm = mins % 60
  if (hrs < 24) return rm > 0 ? `${hrs}h${rm}m` : `${hrs}h`
  const days = Math.floor(hrs / 24)
  const rh = hrs % 24
  return rh > 0 ? `${days}d${rh}h` : `${days}d`
}

/** Time until a window resets, or "" once it has. */
export function formatReset(resetTs: number | null | undefined, now = Date.now()): string {
  if (!resetTs || resetTs <= now) return ""
  return formatSpan(resetTs - now)
}

/** How old a number is: now, 4m ago, 3h ago, 2d ago. */
export function formatAgo(ms: number): string {
  if (ms < 60_000) return "now"
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`
  if (ms < 48 * 3_600_000) return `${Math.floor(ms / 3_600_000)}h ago`
  return `${Math.floor(ms / 86_400_000)}d ago`
}

/** 12662 → 12.7k, 956 → 956, 1_200_000 → 1.2M. */
export function compact(n: number): string {
  if (n < 1000) return String(n)
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k`
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}

/** 956 → 956ms, 2602 → 2.6s, 75000 → 1m15s. */
export function formatMs(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "-"
  if (n < 1000) return `${Math.round(n)}ms`
  if (n < 10_000) return `${(n / 1000).toFixed(1)}s`
  if (n < 60_000) return `${Math.round(n / 1000)}s`
  return `${Math.floor(n / 60_000)}m${Math.round((n % 60_000) / 1000)}s`
}

// ---- quota bars

const EIGHTHS = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]
const TRACK_BG = "48;5;236"
const TRACK_TEXT = "38;5;252"
const MUTED_BG = "48;5;240"
const MUTED_TEXT = "38;5;245"

/** Palette colour: green, yellow from 70%, red at the switch threshold — where rotation treats the bucket as spent. */
const level = (r: number, th: number) => (r >= th ? 1 : r >= 0.7 ? 3 : 2)

/**
 * A quota bar that carries its own numbers: the percent used at its left, the
 * time until the window resets at its right, the fill drawn under them. Where
 * no text sits on the edge of the fill it is cut in eighths of a cell, so 2%
 * still shows. The reset time goes when the bar is too narrow for both.
 */
export function meter(ratio: number | null, w: number, th: number, opts: { reset?: string; muted?: boolean } = {}): string {
  const known = ratio != null && !Number.isNaN(ratio)
  const left = known ? `${Math.round(ratio * 100)}%` : "—"
  const right = opts.reset ?? ""
  const chars = Array.from({ length: w }, () => " ")
  const put = (s: string, at: number) => {
    for (let i = 0; i < s.length; i++) if (at + i >= 0 && at + i < w) chars[at + i] = s[i]
  }
  put(left, 1)
  if (right && 1 + left.length + 1 + right.length + 1 <= w) put(right, w - 1 - right.length)

  const c = known ? level(ratio, th) : 2
  const fill = opts.muted ? `97;${MUTED_BG}` : `${c === 1 ? 97 : 30};4${c}`
  const edge = opts.muted ? `38;5;240;${TRACK_BG}` : `3${c};${TRACK_BG}`
  const track = `${opts.muted || !known ? MUTED_TEXT : TRACK_TEXT};${TRACK_BG}`
  const eighths = known ? Math.round(clamp(ratio, 0, 1) * w * 8) : 0
  const full = Math.floor(eighths / 8)
  const part = eighths % 8

  let out = ""
  let cur = ""
  chars.forEach((ch, i) => {
    let style = track
    if (i < full) style = fill
    else if (i === full && part > 0) {
      if (ch === " ") [ch, style] = [EIGHTHS[part], edge]
      else if (part >= 4) style = fill
    }
    if (style !== cur) out += `${ESC}0;${(cur = style)}m`
    out += ch
  })
  return out + RESET
}

type Buckets = {
  ses: number | null
  sesR: number | null
  wk: number | null
  wkR: number | null
  s7: number | null
  s7R: number | null
  f7: number | null
  f7R: number | null
}

/**
 * The engine's live numbers when it answered — it is the one rotating, and it
 * clears a bucket the moment its window passes — else its last written state.
 */
function bucketsOf(a: AccountView, l: UpstreamAccount | undefined): Buckets {
  if (!l) {
    const q = a.quota
    return { ses: q.session, sesR: q.sessionReset, wk: q.week, wkR: q.weekReset, s7: q.weekSonnet, s7R: null, f7: q.weekFable, f7R: null }
  }
  const n = (k: string): number | null => (typeof l.quota[k] === "number" ? (l.quota[k] as number) : null)
  return {
    ses: n("unified5h"),
    sesR: n("unified5hReset"),
    wk: n("unified7d"),
    wkR: n("unified7dReset"),
    s7: n("unified7dSonnet"),
    s7R: n("unified7dSonnetReset"),
    f7: n("unified7dFable"),
    f7R: n("unified7dFableReset"),
  }
}

type ProbeAccount = ProbeStatus["accounts"][number]

/** When an account's numbers were last learned: a response through it, or a probe it answered. */
export function checkedAt(l: UpstreamAccount | undefined, p: ProbeAccount | undefined): number | null {
  const seen = [l?.lastUsed ? Date.parse(l.lastUsed) : Number.NaN, p?.status === "ok" && p.lastProbedAt ? Date.parse(p.lastProbedAt) : Number.NaN]
  const ok = seen.filter(Number.isFinite)
  return ok.length ? Math.max(...ok) : null
}

/**
 * The route table after `s` picks an account (or `c`, pick null, clears it), or
 * null when that changes nothing. Picking the account already set is a no-op,
 * never a clear: the picker opens on it, so "s, Enter" to make sure must not
 * turn the preference off.
 */
export function fallbackAfter(table: RouteTable, pick: { account: string; mode: RouteMode } | null): RouteTable | null {
  const fallback = pick ?? { account: "", mode: "prefer" as RouteMode }
  if (!pick && !table.fallback.account) return null
  if (pick && table.fallback.account === pick.account && table.fallback.mode === pick.mode) return null
  return { ...table, fallback }
}

export type AccountsData = {
  accounts: AccountView[]
  live: UpstreamStatus | null
  probe?: ProbeStatus | null
  routes?: RouteTable
}

export type AccountsOpts = {
  width: number
  now?: number
  /** The row the `s`/`d`/`P` picker is on, if one is open. */
  selected?: number | null
}

/** The accounts block: a title line, a column header, one row per account. */
export function accountsBlock(data: AccountsData, opts: AccountsOpts): string[] {
  const now = opts.now ?? Date.now()
  const W = opts.width
  const live = data.live
  const probe = data.probe ?? null
  const th = live?.switchThreshold ?? 0.98
  const list = data.accounts
  const fb = data.routes?.fallback
  const lines: string[] = []

  const probeNote = !probe
    ? ""
    : probe.running
      ? dim("checking quota now…")
      : probe.enabled
        ? dim(`quota checked every ${formatSpan(probe.intervalSeconds * 1000)}${probe.nextRunAt ? `, next in ${formatReset(Date.parse(probe.nextRunAt), now) || "a moment"}` : ""}`)
        : yellow("quota check off — an idle account keeps old numbers · g to turn it on")
  const routeNote = fb?.account ? magenta(`◆ ${fb.mode === "pin" ? "pinned to" : "preferring"} ${fb.account}`) : ""
  const sessions = live ? dim(`${live.sessionsActive} active / ${live.sessionsKnown} known sessions`) : ""
  lines.push(` ${bold("Accounts")}  ${[dim(`switch at ${Math.round(th * 100)}%`), probeNote, sessions, routeNote].filter(Boolean).join(dim("  ·  "))}`)
  if (list.length === 0) {
    lines.push(`   ${dim("none — `a` adds one")}`)
    return lines
  }

  const rows = list.map((a) => {
    const l = live?.accounts.find((x) => x.name === a.name)
    return { a, l, b: bucketsOf(a, l), p: probe?.accounts.find((x) => x.name === a.name) }
  })
  // A model-scoped weekly column is drawn when any account has one, so every
  // row's columns line up under the same header.
  const cols: { label: string; ratio: (b: Buckets) => number | null; reset: (b: Buckets) => number | null }[] = [
    { label: "5-hour", ratio: (b) => b.ses, reset: (b) => b.sesR },
    { label: "week", ratio: (b) => b.wk, reset: (b) => b.wkR },
  ]
  if (rows.some((r) => r.b.s7 != null)) cols.push({ label: "Sonnet week", ratio: (b) => b.s7, reset: (b) => b.s7R })
  if (rows.some((r) => r.b.f7 != null)) cols.push({ label: "Fable week", ratio: (b) => b.f7, reset: (b) => b.f7R })

  // Widths: the name, then the bars share what is left — each bar carries its
  // own percent and reset time, so nothing else sits between them.
  const nameW = clamp(Math.max(...list.map((a) => a.name.length)), 12, W >= 140 ? 32 : 24)
  const STATUS_W = 9
  const SESS_W = 6
  const leadW = 4 + nameW + 1 + STATUS_W + 1 + SESS_W + 1
  const bw = clamp(Math.floor((W - leadW - 12) / cols.length) - 2, 10, 24)
  const lead = (mark: string, name: string, status: string, sess: string) => ` ${mark} ${rpad(name, nameW)} ${rpad(status, STATUS_W)} ${rpad(sess, SESS_W)} `
  // The bucket's name over the percent, "resets in" over the time, where both fit.
  const colHead = (label: string) => (label.length + 1 + "resets in".length <= bw ? ` ${label}`.padEnd(bw - "resets in".length - 1) + "resets in " : ` ${label}`.padEnd(bw))

  // Stale: two probe intervals and a minute with no news, or half an hour when nothing probes.
  const staleMs = probe?.enabled ? 2 * probe.intervalSeconds * 1000 + 60_000 : 30 * 60_000

  lines.push(dim(lead("  ", "", "", "") + cols.map((c) => colHead(c.label)).join("  ") + "  checked"))
  rows.forEach(({ a, l, b, p }, i) => {
    const isCur = live?.currentAccount === a.name
    const isRouted = fb?.account === a.name
    const isSel = opts.selected === i
    const disabled = a.state === "disabled" || l?.disabled === true
    const mark = (isSel ? cyan(">") : " ") + (isRouted ? magenta("◆") : isCur ? green("►") : " ")

    let status: string
    const st = l?.status ?? a.state
    if (disabled) status = gray("disabled")
    // Only what is not the ordinary is said: a ready account's status is blank.
    else if (st === "active" || st === "usable") status = isCur ? green("serving") : ""
    else if (st === "throttled") status = yellow("throttled")
    else if (st === "exhausted" || st === "error" || st === "stale") status = red(st)
    else status = st
    const sess = l?.sessions ? dim(`${l.sessions} sess`) : ""

    const cells = cols.map((c) => meter(c.ratio(b), bw, th, { reset: formatReset(c.reset(b), now), muted: disabled }))

    let checked = ""
    if (!disabled) {
      const at = checkedAt(l, p)
      const ago = at == null ? "never" : formatAgo(now - at)
      checked = at != null && now - at <= staleMs ? dim(ago) : yellow(ago)
      if (p?.status === "error" || p?.status === "timeout") checked += ` ${red(`check failed${p.error?.match(/HTTP \d+/) ? ` (${p.error.match(/HTTP (\d+)/)?.[1]})` : ""}`)}`
    }

    const name = isSel ? bold(clip(a.name, nameW)) : disabled ? gray(clip(a.name, nameW)) : clip(a.name, nameW)
    let line = lead(mark, name, status, sess) + cells.join("  ") + `  ${checked}`
    const blocked: string[] = []
    if (!disabled && (b.s7 ?? 0) >= th) blocked.push("Sonnet")
    if (!disabled && (b.f7 ?? 0) >= th) blocked.push("Fable")
    if (blocked.length) line += `  ${red(`⊘ ${blocked.join(" ")}`)}`
    if (l?.pausedUntil) line += `  ${yellow("paused")}`
    if (l?.rateLimitedUntil) line += `  ${yellow(`held ${formatReset(new Date(l.rateLimitedUntil).getTime(), now)}`)}`
    if (a.priority) line += `  ${dim(`pri ${a.priority}`)}`
    lines.push(line)
  })
  return lines
}

export type ActiveRow = { id: string; ts: string; path: string; model: string | null; who: string; account: string | null }

/** Requests in flight, longest-running first: how long, what, for whom. */
export function activeBlock(active: ActiveRow[], inFlight: number, opts: { now?: number; max?: number } = {}): string[] {
  const now = opts.now ?? Date.now()
  const max = opts.max ?? 5
  const lines = [` ${bold("Active")} ${dim(`(${inFlight})`)}${active.length === 0 && inFlight > 0 ? `  ${dim(`details need the new daemon — ${gw("restart")}`)}` : ""}`]
  const sorted = [...active].sort((x, y) => x.ts.localeCompare(y.ts))
  for (const r of sorted.slice(0, max))
    lines.push(`   ${lpad(formatSpan(now - Date.parse(r.ts)), 6)}  ${clip(r.model ?? r.path, 26)}  ${clip(r.who, 24)}  ${dim(r.account ?? "")}`)
  if (sorted.length > max) lines.push(`   ${dim(`… ${sorted.length - max} more`)}`)
  return lines
}

export type TraceRowView = Record<string, unknown>

const SOURCE: Record<string, string> = { pinned: magenta("pinned"), preferred: magenta("preferred"), observed: dim("observed") }

/**
 * The last `n` finished requests, oldest at the top. A row is written when its
 * reply ends but stamped with when it began, so they are sorted by that stamp —
 * a long reply lands where it started, not where it finished.
 */
export function recentBlock(rows: TraceRowView[], n: number): string[] {
  const head =
    `   ${rpad("time", 8)}  ${rpad("st", 3)}  ${rpad("model", 22)}  ${lpad("ttfb", 6)}  ${lpad("total", 6)}  ` +
    `${lpad("in", 6)}  ${lpad("cached", 6)}  ${lpad("out", 6)}  ${rpad("who", 16)}  account`
  const lines = [` ${bold("Recent")}`, dim(head)]
  const sorted = [...rows].sort((x, y) => String(x.ts).localeCompare(String(y.ts)))
  for (const r of sorted.slice(-n)) {
    const u = r.usage as { input: number; output: number; cacheRead: number; cacheCreation: number } | null
    const idn = r.identity as { agent: string | null; session: string | null; run: string | null } | undefined
    const who = idn?.agent ?? idn?.run ?? (idn?.session ? `sess:${idn.session.slice(0, 8)}` : "anon")
    const st = Number(r.status)
    const status = st >= 500 || !st ? red(String(r.status ?? "err")) : st >= 400 ? yellow(String(st)) : green(String(st))
    const pol = (r.policy ?? []) as { rule: string; action: string }[]
    const tok = (v: number | undefined) => lpad(u && typeof v === "number" ? compact(v) : dim("-"), 6)
    const ts = Date.parse(String(r.ts))
    const source = SOURCE[String(r.accountSource ?? "")]
    lines.push(
      `   ${dim(Number.isFinite(ts) ? hhmm(ts) : "--:--:--")}  ${rpad(status, 3)}  ${clip(String(r.model ?? "-"), 22)}  ` +
        `${lpad(formatMs(r.ttfbMs as number | null), 6)}  ${lpad(formatMs(r.durMs as number | null), 6)}  ` +
        `${tok(u?.input)}  ${tok(u?.cacheRead)}  ${tok(u?.output)}  ${clip(who, 16)}  ${String(r.account ?? "-")}` +
        (source ? ` ${source}` : "") +
        (pol.length ? `  ${dim(pol.map((d) => `${d.rule}:${d.action}`).join(","))}` : "") +
        (r.error ? `  ${red(`ERR ${String(r.error)}`)}` : ""),
    )
  }
  return lines
}

type Stats = {
  requests: number
  inFlight: number
  errors: number
  refusals: number
  port: number
  active?: ActiveRow[]
  rules: { rule: string; level: string }[]
}
type Accounts = { accounts: AccountView[]; observedAt: number | null; live: UpstreamStatus | null; routes?: RouteTable; probe?: ProbeStatus }
type Row = Record<string, unknown>

type Mode = "normal" | "select" | "settings" | "input" | "confirm"
type SelectAction = "switch" | "toggle" | "priority" | "remove"

export async function runTui(cfg: Config): Promise<number> {
  const base = `http://${cfg.host}:${cfg.port}`
  const out = process.stdout
  const inp = process.stdin
  if (!out.isTTY || !inp.isTTY) {
    process.stderr.write(`tui needs a terminal — \`${gw("status")}\` prints the same without one\n`)
    return 1
  }

  const get = async <T>(url: string): Promise<T | null> => {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(2500) })
      return (await r.json()) as T
    } catch {
      return null
    }
  }
  const send = async (url: string, method: string, body?: unknown): Promise<Record<string, unknown> & { _status: number }> => {
    try {
      const r = await fetch(url, {
        method,
        headers: body === undefined ? {} : { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(15_000),
      })
      const text = await r.text()
      let parsed: Record<string, unknown> = {}
      try {
        parsed = JSON.parse(text) as Record<string, unknown>
      } catch {
        parsed = text ? { error: text.slice(0, 120) } : {}
      }
      return { ...parsed, _status: r.status }
    } catch (err) {
      return { error: (err as Error).message, _status: 0 }
    }
  }

  // ---- state
  let stats: Stats | null = null
  let accounts: Accounts | null = null
  let rows: Row[] = []
  let mode: Mode = "normal"
  let selAction: SelectAction = "switch"
  let selIdx = 0
  let selMode: RouteMode = "prefer"
  let setIdx = 0
  let inputBuf = ""
  let inputFor: { label: string; apply: (v: string) => Promise<void> } | null = null
  /** A question that must be answered `y` before anything happens. */
  let confirmFor: { prompt: string; yes: () => Promise<void> } | null = null
  let busy: string | null = null
  const log: string[] = []
  const note = (s: string) => {
    log.push(`${hhmm()} ${s}`)
    if (log.length > 6) log.shift()
  }
  let running = true

  const refresh = async () => {
    const [s, a, t] = await Promise.all([
      get<Stats>(`${base}/_gateway/status`),
      get<Accounts>(`${base}/_gateway/accounts`),
      get<{ rows: Row[] }>(`${base}/_gateway/trace?n=40`),
    ])
    if (s) stats = s
    if (a) accounts = a
    if (t) rows = t.rows
    if (!s && !a) note(red(`gateway not answering on :${cfg.port}`))
  }

  // ---- actions: one per key, each one endpoint
  const reload = async () => {
    const r = await send(`${base}/_gateway/reload`, "POST")
    note(r.error ? red(`reload: ${String(r.error)}`) : `gateway reloaded${r.added ? ` (+${String(r.added)})` : ""}`)
    await refresh()
  }

  const patchAccount = async (name: string, patch: { disabled?: boolean; priority?: number }, what: string) => {
    const r = await send(`${base}/_gateway/accounts/${encodeURIComponent(name)}`, "PATCH", patch)
    note(r.error ? red(String(r.error)) : `${name}: ${what}`)
    await refresh()
  }

  /**
   * "Serve from X" = the route table's fallback, handed to the engine as a pin
   * or a preference for every request no rule claims; `pick` null clears it —
   * back to rotation. Choosing what is already set writes nothing.
   */
  const switchTo = async (pick: { account: string; mode: RouteMode } | null) => {
    const table = accounts?.routes ?? { fallback: { account: "", mode: "prefer" }, rules: [] }
    const next = fallbackAfter(table, pick)
    if (!next) {
      note(pick ? `already serving from "${pick.account}" — c clears it` : "no route set — rotation decides")
      return
    }
    const r = await send(`${base}/_gateway/routes`, "PUT", next)
    note(r.error ? red(String(r.error)) : !pick ? "route cleared — rotation decides" : `serving from "${pick.account}" (${pick.mode === "pin" ? "pinned — that account or an error" : "preferred — rotation takes over when it is spent"})`)
    await refresh()
  }

  /** `p`: one run of the engine's prober — the usage API for every enabled OAuth account. */
  const probe = async () => {
    busy = "checking quota…"
    render()
    const r = await send(`${base}/_gateway/probe`, "POST")
    const p = r.probe as { accounts?: { name: string; status: string; error: string | null }[] } | undefined
    const bad = (p?.accounts ?? []).filter((a) => a.error)
    note(r.error ? red(String(r.error)) : bad.length ? yellow(`quota check failed — ${bad.map((b) => `${b.name}: ${b.error}`).join("; ")}`) : "quota checked — every bar is fresh")
    busy = null
    await refresh()
  }

  /** `x`: the account leaves the pool — its row and login, and any route that named it. */
  const removeAccount = async (name: string) => {
    const r = await send(`${base}/_gateway/accounts/${encodeURIComponent(name)}`, "DELETE")
    const cleared = Array.isArray(r.routesCleared) ? (r.routesCleared as string[]) : []
    note(r.error ? red(String(r.error)) : `removed "${name}"${cleared.length ? ` — back to rotation: ${cleared.join(", ")}` : ""}`)
    await refresh()
    selIdx = Math.min(selIdx, Math.max(0, (accounts?.accounts.length ?? 1) - 1))
  }

  const addAccount = async () => {
    await suspended(() => {
      const r = spawnSync(
        process.execPath,
        ["--experimental-strip-types", "--no-warnings", fileURLToPath(new URL("./cli.ts", import.meta.url)), "account", "login", "--no-reload"],
        { stdio: "inherit", env: { ...process.env, [envName("GATEWAY_ACCOUNTS_FILE")]: cfg.accountsFile } },
      )
      return r.status ?? 1
    })
    await reload()
    mode = "normal"
  }

  // ---- settings rows (BIOS-style)
  type Setting = { label: string; value: () => string; hint: string; left?: () => Promise<void>; right?: () => Promise<void>; enter?: () => Promise<void> }
  const threshold = () => accounts?.live?.switchThreshold ?? pool.readPool(cfg.accountsFile).switchThreshold ?? 0.98
  // The prober's own interval: the file's value, or the default when the file has none.
  const probeSeconds = () => accounts?.probe?.intervalSeconds ?? (pool.readPool(cfg.accountsFile).quotaProbeSeconds as number | undefined) ?? 0
  const putSetting = async (fields: { switchThreshold?: number; holdSeconds?: number; distributeSessions?: boolean; quotaProbeSeconds?: number }) => {
    const r = await send(`${base}/_gateway/settings`, "PUT", fields)
    const later = Array.isArray(r.appliesAtRestart) && r.appliesAtRestart.length ? ` (${(r.appliesAtRestart as string[]).join(", ")} at restart)` : ""
    note(r.error ? red(String(r.error)) : `saved${later}`)
    await refresh()
  }
  const askNumber = (label: string, apply: (n: number) => Promise<void>, check: (n: number) => boolean, back: Mode = "settings") => {
    inputFor = {
      label,
      apply: async (v) => {
        const n = Number(v)
        if (Number.isFinite(n) && check(n)) await apply(n)
        else note(red(`${label}: not a valid value`))
        mode = back
      },
    }
    inputBuf = ""
    mode = "input"
  }
  const settings: Setting[] = [
    {
      label: "Switch threshold",
      value: () => `${Math.round(threshold() * 100)}%`,
      hint: "←→ ±1%  Enter to type",
      left: () => putSetting({ switchThreshold: Math.max(0.01, +(threshold() - 0.01).toFixed(2)) }),
      right: () => putSetting({ switchThreshold: Math.min(1, +(threshold() + 0.01).toFixed(2)) }),
      enter: async () => askNumber("Switch threshold (1–100)", (n) => putSetting({ switchThreshold: n / 100 }), (n) => Number.isInteger(n) && n >= 1 && n <= 100),
    },
    {
      label: "Quota check",
      value: () => (probeSeconds() > 0 ? `every ${formatSpan(probeSeconds() * 1000)}` : "off"),
      hint: "←→ ±30s (0 = off)  Enter to type — the usage API, spends nothing; keeps idle accounts' bars true",
      left: () => putSetting({ quotaProbeSeconds: Math.max(0, probeSeconds() - 30) }),
      right: () => putSetting({ quotaProbeSeconds: probeSeconds() === 0 ? 300 : probeSeconds() + 30 }),
      enter: async () => askNumber("Check interval in seconds (0 = off, 30 to 604800)", (n) => putSetting({ quotaProbeSeconds: n }), (n) => n === 0 || (n >= 30 && n <= 604_800)),
    },
    {
      label: "Hold when exhausted",
      value: () => `${(pool.readPool(cfg.accountsFile).holdSeconds as number | undefined) ?? cfg.holdSeconds}s`,
      hint: "Enter to set — 0 = immediate 429; applies at restart",
      enter: async () => askNumber("Hold seconds", (n) => putSetting({ holdSeconds: n }), (n) => Number.isInteger(n) && n >= 0),
    },
    {
      label: "Distribute sessions",
      value: () => (accounts?.live?.distributeSessions ? "on" : "off"),
      hint: "←→ toggle — spread concurrent sessions across equal-priority accounts; applies at restart",
      left: () => putSetting({ distributeSessions: !accounts?.live?.distributeSessions }),
      right: () => putSetting({ distributeSessions: !accounts?.live?.distributeSessions }),
    },
    { label: "Add account", value: () => "browser login", hint: "Enter to open", enter: addAccount },
    { label: "Accounts file", value: () => cfg.accountsFile, hint: `\`${gw("account")}\` edits it from a shell` },
  ]

  /** Leave raw mode, run something that owns the terminal, come back. */
  const suspended = async (fn: () => number) => {
    inp.setRawMode(false)
    inp.pause()
    out.write(`${ESC}?25h${ESC}2J${ESC}H`)
    fn()
    out.write(`${ESC}2J${ESC}H`)
    inp.setRawMode(true)
    inp.resume()
  }

  // ---- render
  const paint = (lines: string[]) => {
    const H = out.rows || 30
    out.write(`${ESC}H${lines.slice(0, H).join(`${ESC}K\n`)}${ESC}K${ESC}J`)
  }
  const render = () => {
    if (!running) return
    const W = out.columns || 100
    const H = out.rows || 30
    const lines: string[] = []
    const push = (s: string) => lines.push(fit(s, W))

    const left = ` ${bold(gw())}  ${dim(`:${cfg.port} → ${cfg.upstreamUrl}`)}${busy ? `  ${yellow(busy)}` : ""}`
    const right = stats ? `${stats.requests} req · ${stats.inFlight} in flight · ${stats.errors} err · ${stats.refusals} refused  ${hhmm()} ` : ""
    push(left + " ".repeat(Math.max(1, W - vw(left) - vw(right))) + right)
    push(` ${dim("─".repeat(W - 2))}`)

    if (mode === "settings" || mode === "input") {
      push(` ${bold("Settings")}  ${dim("kept in the accounts file, applied by the engine")}`)
      push("")
      settings.forEach((s, i) => {
        const cur = i === setIdx
        const label = rpad(s.label, 22)
        const value = rpad(s.value(), 44)
        push(`  ${cur ? rev(` ${label} ${value} `) : ` ${label} ${value} `}  ${cur ? dim(s.hint) : ""}`)
      })
      if (mode === "input" && inputFor) {
        push("")
        push(`  ${inputFor.label}: ${inputBuf}${rev(" ")}`)
      }
      while (lines.length < H - 2) lines.push("")
      push(` ${dim("─".repeat(W - 2))}`)
      push(`  ${mode === "input" ? "Enter apply  Esc cancel" : "↑↓ move  ←→ adjust  Enter open  Esc/q back"}`)
      return paint(lines)
    }

    // -- accounts
    const view: AccountsData = { accounts: accounts?.accounts ?? [], live: accounts?.live ?? null, probe: accounts?.probe ?? null, routes: accounts?.routes }
    for (const l of accountsBlock(view, { width: W, selected: mode === "select" || mode === "confirm" ? selIdx : null })) push(l)
    if (mode === "select") {
      const what =
        selAction === "switch"
          ? `Enter: ${selMode === "pin" ? "pin to" : "prefer"} the account · ←→ prefer/pin · c: clear, back to rotation`
          : selAction === "toggle"
            ? "Enter: enable / disable"
            : selAction === "remove"
              ? "Enter: remove the account (asks first)"
              : "Enter: set priority"
      push(`   ${dim(`↑↓ pick · ${what} · Esc`)}`)
    }
    if (mode === "confirm" && confirmFor) push(`   ${red(bold(confirmFor.prompt))}  ${dim("y yes · any other key keeps it")}`)

    // -- active
    push("")
    for (const l of activeBlock(stats?.active ?? [], stats?.inFlight ?? 0)) push(l)

    // -- recent
    push("")
    const budgetRows = Math.max(3, H - lines.length - 5 - Math.min(log.length, 3))
    for (const l of recentBlock(rows, budgetRows)) push(l)
    for (const l of log.slice(-3)) push(`   ${l}`)

    while (lines.length < H - 2) lines.push("")
    push(` ${dim("─".repeat(W - 2))}`)
    push(`  ${bold("s")} switch  ${bold("d")} enable/disable  ${bold("P")} priority  ${bold("p")} check quota now  ${bold("a")} add  ${bold("x")} remove  ${bold("R")} reload  ${bold("g")} settings  ${bold("q")} quit`)
    paint(lines)
  }
  const safeRender = () => {
    try {
      render()
    } catch (err) {
      note(red(`render: ${(err as Error).message}`))
      mode = "normal"
    }
  }

  // ---- keys
  const names = () => (accounts?.accounts ?? []).map((a) => a.name)
  const acct = () => (accounts?.accounts ?? [])[selIdx]
  const key = async (k: string) => {
    if (mode === "input") {
      if (k === "esc") {
        mode = "settings"
        return
      }
      if (k === "enter") {
        const f = inputFor
        if (f) await f.apply(inputBuf)
        else mode = "settings"
        return
      }
      if (k === "bs") inputBuf = inputBuf.slice(0, -1)
      else if (k.length === 1 && k >= " ") inputBuf += k
      return
    }
    if (mode === "confirm") {
      const c = confirmFor
      mode = "normal"
      confirmFor = null
      if (c && (k === "y" || k === "Y")) await c.yes()
      else note("kept — nothing removed")
      return
    }
    if (mode === "settings") {
      if (k === "esc" || k === "q") mode = "normal"
      else if (k === "up") setIdx = Math.max(0, setIdx - 1)
      else if (k === "down") setIdx = Math.min(settings.length - 1, setIdx + 1)
      else if (k === "left") await settings[setIdx].left?.()
      else if (k === "right") await settings[setIdx].right?.()
      else if (k === "enter") await settings[setIdx].enter?.()
      return
    }
    if (mode === "select") {
      const n = (accounts?.accounts ?? []).length
      if (k === "esc" || k === "q") mode = "normal"
      else if (k === "up") selIdx = (selIdx - 1 + n) % n
      else if (k === "down") selIdx = (selIdx + 1) % n
      else if ((k === "left" || k === "right") && selAction === "switch") selMode = selMode === "pin" ? "prefer" : "pin"
      else if (k === "c" && selAction === "switch") {
        mode = "normal"
        await switchTo(null)
      } else if (k === "enter") {
        const a = acct()
        if (!a) return
        if (selAction === "switch") {
          mode = "normal"
          await switchTo({ account: a.name, mode: selMode })
        } else if (selAction === "toggle") {
          mode = "normal"
          await patchAccount(a.name, { disabled: a.state !== "disabled" }, a.state !== "disabled" ? "disabled" : "enabled")
        } else if (selAction === "remove") {
          const serving = accounts?.live?.currentAccount === a.name ? " It is serving now — rotation moves on." : ""
          confirmFor = { prompt: `Remove "${a.name}"? Its login leaves the gateway; getting it back takes a new login (a).${serving}`, yes: () => removeAccount(a.name) }
          mode = "confirm"
        } else {
          askNumber(`Priority for ${a.name} (lower = preferred)`, (p) => patchAccount(a.name, { priority: p }, `priority ${p}`), Number.isInteger, "normal")
        }
      }
      return
    }
    // normal
    const n = (accounts?.accounts ?? []).length
    if (k === "q" || k === "Q" || k === "ctrl-c") running = false
    else if (k === "g") {
      mode = "settings"
      setIdx = 0
    } else if (k === "R") await reload()
    else if (k === "p") await probe()
    else if (k === "a") await addAccount()
    else if (n > 0 && (k === "s" || k === "d" || k === "P" || k === "x")) {
      mode = "select"
      selAction = k === "s" ? "switch" : k === "d" ? "toggle" : k === "x" ? "remove" : "priority"
      selMode = accounts?.routes?.fallback.mode ?? "prefer"
      const start = accounts?.routes?.fallback.account || accounts?.live?.currentAccount || ""
      selIdx = Math.max(0, names().indexOf(start))
      if (k === "x") {
        // Removal starts where it is likeliest wanted — a switched-off account —
        // and never on the one serving.
        const list = accounts?.accounts ?? []
        const off = list.findIndex((a) => a.state === "disabled")
        const idle = list.findIndex((a) => a.name !== accounts?.live?.currentAccount)
        selIdx = off >= 0 ? off : Math.max(0, idle)
      }
    }
  }

  const decode = (d: string): string => {
    if (d === `${ESC}A`) return "up"
    if (d === `${ESC}B`) return "down"
    if (d === `${ESC}C`) return "right"
    if (d === `${ESC}D`) return "left"
    if (d === "\x1b") return "esc"
    if (d === "\r" || d === "\n") return "enter"
    if (d === "\x03") return "ctrl-c"
    if (d === "\x7f" || d === "\x08") return "bs"
    return d
  }

  // ---- loop
  inp.setRawMode(true)
  inp.resume()
  inp.setEncoding("utf8")
  out.write(`${ESC}?25l${ESC}2J${ESC}H`)
  const restore = () => {
    out.write(`${ESC}?25h${ESC}2J${ESC}H`)
    try {
      inp.setRawMode(false)
    } catch {
      // Already restored by a suspended child.
    }
    inp.pause()
  }
  out.on("resize", () => safeRender())

  await refresh()
  safeRender()
  const timer = setInterval(() => void refresh().then(safeRender), 2000)

  await new Promise<void>((resolve) => {
    inp.on("data", (d: string) => {
      void key(decode(d))
        .catch((err: Error) => note(red(`error: ${err.message}`)))
        .then(() => {
          if (!running) resolve()
          else safeRender()
        })
    })
  })
  clearInterval(timer)
  restore()
  return 0
}
