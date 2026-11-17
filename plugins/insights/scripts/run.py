#!/usr/bin/env python3
"""The two halves of the setup tool around the one AI pass. Read-only on the person's machine: it reads
~/.claude and the git repos Claude Code ran in, and writes only into RUN_DIR (and the page beside it;
frame.py keeps the page it replaces in archive/ there).

    python3 run.py pre  RUN_DIR   inventory -> commit probe -> problems, artifacts, practices packet
                                  (--only NAME: review these projects instead of the 3 busiest; --skip NAME: leave
                                  this one out of the review; each repeatable, a folder name or a path: setup_choice.py)
    python3 run.py post RUN_DIR   check the AI pass's rows -> assemble -> engine -> page   (--no-open: don't open the browser)
    python3 run.py pre [RUN_DIR] --dry-run   what a real `pre` and the AI pass would do, in plain words: the run folder it would
                                  make, the projects it would read and why, each step with what it reads and writes, the AI work
                                  and a rough token figure. Writes nothing, starts no AI (RUN_DIR optional: it is not made)
    python3 run.py candidates [--json]   the repos with sessions in the last 30 days, busiest first, each with the key to give
                                  --only/--skip and whether the default pick takes it; read-only, session logs only (--only/--skip
                                  show the pick they would give). --json is what the skill reads
    python3 run.py newrun         a new, empty run folder, ~/agentic-setup/run-<YYYY-MM-DD-HHMM>/ (-2, -3 when that minute is taken): prints its
                                  path, which is the RUN_DIR of the other phases. Every run gets its own, so no run overwrites another's answers
    python3 run.py share RUN_DIR  share-with-proxify.json beside RUN_DIR: the summary the page's Share tab sends, as a file
                                  (which practices were seen and at which step, as ids and levels; no file names, no text)

The page is the member's copy, opening on "Your level": the level the laptop reads, said to the member, with none of
Proxify's internal views (11-11 ask; the 11-11 morning default shipped the full page, internal tabs and all).
--proxify or AGENTIC_SETUP_PROXIFY=1 builds the full page (substrate, call sheet, internal level read) for Proxify staff;
give it to `pre` too, so the readers' briefs keep the part-by-part questions that page shows (a member's brief asks
only for the review of their agent-facing files).
--no-level or AGENTIC_SETUP_NO_LEVEL=1 (old names: --member, AGENTIC_SETUP_MEMBER=1) builds the member's copy with no level.

The AI pass sits between them (SKILL.md): a subagent reads RUN_DIR/cheap-practices.md and RUN_DIR/cheap-digest.md and writes
RUN_DIR/ai-result.json = {"rows": [...]}. The page lands in RUN_DIR's parent as my-setup.html (--out to change).

Python: with PyYAML (the substrate tabs read yaml) the page has every tab. Without it those tabs say "not in this run".
When PyYAML is missing and `uv` is on PATH, this re-runs itself under `uv run --with pyyaml` (uv fetches PyYAML once).
The code it calls is a copy of labs/ (see GENERATED.md) with labs' layout, so it finds its neighbours by relative path.
"""
import argparse
import contextlib
import glob
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.join(HERE, "setup-snapshot", "experiments")
CHEAP = os.path.join(EXP, "2026-11-02-cheap")
FRAME = os.path.join(HERE, "v15", "engine", "variants", "views", "frame.py")
ENGINE = os.path.join(HERE, "v15", "engine", "engine.py")
ANATOMY = os.path.join(HERE, "v15", "engine", "anatomy_scan.py")  # each discipline's parts: own checks + AI briefs
SETUP_READ = os.path.join(EXP, "2026-11-11-setup-read")  # the map of their agent-facing files, and the review the readers write from it
AI_RESULT = "ai-result.json"  # the file the AI pass writes and assemble.py reads
RUNGS, STATES = {"L1", "L2", "L3", "L4", ""}, {"seen", "looked, not found", "not read"}
ROW_KEYS = ("id", "state", "rung", "could_be", "why", "basis", "evidence", "rule", "ask")


