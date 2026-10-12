import http from 'node:http';
import https from 'node:https';
import { timingSafeEqual } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { ensureCerts, createConnectHandler } from './mitm.js';
import { patchAccountUuid } from './account-uuid-rewrite.js';
import { sanitizeToolPairs } from './tool-pair-sanitize.js';
import { parseRequestModel, parseAdvisorModel } from './account-manager.js';
import { TopLevelFieldFinder, modelGlobMatches } from './model.js';
import { BodyWriter } from './request-log.js';
import { upstreamFetch } from './upstream-fetch.js';
import { tunnelTls } from './sx.js';
import { createEgressGuard } from './egress-guard.js';
import { safeLine } from './safe-text.js';


export const HOP_BY_HOP_HEADERS = new Set([
  'host', 'connection', 'keep-alive', 'transfer-encoding',
  'te', 'trailer', 'upgrade', 'proxy-authorization', 'proxy-authenticate',
]);
// Path prefix for the deprecated URL-based account pin (superseded by TC_ACCT).
const PIN_PREFIX = '/tc-acct/';
// Same, but a PREFERENCE: bias to this account, then rotate normally. Exists so
// a launcher can say "start on the work account, roll onto the others when it is
// spent" per-launch, which account `priority` can only express globally.
const PREFER_PREFIX = '/tc-prefer/';
const INLINE_RETRY_AFTER_MAX_SECONDS = 15;
// How long the proxy will absorb a rate-limit 429's retry-after inline (waiting
// on the SAME account) before surfacing a 429 + retry-after to the client. A
// rate-limit 429 never rotates accounts (that just moves the burst); it pauses
// the account so concurrent requests wait, then retries the same account.
const RATE_LIMIT_ABSORB_MAX_SECONDS =
  Number(process.env.TEAMCLAUDE_RATE_LIMIT_ABSORB_MAX_SECONDS) || 60;

// How long to wait before the one retry of a headerless 429 — a 429 carrying no
// retry-after and no anthropic-ratelimit-* headers at all.
//
// Observed over a 32-minute window on a live fleet: these land about once every
// 8 minutes on Fable traffic and never on any other model; they follow the
// request onto whichever account the failover hop moves it to; consecutive
// refusals arrive 0.6-0.8s apart; and the client's own retry, after the 2m 38s
// backoff Claude Code applies, usually succeeds.
//
// 2s is chosen against those numbers rather than measured from them — nothing
// observed says how long the limit actually lasts. It sits above the 0.6-0.8s
// the hop already re-asked across and was refused, and far below the backoff the
// client would otherwise serve out. That is the entire argument for it, which is
// why it is an env var: TEAMCLAUDE_HEADERLESS_429_RETRY_DELAY_MS.
//
// One delay, not a ladder: the limit's window is unknown, and a second guess at
// it would cost the client the wait without evidence that it helps. So the worst
// case is the retry being refused too, and the client getting the 429 it gets
// today about 2s later.
const DEFAULT_HEADERLESS_429_RETRY_DELAY_MS = 2000;

/**
 * The wait before a headerless 429 is re-asked, in ms — or 0 for "do not retry".
 *
 * 0 is a setting, not a missing value: an operator who would rather have the
 * 429 at once than have the transient absorbed needs a way to say so, and a
 * delay of nothing is the natural spelling. Unset, empty, negative or
 * unparseable all mean the default, so a typo cannot switch the retry off.
 *
 * @returns {number}
 */
function resolveHeaderless429RetryDelayMs() {
  const raw = process.env.TEAMCLAUDE_HEADERLESS_429_RETRY_DELAY_MS;
  if (raw == null || raw.trim() === '') return DEFAULT_HEADERLESS_429_RETRY_DELAY_MS;
  const env = Number(raw);
  if (env === 0) return 0;
  return env > 0 ? env : DEFAULT_HEADERLESS_429_RETRY_DELAY_MS;
}

// A request-scoped 429's body names the reason; more than this is not one.
const ERROR_BODY_INSPECTION_LIMIT = 64 * 1024;

/** Read an error body up to `limit` bytes; null when it is longer or the read fails. */
async function readErrorBody(body, limit = ERROR_BODY_INSPECTION_LIMIT) {
  if (!body) return Buffer.alloc(0);
  const reader = body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return Buffer.concat(chunks, length);
      length += value.byteLength;
      if (length > limit) {
        await reader.cancel();
        return null;
      }
      chunks.push(Buffer.from(value));
    }
  } catch {
    await reader.cancel().catch(() => {});
    return null;
  } finally {
    reader.releaseLock();
  }
}

// Response header names that are connection-specific and thus illegal on an
// HTTP/2 response (Node's Http2ServerResponse.writeHead rejects them). Also
// hop-by-hop on h1, so stripping them is correct on both paths.
const CONNECTION_SPECIFIC_HEADERS = new Set([
  'connection', 'keep-alive', 'transfer-encoding', 'upgrade',
  'proxy-connection', 'te', 'trailer',
]);

// Constant-time proxy-API-key comparison (both the HTTP gate and the CONNECT
// gate use it). Returns false on any type/length mismatch without leaking timing.
export function safeKeyEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const ba = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ba.length !== bb.length) return false;
  return timingSafeEqual(ba, bb);
}

// True if a socket's remote address is loopback — the proxy-key gate exempts
// localhost on both the HTTP and CONNECT paths.
export function isLoopbackAddr(addr) {
  return addr === '127.0.0.1' || addr === '::1' || addr === '::ffff:127.0.0.1';
}

export function createProxyServer(accountManager, config, hooks = {}, sx = null) {
  const upstream = config.upstream || 'https://api.anthropic.com';
  const proxyApiKey = config.proxy?.apiKey;
  const logDir = config.logDir || null;
  const holdMs = (config.holdSeconds || 0) * 1000;

  if (logDir) {
    mkdir(logDir, { recursive: true }).catch(() => {});
  }

  const requestHandler = async (req, res) => {
    try {
      // Auth check — skip for localhost connections.
      const clientKey = req.headers['x-api-key'];
      const isLocal = isLoopbackAddr(req.socket.remoteAddress);
      if (proxyApiKey && !safeKeyEqual(clientKey, proxyApiKey) && !isLocal) {
        res.writeHead(401, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          type: 'error',
          error: { type: 'authentication_error', message: 'Invalid proxy API key' },
        }));
        return;
      }

      // Forward-proxy request (HTTP_PROXY): an absolute-form URL is a tool
      // proxying plain HTTP to some host. Account logic is only for hosts we
      // manage (the Anthropic upstream, which is HTTPS-only and never arrives
      // this way); forward anything else transparently instead of hijacking it.
      if (/^https?:\/\//i.test(req.url || '')) { relayHttpForward(req, res); return; }

      // Status endpoint
      if (req.method === 'GET' && req.url === '/teamclaude/status') {
        const status = accountManager.getStatus();
        const extra = hooks.getStatusExtra?.() || {};
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ...extra, ...status }, null, 2));
        return;
      }

      // Reload endpoint — re-sync accounts from config without a restart. This
      // is the headless equivalent of pressing 'R' in the TUI. Local control
      // only (no upstream calls); the auth gate above already applies.
      if (req.method === 'POST' && req.url === '/teamclaude/reload') {
        if (!hooks.reload) {
          res.writeHead(501, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'reload not supported' }));
          return;
        }
        try {
          const added = await hooks.reload();
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: true, added: added || 0 }));
        } catch (err) {
          res.writeHead(500, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: err.message }));
        }
        return;
      }

      return forward(req, res);
    } catch (err) {
      console.error('[TeamClaude] Unhandled error:', err);
    }
  };

  // Opt-in egress pin: null unless config.egress.pin is set, and then shared by
  // the base listener and the MITM one so both honour the same hold.
  const egress = createEgressGuard(config, console.error);
  const forward = createProxyRequestListener({ accountManager, upstream, logDir, hooks, sx, holdMs, config, egress });
  const server = http.createServer(requestHandler);

  // Forward-proxy support (always on, so multiple claude instances can use
  // either ANTHROPIC_BASE_URL or HTTPS_PROXY against the same server). A CONNECT
  // to the upstream host is a transparent MITM relay (rewrite only auth); the
  // test host is answered locally; anything else is blind-tunneled. Certs are
  // minted lazily on the first intercepted CONNECT.
  const mitmHost = (() => { try { return new URL(upstream).hostname; } catch { return 'api.anthropic.com'; } })();
  let certsPromise = null;
  const ensureLeaf = async () => {
    // Reset the memo on failure so a transient cert error doesn't wedge the MITM
    // path permanently (a cached rejected promise would re-throw on every CONNECT).
    certsPromise ||= ensureCerts(mitmHost).catch((err) => { certsPromise = null; throw err; });
    const c = await certsPromise;
    return { key: c.leafKeyPem, cert: c.leafCertPem };
  };
  server.on('connect', createConnectHandler({ config, accountManager, ensureLeaf, logDir, hooks, log: console.error, sx, egress }));
  // Remote Control's real-time channel is a WebSocket, not a request/response
  // call — Node fires 'upgrade' for that handshake, never 'request', so it
  // needs its own listener (base-URL routing path; the MITM path wires the
  // same relayUpgrade onto its own terminating server in mitm.js).
  server.on('upgrade', (req, socket, head) => relayUpgrade(req, socket, head, upstream, sx));

  return server;
}

/**
 * Resolve an account pin to an index, or null.
 *
 * Accepted forms, first match wins:
 *   - `accountUuid/orgUuid` — fully qualified, the only form that distinguishes
 *     one person's accounts across several orgs
 *   - `accountUuid`
 *   - `orgUuid`
 *   - the display name (`email` or `email (Org)`), or the bare email
 *
 * UUIDs are the identity to use for anything scripted or long-lived: display
 * names are rewritten in place when an email gains a second org (see
 * accountsCommand), so a name is a convenience, not an identifier.
 *
 * The rotation index is deliberately NOT accepted. It is array position, so
 * deleting an account would silently repoint every later pin at a DIFFERENT
 * account — a wrong-account misroute rather than an honest failure.
 */
