#!/usr/bin/env node
// cc-shim — the singleton wrapper for the `claude` binary, and the installer for it.
//
// One Node module, no dependencies, the same on macOS, Linux and Windows. Two faces:
//
//   node cc-shim.mjs claude [args…]     the wrapper: what `claude` on PATH runs
//   node cc-shim.mjs install|uninstall|status|doctor|fragment
//
// `cc-shim install` copies this file into the shim folder and writes a tiny stub named
// `claude` beside it (POSIX: a #!/bin/sh that execs node on this file; Windows: claude.cmd),
// then puts that folder first on PATH. There can only be one `claude` on PATH, so anything
// that wants to influence how claude launches drops a fragment into conf.d rather than
// shipping a competing shim.
//
// The one hard guarantee: the wrapper always ends by handing over to the real claude.
// A fragment that errors, hangs, exits, or fails to parse is skipped. Being unable to run
// `claude` is the only unacceptable outcome, which is why the real binary is resolved
// BEFORE any fragment runs.
//
// No brand words here: the few the shim prints come from _brand.sh, generated beside it.

import { spawn, spawnSync } from "node:child_process"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"
import { Worker } from "node:worker_threads"

const IS_WIN = process.platform === "win32"

function realpathOf(p) {
  try {
    return fs.realpathSync(p)
  } catch {
    return path.resolve(p)
  }
}

const SELF = realpathOf(fileURLToPath(import.meta.url))
const SELF_DIR = path.dirname(SELF)
const NAME = "cc-shim"
const TAG = "# cc-shim"
const FRAGMENT_TIMEOUT_S = 5

// ------------------------------------------------------------------ dirs
// A copy of scripts/seam/platform.ts's configDir/dataDir: this file is installed alone, so
// it cannot import the seam. Keep the two in step.
function envPath(name) {
  const v = process.env[name]
  if (!v) return undefined
  return v === "~" || v.startsWith("~/") || v.startsWith("~\\") ? path.join(os.homedir(), v.slice(1)) : v
}

function xdg(name) {
  const v = process.env[name] // the XDG spec: a relative value is ignored
  return v && path.isAbsolute(v) ? v : undefined
}

export function configDir() {
  if (IS_WIN) return envPath("APPDATA") ?? path.join(os.homedir(), "AppData", "Roaming")
  return xdg("XDG_CONFIG_HOME") ?? path.join(os.homedir(), ".config") // portable: ok — the seam's POSIX default
}

export function dataDir() {
  if (IS_WIN) return envPath("LOCALAPPDATA") ?? path.join(os.homedir(), "AppData", "Local")
  return xdg("XDG_DATA_HOME") ?? path.join(os.homedir(), ".local", "share") // portable: ok — the seam's POSIX default
}

/** The shim's own folder (the wrapper, this file, the stubs) and its config folder (conf.d, the system prompt). */
export const shimDir = () => path.join(dataDir(), NAME)
export const cfgDir = () => path.join(configDir(), NAME)
export const confDir = () => process.env.CC_SHIM_CONFD || path.join(cfgDir(), "conf.d")
const installedConfDir = () => path.join(cfgDir(), "conf.d")
const wrapperPath = (dir = shimDir()) => path.join(dir, IS_WIN ? "claude.cmd" : "claude")

// ------------------------------------------------------------------ small helpers
const warn = (m) => {
  if (process.env.CC_SHIM_DEBUG) fs.writeSync(2, `${NAME}: ${m}\n`)
}
// fs.writeSync, never process.stderr, on the launch path: the stream would put the shared
// descriptor into non-blocking mode, and the real claude inherits it.
const errln = (m) => fs.writeSync(2, `${m}\n`)

const sameFile = (a, b) => (IS_WIN ? a.toLowerCase() === b.toLowerCase() : a === b)

function isFile(p) {
  try {
    return fs.statSync(p).isFile()
  } catch {
    return false
  }
}

function isExecFile(p, plat = process.platform) {
  if (!isFile(p)) return false
  if (plat === "win32") return true
  try {
    fs.accessSync(p, fs.constants.X_OK)
    return true
  } catch {
    return false
  }
}

function pathVar(env) {
  if (env.PATH !== undefined) return env.PATH
  const k = Object.keys(env).find((n) => n.toUpperCase() === "PATH")
  return k ? env[k] : ""
}

/** The file names `name` answers to: itself on POSIX, name + each PATHEXT extension on Windows. */
export function commandNames(name, { env = process.env, plat = process.platform } = {}) {
  if (plat !== "win32") return [name]
  const exts = (env.PATHEXT || ".COM;.EXE;.BAT;.CMD").split(";").filter(Boolean)
  return path.extname(name) ? [name] : exts.map((e) => name + e.toLowerCase())
}

/** Every executable `name` on PATH, in order — PATHEXT on Windows, the path delimiter of the OS. */
export function whichAll(name, { env = process.env, plat = process.platform, exec = isExecFile } = {}) {
  const P = plat === "win32" ? path.win32 : path.posix
  const out = []
  for (let d of pathVar(env).split(P.delimiter)) {
    if (!d) {
      if (plat === "win32") continue
      d = "." // POSIX: an empty PATH entry is the current directory
    }
    for (const n of commandNames(name, { env, plat })) {
      const cand = P.join(d, n)
      if (exec(cand, plat)) out.push(cand)
    }
  }
  return out
}

export const which = (name, opts) => whichAll(name, opts)[0] ?? null

// ------------------------------------------------------------------ resolve the real claude
/** Where a standard install puts claude, for a stripped PATH (cron, launchd, a service). */
function fallbackCandidates(env, plat) {
  const home = plat === "win32" ? env.USERPROFILE || os.homedir() : env.HOME || os.homedir()
  if (plat !== "win32") return [path.posix.join(home, ".local", "bin", "claude")]
  const W = path.win32
  const c = [W.join(home, ".local", "bin", "claude.exe")]
  if (env.LOCALAPPDATA) c.push(W.join(env.LOCALAPPDATA, "Microsoft", "WinGet", "Links", "claude.exe"))
  if (env.APPDATA) c.push(W.join(env.APPDATA, "npm", "claude.cmd"))
  return c
}

/**
 * The claude to hand over to. Order: $CC_SHIM_REAL → the first `claude` on PATH that is not
 * us → the standard install location. Two recursion guards: skip any PATH dir that IS our
 * dir, and skip any candidate whose realpath is one of ours (a symlink pointing back at us
 * from elsewhere).
 */
export function resolveReal({
  env = process.env,
  plat = process.platform,
  selfDir = SELF_DIR,
  exec = isExecFile,
  real = realpathOf,
} = {}) {
  if (env.CC_SHIM_REAL && exec(env.CC_SHIM_REAL, plat)) return env.CC_SHIM_REAL
  const P = plat === "win32" ? path.win32 : path.posix
  const eq = (a, b) => (plat === "win32" ? a.toLowerCase() === b.toLowerCase() : a === b)
  const selfDirReal = real(selfDir)
  const ours = ["claude", "claude.cmd", "cc-shim", "cc-shim.cmd", "cc-shim.mjs"].map((n) => real(P.join(selfDir, n)))
  const mine = (cand) => ours.some((o) => eq(o, real(cand)))
  for (const cand of whichAll("claude", { env, plat, exec })) {
    if (eq(real(P.dirname(cand)), selfDirReal)) continue
    if (mine(cand)) continue
    return cand
  }
  for (const cand of fallbackCandidates(env, plat)) if (exec(cand, plat) && !mine(cand)) return cand
  return null
}

