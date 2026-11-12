#!/usr/bin/env python3
"""Run the portable checks on this laptop for the setup tool: RUN_DIR/portable-checks.json.

    python3 run_portable.py RUN_DIR [--budget 5] [--days 30]

Every check reads only what reads the same on any laptop (base.py). Each runs with a hard time limit: a check that
runs out of time or errors is written as "not proven on other laptops yet" (status timeout / error) and moves no
level, so the scan can't hang on a heavy laptop. A check that answers "can't tell" says why in its note.

Output: {"meta": {...}, "checks": [{"pid", "L", "i", "value", "status": counted|cant_tell|timeout|error, "secs",
"evidence", "note"}]}. Shared work (log searches, git, repos, identity) is done once up front, then the checks run
8 at a time. The log searches to do up front are the ones the checks asked for on the stranger laptop
(grep-patterns.json, written by `evaluate.py --real --save-patterns` after a check's searches change).
"""
import argparse
import glob
import importlib.util
import json
import multiprocessing
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import base  # noqa: E402

LT = None   # the Laptop, loaded before the workers fork so each inherits it


def load_checks():
    for f in sorted(glob.glob(os.path.join(HERE, "checks_*.py"))):
        spec = importlib.util.spec_from_file_location(os.path.basename(f)[:-3], f)
        assert spec and spec.loader
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)

def run_prewarm_hooks(lt):
    """Each checks_*.py may export PREWARM = [fn, ...]: shared builders (e.g. the "what you built" list) that several of its
    checks use. Called once on the loaded Laptop before the workers fork, so every worker shares the result instead of
    rebuilding it (found 11-05: one list rebuilt in 8 workers at once, ~50s each)."""
    for m in [sys.modules[n] for n in list(sys.modules) if n.startswith("checks_")]:
        for fn in getattr(m, "PREWARM", []) or []:
            try:
                fn(lt)
            except Exception as e:
                print(f"prewarm {m.__name__}.{getattr(fn, '__name__', fn)} failed: {type(e).__name__}: {e}", file=sys.stderr)


def one(k):
    t = time.monotonic()
    try:
        got = base.CHECKS[k](LT)
        v = got.get("value")
        return {"pid": k[0], "L": k[1], "i": k[2], "value": v, "status": "cant_tell" if v is None else "counted",
                "secs": round(time.monotonic() - t, 2), "evidence": (got.get("evidence") or [])[:3], "note": got.get("note")}
    except Exception as e:
        return {"pid": k[0], "L": k[1], "i": k[2], "value": None, "status": "error", "secs": round(time.monotonic() - t, 2),
                "note": f"{type(e).__name__}: {e}"[:300]}


RANK = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}
# when a count is enough to show a level: L2 is a habit (3 times, as signals.yaml's bars), L3/L4 a setup or build that exists
BAR = {"L2": 3, "L3": 1, "L4": 1}


