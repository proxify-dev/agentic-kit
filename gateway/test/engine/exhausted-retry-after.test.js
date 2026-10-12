// ak-gateway: the "all accounts exhausted" 429 says when an account really
// comes back (a minimal take on upstream 189ec72, #429).
//
// It read only `rateLimitedUntil || quota.resetsAt`, both null for a
// subscription account spent past the switch threshold — so a fleet out for
// five hours, or for the week, answered "retry in 60s", and Claude Code retried
// every minute into the wall. And it counted every row, the disabled ones too.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

process.env.TEAMCLAUDE_CONFIG = join(mkdtempSync(join(tmpdir(), 'tc-exhausted-')), 'config.json');

const { AccountManager } = await import('../../src/engine/account-manager.js');
const { computeRetryAfter, createProxyServer } = await import('../../src/engine/server.js');

const NOW = 1_800_000_000_000;
const H = 3600_000;
const acct = (quota, extra = {}) => ({ status: 'active', disabled: false, rateLimitedUntil: null, quota: { resetsAt: null, ...quota }, ...extra });

test('a spent 5h window answers with its reset, not 60 s', () => {
  assert.equal(computeRetryAfter([acct({ unified5h: 0.99, unified5hReset: NOW + 3 * H })], 0.98, NOW), 3 * 3600);
});

test('a spent weekly bucket answers with its reset, and a family bucket counts too', () => {
  assert.equal(computeRetryAfter([acct({ unified7d: 1.0, unified7dReset: NOW + 50 * H })], 0.98, NOW), 50 * 3600);
  assert.equal(computeRetryAfter([acct({ unified7dSonnet: 0.99, unified7dSonnetReset: NOW + 7 * H })], 0.98, NOW), 7 * 3600);
});

test('within an account, the LATEST blocking clock: it serves only once all have passed', () => {
  const both = acct({ unified5h: 0.99, unified5hReset: NOW + 2 * H, unified7d: 0.99, unified7dReset: NOW + 30 * H });
  assert.equal(computeRetryAfter([both], 0.98, NOW), 30 * 3600);
});

test('a bucket under the threshold does not block, whatever its reset', () => {
  const a = acct({ unified5h: 0.99, unified5hReset: NOW + 2 * H, unified7d: 0.5, unified7dReset: NOW + 90 * H });
  assert.equal(computeRetryAfter([a], 0.98, NOW), 2 * 3600);
});

test('across the fleet, the SOONEST account', () => {
  const a = acct({ unified7d: 0.99, unified7dReset: NOW + 40 * H });
  const b = acct({ unified5h: 0.99, unified5hReset: NOW + 4 * H });
  assert.equal(computeRetryAfter([a, b], 0.98, NOW), 4 * 3600);
});

test('a live hold still counts, and an account with no known clock is 60 s as before', () => {
  assert.equal(computeRetryAfter([acct({}, { rateLimitedUntil: new Date(NOW + 90_000).toISOString() })], 0.98, NOW), 90);
  assert.equal(computeRetryAfter([acct({})], 0.98, NOW), 60);
  assert.equal(computeRetryAfter([], 0.98, NOW), 60);
});

test('disabled and errored accounts are not candidates', () => {
  const soon = acct({ unified5h: 0.99, unified5hReset: NOW + 1 * H }, { disabled: true });
  const broken = acct({ unified5h: 0.99, unified5hReset: NOW + 1 * H }, { status: 'error' });
  const real = acct({ unified7d: 0.99, unified7dReset: NOW + 20 * H });
  assert.equal(computeRetryAfter([soon, broken, real], 0.98, NOW), 20 * 3600);
});

test('a reset already in the past is not a clock', () => {
  assert.equal(computeRetryAfter([acct({ unified5h: 0.99, unified5hReset: NOW - H })], 0.98, NOW), 60);
});

test('the exhausted 429 counts the enabled accounts, and carries the real retry-after', async () => {
  const upstream = http.createServer((_req, res) => { res.writeHead(200); res.end('{}'); });
  await new Promise(r => upstream.listen(0, '127.0.0.1', r));
  const am = new AccountManager([
    { name: 'a', type: 'apikey', apiKey: 'k-a' },
    { name: 'b', type: 'apikey', apiKey: 'k-b' },
    { name: 'off', type: 'apikey', apiKey: 'k-off', disabled: true },
  ], 0.98);
  // Both enabled accounts on a quota hold (markRateLimited arms no probe until
  // its floor passes), so selection finds nothing and the fleet is spent.
  am.markRateLimited(0, 2 * 3600);
  am.markRateLimited(1, 5 * 3600);
  const proxy = createProxyServer(am, { proxy: { apiKey: 'k' }, upstream: `http://127.0.0.1:${upstream.address().port}` }, {});
  await new Promise(r => proxy.listen(0, '127.0.0.1', r));
  try {
    const res = await fetch(`http://127.0.0.1:${proxy.address().port}/v1/messages`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"model":"claude-sonnet-5","messages":[]}',
    });
    assert.equal(res.status, 429);
    const retryAfter = Number(res.headers.get('retry-after'));
    assert.ok(retryAfter > 7000 && retryAfter <= 7200, `the soonest hold, about 2 h: ${retryAfter}`);
    assert.match((await res.json()).error.message, /^All 2 accounts exhausted/);
  } finally {
    proxy.close();
    upstream.close();
  }
});
