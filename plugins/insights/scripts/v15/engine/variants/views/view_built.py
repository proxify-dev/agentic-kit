"""What you've built: what the person built for their agent, and whether it got used in the last 30 days.

Why it looks like this (his 11-11 on the tab before it: "really hard to read"; on the new Review: "go further… on this
view as well"). Every kind of thing was a long text list, with words like "Depth 3 · progressive disclosure" and a
legend paragraph to decode them, and the rule-file line said "1632 instruction files of yours", which counted copies and
files his tools generate. Now, in the Review's visual language:
  1. at a glance: one tile per kind they have, a big number with what it counts and a meter of how many got used
  2. per kind: one plain line of what it is, then the things sorted by use, the top 8 first, the unused folded in one group;
     a skill shows how far it goes as four plain steps (reads one file → opens more files when needed → runs code →
     tested automatically) instead of "Depth N"
  3. hooks as a picture: a session's life as a line, each hook pinned at the moment it fires, with how often it did
  4. rule files counted only where the count holds: the busiest projects the setup read mapped (setup-map.json), since
     elsewhere the scan can't yet tell files they wrote from copies and generated ones (memory/scanner/issues/
     cached-plugin-copies-counted-as-their-rule-files.md)
What a kind counts, and its list, are board.py's (engine/board.py rows, `list`: one entry per thing, copies counted
once), so a number and its list never disagree; nothing here decides whose a thing is.

The person's own page names their things. ctx["masked"] (frame.py --masked) is the copy made to leave the laptop: a
thing is own-skill-N, project-N…, with no description, path or file name. Proxify's copy adds where each count comes from.
"""
import html
import os
import re

ORDER = 26
TITLE = "What you've built"
QUESTION = "What they built for their agent, and whether it got used in the last 30 days."
QUESTION_MEMBER = "What you built for your agent, and whether it got used in the last 30 days."
PROXIFY_ONLY = False

TOP = 8  # things shown per kind before "Show all"

# kind -> (board row, the tile's words after its number, section title, what it is in one line, what a use is called)
KINDS = [
    ("skills", "cap-skills-own", "skills you wrote", "Skills",
     "Instructions you wrote for a task you repeat. Claude loads one only when the task comes up.", "uses"),
    ("commands", "cap-skills-own", "commands you wrote", "Commands",
     "Prompts you saved to start with /name.", "uses"),
    ("rules", "cap-instructions", "rule files in your {n} busiest projects", "Rule files",
     "Files such as CLAUDE.md that Claude reads before you type, in the project or the folder they sit in.", "sessions"),
    ("agents", "cap-subagents", "sub-agents you defined", "Sub-agents",
     "Agent roles you defined, each with its own instructions, tools and model.", "launches"),
    ("hooks", "cap-hooks", "hooks you set up", "Hooks",
     "Scripts that run by themselves at set moments of a session. A hook makes a rule hold every time; an instruction only asks.", "runs"),
    ("plugins", "cap-plugins-own", "plugins you wrote", "Plugins",
     "Your skills, hooks and sub-agents packaged so they install together.", "uses"),
    ("tools", "cap-mcp-write", "tools you built for the agent", "Tools you built for the agent",
     "Each is an MCP server of yours: a program that gives the agent tools it didn't have.", "calls"),
    ("programs", "cap-own-rig", "projects that script the agent", "Programs that run the agent",
     "Scripts of yours that start Claude and hand it work, with nobody typing (headless runs).", "runs"),
    ("evals", "cap-evals", "sets of evals you keep", "Evals",
     "Saved test cases for your skills, sub-agents or hooks, run again after a change.", "runs"),
    ("memory", "cap-memory", "memory stores you keep", "Memory",
     "Notes kept in files between sessions, which later sessions read.", "reads"),
    ("workflows", "cap-workflows", "workflows you saved", "Workflows",
     "Scripts that spread one job over many sub-agents.", ""),
]
USED_WORD = {"skills": "used", "commands": "used", "agents": "launched", "hooks": "ran", "plugins": "in use",
             "tools": "called", "programs": "ran the agent", "evals": "ran", "memory": "read", "workflows": "used"}
