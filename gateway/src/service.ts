/**
 * The gateway as a user service — the part that makes it a service and not a
 * process someone once started in a terminal. What it runs is the keeper
 * (keeper.ts), which runs the gateway as a child and swaps that child without
 * closing the port; the service manager only ever sees the keeper.
 *
 * One interface, three backends, picked by the OS:
 *
 *   launchd          macOS, a LaunchAgent (service-launchd.ts)
 *   systemd          Linux, a user unit (service-systemd.ts); WSL with systemd on
 *   Task Scheduler   Windows, a logon task (service-schtasks.ts)
 *
 * Each restarts the keeper when it dies, so "stop" is never a signal to it —
 * it is the manager's own stop (bootout, `systemctl stop`, a stop request and
 * `schtasks /End`), which holds until the next login or `start`. The unit's
 * file stays on disk either way; only `uninstall` removes it.
 *
 * What is the same everywhere lives here: which node runs it, the command, the
 * port check, the release the keeper will run.
 */
import { spawnSync } from "node:child_process"
import { existsSync, mkdirSync, readdirSync, statSync } from "node:fs"
import { homedir } from "node:os"
import { basename, dirname, join, resolve } from "node:path"
import type { Config } from "./config.ts"
import { CONFIG_PATH, loadConfig, paths } from "./config.ts"
import { gatewayDirOf, installKeeper, keeperPath, snapshot, want } from "./release.ts"
import type { Release } from "./release.ts"
import { GATEWAY_LABEL, env as brandEnv, envName, isMacos, isWindows } from "./brand.ts"
import { commandOf, portBindable, portListeners, which } from "./procs.ts"
import { launchd } from "./service-launchd.ts"
import { lingerProblem, systemd, systemdProblem } from "./service-systemd.ts"
import { schtasks } from "./service-schtasks.ts"

export { renderPlist } from "./service-launchd.ts"

export type Backend = "launchd" | "systemd" | "schtasks"

export type UnitState = {
  backend: Backend
  label: string
  /** The unit's file: the plist, the .service, the task's XML. */
  file: string
  /** That file exists. */
  installed: boolean
  /** The manager has it loaded right now (launchd: bootstrapped; systemd: active; Windows: running). */
  loaded: boolean
  /** The keeper's pid, when loaded and running. */
  pid: number | null
  /** What the unit would run — so drift from the checkout is visible. */
  program: string[] | null
}

/** `warn`: it worked, with a caveat the person must act on (systemd: linger refused). */
export type Result = { ok: boolean; detail: string; warn?: string }

/** What a backend is asked to run. */
export type Unit = { program: string[]; env: Record<string, string>; log: string }

/** A command and its combined output — injected, so a test drives a backend with a fake. */
export type Run = (cmd: string, args: string[]) => { ok: boolean; out: string }

export const run: Run = (cmd, args) => {
  const r = spawnSync(cmd, args, { encoding: "utf8", windowsHide: true })
  return { ok: r.status === 0, out: `${r.stdout ?? ""}${r.stderr ?? ""}`.trim() }
}

export interface ServiceManager {
  readonly backend: Backend
  /** The manager as a person names it: launchd, systemd, Task Scheduler. */
  readonly manager: string
  readonly label: string
  readonly file: string
  state(): UnitState
  /** Write the unit and run it from that file. Any process it ran before is gone before the new one starts. */
  install(unit: Unit, before: UnitState): Result
  /** Start an installed unit without rewriting it. */
  load(): Result
  /** Stop it, no restart, and keep the file. Returns once the process is gone. */
  unload(): Result
  /** Unload and delete the file. */
  uninstall(): Result
}

/** Overridable so a check against a real manager can use a throwaway unit, never this one. */
export const LABEL = brandEnv("GATEWAY_LABEL") ?? GATEWAY_LABEL

/** `com.ak.gateway` → `ak-gateway`: the name systemd and Task Scheduler know it by. */
export const unitName = (label: string) => label.replace(/^com\./, "").replace(/[^A-Za-z0-9_-]/g, "-")

export function backendFor(platform: NodeJS.Platform = process.platform): Backend {
  return platform === "darwin" ? "launchd" : platform === "win32" ? "schtasks" : "systemd"
}

let chosen: ServiceManager | null = null

