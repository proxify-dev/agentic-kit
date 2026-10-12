/**
 * The credential plane: teamclaude's engine (vendored under ./engine/) booted
 * against the gateway's own accounts file.
 *
 * What lives here is the glue teamclaude's index.js did around the engine —
 * boot, persistence, reload — and one function, `serve`, that hands a request
 * to the engine's retry-switch loop and reports back what it did. Nothing in
 * this file decides which account serves or when to rotate; that is the engine's
 * job and its tests' job to keep honest.
 *
 * OWNERSHIP. Whoever refreshes an OAuth token owns the grant: a refresh rotates
 * the refresh token, and every other holder's copy dies with `invalid_grant`.
 * So an account lives in exactly one engine at a time, and this one refreshes
 * only what is in ITS file (`accountsFile`). A login copied here from anywhere
 * else must stop being refreshed there first.
 */
import type { IncomingMessage, ServerResponse } from "node:http"
import { existsSync, writeFileSync } from "node:fs"
import { dirname } from "node:path"
import type { Config } from "./config.ts"
import { privateDir, privateFile } from "./fsperm.ts"
import { AccountManager, parseAdvisorModel, parseRequestModel } from "./engine/account-manager.js"
import { atomicConfigUpdate, loadConfig as readAccountsFile, loadState, saveState } from "./engine/config.js"
import { sameIdentity } from "./engine/identity.js"
import { importCredentials, refreshAccessToken } from "./engine/oauth.js"
import { Prober } from "./engine/prober.js"
import type { ProbeStatus } from "./engine/prober.js"
import { resolveAccounts } from "./engine/resolve-accounts.js"
import { forwardRequest, resolveAccountPin } from "./engine/server.js"
import type { ForwardCtx } from "./engine/server.js"
import { SxManager } from "./engine/sx.js"
import type { AccountRecord, AccountsFile, ManagerStatus, Tokens } from "./engine/types.d.ts"
import { IDENTITY_HEADERS } from "./identity.ts"
import { UsageReader } from "./trace.ts"

/**
 * Paths that must reach upstream with the CLIENT's credential, never a rotated
 * account token: Claude Code's own OAuth refresh, the Remote Control channel,
 * attachment transfers. teamclaude's CLIENT_CREDENTIAL_PATHS plus the token
 * endpoint it relays raw. The gateway forwards these with `cred: null`.
 */
const PASSTHROUGH_PATHS = ["/v1/oauth/token", "/v1/code/", "/api/oauth/files/", "/api/oauth/file_upload"]
export const isPassthrough = (path: string) => PASSTHROUGH_PATHS.some((p) => path.startsWith(p))

/**
 * How often the quota prober reads every account's buckets when the accounts
 * file does not say. The usage API spends no quota; without it an account nobody
 * is using keeps the numbers it had when traffic last left it.
 */
export const DEFAULT_PROBE_SECONDS = 300

/**
 * The file's `quotaProbeSeconds` when it has one (0 = off). Otherwise the default,
 * but only against Anthropic: the usage API speaks for its accounts, and a
 * gateway aimed elsewhere — a test's fake upstream — has nobody to ask.
 */
export function probeSecondsFor(doc: { quotaProbeSeconds?: unknown }, upstreamUrl: string): number {
  if (typeof doc.quotaProbeSeconds === "number") return doc.quotaProbeSeconds
  return new URL(upstreamUrl).host === "api.anthropic.com" ? DEFAULT_PROBE_SECONDS : 0
}

/** Telemetry Claude Code posts; teamclaude answers 200 locally and so do we. */
export const isEventLogging = (path: string) => path.startsWith("/api/event_logging")

/**
 * The serving org's billing state — whether extra usage is on, why not, what
 * upgrade it could buy (teamclaude b5be7fa, #478). Claude Code caches it as its
 * OWN org's: behind a pool of two orgs it can show "no usage credits", block a
 * model, or offer extra usage on the wrong one. The engine reads every header
 * for quota before the reply is written; only the client's copy loses these.
 */
