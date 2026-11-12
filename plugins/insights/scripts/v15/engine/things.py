#!/usr/bin/env python3
"""The level read from plain things (a trial, 11-11), beside the read from practices (engine.level_pointer).

Why: the practice read gave each of 33 practices four rungs, and the rungs that should not exist got filled. On 11-11 a
one page showed "tools made for the agent" at L3 (the ruling: building tools is L4), plan mode as "agree the
approach first" at L3 (it is a built-in), and the model's own sub-agent calls as the person's habit. The rulings place THINGS at
levels, so this read does too: each thing has one home level and a name he would say out loud.

    L1  uses Claude Code and what ships with it or what others made
    L2  habits that are the person's own act: only a person presses Esc or types a command
    L3  a setup of theirs carries the context. The things show it is there; whether it is deep is the setup read's
        call (engine.setup_read: the code's map of their files and an agent's quoted reading must agree)
    L4  they built and use what changes how the agent runs (several kinds of it), with evals, and keep refining it

A thing's state comes from the capability board (board.py: theirs, by ownership.py, and whether it ran) or from the
scan's who-did-it counts (inventory.json: what Claude Code records only when the person acts). What the model or Claude
Code did unasked (its own sub-agents, automatic compactions, built-in memory) shows as "Claude did this" and credits
nothing. Standard library only; engine.py passes in what it already decided (builds, evals, the setup read)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wording  # noqa: E402  the neutral words: plurals and counts, imported by the board, this file, the readers and the views
from wording import noun, plural, say, unit_noun  # noqa: E402,F401  re-exported: the views reach them through here

MODEL_ENV = "AGENTIC_SETUP_LEVEL_MODEL"  # "practices" = the old read on the page and in the share summary

YOURS_USED, YOURS_IDLE, USES_OTHERS, CLAUDE_DID, NOT_SEEN, CANT_CHECK = (
    "yours and used", "yours, not used in 30 days", "uses others'", "Claude did this", "not seen", "couldn't check")
ICON = {YOURS_USED: "✓", YOURS_IDLE: "◐", USES_OTHERS: "⇄", CLAUDE_DID: "↻", NOT_SEEN: "○", CANT_CHECK: "?"}
PRESENT = (YOURS_USED, YOURS_IDLE)
BOARD_STATE = {"built and used": YOURS_USED, "built, not used lately": YOURS_IDLE, "uses others'": USES_OTHERS,
               "not seen": NOT_SEEN, "can't tell": CANT_CHECK}

# Every number below is a guess until hand-checked on member laptops (setup-snapshot CLAUDE.md, "Detectors").
L2_HABITS_FOR_L2 = 2             # of the four L2 things, counted as the person's own act
DEFAULT_BAR = {"n": 3, "chats": 3}  # a habit, for a count the signal map (substrate signals.yaml) gives no bar
MIN_DAYS_FOR_L1 = 7              # days with sessions before "uses Claude Code on real work" is said (certainty's floor for a fair read)
# L4, in this read's own terms: evals on what they built, plus this many of the other four kinds of built thing, each
# built AND used in the window. It stays strict on purpose: one built thing plus an eval folder once read L4,
# and the answer was "we can't give L4 away like that" (11-05). It does not borrow the practice read's "builds in 3
# areas": the board can only ever name two areas, so a laptop with all five L4 things in use read L3 (11-11).
L4_BUILT_KINDS = 3
L4_KINDS = ("hooks", "mcp-built", "plugins", "agent-programs")   # the four kinds beside evals

MEANING = {"L1": "Uses agents on real work", "L2": "Works with discipline",
           "L3": "Sets agents up to know, learn and remember", "L4": "Builds the harness around the agent"}

# id, name, home level, source. A source is ("board", [row ids]) = the best state among those rows, or a function below.
# "own" lists the who-did-it counts that are the person's act; "claude" the ones that are not, shown when no own count
# makes a habit.
THINGS = [
    {"id": "use-claude-code", "name": "Use Claude Code on real work", "level": "L1", "from": "days"},
    {"id": "use-plan-mode", "name": "Use plan mode", "level": "L1", "from": "counts", "bar": {"n": 1},
     "own": ["you.plan_mode_switched_on", "you.plans_asked", "setup.chats_opened_in_plan_by_settings"],
     "claude": ["cant.plan_mode_entered_by_claude"],
     "say": "You switched plan mode on {n:time} in the last 30 days.",
     "say_claude": ("time Claude asked to switch plan mode on", "times Claude asked to switch plan mode on")},
    {"id": "use-others-tools", "name": "Use MCP servers and skills others made", "level": "L1",
     # two counts, never summed: skills from others and MCP servers are different rows with different calls (others_said)
     "from": "board", "rows": ["cap-mcp", "cap-skills-installed"]},

    {"id": "steer-mid-run", "name": "Steer the agent mid-run", "level": "L2", "from": "counts",
     "own": ["you.esc_interrupts", "you.steps_refused", "you.helpers_stopped_by_hand"],
     "say": "You interrupted the agent, rejected a tool call or stopped a sub-agent {n:time} in the last 30 days."},
    {"id": "reset-sessions", "name": "Reset sessions on purpose", "level": "L2", "from": "counts",
     "own": ["you.clears_typed", "you.manual_compactions", "you.chats_split_off"], "claude": ["claude.auto_compactions"],
     "say": "You typed /clear, /compact, /fork or /branch {n:time} in the last 30 days.",
     "say_claude": ("automatic compaction", "automatic compactions")},
    {"id": "start-side-work", "name": "Start sub-agents or parallel sessions yourself", "level": "L2", "from": "counts",
     "own": ["you.btw_side_questions", "you.subtask_forks", "you.helper_mentions", "you.ultracode_requests",
             "you.chats_at_once"],
     "claude": ["cant.helper_calls_unattributed", "claude.background_helpers"],
     "say": "You started side questions, forks, workflows or parallel sessions {n:time} in the last 30 days.",
     "say_claude": ("sub-agent call the model made with nothing of yours behind it",
                    "sub-agent calls the model made with nothing of yours behind them")},
    {"id": "pick-the-model", "name": "Pick the model for the task", "level": "L2", "from": "counts",
     "own": ["/model"], "claude": ["cant.helper_calls_with_model"],
     "say": "You typed /model {n:time} in the last 30 days.",
     "say_claude": ("sub-agent call where the model was named", "sub-agent calls where the model was named")},

    {"id": "rule-files", "name": "AGENTS.md files", "level": "L3", "from": "board", "rows": ["cap-instructions"],
     "used_p": "You wrote AGENTS.md files in {p:project}; {calls:session} ran where they load.",
     "used": "You wrote {n:AGENTS.md file}; {calls:session} ran where they load.",
     "idle": "You wrote {n:AGENTS.md file}; no session in the last 30 days ran where they load."},
    {"id": "scoped-rules", "name": "Instructions for part of the project", "level": "L3", "from": "scoped"},
    {"id": "skills-commands", "name": "Skills and commands you wrote", "level": "L3", "from": "board", "rows": ["cap-skills-own"],
     "used": "You wrote {n:skill or command|skills and commands}; they were called {calls:time} in the last 30 days.",
     "idle": "You wrote {n:skill or command|skills and commands}; none was called in the last 30 days."},
    # built-in memory is Claude Code's own (board.AUTO_MEMORY): the board reads it as "uses others'", which here is Claude's doing
    {"id": "memory", "name": "Memory across sessions", "level": "L3", "from": "board", "rows": ["cap-memory"],
     "others_is": CLAUDE_DID,
     "used": "You keep {n:memory store} of your own; sessions read them {calls:time} in the last 30 days.",
     "idle": "You keep {n:memory store} of your own; no session read them in the last 30 days."},
    {"id": "own-subagents", "name": "Sub-agents you wrote", "level": "L3", "from": "board", "rows": ["cap-subagents"],
     "used": "You wrote {n:sub-agent}; they were called {calls:time} in the last 30 days.",
     "idle": "You wrote {n:sub-agent}; none was called in the last 30 days."},
    {"id": "permission-rules", "name": "Permission rules", "level": "L3", "from": "board", "rows": ["cap-permissions"],
     "used": "You wrote {n:deny or ask rule}; they applied in {calls:session}.",
     "idle": "You wrote {n:deny or ask rule}; no session in the last 30 days ran under them."},

    {"id": "hooks", "name": "Hooks you wrote", "level": "L4", "from": "board", "rows": ["cap-hooks"],
     "used": "You wrote {n:hook}; they ran {calls:time} in the last 30 days.",
     "idle": "You wrote {n:hook}; none ran in the last 30 days."},
    {"id": "mcp-built", "name": "MCP servers and tools you built", "level": "L4", "from": "board", "rows": ["cap-mcp-write"],
     "used": "You built {n:MCP server}; their tools were called {calls:time} in the last 30 days.",
     "idle": "You built {n:MCP server}; no tool of theirs was called in the last 30 days."},
    {"id": "plugins", "name": "Plugins you wrote", "level": "L4", "from": "board", "rows": ["cap-plugins-own"],
     "used": "You wrote {n:plugin}; their hooks and skills ran {calls:time} in the last 30 days.",
     "idle": "You wrote {n:plugin}; nothing of theirs ran in the last 30 days."},
    # three rows, worded as what each is (programs_rows); a run through the SDK is not summed with a headless run
    {"id": "agent-programs", "name": "Programs that run the agent", "level": "L4", "from": "board",
     "rows": ["cap-own-rig", "cap-sdk", "cap-event-runs"]},
    # what ran and what only changed are two facts (evals_said); the level asks for either (engine: `ran or changed`)
    {"id": "evals", "name": "Evals on what you built", "level": "L4", "from": "board", "rows": ["cap-evals"]},
]
STATE_RANK = [YOURS_USED, YOURS_IDLE, USES_OTHERS, CLAUDE_DID, NOT_SEEN, CANT_CHECK]


_n = wording.count   # a count with its thousands, as every sentence here prints it
COUNT_KEYS = ("items", "calls", "projects", "in_read", "read_projects", "ran", "changed")   # what a board row counts


def checked_rows(rows):
    """{id: board row} where every count is a number. A row whose count is there but is not one (a list, "12k", "n/a") becomes
    a row that reads "couldn't check", with nothing counted, so the one thing says so and the rest of the tab shows (11-12: such a
    value made the whole "What you've built" tab read "This view failed to build"). A readable count in a text ("1,204") is read.
    The one entry the Built tab and the level read both take board rows through."""
    out = {}
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        bad = [k for k in COUNT_KEYS if wording.is_not_a_number(r.get(k))]
        if bad:
            r = {**r, **{k: 0 for k in COUNT_KEYS if k not in bad and k in r}, **{k: 0 for k in bad}, "state": "can't tell",
                 "unreadable": bad, "why": f"Couldn't check: the run's file holds no number for its {' or '.join(bad)}."}
        else:
            r = {**r, **{k: wording.number(r[k]) for k in COUNT_KEYS if r.get(k) is not None}}
        out[r["id"]] = r
    return out


def things_met(pt):
    """Per level: (things in place, things). The one rule for "n of m in place", used by the Review's ladder and the Your
    level tab's groups. In place = theirs (used or idle); at L1 what others made counts too, since using it is that
    level's point. (11-12: the two tabs counted this under two rules and printed "3 of 3" beside "2 of 3".)"""
    out = {}
    for t in (pt or {}).get("things") or []:
        if not isinstance(t, dict) or t.get("level") not in MEANING:
            continue
        met = t.get("state") in PRESENT or (t.get("level") == "L1" and t.get("state") == USES_OTHERS)
        have, of = out.get(t["level"], (0, 0))
        out[t["level"]] = (have + met, of + 1)
    return out


