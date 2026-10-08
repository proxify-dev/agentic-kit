import {
  closeSync,
  openSync,
  readSync,
  readFileSync,
  readdirSync,
  statSync,
} from "node:fs"
import { basename, dirname, join, sep } from "node:path"
import { HOME_CLAUDE } from "./paths.ts"
import { splitTool } from "./traces-blobs.ts"
import * as store from "./traces.ts"
import { env } from "./brand.ts"

/**
 * The durable witness: Claude Code's own transcripts.
 *
 * Checked on 2026-09-12 against a real session — 136 `tool_use` and 136
 * `tool_result` blocks, every one with its id, a per-line timestamp and both
 * payloads. Every session that has ever run on this machine already holds its
 * tool spans on disk. So the store is an INDEX over what exists, not a recorder
 * that starts empty, and the Tools fold shows years of history on the day it
 * ships.
 *
 * Two rules make that safe. **Nothing here writes to a transcript** — this is
 * the second reader of a file Claude Code owns, never a second writer. And **a
 * malformed line is skipped, never fatal**: the writer may be mid-append, and
 * the next pass picks the line up when it is whole (hooktrace's rule).
 *
 * Payloads are not copied. A span's blob ref is `<path>:<byte offset of the
 * line>`, which is the episodic indexer's own trick, so indexing 3 GB of
 * transcripts costs a few megabytes of rows and nothing else.
 *
 * Layout, from the disk rather than from memory:
 *
 *   <claudeHome>/projects/<slugged cwd>/<sessionId>.jsonl          a session
 *   <claudeHome>/projects/<slugged cwd>/<sessionId>/subagents/
 *       agent-<agentId>.jsonl                                   a sub-agent
 *       agent-<agentId>.meta.json                               its name + task
 *       workflows/<wf_id>/agent-<agentId>.jsonl                 a workflow step
 *
 * That third shape is why `subagents/` is walked RECURSIVELY rather than
 * listed. Counted on this machine: 375 sub-agent transcripts sit directly in
 * `subagents/` and 2,329 sit one workflow folder deeper — so a flat listing
 * would have indexed 14% of them and reported success.
 *
 * A sub-agent's lines carry the PARENT's `sessionId` and their own `agentId`,
 * which is why `identity.session` is the parent session and the nesting lives
 * in `parent` instead. The parent tool_use id is not on the wire anywhere, so
 * it is recovered by matching the meta's `description` against the Agent call
 * the walker already indexed in the parent transcript — verified on a real
 * pair, 66 ms apart.
 */

const ROOT = env("TRACES_PROJECTS") || join(HOME_CLAUDE, "projects")

/** A pass indexes the newest transcripts and stops at this many bytes; the
 *  next one walks further back. 3 GB of JSONL in one go is a minute spent on
 *  sessions nobody is going to open first. */
export const BACKFILL_BYTES =
  Number(env("TRACES_BACKFILL_MB") ?? 256) * 1024 * 1024
/** The small budget: a read that refreshes before it answers. */
export const TICK_BYTES =
  Number(env("TRACES_TICK_MB") ?? 16) * 1024 * 1024
/** `pass({recent})` looks only at files written in this window — a turn that
 *  just ended, not the backlog. */
const RECENT_MS = 60 * 60 * 1000
/** Summaries are an index column; a 1.2 MB screenshot does not need one. */
const PAYLOAD_TRIM = 4096
const CHUNK = 256 * 1024

type Line = Record<string, unknown>
type Block = {
  type?: string
  id?: string
  name?: string
  input?: Record<string, unknown>
  tool_use_id?: string
  content?: unknown
  is_error?: boolean
}

/* ── enumeration ─────────────────────────────────────────────────────────── */

export type Candidate = {
  path: string
  mtime: number
  size: number
  sub: boolean
}

/** Every `.jsonl` under a `subagents/` tree. Depth-capped because it is a
 *  directory an outside program writes, and an unbounded walk of one of those
 *  is how a startup path becomes a hang. Four levels covers the workflow
 *  shape twice over. */
function jsonlUnder(dir: string, depth: number): string[] {
  if (depth > 4) return []
  let names: string[]
  try {
    names = readdirSync(dir)
  } catch {
    return [] // a session with no sub-agents is the common case
  }
  const out: string[] = []
  for (const name of names) {
    const path = join(dir, name)
    if (name.endsWith(".jsonl")) out.push(path)
    else if (!name.includes(".")) out.push(...jsonlUnder(path, depth + 1))
  }
  return out
}

