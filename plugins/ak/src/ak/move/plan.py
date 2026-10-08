"""plan — what one move touches, decided up front, as data the runner can replay and undo.

A workspace is a folder sessions ran in (workspace.py). Moving one is more than `mv`: Claude Code
files its chat history under a folder named after the path, git records every worktree by absolute
path, the tracer and the trace store key rows by that folder name, settings and ~/.claude.json name
the path, and the memory keeps a cache per folder. Each of those is a HANDLER here, or in the plugin
that owns the data (plugin.json `"relocate": {"entry","attr"}`, loaded like the `"cli"` block).

    handler.plan(move)   adds steps (move.step) and findings (move.fail / warn / info)

The rule every handler follows: rewrite POINTERS, leave HISTORY. A slug folder's name, an index
key, a settings path are pointers and move; a transcript's recorded cwd, an observer row's label,
a ledger line are history and stay, readable under the new name through lineage.jsonl.

Nothing here writes. `build()` returns the Move; the CLI checks it is quiet (quiesce), freezes the
engine into the move's folder and runs it there (runner.py).
"""
from __future__ import annotations

import glob
import hashlib
import importlib.util
import json
import os
import sqlite3
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field

from ak.move import ops
from ak._brand import HOME_DIR, cmd, env, is_windows, pid_alive, posix
from ak._brand import claude_home as _claude_home, claude_json as _claude_json, slug as _slug


def slug(path: str) -> str:
    """Claude Code's folder name for a path under <claude home>/projects: every non-alphanumeric is `-`."""
    return _slug(path)


def claude_dir() -> str:
    return os.path.realpath(str(_claude_home()))


def claude_json() -> str:
    return str(_claude_json())


# The kit home's layout (layout 2), as vault.engine.ladder names it — ak imports no vault, so the
# folders are named again here, once; everything in ak/move asks these, never a join of its own.
#
#   <kit>/vault/  the global vault   <kit>/db/  what can't be rebuilt   <kit>/cache/  what rebuilds itself
#   <kit>/moves/  this package's journals and undo copies                <kit>/layout  {"layout": 2}
LAYOUT = 2


def kit_home() -> str:
    """The kit home, where its state lives: $AK_HOME, else $AK_PATH, else ~/.ak (ladder.kit_home)."""
    h = (env("HOME") or "").strip() or (env("PATH") or "").strip()
    return os.path.realpath(os.path.expanduser(h or "~/" + HOME_DIR))


def global_vault() -> str:
    """The global vault: $AK_PATH, else <kit>/vault (ladder.global_kit)."""
    p = (env("PATH") or "").strip()
    return os.path.realpath(os.path.expanduser(p)) if p else os.path.join(kit_home(), "vault")


def db_dir(home: str = "") -> str:
    """What the kit can't rebuild — brain.db, the ledgers, lineage, the registries: $AK_STORE, else <home>/db."""
    s = (env("STORE") or "").strip()
    return os.path.realpath(os.path.expanduser(s)) if s else os.path.join(home or kit_home(), "db")


def cache_dir(home: str = "") -> str:
    """What rebuilds itself when deleted — the index caches, names.json: <home>/cache."""
    return os.path.join(home or kit_home(), "cache")


def moves_dir(home: str = "") -> str:
    """Every move's folder (plan, journal, backups) and the one-move LOCK: <home>/moves."""
    return os.path.join(home or kit_home(), "moves")


def layout_file(home: str = "") -> str:
    return os.path.join(home or kit_home(), "layout")


def _depth(p: str) -> int:
    return posix(p).rstrip("/").count("/")


def _real(p: str) -> str:
    """A realpath that works for a path that does not exist yet: its parent's realpath + its name."""
    p = os.path.abspath(os.path.expanduser(p))
    p = p if p == os.path.dirname(p) else p.rstrip("/\\")  # a root keeps its separator
    if os.path.lexists(p) and not os.path.islink(p):
        return os.path.realpath(p)
    return os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p))


@dataclass
class Finding:
    handler: str
    level: str          # fail · warn · info
    what: str
    fix: str | None = None

    def as_dict(self):
        return {"handler": self.handler, "level": self.level, "what": self.what, "fix": self.fix}


