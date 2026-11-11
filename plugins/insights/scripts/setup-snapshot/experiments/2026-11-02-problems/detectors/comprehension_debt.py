#!/usr/bin/env python3
"""Comprehension debt: code ships that nobody checked or understands.

Named problem "comprehension debt" (emerging; the standard term nearby is "automation bias") read as a pattern from one
person's Claude Code logs:

    symptom   -> a `git commit` (main chat or helper) where nothing checked the change after the agent's last edit:
                 no test / build / lint / type check, no running the app or the changed code, no review helper, no diff
                 shown. Commits of docs and memory only are counted apart: they need no test.
    response  -> the share of commits that had a check, by kind of check
    held      -> was a checked commit less often followed, within 2 h, by a fix or revert commit on the same files or by
                 you saying it is broken, than an unchecked one? Compared inside the same kind and size of change.

Reads the raw logs (main chat + helper files) for the calls and what came back; reads the repos read-only
(`git show --name-only`, `git rev-parse`) to learn which files a commit held, and falls back to the files the agent edited
when the commit is gone (worktree removed, history rewritten). Standard library only (members run system python3).
Usage: python3 comprehension_debt.py [--chats FILE] [--out FILE]
"""
import argparse
import functools
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from multiprocessing import Pool

# Apps a person types into (VS Code, desktop, JetBrains) drive Claude Code through the Agent SDK, so Claude Code
# writes the person's own message as promptSource "sdk", like a script's. In their sessions "sdk" is the person.
PERSON_APPS = ("claude-vscode", "claude-desktop", "claude-jetbrains")

ID = "comprehension-debt"
CACHE = os.path.expanduser("~/.cache/setup-snapshot/problems-2026-11-02")
ROOT = os.path.expanduser("~/.claude/projects")
# Hand check of the "no check" flag: 28 flagged commits opened in the raw log (two rounds, 16 + 12), 21 really had no check;
# the 7 others (4 docs-only, 2 checks the first version missed, 1 edit of a log file) are the fixes under checks.dropped.
HAND_CHECKED, HAND_HELD_UP = 28, 21
FOLLOW_UP_WINDOW = 2 * 3600
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
HELPER_TOOLS = {"Agent", "Task", "Workflow"}          # work done elsewhere; it is finished when the result arrives
# a shell command that writes a file of the repo: sed -i, perl -i, a redirect into a path that has a name and an extension
# (not /tmp, not an arrow `=>`), tee, git apply, or an inline script that writes
REDIRECT = re.compile(r"(?<![&\d>=<-])>>?\s*[\"']?(?!/dev/|/tmp/|/private/tmp/|/private/var/|\$\{?TMP)([~./\w$-]*[\w-]\.\w{1,6})\b")
BASH_WRITE = re.compile(r"\bsed\s+-i|\bperl\s+-\w*i|\btee\s+(?!/tmp/|/dev/)|\bgit apply\b|\bpatch\b|write_text|"
                        r"\bwriteFileSync|\bwriteFile\(|\.write\(|open\([^)]*['\"][wa]['\"]")
INTERRUPT = "[Request interrupted by user"
TYPED = ("typed", "queued")
INJECTED = ("<command", "<local", "<task-notif", "<teammate", "<system-rem", "<cross", "Caveat:")
REFUSED = ("doesn't want to proceed", "doesn’t want to proceed")
COMMIT = re.compile(r"(?:^|[;&|\n(]\s*)git\s+(?:-C\s+\S+\s+)?(?:-c\s+\S+\s+)*commit\b")
HASH_BRACKET = re.compile(r"\[([^\]\n]+?) ([0-9a-f]{7,40})\]")
HASH_ONELINE = re.compile(r"(?m)^([0-9a-f]{7,12}) (.+)$")
FIX_MESSAGE = re.compile(r"^\W*(fix(es|ed)?\b|hotfix|revert\b\W+(\"|commit|\w{7,}))", re.I)    # opens like a repair
BROKEN_SAID = re.compile(r"\b(you('ve)? (clearly )?broke|is broken|are broken|it'?s broken|broke (the|it|my)|doesn'?t work|"
                         r"not working|still (fails?|wrong|broken|not working)|regress\w*)\b", re.I)
