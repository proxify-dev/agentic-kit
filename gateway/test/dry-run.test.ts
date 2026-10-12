/**
 * `x-brain-dry-run`: a request the gateway answers itself. A fake upstream on an
 * ephemeral port counts what reaches it; a dry run must be nothing.
 *
 * What these keep honest: a dry run never reaches upstream — not even past a
 * blocking policy rule, which it comes before — and gets a reply that parses as
 * a whole message; its row is written like any other (origin, run, hints) with
 * the body blob kept at `capture: meta` and even `none`; a request without the
 * header goes upstream exactly as before; the header never leaves the machine;
 * and health says the build can do it.
 */
import assert from "node:assert/strict"
import http from "node:http"
import { existsSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"

import { loadConfig } from "../src/config.ts"
import type { CaptureLevel } from "../src/config.ts"
import { cannedStream, isDryRun } from "../src/dry-run.ts"
import { IDENTITY_HEADERS } from "../src/identity.ts"
import { createServer } from "../src/server.ts"
import { upstreamHeaders } from "../src/wire.ts"

const FAR = Date.now() + 24 * 3_600_000

type Harness = { dir: string; base: string; hits: () => number; rows: () => Record<string, any>[]; blob: (h: string) => unknown; close: () => Promise<void> }

/** An in-process gateway over a counting fake upstream, with one account and a policy that blocks every opus call. */
async function harness(capture: CaptureLevel): Promise<Harness> {
  const dir = mkdtempSync(join(tmpdir(), "gw-dry-"))
  let hits = 0
  const upstream = http.createServer((req, res) => {
    hits += 1
    assert.equal(req.headers["x-brain-dry-run"], undefined, "the dry-run header reached upstream")
    req.resume()
    res.writeHead(200, { "content-type": "application/json" })
    res.end(JSON.stringify({ id: "msg_real", type: "message", content: [], usage: { input_tokens: 5, output_tokens: 1 }, stop_reason: "end_turn" }))
  })
  await new Promise<void>((r) => upstream.listen(0, "127.0.0.1", r))
  const accounts = join(dir, "accounts.json")
  writeFileSync(
    accounts,
    JSON.stringify({ accounts: [{ name: "a", type: "oauth", accountUuid: "u-a", orgUuid: "o-a", accessToken: "t", refreshToken: "r", expiresAt: FAR }] }),
  )
  const gw = await createServer(
    loadConfig({
      port: 0,
      dataDir: dir,
      upstreamUrl: `http://127.0.0.1:${(upstream.address() as { port: number }).port}`,
      accountsFile: accounts,
      capture,
      policy: [{ rule: "no-opus", on: "PreRequest", kind: "deny-model", level: "block", models: ["*opus*"] }],
    }),
  )
  await new Promise<void>((r) => gw.server.listen(0, "127.0.0.1", r))
  const base = `http://127.0.0.1:${(gw.server.address() as { port: number }).port}`
  return {
    dir,
    base,
    hits: () => hits,
    rows: () =>
      existsSync(join(dir, "trace"))
        ? readdirSync(join(dir, "trace"))
            .flatMap((f) => readFileSync(join(dir, "trace", f), "utf8").split("\n").filter(Boolean))
            .map((l) => JSON.parse(l) as Record<string, any>)
        : [],
    blob: (h) => JSON.parse(readFileSync(join(dir, "blobs", h.slice(0, 2), `${h}.json`), "utf8")),
    close: async () => {
      await gw.close()
      await new Promise<void>((r) => upstream.close(() => r()))
    },
  }
}

const BODY = {
  model: "claude-opus-5-5",
  stream: true,
  system: [{ type: "text", text: "You are Claude Code." }],
  tools: [{ name: "Bash", input_schema: { type: "object" } }],
  messages: [{ role: "user", content: "hi" }],
}

const dry = (extra: Record<string, string> = {}) => ({
  "content-type": "application/json",
  authorization: "Bearer gateway-doorman",
  "x-brain-dry-run": "1",
  "x-brain-origin": "xray",
  "x-brain-run": "xray-test",
  "x-claude-code-request-class": "main",
  "x-claude-code-session-id": "0a1b2c3d-1111-4222-8333-444455556666",
  ...extra,
})

test("a dry run never reaches upstream — not even past a blocking rule — and its reply parses as one whole message", async () => {
  const h = await harness("meta")
  try {
    const r = await fetch(`${h.base}/v1/messages?beta=true`, { method: "POST", headers: dry(), body: JSON.stringify(BODY) })
    assert.equal(r.status, 200, "answered, where the opus rule would have refused it")
    assert.equal(r.headers.get("content-type"), "text/event-stream")
    const events = (await r.text())
      .split("\n\n")
      .filter(Boolean)
      .map((e) => {
        assert.match(e, /^event: (\w+)\ndata: \{"type":"\1"/)
        return JSON.parse(e.slice(e.indexOf("data: ") + 6)) as Record<string, any>
      })
    assert.deepEqual(events.map((e) => e.type), ["message_start", "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"])
    assert.match(events[0].message.id, /^msg_dryrun_/)
    assert.deepEqual(events[0].message.usage, { input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 })
    assert.equal(events[2].delta.text, "ok")
    assert.equal(events[4].delta.stop_reason, "end_turn")

    const plain = await fetch(`${h.base}/v1/messages`, { method: "POST", headers: dry({ "x-claude-code-request-class": "auxiliary" }), body: JSON.stringify({ ...BODY, stream: false }) })
    const msg = (await plain.json()) as { content: { text: string }[]; stop_reason: string }
    assert.equal(msg.content[0].text, "ok")
    assert.equal(msg.stop_reason, "end_turn")

    // `claude --account X` arrives with its pin in the path; a dry run is answered all the same.
    const pinned = await fetch(`${h.base}/tc-acct/foo/v1/messages?beta=true`, { method: "POST", headers: dry(), body: JSON.stringify({ ...BODY, stream: false }) })
    assert.equal(pinned.status, 200)
    assert.match(((await pinned.json()) as { id: string }).id, /^msg_dryrun_/)

    const other = await fetch(`${h.base}/v1/messages/count_tokens`, { method: "POST", headers: dry(), body: JSON.stringify(BODY) })
    assert.deepEqual(await other.json(), {}, "any other path: {}")

    assert.equal(h.hits(), 0, "nothing reached upstream")
    const rows = h.rows()
    assert.ok(rows.every((r) => r.dryRun === true), "every row is a dry run")
    assert.ok(rows.some((r) => r.path === "/tc-acct/foo/v1/messages" && r.messageId?.startsWith("msg_dryrun_")), "the pinned one too, path as it came")
  } finally {
    await h.close()
  }
})

test("a dry run's row is written like any other, with its body blob kept at capture meta", async () => {
  const h = await harness("meta")
  try {
    await (await fetch(`${h.base}/v1/messages`, { method: "POST", headers: dry(), body: JSON.stringify(BODY) })).text()
    const [row] = h.rows()
    assert.equal(row.dryRun, true)
    assert.equal(row.status, 200)
    assert.equal(row.account, null, "no account served it")
    assert.equal(row.identity.origin, "xray")
    assert.equal(row.identity.run, "xray-test")
    assert.equal(row.identity.session, "0a1b2c3d-1111-4222-8333-444455556666")
    assert.equal(row.hints.requestClass, "main")
    assert.match(row.messageId, /^msg_dryrun_/)
    assert.deepEqual(row.policy, [], "policy was never consulted")
    assert.deepEqual(h.blob(row.blobs.body), BODY, "the whole body, retrievable by its hash")
    assert.ok(!JSON.stringify(row).includes("gateway-doorman"), "no header value on disk")
  } finally {
    await h.close()
  }
})

test("at capture none a dry run still keeps its body; system and tools follow the capture level", async () => {
  const h = await harness("none")
  try {
    await (await fetch(`${h.base}/v1/messages`, { method: "POST", headers: dry(), body: JSON.stringify(BODY) })).text()
    const [row] = h.rows()
    assert.deepEqual(h.blob(row.blobs.body), BODY)
    assert.equal(row.blobs.system, null)
    assert.equal(row.blobs.tools, null)
  } finally {
    await h.close()
  }
})

test("a request without the header takes the path it always did", async () => {
  const h = await harness("meta")
  try {
    const { "x-brain-dry-run": _drop, ...headers } = dry()
    const r = await fetch(`${h.base}/v1/messages`, { method: "POST", headers, body: JSON.stringify({ ...BODY, model: "claude-haiku-4-5", stream: false }) })
    assert.equal(r.status, 200)
    assert.equal(((await r.json()) as { id: string }).id, "msg_real")
    assert.equal(h.hits(), 1)
    const [row] = h.rows()
    assert.equal(row.dryRun, undefined)
    assert.equal(row.account, "a")
    assert.ok(!existsSync(join(h.dir, "blobs", row.blobs.body.slice(0, 2), `${row.blobs.body}.json`)), "at meta the body is a hash, not a file")

    const refused = await fetch(`${h.base}/v1/messages`, { method: "POST", headers, body: JSON.stringify(BODY) })
    assert.equal(refused.status, 403, "and policy still refuses what it refused")
    assert.equal(h.hits(), 1)
  } finally {
    await h.close()
  }
})

test("the dry-run header is never sent upstream, and health says this build honours it", async () => {
  assert.ok(IDENTITY_HEADERS.includes("x-brain-dry-run"))
  const out = upstreamHeaders({ "x-brain-dry-run": "1", "anthropic-version": "2023-06-01" }, { type: "oauth", value: "t" }, "api.anthropic.com")
  assert.equal(out["x-brain-dry-run"], undefined)
  assert.equal(out["anthropic-version"], "2023-06-01")
  assert.equal(isDryRun({ "x-brain-dry-run": "0" }), true, "present is a dry run — the header fails safe")
  assert.equal(isDryRun({ "x-brain-dry-run": " " }), false)
  assert.equal(isDryRun({}), false)
  assert.match(cannedStream("m", "msg_x"), /"id":"msg_x"/)

  const h = await harness("meta")
  try {
    const health = (await (await fetch(`${h.base}/_gateway/health`)).json()) as { dryRun?: boolean }
    assert.equal(health.dryRun, true)
  } finally {
    await h.close()
  }
})
