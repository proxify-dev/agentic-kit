/**
 * Releases — what the keeper (keeper.ts) runs. A release is a copy of
 * gateway/src and its package.json under <dataDir>/releases/<id>, made from a
 * checkout by `enable` and `restart`. What serves is always one of these, never
 * the checkout, so a merge, a branch switch or a deleted worktree changes
 * nothing live until the next restart — and `rollback` has somewhere to go.
 *
 * The CLI and the keeper meet in four files beside the keeper's copy:
 * `wanted` (written here: the release to run next), `control` (written here: a
 * request — roll or stop — the keeper watches for), keeper.json (written by the
 * keeper: what runs, what ran before, how the last hand-over went) and the pid
 * file. A hand-over is `wanted` + a roll request, answered in keeper.json. The
 * request is a file and not a signal so it means the same on Windows, which has
 * no SIGHUP; a keeper from before `control` gets the SIGHUP it knows.
 */
import { fork, spawnSync } from "node:child_process"
import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import type { Config } from "./config.ts"
import type { KeeperState } from "./keeper.ts"
import { envName, pidAlive } from "./brand.ts"
import { command as gw } from "./command.ts"
import { renameRetrySync } from "./fsperm.ts"

export type { KeeperState }

export type Release = {
  id: string
  /** The checkout's HEAD when it was copied; null outside git. */
  commit: string | null
  /** gateway/ had uncommitted changes — the copy has them, the commit does not. */
  dirty: boolean
  at: string
  from: string
}

export const releasesDir = (cfg: Config) => join(cfg.dataDir, "releases")
/** The keeper the service runs — a copy, so a merge never changes it under a running service. */
export const keeperPath = (cfg: Config) => join(cfg.dataDir, "keeper.mts")
/** The brand words the keeper reads, installed beside it: its one import. */
export const keeperBrandPath = (cfg: Config) => join(cfg.dataDir, "brand.ts")
export const keeperBrandSource = (gatewayDir: string) => join(gatewayDir, "src", "brand.ts")
/** The keeper a checkout carries — what `keeperPath` should be a copy of. */
export const keeperSource = (gatewayDir: string) => join(gatewayDir, "src", "keeper.ts")
/** <checkout>/gateway, from the daemon entry every caller already holds. */
export const gatewayDirOf = (daemonEntry: string) => dirname(dirname(daemonEntry))

const stateFile = (cfg: Config) => join(cfg.dataDir, "keeper.json")
const wantedFile = (cfg: Config) => join(cfg.dataDir, "wanted")
/** Where a request to the keeper lands; keeper.ts watches the same name beside itself. */
export const controlFile = (dataDir: string) => join(dataDir, "control")

const alive = pidAlive

/**
 * Ask the keeper under `dataDir` to roll (start what `wanted` names) or stop.
 * A new file each time (temp and rename), so the keeper's stat poll sees it
 * even inside one mtime tick.
 */
export function tellKeeper(dataDir: string, op: "roll" | "stop"): void {
  const file = controlFile(dataDir)
  const tmp = `${file}.${process.pid}`
  writeFileSync(tmp, `${op} ${Date.now()} ${process.pid}\n`, { mode: 0o600 })
  renameRetrySync(tmp, file)
}

function git(dir: string, ...args: string[]): string | null {
  const r = spawnSync("git", ["-C", dir, ...args], { encoding: "utf8" })
  return r.status === 0 ? r.stdout.trim() : null
}

const pad = (n: number) => String(n).padStart(2, "0")
const stamp = (d: Date) =>
  `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`

/** Copy the checkout's gateway into a new release. It appears whole or not at all. */
export function snapshot(cfg: Config, gatewayDir: string): Release {
  const commit = git(gatewayDir, "rev-parse", "--short", "HEAD")
  const dirty = commit !== null && (git(gatewayDir, "status", "--porcelain", "--", ".") ?? "") !== ""
  const root = releasesDir(cfg)
  mkdirSync(root, { recursive: true, mode: 0o700 })
  const base = `${stamp(new Date())}-${commit ?? "local"}${dirty ? "-dirty" : ""}`
  let id = base
  for (let n = 2; existsSync(join(root, id)); n++) id = `${base}-${n}`
  const tmp = join(root, `.${id}.partial`)
  rmSync(tmp, { recursive: true, force: true })
  mkdirSync(tmp, { recursive: true, mode: 0o700 })
  cpSync(join(gatewayDir, "src"), join(tmp, "src"), { recursive: true })
  copyFileSync(join(gatewayDir, "package.json"), join(tmp, "package.json"))
  const rel: Release = { id, commit, dirty, at: new Date().toISOString(), from: gatewayDir }
  writeFileSync(join(tmp, "release.json"), `${JSON.stringify(rel, null, 2)}\n`)
  renameRetrySync(tmp, join(root, id))
  return rel
}

