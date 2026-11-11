#!/usr/bin/env python3
"""Put the cheap version together: the AI pass's practice rows + the code's problem rows → RUN_DIR/evidence.json in the
engine's format (verdict 'kept': there is no skeptic in the cheap version). Then render with
../2026-11-02-evidence/render_run.sh RUN_DIR.

    python3 assemble.py RUN_DIR AI_RESULT.json
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("packets", os.path.join(HERE, "..", "2026-11-02-evidence", "packets.py"))
packets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(packets)
SUB = os.path.normpath(os.path.join(HERE, "..", "..", "..", "v15", "engine", "substrate.json"))


def rested_on(basis, arts):
    """The digest lines the AI saw for the artifacts a row rests on: the page's "what the reader saw there"."""
    import re
    ids = re.findall(r"\[([^\]]+)\]", basis or "")
    return [f"[{i}] {arts[i]}" for i in ids if i in arts][:3]


RANK = {"": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


def fold_in_signals(rows, run):
    """signal-rows.json (signals.py: who-did-it counts → rungs) into the practice rows, one row per practice as the page
    expects. The higher rung wins: when the counts show more than the AI pass found, the row takes the counts' rung and
    sentence, and keeps the AI pass's reading after it. Either way the counts join the row's evidence, so the page shows
    them. Returns [(practice, old rung, new rung)] for the rows the counts raised."""
    sig = {}
    # who-did-it counts (signals.py) and the portable checks (run_portable.py: counts that read the same on any
    # laptop, a level only where a skeptic let the check set one) have the same shape; a practice takes both
    for name in ("signal-rows.json", "portable-rows.json"):
        path = os.path.join(run, name)
        if not os.path.exists(path):
            continue
        for pid, s in (json.load(open(path)).get("practices") or {}).items():
            cur = sig.setdefault(pid, {"rung": "", "signals": [], "below_bar": []})
            cur["signals"] += s.get("signals") or []
            cur["below_bar"] += s.get("below_bar") or []
            if RANK.get(s.get("rung") or "", 0) > RANK.get(cur["rung"], 0):
                cur["rung"] = s["rung"]
    if not sig:
        return []
    raised = []
    for r in rows:
        s = sig.get(r["id"]) if r["kind"] == "practice" else None
        if not s or not s["signals"]:
            continue
        met = s["signals"]
        r["evidence"] = list(r.get("evidence") or []) + [
            {"source": "Who did it", "detail": e["says"], "signal": e["id"], "rung": e["rung"]} for e in met]
        r["signals"] = [e["id"] for e in met]
        old = r["rung"] if r["state"] == "seen" else ""
        if RANK[s["rung"]] <= RANK[old]:
            continue
        top = [e for e in met if e["rung"] == s["rung"]]
        before = f" The AI pass read: {r['why']}" if old and r.get("why") else ""
        r.update({"state": "seen", "rung": s["rung"], "why": " ".join(e["says"] for e in top[:3]) + before,
                  # basis: what the rung rests on, so the level counts it once when it backs two practices
                  "basis": f"who did it: {top[0]['id']}", "derivation": "counted", "found_by": "code",
                  "rule": f"who-did-it counts (substrate/signals.yaml): {', '.join(e['id'] for e in top)} → {s['rung']}"})
        if r.get("could_be") and RANK[r["could_be"]] <= RANK[s["rung"]]:
            r["could_be"] = ""
        raised.append((r["id"], old, s["rung"]))
    return raised


def main(run, ai):
    S = json.load(open(SUB))
    live = set(packets.live_practices(S))  # what the AI pass is asked about, and all the log counts
    arts = {a["id"]: a["text"] for a in json.load(open(os.path.join(run, "cheap-artifacts.json")))}
    res = json.load(open(ai))
    res = res.get("result", res) if isinstance(res, dict) and "rows" not in res else res
    rows = []
    seen = set()
    for r in res["rows"]:
        if r["id"] not in S["practices"] or r["id"] in seen:
            continue
        seen.add(r["id"])
        # no skeptic in the cheap version: verdict 'unchecked' (the page must not say a second reader checked it)
        rows.append({"kind": "practice", "discipline": S["practices"][r["id"]]["discipline"], "shows": "", "held": "", "try": "",
                     "checked": ["the digest: everything you built (hooks, plugins, agents, programs, evals, tools) and your by-hand counts"],
                     "derivation": "judgment", "found_by": "one-pass", "verdict": "unchecked", **r,
                     "searched": rested_on(r.get("basis"), arts), "rung": r["rung"] if r["state"] == "seen" else ""})
    # a retired practice keeps its row (as before): the who-did-it counts and the checks still name old ids, and the engine
    # (retire_ids) folds that evidence into the practice each merged into. Only the log counts live practices.
    missing = [p for p in S["practices"] if p not in seen]
    for p in missing:
        rows.append({"kind": "practice", "id": p, "discipline": S["practices"][p]["discipline"], "state": "not read", "rung": "",
                     "could_be": "", "why": "The single AI pass returned no row for this practice.", "basis": "", "evidence": [],
                     "rule": "no row returned", "ask": "", "shows": "", "held": "", "try": "", "checked": [],
                     "derivation": "judgment", "found_by": "one-pass", "verdict": "unchecked"})
    raised = fold_in_signals(rows, run)
    for r in json.load(open(os.path.join(run, "cheap-problems.json")))["rows"]:
        rows.append({**r, "found_by": "code", "verdict": "unchecked",
                     "checked": r.get("checked") or [x["source"] for x in r.get("evidence") or []]})
    json.dump({"rows": rows, "tally": {}, "blocked": {}}, open(os.path.join(run, "evidence.json"), "w"), indent=1, ensure_ascii=False)
    print(f"evidence.json: {sum(r['kind'] == 'practice' and r['id'] in live for r in rows)} practices "
          f"({sum(p in live for p in missing)} missing from the AI pass), "
          f"{sum(r['kind'] == 'challenge' for r in rows)} problems")
    if raised:
        print(f"   rungs raised by who-did-it counts: {', '.join(f'{p} {a or 'none'}→{b}' for p, a, b in raised)}")


if __name__ == "__main__":
    main(os.path.expanduser(sys.argv[1]), os.path.expanduser(sys.argv[2]))
