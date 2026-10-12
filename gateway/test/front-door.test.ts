/**
 * The front door: a web page in the owner's browser is loopback too, so a
 * cross-origin request or a Host that does not name this machine is refused
 * before it reaches an account; a preflight and an unclaimed control path are
 * answered here. Local clients — curl, Node's fetch, a bare http.request, an
 * HTTP/1.0 tool with no Host — pass untouched.
 */
import assert from "node:assert/strict"
import http from "node:http"
import type { IncomingMessage, ServerResponse } from "node:http"
import net from "node:net"
import { mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"

import { loadConfig } from "../src/config.ts"
import { browserRefusal, controlPath, isLocalHost, isSameOrigin } from "../src/front-door.ts"
import { createServer } from "../src/server.ts"
import type { Server } from "../src/server.ts"

const FAR = Date.now() + 24 * 3_600_000

async function upstream(): Promise<{ url: string; hits: string[]; close: () => Promise<void> }> {
  const hits: string[] = []
  const srv = http.createServer((req: IncomingMessage, res: ServerResponse) => {
    hits.push(`${req.method} ${req.url}`)
    req.resume()
    req.on("end", () => {
      res.writeHead(200, { "content-type": "application/json" })
      res.end('{"type":"message","content":[],"usage":{"input_tokens":1,"output_tokens":1}}')
    })
  })
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r))
  const port = (srv.address() as { port: number }).port
  return { url: `http://127.0.0.1:${port}`, hits, close: () => new Promise((r) => srv.close(() => r())) }
}

async function gateway(up: string, host = "127.0.0.1"): Promise<{ gw: Server; port: number }> {
  const dir = mkdtempSync(join(tmpdir(), "gw-door-"))
  const file = join(dir, "accounts.json")
  writeFileSync(file, JSON.stringify({
    accounts: [{ name: "a@x", type: "oauth", accountUuid: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", orgUuid: "org-a", accessToken: "tok-A", refreshToken: "r-A", expiresAt: FAR }],
    switchThreshold: 0.98,
  }))
  const cfg = loadConfig({ port: 0, host, dataDir: dir, upstreamUrl: up, accountsFile: file, capture: "meta", policy: [] })
  const gw = await createServer(cfg)
  await new Promise<void>((r) => gw.server.listen(0, "127.0.0.1", r))
  return { gw, port: (gw.server.address() as { port: number }).port }
}

/** A raw request, so Host and Origin are exactly what the test says. */
function send(port: number, method: string, path: string, headers: Record<string, string>, body = ""): Promise<{ status: number; headers: http.IncomingHttpHeaders; text: string }> {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, method, path, headers: { ...headers, "content-length": String(Buffer.byteLength(body)) } }, (res) => {
      const chunks: Buffer[] = []
      res.on("data", (c: Buffer) => chunks.push(c))
      res.on("end", () => resolve({ status: res.statusCode ?? 0, headers: res.headers, text: Buffer.concat(chunks).toString("utf8") }))
    })
    req.on("error", reject)
    req.end(body)
  })
}

const MSG = JSON.stringify({ model: "claude-sonnet-5", messages: [{ role: "user", content: "hi" }] })
const JSONH = { "content-type": "application/json" }

test("front door: the rule, as a table", () => {
  const cases: [Record<string, string>, boolean][] = [
    [{}, true],
    [{ "sec-fetch-site": "same-origin" }, true],
    [{ "sec-fetch-site": "none" }, true],
    [{ "sec-fetch-site": "cross-site" }, false],
    [{ "sec-fetch-site": "same-site" }, false],
    [{ origin: "https://evil.example" }, false],
    // Node's fetch sends sec-fetch-mode, never sec-fetch-site or origin.
    [{ "sec-fetch-mode": "cors" }, true],
  ]
  for (const [h, ok] of cases) assert.equal(isSameOrigin(h), ok, JSON.stringify(h))

  assert.ok(isLocalHost(undefined, "127.0.0.1"))
  assert.ok(isLocalHost("localhost:4747", "127.0.0.1"))
  assert.ok(isLocalHost("127.0.0.1:4747", "127.0.0.1"))
  assert.ok(isLocalHost("[::1]:4747", "127.0.0.1"))
  assert.ok(isLocalHost("LOCALHOST", "127.0.0.1"))
  assert.ok(!isLocalHost("attacker.example:4747", "127.0.0.1"))
  assert.ok(!isLocalHost("[::1", "127.0.0.1"), "an unclosed bracket names nothing")
  assert.ok(isLocalHost("gw.lan:4747", "gw.lan"), "the name we are bound to")
  assert.ok(!isLocalHost("gw.lan:4747", "0.0.0.0"), "a wildcard bind widens nothing")

  assert.equal(browserRefusal({ host: "localhost:4747" }, "127.0.0.1"), null)
  assert.match(browserRefusal({ host: "localhost", origin: "https://x" }, "127.0.0.1") ?? "", /cross-origin/)
  assert.match(browserRefusal({ host: "evil.example" }, "127.0.0.1") ?? "", /Host/)

  assert.equal(controlPath("/%5Fgateway/x?y=1"), "/_gateway/x")
  assert.equal(controlPath("//_gateway\\status"), "/_gateway/status")
  assert.equal(controlPath("/%E0%A4%A"), "/%E0%A4%A", "a malformed escape stays as it is")
})