/** Session transcripts first, then sub-agent files, each newest-first.
 *
 *  The order is load-bearing: a sub-agent's parent link is resolved out of the
 *  `agent_calls` the parent transcript produces, so walking parents first turns
 *  a second pass into a first one. When it still misses, `adopt()` below fixes
 *  it later rather than leaving the nesting wrong forever. */
export function candidates(): Candidate[] {
  const out: Candidate[] = []
  let projects: string[]
  try {
    projects = readdirSync(ROOT)
  } catch {
    return out // no transcripts on this machine — an empty store, not a crash
  }
  for (const project of projects) {
    const dir = join(ROOT, project)
    let names: string[]
    try {
      names = readdirSync(dir)
    } catch {
      continue
    }
    for (const name of names) {
      const path = join(dir, name)
      try {
        if (name.endsWith(".jsonl")) {
          const st = statSync(path)
          out.push({ path, mtime: st.mtimeMs, size: st.size, sub: false })
          continue
        }
        for (const f of jsonlUnder(join(path, "subagents"), 0)) {
          const st = statSync(f)
          out.push({ path: f, mtime: st.mtimeMs, size: st.size, sub: true })
        }
      } catch {
        /* a session dir with no subagents/ is the common case */
      }
    }
  }
  return out.sort((a, b) => Number(a.sub) - Number(b.sub) || b.mtime - a.mtime)
}

/* ── sub-agent context ───────────────────────────────────────────────────── */

type SubContext = {
  agent: string | null
  parent: string | null
  description: string
}

/**
 * The session id is the path segment immediately before `subagents`, whatever
 * depth the file sits at — `<session>/subagents/agent-x.jsonl` and
 * `<session>/subagents/workflows/<wf>/agent-x.jsonl` both answer the same.
 * Taking two dirnames would read "workflows" as a session id for 2,329 files.
 */
function parentSession(path: string): string {
  const parts = path.split(sep)
  const at = parts.lastIndexOf("subagents")
  return at > 0 ? parts[at - 1] : basename(dirname(dirname(path)))
}

function subContext(path: string, mark: store.FileMark | null): SubContext {
  let agent = mark?.agentName ?? null
  let parent = mark?.parentUse ?? null
  let description = ""
  try {
    const meta = JSON.parse(
      readFileSync(path.replace(/\.jsonl$/, ".meta.json"), "utf8")
    ) as Record<string, unknown>
    description = String(meta.description ?? "")
    agent ??= String(meta.name ?? meta.agentType ?? "") || null
  } catch {
    /* a sub-agent file with no meta still yields spans, just unattributed */
  }
  if (!parent && description) {
    const first = firstTimestamp(path)
    parent = store.findAgentCall(
      parentSession(path),
      description,
      first ?? "9999"
    )
  }
  return { agent, parent, description }
}

function firstTimestamp(path: string): string | null {
  let fd = -1
  try {
    fd = openSync(path, "r")
    const buf = Buffer.alloc(64 * 1024)
    const n = readSync(fd, buf, 0, buf.length, 0)
    for (const raw of buf.subarray(0, n).toString("utf8").split("\n")) {
      if (!raw.trim()) continue
      const ts = (JSON.parse(raw) as Line).timestamp
      if (typeof ts === "string") return ts
    }
    return null
  } catch {
    return null
  } finally {
    if (fd >= 0) closeSync(fd)
  }
}

/* ── one file ────────────────────────────────────────────────────────────── */

const trim = (v: unknown): unknown =>
  typeof v === "string" && v.length > PAYLOAD_TRIM
    ? v.slice(0, PAYLOAD_TRIM)
    : v
const bytesOf = (v: unknown): number => {
  try {
    return Buffer.byteLength(
      typeof v === "string" ? v : JSON.stringify(v ?? null)
    )
  } catch {
    return 0
  }
}

/** The two shapes a refusal takes in a transcript. A denied call is not an
 *  error the model made — it is a person or a rule saying no — and the fold's
 *  outcome filter is only useful if it can tell them apart. */
const DENIED =
  /The user doesn't want to proceed|haven't granted it yet|requested permissions to use/

function outcomeOf(b: Block): {
  outcome: "ok" | "error" | "denied"
  error?: string
} {
  if (!b.is_error) return { outcome: "ok" }
  const text =
    typeof b.content === "string" ? b.content : JSON.stringify(b.content ?? "")
  const message = text.replace(/\s+/g, " ").slice(0, 400)
  return DENIED.test(text)
    ? { outcome: "denied", error: message }
    : { outcome: "error", error: message }
}

