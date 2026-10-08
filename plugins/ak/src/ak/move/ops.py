"""ops — the only code in a move that changes anything on disk.

A plan is a list of steps; each step names one op here and its args, as plain JSON, so a fresh
process (the frozen runner, a rollback days later) can replay it or undo it without the planner.
Every op has the same two halves:

    apply(args, ctx)  -> result   idempotent: a second call on a done step changes nothing
    revert(args, result, ctx)     undoes exactly what `result` says was done; result None means
                                  "the step began and may not have finished" — revert checks the
                                  disk and undoes only what it finds

Pointers are edited by key, never by restoring a whole file, so what someone else wrote after the
move (Claude Code adds a project key, the tracer indexes a turn) survives a rollback.

GUARDED. The gateway is the wire every session's model calls ride on; a move that touched its
files, its launchd unit or the shim fragment that routes claude at it would cut this session off
mid-turn. Every path an op touches is checked against protected() first, and the move refuses.

stdlib only: frozen into the move's own folder and run by bare python3 (runner.py).
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time

try:  # in the package, or bare beside a copy of _brand.py (the frozen engine)
    from .._brand import GATEWAY_ACCOUNTS, GATEWAY_CONFIG, GATEWAY_DATA, cmd, config_dir, data_dir, is_macos, is_windows, posix, replace_retry
except ImportError:
    from _brand import GATEWAY_ACCOUNTS, GATEWAY_CONFIG, GATEWAY_DATA, cmd, config_dir, data_dir, is_macos, is_windows, posix, replace_retry

def protected() -> tuple:
    """The gateway's files, its launchd unit and the shim that routes claude at it — read per call,
    so a test's $HOME is the one guarded."""
    h, cfg, data = os.path.expanduser("~"), str(config_dir()), str(data_dir())
    return (
        os.path.join(data, GATEWAY_DATA),
        os.path.join(h, "Library", "LaunchAgents"),
        os.path.join(cfg, "cc-shim"),
        os.path.join(data, "cc-shim"),
        os.path.join(cfg, GATEWAY_CONFIG),
        os.path.join(cfg, GATEWAY_ACCOUNTS),
    )


class Refused(Exception):
    """A step that must not run. The runner stops and rolls back what it did."""


def under(p: str, d: str) -> bool:
    p, d = posix(p), posix(d).rstrip("/")
    if is_windows():  # NTFS names are case-insensitive: C:\Users\Me\x is under c:/users/me
        p, d = p.lower(), d.lower()
    return p == d or p.startswith(d + "/")


def remap(p: str, old: str, new: str) -> str:
    if not under(p, old):
        return p
    if not is_windows():
        return new + p[len(posix(old).rstrip("/")):]
    out = new + posix(p)[len(posix(old).rstrip("/")):]  # portable: ok (posix()'d: "/" on Windows too)
    return _sep_like(p, out)


def _sep_like(orig: str, s: str) -> str:
    """S in ORIG's separator style (Windows): a path written C:\\a\\b stays backslashed, C:/a/b stays forward."""
    return s.replace("/", "\\") if "\\" in orig and "/" not in orig else s.replace("\\", "/")


def inside(root: str, p: str) -> str:
    """P as a relative path under ROOT, for a copy of it kept in the move's folder: `/a/b` → ROOT/a/b,
    `C:\\a\\b` → ROOT/C/a/b. Never os.path.join(ROOT, p) with P absolute: on Windows that IS p, the source."""
    drive, rest = os.path.splitdrive(p)
    rest = rest.lstrip("/\\")
    drive = drive.replace(":", "").strip("/\\")
    return os.path.join(root, drive, rest) if drive else os.path.join(root, rest)


def guard(*paths) -> None:
    for p in paths:
        if not p:
            continue
        rp = os.path.realpath(os.path.expanduser(str(p)))
        for g in protected():
            gr = os.path.realpath(g)
            if under(rp, gr) or under(gr, rp):
                raise Refused(f"{p} touches {g} — the gateway's own files are never part of a move")


