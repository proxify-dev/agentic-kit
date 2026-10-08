"""SQLite-backed index storage: writer, manifest, incremental indexer, rebuild."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from ._brand import claude_home, pid_alive, project_dir, replace_retry, slug
from .db import (
    DB_PATH,
    get_write_connection,
    wal_checkpoint,
)
from .models import InteractionRecord

_CWD_B = re.compile(rb'"cwd"\s*:\s*"([^"]*)"')


def get_project_hash(project_path: str) -> str:
    """Derive the project hash used by Claude Code for storage paths: the seam's slug(),
    Claude Code's own rule — every character but a-z A-Z 0-9 becomes "-".
    e.g. /srv/me/my-plugins             -> -srv-me-my-plugins
         /srv/me/.ak                    -> -srv-me--ak
         C:\\Users\\me\\ak                -> C--Users-me-ak

    The "." rule is not cosmetic: omitting it pointed every dotted project at a
    directory that does not exist, so indexing silently found 0 files and
    reported success. The copy here once replaced only "/" and ".", which missed
    spaces, underscores and every Windows path. See owning_project_dirs() for the
    same rule applied forwards.
    """
    return slug(project_path)


def get_conversations_dir(project_path: str) -> Path:
    """Get the Claude Code conversations directory for a project."""
    return project_dir(project_path)


def get_conversations_dir_for_hash(project_hash: str) -> Path:
    """Get the Claude Code conversations directory from a project hash."""
    return claude_home() / "projects" / project_hash


def list_session_files(conversations_dir: Path) -> dict[str, float]:
    """List all JSONL session files with their mtimes."""
    sessions: dict[str, float] = {}
    if not conversations_dir.exists():
        return sessions
    for f in conversations_dir.glob("*.jsonl"):
        sessions[f.name] = f.stat().st_mtime
    return sessions


def owning_project_dirs(file_path: str) -> list[Path]:
    """Session dirs whose project cwd is an ancestor of ``file_path``.

    Claude Code slugifies a project cwd into a session-dir name by replacing
    every non-alphanumeric character with ``-`` (e.g. ``/srv/x/.ak`` ->
    ``-srv-x--ak``). We compute that slug for each ancestor directory of
    ``file_path`` and keep the ones that actually exist under
    ``<claude_home>/projects``. Existence is checked on disk.

    Used to scope a file trace to the handful of projects that could plausibly
    have touched the file, before falling back to a full cross-project scan.
    """
    projects_root = claude_home() / "projects"
    if not projects_root.exists():
        return []
    dirs: list[Path] = []
    seen: set[str] = set()
    for ancestor in Path(file_path).parents:
        name = slug(ancestor)
        if name in seen:
            continue
        seen.add(name)
        candidate = projects_root / name
        if candidate.is_dir():
            dirs.append(candidate)
    return dirs


def discover_projects() -> list[tuple[str, Path]]:
    """Discover all Claude Code project directories.

    Returns list of (project_hash, conversations_dir) tuples.
    """
    projects_root = claude_home() / "projects"
    if not projects_root.exists():
        return []
    results = []
    for d in sorted(projects_root.iterdir()):
        if d.is_dir() and any(d.glob("*.jsonl")):
            results.append((d.name, d))
    return results


class Manifest:
    """Tracks indexed sessions via the SQLite manifest/project_meta tables."""

    def __init__(self, con: sqlite3.Connection, project_hash: str):
        self._con = con
        self._project_hash = project_hash

    def get_mode(self) -> str:
        row = self._con.execute(
            "SELECT mode FROM project_meta WHERE project_hash = ?",
            (self._project_hash,),
        ).fetchone()
        return row[0] if row else "active"

    def set_mode(self, mode: str) -> None:
        self._con.execute(
            """INSERT INTO project_meta (project_hash, mode)
               VALUES (?, ?)
               ON CONFLICT(project_hash)
               DO UPDATE SET mode = excluded.mode""",
            (self._project_hash, mode),
        )

    def needs_indexing(self, filename: str, mtime: float) -> bool:
        """New, changed since, or indexed by an older parser (PARSER_VERSION).

        The version is read off the interaction rows as well as the manifest, and the
        lower one wins. A writer from before the version existed — the released Stop hook
        in every session not restarted since a release — re-indexes a changed transcript
        with its own segmentation and upserts the manifest without touching
        `parser_version`, so the manifest alone went on saying "current" over its rows.
        Its INSERT leaves the rows' column NULL, and NULL reads as 0.
        """
        from .parser import PARSER_VERSION

        row = self._con.execute(
            "SELECT mtime, parser_version FROM manifest WHERE project_hash = ? AND session_filename = ?",
            (self._project_hash, filename),
        ).fetchone()
        if not row:
            return True
        if row[0] != mtime or (row[1] or 0) < PARSER_VERSION:
            return True
        oldest = self._con.execute(
            "SELECT MIN(COALESCE(parser_version, 0)) FROM interactions WHERE session_id = ?",
            (filename.removesuffix(".jsonl"),),
        ).fetchone()[0]
        return oldest is not None and oldest < PARSER_VERSION

    def update_session(
        self, filename: str, mtime: float, record_count: int, file_size: int
    ) -> None:
        from .parser import PARSER_VERSION

        self._con.execute(
            """INSERT INTO manifest (project_hash, session_filename, mtime, record_count, file_size, parser_version)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_hash, session_filename)
               DO UPDATE SET mtime = excluded.mtime,
                             record_count = excluded.record_count,
                             file_size = excluded.file_size,
                             parser_version = excluded.parser_version""",
            (self._project_hash, filename, mtime, record_count, file_size, PARSER_VERSION),
        )

    def update_meta(self, project_path: str) -> None:
        self._con.execute(
            """INSERT INTO project_meta (project_hash, project_path, last_run, mode)
               VALUES (?, ?, ?, 'active')
               ON CONFLICT(project_hash)
               DO UPDATE SET project_path = excluded.project_path,
                             last_run = excluded.last_run""",
            (
                self._project_hash,
                project_path,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


class IndexWriter:
    """Writes interaction records to SQLite tables using batched inserts."""

    def __init__(self, con: sqlite3.Connection, project_hash: str):
        self._con = con
        self._project_hash = project_hash

    def insert_records(self, records: list[InteractionRecord]) -> None:
        """Insert records and their junction rows via executemany batches.

        Uses one executemany call per junction table per call, rather than
        per-row execute, to avoid the N-interactions * 5-junctions * N-rows
        round-trip tax.
        """
        if not records:
            return
        from .parser import PARSER_VERSION

        interaction_rows = [
            (
                rec.id,
                self._project_hash,
                rec.session_id,
                rec.session_file,
                rec.byte_offset,
                rec.timestamp,
                rec.git_branch,
                rec.user_message_preview,
                rec.assistant_reply_preview,
                rec.intent,
                rec.outcome,
                rec.tokens_estimated,
                rec.duration_seconds,
                rec.ending,
                json.dumps(rec.local_commands, ensure_ascii=False) if rec.local_commands else None,
                PARSER_VERSION,
            )
            for rec in records
        ]
        self._con.executemany(
            """INSERT INTO interactions
               (id, project_hash, session_id, session_file, byte_offset,
                timestamp, git_branch, user_message_preview, assistant_reply_preview,
                intent, outcome, tokens_estimated, duration_seconds,
                ending, local_commands, parser_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            interaction_rows,
        )

        plugin_rows: list[tuple[str, str, str, str]] = []
        skill_rows: list[tuple[str, str, str]] = []
        tool_rows: list[tuple[str, str, int]] = []
        file_rows: list[tuple[str, str, str]] = []
        agent_rows: list[tuple[str, str, str]] = []
        error_rows: list[tuple[str, str, str]] = []

        for rec in records:
            if rec.plugin_credits:
                for c in rec.plugin_credits:
                    plugin_rows.append((rec.id, c.plugin, c.via, c.how))
            else:
                for p in rec.plugins:
                    plugin_rows.append((rec.id, p, None, None))
            for sk in rec.skills_invoked:
                skill_rows.append((rec.id, sk.name, sk.plugin or None))
            for t in rec.tools_called:
                tool_rows.append((rec.id, t.name, t.count))
            for f in rec.files_touched:
                file_rows.append((rec.id, f.path, f.operation))
            for a in rec.agents_spawned:
                agent_rows.append((rec.id, a.type, a.prompt_preview))
            for e in rec.error_signals:
                error_rows.append((rec.id, e.type, e.preview))

        if plugin_rows:
            self._con.executemany(
                "INSERT INTO i_plugins (iid, plugin, via, how) VALUES (?, ?, ?, ?)", plugin_rows
            )
        if skill_rows:
            self._con.executemany(
                "INSERT INTO i_skills (iid, name, plugin) VALUES (?, ?, ?)", skill_rows
            )
        if tool_rows:
            self._con.executemany(
                "INSERT INTO i_tools (iid, name, count) VALUES (?, ?, ?)", tool_rows
            )
        if file_rows:
            self._con.executemany(
                "INSERT INTO i_files (iid, path, operation) VALUES (?, ?, ?)", file_rows
            )
        if agent_rows:
            self._con.executemany(
                "INSERT INTO i_agents (iid, type, prompt_preview) VALUES (?, ?, ?)",
                agent_rows,
            )
        if error_rows:
            self._con.executemany(
                "INSERT INTO i_errors (iid, type, preview) VALUES (?, ?, ?)", error_rows
            )

    def replace_session_records(
        self, session_id: str, new_records: list[InteractionRecord]
    ) -> None:
        """Delete existing records for a session, then insert new ones."""
        # Junction deletes use the interactions row as a subquery — avoids
        # marshalling an IN (?,?,?,…) list for large sessions.
        # Scoped by session_id ALONE, deliberately. Interaction ids are
        # f"{session_id}-{seq}" — globally unique, not project-scoped. Claude
        # Code files the same session under several project dirs (nested cwd,
        # worktrees), so a project-scoped delete left the other project's rows
        # in place and the insert died on UNIQUE(interactions.id), aborting the
        # whole --all run. One session, one home: last writer wins.
        for table in ("i_plugins", "i_tools", "i_files", "i_agents", "i_errors", "i_skills"):
            self._con.execute(
                f"""DELETE FROM {table}
                    WHERE iid IN (
                        SELECT id FROM interactions WHERE session_id = ?
                    )""",
                (session_id,),
            )
        self._con.execute(
            "DELETE FROM interactions WHERE session_id = ?",
            (session_id,),
        )
        self.insert_records(new_records)


