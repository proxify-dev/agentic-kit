/**
 * `direct` mode: the engine picks, injects, refreshes and rotates. A fake
 * upstream on an ephemeral port scripts the answers; no network, no real
 * credential, and the refresh call is a stub handed in through createServer.
 *
 * What these keep honest is the seam, not the engine — the engine's own
 * tests live under test/engine/. Here: the token that reaches upstream is the
 * account's, the account_uuid in the body matches it, a quota-spent 429 moves
 * to the next account while a per-minute 429 hops once (never to a third, never
 * for a pin), a 401 refreshes once and the new token lands in OUR file, what the
 * trace records when upstream fails, and the control surface edits it.
 */
import assert from "node:assert/strict"
import { execFile } from "node:child_process"
import http from "node:http"
import type { IncomingMessage, ServerResponse } from "node:http"
import { existsSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

import { loadConfig } from "../src/config.ts"
import { createServer } from "../src/server.ts"
import type { Server } from "../src/server.ts"
import { renderFragment } from "../src/shim.ts"

const scratch = () => mkdtempSync(join(tmpdir(), "gw-direct-"))
const FAR = Date.now() + 24 * 3_600_000

type Seen = { auth: string | undefined; body: string; path: string; headers: http.IncomingHttpHeaders }
type Script = (n: number, seen: Seen, res: ServerResponse) => void

const sse = (res: ServerResponse) => {
  res.writeHead(200, { "content-type": "text/event-stream" })
  res.write('event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":7,"cache_read_input_tokens":0}}}\n\n')
  res.write('event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":2}}\n\n')
  res.end()
}

/** A scripted upstream: `script(n, seen, res)` answers the n-th request (1-based). */
async function fakeUpstream(script: Script): Promise<{ url: string; seen: Seen[]; close: () => Promise<void> }> {
  const seen: Seen[] = []
  const srv = http.createServer((req: IncomingMessage, res: ServerResponse) => {
    const chunks: Buffer[] = []
    req.on("data", (c: Buffer) => chunks.push(c))
    req.on("end", () => {
      const s: Seen = {
        auth: typeof req.headers.authorization === "string" ? req.headers.authorization : undefined,
        body: Buffer.concat(chunks).toString("utf8"),
        path: req.url ?? "/",
        headers: req.headers,
      }
      seen.push(s)
      script(seen.length, s, res)
    })
  })
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r))
  const port = (srv.address() as { port: number }).port
  return { url: `http://127.0.0.1:${port}`, seen, close: () => new Promise((r) => srv.close(() => r())) }
}

function accountsFile(dir: string, rows: Record<string, unknown>[]): string {
  const f = join(dir, "accounts.json")
  writeFileSync(f, JSON.stringify({ accounts: rows, switchThreshold: 0.98 }, null, 2))
  return f
}

const oauth = (name: string, uuid: string, tok: string) => ({
  name, type: "oauth", accountUuid: uuid, orgUuid: `org-${uuid}`, accessToken: tok, refreshToken: `r-${tok}`, expiresAt: FAR,
})

async function gateway(dir: string, file: string, upstream: string, refreshFn?: (r: string) => Promise<{ accessToken: string; refreshToken: string; expiresAt: number }>): Promise<{ gw: Server; base: string }> {
  const cfg = loadConfig({ port: 0, dataDir: dir, upstreamUrl: upstream, accountsFile: file, capture: "meta", policy: [] })
  const gw = await createServer(cfg, { refreshFn })
  await new Promise<void>((r) => gw.server.listen(0, "127.0.0.1", r))
  const port = (gw.server.address() as { port: number }).port
  return { gw, base: `http://127.0.0.1:${port}` }
}

const messages = (base: string, extra: Record<string, string> = {}) =>
  fetch(`${base}/v1/messages`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: "Bearer gateway-doorman", "x-brain-origin": "test", ...extra },
    body: JSON.stringify({
      model: "claude-sonnet-5",
      stream: true,
      metadata: { user_id: JSON.stringify({ device_id: "d", account_uuid: "00000000-0000-0000-0000-000000000000" }) },
      messages: [{ role: "user", content: "hi" }],
    }),
  })

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

