#!/usr/bin/env python3
"""Laptop evidence for the v15 engine: the rows the per-discipline readers wrote from the laptop inventory and a second
agent (the skeptic) failed to refute. Built by labs/setup-snapshot/experiments/2026-11-02-evidence/ (inventory.py →
evidence.workflow.js → save.py), which writes RUN_DIR/evidence.json.

Why not the detectors any more: one detector per named problem read one kind of evidence, and the rules that turned
its numbers into rungs carried fixed sentences and unchecked L4s; an independent audit found 10 of 70 claims held
(../../setup-snapshot/memory/problems/v15-evidence-failed-audit.md). The detectors' numbers survive only as probes in
the inventory, which readers may cite.

Each row keeps what the page needs to show how we know: the sources it rests on, what was checked, whether it held,
the question for the call, and what the skeptic did with it. Practices carry a state the page must show apart:
  seen               a rung, with evidence
  looked, not found  the sources that would show it were read and it isn't there
  not read           no source on this laptop covers it (or it was blocked); ask on the call
Evidence from the laptop is "did" (levels.md). Calls produce the same rows from answers ("showed" or "said").

Only facts move a rung on the laptop (levels.md, 11-05; ruling 11-03 "focus only on what you can count"): a row
moves its rung only when it rests on Claude Code's own records or on the person's own files, and an L4 only when it
rests on something they built. Any other row keeps its reading as "could be" and goes to the call.
"""
import json
import os
import re

SHOWS = {"shows": True, "doesn't show": False, "can't tell": None, "": None}
RANK = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}
# artifacts.py's "what they do by hand" holds two kinds of line: counts from Claude Code's records (slash commands,
# plan approvals, compactions…) and typed asks found by fixed English patterns, which end in "N messages in M sessions".
# A word list reads differently on every laptop (setup-snapshot CLAUDE.md: "can't tell"), so it is no fact.
WORD_LIST_LINE = re.compile(r"\d+ messages in \d+ sessions")


def built_ids(run_dir):
    """What the scan found, by the id the AI pass cites (artifacts.py → cheap-artifacts.json): id → its record. Builds
    (hooks, plugins, agents, runners, evals, tools, tests, skills, commands, output styles, workflows, hook scripts) are
    the person's by git; rules files and settings carry `own`; memory stores carry `reads` (sessions that read them)."""
    p = os.path.join(run_dir, "cheap-artifacts.json")
    return {a["id"]: a for a in json.load(open(p))} if os.path.exists(p) else {}


# kinds of cited record and what each is as a fact (11-05: her skills, memory and settings had no id and read as opinion)
FILE_KINDS = ("rules", "settings")   # one of their own files, when `own`
COUNT_KINDS = ("memory",)            # Claude Code's record of sessions reading it, when `reads` > 0


def fact(r, built):
    """What a laptop practice row rests on that reads the same on every laptop:
      built  its basis cites something the scan found they built (their own files, by git)
      count  the code set it from Claude Code's own records, or its basis cites a count from those records (a by-hand
             line, or a memory store sessions read)
      file   its basis cites one of their own files the scan read (rule files, their settings)
      None   only an AI reader's opinion, or a count from a word list"""
    if r.get("found_by") == "code":
        # a portable check at L4 counts something they built (its claim is an L4 "you built it"), checked by the
        # stranger-laptop tests and a skeptic before it may set a level (setup-snapshot experiments/2026-11-05-portable)
        if "portable:" in (r.get("basis") or "") and r.get("rung") == "L4":
            return "built"
        return "count"
    basis = r.get("basis") or ""
    cited = [c.strip() for c in re.findall(r"\[([^\]]+)\]", basis)]
    # the AI pass is told to cite ids in [brackets] but sometimes writes them bare (11-06: one pass wrote every basis
    # without brackets and 22 of its 25 readings were thrown out as opinion): read bare ids and by-hand lines too
    if not cited:
        parts = [x.strip(" .") for x in re.split(r"[;,]|\s\+\s|\band\b", basis) if x.strip(" .")]
        cited = [x for x in parts if isinstance(built, dict) and x in built]
        cited += [x for x in [basis.strip()] if isinstance(built, dict) and x in built and x not in cited]
        m = re.search(r"by hand:\s*(.+)", basis, re.I)
        if m:
            cited.append("by hand: " + m.group(1).strip())
    recs = [built[c] for c in cited if isinstance(built, dict) and c in built]
    if any(x.get("kind") not in FILE_KINDS + COUNT_KINDS and x.get("own", True) for x in recs):
        return "built"
    if any(c.startswith("by hand:") and not WORD_LIST_LINE.search(c) for c in cited) or any(
            x.get("kind") in COUNT_KINDS and (x.get("reads") or 0) > 0 for x in recs):
        return "count"
    if any(x.get("kind") in FILE_KINDS and x.get("own") for x in recs) or any(c.startswith(("~/", "/")) for c in cited):
        return "file"
    return None


