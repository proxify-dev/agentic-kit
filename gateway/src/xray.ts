#!/usr/bin/env -S node --experimental-strip-types --no-warnings
/**
 * xray (`cc-gateway xray`; `ak xray` in the kit) — the exact prompt a `claude` launch would send; its model calls never leave the gateway.
 *
 *   cc-gateway xray [--full] [--part system|tools|messages] [--json] [-o NAME] [--vs NAME] claude …
 *
 * Everything from `claude` on runs as typed — through the cc-shim, so its
 * appended prompt is there — on the gateway's normal route (routedEnv), every
 * request carrying `x-brain-origin: xray`, `x-brain-run: <id>` and
 * `x-brain-dry-run: 1`. The gateway answers those itself (dry-run.ts) and keeps
 * each body blob; xray reads the run's rows as they land (trace.ts), prints the
 * main call's body, and counts the rest as side calls. `-p` runs to its end
 * with `--no-session-persistence`; a person's session runs in a pty, is killed
 * at the catch, and what it started under Claude Code's config dir is removed. The story,
 * caveats included: gateway/docs/xray.md.
 */
import { spawn, spawnSync } from "node:child_process"
import type { ChildProcess } from "node:child_process"
import { randomBytes } from "node:crypto"
import { existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path"
import { pathToFileURL } from "node:url"
import { routedEnv } from "./claude-env.ts"
import { loadConfig, paths } from "./config.ts"
import { killTree, resolveProgram, spawnable, which } from "./procs.ts"
import type { SpawnPlan } from "./procs.ts"
import { CLAUDE_DIR, managedSettings } from "./shadows.ts"
import { isMainCall, isModelCall, readBlob, TraceTail } from "./trace.ts"
import type { TraceRow } from "./trace.ts"
import { env as brandEnv, claudeHome, configDir, isMacos, isWindows, pidAlive, posix } from "./brand.ts"
import { command as gw } from "./command.ts"

export type Block = { type?: string; text?: string; [k: string]: unknown }
export type Message = { role?: string; content?: string | Block[] }
export type Tool = { name?: string; type?: string; description?: string; input_schema?: unknown; defer_loading?: boolean; [k: string]: unknown }
export type Body = { model?: string; system?: string | Block[]; tools?: Tool[]; messages?: Message[]; stream?: boolean; [k: string]: unknown }

// ── the command line ────────────────────────────────────────────────────────
export type Part = "system" | "tools" | "messages"
export type Opts = { full: boolean; part: Part | null; json: boolean; out: string | null; vs: string | null; command: string[] }

const USAGE = `usage: ${gw("xray")} [--full] [--part system|tools|messages] [--json] [-o NAME] [--vs NAME] claude …

  the exact prompt a claude launch would send — system prompt, tools, messages. The launch goes to the gateway
  like any routed session, marked dry-run: the gateway records each model call and answers it itself, so none
  leaves it. Everything from \`claude\` on is the launch, exactly as you would type it.

  ${gw("xray")} claude                                        a person's interactive session here
  ${gw("xray")} claude -p "hi"                                a program session (claude -p)
  ${gw("xray")} claude --plugin-dir ./plugins/tracer    any claude flags: --bare, --model, --settings, --agent, …

  --full          the whole text, a separator per section
  --part P        one part only: system · tools · messages
  --json          the raw request body as caught
  -o NAME         save it as NAME (<dataDir>/xray/NAME.json)
  --vs NAME       only what was added, removed or changed against NAME, as line diffs
`

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

/** xray's flags stop at the first word that is not one: that word is the launch. */
export function parseArgs(argv: string[]): Opts | { error: string } | { help: true } {
  const o: Opts = { full: false, part: null, json: false, out: null, vs: null, command: [] }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    const value = (): string | null => (i + 1 < argv.length ? argv[++i] : null)
    if (a === "-h" || a === "--help" || a === "help") return { help: true }
    else if (a === "--full") o.full = true
    else if (a === "--json") o.json = true
    else if (a === "--part") {
      const v = value()
      if (v !== "system" && v !== "tools" && v !== "messages") return { error: `--part takes system, tools or messages${v ? `, not ${v}` : ""}` }
      o.part = v
    } else if (a === "-o" || a === "--vs") {
      const v = value()
      if (!v || !NAME_RE.test(v.replace(/\.json$/, ""))) return { error: `${a} takes a NAME (letters, digits, . _ -)${v ? `, not ${v}` : ""}` }
      if (a === "-o") o.out = v.replace(/\.json$/, "")
      else o.vs = v.replace(/\.json$/, "")
    } else if (a === "--") {
      o.command = argv.slice(i + 1)
      break
    } else if (a.startsWith("-")) {
      return { error: `xray has no ${a} — claude's flags go after \`claude\`` }
    } else {
      o.command = argv.slice(i)
      break
    }
  }
  if (o.command.length === 0) return { error: `no claude command — put it after xray's flags: ${gw("xray")} claude -p "hi"` }
  return o
}

// claude's option arity, for the one question xray asks of its argv: was a prompt given?
const VALUED = new Set([
  "--agent", "--agents", "--append-system-prompt", "--append-system-prompt-file", "--autocompact", "--debug-file", "--effort",
  "--environment", "--fallback-model", "--input-format", "--json-schema", "--max-budget-usd", "--max-turns", "--model", "-n",
  "--name", "--output-format", "--permission-mode", "--permission-prompt-tool", "--permission-prompts", "--plugin-dir",
  "--plugin-url", "--remote-control-session-name-prefix", "--session-id", "--setting-sources", "--settings", "--system-prompt",
  "--system-prompt-file", "--system-prompt-snapshot",
])
const VARIADIC = new Set(["--add-dir", "--allowedTools", "--allowed-tools", "--betas", "--disallowedTools", "--disallowed-tools", "--file", "--mcp-config", "--tools"])
// Optional values take the next word unless it is a flag.
const OPTIONAL = new Set(["-d", "--debug", "--cloud", "--from-pr", "--prompt-suggestions", "--remote-control", "-r", "--resume", "--teleport", "-w", "--worktree"])
// The cc-shim's own, read as it reads them (cc-shim docs/reference.md): to a `--`, `=NAME` or the next word unless that is a flag.
const SHIM_FLAGS = /^--(account|acct|prefer)(=|$)/
const SUBCOMMANDS = new Set([
  "agents", "attach", "auth", "auto-mode", "config", "doctor", "gateway", "import", "install", "logs", "mcp", "migrate-installer",
  "plugin", "plugins", "project", "respawn", "rm", "setup-token", "stop", "kill", "ultrareview", "update", "upgrade",
])
const RESUMING = new Set(["-c", "--continue", "-r", "--resume", "--from-pr", "--teleport"])

