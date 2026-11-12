#!/usr/bin/env python3
"""Every source of evidence on one laptop, with counts: the list the v15 readers work from and the only place a number
on the page may come from. No AI, standard library only, read-only.

Why it exists: the first v15 evidence (one detector per named problem) failed its audit, 10 of 70 claims held, because
each detector read one kind of evidence and nobody listed what was never read (hook-injected context, slash-command
history, eval ledgers, git hooks, launchers, word counts). This file lists every source first, so a reader can say
"looked, not found" only after looking, and "not read" when no source covers a practice.

    python3 inventory.py --out RUN_DIR [--days 30]

Writes into RUN_DIR:
  inventory.json  every section below; a reader opens one section at a time (`python3 -c` / jq on a key)
  inventory.md    one screen per section: what was read, the headline counts; the readers' table of contents
  chats.jsonl     typed chats, from ../2026-11-02-problems/logs.py (other sessions' reports dropped)
  anatomy.json    the full file inventory, from ../2026-10-27-anatomy/inventory.py (settings as key names only)

Sections (inventory.json):
  meta            window, Claude Code versions, files read, what could not be read
  person          who "the person" is for authorship: git identities + the most frequent author where their chats run
  repos           every repo their chats ran in: chats, headless runs, commits in the window (theirs / all)
  chats           typed chats: count, typed messages, permission modes, entrypoints, by repo
  slash_commands  every typed /command, per command from one source: the session logs, or ~/.claude/history.jsonl for a
                  command the logs never record; a row says which, and counts times in sessions you typed in
  tools           the agent's tool calls in main chats and in helpers: by tool, by program run in the shell, by MCP server;
                  programs resolved to where they live and whether the person wrote them
  helpers         sub-agent calls by type (model asked for, in the background), with the agent file and its author
  workflows       Workflow tool runs; saved workflow scripts
  plan_dialogs    plan approvals: calls, refused, chats; plan files written and read by a later chat
  hook_context    text hooks put in front of the agent: by event and by the plugin tag it starts with ("[name] …"),
                  attachments and chats; blocking errors (a hook refused something); hook messages shown to the person
  rules_reaching  instruction files from subfolders that reached the agent (nested_memory), by file
  compactions     by trigger; by hand with or without focus text; how full; chats compacted more than once
  headless        runs no one typed in (entrypoint sdk-*): by repo, by prompt template, by day; permission modes, models
  settings        per settings file: key names, permission rule counts, deny/ask rules (tool patterns, no values),
                  default mode, hook events, plugins enabled/disabled, skill overrides
  plugins         installed plugins: who wrote them (own marketplace → the source repo in git), what they add, their evals
  own_artifacts   agents, skills, commands, workflows, output styles and hook scripts the person wrote (git), with model/tools
  mcp_servers     configured servers: scope, transport, flags (read-only, features); never env values or headers
  skill_depth     per skill the person wrote: lines, other files, loads, loads that opened another file of it, a script of it run
                  after a load, live-context syntax, evals seen running it; board.skill_depth turns it into depth 1-4
  evals           eval folders with cases and run ledgers: author, cases, runs in the window, last run
  instruction_files  always-loaded files (home + repo roots): size in WORDS now, their history (peak, trims, authors);
                  folder files: count and the largest; skill listing size the agent sees
  memory_stores   stores (notes folders, vaults, databases): size, newest write, who writes (hooks), chats that read them
  git_hooks       per repo: hooks path, hook files, author
  launchers       cron, launchd, scheduled tasks, CI, scripts that start agents (from the file inventory)
  probes          the old detectors' numbers, kept only as numeric probes (each labelled with its audit verdict)
"""
import argparse
import bisect
import functools
import glob
import importlib.util
import json
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from multiprocessing import Pool, parent_process
from pathlib import Path

# Apps a person types into (VS Code, desktop, JetBrains) drive Claude Code through the Agent SDK, so Claude Code
# writes the person's own message as promptSource "sdk", like a script's. In their sessions "sdk" is the person.
PERSON_APPS = ("claude-vscode", "claude-desktop", "claude-jetbrains")

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
UNATTENDED = "sdk"
# attachment kinds only a person's typing produces; counted once per record (see read_log)
AT_PATH_RX = re.compile(r'@"?([^\s"]+)')


def at_mentions_file(typed, a):
    """True when the message the person typed names this attached file after an @ (a path relative to anywhere: the
    end of the attached path must match). Checked 11-04: 159 of 178 file attachments followed a /compact, where Claude
    Code re-attaches files it had read, and only 19 followed an @ in the person's message."""
    fn, dp = str(a.get("filename") or ""), str(a.get("displayPath") or "")
    base = os.path.basename(fn)
    for tok in AT_PATH_RX.findall(typed or ""):
        t = tok.rstrip(".,;:)!?'\"").lstrip("./")
        if t and (fn.endswith(t) or dp.endswith(t) or (base and t.endswith(base))):
            return True
    return False
MENTIONS = ("agent_mention", "skill_mention", "file", "workflow_keyword_request")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DIGITS = re.compile(r"\d+")
PATH_RX = re.compile(r"(?:~|/Users/[^/\s'\"]+|/home/[^/\s'\"]+)(?:/[^\s'\"|;&()<>]+)+")
REJECT = ("The user doesn't want to proceed", "The user doesn’t want to proceed")
TAG_RX = re.compile(r"^\s*\[([A-Za-z0-9_.:@ -]{2,40})\]")
# who did it: a message another session or a teammate sent into this chat (idle and shutdown notices left out); a
# typed message asking for helpers (word match, so "probably you", never proof); the helper types Claude Code ships
SESSION_MSG = ("Another Claude session sent a message", "<teammate-message", "<cross-session-message")
# A job Claude watched until it ended (claude.jobs_watched). Claude Code writes: the Bash result of a background command
# (toolUseResult.backgroundTaskId), a <task-notification> with the task's final status when it ends (as a user message or
# a queued_command attachment), and Claude's own later calls that name the task (TaskOutput/BashOutput with its id, a Read
# or a shell command on its tasks/<id>.output file). A poll loop is plain shell: a loop that sleeps between checks.
NOTIFICATION_RX = re.compile(r"<task-id>\s*([A-Za-z0-9_-]+)\s*</task-id>.{0,600}?<status>\s*([a-z_-]+)\s*</status>", re.S)
POLL_LOOP_RX = re.compile(r"(?:^|[;&|({])\s*(?:until|while|for\s+\w+\s+in\s+(?:\$\(\s*seq|`seq|\{\d+\.\.\d+\}))(?:\b|(?<=\}))[^\n;]*;\s*do\b(?:(?!\bdone\b).)*?\bsleep\s+[0-9$\"'(]", re.S | re.M)
NOT_A_RETURN_TO_A_JOB = ("TaskStop", "Monitor", "ScheduleWakeup", "Agent", "Task", "SendMessage")
# What shows a skill running in an eval (skill_depth_facts). Claude Code writes the first line of a skill's text when it loads it
# (a user record flagged isMeta), and the person's own `claude plugin eval <path | plugin | plugin@marketplace>` as a Bash call
# or a typed `!command` (<bash-input>).
SKILL_LOADED_PREFIX = "Base directory for this skill:"
PLUGIN_EVAL_RX = re.compile(r"\bclaude\b[^\n|;&]*?\bplugin\s+eval\b")
JOB_ENDED = ("completed", "failed")   # a job TaskStop killed was stopped, not watched until it ended
IDLE_RX = re.compile(r'"type"\s*:\s*"(idle_notification|shutdown_\w+)"')
# Plain English only, never a tool's name; a word right after "/" is a typed command, not an ask in words.
ASK_HELPERS_RX = re.compile(r"(?i)(?<![/\w])(agents?|sub-?agents?|fork\w*|helpers?|parallel\w*|background|delegat\w*|spawn\w*|"
                            r"teams?|teammates?|workflows?|split\w*|swarm|explore)\b|@\w")
# the agent types Claude Code itself ships (labs/v15/substrate/claude-code.yaml agent_types, 2.1.318); anything else is
# the person's or a plugin's, and only git decides which (own.agent)
BUILTIN_HELPERS = {"Explore", "Plan", "claude", "claude-code-guide", "comment-thread-analyst", "fork", "general-purpose",
                   "statusline-setup", "web-fetch", "worker", "workflow-subagent"}
# The commands Claude Code itself registers (labs/v15/substrate/claude-code.yaml commands, 2.1.318): a typed /name among
# these is Claude Code's, never "a command of yours", even when a skill of theirs shares the name.
BUILTIN_COMMANDS = frozenset({
    "add-dir", "advisor", "agents", "artifacts", "auto-mode-setup", "autocompact", "autofix-pr", "background",
    "branch", "brief", "btw", "bug", "cd", "chrome", "clear", "cloud-plugins", "color", "compact", "config",
    "context", "copy", "daemon", "design-consent", "design-login", "design-revoke", "desktop", "diff", "effort",
    "exit", "export", "extra-usage", "fast", "feedback", "focus", "fork", "goal", "heapdump", "help", "hooks", "ide",
    "import", "init", "insights", "install", "install-github-app", "install-slack-app", "keybindings", "list-agents",
    "login", "logout", "loops", "mcp", "memory", "mobile", "model", "output-style", "passes", "pause-memory",
    "permissions", "plan", "plugin", "powerup", "privacy-settings", "pro-trial-expired", "project", "radio",
    "rate-limit-options", "recap", "release-notes", "reload-plugins", "reload-skills", "remote-control",
    "remote-env", "rename", "restart", "resume", "rewind", "sandbox", "scroll-speed", "session", "setup-bedrock",
    "setup-vertex", "skill-doctor", "skills", "status", "statusline", "stickers", "stop", "subtask", "tasks",
    "team-onboarding", "teleport", "terminal-setup", "theme", "tui", "ultraplan", "ultrareview", "upgrade", "usage",
    "usage-credits", "use-case", "version", "voice", "web-setup", "wellbeing", "workflow-launch-exec", "workflows"
})
# Rule-file or hook text that talks about sub-agents (plain English). Whose text it is comes from git (own.hook,
# own.rule_file), never from the text: the ruling 11-04, nothing tuned to one person's tools.
SUBAGENT_TALK_RX = re.compile(r"(?i)\bsub-?agents?\b|\bdelegat\w*|\bteammates?\b|\bAgent tool\b|\bparallel agents?\b")
INSTRUCTION_NAMES = ("CLAUDE.md", "AGENTS.md", "CLAUDE.local.md", "GEMINI.md")
GITENV = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    # a module that starts worker processes (logs.py's Pool) must be importable by name in them: where processes are
    # spawned (macOS, Windows) the worker imports it again from sys.path, else "Can't pickle ...: import of module failed"
    sys.modules[name] = m
    if os.path.dirname(path) not in sys.path:
        sys.path.append(os.path.dirname(path))
    spec.loader.exec_module(m)
    return m


clock = load_module("clock", os.path.join(HERE, "clock.py"))   # the one place the calendar clock is read
START = clock.now() - 30 * 86400  # set in main() from --days
ANATOMY = os.path.join(EXP, "2026-10-27-anatomy", "inventory.py")
LOGS = os.path.join(EXP, "2026-11-02-problems", "logs.py")
IDENTITY = os.path.join(HERE, "identity.py")
OWNERSHIP = os.path.join(HERE, "ownership.py")   # the one ownership rule: theirs unless a receipt says installed
_own_mod, _receipts = None, None
WRITTEN_DIRS = ()   # folders their agents wrote a file in (Write/Edit in the logs); main() fills it from the session records


def written_in_logs(folder):
    """ownership.owner()'s callback: a Write/Edit in the person's own logs touched a file under this skill folder, so
    their agent wrote it and it is not an unpacked install."""
    want = {folder, os.path.realpath(folder)}
    return any(d == w or d.startswith(w.rstrip("/") + "/") for d in WRITTEN_DIRS for w in want)


def receipts(roots=None):
    """ownership.py's receipts, built once (main() passes the scan roots before anything asks)."""
    global _own_mod, _receipts
    if _own_mod is None:
        _own_mod = load_module("ownership", OWNERSHIP)
    if _receipts is None or roots is not None:
        _receipts = _own_mod.load_receipts(roots=roots or [])
    return _own_mod, _receipts
_scrub = None


def scrub(s):
    """Free text from logs goes through the file inventory's secret scrubber before it is written."""
    global _scrub
    if _scrub is None:
        try:
            _scrub = load_module("anatomy_inv", ANATOMY).scrub
        except Exception:  # noqa: BLE001 - never write unscrubbed text
            _scrub = lambda t: re.sub(r"[A-Za-z0-9+/_-]{24,}", "<redacted>", t)  # noqa: E731
    return _scrub(s or "")


def day(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def git(args, cwd, timeout=20):
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              env=GITENV).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


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


def in_linked_worktree(path):
    q = path
    while q and q not in ("/", HOME):
        if os.path.isfile(os.path.join(q, ".git")):
            return True
        if os.path.isdir(os.path.join(q, ".git")):
            return False
        q = parent_dir(q)
    return False


TMP = re.compile(r"^(/private)?/(tmp|var/folders)(/|$)")


def place(cwd):
    """Where a run happened: its repo, or for temporary folders 'temp: <last folder name>' (copies made for one run)."""
    if not cwd:
        return "?"
    if TMP.match(cwd):
        return "temp: " + os.path.basename(cwd.rstrip("/"))
    return repo_root(cwd) or cwd


def scan_folder(cwd):
    """The folder the scan reads for a session that started in cwd, following Claude Code: it works by start folder, not
    by repo (it loads CLAUDE.md from the start folder and its parents, and keeps memory per start folder). That is the
    repo the folder belongs to when there is one above it, else the folder itself. None for a folder that is gone, a
    temporary folder, Claude Code's own ~/.claude (its plugin cache is other people's files), HOME itself, or a folder
    that holds HOME (walking those would walk the whole machine)."""
    if not cwd or TMP.match(cwd) or not os.path.isdir(cwd) or (cwd + "/").startswith(os.path.join(HOME, ".claude") + "/"):
        return None
    f = (repo_root(cwd) or cwd).rstrip("/") or "/"
    if f == HOME or (HOME + "/").startswith(f.rstrip("/") + "/"):
        return None
    return f


BELOW_DEPTH, BELOW_SECONDS, BELOW_CAP = 3, 4.0, 15


def repos_below(folder, budget_s=BELOW_SECONDS, depth=BELOW_DEPTH):
    """Git repos beneath a start folder that is not itself a repo: the work happens there, so its commits, authorship,
    hooks and evals are read from there. Bounded: `depth` levels, the anatomy scan's PRUNE folders skipped, never into a
    repo found (a nested one is part of it), linked worktrees left out, `budget_s` seconds. Newest activity first."""
    prune = (sys.modules.get("anatomy") or load_module("anatomy", ANATOMY)).PRUNE
    end, found, stack = time.monotonic() + budget_s, [], [(folder, 0)]
    while stack and time.monotonic() < end:
        d, n = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False) or e.name in prune or e.name.endswith(".egg-info"):
                    continue
                if os.path.isdir(os.path.join(e.path, ".git")):
                    found.append(e.path)
                elif n + 1 < depth and not os.path.isfile(os.path.join(e.path, ".git")):
                    stack.append((e.path, n + 1))
            except OSError:
                continue

    def latest(r):
        for f in ("logs/HEAD", "HEAD"):
            try:
                return os.path.getmtime(os.path.join(r, ".git", f))
            except OSError:
                pass
        return 0
    return sorted(found, key=lambda r: (-latest(r), r))   # a tie (equal times, or none readable) breaks by path, never by the order the folder lists


WORKED_CAP = 15   # repos kept for the work sessions did; git is read for each of them


def repos_from_work(worked_chats, roots, cap=None):
    """The repos found only from the work (no session started in them): most sessions first, the first `cap` kept. A tie
    breaks by path, never by the order sessions were read, so the cap keeps the same repos on every run."""
    ranked = sorted(((r, n) for r, n in worked_chats.items() if r not in roots and os.path.isdir(r)), key=lambda kv: (-kv[1], kv[0]))
    return Counter(dict(ranked[:cap or WORKED_CAP]))


def rank_folders(all_folders, roots, from_work, head_folders, last, cap=15):
    """The folders the file scan reads, most sessions typed in first; among equals the ones with headless runs, then a repo
    before a plain folder, then the newest session, and last the path: a full key, so no tie falls to the order folders
    were listed in (the picked project flipped on about one run in three when it did)."""
    return sorted(all_folders, key=lambda f: (-(roots[f] + from_work[f]), -head_folders[f], repo_root(f) is None,
                                              -last.get(f, 0), f))[:cap]


def sort_repos(repos):
    """The inventory's repo rows, busiest first (typed + headless + worked sessions), a tie by path. In place."""
    repos.sort(key=lambda x: (-(x["typed_chats"] + x["headless_runs"] + x.get("worked_chats", 0)), x["repo"]))
