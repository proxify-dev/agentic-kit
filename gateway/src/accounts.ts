/**
 * The accounts file as the CLI shows it while the daemon is down — READ-ONLY,
 * and never the credential itself. The engine owns the file while it runs; this
 * is only a look.
 */
import { existsSync, readFileSync } from "node:fs"

export type Account = {
  name: string
  type: "oauth" | "api"
  credential: string
  accountUuid?: string
  orgUuid?: string
  expiresAt?: number
  disabled: boolean
  /** Token already expired: the engine refreshes it on the next request. */
  stale: boolean
}

type RawAccount = {
  name?: string
  type?: string
  accessToken?: string
  apiKey?: string
  accountUuid?: string
  orgUuid?: string
  expiresAt?: number
  disabled?: boolean
}

/** Read the pool. A missing file is an empty pool, not an error. */
export function loadAccounts(path: string, now: number = Date.now()): Account[] {
  if (!existsSync(path)) return []
  let doc: { accounts?: RawAccount[] }
  try {
    doc = JSON.parse(readFileSync(path, "utf8")) as { accounts?: RawAccount[] }
  } catch (err) {
    throw new Error(`gateway: cannot read accounts from ${path} — ${(err as Error).message}`)
  }
  const raw = Array.isArray(doc.accounts) ? doc.accounts : []
  return raw.map((a) => {
    const isOAuth = a.type !== "api" && typeof a.accessToken === "string"
    return {
      name: a.name ?? "(unnamed)",
      type: isOAuth ? "oauth" : "api",
      credential: (isOAuth ? a.accessToken : a.apiKey) ?? "",
      accountUuid: a.accountUuid,
      orgUuid: a.orgUuid,
      expiresAt: a.expiresAt,
      disabled: a.disabled === true,
      // 60s of slack: a token about to expire mid-request is already unusable.
      stale: typeof a.expiresAt === "number" ? a.expiresAt <= now + 60_000 : false,
    }
  })
}

/** Status view for the CLI. Never returns the credential itself. */
export function describe(accounts: Account[]): {
  name: string
  type: string
  state: string
  expiresIn: string
}[] {
  const now = Date.now()
  return accounts.map((a) => ({
    name: a.name,
    type: a.type,
    state: a.disabled ? "disabled" : a.stale ? "expired — refreshed on next use" : "usable",
    expiresIn:
      typeof a.expiresAt === "number"
        ? `${Math.round((a.expiresAt - now) / 3_600_000)}h`
        : "n/a",
  }))
}
