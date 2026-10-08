"""Inspecting a session in as few calls as the question needs.

Asked "how did 57e7a5bf perform with our ak tools", a probe session (e740f43a) spent 14
calls: dialogue and query for the same header, the drill, five step-opening calls because
each step line said only "→ 913 B ok", and two Bash+jq runs on a saved file to learn
whether get_observations' rows had a session id. The session view, a gist per step, flags
a machine can check, several steps per call and a JSON result's shape answer those.
"""
from __future__ import annotations

import json

import pytest
from conftest import UUID, asst, call, result, text, user, write_jsonl

from tracer import drill
from tracer.verbs import command
from tracer.drill import (
    DrillError,
    annotate,
    format_session,
    load_session,
    load_turn,
    open_steps,
    parse_steps,
    render,
)
from tracer.gist import gist, json_view, shape, similarity


@pytest.fixture
def on_disk(session_tree, monkeypatch):
    """The fixture session, findable by uuid and by prefix, with no index."""
    monkeypatch.setattr(drill, "find_session_files", lambda sid: [session_tree] if sid == UUID else [])
    monkeypatch.setattr(drill, "match_session_ids", lambda p: [UUID] if UUID.startswith(p) else [])
    return session_tree


# ── gists ────────────────────────────────────────────────────────────────────


def test_a_json_result_gists_as_its_shape():
    rows = [{"id": str(i), "date": "d", "title": "t", "project": "p"} for i in range(30)]
    assert shape({"count": 30, "results": rows}) == "{count: 30, results: [30 × {id,date,title,project}]}"
    # a key only some rows carry is marked, so "no session field" is visible at a glance
    mixed = [{"id": 1, "session": "s"}, {"id": 2}]
    assert shape(mixed) == "[2 × {id,session?}]"
    mcp = json.dumps({"result": json.dumps({"observations": [{"id": "1", "content": "x"}] * 17})})
    assert gist(mcp) == "{observations: [17 × {id,content}]}"


def test_gists_of_text_errors_reads_and_persisted_output():
    assert gist("\n\n```\nfirst real line\nsecond\nthird\n") == "first real line (+3 lines)"
    assert gist("<tool_use_error>\nInputValidationError: bad model\n</tool_use_error>") .startswith("InputValidationError: bad model")
    assert gist("     1\t# Title\n     2\tbody\n", tool="Read") == "2 lines: # Title"
    persisted = "<persisted-output>\nOutput too large (130.4KB). Full output saved to: /x\n\nPreview (first 2KB):\nROW one\n</persisted-output>"
    assert gist(persisted, persisted_lines=577) == "ROW one (577 lines)"
    assert len(gist("x" * 500)) <= 120


def test_json_view_shows_first_rows_of_the_biggest_list():
    doc = {"count": 5, "results": [{"id": i, "title": f"t{i}"} for i in range(5)]}
    view = json_view(doc, rows=2)
    assert view[0] == "shape: {count: 5, results: [5 × {id,title}]}"
    assert view[1] == 'results[0]: {"id":0,"title":"t0"}'
    assert view[-1] == "… 3 more in results"


def test_similarity_needs_enough_words():
    a = " ".join(f"session{i} hit" for i in range(30))
    assert similarity(a, a + " extra") >= 0.8
    assert similarity("file updated", "file updated") == 0.0  # too few words to mean anything


# ── flags ────────────────────────────────────────────────────────────────────


def test_flags_are_mechanical(tmp_path):
    rows = " ".join(f"row{i}" for i in range(40))
    recs = [
        user("go", 0),
        asst([call("t1", "mcp__plugin_x_x__search", query="a")], 1, "m1"),
        result("t1", json.dumps({"result": rows}), 2),
        asst([call("t2", "mcp__plugin_x_x__search", query="b")], 3, "m2"),
        result("t2", json.dumps({"result": rows + " row99"}), 4),
        asst([call("t3", "mcp__plugin_x_x__search", query="a")], 5, "m3"),
        result("t3", "different words entirely here", 6),
        asst([call("t4", "Bash", command="cat ~/.claude/projects/p/s.jsonl | head")], 7, "m4"),
        result("t4", "x" * 40_000, 8),
    ]
    path = write_jsonl(tmp_path / "p" / "s.jsonl", recs)
    from tracer.turns import build_turn

    turn = build_turn([(i + 1, r) for i, r in enumerate(recs)])
    notes = annotate(turn, path.with_suffix(""))
    assert notes[2].flags == ["≈#1"]  # same tool, near-identical result
    assert notes[3].flags == ["=#1"]  # the very same call again
    assert notes[4].flags == ["large", "raw"]
    assert notes[1].flags == []


# ── the session view ─────────────────────────────────────────────────────────


