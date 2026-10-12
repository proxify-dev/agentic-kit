/**
 * The keeper: a restart is a hand-over. A real keeper process, real releases
 * (copies of this checkout's src), a fake upstream that streams every reply over
 * ~600 ms, and keep-alive clients firing through it the whole time.
 *
 * What must hold: a hand-over refuses no connection and cuts no reply, and the
 * new build serves after it; a build that cannot start never replaces the one
 * serving; a crashed worker comes back, and one that keeps crashing gives way to
 * the build before; a stop takes everything down and cleans up. Plus the token
 * rule in-process: a quiet engine neither refreshes nor writes a token.
 */
import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import type { ChildProcess } from "node:child_process"
import http from "node:http"
import type { IncomingMessage, ServerResponse } from "node:http"
import net from "node:net"
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import test from "node:test"

import { loadConfig } from "../src/config.ts"
import type { Config } from "../src/config.ts"
import { handOver, installKeeper, keeperFresh, keeperPath, listReleases, prune, readKeeper, readWanted, snapshot, tellKeeper, want } from "../src/release.ts"
import { createServer } from "../src/server.ts"
import { envName, pidAlive } from "../src/brand.ts"

const gatewayDir = join(dirname(fileURLToPath(import.meta.url)), "..")
const FAR = Date.now() + 24 * 3_600_000
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function until<T>(probe: () => T | Promise<T>, ms: number, what: string): Promise<NonNullable<T>> {
  const deadline = Date.now() + ms
  while (Date.now() < deadline) {
    const v = await Promise.resolve(probe()).catch(() => null)
    if (v) return v as NonNullable<T>
    await sleep(50)
  }
  throw new Error(`timed out waiting for ${what}`)
}

const freePort = () =>
  new Promise<number>((resolve) => {
    const s = net.createServer()
    s.listen(0, "127.0.0.1", () => {
      const port = (s.address() as { port: number }).port
      s.close(() => resolve(port))
    })
  })

/** An upstream that takes its time: first event at once, the last 600 ms later. */
async function slowUpstream(): Promise<{ url: string; close: () => Promise<void> }> {
  const srv = http.createServer((req: IncomingMessage, res: ServerResponse) => {
    req.resume()
    req.on("end", () => {
      res.writeHead(200, { "content-type": "text/event-stream" })
      res.write('event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":7}}}\n\n')
      setTimeout(() => {
        res.write('event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":2}}\n\n')
        res.end()
      }, 600)
    })
  })
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r))
  const port = (srv.address() as { port: number }).port
  return {
    url: `http://127.0.0.1:${port}`,
    close: () =>
      new Promise((r) => {
        srv.closeAllConnections()
        srv.close(() => r())
      }),
  }
}

type World = {
  dir: string
  cfg: Config
  base: string
  first: string
  keeper: ChildProcess
  log: () => string
  stop: () => Promise<void>
}

/** A keeper serving one release of this checkout, on its own port, data and accounts. */
async function world(): Promise<World> {
  const dir = mkdtempSync(join(tmpdir(), "gw-keeper-"))
  const up = await slowUpstream()
  const port = await freePort()
  const accountsFile = join(dir, "accounts.json")
  writeFileSync(
    accountsFile,
    JSON.stringify({
      accounts: [{ name: "a", type: "oauth", accountUuid: "u-a", orgUuid: "o-a", accessToken: "tok-a", refreshToken: "r-a", expiresAt: FAR }],
      switchThreshold: 0.98,
    }),
  )
  const fields = { port, host: "127.0.0.1", upstreamUrl: up.url, accountsFile, dataDir: dir, capture: "none" as const, policy: [] }
  const cfgFile = join(dir, "config.json")
  writeFileSync(cfgFile, JSON.stringify(fields))
  const cfg = loadConfig(fields)

  copyFileSync(join(gatewayDir, "src", "keeper.ts"), join(dir, "keeper.mts"))
  copyFileSync(join(gatewayDir, "src", "brand.ts"), join(dir, "brand.ts"))
  const first = snapshot(cfg, gatewayDir).id
  want(cfg, first)

  const env: Record<string, string> = {}
  for (const [k, v] of Object.entries(process.env)) if (v !== undefined && !k.startsWith(envName("GATEWAY_"))) env[k] = v
  const keeper = spawn(process.execPath, ["--experimental-strip-types", "--no-warnings", join(dir, "keeper.mts")], {
    env: { ...env, [envName("GATEWAY_CONFIG")]: cfgFile, [envName("GATEWAY_BOOT_MS")]: "10000", [envName("GATEWAY_PROBATION_MS")]: "500" },
    stdio: ["ignore", "ignore", "pipe"],
  })
  let log = ""
  keeper.stderr?.on("data", (d: Buffer) => (log += d.toString()))
  const base = `http://127.0.0.1:${port}`
  await until(() => health(base).then((h) => h.build === first), 10_000, "the first build to serve").catch((err: Error) => {
    throw new Error(`${err.message}\n${log}`)
  })

  return {
    dir, cfg, base, first, keeper,
    log: () => log,
    stop: async () => {
      if (keeper.exitCode === null && keeper.signalCode === null) {
        const gone = new Promise((r) => keeper.once("exit", r))
        keeper.kill("SIGTERM")
        await Promise.race([gone, sleep(6000).then(() => keeper.kill("SIGKILL"))])
      }
      await up.close()
    },
  }
}

