"""Option 4 + 1 of his 11-03 ask for another way to show a practice: the finding is the headline (in the person's own
terms), the practice name sits small under it, and "Found in" tags say where. No step word, no grade."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lvl_tip  # noqa: E402  the substrate's short names
import panel_kit as PK  # noqa: E402  a practice name opens the shared side panel
import practice_kit as P  # noqa: E402
import receipt_kit as K  # noqa: E402

ORDER = 21
TITLE = "What you have"
QUESTION = "Which practices your setup shows, and where each one lives. Open one for the sentence behind it."

CSS = """
.v-found .area{margin:0 0 32px}.v-found .area>h3{font-size:18px;font-weight:600;margin:0 0 8px}
.v-found details.row{background:#FFFFFF;border:1px solid #EAECF0;border-radius:8px;margin:0 0 8px}
.v-found details.row>summary{list-style:none;cursor:pointer;padding:12px 16px}
.v-found details.row>summary::-webkit-details-marker{display:none}
.v-found details.row[open]>summary{border-bottom:1px solid #EAECF0}
.v-found .hl{font-size:14px;font-weight:600;color:#15172F;margin:0 0 6px}
.v-found details.row>summary{position:relative;padding-right:40px}
.v-found details.row>summary::after{content:"";position:absolute;right:18px;top:18px;width:6px;height:6px;border-right:2px solid #667085;border-bottom:2px solid #667085;transform:rotate(45deg)}
.v-found details.row[open]>summary::after{transform:rotate(-135deg);top:21px}
.v-found .tags{display:flex;flex-wrap:wrap;gap:6px}
.v-found .tag{font-size:12px;font-weight:500;padding:2px 8px;border-radius:999px;background:#F5F5FF;color:#272594;border:1px solid #D5D5FE}
.v-found .tag.t-chats{background:#F4F5F7;color:#475467;border-color:#EAECF0}
.v-found .tag.t-tested{background:#F6FEF9;color:#039855;border-color:#C4F3D8}
.v-found .inner{padding:12px 16px}
.v-found .ev{display:grid;grid-template-columns:minmax(160px,240px) 1fr;gap:4px 16px;margin:0 0 8px}
.v-found .ev dt{font-size:12px;font-weight:500;color:#475467}
.v-found .ev dd{margin:0;min-width:0;overflow-wrap:anywhere;font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#344054}
.v-found .full{color:#344054;margin:0 0 8px}
@media (max-width:700px){.v-found .ev{grid-template-columns:1fr}.v-found .ev dd{margin-bottom:8px}}
.v-found .by{font-size:12px;color:#667085;margin:0}
.v-found .none{color:#475467;font-size:12px;margin:8px 0 0}
"""


def _row(p):
    esc = K.esc
    tags = "".join(f'<span class="tag t-{layer}">{esc(label)}' + (f" ×{n}" if n > 1 else "") + "</span>"
                   for label, layer, n in P.tags(p["evidence"]))
    ev = "".join(f'<dt>{esc(e["label"])}</dt><dd>{esc(e["detail"])}</dd>' for e in p["evidence"])
    by = ("Found by plain code over your logs and files." if p["derivation"] == "code"
          else "Found by one AI read of everything you built, then written as the sentence above.")
    short = (lvl_tip._practices().get(p["id"]) or {}).get("short_name") or p["name"]
    return (f'<details class="row"><summary><div class="hl">{PK.link("p", p["id"], short)}</div><div class="tags">{tags}</div></summary>'
            f'<div class="inner"><p class="full">{esc(p["why"])}</p><p class="by">{esc(p["name"])}</p><dl class="ev">{ev}</dl>'
            f'<p class="by">{by} Not checked by a person yet.</p></div></details>')


def render(ctx):
    ps = P.practices(ctx)
    if not ps:
        return '<p class="empty">Not in this run.</p>'
    out, areas = [], []
    for p in ps:
        if p["area"] not in areas:
            areas.append(p["area"])
    for a in areas:
        mine = [p for p in ps if p["area"] == a]
        found = [p for p in mine if p["evidence"] and p["state"] == "seen"]
        none = [p for p in mine if p not in found]
        found.sort(key=lambda p: -len(p["evidence"]))
        out.append(f'<div class="area"><h3>{K.esc(a)}</h3>' + "".join(_row(p) for p in found)
                   + (f'<p class="none">Nothing found yet for: {K.esc("; ".join((lvl_tip._practices().get(p["id"]) or {}).get("short_name") or p["name"] for p in none))}.</p>' if none else "")
                   + "</div>")
    return "".join(out)
