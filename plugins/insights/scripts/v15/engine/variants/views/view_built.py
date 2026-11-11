"""What you've built: v13's yes/no capability board, read from facts on the laptop (engine/board.py), one row per part of the harness.

A state is an icon and a word (colour only adds to them). The member's copy shows counts and never the names of the
client's things; Proxify's copy adds up to three example names and the field each state rests on. No level anywhere.

Under the "Skills and commands you wrote" row the page lists the per-skill depth the board reads (engine/board.py skill_depth):
its summary line, the skills that were used deepest first, the ones not used in the last 30 days behind a toggle. Depth is the
depth of use, never a level of the person; the over-250-lines flag is a note with a next step, never a minus on a depth.
The member's copy names no skill and no file: a skill is own-skill-N and the files inside a `why` are dropped.
"""
import html
import re


ORDER = 26
TITLE = "What you've built"
QUESTION = "Which parts of the harness have you built, which do you only use from others, and did what you built run lately?"
QUESTION_MEMBER = "Which parts of your setup did you build yourself, and did they run in the last 30 days?"
PROXIFY_ONLY = False

# state -> (icon, word, what it means, kind of the left edge)
STATE = {"built and used": ("●", "Built and used", "yours, and it ran or was called in the window", "yes"),
         "built, not used lately": ("◐", "Built, not used lately", "yours, nothing in the window shows it running", "idle"),
         "uses others'": ("⇄", "Uses others'", "installed from someone else, or a feature Claude Code ships", "others"),
         "not seen": ("○", "Not seen", "looked for in the files and logs, none found", "none"),
         "can't tell": ("?", "Can't tell", "the logs can't show it", "unk")}
ORDER_OF_STATES = list(STATE)

CSS = """
.v-built .bsum{display:flex;flex-wrap:wrap;gap:8px 16px;margin:0 0 16px;font-size:14px;color:#344054}
.v-built .bsum b{font-weight:600;color:#15172F}
.v-built ul.brows{list-style:none;margin:0;padding:0;background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px}
.v-built .brow{display:grid;grid-template-columns:200px minmax(0,1fr) 180px;gap:4px 16px;align-items:baseline;padding:10px 16px;border-top:1px solid #EAECF0;border-left:3px solid #D0D5DD}
.v-built .brow:first-child{border-top:0;border-radius:12px 12px 0 0}
.v-built .brow:last-child{border-radius:0 0 12px 12px}
.v-built .brow.yes{border-left-color:#039855}.v-built .brow.idle{border-left-color:#DC6803}
.v-built .brow.others{border-left-color:#999AFD}.v-built .brow.unk{border-left-color:#667085}
.v-built .bst{font-weight:600;white-space:nowrap}
.v-built .bst i{font-style:normal;display:inline-block;width:1.4em}
.v-built .bnm{color:#15172F}
.v-built .bct{font-size:14px;color:#344054;text-align:right}
.v-built .bex{grid-column:2 / 4;font-size:12px;color:#475467;margin:0}
.v-built .bwhy{grid-column:2 / 4;font-size:12px;color:#667085;margin:0}
.v-built details.bex>summary{font-size:12px;color:#3C3ED4;width:max-content}
.v-built .bsrc{font-size:12px;color:#667085;margin:4px 0 0;overflow-wrap:anywhere}
.v-built .bsk{grid-column:2 / 4;margin:6px 0 0;font-size:13px;color:#344054}
.v-built .bsk .ssum{margin:0 0 4px;font-weight:600;color:#15172F}
.v-built .bsk .skey{margin:0 0 8px;font-size:12px;color:#667085}
.v-built .bsk ul{list-style:none;margin:0;padding:0}
.v-built .bsk li.sk{padding:6px 0;border-top:1px solid #EAECF0}
.v-built .bsk .skh{display:flex;flex-wrap:wrap;gap:2px 12px;align-items:baseline}
.v-built .bsk .skn{font-weight:600;color:#15172F;overflow-wrap:anywhere;min-width:120px}
.v-built .bsk .skd{white-space:nowrap;font-weight:500}
.v-built .bsk .skd i{font-style:normal;letter-spacing:.1em;margin-right:6px;color:#3C3ED4}
.v-built .bsk .skw{margin:2px 0 0;font-size:12px;color:#667085;overflow-wrap:anywhere}
.v-built .bsk .skflag{margin:2px 0 0;font-size:12px;color:#B54708}
.v-built .bsk .skflag b{font-weight:600}
.v-built .bsk details.sknu{margin-top:8px}
.v-built .bsk details.sknu>summary{font-size:12px;color:#3C3ED4;width:max-content;cursor:pointer}
@media (max-width:720px){.v-built .brow{grid-template-columns:1fr}.v-built .bct,.v-built .bex,.v-built .bwhy,.v-built .bsk{grid-column:1;text-align:left}}
"""


