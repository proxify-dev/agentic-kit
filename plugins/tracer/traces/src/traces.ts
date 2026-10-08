import { existsSync, mkdirSync, rmSync } from "node:fs"
import { homedir, setPriority } from "node:os"
import { DatabaseSync } from "node:sqlite"
import { join } from "node:path"
import { HOME_CLAUDE, STORE_DIR } from "./paths.ts"
import {
  classifyOutput,
  readBlob,
  redact,
  stepLabel,
  summarize,
  sweepBlobs,
  writeBlob,
} from "./traces-blobs.ts"
// The witnesses read and write THIS module, so the import graph here is a
// cycle by design: this file is the store they share, and it is also the one
// door main.ts opens. It is safe because every cross-module use is inside a
// function body — nothing below runs at module-evaluation time — which is
// exactly the condition ESM circular imports require.
import * as witness from "./traces-witness.ts"
import type {
  TraceDetail,
  TraceFilter,
  TraceInput,
  TraceOutcome,
  TracePage,
  TraceRow,
  TraceStats,
  TraceWitness,
} from "./traces.types.ts"
import { env, envName, spawnDetached } from "./brand.ts"

/**
 * The span store — one row per `tool_use_id`, four witnesses, one file.
 *
 * `node:sqlite` because Electron 44 ships Node 24 and the episodic lobe already
 * proved it costs no native dependency and no per-request subprocess. The file
 * is PUBLIC on purpose: the app, the kit's stdio door and the one-shot
 * indexer (traces-index.ts, run by a Stop hook) write it, and `ak trace`
 * opens it read-only the way `ak replay` opens the transcripts.
 *
 * Three rules hold this together and none of them are negotiable:
 *
 *   **One row per id, upsert.** Whichever witness arrives first CREATES the
 *   row; every later one fills only what is still null and appends itself to
 *   `witnesses`. A hook's PreToolUse opens the row as `running` before the call
 *   returns; the transcript closes it seconds later with byte offsets for the
 *   payloads. Neither overwrites the other, so the order they arrive in cannot
 *   change what the row says. There are exactly two deliberate exceptions and
 *   both are documented on `record()`.
 *
 *   **The witness never throws into the door.** Every entry point here is
 *   wrapped: a recorder failure is a `console.error("[traces] …")` line and the
 *   tool call it observed is unaffected. Same posture as the feed — observe,
 *   never steer.
 *
 *   **Redaction before hashing and before summarising.** traces-blobs.ts owns
 *   the pass; this file only makes sure nothing reaches a column without it.
 *
 * Payloads are blobs by hash for the bench and byte offsets into the session
 * JSONL for everything else. The second spelling is the episodic indexer's own
 * trick and it is why a fresh install shows years of history without copying a
 * single megabyte.
 */

/** Where the trace store lives.
 *
 *  The env override exists so the selftest points at a temp dir — the same
 *  knob shape hooktrace uses, for the same reason.
 *
 *  The store lives in the kit's `db/` beside brain.db: a trace can't be
 *  rebuilt once its transcript is gone, so it belongs with what the kit keeps
 *  rather than in `claudeHome()`, where the harness keeps its own files and a
 *  reinstall is free to sweep. The move is triggered by the DIRECTORY, not by
 *  a flag — `<kit>/db/traces` wins the moment it exists — so the old location
 *  stays honoured until someone actually moves the files and no install
 *  silently loses its history. */
const KIT_TRACES = join(STORE_DIR, "traces")
const DIR =
  env("TRACES_DIR") ||
  (existsSync(KIT_TRACES) ? KIT_TRACES : join(HOME_CLAUDE, "brain-traces"))
const DEFAULT_LIMIT = 50

/**
 * Bump this when a stored COLUMN's meaning changes, and the rows re-read
 * themselves.
 *
 * The gate is PER ROW — `spans.sumv` — and not per file, and that is a lesson
 * rather than a preference. v2 was stamped on the file before its rows were
 * rebuilt, so the store reported itself current while half of it carried
 * summaries from v1, and there was no path out: the file said "done". A row
 * that carries its own version cannot be stranded that way, because the gate
 * and the work it guards are the same record.
 *
 * It also makes the pass resumable and cheap to schedule. 134,000 rows is
 * thirteen seconds of payload reads, which is not something to do inside
 * `open()` while a window is waiting; chunked in the background, a quit
 * halfway simply leaves the rest for next time.
 *
 * The migration is IN PLACE, never a rebuild, and the reason is the bench: it
 * is the one door with no transcript behind it, so dropping its rows destroys
 * history nothing can regenerate. Every row can reach its own payload — a
 * content-addressed blob for the bench, a byte offset for everything else — so
 * a column derived from a payload can simply be recomputed. A row whose
 * transcript has since been deleted keeps what it had, which is honest: the
 * summary was true when it was written.
 *
 * 2: `summary.output` became a shape-aware line (`refused · …`, `12 rows`)
 *    instead of a serialisation of the envelope, which read `{2}`. `outcome`
 *    learned that a kit refusal is not an error.
 * 3: redaction descends through serialisation, so a payload that had been
 *    flattened to `[redacted:124 chars]` summarises properly.
 * 4: a hook's `{stdout, stderr, …}` envelope reads as the text the command
 *    printed, so the hook and the transcript describe one call the same way.
 * 5: `summary.label`, the call as one line — its description, else `$ command`,
 *    a path (`stepLabel`). `summary.input` cut every Bash row off at
 *    `{command:cd <your repo>…`, before the line that said what it was for.
 */
const SCHEMA_VERSION = 5
/** Rows per migration chunk. One transaction each, then the loop yields. */
const MIGRATE_CHUNK = 400

