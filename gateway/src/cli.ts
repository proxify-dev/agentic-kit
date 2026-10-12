#!/usr/bin/env -S node --experimental-strip-types --no-warnings
/**
 * cc-gateway (`ak gateway` in the kit) — status | enable | disable | restart | rollback | account | tui |
 * tail | try | run | env | xray, and the plumbing under them: start | stop | service | shim | reload | probe
 *
 * `status`, `enable`, `disable` and `env` take `--json`: one JSON document on stdout
 * for a program that drives the gateway, and nothing else there.
 *
 * `restart` is a hand-over (release.ts, keeper.ts): the checkout is copied into
 * a release, the keeper starts it beside the one serving and retires the old one
 * once the new one answers. No gap, no reply cut; `rollback` is the same move
 * back to the build before.
 *
 * `enable` / `disable` are the switch (power.ts): the service (launchd, systemd
 * or Task Scheduler — service.ts) plus the cc-shim fragment that routes
 * terminal claude through it, both up or both down. The plumbing verbs move one
 * half at a time, for when something is wrong with that half.
 *
 * `start`/`stop` are service-aware: once the service is installed they go
 * through its manager (load / unload), so a stop stays stopped and a start
 * survives the terminal. Before that they spawn a plain process and stop it
 * with `POST /_gateway/stop` — a signal only when it cannot answer.
 *
 * `doctor` says whether this machine can run the service at all (WSL without
 * systemd cannot) and what to do instead.
 *
 * `probe` launches a real headless `claude` through the gateway, with
 * `CC_SHIM_DISABLE=1` so the cc-shim fragment cannot pick the base URL itself.
 * `try` is its interactive sibling: a second gateway on its own port with its
 * own browser login, one real claude session through it, stopped when claude
 * exits. The real gateway and its tokens are never touched.
 */
import { spawn, spawnSync } from "node:child_process"
import { closeSync, existsSync, openSync, readFileSync, readdirSync, unlinkSync, writeFileSync } from "node:fs"
import { constants, homedir } from "node:os"
import { basename, dirname, join } from "node:path"
import { createInterface } from "node:readline"
import { fileURLToPath } from "node:url"
import { loadAccounts, describe } from "./accounts.ts"
import { claudeHome, cmd as brandCmd, envName, isWindows, pidAlive, spawnDetached } from "./brand.ts"
import { command as gw, inKit } from "./command.ts"
import { resolveProgram, spawnable, which } from "./procs.ts"
import { routedEnv, routing, terminalEnv } from "./claude-env.ts"
import { CONFIG_PATH, ensureDirs, loadConfig, paths } from "./config.ts"
import type { Config } from "./config.ts"
import type { UpstreamStatus } from "./upstream-status.ts"
import * as pool from "./pool.ts"
import * as power from "./power.ts"
import { liveKeeper, readRelease } from "./release.ts"
import { shadows } from "./shadows.ts"
import type { Power } from "./power.ts"
import * as service from "./service.ts"
import { shimOff, shimOn, shimState } from "./shim.ts"
import { runTui } from "./tui.ts"

const args = process.argv.slice(2)
const cmd = args[0] ?? "status"

/**
 * Where the program `run` launches starts in argv: after `--`, else at the
 * first word that is not one of run's own flags — so `run claude --account X`
 * hands `--account X` to claude. Inside the kit the `--` never arrives: click
 * eats it.
 */
function programAt(argv: string[]): number {
  for (let i = 1; i < argv.length; i++) {
    if (argv[i] === "--") return i + 1
    if (argv[i] === "--account" || argv[i] === "--port") i++
    else if (argv[i] !== "--trial") return i
  }
  return argv.length
}

// Our flags stop at `--`; what follows it belongs to the claude `try` launches.
// `run`'s stop at the program it launches, `--` or not.
const ours = cmd === "run" ? args.slice(0, programAt(args)) : args.includes("--") ? args.slice(0, args.indexOf("--")) : args
const flag = (name: string): string | undefined => {
  const i = ours.indexOf(`--${name}`)
  return i >= 0 ? ours[i + 1] : undefined
}

const overrides: Partial<Config> = {
  ...(flag("port") ? { port: Number(flag("port")) } : {}),
  ...(flag("account") ? { account: flag("account") as string } : {}),
}
const real: Config = loadConfig(overrides)

/**
 * `try`, and any verb given `--trial`, address a second gateway: the next port
 * up, its own accounts file and data under <dataDir>/trial. It runs only while
 * a `try` does, so the verbs that manage the real one refuse it — `stop` or
 * `disable` would otherwise reach the service.
 */
const trial = cmd === "try" || ours.includes("--trial")
const trialDir = join(real.dataDir, "trial")
const ownerFile = join(trialDir, "owner.pid")
const cfg: Config = trial
  ? loadConfig({
      ...overrides,
      port: flag("port") ? Number(flag("port")) : real.port + 1,
      dataDir: trialDir,
      accountsFile: join(trialDir, "accounts.json"),
    })
  : real
const p = paths(cfg)

const alive = pidAlive

const readPid = (): number | null => {
  if (!existsSync(p.pid)) return null
  const n = Number(readFileSync(p.pid, "utf8").trim())
  return Number.isInteger(n) && alive(n) ? n : null
}

async function fetchJson(path: string, init?: RequestInit): Promise<unknown> {
  const r = await fetch(`http://${cfg.host}:${cfg.port}${path}`, init)
  return r.json()
}

const say = (line: string) => process.stdout.write(`${line}\n`)

/** After an edit to the accounts file: the running daemon adopts it, or it will at start. */
async function reloadIfRunning(): Promise<void> {
  if (!readPid()) {
    say("gateway not running — picked up at next start")
    return
  }
  const r = (await fetchJson("/_gateway/reload", { method: "POST" })) as { ok?: boolean; added?: number; error?: string }
  say(r.ok ? `gateway reloaded${r.added ? ` (+${r.added} account${r.added === 1 ? "" : "s"})` : ""}` : `gateway: ${r.error ?? "reload refused"}`)
}

