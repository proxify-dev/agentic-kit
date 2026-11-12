"""The Substrate section (11-03: "Include the full substrate in this doc... that's how we'll know whether the
substrate needs a fix or not... If something's missing from the substrate or something's wrong in the substrate I
should be able to tell."). Not a view: frame.py only loads view_*.py. Used by view_sub_board.py and the discipline pages (where each element's
chain breaks) and the five discipline pages view_sub_<discipline>.py (the full substrate text + this laptop's run).

Sources, all read relative to this file (labs/v15 = three folders up from here):
  engine/substrate.json     the substrate (compiled from substrate/<discipline>.yaml, which is what to edit)
  coverage/tooling.yaml     an AI audit of each practice's detector, made on one laptop, NOT person-checked
  coverage/playbook.json    what the playbook teaches per practice rung and per challenge
  ctx["cheap_problems"]     this laptop's run, per challenge        ctx["v15_result"]["detectors"]  per practice

THE CHAIN. An element is only useful if every link holds. A link is ok / part (works, with a caveat) / bad / na
(does not apply). The FIRST bad link, in the order below, says who has to fix it:
  substrate = the definition or the laptop signs  tooling = the check, the read, the audit, the agreement
  data      = what the run kept                   teaching  = the playbook
Challenges:  Defined . Laptop signs . Check . Agrees . Moments . Taught
Practices:   Defined . Laptop signs . Read . Audit . Lines kept . Taught
(the page's column order puts Check beside Data and Agrees; the break order is the one above).
"""
import html
import json
import os
import re

try:
    import yaml
except ImportError:  # the page still builds: the audit column says it is not loaded
    yaml = None

HERE = os.path.dirname(os.path.abspath(__file__))
LABS = os.path.normpath(os.path.join(HERE, "..", "..", ".."))  # labs/v15
PLAYBOOK = "https://agentic.proxify.io"
esc = lambda s: html.escape("" if s is None else str(s))  # noqa: E731

SHORT = {"context": "Context", "prompt": "Prompt", "verification": "Verification", "harness": "Harness",
         "orchestration": "Orchestration"}
OWNER = {"defined": "substrate", "signs": "substrate", "check": "tooling", "agrees": "tooling", "read": "tooling",
         "audit": "tooling", "moments": "data", "lines": "data", "taught": "teaching"}
OWNER_WORD = {"substrate": "the substrate", "tooling": "the tooling", "data": "the data", "teaching": "the teaching"}
CH_LINKS = [("defined", "Defined"), ("signs", "Laptop signs"), ("check", "Check"), ("agrees", "Agrees"),
            ("moments", "Moments"), ("taught", "Taught")]
PR_LINKS = [("defined", "Defined"), ("signs", "Laptop signs"), ("read", "Read"), ("audit", "Audit"),
            ("lines", "Lines kept"), ("taught", "Taught")]
KIND_WORD = {"counts": "Counts from your logs", "words": "Word search in what you typed", "hand read": "One-off hand read",
             "files": "Read from your files", "git": "Read from git", "settings": "Read from settings",
             "tool calls": "Read from tool calls"}
FRAGILE = {"words", "hand read"}
_CACHE = {}


# --- loading --------------------------------------------------------------------------------------------------

def load():
    """-> {"sub", "tooling" (None if not loaded), "playbook": {discipline: record}, "tooling_why"}."""
    if _CACHE:
        return _CACHE
    sub, tooling, why = None, None, ""
    try:
        sub = json.load(open(os.path.join(LABS, "engine", "substrate.json")))
    except (OSError, ValueError):
        pass
    if yaml is None:
        why = "tooling audit not loaded (PyYAML is not installed)"
    else:
        try:
            tooling = (yaml.safe_load(open(os.path.join(LABS, "coverage", "tooling.yaml"))) or {}).get("practices") or {}
        except (OSError, ValueError, yaml.YAMLError):
            why = "tooling audit not loaded (coverage/tooling.yaml is missing or unreadable)"
    pb = {}
    try:
        pb = {d["discipline"]: d for d in json.load(open(os.path.join(LABS, "coverage", "playbook.json")))}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    _CACHE.update({"sub": sub, "tooling": tooling, "playbook": pb, "tooling_why": why})
    return _CACHE