# What a Bash call does to a repo: these git subcommands change it; a read (log, diff, status, show, grep) does not,
# and neither does a pull or a checkout, which is how a reference clone is brought up to date.
GIT_WRITES = {"commit", "add", "apply", "am", "mv", "rm", "merge", "rebase", "cherry-pick", "revert", "restore"}
FILE_WRITERS = {"tee", "touch", "mv", "cp", "rm", "mkdir", "patch", "install"}   # `sed` only with -i
WORKISH_RX = re.compile(r"\bgit\b|\b(sed|tee|touch|mv|cp|rm|mkdir|patch|install)\b|>")
NOT_WORKED_IN = re.compile(r"/node_modules/|/\.git/")
_WORKED = {}
NOT_A_PATH = re.compile(r"[$`<>*?|\x00]|://")   # a shell variable, a command substitution, a here-doc marker, a glob, a URL
LEADING_SLASHES = re.compile(r"^/{2,}")


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
    files_named_by: the same segments, `cd` moves the folder, `~` is expanded; whether the folder is a repo is asked later."""
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


HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\b", re.S)
QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
RUNNERS = ("uv", "uvx", "npx", "bunx", "pnpm", "python", "python3", "node", "bun", "bash", "sh", "zsh", "deno", "tsx")


def programs_of(cmd):
    """Programs a shell command runs: first word of each segment, env assignments and cd skipped, path → basename.
    Heredoc bodies and quoted strings are dropped first (their code is not a program the shell runs); a runner
    (python, node, uv run…) is named with its script: 'python check.py', 'python -c' for inline code."""
    cmd = QUOTED.sub("''", HEREDOC.sub("", cmd))
    out = []
    for seg in re.split(r"&&|\|\||;|\||\n|\$\(|`", cmd):
        words = seg.strip().split()
        while words and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]) or words[0] in ("sudo", "time", "env", "exec", "(", "{", "!", "nohup", "timeout") or words[0].isdigit()):
            words = words[1:]
        if not words or words[0] in ("cd", "echo", "printf", "true", "false", "test", "[", "[[", "then", "do", "done", "fi",
                                     "else", "for", "if", "while", "export", "set", "local", "return", "#", "}", ")", "''", "sleep"):
            continue
        w = words[0].strip("'\"")
        if os.path.basename(w) in RUNNERS:
            w = os.path.basename(w)
            rest = words[1:]
            if w in ("uv", "pnpm", "bun") and "run" in rest[:3]:
                rest = rest[rest.index("run") + 1:]
            script = None
            for x in rest:
                if x in ("-c", "-e", "--eval"):
                    script = "-c"
                    break
                if x.startswith("-") or x in ("python", "python3", "node", "''", "-"):
                    continue
                script = os.path.basename(x.strip("'\""))[:40]
                break
            w = f"{w} {script}" if script else w
        else:
            w = os.path.basename(w)
        if re.match(r"^[\w.@+ /-]{1,60}$", w):
            out.append(w)
    return out


def dirs_of(paths):
    """The folder a path is about, kept to 5 levels below the home folder (7 from the root elsewhere): counted per
    folder, not per file. Relative to HOME, so a home deeper than /Users/<name> doesn't cut a memory store's path short
    (found 11-05 by the whole-tool test)."""
    out = set()
    home_parts = len(HOME.rstrip("/").split("/"))
    for p in paths:
        p = p.replace("~", HOME, 1) if p.startswith("~") else p
        parts = p.split("/")
        keep = home_parts + 5 if p.startswith(HOME.rstrip("/") + "/") else 7
        out.add("/".join(parts[:keep]))
    return out


# ---------------------------------------------------------------- one pass over every log file


def hook_context_text(event, stdout):
    """The text a hook's run added to Claude's context: hookSpecificOutput.additionalContext in its JSON stdout, or, for
    SessionStart and UserPromptSubmit, plain stdout (Claude Code adds that to the context as it is)."""
    out = str(stdout or "").strip()
    if not out:
        return ""
    if out.startswith("{"):
        try:
            j = json.loads(out)
        except ValueError:
            j = None
        if isinstance(j, dict):
            return str(((j.get("hookSpecificOutput") or {}).get("additionalContext")) or "")
    return out if event in ("SessionStart", "UserPromptSubmit") else ""


def own_marketplace_folders():
    """Folders the person registered as a plugin marketplace from this laptop: known_marketplaces.json entries whose
    source is a "directory" (Claude Code's own record). Their plugins' agents, skills and hooks are the person's
    (ownership.py: a marketplace read from a folder here is no install receipt). Read whether or not a session ran in
    them: the plugins run everywhere, the sessions rarely start in the marketplace's folder (11-05: removing one laptop's
    hard-coded kit folder dropped its own plugins from the scan; this is the general rule it stood in for)."""
    km = {}
    try:
        km = json.load(open(os.path.join(HOME, ".claude", "plugins", "known_marketplaces.json"))) or {}
    except (OSError, ValueError):
        pass
    out = []
    for m in km.values():
        src = (m or {}).get("source") or {}
        p = src.get("path") if src.get("source") == "directory" else None
        if p and os.path.isdir(p) and not os.path.realpath(p).startswith(os.path.realpath(os.path.join(HOME, ".claude")) + os.sep):
            out.append(os.path.realpath(p))
    return list(dict.fromkeys(out))


# One session log gets this long. One that takes longer (a loop on a strange line, a log of a size nobody expected) is
# skipped and counted in meta.could_not_read, so a run still ends; the page reads that as "couldn't check", never a minus.
SESSION_SECONDS = float(os.environ.get("SETUP_SESSION_SECONDS") or 300)
_DEADLINE = None


class SessionTooLong(BaseException):   # not an Exception: no `except Exception` in the reader may swallow it
    pass


def parent_gone():
    """True in a pool worker whose parent process has died (killed on a time limit, say): nobody is waiting for its answer, so it
    stops instead of running on, alone, until its log ends. False in the main process and while the parent lives. (11-12: a worker
    kept a core at 100% for almost two hours after the test that started the scan was killed on its limit.)"""
    p = parent_process()
    return p is not None and not p.is_alive()


def read_log_limited(args):
    """read_log with a time limit: the record, or {"timed_out": path}. Where the system has SIGALRM (macOS, Linux) it
    stops a loop in the middle of one line; elsewhere (Windows) the check between lines still ends a slow log."""
    global _DEADLINE
    if parent_gone():
        return {"timed_out": args[0]}
    _DEADLINE = time.monotonic() + SESSION_SECONDS

    def stop(*_):
        raise SessionTooLong()
    old = None
    try:
        if hasattr(signal, "setitimer"):
            old = signal.signal(signal.SIGALRM, stop)
            signal.setitimer(signal.ITIMER_REAL, SESSION_SECONDS)
        return read_log(args)
    except SessionTooLong:
        return {"timed_out": args[0]}
    finally:
        if old is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)
        _DEADLINE = None


def read_log(args):
    """One log file → a compact record. Main chats (typed or headless) and helper files alike."""
    path, start, is_helper = args
    r = {"file": path, "helper": is_helper, "chat": os.path.basename(path)[:-6], "ep": None, "cwd": None, "t0": None,
         "t1": None, "version": None, "pm": Counter(), "models": Counter(), "tools": Counter(), "progs": Counter(),
         "mcp": Counter(), "agents": [], "workflows": [], "skills": Counter(), "plan_calls": 0, "plan_rejected": 0,
         "hooks": Counter(), "hook_blocks": Counter(), "hook_msgs": Counter(), "nested": Counter(),
         "compactions": [], "commands": [], "first_prompt": None, "dirs_read": Counter(), "dirs_written": Counter(),
         "instr_edits": Counter(), "worktree_adds": 0, "bg_bash": 0, "skill_listing_words": 0, "tool_errors": 0,
         "hook_denials": Counter(), "attach": Counter(), "hook_runs": Counter(),
         # who did it: names Claude gave its helpers, who each SendMessage went to, messages other sessions or
         # teammates sent in (logs.py drops these from typed messages, rightly; here they are counted), --agent X
         "agent_names": set(), "send_to": [], "session_msgs_in": 0, "agent_setting": None,
         "mention_ids": defaultdict(list), "file_reattached": 0,
         # what reached Claude's context, for ownership later (main, by git): (t, "hook", event, command, talks about
         # sub-agents) from a hook's own run record; (t, "rule", how, path, talks) for a rule file loaded at start or mid-session
         "ctx": [], "block_cmds": Counter(), "blocks_by_name": Counter(), "sched_fires": 0,
         # records written while the session runs in the background carry sessionKind "bg" (/bg, or started there)
         "bg_kind": False,
         # when Claude Code loaded the project's auto-memory MEMORY.md into the session (an `instructions` record whose
         # file is type AutoMem, with text): the session began with earlier work in its context, no Read call needed
         "automem_t": None, "jobs_came_back": 0, "jobs_poll_loops": 0,
         # what could show a skill's depth (skill_depth_facts): (t, "load", name) for a Skill call or a typed built-in-style
         # /name; (t, "loaded", folder name) for Claude Code's "Base directory for this skill" record (a typed skill command
         # leaves only this one);
         # (t, "read" | "edit" | "bash", path or command) for a tool call that names a `skills/` path;
         # (t, "script", (file paths the command names)) for a Bash call that ran a script or test
         "skill_ev": [],
         # repos this session changed, with how many calls did (worked_repo, worked_dirs_of): found whatever folder it started in
         "worked": Counter()}
    # the jobs Claude may have watched: background Bash task id → the Bash call's id; a task id → how it ended; task ids
    # Claude's later calls named; every Bash call's (in background, poll loop); Bash calls whose result was an error
    job_task, job_end, job_back, job_bash, job_failed = {}, {}, set(), {}, set()
    script_seen = set()      # the files a script or test call named, once per log (skill_depth_facts reads them)
    last_typed = ""
    plan_ids = set()
    try:
        fh = open(path, "rb")
    except OSError:
        return None
    with fh:
        for n, line in enumerate(fh):
            if _DEADLINE and n % 200 == 0 and (time.monotonic() > _DEADLINE or parent_gone()):
                raise SessionTooLong()
            if r["ep"] is None and b'"entrypoint":"' in line:
                j = line.find(b'"entrypoint":"')
                r["ep"] = line[j + 14:line.find(b'"', j + 14)].decode("ascii", "replace")
            if not r["bg_kind"] and b'"sessionKind":"bg"' in line:
                r["bg_kind"] = True
            if b'"subtype":"scheduled_task_fire"' in line:  # a Claude Code scheduled task started a turn
                r["sched_fires"] += 1
            if b'"type":"agent-setting"' in line:  # `claude --agent X`; the record carries no timestamp
                try:
                    r["agent_setting"] = r["agent_setting"] or json.loads(line).get("agentSetting")
                except ValueError:
                    pass
                continue
            is_asst = b'"type":"assistant"' in line
            is_att = b'"type":"attachment"' in line
            is_user = b'"type":"user"' in line
            is_sys = b'"compact_boundary"' in line or b'"stop_hook_summary"' in line
            if is_asst and b'"tool_use"' not in line:
                j = line.find(b'"model":"')
                if j >= 0:
                    r["models"][line[j + 9:line.find(b'"', j + 9)].decode("ascii", "replace")] += 1
                continue
            if is_user and b'"tool_result"' in line and b'"is_error":true' not in line and b'"backgroundTaskId"' not in line:
                continue
            if not (is_asst or is_att or is_user or is_sys):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = rec.get("timestamp")
            if not ts:
                continue
            try:
                t = datetime.fromisoformat(ts[:19] + "+00:00").timestamp()
            except ValueError:
                continue
            if t < start:
                continue
            r["t0"] = r["t0"] or t
            r["t1"] = t
            r["cwd"] = r["cwd"] or rec.get("cwd")
            r["version"] = r["version"] or rec.get("version")
            kind = rec.get("type")
            if kind == "attachment":
                a = rec.get("attachment") or {}
                if not isinstance(a, dict):
                    continue
                at = a.get("type") or "?"
                r["attach"][at] += 1
                if at == "queued_command" and "<task-notification>" in str(a.get("prompt") or ""):
                    for tid, st_ in NOTIFICATION_RX.findall(str(a.get("prompt"))):
                        job_end[tid] = st_
                if at in MENTIONS:
                    if at == "file" and not at_mentions_file(last_typed, a):
                        r["file_reattached"] += 1
                    else:
                        r["mention_ids"][at].append(rec.get("uuid") or f"{path}:{n}")
                if at == "hook_additional_context":
                    items = a.get("content")
                    items = items if isinstance(items, list) else [items]
                    for it in items:
                        s = str(it or "")
                        m = TAG_RX.match(s)
                        tag = m.group(1) if m else "(no tag) " + re.sub(r"\s+", " ", s[:40])
                        r["hooks"][(a.get("hookName") or a.get("hookEvent") or "?", tag, re.sub(r"\s+", " ", s[:140]))] += 1
                elif at == "hook_blocking_error" and not a.get("command"):
                    r["blocks_by_name"][str(a.get("hookName") or "?")] += 1  # e.g. PostToolUse:Write; the record names no command
                    be = a.get("blockingError")
                    s = str((be or {}).get("blockingError") if isinstance(be, dict) else be or "")
                    m = TAG_RX.match(s)
                    r["hook_blocks"][(a.get("hookName") or "?", m.group(1) if m else "(no tag)", re.sub(r"\s+", " ", s[:140]))] += 1
                elif at == "hook_system_message":
                    s = re.sub(r"\x1b\[[0-9;]*m", "", str(a.get("content") or ""))
                    m = TAG_RX.match(s)
                    r["hook_msgs"][(a.get("hookName") or "?", m.group(1) if m else re.sub(r"\s+", " ", s[:50]))] += 1
                elif at in ("hook_success", "hook_non_blocking_error", "hook_blocking_error") and a.get("command"):
                    ev = str(a.get("hookName") or a.get("hookEvent") or "?").split(":")[0]
                    r["hook_runs"][(ev, str(a.get("command"))[:200])] += 1
                    if at == "hook_success":
                        txt = hook_context_text(ev, a.get("stdout"))
                        if txt:
                            r["ctx"].append((t, "hook", ev, str(a.get("command"))[:200], bool(SUBAGENT_TALK_RX.search(txt))))
                    if at == "hook_blocking_error":
                        r["block_cmds"][str(a.get("command"))[:200]] += 1
                        be = a.get("blockingError")
                        s = str((be or {}).get("blockingError") if isinstance(be, dict) else be or "")
                        m = TAG_RX.match(s)
                        r["hook_blocks"][(a.get("hookName") or "?", m.group(1) if m else "(no tag)", re.sub(r"\s+", " ", s[:140]))] += 1
                elif at == "nested_memory":
                    r["nested"][str(a.get("path") or "")] += 1
                    c = a.get("content")
                    c = c.get("content") if isinstance(c, dict) else c
                    r["ctx"].append((t, "rule", "nested", str(a.get("path") or ""), bool(SUBAGENT_TALK_RX.search(str(c or "")))))
                elif at == "instructions":
                    for f in a.get("files") or []:
                        if isinstance(f, dict) and f.get("path"):
                            r["ctx"].append((t, "rule", "start", str(f["path"]), bool(SUBAGENT_TALK_RX.search(str(f.get("content") or "")))))
                            if f.get("type") == "AutoMem":
                                if str(f.get("content") or "").strip() and (r["automem_t"] is None or t < r["automem_t"]):
                                    r["automem_t"] = t
                                # Claude Code loaded its auto memory into the session itself (no Read call): the session
                                # read that store (11-05: a person's memory read in 92 of 101 sessions read as 16)
                                for d in dirs_of([str(f["path"])]):
                                    r["dirs_read"][d] += 1
                elif at == "skill_listing":
                    w = len(str(a.get("content") or "").split())
                    r["skill_listing_words"] = max(r["skill_listing_words"], w)
                continue
            if kind == "system":
                if rec.get("subtype") == "stop_hook_summary":
                    # Stop hooks are logged here, not as attachments: every hook that ran when the agent tried to stop
                    for hi in rec.get("hookInfos") or []:
                        if isinstance(hi, dict) and hi.get("command"):
                            r["hook_runs"][("Stop", str(hi["command"])[:200])] += 1
                    if rec.get("preventedContinuation") or rec.get("hookErrors"):
                        r["hook_runs"][("Stop (blocked or errored)", "any Stop hook")] += 1
                    continue
                if rec.get("subtype") == "compact_boundary":
                    md = rec.get("compactMetadata") or {}
                    r["compactions"].append({"t": t, "trigger": md.get("trigger"), "pre": md.get("preTokens")})
                continue
            if kind == "assistant":
                m = rec.get("message") or {}
                if m.get("model"):
                    r["models"][m["model"]] += 1
                for b in m.get("content") or []:
                    if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                        continue
                    name = b.get("name") or "?"
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    r["tools"][name] += 1
                    if job_task and name not in NOT_A_RETURN_TO_A_JOB:
                        named = json.dumps(inp)
                        job_back.update(tid for tid in job_task if tid in named)
                    if name.startswith("mcp__"):
                        r["mcp"][name.split("__")[1]] += 1
                    elif name == "Bash":
                        cmd = inp.get("command") or ""
                        if "skills/" in cmd:
                            r["skill_ev"].append((t, "bash", cmd[:600]))
                        if "plugin" in cmd and PLUGIN_EVAL_RX.search(cmd):      # `claude plugin eval <target>` run as a tool call
                            for tg in plugin_eval_calls(cmd, rec.get("cwd") or ""):
                                r["skill_ev"].append((t, "plugin_eval", tg))
                        if runs_a_script_or_test(cmd):      # the files it names: a test or eval script reads cases from some of them
                            named = files_named_by(cmd, rec.get("cwd") or "")
                            if named and named not in script_seen:
                                script_seen.add(named)
                                r["skill_ev"].append((t, "script", named))
                        for p in set(programs_of(cmd)):
                            r["progs"][p] += 1
                        if "git worktree add" in cmd:
                            r["worktree_adds"] += 1
                        job_bash[b.get("id")] = (bool(inp.get("run_in_background")), bool(POLL_LOOP_RX.search(cmd)))
                        if inp.get("run_in_background"):
                            r["bg_bash"] += 1
                        for d in dirs_of(PATH_RX.findall(cmd)[:6]):
                            r["dirs_read"][d] += 1
                        if WORKISH_RX.search(cmd):
                            for d in worked_dirs_of(cmd, rec.get("cwd") or ""):
                                if worked_repo(d):
                                    r["worked"][worked_repo(d)] += 1
                    elif name in ("Agent", "Task"):
                        r["agents"].append([inp.get("subagent_type") or "general-purpose", inp.get("model"),
                                            bool(inp.get("run_in_background")), (inp.get("description") or "")[:60]])
                        if inp.get("name"):
                            r["agent_names"].add(str(inp["name"]))
                    elif name == "SendMessage":
                        r["send_to"].append(str(inp.get("to") or ""))
                    elif name == "Workflow":
                        r["workflows"].append(str(inp.get("name") or inp.get("scriptPath") or
                                                  (re.search(r"name:\s*['\"]([^'\"]+)", inp.get("script") or "") or [None, "inline"])[1])[:80])
                    elif name == "Skill":
                        r["skills"][str(inp.get("skill") or inp.get("command") or "?")[:60]] += 1
                        if inp.get("skill") or inp.get("command"):
                            r["skill_ev"].append((t, "load", str(inp.get("skill") or inp.get("command"))[:60]))
                    elif name == "ExitPlanMode":
                        r["plan_calls"] += 1
                        plan_ids.add(b.get("id"))
                    elif name in ("Read", "Grep", "Glob"):
                        p = inp.get("file_path") or inp.get("path") or ""
                        if name == "Read" and "skills/" in p:
                            r["skill_ev"].append((t, "read", p))
                        if p:
                            for d in dirs_of([p]):
                                r["dirs_read"][d] += 1
                    elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        p = inp.get("file_path") or inp.get("notebook_path") or ""
                        if "skills/" in p:
                            r["skill_ev"].append((t, "edit", p))
                        if p:
                            for d in dirs_of([p]):
                                r["dirs_written"][d] += 1
                            q = os.path.expanduser(p)
                            q = q if q.startswith("/") else os.path.join(rec.get("cwd") or "/", q)
                            if worked_repo(os.path.dirname(q)):
                                r["worked"][worked_repo(os.path.dirname(q))] += 1
                            wd = r.setdefault("written_dirs", set())   # whole folders, for ownership.written_in_logs
                            if len(wd) < 3000:
                                wd.add(os.path.dirname(p.replace("~", HOME, 1) if p.startswith("~") else p))
                            base = os.path.basename(p)
                            if base in INSTRUCTION_NAMES or base == "SKILL.md" or "/agents/" in p and p.endswith(".md"):
                                r["instr_edits"][p] += 1
                continue
            if kind == "user":
                content = (rec.get("message") or {}).get("content")
                head = content if isinstance(content, str) else " ".join(
                    x.get("text", "") for x in (content or []) if isinstance(x, dict) and x.get("type") == "text")
                if rec.get("promptSource") in ("typed", "queued") or (
                        rec.get("promptSource") == "sdk" and rec.get("entrypoint") in PERSON_APPS   # the person, in VS Code / desktop / JetBrains
                        and (rec.get("origin") or {}).get("kind") in (None, "human")) or (
                        rec.get("promptSource") is None and head and not rec.get("isMeta")
                        and not rec.get("isCompactSummary") and not head.lstrip().startswith("<")):
                    last_typed = head
                if head.lstrip().startswith(SESSION_MSG) and not IDLE_RX.search(head[:800]):
                    r["session_msgs_in"] += 1
                if isinstance(content, str) and "<task-notification>" in content[:200]:
                    for tid, st_ in NOTIFICATION_RX.findall(content):
                        job_end[tid] = st_
                if rec.get("isMeta") and head.startswith(SKILL_LOADED_PREFIX):
                    # Claude Code's own record that a skill's text was loaded, by a Skill call or a typed /name, in any run
                    folder = head[len(SKILL_LOADED_PREFIX):].split("\n", 1)[0].strip().rstrip("/")
                    if folder:
                        r["skill_ev"].append((t, "loaded", os.path.basename(folder)[:60]))
                tr = rec.get("toolUseResult")
                if isinstance(content, list):
                    for b in content:
                        if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                            continue
                        if isinstance(tr, dict) and tr.get("backgroundTaskId"):
                            job_task[str(tr["backgroundTaskId"])] = b.get("tool_use_id")
                        if not b.get("is_error"):
                            continue
                        job_failed.add(b.get("tool_use_id"))
                        r["tool_errors"] += 1
                        body = b.get("content")
                        body = body if isinstance(body, str) else " ".join(
                            x.get("text", "") for x in (body or []) if isinstance(x, dict))
                        if b.get("tool_use_id") in plan_ids and any(x in body for x in REJECT):
                            r["plan_rejected"] += 1
                        low = body[:300].lower()
                        if "hook" in low and ("block" in low or "denied" in low or "deny" in low or "refus" in low):
                            m = TAG_RX.search(body[:300])
                            r["hook_denials"][re.sub(r"\s+", " ", body[:90])] += 1
                    if r["first_prompt"] is None and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                        txt = " ".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
                        if txt.strip() and not txt.lstrip().startswith(("<", "Caveat:")):
                            r["first_prompt"] = txt.strip()[:200]
                elif isinstance(content, str):
                    st = content.lstrip()
                    if st.startswith("<bash-input>") and "plugin" in st and PLUGIN_EVAL_RX.search(st):    # typed: `!claude plugin eval <target>`
                        for tg in plugin_eval_calls(st[12:st.find("</bash-input>")], rec.get("cwd") or ""):
                            r["skill_ev"].append((t, "plugin_eval", tg))
                    # Claude Code writes a command two ways: <command-name> first (built-in commands) or <command-message> first
                    # (skills and plugin commands, followed by an isMeta record holding what they expanded to; 11-06: 254 of 465).
                    # A skill or plugin command a plugin or a hook started (origin kind other than human) is not the person typing.
                    nonhuman = (rec.get("origin") or {}).get("kind") not in (None, "human")
                    by_message = st.startswith("<command-message>") and "<command-name>" in st and not nonhuman
                    if by_message:
                        st = st[st.find("<command-name>"):]
                    if st.startswith("<command-name>"):
                        nm = st[len("<command-name>"):st.find("</command-name>")]
                        a0 = st.find("<command-args>")
                        args = st[a0 + 14:st.find("</command-args>")].strip() if a0 >= 0 else ""
                        r["commands"].append([nm, bool(args), args[:80]])
                        if not by_message:   # the skill evidence of a skill command stays what it was: its expansion record is read at SKILL_LOADED_PREFIX
                            r["skill_ev"].append((t, "load", nm.strip().lstrip("/")[:60]))
                    elif r["first_prompt"] is None and st and not st.startswith(("<", "Caveat:")):
                        r["first_prompt"] = st[:200]
                pm = rec.get("permissionMode")
                if pm and not rec.get("isMeta") and not rec.get("toolUseResult"):
                    r["pm"][pm] += 1
    if r["t0"] is None:
        return None
    # Jobs Claude watched until they ended, besides Monitor calls: a background command that finished (Claude Code's
    # notice) and that Claude came back to with a later call naming it, or that was itself a poll loop; and a poll loop
    # run in the foreground that returned without an error. A background command nobody came back to counts for nothing.
    for tid, use_id in job_task.items():
        if job_end.get(tid) not in JOB_ENDED:
            continue
        if (job_bash.get(use_id) or (False, False))[1]:
            r["jobs_poll_loops"] += 1
        elif tid in job_back:
            r["jobs_came_back"] += 1
    r["jobs_poll_loops"] += sum(1 for use_id, (bg, loop) in job_bash.items() if loop and not bg and use_id not in job_failed)
    # Counters with tuple keys don't survive pickling into JSON later; keep them as lists
    for k in ("hooks", "hook_blocks", "hook_msgs", "hook_runs"):
        r[k] = [[list(key), v] for key, v in r[k].items()]
    r["agent_names"] = sorted(r["agent_names"])
    r["written_dirs"] = sorted(r.get("written_dirs", ()))
    r["worked"] = dict(r["worked"].most_common(8))
    return r


def scan_logs(days, root=PROJECTS):
    start = clock.now() - days * 86400
    mains = [p for p in glob.glob(os.path.join(root, "*", "*.jsonl")) if os.path.getmtime(p) >= start]
    helpers = [p for p in glob.glob(os.path.join(root, "*", "*", "subagents", "*.jsonl")) if os.path.getmtime(p) >= start]
    jobs = [(p, start, False) for p in mains] + [(p, start, True) for p in helpers]
    with Pool(max(1, (os.cpu_count() or 2) - 1)) as pool:
        got = [x for x in pool.imap_unordered(read_log_limited, jobs, chunksize=6) if x]
    recs = [x for x in got if "timed_out" not in x]
    timed_out = sorted(x["timed_out"] for x in got if "timed_out" in x)
    return recs, len(mains), len(helpers), timed_out


# ---------------------------------------------------------------- aggregation of the logs


def top(counter, n=25):
    return [[k, v] for k, v in Counter(counter).most_common(n)]


def chats_counter():
    return defaultdict(lambda: [0, set()])


def add(dd, key, n, chat):
    dd[key][0] += n
    dd[key][1].add(chat)


def flat(dd, n=30, key=lambda kv: -kv[1][0]):
    return [{"what": k if not isinstance(k, tuple) else list(k), "calls": v[0], "chats": len(v[1])}
            for k, v in sorted(dd.items(), key=key)[:n]]


def typed_times(chats_path):
    ts = []
    try:
        with open(chats_path) as f:
            f.readline()
            for line in f:
                ts.extend(m["t"] for m in json.loads(line)["msgs"])
    except (OSError, ValueError, KeyError):
        pass
    return sorted(ts)


def _cron_field_hits(spec, value, lo, hi):
    """Whether a cron field ("*", "5", "1-5", "*/15", "1,3") covers the value."""
    for part in str(spec).split(","):
        step = 1
        if "/" in part:
            part, _, st = part.partition("/")
            step = int(st) if st.isdigit() and int(st) > 0 else 1
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, _, b = part.partition("-")
            a, b = int(a), int(b)
        else:
            a = b = int(part)
        if a <= value <= b and (value - a) % step == 0:
            return True
    return False


def _calendar_hits(fire, lt):
    """Whether a calendar entry {minute, hour, day, month, weekday} (missing = any) fires at local time `lt` (a struct_time).
    Weekday: 0 or 7 is Sunday, as in launchd and cron."""
    wd = (lt.tm_wday + 1) % 7   # struct_time Monday=0 → cron Sunday=0
    for key, value, lo, hi in (("minute", lt.tm_min, 0, 59), ("hour", lt.tm_hour, 0, 23), ("day", lt.tm_mday, 1, 31),
                               ("month", lt.tm_mon, 1, 12), ("weekday", wd, 0, 7)):
        spec = fire.get(key)
        if spec in (None, ""):
            continue
        try:
            ok = _cron_field_hits(spec, value, lo, hi) or (key == "weekday" and value == 0 and _cron_field_hits(spec, 7, lo, hi))
        except ValueError:
            return False
        if not ok:
            return False
    return True


PROMPT_PREFIX = 60   # characters of a run's first prompt compared with a file
PROMPT_MIN = 12      # a shorter prompt says nothing about where it came from
_JSON_ESCAPES = {"n": " ", "t": " ", "r": " ", '"': '"', "\\": "\\", "/": "/"}


def norm_ws(x):
    return re.sub(r"\s+", " ", str(x))


def prompt_key(prompt):
    """What is compared of a headless run's first prompt: whitespace-normalised, its first PROMPT_PREFIX characters."""
    return norm_ws((prompt or "").strip())[:PROMPT_PREFIX]


def searchable(text):
    """A file's text as a prompt would be found in it: whitespace-normalised, and (when the file escapes) once more with a JSON
    string's escapes undone, since a prompt with a line break sits in a .json or .jsonl file as backslash-n."""
    text = norm_ws(text)
    if "\\" in text:
        text += " " + norm_ws(re.sub(r'\\(["\\/nrt])', lambda m: _JSON_ESCAPES[m.group(1)], text))
    return text


