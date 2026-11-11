"""The setup as one board: five columns (the disciplines), one card per practice, readable in one look. Shape copied from
the by-area board on the practices pre-selection page; its choosing parts (Keep/Maybe/Drop, clusters) are left out, this
is for reading one person's result.

A card shows, from the run: the practice's short name, the level it reached (or "could be" when the reading is only a
hint), how we know (card_kit.how: built / counted / a file / AI reading only), and whether a count on any laptop can
prove it. Proxify's copy adds what each level list asks of the practice and which cards are still needed for the next level.
The member's copy (ctx["member"]) shows no level and no list marks: it says who carries each practice, in words.
A click opens the shared side panel (panel_kit): the practice, the steps with your level marked, the problems it handles.

Sources, nothing here is a list of its own:
- rung, state, could_be: v15-result.json disciplines[].practices[]; how we know: v15-result.json evidence[] (fact, not_counted)
- short name and laptop_counts: the substrate (labs/v15/substrate/*.yaml, read through engine/substrate.json)
- level lists: engine.LEVEL_LISTS (read only); what's missing for the next level: v15-result.json pointer.missing_for_next
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card_kit as C  # noqa: E402  the card, and how we know / laptop badges
import panel_kit as PK  # noqa: E402  the portable checks of each practice
import lvl_tip  # noqa: E402  one tooltip for every level badge

try:  # the level lists live in the engine; a page built without it just leaves the list marks out
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    import engine as _engine  # noqa: E402
    LEVEL_LISTS = _engine.LEVEL_LISTS
    HAND, SETUP = _engine.HAND, _engine.SETUP
except Exception:  # noqa: BLE001
    LEVEL_LISTS, HAND, SETUP = {}, 2, 3

ORDER = 3
TITLE = "Your setup on one board"
QUESTION = "Every practice, in one look: the level you reached, how we know, and what a check on any laptop can prove."
QUESTION_MEMBER = "Every practice, in one look: who carries it, how we know, and what a check on any laptop can prove."
PROXIFY_ONLY = True  # a tester 11-06: "looks like a call agenda for you, not for member. If I had a call scheduled, I would try to \"prepare\""

RUNGS = ["L4", "L3", "L2"]  # the levels the lists ask for
LEVELS = ["L4", "L3", "L2", "L1"]  # every level a practice can sit at: counted the same way as By discipline
RANK = {"L4": 4, "L3": 3, "L2": 2, "L1": 1}  # a card sorts by this; L1 (tried once) is on no list but can be a practice's rung
LIST_WHO = {HAND: "you", SETUP: "your setup"}

CSS = """
.v-board .sum{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px;margin:0 0 16px;font-size:14px;color:#344054}
.v-board .sum b.lv{font-size:28px;font-weight:600;line-height:1;color:#15172F;letter-spacing:-.01em}
.v-board .sum .need{flex:1 1 320px;min-width:0}
.v-board .sum .need b{font-weight:600;color:#B54708}
.v-board .sum .ok{color:#027A48;font-weight:500}
.v-board .sum .mix{flex:1 0 100%;font-size:12px;color:#667085}
.v-board .bar{display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;margin:0 0 8px}
.v-board .bar .gl{font-size:12px;color:#667085;min-width:132px}
.v-board .kc-chk{font-size:11px;white-space:nowrap;letter-spacing:-.005em}
.v-board .chip{font:inherit;font-size:12px;font-weight:500;line-height:1;padding:7px 10px;border-radius:999px;border:1px solid #D0D5DD;
  background:#FFFFFF;color:#344054;cursor:pointer}
.v-board .chip b{font-weight:600;margin-left:6px;color:#667085}
.v-board .chip:hover{border-color:#5258FB;color:#15172F}
.v-board .chip[aria-pressed=true]{background:#15172F;border-color:#15172F;color:#FFFFFF}
.v-board .chip[aria-pressed=true] b{color:#D0D5DD}
"""

JS = """<script>(function(){var r=document.getElementById('v-board');if(!r)return;
var cards=[].slice.call(r.querySelectorAll('.kc')),chips=[].slice.call(r.querySelectorAll('.chip')),f={lap:'all',lv:'all'};
function show(){cards.forEach(function(c){
  c.hidden=!((f.lap==='all'||c.dataset.lap===f.lap)&&(f.lv==='all'||(f.lv==='need'?c.dataset.need==='1':c.dataset.lv===f.lv)))})}
chips.forEach(function(c){c.addEventListener('click',function(){var g=c.dataset.g;f[g]=c.dataset.f;
 chips.forEach(function(x){if(x.dataset.g===g)x.setAttribute('aria-pressed',x===c)});show()})})})();</script>"""


def _lists(pid):
    """{"L3": "you", "L4": "your setup"}: what each level list asks of this practice. Empty when it is on no list."""
    out = {}
    for lv in RUNGS[::-1]:
        for ids, need in LEVEL_LISTS.get(lv, []):
            if pid in ids:
                out[lv] = LIST_WHO[need]
    return out


def _lists_text(lists):
    """{"L2": "you", "L3": "you", "L4": "your setup"} -> "L2-L3 lists: you · L4 list: your setup" (neighbouring levels that ask the same are one mark)."""
    runs = []
    for lv, who in lists.items():
        if runs and runs[-1][2] == who:
            runs[-1][1] = lv
        else:
            runs.append([lv, lv, who])
    return " · ".join(f"{a}{'–' + b if a != b else ''} list{'s' if a != b else ''}: {who}" for a, b, who in runs)


def render(ctx):
    esc, clean = ctx["esc"], ctx["clean"]
    member = bool(ctx.get("member"))
    RW = ctx["RUNG_WORD"]
    res = ctx.get("v15_result") or {}
    if not res.get("disciplines"):
        return '<p class="empty">Not in this run: no v15-result.json.</p>'
    from common import NO_RUNG_WORD  # noqa: E402  the member wording
    sub = lvl_tip._practices()
    ev = {e["id"]: e for e in res.get("evidence") or [] if e.get("kind") == "practice" and e.get("id")}
    checks = PK.portable_by_pid(ctx)
    pointer = res.get("pointer") or {}
    missing = (pointer.get("missing_for_next") or {}) if not member else {}
    next_lv = missing.get("level")
    groups = [[i for i in g] for g in missing.get("practices") or []]
    needed = {i: g for g in groups for i in g}
    short = lambda pid: (sub.get(pid) or {}).get("short_name") or pid  # noqa: E731

    cols, counts, total = [], {"all": 0, "now": 0, "fix": 0, "call": 0}, 0
    by_lv = {**{r: 0 for r in LEVELS}, "none": 0}
    ai_only = 0
    for d in res["disciplines"]:
        cards = []
        d_name = d.get("name") or d.get("id") or ""
        dist = {r: 0 for r in LEVELS} | {"none": 0}
        for p in d.get("practices") or []:
            pid = p.get("id") or ""
            s = sub.get(pid) or {}
            rung, could = p.get("rung"), p.get("could_be_rung") if p.get("could_be") else None
            e = ev.get(pid) or {}
            ai = C.how(e.get("fact"), e.get("not_counted"))[0] == C.HOW_NONE[0]
            lap_key = C.laptop(s.get("laptop_counts"))[0]
            counts["all"] += 1
            counts[lap_key] += 1
            by_lv[rung if rung in by_lv else "none"] += 1
            dist[rung if rung in dist else "none"] += 1
            ai_only += ai
            lists_txt = "" if member else _lists_text(_lists(pid))
            is_need = pid in needed
            badge = C.level(rung, RW, member, lvl_tip.attrs(pid, rung or could), could=could,
                            no_rung_word=NO_RUNG_WORD.get(p.get("state") or "", "Not yet"))
            tag = C.laptop_pill(s.get("laptop_counts"), short=True)
            cs = checks.get(pid) or []
            lines = (C.how_line(e.get("fact"), e.get("not_counted"))
                     + ("" if rung else C.log_line())
                     + (f'<div class="kc-line kc-chk">{len(cs)} check{"s" if len(cs) != 1 else ""} · {sum(c["approved"] for c in cs)} approved · {sum(c["run"] == "counted" for c in cs)} counted</div>' if cs else "")
                     + (f'<div class="kc-line">{esc(lists_txt)}</div>' if lists_txt else "")
                     + (f'<span class="kc-flag">Needed for {esc(next_lv)}</span>' if is_need else ""))
            cards.append((10 if is_need else 0, RANK.get(rung or "", 0), len(cards), C.card(
                s.get("short_name") or clean(p.get("name")) or pid, badge + tag, lines, pk=f"p:{pid}", cls="need" if is_need else "",
                attrs=f' data-lap="{lap_key}" data-lv="{esc(rung or "none")}" data-need="{int(is_need)}"')))
        cards.sort(key=lambda c: (-c[0], -c[1], c[2]))
        n = len(d.get("practices") or [])
        total += n
        words = " · ".join([f"{dist[r]} {RW.get(r or '', r).lower() if member else r}" for r in LEVELS if dist[r]]
                           + ([f"{dist['none']} not yet"] if dist["none"] else []))
        bar = "".join(f'<i class="{C.rung_class(r)}" style="flex:{dist[r]}"></i>' for r in LEVELS if dist[r])
        bar += f'<i class="r0" style="flex:{dist["none"]}"></i>' if dist["none"] else ""
        cols.append(C.group(d_name, "", "".join(c[3] for c in cards), sub=f"{n} practices",
                            bar=f'<div class="kdist" title="{esc(words)}" role="img" aria-label="{esc(words)}">{bar}</div>'))

    return (summary(pointer, member, next_lv, groups, short, by_lv, total, ai_only, ctx) + _bar(counts, by_lv, member, next_lv, groups)
            + f'<div class="kboard cols5">{"".join(cols)}</div>' + JS)


def summary(pointer, member, next_lv, groups, short, by_lv, total, ai_only, ctx):
    esc = ctx["esc"]
    if member:
        vm = ctx.get("vm") or {}
        return f'<p class="sum"><span class="need">{esc(vm.get("summary") or "")}</span></p>'
    lvl = pointer.get("level")
    mix = " · ".join([f"{by_lv[r]} at {r}" for r in LEVELS if by_lv[r]] + ([f"{by_lv['none']} not yet"] if by_lv["none"] else []))
    if not lvl:
        head, need = "No level yet", "Too little could be read to place a level."
    elif next_lv:
        head = lvl
        need = ("<b>Missing for " + esc(next_lv) + ":</b> "
                + "; ".join(" or ".join(esc(short(i)) for i in g) for g in groups) + ".")
    else:
        head, need = lvl, '<span class="ok">✓ Nothing missing: every list is met.</span>'
    return (f'<p class="sum"><b class="lv">{esc(head)}</b><span class="need">{need}</span>'
            f'<span class="mix">{total} practices: {esc(mix)} · {ai_only} AI reading only</span></p>')


def _bar(counts, by_lv, member, next_lv, groups):
    def chip(g, f, text, n, on=False):
        return (f'<button class="chip" type="button" data-g="{g}" data-f="{f}" aria-pressed="{"true" if on else "false"}">'
                f'{text}<b>{n}</b></button>')
    lap = ('<div class="bar"><span class="gl">On other laptops</span>' + chip("lap", "all", "All", counts["all"], True)
           + chip("lap", "now", "✓ " + C.LAPTOP["proven"][2], counts["now"]) + chip("lap", "fix", "◐ " + C.LAPTOP["not-proven"][2], counts["fix"])
           + chip("lap", "call", "☎︎ " + C.LAPTOP["call"][2], counts["call"]) + "</div>")
    if member:
        return lap
    need_n = sum(len(g) for g in groups)
    lv = ('<div class="bar"><span class="gl">Level</span>' + chip("lv", "all", "All", counts["all"], True)
          + "".join(chip("lv", r, r, by_lv[r]) for r in LEVELS if by_lv[r] or r in RUNGS) + chip("lv", "none", "Not yet", by_lv["none"])
          + (chip("lv", "need", f"Needed for {next_lv}", need_n) if need_n else "") + "</div>")
    return lap + lv
