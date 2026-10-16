#!/usr/bin/env python3
"""ak file-context (PreToolUse · Read): a pointer to what memory says about the file
about to be read.

It used to put up to ten observer rows beside every Read. Measured over 623 firings, the
agent used one of them once: rows nobody asked for are context rot. Now it says one line
when the store has rows that touched the file, and the command that shows them:

    3 past observations on this file: observer search --files ~/agentic-kit/app/x.ts

`--files` with an absolute (or `~/`) path matches exactly the rows counted here
(obs_index.touching_ids shares `_touches`). Nothing when the store has no rows for the
path, nothing when there is no store. Never blocks a Read, never decides anything: a
PreToolUse answer with no `decision` lets the tool run.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store_common  # noqa: E402
from _brand import posix, utf8_stdio  # noqa: E402
from capture_common import automated_entrypoint, git_root, labels_with_aliases, project_label  # noqa: E402
from prompt_text import prompt  # noqa: E402

WINDOW = 200  # the newest rows whose file lists name the basename; the count is of these


def _jlist(v) -> list:
    try:
        x = json.loads(v) if v else []
        return [str(i) for i in x] if isinstance(x, list) else []
    except Exception:
        return []


def _repo_rel(path: str) -> str | None:
    """`path` relative to the repo holding it, or None outside a repo (or for a file whose
    repo is gone from disk)."""
    root = git_root(os.path.dirname(path))
    if not root:
        return None
    try:
        return os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    except ValueError:
        return None


def _touches(entry: str, path: str, rel: str | None, same_project: bool) -> bool:
    """Does a stored path name the file being read?

    The observer stores what the model wrote — absolute, or relative to wherever that session
    stood — so a bare `README.md` names a README in *some* repo. It used to count as a match
    for every README on the machine (and `CLAUDE.md`, `package.json` …): opening ~/agentic-kit's
    README surfaced rows about unrelated repos. Now:
      absolute  the same file, or the same repo-relative path in the same project (a checkout,
                its worktree and the release clone are one file to memory)
      relative  only a row of the same project, matched against the repo-relative path: a bare
                `README.md` is the repo's top-level README, `src/x.py` any path ending /src/x.py
    Precision over recall — this context is supplementary, and a wrong row misleads."""
    e = entry.strip().rstrip("/\\")
    if not e:
        return False
    if os.path.isabs(e) or e.startswith("~"):
        e = os.path.expanduser(e)
        if os.path.normcase(os.path.normpath(e)) == os.path.normcase(os.path.normpath(path)):
            return True
        return same_project and rel is not None and _repo_rel(e) == rel
    if not same_project or rel is None:
        return False
    e, rel = posix(os.path.normpath(e)), posix(rel)  # matched "/"-separated: Windows' relpath says "\\"
    return rel == e or ("/" in e and rel.endswith("/" + e))


def prior_observations(path: str) -> list:
    if not store_common.store_db().exists():
        return []
    base = os.path.basename(path)
    if not base:
        return []
    rel = _repo_rel(path)
    labels = set(labels_with_aliases([project_label(os.path.dirname(path))]))
    try:
        conn = store_common.connect(readonly=True)
        rows = conn.execute(
            "SELECT id, files_modified, files_read, project FROM observations "
            f"WHERE files_modified LIKE ? OR files_read LIKE ? ORDER BY created_at_epoch DESC LIMIT {WINDOW}",
            (f"%{base}%", f"%{base}%")).fetchall()
        conn.close()
    except Exception:
        return []
    out = []
    for r in rows:
        files = _jlist(r[1]) + _jlist(r[2])
        if any(_touches(f, path, rel, r[3] in labels) for f in files):
            out.append(r)
    return out


def _shown(path: str) -> str:
    """The path as the command should carry it: absolute, `~/` for home, quoted for a shell."""
    import shlex
    home = os.path.expanduser("~")
    p = os.path.abspath(os.path.expanduser(path))
    if p == home or p.startswith(home + os.sep):
        tail = posix(p[len(home) + 1:])
        return "~/" + shlex.quote(tail) if tail else "~"
    return shlex.quote(p)


def main() -> None:
    utf8_stdio()
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Read" or automated_entrypoint():
        return  # a program's session gets no hints about past work (session_origin.py)
    if payload.get("agent_id"):
        return  # a subagent: Claude Code names it on every hook it fires; it reads many files and follows none of these

    path = str((payload.get("tool_input") or {}).get("file_path") or "")
    if not path:
        return
    rows = prior_observations(path)
    if not rows:
        return
    n = len(rows)
    line = prompt("before-read.past-observations",
                  observations=f"{n} past observation{'s' if n != 1 else ''}", path=_shown(path))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": line}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