class SchedulerStarts:
    """Which headless runs a scheduler started (a LaunchAgent, a crontab line) rather than the person or a hook of theirs.
    A job counts when its command or script mentions claude. A run is its child when (1) the run's first prompt is written
    in the job (its arguments, its script, or a prompt file the script names), or (2) the job's calendar entry fires in the
    minute the run began (local time, as launchd and cron read it). Read from the person's own job files, never from a name."""
    TOLERANCE = 90   # seconds a job may take to start (launchd fires on the minute)

    def __init__(self, home=None):
        self.home = home or HOME
        self.jobs = []   # {"text": normalized text the job holds, "fires": [calendar dicts], "cwd": str|None}
        self._read_launchd()
        self._read_cron()

    _norm = staticmethod(norm_ws)

    def _job_text(self, args, cwd):
        """The words a job holds: its arguments, its script, and the prompt files the script names (each up to 200 kB)."""
        base = [self._norm(" ".join(str(a) for a in args))]
        seen = set()
        todo = [os.path.expanduser(str(a)) for a in args if isinstance(a, str)]
        for _ in range(2):   # the script, then the files it names
            nxt = []
            for pth in todo:
                if not pth.startswith("/") or pth in seen or not os.path.isfile(pth):
                    continue
                seen.add(pth)
                try:
                    with open(pth, errors="replace") as f:
                        txt = f.read(200_000)
                except OSError:
                    continue
                base.append(self._norm(txt))
                for m in re.findall(r"[~$A-Za-z0-9_./{}-]+\.(?:md|txt|prompt)\b", txt):
                    m = os.path.expandvars(os.path.expanduser(m.replace("${HOME}", self.home).replace("$HOME", self.home)))
                    for root in (os.path.dirname(pth), cwd or "", self.home):
                        cand = m if m.startswith("/") else os.path.join(root, m)
                        if root and os.path.isfile(cand):
                            nxt.append(cand)
            todo = nxt
        return " ".join(base)

    def _read_launchd(self):
        import plistlib
        from xml.parsers.expat import ExpatError
        for f in glob.glob(os.path.join(self.home, "Library", "LaunchAgents", "*.plist")):
            try:
                with open(f, "rb") as fh:
                    d = plistlib.load(fh)
            except (OSError, ValueError, ExpatError):   # plistlib.InvalidFileException is a ValueError
                continue
            args = list(d.get("ProgramArguments") or ([d["Program"]] if d.get("Program") else []))
            cwd = d.get("WorkingDirectory")
            text = self._job_text(args, cwd)
            if not re.search(r"(?i)claude", text):
                continue
            cal = d.get("StartCalendarInterval") or []
            cal = [cal] if isinstance(cal, dict) else cal
            fires = [{"minute": c.get("Minute"), "hour": c.get("Hour"), "day": c.get("Day"), "month": c.get("Month"),
                      "weekday": c.get("Weekday")} for c in cal if isinstance(c, dict)]
            self.jobs.append({"text": text, "fires": fires, "cwd": cwd})

    def _read_cron(self):
        try:
            r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return
        if r.returncode:
            return
        for ln in r.stdout.splitlines():
            s = ln.strip()
            parts = s.split(None, 5)
            if not s or s.startswith(("#", "@")) or len(parts) < 6 or re.match(r"^[A-Za-z_]\w*=", s):
                continue
            cmd = parts[5]
            try:
                args = shlex.split(cmd)
            except ValueError:
                args = cmd.split()
            text = self._job_text(args, None)
            if re.search(r"(?i)claude", text):
                m, h, dom, mon, dow = parts[:5]
                self.jobs.append({"text": text, "fires": [{"minute": m, "hour": h, "day": dom, "month": mon, "weekday": dow}], "cwd": None})

    def started(self, run):
        """Why a scheduler is this run's parent: "its prompt is in a job", "a job fires at its start", or None."""
        prompt = prompt_key(run.get("first_prompt"))
        t0 = run.get("t0")
        for j in self.jobs:
            if len(prompt) >= PROMPT_MIN and prompt in j["text"]:
                return "its prompt is in a job"
            # a calendar entry with no minute (launchd reads that as every minute of the hour) says nothing about one run
            fires = [fr for fr in j["fires"] if str(fr.get("minute") if fr.get("minute") is not None else "*") not in ("*", "")]
            if t0 is not None and fires and (not j["cwd"] or j["cwd"] == run.get("cwd")):
                for off in range(0, int(self.TOLERANCE) + 1, 30):
                    if any(_calendar_hits(fr, time.localtime(t0 - off)) for fr in fires):
                        return "a job fires at its start"
        return None


def quiet_before(t, times, window=1800):
    import bisect
    i = bisect.bisect_right(times, t)
    return i == 0 or t - times[i - 1] > window


def aggregate(recs, interactive_ids, times=(), scheduler=None):
    """interactive_ids: the typed chats (logs.py); a main file that isn't one of them and has an sdk entrypoint is
    headless. Helper files count toward the chat that started them (their parent folder name)."""
    out = {}
    typed = [r for r in recs if not r["helper"] and not (r["ep"] or "").startswith(UNATTENDED)]
    headless = [r for r in recs if not r["helper"] and (r["ep"] or "").startswith(UNATTENDED)]
    helpers = [r for r in recs if r["helper"]]
    parent = lambda r: os.path.basename(os.path.dirname(os.path.dirname(r["file"])))  # noqa: E731

    # tools in main chats and in helpers
    def tool_block(rs, chat_of):
        t, p, m = chats_counter(), chats_counter(), chats_counter()
        for r in rs:
            c = chat_of(r)
            for k, v in r["tools"].items():
                add(t, k, v, c)
            for k, v in r["progs"].items():
                add(p, k, v, c)
            for k, v in r["mcp"].items():
                add(m, k, v, c)
        return {"by_tool": flat(t, 40), "by_program": flat(p, 60), "by_mcp_server": flat(m, 30)}
    out["tools"] = {"typed_chats": tool_block(typed, lambda r: r["chat"]),
                    "in_helpers": tool_block(helpers, parent),
                    "headless": tool_block(headless, lambda r: r["chat"])}

    # helpers asked for in typed chats
    ag = defaultdict(lambda: {"calls": 0, "chats": set(), "models": Counter(), "background": 0, "examples": []})
    for r in typed:
        for typ, model, bg, desc in r["agents"]:
            a = ag[typ]
            a["calls"] += 1
            a["chats"].add(r["chat"])
            a["models"][model or "(not set)"] += 1
            a["background"] += bg
            if len(a["examples"]) < 3 and desc:
                a["examples"].append(f"{r['chat'][:8]} {day(r['t0'])}: {scrub(desc)}")
    calls = sum(a["calls"] for a in ag.values())
    model_set = sum(a["calls"] - a["models"].get("(not set)", 0) for a in ag.values())
    out["helpers"] = {"calls": calls, "chats": len({c for a in ag.values() for c in a["chats"]}),
                      "calls_that_set_a_model": model_set, "background_calls": sum(a["background"] for a in ag.values()),
                      "helper_files_in_window": len(helpers),
                      "by_type": sorted(({"type": k, "calls": a["calls"], "chats": len(a["chats"]),
                                          "models": dict(a["models"]), "background": a["background"], "examples": a["examples"]}
                                         for k, a in ag.items()), key=lambda x: -x["calls"])[:40]}

    wf = chats_counter()
    for r in typed:
        for w in r["workflows"]:
            add(wf, w, 1, r["chat"])
    out["workflows"] = {"runs": sum(v[0] for v in wf.values()), "chats": len({c for v in wf.values() for c in v[1]}),
                        "by_name": flat(wf, 30)}

    sk = chats_counter()
    for r in typed:
        for k, v in r["skills"].items():
            add(sk, k, v, r["chat"])
    out["skills_used"] = flat(sk, 50)

    out["plan_dialogs"] = {"calls": sum(r["plan_calls"] for r in typed), "refused": sum(r["plan_rejected"] for r in typed),
                           "chats": sum(1 for r in typed if r["plan_calls"]),
                           "chats_with_a_refusal": sum(1 for r in typed if r["plan_rejected"])}

    # hook context: by event + tag (the plugin's name in brackets), attachments and chats, typed vs helper vs headless
    def hook_block(rs, chat_of, n=60):
        h, ex = chats_counter(), {}
        for r in rs:
            for (event, tag, text), v in r["hooks"]:
                ev = event.split(":")[0] if event.startswith(("SessionStart", "Stop")) else event
                add(h, (ev, tag), v, chat_of(r))
                ex.setdefault((ev, tag), Counter())[text] += v
        rows = flat(h, n)
        for row in rows:
            row["texts"] = [scrub(t)[:140] for t, _ in ex[tuple(row["what"])].most_common(2)]
        return rows
    blocks, bex = chats_counter(), {}
    for r in typed + helpers:
        for (event, tag, text), v in r["hook_blocks"]:
            add(blocks, (event, tag), v, r["chat"] if not r["helper"] else parent(r))
            bex.setdefault((event, tag), Counter())[text] += v
    brows = flat(blocks, 30)
    for row in brows:
        row["texts"] = [scrub(t)[:140] for t, _ in bex[tuple(row["what"])].most_common(2)]
    msgs = chats_counter()
    for r in typed:
        for (event, tag), v in r["hook_msgs"]:
            add(msgs, (event, scrub(tag)), v, r["chat"])
    den = chats_counter()
    for r in typed + helpers:
        for k, v in r["hook_denials"].items():
            add(den, scrub(k), v, r["chat"] if not r["helper"] else parent(r))
    tagged_chats = {r["chat"] for r in typed if r["hooks"]}
    out["hook_context"] = {
        "typed_chats_with_hook_text": len(tagged_chats), "typed_chats": len(typed),
        "attachments_in_typed_chats": sum(v for r in typed for _, v in r["hooks"]),
        "by_event_and_tag_typed": hook_block(typed, lambda r: r["chat"]),
        "by_event_and_tag_helpers": hook_block(helpers, parent, 25),
        "by_event_and_tag_headless": hook_block(headless, lambda r: r["chat"], 25),
        "blocking_errors": brows, "messages_shown_to_person": flat(msgs, 20), "tool_errors_naming_a_hook": flat(den, 20)}

    runs = chats_counter()
    for r in typed:
        for (event, cmd), v in r["hook_runs"]:
            add(runs, (event, cmd), v, r["chat"])
    out["hook_runs_typed"] = runs
    hruns = chats_counter()
    for r in headless:
        for (event, cmd), v in r["hook_runs"]:
            add(hruns, (event, cmd), v, r["chat"])
    out["hook_runs_headless"] = hruns

    nested = chats_counter()
    for r in typed:
        for p, v in r["nested"].items():
            add(nested, p, v, r["chat"])
    out["rules_reaching"] = {"chats_where_a_subfolder_file_reached_the_agent": len({c for v in nested.values() for c in v[1]}),
                             "files": len(nested), "by_file": flat(nested, len(nested))}   # every file: the board asks about one rule file at a time

    comp = []
    for r in typed:
        for c in r["compactions"]:
            comp.append((r["chat"], c))
    manual_cmds = [(r["chat"], a) for r in typed for (nm, has, a) in r["commands"] if nm.strip("/") == "compact"]
    pres = sorted(c["pre"] for _, c in comp if c.get("pre"))
    per_chat = Counter(ch for ch, _ in comp)
    out["compactions"] = {
        "total": len(comp), "by_trigger": dict(Counter(c.get("trigger") or "?" for _, c in comp)),
        "typed_compact_commands": len(manual_cmds), "with_focus_text": sum(1 for _, a in manual_cmds if a),
        "focus_text_examples": [f"{ch[:8]}: {scrub(a)[:80]}" for ch, a in manual_cmds if a][:5],
        "median_tokens_before": pres[len(pres) // 2] if pres else None, "max_tokens_before": pres[-1] if pres else None,
        "chats_compacted": len(per_chat), "chats_compacted_more_than_once": sum(1 for v in per_chat.values() if v > 1)}

    cmds = chats_counter()
    for r in typed:
        if r["chat"] not in interactive_ids:   # the unit is the sessions you typed in, as for slash_commands
            continue
        for nm, has, a in r["commands"]:
            add(cmds, nm if nm.startswith("/") else "/" + nm, 1, r["chat"])
    out["slash_commands_in_chats"] = flat(cmds, 60)

    # headless runs: grouped by repo and prompt template (the first 60 characters with numbers and ids blanked)
    groups = defaultdict(lambda: {"runs": 0, "days": set(), "pm": Counter(), "models": Counter(), "repo": None,
                                  "example": None, "eps": Counter(), "quiet": 0, "away": 0})
    # "while you were away": nothing typed in the 30 minutes before the run began, or a scheduler (a LaunchAgent, a cron
    # line) is its parent: a job fired at a time nobody typed is no less unattended when the person was typing at that minute
    by_scheduler = {r["file"]: scheduler.started(r) if scheduler else None for r in headless}
    away = {r["file"]: quiet_before(r["t0"], times) or bool(by_scheduler[r["file"]]) for r in headless}
    for r in headless:
        tpl = DIGITS.sub("#", UUID.sub("<id>", (r["first_prompt"] or "(no prompt)")[:60]))
        rr = place(r["cwd"])
        g = groups[(rr, tpl)]
        g["runs"] += 1
        g["days"].add(day(r["t0"]))
        g["pm"].update(r["pm"])
        g["models"].update({m: 1 for m in r["models"]})
        g["eps"][r["ep"]] += 1
        g["quiet"] += quiet_before(r["t0"], times)
        g["away"] += away[r["file"]]
        g["repo"] = rr
        g["example"] = g["example"] or f"{r['chat'][:8]} {day(r['t0'])}"
    by_repo = Counter()
    for (rr, _), g in groups.items():
        by_repo[rr] += g["runs"]
    out["headless"] = {
        "runs": len(headless), "days_with_runs": len({day(r["t0"]) for r in headless}),
        "started_with_no_typed_message_in_30_min_before": sum(quiet_before(r["t0"], times) for r in headless),
        "started_by_a_scheduled_job": sum(bool(v) for v in by_scheduler.values()),
        "while_away": sum(away.values()),
        "while_away_rule": "nothing typed by the person in the 30 minutes before the run began, or a scheduler is the run's parent "
                           "(a LaunchAgent or cron line whose prompt is the run's prompt, or whose calendar entry fires at its start)",
        "entrypoints": dict(Counter(r["ep"] for r in headless)),
        "permission_modes": dict(sum((r["pm"] for r in headless), Counter())),
        "by_repo": top(by_repo, 15),
        "by_template": sorted(({"repo": g["repo"], "template": scrub(tpl), "runs": g["runs"], "days": len(g["days"]),
                                "first_day": min(g["days"]), "last_day": max(g["days"]),
                                "permission_modes": dict(g["pm"]), "models": dict(g["models"]), "example": g["example"],
                                "started_with_no_typed_message_in_30_min_before": g["quiet"], "while_away": g["away"]}
                               for (rr, tpl), g in groups.items()), key=lambda x: -x["runs"])[:30]}

    # typed chats overview
    pm = sum((r["pm"] for r in typed), Counter())
    out["chats_logged"] = {"typed_chat_files": len(typed), "entrypoints": dict(Counter(r["ep"] for r in typed)),
                           "permission_modes_of_typed_messages": dict(pm),
                           "versions": top(Counter(r["version"] for r in typed if r["version"]), 5),
                           "skill_listing_words_max": max((r["skill_listing_words"] for r in typed), default=0),
                           "worktree_adds_by_shell": sum(r["worktree_adds"] for r in typed + helpers),
                           "background_shell_runs": sum(r["bg_bash"] for r in typed),
                           "attachments": top(sum((r["attach"] for r in typed), Counter()), 40)}

    # jobs Claude watched until they ended, other than Monitor calls (read_log): in your sessions and inside sub-agents
    out["jobs_watched"] = {k: {"background_came_back": sum(r["jobs_came_back"] for r in rs),
                               "poll_loops": sum(r["jobs_poll_loops"] for r in rs)}
                           for k, rs in (("typed_chats", typed), ("in_helpers", helpers))}

    # directories the agent read or wrote: the raw material for memory-store reads and plan reuse
    rd, wr = chats_counter(), chats_counter()
    for r in typed + helpers:
        c = r["chat"] if not r["helper"] else parent(r)
        for d, v in r["dirs_read"].items():
            add(rd, d, v, c)
        for d, v in r["dirs_written"].items():
            add(wr, d, v, c)
    ie = chats_counter()
    for r in typed:
        for p, v in r["instr_edits"].items():
            add(ie, p, v, r["chat"])
    out["instruction_edits"] = {"files": len(ie), "chats": len({c for v in ie.values() for c in v[1]}),
                                "edits": sum(v[0] for v in ie.values()), "by_file": flat(ie, 30)}

    # who did it, raw (who_did_it() turns this into the page's groups)
    own_sent = other_sent = 0
    sent_chats = set()
    for r in typed:
        names = set(r["agent_names"])
        for to in r["send_to"]:
            sent_chats.add(r["chat"])
            if to in names:
                own_sent += 1
            else:
                other_sent += 1
    att = {}
    for k in MENTIONS:
        seen, chats_k, copies = set(), set(), 0
        for r in sorted(typed, key=lambda r: r["t0"] or 0):
            for u in r["mention_ids"].get(k, ()):
                if u in seen:
                    copies += 1
                    continue
                seen.add(u)
                chats_k.add(r["chat"])
        att[k] = {"n": len(seen), "chats": len(chats_k), "copies": copies}
    att["file"]["reattached"] = sum(r["file_reattached"] for r in typed)
    out["who_raw"] = {
        "ctx_by_chat": {r["chat"]: sorted(r["ctx"]) for r in typed if r["ctx"]},
        "automem_chats": sorted(r["chat"] for r in typed if r["automem_t"] is not None),
        "block_cmds": sum((r["block_cmds"] for r in typed), Counter()),
        "blocks_by_name": sum((r["blocks_by_name"] for r in typed), Counter()),
        "sched_fires": sum(r["sched_fires"] for r in typed + headless),
        "nested_by_chat": {r["chat"]: list(r["nested"]) for r in typed if r["nested"]},
        "send_to_own_helper": own_sent, "send_to_other": other_sent, "send_chats": len(sent_chats),
        "session_msgs_in": sum(r["session_msgs_in"] for r in typed),
        "session_msgs_in_chats": sum(1 for r in typed if r["session_msgs_in"]),
        "agent_settings": dict(Counter(r["agent_setting"] for r in typed if r["agent_setting"]).most_common(8)),
        "agent_setting_chats": sum(1 for r in typed if r["agent_setting"]),
        # background sessions among the sessions the person typed in (a share of those, never above them); background
        # sessions with no message of theirs (started there by something else) are a different unit and counted apart
        "bg_sessions_typed": sum(1 for r in typed if r["bg_kind"] and r["chat"] in interactive_ids),
        "bg_sessions_no_message_of_yours": sum(1 for r in typed if r["bg_kind"] and r["chat"] not in interactive_ids),
        "bg_sessions_headless": sum(1 for r in headless if r["bg_kind"]),
        "attachments": att}
    return out, rd, wr


def helper_meta(start, typed_ids):
    """The small record Claude Code keeps next to each helper's log (subagents/*.meta.json): how it was started (a
    helper call from Claude carries toolUseId; a /subtask fork and a team member don't), team, depth, stopped by the
    person, own worktree. Not "background": requestShape reads "background" whether or not Claude set run_in_background
    (checked 11-04: 667 of 1,136 such helpers were started without it). Helpers of chats the person typed in only."""
    n, ch, teams = Counter(), defaultdict(set), set()
    for p in glob.glob(os.path.join(PROJECTS, "*", "*", "subagents", "*.meta.json")):
        chat = os.path.basename(os.path.dirname(os.path.dirname(p)))
        if chat not in typed_ids:
            continue
        try:
            if os.path.getmtime(p) < start:
                continue
            m = json.load(open(p))
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict):
            continue
        hits = ["helpers"]
        if m.get("toolUseId"):
            hits.append("started_by_a_claude_call")
        elif m.get("isFork"):
            hits.append("forks_without_a_claude_call")
            if str(m.get("description") or "").startswith("<btw>"):
                hits.append("btw_forks")
        if m.get("teamName") or m.get("taskKind") == "in_process_teammate":
            hits.append("teammates")
            teams.add((chat, str(m.get("teamName"))))
        if (m.get("spawnDepth") or 0) >= 2:
            hits.append("started_by_a_helper")
        if m.get("stoppedByUser"):
            hits.append("stopped_by_the_person")
        if m.get("spawnedWithWorktree"):
            hits.append("own_worktree")
        for k in hits:
            n[k] += 1
            ch[k].add(chat)
    return {**{k: {"n": v, "chats": len(ch[k])} for k, v in n.items()}, "teams": len(teams)}


def typed_concurrency(chats, window=600):
    """Several chats driven at once, from typed messages only (a chat left open and idle doesn't count): the most
    different chats typed into within `window` seconds, and how many messages were typed while 3+ chats got a typed
    message within window/2 either side."""
    import bisect
    ev = sorted((m["t"], c["chat"]) for c in chats for m in c.get("msgs", []))
    best, j, live = 0, 0, Counter()
    for t, c in ev:
        live[c] += 1
        while ev[j][0] < t - window:
            live[ev[j][1]] -= 1
            if not live[ev[j][1]]:
                del live[ev[j][1]]
            j += 1
        best = max(best, len(live))
    ts = [t for t, _ in ev]
    multi = sum(1 for t, _ in ev
                if len({ev[x][1] for x in range(bisect.bisect_left(ts, t - window / 2), bisect.bisect_right(ts, t + window / 2))}) >= 3)
    return {"most_chats_in_10_min": best, "messages_with_3_plus_chats": multi, "messages": len(ev)}


# What each who_did_it count can't tell you, keyed by its id; the page shows it beside the proof.
WHO_DID_IT_CANT_TELL = {
    'you.typed_messages': 'How long or how good the messages were, only how many.',
    'you.btw_side_questions': 'Whether you used the answer, or whether the question needed its own session.',
    'you.subtask_forks': 'What the sub-agent brought back, or whether you used it.',
    'you.goals_set': 'Whether the goal was met, or how clearly it was worded.',
    'you.plans_asked': 'Whether you read or changed the plan before saying go.',
    'you.chats_split_off': 'Why you split it off, or whether the side session paid off.',
    'you.own_commands_typed': "What the command did, or whether it helped.",
    'you.helper_mentions': 'Whether the sub-agent you named did the job well.',
    'you.skill_mentions': 'Whether you only wrote the name or the skill actually ran.',
    'you.file_mentions': "Whether those were the right files; files Claude found on its own aren't here.",
    'you.ultracode_requests': 'What the workflow did, or whether its result was kept.',
    'you.esc_interrupts': 'Why you stopped it: a wrong turn, a change of mind and impatience look the same.',
    'you.steps_refused': 'Why you rejected it, unless you typed a reason with it.',
    'you.helpers_stopped_by_hand': 'Why you stopped it, or whether it was off track.',
    'you.manual_compactions': 'Whether what mattered survived the compaction.',
    'you.plan_mode_switched_on': 'Whether the plan changed what got built: switching it on and saying go at once looks the same.',
    'you.chats_at_once': 'Whether the sessions were separate jobs or one job spread out, or whether any of them clashed.',
    'you.most_chats_in_10_min': "Whether it's a habit: this is a peak, and one busy afternoon sets it.",
    'setup.subagents_after_own_context': "Whether that text is why Claude started them; it might have anyway.",
    'setup.sessions_as_named_agent': 'Whether you typed claude --agent yourself or a launcher of yours did it, or what that agent does differently.',
    'claude.automemory_at_start': "Whether the notes were still true or useful: the record shows they were in context, not that Claude used them.",
    'setup.own_hook_context_at_start': "Whether the text held earlier work or a fixed note, or whether Claude used it.",
    'cant.early_reads_of_earlier_work': "Whether Claude did it on its own or because a rule or skill of yours told it to, or whether it was the right file.",
    'you.sessions_backgrounded': "Whether you sent each one to the background, or another session or a program started it there.",
    'setup.own_agent_launches': "Who chose to start them, you or Claude, or whether their work was used. A forked or resumed session repeats its parent's records, so some are counted twice.",
    'setup.hook_blocks': 'Whether each block was right, or whether the hook is yours or came with a plugin.',
    'setup.subfolder_rules_reached': 'Whether you wrote the rule file, or whether Claude followed it once it loaded.',
    'setup.hook_text_chats': 'Which of these hooks are yours and which came with an add-on.',
    'programs.runs_started_by_code': 'Whether the runs did useful work; a loop that retries a failure counts every try.',
    'programs.runs_unattended': 'Whether anyone checked the result afterwards.',
    'programs.scheduled_jobs': 'Jobs started by other schedulers (CI, launchd jobs with other names) may be missing.',
    'programs.own_eval_folders_changed': "Whether the evals ran, or whether a failing one stops anything; a change can be an edit, not a run.",
    'probably.helpers_after_ask': "For sure that you asked: a word match misses some asks and catches some messages that aren't. A forked or resumed session repeats its parent's records, so some are counted twice.",
    'probably.worktree_mentions': 'Whether a worktree followed, or whether you were only asking about one.',
    'probably.corrections': "Corrections worded another way or in another language; and some matches aren't corrections.",
    'cant.helper_calls_unattributed': "Who started them: Claude's own choice, or a skill or rule of yours the log doesn't name. A forked or resumed session repeats its parent's records, so some are counted twice.",
    'cant.messages_to_own_helpers': "Whether a message passes on your words or is Claude's own follow-up. A forked or resumed session repeats its parent's records, so some are counted twice.",
    'cant.messages_to_other_sessions': "Who set up the other session, or what the message said. A forked or resumed session repeats its parent's records, so some are counted twice.",
    'cant.messages_from_other_sessions': 'Who asked the other session to write, or whether the message was acted on.',
    'cant.teammates': 'Who created each team, you or Claude.',
    'cant.helpers_started_by_helpers': "Whether the sub-agent's own instructions told it to start more, or it chose to.",
    'cant.helpers_own_worktree': "Whether your agent file asked for the copy or Claude's call did.",
    'cant.helpers_stopped_by_claude': "Whether you asked for the stop or Claude decided, or which kind of task it was. A forked or resumed session repeats its parent's records, so some are counted twice.",
    'cant.helper_calls_with_model': "Who picked the model: a rule of yours, your agent file, or Claude. A forked or resumed session repeats its parent's records, so some are counted twice.",
    'cant.plan_mode_entered_by_claude': 'Whether a rule of yours told Claude to plan first.',
    'cant.chats_opened_in_plan_mode': 'Whether it was your default setting, a launch flag, or a resumed session.',
    'claude.jobs_watched': 'Anything about you: Claude Code tells Claude when to do this.',
    'claude.background_shell': "Anything about you: Claude decides when a command runs in the background. A forked or resumed session repeats its parent's records, so some are counted twice.",
    'claude.background_helpers': "Whether you asked for it: Claude sets the flag. A forked or resumed session repeats its parent's records, so some are counted twice.",
    'claude.auto_compactions': 'Anything about you: Claude Code starts these when the session is full.',
}


