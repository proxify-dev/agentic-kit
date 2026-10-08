import { createHash } from "node:crypto"
import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { renameRetrySync } from "./rename.ts"

/**
 * Payloads, and what is safe to keep of them.
 *
 * Split out of traces.ts because it is the half with no database in it: what a
 * secret looks like, what one line of summary is, where a blob lives, and how
 * to read ONE line out of an 8 MB JSONL without parsing the file. The store
 * calls in; nothing here calls back, so the two files cannot form a cycle.
 *
 * The privacy rule lives here and it runs before anything is written: a key is
 * redacted before it is hashed and before it is summarised, so no unredacted
 * byte this app wrote reaches disk. It also runs on the way OUT of a
 * transcript, which is not ours to write but IS read by `ak trace get` —
 * a verb an agent runs, and handing it a credential it did not otherwise
 * have is the risk the whole pass exists for.
 */

const SUMMARY_CAP = 160
/** A transcript line can be a 1.2 MB base64 screenshot; past this we stop. */
export const LINE_CAP = 8 * 1024 * 1024
/** Blobs age out; rows never do. A row is ~300 bytes and answers a question; a
 *  blob is a payload and mostly does not get read twice. */
const BLOB_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000
const BLOB_MAX_BYTES = 200 * 1024 * 1024

/* ── redaction ───────────────────────────────────────────────────────────── */

const SECRET_KEY = /key|token|secret|password|authorization|cookie/i
/** Prefixes that are a credential whatever their entropy says. */
const SECRET_SHAPE =
  /^(sk-|ghp_|gho_|ghs_|github_pat_|xox[abprs]-|AKIA|ASIA|eyJ[A-Za-z0-9_-]{10,}\.)/

function entropy(s: string): number {
  const freq = new Map<string, number>()
  for (const c of s) freq.set(c, (freq.get(c) ?? 0) + 1)
  let h = 0
  for (const n of freq.values()) {
    const p = n / s.length
    h -= p * Math.log2(p)
  }
  return h
}

/**
 * A long string that is ONE token, is not a path or a URL, and carries
 * near-random characters.
 *
 * Paths and URLs are excluded deliberately, and the exclusion costs a real
 * false negative: base64 containing a slash survives. That trade is the right
 * way round — a trace whose every file path reads `[redacted:97 chars]` is a
 * feature nobody can use, while a key under a key-shaped NAME is caught by the
 * key rule and the known credential prefixes are caught outright.
 */
function looksSecret(s: string): boolean {
  if (s.length <= 64) return false
  if (SECRET_SHAPE.test(s)) return true
  if (/\s/.test(s)) return false
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) return false
  if (s.includes("/") || s.includes("\\")) return false
  return entropy(s) >= 3.6
}

/** Past this a serialised payload is not worth parsing to look inside it; the
 *  key-name rule and the entropy rule still apply to it as a string. */
const NESTED_JSON_CAP = 256 * 1024

/**
 * Redaction descends THROUGH serialisation.
 *
 * A tool result arrives as `[{type:"text", text:"{\"api_key\":\"sk-…\"}"}]` —
 * the credential is inside a string, so to a key-walking pass it is not a key
 * at all, and to the entropy rule the whole envelope is one long token. Both
 * answers are wrong: the first leaks it into `get()` and the detail view, the
 * second replaced every kit result with `[redacted:124 chars]`.
 *
 * So a string that parses as JSON is redacted as the STRUCTURE it is and
 * re-serialised. The bytes change, which is fine — this is the redacted view
 * by definition, and `shape` already records the original size, so nothing
 * downstream is lied to about how big the payload was.
 */
export function redact(v: unknown, depth = 0): unknown {
  if (typeof v === "string") {
    const t = v.trim()
    if (
      (t.startsWith("{") || t.startsWith("[")) &&
      t.length <= NESTED_JSON_CAP &&
      depth < 8
    ) {
      try {
        return JSON.stringify(redact(JSON.parse(t), depth + 1))
      } catch {
        /* a line that merely starts with a brace — fall through to the rules */
      }
    }
    return looksSecret(v) ? `[redacted:${v.length} chars]` : v
  }
  if (v === null || typeof v !== "object") return v
  if (depth >= 8) return "…"
  if (Array.isArray(v)) return v.slice(0, 200).map((x) => redact(x, depth + 1))
  const out: Record<string, unknown> = {}
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    // A NUMBER under a credential-shaped key is a count, not a credential:
    // `tokens: 412`, `max_tokens: 32`, `input_tokens`, `key: 3`. Redacting
    // those was a measured false positive — it turned recall's own
    // "412 tokens" into NaN — and no credential this pass can protect is a
    // number. Strings (and anything structured) still go.
    const numeric = typeof val === "number" || typeof val === "boolean"
    out[k] =
      SECRET_KEY.test(k) && !numeric ? "[redacted]" : redact(val, depth + 1)
  }
  return out
}

