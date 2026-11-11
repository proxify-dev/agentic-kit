"""Vocabulary: every named problem and move in labs/v15/substrate/vocabulary.yaml, by family, with its grade, whether it is
on the public vocabulary page, and which challenges and practices of the substrate point to it (their `vocab:` lists).
Reads the yaml directly and map.py's loader (imported by path) for the challenges and practices. Needs PyYAML."""
import html
import importlib.util
import os

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel_kit as PK  # noqa: E402  a term name opens the shared side panel

ORDER = 94
TITLE = "Vocabulary"
QUESTION = ("Which named problems and moves does the substrate know, how well sourced is each, and which parts of the "
            "substrate and the public page use them?")
PROXIFY_ONLY = True

LABS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))  # labs/v15
TOP = 5
GRADE_ORDER = {"standard": 0, "emerging": 1, "single-source": 2, "folk": 3}
GRADE_WORD = {"standard": "Standard", "emerging": "Emerging", "single-source": "Single source", "folk": "Folk"}
GRADE_MEANS = {"standard": "named by the field, several sources", "emerging": "named by the field, still settling",
               "single-source": "one source uses it", "folk": "said in practice, no written source"}
FAMILY_WORD = {"context": "Context", "prompt": "Prompt", "harness": "Harness", "loop": "Loop", "human": "Human"}
SRC = "labs/v15/substrate/vocabulary.yaml"

CSS = """
.v-vocabulary [hidden]{display:none!important}
.v-vocabulary .sum{display:flex;flex-wrap:wrap;gap:8px 32px;margin:0 0 8px;padding:16px 0;border-top:1px solid #EAECF0;border-bottom:1px solid #EAECF0}
.v-vocabulary .sum div{min-width:120px}.v-vocabulary .sum b{display:block;font-size:24px;font-weight:600;line-height:1.2}
.v-vocabulary .sum span{font-size:12px;color:#475467}
.v-vocabulary .grades{font-size:12px;color:#475467;margin:8px 0 0;max-width:100ch}
.v-vocabulary h3{font-size:18px;font-weight:600;margin:48px 0 4px}
.v-vocabulary h3 .cnt,.v-vocabulary h4 .cnt{font-weight:400;color:#667085;font-size:14px}
.v-vocabulary h4{font-size:14px;font-weight:600;margin:24px 0 4px}
.v-vocabulary .mono{font:400 12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}
.v-vocabulary .dim{color:#667085;font-size:12px}
.v-vocabulary .hd,.v-vocabulary .row>summary{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,1fr) minmax(0,1fr) minmax(0,2fr);gap:16px;align-items:start}
.v-vocabulary .hd{font-size:12px;font-weight:500;color:#667085;padding:0 0 8px 16px;border-bottom:1px solid #EAECF0}
.v-vocabulary .row{border-bottom:1px solid #EAECF0}
.v-vocabulary .row>summary{list-style:none;padding:10px 0 10px 16px;position:relative;cursor:pointer}
.v-vocabulary .row>summary::-webkit-details-marker,.v-vocabulary .more>summary::-webkit-details-marker,.v-vocabulary .rc>summary::-webkit-details-marker{display:none}
.v-vocabulary .row>summary::before{content:"";position:absolute;left:2px;top:17px;width:5px;height:5px;border-right:2px solid #667085;border-bottom:2px solid #667085;transform:rotate(-45deg);transition:transform .12s}
.v-vocabulary .row[open]>summary::before{transform:rotate(45deg);top:15px}
.v-vocabulary .row>summary:hover .nm{color:#3C3ED4}
.v-vocabulary .nm{font-weight:500;min-width:0}
.v-vocabulary .pill{display:inline-flex;align-items:center;font-size:12px;font-weight:500;padding:0 8px;border-radius:999px;border:1px solid #EAECF0;background:#F9FAFB;color:#475467;white-space:nowrap}
.v-vocabulary .pill.standard{border-color:#C4F3D8;background:#F6FEF9;color:#039855}
.v-vocabulary .pill.emerging{border-color:#D5D5FE;background:#F5F5FF;color:#272594}
.v-vocabulary .pill.single-source,.v-vocabulary .pill.folk{border-color:#FDF1D9;background:#FEFBF5;color:#B54708}
.v-vocabulary .pill.on{border-color:#D5D5FE;background:#F5F5FF;color:#272594}
.v-vocabulary .used{font-size:12px;color:#475467;min-width:0}
.v-vocabulary .used.none{color:#667085}
.v-vocabulary .body{padding:0 0 16px 16px;display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:16px 32px;max-width:110ch}
.v-vocabulary .body p{margin:0 0 8px}
.v-vocabulary .lab{display:block;font-size:12px;font-weight:600;color:#475467;margin:8px 0 2px}
.v-vocabulary .lab:first-child{margin-top:0}
.v-vocabulary .body ul{margin:0;padding:0;list-style:none}.v-vocabulary .body li{font-size:12px;margin:0 0 2px}
.v-vocabulary .more{margin:8px 0 0}
.v-vocabulary .more>summary{list-style:none;cursor:pointer;color:#3C3ED4;font-weight:500;width:max-content;padding:4px 0 4px 16px}
.v-vocabulary .more[open]>summary{margin-bottom:0}
.v-vocabulary .rc{margin:12px 0 0;font-size:12px}
.v-vocabulary .rc>summary{list-style:none;cursor:pointer;color:#3C3ED4;font-weight:500;width:max-content}
.v-vocabulary .rc>summary::before{content:"+ "}.v-vocabulary .rc[open]>summary::before{content:"- "}
.v-vocabulary .rc dl{margin:8px 0 0;display:grid;grid-template-columns:96px minmax(0,1fr);gap:4px 12px;padding:8px 12px;background:#F9FAFB;border:1px solid #EAECF0;border-radius:8px;max-width:100ch}
.v-vocabulary .rc dt{font-weight:600;color:#15172F}.v-vocabulary .rc dd{margin:0;color:#475467;overflow-wrap:anywhere}
@media (max-width:900px){.v-vocabulary .hd,.v-vocabulary .row>summary{grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:8px}
  .v-vocabulary .hd span:nth-child(n+3){display:none}.v-vocabulary .row>summary>.used{grid-column:1 / -1}
  .v-vocabulary .body{grid-template-columns:1fr}}
"""