def who_did_it(inv, chats, raw, meta):
    """Raw counts grouped by who made each thing happen: you (only a person writes the field), your setup (a mode,
    hook, agent or rule of yours fired), your programs, probably you (inferred from what you typed), can't tell, and
    Claude by itself. This block only counts; the rungs come from labs/v15/substrate/signals.yaml (signals.py), keyed on
    each item's id. Every item: id, label, n, chats, of, proof, cant_tell."""
    def it(id_, label, n, chats=None, proof="", of=None):
        # id_ is the stable key other code keys on (labs/v15/substrate/signals.yaml): rename the label freely, never the id
        return {"id": id_, "label": label, "n": int(n or 0), "chats": chats, "of": of, "proof": proof,
                "cant_tell": WHO_DID_IT_CANT_TELL.get(id_, "")}

    days = inv["meta"]["days"]
    cmd = {x["command"]: x for x in (inv.get("slash_commands") or {}).get("by_command", [])}

    def c(*names):
        return sum((cmd.get(x) or {}).get("times", 0) for x in names), sum((cmd.get(x) or {}).get("chats", 0) for x in names)

    def bare(name):
        return (cmd.get(name) or {}).get("bare", 0)

    hist = "your command history (~/.claude/history.jsonl); only a person types a command"
    COPY_NOTE = " A record copied into a forked or resumed session counts once"
    msgs = [m for ch in chats for m in ch.get("msgs", [])]
    conc = typed_concurrency(chats)
    comp = inv["compactions"]
    att = raw["attachments"]
    m = lambda k: meta.get(k) or {"n": 0, "chats": 0}  # noqa: E731
    tools = {x["what"]: x for x in inv["tools"]["typed_chats"]["by_tool"]}
    tools_h = {x["what"]: x for x in inv["tools"]["in_helpers"]["by_tool"]}
    monitors = (tools.get("Monitor") or {}).get("calls", 0) + (tools_h.get("Monitor") or {}).get("calls", 0)
    jw = inv.get("jobs_watched") or {"typed_chats": {"background_came_back": 0, "poll_loops": 0},
                                     "in_helpers": {"background_came_back": 0, "poll_loops": 0}}

    # every sub-agent call Claude made in an interactive session, put in the first bucket that explains it: asked in
    # words, then text of the person's own (a hook or rule file git credits to them that talks about sub-agents) already
    # in Claude's context, then one of their own agents
    own_talk = raw.get("own_talk_first_t") or {}
    mine_types = {x["type"] for x in inv["helpers"]["by_type"] if (x.get("author") or {}).get("yours")}
    split, split_chats = Counter(), defaultdict(set)
    for ch in chats:
        ms = ch.get("msgs", [])
        for k in ch.get("calls", []):
            if k.get("name") not in ("Agent", "Task"):
                continue
            typ = (k.get("key") or "").split(" | ")[0]
            prev = ms[k["at"] - 1]["head"] if 0 < k.get("at", 0) <= len(ms) else ""
            b = ("asked" if ASK_HELPERS_RX.search(prev)
                 else "own_ctx" if ch["chat"] in own_talk and own_talk[ch["chat"]] <= k["t"]
                 else "own_type" if typ in mine_types else "other_type" if typ not in BUILTIN_HELPERS else "none")
            split[b] += 1
            split_chats[b].add(ch["chat"])
    calls = sum(split.values())
    sc = lambda b: len(split_chats[b])  # noqa: E731
    wt = [ch["chat"] for ch in chats for x in ch.get("msgs", []) if re.search(r"(?i)\bworktree", x.get("head") or "")]
    sessions_as_agent = raw["agent_setting_chats"]
    pm = Counter(x.get("pm") for x in msgs)
    by = comp.get("by_trigger", {})
    blocks = inv["hook_context"]["blocking_errors"]
    pw = inv.get("past_work_reach") or {}
    ev_own = [e for e in inv.get("evals") or [] if e.get("yours") and e.get("changed_in_window")]
    cron = [x for x in (inv.get("launchers") or {}).get("crontab", []) if x.get("starts_agents")]
    sched = sum(x.get("count", 0) for x in (inv.get("launchers") or {}).get("scheduled_tasks", []))
    launchd = (inv.get("launchers") or {}).get("launchd_matching") or []
    bl = raw.get("blocks") or {"own": 0, "others": 0, "unknown": 0}
    nown = raw.get("nested_own") or {}
    own_launch = sum(x["calls"] for x in inv["helpers"]["by_type"] if x["type"] in mine_types)
    # own.command: a typed /name whose command or skill file git credits to the person (never matched by its name alone)
    own_names = {x.get("name") for x in (inv.get("own_artifacts") or {}).get("own", [])
                 if x.get("kind") in ("command", "skill") and ((x.get("git") or {}).get("user_commits") or 0) > 0}
    own_cmd_hits = Counter()
    own_cmd_chats = 0
    for x in (inv.get("slash_commands") or {}).get("by_command", []):
        full = str(x.get("command") or "").lstrip("/")
        nm = full.split(":")[-1]
        if nm in own_names and full not in BUILTIN_COMMANDS:
            own_cmd_hits[x["command"].lstrip("/")] += x.get("times", 0)
            own_cmd_chats += x.get("chats", 0)
    own_cmds = sum(own_cmd_hits.values())
    own_cmd_top = own_cmd_hits.most_common(5)

    # Who put the chat into plan mode. The mode is stored on each typed message; a run of plan-mode messages started
    # either by Claude's EnterPlanMode call, by /plan, by the chat opening in plan mode (a default setting, a launch flag
    # or a resumed chat), or else by the person switching modes (Shift+Tab: the switch itself leaves no record).
    # A chat that opens in plan mode with none of those is "opened"; when the settings that win for its folder set
    # defaultMode "plan" and the file is the person's (ownership.py), their setup opened it: "settings".
    plan = {k: {"times": 0, "chats": set(), "msgs": 0} for k in ("you", "command", "claude", "opened", "settings")}
    default_mode = raw.get("default_mode_by_cwd") or {}
    for ch in chats:
        prev, who = None, None
        for x in ch.get("msgs", []):
            if x.get("pm") == "plan":
                if prev != "plan":
                    i = x.get("i", 0)
                    who = ("claude" if any(k.get("name") == "EnterPlanMode" and k.get("at") == i for k in ch.get("calls", []))
                           else "command" if any(k.get("name") == "/plan" and k.get("at") == i for k in ch.get("commands", []))
                           else "you" if i != 0
                           else "settings" if (default_mode.get(ch.get("cwd")) or {}).get("mode") == "plan"
                           and default_mode[ch.get("cwd")].get("yours") else "opened")
                    plan[who]["times"] += 1
                    plan[who]["chats"].add(ch["chat"])
                plan[who]["msgs"] += 1
            prev = x.get("pm")
    # counted from the chats: by_tool keeps only the 40 most used tools, and a rare one falls off it
    ep = [ch["chat"] for ch in chats for k in ch.get("calls", []) if k.get("name") == "EnterPlanMode"]
    enter_plan = {"calls": len(ep), "chats": len(set(ep))}

    you = [
        it("you.typed_messages", "Messages you typed", inv["chats"]["typed_messages"], inv["chats"]["typed_chats"],
           "Claude Code marks each message a person typed; messages from other sessions, hooks and programs are left out."),
        it("you.btw_side_questions", "Side questions with /btw, asked without derailing the task", c("/btw")[0] - bare("/btw"), c("/btw")[1],
           hist + f". Only /btw with a question after it; {bare('/btw')} more had nothing after it."),
        it("you.subtask_forks", "Sub-agents you forked with /subtask or /btw", m("forks_without_a_claude_call")["n"], m("forks_without_a_claude_call")["chats"],
           f"The sub-agent's own record: a fork that no sub-agent call from Claude started. Claude Code starts these when you type "
           f"/subtask (you typed it {c('/subtask')[0]} times) and sometimes for a /btw (at least {m('btw_forks')['n']} of these: their "
           "description starts with <btw>)."),
        it("you.goals_set", "Goals you set with /goal", c("/goal")[0] - bare("/goal"), c("/goal")[1],
           hist + f". Only /goal with a condition after it; {bare('/goal')} more had nothing after it (a status check)."),
        it("you.plans_asked", "Plans you asked for with /plan", *c("/plan"), proof=hist + "."),
        it("you.clears_typed", "Times you started over with /clear", *c("/clear"), of=len(chats), proof=
           f"{c('/clear')[0]} times in {c('/clear')[1]} of your {len(chats)} typed sessions, from "
           f"{(cmd.get('/clear') or {}).get('source', 'session logs')}: a /clear is written in the new session it starts, so the "
           "session logs count each once; ~/.claude/history.jsonl keys it to the session you left and is read only for a "
           "command the logs never record."),
        it("you.chats_split_off", "Sessions you split off with /fork or /branch", *c("/fork", "/branch"), proof=hist + "."),
        it("you.sessions_backgrounded", "Sessions you typed in that ran in the background (/bg)", raw.get("bg_sessions_typed", 0), None,
           f"Records written while a session runs in the background carry sessionKind \"bg\". Counted among the {len(chats)} sessions "
           f"you typed in; you typed /bg {c('/bg', '/background')[0]} times, and the other background sessions got there another "
           f"way the log doesn't name. Not counted: {raw.get('bg_sessions_no_message_of_yours', 0)} background sessions with no message "
           f"of yours and {raw.get('bg_sessions_headless', 0)} headless runs, which are other units.", of=len(chats)),
        it("you.own_commands_typed", "Times you typed a command or skill of your own", own_cmds, own_cmd_chats, proof=hist +
           ". Counted when its name matches a command or skill file in a repo where git shows your commits: "
           + (", ".join(f"/{k} {v}" for k, v in own_cmd_top) or "none") + "."),
        it("you.helper_mentions", "Sub-agents you called by name (@-mention)", att["agent_mention"]["n"], att["agent_mention"]["chats"],
           "Claude Code records an @-mention of a sub-agent as its own entry (agent_mention); in your message it reads "
           f"@\"name (agent)\".{COPY_NOTE}" + f" ({att['agent_mention']['copies']} copies left out)."),
        it("you.skill_mentions", "Skills you named with a slash (/skill-name)", att["skill_mention"]["n"], att["skill_mention"]["chats"],
           "Claude Code records a /skill-name inside your message as its own entry (skill_mention). Sessions you typed in, "
           f"last {days} days.{COPY_NOTE}" + f" ({att['skill_mention']['copies']} copies left out); headless runs are left out."),
        it("you.file_mentions", "Files you @-mentioned", att["file"]["n"], att["file"]["chats"],
           "A file attachment whose path follows an @ in the message you typed just before. Claude Code also re-attaches "
           f"files it had read after a compaction; those {att['file']['reattached']} are left out.{COPY_NOTE}" +
           f" ({att['file']['copies']} copies left out)."),
        it("you.ultracode_requests", "Workflows you asked for by typing \"ultracode\"", att["workflow_keyword_request"]["n"], att["workflow_keyword_request"]["chats"],
           f"Claude Code records the keyword as its own entry (workflow_keyword_request).{COPY_NOTE}" +
           f" ({att['workflow_keyword_request']['copies']} copies left out)."),
        it("you.esc_interrupts", "Times you interrupted Claude (Esc)", sum(x.get("int", 0) for x in msgs), sum(1 for ch in chats if any(x.get("int") for x in ch.get("msgs", []))),
           "\"[Request interrupted by user]\" in the session after you pressed Esc."),
        it("you.steps_refused", "Tool calls you rejected", sum(x.get("rej", 0) for x in msgs), sum(1 for ch in chats if any(x.get("rej") for x in ch.get("msgs", []))),
           "\"The user doesn't want to proceed\" in reply to a tool call Claude asked to make (toolDenialKind: user-rejected). "
           "Includes plans you declined: leaving plan mode is a tool call too."),
        it("you.helpers_stopped_by_hand", "Sub-agents you stopped by hand", m("stopped_by_the_person")["n"], m("stopped_by_the_person")["chats"],
           "The sub-agent's own record says it was stopped by the user."),
        it("you.manual_compactions", "Times you compacted by hand (/compact)", by.get("manual", 0), None,
           f"The compaction record says it was started by hand; {comp.get('with_focus_text', 0)} of your /compact commands "
           "said what to keep."),
        it("you.plan_mode_switched_on", "Times you switched plan mode on yourself", plan["you"]["times"], len(plan["you"]["chats"]),
           f"Plan mode started between two of your messages with no call from Claude to enter it and no /plan, which leaves "
           f"your own mode switch (Shift+Tab). {plan['you']['msgs']} of the {pm.get('plan', 0)} messages you typed in plan mode "
           "came after one of these."),
        it("you.chats_at_once", "Messages typed while 3 or more of your sessions were active", conc["messages_with_3_plus_chats"], None, of=conc["messages"], proof=
           "At least 3 different sessions got a message from you within 5 minutes either side. Sessions left open but idle "
           "don't count."),
        it("you.most_chats_in_10_min", "Most sessions you typed into within 10 minutes", conc["most_chats_in_10_min"], None,
           "Counted from your typed messages only."),
    ]
    setup = [
        it("setup.chats_opened_in_plan_by_settings", "Sessions that opened in plan mode because your own settings make it the default",
           plan["settings"]["times"], plan["settings"]["times"], of=len(chats), proof=
           f"The session's first message was in plan mode, and the settings file that wins for its folder sets defaultMode \"plan\": "
           f"{', '.join(sorted({v['file'] for v in default_mode.values() if v and v.get('mode') == 'plan' and v.get('yours')})) or 'none found'}, "
           f"a file ownership.py says is yours. {plan['settings']['msgs']} messages in plan mode followed. Read from your settings as "
           "they are now: the files keep no history. A teammate's committed project settings or an organisation's managed settings "
           "that set the mode are not yours, and those sessions stay under Can't tell."),
        it("setup.subagents_after_own_context", "Sub-agents Claude started after a hook or rule file of yours about sub-agents was in its context",
           split["own_ctx"], sc("own_ctx"),
           "Text that talks about sub-agents reached Claude's context before the call, from a hook whose script git credits to "
           f"you or a CLAUDE.md / AGENTS.md git credits to you. {len(own_talk)} sessions had such text. Sub-agents you also asked "
           "for in words are under Probably you instead."),
        it("setup.own_agent_launches", "Launches of sub-agents you wrote", own_launch, None,
           "The sub-agent type's agent file is in a repo where git shows your commits: " + (", ".join(sorted(mine_types)) or "none found") + "."),
        it("setup.hook_blocks", "Tool calls your hooks blocked", bl["own"], None,
           f"A hook returned a blocking error, and git credits its script to you. Hooks of others blocked {bl['others']} more; "
           f"{bl['unknown']} git can't place: the record names only the event and tool (PostToolUse:Write, say), and the "
           "hooks set up there are a mix, or sit outside any repo."),
        it("setup.own_hook_context_at_start", "New sessions where a start-up hook of yours put text in Claude's context",
           pw.get("notes_put_in_by_a_hook", 0), None, of=pw.get("new_chats"), proof=
           "A SessionStart hook whose script git credits to you added text to Claude's context (its run record holds the text)."),
        it("setup.sessions_as_named_agent", "Sessions started as a named agent (claude --agent …), by you or a launcher of yours", sessions_as_agent, None, proof=
           "The session's agent-setting record" + (": " + ", ".join(f"{k} {v}" for k, v in raw["agent_settings"].items())
                                               if raw["agent_settings"] else "") + "."),
        it("setup.subfolder_rules_reached", "Sessions where a rule file of yours from a subfolder loaded into Claude's context",
           nown.get("chats", 0), None,
           f"{nown.get('files', 0)} of the {nown.get('files_all', 0)} subfolder rule files that loaded mid-session are files git "
           f"credits to you. Git can't say for {nown.get('files_gone', 0)} that are gone (a removed worktree, say) and "
           f"{nown.get('files_unknown', 0)} outside any repo."),
        it("setup.hook_text_chats", "Sessions where hook text was in Claude's context (yours or installed)", inv["hook_context"]["typed_chats_with_hook_text"], None,
           "Which of these hooks are yours is decided by git.", of=inv["hook_context"]["typed_chats"]),
    ]
    programs = [
        it("programs.runs_started_by_code", "Headless runs (claude -p, Agent SDK)", inv["headless"]["runs"], None,
           "Sessions whose entry point is the Agent SDK or claude -p: a program started them, nobody typed."),
        it("programs.runs_unattended", "…of them started while you were away", inv["headless"].get("while_away", inv["headless"]["started_with_no_typed_message_in_30_min_before"]), None,
           "No message from you in any session in the half hour before the run started, or a scheduler of yours (a LaunchAgent or "
           f"cron line) started it: {inv['headless'].get('started_by_a_scheduled_job', 0)} runs trace to one.", of=inv["headless"]["runs"]),
        it("programs.scheduled_jobs", "Scheduled jobs that start an agent", len(cron) + len(launchd) + sched, None,
           f"crontab entries {len(cron)} and launchd jobs {len(launchd)} whose command or script mentions claude, and Claude Code "
           f"scheduled tasks {sched}. Not counted here: {raw.get('sched_fires', 0)} turns Claude Code resumed on a wakeup "
           "(scheduled_task_fire), which a /loop you started also writes, so they aren't proof of a scheduled job."),
        it("programs.own_eval_folders_changed", "Eval folders of yours that changed in the last " + str(days) + " days", len(ev_own), None,
           "An evals/ folder in a repo where git shows your commits on it, with a file changed in the window "
           "(cases added, results written)."),
    ]
    probably = [
        it("probably.helpers_after_ask", "Sub-agents Claude started right after you asked for agents in words", split["asked"], sc("asked"),
           "Your message right before mentions agents, a fork, parallel work, a team or a workflow (a word match on its first "
           "160 characters, so some asks are missed and a few are wrong)."),
        it("probably.worktree_mentions", "Messages where you mentioned a worktree", len(wt), len(set(wt)),
           "A word match on your typed message; it doesn't show the worktree that followed."),
        it("probably.corrections", "Messages that read as a correction", sum(1 for x in msgs if x.get("corr")), None,
           "A short English word list (\"no,\", \"that's wrong\"…) at the start of your message; it catches only some."),
    ]
    cant = [
        it("cant.early_reads_of_earlier_work", "New sessions where Claude read a file an earlier session wrote, before your 3rd message",
           pw.get("looked_it_up_early", 0), None, of=pw.get("new_chats"), proof=
           f"A Read of a file that another, earlier session wrote or edited. {pw.get('either', 0)} new sessions had this, "
           "a start-up hook of yours, or both."),
        it("cant.helper_calls_unattributed", "Sub-agent calls with nothing of yours found behind them", split["none"] + split["other_type"], None,
           "Of the sub-agent calls in your interactive sessions: not right after an ask, not after a hook or rule file of yours "
           "about sub-agents, not one of your own agents. Claude's own choice, or something of yours the log doesn't name.", of=calls),
        it("cant.messages_to_own_helpers", "Messages Claude sent to sub-agents it started in the same session", raw["send_to_own_helper"], None,
           "SendMessage to a name Claude gave a sub-agent in that session; your words relayed and Claude's own follow-ups look the same."),
        it("cant.messages_to_other_sessions", "Messages Claude sent to other sessions or teammates", raw["send_to_other"], None,
           "SendMessage to any other name."),
        it("cant.messages_from_other_sessions", "Messages other sessions and teammates sent into your sessions", raw["session_msgs_in"], raw["session_msgs_in_chats"],
           "\"Another Claude session sent a message\" and teammate messages; idle and shutdown notices left out."),
        it("cant.teammates", "Teammates in agent teams", m("teammates")["n"], m("teammates")["chats"],
           f"{meta.get('teams', 0)} teams. The sub-agent's record names its team; teams are a feature you switch on, but who "
           "created each team is not recorded."),
        it("cant.helpers_started_by_helpers", "Sub-agents started by another sub-agent", m("started_by_a_helper")["n"], m("started_by_a_helper")["chats"],
           "The sub-agent's record says it sits two or more layers deep."),
        it("cant.helpers_own_worktree", "Sub-agents given their own worktree", m("own_worktree")["n"], m("own_worktree")["chats"],
           "The sub-agent's record says it started in a worktree; your agent file or Claude's call can ask for that, and the "
           "two are not split yet."),
        it("cant.helpers_stopped_by_claude", "Background tasks Claude stopped (TaskStop)", (tools.get("TaskStop") or {}).get("calls", 0), (tools.get("TaskStop") or {}).get("chats"),
           "TaskStop calls. TaskStop stops any background task: a shell command, a sub-agent or a workflow, and the count "
           "mixes all three. You asking for the stop and Claude deciding look the same."),
        it("cant.helper_calls_with_model", "Sub-agent calls that named a model", inv["helpers"]["calls_that_set_a_model"], None,
           "The model on the sub-agent call; it may come from a rule of yours or Claude's own pick."),
        it("cant.plan_mode_entered_by_claude", "Times Claude asked to switch plan mode on", enter_plan.get("calls", 0), enter_plan.get("chats"),
           f"EnterPlanMode calls; {plan['claude']['times']} led to plan-mode messages from you. A rule of yours may have told "
           "Claude to plan first, or it chose to."),
        it("cant.chats_opened_in_plan_mode", "Sessions already in plan mode at your first message", plan["opened"]["times"], None,
           f"{plan['opened']['msgs']} messages. A launch flag of yours, a default set by someone else's settings, or a resumed "
           f"session that was already in plan mode; the log doesn't say which. Sessions your own defaultMode \"plan\" opened "
           f"({plan['settings']['times']}) are counted under Your setup instead."),
    ]
    claude = [
        it("claude.jobs_watched", "Times Claude watched a job until it ended",
           monitors + jw["typed_chats"]["background_came_back"] + jw["typed_chats"]["poll_loops"]
           + jw["in_helpers"]["background_came_back"] + jw["in_helpers"]["poll_loops"], None,
           f"Three records, each once: Monitor calls ({monitors}); background commands that Claude Code reported finished and that "
           f"Claude came back to with a later call naming them ({jw['typed_chats']['background_came_back'] + jw['in_helpers']['background_came_back']}); "
           f"shell loops that sleep between checks and returned ({jw['typed_chats']['poll_loops'] + jw['in_helpers']['poll_loops']}). "
           "A background command Claude never came back to, or that was stopped, is not counted. Claude Code tells Claude when to use these."),
        it("claude.background_shell", "Shell commands Claude left running in the background", inv["chats_logged"]["background_shell_runs"], None,
           "Bash calls with run_in_background; Claude decides this."),
        it("claude.background_helpers", "Sub-agent calls Claude ran in the background", inv["helpers"]["background_calls"], None,
           "Sub-agent calls with run_in_background set; Claude sets it, and Claude Code's own instructions suggest it for "
           "long work.", of=inv["helpers"]["calls"]),
        it("claude.automemory_at_start", "New sessions that began with the project's auto-memory in Claude's context",
           pw.get("loaded_automatically", 0), None, of=pw.get("new_chats"), proof=
           "Claude Code loads the project's auto-memory MEMORY.md (~/.claude/projects/<project>/memory/MEMORY.md) into every session "
           "started there, once it has text; the session's `instructions` record lists it as AutoMem. Earlier sessions or you wrote it."),
        it("claude.auto_compactions", "Automatic compactions", by.get("auto", 0), None, "The compaction record says Claude Code started it."),
    ]
    return {"days": days,
            "note": "Raw counts from your logs, grouped by who made each thing happen.",
            "groups": [
                {"id": "you", "title": "You", "means": "Only a person can produce these: Claude Code writes the field when you act.", "items": you},
                {"id": "setup", "title": "Your setup", "means": "A mode, hook, agent or rule file of yours made it happen without you asking.", "items": setup},
                {"id": "programs", "title": "Your headless runs", "means": "Code of yours started the agent.", "items": programs},
                {"id": "probably", "title": "Probably you", "means": "Inferred from what you typed right before. Not proof.", "items": probably},
                {"id": "cant", "title": "Can't tell", "means": "The same log line can come from you, your setup or Claude.", "items": cant},
                {"id": "claude", "title": "Claude by itself", "means": "Claude Code's defaults or Claude's own judgement.", "items": claude}]}