def _index(sub):
    """id -> (kind, element). Challenge and practice ids never collide (checked when the page builds)."""
    idx = {}
    for i, c in sub["challenges"].items():
        idx[i] = ("challenge", c)
    for i, p in sub["practices"].items():
        idx[i] = ("practice", p)
    return idx


# --- the chain ------------------------------------------------------------------------------------------------

def _link(key, mark, word, line):
    return {"key": key, "label": dict(CH_LINKS + PR_LINKS)[key], "mark": mark, "word": word, "line": line}


def _signs(c):
    """(real signs, 'Not measured' notes, substrate_silent). A sign starting 'Not measured' is the substrate saying a
    laptop cannot see it. One leading the list disclaims the whole element; one further down narrows a single aspect.
    substrate_silent = no real sign, or the list leads with 'Not measured'."""
    signs = [s for s in (c.get("laptop_signs") or []) if (s or "").strip()]
    nm = [s for s in signs if s.strip().lower().startswith("not measured")]
    real = [s for s in signs if s not in nm]
    lead = bool(signs) and signs[0] in nm
    return real, nm, (not real) or lead


def _challenge_chain(c, row, pb):
    real, nm, silent = _signs(c)
    out = []
    hb = c.get("handled_by") or []
    if c.get("what_goes_wrong") and hb:
        out.append(_link("defined", "ok", "defined", f"Says what goes wrong, and {len(hb)} practice{'s' if len(hb) != 1 else ''} handle{'' if len(hb) != 1 else 's'} it."))
    else:
        out.append(_link("defined", "bad", "missing",
                         "No account of what goes wrong." if not c.get("what_goes_wrong") else "No practice is named as handling it."))
    if not real:
        out.append(_link("signs", "bad", "none" if not nm else "not measured",
                         "The substrate lists no laptop signs: it has not said what a laptop could show." if not nm else
                         "The substrate says 'Not measured': it does not claim a laptop can show this."))
    elif nm:
        out.append(_link("signs", "part", "partly",
                         f"{len(real)} laptop sign{'s' if len(real) != 1 else ''}, and {len(nm)} 'Not measured' note{'s' if len(nm) != 1 else ''}: the substrate says part of it can't be seen."
                         + (" It leads with 'Not measured'." if silent else "")))
    else:
        out.append(_link("signs", "ok", f"{len(real)} signs", f"The substrate names {len(real)} thing{'s' if len(real) != 1 else ''} a laptop can show."))
    chk = (row or {}).get("check") or {}
    kind = chk.get("kind") or "none"
    ran = row is not None and kind != "none"
    if not ran:
        out.append(_link("check", "bad", "not written",
                         "No check written yet, so nothing was run." + (" No laptop source either." if (row or {}).get("state") == "not read" else "")))
    elif kind in FRAGILE:
        out.append(_link("check", "part", kind, f"{KIND_WORD.get(kind, kind)}: looked at {chk.get('looked_at')}. May not hold on another laptop."))
    else:
        out.append(_link("check", "ok", kind, f"{KIND_WORD.get(kind, kind)}: looked at {chk.get('looked_at')}."))
    if ran and silent:
        out.append(_link("agrees", "bad", "beyond substrate",
                         f"The substrate says 'Not measured' (or lists no signs), yet the tool ran a check ({KIND_WORD.get(kind, kind).lower()}) and said '{row.get('shows') or 'nothing'}'. "
                         "The tool goes beyond the substrate: one of the two is wrong."))
    elif not ran and real and not silent:
        out.append(_link("agrees", "bad", "tool lags",
                         "The substrate names signs a laptop can show, but no check reads them. The tool lags the substrate."))
    elif ran:
        out.append(_link("agrees", "ok", "agree", "Both exist. Whether the check reads what the signs name is for a person to judge."))
    else:
        out.append(_link("agrees", "na", "both silent", "Neither the substrate nor the tool measures it."))
    if not ran:
        out.append(_link("moments", "na", "no check", "No check, so no moments."))
    elif chk.get("moments"):
        out.append(_link("moments", "ok", f"{len(chk['moments'])} kept", f"{len(chk['moments'])} moments kept, openable."))
    else:
        out.append(_link("moments", "bad", "none kept", chk.get("no_moments") or "No moments kept."))
    ex, how = (pb or {}).get("explained_in") or [], (pb or {}).get("how_to_in") or []
    if how:
        out.append(_link("taught", "ok", "taught", f"Explained in {len(ex)} playbook page{'s' if len(ex) != 1 else ''}; how to deal with it in {len(how)}."))
    elif ex:
        out.append(_link("taught", "part", "explained only", f"Explained in {len(ex)} playbook page{'s' if len(ex) != 1 else ''}, but no page shows how to deal with it."))
    else:
        out.append(_link("taught", "bad", "not taught", "No playbook page explains it or shows how to deal with it."))
    return out