export const isOverageHeader = (name: string) => {
  const lk = name.toLowerCase()
  return lk.startsWith("anthropic-ratelimit-unified-overage-") || lk === "anthropic-ratelimit-unified-upgrade-paths"
}

/** On unless the file says `stripOverageHeaders: false` — a pool is the gateway's normal case (upstream's default is off). */
export const stripsOverage = (doc: { stripOverageHeaders?: unknown }) => doc.stripOverageHeaders !== false

export type ServeResult = {
  /** The status the client got; null when no reply head was sent. */
  status: number | null
  account: string | null
  ttfbMs: number | null
  bytesOut: number
  usage: UsageReader
  /** Why upstream failed this request (a reset before any reply, a cut stream); null otherwise. */
  error: string | null
}

export type Engine = {
  am: AccountManager
  /** The engine's status — the same shape as teamclaude's /teamclaude/status. */
  status(): ManagerStatus
  /** Re-sync accounts from the file: new rows added, credentials refreshed, toggles applied. */
  reload(): Promise<{ added: number }>
  /**
   * Take an account out: its row leaves the file (and any per-model route that
   * listed it), and the running engine stops serving it now. Null when the file
   * has no account by that name.
   */
  remove(name: string): Promise<{ name: string } | null>
  /** Hand one request to the retry-switch loop. Resolves when the response is done. */
  serve(args: {
    req: IncomingMessage
    res: ServerResponse
    /** The path to send upstream — the client's, with any /tc-acct/ pin prefix already stripped. */
    url: string
    body: Buffer
    reqId: string
    sessionId: string | null
    pin: { account: string; mode: "pin" | "prefer" }
  }): Promise<ServeResult>
  /** Ask Anthropic's usage API for every enabled OAuth account's buckets — the only source of the Sonnet-7d one. */
  probe(): Promise<ProbeStatus>
  probeStatus(): ProbeStatus
  /** Change the probe interval live (0 = off). */
  setProbeInterval(seconds: number): void
  /** Whether replies lose the per-org billing headers now (the file's stripOverageHeaders, default on). */
  stripsOverage(): boolean
  /** Write quota state now (the interval does it every minute anyway). */
  persist(): Promise<void>
  /**
   * Step down for a newer gateway: from here on this engine neither refreshes a
   * token nor writes one, and the promise settles once the writes it already
   * started are on disk. It still serves the replies it has open.
   */
  quiesce(): Promise<void>
  /**
   * Take the grants: refresh and write tokens from here on, start the prober,
   * and re-read the file for anything written while this engine was quiet.
   * A no-op for an engine that already owns them.
   */
  own(): Promise<{ added: number }>
  stop(): Promise<void>
}

/** A file with no accounts is a valid start; the CLI's `account login` fills it. */
export function ensureAccountsFile(path: string): void {
  if (existsSync(path)) return
  privateDir(dirname(path))
  const empty: AccountsFile = { accounts: [], switchThreshold: 0.98, holdSeconds: 0 }
  writeFileSync(path, `${JSON.stringify(empty, null, 2)}\n`, { mode: 0o600 })
  privateFile(path, dirname(path))
}

/**
 * Point the vendored config.js at OUR file. It reads `TEAMCLAUDE_CONFIG` on every
 * call rather than caching, so setting it once before boot is the whole seam.
 * Process-wide by nature: one gateway process, one accounts file.
 */
function bindAccountsFile(path: string): void {
  process.env.TEAMCLAUDE_CONFIG = path
}

const findRow = (doc: AccountsFile, account: { accountUuid: string | null; orgUuid: string | null; name: string }) =>
  doc.accounts.findIndex((a) => sameIdentity(a, account))

