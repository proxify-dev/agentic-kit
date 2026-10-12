/**
 * Linux: the gateway as a systemd user unit, ~/.config/systemd/user/ak-gateway.service.
 *
 * Restart=always is launchd's KeepAlive: a crash restarts, an exit restarts.
 * "stop" is `systemctl --user stop`, which holds until the next login or
 * `start`, and returns only once the process is gone — so there is no settling
 * loop here, where launchd needs one. KillMode=mixed sends the SIGTERM to the
 * keeper alone: it drains its workers itself, and anything left at the timeout
 * is killed with the rest of the group.
 *
 * `enable` makes it start at login; `loginctl enable-linger` at boot, with
 * nobody logged in, and past a logout. An unprivileged enable-linger needs
 * polkit; refused (a headless box, ssh), install still succeeds but warns with
 * the sudo line, and `doctor` repeats it. WSL runs it only with systemd on.
 * No After=network-online.target: a user manager cannot order on a system target.
 */
import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs"
import { userInfo } from "node:os"
import { dirname, join } from "node:path"
import { DISPLAY_NAME, configDir } from "./brand.ts"
import { command as gw } from "./command.ts"
import type { Result, Run, ServiceManager, Unit, UnitState } from "./service.ts"

/** One ExecStart word: quoted when it must be, `%` and `$` doubled (systemd expands both). */
export function execWord(a: string): string {
  const esc = a.replace(/%/g, "%%").replace(/\$/g, "$$$$")
  return /^[A-Za-z0-9_@%$+=:,./-]+$/.test(esc) ? esc : `"${esc.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`
}

const quoted = (s: string) => `"${s.replace(/%/g, "%%").replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`

/** The unit text. Pure, like renderPlist. */
export function renderUnit(args: { description: string; program: string[]; log: string; env?: Record<string, string> }): string {
  const log = args.log.replace(/%/g, "%%")
  return [
    `[Unit]`,
    `Description=${args.description}`,
    ``,
    `[Service]`,
    `Type=simple`,
    `ExecStart=${args.program.map(execWord).join(" ")}`,
    ...Object.entries(args.env ?? {}).map(([k, v]) => `Environment=${quoted(`${k}=${v}`)}`),
    `Restart=always`,
    // launchd's ThrottleInterval, the same five seconds.
    `RestartSec=5`,
    `KillMode=mixed`,
    `TimeoutStopSec=15`,
    `StandardOutput=append:${log}`,
    `StandardError=append:${log}`,
    ``,
    `[Install]`,
    `WantedBy=default.target`,
    ``,
  ].join("\n")
}

/** The words of a unit's ExecStart, unquoted — what the unit runs. */
export function readExecStart(text: string): string[] | null {
  const line = text.split("\n").find((l) => l.startsWith("ExecStart="))
  if (!line) return null
  const s = line.slice("ExecStart=".length)
  const words: string[] = []
  let i = 0
  while (i < s.length) {
    while (s[i] === " ") i++
    if (i >= s.length) break
    let w = ""
    if (s[i] === '"') {
      for (i++; i < s.length && s[i] !== '"'; i++) w += s[i] === "\\" ? s[++i] : s[i]
      i++
    } else for (; i < s.length && s[i] !== " "; i++) w += s[i]
    words.push(w.replace(/%%/g, "%").replace(/\$\$/g, "$"))
  }
  return words
}

/** `systemctl show -p ActiveState -p MainPID` output → whether it runs, and its pid. */
export function parseShow(out: string): { active: boolean; pid: number | null } {
  const kv = Object.fromEntries(
    out
      .split("\n")
      .filter((l) => l.includes("="))
      .map((l) => [l.slice(0, l.indexOf("=")).trim(), l.slice(l.indexOf("=") + 1).trim()]),
  )
  const pid = Number(kv.MainPID)
  return {
    active: ["active", "activating", "reloading"].includes(kv.ActiveState ?? ""),
    pid: Number.isInteger(pid) && pid > 0 ? pid : null,
  }
}

/** Running under WSL: its kernel says so. */
export function isWsl(procVersion: string, env: NodeJS.ProcessEnv = process.env): boolean {
  return /microsoft/i.test(procVersion) || Boolean(env.WSL_DISTRO_NAME)
}

/** Why no user unit can run here, or null — a WSL distro without systemd is the usual one. */
export function systemdProblem(run: Run, procVersion = readProcVersion()): string | null {
  const r = run("systemctl", ["--user", "show-environment"])
  if (r.ok) return null
  const fallback = `\`${gw("start --detach")}\` runs it without a service meanwhile`
  if (isWsl(procVersion))
    return `WSL without systemd: this distro has no service manager to keep the gateway up. Add\n  [boot]\n  systemd=true\nto /etc/wsl.conf, run \`wsl --shutdown\` from Windows and reopen the distro; ${fallback}`
  return `no systemd user session (systemctl --user: ${r.out.split("\n")[0] || "not found"}) — ${fallback}`
}

