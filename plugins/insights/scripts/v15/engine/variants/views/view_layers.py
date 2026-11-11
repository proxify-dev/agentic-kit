"""Option 2 of his 11-03 ask: where each practice lives — in your chats, in a file you set up, in something you built,
checked by a test. Several can be true at once, which a single step word could never show. No grade."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import practice_kit as P  # noqa: E402
import lvl_tip  # noqa: E402  the substrate's short names
import panel_kit as PK  # noqa: E402  a practice name opens the shared side panel
import receipt_kit as K  # noqa: E402

ORDER = 22
TITLE = "Where each practice lives"
QUESTION = "For each practice: done by hand, set up in a file, built as a tool, or proven by a test."

CSS = """
.v-layers .legend{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 24px}
.v-layers .legend div{background:#FFFFFF;border:1px solid #EAECF0;border-radius:8px;padding:12px}
.v-layers .legend b{display:block;font-weight:600}.v-layers .legend span{font-size:12px;color:#475467}
.v-layers .legend .n{font-size:24px;font-weight:600;color:#3C3ED4;display:block}
.v-layers>.q{margin:0 0 16px}
.v-layers h3{font-size:18px;font-weight:600;margin:24px 0 8px}
.v-layers .grid,.v-layers details>summary{display:grid;grid-template-columns:1fr repeat(4,88px);align-items:center;gap:8px}
.v-layers .head{font-size:12px;font-weight:500;color:#667085;padding:0 16px 4px;text-align:center;white-space:nowrap}
.v-layers .head span:first-child{text-align:left}
.v-layers details{background:#FFFFFF;border:1px solid #EAECF0;border-radius:8px;margin:0 0 4px}
.v-layers details>summary{list-style:none;cursor:pointer;padding:8px 16px}
.v-layers details>summary::-webkit-details-marker{display:none}
.v-layers .dot{justify-self:center;width:12px;height:12px;border-radius:999px;border:2px solid #D0D5DD}
.v-layers .dot.on{background:#5258FB;border-color:#5258FB}
.v-layers .dot.on.tested{background:#039855;border-color:#039855}
.v-layers .inner{padding:8px 16px 12px;border-top:1px solid #EAECF0}
.v-layers .inner p{margin:4px 0;font-size:12px;color:#344054}
.v-layers .inner b{font-weight:600;color:#475467}
@media (max-width:720px){.v-layers .legend{grid-template-columns:1fr 1fr}
  .v-layers .grid,.v-layers details>summary{grid-template-columns:1fr repeat(4,56px)}}
@media (max-width:480px){.v-layers .grid,.v-layers details>summary{grid-template-columns:repeat(4,1fr);row-gap:4px}
  .v-layers .grid>span:first-child,.v-layers details>summary>:first-child{grid-column:1/-1}}
"""


SHORT = {"chats": "Sessions", "file": "A file", "built": "Built", "tested": "Tested"}


def render(ctx):
    esc = K.esc
    ps = P.practices(ctx)
    if not ps:
        return '<p class="empty">Not in this run.</p>'
    legend = "".join(f'<div><span class="n">{sum(k in p["layers"] for p in ps)}</span><b>{esc(t)}</b>'
                     f'<span>{esc(d)}</span></div>' for k, t, d in P.LAYERS)
    out = [f'<p class="q">Of {len(ps)} practices, how many show up in each place. Open a row for the lines behind each dot.</p><div class="legend">{legend}</div>']
    # the column says one word; the legend cards above (and the title) carry the full name, so nothing wraps
    head = ('<div class="grid head"><span>Practice</span>'
            + "".join(f'<span title="{esc(t)}">{esc(SHORT.get(k, t))}</span>' for k, t, _ in P.LAYERS) + "</div>")
    areas = []
    for p in ps:
        if p["area"] not in areas:
            areas.append(p["area"])
    for a in areas:
        out.append(f"<h3>{esc(a)}</h3>{head}")
        for p in [p for p in ps if p["area"] == a]:
            dots = "".join(f'<span class="dot {"on" if k in p["layers"] else ""} {k}" role="img" '
                           f'aria-label="{esc(t)}: {"yes" if k in p["layers"] else "no"}" title="{esc(t)}"></span>'
                           for k, t, _ in P.LAYERS)
            lines = "".join(f'<p><b>{esc(t)}:</b> ' + esc("; ".join(f'{e["label"]} — {e["detail"]}'
                            for e in p["evidence"] if e["layer"] == k)) + "</p>"
                            for k, t, _ in P.LAYERS if k in p["layers"]) or "<p>Nothing found yet.</p>"
            out.append(f'<details><summary><span title="{esc(p["name"])}">{PK.link("p", p["id"], (lvl_tip._practices().get(p["id"]) or {}).get("short_name") or p["name"])}</span>{dots}</summary>'
                       f'<div class="inner">{lines}</div></details>')
    return "".join(out)