const SCHEMA = `
  CREATE TABLE IF NOT EXISTS spans (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    parent     TEXT,
    ts         TEXT NOT NULL,
    durMs      INTEGER,
    server     TEXT NOT NULL,
    tool       TEXT NOT NULL,
    origin     TEXT NOT NULL,
    identity   TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    summary    TEXT NOT NULL,
    blobs      TEXT NOT NULL,
    shape      TEXT NOT NULL,
    witnesses  TEXT NOT NULL,
    policy     TEXT,
    ledger     TEXT,
    error      TEXT,
    session    TEXT,
    file       TEXT,
    sumv       INTEGER NOT NULL DEFAULT 0
  );
  CREATE INDEX IF NOT EXISTS spans_ts      ON spans(ts DESC);
  CREATE INDEX IF NOT EXISTS spans_server  ON spans(server);
  CREATE INDEX IF NOT EXISTS spans_tool    ON spans(tool);
  CREATE INDEX IF NOT EXISTS spans_session ON spans(session);
  CREATE INDEX IF NOT EXISTS spans_outcome ON spans(outcome);
  CREATE INDEX IF NOT EXISTS spans_file    ON spans(file);
  CREATE INDEX IF NOT EXISTS spans_sumv    ON spans(sumv);

  CREATE TABLE IF NOT EXISTS files (
    path       TEXT PRIMARY KEY,
    pos        INTEGER NOT NULL,
    mtime      INTEGER NOT NULL,
    size       INTEGER NOT NULL,
    parentUse  TEXT,
    agentName  TEXT
  );

  CREATE TABLE IF NOT EXISTS agent_calls (
    id          TEXT PRIMARY KEY,
    session     TEXT NOT NULL,
    ts          TEXT NOT NULL,
    description TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS agent_calls_session ON agent_calls(session, ts DESC);
`

let db: DatabaseSync | null = null
let degraded = false
let notify: ((row: TraceRow) => void) | null = null
/**
 * True while a transaction THIS module began is open, so a write inside a
 * batch joins it instead of beginning a second one (which SQLite refuses).
 * A flag rather than `conn.isTransaction`, which arrived in node 22.16 — the
 * floor is 22.13.
 */
let inTxn = false
/** Set by `raising()`: a read that fails throws instead of answering empty. */
let strict = false

/**
 * Open read-write, and degrade rather than fail.
 *
 * WAL plus a busy timeout, because this file genuinely has more than one
 * writer and pretending otherwise would be the bug. The app's main process is
 * the main one, but the kit's STDIO door runs inside each terminal session's
 * own process and shares `createTools` with the in-process server — which is
 * the whole reason one wrapper covers both doors. Denying it the write would
 * blank `policy` and `ledger` on exactly the door people use most, to protect
 * an invariant WAL already provides.
 *
 * What is still true: nobody outside this service writes here, and a reader
 * (`ak traces ls`) opens it read-only like the episodic lobe. A second app
 * instance is a real case too — the feed handles it by not binding the port —
 * and a database that will not open at all sets `degraded`, which turns every
 * write below into a no-op while every read keeps working.
 */
export function open(): DatabaseSync | null {
  if (db) return db
  if (degraded) return null
  let conn: DatabaseSync | null = null
  try {
    mkdirSync(DIR, { recursive: true })
    conn = new DatabaseSync(join(DIR, "traces.db"))
    // The timeout FIRST. Switching a fresh file to WAL takes an exclusive
    // lock, and with no timeout armed a second opener fails at once instead
    // of waiting: six one-shots started together on a new store left one
    // "running degraded: database is locked".
    conn.exec("PRAGMA busy_timeout = 3000")
    conn.exec("PRAGMA journal_mode = WAL")
    // A table written before `sumv` existed needs the column BEFORE the schema
    // runs, because the schema declares an index on it. In the other order this
    // throws `no such column: sumv` and every store already on disk opens
    // degraded — caught on a copy of a real 134,000-row store, never on a fresh
    // one, which is why a migration is only proven against a store it migrates.
    // Every row in such a table predates this build by definition, so a default
    // of 0 is exactly right and the background pass picks them up.
    const older = conn
      .prepare(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'spans'"
      )
      .get() as Raw | undefined
    if (older) {
      const columns = conn.prepare("PRAGMA table_info(spans)").all() as Raw[]
      if (!columns.some((c) => c.name === "sumv"))
        conn.exec(
          "ALTER TABLE spans ADD COLUMN sumv INTEGER NOT NULL DEFAULT 0"
        )
    }
    conn.exec(SCHEMA)
    // `user_version` is now a NOTE, not a gate — `sumv` decides what re-reads.
    // It is stamped here so a reader can still tell which build wrote the file.
    // Only when it DIFFERS: the pragma is a write even at the same value, and
    // a write moves every other connection's `data_version` — so a one-shot
    // indexer that opened and found nothing would still wake the app's watch.
    const stamped = conn.prepare("PRAGMA user_version").get() as Raw | undefined
    if (Number(stamped?.user_version) !== SCHEMA_VERSION)
      conn.exec(`PRAGMA user_version = ${SCHEMA_VERSION}`)
    db = conn
    return db
  } catch (err) {
    try {
      conn?.close()
    } catch {
      /* it never finished opening */
    }
    // A lock outlasting the timeout is a busy moment, not a broken file: the
    // next call opens again. Only anything else degrades, and `degraded`
    // sticks for the life of the process.
    if (isLocked(err)) {
      console.error(`[traces] store busy, not opened: ${(err as Error).message}`)
      return null
    }
    degraded = true
    console.error(
      `[traces] store unavailable, running degraded: ${(err as Error).message}`
    )
    return null
  }
}

/**
 * Open for reading and nothing else — the door `ak trace` reads through.
 *
 * A missing file answers null, and that is the whole point: a read must never
 * leave an empty database behind, because an empty store and "never indexed"
 * would then look the same to every later reader. Only `open()` creates, and
 * only a writer calls it. A file with no `spans` table is "never indexed" too.
 *
 * Whatever is open already wins, so the app's read-write handle serves reads
 * as it always has.
 */
export function openReadOnly(): DatabaseSync | null {
  if (db) return db
  if (!existsSync(dbFile())) return null
  try {
    const conn = new DatabaseSync(dbFile(), { readOnly: true })
    conn.exec("PRAGMA busy_timeout = 3000")
    const spans = conn
      .prepare(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'spans'"
      )
      .get()
    if (!spans) {
      conn.close()
      return null
    }
    db = conn
    return db
  } catch (err) {
    console.error(`[traces] store unreadable: ${(err as Error).message}`)
    return null
  }
}

export const isDegraded = (): boolean => degraded
export const dir = (): string => DIR
export const dbFile = (): string => join(DIR, "traces.db")

