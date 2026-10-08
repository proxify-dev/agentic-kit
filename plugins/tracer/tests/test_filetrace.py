"""`ak trace blame` answers "which sessions changed F, and what did each change" in one call.

The eval's file case took 17 calls: the old trace named four interactions and no change,
so the agent drilled turns, ran git log/show/diff, and jq'd a subagent transcript — the
edits it was looking for had been made by a subagent, which the trace never read.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import asst, call, result, user, write_jsonl

from tracer import __main__ as engine_main
from tracer import cli, filetrace, query
from tracer.verbs import command
from tracer.db import get_write_connection
from tracer.filetrace import change_gist, format_file_trace, query_suffix, trace
from tracer.parser import parse_jsonl, segment_interactions
from tracer.storage import IndexWriter

REL = "plugins/x/src/f.py"
CHECKOUT = f"/r/{REL}"
WORKTREE = f"/r/.claude/worktrees/wt/{REL}"
RELEASE = f"{filetrace.RELEASE_CLONE}/{REL}"
OTHER = "/r/plugins/old/src/f.py"  # the same file name, a different file
S1, S2, S3, S4 = (f"{c * 8}-0000-4000-8000-00000000000{i}" for i, c in enumerate("1234"))


def _edit_result(tid, sec, old_start, lines):
    return result(tid, f"The file {CHECKOUT} has been updated successfully.", sec,
                  tur={"filePath": CHECKOUT, "structuredPatch": [
                      {"oldStart": old_start, "oldLines": 3, "newStart": old_start, "newLines": 3, "lines": lines}]})


@pytest.fixture
def world(tmp_path):
    proj = tmp_path / "projects" / "-r"
    # S1 — edits in its own transcript: a patched Edit, a created Write, a read; then an
    # Edit in a worktree copy with no structuredPatch (the diff is computed)
    write_jsonl(proj / f"{S1}.jsonl", [
        user("make the search hybrid", 300),
        asst([call("r1", "Read", file_path=CHECKOUT)], 301, "m1"),
        result("r1", "     1\tcode", 302),
        asst([call("e1", "Edit", file_path=CHECKOUT, old_string="    mode = 'union'", new_string="    mode = 'hybrid'")], 303, "m2"),
        _edit_result("e1", 304, 10, ["     keep", "-    mode = 'union'", "+    mode = 'hybrid'", "     keep"]),
        asst([call("w1", "Write", file_path=CHECKOUT, content="a\nb\nc\n")], 305, "m3"),
        result("w1", f"File created successfully at: {CHECKOUT}", 306, tur={"type": "create", "filePath": CHECKOUT}),
        asst([call("e2", "Edit", file_path="/tmp/unrelated.py", old_string="x", new_string="y")], 307, "m4"),
        result("e2", "ok", 308),
        user("and in the worktree", 400),
        asst([call("e3", "Edit", file_path=WORKTREE, old_string="a = 1", new_string="a = 2\nb = 3", replace_all=True)], 401, "m5"),
        result("e3", "The file has been updated successfully.", 402),
        asst([call("e4", "Edit", file_path=WORKTREE, old_string="zz", new_string="yy")], 403, "m6"),
        result("e4", "<tool_use_error>String to replace not found in file.</tool_use_error>", 404, is_error=True),
    ])
    # S2 — only reads it, in the release clone
    write_jsonl(proj / f"{S2}.jsonl", [
        user("how does search rank?", 200),
        asst([call("r2", "Read", file_path=RELEASE)], 201, "m1"),
        result("r2", "     1\tcode", 202),
    ])
    # S3 — the change was made by its subagent, and by a workflow agent
    write_jsonl(proj / f"{S3}.jsonl", [
        user("port the observer", 100),
        asst([{"type": "tool_use", "id": "a1", "name": "Agent",  # `name` is also an input field here
               "input": {"name": "port", "description": "port it", "prompt": "port it"}}], 101, "m1"),
        result("a1", [{"type": "text", "text": "Spawned successfully."}], 102, tur={"status": "teammate_spawned"}),
    ])
    sub = proj / S3 / "subagents"
    write_jsonl(sub / "agent-aport-abc.jsonl", [
        user("port it", 103),
        asst([call("s1", "Edit", file_path=WORKTREE, old_string="old line", new_string="new line")], 104, "x1"),
        result("s1", "The file has been updated successfully.", 105),
        asst([call("s2", "Write", file_path=WORKTREE, content="1\n2\n")], 106, "x2"),
        result("s2", "The file has been updated successfully.", 107, tur={"type": "update"}),
    ])
    (sub / "agent-aport-abc.meta.json").write_text(json.dumps({"name": "port", "agentType": "port"}))
    write_jsonl(sub / "workflows" / "wf_1" / "agent-a9.jsonl", [
        user("tidy", 110),
        asst([call("w9", "Edit", file_path=CHECKOUT, old_string="p", new_string="q")], 111, "x9"),
        result("w9", "ok", 112),
    ])
    (sub / "workflows" / "wf_1" / "agent-a9.meta.json").write_text(json.dumps({"agentType": "workflow-subagent", "description": "tidy up"}))
    # S4 — the same file name at another path: listed apart, not counted
    write_jsonl(proj / f"{S4}.jsonl", [
        user("old layout", 50),
        asst([call("o1", "Edit", file_path=OTHER, old_string="1", new_string="2")], 51, "m1"),
        result("o1", "ok", 52),
    ])

    con = get_write_connection(tmp_path / "index.db")
    for sid in (S1, S2, S3, S4):
        f = proj / f"{sid}.jsonl"
        IndexWriter(con, "-r").replace_session_records(sid, segment_interactions(parse_jsonl(f), sid, str(f)))
    yield con, proj
    con.close()


def test_one_block_per_session_with_each_change(world):
    con, proj = world
    out = format_file_trace(trace(REL, con, [proj]))
    lines = out.splitlines()
    assert lines[0] == f'"{REL}" — 2 session(s) changed it, 1 only read it · newest first'
    # newest first: S1 (17:06), S3 (17:01); S2 only read
    blocks = [ln.split(" · ")[0] for ln in lines if ln[:8] in (S1[:8], S2[:8], S3[:8])]
    assert blocks[:2] == [S1, S3]

    s1 = out.split(S1, 1)[1].split(S3, 1)[0]
    assert "EDIT×3 WRITE×1 READ×1" in s1.splitlines()[0]
    assert "  turn 000 asked: make the search hybrid  [checkout]" in s1
    assert "    #2 Edit L10 −1 +1 · `mode = 'union'` → `mode = 'hybrid'`" in s1
    assert "    #3 Write created, 3 lines" in s1
    assert f"    open: {command('trace turn')} {S1}-000 --open 2-3" in s1
    assert "unrelated" not in s1  # another file's edit in the same turn
    # no structuredPatch: the diff is computed; a failed edit says so
    assert "    #1 Edit −1 +2 · `a = 1` → `a = 2` (every occurrence)" in s1
    assert "    #2 Edit failed: String to replace not found in file." in s1
    assert f"open: {command('trace turn')} {S1}-001 --open 1-2" in s1


def test_changes_made_by_subagents_count_for_the_parent(world):
    con, proj = world
    out = format_file_trace(trace(REL, con, [proj]))
    s3 = out.split(S3, 1)[1].split("only read it:", 1)[0]
    assert "EDIT×2 WRITE×1" in s3.splitlines()[0]
    assert "  via subagent port — started at turn 000 #1 · " in s3 and "agent-aport-abc.jsonl" in s3
    assert "    :2 Edit −1 +1 · `old line` → `new line`" in s3
    assert "    :4 Write overwrote, 2 lines" in s3
    assert "  via workflow wf_1 (tidy up) — its Agent call not found in the session" in s3


def test_paths_are_matched_by_suffix_and_named(world):
    con, proj = world
    out = format_file_trace(trace(REL, con, [proj]))
    assert "matched paths:" in out
    assert f"  {CHECKOUT}  (checkout · 2 sessions)" in out
    assert f"  {WORKTREE}  (worktree wt · 2 sessions)" in out
    assert "release clone · 1 session)" in out
    assert "same file name, other path — a different file, not counted above:" in out
    assert f"  {OTHER}  (checkout · 1 session(s)" in out
    assert S4 not in out.split("same file name", 1)[1].split("\n\n", 1)[1]
    assert out.split("only read it:", 1)[1].splitlines()[1].startswith(f"  {S2} · 2026-09-22 · turn 000 #1")


def test_absolute_query_matches_every_copy(world, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert query_suffix(str(repo / REL)) == REL
    assert query_suffix("./" + REL) == REL
    assert query_suffix("/nowhere/f.py") == "/nowhere/f.py"


def test_operation_filter_and_nothing_found(world):
    con, proj = world
    reads = format_file_trace(trace(REL, con, [proj], operations=["read"]))
    assert "0 session(s) changed it, 2 only read it" in reads
    none = format_file_trace(trace("plugins/none/g.py", con, [proj]))
    assert none.startswith('No interactions found that touched "plugins/none/g.py"')  # blame's fallback keys on it


def test_a_folder_is_one_summary_per_session(world):
    """Asked for the present plugin's backstory (163e6deb), an agent knew the plugin, not a
    session: `blame plugins/present` answered "no interactions" — no file is named present."""
    con, proj = world
    ft = trace("plugins/x", con, [proj])  # no file is named x, files sit under it
    assert ft.folder and trace("plugins/x/", con, [proj]).folder
    out = format_file_trace(ft)
    lines = out.splitlines()
    assert lines[0] == '"plugins/x/" — 2 session(s) changed files under it, 1 only read them · newest first'
    assert "  /r/plugins/x/  (checkout · 2 sessions)" in out
    assert "  /r/.claude/worktrees/wt/plugins/x/  (worktree wt · 2 sessions)" in out
    s1 = out.split(S1, 1)[1].split(S3, 1)[0]
    # both copies are one file inside the folder, changed in two turns
    assert s1.splitlines()[0].endswith("· main · 1 file(s) changed in 2 turn(s)")
    assert "  most changed: src/f.py (2×)" in s1
    assert "  turn 000 asked: make the search hybrid" in s1 and "  turn 001 asked: and in the worktree" in s1
    assert "unrelated" not in out and OTHER not in out  # plugins/old is not under plugins/x
    s3 = out.split(S3, 1)[1].split("only read files under it:", 1)[0]
    assert s3.splitlines()[0].endswith("· 1 file(s) changed")  # by its agents, in no turn of its own
    assert "  via subagent port: 1 file(s) changed" in s3
    assert "  via workflow wf_1 (tidy up): 1 file(s) changed" in s3
    assert f"only read files under it:\n  {S2} · 2026-09-22 · 1 file(s)" in out
    assert lines[-1] == (f"what the user asked there: {command('sessions replay')} {S1} --role user · "
                         "one file's changes: " + command("trace blame") + " plugins/x/<file> · not seen: changes made through Bash (sed -i, heredocs, git)")


def test_layout_labels_the_release_clone_under_its_old_folder_too():
    """i_files and transcripts keep the path as it was recorded: a session run before the rename
    names ~/.local/share/ak/release, and it is still the release clone."""
    old_clone = f"{Path.home()}/.local/share/brain/release/{REL}"  # brand: historical
    assert filetrace.layout(RELEASE) == "release clone"
    assert filetrace.layout(old_clone) == "release clone"
    assert filetrace.layout(CHECKOUT) == "checkout"


def test_a_file_blame_ends_at_the_users_words(world):
    con, proj = world
    assert f"what the user asked there: {command('sessions replay')} {S1} --role user" in format_file_trace(trace(REL, con, [proj]))


def test_change_gist_cases():
    assert change_gist("Edit", {"old_string": "a\nb", "new_string": "a\nc\nd"}, "ok", None, False, True) \
        == "−1 +2 · `b` → `c`"
    assert change_gist("Edit", {"old_string": "x", "new_string": "x\ny"}, "", None, False, False) \
        == "−0 +1 · + `y` (no result — interrupted?)"
    assert change_gist("NotebookEdit", {"new_source": "print(1)", "edit_mode": "insert"}, "", None, False, True) \
        == "notebook cell insert · + `print(1)`"
    # a re-indent is not the change: the first line whose text changed is
    patch = {"structuredPatch": [{"oldStart": 5, "lines": ["-  conn = db()", "-  old()", "+    conn = db()", "+    new()"]}]}
    assert change_gist("Edit", {}, "", patch, False, True) == "L5 −2 +2 · `old()` → `new()`"


def test_the_blame_verb_reads_every_project_and_narrows_by_operation(world, monkeypatch):
    """`ak trace blame <path> --operation edit,write`, as the shell runs it."""
    con, proj = world
    monkeypatch.setattr(engine_main, "discover_projects", lambda: [("-r", proj)])
    monkeypatch.setattr(engine_main, "get_readonly_connection", lambda: _no_close(con))
    monkeypatch.setattr(engine_main, "staleness_warning", lambda *a: None)
    r = CliRunner().invoke(cli.cli, ["blame", REL, "--operation", "edit,write"])
    assert r.exit_code == 0, r.output
    assert "2 session(s) changed it, 0 only read it" in r.output and S2 not in r.output
    assert "via subagent port" in r.output


class _no_close:
    """The fixture's connection, which the verb must not close under the test."""

    def __init__(self, con: sqlite3.Connection):
        self._con = con

    def __getattr__(self, name):
        return getattr(self._con, name)

    def close(self):
        pass
