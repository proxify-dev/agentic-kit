/**
 * The policy plane: what the harness can do DURING a model call.
 *
 * This is the half that no amount of launch-time configuration or after-the-fact
 * transcript reading can reach. A launch flag cannot stop turn 40 of a runaway
 * loop; a transcript cannot un-send a prompt. The wire can do both.
 *
 * Rules are evaluated in order and each carries a `level`, deliberately the same
 * grammar the behavior records already use for tool calls:
 *
 *   suggest  record the decision in the trace row, change nothing
 *   block    enforce it — refuse the request, or apply the mutation
 *
 * Shipping a rule at `suggest` first and reading the trace before promoting it
 * to `block` is the whole point of having the two levels: you find out what a
 * rule WOULD have done to live traffic before it does it.
 *
 * Rules live in the gateway config today. They move to `loadout/behaviors/`
 * records with `on: PreRequest` once the app owns them — the shape here is
 * chosen to match that, not to be a second grammar.
 */
import type { Identity } from "./identity.ts"
import type { Usage } from "./trace.ts"

export type Level = "suggest" | "block"

export type Match = {
  agent?: string
  origin?: string
  model?: string
  path?: string
}

export type Rule =
  | ({ rule: string; on: "PreRequest"; kind: "deny-model"; level: Level; match?: Match } & {
      models: string[]
    })
  | { rule: string; on: "PreRequest"; kind: "require-identity"; level: Level; match?: Match }
  | ({ rule: string; on: "PreRequest"; kind: "append-system"; level: Level; match?: Match } & {
      text: string
    })
  | ({ rule: string; on: "PreRequest"; kind: "budget"; level: Level; match?: Match } & {
      /** Ceiling on total tokens for one identity key, this process lifetime. */
      maxTokens: number
      /** run | agent | item | session — what the ceiling is per. */
      per?: "run" | "agent" | "item" | "session"
    })
  | ({ rule: string; on: "PreRequest"; kind: "redact"; level: Level; match?: Match } & {
      /** JS regex sources, applied to the serialised body. */
      patterns: string[]
      replacement?: string
    })

export type Decision = { rule: string; action: string; detail?: string }

export type PreContext = {
  identity: Identity
  model: string | null
  path: string
  /** Parsed JSON request body, mutated in place by mutating rules. */
  body: Record<string, unknown> | null
}

export type PreResult = {
  decisions: Decision[]
  /** Set when a `block`-level rule refuses the request. */
  refusal: { status: number; message: string; rule: string } | null
  /** True when a rule changed the body and it must be re-serialised. */
  mutated: boolean
}

/** `*` is the only wildcard, same as teamclaude's route globs. */
function glob(pattern: string, value: string): boolean {
  const rx = new RegExp(
    `^${pattern.split("*").map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`,
    "i",
  )
  return rx.test(value)
}

function applies(m: Match | undefined, ctx: PreContext): boolean {
  if (!m) return true
  if (m.agent && !(ctx.identity.agent && glob(m.agent, ctx.identity.agent))) return false
  if (m.origin && !glob(m.origin, ctx.identity.origin)) return false
  if (m.model && !(ctx.model && glob(m.model, ctx.model))) return false
  if (m.path && !glob(m.path, ctx.path)) return false
  return true
}

/** Running token totals per identity key. In-memory: a restart is a reset. */
export class Budget {
  #spend = new Map<string, number>()

  key(id: Identity, per: string): string {
    const v =
      per === "agent" ? id.agent : per === "item" ? id.item : per === "session" ? id.session : id.run
    return `${per}:${v ?? "anonymous"}`
  }

  get(key: string): number {
    return this.#spend.get(key) ?? 0
  }

  add(key: string, tokens: number): void {
    this.#spend.set(key, this.get(key) + tokens)
  }

  /** Called once a response completes, so the next request sees the real total. */
  record(id: Identity, usage: Usage): void {
    const t = usage.input + usage.output + usage.cacheRead + usage.cacheCreation
    for (const per of ["run", "agent", "item", "session"]) this.add(this.key(id, per), t)
  }

