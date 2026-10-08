import {
  closeSync,
  existsSync,
  openSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
  writeSync,
} from "node:fs"
import { join } from "node:path"
import { pidAlive } from "./brand.ts"
import * as store from "./traces.ts"
import { BACKFILL_BYTES, pass } from "./traces-walk.ts"

/**
 * The one-shot indexer: walk the transcripts' new bytes into the store, then
 * exit.
 *
 * The walker used to run on a ten-second timer inside the app, so the store
 * filled only while a window was open — the one place that is, by definition,
 * sometimes closed. It runs here instead, in a short-lived process a Stop hook
 * launches after every turn (and the app launches once at start). A no-change
 * run is the ten thousand stats `candidates()` costs and one select of the
 * marks; a turn's worth of new lines is one more commit per 256 KB.
 *
 * **Correctness never depends on the lock.** Every write is an idempotent
 * upsert keyed by `tool_use_id` inside its own transaction, so two runs at once
 * produce the same rows as one. The lock only saves the duplicate WORK:
 *
 *   `walk.lock`   O_EXCL, holding the pid. Stale when that pid is dead or the
 *                 file is over an hour old — a run is capped far below that.
 *   `walk.again`  "someone wanted a pass". A caller touches it FIRST and only
 *                 then tries the lock; losing the race means exiting at once,
 *                 because the holder will see the flag. The holder deletes it
 *                 before each pass and passes again while it reappears.
 *
 * The holder re-checks `again` only AFTER releasing the lock. Checked before,
 * a caller could touch it between the check and the release, find the lock
 * still held, and exit — and nobody would walk its turn until the next Stop.
 * Checked after, whoever touched it either saw the lock free and ran itself,
 * or saw it held, in which case the touch precedes this check.
 *
 * A pass that used its whole byte budget goes again too. Capped at three passes
 * or thirty seconds; a flag or a backlog still standing then is left for the
 * next run to find.
 */

export type IndexResult = {
  files: number
  bytes: number
  ms: number
  passes: number
  locked: boolean
}

const STALE_MS = 60 * 60 * 1000
const MAX_PASSES = 3
const MAX_MS = 30_000

// EPERM is a live process some other user owns — alive, just not ours. The seam says so.
const alive = (pid: number): boolean => pidAlive(pid)

/** A lock with no pid in it is a holder between its create and its write —
 *  microseconds, normally. One still empty after this long was killed right
 *  there, and would otherwise block every run for the full hour. */
const EMPTY_STALE_MS = 10_000

export type LockState = "held" | "stale" | "gone"

/**
 * What a lock we failed to create is. `gone` is ONLY a missing file: its
 * holder released between our create and this look, and the next create
 * decides. Any other failure to read it is read as held — a lock we cannot
 * judge is not ours to break.
 */
export function lockState(lock: string): LockState {
  try {
    const age = Date.now() - statSync(lock).mtimeMs
    if (age > STALE_MS) return "stale"
    const pid = Number.parseInt(readFileSync(lock, "utf8"), 10)
    if (!(pid > 0)) return age > EMPTY_STALE_MS ? "stale" : "held"
    return alive(pid) ? "held" : "stale"
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === "ENOENT" ? "gone" : "held"
  }
}

/** `state` is injectable for ONE reason: the selftest makes the lock vanish
 *  between the failed create and the look, a window too narrow to hit by
 *  timing. */
export function acquire(
  lock: string,
  state: (lock: string) => LockState = lockState
): boolean {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const fd = openSync(lock, "wx")
      try {
        writeSync(fd, String(process.pid))
      } finally {
        closeSync(fd)
      }
      return true
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "EEXIST") throw err
      const now = state(lock)
      if (now === "held") return false
      // Two breakers can race here and both win. That costs one duplicate
      // pass, which the upserts absorb; it is why the lock is not load-bearing.
      if (now === "stale") rmSync(lock, { force: true })
      // "gone": nothing to break — create again.
    }
  }
  return false
}

/** Only our own lock: a breaker that replaced it has a run of its own going. */
export function release(lock: string): void {
  try {
    if (readFileSync(lock, "utf8") === String(process.pid)) rmSync(lock)
  } catch {
    /* already gone */
  }
}

function consume(flag: string): void {
  rmSync(flag, { force: true })
}

export async function runIndex(
  opts: {
    recent?: boolean
    budget?: number
    /** false skips the re-summarise pass — a read refreshing before it answers
     *  must not wait out a schema migration. */
    upkeep?: boolean
    /** The wall-clock cap on walking, 30 s by default. A read that refreshes
     *  first passes ~2 s: the walk stops between chunks, even mid-file. */
    maxMs?: number
  } = {}
): Promise<IndexResult> {
  const t0 = Date.now()
  const out: IndexResult = { files: 0, bytes: 0, ms: 0, passes: 0, locked: false }
  if (!store.open()) throw new Error(`the store at ${store.dir()} would not open`)
  const lock = join(store.dir(), "walk.lock")
  const again = join(store.dir(), "walk.again")
  const deadline = t0 + (opts.maxMs ?? MAX_MS)
  const budget = opts.budget ?? BACKFILL_BYTES

  // Ask first, then try. See the header for why this order loses no turn.
  writeFileSync(again, "")
  if (!acquire(lock)) {
    out.locked = true
    out.ms = Date.now() - t0
    return out
  }

  for (let round = 0; ; round++) {
    try {
      // A pass that spent its whole budget stopped with bytes still unread — a
      // backlog after days offline — so it goes again inside the same caps.
      let more = false
      do {
        consume(again)
        const r = await pass(budget, { recent: opts.recent, deadline })
        out.files += r.files
        out.bytes += r.bytes
        out.passes++
        more = r.bytes >= budget
      } while (
        out.passes < MAX_PASSES &&
        Date.now() < deadline &&
        (more || existsSync(again))
      )
      if (round === 0) {
        store.closeStale()
        if (opts.upkeep !== false) await store.resummarize()
      }
    } finally {
      release(lock)
    }
    // AFTER the release — the order the header argues for.
    if (!existsSync(again)) break
    if (out.passes >= MAX_PASSES || Date.now() >= deadline) break
    if (!acquire(lock)) break
  }

  out.ms = Date.now() - t0
  return out
}