const lastRow = (dir: string): Record<string, unknown> => {
  const files = readdirSync(join(dir, "trace")).filter((f) => f.endsWith(".ndjson")).sort()
  const lines = readFileSync(join(dir, "trace", files[files.length - 1]), "utf8").trim().split("\n")
  return JSON.parse(lines[lines.length - 1]) as Record<string, unknown>
}

const until = async (pred: () => boolean, ms = 2000) => {
  const t = Date.now() + ms
  while (!pred() && Date.now() < t) await new Promise((r) => setTimeout(r, 20))
  return pred()
}

test("direct: the account's token goes upstream, the body's account_uuid follows it, the trace names the account", async () => {
  const dir = scratch()
  const A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
  const file = accountsFile(dir, [oauth("a@x", A, "tok-A")])
  const up = await fakeUpstream((_n, _s, res) => sse(res))
  const { gw, base } = await gateway(dir, file, up.url)

  const res = await messages(base, { "x-brain-agent": "sweep", "x-claude-code-session-id": "sess-1" })
  assert.equal(res.status, 200)
  await res.text()

  assert.equal(up.seen.length, 1)
  assert.equal(up.seen[0].auth, "Bearer tok-A", "the account's token, not the doorman pass")
  assert.ok(up.seen[0].body.includes(A), "account_uuid rewritten to the serving account")
  assert.ok(!up.seen[0].body.includes("00000000-0000"), "the client's placeholder uuid must not reach upstream")
  assert.equal(up.seen[0].headers["x-brain-agent"], undefined, "identity header leaked upstream")

  const row = lastRow(dir)
  assert.equal(row.status, 200)
  assert.equal(row.account, "a@x")
  assert.equal(row.accountSource, "rotated")
  assert.deepEqual(row.usage, { input: 7, output: 2, cacheRead: 0, cacheCreation: 0 })
  assert.ok(typeof row.ttfbMs === "number")

  await gw.close()
  await up.close()
})

test("direct: a quota-spent 429 rotates to the next account; the client never sees it", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [
    oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A"),
    oauth("b@x", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "tok-B"),
  ])
  const up = await fakeUpstream((n, _s, res) => {
    if (n === 1) {
      res.writeHead(429, {
        "content-type": "application/json",
        "retry-after": "3600",
        "anthropic-ratelimit-unified-5h-status": "rejected",
        "anthropic-ratelimit-unified-5h-utilization": "1.0",
      })
      res.end('{"type":"error","error":{"type":"rate_limit_error","message":"spent"}}')
      return
    }
    sse(res)
  })
  const { gw, base } = await gateway(dir, file, up.url)

  const res = await messages(base)
  assert.equal(res.status, 200)
  await res.text()
  assert.deepEqual(up.seen.map((s) => s.auth), ["Bearer tok-A", "Bearer tok-B"])
  assert.equal(lastRow(dir).account, "b@x")

  const status = gw.engine!.status()
  assert.equal(status.accounts.find((a) => a.name === "a@x")?.status, "throttled", "the spent account is held for its window")

  await gw.close()
  await up.close()
})

// Upstream f603ee0/eed3f40: a per-minute 429 detours THIS request once to an
// idle sibling — it used to wait 60 s per attempt on the throttled account while
// another sat idle (two requests 183 s and 185 s on 2026-09-27). One hop only,
// and the fleet's current account does not move.
test("direct: a per-minute 429 hops once to an idle sibling — never a third account, the cursor stays", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [
    oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A"),
    oauth("b@x", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "tok-B"),
    oauth("c@x", "cccccccc-cccc-cccc-cccc-cccccccccccc", "tok-C"),
  ])
  const up = await fakeUpstream((n, _s, res) => {
    if (n === 1) {
      res.writeHead(429, { "content-type": "application/json", "retry-after": "1", "anthropic-ratelimit-unified-status": "allowed" })
      res.end('{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}')
      return
    }
    sse(res)
  })
  const { gw, base } = await gateway(dir, file, up.url)

  const res = await messages(base)
  assert.equal(res.status, 200)
  await res.text()
  assert.deepEqual(up.seen.map((s) => s.auth), ["Bearer tok-A", "Bearer tok-B"], "one hop, to the idle sibling")
  assert.equal(gw.engine.am.currentIndex, 0, "a detour is not a switch")
  assert.equal(lastRow(dir).account, "b@x")

  await gw.close()
  await up.close()
})

