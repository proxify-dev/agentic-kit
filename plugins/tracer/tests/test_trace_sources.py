"""The trace prefetch's DELEGATED and OBSERVER sections.

`ak trace trace 57e7a5bf` reported "background tasks: 1" for bxeyk8xjx — a foreground
Bash result Claude Code persisted because it was large, not a task. Its CLAUDE-MEM
section read a legacy ~/.claude-mem database and said NOT AVAILABLE; the observer's
store was never consulted.
"""
from __future__ import annotations

import sqlite3

import pytest
from conftest import UUID, asst, call, result, trace_sources, user, write_jsonl

from tracer._brand import env_name
from tracer.verbs import command


@pytest.fixture
def sources(tmp_path, monkeypatch):
    src = trace_sources()
    monkeypatch.setattr(src, "CC_PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(src, "TASKS_ROOT", tmp_path / "tasks-root")
    monkeypatch.setattr(src, "VAULT_ROOT", tmp_path / "vault")
    monkeypatch.setenv(env_name("HOME"), str(tmp_path / "kit"))
    monkeypatch.delenv(env_name("STORE"), raising=False)
    return src


def test_delegated_lists_launched_tasks_not_persisted_outputs(tmp_path, sources):
    slug = "-tmp-proj"
    write_jsonl(tmp_path / "projects" / slug / f"{UUID}.jsonl", [
        user("go", 0),
        asst([call("t1", "Bash", command="npm run dev", description="dev server", run_in_background=True)], 1, "m1"),
        result("t1", "Command running in background with ID: bgtask1.", 2, tur={"backgroundTaskId": "bgtask1"}),
        asst([call("t2", "Monitor", description="watch the log", command="tail -f x")], 3, "m2"),
        result("t2", "Monitor started (task mon1)", 4, tur={"taskId": "mon1"}),
        asst([call("t3", "Bash", command="cat big")], 5, "m3"),
        result("t3", "<persisted-output>\nOutput too large (130.4KB). Full output saved to: /x/tool-results/bxeyk8xjx.txt\n</persisted-output>", 6),
    ])
    tasks = tmp_path / "tasks-root" / slug / UUID / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "bgtask1.output").write_text("compiled\nlistening on :3000\n")
    (tasks / "bxeyk8xjx.output").write_text("ROW\n" * 1000)  # the persisted foreground output

    out = sources.delegated(UUID)
    assert "background tasks: 2" in out, out
    assert "[bgtask1] Bash: dev server" in out and "listening on :3000" in out
    assert "[mon1] Monitor: watch the log — output gone" in out
    assert "bxeyk8xjx" not in out


def _store(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE sdk_sessions (content_session_id TEXT, memory_session_id TEXT, custom_title TEXT);
        CREATE TABLE observations (id INTEGER, memory_session_id TEXT, type TEXT, title TEXT,
            created_at_epoch INTEGER, folded_into INTEGER, forgotten_at TEXT);
        """
    )
    con.execute("INSERT INTO sdk_sessions VALUES (?, 'mem-1', 'Named by the observer')", (UUID,))
    con.execute("INSERT INTO sdk_sessions VALUES ('other', 'mem-2', NULL)")
    con.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_observer_rows_are_the_live_rows_of_this_session(tmp_path, sources):
    live = [(100 + i, "mem-1", "discovery", f"finding {i}", i, None, "") for i in range(17)]
    _store(tmp_path / "kit" / "db" / "brain.db", live + [
        (900, "mem-1", "decision", "forgotten one", 50, None, "2026-09-01T00:00:00Z"),
        (901, "mem-1", "decision", "folded one", 51, 5, None),
        (902, "mem-2", "decision", "another session", 52, None, None),
    ])
    out = sources.observer_rows(UUID, cap=15)
    lines = out.splitlines()
    assert lines[0] == "17 observation(s) — #id · type · title"
    assert lines[1] == "  #100 · discovery · finding 0"
    assert "  … +2 more" in lines and "forgotten one" not in out and "folded one" not in out
    assert "another session" not in out
    assert lines[-1] == f"whole rows: observer get <id> …"
    assert sources.observer_rows("nobody") == "the observer has no record of this session (it writes at session end)"


def test_observer_store_follows_brain_store_and_says_when_absent(tmp_path, sources, monkeypatch):
    assert sources.observer_rows(UUID).startswith("no observer store at ")
    _store(tmp_path / "elsewhere" / "brain.db", [])
    monkeypatch.setenv(env_name("STORE"), str(tmp_path / "elsewhere"))
    assert sources.observer_rows(UUID) == "the observer kept no live rows from this session"
    # and the store's title is the fallback when the transcript never got one
    assert sources.session_title(UUID) == "Named by the observer"


def test_the_template_renders_an_observer_section(sources, monkeypatch):
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    tpl = sources.PLUGIN_ROOT / "skills" / "trace" / "templates"
    env = Environment(loader=FileSystemLoader(str(tpl)), undefined=StrictUndefined,
                      trim_blocks=True, lstrip_blocks=True)
    fetch = __import__("bin.fetch", fromlist=["_load_config"])
    config = fetch._load_config(tpl / "report.yaml")
    assert config.include_observer and config.observer_limit == 15
    ctx = {k: "" for k in ("title_text", "index_text", "header_text", "dialogue_text", "structure_text",
                           "delegated_text", "artifacts_text", "search_text", "query")}
    ctx.update(mode="uuid", session_uuid=UUID, dialogue_full=False, include_structure=False,
               include_observer=True, observer_text="3 observation(s) — #id · type · title")
    out = env.get_template("report.md.j2").render(**ctx)
    assert "=== OBSERVER" in out and "3 observation(s)" in out and "CLAUDE-MEM" not in out


def test_the_blame_prefetch_imports():
    """blame's fetch.py looked for its engine in skills/replay — the trace skill's old
    name — so /tracer:blame died on import in every session."""
    import importlib.util

    from conftest import PLUGIN

    path = PLUGIN / "skills" / "blame" / "bin" / "fetch.py"
    spec = importlib.util.spec_from_file_location("_blame_fetch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(mod.trace_file) and "trace turn <uuid>-<turn>" in mod.HINTS