_AUDIT = {"holds": ("ok", "holds", "The audit says the check reads this practice correctly."),
          "partly": ("part", "partly", "The audit says the check gets it partly right."),
          "wrong": ("bad", "wrong", "The audit says the check gets it wrong."),
          "cant-see": ("bad", "can't see", "The audit says a laptop can't see this practice.")}


def _practice_chain(p, det, tool, pb, tooling_loaded):
    out = []
    lad = p.get("ladder") or {}
    if p.get("what_you_do") and all(lad.get(k) for k in ("L2", "L3", "L4")):
        out.append(_link("defined", "ok", "defined", "Says what you do, with a bar for each of the three levels."))
    else:
        missing = [k for k in ("L2", "L3", "L4") if not lad.get(k)]
        out.append(_link("defined", "bad", "missing", "Missing: " + ", ".join((["what you do"] if not p.get("what_you_do") else []) + missing) + "."))
    sig = [t for t in (p.get("techniques") or []) if t.get("laptop_signal")]
    ev = bool((p.get("laptop_evidence") or "").strip())
    n = len(p.get("techniques") or [])
    if ev and sig:
        out.append(_link("signs", "ok", "signs", f"Laptop evidence is described, and {len(sig)} of {n} techniques name a laptop signal."))
    elif ev or sig:
        out.append(_link("signs", "part", "thin", "Laptop evidence is described, but no technique names a laptop signal." if ev else
                         f"{len(sig)} of {n} techniques name a laptop signal, but no laptop evidence is described."))
    else:
        out.append(_link("signs", "bad", "none", "Nothing says what a laptop could show."))
    state, rung = (det or {}).get("state"), (det or {}).get("rung")
    if det is None:
        out.append(_link("read", "bad", "no check", "No check result for this practice in this run."))
    elif state == "seen" and rung:
        out.append(_link("read", "ok", "read", f"Read on this laptop, and a step of the ladder was met ({rung})."))
    elif state == "seen":
        out.append(_link("read", "part", "no step met", "Read on this laptop, but no step of the ladder was met."))
    else:
        out.append(_link("read", "bad", "not read", f"The check did not read it ({state or 'no state'})."))
    if not tooling_loaded:
        out.append(_link("audit", "na", "not loaded", "Tooling audit not loaded."))
    elif not tool:
        out.append(_link("audit", "na", "not audited", "The tooling audit has no entry for this practice."))
    else:
        m, w, l = _AUDIT.get(tool.get("verdict"), ("na", tool.get("verdict") or "?", "Unknown audit verdict."))
        out.append(_link("audit", m, w, l + (f" {tool['note']}" if tool.get("note") else "")))
    evl, srch = bool((det or {}).get("evidence")), bool((det or {}).get("searched"))
    if evl and srch:
        out.append(_link("lines", "ok", "kept", f"{len(det['evidence'])} evidence lines and {len(det['searched'])} places looked are kept."))
    elif evl or srch:
        out.append(_link("lines", "part", "thin", "Evidence lines are kept but not where we looked." if evl else "Where we looked is kept but no evidence lines."))
    else:
        out.append(_link("lines", "bad", "none kept", "No evidence lines and no record of where we looked."))
    teaches = [(lv, x) for lv in ("L2", "L3", "L4") for x in (pb or {}).get(lv) or []]
    if any(x.get("status") == "teaches" for _, x in teaches):
        lv = sorted({lv for lv, x in teaches if x.get("status") == "teaches"})
        out.append(_link("taught", "ok", "taught", f"The playbook teaches it at {', '.join(lv)}."))
    elif teaches:
        out.append(_link("taught", "part", "mentioned", "The playbook mentions it but no page teaches it."))
    else:
        out.append(_link("taught", "bad", "not taught", "No playbook page teaches or mentions it."))
    return out


