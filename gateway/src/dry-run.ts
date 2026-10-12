/**
 * A request that asks not to be sent.
 *
 * `x-brain-dry-run` on a request makes the gateway answer it itself: the trace
 * row is written as for any request — identity, hints, blobs, with the body
 * blob kept whatever the capture level, and `dryRun: true` — and the reply is a
 * canned one, built here. Nothing is forwarded; no policy, route or account is
 * consulted, so a dry run needs no account and spends none. `ak xray` is
 * the caller: it launches claude with the header in ANTHROPIC_CUSTOM_HEADERS
 * and reads the prompt back out of the trace.
 *
 * The header is also in IDENTITY_HEADERS (identity.ts), so a request that
 * somehow carried it down the normal path would not take it upstream.
 */
import type { IncomingHttpHeaders } from "node:http"

export const DRY_RUN_HEADER = "x-brain-dry-run"

/** Present with any non-empty value: the header fails safe, a `0` is still a dry run. */
export function isDryRun(headers: IncomingHttpHeaders): boolean {
  const v = headers[DRY_RUN_HEADER]
  const s = Array.isArray(v) ? v[0] : v
  return typeof s === "string" && s.trim().length > 0
}

const ZERO = { input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 }

/** A whole, valid streamed Messages reply: one text block, "ok", end_turn, zero usage. */
export function cannedStream(model: string, id: string): string {
  const ev = (type: string, data: Record<string, unknown>) => `event: ${type}\ndata: ${JSON.stringify({ type, ...data })}\n\n`
  return [
    ev("message_start", {
      message: { id, type: "message", role: "assistant", model, content: [], stop_reason: null, stop_sequence: null, usage: ZERO },
    }),
    ev("content_block_start", { index: 0, content_block: { type: "text", text: "" } }),
    ev("content_block_delta", { index: 0, delta: { type: "text_delta", text: "ok" } }),
    ev("content_block_stop", { index: 0 }),
    ev("message_delta", { delta: { stop_reason: "end_turn", stop_sequence: null }, usage: { output_tokens: 0 } }),
    ev("message_stop", {}),
  ].join("")
}

/** The same reply, not streamed. */
export function cannedMessage(model: string, id: string): Record<string, unknown> {
  return {
    id,
    type: "message",
    role: "assistant",
    model,
    content: [{ type: "text", text: "ok" }],
    stop_reason: "end_turn",
    stop_sequence: null,
    usage: ZERO,
  }
}

/**
 * The reply for one dry-run request. A Messages call gets a message (SSE when
 * it asked to stream); any other path — count_tokens, a models list — `{}`.
 */
export function dryRunReply(opts: { path: string; model: string | null; stream: boolean; messageId: string }): {
  status: number
  contentType: string
  text: string
  messages: boolean
} {
  if (opts.path !== "/v1/messages") return { status: 200, contentType: "application/json", text: "{}", messages: false }
  const model = opts.model ?? "claude"
  return opts.stream
    ? { status: 200, contentType: "text/event-stream", text: cannedStream(model, opts.messageId), messages: true }
    : { status: 200, contentType: "application/json", text: JSON.stringify(cannedMessage(model, opts.messageId)), messages: true }
}
