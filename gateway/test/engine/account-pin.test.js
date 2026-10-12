import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { AccountManager } from '../../src/engine/account-manager.js';
import { createProxyServer, resolveAccountPin } from '../../src/engine/server.js';

function listen(server) {
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
}

function oauth(name) {
  return { name, type: 'oauth', accessToken: 't-' + name, refreshToken: 'r', expiresAt: Date.now() + 3600_000 };
}

// ── resolveAccountPin (unit) ─────────────────────────────────────────────────

const withIds = (name, accountUuid, orgUuid) => ({ ...oauth(name), accountUuid, orgUuid });

test('resolveAccountPin matches by accountUuid, orgUuid, name and email', () => {
  const am = new AccountManager([
    withIds('me@x.com (Acme)', 'AAA', 'O1'),
    withIds('me@x.com (Beta)', 'AAA', 'O2'),
    withIds('other@x.com', 'BBB', 'O3'),
  ], 0.98);
  assert.equal(resolveAccountPin(am, 'BBB'), 2);              // accountUuid
  assert.equal(resolveAccountPin(am, 'O2'), 1);               // orgUuid
  assert.equal(resolveAccountPin(am, 'me@x.com (Beta)'), 1);  // display name
  assert.equal(resolveAccountPin(am, 'other@x.com'), 2);      // bare email
  assert.equal(resolveAccountPin(am, 'BbB'), 2);              // case-insensitive
});

// One account across several orgs shares an accountUuid, so the qualified form
// is the only way to name the second one; a bare uuid takes the first match.
test('accountUuid/orgUuid selects one account among an org set', () => {
  const am = new AccountManager([
    withIds('me@x.com (Acme)', 'AAA', 'O1'),
    withIds('me@x.com (Beta)', 'AAA', 'O2'),
  ], 0.98);
  assert.equal(resolveAccountPin(am, 'AAA/O2'), 1);
  assert.equal(resolveAccountPin(am, 'AAA/O1'), 0);
  assert.equal(resolveAccountPin(am, 'AAA'), 0);       // first match wins
  assert.equal(resolveAccountPin(am, 'me@x.com'), 0);  // ditto for the email
});

// The rotation index is array position: deleting an account would repoint every
// later pin at a DIFFERENT account, so it is not an accepted pin form.
test('resolveAccountPin does not accept a rotation index', () => {
  const am = new AccountManager([oauth('alpha'), oauth('beta')], 0.98);
  assert.equal(resolveAccountPin(am, '0'), null);
  assert.equal(resolveAccountPin(am, '1'), null);
});

test('resolveAccountPin returns null for an unknown token', () => {
  const am = new AccountManager([oauth('alpha')], 0.98);
  assert.equal(resolveAccountPin(am, 'nope'), null);
  assert.equal(resolveAccountPin(am, ''), null);
  assert.equal(resolveAccountPin(am, '9'), null);
});

test('an account literally named "0" still resolves by name', () => {
  const am = new AccountManager([oauth('x'), { ...oauth('y'), name: '0' }], 0.98);
  assert.equal(resolveAccountPin(am, '0'), 1);
});

// ── end-to-end pin routing (integration) ─────────────────────────────────────

// Stand up a mock upstream that records the path and Authorization it received,
// so we can prove which account a pinned request was routed to and that the
// /tc-acct/<pin> prefix was stripped before forwarding.
async function withProxy(run) {
  const seen = [];
  const upstream = http.createServer((req, res) => {
    seen.push({ path: req.url, auth: req.headers.authorization });
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
  });
  const upstreamPort = await listen(upstream);

  const am = new AccountManager([oauth('alpha'), oauth('beta')], 0.98);
  const proxy = createProxyServer(am, {
    proxy: { apiKey: 'k' },
    upstream: `http://127.0.0.1:${upstreamPort}`,
  });
  const proxyPort = await listen(proxy);
  try {
    return await run({ proxyPort, seen, am });
  } finally {
    proxy.close();
    upstream.close();
  }
}

const post = (url) => fetch(url, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ model: 'x', messages: [] }),
});

test('a /tc-acct/<name> request is routed to that exact account, prefix stripped', async () => {
  await withProxy(async ({ proxyPort, seen }) => {
    const res = await post(`http://127.0.0.1:${proxyPort}/tc-acct/beta/v1/messages`);
    await res.text();
    assert.equal(res.status, 200);
    assert.equal(seen.length, 1);
    assert.equal(seen[0].path, '/v1/messages');          // prefix stripped
    assert.equal(seen[0].auth, 'Bearer t-beta');         // routed to 'beta', not rotation default
  });
});

test('pinning by numeric index also works', async () => {
  await withProxy(async ({ proxyPort, seen }) => {
    const res = await post(`http://127.0.0.1:${proxyPort}/tc-acct/alpha/v1/messages`);
    await res.text();
    assert.equal(res.status, 200);
    assert.equal(seen[0].auth, 'Bearer t-alpha');
  });
});

test('an unknown pin returns 404 and never reaches upstream', async () => {
  await withProxy(async ({ proxyPort, seen }) => {
    const res = await post(`http://127.0.0.1:${proxyPort}/tc-acct/ghost/v1/messages`);
    const body = await res.json();
    assert.equal(res.status, 404);
    assert.equal(body.error.type, 'not_found_error');
    assert.equal(seen.length, 0);
  });
});

