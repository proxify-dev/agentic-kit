// Declarations for the vendored identity.js — see types.d.ts.
import type { AccountRecord } from "./types.d.ts"

export function sameIdentity(a: { accountUuid?: string | null; orgUuid?: string | null; name?: string }, b: { accountUuid?: string | null; orgUuid?: string | null; name?: string }): boolean
export function findUpsertTarget(accounts: AccountRecord[], incoming: AccountRecord): number
