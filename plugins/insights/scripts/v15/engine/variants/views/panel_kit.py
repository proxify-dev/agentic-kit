"""One side panel for every substrate item, used by every view. Not a view: frame.py only loads view_*.py.

The 11-05 point: the substrate is a knowledge base as well as an evaluation, so wherever a practice, a problem or a
vocabulary term appears, its name opens the same panel, and the panel links onward. Each has a deep link that works from
any tab: `#p/<practice id>`, `#c/<problem id>`, `#t/<term id>` (frame.py leaves the current tab where it is and opens the panel).

A view makes a name open the panel with `link(kind, id, text)` (an anchor), or marks a whole card with `data-pk="p:<id>"`
(card_kit.card does this). frame.py embeds `payload(ctx)` once as JSON and adds `html()`, `CSS` and `JS`.

What the panels show, from the run and the substrate, nothing typed here:
- practice: what you do · the steps with your level marked · why it matters (the problems it handles, each a link, with
  your status on it) · how we know (card_kit.how, the evidence row) and whether it counts on any laptop · the next step for
  you if one points at it · learning material · the vocabulary it uses. Proxify's copy adds the level lists, what is missing
  for the next level, and what to ask on the call.
- problem: what goes wrong · what shows it in your logs · your status · the practices that handle it (each a link, with
  your level) · the next step if one points at it · its vocabulary. Proxify's copy adds the question to ask and the call signs.
- term: what it means, who coined it, what it is also called, and the practices and problems that use it.
The member's copy never shows a level: words ("You do it", "Your setup does it", "Runs without you") take its place.
The substrate board's own "story" (view_sub_board) opens in this same panel through `window.kpOpenHtml`.
"""
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import card_kit as C  # noqa: E402
import check_kit  # noqa: E402  how a problem's verdict was reached: the check and its moments
import common  # noqa: E402  the member wording
import receipt_kit  # noqa: E402  plain(): the checks' wording says he/his, the page says you/your; the raw numbers a check stored
import substrate_kit as S  # noqa: E402

esc = lambda s: html.escape("" if s is None else str(s))  # noqa: E731
PLAYBOOK = "https://agentic.proxify.io"
LEVELS = ("L1", "L2", "L3", "L4")
KIND_NAME = {"p": "Practice", "c": "Problem", "t": "Term", "m": "Map item"}


def link(kind, id_, text, cls=""):
    """An anchor that opens the panel on this item: `#p/<id>`, `#c/<id>` or `#t/<id>`."""
    return f'<a class="pl{" " + cls if cls else ""}" href="#{kind}/{esc(id_)}">{esc(text)}</a>'


def _first(s, n=160):
    s = (s or "").strip()
    cut = re.split(r"(?<=[.!?])\s", s, 1)[0]
    return (cut if len(cut) <= n else cut[: n - 1].rstrip() + "…")


def _you(s, member):
    """A substrate sentence is written about "they"; the member's copy says "you" and drops level talk."""
    if not member:
        return common.clean(s) or (s or "")
    s = common.clean(s)
    for pat, to in common._GENERIC:
        s = pat.sub(to, s)
    return s


EXT = ('<svg class="kp-ext" viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.5" '
       'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 2.5h5v5M9.5 2.5 3 9"/></svg>')


def _learn(items):
    """Small cards: the title, a pill saying where it lives (Playbook, Outside, Hands-on), an arrow for an outside link."""
    out = []
    for l in items or []:
        title, ref, kind = l.get("title") or l.get("ref"), l.get("ref") or "", l.get("kind")
        if kind == "playbook":
            href, word, ext = PLAYBOOK + ref, "Playbook", False
        elif kind == "outside" and ref.startswith("http"):
            href, word, ext = ref, "Outside", True
        else:
            href, word, ext = None, "Hands-on" if kind == "station" else (kind or ""), False
        pill = C.pill("", word, "brand" if word == "Playbook" else "") if word else ""
        inner = f'<span class="kp-ct">{esc(title)}</span><span class="kp-cm">{pill}{EXT if ext or href else ""}</span>'
        out.append(f'<a class="kp-card" href="{esc(href)}" target="_blank" rel="noopener">{inner}</a>' if href
                   else f'<div class="kp-card">{inner}</div>')
    return f'<div class="kp-cards">{"".join(out)}</div>' if out else ""


def _sec(title, body, cls=""):
    return f'<section class="kp-s{" " + cls if cls else ""}"><h4>{esc(title)}</h4>{body}</section>' if body else ""


def _call(question, listen_html="", label="Listen for"):
    """On the call: the question as a callout; what to listen for stays behind a click."""
    q = f'<div class="kp-call"><span>Ask</span><p>“{esc(question)}”</p></div>' if question else ""
    more = f'<details class="kp-more"><summary>{esc(label)}</summary><div>{listen_html}</div></details>' if listen_html else ""
    return q + more