def first_break(links):
    for l in links:
        if l["mark"] == "bad":
            return l
    return None


def _verdict(row):
    if row is None:
        return "no result"
    if row.get("state") == "not read":
        return "not read"
    return row.get("shows") or "not read"


def category(row):
    """checked = a check ran · nocheck = laptop has data, no check written · nosrc = no laptop source (state: not read)."""
    if row is None or row.get("state") == "not read":
        return "nosrc"
    return "checked" if ((row.get("check") or {}).get("kind") or "none") != "none" else "nocheck"


def build(ctx):
    """-> None if no substrate, else {"sub", "idx", "by_disc": {disc: {"challenges": [item], "practices": [item]}}, ...}.
    item = {id, el, kind, links, fb (first bad link or None), row/det, cat/verdict, tool, pb, disc}."""
    D = load()
    sub = D["sub"]
    if not sub:
        return None
    rows = {r["id"]: r for r in ((ctx.get("cheap_problems") or {}).get("rows") or [])}
    dets = (ctx.get("v15_result") or {}).get("detectors") or {}
    pbs = {d: {x["id"]: x for x in rec.get("challenges", [])} for d, rec in D["playbook"].items()}
    pbp = {d: {x["id"]: x for x in rec.get("practices", [])} for d, rec in D["playbook"].items()}
    by = {d["id"]: {"challenges": [], "practices": []} for d in sub["disciplines"]}
    for i, c in sub["challenges"].items():
        d = c["discipline"]
        row = rows.get(i)
        links = _challenge_chain(c, row, pbs.get(d, {}).get(i))
        by[d]["challenges"].append({"id": i, "el": c, "kind": "challenge", "links": links, "fb": first_break(links), "row": row,
                                    "cat": category(row), "verdict": _verdict(row), "pb": pbs.get(d, {}).get(i), "disc": d})
    for i, p in sub["practices"].items():
        d = p["discipline"]
        det = dets.get(f"{d}/{i}")
        tool = (D["tooling"] or {}).get(i)
        links = _practice_chain(p, det, tool, pbp.get(d, {}).get(i), D["tooling"] is not None)
        by[d]["practices"].append({"id": i, "el": p, "kind": "practice", "links": links, "fb": first_break(links), "det": det,
                                   "tool": tool, "pb": pbp.get(d, {}).get(i), "disc": d})
    return {"sub": sub, "idx": _index(sub), "by": by, "tooling_why": D["tooling_why"], "playbook": D["playbook"]}


def counts(items):
    """First-break counts over a list of items: {substrate, tooling, data, teaching, whole}."""
    n = {"substrate": 0, "tooling": 0, "data": 0, "teaching": 0, "whole": 0}
    for it in items:
        n[OWNER[it["fb"]["key"]] if it["fb"] else "whole"] += 1
    return n


def verdict_line(items):
    """The one plain line about the problems, computed from the rows (never typed)."""
    ch = [it for it in items if it["kind"] == "challenge"]
    real = [it for it in ch if it["cat"] == "checked" and it["verdict"] in ("shows", "doesn't show")]
    shows = sum(1 for it in real if it["verdict"] == "shows")
    cant = sum(1 for it in ch if it["cat"] == "checked" and it["verdict"] == "can't tell")
    nochk = sum(1 for it in ch if it["cat"] == "nocheck")
    nosrc = sum(1 for it in ch if it["cat"] == "nosrc")
    return (f"{len(real)} of {len(ch)} problems got a real answer ({shows} shows · {len(real) - shows} doesn't show) · "
            f"{cant} can't tell · {nochk} have data on the laptop but no check written · {nosrc} have no laptop source")


# --- drawing ---------------------------------------------------------------------------------------------------

_PATH = {"ok": '<path d="M2.5 6.5l2.5 2.5 4.5-5.5"/>', "part": '<path d="M2 6.5c1.5-2 2.5-2 4-.5s2.5 1.5 4-.5"/>',
         "bad": '<path d="M3 3l6 6M9 3l-6 6"/>', "na": '<path d="M3 6h6"/>'}


