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
  cards     advice, each {id, saw, cost, do, source}: what the page says to change first. source "records": counted from
            Claude Code's records (how long sessions run, how full they start, setup nobody uses, an `AGENTS.md` that
            only grows). source "files": counted by code in the person's instruction files, no AI (a pointer to a file
            that is not there, lines that restate the repo, an `AGENTS.md` Claude Code never reads, no command that
            checks the work). Advice only: no card moves a level. A card the page draws a picture of carries its
            numbers in `figures`.
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
import setup_instruction  # noqa: E402  what code alone reads in one instruction file (no AI): pointers, commands, restated lines
import setup_craft  # noqa: E402  the craft read: the person's most used crafts placed on a step, advice only (craft-read.json)
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
RESTATES_MIN_LINES = 1       # restates the repo: lines of one always-loaded file that say what the repo's own files say
FREE_FILES = 6               # instruction files code reads per project for the cards below (always loaded, then unread)
LINES_ALWAYS = 6             # the line-by-line read: always-loaded files of a project in the reader's brief
LINES_SCOPED = 2             # ... and scoped files (a subfolder's file, a rule with paths:), the ones Claude Code's records show loaded most
LINES_CAP = 40               # ... blocks asked about in one project, after code drops what it settles
LINES_USER_CAP = 20          # ... and blocks of the user-level file, once per run, in the first project's brief
DEAD_ONLY_WORDS = 6          # a block that is a dead pointer and at most this many other words is code's to report, not the reader's
MAX_CARDS = 4
SHAPED = ("indexed", "layered", "linked")

GIT_TIMEOUT, GIT_FILES, GIT_BUDGET = 4, 12, 20   # seconds per file, files per project, seconds per project
BRIEF_TREE_WORDS, PAGE_TREE_WORDS = 1200, 900
MAX_QUOTE_FILE = 2_000_000
MARK = "## The setup read"
PARTS = (("rules", "Instructions the agent starts with"), ("corrections", "Corrections that stick"),
         ("memory", "Memory across sessions"))
JUDGED = ("shown", "partly", "not shown", "can't tell")

READER = """{mark}

{intro}Review this project's agent setup the way a senior engineer reviews a colleague's: what the agent knows when a
session starts here, how that knowledge is organised, and whether it would hold as the work grows.

The outline at the end was drawn by code. It shows structure and judges nothing. Read-only. Open what you need to
judge the three parts below and no more than this: {always}; up to {scoped} scoped files; up to {docs}
docs they link to (the ones an `AGENTS.md` sends the agent to most plainly); the memory index and {notes} notes; the
SKILL.md of up to {skills} skills. `git log` on at most {logs} files, the main `AGENTS.md` first. When the outline lists
nothing for a part, judge it from the outline and open nothing.{lines_open}

Judge three parts, each `shown`, `partly`, `not shown` or `can't tell`:

1. `rules`: the agent starts with the instructions it needs, without being told.
   - shown: the always-loaded files hold facts the agent can't work out from the code, each short and with its
     reason; instructions for one area sit in that area (an `AGENTS.md` in a subfolder; extra: a `.claude/rules/` file with `paths:`), not in one file for
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

Word it plainly. The instruction file the agent reads at the start of every session is called `AGENTS.md` (`CLAUDE.md` is
its known synonym: say "`AGENTS.md` (or `CLAUDE.md`)" the first time, then `AGENTS.md`). A file in `.claude/rules/` is a separate,
extra mechanism: name it `.claude/rules/` and keep it apart. Never call either of them a "rule file" or "rules".

Add one key, `setup_read`, to the JSON object you write to {out}:

    "setup_read": {{
      "parts": [{{"id": "rules", "judged": "shown|partly|not shown|can't tell", "why": "...",
                 "quote": {{"path": "...", "line": 0, "text": "..."}}, "complexity": "...", "next": "..."}},
                {{"id": "corrections", ...}}, {{"id": "memory", ...}}],
      "tagline": "...", "headline": "...", "summary": "...",
      "works": [{{"what": "...", "why": "...", "quote": {{"path": "...", "line": 0, "text": "..."}}}}],
      "change": [{{"saw": "...", "cost": "...", "do": "..."}}]{lines_shape}
    }}
{lines}
### The outline (`w` = words; `→` = a pointer to another file)

{tree}
"""

BARE = """# Setup read: {name}

Read-only. Review this project only: {root}

Write the result with the Write tool to {out} as one JSON object:
{{"project": "{root}", "answers": [], "setup_read": {{...}}}}

"""
SECOND_JOB = "A second job, in the same answer file. "