// ------------------------------------------------------------------ --account / --prefer
// `claude --account [NAME]` — choose the login for THIS launch. Consumed here and never
// forwarded; the real claude has no such flag. `--prefer NAME` is the same selection with the
// opposite failure mode:
//
//   --account NAME   that account or nothing. Never rotates. Refuses to launch if it could not
//                    route — you named it, so spending someone else's quota is worse than not starting.
//   --prefer  NAME   start there, then roll onto the other accounts as quota runs out. Fails
//                    open: unroutable just means normal rotation.
//
// Accepted at ANY position up to a `--`, as `--account NAME` or `--account=NAME` (also `--acct`).
// A launcher that presets flags (`exec claude --agent X "$@"`) appends the user's arguments, so
// the flag lands mid-argv. Scanning cannot corrupt a working invocation: the real claude rejects
// an unknown `--account` wherever it sits. Literal `--account` prompt text goes after `--`, which
// stops the scan; everything past it is forwarded byte-for-byte.
//
// ponytail: a value-taking claude flag whose value is literally `--account`
// (`--append-system-prompt --account`) is mis-consumed; the `=` form is unaffected.
export function parseAccount(args) {
  let seen = false, name = "", want = false, rest = false, soft = false
  const argv = []
  for (const a of args) {
    if (rest) { argv.push(a); continue }
    // A pending name is resolved before the token can be kept, or a flag following a bare
    // `--account` would be swallowed as its value.
    if (want) {
      want = false
      if (a === "--") { rest = true; argv.push(a) }
      else if (a.startsWith("-")) argv.push(a)
      else name = a
      continue
    }
    // Last one wins if both are given: the same choice under two failure modes.
    if (a === "--") { rest = true; argv.push(a) }
    else if (a.startsWith("--account=") || a.startsWith("--acct=")) { seen = true; soft = false; name = a.slice(a.indexOf("=") + 1) }
    else if (a === "--account" || a === "--acct") { seen = true; soft = false; name = ""; want = true }
    else if (a.startsWith("--prefer=")) { seen = true; soft = true; name = a.slice(a.indexOf("=") + 1) }
    else if (a === "--prefer") { seen = true; soft = true; name = ""; want = true }
    else argv.push(a)
  }
  return { seen, name, soft, argv }
}

/** BRAND_* words from the _brand.sh generated beside the shim (installed or in the repo). */
export function brandWords(dir = SELF_DIR) {
  try {
    const text = fs.readFileSync(path.join(dir, "_brand.sh"), "utf8")
    const words = {}
    for (const m of text.matchAll(/^BRAND_([A-Z_]+)="([^"]*)"/gm)) words[m[1]] = m[2]
    return words
  } catch {
    return null
  }
}

// ------------------------------------------------------------------ fragments
// Each fragment runs contained — a child shell for *.sh, a worker thread for *.mjs — and only
// the environment it leaves behind is imported. That containment is what makes the fail-open
// guarantee true: whatever a fragment does to its process, it does to one we discard.
//
//   * `exit N` / process.exit(), a syntax error, a thrown error — nothing that fragment set is
//     imported. All-or-nothing.
//   * `set -e` plus a failing command (sh) — errexit is suppressed, because the source runs as
//     the left side of a `|| true` list. The fragment keeps going and its exports still apply.
//   * a hang — killed at the 5 s cap; nothing imported.
//
// *.sh runs on macOS and Linux only (it needs a POSIX shell); *.mjs runs everywhere.
const SKIP_NAMES = new Set(["PWD", "OLDPWD", "SHLVL", "_", "BASHOPTS", "SHELLOPTS", "BASH_VERSINFO", "EUID", "UID", "PPID", "_cc_shim_node"])

export function fragmentKinds(plat = process.platform) {
  return plat === "win32" ? [".mjs"] : [".sh", ".mjs"]
}

/** The fragment files in `dir` this OS runs, in lexical order. */
export function listFragments(dir, plat = process.platform) {
  let names
  try {
    names = fs.readdirSync(dir)
  } catch {
    return []
  }
  const kinds = fragmentKinds(plat)
  return names.filter((n) => kinds.includes(path.extname(n))).sort()
}

function timeoutMs() {
  const s = Number(process.env.CC_SHIM_FRAGMENT_TIMEOUT)
  return (Number.isFinite(s) && s > 0 ? s : FRAGMENT_TIMEOUT_S) * 1000
}

// How the child shell hands its environment back, NUL-delimited: `env -0` (GNU, BSD), else
// `printenv -0`, else node itself — busybox's env and printenv may have no -0 (Alpine), and a
// dump that silently yields nothing would import nothing. The marker says the fragment finished,
// so a dump that failed is told apart from a fragment that exited.
const DUMP_MARK = "\0__cc_shim_dumped__\0"
const NODE_DUMP = 'for(const[k,v]of Object.entries(process.env))process.stdout.write(k+"="+v+"\\0")'
const shDumpScript = (source) =>
  `_cc_shim_node=$2; ${source}; export CC_SHIM_CLAIM CC_SHIM_UNSET 2>/dev/null; printf '\\000__cc_shim_dumped__\\000'; ` +
  `env -0 2>/dev/null || printenv -0 2>/dev/null || "$_cc_shim_node" -e '${NODE_DUMP}'`

// How a *.sh fragment is sourced. Its stdout goes to our stderr, so a stray echo cannot corrupt
// the env stream. `set -e` must not end the shell mid-fragment: bash 5 ignores it inside the
// `||` list, bash 3.2 (macOS's /bin/bash) does not — there a `set` wrapper turns errexit back
// off as soon as it is turned on. (So a fragment's own `set -- …` sets the wrapper's arguments,
// not the fragment's: use variables.) POSIX-mode bash and other shells skip the wrapper.
const SH_SOURCE =
  'if [ -n "$BASH_VERSION" ] && ! shopt -oq posix; then set() { builtin set "$@"; builtin set +e; }; fi; ' +
  '. "$1" 1>&2 || true; unset -f set 2>/dev/null'

/**
 * Whether a *.sh fragment's exports come back on this machine, and by which dump — run for real,
 * on a throwaway fragment, the way a launch runs one. For `doctor`.
 */
export function probeShImport(sh = which("bash") ?? "/bin/sh") {
  let dir
  try {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "cc-shim-probe-"))
    const frag = path.join(dir, "probe.sh")
    fs.writeFileSync(frag, "export CC_SHIM_PROBE=ok\n")
    const r = spawnSync(sh, ["-c", shDumpScript(SH_SOURCE), "_", frag, process.execPath], { encoding: "utf8", timeout: 10000 })
    const text = r.stdout ?? ""
    const at = text.indexOf(DUMP_MARK)
    const ok = at >= 0 && parseEnvDump(text.slice(at + DUMP_MARK.length)).CC_SHIM_PROBE === "ok"
    const has = (cmd) => (spawnSync(sh, ["-c", cmd], { encoding: "utf8", timeout: 5000 }).stdout ?? "").includes("\0")
    const via = has("env -0 2>/dev/null") ? "env -0" : has("printenv -0 2>/dev/null") ? "printenv -0" : "node"
    return { ok, via, sh }
  } catch {
    return { ok: false, via: null, sh }
  } finally {
    if (dir) try { fs.rmSync(dir, { recursive: true, force: true }) } catch {}
  }
}

/** {NAME: value} from a NUL-delimited dump. */
export function parseEnvDump(text) {
  const out = {}
  for (const kv of text.split("\0")) {
    const i = kv.indexOf("=")
    if (i > 0) out[kv.slice(0, i)] = kv.slice(i + 1)
  }
  return out
}

