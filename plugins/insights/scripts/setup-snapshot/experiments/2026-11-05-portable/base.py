#!/usr/bin/env python3
"""What any laptop can tell, read the same way on every laptop: the base the rewritten checks stand on (2026-11-05).

The ask: "if something can't be detected by heuristics on somebody else's machine then we're doomed". The 155 checks
the kept practices need were written on one laptop; the review in the practices page found most of them tuned to him or
guessing. Each check is rewritten in checks_*.py on top of this file, which allows only two kinds of evidence:

  1. What Claude Code writes the same way for everyone (session logs, history.jsonl, sub-agent meta files, settings).
     One pass, every copy counted once: forked and resumed sessions copy records with the same uuid and tool_use id.
     Every interactive entrypoint counts (terminal, VS Code, JetBrains, desktop), not only the terminal.
  2. What the person's own files say: who they are (every email their git config knows: identity.py), which files
     are theirs (most of a file's commits by them), the repos they work in (from their own sessions, never a list),
     and the commands their repos declare as checks (package.json scripts, Makefile/justfile, CI steps, pre-commit).

No word lists on typed text, no names of anyone's tools, no folder layout of one laptop. A check that can't be built
on these says so ("can't tell") rather than guess.

    lt = Laptop(home)                      # the real laptop: Laptop(os.path.expanduser("~"))
    lt.typed, lt.commands, lt.calls, lt.attachments, lt.systems, lt.others, lt.meta, lt.history, lt.sessions
    lt.identity, lt.repos, lt.authorship(path), lt.settings_files(), lt.instruction_files(), lt.declared_checks(repo)

Standard library only (members have nothing else installed).
"""
from __future__ import annotations

import glob
import hashlib
import importlib.util
import json
import os
import pickle
import re
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
IDENTITY = os.path.join(os.path.dirname(HERE), "2026-11-02-evidence", "identity.py")
OWNERSHIP = os.path.join(os.path.dirname(HERE), "2026-11-02-evidence", "ownership.py")   # the one ownership rule (his, 11-05)


