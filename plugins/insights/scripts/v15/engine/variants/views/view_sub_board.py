"""The substrate as a board: where each problem and practice gets stuck, and who fixes it.

His 11-03: "What does 'where the chain breaks' even mean? … too mechanical", then "come with a better way to represent
this data". So no columns of ticks. Each column says, in words, where an item stops on its way from "the substrate
describes it" to "you can open the proof"; rows are the disciplines; each item is a named card in the column where it
stopped. Click a card: its story opens under that row, in five plain sentences.

Stages (an item stands in the column of the last stage it reached in order; "partly" counts as reached, drawn hollow):
  problems:  described -> a laptop can show it -> checked -> answered (shows / doesn't show) -> proof you can open
  practices: described -> a laptop can show it -> read on this laptop -> the audit says the reading holds -> lines kept
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import substrate_kit as S  # noqa: E402
import substrate_pages as P  # noqa: E402

ORDER = 69
TITLE = "Where each item gets stuck"
QUESTION = ("Every problem and practice in the substrate, placed where it stops on the way from 'described' to "
            "'proof you can open'. Click one for its story.")
PROXIFY_ONLY = True

# column -> (what it means, who fixes it)
COLS = {
    "challenge": [
        ("No laptop sign", "The substrate says a laptop can't show it", "substrate"),
        ("Nothing checks it", "A laptop could show it, but the tool has no check", "tool"),
        ("No clear answer", "Checked, but it can't tell", "tool"),
        ("No proof kept", "Answered, but nothing you can open", "data"),
        ("Answered with proof", "You can open the moments it rests on", "done"),
    ],
    "practice": [
        ("No laptop sign", "The substrate says a laptop can't show it", "substrate"),
        ("Not read", "A laptop could show it, but the tool didn't read it", "tool"),
        ("Reading is wrong", "Read, but the audit says the reading is wrong", "tool"),
        ("No lines kept", "The reading holds, but the lines it read are not kept", "data"),
        ("Read, holds, kept", "The reading holds and you can see what it read", "done"),
    ],
}
WHO = {"substrate": "you decide", "tool": "build or fix a check", "data": "keep the proof", "done": "nothing to do"}
FIX = {
    "challenge": ["Decide whether a laptop really can't show this. If it can, write the sign into the substrate.",
                  "Write a check for the sign the substrate already names.",
                  "Give the check a bar it can decide on, or say it needs the call.",
                  "Make the check keep the sessions or files it counted, so you can open them.",
                  "Nothing: open the moments and judge them."],
    "practice": ["Decide whether a laptop really can't show this. If it can, write the sign into the substrate.",
                 "Teach the reader to look for the signs the substrate names.",
                 "Fix the reading the audit says is wrong (the audit note says how).",
                 "Make the reader keep the exact lines it relied on.",
                 "Nothing: check the lines it read."],
}


def _stage(it):
    """-> (column index 0-4, hollow, jumped). jumped = the tool checked it although the substrate says no laptop sign."""
    L = {l["key"]: l["mark"] for l in it["links"]}
    reached = lambda k: L.get(k) in ("ok", "part")  # noqa: E731
    if it["kind"] == "challenge":
        steps = [reached("signs"), reached("check"), it.get("verdict") in ("shows", "doesn't show"), L.get("moments") == "ok"]
        keys = ["signs", "check", None, "moments"]
    else:
        steps = [reached("signs"), reached("read"), L.get("audit") in ("ok", "part"), reached("lines")]
        keys = ["signs", "read", "audit", "lines"]
    col = 0
    for ok in steps:
        if not ok:
            break
        col += 1
    hollow = any(L.get(k) == "part" for k in keys[:col] if k)
    # the tool judged it although the substrate says no laptop can show it: a disagreement only he can settle,
    # so it goes to the first column ("you decide") whatever the tool's answer was
    if it["kind"] == "challenge":
        jumped = L.get("agrees") == "bad" and reached("check")
    else:
        jumped = (not reached("signs")) and reached("read")
    return (0 if jumped else min(col, 4)), hollow, jumped


def _line(it, key):
    l = next((l for l in it["links"] if l["key"] == key), None)
    return l["line"] if l else ""


def _story(B, it, col, RW, NO):
    """The side panel: what to do, what we got in one line, then the element in full (substrate text verbatim,
    this laptop's check and moments, the audit, the playbook pages), never a summary of it."""
    esc, row = S.esc, it.get("row") or {}
    fix = FIX[it["kind"]][col]
    if _stage(it)[2]:
        fix = ("The tool judged this although the substrate says a laptop can't show it. Decide which is right: "
               "write the laptop sign into the substrate, or drop the check.")
    got = (f'{S.verdict_word(it, RW, NO)}. ' + (row.get("why") or (it.get("det") or {}).get("why") or "")).strip()
    body = (P._challenge_body(B, it, chain=False) if it["kind"] == "challenge"
            else P._practice_body(B, it, RW, NO, chain=False))
    return (f'<p class="sp-k">{"Problem" if it["kind"] == "challenge" else "Practice"} · {esc(S.SHORT.get(it["disc"], it["disc"]))}'
            f' · stuck at: {esc(COLS[it["kind"]][col][0])}</p><p class="sp-h">{esc(it["el"]["name"])}</p>'
            f'<p><a class="pl" href="#{"c" if it["kind"] == "challenge" else "p"}/{esc(it["id"])}">See it as {"a problem" if it["kind"] == "challenge" else "a practice"} on your setup</a></p>'
            f'<p class="sp-fix"><b>What to do</b> {esc(fix)}</p><p class="sp-got"><b>What we got</b> {esc(got)}</p>{body}')


def _board(B, kind, RW, NO):
    esc = S.esc
    cols = COLS[kind]
    key = "challenges" if kind == "challenge" else "practices"
    placed = {d: [[] for _ in cols] for d in B["by"]}
    total = [0] * len(cols)
    jumped_all = []
    for d, v in B["by"].items():
        for it in v[key]:
            col, hollow, jumped = _stage(it)
            placed[d][col].append((it, hollow, jumped))
            total[col] += 1
            if jumped:
                jumped_all.append(it)
    n = sum(total)
    # how many get past each step: the funnel line
    past = [n - sum(total[:i]) for i in range(len(cols))] + [total[-1]]
    steps = (["described", "a laptop can show it", "checked", "answered", "with proof"] if kind == "challenge" else
             ["described", "a laptop can show it", "read", "reading holds", "lines kept"])
    funnel = " → ".join(f"<b>{past[i]}</b> {esc(s)}" for i, s in enumerate(steps))
    head = "".join(f'<div class="bh {who}"><b>{esc(t)}</b><span>{esc(m)}</span>'
                   f'<em>{total[i]} · {esc(WHO[who])}</em></div>' for i, (t, m, who) in enumerate(cols))
    lanes = ""
    for d in B["by"]:
        cells = ""
        for i, items in enumerate(placed[d][: len(cols)]):
            cards = "".join(
                f'<button type="button" class="cd{" hol" if h else ""}{" jmp" if j else ""}" data-st="st-{esc(it["id"])}"'
                f' title="{esc(it["el"]["name"])}">{esc(it["el"]["name"])}</button>'
                f'<template id="st-{esc(it["id"])}">{_story(B, it, i, RW, NO)}</template>' for it, h, j in items)
            cells += f'<div class="cell">{cards}</div>'
        lanes += f'<div class="lane"><div class="ln">{esc(S.SHORT.get(d, d))}</div>{cells}</div>'
    jump = ""
    if jumped_all:
        jump = (f'<p class="jumpnote"><b>Orange cards:</b> the tool judged {len(jumped_all)} '
                f'{"items" if len(jumped_all) > 1 else "item"} although the substrate says a laptop can\'t show '
                f'{"them" if len(jumped_all) > 1 else "it"}. One of the two is wrong.</p>')
    return (f'<p class="funnel">{funnel}</p>{jump}<div class="board"><div class="bhead"><div></div>{head}</div>{lanes}</div>')


CSS = S.css_disc("sub_board") + """
.v-sub_board h3{font-size:18px;font-weight:600;margin:40px 0 4px}
.v-sub_board .funnel{color:#475467;margin:0 0 12px}
.v-sub_board .funnel b{color:#15172F;font-weight:600}
.v-sub_board .jumpnote{font-size:12px;color:#475467;margin:0 0 16px;max-width:80ch}.v-sub_board .jumpnote b{color:#B54708;font-weight:600}
.v-sub_board .board{overflow-x:auto}
.v-sub_board .bhead,.v-sub_board .lane{display:grid;grid-template-columns:96px repeat(5,minmax(150px,1fr));gap:8px;min-width:900px}
.v-sub_board .bh{padding:0 0 10px;border-bottom:2px solid #D0D5DD}
.v-sub_board .bh b{display:block;font-size:14px;font-weight:600}
.v-sub_board .bh span{display:block;font-size:12px;color:#475467;line-height:1.4;min-height:34px}
.v-sub_board .bh em{display:block;font-style:normal;font-size:12px;font-weight:500;margin-top:4px;color:#667085}
.v-sub_board .bh.substrate{border-color:#DC6803}.v-sub_board .bh.substrate em{color:#B54708}
.v-sub_board .bh.tool,.v-sub_board .bh.data{border-color:#999AFD}
.v-sub_board .bh.done{border-color:#039855}.v-sub_board .bh.done em{color:#039855}
.v-sub_board .lane{padding:10px 0;border-bottom:1px solid #EAECF0;align-items:start}
.v-sub_board .ln{font-size:12px;font-weight:600;color:#475467;padding-top:4px}
.v-sub_board .cell{display:flex;flex-direction:column;gap:4px;min-width:0}
.v-sub_board .cd{all:unset;box-sizing:border-box;cursor:pointer;display:block;font:400 12px/1.4 Inter,system-ui,sans-serif;
  color:#15172F;background:#FFFFFF;border:1px solid #EAECF0;border-radius:6px;padding:6px 8px;overflow-wrap:anywhere}
.v-sub_board .cd:hover{border-color:#999AFD}
.v-sub_board .cd:focus-visible{outline:2px solid #5258FB;outline-offset:1px}
.v-sub_board .cd.on{border-color:#5258FB;background:#F5F5FF}
.v-sub_board .cd.hol{border-style:dashed}
.v-sub_board .cd.jmp{border-color:#DC6803;background:#FEFBF5}
.kp .sp-k{font-size:12px;color:#667085;margin:0}
.kp .sp-h{font-size:18px;font-weight:600;line-height:1.35;margin:4px 0 12px}
.kp .sp-fix,.kp .sp-got{margin:0 0 8px;color:#15172F}
.kp .sp-fix b,.kp .sp-got b{display:block;font-size:12px;font-weight:500;color:#667085}
.kp .sp-fix{padding:12px 14px;background:#F5F5FF;border-radius:8px}
.v-sub_board .legend{font-size:12px;color:#667085;margin:8px 0 0}
"""

JS = """<script>(function(){var r=document.getElementById('v-sub_board');if(!r)return;
function clear(){r.querySelectorAll('.cd.on').forEach(function(x){x.classList.remove('on')})}
r.addEventListener('click',function(e){
 var b=e.target.closest('.cd[data-st]');if(!b)return;
 if(b.classList.contains('on')){window.kpClose();return}
 clear();b.classList.add('on');window.kpOpenHtml('<div class="v-sub_board">'+document.getElementById(b.dataset.st).innerHTML+'</div>')});
document.addEventListener('kp-close',clear);})();</script>"""


def render(ctx):
    B = S.build(ctx)
    if not B:
        return '<p class="empty">The substrate could not be loaded.</p>'
    try:
        from common import NO_RUNG_WORD as NO
    except ImportError:
        NO = {}
    RW = ctx["RUNG_WORD"]
    return ("<h3>Problems</h3>" + _board(B, "challenge", RW, NO)
            + '<p class="legend">A dashed card: one step was only partly met (a word search, a one-off read, an audit '
              'that says "partly"). Left of the board is earlier on the way.</p>'
            + "<h3>Practices</h3>" + _board(B, "practice", RW, NO) + S.js() + JS)