# ---------------------------------------------------------------- history.jsonl


def slash_history(days, transcript=None, typed_sessions=None):
    """Slash commands typed, per command, from ONE source per command name (11-06: /clear read 67 in one place, 134 in
    another, 122 in 122 sessions on the page, because two sources were added together and a /clear is written under a
    different session id in each).
      session logs   the transcripts record every command from any app (terminal, VS Code, JetBrains, desktop), in the session
                     the command ran in; a /clear or /branch lands in the NEW session. Counted in your typed sessions.
      history.jsonl  written by the terminal only, and keyed by the session you were in before a /clear. Used only for a name
                     the logs never record in the window (/resume and /exit leave no transcript record).
    transcript: {session id: (Counter(command), project, Counter(bare))} from the typed sessions. Each row says its source."""
    start = (clock.now() - days * 86400) * 1000
    per = defaultdict(Counter)            # (session) -> command -> times
    per_bare, proj_of = defaultdict(Counter), {}
    total, read = 0, True
    from_history = defaultdict(Counter)
    h_bare, h_proj = defaultdict(Counter), {}
    try:
        for line in open(os.path.join(HOME, ".claude", "history.jsonl")):
            try:
                h = json.loads(line)
            except ValueError:
                continue
            if (h.get("timestamp") or 0) < start:
                continue
            total += 1
            d = (h.get("display") or "").strip()
            if d.startswith("/") and len(d) > 1:
                nm = d.split()[0][:40]
                sid = h.get("sessionId") or "?"
                from_history[sid][nm] += 1
                h_bare[sid][nm] += d == nm
                h_proj[sid] = os.path.basename(h.get("project") or "?")
    except OSError:
        read = False
    in_logs = set()
    for sid, (cnt, project, bare_c) in (transcript or {}).items():
        for nm, n in cnt.items():
            in_logs.add(nm)
            per[sid][nm] += n
            per_bare[sid][nm] += bare_c.get(nm, 0)
        proj_of.setdefault(sid, project)
    source = {nm: "session logs" for nm in in_logs}
    for sid, cnt in from_history.items():
        for nm, n in cnt.items():
            if nm in in_logs:
                continue
            per[sid][nm] += n
            per_bare[sid][nm] += h_bare[sid][nm]
            proj_of.setdefault(sid, h_proj.get(sid, "?"))
            source[nm] = "history.jsonl"
    if not read and not transcript:
        return {"read": False}
    c, chats, proj, bare = Counter(), defaultdict(set), defaultdict(Counter), Counter()
    for sid, cnt in per.items():
        for nm, n in cnt.items():
            if n:
                c[nm] += n
                bare[nm] += per_bare[sid][nm]
                chats[nm].add(sid)
                proj[nm][proj_of.get(sid, "?")] += n
    return {"read": True, "entries_in_window": total, "commands": sum(c.values()), "typed_sessions": typed_sessions,
            "by_command": [{"command": k, "times": v, "bare": bare[k], "chats": len(chats[k]), "source": source.get(k, "session logs"),
                            "projects": dict(proj[k].most_common(3))}
                           for k, v in c.most_common(60)]}


# ---------------------------------------------------------------- authorship


def me_from_repos(roots):
    authors = Counter()
    for r in roots:
        authors.update(a for a in git(["log", "-300", "--format=%an <%ae>"], r).split("\n") if a)
    return authors


AUTHOR_SHARE = 0.10   # a repo is one the person writes in when their commits are at least this share of the window's


def first_commit_is_mine(repo, me):
    """Whether the repo's very first commit (any root commit) is the person's: they started it, so it is theirs to write in
    even when others wrote most of the window's commits since."""
    roots = git(["rev-list", "--max-parents=0", "HEAD"], repo).split()
    return any(is_mine(git(["log", "-1", "--format=%an <%ae>", c], repo).strip(), me) for c in roots[:5])


def authors_in(theirs, total, first_commit_theirs):
    """The rule for the share of commits "under your name": over the repos where the person writes a meaningful part, that
    is their commits are at least AUTHOR_SHARE of the repo's commits in the window, or they made the repo's first commit.
    Every other repo is one they have open (they read it, or send an occasional fix) and stays out of the base."""
    return theirs > 0 and (theirs >= AUTHOR_SHARE * total or bool(first_commit_theirs))


def git_identity(path):
    """The one name for a repo however it was reached (a symlink, a second checkout path, a linked worktree): the real
    path of its common git dir. None when git cannot say (not a repo)."""
    if not os.path.isdir(path):
        return None
    out = git(["rev-parse", "--git-common-dir"], path).strip()
    if not out:
        return None
    return os.path.realpath(out if os.path.isabs(out) else os.path.join(path, out))


def one_row_per_repo(repos):
    """Merge rows that are the same git repository reached by two paths (a symlinked folder, say), summing the sessions
    that ran in each; the path with more sessions stays as the name. The commits are read from one history, so they are
    not added."""
    by, order = {}, []
    for r in repos:
        key = git_identity(r["repo"]) if r["repo"].startswith("/") else None
        key = key or r["repo"]
        if key not in by:
            by[key] = r
            order.append(key)
            continue
        keep, other = by[key], r
        if other["typed_chats"] + other["headless_runs"] + other.get("worked_chats", 0) > keep["typed_chats"] + keep["headless_runs"] + keep.get("worked_chats", 0):
            keep, other = other, keep
            by[key] = keep
        keep["typed_chats"] += other["typed_chats"]
        keep["headless_runs"] += other["headless_runs"]
        keep["worked_chats"] = keep.get("worked_chats", 0) + other.get("worked_chats", 0)
        keep["also_reached_as"] = [*keep.get("also_reached_as", []), other["repo"], *other.get("also_reached_as", [])]
        for k in ("found_under", "found_from_work"):
            if k in other and k not in keep:
                keep[k] = other[k]
    return [by[k] for k in order]


def is_mine(author_line, me):
    """One commit's "name <email>" is the person's: its email is one of theirs (identity.py: every email their git config
    knows), or their GitHub no-reply address, or the full name equals one of theirs (longer than 3 letters).
    Exact matches only: "al" must not match "alex"."""
    name, _, email = author_line.strip().rpartition(" <")
    email, name = email.rstrip(">").strip().lower(), name.strip().lower()
    if email in me["emails"]:
        return True
    if email.endswith("@users.noreply.github.com"):
        user = email.split("@")[0].split("+")[-1]
        if user and user in me["names"]:
            return True
    return len(name) > 3 and name in me["names"]


def _git_log_authors(path, cwd=None):
    if not path or not os.path.exists(path):
        return []
    cwd = cwd or (path if os.path.isdir(path) else os.path.dirname(path))
    return [x for x in git(["log", "--format=%an <%ae>", "--", path], cwd).split("\n") if x]


def authorship(path, me, cwd=None):
    """Commits on a path: total, the person's, other authors (names only), and whose it is by ownership.py.
    yours = ownership.owner() says "theirs": no install receipt (marketplace, skills lock) and git doesn't show it is
    mostly someone else's. A file in no repo is theirs (the ruling 11-05); until then it was nobody's and a person's
    own skills, hooks and rule files went uncounted."""
    log = _git_log_authors(path, cwd)
    mine = sum(1 for a in log if is_mine(a, me))
    others = Counter(a.split(" <")[0] for a in log if not is_mine(a, me))

    def auth(p):
        lg = log if p == path else _git_log_authors(p)
        return {"commits": len(lg), "theirs": mine if p == path else sum(1 for a in lg if is_mine(a, me))} if lg else None

    def blob_authorship(repo, sha):
        lg = [x for x in git(["log", "--all", f"--find-object={sha}", "--format=%an <%ae>", "-n", "50"], repo, timeout=60).split("\n") if x]
        return {"commits": len(lg), "theirs": sum(1 for x in lg if is_mine(x, me))} if lg else None

    mod, rc = receipts()
    o = mod.owner(path, rc, auth, written_in_logs=written_in_logs, blob_authorship=blob_authorship)
    return {"commits": len(log), "theirs": mine, "yours": o["owner"] == "theirs", "owner": o["owner"],
            "owner_how": o["how"], "others": dict(others.most_common(3))}


MANAGED_SETTINGS = ("/Library/Application Support/ClaudeCode/managed-settings.json", "/etc/claude-code/managed-settings.json")


def effective_default_mode(cwd, me, memo=None):
    """The permission mode a session started in cwd opens in by settings, and whose file says so: the settings file that
    wins by Claude Code's precedence (managed, then the project's settings.local.json, then its settings.json, then
    ~/.claude/settings.json) and sets permissions.defaultMode. The project is the start folder and its parents up to the
    first one that holds a .git (a worktree has its own). yours = ownership.py says the file is theirs, so a default in a
    teammate's committed project settings, or in an organisation's managed settings, is not the person's setup. Settings
    as they are now: the files carry no history. None when no file sets a mode."""
    key = cwd or ""
    if memo is not None and key in memo:
        return memo[key]
    cands = [(p, True) for p in MANAGED_SETTINGS]
    q = cwd or ""
    while q and q not in ("/", HOME):
        cands += [(os.path.join(q, ".claude", "settings.local.json"), False), (os.path.join(q, ".claude", "settings.json"), False)]
        if os.path.exists(os.path.join(q, ".git")):
            break
        q = parent_dir(q)
    cands.append((os.path.join(HOME, ".claude", "settings.json"), False))
    out = None
    for path, managed in cands:
        try:
            mode = ((json.load(open(path)).get("permissions") or {}).get("defaultMode"))
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(mode, str) and mode:
            out = {"mode": mode, "file": t_(path), "yours": False if managed else bool(authorship(path, me)["yours"])}
            break
    if memo is not None:
        memo[key] = out
    return out


def resolve_program(name, me, roots):
    """Where a shell program lives, and whether the person wrote it (its source sits in a repo with their commits)."""
    base = name.split()[-1] if " " in name else name
    path = None
    for d in os.environ.get("PATH", "").split(":") + [os.path.join(HOME, ".local", "bin"), os.path.join(HOME, "bin")]:
        p = os.path.join(d, base)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            path = p
            break
    if not path:
        return None
    real = os.path.realpath(path)
    if real.startswith(("/usr/", "/bin/", "/sbin/", "/opt/homebrew/Cellar", "/System", "/Library", "/Applications")):
        return {"path": path, "real": real, "system": True}
    rr = repo_root(os.path.dirname(real))
    out = {"path": path, "real": real}
    if rr:
        out["repo"] = rr
        out.update(authorship(real, me, rr))
    else:
        # an installed copy outside git: look for plugins/<x>/ in the person's repos that holds a file with this name
        for r in roots:
            hits = glob.glob(os.path.join(r, "plugins", "*", "bin", base)) + glob.glob(os.path.join(r, "plugins", "*", base))
            if hits:
                out["source"] = hits[0]
                out.update(authorship(hits[0], me, r))
                break
    return out


# ---------------------------------------------------------------- files: evals, instruction history, stores, git hooks

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".venv", "venv", "__pycache__", ".cache", "playground", "repos",
             ".next", "target", "vendor"}


def walk_dirs(root, maxdepth=6):
    root_depth = root.rstrip("/").count("/")
    for d, subdirs, files in os.walk(root):
        if d.count("/") - root_depth >= maxdepth:
            subdirs[:] = []
        # a linked worktree (its .git is a file) or a worktrees folder holds copies of a repo: skip it
        subdirs[:] = [s for s in subdirs if s not in SKIP_DIRS and s not in ("worktrees", "w")
                      and not (s.startswith(".") and s not in (".claude", ".agents", ".githooks"))
                      and not os.path.isfile(os.path.join(d, s, ".git"))]
        yield d, subdirs, files


def find_evals(roots, me, start):
    out = []
    seen = set()
    for root in roots:
        for d, subdirs, files in walk_dirs(root, 6):
            b = os.path.basename(d)
            if b not in ("evals", "eval") or d in seen:
                continue
            seen.add(d)
            subdirs[:] = []
            cases = runs = in_window = 0
            last = None
            ledgers = []
            newest = 0
            nfiles = 0
            for dd, _, ff in os.walk(d):
                if any(x in dd for x in ("/node_modules", "/.git/")):
                    continue
                for f in ff:
                    p = os.path.join(dd, f)
                    nfiles += 1
                    try:
                        newest = max(newest, os.path.getmtime(p))
                    except OSError:
                        pass
                    if f.startswith("case.") or f in ("prompt.md", "task.md") and not any(x.startswith("case.") for x in ff):
                        cases += 1
                        continue
                    if f.endswith((".jsonl",)) and ("run" in f or "ledger" in f or "results" in f):
                        try:
                            lines = open(p, errors="replace").read().splitlines()
                        except OSError:
                            continue
                        ledgers.append(os.path.relpath(p, d))
                        for ln in lines:
                            runs += 1
                            m = re.search(r'"(?:ts|timestamp|date|started|at|time)"\s*:\s*"?(\d{4}-\d\d-\d\d|\d{10})', ln)
                            if m:
                                v = m.group(1)
                                tt = float(v) if v.isdigit() else datetime.fromisoformat(v + "T00:00:00+00:00").timestamp()
                                last = max(last or 0, tt)
                                in_window += tt >= start
                    elif f.endswith((".json", ".yaml", ".yml", ".md", ".txt", ".jsonl")) and (
                            "/cases" in dd or f.startswith("case")):
                        cases += 1
            if nfiles < 2:
                continue
            a = authorship(d, me, root)
            runner_lines, results = eval_runner_and_results(d)
            out.append({"path": d, "repo": root, "files": sum(len(ff) for _, _, ff in os.walk(d)), "case_files": cases,
                        "runner_keeps_agent_out": runner_lines, "results_in_docs": results,
                        "ledgers": ledgers[:6], "ledger_lines": runs, "ledger_lines_in_window": in_window,
                        "last_run": day(last) if last else None, "newest_file": day(newest) if newest else None,
                        "changed_in_window": newest >= start, **a})
    return sorted(out, key=lambda x: (-x["ledger_lines_in_window"], -x["ledger_lines"]))


def eval_runner_and_results(d):
    """Lines in an eval's runner that keep the agent under test away from the cases (sandbox HOME, deny rules, tool
    lists), and result lines kept in its README or report (some evals keep no run ledger). Fresh audit 11-02: nobody
    opened evals/run.py, and one eval's result lived only in its README."""
    deny, results = [], []
    for dd, subdirs, files in os.walk(d):
        subdirs[:] = [x for x in subdirs if x not in SKIP_DIRS]
        for f in files:
            fp = os.path.join(dd, f)
            try:
                if os.path.getsize(fp) > 300_000:
                    continue
            except OSError:
                continue
            low = f.lower()
            if re.match(r"(run|bench|grade|eval|harness)\w*\.(py|mjs|js|ts|sh)$", low):
                try:
                    for ln in open(fp, errors="replace"):
                        if re.search(r"\bden(y|ied)\b|disallow|sandbox|allowedTools|--tools|tmp ?home|HOME=|read.?only|without (evals|tests)", ln, re.I):
                            deny.append(f"{os.path.relpath(fp, d)}: {scrub(ln.strip())[:140]}")
                            if len(deny) >= 6:
                                break
                except OSError:
                    pass
            elif low.startswith(("readme", "report", "results", "story")) and low.endswith(".md"):
                try:
                    for ln in open(fp, errors="replace"):
                        if re.search(r"R@\d+|\b\d+\s*/\s*\d+\b|\b\d+(\.\d+)?%|pass(ed)?\b.*\d", ln) and len(ln.strip()) > 12:
                            results.append(f"{os.path.relpath(fp, d)}: {scrub(ln.strip())[:140]}")
                            if len(results) >= 5:
                                break
                except OSError:
                    pass
    return deny[:6], results[:5]


# ---------------------------------------------------------------- how deep each skill of theirs goes (the ruling 11-06)
# Facts only; the rule that turns them into depth 1-4 is labs/v15/engine/board.py (skill_depth). Display only: no level moves.
# A skill is found in the logs by its name and the `skills/<name>/` part of a path (so a worktree, a checkout, a symlink or a
# plugin cache copy of it all count), never by the name of one person's tool.
CODE_EXT = (".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php", ".go", ".lua", ".ps1")
RUNNERS = {"python", "python3", "uv", "uvx", "node", "bun", "deno", "npx", "tsx", "ts-node", "bash", "sh", "zsh", "ruby",
           "perl", "php", "go", "source", ".", "pnpm", "npm", "yarn", "make", "just", "pwsh", "lua", "pipx", "poetry", "env"}
# `skills/<name>/<file>` anywhere in a path or command; a lookahead, so `skills/a/skills/b/x` yields both a/skills/b/x and b/x
SKILL_PATH_RX = re.compile(r"(?=(?:^|[/\s'\"=])skills/([^/\s'\"]+)/([^\s'\"|;&()<>]+))")
CD_SKILL_RX = re.compile(r"skills/([^/\s'\"]+)")
# a folder in a skill that suggests evals; it is only a note until the logs show them running (see eval_seen_running)
EVAL_NOTE_DIRS = {"evals", "tests", "test"}
SCRIPT_RUNNERS = {"pytest", "py.test", "vitest", "jest", "mocha", "tox", "nox", "bats", "unittest", "ava", "playwright"}
PACKAGE_TOOLS = {"npm", "pnpm", "yarn", "uv", "uvx", "make", "just", "cargo", "go", "poetry", "pipx", "bun", "deno", "npx"}
NOT_A_RUN = {"install", "add", "sync", "i", "ci", "lock", "init", "remove", "update", "upgrade", "build", "pip", "version", "help"}


def skill_refs(text):
    """[(skill name, file inside it)] for every `skills/<name>/<file>` in a path or shell command."""
    return [(m.group(1), m.group(2)) for m in SKILL_PATH_RX.finditer(text if text.startswith("/") else "/" + text)]


def uses_live_context(text):
    """Claude Code's live-context syntax: !`command` on a line, or a fenced block that opens with ```!. Anything inside an
    ordinary code fence is documentation of it, not use."""
    in_fence = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("```"):
            if not in_fence and s[3:].lstrip().startswith("!"):
                return True
            in_fence = not in_fence
            continue
        if not in_fence and re.search(r"(?<![\w`])!`[^`\n]+`", ln):
            return True
    return False


def skill_folder(folder, entry_name):
    """{relative path: is it code} for every file in a skill's folder except its SKILL.md and junk."""
    out = {}
    for d, subdirs, files in os.walk(folder):
        subdirs[:] = [s for s in subdirs if s not in SKIP_DIRS]
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), folder)
            if f == ".DS_Store" or rel == entry_name:
                continue
            try:
                ex = os.access(os.path.join(d, f), os.X_OK)
            except OSError:
                ex = False
            out[rel] = f.endswith(CODE_EXT) or ex
            if len(out) >= 500:
                return out
    return out


READERS = {"cat", "head", "tail", "sed", "less", "more", "bat", "nl"}


def shell_reads(cmd):
    """{(skill name, file inside it)} that a shell command opens to read: `cat`, `head`, `sed -n`, ... on a `skills/<name>/<file>`
    path. The agent often opens a skill's reference this way instead of with its Read tool."""
    out = set()
    for seg in re.split(r"&&|\|\||;|\||\n", cmd):
        try:
            tok = shlex.split(seg)
        except ValueError:
            tok = seg.split()
        if tok and os.path.basename(tok[0]) in READERS:
            for t in tok[1:]:
                out.update(skill_refs(t))
    return out


def runs_script(cmd, name, scripts):
    """Does this shell command run one of the skill's scripts? A script is run when it is the program of a command (`./x`,
    `scripts/x.sh`) or an argument of an interpreter or runner (`python3 x.py`, `uv run x.py`); `cat x.py`, `git add x.py`,
    `ls` only name it. `cd <the skill's folder> && ./x.sh` counts: relative names are read from the folder cd'd into.
    Returns the script's path inside the skill, or None."""
    here = None
    for seg in re.split(r"&&|\|\||;|\||\n", cmd):
        try:
            tok = shlex.split(seg)
        except ValueError:
            tok = seg.split()
        while tok and (re.match(r"^\w+=", tok[0]) or tok[0] in ("sudo", "time", "exec", "nohup")):
            tok = tok[1:]
        if not tok:
            continue
        if tok[0] == "cd" and len(tok) > 1:
            m = CD_SKILL_RX.search(tok[1])
            here = m.group(1) if m and m.group(1) == name else None
            continue
        if not (os.path.basename(tok[0]).split("-")[0] in RUNNERS or os.path.basename(tok[0]) in RUNNERS or
                any(tok[0].endswith(s) for s in scripts) or tok[0].startswith("./")):
            continue
        for t in tok:
            for n, rel in skill_refs(t):
                if n == name and rel in scripts:
                    return rel
            if here == name and os.path.normpath(t) in scripts:
                return os.path.normpath(t)
    return None


def runs_a_script_or_test(cmd):
    """Does this shell command run a script or a test (as opposed to reading, listing or committing)? True for a program that
    is an interpreter or runner (`python3 x.py`, `node x.js`, `bash x.sh`, `uv run ...`, `npm test`, `pytest`), a path to run
    (`./x`) or a file with a code extension. `claude -p ...` alone is not: a run launched by a script or a test is what counts."""
    for seg in re.split(r"&&|\|\||;|\||\n", cmd):
        try:
            tok = shlex.split(seg)
        except ValueError:
            tok = seg.split()
        while tok and (re.match(r"^\w+=", tok[0]) or tok[0] in ("sudo", "time", "exec", "nohup", "env")):
            tok = tok[1:]
        if not tok or tok[0] == "cd":
            continue
        prog = os.path.basename(tok[0])
        if prog in PACKAGE_TOOLS:
            sub = next((x for x in tok[1:] if not x.startswith("-")), "")
            if sub not in NOT_A_RUN:
                return True
        elif prog in SCRIPT_RUNNERS or prog.split("-")[0] in RUNNERS or prog in RUNNERS or tok[0].startswith("./") or tok[0].endswith(CODE_EXT):
            return True
    return False