# a skill's step, from board.skill_depth: how far it goes when it is used. Never a level of the person.
STEPS = ["Reads one file", "Opens more files when needed", "Runs code", "Tested automatically"]
STEP_TIP = ["Its SKILL.md is read whole; nothing else of it is opened.",
            "Its own reference files are opened only in the uses that need them.",
            "A script of its own runs after it loads, or its SKILL.md runs a command when it loads.",
            "Evals were seen running it."]
# a session's life, left to right; a moment is drawn only when a hook of theirs fires there, the main six always
MOMENTS = [("Session starts", ("SessionStart",), True), ("You send a message", ("UserPromptSubmit", "UserPromptExpansion"), True),
           ("Before a tool runs", ("PreToolUse", "PermissionRequest"), True), ("After it runs", ("PostToolUse", "PostToolUseFailure"), True),
           ("A sub-agent starts or stops", ("SubagentStart", "SubagentStop"), False), ("Claude waits for you", ("Notification",), False),
           ("Agent stops", ("Stop",), True), ("Before compaction", ("PreCompact",), False), ("Session ends", ("SessionEnd",), True)]
STATE_WORD = {"built and used": "Yours, in use", "built, not used lately": "Yours, not used lately", "uses others'": "In use, made by others",
              "not seen": "Not seen", "can't tell": "Couldn't tell"}
# what each kind would give, for the parts not seen on this laptop (one sentence each, to the person)
GIVES = {"cap-skills-own": "Know-how you would otherwise paste in stays out of every session until it is needed.",
         "cap-hooks": "A hook makes a rule hold every time. An instruction only asks.",
         "cap-subagents": "Searching and reading long output happen in the sub-agent's context, so the main session stays clean.",
         "cap-teams": "Several agents on one job, messaging each other. It costs more than sub-agents.",
         "cap-workflows": "A big sweep, such as an audit or a migration, gets done without filling one session's context.",
         "cap-parallel": "Two pieces of work run at once, each in its own worktree, and their edits don't collide.",
         "cap-memory": "A new session starts knowing what the last one learned, without you explaining it again.",
         "cap-evals": "They show whether a change to your setup made it better or worse.",
         "cap-own-rig": "The agent works through a list or a pipeline without you at the keyboard.",
         "cap-plugins-own": "Your setup moves to another machine, or to a teammate, in one step.",
         "cap-mcp-write": "You give the agent a tool that didn't exist, shaped for your work.",
         "cap-event-runs": "Work gets done while you are away.", "cap-permissions": "Routine steps stop interrupting you.",
         "cap-output-styles": "Answers come in the shape you read fastest.", "cap-instructions": "The agent learns what it can't work out from the code.",
         "cap-mcp": "The agent fetches what it needs from other systems.", "cap-skills-installed": "Skills others wrote, installed here.",
         "cap-model-routing": "A fast model for reading and the strongest one for judgment.", "cap-telemetry": "You see what your sessions cost.",
         "cap-sdk": "You decide what the agent gets and when it stops."}

_SVG = ('<svg class="{c}" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true">{d}</svg>')
CHEV = _SVG.format(c="bt-chev", d='<path d="M6 4l4 4-4 4"/>')

