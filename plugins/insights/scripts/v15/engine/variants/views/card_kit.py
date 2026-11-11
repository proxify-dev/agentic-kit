"""The one card every view uses, and the words that go on it. Not a view: frame.py only loads view_*.py.

A card = a short title, badges (a level, a state), at most one line of text, and a click for the rest. The click opens the
shared side panel (panel_kit.py) when the card names a substrate item: `data-pk="p:<practice id>"`, `c:<problem id>`,
`t:<vocabulary term id>`. The Board's card was the base; its CSS moved here so every view looks alike.

Words that must mean one thing everywhere live here too:
- how(fact, not_counted): how we know a practice, from the evidence row's `fact`. By discipline, the Board and the
  panel all use it (his 11-05: one view said "AI reading only", another "Something you built", for the same practice).
- LAPTOP: whether a check can prove a practice on any laptop (substrate laptop_counts, from tools/laptop_counts.py).
- level(...): the level badge. The member's copy shows words and never a level (his 10-26 ruling).
Stdlib only.
"""
import html
import re

esc = lambda s: html.escape("" if s is None else str(s))  # noqa: E731

# how we know: evidence fact -> (icon, words). No fact, or a row marked not_counted, is the AI's reading with nothing counted behind it.
HOW = {"built": ("◆", "Something you built"), "count": ("#", "Counted from Claude Code's records"),
       "file": ("▤", "One of your files")}
HOW_NONE = ("◌", "AI reading only: confirm on the call")


def how(fact, not_counted=None):
    """(icon, words). One function for every view."""
    return HOW_NONE if (not_counted or fact not in HOW) else HOW[fact]


# laptop_counts (substrate, from the portable checks) -> (filter key, icon, words on the card, full words)
# This is a fact about the tool (an approved check exists), not about this run: what a check counted on this laptop is separate (panel_kit).
LAPTOP = {"proven": ("now", "✓", "Countable on any laptop", "Countable on any laptop: a skeptic-approved check counts it the same everywhere", "Countable on any laptop"),
          "not-proven": ("fix", "◐", "Not countable on other laptops yet", "Has checks that run on any laptop, none approved yet", "Not yet"),
          "call": ("call", "☎︎", "Only a call can tell", "No check can count it on someone else's laptop: only a call can tell", "Only a call can tell")}


def laptop(value):
    return LAPTOP.get(value or "", LAPTOP["call"])


def rung_class(r):
    return {"L4": "r4", "L3": "r3", "L2": "r2", "L1": "r1"}.get(r or "", "r0")


def level(rung, RW, member, extra_attrs="", could=None, no_rung_word="Not yet"):
    """The badge for one practice's level: L-code for Proxify, a word for the member; "could be" when it is only a hint."""
    if rung:
        label = (RW.get(rung) or "Tried once") if member else rung
        return f'<span class="rung {rung_class(rung)}"{"" if member else extra_attrs}>{esc(label)}</span>'
    if could:
        label = f"could be: {RW.get(could, could)}" if member else f"could be {could}"
        return f'<span class="rung could"{"" if member else extra_attrs}>{esc(label)}</span>'
    return f'<span class="rung r0">{esc(no_rung_word)}</span>'


_YOU = [(r"\bof his\b", "of yours"), (r"\bhis own\b", "your own"), (r"\bhis\b", "your"), (r"\bhim\b", "you"), (r"\bhimself\b", "yourself"),
        (r"\bHe\b", "You"), (r"\bhe\b", "you"), (r"\bHis\b", "Your"), (r"\bthe person\b", "you"), (r"\bone person\b", "you")]


def you_text(s):
    """The one place the checks' wording turns to second person at render time (the portable folder's own files say he/his)."""
    s = "" if s is None else str(s)
    for pat, to in _YOU:
        s = re.sub(pat, to, s)
    return s


_LOGS = {}


def set_logs(ctx):
    """Read how much log this run stands on (portable-checks.json meta.logs, written by the portable runner). Older runs have none: then
    every line below is empty and nothing is shown. Called once by frame.build before any view renders."""
    global _LOGS
    meta = ((ctx.get("portable_checks") or {}).get("meta")) or {}
    _LOGS = meta.get("logs") if isinstance(meta.get("logs"), dict) else {}


