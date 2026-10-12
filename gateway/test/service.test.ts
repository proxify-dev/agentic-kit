/**
 * The service behind one interface (service.ts) and its three backends —
 * launchd, systemd, Task Scheduler — driven with a fake `run`, so every
 * backend is checked on every OS: what file it writes, which commands it
 * issues in which order, and how it reads a manager's answers back. The per-OS
 * process helpers (procs.ts) are fed each OS's real output format.
 *
 * What this cannot show: that a real systemd or Task Scheduler accepts the
 * file and keeps the keeper up. That is the per-OS lifecycle run in CI.
 */
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"

import { backendFor, unitName } from "../src/service.ts"
import type { Run, UnitState } from "../src/service.ts"
import { launchd, printedPid, readProgram, renderPlist } from "../src/service-launchd.ts"
import { execWord, isWsl, parseShow, readExecStart, renderUnit, systemd, systemdProblem } from "../src/service-systemd.ts"
import { parseQuery, readTaskArgv, renderTask, schtasks, taskArgv, winQuote, winSplit } from "../src/service-schtasks.ts"
import { listenInodes, netstatListeners, ppidOfStat } from "../src/procs.ts"
import { icaclsArgs, ours } from "../src/fsperm.ts"
import { lineDiff } from "../src/xray.ts"
import { managedSettings } from "../src/shadows.ts"

/** A `run` that records every call and answers from a table (first matching prefix wins). */
function fakeRun(answers: [string, { ok: boolean; out: string }][] = []): { run: Run; calls: string[] } {
  const calls: string[] = []
  const run: Run = (cmd, args) => {
    const line = [cmd, ...args].join(" ")
    calls.push(line)
    return answers.find(([p]) => line.startsWith(p))?.[1] ?? { ok: false, out: "" }
  }
  return { run, calls }
}

const down = (backend: UnitState["backend"]): UnitState => ({ backend, label: "x", file: "x", installed: false, loaded: false, pid: null, program: null })
const unit = { program: ["/n/node", "--experimental-strip-types", "--no-warnings", "/d/keeper.mts"], env: { HOME: "/h", PATH: "/n:/usr/bin" }, log: "/d/gateway.log" }

test("the backend follows the OS, and the unit name comes from the label", () => {
  assert.equal(backendFor("darwin"), "launchd")
  assert.equal(backendFor("linux"), "systemd")
  assert.equal(backendFor("win32"), "schtasks")
  assert.equal(unitName("com.ak.gateway"), "ak-gateway")
  assert.equal(unitName("com.test.gw.throwaway"), "test-gw-throwaway")
})

// ── launchd ─────────────────────────────────────────────────────────────────

test("launchd: the plist keeps the keeper alive, logs, and reads back what it runs", () => {
  const text = renderPlist({ label: "com.ak.gateway", program: ["/n/node", "/a & b/keeper.mts"], log: "/d/gw.log", env: { PATH: "/n" } })
  for (const want of ["<key>Label</key><string>com.ak.gateway</string>", "<key>RunAtLoad</key><true/>", "<key>KeepAlive</key><true/>",
    "<key>ThrottleInterval</key><integer>5</integer>", "<key>StandardOutPath</key><string>/d/gw.log</string>", "<key>PATH</key><string>/n</string>",
    "<string>/a &amp; b/keeper.mts</string>"])
    assert.ok(text.includes(want), want)
  assert.deepEqual(readProgram(text), ["/n/node", "/a & b/keeper.mts"])
  assert.equal(printedPid("gui/501/com.ak.gateway = {\n\tstate = running\n\tpid = 4321\n}"), 4321)
  assert.equal(printedPid("state = not running"), null)
})

