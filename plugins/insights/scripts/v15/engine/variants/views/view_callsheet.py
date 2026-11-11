"""The interviewer's call sheet as a view: what to ask on a call, given what the laptop already showed.

Data is the same as `engine/call_sheet.py` (it only has `main()`, which writes a whole page, so nothing could be
imported from it but its location): the questions per discipline come from `engine/substrate.json`; "folk" problems
are left out; the laptop has answered a problem when it settled it (shows / doesn't show, not "can't tell", not
"not read"). Those move to the end of their discipline. Kept printable: every block is open, nothing sits behind a tab.
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card_kit as C  # noqa: E402
import panel_kit as PK  # noqa: E402  a problem name opens the shared side panel

ORDER = 95
TITLE = "Call sheet"
QUESTION = "What to ask on a call, given what the scan already showed."
PROXIFY_ONLY = True

_CS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "call_sheet.py")
SAY = {True: "Laptop: the problem shows", False: "Laptop: the problem doesn't show"}

CSS = """
.v-callsheet .bar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:0 0 16px}
.v-callsheet .bar p{margin:0;font-size:14px;color:#475467}.v-callsheet .bar b{font-size:28px;font-weight:600;color:#15172F;margin-right:6px;letter-spacing:-.01em}
.v-callsheet .openall{font:inherit;font-size:12px;font-weight:500;color:#3C3ED4;background:none;border:0;cursor:pointer}
.v-callsheet .how{margin:0 0 24px}
.v-callsheet .how>summary{font-size:13px;font-weight:500;color:#3C3ED4}
.v-callsheet .how div{background:#F5F5FF;border:1px solid #D5D5FE;border-radius:12px;padding:16px 20px;margin-top:8px;max-width:900px}
.v-callsheet .how p{margin:0 0 8px}
.v-callsheet .how p:last-child{margin:0}
.v-callsheet .how code{font-size:12px;background:#FFFFFF;border:1px solid #D5D5FE;border-radius:6px;padding:8px 10px;display:block;overflow-wrap:anywhere;white-space:pre-wrap}
.v-callsheet .disc{margin:32px 0 0}
.v-callsheet .disc h3{font-size:18px;font-weight:600;line-height:1.35;margin:0}
.v-callsheet .dq{color:#475467;margin:0}
.v-callsheet .cnt{font-size:12px;color:#667085;margin:4px 0 12px}
.v-callsheet .kboard{grid-template-columns:repeat(auto-fit,minmax(380px,1fr));margin:0}
.v-callsheet details.kc{padding:0}
.v-callsheet details.kc>summary{list-style:none;cursor:pointer;padding:12px 14px;display:block}
.v-callsheet details.kc>summary::-webkit-details-marker{display:none}
.v-callsheet details.kc[open]>summary{border-bottom:1px solid #EAECF0}
.v-callsheet details.kc>summary:hover .kc-t{color:#3C3ED4}
.v-callsheet .kc-t{display:flex;gap:8px;align-items:baseline;justify-content:space-between;font-size:13px;margin:0 0 4px}
.v-callsheet .ask{font-size:16px;line-height:1.45;color:#15172F;margin:0}
.v-callsheet .inner{padding:10px 14px 14px}
.v-callsheet .lap{font-size:12px;color:#475467;margin:0 0 8px}
.v-callsheet .lis{margin:0 0 8px}
.v-callsheet .lis b,.v-callsheet .ru b{font-size:12px;font-weight:500;color:#667085}
.v-callsheet .lis ul{margin:2px 0 0;padding-left:20px;color:#344054}
.v-callsheet .pr{margin:8px 0 0;padding-top:8px;border-top:1px solid #EAECF0}
.v-callsheet .pr i{font-style:normal;font-weight:500;color:#15172F}
.v-callsheet .rg{display:grid;grid-template-columns:40px minmax(0,1fr);gap:2px 12px;margin:4px 0 0}
.v-callsheet .rg dt{font-size:12px;font-weight:500;color:#475467;padding-top:2px}
.v-callsheet .rg dd{margin:0;color:#344054}
.v-callsheet .done{margin:12px 0 0}
.v-callsheet .done>summary{font-weight:500;color:#3C3ED4;font-size:13px;margin-bottom:8px}
.v-callsheet .rc{margin:32px 0 0;max-width:900px}
.v-callsheet .rc>summary{font-size:12px;font-weight:500;color:#3C3ED4}
.v-callsheet .rc dl{margin:8px 0 0;display:grid;grid-template-columns:152px minmax(0,1fr);gap:4px 16px;font-size:12px;color:#475467}
.v-callsheet .rc dt{font-weight:500;color:#344054}
.v-callsheet .rc dd{margin:0;overflow-wrap:anywhere}
@media (max-width:700px){.v-callsheet .rc dl{grid-template-columns:1fr}}
@media print{.v-callsheet details.kc{border-color:#D0D5DD}}
"""

JS = """<script>(function(){var r=document.getElementById('v-callsheet');if(!r)return;var b=r.querySelector('.openall');
b.addEventListener('click',function(){var d=[].slice.call(r.querySelectorAll('details.kc')),open=!d.every(function(x){return x.open});
 d.forEach(function(x){x.open=open});b.textContent=open?'Close all':'Open all'})})();</script>"""


def _substrate():
    spec = importlib.util.spec_from_file_location("call_sheet_data", _CS)
    cs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cs)
    return json.load(open(os.path.join(cs.HERE, "substrate.json")))


def _laptop(ctx):
    """id -> the laptop's evidence row, for the problems it settled. None when this run has no laptop result."""
    r = ctx.get("v15_result")
    if not r or "evidence" not in r:
        return None
    return {e["id"]: e for e in r["evidence"] if e["kind"] == "challenge" and e.get("seen") is not None
            and e.get("state") != "not read"}


def _question(esc, S, c, laptop_row):
    """One question as a card: the problem, the question to say out loud, and (open) what to listen for and what each step sounds like."""
    ladder = ""
    for pid in c["all_handlers"][:3]:
        p = S["practices"].get(pid)
        if not p:
            continue
        rows = "".join(f'<dt>{esc(k)}</dt><dd>{esc(v)}</dd>' for k, v in p["ladder"].items() if v)
        ladder += f'<div class="pr"><i>{PK.link("p", pid, p["name"])}</i><dl class="rg">{rows}</dl></div>'
    signs = "".join(f"<li>{esc(s)}</li>" for s in c["call_signs"][:3])
    tag = C.state("✓", "Laptop already answered", "good") if laptop_row else ""
    lap = (f'<p class="lap">{esc(SAY[bool(laptop_row["seen"])])}. {esc(laptop_row.get("why") or "")}</p>' if laptop_row else "")
    return (f'<details class="kc"><summary><span class="kc-t">{PK.link("c", c["id"], c["name"])}{tag}</span>'
            f'<p class="ask">“{esc(c["ask"])}”</p></summary><div class="inner">{lap}'
            + (f'<div class="lis"><b>Listen for</b><ul>{signs}</ul></div>' if signs else "")
            + (f'<div class="ru"><b>What each step sounds like</b>{ladder}</div>' if ladder else "")
            + "</div></details>")


def render(ctx):
    esc = ctx["esc"]
    try:
        S = _substrate()
    except (OSError, ValueError, ImportError):
        return '<p class="empty">not in this run: the substrate (engine/substrate.json) could not be read.</p>'
    laptop = _laptop(ctx)
    answered = laptop or {}

    how = ('<details class="how"><summary>How to record an answer</summary><div><p><b>How to record an answer.</b> Ask about the problem, never the tool. Write each answer as '
           'one line the engine reads like laptop evidence:</p>'
           '<code>{"kind":"practice","id":"&lt;practice id&gt;","rung":"L2|L3|L4","strength":"showed|said","why":"&lt;their words&gt;"}</code>'
           '<p>"showed" when they walk you through a real moment; "said" when they only claim it. '
           'A question you don\'t ask is never a minus.</p></div></details>')

    parts, n_all, n_done = [], 0, 0
    for d in S["disciplines"]:
        todo, done = [], []
        for cid in d["challenges"]:
            c = S["challenges"][cid]
            if c["grade"] == "folk":
                continue
            row = answered.get(cid)
            (done if row else todo).append(_question(esc, S, c, row))
        n_all += len(todo) + len(done)
        n_done += len(done)
        cnt = f"{len(todo) + len(done)} questions" + (f" · {len(done)} already answered by the laptop, at the end" if laptop else "")
        tail = (f'<details class="done"><summary>Already answered by the laptop ({len(done)}): ask only to confirm</summary>'
                f'<div class="kboard">{"".join(done)}</div></details>' if done else "")
        parts.append(f'<section class="disc"><h3>{esc(d["name"])}</h3><p class="dq">{esc(d["question"])}</p>'
                     f'<p class="cnt">{cnt}</p><div class="kboard">{"".join(todo)}</div>{tail}</section>')

    if laptop is None:
        read = ("This run has no laptop result, so the questions are in the substrate's order, none moved to the end.")
    else:
        read = (f"The laptop settled {n_done} of {n_all} questions (shows or doesn't show). Those sit at the end of each "
                "discipline; the rest stay first, because that is where the laptop is blind. "
                "\"Can't tell\" and \"not read\" count as unanswered.")
    receipt = ('<details class="rc"><summary>Where this came from, and how the order was set</summary><dl>'
               f'<dt>Source</dt><dd>engine/substrate.json, version {esc(S.get("version"))}: the same problems and practices '
               'the laptop page reads. Which ones the laptop settled: v15-result.json, evidence list.</dd>'
               '<dt>How it was worked out</dt><dd>Per discipline, every problem except those graded "folk", with its question, '
               'what to listen for (first 3), and up to 3 practices that handle it, each with its three steps. '
               f'{esc(read)}</dd>'
               '<dt>Decided by</dt><dd>Code only. No AI read decided any of this; the wording is the substrate\'s.</dd>'
               '<dt>Checked by a person</dt><dd>not checked by a person yet</dd>'
               '<dt>Left out here</dt><dd>The old standalone sheet (call_sheet.py) shows the same content as its own page.</dd>'
               '</dl></details>')
    lead = (f'<div class="bar"><p><b>{n_all}</b>questions to ask'
            + (f', the laptop already answered {n_done}' if laptop else "") + '</p><button class="openall" type="button">Open all</button></div>')
    return lead + how + "".join(parts) + receipt + JS