/**
 * Tell a caller when ANOTHER process committed to the store.
 *
 * `PRAGMA data_version` moves on this connection only when a different
 * connection commits, which is exactly the edge the app cannot see otherwise:
 * the one-shot indexer and each terminal's stdio door write from their own
 * processes, and their rows reach no `onRow`. This process's own writes push
 * through `onRow` and never show here. A poll of one pragma every two seconds
 * costs nothing measurable.
 *
 * A reopened connection starts its own count, so the first reading after one
 * is a baseline rather than a change.
 */
const watchers = new Set<ReturnType<typeof setInterval>>()

export function watch(fn: () => void, ms = 2000): () => void {
  let seen: { conn: DatabaseSync; version: number } | null = null
  const tick = (): void => {
    try {
      const conn = open()
      if (!conn) return
      const r = conn.prepare("PRAGMA data_version").get() as Raw | undefined
      const version = Number(r?.data_version)
      const moved = seen?.conn === conn && seen.version !== version
      seen = { conn, version }
      if (moved) fn()
    } catch (err) {
      console.error(`[traces] watch: ${(err as Error).message}`)
    }
  }
  tick()
  const timer = setInterval(tick, ms)
  timer.unref?.()
  watchers.add(timer)
  return () => {
    clearInterval(timer)
    watchers.delete(timer)
  }
}

export function close(): void {
  try {
    db?.close()
  } catch {
    /* closing a closed handle is not a failure worth a line */
  }
  db = null
  degraded = false
}

/** main.ts hands one callback; every upsert is pushed to every window. */
export function onRow(fn: ((row: TraceRow) => void) | null): void {
  notify = fn
}

let daily: ReturnType<typeof setInterval> | null = null

/**
 * The whole service, from main.ts: open the file, arm the live witnesses,
 * start the one-shot indexer once, sweep the blobs.
 *
 * The transcript walk is NOT in this process any more. It lived here on a
 * ten-second timer, which meant the store filled only while a window was open;
 * it is now `traces-index.ts`, a short-lived process a Stop hook launches after
 * every turn. The app launches the same one-shot once at start so a person who
 * only ever uses the app still catches up, and the backfill is off the main
 * thread. Rows an older build wrote are re-read there too (`resummarize`).
 *
 * What stays is what only this process can do: the live witnesses (the feed's
 * hooks, the runner's stream, the bench), the per-session reconcile they
 * schedule, and the blob retention — only the bench writes blobs.
 */
export function start(
  opts: { onRow?: (row: TraceRow) => void; indexer?: string | null } = {}
): void {
  try {
    open()
    if (opts.onRow) onRow(opts.onRow)
    sweep()
    witness.start()
    if (opts.indexer) launchIndexer(opts.indexer)
    // Retention is a daily job with an immediate first run above; blobs age out
    // at 30 days or 200 MB, and rows never do.
    daily = setInterval(() => void sweep(), 24 * 60 * 60 * 1000)
  } catch (err) {
    console.error(`[traces] start failed: ${(err as Error).message}`)
  }
}

/**
 * Run the one-shot in the background and forget it.
 *
 * `process.execPath` is Electron in the app, and `ELECTRON_RUN_AS_NODE` makes
 * it node for the child — the way the terminal door and the gateway run. The
 * store dir is passed down rather than re-derived, so the child indexes the
 * file this process reads even when the two would resolve the kit home differently.
 * Detached, unref'd, niced: the window never waits on it, and the lock in
 * traces-index.ts makes it a no-op when a Stop hook's run is already going.
 */
function launchIndexer(entry: string): void {
  if (!existsSync(entry)) {
    console.error(`[traces] no indexer at ${entry}; the store fills from the Stop hook alone`)
    return
  }
  try {
    const flags = entry.endsWith(".ts")
      ? ["--experimental-strip-types", "--no-warnings"]
      : ["--no-warnings"]
    // detached + unref'd + no console window on Windows: the seam's spawnDetached
    const child = spawnDetached(process.execPath, [...flags, entry, "index"], {
      env: { ...process.env, ELECTRON_RUN_AS_NODE: "1", [envName("TRACES_DIR")]: DIR },
      cwd: homedir(),
    })
    child.on("error", (err) =>
      console.error(`[traces] indexer failed to start: ${err.message}`)
    )
    try {
      if (child.pid) setPriority(child.pid, 10)
    } catch {
      /* a normal-priority catch-up is still a catch-up */
    }
  } catch (err) {
    console.error(`[traces] indexer failed to start: ${(err as Error).message}`)
  }
}

export function stop(): void {
  if (daily) clearInterval(daily)
  daily = null
  for (const t of watchers) clearInterval(t)
  watchers.clear()
  try {
    witness.stop()
  } catch (err) {
    console.error(`[traces] stop failed: ${(err as Error).message}`)
  }
  onRow(null)
  close()
}

/* ── rows ────────────────────────────────────────────────────────────────── */

type Raw = Record<string, unknown>

const parse = <T>(s: unknown, fallback: T): T => {
  try {
    return JSON.parse(String(s)) as T
  } catch {
    return fallback
  }
}

const EMPTY_IDENTITY = {
  session: null,
  prompt: null,
  cwd: null,
  agent: null,
  run: null,
  item: null,
}

function hydrate(r: Raw): TraceRow {
  const row: TraceRow = {
    id: String(r.id),
    kind: "tool",
    parent: (r.parent as string) ?? null,
    ts: String(r.ts),
    durMs: r.durMs === null || r.durMs === undefined ? null : Number(r.durMs),
    server: String(r.server),
    tool: String(r.tool),
    origin: String(r.origin) as TraceRow["origin"],
    identity: parse(r.identity, { ...EMPTY_IDENTITY }),
    outcome: String(r.outcome) as TraceOutcome,
    summary: parse(r.summary, { input: "", output: "" }),
    blobs: parse(r.blobs, { input: null, output: null }),
    shape: parse(r.shape, { bytesIn: 0, bytesOut: 0 }),
    witnesses: parse(r.witnesses, [] as TraceWitness[]),
  }
  if (r.policy) row.policy = String(r.policy)
  if (r.ledger) row.ledger = String(r.ledger)
  if (r.error) row.error = String(r.error)
  return row
}

/** Fill a hole, never overwrite a value. The upsert rule in one helper. */
const fill = <T>(
  current: T | null | undefined,
  next: T | null | undefined
): T | null =>
  current === null || current === undefined || current === ""
    ? (next ?? null)
    : current