test("launchd: install writes the plist, enables, bootstraps; a loaded unit is booted out first", () => {
  const home = mkdtempSync(join(tmpdir(), "gw-launchd-"))
  const { run, calls } = fakeRun([["launchctl bootstrap", { ok: true, out: "" }]])
  const mgr = launchd({ label: "com.test.gw", home, run, uid: 501 })
  const r = mgr.install(unit, down("launchd"))
  assert.ok(r.ok, r.detail)
  assert.ok(existsSync(join(home, "Library", "LaunchAgents", "com.test.gw.plist")))
  assert.deepEqual(calls.filter((c) => !c.includes(" print ")), ["launchctl enable gui/501/com.test.gw", "launchctl bootstrap gui/501 " + join(home, "Library", "LaunchAgents", "com.test.gw.plist")])
  assert.deepEqual(mgr.state().program, unit.program)

  const again = fakeRun([["launchctl bootstrap", { ok: true, out: "" }]])
  const r2 = launchd({ label: "com.test.gw", home, run: again.run, uid: 501 }).install(unit, { ...down("launchd"), loaded: true, pid: null })
  assert.ok(r2.ok)
  assert.ok(again.calls.indexOf("launchctl bootout gui/501/com.test.gw") < again.calls.findIndex((c) => c.startsWith("launchctl bootstrap")))
})

// ── systemd ─────────────────────────────────────────────────────────────────

test("systemd: the unit restarts always, every 5 s, drains through the keeper and appends to the log", () => {
  const text = renderUnit({ description: "agentic kit gateway", program: ["/n/node", "/my dir/keeper.mts", "100%", "$HOME"], log: "/d/gw.log", env: { PATH: "/n:/usr/bin", HOME: "/h" } })
  for (const want of ["Restart=always", "RestartSec=5", "KillMode=mixed", "StandardOutput=append:/d/gw.log", "StandardError=append:/d/gw.log",
    'Environment="PATH=/n:/usr/bin"', 'Environment="HOME=/h"', "WantedBy=default.target", "Type=simple"])
    assert.ok(text.split("\n").includes(want), want)
  assert.ok(text.includes('ExecStart=/n/node "/my dir/keeper.mts" 100%% $$HOME'), text)
  assert.deepEqual(readExecStart(text), ["/n/node", "/my dir/keeper.mts", "100%", "$HOME"])
  assert.equal(execWord('a "q" b'), '"a \\"q\\" b"')
  assert.deepEqual(readExecStart(`ExecStart=${execWord('a "q" \\ b')}`), ['a "q" \\ b'])
})

test("systemd: show output says running and the pid; a failed unit is not loaded", () => {
  assert.deepEqual(parseShow("ActiveState=active\nMainPID=812\n"), { active: true, pid: 812 })
  assert.deepEqual(parseShow("MainPID=0\nActiveState=activating\n"), { active: true, pid: null })
  assert.deepEqual(parseShow("ActiveState=failed\nMainPID=0\n"), { active: false, pid: null })
  assert.deepEqual(parseShow("ActiveState=inactive\nMainPID=0"), { active: false, pid: null })
})

test("systemd: install reloads, stops a running unit, enables it --now; unload stops; uninstall disables", () => {
  const dir = mkdtempSync(join(tmpdir(), "gw-systemd-"))
  const { run, calls } = fakeRun([
    ["systemctl --user show", { ok: true, out: "ActiveState=active\nMainPID=77\n" }],
    ["systemctl", { ok: true, out: "" }],
  ])
  const mgr = systemd({ unit: "ak-gateway", run, dir })
  const r = mgr.install(unit, { ...down("systemd"), loaded: true, pid: 70 })
  assert.ok(r.ok, r.detail)
  assert.equal(r.detail, "ak-gateway.service active (pid 77)")
  const verbs = calls.filter((c) => c.startsWith("systemctl") && !c.includes(" show ")).map((c) => c.split(" ").slice(2).join(" "))
  assert.deepEqual(verbs, ["daemon-reload", "stop ak-gateway.service", "enable --now ak-gateway.service"])
  assert.ok(calls.some((c) => c.startsWith("loginctl enable-linger")), "linger asked for, best effort")
  assert.deepEqual(mgr.state().program, unit.program)
  assert.equal(mgr.state().file, join(dir, "ak-gateway.service"))

  calls.length = 0
  assert.equal(mgr.unload().detail, "ak-gateway.service stopped")
  assert.ok(calls.includes("systemctl --user stop ak-gateway.service"))
  calls.length = 0
  assert.ok(mgr.uninstall().ok)
  assert.ok(calls.includes("systemctl --user disable --now ak-gateway.service"))
  assert.ok(!existsSync(join(dir, "ak-gateway.service")))
})

