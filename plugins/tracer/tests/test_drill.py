"""The drill as steps, and one step whole.

The old drill printed ~59 "source messages" for 57e7a5bf, ~40 of them [attachment] /
[mode] / [last-prompt] noise, tool inputs cut at 150 chars, and no results at all — no
size, no is_error, no timing, no path to the 130 KB output the Bash step saved. There was
no way to open one call.
"""
from __future__ import annotations

import pytest
from conftest import UUID

from tracer import drill
from tracer.verbs import command
from tracer.drill import DrillError, format_step, format_turn, load_turn

IID = f"{UUID}-000"


def test_steps_are_numbered_with_timing_size_status_and_saved_paths(indexed):
    con, jsonl = indexed
    out = format_turn(load_turn(IID, con=con))
    lines = out.splitlines()
    assert lines[0].startswith(f"{IID} · 2026-09-22 17:00:01 UTC · main · 40s")
    assert "prompt: Which of our sessions discussed the sandbox profile?" in out
    # no metadata noise
    assert "attachment" not in out and "last-prompt" not in out and "ai-title" not in out
    # the thinking before the first call, then the two parallel calls
    assert "  think: I will search the tracer and the observer in parallel." in out
    # each call as its label: the query here, the model's description below, never the input JSON
    assert "#1 17:00:04 (+3s) tracer.convo_search sandbox → " in out
    assert "#2 17:00:04 ∥ observer.search" in out
    # a saved path only where the transcript holds less than the whole result
    assert "saved: toolu_02" not in out
    # text before a call shows as said:, the persisted result sized from its file
    assert "  said: Let me scan the transcripts directly." in out
    step3 = next(ln for ln in lines if ln.startswith("#3 "))
    assert "(+4s) Bash scan → 117.2 KB ok 24s saved: bxeyk8xjx.txt [large]" in step3
    assert lines[lines.index(step3) + 1] == "   ↳ ROW (30,000 lines)"
    step4 = next(ln for ln in lines if ln.startswith("#4 "))
    assert step4.endswith("ERROR 1s") and lines[lines.index(step4) + 1] == "   ↳ Exit code 1"
    assert "transcript: subagents/agent-a1b2c3.jsonl" in next(ln for ln in lines if ln.startswith("#5 "))
    assert "[large] ≥30 KB" in out  # the legend names only the flags that occur
    # the trailing thought, then how it ended, then the local command
    assert lines[-3] == "  think: Too many headless sessions; filter the temp dirs out."
    assert lines[-2] == "ended: interrupted by the user after step 5 (Agent) at 17:00:41"
    assert lines[-1] == "  /export (local command) → Conversation copied to clipboard"
    assert f"{command('trace turn')} {IID} --open 1-3 opens steps" in out


def test_the_answered_turn_ends_with_its_final_text(indexed):
    con, _ = indexed
    out = format_turn(load_turn(f"{UUID}-001", con=con))
    assert out.splitlines()[-1] == "final: Three sessions discussed sandboxing."


def test_one_step_whole_reads_the_persisted_file(indexed):
    con, jsonl = indexed
    loaded = load_turn(IID, con=con)
    out = format_step(loaded, 3, cap=1000)
    assert out.splitlines()[0] == f"{IID} step 3/5 · Bash · 17:00:11 → 17:00:35 (24s) · ok"
    assert "said before the call:\nLet me scan the transcripts directly." in out
    # a heredoc reads as a heredoc, not as one JSON string of \n
    assert "  command: |\n    cat > /tmp/sbx.py <<'EOF'\n    import json" in out
    assert "result · 120,000 B · 30,000 lines · first 1,000 of 120,000 chars:" in out
    assert "the whole result: " in out and out.count("ROW") >= 200
    assert "bxeyk8xjx.txt" in out.split("the whole result: ")[1]
    assert f"{jsonl.name}:12 (call) · :13 (result)" in out


def test_one_step_whole_has_tokens_error_mcp_unwrap_and_subagent(indexed):
    con, _ = indexed
    loaded = load_turn(IID, con=con)
    s2 = format_step(loaded, 2)
    assert "tokens: in 2 · cache read 1,000 · cache write 50 · out 244 (thinking 56) · claude-test" in s2
    assert "one message, shared by steps 1–2" in s2
    assert 'result (the MCP {"result": …} envelope unwrapped) · ' in s2 and '{"count": 1}' in s2
    assert "· ERROR" in format_step(loaded, 4).splitlines()[0]
    assert "subagent transcript: " in format_step(loaded, 5) and "agent-a1b2c3.jsonl" in format_step(loaded, 5)
    assert "has 5 step(s); step 9 does not exist" in format_step(loaded, 9)


def test_a_turn_the_index_never_saw_is_found_in_the_transcript(session_tree, monkeypatch):
    monkeypatch.setattr(drill, "find_session_files", lambda sid: [session_tree] if sid == UUID else [])
    loaded = load_turn(f"{UUID}-001")  # no index connection at all
    assert loaded.turn.prompt_text == "and the answer?"
    with pytest.raises(DrillError, match="has 2 turn"):
        load_turn(f"{UUID}-007")
    with pytest.raises(DrillError, match="not a session id: 'nonsense' — want a session UUID, its first 8"):
        load_turn("nonsense")


def test_an_index_from_before_the_fold_falls_back_to_the_transcript(indexed):
    """The old parser indexed '[Request interrupted by user]' as turn 001. Its row's
    offset points at the interrupt — not where a turn opens — so the drill walks the
    transcript instead and says so."""
    con, jsonl = indexed
    raw = jsonl.read_bytes()
    offset = raw.index(b"[Request interrupted by user]")
    offset = raw.rindex(b"\n", 0, offset) + 1
    con.execute("UPDATE interactions SET byte_offset = ? WHERE id = ?", (offset, f"{UUID}-001"))
    loaded = load_turn(f"{UUID}-001", con=con)
    assert loaded.note.startswith("the index is behind this transcript")
    assert loaded.turn.prompt_text == "and the answer?"


def test_a_gone_transcript_says_so(indexed):
    con, jsonl = indexed
    jsonl.unlink()
    with pytest.raises(DrillError, match="source JSONL not available"):
        load_turn(IID, con=con)
