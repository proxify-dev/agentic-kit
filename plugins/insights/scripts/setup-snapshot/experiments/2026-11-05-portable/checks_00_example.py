"""The worked example every other checks file copies: one rewritten check and its stranger-laptop case."""
from base import check
from fixture import fixture


@check("write-down-what-it-cannot-know", "L2", 0)
def corrected_the_agent(lt):
    """Person corrected the agent: an Esc interrupt or a refused step (Claude Code's toolDenialKind, whatever the
    language), right before a message they typed. Counted per typed message, in every interactive entrypoint."""
    marks = [(o["s"], o["t"]) for o in lt.others if o["type"] == "interrupt" and lt.interactive(o["s"])]
    marks += [(c["s"], c["t"]) for c in lt.calls if c.get("denial") == "user-rejected" and not c["sc"] and lt.interactive(c["s"])]
    # the first message typed after each mark, in the same session (not every message within some minutes)
    by_s = {}
    for m in lt.typed:
        if lt.interactive(m["s"]):
            by_s.setdefault(m["s"], []).append(m)
    hit = {}
    for s, t in marks:
        nxt = next((m for m in by_s.get(s, []) if m["t"] >= t), None)
        if nxt:
            hit[nxt["u"] or id(nxt)] = nxt
    n, ev = len(hit), [m["text"][:80] for m in list(hit.values())[:3]]
    return {"value": n, "evidence": ev, "note": "an Esc can also mean 'stop', not only 'that's wrong'"}


@fixture("write-down-what-it-cannot-know", "L2", 0)
def _(fx):
    s = fx.session("~/work/shop", entrypoint="claude-desktop")       # desktop: no history.jsonl
    s.typed("add a login page")
    s.refuse("Edit", {"file_path": fx.p("~/work/shop/a.ts")})
    s.typed("no — use the existing form component")                  # counts: right after a refusal
    s.interrupt()
    s.typed("stop, keep the old route")                               # counts: right after an Esc
    s.typed("great, now the tests")                                   # trap: no Esc or refusal before it
    h = fx.session("~/work/shop", entrypoint="sdk-cli")               # trap: a headless run is not the person
    h.refuse("Bash", {"command": "rm -rf build"}); h.typed("go on")
    return {"value": 2}