def dir_cwd(d: Path) -> str:
    """The launch cwd of a session dir, from the first `cwd` in one of its transcripts."""
    try:
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return ""
    for f in files[:3]:
        try:
            with open(f, "rb") as fh:
                m = _CWD_B.search(fh.read(1 << 16))
        except OSError:
            continue
        if m:
            try:
                return json.loads(b'"' + m.group(1) + b'"')  # JSON escapes: a Windows cwd is C:\\Users\\…
            except ValueError:
                return m.group(1).decode("utf-8", "replace")
    return ""


def _resolve_project_path(project_hash: str) -> str:
    """A best-effort absolute path from the hash alone — lossy (a "-" may have been "/", "." or
    "-"), so a recorded cwd is preferred wherever one exists. A Windows hash starts with its
    drive (C--x-y is C:\\x\\y, shown as C:/x/y)."""
    m = re.match(r"^([A-Za-z])--", project_hash)
    if m:
        return f"{m.group(1)}:/" + project_hash[3:].replace("-", "/")
    return "/" + project_hash.lstrip("-").replace("-", "/")


def _index_project(
    con: sqlite3.Connection,
    project_hash: str,
    conversations_dir: Path,
    force: bool = False,
    project_path: str = "",
) -> int:
    """Index a single project. Returns number of interactions written. ``project_path`` is the
    cwd when the caller knows it; otherwise a transcript's recorded cwd names the project."""
    from .parser import parse_jsonl, segment_interactions

    manifest = Manifest(con, project_hash)
    writer = IndexWriter(con, project_hash)

    session_files = list_session_files(conversations_dir)

    # A missing dir used to be indistinguishable from "nothing new" — that is
    # exactly how the dotted-path bug hid for two months. Never silent again.
    if not conversations_dir.exists():
        print(
            f"  warn: no conversations dir for {project_hash} "
            f"(looked in {conversations_dir}) — indexed nothing",
            file=sys.stderr,
        )

    to_index = (
        dict(session_files)
        if force
        else {f: m for f, m in session_files.items() if manifest.needs_indexing(f, m)}
    )

    # NO REAPER HERE, DELIBERATELY.
    #
    # This used to delete every interaction whose JSONL had vanished, on the
    # theory that the index mirrors the disk. But Claude Code expires
    # transcripts on a `cleanupPeriodDays` clock (default 30), so "vanished"
    # almost always means "aged out", not "retracted" — and the reaper ran on
    # every Stop hook, quietly eating the only surviving record of the
    # conversation. ~1181 sessions went that way before anyone noticed.
    #
    # The index is an ARCHIVE, not a cache of the disk. A row outlives its
    # transcript: previews, tools, timestamps and file-touches stay queryable
    # (`show` / `grep` / `blame`); only `--drill`, which seeks a byte offset in
    # the source file, degrades — and it says so ("source JSONL not
    # available"). Stale manifest rows for gone files are harmless: the
    # staleness check only walks files that exist.
    #
    # Genuine removal is an explicit act (`claude project purge`), and should
    # stay explicit.

    total = 0
    for filename, mtime in to_index.items():
        session_id = filename.replace(".jsonl", "")
        filepath = conversations_dir / filename

        try:
            offset_messages = parse_jsonl(filepath)
        except OSError as exc:
            print(f"  warn: {filepath.name}: {exc}", file=sys.stderr)
            continue

        records = segment_interactions(
            offset_messages, session_id, session_file=str(filepath)
        )
        writer.replace_session_records(session_id, records)
        manifest.update_session(
            filename, mtime, len(records), filepath.stat().st_size
        )
        total += len(records)

    manifest.update_meta(project_path or dir_cwd(conversations_dir) or _resolve_project_path(project_hash))
    return total