def _logs_days():
    """(N, M, window): days of log the counts stand on, days Claude Code keeps, and the window asked for. None when the run did not record it."""
    L = _LOGS or {}
    try:
        n = L.get("days_with_sessions")
        if L.get("oldest_day_held"):
            n = min(round(float(L["oldest_day_held"])), L.get("window_days") or 10 ** 6)
        m, win = L.get("cleanup_period_days"), L.get("window_days")
        if n is None or m is None:
            return None
        return int(n), m, win
    except (TypeError, ValueError):
        return None


def log_text():
    """"read from N days of logs (keeps M)": shown next to every "not seen" / "not yet" / "started". "" when the run recorded nothing."""
    d = _logs_days()
    if not d:
        return ""
    n, m, _ = d
    return f"read from {n} day{'s' if n != 1 else ''} of logs (keeps {m}{' (default)' if not (_LOGS or {}).get('cleanup_period_set') else ''})"


def log_line(cls=""):
    """The shared 'how much log was read' line for a state that says "not seen". Adds a quiet hint when Claude Code keeps fewer days than the window."""
    t = log_text()
    if not t:
        return ""
    n, m, win = _logs_days()
    hint = " · a longer log setting would show more" if win and m < win else ""
    return f'<span class="klog{" " + cls if cls else ""}" title="Claude Code keeps session logs for {m} days (cleanupPeriodDays in its settings)">{esc(t + hint)}</span>'


def header_note():
    """For the page header: Claude Code's newest version and the one setting that decides how far back the logs go, named once."""
    L = _LOGS or {}
    d = _logs_days()
    bits = []
    if L.get("claude_code_newest"):
        bits.append(f"Claude Code {L['claude_code_newest']}")
    if d:
        bits.append(f"logs: cleanupPeriodDays keeps {d[1]} days{'' if L.get('cleanup_period_set') else ' (default)'}")
    return " · ".join(bits)


def how_line(fact, not_counted=None):
    icon, words = how(fact, not_counted)
    ai = icon == HOW_NONE[0]
    return f'<div class="kc-how{" ai" if ai else ""}"><i aria-hidden="true">{icon}</i><span>{esc(words)}</span></div>'


def card(title, badges="", lines="", pk=None, cls="", attrs=""):
    """One card. `badges`: html chips (level, state). `lines`: html, at most one or two short lines. `pk`: "p:<id>" opens the panel."""
    open_ = (f' data-pk="{esc(pk)}" role="button" tabindex="0" aria-label="{esc(title)}: details"' if pk else "")
    return (f'<div class="kc{" " + cls if cls else ""}"{open_}{attrs}><span class="kc-t">{esc(title)}</span>'
            + (f'<div class="kc-row">{badges}</div>' if badges else "") + lines + "</div>")


def group(title, count_html, cards_html, sub="", bar="", cls=""):
    """A column or row of cards with its name and counts in the header, like the Board."""
    return (f'<div class="kg{" " + cls if cls else ""}"><div class="kg-h"><b>{esc(title)}'
            + (f' <em class="kg-n">{count_html}</em>' if count_html else "") + "</b>"
            + (f"<span>{sub}</span>" if sub else "") + bar + f"</div>{cards_html}</div>")


def pill(icon, word, tone=""):
    """The one pill: an optional icon, a space, a word. tone: good, warn, brand, or "" for neutral. A state is always icon plus word."""
    ic = f'<span class="pi" aria-hidden="true">{icon}</span>' if icon else ""
    return f'<span class="pill{" " + tone if tone else ""}">{ic}{esc(word)}</span>'


def state(icon, word, tone=""):
    """A state is always an icon plus a word; colour only adds."""
    return pill(icon, word, tone)


def laptop_pill(value, short=False):
    """Whether an approved check can count the practice on any laptop, as a pill. short=True: the card's one-line form, full words in the tooltip."""
    key, icon, words, full, brief = laptop(value)
    tone = {"now": "good", "fix": "warn"}.get(key, "")
    return f'<span class="pill{" " + tone if tone else ""}" title="{esc(full)}"><span class="pi" aria-hidden="true">{icon}</span>{esc(brief if short else words)}</span>'


