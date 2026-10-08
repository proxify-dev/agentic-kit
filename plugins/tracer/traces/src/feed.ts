import { createServer, type Server } from "node:http"
import { env } from "./brand.ts"

/**
 * The feed — live hook events from ANY Claude Code session on this machine.
 *
 * The sender is the feed plugin (~/agentic-kit/plugins/feed, installed
 * at user scope as feed@ak): one async hook script that POSTs each
 * event here as it happens, from ANY project. This is the one intentional
 * break in "the process boundary IS the API boundary": an outside process must
 * be able to reach us, and hooks were chosen over telemetry precisely because
 * they push per-event in milliseconds instead of exporting batches.
 *
 * Observe-only by construction. The 200 `{}` reply carries no decision —
 * this port can watch a session but never steer one.
 *
 * Why a script and not a `type: http` hook (the first attempt, 2026-09-01):
 * `async: true` only means the hook does not BLOCK the turn. With the app
 * closed, nothing bound 4577 and Claude Code printed
 *   `Stop hook error: connect ECONNREFUSED 127.0.0.1:4577`
 * on every single turn — so the hooks got disabled, then deleted. send.sh
 * swallows the refused connection and exits 0; a closed app costs nothing.
 * Whether the sender is present, enabled and current is the doctor's job
 * (services/doctor.ts).
 */

export type FeedEvent = {
  seq: number
  /** Listener clock, ms epoch — ordering is ours, not the sender's. */
  at: number
  /** hook_event_name: SessionStart, PreToolUse, PostToolUse, Stop, ... */
  event: string
  session: string
  cwd: string
  /** tool_name when the event has one — "Bash", "Skill", "mcp__server__tool". */
  tool?: string
  /** The hook payload, clipped: long strings truncated, depth capped. */
  detail: Record<string, unknown>
}

const MAX_EVENTS = 1000
const MAX_BODY = 5 * 1024 * 1024
const PORT = Number(env("FEED_PORT")) || 4577

const buffer: FeedEvent[] = []
let seq = 0
let server: Server | null = null
let bound = 0
let notify: ((e: FeedEvent) => void) | undefined
/** In-process subscribers (the automations scheduler). The window push above
 *  stays a single callback; this is for services that want the same stream. */
const listeners = new Set<(e: FeedEvent) => void>()
/**
 * The same stream BEFORE `clip()` — the hook payload exactly as it arrived.
 *
 * The feed's own event is deliberately lossy: strings truncated at 600 chars,
 * depth capped, so a pane can render a thousand of them. The traces witness
 * needs the opposite thing: whole `tool_input` and `tool_response` values, to
 * size the call and summarise it honestly. Rather than raise the feed's clip
 * for everyone (and pay it on every event in every pane), the raw payload is
 * offered on its own channel and only the witness subscribes.
 *
 * Read-only by contract: a subscriber here is handed the parsed body and must
 * not mutate it, because the clipped event is built from the same object.
 */
const rawListeners = new Set<(payload: Record<string, unknown>) => void>()

export function subscribe(fn: (e: FeedEvent) => void): () => void {
  listeners.add(fn)
  return () => void listeners.delete(fn)
}

export function subscribeRaw(fn: (payload: Record<string, unknown>) => void): () => void {
  rawListeners.add(fn)
  return () => void rawListeners.delete(fn)
}

/** Payloads carry whole tool responses; the feed needs shapes, not contents. */
function clip(v: unknown, depth = 0): unknown {
  if (typeof v === "string") return v.length > 600 ? `${v.slice(0, 600)}…` : v
  if (v === null || typeof v !== "object" || depth >= 4) {
    return typeof v === "object" && v !== null ? "…" : v
  }
  if (Array.isArray(v)) return v.slice(0, 20).map((x) => clip(x, depth + 1))
  const out: Record<string, unknown> = {}
  for (const [k, val] of Object.entries(v).slice(0, 40)) out[k] = clip(val, depth + 1)
  return out
}

function record(payload: Record<string, unknown>): void {
  const e: FeedEvent = {
    seq: ++seq,
    at: Date.now(),
    event: String(payload.hook_event_name ?? "unknown"),
    session: String(payload.session_id ?? ""),
    cwd: String(payload.cwd ?? ""),
    tool: typeof payload.tool_name === "string" ? payload.tool_name : undefined,
    detail: clip(payload) as Record<string, unknown>,
  }
  buffer.push(e)
  if (buffer.length > MAX_EVENTS) buffer.splice(0, buffer.length - MAX_EVENTS)
  notify?.(e)
  for (const l of listeners) l(e)
  // Raw last, and each one guarded: an observer that throws must not cost the
  // feed its own listeners, and this port can never steer a session.
  for (const l of rawListeners) {
    try {
      l(payload)
    } catch (err) {
      console.error(`[feed] raw subscriber failed: ${(err as Error).message}`)
    }
  }
}

/** The port actually bound, 0 when another instance holds it — see start(). */
export function port(): number {
  return bound
}

export function recent(): FeedEvent[] {
  return [...buffer]
}

/**
 * Binds 127.0.0.1 only. Resolves the actual port (pass 0 for ephemeral —
 * selftest does), or 0 when the port is taken: a second app instance already
 * listens, and this one just runs without a feed rather than failing startup.
 */
export function start(opts: { port?: number; onEvent?: (e: FeedEvent) => void } = {}): Promise<number> {
  notify = opts.onEvent
  return new Promise((resolve) => {
    const s = createServer((req, res) => {
      if (req.method === "POST" && req.url === "/hook") {
        let body = ""
        let dropped = false
        req.on("data", (chunk: Buffer) => {
          body += chunk
          if (body.length > MAX_BODY) {
            dropped = true
            req.destroy()
          }
        })
        req.on("end", () => {
          if (dropped) return
          try {
            record(JSON.parse(body) as Record<string, unknown>)
            res.writeHead(200, { "content-type": "application/json" }).end("{}")
          } catch {
            res.writeHead(400).end()
          }
        })
        return
      }
      if (req.method === "GET" && req.url === "/recent") {
        res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(buffer))
        return
      }
      res.writeHead(404).end()
    })
    s.on("error", () => resolve(0)) // port taken — see above
    s.listen(opts.port ?? PORT, "127.0.0.1", () => {
      server = s
      const addr = s.address()
      bound = typeof addr === "object" && addr ? addr.port : 0
      resolve(bound)
    })
  })
}

export function stop(): void {
  server?.close()
  server = null
  bound = 0
  notify = undefined
}
