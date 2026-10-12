/**
 * The accounts file from outside the daemon. No network: login/import run on
 * stubbed calls (the real ones open a browser / read Claude Code's keychain file).
 */
import assert from "node:assert/strict"
import { existsSync, mkdtempSync, readdirSync, readFileSync, statSync, unlinkSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"

import { atomicConfigUpdate } from "../src/engine/config.js"
import { addApiKey, findAccount, importFrom, patchAccount, readPool, removeAccount, updatePool } from "../src/pool.ts"
import { parsePinPrefix } from "../src/routes.ts"

const scratch = () => mkdtempSync(join(tmpdir(), "gw-pool-"))
const row = (name: string, uuid: string, org: string, tok: string, extra: Record<string, unknown> = {}) => ({
  name, type: "oauth" as const, accountUuid: uuid, orgUuid: org, accessToken: tok, refreshToken: `r-${tok}`, expiresAt: 1, ...extra,
})

test("find: exact name or uuid first, then a unique substring; an ambiguous one refuses", () => {
  const doc = { accounts: [row("work@acme", "u-1", "o", "t"), row("work@home", "u-2", "o", "t"), row("solo@x", "u-3", "o", "t")] }
  assert.equal(findAccount(doc, "solo@x"), 2)
  assert.equal(findAccount(doc, "U-2"), 1)
  assert.equal(findAccount(doc, "solo"), 2)
  assert.throws(() => findAccount(doc, "work"), /matches 2/)
  assert.equal(findAccount(doc, "nobody"), -1)
})

test("patch, api key, remove — each a full write that keeps the rest of the file", async () => {
  const dir = scratch()
  const f = join(dir, "accounts.json")
  writeFileSync(f, JSON.stringify({ accounts: [row("a@x", "u-a", "o", "t")], switchThreshold: 0.9, custom: { keep: true } }))

  await patchAccount(f, "a@x", { disabled: true, priority: 3 })
  await addApiKey(f, "key-1", "sk-fake-000")
  let doc = readPool(f)
  assert.equal(doc.accounts[0].disabled, true)
  assert.equal(doc.accounts[0].priority, 3)
  assert.deepEqual(doc.accounts[1], { name: "key-1", type: "apikey", source: "login", apiKey: "sk-fake-000" })
  assert.equal(doc.switchThreshold, 0.9)
  assert.deepEqual(doc.custom, { keep: true })

  const gone = await removeAccount(f, "key-1")
  assert.equal(gone.name, "key-1")
  doc = readPool(f)
  assert.equal(doc.accounts.length, 1)
  await assert.rejects(() => removeAccount(f, "nobody"), /no account matches/)
  assert.ok(!readFileSync(f, "utf8").includes("sk-fake-000"))
  assert.deepEqual(readdirSync(dir), ["accounts.json"], "no temp file, no lock left behind")
  assert.equal(statSync(f).mode & 0o777, 0o600)
})

// The lock is the engine's (config.js withConfigLock): `<file>.lock`, O_EXCL,
// {pid, at}, stale past 10 s or a dead pid. The CLI must honour it, or an edit
// that read the file before the daemon rotated a refresh token writes the old
// token back — and the next boot needs a re-login.
test("a pool edit waits for a lock another writer holds, then lands", async () => {
  const dir = scratch()
  const f = join(dir, "accounts.json")
  writeFileSync(f, JSON.stringify({ accounts: [row("a@x", "u-a", "o", "t")] }))
  writeFileSync(`${f}.lock`, JSON.stringify({ pid: process.pid, at: Date.now() }))
  setTimeout(() => unlinkSync(`${f}.lock`), 150)

  const t0 = Date.now()
  await patchAccount(f, "a@x", { priority: 7 })
  assert.ok(Date.now() - t0 >= 120, "it waited for the holder")
  assert.equal(readPool(f).accounts[0].priority, 7)
})

test("a stale lock — too old, or its pid dead — is broken, not waited on", async () => {
  const dir = scratch()
  const f = join(dir, "accounts.json")
  writeFileSync(f, JSON.stringify({ accounts: [row("a@x", "u-a", "o", "t")] }))
  for (const stale of [{ pid: process.pid, at: Date.now() - 60_000 }, { pid: 2 ** 22 + 12345, at: Date.now() }]) {
    writeFileSync(`${f}.lock`, JSON.stringify(stale))
    const t0 = Date.now()
    await patchAccount(f, "a@x", { disabled: true })
    assert.ok(Date.now() - t0 < 1000, `broken at once: ${JSON.stringify(stale)}`)
    assert.ok(!existsSync(`${f}.lock`))
  }
})

test("a pool edit racing the engine's token write: both land", async () => {
  const dir = scratch()
  const f = join(dir, "accounts.json")
  writeFileSync(f, JSON.stringify({ accounts: [row("a@x", "u-a", "o", "tok-A"), row("b@x", "u-b", "o", "tok-B")] }))
  const prev = process.env.TEAMCLAUDE_CONFIG
  process.env.TEAMCLAUDE_CONFIG = f
  try {
    await Promise.all([
      atomicConfigUpdate((disk) => {
        disk.accounts[1].refreshToken = "r-rotated"
      }),
      updatePool(f, (doc) => {
        doc.accounts[0].priority = 2
      }),
    ])
  } finally {
    if (prev === undefined) delete process.env.TEAMCLAUDE_CONFIG
    else process.env.TEAMCLAUDE_CONFIG = prev
  }
  const doc = readPool(f)
  assert.equal(doc.accounts[0].priority, 2)
  assert.equal(doc.accounts[1].refreshToken, "r-rotated")
})

test("import fetches the profile outside the lock: a token the daemon rotates meanwhile survives", async () => {
  const dir = scratch()
  const f = join(dir, "accounts.json")
  writeFileSync(f, JSON.stringify({ accounts: [row("b@x", "u-b", "o", "tok-B")] }))
  const prev = process.env.TEAMCLAUDE_CONFIG
  process.env.TEAMCLAUDE_CONFIG = f
  try {
    const got = await importFrom(f, "/unused", {}, () => {}, {
      importCredentials: async () => ({ accessToken: "tok-N", refreshToken: "r-N", expiresAt: 1 }),
      // While the profile call is out, the daemon rotates b's refresh token.
      fetchProfile: async () => {
        await atomicConfigUpdate((disk) => {
          disk.accounts[0].refreshToken = "r-rotated"
        })
        return { email: "new@x", accountUuid: "u-n", orgUuid: "o", orgName: "O" }
      },
    })
    assert.equal(got.name, "new@x")
  } finally {
    if (prev === undefined) delete process.env.TEAMCLAUDE_CONFIG
    else process.env.TEAMCLAUDE_CONFIG = prev
  }
  const doc = readPool(f)
  assert.deepEqual(doc.accounts.map((a) => a.name), ["b@x", "new@x"])
  assert.equal(doc.accounts[0].refreshToken, "r-rotated", "the import read the file after the rotation, not before")
})

test("a pin the client carried in its path is parsed off, and outranks nothing else here", () => {
  assert.deepEqual(parsePinPrefix("/tc-acct/a%40b.com/v1/messages"), { url: "/v1/messages", account: "a@b.com", mode: "pin" })
  assert.deepEqual(parsePinPrefix("/tc-prefer/work/v1/messages?x=1"), { url: "/v1/messages?x=1", account: "work", mode: "prefer" })
  assert.deepEqual(parsePinPrefix("/tc-acct/only"), { url: "/", account: "only", mode: "pin" })
  assert.equal(parsePinPrefix("/v1/messages"), null)
  assert.equal(parsePinPrefix("/tc-acct/"), null)
})
