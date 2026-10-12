/**
 * Who may knock at all.
 *
 * The gateway takes no key: being on this machine is the credential. A web page
 * in the owner's browser is on this machine too, so two more conditions hold
 * every request to what a local client looks like. teamclaude's rule
 * (925a80a, `isSameOriginControlRequest` / `isLocalHostHeader`), made ours:
 *
 *   cross-origin   a page's fetch carries Sec-Fetch-Site or Origin; curl, the CLI,
 *                  Claude Code and Node's fetch send neither — a page could spend
 *                  an account on its own prompts and read the answer
 *   rebinding      a page whose name flips to 127.0.0.1 is same-origin to its
 *                  browser, but the Host it sends still names the page
 */
import type { IncomingHttpHeaders } from "node:http"

const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1"])
// A wildcard bind says nothing about the name that reached us.
const WILDCARD_BINDS = new Set(["0.0.0.0", "::", ""])

const first = (v: string | string[] | undefined): string | undefined => (Array.isArray(v) ? v[0] : v)

/** A Host value's name: port and IPv6 brackets off, lowercased. Null when it cannot be one. */
function hostnameOf(host: string): string | null {
  const h = host.trim().toLowerCase()
  if (h.startsWith("[")) {
    const end = h.indexOf("]")
    return end < 0 ? null : h.slice(1, end)
  }
  // A bare IPv6 address has several colons and no port; `name:port` has one.
  const colon = h.indexOf(":")
  if (colon >= 0 && h.indexOf(":", colon + 1) >= 0) return h
  return colon >= 0 ? h.slice(0, colon) : h
}

/** Browser-set headers only: a same-origin or typed-in request is fine, anything else is a page. */
export function isSameOrigin(headers: IncomingHttpHeaders): boolean {
  const site = first(headers["sec-fetch-site"])
  if (site) return site === "same-origin" || site === "none"
  return !first(headers.origin)
}

/**
 * Whether Host names this machine. A missing Host passes: only HTTP/1.0 may omit
 * it (Node refuses an HTTP/1.1 request without one), and no browser speaks it.
 */
export function isLocalHost(host: string | undefined, bindHost: string): boolean {
  if (host == null || host === "") return true
  const name = hostnameOf(host)
  if (name == null) return false
  if (LOCAL_HOSTNAMES.has(name)) return true
  const bound = hostnameOf(bindHost)
  return bound != null && !WILDCARD_BINDS.has(bound) && bound === name
}

/** Why this request is refused at the door, or null to let it in. */
export function browserRefusal(headers: IncomingHttpHeaders, bindHost: string): string | null {
  if (!isSameOrigin(headers)) return "cross-origin request refused: a web page cannot use the gateway"
  if (!isLocalHost(first(headers.host), bindHost)) return "request refused: the Host header does not name this gateway"
  return null
}

/**
 * The path as upstream would read it — percent-decoded, `\` and `//` folded, query
 * off — so `/%5Fgateway/x` is known for a control path and not sent out as one.
 */
export function controlPath(url: string): string {
  let p = url.split("?")[0]
  try {
    p = decodeURIComponent(p)
  } catch {
    // Malformed escapes stay as they are; they match nothing below.
  }
  return p.replace(/\\/g, "/").replace(/\/{2,}/g, "/")
}

/**
 * A path that is ours, never Anthropic's: anything under `/_gateway` a control
 * route did not claim, and `/health` (a probe that meant `/_gateway/health`).
 * Sent upstream, it would go with an account's token and come back 404 anyway.
 */
export function isUnclaimed(path: string): boolean {
  return path === "/_gateway" || path.startsWith("/_gateway/") || path === "/health"
}
