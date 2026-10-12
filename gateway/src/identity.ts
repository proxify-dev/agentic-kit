/**
 * Who is asking.
 *
 * This is the piece a standalone proxy cannot have. teamclaude sees a request
 * and knows a session id; the harness knows that this session is run 12 of the
 * `nightly-sweep` agent working item `catchup`. Joining the two is what
 * turns the wire into a control point: policy can key on the agent, and a trace
 * row can be attributed to an item.
 *
 * Identity travels as request headers, because that is the one channel every
 * client already forwards:
 *
 *   x-brain-run      the run/tab this request belongs to
 *   x-brain-agent    the agent record driving it
 *   x-brain-item     the item it is attached to
 *   x-brain-project  the project note
 *   x-brain-origin   app | cli | automation | probe
 *
 * The app's runner sets them via ANTHROPIC_CUSTOM_HEADERS; a CLI launch gets
 * them from the cc-shim fragment. Neither is wired yet — an unlabelled request
 * is normal and falls back to the session id Claude Code already sends.
 */
import type { IncomingHttpHeaders } from "node:http"

export type Identity = {
  run: string | null
  agent: string | null
  item: string | null
  project: string | null
  origin: string
  /** Claude Code's own session id — present on every CLI/SDK request. */
  session: string | null
  /** True when nothing above the session id was supplied. */
  anonymous: boolean
}

const one = (h: IncomingHttpHeaders, k: string): string | null => {
  const v = h[k]
  const s = Array.isArray(v) ? v[0] : v
  const t = typeof s === "string" ? s.trim() : ""
  // Header values are attacker-adjacent input in the general case and end up in
  // file paths and log lines here, so bound them and drop control characters.
  return t.length > 0 && t.length <= 200 ? t.replace(/[\u0000-\u001f\u007f]/g, "") : null
}

export function readIdentity(headers: IncomingHttpHeaders): Identity {
  const run = one(headers, "x-brain-run")
  const agent = one(headers, "x-brain-agent")
  const item = one(headers, "x-brain-item")
  const project = one(headers, "x-brain-project")
  return {
    run,
    agent,
    item,
    project,
    origin: one(headers, "x-brain-origin") ?? "unknown",
    session: one(headers, "x-claude-code-session-id"),
    anonymous: !run && !agent && !item && !project,
  }
}

/**
 * Claude Code's own routing hints — the `x-claude-code-*` headers it sends from
 * v2.1.273, to api.anthropic.com by default and to a custom base URL only with
 * CLAUDE_CODE_GATEWAY_HINT_HEADERS=1 (claude-env.ts sets it alongside the base
 * URL). Fixed vocabularies, tool names and durations; never prompt text. Parsed
 * into fields here, so the trace still writes no header: a value outside the
 * expected shape is dropped, not stored.
 */
export type Hints = {
  /** main · subagent · workflow · compaction · auxiliary (titles, classifiers, summaries). */
  requestClass: string | null
  /** A subagent's turns only: Explore · Plan · general-purpose · custom · teammate · fork. */
  agentType: string | null
  /** The request that summarises the conversation: auto · manual · reactive. */
  compaction: string | null
  /** The first main request after a compaction, same values: the old prefix is dead. */
  contextCompacted: string | null
  /** Run time of the tool calls whose results this request carries, in the order collected. */
  tools: { name: string; ms: number }[] | null
}

const word = (h: IncomingHttpHeaders, k: string): string | null => {
  const v = one(h, k)
  return v && /^[A-Za-z0-9_.-]{1,40}$/.test(v) ? v : null
}

/** `Bash=742;Read=9` — names percent-encoded, at most 32 entries (Claude Code's own cap). */
function toolDurations(h: IncomingHttpHeaders): Hints["tools"] {
  const raw = h["x-claude-code-prev-tool-durations"]
  const s = Array.isArray(raw) ? raw[0] : raw
  if (typeof s !== "string" || s.length === 0 || s.length > 4096) return null
  const out: { name: string; ms: number }[] = []
  for (const part of s.split(";").slice(0, 32)) {
    const eq = part.lastIndexOf("=")
    if (eq <= 0) continue
    let name: string
    try {
      name = decodeURIComponent(part.slice(0, eq))
    } catch {
      continue
    }
    const ms = Number(part.slice(eq + 1))
    if (!Number.isInteger(ms) || ms < 0 || name.length > 120 || /[\u0000-\u001f\u007f]/.test(name)) continue
    out.push({ name, ms })
  }
  return out.length > 0 ? out : null
}

export function readHints(headers: IncomingHttpHeaders): Hints | null {
  const hints: Hints = {
    requestClass: word(headers, "x-claude-code-request-class"),
    agentType: word(headers, "x-claude-code-agent-type"),
    compaction: word(headers, "x-claude-code-compaction"),
    contextCompacted: word(headers, "x-claude-code-context-compacted"),
    tools: toolDurations(headers),
  }
  return Object.values(hints).some((v) => v !== null) ? hints : null
}

/** The identity headers are ours; upstream has no use for them. */
export const IDENTITY_HEADERS = [
  "x-brain-run",
  "x-brain-agent",
  "x-brain-item",
  "x-brain-project",
  "x-brain-origin",
  // Not identity, but ours alike: a dry run (dry-run.ts) is answered before
  // anything goes upstream, and the header is never let out if one were not.
  "x-brain-dry-run",
]

/** One-line label for logs and the status view. */
export function label(id: Identity): string {
  const parts = [id.agent && `agent:${id.agent}`, id.item && `item:${id.item}`, id.run && `run:${id.run}`]
    .filter(Boolean)
    .join(" ")
  if (parts) return parts
  return id.session ? `session:${id.session.slice(0, 8)}` : "anonymous"
}