const ask = (q: string): Promise<string> =>
  new Promise((resolve) => {
    const rl = createInterface({ input: process.stdin, output: process.stdout })
    rl.question(q, (a) => {
      rl.close()
      resolve(a.trim())
    })
  })

const pct = (v: unknown) => (typeof v === "number" ? `${Math.round(v * 100)}%`.padStart(4) : "   -")

const here = dirname(fileURLToPath(import.meta.url))
const daemonEntry = join(here, "daemon.ts")

/**
 * A plain daemon's stop: asked over loopback first, the same on every OS — on
 * Windows a signal is always a hard kill, with no drain and no quota written.
 * A signal only when nothing answers (a build from before the stop route).
 */
async function stopDaemon(pid: number): Promise<void> {
  const r = await fetch(`http://${cfg.host}:${cfg.port}/_gateway/stop`, { method: "POST", signal: AbortSignal.timeout(2000) }).catch(() => null)
  if (r?.ok) return
  try {
    process.kill(pid, "SIGTERM")
  } catch {
    // Already gone.
  }
}

/** The claude binary a probe runs: PATH first, then where installers put it. */
function claudeBinary(): string | null {
  const found = which("claude")
  if (found) return found
  const home = homedir()
  const candidates = isWindows()
    ? [join(home, ".local", "bin", "claude.exe"), join(process.env.APPDATA ?? join(home, "AppData", "Roaming"), "npm", "claude.cmd")]
    : [join(home, ".local", "bin", "claude"), join(claudeHome(), "local", "claude")]
  return candidates.find((c) => existsSync(c)) ?? null
}

/** Wait for the health endpoint, so a start reports the truth and not a hope. */
async function waitUp(ms = 3000): Promise<{ pid?: number } | null> {
  const deadline = Date.now() + ms
  while (Date.now() < deadline) {
    try {
      return (await fetchJson("/_gateway/health")) as { pid?: number }
    } catch {
      await new Promise((r) => setTimeout(r, 150))
    }
  }
  return null
}

function printUnit(s: service.UnitState): void {
  const how = !s.installed ? "not installed" : s.loaded ? `loaded${s.pid ? ` (pid ${s.pid})` : " (no pid)"}` : "installed, not loaded"
  process.stdout.write(`${s.label.padEnd(22)} ${how}\n`)
  if (s.program) process.stdout.write(`${"".padEnd(22)} runs ${s.program.join(" ")}\n`)
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`
const tokens = (t: number) => (t >= 1e9 ? `${(t / 1e9).toFixed(1)}B` : t >= 1e6 ? `${(t / 1e6).toFixed(1)}M` : t >= 1e3 ? `${Math.round(t / 1e3)}k` : String(t))

type Health = { pid?: number; accounts?: number; build?: string | null; keeper?: number | null }

/**
 * The second line of `status`: which build serves, and what `rollback` would
 * return to. Nothing for an older daemon — the headline already says restart.
 */
function buildLine(health: Health | null): string | null {
  if (!health || typeof health.accounts !== "number") return null
  const k = liveKeeper(real)
  if (!health.keeper || !k)
    return `straight from a checkout, no keeper — \`${gw("restart")}\` puts one in (one short restart, at a quiet moment); every restart after that is a hand-over`
  const rel = k.current ? readRelease(real, k.current) : null
  const parts = [`${k.current ?? "?"}${rel?.dirty ? " (had uncommitted changes)" : ""}`]
  if (k.draining.length > 0) parts.push(`${plural(k.draining.length, "older build")} finishing open replies`)
  if (k.previous) parts.push(`before it: ${k.previous} — \`${gw("rollback")}\``)
  const failed = k.last && !k.last.ok ? `\n          last restart failed: ${k.last.detail.split("\n")[0]}` : ""
  return parts.join(" · ") + failed
}

/**
 * The first line of `status`: on, off, or which half is on — and the verb that
 * settles it. Told by the running daemon where there is one: the file says what
 * the next start would serve, the daemon what is being served now.
 */
function headline(pw: Power, health: Health | null): string {
  const stale = pw.shim.installed && !pw.shim.current ? " · the routing file is an older rev — `enable` rewrites it" : ""
  // A daemon older than this CLI answers health without `accounts`: it serves by
  // its own rules, which this build cannot read — and restarting it runs this
  // build, with whatever the file holds.
  if (health && typeof health.accounts !== "number")
    return pw.accounts === 0
      ? `running an older build (pid ${health.pid ?? "?"}) that still serves by its own rules · this build has no accounts yet — \`${gw("account login")}\` before \`${gw("restart")}\``
      : `running an older build (pid ${health.pid ?? "?"}) · \`${gw("restart")}\` runs this one, with ${plural(pw.accounts, "account")}`
  const serving = typeof health?.accounts === "number" ? health.accounts : pw.accounts
  if (pw.on && serving === 0) return `on, but with no accounts — nothing can be served: \`${gw("account login")}\``
  if (pw.on) return `on · ${plural(serving, "account")} · every new claude goes through :${real.port}${stale}`
  if (!pw.partial) return `off · claude uses its own login — \`${gw("enable")}\` turns it on`
  return pw.service.loaded
    ? "half on — running, but claude is not routed through it · `enable` finishes it, `disable` undoes it"
    : `half on — claude is routed to it, but it is stopped (launches fall back to their own login) · \`enable\` finishes it, \`disable\` undoes it${stale}`
}

/** Wait for a pid to go, so the summary reads a trace the daemon has finished writing. */
async function untilGone(pid: number, ms = 5000): Promise<boolean> {
  const deadline = Date.now() + ms
  while (Date.now() < deadline && alive(pid)) await new Promise((r) => setTimeout(r, 100))
  return !alive(pid)
}

