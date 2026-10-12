import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AccountManager } from '../../src/engine/account-manager.js';
import { gatingUtilization } from '../../src/engine/model.js';

// The weekly gate: which bucket decides whether an account may serve a family
// model. Family spend meters TWICE, once in the family bucket and once in the
// shared one, so reading the family bucket alone let an account already over its
// shared cap keep taking family traffic — and every such request pushed the
// shared bucket further past it. Issue #175 measured the coupling at
// [+1.14e-4, +5.21e-4] per request against Fable-only traffic.
//
// The rule is a MAXIMUM over the two reported buckets, with null meaning
// unreported and never zero.

const OPUS = 'claude-opus-5';
const FABLE = 'claude-fable-5';
const SONNET = 'claude-sonnet-4-6';
const stripAnsi = s => s.replace(/\x1b\[[0-9;]*m/g, '');
const acct = (quota) => ({ name: 'a', type: 'apikey', apiKey: 'k', quota });

function managerWith(quota) {
  const am = new AccountManager([acct({})], 0.98);
  am.accounts[0].quota = { ...am.accounts[0].quota, ...quota };
  am.accounts[0].probing = false;
  return am;
}

// ---------------------------------------------------------------------------
// The matrix from issue #175, one test per row.
// ---------------------------------------------------------------------------

test('shared over cap, family under: the account is barred from the family model', () => {
  const am = managerWith({ unified7d: 1.0, unified7dFable: 0.2 });
  assert.equal(am._governingWeekly(am.accounts[0], FABLE), 1.0,
    'the gate read the family bucket alone and missed the spent shared bucket');
  assert.equal(am._isNearQuota(am.accounts[0], FABLE), true);
  assert.equal(am._isAvailable(am.accounts[0], FABLE), false,
    'an account past its shared weekly cap kept serving family traffic');
});

test('family over cap, shared under: gating stays model-scoped', () => {
  const am = managerWith({ unified7d: 0.2, unified7dFable: 1.0 });
  // The family bucket still bars its own family...
  assert.equal(am._isNearQuota(am.accounts[0], FABLE), true);
  // ...and must not bar anything else. This is the property the maximum could
  // have broken by leaking the family figure into other models' decisions.
  assert.equal(am._governingWeekly(am.accounts[0], OPUS), 0.2);
  assert.equal(am._isNearQuota(am.accounts[0], OPUS), false,
    'a spent Fable bucket barred Opus, which is the bug this rule must not create');
});

test('both under cap: the account serves both', () => {
  const am = managerWith({ unified7d: 0.3, unified7dFable: 0.2 });
  assert.equal(am._governingWeekly(am.accounts[0], FABLE), 0.3,
    'the maximum of the two reported buckets is the shared one here');
  assert.equal(am._isNearQuota(am.accounts[0], FABLE), false);
  assert.equal(am._isNearQuota(am.accounts[0], OPUS), false);
});

test('family reported, shared unreported: the family figure stands alone', () => {
  const am = managerWith({ unified7dFable: 0.5 });
  assert.equal(am._governingWeekly(am.accounts[0], FABLE), 0.5,
    'an absent shared bucket must not be floored to 0 and must not erase the family figure');
});

test('both unreported: the answer is null, not zero', () => {
  const am = managerWith({});
  assert.equal(am._governingWeekly(am.accounts[0], FABLE), null,
    'unreported became a number, which reads as "empty" rather than "unknown"');
  assert.equal(am._isNearQuota(am.accounts[0], FABLE), false,
    'the gate decided on a dimension nothing reported');
});

test('governing bucket already IS the shared one: behaviour is unchanged', () => {
  // max(x, x) is x. Asserted rather than assumed, because the maximum is the
  // whole change and this is the case where it must do nothing.
  const am = managerWith({ unified7d: 0.7 });
  assert.equal(am._governingWeekly(am.accounts[0], OPUS), 0.7);
  assert.equal(am._governingWeekly(am.accounts[0], null), 0.7);
  const spent = managerWith({ unified7d: 0.99 });
  assert.equal(spent._isNearQuota(spent.accounts[0], OPUS), true);
});

test('shared reported at zero is a value, not an absence', () => {
  // The distinction the null rule protects: 0 is a measurement.
  const am = managerWith({ unified7d: 0, unified7dFable: 0.4 });
  assert.equal(am._governingWeekly(am.accounts[0], FABLE), 0.4);
  const empty = managerWith({ unified7dFable: 0.4 });
  assert.equal(empty._governingWeekly(empty.accounts[0], FABLE), 0.4,
    'a reported 0 and an absent bucket must reach the same answer here, by different routes');
});

test('gatingUtilization keeps null as unreported for every combination', () => {
  // The one rule a maximum most easily breaks, over the whole cross product.
  for (const shared of [null, 0, 0.5, 1]) {
    for (const fam of [null, 0, 0.5, 1]) {
      const q = {};
      if (shared != null) q.unified7d = shared;
      if (fam != null) q.unified7dFable = fam;
      const got = gatingUtilization(q, 'unified7dFable');
      const expected = shared == null && fam == null ? null
        : shared == null ? fam
          : fam == null ? shared : Math.max(fam, shared);
      assert.equal(got, expected, `shared=${shared} family=${fam}`);
      if (shared == null && fam == null) assert.equal(got, null, 'absent became a number');
    }
  }
});

// ---------------------------------------------------------------------------
// Sonnet. `unified7dSonnet` is only populated by the opt-in usage prober, so
// every pre-existing fixture leaves it null and nothing executed that arm.
// ---------------------------------------------------------------------------

test('Sonnet is gated by the shared bucket exactly as Fable is', () => {
  const am = managerWith({ unified7d: 1.0, unified7dSonnet: 0.2 });
  assert.equal(am._governingWeekly(am.accounts[0], SONNET), 1.0);
  assert.equal(am._isAvailable(am.accounts[0], SONNET), false,
    'an account past its shared weekly cap kept serving Sonnet');
});

test('a spent Sonnet bucket bars only Sonnet', () => {
  const am = managerWith({ unified7d: 0.2, unified7dSonnet: 1.0, unified7dFable: 0.1 });
  assert.equal(am._isNearQuota(am.accounts[0], SONNET), true);
  assert.equal(am._isNearQuota(am.accounts[0], FABLE), false, 'Sonnet\'s bucket barred Fable');
  assert.equal(am._isNearQuota(am.accounts[0], OPUS), false, 'Sonnet\'s bucket barred Opus');
});

// ---------------------------------------------------------------------------
// The consumers that inherit the fix, and the ones that deliberately do not.
// ---------------------------------------------------------------------------

test('the probe ranker sees the shared bucket too', () => {
  // `_selectProbe` ranks on `_maxUtilization`, so without this it could aim a
  // probe at an account that cannot serve the model it is probing for.
  const am = managerWith({ unified7d: 1.0, unified7dFable: 0.2 });
  assert.equal(am._maxUtilization(am.accounts[0], FABLE), 1.0,
    'the probe ranker read the family bucket alone');
});

test('_modelWeeklyExhausted stays family-only and does NOT take the maximum', () => {
  // A different question: "can this account serve this family at all", not "is
  // it near any cap". Folding the shared bucket in would skip accounts for
  // probes they could have served, hardening the stale cached utilization a
  // probe exists to correct.
  const am = managerWith({ unified7d: 1.0, unified7dFable: 0.2 });
  assert.equal(am._modelWeeklyExhausted(am.accounts[0], FABLE), false,
    'the advisor family gate started consulting the shared bucket');
  const spentFamily = managerWith({ unified7d: 0.2, unified7dFable: 1.0 });
  assert.equal(spentFamily._modelWeeklyExhausted(spentFamily.accounts[0], FABLE), true);
});

test('the reset still names the governing window, not the bucket that won the max', () => {
  // The value and the reset may now describe different buckets. That is safe
  // because no caller pairs them: both readers of the reset
  // (`_pickBestAvailable`, `_pickLeastLoaded`) use it as a ranking tiebreak
  // among accounts that already passed `_isAvailable`, and neither divides a
  // headroom by it. Pinned here so a later "make them consistent" edit has to
  // argue with a test rather than quietly pair one bucket's level with another
  // bucket's clock.
  const now = Date.now();
  const am = managerWith({
    unified7d: 1.0, unified7dReset: now + 100_000,
    unified7dFable: 0.2, unified7dFableReset: now + 900_000,
  });
  assert.equal(am._governingWeekly(am.accounts[0], FABLE), 1.0, 'the value is the maximum');
  assert.equal(am._governingWeeklyReset(am.accounts[0], FABLE), now + 900_000,
    'the reset must stay with the governing window');
});
