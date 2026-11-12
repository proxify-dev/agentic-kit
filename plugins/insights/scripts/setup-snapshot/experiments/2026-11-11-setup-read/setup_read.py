#!/usr/bin/env python3
"""The setup read: the step between the structure map (setup_map.py) and the engine. Why: README.md in this folder.

    python3 setup_read.py brief RUN_DIR   before the AI pass: one brief per project, anatomy-brief-<slug>.md, holding
                                          "## The setup read" (the project's outline, what to judge, how much to open,
                                          the answer shape). A member's run: the brief holds only that. Proxify's own
                                          run (--proxify, or AGENTIC_SETUP_PROXIFY=1): it is added to the anatomy
                                          brief, so the reader that answers the part-by-part questions also reviews
    python3 setup_read.py merge RUN_DIR   after it: the readers' answers + the map + the inventory -> setup-read.json

setup-read.json is what `engine.setup_read_gate` and the page read:
  parts     rules · corrections · memory. A part counts toward L3 only when `structure` (code, from the map and git),
            `judged == "shown"` (the reader) and `quote_ok` (code found the reader's quote in the person's own file)
            all hold FOR THE SAME PROJECT. Per part the best project is reported; two projects are never mixed.
            A quote from a line that holds a secret is dropped (`quote: null`, `quote_dropped`) and is never ok;
            every string a reader wrote is scrubbed before it is written (setup_map.scrub).
  holds     the opposite habit, counted from Claude Code's own records.
  cards     advice counted from those records, each {id, saw, cost, do, source: "records"}: what the page says to
            change first that no file shows (how long sessions run, how full they start, setup nobody uses, rule
            files that only grow). Advice only: no card moves a level. A card the page draws a picture of carries
            its numbers in `figures`.
  projects  the review the page opens on (tagline, headline, summary, what works, what to change) with the project's
            outline, its verdicts per part, and per part what a verdict rests on (`read`: why, quote, next step).
Standard library only; read-only on the person's files; a missing or damaged input reads as "not there", never a crash.
"""
import glob
import json
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import setup_map  # noqa: E402  the outline drawn as a text tree
ENGINE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "v15", "engine"))   # scripts/ keeps labs' layout
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)
import things as level_things  # noqa: E402  the level read: its counts, and the one rule for "a part is shown"
from wording import plural  # noqa: E402  the counts and plurals every tab uses, worded once

# Every number below is a guess until hand-checked on 3-4 member laptops (labs/setup-snapshot/CLAUDE.md, thresholds).
RULES_ON_DEMAND_MIN = 2      # rules: docs behind pointers from the rule files, when no rule file is scoped
CORRECTION_WEEKS_MIN = 3     # corrections: different weeks one rule file or skill of theirs changed in
MEMORY_NOTES_MIN = 5         # memory: notes in a store with an index or links between notes
MEMORY_SESSIONS_MIN = 3      # memory: sessions that read the store, when the inventory counted them
HOLD_AUTO_COMPACTIONS = 5    # endless sessions: automatic compactions in the window
HOLD_SESSIONS_TWICE = 3      # endless sessions: sessions compacted more than once
QUOTE_MIN_CHARS = 20         # a shorter quote matches by chance
QUOTE_SPAN = 8               # lines one quote may run over, when the reader joined a wrapped sentence
# cards (advice, never a level): the same rule, each bar a guess until hand-checked on member laptops
SCOPE_HOURS = 24             # session scope: a session open this long, first message to last
SCOPE_COMPACTIONS = 2        # session scope: ... that was compacted at least this often
SCOPE_SESSIONS = 2           # session scope: how many such sessions make it a habit
SCOPE_SHARE = 0.05           # session scope: ... and their share of the typed sessions (10 of 588 is no habit)
START_TOKENS = 40_000        # starting context: median tokens in context at a session's first reply
START_SESSIONS = 5           # starting context: sessions measured before a median means anything
START_RULES_SHARE = 0.3      # starting context: from this share up, their own rule files are the part to cut
UNUSED_INSTALLED_MIN = 10    # unused setup: skills, commands and plugins from others that are set up
UNUSED_CALLS_PER_ITEM = 0.25 # unused setup: ... called less often than this, per thing, in the window
UNUSED_OWN_MIN = 2           # unused setup: skills of their own never loaded in the window
RULES_GROW_WORDS = 1000      # rules only grow: words now in an always-loaded rule file of theirs
RULES_GROW_CHANGES = 3       # rules only grow: commits to it, none of which made it shorter
MAX_CARDS = 4
SHAPED = ("indexed", "layered", "linked")

GIT_TIMEOUT, GIT_FILES, GIT_BUDGET = 4, 12, 20   # seconds per file, files per project, seconds per project
BRIEF_TREE_WORDS, PAGE_TREE_WORDS = 1200, 900
MAX_QUOTE_FILE = 2_000_000
MARK = "## The setup read"
PARTS = (("rules", "Rules that reach the agent"), ("corrections", "Corrections that stick"),
         ("memory", "Memory across sessions"))
JUDGED = ("shown", "partly", "not shown", "can't tell")