test("direct: a pinned route never hops — a 429 and a 529 stay on the pinned account", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [
    oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A"),
    oauth("b@x", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "tok-B"),
  ])
  const up = await fakeUpstream((_n, s, res) => {
    if (s.auth !== "Bearer tok-A") return sse(res)
    if (s.path.includes("count_tokens")) {
      res.writeHead(529, { "content-type": "application/json" })
      res.end('{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}')
      return
    }
    res.writeHead(429, { "content-type": "application/json", "retry-after": "1", "anthropic-ratelimit-unified-status": "allowed" })
    res.end('{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}')
  })
  const { gw, base } = await gateway(dir, file, up.url)

  const limited = await fetch(`${base}/tc-acct/a%40x/v1/messages`, { method: "POST", headers: { "content-type": "application/json" }, body: '{"model":"claude-sonnet-5","messages":[]}' })
  assert.equal(limited.status, 429)
  await limited.text()
  assert.equal(lastRow(dir).accountSource, "pinned")
  const over = await fetch(`${base}/tc-acct/a%40x/v1/messages/count_tokens`, { method: "POST", headers: { "content-type": "application/json" }, body: '{"model":"claude-sonnet-5","messages":[]}' })
  assert.equal(over.status, 529)
  await over.text()
  assert.ok(up.seen.every((s) => s.auth === "Bearer tok-A"), "nothing went out on b's token")

  await gw.close()
  await up.close()
})

test("direct: a 401 forces one refresh, retries with the new token, and the new token lands in OUR file", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-old")])
  const refreshed: string[] = []
  const refreshFn = async (r: string) => {
    refreshed.push(r)
    return { accessToken: "tok-new", refreshToken: "r-tok-new", expiresAt: FAR + 1000 }
  }
  const up = await fakeUpstream((_n, s, res) => {
    if (s.auth === "Bearer tok-old") {
      res.writeHead(401, { "content-type": "application/json" })
      res.end('{"type":"error","error":{"type":"authentication_error","message":"revoked"}}')
      return
    }
    sse(res)
  })
  const { gw, base } = await gateway(dir, file, up.url, refreshFn)

  const res = await messages(base)
  assert.equal(res.status, 200)
  await res.text()
  assert.deepEqual(refreshed, ["r-tok-old"], "exactly one refresh, with the account's refresh token")
  assert.deepEqual(up.seen.map((s) => s.auth), ["Bearer tok-old", "Bearer tok-new"])

  const landed = await until(() => readFileSync(file, "utf8").includes("tok-new"))
  assert.ok(landed, "refreshed token persisted to the accounts file")
  const doc = JSON.parse(readFileSync(file, "utf8")) as { accounts: { accessToken: string; refreshToken: string }[] }
  assert.equal(doc.accounts[0].accessToken, "tok-new")
  assert.equal(doc.accounts[0].refreshToken, "r-tok-new")

  await gw.close()
  await up.close()
})

test("direct: reload picks up an account added to the file; PATCH disables one and the engine follows", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A")])
  const up = await fakeUpstream((_n, _s, res) => sse(res))
  const { gw, base } = await gateway(dir, file, up.url)

  // A new row lands (what `account login` does), then a reload.
  const doc = JSON.parse(readFileSync(file, "utf8")) as { accounts: unknown[] }
  doc.accounts.push(oauth("b@x", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "tok-B"))
  writeFileSync(file, JSON.stringify(doc, null, 2))
  const reload = await (await fetch(`${base}/_gateway/reload`, { method: "POST" })).json() as { ok: boolean; added: number }
  assert.deepEqual(reload, { ok: true, added: 1 })
  assert.equal(gw.engine!.status().accounts.length, 2)

  // Disable a@x through the control surface: file updated, engine reloaded.
  const patch = await fetch(`${base}/_gateway/accounts/${encodeURIComponent("a@x")}`, {
    method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ disabled: true }),
  })
  assert.equal(patch.status, 200)
  const after = JSON.parse(readFileSync(file, "utf8")) as { accounts: { name: string; disabled?: boolean }[] }
  assert.equal(after.accounts.find((a) => a.name === "a@x")?.disabled, true)
  assert.equal(gw.engine!.status().accounts.find((a) => a.name === "a@x")?.disabled, true)

  // The pool view carries the prober's status — what the TUI's "checked" column
  // reads. Against a fake upstream nothing probes unless the file says so.
  const view = (await (await fetch(`${base}/_gateway/accounts`)).json()) as { probe: { enabled: boolean; accounts: { name: string; status: string }[] } }
  assert.equal(view.probe.enabled, false)
  assert.equal(view.probe.accounts.find((a) => a.name === "a@x")?.status, "disabled")

  const res = await messages(base)
  assert.equal(res.status, 200)
  await res.text()
  assert.equal(up.seen[up.seen.length - 1].auth, "Bearer tok-B", "a disabled account is not served")

  const missing = await fetch(`${base}/_gateway/accounts/nobody`, { method: "PATCH", headers: { "content-type": "application/json" }, body: "{}" })
  assert.equal(missing.status, 404)

  await gw.close()
  await up.close()
})