def files_named_by(cmd, cwd):
    """Absolute paths of the files a shell command names: each word shaped like a path (a slash, or a file extension), read
    from cwd, or from the folder a `cd` moved to. At most 8. A script or test call names the script and often the file of
    cases it reads (`python3 evals/run.py --cases evals/cases.json`); whether a path exists is asked later."""
    out, here = [], cwd

    def absolute(w):
        w = os.path.expanduser(w)
        return os.path.normpath(w if w.startswith("/") else os.path.join(here, w)) if w.startswith("/") or here else None

    for seg in re.split(r"&&|\|\||;|\||\n", cmd):
        try:
            tok = shlex.split(seg)
        except ValueError:
            tok = seg.split()
        if tok and tok[0] == "cd" and len(tok) > 1:
            here = absolute(tok[1]) or here
            continue
        skip = False
        for w in tok:
            if skip or re.match(r"^\d*>", w):      # a redirect writes its target; it reads nothing
                skip = w in (">", ">>", "&>") or bool(re.fullmatch(r"\d*>>?", w))
                continue
            if w.startswith("-"):
                w = w.partition("=")[2]
            if not w or "://" in w or len(w) > 300 or ("/" not in w and not re.search(r"\.\w{1,6}$", w)):
                continue
            a = absolute(w)
            if a:
                out.append(a)
    return tuple(dict.fromkeys(out))[:8]


def plugin_of(folder):
    """(folder, name) of the plugin that holds this folder: the nearest parent with .claude-plugin/plugin.json; None if none."""
    d = os.path.realpath(folder)
    for _ in range(8):
        pj = os.path.join(d, ".claude-plugin", "plugin.json")
        if os.path.isfile(pj):
            try:
                with open(pj, errors="replace") as fh:
                    name = json.load(fh).get("name")
            except (OSError, ValueError, AttributeError):
                name = None
            return d, str(name or os.path.basename(d))
        if os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    return None


EVAL_FLAGS_WITH_A_VALUE = {"--ablation", "--case", "-j", "--concurrency", "--eval-dir", "--judge-model", "--max-cost-usd", "--mocks",
                           "--model", "--output-dir", "--report", "--runs", "--allow-tools", "--disallow-tools"}


def plugin_eval_calls(cmd, cwd):
    """[(target, folder to read a relative target from)] for each `claude plugin eval <target>` that a shell command RUNS: the
    program of a command, not text in a heredoc, an echo, a grep or `--help`. The target is a path, a plugin name or
    plugin@marketplace; a call with no target proves nothing. A `cd X` earlier in the command moves the folder."""
    heredoc = cmd.find("<<")
    if heredoc >= 0:
        cmd = cmd[:heredoc]
    here, out = cwd or "", []
    for seg in re.split(r"&&|\|\||;|\||\n", cmd):
        try:
            tok = shlex.split(seg)
        except ValueError:
            tok = seg.split()
        while tok and (re.match(r"^\w+=", tok[0]) or tok[0] in ("sudo", "time", "exec", "nohup", "env")):
            tok = tok[1:]
        if not tok:
            continue
        if tok[0] == "cd" and len(tok) > 1:
            c = os.path.expanduser(tok[1])
            here = c if os.path.isabs(c) else os.path.join(here, c)
            continue
        if os.path.basename(tok[0]) != "claude":
            continue
        rest = tok[1:]
        k = next((j for j in range(len(rest) - 1) if rest[j] == "plugin" and rest[j + 1] == "eval"), None)
        if k is None or not all(x.startswith("-") for x in rest[:k]):
            continue
        args, positional, j = rest[k + 2:], [], 0
        if "-h" in args or "--help" in args:
            continue
        while j < len(args):
            if args[j] in EVAL_FLAGS_WITH_A_VALUE:
                j += 2
                continue
            if not args[j].startswith("-"):
                positional.append(args[j])
                break
            j += 1
        if positional and positional[0] not in ("init", "help"):
            out.append((positional[0], here))
    return out


def target_is_plugin(token, here, plugin_dir, plugin_name):
    """Does a `claude plugin eval` target (a path, a plugin name, or plugin@marketplace) resolve to this plugin? A path counts
    when it is the plugin's folder or inside it, or (the folder is another checkout of it, or gone) names a plugin.json of the
    same name, or ends the same way as the plugin's folder."""
    if "/" in token or token.startswith((".", "~")):
        path = os.path.expanduser(token)
        path = os.path.normpath(path if os.path.isabs(path) else os.path.join(here, path))
        if os.path.exists(path):
            real = os.path.realpath(path)
            if real == plugin_dir or real.startswith(plugin_dir + os.sep):
                return True
            there = plugin_of(real)
            return bool(there and there[1] == plugin_name)
        parts, want = path.split(os.sep), plugin_dir.split(os.sep)[-2:]
        return any(parts[i:i + len(want)] == want for i in range(len(parts)))
    name = token.partition("@")[0]
    return name in (plugin_name, os.path.basename(plugin_dir))


EVAL_PROMPT_MIN = 24            # a headless run's prompt shorter than this could be in any test file by chance
EVAL_FILE_MAX = 2_000_000       # bytes of one file read for prompts
EVAL_SCAN_BYTES = 200_000_000   # and in all
EVAL_SCAN_SECONDS = 45
EVAL_DATA_EXT = (".json", ".jsonl", ".yaml", ".yml", ".md", ".txt", ".csv", ".tsv", ".toml", ".prompt")
EVAL_DATA_RX = re.compile(r"[~$A-Za-z0-9_./{}-]+\.(?:jsonl?|ya?ml|md|txt|csv|tsv|toml|prompt)\b")
LAUNCHES_CLAUDE_RX = re.compile(r"(?i)\bclaude\b|claude[_-]agent[_-]sdk|\banthropic\b")


@functools.lru_cache(maxsize=None)
def _launches_claude(script):
    """Whether a script's text mentions the Claude program or its SDK: one that can start a headless run."""
    try:
        with open(script, errors="replace") as f:
            return bool(LAUNCHES_CLAUDE_RX.search(f.read(200_000)))
    except OSError:
        return False


def _file_is_theirs(path, me):
    """Whose a file is, by ownership.py (through authorship): only asked of a file that holds a run's prompt."""
    try:
        return bool(authorship(path, me, repo_root(os.path.dirname(path)))["yours"])
    except Exception:  # noqa: BLE001 - a file we can't place is not counted
        return False


def _eval_dirs_files(root):
    """Every file inside an evals/, tests/ or test/ folder under root."""
    for d, subdirs, _files in walk_dirs(root, 6):
        if os.path.basename(d) not in EVAL_NOTE_DIRS:
            continue
        subdirs[:] = []
        for dd, dns, fns in os.walk(d):
            dns[:] = [x for x in dns if x not in SKIP_DIRS]
            for fn in fns:
                yield os.path.join(dd, fn)


@functools.lru_cache(maxsize=None)
def _files_a_script_reads(script):
    """Data files a script's text names (json, yaml, md, txt, csv ...), read from its folder, its repo and home."""
    out = []
    try:
        with open(script, errors="replace") as f:
            text = f.read(200_000)
    except OSError:
        return ()
    for m in EVAL_DATA_RX.findall(text):
        m = os.path.expandvars(os.path.expanduser(m.replace("${HOME}", HOME).replace("$HOME", HOME)))
        sd = os.path.dirname(script)
        for base in (sd, os.path.dirname(sd), repo_root(sd) or sd, HOME):
            cand = m if m.startswith("/") else os.path.join(base, m)
            if os.path.isfile(cand):
                out.append(os.path.normpath(cand))
                break
    return tuple(dict.fromkeys(out))


