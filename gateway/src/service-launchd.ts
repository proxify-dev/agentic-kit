/**
 * macOS: the gateway as a launchd user agent, com.ak.gateway.
 *
 * KeepAlive is unconditional: a crash restarts, a SIGTERM restarts. So "stop"
 * cannot be a signal — it is `bootout`, which unloads the unit until the next
 * login or `start`. The plist stays on disk either way; only `uninstall`
 * removes it.
 *
 * Everything shells to launchd's own CLI. There is no library, and its exit
 * codes are the contract we have.
 */
import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { command as gw } from "./command.ts"
import type { Result, Run, ServiceManager, Unit, UnitState } from "./service.ts"

const CTL = "launchctl" // portable: ok — the launchd backend's own tool

const sleepSync = (ms: number) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms)

const alive = (pid: number): boolean => {
  try {
    process.kill(pid, 0) // portable: ok — macOS only
    return true
  } catch {
    return false
  }
}

const xml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")

/**
 * The plist text. Pure — takes every path as an argument so a test can render
 * one without touching launchd or the home directory.
 */
export function renderPlist(args: {
  label: string
  program: string[]
  log: string
  env?: Record<string, string>
}): string {
  const env = Object.entries(args.env ?? {})
  return [
    `<?xml version="1.0" encoding="UTF-8"?>`,
    `<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">`,
    `<plist version="1.0">`,
    `<dict>`,
    `  <key>Label</key><string>${xml(args.label)}</string>`,
    `  <key>ProgramArguments</key>`,
    `  <array>`,
    ...args.program.map((a) => `    <string>${xml(a)}</string>`),
    `  </array>`,
    `  <key>RunAtLoad</key><true/>`,
    `  <key>KeepAlive</key><true/>`,
    // Ten seconds is launchd's default and turns a boot loop into a slow
    // crawl. Five keeps a port-collision retry visible in the log without
    // hammering.
    `  <key>ThrottleInterval</key><integer>5</integer>`,
    `  <key>StandardOutPath</key><string>${xml(args.log)}</string>`,
    `  <key>StandardErrorPath</key><string>${xml(args.log)}</string>`,
    ...(env.length
      ? [
          `  <key>EnvironmentVariables</key>`,
          `  <dict>`,
          ...env.map(([k, v]) => `    <key>${xml(k)}</key><string>${xml(v)}</string>`),
          `  </dict>`,
        ]
      : []),
    `</dict>`,
    `</plist>`,
    ``,
  ].join("\n")
}

/** What a plist runs, read back from its text. */
export function readProgram(text: string): string[] | null {
  const m = text.match(/<key>ProgramArguments<\/key>\s*<array>([\s\S]*?)<\/array>/)
  if (!m) return null
  return [...m[1].matchAll(/<string>([^<]*)<\/string>/g)].map((x) =>
    x[1].replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"),
  )
}

/** The pid in `launchctl print` output, when it is running. */
export function printedPid(out: string): number | null {
  const m = out.match(/^\s*pid = (\d+)/m)
  return m ? Number(m[1]) : null
}

