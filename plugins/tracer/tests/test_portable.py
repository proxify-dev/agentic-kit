"""The tracer on any OS: Claude Code's folder names, its config dir, and paths a Windows session
recorded ("C:\\Users\\x\\…") read the same as a Mac's. Windows is simulated where the rule is ours
(the seam's is_windows); the rest are cases a transcript from any machine can carry."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from conftest import trace_sources

from tracer import _brand, drill, filetrace, sessions, storage

@pytest.mark.parametrize("cwd,name", [
    ("/Users/x/plain", "-Users-x-plain"),
    ("/Users/x/.ak", "-Users-x--ak"),
    ("/Users/x/my repo", "-Users-x-my-repo"),        # a space: the old "/ and ." copy kept it
    ("/Users/x/snake_case", "-Users-x-snake-case"),  # an underscore, likewise
    ("/Users/x/café", "-Users-x-caf-"),
    ("C:\\Users\\x\\ak", "C--Users-x-ak"),
])
def test_the_project_hash_is_claude_codes_slug(cwd, name):
    assert storage.get_project_hash(cwd) == name


def test_the_projects_root_honours_claude_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert storage.get_conversations_dir("/a/b") == tmp_path / "cfg" / "projects" / "-a-b"
    assert storage.get_conversations_dir_for_hash("-a-b") == tmp_path / "cfg" / "projects" / "-a-b"


@pytest.mark.parametrize("name,path", [
    ("-Users-x-ak", "/Users/x/ak"),
    ("C--Users-x-ak", "C:/Users/x/ak"),
])
def test_a_name_alone_reads_back_drive_aware(name, path):
    assert storage._resolve_project_path(name) == path


def test_the_recorded_cwd_wins_over_the_name(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    for name, cwd in (("-p-with-dot", "/p/with.dot"), ("C--Users-x-a-b", "C:\\Users\\x\\a_b")):
        d = tmp_path / "cfg" / "projects" / name
        d.mkdir(parents=True)
        (d / "s.jsonl").write_text(json.dumps({"type": "user", "cwd": cwd}) + "\n", encoding="utf-8")
        assert storage.dir_cwd(d) == cwd, "JSON escapes decoded: a Windows cwd keeps its backslashes"
        assert sessions.resolve_root(name) == cwd
    assert sessions.resolve_root("C--nowhere-x") == "C:/nowhere/x", "nothing recorded, nothing on disk"


def test_the_index_names_a_project_by_its_recorded_cwd(tmp_path, monkeypatch):
    from tracer.db import get_write_connection
    d = tmp_path / "projects" / "-p-with-dot"
    d.mkdir(parents=True)
    (d / "s.jsonl").write_text(json.dumps({"type": "user", "cwd": "/p/with.dot", "sessionId": "s",
                                           "message": {"role": "user", "content": "hi"}}) + "\n", encoding="utf-8")
    con = get_write_connection(tmp_path / "index.db")
    storage._index_project(con, "-p-with-dot", d)
    assert con.execute("SELECT project_path FROM project_meta").fetchone()[0] == "/p/with.dot"
    con.close()


def test_a_name_is_matched_against_every_character_slug_turns_into_a_dash(tmp_path):
    p = tmp_path / "my repo" / "snake_case" / ".hidden"
    p.mkdir(parents=True)
    assert sessions.real_path(_brand.slug(p)) == str(p)


@pytest.mark.parametrize("path,suffix,hit", [
    ("/r/plugins/x/a.py", "plugins/x/a.py", True),
    ("/r/plugins/x/a.py", "x/a.py", True),
    ("/r/plugins/xx/a.py", "x/a.py", False),
    ("C:\\r\\plugins\\x\\a.py", "plugins/x/a.py", True),
    ("a.py", "a.py", True),
])
def test_a_file_is_matched_name_for_name_whatever_the_separator(path, suffix, hit):
    assert filetrace.matches(path, suffix) is hit


def test_a_windows_path_is_placed_in_its_copy():
    assert filetrace.layout("C:\\r\\.claude\\worktrees\\wt\\a.py") == "worktree wt"
    assert filetrace.layout(str(Path(tempfile.gettempdir()) / "x" / "a.py")) == "temp dir"
    assert filetrace.layout("/tmp/x/a.py") == "temp dir"
    assert filetrace.layout("/Users/x/ak/a.py") == "checkout"
    assert filetrace.under("C:\\r\\plugins\\x\\a.py", "plugins/x")


@pytest.mark.parametrize("tool,inp,raw", [
    ("Read", {"file_path": "C:\\Users\\x\\.claude\\projects\\p\\s.jsonl"}, True),
    ("Read", {"file_path": "/Users/x/.claude/sessions/1.json"}, True),
    ("Read", {"file_path": "C:\\Users\\x\\repo\\README.md"}, False),
])
def test_a_raw_read_is_seen_on_a_windows_path(tool, inp, raw):
    assert drill._is_raw_read(tool, inp) is raw


def test_a_windows_session_is_read_as_windows(monkeypatch):
    """posix() folds "\\" to "/" on Windows whatever the path's shape; the seam's own switch."""
    monkeypatch.setattr(_brand, "is_windows", lambda: True)
    assert filetrace.matches("C:\\r\\plugins\\x\\a.py", "plugins\\x\\a.py")
    assert drill._is_raw_read("Grep", {"path": "\\\\srv\\h\\.claude\\projects"})


def test_a_windows_engine_override_keeps_its_backslashes(tmp_path):
    """CONVO_INDEXER_CMD=C:\\x\\tracer.exe: a POSIX shlex made it C:xtracer.exe."""
    split = trace_sources().split_cmd
    assert split(r"C:\x\tracer.exe replay", windows=True) == [r"C:\x\tracer.exe", "replay"]
    assert split(r"'C:\Users\José\My Proj\ak.exe' tracer", windows=True) == [r"C:\Users\José\My Proj\ak.exe", "tracer"]
    assert split(r'"C:\My Proj\py.exe" -m tracer.cli', windows=True) == [r"C:\My Proj\py.exe", "-m", "tracer.cli"]
    exe = tmp_path / "My Proj" / "tracer.exe"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")
    assert split(str(exe), windows=True) == [str(exe)]  # one existing file, spaces and all
    assert split("'/opt/My Proj/ak' tracer", windows=False) == ["/opt/My Proj/ak", "tracer"]


def test_background_tasks_live_where_claude_code_puts_them(monkeypatch):
    src = trace_sources()
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    if hasattr(os, "getuid"):
        assert src._tasks_root() == Path("/tmp") / f"claude-{os.getuid()}", "POSIX: /tmp, not $TMPDIR"
        monkeypatch.delattr(os, "getuid")
    monkeypatch.setattr(src, "is_windows", lambda: True)
    assert src._tasks_root() == Path(tempfile.gettempdir()) / "claude-0", "Windows: the OS temp dir, no uid"
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", "/elsewhere")
    assert src._tasks_root() == Path("/elsewhere") / "claude-0"
