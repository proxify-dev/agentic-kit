"""`--role user`: what the user typed, and nothing else — and every flag named after its data.

Asked to write the present plugin's backstory (163e6deb), an agent needed the user's own
words from the session that built it, c9fdf42b. `replay` gave the newest 30 of 44 turns (the
origin was in the first 12), each prompt cut at 200 characters, 17 of them task notices and
teammate reports; the agent went around the tracer to jq on the transcript. `replay --role user`
gives every turn the user typed, whole; `grep --role user` finds where the user said a thing.

The flag is `--role` because grep's hit counts already print roles ("user 4, assistant 31"):
a flag takes the name of the class the record has and the values the output prints, so an
agent reads the label back as the flag. The same rule renamed --errors (--outcome, as on
`ak trace`), --include-automated (--entrypoint, Claude Code's field) and show --step (--open).
"""
from __future__ import annotations

import re
import sys

import pytest
from click.testing import CliRunner
from conftest import PLUGIN, asst, call, result, text, ts, user, write_jsonl

from tracer import cli, drill
from tracer.verbs import command
from tracer.db import get_write_connection
from tracer.parser import parse_jsonl, segment_interactions
from tracer.query import QueryFilters, format_user_prompts, query_dialogue
from tracer.search import classify, format_search, parse_entrypoints, search_sessions
from tracer.storage import IndexWriter
from tracer.turns import said, sender

SID = "c9fdf42b-0000-4000-8000-000000000001"
LONG = "One more case to cover: the draft view. " + "We edit one draft across many turns. " * 8
TEAMMATE = ('Another Claude session sent a message:\n<teammate-message teammate_id="layout-modes">\n'
            '{"type":"idle_notification","result":"## layout: modes, ranked"}\n</teammate-message>')
NOTICE = ('<task-notification>\n<task-id>a1</task-id>\n<summary>Agent "Map the repo" finished</summary>\n'
          '</task-notification>')
COMMAND = ("<command-name>/subtask</command-name>\n<command-message>subtask</command-message>\n"
           "<command-args>wait, <b>before</b> assuming, try it</command-args>")


def _session(tmp_path):
    """The shape of c9fdf42b in eight turns: the user's first ask; a prompt sent, then sent
    again edited past its opening before any answer; a teammate's report and a task notice,
    each opening a turn; a long prompt; a prompt typed while the model worked; a slash
    command whose args hold a `<`."""
    recs = [
        user("Somewhere we had a prototype of the export job. Can you find the script it ran?", 0),
        asst([text("Found it.")], 2, "m0"),
        user("Right, wouldn't the same hold for CSV? or Parquet?", 10),  # no answer: re-sent
        user("Right, wouldn't the same hold for CSV? or Parquet (even better choice?)", 12),
        asst([text("Parquet it is.")], 14, "m1"),
        user(TEAMMATE, 20),
        asst([text("Merged the modes report.")], 21, "m2"),
        user(NOTICE, 30),
        asst([text("The repo map is back.")], 31, "m3"),
        user(LONG, 40),
        asst([call("t1", "Bash", command="ls")], 41, "m4"),
        {"type": "attachment", "timestamp": ts(42),
         "attachment": {"type": "queued_command", "prompt": "and keep every version"}},
        {"type": "attachment", "timestamp": ts(43),
         "attachment": {"type": "queued_command", "prompt": NOTICE}},
        result("t1", "README.md", 44),
        asst([text("Draft view added.")], 45, "m5"),
        user(COMMAND, 50),
        asst([text("Tried it.")], 51, "m6"),
    ]
    path = write_jsonl(tmp_path / "projects" / "-p" / f"{SID}.jsonl", recs)
    con = get_write_connection(tmp_path / "prompts.db")
    IndexWriter(con, "-p").replace_session_records(SID, segment_interactions(parse_jsonl(path), SID, str(path)))
    return con


def test_role_user_prints_every_prompt_the_user_typed_whole_and_nothing_else(tmp_path):
    con = _session(tmp_path)
    f = QueryFilters(session_id=SID)
    out = format_user_prompts(query_dialogue(f, con=con), f, con=con)
    lines = out.splitlines()
    assert lines[0] == f"{SID} · 4 prompts the user typed, in 7 turns (2026-09-22 to 2026-09-22)"
    assert lines[1] == ("left out: 1 teammate message(s) · 1 task notice(s) · "
                        "1 re-send(s), folded into the copy that was answered")
    assert "[000] 2026-09-22 17:00:00\n  Somewhere we had a prototype of the export job." in out
    # the re-send folds into the copy that was answered, and says it went twice
    assert "[002] 2026-09-22 17:00:12 · sent 2×\n  Right, wouldn't the same hold for CSV? or Parquet (even better choice?)" in out
    assert "[001]" not in out
    # whole: the index keeps 200 characters of a prompt
    assert f"  {LONG.strip()}" in out and len(LONG) > 200
    assert "  typed while it worked (17:00:42):\n    and keep every version" in out
    # a slash command as typed, its args whole even with a `<` in them
    assert "  /subtask wait, <b>before</b> assuming, try it" in out
    assert "modes, ranked" not in out and "Map the repo" not in out
    assert lines[-1] == (f"next: {command('trace turn')} {SID}-000 (what one turn did) · "
                         f"{command('sessions replay')} {SID} (the answers too)")