/**
 * Re-read the columns this app DERIVES, for rows an older build wrote.
 *
 * Only `summary` and `outcome` move, because those are the two computed here
 * rather than read off a wire. Everything else on a row — the id, the clock,
 * the identity, the offsets — came from a transcript and is as true as the day
 * it landed. A row whose payload no longer resolves is left exactly as it is,
 * which is why this cannot make a store worse: the failure mode is
 * "unchanged", never "blank".
 *
 * One transaction per chunk, and `sumv` is stamped inside it, so the unit of
 * progress and the unit of durability are the same thing. Quit halfway and the
 * rows that did not get there still read `sumv < SCHEMA_VERSION`; the next
 * start picks them up and nothing has to remember where it was.
 *
 * **The lock is never held across file I/O**, and that is the whole shape of
 * this function. Four hundred payload reads inside `BEGIN` is four hundred
 * disk seeks holding the write lock, and a second instance trying to record a
 * bench call in that window waits past `busy_timeout` and loses the row. So
 * the reads happen first, with no transaction open, and the transaction only
 * wraps the UPDATE loop — microseconds of lock instead of seconds. Measured
 * before the split: `re-read stopped after 58000 rows: database is locked`,
 * and a bench row that never landed.
 */
type Rewrite = { id: string; summary: string; outcome: TraceOutcome }

/** Phase one: read every payload for the chunk, holding NO lock. */
function planChunk(
  rows: Raw[],
  read: typeof readBlob = readBlob
): Rewrite[] {
  const out: Rewrite[] = []
  for (const r of rows) {
    const id = String(r.id)
    const refs = parse(r.blobs, {
      input: null,
      output: null,
    } as TraceRow["blobs"])
    const was = parse<TraceRow["summary"]>(r.summary, { input: "", output: "" })
    const next = { ...was }
    let outcome = String(r.outcome) as TraceOutcome

    const inPayload = read(DIR, refs.input, id, "input")
    if (inPayload !== null) {
      const safe = redact(inPayload)
      next.input = summarize(safe)
      next.label = stepLabel(String(r.tool), safe)
    }
    const outPayload = read(DIR, refs.output, id, "output")
    if (outPayload !== null) {
      const line = classifyOutput(outPayload)
      next.output = line.line
      if (line.refused && (outcome === "error" || outcome === "ok"))
        outcome = "refused"
    }
    out.push({ id, summary: JSON.stringify(next), outcome })
  }
  return out
}

/** `read` is injectable for ONE reason: the selftest proves, from inside a
 *  payload read, that no transaction is open around it. A seam is cheaper than
 *  a comment promising the same thing. */
export function migrateChunk(
  conn: DatabaseSync,
  read: typeof readBlob = readBlob
): number {
  const all = conn
    .prepare(
      `SELECT id, tool, blobs, summary, outcome FROM spans WHERE sumv < ? LIMIT ${MIGRATE_CHUNK}`
    )
    .all(SCHEMA_VERSION) as Raw[]
  if (!all.length) return 0

  // Phase one, outside any transaction: all the slow work.
  const planned = planChunk(all, read)

  // Phase two: the writes, and nothing else.
  const update = conn.prepare(
    "UPDATE spans SET summary = ?, outcome = ?, sumv = ? WHERE id = ?"
  )
  conn.exec("BEGIN IMMEDIATE")
  try {
    for (const p of planned)
      update.run(p.summary, p.outcome, SCHEMA_VERSION, p.id)
    conn.exec("COMMIT")
  } catch (err) {
    conn.exec("ROLLBACK")
    throw err
  }
  return all.length
}

let migrating = false

/**
 * Drain the backlog in the background, yielding between chunks.
 *
 * Never inside `open()`: 134,000 rows is about thirteen seconds of payload
 * reads, and a window waiting on that is a window that looks broken. The
 * walker's backfill makes the same bargain next door for the same reason.
 */
export async function resummarize(): Promise<{ rows: number; ms: number }> {
  const t0 = Date.now()
  let rows = 0
  if (migrating) return { rows, ms: 0 }
  migrating = true
  try {
    for (;;) {
      const conn = open()
      if (!conn || degraded) break
      const n = migrateChunk(conn)
      if (!n) break
      rows += n
      await new Promise<void>((r) => setImmediate(r))
    }
    if (rows)
      console.error(
        `[traces] re-read ${rows} rows to v${SCHEMA_VERSION} in ${Date.now() - t0}ms`
      )
  } catch (err) {
    // A failed pass leaves those rows at their old `sumv` and their old
    // summaries, which is survivable; the next start tries again.
    console.error(
      `[traces] re-read stopped after ${rows} rows: ${(err as Error).message}`
    )
  } finally {
    migrating = false
  }
  return { rows, ms: Date.now() - t0 }
}

const UPSERT = `INSERT INTO spans (id, kind, parent, ts, durMs, server, tool, origin, identity, outcome,
                     summary, blobs, shape, witnesses, policy, ledger, error, session, file, sumv)
  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  ON CONFLICT(id) DO UPDATE SET
    parent=excluded.parent, durMs=excluded.durMs, origin=excluded.origin,
    identity=excluded.identity, outcome=excluded.outcome, summary=excluded.summary,
    blobs=excluded.blobs, shape=excluded.shape, witnesses=excluded.witnesses,
    policy=excluded.policy, ledger=excluded.ledger, error=excluded.error,
    session=excluded.session, file=COALESCE(spans.file, excluded.file), sumv=excluded.sumv`

/**
 * The upsert. First witness creates; every later one fills holes.
 *
 * Two fields a later witness MAY overwrite, and only in one direction each:
 *
 *   `outcome`, but only away from `running` — `running` is the absence of an
 *   answer rather than an answer, which is exactly what lets a hook open a row
 *   before the call returns without the transcript having to fight it later.
 *
 *   `origin`, but only away from `session` — the transcript cannot tell an
 *   app-run agent from a terminal session (both land in <claudeHome>/projects),
 *   while the sdk witness that also saw the call knows which run it was. The
 *   more specific answer wins; nothing else about the row moves.
 */
/**
 * SQLITE_BUSY and SQLITE_LOCKED, recognised by message.
 *
 * `node:sqlite` surfaces no error CODE, so the message is what there is. Both
 * words are matched because the two conditions mean the same thing to a caller
 * here: another writer holds the file and this one should wait, not give up.
 */
