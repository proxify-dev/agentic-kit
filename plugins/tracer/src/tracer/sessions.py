"""Every session one project holds, oldest first — "what have we been doing here?" in one call.

The other verbs want a topic (`ak sessions search`), a session (`ak sessions show`) or a file
(`ak trace blame`). A question with none of those — a timeline, a sitrep, "where do we
stand", "look at my prompts" — used to end in a script over the raw transcripts
(2026-09-24, evals/tool-routing case `timeline`). This answers it from the index: one
line per session with what it began with and where it ended up, and a coverage band
that says what the record does and does not reach.

Scope is a folder, not a directory name. Claude Code files a session under its launch
cwd, so one project's history is spread over:

    the folder itself                      ~/ak
    its dot-twin                           ~/.ak — the same name with or without a
                                           leading dot: where a project lived before it moved
                                           (the observer carries the same pair as an alias)
    every folder inside either             worktrees, app/, plugins/<p>/ …

A session dir's name drops dots and slashes alike (`-Users-x--ak`), so membership is
decided on the real cwd read from a transcript; the name only narrows the candidates.
Who started a session is search.leave_out's rule — the one gate — and the index keeps
turns whose transcripts Claude Code has since expired, so the record can reach further
back than the files on disk.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ._brand import claude_home, cmd, slug
from .verbs import spell
from .search import calling_session, leave_out, session_entrypoint
from .storage import _resolve_project_path, dir_cwd, discover_projects, get_project_hash

_ANSI = re.compile(r"\x1b\[[0-9;]*m|\^\[\[[0-9;]*m")
_SLASH_ECHO = re.compile(r"^/(\S+)\s+\1\b\s*")   # "/compact\n compact\n args" → "/compact args"
_BARE_COMMAND = re.compile(r"^/[\w:.\-]+\]?$")  # /plugin, /exit, /model — nothing asked
_TASK_NOTE = re.compile(r"^\w{6,}\s+toolu_\w+")    # a background task's notification, tags stripped
_CONTINUED = "This session is being continued from a previous conversation"

ASK_WIDTH = 110
LAST_WIDTH = 70


@dataclass
class Folder:
    hash: str
    dir: Path | None
    name: str         # ~/agentic-kit · ~/.ak · app · worktree gateway-try
    role: str         # "here" · "twin" · "inside"


@dataclass
class SessionRow:
    session_id: str
    folder: Folder
    start: str
    end: str
    turns: int
    seconds: int
    first: str
    last: str
    entrypoint: str
    same_as: list[str] = field(default_factory=list)   # later sessions that began with the same words


@dataclass
class SessionsReport:
    root: str
    folders: list[Folder]
    rows: list[SessionRow]
    unindexed: dict[str, int] = field(default_factory=dict)   # folder name → transcripts the index has not read
    automated: int = 0
    commands_only: int = 0
    excluded: int = 0
    on_disk_since: str = ""


def real_path(name: str, budget: int = 4000) -> str:
    """The folder a session-dir name stands for, found on disk: each "-" was a "/" or any other
    character Claude Code's slug() turns into one (".", " ", "_", "-" …), so each folder's
    entries are slugged and matched against the name. "" when no existing folder fits (a
    deleted worktree, a temp dir long gone) or the search runs past `budget` steps. A split
    at the earliest "-" is tried first. A Windows name starts at its drive: C--Users-x is
    C:\\Users\\x."""
    steps = 0
    listed: dict[Path, list[tuple[str, str]]] = {}

    def names(base: Path) -> list[tuple[str, str]]:
        if base not in listed:
            try:
                listed[base] = sorted(((slug(n), n) for n in os.listdir(base)), key=lambda e: len(e[0]))
            except OSError:
                listed[base] = []
        return listed[base]

    def walk(i: int, base: Path) -> str:
        nonlocal steps
        steps += 1
        if steps > budget:
            return ""
        for s, n in names(base):
            if not name.startswith(s, i):
                continue          # no entry here begins this way: a dead branch, pruned at once
            j = i + len(s)
            if j == len(name):
                return str(base / n)
            if name[j] == "-" and (base / n).is_dir():
                found = walk(j + 1, base / n)
                if found:
                    return found
        return ""

    drive = re.match(r"^([A-Za-z])--", name)
    if drive:
        return walk(3, Path(drive.group(1) + ":/"))
    return walk(1, Path("/")) if name.startswith("-") else ""


def folder_of(d: Path) -> str:
    """Where a session dir's sessions ran: its name read against the disk, else the cwd its
    newest transcript started in (a session that entered a worktree started outside it)."""
    return real_path(d.name) or dir_cwd(d)


def twin_of(root: Path) -> Path:
    name = root.name
    return root.with_name(name[1:] if name.startswith(".") else "." + name)


def resolve_root(project: str | None) -> str:
    """The folder a question means. None or "." is this session's project — in a git worktree,
    the repository's main checkout, whose folder holds the worktrees too. A path is itself; a
    session-dir name (-Users-x--ak, C--Users-x-ak) is the cwd its transcripts record, else the
    folder on disk it fits, else a best-effort reading of the name."""
    if project and project not in (".", ""):
        if project.startswith("-") or re.match(r"^[A-Za-z]--", project):
            d = claude_home() / "projects" / project
            return dir_cwd(d) or real_path(project) or _resolve_project_path(project)
        return str(Path(project).expanduser().resolve())
    start = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        common = subprocess.run(["git", "-C", start, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        common = ""
    return str(Path(common).parent) if common and Path(common).name == ".git" else start


def _inside(p: Path, base: Path) -> bool:
    return p == base or base in p.parents


def _inner_name(h: str, base_hash: str) -> str:
    """A folder inside the project, named from its dir-name tail: `app`, `worktree gateway-try`.
    The tail is lossy (a dash may have been a slash or a dot), which is fine for a label."""
    tail = h[len(base_hash):].lstrip("-")
    return "worktree " + tail[len("claude-worktrees-"):] if tail.startswith("claude-worktrees-") else tail


def scope(root: str, indexed_hashes: set[str] | None = None) -> list[Folder]:
    """The session dirs that belong to `root`: itself, its dot-twin, and every folder inside
    either. A dir whose name only starts like root's is kept when a transcript's cwd is inside
    root or the twin — the prefix alone would also take in ~/agentic-kit-wt/…; with no
    transcript left to read, it is dropped."""
    here = Path(root)
    twin = twin_of(here)
    exact = {get_project_hash(str(here)): ("here", here), get_project_hash(str(twin)): ("twin", twin)}
    dirs: dict[str, Path | None] = {h: d for h, d in discover_projects()}
    for h in indexed_hashes or ():
        dirs.setdefault(h, None)
    out: list[Folder] = []
    for h, d in sorted(dirs.items(), key=lambda kv: kv[0]):
        if h in exact:
            role, path = exact[h]
            out.append(Folder(h, d, _home(str(path)), role))
            continue
        base = next((e for e in exact if h.startswith(e + "-")), "")
        if not base:
            continue
        cwd = folder_of(d) if d is not None else ""
        if cwd and (_inside(Path(cwd), here) or _inside(Path(cwd), twin)):
            out.append(Folder(h, d, _inner_name(h, base), "inside"))
    return out


def clean(preview: str | None) -> str:
    """A prompt as the person typed it: no ANSI, command echo folded, one line."""
    t = _ANSI.sub("", preview or "")
    t = " ".join(t.split())
    return _SLASH_ECHO.sub(r"/\1 ", t).strip()


def is_noise(prompt: str) -> bool:
    """A turn that asked nothing: a bare /command, a compaction's own lines."""
    return (not prompt or bool(_BARE_COMMAND.match(prompt)) or prompt.startswith(_CONTINUED)
            or prompt.startswith("Compacted") or prompt.startswith("<local-command")
            or bool(_TASK_NOTE.match(prompt)))


