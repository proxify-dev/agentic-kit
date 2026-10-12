/**
 * Account quota, read from the engine's observed state.
 *
 * The engine learns quota from `anthropic-ratelimit-unified-*` response headers
 * and persists it beside the accounts file (`<name>.state.json`). Reading that
 * file is how the pane shows real bars without a probe request of its own — and
 * without a second quota model that could disagree with the one actually making
 * rotation decisions.
 *
 * READ-ONLY. The file belongs to the engine serving live traffic; a stale read
 * is a cosmetic problem, a write would be a real one.
 */
import { existsSync, readFileSync, statSync } from "node:fs"

export type Quota = {
  /** 0–1 of the 5-hour session bucket. Null when never observed. */
  session: number | null
  /** 0–1 of the 7-day bucket — the one that governs rotation. */
  week: number | null
  weekSonnet: number | null
  weekFable: number | null
  /** Epoch ms, or null. */
  sessionReset: number | null
  weekReset: number | null
  status: string | null
}

export type AccountView = {
  name: string
  org: string | null
  accountUuid: string | null
  state: "usable" | "stale" | "disabled"
  priority: number
  expiresAt: number | null
  quota: Quota
}

const num = (v: unknown): number | null => (typeof v === "number" ? v : null)

/** The engine writes state beside the accounts file, same basename + `.state`. */
export const statePathFor = (configPath: string) => configPath.replace(/\.json$/, ".state.json")

type RawQuota = Record<string, unknown>

function readState(path: string): Map<string, RawQuota> {
  const out = new Map<string, RawQuota>()
  if (!existsSync(path)) return out
  try {
    const doc = JSON.parse(readFileSync(path, "utf8")) as {
      quota?: { accountUuid?: string; name?: string; quota?: RawQuota }[]
    }
    for (const row of doc.quota ?? []) {
      const key = row.accountUuid ?? row.name
      if (key && row.quota) out.set(key, row.quota)
    }
  } catch {
    // The engine rewrites this file live; a torn read is normal and transient.
  }
  return out
}

/**
 * Join the account pool with observed quota.
 * `mtime` is returned so a caller can tell a fresh view from a frozen one —
 * a quota panel that silently shows hour-old numbers is worse than one that
 * admits its age.
 */
export function accountViews(configPath: string): { accounts: AccountView[]; observedAt: number | null } {
  if (!existsSync(configPath)) return { accounts: [], observedAt: null }
  let doc: { accounts?: Record<string, unknown>[] }
  try {
    doc = JSON.parse(readFileSync(configPath, "utf8")) as { accounts?: Record<string, unknown>[] }
  } catch {
    return { accounts: [], observedAt: null }
  }
  const statePath = statePathFor(configPath)
  const state = readState(statePath)
  const observedAt = existsSync(statePath) ? statSync(statePath).mtimeMs : null
  const now = Date.now()

  const accounts = (doc.accounts ?? []).map((a): AccountView => {
    const uuid = typeof a.accountUuid === "string" ? a.accountUuid : null
    const name = typeof a.name === "string" ? a.name : "(unnamed)"
    // Keyed by uuid, with the name as a fallback for a pre-uuid state file.
    // `uuid && state.get(uuid)` would evaluate to "" for a null uuid, which
    // `??` does not treat as absent — hence the explicit ternary.
    const q: RawQuota = (uuid ? state.get(uuid) : undefined) ?? state.get(name) ?? {}
    const expiresAt = typeof a.expiresAt === "number" ? a.expiresAt : null
    return {
      name,
      org: typeof a.orgName === "string" ? a.orgName : null,
      accountUuid: uuid,
      state:
        a.disabled === true
          ? "disabled"
          : expiresAt !== null && expiresAt <= now + 60_000
            ? "stale"
            : "usable",
      priority: typeof a.priority === "number" ? a.priority : 0,
      expiresAt,
      quota: {
        session: num(q.unified5h),
        week: num(q.unified7d),
        weekSonnet: num(q.unified7dSonnet),
        weekFable: num(q.unified7dFable),
        sessionReset: num(q.unified5hReset),
        weekReset: num(q.unified7dReset),
        status: typeof q.unifiedStatus === "string" ? q.unifiedStatus : null,
      },
    }
  })
  return { accounts, observedAt }
}