export async function bootEngine(
  cfg: Config,
  opts: {
    refreshFn?: (refreshToken: string) => Promise<Tokens>
    log?: (line: string) => void
    /**
     * False boots the engine quiet — serving, but neither refreshing nor writing
     * a token, the prober off — until `own()`. How a gateway under the keeper
     * starts: the one before it may still hold the grants.
     */
    owner?: boolean
  } = {},
): Promise<Engine> {
  const log = opts.log ?? ((line: string) => process.stderr.write(`gateway: ${line}\n`))
  ensureAccountsFile(cfg.accountsFile)
  bindAccountsFile(cfg.accountsFile)

  const doc = (await readAccountsFile()) ?? { accounts: [] }
  const accounts = await resolveAccounts(doc)
  const threshold = typeof doc.switchThreshold === "number" ? doc.switchThreshold : 0.98
  const holdMs = (typeof doc.holdSeconds === "number" ? doc.holdSeconds : cfg.holdSeconds) * 1000
  // Live: reload() re-reads it, and the next reply follows.
  let stripOverage = stripsOverage(doc)

  // A quiet engine does not own its grants: another gateway does, or will. A
  // refresh from here would rotate a refresh token that one holds and kill its
  // copy, so it fails as a transient error (not 400/401/403): the account keeps
  // its token and stays in rotation, and the request that wanted it retries.
  // A refresh that started before the engine went quiet still lands in the file
  // — its tokens are `minted` — or the new owner would boot with the old token.
  let quiet = opts.owner === false
  const minted = new Set<string>()
  const refresh = opts.refreshFn ?? refreshAccessToken
  const am = new AccountManager(accounts, threshold, {
    routes: doc.routes,
    ramp: doc.stormRamp,
    distributeSessions: doc.distributeSessions,
    refreshFn: async (refreshToken: string) => {
      if (quiet) throw Object.assign(new Error("another gateway owns token refreshes now"), { status: 503 })
      const tokens = await refresh(refreshToken)
      minted.add(tokens.accessToken)
      return tokens
    },
  })

  // Quota observed by a previous run, so a restart does not forget which
  // account is nearly spent. Passive: nothing here calls the API to re-learn it.
  const saved = await loadState().catch((err: Error) => {
    log(`could not read quota state: ${err.message}`)
    return null
  })
  if (saved?.quota) am.restoreQuotaState(saved.quota)
  am.selectActiveAccount()

  const persist = () =>
    saveState({ quota: am.exportQuotaState() }).catch((err: Error) => log(`could not save quota state: ${err.message}`))
  let timer: ReturnType<typeof setInterval> | null = null
  const startPersisting = () => {
    timer ??= setInterval(() => void persist(), 60_000)
    timer.unref()
  }

  // The quota prober: teamclaude's, every DEFAULT_PROBE_SECONDS unless the file
  // says otherwise (0 = off), and always available as a one-shot for the TUI's
  // `p`. It keeps an idle account's bars true, and it is the only way to learn
  // the Sonnet weekly bucket — response headers never carry it. A probe can
  // refresh a token, so a quiet engine does not start it.
  const prober = new Prober(am, {
    intervalMs: probeSecondsFor(doc, cfg.upstreamUrl) * 1000,
    log: (m) => log(m.replace(/^\[TeamClaude\] /, "")),
  })
  if (!quiet) {
    startPersisting()
    prober.start()
  }

  // A refreshed token goes to disk through the serialized read-modify-write
  // chain — the thing that keeps two accounts refreshing at boot from clobbering
  // each other's rotated refresh token. Matched by identity, not index: the file
  // may have gained a row since we read it. A quiet engine writes only tokens it
  // minted itself; a token it merely re-read (reload) is not its to write.
  let writes: Promise<unknown> = Promise.resolve()

  // Accounts taken out while serving. The engine knows an account by its
  // POSITION — a refresh under way reports back by index, a request releases
  // its slot by index, sessions remember one — so splicing a live account out
  // would shift every one after it, and a refresh still running on the removed
  // login would land its token in the neighbour's row. So a removed account
  // stays in place, disabled and hidden from every status, until the next boot
  // reads a file without it. Its row is gone, so nothing it does is written.
  const removed = new Set<unknown>()
  const shown = <T>(rows: T[]): T[] => rows.filter((_, i) => !removed.has(am.accounts[i]))

  am.onTokenRefresh((idx, tokens) => {
    const account = am.accounts[idx]
    if (!account) return
    const own = minted.delete(tokens.accessToken)
    if (quiet && !own) return
    writes = atomicConfigUpdate((disk) => {
      for (const row of disk.accounts) {
        if (!am.accounts.some((a) => sameIdentity(a, row))) am.addAccount(row)
      }
      const i = findRow(disk, account)
      if (i >= 0) {
        disk.accounts[i].accessToken = tokens.accessToken
        disk.accounts[i].refreshToken = tokens.refreshToken
        disk.accounts[i].expiresAt = tokens.expiresAt
      }
    }).catch((err: Error) => log(`could not save refreshed token for ${account.name}: ${err.message}`))
  })

  // sx.org egress. Dormant without a key — the live teamclaude config had
  // mode:"always" and no key, which is exactly this state.
  const sx = new SxManager({ log: (m) => log(`sx: ${m}`) })
  if (doc.sx?.apiKey) {
    const r = await sx.configure(doc.sx.apiKey, doc.sx.mode)
    if (!r.ok) log(`sx.org disabled: ${r.error ?? "unknown"}`)
  } else if (doc.sx?.mode) await sx.setMode(doc.sx.mode)

  /**
   * teamclaude's syncAccountsFromDisk, unchanged in behaviour: pair file rows to
   * running accounts by identity (greedy 1:1, so two orgs of one person pair
   * right), add the new ones, pick up renames / priority / disable toggles, and
   * take fresher credentials — never staler ones.
   */
  const reload = async (): Promise<{ added: number }> => {
    const disk = await readAccountsFile()
    if (!disk) return { added: 0 }
    let added = 0
    const claimed = new Set<number>()
    const claim = (row: AccountRecord): number => {
      for (let i = 0; i < am.accounts.length; i++) {
        if (!claimed.has(i) && sameIdentity(am.accounts[i], row)) {
          claimed.add(i)
          return i
        }
      }
      return -1
    }
    for (const row of disk.accounts) {
      const i = claim(row)
      if (i < 0) {
        am.addAccount(row)
        claimed.add(am.accounts.length - 1)
        added += 1
        log(`picked up account "${row.name}"`)
        continue
      }
      const mgr = am.accounts[i]
      // A removed account logged in again: the same place, back in service.
      removed.delete(mgr)
      if (row.orgUuid && !mgr.orgUuid) mgr.orgUuid = row.orgUuid
      if (row.orgName && !mgr.orgName) mgr.orgName = row.orgName
      if (row.name && mgr.name !== row.name) mgr.name = row.name
      if (row.priority != null && mgr.priority !== row.priority) mgr.priority = row.priority
      const wantDisabled = row.disabled === true
      if (mgr.disabled !== wantDisabled) am.setDisabled(mgr.index, wantDisabled)

      let fresh: { accessToken?: string; refreshToken?: string; expiresAt?: number; apiKey?: string } | null = null
      if (row.type === "oauth" && row.importFrom) {
        try {
          const c = await importCredentials(row.importFrom)
          fresh = { accessToken: c.accessToken, refreshToken: c.refreshToken, expiresAt: c.expiresAt }
        } catch (err) {
          log(`re-import failed for "${row.name}": ${(err as Error).message}`)
        }
      } else if (row.type === "oauth" && row.accessToken) {
        fresh = { accessToken: row.accessToken, refreshToken: row.refreshToken, expiresAt: row.expiresAt }
      } else if (row.type === "apikey" && row.apiKey) {
        fresh = { apiKey: row.apiKey }
      }
      if (!fresh) continue
      if (fresh.accessToken) {
        const changed = mgr.credential !== fresh.accessToken || mgr.refreshToken !== (fresh.refreshToken ?? null)
        const diskIsStaler = !!fresh.expiresAt && !!mgr.expiresAt && fresh.expiresAt < mgr.expiresAt
        if (changed && !diskIsStaler) {
          am.updateAccountTokens(mgr.index, {
            accessToken: fresh.accessToken,
            refreshToken: fresh.refreshToken,
            expiresAt: fresh.expiresAt,
          })
          log(`refreshed credentials for "${mgr.name}" from file`)
        }
      } else if (fresh.apiKey && mgr.credential !== fresh.apiKey) {
        mgr.credential = fresh.apiKey
        if (mgr.status === "error") mgr.status = "active"
      }
    }
    if (disk.routes !== undefined) am.setRoutes(disk.routes)
    stripOverage = stripsOverage(disk)
    return { added }
  }

  const serve: Engine["serve"] = async ({ req, res, url, body, reqId, sessionId, pin }) => {
    // The engine reads req.headers straight through, so hand it a view with
    // what must not leave the machine already gone: our identity headers, and
    // the doorman pass the client sent in place of a credential.
    const headers: Record<string, string | string[] | undefined> = {}
    for (const [k, v] of Object.entries(req.headers)) {
      const lk = k.toLowerCase()
      if (IDENTITY_HEADERS.includes(lk)) continue
      if (lk === "authorization" || lk === "x-api-key") continue
      headers[k] = v
    }
    const pinnedIndex = pin.account ? resolveAccountPin(am, pin.account) : null
    const ctx: ForwardCtx = {
      tried: new Set(),
      reauthed: new Set(),
      model: parseRequestModel(body),
      advisorModel: parseAdvisorModel(body),
      sessionId,
      pinnedIndex,
      pinSoft: pin.mode === "prefer",
      holdBudgetMs: holdMs,
    }
    const tap = tapResponse(res, stripOverage)
    await forwardRequest(
      { method: req.method, url, headers },
      res,
      body,
      am,
      cfg.upstreamUrl,
      0,
      {},
      reqId,
      ctx,
      null,
      sx,
      undefined,
    )
    tap.finish()
    // What reached the client, as wire.ts reports it for the passthrough path.
    // No head sent: no status — the engine reset the socket (a failure) or the
    // client left first. `res.statusCode` alone defaults to 200, and every reset
    // was traced as one. A head sent but the reply never ended, with a failure:
    // the stream was cut. A client hanging up is not an error.
    const sent = res.headersSent
    const failure = ctx.failure ?? null
    return {
      status: sent ? (ctx.status ?? res.statusCode) : null,
      account: ctx.account ?? null,
      ttfbMs: tap.ttfbMs,
      bytesOut: tap.bytesOut,
      usage: tap.usage,
      error: !sent ? (failure ? `upstream: ${failure}` : null) : !res.writableEnded && failure ? `upstream stream: ${failure}` : null,
    }
  }

  const remove = async (name: string): Promise<{ name: string } | null> => {
    const found: { row?: AccountRecord } = {}
    await atomicConfigUpdate((disk) => {
      const i = disk.accounts.findIndex((row) => row.name === name)
      if (i < 0) return
      found.row = disk.accounts.splice(i, 1)[0]
      // A per-model route that listed it would name an account that is not there.
      disk.routes = (disk.routes ?? []).map((r) => {
        const route = r as { accounts?: unknown }
        return Array.isArray(route.accounts) ? { ...route, accounts: route.accounts.filter((n) => n !== name) } : r
      })
    })
    const row = found.row
    if (!row) return null
    const mgr = am.accounts.find((a) => !removed.has(a) && sameIdentity(a, row))
    if (mgr) {
      am.setDisabled(mgr.index, true)
      removed.add(mgr)
      if (am.currentIndex === mgr.index) am.selectActiveAccount()
    }
    await persist()
    log(`removed account "${row.name}"`)
    return { name: row.name }
  }

  return {
    am,
    status: () => {
      const s = am.getStatus()
      return { ...s, accounts: shown(s.accounts) }
    },
    reload,
    remove,
    serve,
    probe: async () => {
      await prober.probeAll()
      await persist()
      const p = prober.getStatus()
      return { ...p, accounts: shown(p.accounts) }
    },
    probeStatus: () => {
      const p = prober.getStatus()
      return { ...p, accounts: shown(p.accounts) }
    },
    setProbeInterval: (seconds) => {
      if (!quiet) prober.reschedule(Math.max(0, seconds) * 1000)
    },
    stripsOverage: () => stripOverage,
    persist,
    own: async () => {
      if (!quiet) return { added: 0 }
      quiet = false
      startPersisting()
      prober.start()
      // What the previous owner wrote while this one booted.
      return reload()
    },
    quiesce: async () => {
      quiet = true
      if (timer) clearInterval(timer)
      prober.stop()
      // Refreshes already under way finish and write (their tokens are minted);
      // then the write chain, serialized, settles after its last write.
      const running = am.accounts
        .map((a) => (a as unknown as { _refreshPromise?: Promise<void> | null })._refreshPromise)
        .filter((p): p is Promise<void> => !!p)
      await Promise.all(running.map((p) => p.catch(() => {})))
      await writes.catch(() => {})
    },
    stop: async () => {
      if (timer) clearInterval(timer)
      prober.stop()
      await persist()
    },
  }
}

