"""One folder per setup run, and undo.

    <kit home>/db/setup/<YYYYmmdd-HHMMSS>/
        run.json     the steps, in order: {id, kind, undo, status}, and the files each touched
        backup/      every file a step was about to write, as it was (mirrored path)
        logs/        each step's child-process output

Undo walks the steps newest first and only takes back what this run did: keys whose value is
still ours, lines we added that are still there, a file restored only if nobody wrote it since.
It never waits for Claude sessions to close.

Undo kinds (Step.undo / record()):
    {"kind": "json_keys", "file": p, "changes": [{"keypath", "before", "after"}]}
    {"kind": "text_lines", "file": p, "lines": [...]}           lines this step appended
    {"kind": "restore", "file": p, "after_sha": sha}             bytes back if sha still matches
    {"kind": "exec", "argv": [...], "env": {...}, "unset": [...]}  the inverse command; "unset" names
                                                                  variables to remove from its environment,
                                                                  "cwd" the folder to run it in
    {"kind": "clone", "path": p}                                 rm only if clean and we made it
    {"kind": "none", "note": "..."}                              nothing to take back (a venv warm)

A json_keys step may also carry "after_sha" (what settings.apply returned): while the file still has
those bytes, undo puts the backup back whole instead of editing keys. A change may carry "created":
how many objects on its way down the step made, so undo drops them again once they are empty.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass

from ak.move.ops import atomic_write, inside
from ak.move.plan import db_dir
from ak.setup import settings


def runs_dir() -> str:
    """<kit home>/db/setup."""
    return os.path.join(db_dir(), "setup")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


@dataclass
class Run:
    id: str
    path: str

    @classmethod
    def new(cls) -> "Run":
        base = time.strftime("%Y%m%d-%H%M%S")
        for n in range(1, 1000):
            rid = base if n == 1 else f"{base}-{n}"
            path = os.path.join(runs_dir(), rid)
            try:
                os.makedirs(path)
            except FileExistsError:
                continue
            break
        os.makedirs(os.path.join(path, "backup"))
        os.makedirs(os.path.join(path, "logs"))
        run = cls(rid, path)
        run._write({"id": rid, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "undone": False,
                    "snapshots": {}, "steps": []})
        return run

    @classmethod
    def load(cls, run_id: str) -> "Run":
        """A run by id, or by a prefix of it that names one."""
        root = runs_dir()
        names = sorted(n for n in (os.listdir(root) if os.path.isdir(root) else [])
                       if os.path.isfile(os.path.join(root, n, "run.json")))
        hit = run_id if run_id in names else None
        if hit is None:
            near = [n for n in names if n.startswith(run_id)]
            if len(near) != 1:
                raise FileNotFoundError(f"no setup run {run_id!r}" if not near else
                                        f"{run_id!r} names {len(near)} setup runs: {', '.join(near)}")
            hit = near[0]
        return cls(hit, os.path.join(root, hit))

    def _file(self) -> str:
        return os.path.join(self.path, "run.json")

    def _read(self) -> dict:
        with open(self._file(), encoding="utf-8") as fh:
            return settings.loads(fh.read())

    def _write(self, doc: dict) -> None:
        atomic_write(self._file(), (settings.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode())

    def backup_dir(self) -> str:
        return os.path.join(self.path, "backup")

    def snapshot(self, path: str) -> None:
        """Before a write: copy path into backup/ (or note it was absent) with its sha. Once per path per run."""
        path = os.path.abspath(path)
        doc = self._read()
        if path in doc["snapshots"]:
            return
        if os.path.isdir(path):
            raise ValueError(f"{path} is a folder; a snapshot is of a file")
        if os.path.exists(path):
            dst = inside(self.backup_dir(), path)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(path, dst)
            doc["snapshots"][path] = {"absent": False, "sha": _sha(_bytes(dst))}
        else:
            doc["snapshots"][path] = {"absent": True, "sha": None}
        self._write(doc)

    def snapshot_of(self, path: str) -> dict | None:
        return self._read()["snapshots"].get(os.path.abspath(path))

    def backed_up(self, path: str) -> bytes | None:
        """The bytes snapshot() kept of path, None when it was absent (or never snapshotted)."""
        snap = self.snapshot_of(path)
        return None if not snap or snap["absent"] else _bytes(inside(self.backup_dir(), os.path.abspath(path)))

    def record(self, step_id: str, title: str, undo: dict, status: str = "done") -> None:
        """Append one step to run.json (written atomically each time)."""
        doc = self._read()
        doc["steps"].append({"id": step_id, "title": title, "undo": undo, "status": status,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "undone": False})
        self._write(doc)

    def log_path(self, step_id: str) -> str:
        return os.path.join(self.path, "logs", re.sub(r"[^\w.-]", "_", step_id) + ".log")

    def steps(self) -> list[dict]:
        return self._read()["steps"]


def list_runs() -> list[dict]:
    """Every run, newest first: {id, path, steps, undone}. `steps` is how many were recorded."""
    root = runs_dir()
    out = []
    for name in (os.listdir(root) if os.path.isdir(root) else []):
        if not os.path.isfile(os.path.join(root, name, "run.json")):
            continue
        doc = Run(name, os.path.join(root, name))._read()
        out.append({"id": name, "path": os.path.join(root, name), "started": doc.get("started", ""),
                    "steps": len(doc["steps"]), "undone": bool(doc.get("undone"))})
    return sorted(out, key=lambda r: (r["id"][:15], len(r["id"]), r["id"]), reverse=True)


# ── one undo per kind: (run, undo dict, dry_run) -> (action, ok, detail) ─────────────────────────────
def _put_back(run: Run, file: str, dry: bool) -> tuple[bool, str]:
    """The file as snapshot() found it: its backup bytes, or gone when it was absent."""
    snap = run.snapshot_of(file)
    if not snap:
        return False, "no backup of it in this run"
    if snap["absent"]:
        if not dry and os.path.exists(file):
            os.unlink(os.path.realpath(file))
        return True, "removed (it did not exist before)"
    old = run.backed_up(file)
    if old is None or _sha(old) != snap["sha"]:
        return False, "the backup copy is missing or changed"
    if not dry:
        atomic_write(os.path.realpath(file), old)  # through a symlink (stow, chezmoi): the target changes, the link stays
    return True, "restored from the backup"


def _prune(data: dict, keypath: list, made: int) -> None:
    """Drop the objects a change made on its way down, from the innermost out, while they are empty."""
    for k in range(1, made + 1):
        way = keypath[:len(keypath) - k]
        node = data
        for key in way[:-1]:
            node = node[key]
        if node.get(way[-1]) != {}:
            return
        del node[way[-1]]


def _undo_json_keys(run: Run, u: dict, dry: bool):
    f = u["file"]
    names = [".".join(c["keypath"]) for c in u["changes"]]
    action = f"put back {', '.join(names)} in {f}"
    cur = _bytes(f)
    if cur is None:
        return action, True, "the file is gone; nothing to put back"
    if u.get("after_sha") and _sha(cur) == u["after_sha"] and run.snapshot_of(f):
        ok, detail = _put_back(run, f, dry)
        if ok:
            return action, ok, detail
    raw = cur.decode("utf-8")
    try:
        data = settings.parse_object(raw, f)
    except ValueError as e:
        return action, False, f"cannot read it: {e}"
    back, left = [], []
    for c, name in reversed(list(zip(u["changes"], names))):
        try:
            node, _ = settings.walk_down(data, c["keypath"], create=False)
        except ValueError:
            node = None
        last = c["keypath"][-1]
        if node is None or last not in node or not settings.same_json(node[last], c["after"]):
            left.append(name)
            continue
        if c["before"] is settings.ABSENT:
            del node[last]
            _prune(data, c["keypath"], c.get("created", 0))
        else:
            node[last] = c["before"]
        back.append(name)
    snap = run.snapshot_of(f)
    if back and not dry:
        if not data and snap and snap["absent"]:
            os.unlink(os.path.realpath(f))  # the file was ours, and nothing else is left in it
        else:
            atomic_write(os.path.realpath(f), settings.render(data, raw).encode())
    detail = (f"put back {', '.join(back)}" if back else "nothing of ours left in it") + \
             (f"; left alone, changed since: {', '.join(left)}" if left else "")
    return action, True, detail


def _undo_text_lines(run: Run, u: dict, dry: bool):
    f = u["file"]
    action = f"remove {len(u['lines'])} line(s) we added to {f}"
    cur = _bytes(f)
    if cur is None:
        return action, True, "the file is gone; nothing to remove"
    rows = cur.splitlines(keepends=True)
    gone, missing = 0, []
    for want in reversed(u["lines"]):  # we appended: the last copy is ours
        w = want.rstrip("\r\n").encode()
        for i in range(len(rows) - 1, -1, -1):
            if rows[i].rstrip(b"\r\n") == w:
                del rows[i]
                gone += 1
                break
        else:
            missing.append(want.rstrip("\r\n"))
    snap = run.snapshot_of(f)
    if gone and not dry:
        if not rows and snap and snap["absent"]:
            os.unlink(os.path.realpath(f))
        else:
            atomic_write(os.path.realpath(f), b"".join(rows))
    detail = f"removed {gone}" + (f"; already gone: {len(missing)}" if missing else "")
    return action, True, detail


def _undo_restore(run: Run, u: dict, dry: bool):
    f = u["file"]
    action = f"restore {f}"
    cur = _bytes(f)
    if (None if cur is None else _sha(cur)) != u.get("after_sha"):
        return action, False, "changed since; left alone"
    ok, detail = _put_back(run, f, dry)
    return action, ok, detail


def _undo_exec(run: Run, u: dict, dry: bool):
    argv = u["argv"]
    action = "run " + " ".join(argv)
    if dry:
        return action, True, ""
    try:
        gone = set(u.get("unset", ()))
        p = subprocess.run(argv, env={**{k: v for k, v in os.environ.items() if k not in gone}, **u.get("env", {})},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                           cwd=u.get("cwd"))
    except (OSError, subprocess.TimeoutExpired) as e:
        return action, False, str(e)
    with open(run.log_path("undo-" + re.sub(r"\W+", "-", argv[0])), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"$ {' '.join(argv)}\n{p.stdout}{p.stderr}")
    tail = ((p.stderr or p.stdout).strip().splitlines() or [""])[-1]
    return action, p.returncode == 0, f"exit {p.returncode}" + (f": {tail}" if p.returncode else "")


def _writable(fn, path, _):
    os.chmod(path, stat.S_IWRITE)  # a read-only file inside .git blocks rmtree on Windows
    fn(path)


def _undo_clone(run: Run, u: dict, dry: bool):
    d = u["path"]
    action = f"remove the clone {d}"
    if not os.path.lexists(d):
        return action, True, "already gone"
    try:
        p = subprocess.run(["git", "-C", d, "status", "--porcelain"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return action, False, f"cannot check it is clean: {e}"
    if p.returncode != 0:
        return action, False, f"git status failed: {p.stderr.strip()}; left alone"
    if p.stdout.strip():
        return action, False, f"{len(p.stdout.splitlines())} uncommitted change(s) in it; left alone"
    for args, what in ((["rev-list", "--count", "HEAD", "--not", "--remotes"], "commit(s) not pushed anywhere"),
                       (["stash", "list"], "stash entr(ies)")):
        try:
            q = subprocess.run(["git", "-C", d, *args], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return action, False, f"cannot check it holds nothing of the person's: {e}"
        if q.returncode != 0:
            return action, False, f"git {args[0]} failed: {q.stderr.strip()}; left alone"
        n = int(q.stdout.strip() or 0) if args[0] == "rev-list" else len(q.stdout.splitlines())
        if n:
            return action, False, f"{n} {what} in it; left alone"
    if not dry:
        if os.path.islink(d):
            os.unlink(d)
        else:
            shutil.rmtree(d, onerror=_writable)
    return action, True, "removed"


def _undo_none(run: Run, u: dict, dry: bool):
    return u.get("note", "nothing to take back"), True, u.get("note", "")


def _undo_unknown(run: Run, u: dict, dry: bool):
    return "nothing to take back", not u, f"unknown undo kind {u.get('kind')!r}" if u else ""


_UNDO = {"json_keys": _undo_json_keys, "text_lines": _undo_text_lines, "restore": _undo_restore,
         "exec": _undo_exec, "clone": _undo_clone, "none": _undo_none}


def undo(run_id: str | None = None, dry_run: bool = False) -> list[dict]:
    """Take back a run (default: the newest not yet undone), newest step first.
    Returns one {step, action, ok, detail} per step. Safe to run twice: a step that was taken back is
    not run again, one that was refused (ok False) is tried again."""
    if run_id is None:
        todo = [r for r in list_runs() if not r["undone"]]
        if not todo:
            return []
        run = Run(todo[0]["id"], todo[0]["path"])
    else:
        run = Run.load(run_id)
    doc = run._read()
    if doc.get("undone"):
        return []
    out = []
    for step in reversed(doc["steps"]):
        if step.get("undone"):
            continue
        u = step.get("undo") or {}
        action, ok, detail = _UNDO.get(u.get("kind"), _undo_unknown)(run, u, dry_run)
        out.append({"step": step["id"], "action": action, "ok": ok, "detail": detail})
        if not dry_run:
            step["undone"], step["result"] = ok, {"ok": ok, "detail": detail}  # a refused step is tried again next time
            run._write(doc)
    if not dry_run:
        doc["undone"] = all(st.get("undone") for st in doc["steps"])
        if doc["undone"]:
            doc["undone_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        run._write(doc)
    return out
