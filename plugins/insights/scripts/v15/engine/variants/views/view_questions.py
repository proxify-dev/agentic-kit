"""Open questions and what doesn't add up in the v15 substrate, leading with what only he can decide.

Ported from the standalone substrate map (labs/v15/tools/map.py): its "What's missing or doesn't add up" checks
(analyse()["flags"]), its "Open questions" (each discipline's open_questions) and the "Open rulings" and "Next"
parts of STATUS.md. All of it comes from map.py's own analyse(); nothing is recomputed here.
"""
import html
import importlib.util
import os
import re

ORDER = 91
TITLE = "Open questions"
QUESTION = "What is waiting on a decision, and what in the substrate is missing or doesn't add up?"
PROXIFY_ONLY = True

MAP_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tools", "map.py"))
esc = lambda s: html.escape("" if s is None else str(s))  # noqa: E731

# The substrate's own opening words decide the kind (map.py question_kind). These three are his to settle.
HIS = ("Ruling needed", "Level guide conflict", "Playbook conflict")
KIND_WORD = {"Ruling needed": "Needs your ruling", "Level guide conflict": "The level guide disagrees",
             "Playbook conflict": "The playbook disagrees", "Content gap": "Content to write",
             "Other": "Drafter's note"}
DISC = {"context": "Context", "prompt": "Prompt", "verification": "Verification", "harness": "Harness",
        "orchestration": "Orchestration"}
SHOW = 4  # rows shown before "Show all N"

CSS = """
.v-questions{max-width:1000px}
.v-questions h3{font-size:18px;font-weight:600;line-height:1.35;margin:40px 0 4px}
.v-questions h3 span{font-size:14px;font-weight:400;color:#667085;margin-left:8px}
.v-questions .gq{color:#475467;margin:0 0 12px;max-width:72ch}
.v-questions .lead{color:#475467;margin:0 0 8px;max-width:72ch}
.v-questions .lead b{color:#15172F;font-weight:600}
.v-questions .sum{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:16px 0 0}
.v-questions .tile{background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;padding:16px;border-top:3px solid #D0D5DD}
.v-questions .tile.his{border-top-color:#5258FB}
.v-questions .tile b{display:block;font-size:24px;font-weight:600;line-height:1.2}
.v-questions .tile span{display:block;font-size:14px;font-weight:600;margin-top:4px}
.v-questions .tile em{display:block;font-style:normal;font-size:12px;color:#667085;margin-top:2px}
.v-questions details.rc{margin:8px 0 0}
.v-questions details.rc>summary{font-size:12px;font-weight:500;color:#3C3ED4;width:max-content}
.v-questions details.rc>summary:hover{text-decoration:underline}
.v-questions details.rc[open]>summary{margin-bottom:8px}
.v-questions dl{margin:0;display:grid;grid-template-columns:128px minmax(0,1fr);gap:6px 16px;font-size:14px;
  padding:12px 16px;background:#F9FAFB;border:1px solid #EAECF0;border-radius:8px}
.v-questions dt{font-size:12px;font-weight:500;color:#667085;padding-top:2px}
.v-questions dd{margin:0;color:#15172F;overflow-wrap:anywhere}
.v-questions code{font:inherit;font-size:12px;background:#F4F5F7;border-radius:4px;padding:0 4px;color:#344054}
.v-questions ol.list{list-style:none;margin:0;padding:0;border-top:1px solid #EAECF0}
.v-questions .row{padding:16px 0;border-bottom:1px solid #EAECF0}
.v-questions .tag{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;font-size:12px;color:#667085;margin:0 0 4px}
.v-questions .tag b{font-weight:600;color:#3C3ED4}
.v-questions .tag b.warn{color:#B54708}
.v-questions .row p{margin:0;max-width:80ch}
.v-questions details.q>summary{list-style:none;cursor:pointer;display:block}
.v-questions details.q>summary::-webkit-details-marker{display:none}
.v-questions details.q>summary .clamp{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.v-questions details.q[open]>summary .clamp{display:none}
.v-questions details.q>summary:hover .tag b{text-decoration:underline}
.v-questions details.q>.bodyin{margin-top:4px}
.v-questions .row p+p{margin-top:8px}
.v-questions .row ul{margin:0;padding-left:20px;max-width:80ch}
.v-questions .row.his{border-left:3px solid #5258FB;padding-left:16px;background:#F5F5FF;border-radius:0 8px 8px 0;margin-bottom:8px;border-bottom:0}
.v-questions .row.his+.row.his{margin-top:0}
.v-questions .flag{display:grid;grid-template-columns:56px minmax(0,1fr);gap:4px 16px}
.v-questions .flag .n{font-size:24px;font-weight:600;line-height:1.2;text-align:right;color:#B54708}
.v-questions .flag b.nm{display:block;font-weight:600}
.v-questions .flag p.why{color:#475467}
.v-questions .row ul.items{margin:0;padding:0;list-style:none;display:flex;flex-wrap:wrap;gap:4px 8px}
.v-questions .row ul.items li{font-size:12px;border:1px solid #EAECF0;background:#FFFFFF;border-radius:999px;padding:2px 10px}
.v-questions details.more{margin-top:8px}
.v-questions details.more>summary{font-size:12px;font-weight:500;color:#3C3ED4;padding:8px 0;width:max-content}
.v-questions details.more>summary:hover{text-decoration:underline}
.v-questions .clear{color:#475467;margin:16px 0 0;max-width:80ch}
.v-questions .clear .w{font-weight:600;color:#039855}
@media (max-width:760px){.v-questions .sum{grid-template-columns:1fr 1fr}.v-questions dl{grid-template-columns:1fr}
  .v-questions dt{padding-top:6px}.v-questions .flag{grid-template-columns:1fr}.v-questions .flag .n{text-align:left}}
"""


