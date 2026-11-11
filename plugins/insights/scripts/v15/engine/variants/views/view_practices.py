"""Practices, precisely: one card per practice with what was found, the lines it rests on, the bar it was held to,
how it was decided, where we looked and whether a person has checked it. Pattern: /tmp/v15-shots/old-place.png."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lvl_tip  # noqa: E402  one tooltip for every level badge
import panel_kit as PK  # noqa: E402  a practice name opens the shared side panel
import receipt_kit as K  # noqa: E402

ORDER = 20
TITLE = "Practices, precisely"
QUESTION = "Every practice: what we found, where, and how its level was set."
PROXIFY_ONLY = True

# who carries it: rung -> (filter key, word). No rung -> state word, filter "not".
_KEY = {"L4": "runs", "L3": "setup", "L2": "you", "L1": "tried"}
_NEXT = {"L1": "L2", "L2": "L3", "L3": "L4"}
_CHIPS = [("all", "All"), ("runs", "L4"), ("setup", "L3"), ("you", "L2"),
          ("not", "Not yet")]

CSS = K.css("practices") + """
.v-practices .nr{font-size:12px;color:#667085}
.v-practices .one{display:none}
"""


def _word(det, RW, NO):
    rung = det.get("rung")
    if rung:
        return _KEY.get(rung, "not"), rung
    return "not", NO.get(det.get("state"), "Not yet")


def _searched(entry):
    """`[hook:x:y.sh] Stop · memory@ak · 5409/438 chats · long text...` -> (head, rest). The head is the tag and the first
    segment; the long lines the AI read sit behind a fold."""
    m = re.match(r"\[([^\]]*)\]\s*(.*)$", entry, re.S)
    tag, body = (m.group(1), m.group(2)) if m else ("", entry)
    first, _, rest = body.partition(" · ")
    head = (K.source_label(tag) + (" · " if tag and first else "") + first)[:160]
    return head, rest


def _evidence(rows):
    if not rows:
        return '<p class="missing">No source lines recorded for this practice.</p>'

    def li(e):
        return (f'<li><span class="src">{K.esc(K.source_label(e.get("source")))}</span>'
                f'<span class="mono">{K.esc(K.receipt(e.get("detail")))}</span></li>')
    out = f'<ul class="ev">{"".join(li(e) for e in rows[:5])}</ul>'
    if len(rows) > 5:
        out += K.fold(f"Show all {len(rows)}", f'<ul class="ev">{"".join(li(e) for e in rows[5:])}</ul>')
    return out


def _bar(p, det, RW):
    lad, rung, could = p.get("ladder", {}), det.get("rung"), det.get("could_be")
    out = []
    if rung and lad.get(rung):
        out.append(f'<p><b>{K.esc(rung)}</b> needs: {K.esc(K.plain(lad[rung]))}</p>')
    nxt = could or _NEXT.get(rung) or ("L2" if not rung else None)
    if rung == "L4":
        nxt = None
    if nxt and lad.get(nxt):
        lead = f"To reach {nxt}" if rung or could else f"First step, {nxt}"
        out.append(f'<p class="dim"><b>{K.esc(lead)}:</b> {K.esc(K.plain(lad[nxt]))}</p>')
    if not rung:
        out.insert(0, '<p>No step of the ladder was met on what we could read.</p>')
    if rung == "L4":
        out.append('<p class="dim">This is the top step.</p>')
    return "".join(out)


def _how(det):
    bits = []
    if det.get("derivation") == "code":
        bits.append("<p><b>Plain code</b> decided this: a counted rule over your logs and files, no AI read.</p>")
    if det.get("found_by") == "one-pass":
        bits.append("<p><b>One AI read of your setup files</b> decided this, one pass over everything you built.</p>")
    if det.get("rule"):
        bits.append(f'<p>Our one-line reason: <span class="mono">{K.esc(K.receipt(det["rule"]))}</span></p>')
    basis = det.get("basis")
    bits.append(f'<p class="dim">Rests on: <span class="mono">{K.esc(basis)}</span></p>' if basis else
                '<p class="dim">No single file or line is named as the basis.</p>')
    return "".join(bits)


def _looked(det):
    bits = []
    for c in det.get("checked") or []:
        bits.append(f"<p>{K.esc(K.plain(c))}</p>")
    for s in det.get("searched") or []:
        head, rest = _searched(s)
        bits.append(f'<p class="mono">{K.esc(head)}</p>' + (K.fold("Show the lines we read", K.esc(rest)) if rest else ""))
    if not bits:
        return '<p class="missing">Nothing recorded about where we looked.</p>'
    return "".join(bits)


def _card(p, det, RW, NO, clean, area):
    key, word = _word(det, RW, NO)
    could = det.get("could_be")
    why = clean(det.get("why")) or K.plain(det.get("why"))
    name = clean(p["name"]) or p["name"]
    verdict = det.get("verdict") or "unchecked"
    checked = ('<p><span class="warn">Not yet</span> - no person has read this finding against the evidence.</p>'
               if verdict == "unchecked" else f"<p>{K.esc(verdict)}</p>")
    if det.get("note"):
        checked += f'<p class="dim">{K.esc(det["note"])}</p>'
    ask = (f'<div class="sec"><div class="lab">Ask on the call</div><p class="ask">{K.esc(K.plain(det["ask"]))}</p></div>'
           if det.get("ask") else "")
    held = (f'<div class="sec"><div class="lab">What held it back</div><p>{K.esc(K.plain(det["held"]))}</p></div>'
            if det.get("held") else "")
    return (
        f'<details class="card" data-w="{key}"><summary><span class="nm">{PK.link("p", p["id"], name)}</span>'
        f'<span class="pill {"" if key != "not" else "none"}"{lvl_tip.attrs(p["id"], det.get("rung"))}>{K.esc(word)}</span>'
        + (f'<span class="could"{lvl_tip.attrs(p["id"], could)}>could be {K.esc(could)}</span>' if could else "")
        + f'<span class="one">{K.esc(why)}</span></summary>'
        f'<div class="body two"><div>'
        f'<div class="sec"><div class="lab">What we found</div><p>{K.esc(why)}</p></div>'
        f'<div class="sec"><div class="lab">Evidence</div>{_evidence(det.get("evidence") or [])}</div>'
        f'<div class="sec"><div class="lab">Where we looked</div>{_looked(det)}</div></div>'
        f'<div><div class="sec"><div class="lab">Bar</div>{_bar(p, det, RW)}</div>'
        f'<div class="sec"><div class="lab">How it was decided</div>{_how(det)}</div>{held}'
        f'<div class="sec"><div class="lab">Checked by a person</div>{checked}</div>{ask}</div>'
        f'</div></details>')


JS = """<script>(function(){var r=document.getElementById('v-practices');if(!r)return;
var cards=[].slice.call(r.querySelectorAll('.card')),chips=[].slice.call(r.querySelectorAll('.chip')),
 areas=[].slice.call(r.querySelectorAll('.area')),btn=r.querySelector('.openall'),f='all';
function show(){cards.forEach(function(c){c.hidden=!(f==='all'||c.dataset.w===f)});
 areas.forEach(function(a){a.hidden=!a.querySelector('.card:not([hidden])')});label()}
function label(){var v=cards.filter(function(c){return!c.hidden});
 btn.textContent=v.length&&v.every(function(c){return c.open})?'Close all':'Open all'}
chips.forEach(function(c){c.addEventListener('click',function(){f=c.dataset.f;
 chips.forEach(function(x){x.setAttribute('aria-pressed',x===c)});show()})});
btn.addEventListener('click',function(){var v=cards.filter(function(c){return!c.hidden}),
 open=!v.every(function(c){return c.open});v.forEach(function(c){c.open=open});label()});
cards.forEach(function(c){c.addEventListener('toggle',label)});show()})();</script>"""


def render(ctx):
    res, D = ctx.get("v15_result"), None
    if not res or not res.get("detectors"):
        return '<p class="empty">Not in this run: v15-result.json has no practice findings.</p>'
    D, RW, clean = res["detectors"], ctx["RUNG_WORD"], ctx["clean"]
    from common import NO_RUNG_WORD as NO  # the member wording for a practice with no rung
    areas, counts = [], {k: 0 for k, _ in _CHIPS}
    for d in res["disciplines"]:
        cards, split = [], {}
        for p in d["practices"]:
            det = D.get(f'{d["id"]}/{p["id"]}')
            if not det:
                continue
            key, word = _word(det, RW, NO)
            cards.append((-{"runs": 4, "setup": 3, "you": 2, "tried": 1}.get(key, 0), _card(p, det, RW, NO, clean, d)))
            split[word] = split.get(word, 0) + 1
            if key in counts:
                counts[key] += 1
            counts["all"] += 1
        cards.sort(key=lambda c: c[0])  # stable: the engine's own order inside one word
        if cards:
            areas.append(
                f'<div class="area"><h3>{K.esc(d["name"])}</h3><p class="sub">{K.esc(clean(d.get("question")))}</p>'
                f'<p class="counts">{len(cards)} practices: {" · ".join(f"{K.esc(w)} {n}" for w, n in split.items())}</p>'
                + "".join(c for _, c in cards) + "</div>")
    chips = "".join(
        f'<button class="chip" type="button" data-f="{k}" aria-pressed="{"true" if k == "all" else "false"}">'
        f'{K.esc(w)}<b>{counts[k]}</b></button>' for k, w in _CHIPS)
    return (f'<div class="bar">{chips}<button class="openall" type="button">Open all</button></div>'
            + "".join(areas) + JS)
