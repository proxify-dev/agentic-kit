"""Your setup by area, in the substrate's own grouping (his 11-04 "more in line with the substrate"): the five areas
(disciplines) -> their practices -> the counts that prove each one. On top, a few lines the code writes from the
numbers, each saying what they mean. Each area opens with advice, not a scoreboard (his 11-06 "is this good or bad?
what now?"): a verdict, your strongest practice, your gap and why it matters, and what to try next, all from
v15-result.json verdicts (engine.verdicts); the practice list and the counts behind it fold below.

Where everything comes from, so nothing here is a list of its own:
- areas, practices, each practice's level: v15-result.json disciplines[].practices[] (the engine's final rung)
- which count proves which practice: signal-rows.json (signals.py, from substrate/signals.yaml)
- the counts and who made them happen: inventory.json who_did_it (ids are a contract with the signal map: never renamed here)
A count the signal map doesn't use yet is listed at the end, so what we collect but don't count stays visible. Stdlib only."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card_kit as C  # noqa: E402  how we know: one function for every view
import lvl_tip  # noqa: E402  one tooltip for every level badge
import common  # noqa: E402  the member wording: clean()
import panel_kit as PK  # noqa: E402  a practice name opens the shared side panel
from view_who import CHECK_NOTE, GROUP, card_status, shown_groups  # noqa: E402  one colour per "who"; the built-ins list

ORDER = 4
TITLE = "Your setup, discipline by discipline"
QUESTION = "Each discipline's practices, the level each one reached, and the numbers from your logs behind it."
QUESTION_MEMBER = "Each discipline's practices, who carries each one, and the numbers from your logs behind it."
PROXIFY_ONLY = False

RUNGS = ["L4", "L3", "L2", "L1"]

CSS = """
.v-areas .find{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));grid-auto-flow:dense;gap:12px;margin:0 0 32px}
.v-areas .f{position:relative;display:grid;grid-template-columns:auto 1fr;column-gap:14px;align-items:baseline;background:#FFFFFF;
  border:1px solid #EAECF0;border-radius:10px;padding:14px 40px 14px 16px}
.v-areas .f>b{min-width:64px;font-size:22px;font-weight:600;line-height:1.1;color:#15172F;letter-spacing:-.01em}
.v-areas .f>p{margin:0;font-size:13px;line-height:1.5;color:#344054}
.v-areas .f>details{grid-column:1 / -1}
.v-areas .f:has(details[open]){grid-column:1 / -1;border-color:#5258FB}
.v-areas .q>summary{position:absolute;top:12px;right:12px;width:20px;height:20px;border-radius:50%;list-style:none;display:grid;
  place-items:center;font-size:11px;font-weight:600;color:#667085;border:1px solid #D0D5DD;cursor:pointer}
.v-areas .q>summary::-webkit-details-marker{display:none}
.v-areas .q>summary:hover,.v-areas .q[open]>summary{color:#3C3ED4;border-color:#5258FB}
.v-areas .q dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:12px 0 0;font-size:12px;line-height:1.5}
.v-areas .q dt{font-weight:600;color:#344054}.v-areas .q dd{margin:0;color:#475467;overflow-wrap:anywhere}
.v-areas .def{margin:0 0 8px;font-size:13px;color:#475467;max-width:90ch}
.v-areas .def b{color:#15172F;font-weight:600}
.v-areas .legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin:0 0 16px;font-size:12px;color:#667085}
.v-areas .legend span{display:inline-flex;align-items:center;gap:6px}
.v-areas .dot{flex:none;width:8px;height:8px;border-radius:50%;background:var(--w)}
.v-areas .area{background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;padding:20px 20px 8px;margin:0 0 16px}
.v-areas .ah{display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px 32px;margin:0 0 12px}
.v-areas .ah h3{font-size:17px;font-weight:600;margin:0;color:#15172F}
.v-areas .ah p{margin:2px 0 0;font-size:13px;color:#667085;max-width:62ch}
.v-areas .dist{flex:none;width:300px}
.v-areas .dbar{display:flex;height:8px;border-radius:4px;overflow:hidden;gap:2px;background:#F2F4F7}
.v-areas .dbar i{display:block}
.v-areas .dist p{margin:6px 0 0;font-size:12px;color:#667085}
.v-areas h5{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#667085;margin:16px 0 4px}
.v-areas .pg{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:2px 24px;padding:4px 0 12px}
.v-areas .pg>span{display:flex;gap:10px;align-items:center;font-size:13px;line-height:1.4;color:#15172F;padding:6px 8px;margin:0 -8px;border-radius:8px;transition:background-color .12s}
.v-areas .pg>span:hover{background:#F9FAFB}
.v-areas .pg a.pl:hover{background:none;box-shadow:none}
.v-areas .pg>span>.rung{flex:none}.v-areas .pg>span>span:last-child{flex:none;margin-left:auto}
.v-areas .dbar .r4{background:#3C3ED4}.v-areas .dbar .r3{background:#5258FB}.v-areas .dbar .r2{background:#DDDEFE}.v-areas .dbar .r1{background:#F2F4F7}.v-areas .dbar .r0{background:#D0D5DD}
.v-areas .proof{margin:0 0 12px;border-top:1px solid #EAECF0}
.v-areas .proof>summary{padding:10px 0;font-size:13px;font-weight:500;color:#3C3ED4}
.v-areas .left>summary{font-size:15px;font-weight:600;color:#15172F;padding:4px 0}
.v-areas .prow{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:6px 24px;padding:10px 0;border-top:1px solid #EAECF0}
.v-areas div.pl{display:flex;gap:10px;align-items:flex-start;font-size:14px;font-weight:500;color:#15172F;line-height:1.4}
.v-areas div.pl small{display:block;font-size:12px;font-weight:400;color:#949BAD;margin-top:2px}
.v-areas .cl>summary{display:flex;gap:8px;align-items:baseline;list-style:none;font-size:13px;color:#475467;padding:2px 0}
.v-areas .cl>summary::-webkit-details-marker{display:none}
.v-areas .cl>summary:hover{color:#15172F}
.v-areas .cl>summary .dot{position:relative;top:-1px}
.v-areas .cl>summary b{color:#15172F;font-weight:600;min-width:44px;text-align:right}
.v-areas .cl>summary em{font-style:normal;color:#949BAD;white-space:nowrap}
.v-areas .cl>summary em.warn{color:#B54708}
.v-areas .cl .qm{flex:none;align-self:center;width:16px;height:16px;border-radius:50%;display:grid;place-items:center;font-style:normal;
  font-size:10px;font-weight:600;color:#949BAD;border:1px solid #D0D5DD}
.v-areas .cl>summary:hover .qm,.v-areas .cl[open] .qm{color:#3C3ED4;border-color:#5258FB}
.v-areas .cl dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 16px;margin:4px 0 10px 68px;font-size:12px;line-height:1.5;max-width:90ch}
.v-areas .cl dt{font-weight:600;color:#344054}.v-areas .cl dd{margin:0;color:#475467;overflow-wrap:anywhere}
.v-areas .left{margin:32px 0 0}
.v-areas .left>p{margin:2px 0 12px;font-size:13px;color:#667085;max-width:80ch}
.v-areas .lgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:0 24px}
.v-areas .sumbox{background:#F5F5FF;border:1px solid #DDDEFE;border-left:4px solid #5258FB;border-radius:10px;padding:14px 18px;margin:0 0 20px}
.v-areas .sumbox h2{margin:0 0 6px;font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#3C3ED4}
.v-areas .sumbox p{margin:3px 0;font-size:14px;line-height:1.5;color:#15172F;max-width:100ch}
.v-areas .sumbox p b,.v-areas .adv b{font-weight:600}
.v-areas .tn{margin:12px 0 4px;padding:12px 16px;border-radius:8px;background:#F5F5FF;border:1px solid #D5D5FE;border-left:3px solid #5258FB}
.v-areas .tn p{margin:0}.v-areas .tnh{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600;letter-spacing:.02em;text-transform:uppercase;color:#3C3ED4}
.v-areas .tni{width:14px;height:14px;flex:none}
.v-areas .tnw{margin-top:4px!important;font-size:15px;font-weight:500;color:#15172F;max-width:80ch}
.v-areas .tnhow{margin-top:4px!important;font-size:13px;color:#475467;max-width:80ch}.v-areas .tnhow b{font-weight:600;color:#344054}
.v-areas .vd{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;margin:0 0 10px}
.v-areas .vd>b{display:inline-flex;gap:6px;align-items:baseline;font-size:15px;font-weight:600;padding:2px 10px;border-radius:12px;background:var(--vb);color:var(--vc)}
.v-areas .vd>span{font-size:14px;color:#344054}
.v-areas .vd.strong{--vb:#F6FEF9;--vc:#027A48}.v-areas .vd.partly{--vb:#FDF1D9;--vc:#B54708}
.v-areas .vd.by_hand{--vb:#F5F5FF;--vc:#3C3ED4}.v-areas .vd.cant_tell{--vb:#F2F4F7;--vc:#475467}
.v-areas .adv{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;margin:0 0 12px;font-size:13px;line-height:1.5;color:#344054}
.v-areas .adv dt{font-weight:600;color:#15172F}.v-areas .adv dd{margin:0;max-width:96ch}
.v-areas .adv .rung{margin:0 4px 0 2px;vertical-align:baseline}
.v-areas .adv a{color:#3C3ED4;font-weight:500}
.v-areas .fold{margin:4px 0 12px;border-top:1px solid #EAECF0}
.v-areas .fold>summary{padding:10px 0;font-size:13px;font-weight:500;color:#3C3ED4;cursor:pointer}
@media (max-width:760px){.v-areas .prow{grid-template-columns:1fr}.v-areas .dist{width:100%}}
"""

VERDICT_ICON = {"strong": "✓", "partly": "◐", "by_hand": "✎", "cant_tell": "?"}
# the member's copy shows no level: three plain states (his 11-06 spec)
MEMBER_STATE = {"L4": "your setup does it", "L3": "your setup does it", "L2": "you do it by hand"}


def _verdicts(res):
    """The engine's verdicts for this run; a result file from before they existed gets them computed here."""
    if res.get("verdicts"):
        return res["verdicts"]
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        import engine  # noqa: E402
        return engine.verdicts(res)
    except Exception:  # no engine next to this view: the page keeps the practice list, without the advice
        return {}


def _you(s):
    """Substrate sentences say "they"; this page talks to the reader. Sentences that name a level are dropped."""
    s = common.clean(s)
    for pat, to in common._GENERIC:
        s = pat.sub(to, s)
    return s.replace("the person", "you").replace("The person", "You")


def _n(x):
    return f"{int(x):,}"


def _pct(a, b):
    return round(100 * a / b) if b else 0


def _rung_class(r):
    return {"L4": "r4", "L3": "r3", "L2": "r2", "L1": "r1"}.get(r or "", "r0")


def _link(esc, l):
    """A learn link: a playbook page, an outside page, or a booking link; plain text when it has no address."""
    if not l:
        return ""
    ref, title = l.get("ref") or "", esc(l.get("title") or l.get("ref") or "")
    href = PK.PLAYBOOK + ref if l.get("kind") == "playbook" else (ref if ref.startswith("http") else None)
    return f' <a href="{esc(href)}" target="_blank" rel="noopener">{title}</a>' if href else f" {title}"


def _next_text(n):
    """What to try, as one sentence from an engine "next": a climb names the step, a spread says where to use it."""
    t = _next_sentence(n)
    return t[:1].upper() + t[1:]


def _next_sentence(n):
    if n["kind"] == "fallback":
        return "read the playbook for this area, or book a call to choose a step together."
    if n["kind"] == "spread":
        return f"use \u201c{_you(n.get('short_name') or n['title'])}\u201d where \u201c{_you(n['problem'])}\u201d still shows."
    step = fit_line(_you(n.get("step")))
    return f"{_you(n.get('short_name') or n['title'])}" + (f": {step}" if step else ".")


def fit_line(text, limit=190):
    """First sentences of a text that fit one short paragraph."""
    out = ""
    for sent in re.split(r"(?<!\be\.g)(?<!\bi\.e)(?<=[.!?])\s+(?=[A-Z])", text or ""):
        if len(out) + len(sent) + 1 > limit:
            break
        out = f"{out} {sent}".strip()
    return out


def summary_box(esc, V, member):
    """Three lines at the top: the strongest area, the gap that matters most, the first step."""
    s = (V or {}).get("summary") or {}
    if not s:
        return ""
    rows = []
    st = s.get("strongest")
    rows.append(f'<p><b>Strongest area:</b> {esc(st["name"])} \u2014 {esc(V["disciplines"][st["area"]]["says"])}</p>' if st else
                "<p><b>Strongest area:</b> too little in your logs to say yet: a question for a call.</p>")
    g = s.get("gap")
    if g:
        why = _you(g.get("why"))
        rows.append(f'<p><b>Biggest gap:</b> {esc(g["name"])}: {esc(g["short_name"])}'
                    + (f' \u2014 why it matters: {esc(why)}' if why else "") + "</p>")
    else:
        rows.append("<p><b>Biggest gap:</b> none we could see: everything we read is handled by your setup.</p>")
    n = s.get("next")
    if n:
        rows.append(f'<p><b>Try first:</b> {esc(_next_text(n))}{_link(esc, n.get("link"))}'
                    + (f'<br><span class="how">How: {esc(fit_line(n["how"], 220))}</span>' if n.get("how") else "") + '</p>')
    return '<div class="sumbox"><h2>In short</h2>' + "".join(rows) + "</div>"


# Phosphor "arrow-right" (regular), the brand's icon set
ARROW = ('<svg class="tni" viewBox="0 0 256 256" fill="currentColor" aria-hidden="true"><path d="M221.66,133.66l-72,72a8,8,0,0,1'
         '-11.32-11.32L196.69,136H40a8,8,0,0,1,0-16H196.69L138.34,61.66a8,8,0,0,1,11.32-11.32l72,72A8,8,0,0,1,221.66,133.66Z"/></svg>')


def area_advice(esc, a, member, chip_of):
    """The top of an area card: verdict word and sentence, then your strongest, your gap and why it matters, try next.
    `chip_of(rung, pid)`: the level badge (Proxify) or the plain state word (member)."""
    if not a:
        return ""
    out = [f'<div class="vd {a["verdict"]}"><b><i aria-hidden="true">{VERDICT_ICON[a["verdict"]]}</i> {esc(a["word"])}</b>'
           f'<span>{esc(a["says"])}</span></div><dl class="adv">']
    st, g, n = a.get("strongest"), a.get("gap"), a.get("next")
    if st:
        out.append(f'<dt>Your strongest</dt><dd><b>{esc(st["short_name"])}</b> {chip_of(st["rung"], st["practice"])}'
                   f'<span title="{esc(_you(st.get("reason_full")))}">\u2014 {esc(_you(st["reason"]))}</span></dd>')
    else:
        out.append("<dt>Your strongest</dt><dd>Nothing read here yet: a question for a call.</dd>")
    if g:
        out.append(f'<dt>Your gap</dt><dd><b>{esc(g["short_name"])}</b> {chip_of(g["rung"], g["practice"])}'
                   + (f'\u2014 why it matters: {esc(_you(g["why"]))}' if g.get("why") else "") + "</dd>")
    elif st:
        out.append("<dt>Your gap</dt><dd>None we could see: every practice we read here is handled by your setup.</dd>")
    else:
        out.append("<dt>Your gap</dt><dd>Can't say yet: nothing was read here.</dd>")
    out.append("</dl>")
    if n:  # the action, set apart from the reading above it (a tester 11-06: "It has 'try next', but somehow not highlighted enough")
        out.append(f'<div class="tn"><p class="tnh">{ARROW}Try next</p><p class="tnw">{esc(_next_text(n))}{_link(esc, n.get("link"))}</p>'
                   + (f'<p class="tnhow"><b>How:</b> {esc(fit_line(n["how"], 220))}</p>' if n.get("how") else "")  # today's technique for that step
                   + "</div>")
    return "".join(out)


def findings(items, res, proven_areas, member=False, headless=None):
    """Each pattern the numbers show: {big, text, source, math, cant}. A finding is left out when its numbers are missing.
    His 11-04 review: no headline that only measures our own blind spot (the untraceable sub-agent calls), and never
    compare two things that aren't the same unit as if they were a share (headless runs vs sessions typed)."""
    g, out = items.get, []
    typed, code, alone = g("you.typed_messages"), g("programs.runs_started_by_code"), g("programs.runs_unattended")
    if typed and code and typed.get("chats") and code["n"]:
        text = f"headless runs (claude -p or the Agent SDK): scripts and batch jobs, not sessions. You typed in {_n(typed['chats'])} sessions."
        if alone and alone["n"]:
            text += f" {_n(alone['n'])} of the runs began while you were away (nothing typed by you in the 30 minutes before, or a scheduled job started them)."
        nolog = (headless or {}).get("scheduled_runs_without_a_log") or 0
        if nolog:  # launchd's count since the job was loaded: never added into the runs above (another unit and window)
            text += (f" Plus {_n(nolog)} run{'s' if nolog != 1 else ''} of your scheduled jobs that keep no session log, "
                     "counted since the job was loaded.")
        out.append({"big": _n(code["n"]), "text": text,
                    "source": f"Runs: {code['proof']} Sessions: {typed['proof']}",
                    "math": f"{_n(code['n'])} headless runs; {_n(typed['chats'])} sessions with a message you typed. Not divided: "
                            "a run and a session are different units.",
                    "cant": "What each run was for, or how many runs make up one job: one batch job can start thousands."})
    both, peak = g("you.chats_at_once"), g("you.most_chats_in_10_min")
    if both and both.get("of") and both["n"]:
        text = "of your messages were typed while 3 or more of your sessions were active"
        text += f". At the busiest, {_n(peak['n'])} sessions within 10 minutes." if peak and peak["n"] else "."
        out.append({"big": f"{_pct(both['n'], both['of'])}%", "text": text,
                    "source": both["proof"] + (f" Busiest 10 minutes: {peak['proof']}" if peak else ""),
                    "math": f"{_n(both['n'])} ÷ {_n(both['of'])} messages you typed = {_pct(both['n'], both['of'])}%.",
                    "cant": "Whether the other sessions were doing useful work at that moment, or just open."})
    esc, refused = g("you.esc_interrupts"), g("you.steps_refused")
    if esc and esc.get("chats") and typed and typed.get("chats"):
        text = "of your sessions had you interrupt Claude (Esc)"
        text += f". You also rejected {_n(refused['n'])} tool calls." if refused and refused["n"] else "."
        out.append({"big": f"{_pct(esc['chats'], typed['chats'])}%", "text": text,
                    "source": esc["proof"] + (f" Tool calls you rejected: {refused['proof']}" if refused else ""),
                    "math": f"{_n(esc['chats'])} sessions with at least one stop ÷ {_n(typed['chats'])} sessions you typed in "
                            f"= {_pct(esc['chats'], typed['chats'])}% ({_n(esc['n'])} stops in all).",
                    "cant": "Why you stopped it: a wrong turn, a change of mind, or a faster idea."})
    hand, auto, early = g("you.manual_compactions"), g("claude.auto_compactions"), g("setup.past_work_reached_early")
    total = (hand or {}).get("n", 0) + (auto or {}).get("n", 0)
    if total:
        text = "compactions were done by you, by hand"
        text += f". Earlier work was loaded into context early in {_pct(early['n'], early['of'])}% of new sessions." if early and early.get("of") else "."
        out.append({"big": f"{_n((hand or {}).get('n', 0))} of {_n(total)}", "text": text,
                    "source": " ".join(x["proof"] for x in (hand, auto, early) if x),
                    "math": f"{_n((hand or {}).get('n', 0))} by hand + {_n((auto or {}).get('n', 0))} automatic = {_n(total)} compactions"
                            + (f"; {_n(early['n'])} ÷ {_n(early['of'])} new sessions = {_pct(early['n'], early['of'])}%." if early and early.get("of") else "."),
                    "cant": "Whether the right things were kept. \"Loaded into context\" means it was in Claude's context, not that Claude used it."})
    # Proxify only: a tile that counts our own blind spot reads to a member as a score of zero (11-04 rule above;
    # a tester 11-06, "Duplicated numbers are especially irritating - cognitive load")
    for d in [] if member else res.get("disciplines") or []:
        if d["id"] not in proven_areas:
            out.append({"big": "0", "text": f"of your numbers say anything about {d['name']}. What we say about it comes from the AI's reading of your setup only.",
                        "source": "substrate/signals.yaml, the map from a count to the practice it proves, as applied in signal-rows.json.",
                        "math": f"0 of the counts we use to place a practice point to a {d['name']} practice." if member
                            else f"0 of the counts that set a level point to a {d['name']} practice.",
                        "cant": "That you don't do it: checks and reviews can happen where these logs don't reach."})
    return out


def render(ctx):
    esc = ctx["esc"]
    res, inv = ctx.get("v15_result") or {}, ctx.get("inventory") or {}
    wd = inv.get("who_did_it")
    if not res.get("disciplines"):
        return '<p class="empty">Not in this run: no v15-result.json.</p>'
    items, who_of = {}, {}
    groups, hidden = shown_groups(wd or {})
    st = card_status()
    wd = {**(wd or {}), "groups": groups}
    for g in groups:
        for i in g["items"]:
            items[i["id"]], who_of[i["id"]] = i, g["id"]
    path = os.path.join(ctx.get("run") or "", "signal-rows.json")
    rows = (json.load(open(path)).get("practices") or {}) if os.path.exists(path) else {}
    area_of = {p["id"]: d["id"] for d in res["disciplines"] for p in d["practices"]}
    proven = {area_of.get(pid) for pid, r in rows.items() if r.get("signals")}
    used = {s["id"] for r in rows.values() for s in r.get("signals") or []}

    V = _verdicts(res)
    out = [summary_box(esc, V, bool(ctx.get("member"))), '<div class="find">']
    for f in findings(items, res, proven, bool(ctx.get("member")), inv.get("headless")):
        head, _, more = f["text"].partition(". ")  # one sentence on the card; the rest opens with the "?"
        out.append(f'<div class="f"><b>{esc(f["big"])}</b><p>{esc(head.rstrip("."))}</p><details class="q"><summary title="Where this comes from" '
                   f'aria-label="Where this comes from">?</summary><dl>{f"<dt>More</dt><dd>{esc(more)}</dd>" if more else ""}<dt>From</dt><dd>{esc(f["source"])}</dd><dt>Math</dt>'
                   f'<dd>{esc(f["math"])}</dd><dt>Can\'t tell</dt><dd>{esc(f["cant"])}</dd></dl></details></div>')
    out.append("</div>")

    titles = {g["id"]: g["title"] for g in (wd or {}).get("groups") or []}
    out.append('<p class="def"><b>Sub-agent:</b> a second Claude your session starts to do one piece of the work and report back.</p>')
    out.append('<div class="legend"><span>Colour = who made it happen:</span>' + "".join(
        f'<span><i class="dot" style="--w:{GROUP[k]}"></i>{esc(titles.get(k, k))}</span>' for k in GROUP if k in titles) + "</div>")

    def count_line(sid, sig=None):
        """One count: who (dot), the number, what it is, and a "?" that opens where it comes from, the math, what it can't tell."""
        i = items.get(sid)
        if not i:
            return ""
        n, chats, of = i["n"], i.get("chats"), i.get("of")
        extra = f"in {_n(chats)} sessions" if chats and chats != n else (f"of {_n(of)}" if of else "")
        math = (f"{_n(n)} ÷ {_n(of)} = {_pct(n, of)}%." if of else
                f"{_n(n)} times, in {_n(chats)} different sessions." if chats and chats != n else f"{_n(n)}, counted once each.")
        rows_ = [("What it shows", C.you_text(sig.get("says"))) if sig and sig.get("says") else None, ("From", i.get("proof")), ("Math", math),
                 ("Can't tell", i.get("cant_tell")), ("Note", CHECK_NOTE) if st.get(sid) == "check" else None]
        dl = "".join(f"<dt>{k}</dt><dd>{esc(v)}</dd>" for k, v in filter(None, rows_) if v)
        return (f'<details class="cl q2"><summary><i class="dot" style="--w:{GROUP.get(who_of.get(sid, ""), "#667085")}"></i>'
                f'<b>{_n(n)}</b><span>{esc(i["label"])}</span>{f"<em>{extra}</em>" if extra else ""}'
                f'{"<em class=warn>partly your own tools</em>" if st.get(sid) == "check" else ""}<i class="qm" aria-label="Where this comes from">?</i>'
                f'</summary><dl>{dl}</dl></details>')

    member = bool(ctx.get("member"))
    evs = {e["id"]: e for e in res.get("evidence") or [] if e.get("kind") == "practice" and e.get("id")}
    RW, subp = ctx["RUNG_WORD"], lvl_tip._practices()

    def chip(p):
        """The level badge; the member's copy says who carries the practice in words and shows no level (his 10-26 ruling)."""
        r = p.get("rung")
        if member:
            return f'<span class="rung word {_rung_class(r)}">{esc(RW.get(r) or "Not yet")}</span>'
        return f'<span class="rung {_rung_class(r)}"{lvl_tip.attrs(p["id"], r)}>{esc(r or "–")}</span>'

    def chip_of(r, pid):
        """Proxify: the level badge. Member: three plain states and no level."""
        if member:
            word = MEMBER_STATE.get(r, "not seen yet")
            return f'<span class="rung word {_rung_class(r if r in MEMBER_STATE else None)}">{esc(word)}</span>'
        return f'<span class="rung {_rung_class(r)}"{lvl_tip.attrs(pid, r)}>{esc(r or "not found")}</span>'

    for d in res["disciplines"]:
        ps = d["practices"]
        dist = {r: sum(1 for p in ps if p.get("rung") == r) for r in RUNGS}
        none = len(ps) - sum(dist.values())
        bar = "".join(f'<i class="{_rung_class(r)}" style="flex:{dist[r]}"></i>' for r in RUNGS if dist[r])
        bar += f'<i class="r0" style="flex:{none}"></i>' if none else ""
        rname = (lambda r: RW.get(r, r).lower()) if member else (lambda r: f"at {r}")
        words = " \u00b7 ".join([f"{dist[r]} {rname(r)}" for r in RUNGS if dist[r]] + ([f"{none} not yet"] if none else []))
        adv = area_advice(esc, ((V or {}).get("disciplines") or {}).get(d["id"]), member, chip_of)
        out.append(f'<section class="area"><div class="ah"><div><h3>{esc(d["name"])}</h3><p>{esc(d.get("question"))}</p></div></div>'
                   + adv + f'<details class="fold"><summary>The {len(ps)} practices in this area</summary>'
                   f'<div class="dist"><div class="dbar" title="{esc(words)}">{bar}</div><p>{esc(words)}</p></div>')
        order = sorted(ps, key=lambda p: (RUNGS.index(p["rung"]) if p.get("rung") in RUNGS else 9))
        counted = [p for p in order if rows.get(p["id"], {}).get("signals")]
        # the answer first: every practice with its level, one line each; the numbers behind it open on a click
        out.append('<div class="pg">' + "".join(
            f'<span>{chip(p)}{PK.link("p", p["id"], subp.get(p["id"], {}).get("short_name") or p["name"])}'
            + (f'<span title="No count from your sessions and nothing you built backs this one yet">{C.pill(C.HOW_NONE[0], "not counted yet (a question for a call)", "warn")}</span>'
               if C.how(evs.get(p["id"], {}).get("fact"), evs.get(p["id"], {}).get("not_counted"))[0] == C.HOW_NONE[0] else "")
            + "</span>" for p in order) + "</div>"
            + (C.log_line() if any(not p.get("rung") for p in order) else ""))
        if counted:
            out.append(f'<details class="proof"><summary>How we know ({len(counted)} practices)</summary>')
            for p in counted:
                r = rows[p["id"]]
                note = (f'<small title="Your numbers alone prove {esc(r["rung"])}; the AI\'s reading of your setup puts it at {esc(p["rung"])}.">'
                        f'numbers alone: {esc(r["rung"])}</small>' if not member and r.get("rung") and p.get("rung") and r["rung"] != p["rung"] else "")
                out.append(f'<div class="prow"><div class="pl">{chip(p)}'
                           f'<div>{PK.link("p", p["id"], p["name"])}{note}</div></div><div class="pc">'
                           + "".join(count_line(s["id"], s) for s in r["signals"]) + "</div></div>")
            out.append("</details>")
        out.append("</details></section>")

    left = [i for g in (wd or {}).get("groups") or [] for i in g["items"] if i["id"] not in used]
    if left:
        out.append(f'<details class="left"><summary>Counted, but no practice uses them yet ({len(left)})</summary>'
                   '<p>We collect these from your logs, but none of them is used to place a practice yet.'
                   + ("" if ctx.get("member") else " The substrate\'s signal map decides which should.")
                   + (f' {hidden} more are left out while they\'re rebuilt to read only what Claude Code itself records.' if hidden else "")
                   + '</p>'
                   '<div class="lgrid">' + "".join(count_line(i["id"]) for i in left) + "</div></details>")
    return "".join(out)
