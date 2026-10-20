"""limits — what a view may claim from what the scanner could see (PLAN.md fixes F1–F4).

One place for the rules every view shares, so a view never has to remember them:

  F1  logs missing or stale     → every view says "can't tell", never 0
  F2  minimum N                 → below the floor a view's lead says "can't tell — too few moments (N)"
  F3  coverage line             → what was read, set aside and why, as a field every view carries
  F4  Claude Code versions      → the version range, and a behaviour switch inside the window

Pure functions over the facts scan.habits() collects (a dict of counts and names, no text and no paths
from the person's work), so a renderer or a later card can reuse them.
"""
from __future__ import annotations

import re
from collections import Counter

# ── F2: minimum N, with the reason ───────────────────────────────────────────────────────────────
# A share quoted from few moments moves a whole step with every moment. At 20 moments the 95% margin on a
# 50% share is about ±22 points: coarse, but enough to tell "most" from "few". At 10 sessions it is ±31:
# the least that separates "a habit" from "one busy afternoon". Tune on member runs (CLAUDE.md rule 4).
MIN_N = {"moments": 20, "sessions": 10}


def too_few(n: int, unit: str = "moments") -> bool:
    return n < MIN_N[unit]


def cant_tell(n: int, noun: str, unit: str = "moments") -> str:
    return f"Can't tell — too few moments ({n} {noun}); at least {MIN_N[unit]} needed."


def no_logs_text(logs: dict) -> str | None:
    """F1: the sentence that replaces every claim when the logs hold nothing to read."""
    if logs.get("state") == "none":
        return f"Can't tell — no Claude Code logs found ({logs.get('dir')})."
    if logs.get("state") == "stale":
        return (f"Can't tell — no Claude Code session in the last {logs.get('window_days')} days "
                f"(the newest log is {logs.get('newest_days')} days old).")
    return None


def gate(sec: dict, reqs: list, logs: dict) -> bool:
    """Replace a view's claims with "can't tell" when the logs are missing or an N is below its floor.

    reqs: [(n, noun, unit, zero_ok)]. zero_ok: a count of 0 is a finding (the logs are there and the thing
    never happened), not a gap. Returns True when the view was gated."""
    why = no_logs_text(logs)
    if why:
        for k in ("points", "numbers", "table", "ladder", "examples"):
            sec.pop(k, None)
    else:
        for n, noun, unit, zero_ok in reqs:
            if (n or not zero_ok) and too_few(n, unit):
                why = cant_tell(n, noun, unit)
                break
        if not why:
            return False
        sec["numbers"] = [{"value": "can't tell", "label": x.get("label", "")} for x in sec.get("numbers") or []]
        # a point that quotes a percentage is a rate from this N; one that quotes "3 of 5" shows its own N
        sec["points"] = [p for p in sec.get("points") or [] if "%" not in (p["text"] if isinstance(p, dict) else p)]
    sec["lead"] = why
    sec["can_tell"] = False
    if isinstance(sec.get("how"), dict):
        sec["how"] = {**sec["how"], "caveat": why + " " + sec["how"].get("caveat", "")}
    return True


# ── F4: Claude Code versions ─────────────────────────────────────────────────────────────────────
# Signals a view relies on and Claude Code only started (or stopped) writing at some version. Found from
# the data, never from a list of versions: a switch is a version from which most sessions carry the signal
# and before which almost none did (a stray session around the upgrade is tolerated). Versions with fewer
# than 3 eligible sessions are too thin to place the switch, but count in the totals.
# signal -> (what the record says, the key of the versions where the signal could have shown)
SIGNALS = {
    "promptSource": ("marks who typed each message (a person, a program or the system)", "eligible"),
    "entrypoint": ("records which app started the session", "eligible"),
    "instructions": ("lists the instruction files it loaded at the start of a session", "eligible"),
    "home_file": ("lists the instruction file in the home folder, such as ~/CLAUDE.md", "home_eligible"),
}


