"""The member page's words, in one place, so every layout variant says exactly the same thing.

Reads the engine's RESULT.json and returns a plain view model already written for the member:
"you" voice, no level, no interviewer notes, no chat ids, no sentence said twice
(the team's brand review, 10-26 and 10-27). A variant only decides the layout.

Usage from a variant:  from common import load;  vm = load(path)
Standard library only.
"""
import json
import re

# who carries the practice. Neutral words, never a pass mark.
RUNG_WORD = {"L4": "Runs without you", "L3": "Your setup does it", "L2": "You do it", "L1": "Tried once"}
RUNG_STEP = {"L4": 4, "L3": 3, "L2": 2, "L1": 1}
# a practice with no rung. "not read" is our gap, not theirs, so it never sounds like a failing.
NO_RUNG_WORD = {"seen": "Started", "looked, not found": "Not yet", "not read": "We couldn't check yet"}
# problem state -> (tone, word). tone drives colour: good / attention / unknown.
PROBLEM = {False: ("good", "Doesn't show"), True: ("attention", "Shows"), None: ("unknown", "We can't tell from your logs")}

_LEVEL = re.compile(r"\bL[1-4]\b|\b[Ll]evel \d\b|anchors? a level|interviewer")
_CHAT_ID = re.compile(r"\s*\(e\.g\. [0-9a-f]{8}: ('[^']*'|\"[^\"]*\")\)")  # "(e.g. 517c7d3c: 'quote')" -> dropped
_BARE_ID = re.compile(r"\b[0-9a-f]{8}\b: ")
# only a sentence that opens on the person ("They named", "They built") turns to "you"; a mid-sentence "they" is
# often a thing (checks, hooks), so it stays
_THEY = [(re.compile(r"(^|(?<=\. ))They (?=[a-z]+ed\b)"), "You "), (re.compile(r"(^|(?<=\. ))Their\b"), "Your")]
# substrate-author asides inside "what you do" are for us, not the member
_ASIDE = re.compile(r"\bthe field\b|\bsubstrate\b|nearest name|\bdiscipline name\b|vocabulary|names this move|"
                    r"\bv1\d\b|playbook's|\bcues?\b|\bmerges\b|\bcrosswalk", re.I)
_SPREAD = "You already do this at its top: use it where this problem still shows. "


def clean(s):
    """Member wording: second person, no chat ids, no sentence that names a level or the interviewer."""
    s = "" if s is None else str(s)
    s = _BARE_ID.sub("", _CHAT_ID.sub("", s))
    for pat, to in _THEY:
        s = pat.sub(to, s)
    return " ".join(x for x in re.split(r"(?<=\.)\s+", s) if x and not _LEVEL.search(x) and not _ASIDE.search(x)).strip()


# "them" only where it can only mean the person: in the substrate it as often means the agents or the files ("passes work
# between them", "two of them collided", "the agent can't reach them"), so a blanket them->you would misread those
_THEM_PERSON = r"(without|costs|cost|tells|pings|teaches|reminds|pushes|brought|wait for|waits for|asks|lets) them\b"
_GENERIC = [(re.compile(r"\bThey\b"), "You"), (re.compile(r"\bthey\b"), "you"), (re.compile(r"\bTheir\b"), "Your"),
            (re.compile(r"\btheir\b"), "your"), (re.compile(r"\bthemselves\b"), "yourself"),
            (re.compile(r"\b" + _THEM_PERSON), r"\1 you")]


def you(s):
    """A practice's description is written about 'a person' throughout, so every they/their there means the reader."""
    s = "" if s is None else str(s)
    keep = []
    for x in re.split(r"(?<=\.)\s+", s):  # the description ends where the authors' notes begin
        if _ASIDE.search(x):
            break
        keep.append(x)
    s = clean(" ".join(keep[:2]))  # two sentences say what the practice is; the rest is detail for us
    for pat, to in _GENERIC:
        s = pat.sub(to, s)
    return s


