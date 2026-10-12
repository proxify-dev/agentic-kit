/**
 * The keeper — what the service manager (launchd, systemd, Task Scheduler) runs
 * as the gateway's unit. It holds the port and never serves a request: the
 * gateway runs as its child, a cluster worker that accepts straight off the
 * shared socket (SCHED_NONE), so the keeper is not in the request path at all.
 * What it buys is a restart without a gap:
 *
 *   roll     start the release named in `wanted`. Once it is listening and has
 *            stood for a moment beside the old one, the old worker stops taking
 *            connections, finishes the replies it has open and exits. A release
 *            that fails to come up, or dies in that moment, never replaces the
 *            one serving — the failure lands in keeper.json, nothing else moves.
 *   crash    the serving worker died: start the current release again, at once
 *            and then backing off. Three crashes inside a minute and the
 *            previous release takes over.
 *   stop     every worker drains (3 s cap, as a plain daemon did) and the keeper
 *            exits — `disable`, `stop`, logout.
 *
 * A request arrives as a `control` file beside it (release.ts `tellKeeper`),
 * polled, so it needs no signal and means the same on every OS. On POSIX the
 * signals still work: SIGHUP rolls, SIGTERM and SIGINT stop. A worker can ask
 * for the stop too (`POST /_gateway/stop`).
 *
 * `--log FILE` sends what it and its workers print to FILE instead of stderr,
 * and `--env K=V` sets a variable first — for Task Scheduler, which neither
 * redirects output nor sets an environment.
 *
 * Every start and every swap runs one at a time (`serial`), so a restart that
 * lands during crash recovery, or a stop during a restart, can never leave two
 * workers serving or one serving nobody knows about.
 *
 * Token ownership. A worker boots without the right to refresh or write tokens
 * and gets it with `adopt`. In a hand-over the outgoing one is told first: it
 * stops starting refreshes, lets the ones already under way land in the file,
 * and says `quiet`; only then is the incoming one told to `adopt` — re-read the
 * file and take over. Two engines never rotate the same grant.
 *
 * It imports nothing from the gateway but brand.ts (constants, no imports of
 * its own), because it is the one file that cannot be swapped under traffic,
 * and it must not die: a keeper exit takes every worker with it. `service
 * install` copies it to <dataDir>/keeper.mts and brand.ts beside it, and every
 * path it uses is beside that copy. What it runs is always a release,
 * <dataDir>/releases/<id> — a copy of gateway/src that `ak gateway restart`
 * made — never a checkout, so a merge changes nothing live.
 */
import cluster from "node:cluster"
import type { Worker } from "node:cluster"
import { existsSync, openSync, readFileSync, renameSync, unlinkSync, watchFile, writeFileSync, writeSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { env as brandEnv, envName, isWindows } from "./brand.ts"

/** command.ts's command(), inlined: the keeper runs from its installed copy with brand.ts alone beside it. */
const gw = (verb: string): string => `${brandEnv("GATEWAY_COMMAND", "cc-gateway")} ${verb}`

/** fsperm.ts's renameRetrySync, copied: the keeper runs from its installed copy with brand.ts alone beside it. */
function renameRetrySync(from: string, to: string): void {
  for (let i = 0; ; i++) {
    try {
      return renameSync(from, to)
    } catch (err) {
      const code = (err as NodeJS.ErrnoException).code
      if (!isWindows() || i >= 5 || (code !== "EPERM" && code !== "EBUSY" && code !== "EACCES")) throw err
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 50)
    }
  }
}

const argv = process.argv.slice(2)
for (let i = 0; i < argv.length - 1; i++)
  if (argv[i] === "--env" && argv[i + 1].includes("=")) {
    const kv = argv[i + 1]
    process.env[kv.slice(0, kv.indexOf("="))] = kv.slice(kv.indexOf("=") + 1)
  }
const logAt = argv.indexOf("--log")
const logFd = logAt >= 0 && argv[logAt + 1] ? openSync(argv[logAt + 1], "a", 0o600) : null
const out = (d: string | Buffer): void => {
  if (logFd === null) process.stderr.write(d)
  else writeSync(logFd, typeof d === "string" ? Buffer.from(d) : d)
}

const home = dirname(fileURLToPath(import.meta.url))
const releases = join(home, "releases")
const stateFile = join(home, "keeper.json")
const wantedFile = join(home, "wanted")
const controlFile = join(home, "control")
const pidFile = join(home, "gateway.pid")

