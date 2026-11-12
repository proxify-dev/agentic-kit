#!/usr/bin/env python3
"""Whose is a file the setup uses: theirs, unless something on the laptop says it was installed.

The ruling 2026-11-05: "it's probably theirs if they're not installed via the skills lock or something". The old
default was "not yours unless git proves it", and it lost one person's own work four times (10-22 a second git email,
10-23 skills linked in from her team repo, 11-04 the folder a chat started in, 11-05 skills/memory/settings with no
id). Each fix taught the scan one more folder layout. Installs leave a receipt; nothing else needs guessing.

The rule, in this order:
  1. installed  Claude Code installed it from a marketplace someone published (`claude plugin list --json`; fallback
                ~/.claude/plugins/installed_plugins.json + known_marketplaces.json). A plugin read from a folder on
                this laptop (a "directory" marketplace, an inline --plugin-dir) is no receipt: its source folder goes
                through 3 and 4.
  2. installed  a skills lock lists it (~/.agents/.skill-lock.json for global installs; a project's skills-lock.json
                for that project's skills).
  3. teammate   git: most of its commits are someone else's.
  4. installed  git says it is theirs, but it is a copy of someone else's file: byte-identical (a skill folder: its SKILL.md) to a
                version of a file in another repo on this laptop whose commits are mostly someone else's (a clone of the
                upstream project), and nobody of theirs ever committed that version there. The version may be an older one
                of the clone's history: upstream moved on after they copied. how = "committed copy of someone else's file".
                A file they edited after copying is not byte-identical to any version there, so it stays theirs. Needs the
                callback blob_authorship; a caller without it gets no such rule. Three guards, for both 4 and 4b: the
                copy came after the original (their file was not in their hands before that repo first held the version:
                a teammate who copied THEIR file later does not make it a copy), their own session logs show no Write/Edit of
                a file in that skill folder (a fork they reworked), and the repos looked in include clones below a project
                folder that the scan passed as `clones` (not projects, so never a marketplace claim).
  4b. installed git has nothing on it (untracked, ignored, or in no repo) and it is that same kind of copy: byte-identical to a
                version at the head of a repo whose commits are mostly someone else's. how = "copy of someone else's file,
                not committed here". 4's reason it did not fire for a file git has nothing on: it ran only when git said
                "theirs", and an untracked file never reaches that answer.
  5. installed  not in git, and byte-identical to a file in an installed copy (an installer copied it into ~/.claude): a
                published marketplace's files, a skill the global lock installed, or a skill folder some project's
                skills-lock.json lists (a receipt that lives in another folder than the copy).
  6. installed  a skill folder (a dir with SKILL.md) not in git, with no receipt, that was unpacked rather than written
                (11-06: a skill copied in by hand leaves no receipt, and it read "theirs"). Either
                  a) its SKILL.md frontmatter carries publishing fields of the Agent Skills format: `license:` or
                     `version:` / `metadata.version`. A package sets them; they are a field, never a word in the text.
                     how = "published package fields"
                  b) it was unpacked in one burst: 2+ files whose modification times all fall within a span shorter
                     than one second per file, and no file changed after it. how = "unpacked in one burst"
                Never when the person's own Claude Code logs show a Write/Edit creating or changing a file of that
                skill: their agent wrote it, however fast and whatever its frontmatter says. That is the optional
                callback written_in_logs(path) -> bool; a caller with no logs leaves it out and (a)/(b) still apply.
                A skill they reworked after installing it (files written minutes apart) stays theirs: only a burst
                nobody touched since reads installed.
  7. theirs     everything else, git or no git.

Every caller asks owner(); no file decides ownership on its own (11-05: the same question was answered in six places
and a fix in one never reached the others). Test: test_ownership.py.

    rc = load_receipts(roots=[...repos the scan read...])
    owner(path, rc, authorship=lambda p: {"commits": n, "theirs": m},   # authorship → None when p is in no repo
          written_in_logs=lambda folder: True,   # does a Write/Edit in their logs touch a file under this skill folder?
          blob_authorship=lambda repo, sha: {"commits": n, "theirs": m})   # the commits of `repo` that added or removed the
                                                                           # blob <sha>; None when it never held it

Run: python3 -I ownership.py PATH...     prints each path's owner on this laptop
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
PLUGIN_CACHE = os.path.join(HOME, ".claude", "plugins", "cache")
GLOBAL_LOCK = os.path.join(HOME, ".agents", ".skill-lock.json")
PROJECT_LOCK = "skills-lock.json"
# where a lock's skills land: the global installer links ~/.claude/skills/<name> to ~/.agents/skills/<name>
GLOBAL_SKILL_DIRS = (os.path.join(HOME, ".agents", "skills"), os.path.join(HOME, ".claude", "skills"))


def _json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _real(p):
    try:
        return os.path.realpath(p)
    except OSError:
        return p


def _under(path, folder):
    return bool(folder) and (path == folder or path.startswith(folder.rstrip("/") + "/"))


def _skill_name(path, skill_dirs):
    """<name> when path sits at or under <one of skill_dirs>/<name>."""
    for d in skill_dirs:
        if _under(path, d) and path != d:
            return path[len(d.rstrip("/")) + 1:].split("/")[0]
    return None


class Receipts:
    """What this laptop says was installed. Built once per scan."""

    def __init__(self):
        self.installed_dirs = {}   # real install folder → "name@marketplace" (published marketplaces only)
        self.source_dirs = {}      # real install folder → the folder on this laptop it was read from (directory marketplaces)
        self.global_lock = {}      # skill name → source, from ~/.agents/.skill-lock.json
        self.project_locks = {}    # project folder → {skill name → source}
        self.how = []              # which receipts were read, for the page's "how we know"
        self.copy_roots = []       # folders holding installed copies, for the same-content receipt (built lazily)
        self._copies = None        # (basename, size) → [paths] in copy_roots
        self.repo_roots = []       # the repos the scan read: where a committed copy of someone else's file may come from
        self.clone_roots = []      # repos found below a project folder (clones of someone else's code): looked in for a copy, never a project
        self._first_seen = {}      # (repo, blob) → the earliest commit time that added the blob there
        self._blob_by = {}         # (repo, blob) → the callback's answer about the commits that added it
        self._heads = {}           # clone root → {blob name: a path holding it} at its head commit (git ls-tree), built when a copy is looked for
        self._has_blob = {}        # (repo, sha) → whether the repo holds that blob (git cat-file)
        self._mostly_others = {}   # repo → whether most of its commits are someone else's
        self._objects = {}         # repo → frozenset of the object names it holds (only for repos that are mostly someone else's), or None


def _plugins_from_claude():
    """`claude plugin list --json`: Claude Code's own answer (installPath, readFromFolder, the marketplace id)."""
    try:
        r = subprocess.run(["claude", "plugin", "list", "--json"], capture_output=True, text=True, timeout=20,
                           stdin=subprocess.DEVNULL)
        out = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
        return out if isinstance(out, list) else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _plugins_from_files():
    """The files `claude plugin list` reads, for a Claude Code without --json."""
    inst = _json(os.path.join(HOME, ".claude", "plugins", "installed_plugins.json")) or {}
    inst = inst.get("plugins", inst) if isinstance(inst, dict) else {}
    out = []
    for pid, recs in inst.items():
        for r in recs if isinstance(recs, list) else [recs]:
            if isinstance(r, dict):
                out.append({"id": pid, "installPath": r.get("installPath")})
    return out


