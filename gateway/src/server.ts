/**
 * The wire itself.
 *
 * One request in, one trace row out, with policy in between. Everything under
 * `/_gateway/` is the control surface the app and the CLI read; everything else
 * goes upstream — through the engine, which picks the account and injects its
 * credential, or untouched for the paths that carry the client's own.
 */
import http from "node:http"
import type { IncomingMessage, ServerResponse } from "node:http"
import net from "node:net"
import { readFileSync, existsSync, readdirSync } from "node:fs"
import { join } from "node:path"
import type { Config } from "./config.ts"
import { paths } from "./config.ts"
import { dryRunReply, isDryRun } from "./dry-run.ts"
import { bootEngine, isEventLogging, isPassthrough } from "./engine.ts"
import { browserRefusal, controlPath, isUnclaimed } from "./front-door.ts"
import type { Engine } from "./engine.ts"
import { atomicConfigUpdate } from "./engine/config.js"
import { MAX_PROBE_INTERVAL_MS } from "./engine/prober.js"
import type { Tokens } from "./engine/types.d.ts"
import { label, readHints, readIdentity } from "./identity.ts"
import { Budget, evaluatePre, validateRules } from "./policy.ts"
import type { Rule } from "./policy.ts"
import { findAccount, readPool } from "./pool.ts"
import { accountViews } from "./quota.ts"
import { parsePinPrefix, Routes } from "./routes.ts"
import type { RouteTable } from "./routes.ts"
import { isModelPath, pinnedBlobs, savedBodies, Trace } from "./trace.ts"
import { Transcripts } from "./transcripts.ts"
import type { Swept } from "./trace.ts"
import type { UsageReader } from "./trace.ts"
import { normalizeStatus } from "./upstream-status.ts"
import type { TraceRow } from "./trace.ts"
import { forward, readBody } from "./wire.ts"
import { env as brandEnv } from "./brand.ts"
import { command as gw } from "./command.ts"

export type Server = {
  server: http.Server
  close: () => Promise<void>
  /**
   * Take no new connection, finish every open reply, then settle; past the cap
   * the rest are cut. In a hand-over a kept-alive socket is retired by its next
   * reply (`connection: close`) or its idle timeout, so the client's next request
   * opens a connection the new gateway answers. In a plain stop there is nobody
   * to answer it, and idle sockets close at once.
   */
  drain: (capMs: number, handover: boolean) => Promise<void>
  stats: () => Stats
  /** The credential plane. */
  engine: Engine
}

export type ServerOptions = {
  /** Stands in for the OAuth refresh call — the tests' fake. */
  refreshFn?: (refreshToken: string) => Promise<Tokens>
  /** False: boot without owning the tokens until `engine.own()` — a gateway under the keeper. */
  owner?: boolean
  /** Claude Code's config dir, whose transcripts `capture: gaps` looks for — the tests' scratch one. */
  claudeConfigDir?: string
  /** `POST /_gateway/stop` — the daemon's graceful stop, the same on every OS. Absent: the route answers 404. */
  onStop?: () => void
}

export type Stats = {
  started: string
  requests: number
  inFlight: number
  errors: number
  refusals: number
  port: number
  budget: Record<string, number>
  lastError: string | null
}

const json = (res: ServerResponse, status: number, body: unknown) => {
  const text = JSON.stringify(body, null, 2)
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(text) })
  res.end(text)
}

/** Anthropic's own error envelope, so a refused client reports it sensibly. */
const apiError = (res: ServerResponse, status: number, type: string, message: string) => {
  const text = JSON.stringify({ type: "error", error: { type, message } })
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(text) })
  res.end(text)
}

type Parsed = {
  model: string | null
  stream: boolean
  system: unknown
  tools: unknown
  messages: number
  systemBlocks: number
  toolCount: number
  obj: Record<string, unknown> | null
}