def part_state(g):
    """One part of "their setup carries the context" as the page says it: "shown" needs both readers to agree, as the
    engine does: the code's map shows the structure AND the agent read it as shown with a quote the code found in the
    file. One without the other is "partly", with the reason said. (state, note)."""
    judged = g.get("judged")
    if g.get("structure") and judged == "shown" and g.get("quote_ok"):
        return "shown", ""
    if judged == "shown":
        return "partly", ("The reading says yes, but the map of your files doesn't show the structure behind it."
                          if not g.get("structure") else
                          # setup_read.py drops a quote whose line holds a secret, so no page ever prints one
                          "The reading says yes, but the line it rests on holds a key or a token, so we don't keep or "
                          "show it. Move the secret out of that file and run the tool again."
                          if g.get("quote_dropped") else "The reading says yes, but its quote wasn't found in your file.")
    if judged == "partly":
        return "partly", ""
    if judged == "not shown" or (g.get("structure") is False and judged in (None, "", "can't tell")):
        return "notshown", ""
    return "unread", ""


def parts_count(parts):
    """(fully shown, partly shown, parts): the numbers behind every "n of m parts" the page prints."""
    states = [part_state(g)[0] for g in parts or [] if isinstance(g, dict)]
    return states.count("shown"), states.count("partly"), len(states)