def find_session_files(session_uuid: str, projects_root: Path | None = None) -> list[Path]:
    """Every transcript on disk for a session UUID, newest first.

    Claude Code files one session under several project dirs when the cwd
    moves (nested dirs, worktrees), so there can be more than one.
    """
    root = Path(projects_root) if projects_root else get_conversations_dir_for_hash("x").parent
    found = [p for p in root.glob(f"*/{session_uuid}.jsonl") if p.is_file()]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def match_session_ids(prefix: str, projects_root: Path | None = None) -> list[str]:
    """Session uuids whose transcript name starts with ``prefix`` — so an 8-character id
    copied from a listing is enough."""
    root = Path(projects_root) if projects_root else get_conversations_dir_for_hash("x").parent
    return sorted({p.stem for p in root.glob(f"*/{prefix}*.jsonl") if p.is_file()})


def ensure_session_indexed(
    session_uuid: str,
    db_path: Path | None = None,
    projects_root: Path | None = None,
) -> dict:
    """Index one session's transcript if the index has never seen it, or the
    file changed since. The self-heal behind `ak trace index --session` and the
    first thing /tracer:trace does — an empty block used to mean "the index is
    stale" three times out of four, and the model went spelunking for why.

    Returns a dict with ``status`` in {"indexed", "current", "missing"},
    plus ``path``, ``project_hash`` and ``interactions`` where they apply.
    """
    from .parser import parse_jsonl, segment_interactions

    candidates = find_session_files(session_uuid, projects_root)
    if not candidates:
        root = Path(projects_root) if projects_root else get_conversations_dir_for_hash("x").parent
        return {"status": "missing", "root": str(root), "path": "", "project_hash": "", "interactions": 0}

    path = candidates[0]
    project_hash = path.parent.name
    mtime = path.stat().st_mtime
    con = get_write_connection(db_path)
    try:
        manifest = Manifest(con, project_hash)
        if not manifest.needs_indexing(path.name, mtime):
            n = con.execute(
                "SELECT count(*) FROM interactions WHERE session_id = ?", (session_uuid,)
            ).fetchone()[0]
            return {"status": "current", "path": str(path), "project_hash": project_hash, "interactions": n}
        con.execute("BEGIN")
        try:
            records = segment_interactions(parse_jsonl(path), session_uuid, session_file=str(path))
            IndexWriter(con, project_hash).replace_session_records(session_uuid, records)
            manifest.update_session(path.name, mtime, len(records), path.stat().st_size)
            manifest.update_meta(dir_cwd(path.parent) or _resolve_project_path(project_hash))
        except Exception:
            con.execute("ROLLBACK")
            raise
        else:
            con.execute("COMMIT")
        return {"status": "indexed", "path": str(path), "project_hash": project_hash, "interactions": len(records)}
    finally:
        con.close()


