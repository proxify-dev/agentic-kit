"""What the tool can do today, job by job, and where v15 stands.

Ported from the standalone substrate map (labs/v15/tools/map.py): its "What can the tool do today?" section
(coverage/capabilities.yaml) and its "Where it stands" section (the layer table in STATUS.md). The data is read by
calling map.py's own loaders (load_capabilities, read_status, load_live), never recomputed here.
"""
import html
import importlib.util
import os
import re

ORDER = 90
TITLE = "What the tool can do"
QUESTION = "What the tool can do today: proven, still on paper, or not built."
PROXIFY_ONLY = True

MAP_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tools", "map.py"))
esc = lambda s: html.escape("" if s is None else str(s))  # noqa: E731

# state -> (word, what it means). Colour comes only from the CSS below and always sits beside the word.
STATE = {"proven": ("Proven", "ran on real input and passed an independent check"),
         "ran": ("Ran, not checked", "ran on real input; no independent check yet"),
         "paper": ("Built, never run", "exists, never used on real input"),
         "missing": ("Not built", "nothing yet")}
LAYER_WORD = {"good": "Holds", "warn": "Holds in part", "bad": "Doesn't work yet"}

CSS = """
.v-capabilities{max-width:1000px}
.v-capabilities h3{font-size:18px;font-weight:600;line-height:1.35;margin:40px 0 4px}
.v-capabilities .gq{color:#475467;margin:0 0 12px}
.v-capabilities .sum{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:0 0 16px}
.v-capabilities .tile{background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;padding:16px;border-top:3px solid #D0D5DD}
.v-capabilities .tile.proven{border-top-color:#039855}
.v-capabilities .tile.ran{border-top-color:#DC6803}
.v-capabilities .tile.paper{border-top-color:#999AFD}
.v-capabilities .tile b{display:block;font-size:24px;font-weight:600;line-height:1.2}
.v-capabilities .tile span{display:block;font-size:14px;font-weight:600;margin-top:4px}
.v-capabilities .tile em{display:block;font-style:normal;font-size:12px;color:#667085;margin-top:2px}
.v-capabilities .bar{display:flex;height:8px;border-radius:4px;overflow:hidden;background:#F4F5F7;margin:0 0 12px}
.v-capabilities .bar i{display:block}
.v-capabilities .bar .proven{background:#039855}.v-capabilities .bar .ran{background:#DC6803}
.v-capabilities .bar .paper{background:#999AFD}.v-capabilities .bar .missing{background:#D0D5DD}
.v-capabilities .lead{color:#475467;margin:0 0 8px;max-width:72ch}
.v-capabilities details.rc{margin:8px 0 0}
.v-capabilities details.rc>summary{font-size:12px;font-weight:500;color:#3C3ED4;width:max-content}
.v-capabilities details.rc>summary:hover{text-decoration:underline}
.v-capabilities details.rc[open]>summary{margin-bottom:8px}
.v-capabilities dl{margin:0;display:grid;grid-template-columns:128px minmax(0,1fr);gap:6px 16px;font-size:14px;
  padding:12px 16px;background:#F9FAFB;border:1px solid #EAECF0;border-radius:8px}
.v-capabilities dt{font-size:12px;font-weight:500;color:#667085;padding-top:2px}
.v-capabilities dd{margin:0;color:#15172F;overflow-wrap:anywhere}
.v-capabilities dd code,.v-capabilities .row code{font:inherit;font-size:12px;background:#F4F5F7;border-radius:4px;padding:0 4px;color:#344054}
.v-capabilities ol.jobs{list-style:none;margin:0;padding:0;border-top:1px solid #EAECF0}
.v-capabilities .row{border-bottom:1px solid #EAECF0}
.v-capabilities .row>details>summary{display:grid;grid-template-columns:152px minmax(0,1fr);gap:4px 24px;padding:14px 0;list-style:none;cursor:pointer}
.v-capabilities .row>details>summary::-webkit-details-marker{display:none}
.v-capabilities .row>details>summary:hover .nm{color:#3C3ED4}
.v-capabilities .row .clamp{margin:2px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#475467}
.v-capabilities .row details[open] .clamp{display:none}
.v-capabilities .row .jbody{padding:0 0 16px 176px}
@media (max-width:700px){.v-capabilities .row>details>summary{grid-template-columns:1fr}.v-capabilities .row .jbody{padding-left:0}}
.v-capabilities .st{font-size:12px;font-weight:600;display:flex;gap:8px;align-items:flex-start;line-height:1.5;padding-top:2px}
.v-capabilities .st::before{content:"";flex:none;width:8px;height:8px;border-radius:50%;margin-top:6px;background:#D0D5DD}
.v-capabilities .st.proven{color:#039855}.v-capabilities .st.proven::before{background:#039855}
.v-capabilities .st.ran{color:#B54708}.v-capabilities .st.ran::before{background:#DC6803}
.v-capabilities .st.paper{color:#3C3ED4}.v-capabilities .st.paper::before{background:#999AFD}
.v-capabilities .st.missing{color:#475467}
.v-capabilities .row b.nm{display:block;font-weight:600}
.v-capabilities .row p{margin:2px 0 0}
.v-capabilities .row p.gap{color:#475467}
.v-capabilities .row p.gap span{font-size:12px;font-weight:500;color:#667085;margin-right:8px}
.v-capabilities .layer .st.good{color:#039855}.v-capabilities .layer .st.good::before{background:#039855}
.v-capabilities .layer .st.warn{color:#B54708}.v-capabilities .layer .st.warn::before{background:#DC6803}
.v-capabilities .layer .st.bad{color:#475467}.v-capabilities .layer .st.bad::before{background:#272594}
.v-capabilities .w{font-weight:600}.v-capabilities .w.ok{color:#039855}.v-capabilities .w.warn{color:#B54708}.v-capabilities .w.bad{color:#3C3ED4}
@media (max-width:760px){.v-capabilities .sum{grid-template-columns:1fr 1fr}.v-capabilities .row{grid-template-columns:1fr}
  .v-capabilities dl{grid-template-columns:1fr}.v-capabilities dt{padding-top:6px}}
"""