/* ── summaries ───────────────────────────────────────────────────────────── */

const oneLine = (s: string): string => s.replace(/\s+/g, " ").trim()
const clip = (s: string, n: number): string =>
  s.length > n ? `${s.slice(0, n - 1)}…` : s

/**
 * A value inside a summary line.
 *
 * A nested object keeps its KEY NAMES rather than collapsing to a count,
 * because two calls of the same tool differ by which field changed, and
 * `{2}` says nothing about which. Clipped hard, since this is one field of a
 * line that is itself capped.
 */
function scalar(v: unknown): string {
  if (typeof v === "string") return oneLine(v)
  if (v === null || v === undefined) return String(v)
  if (typeof v === "object") return clip(JSON.stringify(v), 40)
  return String(v)
}

/** One line, 160 chars, built from the REDACTED payload — it is an index
 *  column, not a payload, so it has to be safe to show anywhere. */
export function summarize(v: unknown): string {
  let s: string
  if (v === null || v === undefined) s = ""
  else if (typeof v === "string") s = oneLine(v)
  else if (Array.isArray(v)) s = v.map(scalar).join(", ")
  else if (typeof v === "object")
    s = `{${Object.entries(v as Record<string, unknown>)
      .slice(0, 12)
      .map(([k, val]) => `${k}:${scalar(val)}`)
      .join(", ")}}`
  else s = String(v)
  return s.length > SUMMARY_CAP ? `${s.slice(0, SUMMARY_CAP - 1)}…` : s
}

/* ── the call as one line ────────────────────────────────────────────────── */

const stringField = (o: Record<string, unknown>, key: string): string =>
  typeof o[key] === "string" ? oneLine(o[key] as string) : ""

const tilde = (p: string): string => {
  const home = homedir()
  return p === home || p.startsWith(home + "/") ? "~" + p.slice(home.length) : p
}

/**
 * What the call was for, as the person saw it in Claude Code — `summary.label`.
 *
 * `summarize()` serialises the arguments, and for a Bash call that is
 * `{command:cd <your repo>…` on every row: the line that says what the call was
 * FOR is `description`, which the model writes before the call runs and
 * Claude Code draws as the row. It was in every payload and cut off at
 * `descriptio…`. First match wins:
 *
 *   description         the model's own line (Bash, Monitor, Agent)
 *   command             "$ " + the command — the "$" marks it raw, not intent
 *   file_path · notebook_path
 *   pattern [in path]   Grep quotes it
 *   skill [args]
 *   url · query · subject · prompt · name · title
 *
 * src/tracer/step_label.py is the same rule for the transcript readers;
 * tests/step_label_cases.json holds both to it. Built from the REDACTED
 * payload, like every summary line.
 */
export function stepLabel(tool: string, v: unknown): string {
  if (v === null || typeof v !== "object" || Array.isArray(v)) return ""
  const o = v as Record<string, unknown>
  const line = (s: string): string => clip(s, SUMMARY_CAP)
  const desc = stringField(o, "description")
  if (desc) return line(desc)
  const cmd = stringField(o, "command")
  if (cmd) return line(`$ ${cmd}`)
  for (const key of ["file_path", "notebook_path"]) {
    const p = stringField(o, key)
    if (p) return line(tilde(p))
  }
  const pattern = stringField(o, "pattern")
  if (pattern) {
    const where = stringField(o, "path")
    const shown = tool === "Grep" ? `'${pattern}'` : pattern
    return line(shown + (where ? ` in ${tilde(where)}` : ""))
  }
  const skill = stringField(o, "skill")
  if (skill) {
    const args = stringField(o, "args")
    return line(skill + (args ? ` ${args}` : ""))
  }
  for (const key of ["url", "query", "subject", "prompt", "name", "title"]) {
    const t = stringField(o, key)
    if (t) return line(t)
  }
  return ""
}

