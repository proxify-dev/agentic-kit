"""The anatomy (anatomy/anatomy.yaml) as data, shared by the scan step (anatomy_scan.py) and the page (tools/anatomy.py),
so both name every question the same way and read a signal by the same rules.

Hierarchy: Discipline → Component → Rung (L2 by hand · L3 their setup · L4 built) → Technique → Signal.
A signal is one of:
  "<inventory id>"                              SCAN: a card or portable check the setup tool already collects
  {read: scan-new, id, what}                    SCAN: a code check of anatomy_scan.py (newer: no stranger-laptop test yet)
  {read: scan+ai, find, scan, what}             SCAN → AI: the scan's finder `find` lists the files; an AI reads only those
  {read: project, what}                         PROJECT READ: an AI reads the most active projects
  {read: call, ask}                             CALL: only the person can tell
"""
import glob
import json
import os

RUNGS = ("L2", "L3", "L4")


def kind(sig):
    if isinstance(sig, dict):
        return {"scan-new": "scan-new", "scan+ai": "scan+ai", "project": "project", "call": "call"}.get(str(sig.get("read")), "unknown")
    return "scan"


def walk(A):
    """Every signal with its address and a stable question id: disc/comp/rung/technique.signal (1-based)."""
    for d in A.get("disciplines") or []:
        for c in d.get("components") or []:
            for lv in RUNGS:
                r = (c.get("rungs") or {}).get(lv) or {}
                for ti, t in enumerate(r.get("techniques") or [], 1):
                    for si, s in enumerate(t.get("signals") or [], 1):
                        yield d, c, lv, t, s, f"{d['id']}/{c['id']}/{lv}/{ti}.{si}"


def ai_questions(A):
    """The questions the AI pass answers: every SCAN → AI and PROJECT READ signal."""
    out = []
    for d, c, lv, t, s, q in walk(A):
        k = kind(s)
        if k in ("scan+ai", "project"):
            out.append({"q": q, "kind": k, "find": s.get("find"), "what": s.get("what") or "", "scan": s.get("scan"),
                        "where": f"{d['name']} › {c['name']} › {lv} › {t.get('name')}"})
    return out


def load_run(run_dir):
    """What a run found: portable checks and Claude Code cards from the setup tool's scan, the anatomy's own code checks
    and file finders (anatomy-scan.json), and the AI pass's answers (anatomy-ai.json). Missing files read as empty."""
    def j(name, default):
        p = os.path.join(run_dir, name)
        try:
            return json.load(open(p))
        except (OSError, ValueError):
            return default
    checks = {f"{c['pid']}:{c['L']}#{c['i']}": c for c in (j("portable-checks.json", {}).get("checks") or [])}
    cards = {}
    for g in ((j("inventory.json", {}).get("who_did_it") or {}).get("groups") or []):
        for it in g.get("items") or []:
            cards[it["id"]] = it
    scan = j("anatomy-scan.json", {})
    answers = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "anatomy-ai*.json"))):  # one file per project brief
        try:
            doc = json.load(open(path))
        except (OSError, ValueError):
            continue
        for a in doc.get("answers") or []:
            answers.setdefault(a.get("q"), []).append({**a, "project": doc.get("project")})
    return {"checks": checks, "cards": cards, "scan": scan, "answers": answers}