CSS = """
.v-built{font-variant-numeric:normal}
.v-built p{margin:0}
.v-built .bt-num,.v-built .it .u,.v-built .hk-chip b,.v-built .rf .u{font-variant-numeric:tabular-nums}
.v-built summary{list-style:none}.v-built summary::-webkit-details-marker{display:none}
.v-built .bt-chev{width:14px;height:14px;color:#3C3ED4;transition:transform .15s ease;flex:none}
.v-built details[open]>summary .bt-chev{transform:rotate(90deg)}
.v-built .bt-more>summary,.v-built .bt-fold>summary{display:inline-flex;align-items:center;gap:6px;color:#3C3ED4;font-size:13px;font-weight:500;margin-top:10px}

/* at a glance */
.v-built .tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(164px,1fr));gap:12px}
.v-built .tile{display:grid;gap:6px;align-content:start;background:#FFFFFF;border:1px solid #EAECF0;border-radius:16px;padding:16px 18px;
  text-decoration:none;color:inherit;transition:border-color .15s ease,box-shadow .15s ease}
.v-built .tile:hover{border-color:#999AFD;box-shadow:0 4px 12px -6px rgba(82,88,251,.35)}
.v-built .bt-num{font-size:36px;font-weight:600;line-height:1;letter-spacing:-.02em;color:#15172F}
.v-built .tile .lab{font-size:13.5px;color:#475467;line-height:1.35;min-height:2.7em}
.v-built .tile .gap{height:6px;margin-top:6px}
.v-built .meter{height:6px;border-radius:999px;background:#EEEFFE;overflow:hidden;margin-top:6px}
.v-built .meter i{display:block;height:100%;border-radius:999px;background:#5258FB}
.v-built .tile .used{font-size:12.5px;color:#344054;line-height:1.4}
.v-built .tile .used b{font-weight:600;color:#15172F}

/* each kind */
.v-built .kd{margin-top:48px;scroll-margin-top:136px}
.v-built .kd-h{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.v-built .kd-h h3{font-size:18px;font-weight:600;margin:0;color:#15172F}
.v-built .kd-h .ct{font-size:13px;color:#667085}
.v-built .kd-what{color:#667085;margin:4px 0 14px !important;max-width:80ch}
.v-built .lst{list-style:none;margin:0;padding:0;background:#FFFFFF;border:1px solid #EAECF0;border-radius:14px;overflow:hidden}
.v-built .it{display:grid;grid-template-columns:minmax(140px,220px) minmax(0,1fr) auto 96px;gap:16px;align-items:center;padding:10px 16px;min-height:48px}
.v-built .lst.nostep .it{grid-template-columns:minmax(140px,260px) minmax(0,1fr) 96px}
.v-built .it+.it{border-top:1px solid #EAECF0}
.v-built .it .nm{font-weight:600;color:#15172F;overflow-wrap:anywhere}
.v-built .it .nm small{font-weight:500;font-size:11px;color:#667085;margin-left:6px;white-space:nowrap}
.v-built .it .ab{font-size:13px;color:#667085;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v-built .it .u{text-align:right;font-size:13px;color:#344054;white-space:nowrap}
.v-built .it .u b{font-weight:600;color:#15172F}
.v-built .it .u.zero{color:#949BAD}
.v-built .step{display:flex;align-items:center;gap:8px;font-size:12px;color:#475467;white-space:nowrap;width:212px}
.v-built .step .seg{display:flex;gap:3px}
.v-built .step .seg i{width:14px;height:6px;border-radius:2px;background:#EAECF0}
.v-built .step .seg i.on{background:#5258FB}
.v-built .step.none{color:#949BAD}
.v-built .bt-fold .lst{margin-top:8px}
.v-built .bt-more .lst{margin-top:8px}
.v-built .names{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.v-built .names span{font-size:12px;color:#475467;background:#FFFFFF;border:1px solid #EAECF0;border-radius:6px;padding:2px 8px}
.v-built .note{font-size:13px;color:#667085;margin-top:10px !important;max-width:80ch}

/* hooks: a session's life */
.v-built .hk{background:#FFFFFF;border:1px solid #EAECF0;border-radius:16px;padding:24px 20px 20px;overflow-x:auto}
.v-built .hk ol{list-style:none;margin:0;padding:0;display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);position:relative}
.v-built .hk ol::before{content:"";position:absolute;left:12px;right:12px;top:7px;height:2px;background:#D5D5FE;border-radius:2px}
.v-built .hk li{position:relative;padding:0 4px;min-width:0}
.v-built .hk .dot{display:block;width:16px;height:16px;border-radius:50%;background:#FFFFFF;border:2px solid #999AFD;margin-left:4px;position:relative}
.v-built .hk li.has .dot{background:#5258FB;border-color:#5258FB}
.v-built .hk .mo{display:block;font-size:13px;font-weight:600;color:#15172F;margin:10px 0 8px;line-height:1.3;min-height:2.6em}
.v-built .hk li:not(.has) .mo{color:#949BAD;font-weight:500}
.v-built .hk .mo small{display:block;font-size:11px;font-weight:500;color:#667085}
.v-built .hk-chips{display:grid;gap:6px}
.v-built .hk-chip{display:flex;justify-content:space-between;gap:8px;align-items:baseline;font-size:12px;color:#344054;background:#F5F5FF;
  border:1px solid #DDDEFE;border-radius:8px;padding:4px 8px;min-width:0}
.v-built .hk-chip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.v-built .hk-chip b{font-weight:600;color:#3C3ED4}
.v-built .hk-chip.idle{background:#FFFFFF;border-style:dashed;border-color:#D0D5DD;color:#667085}
.v-built .hk-chip.idle b{color:#949BAD;font-weight:500}

/* rule files, the rest */
.v-built .rf .it{grid-template-columns:minmax(140px,240px) minmax(0,1fr) 120px}
.v-built .rf .ab{white-space:normal}
.v-built .also{margin-top:48px;display:grid;gap:10px}
.v-built .also>div{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:13px;color:#475467}
.v-built .also b.k{font-weight:600;color:#15172F;margin-right:4px}
.v-built .also span.c{background:#FFFFFF;border:1px solid #EAECF0;border-radius:999px;padding:3px 10px;color:#344054}
.v-built .bt-fold.none>summary{color:#475467}
.v-built .bt-fold ul.gv{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:6px}
.v-built .bt-fold ul.gv li{font-size:13px;color:#475467}.v-built .bt-fold ul.gv b{color:#15172F;font-weight:600}
.v-built .src{font-size:12px;color:#667085;overflow-wrap:anywhere}

@media (max-width:720px){
  .v-built .tiles{grid-template-columns:1fr 1fr;gap:8px}
  .v-built .tile{padding:14px}
  .v-built .bt-num{font-size:28px}
  .v-built .tile .lab{font-size:13px}
  .v-built .kd{margin-top:40px}
  .v-built .it,.v-built .lst.nostep .it,.v-built .rf .it{grid-template-columns:minmax(0,1fr) auto;gap:4px 12px;padding:10px 14px}
  .v-built .it .ab{grid-column:1/-1;grid-row:2;white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
  .v-built .it .step{grid-column:1/-1;grid-row:3;width:auto}
  .v-built .it .u{grid-column:2;grid-row:1}
  .v-built .hk{padding:16px}
  .v-built .hk ol{grid-auto-flow:row;grid-auto-columns:auto;min-width:0;gap:14px}
  .v-built .hk ol::before{left:7px;right:auto;top:8px;bottom:8px;width:2px;height:auto}
  .v-built .hk li{display:grid;grid-template-columns:16px minmax(0,1fr);column-gap:12px;padding:0}
  .v-built .hk .dot{margin:2px 0 0}
  .v-built .hk .mo{margin:0 0 6px;min-height:0}
  .v-built .hk .mo,.v-built .hk-chips{grid-column:2}
  .v-built .hk-chips{display:flex;flex-wrap:wrap}
}
"""


