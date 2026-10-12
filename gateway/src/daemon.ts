/**
 * Daemon entry — one gateway process.
 *
 * Two ways to run. Under the service it is a worker of the keeper (keeper.ts),
 * started from a release: the keeper owns the pid file and the port, and talks
 * to it in messages — `ready` (sent: serving), `adopt` (received: take the
 * tokens — refresh and write them from now on), `drain` (received: stop owning
 * the tokens, say `quiet` once the last write has landed, finish the open
 * replies, exit). It boots without the tokens and serves with what the file
 * holds until `adopt`. By hand (`try`, a foreground `start`) it is on its own:
 * it owns the tokens from boot, writes its pid file, and a stop drains it with
 * a 3 s cap — `POST /_gateway/stop` on every OS, SIGTERM or SIGINT on POSIX.
 */
import cluster from "node:cluster"
import { writeFileSync, unlinkSync, existsSync } from "node:fs"
import { ensureDirs, loadConfig, paths } from "./config.ts"
import { createServer } from "./server.ts"
import { env as brandEnv } from "./brand.ts"
import { command as gw } from "./command.ts"

type Msg = { t?: string; capMs?: number; handover?: boolean }

const cfg = loadConfig()
ensureDirs(cfg)
const p = paths(cfg)
const underKeeper = cluster.isWorker

// Listening before the engine boots: a drain sent while this worker is still
// starting (a stop that lands during a restart) is held, not dropped, and
// handled once it serves. A signal before then has nothing to finish.
const held: Msg[] = []
let onMessage: (m: Msg) => void = (m) => void held.push(m)
if (underKeeper) process.on("message", (m: Msg) => onMessage(m))
let onSignal: (sig: string) => void = () => process.exit(0)
process.on("SIGINT", () => onSignal("SIGINT"))
process.on("SIGTERM", () => onSignal("SIGTERM"))

// `POST /_gateway/stop`: under the keeper the keeper stops (it drains every
// worker); on its own it is the same stop a signal gives.
const onStop = () => (underKeeper ? process.send?.({ t: "stop" }) : onSignal("stop requested"))
const { server, drain, stats, engine } = await createServer(cfg, { owner: !underKeeper, onStop })

server.on("error", (err: NodeJS.ErrnoException) => {
  if (err.code === "EADDRINUSE") {
    process.stderr.write(
      `gateway: port ${cfg.port} is already in use. Another gateway may be running ` +
        `(${gw("status")}), or something else owns it.\n`,
    )
    process.exit(2)
  }
  process.stderr.write(`gateway: ${err.message}\n`)
  process.exit(1)
})

let leaving = false

/**
 * Stop owning tokens, finish what is open, exit. A hand-over leaves the quota
 * state to the gateway taking over; a plain stop writes it, so the next start
 * remembers which account is nearly spent.
 */
async function stepDown(why: string, capMs: number, handingOver: boolean): Promise<void> {
  if (leaving) return
  leaving = true
  const open = stats().inFlight
  process.stderr.write(`gateway: ${why} — pid ${process.pid} finishing ${open} open repl${open === 1 ? "y" : "ies"}, taking nothing new\n`)
  await engine.quiesce()
  process.send?.({ t: "quiet" })
  await drain(capMs, handingOver)
  if (!handingOver) await engine.persist()
  if (!underKeeper && existsSync(p.pid)) {
    try {
      unlinkSync(p.pid)
    } catch {
      // Racing another shutdown is fine; the file is advisory.
    }
  }
  process.exit(0)
}

function handle(m: Msg): void {
  if (m?.t === "drain")
    void stepDown(m.handover === false ? "stopping" : "handing over", m.capMs ?? 15 * 60_000, m.handover !== false)
  if (m?.t === "adopt" && !leaving)
    void engine.own().then(
      () => process.stderr.write(`gateway: pid ${process.pid} owns the tokens — re-read from ${cfg.accountsFile}\n`),
      (err: Error) => process.stderr.write(`gateway: taking the tokens failed: ${err.message}\n`),
    )
}

server.listen(cfg.port, cfg.host, () => {
  if (!underKeeper) writeFileSync(p.pid, String(process.pid), { mode: 0o600 })
  const n = engine.status().accounts.filter((a) => !a.disabled).length
  const build = brandEnv("GATEWAY_RELEASE")
  process.stderr.write(
    `gateway listening on http://${cfg.host}:${cfg.port} → ${cfg.upstreamUrl} ` +
      `(${n} account${n === 1 ? "" : "s"}, capture=${cfg.capture}, ${cfg.policy.length} rules` +
      `${build ? `, build ${build}, pid ${process.pid}` : ""})\n`,
  )
  process.send?.({ t: "ready" })
  onMessage = handle
  for (const m of held.splice(0)) handle(m)
  // Under the keeper a signal still means stop: it reaches the worker directly
  // only when someone kills it by pid, or with the whole group on a Ctrl-C.
  onSignal = (sig) => {
    void stepDown(sig, 3000, false)
    // A stuck upstream must not make the daemon unkillable.
    setTimeout(() => process.exit(0), 3500).unref()
  }
})