/* ── what came back ──────────────────────────────────────────────────────── */

/**
 * The OUTPUT summary, read by shape rather than serialised.
 *
 * `summarize()` is right for an input: a tool's arguments are a flat object and
 * `{k:v, k:v}` is the most useful thing a line can be. An output is not that.
 * It arrives in four shapes, measured against real transcripts on 2026-09-12,
 * and serialising any of them produces a line nobody can read:
 *
 *   [{type:"text", text:"…"}]          most tools — a content array, which
 *                                      serialised reads `{2}`
 *   [{type:"text", text:"{json}"}]     the kit — a ToolResult inside a text
 *                                      block, which reads as 300 chars of JSON
 *   "plain text"                       Bash, Read — already a line
 *   "MCP error -32602: …"              a protocol failure — already a line
 *
 * So: unwrap the envelope, and if what falls out is a result this app knows the
 * grammar of, say what HAPPENED instead of what was returned. The register
 * mirrors `brainOutcome`/`hostOutcome` in src/features/tools/shapes.ts, which
 * the renderer has drawn with since the playground — one vocabulary, two
 * places, and this is the one that reaches the store.
 *
 * **Redaction happens at the LEAF, after the shape is read.** A JSON string is
 * opaque to `redact()` twice over: a key inside it is not a key yet, and the
 * string itself is long, whitespace-free and high-entropy, so redacting the
 * payload first classified every kit result as one secret and produced
 * `[redacted:124 chars]`. Unwrapping first and redacting the parsed structure
 * gets both — `api_key` still becomes `[redacted]`, and the line is readable.
 * Every branch below that produces text passes through `redact()`; if one ever
 * does not, a credential reaches an index column.
 */
type Envelope = Record<string, unknown>

const textOf = (v: unknown): string | null => {
  if (!Array.isArray(v)) return null
  for (const raw of v) {
    const b = (raw ?? {}) as { type?: string; text?: string }
    if (b.type === "text" && typeof b.text === "string") return b.text
    if (b.type === "image" || b.type === "audio") return "media"
  }
  return null
}

/**
 * Claude Code's own `tool_response` envelope, which ONLY the hook witness sees.
 *
 * A transcript records a Bash result as the text the command printed. A hook
 * records the same call as `{stdout, stderr, interrupted, isImage,
 * noOutputExpected}` — so without this the two witnesses summarise one call two
 * different ways, and which spelling you got depended on which witness happened
 * to arrive first. Unwrapping to the same text is what makes them agree.
 *
 * Bash alone is 45,000 spans in a real store, more than every MCP server
 * combined, so this is the common case rather than a special one.
 */
function builtinLine(o: Envelope): string | null {
  if (typeof o.stdout !== "string" && typeof o.stderr !== "string") return null
  const out = String(o.stdout ?? "").trim()
  const err = String(o.stderr ?? "").trim()
  if (!out && !err) return "no output"
  // stderr is named when it is all there is; a command that printed both is
  // reported by its stdout, which is what the reader came for.
  return out ? out : `stderr · ${err}`
}

/** The kit's own result grammar: what the door answered, in its own words. */
function brainLine(o: Envelope): string | null {
  switch (o.kind) {
    case "refused": {
      const reason = ((o.refusal ?? {}) as { reason?: string }).reason
      return `refused · ${oneLine(reason ?? "no reason given")}`
    }
    case "recall":
      return `${(o.rows as unknown[])?.length ?? 0} recalled · ${Number(o.tokens ?? 0)} tokens`
    case "list":
      return `${(o.rows as unknown[])?.length ?? 0} of ${Number(o.total ?? 0)}`
    case "note":
      return String(o.stem ?? o.path ?? "note")
    case "object": {
      const obj = (o.object ?? {}) as {
        kind?: string
        path?: string
        stem?: string
      }
      const what = obj.path ?? obj.stem
      if (!what) return "written"
      return `${obj.kind === "proposal" ? "proposed" : "wrote"} ${what}`
    }
    // The trace family answers on this server too, so its shapes belong here.
    case "traces":
      return `${(o.rows as unknown[])?.length ?? 0} rows`
    case "trace":
      return String(((o.row ?? {}) as { tool?: string }).tool ?? "trace")
    case "purged":
      return `${Number(o.removed ?? 0)} removed`
    default:
      return null
  }
}