const isLocked = (err: unknown): boolean =>
  /locked|busy/i.test((err as Error)?.message ?? "")

const LOCK_RETRIES = 3
const LOCK_WAIT_MS = 200

/**
 * Wait, synchronously, and deliberately.
 *
 * `DatabaseSync` is synchronous by design — that is what makes a witness a
 * function call rather than a promise the door has to await — so a synchronous
 * pause is the honest one here. `Atomics.wait` on a throwaway SharedArrayBuffer
 * is a real sleep rather than a spin, so it costs no CPU while it waits.
 */
function pause(ms: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms)
}

/**
 * Run a database call, and wait out a lock rather than losing to it.
 *
 * This exists because two processes genuinely write here — the app, and the
 * kit's stdio door inside each terminal session. Measured at integration:
 * with one instance mid-migration, a second instance's bench row was simply
 * DROPPED, and a read returned an empty page while the pane was showing it as
 * "no history". Neither is a failure a person should ever be shown for a
 * condition that clears in milliseconds.
 *
 * Three attempts, 200ms apart — long enough to outlast a chunk's write phase,
 * short enough that a genuinely stuck file still reports quickly. The log line
 * is written ONCE, at the end, so a busy minute is not a thousand lines.
 */
function withRetry<T>(what: string, fn: () => T, fallback: T): T {
  for (let attempt = 0; ; attempt++) {
    try {
      return fn()
    } catch (err) {
      if (attempt < LOCK_RETRIES && isLocked(err)) {
        pause(LOCK_WAIT_MS)
        continue
      }
      if (strict) throw err
      console.error(`[traces] ${what}: ${(err as Error).message}`)
      return fallback
    }
  }
}

/**
 * Run a read so that a failure THROWS instead of answering empty.
 *
 * The app wants the empty answer — a fold that shows "no history" beats one
 * that shows a stack. A CLI wants the opposite: an empty page with exit 0 is a
 * lie a script will believe, so `brain-traces` wraps its reads in this and
 * turns the throw into one stderr line and a non-zero exit. Scoped to the call
 * rather than to the process, so the walker's own writes keep their log-and-go.
 */
export function raising<T>(fn: () => T): T {
  const was = strict
  strict = true
  try {
    return fn()
  } finally {
    strict = was
  }
}

/**
 * One write transaction around `fn`, or the one already open.
 *
 * `BEGIN IMMEDIATE` takes the write lock BEFORE the read, and that is the
 * point: `record()` reads the row, merges, and upserts the whole of it, so two
 * processes that both read before either wrote would each write back a row
 * missing the other's witness — the app's hook row losing the `blobs` the
 * one-shot walker had just filled. Taking the lock first makes read-merge-write
 * one step. Busy is waited out by `busy_timeout`, then by `withRetry` around
 * the caller.
 *
 * Nested calls join the outer transaction, which is how the walker batches a
 * whole chunk of a transcript into one commit: one fsync per 256 KB rather than
 * one per span, and never a lock held across the file read that produced it.
 */
export function batch<T>(fn: () => T): T {
  const conn = open()
  if (!conn || inTxn) return fn()
  conn.exec("BEGIN IMMEDIATE")
  inTxn = true
  try {
    const out = fn()
    conn.exec("COMMIT")
    return out
  } catch (err) {
    try {
      conn.exec("ROLLBACK")
    } catch {
      /* SQLite already rolled back on its own; nothing is open */
    }
    throw err
  } finally {
    inTxn = false
  }
}

export function record(input: TraceInput): TraceRow | null {
  // A stored payload is a content-addressed FILE, so it is written before the
  // transaction: the write lock never waits on a disk write. Writing the same
  // bytes twice is the same file, and a side the row already had keeps its
  // own ref (the orphan ages out with the sweep).
  const stored: Blobs = { input: null, output: null }
  if (input.store)
    for (const side of ["input", "output"] as const)
      if (input[side] !== undefined)
        stored[side] = writeBlob(DIR, redact(input[side]))
  // The upsert is idempotent, so a retried attempt re-reads and re-writes the
  // same row; `notify` is deliberately outside, so a window is told once.
  const row = withRetry(
    `record failed for ${input.id}`,
    () => batch(() => writeSpan(input, stored)),
    null
  )
  if (row) notify?.(row)
  return row
}

type Blobs = TraceRow["blobs"]