export function listReleases(cfg: Config): string[] {
  const root = releasesDir(cfg)
  if (!existsSync(root)) return []
  // The id starts with its timestamp, so name order is age order.
  return readdirSync(root).filter((d) => !d.startsWith(".") && existsSync(join(root, d, "src", "daemon.ts"))).sort()
}

export function readRelease(cfg: Config, id: string): Release | null {
  try {
    return JSON.parse(readFileSync(join(releasesDir(cfg), id, "release.json"), "utf8")) as Release
  } catch {
    return null
  }
}

/** Keep the newest `keep`, and never the one serving, the one before it, or the one asked for. */
export function prune(cfg: Config, keep = 5): string[] {
  const st = readKeeper(cfg)
  const protect = new Set([st?.current, st?.previous, readWanted(cfg)].filter(Boolean))
  const all = listReleases(cfg)
  const drop = all.slice(0, Math.max(0, all.length - keep)).filter((id) => !protect.has(id))
  for (const id of drop) rmSync(join(releasesDir(cfg), id), { recursive: true, force: true })
  return drop
}

export function readKeeper(cfg: Config): KeeperState | null {
  try {
    return JSON.parse(readFileSync(stateFile(cfg), "utf8")) as KeeperState
  } catch {
    return null
  }
}

/** The keeper's state, only while that keeper is alive. */
export function liveKeeper(cfg: Config): KeeperState | null {
  const st = readKeeper(cfg)
  return st && alive(st.pid) ? st : null
}

export function readWanted(cfg: Config): string | null {
  try {
    return readFileSync(wantedFile(cfg), "utf8").trim() || null
  } catch {
    return null
  }
}

export function want(cfg: Config, id: string): void {
  const tmp = `${wantedFile(cfg)}.${process.pid}`
  writeFileSync(tmp, `${id}\n`, { mode: 0o600 })
  renameRetrySync(tmp, wantedFile(cfg))
}

/** The installed keeper is byte-for-byte the checkout's. */
export function keeperFresh(cfg: Config, gatewayDir: string): boolean {
  const installed = keeperPath(cfg)
  const source = keeperSource(gatewayDir)
  const brand = keeperBrandPath(cfg)
  return (
    existsSync(installed) && existsSync(source) && readFileSync(installed).equals(readFileSync(source)) &&
    existsSync(brand) && readFileSync(brand).equals(readFileSync(keeperBrandSource(gatewayDir)))
  )
}

export function installKeeper(cfg: Config, gatewayDir: string): void {
  mkdirSync(cfg.dataDir, { recursive: true, mode: 0o700 })
  // brand.ts first: a keeper that starts between the two renames must find what it imports.
  const brandTmp = `${keeperBrandPath(cfg)}.${process.pid}`
  copyFileSync(keeperBrandSource(gatewayDir), brandTmp)
  renameRetrySync(brandTmp, keeperBrandPath(cfg))
  const tmp = `${keeperPath(cfg)}.${process.pid}`
  copyFileSync(keeperSource(gatewayDir), tmp)
  renameRetrySync(tmp, keeperPath(cfg))
}

export type HandOver = { ok: boolean; detail: string; state: KeeperState | null }

/**
 * Ask the running keeper to serve `id`, and wait for its answer — the record it
 * writes once the swap is done and the new worker still stands. On a failure or
 * a timeout `wanted` goes back to what is serving, so a later keeper boot does
 * not try the broken release first.
 */