def _practice(p, ev):
    e = ev.get(p["id"], {})
    rung = e.get("rung") or p.get("rung")
    state = e.get("state") or "not read"
    return {"name": clean(p["name"]), "what_you_do": you(p.get("what_you_do")),
            "step": RUNG_STEP.get(rung, 0), "word": RUNG_WORD.get(rung) or NO_RUNG_WORD.get(state, "Not yet"),
            "found": clean(e.get("why")), "has_rung": bool(rung)}


def _problem(c):
    looked = None if c.get("state") == "not read" else bool(c.get("seen"))
    tone, word = PROBLEM[looked]
    return {"name": clean(c["name"]), "tone": tone, "word": word, "found": clean(c.get("why")),
            "what_goes_wrong": clean(c.get("what_goes_wrong"))}


def _next(r):
    step = r["next_step"]
    already = step.startswith(_SPREAD)
    return {"area": r["discipline"], "problem": clean(r["challenge_name"]), "practice": clean(r["practice_name"]),
            "today": RUNG_WORD.get(r.get("now"), "Not yet"),
            # the engine repeats the evidence inside the step; say it once
            # the evidence IS the advice here (where the problem still shows), so it leads instead of hiding in a fold
            "intro": ("You already do this well. It still shows here: " + clean(r["evidence"])) if already else "",
            "found": "",
            "step": "" if already else clean(step),
            "why_it_matters": clean(r["evidence"]) if not already else "",
            "learn": [{"title": l["title"], "href": "https://agentic.proxify.io" + l["ref"]
                       if l.get("ref", "").startswith("/") else l.get("ref", "")} for l in r.get("learn", [])[:3]]}


def load(path):
    d = json.load(open(path))
    ev = {e["id"]: e for e in d["evidence"] if e["kind"] == "practice"}
    areas = []
    for D in d["disciplines"]:
        prs = sorted((_practice(p, ev) for p in D["practices"]), key=lambda x: -x["step"])
        pbs = [_problem(c) for c in D["challenges"]]
        top = max((p["step"] for p in prs), default=0)
        areas.append({"id": D["id"], "name": D["name"], "question": clean(D["question"]),
                      "step": top,
                      "seen": sum(p["has_rung"] for p in prs), "total": len(prs),
                      "shows": sum(p["tone"] == "attention" for p in pbs), "practices": prs, "problems": pbs})
    allp = [p for a in areas for p in a["practices"]]
    count = {w: sum(p["word"] == w for p in allp) for w in ("Runs without you", "Your setup does it", "You do it")}
    for a in areas:  # how the area's practices split by who carries them: the bar each layout can draw
        a["split"] = {w: sum(p["word"] == w for p in a["practices"]) for w in count} | {
            "Not yet": sum(not p["has_rung"] for p in a["practices"])}
    return {
        "title": "How you work with agents",
        "period": "Last 30 days · read on your own laptop, from your Claude Code logs and setup files",
        # one plain sentence first (his 10-29 ask: a summary is the first thing someone should see)
        "summary": (f"Your setup carries {count['Runs without you'] + count['Your setup does it']} of the "
                    f"{len(allp)} practices we looked for – {count['Runs without you']} of them run without you."),
        "split": count | {"Not yet": sum(not p["has_rung"] for p in allp)},
        "areas": areas,
        "next": [_next(r) for r in d["recommendations"]],
        "more_next": [_next(r) for r in d["more_recommendations"]],
        "how_we_know": ("We read your Claude Code session logs, your setup files (instructions, memory, hooks, skills) "
                        "and your git history from the last 30 days, on your own laptop. Nothing leaves it unless "
                        "you choose to share. Where your logs can't show something, we say so instead of guessing."),
        "rung_legend": [("You do it", "you carry it, session by session"),
                        ("Your setup does it", "a file or tool you set up carries it"),
                        ("Runs without you", "something you built does it and checks it")],
    }


if __name__ == "__main__":
    import sys
    print(json.dumps(load(sys.argv[1]), indent=1, ensure_ascii=False)[:6000])
