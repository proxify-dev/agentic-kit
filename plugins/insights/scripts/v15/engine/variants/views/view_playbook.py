"""Playbook: what the playbook teaches for each practice, what is missing, and which of its pages are built from the
substrate. Reads labs/v15/tools/map.py (imported by path: its `analyse()` already joins the substrate with
coverage/playbook.json) and the substrate yamls. Nothing is recomputed that map.py or tools/coverage.py already decide.
Needs PyYAML (frame.py is run with `uv run --no-project --with pyyaml`); without it the view says so."""
import html
import importlib.util
import json
import os
import re

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel_kit as PK  # noqa: E402  a practice name opens the shared side panel

ORDER = 93
TITLE = "Playbook"
QUESTION = ("What does the playbook teach for each practice, what is missing, and which pages are built from the "
            "substrate?")
PROXIFY_ONLY = True

LABS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))  # labs/v15
READ_ON, READ_RUN = "2026-11-02", "wf_5514a5f1-d0c"  # the coverage read: coverage/README.md header
TOP = 5
WORD = {"taught": "Taught", "mentioned": "Only mentioned", "missing": "Missing", "n/a": "No such level"}
RUNG_ORDER = {"L2": 0, "L3": 1, "L4": 2}
# the words tools/coverage.py searches the coverage notes for, plus "rejects" (the hooks note says it that way)
OPPOSITE = re.compile(r"(opposite|contradict|teaches instruction stacking|unsupervised|rules out|the wrong|misleads|rejects)", re.I)
PITFALLS_PAGE = "/reference/common-pitfalls"

