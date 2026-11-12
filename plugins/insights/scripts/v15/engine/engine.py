#!/usr/bin/env python3
"""v15 engine: substrate + evidence → the person's practice rungs per discipline, a level pointer, and
recommendations. The same function serves the laptop (evidence rows from the per-discipline readers, after a skeptic,
in RUN_DIR/evidence.json) and a call (evidence from answers). Rules: ../levels.md. Standard library only.
Usage: python3 engine.py RUN_DIR [OUT.json]   (RUN_DIR holds evidence.json, from setup-snapshot's 2026-11-02-evidence)"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board as capability_board  # noqa: E402  v13's yes/no board of what they built, from laptop facts
import laptop_evidence  # noqa: E402
import things as level_things  # noqa: E402  the level read from plain things, a trial beside the practice read (11-11)

RANK = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}
STRENGTH = {"did": 3, "showed": 2, "said": 1}


def picture(S, ev):
    """Rung per practice, from Did/Showed evidence only (levels.md). "Said" never moves a rung: it only sets
    `could_be_rung`, and so does a laptop row's could_be (a rung the evidence points to but doesn't prove).
    Dated rows (calls): per practice only the newest date counts; an older call is history. Undated rows (this laptop
    read) always count."""
    newest = {}
    for e in ev:
        if e["kind"] == "practice" and e.get("date"):
            newest[e["id"]] = max(newest.get(e["id"], ""), e["date"])
    rung = {}
    for e in ev:
        if e["kind"] != "practice" or (e.get("date") and e["date"] != newest.get(e["id"])):
            continue
        cur = rung.setdefault(e["id"], {"rung": None, "strength": None, "why": e.get("why"), "held": None,
                                        "source": e.get("source"), "basis": None, "could_be_rung": None})
        for cb in ([e["rung"]] if e.get("rung") and e.get("strength") == "said" else []) + \
                  ([e["could_be_rung"]] if e.get("could_be_rung") else []):
            if not cur["could_be_rung"] or RANK[cb] > RANK[cur["could_be_rung"]]:
                cur["could_be_rung"] = cb
        if not e.get("rung") or e.get("strength") == "said":
            continue
        better = cur["rung"] is None or RANK[e["rung"]] > RANK[cur["rung"]] or (
            RANK[e["rung"]] == RANK[cur["rung"]] and STRENGTH[e["strength"]] > STRENGTH[cur["strength"]])
        if better:
            cur.update({"rung": e["rung"], "strength": e["strength"], "why": e["why"], "held": e.get("held"),
                        "source": e["source"], "basis": e.get("basis") or None})
    for r in rung.values():
        if r["could_be_rung"] and r["rung"] and RANK[r["could_be_rung"]] <= RANK[r["rung"]]:
            r["could_be_rung"] = None
        r["could_be"] = r["rung"] is None and bool(r["could_be_rung"])  # only talk or a hint, no shown rung
    return rung


MIN_DISCIPLINES = 2  # below this, too little to place a level: "can't tell" and what to ask (levels.md)
MIN_BUILT_AREAS = 3  # L4: built things shown at L4 in this many different disciplines (11-05)
EVAL_GATE = "retest-agent-setup"  # L4's evals on what they built: a condition of its own, never one of the areas

# The level lists (levels.md, 11-05: "it should take as many practices as reasonable"). Each level takes every entry
# of the level below, plus more, and is met when every entry is shown at its step. HAND = shown at L2 or higher (they do
# it, at least by hand); SETUP = shown at L3 or higher (their setup does it). An entry naming two practices is met by
# either. On no list: practices the call judges better, ones that depend on what the client allows, and ones only
# people who build agent products do.
HAND, SETUP = 2, 3


def _raise(base, more):
    out = dict(base)
    for ids, need in more:
        out[ids] = max(out.get(ids, 0), need)
    return list(out.items())


# 11-05 rewrite: ids retired into another practice (substrate `merged_into`) are named by the practice they went into;
# the either/or entry "compact or reset" is now just reset-with-a-handoff, and _raise dedupes entries that landed together.
_L2 = [(("put-rules-where-they-apply",), HAND), (("notes-the-next-chat-reads",), HAND),
       (("reset-with-a-handoff",), HAND),
       # isolate's L2 rung names two ways: a fork by hand, or a workflow that spreads the reading. The trusted workflow-runs
       # check moved to lead-a-team (e68b0df2), so the entry is either/or or a workflow user with no hand forks reads L1 (11-06)
       (("isolate-side-work", "lead-a-team-of-agents"), HAND),
       (("bound-the-task",), HAND), (("settle-the-approach-first",), HAND), (("reword-the-ask",), HAND),
       (("prove-it-works",), HAND), (("limit-what-the-agent-can-reach",), HAND), (("parallel-work-kept-apart",), HAND)]
_L3 = _raise(_L2, [(("notes-the-next-chat-reads",), SETUP), (("write-down-what-it-cannot-know",), SETUP),
                   (("put-rules-where-they-apply",), SETUP), (("write-down-what-it-cannot-know",), SETUP),
                   (("second-look",), HAND), (("limit-what-the-agent-can-reach",), HAND),
                   (("step-in-on-signals",), HAND), (("model-and-budget-per-step",), HAND)])
_L4 = _raise(_L3, [(("put-rules-where-they-apply",), SETUP), (("isolate-side-work",), SETUP), (("prove-it-works",), SETUP),
                   (("limit-what-the-agent-can-reach",), SETUP), (("step-in-on-signals",), SETUP),
                   (("model-and-budget-per-step",), SETUP), (("enforce-fixed-rules-in-code",), SETUP),
                   (("give-the-agent-tools-made-for-it",), SETUP), (("runs-start-without-you",), SETUP),
                   (("fresh-run-per-task",), HAND), (("lead-a-team-of-agents",), HAND)])
DESIGN_LISTS = {"L2": _L2, "L3": _L3, "L4": _L4}  # the lists as designed (levels.md); a laptop reads countable() of them


def countable(lists, S):
    """The lists a laptop level is read from (labs/v15/CLAUDE.md, the 11-04 rule, applied 11-05): an entry is required only
    up to the rung a skeptic-approved check proves on any laptop (substrate `laptop_proven_at`, tools/laptop_counts.py);
    an entry no approved check proves is left out and becomes a question for the call. Why: a list that required habits
    no laptop can see read a person L1 whose setup met L2, and dropped them (L4) two levels over one unseen habit.
    A substrate built without laptop counts (an old substrate.json) keeps the lists as designed."""
    pr = {pid: p.get("laptop_proven_at") for pid, p in S.get("practices", {}).items()}
    lo = {pid: p.get("laptop_proven_min") or p.get("laptop_proven_at") for pid, p in S.get("practices", {}).items()}
    if not any(pr.values()):
        return lists
    out = {}
    for lv, lst in lists.items():
        keep = []
        for ids, need in lst:
            # an either/or entry counts only when every alternative is provable: if one isn't (reset with a handoff),
            # a laptop that doesn't see the other (compaction) can't tell a miss from the unprovable alternative.
            # And only from the lowest step a check sees: a check that sees only their setup (L3) is silent on the
            # same habit done by hand (L2), so an L2 entry it can't see is a call question (11-05, the merge of
            # fetch into rules-where-they-apply read a person L1 over it)
            if all(pr.get(i) and RANK[lo[i]] <= need for i in ids):
                keep.append((ids, min(need, min(RANK[pr[i]] for i in ids))))
        out[lv] = keep
    return out


def left_for_the_call(lists, S):
    """Per level, the designed entries countable() dropped or lowered: the call's questions, never a minus."""
    got = countable(lists, S)
    out = {}
    for lv, lst in lists.items():
        kept = {ids_k: n for ids_k, n in got[lv]}
        out[lv] = [(ids, need) for ids, need in lst
                   if not any(set(k) <= set(ids) and n >= need for k, n in kept.items())]
    return out


