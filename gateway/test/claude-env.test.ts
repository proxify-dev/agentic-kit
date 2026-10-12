/**
 * What rides with the routing, and what can override it.
 *
 * The routing fragment fills Claude Code's gateway variables only when the launch
 * environment leaves them unset (a user's export wins), and `shadows` names every
 * settings.json `env` value that wins over the routing — Claude Code applies those
 * over the launch environment — with what it does to a session. No real HOME, no
 * real settings: every file here is scratch.
 */
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"

import { CLAUDE_ENV, claudeEnvDefaults } from "../src/claude-env.ts"
import { loadConfig } from "../src/config.ts"
import { shadows } from "../src/shadows.ts"
import { renderFragment } from "../src/shim.ts"

const GW = "http://127.0.0.1:4747"

/** Run the fragment's defaults lines in node with `env`, print what they leave. */
function fragmentLeaves(env: Record<string, string>): Record<string, string> {
  const lines = renderFragment(loadConfig({ port: 4747 }))
    .split("\n")
    .filter((l) => l.startsWith("const defaults = ") || l.startsWith("for (const [k, v] of Object.entries(defaults))"))
  assert.equal(lines.length, 2, "the fragment fills its defaults in two lines")
  const names = Object.keys(CLAUDE_ENV)
  const script = `const env = process.env\n${lines.join("\n")}\nconsole.log(JSON.stringify(Object.fromEntries(${JSON.stringify(names)}.map((n) => [n, env[n]]))))`
  const r = spawnSync(process.execPath, ["-e", script], { env: { PATH: process.env.PATH ?? "", ...env }, encoding: "utf8" })
  return JSON.parse(r.stdout) as Record<string, string>
}

test("the routing fills the gateway variables only where the launch leaves them unset", () => {
  assert.deepEqual(fragmentLeaves({}), { ENABLE_TOOL_SEARCH: "true", CLAUDE_CODE_GATEWAY_HINT_HEADERS: "1" })
  assert.deepEqual(fragmentLeaves({ ENABLE_TOOL_SEARCH: "auto:5" }), { ENABLE_TOOL_SEARCH: "auto:5", CLAUDE_CODE_GATEWAY_HINT_HEADERS: "1" })
  assert.deepEqual(claudeEnvDefaults({ CLAUDE_CODE_GATEWAY_HINT_HEADERS: "0" }), { ENABLE_TOOL_SEARCH: "true" })
})

test("shadows: a settings value that beats the routing is named, with what it does", () => {
  const home = mkdtempSync(join(tmpdir(), "gw-shadow-"))
  const repo = join(home, "code", "repo")
  mkdirSync(join(repo, ".git"), { recursive: true })
  mkdirSync(join(repo, ".claude"), { recursive: true })
  mkdirSync(join(home, ".claude"), { recursive: true })
  writeFileSync(
    join(home, ".claude", "settings.json"),
    JSON.stringify({ env: { ENABLE_TOOL_SEARCH: "false", ANTHROPIC_API_KEY: "sk-secret", SOMETHING_ELSE: "1" } }),
  )
  writeFileSync(join(repo, ".claude", "settings.local.json"), JSON.stringify({ env: { ANTHROPIC_BASE_URL: "http://127.0.0.1:8787" } }))

  const found = shadows(GW, { home, cwd: join(repo), env: { CLAUDE_CODE_GATEWAY_HINT_HEADERS: "0" } })
  const by = (name: string) => found.find((s) => s.name === name)

  assert.equal(by("ANTHROPIC_BASE_URL")?.scope, "local")
  assert.match(by("ANTHROPIC_BASE_URL")?.effect ?? "", /not through the gateway/)
  assert.equal(by("ENABLE_TOOL_SEARCH")?.scope, "user")
  assert.match(by("ENABLE_TOOL_SEARCH")?.effect ?? "", /tool search off/)
  assert.equal(by("ANTHROPIC_API_KEY")?.value, "(set)", "a credential is named, never printed")
  assert.equal(by("CLAUDE_CODE_GATEWAY_HINT_HEADERS")?.scope, "shell")
  assert.equal(by("SOMETHING_ELSE"), undefined, "only what touches the gateway")
  assert.deepEqual(found.map((s) => s.scope), ["local", "user", "user", "shell"], "highest precedence first")
})

test("shadows: nothing set, nothing reported — and the gateway's own values are no conflict", () => {
  const home = mkdtempSync(join(tmpdir(), "gw-shadow-"))
  mkdirSync(join(home, ".claude"), { recursive: true })
  writeFileSync(join(home, ".claude", "settings.json"), JSON.stringify({ env: { ENABLE_TOOL_SEARCH: "true" } }))
  assert.deepEqual(shadows(GW, { home, cwd: home, env: { ENABLE_TOOL_SEARCH: "true" } }), [])
})