/** A release that has not bound its port by now is broken. */
const BOOT_MS = Number(brandEnv("GATEWAY_BOOT_MS") ?? 20_000)
/** The longest a hand-over waits for the old worker's open replies. A reply runs minutes, not hours. */
const DRAIN_MS = Number(brandEnv("GATEWAY_DRAIN_MS") ?? 15 * 60_000)
const STOP_MS = 3000
/** How long a new build serves beside the old one before the old one is retired. */
const PROBATION_MS = Number(brandEnv("GATEWAY_PROBATION_MS") ?? 1500)

type Last = { at: string; keeper: number; want: string; ok: boolean; detail: string }
export type KeeperState = {
  pid: number
  since: string
  /** The release serving now. */
  current: string | null
  /** The one before it — `ak gateway rollback` goes back to it. */
  previous: string | null
  worker: number | null
  /** Old workers still finishing their replies. */
  draining: number[]
  last: Last | null
  /** It watches the `control` file — ask it there, not by signal. */
  control: boolean
}

type Run = { w: Worker; release: string; tail: string[]; up: boolean; draining: boolean; quiet: Promise<void> }

const log = (line: string) => out(`${new Date().toISOString()} keeper: ${line}\n`)

// A keeper that dies takes every worker and every open reply with it. Nothing
// a worker does may reach here as an exception; if something does, say so and
// keep holding the port.
process.on("uncaughtException", (err) => log(`uncaught: ${err.stack ?? err.message}`))
process.on("unhandledRejection", (err) => log(`unhandled: ${err instanceof Error ? (err.stack ?? err.message) : String(err)}`))

const readState = (): Partial<KeeperState> => {
  try {
    return JSON.parse(readFileSync(stateFile, "utf8")) as Partial<KeeperState>
  } catch {
    return {}
  }
}

const saved = readState()
const state: KeeperState = {
  pid: process.pid,
  since: new Date().toISOString(),
  current: saved.current ?? null,
  previous: saved.previous ?? null,
  worker: null,
  draining: [],
  last: saved.last ?? null,
  control: true,
}

const runs = new Set<Run>()
let serving: Run | null = null
let stopping = false

function writeState(): void {
  state.worker = serving?.w.process.pid ?? null
  state.draining = [...runs].filter((r) => r.draining).map((r) => r.w.process.pid ?? 0)
  try {
    const tmp = `${stateFile}.${process.pid}`
    writeFileSync(tmp, `${JSON.stringify(state, null, 2)}\n`, { mode: 0o600 })
    renameRetrySync(tmp, stateFile)
  } catch (err) {
    log(`could not write ${stateFile}: ${(err as Error).message}`)
  }
}

function record(want: string, ok: boolean, detail: string): void {
  state.last = { at: new Date().toISOString(), keeper: process.pid, want, ok, detail }
  log(`${ok ? "" : "FAILED "}${want}: ${detail}`)
  writeState()
}

const readWanted = (): string | null => {
  try {
    const id = readFileSync(wantedFile, "utf8").trim()
    return /^[\w.-]+$/.test(id) ? id : null
  } catch {
    return null
  }
}

/** Point `wanted` at what serves, so a later boot never starts a build known to fail first. */
function settleWanted(): void {
  if (!state.current || readWanted() === state.current) return
  try {
    const tmp = `${wantedFile}.${process.pid}`
    writeFileSync(tmp, `${state.current}\n`, { mode: 0o600 })
    renameRetrySync(tmp, wantedFile)
  } catch (err) {
    log(`could not write ${wantedFile}: ${(err as Error).message}`)
  }
}

/** A message to a worker that may be gone — never an exception, never an unhandled 'error'. */
function tell(run: Run, msg: Record<string, unknown>): boolean {
  if (run.w.isDead() || !run.w.isConnected()) return false
  try {
    run.w.send(msg, (err: Error | null) => {
      if (err) log(`pid ${run.w.process.pid}: ${String(msg.t)} not delivered: ${err.message}`)
    })
    return true
  } catch (err) {
    log(`pid ${run.w.process.pid}: ${String(msg.t)} not delivered: ${(err as Error).message}`)
    return false
  }
}

let chain: Promise<unknown> = Promise.resolve()
/** Starts and swaps, one at a time, in the order asked. */
function serial<T>(fn: () => Promise<T>): Promise<T> {
  const run = chain.then(fn)
  chain = run.then(
    () => {},
    () => {},
  )
  return run
}

// The worker accepts off the shared socket itself; the keeper only hands it over.
cluster.schedulingPolicy = cluster.SCHED_NONE

