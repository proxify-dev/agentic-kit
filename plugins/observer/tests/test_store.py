"""The event store: init copies claude-mem 1:1 and lifts the id floors, sync pulls
only what claude-mem wrote since, and the summariser's store sink lands a
session · prompts · observations · summary in one idempotent transaction."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
from _brand import env_name  # noqa: E402
import store_common as sc  # noqa: E402

SUMMARIZER = PLUGIN / "scripts" / "summarize-session.py"
UUID = "11111111-2222-3333-4444-555555555555"


def _fake_claude_mem(path: Path, n_obs: int = 3) -> None:
    """A db in claude-mem's shape (the DDL is 1:1, so it builds theirs), with a
    session and a few observations — but WITHOUT our three columns."""
    conn = sqlite3.connect(path)
    conn.executescript(sc.DDL)
    conn.execute("ALTER TABLE observations DROP COLUMN verdict") if "verdict" in sc.columns(conn, "observations") else None
    conn.execute("INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source, "
                 "started_at, started_at_epoch) VALUES(?,?,?,?,?,?)",
                 ("cm-session", "cm-mem-1", "demo", "claude", "2026-09-01T00:00:00Z", 1))
    for i in range(n_obs):
        conn.execute("INSERT INTO observations(memory_session_id, project, type, title, narrative, facts, "
                     "concepts, created_at, created_at_epoch) VALUES(?,?,?,?,?,?,?,?,?)",
                     ("cm-mem-1", "demo", "discovery", f"claude-mem observation {i}", "narrative",
                      json.dumps(["a fact"]), json.dumps(["demo"]), f"2026-09-01T00:00:0{i}Z", 1000 + i))
    conn.commit()
    conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "cache" / "sessions").mkdir(parents=True)
    (vault / "projects").mkdir()
    cm = tmp_path / "claude-mem.db"
    _fake_claude_mem(cm)
    monkeypatch.setenv(env_name("PATH"), str(vault))
    monkeypatch.setenv(env_name("STORE"), str(tmp_path / "store"))
    monkeypatch.setenv("CLAUDE_MEM_DB", str(cm))
    return {"vault": vault, "cm": cm, "store": tmp_path / "store" / "brain.db"}


def test_init_copies_claude_mem_and_lifts_floors(env):
    r = sc.init()
    assert r["created"] and r["forked_from"] == str(env["cm"])
    assert env["store"].exists()
    conn = sc.connect(readonly=True)
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 3
    assert {"verdict", "anchors", "supersedes"} <= set(sc.columns(conn, "observations"))
    floors = r["floors"]
    assert floors["observations"]["fork_max"] == 3
    assert floors["observations"]["floor"] == 3 + sc.GAP
    assert conn.execute("SELECT COUNT(*) FROM pending_messages").fetchone()[0] == 0
    conn.close()
    # idempotent: a second init settles, never recreates
    r2 = sc.init()
    assert not r2["created"] and r2["added"] == []


def test_init_without_claude_mem_is_an_empty_schema(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_MEM_DB", str(env["cm"].with_name("absent.db")))
    r = sc.init()
    assert r["created"] and r["forked_from"] is None
    conn = sc.connect(readonly=True)
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert "verdict" in sc.columns(conn, "observations")
    conn.close()


def test_sync_pulls_only_post_fork_rows_with_their_ids(env):
    sc.init()
    cm = sqlite3.connect(env["cm"])
    cm.execute("INSERT INTO observations(memory_session_id, project, type, title, created_at, created_at_epoch) "
               "VALUES('cm-mem-1','demo','bugfix','after the fork','2026-09-02T00:00:00Z', 2000)")
    cm.commit()
    cm.close()
    assert sc.status()["claude_mem"]["behind"]["observations"] == 1
    dry = sc.sync(dry_run=True)
    assert dry["pulled"]["observations"] == 1
    r = sc.sync()
    assert r["pulled"]["observations"] == 1
    conn = sc.connect(readonly=True)
    row = conn.execute("SELECT id, title FROM observations WHERE title='after the fork'").fetchone()
    assert row[0] == 4  # claude-mem's own id, below our floor
    assert conn.execute("SELECT COUNT(*) FROM observations_fts WHERE observations_fts MATCH 'fork'").fetchone()[0] == 1
    conn.close()
    assert sc.sync()["pulled"]["observations"] == 0  # idempotent
    assert sc.status()["claude_mem"]["behind"]["observations"] == 0


def _transcript(path: Path, cwd: str) -> None:
    lines = [
        {"type": "user", "cwd": cwd, "sessionId": UUID, "timestamp": "2026-09-14T08:00:00.000Z",
         "message": {"role": "user", "content": "please fix the login bug"}},
        {"type": "assistant", "cwd": cwd, "sessionId": UUID, "timestamp": "2026-09-14T08:00:05.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Read", "input": {"file_path": "/repo/auth.py"}},
             {"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/auth.py"}}]}},
        {"type": "user", "cwd": cwd, "sessionId": UUID, "timestamp": "2026-09-14T08:00:06.000Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "user", "cwd": cwd, "sessionId": UUID, "timestamp": "2026-09-14T08:01:00.000Z",
         "message": {"role": "user", "content": "<command-name>/compact</command-name>"}},
        {"type": "user", "cwd": cwd, "sessionId": UUID, "timestamp": "2026-09-14T08:02:00.000Z",
         "message": {"role": "user", "content": "thanks, ship it"}},
        {"type": "assistant", "cwd": cwd, "sessionId": UUID, "timestamp": "2026-09-14T08:02:30.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}},
    ]
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


OUTPUT = {
    "observations": [
        {"obs_type": "bugfix", "verdict": "worked", "anchors": ["/repo/auth.py"], "supersedes": None,
         "title": "Login bug was a stale token check", "subtitle": None, "about": "auth",
         "facts": ["token expiry compared in ms vs s"], "narrative": "Fixed by normalising to seconds.",
         "concepts": ["auth", "bugfix"], "files_read": ["/repo/auth.py"], "files_modified": ["/repo/auth.py"]},
        {"obs_type": "discovery", "verdict": "dead-end", "anchors": [], "supersedes": None,
         "title": "Clearing the cache does not fix it", "subtitle": None, "about": "auth",
         "facts": [], "narrative": "Tried first; the token was the cause. password=hunter2secret",
         "concepts": ["auth"], "files_read": [], "files_modified": []},
    ],
    "summary": {"request": "fix the login bug", "investigated": "auth.py", "learned": "ms vs s",
                "completed": "normalised expiry", "next_steps": None},
}


def _run(args: list, env_extra: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SUMMARIZER), *args], capture_output=True, text=True,
                          env={**os.environ, **env_extra}, timeout=60)


def test_summarizer_store_sink_lands_one_session(env, tmp_path):
    sc.init()
    cwd = str(tmp_path / "repo")
    Path(cwd).mkdir()
    transcript = tmp_path / f"{UUID}.jsonl"
    _transcript(transcript, cwd)
    prepared = tmp_path / "out.json"
    prepared.write_text(json.dumps(OUTPUT))
    e = {env_name("PATH"): str(env["vault"]), env_name("STORE"): str(env["store"].parent), "CLAUDE_MEM_DB": str(env["cm"])}

    r = _run(["--session", UUID, "--transcript", str(transcript), "--from-json", str(prepared), "--dry-run"], e)
    assert r.returncode == 0 and "would" not in r.stderr
    assert "[bugfix/worked] Login bug" in r.stdout and "2 prompt(s)" in r.stdout

    r = _run(["--session", UUID, "--transcript", str(transcript), "--from-json", str(prepared)], e)
    assert r.returncode == 0, r.stderr
    assert "2 observation(s), 2 prompt(s), 1 summary → store" in r.stdout

    conn = sc.connect(readonly=True)
    s = conn.execute("SELECT * FROM sdk_sessions WHERE content_session_id=?", (UUID,)).fetchone()
    assert (s["project"] == "repo" and s["platform_source"] == "brain"  # brand: historical (platform_source)
            and s["status"] == "completed")
    assert s["id"] >= sc.GAP  # above the floor: never a claude-mem id
    prompts = conn.execute("SELECT prompt_number, prompt_text FROM user_prompts WHERE content_session_id=? "
                           "ORDER BY prompt_number", (UUID,)).fetchall()
    assert [p[1] for p in prompts] == ["please fix the login bug", "thanks, ship it"]
    obs = conn.execute("SELECT type, verdict, anchors, narrative, generated_by_model, metadata FROM observations "
                       "WHERE memory_session_id=? ORDER BY id", (UUID,)).fetchall()
    assert [(o[0], o[1]) for o in obs] == [("bugfix", "worked"), ("discovery", "dead-end")]
    assert json.loads(obs[0][2]) == ["/repo/auth.py"]
    assert "[REDACTED]" in obs[1][3] and "hunter2" not in obs[1][3]
    assert obs[0][4] == "brain/haiku" and json.loads(obs[0][5])["source"] == "observer"  # brand: historical
    summ = conn.execute("SELECT request FROM session_summaries WHERE memory_session_id=?", (UUID,)).fetchone()
    assert summ[0] == "fix the login bug"
    assert conn.execute("SELECT COUNT(*) FROM observations_fts WHERE observations_fts MATCH 'stale'").fetchone()[0] == 1
    conn.close()

    # a re-run is idempotent: nothing doubles
    r = _run(["--session", UUID, "--transcript", str(transcript), "--from-json", str(prepared)], e)
    assert "0 observation(s), 0 prompt(s), 1 summary → store" in r.stdout
    conn = sc.connect(readonly=True)
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE memory_session_id=?", (UUID,)).fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM sdk_sessions WHERE content_session_id=?", (UUID,)).fetchone()[0] == 1
    conn.close()


def test_summarizer_reuses_a_session_row_claude_mem_wrote(env, tmp_path):
    sc.init()
    conn = sc.connect()
    conn.execute("INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source, "
                 "started_at, started_at_epoch) VALUES(?,?,?,?,?,?)", (UUID, "their-mem-id", "repo", "claude", "x", 1))
    conn.commit()
    conn.close()
    transcript = tmp_path / f"{UUID}.jsonl"
    _transcript(transcript, str(tmp_path / "repo"))
    prepared = tmp_path / "out.json"
    prepared.write_text(json.dumps(OUTPUT["observations"]))  # a bare array is tolerated
    e = {env_name("PATH"): str(env["vault"]), env_name("STORE"): str(env["store"].parent), "CLAUDE_MEM_DB": str(env["cm"])}
    r = _run(["--session", UUID, "--transcript", str(transcript), "--from-json", str(prepared)], e)
    assert "2 observation(s), 2 prompt(s), 0 summary → store" in r.stdout, r.stderr
    conn = sc.connect(readonly=True)
    assert conn.execute("SELECT COUNT(*) FROM sdk_sessions WHERE content_session_id=?", (UUID,)).fetchone()[0] == 1
    mids = {r[0] for r in conn.execute("SELECT memory_session_id FROM observations WHERE title LIKE 'Login%'")}
    assert mids == {"their-mem-id"}  # hung off THEIR session row, not a twin
    conn.close()


def test_obs_index_sqlite_backend_three_layers(env, monkeypatch):
    sc.init()
    sys.path.insert(0, str(PLUGIN / "src"))
    import importlib
    from observer import obs_index
    importlib.reload(obs_index)  # BACKEND is chosen at import; the store now exists
    b = obs_index.BACKEND
    assert isinstance(b, obs_index.SqliteFtsBackend)
    hits = b.search(query="observation", project="demo")
    assert len(hits) == 3 and all(h["project"] == "demo" for h in hits)
    recent = b.search()
    assert recent[0]["id"] == "3"  # most recent first
    tl = b.timeline(anchor="2", before=1, after=1)
    assert [r["id"] for r in tl["rows"]] == ["1", "2", "3"] and tl["rows"][1]["is_anchor"]
    got = b.get(["memdb:1", "999"])
    assert "claude-mem observation 0" in got[0]["content"] and got[1]["error"]


def test_an_update_off_the_indexed_columns_leaves_the_fts_index_alone(env):
    """The *_au triggers fire only on the columns FTS indexes (claude-mem v54): fold and forget write
    other columns and used to re-index the row on every write. An old any-update trigger is replaced."""
    sc.init()
    conn = sc.connect()
    conn.execute("DROP TRIGGER observations_au")
    conn.execute("CREATE TRIGGER observations_au AFTER UPDATE ON observations BEGIN SELECT 1; END")
    sc.ensure_schema(conn)
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='observations_au'").fetchone()[0]
    assert "UPDATE OF title" in sql
    conn.execute("INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, started_at, "
                 "started_at_epoch) VALUES('s', 'm', 'p', 'now', 0)")
    oid = conn.execute("INSERT INTO observations(memory_session_id, project, type, title, created_at, "
                       "created_at_epoch) VALUES('m', 'p', 'discovery', 'a pelican', 'now', 0)").lastrowid
    before = conn.total_changes
    conn.execute("UPDATE observations SET verdict='worked' WHERE id=?", (oid,))
    assert conn.total_changes - before == 1  # the row itself, no FTS delete + insert
    conn.execute("UPDATE observations SET title='a heron' WHERE id=?", (oid,))
    assert conn.execute("SELECT COUNT(*) FROM observations_fts WHERE observations_fts MATCH 'heron'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM observations_fts WHERE observations_fts MATCH 'pelican'").fetchone()[0] == 0
    conn.close()
