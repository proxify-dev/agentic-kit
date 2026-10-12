/**
 * `capture: gaps` — the gateway saves what no transcript will hold, and nothing a transcript does.
 * Measured before this was built (poc/wire-research/2026-09-25): a Claude Code transcript holds
 * every message, the whole system prompt and every tool of a session that keeps one; what it does
 * not hold is a session that keeps none, a side call's newest message, count_tokens, the settings
 * and the safeguards.
 *
 * What these keep honest: `Transcripts` finds a session's file in any project dir and refuses an
 * id that is not one; `putCall` stores exactly its level's parts; and through a live gateway over
 * a scratch Claude config dir, each kind of call leaves exactly its blobs.
 */
import assert from "node:assert/strict"
import http from "node:http"
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"

import { loadConfig } from "../src/config.ts"
import type { CaptureLevel } from "../src/config.ts"
import { createServer } from "../src/server.ts"
import { Trace } from "../src/trace.ts"
import { Transcripts } from "../src/transcripts.ts"

const KEPT = "0a1b2c3d-1111-4222-8333-444455556666" // a session with a transcript
const NONE = "9f8e7d6c-1111-4222-8333-444455556666" // a session without one

/** A Claude config dir holding one transcript, for KEPT. */
function claudeDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "gw-gaps-claude-"))
  mkdirSync(join(dir, "projects", "-Users-someone-project"), { recursive: true })
  mkdirSync(join(dir, "projects", "-Users-someone-other"), { recursive: true })
  writeFileSync(join(dir, "projects", "-Users-someone-project", `${KEPT}.jsonl`), "{}\n")
  return dir
}

const BODY = {
  model: "claude-opus-5-5",
  max_tokens: 64000,
  thinking: { type: "adaptive" },
  metadata: { user_id: "{}" },
  safeguards: { rules: ["Bash(git:*)"] },
  system: [{ type: "text", text: "You are Claude Code." }],
  tools: [{ name: "Bash", input_schema: { type: "object" } }],
  messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "hello" },
    { role: "user", content: "Describe your most recent action in 3-5 words" },
  ],
}

test("Transcripts: any project dir, a malformed id refused, a no asked again later", () => {
  const dir = claudeDir()
  const t = new Transcripts(dir)
  assert.equal(t.has(KEPT), true)
  assert.equal(t.has(NONE, 1_000), false)
  assert.equal(t.has(null), false)
  assert.equal(t.has("../../../etc/passwd"), false)

  writeFileSync(join(dir, "projects", "-Users-someone-other", `${NONE}.jsonl`), "{}\n")
  assert.equal(t.has(NONE, 2_000), false, "a no is kept for a while")
  assert.equal(t.has(NONE, 40_000), true, "then asked again")
})

test("putCall stores exactly its level's parts", () => {
  const file = (dir: string, h: string | null | undefined) => !!h && existsSync(join(dir, h.slice(0, 2), `${h}.json`))
  const at = (capture: CaptureLevel, is: { recorded: boolean; side: boolean }) => {
    const dir = mkdtempSync(join(tmpdir(), "gw-gaps-blobs-"))
    return { dir, b: new Trace(join(dir, "trace"), dir, capture).putCall(BODY, is) }
  }

  const none = at("none", { recorded: false, side: false })
  assert.deepEqual(none.b, { system: null, tools: null, body: null })

  const meta = at("meta", { recorded: false, side: true })
  assert.ok(file(meta.dir, meta.b.system) && file(meta.dir, meta.b.tools))
  assert.ok(file(meta.dir, meta.b.settings) && file(meta.dir, meta.b.safeguards))
  assert.deepEqual(JSON.parse(readFileSync(join(meta.dir, meta.b.settings!.slice(0, 2), `${meta.b.settings}.json`), "utf8")), {
    max_tokens: 64000,
    thinking: { type: "adaptive" },
  })
  assert.ok(meta.b.body && !file(meta.dir, meta.b.body), "meta: the body is a hash, not a file")
  assert.equal(meta.b.tail, undefined)

  const held = at("gaps", { recorded: true, side: false })
  assert.ok(held.b.body && !file(held.dir, held.b.body), "gaps: a transcript holds it, so no body")

  const alone = at("gaps", { recorded: false, side: false })
  assert.ok(file(alone.dir, alone.b.body), "gaps: no transcript will, so the whole body")

  const side = at("gaps", { recorded: true, side: true })
  assert.ok(!file(side.dir, side.b.body))
  assert.deepEqual(JSON.parse(readFileSync(join(side.dir, side.b.tail!.slice(0, 2), `${side.b.tail}.json`), "utf8")), BODY.messages.at(-1))

  const full = at("full", { recorded: true, side: true })
  assert.ok(file(full.dir, full.b.body))
  assert.equal(full.b.tail, undefined)
})