CORRECTION = re.compile(r"^[\s\W]*(no|nope|nah|don'?t|do not|dont|wrong|not what|stop|wait|why did you|you didn'?t|"
                        r"that'?s not|i said|i told you|undo|revert)\b", re.I)
CODE_EXT = re.compile(r"\.(tsx?|jsx?|mjs|cjs|py|sh|rs|go|java|rb|swift|kt|c|cc|cpp|h|css|html|j2|sql|toml|ya?ml|ipynb)$", re.I)
DOC_PATH = re.compile(r"(\.(md|mdx|txt|rst)$|(^|/)(docs?|memory|\.agents/memory|\.claude/agent-memory[\w-]*|research)/|"
                      r"(^|/)(CHANGELOG|README|LICENSE)|/\.claude/(skills|agents|commands)/)", re.I)

# kinds of check, matched on one shell command (each segment of it)
TEST_BUILD = re.compile(
    r"pytest|py\.test|unittest|\bvitest|\bjest|\bnode\s+(--\S+\s+)*--test|\bnode\s+\S*\.test\.m?js|"
    r"\bnode\s+\S*/test/|(npm|pnpm|yarn|bun) (run )?(-\w+ )*\S*(test|build|lint|check|verify|e2e)\S*|\bjust (test|check|lint|build)|"
    r"\bmake( test| check| lint| build)?\b|\bcargo (test|build|check|clippy)|\bgo (test|build|vet)|\btsc\b|\bmypy\b|"
    r"\bpyright\b|\bruff\b|\beslint\b|\bnext build|\bvite build|\bnode\s+--check|\bbash\s+-n\b|\bpy_compile|"
    r"\bplugin validate|\bshellcheck|\byamllint|\bplaywright test",
    re.I)   # public tools only (11-05: the builder's own recipes were removed; the guard in plugins/agentic-setup fails on them)
RUN_APP = re.compile(r"\bcurl\b|\bplaywright\b|screencapture|"
                     r"\bopen\s+(-a\s+)?\S*(http|localhost|\.html)|localhost:\d+|127\.0\.0\.1|\bnpm (run )?(dev|start)\b|"
                     r"\belectron\b|google chrome|\bvite\b(?! build)", re.I)
DIFF = re.compile(r"\bgit\s+(-C\s+\S+\s+)?(diff|show)\b(?!.*(--stat|--name-only|--name-status|--shortstat|--numstat|"
                  r"--quiet|--check))")
DIFF_STAT = re.compile(r"\bgit\s+(-C\s+\S+\s+)?(diff|show)\b.*(--stat|--name-only|--name-status|--shortstat|--numstat)")
REVIEW_HELPER = re.compile(r"review|verif|audit|critic|check|qa\b|validate|test|grade|bench|measure|judge", re.I)
SCRIPT_RUN = re.compile(r"\b(node|python3?|uv run( --?\S+)*( python3?)?|bash|sh|npx( -y)?( tsx)?|tsx|deno run)\s+(-[\w-]+\s+)*"
                        r"(?!/tmp/|/private/)([~./][\w./~-]*\.(mjs|cjs|js|ts|py|sh)|[\w-]+/[\w./-]*\.(mjs|cjs|js|ts|py|sh)|"
                        r"[\w-]+\.(mjs|cjs|js|ts|py|sh))\b")
IMAGE = re.compile(r"\.(png|jpe?g|webp|gif)$", re.I)


