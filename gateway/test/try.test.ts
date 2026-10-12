/**
 * The CLI as a subprocess. `try` end to end: a trial gateway comes up on its
 * own port and file, one "claude" goes through it, and it is gone afterwards —
 * the claude is a script that sends one request and prints what it was handed,
 * the upstream a fake. Then the switch: `enable` / `disable` / `status` in a
 * sandbox where launchctl is a stub and HOME is scratch.
 *
 * What these keep honest: the account's token (not the doorman) reaches
 * upstream, the args after `--` reach claude, claude's exit code is the verb's,
 * the trial is stopped and its pid files removed, `--trial` can never make a
 * lifecycle verb reach the real launchd unit, and `enable` with nothing to
 * serve touches nothing.
 */
import assert from "node:assert/strict"
import { spawn, spawnSync } from "node:child_process"
import http from "node:http"
import type { IncomingMessage, ServerResponse } from "node:http"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

import { manager, worktreeOf } from "../src/service.ts"
import { envName } from "../src/brand.ts"
import { command as gw } from "../src/command.ts"

const CLI = fileURLToPath(new URL("../src/cli.ts", import.meta.url))
const FAR = Date.now() + 24 * 3_600_000

type Seen = { auth: string | undefined; path: string }

async function fakeUpstream(): Promise<{ url: string; seen: Seen[]; close: () => Promise<void> }> {
  const seen: Seen[] = []
  const srv = http.createServer((req: IncomingMessage, res: ServerResponse) => {
    req.resume()
    req.on("end", () => {
      seen.push({ auth: typeof req.headers.authorization === "string" ? req.headers.authorization : undefined, path: req.url ?? "/" })
      res.writeHead(200, { "content-type": "text/event-stream" })
      res.write('event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":7,"cache_read_input_tokens":0}}}\n\n')
      res.write('event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":2}}\n\n')
      res.end()
    })
  })
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r))
  const port = (srv.address() as { port: number }).port
  return { url: `http://127.0.0.1:${port}`, seen, close: () => new Promise((r) => srv.close(() => r())) }
}

async function freePort(): Promise<number> {
  const srv = http.createServer()
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r))
  const port = (srv.address() as { port: number }).port
  await new Promise<void>((r) => srv.close(() => r()))
  return port
}

/**
 * A scratch world: its own config (upstreamUrl → the fake), data dir, a real
 * accounts file of one and a trial pool of one — nothing of the machine's.
 */
function world(upstreamUrl: string): { dir: string; trialDir: string; env: NodeJS.ProcessEnv } {
  const dir = mkdtempSync(join(tmpdir(), "gw-try-"))
  const config = join(dir, "gateway.json")
  const accounts = join(dir, "accounts.json")
  writeFileSync(config, JSON.stringify({ upstreamUrl, capture: "meta", policy: [], accountsFile: accounts }))
  writeFileSync(
    accounts,
    JSON.stringify({
      accounts: [{ name: "real", type: "oauth", accountUuid: "u-r", orgUuid: "org-r", accessToken: "tok-r", refreshToken: "r-r", expiresAt: FAR }],
    }),
  )
  const trialDir = join(dir, "data", "trial")
  mkdirSync(trialDir, { recursive: true })
  writeFileSync(
    join(trialDir, "accounts.json"),
    JSON.stringify({
      accounts: [{ name: "a", type: "oauth", accountUuid: "u-a", orgUuid: "org-a", accessToken: "tok-a", refreshToken: "r-a", expiresAt: FAR }],
    }),
  )
  const env: NodeJS.ProcessEnv = { ...process.env, HOME: dir, [envName("GATEWAY_CONFIG")]: config, [envName("GATEWAY_DATA_DIR")]: join(dir, "data") }
  for (const k of ["GATEWAY_PORT", "GATEWAY_UPSTREAM", "GATEWAY_ACCOUNTS_FILE", "GATEWAY_ACCOUNT"]) delete env[envName(k)]
  return { dir, trialDir, env }
}