def collect(
    root: str,
    con: sqlite3.Connection,
    since: str = "",
    until: str = "",
    include_automated: bool = False,
    exclude_session: str | None = None,
    entrypoints: frozenset[str] | None = None,
) -> SessionsReport:
    indexed = {r[0] for r in con.execute("SELECT DISTINCT project_hash FROM interactions")}
    folders = scope(root, indexed)
    report = SessionsReport(root=root, folders=folders, rows=[])
    if not folders:
        return report
    by_hash = {f.hash: f for f in folders}
    marks = ",".join("?" * len(by_hash))
    read: dict[str, set[str]] = {}   # the manifest: every file the indexer has read, turns or not
    for h, name in con.execute(f"SELECT project_hash, session_filename FROM manifest "
                               f"WHERE project_hash IN ({marks})", list(by_hash)):
        read.setdefault(h, set()).add(name)
    me = calling_session()
    for f in folders:
        if f.dir is not None:
            n = sum(1 for p in f.dir.glob("*.jsonl")
                    if p.name not in read.get(f.hash, ()) and p.stem != me
                    and not leave_out("", session_entrypoint(str(p)), set(), include_automated, entrypoints))
            if n:
                report.unindexed[f.name] = n
    sql = (f"SELECT session_id, project_hash, timestamp, user_message_preview, duration_seconds, "
           f"session_file FROM interactions WHERE project_hash IN ({marks})")
    params: list = list(by_hash)
    if since:
        sql += " AND timestamp >= ?"
        params.append(since)
    if until:
        sql += " AND timestamp <= ?" if "T" in until else " AND substr(timestamp, 1, 10) <= ?"
        params.append(until)
    sql += " ORDER BY timestamp"
    sessions: dict[str, list[tuple]] = {}
    for row in con.execute(sql, params):
        sessions.setdefault(row[0], []).append(row)

    exclude = {calling_session()} if exclude_session is None else ({exclude_session} - {""})
    first_seen: dict[str, SessionRow] = {}
    on_disk: list[str] = []
    for sid, turns in sessions.items():
        path = next((t[5] for t in turns if t[5]), "")
        exists = bool(path) and Path(path).exists()
        ep = session_entrypoint(path) if exists else ""
        if exists:
            on_disk.append(turns[0][2] or "")
        why = leave_out(sid, ep, exclude, include_automated, entrypoints)
        if why == "automated":
            report.automated += 1
            continue
        if why:
            report.excluded += 1
            continue
        asks = [clean(t[3]) for t in turns]
        real = [a for a in asks if not is_noise(a)]
        if not real:
            report.commands_only += 1
            continue
        row = SessionRow(
            session_id=sid, folder=by_hash[turns[0][1]], start=turns[0][2] or "",
            end=turns[-1][2] or "", turns=len(real), seconds=sum(t[4] or 0 for t in turns),
            first=real[0], last=real[-1] if len(real) > 1 else "", entrypoint=ep,
        )
        key = row.first[:ASK_WIDTH].lower()
        if key in first_seen:                    # the same opening sent to several sessions at once
            first_seen[key].same_as.append(sid)
        else:
            first_seen[key] = row
    report.rows = sorted(first_seen.values(), key=lambda r: r.start)
    report.on_disk_since = min(on_disk) if on_disk else ""
    return report


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _dur(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}"