/**
 * The engine writes the response itself, so the trace reads it off `res` on the
 * way out: first byte time, bytes, and usage from the SSE stream or JSON body.
 * Same UsageReader the passthrough path feeds from wire.ts — one parser, two feeds.
 * With `stripOverage`, the reply head loses the per-org billing headers
 * (isOverageHeader) — the engine has already read them for quota by then.
 */
function tapResponse(res: ServerResponse, stripOverage = false): { ttfbMs: number | null; bytesOut: number; usage: UsageReader; finish: () => void } {
  const t0 = Date.now()
  const usage = new UsageReader()
  const state = { ttfbMs: null as number | null, bytesOut: 0, usage, finish: () => {} }
  let sse: boolean | null = null
  const chunks: Buffer[] = []

  const sniff = (headers: unknown) => {
    if (sse !== null) return
    if (headers && typeof headers === "object" && !Array.isArray(headers)) {
      for (const [k, v] of Object.entries(headers as Record<string, unknown>))
        if (k.toLowerCase() === "content-type") sse = String(v).includes("event-stream")
    }
  }
  const take = (chunk: unknown) => {
    if (chunk == null) return
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk))
    state.bytesOut += buf.length
    if (sse === null) sse = /^(event|data):/.test(buf.subarray(0, 8).toString())
    if (sse) usage.pushSSE(buf.toString("utf8"))
    else if (chunks.length < 512) chunks.push(buf)
  }

  const origWriteHead = res.writeHead.bind(res)
  const origWrite = res.write.bind(res)
  const origEnd = res.end.bind(res)
  res.writeHead = ((status: number, ...rest: unknown[]) => {
    if (state.ttfbMs === null) state.ttfbMs = Date.now() - t0
    const at = typeof rest[0] === "string" ? 1 : 0
    const headers = rest[at]
    if (stripOverage && headers && typeof headers === "object" && !Array.isArray(headers))
      rest[at] = Object.fromEntries(Object.entries(headers as Record<string, unknown>).filter(([k]) => !isOverageHeader(k)))
    sniff(rest[at])
    return (origWriteHead as (...a: unknown[]) => ServerResponse)(status, ...rest)
  }) as typeof res.writeHead
  res.write = ((chunk: unknown, ...rest: unknown[]) => {
    take(chunk)
    return (origWrite as (...a: unknown[]) => boolean)(chunk, ...rest)
  }) as typeof res.write
  res.end = ((chunk?: unknown, ...rest: unknown[]) => {
    if (typeof chunk !== "function") take(chunk)
    return (origEnd as (...a: unknown[]) => ServerResponse)(chunk, ...rest)
  }) as typeof res.end

  state.finish = () => {
    if (!sse && chunks.length > 0) usage.pushJSON(Buffer.concat(chunks))
  }
  return state
}
