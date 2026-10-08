"""A machine that has never indexed anything: query paths must see an empty index, not crash."""
import sqlite3

from tracer.db import get_readonly_connection


def test_a_read_only_open_with_no_index_yet_lays_down_an_empty_one(tmp_path):
    path = tmp_path / "data" / "conversation-index" / "index.db"  # neither folder nor file exists
    con = get_readonly_connection(path)
    try:
        assert path.exists()
        tables = {r[0] for r in con.execute("select name from sqlite_master where type = 'table'")}
        assert tables, "the schema is laid down so a query finds no rows instead of no table"
        try:
            con.execute("create table nope (x)")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("the connection must stay read-only")
    finally:
        con.close()


def _transcript(path):
    import json
    lines = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(m) for m in lines) + "\n")


def test_a_first_index_counts_what_it_put_in_and_leaves_no_progress_behind(tmp_path, monkeypatch, capsys):
    from tracer import storage
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    _transcript(tmp_path / "claude" / "projects" / "-srv-app" / "11111111-2222-3333-4444-555555555555.jsonl")
    db = tmp_path / "data" / "index.db"
    n = storage.incremental_index(str(tmp_path), quiet=True, db_path=db, index_all=True)
    assert n == 1, "a first run's rebuild is what it indexed: '0 new' after bootstrapping reads as a failure"
    assert "[1/1] " in (err := capsys.readouterr().err) and ": 1 interactions" in err
    assert storage.read_progress(db) is None and not db.with_name(storage.PROGRESS_NAME).exists()
    assert storage.incremental_index(str(tmp_path), quiet=True, db_path=db, index_all=True) == 0


def test_progress_counts_for_the_process_that_writes_it_only(tmp_path):
    from tracer import storage
    db = tmp_path / "index.db"
    assert storage.read_progress(db) is None and storage.indexing_note(db) is None
    storage.say_progress(storage.progress_path(db), "transcripts", 12, 26, 38, 100, started=0)
    doc = storage.read_progress(db)
    assert doc["done"] == 12 and doc["phase"] == "transcripts", "this process is alive: it is indexing"
    assert storage.progress_words(doc, now=40) == "38% · 12/26 projects · about 1 min left"
    assert "still indexing your history (38%" in storage.indexing_note(db)
    doc["pid"] = 2 ** 22 + 12345  # no such process: a run that crashed must not say "indexing" forever
    storage.progress_path(db).write_text(__import__("json").dumps(doc), encoding="utf-8")
    assert storage.read_progress(db) is None


def test_the_tool_call_pass_has_no_count_but_says_how_long_so_far():
    from tracer import storage
    assert storage.progress_words({"phase": "tool calls", "started": 0}, now=90) == "the tool calls · 2 min so far"


def test_the_ak_row_says_indexing_while_an_index_is_built(tmp_path, monkeypatch):
    from tracer import cli, db as dbmod, storage
    path = tmp_path / "index.db"
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    monkeypatch.setattr(storage, "DB_PATH", path)
    assert cli.brain_status() == [("tracer", "no index yet · ak trace index --all")], "a terminal has no `tracer` on PATH"
    storage.say_progress(storage.progress_path(), "transcripts", 3, 26, 10, 100)
    row = cli.brain_status()[0][1]
    assert "↻ indexing your history · 10% · 3/26 projects" in row and "runs in the background" in row


def test_hints_name_the_command_the_person_typed(monkeypatch):
    import sys
    from tracer import verbs
    monkeypatch.setattr(sys, "argv", ["/home/me/.local/bin/ak", "sessions"])
    assert verbs._typed() == "ak", "mounted in ak, in a terminal where `tracer` is not on PATH"
    monkeypatch.setattr(sys, "argv", ["/plugin/.venv/bin/tracer", "sessions"])
    assert verbs._typed() == "tracer"


def test_no_sessions_here_points_at_the_projects_that_have_them(capsys):
    from tracer import cli
    assert cli.print_sessions({"sessions": [], "root": "/home/me", "elsewhere": 26}) == 1
    out = capsys.readouterr().out
    assert "your history has 26 other projects" in out and "sessions ls --all" in out
    assert "index" not in out, "the index is there: never send the person to build it again"
