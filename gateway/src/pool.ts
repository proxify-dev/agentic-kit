/**
 * The accounts file, from outside the daemon: add, import, toggle, remove.
 *
 * The vendored engine's schema, and its identity rule for merging — an account
 * is (accountUuid, orgUuid), so one person in two orgs is two rows and a
 * re-login updates rather than duplicates. Both come from the vendored
 * identity.js, so the CLI and the engine cannot disagree.
 *
 * Every write holds the engine's own lock (`<file>.lock`) across its read and its
 * write, and lands the way the engine's does — fsync'd temp file, rename, 0600
 * (config.js `withConfigLock`, `writeJsonAtomic`). The daemon rotates refresh
 * tokens into this file while it serves; without the lock, a CLI edit that read
 * the file before a rotation wrote it back with the rotated-away token, and the
 * next boot needed a re-login. Nothing slow runs inside the lock — a login's
 * profile call is made before it — because the daemon waits 2 s at most.
 */
import { existsSync, readFileSync } from "node:fs"
import { dirname } from "node:path"
import { privateDir, privateFile } from "./fsperm.ts"
import { withConfigLock, writeJsonAtomic } from "./engine/config.js"
import { findUpsertTarget, sameIdentity } from "./engine/identity.js"
import { fetchProfile, importCredentials, loginOAuth } from "./engine/oauth.js"
import type { AccountRecord, AccountsFile, Tokens } from "./engine/types.d.ts"

export type Profile = Awaited<ReturnType<typeof fetchProfile>>

export function readPool(path: string): AccountsFile {
  if (!existsSync(path)) return { accounts: [] }
  const doc = JSON.parse(readFileSync(path, "utf8")) as Partial<AccountsFile>
  return { ...doc, accounts: Array.isArray(doc.accounts) ? doc.accounts : [] }
}

/** Read, edit, write — all under the lock. `edit` must not await anything slow. */
export function updatePool<T>(path: string, edit: (doc: AccountsFile) => T): Promise<T> {
  return withConfigLock(path, async () => {
    const doc = readPool(path)
    const out = edit(doc)
    privateDir(dirname(path))
    await writeJsonAtomic(path, doc)
    privateFile(path, dirname(path))
    return out
  })
}

/** Substring on name, exact on either uuid — teamclaude's own grammar. */
export function findAccount(doc: AccountsFile, want: string): number {
  const w = want.trim().toLowerCase()
  if (!w) return -1
  const exact = doc.accounts.findIndex((a) => a.name.toLowerCase() === w || a.accountUuid?.toLowerCase() === w || a.orgUuid?.toLowerCase() === w)
  if (exact >= 0) return exact
  const hits = doc.accounts.map((a, i) => (a.name.toLowerCase().includes(w) ? i : -1)).filter((i) => i >= 0)
  if (hits.length > 1) throw new Error(`"${want}" matches ${hits.length} accounts — be more specific`)
  return hits[0] ?? -1
}

const orgLabel = (a: AccountRecord) => a.orgName || (a.orgUuid ? a.orgUuid.slice(0, 8) : "org")

/**
 * teamclaude's upsertOAuthAccount: the profile names the account and fixes its
 * identity, then it updates a row with the same identity or adds one. Two orgs
 * for one person get " (org)" suffixes so the names stay distinct. Pure: the
 * profile is fetched by the caller, outside the lock.
 */
