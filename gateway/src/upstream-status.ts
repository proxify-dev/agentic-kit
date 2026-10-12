/**
 * The engine's status, in the one shape every face reads — the app pane, the
 * TUI and the CLI. The engine's getStatus() is teamclaude's status object (the
 * engine is teamclaude's, vendored), so this is a normalizer, not a model.
 */

export type UpstreamAccount = {
  name: string
  type: string
  priority: number
  status: string
  sessions: number
  disabled: boolean
  rateLimitedUntil: string | null
  pausedUntil: string | null
  totalRequests: number
  lastUsed: string | null
  /** The engine's own quota view (unified5h, unified7d, …) — what the bars draw. */
  quota: Record<string, unknown>
}

export type UpstreamStatus = {
  currentAccount: string | null
  switchThreshold: number | null
  sessionsActive: number
  sessionsKnown: number
  distributeSessions: boolean
  accounts: UpstreamAccount[]
  at: number
}

type RawStatus = {
  currentAccount?: string
  switchThreshold?: number
  sessions?: { active?: number; known?: number; distribute?: boolean }
  accounts?: {
    name?: string
    type?: string
    priority?: number
    status?: string
    sessions?: number
    disabled?: boolean
    rateLimitedUntil?: string | null
    pausedUntil?: string | null
    usage?: { totalRequests?: number; lastUsed?: string | number | null }
    quota?: Record<string, unknown>
  }[]
}

export function normalizeStatus(d: RawStatus, at = Date.now()): UpstreamStatus {
  return {
    currentAccount: typeof d.currentAccount === "string" ? d.currentAccount : null,
    switchThreshold: typeof d.switchThreshold === "number" ? d.switchThreshold : null,
    sessionsActive: d.sessions?.active ?? 0,
    sessionsKnown: d.sessions?.known ?? 0,
    distributeSessions: d.sessions?.distribute === true,
    accounts: (d.accounts ?? []).map((a) => ({
      name: a.name ?? "(unnamed)",
      type: a.type ?? "oauth",
      priority: typeof a.priority === "number" ? a.priority : 0,
      status: a.status ?? "unknown",
      sessions: a.sessions ?? 0,
      disabled: a.disabled === true,
      rateLimitedUntil: a.rateLimitedUntil ?? null,
      pausedUntil: a.pausedUntil ?? null,
      totalRequests: a.usage?.totalRequests ?? 0,
      lastUsed: a.usage?.lastUsed == null ? null : String(a.usage.lastUsed),
      quota: a.quota ?? {},
    })),
    at,
  }
}
