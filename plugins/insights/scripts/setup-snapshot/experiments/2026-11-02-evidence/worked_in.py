#!/usr/bin/env python3
"""Where a session worked: the repos it changed, whatever folder it started in. The one rule for it, read from the
session logs alone (no git call: a repo is found by its `.git` on disk).

Two callers, one answer:
- inventory.py writes it as worked-in.json (its own pass over the logs calls `repos_of_call` and `record_time` for
  each tool call, `keep` per log and `by_session` to merge);
- the project pick (anatomy_scan.py: the anatomy step of `run.py pre`, and `run.py candidates` / `pre --dry-run`
  before anything runs) calls `worked_in`, which runs the same functions over the same logs.
Why one module: on 11-17 the list a member chose from ranked by the folder each session started in, while the real run
also credited the repo a session edited from another folder (read only after the 3-minute inventory). A person whose
sessions start in a parent folder (~/work) and edit a repo below it (~/work/api) was shown one pick and given another.

What counts as working in a repo: a Write/Edit of a file in it, or a shell command that changes it (a git commit, add,
apply, merge… there, a `sed -i`, tee, touch, mv, cp target or `>` redirect into it). Reading (cat, grep, git log or
diff) is not working. Left out: temporary folders, Claude Code's own ~/.claude (its plugin cache is other people's
files), node_modules and .git. A sub-agent's work counts for the session that started it. Standard library only.
"""
import glob
import json
import os
import re
import shlex
from collections import Counter, defaultdict
from datetime import datetime

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
TMP = re.compile(r"^(/private)?/(tmp|var/folders)(/|$)")
KEEP = 8   # repos kept per log, the most-changed first: a guard on size, not a threshold

# What a Bash call does to a repo: these git subcommands change it; a read (log, diff, status, show, grep) does not,
# and neither does a pull or a checkout, which is how a reference clone is brought up to date.
GIT_WRITES = {"commit", "add", "apply", "am", "mv", "rm", "merge", "rebase", "cherry-pick", "revert", "restore"}
FILE_WRITERS = {"tee", "touch", "mv", "cp", "rm", "mkdir", "patch", "install"}   # `sed` only with -i
WORKISH_RX = re.compile(r"\bgit\b|\b(sed|tee|touch|mv|cp|rm|mkdir|patch|install)\b|>")
EDITS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
NOT_WORKED_IN = re.compile(r"/node_modules/|/\.git/")
NOT_A_PATH = re.compile(r"[$`<>*?|\x00]|://")   # a shell variable, a command substitution, a here-doc marker, a glob, a URL
LEADING_SLASHES = re.compile(r"^/{2,}")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\b", re.S)
_WORKED = {}


def parent_dir(q):
    """The folder above q, or None at the top. A walk up a path stops here: a root is its own parent ("/", "//" (POSIX
    keeps a leading double slash), "C:\\"), so a loop that waits to reach "/" or the home folder never ends on those."""
    up = os.path.dirname(q)
    return up if up and up != q else None


def repo_root(path):
    """The repo a folder belongs to, read from the files (worktrees resolve to their main repo). No git call."""
    q = path
    while q and q not in ("/", HOME):
        g = os.path.join(q, ".git")
        if os.path.isdir(g):
            return q
        if os.path.isfile(g):
            try:
                t = open(g).read().split("gitdir:")[-1].strip()
            except OSError:
                return q
            if not os.path.isabs(t):
                t = os.path.normpath(os.path.join(q, t))
            return t.split("/.git/")[0] if "/.git/" in t else q
        q = parent_dir(q)
    return None


def tidy_path(p):
    """A path a command names, cleaned for a walk up the tree: `//x` is `/x` (normpath keeps a leading double slash, and
    a walk up from it never reached `/`). None when it is not a real path: empty, `-`, `/dev/...`, `&1`, a shell variable
    (`$OUT/file`), a URL, a here-doc marker."""
    if not p or p == "-" or p.startswith(("/dev/", "&")) or NOT_A_PATH.search(p):
        return None
    return LEADING_SLASHES.sub("/", os.path.normpath(p))


def worked_repo(folder):
    """The repo a folder belongs to when work done in it counts as worked in; None otherwise. Left out: temporary folders,
    Claude Code's own ~/.claude (its plugin cache is other people's files), node_modules. No git call."""
    folder = tidy_path(folder)
    if not folder:
        return None
    if folder not in _WORKED:
        bad = TMP.match(folder) or (folder + "/").startswith(os.path.join(HOME, ".claude") + "/") or NOT_WORKED_IN.search(folder + "/")
        root = None if bad else repo_root(folder)
        _WORKED[folder] = None if root and TMP.match(root) else root
    return _WORKED[folder]