/** The environment a *.sh fragment leaves, as {NAME: value}, or null when it exited, broke or hung. */
function runSh(file) {
  return new Promise((resolve) => {
    const sh = which("bash") ?? "/bin/sh"
    // SH_SOURCE: stdout to our stderr, set -e defused. CC_SHIM_CLAIM/CC_SHIM_UNSET are
    // force-exported so a plain assignment is enough.
    const script = shDumpScript(SH_SOURCE)
    let child
    try {
      // Its own process group, so the cap kills whatever the fragment started, too.
      child = spawn(sh, ["-c", script, "_", file, process.execPath], { env: process.env, stdio: ["ignore", "pipe", "inherit"], detached: true })
    } catch {
      return resolve(null)
    }
    const chunks = []
    let late = false
    const timer = setTimeout(() => {
      late = true
      warn(`${path.basename(file)} timed out after ${timeoutMs() / 1000}s`)
      try { process.kill(-child.pid, "SIGKILL") } catch { child.kill("SIGKILL") }
      resolve(null)
    }, timeoutMs())
    child.stdout.on("data", (d) => chunks.push(d))
    child.on("error", () => { clearTimeout(timer); resolve(null) })
    child.on("close", () => {
      clearTimeout(timer)
      if (late) return
      const text = Buffer.concat(chunks).toString("utf8")
      const at = text.indexOf(DUMP_MARK)
      if (at < 0) return resolve(null) // the fragment exited: nothing of it, by design
      const out = parseEnvDump(text.slice(at + DUMP_MARK.length))
      // Finished, yet no environment came back: say so — never a silent empty import.
      if (!Object.keys(out).length) errln(`${NAME}: ${path.basename(file)} ran, but its environment could not be read back (no env -0, printenv -0 or node) — nothing of it applied; '${NAME} doctor' checks`)
      resolve(Object.keys(out).length ? out : null)
    })
  })
}

// The worker imports the fragment, then posts its whole process.env back. A worker's
// process.exit() ends the worker, not us; a thrown error ends it before the post.
const WORKER_BOOT = `
const { parentPort, workerData } = require("node:worker_threads")
import(workerData.url).then(
  () => parentPort.postMessage({ ...process.env }),
  (e) => { process.stderr.write("cc-shim: skipped " + workerData.name + ": " + ((e && e.message) || e) + "\\n") },
)`

/** The environment a *.mjs fragment leaves, as {NAME: value}, or null when it exited, threw or hung. */
function runMjs(file) {
  return new Promise((resolve) => {
    let w
    try {
      w = new Worker(WORKER_BOOT, {
        eval: true,
        workerData: { url: pathToFileURL(file).href, name: path.basename(file) },
        stdout: true, // its stdout joins our stderr, as a sh fragment's does
        stderr: true,
      })
    } catch {
      return resolve(null)
    }
    const fwd = (d) => { try { fs.writeSync(2, d) } catch {} }
    w.stdout.on("data", fwd)
    w.stderr.on("data", fwd)
    let done = false
    const finish = (v) => {
      if (done) return
      done = true
      clearTimeout(timer)
      resolve(v)
      // A fragment may leave a timer or a keep-alive socket behind; it has said its piece.
      w.terminate().catch(() => {})
    }
    const timer = setTimeout(() => {
      warn(`${path.basename(file)} timed out after ${timeoutMs() / 1000}s`)
      finish(null)
    }, timeoutMs())
    w.on("message", (env) => finish(env && typeof env === "object" ? env : null))
    w.on("error", () => finish(null))
    w.on("exit", () => finish(null))
    w.unref()
  })
}

export function runFragment(file) {
  return file.endsWith(".mjs") ? runMjs(file) : runSh(file)
}

/** Put what a fragment left into our environment: every sane name, but the shell's bookkeeping. */
function importEnv(out) {
  for (const [name, value] of Object.entries(out)) {
    if (name.startsWith("BASH_FUNC_") || SKIP_NAMES.has(name)) continue
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) continue
    if (typeof value === "string") process.env[name] = value
  }
}

/** Apply CC_SHIM_UNSET; true when CC_SHIM_CLAIM says later fragments must not run. */
function afterFragment() {
  if (process.env.CC_SHIM_UNSET) {
    warn(`unset: ${process.env.CC_SHIM_UNSET}`)
    for (const n of process.env.CC_SHIM_UNSET.split(/\s+/).filter(Boolean)) delete process.env[n]
    delete process.env.CC_SHIM_UNSET
  }
  if (process.env.CC_SHIM_CLAIM) {
    warn(`claimed by ${process.env.CC_SHIM_CLAIM} — later fragments skipped`)
    return true
  }
  return false
}

export async function runFragments(dir = confDir()) {
  if (process.env.CC_SHIM_DISABLE) return warn("disabled via CC_SHIM_DISABLE")
  for (const name of listFragments(dir)) {
    const file = path.join(dir, name)
    try {
      fs.accessSync(file, fs.constants.R_OK)
    } catch {
      warn(`skip ${name}: not readable`)
      continue
    }
    warn(`running ${name}`)
    const out = await runFragment(file)
    if (out) importEnv(out)
    if (afterFragment()) break
  }
}