# ── atomic file helpers ─────────────────────────────────────────────────────
def atomic_write(path: str, data: bytes, keep_mtime=None) -> None:
    d = os.path.dirname(path) or "."
    tmp = os.path.join(d, f".{os.path.basename(path)}.move-{os.getpid()}.tmp")
    try:
        mode = os.stat(path).st_mode & 0o7777
    except OSError:
        mode = 0o644
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    replace_retry(tmp, path)
    if keep_mtime is not None:
        os.utime(path, (keep_mtime, keep_mtime))


def backup_copy(src: str, ctx: dict) -> str:
    """A copy of SRC under the move's backup/: a clonefile on macOS (instant, shares blocks on APFS)."""
    dst = inside(os.path.join(ctx["dir"], "backup"), src)
    if os.path.exists(dst):
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if is_macos() and subprocess.run(["cp", "-c", src, dst], capture_output=True).returncode == 0:  # portable: ok (APFS clonefile)
        return dst
    if os.path.isdir(src):
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst)
    return dst


# ── rename ──────────────────────────────────────────────────────────────────
def rename_apply(a, ctx):
    src, dst = a["src"], a["dst"]
    guard(src, dst)
    if not os.path.lexists(src) and os.path.lexists(dst):
        return {"skipped": "already there"}
    if os.path.lexists(dst):
        raise Refused(f"{dst} already exists")
    parent = os.path.dirname(dst.rstrip("/\\"))
    made = []
    if a.get("mkparents"):  # a plugin moving to a marketplace whose cache folder does not exist yet
        top = parent
        while top and not os.path.isdir(top):
            made.append(top)
            top = os.path.dirname(top)
        os.makedirs(parent, exist_ok=True)
    if os.stat(src).st_dev != os.stat(parent).st_dev:
        raise Refused(f"{src} and {parent} are on different disks — a move is a rename, never a copy")
    os.rename(src, dst)
    return {"renamed": True, "made": made}


def rename_revert(a, r, ctx):
    src, dst = a["src"], a["dst"]
    if r and r.get("skipped"):
        return
    if os.path.lexists(dst) and not os.path.lexists(src):
        os.rename(dst, src)
    for d in (r or {}).get("made") or []:  # the folders apply had to create, now empty again
        try:
            os.rmdir(d)
        except OSError:
            pass


# ── stash: set a derived thing aside (a cache, a venv) so it rebuilds; revert puts it back ─────
def stash_apply(a, ctx):
    src = a["path"]
    guard(src)
    if not os.path.lexists(src):
        return {"skipped": "absent"}
    dst = inside(os.path.join(ctx["dir"], "stash"), src)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.rename(src, dst)
    return {"at": dst}


def stash_revert(a, r, ctx):
    src = a["path"]
    dst = (r or {}).get("at") or inside(os.path.join(ctx["dir"], "stash"), src)
    if os.path.lexists(dst) and not os.path.lexists(src):
        os.makedirs(os.path.dirname(src), exist_ok=True)
        os.rename(dst, src)


# ── tombstone: the old path becomes a file that says where it went ─────────
def tombstone_apply(a, ctx):
    p = a["path"]
    guard(p)
    if os.path.lexists(p):
        if _is_our_tombstone(p, ctx):
            return {"skipped": "already there"}
        raise Refused(f"{p} exists again — something re-created it after the move")
    if a.get("link"):
        try:
            _symlink(a["moved_to"], p)
            return {"link": True}
        except (OSError, NotImplementedError):
            pass  # symlinks refused (Windows without Developer Mode): the note says the same thing
    body = json.dumps({"moved_to": a["moved_to"], "move": ctx["id"], "at": _now(),
                       "undo": cmd(f"moves undo {ctx['id']}")}, indent=1) + "\n"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(fd, body.encode())
    finally:
        os.close(fd)
    return {"file": True}


def _is_our_tombstone(p, ctx) -> bool:
    if os.path.islink(p):
        return True
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("move") == ctx["id"]
    except (OSError, ValueError, AttributeError):
        return False