def with_pyyaml():
    """The code needs Python 3.12+ (f-strings with quotes and backslashes in the views) and, for the substrate tabs, PyYAML.
    When either is missing and `uv` is on PATH, re-run this under `uv run --with pyyaml` (uv brings its own Python 3.12
    and fetches PyYAML once). No uv: too old a Python is an error; a missing PyYAML only blanks the substrate tabs."""
    old = sys.version_info < (3, 12)
    try:
        import yaml  # noqa: F401
        have_yaml = True
    except ImportError:
        have_yaml = False
    if not old and have_yaml:
        return
    uv = shutil.which("uv")
    if old and not uv:
        sys.exit(f"This needs Python 3.12 or newer (this is {sys.version_info[0]}.{sys.version_info[1]}). Install uv "
                 "(https://docs.astral.sh/uv/), which brings its own Python, or a newer Python, and run it again.")
    if os.environ.get("AGENTIC_SETUP_UV") or not uv:
        print("note: PyYAML not available; the substrate tabs will say 'not in this run' (your own tabs are unaffected)")
        return
    cmd = [uv, "run", "--no-project", "--with", "pyyaml", *(["--python", "3.12"] if old else []), "python"]
    if subprocess.run(cmd + ["-c", "import yaml"], capture_output=True).returncode != 0:
        if old:
            sys.exit("uv could not set up Python 3.12 with PyYAML (offline?). Connect once and run it again.")
        print("note: uv could not fetch PyYAML (offline?); the substrate tabs will say 'not in this run'")
        return
    sys.exit(subprocess.run(cmd + [os.path.abspath(__file__), *sys.argv[1:]], env={**os.environ, "AGENTIC_SETUP_UV": "1"}).returncode)


def run_stem(root=None):
    """(the folder all runs live in, ~/agentic-setup by default; the first name a run takes: run-<YYYY-MM-DD-HHMM>, local
    time from clock.py). Nothing is made here."""
    sys.path.insert(0, os.path.join(EXP, "2026-11-02-evidence"))
    import clock
    root = root or os.path.join(os.path.expanduser("~"), "agentic-setup")
    return root, os.path.join(root, "run-" + time.strftime("%Y-%m-%d-%H%M", time.localtime(clock.now())))


def would_be_run(root=None):
    """The folder `newrun` would make now (-2, -3 ... when that minute is taken), without making it. For the dry run."""
    _, stem = run_stem(root)
    n = 1
    while os.path.exists(stem if n == 1 else f"{stem}-{n}"):
        n += 1
    return stem if n == 1 else f"{stem}-{n}"


def newrun(root=None):
    """A run folder that did not exist a moment ago: ~/agentic-setup/run-<YYYY-MM-DD-HHMM>, with -2, -3 ... when that minute is
    taken. os.mkdir fails on an existing folder, so two runs can never share one. The time is clock.py's (local time).
    Python 3.9 and the standard library only: this runs before with_pyyaml can re-run it under uv."""
    root, stem = run_stem(root)
    os.makedirs(root, exist_ok=True)
    n = 1
    while True:
        path = stem if n == 1 else f"{stem}-{n}"
        try:
            os.mkdir(path)
            return path
        except FileExistsError:
            n += 1


def step(title, argv):
    t = time.time()
    print(f"\n== {title}", flush=True)
    r = subprocess.run([sys.executable, *argv])
    if r.returncode != 0:
        sys.exit(f"\nstopped: '{title}' failed (exit {r.returncode}). Nothing was changed outside the run folder.")
    print(f"   done in {time.time() - t:.0f}s", flush=True)


def soft_step(title, argv):
    """A step whose failure is not a stop: the page is built without it and says so. The setup read is one: when it
    fails, the engine reads the level as 'could be' (engine.setup_read_gate), never as a pass."""
    t = time.time()
    print(f"\n== {title}", flush=True)
    r = subprocess.run([sys.executable, *argv])
    if r.returncode != 0:
        print(f"   note: '{title}' failed (exit {r.returncode}); the run goes on without it", flush=True)
        return False
    print(f"   done in {time.time() - t:.0f}s", flush=True)
    return True


