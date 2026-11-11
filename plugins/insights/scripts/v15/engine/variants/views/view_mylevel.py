"""Your level: the member-facing level read. The same answer as the internal "Level read" (v15-result.json's pointer),
said to the member: the level and what it means, how sure the laptop is, what it rests on, what would take them up a
step, and what only a call can add. Left out on purpose (internal): the rule text, evidence rows, sources, and the
list of questions the interviewer asks."""
import re

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
@media (max-width:720px){.v-mylevel .ladder{grid-template-columns:1fr 1fr}}
"""

_SAY = [(r"\btheir setup does it\b", "your setup does it"), (r"\bthey do it, at least by hand\b", "you do it, at least by hand"),
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
    practice = [c for c in conds if ":" in str(c.get("test", ""))]
    other = [c for c in conds if ":" not in str(c.get("test", ""))]
    out = []
    if practice:
        met = sum(bool(c.get("met")) for c in practice)
        out.append((met == len(practice), f"Your setup or your habits cover the practices this level asks for",
                    f"{met} of {len(practice)} in place"))
    for c in other:
        out.append((bool(c.get("met")), _say(c.get("test")), ""))
    return out


def render(ctx):
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
                 f'{esc(ce.get("days_with_sessions"))} days of sessions, {esc(ce.get("backed_practices"))} practices backed by a '
                 f'count or a file</span>' if sure else "")
    hero = (f'<div class="hero"><div class="lv"><span class="big">{esc(lvl)}</span><span class="mean">{esc(MEANING[lvl])}</span></div>'
            f'{sure_html}<p>This is what your laptop shows today, read from the files Claude Code keeps on it. It is a starting '
            f'point, not a final score: an evaluation call confirms it or raises it. It never goes down because a laptop '
            f'couldn\'t see something.</p></div>')

    n = int(lvl[1])
    ladder = '<h3>Where that sits</h3><div class="ladder">' + "".join(
        f'<div class="step{" you" if k == lvl else (" done" if int(k[1]) < n else "")}"><b>{k}</b><span>{esc(MEANING[k])}</span>'
        + ('<em>You are here</em>' if k == lvl else "") + "</div>" for k in sorted(MEANING)) + "</div>"

    trail = {t["level"]: t for t in p.get("trail", [])}
    conds = (trail.get(lvl) or {}).get("conditions", [])
    why = ""
    if conds:
        lines = "".join(f'<li><span class="m">✓</span><span>{esc(t)}{f"<small>{esc(sub)}</small>" if sub else ""}</span></li>'
                        for ok, t, sub in _summary(conds) if ok)
        every = "".join(f"<li>{esc(_say(c.get('test')))}</li>" for c in conds if c.get("met"))
        why = (f'<h3>Why {esc(lvl)}</h3><ul class="why">{lines}</ul>'
               f'<details><summary>See all {sum(bool(c.get("met")) for c in conds)} things your laptop showed</summary><ul>{every}</ul></details>')

    up = f"L{n + 1}"
    nxt = ""
    if up in MEANING and up in trail:
        miss = [c for c in trail[up].get("conditions", []) if not c.get("met")]
        if miss:
            nxt = (f'<h3>What would take you to {esc(up)}</h3><ul class="why">'
                   + "".join(f'<li class="no"><span class="m">→</span><span>{esc(_say(c.get("test")))}</span></li>' for c in miss)
                   + '</ul><p style="color:#475467;font-size:13px">The Next steps tab shows how to start on each one.</p>')

    best = p.get("best_rung_by_discipline") or {}
    top = max((int(v[1]) for v in best.values() if v in MEANING), default=0)
    areas = ""
    if best:
        strong = [k for k, v in best.items() if v in MEANING and int(v[1]) == top]
        areas = ('<h3>Where you are strongest</h3><div class="areas">'
                 + "".join(f'<span class="{"top" if k in strong else ""}">{esc(names.get(k, k))}</span>' for k in best) + "</div>"
                 + f'<p style="color:#475467;font-size:13px">Highlighted: the areas where your setup goes furthest. The By discipline tab shows each one part by part.</p>')

    qs = p.get("call_questions") or []
    examples = [names.get(ids[0], ids[0]) for ids in qs[:2] if ids]
    call = ""
    if qs:
        call = ('<h3>What only a call can add</h3><div class="call">'
                + (f'<p>About {len(qs)} habits don\'t leave a trace a laptop can count' if len(qs) > 1 else '<p>One habit doesn\'t leave a trace a laptop can count')
                + (f', for example: <i>{esc(examples[0].lower())}</i>' + (f', or <i>{esc(examples[1].lower())}</i>' if len(examples) > 1 else "") if examples else "")
                + '.</p><p>A call can only add these to your level, never take away. If you want your level confirmed, send '
                  'Proxify the summary from the <a href="#share">Share with Proxify</a> tab.</p></div>')
    return hero + ladder + why + nxt + areas + call
