#!/usr/bin/env python3
# Derived from Claude-Mem v13.11.0 (Apache-2.0, Copyright 2026 Alex Newman) and modified: the schema, ported to Python. See NOTICE.
"""observer store — the event store, shared low-level.

    ~/.ak/db/brain.db       ($AK_STORE overrides the directory)

ONE store, in the kit's db/ (vault_root.db_dir(): $AK_STORE, else <kit home>/db), whichever
scope a session runs in. A project's `.ak/` holds that project's notes; its sessions are
rows here, labelled by project, so one search spans every project and a split vault never
splits the log.

The vault is what a person decided to keep; the store is what the machine
remembers. Sessions, prompts, observations and summaries land HERE as rows —
never as notes — because they are events: they happened, they do not change,
and there are tens of thousands of them. A folder of markdown was the wrong
substrate for that (543 notes, zero inbound links, and a walk the app could
not afford past a few thousand files).

Schema is claude-mem's as of their schema v40 (v13.11, the fork) — same tables, same
columns, same FTS5 triggers, the *_au ones scoped to the indexed columns as theirs are since v54 —
so every reader written against ~/.claude-mem/claude-mem.db keeps working when
its path constant changes, every `memdb:<id>` reference in the vault still
resolves, and the migration is a copy. Three columns are ours, added on top of
`observations`: `verdict`, `anchors`, `supersedes` — the ledger shape the
markdown observer already produced.

`pending_messages` is kept for shape and never written: it was claude-mem's
LLM-coupled queue, the place a session's memory was dropped when the model said
no. Our hooks append to a JSONL spool (<kit>/cache/sessions/) and the
summariser writes rows at session end; the raw transcript is always there to
rebuild from (`observer store backfill`).

`observations.relevance_count` is claude-mem's too, inherited on the copy, and
no code anywhere — this store, another reader of it, `ak memory harvest` —
writes or reads it (checked 2026-09-27). It is not an importance signal; it is a column with a default that
nothing has ever touched. Left in place, no migration: dropping a live column
is its own hazard and the field costs nothing sitting at 0. Treat it as dead
until something starts writing it.

Two sides can write one session during the parallel window (claude-mem on,
ours on). Ids never collide because `init` raises every AUTOINCREMENT floor by
GAP past claude-mem's maximum at fork time; `sync` later pulls claude-mem's
post-fork rows in with their own ids, below the floor, untouched.

Dependency-free: hooks run under bare python3 and cannot import the installed
`ak` package. The CLI (src/ak/store.py) imports THIS file by path.
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault_root import brain_root, db_dir, home_root  # noqa: E402,F401
from _brand import env  # noqa: E402
from capture_common import command  # noqa: E402

SCHEMA_VERSION = 1
GAP = 1_000_000
# FK order — parents first, so sync and a fresh write both land cleanly.
TABLES = ["sdk_sessions", "user_prompts", "observations", "session_summaries"]
# The migration list: ensure_schema() ALTERs in whatever is missing. Any other
# reader of this store must read the same extra columns: keep them in step.
OUR_COLUMNS = {
    "observations": [
        ("verdict", "TEXT"),
        ("anchors", "TEXT"),
        ("supersedes", "TEXT"),
        # pass 3 — after the row is written
        ("until", "TEXT"),  # YYYY-MM-DD the claim stops being true
        ("folds", "TEXT"),  # JSON ids a summary row stands for
        ("folded_into", "INTEGER"),  # the summary that speaks for this row
        ("forgotten_at", "TEXT"),
        ("forget_reason", "TEXT"),
    ]
}


def vault_root(cwd: str | None = None) -> Path:
    return brain_root(cwd)


def brain_now() -> float:
    """Epoch seconds — AK_NOW when set and readable, else the clock. The
    a bare date is LOCAL midnight, an ISO
    instant or epoch ms is exact, garbage is the real clock. The observer
    dates its rows off this so a demo that walks a story forward a day at a
    time writes rows dated in the story, not today."""
    raw = (env("NOW") or "").strip()
    if not raw:
        return time.time()
    if raw.isdigit() and len(raw) >= 10:
        return int(raw) / 1000.0
    try:
        from datetime import datetime, timezone
        if len(raw) == 10:
            return datetime.strptime(raw, "%Y-%m-%d").timestamp()
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return time.time()


def store_dir() -> Path:
    return Path(db_dir())


def store_db() -> Path:
    return store_dir() / "brain.db"


def claude_mem_db() -> Path:
    return Path(os.environ.get("CLAUDE_MEM_DB") or "~/.claude-mem/claude-mem.db").expanduser()


# ── the schema, claude-mem's v40 (13.11) plus our columns and indexes ─────────────────────────────────────
DDL = """
CREATE TABLE IF NOT EXISTS schema_versions (
  id INTEGER PRIMARY KEY,
  version INTEGER UNIQUE NOT NULL,
  applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sdk_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content_session_id TEXT NOT NULL,
  memory_session_id TEXT UNIQUE,
  project TEXT NOT NULL,
  platform_source TEXT NOT NULL DEFAULT 'claude',
  user_prompt TEXT,
  started_at TEXT NOT NULL,
  started_at_epoch INTEGER NOT NULL,
  completed_at TEXT,
  completed_at_epoch INTEGER,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'completed', 'failed')),
  worker_port INTEGER,
  prompt_counter INTEGER DEFAULT 0,
  custom_title TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sdk_sessions_platform_content ON sdk_sessions(platform_source, content_session_id);