def _code_sig():
    """The code a cache was built by. Both caches are keyed on it as well as on home and window: keyed on home and window
    only, a cache built by older rules was reused after the rules changed (found 11-06: a repo's declared checks read 84
    patterns from the old rules against 26 from the current ones), so a reading moved the day the cache was rebuilt."""
    h = hashlib.sha1()
    for f in (os.path.abspath(__file__), IDENTITY, OWNERSHIP):
        try:
            with open(f, "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"-")
    return h.hexdigest()[:12]


import threading

# Apps a person types into (VS Code, desktop, JetBrains) drive Claude Code through the Agent SDK, so Claude Code
# writes the person's own message as promptSource "sdk", like a script's. In their sessions "sdk" is the person.
PERSON_APPS = ("claude-vscode", "claude-desktop", "claude-jetbrains")

# identity.py and ownership.py read HOME when they load, so a Laptop for another home loads them with HOME pointed there
# for a moment. One lock for every such moment: two threads swapping at once restored each other's value and left HOME
# on a fixture home, so a later real run wrote its cache there (11-06).
_HOME_SWAP = threading.RLock()


class _home_as:
    def __init__(self, home):
        self.home = home

    def __enter__(self):
        _HOME_SWAP.acquire()
        self.old = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        return self.old

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old
        _HOME_SWAP.release()


CACHE_HOURS = 6   # live sessions keep adding files; a few hours stale is fine. Age counts from when the cache was built.
_CACHE_FILE = re.compile(r"^[0-9a-f]{16}\.(state\.)?pkl(\.\d+\.tmp)?$")   # <sig>.pkl, <sig>.state.pkl, and their temp files


def _prune_cache(folder):
    """Delete the cache files no run will read again: every one last written more than CACHE_HOURS ago (a state file's
    build time is never later than its write time, so it has expired too), and the temp files a stopped run left.
    Both caches are keyed on the code, so each version of the tool writes a new pair (~1.7 GB on a laptop with a busy
    month of logs) and, until 11-11, none was ever deleted: 24 GB on one laptop after one week of changes,
    and one more pair per release on every member's. Only names this cache writes are touched."""
    cutoff = time.time() - CACHE_HOURS * 3600
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if not _CACHE_FILE.match(name):
            continue
        p = os.path.join(folder, name)
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:   # another run removed or replaced it first
            pass
TYPED = ("typed", "queued")
INJECTED = ("<command-name>", "<command-message>", "<local-command", "<task-notification>", "<teammate-message",
            "<system-reminder>", "<cross-session-message", "Caveat:", "[Request interrupted")
NOT_THE_PERSON = ("Another Claude session sent a message", "[SYSTEM NOTIFICATION")
INTERRUPT = "[Request interrupted by user"
EXPANSION_CUT = 30000        # characters kept of what a slash command expands to (a skill's whole SKILL.md)
CUT_IN, CUT_OUT = 1500, 600   # characters kept of a tool call's input and of its result
# what makes a declared command a check (its role, in the names every ecosystem uses), and what never is one: setting
# up, installing, syncing, building or starting is not checking the work (the skeptic, 11-05: npm ci, uv sync, uv build)
CHECK_ROLE = re.compile(r"(?i)(^|[\W_])(test|tests|spec|e2e|lint|check|checks|typecheck|type-check|tsc|verify|validate|mypy|pyright|ruff|eslint|clippy|vet|pytest|jest|vitest|rspec|phpunit)([\W_]|$)")
NOT_A_CHECK = re.compile(r"(?i)\b(npm ci|npm install|pnpm install|yarn install|uv sync|pip install|poetry install|bundle install|go mod download)\b")


def epoch(ts):
    try:
        return datetime.fromisoformat(str(ts)[:23] + "+00:00").timestamp()
    except ValueError:
        return None


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _read_file(args):
    """One session file -> its records, compact. Dedup happens in the merge (Laptop._index)."""
    path, start, sidechain_file = args
    out = {"typed": [], "commands": [], "calls": [], "results": [], "attachments": [], "systems": [], "others": [],
           "assist": [], "headless": [], "expansions": [], "sess": {}}
    pending = None   # the slash command whose expansion record may come next
    try:
        fh = open(path, "rb")
    except OSError:
        return out
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict):
                continue
            t = epoch(r.get("timestamp")) if r.get("timestamp") else None
            sid = r.get("sessionId") or os.path.basename(path)[:-6]
            s = out["sess"].setdefault(sid, {"cwd": None, "ep": None, "version": None, "t0": None, "t1": None})
            if r.get("entrypoint") and not s["ep"]:
                s["ep"] = r["entrypoint"]
            if r.get("cwd") and not s["cwd"]:
                s["cwd"] = r["cwd"]
            if r.get("version") and not s["version"]:
                s["version"] = r["version"]
            if t is None or t < start:
                continue
            s["t0"] = min(s["t0"] or t, t); s["t1"] = max(s["t1"] or t, t)
            uid = r.get("uuid")
            sc = bool(r.get("isSidechain")) or sidechain_file
            kind = r.get("type")
            base = {"s": sid, "t": t, "u": uid, "sc": sc}
            if kind == "assistant":
                pending = None
                m = r.get("message") or {}
                use = m.get("usage") if isinstance(m.get("usage"), dict) else None
                blocks = m.get("content") if isinstance(m.get("content"), list) else []
                out["assist"].append({**base, "mid": m.get("id"), "model": m.get("model"), "effort": r.get("effort"),
                                      "usage": (use.get("input_tokens") or 0, use.get("cache_creation_input_tokens") or 0,
                                                use.get("cache_read_input_tokens") or 0) if use else None,
                                      "agents": [b.get("id") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"
                                                 and b.get("name") in ("Agent", "Task")]})
                for b in (r.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        out["calls"].append({**base, "id": b.get("id"), "name": b.get("name"),
                                             "input": json.dumps(b.get("input"), ensure_ascii=False)[:CUT_IN]})
                continue
            if kind == "attachment":
                a = r.get("attachment") or {}
                if isinstance(a, dict):
                    out["attachments"].append({**base, "type": a.get("type"), "data": json.dumps(a, ensure_ascii=False)[:CUT_OUT]})
                continue
            if kind == "system":
                out["systems"].append({**base, "subtype": r.get("subtype"),
                                       "data": json.dumps({k: v for k, v in r.items() if k not in ("message",)}, ensure_ascii=False)[:CUT_OUT]})
                continue
            if kind != "user":
                if kind not in ("progress",):
                    out["others"].append({**base, "type": kind, "data": json.dumps(r, ensure_ascii=False)[:CUT_OUT]})
                continue
            content = (r.get("message") or {}).get("content")
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                pending = None
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        body = text_of(b.get("content")) or str(b.get("content") or "")
                        out["results"].append({"id": b.get("tool_use_id"), "err": bool(b.get("is_error")),
                                               "out": body[:CUT_OUT], "denial": r.get("toolDenialKind")})
                continue
            st = text_of(content).lstrip()
            if sc:
                continue   # a sub-agent's own prompts are not the person's
            if st.startswith(("<command-name>", "<command-message>")):
                # Claude Code writes the command record, then (for a skill or a plugin command) one isMeta user record holding what
                # the command expanded to. The record starts with <command-name> or with <command-message>; checked on real logs 11-06
                i0 = st.find("<command-name>")
                pending = st[i0 + 14:st.find("</command-name>")].strip() if i0 >= 0 else None
            elif r.get("isMeta") and pending:
                out["expansions"].append({**base, "cmd": pending, "text": st[:EXPANSION_CUT]})
            else:
                pending = None
            if st.startswith(INTERRUPT):
                out["others"].append({**base, "type": "interrupt", "data": st[:120]})
                continue
            if st.startswith("<command-name>"):
                name = st[len("<command-name>"):st.find("</command-name>")]
                a0 = st.find("<command-args>")
                args = st[a0 + 14:st.find("</command-args>")] if a0 >= 0 else ""
                out["commands"].append({**base, "name": name.strip(), "args": args[:300], "src": "transcript"})
                continue
            if st.startswith("<command-message>"):
                # the other shape of a command record (skills and plugin commands; 254 of 465 on one laptop): <command-message> first, <command-name>
                # after it, then the isMeta expansion read above. The person's own typing only: not a headless run (entrypoint sdk-*), not
                # a command a plugin or a hook started (origin kind other than human). Same fields as the first shape, `shape` tells them apart.
                i0 = st.find("<command-name>")
                if i0 >= 0 and not (s["ep"] or "").startswith("sdk") and (r.get("origin") or {}).get("kind") in (None, "human"):
                    name = st[i0 + 14:st.find("</command-name>")]
                    a0 = st.find("<command-args>")
                    args = st[a0 + 14:st.find("</command-args>")] if a0 >= 0 else ""
                    out["commands"].append({**base, "name": name.strip(), "args": args[:300], "src": "transcript", "shape": "message"})
                continue
            if r.get("isMeta") or r.get("isCompactSummary"):
                continue
            ps = r.get("promptSource")
            if ps == "sdk" and r.get("entrypoint") in PERSON_APPS and (r.get("origin") or {}).get("kind") in (None, "human"):
                ps = "typed"   # the person, typing in VS Code / desktop / JetBrains (see PERSON_APPS)
            if ps == "sdk":   # a headless run's prompt (claude -p, the Agent SDK): a program's, never the person typing
                out["headless"].append({**base, "text": st[:2000]})
                continue
            if ps is not None and ps not in TYPED:
                continue
            if ps is None and (any(st.startswith(m) for m in INJECTED) or (r.get("origin") or {}).get("kind") not in (None, "human")):
                continue
            if not st or st.startswith(NOT_THE_PERSON):
                continue
            if st.startswith("/") and " " not in st.strip() and len(st.strip()) < 40:
                out["commands"].append({**base, "name": st.strip(), "args": "", "src": "transcript"})
                continue
            out["typed"].append({**base, "text": st[:2000], "pm": r.get("permissionMode"),
                                 "images": sum(1 for b in content if isinstance(b, dict) and b.get("type") == "image") if isinstance(content, list) else 0})
    return out


GREP_SEEN = set()   # every pattern a check has asked for (evaluate.py searches them all up front, before forking)


def _grep_file_many(args):
    """One file read once, searched for several patterns: {pattern: [whole matching lines]}."""
    path, patterns = args
    try:
        with open(path, "rb") as fh:
            buf = fh.read()
    except OSError:
        return {}
    out = {}
    for pat in patterns:
        lines, last = [], -1
        for m in re.finditer(pat, buf):
            a = buf.rfind(b"\n", 0, m.start()) + 1
            if a <= last:
                continue
            b = buf.find(b"\n", m.end())
            lines.append(buf[a:len(buf) if b < 0 else b])
            last = a
        if lines:
            out[pat] = lines
    return out


class Laptop:
    """One laptop's Claude Code records and the person's own files. home: the home folder to read (a fixture's, or ~)."""

    def __init__(self, home=None, days=30, now=None, cache=True, system_root=None):
        self.home = os.path.realpath(home or os.path.expanduser("~"))
        self.claude = os.path.join(self.home, ".claude")
        self.days, self.now = days, now or time.time()
        self.start = self.now - days * 86400
        self.system_root = system_root   # where managed settings live; None = the real system paths
        self._cache = cache
        self._idn = self._repos = None
        # filled by _index(): each a list of dicts with s (session id), t (epoch), sc (from a sub-agent's log)
        self.typed: list = []; self.commands: list = []; self.calls: list = []; self.attachments: list = []
        self.systems: list = []; self.others: list = []; self.meta: list = []; self.history: list = []
        self.expansions: list = []  # what a slash command expanded to (the isMeta record after its command record): s, t, cmd, text
        self.headless: list = []  # prompts of headless runs (promptSource "sdk"): s, t, text. Never in typed.
        self.assist: list = []   # one per assistant message: s, t, u, sc, mid, model, effort, usage (in, cache write, cache read), agents
        self.sessions: dict = {}
        self._index()

    # ------------------------------------------------------------------ Claude Code's records
    def _files(self):
        main, side = [], []
        for f in glob.glob(os.path.join(self.claude, "projects", "*", "*.jsonl")):
            try:
                if os.path.getmtime(f) >= self.start:
                    main.append(f)
            except OSError:
                pass
        for f in glob.glob(os.path.join(self.claude, "projects", "*", "*", "subagents", "*.jsonl")):
            try:
                if os.path.getmtime(f) >= self.start:
                    side.append(f)
            except OSError:
                pass
        key = lambda f: os.path.getmtime(f)
        return sorted(main, key=key), sorted(side, key=key)

    def _index(self):
        main, side = self._files()
        # one cache per home, window and code, reused for CACHE_HOURS
        cpath = self._cache_base() + ".pkl"
        if self._cache and os.path.exists(cpath) and time.time() - os.path.getmtime(cpath) < CACHE_HOURS * 3600:
            with open(cpath, "rb") as f:
                self.__dict__.update(pickle.load(f))
            self.load_state()
            return
        jobs = [(f, self.start, False) for f in main] + [(f, self.start, True) for f in side]
        if len(jobs) > 40:
            with ProcessPoolExecutor() as ex:
                parts = list(ex.map(_read_file, jobs, chunksize=8))
        else:
            parts = [_read_file(j) for j in jobs]
        seen_u, seen_call = set(), set()
        idx = {"typed": [], "commands": [], "calls": [], "attachments": [], "systems": [], "others": [], "assist": [], "headless": [], "expansions": [], "sessions": {}}
        results = {}
        for (path, _, side_f), p in zip(jobs, parts):
            for sid, s in p["sess"].items():
                cur = idx["sessions"].setdefault(sid, {"cwd": None, "ep": None, "version": None, "t0": None, "t1": None, "files": []})
                for k in ("cwd", "ep", "version"):
                    cur[k] = cur[k] or s[k]
                if s["t0"]:
                    cur["t0"] = min(cur["t0"] or s["t0"], s["t0"]); cur["t1"] = max(cur["t1"] or s["t1"], s["t1"])
                cur["files"].append(path)
            for k in ("typed", "commands", "attachments", "systems", "others", "assist", "headless", "expansions"):
                for x in p[k]:
                    if x.get("u"):
                        if x["u"] in seen_u:
                            continue
                        seen_u.add(x["u"])
                    idx[k].append(x)
            for x in p["calls"]:
                if x["id"] in seen_call:
                    continue
                seen_call.add(x["id"])
                idx["calls"].append(x)
            for r in p["results"]:
                old = results.get(r["id"])
                if old is None or (r["err"] and not old["err"]):
                    results[r["id"]] = r
        for c in idx["calls"]:
            r = results.get(c["id"])
            c["err"], c["out"], c["denial"] = (r["err"], r["out"], r["denial"]) if r else (None, None, None)
        for k in idx:
            if isinstance(idx[k], list):
                idx[k].sort(key=lambda x: x.get("t") or 0)
        idx["meta"] = self._read_meta()
        idx["history"] = self._read_history()
        self.__dict__.update(idx)
        if self._cache:
            os.makedirs(os.path.dirname(cpath), exist_ok=True)
            _prune_cache(os.path.dirname(cpath))   # before the write: a nearly full disk gets the space back first
            tmp = cpath + f".{os.getpid()}.tmp"
            with open(tmp, "wb") as f:
                pickle.dump(idx, f)
            os.replace(tmp, cpath)

    def _read_meta(self):
        out = []
        for f in glob.glob(os.path.join(self.claude, "projects", "*", "*", "subagents", "*.meta.json")):
            try:
                if os.path.getmtime(f) < self.start:
                    continue
                m = json.load(open(f))
                m["_session"] = os.path.basename(os.path.dirname(os.path.dirname(f)))
                m["_file"] = f
                out.append(m)
            except (OSError, ValueError):
                continue
        return out

    def _read_history(self):
        """history.jsonl: what was typed in the terminal (the desktop app and IDEs don't write it)."""
        out = []
        p = os.path.join(self.claude, "history.jsonl")
        if not os.path.exists(p):
            return out
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = (r.get("timestamp") or 0) / 1000
            if t >= self.start:
                out.append({"t": t, "display": (r.get("display") or "")[:500], "project": r.get("project"), "s": r.get("sessionId")})
        return out

    def grep(self, pattern):
        """Every whole record (parsed) in the window's session files whose line matches `pattern` (a regex, str or bytes),
        each uuid once, with _sid (session), _sc (from a sub-agent's log), _f (file). For the details the index keeps short
        or drops (full attachments, records Claude Code writes without a timestamp). One parallel pass per pattern,
        remembered on this Laptop; checks share it instead of re-reading the logs."""
        memo = self.__dict__.setdefault("_grep_memo", {})
        key = pattern if isinstance(pattern, bytes) else pattern.encode()
        GREP_SEEN.add(key)
        if key in memo:
            return memo[key]
        self.grep_many([key])
        return memo[key]

    def grep_many(self, patterns):
        """Search the logs for every pattern not yet searched, in one parallel pass (each file read once)."""
        memo = self.__dict__.setdefault("_grep_memo", {})
        todo = [p if isinstance(p, bytes) else p.encode() for p in patterns]
        todo = [p for p in dict.fromkeys(todo) if p not in memo]
        if not todo:
            return
        GREP_SEEN.update(todo)
        files = sorted({f for s in self.sessions.values() for f in s.get("files") or []})
        import multiprocessing
        if len(files) > 40 and not multiprocessing.current_process().daemon:
            with ProcessPoolExecutor() as ex:
                parts = list(ex.map(_grep_file_many, [(f, todo) for f in files], chunksize=16))
        else:
            parts = [_grep_file_many((f, todo)) for f in files]
        for key in todo:
            memo[key] = self._parse_grep(files, [part.get(key, []) for part in parts])

    def _parse_grep(self, files, per_file):
        out, seen = [], set()
        for f, lines in zip(files, per_file):
            side = os.sep + "subagents" + os.sep in f
            for line in lines:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                t = epoch(r["timestamp"]) if r.get("timestamp") else None
                if t is not None and t < self.start:
                    continue
                u = r.get("uuid")
                if u:
                    if u in seen:
                        continue
                    seen.add(u)
                r["_sid"] = r.get("sessionId") or os.path.basename(f)[:-6]
                r["_sc"] = bool(r.get("isSidechain")) or side
                r["_f"] = f
                out.append(r)
        return out

    STATE = ("_repos", "_idn", "_auth_memo", "_git_memo", "_grep_memo", "_instr_memo", "_decl_memo", "_top_memo")

    def _cache_base(self):
        sig = hashlib.sha1(json.dumps([self.home, self.days, _code_sig()]).encode()).hexdigest()[:16]
        return os.path.join(os.path.expanduser("~"), ".cache", "setup-snapshot", "portable", sig)

    def _state_path(self):
        return self._cache_base() + ".state.pkl"

    def load_state(self):
        """The shared state prewarm saved, while younger than CACHE_HOURS from when it was first built (`_state_t0`; the
        file's own time moves on every save, so by it a cache in daily use never expired)."""
        p = self._state_path()
        if self._cache and os.path.exists(p):
            with open(p, "rb") as f:
                st = pickle.load(f)
            if time.time() - (st.get("_state_t0") or 0) < CACHE_HOURS * 3600:
                self.__dict__.update(st)
                if self._repos is not None:   # a folder deleted since the state was saved (a removed worktree, a temp
                    self._repos = [r for r in self._repos if os.path.isdir(r)]   # folder) is no repo of theirs any more
                return True
        return False

    def prewarm(self, patterns=()):
        """Everything checks share, done once before they run: repos, identity, each repo's authors and declared checks,
        rule files, and every log search the checks ask for (one pass). Kept with the cache for 6 hours."""
        from concurrent.futures import ThreadPoolExecutor
        self.repos; self.identity
        self._ownership()   # install receipts (asks the claude program once): before the fork, so no worker asks again
        with ThreadPoolExecutor(8) as ex:   # git runs in its own process: threads overlap the waiting
            list(ex.map(self._authors, self.repos))
            list(ex.map(self.declared_checks, self.repos))
        for r in self.repos:
            self.declared_rx(r)
        self.instruction_files()
        self.grep_many(patterns)
        if self._cache:   # atomic: several runs may share the cache
            tmp = self._state_path() + f".{os.getpid()}.tmp"
            with open(tmp, "wb") as f:
                st = {k: self.__dict__.get(k) for k in self.STATE if self.__dict__.get(k) is not None}
                st["_state_t0"] = self.__dict__.get("_state_t0") or time.time()   # kept across saves: age is from the build
                pickle.dump(st, f)
            os.replace(tmp, self._state_path())

    def interactive(self, sid):
        """A session the person worked in (any interactive entrypoint), not a headless run (sdk-*)."""
        ep = (self.sessions.get(sid) or {}).get("ep") or ""
        return not ep.startswith("sdk")

    def all_commands(self):
        """Slash commands, each name from ONE source (11-06: /clear read 67 in one place and 134 in another): the session
        logs, which record every command from any app, in the session the command ran in (a /clear or /branch lands in the NEW
        session, while history.jsonl keys it to the session left behind, so adding the two counted it twice); terminal history
        lines only for a name the logs never record in the window (/resume, /exit). The inventory's slash_history uses the same
        rule. Each: {s, t, name, args, src}."""
        in_logs = {c["name"] for c in self.commands}
        out = list(self.commands)
        for h in self.history:
            d = h["display"].strip()
            if not d.startswith("/"):
                continue
            name, _, args = d.partition(" ")
            if name in in_logs:
                continue
            out.append({"s": h["s"], "t": h["t"], "name": name, "args": args[:300], "src": "history"})
        return sorted(out, key=lambda c: c["t"])

    # ------------------------------------------------------------------ the person's own files
    @property
    def identity(self):
        """Every email the person's git config knows (identity.py) and their names. Never the busiest author."""
        if self._idn is None:
            spec = importlib.util.spec_from_file_location("identity_portable", IDENTITY)
            assert spec and spec.loader
            m = importlib.util.module_from_spec(spec)
            repos = [__import__("pathlib").Path(r) for r in self.repos]   # outside the swap: it runs git
            with _home_as(self.home):
                spec.loader.exec_module(m)
                m.HOME = __import__("pathlib").Path(self.home)
                got = m.collect(repos)
            self._idn = {"emails": set(got["emails"]), "names": set(m.NAMES)}
        return self._idn

    def git(self, *args, cwd=None, timeout=20):
        """Read-only git, each identical question asked once per Laptop (checks repeat the same ones)."""
        memo = self.__dict__.setdefault("_git_memo", {})
        key = (args, cwd)
        if key not in memo:
            memo[key] = self._git(*args, cwd=cwd, timeout=timeout)
        return memo[key]

    def _git(self, *args, cwd=None, timeout=20):
        try:
            r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                               env={**os.environ, "HOME": self.home, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
            return r.stdout if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    @property
    def repos(self):
        """The repos the person's sessions ran in: the repo around each session's folder (its main checkout, so a worktree
        counts once), or the repos up to 3 levels below a folder that isn't one. From their sessions, never a list."""
        if self._repos is None:
            found = []
            for s in self.sessions.values():
                cwd = s.get("cwd")
                if not cwd or not os.path.isdir(cwd) or os.path.realpath(cwd) in (self.home, os.path.realpath(self.claude)):
                    continue
                if os.path.realpath(cwd).startswith(os.path.realpath(self.claude) + os.sep):
                    continue
                top = self.git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd).strip()
                if top.endswith(".git"):
                    found.append(os.path.dirname(top))
                    continue
                seen_dirs = 0
                for root, dirs, _ in os.walk(cwd):
                    seen_dirs += 1
                    if seen_dirs > 400:   # a big folder that isn't a repo: stop looking below it (bounded, every laptop)
                        break
                    depth = os.path.relpath(root, cwd).count(os.sep) + (0 if root == cwd else 1)
                    if ".git" in dirs or os.path.isfile(os.path.join(root, ".git")):
                        found.append(root); dirs[:] = []; continue
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "vendor")] if depth < 3 else []
            self._repos = sorted(set(os.path.realpath(r) for r in found))
        return self._repos

    def is_mine(self, author_line):
        """'name <email>' is the person's: one of their emails, their GitHub no-reply address, or their full name."""
        name, _, email = author_line.strip().rpartition(" <")
        email, name = email.rstrip(">").strip().lower(), name.strip().lower()
        idn = self.identity
        if email in idn["emails"]:
            return True
        if email.endswith("@users.noreply.github.com") and email.split("@")[0].split("+")[-1] in idn["names"]:
            return True
        return len(name) > 3 and name in idn["names"]

    def _authors(self, top):
        """One `git log` per repo, read once: {path in the repo: [author line per commit touching it]}."""
        memo = self.__dict__.setdefault("_auth_memo", {})
        if top not in memo:
            idx, who = {}, None
            for line in self._git("log", "--format=%x00%an <%ae>", "--name-only", "--no-renames", cwd=top, timeout=120).splitlines():
                if line.startswith("\x00"):
                    who = line[1:]
                elif line and who:
                    idx.setdefault(line, []).append(who)
            memo[top] = idx
        return memo[top]

    def toplevel(self, path):
        """The repo root a path lies in: the longest known repo (lt.repos) containing it, else git, asked once per folder."""
        real = os.path.realpath(path)
        d = real if os.path.isdir(real) else os.path.dirname(real)
        memo = self.__dict__.setdefault("_top_memo", {})
        if d not in memo:
            hit = None
            for r in self.repos:
                if (d == r or d.startswith(r + os.sep)) and (hit is None or len(r) > len(hit)):
                    hit = r
            if os.path.exists(os.path.join(d, ".git")):
                hit = d   # a folder holding .git (a folder for a repo, a file for a linked worktree) is its own root, even inside a known repo
            elif hit:
                # a nested repo inside a known repo has its own .git between them; check the folders in between
                cur = d
                while cur != hit and cur.startswith(hit):
                    if os.path.exists(os.path.join(cur, ".git")):
                        hit = None
                        break
                    cur = os.path.dirname(cur)
            memo[d] = hit or (self._git("rev-parse", "--show-toplevel", cwd=d).strip() if os.path.isdir(d) else "") or None
        return memo[d]

    def authorship(self, path):
        """{commits, theirs, yours}. yours follows the one ownership rule (ownership.py, the ruling 11-05): theirs unless
        a receipt says installed (marketplace, skills lock); git only makes it a teammate's (most commits someone
        else's). Untracked files are theirs too. A link is followed first; a folder counts every commit under it."""
        real = os.path.realpath(path)
        top = self.toplevel(real)
        if not top:   # in no repo: theirs unless a receipt says installed (the ruling); git can't say a teammate's
            return {"commits": 0, "theirs": 0, "yours": self.owner(real, None, 0, 0) == "theirs"}
        rel = os.path.relpath(real, top).replace(os.sep, "/")
        idx = self._authors(top)
        if os.path.isdir(real):
            log = [a for p, al in idx.items() if rel == "." or p.startswith(rel + "/") for a in al]
        else:
            log = idx.get(rel, [])
        mine = sum(1 for a in log if self.is_mine(a))
        return {"commits": len(log), "theirs": mine, "yours": self.owner(real, top, len(log), mine) == "theirs"}

    def _ownership(self):
        """ownership.py, loaded for this Laptop's home (its HOME-based paths are read at import), with its receipts."""
        if "_own_mod" not in self.__dict__:
            spec = importlib.util.spec_from_file_location(f"ownership_{abs(hash(self.home))}", OWNERSHIP)
            assert spec and spec.loader
            m = importlib.util.module_from_spec(spec)
            roots = self.repos   # outside the swap: it runs git
            with _home_as(self.home) as old:
                spec.loader.exec_module(m)
                # a fixture home has no real `claude` to ask; the files Claude Code keeps say the same
                use_claude = self.home == os.path.realpath(os.path.expanduser("~" if old is None else old)) and \
                    not os.environ.get("AGENTIC_SETUP_NO_CLAUDE")
                rc = m.load_receipts(roots=roots, use_claude=use_claude)
            self._own_mod, self._own_rc = m, rc
        return self._own_mod, self._own_rc

    def owner(self, path, top=None, commits=None, theirs=None):
        """installed / teammate / theirs (ownership.py): theirs unless a receipt says installed; git only says a teammate's."""
        memo = self.__dict__.setdefault("_owner_memo", {})
        key = (os.path.realpath(path), commits, theirs)
        if key in memo:
            return memo[key]
        m, rc = self._ownership()
        here = os.path.realpath(path)
        def auth(p):
            if commits is not None and os.path.realpath(p) == here:   # the caller's count is for this path only: rule 4 asks about another repo's root
                return {"commits": commits, "theirs": theirs} if commits else None
            a = self.authorship_counts(p)
            return a if a["commits"] else None
        def blob_authorship(repo, sha):
            """The commits of repo that added or removed this version of a file, and how many are theirs (ownership.py rule 4):
            the same question inventory.py asks, so a committed copy of someone else's file reads installed here too."""
            lg = [x for x in self.git("log", "--all", f"--find-object={sha}", "--format=%an <%ae>", "-n", "50", cwd=repo, timeout=60).splitlines() if x]
            return {"commits": len(lg), "theirs": sum(1 for x in lg if self.is_mine(x))} if lg else None
        memo[key] = m.owner(path, rc, authorship=auth, written_in_logs=self.written_in_logs, blob_authorship=blob_authorship)["owner"]
        return memo[key]

    def written_in_logs(self, folder):
        """True when a Write/Edit/MultiEdit/NotebookEdit in the person's own session logs (the window, sub-agents too)
        created or changed a file under folder: their agent wrote it. ownership.py asks this before it calls a skill
        folder an unpacked install. Bash writes (`cat > file`) are not seen."""
        if "_written_dirs" not in self.__dict__:
            dirs = set()
            for c in self.calls:
                if c["name"] in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                    mt = re.search(r'"(?:file_path|notebook_path)":\s*"((?:[^"\\]|\\.)*)"', c.get("input") or "")
                    if mt:
                        try:
                            p = json.loads('"' + mt.group(1) + '"')
                        except ValueError:
                            continue
                        d = os.path.dirname(os.path.expanduser(p) if p.startswith("~") else p)
                        dirs.add(d)
                        dirs.add(os.path.realpath(d))
            self.__dict__["_written_dirs"] = dirs
        want = {folder, os.path.realpath(folder)}
        return any(d == w or d.startswith(w.rstrip("/") + "/") for d in self.__dict__["_written_dirs"] for w in want)

    def authorship_counts(self, path):
        real = os.path.realpath(path)
        top = self.toplevel(real)
        if not top:
            return {"commits": 0, "theirs": 0}
        rel = os.path.relpath(real, top).replace(os.sep, "/")
        idx = self._authors(top)
        log = [a for p, al in idx.items() if rel == "." or p.startswith(rel + "/") for a in al] if os.path.isdir(real) else idx.get(rel, [])
        return {"commits": len(log), "theirs": sum(1 for a in log if self.is_mine(a))}

    def session_folders(self):
        return sorted({s["cwd"] for s in self.sessions.values() if s.get("cwd")})

    def settings_files(self):
        """Every settings file Claude Code reads: (scope, path, parsed json). Managed settings from the system paths."""
        out = []
        def add(scope, p):
            try:
                out.append((scope, p, json.load(open(p))))
            except (OSError, ValueError):
                pass
        add("user", os.path.join(self.claude, "settings.json"))
        add("user-local", os.path.join(self.claude, "settings.local.json"))
        for d in dict.fromkeys(self.repos + self.session_folders()):
            add("project", os.path.join(d, ".claude", "settings.json"))
            add("project-local", os.path.join(d, ".claude", "settings.local.json"))
        root = self.system_root
        for p in ("/Library/Application Support/ClaudeCode/managed-settings.json", "/etc/claude-code/managed-settings.json",
                  "C:\\ProgramData\\ClaudeCode\\managed-settings.json"):
            add("managed", os.path.join(root, p.lstrip("/\\")) if root else p)
        seen, uniq = set(), []
        for x in out:
            if os.path.realpath(x[1]) not in seen:
                seen.add(os.path.realpath(x[1])); uniq.append(x)
        return uniq

    def instruction_files(self):
        """Instruction files Claude Code loads: the user's, and in each repo every CLAUDE.md / CLAUDE.local.md /
        AGENTS.md / .claude/rules/*.md git tracks or the folder holds. (scope, path)."""
        memo = self.__dict__.get("_instr_memo")
        if memo is not None:
            return list(memo)
        out = [("user", p) for p in (os.path.join(self.claude, "CLAUDE.md"),) if os.path.exists(p)]
        out += [("user-rule", p) for p in glob.glob(os.path.join(self.claude, "rules", "**", "*.md"), recursive=True)]
        for r in self.repos:
            listed = self.git("ls-files", "--cached", cwd=r).splitlines()
            for rel in listed:
                b = os.path.basename(rel)
                if b in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md") or re.match(r"(^|.*/)\.claude/rules/.*\.md$", rel):
                    out.append(("root" if "/" not in rel.replace(".claude/", "") else "folder", os.path.join(r, rel)))
            for b in ("CLAUDE.local.md", "CLAUDE.md", "AGENTS.md"):   # untracked at the root still loads
                p = os.path.join(r, b)
                if os.path.exists(p) and ("root", p) not in out:
                    out.append(("root", p))
        # untracked rule files in subfolders load too: take the ones Claude Code recorded loading (nested_memory), not a
        # walk of every working tree (that walk cost 27s on a heavy laptop)
        have = {os.path.realpath(p) for _, p in out}
        for a in self.attachments:
            if a.get("type") != "nested_memory":
                continue
            m = re.search(r'"path":\s*"([^"]+)"', a.get("data") or "")
            p = m and m.group(1).encode().decode("unicode_escape")
            # one file, once: a copy in a linked worktree or a temp clone sits in a repo other than the known main
            # checkouts (lt.repos) and is the same file again; a file in no repo (a start folder) still loads
            top = self.toplevel(p) if p else None
            if p and os.path.basename(p) in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md") and os.path.exists(p) \
                    and (top is None or top in self.repos) and os.path.realpath(p) not in have:
                have.add(os.path.realpath(p))
                out.append(("folder" if self.below_project_root(p) else "root", p))
        self._instr_memo = list(out)
        return out

    def below_project_root(self, path):
        """Is this rule file scoped: does it sit BELOW the root of the project it belongs to, or is it a `.claude/rules`
        file? A file at its project's root (CLAUDE.md, AGENTS.md, .claude/CLAUDE.md) is that project's always-loaded
        file wherever the session started. Why (11-11): Claude Code records a repo's root file as `nested_memory`
        when the session was started above the repo (the home folder), and a person with one rule file per repo read
        "rules scoped to where they apply". The project: the git root around the file; in no repo, the deepest folder
        a session started in that holds it (the home folder is never a project, as in `repos`)."""
        real = os.path.realpath(path)
        if "/.claude/rules/" in real.replace(os.sep, "/"):
            return True
        top = self.toplevel(real)
        if not top:
            d = os.path.dirname(real)
            skip = (self.home, os.path.realpath(self.claude))
            starts = {os.path.realpath(s["cwd"]) for s in self.sessions.values() if s.get("cwd")} - set(skip)
            top = max((c for c in starts if d == c or d.startswith(c + os.sep)), key=len, default=None)
            if not top:
                return False
        rel = os.path.relpath(real, os.path.realpath(top)).replace(os.sep, "/")
        return "/" in (rel[len(".claude/"):] if rel.startswith(".claude/") else rel)

    def declared_rx(self, repo):
        """All of a repo's declared checks as one pattern (one regex search per command, not one per declared check)."""
        memo = self.__dict__.setdefault("_decl_rx", {})
        if repo not in memo:
            pats = self.declared_checks(repo)
            memo[repo] = re.compile("|".join("(?:%s)" % x.pattern for x in pats), re.M) if pats else None
        return memo[repo]

    def is_declared_check(self, cmd, repo):
        rx = self.declared_rx(repo) if repo else None
        return bool(rx and rx.search(cmd or ""))

    def declared_checks(self, repo):
        """The commands this repo itself declares as test / lint / build / type checks: package.json scripts, Makefile and
        justfile targets, CI workflow steps, pre-commit hooks, and each language's own test command when its project file
        is there. Returns a list of compiled patterns that match a shell command running one of them."""
        memo = self.__dict__.setdefault("_decl_memo", {})
        if repo in memo:
            return list(memo[repo])
        pats = []
        def lit(cmd):
            # the command a shell segment runs, not a word inside it: at the start of the command or of a segment
            # (after ; && || |), optionally behind env assignments and a runner (uv run, npx, poetry run, bunx…).
            # The skeptic, 11-05: `uv add --dev pytest`, `pip install mypy`, `which tsc` had counted as running a check.
            pats.append(re.compile(r"(?:^|[;&|]\s*)(?:\w+=\S*\s+)*(?:(?:uv run|uvx|npx|bunx|pnpm exec|poetry run|pipenv run|"
                                   r"python -m|python3 -m|bundle exec)\s+)?" + re.escape(cmd) + r"(\s|$)", re.M))   # each line of a script too
        pj = os.path.join(repo, "package.json")
        if os.path.exists(pj):
            try:
                scripts = (json.load(open(pj)).get("scripts") or {})
            except (OSError, ValueError):
                scripts = {}
            for name, body in scripts.items():
                if not CHECK_ROLE.search(name):   # install, ci, dev, start, build, sync… are not checks
                    continue
                for runner in ("npm run", "pnpm run", "yarn run", "bun run", "pnpm", "yarn", "bun"):
                    lit(f"{runner} {name}")
                if name == "test":
                    lit("npm test"); lit("npm t")
                if isinstance(body, str) and len(body) > 3:
                    lit(body.strip())
        for mk in ("Makefile", "makefile", "GNUmakefile"):
            p = os.path.join(repo, mk)
            if os.path.exists(p):
                for t in re.findall(r"^([A-Za-z0-9_.-]+):(?!=)", open(p, errors="replace").read(), re.M):
                    if CHECK_ROLE.search(t):
                        lit(f"make {t}")
        for jf in ("justfile", "Justfile", ".justfile"):
            p = os.path.join(repo, jf)
            if os.path.exists(p):
                for t in re.findall(r"^@?([A-Za-z0-9_-]+)[^\n:=]*:(?!=)", open(p, errors="replace").read(), re.M):
                    if CHECK_ROLE.search(t):
                        lit(f"just {t}")
        for wf in glob.glob(os.path.join(repo, ".github", "workflows", "*.y*ml")) + glob.glob(os.path.join(repo, ".gitlab-ci.yml")):
            for line in open(wf, errors="replace"):
                m = re.match(r"\s*(?:-\s*)?run:\s*(.+)$", line)
                cmd = m.group(1).strip().strip("'\"") if m else ""
                if cmd and cmd not in ("|", ">") and CHECK_ROLE.search(cmd) and not NOT_A_CHECK.search(cmd):
                    lit(cmd)
        if os.path.exists(os.path.join(repo, ".pre-commit-config.yaml")):
            lit("pre-commit run")
        # each language's own check commands; build is not a check (the skeptic, 11-05: a SessionStart `cargo build`)
        lang = {"Cargo.toml": ("cargo test", "cargo check", "cargo clippy"), "go.mod": ("go test", "go vet"),
                "pyproject.toml": ("pytest", "python -m pytest", "uv run pytest", "ruff check", "mypy"),
                "setup.cfg": ("pytest", "python -m pytest"), "tox.ini": ("tox",), "pom.xml": ("mvn test", "mvn verify", "./mvnw test"),
                "build.gradle": ("gradle test", "./gradlew test", "./gradlew check"), "build.gradle.kts": ("./gradlew test", "./gradlew check"),
                "Gemfile": ("bundle exec rspec", "rspec", "rake test"), "composer.json": ("vendor/bin/phpunit", "composer test"),
                "tsconfig.json": ("tsc", "npx tsc"), "deno.json": ("deno test",), "Package.swift": ("swift test",)}
        for f, cmds in lang.items():
            if os.path.exists(os.path.join(repo, f)):
                for c in cmds:
                    lit(c)
        if glob.glob(os.path.join(repo, "*.sln")) or glob.glob(os.path.join(repo, "*.csproj")):
            lit("dotnet test")
        memo[repo] = list(pats)
        return pats

    def repo_of(self, path):
        """The repo (from self.repos) a path or session folder lies in, or None."""
        rp = os.path.realpath(path or "")
        best = None
        for r in self.repos:
            if rp == r or rp.startswith(r + os.sep):
                if best is None or len(r) > len(best):
                    best = r
        return best