def parts_said(parts):
    """"0 fully shown, 3 partly": both counts, so a reader who sees three "Partly" rows meets no "0 of 3"."""
    full, partly, _ = parts_count(parts)
    return f"{full} fully shown, {partly} partly"


def _lc(name):
    """A thing's name inside a sentence: "Rule files (CLAUDE.md…)" -> "rule files (CLAUDE.md…)", "MCP servers" as it is."""
    return name[:1].lower() + name[1:] if name[1:2].islower() else name


# ---- what others made: skills and MCP servers are two counts, each with its own calls -----------------------------

def others_tools(rows):
    """Skills, commands and plugins from others (cap-skills-installed) and MCP servers (cap-mcp), each with its calls.
    Never summed: a person with 2,094 MCP calls and no skill calls has "0 times" for the one and "2,094 times" for the
    other, and the Review's card and the level's sentence both say exactly that (11-12: one read the first row, the other the sum)."""
    s, m = (rows or {}).get("cap-skills-installed") or {}, (rows or {}).get("cap-mcp") or {}
    n = lambda r, k: wording.count_or_zero(r.get(k))  # noqa: E731
    return {"skills": n(s, "items"), "skill_calls": n(s, "calls"), "mcp": n(m, "items"), "mcp_calls": n(m, "calls")}