export function resolveAccountPin(accountManager, token) {
  const accounts = accountManager.accounts || [];
  const norm = (s) => (s || '').trim().toLowerCase();
  const t = norm(token);
  if (!t) return null;

  const at = (pick) => accounts.findIndex(a => norm(pick(a)) === t);
  const qualified = accounts.findIndex(a => a.accountUuid && a.orgUuid
    && `${norm(a.accountUuid)}/${norm(a.orgUuid)}` === t);

  for (const i of [
    qualified,
    at(a => a.accountUuid),
    at(a => a.orgUuid),
    at(a => a.name),
    at(a => (a.name || '').split(' (')[0]), // display name minus the org suffix
  ]) if (i >= 0) return i;

  return null;
}

// Paths that must reach upstream with the client's own credential (never a
// rotated account token): the Remote Control channel and attachment transfers.
// teamclaude applies its account logic (rotation, exhaustion, token injection)
// ONLY to hosts it manages — the Anthropic upstream. Anything else must be
// forwarded transparently, never hijacked into "all accounts exhausted". For
// HTTPS this is already true (the CONNECT tunnel in mitm.js blind-relays
// non-upstream hosts). This is the plain-HTTP counterpart: a tool honoring
// HTTP_PROXY sends an ABSOLUTE-form request (`GET http://host/path`), which
// otherwise gets misrouted to Anthropic. Blind-relay it to its target with the
// client's own headers — no account selection, no token injection,
// content-encoding passed through (a transparent forward proxy). Anthropic is
// HTTPS-only, so in practice this only ever sees third-party hosts.
export function relayHttpForward(req, res) {
  let target;
  try { target = new URL(req.url); } catch {
    res.writeHead(400, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ type: 'error', error: { type: 'invalid_request_error', message: 'Malformed forward-proxy URL' } }));
    return;
  }
  const transport = target.protocol === 'http:' ? http : https;
  const headers = {};
  for (const [key, value] of Object.entries(req.headers)) {
    const lk = key.toLowerCase();
    // Drop hop-by-hop + proxy-control headers; `host` is reset from the target.
    if (lk.startsWith(':') || HOP_BY_HOP_HEADERS.has(lk) || lk === 'proxy-connection') continue;
    headers[key] = value;
  }

  const upstreamReq = transport.request(target, { method: req.method, headers }, (upstreamRes) => {
    const responseHeaders = {};
    for (const [key, value] of Object.entries(upstreamRes.headers)) {
      if (CONNECTION_SPECIFIC_HEADERS.has(key)) continue;
      responseHeaders[key] = value;
    }
    res.writeHead(upstreamRes.statusCode, responseHeaders);
    upstreamRes.pipe(res);
  });
  upstreamReq.on('error', (err) => {
    console.error(`[TeamClaude] HTTP forward to ${target.host} failed:`, describeConnectError(err));
    if (!res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ type: 'error', error: { type: 'proxy_error', message: 'Upstream unreachable' } }));
    }
  });
  res.on('close', () => upstreamReq.destroy());
  if (['GET', 'HEAD'].includes(req.method)) upstreamReq.end();
  else req.pipe(upstreamReq);
}

const CLIENT_CREDENTIAL_PATHS = ['/v1/code/', '/api/oauth/files/', '/api/oauth/file_upload'];

/**
 * Build the core proxy request listener — buffer the body, then forward with
 * account selection + retry (forwardRequest). Shared by the base HTTP server and
 * the MITM's terminating h2/h1 server, so both get identical buffering, model-
 * aware routing, and retry-on-quota behavior. Control endpoints (status/reload)
 * and the proxy-API-key gate live in the base server's wrapper, not here.
 */
export function createProxyRequestListener({ accountManager, upstream, logDir = null, hooks = {}, sx = null, holdMs = 0, config = {}, forcedPin = null, egress = null }) {
  let counter = 0;
  return async (req, res) => {
    try {
      // Claude Code's telemetry (`/api/event_logging/*`) is high-volume noise in
      // the activity log. `config.eventLogging` (read live so the TUI toggle takes
      // effect immediately): 'show' forwards + displays; 'hide' (default) forwards
      // but suppresses the activity entry; 'block' answers 200 locally without
      // forwarding (no upstream round-trip, no account/token spent).
      const eventLogging = config?.eventLogging || 'hide';
      const isEventLog = (req.url || '').startsWith('/api/event_logging');
      if (isEventLog && eventLogging === 'block') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end('{}');
        return;
      }
      const hideActivity = isEventLog && eventLogging !== 'show';
      // Egress pin (opt-in): with the exit IP off the pinned one — a VPN that
      // dropped — hold rather than send. Upstream answers a request from an
      // unexpected region with a 403 that Claude Code reports as a dead session,
      // so sending it costs a re-login while waiting costs latency. Checked here
      // rather than per-account: it is a property of the connection, and this is
      // the one path every request takes, MITM included.
      if (egress?.enabled()) {
        const state = await egress.waitUntilPinned({ isAborted: () => res.destroyed });
        if (res.destroyed) return;
        if (!state.ok) {
          res.writeHead(503, { 'Content-Type': 'application/json', 'retry-after': '30' });
          res.end(JSON.stringify({
            type: 'error',
            error: {
              type: 'proxy_error',
              message: `Egress is ${state.ip || 'unknown'}, not the pinned ${state.expected.join(', ')} — not sending this request. Check the VPN.`,
            },
          }));
          return;
        }
      }
      // Client token refresh: pass through untouched (the proxy manages its own
      // tokens via ensureTokenFresh; rewriting client refreshes would conflict).
      if (req.method === 'POST' && req.url === '/v1/oauth/token') { await relayRaw(req, res, upstream, sx); return; }
      // Remote Control (/v1/code/*) is bound to the session's paired claude.ai
      // identity — forward with the client's OWN credential (streamed), never a
      // rotated account token, which would 403 the worker event stream.
      // Attachment transfers (/api/oauth/files/*, /api/oauth/file_upload) are
      // likewise account-bound: files uploaded from claude.ai belong to the
      // paired identity, so fetching them with a rotated token 403s and Claude
      // Code silently drops the image from the message.
      if (CLIENT_CREDENTIAL_PATHS.some((p) => (req.url || '').startsWith(p))) { await relayStream(req, res, upstream, sx); return; }

      // Account pin: a request to `/tc-acct/<name-or-index>/...` (e.g. via
      // ANTHROPIC_BASE_URL=http://host:port/tc-acct/deepseek) is forced onto that
      // one account, bypassing rotation. Used by the keep-warm scheduler and for
      // manual per-account testing. The prefix is stripped before forwarding.
      let pinnedIndex = null;
      // Soft pin: same resolution, opposite failure mode. `/tc-prefer/<name>/…`
      // BIASES selection to that account while it is eligible and then lets
      // normal rotation take over — a preference, not an exclusion. `/tc-acct/`
      // stays hard (that account or a 429), because the warmer and per-account
      // testing both depend on never silently landing somewhere else.
      let pinSoft = false;
      // DEPRECATED: the path-prefix pin. Superseded by TC_ACCT, which works in
      // MITM mode too (this form cannot — inside a CONNECT tunnel the path is
      // the real upstream one). Kept for the warmer and for direct API callers.
      // One segment only, so the fully-qualified `accountUuid/orgUuid` form is
      // not expressible here; use TC_ACCT for that.
      const url = req.url || '';
      const pinPrefix = url.startsWith(PIN_PREFIX) ? PIN_PREFIX
        : url.startsWith(PREFER_PREFIX) ? PREFER_PREFIX
        : null;
      const afterPrefix = pinPrefix ? url.slice(pinPrefix.length) : null;
      // The token runs to the next '/', which also begins the real request path.
      const tokenEnd = afterPrefix == null ? -1 : afterPrefix.indexOf('/');
      if (tokenEnd > 0) {
        const token = decodeURIComponent(afterPrefix.slice(0, tokenEnd));
        pinSoft = pinPrefix === PREFER_PREFIX;
        pinnedIndex = resolveAccountPin(accountManager, token);
        if (pinnedIndex == null) {
          const reqId = ++counter;
          const sessionId = req.headers['x-claude-code-session-id'] || null;
          if (!hideActivity) hooks.onRequestEnd?.(reqId, { method: req.method, path: req.url, account: `(unknown pin: "${token}")`, status: 404, model: null, sessionId, pinned: false });
          res.writeHead(404, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ type: 'error', error: { type: 'not_found_error', message: `Unknown account pin "${token}"` } }));
          return;
        }
        req.url = afterPrefix.slice(tokenEnd);
      }

      // MITM-mode pin. A CONNECT carrying `Proxy-Authorization: Basic <acct>:…`
      // has no URL to hang a `/tc-acct/` prefix on — the path inside the tunnel
      // is the real Anthropic one — so the pin arrives as a listener bound to
      // that account (see createConnectHandler). Resolved per request rather
      // than at CONNECT time: a hot reload can renumber accounts while a tunnel
      // is open, and a name outliving an index is the safer half of that race.
      if (pinnedIndex == null && forcedPin != null) {
        pinnedIndex = resolveAccountPin(accountManager, forcedPin);
        if (pinnedIndex == null) {
          const reqId = ++counter;
          const sessionId = req.headers['x-claude-code-session-id'] || null;
          if (!hideActivity) hooks.onRequestEnd?.(reqId, { method: req.method, path: req.url, account: `(unknown pin: "${forcedPin}")`, status: 404, model: null, sessionId, pinned: false });
          res.writeHead(404, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ type: 'error', error: { type: 'not_found_error', message: `Unknown account pin "${forcedPin}" (from TC_ACCT)` } }));
          return;
        }
      }

      const reqId = ++counter;
      // Claude Code tags each session's requests with this header (present on
      // /v1/messages and count_tokens). Read from headers up front so it drives
      // session-aware routing (issue #109) and colors the TUI activity stream.
      const sessionId = req.headers['x-claude-code-session-id'] || null;
      if (!hideActivity) hooks.onRequestStart?.(reqId, { method: req.method, path: req.url, sessionId, pinned: pinnedIndex != null });

      // Buffer request body (needed to resend on a different account after a 429).
      // Peek the top-level `model` field incrementally as chunks arrive so the
      // TUI can show it the instant it appears in the stream — usually the first
      // frame — rather than waiting for the whole body and the request to finish.
      const bodyChunks = [];
      const modelFinder = new TopLevelFieldFinder('model');
      for await (const chunk of req) {
        bodyChunks.push(chunk);
        if (!modelFinder.done) {
          const found = modelFinder.push(chunk);
          if (found && !hideActivity) hooks.onRequestModel?.(reqId, { model: found });
        }
      }
      const body = Buffer.concat(bodyChunks);

      const model = modelFinder.done ? modelFinder.value : parseRequestModel(body);
      // An advisor request (Claude Code's advisor tool) carries a SECOND model
      // nested in tools[]; the advisor sub-inference runs on the selected
      // account, so selection must be eligible for it too (issue #98).
      const advisorModel = parseAdvisorModel(body);

      // Model blocklist (issue #116): reject a request for a blocked model right
      // here instead of forwarding it. A model no account can serve (e.g. Fable
      // once it left base plans) otherwise gets rate-limited upstream and hangs
      // the pipeline; a fast, non-retryable 400 lets the client move on. Read
      // live from the shared config so the TUI editor takes effect immediately.
      const blockedBy = model ? (config?.blockedModels || []).find((p) => modelGlobMatches(p, model)) : null;
      if (blockedBy) {
        if (!res.headersSent) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ type: 'error', error: { type: 'invalid_request_error', message: `Model "${model}" is blocked by teamclaude (matched "${blockedBy}").` } }));
        }
        hooks.onRequestEnd?.(reqId, { method: req.method, path: req.url, account: '(blocked)', status: 400, model, sessionId });
        return;
      }

      const ctx = { account: null, status: null, tried: new Set(), reauthed: new Set(), model, advisorModel, pinnedIndex, pinSoft, holdBudgetMs: holdMs, sessionId };
      // Hold the session "in flight" across the WHOLE request (incl. retries and
      // a multi-minute streaming completion) so it stays counted as active and
      // never expires mid-request.
      accountManager.beginSession(sessionId);
      try {
        await forwardRequest(req, res, body, accountManager, upstream, 0, hooks, reqId, ctx, logDir, sx);
      } catch (err) {
        ctx.status = ctx.status || 502;
        console.error('[TeamClaude] Unhandled error:', err);
        if (!res.headersSent) {
          res.writeHead(502, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ type: 'error', error: { type: 'proxy_error', message: 'Internal proxy error' } }));
        }
      } finally {
        accountManager.endSession(sessionId);
        if (!hideActivity) hooks.onRequestEnd?.(reqId, { method: req.method, path: req.url, account: ctx.account, status: ctx.status, model: ctx.model, sessionId, pinned: ctx.pinnedIndex != null });
      }
    } catch (err) {
      console.error('[TeamClaude] Unhandled error:', err);
    }
  };
}

