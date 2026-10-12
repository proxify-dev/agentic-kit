// ak-gateway: the pin rule, through every failover the upstream ports added.
//
// A HARD pin (/tc-acct/<name>, `claude --account X`) is that account or an
// error — it never fails over, hops, or leaks onto another account's quota. A
// SOFT pin (/tc-prefer/<name>) is a preference: it ranges over the fleet like
// rotation does. Upstream v1.1.22 has no soft pin, and its hops outrank its hard
// pin (a 529 on the pinned account is served by another one) — so every port
// that added a failover path is pinned here against both.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

process.env.TEAMCLAUDE_CONFIG = join(mkdtempSync(join(tmpdir(), 'tc-pinhop-')), 'config.json');

const { AccountManager } = await import('../../src/engine/account-manager.js');
const { createProxyServer } = await import('../../src/engine/server.js');
const { setUpstreamProxy, resolveUpstreamProxy, resetUpstreamProxy } = await import('../../src/engine/upstream-proxy.js');

const UNRESOLVABLE = 'https://does-not-exist.invalid';

function listen(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
}

function send(port, path = '/v1/messages') {
  return new Promise((resolve) => {
    const req = http.request({ host: '127.0.0.1', port, method: 'POST', path, headers: { 'content-type': 'application/json' } }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { body += c; });
      res.on('end', () => resolve({ type: 'response', status: res.statusCode, headers: res.headers, body }));
    });
    req.on('error', (err) => resolve({ type: 'error', code: err.code }));
    req.end(JSON.stringify({ model: 'claude-sonnet-5', messages: [{ role: 'user', content: 'hi' }] }));
  });
}

/** An upstream that answers by script and records which account's key it saw. */
async function upstream(script) {
  const seen = [];
  const srv = http.createServer((req, res) => {
    req.resume();
    req.on('end', () => {
      seen.push(req.headers['x-api-key']);
      script(seen.length, req.headers['x-api-key'], res);
    });
  });
  const port = await listen(srv);
  return { url: `http://127.0.0.1:${port}`, seen, close: () => new Promise(r => srv.close(r)) };
}

const ok = (res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"ok":true}'); };
// A per-minute throttle: it carries retry-after and the rate-limit headers.
const throttle = (res) => {
  res.writeHead(429, { 'retry-after': '1', 'anthropic-ratelimit-unified-status': 'allowed', 'content-type': 'application/json' });
  res.end('{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}');
};
const overloaded = (res) => {
  res.writeHead(529, { 'content-type': 'application/json' });
  res.end('{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}');
};

const keys = (...names) => names.map(n => ({ name: n, type: 'apikey', apiKey: `k-${n}` }));

async function fleet(names, script) {
  const up = await upstream(script);
  const am = new AccountManager(keys(...names), 0.98);
  const proxy = createProxyServer(am, { proxy: { apiKey: 'k' }, upstream: up.url }, {});
  const port = await listen(proxy);
  return { am, up, port, close: async () => { proxy.close(); await up.close(); } };
}

test.afterEach(() => resetUpstreamProxy());

// ── DNS / host-level connect failures (18b0360) ──────────────────────────────

test('hard pin + DNS failure: the pinned-unavailable 429, even with a different-host account to go to', async () => {
  setUpstreamProxy(resolveUpstreamProxy({ upstreamProxy: false }, {}));
  const good = await upstream((_n, _k, res) => ok(res));
  const am = new AccountManager([
    { name: 'alpha', type: 'apikey', apiKey: 'k-alpha', upstream: UNRESOLVABLE },
    { name: 'beta', type: 'apikey', apiKey: 'k-beta', upstream: good.url },
  ], 0.98);
  const proxy = createProxyServer(am, { proxy: { apiKey: 'k' } }, {});
  const port = await listen(proxy);
  try {
    const out = await send(port, '/tc-acct/alpha/v1/messages');
    assert.equal(out.type, 'response');
    assert.equal(out.status, 429);
    assert.match(out.body, /pinned account is unavailable/i);
    assert.deepEqual(good.seen, [], 'nothing leaked onto beta');
  } finally {
    proxy.close();
    await good.close();
  }
});