test("systemd: a machine without a user session says so — WSL with the wsl.conf fix and the fallback", () => {
  const ok = fakeRun([["systemctl --user show-environment", { ok: true, out: "HOME=/h" }]])
  assert.equal(systemdProblem(ok.run, "Linux version 6.1"), null)
  const none = fakeRun([["systemctl", { ok: false, out: "System has not been booted with systemd as init system (PID 1). Can't operate." }]])
  const wsl = systemdProblem(none.run, "Linux version 5.15.153.1-microsoft-standard-WSL2")
  assert.match(wsl ?? "", /WSL without systemd/)
  assert.match(wsl ?? "", /\[boot\]\n {2}systemd=true/)
  assert.match(wsl ?? "", /wsl --shutdown/)
  assert.match(wsl ?? "", /gateway start --detach/)
  assert.match(systemdProblem(none.run, "Linux version 6.1") ?? "", /^no systemd user session/)
  assert.ok(isWsl("", { WSL_DISTRO_NAME: "Ubuntu" }))
  assert.ok(!isWsl("Linux version 6.1", {}))
})

// ── Task Scheduler ──────────────────────────────────────────────────────────

test("windows: arguments quote the way the C runtime reads them back", () => {
  for (const a of ["plain", "C:\\Program Files\\nodejs\\node.exe", 'say "hi"', "trailing\\", "C:\\a b\\", "", "x\\\\\"y"])
    assert.deepEqual(winSplit(winQuote(a)), [a], JSON.stringify(a))
  assert.deepEqual(winSplit(`--headless "C:\\a b\\node.exe" --log C:\\d\\gw.log`), ["--headless", "C:\\a b\\node.exe", "--log", "C:\\d\\gw.log"])
})

test("windows: the task starts at logon, restarts on failure, has no time limit and no window", () => {
  const argv = taskArgv({ program: ["C:\\Program Files\\nodejs\\node.exe", "--experimental-strip-types", "--no-warnings", "C:\\Users\\a b\\AppData\\Local\\ak\\gateway\\keeper.mts"], env: { AK_GATEWAY_CONFIG: "C:\\cfg.json" }, log: "C:\\d\\gateway.log" })
  const text = renderTask({ description: "agentic kit gateway", user: "PC\\alice", argv, workDir: "C:\\d" })
  for (const want of ["<LogonTrigger>", "<UserId>PC\\alice</UserId>", "<LogonType>InteractiveToken</LogonType>", "<Hidden>true</Hidden>",
    "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>", "<Interval>PT1M</Interval>", "<Count>999</Count>", "<Command>conhost.exe</Command>",
    "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
    "<WorkingDirectory>C:\\d</WorkingDirectory>", 'encoding="UTF-16"'])
    assert.ok(text.includes(want), want)
  assert.match(text, /<Arguments>--headless &quot;C:\\Program Files\\nodejs\\node.exe&quot; /)
  assert.deepEqual(readTaskArgv(text), argv)
  assert.deepEqual(argv.slice(-4), ["--log", "C:\\d\\gateway.log", "--env", "AK_GATEWAY_CONFIG=C:\\cfg.json"])
})

