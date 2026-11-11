"""What a member may send Proxify: the share summary, built from a run folder. One function for both ways it leaves the
laptop: `run.py share` writes it as share-with-proxify.json, and the member page's "Share with Proxify" tab embeds it
and sends it only when the person presses Send (views/view_share.py).

Every value is a practice, discipline or capability id from the substrate, a level, a state word or a small number:
never a file name, a path, a sentence or a count of their work, so no client or personal detail can leave the machine.
Proxify sets it beside the person's evaluation call (engine calibration: above / confirms / stands).
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SUBSTRATE = os.path.join(HERE, "substrate.json")

BOARD_IDS = {"cap-skills-own", "cap-skills-installed", "cap-instructions", "cap-hooks", "cap-mcp", "cap-subagents", "cap-teams",
             "cap-workflows", "cap-parallel", "cap-memory", "cap-model-routing", "cap-permissions", "cap-event-runs", "cap-evals",
             "cap-own-rig", "cap-plugins-own", "cap-sdk", "cap-mcp-write", "cap-output-styles", "cap-telemetry"}
LEVELS = {"L1", "L2", "L3", "L4", "can't tell", None}
STATES = {"seen", "looked, not found", "not read"}
WHAT = "agentic-setup share: practice ids and levels only (no file names, no text)"


class Unsafe(ValueError):
    """The summary would hold something that looks like a path, an address or markup: nothing may be sent."""


def payload(run, today=None):
    res = json.load(open(os.path.join(run, "v15-result.json")))
    S = json.load(open(SUBSTRATE))
    pids, dids = set(S["practices"]), {d["id"] for d in S["disciplines"]}
    p = res.get("pointer") or {}

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
        "made": today or time.strftime("%Y-%m-%d"),
        "laptop_level": p.get("level") if p.get("level") in LEVELS else None,
        "could_be": p.get("could_be") if p.get("could_be") in LEVELS else None,
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
