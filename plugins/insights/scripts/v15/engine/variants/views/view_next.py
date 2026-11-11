"""What to try next: one step per discipline, the same "Try next" the discipline cards show, the summary's first step on
top. One answer to "what should I do": on 11-06 this tab ranked problems and the cards ranked practice steps, so a member
read two different first steps (a tester's run: "Own what ships" on the cards, "Nothing is guarded" here). Proxify's copy
keeps the engine's problem ranking below, for the call."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lvl_tip  # noqa: E402  one tooltip for every level badge
import panel_kit as PK  # noqa: E402  the problem and the practice open the shared side panel
import view_areas as VA  # noqa: E402  the verdicts and the Try next sentence, so both tabs say the same thing

ORDER = 40
TITLE = "What to try next"
QUESTION = "One step per discipline, the most useful first."
PROXIFY_ONLY = False

CSS = """
.v-next .lede{font-size:18px;font-weight:600;line-height:1.4;color:#15172F;margin:0 0 16px}
.v-next .kboard{grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.v-next .kc{display:flex;flex-direction:column;gap:6px;padding:14px 16px;border-radius:12px;cursor:default}
.v-next .kc-t{font-size:16px;font-weight:600;line-height:1.35;margin:0}
.v-next .area{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#3C3ED4}
.v-next .do{margin:0;color:#344054;font-size:13px}
.v-next .do b{font-weight:600;color:#15172F}
.v-next .kc-line{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;margin:0;font-size:13px;color:#344054}
.v-next details.rc{border-top:1px solid #EAECF0;padding-top:8px;margin-top:auto}
.v-next details.rc>summary{cursor:pointer;font-size:12px;color:#3C3ED4}
.v-next details.more>summary{cursor:pointer;color:#3C3ED4;font-size:12px;font-weight:500;margin:16px 0}
.v-next dl{margin:8px 0 0;display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:12px;color:#475467}
.v-next dt{font-weight:500;color:#344054}.v-next dd{margin:0;overflow-wrap:anywhere}
.v-next ol.steps{list-style:none;margin:0;padding:0;display:grid;gap:12px}
.v-next .st{padding:14px 16px;border-radius:12px;background:#FFFFFF;border:1px solid #EAECF0}
.v-next .st.first{border-color:#999AFD;border-left:3px solid #5258FB;background:#F5F5FF}
.v-next .st p{margin:0}.v-next .st .w{margin-top:4px;font-size:15px;font-weight:500;color:#15172F;max-width:80ch}
.v-next .st .hw,.v-next .st .why{margin-top:4px;font-size:13px;color:#475467;max-width:80ch}.v-next .st b{font-weight:600;color:#344054}
.v-next .tag{font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#3C3ED4;margin-left:8px}
.v-next h3{font-size:16px;font-weight:600;margin:40px 0 4px}.v-next .sub{color:#475467;margin:0 0 16px;font-size:13px}
"""

NEXT_WORD = {"spread": "use it where this problem still shows"}


def _next_word(n, RUNG_WORD, member=False):
    return NEXT_WORD.get(n) or ("go up to: " + (RUNG_WORD[n] if member else str(n)) if n in RUNG_WORD else str(n))


def _first(s):
    return (s or "").strip().split(". ")[0].rstrip(".") + "." if s else ""


def _card(item, raw, esc, RUNG_WORD, area_name, member=False):
    """One next step as a card: the problem, what to do, where you are today, one line of why. The rest is in the panel
    (the problem and the practice are links) and behind "How this was picked"."""
    text = item["intro"] or item["step"] or item["why_it_matters"]
    rows = [("Evidence from", raw.get("evidence_key")), ("Strength of the evidence", raw.get("grade")),
            ("Weight", raw.get("weight")), ("Problem id", raw.get("challenge")),
            ("Practice id", raw.get("practice")),
            ("Where you are now", f'{RUNG_WORD.get(raw.get("now"), raw.get("now")) if member else raw.get("now")}; next move: '
                                  f'{_next_word(raw.get("next"), RUNG_WORD, member)}'),
            ("Why this one", "The engine ranks by how close you already are to the top of the practice, then by "
                             "its place in the list. It is a fixed rule in code, no AI read picks the order."),
            ("Also helps with", "; ".join(raw.get("also_for") or []) or None),
            ("Checked by a person", "not checked by a person yet")]
    dl = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in rows if v not in (None, ""))
    cid, pid = raw.get("challenge"), raw.get("practice")
    prob = PK.link("c", cid, item["problem"]) if cid else esc(item["problem"])
    prac = PK.link("p", pid, item["practice"]) if pid else esc(item["practice"])
    today = f'<span class="rung r0"{"" if member else lvl_tip.attrs(pid, raw.get("now"))}>Today: {esc(item["today"])}</span>'
    return (f'<div class="kc" data-pk="c:{esc(cid)}"><span class="area">{esc(area_name)}</span><span class="kc-t">{prob}</span>'
            f'<div class="kc-row">{today}</div><p class="do"><b>Try:</b> {prac}</p>'
            + (f'<p class="kc-line">{esc(text)}</p>' if text else "")
            + f'<details class="rc"><summary>How this was picked</summary><dl>{dl}</dl></details></div>')


def steps(ctx):
    """One step per discipline from the engine's verdicts, the summary's first step on top, then areas with a gap."""
    esc, res = ctx["esc"], ctx.get("v15_result") or {}
    V = VA._verdicts(res) or {}
    D, first = V.get("disciplines") or {}, ((V.get("summary") or {}).get("next") or {})
    names = {d["id"]: d["name"] for d in res.get("disciplines") or []}
    rank = {"partly": 0, "by_hand": 1, "cant_tell": 2, "strong": 3}
    order = sorted((k for k in D if D[k].get("next")), key=lambda k: (not (first and D[k]["next"].get("practice") == first.get("practice")),
                                                                      rank.get(D[k].get("verdict"), 9)))
    lis = []
    for i, k in enumerate(order):
        a, n = D[k], D[k]["next"]
        g = a.get("gap") or {}
        lis.append(f'<li class="st{" first" if i == 0 else ""}"><p><span class="area">{esc(names.get(k, k))}</span>'
                   + ('<span class="tag">Start here</span>' if i == 0 else "") + "</p>"
                   f'<p class="w">{esc(VA._next_text(n))}{VA._link(esc, n.get("link"))}</p>'
                   + (f'<p class="hw"><b>How:</b> {esc(VA.fit_line(n["how"], 220))}</p>' if n.get("how") else "")
                   + (f'<p class="why"><b>Why it matters:</b> {esc(VA._you(g["why"]))}</p>' if g.get("why") else "")
                   + "</li>")
    return lis


def render(ctx):
    esc, vm, res = ctx["esc"], ctx["vm"], ctx.get("v15_result")
    if not res:
        return '<p class="empty">not in this run</p>'
    lis = steps(ctx)
    out = (f'<p class="lede">{len(lis)} step{"s" if len(lis) != 1 else ""} worth trying, one per discipline.</p>'
           f'<ol class="steps">{"".join(lis)}</ol>') if lis else '<p class="empty">No step to suggest from this run.</p>'
    if ctx.get("member") or not vm.get("next"):
        return out
    # Proxify only: the engine's problem ranking, for the call
    raws = list(res.get("recommendations", [])) + list(res.get("more_recommendations", []))
    items = list(vm["next"]) + list(vm.get("more_next", []))
    area = {a["id"]: a["name"] for a in vm.get("areas", [])}

    def cards(lo, hi):
        return "".join(_card(it, raws[i] if i < len(raws) else {}, esc, ctx["RUNG_WORD"],
                             area.get(it["area"], it["area"]), False)
                       for i, it in enumerate(items[lo:hi], lo))
    n = len(vm["next"])
    out += ('<h3>Problems the engine ranked</h3><p class="sub">Internal: never on the member\'s copy. Ranked by problem, '
            'not by practice step; for the call.</p>' f'<div class="kboard">{cards(0, n)}</div>')
    if len(items) > n:
        out += (f'<details class="more"><summary>Show all {len(items)} (the other {len(items) - n})</summary>'
                f'<div class="kboard">{cards(n, len(items))}</div></details>')
    return out
