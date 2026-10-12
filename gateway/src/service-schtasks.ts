/**
 * Windows: the gateway as a Task Scheduler task, \ak-gateway — started at
 * logon, restarted when it fails, no time limit, no window. `schtasks` is the
 * whole interface: /Create from an XML file, /Run, /End, /Delete, /Query.
 *
 * Task Scheduler neither reports the pid nor redirects output nor sets an
 * environment, so the keeper does all three itself: it writes its pid file
 * (gateway.pid beside it), takes `--log FILE` and `--env K=V`. It runs under
 * `conhost --headless`, the console host with no window, since a console
 * program started at logon would otherwise open one.
 *
 * "stop" is a stop request in the keeper's `control` file — it drains its
 * workers and exits 0, which is not a failure, so nothing restarts it — then
 * `/End` for whatever did not listen. It holds until the next logon or `start`.
 */
import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { DISPLAY_NAME, pidAlive } from "./brand.ts"
import { command as gw } from "./command.ts"
import { windowsPrincipal } from "./fsperm.ts"
import { tellKeeper } from "./release.ts"
import type { Result, Run, ServiceManager, Unit, UnitState } from "./service.ts"

const sleepSync = (ms: number) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms)

const xml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")

const unxml = (s: string) =>
  s.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&")

/** One argument as the Windows C runtime reads a command line back into argv. */
export function winQuote(a: string): string {
  if (a !== "" && !/[\s"]/.test(a)) return a
  return `"${a.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\+)$/, "$1$1")}"`
}

/** A command line back into argv, by the same rules. */
export function winSplit(s: string): string[] {
  const out: string[] = []
  let i = 0
  while (i < s.length) {
    while (s[i] === " " || s[i] === "\t") i++
    if (i >= s.length) break
    let w = ""
    let inQuote = false
    while (i < s.length) {
      const c = s[i]
      if (!inQuote && (c === " " || c === "\t")) break
      if (c === "\\") {
        let n = 0
        while (s[i] === "\\") n++, i++
        if (s[i] === '"') {
          w += "\\".repeat(n >> 1)
          if (n % 2) (w += '"'), i++
        } else w += "\\".repeat(n)
        continue
      }
      if (c === '"') inQuote = !inQuote
      else w += c
      i++
    }
    out.push(w)
  }
  return out
}

/** The keeper's argv under Task Scheduler: its program, then the output and environment it cannot be given. */
export function taskArgv(unit: Unit): string[] {
  return [...unit.program, "--log", unit.log, ...Object.entries(unit.env).flatMap(([k, v]) => ["--env", `${k}=${v}`])]
}

/** The task's XML. Pure, like renderPlist; `schtasks /Create /XML` wants it as UTF-16. */
export function renderTask(args: { description: string; user: string; argv: string[]; workDir: string; conhost?: string }): string {
  const conhost = args.conhost ?? "conhost.exe"
  return [
    `<?xml version="1.0" encoding="UTF-16"?>`,
    `<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">`,
    `  <RegistrationInfo>`,
    `    <Description>${xml(args.description)}</Description>`,
    `  </RegistrationInfo>`,
    `  <Triggers>`,
    `    <LogonTrigger>`,
    `      <Enabled>true</Enabled>`,
    `      <UserId>${xml(args.user)}</UserId>`,
    `    </LogonTrigger>`,
    `  </Triggers>`,
    `  <Principals>`,
    `    <Principal id="Author">`,
    `      <UserId>${xml(args.user)}</UserId>`,
    // Run only when the user is logged on: the tokens are the user's, and so is the port.
    `      <LogonType>InteractiveToken</LogonType>`,
    `      <RunLevel>LeastPrivilege</RunLevel>`,
    `    </Principal>`,
    `  </Principals>`,
    `  <Settings>`,
    `    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>`,
    `    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>`,
    `    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>`,
    `    <AllowHardTerminate>true</AllowHardTerminate>`,
    `    <StartWhenAvailable>true</StartWhenAvailable>`,
    `    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>`,
    `    <IdleSettings>`,
    `      <StopOnIdleEnd>false</StopOnIdleEnd>`,
    `      <RestartOnIdle>false</RestartOnIdle>`,
    `    </IdleSettings>`,
    `    <AllowStartOnDemand>true</AllowStartOnDemand>`,
    `    <Enabled>true</Enabled>`,
    `    <Hidden>true</Hidden>`,
    `    <RunOnlyIfIdle>false</RunOnlyIfIdle>`,
    `    <WakeToRun>false</WakeToRun>`,
    // PT0S: no limit. The default, three days, would end the gateway on the third.
    `    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>`,
    `    <Priority>7</Priority>`,
    `    <RestartOnFailure>`,
    `      <Interval>PT1M</Interval>`,
    `      <Count>999</Count>`,
    `    </RestartOnFailure>`,
    `  </Settings>`,
    `  <Actions Context="Author">`,
    `    <Exec>`,
    `      <Command>${xml(conhost)}</Command>`,
    `      <Arguments>${xml(["--headless", ...args.argv].map(winQuote).join(" "))}</Arguments>`,
    `      <WorkingDirectory>${xml(args.workDir)}</WorkingDirectory>`,
    `    </Exec>`,
    `  </Actions>`,
    `</Task>`,
    ``,
  ].join("\r\n")
}

/** What a task XML runs, past `conhost --headless`. */
export function readTaskArgv(text: string): string[] | null {
  const m = text.match(/<Arguments>([\s\S]*?)<\/Arguments>/)
  if (!m) return null
  const argv = winSplit(unxml(m[1]))
  return argv[0] === "--headless" ? argv.slice(1) : argv
}

/** `schtasks /Query /TN x /FO LIST` → the Status line (English only; elsewhere the pid file answers). */
export function parseQuery(out: string): { status: string | null } {
  const m = out.match(/^\s*Status:\s*(.+?)\s*$/im)
  return { status: m ? m[1] : null }
}

export function schtasks(opts: {
  task: string
  dataDir: () => string
  run: Run
  alive?: (pid: number) => boolean
  user?: string
}): ServiceManager {
  const TASK = opts.task
  const alive = opts.alive ?? pidAlive
  const dir = () => opts.dataDir()
  const file = () => join(dir(), `${TASK}.task.xml`)
  const tasks = (...args: string[]) => opts.run("schtasks", args)

  const keeperPid = (): number | null => {
    try {
      const n = Number(readFileSync(join(dir(), "gateway.pid"), "utf8").trim())
      return Number.isInteger(n) && n > 0 && alive(n) ? n : null
    } catch {
      return null
    }
  }

  function state(): UnitState {
    const q = tasks("/Query", "/TN", TASK, "/FO", "LIST")
    const pid = q.ok ? keeperPid() : null
    const running = q.ok && /^running$/i.test(parseQuery(q.out).status ?? "")
    const f = file()
    return {
      backend: "schtasks",
      label: TASK,
      file: f,
      installed: q.ok,
      loaded: running || pid !== null,
      pid,
      program: existsSync(f) ? readTaskArgv(readFileSync(f, "utf16le").replace(/^﻿/, "")) : null,
    }
  }

  /** Until the keeper's pid is gone, or `ms` passes. */
  const waitGone = (pid: number | null, ms: number): boolean => {
    const deadline = Date.now() + ms
    while (pid !== null && alive(pid) && Date.now() < deadline) sleepSync(100)
    return pid === null || !alive(pid)
  }

  /** The stop request first — the keeper drains and exits 0 — then /End for a keeper that did not hear it. */
  function stop(s: UnitState): boolean {
    if (s.pid !== null) {
      tellKeeper(dir(), "stop")
      waitGone(s.pid, 8000)
    }
    tasks("/End", "/TN", TASK)
    return waitGone(s.pid, 5000)
  }

  function install(unit: Unit, before: UnitState): Result {
    if (before.loaded && !stop(before))
      return { ok: false, detail: `the old gateway (pid ${before.pid ?? "?"}) did not exit; the new one was not started — \`${gw("enable")}\` retries` }
    mkdirSync(dir(), { recursive: true })
    const text = renderTask({ description: `${DISPLAY_NAME} gateway`, user: opts.user ?? windowsPrincipal(), argv: taskArgv(unit), workDir: dir() })
    writeFileSync(file(), Buffer.from(`﻿${text}`, "utf16le"))
    const made = tasks("/Create", "/TN", TASK, "/XML", file(), "/F")
    if (!made.ok) return { ok: false, detail: `schtasks /Create: ${made.out || "failed"} — the gateway is NOT running` }
    const started = tasks("/Run", "/TN", TASK)
    if (!started.ok) return { ok: false, detail: `schtasks /Run: ${started.out || "failed"} — the gateway is NOT running; \`${gw("enable")}\` retries` }
    // The pid appears once the keeper has written it.
    const deadline = Date.now() + 5000
    let pid = keeperPid()
    while (pid === null && Date.now() < deadline) sleepSync(100), (pid = keeperPid())
    return { ok: true, detail: `${TASK} running${pid ? ` (pid ${pid})` : ""}` }
  }

  function load(): Result {
    const s = state()
    if (!s.installed) return { ok: false, detail: `${TASK} is not installed` }
    if (s.loaded) return { ok: true, detail: `${TASK} already running` }
    const r = tasks("/Run", "/TN", TASK)
    return r.ok ? { ok: true, detail: `${TASK} started` } : { ok: false, detail: r.out }
  }

  function unload(): Result {
    const s = state()
    if (!s.loaded) return { ok: true, detail: `${TASK} not loaded` }
    return stop(s)
      ? { ok: true, detail: `${TASK} stopped` }
      : { ok: false, detail: `${TASK}: stop asked and /End sent, but pid ${s.pid ?? "?"} is still running` }
  }

  function uninstall(): Result {
    const s = state()
    if (s.loaded) stop(s)
    if (s.installed) {
      const r = tasks("/Delete", "/TN", TASK, "/F")
      if (!r.ok) return { ok: false, detail: r.out }
    }
    try {
      if (existsSync(file())) unlinkSync(file())
    } catch {
      // The registration is what counts; the copy beside it is for reading back.
    }
    return { ok: true, detail: s.installed ? `${TASK} removed` : `${TASK} was not installed` }
  }

  return {
    backend: "schtasks",
    manager: "Task Scheduler",
    label: TASK,
    get file() {
      return file()
    },
    state,
    install,
    load,
    unload,
    uninstall,
  }
}
