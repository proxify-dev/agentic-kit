"""`ak sessions live` (old spelling `ak tracer live`): the registry's live entries joined to their transcripts."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from conftest import asst, call, printed, text, user, write_jsonl

from tracer import cli, drill, live
from tracer.verbs import command

UUID_A = "aaaaaaaa-0000-4000-8000-000000000001"  # asked twice, answered
UUID_B = "bbbbbbbb-0000-4000-8000-000000000002"  # mid-call, no answer yet
UUID_C = "cccccccc-0000-4000-8000-000000000003"  # a program's session, nothing asked


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _entry(reg, pid, sid, name, status="idle", **extra):
    rec = {"pid": pid, "sessionId": sid, "cwd": "/tmp/proj", "name": name, "status": status,
           "startedAt": 1790000000000, "entrypoint": "cli", **extra}
    (reg / f"{pid}-{name}.json").write_text(json.dumps(rec))


@pytest.fixture
def world(tmp_path, monkeypatch):
    reg = tmp_path / "sessions"
    reg.mkdir()
    me = os.getpid()
    _entry(reg, me, UUID_A, "alpha")
    _entry(reg, me, UUID_B, "beta", status="busy")
    _entry(reg, me, UUID_C, "gamma", entrypoint="sdk-cli")
    _entry(reg, _dead_pid(), "dddddddd-0000-4000-8000-000000000004", "crashed", status="busy")
    (reg / "broken.json").write_text("{not json")

    proj = tmp_path / "projects" / "-tmp-proj"
    files = {
        UUID_A: write_jsonl(proj / f"{UUID_A}.jsonl", [
            user("first question", 0),
            asst([text("**First answer.**\n\n```\na table\n```")], 5, "m1"),
            user("second question", 60),
            asst([text("Second answer, whole.")], 65, "m2"),
        ]),
        UUID_B: write_jsonl(proj / f"{UUID_B}.jsonl", [
            user("run the tests", 0),
            asst([call("t1", "Bash", command="uv run pytest -q", description="run tests")], 3, "m1"),
        ]),
    }
    monkeypatch.setattr(drill, "find_session_files", lambda sid: [files[sid]] if sid in files else [])
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", UUID_A)
    return reg


def test_only_live_processes_are_listed(world):
    names = {o.name for o in live.open_sessions(world)}
    assert names == {"alpha", "beta", "gamma"}


def test_each_session_says_what_it_was_asked_and_where_it_is(world, capsys):
    out = printed(capsys, cli.print_live, live.collect(world))
    assert out.startswith("3 open sessions")
    assert "crashed" not in out
    alpha = out[out.index("alpha"):out.index("\n\n", out.index("alpha"))]
    assert "asked: first question" in alpha and "last asked" in alpha and "second question" in alpha
    assert "answered: Second answer, whole." in alpha
    assert "this session" in alpha
    beta = out[out.index("beta · "):]
    # the model's own line for the call, not its command
    assert "now: Bash run tests (called" in beta and "no answer yet" in beta
    gamma = out[out.index("gamma · "):]
    assert "a program started it (sdk-cli)" in gamma and "nothing asked yet" in gamma
    assert out.rstrip().splitlines()[-1].startswith(f"hint: {command('sessions show')} <id>")
    assert "\x1b[" not in out and "│" not in out  # the plain grammar: no ANSI, no boxes


def test_json_is_the_same_facts(world):
    got = json.loads(json.dumps(live.collect(world)))
    by = {s["name"]: s for s in got["sessions"]}
    assert by["alpha"]["state"] == "answered" and by["alpha"]["this_session"]
    assert by["beta"]["state"] == "working" and by["beta"]["now"].startswith("Bash run tests")
    assert by["gamma"]["program"] and by["gamma"]["state"] == "nothing asked"


def test_an_answer_is_its_opening_paragraph_not_its_tables():
    assert live.gist("**First answer.** More.\n\n```\na table\n```") == "First answer. More."
    assert live.gist("```\nonly a block\n```") == ""


def test_busy_with_an_answer_means_background_work(world, capsys):
    rec = json.loads(next(world.glob("*-alpha.json")).read_text())
    rec["status"] = "busy"
    next(world.glob("*-alpha.json")).write_text(json.dumps(rec))
    assert "background agents or workflows are running" in printed(capsys, cli.print_live, live.collect(world))


def test_nothing_open(tmp_path, capsys):
    assert printed(capsys, cli.print_live, live.collect(tmp_path)).startswith("warn: no open sessions")