def mark(m):
    return (f'<svg class="mk" viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_PATH[m]}</svg>')


def sub_href(disc):
    return f"#sub_{disc}"


def goto(id_, disc, text, anchor="sub-"):
    """A link to an element on its discipline page (the script opens it and scrolls)."""
    return f'<a href="{sub_href(disc)}" data-go="{anchor}{esc(id_)}">{esc(text)}</a>'


def _ref(idx, ref):
    """'orchestration:fresh-run-per-task' or 'doom-loop' -> link to that element."""
    i = ref.split(":", 1)[-1]
    if i in idx:
        kind, el = idx[i]
        return goto(i, el["discipline"], el["name"])
    return esc(ref)


def js():
    return """<script>(function(){if(window.__subNav)return;window.__subNav=1;
function open_(el){for(var d=el;d;d=d.parentElement)if(d.tagName==='DETAILS')d.open=true}
function counts(sec){sec.querySelectorAll('.tbl').forEach(function(t){var c=t.querySelector('.cnt');
 if(c)c.textContent=t.querySelectorAll('details.row:not([hidden])').length})}
document.addEventListener('click',function(e){
 var a=e.target.closest('a[data-go]');
 if(a){e.preventDefault();var id=a.dataset.go,view=a.getAttribute('href').slice(1);
  var go=function(){var el=document.getElementById(id);if(!el)return;open_(el);el.scrollIntoView({block:'start'})};
  if(location.hash==='#'+view)go();else{location.hash=view;setTimeout(go,80)}return}
 var c=e.target.closest('.sub-chip');
 if(c){var sec=c.closest('section'),f=c.dataset.f;
  sec.querySelectorAll('.sub-chip').forEach(function(x){x.setAttribute('aria-pressed',x===c)});
  sec.querySelectorAll('details.row[data-cat]').forEach(function(r){r.hidden=!(f==='all'||r.dataset.cat===f)});counts(sec);return}
 var o=e.target.closest('.sub-openall');
 if(o){var s=o.closest('section'),ds=[].slice.call(s.querySelectorAll('details.row:not([hidden]),details.el')),
  open=!ds.every(function(d){return d.open});ds.forEach(function(d){d.open=open});o.textContent=open?'Close all':'Open all'}
});})();</script>"""


# --- the chain page pieces ---------------------------------------------------------------------------------------

def chain_lines(it, with_edit=True):
    """One line per link, the failing ones marked, for the chain row and for the element on its discipline page."""
    fb = it["fb"]
    lis = "".join(
        f'<li class="{l["mark"]}{" fb" if l is fb else ""}"><span class="lh">{mark(l["mark"])}{esc(l["label"])}</span>'
        f'<span class="ll">{esc(l["line"])}'
        + (f' <span class="who">Breaks here: {OWNER_WORD[OWNER[l["key"]]]}.</span>' if l is fb else "") + "</span></li>"
        for l in it["links"])
    return f'<ol class="lk">{lis}</ol>'


def edit_line(disc):
    return f'Edit in <span class="mono">labs/v15/substrate/{esc(disc)}.yaml</span>'


def fb_text(it):
    fb = it["fb"]
    return (f'Breaks first at {fb["label"]} ({OWNER_WORD[OWNER[fb["key"]]]})' if fb else "No break in the chain")


def verdict_word(it, RW=None, NO=None):
    if it["kind"] == "challenge":
        return {"shows": "Shows", "doesn't show": "Doesn't show", "can't tell": "Can't tell", "not read": "Not read"}.get(it["verdict"], it["verdict"])
    det = it.get("det") or {}
    RW = RW or {}
    return det.get("rung") or (NO or {}).get(det.get("state")) or "Not read"


def cell(l, first):
    return (f'<span class="c {l["mark"]}{" fb" if first else ""}" data-l="{esc(l["label"])}">{mark(l["mark"])}'
            f'<span class="w">{esc(l["word"])}</span></span>')


def info_cell(label, word, cls=""):
    return f'<span class="c info {cls}" data-l="{esc(label)}"><span class="w">{esc(word)}</span></span>'