def person(sig, q, run):
    """What one signal says about this person: fired True (seen), False (looked, not there), None (can't tell)."""
    k = kind(sig)
    if k == "scan":
        sid = str(sig)
        if sid in run["checks"]:
            c = run["checks"][sid]
            if c.get("status") == "timeout":
                return {"fired": None, "value": "timeout", "note": "ran out of time on this laptop"}
            if c.get("status") != "counted":
                return {"fired": None, "value": c.get("status"), "note": c.get("note") or "can't tell"}
            v = c.get("value")
            return {"fired": bool(v), "value": v, "note": "; ".join((c.get("evidence") or [])[:1])}
        if sid in run["cards"]:
            it = run["cards"][sid]
            n = it.get("n")
            if sid.startswith("cant."):
                return {"fired": None, "value": n, "note": "Claude Code's records can't tell this apart"}
            return {"fired": bool(n), "value": n, "note": it.get("says") or ""}
        return {"fired": None, "value": None, "note": "not in this run"}
    if k == "scan-new":
        c = ((run["scan"].get("checks") or {}).get(sig.get("id"))) or {}
        if not c:
            return {"fired": None, "value": None, "note": "check not run"}
        return {"fired": bool(c.get("value")), "value": c.get("value"), "note": "; ".join((c.get("evidence") or [])[:2])}
    if k in ("scan+ai", "project"):
        if k == "scan+ai":
            fids = sig.get("find") if isinstance(sig.get("find"), list) else [sig.get("find")]
            files = [x for f in fids for x in ((run["scan"].get("files") or {}).get(f) or {}).values()]
            if run["scan"] and not any(files):
                return {"fired": False, "value": "no file", "note": "the scan found no file to read"}
        ans = run["answers"].get(q) or []
        yes = [a for a in ans if a.get("found") == "yes"]
        if yes:
            return {"fired": True, "value": "yes", "note": " · ".join(a.get("evidence") or "" for a in yes[:2])}
        if ans and all(a.get("found") == "no" for a in ans):
            return {"fired": False, "value": "no", "note": ans[0].get("evidence") or ""}
        return {"fired": None, "value": "unclear" if ans else None, "note": "no AI answer" if not ans else (ans[0].get("evidence") or "")}
    if k == "call":
        return {"fired": None, "value": None, "note": "ask on the call: " + (sig.get("ask") or "")}
    return {"fired": None, "value": None, "note": "unknown signal"}


def rung_result(sigs):
    """A rung is seen when any signal fired; not seen when something looked and found nothing and nothing can't tell
    more; otherwise can't tell (a timeout, an unanswered AI question or a call question is never a minus)."""
    fired = [s["fired"] for s in sigs]
    if any(f is True for f in fired):
        return "seen"
    if fired and all(f is False for f in fired):
        return "not seen"
    if any(f is False for f in fired):
        return "not seen (partly)"
    return "can't tell"


# ---- the tree: the lab page's one builder (tools/anatomy.py) ----
SEE = ["none", "call", "todo", "ai", "weak", "scan+ai", "solid"]  # how well any laptop can see a rung, worst to best


def signal_state(sig, inv):
    """How well any laptop can see one signal (from the inventory, never from the anatomy file) → (state, record)."""
    k = kind(sig)
    if k == "project":
        return "ai", {"source": "PROJECT READ", "text": sig.get("what") or ""}
    if k == "scan+ai":
        return "scan+ai", {"source": "SCAN → AI", "text": f"scan finds: {sig.get('scan')}; AI reads: {sig.get('what')}"}
    if k == "call":
        return "call", {"source": "CALL", "text": sig.get("ask") or ""}
    if k == "scan-new":
        if sig.get("id"):
            return "weak", {"source": "SCAN", "id": sig["id"], "text": sig.get("what") or "",
                            "why": "a code check of the anatomy step; no stranger-laptop test yet"}
        return "todo", {"source": "SCAN (not built)", "text": sig.get("what") or ""}
    if k != "scan":
        return "none", {"source": "?", "text": json.dumps(sig)}
    x = inv.get(str(sig))
    if not x:
        return None, {"source": "SCAN", "id": str(sig), "text": "not in the signal inventory"}
    rec = {"source": "SCAN", "id": x["id"], "text": x.get("reads") or ""}
    if x["source"] == "portable":
        if x.get("approved") and not x.get("slow") and x.get("stranger_pass"):
            return "solid", {**rec, "why": "approved, fast on a real laptop, passes a stranger laptop"}
        if x.get("approved"):
            return "weak", {**rec, "why": "approved, but " + ("slow on a real laptop" if x.get("slow") else "fails or lacks the stranger-laptop test")}
        return "weak", {**rec, "why": "a check exists, not approved" + (" (slow)" if x.get("slow") else "")}
    if str(x["id"]).startswith("cant."):
        return "none", {**rec, "why": "Claude Code's records can't tell this apart"}
    if str(x["id"]).startswith("probably."):
        return "weak", {**rec, "why": "word match on typed text"}
    if x.get("status") == "ok":
        return ("solid", {**rec, "why": "Claude Code record, sets a rung"}) if x.get("sets_rung") else \
               ("weak", {**rec, "why": "Claude Code record, counted but not wired to a rung"})
    return "weak", {**rec, "why": f"card status {x.get('status')}"}