def epoch(ts):
    return datetime.fromisoformat(ts[:23] + "+00:00").timestamp()


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def read_stream(path, helper, start):
    """One raw log file -> ordered events: call, result, typed, interrupt."""
    ev = []
    with open(path, "rb") as fh:
        for line in fh:
            if b'"type":"assistant"' in line:
                if b'"tool_use"' not in line:
                    continue
            elif b'"type":"user"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            ts = r.get("timestamp")
            if not ts:
                continue
            t = epoch(ts)
            if t < start or (not helper and r.get("isSidechain")):
                continue
            c = (r.get("message") or {}).get("content")
            if r.get("type") == "assistant":
                for b in c or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        slim = {k: str(inp[k])[:3000 if k == "command" else 400]
                                for k in ("command", "file_path", "notebook_path", "subagent_type", "description", "prompt", "scriptPath")
                                if k in inp}
                        ev.append(("call", t, b.get("id"), b.get("name"), slim))
                continue
            if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        body = text_of(b.get("content")) or str(b.get("content") or "")
                        ev.append(("result", t, b.get("tool_use_id"), bool(b.get("is_error")), body[:700]))
                continue
            if helper:
                continue
            st = text_of(c).lstrip()
            if st.startswith(INTERRUPT):
                ev.append(("int", t))
            elif st.startswith("<command-name>") or r.get("isMeta") or r.get("isCompactSummary"):
                continue
            else:
                ps = r.get("promptSource")
                if ps == "sdk" and r.get("entrypoint") in PERSON_APPS and (r.get("origin") or {}).get("kind") in (None, "human"):
                    ps = "typed"   # the person, typing in VS Code / desktop / JetBrains (see PERSON_APPS)
                if ps is not None and ps not in TYPED:
                    continue
                if ps is None and (st.startswith(INJECTED) or (r.get("origin") or {}).get("kind") not in (None, "human")):
                    continue
                if st:
                    ev.append(("typed", t, st[:200].replace("\n", " ")))
    return ev


def read_chat(args):
    project, chat, start = args
    base = os.path.join(ROOT, project, chat)
    out = {"chat": chat, "main": [], "helpers": []}
    try:
        out["main"] = read_stream(base + ".jsonl", False, start)
    except OSError:
        pass
    for q in glob.glob(base + "/subagents/*.jsonl"):
        try:
            out["helpers"].append(read_stream(q, True, start))
        except OSError:
            pass
    return out


# ---- one stream -> its commits, each with what happened between the last edit and the commit -----------------------

def strip_writes_and_commit(cmd):
    """The text of a shell command that can have run something: bodies written by cat/tee heredocs are not commands, and
    everything from the commit on is after the check."""
    cmd = re.sub(r"((?:cat|tee)\b[^\n<]*)<<-?\s*['\"]?(\w+)['\"]?.*?\n\s*\2\s*(\n|$)", r"\1 ", cmd, flags=re.S)
    cmd = re.sub(r"\bgit\s+(-C\s+\S+\s+)?(add|rm|mv|restore|status)\b[^&;|\n]*", " ", cmd)     # file names, not runs
    m = COMMIT.search(cmd)
    return cmd[:m.start()] if m else cmd


def is_write(cmd):
    return bool(REDIRECT.search(cmd) or BASH_WRITE.search(cmd))


def kinds_of_command(cmd):
    """Kinds of check one shell command is, from its own text (a command can be several)."""
    cmd = strip_writes_and_commit(cmd)
    k = set()
    if TEST_BUILD.search(cmd):
        k.add("test, build or lint")
    if RUN_APP.search(cmd):
        k.add("ran the app")
    if DIFF.search(cmd):
        k.add("diff shown")
    elif DIFF_STAT.search(cmd):
        k.add("file list only")
    return k


def kinds_with_files(cmd, names):
    """Kinds that need to know which files the commit changed: running one of them, or a script of the project."""
    cmd = strip_writes_and_commit(cmd)
    k = set()
    if is_write(cmd):
        return k                      # a script that edits the file names it too; that is work, not a check
    if names and re.search(r"\b(node|python3?|uv run|bash|npx|tsx|deno)\b", cmd) and \
            any(re.search(r"(?<![\w.-])" + re.escape(n) + r"(?![\w-])", cmd) for n in names if n):
        k.add("ran a file it changed")
    elif SCRIPT_RUN.search(cmd):
        k.add("ran a script of the project")
    return k