class Ctx:
    """The run and the substrate, read once for all panels."""

    def __init__(self, ctx):
        self.member = bool(ctx.get("member"))
        self.RW = ctx.get("RUNG_WORD") or common.RUNG_WORD
        sub = (S.load() or {}).get("sub") or {}
        self.P, self.Ch, self.V = sub.get("practices") or {}, sub.get("challenges") or {}, sub.get("vocabulary") or {}
        res = ctx.get("v15_result") or {}
        self.area, self.run_p, self.run_c = {}, {}, {}
        for d in res.get("disciplines") or []:
            self.area[d.get("id")] = d.get("name")
            for p in d.get("practices") or []:
                self.run_p[p.get("id")] = p
            for c in d.get("challenges") or []:
                self.run_c[c.get("id")] = c
        self.ev = {e["id"]: e for e in res.get("evidence") or [] if e.get("kind") == "practice" and e.get("id")}
        self.recs = list(res.get("recommendations") or []) + list(res.get("more_recommendations") or [])
        vm = ctx.get("vm") or {}
        self.vm_next = list(vm.get("next") or []) + list(vm.get("more_next") or [])
        self.pointer = res.get("pointer") or {}
        self.rows = {r["id"]: r for r in ((ctx.get("cheap_problems") or {}).get("rows") or []) if r.get("kind") == "challenge" and r.get("id")}
        self.clean = ctx.get("clean") or common.clean
        self.portable = portable_by_pid(ctx)
        missing = (self.pointer.get("missing_for_next") or {}) if not self.member else {}
        self.next_lv = missing.get("level")
        self.missing = [list(g) for g in missing.get("practices") or []]
        try:
            sys.path.insert(0, os.path.join(HERE, "..", ".."))
            import engine as _engine
            self.lists = _engine.LEVEL_LISTS
            self.HAND = _engine.HAND
        except Exception:  # noqa: BLE001 - without the engine the level lists are left out
            self.lists, self.HAND = {}, 2

    def pname(self, pid):
        return (self.P.get(pid) or {}).get("short_name") or (self.P.get(pid) or {}).get("name") or pid

    def cname(self, cid):
        return (self.Ch.get(cid) or {}).get("name") or cid

    def level(self, pid):
        """The practice's level badge for this person: an L-code for Proxify, words for the member."""
        p = self.run_p.get(pid) or {}
        could = p.get("could_be_rung") if p.get("could_be") else None
        return C.level(p.get("rung"), self.RW, self.member, could=could,
                       no_rung_word=common.NO_RUNG_WORD.get(p.get("state") or "", "Not yet"))

    def status(self, cid):
        """(tone, word) for a problem: shows / doesn't show / can't tell (common.PROBLEM)."""
        c = self.run_c.get(cid)
        if not c:
            return None
        looked = None if c.get("state") == "not read" else bool(c.get("seen"))
        return common.PROBLEM[looked]

    def status_html(self, cid):
        st = self.status(cid)
        if not st:
            return ""
        tone, word = st
        icon = {"attention": "▲", "good": "✓", "unknown": "?"}[tone]
        return C.state(icon, word, {"attention": "warn", "good": "good"}.get(tone, ""))

    def rec_for(self, pid=None, cid=None):
        """(raw, member card) for the next step that points at this practice or problem, if one does."""
        for i, r in enumerate(self.recs):
            if (pid and r.get("practice") == pid) or (cid and r.get("challenge") == cid):
                return r, (self.vm_next[i] if i < len(self.vm_next) else None)
        return None, None

    def vocab_links(self, ids):
        """Chips: the definition shows on hover or focus; the chip opens the term panel."""
        seen, out = set(), []
        for t in ids:
            if t in self.V and t not in seen:
                seen.add(t)
                out.append(f'<a class="kp-chip" href="#t/{esc(t)}" data-tip="{esc(_first(self.V[t].get("meaning"), 200))}">{esc(self.V[t].get("term") or t)}</a>')
        return f'<div class="kp-chips">{"".join(out[:12])}</div>' if out else ""




_VALUE = re.compile(r"^(.*?):\s+(?:None|-?\d[\d.,]*)\b")
_ALLOWED = os.path.join(HERE, "..", "..", "..", "..", "setup-snapshot", "experiments", "2026-11-05-portable", "level-allowed.json")


def _approved_ids():
    """Ids of the checks the skeptic approved to set a level (read only: the portable folder is not ours). Empty when the file is missing."""
    try:
        return {f"portable:{x['pid']}:{x['L']}#{x['i']}" for x in json.load(open(_ALLOWED, encoding="utf-8"))["allowed"]}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _nolevel(t):
    """The member's copy never names a level: a check's note that points at another check by its level code says 'another check'."""
    return re.sub(r"\bL[1-4]#\d+\b", "another check", re.sub(r"\bL[1-4]\b", "a level", t or ""))