def _git_config(root):
    """The text of a repo's .git/config, read from the files (a worktree's .git file points at its main repo)."""
    g = os.path.join(root, ".git")
    if os.path.isfile(g):
        try:
            with open(g) as f:
                t = f.read().split("gitdir:")[-1].strip()
        except OSError:
            return ""
        g = t if os.path.isabs(t) else os.path.normpath(os.path.join(root, t))
        common = os.path.join(g, "commondir")
        if os.path.isfile(common):
            try:
                with open(common) as f:
                    g = os.path.normpath(os.path.join(g, f.read().strip()))
            except OSError:
                pass
    try:
        with open(os.path.join(g, "config")) as f:
            return f.read()
    except OSError:
        return ""


def _clones(roots):
    """"owner/repo" (lower case) → a clone of it among the folders the scan read."""
    out = {}
    for r in roots:
        for url in re.findall(r"^\s*url\s*=\s*(\S+)", _git_config(r), re.M):
            m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
            if m:
                out.setdefault(m.group(1).lower(), r)
    return out


def _marketplaces(roots=()):
    """marketplace name → its source on this laptop, else None. A "directory" marketplace is a folder here; a
    marketplace published from a repo they have a clone of is their own too (their toolset published on GitHub)."""
    km = _json(os.path.join(HOME, ".claude", "plugins", "known_marketplaces.json")) or {}
    clones = _clones(roots)
    out = {}
    for name, m in km.items():
        src = (m or {}).get("source") or {}
        repo = (src.get("repo") or "").lower() or (re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", src.get("url") or "")
                                                   or [None, ""])[1].lower()
        out[name] = src.get("path") if src.get("source") == "directory" else clones.get(repo) if repo else None
    return out


