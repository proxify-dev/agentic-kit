"""Your level: the member-facing level read. The same answer as the internal "Level read" (v15-result.json's pointer),
said to the member: the level and what it means, how sure the laptop is, what it rests on, what would take them up a
step, and what only a call can add. Left out on purpose (internal): the rule text, evidence rows, sources, and the
list of questions the interviewer asks.

From 11-11 it also shows the setup read (engine.setup_read_gate, pointer["setup_read"]): what the person's own files
show, part by part, with the line each reading rests on and the next step. L3 and L4 need it, so a level the files
don't back reads "L2, could be L3": the tab says so in the headline, and why, before anything else. A run made before
the setup read has no `setup_read` and renders as it always did.

Also from 11-11, a trial: the level read from plain things (engine/things.py, result["pointer_things"]) is the one the
tab shows by default: the level, what decided it in one sentence, what the files show, then every thing by its home
level with its state and proof. AGENTIC_SETUP_LEVEL_MODEL=practices, or a result made before that read, renders the
practice read below (_render_practices), unchanged."""
import os
import re
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))
import things as level_things  # noqa: E402  which level read is in use, and the things' states
import wording  # noqa: E402  the one plural

ORDER = 1
TITLE = "Your level"
QUESTION = "The level your laptop shows today: a starting point that an evaluation call confirms or raises."
QUESTION_MEMBER = QUESTION
PROXIFY_ONLY = False
LEVEL_VIEW = True  # frame.build(level=False) drops it: the copy with no level at all

MEANING = {"L1": "Uses agents on real work", "L2": "Works with discipline",
           "L3": "Sets agents up to know, learn and remember", "L4": "Builds the harness around the agent"}
SURE = {"high": "Very sure", "medium": "Fairly sure", "low": "Not very sure yet"}