def _n(x):
    return f"{x:,}" if isinstance(x, int) else str(x)


def _rows(ctx):
    b = (ctx.get("v15_result") or {}).get("board") or {}
    return {r.get("id"): r for r in b.get("rows") or [] if isinstance(r, dict) and r.get("id")}


def _items(row, kind):
    row = row or {}
    if row.get("list") is None and kind == "skills" and isinstance(row.get("skills"), list):
        # a run made before the board's `list`: the per-skill depth it already had, with no count of uses
        return [{"name": x["name"], "kind": "skill", "step": x.get("depth"), "used": x.get("used"), "why": x.get("why", "")}
                for x in row["skills"] if isinstance(x, dict) and x.get("name")]
    items = [x for x in row.get("list") or [] if isinstance(x, dict) and x.get("name")]
    if kind in ("skills", "commands"):
        items = [x for x in items if (x.get("kind") or "skill") == kind[:-1]]
    return items


def _in_use(x):
    """In use in the window: the board's own flag where it gave one (a skill: a name an installed skill also has was loaded,
    whoever's it was, and counts as in use, as on the Review's card), else a use counted."""
    return x.get("used") is not False if "used" in x else bool(x.get("uses"))


def _short(path):
    home = os.path.expanduser("~")
    path = str(path or "")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def _masked_names(kind, items):
    stem = {"skills": "skill", "commands": "command", "agents": "sub-agent", "hooks": "hook", "plugins": "plugin",
            "tools": "tool", "programs": "project", "evals": "evals", "memory": "memory", "workflows": "workflow"}[kind]
    return {id(x): f"own-{stem}-{i + 1}" for i, x in enumerate(items)}