try:
    _S = json.load(open(os.path.join(HERE, "substrate.json")))
except (OSError, ValueError):
    _S = {}
LEVEL_LISTS = countable(DESIGN_LISTS, _S)
CALL_ONLY = left_for_the_call(DESIGN_LISTS, _S)
LEVEL_RULE = {
    "L4": f"every practice on the L4 list (their setup does most of them), things they built in at least "
          f"{MIN_BUILT_AREAS} areas, and evals on what they built (levels.md, 11-05)",
    "L3": "every practice on the L3 list: their setup carries the context, and they handle the rest at least by hand "
          "(levels.md, 11-05)",
    "L2": "every practice on the L2 list: the everyday habits, at least by hand (levels.md, 11-05)",
    "L1": f"evidence in at least {MIN_DISCIPLINES} disciplines, not yet every practice on the L2 list (levels.md, 11-05)",
    "can't tell": f"evidence in fewer than {MIN_DISCIPLINES} disciplines, too little to place a level (levels.md)",
}
STEP_WORD = {HAND: "they do it, at least by hand (L2+)", SETUP: "their setup does it (L3+)"}


# the board's build rows that are L4 building by the rulings (hooks, plugins, MCP servers they built = L4; a program that runs
# the agent = a runner), and the area each one builds in. Skills, agent roles, notes, instruction files and output styles
# are setup (L3), never an L4 build. A board row counts only when it is "built and used" (theirs, and it ran).
BOARD_BUILD_AREAS = {"cap-hooks": "harness", "cap-plugins-own": "harness", "cap-mcp-write": "harness",
                     "cap-own-rig": "orchestration", "cap-sdk": "orchestration", "cap-event-runs": "orchestration"}


def board_areas(board):
    """Areas with a deterministic L4 build, from the capability board: the same answer on every run of the same laptop,
    where the AI pass's L4 readings vary between runs (11-06: one laptop read builds in 4 areas, then in 2)."""
    return sorted({BOARD_BUILD_AREAS[r["id"]] for r in (board or {}).get("rows") or []
                   if r.get("id") in BOARD_BUILD_AREAS and r.get("state") == "built and used"})


