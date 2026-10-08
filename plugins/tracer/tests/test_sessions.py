"""`ak sessions` (old spelling `ak tracer sessions`) — every session one project holds, oldest first (sessions.py).

Written against the 2026-09-24 miss (evals/tool-routing case `timeline`): asked for "a timeline of
what we've been doing in this folder", an agent scripted over the raw transcripts because no tool
answered a question with no topic, session or file in it. The project here has the same shape as
~/agentic-kit: it moved from a dotted name, has worktrees inside it, and a sibling whose dir name starts
the same way but is not inside it.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tracer import cli, db
from tracer.verbs import command
from tracer.db import get_write_connection
from tracer.sessions import (as_data, clean, collect, is_noise, real_path, resolve_root,
                                   scope)
from tracer.storage import _index_project, get_project_hash

from conftest import printed, write_jsonl

ASKING = "ffffffff-0000-4000-8000-00000000000f"

# The real pair these tests model — the folder ~/x/ak, moved there from its dot-twin ~/x/.ak — and
# a prompt typed in it. Data about real paths, not the brand: a rebrand does not rename them.
FOLDER = "brain"  # brand: historical
TWIN = "." + FOLDER
SIBLING = f"{FOLDER}-wt"
TYPED = f"separate ~/{TWIN} from the app"


def session(cwd: str, sid: str, day: str, prompts: list[str], entrypoint: str = "cli") -> list[dict]:
    recs = []
    for i, p in enumerate(prompts):
        t = f"{day}T10:{i:02d}:00.000Z"
        recs.append({"type": "user", "timestamp": t, "cwd": cwd, "sessionId": sid, "entrypoint": entrypoint,
                     "uuid": f"u{i}", "message": {"role": "user", "content": p}})
        recs.append({"type": "assistant", "timestamp": t.replace(":00.000Z", ":30.000Z"), "cwd": cwd,
                     "sessionId": sid, "uuid": f"a{i}",
                     "message": {"id": f"m{i}", "role": "assistant", "model": "claude-test",
                                 "content": [{"type": "text", "text": "ok"}]}})
    return recs


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A HOME with ~/x/ak (moved there from ~/x/.ak), a worktree inside it, and ~/x/agentic-kit-wt."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ASKING)
    root = tmp_path / "x" / FOLDER
    twin = tmp_path / "x" / TWIN
    wt = root / ".claude" / "worktrees" / "evals"
    sibling = tmp_path / "x" / SIBLING / "other"
    projects = tmp_path / ".claude" / "projects"
    plan = [
        (twin, "11111111-0000-4000-8000-000000000001", "2026-07-16",
         ["Ranked Q3 plan below", "Unknown view type: kanban"], "cli"),
        (twin, "11111111-0000-4000-8000-000000000002", "2026-08-02", ["/plugin"], "cli"),
        (twin, "11111111-0000-4000-8000-000000000003", "2026-09-02", ["we've been unorganized"], "cli"),
        (twin, "11111111-0000-4000-8000-000000000004", "2026-09-02", ["we've been unorganized"], "cli"),
        (root, "22222222-0000-4000-8000-000000000001", "2026-09-15", [TYPED], "cli"),
        (root, "22222222-0000-4000-8000-000000000002", "2026-09-16", ["probe"], "sdk-cli"),
        (wt, "33333333-0000-4000-8000-000000000001", "2026-09-23", ["build the eval"], "cli"),
        (sibling, "44444444-0000-4000-8000-000000000001", "2026-09-21", ["not this project"], "cli"),
        (root, ASKING, "2026-09-24", ["give me a timeline"], "cli"),
    ]
    for cwd, sid, day, prompts, ep in plan:
        write_jsonl(projects / get_project_hash(str(cwd)) / f"{sid}.jsonl", session(str(cwd), sid, day, prompts, ep))
    con = get_write_connection(tmp_path / "index.db")
    for cwd in {p[0] for p in plan}:
        h = get_project_hash(str(cwd))
        _index_project(con, h, projects / h)
    # one person's session written after the last index run
    late = "55555555-0000-4000-8000-000000000001"
    write_jsonl(projects / get_project_hash(str(root)) / f"{late}.jsonl", session(str(root), late, "2026-09-24", ["late"]))
    yield tmp_path, root, con
    con.close()


def test_scope_is_the_folder_its_dot_twin_and_what_is_inside_not_a_name_prefix(home):
    _, root, _ = home
    roles = {f.name: f.role for f in scope(str(root))}
    assert roles == {f"~/x/{FOLDER}": "here", f"~/x/{TWIN}": "twin", "worktree evals": "inside"}


def test_one_line_per_session_oldest_first_with_what_it_left_out(home, capsys):
    _, root, con = home
    report = collect(str(root), con)
    assert [r.first for r in report.rows] == [
        "Ranked Q3 plan below", "we've been unorganized", TYPED, "build the eval"]
    assert report.rows[0].last == "Unknown view type: kanban"
    assert report.rows[1].same_as, "two sessions that began with the same words show once"
    assert (report.automated, report.commands_only, report.excluded) == (1, 1, 1)
    assert report.unindexed == {f"~/x/{FOLDER}": 1}
    out = printed(capsys, cli.print_sessions, as_data(report))
    assert f"scope: ~/x/{TWIN} (3) — the same name with a dot" in out   # the /plugin-only session is not one
    assert "1 folder inside (1: worktree evals)" in out
    assert "×2 same opening" in out
    assert "left out: 1 started by programs (--entrypoint all), 1 that only ran a /command, 1 excluded" in out
    assert f"gaps: 1 sessions here the index has not read yet (~/x/{FOLDER} 1)" in out
    assert "reach: " in out and "observer search" in out, "it says what the record does not reach"
    assert "not this project" not in out
    assert out.splitlines()[-1].startswith(f"hint: {command('sessions replay')} <id>")
    assert "\n2026-07-16\n  10:00  11111111" in out, "a tree per day, two spaces a level"


def test_since_limit_and_include_automated(home, capsys):
    _, root, con = home
    assert [r.first for r in collect(str(root), con, since="2026-09-10").rows] == [
        TYPED, "build the eval"]
    out = printed(capsys, cli.print_sessions, as_data(collect(str(root), con), limit=1))
    assert "shown: the newest 1 of 4 lines" in out and "build the eval" in out and "Ranked Q3" not in out
    assert "probe" in printed(capsys, cli.print_sessions, as_data(collect(str(root), con, include_automated=True)))


def test_a_dir_name_is_read_back_against_the_disk(tmp_path):
    wt = tmp_path / "x" / FOLDER / ".claude" / "worktrees" / "evals-with-story"
    wt.mkdir(parents=True)
    (tmp_path / "x" / SIBLING).mkdir()
    assert real_path(get_project_hash(str(wt))) == str(wt), "a dot, a slash and a dash, each told apart"
    assert real_path(get_project_hash(str(tmp_path / "x" / SIBLING))) == str(tmp_path / "x" / SIBLING)
    assert real_path(get_project_hash(str(tmp_path / "gone" / "a-b"))) == "", "nothing on disk fits"


def test_prompts_read_as_typed():
    assert clean("/compact\n  compact\n  keep the UX notes") == "/compact keep the UX notes"
    assert clean("\x1b[2mCompacted \x1b[22m") == "Compacted"
    for noise in ("/plugin", "/exit", "/resume]", "Compacted", "This session is being continued from a previous conversation …",
                  "b1x9m2k7q toolu_01ABC /private/tmp/claude-501/x.output completed"):
        assert is_noise(clean(noise)), noise
    assert not is_noise("/tracer:trace 57e7a5bf why did it stop?")


def test_root_resolves_a_path_a_dir_name_and_this_project(home, monkeypatch):
    tmp, root, _ = home
    assert resolve_root(str(root)) == str(root.resolve())
    assert resolve_root(get_project_hash(str(root))) == str(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp / "not-a-repo"))
    assert resolve_root(None) == str(tmp / "not-a-repo")


def test_the_verb_wires_its_options_and_speaks_json(home, monkeypatch):
    tmp, root, _ = home
    monkeypatch.setattr(db, "get_readonly_connection", lambda: get_write_connection(tmp / "index.db"))
    r = CliRunner().invoke(cli.cli, ["sessions", "ls", str(root), "--since", "2026-09-01", "--limit", "2"])
    assert r.exit_code == 0, r.output
    assert r.output.startswith(f"sessions in ~/x/{FOLDER}")
    assert "shown: the newest 2 of 3 lines" in r.output
    got = json.loads(CliRunner().invoke(cli.cli, ["sessions", "ls", str(root), "--json"]).output)
    assert got["root"] == f"~/x/{FOLDER}" and len(got["sessions"]) == 4
    assert set(got["sessions"][0]) >= {"id", "start", "turns", "first", "last", "folder", "same_opening"}
    first_line = (cli.sessions_group.commands["ls"].help or "").splitlines()[0]
    assert len(first_line) <= 60, "AGENTS.md §5: the first help line is short"


def test_no_index_is_a_miss_not_a_crash(tmp_path, monkeypatch):
    real = db.get_readonly_connection
    monkeypatch.setattr(db, "get_readonly_connection", lambda: real(tmp_path / "none.db"))
    r = CliRunner().invoke(cli.cli, ["sessions", "ls", str(tmp_path), "--json"])
    assert r.exit_code == 1 and json.loads(r.output)["sessions"] == []
