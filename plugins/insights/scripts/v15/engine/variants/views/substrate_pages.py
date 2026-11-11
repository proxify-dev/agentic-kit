"""One discipline page of the Substrate section: the question and scope note, then every challenge and practice of the
discipline with its FULL substrate text (verbatim: it is what he is judging), where its chain breaks, what this
laptop's run says, and what the playbook teaches. Not a view. Called by view_sub_<discipline>.py."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_kit as CK  # noqa: E402
import receipt_kit as K  # noqa: E402
import substrate_kit as S  # noqa: E402

esc = S.esc


def _disc(disc):
    sub = S.load()["sub"]
    return next((d for d in (sub or {}).get("disciplines", []) if d["id"] == disc), None)


def title(disc):
    d = _disc(disc)
    return d["name"] if d else S.SHORT.get(disc, disc)


def question(disc):
    d = _disc(disc)
    return d["question"] if d else "The substrate for this discipline is not in this run."


# --- small pieces -----------------------------------------------------------------------------------------------

def _f(label, val):
    return f'<div class="f"><div class="lab">{esc(label)}</div><div class="val">{val}</div></div>' if val else ""


def _ul(items, cls=""):
    return f'<ul class="{cls}">' + "".join(f"<li>{i}</li>" for i in items) + "</ul>" if items else ""


def _fold(summary, inner):
    return f'<details class="fold"><summary>{esc(summary)}</summary><div class="in">{inner}</div></details>'


def _page(path):
    """A playbook page path -> a link; anything else (a hands-on station) stays text."""
    head, _, rest = path.partition(" \u2014 ")  # "/reference/vocabulary \u2014 Prompts and instructions": page, then the section
    if not head.startswith("/"):
        return esc(path)
    return f'<a href="{S.PLAYBOOK}{esc(head)}">{esc(head)}</a>' + (f' <span class="tq">\u00b7 {esc(rest)}</span>' if rest else "")


def _source(el):
    src, grade = el.get("source") or "", el.get("grade") or ""
    link = (f'<a href="{esc(src)}">{esc(src)}</a>' if src.startswith("http") else esc(src)) if src else "no source"
    return f'{link} <span class="tq">· grade: {esc(grade)}</span>'


def _refs(idx, ids):
    return _ul([S._ref(idx, i) for i in ids]) if ids else ""


def _part(title_, note, inner):
    return f'<div class="part"><h5>{esc(title_)}<span>{note}</span></h5>{inner}</div>'


def _missing(B, disc, it):
    rec = B["playbook"].get(disc) or {}
    key = "challenges" if it["kind"] == "challenge" else "practices"
    hits = [m for m in rec.get("missing_pages", []) if it["id"] in (m.get(key) or [])]
    if not hits:
        return ""
    body = "".join(f'<p><b>{esc(m["title"])}</b>. {esc(m.get("why_it_matters"))}</p>' for m in hits)
    return _f("Pages we lack", _fold(f"{len(hits)} proposed page{'s' if len(hits) > 1 else ''} would cover it", body))


# --- challenge --------------------------------------------------------------------------------------------------

def _challenge_body(B, it, chain=True):
    c, idx, row = it["el"], B["idx"], it["row"]
    sub = (_f("Also called", esc(c["term"]) if c.get("term") and c["term"] != "none" else "")
           + _f("Source", _source(c))
           + _f("What goes wrong", esc(c.get("what_goes_wrong")))
           + _f("Handled by", _refs(idx, c.get("handled_by") or []))
           + _f("Also handled in", _refs(idx, c.get("also_handled_in") or []))
           + _f("Ask on the call", esc(c.get("ask")))
           + _f("Call signs", _ul([esc(s) for s in c.get("call_signs") or []]))
           + _f("Laptop signs", _ul([esc(s) for s in c.get("laptop_signs") or []]) or "none listed")
           + _f("Check", f'<span class="mono">{esc(c.get("detector"))}</span>' if c.get("detector") else ""))
    if row:
        lap = (_f("Verdict", f'<b>{esc(S.verdict_word(it))}</b>')
               + _f("Why", esc(row.get("why"))) + _f("Rule", f'<span class="mono">{esc(row.get("rule"))}</span>')
               + CK.block(row))
    else:
        lap = '<p class="note">Not in this run.</p>'
    pb = it["pb"] or {}
    ex, how = pb.get("explained_in") or [], pb.get("how_to_in") or []
    teach = (_f("Explained in", _ul([_page(p) for p in ex]) or "no page") + _f("How to, in", _ul([_page(p) for p in how]) or "no page")
             + _missing(B, it["disc"], it))
    return ((_part("Where the chain breaks", "", S.chain_lines(it)) if chain else "")
            + _part("In the substrate", S.edit_line(it["disc"]), sub)
            + _part("On this laptop", "this laptop's run", lap)
            + _part("In the playbook", "", teach))


# --- practice ---------------------------------------------------------------------------------------------------

def _searched(entry):
    m = re.match(r"\[([^\]]*)\]\s*(.*)$", entry, re.S)
    tag, body = (m.group(1), m.group(2)) if m else ("", entry)
    first = body.partition(" · ")[0]
    return (K.source_label(tag) + (" · " if tag and first else "") + first)[:160]


def _practice_body(B, it, RW, NO, chain=True):
    p, idx, det, tool = it["el"], B["idx"], it["det"], it["tool"]
    lad = p.get("ladder") or {}
    ladder = "".join(f'<p class="lad"><b>{k}</b>: {esc(lad.get(k)) or "missing"}</p>' for k in ("L2", "L3", "L4"))
    techs = _ul([f'<b>{esc(t.get("tool"))}</b> · {esc(t.get("how"))} <span class="tq">· '
                 + (f'Laptop signal: {esc(t["laptop_signal"])}' if t.get("laptop_signal") else "no laptop signal") + "</span>"
                 for t in p.get("techniques") or []])
    learn = _ul([(f'<a href="{S.PLAYBOOK}{esc(l["ref"])}">{esc(l.get("title") or l["ref"])}</a>' if l.get("kind") == "playbook"
                  else f'<a href="{esc(l["ref"])}">{esc(l.get("title") or l["ref"])}</a>' if str(l.get("ref", "")).startswith("http")
                  else f'Hands-on · {esc(l.get("title") or l.get("ref"))}') + f' <span class="tq">· {esc(l.get("kind"))}</span>'
                 for l in p.get("learn") or []])
    sub = (_f("Also called", esc(p["term"]) if p.get("term") and p["term"] != "none" else "")
           + _f("Source", _source(p)) + _f("Audience", esc(p.get("audience")))
           + _f("What you do", esc(p.get("what_you_do")))
           + _f("Handles", _refs(idx, p.get("handles") or [])) + _f("Also handles", _refs(idx, p.get("also_handles") or []))
           + _f("The ladder", ladder) + _f("Techniques", techs or "none listed")
           + _f("Call evidence", esc(p.get("call_evidence"))) + _f("Laptop evidence", esc(p.get("laptop_evidence")))
           + _f("Learn", learn) + _f("Rulings", _ul([esc(r) for r in p.get("rulings") or []])))
    if det:
        ev = det.get("evidence") or []
        li = lambda e: (f'<li><span class="src">{esc(K.source_label(e.get("source")))}</span>'  # noqa: E731
                        f'<span class="mono">{esc(K.receipt(e.get("detail")))}</span></li>')
        evh = (f'<ul class="ev">{"".join(li(e) for e in ev[:5])}</ul>'
               + (_fold(f"Show all {len(ev)}", f'<ul class="ev">{"".join(li(e) for e in ev[5:])}</ul>') if len(ev) > 5 else "")
               if ev else '<span class="warn">none kept</span>')
        srch = det.get("searched") or []
        sh = (_fold(f"Show the {len(srch)} place{'s' if len(srch) != 1 else ''} read", _ul([f'<span class="mono">{esc(_searched(s))}</span>' for s in srch]))
              if srch else '<span class="warn">none kept</span>')
        how = {"code": "plain code, a counted rule", "judgment": "an AI read"}.get(det.get("derivation"), det.get("derivation") or "")
        if det.get("found_by"):
            how += f" ({det['found_by']})"
        lap = (_f("Reads as", f'<b>{esc(S.verdict_word(it, RW, NO))}</b> <span class="tq">· state: {esc(det.get("state"))}'
                  + (f", step {esc(det['rung'])}" if det.get("rung") else "") + "</span>")
               + _f("Why", esc(det.get("why"))) + _f("Evidence lines", evh) + _f("Where we looked", sh)
               + _f("Rule", f'<span class="mono">{esc(det.get("rule"))}</span>' if det.get("rule") else "")
               + _f("Decided by", esc(how)) + _f("Checked by a person", esc(det.get("verdict") or "unchecked")))
    else:
        lap = '<p class="note">No check result in this run.</p>'
    if tool:
        aud = (_f("Audit verdict", f'<b>{esc(tool.get("verdict"))}</b> <span class="tq">· AI audit on one laptop, not checked by a person</span>')
               + _f("Note", esc(tool.get("note"))) + _f("Cost to read", esc(tool.get("cost"))) + _f("Reads", esc(tool.get("reads"))))
    else:
        aud = f'<p class="note">{esc(B["tooling_why"] or "The tooling audit has no entry for this practice.")}</p>'
    pb = it["pb"] or {}
    lv = ""
    for k in ("L2", "L3", "L4"):
        pages = pb.get(k) or []
        lv += _f(f"{k}", _ul([f'{_page(x["page"])} <span class="tq">· {esc(x.get("section"))} · {esc(x.get("status"))}</span>'
                                               for x in pages]) or "no page")
    teach = _f("Note", esc(pb.get("note"))) + lv + _missing(B, it["disc"], it)
    return ((_part("Where the chain breaks", "", S.chain_lines(it)) if chain else "")
            + _part("In the substrate", S.edit_line(it["disc"]), sub)
            + _part("On this laptop", "this laptop's run", lap)
            + _part("Tooling audit", "an AI audit, not person-checked", aud)
            + _part("In the playbook", "", teach))


# --- the page ---------------------------------------------------------------------------------------------------

def _el(B, it, RW, NO):
    fb = it["fb"]
    vd = S.verdict_word(it, RW, NO)
    body = _challenge_body(B, it) if it["kind"] == "challenge" else _practice_body(B, it, RW, NO)
    return (f'<details class="el" id="sub-{esc(it["id"])}"><summary><span class="nm">{esc(it["el"]["name"])}</span>'
            f'<span class="vd{" shows" if vd == "Shows" else ""}">{esc(vd)}</span>'
            f'<span class="fbw{" bad" if fb else ""}">{esc(S.fb_text(it))}</span></summary>'
            f'<div class="body">{body}<p class="note"><a class="pl" href="#{"c" if it["kind"] == "challenge" else "p"}/{esc(it["id"])}">See it on your setup</a> · <a href="#sub_board">Where it stands on the board</a></p></div></details>')


def render(ctx, disc):
    B = S.build(ctx)
    d = _disc(disc)
    if not B or not d:
        return '<p class="empty">Not in this run: engine/substrate.json was not found.</p>'
    try:
        from common import NO_RUNG_WORD as NO
    except ImportError:
        NO = {}
    RW = ctx["RUNG_WORD"]
    b = B["by"][disc]
    allit = b["challenges"] + b["practices"]
    n = S.counts(allit)
    scope = d.get("scope_note") or ""
    first, _, rest = scope.partition(". ")
    stats = (f'<div class="stats"><span><b>{len(b["challenges"])}</b>problems</span><span><b>{len(b["practices"])}</b>practices</span>'
             f'<span class="sep"></span><span class="fb"><b>{n["substrate"]}</b>break first in the substrate</span>'
             f'<span class="fb"><b>{n["tooling"]}</b>in the tooling</span><span class="fb"><b>{n["data"]}</b>in the data</span>'
             f'<span class="fb"><b>{n["teaching"]}</b>in the teaching</span><span class="fb"><b>{n["whole"]}</b>whole</span></div>')
    top = (f'<p class="scope">{esc(first)}.</p>'
           + (f'<details class="scopemore"><summary>The full scope note</summary><div class="qq"><p>{esc(scope)}</p></div></details>' if rest else "")
           + stats + '<div class="head"><span class="small"></span><button class="sub-openall" type="button">Open all</button></div>')
    out = [top, f'<h3 class="grp">Problems ({len(b["challenges"])})</h3>']
    out += [_el(B, it, RW, NO) for it in b["challenges"]]
    out.append(f'<h3 class="grp">Practices ({len(b["practices"])})</h3>')
    out += [_el(B, it, RW, NO) for it in b["practices"]]
    return "".join(out) + S.js()