READER = """{mark}

{intro}Review this project's agent setup the way a senior engineer reviews a colleague's: what the agent knows when a
session starts here, how that knowledge is organised, and whether it would hold as the work grows.

The outline at the end was drawn by code. It shows structure and judges nothing. Read-only. Open what you need to
judge the three parts below and no more than this: the always-loaded files; up to {scoped} scoped files; up to {docs}
docs behind pointers (the ones a rule file sends the agent to most plainly); the memory index and {notes} notes; the
SKILL.md of up to {skills} skills. `git log` on at most {logs} files, the main rule file first. When the outline lists
nothing for a part, judge it from the outline and open nothing.

Judge three parts, each `shown`, `partly`, `not shown` or `can't tell`:

1. `rules`: the agent finds its rules without being told.
   - shown: the always-loaded files hold facts the agent can't work out from the code, each short and with its
     reason; rules for one area sit in that area (a subfolder file, a path-scoped rule), not in one file for
     everything; deeper detail sits behind pointers that say when to open it, or in skills that load when the task
     matches.
   - partly: one decent file with no layering, or layers that are a dump of docs.
   - not shown: generic or generated text nobody shaped, or nothing.
2. `corrections`: a correction sticks.
   - shown: lines that are plainly corrections made to last (a specific mistake named, with the fix or the reason),
     and the file's history shows lines added and removed over weeks, not written in one go.
   - partly: such lines exist, but the file only grows or all of it arrived at once.
   - not shown: no such line.
3. `memory`: a fresh session knows what happened before.
   - shown: notes with a shape someone designed (an index, notes by topic or type, links between them), kept
     current, pruned or merged.
   - partly: notes exist, but as a log that only grows, or as files nothing links.
   - not shown: only Claude Code's built-in memory left as it writes it, or nothing.

For each part:
- `why`: one sentence a non-engineer follows, naming the file.
- `quote`: `path`, `line` (where the sentence starts) and `text`: the whole sentence, word for word, that best shows it,
  at most 300 characters. Files are hard-wrapped, so a sentence may run over several lines: copy all of it, joined
  with single spaces, and never stop at the end of a line. Required for shown and partly. Code looks the quote up in
  the file: a quote it can't find does not count. Never pick a line that holds a key, a token or a password, and never
  copy one into anything you write: code drops such a quote.
- `complexity`: one sentence on how it is organised and how many layers deep.
- `next`: the one step that moves this part up: the file, and what goes in it.

Then the review the page opens on, written to the person as "you":
- `tagline`: at most 7 plain words saying what this project is, for a reader who isn't an engineer (no file
  names, no tool names), e.g. "Video studio run by agents".
- `headline`: one sentence, what kind of setup this is.
- `summary`: 2-3 sentences, what the agent starts with here and how it is organised.
- `works`: up to 3 things that work: `what`, `why` it matters, `quote`.
- `change`: up to 3 things to change first: `saw` (with its number, or the line you read), `cost` (what it costs
  them), `do` (the one concrete step, naming the file).

Judge the content, not that a file exists. A long file is not a deep one. Text an agent generated and nobody shaped
is not the person's design. Never write the person's name.

Add one key, `setup_read`, to the JSON object you write to {out}:

    "setup_read": {{
      "parts": [{{"id": "rules", "judged": "shown|partly|not shown|can't tell", "why": "...",
                 "quote": {{"path": "...", "line": 0, "text": "..."}}, "complexity": "...", "next": "..."}},
                {{"id": "corrections", ...}}, {{"id": "memory", ...}}],
      "tagline": "...", "headline": "...", "summary": "...",
      "works": [{{"what": "...", "why": "...", "quote": {{"path": "...", "line": 0, "text": "..."}}}}],
      "change": [{{"saw": "...", "cost": "...", "do": "..."}}]
    }}

### The outline (`w` = words; `→` = a pointer to another file)

{tree}
"""

BARE = """# Setup read: {name}

Read-only. Review this project only: {root}

Write the result with the Write tool to {out} as one JSON object:
{{"project": "{root}", "answers": [], "setup_read": {{...}}}}

"""
SECOND_JOB = "A second job, in the same answer file. "
# How much a reader opens (11-11: three readers spent 112k to 137k tokens each, against about 200k for the whole AI
# part of a member's run). Guesses until the readings of a few member laptops are compared with a fuller read.
OPEN_SCOPED, OPEN_DOCS, OPEN_NOTES, OPEN_SKILLS, OPEN_GIT_LOGS = 3, 5, 3, 3, 2


