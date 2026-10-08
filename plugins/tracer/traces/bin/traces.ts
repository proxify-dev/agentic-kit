// FIRST, and it must stay first: the node floor and `--root` are only
// correct before paths.ts computes BRAIN. See src/preflight.ts.
import "../src/preflight.ts"
import { existsSync } from "node:fs"
import { parseArgs } from "node:util"
import { cmd } from "../src/brand.ts"
import * as store from "../src/traces.ts"
import { runIndex } from "../src/traces-index.ts"
import { TICK_BYTES } from "../src/traces-walk.ts"
import type { TraceFilter, TraceOrigin, TraceOutcome } from "../src/traces.types.ts"

/**
 * traces — the tool-call index from a shell. The one door `ak trace`
 * (`ak tracer calls`, this plugin's CLI) and the tracer Stop hook reach it through.
 *
 *   index [--recent]                                  {files, bytes, ms, passes, locked}
 *   list  [--server --tool --session --origin --outcome
 *          --since --until --q --limit --cursor --refresh]   TracePage
 *   get   <id>                                        TraceDetail
 *   stats [--server]                                  TraceStats
 *   purge --before ISO [--server] [--dry-run]         {removed, dryRun}
 *
 * THE CONTRACT, which a Python caller parses: on success exactly ONE JSON
 * document on stdout; on failure nothing on stdout and one line on stderr.
 * Exit 0 ok · 1 not found, no index yet, or a failed read · 2 an ambiguous
 * `--session` prefix (stdout then carries `{"candidates": [...]}`) · 64 usage.
 * The shapes are traces.types.ts, field for field — renaming one breaks the
 * reader on the other side of the pipe.
 *
 * Only `index` creates the store. Every read opens the file read-only, and a
 * missing file is "no index yet" rather than a new empty database — an empty
 * store would look, to every later reader, like a machine with no history.
 */

const USAGE =
  "usage: traces index [--recent] | list [--server S] [--tool T] [--session ID] [--origin O] [--outcome O] " +
  "[--since ISO] [--until ISO] [--q TEXT] [--limit N] [--cursor C] [--refresh] | get <id> | stats [--server S] | " +
  "purge --before ISO [--server S] [--dry-run]"

const NO_INDEX = `no trace index yet — run: ${cmd("trace index")}`
/** A full uuid; anything shorter is a prefix to resolve. */
const UUID_LENGTH = 36
/** How many sessions an ambiguous prefix lists, newest first. */
const CANDIDATES = 20
/** How long `list --refresh` may walk before it answers. */
const REFRESH_MS = 2000
const ORIGINS = new Set<string>(["session", "agent", "automation", "bench"])
const OUTCOMES = new Set<string>(["ok", "error", "refused", "denied", "running"])

/** A failure with its exit code; `doc` is what stdout carries anyway (exit 2). */
class Exit extends Error {
  code: number
  doc: unknown
  constructor(code: number, message: string, doc?: unknown) {
    super(message)
    this.code = code
    this.doc = doc
  }
}
/** One line, like every other failure. */
const usage = (why: string): Exit => new Exit(64, `${why} — ${USAGE}`)

/** A parser error is a usage error: same exit, same one line. */
function parsed<T>(fn: () => T): T {
  try {
    return fn()
  } catch (err) {
    throw usage((err as Error).message)
  }
}

/** `--root` is honoured before any module loads (preflight, the bundle's
 *  banner); it is declared here only so the parser does not refuse it. */
const ROOT = { root: { type: "string" } } as const

/** Any spelling Date.parse reads, normalised to the store's own — `ts` is a
 *  `toISOString()` string, and the filters compare it as text. */
function iso(flag: string, v: string | undefined): string | undefined {
  if (v === undefined) return undefined
  const t = Date.parse(v)
  if (Number.isNaN(t)) throw usage(`--${flag} is not a date: ${v}`)
  return new Date(t).toISOString()
}

function readable(): void {
  if (!store.openReadOnly()) throw new Exit(1, NO_INDEX)
}

/** An 8-character prefix becomes the one session it names, or the exit says
 *  why not: none (1), or several — listed, newest first (2). */
function resolveSession(given: string): string {
  if (!given) throw usage("--session is empty")
  if (given.length >= UUID_LENGTH) return given
  // One past the cap, so "more than twenty" is said as that and not as twenty.
  const found = store.raising(() => store.sessionsLike(given, CANDIDATES + 1))
  if (!found.length) throw new Exit(1, `no session starts with ${given}`)
  if (found.length > 1) {
    const count = found.length > CANDIDATES ? `more than ${CANDIDATES}` : String(found.length)
    throw new Exit(2, `${count} sessions start with ${given} — give more of the id`, {
      candidates: found.slice(0, CANDIDATES),
    })
  }
  return found[0]
}

