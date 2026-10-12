/**
 * Types for the vendored JS the gateway calls. Only the surface `../engine.ts`
 * and the CLI touch is declared; the rest of each module stays untyped until
 * its TS-ify pass. A signature here is a promise about the JS beside it —
 * change one, change the other.
 */

/** One row of the accounts file. teamclaude's schema, kept so hand-off is a copy. */
export type AccountRecord = {
  name: string
  type: "oauth" | "apikey"
  source?: string
  accountUuid?: string | null
  orgUuid?: string | null
  orgName?: string | null
  accessToken?: string
  refreshToken?: string
  expiresAt?: number
  apiKey?: string
  priority?: number
  disabled?: boolean
  importFrom?: string
  upstream?: string
  modelMap?: Record<string, string>
}

/** The accounts file as a whole. */
export type AccountsFile = {
  accounts: AccountRecord[]
  switchThreshold?: number
  holdSeconds?: number
  distributeSessions?: boolean
  routes?: unknown[]
  stormRamp?: Record<string, unknown>
  sx?: { apiKey?: string; mode?: string }
  quotaProbeSeconds?: number
  /** Drop the per-org overage/upgrade headers from replies (engine.ts). The gateway's default is true; upstream's key, upstream's default false. */
  stripOverageHeaders?: boolean
  [k: string]: unknown
}

export type Tokens = { accessToken: string; refreshToken?: string; expiresAt?: number }

/** An account as the manager holds it in memory. */
export type ManagedAccount = {
  index: number
  name: string
  type: "oauth" | "apikey"
  accountUuid: string | null
  orgUuid: string | null
  orgName: string | null
  priority: number
  disabled: boolean
  credential: string
  refreshToken: string | null
  expiresAt: number | null
  status: "active" | "throttled" | "exhausted" | "error"
  quota: Record<string, unknown>
  usage: { totalInputTokens: number; totalOutputTokens: number; totalRequests: number; lastUsed: number | null }
  rateLimitedUntil: number | null
  pausedUntil: number | null
}

/** `/teamclaude/status` — and now `/_gateway/accounts.live` in direct mode. */
export type ManagerStatus = {
  currentAccount: string | undefined
  switchThreshold: number
  routes: unknown[]
  sessions: { known: number; active: number; perAccount: Record<number, number>; distribute: boolean }
  accounts: {
    name: string
    type: string
    orgName: string | null
    priority: number
    disabled: boolean
    status: string
    sessions: number
    quota: Record<string, unknown>
    usage: Record<string, unknown>
    rateLimitedUntil: string | null
    pausedUntil: string | null
  }[]
}