export type Launch = {
  /** `-p`: a program's session, run to its end. Otherwise a person's, in a pty. */
  program: boolean
  /** The argv handed to the command — the user's, less the account flags, plus what xray adds. */
  argv: string[]
  placeholder: string | null
  /** The cc-shim's account flags, taken out: under CC_SHIM_DISABLE its routed-or-refuse check would stop the launch. */
  dropped: string[]
  /** `--settings` values: a settings file or JSON the launch reads, checked like the others. */
  settings: string[]
}

export const PLACEHOLDER = "hi"

/** What xray runs for a command: the user's argv, plus a first prompt if none was given. */
export function plan(command: string[]): Launch | { error: string } {
  const args: string[] = []
  const dropped: string[] = []
  for (let i = 1; i < command.length; i++) {
    const a = command[i]
    if (a === "--") {
      args.push(...command.slice(i))
      break
    }
    const m = a.match(SHIM_FLAGS)
    if (!m) args.push(a)
    else if (m[2] === "=" || i + 1 >= command.length || command[i + 1] === "--" || command[i + 1].startsWith("-")) dropped.push(a)
    else dropped.push(a, command[++i])
  }
  let program = false
  let prompt = false
  let persist = true
  let resuming = false
  let forked = false
  let settings: string[] = []
  for (let i = 0; i < args.length; i++) {
    const a = args[i]
    if (a === "--") {
      prompt ||= i + 1 < args.length
      break
    }
    const flag = a.includes("=") && a.startsWith("--") ? a.slice(0, a.indexOf("=")) : a
    if (flag === "-p" || flag === "--print") program = true
    else if (flag === "--no-session-persistence") persist = false
    else if (flag === "--fork-session") forked = true
    else if (flag === "-v" || flag === "--version" || flag === "-h" || flag === "--help")
      return { error: `\`claude ${a}\` sends no prompt — nothing to catch` }
    if (RESUMING.has(flag)) resuming = true
    if (flag === "--settings" && a === flag && i + 1 < args.length) settings = [...settings, args[i + 1]]
    if (flag === "--settings" && a !== flag) settings = [...settings, a.slice(a.indexOf("=") + 1)]
    if (a !== flag) continue // --x=value carries its own value
    if (VALUED.has(a)) i++
    else if (VARIADIC.has(a)) while (i + 1 < args.length && !args[i + 1].startsWith("-")) i++
    else if (OPTIONAL.has(a)) {
      if (i + 1 < args.length && !args[i + 1].startsWith("-")) i++
    } else if (!a.startsWith("-") || a === "-") {
      if (!prompt && SUBCOMMANDS.has(a)) return { error: `\`claude ${a}\` is a subcommand — it sends no session prompt to catch` }
      prompt = true
    }
  }
  if (!program && resuming && !forked)
    return { error: "a resumed session would keep xray's turn in its transcript — add --fork-session, or use -p (xray adds --no-session-persistence)" }
  const argv = [...args]
  // First, not last: a variadic flag at the end (`--add-dir a b`) would take it as a value.
  const placeholder = prompt ? null : PLACEHOLDER
  if (placeholder) argv.unshift(placeholder)
  if (program && persist) {
    // Ahead of a `--`, past which it would be prompt text.
    const end = argv.indexOf("--")
    argv.splice(end >= 0 ? end : argv.length, 0, "--no-session-persistence")
  }
  return { program, argv, placeholder, dropped, settings }
}

// ── the environment ─────────────────────────────────────────────────────────
/**
 * What a Claude Code session exports to the commands it runs. xray run from a
 * Bash tool must not hand them on: the launch would join that session's
 * messaging socket, keep its entrypoint (and so its person-or-program verdict)
 * and its session id — a different prompt from the one a terminal gets.
 */
export const PARENT_SESSION_VARS = [
  "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ATTENDED",
  "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SSE_PORT", "CLAUDE_PID",
  "CLAUDE_EFFORT", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_PROJECT_DIR", "CLAUDE_ENV_FILE", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA",
  "CC_SHIM_CLAIM", "CC_SHIM_ROUTED",
]

/** A routed session's environment — a parent session's markers dropped — every request marked dry-run. */
export function launchEnv(base: string, run: string, from: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...from }
  if (env.CLAUDECODE) for (const k of PARENT_SESSION_VARS) delete env[k]
  return routedEnv(base, ["x-brain-origin: xray", `x-brain-run: ${run}`, "x-brain-dry-run: 1"], env)
}

/**
 * A settings file's `env` wins over the launch environment, so one that sets a
 * base URL or a cloud provider would take the prompt past the gateway — and
 * past the dry run. Checked before anything is launched.
 */
const ROUTING_KEYS = ["ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"]

export function bypasses(cwd: string, settingsArgs: string[] = [], home?: string): string[] {
  const config = process.env.CLAUDE_CONFIG_DIR || !home ? claudeHome() : join(home, CLAUDE_DIR)
  const files = [join(config, "settings.json"), join(config, "settings.local.json")]
  for (let d = resolve(cwd); ; d = dirname(d)) {
    files.push(join(d, CLAUDE_DIR, "settings.json"), join(d, CLAUDE_DIR, "settings.local.json"))
    if (dirname(d) === d) break
  }
  // Every OS's managed file: one that does not exist here is skipped below.
  files.push(...managedSettings("darwin"), ...managedSettings("linux"), ...managedSettings("win32"))
  const found: string[] = []
  const check = (where: string, raw: string) => {
    try {
      const env = (JSON.parse(raw) as { env?: Record<string, unknown> }).env ?? {}
      for (const k of ROUTING_KEYS) if (env[k] !== undefined && env[k] !== "" && env[k] !== "0") found.push(`${where} sets env.${k}`)
    } catch {
      // Claude Code ignores a settings file it cannot parse; so does this check.
    }
  }
  for (const f of new Set(files)) if (existsSync(f)) check(f, readFileSync(f, "utf8"))
  for (const s of settingsArgs) {
    const t = s.trim()
    if (t.startsWith("{")) check("--settings", t)
    else if (existsSync(resolve(cwd, t))) check(`--settings ${t}`, readFileSync(resolve(cwd, t), "utf8"))
  }
  return found
}

// ── the launch ──────────────────────────────────────────────────────────────
/**
 * SIGKILL, the whole tree: claude, its MCP servers, its hooks. Nothing gets to
 * run a SessionEnd hook for a session that never happened. (procs.ts: a /proc
 * or `ps` walk on POSIX, `taskkill /T /F` on Windows.)
 */
async function killAll(root: number): Promise<void> {
  const all = killTree(root)
  const deadline = Date.now() + 3000
  while (Date.now() < deadline && all.some(pidAlive)) await new Promise((r) => setTimeout(r, 50))
}

const ANSI = /\u001b\[[0-9;?<>=]*[ -/]*[@-~]|\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)|\u001b[()][A-Za-z0-9]|\u001b[=>78MDEHc]|\r/g