function writeSpan(input: TraceInput, stored: Blobs): TraceRow | null {
  {
    const conn = open()
    if (!conn || degraded) return null

    const existing = conn
      .prepare("SELECT * FROM spans WHERE id = ?")
      .get(input.id) as Raw | undefined
    const prev = existing ? hydrate(existing) : null

    // A closing witness with nothing to close. Dropping it is correct: a row
    // with no tool name is a row no filter could ever find.
    if (!prev && !(input.server && input.tool)) return null

    const ts = prev?.ts ?? input.ts ?? new Date().toISOString()

    let durMs = prev?.durMs ?? null
    if (durMs === null && input.durMs !== undefined && input.durMs !== null)
      durMs = input.durMs
    if (durMs === null && input.endTs)
      durMs = Math.max(0, Date.parse(input.endTs) - Date.parse(ts))

    const identity = { ...EMPTY_IDENTITY, ...(prev?.identity ?? {}) }
    for (const [k, v] of Object.entries(input.identity ?? {})) {
      const key = k as keyof typeof identity
      identity[key] = fill(identity[key], v as string | null)
    }

    const summary = { ...(prev?.summary ?? { input: "", output: "" }) }
    const blobs = { ...(prev?.blobs ?? { input: null, output: null }) }
    const shape = { ...(prev?.shape ?? { bytesIn: 0, bytesOut: 0 }) }
    // Set when THIS payload is a kit refusal envelope. See the outcome rule.
    let refused = false
    for (const side of ["input", "output"] as const) {
      const payload = input[side]
      if (payload !== undefined) {
        const key = side === "input" ? "bytesIn" : "bytesOut"
        // The ORIGINAL bytes, not the redacted ones — the honest size, and it
        // leaks nothing, which is the whole reason `shape` is a column.
        const given = side === "input" ? input.bytes?.in : input.bytes?.out
        if (!shape[key])
          shape[key] =
            given ?? Buffer.byteLength(JSON.stringify(payload ?? null))
        if (side === "output") {
          // An output is an ENVELOPE, not an argument list: read its shape and
          // say what happened. traces-blobs.ts owns the grammar.
          // The RAW payload: classifyOutput redacts at the leaf, after it has
          // read the envelope's shape. Redacting here first would hand it one
          // long opaque string. See traces-blobs.ts.
          const read = classifyOutput(payload)
          refused = read.refused
          if (!summary.output) summary.output = read.line
        } else if (!summary.input) {
          const safe = redact(payload)
          summary.input = summarize(safe)
          summary.label = stepLabel(prev?.tool ?? input.tool ?? "", safe)
        }
        if (!blobs[side] && stored[side]) blobs[side] = stored[side]
      }
      const ref = input.blobs?.[side]
      if (ref && !blobs[side]) blobs[side] = ref
    }

    const witnesses = [...(prev?.witnesses ?? [])]
    if (!witnesses.includes(input.witness)) witnesses.push(input.witness)

    let outcome: TraceOutcome = prev?.outcome ?? input.outcome ?? "running"
    /**
     * `closeStale` GUESSED this row: running for ten minutes, so the process
     * died. A long Bash or a foreground Agent in another session outlives
     * that and then reports — and a real close is evidence, which beats a
     * guess. So `error: unreported` yields to an arriving outcome exactly as
     * `running` does, and takes its error message with it.
     */
    const overturned =
      prev?.outcome === "error" &&
      prev.error === UNREPORTED &&
      input.outcome !== undefined &&
      input.outcome !== "running"
    if (input.outcome && (!prev || prev.outcome === "running" || overturned))
      outcome = input.outcome
    /**
     * A kit refusal is not an error and every witness reports it as one.
     *
     * The ladder looked at a write and said no, for a reason the model can act
     * on — but it travels the wire as `isError`, so the transcript, the SDK
     * stream and the bench all close the row as `error`. Only the PAYLOAD can
     * tell them apart, and it is unambiguous: `{kind:"refused", refusal:{…}}`.
     * Upgrading here rather than in each witness means one rule, and it is
     * what keeps the fold's outcome filter worth having — a refusal is the
     * single most interesting thing the kit server ever answers.
     */
    if (refused && (outcome === "error" || outcome === "ok"))
      outcome = "refused"

    let origin = prev?.origin ?? input.origin ?? "session"
    if (input.origin && input.origin !== "session" && origin === "session")
      origin = input.origin

    const row: TraceRow = {
      id: input.id,
      kind: "tool",
      parent: fill(prev?.parent, input.parent),
      ts,
      durMs,
      server: prev?.server ?? input.server!,
      tool: prev?.tool ?? input.tool!,
      origin,
      identity,
      outcome,
      summary,
      blobs,
      shape,
      witnesses,
    }
    const policy = fill(prev?.policy, input.policy)
    const ledger = fill(prev?.ledger, input.ledger)
    const error = overturned
      ? (input.error ?? null)
      : fill(prev?.error, input.error)
    if (policy) row.policy = policy
    if (ledger) row.ledger = ledger
    if (error) row.error = error

    conn
      .prepare(UPSERT)
      .run(
        row.id,
        row.kind,
        row.parent,
        row.ts,
        row.durMs,
        row.server,
        row.tool,
        row.origin,
        JSON.stringify(row.identity),
        row.outcome,
        JSON.stringify(row.summary),
        JSON.stringify(row.blobs),
        JSON.stringify(row.shape),
        JSON.stringify(row.witnesses),
        row.policy ?? null,
        row.ledger ?? null,
        row.error ?? null,
        row.identity.session,
        input.file ?? null,
        SCHEMA_VERSION
      )
    return row
  }
}

/** The inproc witness's whole surface: enrich a row that already exists, never
 *  create one. It has no tool_use_id to create with — that is the point. */
export function enrich(
  id: string,
  patch: { policy?: string; ledger?: string }
): void {
  withRetry(
    `enrich failed for ${id}`,
    () => {
      const conn = open()
      if (!conn || degraded) return
      conn
        .prepare(
          "UPDATE spans SET policy = COALESCE(policy, ?), ledger = COALESCE(ledger, ?) WHERE id = ?"
        )
        .run(patch.policy ?? null, patch.ledger ?? null, id)
    },
    undefined
  )
}

/** The newest call this session made with this tool inside the window — how
 *  the inproc witness finds the row another witness created for its own call. */
export function findRecent(args: {
  session: string
  tool: string
  windowMs: number
}): string | null {
  try {
    const conn = open()
    if (!conn) return null
    const since = new Date(Date.now() - args.windowMs).toISOString()
    const r = conn
      .prepare(
        "SELECT id FROM spans WHERE session = ? AND tool = ? AND ts >= ? ORDER BY ts DESC LIMIT 1"
      )
      .get(args.session, args.tool, since) as Raw | undefined
    return r ? String(r.id) : null
  } catch {
    return null
  }
}

/* ── reading ─────────────────────────────────────────────────────────────── */

/**
 * Reads retry a lock too, and the reason is not symmetry.
 *
 * An empty page is not an error shape here — it is the shape of "no history",
 * and the fold draws it as exactly that. So a read that gives up on a lock
 * tells a person something false about their own machine, for a condition that
 * clears in milliseconds. Waiting is the only honest answer.
 */
export function list(filter: TraceFilter = {}): TracePage {
  return withRetry("list failed", () => readPage(filter), { rows: [], next: null })
}

