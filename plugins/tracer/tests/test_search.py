"""`ak sessions search` ranks sessions by what they said, not by what hooks injected.

Asked "Which of our sessions discussed the sandbox profile?", the old search put first the asking
session itself (its own ai-title and last-prompt) and two sessions whose only hits were
SessionStart hook text; sorted by recency; showed 3 sessions whatever `limit` said, under
a footer telling the MCP caller to "raise --limit"; and dropped plugin/tool/errors/since
without a word.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from conftest import asst, call, meta, result, text, think, user, write_jsonl

from tracer import __main__ as engine_main
from tracer import cli, query, search
from tracer.verbs import command
from tracer.search import format_search, search_sessions

A, B, C, D, E, F = (f"{c * 8}-0000-4000-8000-00000000000{i}" for i, c in enumerate("abcdef"))
ASKING = C


@pytest.fixture
def project(tmp_path):
    d = tmp_path / "projects" / "-tmp-proj"
    # A: discussed it — prompt, answer, thinking; oldest
    write_jsonl(d / f"{A}.jsonl", [
        user("how does the sandbox profile work?", 0),
        asst([think("the sandbox denies network")], 1, "m1"),
        asst([text("The sandbox profile denies network.")], 2, "m1"),
        user("thanks", 100),
        asst([text("ok")], 101, "m2"),
    ])
    # B: only hook output, a title and a last-prompt mention it
    write_jsonl(d / f"{B}.jsonl", [
        {"type": "attachment", "timestamp": "2026-09-22T17:30:00Z",
         "attachment": {"type": "hook_success", "stdout": "SessionStart: sandbox tips"}},
        meta("ai-title", aiTitle="Sandbox musings"),
        user("unrelated question", 1800),
        asst([text("unrelated answer")], 1801, "m"),
    ])
    # C: the asking session — its prompt and its own title
    write_jsonl(d / f"{C}.jsonl", [
        user("Which of our sessions discussed the sandbox profile?", 3000),
        meta("ai-title", aiTitle="Sandbox profile discussions"),
    ])
    # D: a failing Bash call about it; newest
    write_jsonl(d / f"{D}.jsonl", [
        user("run it", 2000),
        asst([call("t1", "Bash", command="sandbox-exec -f p.sb ls")], 2001, "m"),
        result("t1", "sandbox-exec: denied", 2002, is_error=True),
        asst([call("t2", "Read", file_path="/tmp/notes.md")], 2003, "m2"),
        result("t2", "the sandbox notes", 2004),
    ])
    # E: the term only in an envelope field (its cwd) — no hit at all
    write_jsonl(d / f"{E}.jsonl", [
        {**user("hello", 2500), "cwd": "/tmp/sandbox-proj"},
    ])
    # F: one mention, in the second turn
    write_jsonl(d / f"{F}.jsonl", [
        user("first", 10),
        asst([text("nothing here")], 11, "m"),
        user("what about sandboxing?", 20),
        asst([text("later")], 21, "m2"),
    ])
    # a subagent's and a workflow agent's transcripts: sidechains of A, not sessions
    write_jsonl(d / A / "subagents" / "agent-a1.jsonl", [user("sandbox sandbox sandbox", 5)])
    write_jsonl(d / A / "subagents" / "workflows" / "wf_1" / "agent-a2.jsonl", [user("sandbox", 6)])
    return d


@pytest.fixture(params=["rg", "python"])
def engine(request, monkeypatch):
    if request.param == "python":
        monkeypatch.setattr(search.shutil, "which", lambda _name: None)
    elif search.shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")
    return request.param


def test_ranked_by_conversation_hits_with_metadata_and_the_asker_left_out(project, engine, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ASKING)
    r = search_sessions("sandbox", [project], scope="-tmp-proj")
    assert [g.session_id for g in r.ranked] == [D, A, F]
    assert [len(g.hits) for g in r.ranked] == [3, 3, 1]
    assert {h.label for h in r.ranked[0].hits} == {"Bash", "tool-output"}
    assert r.meta_hits == 2 and r.meta_only_sessions == 1  # B's hook output and title
    assert r.excluded == [ASKING]
    assert r.turns[F] == {3: (1, "what about sandboxing?")}


def test_more_hits_outrank_recency(project, monkeypatch):
    """F is newer than A but mentions it once; A discussed it. A and D tie on hits, and
    D's latest hit is the newer one."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ASKING)
    r = search_sessions("sandbox", [project])
    order = [g.session_id for g in r.ranked]
    assert order.index(A) < order.index(F) and order.index(D) < order.index(A)