@dataclass
class Move:
    id: str
    old: str
    new: str
    adopt: bool = False
    link: bool = False
    home: str = ""
    claude: str = ""
    claude_json: str = ""
    steps: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    lineage_rows: list = field(default_factory=list)
    slugs: dict = field(default_factory=dict)   # old slug folder → new slug folder (full paths)
    facts: dict = field(default_factory=dict)
    kind: str = "workspace"   # workspace · home · plugin · marketplace

    # ── what a handler calls ──
    def step(self, handler: str, name: str, phase: int, op: str, **args):
        self.steps.append({"handler": handler, "name": name, "phase": phase, "op": op, "args": args})

    def fail(self, handler, what, fix=None):
        self.findings.append(Finding(handler, "fail", what, fix))

    def warn(self, handler, what, fix=None):
        self.findings.append(Finding(handler, "warn", what, fix))

    def info(self, handler, what, fix=None):
        self.findings.append(Finding(handler, "info", what, fix))

    def lineage(self, kind: str, frm: str, to: str, **extra):
        self.lineage_rows.append({"kind": kind, "from": frm, "to": to, **extra})

    under = staticmethod(ops.under)

    def remap(self, p: str) -> str:
        return ops.remap(p, self.old, self.new)

    @property
    def here(self) -> str:
        """Where the folder is while planning: the old path, or the new one when adopting."""
        return self.new if self.adopt else self.old

    def ordered(self) -> list:
        return sorted(self.steps, key=lambda s: s["phase"])

    def as_plan(self) -> dict:
        return {"id": self.id, "kind": self.kind, "old": self.old, "new": self.new, "adopt": self.adopt,
                "home": self.home, "claude": self.claude, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "facts": self.facts, "steps": self.ordered()}


# ── ak handlers ─────────────────────────────────────────────────────────────
def h_fs(m: Move):
    """The folder itself, the venvs inside it, the links pointing into it, the tombstone left behind."""
    if not m.adopt:
        m.step("fs", f"move {m.old} → {m.new}", 30, "rename", src=m.old, dst=m.new)
    # A venv's scripts carry absolute shebangs: after the move they point at nothing. Set each aside
    # (undo puts it back); `uv run` builds a fresh one on first use.
    for venv in _venvs(m.here):
        m.step("fs", f"set aside {venv} (absolute shebangs; uv rebuilds it)", 10 if not m.adopt else 35,
               "stash", path=venv)
    for d in bin_dirs():
        for link in glob.glob(os.path.join(d, "*")):
            if os.path.islink(link):
                t = os.readlink(link)
                if os.path.isabs(t) and m.under(t, m.old):
                    m.step("fs", f"re-point {link}", 65, "symlink", link=link, **{"from": t, "to": m.remap(t)})
    m.step("fs", f"leave a {'link' if m.link else 'note'} at {m.old} saying where it went", 95,
           "tombstone", path=m.old, moved_to=m.new, link=m.link)


def bin_dirs() -> tuple:
    """The per-user bin folders that hold links into workspaces: ~/.local/bin (uv's, on every OS) and ~/bin."""
    h = os.path.expanduser("~")
    return (os.path.join(h, ".local", "bin"), os.path.join(h, "bin"))


def _venvs(root: str, depth: int = 7) -> list:
    out = []
    base = _depth(root)
    for d, dirs, _files in os.walk(root):
        if _depth(d) - base >= depth:
            dirs[:] = []
            continue
        keep = []
        for n in dirs:
            p = os.path.join(d, n)
            if n in ("node_modules", ".git") or os.path.islink(p):
                continue
            if os.path.isfile(os.path.join(p, "pyvenv.cfg")):
                out.append(p)
                continue
            keep.append(n)
        dirs[:] = keep
    return sorted(out)


def _git(*args, cwd=None) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return p.returncode, (p.stdout or "")


def _worktrees(repo: str) -> list:
    rc, out = _git("-C", repo, "worktree", "list", "--porcelain")
    if rc:
        return []
    paths = [line[len("worktree "):] for line in out.splitlines() if line.startswith("worktree ")]
    return paths[1:]  # the first is the main checkout itself


def _main_of(worktree: str) -> str:
    """The main checkout of a linked worktree (its .git file → gitdir → commondir), or ""."""
    try:
        with open(os.path.join(worktree, ".git"), encoding="utf-8") as f:
            gitdir = f.read().split("gitdir:", 1)[1].strip()
    except (OSError, IndexError):
        return ""
    gitdir = gitdir if os.path.isabs(gitdir) else os.path.normpath(os.path.join(worktree, gitdir))
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as f:
            common = f.read().strip()
    except OSError:
        common = os.path.dirname(os.path.dirname(gitdir))
    common = common if os.path.isabs(common) else os.path.normpath(os.path.join(gitdir, common))
    return os.path.dirname(common.rstrip("/\\"))