def worked_dirs_of(cmd, cwd):
    """Folders a shell command CHANGES, for the repos a session worked in: where a git commit, add, apply, merge...
    ran (the cwd, a `cd X` before it, or `git -C X`), and the paths a file-writing program (sed -i, tee, touch, mv, cp's
    target, a `>` redirect) writes. Reading is not working: cat, grep, git log or diff name no folder. Follows
    inventory.files_named_by: the same segments, `cd` moves the folder, `~` is expanded; whether the folder is a repo is
    asked later."""
    out, here = [], cwd or ""

    def absolute(w):
        w = os.path.expanduser(w)
        if not tidy_path(w) or not (w.startswith("/") or here):
            return None
        return tidy_path(w if w.startswith("/") else os.path.join(here, w))

    def folder_of(a):
        return a if a and os.path.isdir(a) else os.path.dirname(a) if a else None

    for seg in re.split(r"&&|\|\||;|\||\n", HEREDOC.sub("", cmd)):
        try:
            tok = shlex.split(seg)
        except ValueError:
            tok = seg.split()
        while tok and (re.match(r"^\w+=", tok[0]) or tok[0] in ("sudo", "time", "exec", "nohup", "env")):
            tok = tok[1:]
        if not tok:
            continue
        if tok[0] == "cd":
            here = (absolute(tok[1]) if len(tok) > 1 and tok[1] != "-" else None) or here
            continue
        prog = os.path.basename(tok[0])
        if prog == "git":
            where, i = here, 1
            while i < len(tok) and tok[i].startswith("-"):
                if tok[i] == "-C" and i + 1 < len(tok):
                    where = absolute(tok[i + 1]) or where
                i += 2 if tok[i] in ("-C", "-c") else 1
            if i < len(tok) and tok[i] in GIT_WRITES and where:
                out.append(where)
        elif prog in FILE_WRITERS or (prog == "sed" and any(re.match(r"^-\w*i", x) or x.startswith("--in-place") for x in tok[1:])):
            args = [x for x in tok[1:] if not x.startswith("-") and ("/" in x or "." in x or x.startswith("~"))]
            for x in (args[-1:] if prog in ("cp", "sed") else args):   # cp reads its sources; sed's first word is its script
                out.append(folder_of(absolute(x)))
        for j, w in enumerate(tok):   # a redirect writes its target: `> file`, `>> file`, `>file`
            m = re.match(r"^\d*&?>>?(.*)$", w)
            if m:
                target = m.group(1) or (tok[j + 1] if j + 1 < len(tok) else "")
                if target:
                    out.append(folder_of(absolute(target)))
    return [d for d in dict.fromkeys(out) if d]


def repos_of_call(name, inp, cwd):
    """The repos one tool call changed (a repo once per folder it named): a Write/Edit's file, or what a shell command
    changes (worked_dirs_of). `cwd` is the record's own folder, for relative paths."""
    inp = inp if isinstance(inp, dict) else {}
    if name in EDITS:
        p = inp.get("file_path") or inp.get("notebook_path") or ""
        if not p:
            return []
        q = os.path.expanduser(p)
        q = q if q.startswith("/") else os.path.join(cwd or "/", q)
        r = worked_repo(os.path.dirname(q))
        return [r] if r else []
    if name == "Bash":
        cmd = inp.get("command") or ""
        if not WORKISH_RX.search(cmd):
            return []
        return [r for r in (worked_repo(d) for d in worked_dirs_of(cmd, cwd or "")) if r]
    return []


def record_time(rec):
    """A log record's time in epoch seconds, None when it has none or it does not parse. A record before the window's
    start counts for nothing, in the inventory and here alike."""
    ts = rec.get("timestamp")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts[:19] + "+00:00").timestamp()
    except (ValueError, TypeError):
        return None


def keep(counter):
    """One log's repos as stored: the KEEP most-changed."""
    return dict(Counter(counter).most_common(KEEP))


def marked(line):
    """True when a log line can hold a change: the assistant's tool calls, as Claude Code writes them. Every reader of
    the rule skips the other lines with this same test."""
    return '"tool_use"' in line and '"type":"assistant"' in line


def worked_of_record(rec, start):
    """The repos one parsed log record changed (a repo once per tool call and folder): an assistant record in the window
    (`start`, epoch seconds), each of its tool calls by repos_of_call with the record's own folder."""
    if rec.get("type") != "assistant":
        return []
    t = record_time(rec)
    if t is None or t < start:
        return []
    out = []
    for b in (rec.get("message") or {}).get("content") or []:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            out += repos_of_call(b.get("name") or "?", b.get("input"), rec.get("cwd"))
    return out


def worked_of_log(args):
    """(path, {repo: calls that changed it}) for one session log or sub-agent log, records since `start` only. Read as
    text, bad bytes dropped, as anatomy_scan.scan_file reads a main log (it applies `marked` and `worked_of_record` to
    the lines it already parses, so a main log is not read twice)."""
    path, start = args
    c = Counter()
    try:
        fh = open(path, errors="ignore")
    except OSError:
        return path, {}
    with fh:
        for line in fh:
            if not marked(line):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            c.update(worked_of_record(rec, start))
    return path, keep(c)


def session_of(path):
    """The session a log belongs to: a main log is its own (<project>/<session>.jsonl); a sub-agent's log
    (<project>/<session>/subagents/<agent>.jsonl) counts for the session that started it."""
    d = os.path.dirname(path)
    if os.path.basename(d) == "subagents":
        return os.path.basename(os.path.dirname(d))
    return os.path.basename(path)[:-6]


def by_session(logs):
    """{session: {repo: calls}} from (log path, that log's repos) pairs; sessions that changed no repo are left out."""
    out = defaultdict(Counter)
    for path, worked in logs:
        out[session_of(path)].update(worked or {})
    return {sid: dict(c) for sid, c in out.items() if c}


def log_files(start, root=None):
    """(main logs, sub-agent logs) written since `start` (epoch seconds), as inventory.scan_logs lists them."""
    root = root or PROJECTS
    mains = [p for p in glob.glob(os.path.join(root, "*", "*.jsonl")) if os.path.getmtime(p) >= start]
    helpers = [p for p in glob.glob(os.path.join(root, "*", "*", "subagents", "*.jsonl")) if os.path.getmtime(p) >= start]
    return mains, helpers


def worked_in(start, root=None, mapper=map):
    """{session: {repo: calls}} for every log written since `start`: worked-in.json's "sessions". `mapper` runs
    worked_of_log over the logs (a Pool's imap_unordered to spread it over the cores)."""
    mains, helpers = log_files(start, root)
    return by_session(mapper(worked_of_log, [(p, start) for p in mains + helpers]))