def test_include_meta_and_exclude_session(project, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ASKING)
    with_meta = search_sessions("sandbox", [project], include_meta=True)
    assert B in [g.session_id for g in with_meta.ranked]
    keep_all = search_sessions("sandbox", [project], exclude_session="")
    assert ASKING in [g.session_id for g in keep_all.ranked] and not keep_all.excluded
    other = search_sessions("sandbox", [project], exclude_session=A)
    assert A not in [g.session_id for g in other.ranked] and ASKING in [g.session_id for g in other.ranked]


def test_filters_are_honoured(project, engine):
    ids = lambda r: [g.session_id for g in r.ranked]  # noqa: E731
    assert ids(search_sessions("sandbox", [project], tool="Bash", exclude_session="")) == [D]
    bash = search_sessions("sandbox", [project], tool="Bash", exclude_session="").ranked[0]
    assert [h.label for h in bash.hits] == ["Bash", "tool-output"]  # its call and its result, not Read's
    errs = search_sessions("sandbox", [project], errors=True, exclude_session="")
    assert ids(errs) == [D] and [h.label for h in errs.ranked[0].hits] == ["tool-output"]
    recent = search_sessions("sandbox", [project], since="2026-09-22T17:30", exclude_session="")
    assert ids(recent) == [D, C]


def test_limit_is_sessions_shown_and_hints_name_the_verbs(project, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ASKING)
    r = search_sessions("sandbox", [project], scope="-tmp-proj")
    out = format_search(r, limit=2)
    assert out.splitlines()[0].startswith('"sandbox" in -tmp-proj: 3 session(s), 7 hit(s) in conversation text')
    assert "--include-meta counts them" in out and f"left out: this session {ASKING[:8]}" in out
    assert "--exclude-session '' keeps it" in out
    assert f"1. {D}" in out and f"2. {A}" in out and f"3. {F}" not in out
    assert out.splitlines()[-1] == (f"a turn's steps: {command('trace turn')} {D}-000 · what the user asked there: "
                                    f"{command('sessions replay')} {D} --role user · 1 more session(s): --limit 3")
    # one session: the turn with most hits, its prompt, a snippet from what people said first
    block = out.split(f"2. {A}")[1].splitlines()
    assert block[1].strip() == "turn 000 · 3 hit(s) · asked: how does the sandbox profile work?"
    assert block[2].strip().startswith("user: how does the sandbox profile work?")


def test_nothing_found_still_says_what_was_left_out(project, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ASKING)
    out = format_search(search_sessions("sandbox musings", [project], include_meta=False))
    assert out.startswith('No conversations matched "sandbox musings"')
    assert "1 hit(s) in hook/metadata records (1 session(s) had only those)" in out


def _search(monkeypatch, *argv) -> str:
    """`ak sessions search` (the `grep` verb) as the shell runs it: every project's transcripts."""
    r = CliRunner().invoke(cli.cli, ["grep", *argv])
    return r.output


def test_the_search_verb_wires_tool_outcome_and_since(project, monkeypatch):
    monkeypatch.setattr(engine_main, "discover_projects", lambda: [("-tmp-proj", project)])
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ASKING)
    out = _search(monkeypatch, "sandbox", "--tool", "Bash", "--outcome", "error", "--limit", "5")
    assert out.splitlines()[0].startswith('"sandbox" in all 1 projects (tool=Bash, outcome=error): 1 session(s), 1 hit(s)')
    out = _search(monkeypatch, "sandbox", "--since", "2026-09-22T17:30")
    assert D in out and A not in out


def test_the_cli_search_wires_its_flags(project, monkeypatch, capsys):
    monkeypatch.setattr(engine_main, "get_conversations_dir_for_hash", lambda h: project)
    monkeypatch.setattr("sys.argv", ["tracer-engine", "search", "sandbox", "--tool", "Bash",
                                     "--exclude-session", "", "--limit", "1"])
    engine_main.main()
    out = capsys.readouterr().out
    assert f"1. {D}" in out and "(tool=Bash)" in out


