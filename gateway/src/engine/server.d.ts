// Declarations for the vendored server.js — see types.d.ts.

import type { IncomingMessage, ServerResponse } from "node:http"
import type { AccountManager } from "./account-manager.js"
/** Per-request state `forwardRequest` reads and writes. */
export type ForwardCtx = {
  tried: Set<number>
  reauthed?: Set<number>
  model: string | null
  advisorModel: string | null
  sessionId: string | null
  pinnedIndex: number | null
  pinSoft: boolean
  holdBudgetMs: number
  /** Written by forwardRequest: the final status and the account that served. */
  status?: number
  account?: string
  /** Written by forwardRequest when upstream failed (a reset, a cut stream): why. */
  failure?: string
}
export function forwardRequest(
  req: { method: string | undefined; url: string | undefined; headers: Record<string, string | string[] | undefined> },
  res: ServerResponse,
  body: Buffer,
  accountManager: AccountManager,
  upstream: string,
  retryCount: number,
  hooks: { onRequestRouted?: (reqId: string, info: { account: string }) => void },
  reqId: string,
  ctx: ForwardCtx,
  logDir: string | null,
  sx: unknown,
  useSx: boolean | undefined,
): Promise<void>
export function resolveAccountPin(accountManager: AccountManager, token: string | null | undefined): number | null