function readPage(filter: TraceFilter): TracePage {
  {
    const conn = open()
    if (!conn) return { rows: [], next: null }
    const where: string[] = []
    const args: string[] = []
    const eq = (col: string, v?: string) => {
      if (v) {
        where.push(`${col} = ?`)
        args.push(v)
      }
    }
    eq("server", filter.server)
    eq("tool", filter.tool)
    eq("session", filter.session)
    eq("origin", filter.origin)
    eq("outcome", filter.outcome)
    if (filter.since) {
      where.push("ts >= ?")
      args.push(filter.since)
    }
    if (filter.until) {
      where.push("ts < ?")
      args.push(filter.until)
    }
    if (filter.q) {
      where.push("(tool LIKE ? OR summary LIKE ?)")
      args.push(`%${filter.q}%`, `%${filter.q}%`)
    }
    // The cursor is (ts, id) rather than an offset, so a row landing while
    // someone pages cannot make the next page repeat or skip one.
    if (filter.cursor) {
      const cut = filter.cursor.lastIndexOf("|")
      const cts = filter.cursor.slice(0, cut)
      where.push("(ts < ? OR (ts = ? AND id < ?))")
      args.push(cts, cts, filter.cursor.slice(cut + 1))
    }
    const limit = Math.max(1, Math.min(500, filter.limit ?? DEFAULT_LIMIT))
    const found = conn
      .prepare(
        `SELECT * FROM spans${where.length ? ` WHERE ${where.join(" AND ")}` : ""} ORDER BY ts DESC, id DESC LIMIT ${limit + 1}`
      )
      .all(...args) as Raw[]
    const rows = found.slice(0, limit).map(hydrate)
    const last = rows.at(-1)
    return {
      rows,
      next: found.length > limit && last ? `${last.ts}|${last.id}` : null,
    }
  }
}

export function get(id: string): TraceDetail | null {
  // The row is fetched under retry; the PAYLOAD reads are file I/O and are
  // deliberately outside it, so a locked database never waits on a disk seek.
  const row = withRetry(
    `get failed for ${id}`,
    () => {
      const conn = open()
      if (!conn) return null
      const r = conn.prepare("SELECT * FROM spans WHERE id = ?").get(id) as
        | Raw
        | undefined
      return r ? hydrate(r) : null
    },
    null
  )
  if (!row) return null
  return {
    row,
    input: readBlob(DIR, row.blobs.input, id, "input"),
    output: readBlob(DIR, row.blobs.output, id, "output"),
  }
}

/**
 * The sessions whose id starts with `prefix`, newest first — how `ak trace
 * --session 8f3a1c2e` finds the one uuid a person means from the eight
 * characters they copied off a status line.
 *
 * A RANGE on the indexed column rather than `LIKE 'p%'`, which SQLite will not
 * serve from an index under its default case-insensitive LIKE: `>= p AND
 * < p || U+FFFF` is the same set for an ASCII uuid, and an index seek.
 */
export function sessionsLike(prefix: string, limit = 20): string[] {
  return withRetry(
    "session lookup failed",
    () => {
      const conn = open()
      if (!conn) return []
      return (
        conn
          .prepare(
            `SELECT session, MAX(ts) at FROM spans WHERE session >= ? AND session < ?
             GROUP BY session ORDER BY at DESC LIMIT ?`
          )
          .all(prefix, `${prefix}￿`, limit) as Raw[]
      ).map((r) => String(r.session))
    },
    [] as string[]
  )
}

export function stats(server?: string): TraceStats {
  const empty: TraceStats = {
    total: 0,
    today: 0,
    errors: 0,
    running: 0,
    lastAt: null,
    tools: {},
  }
  return withRetry("stats failed", () => readStats(server), empty)
}

function readStats(server?: string): TraceStats {
  {
    const conn = open()
    const empty: TraceStats = {
      total: 0,
      today: 0,
      errors: 0,
      running: 0,
      lastAt: null,
      tools: {},
    }
    if (!conn) return empty
    const w = server ? " WHERE server = ?" : ""
    const and = server ? `${w} AND` : " WHERE"
    const a: string[] = server ? [server] : []
    const midnight = new Date()
    midnight.setHours(0, 0, 0, 0)
    const one = (sql: string, extra: string[] = []) =>
      Number((conn.prepare(sql).get(...a, ...extra) as Raw | undefined)?.n ?? 0)
    const tools: Record<string, number> = {}
    for (const r of conn
      .prepare(
        `SELECT tool, COUNT(*) n FROM spans${w} GROUP BY tool ORDER BY n DESC LIMIT 200`
      )
      .all(...a) as Raw[])
      tools[String(r.tool)] = Number(r.n)
    const last = conn.prepare(`SELECT MAX(ts) ts FROM spans${w}`).get(...a) as
      Raw | undefined
    return {
      total: one(`SELECT COUNT(*) n FROM spans${w}`),
      today: one(`SELECT COUNT(*) n FROM spans${and} ts >= ?`, [
        midnight.toISOString(),
      ]),
      errors: one(
        `SELECT COUNT(*) n FROM spans${and} outcome IN ('error','denied','refused')`
      ),
      running: one(`SELECT COUNT(*) n FROM spans${and} outcome = 'running'`),
      lastAt: last?.ts ? String(last.ts) : null,
      tools,
    }
  }
}

/**
 * Remove rows AND the blobs only they referenced.
 *
 * A blob is content-addressed, so two rows can share one file; the reference
 * count is checked before anything is unlinked. Transcript offsets are never
 * touched — those files belong to Claude Code.
 *
 * `dryRun` counts what WOULD go and writes nothing, so a person is shown the
 * number before they confirm; `removed` means "would be removed" there.
 */
export function purge(args: {
  server?: string
  before: string
  dryRun?: boolean
}): {
  removed: number
} {
  try {
    const conn = open()
    if (!conn || degraded) return { removed: 0 }
    const where = args.server ? "ts < ? AND server = ?" : "ts < ?"
    const params = args.server ? [args.before, args.server] : [args.before]
    if (args.dryRun)
      return {
        removed: Number(
          (
            conn
              .prepare(`SELECT COUNT(*) n FROM spans WHERE ${where}`)
              .get(...params) as Raw | undefined
          )?.n ?? 0
        ),
      }
    const hashes = new Set<string>()
    for (const d of conn
      .prepare(`SELECT blobs FROM spans WHERE ${where}`)
      .all(...params) as Raw[]) {
      const b = parse(d.blobs, {
        input: null,
        output: null,
      } as TraceRow["blobs"])
      for (const ref of [b.input, b.output])
        if (ref?.startsWith("sha256:")) hashes.add(ref.slice(7))
    }
    const removed = Number(
      conn.prepare(`DELETE FROM spans WHERE ${where}`).run(...params).changes
    )
    for (const hash of hashes) {
      const still = conn
        .prepare("SELECT COUNT(*) n FROM spans WHERE blobs LIKE ?")
        .get(`%${hash}%`) as Raw | undefined
      if (Number(still?.n ?? 0) === 0)
        rmSync(join(DIR, "blobs", hash.slice(0, 2), `${hash}.json`), {
          force: true,
        })
    }
    return { removed }
  } catch (err) {
    if (strict) throw err
    console.error(`[traces] purge failed: ${(err as Error).message}`)
    return { removed: 0 }
  }
}