def test_hits_only_in_record_envelopes_are_not_hits(project):
    r = search_sessions("sandbox-proj", [project], include_meta=True, exclude_session="")
    assert not r.ranked


def test_classify_roles():
    q = "zeta"
    assert search.classify(user("zeta?", 0), 1, q).role == "user"
    assert search.classify(user("<task-notification>zeta done</task-notification>", 0), 1, q).role == "notification"
    assert search.classify(user("Base directory for this skill: zeta", 0, isMeta=True), 1, q).meta
    queued = {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "and zeta?"}}
    assert search.classify(queued, 1, q).role == "user"
    assert search.classify(meta("last-prompt", text="zeta"), 1, q).meta
    assert search.classify({"type": "user", "cwd": "/zeta", "message": {"content": "hi"}}, 1, q) is None
    assert json.dumps  # keep the import honest for editors


AUTO, DESKTOP, OLD = (f"{c * 8}-0000-4000-8000-00000000009{i}" for i, c in enumerate("789"))


@pytest.fixture
def mixed(project):
    """An observer summarizer run (`claude -p`: sdk-cli), a desktop-app session, and a
    transcript from before the entrypoint field existed."""
    write_jsonl(project / f"{AUTO}.jsonl", [
        {**user("summarize: the sandbox profile, the sandbox denies", 2900), "entrypoint": "sdk-cli"},
        {**asst([text("sandbox sandbox sandbox")], 2901, "m"), "entrypoint": "sdk-cli"},
    ])
    write_jsonl(project / f"{DESKTOP}.jsonl", [
        meta("ai-title", aiTitle="x"),  # no entrypoint on this record: read from the file head
        {**user("sandbox in the app?", 2950), "entrypoint": "claude-desktop"},
    ])
    write_jsonl(project / f"{OLD}.jsonl", [user("an old sandbox chat", 40)])
    return project


def test_automated_sessions_are_hidden_by_entrypoint(mixed, engine):
    r = search_sessions("sandbox", [mixed], exclude_session="")
    listed = [g.session_id for g in r.ranked]
    assert AUTO not in listed and DESKTOP in listed and OLD in listed
    assert r.automated == {"sdk-cli": 1}
    out = format_search(r)
    assert "hidden: 1 session(s) a program started that matched (sdk-cli 1) — --entrypoint all lists them" in out
    shown = search_sessions("sandbox", [mixed], exclude_session="", include_automated=True)
    assert AUTO in [g.session_id for g in shown.ranked] and not shown.automated
    assert shown.filters == "entrypoint=all"


def test_the_gate_and_its_default():
    """One predicate lists or leaves out a session, and one constant sets the default —
    the engine's --include-automated takes it from there."""
    assert search.leave_out("s", "cli", set(), False) == ""
    assert search.leave_out("s", "sdk-ts", set(), False) == ""
    assert search.leave_out("s", "", set(), False) == ""  # predates the field: a person's
    assert search.leave_out("s", "sdk-py", set(), False) == "automated"
    assert search.leave_out("s", "sdk-py", set(), True) == ""
    assert search.leave_out("s", "cli", {"s"}, True) == "excluded"
    assert search.INCLUDE_AUTOMATED is False


def test_subagent_and_workflow_transcripts_are_not_sessions(project, engine):
    r = search_sessions("sandbox", [project], exclude_session="")
    ids = {g.session_id for g in r.ranked}
    assert not any(i.startswith("agent-") for i in ids)
    a = next(g for g in r.ranked if g.session_id == A)
    assert len(a.hits) == 3  # its own three, none of its subagents'


def test_search_covers_every_project_and_the_engine_one_unless_told(project, tmp_path, monkeypatch, capsys):
    other = tmp_path / "projects" / "-other"
    write_jsonl(other / f"{OLD}.jsonl", [user("sandbox elsewhere", 30)])
    monkeypatch.setattr(engine_main, "discover_projects", lambda: [("-tmp-proj", project), ("-other", other)])
    monkeypatch.setattr(engine_main, "get_conversations_dir_for_hash", lambda h: project)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ASKING)
    everywhere = _search(monkeypatch, "sandbox")
    assert everywhere.startswith('"sandbox" in all 2 projects:') and OLD in everywhere
    monkeypatch.setattr("sys.argv", ["tracer-engine", "search", "sandbox"])  # no --all-projects: this project
    engine_main.main()
    here = capsys.readouterr().out
    assert D in here and OLD not in here