def portable_by_pid(ctx):
    """{practice id: [check]}, every portable check of the run (run.py pre writes portable-checks.json and portable-rows.json).
    Two separate facts per check, never mixed (11-05): `approved` is about the tool (the skeptic let this check set a level), `run` is
    about this laptop: counted | slow (over its time budget) | cant (can't tell) | error. An approved check that was too slow is approved
    and slow, never "proven"."""
    checks = ((ctx.get("portable_checks") or {}).get("checks")) or []
    rows = (ctx.get("portable_rows") or {}).get("practices") or {}
    says, approved = {}, _approved_ids()
    for row in rows.values():
        for s in (row.get("signals") or []) + (row.get("below_bar") or []):
            says[s.get("id")] = s.get("says")
        approved |= {s["id"] for s in row.get("signals") or [] if str(s.get("id", "")).startswith("portable:")}
    nl = _nolevel if ctx.get("member") else (lambda t: t)
    out = {}
    for c in checks:
        cid = f"portable:{c.get('pid')}:{c.get('L')}#{c.get('i')}"
        m = _VALUE.match(says.get(cid) or "")
        run = {"timeout": "slow", "cant_tell": "cant", "error": "error"}.get(c.get("status")) or "counted"
        t = lambda x: nl(receipt_kit.plain(C.you_text(x)))  # noqa: E731
        out.setdefault(c.get("pid"), []).append({"L": c.get("L"), "claim": t(m.group(1) if m else (says.get(cid) or cid)), "value": c.get("value"),
                                                 "approved": cid in approved, "run": run, "note": t((c.get("note") or "").replace("not proven on other laptops yet: ", "")),
                                                 "evidence": [t(e) for e in c.get("evidence") or []]})
    return out


_RUN_PILL = {"counted": ("✓", "counted on your laptop", "good"), "slow": ("◷", "too slow here", "warn"),
             "cant": ("?", "can't tell", ""), "error": ("!", "could not run", "warn")}


def _checks_html(K, pid):
    cs = K.portable.get(pid) or []
    if not cs:
        return ""
    k, n, j = sum(c["approved"] for c in cs), sum(c["run"] == "counted" for c in cs), sum(c["run"] == "slow" for c in cs)
    head = f'<b>{k}</b> of {len(cs)} checks approved · <b>{n}</b> counted on your laptop' + (f" ({j} too slow here)" if j else "")
    rows = []
    for c in cs:
        icon, word, tone = _RUN_PILL[c["run"]]
        val = "" if c["value"] is None else f'{c["value"]:,}' if isinstance(c["value"], int) else str(c["value"])
        pills = (C.pill("✓", "approved", "brand") if c["approved"] else C.pill("◐", "not approved yet")) + C.pill(icon, word, tone)
        ev = "".join(f"<li>{esc(e)}</li>" for e in c["evidence"][:3])
        rows.append(f'<li class="kp-chk"><span class="kp-ck" title="{esc(c["claim"])}">{esc(c["claim"])}</span><span class="kp-cp">{pills}</span>'
                    f'<span class="kp-cv">{" · ".join(x for x in (("<b>" + esc(val) + "</b>") if val and c["run"] == "counted" else "", "" if K.member else esc(c["L"]), esc(c["note"]) if c["note"] and c["run"] != "counted" else "") if x)}</span>'
                    + (f'<details class="kp-more"><summary>What it read</summary><ul class="kp-ul">{ev}</ul></details>' if ev else "") + "</li>")
    return f'<p class="kp-dim">{head}</p><ul class="kp-rows kp-chks">{"".join(rows)}</ul>'


SPLIT = "<!--kp-->"  # everything before it is the panel's sticky header (kicker, title, badges); the rest scrolls


def _head(kicker, title, badges=""):
    return (f'<p class="kp-k">{kicker}</p><h3>{esc(title)}</h3>' + (f'<div class="kp-b">{badges}</div>' if badges else "") + SPLIT)


def _rec_html(K, raw, card):
    if not raw:
        return ""
    step = (card or {}).get("step") or (card or {}).get("intro") or ""
    why = (card or {}).get("why_it_matters") or ""
    head = (f'<p class="kp-pair"><span><i>Problem</i>{link("c", raw.get("challenge"), (card or {}).get("problem") or K.cname(raw.get("challenge")))}</span>'
            f'<span><i>Try</i>{link("p", raw.get("practice"), K.pname(raw.get("practice")))}</span></p>')
    return head + (f"<p>{esc(step)}</p>" if step else "") + (f'<p class="kp-dim">{esc(why)}</p>' if why else "")


def _ladder(K, pid, p):
    run = K.run_p.get(pid) or {}
    rung = run.get("rung")
    could = run.get("could_be_rung") if run.get("could_be") else None
    ladder, short = p.get("ladder") or {}, p.get("ladder_short") or {}
    rows = []
    for lv in LEVELS:
        if not ladder.get(lv) and not short.get(lv):
            continue
        label = (K.RW.get(lv) if K.member else lv) or lv
        you = lv == rung
        mark = ('<span class="pill brand">You are here</span>' if you else
                ('<span class="pill">Could be</span>' if lv == could and not rung else ""))
        full = _you(ladder.get(lv), K.member)
        rows.append(f'<li class="{"me" if you else ""}"><span class="kp-lv">{esc(label)}</span>'
                    f'<span class="kp-lt"><span>{esc(short.get(lv) or _first(full))}</span>{mark}'
                    + (f'<details><summary>Full wording</summary><p>{esc(full)}</p></details>' if full and full != short.get(lv) else "")
                    + "</span></li>")
    return f'<ol class="kp-lad">{"".join(rows)}</ol>' if rows else ""


