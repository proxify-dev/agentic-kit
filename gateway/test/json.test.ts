/**
 * The verbs a program drives — `status`, `enable`, `disable`, `env` with `--json`
 * (and `env --plain`) — as subprocesses with the daemon down: the shape of each
 * document, and the exit code the verb already uses. No live service is needed or
 * touched: HOME and the XDG dirs are a temp folder, and the service label is a
 * throwaway one that no manager has heard of.
 */
import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import http from "node:http"
import { mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

import { envName } from "../src/brand.ts"

const CLI = fileURLToPath(new URL("../src/cli.ts", import.meta.url))

async function freePort(): Promise<number> {
  const srv = http.createServer()
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r))
  const port = (srv.address() as { port: number }).port
  await new Promise<void>((r) => srv.close(() => r()))
  return port
}

async function world(): Promise<{ dir: string; env: NodeJS.ProcessEnv; port: number }> {
  const dir = mkdtempSync(join(tmpdir(), "gw-json-"))
  const port = await freePort()
  const config = join(dir, "gateway.json")
  writeFileSync(config, JSON.stringify({ host: "127.0.0.1", port, accountsFile: join(dir, "accounts.json") }))
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    HOME: dir,
    XDG_CONFIG_HOME: join(dir, "config"),
    XDG_DATA_HOME: join(dir, "share"),
    [envName("GATEWAY_CONFIG")]: config,
    [envName("GATEWAY_DATA_DIR")]: join(dir, "data"),
    [envName("GATEWAY_LABEL")]: "com.test.gw.json",
  }
  for (const k of ["GATEWAY_PORT", "GATEWAY_ACCOUNT", "GATEWAY_CAPTURE"]) delete env[envName(k)]
  delete env.ENABLE_TOOL_SEARCH
  delete env.CLAUDE_CODE_GATEWAY_HINT_HEADERS
  return { dir, env, port }
}

function cli(args: string[], env: NodeJS.ProcessEnv, cwd: string): Promise<{ code: number | null; out: string; err: string }> {
  return new Promise((resolve) => {
    const c = spawn(process.execPath, ["--experimental-strip-types", "--no-warnings", CLI, ...args], { env, cwd })
    let out = ""
    let err = ""
    c.stdout.on("data", (d) => (out += d))
    c.stderr.on("data", (d) => (err += d))
    c.on("exit", (code) => resolve({ code, out, err }))
  })
}

const POWER_KEYS = ["accounts", "on", "partial", "service", "shim"]

function assertPower(p: Record<string, unknown>): void {
  assert.deepEqual(Object.keys(p).sort(), POWER_KEYS)
  assert.equal(typeof p.on, "boolean")
  assert.equal(typeof p.partial, "boolean")
  assert.equal(typeof p.accounts, "number")
  assert.equal((p.service as { loaded: boolean }).loaded, false)
  assert.equal((p.shim as { installed: boolean }).installed, false)
}

test("status --json: one document, exit 0 with the daemon down", async () => {
  const { dir, env, port } = await world()
  const r = await cli(["status", "--json"], env, dir)
  assert.equal(r.code, 0, r.err)
  const doc = JSON.parse(r.out) as Record<string, unknown>
  assert.deepEqual(Object.keys(doc).sort(), ["config", "pid", "power", "running"])
  assert.equal(doc.running, false)
  assert.equal(doc.pid, null)
  assertPower(doc.power as Record<string, unknown>)
  assert.deepEqual(doc.config, {
    host: "127.0.0.1",
    port,
    upstreamUrl: "https://api.anthropic.com",
    capture: "gaps",
    dataDir: join(dir, "data"),
  })
})