def pre_steps(run, proxify=False, asked=(), also=()):
    """The steps `pre` runs, in order: the one list `pre` executes and `pre --dry-run` describes, so they cannot drift.
    Each: title (as printed), argv (for sys.executable), soft (a failure is not a stop), after_previous (runs only when the
    step before it worked), reads and writes (plain words for the dry run)."""
    window = "the last 30 days"
    return [
        {"title": "1/3 inventory: what is on this machine and what the sessions did (the slow step)",
         "argv": ["-I", os.path.join(EXP, "2026-11-02-evidence", "inventory.py"), "--out", run, *[x for r in also for x in ("--also", r)]],
         "reads": f"your Claude Code session logs ({window}), the settings, skills, agents and hooks in ~/.claude, and the git repos your sessions ran in (git log and file lists only)",
         "writes": "inventory.json, chats.jsonl, worked-in.json, anatomy.json and a few more files in the run folder"},
        {"title": "2/3 commit check: were the agent's commits looked at before they landed",
         "argv": ["-I", os.path.join(EXP, "2026-11-02-problems", "detectors", "comprehension_debt.py"),
                  "--chats", os.path.join(run, "chats.jsonl"), "--out", os.path.join(run, "comprehension-debt.json")],
         "reads": f"your session logs ({window}) and the commits made in them",
         "writes": "comprehension-debt.json in the run folder, and a cache in ~/.cache/setup-snapshot/ (removed by a later run)"},
        {"title": "3/3 problems (code only), what you built, and the brief for the AI pass",
         "argv": ["-I", os.path.join(CHEAP, "problems.py"), run, "--commits", os.path.join(run, "comprehension-debt.json")],
         "reads": "the inventory and the commit check in the run folder",
         "writes": "cheap-problems.json in the run folder"},
        {"title": "   artifacts",
         "argv": ["-I", os.path.join(CHEAP, "artifacts.py"), run],
         "reads": "the inventory in the run folder, and the hooks, agents, skills and tools of yours it lists",
         "writes": "cheap-digest.md (what you built, read by the AI pass) and cheap-artifacts.json"},
        {"title": "   practices packet",
         "argv": ["-I", os.path.join(CHEAP, "practices_packet.py"), run],
         "reads": "the list of practices and their ladders shipped with this tool",
         "writes": "cheap-practices.md (read by the AI pass)"},
        {"title": "   who-did-it counts -> rungs (code, by the substrate's signal map)",
         "argv": ["-I", os.path.join(CHEAP, "signals.py"), run],
         "reads": "the inventory in the run folder",
         "writes": "signal-rows.json"},
        {"title": "   portable checks: counts that read the same on any laptop (each with a time budget)",
         "argv": ["-I", os.path.join(EXP, "2026-11-05-portable", "run_portable.py"), run],
         "reads": f"your session logs and git repos ({window})",
         "writes": "portable-checks.json in the run folder, and a speed-up cache in ~/.cache/setup-snapshot/portable/ (up to a few GB, used for 6 hours)"},
        {"title": "   anatomy: each discipline's parts — its own code checks, and one brief per most active project",
         "argv": ["-I", ANATOMY, run, *asked],
         "reads": f"your session logs ({window}) and, for each reviewed project, its files and git history",
         "writes": "anatomy-scan.json, and one anatomy-brief-<project>.md per reviewed project, in the run folder"},
        # A member's run: each project's brief holds only the review of its agent-facing files (the member's page has no
        # part-by-part view, and its questions were most of what a reader spent). Proxify's own run keeps the questions.
        {"title": "   setup map: the outline of your AGENTS.md files, memory, skills and agents, and what points to what",
         "argv": ["-I", os.path.join(SETUP_READ, "setup_map.py"), run, *asked], "soft": True,
         "reads": "the AGENTS.md / CLAUDE.md files, memory, skills and agents of the reviewed projects and of ~/.claude (structure only)",
         "writes": "setup-map.json and setup-map.md in the run folder"},
        {"title": "   setup read: the review of those files, " + ("added to each project's brief" if proxify else "one brief per project"),
         "argv": ["-I", os.path.join(SETUP_READ, "setup_read.py"), "brief", run] + (["--proxify"] if proxify else []),
         "soft": True, "after_previous": True,
         "reads": "the outline in the run folder",
         "writes": "the briefs the readers follow (anatomy-brief-<project>.md) in the run folder"},
    ]