def stream_commits(ev, where):
    calls, work, commits, checks = {}, [], [], []     # work: (t, path, source); checks: (t, command, kinds)
    last_commit_t = 0.0
    for e in ev:
        if e[0] == "call":
            calls[e[2]] = e
            name, inp, t = e[3], e[4], e[1]
            if name in EDIT_TOOLS:
                work.append((t, inp.get("file_path") or inp.get("notebook_path") or "", "file"))
            elif name == "SendMessage":
                work.append((t, "", "helper"))
            elif name == "Bash":
                cmd = inp.get("command", "")
                pre = COMMIT.search(cmd)
                body = cmd[:pre.start()] if pre else cmd
                hit = REDIRECT.search(body)
                if hit:
                    work.append((t, hit.group(1), "shell"))
                elif BASH_WRITE.search(body):
                    named = {n for n in re.findall(r"[\"']([~./\w-]*[\w-]\.\w{1,6})[\"']", body)
                             if not n.startswith(("/tmp/", "/private/tmp/"))}
                    for seg in re.findall(r"\b(?:sed|perl)\s+-\w*i[^&;|\n]*", body):      # the file is the last word
                        last = seg.split()[-1].strip("\"'")
                        if CODE_EXT.search(last) or DOC_PATH.search(last):
                            named = {last}
                    for n in (named or {""}):
                        work.append((t, n, "shell"))
        elif e[0] == "result":
            c = calls.get(e[2])
            if not c:
                continue
            name, inp, t = c[3], c[4], e[1]
            if name == "Workflow" and os.path.basename(inp.get("scriptPath", "")) in {os.path.basename(p) for tt, p, s in work if p}:
                checks.append((c[1], "", {"ran a file it changed"}))
            elif name in HELPER_TOOLS:
                work.append((t, "", "helper"))
                if REVIEW_HELPER.search((inp.get("subagent_type") or "") + " " + (inp.get("description") or "")):
                    checks.append((t, "", {"review sub-agent"}))
            elif name == "Read" and IMAGE.search(inp.get("file_path", "")):
                checks.append((t, "", {"looked at a screenshot"}))
            elif name == "Bash":
                cmd = inp.get("command", "")
                if COMMIT.search(cmd):
                    if e[3] or re.search(r"nothing (added )?to commit|no changes added", e[4]) or "--amend" in cmd:
                        continue
                    commits.append({"t": t, "cmd": cmd, "result": e[4], "call_t": c[1], "where": where,
                                    "work": list(work), "checks": list(checks), "prev_commit_t": last_commit_t})
                    last_commit_t = t
                else:
                    checks.append((c[1], cmd, kinds_of_command(cmd)))
    return commits


def message_and_hash(c):
    m = HASH_BRACKET.search(c["result"])
    if m:
        return m.group(2), c["result"][m.end():].split("\n")[0].strip()
    m = HASH_ONELINE.search(c["result"])
    if m:
        return m.group(1), m.group(2).strip()
    mm = re.search(r"-m\s+(\"([^\"]*)\"|'([^']*)')", c["cmd"])
    return None, (mm.group(2) or mm.group(3)) if mm else ""


def dirs_for(c, chat_cwd, edit_dirs):
    ds = []
    for m in re.finditer(r"(?:^|[;&|\n(]\s*)cd\s+\"?(~?/[^\s;&|\")]*)", c["cmd"]):
        ds.append(os.path.expanduser(m.group(1)))
    for m in re.finditer(r"-C\s+\"?(~?/[^\s\")]+)", c["cmd"]):
        ds.append(os.path.expanduser(m.group(1)))
    return ds + edit_dirs[-6:] + ([chat_cwd] if chat_cwd else [])


@functools.lru_cache(maxsize=None)
def git(d, *a):
    try:
        return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def resolve(h, ds):
    """(repo key, commit time, files) for a commit hash found in one of the directories, else None."""
    seen = []
    for d in ds:
        while d and not os.path.isdir(d) and d != "/":
            d = os.path.dirname(d)
        if not d or d in seen:
            continue
        seen.append(d)
        o = git(d, "show", "--no-patch", "--format=%ct", h).strip()
        if o.isdigit():
            files = [x for x in git(d, "show", "--name-only", "--format=", h).split("\n") if x]
            key = git(d, "rev-parse", "--git-common-dir").strip()
            key = os.path.normpath(key if os.path.isabs(key) else os.path.join(d, key))
            return key, int(o), files
    return None