type Health = { ok: boolean; pid: number; build: string | null; keeper: number | null }
const health = async (base: string): Promise<Health> => (await fetch(`${base}/_gateway/health`)).json() as Promise<Health>

const ask = (base: string) =>
  fetch(`${base}/v1/messages`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: "Bearer gateway-doorman", "x-brain-origin": "test" },
    body: JSON.stringify({ model: "claude-test", stream: true, max_tokens: 5, messages: [{ role: "user", content: "hi" }] }),
  })

/** Clients that keep asking until told to stop: every reply read to its end. */
async function hammer(base: string, done: () => boolean, clients = 6) {
  const out = { ok: 0, refused: [] as string[], cut: [] as string[] }
  const one = async () => {
    while (!done()) {
      try {
        const r = await ask(base)
        const text = await r.text()
        if (r.status === 200 && text.includes("message_delta")) out.ok += 1
        else out.cut.push(`${r.status}: ${text.slice(0, 120)}`)
      } catch (err) {
        const e = err as Error & { cause?: { code?: string; message?: string } }
        out.refused.push(e.cause?.code ?? e.cause?.message ?? e.message)
      }
    }
  }
  await Promise.all(Array.from({ length: clients }, one))
  return out
}

test("a restart under load refuses nothing, cuts nothing, and the new build serves", async () => {
  const w = await world()
  try {
    let stop = false
    const load = hammer(w.base, () => stop)
    await sleep(900)
    const next = snapshot(w.cfg, gatewayDir).id
    const r = await handOver(w.cfg, next, 15_000)
    assert.ok(r.ok, `${r.detail}\n${w.log()}`)
    await sleep(1500)
    stop = true
    const out = await load

    assert.deepEqual(out.refused, [], "no request failed to connect")
    assert.deepEqual(out.cut, [], "every reply arrived whole")
    assert.ok(out.ok >= 15, `enough traffic to mean something (${out.ok})`)
    const h = await health(w.base)
    assert.equal(h.build, next)
    const st = readKeeper(w.cfg)
    assert.equal(st?.current, next)
    assert.equal(st?.previous, w.first)
    // The old worker leaves once its last socket retires (the 5 s keep-alive timeout at most).
    await until(() => readKeeper(w.cfg)?.draining.length === 0, 10_000, "the old build to exit")
    assert.match(w.log(), /handing over — pid \d+ finishing/)
    // The new build takes the tokens only after the old one stepped down.
    const log = w.log()
    const handing = log.indexOf("handing over")
    const owns = log.lastIndexOf("owns the tokens")
    assert.ok(handing >= 0 && owns > handing, "adopt came after the old build went quiet")
  } finally {
    await w.stop()
  }
})