type Child = { proc: ChildProcess; exited: Promise<number | null>; tail: (n?: number) => string[] }

/** The terminal a person's session gets: wide enough that nothing it prints folds. */
const TTY = { cols: 160, rows: 48 }

export type ScriptFlavor = "bsd" | "util-linux" | "busybox"

/**
 * Which `script` this machine has, or null for none. macOS ships BSD's; Linux
 * has util-linux's, or busybox's on Alpine and friends — an applet that takes
 * `-c` but has no `-e`, so util-linux's `-qec` fails there.
 */
export function scriptFlavor(
  opts: { mac?: boolean; path?: string | null; real?: (p: string) => string; version?: (p: string) => string } = {},
): ScriptFlavor | null {
  if (opts.mac ?? isMacos()) return "bsd"
  const bin = opts.path !== undefined ? opts.path : which("script")
  if (!bin) return null
  const real = opts.real ?? ((p: string) => {
    try {
      return realpathSync(p)
    } catch {
      return p
    }
  })
  if (/^busybox/i.test(basename(real(bin)))) return "busybox"
  const version =
    opts.version ??
    ((p: string) => {
      const r = spawnSync(p, ["--version"], { encoding: "utf8", timeout: 3000 })
      return `${r.stdout ?? ""}${r.stderr ?? ""}`
    })
  return /busybox/i.test(version(bin)) ? "busybox" : "util-linux"
}

/** The `script` command line that runs `$XRAY_COMMAND` (util-linux, busybox) or `/bin/sh -c SIZE "$@"` (BSD) in a pty. */
export function scriptInvocation(flavor: ScriptFlavor, quotedSize: string): string {
  if (flavor === "bsd") return `script -q /dev/null /bin/sh -c ${quotedSize} "$@"`
  if (flavor === "busybox") return `script -q -c "$XRAY_COMMAND" /dev/null`
  return `script -qec "$XRAY_COMMAND" /dev/null`
}