def describe_index_status(result: dict, session_uuid: str) -> str:
    """One line a human or a model can act on, from ensure_session_indexed()."""
    home = str(Path.home())
    path = (result.get("path") or "").replace(home, "~")
    n = result.get("interactions", 0)
    if result["status"] == "indexed":
        return f"indexed just now: {n} interaction(s) from {path}"
    if result["status"] == "current":
        return f"current: {n} interaction(s) ({path})"
    root = (result.get("root") or "").replace(home, "~")
    return (
        f"no transcript on disk: no {session_uuid}.jsonl under {root} "
        "(aged out of Claude Code's cleanup window, or a UUID that never ran on this machine)"
    )


def incremental_index(
    project_path: str,
    quiet: bool = False,
    db_path: Path | None = None,
    index_all: bool = False,
) -> int:
    """Run incremental indexing. Returns total interaction count written."""
    # Detect and rebuild from legacy DuckDB if needed. A first run's rebuild counts as indexed: it is
    # everything this call put in, and "0 new" after bootstrapping 276 interactions reads as a failure.
    total_indexed = auto_migrate_if_needed(db_path)

    con = get_write_connection(db_path)
    failed: list[str] = []

    if index_all:
        projects = discover_projects()
    else:
        project_hash = get_project_hash(project_path)
        conv_dir = get_conversations_dir(project_path)
        projects = [(project_hash, conv_dir)]

    try:
        for project_hash, conversations_dir in projects:
            con.execute("BEGIN")
            try:
                total_indexed += _index_project(con, project_hash, conversations_dir,
                                                project_path="" if index_all else project_path)
            except Exception as exc:  # noqa: BLE001
                con.execute("ROLLBACK")
                # A --all sweep is a maintenance pass over ~hundreds of
                # projects; one poisoned project used to abort the rest and
                # leave coverage dependent on where it died. Isolate and carry
                # on. A single-project run still raises — the caller asked
                # about that project specifically.
                if not index_all:
                    raise
                failed.append(project_hash)
                print(f"  warn: {project_hash}: {exc}", file=sys.stderr)
            else:
                con.execute("COMMIT")
        wal_checkpoint(con)
    finally:
        con.close()

    if not quiet and total_indexed > 0:
        print(f"Indexed {total_indexed} interactions.", file=sys.stderr)
    if failed:
        print(
            f"warn: {len(failed)} project(s) failed to index: "
            f"{', '.join(failed[:5])}{' …' if len(failed) > 5 else ''}",
            file=sys.stderr,
        )

    return total_indexed


