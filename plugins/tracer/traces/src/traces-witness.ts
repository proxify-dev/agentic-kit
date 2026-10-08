import { existsSync } from "node:fs"
import { join } from "node:path"
import * as feed from "./feed.ts"
import { projectDir } from "./brand.ts"
import { splitTool } from "./traces-blobs.ts"
import * as store from "./traces.ts"
import { reconcile } from "./traces-walk.ts"
import type { BenchSpan, TraceOrigin, TraceOutcome } from "./traces.types.ts"

/**
 * The live witnesses — the edge the transcript cannot be.
 *
 * The transcript is the durable record and it is authoritative, but it lands
 * when the turn lands. These four fill the gap between "the call started" and
 * "the file has it", and each one knows exactly one thing the others cannot:
 *
 *   **hook** — a row the MOMENT a call starts, from ANY session on this
 *   machine, app-run or not, with the turn it belongs to (`prompt_id`). It is
 *   the only witness that sees a PermissionDenied at all: a call that never ran
 *   writes no transcript line, so without this one a denial is invisible.
 *
 *   **sdk** — the runner's own message stream, which is the only place that
 *   knows an app run's identity: which agent record, which run id, which item.
 *   The transcript for an app-run session lands in <claudeHome>/projects like any
 *   other and cannot tell itself apart from a terminal session.
 *
 *   **inproc** — the kit tool wrapper. It ENRICHES and never creates, because
 *   an in-process MCP call carries no `tool_use_id`; a row it created would be
 *   a duplicate of the one the transcript is about to write under the real id.
 *   What it adds is the two things only it knows: the memory policy in force,
 *   and the ledger line the call produced.
 *
 *   **bench** — the one door with no transcript behind it. It writes its own
 *   span, and it is the only witness that stores payload blobs, because there
 *   is no file to point a byte offset at.
 *
 * Every entry point is wrapped. A witness that throws into the door it watches
 * has stopped observing and started steering, which is the one thing the feed's
 * whole design forbids.
 */

/** How long after the call a hook row waits for its transcript line. */
const RECONCILE_DEBOUNCE_MS = 800

let unsubscribe: (() => void) | null = null
const pending = new Map<string, ReturnType<typeof setTimeout>>()

/* ── hook ────────────────────────────────────────────────────────────────── */

const str = (v: unknown): string | null =>
  typeof v === "string" && v ? v : null

/**
 * Where a session's transcript lives, when the payload did not say.
 *
 * Claude Code slugs the cwd by turning every character but a-z A-Z 0-9 into
 * `-` (the seam's `projectDir`), so `<home>/.ak` becomes `-<home>--ak`.
 * `transcript_path` is preferred whenever the hook sends it, and a derived
 * path that does not exist is simply not reconciled.
 */
function transcriptPath(payload: Record<string, unknown>): string | null {
  const given = str(payload.transcript_path)
  if (given && existsSync(given)) return given
  const session = str(payload.session_id)
  const cwd = str(payload.cwd)
  if (!session || !cwd) return null
  const guess = join(projectDir(cwd), `${session}.jsonl`)
  return existsSync(guess) ? guess : null
}

/**
 * Walk the session's own transcript shortly after a call closes.
 *
 * This is what lets a hook row leave `blobs` null: the payloads are already on
 * disk in a file this app does not own, and an offset into it beats a copy of
 * it. Debounced per file because a busy turn closes a call every few hundred
 * milliseconds and the walk is incremental anyway.
 */
function scheduleReconcile(path: string | null): void {
  if (!path) return
  const armed = pending.get(path)
  if (armed) clearTimeout(armed)
  pending.set(
    path,
    setTimeout(() => {
      pending.delete(path)
      reconcile(path)
    }, RECONCILE_DEBOUNCE_MS)
  )
}

/** The message inside a PostToolUseFailure or a PermissionDenied payload. */
function failureText(payload: Record<string, unknown>): string {
  const candidate =
    str(payload.error) ??
    str(payload.message) ??
    str(payload.reason) ??
    str(
      (payload.tool_response as Record<string, unknown> | undefined)
        ?.error as string
    ) ??
    (typeof payload.tool_response === "string" ? payload.tool_response : null)
  return (candidate ?? "unreported").replace(/\s+/g, " ").slice(0, 400)
}

export function onHook(payload: Record<string, unknown>): void {
  try {
    const event = str(payload.hook_event_name)
    const id = str(payload.tool_use_id)
    const name = str(payload.tool_name)
    if (!event || !id || !name) return

    const { server, tool } = splitTool(name)
    const identity = {
      session: str(payload.session_id),
      prompt: str(payload.prompt_id) ?? str(payload.promptId),
      cwd: str(payload.cwd),
      agent: str(payload.agent_type),
    }
    const now = new Date().toISOString()
    // No transcript for this session on disk means no offsets are coming, so
    // this row keeps its own redacted copies instead of pointing at nothing.
    const path = transcriptPath(payload)
    const keepBlobs = !path

    if (event === "PreToolUse") {
      store.record({
        id,
        witness: "hook",
        ts: now,
        server,
        tool,
        origin: "session",
        outcome: "running",
        identity,
        input: payload.tool_input,
        store: keepBlobs,
      })
      return
    }

    const closing: Record<string, TraceOutcome> = {
      PostToolUse: "ok",
      PostToolUseFailure: "error",
      PermissionDenied: "denied",
    }
    const outcome = closing[event]
    if (!outcome) return

    store.record({
      id,
      witness: "hook",
      ts: now,
      endTs: now,
      server,
      tool,
      origin: "session",
      outcome,
      identity,
      // PermissionDenied never ran, so its input is the only payload there is.
      input: payload.tool_input,
      ...(outcome === "ok"
        ? { output: payload.tool_response }
        : { error: failureText(payload) }),
      store: keepBlobs,
    })
    scheduleReconcile(path)
  } catch (err) {
    console.error(`[traces] hook witness failed: ${(err as Error).message}`)
  }
}