def level_pointer(S, rung, states=None, built_by_board=()):
    """levels.md: the highest level whose list is fully met (11-05; each level takes the level below's list, which
    retires the 10-13 "a level below never blocks a level above"). Only shown rungs count (never "said").
      L4  the L4 list, plus things they built shown at L4 in at least MIN_BUILT_AREAS different disciplines (one built
          thing counts for one area: practices resting on the same basis count once), plus evals on what they built
          (retest-agent-setup at L3+; "an eval, period", 11-02). One thing may back two list practices (11-05: four
          pairs of practices describe the same build at L4, e.g. a gate before the finish check and a run kept going
          until a script-checked pass); only the areas count each built thing once.
      L3, L2  their lists. One thing may back two habits here.
      L1  shown evidence in enough disciplines, no list met.
      can't tell  shown evidence in fewer than MIN_DISCIPLINES disciplines.
    could_be: the next level up when everything it still lacks is something the laptop couldn't see (no row, "not
    read", or a reading kept as "could be"), never what it looked for and didn't find. Its missing practices are the
    call's questions. `states`: practice id → "seen" / "looked, not found" / "not read" (run() passes them)."""
    states = states or {}
    name = lambda pid: (S["practices"].get(pid) or {}).get("name", pid)  # noqa: E731
    shown = {pid: r for pid, r in rung.items() if r.get("rung")}
    counted, used, shared = {}, {}, {}
    for pid, r in sorted(shown.items(), key=lambda kv: (-RANK[kv[1]["rung"]], kv[0])):
        b = r.get("basis")
        if b and b in used:
            shared[pid] = used[b]
            continue
        if b:
            used[b] = pid
        counted[pid] = r
    by_disc = {}
    for pid, r in counted.items():
        by_disc.setdefault(S["practices"][pid]["discipline"], []).append(RANK[r["rung"]])
    best = {d: max(v) for d, v in by_disc.items()}
    built = sorted(pid for pid, r in counted.items() if r["rung"] == "L4" and pid != EVAL_GATE)
    built_areas = sorted({S["practices"][pid]["discipline"] for pid in built} | set(built_by_board))
    eval_gate = RANK.get((rung.get(EVAL_GATE) or {}).get("rung") or "", 0) >= 3
    builds_ok = len(built_areas) >= MIN_BUILT_AREAS and eval_gate

    def have(pid):
        return RANK.get((shown.get(pid) or {}).get("rung") or "", 0)

    def unseen(pid, need):
        r = rung.get(pid) or {}
        if RANK.get(r.get("could_be_rung") or "", 0) >= need:
            return True
        return not r.get("rung") and states.get(pid, "not read") == "not read"

    missing = {lv: [(ids, need) for ids, need in lst if not any(have(i) >= need for i in ids)]
               for lv, lst in LEVEL_LISTS.items()}
    met = {"L2": not missing["L2"], "L3": not missing["L3"], "L4": not missing["L4"] and builds_ok}

    def candidate(lv):
        if lv == "L4" and not builds_ok:
            return False
        return bool(missing[lv]) and all(any(unseen(i, need) for i in ids) for ids, need in missing[lv])

    top = next((lv for lv in ("L4", "L3", "L2") if met[lv]), "L1")
    if len(best) < MIN_DISCIPLINES:
        lvl = "can't tell"
        could = top if top != "L1" else None
    else:
        lvl = top
        nxt = {"L1": "L2", "L2": "L3", "L3": "L4"}.get(lvl)
        could = nxt if nxt and candidate(nxt) else None

    def entry(ids, need):
        return f"{' or '.join(name(i) for i in ids)}: {STEP_WORD[need]}"

    reasons = []
    if lvl == "can't tell":
        reasons.append(f"shown evidence in {len(best)} discipline{'s' if len(best) != 1 else ''}: too little to place "
                       "a level" + (f"; could be {could}" if could else ""))
    elif lvl != "L1":
        reasons.append(f"meets every practice on the {lvl} list ({len(LEVEL_LISTS[lvl])})")
    nxt = {"L1": "L2", "L2": "L3", "L3": "L4", "can't tell": None}.get(lvl)
    if nxt:
        miss = missing[nxt]
        if miss:
            reasons.append(f"for {nxt}, still missing ({len(miss)} of {len(LEVEL_LISTS[nxt])}): "
                           + "; ".join(entry(ids, need) for ids, need in miss) + ". Ask on the call.")
        if nxt == "L4":
            if built:
                reasons.append(f"built things that change how the agent runs: {', '.join(built)}")
            if len(built_areas) < MIN_BUILT_AREAS:
                reasons.append(f"what they built shows in {len(built_areas)} area{'s' if len(built_areas) != 1 else ''}"
                               f"{' (' + ', '.join(built_areas) + ')' if built_areas else ''}; L4 needs "
                               f"{MIN_BUILT_AREAS}: ask on the call what else they built")
            if not eval_gate:
                reasons.append("runs evals on what they built was not shown (a saved set of cases re-run after a "
                               "change): ask on the call")
    elif lvl == "L4" and built:
        reasons.append(f"built things that change how the agent runs, in {len(built_areas)} areas: {', '.join(built)}")
    call_q = CALL_ONLY.get(nxt or lvl, []) if lvl != "can't tell" else []
    if call_q:
        reasons.append(f"no laptop can count these yet, so they are questions for the call, never a minus: "
                       + "; ".join(entry(ids, need) for ids, need in call_q))
    dropped = sorted(shared)
    if dropped:
        reasons.append(f"counted once toward the areas built in (same evidence as a higher practice): {', '.join(dropped)}")
    no_ev = [d["id"] for d in S["disciplines"] if d["id"] not in best]
    if no_ev:
        reasons.append(f"no evidence yet in: {', '.join(no_ev)} (ask on the call; never a minus)")

    def ev_of(pids):
        return [{"practice": pid, "rung": shown[pid]["rung"], "discipline": S["practices"][pid]["discipline"],
                 "evidence_row": shown[pid].get("source"), "why": shown[pid].get("why")} for pid in pids if pid in shown]

    def conds(lv):
        return [{"test": entry(ids, need), "met": any(have(i) >= need for i in ids), "by": ev_of(ids)}
                for ids, need in LEVEL_LISTS[lv]]

    trail = [
        {"level": "L4", "rule": LEVEL_RULE["L4"], "conditions": conds("L4") + [
            {"test": f"things they built, shown at L4 in at least {MIN_BUILT_AREAS} different areas (one built thing "
                     "counts for one area)", "met": len(built_areas) >= MIN_BUILT_AREAS, "by": built_areas},
            {"test": "re-tests what they built with evals (retest-agent-setup at L3+)", "met": eval_gate,
             "by": ev_of([EVAL_GATE] if eval_gate else [])}]},
        {"level": "L3", "rule": LEVEL_RULE["L3"], "conditions": conds("L3")},
        {"level": "L2", "rule": LEVEL_RULE["L2"], "conditions": conds("L2")},
        {"level": "minimum", "rule": f"shown evidence in at least {MIN_DISCIPLINES} disciplines, else can't tell",
         "conditions": [{"test": "disciplines with shown evidence", "met": len(best) >= MIN_DISCIPLINES, "by": sorted(best)}]},
    ]
    return {"level": lvl, "could_be": could, "reasons": reasons,
            "decided_by": LEVEL_RULE.get(lvl), "trail": trail,
            "missing_for_next": {"level": nxt, "practices": [list(ids) for ids, _ in missing.get(nxt, [])]} if nxt else None,
            # what no laptop can count for the next level (or this one, at L4): the call's questions (countable())
            "call_questions": [list(ids) for ids, _ in call_q],
            "best_rung_by_discipline": {d: f"L{v}" for d, v in best.items()},
            # today's L4 conditions as decided here, for the read from things (things.py)
            "builds": {"areas": built_areas, "areas_ok": len(built_areas) >= MIN_BUILT_AREAS, "evals": eval_gate}}

def recommendations(S, rung, ev, top=3):
    """levels.md: a problem seen → the practice that handles it, one rung above where the person is → material.
    Rules (fresh audit 11-02, cases in test_engine.py):
    - only problems that show (seen True);
    - the practice comes from the problem's own handlers (handled_by, its own discipline); a cross-link
      (also_handled_in) only when none of its own can give a step;
    - "spread" (use what they have where the problem still shows) only when the problem's own practices all sit at
      their top rung; otherwise the cheapest climb;
    - never "build what you already built": a climb to a rung the evidence already points to (could_be) is skipped;
    - one card per piece of evidence: problems resting on the same first evidence source become one card;
    - ranked by how close the next rung is, then how directly it handles the problem (its place in handled_by),
      never by id order;
    - a "spread" card (the person is already at the top of the practice that answers the problem) is never the first
      card and comes after every climb; with no climb at all the first card says so (fallback_card(handled=True)), and
      its title names the practice they already do, not the problem, so title and body can't contradict (11-06: a
      card titled "Nothing is guarded" whose body said "you already do this at its top")."""
    seen = {}
    for e in ev:
        if e["kind"] == "challenge" and e.get("seen"):
            seen.setdefault(e["id"], e)
    tries = {e["id"]: e.get("try") for e in ev if e["kind"] == "practice" and e.get("try")}

    def step_for(pid):
        p = S["practices"].get(pid)
        if not p:
            return None
        have = RANK[rung[pid]["rung"]] if pid in rung and rung[pid]["rung"] else 1
        nxt = next((f"L{k}" for k in range(have + 1, 5) if p["ladder"].get(f"L{k}")), None)
        if nxt is None:
            return None
        cb = (rung.get(pid) or {}).get("could_be_rung")
        if cb and RANK[cb] >= RANK[nxt]:
            return None  # the evidence already points there: asking to build it would be "build what you built"
        return have, nxt

    recs, gaps = [], []
    for cid, e in seen.items():
        c = S["challenges"].get(cid)
        if not c:
            continue
        own = [x for x in c.get("handled_by") or [] if x in S["practices"]]
        cross = [x.split(":")[-1] for x in c.get("also_handled_in") or []]
        rec = None
        options = []
        for idx, pid in enumerate(own):
            st = step_for(pid)
            if st:
                have, nxt = st
                options.append((RANK[nxt] - have, RANK[nxt], idx, pid, have, nxt))
        strong = sorted(((RANK[rung[pid]["rung"]], -i, pid) for i, pid in enumerate(own)
                         if pid in rung and rung[pid]["rung"] and RANK[rung[pid]["rung"]] >= 3), reverse=True)
        if not options and strong:
            # own practices can't climb (top rung, or already pointing there): spread what they have
            have, _, pid = strong[0]
            tip = e.get("try") or tries.get(pid) or (
                f"You already do this at its top: use it where this problem still shows. {e['why']}")
            p = S["practices"][pid]
            rec = {"practice": pid, "now": f"L{have}", "next": "spread", "next_step": tip, "try": None,
                   "weight": 2.5, "rank": (0, 0)}
        elif options:
            options.sort()
            jump, _, idx, pid, have, nxt = options[0]
            p = S["practices"][pid]
            rec = {"practice": pid, "now": f"L{have}" if have > 1 else "not seen", "next": nxt,
                   "next_step": p["ladder"][nxt], "try": e.get("try"), "weight": 3 - jump, "rank": (jump, idx)}
        else:
            for idx, pid in enumerate(cross):
                st = step_for(pid)
                if st:
                    have, nxt = st
                    p = S["practices"][pid]
                    rec = {"practice": pid, "now": f"L{have}" if have > 1 else "not seen", "next": nxt,
                           "next_step": p["ladder"][nxt], "try": e.get("try"), "weight": 1, "rank": (9, idx)}
                    break
        if not rec:
            continue
        p = S["practices"][rec["practice"]]
        learn = p.get("learn") or []
        if rec["next"] != "spread" and not [l for l in learn if l["kind"] in ("playbook", "station")] and not any(
                g["practice"] == rec["practice"] and g["rung"] == rec["next"] for g in gaps):
            gaps.append({"practice": rec["practice"], "name": p["name"], "rung": rec["next"]})
        rec.update({"challenge": cid, "challenge_name": c["name"], "problem_name": c["name"], "term": c["term"], "grade": c["grade"],
                    "evidence": e["why"], "practice_name": p["name"], "discipline": p["discipline"], "learn": learn,
                    "evidence_key": (e.get("evidence_keys") or [None])[0]})
        if rec["next"] == "spread":
            rec["challenge_name"] = f"You already do this: {p['name']}"
        recs.append(rec)
    recs.sort(key=lambda r: (-r["weight"], r["rank"]))
    # one card per piece of evidence, and one per step: merged cards name every problem they answer
    merged, by_ev = {}, {}
    for r in recs:
        k = (r["practice"], r["next"], r["next_step"] if r["next"] == "spread" else "")
        ek = r.get("evidence_key")
        target = merged.get(k) or (by_ev.get(ek) if ek else None)
        if target is not None:
            target.setdefault("also_for", []).append(r["problem_name"])
            continue
        merged[k] = r
        if ek:
            by_ev[ek] = r
    recs = list(merged.values())
    for r in recs:
        r.pop("rank", None)
        # never a step with nothing to read (ruling 11-03): no playbook page or hands-on station yet → the playbook, or a call
        if not any(l.get("kind") in ("playbook", "station") for l in r["learn"]):
            r["learn"] = list(r["learn"]) + fallback_learn()
    climbs = [r for r in recs if r["next"] != "spread"]
    spreads = [r for r in recs if r["next"] == "spread"]
    ordered = (climbs or [fallback_card(handled=bool(spreads))]) + spreads
    return ordered[:top], ordered[top:], gaps