# The line-by-line read (11-12, from the instruction read trial: experiments/2026-11-12-instruction-read/): the same reader, one
# more answer key. Advice only: nothing here reaches a level, and no number the reader counts reaches the page.
LINES_OPEN = (" The text of the always-loaded files is in \"The lines\" below: do not open them.")
LINES_SHAPE = """,
      "lines": {
        "blocks": [{"id": "B1", "kind": "...", "known": "yes|no", "situation": null, "action": null, "checkable": "yes|no",
                    "reason": "yes|no", "earned": "yes|no|can't tell", "quote": "..."}],
        "weakest": {"block": "B?", "quote": "...", "why": "...", "rewrite": "..."},
        "strongest": {"block": "B?", "quote": "...", "why": "..."}
      }"""
LINES = """
### The lines (a second job, in the same answer file)

The files below load at the start of every session they are named for, so each line costs the agent attention in every such
session, and earns its place only when it changes what the agent does. Their text is here, in blocks, with what code found
(`found` = a name the repo's own files hold; `ptr` = a pointer, MISSING when its file is not there; `cmd` = a command; `hist` =
git's record of when the line was written). Open another file only when a block's answer turns on it and code's findings do not
settle it, and at most three. For each block marked `judge`, add one entry to `lines.blocks`:

- `kind`: `describes` (states what the project is or uses) · `points` (sends the agent to another file) · `steers` (tells it how
  to work in some situation) · `guards` (forbids or limits an action) · `verifies` (says how to check the work, or when it is
  done) · `preference` (how the person wants the conversation: tone, format of answers or plans) · `structure` (only organises).
- `known`: `yes` when the agent would get this anyway, from the repo or by default, with no line telling it; else `no`.
- `situation`: the situation the agent can recognise in this project in which the block applies (a kind of task, a file or folder,
  a command, an event), in a few words; `null` when it applies always or never says. "When needed", "when possible" and a topic
  label ("Architecture & Routing:") are no situation: the task itself ("adding a route") is. A labelled list of docs has `null`.
- `action`: what the block makes the agent do there that it would not do by default; `null` when none.
- `checkable`: `yes` when someone reading the transcript or the diff could tell whether it was followed.
- `reason`: `yes` when the block says why, in a way that helps decide a case it does not name (a consequence in this project: what
  breaks, who is hurt, what happened); `no` when it has none or only one that fits any project ("to keep things clean").
- `earned`: `yes` when the block exists because something went wrong (`hist` shows it added after the file's first commit, or it
  names the specific mistake it prevents); `no` when it reads as written before any agent ran and `hist` agrees; else `can't tell`.
- `quote`: the block's most telling words, copied exactly from the file (at least 20 characters when the block has that many).

Then, for the whole of these files:
- `strongest`: the block that most changes what the agent does here (`block`, `quote`, `why` in one sentence).
- `weakest`: the block that costs the most for what it gives (`block`, `quote`, `why`), and `rewrite`: that block written as it
  should be (a situation the agent can recognise, the action, how to tell it was done, the reason), using only facts in these
  files or in code's findings: invent no command and no path. Code checks the quote against the file and the rewrite against
  the repo; the person reads the `why` and the `rewrite`, so word them as above and never write a person's name.

{files}"""

LINES_NOTE = ("Blocks code settled are left out: headings that only label, lines that restate what the repo's own files say, a line that is "
              "only a pointer to a file that is not there. A block with a secret on its line is left out too.")
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


# ------------------------------------------------------------------ the line-by-line read: the reader's second job

def reader_files(p, user, root, home, inv, first):
    """The files whose lines the reader judges, from the map: [(path on disk, how it loads)]. A project's always-loaded files
    (the `AGENTS.md` or `CLAUDE.md` chain at its root, `CLAUDE.local.md`, what they @import, `.claude/rules/` files with no
    `paths:`), the shallowest first, then at most LINES_SCOPED scoped files: the ones Claude Code's records show loaded most
    (`rules_reaching`). The user-level `~/.claude/CLAUDE.md` joins the first project's brief only, once per run. Files above
    the repo root are not read. An unreadable or unlisted file is left out."""
    out = []
    ents = [e for e in p.get("instruction_files") or [] if "error" not in e]
    always = sorted((e for e in ents if e.get("loads") == "always"), key=lambda e: (e.get("depth", 0), e["path"]))
    for e in always[:LINES_ALWAYS]:
        out.append((absolute(e["path"], root, home), "every session in this project"))
    reached = {os.path.normpath(str(x.get("what"))): x.get("chats") or 0
               for x in ((inv or {}).get("rules_reaching") or {}).get("by_file") or [] if isinstance(x, dict) and x.get("what")}
    scoped = [e for e in ents if e.get("loads") == "scoped"]
    scoped.sort(key=lambda e: (-reached.get(absolute(e["path"], root, home), 0), -e.get("words", 0), e["path"]))
    for e in scoped[:LINES_SCOPED]:
        out.append((absolute(e["path"], root, home), "when the agent works in its folder"))
    if first:
        up = os.path.join(home, ".claude", "CLAUDE.md")
        if any(absolute(e["path"], os.path.join(home, ".claude"), home) == up for e in (user or {}).get("instruction_files") or []
               if e.get("loads") == "always" and "error" not in e) and os.path.isfile(up):
            out.append((up, "every session on this laptop"))
    return out