def tombstone_revert(a, r, ctx):
    p = a["path"]
    if r and r.get("skipped"):
        return
    if os.path.islink(p) or (os.path.isfile(p) and _is_our_tombstone(p, ctx)):
        os.chmod(p, 0o644) if not os.path.islink(p) else None
        os.unlink(p)


# ── json_remap: every string (and, with keys, every key) under OLD rewritten under NEW ───────
# With `embedded`, a string that only CONTAINS the folder is rewritten too — a settings command
# (`python3 /old/bin/x`, `cd /old;make`, `PATH=/old:/usr/bin`, `$(cat /old)`, `Read(//old/**)`) — at a path
# boundary: OLD not glued to a longer name before it, and followed by `/`, the end, whitespace, a quote or
# one of ; : ) | & , > or a backtick. A folder that only starts with the same text (`/old-wt/x`) is
# another folder and stays. When OLD sits under a HOME (`homes`), its `~/rest` form is found by the same
# rules (`"~/.ak"` whole, `cd ~/.ak;make`) and stays a `~/` form: it is re-pointed at `~/rest-of-NEW`
# (NEW outside HOME has no such form: it is written as the absolute path). A change is recorded as the whole
# string, before and after, so undo puts back the exact original by its key.
_EMBED_BEFORE = r"(?<![\w.~-])"
_EMBED_AFTER = r"""(?=[/\s'";:)|&,>`]|\Z)"""
_EMBED_RE: dict = {}


def tilde_of(path: str, homes) -> str:
    """`~/rest` for PATH under the first of HOMES that holds it, else ''. The home itself has no `~/rest`."""
    p = posix(path)
    for h in homes or ():
        h = posix(h).rstrip("/")
        if h and p.startswith(h + "/") and len(p) > len(h) + 1:
            return "~/" + p[len(h) + 1:]
    return ""


def _embed_sub(s: str, old: str, new: str, homes=()) -> str:
    if is_windows():
        return _embed_sub_win(s, old, new, homes)
    okey, nkey = old.rstrip("/"), new.rstrip("/")
    told = tilde_of(okey, homes)
    rx = _EMBED_RE.get((okey, told))
    if rx is None:
        alt = "(?P<abs>" + re.escape(okey) + ")" + (("|(?P<tilde>" + re.escape(told) + ")") if told else "")
        rx = _EMBED_RE[(okey, told)] = re.compile(_EMBED_BEFORE + "(?:" + alt + ")" + _EMBED_AFTER)
    tnew = (tilde_of(nkey, homes) or nkey) if told else nkey
    return rx.sub(lambda m: tnew if told and m.group("tilde") else nkey, s)


# Windows: a command names C:\old\x as often as C:/old/x, in any case, and a "\" ends the folder's name too.
# The match ignores case and separator style; the replacement is written in the style of the text it replaces.
_EMBED_AFTER_WIN = r"""(?=[/\\\s'";:)|&,>`]|\Z)"""


def _sep_free(s: str) -> str:
    return "".join(r"[/\\]" if c in "/\\" else re.escape(c) for c in s)


def _embed_sub_win(s: str, old: str, new: str, homes=()) -> str:
    okey, nkey = posix(old).rstrip("/"), posix(new).rstrip("/")  # portable: ok (posix()'d)
    told = tilde_of(okey, homes)
    rx = _EMBED_RE.get(("win", okey, told))
    if rx is None:
        alt = "(?P<abs>" + _sep_free(okey) + ")" + (("|(?P<tilde>" + _sep_free(told) + ")") if told else "")
        rx = _EMBED_RE[("win", okey, told)] = re.compile(_EMBED_BEFORE + "(?:" + alt + ")" + _EMBED_AFTER_WIN, re.I)
    tnew = (tilde_of(nkey, homes) or nkey) if told else nkey
    return rx.sub(lambda m: _sep_like(m.group(0), tnew if told and m.group("tilde") else nkey), s)