def others_said(c, days=30):
    """The sentence for `others_tools`: "You have 41 skills, commands and plugins from others, called 0 times, and 3 MCP servers
    set up, called 2,094 times, in the last 30 days." A side with nothing is left out."""
    bits = []
    if c["skills"]:
        bits.append(say("{skills:skill, command or plugin|skills, commands and plugins} from others, called {skill_calls:time}", **c))
    if c["mcp"]:
        bits.append(say("{mcp:MCP server} set up, called {mcp_calls:time}", **c))
    elif c["mcp_calls"]:
        bits.append(say("MCP tools called {mcp_calls:time}", **c))
    return f"You have {' and '.join(bits)} in the last {days} days." if bits else ""


# ---- programs that start the agent: three rows, worded as what each is -------------------------------------------

PROGRAM_ROWS = (
    ("cap-own-rig", "Scripts", "script that starts the agent", "scripts that start the agent", "headless run", "headless runs",
     "in those projects"),
    ("cap-sdk", "SDK programs", "SDK program that starts the agent", "SDK programs that start the agent", "SDK run", "SDK runs", ""),
    ("cap-event-runs", "Schedules", "schedule that starts the agent", "schedules that start the agent", "run", "runs",
     "began while you were away"))


def programs_rows(rows):
    """The three kinds of program that start the agent, each as what it is: [{id, title, n, calls, phrase}]. `phrase` is
    the sentence for the row ("2 scripts that start the agent (7 headless runs in those projects)"). A row not read
    (no board row) is left out; one with none of theirs has n 0."""
    out = []
    for rid, title, one, many, run1, runs, tail in PROGRAM_ROWS:
        r = (rows or {}).get(rid)
        if not r:
            continue
        n, calls = wording.count_or_zero(r.get("items")), wording.count_or_zero(r.get("calls"))
        run = plural(calls, run1, runs) + (f" {tail}" if tail else "")
        out.append({"id": rid, "title": title, "n": n, "calls": calls, "state": r.get("state"),
                    "phrase": f"{plural(n, one, many)} ({run})" if n else f"no {many}"})
    return out


# ---- evals: what ran and what only changed are two facts -----------------------------------------------------------

def evals_counts(row):
    """(runs recorded, folders changed) in the window, from the board's cap-evals row. A run folder from before the row
    kept them apart has one `calls` and a `unit` that says which it was."""
    row = row or {}
    unit = row.get("unit")
    ran = row["ran"] if "ran" in row else (row.get("calls") or 0) if unit == "recorded runs" else 0
    changed = row["changed"] if "changed" in row else (row.get("calls") or 0) if unit == "folders changed" else 0
    return wording.count_or_zero(ran), wording.count_or_zero(changed)


def evals_said(row, days=30):
    """The one sentence for what evals did, on every tab: "ran 4 times", "changed in the last 30 days, no run recorded",
    or "none ran or changed in the last 30 days". Never "used" when nothing ran."""
    ran, changed = evals_counts(row)
    if ran:
        return f"ran {plural(ran, 'time')} in the last {days} days"
    if changed:
        return f"changed in the last {days} days, no run recorded"
    return f"none ran or changed in the last {days} days"


def in_use(x):
    """Whether one listed thing (an entry of a board row's `list`) was in use in the window: the board's own flag where it
    gave one (a skill: a name an installed skill also has was loaded, whoever's it was, and counts as in use, as on the
    Review's card), else a use counted. An eval folder that changed counts as in use too: it is listed with "changed, no run
    recorded", never under "not used". The one rule the Built tab's meters and its used/idle lists both read."""
    if "changed" in x:
        return bool(x.get("uses")) or bool(x.get("changed"))
    return x.get("used") is not False if "used" in x else bool(x.get("uses"))


# ---- rule files: one count, one scope, one session figure ---------------------------------------------------------