def _directory_plugin_source(mk_dir, name):
    """Where plugin <name> lives inside a directory marketplace: its marketplace.json entry, else plugins/<name>."""
    man = _json(os.path.join(mk_dir, ".claude-plugin", "marketplace.json")) or {}
    for p in man.get("plugins") or []:
        if p.get("name") == name and isinstance(p.get("source"), str):
            return os.path.normpath(os.path.join(mk_dir, p["source"]))
    for c in (os.path.join(mk_dir, "plugins", name), os.path.join(mk_dir, name)):
        if os.path.isdir(c):
            return c
    return None


def load_receipts(roots=(), use_claude=True, clones=()):
    """roots: the repos and project folders the scan reads; each may hold a skills-lock.json.
    clones: more repos on this laptop (found below a project folder, where the scan does not read them as projects):
    only a copy of someone else's file is looked for in them. They are never roots: a marketplace published from a
    repo in `roots` is the person's own, one published from a clone is not."""
    rc = Receipts()
    rc.repo_roots = [r for r in dict.fromkeys(_real(r) for r in roots) if os.path.exists(os.path.join(r, ".git"))]
    rc.clone_roots = [r for r in dict.fromkeys(_real(r) for r in clones) if r not in rc.repo_roots and os.path.exists(os.path.join(r, ".git"))]
    # ask Claude Code only when something is installed (a laptop with no plugins folder has nothing to list) and not
    # in tests (AGENTIC_SETUP_NO_CLAUDE=1: a made-up HOME must never reach the real `claude`)
    use_claude = use_claude and not os.environ.get("AGENTIC_SETUP_NO_CLAUDE") and os.path.isdir(
        os.path.join(HOME, ".claude", "plugins"))
    plugins = _plugins_from_claude() if use_claude else None
    rc.how.append("claude plugin list --json" if plugins is not None else "installed_plugins.json")
    if plugins is None:
        plugins = _plugins_from_files()
    mks = _marketplaces(roots)
    for p in plugins:
        pid, inst = p.get("id") or "", p.get("installPath")
        name, _, mk = pid.partition("@")
        src = p.get("readFromFolder")
        if not src and mks.get(mk):
            src = _directory_plugin_source(mks[mk], name)
        if inst:
            if src or mk == "inline" or mk in mks and mks[mk]:
                rc.source_dirs[_real(inst)] = _real(src) if src else None
            else:
                rc.installed_dirs[_real(inst)] = pid
        if src and src != inst:
            rc.source_dirs.setdefault(_real(src), _real(src))
    # everything Claude Code cached from a published marketplace (other versions, plugins since removed) came from it
    for mk, local in mks.items():
        if not local and mk:
            rc.installed_dirs.setdefault(_real(os.path.join(PLUGIN_CACHE, mk)), f"@{mk}")
    # where installed copies live, for "a byte-identical copy of an installed file" (an installer that copies a plugin's
    # agent or skill into ~/.claude leaves no other trace)
    rc.copy_roots = sorted(set(rc.installed_dirs) | {
        _real(os.path.join(HOME, ".claude", "plugins", "marketplaces", mk)) for mk, local in mks.items() if not local and mk}
        | {_real(d) for d in GLOBAL_SKILL_DIRS[:1]})
    gl = _json(GLOBAL_LOCK)
    if gl:
        rc.global_lock = {k: (v or {}).get("source") for k, v in (gl.get("skills") or {}).items()}
        rc.how.append("~/.agents/.skill-lock.json")
    for r in roots:
        lk = _json(os.path.join(r, PROJECT_LOCK))
        if lk:
            rc.project_locks[_real(r)] = {k: (v or {}).get("source") for k, v in (lk.get("skills") or {}).items()}
    if rc.project_locks:
        rc.how.append(f"{len(rc.project_locks)} project skills-lock.json")
        # a lock is a receipt for the skill folders of its own project; the same skill, byte for byte, in any other folder
        # (a user-level skills folder, another project) is a copy of what that lock installed
        rc.copy_roots = sorted(set(rc.copy_roots) | {
            _real(os.path.join(proj, d, "skills", name)) for proj, skills in rc.project_locks.items() for name in skills
            for d in (".agents", ".claude", "") if os.path.isdir(os.path.join(proj, d, "skills", name))})
    return rc