def _count(r):
    """The count column: their own things and what ran, in plain words. Both are numbers only."""
    bits = []
    if r["items"]:
        bits.append(f'{r["items"]:,} of yours' if r["kind"] == "build" else f'{r["items"]:,} set up')
    if r["calls"]:
        bits.append(f'{r["calls"]:,} {r["unit"]}')
    return " · ".join(bits)


# depth -> word. Depth is read from how the skill was used, never a level of the person.
DEPTH_WORD = {1: "reads one file", 2: "progressive disclosure", 3: "runs code", 4: "evals seen running it"}
DEPTH_KEY = ("Depth 1 one file read whole · 2 progressive disclosure (its own files opened only in the uses that need them) · "
             "3 runs code (a script of its own run after it loaded, or a !`command` that runs when it loads) · "
             "4 evals seen running it (an evals run, or a headless run given a prompt from a test file).")
_FILE_IN_PARENS = re.compile(r"\s*\((?!\d+x\)|!`)[^)]*\)")  # "(refs/a.md)" -> dropped; "(2x)" and "(!`...`)" stay
_SCRIPT_NAME = re.compile(r"\bits script .+? (?=was run after it loaded)")


def _mask_why(why):
    """The member's copy of a skill's `why`: the files it names are dropped (board.py puts a file only inside parentheses or after
    "its script"), so no skill or file name of theirs leaves the laptop. Words of the sentence are never matched against skill names:
    a skill called `file` or `run` would damage the sentence."""
    return _SCRIPT_NAME.sub("its script ", _FILE_IN_PARENS.sub("", str(why or ""))).strip()


def _skill(esc, sk, n, member):
    d = sk.get("depth")
    name = f"own-skill-{n}" if member else sk.get("name") or ""
    why = _mask_why(sk.get("why")) if member else str(sk.get("why") or "")
    if d in DEPTH_WORD:
        depth = f'<span class="skd"><i aria-hidden="true">{"●" * d}{"○" * (4 - d)}</i>Depth {d} · {esc(DEPTH_WORD[d])}</span>'
    else:
        depth = '<span class="skd"><i aria-hidden="true">?</i>Depth not told</span>'
    flag = ""
    if sk.get("too_big_one_file"):
        flag = (f'<p class="skflag"><b><span aria-hidden="true">⚠</span> Flag:</b> {int(sk.get("lines") or 0):,} lines in one file '
                f'with nothing else. Try next: {esc(sk.get("try_next") or "")}.</p>')
    note = f'<p class="skw">{esc(sk["evals_note"])}</p>' if sk.get("evals_note") else ""
    return (f'<li class="sk"><div class="skh"><span class="skn">{esc(name)}</span>{depth}</div>'
            f'<p class="skw">{esc(why)}</p>{flag}{note}</li>')


def _plain_summary(skills, items):
    """The summary in plain words, counted from the list itself (his 11-06: "depth 4 0 · 3 2" read like code). The row's
    count holds skills AND commands, so the line says how many of them are skills."""
    used = [sk for sk in skills if sk.get("used") is True]
    by = {d: sum(1 for sk in used if sk.get("depth") == d) for d in (4, 3, 2, 1)}
    unsure = sum(1 for sk in skills if sk.get("used") is None)
    parts = [f"{by[4]} with evals seen running it" if by[4] else "",
             f"{by[3]} run code" if by[3] else "",
             f"{by[2]} use progressive disclosure" if by[2] else "",
             f"{by[1]} read one file" if by[1] else ""]
    head = f"{len(skills)} skills" + (f" (the rest of the {items:,} are commands)" if items and items > len(skills) else "")
    line = f"{head}; {len(used)} used in the last 30 days: " + (" · ".join(x for x in parts if x) or "none with a depth told")
    flagged = sum(1 for sk in skills if sk.get("too_big_one_file"))
    evals_idle = sum(1 for sk in skills if sk.get("has_evals_not_seen_running"))
    extra = [f"{unsure} can't tell apart from an installed skill with the same name" if unsure else "",
             f"{flagged} too big for one file" if flagged else "",
             f"{evals_idle} with evals never seen running" if evals_idle else ""]
    return line + "".join(f"; {x}" for x in extra if x) + "."