def test_a_small_session_is_one_call(on_disk):
    out = render(UUID[:8])  # a prefix is enough
    lines = out.splitlines()
    assert lines[0].startswith(f'{UUID} · "Sandbox profile discussions" · 2026-09-22 17:00 UTC · /tmp/proj · main')
    assert lines[1] == "asked: Which of our sessions discussed the sandbox profile?"
    assert lines[2] == "last asked (001): and the answer?"
    assert lines[3] == "ended: answered (final text below)"
    assert lines[4].startswith("2 turns · 5 steps · 43s in turns: tools ")
    # counted once per API message: the two records of message m1 carry one usage
    assert lines[5] == ("tokens: in 2 · cache read 1.0k · cache write 50 · out 244 (thinking 56) · claude-test"
                        " · cost not recorded (no cost-state in the transcript)")
    assert lines[6].startswith("tools: Bash×2, tracer.convo_search, observer.search, Agent")
    assert lines[7] == "signals: interrupted 000 after #5 · errors 000#4 Bash · large 000#3 117.2 KB"
    # every step inline, each with what came back
    assert "── turn 000 · 17:00:01 · 5 steps · 40s · asked: Which of our sessions" in out
    assert "   ↳ ROW (30,000 lines)" in out and "   ↳ Exit code 1" in out
    assert "── turn 001 · 17:01:00 · 0 steps · 3s · asked: and the answer?" in out
    assert out.rstrip().endswith("final: Three sessions discussed sandboxing.")


def test_claude_codes_own_cost_and_time_are_used_when_recorded(session_tree, on_disk):
    with open(session_tree, "a") as f:
        f.write(json.dumps({"type": "cost-state", "totalCostUSD": 0.8264, "totalAPIDuration": 87504,
                            "totalToolDuration": 3297, "modelUsage": {"claude-test": {
                                "inputTokens": 14, "outputTokens": 8773, "thinkingTokens": 5375,
                                "cacheReadInputTokens": 490407, "cacheCreationInputTokens": 110561,
                                "costUSD": 0.8264}}}) + "\n")
    out = format_session(load_session(UUID))
    assert "model 1m27s · tools 3s (Claude Code's count; calls running in parallel add up)" in out
    assert ("tokens: in 14 · cache read 490.4k · cache write 110.6k · out 8.8k (thinking 5.4k) · $0.83"
            " — Claude Code's own count") in out


def test_a_big_session_is_one_line_per_turn(tmp_path, monkeypatch):
    sid = "bbbbbbbb-0000-4000-8000-000000000001"
    recs = []
    for t in range(5):
        base = t * 100
        recs += [user(f"question {t}", base)]
        for k in range(12):
            recs += [asst([call(f"t{t}{k}", "Bash", command=f"echo {t} {k}")], base + 1 + k, f"m{t}{k}"),
                     result(f"t{t}{k}", f"out {k}", base + 1 + k, is_error=(t == 2 and k < 5) or (t == 3 and k < 3))]
        recs += [asst([text(f"answer {t}")], base + 30, f"a{t}")]
    path = write_jsonl(tmp_path / "p" / f"{sid}.jsonl", recs)
    monkeypatch.setattr(drill, "find_session_files", lambda s: [path])
    out = format_session(load_session(sid))
    assert "5 turns · 60 steps" in out
    assert "[002] 17:03:20 · 12 steps · 30s · answered · Bash×12 · ⚑ err5 · asked: question 2" in out
    # past six references, many across turns read as a count per turn
    assert "signals: errors 8 in 002×5, 003×3" in out
    assert out.splitlines()[-1].startswith(f"next: {command('trace turn')} {sid}-NNN for a turn's steps")
    assert "↳" not in out  # no step lines in the big view


def test_a_big_session_says_under_each_turn_what_its_calls_were_for(tmp_path, monkeypatch):
    """The did: line is the model's own descriptions, once each, in order, ✗ where the call
    failed — so a reader sees what a turn was doing without opening its steps."""
    sid = "dddddddd-0000-4000-8000-000000000001"
    said = ["Read the setup screen", "Run the setup tests", "Read the setup screen", "Run the setup tests"]
    recs = []
    for t in range(4):
        base = t * 100
        recs += [user(f"question {t}", base)]
        for k, desc in enumerate(said):
            recs += [asst([call(f"t{t}{k}", "Bash", command=f"echo {k}", description=desc)], base + 1 + k, f"m{t}{k}"),
                     result(f"t{t}{k}", "out", base + 1 + k, is_error=(t == 1 and k == 1))]
        recs += [asst([call(f"r{t}", "Read", file_path="/tmp/a.py")], base + 9, f"r{t}"),
                 result(f"r{t}", "1 x", base + 9)]
        recs += [asst([text(f"answer {t}")], base + 30, f"a{t}")]
    path = write_jsonl(tmp_path / "p" / f"{sid}.jsonl", recs)
    monkeypatch.setattr(drill, "find_session_files", lambda s: [path])
    lines = format_session(load_session(sid)).splitlines()
    turn0 = next(i for i, ln in enumerate(lines) if ln.startswith("[000]"))
    assert lines[turn0 + 1] == "      did: Read the setup screen · Run the setup tests"
    turn1 = next(i for i, ln in enumerate(lines) if ln.startswith("[001]"))
    assert lines[turn1 + 1] == "      did: Read the setup screen · Run the setup tests ✗ · Run the setup tests"