/** A claude stand-in: one streamed request through the base URL it was given, then exit 3. */
function fakeClaude(dir: string): string {
  const js = join(dir, "fake-claude.mjs")
  writeFileSync(
    js,
    `const hdrs = Object.fromEntries((process.env.ANTHROPIC_CUSTOM_HEADERS ?? "").split("\\n").filter(Boolean)
  .map((l) => [l.slice(0, l.indexOf(":")).trim(), l.slice(l.indexOf(":") + 1).trim()]))
const r = await fetch(process.env.ANTHROPIC_BASE_URL + "/v1/messages", {
  method: "POST",
  headers: { "content-type": "application/json", authorization: "Bearer " + process.env.ANTHROPIC_AUTH_TOKEN, ...hdrs },
  body: JSON.stringify({ model: "claude-haiku-4-5", max_tokens: 5, stream: true, messages: [{ role: "user", content: "hi" }] }),
})
await r.text()
console.log("fake-claude status=" + r.status + " argv=" + process.argv.slice(2).join(" ") + " shim=" + process.env.CC_SHIM_DISABLE)
process.exit(3)
`,
  )
  const sh = join(dir, "claude")
  writeFileSync(sh, `#!/bin/sh\nexec "${process.execPath}" "${js}" "$@"\n`)
  chmodSync(sh, 0o755)
  return sh
}

/** Async on purpose: the fake upstream lives in this process and must keep answering. */
function cli(argv: string[], env: NodeJS.ProcessEnv): Promise<{ code: number | null; out: string; err: string }> {
  return new Promise((resolve) => {
    const c = spawn(process.execPath, ["--experimental-strip-types", "--no-warnings", CLI, ...argv], { env, stdio: ["ignore", "pipe", "pipe"] })
    let out = ""
    let err = ""
    c.stdout.on("data", (b: Buffer) => (out += b.toString()))
    c.stderr.on("data", (b: Buffer) => (err += b.toString()))
    c.on("exit", (code) => resolve({ code, out, err }))
  })
}

test("try: one claude through a direct trial gateway, which is gone afterwards", async () => {
  const up = await fakeUpstream()
  try {
    const { dir, trialDir, env } = world(up.url)
    const port = await freePort()
    const r = await cli(["try", "--port", String(port), "--bin", fakeClaude(dir), "--", "-p", "hello"], env)

    assert.equal(r.code, 3, `claude's exit code is the verb's\n${r.out}\n${r.err}`)
    assert.match(r.out, /fake-claude status=200 argv=-p hello shim=1/)
    assert.equal(up.seen.length, 1)
    assert.equal(up.seen[0].auth, "Bearer tok-a", "the trial account's token went upstream, not the doorman")
    assert.match(r.out, /served\s+1 request — 1 ok · by a ×1/)
    assert.match(r.out, /token\s+a: not refreshed yet/)
    assert.ok(!existsSync(join(trialDir, "gateway.pid")), "the trial daemon removed its pid file on the way out")
    assert.ok(!existsSync(join(trialDir, "owner.pid")), "the owner marker is gone")
    const probe = await fetch(`http://127.0.0.1:${port}/_gateway/health`).then(
      () => "up",
      () => "down",
    )
    assert.equal(probe, "down")
  } finally {
    await up.close()
  }
})

test("try: without `--`, what is not ours still reaches claude — inside the kit the CLI is reached through click, which eats the `--`", async () => {
  const up = await fakeUpstream()
  try {
    const { dir, env } = world(up.url)
    const r = await cli(["try", "-p", "hello", "--port", String(await freePort()), "--model", "haiku", "--bin", fakeClaude(dir)], env)
    assert.equal(r.code, 3, `${r.out}\n${r.err}`)
    assert.match(r.out, /argv=-p hello --model haiku shim=1/)
  } finally {
    await up.close()
  }
})

test("try: a port someone else holds is refused before any login", async () => {
  const { env } = world("http://127.0.0.1:9")
  const squatter = http.createServer()
  await new Promise<void>((r) => squatter.listen(0, "127.0.0.1", r))
  const port = (squatter.address() as { port: number }).port
  try {
    const r = await cli(["try", "--port", String(port), "--bin", "/bin/false"], env)
    assert.equal(r.code, 1)
    assert.match(r.err, new RegExp(`:${port} is held by pid ${process.pid}`))
    assert.doesNotMatch(r.out, /login/)
  } finally {
    await new Promise<void>((r) => squatter.close(() => r()))
  }
})