def skills_block(esc, row, member):
    """The per-skill depth under the skills row. Old run (no fields) or the can't-tell row: nothing. Skills that go deeper than
    one file are listed; the ones that read one file and the unused ones fold away (his 11-06: 21 depth-1 rows were a wall)."""
    summary, skills = row.get("skills_summary"), row.get("skills") or []
    if not summary:
        return ""
    numbered = [(i + 1, sk) for i, sk in enumerate(skills) if isinstance(sk, dict)]
    if not numbered:
        return f'<div class="bsk"><p class="ssum">{esc(summary)}</p></div>'
    out = [f'<div class="bsk"><p class="ssum">{esc(_plain_summary([sk for _, sk in numbered], row.get("items") or 0))}</p>',
           f'<p class="skey">{esc(DEPTH_KEY)}</p>']
    used = [x for x in numbered if x[1].get("used") is not False]
    deep = [x for x in used if (x[1].get("depth") or 0) >= 2 or x[1].get("too_big_one_file")]
    flat = [x for x in used if x not in deep and x[1].get("depth") == 1]
    unsure = [x for x in used if x not in deep and x not in flat]   # used: null, a name an installed skill also has
    idle = [x for x in numbered if x[1].get("used") is False]
    if deep:
        out.append('<ul>' + "".join(_skill(esc, sk, n, member) for n, sk in deep) + '</ul>')
    if flat:
        out.append(f'<details class="sknu"><summary>Show {len(flat)} that read one file</summary>'
                   '<ul>' + "".join(_skill(esc, sk, n, member) for n, sk in flat) + '</ul></details>')
    if unsure:
        out.append(f'<details class="sknu"><summary>Show {len(unsure)} we can\'t tell apart</summary><p class="skey">An installed '
                   'skill has the same name, so its uses can\'t be told apart from yours.</p>'
                   '<ul>' + "".join(_skill(esc, sk, n, member) for n, sk in unsure) + '</ul></details>')
    if idle:
        out.append(f'<details class="sknu"><summary>Show {len(idle)} not used</summary><p class="skey">Not used in the last 30 days.</p>'
                   '<ul>' + "".join(_skill(esc, sk, n, member) for n, sk in idle) + '</ul></details>')
    out.append("</div>")
    return "".join(out)


def render(ctx):
    esc = ctx.get("esc") or html.escape
    b = (ctx.get("v15_result") or {}).get("board") or {}
    rows = b.get("rows") or []
    if not rows:
        return '<p class="empty">not in this run</p>'
    member = ctx.get("member")
    tally = {s: sum(1 for r in rows if r["state"] == s) for s in ORDER_OF_STATES}
    summ = "".join(f'<span><b>{tally[s]}</b> {esc(STATE[s][1].lower())}</span>' for s in ORDER_OF_STATES if tally[s])
    out = [f'<p class="bsum">{summ}</p><ul class="brows">']
    for r in sorted(rows, key=lambda r: ORDER_OF_STATES.index(r["state"])):
        icon, word, means, cls = STATE[r["state"]]
        extra = ""
        if not member:
            if r.get("examples"):
                extra += f'<p class="bex">e.g. {esc(", ".join(r["examples"]))}</p>'
            extra += f'<p class="bwhy">{esc(r.get("why") or "")}</p>'
            if r.get("source"):
                extra += f'<details class="bex"><summary>how we know</summary><p class="bsrc">{esc(r["source"])}</p></details>'
        elif r["state"] == "can't tell":
            extra = f'<p class="bwhy">{esc(r.get("why") or "")}</p>'  # a reason, never a name: board.py's can't-tell reasons hold none
        if r.get("id") == "cap-skills-own":
            extra += skills_block(esc, r, member)
        out.append(f'<li class="brow {cls}" title="{esc(means)}"><span class="bst"><i aria-hidden="true">{icon}</i>{esc(word)}</span>'
                   f'<span class="bnm">{esc(r["name"])}</span><span class="bct">{esc(_count(r))}</span>{extra}</li>')
    out.append("</ul>")
    return "".join(out)