def _only_a_dead_pointer(b):
    ptrs = b.get("pointers") or []
    if not ptrs or any(q["exists"] for q in ptrs):
        return False
    rest = b["text"]
    for q in ptrs:
        rest = rest.replace(q["raw"], " ")
    return len(re.sub(r"[#*_`>|\[\]()@-]", " ", rest).split()) <= DEAD_ONLY_WORDS


def reader_pool(p, user, root, home, inv, first):
    """(listing for the brief, sidecar for merge) of one project's line-by-line read, or (None, None) when there is nothing to
    read. Blocks code settles are left out (LINES_NOTE), the rest are numbered B1, B2... across the files, at most LINES_CAP in the
    project's own files and LINES_USER_CAP in the user-level one. The sidecar holds, per block, its file, lines and what code
    found, and a hash of each file, so merge checks the answer against the file it was asked about."""
    import hashlib
    files = reader_files(p, user, root, home, inv, first)
    if not files:
        return None, None
    view = setup_instruction.repo_view(root)
    facts = view[0]
    vocab = sorted({*facts["scripts"], *facts["folders"], *facts["files"], *(os.path.basename(x) for x in view[1][:5000])})
    side = {"schema": "instruction-lines/1", "root": root, "files": [], "blocks": {}, "vocab": vocab[:3000]}
    out, n = [], 0
    for path, loads in files:
        user_level = loads.startswith("every session on this laptop")
        text = setup_instruction.read_text(path)
        if text is None:
            continue
        r = setup_instruction.read_file(path, root=None if user_level else root, home=home, with_history=True,
                                        view=None if user_level else view)
        if r.get("error"):
            continue
        own = sum(1 for k in side["blocks"].values() if k["user"] == user_level)
        cap = LINES_USER_CAP if user_level else LINES_CAP
        fi = len(side["files"])
        side["files"].append({"path": path, "display": r["display"], "loads": loads, "user": user_level,
                              "sha": hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()})
        head = [f"#### {r['display']} ({plural(r['lines'], 'line')}, about {tokens(r['tokens_est'])} tokens; loads {loads})"]
        g = r.get("git") or {}
        head.append(f"- git: {g['commits']} commits from {g['first']} to {g['last']}" if g.get("tracked") else
                    "- git: the file is not in git, so its history can't be read")
        if r.get("sibling") and r["sibling"].get("relation"):
            head.append(f"- beside it: {r['sibling']['file']}; {r['sibling']['relation']}")
        body = []
        for b in r["blocks"]:
            if b["kind"] == "heading" and not b.get("judge"):
                body.append(f"    (heading, line {b['start']}: {setup_map.scrub(b['text'])[:100]})")
                continue
            if not b.get("judge") or b.get("restates_repo") or _only_a_dead_pointer(b):
                continue
            if setup_map.has_secret(b["text"]):
                continue   # a line holding a secret is never sent to a reader and never quoted
            if own >= cap:
                break
            n, own = n + 1, own + 1
            bid = f"B{n}"
            side["blocks"][bid] = {"file": fi, "start": b["start"], "end": b["end"], "user": user_level,
                                   "source": next((str(v).split(":")[0] for v in (b.get("found") or {}).values()), None)}
            facts_ = setup_instruction._block_line(b)
            text_ = "\n".join("    | " + ln for ln in b["text"].split("\n"))
            body.append(f"{bid} · lines {b['start']}-{b['end']} · {b['kind']}\n{text_}" + ("\n    - " + "\n    - ".join(facts_) if facts_ else ""))
        out.append("\n".join(head) + "\n\n" + "\n".join(body))
    if not side["blocks"]:
        return None, None
    repo = [f"- the repo: scripts that check the work {r_} ; hooks {sorted(facts['hooks'])[:6]}; skills {facts['skills'][:8]}".replace(" ;", ";")
            for r_ in [sorted(s for s in facts["scripts"] if setup_instruction.CHECK_WORDS.search(s))[:10]]]
    listing = LINES_NOTE + "\n" + "\n".join(repo) + "\n\n" + "\n\n".join(out)
    return LINES.format(files=listing), side


