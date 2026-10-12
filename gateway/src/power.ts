/**
 * On and off — the gateway's one switch, shared by the CLI and the app pane.
 *
 *   on    the service (launchd, systemd or Task Scheduler — service.ts) runs
 *         the daemon, and the cc-shim fragment routes every terminal `claude`
 *         through it. The app's runner routes whenever the daemon's pid file
 *         is live, so it follows on its own.
 *   off   the fragment is removed and the service unloaded: claude reaches
 *         Anthropic with its own login, as if the gateway had never been here.
 *
 * Routing is the second step on the way up and the first on the way down, so a
 * `claude` launched mid-switch never lands on a gateway that is not there.
 */
import { command as gw } from "./command.ts"
import type { Config } from "./config.ts"
import { readPool } from "./pool.ts"
import { gatewayDirOf, handOver, keeperFresh, keeperPath, liveKeeper, preflight, prune, quietMoment, snapshot } from "./release.ts"
import type { Release } from "./release.ts"
import * as service from "./service.ts"
import type { UnitState } from "./service.ts"
import { shimOff, shimOn, shimState } from "./shim.ts"
import type { ShimState } from "./shim.ts"

export type Power = {
  /** Both halves are on. */
  on: boolean
  /** Exactly one half is on — `enable` or `disable` settles it either way. */
  partial: boolean
  service: UnitState
  shim: ShimState
  /** Enabled accounts in the file. An enabled gateway with none serves nothing. */
  accounts: number
}

export type Switched = { ok: boolean; steps: string[]; power: Power }

const enabledAccounts = (cfg: Config): number => readPool(cfg.accountsFile).accounts.filter((a) => !a.disabled).length

export function powerState(cfg: Config): Power {
  const svc = service.state()
  const shim = shimState()
  const up = svc.loaded
  return { on: up && shim.installed, partial: up !== shim.installed, service: svc, shim, accounts: enabledAccounts(cfg) }
}

/** The daemon answering, not merely loaded — the service manager reports a pid before the port is bound. */
async function answers(cfg: Config, ms: number): Promise<boolean> {
  const deadline = Date.now() + ms
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://${cfg.host}:${cfg.port}/_gateway/health`, { signal: AbortSignal.timeout(500) })
      if (r.ok) return true
    } catch {
      // Not bound yet.
    }
    await new Promise((r) => setTimeout(r, 150))
  }
  return false
}

/**
 * The build a real (un-handed-over) start will run: copied from the checkout
 * and booted once on a spare port first. Null, with the reason in `steps`, when
 * it is a worktree or the build cannot start — nothing has been touched then.
 */
async function prepare(cfg: Config, daemonEntry: string, steps: string[]): Promise<Release | null> {
  const wt = service.worktreeOf(daemonEntry)
  if (wt) {
    steps.push(`service   ${wt} is a worktree — its code is unmerged. Run this from a main checkout or an installed copy.`)
    return null
  }
  const rel = snapshot(cfg, gatewayDirOf(daemonEntry))
  const check = await preflight(cfg, rel.id)
  if (!check.ok) {
    steps.push(`build     ${rel.id} ${check.detail} — nothing was restarted`)
    return null
  }
  return rel
}

export async function enable(cfg: Config, daemonEntry: string): Promise<Switched> {
  const steps: string[] = []
  const done = (ok: boolean): Switched => ({ ok, steps, power: powerState(cfg) })
  const n = enabledAccounts(cfg)
  if (n === 0) {
    steps.push(`no accounts in ${cfg.accountsFile} — add one first: ${gw("account login")}`)
    return done(false)
  }

  const svc = service.state()
  // A unit from before the keeper runs a checkout directly; a stopped one is rewritten on the way up.
  const preKeeper = svc.installed && !(svc.program ?? []).includes(keeperPath(cfg))
  if (!svc.installed || (preKeeper && !svc.loaded)) {
    const rel = await prepare(cfg, daemonEntry, steps)
    if (!rel) return done(false)
    const r = service.install(cfg, daemonEntry, rel)
    steps.push(r.ok ? `service   installed — ${r.detail}` : `service   ${r.detail}`)
    if (!r.ok) return done(false)
    if (r.warn) steps.push(`warn:     ${r.warn}`)
  } else if (!svc.loaded) {
    const r = service.load()
    steps.push(`service   ${r.detail}`)
    if (!r.ok) return done(false)
  } else steps.push(`service   already running${svc.pid ? ` (pid ${svc.pid})` : ""}`)

  if (!(await answers(cfg, 8000))) {
    steps.push(`service   loaded, but nothing answers on :${cfg.port} — routing left off; see ${cfg.dataDir}/gateway.log`)
    return done(false)
  }

  const shim = shimOn(cfg)
  steps.push(`routing   on — every new claude goes through :${cfg.port} (${shim.path})`)
  steps.push(`accounts  ${n} serving`)
  return done(true)
}