CSS = """
.v-mylevel .hero{background:#F5F5FF;border:1px solid #D5D5FE;border-radius:16px;padding:24px;display:grid;gap:8px}
.v-mylevel .hero .lv{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
.v-mylevel .hero .big{font-size:40px;font-weight:600;line-height:1;color:#272594}
.v-mylevel .hero .mean{font-size:20px;font-weight:600;color:#272594}
.v-mylevel .hero p{margin:0;color:#344054;max-width:68ch}
.v-mylevel .sure{display:inline-flex;gap:8px;align-items:center;font-size:12px;font-weight:500;color:#039855;background:#F6FEF9;
  border:1px solid #039855;border-radius:999px;padding:4px 10px;width:max-content}
.v-mylevel .sure.mid{color:#B54708;background:#FEFBF5;border-color:#FDF1D9}
.v-mylevel h3{font-size:16px;font-weight:600;margin:32px 0 8px}
.v-mylevel .ladder{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.v-mylevel .step{border:1px solid #EAECF0;border-radius:12px;background:#FFFFFF;padding:12px;display:grid;gap:4px}
.v-mylevel .step b{font-size:14px;color:#667085}.v-mylevel .step span{font-size:13px;color:#475467;line-height:1.4}
.v-mylevel .step.done b{color:#272594}
.v-mylevel .step.you{border-color:#5258FB;box-shadow:0 0 0 1px #5258FB inset;background:#F5F5FF}
.v-mylevel .step.you b{color:#3C3ED4}.v-mylevel .step.you span{color:#15172F;font-weight:500}
.v-mylevel .step em{font-style:normal;font-size:12px;font-weight:600;color:#3C3ED4}
.v-mylevel ul.why{margin:0;padding:0;list-style:none;display:grid;gap:8px}
.v-mylevel ul.why li{background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;padding:12px 16px;display:flex;gap:12px}
.v-mylevel ul.why li .m{font-weight:600;color:#039855;flex:none}
.v-mylevel ul.why li.no .m{color:#B54708}
.v-mylevel ul.why li small{display:block;color:#667085;font-size:12px}
.v-mylevel details{margin-top:8px}.v-mylevel details>summary{color:#3C3ED4;font-size:13px}
.v-mylevel details ul{margin:8px 0 0;padding-left:20px;color:#475467;font-size:13px;display:grid;gap:4px}
.v-mylevel .areas{display:flex;flex-wrap:wrap;gap:8px}
.v-mylevel .areas span{font-size:13px;color:#344054;border:1px solid #EAECF0;border-radius:999px;padding:4px 12px;background:#FFFFFF}
.v-mylevel .areas span.top{border-color:#999AFD;background:#F5F5FF;color:#272594;font-weight:500}
.v-mylevel .call{background:#FFFFFF;border:1px dashed #D0D5DD;border-radius:12px;padding:16px;color:#344054}
.v-mylevel .call p{margin:0 0 8px}.v-mylevel .call p:last-child{margin:0}
.v-mylevel .hero .could{font-size:13px;font-weight:600;color:#3C3ED4;border:1px dashed #999AFD;border-radius:999px;padding:4px 10px;background:#FFFFFF}
.v-mylevel .step.maybe{border-style:dashed;border-color:#999AFD}
.v-mylevel ul.files li>span:last-child{flex:1;min-width:0}
.v-mylevel ul.files .st{font-size:12px;font-weight:600;color:#039855;margin-left:8px;white-space:nowrap}
.v-mylevel ul.files li.partly .m,.v-mylevel ul.files li.partly .st{color:#B54708}
.v-mylevel ul.files li.notshown .m,.v-mylevel ul.files li.notshown .st,.v-mylevel ul.files li.unread .m,.v-mylevel ul.files li.unread .st{color:#667085}
.v-mylevel ul.files .nx{display:block;font-size:13px;color:#344054;margin-top:4px}
.v-mylevel ul.files blockquote{margin:8px 0;padding:8px 12px;border-left:3px solid #D5D5FE;background:#F9FAFB;color:#344054;font-size:13px;overflow-wrap:anywhere}
.v-mylevel ul.files blockquote cite{display:block;font-style:normal;font-size:12px;color:#667085;margin-top:4px}
.v-mylevel .hold{margin-top:8px;border:1px solid #FDF1D9;background:#FEFBF5;border-radius:12px;padding:12px 16px;color:#344054;display:flex;gap:12px}
.v-mylevel .hold .m{font-weight:600;color:#B54708;flex:none}
.v-mylevel .hold small{display:block;color:#667085;font-size:12px;margin-top:4px}
.v-mylevel .tg{margin-top:16px}
.v-mylevel .tg>h4{font-size:14px;font-weight:600;margin:0 0 8px;color:#272594;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.v-mylevel .tg>h4 small{font-weight:400;color:#667085;font-size:12px}
.v-mylevel ul.things li{align-items:baseline}
.v-mylevel ul.things li>span:last-child{flex:1;min-width:0}
.v-mylevel ul.things .st{font-size:12px;font-weight:600;margin-left:8px;white-space:nowrap;color:#667085}
.v-mylevel ul.things li.yours .m,.v-mylevel ul.things li.yours .st{color:#039855}
.v-mylevel ul.things li.idle .m,.v-mylevel ul.things li.idle .st{color:#B54708}
.v-mylevel ul.things li.other .m,.v-mylevel ul.things li.none .m{color:#667085}
.v-mylevel .cmp{margin-top:16px;font-size:13px;color:#667085;border-top:1px dashed #D0D5DD;padding-top:12px}
@media (max-width:720px){.v-mylevel .ladder{grid-template-columns:1fr 1fr}}
"""

# The two conditions engine.setup_read_gate adds to the L3 and L4 lists. They carry a ":" like a practice line does, so
# _summary would count them as practices ("7 of 7 in place") unless it knows them by their opening words.
GATE_TESTS = ("their setup carries the context", "Claude Code's records show no sign of the opposite habit")