function parseBody(body: Buffer, contentType: string | undefined): Parsed {
  const empty: Parsed = {
    model: null, stream: false, system: undefined, tools: undefined,
    messages: 0, systemBlocks: 0, toolCount: 0, obj: null,
  }
  if (body.length === 0 || !String(contentType ?? "").includes("json")) return empty
  try {
    const o = JSON.parse(body.toString("utf8")) as Record<string, unknown>
    const sys = o.system
    return {
      model: typeof o.model === "string" ? o.model : null,
      stream: o.stream === true,
      system: sys,
      tools: o.tools,
      messages: Array.isArray(o.messages) ? o.messages.length : 0,
      systemBlocks: Array.isArray(sys) ? sys.length : sys === undefined ? 0 : 1,
      toolCount: Array.isArray(o.tools) ? o.tools.length : 0,
      obj: o,
    }
  } catch {
    return empty
  }
}

export async function createServer(cfg: Config, opts: ServerOptions = {}): Promise<Server> {
  const p = paths(cfg)
  const trace = new Trace(p.trace, p.blobs, cfg.capture)
  const transcripts = new Transcripts(opts.claudeConfigDir)
  const routes = new Routes(join(cfg.dataDir, "routes.json"))
  const budget = new Budget()
  const rules: Rule[] = validateRules(cfg.policy)
  const started = new Date().toISOString()

  const engine: Engine = await bootEngine(cfg, { refreshFn: opts.refreshFn, owner: opts.owner })
  const enabledAccounts = () => engine.status().accounts.filter((a) => !a.disabled).length

  const stats: Stats = {
    started, requests: 0, inFlight: 0, errors: 0, refusals: 0,
    port: cfg.port, budget: {}, lastError: null,
  }

  // Retention (trace.ts `sweep`): a minute after boot, so a restart storm
  // never sweeps, then every six hours. Unref'd: it never keeps a process up.
  let swept: Swept | null = null
  let sweeping = false
  const sweep = () => {
    if (!cfg.blobDays || sweeping) return
    sweeping = true
    Promise.resolve()
      .then(() => trace.sweep(cfg.blobDays, [...savedBodies(p.xray), ...pinnedBlobs(p.pins)]))
      .then((s) => (swept = s), (err: Error) => (stats.lastError = `blob sweep: ${err.message}`))
      .finally(() => (sweeping = false))
  }
  const sweepTimers = [setTimeout(sweep, 60_000), setInterval(sweep, 6 * 3_600_000)]
  for (const t of sweepTimers) t.unref()
  const stopSweeps = () => sweepTimers.forEach((t) => clearTimeout(t))

  /** What is in flight right now, for the TUI's activity panel. */
  type Active = { id: string; ts: string; path: string; model: string | null; who: string; account: string | null }
  const active = new Map<string, Active>()

  /** Last N trace rows, newest last — today's file, plus yesterday's if short. */
  const recentRows = (n: number): unknown[] => {
    if (!existsSync(p.trace)) return []
    const files = readdirSync(p.trace)
      .filter((f) => f.endsWith(".ndjson"))
      .sort()
      .slice(-2)
    const lines: string[] = []
    for (const f of files)
      lines.push(...readFileSync(join(p.trace, f), "utf8").split("\n").filter(Boolean))
    return lines
      .slice(-n)
      .map((l) => {
        try {
          return JSON.parse(l) as unknown
        } catch {
          return null
        }
      })
      .filter(Boolean)
  }

  const control = (
    req: IncomingMessage,
    res: ServerResponse,
    path: string,
    url: string,
  ): boolean => {
    // `build` and `keeper` are set by the keeper that runs this as a release;
    // both null for a daemon started by hand (`try`, a foreground `start`).
    if (path === "/_gateway/health") {
      json(res, 200, {
        ok: true, port: cfg.port, pid: process.pid, accounts: enabledAccounts(),
        // A caller about to send `x-brain-dry-run` checks this first: a build
        // without it would forward the request.
        dryRun: true,
        build: brandEnv("GATEWAY_RELEASE") ?? null,
        keeper: brandEnv("GATEWAY_KEEPER") ? Number(brandEnv("GATEWAY_KEEPER")) : null,
      })
      return true
    }
    // The account pool with observed quota, read from the state file beside
    // the accounts file — never a second quota model that could disagree with
    // the one actually making rotation decisions. `live` is the engine's own
    // status: who is serving right now, and how loaded each account is. `probe`
    // is the quota prober's: how often it runs, when each account last answered.
    if (path === "/_gateway/accounts" && req.method !== "PATCH") {
      json(res, 200, {
        ...accountViews(cfg.accountsFile),
        routes: routes.read(),
        live: normalizeStatus(engine.status()),
        probe: engine.probeStatus(),
      })
      return true
    }
    // `claude --account X` → the one account X names, as plain text, for the
    // cc-shim fragment to put in a /tc-acct/ prefix. The engine's own pin
    // resolver is exact-match; this is where a partial name becomes exact —
    // before launch, so a bad name refuses at the prompt, not mid-session.
    if (path === "/_gateway/resolve") {
      const q = new URL(url, "http://x").searchParams.get("q") ?? ""
      const text = (status: number, body: string) => {
        res.writeHead(status, { "content-type": "text/plain; charset=utf-8" })
        res.end(`${body}\n`)
      }
      try {
        const doc = readPool(cfg.accountsFile)
        const i = findAccount(doc, q)
        if (i < 0) text(404, `no account matches "${q}"`)
        else if (doc.accounts[i].disabled) text(409, `${doc.accounts[i].name} is disabled — ${gw("account enable")} ${doc.accounts[i].name}`)
        else text(200, doc.accounts[i].name)
      } catch (err) {
        text(409, (err as Error).message)
      }
      return true
    }
    // Edit one account's toggles in the file, then have the engine re-read it.
    if (path.startsWith("/_gateway/accounts/") && req.method === "PATCH") {
      const name = decodeURIComponent(path.slice("/_gateway/accounts/".length))
      void readBody(req).then(
        async (b) => {
          try {
            const patch = JSON.parse(b.toString("utf8")) as { disabled?: boolean; priority?: number; current?: boolean }
            let found = false
            await atomicConfigUpdate((disk) => {
              const row = disk.accounts.find((a) => a.name === name)
              if (!row) return
              found = true
              if (typeof patch.disabled === "boolean") row.disabled = patch.disabled
              if (typeof patch.priority === "number") row.priority = patch.priority
            })
            if (!found) {
              json(res, 404, { error: `no account named ${JSON.stringify(name)}` })
              return
            }
            await engine.reload()
            // "Serve from this one now" — the TUI's `s`. In-memory only, like teamclaude.
            if (patch.current === true) {
              const i = engine.am.accounts.findIndex((a) => a.name === name)
              if (i >= 0) engine.am.currentIndex = i
            }
            json(res, 200, { ok: true, live: normalizeStatus(engine.status()) })
          } catch (err) {
            json(res, 400, { error: (err as Error).message })
          }
        },
        (err: Error) => json(res, 400, { error: err.message }),
      )
      return true
    }
    // Take an account out of the pool — the TUI's `x`, `account remove` from a
    // shell. The engine drops it from the file and from rotation at once; a route
    // that named it would pin requests to nothing, so it goes back to rotation.
    if (path.startsWith("/_gateway/accounts/") && req.method === "DELETE") {
      const name = decodeURIComponent(path.slice("/_gateway/accounts/".length))
      void engine.remove(name).then(
        (r) => {
          if (!r) {
            json(res, 404, { error: `no account named ${JSON.stringify(name)}` })
            return
          }
          const t = routes.read()
          const fallback = t.fallback.account === name
          const rules = t.rules.filter((rule) => rule.account === name).map((rule) => rule.name)
          if (fallback || rules.length)
            routes.write({ fallback: fallback ? { account: "", mode: "prefer" } : t.fallback, rules: t.rules.filter((rule) => rule.account !== name) })
          json(res, 200, { ok: true, removed: r.name, routesCleared: [...(fallback ? ["fallback"] : []), ...rules], live: normalizeStatus(engine.status()) })
        },
        (err: Error) => json(res, 500, { error: err.message }),
      )
      return true
    }
    // One probe of the usage API for every account, now. The TUI's `p`.
    if (path === "/_gateway/probe" && req.method === "POST") {
      void engine.probe().then(
        (r) => json(res, 200, { ok: true, probe: r, live: normalizeStatus(engine.status()) }),
        (err: Error) => json(res, 500, { error: err.message }),
      )
      return true
    }
    // A stop with no signal: on Windows a kill is always a hard kill, so the CLI
    // asks here first. Loopback only; a web page never gets this far (front-door.ts).
    if (path === "/_gateway/stop" && req.method === "POST" && opts.onStop) {
      const from = req.socket.remoteAddress ?? ""
      if (!/^(127\.|::1$|::ffff:127\.)/.test(from)) {
        json(res, 403, { error: "stop is asked from this machine only" })
        return true
      }
      req.resume()
      const stop = opts.onStop
      res.once("finish", () => setImmediate(stop))
      json(res, 200, { ok: true, pid: process.pid })
      return true
    }
    // Re-read the accounts file without a restart — after `account login` or a
    // hand edit.
    if (path === "/_gateway/reload" && req.method === "POST") {
      void engine.reload().then(
        (r) => json(res, 200, { ok: true, ...r }),
        (err: Error) => json(res, 500, { error: err.message }),
      )
      return true
    }
    if (path === "/_gateway/routes" && req.method === "PUT") {
      void readBody(req).then(
        (b) => {
          try {
            json(res, 200, routes.write(JSON.parse(b.toString("utf8")) as RouteTable))
          } catch (err) {
            json(res, 400, { error: (err as Error).message })
          }
        },
        (err: Error) => json(res, 400, { error: err.message }),
      )
      return true
    }
    if (path === "/_gateway/trace") {
      const n = Number(new URL(url, "http://x").searchParams.get("n") ?? 50)
      json(res, 200, { rows: recentRows(Number.isFinite(n) ? n : 50) })
      return true
    }
    // Runtime-tunable settings, kept in the accounts file so they survive a
    // restart. Threshold applies live; the rest at the next boot, and the reply
    // says so.
    if (path === "/_gateway/settings") {
      if (req.method === "PUT") {
        void readBody(req).then(
          async (b) => {
            try {
              const put = JSON.parse(b.toString("utf8")) as { switchThreshold?: number; holdSeconds?: number; distributeSessions?: boolean; quotaProbeSeconds?: number }
              // The prober clamps too; refusing here keeps the file saying what runs.
              const maxProbe = MAX_PROBE_INTERVAL_MS / 1000
              if (put.quotaProbeSeconds !== undefined && !(Number.isFinite(put.quotaProbeSeconds) && put.quotaProbeSeconds >= 0 && put.quotaProbeSeconds <= maxProbe))
                throw new Error(`quotaProbeSeconds must be 0 (off) to ${maxProbe} (seven days)`)
              const restart: string[] = []
              await atomicConfigUpdate((disk) => {
                if (typeof put.switchThreshold === "number" && put.switchThreshold > 0 && put.switchThreshold <= 1) {
                  disk.switchThreshold = put.switchThreshold
                  engine.am.switchThreshold = put.switchThreshold
                }
                if (typeof put.holdSeconds === "number" && put.holdSeconds >= 0) {
                  disk.holdSeconds = put.holdSeconds
                  restart.push("holdSeconds")
                }
                if (typeof put.distributeSessions === "boolean") {
                  disk.distributeSessions = put.distributeSessions
                  restart.push("distributeSessions")
                }
                if (typeof put.quotaProbeSeconds === "number" && put.quotaProbeSeconds >= 0) {
                  disk.quotaProbeSeconds = put.quotaProbeSeconds
                  engine.setProbeInterval(put.quotaProbeSeconds)
                }
              })
              json(res, 200, { ok: true, switchThreshold: engine.am.switchThreshold, quotaProbeSeconds: engine.probeStatus().intervalSeconds, appliesAtRestart: restart })
            } catch (err) {
              json(res, 400, { error: (err as Error).message })
            }
          },
          (err: Error) => json(res, 400, { error: err.message }),
        )
        return true
      }
      json(res, 200, {
        switchThreshold: engine.am.switchThreshold, holdSeconds: cfg.holdSeconds,
        quotaProbeSeconds: engine.probeStatus().intervalSeconds, stripOverageHeaders: engine.stripsOverage(),
        accountsFile: cfg.accountsFile,
      })
      return true
    }
    if (path === "/_gateway/status") {
      json(res, 200, {
        ...stats,
        active: [...active.values()],
        budget: budget.snapshot(),
        capture: cfg.capture,
        blobDays: cfg.blobDays,
        swept,
        rules: rules.map((r) => ({ rule: r.rule, kind: r.kind, level: r.level })),
        accounts: engine.status().accounts.map((a) => ({ name: a.name, state: a.disabled ? "disabled" : a.status })),
        accountsFile: cfg.accountsFile,
        dataDir: cfg.dataDir,
      })
      return true
    }
    return false
  }

  let draining = false
  let refusalLogged = 0
  const server = http.createServer((req, res) => {
    const t0 = Date.now()
    const url = req.url ?? "/"
    const path = url.split("?")[0]
    // A request that still reached us on a kept-alive socket is served, and told
    // to open its next connection fresh — which lands on the new gateway.
    if (draining) res.setHeader("connection", "close")

    // A web page is on this machine too (front-door.ts). Refused before anything
    // reads the body, routes, or reaches an account; no trace row — it was never
    // a model call. One log line a minute, so a page retrying cannot flood it.
    const refusal = browserRefusal(req.headers, cfg.host)
    if (refusal) {
      stats.refusals += 1
      if (Date.now() - refusalLogged > 60_000) {
        refusalLogged = Date.now()
        process.stderr.write(`gateway: ${refusal} (${req.method} ${path}, origin ${String(req.headers.origin ?? "-")})\n`)
      }
      req.resume()
      apiError(res, 403, "permission_error", refusal)
      return
    }
    // A preflight is answered here with no CORS grant: sent upstream it went with
    // an account's token, and Anthropic's answer allows any origin.
    if (req.method === "OPTIONS") {
      req.resume()
      res.writeHead(204, { allow: "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS" })
      res.end()
      return
    }

    // A bug in a control endpoint must not take the wire down with it.
    try {
      if (control(req, res, path, url)) return
    } catch (err) {
      stats.errors += 1
      stats.lastError = (err as Error).message
      if (!res.headersSent) json(res, 500, { error: (err as Error).message })
      else res.end()
      return
    }
    // A control path no route claimed — a typo, a wrong verb, an old client —
    // is answered here, not sent to Anthropic with an account's token.
    if (isUnclaimed(controlPath(parsePinPrefix(url)?.url ?? url))) {
      req.resume()
      json(res, 404, { error: `no gateway route for ${req.method} ${path} — health is GET /_gateway/health` })
      return
    }

    // Telemetry: answered here, never forwarded, never traced — the engine's
    // `eventLogging: hide`. Claude Code 2.1.281 sends it straight to Anthropic
    // instead (docs/security.md, "What still goes around it"); older clients still land here.
    if (isEventLogging(path)) {
      req.resume()
      json(res, 200, {})
      return
    }

    stats.requests += 1
    stats.inFlight += 1

    void (async () => {
      const id = trace.nextId()
      const identity = readIdentity(req.headers)
      const hints = readHints(req.headers)
      let row: Partial<TraceRow> = {}
      try {
        const body = await readBody(req)
        const parsed = parseBody(body, req.headers["content-type"])

        // A dry run is answered here, before policy, routes, accounts or
        // anything upstream: its row is written first, then the canned reply.
        if (isDryRun(req.headers)) {
          const real = (parsePinPrefix(url)?.url ?? url).split("?")[0]
          const messageId = `msg_dryrun_${id}`
          const reply = dryRunReply({ path: real, model: parsed.model, stream: parsed.stream, messageId })
          trace.write({
            v: 2, id, ts: new Date(t0).toISOString(), durMs: Date.now() - t0, ttfbMs: null,
            method: req.method ?? "GET", path, status: reply.status, account: null, accountSource: null,
            model: parsed.model, stream: parsed.stream, identity, hints, usage: null,
            stopReason: reply.messages ? "end_turn" : null, messageId: reply.messages ? messageId : null,
            dryRun: true,
            blobs: { system: trace.putBlob(parsed.system), tools: trace.putBlob(parsed.tools), body: trace.putBlob(parsed.obj, true) },
            shape: {
              messages: parsed.messages, tools: parsed.toolCount, systemBlocks: parsed.systemBlocks,
              bytesIn: body.length, bytesOut: Buffer.byteLength(reply.text),
            },
            policy: [], error: null,
          })
          res.writeHead(reply.status, { "content-type": reply.contentType, "content-length": Buffer.byteLength(reply.text), "request-id": `req_dryrun_${id}` })
          res.end(reply.text)
          return
        }

        active.set(id, { id, ts: new Date(t0).toISOString(), path, model: parsed.model, who: label(identity), account: null })

        const pre = evaluatePre(rules, { identity, model: parsed.model, path, body: parsed.obj }, budget)
        let sendBody = body
        if (pre.mutated && parsed.obj) sendBody = Buffer.from(JSON.stringify(parsed.obj))

        // capture: gaps saves what no transcript will hold (trace.ts `putCall`).
        const gaps = cfg.capture === "gaps"
        const recorded = gaps && isModelPath(path) && transcripts.has(identity.session)
        const side = hints?.requestClass === "auxiliary" || hints?.requestClass === "compaction"
        row = {
          v: 2, id, ts: new Date(t0).toISOString(), method: req.method ?? "GET", path,
          model: parsed.model, stream: parsed.stream, identity, hints,
          blobs: trace.putCall(parsed.obj, { recorded, side }),
          ...(gaps ? { recorded } : {}),
          shape: {
            messages: parsed.messages, tools: parsed.toolCount, systemBlocks: parsed.systemBlocks,
            bytesIn: sendBody.length, bytesOut: 0,
          },
          policy: pre.decisions,
        }

        if (pre.refusal) {
          stats.refusals += 1
          apiError(res, pre.refusal.status, "gateway_policy", pre.refusal.message)
          trace.write({
            ...(row as TraceRow), status: pre.refusal.status, account: null, accountSource: null,
            durMs: Date.now() - t0, ttfbMs: null, usage: null, stopReason: null, messageId: null,
            error: `refused by ${pre.refusal.rule}`,
          })
          return
        }

        // Which account serves this request: the route table's answer, handed
        // to the engine as a pin or a preference. A pin the client carried in
        // its path (`claude --account X`) outranks the table.
        const carried = parsePinPrefix(url)
        const route = carried
          ? { account: carried.account, mode: carried.mode, rule: null }
          : routes.resolve(identity, parsed.model)
        if (route.rule)
          row.policy?.push({
            rule: route.rule,
            action: "routed",
            detail: `${route.account} (${route.mode})`,
          })

        let accountName: string | null = null
        let accountSource: TraceRow["accountSource"] = null
        let result: { status: number | null; ttfbMs: number | null; bytesOut: number; usage: UsageReader; error: string | null }

        if (!isPassthrough(carried ? carried.url.split("?")[0] : path)) {
          // The engine picks the account, injects and refreshes the credential,
          // and runs the whole 429/401/403 retry-switch loop. A route pin wins;
          // `cfg.account` is the standing pin when no route says.
          const pin = route.account
            ? { account: route.account, mode: route.mode }
            : { account: cfg.account, mode: "pin" as const }
          const served = await engine.serve({
            req, res, url: carried ? carried.url : url, body: sendBody, reqId: id,
            sessionId: identity.session ?? identity.run,
            pin,
          })
          accountName = served.account
          accountSource = served.account === null ? null : pin.account ? (pin.mode === "pin" ? "pinned" : "preferred") : "rotated"
          result = { ...served, error: served.error ?? (served.status !== null && served.status >= 500 ? `upstream ${served.status}` : null) }
        } else {
          // A passthrough path must carry the client's own credential (its
          // OAuth refresh, an upload) untouched — no account of ours serves it.
          const target = new URL(carried ? carried.url : url, cfg.upstreamUrl)
          result = await forward({ req, res, body: sendBody, target, cred: null })
        }

        const usage = result.usage.total
        const hasUsage = usage.input + usage.output + usage.cacheRead + usage.cacheCreation > 0
        if (hasUsage) budget.record(identity, usage)
        if (result.error) {
          stats.errors += 1
          stats.lastError = result.error
        }

        trace.write({
          ...(row as TraceRow),
          status: result.status,
          account: accountName,
          accountSource,
          durMs: Date.now() - t0,
          ttfbMs: result.ttfbMs,
          usage: hasUsage ? usage : null,
          stopReason: result.usage.stopReason,
          messageId: result.usage.messageId,
          shape: { ...(row.shape as TraceRow["shape"]), bytesOut: result.bytesOut },
          error: result.error,
        })

        if (brandEnv("GATEWAY_VERBOSE"))
          process.stderr.write(
            `${new Date().toISOString()} ${result.status} ${parsed.model ?? "-"} ` +
              `${label(identity)} ${result.ttfbMs ?? "-"}ms in=${usage.input} out=${usage.output} ` +
              `cache_r=${usage.cacheRead}\n`,
          )
      } catch (err) {
        stats.errors += 1
        stats.lastError = (err as Error).message
        if (!res.headersSent) apiError(res, 500, "gateway_error", (err as Error).message)
        else res.end()
        try {
          trace.write({
            v: 2, id, ts: new Date(t0).toISOString(), durMs: Date.now() - t0, ttfbMs: null,
            method: req.method ?? "GET", path, status: null, account: null,
            accountSource: null,
            model: null, stream: false, identity, hints, usage: null, stopReason: null, messageId: null,
            blobs: { system: null, tools: null, body: null },
            shape: { messages: 0, tools: 0, systemBlocks: 0, bytesIn: 0, bytesOut: 0 },
            policy: [], error: (err as Error).message,
          })
        } catch {
          // A trace that cannot be written must not take the request with it.
        }
      } finally {
        stats.inFlight -= 1
        active.delete(id)
      }
    })()
  })

  return {
    server,
    engine,
    stats: () => ({ ...stats, budget: budget.snapshot() }),
    close: async () => {
      stopSweeps()
      await new Promise<void>((resolve) => {
        server.close(() => resolve())
        server.closeIdleConnections()
      })
      await engine.stop()
    },
    drain: (capMs, handover) =>
      new Promise<void>((resolve) => {
        draining = true
        stopSweeps()
        let closed = false
        // In a hand-over, net's close, not http's: http.Server.close also shuts
        // every idle kept-alive socket on the spot, and a client writing its next
        // request into one at that instant gets a reset. Left open, an idle socket
        // either carries one more request (answered, `connection: close`) or
        // reaches the keep-alive timeout the client was told in every reply.
        if (handover) net.Server.prototype.close.call(server, () => (closed = true))
        else server.close(() => (closed = true))
        // Settled only when every socket is gone AND every handler has written
        // its trace row — the row lands after the reply's last byte.
        const tick = setInterval(() => {
          if (closed && stats.inFlight === 0) done()
        }, 100)
        const cap = setTimeout(() => {
          server.closeAllConnections()
          done()
        }, capMs)
        const done = () => {
          clearInterval(tick)
          clearTimeout(cap)
          resolve()
        }
      }),
  }
}
