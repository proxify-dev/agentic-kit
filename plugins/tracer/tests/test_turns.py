"""What opens a turn, and what rides inside one.

Session 57e7a5bf was one question, interrupted, then /export. The index made it four
interactions: the question, "[Request interrupted by user]", "/export" and "Conversation
copied to clipboard" — three turns nobody asked, each printed as "(tool-only — no final
text)". An interrupt and a local command belong to the turn they follow.
"""
from __future__ import annotations

from conftest import UUID, asst, call, result, text, user, write_jsonl

from tracer.db import get_write_connection
from tracer.parser import PARSER_VERSION, parse_jsonl, segment_interactions
from tracer.storage import Manifest
from tracer.turns import TurnSplitter, build_turn, short_tool, turn_starts


def test_interrupt_and_local_command_ride_inside_the_turn(session_tree):
    recs = segment_interactions(parse_jsonl(session_tree), UUID, str(session_tree))
    assert [r.id for r in recs] == [f"{UUID}-000", f"{UUID}-001"]
    first, second = recs
    assert first.user_message_preview == "Which of our sessions discussed the sandbox profile?"
    assert first.ending == "interrupted:5"
    assert first.local_commands == ["/export (local command) → Conversation copied to clipboard"]
    # duration runs to the interrupt, not to the /export ten seconds later
    assert first.duration_seconds == 40
    assert second.user_message_preview == "and the answer?"
    assert second.ending == "answered" and second.local_commands == []


def test_a_skill_slash_command_is_a_prompt_a_local_one_is_not():
    skill = user("<command-message>trace</command-message>\n<command-name>/tracer:trace</command-name>\n<command-args>x</command-args>", 1)
    caveat = user("<local-command-caveat>Caveat: …</local-command-caveat>", 2, isMeta=True)
    local = user("<command-name>/copy</command-name>", 2)
    after = user("<command-name>/tracer:blame</command-name>", 3)
    msgs = [skill, asst([text("ok")], 1, "m"), caveat, local, after]
    # the caveat marks only the command right after it as local
    assert turn_starts(msgs) == [0, 4]


def test_compaction_summary_and_system_local_output_do_not_open_turns():
    msgs = [
        user("go", 0),
        asst([call("t1", "Bash", command="ls")], 1, "m1"),
        user("This session is being continued from a previous conversation…", 2, isCompactSummary=True),
        user("<local-command-caveat>c</local-command-caveat>", 3, isMeta=True),
        user("<command-name>/reload-plugins</command-name>", 3),
        {"type": "system", "subtype": "local_command", "timestamp": "2026-09-22T17:00:04Z",
         "content": "<local-command-stdout>Reloaded 3 plugins</local-command-stdout>",
         "commandRun": {"command": "reload-plugins", "args": ""}},
    ]
    assert turn_starts(msgs) == [0]
    turn = build_turn(list(enumerate(msgs)))
    assert [c.line() for c in turn.local_commands] == ["/reload-plugins (local command) → Reloaded 3 plugins"]
    assert turn.ending == "tool:1"


def test_ending_says_how_a_turn_stopped():
    answered = build_turn(list(enumerate([user("q", 0), asst([text("a")], 1, "m")])))
    pre_text_only = build_turn(list(enumerate([
        user("q", 0), asst([text("let me look")], 1, "m"), asst([call("t", "Read", file_path="/x")], 1, "m"),
        result("t", "body", 2),
    ])))
    empty = build_turn(list(enumerate([user("q", 0)])))
    assert answered.ending == "answered" and answered.final_text == "a"
    # narration before a call is not an answer
    assert pre_text_only.ending == "tool:1" and pre_text_only.final_text == ""
    assert pre_text_only.last_words == ("text", "let me look")
    assert empty.ending == "empty"


def test_splitter_state_resets_on_a_prompt():
    s = TurnSplitter()
    assert s.kind(user("<local-command-caveat>c</local-command-caveat>", 0, isMeta=True)) == "caveat"
    assert s.kind(user("real question", 1)) == "prompt"
    assert s.kind(user("<command-name>/tracer:trace</command-name>", 2)) == "prompt"


def test_an_older_parser_makes_a_session_stale(tmp_path):
    """Turn numbering changed; a session indexed under the old one must be re-derived,
    or the drill's ids and the listing's ids disagree."""
    con = get_write_connection(tmp_path / "t.db")
    m = Manifest(con, "-p")
    con.execute("INSERT INTO manifest (project_hash, session_filename, mtime, record_count, file_size, parser_version) "
                "VALUES ('-p', 's.jsonl', 1.0, 1, 1, ?)", (PARSER_VERSION - 1,))
    assert m.needs_indexing("s.jsonl", 1.0)
    m.update_session("s.jsonl", 1.0, 1, 1)
    assert not m.needs_indexing("s.jsonl", 1.0)
    assert m.needs_indexing("s.jsonl", 2.0)
    con.close()