def _home(p: str) -> str:
    home = str(Path.home())
    return "~" + p[len(home):] if p.startswith(home) else p


# --- the data shape, and its plain rendering (plugins/AGENTS.md §8) -------------------------

def as_data(report: SessionsReport, limit: int = 0) -> dict:
    """The one shape every mode renders: --json emits it, plain and rich draw it."""
    counts: dict[str, int] = {}
    for r in report.rows:
        counts[r.folder.hash] = counts.get(r.folder.hash, 0) + 1 + len(r.same_as)
    rows = report.rows[-limit:] if limit else report.rows
    reach = report.rows[0].start[:10] if report.rows else ""
    disk = report.on_disk_since[:10]
    return {
        "root": _home(report.root),
        "folders": [{"name": f.name, "role": f.role, "sessions": counts.get(f.hash, 0)} for f in report.folders
                    if f.role in ("here", "twin") or counts.get(f.hash)],
        "first": reach,
        "last": report.rows[-1].end[:10] if report.rows else "",
        "people": sum(1 + len(r.same_as) for r in report.rows),
        "left_out": {"programs": report.automated, "commands_only": report.commands_only,
                     "excluded": report.excluded},
        "transcripts_from": disk if disk and disk > reach else reach,
        "unindexed": report.unindexed,
        "lines": len(report.rows),
        "shown": len(rows),
        "sessions": [{"id": r.session_id, "start": r.start, "end": r.end, "turns": r.turns,
                      "seconds": r.seconds, "folder": r.folder.name, "role": r.folder.role,
                      "first": r.first, "last": r.last, "same_opening": r.same_as} for r in rows],
    }


