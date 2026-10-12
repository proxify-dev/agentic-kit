/**
 * `run` end to end, the CLI as a subprocess: a fake gateway that answers
 * health and resolve, and a "claude" that prints the environment and argv it
 * was handed, then exits 7.
 *
 * What these keep honest: the launch is routed as the cc-shim fragment would
 * route it (base URL, doorman, identity headers, `/tc-acct/<name>` for a pin,
 * no inherited credential), the program's args and exit code pass through,
 * `--` is optional, a gateway that does not answer is a refusal and never a
 * launch on claude's own login, and `env` prints exactly what `run` sets.
 */
import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import http from "node:http"
import { chmodSync, existsSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { basename, join } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

import { envName } from "../src/brand.ts"
import { command as gw } from "../src/command.ts"

const CLI = fileURLToPath(new URL("../src/cli.ts", import.meta.url))

/** A gateway that only answers what `run` asks: health, and resolve for "acme". */
async function fakeGateway(): Promise<{ port: number; asked: string[]; close: () => Promise<void> }> {
  const asked: string[] = []
  const srv = http.createServer((req, res) => {
    asked.push(req.url ?? "/")
    const url = new URL(req.url ?? "/", "http://x")
    if (url.pathname === "/_gateway/health") return res.writeHead(200, { "content-type": "application/json" }), res.end('{"pid":1}')
    if (url.pathname === "/_gateway/resolve")
      return url.searchParams.get("q") === "acme" ? (res.writeHead(200), res.end("work@acme.io\n")) : (res.writeHead(404), res.end(`no account matches "${url.searchParams.get("q")}"\n`))
    res.writeHead(404)
    res.end()
  })
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r))
  return { port: (srv.address() as { port: number }).port, asked, close: () => new Promise((r) => srv.close(() => r())) }
}

/** A claude stand-in: prints argv and env as JSON, then exits 7. */
function fakeClaude(dir: string): string {
  const bin = join(dir, "claude")
  writeFileSync(bin, `#!${process.execPath}\nconsole.log(JSON.stringify({ argv: process.argv.slice(2), env: process.env }))\nprocess.exit(7)\n`)
  chmodSync(bin, 0o755)
  return bin
}

function world(port: number): { dir: string; env: NodeJS.ProcessEnv } {
  const dir = mkdtempSync(join(tmpdir(), "gw-run-"))
  const config = join(dir, "gateway.json")
  writeFileSync(config, JSON.stringify({ host: "127.0.0.1", port, accountsFile: join(dir, "accounts.json") }))
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    HOME: dir,
    [envName("GATEWAY_CONFIG")]: config,
    [envName("GATEWAY_DATA_DIR")]: join(dir, "data"),
    CLAUDE_CODE_OAUTH_TOKEN: "a-real-token",
    ANTHROPIC_CUSTOM_HEADERS: "x-team: blue",
  }
  for (const k of ["GATEWAY_PORT", "GATEWAY_ACCOUNT"]) delete env[envName(k)]
  return { dir, env }
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

type Seen = { argv: string[]; env: Record<string, string | undefined> }

test("run: the program goes through the gateway as the fragment would send it; args and exit code pass through", async () => {
  const g = await fakeGateway()
  const { dir, env } = world(g.port)
  const claude = fakeClaude(dir)
  try {
    const r = await cli(["run", "--", claude, "-p", "hi", "--account", "theirs"], env, dir)
    assert.equal(r.code, 7, r.err)
    const seen = JSON.parse(r.out) as Seen
    assert.deepEqual(seen.argv, ["-p", "hi", "--account", "theirs"])
    assert.equal(seen.env.ANTHROPIC_BASE_URL, `http://127.0.0.1:${g.port}`)
    assert.equal(seen.env.ANTHROPIC_AUTH_TOKEN, "gateway-doorman")
    assert.equal(seen.env.CLAUDE_CODE_OAUTH_TOKEN, undefined)
    assert.equal(seen.env.CC_SHIM_DISABLE, "1")
    assert.equal(seen.env.ENABLE_TOOL_SEARCH, "true")
    assert.equal(seen.env.ANTHROPIC_CUSTOM_HEADERS, `x-team: blue\nx-brain-origin: cli\nx-brain-project: ${basename(dir)}`)
    // Without `--` (the kit's click eats it), and the program's own `--account` is still its own.
    const bare = await cli(["run", claude, "--account", "theirs"], env, dir)
    assert.equal(bare.code, 7, bare.err)
    assert.deepEqual((JSON.parse(bare.out) as Seen).argv, ["--account", "theirs"])
    assert.ok(!g.asked.some((u) => u.startsWith("/_gateway/resolve")), "nobody asked for a pin")
  } finally {
    await g.close()
  }
})