async function list(args: string[]): Promise<unknown> {
  const { values: v } = parsed(() =>
    parseArgs({
      args,
      strict: true,
      options: {
        server: { type: "string" },
        tool: { type: "string" },
        session: { type: "string" },
        origin: { type: "string" },
        outcome: { type: "string" },
        since: { type: "string" },
        until: { type: "string" },
        q: { type: "string" },
        limit: { type: "string" },
        cursor: { type: "string" },
        refresh: { type: "boolean" },
        ...ROOT,
      },
    }),
  )
  if (v.origin !== undefined && !ORIGINS.has(v.origin)) throw usage(`--origin is one of ${[...ORIGINS].join(", ")}`)
  if (v.outcome !== undefined && !OUTCOMES.has(v.outcome))
    throw usage(`--outcome is one of ${[...OUTCOMES].join(", ")}`)
  let limit: number | undefined
  if (v.limit !== undefined) {
    if (!/^\d+$/.test(v.limit) || Number(v.limit) < 1) throw usage(`--limit is a positive integer: ${v.limit}`)
    limit = Math.min(500, Number(v.limit))
  }
  const since = iso("since", v.since)
  const until = iso("until", v.until)

  // A refresh indexes the turns that just ended before answering. It never
  // creates the store (that is `index`'s alone), never waits on a run already
  // going — its touch of `again` asks that run for one more pass instead —
  // never waits out a migration, and walks for about two seconds at most:
  // a never-indexed 65 MB transcript once held a list for 14. What is left is
  // resumed from its mark by the next run.
  if (v.refresh) {
    if (!existsSync(store.dbFile())) throw new Exit(1, NO_INDEX)
    await runIndex({ recent: true, budget: TICK_BYTES, upkeep: false, maxMs: REFRESH_MS })
  } else readable()

  const filter: TraceFilter = {
    ...(v.server ? { server: v.server } : {}),
    ...(v.tool ? { tool: v.tool } : {}),
    ...(v.session !== undefined ? { session: resolveSession(v.session) } : {}),
    ...(v.origin ? { origin: v.origin as TraceOrigin } : {}),
    ...(v.outcome ? { outcome: v.outcome as TraceOutcome } : {}),
    ...(since ? { since } : {}),
    ...(until ? { until } : {}),
    ...(v.q ? { q: v.q } : {}),
    ...(limit ? { limit } : {}),
    ...(v.cursor ? { cursor: v.cursor } : {}),
  }
  return store.raising(() => store.list(filter))
}

function get(args: string[]): unknown {
  const { positionals } = parsed(() =>
    parseArgs({ args, strict: true, allowPositionals: true, options: { ...ROOT } }),
  )
  if (positionals.length !== 1) throw usage(`get takes one id, got ${positionals.length}`)
  readable()
  const detail = store.raising(() => store.get(positionals[0]))
  if (!detail) throw new Exit(1, `no trace ${positionals[0]}`)
  return detail
}

function stats(args: string[]): unknown {
  const { values } = parsed(() =>
    parseArgs({ args, strict: true, options: { server: { type: "string" }, ...ROOT } }),
  )
  readable()
  return store.raising(() => store.stats(values.server))
}

function purge(args: string[]): unknown {
  const { values } = parsed(() =>
    parseArgs({
      args,
      strict: true,
      options: {
        before: { type: "string" },
        server: { type: "string" },
        "dry-run": { type: "boolean" },
        ...ROOT,
      },
    }),
  )
  const before = iso("before", values.before)
  if (!before) throw usage("purge needs --before")
  const dryRun = values["dry-run"] === true
  // A dry run only counts, so it reads like any other read. The real one
  // writes, and still must not create a store in order to empty it.
  if (dryRun) readable()
  else if (!existsSync(store.dbFile())) throw new Exit(1, NO_INDEX)
  const result = store.raising(() =>
    store.purge({ before, dryRun, ...(values.server ? { server: values.server } : {}) }),
  )
  return { ...result, dryRun }
}

async function index(args: string[]): Promise<unknown> {
  const { values } = parsed(() =>
    parseArgs({ args, strict: true, options: { recent: { type: "boolean" }, ...ROOT } }),
  )
  return runIndex({ recent: values.recent === true })
}

const VERBS: Record<string, (args: string[]) => unknown> = { index, list, get, stats, purge }

/**
 * `process.exitCode`, never `process.exit()`: a 500-row page is hundreds of KB
 * on a pipe, and exit() can cut stdout off mid-document.
 */
async function main(argv: string[]): Promise<void> {
  const [verb, ...rest] = argv
  try {
    const run = verb ? VERBS[verb] : undefined
    if (!run) throw usage(verb ? `no verb ${verb}` : "no verb")
    const doc = await run(rest)
    process.stdout.write(`${JSON.stringify(doc)}\n`)
    process.exitCode = 0
  } catch (err) {
    const e = err instanceof Exit ? err : new Exit(1, String((err as Error)?.message ?? err).split("\n")[0])
    if (e.doc !== undefined) process.stdout.write(`${JSON.stringify(e.doc)}\n`)
    process.stderr.write(`traces: ${e.message}\n`)
    process.exitCode = e.code
  } finally {
    store.close()
  }
}

/** The entry check is on argv, as in the app's brain-mcp.ts: the source and the bundle
 *  are both started by path and never imported. */
if (/(^|[\\/])traces\.(ts|mjs)$/.test(process.argv[1] ?? "")) await main(process.argv.slice(2))