def _map(ctx):
    """map.py imported by path (it reads labs/v15 relative to its own file). Cached on ctx; (None, why) if it can't load."""
    if "_v15_map_mod" not in ctx:
        try:
            spec = importlib.util.spec_from_file_location("v15_map", MAP_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            ctx["_v15_map_mod"] = (mod, None)
        except Exception as e:  # missing file, or PyYAML not installed
            ctx["_v15_map_mod"] = (None, f"{type(e).__name__}: {e}")
    return ctx["_v15_map_mod"]


def _inline(s):
    """STATUS.md cell -> html: **bold**, `code`, and the three status marks turned into words."""
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(s)))
    for mark, cls, word in (("✅", "ok", "Holds:"), ("⚠️", "warn", "Open:"), ("⚠", "warn", "Open:"),
                            ("❌", "bad", "Missing:")):
        s = s.replace(mark, f'<span class="w {cls}">{word}</span>')
    return re.sub(r"\s+", " ", s).strip()


def _plain(s):
    return re.sub(r"[*`]", "", re.sub(r"\s+", " ", str(s or ""))).strip()


def _live_lines(live):
    """The 'Live' lines map.py shows for capabilities that name one (tally, states, pointer, rungs, recommendations)."""
    if not live:
        return {}
    tally = list((live.get("tally") or {}).values())
    pt = live.get("pointer") or {}
    recs = live.get("recommendations") or []
    total = lambda f: sum(f(t) or 0 for t in tally)  # noqa: E731
    sk = lambda k: total(lambda t: (t.get("skeptic") or {}).get(k))  # noqa: E731
    out = {}
    if tally:
        out["tally"] = (f"{total(lambda t: t.get('reader_rows'))} practice and challenge rows; the second AI fixed {sk('fixed')}, "
                        f"kept {sk('kept')} as written, threw out {sk('killed')}")
        out["states"] = (f"{total(lambda t: t.get('practices_seen'))} practices seen, "
                         f"{total(lambda t: t.get('practices_looked_not_found'))} looked for and not found, "
                         f"{total(lambda t: t.get('practices_not_read'))} never looked at")
    if pt.get("level"):
        could = f", could be {pt['could_be']}" if pt.get("could_be") else ""
        out["pointer"] = f"reads this laptop as {pt['level']}{could}"
        out["rungs"] = "every practice with its level is listed in the Level read view"
    if recs:
        r = recs[0]
        out["recommendations"] = (f"{len(recs)} shown and {live.get('more') or 0} more; first: "
                                  f"{r.get('challenge_name')} -> {r.get('practice_name')}")
    return out


def _job(c, live_lines, live, checked):
    state = c.get("state") or "missing"
    word = STATE.get(state, (state, ""))[0]
    gap = c.get("missing")
    rows = []
    if c.get("proof") and c["proof"] != "—":
        rows.append(("Proof", esc(c["proof"])))
    else:
        rows.append(("Proof", "None written down yet."))
    if c.get("live") and live_lines.get(c["live"]):
        rows.append(("Live now", f'{esc(live_lines[c["live"]])} (read from the newest evidence run, '
                                 f'{esc(live.get("run"))}, built {esc(live.get("built"))})'))
    if c.get("where") and c["where"] != "—":
        rows.append(("Where it lives", f'<code>{esc(c["where"])}</code>'))
    rows.append(("How the state was set", f'By hand, in <code>coverage/capabilities.yaml</code> (last checked {esc(checked or "?")}): '
                 f'"{esc(word)}" means it {esc(STATE.get(state, ("", "?"))[1])}. A person or an agent wrote it; code did not compute it.'))
    rows.append(("Checked by a person",
                 "An independent check passed (see the proof)." if state == "proven" else "not checked by a person yet"))
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    return (f'<li class="row"><details><summary><div class="st {esc(state)}">{esc(word)}</div><div><b class="nm">{esc(c.get("name"))}</b>'
            f'<p class="clamp">{esc(c.get("today"))}</p></div></summary><div class="jbody">'
            f'<p>{esc(c.get("today"))}</p>'
            + (f'<p class="gap"><span>Still missing</span>{esc(gap)}</p>' if gap and state != "proven" else "")
            + f'<details class="rc"><summary>Proof and where this comes from</summary><dl>{dl}</dl></details></div></details></li>')