test("direct: a route pin is honoured by the engine and named in the trace", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [
    oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A"),
    oauth("b@x", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "tok-B"),
  ])
  writeFileSync(
    join(dir, "routes.json"),
    JSON.stringify({ fallback: { account: "", mode: "prefer" }, rules: [{ name: "work", match: { agent: "sweep" }, account: "b@x", mode: "pin" }] }),
  )
  const up = await fakeUpstream((_n, _s, res) => sse(res))
  const { gw, base } = await gateway(dir, file, up.url)

  const res = await messages(base, { "x-brain-agent": "sweep" })
  assert.equal(res.status, 200)
  await res.text()
  assert.equal(up.seen[0].auth, "Bearer tok-B")
  const row = lastRow(dir)
  assert.equal(row.account, "b@x")
  assert.equal(row.accountSource, "pinned")

  await gw.close()
  await up.close()
})

test("direct: removing an account takes it out live — file, rotation, routes — and never shifts the others", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [
    oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A"),
    oauth("b@x", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "tok-B"),
  ])
  writeFileSync(join(dir, "routes.json"), JSON.stringify({ fallback: { account: "a@x", mode: "pin" }, rules: [] }))
  const up = await fakeUpstream((_n, _s, res) => sse(res))
  const { gw, base } = await gateway(dir, file, up.url)
  const am = gw.engine!.am
  const aIdx = am.accounts.findIndex((a) => a.name === "a@x")

  const before = await messages(base)
  await before.text()
  assert.equal(up.seen[0].auth, "Bearer tok-A", "pinned to a@x")

  const del = await fetch(`${base}/_gateway/accounts/${encodeURIComponent("a@x")}`, { method: "DELETE" })
  assert.equal(del.status, 200)
  const body = (await del.json()) as { removed: string; routesCleared: string[] }
  assert.equal(body.removed, "a@x")
  assert.deepEqual(body.routesCleared, ["fallback"], "a pin to a removed account would pin to nothing")

  // Gone from the file and from every face of the daemon.
  const disk = JSON.parse(readFileSync(file, "utf8")) as { accounts: { name: string }[] }
  assert.deepEqual(disk.accounts.map((a) => a.name), ["b@x"])
  const view = (await (await fetch(`${base}/_gateway/accounts`)).json()) as {
    accounts: { name: string }[]
    live: { accounts: { name: string }[] }
    probe: { accounts: { name: string }[] }
    routes: { fallback: { account: string } }
  }
  for (const list of [view.accounts, view.live.accounts, view.probe.accounts]) assert.deepEqual(list.map((a) => a.name), ["b@x"])
  assert.equal(view.routes.fallback.account, "")

  const after = await messages(base)
  assert.equal(after.status, 200)
  await after.text()
  assert.equal(up.seen[up.seen.length - 1].auth, "Bearer tok-B", "the next request goes to the one left")

  // The trap: the engine knows accounts by position. The removed one keeps its
  // place, so a refresh landing for it now writes nowhere — not into b@x's row.
  assert.equal(am.accounts[aIdx].name, "a@x", "nothing shifted")
  am.updateAccountTokens(aIdx, { accessToken: "tok-A2", refreshToken: "r-A2", expiresAt: FAR + 1 })
  await sleep(100)
  const b = (JSON.parse(readFileSync(file, "utf8")) as { accounts: { name: string; accessToken: string }[] }).accounts
  assert.deepEqual(b.map((a) => [a.name, a.accessToken]), [["b@x", "tok-B"]], "a removed login's token lands nowhere")

  // Logged in again: the same place, back in service.
  writeFileSync(file, JSON.stringify({ accounts: [...b, oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A3")], switchThreshold: 0.98 }, null, 2))
  await (await fetch(`${base}/_gateway/reload`, { method: "POST" })).json()
  const back = gw.engine!.status().accounts.find((a) => a.name === "a@x")
  assert.ok(back && !back.disabled, "a re-added account serves again")
  assert.equal(am.accounts.length, 2, "revived in place, not added twice")

  assert.equal((await fetch(`${base}/_gateway/accounts/nobody`, { method: "DELETE" })).status, 404)

  await gw.close()
  await up.close()
})

test("direct: the client's own OAuth refresh passes through untouched; telemetry is absorbed", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A")])
  const up = await fakeUpstream((_n, _s, res) => {
    res.writeHead(200, { "content-type": "application/json" })
    res.end('{"ok":true}')
  })
  const { gw, base } = await gateway(dir, file, up.url)

  const tok = await fetch(`${base}/v1/oauth/token`, {
    method: "POST", headers: { "content-type": "application/json", authorization: "Bearer the-clients-own" }, body: "{}",
  })
  assert.equal(tok.status, 200)
  assert.equal(up.seen[0].auth, "Bearer the-clients-own", "no account token injected on a passthrough path")

  const ev = await fetch(`${base}/api/event_logging/batch`, { method: "POST", headers: { "content-type": "application/json" }, body: "[]" })
  assert.equal(ev.status, 200)
  assert.equal(up.seen.length, 1, "telemetry never reached upstream")

  await gw.close()
  await up.close()
})

test("direct: no accounts at all is a clean 429 with a reason, not a crash", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [])
  const up = await fakeUpstream((_n, _s, res) => sse(res))
  const { gw, base } = await gateway(dir, file, up.url)

  const res = await messages(base)
  assert.equal(res.status, 429)
  const body = (await res.json()) as { error: { message: string } }
  assert.match(body.error.message, /0 accounts|exhausted/i)
  assert.equal(up.seen.length, 0)
  assert.equal(lastRow(dir).status, 429)

  await gw.close()
  await up.close()
})