def judge_commit(c, chat_cwd):
    h, msg = message_and_hash(c)
    c["hash"], c["msg"] = h, msg
    edits = [e for e in c["work"] if e[0] > c["prev_commit_t"]]
    edit_dirs = [os.path.dirname(p) for t, p, s in c["work"] if p]
    res = resolve(h, dirs_for(c, chat_cwd, edit_dirs)) if h else None
    if res:
        c["repo"], c["files"], c["files_from"] = res[0], res[2], "git"
    else:
        c["repo"] = chat_cwd or ""
        named = set(re.findall(r"git\s+(?:-C\s+\S+\s+)?add\s+(?:-\w+\s+)*([^&;|\n]+)", c["cmd"]))
        named = {x for chunk in named for x in chunk.split() if not x.startswith("-")}
        named |= set(re.findall(r"\s--\s+([^&;|\n]+)", c["cmd"][:3000]))
        named = {x for chunk in named for x in chunk.split() if "/" in x or "." in x}
        c["files"] = sorted(named or {p for t, p, s in edits if p})
        c["files_from"] = "edits"
    c["merge"] = msg.startswith("Merge ") or "--no-edit" in c["cmd"] and not edits
    paths = [f for f in c["files"] if f]
    docs = bool(paths) and not any(CODE_EXT.search(f) and not DOC_PATH.search(f) for f in paths)
    c["docs_only"] = docs
    if not edits:
        c["state"] = "no new edit seen"
        return c
    code = [e for e in edits if (e[1] and CODE_EXT.search(e[1]) and not DOC_PATH.search(e[1])) or not e[1]]
    last = max(code if (code and not docs) else edits)            # the last edit of the kind that needs a check
    c["last_edit_by"] = last[2]
    names = {os.path.basename(p) for t, p, s in edits if p} | {os.path.basename(f) for f in c["files"]}
    kinds = set()
    for t, cmd, k in c["checks"]:
        if last[0] <= t <= c["call_t"]:
            kinds |= k | (kinds_with_files(cmd, names) if cmd else set())
    kinds |= kinds_of_command(c["cmd"]) | kinds_with_files(c["cmd"], names)
    c["kinds"] = sorted(kinds)
    if kinds:
        c["state"] = "checked"
    else:
        c["state"] = "sub-agent work, no check here" if last[2] == "helper" else "unchecked"
    return c


# ---- all commits -> numbers ---------------------------------------------------------------------------------------

def rate(a, b):
    return round(a / b, 3) if b else None