def _map(ctx):
    """map.py imported by path, analyse() run once and cached on ctx (the crosswalk view shares it)."""
    if "_v15_map" not in ctx:
        try:
            spec = importlib.util.spec_from_file_location("v15_map", MAP_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            ctx["_v15_map"] = (mod, mod.analyse(), None)
        except Exception as e:  # missing file, or PyYAML not installed
            ctx["_v15_map"] = (None, None, f"{type(e).__name__}: {e}")
    return ctx["_v15_map"]


def _names(data):
    """id -> plain name, for practices and challenges, so prose says 'Prove it works', not 'prove-it-works'."""
    out = {i: p.get("name") or i for i, p in data["practices"].items()}
    out.update({i: c.get("name") or i for i, c in data["challenges"].items()})
    return out


def _say(text, names, rx):
    """Escape a substrate sentence and swap each practice or challenge id for its name (quoted)."""
    return rx.sub(lambda m: f"‘{names[m.group(0)]}’", esc(text)) if rx else esc(text)


def _md(s):
    """STATUS.md prose -> html: **bold**, `code`, leading '- ' bullets as a list."""
    def one(t):
        return re.sub(r"`(.+?)`", r"<code>\1</code>", re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(re.sub(r"\s+", " ", t).strip())))
    lines = (s or "").splitlines()
    bullets = [re.sub(r"^\s*-\s+", "", ln) for ln in lines if ln.lstrip().startswith("- ")]
    if bullets and len(bullets) == len([ln for ln in lines if ln.strip()]):
        return "<ul>" + "".join(f"<li>{one(b)}</li>" for b in bullets) + "</ul>"
    return "".join(f"<p>{one(p)}</p>" for p in re.split(r"\n\s*\n", s or "") if p.strip())


def _dl(rows):
    return "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)


def _rc(rows, label="Where this comes from"):
    return f'<details class="rc"><summary>{label}</summary><dl>{_dl(rows)}</dl></details>'


LEAD = re.compile(r"^(Ruling needed|Level guide conflict|Playbook conflict|Content gap)\s*:\s*")