PLAYBOOK_HOME = {"kind": "playbook", "title": "The Proxify agentic engineering playbook", "ref": "/"}
# A booking link with no person's name in it: guard.py fails the build on personal names, and a link naming one person
# breaks when they move on. Until there is one, a run can pass it in AGENTIC_SETUP_BOOK_A_CALL.
BOOK_A_CALL = ""


def fallback_learn():
    """What every recommendation can point to when its practice has no material yet: the playbook, and a call."""
    url = os.environ.get("AGENTIC_SETUP_BOOK_A_CALL") or BOOK_A_CALL
    return [PLAYBOOK_HOME] + ([{"kind": "call", "title": "Book a call with the Proxify agentic engineering team",
                                "ref": url}] if url else [])


def fallback_card(handled=False):
    """Never an empty list (ruling 11-03): when no problem a practice handles shows, one card that says so and points
    to the playbook or a call. It names no practice and no rung, so it never reads as a climb. `handled`: problems did
    show, but each one is answered by a practice the person is already at the top of, so there is no step up to point at."""
    return {"practice": None, "practice_name": "Pick your next step with someone",
            "discipline": "", "challenge": "none-seen", "challenge_name": "No single problem stood out in these logs",
            "term": "none", "grade": None, "weight": 0, "now": None, "next": "call", "fallback": True,
            "evidence": ("Every problem that showed in your logs is answered by a practice you already do at its top, so there "
                         "is no step up to point at; the cards below say where it still shows." if handled else
                         "None of the problems a practice handles showed in your logs, so there is no one change to "
                         "point at."),
            "next_step": "Read the playbook for the practices you want to grow, or book a call to choose the next step "
                         "together.",
            "try": None, "learn": fallback_learn(), "evidence_key": None}


VERDICT_WORD = {"strong": "Strong", "partly": "Partly", "by_hand": "By hand", "cant_tell": "Can't tell"}
VERDICT_SAYS = {"strong": "Your setup handles most of this.",
                "partly": "Your setup handles part of this; the rest you do yourself or we haven't seen yet.",
                "by_hand": "You handle this yourself, each time.",
                "cant_tell": "Too little in your logs to say: a question for a call."}
VERDICT_ORDER = ["strong", "partly", "by_hand", "cant_tell"]
BACKED_BY = ("built", "count", "file")  # evidence `fact` values that mean a count or a file backs the rung


SENTENCE_END = re.compile(r"(?<!\be\.g)(?<!\bi\.e)(?<=[.!?])\s+(?=[A-Z])")  # not inside "e.g." or "i.e."


def first_sentence(text):
    """The first sentence of a substrate text."""
    text = (text or "").strip()
    return SENTENCE_END.split(text, maxsplit=1)[0] if text else ""


def fit(text, limit):
    """As many whole sentences of a substrate text as fit in `limit` characters, so a card line stays one short
    paragraph. When even the first sentence is longer, it ends at its last comma before the limit."""
    text = (text or "").strip()
    out = ""
    for sent in SENTENCE_END.split(text):
        if len(out) + len(sent) + (1 if out else 0) > limit:
            break
        out = f"{out} {sent}".strip()
    if out or len(text) <= limit:
        return out or text
    head = SENTENCE_END.split(text)[0]
    cut = head.rfind(",", 0, limit)
    return (head[:cut] if cut > 40 else head[:limit].rsplit(" ", 1)[0]).rstrip(" ,;:") + "."


def _first_count(text):
    """The first plain count a piece of evidence states ("5 sessions", "101 of 344 commits"): how much it rests on.
    A percentage, an id or a date is not a count."""
    m = re.search(r"(?<![\w.%-])(\d[\d,]*)(?![\w%-]|\.\d)", text or "")
    return int(m.group(1).replace(",", "")) if m else 0