const tailOf = (file: string, n = 15): string =>
  existsSync(file) ? `${readFileSync(file, "utf8").trimEnd().split("\n").slice(-n).join("\n")}\n` : "(no log)\n"

type TrialRow = { status: number | null; account: string | null; identity?: { run?: string | null } }

/** The rows one `try` produced: its run id rides on every request its claude sent. */
function rowsFor(run: string, sinceDay: string): TrialRow[] {
  if (!existsSync(p.trace)) return []
  const rows: TrialRow[] = []
  for (const f of readdirSync(p.trace).filter((f) => f.endsWith(".ndjson") && f.slice(0, 10) >= sinceDay).sort())
    for (const line of readFileSync(join(p.trace, f), "utf8").split("\n")) {
      if (!line) continue
      try {
        const r = JSON.parse(line) as TrialRow
        if (r.identity?.run === run) rows.push(r)
      } catch {
        // A joined trial is still writing; its last line may be half there.
      }
    }
  return rows
}

/**
 * What `try` hands claude: everything after `--`, and every argument before it
 * that is not one of ours. The second half is not a nicety — `ak gateway`
 * reaches this CLI through click, which swallows the `--` itself.
 */
function claudeArgs(): string[] {
  const valued = new Set(["--name", "--port", "--bin", "--account"])
  const rest = args.slice(1)
  const out: string[] = []
  for (let i = 0; i < rest.length; i++) {
    if (rest[i] === "--") return [...out, ...rest.slice(i + 1)]
    if (valued.has(rest[i])) i++
    else if (rest[i] !== "--login" && rest[i] !== "--trial") out.push(rest[i])
  }
  return out
}

const tally = (xs: string[]): string =>
  [...xs.reduce((m, x) => m.set(x, (m.get(x) ?? 0) + 1), new Map<string, number>())].map(([k, n]) => `${k} ×${n}`).join(", ")

/**
 * A program on this terminal until it exits; its exit code (128 + the signal's
 * number when a signal ended it), or 127 when it could not start.
 */
function launchInTerminal(bin: string, argv: string[], env: NodeJS.ProcessEnv): Promise<number> {
  return new Promise<number>((resolve) => {
    let plan
    try {
      plan = spawnable(bin, argv)
    } catch (err) {
      process.stderr.write(`could not launch ${bin}: ${(err as Error).message}\n`)
      return resolve(127)
    }
    const c = spawn(plan.command, plan.args, { stdio: "inherit", env, ...plan.options })
    // The program owns the terminal while it runs: Ctrl-C reaches it from the
    // terminal itself, and this process must outlive it to report (and, for
    // `try`, stop the wire). A hangup or SIGTERM is passed on.
    process.on("SIGINT", () => {})
    process.on("SIGHUP", () => c.kill("SIGHUP"))
    process.on("SIGTERM", () => c.kill("SIGTERM"))
    c.on("error", (err) => {
      process.stderr.write(`could not launch ${bin}: ${err.message}\n`)
      resolve(127)
    })
    c.on("exit", (n, sig) => resolve(n ?? (sig ? 128 + (constants.signals[sig] ?? 0) : 1)))
  })
}

/**
 * The environment `run` launches with and `env` prints — one function, so
 * the two cannot drift. The gateway is asked first: down is `down` (a launch
 * refuses; `env` only warns), and an `--account` is resolved by it to the one
 * name it routes, as the cc-shim fragment does. A name it cannot route is an
 * error: a pinned launch never falls back to another account.
 */
async function terminalLaunch(account: string | undefined): Promise<{ env: NodeJS.ProcessEnv; down: string | null } | { error: string }> {
  const base = `http://${cfg.host}:${cfg.port}`
  const up = await fetch(`${base}/_gateway/health`, { signal: AbortSignal.timeout(1000) }).then(
    async (r) => (await r.body?.cancel(), r.ok),
    () => false,
  )
  const down = up ? null : `the gateway is not answering on ${cfg.host}:${cfg.port} — \`${gw("enable")}\` turns it on`
  if (!account) return { env: terminalEnv(base), down }
  if (down) return { error: `--account ${account}: ${down}` }
  const name = await resolveAccount(base, account)
  if ("error" in name) return name
  return { env: terminalEnv(base, { account: name.name }), down: null }
}

/** The one account name the running gateway routes `q` to, or why it cannot. */
async function resolveAccount(base: string, q: string): Promise<{ name: string } | { error: string }> {
  const r = await fetch(`${base}/_gateway/resolve?q=${encodeURIComponent(q)}`, { signal: AbortSignal.timeout(2000) }).catch(() => null)
  const text = r ? (await r.text()).trim() : "the gateway did not answer"
  return r?.ok ? { name: text } : { error: `--account ${q}: ${text}` }
}

/** One JSON document and a newline: what a `--json` verb prints. */
const json = (v: unknown) => say(JSON.stringify(v))

