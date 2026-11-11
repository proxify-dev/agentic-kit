"""What you've built: the parts of a setup, one row each, read from facts on the laptop (engine/board.py).

Each row says what the part is, whether it is theirs and whether it ran, and why it matters, in sentences: on a
tester's page (11-11) the rows were a state, a name and "6 of yours · 3 calls", and nothing said what a part is for.
Rows are grouped by state (an icon and a word; colour only adds to them), and the parts that weren't seen fold behind
one line instead of standing as a wall of "Not seen". What a row counts is board.py's and is not changed here.

The person's own page names their things: their skills by name, up to three examples per row. ctx["masked"]
(frame.py --masked) is the copy made to leave the laptop: a skill is own-skill-N, the files inside a `why` are
dropped, and no row gives examples or the board's sentence (the count only). Proxify's copy adds the field each
state rests on.

Under the "Skills and commands you wrote" row the page lists the per-skill depth the board reads (engine/board.py skill_depth):
its summary line, the skills that were used deepest first, the ones not used in the last 30 days behind a toggle. Depth is the
depth of use, never a level of the person; the over-250-lines flag is a note with a next step, never a minus on a depth.
"""
import html
import re


ORDER = 26
TITLE = "What you've built"
QUESTION = "Which parts of the harness have you built, which do you only use from others, and did what you built run lately?"
QUESTION_MEMBER = "Which parts of your setup did you build yourself, and did they run in the last 30 days?"
PROXIFY_ONLY = False

# state -> (icon, word, the group's heading, kind of the left edge)
STATE = {"built and used": ("●", "Yours, in use", "Yours, and in use in the last 30 days", "yes"),
         "built, not used lately": ("◐", "Yours, not used lately", "Yours, but nothing shows it running in the last 30 days", "idle"),
         "uses others'": ("⇄", "In use, made by others", "In use, made by others or built into Claude Code", "others"),
         "not seen": ("○", "Not seen", "Not seen on this laptop", "none"),
         "can't tell": ("?", "Couldn't tell", "Couldn't tell from the logs", "unk")}
ORDER_OF_STATES = list(STATE)

# row id -> (what the part is, why it matters). One sentence each, to the person, in the field's terms (labs/CLAUDE.md).
ABOUT = {
    "cap-skills-own": ("Instructions you wrote for a task you repeat. Claude loads one only when the task comes up.",
                       "Know-how you would otherwise paste in stays out of every session until it is needed."),
    "cap-skills-installed": ("Skills and plugins someone else wrote, installed here.",
                             "Each one adds its name and description to the start of every session, so the ones you never call still take room."),
    "cap-instructions": ("The files Claude reads at the start of a session in a project, such as CLAUDE.md.",
                         "This is where the agent learns what it can't work out from the code. Without them you type it again."),
    "cap-hooks": ("Scripts that run by themselves at set moments of a session: before a tool call, or when the agent stops.",
                  "A hook makes a rule hold every time. An instruction only asks."),
    "cap-mcp": ("Connections that give Claude tools for other systems, such as a database, Slack or a browser.",
                "The agent fetches what it needs, so you don't paste it in. Each server also adds its tool list to the start of every session."),
    "cap-subagents": ("Agent roles you defined, each with its own instructions, tools and model.",
                      "Searching and reading long output happen in the sub-agent's context, so the main session stays clean."),
    "cap-teams": ("Several agents working on one job and messaging each other.",
                  "It fits a job whose parts need to talk to each other. It costs more than sub-agents."),
    "cap-workflows": ("Scripts that spread one job over many sub-agents.",
                      "A big sweep, such as an audit or a migration, gets done without filling one session's context."),
    "cap-parallel": ("More than one session at a time, each in its own worktree (a separate working copy of the repo).",
                     "Two pieces of work run at once and their edits don't collide."),
    "cap-memory": ("Notes kept in files between sessions, which later sessions read.",
                   "A new session starts knowing what the last one learned, without you explaining it again."),
    "cap-model-routing": ("Choosing which model does which step: by typing /model, or in a sub-agent's own file.",
                          "A fast, cheap model for searching and reading and the strongest one for judgment gives the same result for less cost and waiting."),
    "cap-permissions": ("Rules in your settings for what Claude may do alone, must ask about, or may never do.",
                        "Routine steps stop interrupting you, and the risky ones still wait for you."),
    "cap-event-runs": ("Agent runs that a schedule or an event starts, not your typing.",
                       "Work gets done while you are away."),
    "cap-evals": ("Evals: a saved set of test cases for your own skills, sub-agents or hooks, run again after a change.",
                  "They show whether a change to your setup made it better or worse, so you don't have to guess."),
    "cap-own-rig": ("A script of yours that starts Claude and hands it work (a headless run: claude -p).",
                    "The agent works through a list or a pipeline without you at the keyboard."),
    "cap-plugins-own": ("Your skills, hooks and sub-agents packaged so they install together.",
                        "Your setup moves to another machine, or to a teammate, in one step."),
    "cap-sdk": ("Code of yours that runs agents through the Agent SDK.",
                "You decide what the agent gets, when it stops and what happens with its result."),
    "cap-mcp-write": ("An MCP server whose code is yours.",
                      "You gave the agent a tool that didn't exist, shaped for your work."),
    "cap-output-styles": ("A saved way for Claude to write its answers.",
                          "Answers come in the shape you read fastest, in every session."),
    "cap-telemetry": ("A record of what your sessions cost and did.",
                      "You can see which runs were worth their cost."),
}