def _lists(K, pid):
    """Proxify's copy: which level lists ask for this practice, and whether it is what is missing for the next level."""
    out = []
    for lv in ("L2", "L3", "L4"):
        for ids, need in K.lists.get(lv, []):
            if pid in ids:
                out.append(C.pill("", f"{lv} list · " + ("you, at least by hand" if need == K.HAND else "your setup")))
    need = next((g for g in K.missing if pid in g), None)
    if need:
        alt = [K.pname(i) for i in need if i != pid]
        out.append(C.pill("▲", f"Needed for {K.next_lv}" + (f" (or {'; '.join(alt)})" if alt else ""), "warn"))
    return f'<div class="kp-pills">{"".join(out)}</div>' if out else ""


def practice(K, pid):
    p, run, e = K.P[pid], K.run_p.get(pid) or {}, K.ev.get(pid) or {}
    area = K.area.get(p.get("discipline")) or p.get("discipline") or ""
    icon, words = C.how(e.get("fact"), e.get("not_counted"))
    found = common.clean(run.get("why")) if run.get("why") else ""
    what = common.you(p.get("what_you_do")) if K.member else common.clean(p.get("what_you_do"))
    handled = []
    for cid in p.get("all_handles") or []:
        c = K.Ch.get(cid)
        if c:
            handled.append(f'<li class="kp-row" data-pk="c:{esc(cid)}"><span class="kp-rt">{link("c", cid, c.get("name"))}</span>{K.status_html(cid)}'
                           f'<span class="kp-rd">{esc(_first(_you(c.get("what_goes_wrong"), K.member)))}</span></li>')
    vocab = list(p.get("vocab") or []) + [t for cid in p.get("all_handles") or [] for t in (K.Ch.get(cid) or {}).get("vocab") or []]
    raw, card = K.rec_for(pid=pid)
    how_html = (f'<div class="kp-pills">{C.pill(icon, words, "warn" if icon == C.HOW_NONE[0] else "")}{C.laptop_pill(p.get("laptop_counts"))}</div>'
                + (f'<p class="kp-dim">{esc(e["not_counted"])}</p>' if e.get("not_counted") else "")
                + (f"<p>{esc(found)}</p>" if found else ""))
    out = [_head(f"Practice · {esc(area)}", p.get("short_name") or p.get("name"), K.level(pid) + C.laptop_pill(p.get("laptop_counts")) + ("" if run.get("rung") else C.log_line())),
           f'<p class="kp-full">{esc(common.clean(p.get("name")) or p.get("name"))}</p>',
           _sec("What you do", f"<p>{esc(what)}</p>" if what else "", "lead"),
           _sec("Where you are on it" if not K.member else "Who carries it", _ladder(K, pid, p)),
           _sec("Why it matters: problems it handles", f'<ul class="kp-rows">{"".join(handled)}</ul>' if handled else ""),
           _sec("How we know", how_html),
           _sec("Checks on your laptop", _checks_html(K, pid)),
           _sec("On the level lists", _lists(K, pid)) if not K.member else "",
           _sec("Your next step on this", _rec_html(K, raw, card), "kp-rec"),
           _sec("Learn more", _learn(p.get("learn"))),
           _sec("Words it uses", K.vocab_links(vocab))]
    if not K.member and (p.get("call_evidence") or run.get("ask")):
        out.append(_sec("On the call", _call(run.get("ask"), f"<p>{esc(p.get('call_evidence') or '')}</p>")))
    return "".join(out)


def _stored(r, why):
    """The raw numbers a problem's check stored, source by source; none when they only repeat the sentence above."""
    rows = r.get("evidence") or []
    if not rows:
        return '<p class="missing">No numbers were read: nothing on this laptop covers this problem.</p>'
    out = []
    for e in rows:
        det = (e.get("detail") or "").strip()
        out.append(f'<div class="src-line"><span class="lab">{esc(receipt_kit.source_label(e.get("source")))}</span></div>')
        if det and (det[:60] in why or why[:60] in det):
            out.append('<p class="dim">The figures are the ones in the sentence above.</p>')
        else:
            out.append(receipt_kit.render_raw(det))
    return "".join(out)


def _measured(r, clean):
    """How a problem's verdict was reached (Proxify's copy): the sentence, the check, whether code or an AI decided, and
    the raw numbers. `r` is a cheap-problems row; the CSS is receipt_kit.css(".kp-m"), added to CSS below."""
    why = clean(r.get("why")) or receipt_kit.plain(r.get("why"))
    how = ("<p><b>Plain code</b> decided this: a counted measure over your logs and files, no AI read.</p>"
           if r.get("derivation") == "code" else "<p><b>An AI read</b> decided this.</p>")
    return (f'<div class="kp-m"><p>{esc(why)}</p>{check_kit.block(r)}<div class="sec" style="margin-top:16px">{how}'
            + receipt_kit.fold("The raw numbers the check stored", _stored(r, why)) + "</div></div>")