def _rule_files(ctx):
    """Rule files where the count holds: the projects the setup read mapped (setup-map.json), each with how many load in
    every session, how many in their folder, and how many docs it opens when needed. None when the map isn't there."""
    m = ctx.get("setup_map") if isinstance(ctx.get("setup_map"), dict) else None
    if not m:
        return None
    projects = []
    for p in m.get("projects") or []:
        t = p.get("totals") if isinstance(p, dict) and isinstance(p.get("totals"), dict) else {}
        always, scoped = int(t.get("always_files") or 0), int(t.get("scoped_files") or 0)
        if always + scoped:
            projects.append({"name": os.path.basename(str(p.get("root") or "").rstrip("/")) or "project", "always": always,
                             "scoped": scoped, "docs": int(t.get("on_demand_files") or 0), "sessions": p.get("typed_sessions")})
    u = (m.get("user") or {}).get("totals") or {}
    return {"projects": projects, "user": int(u.get("always_files") or 0) + int(u.get("scoped_files") or 0)}


def _plural(n, word):
    return f"{_n(n)} {word}{'' if n == 1 else 's'}"


# ------------------------------------------------------------------ at a glance

def _kinds(ctx):
    """The kinds they have: (kind, row, items, number, used count, tile's used line). A kind with nothing of theirs has none."""
    rows, out = _rows(ctx), []
    for kind, rid, label, title, what, unit in KINDS:
        row = rows.get(rid) or {}
        if kind == "rules":
            rf = _rule_files(ctx)
            if not rf or not rf["projects"]:
                continue
            n = sum(p["always"] + p["scoped"] for p in rf["projects"])
            sessions = sum(int(p["sessions"] or 0) for p in rf["projects"])
            out.append((kind, row, rf, n, None, f"<b>{_n(sessions)}</b> sessions ran with them" if sessions else "",
                        label.format(n=len(rf["projects"])) if len(rf["projects"]) > 1 else "rule files in your busiest project"))
            continue
        if row.get("state") not in ("built and used", "built, not used lately"):
            continue
        items = _items(row, kind)
        if kind == "commands" and not items:
            continue
        if kind == "programs":
            n = len(items) or row.get("projects") or 0
        else:
            n = len(items) if row.get("list") is not None or items else (row.get("items") or 0)
        if not n:
            continue
        used = sum(1 for x in items if _in_use(x))
        if kind == "workflows" or not items:
            line = f"used <b>{_n(row.get('calls') or 0)}</b> times this month" if row.get("calls") else "not used this month"
            used = None
        elif kind == "programs":
            line = f"<b>{_n(row.get('calls') or 0)}</b> runs this month" if used else "none ran it this month"
        else:
            line = f"<b>{used}</b> {USED_WORD[kind]} this month"
        out.append((kind, row, items, n, used, line, label))
    return out


def _tiles(ctx, kinds):
    tiles = []
    for kind, row, items, n, used, line, label in kinds:
        meter = (f'<span class="meter" role="img" aria-label="{used} of {n} {USED_WORD[kind]} this month">'
                 f'<i style="width:{max(2, round(100 * used / n)) if used else 0}%"></i></span>') if used is not None and n else '<span class="gap"></span>'
        tiles.append(f'<a class="tile" href="#built-{kind}"><span class="bt-num">{_n(n)}</span><span class="lab">{label}</span>'
                     f'{meter}<span class="used">{line}</span></a>')
    return f'<div class="tiles">{"".join(tiles)}</div>'


