"""`tracer trace blame`: which sessions read or changed a file, and what each change was.

Asked "which sessions edited obs_index.py, and what did each change?", an agent spent 17
calls: the trace named four interactions and nothing of the changes, so it drilled five
turns, ran git log/show/diff, and finally jq'd a subagent transcript — because the edits
had been made by a SUBAGENT, and the trace never looked there. This answers from one call:

    where it looks        the index for the sessions' own transcripts (i_files → the turn →
                          the exact step), and one ripgrep pass over subagent and workflow
                          transcripts (<session>/subagents/**/*.jsonl), each credited to the
                          session that spawned it
    what a change says    an Edit's lines removed/added, where (from Claude Code's own
                          structuredPatch when the result carries one), and its first changed
                          line on each side; a Write: created or overwrote, how many lines
    which file            the query is matched as a path SUFFIX, so one repo file is found in
                          the checkout, a worktree and the release clone alike — each matched
                          path is named. A file of the same name at another path is listed
                          apart, never merged
    a folder              `plugins/present` (or a trailing /): every session that changed a
                          file under it, one summary each — its files, the turns that changed
                          them and what each asked. Asked for present's backstory, an agent
                          knew the plugin, not a session; a file-only blame found nothing
    what it cannot see    a change made through Bash (sed -i, a heredoc, git checkout)
"""

from __future__ import annotations

import difflib
import json
import shutil
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ._brand import RELEASE_DIR, data_dir, posix, temp_dir
from .turns import TurnSplitter, one_line, result_text, sender

CHANGE_TOOLS = {"Edit": "EDIT", "MultiEdit": "EDIT", "NotebookEdit": "EDIT", "Write": "WRITE"}
READ_TOOL = "Read"
RELEASE_CLONE = data_dir() / RELEASE_DIR
# Paths an old session recorded (i_files, transcripts) still name the folder the clone had before the rename.
_OLD_RELEASE = Path.home() / ".local" / "share" / "brain" / "release"  # brand: historical
# Where a temp dir lives: POSIX's fixed roots (and macOS's /private twin), plus this OS's own temp_dir().
_TEMP_ROOTS = ("/tmp/", "/private/", "/var/")  # portable: ok — fixed POSIX roots; Windows' is temp_dir()


def target_path(inp: dict) -> str:
    return str(inp.get("file_path") or inp.get("notebook_path") or "") if isinstance(inp, dict) else ""


def query_suffix(query: str) -> str:
    """What to match: an absolute path inside a git repo becomes its repo-relative tail,
    so the same file is found in every checkout of the repo; anything else as given."""
    q = query.strip().removeprefix("./")
    p = Path(q).expanduser()
    if not p.is_absolute():
        return q
    for parent in p.parents:
        if (parent / ".git").exists():
            return str(p.relative_to(parent))
    return str(p)


def _parts(path: str) -> tuple[str, ...]:
    """A path's names, "/" or "\\" separated alike (a Windows transcript records C:\\x\\y)."""
    return tuple(n for n in PurePosixPath(posix(path)).parts if n != "/")


def matches(path: str, suffix: str) -> bool:
    """The path ends with the suffix, name for name."""
    if path == suffix:
        return True
    want = _parts(suffix)
    return bool(want) and _parts(path)[-len(want):] == want


def under(path: str, folder: str) -> bool:
    """A path inside the folder, the folder matched as a path suffix the way a file is."""
    path, f = posix(path), posix(folder).strip("/")
    return path.startswith(f + "/") or f"/{f}/" in path


def _folder_split(path: str, folder: str) -> tuple[str, str]:
    """(the copy of the folder a path lives in, the path inside it)."""
    path, f = posix(path), posix(folder).strip("/")
    if path.startswith(f + "/"):
        return f + "/", path[len(f) + 1:]
    root, _, rest = path.partition(f"/{f}/")
    return f"{root}/{f}/", rest


def layout(path: str) -> str:
    """Which copy of a repo a path lives in."""
    path = posix(path)
    if "/.claude/worktrees/" in path:
        return "worktree " + path.split("/.claude/worktrees/", 1)[1].split("/", 1)[0]
    if path.startswith((posix(RELEASE_CLONE) + "/", posix(_OLD_RELEASE) + "/")):
        return "release clone"
    if path.startswith(_TEMP_ROOTS) or path.startswith(posix(temp_dir()) + "/"):
        return "temp dir"
    return "checkout"


