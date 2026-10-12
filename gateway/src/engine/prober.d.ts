// Declarations for the vendored prober.js — see types.d.ts.
import type { AccountManager } from "./account-manager.js"

export type ProbeStatus = {
  enabled: boolean
  intervalSeconds: number
  running: boolean
  lastRunStartedAt: string | null
  lastRunFinishedAt: string | null
  nextRunAt: string | null
  accounts: { name: string; status: string; lastProbedAt: string | null; durationMs: number | null; error: string | null }[]
}

/** Seven days: Node fires a timer longer than 2^31-1 ms at once. */
export const MAX_PROBE_INTERVAL_MS: number

export class Prober {
  constructor(am: AccountManager, opts?: { intervalMs?: number; timeoutMs?: number; log?: (line: string) => void })
  intervalMs: number
  lastRunStartedAt: number | null
  lastRunFinishedAt: number | null
  nextRunAt: number | null
  getStatus(): ProbeStatus
  start(): void
  reschedule(intervalMs: number): void
  stop(): void
  probeAll(): Promise<void>
}