function handle(
  line: Line,
  offset: number,
  path: string,
  ctx: SubContext | null
): void {
  const ts =
    typeof line.timestamp === "string"
      ? line.timestamp
      : new Date().toISOString()
  const session = typeof line.sessionId === "string" ? line.sessionId : null
  const prompt = typeof line.promptId === "string" ? line.promptId : null
  const content = (line.message as { content?: unknown } | undefined)?.content
  if (!Array.isArray(content)) return

  if (line.type === "assistant") {
    for (const b of content as Block[]) {
      if (b.type !== "tool_use" || !b.id || !b.name) continue
      const { server, tool } = splitTool(b.name)
      store.record({
        id: b.id,
        witness: "transcript",
        ts,
        server,
        tool,
        origin: "session",
        parent: ctx?.parent ?? null,
        identity: {
          session,
          prompt,
          cwd: typeof line.cwd === "string" ? line.cwd : null,
          agent: ctx?.agent ?? null,
        },
        input: trim(b.input),
        bytes: { in: bytesOf(b.input) },
        blobs: { input: `${path}:${offset}` },
        file: path,
      })
      // An Agent call is what a sub-agent transcript hangs off. Indexed here so
      // the link is a keyed lookup later instead of a second full-file read.
      if (
        server === "claude" &&
        (tool === "Agent" || tool === "Task") &&
        session
      )
        store.noteAgentCall({
          id: b.id,
          session,
          ts,
          description: String(b.input?.description ?? ""),
        })
    }
    return
  }

  if (line.type !== "user") return
  for (const b of content as Block[]) {
    if (b.type !== "tool_result" || !b.tool_use_id) continue
    const { outcome, error } = outcomeOf(b)
    store.record({
      id: b.tool_use_id,
      witness: "transcript",
      endTs: ts,
      outcome,
      ...(error ? { error } : {}),
      identity: { session, prompt },
      output: trim(b.content),
      bytes: { out: bytesOf(b.content) },
      blobs: { output: `${path}:${offset}` },
      file: path,
    })
  }
}

/**
 * Read the new bytes of one transcript and emit its spans. Returns how many
 * bytes were consumed, which is what the pass budget counts.
 *
 * Incremental by byte offset: a 200 MB session that gained one line costs one
 * line of work. The trailing partial line is deliberately NOT consumed — the
 * writer may be mid-append, and advancing past it would lose the line forever.
 *
 * `known` is the mark a pass already loaded in its one select; a caller that
 * has none (the hook witness's reconcile) omits it and one is read here.
 *
 * `limit` stops the walk BETWEEN CHUNKS, mid-file if need be: a never-indexed
 * 65 MB transcript read to its end held `ak trace --refresh` for 14 s. The
 * mark moves with every committed chunk, so the next run resumes at the line
 * this one stopped on. A reconcile passes no limit and reads to the end.
 */