export function launchd(opts: { label: string; home: string; run: Run; uid?: number }): ServiceManager {
  const LABEL = opts.label
  const AGENTS_DIR = join(opts.home, "Library", "LaunchAgents")
  const PLIST = join(AGENTS_DIR, `${LABEL}.plist`)

  /** `gui/<uid>` — the per-user domain launchd wants for a LaunchAgent. */
  const domain = () => `gui/${opts.uid ?? process.getuid?.() ?? 501}`
  const ctl = (...args: string[]) => opts.run(CTL, args)

  /**
   * `bootout` returns while the job is still being SIGTERMed, and a `bootstrap`
   * in that window fails with EIO and leaves the unit unloaded — the gateway
   * down until someone notices. So after a bootout, wait until launchd has let
   * go of the label and the old process is gone.
   */
  function waitUnloaded(pid: number | null, ms = 15_000): boolean {
    const deadline = Date.now() + ms
    while (Date.now() < deadline) {
      if (!ctl("print", `${domain()}/${LABEL}`).ok && (pid === null || !alive(pid))) return true
      sleepSync(50)
    }
    return false
  }

  /** Bootstrap, retried through launchd's own settling: EIO (5) and friends pass in a moment. */
  function bootstrap(): { ok: boolean; out: string } {
    let r = ctl("bootstrap", domain(), PLIST)
    for (let i = 0; i < 25 && !r.ok && !/already bootstrapped|Service already loaded/i.test(r.out); i++) {
      sleepSync(200)
      r = ctl("bootstrap", domain(), PLIST)
    }
    return r
  }

  function state(): UnitState {
    const print = ctl("print", `${domain()}/${LABEL}`)
    return {
      backend: "launchd",
      label: LABEL,
      file: PLIST,
      installed: existsSync(PLIST),
      loaded: print.ok,
      pid: print.ok ? printedPid(print.out) : null,
      program: existsSync(PLIST) ? readProgram(readFileSync(PLIST, "utf8")) : null,
    }
  }

  function install(unit: Unit, before: UnitState): Result {
    if (!existsSync(AGENTS_DIR)) mkdirSync(AGENTS_DIR, { recursive: true })
    writeFileSync(PLIST, renderPlist({ label: LABEL, program: unit.program, log: unit.log, env: unit.env }), { mode: 0o644 })

    // A unit disabled by an earlier `disable` stays disabled across
    // bootstraps, and the error message for that is famously unhelpful.
    ctl("enable", `${domain()}/${LABEL}`)
    if (before.loaded) {
      // Already loaded: bootout+bootstrap rereads the plist; kickstart alone
      // would keep running the old ProgramArguments.
      ctl("bootout", `${domain()}/${LABEL}`)
      if (!waitUnloaded(before.pid))
        return {
          ok: false,
          detail: `the old gateway (pid ${before.pid ?? "?"}) did not exit within 15 s; the new one was not loaded — \`${gw("enable")}\` retries`,
        }
    }
    // RunAtLoad starts it; a kickstart -k here would kill that start mid-boot.
    const boot = bootstrap()
    if (!boot.ok) {
      if (!/already bootstrapped|Service already loaded/i.test(boot.out))
        return {
          ok: false,
          detail: `${CTL} bootstrap: ${boot.out || "failed"} — the gateway is NOT running; \`${gw("enable")}\` retries`,
        }
      ctl("kickstart", `${domain()}/${LABEL}`)
    }
    const after = state()
    return { ok: true, detail: `${LABEL} loaded${after.pid ? ` (pid ${after.pid})` : ""}` }
  }

  function unload(): Result {
    const s = state()
    if (!s.loaded) return { ok: true, detail: `${s.label} not loaded` }
    const r = ctl("bootout", `${domain()}/${s.label}`)
    if (!r.ok) return { ok: false, detail: r.out }
    return waitUnloaded(s.pid)
      ? { ok: true, detail: `${s.label} unloaded` }
      : { ok: false, detail: `${s.label}: bootout sent, but pid ${s.pid ?? "?"} is still running after 15 s` }
  }

  function load(): Result {
    const s = state()
    if (!s.installed) return { ok: false, detail: `${s.label} is not installed` }
    if (s.loaded) {
      ctl("kickstart", `${domain()}/${s.label}`)
      return { ok: true, detail: `${s.label} already loaded` }
    }
    ctl("enable", `${domain()}/${s.label}`)
    const r = bootstrap()
    return r.ok ? { ok: true, detail: `${s.label} loaded` } : { ok: false, detail: r.out }
  }

  function uninstall(): Result {
    const s = state()
    if (s.loaded) {
      ctl("bootout", `${domain()}/${s.label}`)
      waitUnloaded(s.pid)
    }
    if (s.installed) {
      try {
        unlinkSync(s.file)
      } catch (err) {
        return { ok: false, detail: (err as Error).message }
      }
    }
    return { ok: true, detail: s.installed ? `${s.label} removed` : `${s.label} was not installed` }
  }

  return { backend: "launchd", manager: "launchd", label: LABEL, file: PLIST, state, install, load, unload, uninstall }
}