_SAY = [(r"^their setup carries the context: its files show it in at least (\d+) of (\d+) parts.*$",
         r"Your files show a setup that carries the context, in at least \1 of \2 parts"),
        (r"^Claude Code's records show no sign of the opposite habit$", "Claude Code's records show no habit that works against it"),
        (r"\btheir setup does it\b", "your setup does it"), (r"\bthey do it, at least by hand\b", "you do it, at least by hand"),
        (r"^things they built, shown at L4 in at least (\d+) different areas.*$", r"You built things that change how the agent runs, in at least \1 different areas"),
        (r"^re-tests what they built with evals.*$", "You re-test what you built with evals"),
        (r"^keeps refining what they built: changes to it in at least (\d+) different weeks$", r"You keep improving what you built: changes in at least \1 different weeks"),
        (r"\s*\((?:L[1-4]\+?|[^)]*\bL[1-4]\b[^)]*)\)", ""), (r"\btheir\b", "your"), (r"\bthey\b", "you")]


def _say(test):
    """One condition of the level rule, said to the member, with no level codes inside it."""
    s = str(test or "")
    for pat, to in _SAY:
        s = re.sub(pat, to, s)
    return s[:1].upper() + s[1:]


def _summary(conds):
    """Three lines instead of thirteen: the practices, the things built, the evals/refining."""
    is_practice = lambda c: ":" in str(c.get("test", "")) and not str(c.get("test", "")).startswith(GATE_TESTS)  # noqa: E731
    practice = [c for c in conds if is_practice(c)]
    other = [c for c in conds if not is_practice(c)]
    out = []
    if practice:
        met = sum(bool(c.get("met")) for c in practice)
        out.append((met == len(practice), f"Your setup or your habits cover the practices this level asks for",
                    f"{met} of {len(practice)} in place"))
    for c in other:
        out.append((bool(c.get("met")), _say(c.get("test")), ""))
    return out


# The three parts of "their setup carries the context" (the Level 3 map's deciding rows), by the ids the setup read
# may use. The name and the next step here are fallbacks: the run's own `name` and `next` win when it wrote them.
PART_NAMES = {"rules": "Instructions the agent starts with", "instructions": "Instructions the agent starts with",
              "corrections": "Corrections that stick", "memory": "Memory the next session reads"}
DEFAULT_PARTS = ("rules", "corrections", "memory")
STATE = {"shown": ("✓", "Shown"), "partly": ("◐", "Partly"), "notshown": ("○", "Not shown yet"), "unread": ("?", "Couldn't read")}
NEXT = {
    ("rules", "open"): "Keep the AGENTS.md every session loads to what every task needs. Move the rest into an "
                       "AGENTS.md in the folder it applies to, or into a doc your AGENTS.md links to, with a line that "
                       "says when to open it. Extra: a file in .claude/rules/ with a paths: line.",
    ("rules", "shown"): "Keep it small: when a line in your AGENTS.md stops earning its place, remove it.",
    ("corrections", "open"): "The next time you correct the agent on the same thing twice, write it as one line, with "
                             "its reason, where every session reads it.",
    ("corrections", "shown"): "When a line you wrote in AGENTS.md still gets ignored, look for the cause instead of louder wording.",
    ("memory", "open"): "Give your notes a shape: an index that points to notes by topic, which the agent keeps "
                        "current and you prune.",
    ("memory", "shown"): "Prune notes that are settled or wrong, so the index stays worth reading.",
}
NEXT["instructions", "open"], NEXT["instructions", "shown"] = NEXT["rules", "open"], NEXT["rules", "shown"]
WHY = {"shown": "Your files show it.", "partly": "Your files show part of it.",
       "notshown": "We looked in your files and didn't find it yet.", "unread": "The read of your files didn't reach this part."}
HOLD_WHY = {"endless-sessions": "your sessions were only ever reset by automatic compaction"}


def _part_state(g):
    """The one rule for a part's state is things.part_state; the Review and the Share tab read it too."""
    return level_things.part_state(g)