CSS = """
.kboard{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;align-items:start;margin:16px 0 0}
.kboard.cols5{grid-template-columns:repeat(5,minmax(0,1fr))}
.kg{display:flex;flex-direction:column;gap:6px;min-width:0}
.kg-h{padding:0 2px 8px;border-bottom:2px solid #15172F;margin:0 0 4px}
.kg-h b{display:block;font-size:14px;font-weight:600;line-height:1.3;color:#15172F}
.kg-h span{display:block;font-size:12px;color:#667085;margin-top:2px}
.kg-h .kg-n{font-style:normal;font-weight:500;color:#667085;margin-left:4px}
.kdist{display:flex;height:6px;border-radius:3px;overflow:hidden;gap:2px;margin-top:8px;background:#F2F4F7}
.kdist i{display:block}
.kc{position:relative;display:block;background:#FFFFFF;border:1px solid #EAECF0;border-radius:9px;padding:9px 10px;text-align:left}
.kc[data-pk]{cursor:pointer}
.kc[data-pk]:hover{border-color:#5258FB}
.kc:focus-visible{outline:2px solid #5258FB;outline-offset:2px}
.kc[hidden]{display:none}
.kc.need{border-color:#DC6803;background:#FEFBF5}
.kc-t{display:block;font-size:13px;font-weight:600;line-height:1.35;color:#15172F;margin:0 0 6px}
.kc-row{display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center}
.klog{display:block;margin:6px 0 0;font-size:11px;line-height:1.4;color:#667085}
.klog.inline{display:inline;margin:0}
.kc-how{display:flex;gap:6px;align-items:baseline;margin:6px 0 0;font-size:11px;line-height:1.4;color:#475467}
.kc-how i{flex:none;font-style:normal;width:12px;text-align:center;color:#3C3ED4}
.kc-how.ai{color:#B54708}.kc-how.ai i{color:#B54708}
.kc-line{margin:6px 0 0;font-size:12px;line-height:1.4;color:#475467}
.kc-flag{display:inline-block;margin:6px 0 0;font-size:11px;font-weight:600;line-height:18px;padding:0 6px;border-radius:4px;background:#FDF1D9;color:#B54708}
.pill,.rung{display:inline-flex;align-items:center;gap:5px;height:20px;padding:0 8px;border-radius:999px;font-size:11px;font-weight:500;line-height:1;
  white-space:nowrap;background:#F2F4F7;color:#475467;font-variant-numeric:tabular-nums}
.pill .pi{font-size:10px;line-height:1}
.kc .pill{height:auto;min-height:20px;padding:3px 8px;line-height:1.25;white-space:normal;border-radius:10px;align-items:flex-start}
.pill.good{background:#F6FEF9;color:#027A48;box-shadow:inset 0 0 0 1px #C4F3D8}
.pill.warn{background:#FEFBF5;color:#B54708;box-shadow:inset 0 0 0 1px #FDF1D9}
.pill.brand{background:#EEEFFE;color:#272594}
.rung{justify-content:center;min-width:28px;font-weight:600}
.rung.r4{background:#3C3ED4;color:#FFFFFF}.rung.r3{background:#5258FB;color:#FFFFFF}
.rung.r2{background:#DDDEFE;color:#272594}.rung.r1{background:#F2F4F7;color:#344054}
.rung.r0{background:transparent;color:#475467;box-shadow:inset 0 0 0 1px #D0D5DD}
.rung.could{background:transparent;color:#475467;box-shadow:inset 0 0 0 1px #D0D5DD;font-weight:500}
.kdist .r4{background:#3C3ED4}.kdist .r3{background:#5258FB}.kdist .r2{background:#DDDEFE}.kdist .r1{background:#F2F4F7}.kdist .r0{background:#D0D5DD}
a.pl{color:inherit;text-decoration:none;border-radius:4px;cursor:pointer;transition:color .12s,background-color .12s,box-shadow .12s}
a.pl:hover{color:#3C3ED4;background:#F5F5FF;box-shadow:0 0 0 3px #F5F5FF}
a.pl:focus-visible{outline:2px solid #5258FB;outline-offset:2px}
a.pl::after{content:"";display:inline-block;width:10px;height:10px;margin-left:3px;vertical-align:-1px;background:currentColor;opacity:0;transform:translateX(-2px);
  -webkit-mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12' fill='none' stroke='black' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4.5 2.5 8 6l-3.5 3.5'/%3E%3C/svg%3E") center/contain no-repeat;
  mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12' fill='none' stroke='black' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4.5 2.5 8 6l-3.5 3.5'/%3E%3C/svg%3E") center/contain no-repeat;
  transition:opacity .12s,transform .12s}
a.pl:hover::after,a.pl:focus-visible::after{opacity:.7;transform:none}
.kp a.pl::after{opacity:.5;transform:none}
@media (max-width:1180px){.kboard.cols5{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:760px){.kboard.cols5{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:520px){.kboard.cols5{grid-template-columns:minmax(0,1fr)}}
"""
