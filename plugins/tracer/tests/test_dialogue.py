"""The dialogue says how a turn ended; a session's listing reads in turn order.

57e7a5bf's dialogue printed "(tool-only — no final text)" for every turn — true, and
useless — and listed `[Request interrupted by user]`, `/export` and its stdout as three
turns. convo_query for the same session listed newest-first while the dialogue listed
oldest-first.
"""
from __future__ import annotations

import sqlite3

from conftest import UUID, asst, text, user, write_jsonl

from tracer.db import get_write_connection
from tracer.models import InteractionRecord
from tracer.parser import parse_jsonl, segment_interactions
from tracer.query import (
    QueryFilters,
    count_interactions,
    format_dialogue,
    format_results,
    query_dialogue,
    query_interactions,
)
from tracer.storage import IndexWriter


def test_dialogue_shows_how_a_turn_ended_and_folds_the_local_command(indexed):
    con, _ = indexed
    f = QueryFilters(session_id=UUID)
    out = format_dialogue(query_dialogue(f, con=con), f, con=con)
    assert out.startswith("2 turns (2026-09-22 to 2026-09-22)")
    first = out.split("[001]")[0]
    assert "[000] 2026-09-22T17:00:01 · 5 steps · 40s" in first
    assert "ASST: — no final text; interrupted by the user after step 5 (Agent). " \
           "last thinking: Too many headless sessions; filter the temp dirs out." in first
    assert "  /export (local command) → Conversation copied to clipboard" in first
    assert "Request interrupted" not in out and "USER: /export" not in out
    assert "tool-only" not in out
    assert "ASST: Three sessions discussed sandboxing." in out.split("[001]")[1]


def _many_turns(tmp_path, n=5):
    recs = []
    for i in range(n):
        recs += [user(f"question {i}", i * 10), asst([text(f"answer {i}")], i * 10 + 1, f"m{i}")]
    path = write_jsonl(tmp_path / "s.jsonl", recs)
    con = get_write_connection(tmp_path / "many.db")
    IndexWriter(con, "-p").replace_session_records("s", segment_interactions(parse_jsonl(path), "s", str(path)))
    return con


def test_a_session_lists_in_turn_order_and_a_limit_keeps_the_newest(tmp_path):
    con = _many_turns(tmp_path)
    f = QueryFilters(session_id="s", limit=3)
    assert [r.id for r in query_interactions(f, con=con)] == ["s-002", "s-003", "s-004"]
    assert [r.id for r in query_dialogue(f, con=con)] == ["s-002", "s-003", "s-004"]
    # across sessions: newest first, as before
    assert [r.id for r in query_interactions(QueryFilters(limit=2), con=con)] == ["s-004", "s-003"]

    listing = format_results(query_interactions(f, con=con), f, 5, matched=5)
    assert listing.startswith("5 interactions matched")
    assert "showing the newest 3 of 5, in turn order; --limit 5 for all" in listing
    assert listing.index("id=s-002") < listing.index("id=s-004")
    assert "next:" not in listing  # one session's turns: the session view already names them

    dialogue = format_dialogue(query_dialogue(f, con=con), f, con=con)
    assert dialogue.startswith("3 turns (2026-09-22 to 2026-09-22) — the last 3 of 5; --limit 5 for all")
    assert "ASST: answer 4" in dialogue
    assert count_interactions(QueryFilters(session_id="s"), con=con) == 5


def test_a_listing_flags_turns_that_gave_no_answer(indexed):
    con, _ = indexed
    f = QueryFilters(session_id=UUID)
    out = format_results(query_interactions(f, con=con), f, 2)
    first = out.split(f"id={UUID}-001")[0]
    assert "end=interrupted@5" in first
    assert "local: /export (local command) → Conversation copied to clipboard" in first


def test_an_index_written_before_the_new_columns_still_reads(tmp_path):
    """A read-only connection cannot migrate; the released writer may not have added
    `ending` / `local_commands` yet. Missing columns read as empty, not as an error."""
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE interactions (id TEXT PRIMARY KEY, project_hash TEXT, session_id TEXT,
            session_file TEXT, byte_offset INTEGER, timestamp TEXT, git_branch TEXT,
            user_message_preview TEXT, assistant_reply_preview TEXT, intent TEXT, outcome TEXT,
            tokens_estimated INTEGER, duration_seconds INTEGER);
        CREATE TABLE i_plugins (iid TEXT, plugin TEXT);
        CREATE TABLE i_tools (iid TEXT, name TEXT, count INTEGER);
        CREATE TABLE i_files (iid TEXT, path TEXT, operation TEXT);
        CREATE TABLE i_agents (iid TEXT, type TEXT, prompt_preview TEXT);
        CREATE TABLE i_errors (iid TEXT, type TEXT, preview TEXT);
        INSERT INTO interactions VALUES ('s-000','-p','s','',0,'2026-01-01T00:00:00Z','main','hi','hello',NULL,NULL,0,0);
        """
    )
    con.commit()
    recs = query_interactions(QueryFilters(session_id="s"), con=con)
    assert recs == [InteractionRecord(id="s-000", session_id="s", timestamp="2026-01-01T00:00:00Z",
                                      git_branch="main", user_message_preview="hi",
                                      assistant_reply_preview="hello")]
    assert "ASST: hello" in format_dialogue(recs, QueryFilters(session_id="s"), con=con)
    con.close()
