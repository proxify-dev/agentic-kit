/**
 * The fixes from the cross-platform review, each with the OS it bites on
 * simulated: a fake PATHEXT table and file set for Windows lookups, a fake
 * `run` for loginctl, fake `script` answers for busybox, a fake rename that
 * fails the way Windows does.
 */
import assert from "node:assert/strict"
import test from "node:test"

import { commandNames, quoteCmdArg, seeThroughCmd, spawnable, which, resolveProgram } from "../src/procs.ts"
import { carriedEnv, CARRIED_ENV } from "../src/service.ts"
import type { Run } from "../src/service.ts"
import { renderPlist } from "../src/service-launchd.ts"
import { currentUser, lingerProblem, renderUnit, systemd } from "../src/service-systemd.ts"
import { renderTask, taskArgv, readTaskArgv } from "../src/service-schtasks.ts"
import { scriptFlavor, scriptInvocation } from "../src/xray.ts"
import { renameRetrySync } from "../src/fsperm.ts"
import { browserCommands } from "../src/engine/oauth.js"
import { renameRetry } from "../src/engine/config.js"
import { envName } from "../src/brand.ts"
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

// ── 4. Windows: which() and a spawn plan ────────────────────────────────────

test("windows which: never the bare name, .exe before .cmd in one dir, the first dir wins", () => {
  const env = { Path: "C:\\npm;C:\\tools", PATHEXT: ".COM;.EXE;.BAT;.CMD" }
  const disk = (files: string[]) => ({ plat: "win32" as const, exists: (f: string) => files.includes(f) })
  // npm's folder: the extensionless sh script and claude.cmd. The bare name is never picked.
  assert.equal(which("claude", env, disk(["C:\\npm\\claude", "C:\\npm\\claude.cmd"])), "C:\\npm\\claude.cmd")
  // PATHEXT listing .CMD first still gets the .exe beside it.
  assert.equal(which("ak", { ...env, PATHEXT: ".CMD;.BAT;.EXE" }, disk(["C:\\npm\\ak.cmd", "C:\\npm\\ak.exe"])), "C:\\npm\\ak.exe")
  // An earlier directory's .cmd beats a later .exe — PATH order, as a shell has it.
  assert.equal(which("claude", env, disk(["C:\\npm\\claude.cmd", "C:\\tools\\claude.exe"])), "C:\\npm\\claude.cmd")
  assert.equal(which("claude", env, disk([])), null)
  assert.deepEqual(commandNames("x", { PATHEXT: ".CMD;.PY;.EXE" }, "win32"), ["x.exe", "x.py", "x.cmd"])
  assert.deepEqual(commandNames("x.cmd", {}, "win32"), ["x.cmd"])
  assert.deepEqual(commandNames("x", {}, "linux"), ["x"])
  // POSIX: spawn's own lookup, untouched.
  assert.equal(resolveProgram("claude", {}, "linux"), "claude")
  assert.equal(resolveProgram("C:\\x\\claude.cmd", {}, "win32"), "C:\\x\\claude.cmd")
})

test("windows spawn plan: an exe as is, an opaque .cmd through cmd.exe quoted for it, a line break refused", () => {
  assert.deepEqual(spawnable("C:\\c\\claude.exe", ["-p", "a b"], "win32"), { command: "C:\\c\\claude.exe", args: ["-p", "a b"], options: {} })
  assert.deepEqual(spawnable("/usr/bin/claude", ["x"], "linux"), { command: "/usr/bin/claude", args: ["x"], options: {} })
  const plan = spawnable("C:\\nowhere\\claude.cmd", ["-p", "a&b"], "win32", () => "@echo off\r\nsomething %*")
  assert.deepEqual(plan.args.slice(0, 3), ["/d", "/s", "/c"])
  assert.equal(plan.args[3], `"C:\\nowhere\\claude.cmd ${quoteCmdArg("-p")} ${quoteCmdArg("a&b")}"`)
  assert.deepEqual(plan.options, { windowsVerbatimArguments: true })
  assert.throws(() => spawnable("C:\\nowhere\\claude.cmd", ["line1\nline2"], "win32", () => ""), /line break/)
  // An npm shim is seen through to what it runs.
  const npm = 'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\node_modules\\@anthropic-ai\\claude-code\\cli.js" %*'
  const seen = seeThroughCmd("C:\\npm\\claude.cmd", npm, () => false)
  assert.deepEqual(seen, { command: process.execPath, args: ["C:\\npm\\node_modules\\@anthropic-ai\\claude-code\\cli.js"] })
})