def asked_args(c, only, skip):
    """(the flags both pickers get, the repos the inventory's file scan must open) from `candidates`' answer `c` to the
    person's --only/--skip. --only becomes the absolute root of each project it took, so the anatomy step and the setup
    map take exactly the repos checked before the scan (no name read twice), and the inventory opens each of them even
    when few sessions started there. --skip stays as given: a folder name leaves every clone of it out."""
    roots = [c["pick_roots"][k] for k in c["pick"]] if only else []
    return [x for r in roots for x in ("--only", r)] + [x for t in skip for x in ("--skip", t)], roots


def pre(run, proxify=False, only=(), skip=()):
    if not os.path.isdir(os.path.expanduser("~/.claude/projects")):
        sys.exit("No ~/.claude/projects here: Claude Code has not kept any session on this machine, so there is nothing to read.")
    # the projects the person named, checked before any slow step: a name the pick can't honour, a path that is no git
    # repo, more than 5, stop here with the reason and the names to give, not after a 3-minute inventory (11-17)
    asked, also = asked_args(session_candidates(only, skip), only, skip) if (only or skip) else ([], [])
    os.makedirs(run, exist_ok=True)
    worked = True   # did the last soft step work (the setup map and the setup read: no read when the map failed)
    for s in pre_steps(run, proxify, asked, also):
        if not s.get("soft"):
            step(s["title"], s["argv"])
        elif s.get("after_previous") and not worked:
            worked = False
        else:
            worked = soft_step(s["title"], s["argv"])
    reviewed = worked
    if not reviewed and not proxify:
        # no review could be asked for: don't start readers on questions whose view this page does not show
        for f in glob.glob(os.path.join(run, "anatomy-brief-*.md")):
            os.remove(f)
        print("   note: no project brief was written; the level will read 'could be' until the tool is run again", flush=True)
    for f in ("cheap-practices.md", "cheap-digest.md"):
        if not os.path.exists(os.path.join(run, f)):
            sys.exit(f"stopped: {f} was not written; the AI pass has nothing to read.")
    print(f"\nready for the AI pass: {run}/cheap-practices.md and {run}/cheap-digest.md\nit writes {run}/{AI_RESULT}")
    briefs = sorted(glob.glob(os.path.join(run, "anatomy-brief-*.md")))
    if briefs:
        print("and one reader per brief (each writes the anatomy-ai-<project>.json its brief names):\n  " + "\n  ".join(briefs))


def anatomy_scan_module():
    """anatomy_scan.py, loaded in this process: `candidates` and `pre --dry-run` rank projects with the functions the real
    pick calls. No bytecode is written (a dry run writes nothing, not even beside the code)."""
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, os.path.dirname(ANATOMY))
    import anatomy_scan
    return anatomy_scan