def detect_legacy_duckdb(path: Path) -> bool:
    """Return True if ``path`` looks like a legacy DuckDB file.

    DuckDB files start with the ASCII bytes ``DUCK`` in the first 4 bytes of the
    header. SQLite files start with ``SQLite format 3\\x00``.
    """
    if not path.exists() or path.stat().st_size < 16:
        return False
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if head.startswith(b"SQLite format 3"):
        return False
    # DuckDB's on-disk format starts with a zero byte then a "DUCK" magic at
    # offset 8 in current versions, but historical versions vary. The simplest
    # reliable test is "not SQLite" combined with file size > 0.
    return b"DUCK" in head or not head.startswith(b"SQLite")


# While an index is being built, its process keeps what it has done here (JSON: pid, phase, projects done and their
# bytes, when it started), and removes it at the end. The `ak` row (cli.brain_status) and every sessions/trace verb
# read it to say "still indexing · 38% · about 1 min left". The pid makes a crashed run's file count for nothing.
PROGRESS_NAME = "indexing"


def progress_path(db_path: Path | None = None) -> Path:
    return (db_path or DB_PATH).with_name(PROGRESS_NAME)


def say_progress(path: Path, phase: str, done: int = 0, total: int = 0, bytes_done: int = 0, bytes_total: int = 0,
                 started: float | None = None) -> None:
    """phase: "transcripts" (projects done of total, by bytes) or "tool calls" (one pass, no count)."""
    import os
    import time
    doc = {"pid": os.getpid(), "phase": phase, "done": done, "total": total, "bytes_done": bytes_done,
           "bytes_total": bytes_total, "started": time.time() if started is None else started}
    try:
        line = json.dumps(doc) + "\n"
        path.write_text(line, encoding="utf-8", newline="\n")
    except OSError:  # progress is a courtesy: never fail the index over it
        pass