def row_html(it, RW, NO, extra=""):
    fb = it["fb"]
    if it["kind"] == "challenge":
        # order: Defined, Laptop signs, Check | Data on the laptop | Agrees, Moments, Taught | Verdict
        data = {"checked": ("yes", ""), "nocheck": ("yes, no check", ""), "nosrc": ("none", "gap")}[it["cat"]]
        L = it["links"]
        cells = ("".join(cell(l, l is fb) for l in L[:3]) + info_cell("Data on the laptop", *data)
                 + "".join(cell(l, l is fb) for l in L[3:])
                 + info_cell("Verdict", verdict_word(it), "shows" if it["verdict"] == "shows" else ""))
        n = 9
        cat = f' data-cat="{it["cat"]}"'
    else:
        cells = "".join(cell(l, l is fb) for l in it["links"]) + info_cell("Reads as", verdict_word(it, RW, NO))
        n = 7
        cat = ""
    name = esc(it["el"]["name"])
    inner = chain_lines(it)
    if it["kind"] == "challenge":
        r = it["row"] or {}
        inner += (f'<p class="why"><b>This laptop:</b> {esc(r.get("why") or "no result")}'
                  + (f' <span class="mono">{esc(r["rule"])}</span>' if r.get("rule") else "") + "</p>")
    else:
        d = it.get("det") or {}
        if d.get("why"):
            inner += f'<p class="why"><b>This laptop:</b> {esc(d["why"])}</p>'
    inner += (f'<p class="go">{goto(it["id"], it["disc"], "Full substrate text and this laptop\'s run")}'
              f' · {edit_line(it["disc"])}</p>')
    return (f'<details class="row" id="ch-{esc(it["id"])}"{cat}><summary><span class="nmc"><span class="nm">{name}</span>'
            f'<span class="fbw">{esc(fb_text(it))}</span></span><span class="cells" style="--n:{n}">{cells}</span></summary>'
            f'<div class="inner">{inner}</div></details>')


def header_html(kind):
    labs = [l for _, l in (CH_LINKS if kind == "challenge" else PR_LINKS)]
    if kind == "challenge":
        labs = labs[:3] + ["Laptop data"] + labs[3:] + ["Verdict"]
    else:
        labs = labs + ["Reads as"]
    return (f'<div class="hd"><span>{"Problem" if kind == "challenge" else "Practice"}</span><span class="cells" style="--n:{len(labs)}">'
            + "".join(f"<span>{esc(l)}</span>" for l in labs) + "</span></div>")


# --- CSS -------------------------------------------------------------------------------------------------------