// ------------------------------------------------------------------ handing over to claude
// cmd.exe quoting, for a batch file we cannot see through (cross-spawn's rules, which have been
// argued over for years): backslashes before a quote doubled, the quote escaped, the whole
// argument quoted, then every cmd metacharacter caret-escaped — twice for a batch file, because
// it re-parses the %* it forwards.
const CMD_META = /([()\][%!^"`<>&|;, *?])/g

export function quoteCmdArg(arg, batch = true) {
  let a = `${arg}`
  a = a.replace(/(?=(\\+?)?)\1"/g, '$1$1\\"')
  a = a.replace(/(?=(\\+?)?)\1$/, "$1$1")
  a = `"${a}"`
  a = a.replace(CMD_META, "^$1")
  if (batch) a = a.replace(CMD_META, "^$1")
  return a
}

export function quoteCmdCommand(file) {
  return path.win32.normalize(file).replace(CMD_META, "^$1")
}

/**
 * An npm-style claude.cmd is two lines of batch around one real target — a script run by node,
 * or a native claude.exe. Seeing through it means no cmd.exe at all: no quoting to get wrong,
 * no "Terminate batch job?" prompt. Returns {command, args} or null when the file is not one.
 */
export function seeThroughCmd(file, text) {
  let body = text
  if (body === undefined) {
    try {
      body = fs.readFileSync(file, "utf8")
    } catch {
      return null
    }
  }
  const W = path.win32
  const dir = W.dirname(file)
  // The target is the last quoted %dp0%-relative program; `"%dp0%\node.exe"` is the interpreter
  // npm's shim prefers when one sits beside it, never the target.
  const hits = [...body.matchAll(/"%~?dp0%?\\([^"%]+\.(?:c?js|mjs|exe))"/gi)].map((m) => m[1]).filter((p) => !/^node\.exe$/i.test(p))
  if (!hits.length) return null
  const target = W.join(dir, hits[hits.length - 1])
  if (/\.exe$/i.test(target)) return { command: target, args: [] }
  const localNode = W.join(dir, "node.exe")
  return { command: isFile(localNode) ? localNode : process.execPath, args: [target] }
}

/** How to start `real` with `args` on this OS: {command, args, options}. Throws on what cmd.exe cannot carry. */
export function launchPlan(real, args, plat = process.platform) {
  if (plat !== "win32" || !/\.(cmd|bat)$/i.test(real)) return { command: real, args, options: {} }
  const seen = seeThroughCmd(real)
  if (seen && isFile(seen.args[0] ?? seen.command)) return { command: seen.command, args: [...seen.args, ...args], options: {} }
  if (args.some((a) => /[\r\n]/.test(a)))
    throw new Error(`${real} is a batch file, and cmd.exe cannot pass an argument with a line break — set CC_SHIM_REAL to claude.exe`)
  const line = [quoteCmdCommand(real), ...args.map((a) => quoteCmdArg(a, true))].join(" ")
  return {
    command: process.env.ComSpec || "cmd.exe",
    args: ["/d", "/s", "/c", `"${line}"`],
    options: { windowsVerbatimArguments: true },
  }
}

function handOver(real, args) {
  warn(`exec ${real}`)
  // macOS and Linux: a real exec (node >= 22.15), so claude keeps this pid and gets every
  // signal first-hand, exactly as a shell `exec` would give it.
  // CC_SHIM_NO_EXEC=1 takes the child-process road, the one Windows and older node take.
  if (!IS_WIN && typeof process.execve === "function" && !process.env.CC_SHIM_NO_EXEC) {
    try {
      process.execve(real, [real, ...args], { ...process.env })
    } catch (e) {
      warn(`execve failed (${e.message}); running it as a child instead`)
    }
  }
  let plan
  try {
    plan = launchPlan(real, args)
  } catch (e) {
    errln(`${NAME}: ${e.message}`)
    process.exit(127)
  }
  const child = spawn(plan.command, plan.args, { stdio: "inherit", env: process.env, ...plan.options })
  // Ctrl-C reaches claude straight from the terminal (the whole foreground group, or the whole
  // console on Windows): forwarding it would deliver it twice. What is sent to us alone is passed on.
  const ignore = () => {}
  const relay = (sig) => () => { try { child.kill(sig) } catch {} }
  const handlers = IS_WIN
    ? [["SIGINT", ignore], ["SIGBREAK", ignore]]
    : [["SIGINT", ignore], ["SIGQUIT", ignore], ["SIGTERM", relay("SIGTERM")], ["SIGHUP", relay("SIGHUP")]]
  for (const [s, h] of handlers) process.on(s, h)
  child.on("error", (e) => {
    errln(`${NAME}: cannot run ${real}: ${e.message}`)
    process.exit(127)
  })
  child.on("exit", (code, sig) => {
    if (sig) {
      for (const [s, h] of handlers) process.off(s, h)
      try { process.kill(process.pid, sig) } catch {}
      setTimeout(() => process.exit(128 + (os.constants.signals[sig] ?? 1)), 100)
      return
    }
    process.exit(code ?? 1)
  })
}

// ------------------------------------------------------------------ the wrapper
async function claudeMain(args) {
  delete process.env.CC_SHIM_NODE // the Windows stub's own variable
  const real = resolveReal()
  if (!real) {
    errln(`${NAME}: cannot find the real claude binary on PATH`)
    errln("  set CC_SHIM_REAL=/path/to/claude, or reinstall Claude Code.")
    process.exit(127)
  }
  // Introspection for `status`/`doctor`: what we WOULD run, and stop — before fragments, so a
  // broken conf.d cannot affect it.
  if (process.env.CC_SHIM_PRINT_REAL) {
    fs.writeSync(1, `${real}\n`)
    process.exit(0)
  }

  const acct = parseAccount(args)
  if (acct.seen) {
    args = acct.argv
    let name = acct.name
    if (!name) {
      // The only place the wrapper names a router: an empty NAME means asking the human, and
      // fragments run capped with stdout captured, which is no place for a prompt.
      // The gateway's account list: cc-gateway installed on its own, else the kit's `<CLI> gateway`.
      const brand = brandWords()
      const standalone = which("cc-gateway")
      const cli = standalone ?? (brand?.CLI ? which(brand.CLI) : null)
      if (!cli) {
        errln(`claude --account: needs cc-gateway${brand?.CLI ? ` or ${brand.CLI}` : ""} (the gateway's account list) to pick from`)
        process.exit(127)
      }
      // Windows: the first match may be a batch file (an npm-style or hand-made ak.cmd), and spawn
      // refuses one without cmd.exe — launchPlan sees through it or quotes for it.
      let plan
      try {
        plan = launchPlan(cli, [...(standalone ? [] : ["gateway"]), "account", "choose"])
      } catch (e) {
        errln(`claude --account: ${e.message}`)
        process.exit(127)
      }
      const r = spawnSync(plan.command, plan.args, { stdio: ["inherit", "pipe", "inherit"], encoding: "utf8", ...plan.options })
      if (r.status !== 0) process.exit(r.status ?? 1)
      name = (r.stdout ?? "").trim()
    }
    process.env.CC_SHIM_ACCOUNT = name
    if (acct.soft) process.env.CC_SHIM_ACCOUNT_SOFT = "1"
    else delete process.env.CC_SHIM_ACCOUNT_SOFT
    // A parent claude session exports CC_SHIM_ROUTED; routed-or-refuse must read only what a
    // fragment sets on THIS launch.
    delete process.env.CC_SHIM_ROUTED
  }

  await runFragments()

  // The one deliberate exception to fail-open: you named an account, and launching on a
  // different one would quietly spend its quota. --prefer is exempt — rotating is what it asked for.
  if (process.env.CC_SHIM_ACCOUNT && !process.env.CC_SHIM_ACCOUNT_SOFT && !process.env.CC_SHIM_ROUTED) {
    errln(`claude --account: could not route to '${process.env.CC_SHIM_ACCOUNT}' — refusing to launch on another account`)
    process.exit(1)
  }

  // The system prompt file (`ak sysprompt`): APPENDED by default — replacing the system prompt
  // throws the harness prompt away with it. Prepended, so a flag the user typed still wins under
  // last-one-wins parsing, and harmless in front of a subcommand. Missing or empty: nothing.
  const sp = process.env.CC_SYSPROMPT_FILE || path.join(cfgDir(), "system-prompt.md")
  try {
    const st = fs.statSync(sp)
    if (st.isFile() && st.size > 0) {
      warn(`system prompt: ${sp}`)
      args = [process.env.CC_SYSPROMPT_FLAG || "--append-system-prompt-file", sp, ...args]
    }
  } catch {}

  handOver(real, args)
}

// ================================================================== the installer
const say = (m = "") => console.log(m)
const errp = (m = "") => console.error(m)
const me = () => process.env.CC_SHIM_INVOKED_AS || NAME

function die(m) {
  errp(`${NAME}: ${m}`)
  process.exit(1)
}

// rc files we may append to (SH_RCS) vs. all we ever strip from (ALL_RCS). .zshrc is
// strip-only: PATH belongs in .zshenv, which zsh reads for non-interactive shells too (cron,
// launchd, IDE subshells, SDK subprocesses).
const SH_RCS = [".zshenv", ".profile", ".bashrc", ".bash_profile"]
const ALL_RCS = [...SH_RCS, ".zshrc"]
// The ones install may create when missing, per login shell; every other rc is wired only if it exists.
const CREATE_RCS = ["zsh:.zshenv", "bash:.bashrc", "bash:.profile"]
const fishDir = () => path.join(configDir(), "fish", "conf.d")
const envdFile = () => path.join(configDir(), "environment.d", `${NAME}.conf`)
const legacyGateway = (dir) => path.join(dir, "05-brain-gateway.sh")

/** $HOME/… when p sits under the home folder — the form rc files carry. */
function homeRef(p) {
  const home = os.homedir()
  if (p === home) return "$HOME"
  return p.startsWith(home + path.sep) ? `$HOME/${p.slice(home.length + 1).split(path.sep).join("/")}` : p
}

const shq = (s) => `'${String(s).replace(/'/g, `'\\''`)}'`

/** The one line each rc file gets. */
export const rcLine = (dir = shimDir()) => `. "${homeRef(path.join(dir, "env"))}"  ${TAG}`

/** The POSIX sh PATH loader the rc line sources. Must work under dash. */
export function renderEnv(dir = shimDir()) {
  const d = homeRef(dir)
  return `# cc-shim PATH loader — sourced from ~/.zshenv, ~/.profile, ~/.bashrc and an
# existing ~/.bash_profile by \`cc-shim install\`. POSIX sh; must work under dash.
#
# PATH only, deliberately. Routing (base URLs, tokens) lives in conf.d and is applied to the
# claude process alone, so it never leaks into every process in your session the way an
# exported ANTHROPIC_API_KEY does.
case ":\${PATH}:" in
  *:"${d}":*) ;;
  *) PATH="${d}:$PATH"; export PATH ;;
esac
`
}

/** The POSIX stub: finds node, then runs this module. No node at all still reaches claude. */
export function renderStub(dir, verb, node = process.execPath) {
  const target = path.join(dir, "cc-shim.mjs")
  const hand = verb === "claude"
    ? `# No node at all: the one thing that must never happen is claude not starting.
echo "cc-shim: node not found — running claude without the shim" >&2
[ -n "\${CC_SHIM_REAL:-}" ] && [ -x "$CC_SHIM_REAL" ] && exec "$CC_SHIM_REAL" "$@"
[ -x "$HOME/.local/bin/claude" ] && exec "$HOME/.local/bin/claude" "$@"
echo "cc-shim: and no claude at ~/.local/bin/claude either" >&2
exit 127
`
    : `echo "cc-shim: node not found — it runs on Node >= 22.13" >&2
exit 127
`
  return `#!/bin/sh
# ${verb} — GENERATED by \`cc-shim install\`; the program is cc-shim.mjs beside it.
# This stub only finds node: the node that installed it, else the first one on PATH.
node=${shq(node)}
[ -x "$node" ] || node=$(command -v node 2>/dev/null) || node=
if [ -n "$node" ]; then exec "$node" ${shq(target)} ${verb === "claude" ? "claude " : ""}"$@"; fi
${hand}`
}

/**
 * The Windows stub. setlocal keeps CC_SHIM_NODE out of the caller's console.
 *
 * cmd.exe reads a batch file in the console's OEM code page, line by line, so a node path with
 * a non-ASCII letter (C:\Users\José\…) written as UTF-8 would be misread. Such a stub switches
 * the console to UTF-8 (65001) before the line holding the path — cmd decodes each line as it
 * reaches it — and puts the saved code page back before it exits. An ASCII path, the usual
 * case, gets the plain stub: nothing about the console changes. `%~dp0` is expanded by cmd
 * itself, never decoded from the file, so the shim's own folder needs none of this.
 */
export function renderCmdStub(verb, node = process.execPath) {
  const run = `"%CC_SHIM_NODE%" "%~dp0cc-shim.mjs"${verb === "claude" ? " claude" : ""} %*`
  const head = ["@echo off", `rem ${verb} - GENERATED by cc-shim install; the program is cc-shim.mjs beside it.`, "setlocal"]
  const pick = [`set "CC_SHIM_NODE=${node}"`, 'if not exist "%CC_SHIM_NODE%" set "CC_SHIM_NODE=node"']
  // eslint-disable-next-line no-control-regex
  if (/^[\x00-\x7f]*$/.test(node)) return [...head, ...pick, run, "exit /b %ERRORLEVEL%", ""].join("\r\n")
  return [
    ...head,
    'for /f "tokens=2 delims=:." %%c in (\'chcp\') do set "CC_SHIM_CP=%%c"',
    "chcp 65001 >nul",
    ...pick,
    run,
    'set "CC_SHIM_RC=%ERRORLEVEL%"',
    "if defined CC_SHIM_CP chcp %CC_SHIM_CP% >nul",
    "exit /b %CC_SHIM_RC%",
    "",
  ].join("\r\n")
}

/** The node a stub was written with, or null. */
function stubNode(file) {
  try {
    const t = fs.readFileSync(file, "utf8")
    const m = t.match(/^node='((?:[^']|'\\'')*)'$/m) ?? t.match(/^set "CC_SHIM_NODE=([^"]*)"/m)
    return m ? m[1].replace(/'\\''/g, "'") : null
  } catch {
    return null
  }
}

// Where the payloads live — the repo's shim/ folder: $CC_SHIM_SRC (what `ak shim` passes),
// this file's own folder when run from the checkout, else the .src the last install recorded.
const isSrc = (d) => isFile(path.join(d, "cc-shim.mjs")) && isFile(path.join(d, "_brand.sh")) && fs.existsSync(path.join(d, "conf.d.example"))

export function resolveSrc() {
  for (const c of [process.env.CC_SHIM_SRC, SELF_DIR]) if (c && isSrc(c)) return realpathOf(c)
  try {
    const c = fs.readFileSync(path.join(shimDir(), ".src"), "utf8").trim()
    if (c && isSrc(c)) return c
  } catch {}
  return null
}

/** Rewrite a file in place minus the lines holding any needle (truncating keeps its mode). */
function stripLines(file, needles) {
  let text
  try {
    text = fs.readFileSync(file, "utf8")
  } catch {
    return false
  }
  const lines = text.split("\n")
  const kept = lines.filter((l) => !needles.some((n) => l.includes(n)))
  if (kept.length === lines.length) return false
  fs.writeFileSync(file, kept.join("\n"))
  return true
}

const fileHas = (file, needle) => {
  try {
    return fs.readFileSync(file, "utf8").includes(needle)
  } catch {
    return false
  }
}

function copyFile(from, to, mode) {
  fs.copyFileSync(from, to)
  if (!IS_WIN) fs.chmodSync(to, mode)
}

function writeFile(to, text, mode) {
  fs.writeFileSync(to, text)
  if (!IS_WIN) fs.chmodSync(to, mode)
}

function cmdInstall() {
  const src = resolveSrc()
  if (!src) {
    const repo = brandWords()?.REPO_DIR ?? "<repo>"
    die(`cannot locate the shim's source files.\n  Run node ~/${repo}/shim/cc-shim.mjs install, or set CC_SHIM_SRC=~/${repo}/shim`)
  }
  const dir = shimDir()
  const cfg = cfgDir()
  const confd = installedConfDir()
  try {
    fs.mkdirSync(dir, { recursive: true })
    fs.mkdirSync(confd, { recursive: true })
  } catch (e) {
    die(`cannot create ${dir} / ${confd}: ${e.message}`)
  }
  if (!IS_WIN) {
    try { fs.chmodSync(cfg, 0o700); fs.chmodSync(confd, 0o700) } catch {}
  }

  // COPY, never symlink. A checkout moves, gets a branch switched under it, or is a worktree
  // that is later removed; a dangling symlink on PATH is a dead `claude` everywhere.
  try {
    copyFile(path.join(src, "cc-shim.mjs"), path.join(dir, "cc-shim.mjs"), 0o644)
    copyFile(path.join(src, "_brand.sh"), path.join(dir, "_brand.sh"), 0o644)
    if (IS_WIN) {
      writeFile(path.join(dir, "claude.cmd"), renderCmdStub("claude"), 0o755)
      writeFile(path.join(dir, "cc-shim.cmd"), renderCmdStub("cc-shim"), 0o755)
    } else {
      writeFile(path.join(dir, "claude"), renderStub(dir, "claude"), 0o755)
      writeFile(path.join(dir, "cc-shim"), renderStub(dir, "cc-shim"), 0o755)
      writeFile(path.join(dir, "env"), renderEnv(dir), 0o644)
    }
    fs.writeFileSync(path.join(dir, ".src"), `${src}\n`)
  } catch (e) {
    die(`cannot install into ${dir}: ${e.message}`)
  }
  say(`installed  ${dir}${path.sep}{${IS_WIN ? "claude.cmd,cc-shim.cmd" : "claude,cc-shim,env"},cc-shim.mjs,_brand.sh}`)

  // The gateway's fragment was a .sh before it was native; both claim, so the older one
  // (which sorts first) would win. The gateway rewrites its own on the next enable.
  if (fs.existsSync(path.join(confd, "05-gateway.mjs")) && fs.existsSync(legacyGateway(confd))) {
    fs.rmSync(legacyGateway(confd), { force: true })
    say(`removed    ${legacyGateway(confd)} (the gateway's fragment is 05-gateway.mjs now)`)
  }

  if (IS_WIN) {
    wireWindowsPath()
  } else {
    migrateTeamclaude()
    wireRcs(dir)
    wireFish(dir)
    wireEnvironmentD(dir)
  }

  say("")
  say(IS_WIN
    ? `Next: open a new terminal (the User Path changed), then '${me()} doctor'.`
    : `Next: open a new shell (or 'source ${path.join(dir, "env")}'), then '${me()} doctor'.`)
  if (listFragments(confd).length === 0) say(`conf.d is empty → pure passthrough. Example: ${path.join(src, "conf.d.example")}${path.sep}`)
}