def _verdict_for(practices):
    """strong · partly · by_hand · cant_tell for one area (11-06 ask: "is this good or bad? what now?").
    A practice has a reading when it has a rung, or the logs were searched and it was not found. "Not read" and a
    rung that is only a hint (could_be) are no reading, and no reading is never a minus: it can only make the
    area "can't tell"."""
    n = len(practices)
    read = [p for p in practices if p.get("rung") or (p.get("state") == "looked, not found" and not p.get("could_be_rung"))]
    high = [p for p in practices if RANK.get(p.get("rung") or "", 0) >= 3]
    if not n or len(read) * 2 < n:
        return "cant_tell"
    if len(high) * 2 >= n:
        return "strong"
    if high:
        return "partly"
    return "by_hand" if any(p.get("rung") for p in practices) else "cant_tell"


def _next_step_of(rec, S=None):
    """One recommendation as the area card's "Try next": what to work on, why, and where to read about it."""
    learn = [l for l in rec.get("learn") or [] if l.get("kind") in ("playbook", "station")] or list(rec.get("learn") or [])
    kind = "fallback" if rec.get("fallback") else ("spread" if rec.get("next") == "spread" else "climb")
    return {"kind": kind, "practice": rec.get("practice"), "title": rec.get("practice_name"),
            "short_name": (((S or _S).get("practices") or {}).get(rec.get("practice")) or {}).get("short_name"),
            "problem": rec.get("challenge_name"), "step": rec.get("next_step"), "next_rung": rec.get("next"),
            "link": learn[0] if learn else None}


_STEP_WORDS = {  # what a technique for that step names: the setup (L3) or something built (L4); techniques carry no level
    "L3": ("CLAUDE.md", "AGENTS.md", "settings", "skill", "command", "rule", "template", "standing", "permission",
           "subfolder", "memory", "index", "config", "alias", "agent definition", "worktree"),
    "L4": ("hook", "plugin", "MCP", "script", "program", "CI", "eval", "runner", "server", "SDK", "headless", "test",
           "schedule", "cron", "watcher", "gate"),
}


def _technique_for(P, rung):
    """The technique that does this practice at `rung`: by hand (L2) = one naming no setup or build; L3/L4 = the one
    naming the most of that step's words. None when nothing fits: no 'how' beats a by-hand how under a setup step."""
    ts = [t for t in P.get("techniques") or [] if t.get("how") and t.get("tool") in ("Claude Code", "any")]
    import re as _re
    def score(t, words):
        return sum(1 for w in words if _re.search(rf"(?i)\b{_re.escape(w)}", t["how"]))
    if rung == "L2":
        plain = [t for t in ts if not score(t, _STEP_WORDS["L3"] + _STEP_WORDS["L4"])]
        return plain[0] if plain else None
    best = max(ts, key=lambda t: score(t, _STEP_WORDS[rung]), default=None)
    return best if best and score(best, _STEP_WORDS[rung]) else None


def you_form(text):
    """A ladder step (written about 'them', for the interviewer) said to the reader: they/their/them -> you/your/you."""
    import re
    if not text:
        return text
    for a, b in (("They're", "You're"), ("they're", "you're"), ("Their", "Your"), ("their", "your"), ("They", "You"),
                 ("they", "you"), ("themselves", "yourself")):
        text = re.sub(rf"\b{a}\b", b, text)
    return text


