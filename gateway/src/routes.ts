/**
 * Account routing — which account serves whom.
 *
 * This is the feature the harness gets that a rotating proxy cannot express: a
 * proxy rotates by quota and can pin a whole LAUNCH to one account, but it does
 * not know what an agent is, so "the sweep agent runs on the work account,
 * everything else rotates freely" has nowhere to live. Here it is one rule, and
 * the engine enforces it as a pin (that account or an error) or a preference
 * (that account first, then rotation).
 *
 * The table lives in a small JSON beside the trace so the app can edit it while
 * the daemon runs — re-read on change, never cached past its mtime. A routing
 * change you have to restart a daemon to apply is a routing change nobody makes.
 */
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs"
import type { Identity } from "./identity.ts"

/** `pin` = that account or an error. `prefer` = bias, then normal rotation. */
export type RouteMode = "pin" | "prefer"

export type Route = {
  /** Display name for the row. */
  name: string
  match: { agent?: string; origin?: string; model?: string; item?: string }
  account: string
  mode: RouteMode
  enabled?: boolean
}

export type RouteTable = {
  /** Applied when no rule matches. Empty account = let rotation decide. */
  fallback: { account: string; mode: RouteMode }
  rules: Route[]
}

export const EMPTY: RouteTable = { fallback: { account: "", mode: "prefer" }, rules: [] }

function glob(pattern: string, value: string): boolean {
  const rx = new RegExp(
    `^${pattern.split("*").map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`,
    "i",
  )
  return rx.test(value)
}

export class Routes {
  #path: string
  #cache: RouteTable = EMPTY
  #mtime = -1

  constructor(path: string) {
    this.#path = path
  }

  /** Cheap on the hot path: one stat, and a parse only when the file changed. */
  read(): RouteTable {
    if (!existsSync(this.#path)) {
      this.#mtime = -1
      this.#cache = EMPTY
      return this.#cache
    }
    const m = statSync(this.#path).mtimeMs
    if (m === this.#mtime) return this.#cache
    try {
      const doc = JSON.parse(readFileSync(this.#path, "utf8")) as Partial<RouteTable>
      this.#cache = {
        fallback: {
          account: typeof doc.fallback?.account === "string" ? doc.fallback.account : "",
          mode: doc.fallback?.mode === "pin" ? "pin" : "prefer",
        },
        rules: Array.isArray(doc.rules) ? doc.rules.filter((r) => r && typeof r.account === "string") : [],
      }
      this.#mtime = m
    } catch {
      // Keep serving the last good table rather than dropping every route
      // because someone saved half a file.
    }
    return this.#cache
  }

  write(table: RouteTable): RouteTable {
    writeFileSync(this.#path, `${JSON.stringify(table, null, 2)}\n`, { mode: 0o600 })
    this.#mtime = -1
    return this.read()
  }

  /** First matching enabled rule wins; otherwise the fallback. */
  resolve(id: Identity, model: string | null): { account: string; mode: RouteMode; rule: string | null } {
    const t = this.read()
    for (const r of t.rules) {
      if (r.enabled === false) continue
      const m = r.match ?? {}
      if (m.agent && !(id.agent && glob(m.agent, id.agent))) continue
      if (m.origin && !glob(m.origin, id.origin)) continue
      if (m.item && !(id.item && glob(m.item, id.item))) continue
      if (m.model && !(model && glob(m.model, model))) continue
      return { account: r.account, mode: r.mode === "pin" ? "pin" : "prefer", rule: r.name }
    }
    return { account: t.fallback.account, mode: t.fallback.mode, rule: null }
  }
}

/**
 * A client that pinned itself through the shim (`claude --account X`) arrives
 * as `/tc-acct/X/v1/messages` — the vendored engine's pin grammar, `/tc-prefer/`
 * for a soft one. Stripped here, and the client's own pin outranks the table.
 */
export function parsePinPrefix(url: string): { url: string; account: string; mode: RouteMode } | null {
  const m = /^\/(tc-acct|tc-prefer)\/([^/]+)(\/.*)?$/.exec(url)
  if (!m) return null
  let account: string
  try {
    account = decodeURIComponent(m[2])
  } catch {
    account = m[2]
  }
  return { url: m[3] || "/", account, mode: m[1] === "tc-acct" ? "pin" : "prefer" }
}