function migrateTeamclaude() {
  const home = os.homedir()
  let hit = false
  for (const rc of ALL_RCS) {
    if (stripLines(path.join(home, rc), ["teamclaude-shim", "# teamclaude shim"])) {
      say(`removed    teamclaude lines from ~/${rc}`)
      hit = true
    }
  }
  const fish = path.join(fishDir(), "teamclaude-shim.fish")
  if (fs.existsSync(fish)) {
    fs.rmSync(fish, { force: true })
    say(`removed    ${fish}`)
    hit = true
  }
  const old = path.join(home, ".local", "share", "teamclaude-shim") // portable: ok — where teamclaude put it
  if (fs.existsSync(old)) {
    for (const f of ["claude", "env", "env.fish"]) fs.rmSync(path.join(old, f), { force: true })
    try { fs.rmdirSync(old) } catch {}
    say("removed    ~/.local/share/teamclaude-shim")
    hit = true
  }
  if (hit) {
    errp("")
    errp("NOTE: teamclaude's shim is gone. ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY")
    errp("      stay exported in THIS shell until you reload it. New shells will")
    errp("      no longer route through the teamclaude proxy.")
  }
}

/** A ~/.bash_profile an earlier install created: nothing in it but the cc-shim line. */
export function onlyOurLine(text) {
  const lines = text.split("\n").filter((l) => l.trim())
  return lines.length > 0 && lines.every((l) => l.includes(TAG))
}