# ------------------------------------------------------------------ per kind

def _step(esc, x, masked):
    s = x.get("step")
    if s not in (1, 2, 3, 4):
        return '<span class="step none"><span class="seg">' + "<i></i>" * 4 + "</span>—</span>"
    why = _mask_why(x.get("why")) if masked else str(x.get("why") or "")
    tip = STEP_TIP[s - 1] + (f" Here: {why}." if why else "")
    return (f'<span class="step" title="{esc(tip)}"><span class="seg" aria-hidden="true">'
            + "".join(f'<i class="{"on" if k < s else ""}"></i>' for k in range(4)) + f"</span>{STEPS[s - 1]}</span>")


def _item(esc, kind, x, name, unit, masked, with_step):
    copies = x.get("copies") or 1
    about = "" if masked else x.get("about") or ""
    if kind == "programs":
        k = x.get("scripts") or 0
        about = f'{_plural(k, "script")} of yours {"starts" if k == 1 else "start"} the agent here'
    if kind == "plugins" and x.get("on") is False and not about:
        about = "Switched off"
    if kind == "agents" and x.get("model") and not masked:
        about = (about + " · " if about else "") + f"runs on {x['model']}"
    if kind == "memory" and not masked:
        # a store reads as the project it sits in, then where in it: ~/shop/.agents/memory -> "shop", ".agents/memory"
        bits = _short(name).lstrip("~/").split("/")
        name, about = (bits[0], "/".join(bits[1:])) if len(bits) > 1 else (bits[0], about)
    uses = x.get("uses")
    if kind == "workflows" or "uses" not in x:
        u = '<span class="u"></span>'
    elif uses is None:
        u = '<span class="u zero" title="An installed skill has the same name, so its uses can\'t be told apart">can\'t tell</span>'
    else:
        u = f'<span class="u{" zero" if not uses else ""}"><b>{_n(uses)}</b> {unit}</span>'
    return (f'<li class="it"><span class="nm">{esc(name)}'
            + (f'<small title="{copies} copies on this laptop, counted once">×{copies}</small>' if copies > 1 else "") + "</span>"
            f'<span class="ab" title="{esc(about)}">{esc(about)}</span>'
            + (_step(esc, x, masked) if with_step else "") + u + "</li>")


def _list(ctx, kind, items, unit):
    esc, masked = ctx["esc"], bool(ctx.get("masked"))
    names = _masked_names(kind, items) if masked else {id(x): str(x["name"]) for x in items}
    with_step = kind == "skills"
    cls = "lst" + ("" if with_step else " nostep")

    def ul(xs):
        return f'<ul class="{cls}">' + "".join(_item(esc, kind, x, names[id(x)], unit, masked, with_step) for x in xs) + "</ul>"
    if kind == "workflows":
        return ul(items)
    used = sorted([x for x in items if _in_use(x)],
                  key=lambda x: (x.get("uses") is None, -(x.get("uses") or 0), str(x["name"])))
    idle = sorted([x for x in items if x not in used], key=lambda x: str(x["name"]))
    out = ul(used[:TOP]) if used else ""
    if len(used) > TOP:
        out += f'<details class="bt-more"><summary>{CHEV}Show all {len(used)}</summary>{ul(used[TOP:])}</details>'
    if idle:
        word = {"hooks": "Set up, didn't run in 30 days", "plugins": "Not used in 30 days"}.get(kind, "Not used in 30 days")
        out += (f'<details class="bt-fold"><summary>{CHEV}{word} ({len(idle)})</summary>'
                f'<div class="names">{"".join(f"<span>{esc(names[id(x)])}</span>" for x in idle)}</div></details>')
    return out