def h_git(m: Move):
    """Git records a worktree by absolute path, both ways. After the move, `worktree repair` re-links."""
    dotgit = os.path.join(m.here, ".git")
    if os.path.isdir(dotgit):
        # git still records the old paths — before the move, and after a move by hand (adopt)
        old_paths = _worktrees(m.here)
        new_paths = [m.remap(p) for p in old_paths]
        if new_paths:
            m.facts["worktrees"] = len(new_paths)
            if not m.adopt:  # runs on undo, after the folder is back: re-link to the old paths
                m.step("git", "on undo: re-link the worktrees to the old paths", 5, "exec",
                       argv=None, revert_argv=["git", "-C", m.old, "worktree", "repair", *old_paths],
                       revert_cwd=m.old)
            m.step("git", f"re-link {len(new_paths)} worktree(s)", 40, "exec",
                   argv=["git", "-C", m.new, "worktree", "repair", *new_paths], cwd=m.new)
        rc, hooks = _git("-C", m.here, "config", "--local", "core.hooksPath")
        hooks = hooks.strip()
        if rc == 0 and os.path.isabs(hooks) and m.under(hooks, m.old):
            m.step("git", f"core.hooksPath → {m.remap(hooks)}", 41, "exec",
                   argv=["git", "-C", m.new, "config", "--local", "core.hooksPath", m.remap(hooks)],
                   revert_argv=["git", "-C", m.old, "config", "--local", "core.hooksPath", hooks],
                   revert_cwd=m.old)
    elif os.path.isfile(dotgit):
        main = _main_of(m.here)
        if main and not m.under(main, m.old):
            if not m.adopt:
                m.step("git", "on undo: re-link this worktree at its old path", 5, "exec", argv=None,
                       revert_argv=["git", "-C", main, "worktree", "repair", m.old], revert_cwd=main)
            m.step("git", f"re-link this worktree in {main}", 40, "exec",
                   argv=["git", "-C", main, "worktree", "repair", m.new], cwd=main)


def _cwd_of(d: str) -> str:
    """The launch folder a session folder stands for: a transcript cwd whose slug is the folder's name."""
    name = os.path.basename(d)
    files = sorted(glob.glob(os.path.join(d, "*.jsonl")), key=lambda f: -os.path.getmtime(f))
    for f in files[:40]:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > 60:
                        break
                    if '"cwd"' not in line:
                        continue
                    try:
                        cwd = json.loads(line).get("cwd")
                    except ValueError:
                        continue
                    if isinstance(cwd, str) and slug(cwd) == name:
                        return cwd
        except OSError:
            continue
    return ""


def _slugs_under(root: str, as_root: str, depth: int = 4) -> dict:
    """slug → path for every folder under ROOT (read as if it sat at AS_ROOT), depth-limited."""
    out = {slug(as_root): as_root}
    base = _depth(root)
    for d, dirs, _files in os.walk(root):
        if _depth(d) - base >= depth:
            dirs[:] = []
        dirs[:] = [n for n in dirs if n not in ("node_modules", ".git", ".venv", "__pycache__")]
        rel = os.path.relpath(d, root)
        p = as_root if rel == "." else os.path.join(as_root, rel)
        out.setdefault(slug(p), p)
    return out