CSS = """
.v-built .blede{font-size:16px;line-height:1.55;color:#15172F;max-width:68ch;margin:0 0 24px}
.v-built h3.bgrp{display:flex;align-items:baseline;gap:8px;margin:24px 0 8px;font-size:14px;font-weight:600;color:#15172F}
.v-built h3.bgrp i{font-style:normal;display:inline-block;width:1.2em}
.v-built h3.bgrp span{font-weight:400;color:#667085}
.v-built ul.brows{list-style:none;margin:0;padding:0;background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px}
.v-built .brow{padding:14px 16px;border-top:1px solid #EAECF0;border-left:3px solid #D0D5DD}
.v-built .brow:first-child{border-top:0;border-radius:12px 12px 0 0}
.v-built .brow:last-child{border-radius:0 0 12px 12px}
.v-built .brow:only-child{border-radius:12px}
.v-built .brow.yes{border-left-color:#039855}.v-built .brow.idle{border-left-color:#DC6803}
.v-built .brow.others{border-left-color:#999AFD}.v-built .brow.unk{border-left-color:#667085}
.v-built .bhead{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px}
.v-built .bnm{margin:0;font-size:14px;font-weight:600;color:#15172F}
.v-built .bct{font-size:13px;color:#475467}
.v-built .bwhat{margin:4px 0 0;color:#344054;max-width:76ch}
.v-built .bsaw{margin:4px 0 0;font-size:13px;color:#344054;max-width:76ch;overflow-wrap:anywhere}
.v-built .bmat{margin:4px 0 0;font-size:13px;color:#475467;max-width:76ch}
.v-built .bmat b,.v-built .bsaw b{font-weight:600;color:#15172F;margin-right:6px}
.v-built details.bex{margin-top:6px}
.v-built details.bex>summary{font-size:12px;color:#3C3ED4;width:max-content}
.v-built .bsrc{font-size:12px;color:#667085;margin:4px 0 0;overflow-wrap:anywhere}
.v-built details.bnone{margin:24px 0 0}
.v-built details.bnone>summary{font-size:14px;font-weight:500;color:#3C3ED4;width:max-content}
.v-built details.bnone>p{margin:8px 0;color:#475467;max-width:76ch}
.v-built .bsk{margin:10px 0 0;font-size:13px;color:#344054}
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
"""


def _count(r):
    """A row's numbers in plain words: how many of their own things, and how often they ran. Numbers only, no names."""
    bits = []
    if r.get("items"):
        bits.append(f'{r["items"]:,} of yours' if r.get("kind") == "build" else f'{r["items"]:,} set up')
    if r.get("calls"):
        bits.append(f'{r["calls"]:,} {r.get("unit") or "uses"} in the last 30 days')
    return " · ".join(bits)


def _saw(r):
    """The board's own sentence for the row, which carries its numbers ("5 deny or ask rules of yours; they applied in
    588 sessions"). "The window" is the tool's word for the days of logs it read."""
    return re.sub(r"\bin the window\b", "in the last 30 days", str(r.get("why") or "")).strip()


# depth -> word. Depth is read from how the skill was used, never a level of the person.
DEPTH_WORD = {1: "reads one file", 2: "progressive disclosure", 3: "runs code", 4: "evals seen running it"}
DEPTH_KEY = ("Depth is how far a skill goes when it is used. 1 one file read whole · 2 progressive disclosure (its own files opened only in the uses that need them) · "
             "3 runs code (a script of its own run after it loaded, or a !`command` that runs when it loads) · "
             "4 evals seen running it (an evals run, or a headless run given a prompt from a test file).")
_FILE_IN_PARENS = re.compile(r"\s*\((?!\d+x\)|!`)[^)]*\)")  # "(refs/a.md)" -> dropped; "(2x)" and "(!`...`)" stay
_SCRIPT_NAME = re.compile(r"\bits script .+? (?=was run after it loaded)")


def _mask_why(why):
    """A skill's `why` on the masked copy: the files it names are dropped (board.py puts a file only inside parentheses or after
    "its script"), so no skill or file name of theirs leaves the laptop. Words of the sentence are never matched against skill names:
    a skill called `file` or `run` would damage the sentence."""
    return _SCRIPT_NAME.sub("its script ", _FILE_IN_PARENS.sub("", str(why or ""))).strip()


def _skill(esc, sk, n, masked):
    d = sk.get("depth")
    name = f"own-skill-{n}" if masked else sk.get("name") or ""
    why = _mask_why(sk.get("why")) if masked else str(sk.get("why") or "")
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