def _hooks(ctx, items):
    """A session's life as a line, each hook pinned where it fires, with how often it did there."""
    esc, masked = ctx["esc"], bool(ctx.get("masked"))
    names = _masked_names("hooks", items) if masked else {id(x): str(x["name"]) for x in items}
    # Claude Code's records count a hook's runs at some moments only; elsewhere a hook is drawn where it is set up, with
    # no number, and the moment says its runs aren't counted (a "—" there would read as "never ran")
    counted = {e for x in items for e in (x.get("runs_at") or {})}
    cols = []
    for label, events, always in MOMENTS:
        here = []
        for x in items:
            hit = [e for e in x.get("events") or [] if e in events]
            if hit:
                runs = sum(int((x.get("runs_at") or {}).get(e) or 0) for e in hit)
                here.append((runs, x))
        if not here and not always:
            continue
        here.sort(key=lambda t: (-t[0], names[id(t[1])]))
        known = any(e in counted for e in events)

        def chip(runs, x):
            whose = f" (plugin {esc(x['plugin'])})" if x.get("plugin") and not masked else ""
            if not known:
                return (f'<span class="hk-chip" title="{esc(names[id(x)])}{whose}: set up here; runs at this moment aren\'t counted">'
                        f'<span>{esc(names[id(x)])}</span></span>')
            return (f'<span class="hk-chip{"" if runs else " idle"}" title="{esc(names[id(x)])}{whose}: '
                    f'{_n(runs) + " runs here" if runs else "set up here, no run in 30 days"}">'
                    f'<span>{esc(names[id(x)])}</span><b>{_n(runs) if runs else "0"}</b></span>')
        cols.append(f'<li class="{"has" if here else ""}"><span class="dot" aria-hidden="true"></span><span class="mo">{label}'
                    + ("" if known or not here else '<small>runs not counted</small>') + "</span>"
                    f'<span class="hk-chips">{"".join(chip(r, x) for r, x in here)}</span></li>')
    return (f'<div class="hk" role="group" aria-label="When your hooks run in a session"><ol>{"".join(cols)}</ol></div>'
            '<p class="note">Each hook sits at the moment it fires, with how often it did there in the last 30 days. '
            'A dashed one is set up there and didn\'t run.</p>')


def _rules(ctx, rf):
    esc, masked = ctx["esc"], bool(ctx.get("masked"))
    rows = []
    for i, p in enumerate(rf["projects"]):
        name = f"project-{i + 1}" if masked else p["name"]
        where = [f'{p["always"]} load in every session' if p["always"] else "", f'{p["scoped"]} load in their folder' if p["scoped"] else ""]
        about = " · ".join(x for x in where if x) + (f' · {_plural(p["docs"], "doc")} opened when needed' if p["docs"] else "")
        rows.append(f'<li class="it"><span class="nm">{esc(name)}</span><span class="ab">{esc(about)}</span>'
                    f'<span class="u"><b>{_n(p["sessions"] or 0)}</b> sessions</span></li>')
    user = f' Plus {_plural(rf["user"], "file")} in your user folder, read in every project.' if rf["user"] else ""
    return (f'<div class="rf"><ul class="lst">{"".join(rows)}</ul></div>'
            f'<p class="note">Counted in your busiest projects only.{user} Elsewhere the scan can\'t yet tell rule files you '
            'wrote from copies and files your tools generate, so it doesn\'t guess.</p>')


