/**
 * Which Claude Code sessions go through the gateway, and the agents inside each
 * — the main thread, its subagents, their own subagents — with model, tokens and
 * state. What `GET /_gateway/sessions` answers and the TUI's sessions views (tui.ts) show.
 *
 * Built from the wire alone. Every trace row names its session; from Claude Code
 * 2.1.304 a subagent's row also names the subagent (`hints.agentId`) and the agent
 * that started it (`hints.parentAgentId`), so the tree is exact. Rows written
 * before the gateway kept those two cannot tell two subagents of one type apart:
 * they fold into one node per type, `merged: true`.
 *
 * State comes from the newest turn's stop reason: `tool_use` means the agent is
 * between calls running tools, `end_turn` means the main thread waits for the
 * person or a subagent is done. Titles and task descriptions come from Claude
 * Code's own files (transcripts.ts), never from a message.
 *
 * Held in memory for WINDOW_HOURS and rebuilt from the trace's day files on the
 * first ask after a start, so a restart or a hand-over loses nothing the record has.
 */
import { readFileSync, readdirSync } from "node:fs"
import { join } from "node:path"
import type { Hints, Identity } from "./identity.ts"
import { isModelCall, isModelPath } from "./trace.ts"
import type { TraceRow, Usage } from "./trace.ts"
import type { SubagentMeta, Transcripts } from "./transcripts.ts"

export const WINDOW_HOURS = 24
/** A turn that stopped on `tool_use` this recently: its tools are still running. */
const TOOLS_MS = 15 * 60_000
/** A main thread that ended its turn this recently waits for the person; after, it is idle. */
const WAITING_MS = 60 * 60_000
const MAIN = "main"

export type AgentState = "calling" | "tools" | "waiting" | "done" | "idle"
export type SessionState = "working" | "waiting" | "idle"

type Agent = {
  id: string
  parent: string | null
  kind: "main" | "subagent" | "workflow"
  type: string | null
  merged: boolean
  /** Turns per model. */
  models: Map<string, number>
  model: string | null
  /** Its own turns: main, subagent or workflow calls. */
  calls: number
  /** Calls made for it on the side: titles, classifiers, compaction. */
  sideCalls: number
  tokens: Usage
  /** The newest turn's input plus cache: how full its context window is. */
  context: number
  lastStop: string | null
  lastTools: string[]
  errors: number
  first: number
  last: number
  /** When the newest turn began — the fields above follow it, whatever order rows arrive in. */
  turnAt: number
  open: Set<string>
  /** Its meta file named its type: a user-defined agent reaches the wire as `custom`, its file says which. */
  named: boolean
}

type Session = {
  id: string
  project: string | null
  origin: string
  first: number
  last: number
  accounts: Map<string, number>
  agents: Map<string, Agent>
}

const zero = (): Usage => ({ input: 0, output: 0, cacheRead: 0, cacheCreation: 0 })
const iso = (ms: number) => new Date(ms).toISOString()
const isTurn = (h: Hints | null | undefined) => {
  const c = h?.requestClass ?? null
  return c === null || c === "main" || c === "subagent" || c === "workflow"
}

/** Which agent a call belongs to, and where that agent hangs in the tree. */
function placeOf(h: Hints | null | undefined): Pick<Agent, "id" | "parent" | "kind" | "type" | "merged"> {
  const cls = h?.requestClass ?? null
  const kind = cls === "workflow" ? "workflow" : "subagent"
  if (h?.agentId) return { id: h.agentId, parent: h.parentAgentId ?? MAIN, kind, type: h.agentType, merged: false }
  if (cls === "subagent" || cls === "workflow")
    return { id: `~${cls}/${h?.agentType ?? "unknown"}`, parent: MAIN, kind, type: h?.agentType ?? null, merged: true }
  return { id: MAIN, parent: null, kind: "main", type: null, merged: false }
}

/**
 * From the wire; `meta` is asked only when the wire says tools are running —
 * a subagent stopped from outside never makes the call that would say it is done.
 */
