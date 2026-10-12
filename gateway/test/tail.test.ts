/**
 * `tail --session`: one session's rows, found the way an agent asks for them — the id from
 * CLAUDE_CODE_SESSION_ID, rows spread over UTC day files. The CLI as a subprocess over a
 * scratch data dir; nothing of the machine's trace is read.
 */
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import { cmd as brandCmd, envName } from "../src/brand.ts"

const CLI = fileURLToPath(new URL("../src/cli.ts", import.meta.url))

const row = (ts: string, session: string, requestClass = "main", agentType: string | null = null) =>
  JSON.stringify({
    v: 1, id: ts, ts, method: "POST", path: "/v1/messages", model: "claude-test", status: 200, ttfbMs: 5,
    usage: { input: 1, output: 2, cacheRead: 3, cacheCreation: 4 },
    shape: { messages: 1, tools: 0, bytesIn: 2048 },
    identity: { agent: null, session }, hints: { requestClass, agentType }, policy: [], error: null,
  })

function world(): NodeJS.ProcessEnv {
  const dir = mkdtempSync(join(tmpdir(), "gw-tail-"))
  const trace = join(dir, "data", "trace")
  mkdirSync(trace, { recursive: true })
  writeFileSync(join(trace, "2026-09-20.ndjson"), `${row("2026-09-20T10:00:00.000Z", "sess-a")}\n`)
  writeFileSync(join(trace, "2026-09-21.ndjson"), `${row("2026-09-21T10:00:00.000Z", "sess-b")}\n`)
  writeFileSync(join(trace, "2026-09-22.ndjson"), `${row("2026-09-22T23:59:00.000Z", "sess-a")}\n${row("2026-09-22T23:59:30.000Z", "sess-b")}\n`)
  writeFileSync(
    join(trace, "2026-09-23.ndjson"),
    `${row("2026-09-23T00:01:00.000Z", "sess-a", "subagent", "Explore")}\n${row("2026-09-23T00:02:00.000Z", "sess-b")}\n`,
  )
  const config = join(dir, "gateway.json")
  writeFileSync(config, JSON.stringify({ capture: "meta", policy: [], accountsFile: join(dir, "accounts.json") }))
  const env: NodeJS.ProcessEnv = { ...process.env, HOME: dir, [envName("GATEWAY_CONFIG")]: config, [envName("GATEWAY_DATA_DIR")]: join(dir, "data") }
  delete env.CLAUDE_CODE_SESSION_ID
  return env
}

const tail = (env: NodeJS.ProcessEnv, ...args: string[]) =>
  spawnSync(process.execPath, ["--experimental-strip-types", "--no-warnings", CLI, "tail", ...args], { env, encoding: "utf8" })

test("--session alone reads the calling session's id, across day files, subagents included", () => {
  const env = world()
  const r = tail({ ...env, CLAUDE_CODE_SESSION_ID: "sess-a" }, "--session")
  assert.equal(r.status, 0, r.stderr)
  const calls = r.stdout.split("\n").filter((l) => l.includes("sess:sess-a"))
  // 2026-09-20 is the fourth file back: outside the three a session reads.
  assert.deepEqual(calls.map((l) => l.slice(0, 8)), ["23:59:00", "00:01:00"])
  assert.match(calls[1], /subagent:Explore/)
  assert.doesNotMatch(r.stdout, /sess-b/)
  assert.match(r.stdout, /2 calls of session sess-a/)
  assert.doesNotMatch(r.stdout, /tracer inspect/)
  assert.match(r.stdout, /docs\/trace\.md/)
})

test("--session points at the kit's tracer only inside the kit — standalone there is just the format", () => {
  const r = tail({ ...world(), [envName("GATEWAY_COMMAND")]: brandCmd("gateway") }, "--session", "sess-a")
  assert.equal(r.status, 0, r.stderr)
  assert.match(r.stdout, new RegExp(`${brandCmd("tracer inspect")} sess-a · the format: .*docs/trace\\.md`))
})

test("--session ID names another session; --n still bounds it", () => {
  const r = tail(world(), "--session", "sess-b", "--n", "1")
  assert.equal(r.status, 0, r.stderr)
  assert.deepEqual(r.stdout.split("\n").filter((l) => l.includes("sess:")).map((l) => l.slice(0, 8)), ["00:02:00"])
})

test("a session with no rows says why it may be missing; no id at all is refused", () => {
  const env = world()
  const none = tail(env, "--session", "sess-z")
  assert.equal(none.status, 0)
  assert.match(none.stdout, /no calls from session sess-z .*CC_SHIM_DISABLE/)
  const bare = tail(env, "--session")
  assert.equal(bare.status, 1)
  assert.match(bare.stderr, /CLAUDE_CODE_SESSION_ID is not set/)
})

test("plain tail is unchanged: the last day's file only", () => {
  const r = tail(world())
  assert.equal(r.status, 0, r.stderr)
  assert.deepEqual(r.stdout.trim().split("\n").map((l) => l.slice(0, 8)), ["00:01:00", "00:02:00"])
})