def test_a_limit_still_keeps_the_newest_turns(tmp_path):
    con = _session(tmp_path)
    f = QueryFilters(session_id=SID, limit=2)
    out = format_user_prompts(query_dialogue(f, con=con), f, con=con)
    assert out.startswith(f"{SID} · 2 prompts the user typed, in 2 turns (2026-09-22 to 2026-09-22) — "
                          "the last 2 of 7; --limit 7 for all")


def test_who_sent_a_prompt():
    assert sender(TEAMMATE) == "teammate"
    assert sender("  " + NOTICE) == "task"
    assert sender("Another thing would be a draft view") == "user"
    assert said(user(COMMAND, 0)) == "/subtask wait, <b>before</b> assuming, try it"
    assert said(user("<command-name>/compact</command-name>\n<command-message>compact</command-message>\n"
                     "<command-args></command-args>", 0)) == "/compact"


def test_a_teammate_has_its_own_role():
    """A teammate's message opens a turn like a prompt; it used to count as the user."""
    assert classify(user(TEAMMATE, 0), 1, "modes").role == "teammate"
    assert classify(user(NOTICE, 0), 1, "repo").role == "notification"
    assert classify(user("the modes pane, please", 0), 1, "modes").role == "user"
    queued = {"type": "attachment", "timestamp": ts(1), "attachment": {"type": "queued_command", "prompt": TEAMMATE}}
    assert classify(queued, 1, "modes").role == "teammate"


# ── search: role, outcome, entrypoint ──────────────────────────────────────────────────────


def _record(tmp_path):
    """Two sessions that say "modes": one a person started (cli) — as the user, in a
    teammate's report, and in a failed and a clean tool result — and one `claude -p` run."""
    proj = tmp_path / "projects" / "-q"
    a, b = "aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002"
    write_jsonl(proj / f"{a}.jsonl", [
        user("the modes pane, please", 0, entrypoint="cli"),
        asst([call("t1", "Bash", command="cat modes.txt")], 1, "m1"),
        result("t1", "modes.txt: No such file", 2, is_error=True),
        asst([call("t2", "Bash", command="ls modes")], 3, "m2"),
        result("t2", "modes/a.md", 4),
        user(TEAMMATE, 10, entrypoint="cli"),
        asst([text("ok")], 11, "m3"),
    ])
    write_jsonl(proj / f"{b}.jsonl", [user("rank the modes", 0, entrypoint="sdk-cli"), asst([text("ok")], 1, "m1")])
    return proj, a, b


def _roles(report, sid):
    g = next(g for g in report.ranked if g.session_id == sid)
    return sorted(h.role for h in g.hits)


def test_role_outcome_and_entrypoint_filter_what_their_names_say(tmp_path):
    proj, a, b = _record(tmp_path)
    everything = search_sessions("modes", [proj], exclude_session="")
    assert _roles(everything, a) == ["Bash", "Bash", "teammate", "tool-output", "tool-output", "user"]
    assert [g.session_id for g in everything.ranked] == [a]  # b is a program's: hidden
    assert everything.automated == {"sdk-cli": 1}

    assert _roles(search_sessions("modes", [proj], exclude_session="", role="user"), a) == ["user"]
    assert _roles(search_sessions("modes", [proj], exclude_session="", role="teammate"), a) == ["teammate"]
    failed = search_sessions("modes", [proj], exclude_session="", outcome="error")
    assert _roles(failed, a) == ["tool-output"] and failed.filters == "outcome=error"
    assert _roles(search_sessions("modes", [proj], exclude_session="", errors=True), a) == ["tool-output"]
    assert len(next(g for g in search_sessions("modes", [proj], exclude_session="", outcome="ok").ranked).hits) == 1

    only = search_sessions("modes", [proj], exclude_session="", include_automated=True, entrypoints=frozenset({"sdk-cli"}))
    assert [g.session_id for g in only.ranked] == [b] and only.automated == {"cli": 1}
    assert only.filters == "entrypoint=sdk-cli"
    assert [g.session_id for g in search_sessions("modes", [proj], exclude_session="", include_automated=True).ranked] == [a, b]