// ── 3. the unit carries the shell's XDG / config env ────────────────────────

test("service env: XDG, CLAUDE_CONFIG_DIR and the config overrides are carried when set, never the keeper's own", () => {
  const src = {
    XDG_CONFIG_HOME: "/x/cfg",
    XDG_DATA_HOME: "/x/data",
    XDG_STATE_HOME: "",
    CLAUDE_CONFIG_DIR: "/x/claude",
    [envName("GATEWAY_ACCOUNTS_FILE")]: "rel/accounts.json",
    [envName("GATEWAY_PORT")]: "4848",
    [envName("GATEWAY_RELEASE")]: "r1",
    [envName("GATEWAY_KEEPER")]: "42",
    HOME: "/h",
  }
  const env = carriedEnv(src)
  assert.equal(env.XDG_CONFIG_HOME, "/x/cfg")
  assert.equal(env.XDG_DATA_HOME, "/x/data")
  assert.ok(!("XDG_STATE_HOME" in env), "an empty value is not set")
  assert.equal(env.CLAUDE_CONFIG_DIR, "/x/claude")
  assert.equal(env[envName("GATEWAY_PORT")], "4848")
  assert.equal(env[envName("GATEWAY_ACCOUNTS_FILE")], join(process.cwd(), "rel/accounts.json"), "a relative file is made absolute")
  assert.ok(!(envName("GATEWAY_RELEASE") in env) && !(envName("GATEWAY_KEEPER") in env))
  assert.ok(!("HOME" in env))
  assert.deepEqual(carriedEnv({}), {})
  assert.ok(CARRIED_ENV.includes("XDG_STATE_HOME"))

  // Every backend's rendered unit has them.
  const unit = { program: ["/n/node", "/d/keeper.mts"], env: { HOME: "/h", ...env }, log: "/d/gw.log" }
  const plist = renderPlist({ label: "com.ak.gateway", ...unit })
  assert.ok(plist.includes("<key>XDG_CONFIG_HOME</key><string>/x/cfg</string>"))
  const service = renderUnit({ description: "gw", ...unit })
  assert.ok(service.split("\n").includes('Environment="XDG_CONFIG_HOME=/x/cfg"'))
  assert.ok(service.split("\n").includes('Environment="CLAUDE_CONFIG_DIR=/x/claude"'))
  const argv = readTaskArgv(renderTask({ description: "gw", user: "me", argv: taskArgv(unit), workDir: "C:\\d" })) ?? []
  assert.ok(argv.includes("XDG_DATA_HOME=/x/data") && argv[argv.indexOf("XDG_DATA_HOME=/x/data") - 1] === "--env")
})

// ── 8. linger ───────────────────────────────────────────────────────────────

function fakeRun(answers: [string, { ok: boolean; out: string }][]): { run: Run; calls: string[] } {
  const calls: string[] = []
  return {
    calls,
    run: (cmd, args) => {
      const line = [cmd, ...args].join(" ")
      calls.push(line)
      return answers.find(([p]) => line.startsWith(p))?.[1] ?? { ok: false, out: "" }
    },
  }
}

test("systemd: a refused enable-linger warns with the sudo line; doctor repeats it; linger already on asks nothing", () => {
  const unit = { program: ["/n/node", "/d/keeper.mts"], env: {}, log: "/d/gw.log" }
  const down = { backend: "systemd" as const, label: "x", file: "x", installed: false, loaded: false, pid: null, program: null }
  const refused = fakeRun([
    ["systemctl --user show", { ok: true, out: "ActiveState=active\nMainPID=9\n" }],
    ["systemctl", { ok: true, out: "" }],
    ["loginctl show-user", { ok: true, out: "Linger=no\n" }],
    ["loginctl enable-linger", { ok: false, out: "Could not enable linger: Access denied" }],
  ])
  const r = systemd({ unit: "ak-gateway", run: refused.run, dir: mkdtempSync(join(tmpdir(), "gw-linger-")), user: "ana" }).install(unit, down)
  assert.ok(r.ok, "the unit is up: install succeeded")
  assert.match(r.warn ?? "", /linger is off for ana \(loginctl: Could not enable linger: Access denied\)/)
  assert.match(r.warn ?? "", /stops when you log out/)
  assert.match(r.warn ?? "", /sudo loginctl enable-linger ana/)
  assert.match(lingerProblem(refused.run, "ana") ?? "", /sudo loginctl enable-linger ana/)

  const on = fakeRun([
    ["systemctl --user show", { ok: true, out: "ActiveState=active\nMainPID=9\n" }],
    ["systemctl", { ok: true, out: "" }],
    ["loginctl show-user", { ok: true, out: "Linger=yes\n" }],
  ])
  const ok = systemd({ unit: "ak-gateway", run: on.run, dir: mkdtempSync(join(tmpdir(), "gw-linger-")), user: "ana" }).install(unit, down)
  assert.equal(ok.warn, undefined)
  assert.ok(!on.calls.some((c) => c.startsWith("loginctl enable-linger")))
  assert.equal(lingerProblem(on.run, "ana"), null)
  assert.equal(lingerProblem(fakeRun([]).run, "ana"), null, "no logind to ask: nothing said")

  assert.equal(currentUser({ USER: "", LOGNAME: "lg" }), "lg", "an empty USER is not a name")
  assert.equal(currentUser({ USER: "u" }), "u")
  assert.ok(currentUser({}).length > 0)
  assert.ok(!renderUnit({ description: "gw", ...unit }).includes("network-online.target"), "a user unit orders on no system target")
})