def logs_held(LT, days):
    """How much log the counts stand on, read from the laptop rather than assumed: `days` is the window asked for, not
    what exists. Claude Code deletes transcripts older than the user's `cleanupPeriodDays` (30 when unset), so a laptop
    set to 7 has 7 days, and a check that needs 3 runs in 30 days had a quarter of the chance. A short window can only
    hide a practice, never invent one, so it never lowers a level: the page shows it beside every 'not seen'."""
    t0s = [s["t0"] for s in LT.sessions.values() if s.get("t0")]
    kept = None
    for scope, _, js in LT.settings_files():
        if scope in ("user", "user-local", "managed") and isinstance(js, dict) and isinstance(js.get("cleanupPeriodDays"), (int, float)):
            kept = js["cleanupPeriodDays"]            # the last scope read wins, as in Claude Code (local over user)
    versions = sorted({s["version"] for s in LT.sessions.values() if s.get("version")},
                      key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")])
    return {"window_days": days,
            "oldest_day_held": round((LT.now - min(t0s)) / 86400, 1) if t0s else 0,
            "days_with_sessions": len({time.strftime("%Y-%m-%d", time.localtime(t)) for t in t0s}),
            "cleanup_period_days": kept if kept is not None else 30, "cleanup_period_set": kept is not None,
            "claude_code_versions": versions[-5:], "claude_code_newest": versions[-1] if versions else None}


def level_rows(rows):
    """The checks' counts -> practice rungs, in signal-rows.json's shape so assemble.py folds them the same way.
    A check may set a level only when the skeptic let it (skeptic-checks.json: may_set_level and positive_supports_level);
    without that file, none may. Its count must reach the level's bar. Everything else is shown, never raises a level."""
    p = os.path.join(HERE, "level-allowed.json")   # the skeptic's yes list, without its reasons (those quote one laptop)
    ok = {(x["pid"], x["L"], x["i"]): {"may_set_level": True, "positive_supports_level": True}
          for x in json.load(open(p))["allowed"]} if os.path.exists(p) else {}
    hp = os.path.join(HERE, "hold.json")   # held back until verified, whatever the skeptic said
    held = {(x["pid"], x["L"], x["i"]): x["why"] for x in json.load(open(hp))["hold"]} if os.path.exists(hp) else {}
    claims = {}
    fk = os.path.join(HERE, "claims.json")   # each check's claim sentence only (the review file holds one laptop's data)
    if os.path.exists(fk):
        claims = {(x["pid"], x["L"], x["i"]): x.get("claim") or "" for x in json.load(open(fk))}
    out = {}
    for r in rows:
        k = (r["pid"], r["L"], r["i"])
        s = out.setdefault(r["pid"], {"rung": "", "signals": [], "below_bar": []})
        item = {"id": f"portable:{r['pid']}:{r['L']}#{r['i']}", "rung": r["L"], "n": r.get("value"),
                "says": f"{claims.get(k) or 'check'}: {r.get('value')}" + (f" ({r['note']})" if r.get("note") else "")}
        sk = ok.get(k) or {}
        proven = sk.get("may_set_level") and sk.get("positive_supports_level") and k not in held
        if r["status"] == "counted" and proven and isinstance(r.get("value"), (int, float)) and r["value"] >= BAR.get(r["L"], 1):
            s["signals"].append(item)
            if RANK.get(r["L"], 0) > RANK.get(s["rung"], 0):
                s["rung"] = r["L"]
        else:
            why = ("not proven on other laptops yet" if r["status"] in ("timeout", "error") else
                   "can't tell from a laptop" if r["status"] == "cant_tell" else
                   f"held until verified: {held[k]}" if k in held else
                   "the skeptic did not let this check set a level" if not proven else "below the level's bar")
            s["below_bar"].append({**item, "why": why})
    return {"practices": out, "source": "portable checks (labs/setup-snapshot/experiments/2026-11-05-portable)"}


def main():
    global LT
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--budget", type=float, default=5.0, help="seconds one check may take; over it, it is not proven")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    t0 = time.monotonic()
    load_checks()
    LT = base.Laptop(days=a.days)
    pats = json.load(open(os.path.join(HERE, "grep-patterns.json"))) if os.path.exists(os.path.join(HERE, "grep-patterns.json")) else []
    LT.prewarm([p.encode() for p in pats])
    run_prewarm_hooks(LT)
    t_shared = time.monotonic() - t0
    keys = sorted(base.CHECKS)
    out = {}
    workers = 8
    # the whole run is bounded: every check could use its full budget, 8 at a time, plus one budget of slack
    deadline = time.monotonic() + a.budget * (-(-len(keys) // workers) + 1)
    with multiprocessing.get_context("fork").Pool(workers) as pool:
        pending = {k: pool.apply_async(one, (k,)) for k in keys}
        for k, res in pending.items():
            try:
                out[k] = res.get(timeout=max(0.0, deadline - time.monotonic()))
                if out[k]["status"] == "counted" and out[k]["secs"] > a.budget:   # answered, but too slow to count on
                    out[k].update(status="timeout", value=None,
                                  note=f"not proven on other laptops yet: took {out[k]['secs']:.0f}s, over the {a.budget:.0f}s budget")
            except multiprocessing.TimeoutError:
                out[k] = {"pid": k[0], "L": k[1], "i": k[2], "value": None, "status": "timeout", "secs": None,
                          "note": f"not proven on other laptops yet: over the {a.budget:.0f}s budget"}
        pool.terminate()
    rows = [out[k] for k in keys]
    meta = {"days": a.days, "checks": len(rows), "shared_secs": round(t_shared, 1), "total_secs": round(time.monotonic() - t0, 1),
            "budget_secs": a.budget, "by_status": {s: sum(1 for r in rows if r["status"] == s) for s in ("counted", "cant_tell", "timeout", "error")},
            "sessions": len(LT.sessions), "repos": len(LT.repos), "logs": logs_held(LT, a.days)}
    os.makedirs(a.run_dir, exist_ok=True)
    with open(os.path.join(a.run_dir, "portable-checks.json"), "w") as f:
        json.dump({"meta": meta, "checks": rows}, f, indent=1, ensure_ascii=False, default=str)
    with open(os.path.join(a.run_dir, "portable-rows.json"), "w") as f:
        json.dump(level_rows(rows), f, indent=1, ensure_ascii=False, default=str)
    print(f"portable checks: {meta['by_status']} in {meta['total_secs']:.0f}s (shared work {meta['shared_secs']:.0f}s)")


if __name__ == "__main__":
    main()