def _section(ctx, kind, row, items, n, unit, title, what, member):
    esc = ctx["esc"]
    if not items and kind != "rules":
        ex = [] if ctx.get("masked") else row.get("examples") or []
        body = f'<div class="names">{"".join(f"<span>{esc(x)}</span>" for x in ex)}</div>' if ex else ""
    elif kind == "hooks":
        placed = [x for x in items if x.get("events")]
        body = _hooks(ctx, placed) if placed else ""
        loose = [x for x in items if not x.get("events")]
        if loose:   # a hook whose moment no record names: listed, not pinned
            names = _masked_names(kind, items) if ctx.get("masked") else {id(x): str(x["name"]) for x in items}
            body += (f'<details class="bt-fold"><summary>{CHEV}Moment not recorded ({len(loose)})</summary><div class="names">'
                     + "".join(f'<span>{ctx["esc"](names[id(x)])}</span>' for x in loose) + "</div></details>")
    elif kind == "rules":
        body = _rules(ctx, items)
    else:
        body = _list(ctx, kind, items, unit)
    src = "" if member or not row.get("source") else (
        f'<details class="bt-fold"><summary>{CHEV}How we know</summary><p class="src">{ctx["esc"](row["source"])}</p></details>')
    count = "" if kind == "rules" else f'<span class="ct">{_n(n)}</span>'
    return (f'<section class="kd" id="built-{kind}"><div class="kd-h"><h3>{title}</h3>{count}</div>'
            f'<p class="kd-what">{what}</p>{body}{src}</section>')


# ------------------------------------------------------------------ the rest

def _also(ctx, rows, member):
    esc, out = ctx["esc"], []
    mine = []
    for rid, say in (("cap-output-styles", lambda r: f'{_plural(r["items"], "output style")}'
                      + (", one switched on" if r.get("state") == "built and used" else "")),
                     ("cap-permissions", lambda r: _plural(r["items"], "permission rule")),
                     ("cap-model-routing", lambda r: f'{_n(r["items"])} sub-agents with their own model')):
        r = rows.get(rid)
        if r and r.get("state") in ("built and used", "built, not used lately") and r.get("items"):
            mine.append(say(r))
    if mine:
        out.append('<div><b class="k">Also yours</b>' + "".join(f'<span class="c">{esc(x)}</span>' for x in mine) + "</div>")
    others = []
    for r in rows.values():
        if r.get("state") == "uses others'":
            others.append(f'{r.get("name")}' + (f' · {_n(r["calls"])} {r.get("unit") or "uses"} this month' if r.get("calls") else ""))
    if others:
        out.append('<div><b class="k">In use, made by others</b>' + "".join(f'<span class="c">{esc(x)}</span>' for x in others) + "</div>")
    folds = []
    for state, word in (("not seen", "Not seen on this laptop"), ("can't tell", "Couldn't tell from the logs")):
        rs = [r for r in rows.values() if r.get("state") == state]
        if rs:
            lis = "".join(f'<li><b>{esc(r.get("name"))}</b>: '
                          + esc(r.get("why") if state == "can't tell" and not ctx.get("masked") else GIVES.get(r.get("id"), "")) + "</li>"
                          for r in rs)
            folds.append(f'<details class="bt-fold none"><summary>{CHEV}{word} ({len(rs)})</summary><ul class="gv">{lis}</ul></details>')
    if not out and not folds:
        return ""
    return '<section class="also">' + "".join(out) + "".join(folds) + "</section>"


def render(ctx):
    ctx = {**ctx, "esc": ctx.get("esc") or html.escape}
    rows = _rows(ctx)
    if not rows:
        return '<p class="empty">not in this run</p>'
    member = ctx.get("member")
    kinds = _kinds(ctx)
    out = []
    if kinds:
        out.append(_tiles(ctx, kinds))
    else:
        out.append('<p class="note">Nothing on this laptop shows a part of the setup that you built yourself yet.</p>')
    meta = {k: (t, w, u) for k, _, _, t, w, u in KINDS}
    for kind, row, items, n, used, line, label in kinds:
        title, what, unit = meta[kind]
        out.append(_section(ctx, kind, row, items, n, unit, title, what, member))
    out.append(_also(ctx, rows, member))
    return "".join(out)


def _mask_why(why):
    """A skill's `why` on the masked copy: the files it names are dropped (board.py puts a file only inside parentheses or after
    "its script"), so no skill or file name of theirs leaves the laptop. Words of the sentence are never matched against skill names:
    a skill called `file` or `run` would damage the sentence."""
    return re.sub(r"\bits script .+? (?=was run after it loaded)", "its script ",
                  re.sub(r"\s*\((?!\d+x\)|!`)[^)]*\)", "", str(why or ""))).strip()