test(`the service refuses a worktree's daemon — \`${gw()}\` now runs the checkout you stand in`, () => {
  const dir = mkdtempSync(join(tmpdir(), "gw-wt-"))
  const main = join(dir, "main")
  const wt = join(dir, "wt")
  for (const root of [main, wt]) mkdirSync(join(root, "gateway", "src"), { recursive: true })
  mkdirSync(join(main, ".git"))
  writeFileSync(join(wt, ".git"), `gitdir: ${join(main, ".git", "worktrees", "wt")}\n`)
  assert.equal(worktreeOf(join(main, "gateway", "src", "daemon.ts")), null)
  assert.equal(worktreeOf(join(wt, "gateway", "src", "daemon.ts")), join(wt))
  // Only the pure check runs here: install() itself would reach launchd.
})

test("worktreeOf: a main checkout, or a copy with no .git above it, is no worktree", () => {
  const dir = mkdtempSync(join(tmpdir(), "gw-wt-"))
  // a main checkout: the .git is in the gateway folder itself.
  const repo = join(dir, "cc-gateway")
  mkdirSync(join(repo, "src"), { recursive: true })
  mkdirSync(join(repo, ".git"))
  assert.equal(worktreeOf(join(repo, "src", "daemon.ts")), null)
  // A worktree of that repo: its .git is a file in the same place.
  const wt = join(dir, "cc-gateway-wt")
  mkdirSync(join(wt, "src"), { recursive: true })
  writeFileSync(join(wt, ".git"), `gitdir: ${join(repo, ".git", "worktrees", "wt")}\n`)
  assert.equal(worktreeOf(join(wt, "src", "daemon.ts")), wt)
  // A copy with no .git anywhere up to the root (tmpdir has none on any CI we run).
  const copy = join(dir, "share", "cc-gateway", "0.1.0")
  mkdirSync(join(copy, "src"), { recursive: true })
  assert.equal(worktreeOf(join(copy, "src", "daemon.ts")), null)
})

/**
 * A sandbox for the verbs that reach the service manager and the shim: PATH
 * holds a launchctl and a systemctl that only record (and answer "not loaded"),
 * and HOME is scratch — so no regression here can load or unload the real unit
 * or rewrite the real shim fragment.
 */
function sandbox(dir: string, env: NodeJS.ProcessEnv): { safe: NodeJS.ProcessEnv; calls: string; run: (argv: string[]) => ReturnType<typeof spawnSync> } {
  const stubs = join(dir, "stubs")
  mkdirSync(stubs)
  const calls = join(dir, "launchctl.calls")
  for (const tool of ["launchctl", "systemctl", "loginctl"]) {
    writeFileSync(join(stubs, tool), `#!/bin/sh\necho "$@" >> "${calls}"\nexit 1\n`)
    chmodSync(join(stubs, tool), 0o755)
  }
  const safe: NodeJS.ProcessEnv = { ...env, HOME: dir, PATH: `${stubs}:${dirname(process.execPath)}` }
  delete safe.XDG_CONFIG_HOME
  delete safe.XDG_DATA_HOME
  const run = (argv: string[]) => spawnSync(process.execPath, ["--experimental-strip-types", "--no-warnings", CLI, ...argv], { env: safe, encoding: "utf8" })
  return { safe, calls, run }
}

test("--trial never lets a lifecycle verb reach the real gateway", () => {
  const { dir, env } = world("http://127.0.0.1:9")
  const { calls, run } = sandbox(dir, env)
  for (const argv of [["stop", "--trial"], ["restart", "--trial"], ["enable", "--trial"], ["disable", "--trial"], ["service", "install", "--trial"], ["shim", "on", "--trial"]]) {
    const r = run(argv)
    assert.equal(r.status, 1, argv.join(" "))
    assert.match(String(r.stderr), /manages the real gateway/)
  }
  assert.ok(!existsSync(calls), "launchctl was never called")
  assert.ok(!existsSync(join(dir, ".config", "cc-shim")), "no shim fragment was written")
})