def challenge(K, cid):
    c, run = K.Ch[cid], K.run_c.get(cid) or {}
    area = K.area.get(c.get("discipline")) or c.get("discipline") or ""
    handlers = [f'<li class="kp-row" data-pk="p:{esc(pid)}"><span class="kp-rt">{link("p", pid, K.pname(pid))}</span>{K.level(pid)}</li>'
                for pid in c.get("all_handlers") or [] if pid in K.P]
    signs = "".join(f"<li>{esc(_you(s, K.member))}</li>" for s in c.get("laptop_signs") or [])
    raw, card = K.rec_for(cid=cid)
    found = common.clean(run.get("why")) if run.get("why") else ""
    term_ = f" · {esc(c.get('term'))}" if c.get("term") not in (None, "none") else ""
    out = [_head(f"Problem · {esc(area)}{term_}", c.get("name"), K.status_html(cid) + ("" if (K.status(cid) or ("attention",))[0] == "attention" else C.log_line())),
           _sec("What goes wrong", f'<p>{esc(_you(c.get("what_goes_wrong"), K.member))}</p>', "lead"),
           _sec("Your status", (f"<p>{esc(found)}</p>" if found else '<p class="kp-dim">Nothing more to say from your logs.</p>') if run else ""),
           _sec("Practices that handle it", f'<ul class="kp-rows">{"".join(handlers)}</ul>' if handlers else ""),
           _sec("What shows it in your logs", f'<ul class="kp-ul">{signs}</ul>' if signs else ""),
           _sec("Your next step on this", _rec_html(K, raw, card), "kp-rec"),
           _sec("Words it uses", K.vocab_links(c.get("vocab") or []))]
    if not K.member and cid in K.rows:
        try:
            out.append(_sec("How we measured it", f'<details class="kp-more"><summary>Show the check</summary><div>{_measured(K.rows[cid], K.clean)}</div></details>'))
        except Exception as e:  # noqa: BLE001
            out.append(_sec("How we measured it", f'<p class="empty">Could not build: {esc(e)}</p>'))
    if not K.member:
        calls = "".join(f"<li>{esc(s)}</li>" for s in c.get("call_signs") or [])
        out.append(_sec("On the call", _call(c.get("ask"), f'<ul class="kp-ul">{calls}</ul>' if calls else "")))
    return "".join(out)


def term(K, tid):
    t = K.V[tid]
    users = ([f'<li class="kp-row" data-pk="p:{esc(pid)}"><span class="kp-rt">{link("p", pid, K.pname(pid))}</span>{C.pill("", "Practice")}</li>'
              for pid, p in K.P.items() if tid in (p.get("vocab") or [])]
             + [f'<li class="kp-row" data-pk="c:{esc(cid)}"><span class="kp-rt">{link("c", cid, K.cname(cid))}</span>{C.pill("", "Problem")}</li>'
                for cid, c in K.Ch.items() if tid in (c.get("vocab") or [])])
    src = t.get("source") or ""
    also = "".join(f'<span class="kp-chip plain">{esc(x)}</span>' for x in t.get("also_called") or [])
    badges = C.pill("", t.get("grade") or "", "brand") if t.get("grade") else ""
    return (_head(f'Term · {esc(t.get("kind") or "")}', t.get("term") or tid, badges)
            + _sec("What it means", f'<p>{esc(t.get("meaning"))}</p>', "lead")
            + _sec("Also called", f'<div class="kp-chips">{also}</div>' if also else "")
            + _sec("Who coined it", f'<p>{esc(t.get("who"))}</p>' if t.get("who") else "")
            + _sec("Used by", f'<ul class="kp-rows">{"".join(users)}</ul>' if users else "")
            + (_sec("Source", f'<a class="kp-card" href="{esc(src)}" target="_blank" rel="noopener"><span class="kp-ct">{esc(src)}</span>'
                    f'<span class="kp-cm">{EXT}</span></a>') if src.startswith("http") else ""))


def payload(ctx):
    """{"p": {id: html}, "c": {...}, "t": {...}}: every panel, built once. A panel that fails to build says so and the rest still work."""
    K = Ctx(ctx)
    out = {"p": {}, "c": {}, "t": {}}
    for kind, items, fn in (("p", K.P, practice), ("c", K.Ch, challenge), ("t", K.V, term)):
        for id_ in items:
            try:
                out[kind][id_] = fn(K, id_)
            except Exception as e:  # noqa: BLE001
                out[kind][id_] = f'{SPLIT}<p class="empty">This panel failed to build: {esc(type(e).__name__)}: {esc(e)}</p>'
                print(f"PANEL FAILED: {kind}/{id_}: {type(e).__name__}: {e}", file=sys.stderr)
    return out


def text(x):
    """One piece of panel text: a string (`code` between backticks is shown as code), a number the page prints from the code
    (has .html()), a {"code": ...} dict, or a list of those."""
    if isinstance(x, (list, tuple)):
        return "".join(text(p) for p in x)
    if isinstance(x, dict):
        return _item(x)
    if hasattr(x, "html"):
        return x.html()
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(x))