function readProcVersion(): string {
  try {
    return readFileSync("/proc/version", "utf8")
  } catch {
    return ""
  }
}

/** The login name loginctl knows: $USER when it is set and not empty, else the passwd entry. */
export function currentUser(env: NodeJS.ProcessEnv = process.env): string {
  return env.USER || env.LOGNAME || userInfo().username
}

/** `loginctl show-user NAME -p Linger` says yes. */
export function lingerOn(run: Run, user: string): boolean {
  const r = run("loginctl", ["show-user", user, "-p", "Linger"])
  return r.ok && /^Linger=yes\s*$/m.test(r.out)
}

export function lingerWarning(user: string, why = ""): string {
  const first = why.split("\n")[0]
  return `linger is off for ${user}${first ? ` (loginctl: ${first})` : ""} — the gateway stops when you log out and starts only at your next login. Run \`sudo loginctl enable-linger ${user}\` once to keep it up.`
}

/** Why the user unit will not outlive a logout, or null. For `doctor`. */
export function lingerProblem(run: Run, user = currentUser()): string | null {
  const r = run("loginctl", ["show-user", user, "-p", "Linger"])
  if (!r.ok) return null // no logind to ask (a container): nothing to say
  return /^Linger=yes\s*$/m.test(r.out) ? null : lingerWarning(user)
}

export function systemd(opts: { unit: string; run: Run; dir?: string; user?: string }): ServiceManager {
  const UNIT = `${opts.unit}.service`
  const FILE = join(opts.dir ?? join(configDir(), "systemd", "user"), UNIT)
  const ctl = (...args: string[]) => opts.run("systemctl", ["--user", ...args])

  function state(): UnitState {
    const installed = existsSync(FILE)
    const show = ctl("show", UNIT, "-p", "ActiveState", "-p", "MainPID")
    const s = show.ok ? parseShow(show.out) : { active: false, pid: null }
    return {
      backend: "systemd",
      label: UNIT,
      file: FILE,
      installed,
      loaded: s.active,
      pid: s.active ? s.pid : null,
      program: installed ? readExecStart(readFileSync(FILE, "utf8")) : null,
    }
  }

  function install(unit: Unit, before: UnitState): Result {
    mkdirSync(dirname(FILE), { recursive: true })
    writeFileSync(FILE, renderUnit({ description: `${DISPLAY_NAME} gateway`, program: unit.program, log: unit.log, env: unit.env }), { mode: 0o644 })
    const reload = ctl("daemon-reload")
    if (!reload.ok) return { ok: false, detail: `systemctl --user daemon-reload: ${reload.out || "failed"}` }
    if (before.loaded) {
      // Synchronous: it returns once the old keeper has drained and exited.
      const stop = ctl("stop", UNIT)
      if (!stop.ok)
        return { ok: false, detail: `the old gateway (pid ${before.pid ?? "?"}) did not stop: ${stop.out} — \`${gw("enable")}\` retries` }
    }
    const start = ctl("enable", "--now", UNIT)
    if (!start.ok)
      return { ok: false, detail: `systemctl --user enable --now: ${start.out || "failed"} — the gateway is NOT running; \`${gw("enable")}\` retries` }
    // With linger the unit starts at boot and outlives the last logout. Without
    // polkit (a headless box, ssh) an unprivileged enable-linger is refused.
    const user = opts.user ?? currentUser()
    const linger = lingerOn(opts.run, user) ? { ok: true, out: "" } : opts.run("loginctl", ["enable-linger", user])
    const after = state()
    const detail = `${UNIT} active${after.pid ? ` (pid ${after.pid})` : ""}`
    return linger.ok ? { ok: true, detail } : { ok: true, detail, warn: lingerWarning(user, linger.out) }
  }

  function load(): Result {
    const s = state()
    if (!s.installed) return { ok: false, detail: `${UNIT} is not installed` }
    if (s.loaded) return { ok: true, detail: `${UNIT} already active` }
    const r = ctl("start", UNIT)
    return r.ok ? { ok: true, detail: `${UNIT} started` } : { ok: false, detail: r.out }
  }

  function unload(): Result {
    const s = state()
    if (!s.loaded) return { ok: true, detail: `${UNIT} not loaded` }
    const r = ctl("stop", UNIT)
    return r.ok ? { ok: true, detail: `${UNIT} stopped` } : { ok: false, detail: r.out }
  }

  function uninstall(): Result {
    const s = state()
    if (s.installed || s.loaded) ctl("disable", "--now", UNIT)
    if (s.installed) {
      try {
        unlinkSync(FILE)
      } catch (err) {
        return { ok: false, detail: (err as Error).message }
      }
      ctl("daemon-reload")
    }
    return { ok: true, detail: s.installed ? `${UNIT} removed` : `${UNIT} was not installed` }
  }

  return { backend: "systemd", manager: "systemd", label: UNIT, file: FILE, state, install, load, unload, uninstall }
}