test("a build that cannot start never replaces the one serving", async () => {
  const w = await world()
  try {
    const bad = snapshot(w.cfg, gatewayDir).id
    writeFileSync(join(w.dir, "releases", bad, "src", "daemon.ts"), 'throw new Error("broken on purpose")\n')
    const r = await handOver(w.cfg, bad, 15_000)
    assert.equal(r.ok, false)
    assert.match(r.detail, /broken on purpose/)
    assert.match(r.detail, new RegExp(`still serving ${w.first}`))
    assert.equal(readWanted(w.cfg), w.first, "a later boot does not try the broken build first")
    assert.equal((await health(w.base)).build, w.first)
    const res = await ask(w.base)
    assert.equal(res.status, 200)
    assert.match(await res.text(), /message_delta/)
  } finally {
    await w.stop()
  }
})

test("a crashed worker comes back; one that keeps crashing gives way to the build before", async () => {
  const w = await world()
  try {
    const next = snapshot(w.cfg, gatewayDir).id
    assert.ok((await handOver(w.cfg, next, 15_000)).ok)

    // Not the first answer after the hand-over: a kept-alive socket still reaches
    // the outgoing worker once (answered, then closed) — by design.
    const before = await until(() => health(w.base).then((h) => (h.build === next ? h : null)), 5000, "the new build")
    process.kill(before.pid, "SIGKILL")
    const back = await until(
      () => health(w.base).then((h) => (h.pid !== before.pid ? h : null)),
      5000,
      "a new worker",
    )
    assert.equal(back.build, next, "the same build, restarted")

    // Two more deaths inside the minute make three: the build before takes over.
    for (let i = 0; i < 2; i++) {
      const h = await until(() => health(w.base).catch(() => null), 5000, "a worker to kill")
      process.kill(h.pid, "SIGKILL")
      await until(() => health(w.base).then((x) => x.pid !== h.pid), 5000, "the next worker")
    }
    const fell = await until(() => health(w.base).then((h) => (h.build === w.first ? h : null)), 5000, "the previous build").catch(
      (err: Error) => {
        throw new Error(`${err.message}\n${w.log()}`)
      },
    )
    assert.equal(fell.build, w.first)
    assert.match(readKeeper(w.cfg)?.last?.detail ?? "", /crashed 3 times in a minute/)
    assert.equal(readWanted(w.cfg), w.first, "a later keeper boot starts the good build, not the crashing one")
  } finally {
    await w.stop()
  }
})

test("a build that dies right after it starts is dropped, and the keeper keeps serving the old one", async () => {
  const w = await world()
  try {
    const flaky = snapshot(w.cfg, gatewayDir).id
    const entry = join(w.dir, "releases", flaky, "src", "daemon.ts")
    // Comes up, says ready, and falls over 100 ms later — inside the probation.
    writeFileSync(entry, `${readFileSync(entry, "utf8")}\nsetTimeout(() => process.exit(3), 100)\n`)
    const r = await handOver(w.cfg, flaky, 15_000)
    assert.equal(r.ok, false)
    assert.match(r.detail, /died within/)
    assert.equal(w.keeper.exitCode, null, "the keeper is still up")
    const h = await until(() => health(w.base).then((x) => (x.build === w.first ? x : null)), 3000, "the old build")
    assert.equal(h.build, w.first)
    assert.equal(readWanted(w.cfg), w.first)
    assert.equal((await ask(w.base)).status, 200)
  } finally {
    await w.stop()
  }
})

test("a restart that lands during crash recovery leaves exactly one build serving", async () => {
  const w = await world()
  try {
    const next = snapshot(w.cfg, gatewayDir).id
    const h = await health(w.base)
    process.kill(h.pid, "SIGKILL")
    // Straight into the revive: ask for the new build at once.
    await sleep(20)
    const r = await handOver(w.cfg, next, 15_000)
    assert.ok(r.ok, `${r.detail}\n${w.log()}`)
    await until(() => readKeeper(w.cfg)?.draining.length === 0, 10_000, "the revived worker to leave")
    const st = readKeeper(w.cfg)
    // Twenty fresh connections: every one answered by the one worker the keeper names.
    const pids = new Set<number>()
    for (let i = 0; i < 20; i++) {
      const res = await fetch(`${w.base}/_gateway/health`, { headers: { connection: "close" } })
      pids.add(((await res.json()) as Health).pid)
    }
    assert.deepEqual([...pids], [st?.worker], `one worker serves, and it is the one keeper.json names\n${w.log()}`)
    assert.equal(st?.current, next)
  } finally {
    await w.stop()
  }
})