def _question(q, names, rx, his=False):
    kind = q.get("kind") or "Other"
    text = LEAD.sub("", q.get("text") or "")  # the tag already says the kind
    text = text[:1].upper() + text[1:]
    src = f'<code>substrate/{esc(q.get("discipline"))}.yaml</code> (open_questions)'
    rows = [("Source", f"{src}, written by the drafting agent when it could not settle the point from your rulings."),
            ("How it was sorted", f'The kind "{esc(KIND_WORD.get(kind, kind))}" comes from the sentence\'s opening words '
                                  f'(map.py question_kind): code, no AI read.'),
            ("Applied?", "Nothing here is applied to the substrate until it is decided."),
            ("Checked by a person", "not checked by a person yet")]
    return (f'<li class="row{" his" if his else ""}"><details class="q"><summary><div class="tag"><b class="{"" if his else "warn"}">'
            f'{esc(KIND_WORD.get(kind, kind))}</b><span>{esc(DISC.get(q.get("discipline"), q.get("discipline")))}</span></div>'
            f'<p class="clamp">{_say(text, names, rx)}</p></summary><div class="bodyin"><p>{_say(text, names, rx)}</p>{_rc(rows)}</div></details></li>')


def _list(items, show=SHOW, label="Show all"):
    """Top few, then the rest inline behind 'Show all N'."""
    head, rest = items[:show], items[show:]
    out = f'<ol class="list">{"".join(head)}</ol>' if head else ""
    if rest:
        out += (f'<details class="more"><summary>{label} {len(items)}</summary>'
                f'<ol class="list">{"".join(rest)}</ol></details>')
    return out


def _flag(f, names, rx):
    items = f.get("items") or []

    def label(x):
        a, _, b = x.partition(" → ")
        if b:
            return f"{_say(a, names, rx)} → {_say(b, names, rx)}"
        first, _, tail = x.partition(" ")
        return _say(first, names, rx) + (f" {esc(tail)}" if tail and tail[:1] == "L" else (" " + esc(tail) if tail else ""))
    chips = "".join(f'<li title="{esc(x)}">{label(x)}</li>' for x in items)
    rows = [("The rule", esc(f.get("why"))),
            ("How it was counted", "A check run on the substrate files every time this page is built (map.py, analyse()). "
                                   f"It lists every item the rule catches: {len(items)} today. Code decides, no AI read."),
            ("Source", "<code>substrate/*.yaml</code>, <code>coverage/playbook.json</code>, <code>coverage/tooling.yaml</code> "
                       "and the v13 practice list"),
            ("Checked by a person", "not checked by a person yet"),
            (f"The {len(items)} items", f'<ul class="items">{chips}</ul>')]
    return (f'<li class="row flag"><div class="n">{len(items)}</div><div><b class="nm">{esc(f.get("title"))}</b>'
            f'<p class="why">{esc(f.get("why"))}</p>{_rc(rows, "Which ones, and how this was counted")}</div></li>')