def rule_files(rows):
    """The person's rule files (CLAUDE.md, AGENTS.md and the files beside them), as every tab prints them, from the board's
    cap-instructions row: {total, projects, in_read, read_projects, sessions, by_project}. `total` is across every project, one
    per file whatever the worktrees and checkouts (board.one_per_repo_path), without the copies Claude Code keeps in its plugin
    folders (ownership.claude_code_copy); `projects` is how many projects hold them; `in_read` is how many of those sit in the
    `read_projects` projects the setup read looked at (None when the run has no map); `sessions` is how many sessions ran where
    they load (one figure, the row's); `by_project` is the table of the read projects: [{root, always, scoped}].
    (11-12: Built counted the 3 busiest projects, 32 files, the Your level tab every project, 1,632.)"""
    r = (rows or {}).get("cap-instructions") or {}
    return {"total": wording.count_or_zero(r.get("items")), "projects": wording.count_or_zero(r.get("projects")),
            "sessions": wording.count_or_zero(r.get("calls")),
            "in_read": wording.number(r.get("in_read")),
            "read_projects": wording.count_or_zero(r.get("read_projects")), "by_project": list(r.get("read") or [])}


def rule_files_said(c):
    """"4 rule files of yours in 3 projects; 4 of them in the 2 projects we read": one sentence on every tab. A side with
    nothing to say is left out (no project count on an older run, no read on a run without the map)."""
    out = plural(c["total"], "AGENTS.md file") + " of yours"
    if c["projects"]:
        out += f" in {plural(c['projects'], 'project')}"
    if c["in_read"] is not None and c["read_projects"]:
        out += f"; {_n(c['in_read'])} of them in the {plural(c['read_projects'], 'project')} we read"
    return out


# ---- projects: one list for every tab ----------------------------------------------------------------------------

def mapped_projects(setup_map, setup_read=None):
    """Every project the setup read mapped (setup-map.json), once, in the map's order: the one list the Review's grid and
    the Built tab's rule files both walk, so "3 busiest" is never "2 busiest" (11-12: Built dropped a project with no
    rule file of theirs, the Review kept it). Each: label, root, always, scoped, docs (counts of their rule files),
    sessions (None when the map holds none), ranked (picked by sessions or work), found_under, picked_by, and `read`
    (that project's entry in setup-read.json, or None). A project the read has and the map lacks is appended."""
    m = setup_map if isinstance(setup_map, dict) else {}
    reads = {}
    for p in (setup_read or {}).get("projects") or []:
        if isinstance(p, dict):
            reads.setdefault(p.get("label"), p)
    out = []
    for p in m.get("projects") or []:
        if not isinstance(p, dict) or p.get("error"):
            continue
        t = p.get("totals") if isinstance(p.get("totals"), dict) else {}
        label = os.path.basename(str(p.get("root") or "").rstrip("/")) or "project"
        out.append({"label": label, "root": p.get("root"), "always": wording.count_or_zero(t.get("always_files")),
                    "scoped": wording.count_or_zero(t.get("scoped_files")), "docs": wording.count_or_zero(t.get("on_demand_files")),
                    "sessions": p.get("typed_sessions"), "ranked": p.get("typed_sessions") is not None,
                    "found_under": p.get("found_under"), "picked_by": p.get("picked_by"), "read": reads.pop(label, None)})
    for label, r in reads.items():
        out.append({"label": label, "root": None, "always": 0, "scoped": 0, "docs": 0, "sessions": None,
                    "ranked": True, "found_under": None, "picked_by": None, "read": r})
    return out


def projects_noun(projects):
    """"3 busiest projects" / "busiest project" when the list was ranked by sessions or work; plain "3 projects" /
    "project" when a slot was filled another way (recently edited rule files): "busiest" is said only where it is true."""
    n = len(projects)
    word = "busiest project" if all(p["ranked"] for p in projects) else "project"
    return f"{n} {word}s" if n > 1 else word


def project_how(p):
    """Why a project is on the list, in words, where the sessions column would print a bare "0 sessions"."""
    if p["ranked"]:
        return plural(wording.count_or_zero(p["sessions"]), "session")
    if p.get("picked_by") == "rule-files":
        return "added for its recently edited AGENTS.md"
    return "found from your work" if not p.get("found_under") else f"found under {p['found_under']}"


def _lc(name):
    """A thing's name inside a sentence: "Rule files (CLAUDE.md…)" -> "rule files (CLAUDE.md…)", "MCP servers" as it is."""
    return name[:1].lower() + name[1:] if name[1:2].islower() else name


def model():
    """Which read the page and the share summary show: "things" (default) or "practices"."""
    return "practices" if (os.environ.get(MODEL_ENV) or "").strip().lower() == "practices" else "things"


def active(res):
    """(the pointer in use, its model). A result made before this read has no pointer_things: the practice read."""
    pt = (res or {}).get("pointer_things")
    if model() == "things" and isinstance(pt, dict) and pt.get("level"):
        return pt, "things"
    return (res or {}).get("pointer") or {}, "practices"


def _load(run_dir, name):
    try:
        return json.load(open(os.path.join(run_dir, name)))
    except (OSError, ValueError):
        return None


