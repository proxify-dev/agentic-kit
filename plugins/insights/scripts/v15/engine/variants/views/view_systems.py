"""Option 5 of his 11-03 ask: start from what you built (the 10-27 anatomy angle), and list under each piece the
practices it serves. A practice becomes a tag on a system. Practices with nothing built for them are listed at the end."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel_kit as PK  # noqa: E402  a practice name opens the shared side panel
import practice_kit as P  # noqa: E402
import receipt_kit as K  # noqa: E402

ORDER = 23
TITLE = "Your systems, and what they do"
PROXIFY_ONLY = True  # member copy: off (11-06, a tester's "the first look on the report feels overwhelming"; By discipline carries the verdict and the step)
QUESTION = "What you built that carries your practices. Open one to see what it carries and how often it ran."

CSS = """
.v-systems>.q{margin:0 0 16px}
.v-systems .cards{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.v-systems .card{background:#FFFFFF;border:1px solid #EAECF0;border-radius:8px;padding:0;align-self:start}
.v-systems .card>summary{list-style:none;cursor:pointer;padding:12px 16px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 12px;align-items:baseline}
.v-systems .card>summary::-webkit-details-marker{display:none}
.v-systems .card>summary:hover h3{color:#3C3ED4}
.v-systems .card[open]>summary{border-bottom:1px solid #EAECF0}
.v-systems .card .n{grid-row:1 / span 2;grid-column:2;font-size:22px;font-weight:600;line-height:1;color:#15172F;text-align:right}
.v-systems .card .n small{display:block;font-size:11px;font-weight:400;color:#667085;margin-top:2px}
.v-systems .card .body{padding:4px 16px 12px}
.v-systems .card h3{font-size:14px;font-weight:600;margin:0}
.v-systems .kind{font-size:12px;color:#667085}
.v-systems .use{font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#344054;margin:8px 0}
.v-systems .serves{font-size:12px;font-weight:500;color:#475467;margin:8px 0 4px}
.v-systems ul{margin:0;padding:0;list-style:none}
.v-systems li{padding:4px 0;border-top:1px solid #EAECF0}
.v-systems li span{font-size:12px;color:#667085}
.v-systems h4{font-size:18px;font-weight:600;margin:32px 0 8px}
.v-systems .gap li{border:0;padding:2px 0}
.v-systems .edges{margin:32px 0 0}.v-systems .edges>summary{padding:4px 0}
@media (max-width:720px){.v-systems .cards{grid-template-columns:1fr}}
"""


def render(ctx):
    esc = K.esc
    ps = P.practices(ctx)
    if not ps:
        return '<p class="empty">Not in this run.</p>'
    pieces = {}
    for p in ps:
        if p["state"] != "seen":
            continue
        for e in p["evidence"]:
            if not P.BUILT_KINDS.search(e["source"]):
                continue
            x = pieces.setdefault(e["source"], {"label": e["label"], "layer": e["layer"], "details": [],
                                                "serves": [], "use": 0})
            if e["detail"] and e["detail"] not in x["details"]:
                x["details"].append(e["detail"])
            if (p["id"], p["name"], p["area"]) not in x["serves"]:
                x["serves"].append((p["id"], p["name"], p["area"]))
            x["use"] = max(x["use"], P.first_number(e["detail"]))
    ranked = sorted(pieces.values(), key=lambda x: (-len(x["serves"]), -x["use"]))
    cards = []
    for x in ranked:
        kind, _, name = x["label"].partition(" · ")
        k = len(x["serves"])
        cards.append(f'<details class="card"><summary><h3>{esc(name or kind)}</h3><div class="kind">{esc(kind)}'
                     + (" · checks your work" if x["layer"] == "tested" else "") + f'</div><span class="n">{k}<small>practice{"s" if k > 1 else ""}</small></span></summary>'
                     + '<div class="body">' + "".join(f'<div class="use">{esc(d)}</div>' for d in x["details"][:2])
                     + '<ul>' + "".join(f'<li>{PK.link("p", i, n)} <span>· {esc(a)}</span></li>' for i, n, a in x["serves"]) + "</ul></div></details>")
    bare = [p for p in ps if not ({"built", "tested"} & p["layers"])]
    gap = (f"<details class=\"edges\"><summary><h4 style=\"display:inline\">Practices with nothing built for them ({len(bare)})</h4></summary><p class=\"q\">You may still do these by hand or in a file; "
           "nothing you built carries them yet.</p><ul class=\"gap\">"
           + "".join(f'<li>{PK.link("p", p["id"], p["name"])} <span>· {esc(p["area"])}</span></li>' for p in bare) + "</ul></details>") if bare else ""
    return (f'<p class="q">{len(ranked)} things you built carry at least one practice, the busiest first.</p>'
            f'<div class="cards">{"".join(cards)}</div>{gap}')