test('pinning overrides rotation even when another account is the active one', async () => {
  await withProxy(async ({ proxyPort, seen, am }) => {
    am.currentIndex = 0; // rotation would pick 'alpha'
    const res = await post(`http://127.0.0.1:${proxyPort}/tc-acct/beta/v1/messages`);
    await res.text();
    assert.equal(seen[0].auth, 'Bearer t-beta'); // pin wins over the active account
  });
});

test('a normal (unpinned) request still rotates as before', async () => {
  await withProxy(async ({ proxyPort, seen }) => {
    const res = await post(`http://127.0.0.1:${proxyPort}/v1/messages`);
    await res.text();
    assert.equal(res.status, 200);
    assert.equal(seen[0].path, '/v1/messages');
    assert.equal(seen[0].auth, 'Bearer t-alpha'); // default rotation → first account
  });
});

// ── soft pin: /tc-prefer/<name> ──────────────────────────────────────────────
// The difference from /tc-acct/ is entirely in the FALLBACK, so these three
// tests are the contract: prefer wins while eligible, rolls onward when not,
// and the hard pin still refuses to roll.

test('a /tc-prefer/<name> request prefers that account over the rotation default', async () => {
  await withProxy(async ({ proxyPort, seen, am }) => {
    am.currentIndex = 0; // rotation would pick 'alpha'
    const res = await post(`http://127.0.0.1:${proxyPort}/tc-prefer/beta/v1/messages`);
    await res.text();
    assert.equal(res.status, 200);
    assert.equal(seen[0].path, '/v1/messages');   // prefix stripped
    assert.equal(seen[0].auth, 'Bearer t-beta');  // preference beat the active account
  });
});

// The reason the flag exists: the preferred account being spent must roll onto
// the fleet instead of failing the request.
test('a /tc-prefer/<name> request ROLLS to another account when the preferred one is not eligible', async () => {
  await withProxy(async ({ proxyPort, seen, am }) => {
    am.accounts[1].disabled = true; // 'beta' is spent/unavailable
    const res = await post(`http://127.0.0.1:${proxyPort}/tc-prefer/beta/v1/messages`);
    await res.text();
    assert.equal(res.status, 200);
    assert.equal(seen[0].auth, 'Bearer t-alpha'); // rolled onward, request served
  });
});

// The inverse guarantee, so a soft pin can never be mistaken for the hard one:
// /tc-acct/ commits to its account regardless of eligibility and never rolls.
test('a /tc-acct/<name> request does NOT roll when its account is not eligible', async () => {
  await withProxy(async ({ proxyPort, seen, am }) => {
    am.accounts[1].disabled = true;
    const res = await post(`http://127.0.0.1:${proxyPort}/tc-acct/beta/v1/messages`);
    await res.text();
    assert.notEqual(seen[0]?.auth, 'Bearer t-alpha'); // never leaked to another account
  });
});

// The REAL failure mode, and the one the `disabled` tests above do NOT reach: a
// hard pin does not check eligibility up front — it forwards, upstream
// quota-rejects, and only the RETRY (with the account now in ctx.tried) produces
// "Pinned account is unavailable". So exhaustion has to come from upstream, not
// from a flag, or the rollover path is never exercised at all.
async function withExhaustedFirstAccount(run) {
  const seen = [];
  const upstream = http.createServer((req, res) => {
    seen.push(req.headers.authorization);
    if (req.headers.authorization === 'Bearer t-alpha') {
      res.writeHead(429, {
        'retry-after': '60',
        'anthropic-ratelimit-unified-5h-status': 'rejected', // durable exhaustion
        'content-type': 'application/json',
      });
      res.end(JSON.stringify({ type: 'error', error: { type: 'rate_limit_error' } }));
      return;
    }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
  });
  const upstreamPort = await listen(upstream);
  const am = new AccountManager([oauth('alpha'), oauth('beta')], 0.98);
  const proxy = createProxyServer(am, {
    proxy: { apiKey: 'k' },
    upstream: `http://127.0.0.1:${upstreamPort}`,
  });
  const proxyPort = await listen(proxy);
  try {
    return await run({ proxyPort, seen, am });
  } finally {
    proxy.close();
    upstream.close();
  }
}

test('soft pin ROLLS to another account when upstream quota-rejects the preferred one', async () => {
  await withExhaustedFirstAccount(async ({ proxyPort, seen }) => {
    const res = await post(`http://127.0.0.1:${proxyPort}/tc-prefer/alpha/v1/messages`);
    await res.text();
    assert.equal(res.status, 200, 'must survive the preferred account being exhausted');
    assert.ok(seen.includes('Bearer t-alpha'), 'tried the preferred account first');
    assert.ok(seen.includes('Bearer t-beta'), 'then rolled onto the next account');
  });
});

test('hard pin still dies with "Pinned account is unavailable" on the same exhaustion', async () => {
  await withExhaustedFirstAccount(async ({ proxyPort, seen }) => {
    const res = await post(`http://127.0.0.1:${proxyPort}/tc-acct/alpha/v1/messages`);
    const body = await res.json();
    assert.equal(res.status, 429);
    assert.match(body.error.message, /Pinned account is unavailable/);
    assert.ok(!seen.includes('Bearer t-beta'), 'never leaked onto another account');
  });
});

test('an unknown /tc-prefer pin is refused loudly rather than silently rotating', async () => {
  await withProxy(async ({ proxyPort, seen }) => {
    const res = await post(`http://127.0.0.1:${proxyPort}/tc-prefer/ghost/v1/messages`);
    const body = await res.json();
    assert.equal(res.status, 404);
    assert.equal(body.error.type, 'not_found_error');
    assert.equal(seen.length, 0);
  });
});