def load(run_dir):
    p = os.path.join(run_dir, "evidence.json")
    if not os.path.exists(p):
        return []
    return [r for r in json.load(open(p))["rows"] if r.get("verdict") != "killed"]


FACT_METHODS = ("counts", "files", "git", "settings", "tool calls")


def rows(run_dir):
    ev = []
    built = built_ids(run_dir)
    for r in load(run_dir):
        src = f"{r['discipline']}/{r['id']}"
        common = {"id": r["id"], "strength": "did", "why": r.get("why") or "", "source": src, "state": r["state"],
                  "ask": r.get("ask") or "", "discipline": r["discipline"]}
        if r["kind"] == "practice":
            rung = (r.get("rung") or None) if r["state"] == "seen" else None
            could = r.get("could_be") or None
            f = fact(r, built)
            not_counted = None
            if rung and (f is None or (rung == "L4" and f != "built")):
                # the reading stays on the page as "could be"; the call confirms it (levels.md, 11-05)
                not_counted = ("rests on nothing the laptop can count the same way for everyone" if f is None
                               else "an L4 needs something they built behind it")
                could = rung if not could or RANK[rung] > RANK.get(could, 0) else could
                # a count the code proved (signals.yaml, or a portable check a skeptic let set a level) still stands
                # when the AI read higher on something that isn't a fact: the row keeps the counted rung (11-05: 306
                # /btw sessions did not save a practice whose AI basis was an id the digest no longer had). A count AT the AI's
                # rung stands too: with "<" a /btw count (L2) was dropped whenever the AI also read L2 (11-06 tester, L1)
                code = [e.get("rung") for e in r.get("evidence") or []
                        if isinstance(e, dict) and e.get("signal") and e.get("rung") in RANK and RANK[e["rung"]] <= RANK[rung]]
                rung = max(code, key=RANK.get) if code else None
                if rung:
                    f = "count"
                    not_counted = f"the AI read {could}; counted from Claude Code's records at {rung}"
            # basis: the one thing the rung rests on (a plugin, a file, a command); the level counts it once
            ev.append({"kind": "practice", "rung": rung, "could_be_rung": could, "held": r.get("held") or None,
                       "fact": f, "not_counted": not_counted,
                       "basis": (r.get("basis") or "").strip().lower() or None, "try": r.get("try") or None, **common})
        else:
            seen = SHOWS.get(r.get("shows") or "") if r["state"] != "not read" else None
            # a problem drives advice only when a fact shows it (11-05, his "we don't need shitty advice"): a count, a
            # file, git, settings or the tool calls. An English word match, a hand read or an AI pass's say-so is a
            # question for the call: it stays on the page as "can't tell", never as "seen"
            method = (r.get("check") or {}).get("kind")
            if seen and method not in FACT_METHODS:
                seen = None
            # evidence_keys: where the verdict rests; two problems resting on the same first source get one recommendation
            keys = [str(x.get("source") or "").strip().lower().split(" ")[0] for x in r.get("evidence") or [] if x.get("source")]
            ev.append({"kind": "challenge", "seen": seen, "try": r.get("try") or None, "evidence_keys": keys, **common})
    return ev


def trail(run_dir):
    """What sits under each claim: the sources and numbers, what was checked, did it hold, the skeptic's verdict."""
    out = {}
    for r in load(run_dir):
        out[f"{r['discipline']}/{r['id']}"] = {
            "state": r["state"], "why": r.get("why"), "evidence": r.get("evidence") or [], "checked": r.get("checked") or [],
            "held": r.get("held"), "ask": r.get("ask"), "verdict": r.get("verdict"), "note": r.get("note"),
            "shows": r.get("shows"), "rung": r.get("rung"), "could_be": r.get("could_be"),
            "derivation": r.get("derivation"), "rule": r.get("rule"), "found_by": r.get("found_by"),
            "basis": r.get("basis"), "searched": r.get("searched") or []}
    return out


if __name__ == "__main__":
    import sys
    json.dump(rows(sys.argv[1]), sys.stdout, indent=1, ensure_ascii=False)
