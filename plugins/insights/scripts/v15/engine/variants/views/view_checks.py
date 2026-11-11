"""Every problem check on one screen, for judging the system in dev mode (his 11-03: "I need to assess the
credibility of what the system has done… imagine if overfitting or words like this only work on my own machine").
Per check: verdict, how it decides, what it found against its bar, whether you can open the moments, and what it
holds only if. Word searches and one-off hand reads come first: they are the ones most likely not to travel."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_kit as CK
import panel_kit as PK  # noqa: E402  a problem name opens the shared side panel

ORDER = 55
TITLE = "How each check decides"
QUESTION = "Each problem check: what it looked at, what it found, and when its answer holds."
PROXIFY_ONLY = True

CSS = """
.v-checks details.rc{margin:16px 0 0}.v-checks details.rc>summary{font-size:13px;font-weight:500;color:#3C3ED4;cursor:pointer}
.v-checks details.rc p.none{font-size:12px;color:#475467;margin:8px 0 0;line-height:1.7}
.v-checks .sum{display:flex;flex-wrap:wrap;gap:8px 32px;margin:0 0 24px;padding:16px 0;border-top:1px solid #EAECF0;border-bottom:1px solid #EAECF0}
.v-checks .sum div{min-width:140px}.v-checks .sum b{display:block;font-size:24px;font-weight:600;line-height:1.2}
.v-checks .sum span{font-size:12px;color:#475467}
.v-checks .sum .warn b{color:#B54708}
.v-checks .row{border-bottom:1px solid #EAECF0}
.v-checks .row>summary{list-style:none;display:grid;grid-template-columns:minmax(0,1.4fr) 112px minmax(0,1.3fr) 96px;gap:16px;padding:12px 0;align-items:baseline}
.v-checks .row>summary::-webkit-details-marker{display:none}
.v-checks .row>summary:hover .nm{color:#3C3ED4}
.v-checks .head{display:grid;grid-template-columns:minmax(0,1.4fr) 112px minmax(0,1.3fr) 96px;gap:16px;font-size:12px;font-weight:500;color:#667085;padding:0 0 8px;border-bottom:1px solid #EAECF0}
.v-checks .nm{font-weight:500}
.v-checks .v{font-size:12px;font-weight:600}.v-checks .v.shows{color:#B54708}.v-checks .v.no{color:#039855}.v-checks .v.ct{color:#667085}
.v-checks .k{font-size:12px;color:#475467}.v-checks .k.frag{color:#B54708;font-weight:500}
.v-checks .m{font-size:12px;color:#475467;text-align:right}
.v-checks .row .inner{padding:0 0 16px}
.v-checks .none{margin:24px 0 0;font-size:12px;color:#475467}
@media (max-width:760px){.v-checks .row>summary,.v-checks .head{grid-template-columns:minmax(0,1fr) 80px}.v-checks .k,.v-checks .m{display:none}}
"""

_V = {"shows": ("shows", "Shows"), "doesn't show": ("no", "Doesn't show"), "can't tell": ("ct", "Can't tell")}


def render(ctx):
    esc = CK.esc
    rows = [r for r in (ctx.get("cheap_problems") or {}).get("rows", []) if r.get("kind") == "challenge"]
    if not rows:
        return '<p class="empty">Not in this run.</p>'
    names = {c["id"]: c["name"] for d in (ctx.get("v15_result") or {}).get("disciplines", []) for c in d["challenges"]}
    ran = [r for r in rows if (r.get("check") or {}).get("kind", "none") != "none"]
    none = [r for r in rows if r not in ran]
    frag = [r for r in ran if r["check"]["kind"] in CK.FRAGILE]
    with_m = [r for r in ran if r["check"].get("moments")]
    verdict = [r for r in ran if r.get("shows") in ("shows", "doesn't show")]
    sums = [(len(ran), f"of {len(rows)} problems have a check that ran", ""),
            (len(verdict), "reached shows / doesn't show", ""),
            (len(frag), "rest on a word search or a one-off read: may not hold on another laptop", "warn"),
            (len(with_m), "keep moments you can open", "warn" if len(with_m) < len(ran) / 2 else "")]
    head = '<div class="sum">' + "".join(f'<div class="{c}"><b>{n}</b><span>{esc(t)}</span></div>' for n, t, c in sums) + "</div>"
    order = sorted(ran, key=lambda r: (r["check"]["kind"] not in CK.FRAGILE, r.get("shows") != "shows", names.get(r["id"], r["id"])))
    out = [head, '<div class="head"><span>Problem</span><span>Verdict</span><span>How it decides</span><span style="text-align:right">Moments</span></div>']
    for r in order:
        c = r["check"]
        cls, word = _V.get(r.get("shows"), ("ct", r.get("shows") or r.get("state")))
        n = len(c.get("moments") or [])
        out.append(f'<details class="row"><summary><span class="nm">{PK.link("c", r["id"], names.get(r["id"], r["id"]))}</span>'
                   f'<span class="v {cls}">{esc(word)}</span>'
                   f'<span class="k{" frag" if c["kind"] in CK.FRAGILE else ""}">{esc(CK.KIND.get(c["kind"], c["kind"]))}</span>'
                   f'<span class="m">{n if n else "none kept"}</span></summary>'
                   f'<div class="inner"><p>{esc(r.get("why"))}</p>{CK.block(r)}</div></details>')
    if none:
        out.append(f'<details class="rc"><summary>{len(none)} problems have no check written yet</summary><p class="none">'
                   + "; ".join(PK.link("c", r["id"], names.get(r["id"], r["id"])) for r in none) + ".</p></details>")
    return "".join(out)