export function walkFile(
  path: string,
  known?: store.FileMark | null,
  limit: { bytes?: number; deadline?: number } = {}
): number {
  let fd = -1
  try {
    const st = statSync(path)
    const mark = known === undefined ? store.fileMark(path) : known
    // A shrunk file was rotated or rewritten; the offsets we hold are lies.
    const pos = mark && mark.size <= st.size ? mark.pos : 0
    if (pos >= st.size) {
      // Nothing new to read. A file touched without growing (a copy, a
      // restore) or an empty one would otherwise fail the pass's size+mtime
      // skip on every run forever; its mark takes the new stamp once.
      if (!mark || mark.mtime !== st.mtimeMs || mark.size !== st.size)
        store.saveFileMark({
          path,
          pos,
          mtime: st.mtimeMs,
          size: st.size,
          parentUse: mark?.parentUse ?? null,
          agentName: mark?.agentName ?? null,
        })
      return 0
    }

    const sub = path.split(sep).includes("subagents")
    const ctx = sub ? subContext(path, mark) : null

    const markAt = (at: number, size: number): store.FileMark => ({
      path,
      pos: at,
      mtime: st.mtimeMs,
      size,
      parentUse: ctx?.parent ?? null,
      agentName: ctx?.agent ?? null,
    })

    fd = openSync(path, "r")
    const buf = Buffer.alloc(CHUNK)
    let carry = Buffer.alloc(0)
    let lineStart = pos
    let saved = pos
    let consumed = 0
    let stopped = false
    for (;;) {
      const n = readSync(fd, buf, 0, buf.length, pos + consumed)
      if (n <= 0) break
      let chunk = Buffer.concat([carry, buf.subarray(0, n)])
      consumed += n
      const found: [string, number][] = []
      for (;;) {
        const nl = chunk.indexOf(0x0a)
        if (nl < 0) break
        const raw = chunk.subarray(0, nl)
        // Most lines are attachments, modes and titles. A byte search before
        // decoding and JSON.parse is the difference between minutes and
        // seconds over 3 GB.
        if (raw.includes('"tool_use"') || raw.includes('"tool_result"'))
          found.push([raw.toString("utf8"), lineStart])
        lineStart += raw.length + 1
        chunk = chunk.subarray(nl + 1)
      }
      carry = chunk
      // One commit per chunk, taken AFTER the read: the write lock is held
      // for the upserts and never across the disk read that produced them.
      // The mark moves in the SAME commit, so the rows and the offset they
      // came from are durable together — a run killed or stopped here resumes
      // at exactly this line. Its `size` is that offset rather than the
      // file's, because a pass skips a file whose size and mtime equal its
      // mark, and a half-walked file must not look finished.
      if (found.length || lineStart > saved) {
        const at = lineStart
        store.batch(() => {
          for (const [text, off] of found) {
            try {
              handle(JSON.parse(text) as Line, off, path, ctx)
            } catch {
              /* mid-append or malformed: skipped, never fatal */
            }
          }
          if (at > saved) store.saveFileMark(markAt(at, at))
        })
        saved = at
      }
      // Stop only after progress: a single line longer than the budget still
      // lands whole, or a file holding one would never move at all.
      const spent = lineStart - pos
      if (
        spent > 0 &&
        ((limit.bytes !== undefined && spent >= limit.bytes) ||
          (limit.deadline !== undefined && Date.now() >= limit.deadline))
      ) {
        stopped = true
        break
      }
    }
    // Read to the end: the mark takes the file's own size and mtime, so the
    // next pass skips it until it grows. The torn tail stays unconsumed.
    if (!stopped) store.saveFileMark(markAt(lineStart, st.size))
    // The parent may only have become findable during THIS pass; when it did,
    // every span this file produced is adopted at once.
    if (ctx?.parent) store.adoptParent(path, ctx.parent)
    return lineStart - pos
  } catch (err) {
    console.error(`[traces] walk failed for ${path}: ${(err as Error).message}`)
    return 0
  } finally {
    if (fd >= 0) closeSync(fd)
  }
}

/** The one file behind a live session, walked right now. The hook witness calls
 *  it when it closes a row, so `get()` can answer with real payload offsets
 *  within seconds of the call ending rather than when the turn's Stop hook
 *  runs the one-shot. */
export function reconcile(path: string): void {
  try {
    walkFile(path)
  } catch (err) {
    console.error(
      `[traces] reconcile failed for ${path}: ${(err as Error).message}`
    )
  }
}

/**
 * One pass over the candidates — what the one-shot (traces-index.ts) runs.
 *
 * A pass that finds nothing new must cost about what `candidates()` costs, the
 * ten thousand stats, because it runs after every turn of every session. So
 * every mark is loaded in ONE select up front, and a file whose size and mtime
 * both equal its mark is skipped before it is opened or looked up. It yields
 * only after a file that did real work, so a no-change pass never waits on the
 * event loop ten thousand times.
 *
 * The budget is what stops a first run from spending a minute on transcripts
 * from 2024 before the newest session is indexed; `deadline` is the one-shot's
 * wall-clock cap; `recent` looks only at files written in the last hour — the
 * turn that just ended, not the backlog.
 */
export async function pass(
  budget: number,
  opts: { recent?: boolean; deadline?: number } = {}
): Promise<{ files: number; bytes: number }> {
  let files = 0
  let bytes = 0
  const marks = store.fileMarks()
  const since = opts.recent ? Date.now() - RECENT_MS : 0
  for (const c of candidates()) {
    if (bytes >= budget) break
    if (opts.deadline && Date.now() >= opts.deadline) break
    if (c.mtime < since) continue
    const mark = marks.get(c.path) ?? null
    if (mark && mark.size === c.size && mark.mtime === c.mtime) continue
    const got = walkFile(c.path, mark, {
      bytes: budget - bytes,
      deadline: opts.deadline,
    })
    if (got > 0) {
      files++
      bytes += got
      await new Promise<void>((r) => setImmediate(r))
    }
  }
  return { files, bytes }
}