test('soft pin + DNS failure on a one-host fleet: a fast reset, not a walk through the fleet', async () => {
  setUpstreamProxy(resolveUpstreamProxy({ upstreamProxy: false }, {}));
  const am = new AccountManager([
    { name: 'alpha', type: 'apikey', apiKey: 'k-alpha' },
    { name: 'beta', type: 'apikey', apiKey: 'k-beta' },
  ], 0.98);
  const proxy = createProxyServer(am, { proxy: { apiKey: 'k' }, upstream: UNRESOLVABLE }, {});
  const port = await listen(proxy);
  try {
    const out = await send(port, '/tc-prefer/alpha/v1/messages');
    assert.equal(out.type, 'error', `expected the connection reset, got ${JSON.stringify(out)}`);
    assert.equal(am.currentIndex, 0, 'no account switch for a failure that is about the host');
  } finally {
    proxy.close();
  }
});

test('soft pin + DNS failure: fails over to an account that dials a different host', async () => {
  setUpstreamProxy(resolveUpstreamProxy({ upstreamProxy: false }, {}));
  const good = await upstream((_n, _k, res) => ok(res));
  const am = new AccountManager([
    { name: 'alpha', type: 'apikey', apiKey: 'k-alpha', upstream: UNRESOLVABLE },
    { name: 'beta', type: 'apikey', apiKey: 'k-beta', upstream: good.url },
  ], 0.98);
  const proxy = createProxyServer(am, { proxy: { apiKey: 'k' } }, {});
  const port = await listen(proxy);
  try {
    const out = await send(port, '/tc-prefer/alpha/v1/messages');
    assert.equal(out.status, 200);
    assert.deepEqual(good.seen, ['k-beta']);
  } finally {
    proxy.close();
    await good.close();
  }
});

// ── the one bounded hop on 429 and 5xx (f603ee0, eed3f40) ──────────────────────

test('hard pin + rate-limit 429: every attempt stays on the pinned account', async () => {
  const f = await fleet(['alpha', 'beta'], (_n, key, res) => (key === 'k-alpha' ? throttle(res) : ok(res)));
  try {
    const out = await send(f.port, '/tc-acct/alpha/v1/messages');
    assert.equal(out.status, 429);
    assert.ok(f.up.seen.length >= 1);
    assert.deepEqual([...new Set(f.up.seen)], ['k-alpha'], 'beta was never touched');
  } finally { await f.close(); }
});

test('hard pin + 529: the overload reaches the client from the pinned account alone', async () => {
  const f = await fleet(['alpha', 'beta'], (_n, key, res) => (key === 'k-alpha' ? overloaded(res) : ok(res)));
  try {
    const out = await send(f.port, '/tc-acct/alpha/v1/messages');
    assert.equal(out.status, 529);
    assert.deepEqual(f.up.seen, ['k-alpha']);
  } finally { await f.close(); }
});

test('soft pin + rate-limit 429: hops once to the idle sibling', async () => {
  const f = await fleet(['alpha', 'beta'], (_n, key, res) => (key === 'k-alpha' ? throttle(res) : ok(res)));
  try {
    const out = await send(f.port, '/tc-prefer/alpha/v1/messages');
    assert.equal(out.status, 200);
    assert.deepEqual(f.up.seen, ['k-alpha', 'k-beta']);
  } finally { await f.close(); }
});

test('soft pin + 529: hops once to the sibling', async () => {
  const f = await fleet(['alpha', 'beta'], (_n, key, res) => (key === 'k-alpha' ? overloaded(res) : ok(res)));
  try {
    const out = await send(f.port, '/tc-prefer/alpha/v1/messages');
    assert.equal(out.status, 200);
    assert.deepEqual(f.up.seen, ['k-alpha', 'k-beta']);
  } finally { await f.close(); }
});

