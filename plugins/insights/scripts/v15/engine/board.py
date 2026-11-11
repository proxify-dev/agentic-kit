#!/usr/bin/env python3
"""The capability board (v13's yes/no board, brought back): which parts of the harness the person built and uses, read
from facts on the laptop only. One row per v13 capability (labs/v13/substrate/capabilities.yaml), each with a state:

    built and used            something of theirs exists AND it ran / was called in the 30-day window
    built, not used lately    something of theirs exists, nothing in the window shows it running
    uses others'              installed from someone else (or a feature Claude Code ships); nothing of theirs
    not seen                  looked, and the files and logs hold none of it
    can't tell                the logs can't show it, or a count would be a guess; `why` says what is missing

Every state rests on a field in the run's files, named in the row's `source`. Whose a thing is comes from the scan, which
asks ownership.py (anatomy `origin`, inventory `owner`/`yours`, cheap-artifacts `own`): this file never decides ownership,
and never matches one person's tool names (names are only read back from the person's own files, to join a thing with
its calls). A row never turns silence into a "no": a silent log gives "not seen" only where the log would have shown it,
and "can't tell" where it would not.

    board(run_dir) -> {"rows": [...], "built_kinds": [ids], "uses_kinds": [ids]}

A row: id, name, kind ("build" | "use"), state, items (count of their own things), calls (runs / calls in the window),
unit (what `calls` counts), examples (up to 3 names: the Proxify copy only; the member copy shows counts, never names of
the client's things), source (file: field), why (one plain sentence). Standard library only.
"""
import hashlib
import json
import os
import re

BUILT_USED, BUILT_IDLE, USES_OTHERS, NOT_SEEN, CANT_TELL = (
    "built and used", "built, not used lately", "uses others'", "not seen", "can't tell")
BUILT_STATES = (BUILT_USED, BUILT_IDLE)

# Claude Code's own sub-agent types: not something the person built, even when an agent file of theirs overrides one.
BUILT_IN_AGENTS = {"general-purpose", "Explore", "Plan", "statusline-setup", "claude-code-guide", "fork"}
AUTO_MEMORY = "claude_auto_memory"  # written by Claude Code itself (autoMemoryEnabled), not a store they built
SDK_KINDS = ("claude_agent_sdk_import", "sdk_query_call")
RIG_KINDS = SDK_KINDS + ("claude_cli_print",)

# id -> (plain name, "build" | "use"). "build" rows can set a built kind; "use" rows can only say uses others' or not seen.
ROWS = [
    ("cap-skills-own", "Skills and commands you wrote", "build"),
    ("cap-skills-installed", "Skills and plugins from others", "use"),
    ("cap-instructions", "Instruction files (CLAUDE.md, AGENTS.md)", "build"),
    ("cap-hooks", "Hooks", "build"),
    ("cap-mcp", "MCP servers", "use"),
    ("cap-subagents", "Sub-agents you wrote", "build"),
    ("cap-teams", "Agent teams", "use"),
    ("cap-workflows", "Dynamic workflows", "build"),
    ("cap-parallel", "Parallel sessions and worktrees", "use"),
    ("cap-memory", "Memory across sessions", "build"),
    ("cap-model-routing", "Model routing", "build"),
    ("cap-permissions", "Permission rules", "build"),
    ("cap-event-runs", "Agents that start themselves", "build"),
    ("cap-evals", "Evals", "build"),
    ("cap-own-rig", "A program of yours that drives the agent", "build"),
    ("cap-plugins-own", "Plugins you wrote", "build"),
    ("cap-sdk", "Agent SDK", "build"),
    ("cap-mcp-write", "MCP servers you wrote", "build"),
    ("cap-output-styles", "Output styles", "build"),
    ("cap-telemetry", "Cost and telemetry", "use"),
]
# The kinds of build that count toward "built something" (the task's list): not instructions, memory, permissions or
# model routing, which are settings and habits as much as builds.
STATE_ORDER = (BUILT_USED, BUILT_IDLE, USES_OTHERS, NOT_SEEN, CANT_TELL)  # the order rows are listed in
BUILD_KINDS = ("cap-skills-own", "cap-hooks", "cap-subagents", "cap-plugins-own", "cap-mcp-write", "cap-own-rig",
               "cap-sdk", "cap-evals", "cap-output-styles", "cap-event-runs")