// Per-request https.Agent tunneled through sx.org — one-shot (no keep-alive
// reuse, matching upstream-fetch.js's proxiedFetch), so a fresh sx tunnel is
// dialed for this connection only.
function sxAgent(sx, targetHost) {
  const proxy = sx.getProxy();
  const agent = new https.Agent({ keepAlive: false });
  agent.createConnection = (_options, cb) => {
    tunnelTls({ proxy, targetHost, targetPort: 443, tlsOptions: sx.tlsOptions || {} })
      .then((sock) => cb(null, sock))
      .catch((err) => cb(err));
    return undefined;
  };
  return agent;
}

/**
 * Relay a request to upstream with the client's OWN headers intact (including
 * its authorization) — used for Remote Control (/v1/code/*), whose event
 * stream is a long-poll: the client keeps the request open indefinitely and
 * the upstream may withhold response headers for minutes between events. No
 * buffering, no timeout, no reconstruction — just pipe bytes both ways as they
 * arrive, exactly like a transparent proxy would.
 */
function relayStream(req, res, upstream, sx) {
  const target = new URL(`${upstream}${req.url}`);
  const headers = {};
  for (const [key, value] of Object.entries(req.headers)) {
    const lk = key.toLowerCase();
    if (lk.startsWith(':') || HOP_BY_HOP_HEADERS.has(lk) || lk === 'accept-encoding') continue;
    headers[key] = value;
  }

  const useProxy = !!(sx?.useByDefault() && sx.isProvisioned());
  const agent = useProxy ? sxAgent(sx, target.hostname) : undefined;
  const transport = target.protocol === 'http:' ? http : https;

  const upstreamReq = transport.request(target, { method: req.method, headers, agent }, (upstreamRes) => {
    const responseHeaders = {};
    for (const [key, value] of Object.entries(upstreamRes.headers)) {
      if (CONNECTION_SPECIFIC_HEADERS.has(key) || key === 'content-encoding' || key === 'content-length') continue;
      responseHeaders[key] = value;
    }
    res.writeHead(upstreamRes.statusCode, responseHeaders);
    upstreamRes.pipe(res);
  });

  upstreamReq.on('error', (err) => {
    console.error('[TeamClaude] Remote Control relay error:', describeConnectError(err));
    if (!res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ type: 'error', error: { type: 'proxy_error', message: 'Upstream unreachable' } }));
    }
  });
  // Client disconnected (e.g. Claude Code closed the channel): tear down the
  // upstream side too instead of leaking an open connection.
  res.on('close', () => upstreamReq.destroy());

  if (['GET', 'HEAD'].includes(req.method)) upstreamReq.end();
  else req.pipe(upstreamReq);
}

/**
 * Relay a WebSocket upgrade (e.g. Remote Control's real-time
 * `/v1/session_ingress/ws/*` channel) to upstream with the client's own
 * headers intact. An HTTP server never emits 'request' for an Upgrade
 * handshake — only 'upgrade', with a raw socket instead of a response object —
 * so this needs its own relay rather than going through relayStream/res.
 * Reuses Node's http(s) client, which already knows how to speak the Upgrade
 * handshake (emits its own 'upgrade' event on a 101); once that fires it's
 * just two raw sockets spliced together.
 */
export function relayUpgrade(req, socket, head, upstream, sx) {
  const target = new URL(`${upstream}${req.url}`);
  const headers = {};
  for (const [key, value] of Object.entries(req.headers)) {
    const lk = key.toLowerCase();
    // Unlike relayStream, do NOT strip 'upgrade'/'connection' here — they ARE
    // the handshake. Only 'host' (the client transport reconstructs it from
    // `target`) and h2 pseudo-headers are dropped.
    if (lk.startsWith(':') || lk === 'host') continue;
    headers[key] = value;
  }

  const useProxy = !!(sx?.useByDefault() && sx.isProvisioned());
  const agent = useProxy ? sxAgent(sx, target.hostname) : undefined;
  const transport = target.protocol === 'http:' ? http : https;

  const upstreamReq = transport.request(target, { method: req.method, headers, agent });

  upstreamReq.on('upgrade', (upstreamRes, upstreamSocket, upstreamHead) => {
    const headerLines = Object.entries(upstreamRes.headers)
      .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join('\r\n');
    socket.write(`HTTP/1.1 ${upstreamRes.statusCode} ${upstreamRes.statusMessage}\r\n${headerLines}\r\n\r\n`);
    if (upstreamHead?.length) socket.write(upstreamHead);
    if (head?.length) upstreamSocket.write(head);
    socket.pipe(upstreamSocket);
    upstreamSocket.pipe(socket);
    // An upgraded socket defaults to half-open: the peer's FIN only ends the
    // READABLE side ('end'), it does NOT destroy the socket or fire 'close' —
    // so without this, one side hanging up (dropped wifi, killed CLI) leaves
    // the other socket open forever. destroy() is idempotent, so reacting to
    // both 'end' and 'close' on each side is a safe, redundant backstop.
    socket.on('end', () => upstreamSocket.destroy());
    upstreamSocket.on('end', () => socket.destroy());
    socket.on('close', () => upstreamSocket.destroy());
    upstreamSocket.on('close', () => socket.destroy());
    // The 101 detaches this socket from upstreamReq, so the request's 'error'
    // listener no longer covers it. A link that flaps mid-session then raises
    // 'error' (write EPIPE / read ECONNRESET) on a socket nobody listens to,
    // which Node escalates to an uncaught exception — one dropped WebSocket
    // would kill the proxy for every other session. Close the pair instead.
    upstreamSocket.on('error', () => socket.destroy());
  });

  upstreamReq.on('error', (err) => {
    console.error('[TeamClaude] Remote Control WebSocket relay error:', describeConnectError(err));
    socket.destroy();
  });
  socket.on('error', () => upstreamReq.destroy());

  upstreamReq.end();
}

/**
 * Relay a request to upstream with no header rewriting — pure passthrough.
 */
async function relayRaw(req, res, upstream, sx) {
  const bodyChunks = [];
  for await (const chunk of req) bodyChunks.push(chunk);
  const body = Buffer.concat(bodyChunks);

  try {
    const upstreamRes = await upstreamFetch(`${upstream}${req.url}`, {
      method: req.method,
      headers: {
        'content-type': req.headers['content-type'] || 'application/json',
        'accept': req.headers['accept'] || 'application/json',
        'user-agent': req.headers['user-agent'] || 'node',
      },
      body: body.length > 0 ? body : undefined,
    }, sx, sx?.useByDefault());

    const responseBody = await upstreamRes.text();
    const responseHeaders = {};
    for (const [key, value] of upstreamRes.headers.entries()) {
      // `.text()` already decompressed the body, so drop content-encoding and
      // the now-stale content-length (both refer to the compressed bytes) — else
      // a gzip'd upstream response reaches the client mis-framed / truncated.
      if (key === 'transfer-encoding' || key === 'connection' ||
          key === 'content-encoding' || key === 'content-length') continue;
      responseHeaders[key] = value;
    }
    res.writeHead(upstreamRes.status, responseHeaders);
    res.end(responseBody);
  } catch (err) {
    console.error('[TeamClaude] Raw relay error:', describeConnectError(err));
    if (!res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ type: 'error', error: { type: 'proxy_error', message: 'Upstream unreachable' } }));
    }
  }
}


function logTimestamp() {
  const d = new Date();
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}

