"""What the three no-grade practice views share (his 11-03: "Runs without you makes no sense… what's that badge even
for?" — the step word is the level in disguise). None of these views shows a step word. They say what was found and
where, from the same detector rows the level read uses.

- practices(ctx): every practice with its area, name, finding, evidence rows and the layers it lives in.
- LAYERS: where a practice can live, in the order a habit usually moves: typed in chats -> a file set up -> something
  built -> a test that proves it. A practice can live in several at once; that is the point of showing layers.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import receipt_kit as K  # noqa: E402

LAYERS = [
    ("chats", "In your sessions", "You do it as you type: we saw it in your session logs or runs."),
    ("file", "In a file you set up", "A rule file, a memory store, a setting or a workflow carries it."),
    ("built", "In something you built", "A hook, program, agent, plugin or tool of yours does it."),
    ("tested", "Checked by a test", "An eval or test of yours shows it holds."),
]
_LAYER_OF = [  # evidence source -> layer, first match wins
    (r"^(eval|test)\b", "tested"),
    (r"^(hook|runner|agent|plugin|tool)\b|^hook_scripts$|^git hooks$|^Programs that start agents$|^refusals by hooks$"
     r"|^schedule logs$|^Their own skills$", "built"),
    (r"^(Instruction files|instruction files|instruction_files|memory stores|settings that limit the agent|workflows)$",
     "file"),
    (r"^(What they do by hand|[Hh]eadless runs|(?:chats|sessions)\.jsonl.*|probes\..*)$", "chats"),
]
BUILT_KINDS = re.compile(r"^(hook|runner|agent|plugin|tool|eval|test):|^hook_scripts$|^git hooks$")


def layer_of(source):
    for pat, layer in _LAYER_OF:
        if re.search(pat, source or ""):
            return layer
    return "chats"


def headline(why):
    """The finding's first statement: up to the first sentence end or ', and ' — short enough to be a title."""
    s = (why or "").strip()
    if not s:
        return ""
    first = re.split(r"(?<=[.;])\s+", s, maxsplit=1)[0]
    if len(first) > 140 and ", and " in first:
        first = first.split(", and ", 1)[0]
    return first.rstrip(".;")


def practices(ctx):
    res = ctx.get("v15_result") or {}
    det = res.get("detectors", {})
    clean = ctx["clean"]
    out = []
    for D in res.get("disciplines", []):
        for p in D["practices"]:
            d = det.get(f'{D["id"]}/{p["id"]}', {})
            ev = [{"source": e.get("source", ""), "label": K.source_label(e.get("source")),
                   "detail": K.receipt(e.get("detail")), "layer": layer_of(e.get("source"))}
                  for e in d.get("evidence", [])]
            for e in ev:  # a chat-log sign reads as where it is, not as the detector's section name
                if e["layer"] == "chats" and not e["source"].startswith("probes."):
                    e["label"] = "Your sessions" if "headless" not in e["source"].lower() else "Headless runs"
            seen = (d.get("state") or "not read") == "seen"
            why = clean(d.get("why")) or K.plain(d.get("why"))
            out.append({"area_id": D["id"], "area": D["name"], "id": p["id"], "name": clean(p["name"]),
                        "why": why, "headline": headline(why), "evidence": ev,
                        "layers": {e["layer"] for e in ev} if seen else set(), "state": d.get("state") or "not read",
                        "found_by": d.get("found_by"), "derivation": d.get("derivation")})
    return out


def tags(evidence):
    """One tag per place, in first-seen order, with how many lines back it (a source named twice is one tag)."""
    out = {}
    for e in evidence:
        out.setdefault(e["label"], [e["layer"], 0])[1] += 1
    return [(label, layer, n) for label, (layer, n) in out.items()]


def first_number(detail):
    """The first count in an evidence line ('5409/438 typed chats' -> 5409), to rank built pieces by use."""
    m = re.search(r"(\d[\d,]*)", detail or "")
    return int(m.group(1).replace(",", "")) if m else 0