/**
 * Put this checkout's gateway on the wire.
 *
 * Under a keeper that is current, a hand-over: the new build starts beside the
 * old, takes every new connection once it is up, and the old one finishes its
 * open replies before it exits. No gap, nothing cut, and a build that fails to
 * start never replaces the one serving.
 *
 * With no keeper yet (a gateway from before it), or a keeper this checkout has
 * changed, one real restart puts it in place. The build is booted once on a
 * spare port first, and the restart waits for a moment with nothing in flight
 * — `now` skips the wait — so the sub-second gap lands where nobody is
 * mid-reply, and claude's own retry covers a call that starts in it.
 */
export async function restart(
  cfg: Config,
  daemonEntry: string,
  opts: { now?: boolean; waitMs?: number; progress?: (line: string) => void } = {},
): Promise<Switched> {
  const steps: string[] = []
  const done = (ok: boolean): Switched => ({ ok, steps, power: powerState(cfg) })
  const progress = opts.progress ?? (() => {})
  const svc = service.state()
  if (!svc.loaded) {
    steps.push(`service   not running — \`${gw("enable")}\` starts it`)
    return done(false)
  }
  const wt = service.worktreeOf(daemonEntry)
  if (wt) {
    steps.push(`restart   ${wt} is a worktree — its code is unmerged. Restart from a main checkout or an installed copy.`)
    return done(false)
  }
  const gatewayDir = gatewayDirOf(daemonEntry)
  const keeper = liveKeeper(cfg)
  const underKeeper = keeper !== null && keeper.pid === svc.pid && (svc.program ?? []).includes(keeperPath(cfg))

  if (underKeeper && keeperFresh(cfg, gatewayDir)) {
    const rel = snapshot(cfg, gatewayDir)
    const r = await handOver(cfg, rel.id)
    if (!r.ok) {
      steps.push(`restart   ${rel.id} did not take over — ${r.detail}`)
      return done(false)
    }
    steps.push(`restart   build ${rel.id} serving — no gap (${r.detail})`)
    if ((r.state?.draining.length ?? 0) > 0) steps.push("          the build before it finishes its open replies, then exits")
    prune(cfg)
    return done(true)
  }

  progress(
    underKeeper
      ? "keeper    this checkout changes the keeper itself — one short restart puts it in place"
      : "keeper    not in place yet — one short restart puts it in, and every restart after is a hand-over",
  )
  const rel = await prepare(cfg, daemonEntry, steps)
  if (!rel) return done(false)
  progress(`build     ${rel.id} starts and serves (checked on a spare port)`)
  if (!opts.now) {
    const q = await quietMoment(cfg, opts.waitMs ?? 10 * 60_000, (open, finishing) =>
      progress(
        `waiting   for a quiet moment — ${open} repl${open === 1 ? "y" : "ies"} in flight` +
          (finishing ? `, ${finishing} older build${finishing === 1 ? "" : "s"} still finishing` : ""),
      ),
    )
    if (!q.quiet) {
      steps.push(`restart   not done — still ${q.open + q.finishing} busy after waiting. \`${gw("restart")} --now\` cuts them`)
      return done(false)
    }
  }
  const r = service.install(cfg, daemonEntry, rel)
  if (!r.ok) {
    steps.push(`service   ${r.detail}`)
    return done(false)
  }
  if (!(await answers(cfg, 25_000))) {
    steps.push(`service   ${r.detail}, but nothing answers on :${cfg.port} — see ${cfg.dataDir}/gateway.log`)
    return done(false)
  }
  steps.push(`restart   ${r.detail} — the keeper is in place; the next restart is a hand-over`)
  if (r.warn) steps.push(`warn:     ${r.warn}`)
  prune(cfg)
  return done(true)
}

/** Back to the build that served before this one — a hand-over like any restart. */
export async function rollback(cfg: Config): Promise<Switched> {
  const steps: string[] = []
  const done = (ok: boolean): Switched => ({ ok, steps, power: powerState(cfg) })
  const keeper = liveKeeper(cfg)
  if (!keeper) {
    steps.push(`rollback  no keeper running — \`${gw("restart")}\` puts one in place`)
    return done(false)
  }
  if (!keeper.previous) {
    steps.push(`rollback  nothing before ${keeper.current ?? "this build"} to go back to`)
    return done(false)
  }
  const r = await handOver(cfg, keeper.previous)
  steps.push(r.ok ? `rollback  build ${keeper.previous} serving again — no gap (${r.detail})` : `rollback  ${r.detail}`)
  return done(r.ok)
}

export function disable(cfg: Config): Switched {
  const steps: string[] = []
  const was = shimState().installed
  shimOff()
  steps.push(was ? "routing   off — new claude sessions use their own login" : "routing   already off")
  const r = service.unload()
  steps.push(`service   ${r.ok ? r.detail : `could not stop: ${r.detail}`}`)
  if (was) steps.push("sessions  started while it was on still point at it — restart them")
  return { ok: r.ok, steps, power: powerState(cfg) }
}