/** POSIX sh single quotes: everything literal, a `'` closed, escaped and reopened. */
const shQuote = (v: string): string => `'${v.replaceAll("'", `'\\''`)}'`

/**
 * One claude through a gateway that is not the real one — to try a change, or
 * an account, without moving anything real. The trial's account is a fresh
 * browser login into its own file: a grant nobody else holds, refreshed here
 * like any account. Nothing of the real gateway's is read or written.
 */
async function tryGateway(): Promise<number> {
  ensureDirs(cfg)
  const ownerPid = (): number | null => {
    if (!existsSync(ownerFile)) return null
    const n = Number(readFileSync(ownerFile, "utf8").trim().split(/\s+/)[0])
    return Number.isInteger(n) && alive(n) ? n : null
  }

  let pid = readPid()
  if (!pid) {
    const holder = service.portHolder(cfg.port)
    if (holder) {
      process.stderr.write(`:${cfg.port} is held by pid ${holder.pid ?? "?"} (${holder.command.slice(0, 60)}) — pick another with --port\n`)
      return 1
    }
  }

  const enabled = () => pool.readPool(cfg.accountsFile).accounts.filter((a) => !a.disabled)
  if (enabled().length === 0 || ours.includes("--login")) {
    say("login     a browser login for the trial (first run only) — its own tokens, so the real gateway's are never touched")
    const row = await pool.login(cfg.accountsFile, { name: flag("name") }, say)
    say(`saved     "${row.name}" to ${cfg.accountsFile}`)
    if (pid) await reloadIfRunning()
  }

  // A trial another `try` started is joined, and stops when that one ends. An
  // orphan — its `try` was killed — is adopted, so this one stops it.
  const other = ownerPid()
  const own = !pid || !other || other === process.pid
  let spawned: number | undefined
  if (!pid) {
    const log = openSync(p.log, "a", 0o600)
    // Its own process group: a Ctrl-C typed into claude must not reach the
    // wire claude is talking through.
    const child = spawnDetached(process.execPath, ["--experimental-strip-types", "--no-warnings", daemonEntry], {
      stdio: ["ignore", log, log],
      env: {
        ...process.env,
        [envName("GATEWAY_PORT")]: String(cfg.port),
        [envName("GATEWAY_ACCOUNTS_FILE")]: cfg.accountsFile,
        [envName("GATEWAY_DATA_DIR")]: cfg.dataDir,
        // `--account X` is the standing pin, as it is for the real one.
        ...(cfg.account ? { [envName("GATEWAY_ACCOUNT")]: cfg.account } : {}),
      },
    })
    closeSync(log)
    spawned = child.pid
  }
  const h = await waitUp(8000)
  if (!h) {
    process.stderr.write(`the trial gateway did not come up on :${cfg.port} — ${p.log}:\n${tailOf(p.log)}`)
    if (spawned && alive(spawned)) process.kill(spawned, "SIGTERM") // it never served: nothing to drain
    return 1
  }
  pid = h.pid ?? readPid()
  if (own) writeFileSync(ownerFile, `${process.pid}\n`, { mode: 0o600 })
  say(`trial     ${own ? "up" : `joined — pid ${other}'s \`try\` started it and stops it`}: :${cfg.port} (pid ${pid ?? "?"}), ${cfg.dataDir}`)

  const before = new Map(enabled().map((a) => [a.name, a.expiresAt]))
  const run = `try-${Date.now().toString(36)}`
  const sinceDay = new Date().toISOString().slice(0, 10)
  const passthrough = claudeArgs()

  // Routed as the fragment would, but at the trial: the fragments themselves
  // would point this claude back at the real gateway.
  const env = routedEnv(`http://${cfg.host}:${cfg.port}`, ["x-brain-origin: trial", `x-brain-run: ${run}`, `x-brain-project: ${basename(process.cwd())}`])

  // Resolved on PATH here, not by spawn: on Windows spawn finds only a claude.exe,
  // never the shim's claude.cmd or npm's, and a .cmd needs the plan spawnable() makes.
  const bin = resolveProgram(flag("bin") ?? "claude")
  say(`claude    ${[bin, ...passthrough].join(" ")}  → :${cfg.port}  (run ${run})`)
  const code = await launchInTerminal(bin, passthrough, env)

  if (own && pid) {
    await stopDaemon(pid)
    await untilGone(pid)
    try {
      unlinkSync(ownerFile)
    } catch {
      // Racing another `try` for the file is fine; it is advisory.
    }
  }

  const rows = rowsFor(run, sinceDay)
  const bad = rows.filter((r) => r.status === null || r.status >= 400).map((r) => String(r.status ?? "err"))
  say(
    `served    ${plural(rows.length, "request")} — ${rows.length - bad.length} ok` +
      (bad.length ? `, failed ${tally(bad)}` : "") +
      (rows.length ? ` · by ${tally(rows.map((r) => r.account ?? "?"))}` : ""),
  )
  const after = pool.readPool(cfg.accountsFile).accounts
  for (const [name, was] of before) {
    const now = after.find((a) => a.name === name)?.expiresAt
    if (typeof was !== "number" || typeof now !== "number") continue
    const left = `${Math.round((now - Date.now()) / 3_600_000)}h`
    say(
      now > was
        ? `token     ${name}: refreshed during the session and saved to the trial's file — expires in ${left}`
        : `token     ${name}: not refreshed yet — expires in ${left}; a session that outlasts it proves the refresh`,
    )
  }
  say(`kept      ${cfg.dataDir} — the next \`try\` reuses the login; delete it to forget${own ? "" : " (still running for the other `try`)"}`)
  return code
}