function wireRcs(dir) {
  const home = os.homedir()
  const shell = path.basename(process.env.SHELL || "")
  // Undo what an earlier install did: a ~/.bash_profile holding only our line hides ~/.profile.
  const bp = path.join(home, ".bash_profile")
  try {
    if (onlyOurLine(fs.readFileSync(bp, "utf8"))) {
      fs.rmSync(bp, { force: true })
      say("removed    ~/.bash_profile (only the cc-shim line was in it; bash logins read ~/.profile again)")
    }
  } catch {}
  for (const rc of SH_RCS) {
    const file = path.join(home, rc)
    // Touch a file that exists, or the canonical one for the login shell. Never CREATE
    // ~/.bash_profile: once it exists a bash login shell reads it INSTEAD of ~/.profile, and
    // Debian/Ubuntu's ~/.profile is what puts ~/.local/bin on PATH and sources ~/.bashrc.
    // ~/.profile is the one to create — bash reads it when no .bash_profile/.bash_login is there.
    if (!fs.existsSync(file) && !CREATE_RCS.includes(`${shell}:${rc}`)) continue
    if (fileHas(file, TAG)) continue
    try {
      fs.appendFileSync(file, `\n${rcLine(dir)}\n`)
      say(`wired      ~/${rc}`)
    } catch {
      errp(`cannot write ~/${rc}`)
    }
  }
}

function wireFish(dir) {
  // Only if fish is configured — no fish tree on a machine without fish. conf.d IS fish's
  // drop-in mechanism, so no env.fish indirection.
  if (!fs.existsSync(path.join(configDir(), "fish"))) return
  const d = homeRef(dir)
  try {
    fs.mkdirSync(fishDir(), { recursive: true })
    fs.writeFileSync(path.join(fishDir(), "cc-shim.fish"),
      `${TAG}\nif not contains "${d}" $PATH\n    set -gx PATH "${d}" $PATH\nend\n`)
    say(`wired      ${path.join(fishDir(), "cc-shim.fish")}`)
  } catch {}
}

/** systemd user sessions (Linux): environment.d is read before any shell, so GUI apps see it too. */
export function renderEnvironmentD(dir) {
  return `${TAG} — the claude wrapper first on PATH for systemd user sessions\nPATH=${dir}:\${PATH}\n`
}

function wireEnvironmentD(dir, plat = process.platform) {
  if (plat !== "linux") return
  try {
    fs.mkdirSync(path.dirname(envdFile()), { recursive: true })
    fs.writeFileSync(envdFile(), renderEnvironmentD(dir))
    say(`wired      ${envdFile()} (systemd user sessions, from the next login)`)
  } catch {}
}

// ------------------------------------------------------------------ Windows: the User Path
// PowerShell reads the raw (unexpanded) User Path from the registry and writes it back with its
// %VARS% intact — [Environment]::GetEnvironmentVariable would expand them for good. A throwaway
// SetEnvironmentVariable then broadcasts the change so new terminals see it.
//
// Both ways the value travels as base64 of its UTF-16LE bytes, never as text: PowerShell 5.1
// writes redirected stdout in the console's legacy OEM code page, so C:\Users\José would come
// back with a U+FFFD — and the set would write that corruption into the registry for good.
export const PS_GET = "$k = Get-Item -LiteralPath 'HKCU:\\Environment'; " +
  "$v = [string]$k.GetValue('Path', '', 'DoNotExpandEnvironmentNames'); " +
  "[Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($v)))"
export const PS_SET = "$v = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($env:CC_SHIM_USER_PATH_B64)); " +
  "$k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true); " +
  "$k.SetValue('Path', $v, [Microsoft.Win32.RegistryValueKind]::ExpandString); $k.Close(); " +
  "[Environment]::SetEnvironmentVariable('CC_SHIM_BROADCAST', '1', 'User'); " +
  "[Environment]::SetEnvironmentVariable('CC_SHIM_BROADCAST', $null, 'User')"

/** PowerShell's stdout, or null when it failed. `run` is injected by the tests. */
function powershell(script, env = {}, run = spawnSync) {
  const r = run("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    encoding: "utf8", env: { ...process.env, ...env }, windowsHide: true,
  })
  return r.status === 0 ? (r.stdout ?? "") : null
}

export const toPsB64 = (s) => Buffer.from(s, "utf16le").toString("base64")
export const fromPsB64 = (b) => Buffer.from(b.trim(), "base64").toString("utf16le")

/** The raw User Path (its %VARS% unexpanded), or null when PowerShell could not say. */
export function readUserPath(run) {
  const out = powershell(PS_GET, {}, run)
  if (out === null) return null
  if (!/^[A-Za-z0-9+/=\s]*$/.test(out)) return null // not our base64: refuse rather than write back garbage
  return fromPsB64(out)
}

/** Write the User Path; true when PowerShell did. */
export function writeUserPath(value, run) {
  return powershell(PS_SET, { CC_SHIM_USER_PATH_B64: toPsB64(value) }, run) !== null
}

const winEq = (a, b) => path.win32.normalize(a).replace(/\\+$/, "").toLowerCase() === path.win32.normalize(b).replace(/\\+$/, "").toLowerCase()

/** `list` (a ;-joined Path) with `dir` first and nowhere else; `changed` false when it already was. */
export function prependPathEntry(list, dir) {
  const parts = (list || "").split(";").filter(Boolean)
  if (parts.length && winEq(parts[0], dir) && !parts.slice(1).some((p) => winEq(p, dir))) return { value: list, changed: false }
  return { value: [dir, ...parts.filter((p) => !winEq(p, dir))].join(";"), changed: true }
}

