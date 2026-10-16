#!/usr/bin/env python3
# COPY of plugins/vault/src/vault/project_match.py: edit that file, never this one (scripts/copies.py)
"""ak project-note binding resolver (Q8 strict v0).

Shared by session-start.py and stop-writeback.py — lives in hooks/ so the
hooks import nothing outside this directory. Deliberately dependency-free
(no PyYAML).

Match rules (open-questions Q8, start-strict envelope):
- `repo` match (normalized git-remote equality) beats any `cwd` match.
- `cwd` match: exact after normalization (expanduser + resolve symlinks),
  except a note cwd that is a path-prefix of the session cwd also matches.
- Most-specific wins: longest matching note-cwd prefix (an exact match is
  the longest possible prefix, so it wins for free). Ties break on note
  path, deterministically.
- Nearest scope wins: the scopes are searched nearest first (vault_root.scopes —
  `<repo>/.ak` before ~/.ak), and the first scope holding any match
  answers. A project's spine lives in its own scope; ~/.ak/projects/ is the
  fallback for a directory whose project has no scope yet.
- A spine binds its own folder: a `type: project` note at a PROJECT scope's
  root with no `cwd`/`repo` binds the directory that holds the scope (`<repo>`
  for `<repo>/.ak`), so a scope committed with its repo binds wherever the
  repo is cloned.
"""
import os
import subprocess
from pathlib import Path

try:  # the src package's copy is `vault.project_match`; a hook's is a top-level module beside its own _brand.py
    from ._brand import HOME_DIR
    from .vault_root import brain_root, home_root, scopes
except ImportError:
    from _brand import HOME_DIR
    from vault_root import brain_root, home_root, scopes

GIT_TIMEOUT_S = 2.0


def vault_root(cwd: str | None = None) -> Path:
    return brain_root(cwd)


def normalize_path(p: str) -> str:
    """expanduser + resolve symlinks; '' on failure."""
    try:
        return str(Path(os.path.expanduser(str(p).strip())).resolve())
    except Exception:
        return ""


def normalize_repo(url: str) -> str:
    """Collapse ssh/https/scp git-remote forms to `host/path` (no .git, no user@)."""
    u = (url or "").strip()
    if not u:
        return ""
    if "://" in u:
        u = u.split("://", 1)[1]  # scheme off (https, ssh, git)
    head = u.split("/", 1)[0]
    if "@" in head:
        u = u.split("@", 1)[1]  # user@ off
        head = u.split("/", 1)[0]
    if ":" in head:
        u = u.replace(":", "/", 1)  # scp-like host:path -> host/path
    if u.endswith(".git"):
        u = u[:-4]
    u = u.rstrip("/")
    host, _, path = u.partition("/")
    return f"{host.lower()}/{path}" if path else host.lower()


def git_remote(cwd: str) -> str:
    """Normalized origin URL for cwd, '' if not a repo / no origin / timeout."""
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_S,
        )
        if out.returncode == 0:
            return normalize_repo(out.stdout.strip())
    except Exception:
        pass
    return ""


def parse_frontmatter(text: str) -> dict | None:
    """Top-level scalars, block lists (`key:` + `- item` lines), inline [a, b].

    Unlike validate.py's flat parser, this one keeps list values — `cwd`/`repo`
    are list-valued. Nested mappings stay out of scope. None if no closed fence.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fm: dict = {}
    pending_key = None  # last scalar-less key, awaiting block-list items
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return fm
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "-" or stripped.startswith("- "):
            if pending_key is not None:
                item = stripped[1:].strip().strip("\"'")
                if item:
                    fm[pending_key].append(item)
            continue
        if line[0] in (" ", "\t"):
            continue  # nested mapping — out of scope
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not val:
            fm[key] = []
            pending_key = key
        elif val.startswith("[") and val.endswith("]"):
            fm[key] = [v.strip().strip("\"'") for v in val[1:-1].split(",") if v.strip()]
            pending_key = None
        else:
            fm[key] = val.strip("\"'")
            pending_key = None
    return None  # unterminated fence


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _is_under(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


# A note's frontmatter is read to its closing fence, not a fixed head. An 8 KB head once read
# An app note's 9.2 KB frontmatter (a long next_action) as no frontmatter at all: the note stopped
# binding its directory and nothing said so. The cap only stops a runaway file.
FRONTMATTER_CAP = 256 * 1024


def _head_to_fence(p: Path) -> str:
    """The file from its start to the end of its frontmatter (or the first 8 KB when it has
    none — enough to see there is no `---`), never more than FRONTMATTER_CAP."""
    with open(p, encoding="utf-8", errors="replace") as f:
        head = f.read(8192)
        if not head.startswith("---"):
            return head
        while "\n---" not in head[3:] and len(head) < FRONTMATTER_CAP:
            more = f.read(8192)
            if not more:
                break
            head += more
    return head


def spine_cwd(note: Path, fm: dict, root: Path) -> str:
    """The folder a project scope's spine binds when it names none: the scope's owner
    (`<repo>` for `<repo>/.ak`). '' for any other note, and for the global kit,
    whose project notes always say where they bind."""
    if fm.get("cwd") or fm.get("repo") or note.parent != root:
        return ""
    try:
        if str(root.resolve()) == str(Path(home_root()).resolve()):
            return ""
    except Exception:
        return ""
    return str(root.parent if root.name == HOME_DIR else root)


def iter_project_notes(root: Path):
    """Yield (path, frontmatter) for every `type: project` note. Skips dotfolders
    (.claude, .obsidian, .git, ...) and `_archive/`. Reads only the head of each file."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "_archive"]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            p = Path(dirpath) / fn
            try:
                head = _head_to_fence(p)
            except Exception:
                continue
            fm = parse_frontmatter(head)
            if fm and fm.get("type") == "project":
                yield p, fm


def resolve_binding(session_cwd: str, session_repo: str | None = None,
                    root: Path | None = None) -> dict | None:
    """Match the session cwd against project-note `cwd`/`repo` frontmatter.

    Returns {"note": Path, "fm": dict, "via": "repo"|"cwd", "matched": str}
    for the winning note, or None.
    """
    chain = [Path(root)] if root else [Path(s) for s in scopes(session_cwd)]
    cwd_norm = normalize_path(session_cwd)
    if session_repo is None:
        session_repo = git_remote(session_cwd)
    for scope in chain:  # nearest first; the first scope with a match answers
        hit = _match_in(scope, cwd_norm, session_repo)
        if hit:
            return hit
    return None


def _match_in(root: Path, cwd_norm: str, session_repo: str) -> dict | None:
    repo_matches: list = []  # (-cwd_specificity, note_path_str, note, fm, matched)
    cwd_matches: list = []
    for note, fm in iter_project_notes(root):
        spec, matched_cwd = -1, None
        implicit = spine_cwd(note, fm, root)
        for raw in _as_list(fm.get("cwd")) + ([implicit] if implicit else []):
            nc = normalize_path(raw)
            if nc and cwd_norm and _is_under(cwd_norm, nc) and len(nc) > spec:
                spec, matched_cwd = len(nc), nc
        note_repos = {normalize_repo(r) for r in _as_list(fm.get("repo"))} - {""}
        if session_repo and session_repo in note_repos:
            repo_matches.append((-spec, str(note), note, fm, session_repo))
        elif matched_cwd:
            cwd_matches.append((-spec, str(note), note, fm, matched_cwd))

    for pool, via in ((repo_matches, "repo"), (cwd_matches, "cwd")):
        if pool:
            pool.sort()
            _, _, note, fm, matched = pool[0]
            return {"note": note, "fm": fm, "via": via, "matched": matched}
    return None