test("direct: `claude --account X` resolves a partial name to the one account, and refuses what it cannot route", async (t) => {
  const dir = scratch()
  const file = accountsFile(dir, [
    oauth("work@acme.io", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A"),
    oauth("me@home.io", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "tok-B"),
    { ...oauth("old@home.io", "cccccccc-cccc-cccc-cccc-cccccccccccc", "tok-C"), disabled: true },
  ])
  const up = await fakeUpstream((_n, _s, res) => sse(res))
  const { gw, base } = await gateway(dir, file, up.url)
  const resolve = async (q: string) => {
    const r = await fetch(`${base}/_gateway/resolve?q=${encodeURIComponent(q)}`)
    return [r.status, (await r.text()).trim()] as const
  }

  assert.deepEqual(await resolve("acme"), [200, "work@acme.io"])
  assert.deepEqual(await resolve("ME@HOME.IO"), [200, "me@home.io"])
  assert.deepEqual(await resolve("nobody"), [404, 'no account matches "nobody"'])
  const [status, body] = await resolve("home")
  assert.equal(status, 409)
  assert.match(body, /matches 2 accounts/)
  assert.match((await resolve("old@"))[1], /old@home\.io is disabled/)

  // The fragment itself, run by cc-shim's own runner (`cc-shim fragment`): a
  // name it can resolve pins the launch; one it cannot leaves CC_SHIM_ROUTED
  // unset, which is what makes the shim refuse. Async: the gateway answers from
  // this process. The cc-shim is the kit's (../shim), or the one CC_SHIM_PATH
  // names when the gateway stands alone.
  const shim = process.env.CC_SHIM_PATH ?? fileURLToPath(new URL("../../shim/cc-shim.mjs", import.meta.url))
  await t.test("the routing fragment, run by cc-shim", { skip: existsSync(shim) ? false : `no cc-shim at ${shim} — set CC_SHIM_PATH to run it` }, async () => {
    const frag = join(dir, "05-gateway.mjs")
    writeFileSync(frag, renderFragment(loadConfig({ port: Number(new URL(base).port), host: "127.0.0.1" })))
    const launch = (env: Record<string, string>) =>
      new Promise<string>((r) =>
        execFile(process.execPath, [shim, "fragment", frag], { env: { PATH: process.env.PATH ?? "", ...env } }, (_e, out, err) => {
          const got = JSON.parse(out) as { imported: boolean; set: Record<string, string> }
          const line = got.imported ? `${got.set.ANTHROPIC_BASE_URL}|${got.set.CC_SHIM_ROUTED ?? ""}` : ""
          r(`${line}${err.trim() ? ` !${err.trim()}` : ""}`)
        }))
    assert.equal(await launch({ CC_SHIM_ACCOUNT: "acme" }), `${base}/tc-acct/work@acme.io|work@acme.io`)
    assert.equal(await launch({ CC_SHIM_ACCOUNT: "me@", CC_SHIM_ACCOUNT_SOFT: "1" }), `${base}/tc-prefer/me@home.io|me@home.io`)
    assert.equal(await launch({ CC_SHIM_ACCOUNT: "nobody" }), ' !claude --account: no account matches "nobody"')
    assert.equal(await launch({}), `${base}|`)
  })
  assert.equal(up.seen.length, 0)

  await gw.close()
  await up.close()
})

// Node fires a timer longer than 2^31-1 ms (~24.8 days) at once: "probe
// practically never" would have become a probe loop. Accepting a non-zero value
// here would start the prober against the real usage API, so the accepted case
// is 0 (off) and the ceiling is tested from above.
test("direct: PUT settings refuses a probe interval a timer cannot hold", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A")])
  const up = await fakeUpstream((_n, _s, res) => sse(res))
  const { gw, base } = await gateway(dir, file, up.url)
  const put = (body: unknown) => fetch(`${base}/_gateway/settings`, { method: "PUT", body: JSON.stringify(body) })

  for (const bad of [7 * 86_400 + 1, 365 * 86_400, -1, 1e308]) {
    const r = await put({ quotaProbeSeconds: bad })
    assert.equal(r.status, 400, String(bad))
    assert.match(((await r.json()) as { error: string }).error, /seven days/)
  }
  assert.equal(JSON.parse(readFileSync(file, "utf8")).quotaProbeSeconds, undefined, "nothing refused reached the file")

  const ok = await put({ quotaProbeSeconds: 0 })
  assert.equal(ok.status, 200)
  assert.equal(JSON.parse(readFileSync(file, "utf8")).quotaProbeSeconds, 0)

  await gw.close()
  await up.close()
})