def render(ctx):
    mod, data, why = _map(ctx)
    if mod is None:
        return f'<p class="empty">not in this run: the substrate map could not be loaded ({esc(why)}).</p>'
    names = _names(data)
    ids = sorted(names, key=len, reverse=True)
    rx = re.compile(r"(?<![\w-])(" + "|".join(re.escape(i) for i in ids) + r")(?![\w-])") if ids else None
    qs = data.get("questions") or []
    his_qs = [q for q in qs if q.get("kind") in HIS]
    rest_qs = [q for q in qs if q.get("kind") not in HIS]
    gaps = [q for q in rest_qs if q.get("kind") == "Content gap"]
    notes = [q for q in rest_qs if q.get("kind") != "Content gap"]
    status = (data.get("status") or {}).get("sections") or {}
    rulings = next((v for k, v in status.items() if k.startswith("Open rulings")), "")
    nxt = next((v for k, v in status.items() if k.startswith("Next")), "")
    flags = data.get("flags") or []
    hit = [f for f in flags if f.get("items")]
    clear = [f for f in flags if not f.get("items")]
    n_rulings = len([ln for ln in rulings.splitlines() if ln.lstrip().startswith("- ")]) or (1 if rulings else 0)

    out = [f'<p class="lead">Only you can settle <b>{n_rulings + len(his_qs)}</b> of these: <b>{n_rulings}</b> rulings in '
           f'STATUS.md and <b>{len(his_qs)}</b> points the substrate drafts could not settle from your earlier rulings. '
           f'Nothing is applied until you decide.</p>'
           f'<div class="sum"><div class="tile his"><b>{n_rulings}</b><span>Rulings waiting on you</span><em>open in STATUS.md</em></div>'
           f'<div class="tile his"><b>{len(his_qs)}</b><span>Points for you to settle</span><em>rulings and conflicts in the substrate</em></div>'
           f'<div class="tile"><b>{len(hit)}</b><span>Checks that found something</span><em>of {len(flags)} run on the substrate</em></div>'
           f'<div class="tile"><b>{len(notes) + len(gaps)}</b><span>Notes and gaps</span><em>for the drafters, no ruling needed</em></div></div>']

    # 1. what only he can decide
    out.append('<h3>Only you can decide<span>first the rulings in STATUS.md, then the substrate\'s own open points</span></h3>')
    if rulings or nxt:
        cards = ""
        src = ("<code>STATUS.md</code>, the \"Open rulings\" and \"Next\" sections, read when this page is built (map.py read_status). "
               "Written by whoever last updated STATUS.md.")
        if rulings:
            cards += (f'<li class="row his"><details class="q"><summary><div class="tag"><b>Open rulings (yours)</b><span>STATUS.md</span></div>'
                      f'<p class="clamp">{esc(" · ".join(re.sub(r"^\\s*-\\s+", "", ln) for ln in rulings.splitlines() if ln.lstrip().startswith("- ")))}</p></summary>'
                      f'<div class="bodyin">{_md(rulings)}{_rc([("Source", src), ("Checked by a person", "not checked by a person yet")])}</div></details></li>')
        if nxt:
            cards += (f'<li class="row his"><details class="q"><summary><div class="tag"><b>Next step, proposed and waiting on your yes</b><span>STATUS.md</span></div>'
                      f'<p class="clamp">{esc(re.sub(r"[*`]", "", re.sub(r"\s+", " ", nxt)).strip())}</p></summary>'
                      f'<div class="bodyin">{_md(nxt)}{_rc([("Source", src), ("Checked by a person", "not checked by a person yet")])}</div></details></li>')
        out.append(f'<ol class="list" style="border:0">{cards}</ol>')
    else:
        out.append('<p class="empty">not in this run: STATUS.md has no open-rulings section.</p>')
    if his_qs:
        out.append(f'<p class="gq" style="margin-top:16px">{len(his_qs)} points the drafts could not settle: rulings to make, and places where '
                   f'the level guide or the playbook contradicts what you ruled.</p>'
                   + _list([_question(q, names, rx, his=True) for q in his_qs]))
    else:
        out.append('<p class="empty">No substrate question is waiting on a ruling.</p>')

    # 2. what is missing or does not add up
    out.append('<h3>What\'s missing or doesn\'t add up<span>checks run on the substrate itself</span></h3>'
               '<p class="gq">Each line is a rule the substrate should meet, and how many items break it. '
               'Open one to see which items.</p>')
    if flags:
        out.append(f'<ol class="list">{"".join(_flag(f, names, rx) for f in hit)}</ol>' if hit else "")
        if clear:
            out.append('<p class="clear"><span class="w">Nothing found</span> by '
                       + ", ".join(esc(f["title"].lower()) for f in clear) + ".</p>")
    else:
        out.append('<p class="empty">not in this run: no checks were produced.</p>')

    # 3. notes and gaps, no ruling needed
    if notes or gaps:
        out.append(f'<h3>Notes and gaps for the drafters<span>{len(notes) + len(gaps)}, none needs a ruling from you</span></h3>'
                   '<p class="gq">Laptop gaps, link and placement checks, and content still to write.</p>'
                   + _list([_question(q, names, rx) for q in gaps + notes]))

    # 4. the checker's edits
    checks = data.get("checks") or []
    if checks:
        rows = "".join(
            f'<li class="row"><div class="tag"><b>{esc(DISC.get(c.get("discipline"), c.get("discipline")))}</b></div>'
            f'<p>{_say(c.get("text"), names, rx)}</p></li>' for c in checks)
        out.append(f'<h3>What the checker changed in each draft<span>{len(checks)}</span></h3>'
                   '<p class="gq">Edits made after the drafts, listed in each discipline\'s <code>checker_changes</code> '
                   '(<code>substrate/&lt;discipline&gt;.yaml</code>). Not checked by a person yet.</p>'
                   f'<details class="more"><summary>Show all {len(checks)}</summary><ol class="list">{rows}</ol></details>')
    return "".join(out)