def _short(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path


def _q(line: str, cap: int = 70) -> str:
    return f"`{one_line(line.strip(), cap)}`"


def _first_changed(old: list[str], new: list[str]) -> tuple[str | None, str | None]:
    """The first removed and first added line whose text changed — a line that only moved
    or was re-indented says nothing about what the change was."""
    new_set = {ln.strip() for ln in new}
    old_set = {ln.strip() for ln in old}
    first_old = next((ln for ln in old if ln.strip() and ln.strip() not in new_set), None)
    first_new = next((ln for ln in new if ln.strip() and ln.strip() not in old_set), None)
    if first_old is None and first_new is None:  # only whitespace moved
        first_old = next((ln for ln in old if ln.strip()), old[0] if old else None)
        first_new = next((ln for ln in new if ln.strip()), new[0] if new else None)
    return first_old, first_new


def _ranges(ns: list[int]) -> str:
    """[13, 14, 15, 18] → "13-15,18" — what parse_steps reads back."""
    out, start, prev = [], None, None
    for n in sorted(set(ns)):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if prev > start else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if prev > start else str(start))
    return ",".join(out)


def change_gist(tool: str, inp: dict, result, use_result, is_error: bool, answered: bool) -> str:
    """One line of what a change did."""
    if answered and is_error:
        first = next((ln.strip() for ln in result_text(result).splitlines() if ln.strip()), "")
        return "failed: " + one_line(first.replace("<tool_use_error>", "").replace("</tool_use_error>", ""), 100)
    ur = use_result if isinstance(use_result, dict) else {}
    tail = "" if answered else " (no result — interrupted?)"
    if tool == "Write":
        n = len(str(inp.get("content", "")).splitlines())
        kind = {"create": "created", "update": "overwrote"}.get(ur.get("type"), "")
        if not kind:
            text = result_text(result)
            kind = "created" if text.startswith("File created") else "overwrote" if "has been updated" in text else "wrote"
        return f"{kind}, {n} lines{tail}"
    if tool == "NotebookEdit":
        first = next((ln for ln in str(inp.get("new_source", "")).splitlines() if ln.strip()), "")
        return f"notebook cell {inp.get('edit_mode') or 'replace'}" + (f" · + {_q(first)}" if first else "") + tail
    edits = inp.get("edits") if tool == "MultiEdit" and isinstance(inp.get("edits"), list) else [inp]
    gone: list[str] = []
    came: list[str] = []
    at = None
    patch = ur.get("structuredPatch")
    if isinstance(patch, list) and patch:
        at = patch[0].get("oldStart")
        for hunk in patch:
            for ln in hunk.get("lines", []):
                if ln.startswith("-"):
                    gone.append(ln[1:])
                elif ln.startswith("+"):
                    came.append(ln[1:])
    else:
        for e in edits:
            a = str(e.get("old_string", "")).splitlines()
            b = str(e.get("new_string", "")).splitlines()
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                if tag in ("replace", "delete"):
                    gone += a[i1:i2]
                if tag in ("replace", "insert"):
                    came += b[j1:j2]
    first_old, first_new = _first_changed(gone, came)
    s = (f"L{at} " if at else "") + f"−{len(gone)} +{len(came)}"
    if first_old is not None and first_new is not None:
        s += f" · {_q(first_old)} → {_q(first_new)}"
    elif first_new is not None:
        s += f" · + {_q(first_new)}"
    elif first_old is not None:
        s += f" · − {_q(first_old)}"
    if any(e.get("replace_all") for e in edits if isinstance(e, dict)):
        s += " (every occurrence)"
    if len(edits) > 1:
        s += f" ({len(edits)} edits)"
    return s + tail


@dataclass
class Touch:
    session_id: str
    ts: str
    op: str  # READ · EDIT · WRITE
    path: str
    turn: int | None = None  # turn number in the session's own transcript
    step: int | None = None
    prompt: str = ""
    gist: str = ""
    via: str = ""  # "subagent observer-port" — the change was made in a subagent's transcript
    sub_file: str = ""
    sub_line: int = 0
    spawned: tuple[int, int] | None = None  # (turn, step) of the parent's Agent call