test("status --json: a daemon that answers is running, with its pid", async () => {
  const { dir, env, port } = await world()
  const srv = http.createServer((_, res) => (res.writeHead(200, { "content-type": "application/json" }), res.end(`{"pid":${process.pid}}`)))
  await new Promise<void>((r) => srv.listen(port, "127.0.0.1", r))
  try {
    // The pid file says who started it; the answer says it is up.
    const { mkdirSync } = await import("node:fs")
    mkdirSync(join(dir, "data"), { recursive: true })
    writeFileSync(join(dir, "data", "gateway.pid"), `${process.pid}\n`)
    const r = await cli(["status", "--json"], env, dir)
    assert.equal(r.code, 0, r.err)
    const doc = JSON.parse(r.out) as { running: boolean; pid: number }
    assert.equal(doc.running, true)
    assert.equal(doc.pid, process.pid)
  } finally {
    await new Promise<void>((r) => srv.close(() => r()))
  }
})

test("enable --json: with no accounts it is {ok:false, steps, power} and exit 1 — nothing is started", async () => {
  const { dir, env } = await world()
  const r = await cli(["enable", "--json"], env, dir)
  assert.equal(r.code, 1, r.err)
  const doc = JSON.parse(r.out) as { ok: boolean; steps: string[]; power: Record<string, unknown> }
  assert.deepEqual(Object.keys(doc).sort(), ["ok", "power", "steps"])
  assert.equal(doc.ok, false)
  assert.ok(Array.isArray(doc.steps) && doc.steps.every((s) => typeof s === "string") && doc.steps.length > 0)
  assert.match(doc.steps[0], /no accounts/)
  assertPower(doc.power)
})

test("disable --json: {ok, steps, power}, exit 0 when there is nothing to stop", async () => {
  const { dir, env } = await world()
  const r = await cli(["disable", "--json"], env, dir)
  assert.equal(r.code, 0, r.err)
  const doc = JSON.parse(r.out) as { ok: boolean; steps: string[]; power: Record<string, unknown> }
  assert.deepEqual(Object.keys(doc).sort(), ["ok", "power", "steps"])
  assert.equal(doc.ok, true)
  assert.ok(doc.steps.length > 0 && doc.steps.every((s) => typeof s === "string"))
  assertPower(doc.power)
})

test("env --json: an empty object when the daemon is not running; --plain prints no lines", async () => {
  const { dir, env } = await world()
  const j = await cli(["env", "--json"], env, dir)
  assert.equal(j.code, 0, j.err)
  assert.deepEqual(JSON.parse(j.out), {})
  const p = await cli(["env", "--plain"], env, dir)
  assert.equal(p.code, 0, p.err)
  assert.equal(p.out, "")
})

test("env --json: with the daemon's pid file live, the base URL, the doorman pass and the defaults — no headers", async () => {
  const { dir, env, port } = await world()
  const { mkdirSync } = await import("node:fs")
  mkdirSync(join(dir, "data"), { recursive: true })
  writeFileSync(join(dir, "data", "gateway.pid"), `${process.pid}\n`)
  const j = await cli(["env", "--json"], { ...env, ANTHROPIC_CUSTOM_HEADERS: "x-team: blue" }, dir)
  assert.equal(j.code, 0, j.err)
  assert.deepEqual(JSON.parse(j.out), {
    ANTHROPIC_BASE_URL: `http://127.0.0.1:${port}`,
    ANTHROPIC_AUTH_TOKEN: "gateway-doorman",
    ENABLE_TOOL_SEARCH: "true",
    CLAUDE_CODE_GATEWAY_HINT_HEADERS: "1",
  })
  // A default the caller already set is not repeated.
  const set = await cli(["env", "--json"], { ...env, ENABLE_TOOL_SEARCH: "false" }, dir)
  assert.equal("ENABLE_TOOL_SEARCH" in (JSON.parse(set.out) as object), false)
  const p = await cli(["env", "--plain"], env, dir)
  assert.deepEqual(p.out.trim().split("\n"), [
    `ANTHROPIC_BASE_URL=http://127.0.0.1:${port}`,
    "ANTHROPIC_AUTH_TOKEN=gateway-doorman",
    "ENABLE_TOOL_SEARCH=true",
    "CLAUDE_CODE_GATEWAY_HINT_HEADERS=1",
  ])
})
