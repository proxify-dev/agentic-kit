#!/usr/bin/env python3
"""The craft read in the tool (11-12; the trial and its sources: ../2026-11-12-craft-read/): for the person's most used
skills, sub-agents, hooks, eval suites and plugins, how well each is made (a step of six, from the owner's playbook) and
what it did in 30 days, with the one change that would move it up. Advice only: `craft-read.json` is read by the page and
by nothing in the engine (test_craft_read_moves_no_level.py).

    setup_read.brief  -> job_section(run): the crafts that need a reader's judgement, as a third job in the first brief
    setup_read.merge  -> merge(run): code's placement, plus the reader's checked answer -> RUN_DIR/craft-read.json

Steps are cumulative (the owner's ruling, 11-12): a craft sits on a step only when it passes every lower step's test.
Code places what it can prove: whether the file loads, a hook's matcher and block, an eval suite's assertions and runner,
a plugin from its parts. Whether a skill says when and is a procedure, whether a guard names what to do instead, whether a
craft checks its own result: the reader answers, with a quote code finds in the craft's own file; an empty or malformed
answer reads "couldn't check" and the craft keeps the step code proved.

What it did is what it caused, counted by the scan from Claude Code's own records (inventory.json), never by the reader:
a skill's loads and whether its own script ran after one; a sub-agent's runs of its type; a hook's own runs and blocks;
an eval suite's runs in its ledger (read from files); a plugin's parts' runs. Never the files and commands that followed
a skill in the turn (the trial credited a read-only guidance skill with 35 files written). Agent files named after one of
Claude Code's own agent types are the built-in's (board.BUILT_IN_AGENTS). Standard library only; read-only.
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import setup_map  # noqa: E402  frontmatter, as_list, MD_LINK, scrub, has_secret
import setup_instruction  # noqa: E402  quote_in (the setup read's quote check), read_text, DONE
ENGINE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "v15", "engine"))
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)
import board  # noqa: E402  BUILT_IN_AGENTS: Claude Code's own agent types
EVIDENCE = os.path.normpath(os.path.join(HERE, "..", "2026-11-02-evidence"))
if EVIDENCE not in sys.path:
    sys.path.insert(0, EVIDENCE)
from wording import plural  # noqa: E402

# How many of each kind, most used first. Guesses until the read runs on member laptops (setup-snapshot CLAUDE.md).
PICK = {"skill": 5, "agent": 3, "hook": 2, "eval": 2, "plugin": 3}
READER_MAX = 10          # crafts one batched reader judges
TEXT_LINES = 80          # lines of a craft's own file the reader is given (it may open the rest)
TEXT_CHARS = 5000
CODE_EXT = (".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php", ".go", ".lua", ".ps1")

STEPS = {
    "skill": ("A note", "A procedure", "With its own reference files", "Runs its own code", "Checks its own result", "Tested by evals"),
    "agent": ("A vague helper", "A narrow job", "Fitted to its job", "Runs its own code", "Checks its own result", "Tested by evals"),
    "hook": ("A note at an event", "Fires where it applies", "A guard", "A guard that says what to do instead",
             "Checks the work and feeds it back", "Tested the way Claude Code runs it"),
    "eval": ("Prompts only", "Checks the answer", "Checks how it got there", "Runs from one command", "Has a pass bar",
             "Compared with a baseline"),
    "plugin": ("Parts packaged side by side", "Parts that work together", "With its own wiring", "Runs its own code", "Checks its own result",
               "Tested by evals"),
}
# what passing the next step takes, said to the person: the change when code alone placed it
NEXT = {
    "skill": ("Say in its description when to use it, then give the steps in order, each ending on something the agent can check.",
              "Move detail that only some cases need into files of its own folder, linked by the task that needs them.",
              "Give it a script of its own that the steps run, so the result is the same every time.",
              "End on a check the agent runs (a test, a validator) whose result decides it is done.",
              "Add evals that run it, and run them."),
    "agent": ("Name in its description the requests to hand it, and give it one job and what it returns.",
              "List in its frontmatter only the tools the job needs.",
              "Give it a script of its own to run, and Bash to run it.",
              "Have it check what it produced before it returns, and say what it ran.",
              "Run it on fixed cases (`claude -p --agent`) and keep the cases."),
    "hook": ("Make it act only in the case its rule is about: a matcher, or a test on the event's input.",
             "Let it stop the action when the rule is broken (exit 2, or a deny).",
             "When it stops the action, name the command or file to use instead.",
             "Run the check on what the agent just produced, and return only the failures so it keeps working.",
             "Add a test that pipes a real event into its command, and run it."),
    "eval": ("Say in each case what a right result contains, and check it.",
             "Also check how the agent got there: the first tool, tools it must not call, turns.",
             "Give it one command that runs every case and scores it.",
             "Set a pass bar before running (how many runs of how many must pass).",
             "Run each case again without the thing under test, to see what it adds."),
    "plugin": ("Add a second part that the first one names (a skill that starts its agent or runs its command).",
               "Wire its parts in with a hook of its own, at the moment they apply.",
               "Give a part a script of its own in `bin/` that the skill or agent runs.",
               "Make one part check its own result (a Stop hook, or a last step that runs a test).",
               "Add evals for it and run them."),
}
STEPS["command"], NEXT["command"] = STEPS["skill"], NEXT["skill"]
KIND_WORD = {"skill": "Skill", "command": "Command", "agent": "Sub-agent", "hook": "Hook", "eval": "Evals", "plugin": "Plugin"}

WHEN_CUE = re.compile(r"(?i)(\buse (?:this |it )?(?:when|whenever|for|to|before|after|if|proactively)\b|\bwhen(?:ever)?\b|\btriggers? (?:on|when)\b"
                      r"|\bbefore (?:you|a|the|committing|merging)\b|\bafter (?:you|a|the|each)\b|\basks? (?:to|for|about)\b|\bdelegate\b)")
STEP_LINE = re.compile(r"(?i)^\s*(?:\d+[.)]\s+\S|#{1,4}\s*(?:step\s*\d+|\d+[.)]\s)|<step\b)")
CHECK_RUN = re.compile(r"(?i)(\bpytest\b|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|lint|typecheck|check)\b|\btsc\b|--check\b|\bunittest\b"
                       r"|\bvalidate\b|\bverify\b|\bruff\b|\beslint\b|\bmypy\b|\bmake\s+(?:test|check|lint)\b)")
BLOCK_CUE = re.compile(r"(\bexit\s+2\b|sys\.exit\(\s*2\s*\)|process\.exit\(\s*2\s*\)|[\"']decision[\"']\s*:\s*[\"']block[\"']"
                       r"|permissionDecision[\"']?\s*[:=]\s*[\"']deny)")
INPUT_CUE = re.compile(r"(json\.load\(sys\.stdin|sys\.stdin\.read|\bjq\b|readFileSync\(0|process\.stdin|tool_input)")
ANSWER_KEYS = re.compile(r"[\"'](?:must|expected?|expect\w*|output_format|content_contains|file_exists|assertions?|graders?|expectations"
                         r"|must_contain|should_contain|answer|gold)[\"']\s*:|^\s*(?:must|expected?|graders?|assertions?)\s*:", re.M)
PATH_KEYS = re.compile(r"[\"'](?:max_turns|max_tool_calls|tool_not_used|tools?_used|tool_order|bash_contains|agent_count|spawn_order"
                       r"|ideal_first|first_tool|must_call|must_not_call)[\"']\s*:|^\s*(?:max_turns|tool_order|ideal_first|must_call)\s*:", re.M)


def load(run, name):
    try:
        with open(os.path.join(run, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def text(s, n=300):
    return setup_map.scrub(re.sub(r"\s+", " ", str(s or "")).strip(), n)


def display(path, home):
    p = os.path.realpath(path) if path else ""
    return "~" + p[len(home):] if home and p.startswith(home + os.sep) else p


# ------------------------------------------------------------------ which crafts: theirs, most used, from the scan

def _plugin_root(path):
    d = os.path.dirname(path)
    while d and d not in ("/",):
        if os.path.isfile(os.path.join(d, ".claude-plugin", "plugin.json")):
            return d
        if os.path.isdir(os.path.join(d, ".git")):
            return None
        d = os.path.dirname(d)
    return None


_PLUGIN_CACHE = re.compile(r"^.*/plugins/cache/[^/]+/([^/]+)/[^/]+/(.+)$")


def _source_of(path, set_up_by, inv, roots):
    """A file Claude Code ran from its plugin storage (`plugins/cache/<marketplace>/<plugin>/<version>/<rel>`), as the same file in
    the source folder of the person's own plugin that set it up, when that file is there; else the path as it is. The source
    folder is what the scan recorded for the plugin (inventory `plugins`: `source_repo_dir`, theirs by ownership.py), or the
    folder of one of their own plugin manifests of that name. A hook of their own plugin is shown and read from their repo,
    the one place it is theirs, and counted once."""
    m = _PLUGIN_CACHE.match(path)
    if not m:
        return path
    dirs = [p.get("source_repo_dir") for p in inv.get("plugins") or []
            if isinstance(p, dict) and p.get("key") == set_up_by and p.get("yours") and p.get("source_repo_dir")]
    dirs += [root for root, name in roots.items() if name == m.group(1)]
    for d in dirs:
        src = os.path.join(os.path.expanduser(d), m.group(2))
        if os.path.isfile(src):
            return src
    return path


def crafts_of(run, home):
    """The person's own crafts with what the scan counted they did, most used first per kind (PICK of each).
    Theirs = anatomy's origin "own" (ownership.py decided), minus agent files named after Claude Code's own agent types."""
    anat, inv = load(run, "anatomy.json") or {}, load(run, "inventory.json") or {}
    arts = [a for a in anat.get("artifacts") or [] if a.get("origin") == "own" and not a.get("in_fixture_path") and a.get("path")]
    depth = {s.get("name"): s for s in inv.get("skill_depth") or [] if isinstance(s, dict)}
    types = {}
    for t in (inv.get("helpers") or {}).get("by_type") or []:
        if isinstance(t, dict) and t.get("type"):
            types[t["type"]] = t
            types.setdefault(str(t["type"]).rsplit(":", 1)[-1], t)
    roots = {}      # the folders of the person's own plugins: where a plugin's source is
    for a in arts:
        if a["kind"] == "plugin_manifest":
            r = os.path.dirname(os.path.dirname(a["path"]))
            roots.setdefault(r, a.get("name") or os.path.basename(r))
    out = []
    seen = set()
    for a in arts:
        kind, name, path = a["kind"], a.get("name") or "", a["path"]
        if kind not in ("skill", "command", "agent") or (kind, name) in seen:
            continue
        if kind == "agent" and name in board.BUILT_IN_AGENTS:
            continue    # the built-in's: runs of that type are Claude Code's, whatever file overrides it
        seen.add((kind, name))
        if kind == "agent":
            t = types.get(name) or {}
            if t.get("agent_file") and os.path.realpath(t["agent_file"]) != os.path.realpath(path):
                t = {}
            did = {"runs": int(t.get("calls") or 0), "sessions": int(t.get("chats") or 0)}
            use = did["runs"]
        else:
            d = depth.get(name) or {}
            did = {"loads": int(d.get("loads") or 0), "script": d.get("script_run"), "evals": bool(d.get("eval")),
                   "shared": bool(d.get("shared_name"))}
            use = did["loads"]
        out.append({"kind": kind, "name": name, "path": path, "use": use, "did": did})
    for src in ("typed_chats", "headless"):     # hooks: the scan's runs per hook script, set up by which plugin or settings file
        for h in (inv.get("hook_runs") or {}).get(src) or []:
            if not (isinstance(h, dict) and h.get("yours") and h.get("script")):
                continue
            key = ("hook", h.get("set_up_by"), os.path.basename(h["script"]))
            c = next((x for x in out if x.get("key") == key), None)
            if c is None:
                c = {"kind": "hook", "name": os.path.basename(h["script"]), "key": key, "path": _source_of(os.path.expanduser(h["script"]), h.get("set_up_by"), inv, roots),
                     "events": [], "plugin": h.get("set_up_by"), "use": 0, "did": {"runs": 0, "sessions": 0, "headless": 0}}
                out.append(c)
            c["events"].append(h.get("event"))
            c["did"]["runs"] += int(h.get("runs") or 0)
            c["did"]["sessions"] += int(h.get("chats") or 0)
            c["did"]["headless"] += int(h.get("runs") or 0) if src == "headless" else 0
            c["use"] = c["did"]["runs"]
    for e in sorted((x for x in inv.get("evals") or [] if isinstance(x, dict)), key=lambda x: -int(x.get("ledger_lines_in_window") or 0)):
        nm = f"{os.path.basename(os.path.dirname(str(e.get('path'))))}/{os.path.basename(str(e.get('path')))}"
        if any(c["kind"] == "eval" and c["name"] == nm for c in out):
            continue    # the same suite in another checkout (a worktree) is one suite
        if e.get("yours") and e.get("path"):
            n = int(e.get("ledger_lines_in_window") or 0)
            out.append({"kind": "eval", "name": f"{os.path.basename(os.path.dirname(e['path']))}/{os.path.basename(e['path'])}",
                        "path": e["path"], "use": n, "did": {"ledger_runs": n, "ledgers": e.get("ledgers") or [], "last": e.get("last_run")}})
    for r, nm in roots.items():
        parts = [c for c in out if c["kind"] != "plugin" and os.path.realpath(c["path"]).startswith(os.path.realpath(r) + os.sep)]
        hooks = [c for c in out if c["kind"] == "hook" and str(c.get("plugin") or "").split("@")[0] == nm]
        parts += [h for h in hooks if h not in parts]
        if not parts:
            continue
        out.append({"kind": "plugin", "name": nm, "path": r, "parts": parts, "use": sum(p["use"] for p in parts),
                    "did": {k: sum(p["use"] for p in parts if p["kind"] == k) for k in ("skill", "command", "agent", "hook", "eval")}})
    picked = []
    for kind, n in PICK.items():
        pool = sorted((c for c in out if (c["kind"] in ("skill", "command") if kind == "skill" else c["kind"] == kind) and c["use"] > 0),
                      key=lambda c: -c["use"])
        picked += pool[:n]
    return picked


# ------------------------------------------------------------------ what code reads in a craft's own files

def md_facts(c):
    """A skill, command or agent file: whether it loads, its description, steps, linked files of its own, scripts and
    bin commands it runs, a check command it names, an agent's tools."""
    entry = os.path.join(c["path"], "SKILL.md") if os.path.isdir(c["path"]) else c["path"]
    if c["kind"] == "skill" and not os.path.isfile(entry) and os.path.isfile(c["path"]):
        entry = c["path"]
    raw = setup_instruction.read_text(entry) or ""
    lines = raw.split("\n")
    fm_ok = bool(lines) and lines[0].strip() == "---" and any(x.strip() in ("---", "...") for x in lines[1:300])
    fm, start = setup_map.frontmatter(lines)
    body = "\n".join(lines[start:])
    desc = fm.get("description")
    desc = " ".join(desc) if isinstance(desc, list) else str(desc or "")
    folder = os.path.dirname(entry) if c["kind"] == "skill" else None
    own = []
    if folder and os.path.isdir(folder):
        for dp, dn, fn in os.walk(folder):
            dn[:] = [x for x in dn if not x.startswith(".") and x not in ("node_modules", "__pycache__")]
            own += [os.path.relpath(os.path.join(dp, f), folder) for f in fn if f not in ("SKILL.md", ".DS_Store")]
            if len(own) > 300:
                break
    named = {r for r in own if r in body or (len(os.path.basename(r)) > 6 and os.path.basename(r) in body)}
    scripts = [r for r in own if r.endswith(CODE_EXT) or r.split(os.sep)[0] in ("scripts", "bin")]
    refs = [r for r in own if r not in scripts and r.lower().endswith((".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".html"))
            and not set(r.split(os.sep)[:-1]) & {"evals", "tests", "test"}]
    plugin = _plugin_root(entry)
    bins = set(os.listdir(os.path.join(plugin, "bin"))) if plugin and os.path.isdir(os.path.join(plugin, "bin")) else set()
    spans = re.findall(r"`([^`\n]+)`", body) + re.findall(r"```[^\n]*\n([\s\S]*?)```", body)
    bins_named = sorted({b for s in spans for b in bins if re.match(rf"\s*\$?\s*{re.escape(b)}(\s|$)", s)})
    tools = setup_map.as_list(fm.get("tools")) if "tools" in fm else None
    return {"entry": entry, "text": raw, "loads": fm_ok and bool(desc.strip()) and (c["kind"] != "agent" or bool(fm.get("name"))),
            "description": desc, "when_cue": bool(WHEN_CUE.search(desc)), "steps": sum(1 for x in body.split("\n") if STEP_LINE.match(x)),
            "refs_linked": sorted(r for r in refs if r in named), "scripts_named": sorted(r for r in scripts if r in named),
            "bins_named": bins_named, "live": bool(re.search(r"(?m)(?<![\w`])!`[^`\n]+`|^```!", body)),
            "check_run": bool(CHECK_RUN.search(body)), "tools": tools,
            "fitted": tools is not None or "disallowedTools" in fm or any(fm.get(k) for k in ("skills", "memory", "mcpServers")),
            "has_bash": tools is None or any(t.split("(")[0].strip() == "Bash" for t in tools)}


def hook_facts(c):
    raw = setup_instruction.read_text(c["path"]) or ""
    code = "\n".join(x for x in raw.split("\n") if not x.lstrip().startswith(("#", "//")))
    tests = []
    plugin = _plugin_root(c["path"])
    for top in filter(None, (plugin,)):
        for d in ("tests", "test"):
            for dp, dn, fn in os.walk(os.path.join(top, d)):
                for f in fn:
                    p = os.path.join(dp, f)
                    if f.endswith(CODE_EXT) and (c["name"].rsplit(".", 1)[0] in f or c["name"] in (setup_instruction.read_text(p) or "")):
                        tests.append(p)
    return {"entry": c["path"], "text": raw, "loads": bool(raw), "reads_input": bool(INPUT_CUE.search(code)),
            "matcher": any(":" in str(e) for e in c.get("events") or []), "blocks": bool(BLOCK_CUE.search(code)),
            "check_run": bool(CHECK_RUN.search(code)), "tests": tests}


def eval_facts(c):
    texts, runners = {}, []
    for dp, dn, fn in os.walk(c["path"]):
        dn[:] = [x for x in dn if not x.startswith(".") and x not in ("__pycache__", "node_modules", "runs")]
        for f in fn:
            p = os.path.join(dp, f)
            if f.endswith((".json", ".yaml", ".yml", ".md", ".py", ".js", ".ts", ".sh")) and len(texts) < 300:
                texts[p] = setup_instruction.read_text(p) or ""
                if f.endswith(CODE_EXT) and re.match(r"(?i)(run|runner|bench|eval)\w*\.", f):
                    runners.append(p)
    alltext = "\n".join(texts.values())
    readme = next((p for p in texts if os.path.basename(p).lower() == "readme.md"), None) or (sorted(texts)[0] if texts else c["path"])
    return {"entry": readme, "text": texts.get(readme, ""), "loads": bool(texts), "answer": bool(ANSWER_KEYS.search(alltext)),
            "path_checks": bool(PATH_KEYS.search(alltext)), "runner": [os.path.relpath(p, c["path"]) for p in runners[:3]],
            "files": sorted(texts)}


# ------------------------------------------------------------------ the steps: cumulative, code first

def tests_of(c, f, crit=None):
    """The tests of steps 2-6 in order: True, False, or None when only the reader can say (and has not, or its quote
    was not found). crit: the reader's checked answers {says_when, procedure, returns, checks_result} -> True/False."""
    crit = crit or {}
    r = lambda q: crit.get(q)  # noqa: E731  None until the reader answered and code found its quote
    k, d = c["kind"], c.get("did") or {}
    if k in ("skill", "command"):
        t2 = (r("says_when") and r("procedure")) if crit else None
        return [t2, bool(f["refs_linked"]), bool(f["scripts_named"] or f["bins_named"] or f["live"]),
                r("checks_result"), bool(d.get("evals"))]
    if k == "agent":
        t2 = (r("says_when") and r("procedure") and r("returns")) if crit else None
        return [t2, f["fitted"], bool(f["bins_named"] or f["scripts_named"]) and f["has_bash"], r("checks_result"), False]
    if k == "hook":
        return [f["matcher"] or f["reads_input"], f["blocks"], r("returns"), (r("checks_result") if f["check_run"] else False),
                False]
    if k == "eval":
        return [f["answer"], f["path_checks"], bool(f["runner"]), r("returns"), r("checks_result")]
    return []


def place(tests, loads=True):
    """(step, why_stopped): the highest step whose test and every lower one hold. why_stopped: "fails" (the next test is
    false), "reader" (only a reader can say), or "top"."""
    if not loads:
        return 1, "fails"
    s = 1
    for t in tests:
        if t is True:
            s += 1
        else:
            return s, ("reader" if t is None else "fails")
    return s, "top"


def plugin_place(c, placed):
    """A plugin from its parts (no reader of its own): one part is that part, packaged; above that, parts that name each
    other, hooks of its own, code of its own, a part that checks its result, evals that ran."""
    parts = c["parts"]
    if len(parts) < 2:
        return 1, "fails", "one part, packaged"
    names = {p["name"] for p in parts if len(p["name"]) > 3}
    together = False
    for p in parts:
        t = setup_instruction.read_text(p["path"] if os.path.isfile(p["path"]) else os.path.join(p["path"], "SKILL.md")) or ""
        if any(n != p["name"] and re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", t) for n in names):
            together = True
            break
    bins = os.path.isdir(os.path.join(c["path"], "bin"))
    steps = [placed.get(id(p), (1,))[0] for p in parts]
    tests = [together, any(p["kind"] == "hook" for p in parts), bins or any(p["kind"] == "hook" for p in parts),
             any(s >= 5 for p, s in zip(parts, steps) if p["kind"] != "eval"),
             any(p["kind"] == "eval" and (p.get("did") or {}).get("ledger_runs") for p in parts)
             or any((p.get("did") or {}).get("evals") for p in parts)]
    s, why = place(tests)
    return s, why, ""


# ------------------------------------------------------------------ the words the page prints (no number the reader counted)

def step_words(kind, s, why):
    """The step in words. A skill or sub-agent that code could not take past step 1 is "not placed": whether its
    description says when is a reader's call, and step 1 would be a verdict nobody made."""
    names = STEPS[kind]
    if s == 1 and why == "reader" and kind in ("skill", "command", "agent"):
        first = "it says when to use it and gives steps" if kind != "agent" else "it names the requests to hand it and one job"
        return f"Not placed yet: whether {first} is a reader's call, and no reader answered for it"
    out = f"{names[s - 1]} (step {s} of 6)"
    if s < 6:
        out += f"; next: {names[s].lower()}" + (", which a reader has to judge" if why == "reader" else "")
    return out


def did_words(c):
    """What it caused in 30 days, from the scan's counts of Claude Code's records."""
    k, d = c["kind"], c.get("did") or {}
    if k in ("skill", "command"):
        if d.get("shared"):
            return "Another skill has the same name, so its uses can't be told apart"
        out = f"Loaded {plural(d.get('loads', 0), 'time')}"
        if d.get("script"):
            out += f"; its own script `{os.path.basename(str(d['script']))}` ran after a load"
        if d.get("evals"):
            out += "; its evals were seen running"
        return out + "."
    if k == "agent":
        return f"Ran {plural(d.get('runs', 0), 'time')} in {plural(d.get('sessions', 0), 'session')}."
    if k == "hook":
        out = f"Ran {plural(d.get('runs', 0), 'time')} in {plural(d.get('sessions', 0), 'session')}"
        return out + (f", {d['headless']:,} of them with nobody typing." if d.get("headless") else ".")
    if k == "eval":
        if not d.get("ledger_runs"):
            return "No run in its ledger in 30 days."
        return (f"{plural(d['ledger_runs'], 'run')} in its ledger in 30 days, last on {d.get('last')} "
                f"(read from {', '.join(d.get('ledgers') or [])[:80]})." )
    parts = [f"{plural(n, w)}" for w, n in (("skill load", d.get("skill", 0) + d.get("command", 0)), ("sub-agent run", d.get("agent", 0)),
                                             ("hook run", d.get("hook", 0)), ("eval run", d.get("eval", 0))) if n]
    return ("Its parts: " + ", ".join(parts) + ".") if parts else "Its parts were not used in 30 days."


def kind_words(c):
    if c["kind"] != "plugin":
        return KIND_WORD[c["kind"]]
    n = {}
    for p in c["parts"]:
        n[p["kind"]] = n.get(p["kind"], 0) + 1
    return "Plugin: " + ", ".join(plural(v, {"skill": "skill", "command": "command", "agent": "sub-agent", "hook": "hook",
                                               "eval": "eval suite"}[k]) for k, v in sorted(n.items(), key=lambda kv: -kv[1]))


# ------------------------------------------------------------------ the reader's job (one batched run, in the first brief)

JOB = """
### The crafts (a third job, in the same answer file)

Below are up to {n} things this person built for their agents (skills, sub-agents, hooks, eval suites), with what code found.
For each, answer four questions `yes` or `no`, judged by the owner's playbook; quote every yes with words copied exactly from
the craft's own file (at least 20 characters). Code looks each quote up in that file: a quote it can't find does not count.
Judge what the words make an agent do, not who wrote them or how long they are. Open a craft's other files only when an
answer turns on them, and at most three in all. Never write a person's name.

- skill or command: `says_when`: its description names situations or requests to load it in, first (a topic label is no);
  `procedure`: the body gives steps in order, or sends each task to the file that holds it, ending on something checkable;
  `returns`: it says what the result is and where it goes; `checks_result`: it ends on a check the agent runs (a test, a
  validator) whose result decides it is done.
- sub-agent: `says_when`: its description names the requests to hand it (a role such as "expert in X" is no); `procedure`:
  one job with a clear end; `returns`: what it returns and in what shape; `checks_result`: it checks what it produced before
  it returns.
- hook: `returns`: when it stops or warns, its message names what to do instead; `checks_result`: it runs a check on what
  the agent just produced and feeds only the failures back. (`says_when` and `procedure`: answer `no`; code reads them.)
- eval suite: `returns`: a pass bar is set before running (N of M runs, a threshold); `checks_result`: each case also runs
  without the thing under test. (`says_when` and `procedure`: answer `no`; code reads them.)

Then `change`: the one change that would move it up one step, doable in an hour, naming only files, commands and names
in its own files; invent none.

Add `crafts` to `setup_read`: [{{"id": "C1", "says_when": {{"answer": "yes|no", "quote": "..."}}, "procedure": {{...}},
"returns": {{...}}, "checks_result": {{...}}, "change": "..."}}]

{items}"""


def job_section(run, home=None):
    """The reader's third job: the picked crafts whose next step only a reader can judge, at most READER_MAX, each with the
    head of its own file. Writes RUN_DIR/craft-read-side.json (what was asked, and each file's hash) for merge."""
    home = home or os.path.expanduser("~")
    try:
        os.remove(os.path.join(run, "craft-read-side.json"))
    except OSError:
        pass
    picked = crafts_of(run, home)
    items, side = [], {}
    for c in picked:
        if c["kind"] == "plugin" or len(side) >= READER_MAX:
            continue
        f = facts_of(c)
        s, why = place(tests_of(c, f), f["loads"])
        if why != "reader" or not f["text"]:
            continue
        cid = f"C{len(side) + 1}"
        side[cid] = {"kind": c["kind"], "name": c["name"], "path": c["path"], "entry": f["entry"],
                     "sha": hashlib.sha1(f["text"].encode("utf-8", "replace")).hexdigest()}
        head = "\n".join(f["text"].split("\n")[:TEXT_LINES])[:TEXT_CHARS]
        if setup_map.has_secret(head):
            head = "\n".join(x for x in head.split("\n") if not setup_map.has_secret(x))
        found = {"skill": f"{len(f.get('refs_linked') or [])} linked files of its own; scripts it runs: {f.get('scripts_named') or f.get('bins_named') or 'none'}",
                 "agent": f"tools: {f.get('tools') if f.get('tools') is not None else 'every tool'}",
                 "hook": f"wired to {', '.join(c.get('events') or [])}; blocks: {'yes' if f.get('blocks') else 'no'}",
                 "eval": f"runner: {f.get('runner') or 'none'}; checks the answer: {'yes' if f.get('answer') else 'no'}"}
        items.append(f"#### {cid} · {c['kind'] if c['kind'] != 'command' else 'skill'} · {display(f['entry'], home)}\n"
                     f"code found: {found.get('skill' if c['kind'] == 'command' else c['kind'])}\n\n````\n{head}\n````")
    if not side:
        return ""
    with open(os.path.join(run, "craft-read-side.json"), "w", encoding="utf-8") as fh:
        json.dump(side, fh)
    return JOB.format(n=len(side), items="\n\n".join(items))


def facts_of(c):
    return hook_facts(c) if c["kind"] == "hook" else eval_facts(c) if c["kind"] == "eval" else md_facts(c)


def check_answer(side, ans):
    """{cid: {crit, change}} for the answers whose quotes code found in the craft's file; state "couldn't check" when the
    answer is empty or malformed. A file changed since the brief drops that craft's answer."""
    if not isinstance(ans, list) or not ans:
        return "couldn't check", "the reader gave no answer about the crafts", {}
    got = {}
    for a in ans:
        cid = str(a.get("id") or "") if isinstance(a, dict) else ""
        sd = side.get(cid)
        if not sd or cid in got:
            continue
        t = setup_instruction.read_text(sd["entry"]) or ""
        if hashlib.sha1(t.encode("utf-8", "replace")).hexdigest() != sd["sha"]:
            continue
        crit = {}
        for q in ("says_when", "procedure", "returns", "checks_result"):
            v = a.get(q) if isinstance(a.get(q), dict) else {}
            if v.get("answer") == "yes":
                ok, _ = setup_instruction.quote_in(t, v.get("quote"))
                crit[q] = bool(ok)
            else:
                crit[q] = False
        change = text(a.get("change"), 400)
        got[cid] = {"crit": crit, "change": "" if setup_map.has_secret(change) else change}
    if not got:
        return "couldn't check", "no answer about a craft could be checked against its file", {}
    return "read", "", got


# ------------------------------------------------------------------ merge: craft-read.json

def merge(run, answer=None, home=None):
    """Code's placement of the picked crafts, with the reader's checked answer where there is one -> craft-read.json.
    answer: the `crafts` list from the reader's answer file (setup_read.merge passes it), or None."""
    home = home or os.path.expanduser("~")
    side = load(run, "craft-read-side.json") or {}
    state, why, checked = ("not asked", "", {}) if not side else check_answer(side, answer)
    by_path = {(sd["kind"], sd["path"]): (cid, sd) for cid, sd in side.items()}
    picked = crafts_of(run, home)
    rows, placed = [], {}
    for c in [x for x in picked if x["kind"] != "plugin"] + [x for x in picked if x["kind"] == "plugin"]:
        if c["kind"] == "plugin":
            for p in c["parts"]:
                if id(p) not in placed:
                    pf = facts_of(p)
                    placed[id(p)] = place(tests_of(p, pf), pf["loads"])
            s, stop, note = plugin_place(c, placed)
            change, by = (NEXT["plugin"][s - 1] if s < 6 else ""), "code"
        else:
            f = facts_of(c)
            cid, _ = by_path.get((c["kind"], c["path"]), (None, None))
            ans = checked.get(cid) if cid else None
            s, stop = place(tests_of(c, f, ans["crit"] if ans else None), f["loads"])
            placed[id(c)] = (s, stop)
            by = "code and a reader" if ans else "code"
            change = (ans or {}).get("change") or ("" if stop == "reader" or s >= 6 else NEXT[c["kind"]][s - 1])
            if not f["loads"]:
                change = "Start the file with `---` frontmatter holding a `description`: as it is, Claude Code can't load it as written."
        placed_ok = not (s == 1 and stop == "reader" and c["kind"] in ("skill", "command", "agent"))
        rows.append({"kind": c["kind"], "name": c["name"], "where": display(c["path"], home), "kind_words": kind_words(c),
                     "step": s if placed_ok else None, "step_words": step_words(c["kind"], s, stop), "placed_by": by if placed_ok else "",
                     "did_words": did_words(c), "change": change})
    out = {"ran": True, "reader": {"state": state, "why": why, "asked": len(side), "read": len(checked)}, "crafts": rows}
    with open(os.path.join(run, "craft-read.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(f"craft read: {plural(len(rows), 'craft')} placed" + (f"; the reader: {state}" + (f" ({len(checked)} of {len(side)})" if side else "")))
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("merge", "job"):
        sys.exit(__doc__)
    if sys.argv[1] == "job":
        print(job_section(os.path.abspath(sys.argv[2])))
    else:
        merge(os.path.abspath(sys.argv[2]))
