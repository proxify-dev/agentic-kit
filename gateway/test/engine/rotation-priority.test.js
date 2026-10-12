import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AccountManager } from '../../src/engine/account-manager.js';

function oauth(name, extra = {}) {
  return { name, type: 'oauth', accessToken: 't-' + name, refreshToken: 'r', expiresAt: Date.now() + 3600_000, ...extra };
}

// Make an account "exhausted" so _selectNext skips it, isolating the priority sort.
function exhaust(am, idx) {
  am.accounts[idx].status = 'exhausted';
}

test('default priority (all 0) preserves the existing selection (first available)', () => {
  const am = new AccountManager([oauth('a'), oauth('b'), oauth('c')], 0.98);
  // No quota known anywhere → weeklyReset is -Infinity for all, ties on priority 0,
  // so the first available account is chosen, matching pre-priority behavior.
  const next = am._selectNext();
  assert.equal(next.name, 'a');
});

test('lower priority value is preferred over config order', () => {
  const am = new AccountManager([
    oauth('a', { priority: 5 }),
    oauth('b', { priority: 1 }),
    oauth('c', { priority: 3 }),
  ], 0.98);
  assert.equal(am._selectNext().name, 'b');
});

test('within the same priority, the existing heuristic breaks the tie', () => {
  const am = new AccountManager([
    oauth('a', { priority: 0 }),
    oauth('b', { priority: 0 }),
  ], 0.98);
  // Give both a known weekly reset; the sooner-expiring one should win the tie.
  am.accounts[0].quota.unified7dReset = 5000;
  am.accounts[1].quota.unified7dReset = 1000;
  assert.equal(am._selectNext().name, 'b');
});

test('priority is respected even when a higher-priority account is also available', () => {
  const am = new AccountManager([
    oauth('a', { priority: 0 }),
    oauth('b', { priority: -1 }), // most preferred
    oauth('c', { priority: 10 }),
  ], 0.98);
  assert.equal(am._selectNext().name, 'b');
});

test('exhausted highest-priority account is skipped, next priority wins', () => {
  const am = new AccountManager([
    oauth('a', { priority: 1 }),
    oauth('b', { priority: 2 }),
  ], 0.98);
  exhaust(am, 0);
  assert.equal(am._selectNext().name, 'b');
});

test('getActiveAccount preempts a healthy current account for a higher-priority one', () => {
  const am = new AccountManager([oauth('a', { priority: 0 }), oauth('b', { priority: 1 })], 0.98);
  am.currentIndex = 1; // currently on the lower-priority account
  assert.equal(am.getActiveAccount().name, 'a');
  assert.equal(am.currentIndex, 0);
});

// ── same tier: leave a healthy current account for one whose weekly resets sooner ──

const H = 3600_000;
const OPUS = 'claude-opus-4-1';
const FABLE = 'claude-fable-5';

function weekly(am, idx, used, resetInMs, extra = {}) {
  Object.assign(am.accounts[idx].quota, { unified5h: 0.3, unified7d: used, unified7dReset: Date.now() + resetInMs, ...extra });
  am.accounts[idx].probing = false;
}

test('getActiveAccount stays sticky within the same tier when the peer resets less than 1h sooner (no thrash)', () => {
  const am = new AccountManager([oauth('a'), oauth('b')], 0.98);
  weekly(am, 0, 0.5, 10 * H);
  weekly(am, 1, 0.5, 9.5 * H);
  am.currentIndex = 0;
  assert.equal(am.getActiveAccount().name, 'a');
  assert.equal(am.currentIndex, 0);
});

test('getActiveAccount leaves a healthy current account for a same-tier one whose weekly resets an hour or more sooner', () => {
  // 2026-10-12: gmail current (reset in ~7 days, 19% used), proxify idle (reset in 3h, 51% used).
  const am = new AccountManager([oauth('proxify'), oauth('gmail')], 0.96);
  weekly(am, 0, 0.51, 3 * H);
  weekly(am, 1, 0.19, 160 * H);
  am.currentIndex = 1;
  assert.equal(am.getActiveAccount(null, OPUS).name, 'proxify');
  assert.equal(am.currentIndex, 0);
  // And it stays there: the order holds until a window resets.
  assert.equal(am.getActiveAccount(null, OPUS).name, 'proxify');
});

test('an account back from a re-login is weighed on the next request', () => {
  const am = new AccountManager([oauth('proxify'), oauth('gmail')], 0.96);
  weekly(am, 0, 0.51, 3 * H);
  weekly(am, 1, 0.19, 160 * H);
  am.accounts[0].status = 'error'; // refresh token rejected
  am.currentIndex = 1;
  assert.equal(am.getActiveAccount(null, OPUS).name, 'gmail');
  am.updateAccountTokens(0, { accessToken: 'new', refreshToken: 'r2', expiresAt: Date.now() + 8 * H });
  assert.equal(am.getActiveAccount(null, OPUS).name, 'proxify');
});

test('a sooner reset never pulls across priority tiers', () => {
  const am = new AccountManager([oauth('a', { priority: 0 }), oauth('b', { priority: 1 })], 0.98);
  weekly(am, 0, 0.5, 100 * H);
  weekly(am, 1, 0.5, 2 * H);
  am.currentIndex = 0;
  assert.equal(am.getActiveAccount().name, 'a');
});

test('a peer with an unknown reset, or over the threshold, does not pull', () => {
  const am = new AccountManager([oauth('a'), oauth('b'), oauth('c')], 0.96);
  weekly(am, 0, 0.5, 100 * H);
  am.accounts[1].quota.unified7dReset = null; am.accounts[1].probing = false; // unknown
  weekly(am, 2, 0.97, 2 * H);                                                 // spent
  am.currentIndex = 0;
  assert.equal(am.getActiveAccount(null, OPUS).name, 'a');
});

