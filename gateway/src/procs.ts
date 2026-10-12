/**
 * Processes and ports, per OS — what the seam (brand.ts) does not cover: who
 * holds a port, what a pid is running, which processes descend from one, where
 * a program is on PATH. Linux answers from /proc, Windows with `netstat` and
 * CIM, macOS with the tools it ships; the parsers are pure so a test can feed
 * any OS's output on any OS.
 */
import { spawnSync } from "node:child_process"
import { existsSync, readdirSync, readFileSync, readlinkSync } from "node:fs"
import { delimiter, posix as posixPath, win32 } from "node:path"
import { isMacos, isWindows } from "./brand.ts"

const sh = (cmd: string, args: string[]): string | null => {
  const r = spawnSync(cmd, args, { encoding: "utf8", windowsHide: true })
  return r.error ? null : (r.stdout ?? "")
}

type WhichOpts = { plat?: NodeJS.Platform; exists?: (f: string) => boolean }

/**
 * The file names `name` answers to in one directory, in the order tried. POSIX:
 * itself. Windows: name + each PATHEXT extension — never the bare name, which is
 * npm's extensionless sh script there — with .exe/.com before .cmd/.bat, since
 * a batch file cannot be spawned without cmd.exe. A name that already has an
 * extension is itself.
 */
export function commandNames(name: string, env: NodeJS.ProcessEnv = process.env, plat: NodeJS.Platform = process.platform): string[] {
  if (plat !== "win32") return [name]
  if (/\.[^.\\/]+$/.test(name)) return [name]
  const exts = (env.PATHEXT || ".COM;.EXE;.BAT;.CMD").split(";").filter(Boolean).map((e) => e.toLowerCase())
  const rank = (e: string) => (e === ".exe" || e === ".com" ? 0 : e === ".cmd" || e === ".bat" ? 2 : 1)
  return exts.map((e, i) => ({ e, i })).sort((a, b) => rank(a.e) - rank(b.e) || a.i - b.i).map(({ e }) => name + e)
}

/** A program on PATH (with PATHEXT on Windows), or null. */
export function which(name: string, env: NodeJS.ProcessEnv = process.env, opts: WhichOpts = {}): string | null {
  const plat = opts.plat ?? process.platform
  const exists = opts.exists ?? existsSync
  const P = plat === "win32" ? win32 : posixPath
  const pathVar = env.PATH ?? env.Path ?? Object.entries(env).find(([k]) => k.toUpperCase() === "PATH")?.[1] ?? ""
  for (const dir of pathVar.split(plat === "win32" ? ";" : delimiter).filter(Boolean))
    for (const n of commandNames(name, env, plat)) {
      const f = P.join(dir, n)
      if (exists(f)) return f
    }
  return null
}

// ── starting a program Windows cannot start directly ────────────────────────
// A copy of shim/cc-shim.mjs's launchPlan and its helpers: the shim is installed
// alone and cannot be imported from here, nor this from there. Keep them in step.