def read_progress(db_path: Path | None = None) -> dict | None:
    """What an index being built now has done (say_progress's fields); None when none is being built."""
    try:
        doc = json.loads(progress_path(db_path).read_text(encoding="utf-8"))
        return doc if pid_alive(doc["pid"]) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def indexing_note(db_path: Path | None = None) -> str | None:
    """The line a sessions or trace verb says first while an index is being built, or None: what it shows is partial."""
    doc = read_progress(db_path)
    return f"still indexing your history ({progress_words(doc)}) — what you see is partial until it ends" if doc else None


def _span(seconds: float) -> str:
    return f"{int(seconds)}s" if seconds < 60 else f"{round(seconds / 60)} min"


def progress_words(doc: dict, now: float | None = None) -> str:
    """'38% · 12/26 projects · about 1 min left', or 'the tool calls · 40s so far': a person reads it."""
    import time
    took = (now or time.time()) - doc.get("started", 0)
    if doc.get("phase") == "tool calls":
        return f"the tool calls · {_span(took)} so far"
    done, total = doc.get("bytes_done", 0), doc.get("bytes_total", 0)
    words = f"{doc.get('done', 0)}/{doc.get('total', 0)} projects"
    if total:
        words = f"{100 * done // total}% · " + words
    if done and total and took > 2:
        words += f" · about {_span(took * (total - done) / done)} left"
    return words


