"""SQLite (WAL mode) storage engine for the conversation index.

Replaces the previous DuckDB implementation. SQLite WAL lets many query
processes open read-only connections concurrently while the indexer (the
sole writer) updates the database in the background. Cold open is ~0.2ms,
so short-lived CLI invocations no longer pay the 22ms DuckDB warmup tax.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ._brand import claude_home

# Default database location
DB_DIR = claude_home() / "plugins" / "data" / "conversation-index"
DB_PATH = DB_DIR / "index.db"

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS interactions (
    id TEXT PRIMARY KEY,
    project_hash TEXT NOT NULL,
    session_id TEXT,
    session_file TEXT,
    byte_offset INTEGER,
    timestamp TEXT,
    git_branch TEXT,
    user_message_preview TEXT,
    assistant_reply_preview TEXT,
    intent TEXT,
    outcome TEXT,
    tokens_estimated INTEGER,
    duration_seconds INTEGER
);

CREATE TABLE IF NOT EXISTS i_plugins (iid TEXT, plugin TEXT);
CREATE TABLE IF NOT EXISTS i_tools (iid TEXT, name TEXT, count INTEGER);
CREATE TABLE IF NOT EXISTS i_files (iid TEXT, path TEXT, operation TEXT);
CREATE TABLE IF NOT EXISTS i_agents (iid TEXT, type TEXT, prompt_preview TEXT);
CREATE TABLE IF NOT EXISTS i_errors (iid TEXT, type TEXT, preview TEXT);
CREATE TABLE IF NOT EXISTS i_skills (iid TEXT, name TEXT, plugin TEXT);

CREATE TABLE IF NOT EXISTS manifest (
    project_hash TEXT,
    session_filename TEXT,
    mtime REAL,
    record_count INTEGER,
    file_size INTEGER,
    PRIMARY KEY (project_hash, session_filename)
);

CREATE TABLE IF NOT EXISTS project_meta (
    project_hash TEXT PRIMARY KEY,
    project_path TEXT,
    last_run TEXT,
    mode TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);

CREATE INDEX IF NOT EXISTS idx_i_project ON interactions(project_hash);
CREATE INDEX IF NOT EXISTS idx_i_session ON interactions(session_id);
CREATE INDEX IF NOT EXISTS idx_i_ts ON interactions(timestamp);
CREATE INDEX IF NOT EXISTS idx_i_branch ON interactions(git_branch);

CREATE INDEX IF NOT EXISTS idx_ip_plugin ON i_plugins(plugin);
CREATE INDEX IF NOT EXISTS idx_ip_iid ON i_plugins(iid);
CREATE INDEX IF NOT EXISTS idx_it_name ON i_tools(name);
CREATE INDEX IF NOT EXISTS idx_it_iid ON i_tools(iid);
CREATE INDEX IF NOT EXISTS idx_if_iid ON i_files(iid);
CREATE INDEX IF NOT EXISTS idx_if_op ON i_files(operation);
CREATE INDEX IF NOT EXISTS idx_if_path ON i_files(path);
CREATE INDEX IF NOT EXISTS idx_ia_iid ON i_agents(iid);
CREATE INDEX IF NOT EXISTS idx_ie_iid ON i_errors(iid);
CREATE INDEX IF NOT EXISTS idx_is_iid ON i_skills(iid);
CREATE INDEX IF NOT EXISTS idx_is_name ON i_skills(name);
"""


def _apply_pragmas(con: sqlite3.Connection, *, readonly: bool) -> None:
    """Apply connection-level pragmas. WAL is set once per DB (persistent)."""
    if not readonly:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("PRAGMA temp_store=MEMORY")


# Columns added after the first release, per table. Additive only: an older reader
# names its columns and never sees these; a newer reader of an unmigrated file gets
# NULL for them (query._rec_cols).
ADDED_COLUMNS = {
    "interactions": (
        ("assistant_reply_preview", "TEXT"),
        ("ending", "TEXT"),
        ("local_commands", "TEXT"),
        # The parser that wrote the row (storage.Manifest.needs_indexing). On the row, not
        # only on the manifest: an older writer's manifest upsert leaves the manifest's
        # column as it found it, but its INSERT names its own columns and so leaves this NULL.
        ("parser_version", "INTEGER"),
    ),
    "manifest": (("parser_version", "INTEGER DEFAULT 0"),),
    # why an interaction is credited to a plugin: via mcp · skill · agent · file, and how
    # sure (exact · inferred) — catalog.py
    "i_plugins": (("via", "TEXT"), ("how", "TEXT")),
}


def migrate_schema(con: sqlite3.Connection) -> None:
    """Apply incremental schema migrations for existing databases."""
    for table, added in ADDED_COLUMNS.items():
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, decl in added:
            if name not in cols:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def ensure_schema(con: sqlite3.Connection) -> None:
    """Create all tables and indexes if they don't exist and set the schema version."""
    con.executescript(SCHEMA_SQL)
    migrate_schema(con)
    row = con.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    if row is None:
        con.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row[0] < SCHEMA_VERSION:
        con.execute(
            "UPDATE schema_version SET version = ? WHERE version = ?",
            (SCHEMA_VERSION, row[0]),
        )


def get_write_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a read-write connection, ensuring the schema exists.

    Called exclusively from indexer write paths (`incremental_index`,
    `rebuild_from_jsonl`). Query paths must use :func:`get_readonly_connection`.
    """
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), isolation_level=None, timeout=30.0)
    _apply_pragmas(con, readonly=False)
    ensure_schema(con)
    return con


def get_readonly_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a read-only SQLite connection via file: URI.

    Multiple read-only connections may exist concurrently with an active
    writer — WAL mode guarantees readers see a consistent snapshot and do
    not block on writers.
    """
    path = db_path or DB_PATH
    if not path.exists():
        # First run on a machine: no index yet. mode=ro cannot create the file ("unable to open
        # database file"), so lay down the empty schema; this reads nothing and indexes nothing.
        get_write_connection(path).close()
    con = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
        isolation_level=None,
        timeout=30.0,
    )
    _apply_pragmas(con, readonly=True)
    return con


def wal_checkpoint(con: sqlite3.Connection) -> None:
    """Run a TRUNCATE checkpoint so the WAL file does not grow unbounded.

    Called at the end of every indexer run.
    """
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError:
        # Checkpoint is best-effort — readers may block a truncate briefly.
        pass
