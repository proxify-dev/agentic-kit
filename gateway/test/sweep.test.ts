/**
 * Retention: `Trace.sweep` deletes content — a row's body or tail — past `blobDays` unless
 * something still names it: a row inside the window, a pin, a saved xray run, a request still in
 * flight. The system prompt, tools, settings and safeguards stay for good; rows are never touched.
 * A scratch data dir; nothing of the machine's trace is read.
 */
import assert from "node:assert/strict"
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, utimesSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"
import { loadConfig } from "../src/config.ts"
import { createServer } from "../src/server.ts"
import { pinnedBlobs, savedBodies, Trace } from "../src/trace.ts"

const DAY = 86_400_000
const day = (ago: number) => new Date(Date.now() - ago * DAY).toISOString().slice(0, 10)

function world() {
  const dir = mkdtempSync(join(tmpdir(), "gw-sweep-"))
  const trace = join(dir, "trace")
  const blobs = join(dir, "blobs")
  mkdirSync(trace, { recursive: true })
  const file = (h: string) => join(blobs, h.slice(0, 2), `${h}.json`)
  /** A blob written `ago` days back, by a process that is gone. */
  const blob = (value: unknown, ago: number) => {
    const h = new Trace(trace, blobs, "full").putBlob(value) as string
    const at = new Date(Date.now() - ago * DAY)
    utimesSync(file(h), at, at)
    return h
  }
  const row = (ago: number, b: Record<string, string>) =>
    writeFileSync(
      join(trace, `${day(ago)}.ndjson`),
      `${JSON.stringify({ v: 2, id: "r", ts: `${day(ago)}T12:00:00.000Z`, blobs: { system: null, tools: null, body: null, ...b } })}\n`,
      { flag: "a" },
    )
  return { dir, trace, blobs, file, blob, row }
}

test("a blob past the window goes; one inside it stays; rows stay", async () => {
  const w = world()
  const old = w.blob({ messages: ["ten days ago"] }, 10)
  const young = w.blob({ messages: ["two days ago"] }, 2)
  w.row(10, { body: old })
  w.row(2, { body: young })

  const swept = await new Trace(w.trace, w.blobs, "full").sweep(7)

  assert.equal(swept.removed, 1)
  assert.ok(swept.bytes > 0)
  assert.equal(existsSync(w.file(old)), false)
  assert.equal(existsSync(w.file(young)), true)
  assert.deepEqual(readdirSync(w.trace).sort(), [`${day(10)}.ndjson`, `${day(2)}.ndjson`])
})

test("the system prompt, tools, settings and safeguards stay however old; a tail goes like a body", async () => {
  const w = world()
  const kept = {
    system: w.blob([{ type: "text", text: "you are claude" }], 60),
    tools: w.blob([{ name: "Bash" }], 60),
    settings: w.blob({ max_tokens: 64000 }, 60),
    safeguards: w.blob({ rules: [] }, 60),
  }
  const tail = w.blob({ role: "user", content: "Describe your most recent action" }, 60)
  w.row(60, { ...kept, tail })

  assert.equal((await new Trace(w.trace, w.blobs, "full").sweep(7)).removed, 1)
  for (const h of Object.values(kept)) assert.equal(existsSync(w.file(h)), true)
  assert.equal(existsSync(w.file(tail)), false)
})

test("an old body a row inside the window still names stays — the same request resent", async () => {
  const w = world()
  const body = w.blob({ messages: ["same"] }, 30)
  w.row(30, { body })
  w.row(1, { body })

  assert.equal((await new Trace(w.trace, w.blobs, "full").sweep(7)).removed, 0)
  assert.equal(existsSync(w.file(body)), true)
})

test("a pin keeps a body past the window, whatever its reason", async () => {
  const w = world()
  const body = w.blob({ messages: ["the only copy"] }, 20)
  const settings = w.blob({ max_tokens: 1 }, 20)
  w.row(20, { body, settings })
  const pins = join(w.dir, "pins")
  mkdirSync(pins)
  writeFileSync(join(pins, "rescue.ndjson"), `${JSON.stringify({ row: "r", reason: "no-transcript", blobs: { body, settings } })}\n{\n`)

  assert.deepEqual(pinnedBlobs(pins), [body, settings])
  assert.equal((await new Trace(w.trace, w.blobs, "full").sweep(7, pinnedBlobs(pins))).removed, 0)
  assert.equal(existsSync(w.file(body)), true)
})

test("each day's rows are read once, as they expire", async () => {
  const w = world()
  const old = w.blob({ messages: ["old"] }, 10)
  w.row(10, { body: old })
  const t = new Trace(w.trace, w.blobs, "full")
  assert.equal((await t.sweep(7)).removed, 1)
  assert.equal(JSON.parse(readFileSync(join(w.blobs, ".swept"), "utf8")).through, day(8))

  // a blob a day already read names is no longer looked for
  const late = w.blob({ messages: ["late"] }, 10)
  w.row(10, { body: late })
  assert.equal((await t.sweep(7)).removed, 0)
})

test("a saved xray run keeps its body past the window", async () => {
  const w = world()
  const body = w.blob({ messages: ["the before"] }, 20)
  const xray = join(w.dir, "xray")
  mkdirSync(xray)
  writeFileSync(join(xray, "before.json"), JSON.stringify({ v: 2, name: "before", body }))
  writeFileSync(join(xray, "broken.json"), "{")

  assert.deepEqual(savedBodies(xray), [body])
  assert.equal((await new Trace(w.trace, w.blobs, "full").sweep(7, savedBodies(xray))).removed, 0)
  assert.equal(existsSync(w.file(body)), true)
})

test("a body resent while its old row expires stays; the row it lands in keeps it; gone, it comes back when reused", async () => {
  const w = world()
  const t = new Trace(w.trace, w.blobs, "full")
  const value = { messages: ["a month old"] }
  const body = w.blob(value, 30)
  w.row(30, { body })
  assert.equal(t.putBlob(value), body) // in flight: its row is not written yet

  assert.equal((await t.sweep(7)).removed, 0)
  w.row(0, { body })
  const later = Date.now() + 10 * DAY
  assert.equal((await t.sweep(7, [], later)).removed, 1) // the new row has expired too
  assert.equal(existsSync(w.file(body)), false)

  t.putBlob(value)
  assert.equal(existsSync(w.file(body)), true)
})

test("blobDays: 0 keeps forever, a negative is refused, and status says what the gateway keeps", async () => {
  assert.throws(() => loadConfig({ blobDays: -1 }), /blobDays must be a non-negative number/)
  assert.equal(loadConfig({ blobDays: 0 }).blobDays, 0)

  const dir = mkdtempSync(join(tmpdir(), "gw-sweep-"))
  const accounts = join(dir, "accounts.json")
  writeFileSync(accounts, JSON.stringify({ accounts: [] }))
  const gw = await createServer(loadConfig({ port: 0, dataDir: dir, accountsFile: accounts, blobDays: 7, policy: [] }))
  try {
    await new Promise<void>((r) => gw.server.listen(0, "127.0.0.1", r))
    const base = `http://127.0.0.1:${(gw.server.address() as { port: number }).port}`
    const status = (await (await fetch(`${base}/_gateway/status`)).json()) as { blobDays: number; swept: unknown }
    assert.equal(status.blobDays, 7)
    assert.equal(status.swept, null, "the first sweep waits a minute past boot")
  } finally {
    await gw.close()
  }
})