@dataclass
class SessionTrace:
    session_id: str
    touches: list[Touch] = field(default_factory=list)
    cwd: str = ""
    branch: str = ""

    @property
    def latest(self) -> str:
        return max((t.ts for t in self.touches), default="")

    @property
    def changed(self) -> bool:
        return any(t.op != "READ" for t in self.touches)


@dataclass
class FileTrace:
    query: str
    suffix: str
    sessions: list[SessionTrace]
    matched_paths: Counter  # path -> sessions (a folder: each copy of it -> sessions)
    other_paths: dict[str, tuple[set, str, str]]  # path -> (sessions, first ts, last ts)
    total_sessions: int = 0
    folder: bool = False


def _index_rows(con: sqlite3.Connection, basename: str, project: str):
    clauses = ["(f.path = ? OR f.path LIKE ?)", "f.operation IN ('read', 'write', 'edit')"]
    params: list = [basename, f"%/{basename}"]
    if project:
        clauses.append("i.project_hash = ?")
        params.append(project)
    return con.execute(
        f"""SELECT i.id, i.session_id, i.timestamp, f.path, f.operation
            FROM i_files f JOIN interactions i ON i.id = f.iid
            WHERE {' AND '.join(clauses)}""",
        params,
    ).fetchall()


def _index_rows_under(con: sqlite3.Connection, folder: str, project: str, limit: int | None = None):
    f = posix(folder).strip("/")
    w = f.replace("/", "\\")  # the same folder as a Windows transcript records it
    clauses = ["(f.path LIKE ? OR f.path LIKE ? OR f.path LIKE ? OR f.path LIKE ?)",
               "f.operation IN ('read', 'write', 'edit')"]
    params: list = [f"%/{f}/%", f"{f}/%", f"%\\{w}\\%", f"{w}\\%"]
    if project:
        clauses.append("i.project_hash = ?")
        params.append(project)
    return con.execute(
        f"""SELECT i.id, i.session_id, i.timestamp, f.path, f.operation, i.user_message_preview, i.git_branch
            FROM i_files f JOIN interactions i ON i.id = f.iid
            WHERE {' AND '.join(clauses)}""" + (f" LIMIT {int(limit)}" if limit else ""),
        params,
    ).fetchall()