CSS = """
.v-playbook [hidden]{display:none!important}
.v-playbook .stale{margin:0 0 24px;padding:12px 16px;border:1px solid #FDF1D9;background:#FEFBF5;border-radius:12px;max-width:80ch}
.v-playbook .stale b{color:#B54708;font-weight:600}
.v-playbook .sum{display:flex;flex-wrap:wrap;gap:8px 32px;margin:0 0 8px;padding:16px 0;border-top:1px solid #EAECF0;border-bottom:1px solid #EAECF0}
.v-playbook .sum div{min-width:120px}.v-playbook .sum b{display:block;font-size:24px;font-weight:600;line-height:1.2}
.v-playbook .sum span{font-size:12px;color:#475467}
.v-playbook h3{font-size:18px;font-weight:600;margin:48px 0 4px}
.v-playbook h4{font-size:14px;font-weight:600;margin:24px 0 8px}
.v-playbook h4 .cnt{font-weight:400;color:#667085}
.v-playbook .lead{color:#475467;margin:0 0 16px;max-width:72ch}
.v-playbook .mono{font:400 12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}
.v-playbook .dim{color:#667085;font-size:12px}
.v-playbook .hd,.v-playbook .row>summary{display:grid;grid-template-columns:minmax(0,3fr) repeat(3,minmax(0,1fr));gap:16px;align-items:start}
.v-playbook .hd{font-size:12px;font-weight:500;color:#667085;padding:0 0 8px 16px;border-bottom:1px solid #EAECF0}
.v-playbook .row{border-bottom:1px solid #EAECF0}
.v-playbook .row>summary{list-style:none;padding:12px 0 12px 16px;position:relative}
.v-playbook .row>summary::-webkit-details-marker,.v-playbook .card>summary::-webkit-details-marker,.v-playbook .more>summary::-webkit-details-marker,.v-playbook .rc>summary::-webkit-details-marker{display:none}
.v-playbook .row>summary::before,.v-playbook .card>summary::before{content:"";position:absolute;left:2px;top:19px;width:5px;height:5px;border-right:2px solid #667085;border-bottom:2px solid #667085;transform:rotate(-45deg);transition:transform .12s}
.v-playbook .row[open]>summary::before,.v-playbook .card[open]>summary::before{transform:rotate(45deg);top:17px}
.v-playbook .row>summary:hover .nm{color:#3C3ED4}
.v-playbook .nm{font-weight:500;min-width:0}
.v-playbook .rung{display:inline-flex;align-items:center;font-size:12px;font-weight:500;padding:0 8px;border-radius:999px;border:1px solid #EAECF0;background:#F9FAFB;color:#475467;white-space:nowrap}
.v-playbook .rung.taught{border-color:#C4F3D8;background:#F6FEF9;color:#039855}
.v-playbook .rung.mentioned{border-color:#FDF1D9;background:#FEFBF5;color:#B54708}
.v-playbook .rung.missing{border-color:#DC6803;background:#FDF1D9;color:#B54708;font-weight:600}
.v-playbook .rung.na{color:#667085}
.v-playbook .rowbody{padding:0 0 16px 16px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px 24px}
.v-playbook .rowbody .note{grid-column:1 / -1;margin:0;color:#475467;max-width:100ch}
.v-playbook .rowbody .lv>b{display:block;font-size:12px;font-weight:600;color:#475467;margin:0 0 4px}
.v-playbook .rowbody .lv p{margin:0 0 4px;font-size:12px;color:#475467}
.v-playbook .rowbody ul{margin:0;padding:0;list-style:none}
.v-playbook .rowbody li{font-size:12px;margin:0 0 4px;overflow-wrap:anywhere}
.v-playbook .rowbody li i{font-style:normal;color:#667085}
.v-playbook .rowbody li.taught i{color:#039855}
.v-playbook .card{background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;margin:0 0 8px}
.v-playbook .card[open]{border-color:#B1B8FF}
.v-playbook .card>summary{list-style:none;cursor:pointer;padding:12px 16px 12px 40px;position:relative;display:flex;flex-wrap:wrap;gap:2px 12px;align-items:center}
.v-playbook .card>summary::before{left:16px;top:18px}
.v-playbook .card[open]>summary::before{top:16px}
.v-playbook .card .rk{font-size:12px;font-weight:600;color:#3C3ED4}
.v-playbook .card .tt{font-weight:600}
.v-playbook .card .one{flex:1 1 100%;color:#475467;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.v-playbook .card[open] .one{display:none}
.v-playbook .card .body{padding:4px 16px 16px 40px;max-width:100ch}
.v-playbook .card .body p{margin:0 0 8px}
.v-playbook .card .lab{display:block;font-size:12px;font-weight:600;color:#475467;margin:8px 0 2px}
.v-playbook .chips{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0 0}
.v-playbook .chip{font-size:12px;font-weight:500;padding:0 8px;border-radius:999px;border:1px solid #D5D5FE;background:#F5F5FF;color:#272594}
.v-playbook .more{margin:8px 0 0}
.v-playbook .more>summary{list-style:none;cursor:pointer;color:#3C3ED4;font-weight:500;width:max-content;padding:4px 0}
.v-playbook .more[open]>summary{margin-bottom:8px}
.v-playbook .tbl{width:100%;border-collapse:collapse}
.v-playbook .tbl th{text-align:left;font-size:12px;font-weight:500;color:#667085;padding:0 16px 8px 0;border-bottom:1px solid #EAECF0}
.v-playbook .tbl td{padding:8px 16px 8px 0;border-bottom:1px solid #EAECF0;vertical-align:top}
.v-playbook .tbl td.n{font-weight:600;color:#3C3ED4;width:40px}
.v-playbook .tbl td.t{font-weight:500}
.v-playbook .cols{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 48px}
.v-playbook .rc{margin:12px 0 0;font-size:12px}
.v-playbook .rc>summary{list-style:none;cursor:pointer;color:#3C3ED4;font-weight:500;width:max-content}
.v-playbook .rc>summary::before{content:"+ "}.v-playbook .rc[open]>summary::before{content:"- "}
.v-playbook .rc dl{margin:8px 0 0;display:grid;grid-template-columns:96px minmax(0,1fr);gap:4px 12px;padding:8px 12px;background:#F9FAFB;border:1px solid #EAECF0;border-radius:8px;max-width:100ch}
.v-playbook .rc dt{font-weight:600;color:#15172F}.v-playbook .rc dd{margin:0;color:#475467;overflow-wrap:anywhere}
@media (max-width:900px){.v-playbook .hd,.v-playbook .row>summary{grid-template-columns:minmax(0,2fr) repeat(3,minmax(0,1fr));gap:8px}
  .v-playbook .rowbody,.v-playbook .cols{grid-template-columns:1fr}}
"""


def _esc(s):
    return html.escape("" if s is None else str(s))