export async function handOver(cfg: Config, id: string, timeoutMs = 40_000): Promise<HandOver> {
  const st = liveKeeper(cfg)
  if (!st) return { ok: false, detail: "no keeper is running", state: readKeeper(cfg) }
  const before = st.last?.at ?? ""
  const settle = (s: KeeperState | null) => {
    if (s?.current && s.current !== id) want(cfg, s.current)
  }
  want(cfg, id)
  if (st.control) tellKeeper(cfg.dataDir, "roll")
  else process.kill(st.pid, "SIGHUP") // a keeper from before `control`: POSIX only, and there were none on Windows
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 100))
    if (!alive(st.pid)) {
      settle(readKeeper(cfg))
      return { ok: false, detail: `the keeper (pid ${st.pid}) exited during the hand-over — the service manager starts it again; \`${gw()}\` says what serves`, state: readKeeper(cfg) }
    }
    const now = readKeeper(cfg)
    // Only this keeper's answer to this request: a boot record from a relaunched
    // keeper, or a crash-recovery record, is not it.
    if (!now?.last || now.last.keeper !== st.pid || now.last.at === before || now.last.want !== id) continue
    if (!now.last.ok) settle(now)
    return { ok: now.last.ok, detail: now.last.detail, state: now }
  }
  settle(readKeeper(cfg))
  return { ok: false, detail: `the keeper did not answer within ${timeoutMs / 1000}s — see ${join(cfg.dataDir, "gateway.log")}`, state: readKeeper(cfg) }
}

/**
 * Boot a release once, on a free port with scratch data and no accounts, and
 * stop it — before the one restart that is not a hand-over, where a build that
 * cannot start would leave nothing serving. It reads the real config, so a
 * policy file that no longer validates fails here too.
 */
export async function preflight(cfg: Config, id: string, timeoutMs = 20_000): Promise<{ ok: boolean; detail: string }> {
  const scratch = mkdtempSync(join(tmpdir(), "gw-preflight-"))
  const env: Record<string, string> = {}
  for (const [k, v] of Object.entries(process.env))
    if (v !== undefined && k !== envName("GATEWAY_KEEPER") && k !== envName("GATEWAY_RELEASE")) env[k] = v
  Object.assign(env, {
    [envName("GATEWAY_PORT")]: "0",
    [envName("GATEWAY_DATA_DIR")]: scratch,
    [envName("GATEWAY_ACCOUNTS_FILE")]: join(scratch, "accounts.json"),
  })
  const child = fork(join(releasesDir(cfg), id, "src", "daemon.ts"), [], {
    execArgv: ["--experimental-strip-types", "--no-warnings"],
    env,
    stdio: ["ignore", "ignore", "pipe", "ipc"],
    // fork hands its options to spawn, whose windowsHide its types leave out: no console window on Windows; POSIX ignores it.
    ...({ windowsHide: true } as object),
  })
  let err = ""
  child.stderr?.on("data", (d: Buffer) => (err += d.toString()))
  const result = await new Promise<{ ok: boolean; detail: string }>((resolve) => {
    const timer = setTimeout(() => resolve({ ok: false, detail: `did not start serving within ${timeoutMs / 1000}s` }), timeoutMs)
    child.on("message", (m: { t?: string }) => {
      if (m?.t !== "ready") return
      clearTimeout(timer)
      resolve({ ok: true, detail: "starts and serves" })
    })
    child.once("exit", (code, signal) => {
      clearTimeout(timer)
      const tail = err.trim().split("\n").slice(-8).join("\n  ")
      resolve({ ok: false, detail: `exited ${signal ?? `with code ${code}`} before serving${tail ? `:\n  ${tail}` : ""}` })
    })
  })
  child.kill("SIGKILL")
  rmSync(scratch, { recursive: true, force: true })
  return result
}

/**
 * Wait until nothing is in flight — for the one restart that is not a hand-over
 * (the keeper itself being put in place). That counts the serving gateway's
 * replies and, under a keeper, any older build still finishing its own: the
 * status endpoint sees only the first, the restart would cut both. `onWait`
 * hears each change.
 */
export async function quietMoment(
  cfg: Config,
  maxMs: number,
  onWait: (open: number, finishing: number) => void = () => {},
): Promise<{ quiet: boolean; open: number; finishing: number }> {
  const deadline = Date.now() + maxMs
  let last = ""
  while (true) {
    let open = 0
    try {
      const r = await fetch(`http://${cfg.host}:${cfg.port}/_gateway/status`, { signal: AbortSignal.timeout(1000) })
      open = Number(((await r.json()) as { inFlight?: number }).inFlight ?? 0)
    } catch {
      // Nothing answers: nothing to interrupt.
      return { quiet: true, open: 0, finishing: 0 }
    }
    const finishing = liveKeeper(cfg)?.draining.length ?? 0
    if (open === 0 && finishing === 0) return { quiet: true, open, finishing }
    if (`${open}/${finishing}` !== last) onWait(open, finishing)
    last = `${open}/${finishing}`
    if (Date.now() >= deadline) return { quiet: false, open, finishing }
    await new Promise((r) => setTimeout(r, 50))
  }
}