def _capabilities(mod, live):
    K = mod.load_capabilities() or {}
    caps = K.get("capabilities") or []
    if not caps:
        return '<p class="empty">not in this run: coverage/capabilities.yaml has no jobs listed.</p>'
    n = lambda s: sum(1 for c in caps if c.get("state") == s)  # noqa: E731
    tiles = "".join(f'<div class="tile {s}"><b>{n(s)}</b><span>{esc(w)}</span><em>{esc(m)}</em></div>' for s, (w, m) in STATE.items())
    bar = "".join(f'<i class="{s}" style="width:{100 * n(s) / len(caps):.2f}%" title="{esc(STATE[s][0])}: {n(s)}"></i>' for s in STATE)
    lines = _live_lines(live)
    groups = ""
    for g in K.get("groups") or []:
        cs = [c for c in caps if c.get("group") == g.get("id")]
        if not cs:
            continue
        groups += (f'<h3>{esc(g.get("name"))}</h3><p class="gq">{esc(g.get("question"))}</p><ol class="jobs">'
                   + "".join(_job(c, lines, live, K.get("checked")) for c in cs) + "</ol>")
    known = {g.get("id") for g in K.get("groups") or []}
    stray = [c for c in caps if c.get("group") not in known]
    if stray:  # a job whose group is not declared must still show
        groups += ('<h3>Other</h3><ol class="jobs">' + "".join(_job(c, lines, live, K.get("checked")) for c in stray) + "</ol>")
    receipt = (f'<details class="rc"><summary>How these counts were made</summary><dl>'
               f'<dt>Source</dt><dd><code>coverage/capabilities.yaml</code>, hand-kept, last checked {esc(K.get("checked") or "?")}. '
               f'Read through the substrate map\'s own loader.</dd>'
               f'<dt>How</dt><dd>Each job carries one state, set by hand. The counts are a plain tally of those states: code, no AI.</dd>'
               f'<dt>Strongest first</dt><dd>Proven: ran on real input and passed an independent check. Ran, not checked: ran on real '
               f'input (so far one laptop), no independent check. Built, never run: exists, never used on real input. Not built: nothing yet.</dd>'
               f'<dt>Checked by a person</dt><dd>not checked by a person yet, except where a job\'s proof says an independent check passed</dd>'
               f'</dl></details>')
    return (f'<p class="lead">Of the <b>{len(caps)}</b> jobs the tool has to do: <b>{n("proven")}</b> proven, '
            f'<b>{n("ran")}</b> ran for real but not checked, <b>{n("paper")}</b> built but never run for real, '
            f'<b>{n("missing")}</b> not built.</p><div class="sum">{tiles}</div><div class="bar">{bar}</div>{receipt}{groups}')


def _stands(mod):
    st = mod.read_status() or {}
    layers = st.get("layers") or []
    if not layers:
        return '<p class="empty">not in this run: no layer table found in STATUS.md.</p>'
    rows = ""
    for r in layers:
        state = r.get("state") if r.get("state") in LAYER_WORD else "warn"
        dl = (f'<dt>Files</dt><dd>{_inline(r.get("files"))}</dd>'
              f'<dt>How this was read</dt><dd>Straight from the row of the layer table in <code>STATUS.md</code>, by code. '
              f'The mark in the row sets the state: all holds = "Holds", any open point = "Holds in part", any missing = "Doesn\'t work yet". '
              f'The row itself was written by whoever last updated STATUS.md.</dd>'
              f'<dt>Checked by a person</dt><dd>not checked by a person yet</dd>')
        rows += (f'<li class="row layer"><div class="st {state}">{esc(LAYER_WORD[state])}</div><div>'
                 f'<b class="nm">{_inline(_plain(r.get("layer")))}</b><p>{_inline(r.get("holds"))}</p>'
                 f'<details class="rc"><summary>Where this comes from</summary><dl>{dl}</dl></details></div></li>')
    return (f'<h3>Where it stands</h3><p class="gq">Each layer of v15 and whether it holds, read from <code>STATUS.md</code> '
            f'every time this page is built.</p><ol class="jobs">{rows}</ol>')


def render(ctx):
    mod, why = _map(ctx)
    if mod is None:
        return f'<p class="empty">not in this run: the substrate map could not be loaded ({esc(why)}).</p>'
    try:
        live = mod.load_live() or {}
    except Exception:
        live = {}
    parts = []
    for build in (lambda: _capabilities(mod, live), lambda: _stands(mod)):
        try:
            parts.append(build())
        except Exception as e:  # one section failing must not hide the other
            parts.append(f'<p class="empty">not in this run: {esc(type(e).__name__)}: {esc(e)}</p>')
    return "".join(parts)