def _item(x):
    """A panel list item: text, or {"label", "code", "note", "href", "e"}: a bold label, code in its own font, a quiet note, a
    link, and `e` = the id of the thing it names (`data-e`), so a test can find it."""
    if not isinstance(x, dict):
        return text(x)
    label = f"<b>{esc(x['label'])}</b> " if x.get("label") else ""
    code = f"<code>{esc(x['code'])}</code>" if x.get("code") else ""
    note = f" · {text(x['note'])}" if x.get("note") else ""
    out = f"{label}{code}{note}" if (x.get("code") or x.get("label")) else text(x.get("text"))
    if x.get("href"):
        out = f'<a href="{esc(x["href"])}">{out}</a>'
    return f'<span data-e="{esc(x["e"])}">{out}</span>' if x.get("e") else out


def _section(s):
    kind, items = s.get("kind") or "bullets", s.get("items") or []
    if kind == "text":
        return "".join(f"<p>{text(i)}</p>" for i in items)
    if kind == "code":  # lines quoted from the code: file:line and the line itself
        return "".join(f'<p class="kp-src"><code>{esc(i[1])}</code><span>{esc(i[0])}</span></p>' for i in items)
    return f'<ul class="kp-ul">{"".join(f"<li>{_item(i)}</li>" for i in items)}</ul>'


def panel(spec):
    """One panel from plain data, for a view whose items are not in the substrate (the system map). spec = {"kicker", "title",
    "badges": [(icon, word, tone)], "sections": [{"title", "kind": bullets | text | code, "items": [...]}]}. Nothing is worked
    out here: the model hands over every line."""
    badges = "".join(C.pill(i, w, t) for i, w, t in spec.get("badges") or [])
    secs = [x for x in spec.get("sections") or [] if x.get("items")]
    body = "".join(_sec(x["title"], _section(x), "lead" if n == 0 else "") for n, x in enumerate(secs))
    return _head(esc(spec.get("kicker")), spec["title"], badges) + body


def panels_script(panels):
    """The page's panel data when it has no substrate behind it: {"m": {key: panel html}}."""
    return ('<script type="application/json" id="kp-data">'
            + json.dumps({"m": {k: panel(v) for k, v in panels.items()}}, ensure_ascii=False).replace("</", "<\\/") + "</script>")


def data_script(ctx):
    return ('<script type="application/json" id="kp-data">' + json.dumps(payload(ctx), ensure_ascii=False).replace("</", "<\\/") + "</script>")


CLOSE = ('<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
         'aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>')
ASIDE = ('<div class="kp-scrim" id="kp-scrim" hidden></div>'
         '<aside class="kp" id="kp" hidden role="dialog" aria-label="Details"><div class="kp-top"><div class="kp-headc"></div>'
         f'<button class="kp-x" type="button" aria-label="Close (Esc)">{CLOSE}</button></div><div class="kp-body"></div></aside>'
         '<div class="kp-tip" id="kp-tip" role="tooltip" hidden></div>')