test('after a hop, the retries stay on the account it reached: no third account, the cursor unmoved', async () => {
  // alpha throttles, the hop reaches beta, beta throttles too (retry-after 1,
  // inside the inline cap). Re-selecting for that retry would exclude alpha and
  // walk the cursor onto beta or gamma; the retry must stay on beta.
  const f = await fleet(['alpha', 'beta', 'gamma'], (_n, key, res) => (key === 'k-gamma' ? ok(res) : throttle(res)));
  try {
    const out = await send(f.port);
    assert.equal(out.status, 429);
    assert.deepEqual([...new Set(f.up.seen)], ['k-alpha', 'k-beta'], 'gamma was never drawn in');
    assert.equal(f.am.currentIndex, 0, 'a detour moves no cursor');
  } finally { await f.close(); }
});

test('forwardRequest: a hopTo never overrides a hard pin, and never lands on a disabled account', async () => {
  const { forwardRequest } = await import('../../src/engine/server.js');
  const serveWith = async (ctx, disable) => {
    const f = await fleet(['alpha', 'beta', 'gamma'], (_n, _k, res) => ok(res));
    if (disable != null) f.am.setDisabled(disable, true);
    const srv = http.createServer((req, res) => {
      const chunks = [];
      req.on('data', c => chunks.push(c));
      req.on('end', () => forwardRequest(req, res, Buffer.concat(chunks), f.am, f.up.url, 0, {}, 'r1', { tried: new Set(), model: null, advisorModel: null, sessionId: null, pinSoft: false, holdBudgetMs: 0, ...ctx }, null, null, false));
    });
    const port = await listen(srv);
    try { await send(port); return f.up.seen; } finally { srv.close(); await f.close(); }
  };
  assert.deepEqual(await serveWith({ pinnedIndex: 0, hopTo: 1 }), ['k-alpha'], 'the hard pin wins');
  assert.deepEqual(await serveWith({ pinnedIndex: null, hopTo: 1 }, 1), ['k-alpha'], 'a disabled hop target is skipped');
  assert.deepEqual(await serveWith({ pinnedIndex: null, hopTo: 2 }), ['k-gamma'], 'an eligible hop target is taken as-is');
});

// ── the headerless 429 (e0c9e9d, 8b9a54a) ─────────────────────────────────────

const headerless = (res) => {
  res.writeHead(429, { 'content-type': 'application/json' });
  res.end('{"type":"error","error":{"type":"rate_limit_error","message":"This model is not available."}}');
};

test('hard pin + headerless 429: no hop, one short retry on the pinned account, then the 429 with the reason and no retry-after', async () => {
  process.env.TEAMCLAUDE_HEADERLESS_429_RETRY_DELAY_MS = '20';
  const f = await fleet(['alpha', 'beta'], (_n, key, res) => (key === 'k-alpha' ? headerless(res) : ok(res)));
  try {
    const out = await send(f.port, '/tc-acct/alpha/v1/messages');
    assert.equal(out.status, 429);
    assert.equal(out.headers['retry-after'], undefined, 'no fabricated retry-after');
    assert.match(out.body, /This model is not available/);
    assert.deepEqual(f.up.seen, ['k-alpha', 'k-alpha']);
    assert.equal(f.am.isPaused(0), false, 'a request-scoped 429 pauses nothing');
  } finally { delete process.env.TEAMCLAUDE_HEADERLESS_429_RETRY_DELAY_MS; await f.close(); }
});

test('soft pin + headerless 429: hops to the sibling', async () => {
  process.env.TEAMCLAUDE_HEADERLESS_429_RETRY_DELAY_MS = '20';
  const f = await fleet(['alpha', 'beta'], (_n, key, res) => (key === 'k-alpha' ? headerless(res) : ok(res)));
  try {
    const out = await send(f.port, '/tc-prefer/alpha/v1/messages');
    assert.equal(out.status, 200);
    assert.deepEqual(f.up.seen, ['k-alpha', 'k-beta']);
  } finally { delete process.env.TEAMCLAUDE_HEADERLESS_429_RETRY_DELAY_MS; await f.close(); }
});