test("a stop during a restart takes everything down", async () => {
  const w = await world()
  const next = snapshot(w.cfg, gatewayDir).id
  want(w.cfg, next)
  const gone = new Promise((r) => w.keeper.once("exit", r))
  w.keeper.kill("SIGHUP")
  await sleep(50)
  w.keeper.kill("SIGTERM")
  await Promise.race([gone, sleep(8000).then(() => assert.fail(`keeper did not exit\n${w.log()}`))])
  await sleep(300)
  await assert.rejects(fetch(`${w.base}/_gateway/health`), "nothing is left listening")
  await w.stop()
})

test("SIGTERM stops the keeper and its worker and removes the pid file", async () => {
  const w = await world()
  const worker = (await health(w.base)).pid
  const pidFile = join(w.dir, "gateway.pid")
  assert.equal(readFileSync(pidFile, "utf8").trim(), String(w.keeper.pid), "the pid file names the keeper")
  // The health probes above left an idle kept-alive socket open. A stop has
  // nobody to hand it to, so it must not sit out the drain cap waiting on it.
  const gone = new Promise((r) => w.keeper.once("exit", r))
  const t0 = Date.now()
  w.keeper.kill("SIGTERM")
  await Promise.race([gone, sleep(6000).then(() => assert.fail("keeper did not exit"))])
  assert.ok(Date.now() - t0 < 1500, `stopped in ${Date.now() - t0} ms, not at the 3 s cap`)
  assert.equal(existsSync(pidFile), false)
  assert.throws(() => process.kill(worker, 0), "the worker is gone too")
  await w.stop()
})

test("the keeper says it takes requests in `control`, and a stop request there stops it — no signal, as on Windows", async () => {
  const w = await world()
  assert.equal(readKeeper(w.cfg)?.control, true)
  const worker = (await health(w.base)).pid
  const gone = new Promise((r) => w.keeper.once("exit", r))
  tellKeeper(w.cfg.dataDir, "stop")
  await Promise.race([gone, sleep(6000).then(() => assert.fail(`keeper did not exit\n${w.log()}`))])
  assert.equal(w.keeper.exitCode, 0, "a stop is not a failure: no manager restarts it")
  assert.equal(existsSync(join(w.dir, "gateway.pid")), false)
  assert.equal(pidAlive(worker), false, "the worker is gone too")
  await w.stop()
})

test("POST /_gateway/stop reaches the keeper through its worker and stops them both", async () => {
  const w = await world()
  const gone = new Promise((r) => w.keeper.once("exit", r))
  const r = await fetch(`${w.base}/_gateway/stop`, { method: "POST" })
  assert.equal(r.status, 200)
  await Promise.race([gone, sleep(6000).then(() => assert.fail(`keeper did not exit\n${w.log()}`))])
  await assert.rejects(fetch(`${w.base}/_gateway/health`), "nothing is left listening")
  await w.stop()
})

test("--log: the keeper writes its own log, for a manager that redirects nothing (Task Scheduler); --env is taken", async () => {
  const dir = mkdtempSync(join(tmpdir(), "gw-keeper-log-"))
  copyFileSync(join(gatewayDir, "src", "keeper.ts"), join(dir, "keeper.mts"))
  copyFileSync(join(gatewayDir, "src", "brand.ts"), join(dir, "brand.ts"))
  writeFileSync(join(dir, "wanted"), "no-such-build\n")
  const log = join(dir, "gateway.log")
  const k = spawn(process.execPath, ["--experimental-strip-types", "--no-warnings", join(dir, "keeper.mts"), "--log", log, "--env", `${envName("GATEWAY_BOOT_MS")}=1234`], { stdio: ["ignore", "ignore", "pipe"] })
  let stderr = ""
  k.stderr?.on("data", (d: Buffer) => (stderr += d.toString()))
  const code = await new Promise((r) => k.once("exit", r))
  assert.equal(code, 1, "nothing to start")
  assert.match(readFileSync(log, "utf8"), /keeper: FAILED no-such-build: no release at .*\n.*nothing could start — exiting for the service manager to try again/)
  assert.equal(stderr, "", "all of it went to the file")
})