/** The backend for this OS. Its paths are read once, on first use. */
export function manager(): ServiceManager {
  if (chosen) return chosen
  const b = backendFor()
  chosen =
    b === "launchd"
      ? launchd({ label: LABEL, home: homedir(), run })
      : b === "systemd"
        ? systemd({ unit: unitName(LABEL), run })
        : schtasks({ task: unitName(LABEL), dataDir: () => loadConfig().dataDir, run })
  return chosen
}

/** Why this machine cannot run the service, or null. Linux only: no systemd user session (WSL without systemd). */
export function serviceProblem(): string | null {
  return backendFor() === "systemd" ? systemdProblem(run) : null
}

/**
 * A real `node`, never Electron. Under the app `process.execPath` is the app
 * binary, and a unit that points at it would tie the wire's lifetime to
 * whatever build the app last was. Env wins, then the running node when it is
 * one, then the usual homes, then PATH.
 */
export function nodeBinary(): string | null {
  const fromEnv = brandEnv("GATEWAY_NODE")
  if (fromEnv && existsSync(fromEnv)) return fromEnv
  if (/^node(\.exe)?$/i.test(basename(process.execPath)) && existsSync(process.execPath)) return process.execPath
  const nvm = join(homedir(), ".nvm", "versions", "node")
  if (existsSync(nvm)) {
    const versions = readdirSync(nvm)
      .filter((v) => /^v\d+/.test(v))
      .sort((a, b) => Number(b.slice(1).split(".")[0]) - Number(a.slice(1).split(".")[0]))
    for (const v of versions) {
      const bin = join(nvm, v, "bin", "node")
      if (existsSync(bin)) return bin
    }
  }
  const homes = isWindows()
    ? [join(process.env.ProgramFiles ?? "C:\\Program Files", "nodejs", "node.exe")]
    : ["/opt/homebrew/bin/node", "/usr/local/bin/node"] // portable: ok — macOS's Homebrew and the classic prefix, where a GUI app's PATH does not reach
  for (const bin of homes) if (existsSync(bin)) return bin
  return which("node")
}

export function state(): UnitState {
  return manager().state()
}

/**
 * The command the unit runs: the keeper's installed copy, never a checkout. The
 * keeper runs a release (release.ts), so nothing the unit touches moves when the
 * checkout does.
 */
export function programFor(cfg: Config): { program: string[]; env: Record<string, string> } | { error: string } {
  const node = nodeBinary()
  if (!node) return { error: `no node binary found — set ${envName("GATEWAY_NODE")}` }
  // A minimal PATH: launchd and systemd give a unit almost nothing, and the
  // daemon shells nothing, but the engine's refresh path wants the basics. A
  // Windows task runs in its user's own logon environment, so it gets none.
  const env: Record<string, string> = isWindows()
    ? {}
    : {
        HOME: homedir(),
        PATH: `${dirname(node)}:/usr/bin:/bin:/usr/sbin:/sbin`, // portable: ok — POSIX only; a Windows task keeps its own
      }
  Object.assign(env, carriedEnv())
  if (brandEnv("GATEWAY_CONFIG")) env[envName("GATEWAY_CONFIG")] = CONFIG_PATH
  return { program: [node, "--experimental-strip-types", "--no-warnings", keeperPath(cfg)], env }
}

/**
 * What of this shell's environment decides where the gateway's files are, so
 * the unit carries it: launchd and systemd start the keeper with next to
 * nothing, and a keeper that missed an XDG_CONFIG_HOME the CLI honoured would
 * read another (empty) accounts file than the one the CLI just wrote. The
 * config overrides loadConfig reads go too, so the service serves the config
 * `enable` checked. Only what is set; never the keeper's own GATEWAY_RELEASE/KEEPER.
 */
export const CARRIED_ENV = [
  "XDG_CONFIG_HOME",
  "XDG_DATA_HOME",
  "XDG_STATE_HOME",
  "CLAUDE_CONFIG_DIR",
  ...["GATEWAY_CONFIG", "GATEWAY_ACCOUNTS_FILE", "GATEWAY_DATA_DIR", "GATEWAY_PORT", "GATEWAY_ACCOUNT", "GATEWAY_CAPTURE"].map(envName),
]

export function carriedEnv(src: NodeJS.ProcessEnv = process.env): Record<string, string> {
  const out: Record<string, string> = {}
  for (const k of CARRIED_ENV) if (src[k]) out[k] = src[k] as string
  // A relative file would resolve against the unit's working directory, not this shell's.
  for (const k of [envName("GATEWAY_ACCOUNTS_FILE"), envName("GATEWAY_DATA_DIR")]) if (out[k]) out[k] = resolve(out[k])
  return out
}