def check_lines(side, ans):
    """The reader's line-by-line answer, checked by code: {state, why, blocks_asked, blocks_read, weakest, strongest}.
    state "couldn't check" when the answer is empty or malformed, or a file changed since the brief: never a verdict. A block counts
    only when its quote sits inside its own lines of the file (setup_instruction.quote_in, the setup read's quote check) and no
    line it sits on holds a secret. `weakest` and `strongest` are None unless their quote is found; a `rewrite` that names a
    backticked command or path found in neither the file nor the repo is dropped with its card (the reader was told to invent none)."""
    asked = len(side.get("blocks") or {})
    res = {"state": "couldn't check", "why": "", "blocks_asked": asked, "blocks_read": 0, "weakest": None, "strongest": None}
    if not isinstance(ans, dict) or not isinstance(ans.get("blocks"), list) or not ans["blocks"]:
        res["why"] = "the reader gave no line-by-line answer"
        return res
    texts = {}
    for i, f in enumerate(side["files"]):
        t = setup_instruction.read_text(f["path"])
        if t is None or __import__("hashlib").sha1(t.encode("utf-8", "replace")).hexdigest() != f["sha"]:
            res["why"] = f"{f['display']} changed since the reader was asked about it"
            return res
        texts[i] = t
    rows = {}
    for a in ans["blocks"]:
        m = re.match(r"^(B\d+)", str(a.get("id") if isinstance(a, dict) else ""))
        bl = side["blocks"].get(m.group(1)) if m else None
        if not bl or m.group(1) in rows:
            continue
        ok, _ = setup_instruction.quote_in(texts[bl["file"]], a.get("quote"), bl["start"], bl["end"])
        if ok:
            rows[m.group(1)] = {"kind": a.get("kind") if a.get("kind") in setup_instruction.KINDS else None,
                                "known": a.get("known") == "yes", "situation": bool(a.get("situation")) and bool(a.get("action")),
                                "reason": a.get("reason") == "yes", "source": bl["source"]}
    res.update(state="read", blocks_read=len(rows))

    def pick(key):
        w = ans.get(key)
        bl = side["blocks"].get((w or {}).get("block")) if isinstance(w, dict) else None
        if not bl:
            return None
        ok, _ = setup_instruction.quote_in(texts[bl["file"]], w.get("quote"), bl["start"], bl["end"])
        why, rewrite = squash(w.get("why") or ""), squash(w.get("rewrite") or "")
        if not ok or setup_map.has_secret(why) or setup_map.has_secret(rewrite):
            return None
        f = side["files"][bl["file"]]
        want = squash(w["quote"]).strip("`\"'“”")
        lines = texts[bl["file"]].split("\n")
        line = next((bl["start"] + i for i, ln in enumerate(lines[bl["start"] - 1:bl["end"]]) if squash(ln) and squash(ln)[:30] in want or want[:30] in squash(ln)),
                    bl["start"])
        out = {"block": w["block"], "file": f["display"], "path": f["path"], "loads": f["loads"], "line": line,
               "quote": text(w["quote"], 300), "why": text(why, 300), "verdict": rows.get(w["block"]) or {}}
        if key == "weakest":
            if rewrite and invents(rewrite, texts[bl["file"]], side.get("vocab") or []):
                return None
            out["rewrite"] = text(rewrite, 600)
        return out
    res["weakest"], res["strongest"] = pick("weakest"), pick("strongest")
    if res["weakest"] is None:
        res["why"] = "the weakest line's quote was not found in the file, or its rewrite named something the repo does not hold"
    return res


def invents(rewrite, file_text, vocab):
    """True when a rewrite names a backticked command or path that is in neither the instruction file nor the repo's names."""
    have = set(vocab)
    for m in setup_map.CODE_SPAN.finditer(rewrite):
        tok = m.group(0).strip("`").strip()
        if not tok or tok in file_text or tok in have:
            continue
        words = tok.split()
        if len(words) > 1 and words[0] in setup_instruction.RUNNERS and words[-1] in have:
            continue
        return True
    return False