def _shown_path(con: sqlite3.Connection, project_hash: str) -> str:
    """The project's folder as its transcripts recorded it (~ for home), else its hash: a progress line a person reads."""
    try:
        row = con.execute("SELECT project_path FROM project_meta WHERE project_hash = ?", (project_hash,)).fetchone()
    except sqlite3.Error:
        row = None
    if not row or not row[0]:
        return project_hash
    folder, home = Path(row[0]), Path.home()
    if folder == home:
        return "~"
    try:
        return str(Path("~") / folder.relative_to(home))
    except ValueError:
        return row[0]


def rebuild_from_jsonl(db_path: Path | None = None) -> int:
    """Wipe the SQLite DB and rebuild the full index from JSONL sources.

    Streams per-project progress to stderr. Returns total interaction count.
    """
    target = db_path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    # The DB file is NOT deleted. It used to be, and that made --rebuild a
    # data-loss command: it can only ever re-derive what is still on disk, so
    # every session already expired by `cleanupPeriodDays` was silently
    # dropped. Forcing a re-parse of every present transcript gets the same
    # correctness (replace_session_records overwrites each session wholesale)
    # while leaving archived-only sessions untouched.
    #
    # ponytail: no schema-migration path here — if the schema ever changes
    # incompatibly, add an explicit `--purge` rather than making the default
    # destructive again.
    con = get_write_connection(target)
    total = 0

    projects = discover_projects()
    if not projects:
        con.close()
        return 0

    print(f"Rebuilding index across {len(projects)} projects...", file=sys.stderr)
    import time
    progress, started = progress_path(target), time.time()
    sizes = [sum(f.stat().st_size for f in d.glob("*.jsonl")) if d.is_dir() else 0 for _, d in projects]
    done_bytes = 0
    try:
        for i, ((project_hash, conversations_dir), size) in enumerate(zip(projects, sizes), 1):
            say_progress(progress, "transcripts", i - 1, len(projects), done_bytes, sum(sizes), started)
            done_bytes += size
            con.execute("BEGIN")
            try:
                added = _index_project(
                    con, project_hash, conversations_dir, force=True
                )
            except Exception as exc:
                con.execute("ROLLBACK")
                print(
                    f"  [{i}/{len(projects)}] {project_hash}: FAILED ({exc})",
                    file=sys.stderr,
                )
                continue
            con.execute("COMMIT")
            total += added
            print(f"  [{i}/{len(projects)}] {_shown_path(con, project_hash)}: {added} interactions", file=sys.stderr)
        wal_checkpoint(con)
    finally:
        con.close()
        progress.unlink(missing_ok=True)

    print(f"Rebuild complete: {total} interactions.", file=sys.stderr)
    return total


def auto_migrate_if_needed(db_path: Path | None = None) -> int:
    """First-run bootstrap for the SQLite engine.

    Cases:
    * Fresh install, no DB file → do a full rebuild from JSONL.
    * Legacy DuckDB file present → rename it to ``.bak``, rebuild, then drop
      the backup.
    * Existing SQLite file → no-op.

    Returns the interactions a rebuild put in (0 when there was none).
    """
    target = db_path or DB_PATH

    if not target.exists():
        print("First run — bootstrapping conversation index...", file=sys.stderr)
        return rebuild_from_jsonl(target)

    if detect_legacy_duckdb(target):
        backup = target.with_name(target.name + ".duckdb.bak")
        print(
            f"Detected legacy DuckDB at {target}. Migrating to SQLite...",
            file=sys.stderr,
        )
        try:
            replace_retry(target, backup)  # onto a .bak left by an earlier try: rename refuses that on Windows
        except OSError as exc:
            print(f"warn: could not back up legacy file: {exc}", file=sys.stderr)
            target.unlink(missing_ok=True)
        try:
            rebuilt = rebuild_from_jsonl(target)
        finally:
            # Best-effort cleanup of the .bak (and any DuckDB .wal siblings).
            try:
                backup.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(
                    f"warn: could not delete backup {backup}: {exc}", file=sys.stderr
                )
        return rebuilt

    return 0