def _receipt(path, rc):
    """(how, source) when a receipt says path was installed, else None. path and its link target are both checked:
    a skill linked into ~/.claude/skills from a lock install is installed; one linked in from their own repo is not."""
    for p in dict.fromkeys((path, _real(path))):
        for d, pid in rc.installed_dirs.items():
            if _under(p, d):
                return "marketplace", pid
        name = _skill_name(p, GLOBAL_SKILL_DIRS)
        if name and name in rc.global_lock:
            return "skills-lock", rc.global_lock[name]
        for proj, skills in rc.project_locks.items():
            if _under(p, proj):
                name = _skill_name(p, [os.path.join(proj, d, "skills") for d in (".agents", ".claude", "")])
                if name and name in skills:
                    return "skills-lock", skills[name]
    return None


COPY_EXT = (".md", ".sh", ".py", ".js", ".mjs", ".ts", ".json", ".yaml", ".yml", ".toml")
COPY_MIN, COPY_MAX = 64, 300_000   # under 64 bytes two files can match by chance (empty, a stub, one line); an agent or skill file names itself in its first lines


def _sha(p):
    import hashlib
    try:
        with open(p, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return None


def _copies(rc):
    """(basename, size) → paths of the files in the installed copies (plugin cache and marketplaces from published
    sources, lock-installed skills). Built once, on the first file that has no other receipt."""
    if rc._copies is None:
        rc._copies = {}
        for root in rc.copy_roots:
            for dp, dns, fns in os.walk(root):
                dns[:] = [d for d in dns if d not in (".git", "node_modules", "__pycache__", ".venv")]
                for fn in fns:
                    if fn.endswith(COPY_EXT):
                        fp = os.path.join(dp, fn)
                        try:
                            n = os.path.getsize(fp)
                        except OSError:
                            continue
                        if COPY_MIN <= n <= COPY_MAX:
                            rc._copies.setdefault((fn, n), []).append(fp)
    return rc._copies


def _same_as_installed(path, rc):
    """The installed file this one is a byte-identical copy of, else None. A skill folder is compared by its SKILL.md."""
    f = os.path.join(path, "SKILL.md") if os.path.isdir(path) else path
    if not os.path.isfile(f):
        return None
    try:
        n = os.path.getsize(f)
    except OSError:
        return None
    if not COPY_MIN <= n <= COPY_MAX:
        return None
    cands = [c for c in _copies(rc).get((os.path.basename(f), n), []) if _real(c) != _real(f)]
    if not cands:
        return None
    h = _sha(f)
    return next((c for c in cands if _sha(c) == h), None)


MAX_OBJECT_SET = 3_000_000   # a repo with more objects than this is asked sha by sha, not listed whole (a huge clone would cost more memory than it saves)


def _ask_blobs(repo, shas, rc):
    """Fill rc._has_blob for every sha not asked of this repo yet, with ONE git process: `git cat-file --batch-check` prints one
    line per name it is given, "<sha> missing" or "<sha> <type> <size>". The same answer as `git cat-file -e <sha>` (the repo holds
    the object, whatever its type). A git that fails or answers with a different number of lines than it was asked is asked again one
    sha at a time, so a surprise costs time, never an answer."""
    todo = [s for s in dict.fromkeys(shas) if (repo, s) not in rc._has_blob]
    if not todo:
        return
    try:
        r = subprocess.run(["git", "-C", repo, "cat-file", "--batch-check"], input="".join(s + "\n" for s in todo),
                           capture_output=True, text=True, timeout=60)
        lines = r.stdout.splitlines()
        if r.returncode == 0 and len(lines) == len(todo):
            for s, line in zip(todo, lines):
                rc._has_blob[(repo, s)] = not line.endswith(" missing")
            return
        if r.returncode == 128 and "not a git repository" in r.stderr:     # a folder with a stray .git: `cat-file -e` says no to every name
            for s in todo:
                rc._has_blob[(repo, s)] = False
            return
    except (OSError, subprocess.SubprocessError):
        pass
    for s in todo:
        try:
            r = subprocess.run(["git", "-C", repo, "cat-file", "-e", s], capture_output=True, timeout=20,
                               stdin=subprocess.DEVNULL)
            rc._has_blob[(repo, s)] = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            rc._has_blob[(repo, s)] = False


def _repo_objects(repo, rc):
    """The names of every object the repo holds (and its alternates), as a set, read with one git process; None when the repo is too big
    to list (MAX_OBJECT_SET) or git cannot say. Kept in rc._objects. Used only for a repo that is mostly someone else's (see
    _git_has_blob): in a scan that asks about hundreds of files, one listing of a few repos replaces a git process per file and repo
    (11-12: 21,192 `git cat-file -e` calls, 392 s of one 491 s step)."""
    if repo not in rc._objects:
        got = None
        try:
            c = subprocess.run(["git", "-C", repo, "count-objects", "-v"], capture_output=True, text=True, timeout=20,
                               stdin=subprocess.DEVNULL)
            n = sum(int(v) for k, _, v in (ln.partition(": ") for ln in c.stdout.splitlines()) if k in ("count", "in-pack") and v.strip().isdigit())
            if c.returncode == 0 and n <= MAX_OBJECT_SET:
                r = subprocess.run(["git", "-C", repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)"],
                                   capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
                if r.returncode == 0:
                    got = frozenset(r.stdout.split())
        except (OSError, subprocess.SubprocessError, ValueError):
            got = None
        rc._objects[repo] = got
    return rc._objects[repo]


def warm_objects(rc):
    """Read the object names of every repo known to be mostly someone else's, once, so callers that fork afterwards share them."""
    for repo in rc.repo_roots:
        if rc._mostly_others.get(repo):
            _repo_objects(repo, rc)
    for repo in rc.clone_roots:      # the nested clones: one ls-tree each, so no worker asks git about a clone again
        _head_blobs(repo, rc)


def _git_has_blob(repo, sha, rc):
    key = (repo, sha)
    if key not in rc._has_blob:
        objs = _repo_objects(repo, rc) if rc._mostly_others.get(repo) else None    # the repos rule 4 can match are few; a set for each repo would cost memory
        if objs is not None:
            rc._has_blob[key] = sha in objs
        else:
            _ask_blobs(repo, [sha], rc)
    return rc._has_blob[key]


def _git_times(repo, sha):
    """The commit times (seconds) of the commits of `repo` that added or removed the blob <sha>; [] when none or no git."""
    try:
        r = subprocess.run(["git", "-C", repo, "log", "--all", f"--find-object={sha}", "--format=%ct", "-n", "50"],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        return [int(x) for x in r.stdout.split() if x.isdigit()] if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []


def _head_blobs(root, rc):
    """{blob name: a path holding it} for everything at the head of `root` (one `git ls-tree`), so that many clones can be
    asked "do you hold this file?" without one git call per clone and file."""
    if root not in rc._heads:
        names = {}
        try:
            r = subprocess.run(["git", "-C", root, "ls-tree", "-r", "HEAD"], capture_output=True, text=True, timeout=60,
                               stdin=subprocess.DEVNULL)
            for line in r.stdout.splitlines() if r.returncode == 0 else ():
                meta, _, name = line.partition("\t")
                meta = meta.split()
                if len(meta) == 3:
                    names.setdefault(meta[2], name)
        except (OSError, subprocess.SubprocessError):
            pass
        rc._heads[root] = names
    return rc._heads[root]


def _held_since(f, tracked):
    """When `f` came into their hands: the earliest commit that added its path in the repo it sits in, else (git has
    nothing on it: untracked, ignored, in no repo) its modification time. Asked of the path only: a search of the whole
    history for the bytes would take seconds in a big repo for every file."""
    d, name = os.path.split(os.path.abspath(f))
    try:
        if tracked:
            r = subprocess.run(["git", "-C", d, "log", "--format=%ct", "--diff-filter=A", "--", name], capture_output=True, text=True,
                               timeout=60, stdin=subprocess.DEVNULL)
            times = [int(x) for x in r.stdout.split() if x.isdigit()]
            if times:
                return min(times)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return os.stat(f).st_mtime
    except OSError:
        return None


def _first_held(repo, sha, rc, path=None):
    """A time no earlier than the moment `repo` first held the blob: the last commit on `path` (where the head holds it) set
    this version, so the repo held it by then; else the earliest commit that added it. Never too early, so a copy that
    came after it came after the original."""
    if (repo, sha) not in rc._first_seen:
        t = None
        if path:
            try:
                r = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%ct", "--", path], capture_output=True, text=True,
                                   timeout=60, stdin=subprocess.DEVNULL)
                t = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None
            except (OSError, subprocess.SubprocessError):
                pass
        if t is None:
            t = min(_git_times(repo, sha) or [None], key=lambda x: (x is None, x))
        rc._first_seen[(repo, sha)] = t
    return rc._first_seen[(repo, sha)]


def _copy_came_after(f, sha, repo, rc, tracked, path=None):
    """True when the file `f` came into their hands no earlier than `repo` held that version. A copy follows its original:
    when the other repo's copy is the later one (a teammate took their file), or no time is known, it is not a proof. A
    path they added earlier than that stays theirs even if its bytes were replaced since: the safe side."""
    mine = _held_since(f, tracked)
    other = _first_held(repo, sha, rc, path)
    return mine is not None and other is not None and mine >= other


def _committed_copy(path, rc, authorship, blob_authorship, tracked=True):
    """The repo whose file `path` is a copy of (rules 4 and 4b), else None. path is a file, or a skill folder (compared by its
    SKILL.md). A repo counts when it is another repo on this laptop (not the one path is in), most of its commits are someone
    else's, it holds this exact version of the file, none of the commits that added or removed that version is theirs, and
    the copy followed the original (_copy_came_after). `tracked`: git has commits on the file (rule 4), else it has none (4b)."""
    f = os.path.join(path, "SKILL.md") if os.path.isdir(path) else path
    try:
        if not COPY_MIN <= os.path.getsize(f) <= COPY_MAX:
            return None
        with open(f, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    sha = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()   # git's own name for these bytes
    clones = [r for r in rc.clone_roots if sha in _head_blobs(r, rc)]     # one ls-tree per clone, kept in rc._heads (warmed before the workers fork)
    real_dir = _real(os.path.dirname(os.path.abspath(f)))   # once, not once per repo (realpath of the same file in 90 repos: 18 s on one laptop)
    for root in rc.repo_roots + clones:
        if _under(real_dir, root) or rc._mostly_others.get(root) is False:   # known not to be someone else's: it holds no source of a copy
            continue
        if root in rc.repo_roots and not _git_has_blob(root, sha, rc):    # a clone was listed by its head just above: it holds the blob
            continue
        if root not in rc._mostly_others:
            rc._mostly_others[root] = False      # authorship(root) may ask who owns a file of root: not this question again
            a = authorship(root)
            rc._mostly_others[root] = bool(a and a.get("commits") and 2 * (a.get("theirs") or 0) < a["commits"])
        if not rc._mostly_others[root]:
            continue
        if (root, sha) not in rc._blob_by:
            rc._blob_by[(root, sha)] = blob_authorship(root, sha)
        b = rc._blob_by[(root, sha)]
        if b and b.get("commits") and not b.get("theirs") and _copy_came_after(f, sha, root, rc, tracked, _head_blobs(root, rc).get(sha) if root in rc.clone_roots else None):
            return root
    return None


SKIP_DIRS = (".git", "node_modules", "__pycache__", ".venv")
SKIP_FILES = (".DS_Store", "Thumbs.db")   # the file manager writes these after the unpacking, whoever unpacked
BURST_SECONDS_PER_FILE = 1.0              # an unpacking writes dozens of files a second; a person writes one every few seconds
BURST_MAX_FILES = 5000


def _skill_folder(path):
    """The real folder of the skill path is, or is inside: the nearest folder at or above it that holds a SKILL.md,
    stopping at a repo root or the home folder. None when path belongs to no skill."""
    cur = _real(path)
    if not os.path.isdir(cur):
        cur = os.path.dirname(cur)
    for _ in range(6):
        if os.path.isfile(os.path.join(cur, "SKILL.md")):
            return cur
        up = os.path.dirname(cur)
        if up == cur or cur == _real(HOME) or os.path.exists(os.path.join(cur, ".git")):
            return None
        cur = up
    return None


def _tracked_by_git(folder):
    """True when git tracks anything in folder (a skill committed to a repo is decided by its commits, not by when its
    files were written: a checkout writes every file in a burst). Inside a repo but untracked or ignored: False."""
    d = folder
    while not os.path.exists(os.path.join(d, ".git")):
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up
    try:
        r = subprocess.run(["git", "-C", folder, "ls-files", "--", "."], capture_output=True, text=True, timeout=20,
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0:   # a stray .git file that is no repo (found on a laptop) means in no repo; any other error: can't tell
            return not re.search(r"not a git repository|invalid gitfile", r.stderr)
        return bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return True


def _publishing_fields(skill_md):
    """["license: Apache 2.0", "version: 4.8.0"] for the publishing fields of the Agent Skills format that SKILL.md's
    frontmatter sets to something: top-level `license` and `version`, and `metadata.version`. Read as text (members have
    no YAML library); a line inside the description or the body is never looked at."""
    try:
        with open(skill_md, encoding="utf-8", errors="replace") as f:
            head = f.read(16384)
    except OSError:
        return []
    m = re.match(r"\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", head, re.S)
    if not m:
        return []
    out, in_meta = [], False
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] not in " \t":
            in_meta = bool(re.match(r"metadata\s*:\s*$", line))
            top = re.match(r"(license|version)\s*:\s*(\S.*?)\s*$", line)
            if top:
                out.append(f"{top.group(1)}: {top.group(2).strip(chr(34) + chr(39))[:40]}")
            inline = re.match(r"metadata\s*:\s*\{.*?\bversion\s*:\s*[\"']?([^,\"'}\s]+)", line)
            if inline:
                out.append(f"metadata.version: {inline.group(1)[:40]}")
        elif in_meta:
            sub = re.match(r"\s+version\s*:\s*(\S.*?)\s*$", line)
            if sub:
                out.append(f"metadata.version: {sub.group(1).strip(chr(34) + chr(39))[:40]}")
    return out


def _burst(folder):
    """"59 files written within 13.1 s, none changed since" when 2+ files of the folder were all written within a span
    shorter than BURST_SECONDS_PER_FILE a file, else None. Every file counts, so one edited later makes the span long."""
    times = []
    for dp, dns, fns in os.walk(folder):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn in SKIP_FILES:
                continue
            try:
                times.append(os.stat(os.path.join(dp, fn)).st_mtime)
            except OSError:
                continue
            if len(times) > BURST_MAX_FILES:
                return None
    if len(times) < 2:
        return None
    span = max(times) - min(times)
    if span < BURST_SECONDS_PER_FILE * len(times):
        return f"{len(times)} files written within {span:.1f} s, none changed since"
    return None


def _unpacked_skill(path, rc, written_in_logs=None):
    """(how, evidence) when path is in a skill folder that was unpacked (rule 6), else None."""
    folder = _skill_folder(path)
    if not folder:
        return None
    # a plugin cache copy and a directory marketplace's folder are decided elsewhere (rules 1-3, their source folder)
    for d in (PLUGIN_CACHE, os.path.join(HOME, ".claude", "plugins"), *rc.source_dirs, *filter(None, rc.source_dirs.values())):
        if _under(folder, _real(d)):
            return None
    if _tracked_by_git(folder):
        return None
    fields = _publishing_fields(os.path.join(folder, "SKILL.md"))
    got = ("published package fields", "SKILL.md frontmatter sets " + ", ".join(fields)) if fields else None
    if not got:
        burst = _burst(folder)
        got = ("unpacked in one burst", burst) if burst else None
    if not got:
        return None
    if written_in_logs:
        for p in dict.fromkeys((folder, os.path.abspath(path if os.path.isdir(path) else os.path.dirname(path)))):
            try:
                if written_in_logs(p):
                    return None   # their agent wrote it
            except Exception:  # noqa: BLE001 - a log that can't be read must not turn a skill of theirs into an install
                return None
    return got


def _agent_wrote_in(path, written_in_logs):
    """True when their own logs show a Write/Edit in the skill folder `path` is, or is inside (or in `path` itself)."""
    if not written_in_logs:
        return False
    folder = _skill_folder(path)
    for p in dict.fromkeys(x for x in (folder, os.path.abspath(path if os.path.isdir(path) else os.path.dirname(path))) if x):
        try:
            if written_in_logs(p):
                return True
        except Exception:  # noqa: BLE001 - a log that can't be read must not turn a skill of theirs into an install
            return True
    return False


def owner(path, rc, authorship=None, written_in_logs=None, blob_authorship=None):
    """{"owner": "installed" | "teammate" | "theirs", "how": "marketplace" | "skills-lock" | "git" |
    "committed copy of someone else's file" | "copy of someone else's file, not committed here" | "same as an installed copy" | "published package fields" | "unpacked in one burst" | "no receipt",
    "source": what it was installed from or read from (for rule 6, the evidence), "commits", "theirs"}.
    authorship(path) → {"commits", "theirs"} or None when the file is in no repo.
    blob_authorship(repo, sha) → {"commits", "theirs"} for the commits of that repo that added or removed the file version
    <sha>, or None when it never held it; leave it out and rule 4 does not apply.
    written_in_logs(folder) → True when a Write/Edit in the person's own session logs created or changed a file under
    that skill folder; leave it out when the caller has no logs."""
    if not path:
        return {"owner": "theirs", "how": "no receipt", "source": None}
    path = os.path.expanduser(str(path))
    got = _receipt(path, rc)
    if got:
        return {"owner": "installed", "how": got[0], "source": got[1]}
    # a plugin copied into the cache from a folder on this laptop: ask git about the folder it was read from
    look = path
    for d, src in rc.source_dirs.items():
        if src and src != d and _under(_real(path), d):
            look = os.path.join(src, _real(path)[len(d) + 1:]) if _real(path) != d else src
            break
    a = authorship(look) if authorship else None
    if a and a.get("commits"):
        if 2 * (a.get("theirs") or 0) < a["commits"]:
            return {"owner": "teammate", "how": "git", "source": look if look != path else None, **a}
        # theirs by git, yet a copy of a version of someone else's file in another repo: they committed what they took
        copy_of = _committed_copy(look, rc, authorship, blob_authorship) if authorship and blob_authorship else None
        if copy_of and not _agent_wrote_in(look, written_in_logs):
            return {"owner": "installed", "how": "committed copy of someone else's file", "source": copy_of, **a}
        return {"owner": "theirs", "how": "git", "source": look if look != path else None, **a}
    # nothing in git says whose it is: a byte-identical copy of an installed file is installed too (an installer copied
    # it). Asked only here, so a file of theirs in git never reads installed because they also published it.
    same = _same_as_installed(look, rc)
    if same:
        return {"owner": "installed", "how": "same as an installed copy", "source": same}
    copy_of = _committed_copy(look, rc, authorship, blob_authorship, tracked=False) if authorship and blob_authorship else None
    if copy_of and not _agent_wrote_in(look, written_in_logs):
        return {"owner": "installed", "how": "copy of someone else's file, not committed here", "source": copy_of}
    unpacked = _unpacked_skill(look, rc, written_in_logs)
    if unpacked:
        return {"owner": "installed", "how": unpacked[0], "source": unpacked[1]}
    return {"owner": "theirs", "how": "no receipt", "source": look if look != path else None}


_CLAUDE_CODE_COPY = re.compile(r"(?:^|/)\.claude[^/]*/plugins/(?:cache|marketplaces)/")


def claude_code_copy(path):
    """True when path sits in Claude Code's own plugin storage, `<config folder>/plugins/cache/` (a copy per installed
    version) or `<config folder>/plugins/marketplaces/` (a clone of each marketplace). Whatever plugin it is, that file is
    a copy Claude Code made, never a file the person wrote there: a plugin of theirs is counted where its source lives (their
    repo), so counting the copy as well credits the same file twice, and a plugin of someone else's credits them with
    its files. Decided by the folder alone (any home, any version, any plugin name), so it needs no receipt, git or
    network, and it holds on a run scanned before this rule existed. For files that are counted by what they say
    (instruction files); `owner()` still answers whose a plugin is."""
    p = os.path.expanduser(str(path or "")).replace(os.sep, "/")
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    return bool(_CLAUDE_CODE_COPY.search(p)) or bool(
        cfg and any(p.startswith(os.path.normpath(cfg).replace(os.sep, "/") + f"/plugins/{d}/") for d in ("cache", "marketplaces")))


def is_theirs(path, rc, authorship=None, written_in_logs=None, blob_authorship=None):
    return owner(path, rc, authorship, written_in_logs, blob_authorship)["owner"] == "theirs"


if __name__ == "__main__":
    rc = load_receipts(roots=[os.getcwd()])
    print("receipts read:", ", ".join(rc.how), file=sys.stderr)
    for p in sys.argv[1:]:
        print(json.dumps({"path": p, **owner(p, rc)}))