# ---------------------------------------------------------------------- Claude Code's own agent types
def builtin_agent_types():
    """The agent types Claude Code ships (Explore, Plan, general-purpose…), from labs/v15/substrate/claude-code.yaml's
    `agent_types` (read from the program itself). A file named after one is an override: it may be theirs, but launches of
    that type are the built-in's, and it is never a thing they built (11-05 night: Explore read as a built agent)."""
    for p in (os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "v15", "substrate", "claude-code.yaml"),
              os.path.join(os.path.dirname(os.path.dirname(HERE)), "..", "v15", "substrate", "claude-code.yaml")):
        try:
            m = re.search(r"^agent_types:\s*\[(.*?)\]", open(p, encoding="utf-8").read(), re.M | re.S)
        except OSError:
            continue
        if m:
            return {x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()}
    return {"Explore", "Plan", "general-purpose", "claude-code-guide", "statusline-setup", "fork"}


# ---------------------------------------------------------------------- the registry the checks use
CHECKS = {}


def check(pid, level, i):
    """Register a rewritten check: fn(lt: Laptop) -> {"value": number, "evidence": [<=3 short strings], "note": str}.
    value is a count (0 = not seen). Say what it can't see in note."""
    def deco(fn):
        CHECKS[(pid, level, i)] = fn
        return fn
    return deco