  snapshot(): Record<string, number> {
    return Object.fromEntries(this.#spend)
  }
}

/** Append text as a new system block, AFTER every cache_control marker.
 *
 * Order matters more than it looks: Anthropic caches a PREFIX. Inserting text
 * before a cached block invalidates the cache for every turn that follows, so a
 * one-line injection would silently make each turn a full-price cache miss.
 * Appending keeps the cached prefix byte-identical.
 */
function appendSystem(body: Record<string, unknown>, text: string): boolean {
  const sys = body.system
  if (typeof sys === "string") {
    body.system = [
      { type: "text", text: sys },
      { type: "text", text },
    ]
    return true
  }
  if (Array.isArray(sys)) {
    sys.push({ type: "text", text })
    return true
  }
  if (sys === undefined) {
    body.system = [{ type: "text", text }]
    return true
  }
  return false
}

export function evaluatePre(rules: Rule[], ctx: PreContext, budget: Budget): PreResult {
  const decisions: Decision[] = []
  let mutated = false

  for (const r of rules) {
    if (r.on !== "PreRequest" || !applies(r.match, ctx)) continue

    if (r.kind === "deny-model") {
      const hit = ctx.model && r.models.some((m) => glob(m, ctx.model as string))
      if (!hit) continue
      decisions.push({ rule: r.rule, action: r.level === "block" ? "denied" : "would-deny", detail: ctx.model ?? "" })
      if (r.level === "block")
        return { decisions, refusal: { status: 403, message: `model ${ctx.model} denied by ${r.rule}`, rule: r.rule }, mutated }
      continue
    }

    if (r.kind === "require-identity") {
      if (!ctx.identity.anonymous) continue
      decisions.push({ rule: r.rule, action: r.level === "block" ? "denied" : "would-deny", detail: "no x-brain-* headers" })
      if (r.level === "block")
        return { decisions, refusal: { status: 403, message: `unlabelled request refused by ${r.rule}`, rule: r.rule }, mutated }
      continue
    }

    if (r.kind === "budget") {
      const key = budget.key(ctx.identity, r.per ?? "run")
      const spent = budget.get(key)
      if (spent < r.maxTokens) continue
      decisions.push({
        rule: r.rule,
        action: r.level === "block" ? "denied" : "would-deny",
        detail: `${key} spent ${spent} of ${r.maxTokens} tokens`,
      })
      if (r.level === "block")
        return {
          decisions,
          refusal: { status: 429, message: `budget exhausted for ${key} (${spent}/${r.maxTokens} tokens)`, rule: r.rule },
          mutated,
        }
      continue
    }

    if (r.kind === "append-system") {
      if (r.level !== "block") {
        decisions.push({ rule: r.rule, action: "would-append", detail: `${r.text.length} chars` })
        continue
      }
      if (ctx.body && appendSystem(ctx.body, r.text)) {
        mutated = true
        decisions.push({ rule: r.rule, action: "appended", detail: `${r.text.length} chars` })
      }
      continue
    }

    if (r.kind === "redact") {
      if (!ctx.body) continue
      const before = JSON.stringify(ctx.body)
      let after = before
      for (const p of r.patterns) after = after.replace(new RegExp(p, "g"), r.replacement ?? "[redacted]")
      if (after === before) continue
      decisions.push({ rule: r.rule, action: r.level === "block" ? "redacted" : "would-redact" })
      if (r.level === "block") {
        // Reassigning the object's own keys keeps the caller's reference valid.
        const next = JSON.parse(after) as Record<string, unknown>
        for (const k of Object.keys(ctx.body)) delete ctx.body[k]
        Object.assign(ctx.body, next)
        mutated = true
      }
    }
  }

  return { decisions, refusal: null, mutated }
}

/** Shape check at load time, so a typo fails on start rather than mid-traffic. */
export function validateRules(raw: unknown[]): Rule[] {
  const kinds = new Set(["deny-model", "require-identity", "append-system", "budget", "redact"])
  return raw.map((r, i) => {
    const o = r as Partial<Rule> & Record<string, unknown>
    if (!o || typeof o !== "object") throw new Error(`gateway: policy[${i}] is not an object`)
    if (typeof o.rule !== "string") throw new Error(`gateway: policy[${i}] needs a "rule" name`)
    if (o.on !== "PreRequest") throw new Error(`gateway: policy[${i}] "on" must be PreRequest (PostResponse lands with the app)`)
    if (typeof o.kind !== "string" || !kinds.has(o.kind))
      throw new Error(`gateway: policy[${i}] unknown kind ${String(o.kind)}`)
    if (o.level !== "suggest" && o.level !== "block")
      throw new Error(`gateway: policy[${i}] level must be suggest|block`)
    return o as Rule
  })
}
