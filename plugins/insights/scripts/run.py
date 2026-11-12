#!/usr/bin/env python3
"""The two halves of the setup tool around the one AI pass. Read-only on the person's machine: it reads
~/.claude and the git repos Claude Code ran in, and writes only into RUN_DIR (and the page beside it;
frame.py keeps the page it replaces in archive/ there).

    python3 run.py pre  RUN_DIR   inventory -> commit probe -> problems, artifacts, practices packet
    python3 run.py post RUN_DIR   check the AI pass's rows -> assemble -> engine -> page   (--no-open: don't open the browser)
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


def pre(run, proxify=False):
    os.makedirs(run, exist_ok=True)
    if not os.path.isdir(os.path.expanduser("~/.claude/projects")):
        sys.exit("No ~/.claude/projects here: Claude Code has not kept any session on this machine, so there is nothing to read.")
    step("1/3 inventory: what is on this machine and what the sessions did (the slow step)",
         ["-I", os.path.join(EXP, "2026-11-02-evidence", "inventory.py"), "--out", run])
    step("2/3 commit check: were the agent's commits looked at before they landed",
         ["-I", os.path.join(EXP, "2026-11-02-problems", "detectors", "comprehension_debt.py"),
          "--chats", os.path.join(run, "chats.jsonl"), "--out", os.path.join(run, "comprehension-debt.json")])
    step("3/3 problems (code only), what you built, and the brief for the AI pass",
         ["-I", os.path.join(CHEAP, "problems.py"), run, "--commits", os.path.join(run, "comprehension-debt.json")])
    step("   artifacts", ["-I", os.path.join(CHEAP, "artifacts.py"), run])
    step("   practices packet", ["-I", os.path.join(CHEAP, "practices_packet.py"), run])
    step("   who-did-it counts -> rungs (code, by the substrate's signal map)", ["-I", os.path.join(CHEAP, "signals.py"), run])
    step("   portable checks: counts that read the same on any laptop (each with a time budget)",
         ["-I", os.path.join(EXP, "2026-11-05-portable", "run_portable.py"), run])
    step("   anatomy: each discipline's parts — its own code checks, and one brief per most active project", ["-I", ANATOMY, run])
    # A member's run: each project's brief holds only the review of its agent-facing files (the member's page has no
    # part-by-part view, and its questions were most of what a reader spent). Proxify's own run keeps the questions.
    reviewed = soft_step("   setup map: the outline of your rule files, memory, skills and agents, and what points to what",
                         ["-I", os.path.join(SETUP_READ, "setup_map.py"), run]) and \
        soft_step("   setup read: the review of those files, " + ("added to each project's brief" if proxify else "one brief per project"),
                  ["-I", os.path.join(SETUP_READ, "setup_read.py"), "brief", run] + (["--proxify"] if proxify else []))
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
    ap.add_argument("phase", choices=["pre", "post", "share"])
    ap.add_argument("run_dir")
    ap.add_argument("--out", help="the page (default: my-setup.html beside RUN_DIR)")
    ap.add_argument("--no-open", action="store_true", help="post: do not open the page in the browser")
    ap.add_argument("--proxify", action="store_true",
                    help="Proxify's own run. pre: the readers' briefs keep the part-by-part questions; "
                         "post: the full page for Proxify staff (internal views included)")
    ap.add_argument("--no-level", action="store_true", help="post: the member's copy without the Your level tab")
    ap.add_argument("--member", action="store_true", help=argparse.SUPPRESS)  # old name of --no-level
    a = ap.parse_args()
    run = os.path.abspath(os.path.expanduser(a.run_dir))
    with_pyyaml()
    proxify = a.proxify or os.environ.get("AGENTIC_SETUP_PROXIFY") == "1"
    if a.phase == "pre":
        pre(run, proxify=proxify)
    elif a.phase == "share":
        share(run, os.path.abspath(os.path.expanduser(a.out)) if a.out else os.path.join(os.path.dirname(run), "share-with-proxify.json"))
    else:
        post(run, os.path.abspath(os.path.expanduser(a.out)) if a.out else os.path.join(os.path.dirname(run), "my-setup.html"),
             not a.no_open, proxify=proxify,
             level=not (a.no_level or a.member or "1" in (os.environ.get("AGENTIC_SETUP_NO_LEVEL"), os.environ.get("AGENTIC_SETUP_MEMBER"))))


if __name__ == "__main__":
    main()
