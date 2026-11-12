"""What a member may send Proxify: the share summary, built from a run folder. One function for both ways it leaves the
laptop: `run.py share` writes it as share-with-proxify.json, and the member page's "Share with Proxify" tab embeds it
and sends it only when the person presses Send (views/view_share.py).

Every value is a practice, discipline, capability or thing id, a level, a state word, a yes/no or a small number:
never a file name, a path, a sentence or a count of their work, so no client or personal detail can leave the machine.
Proxify sets it beside the person's evaluation call (engine calibration: above / confirms / stands).

Since 11-11 it also carries the read the level now rests on (NEW_KEYS): which read is in use, each thing's state, and
the setup read's verdict per part (did the code prove the structure, what the agent judged, did its quote check out)
with the ids of the holds that fired and of the cards counted from Claude Code's records. The practice rungs stay
under `practices`, which tools/calibrate.py reads.

The deployed receiver refuses a summary with any top-level key it does not know (its check(): "unknown field <key>";
an Apps Script whose source is kept outside this repo). So Send carries wire(): only RECEIVER_KEYS until
RECEIVER_TAKES_NEW_KEYS is switched on, which is right only after the receiver's KEYS list holds NEW_KEYS. The file
`run.py share` writes for sending by hand holds everything: no receiver checks it.
"""
import json
import os
import re
import time

import sys

HERE = os.path.dirname(os.path.abspath(__file__))
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("clock", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "setup-snapshot", "experiments", "2026-11-02-evidence", "clock.py"))
clock = importlib.util.module_from_spec(_spec)   # the one place the calendar clock is read
_spec.loader.exec_module(clock)
sys.path.insert(0, HERE)
import things as level_things  # noqa: E402  which level read is in use: from things (default) or from practices
SUBSTRATE = os.path.join(HERE, "substrate.json")

BOARD_IDS = {"cap-skills-own", "cap-skills-installed", "cap-instructions", "cap-hooks", "cap-mcp", "cap-subagents", "cap-teams",
             "cap-workflows", "cap-parallel", "cap-memory", "cap-model-routing", "cap-permissions", "cap-event-runs", "cap-evals",
             "cap-own-rig", "cap-plugins-own", "cap-sdk", "cap-mcp-write", "cap-output-styles", "cap-telemetry"}
RECEIVER_KEYS = ("what", "substrate", "made", "laptop_level", "could_be", "built_areas", "evals_shown", "call_questions",
                 "built_kinds", "uses_kinds", "practices", "logs", "certainty")
NEW_KEYS = ("level_model", "things", "setup_read")
RECEIVER_TAKES_NEW_KEYS = False  # switch on only after the receiver's KEYS list holds NEW_KEYS: until then a Send with them is refused
ID = re.compile(r"^[a-z0-9-]{3,60}$")
THING_STATES = {level_things.YOURS_USED, level_things.YOURS_IDLE, level_things.USES_OTHERS, level_things.CLAUDE_DID,
                level_things.NOT_SEEN, level_things.CANT_CHECK}
JUDGED = {"shown", "partly", "not shown", "can't tell"}
LEVELS = {"L1", "L2", "L3", "L4", "can't tell", None}
STATES = {"seen", "looked, not found", "not read"}
WHAT = "agentic-setup share: practice ids and levels only (no file names, no text)"


class Unsafe(ValueError):
    """The summary would hold something that looks like a path, an address or markup: nothing may be sent."""


def wire(sh, takes_new=None):
    """What Send carries: the whole summary once the receiver takes NEW_KEYS, else only the keys it knows."""
    takes_new = RECEIVER_TAKES_NEW_KEYS if takes_new is None else takes_new
    return dict(sh) if takes_new else {k: v for k, v in sh.items() if k in RECEIVER_KEYS}


def _things(res):
    """thing id -> its home level and state word (things.py). Never the name, the proof or a count."""
    out = {}
    for t in (res.get("pointer_things") or {}).get("things") or []:
        if isinstance(t, dict) and isinstance(t.get("id"), str) and ID.match(t["id"]) and t.get("state") in THING_STATES:
            out[t["id"]] = {"level": t.get("level") if t.get("level") in LEVELS else None, "state": t["state"]}
    return out