def h_claude_code(m: Move):
    """Chat history: the slug folders under ~/.claude/projects, ~/.claude.json, history, settings.

    Settings name the folder as a whole value and inside command strings: `_settings_step`."""
    projects = os.path.join(m.claude, "projects")
    base = slug(m.old)
    candidates = sorted(glob.glob(os.path.join(projects, glob.escape(base))) +
                        glob.glob(os.path.join(projects, glob.escape(base) + "-*")))
    on_disk = None
    for d in candidates:
        if not os.path.isdir(d) or os.path.islink(d):
            continue
        name = os.path.basename(d)
        cwd = _cwd_of(d)
        if not cwd and name == base:
            cwd = m.old
        if not cwd:  # a session that started in the repo and moved into a worktree: find the folder itself
            if on_disk is None:
                on_disk = _slugs_under(m.here, m.old)
            cwd = on_disk.get(name, "")
        if not cwd:  # a prefix alone also matches /x/agentic-kit-wt/…: read, never guess
            m.warn("claude_code", f"{name}: no transcript or folder says which workspace it belongs to — left in place")
            continue
        if not m.under(cwd, m.old):
            continue  # a sibling that only starts the same way (agentic-kit-wt)
        target_path = m.remap(cwd)
        target = slug(target_path)
        if len(target) > 200:
            m.fail("claude_code", f"{target_path}: Claude Code shortens names past 200 characters with a hash "
                   "this tool does not reproduce — choose a shorter destination")
            continue
        dst = os.path.join(projects, target)
        if os.path.lexists(dst):
            m.fail("claude_code", f"{dst} already exists — two histories would share one folder",
                   f"look inside, then move one aside: ls {dst}")
            continue
        m.slugs[d] = dst
        m.step("claude_code", f"chat history {name} → {target}", 45, "rename", src=d, dst=dst)
        _relocated(m, d, dst)
    m.facts["history_folders"] = len(m.slugs)
    m.facts["sessions"] = sum(len(glob.glob(os.path.join(d, "*.jsonl"))) for d in m.slugs)

    m.step("claude_code", "~/.claude.json: project entries and repo paths", 60, "json_remap",
           file=m.claude_json, old=m.old, new=m.new, keys=True)
    m.step("claude_code", "prompt history (up-arrow)", 60, "jsonl_remap",
           file=os.path.join(m.claude, "history.jsonl"), field="project", old=m.old, new=m.new)
    names = [m.old] + [t for t in [ops.tilde_of(m.old, _homes())] if t]  # the path, and its `~/` form
    for f in ("settings.json", "settings.local.json"):
        p = os.path.join(m.claude, f)
        if os.path.isfile(p) and any(_mentions(p, n) for n in names):
            _settings_step(m, p)
    for f in (os.path.join("plugins", "known_marketplaces.json"), os.path.join("plugins", "installed_plugins.json")):
        p = os.path.join(m.claude, f)
        if os.path.isfile(p) and _mentions(p, m.old):
            m.step("claude_code", f"{p}: paths", 60, "json_remap", file=p, old=m.old, new=m.new, keys=False)


def _settings_step(m: Move, p: str):
    """A settings file names the folder as a whole value (additionalDirectories, an env path) AND inside
    strings (fileSuggestion.command, statusLine.command, a hook's command, a permission rule): both
    are re-pointed, at a path boundary, so `/old-wt/x` is left alone (ops.json_remap `embedded`). A folder
    under HOME is also found as `~/rest` and stays a `~/` form. The step names each key it will edit,
    so the dry run shows them."""
    args = dict(file=p, old=m.old, new=m.new, keys=False, embedded=True, homes=_homes())
    try:
        with open(p, encoding="utf-8") as fh:
            changes = ops.remap_changes(json.load(fh), m.old, m.new, embedded=True, homes=args["homes"])
    except (OSError, ValueError):  # unreadable now: the step still runs, and fails with its own reason
        m.step("claude_code", f"{p}: paths", 60, "json_remap", **args)
        return
    if not changes:
        return  # it only names a look-alike folder (`/old-wt/x`)
    keys = [_dotted(c["path"]) for c in changes]
    shown = ", ".join(keys[:10]) + (f", +{len(keys) - 10} more" if len(keys) > 10 else "")
    m.step("claude_code", f"{p}: paths ({shown})", 60, "json_remap", **args)
    m.facts["settings_paths"] = m.facts.get("settings_paths", 0) + len(changes)


def _homes() -> list:
    """The HOME a `~/` in a settings string expands to, as the shell has it and with symlinks resolved."""
    h = os.path.expanduser("~")
    return list(dict.fromkeys([h, os.path.realpath(h)]))


def _dotted(path: list) -> str:
    """A json path as a person reads it: permissions.additionalDirectories[1], hooks.Stop[0].hooks[0].command."""
    out = ""
    for seg in path:
        out += f"[{seg}]" if isinstance(seg, int) else (f".{seg}" if out else str(seg))
    return out