// A per-request log that streams to disk as the request/response flow, instead
// of buffering the whole body in memory and writing once at the end. The file
// is opened on first write; header sections are written verbatim and bodies are
// streamed through BodyWriter (JSON pretty-printed on the fly, SSE/other raw),
// so even a ~1M-token response costs only the current chunk.
function openRequestLog(logDir, reqId) {
  const filename = `${logTimestamp()}_${String(reqId).padStart(5, '0')}.log`;
  const ws = createWriteStream(join(logDir, filename), { flags: 'a' });
  ws.on('error', (err) => console.error(`[TeamClaude] Failed to write log: ${err.message}`));
  let ended = false;
  const write = (s) => { if (!ended && s) ws.write(Buffer.from(String(s), 'latin1')); };
  return {
    write,
    // Stream a complete body buffer under a section header.
    body(label, buf, contentType) {
      if (!buf || !buf.length) { write(`\n\n=== ${label} ===\n(empty)`); return; }
      new BodyWriter(write, label, contentType || '').chunk(buf);
    },
    // A BodyWriter to append chunks incrementally (e.g. an SSE response).
    bodyWriter(label, contentType) { return new BodyWriter(write, label, contentType || ''); },
    end() { if (!ended) { ended = true; ws.end('\n'); } },
  };
}

function formatHeaders(headers) {
  if (headers.entries) {
    return [...headers.entries()].map(([k, v]) => `  ${k}: ${v}`).join('\n');
  }
  return Object.entries(headers).map(([k, v]) => `  ${k}: ${v}`).join('\n');
}

/**
 * What actually went wrong on a failed connect, as a string worth printing.
 *
 * Node's happy-eyeballs dialer (`autoSelectFamily`, on by default across the
 * versions this package supports; `package.json` declares `node >=20`, measured
 * here on 24) reports a connect where every address failed as an AggregateError.
 * Node builds that error with an empty `message`; the per-address reasons are in
 * `.errors`. Any multi-address host reaches this, and the upstream is one, so
 * `err.message` prints nothing for the failure operators most need to read.
 *
 * Looked for one level down as well, because `TEAMCLAUDE_UPSTREAM_GLOBAL_FETCH`
 * routes through global fetch, which wraps the same failure in a TypeError whose
 * own message is the equally unhelpful "fetch failed".
 *
 * The `err.message` fallback is required: with `autoSelectFamily` off, and on
 * every single-address failure, the reason arrives as a plain Error in
 * `message`. It also covers a wrapper whose `.cause` carries no reasons.
 */
export function describeConnectError(err) {
  const reasons = (e) => (Array.isArray(e?.errors) ? e.errors.map(c => c?.message).filter(Boolean) : []);
  const own = reasons(err);
  // A wrapper with a non-aggregated cause (global fetch's TypeError('fetch
  // failed') around a single-address connect error) still says only 'fetch
  // failed' by itself; the cause's message is the reason.
  return (own.length ? own : reasons(err?.cause)).join('; ') || err?.cause?.message || err?.message;
}

// Failures that say nothing about the ACCOUNT, only about the socket. Retrying
// can succeed where failing over cannot, and closing fast lets Node evict the
// dead socket so the client's retry reconnects cleanly. EPIPE joins the set as
// the write-side sibling of ECONNRESET.
//
// ECONNREFUSED sits here despite being arguably a property of the host. It is
// already unconditionally transient, so making it conditional converts every gap
// in that condition into a regression instead of leaving an unfixed case. One
// such gap was measurable before the other-host scan gated on selection's own
// eligibility predicate: a disabled account carrying its own `upstream` was
// never selected, never entered `ctx.tried`, and satisfied the condition
// indefinitely — a four-account fleet spent three accounts on a refused
// connection and answered rate_limit_error. That instance is closed; keeping
// ECONNREFUSED unconditional means any future gap stays a non-regression.
const SOCKET_TRANSIENT = new Set([
  'ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'EPIPE',
  'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT',
  'TEAMCLAUDE_HEADERS_TIMEOUT', 'TEAMCLAUDE_BODY_TIMEOUT',
]);

// Failures that are a property of the HOST being dialled: name resolution and
// routing. The hostname has no per-account component, so every account produces
// the same failure, and walking the fleet spends an upstream call per account to
// learn the same thing. The client is then told its quota is exhausted because a
// name would not resolve.
//
// Conditional, because an account may name its own `upstream` for a third-party
// backend. Where an untried account would dial a different host, this failure
// says nothing about that one, and failing over is correct.
const HOST_TRANSIENT = new Set(['ENOTFOUND', 'EAI_AGAIN', 'EHOSTUNREACH', 'ENETUNREACH', 'ENETDOWN']);

/**
 * Every error code a failure carries: its own, its `cause`'s, and its
 * children's. Node's global fetch puts the real error on `cause`, and the
 * happy-eyeballs dialer reports an all-addresses-failed connect as an
 * AggregateError that may carry no top-level code at all, with the reason
 * recorded once per address.
 */
function errorCodes(err) {
  const codes = [err?.code, err?.cause?.code];
  for (const child of err?.errors || []) codes.push(child?.code);
  for (const child of err?.cause?.errors || []) codes.push(child?.code);
  return codes.filter(Boolean);
}

/**
 * Should this upstream failure close the connection for the client to retry,
 * instead of being failed over to the next account?
 *
 * `otherHostAvailable` states whether an untried account would dial a different
 * host, which is what makes a host-scoped failure worth failing over. Exported
 * for its own tests.
 */
export function isTransientUpstreamError(err, { otherHostAvailable = false } = {}) {
  if (!(err instanceof Error)) return false;
  if (err.name === 'TimeoutError' || err.name === 'AbortError') return true;
  const codes = errorCodes(err);
  if (codes.some(c => SOCKET_TRANSIENT.has(c))) return true;
  if (codes.some(c => HOST_TRANSIENT.has(c))) return !otherHostAvailable;
  // Read last, and only once no code has been found. Node's global fetch, which
  // `TEAMCLAUDE_UPSTREAM_GLOBAL_FETCH` selects, reports every failure with this
  // message and the real error on `.cause`; checking it earlier would answer for
  // the whole transport before the codes above were consulted, so a host-scoped
  // failure there would never reach its conditional arm.
  if (typeof err.message === 'string' && err.message.includes('fetch failed')) return true;
  return false;
}