def _esc(s):
    return html.escape("" if s is None else str(s))


def _receipt(rows):
    body = "".join(f"<dt>{_esc(k)}</dt><dd>{v}</dd>" for k, v in rows)
    return f'<details class="rc"><summary>How we know</summary><dl>{body}</dl></details>'


def _plural(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _users_line(u):
    ch, pr = u["challenges"], u["practices"]
    if not ch and not pr:
        return '<span class="used none">Nothing points to it</span>'
    parts = []
    if ch:
        parts.append(_plural(len(ch), "problem"))
    if pr:
        parts.append(_plural(len(pr), "practice"))
    return f'<span class="used">Used by {" and ".join(parts)}</span>'


def _row(t, users):
    u = users.get(t["id"], {"challenges": [], "practices": []})
    pages = t.get("page")
    if pages:
        labels = [x.get("label") for x in pages if isinstance(x, dict) and x.get("label")] if isinstance(pages, list) else []
        page_pill = '<span class="pill on">On public page</span>'
        page_line = ('Shown on the public page as: ' + ", ".join(_esc(x) for x in labels)) if labels else "Shown on the public page."
    else:
        page_pill = '<span class="pill">Not on page</span>'
        page_line = "Not on the public page."
    g = t.get("grade") or "folk"
    also = ", ".join(_esc(x) for x in t.get("also_called") or [])
    src = t.get("source")
    link = f'<a href="{_esc(src)}" target="_blank" rel="noopener">{_esc(src)}</a>' if src and str(src).startswith("http") else _esc(src or "no source")
    checked = "checked against the source" if t.get("checked_against_source") else "not checked against the source"
    ul = ""
    for label, key in (("Problems that point to it", "challenges"), ("Practices that point to it", "practices")):
        if u[key]:
            ul += f'<span class="lab">{label}</span><ul>' + "".join(f"<li>{_esc(n)} <span class=\"dim\">· {_esc(d)}</span></li>" for n, d in u[key]) + "</ul>"
    if not ul:
        ul = '<span class="lab">Who points to it</span><p class="dim">No problem or practice lists this term in its vocab.</p>'
    return (f'<details class="row"><summary><span class="nm">{PK.link("t", t["id"], t["term"])}</span>'
            f'<span><span class="pill {_esc(g)}">{_esc(GRADE_WORD.get(g, g))}</span></span><span>{page_pill}</span>{_users_line(u)}</summary>'
            f'<div class="body"><div><span class="lab">Meaning</span><p>{_esc(t.get("meaning"))}</p>'
            f'<span class="lab">Named by</span><p>{_esc(t.get("who"))}</p>'
            + (f'<span class="lab">Also called</span><p>{also}</p>' if also else "")
            + f'<span class="lab">Source</span><p>{link} <span class="dim">· {_esc(t.get("year") or "year unknown")} · {checked}</span></p></div>'
            f'<div>{ul}<span class="lab">Public page</span><p>{_esc(page_line)}</p></div></div></details>')


def _group(terms, users):
    ordered = sorted(terms, key=lambda t: (not (users.get(t["id"]) and (users[t["id"]]["challenges"] or users[t["id"]]["practices"])),
                                          GRADE_ORDER.get(t.get("grade"), 9)))
    head, rest = ordered[:TOP], ordered[TOP:]
    out = "".join(_row(t, users) for t in head)
    if rest:
        out += (f'<details class="more"><summary>Show all {len(ordered)}</summary>'
                + "".join(_row(t, users) for t in rest) + "</details>")
    return out


def render(ctx):
    try:
        import yaml
    except ImportError:
        return '<p class="empty">Not in this run: PyYAML is not installed. Build with <span class="mono">uv run --no-project --with pyyaml python frame.py</span>.</p>'
    path = os.path.join(LABS, "substrate", "vocabulary.yaml")
    try:
        terms = (yaml.safe_load(open(path)) or {}).get("terms") or []
    except (OSError, yaml.YAMLError):
        terms = []
    if not terms:
        return f'<p class="empty">Not in this run: {_esc(SRC)} is missing or has no terms.</p>'
    users = {}
    try:
        spec = importlib.util.spec_from_file_location("v15map", os.path.join(LABS, "tools", "map.py"))
        mp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mp)
        _, challenges, practices = mp.load()[:3]
        for key, items in (("challenges", challenges), ("practices", practices)):
            for it in items.values():
                for v in it.get("vocab") or []:
                    users.setdefault(v, {"challenges": [], "practices": []})[key].append((it["name"], it["discipline"]))
    except Exception:  # the term list still stands without who uses it
        pass

    n = len(terms)
    kinds = {k: sum(t.get("kind") == k for t in terms) for k in ("problem", "move")}
    grades = {g: sum(t.get("grade") == g for t in terms) for g in GRADE_ORDER}
    anchored = sum(1 for t in terms if users.get(t["id"]))
    allowed = grades["standard"] + grades["emerging"]
    on_page = sum(1 for t in terms if t.get("page"))
    top = "".join(f"<div><b>{a}</b><span>{_esc(b)}</span></div>" for a, b in [
        (n, f'terms: {kinds["problem"]} problems, {kinds["move"]} moves'),
        (anchored, "anchor the substrate (a problem or practice points to them)"),
        (allowed, "are standard or emerging, so allowed to anchor"),
        (on_page, "are on the public page")])
    grade_line = " · ".join(f'<b>{grades[g]}</b> {GRADE_WORD[g].lower()} ({GRADE_MEANS[g]})' for g in GRADE_ORDER)

    families = []
    for t in terms:
        if t.get("family") not in families:
            families.append(t.get("family"))
    families.sort(key=lambda f: -sum(t.get("family") == f for t in terms))
    body = ""
    for f in families:
        ft = [t for t in terms if t.get("family") == f]
        body += f'<h3>{_esc(FAMILY_WORD.get(f, f))} <span class="cnt">{len(ft)} terms</span></h3>'
        for kind, word in (("problem", "Problems"), ("move", "Moves")):
            kt = [t for t in ft if t.get("kind") == kind]
            if not kt:
                continue
            sub = "things that go wrong" if kind == "problem" else "what a person does about it"
            body += (f'<h4>{word} <span class="cnt">{len(kt)} · {sub}</span></h4>'
                     '<div class="hd"><span>Term</span><span>Grade</span><span>Public page</span><span>Used by</span></div>'
                     + _group(kt, users))

    receipt = _receipt([
        ("From", f'<span class="mono">{SRC}</span> (the <span class="mono">terms</span> list). The problems and practices come from '
                 '<span class="mono">labs/v15/substrate/*.yaml</span>, read through <span class="mono">labs/v15/tools/map.py</span>.'),
        ("Source link", "Each term's own source link is in its open row (Source). The public page "
                        '<span class="mono">docs/reference/vocabulary.mdx</span> is generated from the terms that have a '
                        '<span class="mono">page:</span> entry by <span class="mono">labs/v15/tools/publish.py</span>.'),
        ("How", "Code counted. A term anchors the substrate when at least one problem or practice lists its id under <span class=\"mono\">vocab:</span>. "
                "Within a family, terms something points to come first, then by grade (standard, emerging, single source, folk). "
                f"The first {TOP} of each list show; the rest open under Show all."),
        ("Grade", "Set by whoever imported the term (10-26 and 10-27 research, imported 2026-11-03), from how many sources use it. "
                  "The 11-02 rule: only standard or emerging terms may anchor a problem or practice."),
        ("Who decided", "Code only. The grades are a person's or an AI read's judgement from the import; not checked by a person yet."),
    ])
    return f'<div class="sum">{top}</div><p class="grades">{grade_line}</p>' + receipt + body