CREATE TABLE IF NOT EXISTS user_prompts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_db_id INTEGER,
  content_session_id TEXT NOT NULL,
  prompt_number INTEGER NOT NULL,
  prompt_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL, synced_at INTEGER,
  FOREIGN KEY(session_db_id) REFERENCES sdk_sessions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_session_id TEXT NOT NULL,
  project TEXT NOT NULL,
  text TEXT,
  type TEXT NOT NULL,
  title TEXT,
  subtitle TEXT,
  facts TEXT,
  narrative TEXT,
  concepts TEXT,
  files_read TEXT,
  files_modified TEXT,
  prompt_number INTEGER,
  discovery_tokens INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL, content_hash TEXT, generated_by_model TEXT, relevance_count INTEGER DEFAULT 0, merged_into_project TEXT, agent_type TEXT, agent_id TEXT, metadata TEXT, synced_at INTEGER,
  FOREIGN KEY(memory_session_id) REFERENCES sdk_sessions(memory_session_id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_observations_sdk_session ON observations(memory_session_id);
CREATE INDEX IF NOT EXISTS idx_observations_project ON observations(project);
CREATE INDEX IF NOT EXISTS idx_observations_type ON observations(type);
CREATE INDEX IF NOT EXISTS idx_observations_created ON observations(created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_observations_content_hash ON observations(content_hash, created_at_epoch);
CREATE INDEX IF NOT EXISTS idx_observations_project_created ON observations(project, created_at_epoch DESC);
CREATE TABLE IF NOT EXISTS session_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_session_id TEXT NOT NULL,
  project TEXT NOT NULL,
  request TEXT,
  investigated TEXT,
  learned TEXT,
  completed TEXT,
  next_steps TEXT,
  files_read TEXT,
  files_edited TEXT,
  notes TEXT,
  prompt_number INTEGER,
  discovery_tokens INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL, merged_into_project TEXT, synced_at INTEGER,
  FOREIGN KEY(memory_session_id) REFERENCES sdk_sessions(memory_session_id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_summaries_sdk_session ON session_summaries(memory_session_id);
CREATE INDEX IF NOT EXISTS idx_session_summaries_project ON session_summaries(project);
CREATE INDEX IF NOT EXISTS idx_session_summaries_created ON session_summaries(created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_session_summaries_project_created ON session_summaries(project, created_at_epoch DESC);
CREATE TABLE IF NOT EXISTS pending_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_db_id INTEGER NOT NULL,
  content_session_id TEXT NOT NULL,
  message_type TEXT NOT NULL CHECK(message_type IN ('observation', 'summarize')),
  tool_name TEXT,
  tool_input TEXT,
  tool_response TEXT,
  cwd TEXT,
  last_user_message TEXT,
  last_assistant_message TEXT,
  prompt_number INTEGER,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'processed', 'failed')),
  created_at_epoch INTEGER NOT NULL,
  started_processing_at_epoch INTEGER,
  agent_type TEXT, agent_id TEXT, tool_use_id TEXT,
  FOREIGN KEY (session_db_id) REFERENCES sdk_sessions(id) ON DELETE CASCADE
);
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
  title, subtitle, narrative, text, facts, concepts,
  content='observations', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
  INSERT INTO observations_fts(rowid, title, subtitle, narrative, text, facts, concepts)
  VALUES (new.id, new.title, new.subtitle, new.narrative, new.text, new.facts, new.concepts);
END;
CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
  INSERT INTO observations_fts(observations_fts, rowid, title, subtitle, narrative, text, facts, concepts)
  VALUES('delete', old.id, old.title, old.subtitle, old.narrative, old.text, old.facts, old.concepts);
END;
CREATE TRIGGER IF NOT EXISTS observations_au
  AFTER UPDATE OF title, subtitle, narrative, text, facts, concepts ON observations BEGIN
  INSERT INTO observations_fts(observations_fts, rowid, title, subtitle, narrative, text, facts, concepts)
  VALUES('delete', old.id, old.title, old.subtitle, old.narrative, old.text, old.facts, old.concepts);
  INSERT INTO observations_fts(rowid, title, subtitle, narrative, text, facts, concepts)
  VALUES (new.id, new.title, new.subtitle, new.narrative, new.text, new.facts, new.concepts);
END;
CREATE VIRTUAL TABLE IF NOT EXISTS session_summaries_fts USING fts5(
  request, investigated, learned, completed, next_steps, notes,
  content='session_summaries', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS session_summaries_ai AFTER INSERT ON session_summaries BEGIN
  INSERT INTO session_summaries_fts(rowid, request, investigated, learned, completed, next_steps, notes)
  VALUES (new.id, new.request, new.investigated, new.learned, new.completed, new.next_steps, new.notes);
END;
CREATE TRIGGER IF NOT EXISTS session_summaries_ad AFTER DELETE ON session_summaries BEGIN
  INSERT INTO session_summaries_fts(session_summaries_fts, rowid, request, investigated, learned, completed, next_steps, notes)
  VALUES('delete', old.id, old.request, old.investigated, old.learned, old.completed, old.next_steps, old.notes);
END;
CREATE TRIGGER IF NOT EXISTS session_summaries_au
  AFTER UPDATE OF request, investigated, learned, completed, next_steps, notes ON session_summaries BEGIN
  INSERT INTO session_summaries_fts(session_summaries_fts, rowid, request, investigated, learned, completed, next_steps, notes)
  VALUES('delete', old.id, old.request, old.investigated, old.learned, old.completed, old.next_steps, old.notes);
  INSERT INTO session_summaries_fts(rowid, request, investigated, learned, completed, next_steps, notes)
  VALUES (new.id, new.request, new.investigated, new.learned, new.completed, new.next_steps, new.notes);
END;
CREATE VIRTUAL TABLE IF NOT EXISTS user_prompts_fts USING fts5(
  prompt_text, content='user_prompts', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS user_prompts_ai AFTER INSERT ON user_prompts BEGIN
  INSERT INTO user_prompts_fts(rowid, prompt_text) VALUES (new.id, new.prompt_text);
END;
CREATE TRIGGER IF NOT EXISTS user_prompts_ad AFTER DELETE ON user_prompts BEGIN
  INSERT INTO user_prompts_fts(user_prompts_fts, rowid, prompt_text) VALUES('delete', old.id, old.prompt_text);
END;
CREATE TRIGGER IF NOT EXISTS user_prompts_au AFTER UPDATE OF prompt_text ON user_prompts BEGIN
  INSERT INTO user_prompts_fts(user_prompts_fts, rowid, prompt_text) VALUES('delete', old.id, old.prompt_text);
  INSERT INTO user_prompts_fts(rowid, prompt_text) VALUES (new.id, new.prompt_text);
END;
CREATE TABLE IF NOT EXISTS brain_meta (key TEXT PRIMARY KEY, value TEXT);
"""


# ── connections ──────────────────────────────────────────────────────────────
def connect(path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    """WAL, a busy timeout, uri mode (so a read-only open and a read-only ATTACH
    are both honest about it). Never creates the file when readonly."""
    p = Path(path or store_db())
    mode = "ro" if readonly else "rwc"
    # A kit that has never run `observer store init` has no db/ yet — the
    # first writer (the observer, after its first substantive session) is the
    # one that must create it, or a project scope never gets a store at all.
    if not readonly:
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{p}?mode={mode}", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


def columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> list:
    return [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    try:
        r = conn.execute("SELECT value FROM brain_meta WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    return json.loads(r[0]) if r else default


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT INTO brain_meta(key, value) VALUES(?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


def ensure_schema(conn: sqlite3.Connection) -> list:
    """Idempotent. Returns the columns it added (empty on a settled db)."""
    # The *_au triggers once fired on ANY update, so `fold` and `forget` (which touch no indexed
    # column) re-wrote the row's FTS entry each time and grew the index. IF NOT EXISTS would keep
    # an old trigger, so one without `UPDATE OF` is dropped here and the DDL below re-creates it.
    for name in ("observations_au", "session_summaries_au", "user_prompts_au"):
        r = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
        if r and "UPDATE OF" not in (r[0] or ""):
            conn.execute(f"DROP TRIGGER {name}")
    conn.executescript(DDL)
    added = []
    for table, cols in OUR_COLUMNS.items():
        have = set(columns(conn, table))
        for name, typ in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
                added.append(f"{table}.{name}")
    set_meta(conn, "schema_version", SCHEMA_VERSION)
    conn.commit()
    return added


def _seq(conn: sqlite3.Connection, table: str) -> int:
    r = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
    return int(r[0]) if r else 0


def _set_seq(conn: sqlite3.Connection, table: str, seq: int) -> None:
    cur = conn.execute("UPDATE sqlite_sequence SET seq=? WHERE name=?", (seq, table))
    if cur.rowcount == 0:
        conn.execute("INSERT INTO sqlite_sequence(name, seq) VALUES(?, ?)", (table, seq))


def raise_floors(conn: sqlite3.Connection, gap: int = GAP) -> dict:
    """Push every AUTOINCREMENT past claude-mem's reach. Records, per table, the
    fork point (`fork_max`) sync starts from and the floor our own ids start at."""
    out = {}
    for t in TABLES:
        mx = int(conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t}").fetchone()[0])
        floor = max(mx, _seq(conn, t)) + gap
        _set_seq(conn, t, floor)
        set_meta(conn, f"fork_max.{t}", mx)
        set_meta(conn, f"floor.{t}", floor)
        out[t] = {"fork_max": mx, "floor": floor}
    conn.commit()
    return out


# ── the three operations ─────────────────────────────────────────────────────
def init(force: bool = False, from_claude_mem: bool = True) -> dict:
    """Bring the store into being. A copy of claude-mem's db when there is one
    (sqlite's backup API — safe against a live WAL writer, unlike cp), an empty
    1:1 schema otherwise. Re-running on an existing store only settles the
    schema; `force` throws the store away first."""
    dst = store_db()
    dst.parent.mkdir(parents=True, exist_ok=True)
    existed = dst.exists()
    if existed and not force:
        conn = connect()
        added = ensure_schema(conn)
        conn.close()
        return {"path": str(dst), "created": False, "added": added, "forked_from": None}
    if existed:
        for suffix in ("", "-wal", "-shm"):
            Path(str(dst) + suffix).unlink(missing_ok=True)
    src = claude_mem_db()
    forked = None
    if from_claude_mem and src.exists():
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
        d = sqlite3.connect(dst)
        with d:
            s.backup(d)
        s.close()
        d.close()
        forked = str(src)
    conn = connect()
    added = ensure_schema(conn)
    floors = raise_floors(conn) if forked else {}
    if forked:
        set_meta(conn, "forked_from", forked)
        set_meta(conn, "forked_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        conn.execute("DELETE FROM pending_messages")  # their queue, never ours
    conn.commit()
    conn.close()
    return {"path": str(dst), "created": True, "added": added, "forked_from": forked, "floors": floors}


def status() -> dict:
    dst = store_db()
    out = {"path": str(dst), "exists": dst.exists(), "size": 0, "counts": {}, "last_observation": "",
           "ours": 0, "meta": {},
           "claude_mem": {"path": str(claude_mem_db()), "exists": claude_mem_db().exists(), "behind": {}}}
    if not dst.exists():
        return out
    out["size"] = dst.stat().st_size
    conn = connect(readonly=True)
    for t in TABLES:
        try:
            out["counts"][t] = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        except sqlite3.OperationalError:
            out["counts"][t] = None
    try:
        r = conn.execute("SELECT MAX(created_at) FROM observations").fetchone()
        out["last_observation"] = r[0] or ""
        out["ours"] = int(conn.execute(
            "SELECT COUNT(*) FROM observations WHERE generated_by_model LIKE 'brain%'").fetchone()[0])  # brand: historical
    except sqlite3.OperationalError:
        pass
    try:
        out["meta"] = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT key, value FROM brain_meta")}
    except sqlite3.OperationalError:
        pass
    if claude_mem_db().exists():
        try:
            conn.execute("ATTACH DATABASE ? AS cm", (f"file:{claude_mem_db()}?mode=ro",))
            for t in TABLES:
                fork_max = out["meta"].get(f"fork_max.{t}", 0)
                floor = out["meta"].get(f"floor.{t}", 1 << 62)
                out["claude_mem"]["behind"][t] = int(conn.execute(
                    f"SELECT COUNT(*) FROM cm.{t} WHERE id > ? AND id < ?", (fork_max, floor)).fetchone()[0])
        except sqlite3.OperationalError as exc:
            out["claude_mem"]["error"] = str(exc)
    conn.close()
    return out


def sync(dry_run: bool = False) -> dict:
    """Pull claude-mem's post-fork rows in, with their own ids. Idempotent: keyed
    on id, and the fork point advances only as far as what was actually seen."""
    src = claude_mem_db()
    if not src.exists():
        return {"error": f"{src} is absent"}
    if not store_db().exists():
        return {"error": f"no store — run `{command('store init')}` first"}
    conn = connect()
    conn.execute("ATTACH DATABASE ? AS cm", (f"file:{src}?mode=ro",))
    pulled = {}
    for t in TABLES:
        fork_max = get_meta(conn, f"fork_max.{t}", 0)
        floor = get_meta(conn, f"floor.{t}", 1 << 62)
        ours = set(columns(conn, t))
        shared = [c for c in columns(conn, t, "cm") if c in ours]
        cols = ", ".join(shared)
        n = conn.execute(f"SELECT COUNT(*), COALESCE(MAX(id), ?) FROM cm.{t} WHERE id > ? AND id < ?",
                         (fork_max, fork_max, floor)).fetchone()
        pulled[t] = int(n[0])
        if dry_run or not n[0]:
            continue
        conn.execute(f"INSERT OR IGNORE INTO main.{t} ({cols}) SELECT {cols} FROM cm.{t} "
                     f"WHERE id > ? AND id < ?", (fork_max, floor))
        set_meta(conn, f"fork_max.{t}", int(n[1]))
    if not dry_run:
        set_meta(conn, "synced_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        conn.commit()
    conn.close()
    return {"pulled": pulled, "dry_run": dry_run}