test('a headerless 429 does not arm the sx.org sticky window; a throttle does', async () => {
  process.env.TEAMCLAUDE_HEADERLESS_429_RETRY_DELAY_MS = '0';
  const { forwardRequest } = await import('../../src/engine/server.js');
  const armed = [];
  const sx = { useByDefault: () => false, useOn429: () => false, noteRateLimited: (s) => armed.push(s) };
  const run = async (script) => {
    const f = await fleet(['alpha'], (_n, _k, res) => script(res));
    const srv = http.createServer((req, res) => {
      const chunks = [];
      req.on('data', c => chunks.push(c));
      req.on('end', () => forwardRequest(req, res, Buffer.concat(chunks), f.am, f.up.url, 0, {}, 'r1', { tried: new Set(), model: null, advisorModel: null, sessionId: null, pinnedIndex: null, pinSoft: false, holdBudgetMs: 0 }, null, sx, false));
    });
    const port = await listen(srv);
    try { return await send(port); } finally { srv.close(); await f.close(); }
  };
  try {
    assert.equal((await run(headerless)).status, 429);
    assert.deepEqual(armed, [], 'a request-scoped 429 is not an IP limit');
    await run((res) => { res.writeHead(429, { 'retry-after': '120', 'anthropic-ratelimit-unified-status': 'allowed' }); res.end('{}'); });
    assert.deepEqual(armed, [120]);
  } finally { delete process.env.TEAMCLAUDE_HEADERLESS_429_RETRY_DELAY_MS; }
});

// ── a second 401 fails over (72c3943) ──────────────────────────────────────────

async function oauthFleet(names, script, { disabled = [] } = {}) {
  const seen = [];
  const srv = http.createServer((req, res) => {
    req.resume();
    req.on('end', () => { const tok = (req.headers.authorization || '').replace(/^Bearer /, ''); seen.push(tok); script(tok, res); });
  });
  const upPort = await listen(srv);
  let refreshes = 0;
  const am = new AccountManager(names.map(n => ({ name: n, type: 'oauth', accessToken: `${n}-1`, refreshToken: `r-${n}`, expiresAt: Date.now() + 3600_000, disabled: disabled.includes(n) })), 0.98, {
    refreshFn: async (rt) => { refreshes++; const n = rt.slice(2); return { accessToken: `${n}-${refreshes + 1}`, refreshToken: rt, expiresAt: Date.now() + 3600_000 }; },
  });
  const proxy = createProxyServer(am, { proxy: { apiKey: 'k' }, upstream: `http://127.0.0.1:${upPort}` }, {});
  const port = await listen(proxy);
  return { am, seen, port, refreshes: () => refreshes, close: async () => { proxy.close(); await new Promise(r => srv.close(r)); } };
}
const unauthorized = (res) => { res.writeHead(401, { 'content-type': 'application/json' }); res.end('{"type":"error","error":{"type":"authentication_error"}}'); };

test('hard pin + a 401 the refresh cannot cure: one refresh, then a 502 — never another account, never the 401', async () => {
  const f = await oauthFleet(['alpha', 'beta'], (tok, res) => (tok.startsWith('alpha') ? unauthorized(res) : ok(res)));
  try {
    const out = await send(f.port, '/tc-acct/alpha/v1/messages');
    assert.equal(out.status, 502);
    assert.equal(f.refreshes(), 1);
    assert.ok(f.seen.every(t => t.startsWith('alpha')), `beta was touched: ${f.seen}`);
  } finally { await f.close(); }
});

test('soft pin + a 401 the refresh cannot cure: served by the next account', async () => {
  const f = await oauthFleet(['alpha', 'beta'], (tok, res) => (tok.startsWith('alpha') ? unauthorized(res) : ok(res)));
  try {
    const out = await send(f.port, '/tc-prefer/alpha/v1/messages');
    assert.equal(out.status, 200);
    assert.ok(f.seen.at(-1).startsWith('beta'));
    assert.notEqual(f.am.accounts[0].status, 'error', 'it holds a refresh token: only failed over, a later request may repair it');
  } finally { await f.close(); }
});

test('a disabled row does not turn "every account was refused" into an exhausted 429', async () => {
  const f = await oauthFleet(['alpha', 'beta', 'gone'], (_tok, res) => unauthorized(res), { disabled: ['gone'] });
  try {
    const out = await send(f.port);
    assert.equal(out.status, 502);
    assert.match(out.body, /refused the credential/);
  } finally { await f.close(); }
});