export function removePathEntry(list, dir) {
  const parts = (list || "").split(";").filter(Boolean)
  const kept = parts.filter((p) => !winEq(p, dir))
  return { value: kept.join(";"), changed: kept.length !== parts.length }
}

export function wireWindowsPath(dir = shimDir(), run) {
  const cur = readUserPath(run)
  if (cur === null) return errp(`could not read the User Path — add ${dir} to it first, by hand`)
  const next = prependPathEntry(cur, dir)
  if (!next.changed) return say(`wired      User Path (already first: ${dir})`)
  if (!writeUserPath(next.value, run)) return errp(`could not write the User Path — add ${dir} to it first, by hand`)
  say(`wired      User Path (${dir} first) — open a new terminal to pick it up`)
}

export function unwireWindowsPath(dir = shimDir(), run) {
  const cur = readUserPath(run)
  if (cur === null) return
  const next = removePathEntry(cur, dir)
  if (next.changed && writeUserPath(next.value, run)) say(`unwired    User Path (${dir})`)
}

// ------------------------------------------------------------------ uninstall
function cmdUninstall(args) {
  const purge = args[0] === "--purge"
  const home = os.homedir()
  const dir = shimDir()
  if (IS_WIN) {
    unwireWindowsPath()
  } else {
    // A ~/.bash_profile of ours alone goes, not left empty: an empty one still hides ~/.profile.
    const bp = path.join(home, ".bash_profile")
    try {
      if (onlyOurLine(fs.readFileSync(bp, "utf8"))) { fs.rmSync(bp, { force: true }); say("removed    ~/.bash_profile (it held only the cc-shim line)") }
    } catch {}
    for (const rc of ALL_RCS) {
      if (stripLines(path.join(home, rc), [TAG, "teamclaude-shim", "# teamclaude shim"])) say(`unwired    ~/${rc}`)
    }
    const fish = path.join(fishDir(), "cc-shim.fish")
    if (fs.existsSync(fish)) { fs.rmSync(fish, { force: true }); say(`unwired    ${fish}`) }
    if (fs.existsSync(envdFile())) { fs.rmSync(envdFile(), { force: true }); say(`unwired    ${envdFile()}`) }
  }
  for (const f of ["claude", "claude.cmd", "cc-shim", "cc-shim.cmd", "cc-shim.mjs", "env", "_brand.sh", ".src"])
    fs.rmSync(path.join(dir, f), { force: true })
  try { fs.rmdirSync(dir) } catch {}
  say(`removed    ${dir}`)
  if (purge) {
    fs.rmSync(cfgDir(), { recursive: true, force: true })
    say(`purged     ${cfgDir()}`)
  } else {
    say(`kept       ${installedConfDir()} (your fragments; --purge to remove)`)
  }
  say("")
  say(IS_WIN ? "Open a new terminal to drop the PATH entry." : "Reload your shell to drop the PATH entry.")
}

// ------------------------------------------------------------------ status
function pathIndex(want) {
  const parts = pathVar(process.env).split(path.delimiter)
  const i = parts.findIndex((d) => d && sameFile(path.resolve(d), path.resolve(want)))
  return i < 0 ? null : i + 1
}