/** Fork one release and resolve once it says it is serving. */
function start(release: string): Promise<Run> {
  const entry = join(releases, release, "src", "daemon.ts")
  if (!existsSync(entry)) return Promise.reject(new Error(`no release at ${dirname(dirname(entry))}`))
  cluster.setupPrimary({ exec: entry, execArgv: ["--experimental-strip-types", "--no-warnings"], silent: true, windowsHide: true })
  const w = cluster.fork({ [envName("GATEWAY_RELEASE")]: release, [envName("GATEWAY_KEEPER")]: String(process.pid) })
  let quieted = () => {}
  const run: Run = { w, release, tail: [], up: false, draining: false, quiet: new Promise((r) => (quieted = r)) }
  runs.add(run)
  // A send to a worker whose channel just closed surfaces here, not as a throw.
  w.on("error", (err: Error) => log(`pid ${w.process.pid}: ${err.message}`))
  // Its output goes to our log, and the last lines stay at hand to say why it died.
  const take = (d: Buffer) => {
    out(d)
    for (const line of d.toString("utf8").split("\n")) if (line) run.tail.push(line)
    if (run.tail.length > 30) run.tail.splice(0, run.tail.length - 30)
  }
  w.process.stdout?.on("data", take)
  w.process.stderr?.on("data", take)
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      w.process.kill("SIGKILL")
      reject(new Error(`did not start serving within ${BOOT_MS / 1000}s`))
    }, BOOT_MS)
    w.on("message", (m: { t?: string }) => {
      if (m?.t === "quiet") quieted()
      if (m?.t === "stop") shutdown(`stop asked by pid ${w.process.pid}`)
      if (m?.t !== "ready" || run.up) return
      clearTimeout(timer)
      run.up = true
      resolve(run)
    })
    w.once("exit", (code, signal) => {
      clearTimeout(timer)
      quieted()
      if (!run.up)
        reject(new Error(`exited ${signal ?? `with code ${code}`} before serving${run.tail.length ? `:\n  ${run.tail.slice(-8).join("\n  ")}` : ""}`))
    })
  })
}

/**
 * Stop taking connections, finish what is open, exit. A worker that hangs is
 * killed after the cap. `handover` false is a real stop: nobody takes over, so
 * the worker writes its quota state on the way out.
 */
function drain(run: Run, capMs: number, handover = true): void {
  if (run.draining) return
  run.draining = true
  if (run === serving) serving = null
  // Delivered even to a worker still booting: it holds the message until it is up.
  if (!tell(run, { t: "drain", capMs, handover })) run.w.process.kill("SIGTERM")
  setTimeout(() => {
    if (!run.w.isDead()) run.w.process.kill("SIGKILL")
  }, capMs + 5000).unref()
  writeState()
}

/** The new worker owns the tokens only once the old one has stopped writing them. */
async function handOver(from: Run | null, to: Run): Promise<void> {
  if (from) {
    drain(from, DRAIN_MS)
    await Promise.race([from.quiet, new Promise((r) => setTimeout(r, 2000))])
  }
  tell(to, { t: "adopt" })
}

/** A worker that came up after a stop began: it must not serve. */
function unwanted(run: Run): boolean {
  if (!stopping) return false
  drain(run, STOP_MS, false)
  return true
}

let rollQueued = false

/** A roll request: queue one roll. Requests that land while one waits are the same roll. */
function requestRoll(): void {
  if (rollQueued || stopping) return
  rollQueued = true
  void serial(async () => {
    rollQueued = false
    await rollOnce()
  })
}

async function rollOnce(): Promise<void> {
  if (stopping) return
  const want = readWanted() ?? state.current
  if (!want) {
    record("-", false, `no release named — \`${gw("restart")}\` makes one`)
    return
  }
  let next: Run
  try {
    next = await start(want)
  } catch (err) {
    record(want, false, `${(err as Error).message} — still serving ${serving ? state.current : "nothing"}`)
    settleWanted()
    if (!serving) scheduleRevive()
    return
  }
  if (unwanted(next)) return
  // Probation: both serve for a moment (the new one still quiet, the old one
  // still the owner). A build that dies right after it came up is dropped here,
  // while the old one has not been told anything.
  await new Promise((r) => setTimeout(r, PROBATION_MS))
  if (next.w.isDead()) {
    record(want, false, `pid ${next.w.process.pid} died within ${PROBATION_MS / 1000}s of starting — still serving ${state.current ?? "nothing"}`)
    settleWanted()
    if (!serving) scheduleRevive()
    return
  }
  if (unwanted(next)) return
  const old = serving
  serving = next
  if (state.current !== want) {
    state.previous = state.current
    state.current = want
  }
  crashes.length = 0
  await handOver(old, next)
  // Recorded once the swap is done and the new worker still stands — not at `ready`.
  if (next.w.isDead()) record(want, false, `pid ${next.w.process.pid} died during the hand-over`)
  else record(want, true, old ? `pid ${old.w.process.pid} → pid ${next.w.process.pid}` : `pid ${next.w.process.pid}`)
}