function launch(command: string[], argv: string[], env: NodeJS.ProcessEnv, program: boolean): Child {
  let buf = ""
  const keep = (b: Buffer) => {
    buf = (buf + b.toString("utf8")).slice(-64_000)
  }
  let proc: ChildProcess
  if (program) {
    // Resolved here, not by spawn: on Windows spawn finds a claude.exe only, and a
    // claude.cmd (the shim's, npm's) needs the plan spawnable() makes.
    let plan: SpawnPlan
    try {
      plan = spawnable(resolveProgram(command[0], env), argv)
    } catch (err) {
      plan = { command: command[0], args: argv, options: {} }
      keep(Buffer.from(`\n${(err as Error).message}\n`))
    }
    proc = spawn(plan.command, plan.args, { env, stdio: ["ignore", "pipe", "pipe"], detached: true, windowsHide: true, ...plan.options })
  } else {
    // A person's session needs a terminal; `script` gives it one. Its stdin is
    // a pipe from a `sleep` that never writes: not a tty, and never EOF — macOS
    // `script` refuses a socket or a FIFO for stdin (a Node pipe is a socket),
    // and a closed stdin makes it type ^D into the session. Inside the pty a
    // shell sizes the terminal and execs the command, so `claude` still
    // resolves on PATH — through the cc-shim.
    const tty: NodeJS.ProcessEnv = { ...env, TERM: env.TERM && env.TERM !== "dumb" ? env.TERM : "xterm-256color" }
    const size = `stty cols ${TTY.cols} rows ${TTY.rows} 2>/dev/null; exec "$0" "$@"`
    const q = (x: string) => `'${x.replace(/'/g, `'\\''`)}'`
    const feed = "sleep 2147483647 2>/dev/null"
    // BSD, util-linux and busybox `script` take their command differently; Windows never gets here (main).
    const script = `${feed} | ${scriptInvocation(scriptFlavor() ?? "util-linux", q(size))}`
    tty.XRAY_COMMAND = ["/bin/sh", "-c", size, command[0], ...argv].map(q).join(" ")
    proc = spawn("/bin/sh", ["-c", script, "xray", command[0], ...argv], { env: tty, stdio: ["ignore", "pipe", "pipe"], detached: true })
  }
  proc.stdout?.on("data", keep)
  proc.stderr?.on("data", keep)
  const exited = new Promise<number | null>((r) => {
    proc.on("error", (err) => {
      keep(Buffer.from(`\ncould not launch ${command[0]}: ${err.message}\n`))
      r(127)
    })
    proc.on("exit", (code, signal) => r(code ?? (signal ? 128 : null)))
    // The pipeline's shell outlives the terminal (it waits on the sleep): the
    // terminal closing its output is the session's end.
    if (!program) proc.stdout?.on("end", () => r(null))
  })
  const tail = (n = 12) =>
    buf
      .replace(ANSI, "\n")
      .split("\n")
      .map((l) => l.replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, "").trimEnd())
      .filter((l) => l.trim())
      .slice(-n)
  return { proc, exited, tail }
}

/**
 * What an interactive run leaves under Claude Code's config dir, keyed by its
 * session id: the transcript and its dir, `session-env/<sid>`, `tasks/` and
 * `teams/session-<sid8>`, its live-session registry entry and `.key`, a project
 * dir it created. Only what was created after `since` — an id that already had
 * a file (a resume) is someone's real session. `history.jsonl` is shared; stays.
 */
const claudeConfig = claudeHome
const configRel = (p: string) => {
  const rel = relative(claudeConfig(), p)
  return rel && !rel.startsWith("..") && !isAbsolute(rel) ? posix(rel) : p
}

export function removeSession(sid: string | null, since: number, config = claudeConfig()): string[] {
  if (!sid || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(sid)) return []
  const gone: string[] = []
  const fresh = (p: string) => {
    try {
      const st = statSync(p)
      return (st.birthtimeMs || st.ctimeMs) >= since - 2000
    } catch {
      return false
    }
  }
  const rm = (p: string) => {
    if (!fresh(p)) return
    rmSync(p, { recursive: true, force: true })
    gone.push(p)
  }
  const list = (d: string) => (existsSync(d) ? readdirSync(d) : [])
  const projects = join(config, "projects")
  for (const d of list(projects)) {
    rm(join(projects, d, `${sid}.jsonl`))
    rm(join(projects, d, sid))
    if (fresh(join(projects, d)) && list(join(projects, d)).length === 0) rm(join(projects, d))
  }
  rm(join(config, "session-env", sid))
  rm(join(config, "tasks", `session-${sid.slice(0, 8)}`))
  rm(join(config, "teams", `session-${sid.slice(0, 8)}`))
  const registry = join(config, "sessions")
  for (const f of list(registry).filter((f) => /^\d+\.json$/.test(f))) {
    const p = join(registry, f)
    if (!fresh(p) || !readFileSync(p, "utf8").includes(sid)) continue
    const pid = f.slice(0, -5)
    for (const g of list(registry)) if (g === f || (g.startsWith(`${pid}.`) && g.endsWith(".key"))) rm(join(registry, g))
  }
  return gone
}

// ── the anatomy ─────────────────────────────────────────────────────────────
export type Section = {
  part: Part
  /** Where it sits: `system block 1`, `message 2`, `tools`. */
  where: string
  label: string
  /** The label plus its occurrence — what `--vs` pairs across runs. */
  key: string
  text: string
  chars: number
  deferred?: boolean
}

export type Row = { name: string; count: string; chars: number; sections: Section[]; note: string }

export type Anatomy = { model: string; rows: Row[]; sections: Section[]; chars: number; deferred: string[] }

const HOME = homedir()
const tildify = (p: string) => (p.startsWith(`${HOME}${sep}`) || p.startsWith(`${HOME}/`) ? `~${p.slice(HOME.length)}` : p)
const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)

/** Claude Code's context keys (`# gitStatus`), named as a person would. */
const CONTEXT_KEYS: Record<string, string> = { gitStatus: "git status", currentDate: "date", directoryStructure: "directory structure" }

type Head = { label: string; container?: boolean }

const TAG = /^\[([A-Za-z][\w.:-]*)\](?:\s|$)/

/**
 * Is this line a header, and what does it name? Inside a `Contents of` (a
 * CLAUDE.md, whose own headings are its business) only the lines Claude Code
 * itself writes break out.
 */
function headOf(line: string, inContainer: boolean): Head | null {
  let m: RegExpMatchArray | null
  if ((m = line.match(/^Contents of (.+?) \(/))) return { label: tildify(m[1]), container: true }
  // `SessionStart hook additional context: [tracer] …` — the first plugin's line rides on the header's.
  if ((m = line.match(/^(\w+) hook additional context:\s*(.*)$/))) return { label: m[2].match(TAG) ? `[${m[2].match(TAG)?.[1]}]` : `${m[1]} hook` }
  if ((m = line.match(/^# ([a-z]+[A-Z]\w*)\s*$/))) return { label: CONTEXT_KEYS[m[1]] ?? m[1] }
  if (inContainer) return null
  if ((m = line.match(TAG))) return { label: `[${m[1]}]` }
  if ((m = line.match(/^The following (.+?) (?:are|is|were|have|has)\b(.*)$/))) return { label: /\bfailed\b/.test(m[2]) ? `${m[1]} (failed)` : /\bstill connecting\b/.test(m[2]) ? `${m[1]} (connecting)` : m[1] }
  if (/^Available agent types/.test(line)) return { label: "agent types" }
  if (/^x-anthropic-billing-header:/.test(line)) return { label: "billing header" }
  if (/^You are Claude Code, Anthropic's official CLI/.test(line)) return { label: "identity" }
  if ((m = line.match(/^# (\S.*)$/))) return { label: `# ${clip(m[1].trim(), 40)}` }
  return null
}

/** Lines Claude Code wraps context in: the header after one names the section. */
const WRAPPERS = [
  /^<system-reminder>\s*$/,
  /^As you answer the user's questions, you can use the following context:\s*$/,
  /^Codebase and user instructions are shown below\./,
]
const isWrapper = (l: string) => WRAPPERS.some((re) => re.test(l))
const WRAP_CLOSE = /^<\/system-reminder>\s*$/

/** Text found by content and kept whole as one section: an appended system prompt. */
export type Pin = { label: string; text: string }

type Piece = { label: string; text: string }

/**
 * One text block, cut into sections at the lines that look like headers. The
 * cuts are character offsets, so the sections put back together are the block.
 */
export function splitSections(text: string, pins: Pin[] = []): Piece[] {
  const spans: { at: number; end: number; label: string }[] = []
  for (const p of pins) {
    const t = p.text.trim()
    const at = t ? text.indexOf(t) : -1
    if (at >= 0 && !spans.some((s) => at < s.end && at + t.length > s.at)) spans.push({ at, end: at + t.length, label: p.label })
  }
  spans.sort((a, b) => a.at - b.at)
  const pieces: Piece[] = []
  let pos = 0
  for (const s of spans) {
    pieces.push(...splitPlain(text.slice(pos, s.at)), { label: s.label, text: text.slice(s.at, s.end) })
    pos = s.end
  }
  pieces.push(...splitPlain(text.slice(pos)))
  // Whitespace between sections belongs to the one before it.
  const out: Piece[] = []
  for (const p of pieces) {
    if (!p.text.trim() && out.length) out[out.length - 1].text += p.text
    else if (!p.text.trim()) continue
    else out.push({ ...p })
  }
  if (out.length && pieces.length && !pieces[0].text.trim() && pieces[0].text) out[0].text = pieces[0].text + out[0].text
  return out.length ? out : [{ label: firstLine(text), text }]
}

function splitPlain(text: string): Piece[] {
  if (!text) return []
  const cuts: { at: number; label: string | null }[] = [{ at: 0, label: null }]
  let container = false
  let fence = false
  let wrapped = false // the section so far is only wrapper lines and blanks
  let afterClose = false
  const cut = (at: number, label: string | null) => {
    const last = cuts[cuts.length - 1]
    if (wrapped || last.at === at || !text.slice(last.at, at).trim()) last.label = label
    else cuts.push({ at, label })
    wrapped = false
  }
  let off = 0
  for (const line of text.split("\n")) {
    const start = off
    off += line.length + 1
    const fenceLine = /^\s*(```|~~~)/.test(line)
    if (fenceLine) fence = !fence
    if (fence && !fenceLine) continue
    if (isWrapper(line)) {
      if (!wrapped) cut(start, null)
      wrapped = true
      container = false
      afterClose = false
      continue
    }
    if (WRAP_CLOSE.test(line)) {
      container = false
      afterClose = true
      wrapped = false
      continue
    }
    if (!line.trim()) continue
    const h = headOf(line, container)
    if (h) {
      cut(start, h.label)
      container = !!h.container
      afterClose = false
      continue
    }
    if (afterClose) cut(start, null)
    afterClose = false
    wrapped = false
  }
  return cuts.map((c, i) => {
    const t = text.slice(c.at, i + 1 < cuts.length ? cuts[i + 1].at : text.length)
    return { label: c.label ?? firstLine(t), text: t }
  })
}

function firstLine(t: string): string {
  const line = t.split("\n").find((l) => l.trim() && !isWrapper(l) && !WRAP_CLOSE.test(l)) ?? ""
  return `"${clip(line.trim(), 28)}"`
}

/**
 * What the launch appends to (or puts in place of) the system prompt, by
 * content: the cc-shim's file, and `--append-system-prompt[-file]` /
 * `--system-prompt[-file]` in the command.
 */
export function pinsFor(command: string[], cwd = process.cwd()): Pin[] {
  const read = (f: string): string => {
    try {
      return readFileSync(resolve(cwd, f), "utf8")
    } catch {
      return ""
    }
  }
  const pins: Pin[] = []
  const shim = process.env.CC_SYSPROMPT_FILE ?? join(configDir(), "cc-shim", "system-prompt.md")
  if (existsSync(shim)) pins.push({ label: "appended prompt", text: read(shim) })
  for (let i = 1; i < command.length; i++) {
    const a = command[i]
    if (a === "--") break
    const eq = a.startsWith("--") ? a.indexOf("=") : -1
    const flag = eq > 0 ? a.slice(0, eq) : a
    const val = eq > 0 ? a.slice(eq + 1) : command[i + 1]
    if (typeof val !== "string") continue
    if (flag === "--append-system-prompt" || flag === "--system-prompt") pins.push({ label: flag, text: val })
    if (flag === "--append-system-prompt-file" || flag === "--system-prompt-file") pins.push({ label: flag, text: read(val) })
  }
  return pins.filter((p) => p.text.trim())
}

const blocksOf = (c: string | Block[] | undefined): Block[] => (typeof c === "string" ? [{ type: "text", text: c }] : Array.isArray(c) ? c : [])

/** A tool as text: its description, then the rest of it as JSON. */
function toolText(t: Tool): string {
  const { description, ...rest } = t
  return `${typeof description === "string" ? `${description}\n\n` : ""}${JSON.stringify(rest, null, 2)}\n`
}

/** The deferred-tools list a message may carry: the names after its header line. */
function deferredNames(text: string): string[] {
  const m = text.match(/deferred tools[^\n]*\n([\s\S]*?)(?:\n\s*\n|<\/|$)/i)
  return m ? m[1].split("\n").map((l) => l.trim()).filter((l) => /^[\w.:-]+$/.test(l)) : []
}

export function anatomy(body: Body, pins: Pin[] = []): Anatomy {
  const sections: Section[] = []
  const rows: Row[] = []
  const seen = new Map<string, number>()
  const add = (s: Omit<Section, "key">): Section => {
    const base = `${s.part}:${s.label}`
    const n = (seen.get(base) ?? 0) + 1
    seen.set(base, n)
    const full: Section = { ...s, key: n > 1 ? `${base} #${n}` : base }
    sections.push(full)
    return full
  }
  const textSections = (part: Part, where: string, blocks: Block[], p: Pin[] = []): Section[] => {
    const out: Section[] = []
    for (const b of blocks) {
      if (b.type === "text" && typeof b.text === "string") {
        for (const s of splitSections(b.text, p)) out.push(add({ part, where, label: s.label, text: s.text, chars: s.text.length }))
      } else {
        const text = JSON.stringify(b, null, 2)
        const label = b.type === "tool_use" ? `tool_use ${String(b.name ?? "")}` : `[${String(b.type ?? "block")}]`
        out.push(add({ part, where, label, text, chars: text.length }))
      }
    }
    return out
  }
  const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`

  const system = blocksOf(body.system)
  const sys: Section[] = []
  system.forEach((b, i) => sys.push(...textSections("system", `system block ${i + 1}`, [b], pins)))
  rows.push({ name: "system", count: plural(system.length, "block"), chars: sum(sys), sections: sys, note: "" })

  const tools = Array.isArray(body.tools) ? body.tools : []
  const toolSecs = tools.map((t) => {
    const text = toolText(t)
    return add({ part: "tools", where: "tools", label: String(t.name ?? t.type ?? "tool"), text, chars: text.length, deferred: t.defer_loading === true })
  })
  const loaded = toolSecs.filter((s) => !s.deferred)
  // Deferred: defined with defer_loading, or only named in a message's list —
  // either way not in the model's context until ToolSearch loads it.
  const deferred = new Set(toolSecs.filter((s) => s.deferred && s.label !== "DeferredToolPlaceholder").map((s) => s.label))

  const messages = Array.isArray(body.messages) ? body.messages : []
  const msgRows: Row[] = []
  messages.forEach((m, i) => {
    const blocks = blocksOf(m.content)
    const role = m.role && m.role !== "user" ? m.role : ""
    const secs = textSections("messages", `message ${i + 1}${role ? ` (${role})` : ""}`, blocks)
    for (const s of secs) if (/deferred tools/i.test(s.label)) for (const n of deferredNames(s.text)) deferred.add(n)
    msgRows.push({ name: `message ${i + 1}`, count: plural(blocks.length, "block"), chars: sum(secs), sections: secs, note: role })
  })
  rows.push({ name: "tools", count: String(loaded.length), chars: sum(loaded), sections: loaded, note: deferred.size ? `(+${deferred.size} deferred)` : "" })
  rows.push(...msgRows)
  const chars = rows.reduce((n, r) => n + r.chars, 0)
  return { model: String(body.model ?? "?"), rows, sections, chars, deferred: [...deferred] }
}

const sum = (xs: { chars: number }[]) => xs.reduce((n, x) => n + x.chars, 0)

// ── rendering ───────────────────────────────────────────────────────────────
export const size = (n: number): string => (n < 100 ? String(n) : n < 1e6 ? `${(n / 1000).toFixed(1)}K` : `${(n / 1e6).toFixed(1)}M`)
const approxTokens = (chars: number) => `≈ ${size(Math.round(chars / 4))} tokens`

/** A section's short name for the outline: a path by its file name. */
const shortLabel = (l: string) => (l.startsWith("/") || l.startsWith("~/") ? basename(l) : l)

/**
 * The detail column: labels in order, repeats folded (`CLAUDE.md ×2`), with
 * sizes. Too long for the line, it shows sizes only from 1K up, then keeps
 * what it can — a plugin's `[tag]` first, then the biggest — in their order,
 * each gap marked `…`.
 */
function detail(sections: Section[], width: number): string {
  const items: { label: string; n: number; chars: number }[] = []
  for (const s of sections) {
    const label = shortLabel(s.label)
    const last = items[items.length - 1]
    if (last && last.label === label) last.n++, (last.chars += s.chars)
    else items.push({ label, n: 1, chars: s.chars })
  }
  const text = (min: number) => items.map((it) => `${it.label}${it.n > 1 ? ` ×${it.n}` : ""}${it.chars >= min ? ` ${size(it.chars)}` : ""}`)
  const render = (parts: string[], keep: Set<number>) => {
    const out: string[] = []
    parts.forEach((p, i) => {
      if (keep.has(i)) out.push(p)
      else if (out[out.length - 1] !== "…") out.push("…")
    })
    return out.join(" · ")
  }
  const all = new Set(items.map((_, i) => i))
  for (const min of [100, 1000]) if (render(text(min), all).length <= width) return render(text(min), all)
  const parts = text(1000)
  const keep = new Set<number>()
  const rank = (i: number) => (items[i].label.startsWith("[") ? 1e9 : 0) + items[i].chars
  for (const i of [...all].sort((a, b) => rank(b) - rank(a))) {
    keep.add(i)
    if (render(parts, keep).length > width) keep.delete(i)
  }
  return render(parts, keep)
}

/** The biggest tools that fit, then how many more. */
function biggest(sections: Section[], width: number): string {
  const parts = [...sections].sort((x, y) => y.chars - x.chars).map((s) => `${s.label} ${size(s.chars)}`)
  let line = ""
  for (let i = 0; i < parts.length; i++) {
    const next = line ? `${line} · ${parts[i]}` : parts[i]
    if (next.length > width - 8 && line && i < parts.length - 1) return `${line} · … +${parts.length - i}`
    line = next
  }
  return line
}

export type Meta = { program: boolean; requests: number; placeholder: string | null; dropped: string[] }

function headerLine(a: Anatomy, meta: Meta): string {
  const side = meta.requests - 1
  return [
    `  ${meta.program ? "program" : "person"} session`,
    a.model,
    `${meta.requests} model call${meta.requests === 1 ? "" : "s"} dry-run${side > 0 ? ` (${side} side)` : ""}`,
    ...(meta.placeholder ? [`no prompt given — sent "${meta.placeholder}"`] : []),
    ...(meta.dropped.length ? [`${meta.dropped.join(" ")} dropped: a dry run is answered before any account is chosen`] : []),
    "none left the gateway",
  ].join(" · ")
}

export function outline(a: Anatomy, meta: Meta, width = 130): string {
  const lines = [headerLine(a, meta), ""]
  const room = Math.max(40, width - 36)
  for (const r of a.rows) {
    let d: string
    if (r.name === "tools") d = `${biggest(r.sections, room - r.note.length - 3)}${r.note ? `   ${r.note}` : ""}`
    else {
      const lead = r.note ? `as ${r.note}: ` : ""
      d = lead + detail(r.sections, room - lead.length)
    }
    lines.push(`  ${r.name.padEnd(13)}${r.count.padEnd(10)}${size(r.chars).padStart(7)}   ${d}`)
  }
  lines.push(`  ${"total".padEnd(13)}${"".padEnd(10)}${size(a.chars).padStart(7)} chars ${approxTokens(a.chars)}`)
  return `${lines.join("\n")}\n`
}

const rule = (title: string, width = 100) => `── ${title} ${"─".repeat(Math.max(3, width - title.length - 4))}`

/** `full`: every section whole under a rule (one part, or all). Else one part, a line per section. */
export function listing(a: Anatomy, part: Part | null, meta: Meta, full: boolean): string {
  const secs = a.sections.filter((s) => (!part || s.part === part) && (full || !s.deferred))
  const out = [headerLine(a, meta), ""]
  const width = Math.min(60, Math.max(10, ...secs.map((s) => clip(s.label, 60).length)))
  let where = ""
  for (const s of secs)
    if (full) out.push(rule(`${s.where} › ${s.label} · ${size(s.chars)}${s.deferred ? " · deferred" : ""}`), s.text.replace(/\n+$/, ""), "")
    else {
      if (s.where !== where && s.part !== "tools") out.push(`  ${s.where}`)
      where = s.where
      out.push(`    ${clip(s.label, 60).padEnd(width)}  ${size(s.chars).padStart(6)}`)
    }
  if (!full && part === "tools" && a.deferred.length) out.push("", `  deferred (${a.deferred.length}): ${a.deferred.join(" ")}`)
  if (!full) out.push("", `  ${"total".padEnd(width + 2)}  ${size(sum(secs)).padStart(6)} chars ${approxTokens(sum(secs))}`)
  return `${out.join("\n")}\n`
}

// ── diff ────────────────────────────────────────────────────────────────────
export type DiffLine = { op: " " | "-" | "+" | "@"; line: string }

/** A shortest edit script (Myers), as one op per line: removals of a change before its additions. */
function editScript(a: string[], b: string[]): DiffLine[] {
  // The common ends cost nothing to set aside, and a section added or dropped whole is no search at all.
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head++
  let tail = 0
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++
  const same = (xs: string[]) => xs.map((line): DiffLine => ({ op: " ", line }))
  const pre = same(a.slice(0, head))
  const post = same(a.slice(a.length - tail))
  const ma = a.slice(head, a.length - tail)
  const mb = b.slice(head, b.length - tail)
  if (ma.length === 0 || mb.length === 0)
    return [...pre, ...ma.map((line): DiffLine => ({ op: "-", line })), ...mb.map((line): DiffLine => ({ op: "+", line })), ...post]
  return [...pre, ...myers(ma, mb), ...post]
}

function myers(a: string[], b: string[]): DiffLine[] {
  const n = a.length
  const m = b.length
  const max = n + m
  const v = new Map<number, number>([[1, 0]])
  const trace: Map<number, number>[] = []
  outer: for (let d = 0; d <= max; d++) {
    trace.push(new Map(v))
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && (v.get(k - 1) ?? 0) < (v.get(k + 1) ?? 0)) ? (v.get(k + 1) ?? 0) : (v.get(k - 1) ?? 0) + 1
      let y = x - k
      while (x < n && y < m && a[x] === b[y]) x++, y++
      v.set(k, x)
      if (x >= n && y >= m) break outer
    }
  }
  const out: DiffLine[] = []
  let x = n
  let y = m
  for (let d = trace.length - 1; d >= 0 && (x > 0 || y > 0); d--) {
    const vd = trace[d]
    const k = x - y
    const down = k === -d || (k !== d && (vd.get(k - 1) ?? 0) < (vd.get(k + 1) ?? 0))
    const pk = down ? k + 1 : k - 1
    const px = vd.get(pk) ?? 0
    const py = px - pk
    while (x > px && y > py) out.push({ op: " ", line: a[--x] }), y--
    if (d > 0) out.push(down ? { op: "+", line: b[--y] } : { op: "-", line: a[--x] })
  }
  out.reverse()
  // Within one change, every removal first, as `diff` prints it.
  for (let i = 0; i < out.length; ) {
    if (out[i].op === " ") {
      i++
      continue
    }
    let j = i
    while (j < out.length && out[j].op !== " ") j++
    const run = out.slice(i, j)
    out.splice(i, j - i, ...run.filter((e) => e.op === "-"), ...run.filter((e) => e.op === "+"))
    i = j
  }
  return out
}

/** `diff -U1` of two texts: each change with a line of context around it, `@` where a hunk starts. */
export function lineDiff(a: string, b: string): DiffLine[] {
  const lines = (t: string) => (t === "" ? [] : t.replace(/\n$/, "").split("\n"))
  const ops = editScript(lines(a), lines(b))
  const changed = ops.map((e, i) => (e.op !== " " ? i : -1)).filter((i) => i >= 0)
  if (changed.length === 0) return []
  const keep = new Set<number>()
  for (const i of changed) for (let j = Math.max(0, i - 1); j <= Math.min(ops.length - 1, i + 1); j++) keep.add(j)
  const out: DiffLine[] = []
  let last = -2
  for (let i = 0; i < ops.length; i++) {
    if (!keep.has(i)) continue
    if (i !== last + 1) out.push({ op: "@", line: "" })
    out.push(ops[i])
    last = i
  }
  return out
}

/** The diff as the reader sees it: `−`/`+`, hunks apart marked `…`, at most `cap` lines. */
function hunks(d: DiffLine[], cap: number): string[] {
  const out = d.flatMap((e, i) => (e.op === "@" ? (i ? ["      …"] : []) : [`    ${e.op === "-" ? "−" : e.op} ${e.line}`]))
  return out.length > cap ? [...out.slice(0, cap), `      … ${out.length - cap} more lines — --full shows them all`] : out
}

/** `2026-09-24 12:31`, in the reader's own time zone. */
const local = (iso: string): string => {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** What `-o NAME` keeps: the run, its catch row and its body's hash — the body stays in the gateway's blobs. */
export type Saved = {
  v: 2
  name: string
  at: string
  cwd: string
  command: string[]
  program: boolean
  requests: number
  placeholder: string | null
  run: string
  row: string
  body: string
  /** The appended prompts as they were, so `--vs` cuts the old body the same way. */
  pins: Pin[]
}

export function versus(now: Anatomy, then: Anatomy, name: string, saved: Pick<Saved, "at" | "command">, full: boolean, part: Part | null): string {
  const cap = full ? Number.POSITIVE_INFINITY : 40
  const inPart = (s: Section) => !part || s.part === part
  const before = new Map(then.sections.filter(inPart).map((s) => [s.key, s]))
  const after = new Map(now.sections.filter(inPart).map((s) => [s.key, s]))
  const lines: string[] = []
  let changed = 0
  let added = 0
  let removed = 0
  const title = (mark: string, s: Section, sizes: string) => `  ${mark} ${s.part === "messages" ? s.where : s.part} › ${s.label}   ${sizes}`
  for (const s of now.sections.filter(inPart)) {
    const old = before.get(s.key)
    if (!old) {
      added++
      lines.push(title("+", s, size(s.chars)), ...hunks(lineDiff("", s.text), cap), "")
    } else if (old.text !== s.text || !!old.deferred !== !!s.deferred) {
      changed++
      const delta = s.chars - old.chars
      const note = !!old.deferred !== !!s.deferred ? (s.deferred ? " · now deferred" : " · now loaded") : ""
      lines.push(title("~", s, `${size(old.chars)} → ${size(s.chars)} (${delta >= 0 ? "+" : "−"}${size(Math.abs(delta))})${note}`), ...hunks(lineDiff(old.text, s.text), cap), "")
    }
  }
  for (const s of then.sections.filter(inPart))
    if (!after.has(s.key)) {
      removed++
      lines.push(title("−", s, size(s.chars)), ...hunks(lineDiff(s.text, ""), cap), "")
    }
  const delta = now.chars - then.chars
  const head = [
    `  vs ${name} (${local(saved.at)} · ${saved.command.join(" ")})`,
    `  ${changed} changed · ${added} added · ${removed} removed` +
      ` · total ${size(then.chars)} → ${size(now.chars)} (${delta >= 0 ? "+" : "−"}${size(Math.abs(delta))} chars ${approxTokens(Math.abs(delta))})` +
      (now.model !== then.model ? ` · model ${then.model} → ${now.model}` : ""),
    "",
  ]
  if (!lines.length) head.push("  nothing added, removed or changed", "")
  return `${[...head, ...lines].join("\n").replace(/\n+$/, "")}\n`
}

// ── main ────────────────────────────────────────────────────────────────────
const xrayDir = () => paths(loadConfig()).xray

/** A saved run and its body, read back from the gateway's blobs. */
function load(name: string): { saved: Saved; body: Body } | { error: string } | null {
  const f = join(xrayDir(), `${name}.json`)
  if (!existsSync(f)) return null
  const saved = JSON.parse(readFileSync(f, "utf8")) as Saved
  const blobs = paths(loadConfig()).blobs
  const body = saved.v === 2 ? (readBlob(blobs, saved.body) as Body | null) : null
  return body ? { saved, body } : { error: `${name} names no body in ${tildify(blobs)} — save it again with -o ${name}` }
}

const TRUST_HINT = /trust (?:the files|this folder)|Do you trust|Quick safety check|Yes, I trust/i
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** xray itself: `<command> xray …` calls it with the rest of argv; run directly, the file calls it below. */
export async function main(argv: string[]): Promise<number> {
  const err = (s: string) => process.stderr.write(`xray: ${s}\n`)
  const o = parseArgs(argv)
  if ("help" in o) return process.stdout.write(USAGE), 0
  if ("error" in o) return err(o.error), process.stderr.write(`\n${USAGE}`), 64
  const p = plan(o.command)
  if ("error" in p) return err(p.error), 64
  const then = o.vs ? load(o.vs) : null
  if (o.vs && !then) {
    const have = existsSync(xrayDir()) ? readdirSync(xrayDir()).filter((f) => f.endsWith(".json")).map((f) => f.slice(0, -5)) : []
    return err(`nothing saved as ${o.vs} in ${tildify(xrayDir())}${have.length ? ` — saved: ${have.join(" ")}` : " — save one first with -o NAME"}`), 66
  }
  if (then && "error" in then) return err(then.error), 66
  const routed = bypasses(process.cwd(), p.settings)
  if (routed.length)
    return err(`refusing: this launch would not reach the gateway — ${routed.join("; ")}. A settings file's env wins over the launch's, so the prompt would go wherever that points, not dry.`), 1

  // A person's session runs in a pty from `script`, which Windows does not have.
  if (!p.program && isWindows())
    return err(`an interactive session needs a terminal to run in, and on Windows xray has none to give it — xray a program session instead: ${gw("xray")} claude -p "hi"`), 1
  if (!p.program && scriptFlavor() === null)
    return err(`an interactive session needs a terminal, which xray gets from \`script\` — none is on PATH. Install it (Debian/Ubuntu: apt install bsdutils; Fedora: dnf install util-linux-script; Alpine: apk add util-linux-misc), or xray a program session: ${gw("xray")} claude -p "hi"`), 1

  // The gateway does the catching; xray starts none of its own. One that
  // predates dry runs would forward the prompt, so it must say it can.
  const cfg = loadConfig()
  const base = `http://${cfg.host}:${cfg.port}`
  const health = await fetch(`${base}/_gateway/health`, { signal: AbortSignal.timeout(3000) }).then(
    (r) => r.json() as Promise<{ ok?: boolean; pid?: number; dryRun?: boolean }>,
    () => null,
  )
  if (!health?.ok)
    return err(`the gateway is off — nothing answers on ${cfg.host}:${cfg.port}. xray has the gateway dry-run the launch's model calls; \`${gw("enable")}\` turns it on.`), 1
  if (health.dryRun !== true)
    return err(`the gateway on :${cfg.port} (pid ${health.pid ?? "?"}) is a build without dry-run — it would send this prompt to Anthropic, so nothing was launched. \`${gw("restart")}\` from a checkout that has dry-run puts one on.`), 1

  const trace = paths(cfg)
  const tail = new TraceTail(trace.trace)
  const run = `xray-${Date.now().toString(36)}-${randomBytes(3).toString("hex")}`
  const timeoutMs = Number(brandEnv("XRAY_TIMEOUT") ?? 60) * 1000
  const since = Date.now()
  const child = launch(o.command, p.argv, launchEnv(base, run), p.program)
  let signalled = false
  for (const sig of ["SIGINT", "SIGTERM"] as const) process.on(sig, () => (signalled = true))
  let exitCode: number | null | undefined
  void child.exited.then((c) => (exitCode = c))

  // Every row since the launch (the leak check reads them), and this run's.
  const all: TraceRow[] = []
  const mine: TraceRow[] = []
  const poll = (): TraceRow | null => {
    for (const r of tail.read()) all.push(r), r.identity?.run === run && mine.push(r)
    return mine.find(isMainCall) ?? null
  }
  let caught: TraceRow | null = null
  let why: "exited" | "timeout" | "signal" = "timeout"
  for (;;) {
    if ((caught = poll())) break
    if (exitCode !== undefined) {
      await sleep(200) // its last row may land just after it exits
      if (!(caught = poll())) why = "exited"
      break
    }
    if (signalled) why = "signal"
    if (signalled || Date.now() > since + timeoutMs) break
    await sleep(100)
  }

  const pid = child.proc.pid
  // claude -p takes the canned reply and exits; a hook that hangs does not hold xray.
  const quit = caught && p.program ? await Promise.race([child.exited.then(() => true), sleep(15_000).then(() => false)]) : false
  if (!quit && pid) await killAll(pid)
  await sleep(150)
  poll()
  // A person's session is cleaned up however it ended: a side call names it too.
  const sid = caught?.identity.session ?? mine.find((r) => r.identity?.session)?.identity.session ?? null
  const removed = !p.program && sid ? removeSession(sid, since) : []
  const calls = mine.filter(isModelCall)
  // A model call of this session that was not dry went upstream: say so, loudly.
  const leaked = all.filter((r) => isModelCall(r) && !r.dryRun && (r.identity?.run === run || (sid !== null && r.identity?.session === sid)))
  const leakNote = `${leaked.length} model call(s) of this session went upstream, not dry: rows ${leaked.map((r) => r.id).join(" ")}`

  if (!caught) {
    const lines = child.tail()
    const n = calls.length
    const head = why === "signal" ? "interrupted" : why === "exited" ? `claude exited (code ${exitCode ?? "?"}) before sending its prompt` : `no prompt caught in ${Math.round(timeoutMs / 1000)} s`
    const likely =
      why === "signal"
        ? ""
        : lines.some((l) => TRUST_HINT.test(l))
          ? " — it is waiting on the folder-trust dialog — run `claude` here once and accept it, or use -p (which skips it)"
          : n > 0
            ? ` — ${n} model call${n === 1 ? "" : "s"} reached the gateway, none marked main — ${calls.map((c) => c.hints?.requestClass ?? "no hints").join(", ")}`
            : why === "timeout" && !p.program
              ? " — nothing reached the gateway — likely a dialog on screen (folder trust, a new MCP server, onboarding); run `claude` here once to clear it, or use -p"
              : " — nothing reached the gateway"
    err(head + likely)
    if (lines.length) process.stderr.write(`  last lines from claude:\n${lines.map((l) => `    ${clip(l, 160)}`).join("\n")}\n`)
    if (removed.length) process.stderr.write(`  removed what the session left: ${removed.map(configRel).join(" · ")}\n`)
    if (leaked.length) process.stderr.write(`  WARNING: ${leakNote}\n`)
    return why === "signal" ? 130 : 1
  }

  const bodyHash = caught.blobs.body
  const body = bodyHash ? (readBlob(trace.blobs, bodyHash) as Body | null) : null
  if (!bodyHash || !body) return err(`the gateway recorded the prompt (row ${caught.id}) but its body blob is not in ${tildify(trace.blobs)}`), 1
  const meta: Meta = { program: p.program, requests: calls.length, placeholder: p.placeholder, dropped: p.dropped }
  const pins = pinsFor(o.command)
  const now = anatomy(body, pins)
  const text = o.json
    ? `${JSON.stringify(o.part ? body[o.part] : body, null, 2)}\n`
    : then && !("error" in then)
      ? `${headerLine(now, meta)}\n${versus(now, anatomy(then.body, then.saved.pins), o.vs as string, then.saved, o.full, o.part)}`
      : o.full || o.part
        ? listing(now, o.part, meta, o.full)
        : outline(now, meta, process.stdout.columns ?? 130)
  process.stdout.write(text)

  const notes: string[] = []
  if (o.out) {
    const f = join(xrayDir(), `${o.out}.json`)
    const saved: Saved = { v: 2, name: o.out, at: new Date().toISOString(), cwd: process.cwd(), command: o.command, program: p.program, requests: calls.length, placeholder: p.placeholder, run, row: caught.id, body: bodyHash, pins }
    mkdirSync(xrayDir(), { recursive: true, mode: 0o700 })
    writeFileSync(f, `${JSON.stringify(saved, null, 2)}\n`, { mode: 0o600 })
    notes.push(`saved     ${tildify(f)} — run ${run}, body ${bodyHash.slice(0, 12)}… in the gateway's blobs; --vs ${o.out} compares against it`)
  }
  if (removed.length) notes.push(`removed   what the session left in ${tildify(claudeConfig())}: ${removed.map(configRel).join(" · ")}`)
  if (leaked.length) notes.push(`WARNING   ${leakNote}`)
  if (notes.length) (o.json ? process.stderr : process.stdout).write(o.json ? `${notes.join("\n")}\n` : `\n${notes.map((n) => `  ${n}`).join("\n")}\n`)
  return leaked.length ? 1 : 0
}

if (process.argv[1] && (import.meta.url === pathToFileURL(process.argv[1]).href || basename(process.argv[1]) === "xray.ts"))
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (e: unknown) => {
      process.stderr.write(`xray: ${(e as Error).stack ?? String(e)}\n`)
      process.exit(1)
    },
  )