def css(name):
    p = f".v-{name}"
    return f"""
{p} [hidden]{{display:none!important}}
{p} .mono{{font:400 12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}}
{p} .sum{{display:flex;flex-wrap:wrap;gap:8px 32px;margin:0 0 16px;padding:16px 0;border-top:1px solid #EAECF0;border-bottom:1px solid #EAECF0}}
{p} .sum div{{min-width:120px}}{p} .sum b{{display:block;font-size:24px;font-weight:600;line-height:1.2}}
{p} .sum span{{font-size:12px;color:#475467}}
{p} .line{{margin:0 0 8px;font-weight:500}}
{p} .small{{font-size:12px;color:#667085;margin:0 0 8px}}
{p} .bar{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:24px 0 8px}}
{p} .sub-chip{{all:unset;cursor:pointer;box-sizing:border-box;padding:4px 12px;border:1px solid #EAECF0;border-radius:999px;
  background:#FFFFFF;color:#475467;font:500 12px/1.5 Inter,system-ui,sans-serif}}
{p} .sub-chip:hover{{border-color:#B1B8FF}}
{p} .sub-chip[aria-pressed=true]{{border-color:#B1B8FF;background:#F5F5FF;color:#272594}}
{p} .sub-chip b{{font-weight:600;margin-left:4px}}
{p} .sub-chip:focus-visible,{p} .sub-openall:focus-visible{{outline:2px solid #5258FB;outline-offset:2px}}
{p} .sub-openall{{all:unset;cursor:pointer;margin-left:auto;font:500 12px/1.5 Inter,system-ui,sans-serif;color:#3C3ED4;padding:4px 8px}}
{p} .area{{margin:32px 0 48px}}
{p} .area>h3{{font-size:18px;font-weight:600;margin:0 0 4px}}
{p} .area>.q{{color:#475467;margin:0 0 16px;max-width:72ch}}
{p} .tbl{{margin:0 0 24px}}
{p} .tbl>h4{{font-size:14px;font-weight:600;margin:0 0 8px}}
{p} .tbl>h4 .cnt{{font-weight:400;color:#667085}}
{p} .cells{{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));gap:8px;align-items:start}}
{p} .hd{{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,7fr);gap:16px;font-size:12px;font-weight:500;color:#667085;
  padding:0 0 8px;border-bottom:1px solid #EAECF0}}
{p} .row{{border-bottom:1px solid #EAECF0}}
{p} .row>summary{{list-style:none;display:grid;grid-template-columns:minmax(0,2fr) minmax(0,7fr);gap:16px;padding:12px 0 12px 16px;
  align-items:start;position:relative}}
{p} .row>summary::-webkit-details-marker{{display:none}}
{p} .row>summary::before{{content:"";position:absolute;left:2px;top:19px;width:5px;height:5px;border-right:2px solid #667085;
  border-bottom:2px solid #667085;transform:rotate(-45deg);transition:transform .12s}}
{p} .row[open]>summary::before{{transform:rotate(45deg);top:17px}}
{p} .row>summary:hover .nm{{color:#3C3ED4}}
{p} .hd{{padding-left:16px}}
{p} .nmc{{min-width:0}}{p} .nm{{display:block;font-weight:500}}
{p} .fbw{{display:block;font-size:12px;color:#667085}}
{p} .c{{display:flex;flex-wrap:wrap;align-items:center;gap:0 6px;font-size:12px;line-height:1.5;padding:2px 6px;margin:-2px -6px;border-radius:6px;min-width:0}}
{p} .c .w{{min-width:0;overflow-wrap:anywhere}}
{p} .c .mk{{flex:none}}
{p} .c.ok .mk{{color:#039855}}{p} .c.part .mk{{color:#DC6803}}{p} .c.bad .mk{{color:#B54708}}{p} .c.na .mk{{color:#949BAD}}
{p} .c.bad .w{{color:#B54708;font-weight:500}}{p} .c.na .w{{color:#667085}}
{p} .c.fb{{background:#FDF1D9}}
{p} .c.info .w{{color:#475467}}{p} .c.info.gap .w{{color:#B54708}}{p} .c.info.shows .w{{color:#B54708;font-weight:500}}
{p} .inner{{padding:0 0 16px 16px}}
{p} .lk{{list-style:none;margin:0 0 12px;padding:0;max-width:96ch}}
{p} .lk li{{display:grid;grid-template-columns:136px minmax(0,1fr);gap:4px 16px;padding:6px 0;border-top:1px solid #EAECF0}}
{p} .lk li:first-child{{border-top:0}}
{p} .lh{{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:500;color:#475467}}
{p} .lk li.ok .lh .mk{{color:#039855}}{p} .lk li.part .lh .mk{{color:#DC6803}}{p} .lk li.bad .lh .mk{{color:#B54708}}{p} .lk li.na .lh .mk{{color:#949BAD}}
{p} .ll{{overflow-wrap:anywhere}}
{p} .who{{font-weight:600;color:#B54708}}
{p} .why,{p} .go{{margin:0 0 4px;max-width:96ch;font-size:12px;color:#475467}}
{p} .why b{{color:#15172F}}
{p} .empty{{color:#667085}}
{p} .cell-hint{{display:none}}
@media (max-width:760px){{
 {p} .hd{{display:none}}
 {p} .row>summary{{grid-template-columns:1fr;gap:8px}}
 {p} .cells{{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 16px}}
 {p} .c::before{{content:attr(data-l);flex:0 0 100%;font-size:12px;color:#667085}}
 {p} .lk li{{grid-template-columns:1fr}}
}}
"""