export async function forwardRequest(req, res, body, accountManager, upstream, retryCount, hooks, reqId, ctx, logDir, sx, useSx) {
  const maxRetries = accountManager.accounts.length;
  // This function is exported, so a caller may hand us a ctx built elsewhere.
  // The 401 path reads ctx.reauthed on every response; default it here rather
  // than trusting every construction site to include it.
  ctx.reauthed ??= new Set();
  // Whether THIS attempt dials via sx.org. Undefined on the first call → derive
  // from the default policy ('always' routes; 'off'/'429' start direct).
  const route = useSx === undefined ? !!(sx?.useByDefault()) : useSx;
  // ak-gateway: a HARD pin is this account or an error — never a failover,
  // a hop, or another account's quota. Every branch below that asks "is this
  // request pinned" means this one; a soft pin ranges over the fleet.
  const hardPinned = ctx.pinnedIndex != null && !ctx.pinSoft;

  // Select account, skipping any already tried (and failed) this request.
  // The model scopes availability so a Fable-exhausted account is skipped only
  // for Fable requests (it still serves other models).
  //
  // ak-gateway (the soft pin and the hop, in this order):
  //
  // A failover hop names its destination up front (ctx.hopTo, set by the 429
  // and 5xx hops below) and is one attempt long: consumed here so the attempt
  // after it, if any, selects normally. It was picked by pickAlternate against
  // this request's own exclusions and is taken as-is: re-selecting would walk
  // the fleet cursor onto it, which is exactly the move a detour must not make
  // (upstream eed3f40, #286). Never for a hard pin, and never onto an account
  // disabled since it was picked (engine.ts removes an account by disabling it).
  //
  // A HARD-pinned request (via /tc-acct/<name>) forces one exact account and
  // never rotates or fails over: once that account has been tried, `account` is
  // null and the caller gets the exhausted response rather than leaking to
  // another.
  //
  // A SOFT pin (/tc-prefer/<name>) is the opposite trade: bias to the preferred
  // account while it is eligible, then fall through to the ordinary rotation
  // walk. Because the fallthrough IS the normal path, every behavior hanging off
  // it — storm-control ramp, fleet retry-after, holdSeconds, the exhausted-fleet
  // probe — applies unchanged. `tried` still gates it, so a preferred account
  // that just failed this request is not re-picked in a retry loop.
  const hopTo = ctx.hopTo ?? null;
  ctx.hopTo = null;
  const hopTarget = hopTo != null && !hardPinned ? accountManager.accounts[hopTo] : null;
  let account;
  if (hopTarget && !hopTarget.disabled) {
    account = hopTarget;
  } else if (hardPinned) {
    account = ctx.tried.has(ctx.pinnedIndex) ? null : accountManager.accounts[ctx.pinnedIndex];
  } else {
    const preferred = ctx.pinSoft && ctx.pinnedIndex != null && !ctx.tried.has(ctx.pinnedIndex)
      ? accountManager.accounts[ctx.pinnedIndex]
      : null;
    account = (preferred && accountManager.isAvailable(preferred, ctx.model, ctx.advisorModel))
      ? preferred
      : accountManager.getActiveAccount(ctx.tried, ctx.model, ctx.advisorModel, ctx.sessionId);
  }
  if (!account) {
    // Every candidate was refused by upstream (403). Waiting will not help — the
    // account needs attention, not a retry — so say so plainly rather than
    // reporting a rate limit. Not a 403 either: the client's own credential is
    // fine, and a 403 would make it drop its login over someone else's problem.
    //
    // Only when the refusals are the WHOLE story, though. If some accounts were
    // refused and others are merely out of quota, a reset will still serve this
    // request — so fall through to the retry-after/hold path below rather than
    // failing fast on the strength of one bad credential. Reporting 502 there
    // would turn a recoverable exhaustion into a hard error, and silently skip
    // the holdSeconds wait an unattended run depends on.
    // A soft pin ranges over the whole fleet, so it takes the fleet-wide test —
    // only a HARD pin narrows "everything was refused" to its one account.
    //
    // ak-gateway: the fleet is the ENABLED accounts. A disabled one is never
    // tried, so it can never be refused; counting it — and engine.ts keeps a
    // removed account in place, disabled — made "every account was refused"
    // unreachable, and a 401/403 on the rest came back as an exhausted 429.
    const rejected = ctx.credentialRejected;
    const serving = accountManager.accounts.filter(a => !a.disabled).length;
    const allRefused = rejected?.size > 0 && (hardPinned
      ? rejected.has(accountManager.accounts[ctx.pinnedIndex]?.name)
      : rejected.size >= serving);
    if (allRefused) {
      const names = [...rejected].map(n => `"${n}"`).join(', ');
      ctx.status = 502;
      ctx.account = `(${[...rejected].join(', ')} refused)`;
      if (!res.headersSent) {
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          type: 'error',
          // ak-gateway: 401 or 403 — a 401 lands here too since the 401 failover.
          error: { type: 'proxy_error', message: `Upstream refused the credential for account ${names} (401/403). Check the account, then re-add it with: teamclaude login` },
        }));
      }
      return;
    }
    // A HARD-pinned request concerns exactly one account: don't compute a
    // fleet-wide retry-after or sleep on other accounts' windows — return
    // immediately. A soft pin falls past this into the normal exhausted-fleet
    // path, which is the whole point: it gets the real retry-after and the
    // holdSeconds wait, because by here every account really is spent.
    if (hardPinned) {
      ctx.status = 429;
      ctx.account = '(pinned account unavailable)';
      if (!res.headersSent) {
        res.writeHead(429, { 'Content-Type': 'application/json', 'retry-after': '5' });
        res.end(JSON.stringify({
          type: 'error',
          error: { type: 'rate_limit_error', message: 'Pinned account is unavailable (rate-limited, errored, or already tried). Retry shortly.' },
        }));
      }
      return;
    }
    ctx.status = 429;
    ctx.account = '(none available)';
    const status = accountManager.getStatus();
    const retryAfter = computeRetryAfter(status.accounts, accountManager.switchThreshold);

    // Long-hold mode: hold the HTTP connection and poll until an account
    // recovers or the budget (holdSeconds) runs out. Claude Code waits for
    // the first response byte, so this is transparent to the client as long
    // as API_TIMEOUT_MS on the Claude Code side is large enough.
    if (ctx.holdBudgetMs > 0) {
      // Cap the per-poll sleep to 60s so a newly-available account (e.g. one
      // manually enabled or whose quota reset early) is picked up within a
      // minute instead of sleeping the full retryAfter (often 3600s).
      const waitMs = Math.min(retryAfter * 1000, ctx.holdBudgetMs, 60_000);
      ctx.holdBudgetMs -= waitMs;
      console.log(`[TeamClaude] All accounts exhausted — holding connection, retry in ${Math.ceil(waitMs / 1000)}s (${Math.ceil(ctx.holdBudgetMs / 1000)}s budget left)`);
      await new Promise(resolve => setTimeout(resolve, waitMs));
      if (res.destroyed) return;
      return forwardRequest(req, res, body, accountManager, upstream, retryCount, hooks, reqId, ctx, logDir, sx, route);
    }

    const exhaustedRetries = ctx.exhaustedRetries || 0;
    if (exhaustedRetries < 1 && retryAfter <= INLINE_RETRY_AFTER_MAX_SECONDS) {
      ctx.exhaustedRetries = exhaustedRetries + 1;
      console.log(`[TeamClaude] All accounts exhausted — waiting ${retryAfter}s before retry`);
      await new Promise(resolve => setTimeout(resolve, retryAfter * 1000));
      if (res.destroyed) return;
      return forwardRequest(req, res, body, accountManager, upstream, retryCount, hooks, reqId, ctx, logDir, sx, route);
    }
    res.writeHead(429, {
      'Content-Type': 'application/json',
      'retry-after': String(retryAfter),
    });
    res.end(JSON.stringify({
      type: 'error',
      error: {
        type: 'rate_limit_error',
        // ak-gateway: the enabled accounts — a disabled or removed row was never a candidate.
        message: `All ${serving} accounts exhausted. Retry in ${retryAfter}s.`,
      },
    }));
    return;
  }

  // Track which account handles this request
  ctx.account = account.name;
  // Pin this session to the serving account (for affinity) and keep it "active"
  // in the running-sessions readout. Passive when distribution is off.
  accountManager.recordSession(ctx.sessionId, account.index);
  hooks.onRequestRouted?.(reqId, { account: account.name });

  // Refresh OAuth token if needed
  await accountManager.ensureTokenFresh(account.index);
  if (account.status === 'error' && retryCount < maxRetries) {
    ctx.tried.add(account.index);
    return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, route);
  }

  // Build upstream request headers
  const isOAuth = account.type === 'oauth';
  const headers = {};
  for (const [key, value] of Object.entries(req.headers)) {
    const lk = key.toLowerCase();
    // HTTP/2 pseudo-headers (:method, :path, :authority, :scheme) live in
    // req.headers on the h2 server path; fetch rejects `:`-prefixed names.
    if (lk.startsWith(':')) continue;
    if (HOP_BY_HOP_HEADERS.has(lk)) continue;
    if (lk === 'x-api-key') continue;
    // Strip accept-encoding: Node fetch auto-decompresses, which would
    // mismatch the Content-Encoding header we forward to the client
    if (lk === 'accept-encoding') continue;
    headers[key] = value;
  }

  if (isOAuth) {
    headers['authorization'] = `Bearer ${account.credential}`;
  } else {
    headers['x-api-key'] = account.credential;
  }

  const upstreamUrl = `${account.upstream || upstream}${req.url}`;
  const method = req.method;

  // Strip orphaned tool_use / tool_result blocks so a client that compacted or
  // interrupted a turn can't wedge the session with Anthropic's non-retryable
  // 400 ("tool_use ids were found without tool_result blocks"). No-op (same
  // Buffer) for a well-formed body.
  let sendBody = sanitizeToolPairs(body, req.url, req.headers['content-type']);
  // Align the body's account_uuid (in metadata.user_id) with the account whose
  // token we're injecting (same-length patch; no-op if absent).
  if (account.accountUuid) sendBody = patchAccountUuid(sendBody, account.accountUuid);
  // Rewrite the model name for accounts that target a different upstream (e.g.
  // GLM), which uses different model identifiers than Anthropic.
  if (account.modelMap) sendBody = rewriteModel(sendBody, account.modelMap);
  // If the body changed length (sanitize or model rewrite), update Content-Length
  // so the upstream doesn't receive a mismatched framing and truncate or stall.
  if (sendBody !== body) headers['content-length'] = String(sendBody.length);

  // Streaming request log, opened lazily on the first terminal outcome (a
  // pure-429-then-retry attempt writes no file, matching prior behavior). The
  // request head+body are written once, just before the response is logged.
  let log = null;
  let reqLogged = false;
  const getLog = () => (logDir ? (log ||= openRequestLog(logDir, reqId)) : null);
  const logRequestHead = () => {
    const l = getLog();
    if (!l || reqLogged) return;
    reqLogged = true;
    const safeHeaders = { ...headers };
    if (safeHeaders['x-api-key']) safeHeaders['x-api-key'] = safeHeaders['x-api-key'].slice(0, 15) + '...';
    if (safeHeaders['authorization']) safeHeaders['authorization'] = safeHeaders['authorization'].slice(0, 20) + '...';
    l.write(`=== REQUEST (account: ${account.name}, retry: ${retryCount}) ===\n${method} ${upstreamUrl}\n${formatHeaders(safeHeaders)}`);
    if (body.length > 0) l.body('REQUEST BODY', body, req.headers['content-type']);
  };

  try {
    // Storm control: pace requests onto a freshly-switched account so a failover
    // burst doesn't slam it all at once and cascade (issue #84). The slot is held
    // only until the response headers arrive — long enough to stagger the burst,
    // then released so streaming bodies don't tie up concurrency. Fail-open: a
    // client that disconnects while waiting just drops out.
    if (!await accountManager.admit(account.index, () => res.destroyed)) return;
    let upstreamRes;
    try {
      upstreamRes = await upstreamFetch(upstreamUrl, {
        method,
        headers,
        body: ['GET', 'HEAD'].includes(method) ? undefined : sendBody,
        redirect: 'manual',
      }, sx, route);
    } finally {
      accountManager.release(account.index);
    }

    // Extract rate limit headers
    const rateLimitHeaders = {};
    for (const [key, value] of upstreamRes.headers.entries()) {
      if (key.startsWith('anthropic-ratelimit-')) {
        rateLimitHeaders[key] = value;
      }
    }
    accountManager.updateQuota(account.index, rateLimitHeaders);

    // Any non-429 response is live proof a rate-limit hold no longer binds —
    // this is what lets a revalidation probe (a throttled account selected by
    // _selectProbe) clear its own hold and return the fleet to service.
    if (upstreamRes.status !== 429) accountManager.clearRateLimited(account.index);

    // Two kinds of 429 are handled differently below: a quota rejection rotates
    // to another account; a transient rate-limit throttle pauses + retries the
    // same account (never rotates — see #84).
    if (upstreamRes.status === 429) {
      // Clamp Retry-After to a sane window: missing/invalid falls back to 60s,
      // and out-of-range values are bounded to [1, 300]. A negative value would
      // otherwise bypass the wait cap — setTimeout returns immediately and a
      // pause/hold would be armed in the past.
      const retryAfterHeader = upstreamRes.headers.get('retry-after');
      let retryAfter = parseInt(retryAfterHeader, 10);
      if (Number.isNaN(retryAfter)) retryAfter = 60;
      // A 429 that says nothing about the account — no retry-after, no
      // anthropic-ratelimit-* — is about the REQUEST: a model id upstream
      // refuses, a shape it will not take. Neither of the account-level
      // responses below applies to it. Pausing the account made every other
      // session on it wait out a fabricated 60s for one client's bad model id,
      // and the inline wait then held that client for the same 60s per attempt;
      // together they turned one request's problem into a fleet-wide stall
      // (#288). A throttle, by contrast, always carries the headers.
      const requestScoped = retryAfterHeader == null && Object.keys(rateLimitHeaders).length === 0;
      // The body is diagnostic for a request-scoped refusal (it names the
      // reason) and noise otherwise.
      let refusal = '';
      if (requestScoped) {
        const raw = await readErrorBody(upstreamRes.body).catch(() => null);
        try { refusal = raw ? String(JSON.parse(raw.toString('utf8'))?.error?.message || '') : ''; } catch { refusal = ''; }
      } else {
        await upstreamRes.body?.cancel();
      }

      // Durable quota exhaustion vs. a transient rate limit. A "rejected" unified
      // status means a quota bucket is spent, so waiting and retrying the SAME
      // account is futile — switch to another account now (updateQuota above
      // already recorded the spent bucket's utilization from the headers).
      const rl = rateLimitHeaders;
      const generalRejected = rl['anthropic-ratelimit-unified-5h-status'] === 'rejected'
        || rl['anthropic-ratelimit-unified-7d-status'] === 'rejected';
      const fableRejected = rl['anthropic-ratelimit-unified-7d_oi-status'] === 'rejected' && !generalRejected;
      if ((generalRejected || fableRejected) && retryCount < maxRetries) {
        // A Fable-only rejection leaves the account fine for other models, so we
        // do NOT throttle it globally — the recorded Fable utilization makes
        // selection skip it for Fable requests only. A general rejection spends a
        // shared bucket, so hold the whole account for its reset window.
        if (fableRejected) {
          console.log(`[TeamClaude] Fable weekly exhausted on "${account.name}" — switching account for this Fable request`);
        } else {
          const hold = Math.min(Math.max(retryAfter, 1), 3600);
          console.log(`[TeamClaude] Quota rejection (429) on "${account.name}" — throttling ${hold}s and switching account`);
          accountManager.markRateLimited(account.index, hold);
        }
        ctx.tried.add(account.index);
        if (res.destroyed) return;
        return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, route);
      }

      retryAfter = Math.min(Math.max(retryAfter, 1), 300);

      // sx.org failover: 429s are IP-based, so retry via the proxy's egress IP.
      // 'always' is already on sx; '429' switches direct→sx now and skips the
      // wait (a fresh IP isn't throttled). Also arm the sticky window for MITM.
      const nextUseSx = !!(sx?.useOn429());
      const switchingToSx = nextUseSx && !route;
      // The sticky window routes every new MITM tunnel through sx.org for a
      // while, which is metered. A request-scoped 429 is not an IP limit, so it
      // does not arm it; the one-shot sx retry below still runs, in case an
      // IP-scoped limit ever presents without headers.
      if (!requestScoped) sx?.noteRateLimited(retryAfter);

      // This is a rate-limit 429 (per-minute throttle), NOT quota exhaustion —
      // quota rejection is handled above and is the only thing that rotates.
      // Do NOT switch accounts here: moving the burst to the next account just
      // throttles it too (thundering herd, #84) and discards this account's KV
      // cache. Instead PAUSE this account so concurrent requests wait in admit()
      // (capped, then released through a fresh ramp) instead of piling on, and
      // retry the SAME account. The pause never marks the account throttled, so
      // selection keeps choosing it.
      // Not for a request-scoped 429: the account is fine, and the pause is
      // exactly the fleet-wide stall #288 describes.
      if (!requestScoped) accountManager.pauseAccount(account.index, Math.min(retryAfter, RATE_LIMIT_ABSORB_MAX_SECONDS));

      // ONE bounded failover hop to an idle sibling (#137, #165, #156).
      //
      // #84's argument against rotating on a rate-limit 429 is that moving a
      // shared burst to the next account just throttles that one too and throws
      // away this account's KV cache. That holds under load. It does not hold
      // when a sibling is sitting idle, which is the case every reporter hit: a
      // three-account fleet stalling for 60s at a time on one throttled account
      // while another was at 9% weekly.
      //
      // So the hop is deliberately not a rotation policy: at most once per
      // request, never onto an account already tried, and never onto one inside
      // its own 429 pause — pauseAccount does not mark an account throttled, so
      // selection would otherwise happily hand back an account that is itself
      // waiting out a 429.
      //
      // The budget is one hop for a specific reason. If the SECOND account is
      // rate-limited too, the limit is almost certainly scoped to the egress IP
      // rather than to either account: every account leaves from the same
      // address, which is the premise the sx.org path below is built on. Hopping
      // further would prove nothing and pay a cold cache each time. After the
      // hop ctx.rateLimitHopped is set, this branch does not run again for this
      // request, and the sx fresh-IP retry and the inline wait take over —
      // which is the right response to an IP-scoped limit.
      //
      // ak-gateway: never for a hard pin — upstream's version hops a
      // /tc-acct/ request onto another account.
      if (!hardPinned && !ctx.rateLimitHopped && retryCount < maxRetries) {
        // pickAlternate, not getActiveAccount: the hop detours THIS request and
        // must leave the fleet cursor where it is (#286).
        const alt = accountManager.pickAlternate(
          new Set([...ctx.tried, account.index]), ctx.model, ctx.advisorModel,
        );
        if (alt && !accountManager.isPaused(alt.index)) {
          ctx.rateLimitHopped = true;
          ctx.tried.add(account.index);
          ctx.hopTo = alt.index;
          console.log(`[TeamClaude] Rate-limit 429 on "${account.name}" — failing over once to idle account "${alt.name}"`);
          if (res.destroyed) return;
          return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, route);
        }
      } else if (ctx.rateLimitHopped && requestScoped) {
        // Second headerless 429, on a different account: it followed the
        // request. Nothing here is about either account.
        //
        // That does not make it permanent. Measured on a live fleet: these land
        // about once every 8 minutes on Fable traffic, from four different
        // starting accounts, and the client's own retry usually succeeds — so
        // most are a transient the fleet cannot route around, not a model id
        // upstream refuses. Returning it straight away is what left Claude Code
        // sitting on "will retry in 2m 38s", with nothing in the session
        // transcript to explain the pause.
        //
        // So take one short retry first, on THIS account. Not on a third one:
        // the limit is scoped to neither account, so another hop would pay a
        // cold prompt cache to learn what the first hop already established —
        // the same argument that bounds the hop budget above. ctx.hopTo keeps
        // the attempt on the account it landed on and off the fleet cursor
        // (#286), and `route` keeps its egress, since the wait is the only
        // variable being tested. A fresh IP is a different hypothesis and the sx
        // retry below still owns it: when it is armed it goes first, for free,
        // and this retry takes the attempt after it.
        //
        // ctx.requestScopedRetried is the SAME flag the no-sibling retry below
        // sets: one headerless-429 wait per request, whichever of the two spends
        // it. A request that finds no sibling, retries, and only then finds one
        // to hop onto reaches both sites, and two flags would let it wait twice.
        //
        // A delay of 0 (TEAMCLAUDE_HEADERLESS_429_RETRY_DELAY_MS=0) turns the
        // retry off and the 429 goes back at once.
        const retryDelayMs = resolveHeaderless429RetryDelayMs();
        if (retryDelayMs > 0 && !ctx.requestScopedRetried && !switchingToSx && retryCount < maxRetries
          && !res.headersSent && !res.destroyed) {
          // Once per request: a retry that is refused too has made the point.
          ctx.requestScopedRetried = true;
          console.log(`[TeamClaude] 429 followed the request onto "${account.name}" with no rate-limit headers — retrying it once on the same account in ${retryDelayMs}ms`
            + (refusal ? ` (${safeLine(refusal)})` : ''));
          await new Promise(resolve => setTimeout(resolve, retryDelayMs));
          if (res.destroyed) return;
          ctx.hopTo = account.index;
          return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, route);
        }
        console.log(`[TeamClaude] 429 followed the request onto "${account.name}" with no rate-limit headers — it is about the request, not the accounts; returning it to the client`
          + (refusal ? ` (${safeLine(refusal)})` : ''));
      } else if (ctx.rateLimitHopped) {
        // Second 429 this request, on a different account. Say so once: the
        // operator chasing "why is my fleet throttled" is looking for exactly
        // this, and it points at the egress IP rather than at the accounts.
        console.log('[TeamClaude] Second account rate-limited too — the limit looks IP-scoped, not per-account'
          + (sx?.useOn429() ? '' : ' (sx.org mode "429" would retry from a fresh egress IP)'));
      }

      // ak-gateway: after a hop, the retries below stay on the account the
      // hop reached. Re-selecting would see the first account in `tried` and
      // walk the fleet cursor onto this one — the #286 flapping, one step later.
      if (ctx.rateLimitHopped) ctx.hopTo = account.index;

      // sx fresh-IP retry (still the same account) takes precedence over waiting.
      // Bounded by retryCount like the inline-wait path below, so a persistently
      // 429ing upstream can't loop forever through sx.
      if (switchingToSx && retryCount < maxRetries) {
        console.log(`[TeamClaude] 429 on "${account.name}" — retrying via sx.org (fresh egress IP)`);
        if (res.destroyed) return;
        return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, nextUseSx);
      }

      // A request-scoped 429 goes back to the client now. The hop above (and
      // the sx retry, for the IP-scoped case that might present the same way)
      // has had its chance; a second account saying the same thing about the
      // same request is the answer, and waiting a fabricated 60s to hear it a
      // third time is the other half of #288. With no sibling to hop to, one
      // short retry covers a momentary blip, and then it is the client's turn.
      // (ak-gateway: a hard pin has no sibling, so it gets this retry, on
      // its own account, and then the 429.)
      if (requestScoped) {
        // The same number as the post-hop retry above, and the same one-wait
        // budget: one phenomenon, one delay, one env var to move both — and 0
        // switches this retry off along with that one.
        const retryDelayMs = resolveHeaderless429RetryDelayMs();
        if (retryDelayMs > 0 && !ctx.rateLimitHopped && !ctx.requestScopedRetried && retryCount < maxRetries) {
          ctx.requestScopedRetried = true;
          console.log(`[TeamClaude] 429 with no rate-limit headers on "${account.name}" — retrying once in ${retryDelayMs}ms${refusal ? ` (${safeLine(refusal)})` : ''}`);
          await new Promise(resolve => setTimeout(resolve, retryDelayMs));
          if (res.destroyed) return;
          return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, nextUseSx);
        }
        // No fabricated retry-after: upstream sent none, and the client's own
        // backoff is a better guess than one invented here.
        ctx.status = 429;
        if (!res.headersSent && !res.destroyed) {
          res.writeHead(429, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ type: 'error', error: { type: 'rate_limit_error', message: refusal || 'Upstream refused this request (429) without rate-limit headers.' } }));
        }
        return;
      }

      // Absorb short waits inline on the same account — the client never sees the
      // 429. Bounded by retryCount (maxRetries = account count) so a persistently
      // rate-limited account can't loop forever tying up the connection.
      if (retryAfter <= RATE_LIMIT_ABSORB_MAX_SECONDS && retryCount < maxRetries) {
        console.log(`[TeamClaude] Rate-limit 429 on "${account.name}" — waiting ${retryAfter}s, retrying same account (no switch)`);
        await new Promise(resolve => setTimeout(resolve, retryAfter * 1000));
        if (res.destroyed) return;
        return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, nextUseSx);
      }
      ctx.hopTo = null;

      // Longer retry-after (or retries exhausted): don't hold the connection and
      // don't rotate — surface the 429 with retry-after so the client backs off.
      // The pause above keeps other requests off this account meanwhile.
      console.log(`[TeamClaude] Rate-limit 429 on "${account.name}" — retry-after ${retryAfter}s over inline cap; returning 429 to client (no switch)`);
      ctx.status = 429;
      if (!res.headersSent && !res.destroyed) {
        res.writeHead(429, { 'Content-Type': 'application/json', 'retry-after': String(retryAfter) });
        res.end(JSON.stringify({ type: 'error', error: { type: 'rate_limit_error', message: `Rate limited; retry in ${retryAfter}s.` } }));
      }
      return;
    }

    // A 401 means the credential we injected was rejected. For an OAuth account
    // that usually means the access token was revoked BEFORE its clock expiry —
    // something else refreshed the same token family, so upstream reports it
    // revoked while it still looks fresh locally. ensureTokenFresh's expiry
    // check cannot see that (it only compares the clock), so the account would
    // otherwise keep serving a dead token until the token aged out, and every
    // request in between would surface a 401 to the client with no recovery.
    // Force one refresh and retry. If the refresh is itself rejected the refresh
    // token is dead too: ensureTokenFresh marks the account errored, and the
    // retry's status check rotates to another account. Bounded to one re-auth
    // per account per request, so a genuinely dead credential surfaces the 401
    // instead of looping.
    // A 403 ("Request not allowed") is upstream refusing THIS account outright —
    // not a stale token a refresh could fix, and not anything the client sent.
    // The client never sees the credential we inject, so it cannot act on the
    // rejection; Claude Code reads a 403 as "your session is dead", drops its
    // own login and asks for a re-login over an account problem it has no part
    // in. Skip the account for the rest of this request and fail over. With no
    // account left, the no-account branch reports a proxy error instead.
    // Upstream 5xx — 529 "Overloaded" above all (#156). This is the provider
    // saying it cannot serve right now, not anything about this account's quota,
    // so surfacing it to the client turns a provider-side transient into a
    // client-visible failure — and Claude Code's own retry loop then re-piles the
    // same load onto the same account.
    //
    // One hop, on the same budget and for the same reason as the 429 path above:
    // if a second account is overloaded too, it is the provider that is
    // overloaded, not the account, and walking the fleet would just spend every
    // account's cache discovering that. After the hop the response goes to the
    // client as it does today, with its own retry-after intact.
    //
    // ak-gateway: never for a hard pin (upstream's hop leaks it).
    if (upstreamRes.status >= 500 && !res.headersSent && !hardPinned && !ctx.serverErrorHopped && retryCount < maxRetries) {
      // Same exclusion as the 429 hop, and the same cursor-preserving pick.
      const alt = accountManager.pickAlternate(
        new Set([...ctx.tried, account.index]), ctx.model, ctx.advisorModel,
      );
      if (alt && !accountManager.isPaused(alt.index)) {
        await upstreamRes.body?.cancel();
        ctx.serverErrorHopped = true;
        ctx.tried.add(account.index);
        ctx.hopTo = alt.index;
        console.log(`[TeamClaude] Upstream ${upstreamRes.status} on "${account.name}" — failing over once to "${alt.name}"`);
        if (res.destroyed) return;
        return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, route);
      }
    }

    if (upstreamRes.status === 403 && !res.headersSent) {
      await upstreamRes.body?.cancel();
      // A set, not a name: the no-account branch needs to tell "every account was
      // refused" (fail fast, nothing to wait for) from "this one was, others are
      // just out of quota" (still worth holding for a reset).
      (ctx.credentialRejected ??= new Set()).add(account.name);
      ctx.tried.add(account.index);
      console.error(`[TeamClaude] 403 on "${account.name}" — upstream refused the account credential`);
      return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, route);
    }

    if (upstreamRes.status === 401 && account.type === 'oauth' && account.refreshToken
        && retryCount < maxRetries && !ctx.reauthed.has(account.index)) {
      ctx.reauthed.add(account.index);
      await upstreamRes.body?.cancel();
      console.log(`[TeamClaude] 401 on "${account.name}" — token rejected; forcing refresh and retrying`);
      await accountManager.ensureTokenFresh(account.index, true);
      if (res.destroyed) return;
      return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, route);
    }

    // A 401 the branch above did not take, or one that came BACK after it. The
    // re-auth is gated on a stored refresh token, so an OAuth account without
    // one, and every API-key account, had no 401 handler at all: the answer was
    // relayed, the account stayed `active`, and the next request picked it
    // again — measured as 4,124 consecutive 401s from one account over twenty
    // hours while three healthy siblings served none of them (#412). Like the
    // 403 above, the client must not see it: Claude Code reads a 401 as its own
    // login having died. So the account is skipped for this request and the
    // request fails over. It also leaves rotation when nothing here can ever
    // repair it: an API key, or an OAuth account with no refresh token to try.
    // An account that DOES hold one only fails over. Its second 401 can be stale
    // news — the forced refresh is suppressed for a short floor after a
    // successful one (the refresh-storm guard), so the retry may have gone out
    // on the same token — and a later request gets to try the refresh again.
    //
    // ak-gateway: the case this port is for here. During a keeper hand-over
    // the booting gateway does not own the grants yet, so its forced refresh is
    // refused (engine.ts, a 503) and the retry goes out on the same dead token.
    // A HARD pin fails over to nothing: its one account is now tried, and the
    // no-account branch answers 502, never the 401.
    if (upstreamRes.status === 401 && !res.headersSent) {
      await upstreamRes.body?.cancel();
      if (account.type !== 'oauth' || !account.refreshToken) {
        accountManager.markCredentialRejected(account.index, account.type !== 'oauth'
          ? 'upstream rejected its API key (401)'
          : 'upstream rejected its token (401) and it has no refresh token');
      }
      (ctx.credentialRejected ??= new Set()).add(account.name);
      ctx.tried.add(account.index);
      console.error(`[TeamClaude] 401 on "${safeLine(account.name, 64)}"; failing over to another account`);
      if (res.destroyed) return;
      return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, route);
    }

    // Log the request head (once) followed by the response headers, streaming
    // to disk from here on.
    logRequestHead();
    getLog()?.write(`\n\n=== RESPONSE ${upstreamRes.status} ===\n${formatHeaders(upstreamRes.headers)}`);

    ctx.status = upstreamRes.status;

    // Build response headers (skip hop-by-hop and encoding headers). The
    // connection-specific names are also illegal on an HTTP/2 response — when
    // this runs behind the MITM's h2 server, writeHead would otherwise throw.
    const responseHeaders = {};
    for (const [key, value] of upstreamRes.headers.entries()) {
      if (CONNECTION_SPECIFIC_HEADERS.has(key)) continue;
      // Strip content-encoding/content-length since fetch may auto-decompress
      if (key === 'content-encoding' || key === 'content-length') continue;
      responseHeaders[key] = value;
    }

    res.writeHead(upstreamRes.status, responseHeaders);

    if (!upstreamRes.body) {
      const l = getLog();
      if (l) { l.write('\n\n=== RESPONSE BODY ===\n(empty)'); l.end(); }
      res.end();
      return;
    }

    const contentType = upstreamRes.headers.get('content-type') || '';
    const isStreaming = contentType.includes('text/event-stream');

    if (isStreaming) {
      // Stream each chunk straight to the log as it is relayed — never hold the
      // whole (potentially ~1M-token) SSE body in memory.
      const l = getLog();
      const bw = l ? l.bodyWriter('RESPONSE BODY (streamed)', contentType) : null;
      await streamResponse(upstreamRes.body, res, account.index, accountManager, bw);
      l?.end();
    } else {
      const buf = Buffer.from(await upstreamRes.arrayBuffer());
      extractUsageFromBody(buf, account.index, accountManager);
      const l = getLog();
      if (l) { l.body('RESPONSE BODY', buf, contentType); l.end(); }
      res.end(buf);
    }
  } catch (err) {
    console.error(`[TeamClaude] Upstream error (account "${account.name}"):`, describeConnectError(err));
    // ak-gateway: what failed, for the caller's trace. A transient failure
    // destroys the socket and sets no status, and the caller would otherwise
    // read a reset as the 200 a fresh response defaults to.
    ctx.failure = describeConnectError(err);

    logRequestHead();
    const l = getLog();
    if (l) { l.write(`\n\n=== ERROR ===\n${err.stack || err.message}`); l.end(); }

    // Would failing over dial anywhere else? Only an untried account pointing at
    // a different `upstream` makes that true, and it is what decides whether a
    // name-resolution failure is worth retrying elsewhere.
    //
    // "Anywhere else" means an account that could actually serve THIS request:
    // selection gates on routes and the disabled flag, so a different-host
    // account this request can never legally route to gives failover nothing to
    // reach. The check reuses the manager's own eligibility predicate rather
    // than restating route logic — and deliberately not getActiveAccount, which
    // can arm the probe cooldown as a side effect. Hosts are compared by
    // hostname, so a port or path difference does not masquerade as a second
    // host.
    //
    // A pinned request never fails over at all: once the pinned account has
    // been tried, selection returns null and the caller sends the informative
    // pinned-unavailable 429. Counting a pin as "somewhere else to go" keeps a
    // host failure on that path instead of a bare reset.
    //
    // ak-gateway: a HARD pin only. A soft pin (/tc-prefer/) ranges over the
    // whole fleet, so for it a host failure is the same as for rotation: reset
    // fast, unless an untried account dials a different host.
    //
    // The advisor model is deliberately NOT part of the eligibility check:
    // when no account satisfies both models, getActiveAccount degrades to
    // executor-only routing, so failover reaches every executor-eligible
    // account. Gating on the advisor here would call a reachable healthy host
    // "nowhere to go" and reset a request that selection would have served.
    const hostOf = (u) => { try { return new URL(u).hostname; } catch { return u; } };
    const thisHost = hostOf(account.upstream || upstream);
    const otherHostAvailable = hardPinned || accountManager.accounts.some(a =>
      a.index !== account.index && !ctx.tried.has(a.index) &&
      hostOf(a.upstream || upstream) !== thisHost &&
      accountManager._isAvailable(a, ctx.model));
    const isTransient = isTransientUpstreamError(err, { otherHostAvailable });

    // Transient network errors (including a stale-socket headers/body timeout):
    // close the connection and let the client retry. Failing over to another
    // account would not help (the poisoned fetch pool is process-wide), but the
    // fast failure lets Node evict the dead socket so the retry reconnects
    // cleanly. If headers were already sent (a mid-stream body timeout), destroy
    // is the only option — the client sees a broken response and retries.
    if (isTransient) {
      res.destroy();
      return;
    }

    // Any other thrown error is a transport/stream failure, NOT proof the
    // account's credentials are bad — a bad credential comes back as a 401
    // *response*, never a throw. So don't sideline the account (that would drop
    // a healthy account from rotation until a credential change). Instead skip
    // it for the rest of THIS request only and fail over to another account.
    if (retryCount < maxRetries && !res.headersSent) {
      ctx.tried.add(account.index);
      return forwardRequest(req, res, body, accountManager, upstream, retryCount + 1, hooks, reqId, ctx, logDir, sx, route);
    }
    ctx.status = 502;

    if (!res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        type: 'error',
        error: { type: 'proxy_error', message: `Upstream error: ${describeConnectError(err)}` },
      }));
    } else if (!res.writableEnded) {
      // Error after headers were already sent (mid-stream) and it wasn't
      // classified transient: we can't send a status or fail over, and
      // streamResponse deliberately skipped res.end(). Destroy so the client
      // sees a broken response and retries instead of hanging on an open socket.
      res.destroy();
    }
  }
}

