/**
 * The trace plane: one durable record per request.
 *
 * Two rules, both structural rather than remembered:
 *
 *   1. HEADERS ARE NEVER WRITTEN. Every request carries a live credential in
 *      `authorization`. This module has no code path that accepts headers, so
 *      the rule cannot be broken by a later edit that "just adds a field".
 *
 *   2. Bodies are content-addressed. A Claude Code turn resends the whole
 *      transcript, and the system blocks plus tool schemas are byte-identical
 *      across thousands of requests. Hashing them means the expensive part is
 *      stored once and every row after that is a 64-char reference — which is
 *      also what makes "when did the system prompt change" a cheap query
 *      instead of a mitmproxy ritual.
 *
 * Storage is NDJSON, one file per day, plus a blob directory. No database on
 * purpose: this must survive being read by grep at 2am, and a schema is easier
 * to change when nothing has migrations. Rows stay, and so do the small
 * content-addressed parts; bodies and tails — the prompts — are kept
 * `blobDays` (config.ts, `sweep`).
 */
import { createHash } from "node:crypto"
import { appendFileSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, readSync, readdirSync, statSync, writeFileSync } from "node:fs"
import { readFile, readdir, stat, unlink, writeFile } from "node:fs/promises"
import { join } from "node:path"
import type { CaptureLevel } from "./config.ts"
import type { Hints, Identity } from "./identity.ts"

export type Usage = {
  input: number
  output: number
  cacheRead: number
  cacheCreation: number
}

export type TraceRow = {
  v: 2
  id: string
  ts: string
  /** Wall time from first byte in to last byte out. */
  durMs: number
  /** Time to first upstream byte — the number that tells latency from generation. */
  ttfbMs: number | null
  method: string
  path: string
  status: number | null
  /** Only on rows written before the gateway held its own accounts: `chain` | `direct`. */
  upstream?: string
  /** Which account served this. Null only when nothing could be established. */
  account: string | null
  /**
   * HOW the account above is known — the difference between a fact and a
   * good-faith reading, kept in the data rather than in a tooltip:
   *
   *   pinned     the gateway forced it; the request could not have gone elsewhere
   *   preferred  the gateway asked for it; the engine may have failed over
   *   rotated    the engine chose by rotation
   *   observed   old rows only: a proxy behind us named its current account
   *   null       nothing established it (a passthrough path)
   */
  accountSource: "pinned" | "preferred" | "observed" | "rotated" | null
  model: string | null
  stream: boolean
  identity: Identity
  /** Claude Code's routing hints, parsed (identity.ts). Absent on rows written before them. */
  hints?: Hints | null
  usage: Usage | null
  stopReason: string | null
  /** The API's message id (`msg_…`) — the key a transcript's assistant entry shares. Absent on older rows. */
  messageId?: string | null
  /**
   * Answered by the gateway itself, never forwarded (dry-run.ts): `status` is
   * the canned reply's, no account served it, and the body blob is kept at any
   * capture level. Absent on every other row.
   */
  dryRun?: true
  /**
   * Content hashes — the join key for prompt/tool drift questions. `settings` (every
   * top-level key but model, system, tools, messages, metadata, safeguards) and
   * `safeguards` are stored at meta and up; `tail` is a side call's newest message
   * (`putCall`). The three are absent on rows written before them.
   */
  blobs: {
    system: string | null
    tools: string | null
    body: string | null
    settings?: string | null
    safeguards?: string | null
    tail?: string | null
  }
  /** capture: gaps only — a transcript holds this call; false means its body is the one copy. */
  recorded?: boolean
  shape: { messages: number; tools: number; systemBlocks: number; bytesIn: number; bytesOut: number }
  /** What policy did, in order. Empty means nothing matched. */
  policy: { rule: string; action: string; detail?: string }[]
  error: string | null
}

/** What one retention pass removed (`Trace.sweep`). */
export type Swept = { at: string; removed: number; bytes: number }

const DAY_MS = 86_400_000
const DAY_FILE = /^\d{4}-\d{2}-\d{2}\.ndjson$/
const isoDay = (ms: number) => new Date(ms).toISOString().slice(0, 10)
const sha = (b: Buffer | string) => createHash("sha256").update(b).digest("hex")
const blobFile = (blobs: string, h: string) => join(blobs, h.slice(0, 2), `${h}.json`)

