#!/usr/bin/env python3
"""The level read from plain things (a trial, 11-11), beside the read from practices (engine.level_pointer).

Why: the practice read gave each of 33 practices four rungs, and the rungs that should not exist got filled. On 11-11 a
tester's page showed "tools made for the agent" at L3 (his ruling: building tools is L4), plan mode as "agree the
approach first" at L3 (it is a built-in), and the model's own sub-agent calls as her habit. His rulings place THINGS at
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
# built AND used in the window. It stays strict on purpose: one built thing plus an eval folder once read a tester L4,
# and his answer was "we can't give L4 away like that" (11-05). It does not borrow the practice read's "builds in 3
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
     "say": "You switched plan mode on {n} times in the last 30 days.",
     "say_claude": "times Claude asked to switch plan mode on"},
    {"id": "use-others-tools", "name": "Use MCP servers and skills others made", "level": "L1",
     "from": "board", "rows": ["cap-mcp", "cap-skills-installed"],
     "others": "You have {n} MCP servers and skills from others set up; they were called {calls} times in the last 30 days.",
     "others_idle": "You have {n} MCP servers and skills from others set up; none was called in the last 30 days."},

    {"id": "steer-mid-run", "name": "Steer the agent mid-run", "level": "L2", "from": "counts",
     "own": ["you.esc_interrupts", "you.steps_refused", "you.helpers_stopped_by_hand"],
     "say": "You interrupted the agent, rejected a tool call or stopped a sub-agent {n} times in the last 30 days."},
    {"id": "reset-sessions", "name": "Reset sessions on purpose", "level": "L2", "from": "counts",
     "own": ["you.clears_typed", "you.manual_compactions", "you.chats_split_off"], "claude": ["claude.auto_compactions"],
     "say": "You typed /clear, /compact, /fork or /branch {n} times in the last 30 days.",
     "say_claude": "automatic compactions"},
    {"id": "start-side-work", "name": "Start sub-agents or parallel sessions yourself", "level": "L2", "from": "counts",
     "own": ["you.btw_side_questions", "you.subtask_forks", "you.helper_mentions", "you.ultracode_requests",
             "you.chats_at_once"],
     "claude": ["cant.helper_calls_unattributed", "claude.background_helpers"],
     "say": "You started side questions, forks, workflows or parallel sessions {n} times in the last 30 days.",
     "say_claude": "sub-agent calls the model made with nothing of yours behind them"},
    {"id": "pick-the-model", "name": "Pick the model for the task", "level": "L2", "from": "counts",
     "own": ["/model"], "claude": ["cant.helper_calls_with_model"],
     "say": "You typed /model {n} times in the last 30 days.",
     "say_claude": "sub-agent calls where the model was named"},

    {"id": "rule-files", "name": "Rule files (CLAUDE.md, AGENTS.md)", "level": "L3", "from": "board", "rows": ["cap-instructions"],
     "used_p": "You wrote rule files in {p} projects; {calls} sessions ran where they load.",
     "used": "You wrote {n} rule files; {calls} sessions ran where they load.",
     "idle": "You wrote {n} rule files; no session in the last 30 days ran where they load."},
    {"id": "scoped-rules", "name": "Rules scoped to a folder or a path", "level": "L3", "from": "scoped"},
    {"id": "skills-commands", "name": "Skills and commands you wrote", "level": "L3", "from": "board", "rows": ["cap-skills-own"],
     "used": "You wrote {n} skills and commands; they were called {calls} times in the last 30 days.",
     "idle": "You wrote {n} skills and commands; none was called in the last 30 days."},
    # built-in memory is Claude Code's own (board.AUTO_MEMORY): the board reads it as "uses others'", which here is Claude's doing
    {"id": "memory", "name": "Memory across sessions", "level": "L3", "from": "board", "rows": ["cap-memory"],
     "others_is": CLAUDE_DID,
     "used": "You keep {n} memory stores of your own; sessions read them {calls} times in the last 30 days.",
     "idle": "You keep {n} memory stores of your own; no session read them in the last 30 days."},
    {"id": "own-subagents", "name": "Sub-agents you wrote", "level": "L3", "from": "board", "rows": ["cap-subagents"],
     "used": "You wrote {n} sub-agents; they were called {calls} times in the last 30 days.",
     "idle": "You wrote {n} sub-agents; none was called in the last 30 days."},
    {"id": "permission-rules", "name": "Permission rules", "level": "L3", "from": "board", "rows": ["cap-permissions"],
     "used": "You wrote {n} deny or ask rules; they applied in {calls} sessions.",
     "idle": "You wrote {n} deny or ask rules; no session in the last 30 days ran under them."},

    {"id": "hooks", "name": "Hooks you wrote", "level": "L4", "from": "board", "rows": ["cap-hooks"],
     "used": "You wrote {n} hooks; they ran {calls} times in the last 30 days.",
     "idle": "You wrote {n} hooks; none ran in the last 30 days."},
    {"id": "mcp-built", "name": "MCP servers and tools you built", "level": "L4", "from": "board", "rows": ["cap-mcp-write"],
     "used": "You built {n} MCP servers; their tools were called {calls} times in the last 30 days.",
     "idle": "You built {n} MCP servers; no tool of theirs was called in the last 30 days."},
    {"id": "plugins", "name": "Plugins you wrote", "level": "L4", "from": "board", "rows": ["cap-plugins-own"],
     "used": "You wrote {n} plugins; their hooks and skills ran {calls} times in the last 30 days.",
     "idle": "You wrote {n} plugins; nothing of theirs ran in the last 30 days."},
    {"id": "agent-programs", "name": "Programs that run the agent", "level": "L4", "from": "board",
     "rows": ["cap-own-rig", "cap-sdk", "cap-event-runs"],
     # runs are not summed across the rows (a run through the Agent SDK is a headless run too): the sentence gives
     # the first row's count under that row's own unit (headless runs in those repos, else SDK runs)
     "used_p": "You wrote programs that start the agent in {p} projects; {calls} {unit} there in the last 30 days.",
     "used": "You wrote {n} programs that start the agent; {calls} {unit} in the last 30 days.",
     "idle": "You wrote {n} programs that start the agent; none ran it in the last 30 days.", "calls": "first"},
    {"id": "evals", "name": "Evals on what you built", "level": "L4", "from": "board", "rows": ["cap-evals"],
     "used": "You keep {n} sets of evals on what you built; {calls} {unit} in the last 30 days.",
     "idle": "You keep {n} sets of evals on what you built; none ran or changed in the last 30 days."},
]
STATE_RANK = [YOURS_USED, YOURS_IDLE, USES_OTHERS, CLAUDE_DID, NOT_SEEN, CANT_CHECK]


def _n(x):
    return f"{x:,}" if isinstance(x, int) else str(x)


def _fit(sentence):
    """A sentence built with a number: "1 times" -> "1 time", "in 1 projects" -> "in 1 project"."""
    for many, one in ((" times", " time"), (" projects", " project"), (" sessions", " session"), (" hooks", " hook"),
                      (" plugins", " plugin"), (" sub-agents", " sub-agent"), (" rule files", " rule file"),
                      (" MCP servers", " MCP server"), (" memory stores", " memory store"), (" programs", " program"),
                      (" sets of evals", " set of evals"), (" deny or ask rules", " deny or ask rule")):
        sentence = sentence.replace(f" 1{many} ", f" 1{one} ").replace(f" 1{many};", f" 1{one};").replace(f" 1{many}.", f" 1{one}.")
    return sentence


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
        return YOURS_USED, _fit(t["say"].format(n=_n(sum(c["n"] for _, c in met))))
    theirs = sum(counts[i]["n"] for i in t.get("claude") or [] if i in counts)
    few = sum(c["n"] for _, c in own)
    if theirs:
        mine = (f"You did it yourself {_n(few)} times, too few to call a habit." if few else "There were none started by you.")
        return CLAUDE_DID, _fit(f"Claude Code did this by itself: {_n(theirs)} {t['say_claude']}. {mine}")
    if few:
        return NOT_SEEN, _fit(f"You did this {_n(few)} times in the last 30 days, too few to call a habit.")
    return NOT_SEEN, "Nothing in your session records shows it."


def _from_board(t, rows):
    got = [rows[r] for r in t["rows"] if r in rows]
    if not got:
        return CANT_CHECK, "This was not read on this run."
    best = min(got, key=lambda r: STATE_RANK.index(BOARD_STATE.get(r.get("state"), CANT_CHECK)))
    state = BOARD_STATE.get(best.get("state"), CANT_CHECK)
    if state == USES_OTHERS and t.get("others_is"):
        state = t["others_is"]
    mine = [r for r in got if r.get("state") == best.get("state") or BOARD_STATE.get(r.get("state")) in PRESENT]
    items = sum(r.get("items") or 0 for r in mine)
    lead = next((r for r in mine if r.get("calls")), best) if t.get("calls") == "first" else best
    calls = (lead.get("calls") or 0) if t.get("calls") == "first" else sum(r.get("calls") or 0 for r in mine)
    # projects: the repos the items sit in, joined across rows by the board's opaque ids (a script that both starts
    # `claude -p` and imports the SDK sits in one project). An older run's rows carry none: the sentence counts items.
    ids = {i for r in mine for i in r.get("project_ids") or []}
    f = {"n": _n(items), "calls": _n(calls), "p": _n(len(ids)), "unit": lead.get("unit") or "calls"}
    # a sentence to "you" with counts only: a name of the person's thing never goes into a level's proof (the copy that
    # leaves the laptop masks names)
    if state == YOURS_USED:
        proof = (t["used_p"] if ids and t.get("used_p") else t.get("used") or "You have {n} of your own; used {calls} times in the last 30 days.").format(**f)
    elif state == YOURS_IDLE:
        proof = (t.get("idle") or "You have {n} of your own; nothing in the last 30 days shows them in use.").format(**f)
    elif state == USES_OTHERS:
        proof = ((t.get("others") if calls else t.get("others_idle")) or
                 ("Set up from others; called {calls} times in the last 30 days." if calls else "Set up from others.")).format(**f)
    else:
        proof = {CLAUDE_DID: "Only what Claude Code writes by itself; nothing here is shaped by you.",
                 NOT_SEEN: "None of your own found on this laptop.", CANT_CHECK: "The records can't show it."}[state]
    return state, _fit(proof)


def _scoped(run_dir, counts):
    """Rule files below a project's root, or a path-scoped rule: the code's map of their files (setup_map.py), and
    whether one reached the agent in a session."""
    m = _load(run_dir, "setup-map.json")
    if not m:
        return CANT_CHECK, "The map of your files was not drawn on this run."
    n = sum(((p.get("totals") or {}).get("scoped_files") or 0) for p in (m.get("projects") or []) + [m.get("user") or {}])
    if not n:
        return NOT_SEEN, "Every rule file of yours sits at a project's root."
    reached = (counts.get("setup.subfolder_rules_reached") or {}).get("n") or 0
    where = f"{_n(n)} of your rule files sit in a sub-folder or carry a path rule"
    return (YOURS_USED, _fit(f"{where}; they loaded in {_n(reached)} sessions.")) if reached else (
        YOURS_IDLE, f"{where}; none loaded in the last 30 days.")


def _days(run_dir):
    logs = ((_load(run_dir, "portable-checks.json") or {}).get("meta") or {}).get("logs") or {}
    return logs.get("days_with_sessions")


def things(run_dir, board, bars):
    """Every thing with its state and a one-line proof (counts, never a name)."""
    inv = _load(run_dir, "inventory.json")
    counts = _counts(inv)
    rows = {r["id"]: r for r in (board or {}).get("rows") or [] if r.get("id")}
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
                                            else f"You typed {_n(typed)} messages in the last 30 days.")
            else:
                state, proof = NOT_SEEN, "No session in the last 30 days."
        out.append({"id": t["id"], "name": t["name"], "level": t["level"], "state": state, "proof": proof})
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
        return (f"your files show a setup that carries the context in {len(setup.get('shown') or [])} of {n_parts} "
                f"parts, and L3 asks for {need}")

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
            reasons.append("L3 asks for a setup of your own that carries the context: rule files, memory with a shape, "
                           "skills you wrote.")
    elif days is None or days >= MIN_DAYS_FOR_L1:
        lvl, decided = "L1", "Claude Code used on real work; fewer than two habits of your own seen"
        reasons.append(f"You use Claude Code on real work{f' ({days} days with sessions)' if days is not None else ''}. "
                       f"L2 asks for at least {L2_HABITS_FOR_L2} of these as your own habit: "
                       + ", ".join(_lc(t["name"]) for t in at("L2")) + ".")
    else:
        lvl, decided = "can't tell", f"sessions on fewer than {MIN_DAYS_FOR_L1} days: too little to place a level"
        reasons.append(f"This laptop holds sessions on {days} day{'s' if days != 1 else ''}: too little to place a level.")
    return {"model": "things", "level": lvl, "could_be": could, "decided_by": decided,
            "reasons": [r for r in reasons if r],
            "things": ts, "habits": [t["id"] for t in habits],
            "setup_read": {"ran": bool(setup.get("ran")), "parts": setup.get("parts") or [],
                           "holds": setup.get("holds") or [], "shown": [g.get("id") for g in setup.get("shown") or []]},
            "builds": {**builds, "kinds": kinds, "kinds_need": L4_BUILT_KINDS, "evals_in_use": evals},
            "days_with_sessions": days}