def header(data: dict) -> list[tuple[str, str]]:
    """The band above the list: what the record covers and what it does not reach."""
    main = [f for f in data["folders"] if f["role"] in ("here", "twin")]
    inner = [f for f in data["folders"] if f["role"] == "inside"]
    parts = [f"{f['name']} ({f['sessions']})" + (" — the same name with a dot: where it lived before or after a move"
                                                 if f["role"] == "twin" else "") for f in main]
    if inner:
        parts.append(f"{len(inner)} folder{'s' if len(inner) > 1 else ''} inside ({sum(f['sessions'] for f in inner)}: "
                     + ", ".join(f["name"] for f in inner[:6]) + (", …" if len(inner) > 6 else "") + ")")
    rows = [("scope", " · ".join(parts))]
    if data["sessions"]:
        lo = data["left_out"]
        left = ([f"{lo['programs']} started by programs (--entrypoint all)"] if lo["programs"] else []) \
            + ([f"{lo['commands_only']} that only ran a /command"] if lo["commands_only"] else []) \
            + ([f"{lo['excluded']} excluded (this one)"] if lo["excluded"] else [])
        rows.append(("covers", f"{data['first']} → {data['last']} · {data['people']} sessions people started"
                     + (" · left out: " + ", ".join(left) if left else "")))
        note = (f"transcripts on disk from {data['transcripts_from']}; the index keeps turns from {data['first']} on"
                if data["transcripts_from"] != data["first"] else f"the record starts {data['first']}")
        rows.append(("reach", f"{note}. Earlier work is not in this record: Claude Code expires transcripts "
                              f"(cleanupPeriodDays) — `observer search` and `git log` may reach further back."))
    if data["unindexed"]:
        gaps = sorted(data["unindexed"].items(), key=lambda kv: -kv[1])
        rows.append(("gaps", f"{sum(data['unindexed'].values())} sessions here the index has not read yet ("
                             + ", ".join(f"{k} {v}" for k, v in gaps[:4]) + (", …" if len(gaps) > 4 else "")
                             + f") — not listed below; {spell('index')} --all reads them"))
    if data["shown"] < data["lines"]:
        rows.append(("shown", f"the newest {data['shown']} of {data['lines']} lines; --limit 0 lists all"))
    return rows


def days(data: dict, style=lambda kind, text: text) -> list[tuple[str, list[str]]]:
    """The list, a tree per day: `HH:MM id turns time [folder] ×N  first ask  →  last ask`."""
    out: list[tuple[str, list[str]]] = []
    for s in data["sessions"]:
        d = s["start"][:10]
        if not out or out[-1][0] != d:
            out.append((d, []))
        where = "" if s["role"] == "here" else "  " + style("label", f"[{s['folder'].removeprefix('~/')}]")
        dup = "  " + style("label", f"×{1 + len(s['same_opening'])} same opening") if s["same_opening"] else ""
        line = (f"{s['start'][11:16]}  {style('id', s['id'][:8])}  {s['turns']:>3}t {_dur(s['seconds']):>5}"
                f"{where}{dup}  {style('ask', _cut(s['first'], ASK_WIDTH))}")
        if s["last"] and s["last"][:40] != s["first"][:40]:
            line += "  →  " + style("dim", _cut(s["last"], LAST_WIDTH))
        out[-1][1].append(line)
    return out


def title(data: dict) -> str:
    return f"sessions in {data['root']} — oldest first, times UTC: what each began with → where it ended up"


def hints() -> str:
    return f"{spell('replay')} <id> — one session's prompts and answers · {spell('show')} <id> — its steps"