function stateOf(a: Agent, now: number, meta: () => SubagentMeta | null): AgentState {
  if (a.open.size > 0) return "calling"
  const quiet = now - a.last
  if (a.lastStop === "tool_use" && quiet < TOOLS_MS) return meta()?.finished ? "done" : "tools"
  if (a.kind === "main") return a.calls > 0 && quiet < WAITING_MS ? "waiting" : "idle"
  return a.lastStop === "end_turn" ? "done" : "idle"
}

const RANK: Record<SessionState, number> = { working: 0, waiting: 1, idle: 2 }

/** What `GET /_gateway/sessions` and `GET /_gateway/sessions/<id>` answer — the TUI's sessions views read these. */
export type SessionsList = ReturnType<Sessions["list"]>
export type SessionSummary = SessionsList["sessions"][number]
export type SessionDetail = NonNullable<ReturnType<Sessions["detail"]>>
export type AgentView = SessionDetail["agents"][number]

export class Sessions {
  #traceDir: string
  #transcripts: Transcripts
  #sessions = new Map<string, Session>()
  /** In-flight request → where it counts. */
  #open = new Map<string, { session: string; agent: string }>()
  #seeded = false
  /** Rows folded live before the seed read the day files that also hold them. */
  #early = new Set<string>()

  constructor(traceDir: string, transcripts: Transcripts) {
    this.#traceDir = traceDir
    this.#transcripts = transcripts
  }