def test_new_columns_round_trip_through_the_index(indexed):
    from tracer.query import QueryFilters, query_interactions

    con, _ = indexed
    recs = query_interactions(QueryFilters(session_id=UUID), con=con)
    assert recs[0].ending == "interrupted:5"
    assert recs[0].local_commands == ["/export (local command) → Conversation copied to clipboard"]


def test_short_tool_names():
    assert short_tool("mcp__plugin_tracer_tracer__convo_search") == "tracer.convo_search"
    assert short_tool("mcp__plugin_brain_observer__search") == "observer.search"
    assert short_tool("mcp__GitHub__get_me") == "GitHub.get_me"
    assert short_tool("Bash") == "Bash"


def test_blank_and_pasted_image_prompts(tmp_path):
    img = {"type": "user", "timestamp": "2026-09-22T17:00:00Z",
           "message": {"role": "user", "content": [{"type": "image", "source": {}}]}}
    path = write_jsonl(tmp_path / "s.jsonl", [img, asst([text("a cat")], 1, "m")])
    assert len(segment_interactions(parse_jsonl(path), "s")) == 1


def test_a_task_notification_prompt_reads_as_its_summary():
    from tracer.parser import extract_user_text
    from tracer.turns import prompt_text

    note = ("<task-notification>\n<task-id>a4b75c557bd84dad1</task-id>\n<tool-use-id>toolu_019z</tool-use-id>\n"
            "<output-file>/private/tmp/x.output</output-file>\n<status>completed</status>\n"
            '<summary>Agent "dig" finished</summary>\n</task-notification>')
    assert prompt_text(note) == 'task notification: Agent "dig" finished'
    assert extract_user_text(user(note, 0)) == 'task notification: Agent "dig" finished'
    assert prompt_text("<command-name>/x</command-name> hi") == "/x hi"


def test_an_old_writer_cannot_pass_its_rows_off_as_current(session_tree, tmp_path):
    """The released Stop hook (parser 1) re-indexed 57e7a5bf after its mtime moved: four
    rows back, ending empty — and the manifest still said parser_version 2, because its
    upsert never names that column. needs_indexing() then called it current forever.
    Played here with the old writer's own statements, verbatim in their column lists."""
    from tracer.storage import ensure_session_indexed

    db, root = tmp_path / "index.db", session_tree.parent.parent
    assert ensure_session_indexed(UUID, db_path=db, projects_root=root)["interactions"] == 2

    con = get_write_connection(db)
    con.execute("DELETE FROM interactions WHERE session_id = ?", (UUID,))
    con.executemany(
        """INSERT INTO interactions
           (id, project_hash, session_id, session_file, byte_offset,
            timestamp, git_branch, user_message_preview, assistant_reply_preview,
            intent, outcome, tokens_estimated, duration_seconds)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(f"{UUID}-{i:03d}", "-tmp-proj", UUID, str(session_tree), 0, "", "main", p, "", None, None, 0, 0)
         for i, p in enumerate(["q", "[Request interrupted by user]", "/export", "Conversation copied"])],
    )
    mtime = session_tree.stat().st_mtime
    con.execute(
        """INSERT INTO manifest (project_hash, session_filename, mtime, record_count, file_size)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(project_hash, session_filename)
           DO UPDATE SET mtime = excluded.mtime, record_count = excluded.record_count,
                         file_size = excluded.file_size""",
        ("-tmp-proj", session_tree.name, mtime, 4, session_tree.stat().st_size),
    )
    assert con.execute("SELECT parser_version FROM manifest").fetchone()[0] == PARSER_VERSION  # the forgery
    assert Manifest(con, "-tmp-proj").needs_indexing(session_tree.name, mtime)
    con.close()

    healed = ensure_session_indexed(UUID, db_path=db, projects_root=root)
    assert healed["status"] == "indexed" and healed["interactions"] == 2
    con = get_write_connection(db)
    assert con.execute("SELECT count(*), MIN(parser_version) FROM interactions WHERE session_id = ?",
                       (UUID,)).fetchone() == (2, PARSER_VERSION)
    assert not Manifest(con, "-tmp-proj").needs_indexing(session_tree.name, mtime)
    con.close()