// Idle deadline for the RESPONSE BODY, complementing the headers timeout in
// upstream-fetch.js. The headers guard only covers time-to-first-byte; once
// headers arrive it is disarmed, so a network drop AFTER the stream starts would
// otherwise hang the read forever (the SSE completion just goes silent mid-way).
// This watchdog resets on every chunk, so a long but healthy stream is never
// cut — it fires only when the socket produces nothing for the whole window,
// converting a mid-stream hang into a fast failure that evicts the dead socket
// (reader.cancel destroys the underlying connection on both the direct-fetch and
// the sx-tunnel path, since both hand back a web ReadableStream). Override with
// TEAMCLAUDE_UPSTREAM_BODY_TIMEOUT_MS.
const DEFAULT_BODY_IDLE_TIMEOUT_MS = 120_000;

function resolveBodyIdleTimeout() {
  const env = Number(process.env.TEAMCLAUDE_UPSTREAM_BODY_TIMEOUT_MS);
  return env > 0 ? env : DEFAULT_BODY_IDLE_TIMEOUT_MS;
}

// Race a single reader.read() against an inactivity deadline. Resolves to the
// read result, or rejects with a transient TEAMCLAUDE_BODY_TIMEOUT if no chunk
// arrives within `ms`. The pending read is abandoned on timeout; the caller
// cancels the reader (evicting the socket) in its finally block.
export function readWithIdleTimeout(reader, ms) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const err = new Error(`upstream stream idle for ${ms}ms`);
      err.code = 'TEAMCLAUDE_BODY_TIMEOUT';
      reject(err);
    }, ms);
    timer.unref?.();
  });
  const read = reader.read();
  // If the timeout wins the race, `read` is abandoned; swallow any later
  // rejection so it can't surface as an unhandledRejection.
  read.catch(() => {});
  return Promise.race([read, timeout]).finally(() => clearTimeout(timer));
}

