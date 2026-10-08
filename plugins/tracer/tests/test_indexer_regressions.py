#!/usr/bin/env python3
"""Regressions for the four defects that made /tracer:trace return nothing.

Each of these failed SILENTLY in production for weeks — an empty result is
indistinguishable from "nothing to report" unless something asserts otherwise.
Run: uv run python tests/test_indexer_regressions.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import json

from tracer.db import get_write_connection
from tracer.models import InteractionRecord
from tracer.parser import (
    ASSISTANT_PREVIEW_CAP,
    detect_bash_error,
    parse_jsonl,
    segment_interactions,
)
from tracer.models import ErrorSignal, FileTouch
from tracer.query import QueryFilters, format_dialogue, format_record_short, format_results
from tracer.storage import IndexWriter, ensure_session_indexed, get_project_hash


def test_dotted_project_slug():
    """Claude Code turns BOTH "/" and "." into "-". Missing the "." rule aimed
    every dotted project at a directory that does not exist -> 0 files, exit 0."""
    assert get_project_hash("/Users/x/.brain") == "-Users-x--brain"  # brand: historical — the slug Claude Code gave the real ~/.brain
    assert get_project_hash("/Users/x/plain") == "-Users-x-plain"
    assert get_project_hash("/a/.b/.c") == "-a--b--c"


def test_same_session_under_two_projects():
    """Claude Code files one session under several project dirs (nested cwd,
    worktrees). Interaction ids are global, so a project-scoped delete left the
    first project's rows behind and the second insert died on UNIQUE(id),
    aborting the rest of an --all sweep."""
    rec = InteractionRecord(id="sess-000", session_id="sess", timestamp="2026-01-01T00:00:00Z")
    with tempfile.TemporaryDirectory() as td:
        con = get_write_connection(Path(td) / "t.db")
        IndexWriter(con, "-proj-a").replace_session_records("sess", [rec])
        IndexWriter(con, "-proj-b").replace_session_records("sess", [rec])  # used to raise
        rows = con.execute("SELECT project_hash FROM interactions WHERE id='sess-000'").fetchall()
        assert rows == [("-proj-b",)], f"expected one row, last writer wins; got {rows}"
        con.close()


def test_expired_transcript_survives_indexing():
    """Claude Code expires transcripts on a cleanupPeriodDays clock (default 30).
    The index used to mirror that deletion on every Stop hook, destroying the
    only surviving record of the conversation. The index is an archive."""
    import json as _json

    from tracer.storage import _index_project

    sid = "aaaaaaaa-0000-0000-0000-000000000000"
    msgs = [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "timestamp": "2026-01-01T00:00:00Z", "cwd": "/tmp/proj",
         "message": {"role": "user", "content": "hello world"}},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
         "timestamp": "2026-01-01T00:00:01Z", "cwd": "/tmp/proj",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}},
    ]
    with tempfile.TemporaryDirectory() as td:
        conv = Path(td) / "proj"
        conv.mkdir()
        jsonl = conv / f"{sid}.jsonl"
        jsonl.write_text("\n".join(_json.dumps(m) for m in msgs) + "\n")

        con = get_write_connection(Path(td) / "t.db")
        con.execute("BEGIN")
        _index_project(con, "-proj", conv)
        con.execute("COMMIT")
        n = con.execute("SELECT count(*) FROM interactions WHERE session_id=?", (sid,)).fetchone()[0]
        assert n == 1, f"expected the session indexed, got {n}"

        jsonl.unlink()  # retention sweep ages it out
        con.execute("BEGIN")
        _index_project(con, "-proj", conv)
        con.execute("COMMIT")
        n = con.execute("SELECT count(*) FROM interactions WHERE session_id=?", (sid,)).fetchone()[0]
        assert n == 1, "index reaped a session whose transcript merely expired"
        con.close()


def test_empty_result_names_its_filters():
    """`Filters: none` on a filtered query is a lie, and it is the reason the
    original failure took a full investigation instead of one glance."""
    out = format_results([], QueryFilters(session_id="abc", tool_name="Bash"), 0)
    assert "session=abc" in out and "tool=Bash" in out, out
    assert format_results([], QueryFilters(), 0).endswith("none")


def _tool_result(text, is_error=None):
    block = {"type": "tool_result", "content": [{"type": "text", "text": text}]}
    if is_error is not None:
        block["is_error"] = is_error
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def test_tool_result_that_mentions_errors_is_not_an_error():
    """`"error" in text.lower()` flagged a WebSearch result whose links talked
    about error handling — ERR:4 on a session with zero failed tool calls."""
    hits = 'Web search results for query: "MCP inspector"\n\nLinks: [{"title":"Error handling in MCP servers","url":"https://x"}]'
    assert detect_bash_error(_tool_result(hits)) is None
    docs = "> ## Documentation Index\n> Fetch the complete documentation index at: https://x/llms.txt\n> Errors are reported inline."
    assert detect_bash_error(_tool_result(docs)) is None


def test_failed_tool_result_is_an_error():
    for text in (
        "Exit code 1\nzsh: command not found: foo",
        "Error: ENOENT: no such file or directory",
        "Traceback (most recent call last):\n  File \"x.py\", line 1",
        "FileNotFoundError: [Errno 2] No such file",
    ):
        assert detect_bash_error(_tool_result(text)) is not None, text
    assert detect_bash_error(_tool_result("anything at all", is_error=True)) is not None
    assert detect_bash_error(_tool_result("Exit code 0\nall good")) is None


def test_dialogue_last_answer_is_whole():
    """The index keeps a 500-char preview of each answer. A recap read that
    preview *as* the answer — the closing table of a 5k-char reply was gone,
    and nothing marked the cut."""
    long_first = "F" * (ASSISTANT_PREVIEW_CAP + 700)
    long_last = "L" * (ASSISTANT_PREVIEW_CAP + 700)
    lines = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z",
         "message": {"role": "user", "content": "first"}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": long_first}]}},
        {"type": "user", "timestamp": "2026-01-01T00:01:00Z",
         "message": {"role": "user", "content": "second"}},
        {"type": "assistant", "timestamp": "2026-01-01T00:01:01Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "narration"}]}},
        {"type": "assistant", "timestamp": "2026-01-01T00:01:02Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": long_last}]}},
    ]
    with tempfile.TemporaryDirectory() as td:
        jsonl = Path(td) / "sess.jsonl"
        jsonl.write_text("\n".join(json.dumps(m) for m in lines) + "\n")
        records = segment_interactions(parse_jsonl(jsonl), "sess", str(jsonl))
        assert len(records) == 2
        assert len(records[1].assistant_reply_preview) == ASSISTANT_PREVIEW_CAP

        con = get_write_connection(Path(td) / "t.db")
        IndexWriter(con, "-proj").replace_session_records("sess", records)
        filters = QueryFilters(session_id="sess")

        out = format_dialogue(records, filters, con=con)
        first, last = out.split("[001]")
        assert long_last in last, "last answer must come whole from the JSONL"
        assert "[…]" not in last
        assert long_first not in first and "F" * ASSISTANT_PREVIEW_CAP + " […]" in first, \
            "an earlier answer stays a preview, and says so"

        assert long_first in format_dialogue(records, filters, con=con, full="all")
        assert long_last not in format_dialogue(records, filters, con=con, full="none")
        con.close()


def test_ensure_session_indexed_heals_a_missing_or_stale_session():
    """Three of the last seventeen traces rendered an empty block for a session
    that was on disk, un-indexed. Trace now indexes that one file first."""
    import os, time
    lines = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}},
    ]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "projects"; proj = root / "-Users-x--ak"; proj.mkdir(parents=True)
        db = Path(td) / "index.db"
        uuid = "11111111-2222-3333-4444-555555555555"

        r = ensure_session_indexed(uuid, db_path=db, projects_root=root)
        assert r["status"] == "missing", r

        f = proj / f"{uuid}.jsonl"
        f.write_text("\n".join(json.dumps(m) for m in lines) + "\n")
        r = ensure_session_indexed(uuid, db_path=db, projects_root=root)
        assert r["status"] == "indexed" and r["interactions"] == 1, r

        r = ensure_session_indexed(uuid, db_path=db, projects_root=root)
        assert r["status"] == "current" and r["interactions"] == 1, r

        lines += [
            {"type": "user", "timestamp": "2026-01-01T00:01:00Z", "message": {"role": "user", "content": "again"}},
            {"type": "assistant", "timestamp": "2026-01-01T00:01:01Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "sure"}]}},
        ]
        f.write_text("\n".join(json.dumps(m) for m in lines) + "\n")
        os.utime(f, (time.time() + 5, time.time() + 5))  # a changed file always has a new mtime
        r = ensure_session_indexed(uuid, db_path=db, projects_root=root)
        assert r["status"] == "indexed" and r["interactions"] == 2, r


def test_session_listing_shows_files_errors_and_duration():
    """The index has always held files touched, error previews and durations;
    `ak show` never printed them, so recaps re-read the transcript for
    "what did it write", "what were the errors", "why so long"."""
    rec = InteractionRecord(
        id="sess-003", session_id="sess", timestamp="2026-01-01T00:00:00Z",
        duration_seconds=521, tokens_estimated=1000,
        files_touched=[FileTouch(path="/tmp/x/report.md", operation="write"),
                       FileTouch(path="/tmp/x/report.md", operation="edit"),
                       FileTouch(path="/tmp/x/report.md", operation="edit"),
                       FileTouch(path="/tmp/x/lib.py", operation="read")],
        error_signals=[ErrorSignal(type="tool_error", preview="Exit code 1\nzsh: command not found: foo")],
    )
    short = format_record_short(rec)
    assert "dur=8m41s" in short and "files:" not in short, short
    detail = format_record_short(rec, detail=True)
    assert "files: /tmp/x/report.md (write, edit×2); /tmp/x/lib.py (read)" in detail, detail
    assert "err[tool_error]: Exit code 1 zsh: command not found: foo" in detail, detail
    # a session-scoped listing gets the detail lines; a cross-session one does not
    assert "files:" in format_results([rec], QueryFilters(session_id="sess"), 1)
    assert "files:" not in format_results([rec], QueryFilters(), 1)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all passed")