def _counts(inv):
    """The scan's who-did-it counts by id, plus typed slash commands as "/name": {n, chats, of}."""
    out = {}
    for g in ((inv or {}).get("who_did_it") or {}).get("groups") or []:
        for i in g.get("items") or []:
            if i.get("id"):
                out[i["id"]] = {"n": i.get("n") or 0, "chats": i.get("chats"), "of": i.get("of")}
    for c in ((inv or {}).get("slash_commands") or {}).get("by_command") or []:
        if c.get("command"):
            out[c["command"]] = {"n": c.get("times") or 0, "chats": c.get("chats"), "of": None}
    return out


def enough(item, bar):
    """Is this count a habit? The signal map's rule (setup-snapshot 2026-11-02-cheap/signals.py `enough`)."""
    n = item.get("n") or 0
    if n < bar.get("n", 1):
        return False
    if bar.get("chats") and item.get("chats") is not None and item["chats"] < bar["chats"]:
        return False
    if bar.get("share") and item.get("of"):
        return n / item["of"] >= bar["share"]
    return True


def _from_counts(t, counts, bars, have_inv):
    if not have_inv:
        return CANT_CHECK, "The session records were not read on this run."
    own = [(i, counts[i]) for i in t["own"] if i in counts]
    met = [(i, c) for i, c in own if enough(c, t.get("bar") or bars.get(i) or DEFAULT_BAR)]
    if met:
        return YOURS_USED, say(t["say"], n=sum(c["n"] for _, c in met))
    theirs = sum(counts[i]["n"] for i in t.get("claude") or [] if i in counts)
    few = sum(c["n"] for _, c in own)
    if theirs:
        mine = (f"You did it yourself {plural(few, 'time')}, too few to call a habit." if few else "There were none started by you.")
        return CLAUDE_DID, f"Claude Code did this by itself: {plural(theirs, *t['say_claude'])}. {mine}"
    if few:
        return NOT_SEEN, f"You did this {plural(few, 'time')} in the last 30 days, too few to call a habit."
    return NOT_SEEN, "Nothing in your session records shows it."


def best_row(rows):
    """The row whose state ranks first (STATE_RANK): the one a thing of several rows reads as. A tile that stands for such a thing
    carries its state, so a test can compare it with the level read's."""
    return min(rows, key=lambda r: STATE_RANK.index(BOARD_STATE.get(r.get("state"), CANT_CHECK)))


def _from_board(t, rows):
    got = [rows[r] for r in t["rows"] if r in rows]
    if not got:
        return CANT_CHECK, "This was not read on this run."
    best = best_row(got)
    state = BOARD_STATE.get(best.get("state"), CANT_CHECK)
    if state == USES_OTHERS and t.get("others_is"):
        state = t["others_is"]
    mine = [r for r in got if r.get("state") == best.get("state") or BOARD_STATE.get(r.get("state")) in PRESENT]
    items = sum(wording.count_or_zero(r.get("items")) for r in mine)
    calls = sum(wording.count_or_zero(r.get("calls")) for r in mine)
    # projects: the repos the items sit in, joined across rows by the board's opaque ids (a script that both starts
    # `claude -p` and imports the SDK sits in one project). An older run's rows carry none: the sentence counts items.
    ids = {i for r in mine for i in r.get("project_ids") or []}
    f = {"n": items, "calls": calls, "p": len(ids)}
    # a sentence to "you" with counts only: a name of the person's thing never goes into a level's proof (the copy that
    # leaves the laptop masks names)
    if t["id"] == "use-others-tools" and state == USES_OTHERS:
        proof = others_said(others_tools(rows)) or "Set up from others."
    elif t["id"] == "agent-programs" and state in PRESENT:
        proof = "You have " + "; ".join(r["phrase"] for r in programs_rows(rows) if r["n"]) + "."
    elif t["id"] == "evals" and state in PRESENT:
        proof = f"You keep {plural(items, 'set of evals', 'sets of evals')} on what you built; {evals_said(best)}."
    elif t["id"] == "rule-files" and state in PRESENT:
        c = rule_files(rows)
        proof = (f"{rule_files_said(c)}; {plural(c['sessions'], 'session')} ran where they load." if c["sessions"]
                 else f"{rule_files_said(c)}; no session in the last 30 days ran where they load.")
    elif state == CANT_CHECK and best.get("unreadable"):
        proof = best["why"]
    elif state == YOURS_USED:
        proof = say(t["used_p"] if ids and t.get("used_p") else t.get("used") or "You have {n} of your own; used {calls:time} in the last 30 days.", **f)
    elif state == YOURS_IDLE:
        proof = say(t.get("idle") or "You have {n} of your own; nothing in the last 30 days shows them in use.", **f)
    elif state == USES_OTHERS:
        proof = say("Set up from others; called {calls:time} in the last 30 days." if calls else "Set up from others.", **f)
    else:
        proof = {CLAUDE_DID: "Only what Claude Code writes by itself; nothing here is shaped by you.",
                 NOT_SEEN: "None of your own found on this laptop.", CANT_CHECK: "The records can't show it."}[state]
    return state, proof