def verdicts(result, S=None):
    """Advice per area, from what the run already holds (result: disciplines, recommendations, more_recommendations).
    Pure; it adds no evidence and moves no rung. Rules, each with its test in test_engine.py:
      verdict   _verdict_for: strong = at least half of the area's practices at L3+; partly = at least one; by hand =
                rungs but none at L3+; can't tell = fewer than half have any reading. An unread practice is never a minus.
      strongest the highest rung (a tie goes to one a count or a file backs), with its ladder step at that rung.
      gap       the lowest rung among practices actually seen or searched and not found (never "not read"); none when
                every one of them is at L3+. Why it matters: what goes wrong in a problem it handles, one seen on this
                laptop first.
      next      the gap's next step up its own ladder (what to do, how in today's tools, where to read); with no gap,
                the strongest practice's next step; then the area's first recommendation; the playbook / a call last.
    summary: the strongest area, the one gap area that matters most (the seen problem whose evidence states the largest
    count), and the first recommendation of the run."""
    S = S or _S
    areas = result.get("disciplines") or []
    recs = list(result.get("recommendations") or []) + list(result.get("more_recommendations") or [])
    where = {p["id"]: d["id"] for d in areas for p in d["practices"]}
    facts = {e["id"]: e for e in result.get("evidence") or [] if e.get("kind") == "practice" and e.get("id")}
    seen_problem = {c["id"]: c for d in areas for c in d.get("challenges") or [] if c.get("seen")}
    handled = {}
    for key, c in (S.get("challenges") or {}).items():  # the dict also holds alias keys: count each problem once, by its id
        cid = c.get("id", key)
        for pid in c.get("handled_by") or []:
            if cid not in handled.setdefault(pid, []):
                handled[pid].append(cid)
    short = lambda pid, name: ((S.get("practices") or {}).get(pid) or {}).get("short_name") or name  # noqa: E731

    def backed(p):
        e = facts.get(p["id"]) or {}
        return e.get("fact") in BACKED_BY and not e.get("not_counted")

    def strongest(ps):
        have = [p for p in ps if p.get("rung")]
        if not have:
            return None
        p = max(have, key=lambda p: (RANK[p["rung"]], backed(p), STRENGTH.get(p.get("strength"), 0), -ps.index(p)))
        return {"practice": p["id"], "name": p["name"], "short_name": short(p["id"], p["name"]), "rung": p["rung"],
                "reason": fit((p.get("ladder") or {}).get(p["rung"]), 170),
                "reason_full": (p.get("ladder") or {}).get(p["rung"]), "backed_by_a_count": backed(p)}

    def gap(ps):
        # seen = has a rung; looked, not found = the logs were searched. "Not read" and a bare hint are no reading.
        cands = [p for p in ps if p.get("rung") or (p.get("state") == "looked, not found" and not p.get("could_be_rung"))]
        cands = [p for p in cands if RANK.get(p.get("rung") or "", 0) < 3]
        if not cands:
            return None
        why_of = lambda p: [c for c in handled.get(p["id"], [])]  # noqa: E731
        p = min(cands, key=lambda p: (RANK.get(p.get("rung") or "", 0), not any(c in seen_problem for c in why_of(p)),
                                      ps.index(p)))
        cids = why_of(p)
        cid = next((c for c in cids if c in seen_problem), cids[0] if cids else None)
        c = (S.get("challenges") or {}).get(cid) or {}
        return {"practice": p["id"], "name": p["name"], "short_name": short(p["id"], p["name"]), "rung": p.get("rung"),
                "state": p.get("state"), "challenge": cid, "challenge_name": c.get("name"),
                "challenge_seen_here": cid in seen_problem, "why": fit(c.get("what_goes_wrong"), 230) or None}

    def climb(pid, rung, problem=None):  # noqa: C901
        """The next step up this practice's own ladder, in its words, with how it's done today and where to read:
        never 'read the playbook' as the advice itself (11-05: "we don't need shitty advice")."""
        P = (S.get("practices") or {}).get(pid) or {}
        lad = P.get("ladder") or {}
        nxt = {0: "L2", 1: "L2", 2: "L3", 3: "L4"}.get(RANK.get(rung or "", 0))
        if not nxt or not lad.get(nxt):
            return None
        tech = _technique_for(P, nxt)
        learn = [l for l in P.get("learn") or [] if l.get("kind") in ("playbook", "station")]
        return {"kind": "climb", "practice": pid, "title": P.get("name"), "short_name": P.get("short_name"),
                "problem": problem, "step": you_form(lad[nxt]), "next_rung": nxt,
                "how": re.sub(r"\s*\(L[1-4]\)", "", you_form((tech or {}).get("how")) or "") or None, "how_tool": (tech or {}).get("tool"),
                "link": learn[0] if learn else None}

    def area_next(did, g=None, top=None):
        # 1. the gap: one step up its ladder; 2. no gap: the strongest practice not yet at L4, one step up
        if g:
            step = climb(g["practice"], g.get("rung"), g.get("challenge_name"))
            if step:
                return step
        if top and top.get("rung") != "L4":
            step = climb(top["practice"], top.get("rung"))
            if step:
                return step
        for r in recs:
            if not r.get("fallback") and (r.get("discipline") or where.get(r.get("practice"))) == did:
                return _next_step_of(r, S)
        card = fallback_card()
        return {"kind": "fallback", "practice": None, "title": "Read the playbook for this area, or book a call",
                "problem": None, "step": card["next_step"], "next_rung": None, "link": card["learn"][0]}

    out = {}
    for d in areas:
        ps = d["practices"]
        v = _verdict_for(ps)
        out[d["id"]] = {"id": d["id"], "name": d["name"], "verdict": v, "word": VERDICT_WORD[v], "says": VERDICT_SAYS[v],
                        "practices": len(ps), "at_l3_plus": sum(1 for p in ps if RANK.get(p.get("rung") or "", 0) >= 3),
                        "strongest": strongest(ps), "gap": gap(ps)}
        out[d["id"]]["next"] = area_next(d["id"], out[d["id"]]["gap"], out[d["id"]]["strongest"])
    # summary
    ranked = sorted((a for a in out.values() if a["verdict"] != "cant_tell"),
                    key=lambda a: (VERDICT_ORDER.index(a["verdict"]), -a["at_l3_plus"], list(out).index(a["id"])))
    best = ranked[0] if ranked else None
    problems = sorted(((_first_count(c.get("why")), c) for c in seen_problem.values()),
                      key=lambda t: -t[0])  # stable: substrate order breaks a tie
    area_of_problem = {c["id"]: d["id"] for d in areas for c in d.get("challenges") or []}
    gap_area = next(((out[area_of_problem[c["id"]]], c) for _, c in problems
                     if out[area_of_problem[c["id"]]]["gap"]), None)
    if not gap_area:  # no seen problem sits in an area with a gap: the weakest area that has one
        weak = [a for a in out.values() if a["gap"] and a["verdict"] != "cant_tell"]
        weak.sort(key=lambda a: (-VERDICT_ORDER.index(a["verdict"]), list(out).index(a["id"])))
        gap_area = (weak[0], None) if weak else None
    first = next((r for r in recs if not r.get("fallback")), None)
    summary = {
        "strongest": {"area": best["id"], "name": best["name"], "verdict": best["verdict"], "word": best["word"]} if best else None,
        "gap": ({"area": gap_area[0]["id"], "name": gap_area[0]["name"], "practice": gap_area[0]["gap"]["practice"],
                 "short_name": gap_area[0]["gap"]["short_name"], "challenge": (gap_area[1] or {}).get("id"),
                 "challenge_name": (S.get("challenges") or {}).get((gap_area[1] or {}).get("id"), {}).get("name"),
                 "evidence": (gap_area[1] or {}).get("why"), "why": gap_area[0]["gap"]["why"]} if gap_area else None),
        # the first thing to try is the biggest gap's next step, so the summary's gap and its advice are the same thing
        "next": (out[gap_area[0]["id"]]["next"] if gap_area else
                 (_next_step_of(first, S) if first else {**area_next(""), "title": "Read the playbook, or book a call"})),
    }
    return {"disciplines": out, "summary": summary}


REFINE_CHECK = ("built-for-the-agent-roll-call", "L4", 5)   # weeks with their changes to things they built (portable check)
MIN_REFINE_WEEKS = 3


def refine_weeks(run_dir):
    """Weeks with their changes to things they built (the trusted portable check), or None when it was not counted."""
    try:
        for c in json.load(open(os.path.join(run_dir, "portable-checks.json"))).get("checks") or []:
            if (c.get("pid"), c.get("L"), c.get("i")) == REFINE_CHECK and c.get("status") == "counted":
                return c.get("value")
    except (OSError, ValueError):
        pass
    return None


def refined_over_time(pointer, run_dir):
    """His L4 (11-05, 'like a vim nerd'): they build their own tooling AND keep refining it. A laptop L4 also needs changes
    to things they built in at least MIN_REFINE_WEEKS different weeks (a trusted portable check). Fewer weeks: L3, could
    be L4, ask on the call. The check not run or timed out: the same, L3 could be L4 with the reason said plainly, never L4:
    what decides a level has to be seen (labs/v15/CLAUDE.md, the rule the setup read follows for L3). Before 11-12 this check
    alone was read as met when it gave no answer, and it timed out in about 1 run in 10 under load."""
    if pointer.get("level") != "L4":
        return pointer
    weeks = refine_weeks(run_dir)
    p = dict(pointer)
    cond = {"test": f"keeps refining what they built: changes to it in at least {MIN_REFINE_WEEKS} different weeks",
            "met": weeks is not None and weeks >= MIN_REFINE_WEEKS,
            "by": [f"{weeks} weeks" if weeks is not None else "not counted this time (the check timed out or did not run)"]}
    p["trail"] = [({**t, "conditions": t["conditions"] + [cond]} if t.get("level") == "L4" else t) for t in p.get("trail") or []]
    if not cond["met"]:
        p.update(level="L3", could_be="L4", decided_by=LEVEL_RULE["L3"])
        p["reasons"] = list(p.get("reasons") or []) + [
            (f"built at L4, but changes to what they built show in {weeks} week{'s' if weeks != 1 else ''} "
             f"(L4 asks for {MIN_REFINE_WEEKS}): could be L4, ask on the call how they keep refining it") if weeks is not None else
            f"built at L4, but the weeks they changed what they built could not be counted this time (the check timed out or did not "
            f"run; L4 asks for {MIN_REFINE_WEEKS}): could be L4, ask on the call how they keep refining it"]
    else:
        p["reasons"] = list(p.get("reasons") or []) + [f"keeps refining what they built: {weeks} weeks"]
    return p


SETUP_READ_PARTS = 2  # of the three parts of "their setup carries the context" (rules, corrections, memory); a guess until hand-checked on member laptops