test("windows: schtasks /Query LIST is read for Status", () => {
  const out = "\r\nFolder: \\\r\nHostName:                             PC\r\nTaskName:                             \\ak-gateway\r\nNext Run Time:                        N/A\r\nStatus:                               Running\r\nLogon Mode:                           Interactive only\r\n"
  assert.deepEqual(parseQuery(out), { status: "Running" })
  assert.deepEqual(parseQuery("Ordner: \\\r\nStatus:       Wird ausgeführt\r\n"), { status: "Wird ausgeführt" })
  assert.deepEqual(parseQuery("ERROR: The system cannot find the file specified."), { status: null })
})

test("windows: install creates from UTF-16 XML and runs; the pid is the keeper's file; stop asks the keeper, then /End", () => {
  const dataDir = mkdtempSync(join(tmpdir(), "gw-schtasks-"))
  let running = false
  const { run, calls } = fakeRun([
    ["schtasks /Create", { ok: true, out: "SUCCESS" }],
    ["schtasks /Run", { ok: true, out: "SUCCESS" }],
    ["schtasks /End", { ok: true, out: "SUCCESS" }],
    ["schtasks /Delete", { ok: true, out: "SUCCESS" }],
  ])
  const live = new Set<number>()
  const query: Run = (cmd, args) => {
    if (args[0] === "/Query") {
      calls.push([cmd, ...args].join(" "))
      return running ? { ok: true, out: "Status: Running" } : { ok: false, out: "ERROR: not found" }
    }
    if (args[0] === "/Create") running = true
    if (args[0] === "/Run") writeFileSync(join(dataDir, "gateway.pid"), "4242"), live.add(4242)
    if (args[0] === "/End") live.clear()
    return run(cmd, args)
  }
  // The keeper hears a stop request and exits.
  const heardStop = () => existsSync(join(dataDir, "control")) && readFileSync(join(dataDir, "control"), "utf8").startsWith("stop")
  const mgr = schtasks({ task: "ak-gateway", dataDir: () => dataDir, run: query, alive: (p) => live.has(p) && !heardStop(), user: "PC\\alice" })
  const r = mgr.install({ ...unit, env: {} }, down("schtasks"))
  assert.ok(r.ok, r.detail)
  assert.equal(r.detail, "ak-gateway running (pid 4242)")
  const create = calls.find((c) => c.startsWith("schtasks /Create")) ?? ""
  assert.equal(create, `schtasks /Create /TN ak-gateway /XML ${join(dataDir, "ak-gateway.task.xml")} /F`)
  const raw = readFileSync(join(dataDir, "ak-gateway.task.xml"))
  assert.deepEqual([...raw.subarray(0, 2)], [0xff, 0xfe], "UTF-16LE with a BOM")
  const s = mgr.state()
  assert.deepEqual([s.installed, s.loaded, s.pid], [true, true, 4242])
  assert.ok(s.program?.includes("/d/keeper.mts"))

  calls.length = 0
  const u = mgr.unload()
  assert.ok(u.ok, u.detail)
  assert.match(readFileSync(join(dataDir, "control"), "utf8"), /^stop /)
  assert.ok(calls.includes("schtasks /End /TN ak-gateway"))
  assert.ok(mgr.uninstall().ok)
  assert.ok(calls.includes("schtasks /Delete /TN ak-gateway /F"))
  assert.ok(!existsSync(join(dataDir, "ak-gateway.task.xml")))
})

// ── processes and ports ─────────────────────────────────────────────────────

test("linux: a listener's inode from /proc/net/tcp, and a parent pid past a command name with parens", () => {
  const tcp = [
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode",
    "   0: 0100007F:128B 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 55501 1 0000000000000000 100 0 0 10 0",
    "   1: 0100007F:128B 0100007F:D431 01 00000000:00000000 00:00000000 00000000  1000        0 55999 1 0000000000000000 20 4 30 10 -1",
    "   2: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1 0000000000000000 100 0 0 10 0",
  ].join("\n")
  assert.deepEqual(listenInodes(tcp, 4747), ["55501"])
  assert.deepEqual(listenInodes(tcp, 22), ["12345"])
  assert.deepEqual(listenInodes(tcp, 80), [])
  assert.equal(ppidOfStat("4242 (node (worker) x) S 4100 4242 4100 0 -1 4194560"), 4100)
})