def _setup_read(sr, esc, labels=None):
    """'What your files show': one row per part (state as icon and word, the why), the next step in sight for a part
    that isn't shown, and the quoted line behind a click. A fired hold is its own line: a habit, not a verdict.
    `labels` = project slug -> its name: each part says which project its reading came from (the best one of the
    projects read, part by part, so two parts may come from two projects)."""
    labels = labels or {}
    parts = sr.get("parts") or [{"id": i} for i in DEFAULT_PARTS]
    rows = []
    for g in parts:
        pid = str(g.get("id") or "")
        state, note = _part_state(g) if sr.get("ran", True) else ("unread", "")
        icon, word = STATE[state]
        why = g.get("why") or g.get("structure_why") or (
            "The read of your files didn't run this time." if not sr.get("ran", True) else WHY[state])
        nxt = g.get("next") or ("Run the tool again so your files get read." if state == "unread" else
                                NEXT.get((pid, "shown" if state == "shown" else "open")) or "The Review tab says what to change first.")
        q = g.get("quote") if isinstance(g.get("quote"), dict) else ({"text": g.get("quote")} if g.get("quote") else {})
        quote = ""
        if q.get("text"):
            where = f'{q.get("path")}{":" + str(q["line"]) if q.get("line") else ""}' if q.get("path") else ""
            quote = f'<blockquote>{esc(q["text"])}{f"<cite>{esc(where)}</cite>" if where else ""}</blockquote>'
        nx = f'<b>Next step:</b> {esc(nxt)}'
        inline = "" if state == "shown" else f'<span class="nx">{nx}</span>'
        inside = quote + (f'<p style="margin:8px 0 0;font-size:13px;color:#344054">{nx}</p>' if state == "shown" else "")
        # the part's best reading came from one project (setup_read.merge picks each part's best separately): say which
        slug = g.get("project")
        project = f'<small>Best read in your project {esc(labels.get(slug) or slug)}.</small>' if slug else ""
        label = ("See the line it rests on, and your next step" if state == "shown" and quote else
                 "See the line it rests on" if quote else "See your next step")
        rows.append(
            f'<li class="{state}"><span class="m" aria-hidden="true">{icon}</span><span><b>{esc(g.get("name") or PART_NAMES.get(pid) or pid)}</b>'
            f'<span class="st">{word}</span><small>{esc(why)}{" " + esc(note) if note else ""}</small>{project}{inline}'
            + (f'<details><summary>{label}</summary>{inside}</details>' if inside else "") + '</span></li>')
    holds = "".join(
        f'<div class="hold"><span class="m" aria-hidden="true">!</span><span><b>A habit seen in Claude Code\'s records:</b> '
        f'{esc(h.get("why") or HOLD_WHY.get(h.get("id")) or h.get("id"))}.<small>While it shows, your laptop reads L2. It is a habit, '
        f'not a score: the next run reads what your sessions do then.</small></span></div>'
        for h in sr.get("holds") or [] if h.get("fired"))
    counted = f" Of the {wording.plural(len(parts), 'part')}, {level_things.parts_said(parts)}." if sr.get("ran", True) and sr.get("parts") else ""
    return ('<h3>What your files show</h3><p style="color:#475467;font-size:13px;margin:0 0 8px">L3 means your setup carries the '
            'context, so the agent knows the right thing without you telling it again. We mapped your own AGENTS.md files, '
            f'memory and skills, and an AI read them. A part counts when both agree.{counted}</p>'
            f'<ul class="why files">{"".join(rows)}</ul>{holds}')


THING_CLASS = {level_things.YOURS_USED: "yours", level_things.YOURS_IDLE: "idle", level_things.USES_OTHERS: "other",
               level_things.CLAUDE_DID: "other", level_things.NOT_SEEN: "none", level_things.CANT_CHECK: "none"}