  #session(identity: Identity, at: number): Session | null {
    const id = identity.session
    if (!id) return null
    let s = this.#sessions.get(id)
    if (!s) {
      s = { id, project: null, origin: identity.origin, first: at, last: at, accounts: new Map(), agents: new Map() }
      this.#sessions.set(id, s)
    }
    s.project = identity.project ?? s.project
    s.first = Math.min(s.first, at)
    s.last = Math.max(s.last, at)
    return s
  }

  #agent(s: Session, h: Hints | null | undefined, at: number): Agent {
    const place = placeOf(h)
    let a = s.agents.get(place.id)
    if (!a) {
      a = {
        ...place, models: new Map(), model: null, calls: 0, sideCalls: 0, tokens: zero(), context: 0,
        lastStop: null, lastTools: [], errors: 0, first: at, last: at, turnAt: 0, open: new Set(), named: false,
      }
      s.agents.set(place.id, a)
    }
    if (place.type && !a.type) a.type = place.type
    a.first = Math.min(a.first, at)
    a.last = Math.max(a.last, at)
    return a
  }

  /** A model call has started. A side call (a title, a classifier) does not make its agent busy. */
  begin(reqId: string, path: string, identity: Identity, hints: Hints | null, now = Date.now()): void {
    if (!isModelPath(path) || !isTurn(hints)) return
    const s = this.#session(identity, now)
    if (!s) return
    const a = this.#agent(s, hints, now)
    a.open.add(reqId)
    this.#open.set(reqId, { session: s.id, agent: a.id })
  }

  /** It has ended, its row written or not. */
  end(reqId: string): void {
    const at = this.#open.get(reqId)
    if (!at) return
    this.#open.delete(reqId)
    this.#sessions.get(at.session)?.agents.get(at.agent)?.open.delete(reqId)
  }

  /** A finished call's trace row. */
  record(row: TraceRow): void {
    if (!this.#seeded) this.#early.add(row.id)
    this.#fold(row)
  }

  #fold(row: TraceRow): void {
    if (row.dryRun || !isModelCall(row)) return
    const start = Date.parse(row.ts)
    if (!Number.isFinite(start)) return
    const end = start + (row.durMs ?? 0)
    const s = this.#session(row.identity, start)
    if (!s) return
    s.last = Math.max(s.last, end)
    if (row.account) s.accounts.set(row.account, (s.accounts.get(row.account) ?? 0) + 1)
    const a = this.#agent(s, row.hints, start)
    a.last = Math.max(a.last, end)
    if (row.error || (row.status !== null && row.status >= 400)) a.errors += 1
    const u = row.v === 2 ? row.usage : null
    if (u) for (const k of ["input", "output", "cacheRead", "cacheCreation"] as const) a.tokens[k] += u[k] ?? 0
    if (!isTurn(row.hints)) {
      a.sideCalls += 1
      return
    }
    a.calls += 1
    if (row.model) a.models.set(row.model, (a.models.get(row.model) ?? 0) + 1)
    if (start >= a.turnAt) {
      a.turnAt = start
      a.model = row.model ?? a.model
      a.lastStop = row.stopReason ?? (row.error ? "error" : null)
      if (u) a.context = u.input + u.cacheRead + u.cacheCreation
      a.lastTools = (row.hints?.tools ?? []).map((t) => t.name).slice(0, 8)
    }
  }

  /** The day files that can hold the window, folded once. */
  #seed(now: number): void {
    if (this.#seeded) return
    this.#seeded = true
    const since = now - WINDOW_HOURS * 3_600_000
    const firstDay = iso(since).slice(0, 10)
    let files: string[] = []
    try {
      files = readdirSync(this.#traceDir).filter((f) => /^\d{4}-\d{2}-\d{2}\.ndjson$/.test(f) && f.slice(0, 10) >= firstDay)
    } catch {
      // no trace yet
    }
    for (const f of files.sort()) {
      let text = ""
      try {
        text = readFileSync(join(this.#traceDir, f), "utf8")
      } catch {
        continue
      }
      for (const line of text.split("\n")) {
        if (!line) continue
        let row: TraceRow
        try {
          row = JSON.parse(line)
        } catch {
          continue // a row still being written
        }
        if (this.#early.has(row.id) || !row.identity || Date.parse(row.ts) < since) continue
        this.#fold(row)
      }
    }
    this.#early.clear()
  }

  #prune(now: number): void {
    const since = now - WINDOW_HOURS * 3_600_000
    for (const [id, s] of this.#sessions)
      if (s.last < since && ![...s.agents.values()].some((a) => a.open.size > 0)) this.#sessions.delete(id)
  }

  /** Its type — `custom` or unknown on the wire upgraded to its meta file's name, once. */
  #typeOf(s: Session, a: Agent): string | null {
    if (!a.named && a.kind !== "main" && !a.merged && (a.type === null || a.type === "custom")) {
      const name = this.#meta(s, a)?.agentType
      if (name) [a.type, a.named] = [name, true]
    }
    return a.type
  }

  #meta(s: Session, a: Agent): SubagentMeta | null {
    return a.kind === "main" || a.merged ? null : this.#transcripts.subagent(s.id, a.id, s.project)
  }

  #summary(s: Session, now: number) {
    const main = s.agents.get(MAIN)
    const agents = [...s.agents.values()]
    const states = agents.map((a) => ({ a, state: stateOf(a, now, () => this.#meta(s, a)) }))
    const live = (st: AgentState) => st === "calling" || st === "tools"
    const subs = states.filter(({ a }) => a.kind !== "main")
    const byType: Record<string, number> = {}
    for (const { a } of subs) {
      const t = this.#typeOf(s, a) ?? a.kind
      byType[t] = (byType[t] ?? 0) + 1
    }
    const mainState = main ? states.find(({ a }) => a === main)!.state : "idle"
    const state: SessionState = states.some(({ state }) => live(state)) ? "working" : mainState === "waiting" ? "waiting" : "idle"
    const tokens = zero()
    for (const a of agents) for (const k of Object.keys(tokens) as (keyof Usage)[]) tokens[k] += a.tokens[k]
    const calls = agents.reduce((n, a) => n + a.calls, 0)
    const newest = agents.filter((a) => a.model).sort((x, y) => y.turnAt - x.turnAt)[0]
    return {
      id: s.id,
      project: s.project,
      origin: s.origin,
      state,
      first: iso(s.first),
      last: iso(s.last),
      model: main?.model ?? newest?.model ?? null,
      accounts: [...s.accounts.entries()].sort((x, y) => y[1] - x[1]).map(([name]) => name),
      agents: { total: subs.length, working: subs.filter(({ state }) => live(state)).length, byType },
      calls,
      sideCalls: agents.reduce((n, a) => n + a.sideCalls, 0),
      tokens,
      context: main?.context ?? 0,
      inFlight: agents.reduce((n, a) => n + a.open.size, 0),
      /** One or two calls, no subagent, nothing running: a `claude -p` job, hidden unless asked. */
      oneShot: calls <= 2 && subs.length === 0 && state !== "working",
    }
  }

  /** Sessions in the window, working first, at most `n`; one-shot jobs only counted unless `all`. */
  list(opts: { all?: boolean; n?: number; now?: number } = {}) {
    const now = opts.now ?? Date.now()
    this.#seed(now)
    this.#prune(now)
    const all = [...this.#sessions.values()].map((s) => this.#summary(s, now))
    const shown = (opts.all ? all : all.filter((s) => !s.oneShot))
      .sort((x, y) => RANK[x.state] - RANK[y.state] || Date.parse(y.last) - Date.parse(x.last))
      .slice(0, opts.n && opts.n > 0 ? opts.n : undefined)
    const count = (st: SessionState) => all.filter((s) => s.state === st).length
    return {
      now: iso(now),
      windowHours: WINDOW_HOURS,
      counts: {
        working: count("working"),
        waiting: count("waiting"),
        idle: count("idle"),
        oneShots: all.filter((s) => s.oneShot).length,
        sessions: all.length,
        subagentsWorking: all.reduce((n, s) => n + s.agents.working, 0),
        inFlight: this.#open.size,
      },
      // A title costs a transcript lookup: a one-shot job goes without.
      sessions: shown.map((s) => ({ ...s, title: s.oneShot ? null : this.#transcripts.title(s.id, s.project) })),
    }
  }

  /** One session and its agents, parents before children. `id` may be a unique prefix. */
  detail(id: string, now = Date.now()) {
    this.#seed(now)
    let s = this.#sessions.get(id)
    if (!s && id.length >= 4) {
      const hits = [...this.#sessions.keys()].filter((k) => k.startsWith(id))
      if (hits.length === 1) s = this.#sessions.get(hits[0])
    }
    if (!s) return null
    const session = s
    const ids = new Set(session.agents.keys())
    // A parent the wire never saw (it made no call yet) hangs its children on main.
    const parentOf = (a: Agent) => (a.parent && ids.has(a.parent) ? a.parent : a.kind === "main" ? null : MAIN)
    const depth = (a: Agent, seen = new Set<string>()): number => {
      const p = parentOf(a)
      if (!p || seen.has(a.id)) return 0
      seen.add(a.id)
      const pa = session.agents.get(p)
      return pa ? 1 + depth(pa, seen) : 1
    }
    const agents = [...session.agents.values()]
      .sort((x, y) => (x.kind === "main" ? -1 : y.kind === "main" ? 1 : x.first - y.first))
      .map((a) => {
        const meta = this.#meta(session, a)
        return {
          id: a.id,
          parent: parentOf(a),
          depth: depth(a),
          kind: a.kind,
          type: this.#typeOf(session, a),
          merged: a.merged,
          description: meta?.description ?? null,
          state: stateOf(a, now, () => meta),
          finished: meta?.finished ?? null,
          model: a.model,
          models: Object.fromEntries(a.models),
          calls: a.calls,
          sideCalls: a.sideCalls,
          tokens: a.tokens,
          context: a.context,
          lastStop: a.lastStop,
          lastTools: a.lastTools,
          errors: a.errors,
          inFlight: a.open.size,
          first: iso(a.first),
          last: iso(a.last),
        }
      })
    return { now: iso(now), session: { ...this.#summary(session, now), title: this.#transcripts.title(session.id, session.project) }, agents }
  }
}