def _scoped(run_dir, counts):
    """AGENTS.md files below a project's root, or a .claude/rules/ file with paths: (a separate extra mechanism): the code's map of their files (setup_map.py), and
    whether one reached the agent in a session."""
    m = _load(run_dir, "setup-map.json")
    if not m:
        return CANT_CHECK, "The map of your files was not drawn on this run."
    n = sum(((p.get("totals") or {}).get("scoped_files") or 0) for p in (m.get("projects") or []) + [m.get("user") or {}])
    if not n:
        return NOT_SEEN, "Every AGENTS.md of yours sits at a project's root."
    reached = (counts.get("setup.subfolder_rules_reached") or {}).get("n") or 0
    where = (f"You have {plural(n, 'file')} for part of a project (an AGENTS.md in a subfolder or, as a separate extra "
             "mechanism, a file in .claude/rules/ with a paths: line)")
    return (YOURS_USED, f"{where}; they loaded in {plural(reached, 'session')}.") if reached else (
        YOURS_IDLE, f"{where}; none loaded in the last 30 days.")


def _days(run_dir):
    logs = ((_load(run_dir, "portable-checks.json") or {}).get("meta") or {}).get("logs") or {}
    return logs.get("days_with_sessions")


def things(run_dir, board, bars):
    """Every thing with its state and a one-line proof (counts, never a name)."""
    inv = _load(run_dir, "inventory.json")
    counts = _counts(inv)
    rows = checked_rows((board or {}).get("rows"))
    days = _days(run_dir)
    out = []
    for t in THINGS:
        if t["from"] == "board":
            state, proof = _from_board(t, rows)
        elif t["from"] == "counts":
            state, proof = _from_counts(t, counts, bars, inv is not None)
        elif t["from"] == "scoped":
            state, proof = _scoped(run_dir, counts)
        else:  # days
            typed = (counts.get("you.typed_messages") or {}).get("n") or 0
            if days is None and not typed:
                state, proof = CANT_CHECK, "The session records were not read on this run."
            elif (days or 0) >= 1 or typed:
                state, proof = YOURS_USED, (f"You had sessions on {days} of the last 30 days." if days is not None
                                            else f"You typed {plural(typed, 'message')} in the last 30 days.")
            else:
                state, proof = NOT_SEEN, "No session in the last 30 days."
        thing = {"id": t["id"], "name": t["name"], "level": t["level"], "state": state, "proof": proof}
        if t["id"] == "rule-files" and state in PRESENT:
            thing["said"] = rule_files_said(rule_files(rows))   # the sentence the Built tab prints too; a test compares them
        if t["id"] == "evals" and state == YOURS_USED and not evals_counts(rows.get("cap-evals"))[0]:
            # the level asks for "ran or changed", but nothing ran: the page never says "used" for it
            thing["word"] = "Yours, changed in 30 days"
        out.append(thing)
    return out, days