def setup_read(run_dir):
    """What the setup read says, for the practice read's gate and for the read from things: a part is shown only when
    the code's map shows its structure AND the agent read it as "shown" with a quote the code found in the file.
    `ok` = enough parts shown; `held` = the holding signs that fired. No setup-read.json: ran False, nothing shown."""
    try:
        sr = json.load(open(os.path.join(run_dir, "setup-read.json")))
    except (OSError, ValueError):
        sr = None
    parts = (sr or {}).get("parts") or []
    shown = [g for g in parts if level_things.part_state(g)[0] == "shown"]   # things.part_state owns "a part is shown"
    return {"ran": sr is not None and sr.get("ran", True) is not False, "file": sr or {}, "parts": parts, "shown": shown,
            "holds": (sr or {}).get("holds") or [], "held": [h for h in (sr or {}).get("holds") or [] if h.get("fired")],
            "ok": len(shown) >= SETUP_READ_PARTS, "need": SETUP_READ_PARTS}


def setup_read_gate(pointer, run_dir):
    """L3 and L4 need the setup read (setup-snapshot experiments/2026-11-11-setup-read): the person's agent-facing files,
    mapped by code and read by an agent, show a setup that carries the context. The level guide's border: "Files and
    skills are not the line; who carries the context is. It is one picture across the Level 3 abilities, never a count
    of them." A part counts only when the code's map shows its structure AND the agent's reading is "shown" with a quote
    the code found in the file; either alone is "could be".

    Unlike every other check, a setup read that did not run is NOT "never a minus": the level is read as L2, could be
    L3. Why: on 11-11 a person who is not an engineer read L3 on six practices, five of them by-hand counts of what
    Claude Code does by default and the sixth an AGENTS.md per repo. What decides a level has to be seen; only what
    is beside the point may go unseen.

    A holding sign is the opposite habit, counted from Claude Code's own records (sessions that only ever compact
    automatically). The level guide holds an L3 at L2 on it (v13 allocations.yaml `instead:`), and so does this."""
    if pointer.get("level") not in ("L3", "L4"):
        return pointer
    r = setup_read(run_dir)
    sr, parts, shown, held, ok = (r["file"] if r["ran"] else None), r["parts"], r["shown"], r["held"], r["ok"]
    p = dict(pointer)
    was = pointer["level"]
    conds = [{"test": f"their setup carries the context: its files show it in at least {SETUP_READ_PARTS} of "
                      f"{len(parts) or 3} parts (instructions the agent starts with, corrections that stick, memory)",
              "met": ok, "by": [g.get("name") or g.get("id") for g in shown] or
                               (["the setup read did not run on this laptop"] if sr is None else [])},
             {"test": "Claude Code's records show no sign of the opposite habit", "met": not held,
              "by": [h.get("why") or h.get("id") for h in held]}]
    p["trail"] = [({**t, "conditions": t["conditions"] + conds} if t.get("level") in ("L3", "L4") else t)
                  for t in p.get("trail") or []]
    p["setup_read"] = {"ran": sr is not None, "parts": parts, "holds": (sr or {}).get("holds") or [],
                       "shown": [g.get("id") for g in shown]}
    reasons = list(p.get("reasons") or [])
    if ok and not held:
        reasons.append(f"their files show a setup that carries the context: {', '.join(g.get('name') or g['id'] for g in shown)}")
    else:
        # the list was met, so L2 stands; what the files did not show is what a call, or the next run, settles
        p.update(level="L2", decided_by=LEVEL_RULE["L2"], could_be=None if held else was,
                 missing_for_next={"level": "L3", "practices": []})
        if sr is None:
            reasons.append(f"the practices on the {was} list are met, but the setup read did not run, so the laptop "
                           f"can't say the setup carries the context: could be {was}")
        elif not ok:
            miss = [g for g in parts if g not in shown]
            reasons.append(f"the practices on the {was} list are met, but the files show a setup that carries the "
                           f"context in {len(parts)} parts: {level_things.parts_said(parts)} ({was} asks for {SETUP_READ_PARTS} fully shown): "
                           + "; ".join(f"{g.get('name') or g.get('id')}: {g.get('why') or g.get('judged') or 'not read'}"
                                       for g in miss))
        for h in held:
            reasons.append(f"held at L2: {h.get('why') or h.get('id')}")
    p["reasons"] = reasons
    return p


def certainty(run_dir, ev, S=None):
    """How far to trust this laptop's read, from what it stands on (11-05: ~148 members' levels hang on it):
      high    at least 14 days with sessions, at least 8 practices backed by a count or a file, in at least 4 areas
      medium  at least 7 days and at least 4 backed practices
      low     anything less: read it as a hint, and ask on a call
    Days come from the portable checks' log metadata (what the laptop really holds, not the window asked for)."""
    S = S or _S
    days = None
    try:
        meta = json.load(open(os.path.join(run_dir, "portable-checks.json"))).get("meta") or {}
        days = (meta.get("logs") or {}).get("days_with_sessions")
    except (OSError, ValueError):
        pass
    backed = {e["id"] for e in ev if e.get("kind") == "practice" and e.get("rung") and e.get("fact") in BACKED_BY
              and not e.get("not_counted")}
    areas = {(S.get("practices") or {}).get(p, {}).get("discipline") for p in backed} - {None}
    if (days or 0) >= 14 and len(backed) >= 8 and len(areas) >= 4:
        level = "high"
    elif (days or 0) >= 7 and len(backed) >= 4:
        level = "medium"
    else:
        level = "low"
    return {"level": level, "days_with_sessions": days, "backed_practices": len(backed), "areas": len(areas),
            "say": {"high": "Enough on this laptop to trust the read.",
                    "medium": "A fair read; confirm the level on a call.",
                    "low": "Too little on this laptop: treat it as a hint and ask on a call."}[level]}


def calibration(pointer, call_level=None):
    """The laptop's answer for a member who already had an evaluation call (11-05, the tool's one job: fill the call's
    gaps so the members waiting can be calibrated without another call; ruled 11-03: "an indicator, never a final
    score"). The laptop level is what its portable, valid checks back (setup and builds; habits are the call's), so it can
    only ever raise a question upward: a laptop never lowers a level, because what it can't see is not evidence.
      above     the laptop backs a higher level than the call found: a candidate, to confirm on a call
      confirms  the laptop backs the call's level
      stands    the laptop backs less, or can't tell: the call level stands
    Without a call level the verdict is "no call level" and the page shows only what the laptop backs."""
    lv = pointer.get("level")
    backs = lv if lv in ("L2", "L3", "L4") else None
    out = {"call_level": call_level, "laptop_backs": backs, "could_be": pointer.get("could_be")}
    if call_level not in RANK:
        out.update(verdict="no call level",
                   say=(f"The laptop backs {backs}" if backs else "The laptop backs no level on its own")
                       + " from what it can count (setup and builds). Compare it with the call's level.")
    elif backs and RANK[backs] > RANK[call_level]:
        out.update(verdict="above", say=f"The laptop shows more than the call found ({call_level}): a candidate for "
                                        f"{backs}. Confirm it on a call.")
    elif backs == call_level:
        out.update(verdict="confirms", say=f"The laptop backs the call's {call_level}.")
    else:
        out.update(verdict="stands", say=f"The call's {call_level} stands. The laptop "
                                         + (f"backs {backs}" if backs else "can't tell from what it can count")
                                         + "; it never lowers a level, because what it can't see is not evidence.")
    return out