test("a plain gateway's stop route calls its stop, and a gateway given none has no such route", async () => {
  const dir = mkdtempSync(join(tmpdir(), "gw-stop-"))
  const cfg = loadConfig({ port: 0, host: "127.0.0.1", dataDir: dir, accountsFile: join(dir, "accounts.json"), capture: "none", policy: [] })
  let stopped = 0
  for (const onStop of [() => void stopped++, undefined]) {
    const gw = await createServer(cfg, { onStop })
    await new Promise<void>((r) => gw.server.listen(0, "127.0.0.1", r))
    const base = `http://127.0.0.1:${(gw.server.address() as { port: number }).port}`
    try {
      const r = await fetch(`${base}/_gateway/stop`, { method: "POST" })
      await r.text()
      assert.equal(r.status, onStop ? 200 : 404)
      const page = await fetch(`${base}/_gateway/stop`, { method: "POST", headers: { origin: "https://evil.example" } })
      await page.text()
      assert.equal(page.status, 403, "a web page never reaches it")
      await sleep(20)
    } finally {
      await gw.close()
    }
  }
  assert.equal(stopped, 1)
})

test("releases: a copy of src with its package.json; prune keeps the newest and never the protected", () => {
  const dir = mkdtempSync(join(tmpdir(), "gw-rel-"))
  const cfg = loadConfig({ dataDir: dir, port: 0 })
  const rel = snapshot(cfg, gatewayDir)
  const root = join(dir, "releases", rel.id)
  assert.ok(existsSync(join(root, "src", "daemon.ts")))
  assert.ok(existsSync(join(root, "src", "engine", "account-manager.js")))
  assert.ok(existsSync(join(root, "package.json")))
  assert.equal(JSON.parse(readFileSync(join(root, "release.json"), "utf8")).id, rel.id)

  // Eight fake releases, oldest first by name; the oldest is "serving".
  const ids = Array.from({ length: 8 }, (_, i) => `20200101-00000${i}-x`)
  for (const id of ids) {
    mkdirSync(join(dir, "releases", id, "src"), { recursive: true })
    writeFileSync(join(dir, "releases", id, "src", "daemon.ts"), "")
  }
  writeFileSync(join(dir, "keeper.json"), JSON.stringify({ current: ids[0], previous: ids[1] }))
  want(cfg, ids[2])
  // Nine in all; the newest five are ids[4..7] and the real copy. Of the four
  // older ones, three are protected — only ids[3] goes.
  assert.deepEqual(prune(cfg, 5), [ids[3]])
  assert.deepEqual(listReleases(cfg), [...ids.filter((id) => id !== ids[3]), rel.id])
})

test("the keeper is installed with the brand words it imports, and either drifting makes it stale", () => {
  const dir = mkdtempSync(join(tmpdir(), "gw-inst-"))
  const cfg = loadConfig({ dataDir: dir, port: 0 })
  assert.equal(keeperFresh(cfg, gatewayDir), false, "nothing installed is not fresh")
  installKeeper(cfg, gatewayDir)
  assert.ok(existsSync(keeperPath(cfg)), "the keeper is in place")
  assert.equal(
    readFileSync(join(dir, "brand.ts"), "utf8"),
    readFileSync(join(gatewayDir, "src", "brand.ts"), "utf8"),
    "brand.ts is beside it, byte for byte",
  )
  assert.equal(keeperFresh(cfg, gatewayDir), true)
  writeFileSync(join(dir, "brand.ts"), "export const CLI = 'stale'\n")
  assert.equal(keeperFresh(cfg, gatewayDir), false, "a brand.ts that differs makes the keeper stale")
  installKeeper(cfg, gatewayDir)
  assert.equal(keeperFresh(cfg, gatewayDir), true)
  rmSync(join(dir, "brand.ts"))
  assert.equal(keeperFresh(cfg, gatewayDir), false, "a missing brand.ts makes the keeper stale")
  installKeeper(cfg, gatewayDir)
  writeFileSync(keeperPath(cfg), "// old keeper\n")
  assert.equal(keeperFresh(cfg, gatewayDir), false, "a keeper that differs is still stale")
})