async function main(): Promise<number> {
  // xray is its own program with its own flags: everything after the verb is its argv.
  if (cmd === "xray") return await (await import("./xray.ts")).main(args.slice(1))

  if (trial && ["start", "stop", "restart", "rollback", "service", "shim", "enable", "on", "disable", "off"].includes(cmd)) {
    process.stderr.write(`\`${cmd}\` manages the real gateway — the trial one runs only while \`${gw("try")}\` does\n`)
    return 1
  }

  if (cmd === "try") return await tryGateway()

  if (cmd === "run") {
    const [program, ...rest] = args.slice(programAt(args))
    if (!program) {
      process.stderr.write(`run: name the program — ${gw("run")} -- claude\n`)
      return 1
    }
    const r = await terminalLaunch(flag("account"))
    // Down is a refusal, not a fallback: the person asked for this launch to go through the gateway.
    if ("error" in r || r.down) {
      process.stderr.write(`${"error" in r ? r.error : r.down}\n`)
      return 1
    }
    // Resolved on PATH here, not by spawn: on Windows spawn finds only an .exe.
    return await launchInTerminal(resolveProgram(program, r.env), rest, r.env)
  }

  if (cmd === "enable" || cmd === "on") {
    const r = await power.enable(cfg, daemonEntry)
    if (ours.includes("--json")) return json(r), r.ok ? 0 : 1
    for (const line of r.steps) say(line)
    if (r.ok) say("sessions  already open stay where they started; new ones go through the gateway")
    return r.ok ? 0 : 1
  }

  if (cmd === "disable" || cmd === "off") {
    const r = power.disable(cfg)
    if (ours.includes("--json")) return json(r), r.ok ? 0 : 1
    for (const line of r.steps) say(line)
    return r.ok ? 0 : 1
  }

  if (cmd === "start") {
    const running = readPid()
    if (running) {
      say(`already running (pid ${running}) on :${cfg.port}`)
      return 0
    }
    ensureDirs(cfg)
    // Installed as a service: its manager owns the process. Load it and report.
    if (service.state().installed) {
      const r = service.load()
      if (!r.ok) {
        process.stderr.write(`${r.detail}\n`)
        return 1
      }
      const h = await waitUp()
      if (!h) {
        process.stderr.write(`service loaded but nothing answers on :${cfg.port} — tail ${p.log}\n`)
        return 1
      }
      say(`gateway up on :${cfg.port} (pid ${h.pid ?? "?"}, ${service.manager().manager})`)
      return 0
    }
    const detach = args.includes("--detach")
    const daemonArgs = ["--experimental-strip-types", "--no-warnings", daemonEntry]
    const child = detach
      ? spawnDetached(process.execPath, daemonArgs, { env: process.env })
      : spawn(process.execPath, daemonArgs, { stdio: "inherit", env: process.env })
    if (detach) {
      // Give it long enough to bind or fail, so `start` reports the truth.
      await new Promise((r) => setTimeout(r, 700))
      try {
        const h = (await fetchJson("/_gateway/health")) as { pid?: number }
        say(`gateway up on :${cfg.port} (pid ${h.pid ?? "?"})`)
        return 0
      } catch {
        process.stderr.write(`gateway did not come up on :${cfg.port} — run without --detach to see why\n`)
        return 1
      }
    }
    return await new Promise<number>((r) => child.on("exit", (code) => r(code ?? 0)))
  }

  if (cmd === "stop") {
    // Under the service manager an exit is a restart, not a stop — it brings
    // the keeper straight back. Unload instead; `start` reloads it.
    const svc = service.state()
    if (svc.loaded) {
      const r = service.unload()
      say(r.detail)
      return r.ok ? 0 : 1
    }
    const pid = readPid()
    if (!pid) {
      say("not running")
      return 0
    }
    await stopDaemon(pid)
    say(`stopped (pid ${pid})`)
    return 0
  }

  if (cmd === "doctor") {
    const mgr = service.manager()
    const s = mgr.state()
    const how = !s.installed ? "not installed" : s.loaded ? `loaded${s.pid ? ` (pid ${s.pid})` : ""}` : "installed, not loaded"
    say(`service   ${mgr.manager} · ${s.label} ${how} · ${s.file}`)
    const problem = service.serviceProblem()
    if (problem) say(`problem   ${problem}`)
    for (const w of problem ? [] : service.serviceWarnings()) say(`warn:     ${w}`)
    const node = service.nodeBinary()
    say(`node      ${node ?? `none found — set ${envName("GATEWAY_NODE")}`}`)
    const holder = service.portHolder(cfg.port, cfg.host)
    const mine = holder?.pid != null && (holder.pid === s.pid || holder.pid === readPid())
    say(`port      :${cfg.port} ${!holder ? "free" : `held by pid ${holder.pid ?? "?"} (${mine ? "this gateway" : holder.command.slice(0, 60) || "unknown"})`}`)
    return problem || !node ? 1 : 0
  }

  if (cmd === "restart") {
    const r = await power.restart(cfg, daemonEntry, { now: args.includes("--now"), progress: say })
    for (const line of r.steps) say(line)
    return r.ok ? 0 : 1
  }

  if (cmd === "rollback") {
    const r = await power.rollback(cfg)
    for (const line of r.steps) say(line)
    return r.ok ? 0 : 1
  }

  if (cmd === "service") {
    const sub = args[1] && !args[1].startsWith("--") ? args[1] : "status"
    if (sub === "status") {
      printUnit(service.state())
      return 0
    }
    if (sub === "install") {
      const r = service.install(cfg, daemonEntry)
      if (!r.ok) {
        process.stderr.write(`${r.detail}\n`)
        return 1
      }
      say(r.detail)
      if (r.warn) process.stderr.write(`warn: ${r.warn}\n`)
      const h = await waitUp()
      say(h ? `gateway up on :${cfg.port} (pid ${h.pid ?? "?"})` : `loaded but nothing answers yet — tail ${p.log}`)
      return h ? 0 : 1
    }
    if (sub === "uninstall") {
      const r = service.uninstall()
      say(r.detail)
      return r.ok ? 0 : 1
    }
    process.stderr.write("service: status | install | uninstall\n")
    return 1
  }

  if (cmd === "shim") {
    const sub = args[1] && !args[1].startsWith("--") ? args[1] : "status"
    if (sub !== "on" && sub !== "off" && sub !== "status") {
      process.stderr.write("shim: on | off | status\n")
      return 1
    }
    const s = sub === "on" ? shimOn(cfg) : sub === "off" ? shimOff() : shimState()
    say(
      s.installed
        ? `fragment  ${s.path} (rev ${s.rev ?? "?"}${s.current ? "" : ", STALE — run shim on"})`
        : "fragment  not installed — terminal claude uses its own login",
    )
    return 0
  }

  if (cmd === "status" && ours.includes("--json")) {
    // Whether the daemon answers, the switch, and what a client needs to reach it over HTTP.
    const health = readPid() ? ((await fetchJson("/_gateway/health").catch(() => null)) as Health | null) : null
    json({
      running: health !== null,
      pid: health ? (health.pid ?? readPid()) : null,
      power: power.powerState(cfg),
      config: { host: cfg.host, port: cfg.port, upstreamUrl: cfg.upstreamUrl, capture: cfg.capture, dataDir: cfg.dataDir },
    })
    return 0
  }

  if (cmd === "status") {
    const pid = readPid()
    const health = pid ? ((await fetchJson("/_gateway/health").catch(() => null)) as Health | null) : null
    if (trial) say(`trial     ${pid ? `running on :${cfg.port} (pid ${pid})` : `not running — \`${gw("try")}\` starts one`}`)
    else {
      say(`gateway   ${headline(power.powerState(cfg), health)}`)
      const b = buildLine(health)
      if (b) say(`build     ${b}`)
    }
    if (pid) {
      const s = (await fetchJson("/_gateway/status")) as Record<string, unknown>
      say(`traffic   ${String(s.requests)} requests, ${String(s.inFlight)} in flight, ${String(s.errors)} errors, ${String(s.refusals)} refused (since pid ${health?.pid ?? pid} started)`)
      const rules = (s.rules ?? []) as { rule: string; kind: string; level: string }[]
      say(`policy    ${rules.length === 0 ? "none" : rules.map((r) => `${r.rule}(${r.level})`).join(" ")}`)
      const b = (s.budget ?? {}) as Record<string, number>
      const spent = Object.entries(b).filter(([k]) => k.startsWith("session:")).sort((x, y) => y[1] - x[1])
      if (spent.length > 0)
        say(
          `spend     ${tokens(spent.reduce((n, [, v]) => n + v, 0))} tokens across ${plural(spent.length, "session")}` +
            ` · most: ${spent.slice(0, 3).map(([k, v]) => `${k.slice(8, 16)} ${tokens(v)}`).join(", ")}`,
        )
    } else for (const a of describe(loadAccounts(cfg.accountsFile))) say(`account   ${a.name}  ${a.state}  expires ${a.expiresIn}`)
    // What would override the routing for a claude started here — Claude Code
    // applies settings.json `env` over the environment the routing sets.
    if (!trial)
      for (const s of shadows(`http://${cfg.host}:${cfg.port}`))
        say(`shadowed  ${s.name}=${s.value} in ${s.file ? s.file.replace(homedir(), "~") : "this shell"} (${s.scope}) — ${s.effect}`)
    say(`config    ${CONFIG_PATH}${existsSync(CONFIG_PATH) ? "" : " (defaults, no file)"}`)
    say(`data      ${cfg.dataDir}`)
    return 0
  }

  if (cmd === "account" || cmd === "accounts" || cmd === "acct") {
    const sub = args[1] && !args[1].startsWith("--") ? args[1] : "list"
    const file = cfg.accountsFile
    const target = args.slice(2).find((a) => !a.startsWith("--"))

    if (sub === "list") {
      const doc = pool.readPool(file)
      let live: UpstreamStatus | null = null
      if (readPid()) {
        const a = (await fetchJson("/_gateway/accounts")) as { live?: UpstreamStatus | null }
        live = a.live ?? null
      }
      say(`accounts  ${file}`)
      if (doc.accounts.length === 0) {
        say("  none — `account login` opens a browser, `account import` takes Claude Code's own login")
        return 0
      }
      const w = Math.max(...doc.accounts.map((a) => a.name.length), 4)
      say(`  ${"NAME".padEnd(w)}  TYPE    ${"ORG".padEnd(14)} PRI  STATE      5h    7d    F7  EXPIRES`)
      for (const a of doc.accounts) {
        const l = live?.accounts.find((x) => x.name === a.name)
        const q = (l?.quota ?? {}) as Record<string, unknown>
        const state = a.disabled ? "disabled" : (l?.status ?? (typeof a.expiresAt === "number" && a.expiresAt < Date.now() ? "expired" : "-"))
        const cur = live?.currentAccount === a.name ? "►" : " "
        const exp = typeof a.expiresAt === "number" ? `${Math.round((a.expiresAt - Date.now()) / 3_600_000)}h` : "n/a"
        say(`${cur} ${a.name.padEnd(w)}  ${a.type.padEnd(7)} ${(a.orgName ?? "").slice(0, 14).padEnd(14)} ${String(a.priority ?? 0).padStart(3)}  ${state.padEnd(9)} ${pct(q.unified5h)} ${pct(q.unified7d)} ${pct(q.unified7dFable)}  ${exp}`)
      }
      if (!live) say("  (gateway not running — quota unknown)")
      return 0
    }
    if (sub === "login") {
      if (args.includes("--api")) {
        const name = flag("name") ?? (await ask("Account name: "))
        const key = await ask("Anthropic API key: ")
        if (!name || !key) throw new Error("name and key are both required")
        await pool.addApiKey(file, name, key)
        say(`added "${name}" (api key) to ${file}`)
      } else {
        const row = await pool.login(file, { name: flag("name") }, say)
        say(`saved "${row.name}" to ${file}`)
      }
      if (!args.includes("--no-reload")) await reloadIfRunning()
      return 0
    }
    if (sub === "import") {
      const from = flag("from") ?? join(claudeHome(), ".credentials.json")
      const row = await pool.importFrom(file, from, { name: flag("name") }, say)
      say(`saved "${row.name}" to ${file} (from ${from})`)
      if (!args.includes("--no-reload")) await reloadIfRunning()
      return 0
    }
    if (sub === "enable" || sub === "disable") {
      if (!target) throw new Error(`account ${sub} <name>`)
      const row = await pool.patchAccount(file, target, { disabled: sub === "disable" })
      say(`${row.name}: ${sub}d`)
      await reloadIfRunning()
      return 0
    }
    if (sub === "priority") {
      const n = Number(args[3])
      if (!target || !Number.isInteger(n)) throw new Error("account priority <name> <n>   (lower = preferred, default 0)")
      const row = await pool.patchAccount(file, target, { priority: n })
      say(`${row.name}: priority ${n}`)
      await reloadIfRunning()
      return 0
    }
    if (sub === "remove") {
      if (!target) throw new Error("account remove <name>")
      if (!readPid()) {
        const row = await pool.removeAccount(file, target)
        say(`removed "${row.name}" from ${file}`)
        return 0
      }
      // Running: the daemon takes it out of the file and out of rotation at once,
      // and sends any route that named it back to rotation.
      const doc = pool.readPool(file)
      const i = pool.findAccount(doc, target)
      if (i < 0) throw new Error(`no account matches "${target}"`)
      const name = doc.accounts[i].name
      const r = (await fetchJson(`/_gateway/accounts/${encodeURIComponent(name)}`, { method: "DELETE" })) as { ok?: boolean; routesCleared?: string[]; error?: string }
      if (!r.ok) throw new Error(`gateway: ${r.error ?? "remove refused"}`)
      say(`removed "${name}" — out of ${file} and out of rotation now`)
      if (r.routesCleared?.length) say(`routes back to rotation: ${r.routesCleared.join(", ")}`)
      return 0
    }
    // A bare `claude --account`: cc-shim runs this inside `$(...)`, so the menu
    // and the question go to stderr and only the chosen name reaches stdout.
    if (sub === "choose") {
      const names = pool.readPool(file).accounts.filter((a) => !a.disabled).map((a) => a.name)
      if (names.length === 0) throw new Error(`no enabled accounts — \`${gw("account login")}\``)
      names.forEach((n, i) => process.stderr.write(`  ${i + 1}  ${n}\n`))
      const rl = createInterface({ input: process.stdin, output: process.stderr })
      const a = await new Promise<string>((r) => rl.question("account: ", (x) => { rl.close(); r(x.trim()) }))
      const pick = names[Number(a) - 1] ?? names.find((n) => n.toLowerCase().includes(a.toLowerCase()))
      if (!a || !pick) throw new Error(`no account "${a}"`)
      process.stdout.write(`${pick}\n`)
      return 0
    }
    process.stderr.write("account: list | login [--api] [--name N] | import [--from PATH] | enable X | disable X | priority X N | remove X | choose\n")
    return 1
  }

  if (cmd === "reload") {
    await reloadIfRunning()
    return 0
  }

  if (cmd === "tui" || cmd === "top") {
    if (!readPid()) {
      process.stderr.write(trial ? `no trial running — \`${gw("try")}\` starts one\n` : `gateway is not running — \`${gw("enable")}\`\n`)
      return 1
    }
    return runTui(cfg)
  }

  if (cmd === "tail") {
    const n = Number(flag("n") ?? 20)
    // `--session` alone is the calling session: Claude Code puts its id in the env of every
    // command it runs, and it is the id the trace keys on (x-claude-code-session-id).
    const at = ours.indexOf("--session")
    const given = at >= 0 && ours[at + 1] && !ours[at + 1].startsWith("--") ? ours[at + 1] : undefined
    const session = at < 0 ? null : (given ?? process.env.CLAUDE_CODE_SESSION_ID ?? "")
    if (session === "") {
      process.stderr.write("--session: no id given and CLAUDE_CODE_SESSION_ID is not set — pass one, or run it from inside claude\n")
      return 1
    }
    if (!existsSync(p.trace)) {
      say("no trace yet")
      return 0
    }
    const files = readdirSync(p.trace).filter((f) => f.endsWith(".ndjson")).sort()
    if (files.length === 0) {
      say("no trace yet")
      return 0
    }
    // Files are UTC days, and a session crosses midnight UTC often: one session reads back three.
    const lines = (session ? files.slice(-3) : files.slice(-1))
      .flatMap((f) => readFileSync(join(p.trace, f), "utf8").trim().split("\n").filter(Boolean))
    const all = lines.map((line) => JSON.parse(line) as Record<string, unknown>)
    const rows = (session ? all.filter((r) => (r.identity as { session?: string | null } | undefined)?.session === session) : all).slice(-n)
    if (session && rows.length === 0) {
      say(`no calls from session ${session} in the last ${Math.min(3, files.length)} day(s) of trace — started before the gateway was on, or with CC_SHIM_DISABLE=1?`)
      return 0
    }
    for (const r of rows) {
      const u = (r.usage ?? null) as
        | { input: number; output: number; cacheRead: number; cacheCreation: number }
        | null
      const sh = r.shape as { messages: number; tools: number; bytesIn: number }
      const idn = r.identity as { agent: string | null; session: string | null }
      const hint = (r.hints ?? null) as { requestClass: string | null; agentType: string | null } | null
      const kind = hint?.requestClass ? `${hint.requestClass}${hint.agentType ? `:${hint.agentType}` : ""}` : ""
      const pol = (r.policy ?? []) as { rule: string; action: string }[]
      say(
        [
          String(r.ts).slice(11, 19),
          String(r.status ?? "err").padEnd(3),
          String(r.model ?? "-").slice(0, 28).padEnd(28),
          `${String(r.ttfbMs ?? "-")}ms`.padStart(7),
          u
            ? `in=${u.input} out=${u.output} cr=${u.cacheRead} cw=${u.cacheCreation}`
            : "no-usage",
          `msgs=${sh.messages} tools=${sh.tools} ${(sh.bytesIn / 1024).toFixed(0)}kb`,
          idn.agent ?? (idn.session ? `sess:${idn.session.slice(0, 8)}` : "anon"),
          kind,
          pol.length > 0 ? pol.map((d) => `${d.rule}:${d.action}`).join(",") : "",
          r.error ? `ERR ${String(r.error)}` : "",
        ].join("  "),
      )
    }
    if (session) {
      say(`${rows.length} calls of session ${session.slice(0, 8)}, subagents included · ${p.trace}`)
      // Joining calls to the transcript is the kit's tracer; standalone there is only the format.
      const format = `the format: ${fileURLToPath(new URL("../docs/trace.md", import.meta.url))}`
      say(inKit() ? `step by step, joined to the transcript: ${brandCmd("tracer inspect")} ${session.slice(0, 8)} · ${format}` : format)
    }
    return 0
  }

  if (cmd === "env" && (ours.includes("--json") || ours.includes("--plain"))) {
    // For a program: what its model calls need to go through this gateway — the
    // same base URL, doorman pass and defaults `run` sets, without the identity
    // headers (a caller adds its own). Nothing when the daemon is not running.
    const base = `http://${cfg.host}:${cfg.port}`
    let vars: Record<string, string> = {}
    if (readPid()) {
      const pin = flag("account") ? await resolveAccount(base, flag("account") as string) : null
      if (pin && "error" in pin) {
        process.stderr.write(`${pin.error}\n`)
        return 1
      }
      vars = routing(pin ? `${base}/tc-acct/${pin.name}` : base)
    }
    if (ours.includes("--json")) json(vars)
    else for (const [k, v] of Object.entries(vars)) say(`${k}=${v}`)
    return 0
  }

  if (cmd === "env") {
    // For a human who wants to route one shell through the gateway by hand:
    // what `run` would change in this environment, as sh lines.
    const r = await terminalLaunch(flag("account"))
    if ("error" in r) {
      process.stderr.write(`${r.error}\n`)
      return 1
    }
    if (r.down) process.stderr.write(`warn: ${r.down}\n`)
    say(`# claude in this shell goes through :${cfg.port} — eval "$(${gw("env")})"`)
    for (const k of Object.keys(r.env).sort()) if (r.env[k] !== process.env[k]) say(`export ${k}=${shQuote(r.env[k] ?? "")}`)
    const gone = Object.keys(process.env).filter((k) => !(k in r.env))
    if (gone.length > 0) say(`unset ${gone.join(" ")}`)
    return 0
  }

  if (cmd === "probe") {
    const pid = readPid()
    if (!pid) {
      process.stderr.write(`gateway is not running — \`${gw("enable")}\` first\n`)
      return 1
    }
    const prompt = flag("prompt") ?? "Reply with exactly one word: pong"
    const model = flag("model") ?? "claude-haiku-4-5-20251001"
    const bin = flag("bin") ?? claudeBinary()
    if (!bin || !existsSync(bin)) {
      process.stderr.write(`${bin ? `no claude binary at ${bin}` : "no claude on PATH"} — pass --bin\n`)
      return 1
    }
    say(`probing :${cfg.port} with ${model}`)
    let plan
    try {
      plan = spawnable(bin, ["-p", prompt, "--model", model])
    } catch (err) {
      process.stderr.write(`${(err as Error).message} — pass --bin with a claude.exe\n`)
      return 1
    }
    const r = spawnSync(plan.command, plan.args, {
      ...plan.options,
      encoding: "utf8",
      timeout: 120_000,
      // CC_SHIM_DISABLE inside: the fragments pick the base URL themselves
      // whenever they claim a launch, and the probe would measure their choice.
      // The identity is what the wire keys on — the point of the exercise.
      env: routedEnv(`http://${cfg.host}:${cfg.port}`, [
        "x-brain-origin: probe",
        `x-brain-run: probe-${Date.now().toString(36)}`,
        "x-brain-agent: gateway-probe",
      ]),
    })
    say(`exit ${String(r.status)}`)
    if (r.stdout) say(`stdout: ${r.stdout.trim()}`)
    if (r.stderr?.trim()) process.stderr.write(`stderr: ${r.stderr.trim().slice(0, 2000)}\n`)
    return r.status ?? 1
  }

  process.stdout.write(
    [
      `${gw()} — the harness's wire: every claude call goes through it while it is on`,
      "",
      "  (nothing) · status    on or off, and what it is doing",
      "  enable                turn it on: start it, and route every new claude through it",
      "  disable               turn it off: claude goes back to its own login",
      "  status|enable|disable --json   one JSON document for a program: {running, pid, power, config} · {ok, steps, power}",
      "  account               the logins it serves — name, quota bars, expiry",
      "  account login [--api] [--name N]   add one (browser, or an API key)",
      "  account import [--from PATH]       take Claude Code's own login",
      "  account enable|disable|remove X · account priority X N",
      "  account choose        pick one on the terminal, print its name (a bare `claude --account`)",
      "  tui                   live screen: accounts, bars, traffic — q leaves, the gateway keeps running",
      "  tail [--n 20]         the last requests",
      "  tail --session [ID]   one session's requests, subagents included — this session's when run from claude",
      "  run [--account NAME] -- claude …   one program through the gateway, no cc-shim needed; refuses when it is off",
      "  try [--login] [--name N] [--port P] [--bin B] [-- claude args]",
      "                        one claude through a throwaway gateway on the next port, with its own login",
      "  xray … claude …       the exact prompt a claude launch would send — dry-run, nothing reaches Anthropic",
      "",
      "  restart [--now]       run this checkout's code — handed over with no gap, nothing cut",
      "  rollback              back to the build before, the same way",
      "",
      "  one half at a time:",
      `  start [--detach] · stop               the process (${service.manager().manager} once installed)`,
      `  service install|uninstall|status      the ${service.manager().manager} unit alone`,
      "  doctor                                can this machine run the service (WSL: needs systemd)",
      "  shim on|off|status                    the routing alone",
      "  reload · probe [--model M] · env [--account N]   re-read accounts · one real request · what run sets, as export lines",
      "  env --json | --plain  the variables that route a program's model calls here, as one object or KEY=value lines; none when it is off",
      "",
      "  --port P --account NAME   override config   · --trial   aim status/account/tail/tui/probe/env at `try`'s gateway",
      "",
    ].join("\n"),
  )
  return cmd === "help" || cmd === "--help" ? 0 : 1
}

main().then(
  (code) => process.exit(code),
  (err: Error) => {
    process.stderr.write(`${gw()}: ${err.message}\n`)
    process.exit(1)
  },
)