CSS = """
.kp-scrim{position:fixed;inset:0;z-index:49;background:rgba(16,24,40,.16);opacity:1;transition:opacity .2s ease-out}
.kp-scrim[hidden]{display:block!important;opacity:0;visibility:hidden;pointer-events:none;transition:opacity .2s ease-out,visibility 0s .2s}
.kp{position:fixed;top:0;right:0;bottom:0;z-index:50;width:min(560px,100vw);display:flex;flex-direction:column;background:#FFFFFF;
  box-shadow:-1px 0 0 #EAECF0,-8px 0 24px -4px rgba(16,24,40,.12),-24px 0 56px -8px rgba(16,24,40,.2);
  transition:transform .24s cubic-bezier(.2,.8,.2,1),visibility 0s}
.kp[hidden]{display:flex!important;transform:translateX(104%);visibility:hidden;transition:transform .2s ease-out,visibility 0s .2s}
.kp-top{position:relative;flex:none;padding:24px 72px 16px 32px;background:#FFFFFF;border-bottom:1px solid transparent;transition:border-color .15s}
.kp-top.scrolled{border-bottom-color:#EAECF0}
.kp-x{position:absolute;top:16px;right:20px;width:40px;height:40px;display:grid;place-items:center;border-radius:10px;border:0;background:transparent;
  color:#667085;cursor:pointer;transition:background-color .12s,color .12s}
.kp-x:hover{background:#F2F4F7;color:#15172F}
.kp-x:active{transform:scale(.96)}
.kp-x:focus-visible,.kp a:focus-visible,.kp summary:focus-visible,.kp-row:focus-visible{outline:2px solid #5258FB;outline-offset:2px}
.kp-body{flex:1 1 auto;overflow-y:auto;overscroll-behavior:contain;padding:8px 32px 64px}
.kp-k{margin:0 0 4px;font-size:12px;line-height:1.4;font-weight:500;color:#667085}
.kp h3{margin:0;font-size:22px;line-height:1.25;font-weight:600;letter-spacing:-.015em;color:#15172F;text-wrap:balance}
.kp-b .klog{flex:1 0 100%;margin:0}
.kp-b{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0 0}
.kp .kp-full{margin:0;font-size:12px;line-height:1.5;color:#667085;max-width:60ch}
.kp-s{margin:24px 0 0;padding-top:24px;border-top:1px solid #F2F4F7}
.kp-s.lead{margin-top:16px;padding-top:0;border-top:0}
.kp-s h4{margin:0 0 12px;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#667085}
.kp-s.lead h4{color:#475467}
.kp p{margin:0 0 8px;font-size:13px;line-height:1.6;color:#344054;max-width:62ch;text-wrap:pretty}
.kp-s.lead p{font-size:15px;line-height:1.6;color:#15172F}
.kp p:last-child{margin-bottom:0}
.kp p.kp-dim,.kp .kp-dim{font-size:12px;color:#475467}
.kp-pills,.kp-chips{display:flex;flex-wrap:wrap;gap:8px}
.kp-pills+p,.kp-pills+.kp-dim{margin-top:12px}
.kp-rows,.kp-ul,.kp-lad{list-style:none;margin:0;padding:0}
.kp-ul{list-style:disc;padding-left:20px}.kp-ul li{font-size:13px;line-height:1.6;color:#344054;padding:2px 0}
.kp-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 12px;align-items:center;padding:8px 12px;margin:0 -12px;border-radius:8px;cursor:pointer;
  transition:background-color .12s}
.kp-row:hover{background:#F9FAFB}
.kp-rt{min-width:0;font-size:13px;font-weight:500;line-height:1.4;color:#15172F}
.kp-rd{grid-column:1 / -1;font-size:12px;line-height:1.5;color:#667085;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kp-chks .kp-chk{display:grid;grid-template-columns:minmax(0,1fr);gap:6px;align-items:center;padding:12px 0;margin:0;border-top:1px solid #F2F4F7}
.kp-chks .kp-chk:first-child{border-top:0}
.kp-ck{min-width:0;font-size:13px;line-height:1.4;font-weight:500;color:#15172F;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.kp-cp{display:flex;flex-wrap:wrap;gap:6px}
.kp-cv{grid-column:1 / -1;font-size:12px;color:#667085;font-variant-numeric:tabular-nums}.kp-cv b{color:#15172F;font-weight:600}
.kp-chk .kp-more{grid-column:1 / -1;margin-top:0}
.kp-lad li{display:grid;grid-template-columns:96px minmax(0,1fr);gap:12px;padding:12px;margin:0 -12px;border-radius:10px;font-size:13px;line-height:1.5;color:#344054}
.kp-lad li.me{background:#F5F5FF;box-shadow:inset 0 0 0 1px #E5E8F9}
.kp-lv{font-size:12px;font-weight:600;color:#475467;padding-top:1px}
.kp-lad li.me .kp-lv{color:#272594}
.kp-lt{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:baseline}
.kp-lt details{flex:1 0 100%}
.kp summary{cursor:pointer;font-size:12px;font-weight:500;color:#3C3ED4;border-radius:4px;list-style:none}
.kp summary::-webkit-details-marker{display:none}
.kp summary::before{content:"";display:inline-block;width:6px;height:6px;margin-right:8px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;
  transform:rotate(-45deg);transition:transform .15s ease-out;vertical-align:1px}
.kp details[open]>summary::before{transform:rotate(45deg)}
.kp summary:hover{text-decoration:underline;text-underline-offset:3px}
.kp details>div,.kp details>p{margin-top:8px}
.kp-rec{padding:16px;border-radius:12px;background:#FEFBF5;box-shadow:inset 0 0 0 1px #FDF1D9;border-top:0}
.kp-pair{display:grid;gap:4px;font-weight:500}
.kp-pair i{font-style:normal;display:inline-block;min-width:56px;margin-right:8px;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#B54708}
.kp-cards{display:grid;gap:8px}
.kp-card{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:12px 14px;border-radius:10px;background:#FFFFFF;box-shadow:inset 0 0 0 1px #EAECF0;
  text-decoration:none;color:#15172F;transition:box-shadow .12s,background-color .12s}
a.kp-card:hover{box-shadow:inset 0 0 0 1px #999AFD;background:#F5F5FF}
.kp-ct{min-width:0;font-size:13px;font-weight:500;line-height:1.4;overflow-wrap:anywhere}
.kp-cm{flex:none;display:flex;gap:8px;align-items:center;color:#667085}
.kp-ext{flex:none}
.kp-chip{display:inline-flex;align-items:center;height:28px;padding:0 12px;border-radius:999px;font-size:12px;font-weight:500;color:#344054;background:#FFFFFF;
  box-shadow:inset 0 0 0 1px #D0D5DD;text-decoration:none;cursor:pointer;transition:box-shadow .12s,color .12s,background-color .12s}
a.kp-chip:hover,a.kp-chip:focus-visible{color:#272594;background:#F5F5FF;box-shadow:inset 0 0 0 1px #999AFD}
.kp-chip.plain{cursor:default;background:#F9FAFB;box-shadow:inset 0 0 0 1px #EAECF0}
.kp-call{padding:16px;border-radius:12px;background:#F5F5FF;box-shadow:inset 3px 0 0 #5258FB}
.kp-call span{display:block;margin:0 0 4px;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#3C3ED4}
.kp-call p{margin:0;font-size:15px;line-height:1.55;color:#15172F}
.kp-more{margin-top:12px}
.kp a{color:#3C3ED4}
.kp a.kp-card{color:#15172F}.kp a.kp-chip{color:#344054}.kp a.kp-chip:hover,.kp a.kp-chip:focus-visible{color:#272594}
.kp a.pl{color:#15172F}.kp a.pl:hover{color:#3C3ED4}
.kp-row a.pl::after{content:none}.kp-row a.pl:hover{background:none;box-shadow:none}.kp-row:hover a.pl{color:#3C3ED4}
.kp code{font:400 12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:#15172F;overflow-wrap:anywhere}
.kp-src{display:grid;gap:2px;padding:8px 12px;margin:0 0 8px;border-radius:8px;background:#F9FAFB}
.kp-src span{font-size:12px;color:#667085}
.kp-tip{position:fixed;z-index:70;max-width:280px;padding:8px 12px;border-radius:8px;background:#15172F;color:#FFFFFF;font:400 12px/1.5 Inter,system-ui,sans-serif;
  box-shadow:0 8px 24px rgba(16,24,40,.28);pointer-events:none;opacity:1;transition:opacity .12s}
.kp-tip[hidden]{display:block!important;opacity:0;visibility:hidden}
@media (max-width:560px){.kp-top{padding:20px 64px 12px 20px}.kp-body{padding:8px 20px 48px}.kp-lad li{grid-template-columns:1fr;gap:4px}}
@media (prefers-reduced-motion:reduce){.kp,.kp[hidden],.kp-scrim,.kp-scrim[hidden]{transition:none}}
""" + receipt_kit.css(".kp-m")