test("an engine booted without ownership refreshes nothing until it owns the tokens", async () => {
  const dir = mkdtempSync(join(tmpdir(), "gw-owner-"))
  const accountsFile = join(dir, "accounts.json")
  const row = { name: "a", type: "oauth", accountUuid: "u-a", orgUuid: "o-a", accessToken: "tok-a", refreshToken: "r-a", expiresAt: FAR }
  // These engines face the default upstream, Anthropic's: no background probe
  // asking its usage API with a fake token (and forcing a refresh on the 401).
  writeFileSync(accountsFile, JSON.stringify({ accounts: [row], quotaProbeSeconds: 0 }))
  let refreshes = 0
  const gw = await createServer(loadConfig({ port: 0, dataDir: dir, accountsFile, capture: "none", policy: [] }), {
    owner: false,
    refreshFn: async () => {
      refreshes += 1
      return { accessToken: `tok-${refreshes}`, refreshToken: `r-${refreshes}`, expiresAt: FAR + refreshes }
    },
  })
  try {
    await gw.engine.am.ensureTokenFresh(0, true)
    assert.equal(refreshes, 0, "the previous owner may still hold the grant")
    await gw.engine.own()
    await gw.engine.am.ensureTokenFresh(0, true)
    assert.equal(refreshes, 1)
    await sleep(100)
    assert.equal(JSON.parse(readFileSync(accountsFile, "utf8")).accounts[0].refreshToken, "r-1", "an owner writes what it minted")
  } finally {
    await gw.close()
  }
})

test("a refresh already under way when the engine goes quiet still lands in the file", async () => {
  const dir = mkdtempSync(join(tmpdir(), "gw-inflight-"))
  const accountsFile = join(dir, "accounts.json")
  const row = { name: "a", type: "oauth", accountUuid: "u-a", orgUuid: "o-a", accessToken: "tok-a", refreshToken: "r-a", expiresAt: FAR }
  writeFileSync(accountsFile, JSON.stringify({ accounts: [row], quotaProbeSeconds: 0 }))
  const gw = await createServer(loadConfig({ port: 0, dataDir: dir, accountsFile, capture: "none", policy: [] }), {
    refreshFn: async () => {
      await sleep(200)
      return { accessToken: "tok-new", refreshToken: "r-new", expiresAt: FAR + 1000 }
    },
  })
  try {
    const refreshing = gw.engine.am.ensureTokenFresh(0, true)
    await sleep(20)
    // quiesce resolves only once that refresh has landed on disk.
    await gw.engine.quiesce()
    assert.equal(JSON.parse(readFileSync(accountsFile, "utf8")).accounts[0].refreshToken, "r-new", "the rotated token is not lost")
    await refreshing
  } finally {
    await gw.close()
  }
})

test("a quiet engine neither refreshes a token nor writes one", async () => {
  const dir = mkdtempSync(join(tmpdir(), "gw-quiet-"))
  const accountsFile = join(dir, "accounts.json")
  const row = { name: "a", type: "oauth", accountUuid: "u-a", orgUuid: "o-a", accessToken: "tok-a", refreshToken: "r-a", expiresAt: FAR }
  writeFileSync(accountsFile, JSON.stringify({ accounts: [row], quotaProbeSeconds: 0 }))
  let refreshes = 0
  const gw = await createServer(loadConfig({ port: 0, dataDir: dir, accountsFile, capture: "none", policy: [] }), {
    refreshFn: async () => {
      refreshes += 1
      return { accessToken: "tok-new", refreshToken: "r-new", expiresAt: FAR + 1000 }
    },
  })
  try {
    await gw.engine.quiesce()
    await gw.engine.am.ensureTokenFresh(0, true)
    assert.equal(refreshes, 0, "no refresh reached the token endpoint")
    assert.notEqual(gw.engine.am.accounts[0].status, "error", "the account stays in rotation")
    gw.engine.am.updateAccountTokens(0, { accessToken: "tok-x", refreshToken: "r-x", expiresAt: FAR })
    await sleep(100)
    assert.equal(JSON.parse(readFileSync(accountsFile, "utf8")).accounts[0].refreshToken, "r-a", "nothing written to the file")
  } finally {
    await gw.close()
  }
})