const CLAIM_RX = /CC_SHIM_CLAIM\s*=\s*["'`]?([^\s;"'`]+)/

function claimOf(file) {
  try {
    const m = fs.readFileSync(file, "utf8").match(CLAIM_RX)
    return m ? m[1] : ""
  } catch {
    return ""
  }
}

/** What the installed wrapper would run: asked of the wrapper itself, so its copy is what answers. */
function wrapperResolves(dir) {
  const stub = wrapperPath(dir)
  if (!isExecFile(stub)) return ""
  const env = { ...process.env, CC_SHIM_PRINT_REAL: "1" }
  // Windows: node on the installed module directly — cmd.exe would add nothing but quoting.
  const r = IS_WIN
    ? spawnSync(process.execPath, [path.join(dir, "cc-shim.mjs"), "claude"], { env, encoding: "utf8", timeout: 10000 })
    : spawnSync(stub, [], { env, encoding: "utf8", timeout: 10000 })
  return r.status === 0 ? (r.stdout ?? "").trim() : ""
}

function wiredList() {
  const home = os.homedir()
  const out = []
  if (IS_WIN) {
    const cur = readUserPath()
    if (cur && cur.split(";").some((p) => p && winEq(p, shimDir()))) out.push("User Path")
    return out
  }
  for (const rc of ALL_RCS) if (fileHas(path.join(home, rc), TAG)) out.push(rc)
  if (fs.existsSync(path.join(fishDir(), "cc-shim.fish"))) out.push("fish")
  if (fs.existsSync(envdFile())) out.push("environment.d")
  return out
}

function allFragmentFiles(dir) {
  try {
    return fs.readdirSync(dir).filter((n) => [".sh", ".mjs"].includes(path.extname(n))).sort()
  } catch {
    return []
  }
}

function cmdStatus(args) {
  const json = args[0] === "--json"
  const dir = shimDir()
  const wrapper = wrapperPath(dir)
  const present = isExecFile(wrapper)
  const idx = pathIndex(dir)
  const shadow = which("claude") ?? ""
  const resolved = present ? wrapperResolves(dir) : ""
  const wired = wiredList()
  const confd = confDir()
  const runs = fragmentKinds()
  const frags = allFragmentFiles(confd).map((f) => ({ file: f, claim: claimOf(path.join(confd, f)), runs: runs.includes(path.extname(f)) }))

  if (json) {
    console.log(JSON.stringify({ wrapper, present, pathPos: idx, claude: shadow, real: resolved, wired, confd, fragments: frags }))
    return 0
  }
  say(`wrapper    ${wrapper} ${present ? "(present)" : "(MISSING)"}`)
  say(idx ? `PATH       position ${idx}` : `PATH       not present — ${IS_WIN ? "open a new terminal" : "reload your shell"}`)
  say(`claude     ${shadow || "not found"}`)
  if (resolved) say(`real       ${resolved}`)
  for (const w of wired) say(`wired      ${w.startsWith(".") ? `~/${w}` : w}`)
  say("")
  say(`fragments  ${confd}`)
  for (const f of frags) say(`  ${f.file}${f.claim ? `   claims: ${f.claim}` : ""}${f.runs ? "" : "   (not run on this OS)"}`)
  if (frags.length === 0) say("  (empty → pure passthrough)")
  return 0
}

// ------------------------------------------------------------------ doctor
// Every check goes through emit() so it is in --json too: `ak shim doctor` renders that.
function cmdDoctor(args) {
  const json = args[0] === "--json"
  const checks = []
  let fail = 0
  const emit = (level, msg) => {
    if (level === "FAIL") fail = 1
    if (json) checks.push({ level, msg })
    else say(`${level}  ${msg}`)
  }
  const dir = shimDir()
  const wrapper = wrapperPath(dir)

  let lst = null
  try { lst = fs.lstatSync(wrapper) } catch {}
  if (!lst) emit("FAIL", `wrapper missing — run '${me()} install'`)
  else if (lst.isSymbolicLink()) emit("FAIL", "wrapper is a SYMLINK; it must be a copy (a checkout moves; a symlink would dangle)")
  else if (!isExecFile(wrapper)) emit("FAIL", "wrapper is not executable")
  else emit("PASS", "wrapper is a regular executable file")

  if (lst && !isFile(path.join(dir, "cc-shim.mjs"))) emit("FAIL", `cc-shim.mjs is missing beside the wrapper — run '${me()} install'`)
  const node = lst ? stubNode(wrapper) : null
  if (lst && node !== null) {
    if (isExecFile(node)) emit("PASS", `the wrapper runs on ${node}`)
    else if (which("node")) emit("WARN", `the wrapper's node ${node} is gone; it falls back to ${which("node")} — '${me()} install' pins it again`)
    else emit("FAIL", `the wrapper's node ${node} is gone and no node is on PATH — claude runs without the shim`)
  }

  const shadow = which("claude")
  if (shadow && sameFile(realpathOf(shadow), realpathOf(wrapper))) emit("PASS", "'claude' resolves to the shim")
  else if (!shadow) emit("FAIL", "'claude' not found on PATH at all")
  else emit("FAIL", `'claude' resolves to ${shadow} (PATH pos ${pathIndex(path.dirname(shadow)) ?? "-"}) — shim is at pos ${pathIndex(dir) ?? "-"}`)

  if (isExecFile(wrapper)) {
    const resolved = wrapperResolves(dir)
    if (!resolved) emit("FAIL", "wrapper cannot resolve a real claude")
    else if (sameFile(realpathOf(resolved), realpathOf(wrapper))) emit("FAIL", `RECURSION: wrapper resolves to itself (${resolved})`)
    else emit("PASS", `real claude resolves to ${resolved}`)
  }

  if (!IS_WIN) {
    const home = os.homedir()
    let stale = false
    for (const rc of ALL_RCS) if (fileHas(path.join(home, rc), "teamclaude-shim")) { emit("WARN", `stale teamclaude line in ~/${rc}`); stale = true }
    if (fs.existsSync(path.join(home, ".local", "share", "teamclaude-shim"))) { emit("WARN", "stale ~/.local/share/teamclaude-shim"); stale = true } // portable: ok — teamclaude's own spot
    if (fs.existsSync(path.join(fishDir(), "teamclaude-shim.fish"))) { emit("WARN", `stale ${path.join(fishDir(), "teamclaude-shim.fish")}`); stale = true }
    if (!stale) emit("PASS", "no teamclaude leftovers")
    try {
      if (onlyOurLine(fs.readFileSync(path.join(home, ".bash_profile"), "utf8")))
        emit("WARN", `~/.bash_profile holds only the cc-shim line — an earlier install made it, and bash logins now skip ~/.profile; '${me()} install' removes it`)
    } catch {}
  }

  if (process.env.ANTHROPIC_BASE_URL || process.env.ANTHROPIC_API_KEY) emit("WARN", "ANTHROPIC_BASE_URL/API_KEY set in THIS shell — leftover exports; open a new shell")
  else emit("PASS", "no leaked ANTHROPIC_* in this environment")

  const confd = confDir()
  const files = allFragmentFiles(confd)
  const kinds = fragmentKinds()
  const bash = IS_WIN ? null : which("bash")
  let bad = false
  for (const f of files) {
    const file = path.join(confd, f)
    if (!kinds.includes(path.extname(f))) { emit("WARN", `${f} is not run on this OS — port it to a .mjs fragment`); bad = true; continue }
    try { fs.accessSync(file, fs.constants.R_OK) } catch { emit("WARN", `${f} is not readable`); bad = true; continue }
    const check = f.endsWith(".mjs")
      ? spawnSync(process.execPath, ["--check", file], { encoding: "utf8" })
      : bash ? spawnSync(bash, ["-n", file], { encoding: "utf8" }) : null
    if (check && check.status !== 0) { emit("FAIL", `syntax error in ${f}`); bad = true }
    // NTFS has no mode bits worth reading; on POSIX a fragment may hold tokens.
    if (!IS_WIN && fs.statSync(file).mode & 0o044) { emit("WARN", `${f} is group/world readable — it may hold tokens`); bad = true }
  }
  if (files.includes("05-brain-gateway.sh") && files.includes("05-gateway.mjs"))
    emit("WARN", `05-brain-gateway.sh sorts first and claims, so the native 05-gateway.mjs never runs — '${me()} install' removes the old one`)
  if (files.length === 0) emit("INFO", "conf.d is empty → pure passthrough")
  else if (!bad) emit("PASS", `${files.length} fragment(s) parse and are private`)
  if (!IS_WIN && files.some((f) => f.endsWith(".sh"))) {
    const p = probeShImport()
    if (!p.ok) { emit("FAIL", `.sh fragments cannot be imported on this machine: ${p.sh} ran a probe fragment but its exports did not come back — port them to .mjs`) }
    else if (p.via !== "env -0") emit("INFO", `.sh fragments import through ${p.via} (this machine's env has no -0)`)
    else emit("PASS", ".sh fragments import (env -0)")
  }

  const src = resolveSrc()
  if (src) {
    const same = (n) => {
      try { return fs.readFileSync(path.join(dir, n)).equals(fs.readFileSync(path.join(src, n))) } catch { return false }
    }
    if (same("cc-shim.mjs") && same("_brand.sh")) emit("PASS", `wrapper matches its source (${src})`)
    else emit("WARN", `wrapper is stale vs ${src} — run '${me()} install'`)
  }

  if (IS_WIN) emit("INFO", "the User Path comes after the Machine Path: a claude installed for all users wins over the shim")
  emit("INFO", "an SDK using pathToClaudeCodeExecutable or a bundled binary bypasses PATH, so it bypasses this shim")

  if (json) console.log(JSON.stringify({ checks, fail }))
  return fail
}

// ------------------------------------------------------------------ fragment (debug)
/** Run one fragment exactly as a launch would, and print what it would change, as JSON. */
async function cmdFragment(args) {
  const file = args[0]
  if (!file) {
    errp(`usage: ${me()} fragment <file>`)
    return 64
  }
  const before = { ...process.env }
  const out = await runFragment(path.resolve(file))
  const set = {}
  if (out) {
    for (const [k, v] of Object.entries(out)) {
      if (k.startsWith("BASH_FUNC_") || SKIP_NAMES.has(k) || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(k)) continue
      if (before[k] !== v && k !== "CC_SHIM_UNSET") set[k] = v
    }
  }
  const unset = (out?.CC_SHIM_UNSET ?? "").split(/\s+/).filter(Boolean)
  console.log(JSON.stringify({ imported: out !== null, set, unset, claim: out?.CC_SHIM_CLAIM ?? "" }))
  return 0
}

function usage() {
  const m = me()
  return `commands
  ${m} install              install the wrapper, wire PATH, migrate off teamclaude
  ${m} uninstall [--purge]  remove it (--purge also deletes your conf.d fragments)
  ${m} status               this listing
  ${m} doctor               diagnose PATH, recursion, leftovers, fragment syntax
  ${m} fragment <file>      run one fragment as a launch would; print what it sets

Fragments (*.mjs everywhere, *.sh on macOS/Linux) run in lexical order; disable one by
renaming it to *.off.`
}

export async function main(argv) {
  const [cmd, ...rest] = argv
  switch (cmd) {
    case "claude": return claudeMain(rest)
    case "install": return cmdInstall(rest), 0
    case "uninstall": return cmdUninstall(rest), 0
    case "status": return cmdStatus(rest)
    case "doctor": return cmdDoctor(rest)
    case "fragment": return cmdFragment(rest)
    case "help": case "--help": case "-h": say(usage()); return 0
    case undefined: cmdStatus([]); say(""); say(usage()); return 0 // bare: what's going on, then what you can do
    default:
      errp(`${me()}: unknown command '${cmd}'`)
      errp("")
      errp(usage())
      return 1
  }
}

const invoked = process.argv[1] && sameFile(realpathOf(process.argv[1]), SELF)
if (invoked) {
  const code = await main(process.argv.slice(2))
  // The wrapper exits on claude's own exit; the installer's verbs return a code.
  if (process.argv[2] !== "claude") process.exitCode = code ?? 0
}