THING_WORD = {level_things.YOURS_USED: "Yours and used", level_things.YOURS_IDLE: "Yours, not used in 30 days",
              level_things.USES_OTHERS: "Uses others'", level_things.CLAUDE_DID: "Claude did this",
              level_things.NOT_SEEN: "Not seen", level_things.CANT_CHECK: "Couldn't check"}
GROUP_SAYS = {"L1": "What ships with Claude Code, or what others made.",
              "L2": "Habits that are your own act: only you press Esc or type a command.",
              "L3": "A setup of your own. That it is there shows here; how deep it goes is what your files show, above.",
              "L4": "Things you built that change how the agent runs."}


def _said_attr(t, esc):
    """An invisible attribute holding the sentence a thing's count is told in, where one function words it for every tab."""
    return f' data-said="{esc(t["said"])}"' if t.get("said") else ""


def _things(pt, esc):
    """Every thing by its home level: state as icon and word, the proof under it. No practice names, no rung words."""
    groups = []
    for lv in sorted(MEANING):
        ts = [t for t in pt.get("things") or [] if t.get("level") == lv]
        if not ts:
            continue
        have, of = level_things.things_met(pt)[lv]
        rows = "".join(
            f'<li class="{THING_CLASS.get(t.get("state"), "none")}" data-thing="{esc(t.get("id"))}" data-state="{esc(t.get("state"))}"{_said_attr(t, esc)}><span class="m" aria-hidden="true">{level_things.ICON.get(t.get("state"), "?")}</span>'
            f'<span><b>{esc(t.get("name"))}</b><span class="st">{esc(t.get("word") or THING_WORD.get(t.get("state"), t.get("state")))}</span>'
            f'<small>{esc(t.get("proof"))}</small></span></li>' for t in ts)
        groups.append(f'<div class="tg" data-level="{lv}" data-have="{have}" data-of="{of}" data-unit="thing"><h4>{lv} · {esc(MEANING[lv])}<small>{have} of {wording.plural(of, "thing")} in place · {esc(GROUP_SAYS[lv])}</small></h4>'
                      f'<ul class="why things">{rows}</ul></div>')
    return ('<h3>What your laptop shows, level by level</h3><p style="color:#475467;font-size:13px;margin:0 0 8px">Each thing '
            'belongs to one level. What Claude Code or the model did by itself is shown, and counts for nothing.</p>'
            + "".join(groups))


def _render_things(ctx, res, pt):
    """The level read from things: the level, what decided it, what the files show, then the things by level."""
    esc = ctx["esc"]
    lvl, could = pt.get("level"), pt.get("could_be") if pt.get("could_be") in MEANING else None
    reasons = [r for r in pt.get("reasons") or [] if r]
    days = pt.get("days_with_sessions")
    head = (f'<span class="big">{esc(lvl)}</span><span class="mean">{esc(MEANING[lvl])}</span>' if lvl in MEANING else
            '<span class="mean">Not enough on this laptop yet to show a level</span>')
    hero = ('<div class="hero"><div class="lv">' + head
            + (f'<span class="could">Could be {esc(could)}</span>' if could else "") + '</div>'
            + (f'<p><b>{esc(reasons[0])}</b></p>' if reasons else "")
            + "".join(f'<p>{esc(r)}</p>' for r in reasons[1:])
            + '<p>This is what your laptop shows today'
            + (f', read from {esc(wording.plural(days, "day"))} of sessions and your own files' if days is not None else ', read from your sessions and your own files')
            + '. It is a starting point, not a final score: an evaluation call confirms it or raises it.</p></div>')
    ladder = ""
    if lvl in MEANING:
        n = int(lvl[1])
        ladder = '<h3>Where that sits</h3><div class="ladder">' + "".join(
            f'<div class="step{" you" if k == lvl else (" done" if int(k[1]) < n else (" maybe" if k == could else ""))}"><b>{k}</b><span>{esc(MEANING[k])}</span>'
            + ('<em>You are here</em>' if k == lvl else ('<em>Could be</em>' if k == could else "")) + "</div>" for k in sorted(MEANING)) + "</div>"
    sr = pt.get("setup_read") if isinstance(pt.get("setup_read"), dict) else None
    labels = {p.get("slug"): p.get("label") for p in (ctx.get("setup_read") or {}).get("projects") or [] if isinstance(p, dict)}
    files = _setup_read(sr, esc, labels) if sr else ""
    cmp_ = ""
    old = res.get("pointer") or {}
    if not ctx.get("member") and old.get("level"):  # staff only: both reads side by side while the trial runs
        cmp_ = (f'<p class="cmp">For comparison, the practice read says {esc(old["level"])}'
                + (f', could be {esc(old["could_be"])}' if old.get("could_be") else "")
                + '. Set AGENTIC_SETUP_LEVEL_MODEL=practices to see that page.</p>')
    return hero + ladder + files + _things(pt, esc) + cmp_