/** Retention, run on start and daily. traces-blobs.ts decides what goes; this
 *  is only the callback that keeps the row's reference honest. */
export const sweep = (): { removed: number; bytes: number } =>
  sweepBlobs(DIR, (hash) => {
    try {
      const conn = open()
      if (!conn || degraded) return
      conn
        .prepare(
          "UPDATE spans SET blobs = replace(blobs, ?, 'null') WHERE blobs LIKE ?"
        )
        .run(`"sha256:${hash}"`, `%${hash}%`)
    } catch {
      /* a dangling ref reads as a missing payload, which is survivable */
    }
  })

/* ── walker bookkeeping (traces-walk.ts owns the reading) ────────────────── */

export type FileMark = {
  path: string
  pos: number
  mtime: number
  size: number
  parentUse: string | null
  agentName: string | null
}

const toMark = (r: Raw): FileMark => ({
  path: String(r.path),
  pos: Number(r.pos),
  mtime: Number(r.mtime),
  size: Number(r.size),
  parentUse: (r.parentUse as string) ?? null,
  agentName: (r.agentName as string) ?? null,
})

export function fileMark(path: string): FileMark | null {
  try {
    const conn = open()
    if (!conn) return null
    const r = conn.prepare("SELECT * FROM files WHERE path = ?").get(path) as
      Raw | undefined
    return r ? toMark(r) : null
  } catch {
    return null
  }
}

/** Every mark in ONE select — what a pass compares ten thousand stats against
 *  before it opens a single file. A select per file was most of a no-change
 *  pass. */
export function fileMarks(): Map<string, FileMark> {
  const out = new Map<string, FileMark>()
  try {
    const conn = open()
    if (!conn) return out
    for (const r of conn.prepare("SELECT * FROM files").all() as Raw[])
      out.set(String(r.path), toMark(r))
  } catch {
    /* no marks is a full walk, which is slower and still correct */
  }
  return out
}

/**
 * Advance a file's mark — and never move it BACKWARDS.
 *
 * Two walkers can read the same transcript at once now: the app reconciling a
 * live session, and a Stop hook's one-shot. The one that stat'd earlier read
 * less, and if it saved last it would drag the mark back and the next pass
 * would re-read bytes both had indexed. Harmless (the upsert is idempotent)
 * but wasted, so a mark only moves forward. The one exception is a file that
 * genuinely SHRANK — a newer observation of a smaller file, which is a
 * rewrite, and the old offsets are lies.
 */
export function saveFileMark(m: FileMark): void {
  try {
    const conn = open()
    if (!conn || degraded) return
    conn
      .prepare(
        `INSERT INTO files (path, pos, mtime, size, parentUse, agentName) VALUES (?,?,?,?,?,?)
         ON CONFLICT(path) DO UPDATE SET pos=excluded.pos, mtime=excluded.mtime, size=excluded.size,
           parentUse=COALESCE(excluded.parentUse, files.parentUse),
           agentName=COALESCE(excluded.agentName, files.agentName)
         WHERE excluded.pos >= files.pos
            OR (excluded.size < files.size AND excluded.mtime > files.mtime)`
      )
      .run(m.path, m.pos, m.mtime, m.size, m.parentUse, m.agentName)
  } catch (err) {
    console.error(
      `[traces] file mark failed for ${m.path}: ${(err as Error).message}`
    )
  }
}

/** Every Agent/Task call the walker sees, so a sub-agent transcript can find
 *  the tool call that spawned it without re-reading its parent end to end. */
export function noteAgentCall(a: {
  id: string
  session: string
  ts: string
  description: string
}): void {
  try {
    const conn = open()
    if (!conn || degraded) return
    conn
      .prepare(
        "INSERT OR REPLACE INTO agent_calls (id, session, ts, description) VALUES (?,?,?,?)"
      )
      .run(a.id, a.session, a.ts, a.description)
  } catch {
    /* the parent link is a nicety; losing one is not worth a log line */
  }
}

/** The newest Agent call in this session whose description matches. A
 *  sub-agent's meta truncates the description with an ellipsis, so it is
 *  matched by prefix rather than by equality. */
export function findAgentCall(
  session: string,
  description: string,
  before: string
): string | null {
  try {
    const conn = open()
    if (!conn || !description) return null
    const stem = description.replace(/[…\s]+$/, "")
    if (!stem) return null
    const r = conn
      .prepare(
        `SELECT id FROM agent_calls WHERE session = ? AND ts <= ? AND description LIKE ? ORDER BY ts DESC LIMIT 1`
      )
      .get(session, before, `${stem}%`) as Raw | undefined
    return r ? String(r.id) : null
  } catch {
    return null
  }
}

/** A parent resolved on a later pass, written back onto every span the file
 *  produced — which is what the `file` column is for. */
export function adoptParent(file: string, parent: string): void {
  try {
    const conn = open()
    if (!conn || degraded) return
    conn
      .prepare("UPDATE spans SET parent = ? WHERE file = ? AND parent IS NULL")
      .run(parent, file)
  } catch {
    /* see noteAgentCall */
  }
}

/** The error `closeStale` writes. A GUESS, and marked as one: `writeSpan`
 *  lets a real close overturn a row carrying it. */
const UNREPORTED = "unreported"

/** Rows a hook opened that nothing ever closed. The hook-trace's stale rule:
 *  ten minutes running is a process that died without reporting — or a call
 *  that is simply long, which is why the verdict can be overturned. */
export function closeStale(maxAgeMs = 10 * 60 * 1000): number {
  try {
    const conn = open()
    if (!conn || degraded) return 0
    const cutoff = new Date(Date.now() - maxAgeMs).toISOString()
    return Number(
      conn
        .prepare(
          "UPDATE spans SET outcome = 'error', error = COALESCE(error, ?) WHERE outcome = 'running' AND ts < ?"
        )
        .run(UNREPORTED, cutoff).changes
    )
  } catch {
    return 0
  }
}
