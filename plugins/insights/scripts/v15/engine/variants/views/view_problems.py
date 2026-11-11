"""Problems, precisely: for each known agent problem, what we measured, the measure it was held to, and the raw numbers.
Source: cheap-problems.json (rows) joined with the names in v15-result.json."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import receipt_kit as K  # noqa: E402
import card_kit as C  # noqa: E402
import check_kit  # noqa: E402

ORDER = 25
TITLE = "Problems, precisely"
QUESTION = "Known agent problems: which ones show up in your logs, and how often."
PROXIFY_ONLY = True

CSS = K.css("problems") + """
.v-problems .sum{display:flex;flex-wrap:wrap;gap:4px 24px;margin:0 0 8px;color:#475467;font-size:14px}
.v-problems .sum b{font-size:28px;font-weight:600;line-height:1;color:#15172F;margin-right:6px;letter-spacing:-.01em}
.v-problems .sum span{display:flex;align-items:baseline}
.v-problems .kboard{grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.v-problems .kc-line{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:6px 0 0}
.v-problems .kc-t{font-weight:500}
.v-problems details.more>summary{cursor:pointer;color:#3C3ED4;font-size:12px;font-weight:500;padding:6px 2px}
.v-problems details.more[open]>summary{margin-bottom:6px}
.v-problems details.more>.kc{margin-bottom:6px}
.v-problems .sec .src-line{margin:8px 0 4px}
.v-problems .sec .src-line:first-of-type{margin-top:0}
"""

# group key -> (heading, pill class, word, one-line meaning, open by default)
_GROUPS = [
    ("shows", "Shows", "shows", "Shows", "The measure was met: this problem is in your work.", True),
    ("cant", "We can't tell from your logs", "unk", "We can't tell from your logs",
     "A measure ran but could not settle it either way. This is a gap in what we read, not a finding about you.", True),
    ("nomeasure", "No measure yet", "unk", "We can't tell from your logs",
     "Nothing is measured for these today: either no laptop source exists for them, or the sources exist but no bar says when the problem shows.", False),
    ("no", "Doesn't show", "ok", "Doesn't show", "The measure was run and the problem is not in your work.", False),
]


def _group(r):
    rule = r.get("rule") or ""
    if r.get("state") == "not read" or not r.get("shows") or rule.startswith(("no probe and no bar", "no laptop source")):
        return "nomeasure"
    if r.get("shows") == "can't tell":
        return "cant"
    return "shows" if r["shows"] == "shows" else "no"


def _evidence(r, why):
    rows = r.get("evidence") or []
    if not rows:
        return '<p class="missing">No numbers were read: nothing on this laptop covers this problem.</p>'
    out = []
    for e in rows:
        det = (e.get("detail") or "").strip()
        out.append(f'<div class="src-line"><span class="lab">{K.esc(K.source_label(e.get("source")))}</span></div>')
        if det and (det[:60] in why or why[:60] in det):
            out.append('<p class="dim">The figures are the ones in the sentence above.</p>')
        else:
            out.append(K.render_raw(det))
    return "".join(out)


def detail(r, meta, clean):
    """How a verdict was reached, for the problem panel (Proxify's copy): the sentence, the check, the raw numbers."""
    why = clean(r.get("why")) or K.plain(r.get("why"))
    code = r.get("derivation") == "code"
    how = ("<p><b>Plain code</b> decided this: a counted measure over your logs and files, no AI read.</p>" if code else
           "<p><b>An AI read</b> decided this.</p>")
    return (f'<div class="v-problems"><p>{K.esc(why)}</p>{check_kit.block(r)}<div class="sec" style="margin-top:16px">{how}'
            + K.fold("The raw numbers the check stored", _evidence(r, why)) + "</div></div>")


def _card(r, meta, clean, grp):
    _, _, cls, word, _, _ = next(g for g in _GROUPS if g[0] == grp)
    name = clean(meta.get("name")) or r["id"].replace("-", " ").capitalize()
    why = clean(r.get("why")) or K.plain(r.get("why"))
    icon = {"shows": "▲", "ok": "✓"}.get(cls, "?")
    state = C.state(icon, word, {"shows": "warn", "ok": "good"}.get(cls, ""))
    return C.card(name, state, f'<div class="kc-line" title="{K.esc(why)}">{K.esc(why)}</div>' + ("" if cls == "shows" else C.log_line()), pk=f"c:{r['id']}")


def render(ctx):
    rows = (ctx.get("cheap_problems") or {}).get("rows")
    res = ctx.get("v15_result")
    if not rows:
        return '<p class="empty">Not in this run: cheap-problems.json is missing.</p>'
    clean = ctx["clean"]
    meta, area = {}, {}
    for d in (res or {}).get("disciplines", []):
        for c in d.get("challenges", []):
            meta[c["id"]] = c
            area[c["id"]] = d["name"]
    by = {g[0]: [] for g in _GROUPS}
    for r in rows:
        if r.get("kind") != "challenge":
            continue
        r = dict(r, area=area.get(r["id"], r.get("discipline", "")))
        by[_group(r)].append(r)
    shown = 8  # the long groups show their first cards; the rest open on a click
    parts, nums = [], []
    for key, head, _, _, meaning, openn in _GROUPS:
        rs = by[key]
        if not rs:
            continue
        nums.append(f'<span><b>{len(rs)}</b>{K.esc(head.lower() if key != "shows" else "show")}</span>')
        cards = [_card(r, meta.get(r["id"], {}), clean, key) for r in rs]
        body = "".join(cards[:shown]) + (f'<details class="more"><summary>Show the other {len(cards) - shown}</summary>{"".join(cards[shown:])}</details>'
                                         if len(cards) > shown else "")
        parts.append(C.group(head, f"{len(rs)}", body, sub=K.esc(meaning)))
    return f'<p class="sum">{"".join(nums)}</p><div class="kboard">{"".join(parts)}</div>'