def render(ctx):
    res = ctx.get("v15_result") or {}
    pt, which = level_things.active(res)
    if which == "things":
        return _render_things(ctx, res, pt)
    return _render_practices(ctx)


def _render_practices(ctx):
    esc = ctx["esc"]
    res = ctx.get("v15_result") or {}
    p = res.get("pointer")
    if not p or p.get("level") not in MEANING:
        return ('<p class="empty">Not enough on this laptop yet to show a level. Use Claude Code on real work for a few more '
                'days and run this again, or get one in an evaluation call.</p>')
    lvl = p["level"]
    names = {}
    for d in res.get("disciplines", []):
        names[d["id"]] = d["name"]
        for pr in d.get("practices", []):
            names[pr["id"]] = pr["name"]
    ce = (res.get("calibration") or {}).get("certainty") or {}
    sure = ce.get("level")
    sure_html = (f'<span class="sure{"" if sure == "high" else " mid"}">{esc(SURE.get(sure, sure))} · '
                 f'{esc(wording.plural(ce.get("days_with_sessions") or 0, "day"))} of sessions, {esc(wording.plural(ce.get("backed_practices") or 0, "practice"))} backed by a '
                 f'count or a file</span>' if sure else "")
    # the setup read (engine.setup_read_gate): present only when the practice lists read L3 or L4. Its level is L2 when
    # the files don't back that, with could_be the level the lists read (none when a hold fired)
    sr = p.get("setup_read") if isinstance(p.get("setup_read"), dict) else None
    gated = bool(sr) and lvl == "L2"
    could = p.get("could_be") if gated and p.get("could_be") in MEANING else None
    reason = ""
    if gated:
        fired = [h for h in sr.get("holds") or [] if h.get("fired")]
        n_parts = len(sr.get("parts") or []) or len(DEFAULT_PARTS)
        n_shown = sum(_part_state(g)[0] == "shown" for g in sr.get("parts") or [])
        # whether enough parts show it is the engine's call (its own condition on the L3 list), not a number kept here
        gate = [c for t in p.get("trail") or [] for c in t.get("conditions") or []
                if str(c.get("test", "")).startswith(GATE_TESTS[0])]
        enough = bool(gate[0].get("met")) if gate else n_shown >= n_parts
        if not sr.get("ran", True):
            reason = ("The read of your files didn't run this time, so your laptop can't say whether your setup carries "
                      "the context. Run it again.")
        elif not enough:
            reason = (f"Your files don't yet show a setup that carries the context: of {wording.plural(n_parts, 'part')}, "
                      f"{level_things.parts_said(sr.get('parts'))}.")
        elif fired:
            h = fired[0]
            reason = ("Claude Code's records show a habit that works against your setup: "
                      f"{h.get('why') or HOLD_WHY.get(h.get('id')) or h.get('id')}.")
    hero = (f'<div class="hero"><div class="lv"><span class="big">{esc(lvl)}</span><span class="mean">{esc(MEANING[lvl])}</span>'
            + (f'<span class="could">Could be {esc(could)}</span>' if could else "") + '</div>'
            + f'{sure_html}' + (f'<p><b>{esc(reason)}</b></p>' if reason else "")
            + '<p>This is what your laptop shows today, read from the files Claude Code keeps on it. It is a starting '
              'point, not a final score: an evaluation call confirms it or raises it.'
            + ("" if gated else " It never goes down because a laptop couldn't see something.") + '</p></div>')

    n = int(lvl[1])
    ladder = '<h3>Where that sits</h3><div class="ladder">' + "".join(
        f'<div class="step{" you" if k == lvl else (" done" if int(k[1]) < n else (" maybe" if k == could else ""))}"><b>{k}</b><span>{esc(MEANING[k])}</span>'
        + ('<em>You are here</em>' if k == lvl else ('<em>Could be</em>' if k == could else "")) + "</div>" for k in sorted(MEANING)) + "</div>"
    files = _setup_read(sr, esc) if sr else ""

    trail = {t["level"]: t for t in p.get("trail", [])}
    conds = (trail.get(lvl) or {}).get("conditions", [])
    why = ""
    if conds:
        lines = "".join(f'<li><span class="m">✓</span><span>{esc(t)}{f"<small>{esc(sub)}</small>" if sub else ""}</span></li>'
                        for ok, t, sub in _summary(conds) if ok)
        every = "".join(f"<li>{esc(_say(c.get('test')))}</li>" for c in conds if c.get("met"))
        why = (f'<h3>Why {esc(lvl)}</h3><ul class="why">{lines}</ul>'
               f'<details><summary>See all {wording.plural(sum(bool(c.get("met")) for c in conds), "thing")} your laptop showed</summary><ul>{every}</ul></details>')

    up = f"L{n + 1}"
    nxt = ""
    if up in MEANING and up in trail:
        miss = [c for c in trail[up].get("conditions", []) if not c.get("met")]
        if miss:
            nxt = (f'<h3>What would take you to {esc(up)}</h3><ul class="why">'
                   + "".join(f'<li class="no"><span class="m">→</span><span>{esc(_say(c.get("test")))}</span></li>' for c in miss)
                   + '</ul><p style="color:#475467;font-size:13px">'
                   + ("What your files show, above, has the next step for each part. " if sr and any(
                       str(c.get("test", "")).startswith(GATE_TESTS) for c in miss) else "")
                   # neither copy has a Next steps tab any more (frame.GROUPS, 11-11): next steps are in Review
                   + "The Review tab says what to change first.</p>")

    best = p.get("best_rung_by_discipline") or {}
    top = max((int(v[1]) for v in best.values() if v in MEANING), default=0)
    areas = ""
    if best:
        strong = [k for k, v in best.items() if v in MEANING and int(v[1]) == top]
        areas = ('<h3>Where you are strongest</h3><div class="areas">'
                 + "".join(f'<span class="{"top" if k in strong else ""}">{esc(names.get(k, k))}</span>' for k in best) + "</div>"
                 + '<p style="color:#475467;font-size:13px">Highlighted: the areas where your setup goes furthest.</p>')

    qs = p.get("call_questions") or []
    examples = [names.get(ids[0], ids[0]) for ids in qs[:2] if ids]
    call = ""
    if qs:
        call = ('<h3>What only a call can add</h3><div class="call">'
                + (f'<p>About {len(qs)} habits don\'t leave a trace a laptop can count' if len(qs) > 1 else '<p>One habit doesn\'t leave a trace a laptop can count')
                + (f', for example: <i>{esc(examples[0].lower())}</i>' + (f', or <i>{esc(examples[1].lower())}</i>' if len(examples) > 1 else "") if examples else "")
                + '.</p><p>A call can only add these to your level, never take away. If you want your level confirmed, send '
                  'Proxify the summary from the <a href="#share">Share with Proxify</a> tab.</p></div>')
    return hero + ladder + files + why + nxt + areas + call