def card_weakest_line(lines):
    """The one line a person should rewrite: the busiest project's weakest line of the files that load, its quote found in the
    file by code (check_lines), with the reader's rewrite. {slug: check_lines result}, in the map's order. Nothing is counted by
    the reader here: the card holds the quote, why it costs, and the rewrite."""
    for res in lines.values():
        w = (res or {}).get("weakest")
        if not w or res.get("state") != "read":
            continue
        v = w.get("verdict") or {}
        head = ("The agent cannot tell when it applies, nor why" if not v.get("situation") and not v.get("reason") else
                "The agent cannot tell when it applies" if not v.get("situation") else
                "The agent is not told why" if not v.get("reason") else "")
        known = f"it finds this in `{v['source']}` already" if v.get("source") else "it gets this without being told"
        said = (f"{head}, and {known}" if head else f"The agent: {known}".replace("The agent: it", "The agent")) if v.get("known") else head
        cost = (said or w["why"]).rstrip(".") + ". It still takes attention on every turn."
        if w.get("rewrite"):
            do = f"Rewrite it as: \u201c{w['rewrite']}\u201d"
        elif v.get("known"):
            do = "Delete it."
        else:
            continue
        where = {"every session in this project": "which loads in every session here",
                 "when the agent works in its folder": "which loads when the agent works in its folder",
                 "every session on this laptop": "which loads in every session on this laptop"}.get(w["loads"], "which loads in a session")
        return {"id": "weakest-line", "source": "files", "file": w["path"], "quote": {"path": w["file"], "line": w["line"], "text": w["quote"]},
                "saw": f"In `{w['file']}`, {where}, this line does the least for what it costs: \u201c{w['quote']}\u201d.",
                "cost": cost, "do": do}
    return None


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
    n, added, kept, inv = 0, 0, set(), load(run, "inventory.json")
    for stale in glob.glob(os.path.join(run, "instruction-lines-*.json")):
        os.remove(stale)   # a rerun asks again: the answer is checked against this run's files
    for k, (s, root, p) in enumerate(projects_of(run, m, home)):
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
        try:
            listing, side = reader_pool(p, m.get("user"), root, home, inv, first=k == 0)
        except Exception:  # noqa: BLE001  the line-by-line read is a second job: without it the review is asked as before
            listing, side = None, None
        if side:
            with open(os.path.join(run, f"instruction-lines-{s}.json"), "w", encoding="utf-8") as f:
                json.dump(side, f)
        crafts = ""
        if k == 0:    # the craft read: one batched job, in the first (busiest) project's brief only
            try:
                crafts = setup_craft.job_section(run, home)
            except Exception:  # noqa: BLE001  a third job: without it the review and the lines are asked as before
                crafts = ""
        section = READER.format(mark=MARK, out=out, tree=drawn, intro=SECOND_JOB if second else "", scoped=OPEN_SCOPED,
                                docs=OPEN_DOCS, notes=OPEN_NOTES, skills=OPEN_SKILLS, logs=OPEN_GIT_LOGS,
                                always="the always-loaded files", lines_open=LINES_OPEN if listing else "",
                                lines_shape=LINES_SHAPE if listing else "", lines=(listing or "") + crafts)
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
    said = (f"{a} file{'s' if a != 1 else ''} read at the start of every session, {s} for part of the project, {d} doc{'s' if d != 1 else ''} they link to"
            + (f" ({t.get('max_depth', 0)} layer{'s' if t.get('max_depth', 0) != 1 else ''} deep)" if d else ""))
    if not a:
        return False, "no AGENTS.md of yours at the top of this project, and no .claude/rules/ file that always loads (" + said + ")"
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
        return False, "no AGENTS.md or skill of yours here"
    best, in_git, t0 = None, 0, time.monotonic()
    for f in files[:GIT_FILES]:
        if time.monotonic() - t0 > GIT_BUDGET:
            break
        h = history(f)
        if not h:
            continue
        in_git += 1
        key = (h["weeks"] >= CORRECTION_WEEKS_MIN and h["removed"] > 0, h["weeks"], h["removed"])
        if best is None or key > best[0]:
            best = (key, f, h)
    if best is None:
        return False, "your AGENTS.md files and skills are not in git, so their history can't be read"
    _, f, h = best
    name = os.path.relpath(f, root) if f.startswith(root + os.sep) else "~/" + os.path.relpath(f, home)
    said = (f"{name} changed in {h['weeks']} different week{'s' if h['weeks'] != 1 else ''}, "
            + (f"{h['removed']} lines removed along the way" if h["removed"] else "and no line was ever removed"))
    return best[0][0], said


def has_shape(store):
    """Whether the store has the shape of memory. Question 1 of memory_shape.py already answered it when the scan listed the
    store (the map carries the answer as memory_shape); it is not judged a second time here. A map with no answer on the
    store (an older run's) falls back to the outline's own shape."""
    if store.get("memory_shape"):
        return store["memory_shape"] != "none"
    return store.get("shape") in SHAPED


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
        shaped = has_shape(s)
        ok = shaped and s.get("files", 0) >= MEMORY_NOTES_MIN and (n is None or n >= MEMORY_SESSIONS_MIN)
        scored.append(((ok, shaped, s.get("files", 0)), s, n))
    key, s, n = max(scored, key=lambda x: x[0])
    named = s.get("shape")
    if has_shape(s) and named not in SHAPED:
        named = "linked"   # the scan's rule found the shape (an index or linked notes); this map's own outline found none
    shape = {"indexed": "an index and notes", "layered": "an index, with sub-folders or typed notes",
             "linked": "notes that link each other, with no index", "flat-log": "a log with no index and no links",
             "single-file": "a single file"}.get(named, named or "unknown shape")
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
           f"{plural(len(starts), 'session')}). Your own `AGENTS.md` files are about {tokens(rules)} of that"
           + (f"; {around} on this laptop." if around else "."))
    if share >= START_RULES_SHARE:
        do = ("Your `AGENTS.md` files are a large part of it. Keep in the `AGENTS.md` at the top only what every task needs; move "
              "the rest into docs it links to, or into an `AGENTS.md` in the folder it applies to.")
    else:
        do = ("Type /context at the start of a session to see what fills it. Then switch off the MCP servers and plugins "
              "this project doesn't use (/mcp, /plugin), or set them up in the one project that needs them.")
    # figures: the numbers the page draws (view_review: the start as pages, their own share lit), so it never parses `saw`
    return {"id": "starting-context", "source": "records", "saw": saw, "do": do,
            "figures": {"tokens": round(med), "own_tokens": round(rules), "sessions": len(starts)},
            "cost": "All of it is read again on every turn of every session, before your task: you pay for it each "
                    "time, and the agent has more to wade through to find what matters."}