def wording_module():
    """v15/engine/wording.py: COMMAND (the slash command, from this plugin's name) and the sentence on changing the pick."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("wording", os.path.join(os.path.dirname(ANATOMY), "wording.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sessions_said(k):
    """"1 session", "2 sessions": a session count with its noun."""
    return f"{k} session" + ("" if k == 1 else "s")


def keys_said(c):
    """The projects and their keys, one line each, busiest first: what a person picks a name from after a bad one."""
    n = sessions_said
    return "\n".join(f"  {r['key']}  ({n(r['typed_sessions'])}" + (f", {r['path']})" if r["key"] != r["path"] else ")")
                     for r in c["projects"]) or "  (no git repo with sessions you typed in, in the last 30 days)"


def whose_pick(c):
    """Who chose the projects shown, in words: "chosen by you", or "your busiest 3" (2, 1: as many as there are)."""
    if c["picked_by"] == "confirmed":
        return wording_module().confirmed_said(len(c["pick"]))
    if c["picked_by"] == "you":
        return "chosen by you"
    k = len(c["default"])
    return "your busiest 3" if k == 3 else "your busiest project" if k == 1 else "no project with sessions" if not k else f"your {k} busiest"


def session_candidates(only=(), skip=(), typed=False):
    """The dict `candidates` prints (anatomy_scan.candidates): reads the session logs of the window, nothing else, with
    the same read and the same pick function as the anatomy step of `pre` (read_logs' worked-in links included), so the
    default it shows is the one the scan takes. `typed`: the names are as a person typed them (a unique start of a
    folder name is taken and said). A name --only/--skip can't honour stops with setup_choice's message, then the keys."""
    A = anatomy_scan_module()
    _, per, worked = A.read_logs(30)
    try:
        return A.candidates(per, 3, only, skip, worked=worked, typed=typed)
    except A.choice.Refused as e:
        print(f"stopped: {e}\n\nYour projects (name to give, sessions you typed in):\n"
              + keys_said(A.candidates(per, 3, worked=worked)), file=sys.stderr)
        sys.exit(2)


def show_candidates(only=(), skip=(), as_json=False):
    c = session_candidates(only, skip, typed=True)
    if as_json:
        print(json.dumps(c, indent=1))
        return
    rows = c["projects"]
    if not rows:
        print("No git repo with sessions you typed in during the last 30 days, so there is nothing to choose between."
              + (" " + c["may_fill"] if c["may_fill"] else ""))
        return
    word = whose_pick(c)
    print(f"Projects with sessions you typed in, last {c['window_days']} days, busiest first. * = reviewed ({word}).\n")
    w = min(40, max(len(r["key"]) for r in rows))   # a longer key runs past its column rather than widen every row
    print(f"   {'key (for --only / --skip)':<{max(w, 25)}}  sessions  last active  path")
    for r in rows:
        print(f"{' *' if r['in_pick'] else '  '} {r['key']:<{max(w, 25)}}  {r['typed_sessions']:>8}  {r['last_active'] or '-':<11}  {r['path']}")
    if c["not_repos"]:
        print("\nNot git repos, so never reviewed themselves (their sessions count for the repos they changed): "
              + ", ".join(f"{r['path']} ({r['typed_sessions']})" for r in c["not_repos"][:5])
              + (f" and {len(c['not_repos']) - 5} more" if len(c["not_repos"]) > 5 else "") + ".")
    for n in c["notes"] + ([c["may_fill"]] if c["may_fill"] else []):
        print(n)
    print("\n" + wording_module().change_pick_said() + "\n" + c["basis"])


def tokens(n_agents):
    """A rough token figure for the AI work: earlier real runs used about 60,000 to 80,000 tokens per agent."""
    return f"{n_agents * 60}k to {n_agents * 80}k"


def dry_run(run=None, proxify=False, only=(), skip=()):
    """What a real run would do, in plain words. Writes nothing (no run folder, no cache), starts no AI, runs no slow step:
    the only work is reading the session logs to rank the projects, as `candidates` does."""
    shown = run or would_be_run()
    page = os.path.join(os.path.dirname(shown), "my-setup.html")
    home = os.path.expanduser("~")
    t = lambda p: "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p  # noqa: E731
    out = ["DRY RUN: this wrote nothing and started no AI. A real run would do the following.", ""]
    out.append(f"Run folder: {t(shown)} ({'already there' if os.path.isdir(shown) else 'not made yet'}). Your page: {t(page)}.")
    if not os.path.isdir(os.path.join(home, ".claude", "projects")):
        out += ["", "A real run would stop at once: there is no ~/.claude/projects here, so Claude Code has not kept any session "
                "on this machine and there is nothing to read."]
        print("\n".join(out))
        return
    c = session_candidates(only, skip)
    rows = {r["key"]: r for r in c["projects"]}
    picked = [rows[k] for k in c["pick"]]
    left = [r for r in c["projects"] if not r["in_pick"] and r["typed_sessions"]]
    out += ["", "Projects the review would read " + f"({whose_pick(c)})" + ":"]
    out += [f"  {r['key']}: {sessions_said(r['typed_sessions'])} you typed in, last active {r['last_active'] or '-'}" for r in picked] \
        or ["  none: no sessions you typed in during the last 30 days"]
    if left:
        out.append("Left out, with sessions: " + ", ".join(f"{r['key']} ({r['typed_sessions']})" for r in left[:4])
                   + (f" and {len(left) - 4} more" if len(left) > 4 else "") + ".")
    out += ([c["may_fill"]] if c["may_fill"] and c["picked_by"] != "you" else []) \
        + [wording_module().change_pick_said(c["picked_by"] == "you"), c["basis"]] + c["notes"]
    out += ["", "Steps `pre` would run, in order. All read-only on your projects; every file goes into the run folder unless said:"]
    for s in pre_steps(shown, proxify, *asked_args(c, only, skip)):
        out += [f"  {s['title'].strip()}", f"      reads:  {s['reads']}", f"      writes: {s['writes']}"]
    n = len(picked)
    out += ["", f"Then the AI work the skill starts in this Claude Code session (sonnet): the practices pass (1 agent) and one reader per "
            f"project ({n}). That is {1 + n} agent{'s' if n else ''}; roughly {tokens(1 + n)} tokens in all, an estimate from earlier runs (60k to 80k "
            "per agent), not a measurement. Then `post` builds the page from their answers, on this machine.",
            "", "Nothing leaves your laptop in any of this. The page's Share tab sends Proxify a summary of practice ids and "
            "levels only when you press Send there."]
    print("\n".join(out))


def check_rows(path):
    """The AI pass's file must be {"rows": [...]} in the workflow's schema; say what is wrong while the subagent can still be re-run."""
    if not os.path.exists(path):
        sys.exit(f"{path} is missing: the AI pass did not write its rows there. Run the pass again.")
    try:
        data = json.load(open(path))
    except ValueError as e:
        sys.exit(f"{path} is not valid JSON ({e}). Run the AI pass again, asking for the JSON file only.")
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list) or not data["rows"]:
        sys.exit(f'{path} must be {{"rows": [ ...one row per practice... ]}}. Run the AI pass again.')
    bad = []
    for i, r in enumerate(data["rows"]):
        if not isinstance(r, dict) or any(k not in r for k in ROW_KEYS):
            bad.append(f"row {i}: missing {[k for k in ROW_KEYS if k not in (r if isinstance(r, dict) else {})]}")
        elif r["state"] not in STATES or r["rung"] not in RUNGS or r["could_be"] not in RUNGS - {"L1"}:
            bad.append(f"row {i} ({r['id']}): state/rung/could_be outside the allowed values")
    if bad:
        sys.exit(f"{path} does not match the schema:\n  " + "\n  ".join(bad[:8]) + "\nRun the AI pass again.")
    print(f"{len(data['rows'])} rows from the AI pass")


def post(run, out, open_page, proxify=False, level=True):
    ai = os.path.join(run, AI_RESULT)
    check_rows(ai)
    # always recount (under a second, code only): the scan or the signal map may be newer than the last signal-rows.json
    step("who-did-it counts -> rungs (code, by the substrate's signal map)", ["-I", os.path.join(CHEAP, "signals.py"), run])
    step("assemble: the AI pass's rows + the code's problems -> evidence.json", ["-I", os.path.join(CHEAP, "assemble.py"), run, ai])
    soft_step("setup read: the readers' reviews + the map -> setup-read.json (what the level and the first tab read)",
              ["-I", os.path.join(SETUP_READ, "setup_read.py"), "merge", run])
    step("engine: rungs, pointers, recommendations -> v15-result.json", ["-I", ENGINE, run, os.path.join(run, "v15-result.json")])
    briefs, answers = glob.glob(os.path.join(run, "anatomy-brief-*.md")), glob.glob(os.path.join(run, "anatomy-ai-*.json"))
    if briefs:
        print(f"anatomy: {len(answers)} of {len(briefs)} project reads answered"
              + ("" if len(answers) >= len(briefs) else " (the rest show as couldn't check; with no review at all the level reads 'could be')"))
    step(f"page -> {out}", ["-I", FRAME, run, out] + ([] if proxify else ["--member"] + ([] if level else ["--no-level"])))
    print(f"\nyour page: {out}")
    if open_page:
        try:
            webbrowser.open_new_tab(pathlib.Path(out).as_uri())
        except Exception:  # noqa: BLE001 - the path above is enough
            pass


def share(run, out):
    """share-with-proxify.json: the summary the page's "Share with Proxify" tab sends, as a file, for anyone who would rather
    send it by hand. What it may hold and why: v15/engine/share_payload.py."""
    sys.path.insert(0, os.path.join(HERE, "v15", "engine"))
    import share_payload
    try:
        d = share_payload.payload(run)
    except share_payload.Unsafe as e:
        sys.exit(f"stopped: {e}; nothing was written.")
    open(out, "w").write(json.dumps(d, indent=1) + "\n")
    print(f"share file: {out} ({len(json.dumps(d))} characters; open it to see every value before sending)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["newrun", "pre", "post", "share", "candidates"])
    ap.add_argument("run_dir", nargs="?", help="not for newrun")
    ap.add_argument("--out", help="the page (default: my-setup.html beside RUN_DIR)")
    ap.add_argument("--no-open", action="store_true", help="post: do not open the page in the browser")
    ap.add_argument("--proxify", action="store_true",
                    help="Proxify's own run. pre: the readers' briefs keep the part-by-part questions; "
                         "post: the full page for Proxify staff (internal views included)")
    ap.add_argument("--no-level", action="store_true", help="post: the member's copy without the Your level tab")
    ap.add_argument("--only", action="append", default=[], metavar="NAME",
                    help="pre: review this project instead of the busiest ones (repeat for more, at most 5; a folder name or a path)")
    ap.add_argument("--skip", action="append", default=[], metavar="NAME",
                    help="pre: leave this project out of the review (repeat for more; a folder name or a path)")
    ap.add_argument("--dry-run", action="store_true", help="pre: say what a real run would do and write nothing (RUN_DIR optional)")
    ap.add_argument("--json", action="store_true", help="candidates: print what the skill reads")
    ap.add_argument("--member", action="store_true", help=argparse.SUPPRESS)  # old name of --no-level
    a = ap.parse_args()
    if a.phase == "newrun":
        print(newrun())
        return
    if a.phase == "candidates":
        with contextlib.redirect_stdout(sys.stderr if a.json else sys.stdout):   # stdout stays pure JSON
            with_pyyaml()
        show_candidates(a.only, a.skip, a.json)
        return
    if a.phase == "pre" and a.dry_run:
        with_pyyaml()
        dry_run(os.path.abspath(os.path.expanduser(a.run_dir)) if a.run_dir else None,
                a.proxify or os.environ.get("AGENTIC_SETUP_PROXIFY") == "1", a.only, a.skip)
        return
    if not a.run_dir:
        ap.error(f"{a.phase} needs RUN_DIR (python3 run.py newrun makes one)")
    run = os.path.abspath(os.path.expanduser(a.run_dir))
    with_pyyaml()
    proxify = a.proxify or os.environ.get("AGENTIC_SETUP_PROXIFY") == "1"
    if a.phase == "pre":
        pre(run, proxify=proxify, only=a.only, skip=a.skip)
    elif a.phase == "share":
        share(run, os.path.abspath(os.path.expanduser(a.out)) if a.out else os.path.join(os.path.dirname(run), "share-with-proxify.json"))
    else:
        post(run, os.path.abspath(os.path.expanduser(a.out)) if a.out else os.path.join(os.path.dirname(run), "my-setup.html"),
             not a.no_open, proxify=proxify,
             level=not (a.no_level or a.member or "1" in (os.environ.get("AGENTIC_SETUP_NO_LEVEL"), os.environ.get("AGENTIC_SETUP_MEMBER"))))


if __name__ == "__main__":
    main()