def _sub_files(dirs: list[Path], needle: str) -> list[str]:
    """Subagent and workflow transcripts that name the file anywhere — ripgrep's -l when it
    is there, a line scan when not. Only these files are then read whole."""
    rg = shutil.which("rg")
    if rg and dirs:
        cmd = [rg, "-l", "-F", "--glob", "**/subagents/**/*.jsonl", "--", needle, *map(str, dirs)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            if proc.returncode in (0, 1):
                return sorted(ln for ln in proc.stdout.splitlines() if ln)
        except (OSError, subprocess.SubprocessError):
            pass
    found = []
    for d in dirs:
        for f in d.glob("*/subagents/**/*.jsonl"):
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    if any(needle in line for line in fh):
                        found.append(str(f))
            except OSError:
                continue
    return sorted(found)


def _read_records(path: str) -> list[tuple[int, dict]]:
    out = []
    try:
        with open(path, "rb") as fh:
            for n, raw in enumerate(fh, 1):
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    out.append((n, msg))
    except OSError:
        pass
    return out


def _agent_label(agent_file: Path) -> tuple[str, str, str]:
    """(label, agent id, teammate name) for a subagent transcript."""
    agent_id = agent_file.stem.removeprefix("agent-")
    meta = {}
    try:
        meta = json.loads(agent_file.with_suffix(".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    parts = agent_file.parts
    if "workflows" in parts:
        wf = parts[parts.index("workflows") + 1]
        what = meta.get("description") or meta.get("agentType") or agent_id
        return f"workflow {wf} ({one_line(what, 40)})", agent_id, ""
    name = meta.get("name") or ""
    kind = meta.get("agentType") or ""
    label = f"subagent {name or agent_id}" + (f" ({kind})" if kind and kind != name else "")
    return label, agent_id, name


def _spawn_points(parent: Path) -> dict[str, tuple[int, int]]:
    """agentId or teammate name → (turn, step) of the Agent/Task call that started it."""
    out: dict[str, tuple[int, int]] = {}
    calls: dict[str, tuple[int, int]] = {}
    splitter = TurnSplitter()
    seq, step = -1, 0
    try:
        fh = open(parent, "rb")
    except OSError:
        return out
    with fh:
        for raw in fh:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if splitter.opens_turn(msg):
                seq, step = seq + 1, 0
                continue
            content = (msg.get("message") or {}).get("content") if isinstance(msg.get("message"), dict) else None
            if msg.get("type") == "assistant" and isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        step += 1
                        if b.get("name") in ("Agent", "Task"):
                            calls[b.get("id", "")] = (seq, step)
                            name = (b.get("input") or {}).get("name")
                            if name:
                                out[name] = (seq, step)
            elif msg.get("type") == "user" and isinstance(content, list):
                aid = (msg.get("toolUseResult") or {}).get("agentId") if isinstance(msg.get("toolUseResult"), dict) else None
                if aid:
                    for b in content:
                        if isinstance(b, dict) and b.get("tool_use_id") in calls:
                            out[aid] = calls[b["tool_use_id"]]
    return out


def _subagent_touches(dirs: list[Path], suffix: str, match=matches) -> list[Touch]:
    touches: list[Touch] = []
    spawns: dict[str, dict] = {}
    for path in _sub_files(dirs, suffix):
        recs = _read_records(path)  # whole: a result need not name the file it answers
        results = {}
        for _line, msg in recs:
            content = (msg.get("message") or {}).get("content") if isinstance(msg.get("message"), dict) else None
            if msg.get("type") == "user" and isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        results[b.get("tool_use_id", "")] = (b, msg.get("toolUseResult"))
        agent_file = Path(path)
        parts = agent_file.parts
        i = parts.index("subagents")
        sid, project_dir = parts[i - 1], Path(*parts[: i - 1])
        label, agent_id, name = _agent_label(agent_file)
        if sid not in spawns:
            spawns[sid] = _spawn_points(project_dir / f"{sid}.jsonl")
        spawned = spawns[sid].get(agent_id) or (spawns[sid].get(name) if name else None)
        for line, msg in recs:
            content = (msg.get("message") or {}).get("content") if isinstance(msg.get("message"), dict) else None
            if msg.get("type") != "assistant" or not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                tool, inp = b.get("name", ""), b.get("input") or {}
                if tool not in CHANGE_TOOLS and tool != READ_TOOL:
                    continue
                target = target_path(inp)
                if not match(target, suffix):
                    continue
                block, ur = results.get(b.get("id", ""), (None, None))
                touches.append(Touch(
                    session_id=sid, ts=msg.get("timestamp", "") or "",
                    op=CHANGE_TOOLS.get(tool, "READ"), path=target,
                    gist="" if tool == READ_TOOL else change_gist(
                        tool, inp, (block or {}).get("content"), ur,
                        bool(block and block.get("is_error") is True), block is not None),
                    via=label, sub_file=path, sub_line=line, spawned=spawned,
                    prompt=f"{msg.get('cwd', '')}\0{msg.get('gitBranch', '')}",
                ))
    return touches


def trace(
    query: str,
    con: sqlite3.Connection,
    dirs: list[Path],
    project: str = "",
    operations: list[str] | None = None,
    limit: int = 20,
) -> FileTrace:
    """Every session that read or changed the file, newest first, ``limit`` of them in
    full. ``dirs``: the project dirs whose subagent transcripts are scanned. A folder (a
    trailing /, a directory here, or a name no file has but files sit under) gets one
    summary per session instead (_trace_folder)."""
    from .drill import DrillError, load_turn

    q = query.strip()
    suffix = query_suffix(q.rstrip("/\\") or query)
    if q.endswith(("/", "\\")) or Path(q).expanduser().is_dir():
        return _trace_folder(query, suffix, con, dirs, project, operations, limit)
    basename = Path(suffix).name
    by_session: dict[str, SessionTrace] = {}
    other: dict[str, tuple[set, str, str]] = {}
    iids: dict[str, set] = {}
    for iid, sid, ts, path, op in _index_rows(con, basename, project):
        if matches(path, suffix):
            iids.setdefault(sid, set()).add((iid, ts or ""))
        elif Path(path).name == basename:
            seen, first, last = other.get(path, (set(), ts or "", ts or ""))
            seen.add(sid)
            other[path] = (seen, min(first, ts or first), max(last, ts or last))
    if not iids and not other and _index_rows_under(con, suffix, project, limit=1):
        return _trace_folder(query, suffix, con, dirs, project, operations, limit)

    subs = _subagent_touches(dirs, suffix)
    latest: dict[str, str] = {}
    for sid, rows in iids.items():
        latest[sid] = max(ts for _, ts in rows)
    for t in subs:
        latest[t.session_id] = max(latest.get(t.session_id, ""), t.ts)
    order = sorted(latest, key=lambda s: latest[s], reverse=True)

    for sid in order[:limit]:
        st = by_session.setdefault(sid, SessionTrace(sid))
        for iid, _ts in sorted(iids.get(sid, ())):
            seq = int(iid.rsplit("-", 1)[-1])
            try:
                turn = load_turn(iid, con=con, with_lines=False).turn
            except (DrillError, OSError):
                st.touches.append(Touch(sid, _ts, "?", suffix, turn=seq,
                                        gist="transcript gone — the index keeps only that it touched the file"))
                continue
            st.cwd = st.cwd or str(turn.prompt.get("cwd") or "")
            st.branch = st.branch or str(turn.prompt.get("gitBranch") or "")
            for s in turn.steps:
                if s.tool not in CHANGE_TOOLS and s.tool != READ_TOOL:
                    continue
                target = target_path(s.input)
                if not matches(target, suffix):
                    continue
                st.touches.append(Touch(
                    session_id=sid, ts=s.ts, op=CHANGE_TOOLS.get(s.tool, "READ"), path=target,
                    turn=seq, step=s.n, prompt=turn.prompt_text,
                    gist="" if s.tool == READ_TOOL else change_gist(
                        s.tool, s.input, s.result, s.use_result, s.is_error, s.answered),
                ))
        for t in subs:
            if t.session_id == sid:
                cwd, _, branch = t.prompt.partition("\0")
                st.cwd, st.branch = st.cwd or cwd, st.branch or branch
                t.prompt = ""
                st.touches.append(t)
        if operations:
            st.touches = [t for t in st.touches if t.op.lower() in operations]
        st.touches.sort(key=lambda t: (t.ts, t.step or 0))
    sessions = [by_session[s] for s in order[:limit] if by_session[s].touches]
    paths: Counter = Counter()
    for st in sessions:
        for p in {t.path for t in st.touches if t.op != "?"}:
            paths[p] += 1
    return FileTrace(query, suffix, sessions, paths, other, total_sessions=len(order))


def _trace_folder(query: str, folder: str, con: sqlite3.Connection, dirs: list[Path], project: str,
                  operations: list[str] | None, limit: int) -> FileTrace:
    """Every session that read or changed a file under ``folder``, from the index alone: a
    turn's files, not its steps, so a folder a hundred sessions touched answers in one query.
    Subagent transcripts are scanned as for a file, and credited to their session."""
    by_sid: dict[str, list[Touch]] = {}
    branch: dict[str, str] = {}
    for iid, sid, ts, path, op, prompt, br in _index_rows_under(con, folder, project):
        if under(path, folder):
            by_sid.setdefault(sid, []).append(Touch(sid, ts or "", op.upper(), path,
                                                    turn=int(iid.rsplit("-", 1)[-1]), prompt=prompt or ""))
            branch.setdefault(sid, br or "")
    for t in _subagent_touches(dirs, folder, match=under):
        _cwd, _, br = t.prompt.partition("\0")
        t.prompt = ""
        by_sid.setdefault(t.session_id, []).append(t)
        branch.setdefault(t.session_id, br)
    if operations:
        by_sid = {s: kept for s, ts in by_sid.items() if (kept := [t for t in ts if t.op.lower() in operations])}
    order = sorted(by_sid, key=lambda s: max(t.ts for t in by_sid[s]), reverse=True)
    sessions = [SessionTrace(s, sorted(by_sid[s], key=lambda t: (t.ts, t.turn or 0)), branch=branch.get(s, ""))
                for s in order[:limit]]
    roots: Counter = Counter()
    for st in sessions:
        for r in {_folder_split(t.path, folder)[0] for t in st.touches}:
            roots[r] += 1
    return FileTrace(query, folder, sessions, roots, {}, total_sessions=len(order), folder=True)


def _format_folder(ft: FileTrace) -> str:
    """One summary per session, newest first: the files it changed under the folder (the
    most changed first), the turns that changed them and what each asked — then where the
    user's own words are."""
    from .query import hint, verb

    f = ft.suffix.strip("/")
    changed = [s for s in ft.sessions if s.changed]
    read_only = [s for s in ft.sessions if not s.changed]
    lines = [f'"{f}/" — {len(changed)} session(s) changed files under it, {len(read_only)} only read them'
             " · newest first", "matched folders:"]
    for root, n in ft.matched_paths.most_common(6):
        lines.append(f"  {_short(root)}  ({layout(root)} · {n} session{'s' if n != 1 else ''})")
    if len(ft.matched_paths) > 6:
        lines.append(f"  … +{len(ft.matched_paths) - 6} more copies")

    for st in changed:
        edits = [t for t in st.touches if t.op in ("EDIT", "WRITE")]
        per_file: Counter = Counter()  # the path inside the folder -> turns (or subagents) that changed it
        for rel, unit in {(_folder_split(t.path, f)[1], t.via or t.turn) for t in edits}:
            per_file[rel] += 1
        turns = sorted({t.turn for t in edits if not t.via and t.turn is not None})
        only_read = {t.path for t in st.touches if t.op == "READ"} - {t.path for t in edits}
        head = f"{st.session_id} · {st.latest[:10]} {st.latest[11:16]} · {st.branch or '-'} · {len(per_file)} file(s) changed"
        head += f" in {len(turns)} turn(s)" if turns else ""
        head += f" · {len(only_read)} only read" if only_read else ""
        lines += ["", head]
        top = per_file.most_common(4)
        lines.append("  most changed: " + " · ".join(f"{rel} ({n}×)" for rel, n in top)
                     + (f" · +{len(per_file) - 4} more" if len(per_file) > 4 else ""))
        for turn in turns[:4]:
            prompt = next((t.prompt for t in st.touches if t.turn == turn and t.prompt and not t.via), "")
            # a turn a task notice or a teammate opened: nobody asked it (turns.sender)
            typed = not prompt.startswith("task notification: ") and sender(prompt) == "user"
            lines.append(f"  turn {turn:03d}{' asked:' if typed else ' ·'} {one_line(prompt, 120) or '?'}")
        if len(turns) > 4:
            lines.append(f"  … +{len(turns) - 4} more turn(s)")
        vias = list(dict.fromkeys(t.via for t in edits if t.via))
        if len(vias) > 2:
            lines.append(f"  via {len(vias)} subagents: {len({t.path for t in edits if t.via})} file(s) changed")
        for via in vias if len(vias) <= 2 else ():
            lines.append(f"  via {via}: {len({t.path for t in edits if t.via == via})} file(s) changed")
    if read_only:
        lines += ["", "only read files under it:"]
        for st in read_only:
            lines.append(f"  {st.session_id} · {st.latest[:10]} · {len({t.path for t in st.touches})} file(s)")
    lines.append("")
    tail = []
    if ft.total_sessions > len(ft.sessions):
        tail.append(f"{ft.total_sessions - len(ft.sessions)} more session(s): {hint('limit', n=ft.total_sessions)}")
    tail.append(f"what the user asked there: {hint('user', sid=(changed or ft.sessions)[0].session_id)}")
    tail.append(f"one file's changes: {verb('blame')} {f}/<file>")
    tail.append("not seen: changes made through Bash (sed -i, heredocs, git)")
    lines.append(" · ".join(tail))
    return "\n".join(lines)


def format_file_trace(ft: FileTrace, limit: int = 20) -> str:
    """One block per session, newest first: its ops, the prompt of each turn that changed
    the file, and each change as a line — then how to open those steps. A folder: one
    summary per session (_format_folder)."""
    from .query import hint, verb

    if ft.folder and ft.sessions:
        return _format_folder(ft)
    if not ft.sessions:
        base = f'No interactions found that touched "{ft.query}"'
        if ft.suffix != ft.query:
            base += f" (matched as …/{ft.suffix})"
        lines = [base + "."]
        if ft.other_paths:
            lines.append("same file name, other path:")
            lines += [f"  {_short(p)} ({layout(p)} · {len(s)} session(s), {a[:10]} → {b[:10]})"
                      for p, (s, a, b) in sorted(ft.other_paths.items(), key=lambda kv: kv[1][2], reverse=True)[:6]]
        lines.append(f"not seen: changes made through Bash (sed -i, heredocs, git) — `{verb('search')} <name> --tool Bash`")
        return "\n".join(lines)

    changed = [s for s in ft.sessions if s.changed]
    read_only = [s for s in ft.sessions if not s.changed]
    lines = [f'"{ft.suffix}" — {len(changed)} session(s) changed it, {len(read_only)} only read it · newest first']
    lines.append("matched paths:")
    for p, n in ft.matched_paths.most_common():
        lines.append(f"  {_short(p)}  ({layout(p)} · {n} session{'s' if n != 1 else ''})")
    if ft.other_paths:
        lines.append("same file name, other path — a different file, not counted above:")
        for p, (s, a, b) in sorted(ft.other_paths.items(), key=lambda kv: kv[1][2], reverse=True)[:6]:
            lines.append(f"  {_short(p)}  ({layout(p)} · {len(s)} session(s), {a[:10]} → {b[:10]})")
        if len(ft.other_paths) > 6:
            lines.append(f"  … +{len(ft.other_paths) - 6} more paths")
    several = len(ft.matched_paths) > 1

    for st in changed:
        ops = Counter(t.op for t in st.touches)
        ops_s = " ".join(f"{op}×{ops[op]}" for op in ("EDIT", "WRITE", "READ", "?") if ops[op])
        where = sorted({layout(t.path) for t in st.touches if t.op != "?"})
        lines.append("")
        lines.append(f"{st.session_id} · {st.latest[:10]} {st.latest[11:16]} · {_short(st.cwd) or '?'}"
                     f" · {st.branch or '-'} · {ops_s}" + (f" · in {', '.join(where)}" if several else ""))
        own = [t for t in st.touches if not t.via]
        for turn in sorted({t.turn for t in own if t.turn is not None}):
            in_turn = [t for t in own if t.turn == turn]
            edits = [t for t in in_turn if t.op in ("EDIT", "WRITE")]
            prompt = next((t.prompt for t in in_turn if t.prompt), "")
            reads = sum(1 for t in in_turn if t.op == "READ")
            head = f"  turn {turn:03d} asked: {one_line(prompt, 150) or '?'}"
            if reads and not edits:
                head += f"  (read ×{reads})"
            copies = {layout(t.path) for t in edits}
            if several and len(copies) == 1:
                head += f"  [{copies.pop()}]"  # one copy for the whole turn: say it once
            lines.append(head)
            for t in edits:
                mark = f" [{layout(t.path)}]" if several and len(copies) > 1 else ""
                lines.append(f"    #{t.step} {t.op.title()} {t.gist}{mark}")
            if edits:
                steps = _ranges([t.step for t in edits])
                iid = f"{st.session_id}-{turn:03d}"
                lines.append(f"    open: {hint('step', id=iid, n=steps)}")
        for t in own:
            if t.turn is None or t.op == "?":
                lines.append(f"  turn {t.turn if t.turn is not None else '?'}: {t.gist}")
        for via in dict.fromkeys(t.via for t in st.touches if t.via):
            vt = [t for t in st.touches if t.via == via]
            sp = vt[0].spawned
            spawned = f"started at turn {sp[0]:03d} #{sp[1]}" if sp else "its Agent call not found in the session"
            lines.append(f"  via {via} — {spawned} · {_short(vt[0].sub_file)}")
            for t in vt:
                if t.op == "READ":
                    continue
                lines.append(f"    :{t.sub_line} {t.op.title()} {t.gist}")
            reads = sum(1 for t in vt if t.op == "READ")
            if reads:
                lines.append(f"    (read ×{reads})")
    if read_only:
        lines.append("")
        lines.append("only read it:")
        for st in read_only:
            first = st.touches[0]
            where = f"turn {first.turn:03d} #{first.step}" if first.turn is not None else (first.via or "?")
            lines.append(f"  {st.session_id} · {st.latest[:10]} · {where} · {_short(first.path)}")
    lines.append("")
    tail = []
    if ft.total_sessions > len(ft.sessions):
        tail.append(f"{ft.total_sessions - len(ft.sessions)} more session(s): {hint('limit', n=ft.total_sessions)}")
    if changed:
        tail.append(f"what the user asked there: {hint('user', sid=changed[0].session_id)}")
    tail.append(f"not seen: changes made through Bash (sed -i, heredocs, git) — `{verb('search')} <name> --tool Bash`")
    lines.append(" · ".join(tail))
    return "\n".join(lines)
