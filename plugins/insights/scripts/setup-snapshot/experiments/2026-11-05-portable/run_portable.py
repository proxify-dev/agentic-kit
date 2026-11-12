#!/usr/bin/env python3
"""Run the portable checks on this laptop for the setup tool: RUN_DIR/portable-checks.json.

    python3 run_portable.py RUN_DIR [--budget 5] [--days 30]

Every check reads only what reads the same on any laptop (base.py). Each runs with its own hard time limit (--budget, counted
from its own start; run_checks ends a check that passes it, with the commands it started, and gives its slot to the next one): a
check that runs out of time or errors is written as "not proven on other laptops yet" (status timeout / error) and moves no
level, so the scan can't hang on a heavy laptop and one slow check cannot take another's turn. A check that answers "can't tell" says why in its note.

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
import multiprocessing.connection
import os
import signal
import sys
import threading
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
    """Each checks_*.py may export PREWARM = [fn, ...] (and WARM, the same for work that is not a builder of the speed map): shared builders (e.g. the "what you built" list) that several of its
    checks use. Called once on the loaded Laptop before the workers fork, so every worker shares the result instead of
    rebuilding it (found 11-05: one list rebuilt in 8 workers at once, ~50s each)."""
    secs = {}
    for m in [sys.modules[n] for n in list(sys.modules) if n.startswith("checks_")]:
        for fn in (getattr(m, "PREWARM", []) or []) + (getattr(m, "WARM", []) or []):
            t = time.monotonic()
            try:
                fn(lt)
            except Exception as e:
                print(f"prewarm {m.__name__}.{getattr(fn, '__name__', fn)} failed: {type(e).__name__}: {e}", file=sys.stderr)
            secs[f"{m.__name__}.{getattr(fn, '__name__', fn)}"] = round(time.monotonic() - t, 1)
    return secs


def one(k):
    t, c = time.monotonic(), time.process_time()
    try:
        got = base.CHECKS[k](LT)
        v = got.get("value")
        return {"pid": k[0], "L": k[1], "i": k[2], "value": v, "status": "cant_tell" if v is None else "counted",
                "secs": round(time.monotonic() - t, 2), "evidence": (got.get("evidence") or [])[:3], "note": got.get("note"),
                "_cpu": time.process_time() - c}
    except Exception as e:
        return {"pid": k[0], "L": k[1], "i": k[2], "value": None, "status": "error", "secs": round(time.monotonic() - t, 2),
                "note": f"{type(e).__name__}: {e}"[:300], "_cpu": time.process_time() - c}


CAN_FORK = "fork" in multiprocessing.get_all_start_methods()   # not on Windows


def _over_budget_row(k, budget, secs=None):
    return {"pid": k[0], "L": k[1], "i": k[2], "value": None, "status": "timeout", "secs": secs,
            "note": f"not proven on other laptops yet: over the {budget:.0f}s budget"}


def _serve(conn):
    """A worker process: runs the checks it is sent, one at a time, until its pipe closes. It leads its own process group, so the
    scheduler can end it together with the git or shell commands a check started."""
    try:
        os.setpgrp()
    except (AttributeError, OSError):
        pass
    while True:
        try:
            k = conn.recv()
        except (EOFError, OSError):
            return
        conn.send(one(k))


def _stop(w):
    """End a worker and whatever it started, and wait for it, so nothing is left running."""
    try:
        os.killpg(w["proc"].pid, signal.SIGKILL)
    except (AttributeError, OSError):
        pass
    w["proc"].kill()
    w["proc"].join(10)
    w["conn"].close()


STARVED = 0.7   # a check that ran out of its budget having used less than this share of it in processor time waited for the machine; it did not run long


def too_slow(row, budget):
    """A check that answered, but not within its budget: not proven (the rule since 11-05)."""
    return row["status"] == "counted" and (row["secs"] or 0) > budget


def run_checks(keys, budget, workers=8, fork=CAN_FORK, poll=0.2, retry=True, info=None):
    """{key: row} for every check, each with its own limit: a check that has not answered `budget` seconds after it started is ended
    (with the commands it started) and written as a timeout, and its worker is replaced at once, so a check that never returns
    holds no slot beyond its own budget and cannot take the others' turn. Before this (11-05 to 11-12) one limit for the whole step
    (budget x (ceil(checks / workers) + 1) = 105 s) bounded all 157 checks, and a few early checks that ran for minutes held their
    workers until it ran out: the rest were never started. A check that answers after its budget is a timeout as before.
    The workers are forked after the shared work, so each inherits it; a worker keeps what one of its checks computed for the next.
    A check over its budget that used under STARVED of it in processor time waited for the machine (its neighbours, other programs),
    it did not run long: it is run once more, after the others, with the same budget; over it again, it stays a timeout. A check that
    really runs long uses its processor time and is never run again. (`info["retried"]`, `info["answered_on_retry"]`.)"""
    if not fork:
        return _run_in_threads(keys, budget)
    out, starved = _run_pool(keys, budget, workers, poll)
    if retry and starved:
        again, _ = _run_pool([k for k in keys if k in starved], budget, workers, poll)
        answered = []
        for k, row in again.items():
            if row["status"] != "timeout" and not too_slow(row, budget):
                out[k] = row
                answered.append(k)
        if info is not None:
            info["retried"] = sorted(f"{k[0]} {k[1]}#{k[2]}" for k in starved)
            info["answered_on_retry"] = sorted(f"{k[0]} {k[1]}#{k[2]}" for k in answered)
    for row in out.values():
        row.pop("_cpu", None)
    return out


def _run_pool(keys, budget, workers, poll):
    """One pass of the checks over a pool of worker processes: ({key: row}, the keys over their budget that were starved of the processor)."""
    ctx = multiprocessing.get_context("fork")

    def start():
        mine, theirs = ctx.Pipe()
        proc = ctx.Process(target=_serve, args=(theirs,), daemon=True)   # daemon: a log search inside a check stays serial, as in a Pool worker
        proc.start()
        theirs.close()
        try:
            os.setpgid(proc.pid, proc.pid)   # the worker does it too; whichever comes first, the group exists before a kill
        except (AttributeError, OSError):
            pass
        return {"proc": proc, "conn": mine, "key": None, "t0": 0.0}

    todo, out, starved = list(keys), {}, set()
    pool = [start() for _ in range(min(workers, len(todo)))]
    try:
        while todo or any(w["key"] is not None for w in pool):
            for w in pool:
                if w["key"] is None and todo:
                    w["key"], w["t0"] = todo.pop(0), time.monotonic()
                    try:
                        w["conn"].send(w["key"])
                    except OSError:    # a worker that died while idle: the recv below sees its closed pipe
                        pass
            busy = {w["conn"]: w for w in pool if w["key"] is not None}
            for conn in multiprocessing.connection.wait(list(busy), timeout=poll):
                w = busy[conn]
                try:
                    row = conn.recv()
                    out[w["key"]] = row
                    if too_slow(row, budget) and row.get("_cpu", budget) < STARVED * budget:
                        starved.add(w["key"])
                    w["key"] = None
                except (EOFError, OSError):    # the worker died under its check (out of memory, a crash)
                    k = w["key"]
                    out[k] = {"pid": k[0], "L": k[1], "i": k[2], "value": None, "status": "error", "secs": None,
                              "note": "the worker running this check stopped"}
                    _stop(w)
                    w.update(start())
            now = time.monotonic()
            for w in pool:
                if w["key"] is not None and now - w["t0"] > budget:
                    out[w["key"]] = _over_budget_row(w["key"], budget)
                    starved.add(w["key"])      # cut off: whether it waited or ran is not known here, so it gets one more try (a long check is cut off again)
                    _stop(w)
                    w.update(start())
    finally:
        for w in pool:
            _stop(w)
    return out, starved


def _run_in_threads(keys, budget):
    """Where processes cannot be forked (Windows): one check at a time in this process, each on its own thread that is given up on
    after its budget. The thread cannot be ended, so a check that never returns goes on in the background while the next ones run."""
    out = {}
    for k in keys:
        box = {}
        t = threading.Thread(target=lambda k=k: box.update(row=one(k)), daemon=True)
        t.start()
        t.join(budget)
        out[k] = box.get("row") or _over_budget_row(k, budget)
    return out


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
    t_laptop = time.monotonic() - t0
    LT.prewarm([p.encode() for p in pats])
    LT.warm_listings()
    t_prewarm = time.monotonic() - t0
    builder_secs = run_prewarm_hooks(LT)
    t_shared = time.monotonic() - t0
    keys = sorted(base.CHECKS)
    info = {}
    out = run_checks(keys, a.budget, info=info)
    for k in keys:
        if out[k]["status"] == "counted" and out[k]["secs"] > a.budget:   # answered, but too slow to count on
            out[k].update(status="timeout", value=None,
                          note=f"not proven on other laptops yet: took {out[k]['secs']:.0f}s, over the {a.budget:.0f}s budget")
    rows = [out[k] for k in keys]
    meta = {"days": a.days, "checks": len(rows), "shared_secs": round(t_shared, 1),
            "shared_parts_secs": {"laptop_index": round(t_laptop, 1), "prewarm_and_listings": round(t_prewarm - t_laptop, 1),
                                  "builders": round(t_shared - t_prewarm, 1)},
            "retried_after_being_cut_off_while_starved": info.get("retried", []), "answered_on_retry": info.get("answered_on_retry", []),
            "builders_over_1s": {k: v for k, v in sorted(builder_secs.items(), key=lambda kv: -kv[1]) if v >= 1}, "total_secs": round(time.monotonic() - t0, 1),
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