class Run:
    """The run's JSON files, each read once; a missing or broken file reads as empty, so one gap never stops the board."""

    def __init__(self, run_dir):
        self.dir = run_dir
        self.missing = []
        self.anatomy = self._load("anatomy.json")
        self.inv = self._load("inventory.json")
        self.cheap = self._load("cheap-artifacts.json", [])
        self.logs_ok = bool(self.inv)

    def _load(self, name, empty=None):
        try:
            with open(os.path.join(self.dir, name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            self.missing.append(name)
            return {} if empty is None else empty


def _l(x):
    return x if isinstance(x, list) else []


def _d(x):
    return x if isinstance(x, dict) else {}


def _n(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def _path(p):
    return os.path.normpath(os.path.expanduser(str(p or "")))


_VERSION_TAIL = re.compile(r"@v?\d[\w.+-]*$")           # a folder named name@1.2.3 is one version of name
_CLAUDE_WORKTREE = re.compile(r"^(.*?)/\.claude/worktrees/[^/]+$")
_main_cache = {}


def _main_checkout(root):
    """The main checkout a linked worktree belongs to, else the folder itself. Read from the worktree's `.git` file
    (`gitdir: <main>/.git/worktrees/<name>`), never by running git: the board reads the run's files only."""
    root = _path(root)
    if root in _main_cache:
        return _main_cache[root]
    main = root
    m = _CLAUDE_WORKTREE.match(root)
    if m:
        main = m.group(1)
    else:
        try:
            g = os.path.join(root, ".git")
            if os.path.isfile(g):
                line = open(g).read(600).strip()
                d = line.partition(":")[2].strip() if line.startswith("gitdir:") else ""
                d = d if os.path.isabs(d) else os.path.normpath(os.path.join(root, d))
                if "/.git/worktrees/" in d:
                    main = d.split("/.git/worktrees/")[0]
        except OSError:
            pass
    _main_cache[root] = main
    return main


def repo_name(root):
    """One name for a repo however many copies of it sit on the laptop: its main checkout's folder name, a trailing
    @version dropped. A worktree resolves to its main checkout; a second full copy keeps the folder's name. Two
    unrelated repos that share a folder name fold only where the same path sits in both."""
    return _VERSION_TAIL.sub("", os.path.basename(_main_checkout(root))) if root else ""


def one_per_repo_path(entries, root_of):
    """(one entry per file, the repos they sit in). The setup tool's rule (setup-snapshot CLAUDE.md, "Detectors"):
    compare files by their path inside the repo, across worktrees and checkouts. Why here (11-11): a laptop with three
    copies of one repo read "2914 rule files of yours", every copy of every file counted. `root_of(entry)` gives the
    checkout the file sits in; an entry with no path or no checkout is kept as it is."""
    def main_first(x):
        r = root_of(x)
        return 0 if r and _main_checkout(r) == _path(r) and not _VERSION_TAIL.search(_path(r)) else 1
    kept, repos, loose = {}, set(), []
    for x in sorted(entries, key=main_first):   # stable: the main checkout's copy stands for the others
        p, root = str(x.get("path") or ""), root_of(x)
        if not p or not root or not _path(p).startswith(_path(root) + os.sep):
            loose.append(x)
            continue
        rel = re.sub(r"^\.claude/worktrees/[^/]+/", "", os.path.relpath(_path(p), _path(root)))
        repos.add(repo_name(root))
        kept.setdefault((repo_name(root), rel), x)
    return list(kept.values()) + loose, repos


def _ids(names):
    """Repo names as opaque ids: a row may say how many projects without naming one."""
    return sorted({hashlib.sha1(str(n).encode()).hexdigest()[:8] for n in names if n})


def _short(name):
    """'plugin:skill' -> ('plugin', 'skill'); 'skill' -> ('', 'skill')."""
    ns, _, short = str(name or "").rpartition(":")
    return ns, short


def _first(*xs, n=3):
    out = []
    for x in xs:
        for name in x:
            if name and name not in out:
                out.append(name)
    return out[:n]


class Facts:
    """The shared reads several rows need, computed once from the run's files."""

    def __init__(self, r):
        a, i = _d(r.anatomy), _d(r.inv)
        self.r, self.a, self.i = r, a, i
        arts = [x for x in _l(a.get("artifacts")) if isinstance(x, dict)]
        self.arts = arts
        # one copy per file across worktrees and checkouts of one repo (one_per_repo_path): a skill in three copies is one skill
        self.own = lambda *kinds: one_per_repo_path(
            [x for x in arts if x.get("kind") in kinds and x.get("origin") == "own"], lambda x: x.get("project"))[0]
        self.others = lambda *kinds: [x for x in arts if x.get("kind") in kinds and x.get("origin") in ("installed", "third_party")]
        self.typed = _n(_d(i.get("chats")).get("typed_chats"))
        tools = _d(_d(i.get("tools")).get("typed_chats"))
        self.tool_calls = {t.get("what"): _n(t.get("calls")) for t in _l(tools.get("by_tool")) if isinstance(t, dict)}
        self.mcp_calls = {t.get("what"): _n(t.get("calls")) for t in _l(tools.get("by_mcp_server")) if isinstance(t, dict)}
        self.slash = {t.get("what"): _n(t.get("calls")) for t in _l(i.get("slash_commands_in_chats")) if isinstance(t, dict)}
        self.skills_used = [t for t in _l(i.get("skills_used")) if isinstance(t, dict)]
        who = {}
        for g in _l(_d(i.get("who_did_it")).get("groups")):
            for it in _l(_d(g).get("items")):
                if isinstance(it, dict):
                    who[it.get("id")] = _n(it.get("n"))
        self.who = who
        # plugin names that are theirs: the anatomy's own plugin manifests, and enabled plugins the inventory credits to them
        bad = {x.get("name") for x in arts if x.get("kind") == "plugin_manifest" and x.get("origin") != "own"}
        self.own_plugins = ({x.get("name") for x in self.own("plugin_manifest")}
                            | {str(p.get("key", "")).partition("@")[0] for p in _l(i.get("plugins"))
                               if isinstance(p, dict) and p.get("owner") == "theirs"}) - bad - {""}
        self.installed_plugins = {str(p.get("key", "")).partition("@")[0] for p in _l(i.get("plugins"))
                                  if isinstance(p, dict) and p.get("owner") == "installed" and p.get("on")} - {""}
        self.repos = {_path(x.get("repo")): x for x in _l(i.get("repos")) if isinstance(x, dict) and x.get("repo")}

    def repo_is_theirs(self, project):
        """A repo counts as theirs only when the inventory shows a commit of theirs there and most commits are theirs
        (ownership.py's majority rule, made stricter: a repo with none of their commits is not counted as theirs here)."""
        x = self.repos.get(_path(project))
        return bool(x) and _n(x.get("theirs_in_window")) > 0 and 2 * _n(x.get("theirs_in_window")) >= _n(x.get("commits_in_window"))


def row(rid, state, items=0, calls=0, unit="calls", examples=(), source="", why="", repos=None):
    """`repos`: the repo names the items sit in (repo_name). The row keeps how many, and opaque ids so two rows can be
    joined (a program that both starts `claude -p` and imports the SDK sits in one project, not two)."""
    name, kind = next((n, k) for i, n, k in ROWS if i == rid)
    out = {"id": rid, "name": name, "kind": kind, "state": state, "items": items, "calls": calls, "unit": unit,
           "examples": list(dict.fromkeys(x for x in examples if x))[:3], "source": source, "why": why}
    if repos is not None:
        out.update(projects=len(set(repos)), project_ids=_ids(repos))
    return out


def build_state(own, calls, others=0, none=NOT_SEEN):
    """The one mapping for a thing they can build: own and ran, own and idle, only others', or none."""
    if own and calls:
        return BUILT_USED
    if own:
        return BUILT_IDLE
    return USES_OTHERS if others else none


def cant(rid, why, source=""):
    return row(rid, CANT_TELL, source=source, why=why)


def _need(f, *names):
    """A row whose file is missing says so ('can't tell'), rather than reading the gap as 'not seen'."""
    gone = [n for n in names if n in f.r.missing]
    return ", ".join(gone) if gone else ""


def _skill_calls(f, names, plugin_names):
    """Skill calls for things of these names: a bare name matches a user/project skill; `x:name` only a thing of plugin x."""
    total, hit = 0, []
    for u in f.skills_used:
        ns, short = _short(u.get("what"))
        if short in names and (not ns or ns in plugin_names):
            total += _n(u.get("calls"))
            hit.append(short)
    return total, hit


# ------------------------------------------------------------------------------------------------------ the rows

TOO_BIG_LINES = 250        # his words: a skill "over 250 lines with no progressive disclosure and no other files"
TRY_SPLIT = "split the parts only some tasks need into references"
NOT_USED = "not used in the last 30 days"
EVALS_NOT_SEEN = "has an evals folder, never seen running"
ALWAYS_OPENED_FROM = 3     # a file opened in every one of this many loads or more is read whole each time: not on demand


def skill_depth(s):
    """One fact row from inventory.json `skill_depth` -> {name, depth, used, why, lines, too_big_one_file, try_next,
    live_context_syntax, has_evals_not_seen_running, evals_note}. Display only: nothing here touches a row's state or any
    level. His rulings 11-06, per skill the person wrote:
      -  never loaded in the window (no Skill call, no typed /name): depth None, used False, "not used in the last 30 days".
         Its structure-only facts (live_context_syntax, the 250-line flag) still show.
      1  one file, read whole every time: no other file of the skill is ever opened
      2  progressive disclosure: its own references or docs are opened in some loads, not all (the same read as
         put-rules-where-they-apply L3#2 deeper_docs_followed: per skill, its loads, and in how many the agent opened a file of it)
      3  it runs code: a script in its own folder was run after it loaded, or its SKILL.md uses the live-context syntax !`cmd`
      4  evals seen RUNNING the skill, from what Claude Code writes (no file-content guessing): `claude plugin eval` on the
         plugin that holds it, or a headless run that loaded it whose first prompt is written in an eval or test file of theirs (a
         file in an evals/, tests/ or test/ folder, or one their test or eval script reads). A skill whose evals are proven this
         way is used, even with no load of its own in the logs.
    An evals/, tests/ or test/ folder in the skill that neither route saw run is a note (has_evals_not_seen_running), never depth 4.
    The highest step the facts show; having a description or a long SKILL.md is never a step. `too_big_one_file` is a flag,
    never a minus: over TOO_BIG_LINES lines in a folder that holds nothing else."""
    s = _d(s)
    name, lines, others = str(s.get("name") or ""), int(_n(s.get("lines"))), int(_n(s.get("other_files")))
    loads, opened = int(_n(s.get("loads"))), int(_n(s.get("loads_opened")))
    ev, ran, live = _d(s.get("eval")), s.get("script_run"), bool(s.get("live_syntax"))
    selective = opened and not (opened == loads and loads >= ALWAYS_OPENED_FROM)
    used = loads > 0 or bool(ev)
    if ev:
        depth = 4
        parts = []
        if ev.get("plugin_evals"):
            parts.append(f"`claude plugin eval` was run on the plugin that holds it ({int(_n(ev.get('plugin_evals')))}x)")
        if ev.get("headless_runs"):
            n, files = int(_n(ev.get("headless_runs"))), [str(x) for x in ev.get("eval_files") or []]
            parts.append(f"{n} headless run{'' if n == 1 else 's'} that loaded it {'was' if n == 1 else 'were'} given a prompt "
                         f"written in an eval or test file of theirs{f' ({files[0]})' if files else ''}")
        why = " and ".join(parts)
    elif s.get("shared_name"):
        depth, used = None, None
        why = "an installed skill has the same name, so its uses can't be told apart"
    elif not loads:
        depth, used = None, False
        why = NOT_USED
    elif ran or live:
        depth = 3
        why = " and ".join(x for x in (f"its script {ran} was run after it loaded" if ran else "",
                                       "its SKILL.md runs a command when it loads (!`...`)" if live else "") if x)
    elif selective:
        depth = 2
        why = f"loaded {loads}x; the agent opened one of its own files in {opened} of them ({s.get('opened')}), not in all"
        if opened == loads:
            why = f"loaded {loads}x; the agent opened one of its own files ({s.get('opened')}) each time; too few loads to tell if always"
    else:
        depth = 1
        if opened:
            why = f"every one of {loads} loads opened another file of it: read whole each time, not on demand"
        elif others:
            why = f"loaded {loads}x; none of its {others} other files was opened"
        else:
            why = f"one file, read whole in all {loads} loads" if loads > 1 else "one file, read whole in its 1 load"
    big = lines > TOO_BIG_LINES and others == 0
    note = bool(s.get("has_evals_folder")) and not ev
    return {"name": name, "depth": depth, "used": used, "why": why, "lines": lines, "too_big_one_file": big,
            "try_next": TRY_SPLIT if big else "", "live_context_syntax": live,
            "has_evals_not_seen_running": note, "evals_note": EVALS_NOT_SEEN if note else ""}


def skill_depths(f):
    """The `skills` list and its summary for the cap-skills-own row: most depth first (a skill not used last), then by name. A run
    folder from before the scan read depth has no `skill_depth` section: no list, and the summary says why."""
    facts = f.i.get("skill_depth")
    if not isinstance(facts, list):
        return [], "depth not read in this run"
    skills = sorted((skill_depth(s) for s in facts if isinstance(s, dict) and s.get("name")),
                    key=lambda x: (x["depth"] is None, -(x["depth"] or 0), x["name"]))
    by = [sum(1 for x in skills if x["depth"] == d) for d in (4, 3, 2, 1)]
    flagged = sum(1 for x in skills if x["too_big_one_file"])
    unused = sum(1 for x in skills if x["used"] is False)
    unseen = sum(1 for x in skills if x["has_evals_not_seen_running"])
    n = len(skills)
    return skills, (f"{n} skill{'' if n == 1 else 's'}: depth 4 {by[0]} \u00b7 3 {by[1]} \u00b7 2 {by[2]} \u00b7 1 {by[3]}; "
                    f"not used {unused}; evals not seen running {unseen}; {flagged} flagged")


def r_skills_own(f):
    gone = _need(f, "anatomy.json")
    if gone:
        return cant("cap-skills-own", f"{gone} is missing", "anatomy.json")
    mine = f.own("skill", "command")
    names = {x.get("name") for x in mine}
    # a bare call to a name that an installed skill also has can't be told apart: it counts for neither row
    shared = names & {x.get("name") for x in f.others("skill", "command")}
    calls, hit = _skill_calls(f, names - shared, f.own_plugins)
    typed = f.who.get("you.own_commands_typed", 0)
    # a typed /name also shows as a Skill call: take the larger of the two, never their sum
    calls = max(calls, typed)
    r = row("cap-skills-own", build_state(len(mine), calls), len(mine), calls, "calls",
            _first(hit, sorted(names)),
            "anatomy.json: artifacts (kind skill/command, origin own); inventory.json: skills_used, skill_depth, who_did_it you.own_commands_typed",
            f"{len(mine)} skills and commands of yours; {calls} calls in the window" if mine else "no skill or command of yours found")
    # depth per skill: display only (see skill_depth); the state above is not touched by it
    r["skills"], r["skills_summary"] = skill_depths(f)
    return r


def r_skills_installed(f):
    gone = _need(f, "anatomy.json")
    if gone:
        return cant("cap-skills-installed", f"{gone} is missing", "anatomy.json")
    theirs = f.others("skill", "command")
    names = {x.get("name") for x in theirs}
    plugin_skills = set()
    for p in _l(_d(f.a.get("plugins")).get("enabled")):
        p = _d(p)
        if p.get("enabled") and p.get("installed") and not p.get("marketplace_is_own") and \
                str(p.get("key", "")).partition("@")[0] not in f.own_plugins:
            plugin_skills |= {s.get("name") for s in _l(_d(p.get("contents")).get("skills")) if isinstance(s, dict)}
    allnames = names | plugin_skills
    shared = names & {x.get("name") for x in f.own("skill", "command")}  # same name as one of theirs: not told apart
    calls, hit = _skill_calls(f, allnames - shared, f.installed_plugins)
    n = len(allnames) + len(f.installed_plugins - {""})
    return row("cap-skills-installed", USES_OTHERS if n else NOT_SEEN, n, calls, "calls", _first(hit, sorted(allnames)),
               "anatomy.json: artifacts (origin installed/third_party), plugins.enabled (installed, not their marketplace); inventory.json: skills_used",
               f"{len(allnames)} skills and commands and {len(f.installed_plugins)} plugins from others; {calls} calls in the window"
               if n else "nothing installed from others found")


def own_instruction_files(f):
    """Every instruction file the scan found that is theirs (anatomy.json instruction_files, origin by ownership.py),
    test fixtures left out; None when the run predates the origin field (11-09). Until 11-09 the row read the AI
    brief's list, which holds only the 10 biggest always-loaded files, so nested and smaller files went uncounted."""
    files = [x for x in _l(f.a.get("instruction_files")) if isinstance(x, dict)]
    if not files or not any("origin" in x for x in files):
        return None
    return [x for x in files if x.get("origin") == "own" and not x.get("in_fixture_path")]


def r_instructions(f):
    full = own_instruction_files(f)
    repos = None
    if full is not None:
        every = [str(x.get("path")) for x in full]   # each copy still loads in the sessions that ran beside it
        full, repos = one_per_repo_path(full, lambda x: _d(x.get("git")).get("repo") or x.get("project"))
        paths = [str(x.get("path")) for x in full]
        always = sum(1 for x in full if x.get("scope") in ("home", "project", "ancestor", "managed"))
        rules = full
    elif f.r.cheap == [] and "cheap-artifacts.json" in f.r.missing:
        return cant("cap-instructions", "cheap-artifacts.json is missing, so whose each file is isn't known", "cheap-artifacts.json")
    else:
        rules = [x for x in f.r.cheap if isinstance(x, dict) and x.get("kind") == "rules" and x.get("own") is True]
        paths = every = [str(x.get("id", "")).partition(":")[2] for x in rules]
        always = None
    sessions = 0
    home_claude = _path("~/.claude") + os.sep
    if any(_path(p).startswith(home_claude) for p in every):
        sessions = f.typed
    else:
        for repo, x in f.repos.items():
            if any(_path(p).startswith(repo + os.sep) for p in every):
                sessions += _n(x.get("typed_chats"))
    names = [os.path.basename(p) for p in paths]
    if always is not None:
        source = ("anatomy.json: instruction_files (origin own by ownership.py, test fixtures left out); "
                  "inventory.json: repos typed_chats (sessions in a repo holding one of them)")
        where = f" in {len(repos)} project{'s' if len(repos) != 1 else ''}" if repos else ""
        why = (f"{len(rules)} instruction files of yours{where}: {always} load in every session where they sit, "
               f"{len(rules) - always} load when Claude works in their folder; {sessions} sessions ran where they load")
    else:
        source = "cheap-artifacts.json: kind rules, own true; inventory.json: repos typed_chats (sessions in a repo holding one of them)"
        why = f"{len(rules)} instruction files of yours; {sessions} sessions ran where they load"
    return row("cap-instructions", build_state(len(rules), sessions), len(rules), sessions, "sessions", sorted(set(names)),
               source, why if rules else "no instruction file of yours found", repos=repos)


def r_hooks(f):
    runs = [h for g in ("typed_chats", "headless") for h in _l(_d(f.i.get("hook_runs")).get(g)) if isinstance(h, dict)]
    mine = {}
    for h in runs:
        if h.get("yours") is True and _n(h.get("runs")):
            mine[h.get("script")] = mine.get(h.get("script"), 0) + _n(h.get("runs"))
    others = [h for h in runs if h.get("yours") is False and _n(h.get("runs"))]
    wired = [x for x in f.own("hook_script") if x.get("wired")]
    n_own = len(set(mine) | {_path(x.get("path")) for x in wired})
    gone = _need(f, "inventory.json")
    if gone and not n_own:
        return cant("cap-hooks", "inventory.json is missing, so hook runs can't be read", "inventory.json")
    calls = sum(mine.values())
    return row("cap-hooks", build_state(n_own, calls, len(others)), n_own, calls, "runs",
               _first([os.path.basename(str(s)) for s in sorted(mine, key=mine.get, reverse=True)],
                      [x.get("name") for x in wired]),
               "inventory.json: hook_runs (yours, runs); anatomy.json: artifacts (hook_script, own, wired)",
               f"{n_own} hook scripts of yours; {calls} runs in the window" if n_own else
               ("only hooks from others ran" if others else "no hook of yours found"))


def r_mcp(f):
    gone = _need(f, "anatomy.json")
    if gone:
        return cant("cap-mcp", f"{gone} is missing", "anatomy.json")
    names = sorted({m.get("name") for m in _l(f.a.get("mcp_servers")) if isinstance(m, dict) and m.get("name")})
    calls = sum(f.mcp_calls.values())
    return row("cap-mcp", USES_OTHERS if names or calls else NOT_SEEN, len(names), calls, "calls", names,
               "anatomy.json: mcp_servers; inventory.json: tools.typed_chats.by_mcp_server",
               f"{len(names)} MCP servers set up; {calls} calls in the window" if names or calls else "no MCP server set up")


def r_subagents(f):
    mine = [x for x in f.own("agent") if x.get("name") not in BUILT_IN_AGENTS]
    names = {x.get("name") for x in mine}
    types = {t.get("type"): _n(t.get("calls")) for t in _l(_d(f.i.get("helpers")).get("by_type")) if isinstance(t, dict)}
    calls = sum(c for t, c in types.items() if t in names)
    others = [x for x in f.others("agent") if x.get("name") not in BUILT_IN_AGENTS]
    ran = [t for t in sorted(names, key=lambda t: -types.get(t, 0)) if types.get(t)]
    built_in = sum(c for t, c in types.items() if t in BUILT_IN_AGENTS)
    return row("cap-subagents", build_state(len(mine), calls, len(others)), len(mine), calls, "calls", _first(ran, sorted(names)),
               "anatomy.json: artifacts (kind agent, origin own); inventory.json: helpers.by_type (calls per sub-agent type)",
               f"{len(mine)} sub-agents of yours; {calls} launches in the window" if mine else
               f"no sub-agent of yours; Claude Code's built-in sub-agents ran {built_in} times")


def r_teams(f):
    n = f.who.get("cant.teammates")
    if n is None:
        return cant("cap-teams", "the teammate count is missing from inventory.json", "inventory.json: who_did_it cant.teammates")
    return row("cap-teams", USES_OTHERS if n else NOT_SEEN, 0, n, "teammates", (),
               "inventory.json: who_did_it cant.teammates (teammates in agent teams)",
               f"{n} teammates ran in agent teams (a Claude Code feature; nothing of yours to count)" if n else "no agent team ran in the window")


def r_workflows(f):
    calls = f.tool_calls.get("Workflow", 0)
    mine = f.own("workflow")
    if not calls and not mine:
        return cant("cap-workflows", "a Claude Code version without the Workflow tool reads the same as never using it; "
                    "ask on the call", "inventory.json: tools.typed_chats.by_tool Workflow; anatomy.json: artifacts (workflow)")
    return row("cap-workflows", build_state(len(mine), calls, 1 if calls else 0), len(mine), calls, "calls",
               [x.get("name") for x in mine],
               "inventory.json: tools.typed_chats.by_tool Workflow; anatomy.json: artifacts (kind workflow, origin own)",
               f"{len(mine)} workflow scripts of yours; Workflow tool called {calls} times" if mine else
               f"Workflow tool called {calls} times, none of your own saved workflow files")


def r_parallel(f):
    wt = f.tool_calls.get("EnterWorktree", 0) + _n(_d(f.i.get("chats_logged")).get("worktree_adds_by_shell"))
    if not f.r.logs_ok:
        return cant("cap-parallel", "inventory.json is missing", "inventory.json")
    return row("cap-parallel", USES_OTHERS if wt else NOT_SEEN, 0, wt, "worktrees", (),
               "inventory.json: tools EnterWorktree calls, chats_logged.worktree_adds_by_shell",
               f"{wt} worktrees opened in the window (a Claude Code feature)" if wt else "no worktree opened in the window")


def r_memory(f):
    stores = [s for s in _l(f.i.get("memory_stores")) if isinstance(s, dict)]
    built = [s for s in stores if s.get("kind") not in (AUTO_MEMORY, "referenced_notes")]
    auto = [s for s in stores if s.get("kind") == AUTO_MEMORY]
    reads = sum(_n(s.get("agent_reads")) for s in built)
    early = _n(_d(f.i.get("past_work_reach")).get("looked_it_up_early"))
    if not stores and "inventory.json" in f.r.missing:
        return cant("cap-memory", "inventory.json is missing", "inventory.json")
    calls = reads or early
    st = build_state(len(built), calls, len(auto))
    return row("cap-memory", st, len(built), calls, "reads", [os.path.basename(str(s.get("path", "")).rstrip("/")) for s in built],
               "inventory.json: memory_stores (kind, agent_reads), past_work_reach.looked_it_up_early",
               f"{len(built)} memory stores kept; the agent read them {calls} times" if built else
               ("only Claude Code's automatic memory exists" if auto else "no memory store found"))


def r_model_routing(f):
    mine = [x for x in f.own("agent") if x.get("name") not in BUILT_IN_AGENTS and x.get("model") not in (None, "", "inherit")]
    names = {x.get("name") for x in mine}
    types = {t.get("type"): _n(t.get("calls")) for t in _l(_d(f.i.get("helpers")).get("by_type")) if isinstance(t, dict)}
    typed = f.slash.get("/model", 0) + f.slash.get("/fast", 0)
    calls = sum(c for t, c in types.items() if t in names) + typed
    other = _n(_d(f.i.get("helpers")).get("calls_that_set_a_model"))
    return row("cap-model-routing", build_state(len(mine), calls, 1 if typed or other else 0), len(mine), calls, "calls",
               sorted(names),
               "anatomy.json: artifacts (agent, own, model set); inventory.json: helpers.by_type, helpers.calls_that_set_a_model, slash_commands_in_chats /model /fast",
               f"{len(mine)} of your sub-agents pin a model; {calls} launches and switches in the window" if mine else
               ("models were set on calls or switched by hand, none pinned in a sub-agent file of yours" if typed or other else "no model choice seen"))


def r_permissions(f):
    own_settings = {_path(str(x.get("id", "")).partition(":")[2]) for x in f.r.cheap
                    if isinstance(x, dict) and x.get("kind") == "settings" and x.get("own") is True}
    limits, files = 0, []
    for c in _l(_d(f.a.get("settings")).get("counts")):
        c = _d(c)
        if _path(c.get("file")) in own_settings:
            k = _n(c.get("permissions_deny")) + _n(c.get("permissions_ask"))
            if k:
                limits += k
                files.append(os.path.basename(str(c.get("file"))))
    if not own_settings and "cheap-artifacts.json" in f.r.missing:
        return cant("cap-permissions", "cheap-artifacts.json is missing, so whose settings file it is isn't known", "cheap-artifacts.json")
    return row("cap-permissions", build_state(limits, f.typed), limits, f.typed, "sessions", (),
               "anatomy.json: settings.counts (permissions_deny + permissions_ask) of settings files cheap-artifacts.json marks own; "
               "inventory.json: chats.typed_chats (the rules load in every session)",
               f"{limits} deny or ask rules of yours; they applied in {f.typed} sessions" if limits else "no deny or ask rule of yours found (allow rules alone are not a limit)")


def r_event_runs(f):
    ln = _d(f.i.get("launchers"))
    cron = [c for c in _l(ln.get("crontab")) if isinstance(c, dict) and c.get("own") and c.get("starts_agents")]
    sched = sum(_n(_d(t).get("count")) for t in _l(ln.get("scheduled_tasks")))
    launchd = [x for x in _l(ln.get("launchd_matching"))]
    own = len(cron) + sched + len(launchd)
    hl = _d(f.i.get("headless"))
    unattended = _n(hl.get("while_away", hl.get("started_with_no_typed_message_in_30_min_before")))
    if own:
        return row("cap-event-runs", build_state(own, unattended), own, unattended, "runs", (),
                   "inventory.json: launchers (crontab starts_agents, launchd_matching, scheduled_tasks); headless.while_away",
                   f"{own} schedules of yours start an agent; {unattended} runs began while you were away")
    if unattended:
        return cant("cap-event-runs", f"{unattended} runs began while you were away, but the logs don't say whether a schedule, a hook, "
                    "an app or a plugin started them, and no schedule of yours that starts an agent was found",
                    "inventory.json: headless.while_away, launchers")
    if not f.r.logs_ok:
        return cant("cap-event-runs", "inventory.json is missing", "inventory.json")
    return row("cap-event-runs", NOT_SEEN, 0, 0, "runs", (), "inventory.json: launchers, headless", "no schedule of yours that starts an agent, and no unattended run")


def r_evals(f):
    seen, mine = set(), []
    for e in _l(f.i.get("evals")):
        e = _d(e)
        if e.get("owner") == "theirs":
            key = (e.get("files"), e.get("case_files"), e.get("ledger_lines"), e.get("last_run"), e.get("newest_file"))
            if key not in seen:  # the same folder reached through a plugin's installed copy counts once
                seen.add(key)
                mine.append(e)
    if not mine and "inventory.json" in f.r.missing:
        return cant("cap-evals", "inventory.json is missing", "inventory.json")
    ran = sum(_n(e.get("ledger_lines_in_window")) for e in mine)
    changed = sum(1 for e in mine if e.get("changed_in_window"))
    calls = ran or changed
    unit = "recorded runs" if ran else "folders changed"
    return row("cap-evals", build_state(len(mine), calls), len(mine), calls, unit,
               [os.path.basename(os.path.dirname(str(e.get("path", "")).rstrip("/"))) for e in mine],
               "inventory.json: evals (owner theirs; ledger_lines_in_window = runs recorded in the window; changed_in_window)",
               f"{len(mine)} eval folders of yours; {ran} runs recorded in the window, {changed} folders changed" if mine else "no eval folder of yours found")


def _own_scripts(f, kinds):
    out = []
    for s in _l(_d(f.a.get("launchers")).get("scripts")):
        s = _d(s)
        if s.get("only_in_comments") or not set(s.get("kinds") or []) & set(kinds):
            continue
        if f.repo_is_theirs(s.get("project")):
            out.append(s)
    return one_per_repo_path(out, lambda x: x.get("project"))[0]


def _repos_of(scripts):
    return {repo_name(s.get("project")) for s in scripts if s.get("project")}


def r_own_rig(f):
    mine = _own_scripts(f, RIG_KINDS)
    projects = {_path(s.get("project")) for s in mine}
    runs = _d(f.i.get("headless"))
    calls = sum(_n(n) for repo, n in (x for x in _l(runs.get("by_repo")) if isinstance(x, list) and len(x) == 2)
                if _path(repo) in projects)
    total = _n(runs.get("runs"))
    if mine:
        return row("cap-own-rig", build_state(len(mine), calls), len(mine), calls, "headless runs",
                   [os.path.basename(str(s.get("path"))) for s in mine],
                   "anatomy.json: launchers.scripts (starts claude -p / SDK, in a repo with their commits); inventory.json: headless.by_repo",
                   f"{len(mine)} scripts of yours start an agent; {calls} headless runs in those repos", repos=_repos_of(mine))
    if total:
        return cant("cap-own-rig", f"{total} headless runs, but a run is recorded the same whether you typed `claude -p`, a script of yours "
                    "or a plugin started it, and no script of yours that starts an agent was found",
                    "inventory.json: headless.runs; anatomy.json: launchers.scripts")
    if not f.r.logs_ok:
        return cant("cap-own-rig", "inventory.json is missing", "inventory.json")
    return row("cap-own-rig", NOT_SEEN, 0, 0, "headless runs", (), "anatomy.json: launchers.scripts; inventory.json: headless", "no script of yours that starts an agent, and no headless run")


def r_plugins_own(f):
    names = sorted(f.own_plugins)
    hooks = {}
    for g in ("typed_chats", "headless"):
        for h in _l(_d(f.i.get("hook_runs")).get(g)):
            key = str(_d(h).get("set_up_by", "")).partition("@")[0]
            if key in f.own_plugins:
                hooks[key] = hooks.get(key, 0) + _n(_d(h).get("runs"))
    skills = {}
    for u in f.skills_used:
        ns, _ = _short(u.get("what"))
        if ns in f.own_plugins:
            skills[ns] = skills.get(ns, 0) + _n(u.get("calls"))
    calls = sum(hooks.values()) + sum(skills.values())
    ran = sorted(set(hooks) | set(skills), key=lambda k: -(hooks.get(k, 0) + skills.get(k, 0)))
    return row("cap-plugins-own", build_state(len(names), calls), len(names), calls, "hook runs and skill calls", _first(ran, names),
               "anatomy.json: artifacts (plugin_manifest, own); inventory.json: plugins (owner theirs), hook_runs set_up_by, skills_used `plugin:skill`",
               f"{len(names)} plugins of yours; their hooks and skills ran {calls} times" if names else "no plugin of yours found")


def r_sdk(f):
    mine = _own_scripts(f, SDK_KINDS)
    ep = _d(_d(f.i.get("headless")).get("entrypoints"))
    calls = _n(ep.get("sdk-py")) + _n(ep.get("sdk-ts"))
    if mine:
        return row("cap-sdk", build_state(len(mine), calls), len(mine), calls, "SDK runs", [os.path.basename(str(s.get("path"))) for s in mine],
                   "anatomy.json: launchers.scripts (SDK import or query call, in a repo with their commits); inventory.json: headless.entrypoints sdk-py / sdk-ts",
                   f"{len(mine)} scripts of yours use the Agent SDK; {calls} SDK runs in the window", repos=_repos_of(mine))
    if calls:
        return cant("cap-sdk", f"{calls} runs came through the Agent SDK, but none from a script of yours that was found "
                    "(an installed plugin or an app can start them too)", "inventory.json: headless.entrypoints; anatomy.json: launchers.scripts")
    if not f.r.logs_ok:
        return cant("cap-sdk", "inventory.json is missing", "inventory.json")
    return row("cap-sdk", NOT_SEEN, 0, 0, "SDK runs", (), "anatomy.json: launchers.scripts; inventory.json: headless.entrypoints", "no Agent SDK script of yours and no SDK run")


def r_mcp_write(f):
    if "anatomy.json" in f.r.missing:
        return cant("cap-mcp-write", "anatomy.json is missing", "anatomy.json")
    plugins = _d(f.a.get("plugins"))
    not_mine = {x.get("name") for x in f.arts if x.get("kind") == "plugin_manifest" and x.get("origin") != "own"}
    own_dirs = {}  # plugin folder -> server names, for plugins the scan says are theirs and not a teammate's
    for p in _l(plugins.get("dir_loaded")):
        p = _d(p)
        if p.get("origin") == "own" and p.get("name") not in not_mine:
            own_dirs[_path(p.get("path"))] = [s for s in _l(_d(p.get("contents")).get("mcp_servers")) if isinstance(s, str)]
    mine, unplaced = set(), 0
    for folder, servers in own_dirs.items():
        mine |= set(servers)
    for m in _l(f.a.get("mcp_servers")):
        m = _d(m)
        entry = m.get("entry")
        if m.get("transport") != "stdio" or not entry or not str(entry).startswith(("/", "~")):
            continue
        if any(_path(entry).startswith(folder + os.sep) for folder in own_dirs):
            mine.add(m.get("name"))
        else:
            unplaced += 1
    mine = {m for m in mine if m}

    def calls_of(name):
        return sum(c for what, c in f.mcp_calls.items() if what == name or what.endswith("_" + name))
    calls = sum(calls_of(n) for n in mine)
    if mine:
        return row("cap-mcp-write", build_state(len(mine), calls), len(mine), calls, "calls",
                   sorted(mine, key=lambda n: -calls_of(n)),
                   "anatomy.json: plugins.dir_loaded (origin own, contents.mcp_servers), mcp_servers entry inside such a folder; inventory.json: tools.by_mcp_server",
                   f"{len(mine)} MCP servers inside plugins of yours; {calls} calls in the window")
    if unplaced:
        return cant("cap-mcp-write", f"{unplaced} MCP servers run a script from a folder the scan didn't place as yours or installed; "
                    "none sits inside a plugin of yours", "anatomy.json: mcp_servers entry")
    return row("cap-mcp-write", NOT_SEEN, 0, 0, "calls", (), "anatomy.json: mcp_servers, plugins.dir_loaded",
               "every MCP server set up is a hosted one or started with npx/uvx; none of yours")


def r_output_styles(f):
    mine = f.own("output_style")
    names = {x.get("name") for x in mine}
    chosen = ""
    for s in _l(f.i.get("settings")):
        v = _d(_d(s).get("notable")).get("outputStyle")
        if isinstance(v, str) and v:
            chosen = v
    on = chosen in names
    return row("cap-output-styles", build_state(len(mine), f.typed if on else 0), len(mine), f.typed if on else 0, "sessions",
               ([chosen] if on else []) + sorted(names),
               "anatomy.json: artifacts (output_style, own); inventory.json: settings notable.outputStyle (the style in force; every session uses it)",
               (f"{len(mine)} output styles of yours; one is switched on" if on else f"{len(mine)} output styles of yours; none switched on") if mine
               else "no output style of yours found")


def r_telemetry(f):
    envs = [e.get("name") for e in _l(_d(f.a.get("settings")).get("env_keys")) if isinstance(e, dict)]
    tele = [n for n in envs if str(n).startswith(("CLAUDE_CODE_ENABLE_TELEMETRY", "OTEL_"))]
    screens = sum(f.slash.get(c, 0) for c in ("/cost", "/usage", "/stats"))
    if not f.r.logs_ok and not tele:
        return cant("cap-telemetry", "inventory.json is missing", "inventory.json")
    on = bool(tele) or screens > 0
    return row("cap-telemetry", USES_OTHERS if on else NOT_SEEN, len(tele), screens, "times opened", (),
               "anatomy.json: settings.env_keys (telemetry variables); inventory.json: slash_commands_in_chats /cost /usage /stats",
               ("telemetry is switched on" if tele else "") + ("; " if tele and screens else "") +
               (f"you opened the cost or usage screens {screens} times" if screens else "") if on else
               "no telemetry variable set and no cost screen opened (a shell profile is not read)")


READERS = [r_skills_own, r_skills_installed, r_instructions, r_hooks, r_mcp, r_subagents, r_teams, r_workflows, r_parallel,
           r_memory, r_model_routing, r_permissions, r_event_runs, r_evals, r_own_rig, r_plugins_own, r_sdk, r_mcp_write,
           r_output_styles, r_telemetry]


def board(run_dir):
    f = Facts(Run(run_dir))
    rows = []
    for rid, reader in zip((r[0] for r in ROWS), READERS):
        try:
            rows.append(reader(f))
        except Exception as e:  # a surprise in one file's shape reads as "can't tell" for that row, never a broken run
            rows.append(cant(rid, f"the files held a shape this row did not expect ({type(e).__name__})"))
    rows.sort(key=lambda r: STATE_ORDER.index(r["state"]))  # stable: v13's order inside each state
    return {"rows": rows,
            "built_kinds": [r["id"] for r in rows if r["id"] in BUILD_KINDS and r["state"] in BUILT_STATES],
            "uses_kinds": [r["id"] for r in rows if r["state"] == USES_OTHERS]}


if __name__ == "__main__":
    import sys
    b = board(sys.argv[1] if len(sys.argv) > 1 else ".")
    for r in b["rows"]:
        print(f'{r["id"]:22} {r["state"]:24} items={r["items"]:<4} calls={r["calls"]:<6} {r["unit"]:12} {r["examples"]} | {r["why"]}')
    print("built_kinds", b["built_kinds"], "\nuses_kinds", b["uses_kinds"])
