#!/usr/bin/env python3
"""A stranger's laptop, built fresh for each check: the records Claude Code writes, in their real shapes (copied from
real logs on 2026-11-05, Claude Code 2.1.317-318), and the person's own files.

The stranger is nothing like the laptop the checks were written on:
  - git: a personal email in ~/.gitconfig, a work email only under ~/work/ through [includeIf "gitdir:~/work/"];
    a teammate with more commits than them in the team repo;
  - works from VS Code and the desktop app as well as the terminal; only the terminal writes ~/.claude/history.jsonl;
  - repos under ~/work/ and ~/code/, plain `npm test` / `pytest`, no plugins or folders of anyone in particular.

    fx = FixtureHome()                       # a new empty laptop
    s = fx.session("~/work/shop", entrypoint="claude-vscode")
    s.typed("fix the login bug"); s.command("/compact"); s.call("Bash", {"command": "npm test"}, "ok")
    s.refuse("Edit", {...}); s.interrupt(); s.attachment("skill_mention", skillName="deploy"); s.system("compact_boundary", ...)
    s.subagent("Explore", "find the auth code", by_claude=True, calls=[("Grep", {...}, "...")])
    fx.repo("~/work/shop", {"package.json": "...", "CLAUDE.md": "..."}, by="work")   # commits as the person (work email)
    fx.commit("~/work/shop", {"src/a.ts": "..."}, by="teammate")
    fx.settings("user", {...}) / fx.settings("project", {...}, repo="~/work/shop") / fx.file("~/notes/x.md", "...")
    lt = fx.laptop()                         # base.Laptop over this home
"""
from __future__ import annotations

import calendar
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
# The one moment every made-up laptop lives at: sessions, commits and file times are all derived from it, and the scan of the
# laptop is told it through AGENTIC_SETUP_NOW (clock.py). Never the real clock: a laptop dated from the real clock read a
# different count on a different day (11-12: 20 days of sessions became 21 when the hour moved one session across midnight).
# Noon UTC, so no hour offset a fixture adds (up to 12 h) crosses a day boundary.
NOW = calendar.timegm((2026, 11, 10, 12, 0, 0))
NOW_ISO = "2026-11-10T12:00:00Z"
DAY = 86400
PERSON = ("Dana Stranger", "dana@home.example", "dana@work.example")
TEAMMATE = ("Lee Teammate", "lee@work.example")
VERSION = "2.1.318"


def slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