_CSS_DISC = """
{p} .scope{{font-size:16px;line-height:1.5;color:#15172F;margin:0 0 8px;max-width:72ch}}
{p} .scopemore{{margin:0 0 20px}}{p} .scopemore>summary{{font-size:13px;color:#3C3ED4;cursor:pointer}}
{p} .stats{{display:flex;flex-wrap:wrap;gap:8px 24px;align-items:baseline;margin:0 0 12px;color:#475467;font-size:13px}}
{p} .stats b{{font-size:24px;font-weight:600;color:#15172F;margin-right:6px;letter-spacing:-.01em}}
{p} .stats .sep{{width:1px;height:20px;background:#EAECF0;align-self:center}}
{p} .stats .fb b{{font-size:18px}}
{p} .el{{border-bottom:1px solid #EAECF0}}
{p} .el>summary{{list-style:none;display:grid;grid-template-columns:minmax(0,3fr) minmax(0,1.2fr) minmax(0,2.2fr);gap:4px 16px;padding:12px 0 12px 16px;
  align-items:baseline;position:relative}}
{p} .el>summary::-webkit-details-marker{{display:none}}
{p} .el>summary::before{{content:"";position:absolute;left:2px;top:19px;width:5px;height:5px;border-right:2px solid #667085;
  border-bottom:2px solid #667085;transform:rotate(-45deg);transition:transform .12s}}
{p} .el[open]>summary::before{{transform:rotate(45deg);top:17px}}
{p} .el>summary:hover .nm{{color:#3C3ED4}}
{p} .el .vd{{font-size:12px;font-weight:500;color:#475467}}{p} .el .vd.shows{{color:#B54708}}
{p} .el .fbw{{font-size:12px;color:#667085}}{p} .el .fbw.bad{{color:#B54708;font-weight:500}}
{p} .el{{scroll-margin-top:120px}}
{p} .body{{padding:0 0 24px 16px}}
{p} .part{{margin:16px 0 0;padding:16px 0 0;border-top:1px solid #D0D5DD}}
{p} .part:first-child{{border-top:0;padding-top:0;margin-top:0}}
{p} .part>h5{{font-size:14px;font-weight:600;margin:0 0 8px}}
{p} .part>h5 span{{font-weight:400;color:#667085;margin-left:8px;font-size:12px}}
{p} .f{{display:grid;grid-template-columns:136px minmax(0,1fr);gap:4px 16px;padding:8px 0;border-top:1px solid #EAECF0}}
{p} .f:first-of-type{{border-top:0}}
{p} .f>.lab{{font-size:12px;font-weight:500;color:#667085;padding-top:2px}}
{p} .f>.val{{min-width:0;overflow-wrap:anywhere;max-width:96ch}}
{p} .f ul{{margin:0;padding-left:16px}}{p} .f li{{margin:0 0 4px}}
{p} .f ul.plain{{list-style:none;padding:0}}
{p} .lad{{margin:0 0 4px}}{p} .lad b{{font-weight:600}}
{p} .tq{{font-size:12px;color:#475467}}
{p} .ev{{display:grid;grid-template-columns:minmax(0,170px) minmax(0,1fr);gap:4px 12px;margin:0;padding:0;list-style:none}}
{p} .ev li{{display:contents}}{p} .ev .src{{font-size:12px;font-weight:500;color:#475467}}
{p} .note{{font-size:12px;color:#667085;margin:4px 0 0}}
{p} .fold{{margin:4px 0 0}}{p} .fold>summary{{cursor:pointer;font-size:12px;color:#3C3ED4;list-style:none}}
{p} .fold>summary::-webkit-details-marker{{display:none}}
{p} .fold>summary::before{{content:"+ "}}{p} .fold[open]>summary::before{{content:"- "}}
{p} .fold .in{{margin-top:4px;color:#475467;font-size:12px}}
{p} .warn{{color:#B54708;font-weight:500}}
{p} .qq{{margin:0 0 16px;max-width:80ch}}{p} .qq p{{margin:0 0 8px}}
{p} .head{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 16px}}
{p} h3.grp{{font-size:18px;font-weight:600;margin:32px 0 8px}}
@media (max-width:760px){{
 {p} .el>summary{{grid-template-columns:1fr}}
 {p} .f{{grid-template-columns:1fr;gap:2px}}
 {p} .ev{{grid-template-columns:1fr}}{p} .ev .src{{margin-top:8px}}
}}
"""


def css_disc(name):
    p = f".v-{name}"
    return css(name) + _CSS_DISC.replace("{p}", p).replace("{{", "{").replace("}}", "}")