test("windows: netstat -ano names the listeners, in any language", () => {
  const out = [
    "Active Connections",
    "  Proto  Local Address          Foreign Address        State           PID",
    "  TCP    127.0.0.1:4747         0.0.0.0:0              LISTENING       5120",
    "  TCP    127.0.0.1:4747         127.0.0.1:50122        ESTABLISHED     5120",
    "  TCP    127.0.0.1:50122        127.0.0.1:4747         ESTABLISHED     9001",
    "  TCP    [::1]:4747             [::]:0                 ABHÖREN         5121",
    "  TCP    0.0.0.0:47470          0.0.0.0:0              LISTENING       7",
  ].join("\r\n")
  assert.deepEqual(netstatListeners(out, 4747), [5120, 5121])
})

test("windows: the owner-only ACL is granted on folders of ours, never on a folder around them", () => {
  assert.deepEqual(icaclsArgs("C:\\d", "PC\\alice", true), ["C:\\d", "/inheritance:r", "/grant:r", "PC\\alice:(OI)(CI)F"])
  assert.deepEqual(icaclsArgs("C:\\d\\a.json", "PC\\alice", false).slice(-1), ["PC\\alice:F"])
  const root = mkdtempSync(join(tmpdir(), "gw-perm-"))
  assert.ok(ours(join(root, "ak"), [root]))
  assert.ok(!ours(root, [root]), "never the root itself")
  assert.ok(!ours(join(root, "..", "elsewhere"), [root]))
})

test("managed settings: every OS's own location", () => {
  assert.deepEqual(managedSettings("linux"), ["/etc/claude-code/managed-settings.json"])
  assert.ok(managedSettings("win32").every((p) => p.startsWith("C:\\")))
  assert.equal(managedSettings("darwin").length, 1)
})

test("the line diff agrees with diff -U1 where diff exists", (t) => {
  if (spawnSync("diff", ["--version"]).error) return t.skip("no diff on this machine")
  const dir = mkdtempSync(join(tmpdir(), "gw-diff-"))
  mkdirSync(dir, { recursive: true })
  const cases: [string, string][] = [
    ["a\nb\nc\nd", "a\nB\nc\nd\ne"],
    ["1\n2\n3\n4\n5\n6\n7\n8\n9", "1\n2\nx\n4\n5\n6\n7\ny\n9"],
    ["1\n2\n3\n4\n5\n6", "1\nx\n3\n4\ny\n6"],
    ["", "a\nb"],
    ["a\nb", ""],
    ["same\nlines", "same\nlines"],
    ["a\nb\nc\na\nb\nc", "c\nb\na\nb\na\nc"],
  ]
  for (const [a, b] of cases) {
    writeFileSync(join(dir, "a"), a && !a.endsWith("\n") ? `${a}\n` : a)
    writeFileSync(join(dir, "b"), b && !b.endsWith("\n") ? `${b}\n` : b)
    const r = spawnSync("diff", ["-U1", join(dir, "a"), join(dir, "b")], { encoding: "utf8" })
    const want = r.stdout ? r.stdout.replace(/\n$/, "").split("\n").slice(2).map((l) => (l[0] === "@" ? "@" : l)) : []
    const got = lineDiff(a, b).map((e) => (e.op === "@" ? "@" : `${e.op}${e.line}`))
    // Two shortest scripts can differ where lines repeat; the changed-line counts cannot.
    const count = (xs: string[], op: string) => xs.filter((x) => x[0] === op).length
    if (a.includes("a\nb\nc\na")) assert.deepEqual([count(got, "-"), count(got, "+")], [count(want, "-"), count(want, "+")])
    else assert.deepEqual(got, want, JSON.stringify([a, b]))
  }
})