test("through a live gateway at gaps, each kind of call leaves exactly its blobs", async () => {
  const dir = mkdtempSync(join(tmpdir(), "gw-gaps-"))
  const upstream = http.createServer((req, res) => {
    req.resume()
    res.writeHead(200, { "content-type": "application/json" })
    res.end(JSON.stringify({ id: "msg_real", type: "message", content: [], usage: { input_tokens: 5, output_tokens: 1 }, stop_reason: "end_turn" }))
  })
  await new Promise<void>((r) => upstream.listen(0, "127.0.0.1", r))
  const accounts = join(dir, "accounts.json")
  writeFileSync(
    accounts,
    JSON.stringify({ accounts: [{ name: "a", type: "oauth", accountUuid: "u-a", orgUuid: "o-a", accessToken: "t", refreshToken: "r", expiresAt: Date.now() + 86_400_000 }] }),
  )
  const gw = await createServer(
    loadConfig({
      port: 0, dataDir: dir, accountsFile: accounts, capture: "gaps", policy: [],
      upstreamUrl: `http://127.0.0.1:${(upstream.address() as { port: number }).port}`,
    }),
    { claudeConfigDir: claudeDir() },
  )
  try {
    await new Promise<void>((r) => gw.server.listen(0, "127.0.0.1", r))
    const base = `http://127.0.0.1:${(gw.server.address() as { port: number }).port}`
    const call = (path: string, session: string, requestClass = "main") =>
      fetch(`${base}${path}`, {
        method: "POST",
        headers: {
          "content-type": "application/json", authorization: "Bearer gateway-doorman",
          "x-claude-code-session-id": session, "x-claude-code-request-class": requestClass,
        },
        // content-addressed: each call its own body, or two calls would share one file
        body: JSON.stringify({ ...BODY, stream: false, messages: [...BODY.messages, { role: "user", content: `${path} ${session} ${requestClass}` }] }),
      }).then((r) => assert.equal(r.status, 200))

    await call("/v1/messages?beta=true", KEPT)
    await call("/v1/messages?beta=true", NONE)
    await call("/v1/messages?beta=true", KEPT, "auxiliary")
    await call("/v1/messages/count_tokens?beta=true", KEPT)

    const rows = readdirSync(join(dir, "trace"))
      .flatMap((f) => readFileSync(join(dir, "trace", f), "utf8").split("\n").filter(Boolean))
      .map((l) => JSON.parse(l) as { recorded?: boolean; blobs: Record<string, string | null | undefined> })
    const has = (h: string | null | undefined) => !!h && existsSync(join(dir, "blobs", h.slice(0, 2), `${h}.json`))
    const [main, alone, side, count] = rows

    assert.equal(main.recorded, true)
    assert.ok(!has(main.blobs.body) && has(main.blobs.system) && has(main.blobs.settings) && has(main.blobs.safeguards))
    assert.equal(alone.recorded, false)
    assert.ok(has(alone.blobs.body), "a session with no transcript keeps its whole call")
    assert.ok(!has(side.blobs.body) && has(side.blobs.tail), "a side call keeps its newest message only")
    assert.equal(count.recorded, false)
    assert.ok(has(count.blobs.body), "count_tokens is in no transcript")
  } finally {
    await gw.close()
    await new Promise<void>((r) => upstream.close(() => r()))
  }
})
