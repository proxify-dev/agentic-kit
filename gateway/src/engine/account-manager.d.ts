// Declarations for the vendored account-manager.js — see types.d.ts.
import type { AccountRecord, Tokens, ManagedAccount, ManagerStatus } from "./types.d.ts"

export class AccountManager {
  constructor(
    accounts: AccountRecord[],
    switchThreshold?: number,
    opts?: {
      refreshFn?: (refreshToken: string) => Promise<Tokens>
      routes?: unknown[]
      ramp?: Record<string, unknown>
      distributeSessions?: boolean
      forcedRefreshFloorMs?: number
    },
  )
  accounts: ManagedAccount[]
  currentIndex: number
  switchThreshold: number
  getStatus(): ManagerStatus
  restoreQuotaState(saved: unknown): void
  exportQuotaState(): unknown
  selectActiveAccount(): void
  onTokenRefresh(cb: (index: number, tokens: Tokens) => void): void
  updateAccountTokens(index: number, tokens: Tokens): void
  addAccount(acct: AccountRecord): void
  removeAccount(index: number): void
  setDisabled(index: number, disabled: boolean): void
  setRoutes(routes: unknown[] | undefined): void
  getRoutes(): unknown[]
  ensureTokenFresh(index: number, force?: boolean): Promise<void>
  isAvailable(account: ManagedAccount, model?: string | null, advisorModel?: string | null): boolean
  /** Inside a rate-limit pause — which selection does not model. */
  isPaused(index: number, now?: number): boolean
  /** The account a one-hop failover would take, moving no cursor. */
  pickAlternate(exclude: Set<number>, model?: string | null, advisorModel?: string | null): ManagedAccount | null
}
export function parseRequestModel(body: Buffer | string | null | undefined): string | null
export function parseAdvisorModel(body: Buffer | string | null | undefined): string | null