def vkey(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", v or "0"))


def detect_switches(per_session: list, min_side: int = 5, share: float = 0.6, stray: float = 0.05) -> list:
    """per_session: one {signal: {versions it was seen in}} per session; each signal's eligible key holds the
    versions the session was in a position to show it (a typed-message record; a start folder under home)."""
    out = []
    for sig, (what, key) in SIGNALS.items():
        elig, seen = Counter(), Counter()
        for sv in per_session:
            for v in sv.get(key, ()):
                elig[v] += 1
                seen[v] += v in sv.get(sig, ())
        versions = sorted(elig, key=vkey)
        strong = [v for v in versions if elig[v] >= 3 and seen[v] / elig[v] >= 0.5]
        if not strong or len(versions) < 2:
            continue

        def side(vs):
            return sum(seen[v] for v in vs), sum(elig[v] for v in vs)

        def found(kind, at, before, after):
            (p0, e0), (p1, e1) = side(before), side(after)
            return {"signal": sig, "what": what, "kind": kind, "at": at,
                    "before": {"with": p0, "of": e0}, "after": {"with": p1, "of": e1}}

        first, last = strong[0], strong[-1]
        old = [v for v in versions if vkey(v) < vkey(first)]
        new = [v for v in versions if vkey(v) >= vkey(first)]
        (p0, e0), (p1, e1) = side(old), side(new)
        if old and e0 >= min_side and e1 >= min_side and p0 / e0 <= stray and p1 / e1 >= share:
            out.append(found("appears", first, old, new))
            continue
        gone = [v for v in versions if vkey(v) > vkey(last)]
        kept = [v for v in versions if vkey(v) <= vkey(last)]
        (p0, e0), (p1, e1) = side(kept), side(gone)
        if gone and e0 >= min_side and e1 >= min_side and p1 / e1 <= stray and p0 / e0 >= share:
            out.append(found("disappears", gone[0], kept, gone))
    return out


def switch_text(sw: dict) -> str:
    b, a = sw["before"], sw["after"]
    verb = "starts" if sw["kind"] == "appears" else "stops"
    return (f"From Claude Code {sw['at']} it {verb} writing a record that {sw['what']} "
            f"({b['with']} of {b['of']} sessions before, {a['with']} of {a['of']} from then on). " + sw.get("note", "")).strip()


def version_text(v: dict) -> str:
    if not v or not v.get("first"):
        return "Claude Code version not recorded."
    span = v["first"] if v["first"] == v["last"] else f"{v['first']}–{v['last']}"
    return f"Claude Code {span}."


# ── F3: coverage line ────────────────────────────────────────────────────────────────────────────
# The common part of a coverage line (sessions read, set aside and why) comes from the facts; a view adds
# what only it knows through `extra` (git commits matched, folders checked), and SOURCE_NOTE what its
# method cannot see.
SOURCE_NOTE = {
    "helpers": "Helper transcripts are read for the files they open only; helpers started by helpers are not counted.",
    "memory": "Notes read or written by other tools, or by hand, leave no record here; shell lookups are matched by tool name.",
    "wrong": "Messages are checked against an English word list: a correction in another language, or a polite one, is not caught.",
    "context": "Instruction files and settings are read from disk as they are today, not as they were in each session.",
    "built": "What exists is read from disk today; use is counted from the logs.",
    "ladder": "Setup is read from disk today; use is counted from the logs.",
    "check": "Tests run by CI, by a hook or in another session leave no record in a session.",
    "unattended": "Only runs that left a log on this machine.",
}


def n_(x) -> str:
    return f"{x:,}"


def coverage(view: str, F: dict, extra: dict | None = None) -> dict:
    """{"line": one sentence for the reader, "read": [...], "skipped": [...]} for one view."""
    extra = extra or {}
    logs, S, P = F["logs"], F["sessions"], F["prompts"]
    read, skipped = [], []
    if logs["state"] != "ok":
        why = (f"no session logs in {logs['dir']}" if logs["state"] == "none" else
               f"the newest log in {logs['dir']} is {logs['newest_days']} days old, outside the {logs['window_days']}-day window")
        return {"line": f"Nothing read: {why}. {version_text(F['versions'])}", "read": [], "skipped": [why]}
    elif view == "unattended":
        read.append(f"{n_(S['automated'])} automated runs (Agent SDK, claude -p) among {n_(S['in_window'])} sessions in the last {logs['window_days']} days")
        skipped.append(f"{n_(S['typed'])} sessions a person typed in: other views")
    else:
        read.append(f"{n_(S['typed'])} sessions a person typed in, of {n_(S['in_window'])} in the last {logs['window_days']} days")
        if view in ("sessions", "start", "handover", "wrong", "memory", "where", "helpers", "crosstalk", "builtins"):
            q = P["queued"]
            read.append(f"{n_(P['counted'])} typed messages" + (f", {n_(q)} of them typed while the agent was busy" if q else ""))
        if S["automated"]:
            skipped.append(f"{n_(S['automated'])} automated runs (no person typing)")
        if S["temp"]:
            skipped.append(f"{n_(S['temp'])} throwaway sessions in temp folders")
        if S["no_prompt"]:
            skipped.append(f"{n_(S['no_prompt'])} sessions with no typed message")
        if P["copied"]:
            skipped.append(f"{n_(P['copied'])} messages copied into resumed or forked chats, counted once")
    read += extra.get("read", [])
    skipped += extra.get("skipped", [])
    parts = ["Read " + "; ".join(read) + "." if read else "", ("Set aside: " + "; ".join(skipped) + ".") if skipped else "",
             SOURCE_NOTE.get(view, ""), version_text(F["versions"]), history_text(logs)]
    return {"line": " ".join(p for p in parts if p), "read": read, "skipped": skipped}


def history_text(logs: dict) -> str:
    if logs.get("oldest_days") is None:
        return ""
    keep = logs.get("cleanup_days")
    note = (f"Claude Code keeps {keep} days of chats here." if keep else
            "Claude Code deletes chats after 30 days unless cleanupPeriodDays is raised.")
    return f"Logs reach back {logs['oldest_days']} days. {note}"