def analyse(chats, meta):
    start = meta["made"] - meta["days"] * 86400
    cwd = {c["chat"]: c.get("cwd") for c in chats}
    msgs = {c["chat"]: c["msgs"] for c in chats}
    with Pool(max(1, (os.cpu_count() or 2) - 1)) as pool:
        raw = pool.map(read_chat, [(c["project"], c["chat"], start) for c in chats], chunksize=4)
    commits = []
    for chat in raw:
        for where, ev in [("main chat", chat["main"])] + [("helper", h) for h in chat["helpers"]]:
            for c in stream_commits(ev, where):
                c["chat"] = chat["chat"]
                commits.append(judge_commit(c, cwd.get(chat["chat"])))
    commits = [c for c in commits if not c["merge"]]
    analyse.commits = commits                      # kept for hand checks
    # held: later fix / revert commit on the same files within 2 h (any chat in that repo), or you saying it is broken
    by_repo = {}
    for c in commits:
        by_repo.setdefault(c["repo"], []).append(c)
    for c in commits:
        names = set(c["files"])
        c["fixed_after"] = False
        if names:
            for o in by_repo[c["repo"]]:
                if o is not c and 0 < o["t"] - c["t"] <= FOLLOW_UP_WINDOW and FIX_MESSAGE.search(o["msg"]) \
                        and names & set(o["files"]):
                    c["fixed_after"], c["fix_msg"] = True, o["msg"]
                    break
        nxt = next((m for m in msgs.get(c["chat"], []) if c["t"] < m["t"] <= c["t"] + FOLLOW_UP_WINDOW), None)
        c["next_said"] = nxt["head"] if nxt else ""
        c["said_broken"] = bool(nxt and BROKEN_SAID.search(nxt["head"]))
        c["bad"] = c["fixed_after"] or c["said_broken"]
    total = len(commits)
    docs = [c for c in commits if c["docs_only"]]
    noedit = [c for c in commits if not c["docs_only"] and c["state"] == "no new edit seen"]
    code = [c for c in commits if not c["docs_only"] and c["state"] != "no new edit seen"]
    unc = [c for c in code if c["state"] == "unchecked"]
    helper_only = [c for c in code if c["state"] == "sub-agent work, no check here"]
    chk = [c for c in code if c["state"] == "checked"]
    strong = [c for c in chk if set(c["kinds"]) - {"file list only"}]       # a file list is not a check
    weak = [c for c in chk if c not in strong]
    unc_all = unc + weak + helper_only
    n_code = len(code)
    kinds = Counter(k for c in strong for k in set(c["kinds"]) - {"file list only"})
    first = Counter()
    for c in strong:
        for k in ("test, build or lint", "ran the app", "ran a file it changed", "ran a script of the project",
                  "looked at a screenshot", "review sub-agent", "diff shown"):
            if k in c["kinds"]:
                first[k] += 1
                break
    bad = lambda cs: sum(1 for c in cs if c["bad"])
    pct = lambda x, n: round(100 * x / n) if n else None
    where = {w: {"commits": sum(1 for c in code if c["where"] == w),
                 "without_a_check": sum(1 for c in unc_all if c["where"] == w)} for w in ("main chat", "helper")}
    # per repo: a pooled share hides the repo where work reaches others (fresh audit 11-02: 15% overall, 28% in the
    # team repo). Worktree and .claude/worktrees paths fold back to their repo.
    def repo_name(c):
        r = (c.get("repo") or "?").split("/.git")[0]
        r = re.sub(r"/(?:\.claude/worktrees|w)/[^/]+$", "", r)
        return os.path.basename(r) or "?"
    by_repo = {}
    for c in code:
        b = by_repo.setdefault(repo_name(c), {"commits": 0, "without_a_check": 0})
        b["commits"] += 1
    for c in unc_all:
        by_repo.setdefault(repo_name(c), {"commits": 0, "without_a_check": 0})["without_a_check"] += 1
    for b in by_repo.values():
        b["pct"] = round(100 * b["without_a_check"] / b["commits"]) if b["commits"] else None
    n_unc = len(unc_all)
    share = n_unc / n_code if n_code else 0
    when = lambda c: datetime.fromtimestamp(c["t"]).date().isoformat()
    ex = []
    pick = next((c for c in sorted(unc, key=lambda c: -len(c["files"])) if c["where"] == "main chat" and c["msg"]), None)
    if pick:
        ex.append({"chat": pick["chat"][:8], "when": when(pick),
                   "what": f"Committed \"{pick['msg'][:70]}\" ({len(pick['files'])} files) straight after the last edit: "
                           f"no test, build, run or diff in between."})
    ok = next((c for c in strong if c["where"] == "main chat" and "test, build or lint" in c["kinds"] and c["msg"]), None)
    if ok:
        ex.append({"chat": ok["chat"][:8], "when": when(ok),
                   "what": f"Committed \"{ok['msg'][:70]}\" only after a test, build or lint run that came after the last edit."})
    if n_code < 30:
        verdict, headline = "can't tell", f"Too few code commits to say ({n_code} in 30 days)."
    elif share >= 0.2:
        verdict = "shows"
        headline = f"{n_unc} of your {n_code} code commits ({pct(n_unc, n_code)}%) went in with nothing run, shown or reviewed after the agent's last edit."
    else:
        verdict = "doesn't show"
        headline = (f"{pct(len(strong), n_code)}% of your {n_code} code commits had a check after the last edit "
                    f"(a test, build, run or diff); {n_unc} ({pct(n_unc, n_code)}%) went in with none.")
    by_band = {}
    for b, f in (("1 file", lambda n: n <= 1), ("2-3 files", lambda n: 2 <= n <= 3), ("4+ files", lambda n: n >= 4)):
        a_ = [c for c in strong if f(len(c["files"]))]
        u_ = [c for c in unc_all if f(len(c["files"]))]
        by_band[b] = {"checked": len(a_), "checked_then_repaired": bad(a_), "unchecked": len(u_), "unchecked_then_repaired": bad(u_)}
    n_bad = bad(code)
    a_files = sum(1 for c in commits if c["files_from"] == "git")
    return {
        "id": ID, "problem": "Code ships that nobody checked or understands.",
        "term": "comprehension debt", "grade": "emerging",
        "verdict": verdict, "headline": headline,
        "symptom": {"what": "A git commit of code (main session or sub-agent) where, after the agent's last edit, nothing ran or showed "
                            "the change: no test, build, lint or type check, no running the app or the changed file, no review "
                            "sub-agent, no screenshot looked at, no diff. Commits of docs and memory only are counted apart.",
                    "numbers": {"commits": total, "docs_and_memory_only_set_apart": len(docs),
                                "merges_and_no_new_edit_seen_set_apart": len(noedit), "code_commits": n_code,
                                "with_no_check": n_unc, "share_with_no_check_pct": pct(n_unc, n_code),
                                "of_which_only_a_file_list": len(weak),
                                "of_which_the_edit_was_a_helper_s_and_no_check_followed": len(helper_only),
                                "by_where": where, "by_repo": by_repo, "files_known_from_git": a_files}},
        "response": {"what": "What checked the change before the commit (a commit can have several): the kinds of check, "
                             "counted among the code commits that had one.",
                     "numbers": {"commits_with_a_check": len(strong), "kinds_any": dict(kinds), "kind_first_found": dict(first)}},
        "held": {"what": "Whether a checked commit was less often followed, within 2 h, by a repair commit on the same files "
                         "or by you saying it broke, than an unchecked one, inside the same size of change. Too few such "
                         "follow-ups to compare.",
                 "numbers": {"followed_by_a_repair_or_a_broken_message": n_bad, "of_code_commits": n_code,
                             "by_size": by_band}},
        "rung": "L3" if verdict == "doesn't show" else "can't tell",
        "rung_why": "Most commits are preceded by a run of a test, the changed file or the app, which looks like a habit "
                    "or a setup that does it; the logs do not show a hook that forces it." if verdict == "doesn't show"
                    else "Not enough to place.",
        "examples": ex,
        "cant_tell": [
            "Whether you understood the change: a check run is not understanding; this reads that something was run or shown, not read.",
            "A check run in another window, by another session, or that you did by eye on the screen is not in the logs.",
            "Which commits the agent wrote versus you: all are counted.",
            "Repairs: only commits that open with fix / revert, and a message of yours saying it broke, within 2 h; "
            f"{n_bad} of {n_code} found, too few and too noisy to compare checked against unchecked.",
        ],
        "try": ("Look at the " + str(n_unc) + " commits with no check: they are mostly small edits and config. If you want "
                "a floor, a pre-commit hook that refuses a code commit when nothing ran since the last edit would catch them."
                if verdict == "doesn't show" else None),
        "checks": {"hand_checked": HAND_CHECKED, "held_up": HAND_HELD_UP, "dropped": [
            "'Ran a file it changed' matched the file names in `git add` arguments: a commit looked checked because it named "
            "its own files. Git add / rm / mv / status are now taken out of the text before matching.",
            "A script that edits a file also names it ('python - <<EOF ... open(p)...'): counted as a run of the file at "
            "first. Commands that write are now work, not checks.",
            "Docs-only: missed memory folders under .claude/agent-memory-local and files with no extension ('null'); fixed. "
            "Commits of docs and memory are set apart because they need no test.",
            "'Followed by a repair' as first built (any commit with fix/revert/wrong/actually in its message on a file with "
            "the same name, or your next message after an interrupt): 1 of 10 hand-checked were real. 'Revert' was a feature "
            "name, file names matched across repos, and an interrupt before the commit was counted after it. Tightened to "
            "messages that open like a repair on the same full path; still too few to compare, so 'held' is not claimed.",
            "Missed checks found by hand: an inline `node -e` that imports the changed file (now counts when the file is named "
            "in a run), and `sed -i` edits of docs with no file named (the file is now read from the command).",
            "Still wrong: an edit of a log-style yaml (decisions.yaml) counts as a code edit, so a check just before it is "
            "ignored; and a check run in another window is invisible.",
        ]},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--chats", default=os.path.join(CACHE, "chats.jsonl"))
    ap.add_argument("--out", default=os.path.join(CACHE, ID + ".json"))
    a = ap.parse_args(argv)
    with open(a.chats) as f:
        meta = json.loads(f.readline())["meta"]
        chats = [json.loads(line) for line in f]
    out = analyse(chats, meta)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(out["verdict"], "-", out["headline"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