def test_a_turn_with_no_described_call_has_no_did_line(tmp_path, monkeypatch):
    sid = "eeeeeeee-0000-4000-8000-000000000001"
    recs = []
    for t in range(4):
        recs += [user(f"question {t}", t * 100), asst([call(f"r{t}", "Read", file_path="/tmp/a.py")], t * 100 + 1, f"r{t}"),
                 result(f"r{t}", "1 x", t * 100 + 1), asst([text("done")], t * 100 + 5, f"a{t}")]
    path = write_jsonl(tmp_path / "p" / f"{sid}.jsonl", recs)
    monkeypatch.setattr(drill, "find_session_files", lambda s: [path])
    assert "did:" not in format_session(load_session(sid))


def test_a_long_turn_lists_its_edges_and_errors_and_counts_the_rest(tmp_path, monkeypatch):
    sid = "cccccccc-0000-4000-8000-000000000001"
    recs = [user("poll", 0)]
    for k in range(80):
        recs += [asst([call(f"t{k}", "Monitor", command="tail -f log")], 1 + k, f"m{k}"),
                 result(f"t{k}", "tick", 1 + k, is_error=(k == 40))]
    path = write_jsonl(tmp_path / "p" / f"{sid}.jsonl", recs)
    monkeypatch.setattr(drill, "find_session_files", lambda s: [path])
    out = render(f"{sid}-000")
    step_ids = [ln.split()[0] for ln in out.splitlines() if ln.startswith("#")]
    assert step_ids[:10] == [f"#{i}" for i in range(1, 11)] and "#41" in step_ids and step_ids[-1] == "#80"
    assert "… #11–#40 (30 steps: Monitor×30; ⚑ rep30) — --steps 11-40" in out
    only = render(f"{sid}-000", steps="11-12")
    assert [ln.split()[0] for ln in only.splitlines() if ln.startswith("#")] == ["#11", "#12"]
    assert "…" not in "".join(ln for ln in only.splitlines() if ln.startswith("  …"))


# ── several steps in one call ────────────────────────────────────────────────


def test_parse_steps():
    assert parse_steps("1-3,5", 9) == [1, 2, 3, 5]
    assert parse_steps([4, 1, 4], 9) == [4, 1]
    assert parse_steps(2, 9) == [2]
    assert parse_steps("all", 3) == [1, 2, 3]
    with pytest.raises(DrillError):
        parse_steps("one", 3)


def test_open_steps_caps_each_and_keeps_the_token_line_once_per_message(indexed, monkeypatch):
    con, jsonl = indexed
    monkeypatch.setattr(drill, "find_session_files", lambda sid: [jsonl])
    out = open_steps(f"{UUID}-000", "1-3", con=con)
    parts = out.split("════════")
    assert len(parts) == 3
    assert "tokens:" in parts[0] and "tokens:" not in parts[1]  # steps 1 and 2 share a message
    assert "first 4,000 of 120,000 chars" in parts[2] and "bxeyk8xjx.txt" in parts[2]
    with pytest.raises(DrillError, match="has 2 turns"):
        open_steps(UUID, "1", con=con)


def test_one_step_opens_whole(indexed):
    con, _ = indexed
    out = open_steps(f"{UUID}-000", 3, con=con)
    assert "first 20,000 of 120,000 chars" in out


# ── the doors ────────────────────────────────────────────────────────────────


def test_the_drill_takes_a_session_and_opens_several_steps_at_once(on_disk, indexed):
    con, _ = indexed
    assert render(UUID, con=con).startswith(UUID)
    out = open_steps(f"{UUID}-000", [1, 2], con=con)
    assert out.count("step 1/5") == 1 and out.count("step 2/5") == 1
    with pytest.raises(DrillError, match="not a session id: 'zz'"):
        render("zz", con=con)


def test_the_search_verb_says_it_finds_mentions():
    from tracer.cli import grep

    assert grep.help.startswith("Which sessions talked about a phrase")
    assert "Mentions, not topics" in grep.help and "observer search X --by-session" in grep.help


def test_the_cli_inspect_verb(on_disk, capsys):
    from click.testing import CliRunner

    from tracer.cli import cli

    res = CliRunner().invoke(cli, ["inspect", UUID[:8]])
    assert res.exit_code == 0, res.output
    cap = capsys.readouterr()
    out = res.output + cap.out
    assert "asked: Which of our sessions discussed the sandbox profile?" in out
    assert f"{command('trace turn')} {UUID}-000 --open 1-3 opens steps" in out  # the hint names the verb