export type OutputLine = {
  line: string
  /** A kit REFUSAL, which arrives over the wire as an error and is not one —
   *  the ladder looked at the write and said no, for a reason. The store
   *  upgrades the outcome on the strength of this flag. */
  refused: boolean
}

export function classifyOutput(v: unknown, depth = 0): OutputLine {
  if (v === null || v === undefined) return { line: "", refused: false }
  if (depth > 3) return { line: summarize(redact(v)), refused: false }

  // A content array: unwrap to the block that carries the answer.
  const text = textOf(v)
  if (text !== null) return classifyOutput(text, depth + 1)

  if (typeof v === "string") {
    const t = v.trim()
    if (t.startsWith("{") || t.startsWith("[")) {
      try {
        return classifyOutput(JSON.parse(t), depth + 1)
      } catch {
        /* not JSON after all — it is just a line that starts with a brace */
      }
    }
    return {
      line: clip(oneLine(String(redact(v))), SUMMARY_CAP),
      refused: false,
    }
  }

  if (Array.isArray(v)) {
    if (v.length && v.every((r) => r && typeof r === "object"))
      return {
        line: `${v.length} ${v.length === 1 ? "row" : "rows"}`,
        refused: false,
      }
    return { line: summarize(redact(v)), refused: false }
  }

  if (typeof v === "object") {
    const envelope = builtinLine(v as Envelope)
    if (envelope !== null)
      return { line: clip(oneLine(envelope), SUMMARY_CAP), refused: false }
    // Redacted HERE rather than by the caller, and that ordering is the whole
    // trick: a compact JSON envelope is one long string with no whitespace and
    // high entropy, so redacting the payload BEFORE reading its shape turned
    // every kit result into `[redacted:124 chars]` — correct about privacy
    // and useless as a summary. Unwrap first, redact the structure, then read.
    const o = redact(v) as Envelope
    const refusal = brainLine(o)
    if (refusal)
      return { line: clip(refusal, SUMMARY_CAP), refused: o.kind === "refused" }
    // An MCP host result: `{ content: [...], isError? }`.
    if (Array.isArray(o.content)) {
      const inner = classifyOutput(o.content, depth + 1)
      return {
        line: o.isError
          ? clip(`error · ${inner.line}`, SUMMARY_CAP)
          : inner.line,
        refused: inner.refused,
      }
    }
    return { line: summarize(o), refused: false }
  }
  return { line: summarize(v), refused: false }
}

/** `mcp__GitHub__search_code` → GitHub / search_code. A built-in is "claude",
 *  so the substrate keeps Bash and Read next to every MCP call and the fold is
 *  what filters. Split at the FIRST `__` after the prefix: a server name never
 *  contains one, a tool name might. */
export function splitTool(name: string): { server: string; tool: string } {
  if (!name.startsWith("mcp__")) return { server: "claude", tool: name }
  const rest = name.slice(5)
  const cut = rest.indexOf("__")
  if (cut < 0) return { server: "claude", tool: name }
  return { server: rest.slice(0, cut), tool: rest.slice(cut + 2) }
}

/* ── blobs ───────────────────────────────────────────────────────────────── */

const blobDir = (dir: string) => join(dir, "blobs")

export function writeBlob(dir: string, value: unknown): string | null {
  try {
    const body = JSON.stringify(value ?? null)
    const hash = createHash("sha256").update(body).digest("hex")
    const folder = join(blobDir(dir), hash.slice(0, 2))
    const file = join(folder, `${hash}.json`)
    if (!existsSync(file)) {
      mkdirSync(folder, { recursive: true })
      const tmp = `${file}.${process.pid}.tmp`
      writeFileSync(tmp, body, "utf8")
      renameRetrySync(tmp, file)
    }
    return `sha256:${hash}`
  } catch (err) {
    console.error(`[traces] blob write failed: ${(err as Error).message}`)
    return null
  }
}