class Session:
    def __init__(self, fx, cwd, entrypoint, sid=None, start=None, version=VERSION, permission_mode="default"):
        self.fx, self.cwd, self.ep = fx, cwd, entrypoint
        self.sid = sid or str(uuid.uuid4())
        self.t = start or (NOW - 3 * DAY)
        self.version, self.pm = version, permission_mode
        self.parent = None
        d = os.path.join(fx.home, ".claude", "projects", slug(cwd))
        os.makedirs(d, exist_ok=True)
        self.path = os.path.join(d, self.sid + ".jsonl")
        os.makedirs(cwd, exist_ok=True)

    def _tick(self, s=7):
        self.t += s
        return self.t

    def _write(self, rec, path=None):
        rec.setdefault("uuid", str(uuid.uuid4()))
        rec.setdefault("timestamp", iso(self._tick()))
        rec.setdefault("parentUuid", self.parent)
        rec.setdefault("isSidechain", False)
        rec.update({"userType": "external", "entrypoint": self.ep, "cwd": self.cwd, "sessionId": self.sid, "version": self.version})
        with open(path or self.path, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")   # compact, as Claude Code writes
        if path is None:
            self.parent = rec["uuid"]
        return rec

    def _history(self, display):
        if self.ep == "cli":   # only the terminal writes history.jsonl
            with open(os.path.join(self.fx.home, ".claude", "history.jsonl"), "a") as f:
                f.write(json.dumps({"display": display, "pastedContents": {}, "timestamp": int(self.t * 1000),
                                    "project": self.cwd, "sessionId": self.sid}, separators=(",", ":")) + "\n")

    def typed(self, text, images=0, permission_mode=None):
        """A message the person types; in a headless run (entrypoint sdk-*) the prompt a program sent, as Claude Code
        writes it there: promptSource "sdk", no origin (checked against real logs, 11-05)."""
        content = text if not images else [{"type": "text", "text": text}] + [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBOR"}}] * images
        if self.ep.startswith("sdk"):
            return self._write({"type": "user", "message": {"role": "user", "content": content}, "promptSource": "sdk"})
        # The apps a person types into (VS Code, desktop, JetBrains) drive Claude Code through the Agent SDK, so Claude Code
        # writes the person's own message as promptSource "sdk", the same as a script's; only the terminal writes "typed".
        # Checked against real logs 11-09: a VS Code 2.1.318 session, and 16 desktop-app messages (14 with origin human).
        source = "typed" if self.ep == "cli" else "sdk"
        r = self._write({"type": "user", "message": {"role": "user", "content": content}, "permissionMode": permission_mode or self.pm,
                         "origin": {"kind": "human"}, "promptSource": source, "promptId": str(uuid.uuid4())})
        self._history(text)
        return r

    def command(self, name, args=""):
        """A slash command as Claude Code records it in the transcript (and in history.jsonl from the terminal)."""
        n = name if name.startswith("/") else "/" + name
        r = self._write({"type": "user", "message": {"role": "user", "content":
                         f"<command-name>{n}</command-name>\n            <command-message>{n[1:]}</command-message>\n            <command-args>{args}</command-args>"}})
        self._history((n + " " + args).strip())
        return r

    def command_expansion(self, name, text, args=""):
        """A slash command of a skill or a plugin, as Claude Code records it in the transcript: the command record starting with
        <command-message> (checked against real logs, 11-06: 254 of 465 command records), then one isMeta user record holding what
        the command expanded to. History gets the line from the terminal, as for a typed command."""
        n = name if name.startswith("/") else "/" + name
        self._write({"type": "user", "message": {"role": "user", "content":
                     f"<command-message>{n[1:]}</command-message>\n<command-name>{n}</command-name>\n<command-args>{args}</command-args>"}})
        self._history((n + " " + args).strip())
        return self._write({"type": "user", "isMeta": True, "message": {"role": "user", "content": [{"type": "text", "text": text}]}})

    def call(self, name, inp, result="ok", is_error=False, denial=None, sidechain=False, path=None, model="claude-sonnet-5"):
        """One tool call and its result. Returns the tool_use id."""
        tid = "toolu_" + uuid.uuid4().hex[:24]
        self._write({"type": "assistant", "isSidechain": sidechain, "message": {"model": model, "id": "msg_" + uuid.uuid4().hex[:20],
                     "type": "message", "role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}, path)
        rec = {"type": "user", "isSidechain": sidechain, "message": {"role": "user", "content": [
               {"type": "tool_result", "content": result, "is_error": is_error, "tool_use_id": tid}]},
               "toolUseResult": ("Error: " + result) if is_error else result}
        if denial:
            rec["toolDenialKind"] = denial
        self._write(rec, path)
        return tid

    def background_call(self, command, task_id):
        """A Bash call with run_in_background, as Claude Code records it: the result says the command runs in the background, and
        the record carries toolUseResult.backgroundTaskId (checked against real logs, 11-06). Returns the task's output file."""
        out = f"/private/tmp/claude-501/-fixture/{self.sid}/tasks/{task_id}.output"
        tid = "toolu_" + uuid.uuid4().hex[:24]
        self._write({"type": "assistant", "message": {"model": "claude-sonnet-5", "id": "msg_" + uuid.uuid4().hex[:20], "type": "message", "role": "assistant",
                     "content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command, "run_in_background": True}}]}})
        self._write({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "is_error": False, "content":
                     f"Command running in background with ID: {task_id}. Output is being written to: {out}. You will be notified when it completes."}]},
                     "toolUseResult": {"stdout": "", "stderr": "", "interrupted": False, "backgroundTaskId": task_id}})
        return out

    def task_notification(self, task_id, status, queued=False):
        """Claude Code tells the session a background task ended: a user message, or a queued_command attachment when it arrived mid-turn."""
        text = (f"<task-notification>\n<task-id>{task_id}</task-id>\n<tool-use-id>toolu_x</tool-use-id>\n<output-file>/private/tmp/x/{task_id}.output</output-file>\n"
                f"<status>{status}</status>\n<summary>Background command \"job\" {status} (exit code 0)</summary>\n</task-notification>")
        if queued:
            return self.attachment("queued_command", prompt=text, commandMode="task-notification")
        return self._write({"type": "user", "message": {"role": "user", "content": text}})

    def refuse(self, name, inp):
        """The person refuses a step (Claude Code's own record: toolDenialKind user-rejected)."""
        return self.call(name, inp, "The user doesn't want to proceed with this tool use. The tool use was rejected "
                         "(eg. if it was a file edit, the new_string was NOT written to the file). STOP what you are doing "
                         "and wait for the user to tell you how to proceed.", is_error=True, denial="user-rejected")

    def interrupt(self, for_tool=False):
        return self._write({"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "[Request interrupted by user for tool use]" if for_tool else "[Request interrupted by user]"}]}})

    def attachment(self, type_, **data):
        return self._write({"type": "attachment", "attachment": {"type": type_, **data}})

    def system(self, subtype, **fields):
        return self._write({"type": "system", "subtype": subtype, "level": "info", **fields})

    def record(self, type_, **fields):
        """Any other record type Claude Code writes (e.g. 'custom-title', 'summary'), as-is."""
        return self._write({"type": type_, **fields})

    def subagent(self, agent_type, description, by_claude=True, is_fork=False, stopped_by_user=False, background=False,
                 worktree=False, calls=(), team=None):
        """A sub-agent: its meta file and its own log. by_claude=True: Claude started it (the meta carries toolUseId);
        else the person did (/subtask, a fork, an @-mention). calls: [(name, input, result)]."""
        aid = "a" + uuid.uuid4().hex[:16]
        d = os.path.join(os.path.dirname(self.path), self.sid, "subagents")
        os.makedirs(d, exist_ok=True)
        tid = None
        if by_claude:
            tid = self.call("Agent", {"subagent_type": agent_type, "description": description, "prompt": description,
                                      **({"run_in_background": True} if background else {})}, "done")
        meta = {"agentType": agent_type, "description": description, "spawnDepth": 1, "finished": True,
                **({"toolUseId": tid} if tid else {}), **({"isFork": True} if is_fork else {}),
                **({"stoppedByUser": True} if stopped_by_user else {}), **({"spawnedWithWorktree": True} if worktree else {}),
                **({"teamName": team} if team else {})}
        with open(os.path.join(d, f"agent-{aid}.meta.json"), "w") as f:
            json.dump(meta, f)
        p = os.path.join(d, f"agent-{aid}.jsonl")
        for name, inp, res in calls:
            self.call(name, inp, res, sidechain=True, path=p)
        return aid


class FixtureHome:
    def __init__(self, keep=False, root=None):
        # outside any repo: inside this checkout, git would find the enclosing repo from a plain folder and the
        # stranger would "have" its settings and .mcp.json (found by group 4, 11-05). root: put it elsewhere; the
        # whole-tool test needs one outside the system temp folder, which the scan leaves out on purpose
        if root:
            os.makedirs(root, exist_ok=True)
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="fixture-home-", dir=root))
        self.keep = keep
        os.makedirs(os.path.join(self.home, ".claude", "projects"))
        name, home_mail, work_mail = PERSON
        self.file("~/.gitconfig", f'[user]\n\tname = {name}\n\temail = {home_mail}\n[includeIf "gitdir:~/work/"]\n\tpath = ~/.gitconfig-work\n')
        self.file("~/.gitconfig-work", f"[user]\n\temail = {work_mail}\n")

    def p(self, path):
        return os.path.join(self.home, path[2:]) if path.startswith("~/") else path

    def file(self, path, text):
        p = self.p(path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        return p

    def session(self, cwd="~/work/shop", entrypoint="claude-vscode", **kw):
        return Session(self, self.p(cwd), entrypoint, **kw)

    def _git(self, repo, *args, who=None):
        env = {**os.environ, "HOME": self.home, "GIT_CONFIG_NOSYSTEM": "1"}
        # a commit is dated by the laptop's moment, one second later than the one before it, unless the caller dated it
        self._commits = getattr(self, "_commits", 0) + 1
        env.setdefault("GIT_AUTHOR_DATE", f"{NOW + self._commits} +0000")
        env.setdefault("GIT_COMMITTER_DATE", f"{NOW + self._commits} +0000")
        if who:
            env.update({"GIT_AUTHOR_NAME": who[0], "GIT_AUTHOR_EMAIL": who[1], "GIT_COMMITTER_NAME": who[0], "GIT_COMMITTER_EMAIL": who[1]})
        subprocess.run(["git", *args], cwd=repo, check=True, env=env, capture_output=True)

    def who(self, by):
        name, home_mail, work_mail = PERSON
        return {"work": (name, work_mail), "home": (name, home_mail), "teammate": TEAMMATE}.get(by) or (by.split("@")[0], by)

    def repo(self, path, files=None, by="work", message="start"):
        r = self.p(path)
        os.makedirs(r, exist_ok=True)
        if not os.path.isdir(os.path.join(r, ".git")):
            self._git(r, "init", "-q", "-b", "main")
        if files:
            self.commit(path, files, by=by, message=message)
        return r

    def commit(self, path, files, by="work", message="change"):
        r = self.p(path)
        for rel, text in files.items():
            self.file(os.path.join(r, rel), text)
        self._git(r, "add", "-A")
        self._git(r, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", message, who=self.who(by))
        return r

    def settings(self, scope, data, repo=None):
        p = {"user": "~/.claude/settings.json", "user-local": "~/.claude/settings.local.json"}.get(scope)
        if scope in ("project", "project-local"):
            p = os.path.join(self.p(repo), ".claude", "settings.json" if scope == "project" else "settings.local.json")
        if scope == "managed":
            p = os.path.join(self.home, "_system", "Library/Application Support/ClaudeCode/managed-settings.json")
        return self.file(p, json.dumps(data, indent=1))

    def settle(self):
        """Give every file of this laptop a modification time derived from NOW, in the order they had (a millisecond apart): files
        written while the fixture was built carry the real clock, and anything comparing a file's age with the scan's moment
        would read a different age every day."""
        files = []
        for dp, dns, fns in os.walk(self.home):
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    files.append((os.lstat(p).st_mtime, p))
                except OSError:
                    pass
        for rank, (_, p) in enumerate(sorted(files)):
            try:
                os.utime(p, (NOW + rank / 1000, NOW + rank / 1000), follow_symlinks=False)
            except OSError:
                pass

    def laptop(self, days=30):
        # the same base module the checks import (not a second copy), so what the checks ask of it is seen by evaluate.py
        import sys
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import base as b
        self.settle()
        return b.Laptop(self.home, days=days, now=NOW, cache=False, system_root=os.path.join(self.home, "_system"))

    def close(self):
        if not self.keep:
            shutil.rmtree(self.home, ignore_errors=True)


FIXTURES = {}


def fixture(pid, level, i):
    """Register the stranger-laptop case for a check: fn(fx) plants records and returns what a right check must read,
    {"value": n} (exact) or {"min": n} / {"max": n}. Plant one trap the old rule would miscount."""
    def deco(fn):
        FIXTURES[(pid, level, i)] = fn
        return fn
    return deco