// Our fix, not upstream's: the engine's tool-pair sanitizer judged a thread
// continue as a whole conversation and pruned its leading tool_result (its
// tool_use is held server-side), so the delta reached upstream as messages: [].
test("direct: a thread continue reaches upstream with its leading tool_result", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A")])
  const up = await fakeUpstream((_n, _s, res) => sse(res))
  const { gw, base } = await gateway(dir, file, up.url)

  const res = await fetch(`${base}/v1/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: "claude-sonnet-5", stream: true,
      thread: { type: "continue", previous_message_id: "msg_1" },
      messages: [{ role: "user", content: [{ type: "tool_result", tool_use_id: "toolu_held", content: "ok" }] }],
    }),
  })
  assert.equal(res.status, 200)
  await res.text()
  const sent = JSON.parse(up.seen[0].body) as { messages: unknown[] }
  assert.equal(sent.messages.length, 1)
  assert.ok(up.seen[0].body.includes("toolu_held"))

  await gw.close()
  await up.close()
})

// Upstream e0c9e9d/8b9a54a: a 429 with no retry-after and no rate-limit headers
// is about the request, not the account. It used to pause the account for a
// fabricated 60 s (every other session on it waited) and hold the client 60 s
// per attempt. Now: one hop, one short retry, then the 429 as upstream said it.
test("direct: a headerless 429 reaches the client with its reason, no invented retry-after, and pauses nothing", async () => {
  process.env.TEAMCLAUDE_HEADERLESS_429_RETRY_DELAY_MS = "20"
  const dir = scratch()
  const file = accountsFile(dir, [
    oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A"),
    oauth("b@x", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "tok-B"),
  ])
  const up = await fakeUpstream((_n, _s, res) => {
    res.writeHead(429, { "content-type": "application/json" })
    res.end('{"type":"error","error":{"type":"rate_limit_error","message":"This model is not available."}}')
  })
  const { gw, base } = await gateway(dir, file, up.url)
  try {
    const t0 = Date.now()
    const res = await messages(base)
    assert.equal(res.status, 429)
    assert.equal(res.headers.get("retry-after"), null)
    assert.match(await res.text(), /This model is not available/)
    assert.ok(Date.now() - t0 < 5_000, "no 60 s wait")
    assert.deepEqual(up.seen.map((s) => s.auth), ["Bearer tok-A", "Bearer tok-B", "Bearer tok-B"], "one hop, one retry where it landed")
    const am = gw.engine.am
    assert.ok(!am.isPaused(0) && !am.isPaused(1), "no account paused for a request's problem")
  } finally {
    delete process.env.TEAMCLAUDE_HEADERLESS_429_RETRY_DELAY_MS
    await gw.close()
    await up.close()
  }
})

// Upstream 72c3943: a 401 that one forced refresh does not cure used to reach
// Claude Code, which reads it as its own login having died. It fails over now.
// Behind the keeper this is the common case: a gateway that does not own the
// grants yet refuses the refresh, and the retry goes out on the same token.
test("direct: a 401 the refresh cannot cure fails over — the client never sees a 401", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [
    oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A"),
    oauth("b@x", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "tok-B"),
  ])
  const up = await fakeUpstream((_n, s, res) => {
    if (s.auth === "Bearer tok-B") return sse(res)
    res.writeHead(401, { "content-type": "application/json" })
    res.end('{"type":"error","error":{"type":"authentication_error","message":"invalid"}}')
  })
  // Stands in for the quiet engine: the refresh is refused, the token stays.
  const refused = async () => { throw Object.assign(new Error("another gateway owns token refreshes now"), { status: 503 }) }
  const { gw, base } = await gateway(dir, file, up.url, refused)
  try {
    const res = await messages(base)
    assert.equal(res.status, 200)
    await res.text()
    assert.deepEqual(up.seen.map((s) => s.auth), ["Bearer tok-A", "Bearer tok-A", "Bearer tok-B"])
    assert.equal(lastRow(dir).account, "b@x")
  } finally {
    await gw.close()
    await up.close()
  }
})

// The trace must say what the client got. forwardRequest resets the socket on a
// transient failure and sets no status; engine.ts read res.statusCode, which
// defaults to 200 — 162 rows since 2026-09-23 counted a reset as a success.
test("direct: an upstream reset before any reply is traced as no status, with the reason — not 200", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A")])
  const up = await fakeUpstream((_n, _s, res) => res.socket?.destroy())
  const { gw, base } = await gateway(dir, file, up.url)
  try {
    await assert.rejects(messages(base).then((r) => r.text()))
    assert.ok(await until(() => { try { return lastRow(dir).path === "/v1/messages" } catch { return false } }))
    const row = lastRow(dir)
    assert.equal(row.status, null)
    assert.match(String(row.error), /^upstream: /)
    assert.equal(gw.stats().errors, 1)
  } finally {
    await gw.close()
    await up.close()
  }
})

test("direct: a reply cut mid-stream keeps the status it sent, and names the cut", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A")])
  const up = await fakeUpstream((_n, _s, res) => {
    res.writeHead(200, { "content-type": "text/event-stream" })
    res.write('event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":7}}}\n\n')
    setTimeout(() => res.socket?.destroy(), 30)
  })
  const { gw, base } = await gateway(dir, file, up.url)
  try {
    const res = await messages(base)
    assert.equal(res.status, 200)
    await res.text().catch(() => "")
    assert.ok(await until(() => { try { return lastRow(dir).path === "/v1/messages" } catch { return false } }))
    const row = lastRow(dir)
    assert.equal(row.status, 200)
    assert.match(String(row.error), /^upstream stream: /)
  } finally {
    await gw.close()
    await up.close()
  }
})

test("direct: a client hanging up mid-stream is not an error", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A")])
  const up = await fakeUpstream((_n, _s, res) => {
    res.writeHead(200, { "content-type": "text/event-stream" })
    const tick = setInterval(() => res.write("event: ping\ndata: {}\n\n"), 20)
    setTimeout(() => { clearInterval(tick); res.end() }, 400)
  })
  const { gw, base } = await gateway(dir, file, up.url)
  try {
    const ac = new AbortController()
    const res = await fetch(`${base}/v1/messages`, { method: "POST", headers: { "content-type": "application/json" }, body: '{"model":"claude-sonnet-5","stream":true,"messages":[]}', signal: ac.signal })
    assert.equal(res.status, 200)
    ac.abort()
    assert.ok(await until(() => { try { return lastRow(dir).path === "/v1/messages" } catch { return false } }, 3000))
    const row = lastRow(dir)
    assert.equal(row.status, 200)
    assert.equal(row.error, null)
    assert.equal(gw.stats().errors, 0)
  } finally {
    await gw.close()
    await up.close()
  }
})

// Upstream b5be7fa, default flipped for a pool: the serving org's overage and
// upgrade headers leave the client's copy (Claude Code caches them as its own
// org's), after the engine has read the reply's quota headers.
test("direct: per-org overage headers are stripped by default, kept with stripOverageHeaders: false, and quota is learned either way", async () => {
  const up = await fakeUpstream((_n, _s, res) => {
    res.writeHead(200, {
      "content-type": "application/json",
      "anthropic-ratelimit-unified-5h-utilization": "0.42",
      "anthropic-ratelimit-unified-overage-status": "rejected",
      "anthropic-ratelimit-unified-overage-disabled-reason": "org_level_disabled",
      "anthropic-ratelimit-unified-upgrade-paths": "max",
    })
    res.end('{"type":"message","content":[],"usage":{"input_tokens":1,"output_tokens":1}}')
  })
  for (const strip of [undefined, false]) {
    const dir = scratch()
    const file = join(dir, "accounts.json")
    writeFileSync(file, JSON.stringify({ accounts: [oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A")], switchThreshold: 0.98, ...(strip === undefined ? {} : { stripOverageHeaders: strip }) }))
    const { gw, base } = await gateway(dir, file, up.url)
    try {
      const res = await messages(base)
      await res.text()
      const kept = strip === false
      assert.equal(res.headers.get("anthropic-ratelimit-unified-overage-status"), kept ? "rejected" : null, String(strip))
      assert.equal(res.headers.get("anthropic-ratelimit-unified-upgrade-paths"), kept ? "max" : null)
      assert.equal(res.headers.get("anthropic-ratelimit-unified-5h-utilization"), "0.42", "the rest of the family stays")
      assert.equal(gw.engine.am.accounts[0].quota.unified5h, 0.42, "the engine read the quota first")
      const settings = (await (await fetch(`${base}/_gateway/settings`)).json()) as { stripOverageHeaders: boolean }
      assert.equal(settings.stripOverageHeaders, !kept)
    } finally {
      await gw.close()
    }
  }
  await up.close()
})

test("direct: stripOverageHeaders follows the file on reload", async () => {
  const dir = scratch()
  const file = accountsFile(dir, [oauth("a@x", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tok-A")])
  const up = await fakeUpstream((_n, _s, res) => sse(res))
  const { gw, base } = await gateway(dir, file, up.url)
  try {
    assert.equal(gw.engine.stripsOverage(), true)
    const doc = JSON.parse(readFileSync(file, "utf8")) as Record<string, unknown>
    writeFileSync(file, JSON.stringify({ ...doc, stripOverageHeaders: false }))
    assert.equal((await fetch(`${base}/_gateway/reload`, { method: "POST" })).status, 200)
    assert.equal(gw.engine.stripsOverage(), false)
  } finally {
    await gw.close()
    await up.close()
  }
})
