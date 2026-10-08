"""lineage — what moved where, the one record every reader follows instead of a hard-coded alias.

A move rewrites POINTERS (a session folder's name, a settings path, an index key) and leaves
HISTORY alone (a transcript's recorded cwd, an observer row's project label, a ledger line's note
path). History stays true to the day it was written; a reader that wants it under today's name asks
this file which names a thing has had. So a rename never rewrites a row, and the next one costs a
line here, not a code change.

    <kit>/db/lineage.jsonl                 append-only, one JSON object per line
    {"v":1,"move":id,"kind":"path|label","from":..,"to":..,"at":..,"by":..}
    {"v":1,"move":id,"revert":true,"at":..,"by":..}     a rolled-back move: its rows stop counting
    {"v":1,...,"split":true}                            both still exist: an alias, never followed forward

Only the move runner writes it. A missing or unreadable file is identity — every name is just
itself — so a reader that runs without a vault (the tracer) loses nothing.

stdlib only, no package imports: a copy sits in observer/hooks/ (the capture hooks run under
bare python3). Edit THIS file, then `uv run python scripts/copies.py render`; its check fails on drift.
"""
import json
import os

try:  # inside the package, or a bare file beside _brand.py (hooks, the frozen engine)
    from .._brand import HOME_DIR, env
except ImportError:
    from _brand import HOME_DIR, env

NAME = "lineage.jsonl"


def default_path() -> str:
    """<kit>/db/lineage.jsonl: the kit's STORE variable, else the db of its HOME variable, else of its
    PATH variable, else of ~/<home dir> (move/plan.py: db_dir)."""
    store = (env("STORE") or "").strip()
    if store:
        return os.path.join(os.path.realpath(os.path.expanduser(store)), NAME)
    home = (env("HOME") or "").strip() or (env("PATH") or "").strip()
    return os.path.join(os.path.realpath(os.path.expanduser(home or "~/" + HOME_DIR)), "db", NAME)


def rows(path=None) -> list:
    """Every applied row, reverted moves left out. Unreadable lines are skipped; no file is []."""
    try:
        with open(path or default_path(), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out, reverted = [], set()
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        if r.get("revert"):
            reverted.add(r.get("move"))
        elif r.get("kind") and r.get("from") and r.get("to"):
            out.append(r)
    return [r for r in out if r.get("move") not in reverted]


def names(kind: str, value: str, path=None, _rows=None) -> list:
    """VALUE first, then every name it was or became, transitively; cycle-safe, order kept."""
    rs = [r for r in (_rows if _rows is not None else rows(path)) if r.get("kind") == kind]
    out, todo = [value], [value]
    while todo:
        v = todo.pop(0)
        for r in rs:
            for a, b in ((r["from"], r["to"]), (r["to"], r["from"])):
                if a == v and b not in out:
                    out.append(b)
                    todo.append(b)
    return out


def _under(p: str, d: str) -> bool:
    return p == d or p.startswith(d.rstrip("/") + "/")


def path_names(p: str, path=None) -> list:
    """P, then P as it reads under every path it was or became — on '/' boundaries, both ways."""
    rs = [r for r in rows(path) if r.get("kind") == "path"]
    out, todo = [p], [p]
    while todo:
        q = todo.pop(0)
        for r in rs:
            for a, b in ((r["from"], r["to"]), (r["to"], r["from"])):
                if _under(q, a):
                    c = b + q[len(a):]
                    if c not in out:
                        out.append(c)
                        todo.append(c)
    return out


def current(kind: str, value: str, path=None) -> str:
    """Follow VALUE forward to its newest name; split rows are aliases and are not followed."""
    rs = [r for r in rows(path) if r.get("kind") == kind and not r.get("split")]
    seen, v = {value}, value
    moved = True
    while moved:
        moved = False
        for r in rs:
            if r["from"] == v and r["to"] not in seen:
                v = r["to"]
                seen.add(v)
                moved = True
    return v


def moved(p: str, path=None) -> str:
    """Where the folder P is now: P itself, else where the newest recorded move of P (or of a folder
    above it) took it. A default like ~/agentic-kit stays right after `ak ws move ~/agentic-kit ~/x`."""
    rs = [r for r in rows(path) if r.get("kind") == "path" and not r.get("split")]
    seen, q = {p}, p
    changed = True
    while changed:
        changed = False
        for r in rs:
            if _under(q, r["from"]):
                c = r["to"] + q[len(r["from"]):]
                if c not in seen:
                    q = c
                    seen.add(q)
                    changed = True
    return q


def append(row: dict, path=None) -> None:
    """One line, one write, fsync'd. The runner is the only caller."""
    p = path or default_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = (json.dumps({"v": 1, **row}, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    # O_BINARY: without it Windows opens the fd in text mode and os.write turns each "\n" into "\r\n"
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