/**
 * Stream an SSE response to the client, parsing usage data along the way.
 */
async function streamResponse(webStream, res, accountIndex, accountManager, bodyWriter) {
  const reader = webStream.getReader();
  const idleMs = resolveBodyIdleTimeout();
  const decoder = new TextDecoder();
  let sseBuffer = '';
  let errored = false;

  try {
    while (true) {
      const { done, value } = await readWithIdleTimeout(reader, idleMs);
      if (done) break;

      // Client disconnected — stop reading from upstream
      if (res.destroyed) break;

      // Forward chunk immediately
      const ok = res.write(value);

      // Append to the log as it streams (no whole-body buffering)
      if (bodyWriter) bodyWriter.chunk(Buffer.from(value));

      const text = decoder.decode(value, { stream: true });

      // Parse SSE events for usage tracking
      sseBuffer += text;
      const events = sseBuffer.split('\n\n');
      sseBuffer = events.pop(); // keep incomplete event

      for (const event of events) {
        parseSSEUsage(event, accountIndex, accountManager);
      }

      // Handle backpressure — also bail out if client disconnects,
      // because 'drain' will never fire on a destroyed socket
      if (!ok) {
        await new Promise(resolve => {
          // Remove BOTH listeners when either fires: otherwise the un-fired one
          // (usually 'close') stays attached and accumulates one leaked listener
          // per backpressure cycle over a long SSE stream to a slow client.
          const done = () => { res.off('drain', done); res.off('close', done); resolve(); };
          res.once('drain', done);
          res.once('close', done);
        });
        if (res.destroyed) break;
      }
    }

    // Parse any remaining buffer
    if (sseBuffer.trim()) {
      parseSSEUsage(sseBuffer, accountIndex, accountManager);
    }
  } catch (err) {
    // A mid-stream idle timeout (or any read error) means the upstream went
    // silent after headers. Rethrow to the caller's transient handler, which
    // destroys the client connection so the truncated stream is NOT ended
    // cleanly (a clean res.end() would look like a complete response and
    // suppress the client's retry). reader.cancel() in finally evicts the socket.
    errored = true;
    throw err;
  } finally {
    // Cancel upstream reader to stop consuming data nobody needs (and, on the
    // timeout path, to destroy the dead socket so the pool drops it).
    reader.cancel().catch(() => {});
    if (!errored && !res.writableEnded) res.end();
  }
}