# ------------------------------------------------------------------ cards: advice from code's read of the instruction files

def instruction_findings(run, m, home):
    """Code's read (setup_instruction.read_file, no AI, no git blame) of the instruction files the map lists as always loaded
    or not loaded, for each project the map covers: [{slug, root, label, files: [{path, rel, loads, read}]}]. At most
    FREE_FILES files a project, the shallowest first. A project with nothing to read is left out; a file that cannot be
    read is left out and the rest goes on."""
    out = []
    for slug, root, p in projects_of(run, m, home):
        files = []
        entries = [e for e in p.get("instruction_files") or [] if e.get("loads") in ("always", "not_loaded") and "error" not in e]
        entries.sort(key=lambda e: (e["loads"] != "always", e.get("depth", 0), e["path"]))
        try:
            view = setup_instruction.repo_view(root)
        except Exception:  # noqa: BLE001  a repo code cannot list is a project with no free cards
            continue
        for e in entries[:FREE_FILES]:
            path = absolute(e["path"], root, home)
            try:
                r = setup_instruction.read_file(path, root=root, home=home, with_history=False, view=view)
            except Exception:  # noqa: BLE001
                continue
            if not r.get("error"):
                files.append({"path": path, "rel": e["path"], "loads": e["loads"], "read": r})
        if files:
            out.append({"slug": slug, "root": root, "label": os.path.basename(root), "files": files, "view": view})
    return out


def _loaded(project):
    return [f for f in project["files"] if f["loads"] == "always"]


def _line_of(block, raw):
    """The file line of the block's line that holds `raw`."""
    return block["start"] + next((i for i, ln in enumerate(block["text"].split("\n")) if raw in ln), 0)


