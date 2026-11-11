#!/usr/bin/env python3
"""Whose is a file the setup uses: theirs, unless something on the laptop says it was installed.

His ruling 2026-11-05: "it's probably theirs if they're not installed via the skills lock or something". The old
default was "not yours unless git proves it", and it lost one tester's own work four times (10-22 a second git email,
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
                callback blob_authorship; a caller without it gets no such rule.
  5. installed  not in git, and byte-identical to a file in an installed copy (an installer copied it into ~/.claude).
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
        self._has_blob = {}        # (repo, sha) → whether the repo holds that blob (git cat-file)
        self._mostly_others = {}   # repo → whether most of its commits are someone else's


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


def load_receipts(roots=(), use_claude=True):
    """roots: the repos and project folders the scan reads; each may hold a skills-lock.json."""
    rc = Receipts()
    rc.repo_roots = [r for r in dict.fromkeys(_real(r) for r in roots) if os.path.exists(os.path.join(r, ".git"))]
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


def _git_has_blob(repo, sha, rc):
    key = (repo, sha)
    if key not in rc._has_blob:
        try:
            r = subprocess.run(["git", "-C", repo, "cat-file", "-e", sha], capture_output=True, timeout=20,
                               stdin=subprocess.DEVNULL)
            rc._has_blob[key] = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            rc._has_blob[key] = False
    return rc._has_blob[key]


def _committed_copy(path, rc, authorship, blob_authorship):
    """The repo whose file `path` is a copy of (rule 4), else None. path is a file, or a skill folder (compared by its
    SKILL.md). A repo counts when it is another repo on this laptop (not the one path is in), most of its commits are someone
    else's, it holds this exact version of the file, and none of the commits that added or removed that version is theirs."""
    f = os.path.join(path, "SKILL.md") if os.path.isdir(path) else path
    try:
        if not COPY_MIN <= os.path.getsize(f) <= COPY_MAX:
            return None
        with open(f, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    sha = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()   # git's own name for these bytes
    for root in rc.repo_roots:
        if _under(_real(f), root) or not _git_has_blob(root, sha, rc):
            continue
        if root not in rc._mostly_others:
            rc._mostly_others[root] = False      # authorship(root) may ask who owns a file of root: not this question again
            a = authorship(root)
            rc._mostly_others[root] = bool(a and a.get("commits") and 2 * (a.get("theirs") or 0) < a["commits"])
        if not rc._mostly_others[root]:
            continue
        b = blob_authorship(root, sha)
        if b and b.get("commits") and not b.get("theirs"):
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


def owner(path, rc, authorship=None, written_in_logs=None, blob_authorship=None):
    """{"owner": "installed" | "teammate" | "theirs", "how": "marketplace" | "skills-lock" | "git" |
    "committed copy of someone else's file" | "same as an installed copy" | "published package fields" | "unpacked in one burst" | "no receipt",
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
        if copy_of:
            return {"owner": "installed", "how": "committed copy of someone else's file", "source": copy_of, **a}
        return {"owner": "theirs", "how": "git", "source": look if look != path else None, **a}
    # nothing in git says whose it is: a byte-identical copy of an installed file is installed too (an installer copied
    # it). Asked only here, so a file of theirs in git never reads installed because they also published it.
    same = _same_as_installed(look, rc)
    if same:
        return {"owner": "installed", "how": "same as an installed copy", "source": same}
    unpacked = _unpacked_skill(look, rc, written_in_logs)
    if unpacked:
        return {"owner": "installed", "how": unpacked[0], "source": unpacked[1]}
    return {"owner": "theirs", "how": "no receipt", "source": look if look != path else None}


def is_theirs(path, rc, authorship=None, written_in_logs=None, blob_authorship=None):
    return owner(path, rc, authorship, written_in_logs, blob_authorship)["owner"] == "theirs"


if __name__ == "__main__":
    rc = load_receipts(roots=[os.getcwd()])
    print("receipts read:", ", ".join(rc.how), file=sys.stderr)
    for p in sys.argv[1:]:
        print(json.dumps({"path": p, **owner(p, rc)}))