def skills_block(esc, row, masked):
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
        out.append('<ul>' + "".join(_skill(esc, sk, n, masked) for n, sk in deep) + '</ul>')
    if flat:
        out.append(f'<details class="sknu"><summary>Show {len(flat)} that read one file</summary>'
                   '<ul>' + "".join(_skill(esc, sk, n, masked) for n, sk in flat) + '</ul></details>')
    if unsure:
        out.append(f'<details class="sknu"><summary>Show {len(unsure)} we can\'t tell apart</summary><p class="skey">An installed '
                   'skill has the same name, so its uses can\'t be told apart from yours.</p>'
                   '<ul>' + "".join(_skill(esc, sk, n, masked) for n, sk in unsure) + '</ul></details>')
    if idle:
        out.append(f'<details class="sknu"><summary>Show {len(idle)} not used</summary><p class="skey">Not used in the last 30 days.</p>'
                   '<ul>' + "".join(_skill(esc, sk, n, masked) for n, sk in idle) + '</ul></details>')
    out.append("</div>")
    return "".join(out)


def _lede(tally):
    """The tab's first sentence: how much of the setup is theirs, how much of that ran, how much they use from others."""
    used, idle, others = tally["built and used"], tally["built, not used lately"], tally["uses others'"]
    part = lambda n: f"{n} part{'' if n == 1 else 's'}"  # noqa: E731
    if used + idle:
        s = f"You built {part(used + idle)} of your setup yourself"
        if not idle:
            s += f", and {'it' if used == 1 else 'all of them' if used > 2 else 'both'} ran in the last 30 days."
        elif used:
            s += f"; {used} of them ran in the last 30 days."
        else:
            s += ", and nothing shows them running in the last 30 days."
    else:
        s = "Nothing on this laptop shows a part of the setup that you built yourself yet."
    if others:
        s += f" {part(others).capitalize()} you use as others made them."
    return s


def _row(esc, r, member, masked):
    icon, word, _, cls = STATE[r["state"]]
    what, matters = ABOUT.get(r.get("id"), ("", ""))
    out = [f'<li class="brow {cls}"><div class="bhead"><h4 class="bnm">{esc(r["name"])}</h4>']
    saw = _saw(r)
    if masked:
        # the copy that leaves the laptop: numbers only; a can't-tell row keeps its reason (board.py's hold no name)
        out.append(f'<span class="bct">{esc(_count(r))}</span></div>')
        if r["state"] == "can't tell" and saw:
            out.append(f'<p class="bsaw">{esc(saw)}</p>')
    else:
        out.append("</div>")
    if what:
        out.append(f'<p class="bwhat">{esc(what)}</p>')
    if not masked and (saw or r.get("examples")):
        eg = f' For example: {esc(", ".join(r["examples"]))}.' if r.get("examples") else ""
        out.append(f'<p class="bsaw"><b>On this laptop</b>{esc(saw[:1].upper() + saw[1:])}{"." if saw and saw[-1] not in ".!?" else ""}{eg}</p>')
    if matters and r["state"] != "can't tell":
        out.append(f'<p class="bmat"><b>Why it matters</b>{esc(matters)}</p>')
    if not member and r.get("source"):
        out.append(f'<details class="bex"><summary>how we know</summary><p class="bsrc">{esc(r["source"])}</p></details>')
    if r.get("id") == "cap-skills-own":
        out.append(skills_block(esc, r, masked))
    out.append("</li>")
    return "".join(out)


def render(ctx):
    esc = ctx.get("esc") or html.escape
    b = (ctx.get("v15_result") or {}).get("board") or {}
    rows = [r for r in b.get("rows") or [] if r.get("state") in STATE]
    if not rows:
        return '<p class="empty">not in this run</p>'
    member, masked = ctx.get("member"), bool(ctx.get("masked"))
    tally = {s: sum(1 for r in rows if r["state"] == s) for s in ORDER_OF_STATES}
    out = [f'<p class="blede">{esc(_lede(tally))}</p>']
    for state in ORDER_OF_STATES:
        mine = [r for r in rows if r["state"] == state]
        if not mine:
            continue
        icon, word, heading, _ = STATE[state]
        if state == "not seen":
            # one line, not a wall of "Not seen": what each part is and what it would give them is one click away
            names = ", ".join(r["name"] for r in mine)
            out.append(f'<details class="bnone"><summary><span aria-hidden="true">{icon}</span> {len(mine)} '
                       f'part{"" if len(mine) == 1 else "s"} not seen on this laptop</summary>'
                       f'<p>We looked in your files and logs for each of these and found none: {esc(names)}. '
                       "None of them is required. Each is here with what it would give you.</p>"
                       f'<ul class="brows">{"".join(_row(esc, r, member, masked) for r in mine)}</ul></details>')
            continue
        out.append(f'<h3 class="bgrp"><i aria-hidden="true">{icon}</i>{esc(heading)} <span>{len(mine)}</span></h3>'
                   f'<ul class="brows">{"".join(_row(esc, r, member, masked) for r in mine)}</ul>')
    return "".join(out)