def eval_file_matches(latest_start, own_evals, roots, script_calls, is_theirs):
    """(prompt key, file, mtime) for every first-prompt key found in a file of theirs that holds eval or test cases, each key
    once. latest_start: key -> when the latest run with that first prompt started. mtime is None for a file in an evals/, tests/
    or test/ folder, and the file's modification time for one a script reads: data that script read can only have fed a run if
    it existed when the run started (a log or a result file a tool wrote afterwards holds prompts too), so only runs starting
    at or after that time count.
    The files, in this order: any file in an evals/, tests/ or test/ folder of a skill of theirs (own_evals), of a repo the
    scan read (roots), and a file a script of theirs reads that starts headless runs (its text mentions claude or its SDK): a
    data file its call names, or one its text names. A script that never mentions claude launched nothing.
    Whitespace is normalised and the key is a prefix of the prompt (prompt_key), as the scheduler match does it; a file that
    is not theirs by ownership.py (an installed skill's evals, a teammate's tests) never counts."""
    remaining, seen, budget, t_end = set(latest_start), set(), EVAL_SCAN_BYTES, time.monotonic() + EVAL_SCAN_SECONDS
    remaining = {k for k in remaining if not k.startswith(SKILL_LOADED_PREFIX)}      # Claude Code's own text, never a prompt of theirs

    def candidates():
        for f in own_evals:
            yield f, False
        for root in roots:
            for f in _eval_dirs_files(root):
                yield f, False
        for call in sorted(script_calls):
            launchers = [f for f in call if f.endswith(CODE_EXT) and _launches_claude(f)]      # a missing file launches nothing
            if not launchers:
                continue
            for f in call:
                if f.endswith(EVAL_DATA_EXT) and os.path.isfile(f):
                    yield f, True
            for f in launchers:
                for g in _files_a_script_reads(f):
                    yield g, True

    for path, read_by_script in candidates():
        if not remaining or budget <= 0 or time.monotonic() > t_end:
            break
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        try:
            size = os.path.getsize(real)
            if size > EVAL_FILE_MAX or not os.path.isfile(real):
                continue
            mtime = os.path.getmtime(real) if read_by_script else None
            with open(real, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        budget -= size
        if b"\0" in raw[:4096]:
            continue
        text = searchable(raw.decode("utf-8", "replace"))
        hits = [k for k in remaining if k in text and (mtime is None or latest_start[k] >= mtime)]
        if hits and is_theirs(path):
            for k in hits:
                remaining.discard(k)
                yield k, path, mtime


def skill_depth_facts(A, recs, start, me, roots, own_plugins=()):
    """One fact row per skill the person wrote (anatomy origin `own`, which ownership.py decided), by name. What it holds
    and what the logs of the window show about it; board.skill_depth turns the row into a depth.

      name · lines (of SKILL.md) · other_files (everything else in its folder) · loads (Skill calls, and typed /name read
      from Claude Code's "Base directory for this skill" record, once per load) ·
      loads_opened (loads after which the agent opened one of the skill's other files, before its next load) · opened (that
      file) · script_run (a script of the skill, run after a load) · live_syntax (SKILL.md uses !`cmd`) ·
      eval (the skill's evals seen RUNNING, from what Claude Code writes: {how: plugin_eval | headless_run, plugin_evals: calls
      of `claude plugin eval` on the plugin that holds the skill, headless_runs: headless runs that loaded the skill and whose first
      prompt is written in an eval or test file of theirs, eval_files: the first such files}; None when neither was seen) ·
      has_evals_folder (an evals/, tests/ or test/ folder inside the skill: only a note unless eval is set) ·
      copies (same name in other folders) · shared_name (an installed skill has the name too: uses can't be told apart,
      none are counted)."""
    arts = [x for x in A.get("artifacts") or [] if x.get("kind") == "skill" and x.get("name")]
    others = {x["name"] for x in arts if x.get("origin") in ("installed", "third_party")}
    rep, copies = {}, Counter()
    for x in arts:
        if x.get("origin") != "own":
            continue
        copies[x["name"]] += 1
        best = rep.get(x["name"])
        if best is None or (x.get("files") or 0, x.get("bytes") or 0) > (best.get("files") or 0, best.get("bytes") or 0):
            rep[x["name"]] = x
    if not rep:
        return []
    # one pass over the sessions' events: loads, and per skill the reads, edits and shell commands that name its folder
    by_session = defaultdict(list)
    headless_t0 = {}         # session id -> when the headless run (entrypoint sdk-*) started
    for r in recs:
        if not r.get("helper") and (r.get("ep") or "").startswith(UNATTENDED):
            headless_t0[r["chat"]] = r.get("t0") or min((e[0] for e in r.get("skill_ev") or ()), default=0)
        if r.get("skill_ev"):
            sid = os.path.basename(os.path.dirname(os.path.dirname(r["file"]))) if r["helper"] else r["chat"]
            by_session[sid].extend(r["skill_ev"])
    info = {}
    for name, a in rep.items():
        folder = os.path.dirname(a["entry"]) if a.get("entry") else a.get("path")
        entry = os.path.basename(a["entry"]) if a.get("entry") else "SKILL.md"
        try:
            with open(os.path.join(folder, entry), errors="replace") as fh:
                text = fh.read()
        except (OSError, TypeError):
            text = ""
        files = skill_folder(folder, entry) if folder and os.path.isdir(folder) else {}
        info[name] = {"name": name, "folder": folder, "lines": len(text.splitlines()), "other_files": len(files),
                      "files": files, "loads": 0, "loads_opened": 0, "opened": None, "script_run": None,
                      "live_syntax": uses_live_context(text), "eval": None, "copies": copies[name],
                      "shared_name": name in others}
    for evs in by_session.values():
        evs.sort(key=lambda e: e[0])
        loads = defaultdict(list)       # skill name -> times it loaded in this session
        edited = set()
        # Claude Code writes "Base directory for this skill: <folder>" (a "loaded" event) every time a skill's text goes in:
        # right after a Skill call, where it repeats that call's load, and on its own when the person typed /name (the
        # <command-message> form, which the log reader leaves to this record). A load is still pending until its record comes.
        pending = Counter()
        for t, kind, val in evs:
            if kind == "load":
                ns, _, short = val.rpartition(":")
                pending[short] += 1
                if short in info and not info[short]["shared_name"] and (not ns or ns in own_plugins):
                    loads[short].append(t)
            elif kind == "loaded":
                if pending[val]:
                    pending[val] -= 1
                elif val in info and not info[val]["shared_name"]:
                    loads[val].append(t)
            elif kind == "edit":
                edited.update(skill_refs(val))
        for name, ts in loads.items():
            i = info[name]
            i["loads"] += len(ts)
            hit = set()
            for t, kind, val in evs:
                if t < ts[0]:
                    continue
                if kind == "read":
                    for n, rel in skill_refs(val):
                        if n == name and rel in i["files"] and (name, rel) not in edited:
                            hit.add(max(j for j, lt in enumerate(ts) if lt <= t))      # the load this read came after
                            i["opened"] = i["opened"] or rel
                elif kind == "bash" and name in val:
                    for n, rel in shell_reads(val):
                        if n == name and rel in i["files"] and (name, rel) not in edited:
                            hit.add(max(j for j, lt in enumerate(ts) if lt <= t))
                            i["opened"] = i["opened"] or rel
                    scripts = {rel for rel, code in i["files"].items() if code}
                    if scripts and not i["script_run"]:
                        i["script_run"] = runs_script(val, name, scripts)
            i["loads_opened"] += len(hit)
    # evals seen RUNNING the skill (the ruling 11-06), never guessed from a file's contents:
    #  (a) `claude plugin eval <target>` in their logs, the target resolving to the plugin folder that holds the skill;
    #  (b) a headless run (entrypoint sdk-*) that loaded the skill and whose first prompt is written in a file of theirs that holds
    #      eval or test cases (eval_file_matches). Not by time: a run that merely overlaps a script call proves nothing.
    eval_calls, script_calls, headless_prompt = [], set(), {}
    headless_t0 = {}
    for r in recs:
        if not r.get("helper") and (r.get("ep") or "").startswith(UNATTENDED):
            headless_prompt[r["chat"]] = prompt_key(r.get("first_prompt"))
            headless_t0[r["chat"]] = r.get("t0") or min((e[0] for e in r.get("skill_ev") or ()), default=0)
    for sid, evs in by_session.items():
        for e in evs:
            if e[1] == "plugin_eval":
                eval_calls.append(e[2])
            elif e[1] == "script" and sid not in headless_prompt:      # a script their own session ran
                script_calls.add(tuple(e[2]))
    runs_of = defaultdict(list)       # first-prompt key -> [(skills that run loaded, when it started)]
    for sid, key in headless_prompt.items():
        names = set()
        for e in by_session.get(sid, ()):
            if e[1] == "load":
                ns, _, short = e[2].rpartition(":")
                if short in info and (not ns or ns in own_plugins):
                    names.add(short)
            elif e[1] == "loaded" and e[2] in info:
                names.add(e[2])
        if names and len(key) >= EVAL_PROMPT_MIN:
            runs_of[key].append((names, headless_t0.get(sid) or 0))
    launched, eval_files = Counter(), defaultdict(list)
    if runs_of:
        own_evals = [os.path.join(i["folder"], rel) for i in info.values() if i["folder"]
                     for rel in i["files"] if EVAL_NOTE_DIRS & set(rel.split(os.sep)[:-1])]
        latest = {k: max(t for _, t in v) for k, v in runs_of.items()}
        for key, path, mtime in eval_file_matches(latest, own_evals, roots, script_calls, lambda f: _file_is_theirs(f, me)):
            for names, t0 in runs_of[key]:
                if mtime is not None and t0 < mtime:
                    continue
                for n in names:
                    launched[n] += 1
                    if path not in eval_files[n]:
                        eval_files[n].append(path)
    for name, i in info.items():
        i["has_evals_folder"] = bool(i["folder"] and any(EVAL_NOTE_DIRS & set(rel.split(os.sep)[:-1]) for rel in i["files"]))
        i["eval"] = None
        if i["shared_name"]:
            continue
        there = plugin_of(i["folder"]) if i["folder"] and os.path.isdir(i["folder"]) else None
        n_eval = sum(1 for tok, here in eval_calls if there and target_is_plugin(tok, here, *there))
        if n_eval or launched[name]:
            i["eval"] = {"how": "plugin_eval" if n_eval else "headless_run", "plugin_evals": n_eval, "headless_runs": launched[name],
                         "eval_files": [t_(f) for f in eval_files[name][:3]]}
    out = []
    for name in sorted(info):
        i = info[name]
        del i["folder"], i["files"]
        out.append(i)
    return out


def words_history(path, me, max_revs=40):
    rr = repo_root(os.path.dirname(path))
    try:
        now = len(open(path, errors="replace").read().split())
    except OSError:
        return None
    out = {"path": path, "words_now": now}
    if not rr:
        return out
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(rr))
    log = [x.split("\t") for x in git(["log", "--follow", "--format=%h\t%ad\t%an <%ae>", "--date=short", "--", rel], rr).split("\n") if x]
    if not log:
        out["in_git"] = False
        return out
    revs = []
    step = max(1, len(log) // max_revs)
    for h, d, a in log[::step][:max_revs]:
        body = git(["show", f"{h}:{rel}"], rr, 10)
        revs.append({"rev": h, "date": d, "words": len(body.split()), "theirs": is_mine(a, me)})
    revs.reverse()
    peak = max(revs, key=lambda x: x["words"]) if revs else None
    trims = [{"date": b["date"], "from": a_["words"], "to": b["words"], "theirs": b["theirs"]}
             for a_, b in zip(revs, revs[1:]) if a_["words"] and b["words"] < a_["words"] * 0.9]
    out.update({"commits": len(log), "theirs": sum(1 for _, _, a in log if is_mine(a, me)),
                "authors": dict(Counter(a.split(" <")[0] for _, _, a in log).most_common(3)),
                "first": log[-1][1], "last": log[0][1], "peak_words": peak["words"] if peak else None,
                "peak_date": peak["date"] if peak else None, "trims": trims[-6:],
                "sampled_revisions": len(revs)})
    return out


def git_hooks(roots, me):
    out = []
    for r in roots:
        cfg = os.path.join(r, ".git", "config")
        hp = None
        try:
            m = re.search(r"hooksPath\s*=\s*(\S+)", open(cfg).read())
            hp = m.group(1) if m else None
        except OSError:
            pass
        d = os.path.join(r, hp) if hp and not os.path.isabs(hp) else (hp or os.path.join(r, ".git", "hooks"))
        d = os.path.expanduser(d)
        files = [f for f in (os.listdir(d) if os.path.isdir(d) else []) if not f.endswith(".sample")]
        if not files:
            continue
        item = {"repo": r, "hooks_path": hp, "dir": d, "hooks": files[:12]}
        # a tracked hooks folder: git decides; .git/hooks is never committed, so it is theirs (ownership.py, no receipt)
        item.update(authorship(d, me, r if d.startswith(r) and "/.git/" not in d else None))
        out.append(item)
    return out


def sqlite_stores(paths):
    out = []
    for p in paths:
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
            tabs = [t for (t,) in con.execute("select name from sqlite_master where type='table'")]
            rows = {}
            for t in tabs[:30]:
                if t.startswith("sqlite_") or "_fts" in t or t.endswith(("_data", "_idx", "_content", "_docsize", "_config")):
                    continue
                try:
                    n = con.execute(f'select count(*) from "{t}"').fetchone()[0]
                    cols = [c[1] for c in con.execute(f'pragma table_info("{t}")')]
                    tc = next((c for c in cols if c.lower() in ("created_at", "created", "ts", "timestamp", "time", "at",
                                                                "date", "inserted_at", "created_epoch")), None)
                    recent = None
                    if tc and n:
                        v = con.execute(f'select "{tc}" from "{t}" where "{tc}" is not null limit 1').fetchone()
                        v = v[0] if v else None
                        if isinstance(v, (int, float)):
                            cut = START * 1000 if v > 1e11 else START
                            recent = con.execute(f'select count(*) from "{t}" where "{tc}" >= ?', (cut,)).fetchone()[0]
                        elif isinstance(v, str) and re.match(r"\d{4}-\d\d-\d\d", v):
                            recent = con.execute(f'select count(*) from "{t}" where "{tc}" >= ?', (day(START),)).fetchone()[0]
                    rows[t] = n if recent is None else f"{n} ({recent} in window)"
                except sqlite3.Error:
                    pass
            con.close()
            out.append({"path": p, "bytes": os.path.getsize(p), "modified": day(os.path.getmtime(p)),
                        "tables": dict(sorted(rows.items(), key=lambda kv: -int(str(kv[1]).split()[0]))[:8])})
        except sqlite3.Error as e:
            out.append({"path": p, "error": str(e)[:80]})
    return out





def find_dbs():
    """Databases kept inside Claude Code's own folder (~/.claude), whoever's tool wrote them. No tool is looked for by
    name (the ruling 11-04): a member's tools live where theirs do."""
    out = []
    for base in (os.path.join(HOME, ".claude"),):
        for d, subdirs, files in os.walk(base):
            if d.count("/") - base.count("/") >= 4:
                subdirs[:] = []
            subdirs[:] = [s for s in subdirs if s not in ("projects", "file-history", "shell-snapshots", "todos", "node_modules",
                                                         "cache", "marketplaces", "versions", ".git")]
            for f in files:
                p = os.path.join(d, f)
                if f.endswith((".db", ".sqlite", ".sqlite3")):
                    out.append(p)
    return out[:30]


def past_work_reach(typed, own_start_ctx, automem_chats=()):
    """New sessions where earlier work reached Claude early, by the effect and never by a tool's name (the ruling 11-04),
    in three kinds kept apart: loaded automatically (Claude Code put the project's auto-memory MEMORY.md, which an earlier
    session or the person wrote, in the session's context at its start: the `instructions` record says so), looked up
    (Claude read a file another, earlier session wrote before the person's 3rd message), or put in by a hook (a
    SessionStart hook git credits to the person added text; own_start_ctx, decided in main()). `either` counts a session
    once however many kinds reached it."""
    first_write = {}
    for c in typed:
        for k in c.get("calls", []):
            if k.get("name") in ("Write", "Edit", "NotebookEdit", "MultiEdit") and k.get("key"):
                w = first_write.get(k["key"])
                if w is None or k["t"] < w[0]:
                    first_write[k["key"]] = (k["t"], c["chat"])
    looked = set()
    for c in typed:
        for k in c.get("calls", []):
            if k.get("name") == "Read" and k.get("at", 99) <= 2 and k.get("key") in first_write:
                t, who = first_write[k["key"]]
                if who != c["chat"] and t < k["t"]:
                    looked.add(c["chat"])
                    break
    ids = {c["chat"] for c in typed}
    inj = set(own_start_ctx) & ids
    auto = set(automem_chats) & ids
    both = looked | inj | auto
    n = len(ids)
    return {"new_chats": n, "loaded_automatically": len(auto), "looked_it_up_early": len(looked),
            "notes_put_in_by_a_hook": len(inj),
            "either": len(both), "either_pct": round(100 * len(both) / n, 1) if n else None,
            "rule": "loaded automatically = Claude Code put the project's auto-memory MEMORY.md (with text) in the session's "
                    "context at its start; looked up = Claude read a file an earlier session wrote, before the person's 3rd "
                    "message; put in = a SessionStart hook git credits to the person added text to Claude's context. "
                    "A session counts once if any of the three reached it"}


def job_is_theirs(args, plist, me):
    """A scheduled job (LaunchAgent, crontab line) that starts an agent: whose it is, by ownership.py. The first argument
    that is a file on disk is the job's script; with none, the job's own file (a plist in ~/Library/LaunchAgents is
    nobody's install, so it is theirs)."""
    for x in args or []:
        x = os.path.expanduser(str(x))
        if x.startswith("/") and os.path.isfile(x) and not x.startswith(("/bin/", "/usr/", "/opt/homebrew/bin/")):
            real = os.path.realpath(x)
            return authorship(real, me, repo_root(os.path.dirname(real)))["yours"]
    return authorship(plist, me, None)["yours"] if plist else True


def scheduled_file_is_theirs(path, me):
    """A Claude Code scheduled_tasks.json: whose it is, by ownership.py, as for any other file of theirs: its authorship in
    the repo it sits in (a teammate's committed project file is not theirs); one in no repo (~/.claude) is theirs."""
    if not path:
        return True
    real = os.path.realpath(path)
    return authorship(real, me, repo_root(os.path.dirname(real)))["yours"]


def hook_ownership(A, me, plugins):
    """own.hook: a hook's command → True when git shows the person's commits on its script (or the plugin it ships in
    comes from their repo), False when it shows only others', None when nothing says (a script outside any repo)."""
    conf = {}
    for h in A.get("hooks") or []:
        if h.get("command"):
            conf.setdefault(h["command"][:200], h.get("script_path"))
    cache = {}

    def own(cmd):
        if cmd in cache:
            return cache[cmd]
        script = conf.get(cmd)
        if not script:
            m = re.search(r"(/[^\s'\"]+\.(?:py|mjs|js|sh|ts))", cmd)
            script = m.group(1) if m else None
        res = None
        if script and (os.path.exists(script) or "/plugins/cache/" in script):
            # ownership.py: a published marketplace's cache = installed; a plugin read from a folder here = git on
            # that folder; anything else is theirs
            real = os.path.realpath(script)
            res = authorship(real, me, repo_root(os.path.dirname(real)))["yours"]
        cache[cmd] = res
        return res
    return own


def matcher_hits(matcher, tool):
    """Whether a hook's matcher (empty or "*" = every tool, "A|B", or a regex) covers this tool name."""
    m = (matcher or "").strip()
    if not m or m == "*" or not tool:
        return True
    if re.fullmatch(r"[\w|]+", m):
        return tool in m.split("|")
    try:
        return re.fullmatch(m, tool) is not None
    except re.error:
        return False


def rule_ownership(me):
    """own.rule_file: a CLAUDE.md / AGENTS.md / CLAUDE.local.md path → True when git shows the person's commits on it,
    False when only others', None when it isn't in a repo (git can't say)."""
    cache = {}

    def own(path):
        if path not in cache:
            res = None
            if path and os.path.isfile(path):
                rr = repo_root(os.path.dirname(os.path.realpath(path)))
                res = authorship(os.path.realpath(path), me, rr)["yours"]
            cache[path] = res
        return cache[path]
    return own


def claude_launch(me):
    """How `claude` starts on this machine: every `claude` on PATH, and for a wrapper script the config folders it reads
    and the lines that route it (a gateway, a base URL). Values are never written, only which files and names."""
    out = []
    seen = set()
    for d in os.environ.get("PATH", "").split(":") + [os.path.join(HOME, ".local", "bin")]:
        pth = os.path.join(d, "claude")
        if not os.path.isfile(pth) or pth in seen:
            continue
        seen.add(pth)
        real = os.path.realpath(pth)
        item = {"path": t_(pth), "real": t_(real)}
        try:
            head = open(real, "rb").read(200_000)
        except OSError:
            head = b""
        if head[:2] == b"#!":
            text = head.decode("utf-8", "replace")
            item["kind"] = "wrapper script"
            item["routes"] = [scrub(x.strip())[:120] for x in text.splitlines()
                              if re.search(r"gateway|BASE_URL|exec |conf\.d|proxy", x, re.I)][:8]
            cfg = sorted(set(re.findall(r"(?:~|\$HOME|\$\{HOME\}|\$XDG_CONFIG_HOME)/[\w./-]*conf\.d", text)))
            confs = []
            for c in cfg:
                cdir = os.path.expanduser(c.replace("$HOME", "~").replace("${HOME}", "~").replace("$XDG_CONFIG_HOME", "~/.config"))
                if os.path.isdir(cdir):
                    confs.append({"dir": t_(cdir), "active": sorted(f for f in os.listdir(cdir) if not f.endswith(".off")),
                                  "off": sorted(f for f in os.listdir(cdir) if f.endswith(".off"))})
            item["config"] = confs
            rr = repo_root(os.path.dirname(real))
            if rr:
                item.update(authorship(real, me, rr))
        else:
            item["kind"] = "binary"
        out.append(item)
    return out


def attribute_hook_runs(typed, headless, A, me):
    """Hooks that ran, by event and the plugin or file that set them up (matched on the configured command)."""
    conf = {}
    for h in A.get("hooks") or []:
        cmd = h.get("command")
        if not cmd:
            continue
        who = h.get("plugin") or f"{h.get('scope')}: {t_(h.get('source'))}"
        conf.setdefault(cmd[:200], (who, h.get("script_path")))
    owner_cache = {}

    def owner(script):
        if not script or not os.path.exists(script):
            return None
        if script not in owner_cache:
            rr = repo_root(os.path.dirname(os.path.realpath(script)))
            owner_cache[script] = authorship(os.path.realpath(script), me, rr)
        return owner_cache[script]

    def rows(dd):
        out = []
        for (event, cmd), (n, chats) in sorted(dd.items(), key=lambda kv: -len(kv[1][1]))[:40]:
            who, script = conf.get(cmd, (None, None))
            if who is None:
                m = re.search(r"(/[^\s'\"]+\.(?:py|mjs|js|sh|ts))", cmd)
                script = m.group(1) if m else None
                who = "(not in any settings or plugin read now)"
            o = owner(script)
            out.append({"event": event, "set_up_by": who, "script": t_(script) if script else scrub(cmd)[:90],
                        "runs": n, "chats": len(chats), **({"theirs": o.get("theirs"), "yours": o.get("yours"), "commits": o.get("commits")} if o else {})})
        return out
    return {"typed_chats": rows(typed), "headless": rows(headless)}


def t_(p):
    return (p or "").replace(HOME, "~")


def who_mentions(name, plugins, roots):
    """Which of the person's plugins (or repos' service code) name a database file: who writes or reads it."""
    hits = []
    dirs = [(p["key"], p["source_repo_dir"]) for p in plugins if p.get("source_repo_dir") and p.get("yours")]
    for key, d in dirs:
        found = False
        for dd, subdirs, files in os.walk(d):
            subdirs[:] = [x for x in subdirs if x not in SKIP_DIRS and x not in ("evals", "tests", "test")]
            for f in files:
                if not f.endswith((".py", ".mjs", ".js", ".ts", ".sh", ".json", ".toml")):
                    continue
                fp = os.path.join(dd, f)
                try:
                    if os.path.getsize(fp) < 400_000 and name in open(fp, errors="replace").read():
                        found = True
                        break
                except OSError:
                    pass
            if found:
                break
        if found:
            hits.append(key)
    return sorted(set(hits))


def schedule_logs(roots, start):
    """Logs a scheduled job leaves (cron.log, *-cron.log, schedule*.log): proof of runs nobody started by hand, even
    when the crontab line is gone."""
    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for d, subdirs, files in os.walk(root):
            if d.count("/") - root.count("/") >= 7:
                subdirs[:] = []
            subdirs[:] = [x for x in subdirs if x not in SKIP_DIRS and x not in ("worktrees", "w", ".upstreams")
                          and not os.path.isfile(os.path.join(d, x, ".git"))]
            for f in files:
                fl = f.lower()
                if not (fl.endswith((".log", ".jsonl")) and ("cron" in fl or "schedul" in fl or "tick" in fl)):
                    continue
                fp = os.path.join(d, f)
                try:
                    st = os.stat(fp)
                    if st.st_mtime < start:
                        continue
                    n = sum(1 for _ in open(fp, errors="replace"))
                except OSError:
                    continue
                out.append({"path": t_(fp), "lines": n, "last_written": day(st.st_mtime)})
    return out[:20]


# ---------------------------------------------------------------- compacting the file inventory


def compact_anatomy(A, me, roots, rd, wr):
    out = {}
    s = A.get("settings") or {}
    files = []
    for f in s.get("files") or []:
        cnt = next((c for c in s.get("counts") or [] if c.get("file") == f["path"]), {})
        notable = {n["key"]: n["value"] for n in s.get("notable") or [] if n.get("file") == f["path"]}
        item = {"path": f["path"], "scope": f.get("scope"), "keys": f.get("top_level_keys"), "counts": {k: v for k, v in cnt.items() if k != "file"},
                "notable": notable}
        try:
            j = json.load(open(f["path"]))
            perms = j.get("permissions") or {}
            item["deny_rules"] = [scrub(str(x))[:80] for x in perms.get("deny") or []][:40]
            item["ask_rules"] = [scrub(str(x))[:80] for x in perms.get("ask") or []][:20]
            item["allow_rules_count"] = len(perms.get("allow") or [])
            item["default_mode"] = perms.get("defaultMode")
            item["hook_events"] = {ev: sum(len(h.get("hooks") or []) for h in hs) for ev, hs in (j.get("hooks") or {}).items()}
            ep = j.get("enabledPlugins") or {}
            item["plugins_on"] = sorted(k for k, v in ep.items() if v)
            item["plugins_off"] = sorted(k for k, v in ep.items() if not v)
            item["skill_overrides"] = len(j.get("skillOverrides") or {})
            item["denied_mcp_servers"] = [x.get("serverName") if isinstance(x, dict) else str(x) for x in j.get("deniedMcpServers") or []][:12]
            for k in ("enabledMcpjsonServers", "disabledMcpjsonServers", "enableAllProjectMcpServers", "sandbox"):
                if k in j:
                    item[k] = j[k] if not isinstance(j[k], dict) else sorted(j[k].keys())
        except (OSError, ValueError):
            pass
        files.append(item)
    out["settings"] = files

    hooks = []
    for h in A.get("hooks") or []:
        hooks.append({"scope": h.get("scope"), "source": h.get("source"), "plugin": h.get("plugin"), "event": h.get("event"),
                      "matcher": h.get("matcher"), "script": h.get("script_path") or scrub(h.get("command") or "")[:100],
                      "script_exists": h.get("script_exists")})
    out["hooks_configured"] = hooks

    P = A.get("plugins") or {}
    plugins = []
    for e in P.get("enabled") or []:
        name = e["key"].split("@")[0]
        src = None
        if e.get("marketplace_is_own"):
            for r in roots:
                if os.path.isdir(os.path.join(r, "plugins", name)):
                    src = os.path.join(r, "plugins", name)
                    break
        item = {"key": e["key"], "on": e.get("enabled"), "marketplace": e.get("marketplace"),
                "marketplace_source": e.get("marketplace_source"), "own_marketplace": e.get("marketplace_is_own"),
                "adds": {k: (len(v) if isinstance(v, list) else v) for k, v in (e.get("contents") or {}).items() if v},
                "source_repo_dir": src}
        if src:
            item.update(authorship(src, me, repo_root(src)))
            ev = os.path.join(src, "evals")
            item["has_evals"] = os.path.isdir(ev)
        elif e.get("install_path"):
            # no copy found among the roots: ownership.py decides from the install receipt (published marketplace =
            # installed; a directory marketplace or a clone of theirs = git on the folder it was read from)
            item.update(authorship(e["install_path"], me, None))
        plugins.append(item)
    out["plugins"] = plugins

    arts = []
    by_kind = Counter()
    for a in A.get("artifacts") or []:
        by_kind[f"{a['kind']}/{a.get('origin')}"] += 1
        # own only: "mixed" (some of their commits, most by others) is a teammate's file they touched, not theirs
        if a.get("origin") != "own" or a.get("in_fixture_path"):
            continue
        # an agent file named after one of Claude Code's own agent types (Explore, Plan…) overrides the built-in: the
        # file may be theirs, but launches of that type are the built-in's, so it is never a thing they built
        if a["kind"] == "agent" and a.get("name") in BUILTIN_HELPERS:
            by_kind["agent/overrides_a_built_in"] += 1
            continue
        arts.append({"kind": a["kind"], "name": a.get("name"), "path": a.get("path"), "model": a.get("model"),
                     "description": scrub(a.get("description") or "")[:100], "bytes": a.get("bytes"),
                     "git": a.get("git"), "wired": a.get("wired"), "hooks": a.get("frontmatter_hooks")})
    # tools lists of own agent files (who may do what), read from the file's frontmatter
    for a in arts:
        if a["kind"] == "agent" and a["path"] and os.path.isfile(a["path"]):
            try:
                head = open(a["path"], errors="replace").read(3000)
                m = re.search(r"^tools:\s*(.+)$", head, re.M)
                a["tools"] = m.group(1).strip()[:160] if m else "(all)"
            except OSError:
                pass
        if a["kind"] == "skill" and a["path"] and os.path.isfile(a["path"]):
            try:
                a["words"] = len(open(a["path"], errors="replace").read().split())
            except OSError:
                pass
    # every one: a silent cap at 250 cut their agents to 7 of 52 once untracked files counted as theirs (11-06), and a
    # practice that cited a cut agent lost its fact. Ordered by kind so the small kinds lead whatever the reader skims.
    order = {"agent": 0, "hook_script": 1, "plugin_manifest": 2, "marketplace_manifest": 3, "command": 4, "workflow": 5,
             "output_style": 6, "skill": 7}
    arts.sort(key=lambda a: order.get(a["kind"], 9))
    out["own_artifacts"] = {"counts_by_kind_and_origin": dict(by_kind), "own": arts}

    out["mcp_servers"] = [{"name": m.get("name"), "scope": m.get("scope"), "source": m.get("source"),
                           "transport": m.get("transport"), "command": scrub(str(m.get("command") or ""))[:60],
                           "args": [scrub(x)[:60] for x in (m.get("args") or [])][:8] if isinstance(m.get("args"), list) else m.get("args_count"),
                           "url_host": m.get("url_host"), "env_names": [e.get("name") for e in m.get("env") or []]}
                          for m in A.get("mcp_servers") or []]

    stores = []
    for m in A.get("memory_stores") or []:
        if m.get("in_fixture_path"):
            continue
        p = m["path"]
        reads = sum(v[0] for d, v in rd.items() if d.startswith(p) or p.startswith(d + "/") and len(d) > len(HOME) + 10)
        rchats = {c for d, v in rd.items() if d.startswith(p) for c in v[1]}
        wchats = {c for d, v in wr.items() if d.startswith(p) for c in v[1]}
        stores.append({"path": p, "kind": m.get("kind"), "scope": m.get("scope"), "files": m.get("files"),
                       "md_files": m.get("md_files"), "frontmatter_share": m.get("frontmatter_share"),
                       "newest": m.get("newest_mtime"), "referenced_from": (m.get("referenced_from") or [])[:4],
                       "gitignored": m.get("gitignored"), "agent_reads": reads, "chats_reading": len(rchats),
                       "chats_writing_by_hand_tools": len(wchats)})
    out["memory_stores"] = sorted(stores, key=lambda x: -x["chats_reading"])

    L = A.get("launchers") or {}
    scripts = []
    for sc in L.get("scripts") or []:
        if sc.get("only_in_comments") or "/test" in sc.get("path", "") or ".test." in sc.get("path", ""):
            continue
        scripts.append({"path": sc["path"], "kinds": sc.get("kinds"),
                        "flags": sorted({f for h in sc.get("hits") or [] for f in h.get("flags") or []})[:12]})
    out["launchers"] = {"crontab": [{"schedule": e.get("schedule"), "command": scrub(e.get("command") or "")[:140],
                                     "starts_agents": e.get("script_mentions_claude") or e.get("mentions_claude"),
                                     "keeps_no_log": bool(e.get("keeps_no_log")), "runs_seen": e.get("runs_seen"),
                                     "last_exit": e.get("last_exit"), "seen_by": e.get("seen_by"),
                                     "own": job_is_theirs(shlex.split(e.get("command") or "", posix=True) if e.get("command") else [], None, me)}
                                    for e in (L.get("crontab") or {}).get("entries") or []],
                        "launchd_matching": [dict(x, own=job_is_theirs(x.get("program_arguments"), x.get("path"), me))
                                             for x in (L.get("launchd") or {}).get("matching") or []],
                        "launchd_other": [x.get("label") for x in (L.get("launchd") or {}).get("other") or []],
                        "scheduled_tasks": [dict(t, own=scheduled_file_is_theirs(t.get("path"), me))
                                            for t in L.get("claude_scheduled_tasks") or [] if isinstance(t, dict)],
                        "ci": [{"path": c.get("path"), "kinds": c.get("kinds")} for c in L.get("ci") or []][:15],
                        "scripts_that_start_agents": len(scripts), "scripts": scripts[:60]}

    instr = A.get("instruction_files") or []
    out["instruction_files_all"] = {"by_scope": dict(Counter(i.get("scope") for i in instr)),
                                    "folder_files": len([i for i in instr if i.get("scope") == "folder"])}
    out["worktrees"] = [{"repo": w.get("repo"), **{k: (w.get("summary") or {}).get(k) for k in ("linked", "merged", "unmerged")}}
                        for w in A.get("worktrees") or []]
    out["could_not_read"] = (A.get("could_not_read") or [])[:30]
    return out


# ---------------------------------------------------------------- the digest readers start from


def digest(inv):
    L = []
    w = L.append
    m = inv["meta"]
    w(f"# Inventory: every source read on this laptop ({m['days']} days to {m['to']})\n")
    w("Open a section with: python3 -c \"import json;print(json.dumps(json.load(open('inventory.json'))['SECTION'],indent=1))\"")
    w("Every number on the page must come from here (or from a file you name). 'not read' = no section covers it.\n")
    w(f"- Logs: {m['main_log_files']} session files + {m['helper_log_files']} sub-agent files; interactive sessions {inv['chats_logged']['typed_chat_files']}, "
      f"headless runs {inv['headless']['runs']} on {inv['headless']['days_with_runs']} days. Claude Code {m['versions']}")
    w(f"- Person (authorship): {inv['person']['identities'][:4]}")
    w(f"- Could not read: {len(m['could_not_read'])} paths" + (f" e.g. {m['could_not_read'][:3]}" if m["could_not_read"] else ""))
    w("\n## repos (sessions ran in)")
    for r in inv["repos"][:12]:
        w(f"- {r['repo']}: {r['typed_chats']} interactive sessions, {r['headless_runs']} headless; commits in window {r['commits_in_window']} "
          f"(theirs {r['theirs_in_window']}{'' if r.get('authors_here', True) else '; not counted in the share: they write little here'})"
          + (f"; also reached as {r['also_reached_as']}" if r.get("also_reached_as") else ""))
    w("\n## sections (key → what it answers → headline)")
    hc = inv["hook_context"]
    w(f"- hook_context → what hooks add to Claude's context: {hc['attachments_in_typed_chats']} attachments in "
      f"{hc['typed_chats_with_hook_text']} of {hc['typed_chats']} interactive sessions; top: " +
      "; ".join(f"{r['what'][1]} {r['what'][0]} {r['calls']}×/{r['chats']} sessions" for r in hc["by_event_and_tag_typed"][:8]))
    w(f"  blocking errors (a hook blocked a tool call): " + "; ".join(f"{r['what'][1]} {r['what'][0]} {r['calls']}" for r in hc["blocking_errors"][:6]))
    sh = inv["slash_commands"]
    w(f"- slash_commands → typed /commands, {sh.get('commands')} in your {sh.get('typed_sessions')} typed sessions (session logs; history.jsonl only for a command the logs never record); top: " +
      ", ".join(f"{c['command']} {c['times']} times in {c['chats']}" for c in sh.get("by_command", [])[:18]))
    t = inv["tools"]["typed_chats"]
    w("- tools → the agent's calls, interactive sessions: " + ", ".join(f"{r['what']} {r['calls']}" for r in t["by_tool"][:16]))
    w("  programs in the shell (own = person wrote it): " + ", ".join(
        f"{r['what']} {r['calls']}/{r['chats']}ch" + (" (own)" if (r.get("where") or {}).get("yours") else "")
        for r in t["by_program"][:30]))
    w("  MCP servers: " + ", ".join(f"{r['what']} {r['calls']}/{r['chats']}ch" for r in t["by_mcp_server"][:12]))
    h = inv["helpers"]
    w(f"- helpers → sub-agents: {h['calls']} calls in {h['chats']} sessions; {h['calls_that_set_a_model']} set a model; "
      f"{h['background_calls']} of the {h['calls']} sub-agent calls ran in the background; types: " + ", ".join(f"{x['type']} {x['calls']}" for x in h["by_type"][:14]))
    w(f"- workflows → Workflow runs: {inv['workflows']['runs']} in {inv['workflows']['chats']} sessions; saved scripts: "
      f"{len([a for a in inv['own_artifacts']['own'] if a['kind'] == 'workflow'])}")
    pd = inv["plan_dialogs"]
    w(f"- plan_dialogs → {pd['calls']} plan approvals, {pd['refused']} rejected, in {pd['chats']} sessions; plan files: {inv['plans']}")
    c = inv["compactions"]
    w(f"- compactions → {c['total']} ({c['by_trigger']}); typed /compact {c['typed_compact_commands']}, with focus text "
      f"{c['with_focus_text']}; median {c['median_tokens_before']} tokens before; {c['chats_compacted_more_than_once']} sessions more than once")
    rr = inv["rules_reaching"]
    w(f"- rules_reaching → subfolder instruction files loaded into Claude's context in {rr['chats_where_a_subfolder_file_reached_the_agent']} sessions ({rr['files']} files)")
    hl = inv["headless"]
    w(f"- headless → {hl['runs']} runs nobody typed in; modes {hl['permission_modes']}; top templates: " +
      "; ".join(f"{os.path.basename(x['repo'] or '?')} '{x['template'][:40]}' {x['runs']} on {x['days']}d "
                f"({x.get('while_away', x['started_with_no_typed_message_in_30_min_before'])} while away)" for x in hl["by_template"][:8]))
    w(f"  started while the person was away (nothing typed in the 30 min before, or a scheduler started it): "
      f"{hl.get('while_away', hl['started_with_no_typed_message_in_30_min_before'])} of {hl['runs']} runs; "
      f"{hl.get('started_by_a_scheduled_job', 0)} trace to a scheduled job")
    hk = Counter((h.get("plugin") or h.get("scope"), h.get("event")) for h in inv["hooks_configured"])
    w("- hooks_configured → hooks set up, by plugin or scope and event: " + ", ".join(f"{k[0]} {k[1]} {v}" for k, v in hk.most_common(30)))
    w(f"- settings → {len(inv['settings'])} files; home deny rules: " +
      str(next((s.get("deny_rules") for s in inv["settings"] if s["scope"] == "home" and s["path"].endswith("settings.json")), None)))
    own_pl = [p for p in inv["plugins"] if p.get("yours")]
    w(f"- plugins → {len(inv['plugins'])} installed ({sum(1 for p in inv['plugins'] if p['on'])} on); written by the person "
      f"(git on the source): " + ", ".join(f"{p['key']}{' +evals' if p.get('has_evals') else ''}" for p in own_pl))
    oa = inv["own_artifacts"]["own"]
    w(f"- own_artifacts → written by the person: " + ", ".join(f"{k} {v}" for k, v in Counter(a['kind'] for a in oa).most_common()))
    w(f"- mcp_servers → {len(inv['mcp_servers'])} configured: " + ", ".join(f"{m['name']}({m['scope']})" for m in inv["mcp_servers"][:14]))
    ev = inv["evals"]
    w(f"- evals → {len(ev)} eval folders (path: run-ledger lines in window/all, last logged run, case files, newest file, "
      f"commits theirs/all): " + "; ".join(
        f"{x['path'].replace(HOME, '~')}: {x['ledger_lines_in_window']}/{x['ledger_lines']} runs, last {x['last_run']}, "
        f"{x['case_files']} cases, newest {x['newest_file']}, {x['theirs']}/{x['commits']}" for x in ev))
    w("- instruction_files → always-loaded files, size in words now (peak): " + ", ".join(
        f"{x['path'].replace(HOME, '~')} {x['words_now']}w" + (f" (peak {x.get('peak_words')} {x.get('peak_date')}; theirs {x.get('theirs')}/{x.get('commits')})" if x.get("peak_words") else " (not in git)")
        for x in inv["instruction_files"]["always_loaded"][:10]))
    w(f"  edits by the agent: {inv['instruction_edits']['edits']} edits to {inv['instruction_edits']['files']} instruction/skill/agent files in {inv['instruction_edits']['chats']} sessions; "
      f"skill listing the agent sees: {inv['chats_logged']['skill_listing_words_max']} words")
    w("- memory_stores → " + "; ".join(f"{s['path'].replace(HOME, '~')} ({s['kind']}, {s['files']} files, read in {s['chats_reading']} sessions)"
                                     for s in inv["memory_stores"][:10]))
    w("  databases: " + "; ".join(f"{d['path'].replace(HOME, '~')} {d.get('tables')}" for d in inv["databases"][:6]))
    w("- git_hooks → " + "; ".join(f"{g['repo'].replace(HOME, '~')}: {g['hooks']}" for g in inv["git_hooks"]))
    la = inv["launchers"]
    w(f"- launchers → crontab {len(la['crontab'])} ({[c['command'][:60] for c in la['crontab']]}); launchd matching {la['launchd_matching']}; "
      f"scripts that start agents {la['scripts_that_start_agents']}")
    w(f"- worktrees → " + ", ".join(f"{os.path.basename(w_['repo'])} {w_['linked']}" for w_ in inv["worktrees"]) +
      f"; `git worktree add` by the agent {inv['chats_logged']['worktree_adds_by_shell']}")
    w(f"- probes → the old checks' numbers (numbers only; their verdicts failed audit): {list(inv['probes'].keys())}")
    w("- chats.jsonl (same folder) → every typed message's first 160 chars: search it with moments.py")
    wd = inv.get("who_did_it")
    if wd:
        w("- who_did_it → raw counts by who made it happen (levels for the mapped ids come from signals.yaml): " + "; ".join(
            f"{g['title']}: " + ", ".join(f"{i['label']} {i['n']}" for i in g["items"][:4]) for g in wd["groups"]))
    return "\n".join(L)


# ---------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="run folder")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--anatomy", help="reuse an existing anatomy.json instead of re-running it")
    ap.add_argument("--chats", help="reuse an existing chats.jsonl")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    T = {}
    t0 = time.monotonic()
    start = clock.now() - a.days * 86400
    global START, WRITTEN_DIRS
    START = start

    # typed chats first (cheap): the repos they ran in are the targets of everything else
    chats_path = os.path.join(a.out, "chats.jsonl")
    if a.chats and os.path.abspath(a.chats) != os.path.abspath(chats_path):
        import shutil
        shutil.copy(a.chats, chats_path)
    elif not a.chats:
        # read the logs again on every run: a session list left in the run folder by an earlier run was built by older
        # code from older logs (11-09: a person's rerun after a fix kept their old empty list). Reuse only on --chats.
        load_module("logs", LOGS).main(["--days", str(a.days), "--out", chats_path])
    T["chats"] = round(time.monotonic() - t0, 1)
    typed = [json.loads(x) for x in open(chats_path).readlines()[1:]]
    cwd_count = Counter(c.get("cwd") for c in typed)
    roots = Counter()   # typed sessions per scan folder (see scan_folder)
    for cwd, n in cwd_count.items():
        f = scan_folder(cwd)
        if f:
            roots[f] += n

    t = time.monotonic()
    recs, n_main, n_help, logs_timed_out = scan_logs(a.days)
    T["logs"] = round(time.monotonic() - t, 1)
    WRITTEN_DIRS = sorted({d for r in recs for d in r.get("written_dirs", ())})
    written_path = os.path.join(a.out, "written-dirs.json")   # the anatomy scan asks the same ownership question
    with open(written_path, "w") as f:
        json.dump(WRITTEN_DIRS, f)
    # the repos each typed session worked in, whatever folder it started in (a helper's work counts for the session that
    # started it): the start-folder rows below find what Claude Code loads; these find where the work happened
    worked_by_session = defaultdict(Counter)
    for r in recs:
        sid = r["chat"] if not r["helper"] else os.path.basename(os.path.dirname(os.path.dirname(r["file"])))
        worked_by_session[sid].update(r.get("worked") or {})
    with open(os.path.join(a.out, "worked-in.json"), "w") as f:   # the anatomy scan ranks projects by the same link
        json.dump({"sessions": {sid: dict(c) for sid, c in worked_by_session.items() if c}}, f)
    worked_chats = Counter()      # repo → typed sessions that worked in it
    for c in typed:
        worked_chats.update(worked_by_session.get(c["chat"], {}).keys())
    # repos found only from the work: no session started in them, nor below a start folder (those are read by their rows)
    from_work = repos_from_work(worked_chats, roots)
    head_cwds = Counter(r["cwd"] for r in recs if not r["helper"] and (r["ep"] or "").startswith(UNATTENDED))
    head_folders = Counter()
    for cwd, n in head_cwds.items():
        f = scan_folder(cwd)
        if f:
            head_folders[f] += n

    # the file inventory (a slow step) runs in the background while the rest is read. Its targets are the folders
    # sessions started in, typed and headless runs: typed first; among equals a repo (it has commits, hooks, evals to read) before a plain folder, then the most recent. A repo beneath a target that is not a repo is read
    # by that scan's own walk; the repos below are listed here only for the commit, hook and eval reads.
    anat_path = os.path.join(a.out, "anatomy.json")
    proc = None
    all_folders = roots + head_folders + from_work
    # sessions a person typed in come first (a pile of headless runs in scratch clones must not crowd out their repos)
    last = {}   # newest session per folder: the last tie-break between folders with as many sessions
    for cwd, t1 in [(c.get("cwd"), c.get("t1")) for c in typed] + [(r["cwd"], r["t1"]) for r in recs if not r["helper"]]:
        f = scan_folder(cwd) if t1 else None
        if f:
            last[f] = max(last.get(f, 0), t1)
    ranked = rank_folders(all_folders, roots, from_work, head_folders, last)
    if a.anatomy:
        anat_path = a.anatomy
    else:
        # the file scan runs again on every run, for the same reason (reuse only on --anatomy); an earlier run's file is
        # removed first, so a scan that fails can't leave the old one to be read as this run's
        if os.path.exists(anat_path):
            os.remove(anat_path)
        targets = list(dict.fromkeys(list(ranked) + own_marketplace_folders()))   # + their own marketplaces' folders
        starts = [c for c, _ in sorted((cwd_count + head_cwds).items(), key=lambda kv: (-kv[1], str(kv[0]))) if c and not TMP.match(c)][:300]
        proc = subprocess.Popen([sys.executable, ANATOMY, "--out", anat_path, "--written-dirs", written_path, *targets,
                                 *[x for f in starts for x in ("--start-folder", f)]],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    below = {}
    for f in [f for f in ranked if repo_root(f) is None]:
        below[f] = repos_below(f)
    below_all = list(dict.fromkeys(r for rs in below.values() for r in rs))[:BELOW_CAP]
    in_repo = {r: f for f, rs in below.items() for r in rs}
    agg, rd, wr = aggregate(recs, {c["chat"] for c in typed}, typed_times(chats_path), SchedulerStarts())

    t = time.monotonic()
    # who "the person" is: every email their git config knows (identity.py), never "whoever commits most here";
    # the most frequent author is kept for the page only, so a teammate's work is never credited to them
    seen_here = [*roots, *below_all, *[r for r in from_work if r not in roots and r not in below_all]]
    me_authors = me_from_repos(seen_here)
    me_top = me_authors.most_common(1)[0][0] if me_authors else ""
    idm = load_module("identity", IDENTITY)
    ident = idm.collect([Path(r) for r in dict.fromkeys(seen_here) if os.path.isdir(r)])
    me = {"emails": set(ident["emails"]), "names": set(idm.NAMES)}

    # repos: chats, headless runs, commits in the window
    head_by_repo = Counter()
    for r in recs:
        if not r["helper"] and (r["ep"] or "").startswith(UNATTENDED):
            head_by_repo[place(r["cwd"])] += 1
    repos = []
    for rr in [*sorted(set(roots) | set(head_by_repo)), *below_all, *[r for r in from_work if r not in below_all]]:
        log = [x for x in git(["log", "--all", f"--since={a.days} days ago", "--format=%an <%ae>"], rr).split("\n") if x] \
            if os.path.isdir(rr) else []
        repos.append({"repo": rr, "typed_chats": roots.get(rr, 0), "headless_runs": head_by_repo.get(rr, 0),
                      "worked_chats": worked_chats.get(rr, 0),
                      "commits_in_window": len(log), "theirs_in_window": sum(1 for x in log if is_mine(x, me)),
                      "other_authors": dict(Counter(x.split(" <")[0] for x in log if not is_mine(x, me)).most_common(3)),
                      "first_commit_theirs": first_commit_is_mine(rr, me) if log else False,
                      "authors_here": False,   # set below, once the row is final
                      **({"found_under": in_repo[rr]} if rr in in_repo else {}),
                      **({"found_from_work": True} if rr in from_work and rr not in in_repo and rr not in head_by_repo else {}),
                      **({"not_a_repo": True} if rr in below else {})})
    repos = one_row_per_repo(repos)
    for r in repos:
        r["authors_here"] = authors_in(r["theirs_in_window"], r["commits_in_window"], r["first_commit_theirs"])
    sort_repos(repos)
    temp = [r for r in repos if r["repo"].startswith("temp: ")]
    repos = [r for r in repos if not r["repo"].startswith("temp: ")]
    if temp:
        repos.append({"repo": f"temporary folders ({len(temp)})", "typed_chats": sum(r["typed_chats"] for r in temp),
                      "headless_runs": sum(r["headless_runs"] for r in temp), "commits_in_window": 0, "theirs_in_window": 0,
                      "by_name": dict(Counter({r["repo"][6:]: r["headless_runs"] for r in temp}).most_common(8))})
    T["repos"] = round(time.monotonic() - t, 1)

    # programs the agent runs, resolved and attributed
    t = time.monotonic()
    # the repos this person's sessions ran in, and the ones found below their start folders; never a folder of one laptop's
    scan_roots = [r["repo"] for r in repos if r["typed_chats"] or r["headless_runs"] or r.get("worked_chats") or "found_under" in r]
    scan_roots += own_marketplace_folders()   # their own plugins: read wherever the sessions ran
    scan_roots = list(dict.fromkeys(x for x in scan_roots if os.path.isdir(x)))
    receipts(scan_roots)   # install receipts (Claude Code's plugin list, skills locks) for every ownership question below
    for block in ("typed_chats", "in_helpers"):
        for row in agg["tools"][block]["by_program"][:40]:
            row["where"] = resolve_program(row["what"], me, scan_roots)
    # helper types → the agent file and its author
    agent_files = {}
    for base in [os.path.join(HOME, ".claude", "agents")] + [os.path.join(r, ".claude", "agents") for r in scan_roots] + \
            glob.glob(os.path.join(HOME, ".claude", "plugins", "cache", "*", "*", "*", "agents")):
        for f in glob.glob(os.path.join(base, "*.md")):
            agent_files.setdefault(os.path.basename(f)[:-3], f)
    for row in agg["helpers"]["by_type"]:
        nm = row["type"].split(":")[-1]
        f = agent_files.get(nm)
        if f:
            row["agent_file"] = f
            src = f
            if "/plugins/cache/" in f:
                plug = f.split("/plugins/cache/")[1].split("/")[1]
                cand = [os.path.join(r, "plugins", plug, "agents", nm + ".md") for r in scan_roots]
                src = next((c for c in cand if os.path.isfile(c)), None)
            if src and repo_root(os.path.dirname(src)):
                row["author"] = authorship(src, me, repo_root(os.path.dirname(src)))
    T["resolve"] = round(time.monotonic() - t, 1)

    t = time.monotonic()
    evals = find_evals(scan_roots, me, start)
    T["evals"] = round(time.monotonic() - t, 1)

    # always-loaded instruction files: home + each repo root, size in words now and over time
    t = time.monotonic()
    always = [os.path.join(HOME, ".claude", "CLAUDE.md"), os.path.join(HOME, "CLAUDE.md")]
    for rr in scan_roots:
        for nm in INSTRUCTION_NAMES:
            always.append(os.path.join(rr, nm))
            always.append(os.path.join(rr, ".claude", nm))
    for cwd in cwd_count:
        # a chat in a linked worktree loads the worktree's copy of the repo's files: the main repo's files stand for it
        if cwd and os.path.isdir(cwd) and not in_linked_worktree(cwd):
            for nm in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md"):
                always.append(os.path.join(cwd, nm))
    instr = [x for x in (words_history(p, me) for p in dict.fromkeys(always) if os.path.isfile(p)) if x]
    instr.sort(key=lambda x: -x["words_now"])
    T["instructions"] = round(time.monotonic() - t, 1)

    plans_dir = os.path.join(HOME, ".claude", "plans")
    plan_files = glob.glob(os.path.join(plans_dir, "*.md"))
    plans = {"files": len(plan_files), "written_in_window": sum(1 for p in plan_files if os.path.getmtime(p) >= start),
             "chats_reading_plan_files": len({c for d, v in rd.items() if d.startswith(plans_dir) for c in v[1]}),
             "chats_writing_plan_files": len({c for d, v in wr.items() if d.startswith(plans_dir) for c in v[1]})}

    gh = git_hooks(scan_roots, me)
    dbs = sqlite_stores(find_dbs())

    if proc:
        _, err = proc.communicate()
        if proc.returncode:
            print("anatomy inventory failed:", (err or "")[-400:], file=sys.stderr)
    T["anatomy_wait"] = round(time.monotonic() - t0, 1)
    A = json.load(open(anat_path)) if os.path.exists(anat_path) else {}
    files_part = compact_anatomy(A, me, scan_roots, rd, wr)
    hook_runs = attribute_hook_runs(agg.pop("hook_runs_typed"), agg.pop("hook_runs_headless"), A, me)
    who_raw = agg.pop("who_raw")
    own_hook = hook_ownership(A, me, files_part["plugins"])
    own_rule = rule_ownership(me)
    first_talk, own_start = {}, set()
    for chat, evs in who_raw.pop("ctx_by_chat").items():
        for t_ev, kind, how, key, talks in evs:
            # hook text from a tool call (PreToolUse:Agent…) comes after Claude decided; only text it had before its
            # turn counts: a session-start hook, a prompt hook, a rule file
            if kind == "hook" and how not in ("SessionStart", "UserPromptSubmit"):
                continue
            mine = own_hook(key) if kind == "hook" else own_rule(key)
            if not mine:
                continue
            if kind == "hook" and how == "SessionStart":
                own_start.add(chat)
            if talks and chat not in first_talk:
                first_talk[chat] = t_ev
    who_raw["own_talk_first_t"] = first_talk
    automem_chats = who_raw.pop("automem_chats")
    who_raw["own_start_ctx_chats"] = len(own_start)
    bc = who_raw.pop("block_cmds")
    blocks = {"own": sum(n for c, n in bc.items() if own_hook(c)),
              "others": sum(n for c, n in bc.items() if own_hook(c) is False),
              "unknown": sum(n for c, n in bc.items() if own_hook(c) is None)}
    # A block record that names no command ("PostToolUse:Write") is placed by the hooks set up for that event and tool:
    # all of them the person's → theirs, none → others', a mix or none found → can't say.
    for name, n in who_raw.pop("blocks_by_name").items():
        ev, _, tool = name.partition(":")
        cands = [h["command"][:200] for h in A.get("hooks") or [] if h.get("command") and h.get("event") == ev
                 and matcher_hits(h.get("matcher"), tool)]
        owns = {own_hook(c) for c in cands}
        blocks["own" if owns == {True} else "others" if owns == {False} else "unknown"] += n
    who_raw["blocks"] = blocks
    nested = who_raw.pop("nested_by_chat")
    files = {p for ps in nested.values() for p in ps}
    own_files = {p for p in files if own_rule(p)}
    memo = {}
    who_raw["default_mode_by_cwd"] = {ch.get("cwd"): effective_default_mode(ch.get("cwd"), me, memo) for ch in typed
                                      if any(x.get("i", 0) == 0 and x.get("pm") == "plan" for x in ch.get("msgs", [])[:1])}
    who_raw["nested_own"] = {"chats": sum(1 for ps in nested.values() if own_files & set(ps)), "files": len(own_files),
                             "files_all": len(files), "files_gone": sum(1 for p in files if not os.path.isfile(p)),
                             "files_unknown": sum(1 for p in files if os.path.isfile(p) and own_rule(p) is None)}
    past_work = past_work_reach(typed, own_start, automem_chats)
    launch = claude_launch(me)
    for d in dbs:
        d["referenced_by"] = who_mentions(os.path.basename(d["path"]), files_part["plugins"], scan_roots)
    sched = schedule_logs(scan_roots, start)

    t = time.monotonic()
    own_plugins = {x.get("name") for x in A.get("artifacts") or [] if x.get("kind") == "plugin_manifest" and x.get("origin") == "own"} | \
        {str(p.get("key", "")).partition("@")[0] for p in files_part.get("plugins") or [] if isinstance(p, dict) and p.get("owner") == "theirs"}
    skill_depth = skill_depth_facts(A, recs, start, me, scan_roots, own_plugins - {""})
    T["skill_depth"] = round(time.monotonic() - t, 1)

    probes = {}
    pdir = os.path.join(os.path.dirname(os.path.abspath(a.out)), "problems-2026-11-02")
    for f in glob.glob(os.path.join(pdir, "*.json")):
        nm = os.path.basename(f)[:-5]
        if nm.startswith("v15"):
            continue
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        probes[nm] = {"verdict": d.get("verdict"), "headline": d.get("headline"),
                      **{part: (d.get(part) or {}).get("numbers") for part in ("symptom", "response", "held") if isinstance(d.get(part), dict)}}

    # commands from the transcripts too (any app), per session; history.jsonl alone is the terminal's
    tr = {}
    typed_ids = {c["chat"] for c in typed}
    for r in recs:
        if r["helper"] or (r["ep"] or "").startswith(UNATTENDED) or r["chat"] not in typed_ids:
            continue
        cnt, bare_c = Counter(), Counter()
        for nm, has_args, _ in r["commands"]:
            n = "/" + nm.strip().lstrip("/")[:39]
            cnt[n] += 1
            bare_c[n] += not has_args
        if cnt:
            tr[r["chat"]] = (cnt, os.path.basename(r["cwd"] or "?"), bare_c)
    hist = slash_history(a.days, tr, len(typed_ids))
    versions = Counter(c.get("version") for c in typed if c.get("version"))
    inv = {
        "meta": {"days": a.days, "to": day(t0), "made": t0, "main_log_files": n_main, "helper_log_files": n_help,
                 "versions": [v for v, _ in versions.most_common(3)], "seconds": T,
                 "logs_timed_out": len(logs_timed_out),
                 "could_not_read": files_part.pop("could_not_read") + [
                     {"path": p, "reason": f"session log took over {SESSION_SECONDS:g}s: skipped, couldn't check"} for p in logs_timed_out[:30]]},
        "person": {"identities": sorted(me["emails"]), "names": sorted(me["names"]), "sources": ident["sources"],
                   "not_counted": ident["ask_about"],
                   "most_frequent_author_where_chats_run": me_top.split(" <")[0],
                   "rule": "identities = every email their git config knows (includes too); a file is theirs when most of "
                           "its commits are by them; installed plugin copies are "
                           "resolved to plugins/<name>/ in their repos"},
        "repos": repos, "author_share": AUTHOR_SHARE,
        # where the work happened against where sessions started (11-11: sessions that start in the home folder worked in 6
        # repos, ~400 commits; the page read "built 0"). The Built tab says it when most typed sessions start outside a repo.
        "work_found": {"typed_sessions": len(typed),
                       "start_outside_repo": sum(1 for c in typed if c.get("cwd") and os.path.isdir(c["cwd"])
                                                 and not TMP.match(c["cwd"]) and repo_root(c["cwd"]) is None),
                       "sessions_that_worked_in_a_repo": sum(1 for c in typed if worked_by_session.get(c["chat"])),
                       "repos_worked_in": sum(1 for r in worked_chats if os.path.isdir(r)),
                       "repos_found_only_from_work": sum(1 for r in repos if r.get("found_from_work"))},
        "chats": {"typed_chats": len(typed), "typed_messages": sum(len(c["msgs"]) for c in typed),
                  "by_repo": [[os.path.basename(r), n] for r, n in sorted(roots.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]},
        "chats_logged": agg["chats_logged"], "jobs_watched": agg["jobs_watched"],
        "slash_commands": hist, "slash_commands_in_chats": agg["slash_commands_in_chats"],
        "tools": agg["tools"], "helpers": agg["helpers"], "workflows": agg["workflows"], "skills_used": agg["skills_used"],
        "plan_dialogs": agg["plan_dialogs"], "plans": plans,
        "hook_context": agg["hook_context"], "rules_reaching": agg["rules_reaching"], "compactions": agg["compactions"],
        "headless": agg["headless"], "instruction_edits": agg["instruction_edits"],
        "instruction_files": {"always_loaded": instr, **files_part.pop("instruction_files_all")},
        "skill_depth": skill_depth, "evals": evals, "git_hooks": gh, "databases": dbs, "probes": probes, "hook_runs": hook_runs,
        "schedule_logs": sched, "past_work_reach": past_work, "claude_launch": launch,
        **files_part,
    }
    # Scheduled jobs whose runs keep no session log (`claude -p --no-session-persistence`) leave nothing for a log count to
    # see, so they are never in the headless runs above: their runs come from launchd's own counter, as another unit.
    kept_none = [x for x in (inv.get("launchers") or {}).get("launchd_matching") or [] if x.get("keeps_no_log")]
    inv["headless"]["scheduled_jobs_without_a_log"] = len(kept_none)
    inv["headless"]["scheduled_runs_without_a_log"] = sum(x.get("runs_seen") or 0 for x in kept_none)
    inv["headless"]["scheduled_runs_without_a_log_by_job"] = [
        {"label": x.get("label"), "runs_seen": x.get("runs_seen"), "last_exit": x.get("last_exit"), "seen_by": x.get("seen_by"),
         "output_last_written": x.get("output_last_written")} for x in kept_none]
    inv["who_did_it"] = who_did_it(inv, typed, who_raw, helper_meta(start, {c["chat"] for c in typed}))
    js = json.dumps(inv, ensure_ascii=False, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    # the same secret self-check the file inventory runs
    # patterns built in pieces so this file itself passes the folder's pre-commit secret grep
    bad = re.findall("xox[abcdpers]-[A-Za-z0-9-]{8,}|\\bsk" + "-ant-[A-Za-z0-9_-]{10,}|\\bgh" + "p_[A-Za-z0-9]{16,}|\\beyJhb" +
                     "GciO[A-Za-z0-9_-]{10,}", js)
    if bad:
        print(f"refusing to write: {len(bad)} secret-like strings", file=sys.stderr)
        return 2
    open(os.path.join(a.out, "inventory.json"), "w").write(js)
    md = digest(inv)
    open(os.path.join(a.out, "inventory.md"), "w").write(md)
    print(f"inventory: {len(js) // 1000} kB json, {len(md.split())} words in inventory.md, {time.monotonic() - t0:.0f}s {T} -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