def test_the_cli_flag_lists_automated_sessions(mixed, monkeypatch, capsys):
    from tracer import __main__ as engine_main

    monkeypatch.setattr(engine_main, "get_conversations_dir_for_hash", lambda h: mixed)
    monkeypatch.setattr("sys.argv", ["tracer-engine", "search", "sandbox", "--include-automated",
                                     "--exclude-session", ""])
    engine_main.main()
    cap = capsys.readouterr()
    assert f". {AUTO}" in cap.out + cap.err


def test_until_bounds_hits_like_since(project, engine, monkeypatch):
    """A probe asked the search for sessions from before a moment (until="2026-09-22T17:04:00")
    and got a validation error: there was `since` and no `until`."""
    ids = lambda r: [g.session_id for g in r.ranked]  # noqa: E731
    # hits run 17:00:00 (A) … 17:50:00 (C); D's are 17:33:21–17:33:24
    early = search_sessions("sandbox", [project], until="2026-09-22T17:30:00", exclude_session="")
    assert ids(early) == [A, F]
    window = search_sessions("sandbox", [project], since="2026-09-22T17:30", until="2026-09-22T17:40",
                             exclude_session="")
    assert ids(window) == [D] and "since 2026-09-22T17:30, until 2026-09-22T17:40" in window.filters
    # a bare date keeps that whole day; the day before keeps nothing
    assert len(search_sessions("sandbox", [project], until="2026-09-22", exclude_session="").ranked) == 4
    assert not search_sessions("sandbox", [project], until="2026-09-21", exclude_session="").ranked
    monkeypatch.setattr(engine_main, "discover_projects", lambda: [("-tmp-proj", project)])
    out = _search(monkeypatch, "sandbox", "--until", "2026-09-22T17:30:00", "--exclude-session", "")
    assert out.startswith('"sandbox" in all 1 projects (until 2026-09-22T17:30:00): 2 session(s)')


def test_each_entry_says_what_the_session_was(tmp_path, monkeypatch):
    """The "about" eval opened three or four sessions per run only to learn what each was
    about. The entry now carries the session's own title, its size, and what it began
    with when the hit turn is a later one."""
    d = tmp_path / "projects" / "-p"
    write_jsonl(d / f"{A}.jsonl", [
        user("set up the eval harness", 0),
        asst([text("done")], 600, "m1"),                      # turn 000: 10 minutes
        meta("ai-title", aiTitle="Eval harness"),
        user("now make it run in a sandbox", 1000),
        asst([text("the sandbox runs it")], 1060, "m2"),      # turn 001: 1 minute
        meta("ai-title", aiTitle="Memory eval sandbox runner"),  # the latest title wins
    ])
    write_jsonl(d / f"{F}.jsonl", [
        user("what about sandboxing?", 0),
        asst([text("sandboxing is …")], 30, "m1"),
        meta("custom-title", customTitle="Named by hand"),
        meta("ai-title", aiTitle="Overridden"),
    ])
    r = search_sessions("sandbox", [d], exclude_session="")
    assert (r.info[A].title, r.info[A].turns, int(r.info[A].active)) == ("Memory eval sandbox runner", 2, 660)
    assert r.info[F].title == "Named by hand"
    out = format_search(r)
    block_a = out.split(f". {A}", 1)[1].splitlines()
    assert block_a[0].startswith(' · "Memory eval sandbox runner" · 2026-09-22 · 2 turns · 11m00s · 2 hit(s)')
    assert block_a[1] == "   began: set up the eval harness"  # the hit turn is a later one
    assert block_a[2].startswith("   turn 001 · 2 hit(s) · asked: now make it run in a sandbox")
    block_f = out.split(f". {F}", 1)[1].splitlines()
    assert block_f[0].startswith(' · "Named by hand" · 2026-09-22 · 1 turn · 30s')
    assert block_f[1].startswith("   turn 000 ")  # began == asked: printed once