def _map():
    spec = importlib.util.spec_from_file_location("v15map", os.path.join(LABS, "tools", "map.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _receipt(rows):
    body = "".join(f"<dt>{_esc(k)}</dt><dd>{v}</dd>" for k, v in rows)
    return f'<details class="rc"><summary>How we know</summary><dl>{body}</dl></details>'


def _rung(state):
    return f'<span class="rung {"na" if state == "n/a" else state}">{WORD[state]}</span>'


def _status_word(st):
    return {"teaches": "teaches it", "mentions": "only mentions it"}.get(st, st)


def _rung_lists(p):
    out = ""
    for r in ("L2", "L3", "L4"):
        state = p["health"]["playbook"][r]
        ladder = (p.get("ladder") or {}).get(r)
        if state == "n/a":
            out += f'<div class="lv"><b>{r} {_rung(state)}</b><p>This practice has no {r} level.</p></div>'
            continue
        pages = "".join(
            f'<li class="{_esc(e.get("status"))}">{_esc(e.get("page"))} <i>· {_esc(e.get("section") or "")} '
            f'({_esc(_status_word(e.get("status")))})</i></li>' for e in p["coverage"][r])
        out += (f'<div class="lv"><b>{r} {_rung(state)}</b><p>{_esc(ladder)}</p>'
                + (f"<ul>{pages}</ul>" if pages else '<p class="dim">No page found.</p>') + "</div>")
    return out


def _practice_rows(P, ids):
    out = ""
    for pid in ids:
        p = P[pid]
        pb = p["health"]["playbook"]
        note = f'<p class="note">{_esc(p["coverage_note"])}</p>' if p.get("coverage_note") else ""
        out += (f'<details class="row"><summary><span class="nm">{PK.link("p", pid, p["name"])}</span>'
                f'<span>{_rung(pb["L2"])}</span><span>{_rung(pb["L3"])}</span><span>{_rung(pb["L4"])}</span></summary>'
                f'<div class="rowbody">{note}{_rung_lists(p)}</div></details>')
    return out


def _more(items, render, top=TOP, noun="items"):
    """Top few, then 'Show all N' inline (a details: the rest opens under the summary)."""
    head, rest = items[:top], items[top:]
    out = "".join(render(i, x) for i, x in enumerate(head))
    if rest:
        out += (f'<details class="more"><summary>Show all {len(items)} {noun}</summary>'
                + "".join(render(top + i, x) for i, x in enumerate(rest)) + "</details>")
    return out


def _page_card(P, i, pg):
    names = [P[x]["name"] if x in P else x for x in pg.get("practices") or []]
    rungs = "".join(f'<span class="chip">{_esc(r)}</span>' for r in pg.get("rungs") or [])
    return (f'<details class="card"><summary><span class="rk">{i + 1}</span><span class="tt">{_esc(pg["title"])}</span>'
            f'<span class="dim">{_esc(pg["discipline"])}</span><span class="one">{_esc(pg.get("what_it_teaches"))}</span></summary>'
            f'<div class="body"><span class="lab">What it would teach</span><p>{_esc(pg.get("what_it_teaches"))}</p>'
            f'<span class="lab">Why it matters</span><p>{_esc(pg.get("why_it_matters"))}</p>'
            f'<span class="lab">Closest page today</span><p>{_esc(pg.get("closest_existing"))}</p>'
            f'<span class="lab">Practices it would close</span><p>{_esc(" · ".join(names))}</p>'
            f'<span class="lab">Levels it would close</span><div class="chips">{rungs}</div></div></details>')


def _opposite_card(i, x):
    p = x
    return (f'<details class="card"><summary><span class="tt">{_esc(p["name"])}</span>'
            f'<span class="dim">{_esc(p["discipline"])}</span><span class="one">{_esc(p["coverage_note"])}</span></summary>'
            f'<div class="body"><span class="lab">What the coverage read says</span><p>{_esc(p["coverage_note"])}</p></div></details>')


def render(ctx):
    try:
        m = _map()
        data = m.analyse()
    except ModuleNotFoundError as e:
        return f'<p class="empty">Not in this run: {_esc(e)}. Build with <span class="mono">uv run --no-project --with pyyaml python frame.py</span>.</p>'
    except Exception as e:  # a changed substrate must not take the page down
        return f'<p class="empty">Not in this run: the substrate could not be read ({_esc(type(e).__name__)}: {_esc(e)}).</p>'

    P, C = data["practices"], data["challenges"]
    cov_path = os.path.join(LABS, "coverage", "playbook.json")
    try:
        raw = json.load(open(cov_path))
    except (OSError, ValueError):
        return '<p class="empty">Not in this run: coverage/playbook.json is missing, so there is nothing to say about what the playbook teaches.</p>'
    cov_ids = {x["id"] for d in raw for x in d.get("practices", []) + d.get("challenges", [])}
    unread = sorted(i for i in list(P) + list(C) if i not in cov_ids)

    t = data["totals"]
    howto = next((f for f in data["flags"] if f["id"] == "no-howto"), {"items": []})["items"]
    # coverage.py's rule: lowest level it closes first (L2 before L3 before L4), then discipline in the file's own order
    # (alphabetical in tools/coverage.py; ties keep the file's order, so within a discipline the most-needed stays first)
    ranked = sorted(data["pages"], key=lambda pg: (min((RUNG_ORDER.get(r.split()[-1], 3) for r in pg.get("rungs") or []), default=3), pg["discipline"]))
    opposite = [P[pid] for pid in P if OPPOSITE.search(P[pid].get("coverage_note") or "")]
    on_pitfalls = sum(1 for p in P.values() for r in ("L2", "L3", "L4") for e in p["coverage"][r] if e.get("page") == PITFALLS_PAGE)
    pitfall_pairs = []  # a challenge carries one pitfall card or a list: the same flattening tools/publish.py does
    for c in C.values():
        cards = c.get("pitfall") or []
        pitfall_pairs += [(c, pf) for pf in (cards if isinstance(cards, list) else [cards])]
    pitfall_pairs.sort(key=lambda x: x[1].get("order", 99))
    practice_pairs = sorted(((p, p["practice_card"]) for p in P.values() if p.get("practice_card")),
                            key=lambda x: x[1].get("order", 99))

    stale = (f'<p class="stale"><b>Not re-run.</b> Coverage was read on {READ_ON} ({READ_RUN}) and has not been re-run '
             f'since the common-pitfalls page changed. {on_pitfalls} of the practice levels below cite '
             f'<span class="mono">{PITFALLS_PAGE}</span>, so their words can be out of date.</p>')
    top = "".join(f"<div><b>{n}</b><span>{_esc(s)}</span></div>" for n, s in [
        (f'{t["taught"]} of {t["rungs"]}', "practice levels the playbook teaches"),
        (t["mentioned"], "only mentioned"), (t["missing"], "missing altogether"),
        (len(ranked), "pages to write or extend"), (len(opposite), "places it teaches the opposite"),
        (f'{len(howto)} of {len(C)}', "problems with no how-to")])
    top_rc = _receipt([
        ("From", '<span class="mono">labs/v15/coverage/playbook.json</span>, joined with the substrate by <span class="mono">labs/v15/tools/map.py</span> (its <span class="mono">analyse()</span>).'),
        ("How", "A practice level is Taught when at least one playbook page section teaches it, Only mentioned when pages touch it but none teaches it, Missing when no page touches it. "
                "Levels the substrate does not define for a practice are not counted (90 levels in all)."),
        ("Who decided", f"An AI read every playbook page ({READ_RUN}, {READ_ON}); code only did the counting. Not checked by a person yet."),
        ("No how-to", f"{len(howto)} of {len(C)} problems have no page that shows the fix. This counts problems added to the substrate after the read "
                      f"({len([i for i in unread if i in C])} have no coverage row at all), so it is higher than the 15 of 61 in the coverage README."),
    ])

    by_disc = ""
    for d in data["disciplines"]:
        ids = [i for i in d["practices"] if i in P]
        states = [s for i in ids for s in P[i]["health"]["playbook"].values() if s != "n/a"]
        counts = " · ".join(f"{states.count(k)} {w.lower()}" for k, w in (("taught", "Taught"), ("mentioned", "Only mentioned"), ("missing", "Missing")))
        by_disc += (f'<h4>{_esc(d["name"])} <span class="cnt">{len(ids)} practices · {counts}</span></h4>'
                    f'<div class="hd"><span>Practice</span><span>L2</span><span>L3</span><span>L4</span></div>'
                    + _practice_rows(P, ids))
    taught_block = (
        '<h3>What the playbook teaches, practice by practice</h3>'
        '<p class="lead">Each practice has up to three levels (L2, L3, L4). For each: does a playbook page teach it, only mention it, or '
        'does nothing touch it? Open a row for the level text, the pages and the coverage note.</p>' + by_disc
        + _receipt([("From", 'The "Pages" lists come straight from <span class="mono">coverage/playbook.json</span>: page, section, and whether the AI read '
                             'judged the section teaches or mentions the level.'),
                    ("How", "Taught beats Only mentioned beats Missing: one teaching section is enough for Taught."),
                    ("Can't see", "Whether a taught section is any good. It says the page covers the level, not that members learn it from it.")]))

    pages_block = (
        '<h3>Pages to write or extend</h3>'
        f'<p class="lead">{len(ranked)} pages the playbook would need so every recommendation has somewhere to point. The most basic level first (L2, then L3, then L4).</p>'
        + _more(ranked, lambda i, pg: _page_card(P, i, pg), noun="pages")
        + _receipt([("From", 'The <span class="mono">missing_pages</span> of <span class="mono">coverage/playbook.json</span>, one list per discipline.'),
                    ("How ranked", "By the lowest level a page would close (L2 before L3 before L4), then by discipline; the file's own order breaks ties, "
                                   "so inside a discipline the most-needed page stays first. The same rule as the list in <span class=\"mono\">coverage/README.md</span>."),
                    ("Who decided", "An AI read proposed the pages and why they matter; code ordered them. Not checked by a person yet.")]))

    opp_block = (
        '<h3>Where the playbook teaches the opposite</h3>'
        '<p class="lead">Not gaps: places where a page says something the substrate rules out. Fix these before linking the page as learning.</p>'
        + ("".join(_opposite_card(i, p) for i, p in enumerate(opposite)) or '<p class="empty">None found.</p>')
        + _receipt([("From", "The coverage note of each practice in <span class=\"mono\">coverage/playbook.json</span>."),
                    ("How", "Code searches each note for the words <span class=\"mono\">opposite, contradict, teaches instruction stacking, unsupervised, rules out, "
                            "the wrong, misleads</span> (the search <span class=\"mono\">tools/coverage.py</span> runs) and <span class=\"mono\">rejects</span> "
                            "(one note says it that way). A note worded differently is missed."),
                    ("Who decided", "The AI read wrote the notes; code only picked them. Not checked by a person yet.")]))

    pit_rows = "".join(
        f'<tr><td class="n">{_esc(pf.get("order", 99))}</td><td class="t">{_esc(pf["title"])}</td>'
        f'<td>{_esc(c["name"])} <span class="dim">· {"team" if pf.get("audience") == "team" else _esc(c["discipline"])}</span></td></tr>'
        for c, pf in pitfall_pairs)
    card_rows = "".join(
        f'<tr><td class="n">{_esc(pc.get("order", 99))}</td><td class="t">{_esc(pc["title"])}</td>'
        f'<td>{_esc(p["name"])} <span class="dim">· {_esc(p["discipline"])}</span></td></tr>' for p, pc in practice_pairs)
    built_block = (
        '<h3>Built from the substrate</h3>'
        '<p class="lead">Two public playbook pages are generated, not written by hand. To change them, change the cards in the substrate, then run the publisher.</p>'
        '<div class="cols"><div>'
        f'<h4>Common pitfalls <span class="cnt">{len(pitfall_pairs)} cards</span></h4>'
        '<p class="dim"><span class="mono">docs/reference/common-pitfalls.mdx</span></p>'
        '<table class="tbl"><thead><tr><th>#</th><th>Card title</th><th>Problem</th></tr></thead><tbody>' + pit_rows + '</tbody></table></div><div>'
        f'<h4>What works <span class="cnt">{len(practice_pairs)} cards</span></h4>'
        '<p class="dim">The second half of the same page</p>'
        '<table class="tbl"><thead><tr><th>#</th><th>Card title</th><th>Practice</th></tr></thead><tbody>' + card_rows + '</tbody></table></div></div>'
        '<p class="line" style="margin:24px 0 0">The vocabulary page (<span class="mono">docs/reference/vocabulary.mdx</span>) is built from every term that has a '
        '<span class="mono">page:</span> entry; see the Vocabulary view.</p>'
        + _receipt([("From", 'Challenges with <span class="mono">pitfall:</span> and practices with <span class="mono">practice_card:</span> in '
                             '<span class="mono">labs/v15/substrate/*.yaml</span>.'),
                    ("How", 'Code, the way <span class="mono">labs/v15/tools/publish.py</span> does: a challenge holds one card or a list of cards, each becomes one card; '
                            'ordered by the card\'s <span class="mono">order</span> (99 when it has none). "team" cards are the ones marked for the team.'),
                    ("Generated", "<span class=\"mono\">common-pitfalls.mdx</span> and <span class=\"mono\">vocabulary.mdx</span> are written by <span class=\"mono\">publish.py</span>; "
                                  "do not edit them by hand."),
                    ("Not re-run", f"The coverage read above is from {READ_ON}; these cards changed after it.")]))

    return stale + f'<div class="sum">{top}</div>' + top_rc + taught_block + pages_block + opp_block + built_block