test("front door: a web page's request never reaches an account", async () => {
  const up = await upstream()
  const { gw, port } = await gateway(up.url)

  const cross = await send(port, "POST", "/v1/messages", { ...JSONH, host: `127.0.0.1:${port}`, "sec-fetch-site": "cross-site", origin: "https://evil.example" }, MSG)
  assert.equal(cross.status, 403)
  assert.match(cross.text, /permission_error/)

  const originOnly = await send(port, "GET", "/_gateway/status", { host: `127.0.0.1:${port}`, origin: "https://evil.example" })
  assert.equal(originOnly.status, 403, "Origin without Sec-Fetch-Site is a page too")

  const rebound = await send(port, "POST", "/v1/messages", { ...JSONH, host: `attacker.example:${port}`, "sec-fetch-site": "same-origin" }, MSG)
  assert.equal(rebound.status, 403, "same-origin to the browser, but the Host names the page")

  assert.deepEqual(up.hits, [], "nothing went upstream")
  assert.equal(gw.stats().refusals, 3)

  await gw.close()
  await up.close()
})

test("front door: local clients pass — same-origin, typed-in, Node fetch, a bare request, HTTP/1.0 with no Host", async () => {
  const up = await upstream()
  const { gw, port } = await gateway(up.url)

  const locals: Record<string, string>[] = [{}, { "sec-fetch-site": "same-origin" }, { "sec-fetch-site": "none" }]
  for (const extra of locals) {
    const r = await send(port, "POST", "/v1/messages", { ...JSONH, host: `localhost:${port}`, ...extra }, MSG)
    assert.equal(r.status, 200, JSON.stringify(extra))
  }
  const viaFetch = await fetch(`http://127.0.0.1:${port}/v1/messages`, { method: "POST", headers: JSONH, body: MSG })
  assert.equal(viaFetch.status, 200)
  await viaFetch.text()

  // HTTP/1.0 may omit Host; Node's own client always sends one, so a raw socket.
  const raw = await new Promise<string>((resolve, reject) => {
    const s = net.connect(port, "127.0.0.1", () => s.end("GET /_gateway/health HTTP/1.0\r\n\r\n"))
    let out = ""
    s.on("data", (c) => (out += c.toString()))
    s.on("end", () => resolve(out))
    s.on("error", reject)
  })
  assert.match(raw, /^HTTP\/1\.[01] 200/)

  assert.equal(up.hits.length, 4)
  await gw.close()
  await up.close()
})

test("front door: a preflight is answered here, with no CORS grant", async () => {
  const up = await upstream()
  const { gw, port } = await gateway(up.url)

  const r = await send(port, "OPTIONS", "/v1/messages", { host: `127.0.0.1:${port}` })
  assert.equal(r.status, 204)
  assert.equal(r.headers["access-control-allow-origin"], undefined)
  assert.ok(String(r.headers.allow).includes("POST"))

  // A real browser preflight carries Origin, and is refused before that.
  const pre = await send(port, "OPTIONS", "/v1/messages", { host: `127.0.0.1:${port}`, origin: "https://evil.example", "access-control-request-method": "POST" })
  assert.equal(pre.status, 403)

  assert.deepEqual(up.hits, [])
  await gw.close()
  await up.close()
})

test("front door: a control path no route claims is a local 404, never sent upstream", async () => {
  const up = await upstream()
  const { gw, port } = await gateway(up.url)
  const host = { host: `127.0.0.1:${port}` }

  for (const [method, path] of [
    ["GET", "/_gateway/stats"],
    ["GET", "/_gateway"],
    ["GET", "/%5Fgateway/x"],
    ["GET", "/tc-acct/a%40x/_gateway/status"],
    ["GET", "/health"],
    ["PATCH", "/_gateway/accounts"],
  ]) {
    const r = await send(port, method, path, host)
    assert.equal(r.status, 404, `${method} ${path}`)
    assert.match(r.text, /_gateway\/health/)
  }
  assert.deepEqual(up.hits, [], "no unclaimed path went out with an account's token")

  // What IS ours still answers, and what is Anthropic's still goes.
  assert.equal((await send(port, "GET", "/_gateway/health", host)).status, 200)
  assert.equal((await send(port, "GET", "/v1/models", host)).status, 200)
  assert.deepEqual(up.hits, ["GET /v1/models"])

  await gw.close()
  await up.close()
})