def retire_ids(S, ev):
    """Evidence for a retired practice (11-05 rewrite) counts for the practice it merged into, once; evidence for one
    moved to the agent-builders track or dropped is not a member's evidence and leaves. The checks and the AI pass may
    still name old ids; the level, the areas built in and every page only ever see live practices."""
    P, out, seen = S.get("practices", {}), [], set()
    for e in ev:
        if e.get("kind") == "practice":
            p = P.get(e.get("id")) or {}
            if p.get("retired"):
                into = p.get("merged_into")
                if not into or (P.get(into) or {}).get("retired"):
                    continue
                e = {**e, "id": into, "why": e.get("why") or "", "merged_from": e["id"]}
        out.append(e)
    return out


def run(run_dir, call_level=None):
    S = json.load(open(os.path.join(HERE, "substrate.json")))
    ev = retire_ids(S, laptop_evidence.rows(run_dir))
    state = {(e["kind"], e["id"]): e for e in ev}
    rung = picture(S, ev)
    recs, more, gaps = recommendations(S, rung, ev)
    # two old problem ids that merged into one (aliases) must not give the same advice twice
    seen_rec, kept = set(), []
    for r in recs + more:
        key = ((S["challenges"].get(r.get("challenge")) or {}).get("id") or r.get("challenge_name"), r.get("practice"))
        if key not in seen_rec:
            seen_rec.add(key)
            kept.append(r)
    recs, more = kept[:len(recs)] if len(kept) >= len(recs) else kept, kept[len(recs):]
    disciplines = []
    for d in S["disciplines"]:
        disciplines.append({
            "id": d["id"], "name": d["name"], "question": d["question"],
            # each practice carries its ladder (what each rung means for it) and how a laptop would show it, so the
            # page can say what a rung means and what "not seen" was looking for
            # state: seen (a rung) · looked, not found · not read (no source covers it, or no row at all)
            "practices": [{"id": pid, "name": S["practices"][pid]["name"], "ladder": S["practices"][pid]["ladder"],
                           "what_you_do": S["practices"][pid]["what_you_do"],
                           "laptop_evidence": S["practices"][pid].get("laptop_evidence"),
                           # what a laptop would show for it, per technique: the page's "we searched for"
                           "signals": [t["laptop_signal"] for t in S["practices"][pid].get("techniques") or [] if t.get("laptop_signal")][:3],
                           # a rung is a sighting: a check or a by-hand row can set one with no AI state row behind it, and
                           # "L3 · not read" read as a contradiction to a person (11-06)
                           "state": "seen" if (rung.get(pid) or {}).get("rung") else
                                    (state.get(("practice", pid)) or {}).get("state") or "not read",
                           "could_be_rung": (state.get(("practice", pid)) or {}).get("could_be_rung"),
                           "ask": (state.get(("practice", pid)) or {}).get("ask"),
                           "source": (state.get(("practice", pid)) or {}).get("source"),
                           **(rung.get(pid) or {"rung": None,
                                                "why": (state.get(("practice", pid)) or {}).get("why")})}
                          for pid in d["practices"]],
            "challenges": [{"id": e["id"], "name": S["challenges"][e["id"]]["name"], "seen": e["seen"], "why": e["why"],
                            "state": e.get("state"), "source": e["source"],
                            "laptop_signs": (S["challenges"][e["id"]].get("laptop_signs") or [])[:3],
                            "what_goes_wrong": S["challenges"][e["id"]]["what_goes_wrong"]}
                           for e in ev if e["kind"] == "challenge" and S["challenges"].get(e["id"], {}).get("discipline") == d["id"]],
            # the call's questions go where the laptop is blind: no row, not read, or can't tell
            "ask": [{"challenge": cid, "ask": S["challenges"][cid]["ask"]} for cid in d["challenges"]
                    if (state.get(("challenge", cid)) or {}).get("state", "not read") == "not read"
                    or (state.get(("challenge", cid)) or {}).get("seen", 0) is None][:3],
        })
    # what sits under every claim, keyed by the row's `source`: the sources and numbers it rests on, what was checked,
    # whether it held, the question for the call, and what the skeptic did with it
    trail = laptop_evidence.trail(run_dir)
    try:
        board = capability_board.board(run_dir)
    except Exception as e:  # noqa: BLE001  a file the board can't read must not break a run
        board = {"rows": [], "built_kinds": [], "uses_kinds": [], "error": type(e).__name__}
    pointer = level_pointer(S, rung, {pid: e.get("state") for (k, pid), e in state.items() if k == "practice"},
                            board_areas(board))
    # the read from things (things.py, a trial): the same builds, evals and refining this file decided, never its own
    weeks = refine_weeks(run_dir)
    builds = {**pointer["builds"], "weeks": weeks, "weeks_ok": weeks is not None and weeks >= MIN_REFINE_WEEKS}
    bars = {x["id"]: x.get("enough") or {} for x in (S.get("signals") or {}).get("signals") or [] if x.get("id")}
    pointer_things = level_things.pointer(run_dir, board, setup_read(run_dir), builds, bars)
    pointer = setup_read_gate(refined_over_time(pointer, run_dir), run_dir)
    in_use = pointer_things if level_things.model() == "things" else pointer
    result = {"substrate": S["version"], "evidence": ev, "disciplines": disciplines, "pointer": pointer,
              "pointer_things": pointer_things, "level_model": level_things.model(),
              "calibration": {**calibration(in_use, call_level), "certainty": certainty(run_dir, ev, S)},
              "recommendations": recs, "more_recommendations": more, "content_gaps": gaps, "detectors": trail}
    result["verdicts"] = verdicts(result, S)
    # the capability board (board.py): its L4 build rows (BOARD_BUILD_AREAS) also count toward "built in 3 areas"
    result["board"] = board
    result["calibration"]["built_kinds"] = len(board.get("built_kinds") or [])
    result["calibration"]["built_areas_by_board"] = board_areas(board)
    return result


if __name__ == "__main__":
    # --call-level L2: the member's level from their evaluation call (v13.9), for the calibration verdict; or
    # AGENTIC_SETUP_CALL_LEVEL. A member's own run has none: the page then shows only what the laptop backs.
    args = list(sys.argv[1:])
    call = os.environ.get("AGENTIC_SETUP_CALL_LEVEL")
    if "--call-level" in args:
        i = args.index("--call-level")
        call = args[i + 1]
        del args[i:i + 2]
    out = run(args[0], call)
    s = json.dumps(out, indent=1, ensure_ascii=False)
    if len(args) > 1:
        open(args[1], "w").write(s)
    else:
        print(s)
