// Declarations for the vendored resolve-accounts.js — see types.d.ts.
import type { AccountRecord, AccountsFile } from "./types.d.ts"

export function resolveAccounts(config: AccountsFile): Promise<AccountRecord[]>