test("enable with no accounts refuses before touching the service or the routing", () => {
  const { dir, env } = world("http://127.0.0.1:9")
  writeFileSync(join(dir, "empty.json"), JSON.stringify({ accounts: [] }))
  const { calls, run } = sandbox(dir, { ...env, [envName("GATEWAY_ACCOUNTS_FILE")]: join(dir, "empty.json") })
  const r = run(["enable"])
  assert.equal(r.status, 1)
  assert.match(String(r.stdout), new RegExp(`no accounts in .* — add one first: ${gw("account login")}`))
  // Asking launchd whether the unit is loaded is fine; telling it anything is not.
  assert.doesNotMatch(existsSync(calls) ? readFileSync(calls, "utf8") : "", /bootout|bootstrap|kickstart|enable/)
  assert.ok(!existsSync(join(dir, "Library", "LaunchAgents")), "no plist was written")
  assert.ok(!existsSync(join(dir, ".config", "systemd")), "no systemd unit was written")
  assert.ok(!existsSync(join(dir, ".config", "cc-shim")), "no shim fragment was written")
})

test("status asks the running gateway, and names an older build rather than misreading it", async () => {
  const { dir, env } = world("http://127.0.0.1:9")
  // A daemon from before this build: its health has no `accounts`, and it
  // serves by rules this build cannot read.
  const older = http.createServer((req, res) => {
    res.writeHead(200, { "content-type": "application/json" })
    res.end(
      JSON.stringify(
        req.url === "/_gateway/health"
          ? { ok: true, upstream: "chain", port: 0, pid: process.pid }
          : { requests: 5, inFlight: 0, errors: 0, refusals: 0, rules: [], budget: {} },
      ),
    )
  })
  await new Promise<void>((r) => older.listen(0, "127.0.0.1", r))
  const port = (older.address() as { port: number }).port
  try {
    writeFileSync(join(dir, "data", "gateway.pid"), String(process.pid))
    writeFileSync(join(dir, "empty.json"), JSON.stringify({ accounts: [] }))
    const { safe } = sandbox(dir, { ...env, [envName("GATEWAY_PORT")]: String(port), [envName("GATEWAY_ACCOUNTS_FILE")]: join(dir, "empty.json") })
    const r = await cli(["status"], safe)
    assert.equal(r.code, 0, r.err)
    assert.match(r.out, /running an older build \(pid \d+\) that still serves by its own rules · this build has no accounts yet/)
    assert.doesNotMatch(r.out, /nothing can be served/, "the file's zero is not what the running gateway serves")
    assert.match(r.out, /traffic {3}5 requests/)
  } finally {
    await new Promise<void>((r) => older.close(() => r()))
  }
})

test("status says off, and disable takes routing down before the service", () => {
  const { dir, env } = world("http://127.0.0.1:9")
  const { calls, safe, run } = sandbox(dir, env)
  const off = run(["status"])
  assert.equal(off.status, 0)
  assert.match(String(off.stdout), new RegExp(`^gateway {3}off · claude uses its own login — \`${gw("enable")}\` turns it on$`, "m"))

  // Routing on by hand, service down: the half-on state names both ways out.
  const frag = join(dir, ".config", "cc-shim", "conf.d", "05-gateway.mjs")
  mkdirSync(dirname(frag), { recursive: true })
  writeFileSync(frag, "# stale\n")
  assert.match(String(run(["status"]).stdout), /half on — claude is routed to it, but it is stopped/)

  const r = spawnSync(process.execPath, ["--experimental-strip-types", "--no-warnings", CLI, "disable"], { env: safe, encoding: "utf8" })
  assert.equal(r.status, 0, String(r.stderr))
  assert.match(r.stdout, /routing {3}off — new claude sessions use their own login/)
  assert.match(r.stdout, new RegExp(`service {3}${manager().label.replace(/\./g, "\\.")} not loaded`))
  assert.ok(!existsSync(frag), "the routing file is gone")
  // The stub was only asked whether the unit is loaded — never told to unload one.
  assert.doesNotMatch(readFileSync(calls, "utf8"), /bootout|bootstrap|kickstart|\bstop\b|disable/)
})
