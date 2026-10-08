/**
 * The span substrate's contract — slice one is every tool invocation.
 *
 * These types are shared by five things that must not be able to disagree: the
 * store, the three live witnesses, the transcript walker, the MCP tools and the
 * renderer across the preload bridge. They live here rather than in traces.ts
 * because the renderer imports the shapes and must never import the store.
 *
 * The row is a SPAN with a `kind` column, not a tool row. Slice one writes only
 * `kind: "tool"`, but `kind`, `parent` and `identity.prompt` are on the row from
 * day one so the gateway's model calls, hook runs and ledger lines can join
 * later without a migration. `blobs` is shared across kinds for the same reason.
 *
 * The durable witness is the TRANSCRIPT. Every session Claude Code has ever run
 * on this machine already holds its tool spans on disk, with per-line timestamps
 * and both payloads, so this store is an INDEX over what exists plus a live
 * edge — not a recorder that starts empty. That is why `blobs` carries two
 * spellings: a content-addressed hash for the one door with no transcript (the
 * bench), and a byte offset into a JSONL for everything else, so nothing is
 * copied and no unredacted byte is written twice.
 */

/** Slice one. "model" (a gateway request), "hook" and "write" join later. */
export type TraceKind = "tool"

/** Which door the call came through. */
export type TraceOrigin = "session" | "agent" | "automation" | "bench"

/** `running` is not a failure — it is a row whose closing witness has not
 *  arrived yet. `refused` is the vault's own semantic no; `denied` is a person
 *  or a permission rule saying no before the tool ran. */
export type TraceOutcome = "ok" | "error" | "refused" | "denied" | "running"

/** Who saw the call. One row can carry several, in arrival order. */
export type TraceWitness = "transcript" | "hook" | "inproc" | "sdk" | "bench"

export type TraceRow = {
  /** tool_use_id when any witness has one; bench rows use the id the renderer
   *  generated. One row per id — every witness upserts against it. */
  id: string
  kind: TraceKind
  /** The parent tool_use_id when this call ran inside a sub-agent; null
   *  otherwise. Derived from the sub-agent transcript's Agent call. */
  parent: string | null
  /** ISO, the FIRST witness's clock — a later witness never moves it. */
  ts: string
  /** null while running. */
  durMs: number | null
  /** "vault", "GitHub", … parsed from `mcp__<server>__<tool>`; "claude" for the
   *  built-in tools (Bash, Read, Agent…). The substrate keeps them all; the
   *  Tools fold is what filters by server. */
  server: string
  /** The tool's own name, without the `mcp__<server>__` prefix. */
  tool: string
  origin: TraceOrigin
  /** `prompt` is the hook payload's prompt_id: the TURN, and the join key for
   *  replay. `run` and `item` are the app's own run identity. */
  identity: {
    session: string | null
    prompt: string | null
    cwd: string | null
    agent: string | null
    run: string | null
    item: string | null
  }
  outcome: TraceOutcome
  /** One line each, at most 160 chars, built from the REDACTED payload — so a
   *  secret cannot reach the index column even when the blob is an offset into
   *  a transcript this app does not own. `label` is the call as one line — its
   *  description, else `$ command`, a path, a pattern (traces-blobs.ts
   *  `stepLabel`); absent on a row a v4 build wrote until the re-read reaches it. */
  summary: { input: string; output: string; label?: string }
  /** `"sha256:<hex>"` for a stored blob, or `"<absolute jsonl path>:<byte
   *  offset of the line>"` for a transcript line. null when unresolved. */
  blobs: { input: string | null; output: string | null }
  /** Bytes of the ORIGINAL payload, not the redacted one — the honest size. */
  shape: { bytesIn: number; bytesOut: number }
  /** Arrival order. First witness creates the row; the rest fill nulls. */
  witnesses: TraceWitness[]
  /** vault server only: the memory policy in force at the call. */
  policy?: string
  /** vault server only: `"<at> <op> <stem>"` of the ledger line it produced. */
  ledger?: string
  error?: string
}

export type TraceFilter = {
  server?: string
  tool?: string
  session?: string
  origin?: TraceOrigin
  outcome?: TraceOutcome
  /** ISO instants, inclusive lower / exclusive upper. */
  since?: string
  until?: string
  /** Matches the tool name and both summary lines. */
  q?: string
  limit?: number
  /** From a previous page's `next`. Opaque: `<ts>|<id>`. */
  cursor?: string
}

export type TracePage = { rows: TraceRow[]; next: string | null }

/** The row with its payloads resolved — a blob read, or a transcript line read
 *  at its byte offset and the matching block picked out of it. */
export type TraceDetail = { row: TraceRow; input: unknown; output: unknown }

export type TraceStats = {
  total: number
  today: number
  errors: number
  running: number
  lastAt: string | null
  /** Call count per tool, newest window not applied — the whole store. */
  tools: Record<string, number>
}

/** What the bench sends over `traces:record`, twice per run: once `running`,
 *  once `done` or `error`. The one door with no transcript behind it. */
export type BenchSpan = {
  id: string
  server: string
  tool: string
  input: Record<string, unknown>
  status: "running" | "done" | "error"
  outcome?: string
  output?: unknown
  policy?: string
}

/**
 * What a witness hands `record()`. Everything but `id` and `witness` is
 * optional, because the whole point of four witnesses is that each one knows a
 * different part of the same call.
 */
export type TraceInput = {
  id: string
  witness: TraceWitness
  kind?: TraceKind
  parent?: string | null
  /** ISO. Only the first witness's value is kept. */
  ts?: string
  /** ISO. The store subtracts the stored `ts` to get `durMs` — a closing
   *  witness knows when the call ENDED, never how long it took. */
  endTs?: string
  durMs?: number | null
  server?: string
  tool?: string
  origin?: TraceOrigin
  identity?: Partial<TraceRow["identity"]>
  outcome?: TraceOutcome
  /** The raw payloads. Redacted before they are summarised or hashed. */
  input?: unknown
  output?: unknown
  /**
   * True payload sizes, when the caller passed a TRIMMED payload above.
   *
   * The walker does exactly that: a tool_result can be a 1.2 MB base64
   * screenshot, and summarising 3 GB of them in full would spend the whole
   * backfill on strings nobody reads. It trims what it hands over and reports
   * the real byte count here, so `shape` stays honest.
   */
  bytes?: { in?: number; out?: number }
  /** Pre-resolved refs — the walker's `<path>:<offset>` pairs. */
  blobs?: Partial<TraceRow["blobs"]>
  /** Write redacted copies of `input`/`output` as content-addressed blobs.
   *  The bench sets it; the transcript witnesses never do. */
  store?: boolean
  /** The transcript this span was read from, so a parent resolved on a later
   *  pass can be written back onto every span the file produced. */
  file?: string
  policy?: string
  ledger?: string
  error?: string
}