/** The request keys that are neither content nor identity: what shaped the generation. */
const NOT_SETTINGS = new Set(["model", "system", "tools", "messages", "metadata", "safeguards"])
export function settingsOf(obj: Record<string, unknown>): Record<string, unknown> | null {
  const s = Object.fromEntries(Object.entries(obj).filter(([k]) => !NOT_SETTINGS.has(k)))
  return Object.keys(s).length ? s : null
}

export class Trace {
  #dir: string
  #blobs: string
  #capture: CaptureLevel
  #seen = new Set<string>()
  /** Every hash handed out since the last sweep — a row is written after its reply. */
  #fresh = new Set<string>()
  #n = 0

  constructor(dir: string, blobs: string, capture: CaptureLevel) {
    this.#dir = dir
    this.#blobs = blobs
    this.#capture = capture
  }

  /** Monotonic per-process id; the ts field carries the real ordering. */
  nextId(): string {
    this.#n += 1
    return `${process.pid.toString(36)}-${this.#n.toString(36)}`
  }

  /**
   * Store a JSON fragment by content hash, return the hash. `always` stores it
   * whatever the capture level — a dry run's body, which its caller asked for.
   * `#seen` keeps the repeat case to zero syscalls, which matters because the
   * system prompt is re-hashed on literally every turn.
   */
  putBlob(value: unknown, always = false): string | null {
    if ((this.#capture === "none" && !always) || value === undefined || value === null) return null
    const text = JSON.stringify(value)
    const h = sha(text)
    this.#fresh.add(h)
    if (this.#seen.has(h)) return h
    const file = blobFile(this.#blobs, h)
    if (!existsSync(file)) {
      mkdirSync(join(this.#blobs, h.slice(0, 2)), { recursive: true, mode: 0o700 })
      writeFileSync(file, text, { mode: 0o600 })
    }
    this.#seen.add(h)
    return h
  }

  /**
   * A call's blobs, by capture level:
   *   none  nothing
   *   meta  system prompt, tools, settings, safeguards — small, content-addressed,
   *         kept for good; the body as a hash only, no content
   *   gaps  meta, plus what no transcript holds: the whole body when `recorded` is
   *         false (a session saving no transcript, count_tokens), and a side call's
   *         newest message — it resends the conversation, which the transcript
   *         has, plus one message, which it does not
   *   full  meta, plus every body
   */
  putCall(obj: Record<string, unknown> | null, is: { recorded: boolean; side: boolean }): TraceRow["blobs"] {
    const b: TraceRow["blobs"] = { system: this.putBlob(obj?.system), tools: this.putBlob(obj?.tools), body: null }
    if (!obj || this.#capture === "none") return b
    const settings = settingsOf(obj)
    if (settings) b.settings = this.putBlob(settings)
    if (obj.safeguards !== undefined) b.safeguards = this.putBlob(obj.safeguards)
    const whole = this.#capture === "full" || (this.#capture === "gaps" && !is.recorded)
    b.body = whole ? this.putBlob(obj) : sha(JSON.stringify(obj))
    const messages = obj.messages
    if (this.#capture === "gaps" && is.recorded && is.side && Array.isArray(messages) && messages.length)
      b.tail = this.putBlob(messages.at(-1))
    return b
  }

  /**
   * Retention. Only content is ever deleted — a row's `body` or `tail`; the
   * system prompt, tools, settings and safeguards stay for good (the one copy
   * where no transcript records them). A content blob goes once every row naming
   * it is older than `days`, unless a pin (`keep`: `<dataDir>/pins`, saved xray
   * runs) or a request in flight still names it — `#fresh`, since a row lands
   * after its reply. Each day's rows are read as they expire, then never again:
   * `<blobs>/.swept` says through which day. Async, file by file: it runs inside
   * the live proxy and must not hold the event loop.
   */
  async sweep(days: number, keep: Iterable<string> = [], now = Date.now()): Promise<Swept> {
    const cutoff = now - days * DAY_MS
    const fromDay = isoDay(cutoff)
    const mark = join(this.#blobs, ".swept")
    const through = await readFile(mark, "utf8").then(
      (t) => String((JSON.parse(t) as { through?: unknown }).through ?? ""),
      () => "",
    )
    const held = new Set<string>([...keep, ...this.#fresh])
    this.#fresh = new Set()
    const doomed = new Set<string>()
    for (const name of (await readdir(this.#dir).catch(() => [] as string[])).sort()) {
      const day = name.slice(0, 10)
      const expired = day < fromDay
      if (!DAY_FILE.test(name) || (expired && day <= through)) continue
      for (const line of (await readFile(join(this.#dir, name), "utf8")).split("\n")) {
        let b: TraceRow["blobs"] | undefined
        try {
          b = (JSON.parse(line) as TraceRow).blobs
        } catch {
          continue // the half-written last line of a day still being appended
        }
        for (const h of [b?.body, b?.tail]) if (typeof h === "string") (expired ? doomed : held).add(h)
      }
    }
    let removed = 0
    let bytes = 0
    for (const h of doomed) {
      if (held.has(h)) continue
      const file = blobFile(this.#blobs, h)
      const st = await stat(file).catch(() => null)
      if (!st?.isFile() || st.mtimeMs >= cutoff) continue
      if (!(await unlink(file).then(() => true, () => false))) continue
      this.#seen.delete(h)
      removed += 1
      bytes += st.size
    }
    const last = isoDay(cutoff - DAY_MS)
    if (last > through && existsSync(this.#blobs))
      await writeFile(mark, `${JSON.stringify({ through: last })}\n`, { mode: 0o600 }).catch(() => {})
    return { at: new Date(now).toISOString(), removed, bytes }
  }

  write(row: TraceRow): void {
    const day = row.ts.slice(0, 10)
    if (!existsSync(this.#dir)) mkdirSync(this.#dir, { recursive: true, mode: 0o700 })
    appendFileSync(join(this.#dir, `${day}.ndjson`), `${JSON.stringify(row)}\n`, { mode: 0o600 })
  }
}

/**
 * Pinned blobs: every hash under `blobs` in `<dataDir>/pins/*.ndjson`, one
 * JSON line per call — `{row, ts, session, reason, blobs: {…}}`. The sweep keeps them.
 */
export function pinnedBlobs(pinsDir: string): string[] {
  if (!existsSync(pinsDir)) return []
  return readdirSync(pinsDir)
    .filter((f) => f.endsWith(".ndjson"))
    .flatMap((f) =>
      readFileSync(join(pinsDir, f), "utf8")
        .split("\n")
        .flatMap((line) => {
          try {
            const b = (JSON.parse(line) as { blobs?: Record<string, unknown> }).blobs ?? {}
            return Object.values(b).filter((h): h is string => typeof h === "string")
          } catch {
            return []
          }
        }),
    )
}

/** The body hashes xray's saved runs name (`-o NAME`, xray.ts `Saved`) — the sweep keeps them. */
export function savedBodies(xrayDir: string): string[] {
  if (!existsSync(xrayDir)) return []
  return readdirSync(xrayDir)
    .filter((f) => f.endsWith(".json"))
    .flatMap((f) => {
      try {
        const body = (JSON.parse(readFileSync(join(xrayDir, f), "utf8")) as { body?: unknown }).body
        return typeof body === "string" ? [body] : []
      } catch {
        return []
      }
    })
}

// ── reading it back ───────────────────────────────────────────────────────────
/** A blob by its hash, as `putBlob` stored it; null when it is not there. */
export function readBlob(blobs: string, hash: string): unknown {
  const f = blobFile(blobs, hash)
  return /^[0-9a-f]{64}$/.test(hash) && existsSync(f) ? (JSON.parse(readFileSync(f, "utf8")) as unknown) : null
}

export const isModelPath = (path: string): boolean => path.split("?")[0].endsWith("/v1/messages") // portable: ok — a URL path, not a file path
export const isModelCall = (r: TraceRow): boolean => isModelPath(r.path)

/** The call that carries a session's prompt: marked `main` by the hints; with none, a model call carrying tools. */
export function isMainCall(r: TraceRow): boolean {
  if (!isModelCall(r)) return false
  return r.hints?.requestClass ? r.hints.requestClass === "main" : r.shape.tools > 0
}

/**
 * The trace as it grows: each `read()` returns the rows appended since the one
 * before, from where the day files ended at construction. By byte offset, so a
 * busy day is not re-read every tick; a half-written line waits until whole.
 */
export class TraceTail {
  #dir: string
  #day = new Date().toISOString().slice(0, 10)
  #at = new Map<string, number>()
  #rest = new Map<string, Buffer>()

  constructor(dir: string) {
    this.#dir = dir
    for (const f of this.#files()) this.#at.set(f, statSync(join(dir, f)).size)
  }

  #files(): string[] {
    return existsSync(this.#dir) ? readdirSync(this.#dir).filter((f) => f.endsWith(".ndjson") && f.slice(0, 10) >= this.#day).sort() : []
  }

  read(): TraceRow[] {
    const out: TraceRow[] = []
    for (const f of this.#files()) {
      const p = join(this.#dir, f)
      const size = statSync(p).size
      const from = this.#at.get(f) ?? 0
      if (size <= from) continue
      const buf = Buffer.alloc(size - from)
      const fd = openSync(p, "r")
      try {
        readSync(fd, buf, 0, buf.length, from)
      } finally {
        closeSync(fd)
      }
      this.#at.set(f, size)
      const text = Buffer.concat([this.#rest.get(f) ?? Buffer.alloc(0), buf])
      const nl = text.lastIndexOf(0x0a)
      this.#rest.set(f, text.subarray(nl + 1))
      for (const line of text.subarray(0, nl + 1).toString("utf8").split("\n"))
        try {
          if (line) out.push(JSON.parse(line) as TraceRow)
        } catch {
          // Not a row; skipped.
        }
    }
    return out
  }
}

/**
 * Usage from an SSE stream or a one-shot JSON body: each field at the latest
 * value the API reported, never a sum.
 *
 * `message_start` carries input and cache counts plus a placeholder
 * `output_tokens`; `message_delta` carries `output_tokens` as a running total,
 * and the API now repeats input and cache there too — as totals, not increments.
 * So a later event's value replaces an earlier one, and a field an event leaves
 * out (or sends as null) keeps what it had: right for the old delta that carried
 * only `output_tokens` and for the current one that repeats everything. Summing
 * the two wrote input and cache at 2× and output one placeholder high; the rows
 * that did are told apart in docs/policy-and-trace.md, "Usage counting".
 */
export class UsageReader {
  usage: Usage = { input: 0, output: 0, cacheRead: 0, cacheCreation: 0 }
  stopReason: string | null = null
  messageId: string | null = null
  #buf = ""

  /** Feed raw SSE bytes; safe to call with partial events. */
  pushSSE(chunk: string): void {
    this.#buf += chunk
    const events = this.#buf.split("\n\n")
    this.#buf = events.pop() ?? ""
    for (const ev of events) this.#event(ev)
  }

  #event(ev: string): void {
    const line = ev.split("\n").find((l) => l.startsWith("data: "))
    if (!line) return
    try {
      const d = JSON.parse(line.slice(6)) as {
        type?: string
        message?: { id?: string; usage?: Record<string, unknown> }
        usage?: Record<string, unknown>
        delta?: { stop_reason?: string }
      }
      if (d.type === "message_start") {
        if (typeof d.message?.id === "string") this.messageId = d.message.id
        if (d.message?.usage) this.#take(d.message.usage)
      } else if (d.type === "message_delta") {
        if (d.usage) this.#take(d.usage)
        if (d.delta?.stop_reason) this.stopReason = d.delta.stop_reason
      }
    } catch {
      // A malformed event is upstream's problem, not a reason to drop the row.
    }
  }

  /** Non-streaming responses carry the whole thing at once. */
  pushJSON(body: Buffer): void {
    try {
      const d = JSON.parse(body.toString("utf8")) as {
        id?: string
        usage?: Record<string, unknown>
        stop_reason?: string
      }
      if (typeof d.id === "string") this.messageId = d.id
      if (d.usage) this.#take(d.usage)
      if (d.stop_reason) this.stopReason = d.stop_reason
    } catch {
      // Not JSON (an SSE body, or an upstream error page) — nothing to read.
    }
  }

  /** Each field the event reports replaces the one before; one it omits or nulls is kept. */
  #take(u: Record<string, unknown>): void {
    const at = (key: string, had: number) => (typeof u[key] === "number" ? (u[key] as number) : had)
    this.usage.input = at("input_tokens", this.usage.input)
    this.usage.output = at("output_tokens", this.usage.output)
    this.usage.cacheRead = at("cache_read_input_tokens", this.usage.cacheRead)
    this.usage.cacheCreation = at("cache_creation_input_tokens", this.usage.cacheCreation)
  }

  get total(): Usage {
    return this.usage
  }
}