def pointer(run_dir, board, setup, builds, bars=None):
    """The level from things. `setup` = engine.setup_read(run_dir): {ran, parts, holds, shown, held, ok, need}.
    `builds` = {areas, areas_ok, evals, weeks, weeks_ok} from the engine; this read takes only the weeks from it
    (engine.refined_over_time's rule: a check that did not run is never a minus). L4 here is this read's own: evals
    on what they built, in use, plus L4_BUILT_KINDS of the other four kinds, each built and used in the window."""
    ts, days = things(run_dir, board, bars or {})
    at = lambda lv: [t for t in ts if t["level"] == lv]  # noqa: E731
    habits = [t for t in at("L2") if t["state"] == YOURS_USED]
    l3_things = [t for t in at("L3") if t["state"] in PRESENT]
    l4_things = [t for t in at("L4") if t["state"] in PRESENT]
    held = setup.get("held") or []
    deep = bool(setup.get("ok")) and not held
    by_id = {t["id"]: t for t in ts}
    used = lambda tid: (by_id.get(tid) or {}).get("state") == YOURS_USED  # noqa: E731  built AND used: built alone doesn't count
    kinds = [k for k in L4_KINDS if used(k)]
    evals, refined = used("evals"), bool(builds.get("weeks_ok", True))
    l4_rest = evals and len(kinds) >= L4_BUILT_KINDS and refined
    names = lambda ids: ", ".join(_lc(by_id[i]["name"]) for i in ids if i in by_id)  # noqa: E731
    shown = ", ".join((g.get("name") or g.get("id") or "").lower() for g in setup.get("shown") or [])
    n_parts, need = len(setup.get("parts") or []) or 3, setup.get("need") or 2
    reasons, could = [], None

    def why_not_deep():
        if held:
            return f"Claude Code's records show a habit that works against it: {held[0].get('why') or held[0].get('id')}"
        if not setup.get("ran"):
            return "the read of your files didn't run this time, so your laptop can't say whether your setup carries the context"
        return (f"your files show the context carried in {n_parts} parts: {parts_said(setup.get('parts'))}, "
                f"and L3 asks for {need} fully shown")

    l4_asks = (f"evals on what you built, and at least {L4_BUILT_KINDS} of these four, each built and in use: "
               + names(L4_KINDS))

    def l4_missing():
        """What L4 still asks for, naming what was counted and what is short."""
        out = []
        if not evals:
            e = (by_id.get("evals") or {}).get("state")
            out.append("evals on what you built" + (" that ran or changed in the last 30 days" if e == YOURS_IDLE else ""))
        if len(kinds) < L4_BUILT_KINDS:
            idle = [k for k in L4_KINDS if (by_id.get(k) or {}).get("state") == YOURS_IDLE]
            out.append(f"{L4_BUILT_KINDS - len(kinds)} more of {names([k for k in L4_KINDS if k not in kinds])}, built and in use"
                       + (f" ({names(idle)}: yours, but not used in the last 30 days)" if idle else ""))
        if not refined:
            out.append("changes to what you built over 3 or more weeks")
        return out

    if deep and l4_rest:
        lvl, decided = "L4", ("the setup read, evals on what you built, and at least "
                              f"{L4_BUILT_KINDS} kinds of things you built and use, changed over weeks")
        reasons.append(f"Your files show a setup that carries the context ({shown}). You built and use {names(kinds)}, "
                       "and you re-test what you built with evals.")
    elif deep:
        lvl, decided = "L3", f"the setup read: at least {need} of its {n_parts} parts shown by your files"
        reasons.append(f"Your files show a setup that carries the context: {shown}.")
        # could be L4 only when something they built is there: what L4 lacks is then a question for the call
        could = "L4" if l4_things else None
        counted = f" Counted so far: {names(kinds + (['evals'] if evals else []))}." if kinds or evals else ""
        reasons.append(f"L4 asks for {l4_asks}.{counted} Still missing: " + "; ".join(l4_missing()) + ".")
    elif len(habits) >= L2_HABITS_FOR_L2 or l3_things or l4_things:
        lvl, decided = "L2", (f"at least {L2_HABITS_FOR_L2} habits that are your own act" if len(habits) >= L2_HABITS_FOR_L2
                              else "a setup of your own is there; the files don't yet show that it carries the context")
        if habits:
            reasons.append("Habits that are your own act: " + ", ".join(_lc(t["name"]) for t in habits) + ".")
        if l3_things or l4_things:
            # the unseen is never a minus: with a setup of theirs present, an unconfirmed read is "could be", not L1
            could = None if held else ("L4" if l4_rest else "L3")
            have = ", ".join(_lc(t["name"]) for t in (l3_things + l4_things)[:4])
            reasons.append(f"You have a setup of your own ({have}), but {why_not_deep()}.")
        else:
            reasons.append("L3 asks for a setup of your own that carries the context: an AGENTS.md, memory with a shape, "
                           "skills you wrote.")
    elif days is None or days >= MIN_DAYS_FOR_L1:
        lvl, decided = "L1", "Claude Code used on real work; fewer than two habits of your own seen"
        reasons.append(f"You use Claude Code on real work{f' ({days} days with sessions)' if days is not None else ''}. "
                       f"L2 asks for at least {L2_HABITS_FOR_L2} of these as your own habit: "
                       + ", ".join(_lc(t["name"]) for t in at("L2")) + ".")
    else:
        lvl, decided = "can't tell", f"sessions on fewer than {MIN_DAYS_FOR_L1} days: too little to place a level"
        reasons.append(f"This laptop holds sessions on {plural(days, 'day')}: too little to place a level.")
    return {"model": "things", "level": lvl, "could_be": could, "decided_by": decided,
            "reasons": [r for r in reasons if r],
            "things": ts, "habits": [t["id"] for t in habits],
            "setup_read": {"ran": bool(setup.get("ran")), "parts": setup.get("parts") or [],
                           "holds": setup.get("holds") or [], "shown": [g.get("id") for g in setup.get("shown") or []]},
            "builds": {**builds, "kinds": kinds, "kinds_need": L4_BUILT_KINDS, "evals_in_use": evals},
            "days_with_sessions": days}