def _walk(node, old, new, keys, path, changes, embedded=False, homes=()):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            nk = k
            if keys and isinstance(k, str) and under(k, old):
                nk = remap(k, old, new)
                if nk in node and nk != k:
                    raise Refused(f"key {nk!r} already exists — merge it by hand, or move with --merge")
                changes.append({"path": path, "key": True, "before": k, "after": nk})
            out[nk] = _walk(v, old, new, keys, path + [nk], changes, embedded, homes)
        return out
    if isinstance(node, list):
        return [_walk(v, old, new, keys, path + [i], changes, embedded, homes) for i, v in enumerate(node)]
    if isinstance(node, str):
        nv = _embed_sub(node, old, new, homes) if embedded else (remap(node, old, new) if under(node, old) else node)
        if nv != node:
            changes.append({"path": path, "key": False, "before": node, "after": nv})
        return nv
    return node


def remap_changes(data, old, new, keys=False, embedded=False, homes=()) -> list:
    """What json_remap would change in DATA, as its result lists it — nothing is written (the planner's preview)."""
    changes: list = []
    _walk(data, old, new, keys, [], changes, embedded, homes)
    return changes


def _dump(data, raw: str) -> bytes:
    indent = 2 if raw.lstrip().startswith("{\n") or "\n  " in raw[:200] else None
    return (json.dumps(data, indent=indent, ensure_ascii=False) + ("\n" if raw.endswith("\n") else "")).encode()


def json_remap_apply(a, ctx):
    f = a["file"]
    guard(f)
    if not os.path.isfile(f):
        return {"changes": []}
    with open(f, encoding="utf-8") as fh:
        raw = fh.read()
    data = json.loads(raw)
    changes = []
    out = _walk(data, a["old"], a["new"], a.get("keys", False), [], changes, a.get("embedded", False),
                a.get("homes", ()))
    if changes:
        backup_copy(f, ctx)
        atomic_write(f, _dump(out, raw))
    return {"changes": changes}


def _node_at(root, path):
    n = root
    for seg in path:
        if isinstance(n, dict) and seg in n:
            n = n[seg]
        elif isinstance(n, list) and isinstance(seg, int) and seg < len(n):
            n = n[seg]
        else:
            return None
    return n


def json_remap_revert(a, r, ctx):
    f = a["file"]
    if not os.path.isfile(f):
        return
    with open(f, encoding="utf-8") as fh:
        raw = fh.read()
    data = json.loads(raw)
    changes = (r or {}).get("changes")
    if changes is None:  # began, result unknown: invert by value, which the collision check made safe
        _walk_back = _walk(data, a["new"], a["old"], a.get("keys", False), [], [], a.get("embedded", False),
                           a.get("homes", ()))
        atomic_write(f, _dump(_walk_back, raw))
        return
    touched = False
    for c in reversed(changes):
        parent = _node_at(data, c["path"][:-1]) if not c["key"] else _node_at(data, c["path"])
        if c["key"]:
            if isinstance(parent, dict) and c["after"] in parent and c["before"] not in parent:
                items = list(parent.items())  # rename in place: the key keeps its position
                parent.clear()
                for k, v in items:
                    parent[c["before"] if k == c["after"] else k] = v
                touched = True
        else:
            last = c["path"][-1] if c["path"] else None
            if parent is None and not c["path"]:
                continue
            if isinstance(parent, (dict, list)) and _node_at(data, c["path"]) == c["after"]:
                parent[last] = c["before"]
                touched = True
    if touched:
        atomic_write(f, _dump(data, raw))


# ── jsonl_remap: one field of each line, only the lines that existed when it ran ─────────────
def jsonl_remap_apply(a, ctx):
    f = a["file"]
    guard(f)
    if not os.path.isfile(f):
        return {"lines": []}
    with open(f, "rb") as fh:
        lines = fh.read().split(b"\n")
    changed = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        v = obj.get(a["field"]) if isinstance(obj, dict) else None
        if isinstance(v, str) and under(v, a["old"]):
            obj[a["field"]] = remap(v, a["old"], a["new"])
            written = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
            changed.append([i, line.decode("utf-8", "surrogateescape"), written.decode("utf-8", "surrogateescape")])
            lines[i] = written
    if changed:
        backup_copy(f, ctx)
        mtime = os.stat(f).st_mtime if a.get("keep_mtime") else None
        atomic_write(f, b"\n".join(lines), keep_mtime=mtime)
    return {"lines": changed}