/** Who holds a port, if anyone — so a collision is named, not guessed at. The pid is null where no tool can say. */
export function portHolder(port: number, host = "127.0.0.1"): { pid: number | null; command: string } | null {
  const holders = portListeners(port)
  const pid = holders?.[0]
  if (pid) return { pid, command: commandOf(pid) }
  // Nobody named: on macOS its tool sees every listener, so that is the answer. Elsewhere the
  // listener may be another user's, whose sockets /proc will not show — the bind says.
  if (isMacos() || portBindable(port, host)) return null
  return { pid: null, command: "" }
}

export type InstallResult = { ok: true; detail: string; state: UnitState; warn?: string } | { ok: false; detail: string }

/**
 * The git worktree a daemon entry lives in, or null when it is not one. Walked
 * up from the gateway folder to the first `.git`: a file there points home, so
 * that folder is a worktree; a directory is a main checkout, and no `.git` at
 * all is a copy (a release `enable` made, an older installed one). The CLI runs
 * the code you stand in, so an install or restart from a worktree would put
 * unmerged code on the wire every session goes through.
 */
export function worktreeOf(daemonEntry: string): string | null {
  for (let dir = gatewayDirOf(daemonEntry); ; dir = dirname(dir)) {
    const git = join(dir, ".git")
    if (existsSync(git)) return statSync(git).isFile() ? dir : null
    if (dirname(dir) === dir) return null
  }
}

/**
 * Copy the checkout into a release (or take one already made and preflighted),
 * put the keeper in place, write the unit and load it. Idempotent, and a real
 * restart when the unit is loaded: the old process exits before the keeper
 * binds. So it is for the first install and for a new keeper; every other code
 * change is a hand-over (`ak gateway restart`).
 */
export function install(cfg: Config, daemonEntry: string, prepared?: Release): InstallResult {
  const wt = worktreeOf(daemonEntry)
  if (wt)
    return {
      ok: false,
      detail: `${wt} is a git worktree — the service would put its unmerged code on the wire. Install from a main checkout or an installed copy.`,
    }
  const problem = serviceProblem()
  if (problem) return { ok: false, detail: problem }
  const prog = programFor(cfg)
  if ("error" in prog) return { ok: false, detail: prog.error }
  const log = paths(cfg).log
  const mgr = manager()

  // The manager would loop on EADDRINUSE forever while whatever holds the port
  // keeps serving — so name it and stop here instead.
  const before = mgr.state()
  const named = portListeners(cfg.port)
  const holders = named ?? []
  const ours = before.loaded && before.pid !== null && holders.includes(before.pid)
  // With nobody named (no tool could say), a bind that fails is held all the same, unless our unit is up.
  const held = holders.length > 0 ? !ours : !isMacos() && !before.loaded && !portBindable(cfg.port, cfg.host)
  if (held) {
    const holder = holders[0] ? commandOf(holders[0]) : ""
    return {
      ok: false,
      detail: `port ${cfg.port} is held by pid ${holders[0] ?? "?"} (${holder.slice(0, 80)}), which ${mgr.manager} does not own — stop it first.`,
    }
  }

  // What the keeper will run: this checkout, copied, and the keeper itself.
  const gatewayDir = gatewayDirOf(daemonEntry)
  const rel = prepared ?? snapshot(cfg, gatewayDir)
  want(cfg, rel.id)
  installKeeper(cfg, gatewayDir)

  if (!existsSync(dirname(log))) mkdirSync(dirname(log), { recursive: true, mode: 0o700 })
  const r = mgr.install({ program: prog.program, env: prog.env, log }, before)
  if (!r.ok) return { ok: false, detail: r.detail }
  return { ok: true, detail: `${r.detail}, build ${rel.id}`, state: mgr.state(), ...(r.warn ? { warn: r.warn } : {}) }
}

/** What works now but will not hold — for `doctor`. systemd: no linger, so the gateway dies at logout. */
export function serviceWarnings(): string[] {
  if (backendFor() !== "systemd" || !manager().state().installed) return []
  const w = lingerProblem(run)
  return w ? [w] : []
}

/** Unload (stop, no restart) but keep the unit's file. `start` reloads it. Returns once the process is gone. */
export function unload(): Result {
  return manager().unload()
}

/** Load an installed unit without rewriting it. */
export function load(): Result {
  return manager().load()
}

/** Unload and delete the unit's file. The daemon stops; nothing brings it back. */
export function uninstall(): Result {
  return manager().uninstall()
}