def _setup_read(run):
    """The setup read as verdicts only: per part whether the code proved the structure, what the agent judged and whether
    its quote was found in the file (never the quote, the why or the next step); the ids of fired holds and of cards."""
    try:
        sr = json.load(open(os.path.join(run, "setup-read.json")))
    except (OSError, ValueError):
        return {"ran": False, "parts": [], "holds": [], "cards": []}
    ok = lambda x: isinstance(x, str) and bool(ID.match(x))  # noqa: E731
    return {"ran": sr.get("ran", True) is not False,
            "parts": [{"id": g["id"], "structure": bool(g.get("structure")),
                       "judged": g.get("judged") if g.get("judged") in JUDGED else "can't tell",
                       "quote_ok": bool(g.get("quote_ok"))}
                      for g in sr.get("parts") or [] if isinstance(g, dict) and ok(g.get("id"))],
            "holds": [h["id"] for h in sr.get("holds") or [] if isinstance(h, dict) and h.get("fired") and ok(h.get("id"))],
            "cards": [c["id"] for c in sr.get("cards") or [] if isinstance(c, dict) and ok(c.get("id"))]}


def payload(run, today=None):
    res = json.load(open(os.path.join(run, "v15-result.json")))
    S = json.load(open(SUBSTRATE))
    pids, dids = set(S["practices"]), {d["id"] for d in S["disciplines"]}
    p = res.get("pointer") or {}  # the practice read: built areas, evals and the call's questions come from its trail
    lv, level_model = level_things.active(res)

    def ids(xs, ok):
        return [x for x in xs if x in ok]

    l4 = next((t for t in p.get("trail") or [] if t.get("level") == "L4"), {})
    built = next((c for c in l4.get("conditions") or [] if str(c.get("test", "")).startswith("things they built")), {})
    evals = next((c for c in l4.get("conditions") or [] if str(c.get("test", "")).startswith("re-tests what they built")), {})
    practices = {}
    for d in res.get("disciplines") or []:
        for pr in d.get("practices") or []:
            if pr.get("id") in pids:
                practices[pr["id"]] = {"rung": pr.get("rung") if pr.get("rung") in LEVELS else None,
                                       "state": pr.get("state") if pr.get("state") in STATES else "not read"}
    logs = {}
    try:
        meta = (json.load(open(os.path.join(run, "portable-checks.json"))).get("meta") or {}).get("logs") or {}
        logs = {k: meta[k] for k in ("window_days", "cleanup_period_days", "days_with_sessions") if isinstance(meta.get(k), (int, float))}
    except (OSError, ValueError):
        pass
    out = {
        "what": WHAT,
        "substrate": res.get("substrate"),
        "made": today or time.strftime("%Y-%m-%d", time.localtime(clock.now())),
        # the level follows the read in use (things.py): a word, never the reasons or the proofs behind it
        "laptop_level": lv.get("level") if lv.get("level") in LEVELS else None,
        "could_be": lv.get("could_be") if lv.get("could_be") in LEVELS else None,
        "level_model": level_model,
        "things": _things(res),
        "setup_read": _setup_read(run),
        "built_areas": ids(built.get("by") or [], dids),
        "evals_shown": bool(evals.get("met")),
        "call_questions": [ids(x, pids) for x in p.get("call_questions") or []],
        # the capability board (v13's 20 rows, v15/engine/board.py): which kinds of thing they built, which they only use
        "built_kinds": ids((res.get("board") or {}).get("built_kinds") or [], BOARD_IDS),
        "uses_kinds": ids((res.get("board") or {}).get("uses_kinds") or [], BOARD_IDS),
        "practices": practices,
        "logs": logs,
        # how far to trust it: a level word and three small numbers (engine.certainty), nothing identifying
        "certainty": {k: ((res.get("calibration") or {}).get("certainty") or {}).get(k)
                      for k in ("level", "days_with_sessions", "backed_practices", "areas")},
    }
    # last line of defence: nothing that looks like a path, an address or markup
    rest = json.dumps({k: v for k, v in out.items() if k != "what"})
    for bad in ("/", "\\", "@", "<", ">"):
        if bad in rest:
            raise Unsafe(f"the summary would hold '{bad}'")
    return out