def jsonl_remap_revert(a, r, ctx):
    f = a["file"]
    if not os.path.isfile(f):
        return
    with open(f, "rb") as fh:
        lines = fh.read().split(b"\n")
    todo = (r or {}).get("lines")
    touched = False
    if todo is not None:  # the exact bytes back, on each line still as the move wrote it
        for i, before, written in todo:
            if i < len(lines) and lines[i] == written.encode("utf-8", "surrogateescape"):
                lines[i] = before.encode("utf-8", "surrogateescape")
                touched = True
        if touched:
            mtime = os.stat(f).st_mtime if a.get("keep_mtime") else None
            atomic_write(f, b"\n".join(lines), keep_mtime=mtime)
        return
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        v = obj.get(a["field"]) if isinstance(obj, dict) else None
        if isinstance(v, str) and under(v, a["new"]):
            obj[a["field"]] = remap(v, a["new"], a["old"])
            lines[i] = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
            touched = True
    if touched:
        mtime = os.stat(f).st_mtime if a.get("keep_mtime") else None
        atomic_write(f, b"\n".join(lines), keep_mtime=mtime)


# ── append_line: one JSON line at the end of a transcript (a `relocated` record) ────────────
def append_line_apply(a, ctx):
    f = a["file"]
    guard(f)
    line = (json.dumps(a["record"], separators=(",", ":")) + "\n").encode()
    with open(f, "rb") as fh:
        body = fh.read()
    if body.endswith(line):
        return {"skipped": "already there"}
    st = os.stat(f)
    with open(f, "ab") as fh:
        if body and not body.endswith(b"\n"):
            fh.write(b"\n")
        fh.write(line)
    os.utime(f, (st.st_atime, st.st_mtime))  # the /resume picker sorts by mtime
    return {"mtime": st.st_mtime}


def append_line_revert(a, r, ctx):
    f = a["file"]
    if (r and r.get("skipped")) or not os.path.isfile(f):
        return
    line = (json.dumps(a["record"], separators=(",", ":")) + "\n").encode()
    with open(f, "rb") as fh:
        body = fh.read()
    i = body.rfind(line)
    if i < 0:
        return
    st = os.stat(f)
    atomic_write(f, body[:i] + body[i + len(line):], keep_mtime=(r or {}).get("mtime", st.st_mtime))


# ── sql_map: rewrite pointers in a SQLite store, one transaction, after a WAL checkpoint ─────
def _connect(db):
    con = sqlite3.connect(db, timeout=10, isolation_level=None)
    con.execute("PRAGMA busy_timeout=10000")
    return con


def _run_updates(con, updates, invert=False):
    counts = []
    for u in updates:
        t, c, mode = u["table"], u["col"], u.get("mode", "eq")
        n = 0
        for old, new in u["map"].items():
            if invert:
                old, new = new, old
            if mode == "eq":
                cur = con.execute(f'UPDATE "{t}" SET "{c}"=? WHERE "{c}"=?', (new, old))
            elif mode == "prefix":  # OLD itself, or OLD followed by '/'
                cur = con.execute(f'UPDATE "{t}" SET "{c}" = ? || substr("{c}", ?) '
                                  f'WHERE "{c}" = ? OR substr("{c}", 1, ?) = ?',
                                  (new, len(old) + 1, old, len(old) + 1, old.rstrip("/") + "/"))
            elif mode == "contains":
                cur = con.execute(f'UPDATE "{t}" SET "{c}" = replace("{c}", ?, ?) WHERE instr("{c}", ?) > 0',
                                  (old, new, old))
            else:
                raise Refused(f"unknown sql mode {mode}")
            n += cur.rowcount
        counts.append(n)
    return counts