function parseSSEUsage(event, accountIndex, accountManager) {
  const dataLine = event.split('\n').find(l => l.startsWith('data: '));
  if (!dataLine) return;

  try {
    const data = JSON.parse(dataLine.slice(6));
    if (data.type === 'message_start' && data.message?.usage) {
      accountManager.updateUsage(accountIndex, data.message.usage.input_tokens, 0);
    } else if (data.type === 'message_delta' && data.usage) {
      accountManager.updateUsage(accountIndex, 0, data.usage.output_tokens);
    }
  } catch {
    // not valid JSON, skip
  }
}

function extractUsageFromBody(buffer, accountIndex, accountManager) {
  try {
    const json = JSON.parse(buffer.toString());
    if (json.usage) {
      accountManager.updateUsage(accountIndex, json.usage.input_tokens, json.usage.output_tokens);
    }
  } catch {
    // not JSON or no usage
  }
}

// Rewrite the `model` field in a JSON request body using a per-account map.
// Returns the original buffer unchanged if the model isn't in the map or the
// body isn't valid JSON, so non-messages endpoints pass through safely.
// Exported for tests.
export function rewriteModel(body, modelMap) {
  try {
    const obj = JSON.parse(body.toString('utf8'));
    if (obj.model && modelMap[obj.model]) {
      obj.model = modelMap[obj.model];
      return Buffer.from(JSON.stringify(obj), 'utf8');
    }
  } catch { /* not JSON — pass through unchanged */ }
  return body;
}

// ak-gateway: a minimal take on upstream 189ec72 (#429). Only the clocks an
// OAuth fleet actually runs on were missing: `rateLimitedUntil || resetsAt` is
// null for a subscription account spent past the switch threshold, so every
// "all accounts exhausted" answered 60 s against a five-hour or weekly window,
// and Claude Code retried every minute into a wall. Now, per account that could
// serve (enabled, not errored), the LATEST of the clocks that block it — a live
// hold, the 5h window over the threshold, a weekly bucket over it, an API-key
// reset — since it serves again only once all of them have passed; across the
// fleet, the SOONEST of those. An account with no known clock counts as 60 s,
// the old answer. Exported for its own test.
export function computeRetryAfter(accounts, threshold = 0.98, now = Date.now()) {
  const at = (v) => {
    if (v == null) return null;
    const ms = typeof v === 'number' ? v : new Date(v).getTime();
    return Number.isFinite(ms) && ms > now ? ms : null;
  };
  let soonest = Infinity;
  for (const acct of accounts) {
    if (acct.disabled || acct.status === 'error') continue;
    const q = acct.quota || {};
    const blocking = [at(acct.rateLimitedUntil), at(q.resetsAt)];
    if (q.unified5h != null && q.unified5h >= threshold) blocking.push(at(q.unified5hReset));
    for (const [level, reset] of [['unified7d', 'unified7dReset'], ['unified7dSonnet', 'unified7dSonnetReset'], ['unified7dFable', 'unified7dFableReset']]) {
      if (q[level] != null && q[level] >= threshold) blocking.push(at(q[reset]));
    }
    const known = blocking.filter((ms) => ms != null);
    const until = known.length ? Math.max(...known) : now + 60_000;
    if (until < soonest) soonest = until;
  }
  return soonest === Infinity ? 60 : Math.max(1, Math.ceil((soonest - now) / 1000));
}