/** One JSONL line, read at its byte offset — never a full-file parse. */
export function readLineAt(path: string, offset: number): unknown {
  let fd = -1
  try {
    fd = openSync(path, "r")
    const chunks: Buffer[] = []
    const buf = Buffer.alloc(64 * 1024)
    let pos = offset
    let total = 0
    for (;;) {
      const n = readSync(fd, buf, 0, buf.length, pos)
      if (n <= 0) break
      const nl = buf.subarray(0, n).indexOf(0x0a)
      if (nl >= 0) {
        chunks.push(Buffer.from(buf.subarray(0, nl)))
        break
      }
      chunks.push(Buffer.from(buf.subarray(0, n)))
      pos += n
      total += n
      if (total > LINE_CAP) return null
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown
  } catch {
    return null
  } finally {
    if (fd >= 0) closeSync(fd)
  }
}

type Block = {
  type?: string
  id?: string
  tool_use_id?: string
  input?: unknown
  content?: unknown
}

/** Pull THIS span's block out of the transcript line it shares with others —
 *  one assistant line can carry three tool_use blocks and only one is this row. */
function pickBlock(
  line: unknown,
  id: string,
  side: "input" | "output"
): unknown {
  const content = (line as { message?: { content?: unknown } } | null)?.message
    ?.content
  if (!Array.isArray(content)) return null
  for (const b of content as Block[]) {
    if (side === "input" && b.type === "tool_use" && b.id === id)
      return b.input ?? null
    if (side === "output" && b.type === "tool_result" && b.tool_use_id === id)
      return b.content ?? null
  }
  return null
}

/** Resolve one `blobs` ref — both spellings, both redacted on the way out. */
export function readBlob(
  dir: string,
  ref: string | null,
  id: string,
  side: "input" | "output"
): unknown {
  if (!ref) return null
  try {
    if (ref.startsWith("sha256:")) {
      const hash = ref.slice(7)
      if (!/^[0-9a-f]{64}$/.test(hash)) return null
      const file = join(blobDir(dir), hash.slice(0, 2), `${hash}.json`)
      if (!existsSync(file)) return null
      const size = statSync(file).size
      const fd = openSync(file, "r")
      try {
        const buf = Buffer.alloc(Math.min(size, LINE_CAP))
        readSync(fd, buf, 0, buf.length, 0)
        return JSON.parse(buf.toString("utf8")) as unknown
      } finally {
        closeSync(fd)
      }
    }
    const cut = ref.lastIndexOf(":")
    if (cut < 0) return null
    const offset = Number(ref.slice(cut + 1))
    if (!Number.isFinite(offset)) return null
    return redact(pickBlock(readLineAt(ref.slice(0, cut), offset), id, side))
  } catch (err) {
    console.error(`[traces] blob read failed: ${(err as Error).message}`)
    return null
  }
}

/**
 * Retention: blobs older than 30 days, then oldest-first past 200 MB. Rows stay.
 *
 * A row is an answer; a blob is a payload nobody reads twice. Dropping the blob
 * keeps the history and gives back the disk, which is the right way round. The
 * dangling reference is cleared through `clearRef` in the same pass, so `get()`
 * never promises a payload that is gone. A transcript offset is never touched:
 * that file belongs to Claude Code and this service is not a second writer of
 * it, today or ever.
 */
export function sweepBlobs(
  dir: string,
  clearRef: (hash: string) => void
): { removed: number; bytes: number } {
  let removed = 0
  let freed = 0
  try {
    const root = blobDir(dir)
    if (!existsSync(root)) return { removed, bytes: freed }
    const files: { path: string; hash: string; mtime: number; size: number }[] =
      []
    for (const shard of readdirSync(root)) {
      let names: string[]
      try {
        names = readdirSync(join(root, shard))
      } catch {
        continue
      }
      for (const name of names) {
        const path = join(root, shard, name)
        try {
          const st = statSync(path)
          files.push({
            path,
            hash: name.replace(/\.json$/, ""),
            mtime: st.mtimeMs,
            size: st.size,
          })
        } catch {
          /* vanished between readdir and stat — nothing to sweep */
        }
      }
    }
    files.sort((a, b) => a.mtime - b.mtime)
    let total = files.reduce((n, f) => n + f.size, 0)
    const cutoff = Date.now() - BLOB_MAX_AGE_MS
    for (const f of files) {
      if (f.mtime >= cutoff && total <= BLOB_MAX_BYTES) break
      rmSync(f.path, { force: true })
      total -= f.size
      removed++
      freed += f.size
      clearRef(f.hash)
    }
  } catch (err) {
    console.error(`[traces] blob sweep failed: ${(err as Error).message}`)
  }
  return { removed, bytes: freed }
}