test('the soonest of several sooner peers wins', () => {
  const am = new AccountManager([oauth('a'), oauth('b'), oauth('c')], 0.98);
  weekly(am, 0, 0.5, 100 * H);
  weekly(am, 1, 0.5, 50 * H);
  weekly(am, 2, 0.5, 5 * H);
  am.currentIndex = 0;
  assert.equal(am.getActiveAccount().name, 'c');
});

test('alternating Opus and Fable requests do not move the cursor when family resets disagree', () => {
  // a's shared weekly resets later but its Fable window sooner; b the other way round.
  const am = new AccountManager([oauth('a'), oauth('b')], 0.98);
  weekly(am, 0, 0.3, 100 * H, { unified7dFable: 0.3, unified7dFableReset: Date.now() + 5 * H });
  weekly(am, 1, 0.3, 5 * H, { unified7dFable: 0.3, unified7dFableReset: Date.now() + 100 * H });
  am.currentIndex = 1; // b: the shared weekly that resets sooner
  for (const model of [OPUS, FABLE, OPUS, FABLE]) {
    assert.equal(am.getActiveAccount(null, model).name, 'b', model);
    assert.equal(am.currentIndex, 1);
  }
});

test('previewRouteIndex names the sooner-resetting peer without moving the cursor', () => {
  const am = new AccountManager([oauth('a'), oauth('b')], 0.98);
  weekly(am, 0, 0.5, 100 * H);
  weekly(am, 1, 0.5, 5 * H);
  am.currentIndex = 0;
  assert.equal(am.previewRouteIndex(OPUS), 1);
  assert.equal(am.currentIndex, 0);
  assert.equal(am.getActiveAccount(null, OPUS).index, 1, 'preview matches the real selection');
});

test('common case: all-equal priority leaves getActiveAccount on the healthy current account', () => {
  const am = new AccountManager([oauth('a'), oauth('b'), oauth('c')], 0.98);
  am.currentIndex = 2;
  assert.equal(am.getActiveAccount().name, 'c'); // unchanged — no preemption when priorities tie
});

// ── selectActiveAccount (daemon-launch up-front selection) ───────────────────

test('selectActiveAccount picks the soonest-resetting weekly window, not index 0', () => {
  const now = Date.now();
  const am = new AccountManager([oauth('a'), oauth('b'), oauth('c')], 0.98);
  // All same priority and available; b's weekly resets soonest → spend it first.
  am.accounts[0].quota.unified7d = 0.5; am.accounts[0].quota.unified7dReset = now + 5 * 86400_000;
  am.accounts[1].quota.unified7d = 0.5; am.accounts[1].quota.unified7dReset = now + 1 * 86400_000;
  am.accounts[2].quota.unified7d = 0.5; am.accounts[2].quota.unified7dReset = now + 3 * 86400_000;
  const chosen = am.selectActiveAccount();
  assert.equal(chosen.name, 'b');
  assert.equal(am.currentIndex, 1); // currentIndex actually moved off 0
});

test('selectActiveAccount honors priority over the weekly heuristic', () => {
  const now = Date.now();
  const am = new AccountManager([
    oauth('a', { priority: 0 }),
    oauth('b', { priority: 1 }), // higher-priority value = less preferred
  ], 0.98);
  // b resets sooner, but a has the better (lower) priority value → a wins.
  am.accounts[0].quota.unified7d = 0.5; am.accounts[0].quota.unified7dReset = now + 5 * 86400_000;
  am.accounts[1].quota.unified7d = 0.5; am.accounts[1].quota.unified7dReset = now + 1 * 86400_000;
  assert.equal(am.selectActiveAccount().name, 'a');
});

test('selectActiveAccount skips an account already over the weekly threshold', () => {
  const now = Date.now();
  const am = new AccountManager([oauth('a'), oauth('b')], 0.98);
  am.accounts[0].quota.unified7d = 0.99; am.accounts[0].quota.unified7dReset = now + 86400_000; // near-exhausted
  am.accounts[1].quota.unified7d = 0.10; am.accounts[1].quota.unified7dReset = now + 5 * 86400_000;
  assert.equal(am.selectActiveAccount().name, 'b');
});

test('selectActiveAccount restores onto the best account after an export → restore cycle', () => {
  const now = Date.now();
  const am1 = new AccountManager([
    oauth('a', { accountUuid: 'p1' }),
    oauth('b', { accountUuid: 'p2' }),
  ], 0.98);
  am1.accounts[0].quota.unified7d = 0.8; am1.accounts[0].quota.unified7dReset = now + 6 * 86400_000;
  am1.accounts[1].quota.unified7d = 0.2; am1.accounts[1].quota.unified7dReset = now + 2 * 86400_000;

  const am2 = new AccountManager([
    oauth('a', { accountUuid: 'p1' }),
    oauth('b', { accountUuid: 'p2' }),
  ], 0.98);
  am2.restoreQuotaState(am1.exportQuotaState());
  // b's weekly resets sooner → start there, mirroring a real daemon launch.
  assert.equal(am2.selectActiveAccount().name, 'b');
});

test('selectActiveAccount leaves current account when none are available', () => {
  const am = new AccountManager([oauth('a'), oauth('b')], 0.98);
  am.accounts[0].status = 'exhausted';
  am.accounts[1].disabled = true;
  am.currentIndex = 1;
  const chosen = am.selectActiveAccount();
  assert.equal(chosen.name, 'b');   // falls back to the existing current account
  assert.equal(am.currentIndex, 1); // unchanged
});