def load(run, name):
    try:
        with open(os.path.join(run, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def slug(root):
    """The same slug anatomy_scan.py gives a project, so the brief and the answer file are found by name."""
    return re.sub(r"[^a-z0-9]+", "-", os.path.basename(root.rstrip("/")).lower()).strip("-") or "root"


def expand(path, home):
    return os.path.join(home, path[2:]) if path.startswith("~/") else (home if path == "~" else path)


def absolute(display, root, home):
    """A path as the map shows it (relative to its project, or under ~) back to a path on disk."""
    if display.startswith("~"):
        return os.path.normpath(expand(display, home))
    return os.path.normpath(display if os.path.isabs(display) else os.path.join(root, display))


def tree(m, project, budget, with_user=True):
    """One project's outline as a text tree, cut to a word budget (setup_map's own levels of detail)."""
    one = {"projects": [project], "user": m.get("user") or {"root": "~/.claude", "error": "not read"}}
    text = ""
    for cap in setup_map.LEVELS:
        text = setup_map.render(one, cap)
        i = text.find("\n## ")
        text = text[i + 1:] if i >= 0 else text       # drop the map's own title and legend
        if not with_user:
            j = text.find("\n## User level:")
            text = text[:j] if j >= 0 else text
        if len(text.split()) <= budget:
            break
    return text.strip() + "\n"


def projects_of(run, m, home):
    """[(slug, absolute root, map entry)] in the map's order (most typed sessions first). The slug is the one the
    anatomy scan wrote, when it ran."""
    scan = {os.path.normpath(p.get("root") or ""): p.get("slug") for p in (load(run, "anatomy-scan.json") or {}).get("projects") or []}
    out, seen = [], set()
    for p in (m or {}).get("projects") or []:
        root = os.path.normpath(expand(p.get("root") or "", home))
        s = scan.get(root) or slug(root)
        if s in seen or "instruction_files" not in p:
            continue
        seen.add(s)
        out.append((s, root, p))
    return out


# ------------------------------------------------------------------ brief

def proxify_run(flag=None):
    """Proxify's own run (run.py --proxify, or AGENTIC_SETUP_PROXIFY=1), whose page keeps the part-by-part views."""
    return os.environ.get("AGENTIC_SETUP_PROXIFY") == "1" if flag is None else bool(flag)


def brief(run, home=None, proxify=None):
    """Proxify's own run: the section is added to the anatomy brief, whose questions feed the page's part-by-part views.
    A member's run: the brief holds ONLY this review. The member's page has no part-by-part view (11-11), and its 19 to
    28 questions were most of what a reader spent; a brief of a project the map does not hold is removed, so no reader
    is started for a view nobody sees."""
    home = home or os.path.expanduser("~")
    proxify = proxify_run(proxify)
    m = load(run, "setup-map.json")
    if not m:
        print("setup read: no setup-map.json in the run folder; the briefs are unchanged")
        return 0
    n, added, kept = 0, 0, set()
    for s, root, p in projects_of(run, m, home):
        path = os.path.join(run, f"anatomy-brief-{s}.md")
        out = os.path.join(run, f"anatomy-ai-{s}.json")
        text = None
        if proxify:
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                pass   # the anatomy step did not run (no PyYAML): the read stands alone
        second = text is not None
        if text is None:
            text = BARE.format(name=s, root=root, out=out)
        i = text.find(MARK)
        text = text[:i].rstrip() + "\n\n" if i >= 0 else text.rstrip() + "\n\n"   # a second run replaces the section
        # indented, so the outline's own headings are not read as headings of the brief
        drawn = "\n".join("    " + line if line else "" for line in tree(m, p, BRIEF_TREE_WORDS).split("\n"))
        section = READER.format(mark=MARK, out=out, tree=drawn, intro=SECOND_JOB if second else "", scoped=OPEN_SCOPED,
                                docs=OPEN_DOCS, notes=OPEN_NOTES, skills=OPEN_SKILLS, logs=OPEN_GIT_LOGS)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + section)
        kept.add(os.path.normpath(path))
        n, added = n + 1, added + len((text + section).split())
    dropped = 0
    if not proxify:
        for path in glob.glob(os.path.join(run, "anatomy-brief-*.md")):
            if os.path.normpath(path) not in kept:
                os.remove(path)
                dropped += 1
    print(f"setup read: {n} project brief{'s' if n != 1 else ''}"
          + (", each with the part-by-part questions and the review" if proxify else ", each holding the review only")
          + (f" (about {added // n} words each)" if n else "")
          + (f"; {dropped} brief{'s' if dropped != 1 else ''} of projects with no outline removed" if dropped else ""))
    return n


# ------------------------------------------------------------------ structure (code)

def rules_structure(p):
    t = p.get("totals") or {}
    a, s, d = t.get("always_files", 0), t.get("scoped_files", 0), t.get("on_demand_files", 0)
    said = (f"{a} always-loaded file{'s' if a != 1 else ''}, {s} scoped, {d} doc{'s' if d != 1 else ''} behind pointers"
            + (f" ({t.get('max_depth', 0)} layer{'s' if t.get('max_depth', 0) != 1 else ''} deep)" if d else ""))
    if not a:
        return False, "no always-loaded rule file of theirs in this project (" + said + ")"
    ok = s >= 1 or d >= RULES_ON_DEMAND_MIN
    return ok, said if ok else said + ": one layer, nothing scoped to a folder and too little behind pointers"


def history(path):
    """Weeks a file changed in and lines removed over its history, from git. None when git has no history of it."""
    try:
        r = subprocess.run(["git", "-C", os.path.dirname(path), "log", "--follow", "--numstat", "--format=@%ad",
                            "--date=format:%G-%V", "--", os.path.basename(path)],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    weeks, removed = set(), 0
    for line in r.stdout.splitlines():
        if line.startswith("@"):
            weeks.add(line[1:].strip())
        else:
            cols = line.split("\t")
            if len(cols) >= 2 and cols[1].isdigit():
                removed += int(cols[1])
    return {"weeks": len(weeks), "removed": removed}


def correction_files(p, user, root, home):
    """Rule files and skills of theirs that load in this project: the project's, then the user level's."""
    out = []
    for place, base in ((p, root), (user or {}, os.path.join(home, ".claude"))):
        for e in place.get("instruction_files") or []:
            if e.get("loads") in ("always", "scoped") and "error" not in e:
                out.append(absolute(e["path"], base, home))
        for e in place.get("skills") or []:
            if "error" not in e:
                out.append(absolute(e["path"], base, home))
    return list(dict.fromkeys(out))


def corrections_structure(p, user, root, home):
    files = correction_files(p, user, root, home)
    if not files:
        return False, "no rule file or skill of theirs here"
    best, in_git, t0 = None, 0, time.time()
    for f in files[:GIT_FILES]:
        if time.time() - t0 > GIT_BUDGET:
            break
        h = history(f)
        if not h:
            continue
        in_git += 1
        key = (h["weeks"] >= CORRECTION_WEEKS_MIN and h["removed"] > 0, h["weeks"], h["removed"])
        if best is None or key > best[0]:
            best = (key, f, h)
    if best is None:
        return False, "their rule files and skills are not in git, so their history can't be read"
    _, f, h = best
    name = os.path.relpath(f, root) if f.startswith(root + os.sep) else "~/" + os.path.relpath(f, home)
    said = (f"{name} changed in {h['weeks']} different week{'s' if h['weeks'] != 1 else ''}, "
            + (f"{h['removed']} lines removed along the way" if h["removed"] else "and no line was ever removed"))
    return best[0][0], said


def memory_structure(p, user, root, home, reads):
    stores = [(s, root) for s in p.get("memory") or []] + [(s, os.path.join(home, ".claude")) for s in (user or {}).get("memory") or []]
    stores = [(s, base) for s, base in stores if "error" not in s]
    if not stores:
        return False, "no notes folder found for this project"
    # Claude Code's built-in memory folder never shows a design by its shape: Claude Code itself writes the index and
    # the typed notes there, for everyone. 11-11: a person's built-in folders (24 files, read in 27 sessions) read
    # as her notes. Whether the person shaped it is the reader's call, and it then needs an own store to count.
    own = [(s, base) for s, base in stores if s.get("kind") != "builtin"]
    if not own:
        return False, "only Claude Code's built-in memory folder, in the shape Claude Code writes by default"
    scored = []
    for s, base in own:
        n = reads.get(absolute(s["path"], base, home))
        ok = s.get("shape") in SHAPED and s.get("files", 0) >= MEMORY_NOTES_MIN and (n is None or n >= MEMORY_SESSIONS_MIN)
        scored.append(((ok, s.get("shape") in SHAPED, s.get("files", 0)), s, n))
    key, s, n = max(scored, key=lambda x: x[0])
    shape = {"indexed": "an index and notes", "layered": "an index, with sub-folders or typed notes",
             "linked": "notes that link each other, with no index", "flat-log": "a log with no index and no links",
             "single-file": "a single file"}.get(s.get("shape"), s.get("shape") or "unknown shape")
    said = f"{s['path']}: {shape}, {s.get('files', 0)} file{'s' if s.get('files', 0) != 1 else ''}"
    if n is not None:
        said += f", read in {n} session{'s' if n != 1 else ''}"
    return key[0], said


# ------------------------------------------------------------------ the reader's quote, checked by code

def squash(text):
    return " ".join(str(text).split())


def allowed(p, user, root, home):
    """Where a quote may come from: (files, folders, docs_root). Files and folders are the person's own in this
    project's map and the user level's (memory stores and skill folders count whole). docs_root is the project root
    when the map reached more docs than it lists: any doc below it may then be one of them, and only a doc."""
    files, dirs = set(), set()
    for place, base in ((p, root), (user or {}, os.path.join(home, ".claude"))):
        for k in ("instruction_files", "agents", "commands"):
            for e in place.get(k) or []:
                files.add(absolute(e["path"], base, home))
        for s in place.get("memory") or []:
            a = absolute(s["path"], base, home)
            (dirs if os.path.isdir(a) else files).add(a)
        for s in place.get("skills") or []:
            a = absolute(s["path"], base, home)
            dirs.add(a if os.path.isdir(a) else os.path.dirname(a))
    return files, dirs, (root if p.get("not_listed") else None)


def below(path, folder):
    return (os.path.realpath(path) + os.sep).startswith(os.path.realpath(folder) + os.sep)


def lines_holding(text, want):
    """The lines of a file that a quote sits on: every line that holds it whole, or, for a quote the reader joined
    from several lines, the shortest run of lines (up to QUOTE_SPAN) that does."""
    lines = [squash(x) for x in text.split("\n")]
    one = [x for x in lines if want in x]
    if one:
        return one
    for i in range(len(lines)):
        acc = ""
        for j in range(i, min(i + QUOTE_SPAN, len(lines))):
            acc = (acc + " " + lines[j]).strip()
            if want in acc:
                while i < j and want in " ".join(lines[i + 1:j + 1]).strip():
                    i += 1
                return lines[i:j + 1]
    return []


def quote_state(q, where, root, home):
    """(ok, secret). ok: the reader's quote is really there: the file is one the map holds as the person's own, and
    the quoted text (whitespace squashed, at least QUOTE_MIN_CHARS) occurs in it on disk. secret: the quote, or a
    line of the file it sits on, holds something secret-looking (setup_map.has_secret). Such a quote is never ok and
    is never written anywhere, masked or not: the first real run (11-11) found session tokens in plain text in an
    agent-facing file, and a quote that is verified is a quote that is printed."""
    if not isinstance(q, dict) or not q.get("path") or not q.get("text"):
        return False, False
    if setup_map.has_secret(q["text"]) or setup_map.has_secret(q["path"]):
        return False, True
    want = squash(q["text"]).strip("`\"'“”")
    if len(want) < QUOTE_MIN_CHARS:
        return False, False
    files, dirs, docs_root = where
    real_files = {os.path.realpath(f) for f in files}
    # the reader may give the path as the outline shows it: relative to the project, or to ~/.claude
    for cand in dict.fromkeys((absolute(str(q["path"]), root, home), absolute(str(q["path"]), os.path.join(home, ".claude"), home))):
        theirs = (os.path.realpath(cand) in real_files or any(below(cand, d) for d in dirs)
                  or bool(docs_root and cand.endswith(setup_map.DOC_EXT) and below(cand, docs_root)))
        if not theirs or not os.path.isfile(cand) or os.path.getsize(cand) > MAX_QUOTE_FILE:
            continue
        try:
            with open(cand, encoding="utf-8", errors="replace") as f:
                body = f.read()
        except OSError:
            continue
        if want in squash(body):
            if any(setup_map.has_secret(x) for x in lines_holding(body, want)):
                return False, True
            return True, False
    return False, False


def quote_ok(q, where, root, home):
    return quote_state(q, where, root, home)[0]


SECRET_LINE = "a secret was on that line, so the quote is not kept"


def quoted(q, where, root, home):
    """What merge writes for one quote: {"quote_ok", "quote"}, plus "quote_dropped" (the reason) when the line held
    a secret: then the quote is null and cannot make its part count."""
    ok, secret = quote_state(q, where, root, home)
    if secret:
        return {"quote_ok": False, "quote": None, "quote_dropped": SECRET_LINE}
    return {"quote_ok": ok, "quote": clean_quote(q)}


# ------------------------------------------------------------------ the opposite habit, from Claude Code's records

def hold_endless(inv):
    c = (inv or {}).get("compactions")
    if not isinstance(c, dict):
        return {"id": "endless-sessions", "fired": False, "why": "compactions were not counted on this laptop"}
    by = c.get("by_trigger") or {}
    auto, twice = by.get("auto", 0), c.get("chats_compacted_more_than_once", 0)
    typed = {x.get("command"): x.get("times", 0) for x in ((inv.get("slash_commands") or {}).get("by_command") or [])}
    by_hand = by.get("manual", 0) + c.get("typed_compact_commands", 0) + typed.get("/compact", 0) + typed.get("/clear", 0)
    # any hook on compaction, theirs or installed: a hold lowers a level, so it fires only when nothing at all
    # carries state across a compaction
    hooked = any((h.get("event") in ("PreCompact", "PostCompact")) or
                 (h.get("event") == "SessionStart" and "compact" in str(h.get("matcher") or ""))
                 for h in inv.get("hooks_configured") or [])
    fired = auto >= HOLD_AUTO_COMPACTIONS and twice >= HOLD_SESSIONS_TWICE and not by_hand and not hooked
    if fired:
        why = (f"{plural(auto, 'compaction')}, all automatic, and {plural(twice, 'session')} compacted more than once; you never typed "
               f"/compact or /clear: sessions run until Claude Code compacts them on its own")
    elif not auto:
        why = "no automatic compactions in the logs read"
    else:
        why = f"{plural(auto, 'automatic compaction')}, {plural(twice, 'session')} compacted more than once" + (
            "; you also reset or compacted by hand" if by_hand else "") + ("; a hook runs on compaction" if hooked else "")
    return {"id": "endless-sessions", "fired": fired, "why": why}


# ------------------------------------------------------------------ cards: advice from Claude Code's own records

SCOPE_COST = ("After a compaction the agent works from a summary of the session. What you decided and corrected early "
              "on is the first thing a summary drops, so you end up explaining it again.")
SCOPE_DO = ("Give each task its own session: type /clear when a task is done. When a task has to carry on, ask the agent "
            "to write what the next session needs into a file, then open that file in the new session.")


def chats_of(run):
    """The typed sessions the inventory wrote (chats.jsonl): one row per session, the first line is its meta."""
    rows = []
    try:
        with open(os.path.join(run, "chats.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and "chat" in r:
                    rows.append(r)
    except OSError:
        pass
    return rows


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def span_hours(r):
    """First message to last, in hours: how long the session stayed open, not how long the agent worked."""
    ts = [t for t in (_num(r.get("t0")), _num(r.get("t1"))) if t is not None]
    if len(ts) < 2:
        ts = [t for k in ("msgs", "calls") for x in r.get(k) or [] if isinstance(x, dict)
              for t in [_num(x.get("t"))] if t is not None]
    return (max(ts) - min(ts)) / 3600 if len(ts) >= 2 else 0.0


def first_context(r):
    """Tokens in context at the session's first reply (the same read as the page's "What a session starts with")."""
    pts = sorted((t, c) for k in ("calls", "msgs") for x in r.get(k) or [] if isinstance(x, dict)
                 for t, c in [(_num(x.get("t")), _num(x.get("ctx")))] if t is not None and c)
    return pts[0][1] if pts else None


def tokens(n):
    return f"{n / 1000:.1f}k" if n < 9950 else (f"{n / 1000:.0f}k" if n < 999_500 else f"{n / 1e6:.1f}M")


def tilde(path, home):
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def long_sessions(chats):
    """Sessions that stayed open past SCOPE_HOURS and were compacted SCOPE_COMPACTIONS times or more, longest first."""
    out = [r for r in chats if span_hours(r) >= SCOPE_HOURS and len(r.get("compactions") or []) >= SCOPE_COMPACTIONS]
    return sorted(out, key=span_hours, reverse=True)


def longest_said(r):
    n = len(r.get("compactions") or [])
    return (f"the longest stayed open {plural(round(span_hours(r)), 'hour')}, with {plural(len(r.get('calls') or []), 'tool call')} and "
            f"{plural(n, 'compaction')}")


def card_session_scope(chats):
    long = long_sessions(chats)
    if len(long) < SCOPE_SESSIONS or len(long) < SCOPE_SHARE * len(chats):
        return None
    return {"id": "session-scope", "source": "records", "cost": SCOPE_COST, "do": SCOPE_DO,
            "saw": f"{len(long)} of your {plural(len(chats), 'session')} stayed open for more than a day and were compacted "
                   f"{SCOPE_COMPACTIONS} times or more; {longest_said(long[0])}."}


def rule_tokens(cwd, files, home):
    """An estimate (bytes / 4) of the rule files that load in a session started in `cwd`: CLAUDE.md and
    CLAUDE.local.md there and in every folder above, the user-level one, and what they import."""
    above, c = {cwd}, cwd
    while c and c != os.path.dirname(c):
        c = os.path.dirname(c)
        above.add(c)
    user = os.path.join(home, ".claude", "CLAUDE.md")
    out = {p: f for p, f in files.items() if f.get("kind") in ("claude", "claude_local") and f.get("bytes")
           and (os.path.dirname(p) in above or p == user)}
    for f in list(out.values()):
        for i in f.get("imports") or []:
            g = files.get(i.get("resolved")) if isinstance(i, dict) else None
            if g and g.get("bytes"):
                out[g["path"]] = g
    return sum(_num(f.get("bytes")) or 0 for f in out.values()) / 4


def card_starting_context(chats, an, home):
    starts = [(c, r.get("cwd")) for r in chats for c in [first_context(r)] if c]
    if len(starts) < START_SESSIONS:
        return None
    med = statistics.median(c for c, _ in starts)
    if med < START_TOKENS:
        return None
    files = {f["path"]: f for f in (an or {}).get("instruction_files") or [] if isinstance(f, dict) and f.get("path")}
    per_cwd = {}
    for _, cwd in starts:
        if cwd and cwd not in per_cwd:
            per_cwd[cwd] = rule_tokens(cwd, files, home)
    rules = statistics.median(per_cwd[cwd] for _, cwd in starts if cwd) if per_cwd else 0
    share = rules / med if med else 0
    servers = len({x.get("name") for x in (an or {}).get("mcp_servers") or [] if isinstance(x, dict) and x.get("name")})
    skills = sum(1 for a in (an or {}).get("artifacts") or [] if isinstance(a, dict) and a.get("kind") == "skill")
    # found, not loaded: the records don't split a session's start, and a skill puts in only its name and description
    around = [f"{plural(servers, 'MCP server')} {'are' if servers != 1 else 'is'} configured" if servers else "",
              f"{plural(skills, 'skill')} {'were' if skills != 1 else 'was'} found" if skills else ""]
    around = " and ".join(x for x in around if x)
    saw = (f"Your sessions start with about {tokens(med)} tokens in context before the first task (the median of "
           f"{plural(len(starts), 'session')}). Your own rule files are about {tokens(rules)} of that"
           + (f"; {around} on this laptop." if around else "."))
    if share >= START_RULES_SHARE:
        do = ("Your rule files are a large part of it. Keep in the always-loaded file only what every task needs; move "
              "the rest into docs it points to, or into rule files in the folders they apply to.")
    else:
        do = ("Type /context at the start of a session to see what fills it. Then switch off the MCP servers and plugins "
              "this project doesn't use (/mcp, /plugin), or set them up in the one project that needs them.")
    # figures: the numbers the page draws (view_review: the start as pages, their own share lit), so it never parses `saw`
    return {"id": "starting-context", "source": "records", "saw": saw, "do": do,
            "figures": {"tokens": round(med), "own_tokens": round(rules), "sessions": len(starts)},
            "cost": "All of it is read again on every turn of every session, before your task: you pay for it each "
                    "time, and the agent has more to wade through to find what matters."}


def board_rows(run):
    """The capability board's rows (labs/v15/engine/board.py), by id: the one place that counts what is set up, whose
    it is and whether it was called, so this card and the "What you've built" tab can never disagree."""
    try:
        if ENGINE not in sys.path:
            sys.path.insert(0, ENGINE)
        import board  # type: ignore[import-not-found]  # on ENGINE, put on sys.path above
        return {r["id"]: r for r in board.board(run)["rows"]}
    except Exception:  # noqa: BLE001  a board that can't be read is a card not shown
        return {}


def card_unused_setup(rows, days):
    own = rows.get("cap-skills-own") or {}
    c = level_things.others_tools(rows)
    n, calls = c["skills"], c["skill_calls"]
    # the same thing the level's sentence counts: skills and commands of theirs, one per name (the board's `list`), not the
    # skill-depth facts alone (11-12: "3 skills you wrote" beside "5 skills and commands")
    theirs = [s for s in (own.get("list") if isinstance(own.get("list"), list) else own.get("skills")) or [] if isinstance(s, dict)]
    idle = [s for s in theirs if s.get("used") is False]
    others = n >= UNUSED_INSTALLED_MIN and calls < n * UNUSED_CALLS_PER_ITEM
    mine = len(idle) >= UNUSED_OWN_MIN
    if not others and not mine:
        return None
    said = []
    if others:
        said.append(level_things.others_said(c, days).rstrip(".").replace("You have ", "", 1))
    if mine:
        said.append(f"{len(idle)} of the {plural(len(theirs), 'skill or command you wrote', 'skills and commands you wrote')} "
                    f"{'was' if len(idle) == 1 else 'were'} not loaded in {'that time' if others else f'the last {days} days'}")
    do = ("Type /plugin and uninstall what you did not use this month. For a skill that sits in a skills folder, move "
          "its folder out: nothing is lost, and it goes back the day you need it." if others else
          "Open each unused skill's SKILL.md. If its description doesn't say when to use it, rewrite that one line; if "
          "you no longer need the skill, move its folder out.")
    figures = {"own": len(theirs), "own_used": len(theirs) - len(idle)} if mine else {"others": n, "others_calls": calls}
    return {"id": "unused-setup", "source": "records", "saw": ("You have " if others else "") + "; ".join(said) + ".", "do": do, "figures": figures,
            "cost": "Each one puts its name and description into every session. The more there are, the more the "
                    "agent has to choose between, and the less often it picks the one you meant."}


def own_always_loaded(m, home):
    """Paths on disk of the person's own always-loaded rule files, from the map (ownership.py's verdict, as mapped)."""
    out = set()
    for p in ((m or {}).get("projects") or []) + [(m or {}).get("user") or {}]:
        root = os.path.normpath(expand(p.get("root") or "", home))
        for e in p.get("instruction_files") or []:
            if e.get("loads") == "always" and e.get("path"):
                out.add(absolute(e["path"], root, home))
    return out


def card_rules_only_grow(inv, own, home):
    hist = (inv or {}).get("instruction_files")
    hist = hist.get("always_loaded") if isinstance(hist, dict) else None
    grew = []
    for f in hist or []:
        if not isinstance(f, dict) or not f.get("path"):
            continue
        path = os.path.normpath(expand(str(f["path"]), home))
        words, commits = int(_num(f.get("words_now")) or 0), int(_num(f.get("commits")) or 0)
        # `trims` is every change that cut the file by more than a tenth: none means it never got shorter
        if path in own and words >= RULES_GROW_WORDS and commits >= RULES_GROW_CHANGES and not f.get("trims"):
            grew.append((words, commits, path))
    if not grew:
        return None
    grew.sort(reverse=True)
    words, commits, path = grew[0]
    name = tilde(path, home)
    more = f", and {len(grew) - 1} more like it" if len(grew) > 1 else ""
    return {"id": "rules-only-grow", "source": "records",
            "saw": f"{name} loads in every session and has only ever grown: {plural(words, 'word')} after {plural(commits, 'change')}, "
                   f"none of which made it shorter{more}.",
            "cost": "Every line loads on every turn. The longer the file, the less weight each line carries, and the "
                    "agent starts passing over rules you care about.",
            "do": f"Read {name} top to bottom. Delete what the agent gets right without being told, and move what "
                  "only one area needs into a rule file in that folder."}


def cards(run, inv, m, hold, home):
    """Advice counted from Claude Code's own records, the largest cost first: what a session loses (its scope), what
    every turn pays (how full it starts, rule files that only grow), then what merely adds clutter (unused setup).
    A fired hold is the session-scope card: it takes that card's cost and next step, and no second card is made."""
    chats = chats_of(run)
    long = long_sessions(chats)
    if hold.get("fired"):
        hold.update(cost=SCOPE_COST, do=SCOPE_DO)
        if long:
            hold["why"] += "; " + longest_said(long[0])
    days = _num(((inv or {}).get("meta") or {}).get("days")) or 30
    makers = ((lambda: None if hold.get("fired") else card_session_scope(chats)),
              (lambda: card_starting_context(chats, load(run, "anatomy.json"), home)),
              (lambda: card_rules_only_grow(inv, own_always_loaded(m, home), home)),
              (lambda: card_unused_setup(board_rows(run), days)))
    out = []
    for make in makers:
        try:
            c = make()
        except Exception:  # noqa: BLE001  a record in a shape this card did not expect is a card not shown
            c = None
        if c:
            out.append(c)
    return out[:MAX_CARDS]


# ------------------------------------------------------------------ merge

def judged_of(v):
    v = squash(v or "").lower().replace("’", "'")
    v = {"cant tell": "can't tell", "unclear": "can't tell", "not_shown": "not shown", "no": "not shown", "yes": "shown"}.get(v, v)
    return v if v in JUDGED else "can't tell"


def tagline(v):
    """The reader's few words naming the project (the Review's grid shows them under its name): at most 7 words."""
    words = text(v, 120).rstrip(".").split()
    return " ".join(words[:7])


def text(v, cap=400):
    """A string the reader wrote, safe to put on the page: squashed, scrubbed of anything secret-looking (the reader
    may repeat what it read), cut at `cap`."""
    v = setup_map.scrub(squash(v)) if isinstance(v, (str, int, float)) else ""
    return v if len(v) <= cap else v[:cap - 1] + "…"


def clean_quote(q):
    if not isinstance(q, dict) or not q.get("text"):
        return None
    line = q.get("line")
    return {"path": text(q.get("path"), 200), "line": line if isinstance(line, int) else None, "text": text(q.get("text"), 300)}


PART_SAYS = {"rules": "rules that reach the agent", "corrections": "corrections that stick",
             "memory": "memory that carries across sessions"}


def _and(xs):
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def opening(projects, first):
    """The Review's first lines and the skill's closing words: one or two sentences about the PERSON, across every
    project that was read. Why (11-11): the page opened on the most active project's headline ("a mature, layered
    setup for a video studio…"), which describes one repo and not the person. Built by code from the verdicts, no new
    AI call: in how many projects each part shows (the level's own test: the code's structure, a "shown" reading and
    a quote found in the file), then the first thing to change. [] when no reader answered."""
    read = [p for p in projects if any(g.get("judged") != "can't tell" for g in p.get("parts") or [])]
    n = len(read)
    if not n:
        return []
    k = {pid: sum(1 for p in read for g in p["parts"]
                  if g["id"] == pid and level_things.part_state(g)[0] == "shown") for pid, _ in PARTS}
    names = [PART_SAYS[pid] for pid, _ in PARTS]
    if n == 1:
        yes, no = [PART_SAYS[i] for i, _ in PARTS if k[i]], [PART_SAYS[i] for i, _ in PARTS if not k[i]]
        if not yes:
            line = ("In your most active project, your files don't yet show a setup that carries the context: "
                    f"{_and(no)} are each missing or thin.")
        else:
            line = f"In your most active project, your files show {_and(yes)}" + (
                f"; {_and(no)} {'is' if len(no) == 1 else 'are'} not shown yet." if no else ".")
    elif all(v == n for v in k.values()):
        line = f"In all {n} of your most active projects, your files show {_and(names)}."
    elif not any(k.values()):
        line = (f"An agent read your {n} most active projects. In none of them do your files yet show {_and(names[:2])}"
                f" or {names[2]}.")
    else:
        line = (f"Across your {n} most active projects, your files show "
                + _and([f"{PART_SAYS[i]} in {k[i]}" for i, _ in PARTS]) + ".")
    out = [line]
    saw = text((first or {}).get("saw") or (first or {}).get("do"))
    if saw:
        out.append("The first thing to change: " + (saw[:1].lower() + saw[1:] if saw[1:2].islower() else saw))
    return out


def first_change(out):
    """What "Change this first" lists first (view_review._changes, the skill's closing words): a fired hold, then a
    card from Claude Code's records, then the most active project's first change, then a part's next step."""
    items = ([{"saw": h.get("why"), "do": h.get("do")} for h in out.get("holds") or [] if h.get("fired")]
             + list(out.get("cards") or []) + [c for p in out.get("projects") or [] for c in (p.get("change") or [])[:1]]
             + [{"saw": g.get("why"), "do": g.get("next")} for g in out.get("parts") or []
                if g.get("judged") in ("partly", "not shown")])
    return next((c for c in items if c.get("saw") or c.get("do")), None)


def merge(run, home=None):
    home = home or os.path.expanduser("~")
    m = load(run, "setup-map.json") or {}
    inv = load(run, "inventory.json") or {}
    user = m.get("user") or {}
    reads = {os.path.normpath(s["path"]): s.get("chats_reading") for s in inv.get("memory_stores") or []
             if s.get("path") and s.get("chats_reading") is not None}
    per, projects, ran = {pid: [] for pid, _ in PARTS}, [], False
    for s, root, p in projects_of(run, m, home):
        doc = load(run, f"anatomy-ai-{s}.json") or {}
        if not doc:   # a reader may have named its file by the folder, not the slug
            for path in glob.glob(os.path.join(run, "anatomy-ai-*.json")):
                d = load(run, os.path.basename(path)) or {}
                if os.path.normpath(str(d.get("project") or "")) == root:
                    doc = d
        sr = doc.get("setup_read")
        sr = sr if isinstance(sr, dict) else {}
        answers = {a.get("id"): a for a in sr.get("parts") or [] if isinstance(a, dict)}
        ran = ran or bool(answers)
        where = allowed(p, user, root, home)
        structure = {"rules": rules_structure(p), "corrections": corrections_structure(p, user, root, home),
                     "memory": memory_structure(p, user, root, home, reads)}
        for pid, name in PARTS:
            a = answers.get(pid) or {}
            ok, why = structure[pid]
            per[pid].append({"id": pid, "name": name, "structure": bool(ok), "structure_why": why,
                             "judged": judged_of(a.get("judged")), **quoted(a.get("quote"), where, root, home),
                             "why": text(a.get("why")),
                             "complexity": text(a.get("complexity")), "next": text(a.get("next")), "project": s})
        projects.append({
            "slug": s, "label": os.path.basename(root), "tagline": tagline(sr.get("tagline")),
            "headline": text(sr.get("headline"), 240),
            "summary": text(sr.get("summary"), 700), "map_md": tree(m, p, PAGE_TREE_WORDS, with_user=False),
            # this project's own verdicts (no text): the opening says in how many projects each part shows
            "parts": [{k: per[pid][-1][k] for k in ("id", "judged", "structure", "quote_ok")} for pid, _ in PARTS],
            # what the page shows when a person opens one of those verdicts: the line it rests on and the next step
            # (scrubbed like every string a reader wrote); the share summary never reads it (share_payload: verdicts only)
            "read": {pid: {k: per[pid][-1].get(k) for k in ("why", "quote", "quote_ok", "quote_dropped", "next")
                           if per[pid][-1].get(k) is not None} for pid, _ in PARTS},
            "works": [{"what": text(w.get("what")), "why": text(w.get("why")), **quoted(w.get("quote"), where, root, home)}
                      for w in (sr.get("works") or [])[:3] if isinstance(w, dict) and w.get("what")],
            "change": [{"saw": text(c.get("saw")), "cost": text(c.get("cost")), "do": text(c.get("do"))}
                       for c in (sr.get("change") or [])[:3] if isinstance(c, dict) and (c.get("saw") or c.get("do"))]})
    rank = {j: i for i, j in enumerate(reversed(JUDGED))}
    parts = []
    for pid, name in PARTS:
        rows = per[pid]
        if not rows:
            parts.append({"id": pid, "name": name, "structure": False, "structure_why": "no project was read",
                          "judged": "can't tell", "quote_ok": False, "quote": None, "why": "", "complexity": "",
                          "next": "", "project": None})
            continue
        # the best single project: one that passes whole, then the strongest reading, then structure; ties go to the
        # most active project (the map's order)
        parts.append(max(rows, key=lambda r: (level_things.part_state(r)[0] == "shown",
                                              rank[r["judged"]], r["structure"], r["quote_ok"], -rows.index(r))))
    hold = hold_endless(inv)
    out = {"ran": ran, "parts": parts, "holds": [hold], "cards": cards(run, inv, m, hold, home), "projects": projects}
    out["opening"] = opening(projects, first_change(out)) if ran else []
    with open(os.path.join(run, "setup-read.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    shown = [x["id"] for x in parts if level_things.part_state(x)[0] == "shown"]
    print(f"setup read: {plural(len(projects), 'project')}"
          + ("" if ran else ", no reader answered (the level reads 'could be')")
          + f"; parts shown: {', '.join(shown) or 'none'}"
          + ("; held: endless sessions" if out["holds"][0]["fired"] else "")
          + (f"; cards: {', '.join(c['id'] for c in out['cards'])}" if out["cards"] else ""))
    return out


def main(argv):
    flags = [a for a in argv if a.startswith("--")]
    argv = [a for a in argv if not a.startswith("--")]
    if len(argv) != 2 or argv[0] not in ("brief", "merge") or any(f != "--proxify" for f in flags):
        sys.exit(__doc__)
    run = os.path.abspath(os.path.expanduser(argv[1]))
    if argv[0] == "brief":
        brief(run, proxify=True if flags else None)
    else:
        merge(run)


if __name__ == "__main__":
    main(sys.argv[1:])