const crashes: number[] = []
let reviving = false

/** The serving worker died without being asked to. Bring the current release back, or the previous one. */
function scheduleRevive(): void {
  if (reviving || stopping) return
  reviving = true
  const now = Date.now()
  crashes.push(now)
  while (crashes.length && now - crashes[0] > 60_000) crashes.shift()
  // The first death restarts at once: the port is dark until a worker holds it again.
  const delay = crashes.length <= 1 ? 0 : Math.min(250 * 2 ** (crashes.length - 1), 30_000)
  setTimeout(() => {
    void serial(async () => {
      reviving = false
      if (stopping || serving) return
      if (crashes.length >= 3 && state.previous && state.previous !== state.current) {
        const bad = state.current as string
        state.current = state.previous
        state.previous = bad
        crashes.length = 0
        settleWanted()
        record(state.current, true, `${bad} crashed 3 times in a minute — back on ${state.current}`)
      }
      if (!state.current) return
      try {
        const run = await start(state.current)
        if (unwanted(run)) return
        serving = run
        await handOver(null, run)
        writeState()
      } catch (err) {
        log(`could not restart ${state.current}: ${(err as Error).message}`)
        scheduleRevive()
      }
    })
  }, delay)
}

cluster.on("exit", (w, code, signal) => {
  const run = [...runs].find((r) => r.w === w)
  if (!run) return
  runs.delete(run)
  if (run.draining) log(`pid ${w.process.pid} (${run.release}) drained and exited`)
  else if (run.up) log(`pid ${w.process.pid} (${run.release}) died ${signal ?? `with code ${code}`}`)
  if (run === serving) {
    serving = null
    scheduleRevive()
  }
  if (stopping && runs.size === 0) finish()
  else writeState()
})

function finish(): void {
  try {
    if (readFileSync(pidFile, "utf8").trim() === String(process.pid)) unlinkSync(pidFile)
  } catch {
    // Advisory file; someone else may have taken it.
  }
  writeState()
  process.exit(0)
}

function shutdown(sig: string): void {
  if (stopping) return
  stopping = true
  log(`${sig}, stopping ${runs.size} worker${runs.size === 1 ? "" : "s"}`)
  for (const r of runs) drain(r, STOP_MS, false)
  if (runs.size === 0) finish()
  setTimeout(finish, STOP_MS + 1000).unref()
}

process.on("SIGTERM", () => shutdown("SIGTERM"))
process.on("SIGINT", () => shutdown("SIGINT"))
// Windows raises SIGHUP when a console closes: that is not a roll.
if (!isWindows()) process.on("SIGHUP", () => requestRoll())

/** What `control` asks for: the first word, `roll` or `stop`. */
const readControl = (): string | null => {
  try {
    return readFileSync(controlFile, "utf8").trim().split(/\s+/)[0] || null
  } catch {
    return null
  }
}

// A stat poll, not fs.watch: it behaves the same on every OS and filesystem.
// Only a change after boot counts, so a request left from an earlier keeper is
// never replayed.
watchFile(controlFile, { interval: 200 }, (now) => {
  if (now.mtimeMs === 0) return // removed
  const op = readControl()
  if (op === "roll") requestRoll()
  else if (op === "stop") shutdown("stop requested")
}).unref()

writeFileSync(pidFile, String(process.pid), { mode: 0o600 })

// Boot: what was asked for, else what last ran, else the one before it.
await serial(async () => {
  for (const id of [...new Set([readWanted(), state.current, state.previous])]) {
    if (!id || stopping) continue
    let run: Run
    try {
      run = await start(id)
    } catch (err) {
      record(id, false, (err as Error).message)
      continue
    }
    if (unwanted(run)) return
    serving = run
    if (state.current !== id) {
      state.previous = state.current
      state.current = id
    }
    settleWanted()
    await handOver(null, run)
    record(id, true, `pid ${run.w.process.pid}`)
    return
  }
})
if (!serving && !stopping) {
  log("nothing could start — exiting for the service manager to try again")
  process.exit(1)
}
