"""Share with Proxify: the member sends the summary of this page to Proxify, to get their level confirmed on an
evaluation call. One email field and one Send button; nothing leaves the laptop until they press it.

What is sent is exactly share_payload.payload(run) (ids and levels, the same as `run.py share`) plus the email they type.
It goes to Proxify's receiver (RECEIVER, a Google Apps Script web app on Proxify's account): it keeps the file, posts it
to Proxify's Slack by email, and emails the member the booking link, so only the owner of that inbox can book.
Why not Slack itself (11-06): no Slack link can fill in a message or attach a file, and nothing may be installed in the
workspace; a paste of the summary arrived as an unreadable wall.

This view never prints a level: "Your level" is the tab for that, and "What Proxify gets" says the summary holds it. The
kinds of things built and used lead the summary. The line per practice, and the questions left for the call, sit one
click away: they are sent, so they must be shown, but they come from the practice read, which the level no longer rests
on and which can contradict Review (11-11). The file they can download holds the levels, as the share file always has.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "..")
sys.path.insert(0, ENGINE)
import share_payload  # noqa: E402
import things as level_things  # noqa: E402
import wording  # noqa: E402  the one plural

ORDER = 45
TITLE = "Get your agentic engineering level confirmed"
QUESTION = ("Send Proxify a short summary of this page. We set it beside your evaluation call, so the call can spend its time "
            "on what your laptop can't show.")
PROXIFY_ONLY = False

# Proxify's receiver: POST {email, share} as text/plain (a simple request, so a page opened from a file can send it)
RECEIVER = "https://script.google.com/macros/s/AKfycbzni9dnslYuSD8l9VegTBKRTiJnNiMB8AwE3F7ipx0rzoxUOq3HRNpnCHwXEh3CH-HNqw/exec"
PROXIFY_EMAIL = re.compile(r"@proxify\.(io|dev)$", re.I)  # prefill only an address Proxify gave them: anything else is a guess

ICON = {
    "send": '<path d="M14 2 7 9M14 2l-4.5 12L7 9 2 6.5z"/>',
    "yes": '<path d="m3.5 8.5 3 3 6-7"/>',
    "no": '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/>',
    "warn": '<path d="M8 2 1.5 13.5h13zM8 6.5v3M8 11.5v.01"/>',
    "badge": '<path d="M8 1.5 9.8 3l2.3-.2.5 2.3 2 1.2-1 2.1 1 2.1-2 1.2-.5 2.3-2.3-.2L8 15.5 6.2 14l-2.3.2-.5-2.3-2-1.2 1-2.1-1-2.1 '
             '2-1.2.5-2.3 2.3.2z"/><path d="m5.8 8.2 1.5 1.5 3-3"/>',
}


def icon(name, size=16):
    return (f'<svg viewBox="0 0 16 16" width="{size}" height="{size}" fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICON[name]}</svg>')


# the header button (frame.py puts it beside Search when this view is on the page)
BUTTON = f'<a class="rp-share" href="#share">{icon("badge", 14)}<span>Get your level confirmed</span></a>'

CSS = """
.v-share h4.sh-h{font-size:14px;font-weight:600;margin:24px 0 8px;color:#15172F}
.v-share details.sh-lines{margin-top:16px}
.v-share details.sh-lines>summary{font-size:13px;font-weight:500;color:#3C3ED4;width:max-content}
.v-share .sh-older{margin:8px 0;font-size:13px;color:#475467;max-width:76ch}
.rp-share{flex:none;display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 12px;border-radius:8px;
  background:#5258FB;color:#FFFFFF;font-weight:500;font-size:14px;text-decoration:none;white-space:nowrap}
.rp-share:hover{background:#3C3ED4}
.rp-share.sent{background:transparent;color:#039855;box-shadow:inset 0 0 0 1px #D0D5DD}
@media (max-width:720px){.rp-share span{display:none}.rp-share{padding:0 9px}}
.v-share .sh-steps{list-style:none;margin:8px 0 0;padding:0;display:grid;grid-template-columns:repeat(3,1fr);gap:12px;counter-reset:s}
.v-share .sh-steps li{position:relative;padding:16px 16px 16px 52px;border:1px solid #EAECF0;border-radius:12px;background:#FFFFFF;counter-increment:s}
.v-share .sh-steps li::before{content:counter(s);position:absolute;left:16px;top:16px;width:24px;height:24px;border-radius:50%;
  display:grid;place-items:center;font-size:12px;font-weight:600;background:#EEEFFE;color:#3C3ED4}
.v-share .sh-steps li.done::before{content:"\\2713";background:#039855;color:#FFFFFF}
.v-share .sh-steps li.now{border-color:#5258FB;box-shadow:0 0 0 3px #EEEFFE}
.v-share .sh-steps b{display:block;font-weight:600;color:#15172F;margin-bottom:4px}
.v-share .sh-steps p{margin:0;color:#475467}
.v-share .sh-form{margin:20px 0 0;max-width:620px}
.v-share .sh-form label{display:block;font-weight:500;color:#15172F;margin:0 0 6px}
.v-share .sh-row{display:flex;gap:8px}
.v-share .sh-row input{flex:1;min-width:0;height:44px;padding:0 14px;border-radius:10px;border:1px solid #D0D5DD;background:#FFFFFF;
  color:#15172F;font:inherit}
.v-share .sh-row input:focus{outline:none;border-color:#5258FB;box-shadow:0 0 0 3px #EEEFFE}
.v-share .sh-row input[aria-invalid=true]{border-color:#DC6803}
.v-share .sh-go{display:inline-flex;align-items:center;gap:8px;height:44px;padding:0 20px;border:0;border-radius:10px;cursor:pointer;
  background:#5258FB;color:#FFFFFF;font:inherit;font-weight:600}
.v-share .sh-go:hover{background:#3C3ED4}.v-share .sh-go[disabled]{opacity:.7;cursor:progress}
.v-share .sh-hint{margin:8px 0 0;color:#475467}.v-share .sh-hint.bad{color:#B54708}
.v-share .sh-link{background:none;border:0;padding:0;cursor:pointer;color:#3C3ED4;font:inherit;text-decoration:underline;text-underline-offset:3px}
.v-share .sh-status{margin:12px 0 0;padding:12px 16px;border-radius:10px;background:#F6FEF9;border:1px solid #C4F3D8;color:#15172F}
.v-share .sh-status.bad{background:#FEFBF5;border-color:#FDF1D9}
.v-share .sh-status p{margin:0}.v-share .sh-status p+p{margin-top:6px;color:#475467}
.v-share .sh-two{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:32px}
.v-share .sh-two section{border:1px solid #EAECF0;border-radius:12px;padding:16px 20px;background:#FFFFFF}
.v-share h3{font-size:18px;font-weight:600;margin:0 0 12px;color:#15172F}
.v-share .sh-two ul{list-style:none;margin:0;padding:0}
.v-share .sh-two li{display:flex;gap:10px;padding:6px 0;color:#344054}.v-share .sh-two li svg{flex:none;margin-top:3px}
.v-share .sh-yes svg{color:#039855}.v-share .sh-no svg{color:#667085}
.v-share .sh-warn{display:flex;gap:12px;margin-top:12px;padding:14px 16px;border-radius:12px;background:#FEFBF5;border:1px solid #FDF1D9}
.v-share .sh-warn svg{flex:none;color:#DC6803;margin-top:3px}.v-share .sh-warn p{margin:0}
.v-share .sh-sum{margin-top:40px}.v-share .sh-sum>p{color:#475467;margin:-4px 0 16px}
.v-share .sh-tbl{width:100%;border-collapse:collapse}
.v-share .sh-tbl td{padding:8px 0;border-top:1px solid #EAECF0;color:#344054}.v-share .sh-tbl td:last-child{text-align:right}
.v-share .sh-tbl th{text-align:left;padding:20px 0 6px;font-size:12px;font-weight:500;letter-spacing:.06em;text-transform:uppercase;color:#667085}
.v-share .sh-tbl .rung{display:inline-flex;padding:2px 8px;border-radius:999px;font-size:12px}
.v-share .sh-cols{display:grid;grid-template-columns:1fr 1fr;gap:12px 32px;margin-top:28px}
.v-share .sh-cols h4{font-size:14px;font-weight:600;margin:0 0 8px;color:#15172F}
.v-share .sh-cols p{margin:0 0 10px;color:#475467}
.v-share ul.sh-old{list-style:none;margin:0;padding:0;display:grid;gap:6px;font-size:13px;color:#344054}
.v-share ul.sh-old small{display:block;color:#667085;font-size:12px}
.v-share .sh-chips{list-style:none;display:flex;flex-wrap:wrap;gap:6px;margin:0;padding:0}
.v-share .sh-chips li{padding:3px 10px;border-radius:999px;background:#F2F4F7;color:#344054;font-size:12px}
.v-share .sh-foot{margin-top:28px;color:#667085;font-size:12px}
@media (max-width:820px){.v-share .sh-steps,.v-share .sh-two,.v-share .sh-cols{grid-template-columns:1fr}}
@media (max-width:520px){.v-share .sh-row{flex-direction:column}.v-share .sh-go{justify-content:center}}
"""

# The new read's lines (share_payload NEW_KEYS), in the page's words. They are printed only when Send carries them:
# the tab shows what is sent, never more (share_payload.RECEIVER_TAKES_NEW_KEYS).
PART_NAME = {"rules": "Instructions the agent starts with", "instructions": "Instructions the agent starts with",
             "corrections": "Corrections that stick", "memory": "Memory a new session reads"}
SIGN_NAME = {"endless-sessions": "Sessions that only compact automatically", "session-scope": "How long sessions stay open",
             "starting-context": "How full a session starts", "unused-setup": "Setup that isn't used",
             "rules-only-grow": "An AGENTS.md that only grows"}


# the five lines of the summary that still come from the practice read (share_payload: practices, built_areas,
# evals_shown, call_questions, certainty). The page's level and parts come from the read of things and of the files, so
# these can differ from the Review and Your level tabs: each says so, in the same words.
OLDER_NOTE = "from the older read; may differ from this page"
SURE_WORD = {"high": "Very sure", "medium": "Fairly sure", "low": "Not very sure yet"}


def older_lines(esc, sh, disc):
    """The five lines that come from the older read, each with the same note (empty when the page shows the older read itself)."""
    if sh.get("level_model") != "things":
        return ""
    areas = [disc.get(a, a) for a in sh.get("built_areas") or []]
    qs = [g for g in sh.get("call_questions") or [] if g]
    sure = SURE_WORD.get((sh.get("certainty") or {}).get("level"), "Not read")
    n_seen = sum(1 for p in (sh.get("practices") or {}).values() if p.get("rung") in RUNG_CLASS)
    lines = [("One line for each habit", f"{len(sh.get('practices') or {})} habits, {n_seen} seen"),
             ("The areas you built in", ", ".join(areas) or "none"),
             ("Evals on what you built", "shown" if sh.get("evals_shown") else "not shown"),
             ("Questions left for your call", str(len(qs))),
             ("How sure the reading is", sure)]
    return ('<h4 class="sh-h">Lines from the older read</h4><ul class="sh-old">'
            + "".join(f"<li><b>{esc(k)}</b>: {esc(v)}<small>{esc(OLDER_NOTE)}</small></li>" for k, v in lines) + "</ul>")


def part_word(g, ran):
    """One word for a part's verdict, the same rule the level uses: shown only when the code proved the structure, the
    agent read it as shown and its quote was found."""
    if level_things.part_state(g)[0] == "shown":   # things.part_state owns "a part is shown"
        return "r3", "Shown"
    if not ran or (g.get("judged") == "can't tell" and not g.get("structure")):
        return "r0", "Couldn't read"
    if g.get("structure") or g.get("judged") in ("shown", "partly"):
        return "r2", "Partly"
    return "r0", "Not shown"


def new_lines(esc, sent):
    """The read the level rests on, as the summary holds it: a verdict per part, the signs from Claude Code's records by
    name, and each thing with its state. No level word (the tab beside this one shows the level)."""
    if "things" not in sent and "setup_read" not in sent:
        return ""
    sr = sent.get("setup_read") or {}
    parts = "".join(f'<tr><td>{esc(PART_NAME.get(g["id"], g["id"].replace("-", " ")))}</td>'
                    f'<td><span class="rung {c}">{esc(w)}</span></td></tr>'
                    for g in sr.get("parts") or [] for c, w in [part_word(g, sr.get("ran"))])
    signs = [SIGN_NAME.get(i, i.replace("-", " ")) for i in (sr.get("holds") or []) + (sr.get("cards") or [])]
    try:
        names, order = {t["id"]: t["name"] for t in level_things.THINGS}, list(level_things.STATE_RANK)
    except Exception:  # noqa: BLE001 - without the list the ids show as they are
        names, order = {}, []
    by_state = {}
    for tid, t in (sent.get("things") or {}).items():
        by_state.setdefault(t.get("state"), []).append((tid, names.get(tid, tid.replace("-", " "))))
    groups = "".join(f'<div><h4>{esc(st[:1].upper() + st[1:])}</h4><ul class="sh-chips">'
                     + "".join(f'<li data-thing="{esc(tid)}" data-state="{esc(st)}">{esc(n)}</li>' for tid, n in ns) + "</ul></div>"
                     for st, ns in sorted(by_state.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99))
    out = []
    if parts:
        out.append('<h4 class="sh-h">What the read of your files says</h4><p class="sh-older">One word per part. The lines '
                   'it quoted and the reasons stay on your laptop.</p>' + f'<table class="sh-tbl">{parts}</table>')
    elif "setup_read" in sent:
        out.append('<h4 class="sh-h">What the read of your files says</h4><p class="sh-older">The read of your files '
                   "didn't finish in this run, and the summary says so.</p>")
    if signs:
        out.append('<h4 class="sh-h">Seen in Claude Code\'s records</h4><ul class="sh-chips">'
                   + "".join(f"<li>{esc(n)}</li>" for n in signs) + "</ul>")
    if groups:
        out.append('<h4 class="sh-h">The parts of your setup, and whose each is</h4>' + f'<div class="sh-cols">{groups}</div>')
    return "".join(out)


# the page's own words for who carries a practice (common.RUNG_WORD), and the rung colours the page already uses
RUNG_CLASS = {"L4": "r4", "L3": "r3", "L2": "r2", "L1": "r1"}

JS = r"""<script>(function(){
var d=JSON.parse(document.getElementById('sh-data').textContent),f=document.getElementById('sh-form'),inp=document.getElementById('sh-email'),
 go=document.getElementById('sh-go'),hint=document.getElementById('sh-hint'),st=document.getElementById('sh-status'),
 steps=document.querySelectorAll('#sh-steps li'),corner=document.querySelector('.rp-top .rp-share'),HINT=hint.innerHTML,KEY='agentic-setup-sent-to';
function mark(n){steps.forEach(function(li,i){li.classList.toggle('done',i<n);li.classList.toggle('now',i===n)})}
function say(h,bad){st.innerHTML=h;st.classList.toggle('bad',!!bad);st.hidden=false}
function esc(t){return t.replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function sent(email){mark(2);f.hidden=true;if(corner){corner.classList.add('sent');corner.lastElementChild.textContent='Summary sent'}
 say('<p><b>Sent.</b> Your summary reached Proxify as a file.</p><p>The link to book your evaluation call is on its way to '+esc(email)+'. Nothing else left your laptop.</p>')}
function busy(on){go.disabled=on;go.lastElementChild.textContent=on?'Sending…':'Send to Proxify'}
function bad(msg){inp.setAttribute('aria-invalid','true');hint.classList.add('bad');hint.textContent=msg;inp.focus()}
function download(){var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([d.file+'\n'],{type:'application/json'}));
 a.download='share-with-proxify.json';a.click()}
hint.addEventListener('click',function(e){if(e.target.id==='sh-dl')download()});
f.addEventListener('submit',function(e){e.preventDefault();var email=inp.value.trim();
 if(!/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(email)){bad("That email doesn't look complete. Check it and press Send again.");return}
 inp.removeAttribute('aria-invalid');hint.classList.remove('bad');hint.innerHTML=HINT;
 busy(true);st.hidden=true;
 fetch(d.receiver,{method:'POST',body:JSON.stringify({email:email,share:d.share}),redirect:'follow'})
 .then(function(r){return r.json()}).then(function(j){busy(false);
   if(j.ok){try{localStorage.setItem(KEY,email)}catch(e){}sent(email);return}
   if(j.error==='email'){bad("Proxify couldn't use that email. Check it and press Send again.");return}
   if(j.error==='busy'){say('<p><b>You’ve sent it a few times already.</b> Give it an hour and try again.</p>',1);return}
   say('<p><b>Proxify couldn’t take this summary.</b> Download the file instead and send it to Proxify.</p>',1)})
 .catch(function(){busy(false);say('<p><b>Couldn’t reach Proxify.</b> Check your connection and press Send again, or download the file instead.</p>',1)})});
try{var was=localStorage.getItem(KEY);if(was)sent(was)}catch(e){}
})();</script>"""


def known_email(ctx):
    """The member's Proxify address when this laptop already knows it: the emails git config commits with (identity.py, in
    the inventory), then the Claude Code login. Only @proxify.io / @proxify.dev; otherwise the field stays empty."""
    seen = list(((ctx.get("inventory") or {}).get("person") or {}).get("identities") or [])
    try:
        login = json.load(open(os.path.expanduser("~/.claude.json")))
        seen.append((login.get("oauthAccount") or {}).get("emailAddress") or "")
    except (OSError, ValueError):
        pass
    return next((e for e in seen if isinstance(e, str) and PROXIFY_EMAIL.search(e.strip())), "").strip()


def render(ctx):
    esc = ctx["esc"]
    try:
        sh = share_payload.payload(ctx["run"])
    except (OSError, ValueError) as e:  # no result yet, or the summary would hold something it must not: no Send button
        return f'<p class="empty">The summary to share isn’t ready: {esc(str(e))}</p>'
    S = json.load(open(share_payload.SUBSTRATE))
    words = ctx.get("RUNG_WORD") or {}
    disc = {d["id"]: d["name"] for d in S["disciplines"]}
    try:
        import board
        kinds = {r[0]: r[1] for r in board.ROWS}
    except Exception:  # noqa: BLE001 - without the board the ids show as they are
        kinds = {}

    def step(p):
        if p.get("rung") in RUNG_CLASS and p.get("rung") in words:
            return RUNG_CLASS[p["rung"]], words[p["rung"]]
        return "r0", "Not yet" if p.get("state") == "looked, not found" else "We couldn't check yet"

    by_area = {}
    for pid, p in sh["practices"].items():
        sp = S["practices"].get(pid) or {}
        by_area.setdefault(sp.get("discipline") or "other", []).append((sp.get("short_name") or sp.get("name") or pid, *step(p)))
    rows = "".join(
        f'<tbody><tr><th colspan="2" scope="rowgroup">{esc(disc.get(a, a))}</th></tr>'
        + "".join(f'<tr><td>{esc(n)}</td><td><span class="rung {c}">{esc(w)}</span></td></tr>' for n, c, w in ps) + "</tbody>"
        for a, ps in sorted(by_area.items(), key=lambda kv: list(disc).index(kv[0]) if kv[0] in disc else 99))
    chips = lambda ks: "".join(f"<li>{esc(kinds.get(k, k))}</li>" for k in ks) or "<li>None</li>"  # noqa: E731
    pname = lambda i: (S["practices"].get(i) or {}).get("short_name") or i  # noqa: E731
    asks = "".join(f"<li>{esc(' or '.join(pname(i) for i in g))}</li>" for g in sh.get("call_questions") or [] if g)
    n_all = len(sh["practices"])
    n_seen = sum(1 for p in sh["practices"].values() if p.get("rung") in RUNG_CLASS)
    days = (sh.get("logs") or {}).get("days_with_sessions")
    email = known_email(ctx)
    # The summary still carries the scan's line per practice, and since 11-11 the level is read another way (plain things
    # plus the read of their files). The lines can credit a habit that Claude Code did unasked, so they sit one click
    # away with that said: the tab must show everything that is sent, but must not lead with what Review contradicts.
    # the summary holds the level this page shows; say so in words (this view prints no level: the tab beside it does)
    level_line = (f'<li>{icon("yes")}<span>The level your laptop shows, as on the Your level tab</span></li>'
                  if ctx.get("level", True) and sh.get("laptop_level") else "")
    older = ('<p class="sh-older">One line per habit: seen on your laptop or not. A line can say "seen" for something '
             'Claude Code did by itself, so where these differ from the Review and Your level tabs, go by those tabs. '
             'Proxify gets these lines too.</p>' if sh.get("level_model") == "things" else "")
    # what Send carries: everything, or only the keys the deployed receiver knows (share_payload.wire). The tab prints
    # and offers for download exactly that, so "what you'd send" stays true either way.
    sent = share_payload.wire(sh)
    fresh = new_lines(esc, sent)
    fresh_gets = ('<li>' + icon("yes") + '<span>What the read of your files says for each of its three parts, in one word, '
                  'and whose each part of your setup is</span></li>' if fresh else "")
    data = json.dumps({"share": sent, "file": json.dumps(sent, indent=1), "receiver": RECEIVER}).replace("</", "<\\/")
    return f"""<ol class="sh-steps" id="sh-steps">
 <li class="now"><b>Check what you'd send</b><p>One summary, shown in full further down this page: the kinds of things you built and use, and one line per habit saying whether your laptop showed it. Nothing else is sent.</p></li>
 <li><b>Send it to Proxify</b><p>Type the email you use with Proxify and press Send. Your summary goes as a file.</p></li>
 <li><b>Book your evaluation call</b><p>The booking link arrives in that inbox, so only you can book with it.</p></li>
</ol>
<form class="sh-form" id="sh-form" novalidate>
 <label for="sh-email">The email you use with Proxify</label>
 <div class="sh-row"><input id="sh-email" name="email" type="email" autocomplete="email" inputmode="email" required placeholder="you@example.com" aria-describedby="sh-hint" value="{esc(email)}">
 <button class="sh-go" type="submit" id="sh-go">{icon("send")}<span>Send to Proxify</span></button></div>
 <p class="sh-hint" id="sh-hint">{"Found on this laptop: change it if you use another email with Proxify. " if email else ""}Sends the summary below as a file, and nothing else. <button class="sh-link" type="button" id="sh-dl">Or download the file</button></p>
</form>
<div class="sh-status" id="sh-status" role="status" aria-live="polite" hidden></div>
<div class="sh-two">
 <section class="sh-yes"><h3>What Proxify gets</h3><ul>
  {level_line}{fresh_gets}<li>{icon("yes")}<span>One line for each of {n_all} habits: whether your laptop showed it, never how or where</span></li>
  <li>{icon("yes")}<span>The kinds of things you built, like skills or hooks, but not their names</span></li>
  <li>{icon("yes")}<span>How many days your logs cover, and how sure the reading is</span></li>
  <li>{icon("yes")}<span>The email you type, so we can send you the booking link</span></li>
 </ul></section>
 <section class="sh-no"><h3>What stays on your laptop</h3><ul>
  <li>{icon("no")}<span>File, folder and repo names</span></li>
  <li>{icon("no")}<span>Your prompts, your code, anything you typed</span></li>
  <li>{icon("no")}<span>Client names, and anything about the work itself</span></li>
  <li>{icon("no")}<span>This page</span></li>
 </ul></section>
</div>
<div class="sh-warn">{icon("warn")}<p><b>Don't send this page or your agentic-setup folder.</b> They hold your project names, file paths and pieces of your prompts. Only the summary below is made to leave your laptop.</p></div>
<section class="sh-sum" aria-labelledby="sh-sum-h"><h3 id="sh-sum-h">The summary, in plain words</h3>
<p>This is everything in it{f", read from {wording.plural(days, 'day')} of your logs" if days else ""}.</p>
{fresh}{older_lines(esc, sh, disc)}<div class="sh-cols">
 <div><h4>Kinds of things you built</h4><ul class="sh-chips">{chips(sh.get("built_kinds") or [])}</ul></div>
 <div><h4>Kinds of things you use</h4><ul class="sh-chips">{chips(sh.get("uses_kinds") or [])}</ul></div>
</div>
<details class="sh-lines"><summary>See the {n_all} habit lines in it ({n_seen} seen){", and the questions left for your call" if asks else ""}</summary>
{older}<table class="sh-tbl">{rows}</table>
{f'<div class="sh-cols"><div style="grid-column:1/-1"><h4>For your call</h4><p>Your laptop can&#x27;t settle these on its own, so the call asks about them.</p><ul class="sh-chips">{asks}</ul></div></div>' if asks else ""}</details></section>
<p class="sh-foot">Changed your mind? Nothing has been sent. Nothing leaves your laptop until you press Send. You can delete your agentic-setup folder whenever you like.</p>
<script type="application/json" id="sh-data">{data}</script>{JS}"""