export function upsertOAuth(
  doc: AccountsFile,
  tokens: Tokens,
  profile: Profile,
  opts: { name?: string; source: string },
  log: (line: string) => void,
): AccountRecord {
  const ok = profile && !profile.error
  if (!ok) log(`warning: could not fetch the account profile — ${profile?.error ?? "no token"}`)
  let name = opts.name
  if (!name && profile?.email) {
    name = profile.email
    const tier = profile.hasClaudeMax ? "Max" : profile.hasClaudePro ? "Pro" : null
    if (tier) log(`Claude ${tier} account: ${profile.email}`)
  }
  if (!name) name = `account-${doc.accounts.filter((a) => a.name.startsWith("account-")).length + 1}`

  const account: AccountRecord = {
    name,
    type: "oauth",
    source: opts.source,
    accountUuid: profile?.accountUuid ?? null,
    orgUuid: profile?.orgUuid ?? null,
    orgName: profile?.orgName ?? null,
    accessToken: tokens.accessToken,
    refreshToken: tokens.refreshToken,
    expiresAt: tokens.expiresAt,
  }
  const idx = findUpsertTarget(doc.accounts, account)
  if (idx >= 0) {
    const prev = doc.accounts[idx]
    doc.accounts[idx] = { ...prev, ...account, name: prev.name }
    log(`updated "${prev.name}"`)
    return doc.accounts[idx]
  }
  if (!opts.name && account.accountUuid) {
    const collisions = doc.accounts.filter((a) => a.accountUuid === account.accountUuid && !sameIdentity(a, account))
    if (collisions.length > 0) {
      for (const c of collisions) if (!c.name.includes(" (")) c.name = `${c.name} (${orgLabel(c)})`
      account.name = `${name} (${orgLabel(account)})`
    }
  }
  doc.accounts.push(account)
  log(`added "${account.name}"`)
  return account
}

/** The network calls login and import make, replaceable in tests. */
export type PoolDeps = {
  loginOAuth?: typeof loginOAuth
  importCredentials?: typeof importCredentials
  fetchProfile?: typeof fetchProfile
}

/** Browser PKCE login, then upsert. Runs in a terminal: it opens a browser and may read a pasted code. */
export async function login(path: string, opts: { name?: string }, log: (line: string) => void, deps: PoolDeps = {}): Promise<AccountRecord> {
  const tokens = await (deps.loginOAuth ?? loginOAuth)()
  const profile = await (deps.fetchProfile ?? fetchProfile)(tokens.accessToken)
  return updatePool(path, (doc) => upsertOAuth(doc, tokens, profile, { name: opts.name, source: "login" }, log))
}

/** Take Claude Code's own credentials file (`.credentials.json` in its config dir, by default). */
export async function importFrom(path: string, from: string, opts: { name?: string }, log: (line: string) => void, deps: PoolDeps = {}): Promise<AccountRecord> {
  const creds = await (deps.importCredentials ?? importCredentials)(from)
  if (!creds.accessToken) throw new Error(`no access token in ${from}`)
  const profile = await (deps.fetchProfile ?? fetchProfile)(creds.accessToken)
  return updatePool(path, (doc) => upsertOAuth(doc, creds, profile, { name: opts.name, source: "import" }, log))
}

export function addApiKey(path: string, name: string, apiKey: string): Promise<AccountRecord> {
  return updatePool(path, (doc) => {
    const i = doc.accounts.findIndex((a) => a.name === name)
    const row: AccountRecord = { name, type: "apikey", source: "login", apiKey }
    if (i >= 0) doc.accounts[i] = { ...doc.accounts[i], ...row }
    else doc.accounts.push(row)
    return row
  })
}

export function patchAccount(path: string, want: string, patch: { disabled?: boolean; priority?: number }): Promise<AccountRecord> {
  return updatePool(path, (doc) => {
    const i = findAccount(doc, want)
    if (i < 0) throw new Error(`no account matches "${want}"`)
    if (patch.disabled !== undefined) doc.accounts[i].disabled = patch.disabled
    if (patch.priority !== undefined) doc.accounts[i].priority = patch.priority
    return doc.accounts[i]
  })
}

export function removeAccount(path: string, want: string): Promise<AccountRecord> {
  return updatePool(path, (doc) => {
    const i = findAccount(doc, want)
    if (i < 0) throw new Error(`no account matches "${want}"`)
    return doc.accounts.splice(i, 1)[0]
  })
}