// ── 9. busybox script ───────────────────────────────────────────────────────

test("xray: script is told apart — BSD on macOS, busybox by its applet or its --version, util-linux otherwise, none said", () => {
  assert.equal(scriptFlavor({ mac: true }), "bsd")
  assert.equal(scriptFlavor({ mac: false, path: null }), null)
  assert.equal(scriptFlavor({ mac: false, path: "/usr/bin/script", real: () => "/bin/busybox", version: () => "" }), "busybox")
  assert.equal(scriptFlavor({ mac: false, path: "/usr/bin/script", real: (p) => p, version: () => "BusyBox v1.36.1 multi-call binary." }), "busybox")
  assert.equal(scriptFlavor({ mac: false, path: "/usr/bin/script", real: (p) => p, version: () => "script from util-linux 2.39.3" }), "util-linux")
  assert.equal(scriptInvocation("util-linux", "'S'"), 'script -qec "$XRAY_COMMAND" /dev/null')
  assert.equal(scriptInvocation("busybox", "'S'"), 'script -q -c "$XRAY_COMMAND" /dev/null', "busybox has -c but no -e")
  assert.equal(scriptInvocation("bsd", "'S'"), `script -q /dev/null /bin/sh -c 'S' "$@"`)
})

// ── 6. the browser ──────────────────────────────────────────────────────────

test("oauth: the browser opens without a shell — rundll32 on Windows, wslview then xdg-open on Linux", () => {
  const url = "https://claude.ai/oauth/authorize?code=true&client_id=x&state=y"
  assert.deepEqual(browserCommands(url, "win32"), [["rundll32", ["url.dll,FileProtocolHandler", url]]])
  assert.deepEqual(browserCommands(url, "darwin"), [["open", [url]]])
  assert.deepEqual(browserCommands(url, "linux"), [["wslview", [url]], ["xdg-open", [url]]])
})

// ── 7. rename over an open file ─────────────────────────────────────────────

test("rename: retried on Windows EPERM/EBUSY, at once elsewhere", async () => {
  const flaky = (fails: number, code = "EPERM") => {
    let n = 0
    return {
      calls: () => n,
      sync: (_a: string, _b: string) => {
        if (n++ < fails) throw Object.assign(new Error(code), { code })
      },
      async: async (_a: string, _b: string) => {
        if (n++ < fails) throw Object.assign(new Error(code), { code })
      },
    }
  }
  let f = flaky(3)
  renameRetrySync("a", "b", "win32", f.sync)
  assert.equal(f.calls(), 4)
  f = flaky(1)
  assert.throws(() => renameRetrySync("a", "b", "linux", f.sync), /EPERM/)
  assert.equal(f.calls(), 1, "POSIX: no retry")
  f = flaky(10)
  assert.throws(() => renameRetrySync("a", "b", "win32", f.sync), /EPERM/)
  assert.equal(f.calls(), 6, "gives up after five retries")
  f = flaky(1, "ENOENT")
  assert.throws(() => renameRetrySync("a", "b", "win32", f.sync), /ENOENT/)

  f = flaky(2, "EBUSY")
  await renameRetry("a", "b", "win32", f.async)
  assert.equal(f.calls(), 3)
  f = flaky(1)
  await assert.rejects(renameRetry("a", "b", "linux", f.async), /EPERM/)
})
