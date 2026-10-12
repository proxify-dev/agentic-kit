/**
 * The forward leg: take a buffered request, send it upstream, stream the answer
 * back while reading usage off the wire.
 *
 * Pooled HTTP/1.1 rather than global fetch, for the reason teamclaude learned
 * the hard way: Node's fetch multiplexes every request to one origin onto a
 * single HTTP/2 connection, and Claude Code POSTs ~1MB of context per turn, so
 * concurrent agents serialise on one flow-control window and a trivial request
 * can wait minutes for headers. Independent H1 sockets have no such contention.
 */
import http from "node:http"
import https from "node:https"
import type { IncomingHttpHeaders, IncomingMessage, ServerResponse } from "node:http"
import { IDENTITY_HEADERS } from "./identity.ts"
import { UsageReader } from "./trace.ts"

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
])

const agents = {
  http: new http.Agent({ keepAlive: true, maxSockets: 256 }),
  https: new https.Agent({ keepAlive: true, maxSockets: 256 }),
}

export type Credential = { type: "oauth" | "api"; value: string; accountUuid?: string } | null

/**
 * Build the upstream header set.
 *
 * `accept-encoding` goes because a compressed body cannot be read for usage
 * without decompressing it, and forwarding a body we decompressed under a
 * Content-Encoding we kept would corrupt the response. Dropping it costs a
 * little bandwidth on loopback and buys a readable stream.
 */
export function upstreamHeaders(
  incoming: IncomingHttpHeaders,
  cred: Credential,
  host: string,
): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(incoming)) {
    const lk = k.toLowerCase()
    if (lk.startsWith(":")) continue
    if (HOP_BY_HOP.has(lk)) continue
    if (lk === "accept-encoding") continue
    // Ours; upstream has no use for them and they should not leave the machine.
    if (IDENTITY_HEADERS.includes(lk)) continue
    // With a `cred` we replace the credential; on a passthrough path it is null
    // and the client's own header rides through untouched.
    if (cred && (lk === "x-api-key" || lk === "authorization")) continue
    if (typeof v === "string") out[k] = v
    else if (Array.isArray(v)) out[k] = v.join(", ")
  }
  if (cred) {
    if (cred.type === "oauth") out["authorization"] = `Bearer ${cred.value}`
    else out["x-api-key"] = cred.value
  }
  out["host"] = host
  return out
}

export type ForwardResult = {
  status: number
  ttfbMs: number | null
  bytesOut: number
  usage: UsageReader
  error: string | null
}

/**
 * Pipe one request upstream and the response back to the client.
 * Never throws: a transport failure becomes a 502 and a populated `error`.
 */
export function forward(opts: {
  req: IncomingMessage
  res: ServerResponse
  body: Buffer
  target: URL
  cred: Credential
  headersTimeoutMs?: number
}): Promise<ForwardResult> {
  const { req, res, body, target, cred } = opts
  const isTls = target.protocol === "https:"
  const mod = isTls ? https : http
  const usage = new UsageReader()
  const started = Date.now()

  return new Promise<ForwardResult>((resolve) => {
    let settled = false
    let bytesOut = 0
    let ttfbMs: number | null = null
    const done = (status: number, error: string | null) => {
      if (settled) return
      settled = true
      resolve({ status, ttfbMs, bytesOut, usage, error })
    }

    const headers = upstreamHeaders(req.headers, cred, target.host)
    headers["content-length"] = String(body.length)

    const up = mod.request(
      {
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port || (isTls ? 443 : 80),
        path: target.pathname + target.search,
        method: req.method,
        headers,
        agent: isTls ? agents.https : agents.http,
      },
      (upRes) => {
        ttfbMs = Date.now() - started
        const status = upRes.statusCode ?? 502
        const outHeaders: Record<string, string | string[]> = {}
        for (const [k, v] of Object.entries(upRes.headers)) {
          if (HOP_BY_HOP.has(k.toLowerCase())) continue
          if (v !== undefined) outHeaders[k] = v
        }
        if (!res.headersSent) res.writeHead(status, outHeaders)

        const ct = String(upRes.headers["content-type"] ?? "")
        const isSSE = ct.includes("event-stream")
        const chunks: Buffer[] = []

        upRes.on("data", (chunk: Buffer) => {
          bytesOut += chunk.length
          // Read usage as it passes; never hold the client's bytes waiting for it.
          if (isSSE) usage.pushSSE(chunk.toString("utf8"))
          else if (chunks.length < 512) chunks.push(chunk)
          const ok = res.write(chunk)
          if (!ok) {
            upRes.pause()
            res.once("drain", () => upRes.resume())
          }
        })
        upRes.on("end", () => {
          if (!isSSE && chunks.length > 0) usage.pushJSON(Buffer.concat(chunks))
          res.end()
          done(status, null)
        })
        upRes.on("error", (err: Error) => {
          res.end()
          done(status, `upstream stream: ${err.message}`)
        })
      },
    )

    // A stuck upstream must fail fast rather than wedge the socket forever;
    // the client retries and Node evicts the dead socket from the pool.
    up.setTimeout(opts.headersTimeoutMs ?? 120_000, () => {
      up.destroy(new Error("upstream timeout"))
    })

    up.on("error", (err: Error) => {
      if (!res.headersSent) {
        res.writeHead(502, { "content-type": "application/json" })
        res.end(
          JSON.stringify({
            type: "error",
            error: { type: "gateway_upstream_error", message: err.message },
          }),
        )
      } else res.end()
      done(502, err.message)
    })

    // The client hanging up mid-stream is normal (ctrl-c), not an error.
    res.on("close", () => {
      if (!settled) up.destroy()
    })

    up.end(body)
  })
}

/** Read the whole request body. Bounded so a runaway upload cannot eat memory. */
export function readBody(req: IncomingMessage, maxBytes = 64 * 1024 * 1024): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    let n = 0
    req.on("data", (c: Buffer) => {
      n += c.length
      if (n > maxBytes) {
        reject(new Error(`request body over ${maxBytes} bytes`))
        req.destroy()
        return
      }
      chunks.push(c)
    })
    req.on("end", () => resolve(Buffer.concat(chunks)))
    req.on("error", reject)
  })
}