def _and_list(items):
    items = [f"`{x}`" for x in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def card_dead_pointer(found):
    """A line in a file every session loads sends the agent to a file that is not in the repo."""
    for pr in found:
        dead = []
        for f in _loaded(pr):
            for b in f["read"]["blocks"]:
                for q in b.get("pointers") or []:
                    if not q["exists"] and not q["raw"].endswith("/"):
                        dead.append((f["rel"], _line_of(b, q["raw"]), q["raw"], q.get("near")))
        if not dead:
            continue
        rel, line, raw, near = dead[0]
        more = f"; {len(dead) - 1} more pointer{'s' if len(dead) > 2 else ''} in your `AGENTS.md` files are as dead" if len(dead) > 1 else ""
        return {"id": "dead-pointer", "source": "files",
                "saw": f"`{rel}` line {line} sends the agent to `{raw}`, which is not in the repo"
                       + (f" (closest: `{near}`)" if near else "") + more + ".",
                "cost": "The agent opens a file that is not there, then guesses what it held.",
                "do": f"Point the line at `{near}`, or delete it." if near else "Point the line at the file that holds what it meant, or delete it."}
    return None


def card_agents_md_not_loaded(found):
    """An `AGENTS.md` that Claude Code never reads, because a CLAUDE.md sits at or above it and imports none of it."""
    for pr in found:
        for f in pr["files"]:
            if f["loads"] != "not_loaded" or os.path.basename(f["path"]) != "AGENTS.md":
                continue
            state, other = setup_instruction.agents_md_state(f["path"], pr["root"], None)
            if state != "shadowed" or not other:
                continue
            rel_other = os.path.relpath(other, pr["root"])
            name, lines = f["rel"], f["read"]["lines"]
            beside = os.path.dirname(other) == os.path.dirname(f["path"])
            if os.path.basename(other) == "CLAUDE.local.md":
                do = ("Add a `CLAUDE.md` whose first line is `@AGENTS.md`: while a `CLAUDE.local.md` sits there on its own, Claude Code "
                      "does not read `AGENTS.md`.")
            else:
                at = os.path.relpath(f["path"], os.path.dirname(other))
                do = (f"Put `@{at}` first in `{rel_other}` and keep below it only what Claude Code alone needs." if beside else
                      f"Add `@{at}` to `{rel_other}`, or move what `{name}` holds into it.")
            others = sum(1 for g in pr["files"] if g["loads"] == "not_loaded") - 1
            return {"id": "agents-md-not-loaded", "source": "files", "file": f["rel"], "figures": {"lines": lines},
                    "saw": f"`{name}` ({plural(lines, 'line')}) sits {'beside' if beside else 'below'} `{rel_other}`, which does not import it, "
                           f"so Claude Code reads `{rel_other}` only" + (f"; {others} more `AGENTS.md` files are in the same position" if others > 0 else "") + ".",
                    "cost": f"What you wrote in `{name}` never reaches the agent in a Claude Code session.", "do": do}
    return None


CHECK_SCRIPT = re.compile(r"(?i)^(tests?|lint|typecheck|type-check|check|verify|validate|e2e|pytest|tsc)(?:[:\-_.]|$)|[:\-_](?:test|lint|check|e2e|typecheck)$")
CHECK_FIRST = ("test", "lint", "typecheck", "type-check", "check")


def _runner(facts, source, script):
    """The command that runs a script of the repo, when its manifest says how: package.json by the lockfile's tool, Makefile and
    justfile by their own. None for anything else: a card names no command it cannot name exactly."""
    name = source.split(":")[0]
    if name == "package.json":
        pm = next((t for t in ("pnpm", "yarn", "bun", "npm") if t in (facts.get("tools") or {})), "npm")
        return f"{pm} test" if script == "test" else f"{pm} run {script}"
    if name in ("Makefile", "makefile"):
        return f"make {script}"
    if name in ("justfile", ".justfile"):
        return f"just {script}"
    return None


def card_no_check_command(found):
    """No line of the files every session loads names a command that checks the work, while the repo has scripts that do."""
    for pr in found:
        loaded = _loaded(pr)
        if not loaded or any(f["read"]["check_commands"] for f in loaded):
            continue
        facts = pr["view"][0]
        scripts = [s for s in facts["scripts"] if CHECK_SCRIPT.search(s)]
        if not scripts:
            continue
        scripts.sort(key=lambda s: (CHECK_FIRST.index(s) if s in CHECK_FIRST else
                                    next((len(CHECK_FIRST) + i for i, w in enumerate(CHECK_FIRST) if s.startswith(w)), 99), s))
        shown = scripts[:4]
        sources = sorted({facts["scripts"][s].split(":")[0] for s in shown})
        where = f"`{sources[0]}`" if len(sources) == 1 else "the repo"
        target = next((f for f in loaded if os.path.basename(f["path"]) in ("CLAUDE.md", "AGENTS.md")), loaded[0])
        example = _runner(facts, facts["scripts"][shown[0]], shown[0])
        return {"id": "no-check-command", "source": "files", "file": target["rel"],
                "saw": f"No line the agent loads names a command that checks the work; {where} has {_and_list(shown)}.",
                "cost": "The agent decides for itself when a change is checked, and may say it is done without running anything.",
                "do": f"Add one line to `{target['rel']}` naming the command to run before saying the work is done"
                      + (f", for example `{example}`." if example else ".")}
    return None


def card_restates_repo(found):
    """Lines of an always-loaded file that say what the repo's own files already say (stack, versions, layout)."""
    for pr in found:
        best = None
        for f in _loaded(pr):
            lines = [b for b in f["read"]["blocks"] if b.get("judge")]
            same = [b for b in lines if b.get("restates_repo")]
            if len(same) >= RESTATES_MIN_LINES and (best is None or len(same) > len(best[1])):
                best = (f, same, len(lines))
        if not best:
            continue
        f, same, n = best
        names = list(dict.fromkeys(k for b in same for k in b.get("found") or {}))[:5]
        srcs = list(dict.fromkeys(str(v).split(":")[0] for b in same for v in (b.get("found") or {}).values()))
        srcs = [x for x in srcs if not x.startswith("folder ")]
        where = _and_list(srcs[:2]) if 1 <= len(srcs) <= 2 else "the repo's own files"
        k, nums = len(same), [str(b["start"]) for b in same]
        return {"id": "restates-repo", "source": "files", "file": f["rel"], "figures": {"lines": k, "of": n},
                "saw": f"{k} of the {n} lines in `{f['rel']}` say{'s' if k == 1 else ''} what {where} already say{'s' if len(srcs) == 1 and where.startswith('`') else ''}"
                       f" ({', '.join(names)}).",
                "cost": "They load in every session, and go stale when a version moves.",
                "do": f"Delete {'it' if k == 1 else 'them'} (line{'s' if k > 1 else ''} {' and '.join(nums) if k <= 2 else ', '.join(nums[:-1]) + ' and ' + nums[-1]}): "
                      "the agent reads the repo itself."}
    return None


def plus_signals(found):
    """{slug: [what a project's files do well]}: XML tags, a private CLAUDE.local.md, sections named by the task, numbered
    steps, an opening that says what the file is for. Shown on the project's line, never a card and never a minus: a file
    without them loses nothing."""
    out = {}
    for pr in found:
        plus = list(dict.fromkeys(x for f in _loaded(pr) for x in f["read"]["structure"]["plus"]))
        if plus:
            out[pr["slug"]] = plus
    return out



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
                    "agent starts passing over instructions you care about.",
            "do": f"Read {name} top to bottom. Delete what the agent gets right without being told, and move what "
                  "only one area needs into an `AGENTS.md` in that folder."}