def sql_map_apply(a, ctx):
    db = a["db"]
    guard(db)
    if not os.path.isfile(db):
        return {"skipped": "no store"}
    con = _connect(db)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        backup_copy(db, ctx)
        con.execute("BEGIN IMMEDIATE")
        try:
            counts = _run_updates(con, a["updates"])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return {"counts": counts}


def sql_map_revert(a, r, ctx):
    db = a["db"]
    if (r and r.get("skipped")) or not os.path.isfile(db):
        return
    con = _connect(db)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            _run_updates(con, a["updates"], invert=True)  # NEW values did not exist before: safe
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


# ── exec: an outside command (git worktree repair); revert runs its own argv ─────────────────
def _exec(argv, cwd=None):
    if not argv:
        return {"rc": 0}
    p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise Refused(f"{' '.join(argv)} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return {"rc": 0, "out": (p.stdout or "").strip()[:300]}


def exec_apply(a, ctx):
    return _exec(a.get("argv"), a.get("cwd"))


def exec_revert(a, r, ctx):
    if a.get("revert_argv"):
        cwd = a.get("revert_cwd") or a.get("cwd")
        if cwd and not os.path.isdir(cwd):
            return
        _exec(a["revert_argv"], cwd)


# ── symlink: re-point a link whose target moved ─────────────────────────────
def symlink_apply(a, ctx):
    link = a["link"]
    guard(link)
    if not os.path.islink(link):
        return {"skipped": "not a link"}
    if os.readlink(link) == a["to"]:
        return {"skipped": "already there"}
    tmp = link + ".move-tmp"
    try:
        _symlink(a["to"], tmp)
    except (OSError, NotImplementedError):  # symlinks refused here: the link stays, `ak doctor` names it
        return {"skipped": "symlinks refused"}
    os.replace(tmp, link)
    return {"from": a["from"]}


def symlink_revert(a, r, ctx):
    link = a["link"]
    if (r and r.get("skipped")) or not os.path.islink(link):
        return
    if os.readlink(link) == a["to"]:
        tmp = link + ".move-tmp"
        try:
            _symlink(a["from"], tmp)
        except (OSError, NotImplementedError):
            return
        os.replace(tmp, link)


# ── move_dir: rename a folder and leave a symlink at the old path in the same step ──────────────
# The kit's own state home holds the move's journal: between a bare rename and a later symlink step the
# runner could not write its next journal line. One op, so the old path never stops resolving; the link
# stays until `moves finalize`, so a straggler running old code writes into the new home, not a fresh one.
def move_dir_apply(a, ctx):
    src, dst = a["src"], a["dst"]
    guard(src, dst)
    if os.path.islink(src) and os.path.realpath(src) == os.path.realpath(dst):
        return {"skipped": "already there"}
    if not os.path.lexists(src) and os.path.isdir(dst):  # a crash between the rename and the link
        _symlink(dst, src)
        return {"renamed": True, "linked": True, "resumed": True}
    if os.path.lexists(dst):
        raise Refused(f"{dst} already exists")
    parent = os.path.dirname(dst.rstrip("/\\"))
    if os.stat(src).st_dev != os.stat(parent).st_dev:
        raise Refused(f"{src} and {dst} are on different disks — a move is a rename, never a copy")
    if not can_symlink(parent):  # the journal lives under SRC: without the link the runner loses it mid-move
        raise Refused(f"this system refuses symlinks, and {src} must keep resolving while it moves "
                      "(Windows: turn on Developer Mode)")
    os.rename(src, dst)
    _symlink(dst, src)
    return {"renamed": True, "linked": True}


def _symlink(target: str, link: str) -> None:
    """os.symlink that says when TARGET is a folder. Windows keeps file and directory links apart: a file link
    to a folder does not open as one (POSIX ignores the flag)."""
    os.symlink(target, link, target_is_directory=os.path.isdir(target))


def can_symlink(d: str) -> bool:
    """Whether a symlink can be made in folder D (Windows refuses one without Developer Mode)."""
    probe = os.path.join(d, f".move-probe-{os.getpid()}")
    try:
        _symlink(d, probe)  # a link to a folder, as the move makes: on Windows a directory link is its own kind
    except (OSError, NotImplementedError):
        return False
    os.unlink(probe)
    return True


def move_dir_revert(a, r, ctx):
    src, dst = a["src"], a["dst"]
    if r and r.get("skipped"):
        return
    if os.path.islink(src) and os.readlink(src) == dst:
        os.unlink(src)
    if os.path.isdir(dst) and not os.path.lexists(src):
        os.rename(dst, src)


# ── json_edit: exact edits chosen at plan time — rename a key, set a value — undone by the same list ──
# Each edit: {"path": [...], "key": True, "before": oldkey, "after": newkey}  (PATH is the dict holding it)
#            {"path": [...], "key": False, "before": v, "after": w}            (PATH is the value itself)
# Values are edited before keys, so a value's path names keys as they were. A key keeps its position.
def _sha(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


def _edit_all(data, edits, invert=False) -> int:
    n = 0
    for e in (reversed(edits) if invert else edits):
        frm, to = (e["after"], e["before"]) if invert else (e["before"], e["after"])
        if e["key"]:
            node = _node_at(data, e["path"])
            if isinstance(node, dict) and frm in node and to not in node:
                items = list(node.items())
                node.clear()
                for k, v in items:
                    node[to if k == frm else k] = v
                n += 1
        else:
            parent = _node_at(data, e["path"][:-1])
            last = e["path"][-1]
            if isinstance(parent, (dict, list)) and _node_at(data, e["path"]) == frm:
                parent[last] = to
                n += 1
    return n


def json_edit_apply(a, ctx):
    f = a["file"]
    guard(f)
    if not os.path.isfile(f):
        return {"changed": 0}
    with open(f, encoding="utf-8") as fh:
        raw = fh.read()
    data = json.loads(raw)
    edits = sorted(a["edits"], key=lambda e: bool(e["key"]))  # values first
    n = _edit_all(data, edits)
    if not n:
        return {"changed": 0}
    backup_copy(f, ctx)
    out = _dump(data, raw)
    atomic_write(f, out)
    return {"changed": n, "sha": _sha(out)}


def json_edit_revert(a, r, ctx):
    f = a["file"]
    if not os.path.isfile(f):
        return
    with open(f, "rb") as fh:
        cur = fh.read()
    bak = inside(os.path.join(ctx["dir"], "backup"), f)
    if r and r.get("sha") and _sha(cur) == r["sha"] and os.path.isfile(bak):
        with open(bak, "rb") as fh:  # untouched since the move wrote it: the exact original bytes
            atomic_write(f, fh.read())
        return
    raw = cur.decode("utf-8")
    data = json.loads(raw)
    edits = sorted(a["edits"], key=lambda e: bool(e["key"]))
    if _edit_all(data, edits, invert=True):
        atomic_write(f, _dump(data, raw))


# ── lineage: the record readers follow; a revert appends, never deletes ──────────────────────
def lineage_apply(a, ctx):
    import lineage as L  # the frozen copy beside this file
    for row in a["rows"]:
        L.append({**row, "move": ctx["id"], "at": _now(), "by": cmd("ws move")}, a["file"])
    return {"n": len(a["rows"])}


def lineage_revert(a, r, ctx):
    import lineage as L
    L.append({"move": ctx["id"], "revert": True, "at": _now(), "by": cmd("moves undo")}, a["file"])


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


OPS = {
    "rename": (rename_apply, rename_revert),
    "stash": (stash_apply, stash_revert),
    "tombstone": (tombstone_apply, tombstone_revert),
    "json_remap": (json_remap_apply, json_remap_revert),
    "jsonl_remap": (jsonl_remap_apply, jsonl_remap_revert),
    "append_line": (append_line_apply, append_line_revert),
    "sql_map": (sql_map_apply, sql_map_revert),
    "exec": (exec_apply, exec_revert),
    "symlink": (symlink_apply, symlink_revert),
    "move_dir": (move_dir_apply, move_dir_revert),
    "json_edit": (json_edit_apply, json_edit_revert),
    "lineage": (lineage_apply, lineage_revert),
}