test("run --account: the gateway names the account, and the launch is pinned to it by path", async () => {
  const g = await fakeGateway()
  const { dir, env } = world(g.port)
  const claude = fakeClaude(dir)
  try {
    const r = await cli(["run", "--account", "acme", claude], env, dir)
    assert.equal(r.code, 7, r.err)
    assert.equal((JSON.parse(r.out) as Seen).env.ANTHROPIC_BASE_URL, `http://127.0.0.1:${g.port}/tc-acct/work@acme.io`)
    // A name it cannot route: refused, nothing launched.
    const none = await cli(["run", "--account", "nobody", "--", claude], env, dir)
    assert.equal(none.code, 1)
    assert.equal(none.out, "")
    assert.match(none.err, /--account nobody: no account matches "nobody"/)
  } finally {
    await g.close()
  }
})

test("run: a gateway that does not answer is a refusal with the verb that fixes it — never claude on its own login", async () => {
  const g = await fakeGateway()
  const port = g.port
  await g.close()
  const { dir, env } = world(port)
  const marker = join(dir, "ran")
  const claude = join(dir, "claude")
  writeFileSync(claude, `#!/bin/sh\ntouch ${marker}\n`)
  chmodSync(claude, 0o755)
  const r = await cli(["run", "--", claude], env, dir)
  assert.equal(r.code, 1)
  assert.equal(r.err.trim().split("\n").length, 1)
  assert.match(r.err, new RegExp(`not answering on 127\\.0\\.0\\.1:${port} — \`${gw("enable")}\` turns it on`))
  assert.ok(!existsSync(marker), "the program ran")
  const nothing = await cli(["run"], env, dir)
  assert.equal(nothing.code, 1)
  assert.match(nothing.err, /name the program/)
})

test("env prints exactly what run sets — the same function, so the two cannot drift", async () => {
  const g = await fakeGateway()
  const { dir, env } = world(g.port)
  const claude = fakeClaude(dir)
  try {
    const printed = await cli(["env", "--account", "acme"], env, dir)
    assert.equal(printed.code, 0, printed.err)
    const ran = await cli(["run", "--account", "acme", claude], env, dir)
    const seen = (JSON.parse(ran.out) as Seen).env
    // Evaluated by sh over the same starting environment, the lines give run's environment.
    const shell = await new Promise<string>((resolve) => {
      const c = spawn("/bin/sh", ["-c", `${printed.out}\nexec "${process.execPath}" -e 'console.log(JSON.stringify(process.env))'`], { env, cwd: dir })
      let out = ""
      c.stdout.on("data", (d) => (out += d))
      c.on("exit", () => resolve(out))
    })
    const evaluated = JSON.parse(shell) as Record<string, string>
    for (const k of ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_CUSTOM_HEADERS", "CC_SHIM_DISABLE", "ENABLE_TOOL_SEARCH", "CLAUDE_CODE_GATEWAY_HINT_HEADERS"])
      assert.equal(evaluated[k], seen[k], k)
    assert.equal(evaluated.CLAUDE_CODE_OAUTH_TOKEN, undefined)
    assert.match(printed.out, /^unset .*CLAUDE_CODE_OAUTH_TOKEN/m)
  } finally {
    await g.close()
  }
})