def test_the_output_names_the_flag_it_takes(tmp_path):
    proj, a, _ = _record(tmp_path)
    out = format_search(search_sessions("modes", [proj], exclude_session="", role="user"))
    assert out.splitlines()[0].startswith('"modes" (role=user): 1 session(s)')
    assert "hidden: 1 session(s) a program started that matched (sdk-cli 1) — --entrypoint all lists them" in out
    assert f"a turn's steps: {command('trace turn')} {a}-000" in out
    assert f"what the user asked there: {command('sessions replay')} {a} --role user" in out
    only = search_sessions("modes", [proj], exclude_session="", include_automated=True, entrypoints=frozenset({"sdk-cli"}))
    assert "hidden: 1 session(s) with another entrypoint that matched (cli 1)" in format_search(only)


def test_parse_entrypoints():
    assert parse_entrypoints(None) == (False, None)
    assert parse_entrypoints("all") == (True, None)
    assert parse_entrypoints("sdk-cli, sdk-py") == (True, frozenset({"sdk-cli", "sdk-py"}))


# ── the CLI: new names, old spellings kept and hidden ──────────────────────────────────────


@pytest.fixture
def engine(monkeypatch):
    """The argv each verb hands the engine, instead of running it."""
    from tracer import storage

    ran = []
    monkeypatch.setattr(cli, "_run_engine", ran.append)
    monkeypatch.setattr(storage, "match_session_ids", lambda p: [SID] if SID.startswith(p) else [])
    monkeypatch.setattr(drill, "match_session_ids", lambda p: [SID] if SID.startswith(p) else [])  # the one resolver
    return ran


def _run(*args):
    return CliRunner().invoke(cli.cli, list(args))


def test_replay_takes_a_short_id_and_a_role(engine):
    assert _run("replay", "c9fdf42b", "--role", "user").exit_code == 0
    assert engine[-1] == ["dialogue", "--session", SID, "--all-projects", "--role", "user"]
    # no session starts with it: a phrase, and the role still applies
    _run("replay", "deadbeef", "--role", "user")
    assert engine[-1] == ["search", "deadbeef", "--all-projects", "--role", "user"]
    assert _run("replay", "c9fdf42b", "--role", "assistant").exit_code == 2  # only user, for now


def test_grep_role_outcome_entrypoint(engine):
    assert _run("grep", "scratchpad", "--role", "user", "--outcome", "error", "--entrypoint", "sdk-cli").exit_code == 0
    assert engine[-1] == ["search", "scratchpad", "--all-projects", "--limit", "10", "--role", "user",
                          "--outcome", "error", "--entrypoint", "sdk-cli"]


@pytest.mark.parametrize("old,new", [
    (["grep", "x", "--errors"], ["grep", "x", "--outcome", "error"]),
    (["grep", "x", "--include-automated"], ["grep", "x", "--entrypoint", "all"]),
    (["show", SID, "--drill", "000", "--step", "1"], ["show", SID, "--drill", "000", "--open", "1"]),
    (["blame", "f.py", "--op", "edit"], ["blame", "f.py", "--operation", "edit"]),
])
def test_a_released_spelling_still_works(engine, old, new):
    assert _run(*old).exit_code == 0 and _run(*new).exit_code == 0
    assert engine[-2] == engine[-1]


def test_two_spellings_that_disagree_are_refused(engine):
    assert "--errors is --outcome error" in _run("grep", "x", "--errors", "--outcome", "ok").output
    assert "--include-automated is --entrypoint all" in _run("grep", "x", "--include-automated", "--entrypoint", "cli").output


def test_help_shows_only_the_new_names():
    grep_help = _run("grep", "--help").output
    for name in ("--role", "--outcome", "--entrypoint", "tool-output", "teammate"):
        assert name in grep_help
    for old in ("--errors", "--include-automated"):
        assert old not in grep_help
    assert "--step" not in _run("show", "--help").output and "--open" in _run("show", "--help").output
    assert "--asks" not in _run("replay", "--help").output
    assert "--include-automated" not in _run("sessions", "ls", "--help").output


def test_the_session_start_line_teaches_role_user_within_budget():
    sys.path.insert(0, str(PLUGIN / "hooks"))
    import session

    assert f"`{command('sessions replay')} <session> --role user`" in session.TEACH
    assert "--role user: where the user typed it" in session.TEACH
    assert f"`{command('trace blame')} <file|folder>`" in session.TEACH
    assert f"`{command('sessions turns')} --tool X` (across sessions)" in session.TEACH
    old = re.findall(r"`tracer (replay|inspect|grep|log|show|blame|index|live)\b", session.TEACH)
    assert not old, "the old spellings still run, but are taught no more"
    assert "`ak " not in session.TEACH, "the line names the plugin's own command (plugins/AGENTS.md §6)"
    assert len(session.TEACH) <= 800  # memory's test_session_start_contract: TEACH_BUDGET
