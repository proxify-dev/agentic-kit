// The platform seam (scripts/seam/platform.ts, stamped into src/brand.ts): one behaviour per OS.
// Windows is simulated by overriding process.platform: the seam reads env vars and joins with node:path.
import { test } from "node:test"
import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { homedir } from "node:os"
import { join } from "node:path"
import {
  claudeHome, claudeJson, configDir, dataDir, stateDir, posix, slug, projectDir, pidAlive, isWindows,
} from "../src/brand.ts"

const KEYS = ["CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA"]

function withEnv(env: Record<string, string>, platform: NodeJS.Platform | null, fn: () => void) {
  const saved = Object.fromEntries(KEYS.map((k) => [k, process.env[k]]))
  const real = process.platform
  for (const k of KEYS) delete process.env[k]
  Object.assign(process.env, env)
  if (platform) Object.defineProperty(process, "platform", { value: platform })
  try {
    fn()
  } finally {
    Object.defineProperty(process, "platform", { value: real })
    for (const k of KEYS) saved[k] === undefined ? delete process.env[k] : (process.env[k] = saved[k])
  }
}

test("claude home defaults to ~/.claude and honours CLAUDE_CONFIG_DIR", () => {
  withEnv({}, null, () => {
    assert.equal(claudeHome(), join(homedir(), ".claude"))
    assert.equal(claudeJson(), join(homedir(), ".claude.json"))
  })
  withEnv({ CLAUDE_CONFIG_DIR: "/x/cfg" }, null, () => {
    assert.equal(claudeHome(), "/x/cfg")
    assert.equal(claudeJson(), join("/x/cfg", ".claude.json"))
  })
  withEnv({ CLAUDE_CONFIG_DIR: "~/elsewhere" }, null, () => assert.equal(claudeHome(), join(homedir(), "elsewhere")))
})

test("posix dirs follow XDG and ignore a relative value", () => {
  withEnv({}, "linux", () => {
    assert.equal(configDir(), join(homedir(), ".config"))
    assert.equal(dataDir(), join(homedir(), ".local", "share"))
    assert.equal(stateDir(), join(homedir(), ".local", "state"))
  })
  withEnv({ XDG_CONFIG_HOME: "/c", XDG_DATA_HOME: "relative" }, "linux", () => {
    assert.equal(configDir(), "/c")
    assert.equal(dataDir(), join(homedir(), ".local", "share"))
  })
})

test("windows dirs use APPDATA and LOCALAPPDATA", () => {
  withEnv({}, "win32", () => {
    assert.ok(isWindows())
    assert.equal(configDir(), join(homedir(), "AppData", "Roaming"))
    assert.equal(dataDir(), join(homedir(), "AppData", "Local"))
  })
  withEnv({ APPDATA: "R", LOCALAPPDATA: "L", XDG_CONFIG_HOME: "/ignored" }, "win32", () => {
    assert.equal(configDir(), "R")
    assert.equal(dataDir(), "L")
    assert.equal(stateDir(), "L")
  })
})

test("slug is Claude Code's rule", () => {
  assert.equal(slug("/Users/x/proj"), "-Users-x-proj")
  assert.equal(slug("/Users/x/my.proj"), "-Users-x-my-proj")
  assert.equal(slug("/Users/x/my proj_2"), "-Users-x-my-proj-2")
  assert.equal(slug("C:\\Users\\x\\proj"), "C--Users-x-proj")
  assert.equal(slug("\\\\server\\share\\p"), "--server-share-p")
  assert.equal(slug("/tmp/é"), "-tmp--")
  assert.equal(slug("/tmp/\u{1F600}"), "-tmp---") // an emoji is two UTF-16 units
  withEnv({ CLAUDE_CONFIG_DIR: "/cfg" }, null, () => assert.equal(projectDir("/a/b"), join("/cfg", "projects", "-a-b")))
})

test("posix() flips drive paths, and every backslash on windows", () => {
  withEnv({}, "linux", () => {
    assert.equal(posix("C:\\Users\\x\\a.md"), "C:/Users/x/a.md")
    assert.equal(posix("/usr/local/a\\b"), "/usr/local/a\\b")
  })
  withEnv({}, "win32", () => assert.equal(posix("a\\b\\c"), "a/b/c"))
  // Git Bash spells C:\x as /c/x; on Linux /c is just a folder
  withEnv({}, "win32", () => {
    assert.equal(posix("/c/Users/me/p"), "C:/Users/me/p")
    assert.equal(posix("/cygdrive/d/w"), "D:/w")
    assert.equal(posix("/cool/x"), "/cool/x")
  })
  withEnv({}, "linux", () => assert.equal(posix("/c/Users/me"), "/c/Users/me"))
})

test("pidAlive probes without killing", async () => {
  assert.ok(pidAlive(process.pid))
  assert.ok(!pidAlive(0))
  assert.ok(!pidAlive(-5))
  const child = spawn(process.execPath, ["-e", ""])
  await new Promise((r) => child.on("exit", r))
  assert.ok(!pidAlive(child.pid!))
})