def _relocated(m: Move, old_dir: str, new_dir: str):
    """A transcript that entered a worktree carries a `relocated` record; its path overrides the
    recorded cwd on resume, so a newer one pointing at the new path is appended."""
    for f in sorted(glob.glob(os.path.join(old_dir, "*.jsonl"))):
        if not _contains(f, b'"relocatedCwd"'):
            continue
        last = None
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"relocatedCwd"' in line:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if o.get("type") == "relocated":
                        last = o
        if last and m.under(str(last.get("relocatedCwd") or ""), m.old):
            rec = {"type": "relocated", "sessionId": last.get("sessionId"),
                   "relocatedCwd": m.remap(last["relocatedCwd"])}
            m.step("claude_code", f"resume path of {os.path.basename(f)}", 46, "append_line",
                   file=os.path.join(new_dir, os.path.basename(f)), record=rec)


def _contains(path: str, needle: bytes) -> bool:
    """Whether the file holds NEEDLE (grep -lF), read in chunks: a transcript can be large."""
    tail = b""
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    return False
                if needle in tail + chunk:
                    return True
                tail = chunk[-len(needle):]
    except OSError:
        return False


def _mentions(path: str, needle: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return needle in f.read()
    except OSError:
        return False


def h_memory(m: Move):
    """The memory's per-folder caches (they rebuild) and its registries (pointers)."""
    for key_dir in sorted(glob.glob(os.path.join(cache_dir(m.home), "workspaces", "*"))):
        scope = ""
        for n in ("memory-index.v4.json", "memory-index.json"):
            try:
                with open(os.path.join(key_dir, n), encoding="utf-8") as f:
                    scope = str(json.load(f).get("scope") or "")
                break
            except (OSError, ValueError, AttributeError):
                continue
        if scope and m.under(scope, m.old):
            m.step("memory", f"set aside the index cache of {scope} (rebuilds on first use)", 20,
                   "stash", path=key_dir)
    for p, keys in ((os.path.join(db_dir(m.home), "workspaces.json"), False),
                    (os.path.join(cache_dir(m.home), "names.json"), True)):
        f = os.path.basename(p)
        if os.path.isfile(p) and _mentions(p, m.old):
            m.step("memory", f"{f}: paths", 60, "json_remap", file=p, old=m.old, new=m.new, keys=keys)


def traces_db() -> str:
    override = env("TRACES_DIR")
    if override:
        return os.path.join(os.path.expanduser(override), "traces.db")
    kit = os.path.join(db_dir(), "traces")
    base = kit if os.path.isdir(kit) else os.path.join(claude_dir(), "brain-traces")
    return os.path.join(base, "traces.db")


def h_traces(m: Move):
    """ak's tool-call store (`ak trace`): it points at transcripts by path and byte offset."""
    db = traces_db()
    if not m.slugs or not os.path.isfile(db):
        return
    dirs = {a + "/": b + "/" for a, b in m.slugs.items()}
    m.step("traces", f"{db}: transcript pointers", 50, "sql_map", db=db, updates=[
        {"table": "files", "col": "path", "mode": "prefix", "map": dict(m.slugs)},
        {"table": "spans", "col": "file", "mode": "prefix", "map": dict(m.slugs)},
        {"table": "spans", "col": "blobs", "mode": "contains", "map": dirs},
        *[{"table": "runs", "col": c, "mode": "prefix", "map": {**m.slugs, m.old: m.new}}
          for c in ("recordPath", "journalPath", "runDir")],
    ])


def _clones(m: Move) -> list:
    """Git checkouts outside the moved folder that may clone it: every marketplace's install
    location (the release clone) and every ~/.claude.json project that is a repo."""
    out = []
    known = os.path.join(m.claude, "plugins", "known_marketplaces.json")
    try:
        with open(known, encoding="utf-8") as f:
            out += [(v or {}).get("installLocation") or "" for v in json.load(f).values()]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with open(m.claude_json, encoding="utf-8") as f:
            out += list((json.load(f).get("projects") or {}).keys())
    except (OSError, ValueError, AttributeError):
        pass
    seen, repos = set(), []
    for p in out:
        p = os.path.realpath(p) if p else ""
        if p and p not in seen and not m.under(p, m.old) and os.path.isdir(os.path.join(p, ".git")):
            seen.add(p)
            repos.append(p)
    return repos


def h_git_remotes(m: Move):
    """A clone elsewhere whose remote is a path into the moved folder (the release clone's origin is
    ~/agentic-kit): its `git pull` would fail. The remote URL is a pointer, and follows."""
    for repo in _clones(m):
        rc, out = _git("-C", repo, "config", "--local", "--get-regexp", r"^remote\..*\.url$")
        if rc:
            continue
        for line in out.splitlines():
            key, _, url = line.partition(" ")
            path = url[len("file://"):] if url.startswith("file://") else url
            if not os.path.isabs(path):
                continue
            real = os.path.realpath(path)  # /tmp/x is /private/tmp/x: compare, and rewrite, the real path
            if not m.under(real, m.old):
                continue
            name = key[len("remote."):-len(".url")]
            new_url = ("file://" if url.startswith("file://") else "") + m.remap(real)
            m.step("git", f"{repo}: remote {name} → {new_url}", 60, "exec",
                   argv=["git", "-C", repo, "remote", "set-url", name, new_url],
                   revert_argv=["git", "-C", repo, "remote", "set-url", name, url], cwd=repo)


CORE = [h_fs, h_git, h_git_remotes, h_claude_code, h_memory, h_traces]


# ── plugin handlers (plugin.json "relocate") ────────────────────────────────
def plugin_handlers(plugins_dir) -> list:
    """(name, handler) for every sibling plugin that declares `"relocate": {"entry", "attr"}`."""
    out = []
    if not plugins_dir or not os.path.isdir(plugins_dir):
        return out
    for pj in sorted(glob.glob(os.path.join(plugins_dir, "*", "plugin.json")) +
                     glob.glob(os.path.join(plugins_dir, "*", ".claude-plugin", "plugin.json"))):
        try:
            with open(pj, encoding="utf-8") as f:
                spec = json.load(f).get("relocate")
        except (OSError, ValueError):
            continue
        if not spec:
            continue
        root = os.path.dirname(pj)
        if os.path.basename(root) == ".claude-plugin":
            root = os.path.dirname(root)
        path = os.path.join(root, spec["entry"])
        try:
            s = importlib.util.spec_from_file_location(f"relocate_{os.path.basename(root)}", path)
            mod = importlib.util.module_from_spec(s)
            s.loader.exec_module(mod)
            out.append((os.path.basename(root), getattr(mod, spec.get("attr", "handler"))))
        except Exception as e:  # a broken handler must not stop the others: it becomes a finding
            out.append((os.path.basename(root), _Broken(f"{path}: {type(e).__name__}: {e}")))
    return out


class _Broken:
    def __init__(self, why):
        self.why = why

    def plan(self, m):
        m.fail("registry", f"a relocate handler failed to load — {self.why}")

    def detect(self, ctx):
        return [{"level": "warn", "what": f"a relocate handler failed to load — {self.why}"}]


# ── build ───────────────────────────────────────────────────────────────────
def new_id(old: str, new: str) -> str:
    """`<second>-<ms as 3 hex><1 hex of hash>`: unique and in creation order within a second. The suffix
    was a hash of (old, new) alone, so the same move started twice in one second (an undo and a re-run)
    shared one id and one journal folder, and the second run skipped steps; `moves list` sorts by id."""
    t = time.time()
    tail = hashlib.sha1(f"{old}\0{new}\0{time.time_ns()}\0{os.getpid()}".encode()).hexdigest()[0]
    return time.strftime("%Y%m%dT%H%M%S", time.localtime(t)) + "-" + f"{int(t % 1 * 1000):03x}{tail}"


def build(old: str, new: str, adopt: bool = False, link: bool = False, plugins_dir=None) -> Move:
    m = Move(id=new_id(old, new), old=_real(old), new=_real(new), adopt=adopt, link=link,
             home=kit_home(), claude=claude_dir(), claude_json=claude_json())
    _preflight(m)
    if any(f.level == "fail" for f in m.findings):
        return m
    for h in CORE:
        h(m)
    for name, h in plugin_handlers(plugins_dir):
        try:
            h.plan(m)
        except Exception as e:
            m.fail(name, f"its relocate handler raised {type(e).__name__}: {e}")
    m.lineage("path", m.old, m.new)
    m.step("lineage", f"record the move ({len(m.lineage_rows)} row(s)) for readers of history", 90,
           "lineage", file=os.path.join(db_dir(m.home), "lineage.jsonl"), rows=m.lineage_rows)
    for s in m.steps:  # the gateway guard, once more over the whole plan
        for k in ("src", "dst", "path", "file", "db", "link"):
            if s["args"].get(k):
                try:
                    ops.guard(s["args"][k])
                except ops.Refused as e:
                    m.fail("gateway", str(e))
    return m


def _preflight(m: Move):
    home = os.path.realpath(os.path.expanduser("~"))
    if m.old == m.new:
        m.fail("move", "the old and new paths are the same")
        return
    for p in (m.old, m.new):
        if p == home or p == os.path.dirname(p):  # $HOME, or a root: / or C:\
            m.fail("move", f"{p} is not a workspace — $HOME and / are where workspaces live")
    if m.under(m.new, m.old) or m.under(m.old, m.new):
        m.fail("move", "one path is inside the other")
    # the home, and its vault and db when $AK_PATH / $AK_STORE put them elsewhere
    for kit in dict.fromkeys((m.home, global_vault(), db_dir(m.home))):
        if ops.under(kit, m.old) or ops.under(m.old, kit) or ops.under(kit, m.new):
            m.fail("move", f"{kit} is the kit's own state — moving it is `{cmd('home move')}`, not a workspace move")
            break
    if ops.under(m.claude, m.old) or ops.under(m.old, m.claude):
        m.fail("move", f"{m.claude} is Claude Code's own folder")
    try:
        ops.guard(m.old, m.new)
    except ops.Refused as e:
        m.fail("gateway", str(e))
    if m.adopt:
        if os.path.lexists(m.old) and not _tombstone_of(m.old):
            m.fail("move", f"--adopt is for a folder already moved by hand, but {m.old} is still there",
                   f"{cmd('ws move')} {m.old} {m.new}")
        if not os.path.isdir(m.new):
            m.fail("move", f"{m.new} does not exist — nothing to adopt")
        return
    if not os.path.isdir(m.old):
        if os.path.isdir(m.new):
            m.fail("move", f"{m.old} is gone and {m.new} exists — it was moved by hand",
                   f"{cmd('ws move')} --adopt {m.old} {m.new}")
        else:
            m.fail("move", f"{m.old} does not exist")
        return
    if os.path.lexists(m.new):
        m.fail("move", f"{m.new} already exists")
        return
    parent = os.path.dirname(m.new)
    if not os.path.isdir(parent):
        m.fail("move", f"{parent} does not exist", f"mkdir -p {parent}")
        return
    if os.stat(m.old).st_dev != os.stat(parent).st_dev:
        m.fail("move", f"{m.old} and {parent} are on different disks — a move is a rename, never a copy")


def _tombstone_of(p: str) -> dict:
    if os.path.islink(p):
        return {"moved_to": os.readlink(p)}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("moved_to") else {}
    except (OSError, ValueError, IsADirectoryError):
        return {}


# ── quiet: nothing may be writing what the move rewrites ────────────────────
def live_sessions(claude: str, is_claude=None) -> list:
    """(pid, cwd) of every Claude Code process still running, from ~/.claude/sessions/<pid>.json."""
    is_claude = is_claude or _is_claude
    out = []
    for f in glob.glob(os.path.join(claude, "sessions", "*.json")):
        try:
            pid = int(os.path.basename(f)[:-5])
            with open(f, encoding="utf-8") as fh:
                cwd = (json.load(fh) or {}).get("cwd") or "?"
        except (ValueError, OSError):
            continue
        if not pid_alive(pid):
            continue
        if is_claude(pid):
            out.append((pid, cwd))
    return out


def _is_claude(pid: int) -> bool:
    """Whether PID still runs Claude Code (a pid file outlives its process, and a pid is reused).
    Unknown counts as yes: a live session is never missed."""
    comm = _comm(pid)
    if comm is None or "claude" in comm.lower():
        return True
    base = os.path.basename(comm).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if base not in _RUNTIMES:
        return False  # a name that is not claude, and cannot be running it
    cl = _cmdline(pid)  # an npm install: the process is `node .../@anthropic-ai/claude-code/cli.js`
    return cl is None or "claude" in cl.lower()


_RUNTIMES = {"node", "nodejs", "bun", "deno"}


def _cmdline(pid: int):
    """The command line PID runs, or None when this system cannot say."""
    if sys.platform.startswith("linux"):  # portable: ok (procfs is Linux's)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return f.read().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        except OSError:
            return None
    if is_windows():
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"]
    else:
        argv = ["ps", "-p", str(pid), "-o", "args="]  # portable: ok (macOS's ps)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                           stdin=subprocess.DEVNULL, creationflags=0x08000000 if is_windows() else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (p.stdout or "").strip()
    return out if p.returncode == 0 and out else None


def _comm(pid: int):
    """The executable name PID runs, or None when this system cannot say."""
    if sys.platform.startswith("linux"):  # portable: ok (procfs is Linux's)
        try:
            with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            return None
    argv = (["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"] if is_windows()
            else ["ps", "-p", str(pid), "-o", "comm="])  # portable: ok (Windows' tasklist, macOS's ps)
    try:
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if is_windows():  # "claude.exe","4242",... ; "INFO: No tasks ..." when it is gone
        return out.split(",")[0].strip('"') if out.startswith('"') else ""
    return os.path.basename(out)


def quiesce(m: Move, is_claude=None) -> list:
    """The reasons not to run now, as findings. Empty means go."""
    out = []
    if os.environ.get("CLAUDECODE"):
        out.append(Finding("quiet", "fail", "this is running inside Claude Code — a move rewrites the history "
                           "and settings the live session is writing", "run it from a plain terminal"))
    live = live_sessions(m.claude, is_claude)
    if live:
        where = ", ".join(f"{pid} in {cwd}" for pid, cwd in live[:6])
        out.append(Finding("quiet", "fail", f"{len(live)} Claude Code session(s) still open: {where}"
                           + (" …" if len(live) > 6 else ""), "quit them (they share ~/.claude.json and history)"))
    dbs = [traces_db(), os.path.join(claude_dir(), "plugins", "data", "conversation-index", "index.db"),
           os.path.join(db_dir(m.home), "brain.db")]
    held = _held(dbs)
    if held is None:
        out.append(Finding("quiet", "fail", "cannot tell whether a process still holds a store (this "
                           "system lists no open files): " + ", ".join(d for d in dbs if os.path.isfile(d)),
                           "quit every Claude Code session and indexer, then run it again"))
    elif held:
        out.append(Finding("quiet", "fail", "a process still holds a store: " + ", ".join(held),
                           "wait for the indexer to finish, then run it again"))
    return out


def _held(dbs):
    """`pid N` for every other process with one of DBS open, or None when this system cannot say:
    a check that did not run is never "nothing held"."""
    dbs = [d for d in dbs if os.path.isfile(d)]
    if not dbs:
        return []
    if sys.platform.startswith("linux") and os.path.isdir("/proc/self/fd"):  # portable: ok (procfs is Linux's)
        return [f"pid {x}" for x in _proc_holders(dbs)]
    if is_windows():
        return _held_windows(dbs)
    if not shutil.which("lsof"):  # portable: ok (nothing here lists open files)
        return None
    try:
        p = subprocess.run(["lsof", "-t", *dbs], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)  # portable: ok (macOS)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode not in (0, 1):  # 1: some file is open by nobody; anything else is the tool failing
        return None
    pids = [x for x in p.stdout.split() if x and x != str(os.getpid())]
    return [f"pid {x}" for x in pids]


def _held_windows(dbs) -> list:
    """Windows lists no open files, but it refuses to rename one another process has open (SQLite opens
    without FILE_SHARE_DELETE): rename each store, and its -wal/-shm, aside and straight back. A refusal is a
    holder. The probe name never outlives the check."""
    out = []
    for d in dbs:
        for f in (d, d + "-wal", d + "-shm", d + "-journal"):
            probe = f + ".probe"
            moved = False
            try:
                os.rename(f, probe)
                moved = True
            except FileNotFoundError:
                continue
            except OSError:  # PermissionError (a sharing violation): someone holds it
                out.append(f"open: {f}")
                continue
            finally:
                if moved:
                    try:
                        os.rename(probe, f)
                    except OSError:
                        # a holder opened it in between: it must not stay renamed — keep trying, then say so
                        import time
                        for _ in range(20):
                            time.sleep(0.05)
                            try:
                                os.rename(probe, f)
                                break
                            except OSError:
                                continue
                        else:
                            out.append(f"left at {probe} — rename it back to {f}")
    return out


def _proc_holders(dbs, proc: str = "/proc") -> list:
    """The pids (this one aside) with a descriptor open on one of DBS or its -wal/-shm, from <proc>/<pid>/fd."""
    want = {os.path.realpath(d) for d in dbs}
    want |= {w + s for w in want for s in ("-wal", "-shm", "-journal")}
    out = []
    for pid in sorted((x for x in os.listdir(proc) if x.isdigit()), key=int):
        if int(pid) == os.getpid():
            continue
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue  # gone, or not ours to read
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) in want:
                    out.append(pid)
                    break
            except OSError:
                continue
    return out


def gateway_up() -> bool:
    import socket
    port = int(env("GATEWAY_PORT") or 4747)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False