def cards(run, inv, m, hold, home, found=None, lines=None):
    """Advice, the largest cost first: what a session loses (its scope), what every turn pays (how full it starts), what
    sends the agent wrong (a pointer to a file that is not there), what an `AGENTS.md` that only grows or is never read
    costs, what is missing (no command that checks the work), then what merely adds clutter (lines that restate the repo,
    setup nobody uses). Counted from Claude Code's own records or by code from the person's files (`found`,
    instruction_findings: no AI). At most MAX_CARDS show. A fired hold is the session-scope card: it takes that card's
    cost and next step, and no second card is made. This order is a proposal for the owner to confirm (PRODUCT.md (c))."""
    chats = chats_of(run)
    long = long_sessions(chats)
    if hold.get("fired"):
        hold.update(cost=SCOPE_COST, do=SCOPE_DO)
        if long:
            hold["why"] += "; " + longest_said(long[0])
    days = _num(((inv or {}).get("meta") or {}).get("days")) or 30
    found = instruction_findings(run, m, home) if found is None else found
    makers = ((lambda: None if hold.get("fired") else card_session_scope(chats)),
              (lambda: card_starting_context(chats, load(run, "anatomy.json"), home)),
              (lambda: card_dead_pointer(found)),
              (lambda: card_weakest_line(lines or {})),
              (lambda: card_rules_only_grow(inv, own_always_loaded(m, home), home)),
              (lambda: card_agents_md_not_loaded(found)),
              (lambda: card_no_check_command(found)),
              (lambda: card_restates_repo(found)),
              (lambda: card_unused_setup(board_rows(run), days)))
    out = []
    for make in makers:
        try:
            c = make()
        except Exception:  # noqa: BLE001  a record in a shape this card did not expect is a card not shown
            c = None
        if c:
            out.append(c)
    # when "rules only grow" fires on the file the weakest line is in, the line is that card's example, not a second card
    grow = next((c for c in out if c["id"] == "rules-only-grow"), None)
    weak = next((c for c in out if c["id"] == "weakest-line"), None)
    if grow and weak and grow["saw"].startswith(tilde(weak["file"], home) + " loads in every session"):   # the card names its file first
        out.remove(weak)
        grow["do"] += f" Start with this line: \u201c{weak['quote']['text']}\u201d. {weak['do']}"
        grow["quote"] = weak["quote"]
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


PART_SAYS = {"rules": "instructions the agent starts with", "corrections": "corrections that stick",
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
    per, projects, ran, checked, craft_answer = {pid: [] for pid, _ in PARTS}, [], False, {}, None
    for s, root, p in projects_of(run, m, home):
        doc = load(run, f"anatomy-ai-{s}.json") or {}
        if not doc:   # a reader may have named its file by the folder, not the slug
            for path in glob.glob(os.path.join(run, "anatomy-ai-*.json")):
                d = load(run, os.path.basename(path)) or {}
                if os.path.normpath(str(d.get("project") or "")) == root:
                    doc = d
        sr = doc.get("setup_read")
        sr = sr if isinstance(sr, dict) else {}
        side = load(run, f"instruction-lines-{s}.json")
        if side:
            checked[s] = check_lines(side, sr.get("lines"))
        if craft_answer is None:      # the craft job was in the first project's brief
            craft_answer = sr.get("crafts") if isinstance(sr.get("crafts"), list) else []
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
    found = instruction_findings(run, m, home)
    plus = plus_signals(found)
    for pj in projects:
        if plus.get(pj["slug"]):
            pj["plus"] = plus[pj["slug"]]
    for pj in projects:
        if pj["slug"] in checked:   # what the reader's line-by-line answer came to, after code's check: words, no number is shown
            pj["lines"] = {k: v for k, v in checked[pj["slug"]].items() if v not in (None, "")}
    out = {"ran": ran, "parts": parts, "holds": [hold], "cards": cards(run, inv, m, hold, home, found, checked), "projects": projects}
    out["opening"] = opening(projects, first_change(out)) if ran else []
    with open(os.path.join(run, "setup-read.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    try:   # advice only, in its own file: nothing in the engine opens craft-read.json
        setup_craft.merge(run, craft_answer, home)
    except Exception as e:  # noqa: BLE001  the craft read never stops the setup read
        print(f"craft read: not written ({type(e).__name__})")
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