JS = """<script>(function(){
var D={};try{D=JSON.parse(document.getElementById('kp-data').textContent)}catch(e){}
var el=document.getElementById('kp'),scrim=document.getElementById('kp-scrim'),head=el.querySelector('.kp-headc'),top=el.querySelector('.kp-top'),
 body=el.querySelector('.kp-body'),tip=document.getElementById('kp-tip'),from=null,SPLIT='<!--kp-->';
function show(html){var i=html.indexOf(SPLIT);head.innerHTML=i>=0?html.slice(0,i):'';body.innerHTML=i>=0?html.slice(i+SPLIT.length):html;
  body.scrollTop=0;top.classList.remove('scrolled');el.hidden=false;scrim.hidden=false}
function remember(){var a=document.activeElement;if(a&&a!==document.body&&!el.contains(a))from=a}
window.kpOpen=function(kind,id){var h=(D[kind]||{})[id];
  show(h||'<p class="kp-k">Not in this run</p><h3>Nothing to show</h3>'+'<!--kp--><p>There is nothing to show for '+kind+'/'+id.replace(/[<>&]/g,'')+'.</p>');remember()};
window.kpOpenHtml=function(html){show(html);remember()};
window.kpClose=function(){if(el.hidden)return;el.hidden=true;scrim.hidden=true;hideTip();document.dispatchEvent(new Event('kp-close'));
  if(from&&document.contains(from)){try{from.focus({preventScroll:true})}catch(e){}}from=null};
function closeToView(){window.kpClose();var v=window.kpView&&window.kpView();if(v&&/^#?[pctm][/]/.test(location.hash)&&history.replaceState)history.replaceState(null,'','#'+v)}
el.querySelector('.kp-x').addEventListener('click',closeToView);
scrim.addEventListener('click',closeToView);
body.addEventListener('scroll',function(){top.classList.toggle('scrolled',body.scrollTop>4)},{passive:true});
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&!el.hidden)closeToView()});
// a card or row marked data-pk="p:<id>" opens its panel; an anchor #p/<id> does the same through the hash
function go(c){var k=c.dataset.pk.split(':');location.hash='#'+k[0]+'/'+k.slice(1).join(':')}
document.addEventListener('click',function(e){
  if(e.target.closest('a,button,summary,input'))return;
  var c=e.target.closest('[data-pk]');if(c)go(c)});
document.addEventListener('keydown',function(e){
  if(e.key!=='Enter'&&e.key!==' ')return;var c=e.target.closest&&e.target.closest('[data-pk]');
  if(!c||e.target!==c)return;e.preventDefault();go(c)});
// a chip with data-tip shows its text in a tooltip on hover and on focus
function hideTip(){tip.hidden=true}
function showTip(t){var r=t.getBoundingClientRect();tip.textContent=t.dataset.tip;tip.hidden=false;
  var w=tip.offsetWidth,h=tip.offsetHeight,x=Math.max(8,Math.min(r.left,innerWidth-w-8)),y=r.bottom+8;if(y+h>innerHeight-8)y=r.top-h-8;
  tip.style.left=x+'px';tip.style.top=y+'px'}
document.addEventListener('mouseover',function(e){var t=e.target.closest&&e.target.closest('[data-tip]');if(t)showTip(t);else hideTip()});
document.addEventListener('focusin',function(e){var t=e.target.closest&&e.target.closest('[data-tip]');if(t)showTip(t);else hideTip()});
document.addEventListener('focusout',hideTip);
})();</script>"""