def component_you(rungs):
    """A component's result for one person: the highest rung seen; 'not seen' when every rung a laptop can check looked
    and found nothing. A rung only a call can tell is left out of that test: it can never be checked here, so it must
    not turn a clear 'not yet' into 'couldn't check' (11-09: untrusted text and keys sat together in one settings file,
    and the part read 'couldn't check' because its by-hand rung is a call question)."""
    seen = [lv for lv in RUNGS if (rungs.get(lv) or {}).get("you") == "seen"]
    if seen:
        return seen[-1]
    checkable = [lv for lv in RUNGS if rungs.get(lv) and not rungs[lv].get("call_only")]
    if checkable and all(str(rungs[lv].get("you")).startswith("not seen") for lv in checkable):
        return "not seen"
    return "can't tell"


def tree(A, inv, run=None):
    """The hierarchy with how well any laptop sees each rung and, given a run (load_run), what was seen for this person.
    → {"discs": [...], "problems": [...], "gaps": [...], "carried": the practice ids of today's substrate it carries}"""
    discs, problems, gaps, carried = [], [], [], set()
    for d in A.get("disciplines") or []:
        comps, tally = [], {s: 0 for s in SEE}
        for c in d.get("components") or []:
            carried.update(c.get("maps_to") or [])
            rungs = {}
            for lv in RUNGS:
                r = (c.get("rungs") or {}).get(lv)
                if not r:
                    problems.append(f"{d['id']}/{c['id']}: no {lv} rung")
                    continue
                techs, best, mine = [], "none", []
                for ti, t in enumerate(r.get("techniques") or [], 1):
                    sigs = []
                    for si, s in enumerate(t.get("signals") or [], 1):
                        st, rec = signal_state(s, inv)
                        if st is None:
                            problems.append(f"{d['id']}/{c['id']}/{lv}: '{rec['id']}' is not in the signal inventory")
                            st = "none"
                        if run is not None:
                            rec["you"] = person(s, f"{d['id']}/{c['id']}/{lv}/{ti}.{si}", run)
                            mine.append(rec["you"])
                        sigs.append({"state": st, **rec})
                        if SEE.index(st) > SEE.index(best):
                            best = st
                    techs.append({"name": t.get("name") or "", "signals": sigs})
                call_only = all(kind(s) == "call" for t in r.get("techniques") or [] for s in t.get("signals") or [])
                rungs[lv] = {"does": r.get("does") or "", "techniques": techs, "state": best, "call_only": call_only,
                             "you": rung_result(mine) if run is not None else None}
                tally[best] += 1
                if best in ("none", "call", "todo", "ai", "weak"):
                    gaps.append({"state": best, "addr": [d["name"], c["name"], lv], "does": r.get("does") or "",
                                 "tech": " · ".join(t["name"] for t in techs)})
            comps.append({"id": c["id"], "name": c["name"], "seen": c.get("seen"), "maps_to": c.get("maps_to") or [],
                          "rungs": rungs, "you": component_you(rungs) if run is not None else None})
        discs.append({"id": d["id"], "name": d["name"], "goal": d.get("goal") or "", "components": comps, "tally": tally})
    return {"discs": discs, "problems": problems, "gaps": gaps, "carried": carried}


def inventory(anatomy_path, A):
    """The signal inventory the anatomy file names (`inventory:`, relative to the file), by id; {} when missing."""
    p = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(anatomy_path)), A.get("inventory") or "signals.json"))
    try:
        return {x["id"]: x for x in json.load(open(p))}
    except (OSError, ValueError):
        return {}