// cmd.exe quoting for a batch file (cross-spawn's rules): backslashes before a
// quote doubled, the quote escaped, the whole argument quoted, then every cmd
// metacharacter caret-escaped — twice, because a batch file re-parses its %*.
const CMD_META = /([()\][%!^"`<>&|;, *?])/g

export function quoteCmdArg(arg: string, batch = true): string {
  let a = `${arg}`
  a = a.replace(/(?=(\\+?)?)\1"/g, '$1$1\\"')
  a = a.replace(/(?=(\\+?)?)\1$/, "$1$1")
  a = `"${a}"`
  a = a.replace(CMD_META, "^$1")
  if (batch) a = a.replace(CMD_META, "^$1")
  return a
}

export const quoteCmdCommand = (file: string): string => win32.normalize(file).replace(CMD_META, "^$1")

/**
 * An npm-style .cmd is two lines of batch around one real target — a script
 * node runs, or a native exe. Seeing through it needs no cmd.exe at all.
 */
export function seeThroughCmd(file: string, text?: string, isFile: (f: string) => boolean = existsSync): { command: string; args: string[] } | null {
  let body = text
  if (body === undefined)
    try {
      body = readFileSync(file, "utf8")
    } catch {
      return null
    }
  const hits = [...body.matchAll(/"%~?dp0%?\\([^"%]+\.(?:c?js|mjs|exe))"/gi)].map((m) => m[1]).filter((p) => !/^node\.exe$/i.test(p))
  if (!hits.length) return null
  const target = win32.join(win32.dirname(file), hits[hits.length - 1])
  if (/\.exe$/i.test(target)) return { command: target, args: [] }
  const localNode = win32.join(win32.dirname(file), "node.exe")
  return { command: isFile(localNode) ? localNode : process.execPath, args: [target] }
}

export type SpawnPlan = { command: string; args: string[]; options: { windowsVerbatimArguments?: boolean } }

/**
 * How to spawn `file` with `args` without a shell. POSIX, and an .exe/.com: as
 * is. A .cmd/.bat on Windows — which spawn refuses (EINVAL) without a shell —
 * seen through to its target when it is an npm shim, else through cmd.exe with
 * every argument quoted for it. Throws on a line break, which cmd.exe cannot carry.
 */
export function spawnable(file: string, args: string[] = [], plat: NodeJS.Platform = process.platform, readText?: (f: string) => string | undefined): SpawnPlan {
  if (plat !== "win32" || !/\.(cmd|bat)$/i.test(file)) return { command: file, args, options: {} }
  const seen = seeThroughCmd(file, readText ? readText(file) : undefined)
  if (seen && existsSync(seen.args[0] ?? seen.command)) return { command: seen.command, args: [...seen.args, ...args], options: {} }
  if (args.some((a) => /[\r\n]/.test(a))) throw new Error(`${file} is a batch file, and cmd.exe cannot pass an argument with a line break`)
  const line = [quoteCmdCommand(file), ...args.map((a) => quoteCmdArg(a, true))].join(" ")
  return { command: process.env.ComSpec || "cmd.exe", args: ["/d", "/s", "/c", `"${line}"`], options: { windowsVerbatimArguments: true } }
}

/** `name` resolved on PATH when it is a bare name (Windows: .exe before .cmd), else as given. */
export function resolveProgram(name: string, env: NodeJS.ProcessEnv = process.env, plat: NodeJS.Platform = process.platform): string {
  if (plat !== "win32" || /[\\/]/.test(name)) return name // POSIX: spawn's own execvp lookup is the right one
  return which(name, env, { plat }) ?? name
}

// ── who listens on a port ───────────────────────────────────────────────────

/** Inodes of the sockets listening on `port` in a /proc/net/tcp{,6} table (state 0A is LISTEN). */
export function listenInodes(table: string, port: number): string[] {
  const hex = port.toString(16).toUpperCase().padStart(4, "0")
  const out: string[] = []
  for (const line of table.split("\n").slice(1)) {
    const f = line.trim().split(/\s+/)
    if (f.length < 10 || f[3] !== "0A") continue
    if (f[1].split(":").pop()?.toUpperCase() === hex) out.push(f[9])
  }
  return out
}

/**
 * Pids listening on `port` in `netstat -ano -p TCP` output. The state word is
 * localized, so a listener is told by its foreign address: port 0.
 */
export function netstatListeners(text: string, port: number): number[] {
  const out: number[] = []
  for (const line of text.split(/\r?\n/)) {
    const f = line.trim().split(/\s+/)
    if (f.length < 5 || !/^TCP/i.test(f[0])) continue
    if (f[1].split(":").pop() !== String(port) || f[2].split(":").pop() !== "0") continue
    const pid = Number(f[f.length - 1])
    if (Number.isInteger(pid) && pid > 0 && !out.includes(pid)) out.push(pid)
  }
  return out
}

function procListeners(port: number): number[] | null {
  if (!existsSync("/proc/net/tcp")) return null
  const inodes = new Set<string>()
  for (const t of ["/proc/net/tcp", "/proc/net/tcp6"])
    try {
      for (const i of listenInodes(readFileSync(t, "utf8"), port)) inodes.add(`socket:[${i}]`)
    } catch {
      // No IPv6 table on this kernel.
    }
  if (inodes.size === 0) return []
  const out: number[] = []
  for (const d of readdirSync("/proc").filter((d) => /^\d+$/.test(d))) {
    let fds: string[]
    try {
      fds = readdirSync(`/proc/${d}/fd`)
    } catch {
      continue // someone else's process, or gone
    }
    for (const fd of fds) {
      try {
        if (inodes.has(readlinkSync(`/proc/${d}/fd/${fd}`))) {
          out.push(Number(d))
          break
        }
      } catch {
        // Closed while we looked.
      }
    }
  }
  return out
}

/**
 * Every pid listening on a port — a keeper and its worker share one socket.
 * Null when this machine has no way to say (no lsof, no /proc, no netstat).
 */
export function portListeners(port: number): number[] | null {
  if (isWindows()) {
    const text = sh("netstat", ["-ano", "-p", "TCP"])
    return text === null ? null : netstatListeners(text, port)
  }
  if (!isMacos()) {
    const found = procListeners(port)
    if (found !== null) return found
  }
  const text = sh("lsof", ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"]) // portable: ok — macOS has no /proc; lsof ships with it
  if (text === null) return null
  return text
    .trim()
    .split("\n")
    .map(Number)
    .filter((n) => Number.isInteger(n) && n > 0)
}

/** Whether a port can be bound right now — the answer even where no tool can name the holder. */
export function portBindable(port: number, host = "127.0.0.1"): boolean {
  const probe = `const s=require("net").createServer();s.once("error",()=>process.exit(1));s.listen(${port},${JSON.stringify(host)},()=>s.close(()=>process.exit(0)))`
  const r = spawnSync(process.execPath, ["-e", probe], { env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" }, windowsHide: true, timeout: 5000 })
  return r.status === 0
}

// ── what a pid is running ───────────────────────────────────────────────────

/** A pid's command line, or "" when it cannot be read. */
export function commandOf(pid: number): string {
  if (isWindows()) {
    // PowerShell 5.1 writes redirected stdout in the console's OEM code page: ask it for UTF-8.
    const q = `[Console]::OutputEncoding=[Text.Encoding]::UTF8; (Get-CimInstance Win32_Process -Filter "ProcessId=${Math.trunc(pid)}").CommandLine`
    return (sh("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", q]) ?? "").trim()
  }
  if (!isMacos() && existsSync(`/proc/${pid}/cmdline`)) {
    try {
      return readFileSync(`/proc/${pid}/cmdline`, "utf8").split("\0").filter(Boolean).join(" ")
    } catch {
      return ""
    }
  }
  return (sh("ps", ["-o", "command=", "-p", String(pid)]) ?? "").trim() // portable: ok — macOS has no /proc
}

// ── process trees ───────────────────────────────────────────────────────────

/** The parent pid in a /proc/<pid>/stat line. The command name may hold spaces and parens, so read past its last ")". */
export function ppidOfStat(stat: string): number | null {
  const f = stat.slice(stat.lastIndexOf(")") + 2).split(" ")
  const n = Number(f[1])
  return Number.isInteger(n) ? n : null
}

/** A child → parent table of every process. Windows is not asked: `taskkill /T` walks its own tree. */
function parents(): Map<number, number> {
  const table = new Map<number, number>()
  if (!isMacos() && existsSync("/proc/self/stat")) {
    for (const d of readdirSync("/proc").filter((d) => /^\d+$/.test(d)))
      try {
        const pp = ppidOfStat(readFileSync(`/proc/${d}/stat`, "utf8"))
        if (pp !== null) table.set(Number(d), pp)
      } catch {
        // Gone while we looked.
      }
    return table
  }
  for (const line of (sh("ps", ["-A", "-o", "pid=,ppid="]) ?? "").split("\n")) { // portable: ok — macOS has no /proc
    const [pid, ppid] = line.trim().split(/\s+/).map(Number)
    if (Number.isInteger(pid) && Number.isInteger(ppid)) table.set(pid, ppid)
  }
  return table
}

/** Every process under `root`, from one snapshot. */
export function descendants(root: number): number[] {
  const kids = new Map<number, number[]>()
  for (const [pid, ppid] of parents()) kids.set(ppid, [...(kids.get(ppid) ?? []), pid])
  const out: number[] = []
  const queue = [root]
  while (queue.length) for (const k of kids.get(queue.shift() as number) ?? []) out.push(k), queue.push(k)
  return out
}

/** Kill `root` and everything under it, at once, no chance to clean up. Returns every pid it aimed at. */
export function killTree(root: number): number[] {
  if (isWindows()) {
    sh("taskkill", ["/PID", String(root), "/T", "/F"])
    return [root]
  }
  const all = [root, ...descendants(root)]
  for (const pid of all)
    try {
      process.kill(pid, "SIGKILL")
    } catch {
      // Already gone.
    }
  return all
}