/* ── sdk (the runner's message stream) ───────────────────────────────────── */

export type RunIdentity = {
  run: string
  origin: "chat" | "bench" | "automation"
  agent: string | null
  session: string | null
  cwd: string | null
  item: string | null
}

/** A chat IS an agent run from the substrate's point of view — "chat" is the
 *  app's word for who started it, "agent" is the trace's word for which door. */
const ORIGIN: Record<RunIdentity["origin"], TraceOrigin> = {
  chat: "agent",
  automation: "automation",
  bench: "bench",
}

type Block = {
  type?: string
  id?: string
  name?: string
  input?: unknown
  tool_use_id?: string
  content?: unknown
  is_error?: boolean
}

/**
 * One SDK message, from `runner.emit`. Cheap by construction: two type checks
 * and a loop over at most a handful of blocks, because emit() runs on every
 * token batch of every live run.
 */
export function onSdk(msg: unknown, who: RunIdentity): void {
  try {
    const m = msg as { type?: string; message?: { content?: unknown } }
    if (m.type !== "assistant" && m.type !== "user") return
    const content = m.message?.content
    if (!Array.isArray(content)) return
    const identity = {
      session: who.session,
      cwd: who.cwd,
      agent: who.agent,
      run: who.run,
      item: who.item,
    }
    for (const b of content as Block[]) {
      if (m.type === "assistant" && b.type === "tool_use" && b.id && b.name) {
        const { server, tool } = splitTool(b.name)
        store.record({
          id: b.id,
          witness: "sdk",
          ts: new Date().toISOString(),
          server,
          tool,
          origin: ORIGIN[who.origin],
          outcome: "running",
          identity,
          input: b.input,
        })
      } else if (
        m.type === "user" &&
        b.type === "tool_result" &&
        b.tool_use_id
      ) {
        store.record({
          id: b.tool_use_id,
          witness: "sdk",
          endTs: new Date().toISOString(),
          outcome: b.is_error ? "error" : "ok",
          origin: ORIGIN[who.origin],
          identity,
          output: b.content,
        })
      }
    }
  } catch (err) {
    console.error(`[traces] sdk witness failed: ${(err as Error).message}`)
  }
}

/* ── inproc (the kit tool wrapper) ─────────────────────────────────────── */

/** How far back the inproc witness looks for the row another witness made for
 *  the call it just served. A tool that took longer than this to return has a
 *  row whose `policy` is worth less than a wrong match would cost. */
const MATCH_WINDOW_MS = 10_000

/**
 * Enrich the row for the kit call that just returned.
 *
 * Matched on (session, tool, within ten seconds) because an in-process MCP call
 * never sees the `tool_use_id` the model used — the SDK keeps it on the wire.
 * It cannot create, so the worst a bad match can do is put a policy on the
 * previous call to the same tool in the same session, and it never recurses:
 * `trace_*` is excluded by name, and `enrich` is an UPDATE that emits nothing.
 */
export function onInproc(args: {
  session: string | null
  tool: string
  policy?: string
  ledger?: string
}): void {
  try {
    if (!args.session || args.tool.startsWith("trace_")) return
    const id = store.findRecent({
      session: args.session,
      tool: args.tool,
      windowMs: MATCH_WINDOW_MS,
    })
    if (!id) return
    store.enrich(id, {
      ...(args.policy ? { policy: args.policy } : {}),
      ...(args.ledger ? { ledger: args.ledger } : {}),
    })
  } catch (err) {
    console.error(`[traces] inproc witness failed: ${(err as Error).message}`)
  }
}

/* ── bench ───────────────────────────────────────────────────────────────── */

const OUTCOMES = new Set<string>([
  "ok",
  "error",
  "refused",
  "denied",
  "running",
])

/** The bench calls twice with the same id: `running`, then `done` or `error`.
 *  The one door that stores blobs, because there is no transcript to offset. */
export function onBench(span: BenchSpan): string {
  try {
    if (span.status === "running") {
      store.record({
        id: span.id,
        witness: "bench",
        ts: new Date().toISOString(),
        server: span.server,
        tool: span.tool,
        origin: "bench",
        outcome: "running",
        identity: {
          session: null,
          prompt: null,
          cwd: null,
          agent: null,
          run: span.id,
          item: null,
        },
        input: span.input,
        store: true,
        ...(span.policy ? { policy: span.policy } : {}),
      })
      return span.id
    }
    const outcome = (
      span.outcome && OUTCOMES.has(span.outcome)
        ? span.outcome
        : span.status === "error"
          ? "error"
          : "ok"
    ) as TraceOutcome
    store.record({
      id: span.id,
      witness: "bench",
      endTs: new Date().toISOString(),
      server: span.server,
      tool: span.tool,
      origin: "bench",
      outcome,
      output: span.output,
      store: true,
      ...(span.policy ? { policy: span.policy } : {}),
      ...(outcome === "error"
        ? { error: String(span.output ?? "failed").slice(0, 400) }
        : {}),
    })
    return span.id
  } catch (err) {
    console.error(`[traces] bench witness failed: ${(err as Error).message}`)
    return span.id
  }
}

/* ── arming ──────────────────────────────────────────────────────────────── */

export function start(): void {
  if (unsubscribe) return
  unsubscribe = feed.subscribeRaw(onHook)
}

export function stop(): void {
  unsubscribe?.()
  unsubscribe = null
  for (const t of pending.values()) clearTimeout(t)
  pending.clear()
}
