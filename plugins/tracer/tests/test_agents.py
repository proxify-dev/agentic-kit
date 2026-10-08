"""`tracer sessions agents`: a session's subagents, one row each, and one agent's steps.

Written against 548c8046 (2026-10-05): 28 Agent calls, 25 transcripts under subagents/, four calls
refused for a bad `model` before any agent ran, and an agent that summed tokens with jq over the raw
files counted each model message once per transcript record. The session below is that shape, small:
two agents started by Agent calls (one tied by its meta's toolUseId, one only by the agentId its
result names), one refused call, and a workflow's agent that answers through StructuredOutput.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import asst, call, result, text, user, write_jsonl

from tracer import agents, cli
from tracer.drill import AmbiguousSession, DrillError, format_turn, load_turn
from tracer.verbs import command

SID = "548c8046-0000-4000-8000-000000000001"
MINER, BUILDER, WF = "a7c19e5d2b84f6c03", "a7cb0000000000001", "af1333318b030da41"
SHARED = "/tmp/proj/frames/f02.html"
REFUSED = ('<tool_use_error>InputValidationError: [\n  {\n    "code": "invalid_value",\n    "values": ["sonnet", "opus"],\n'
           '    "path": ["model"],\n    "message": "Invalid option: expected one of \\"sonnet\\"|\\"opus\\""\n  }\n]</tool_use_error>')
USAGE = {"input_tokens": 3, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 100, "output_tokens": 40}


def sidechain(rec: dict, aid: str) -> dict:
    return {**rec, "isSidechain": True, "agentId": aid}


def miner(aid: str) -> list[dict]:
    """Brief → one message of two parallel calls (two records, each repeating its usage) → a failed Bash
    → a report. Tokens: two messages of USAGE, never three."""
    return [sidechain(r, aid) for r in (
        user("Mine the demo footage for seconds 7-20.", 100),
        asst([call("t1", "Read", file_path=SHARED)], 103, "s1", USAGE),
        asst([call("t2", "WebFetch", url="https://example.com/a", prompt="dates")], 103, "s1", USAGE),
        result("t1", "<html>", 104),
        result("t2", "April 2024", 106),
        asst([call("t3", "Bash", command="python3 - <<'E' 2>/dev/null || true", description="parse cues")], 110, "s2", USAGE),
        result("t3", "Exit code 1", 111, is_error=True),
        asst([text("Found the clip at 25:36.")], 115, "s3"),
    )]


def builder(aid: str) -> list[dict]:
    return [sidechain(r, aid) for r in (
        user("Re-frame f02 for readability.", 200),
        asst([call("b1", "Read", file_path=SHARED)], 202, "b1m", USAGE),
        result("b1", "<html>", 203),
        asst([call("b2", "Write", file_path=SHARED, content="<html>new</html>")], 205, "b2m", USAGE),
        result("b2", "ok", 206),
        asst([text("Rewrote f02.")], 207, "b3m"),
    )]


def workflow_agent(aid: str) -> list[dict]:
    return [sidechain(r, aid) for r in (
        user("You are drafting the Prompt Engineering section.", 300),
        asst([call("w1", "Write", file_path=SHARED, content="x")], 302, "w1m", USAGE),
        result("w1", "ok", 303),
        asst([call("w2", "StructuredOutput", badge="prompt")], 305, "w2m", USAGE),
        result("w2", "Structured output provided successfully", 306),
    )]


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """CLAUDE_CONFIG_DIR/projects/-tmp-proj/<SID>.jsonl and its subagents/, as Claude Code lays them out."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    slug = tmp_path / "projects" / "-tmp-proj"
    sub = slug / SID / "subagents"
    write_jsonl(slug / f"{SID}.jsonl", [
        user("Find the clips, then fix f02.", 1),
        asst([call("toolu_bad", "Agent", description="Mine essay pages", prompt="…", subagent_type="content-miner",
                   model="sonnet[1m]")], 2, "m1"),
        result("toolu_bad", REFUSED, 3, is_error=True),
        asst([call("toolu_miner", "Agent", description="Scout the demo footage", prompt="Mine the demo…",
                   subagent_type="content-miner")], 4, "m2"),
        result("toolu_miner", [{"type": "text", "text": "Async agent launched"}], 5,
               tur={"agentId": MINER, "status": "async_launched"}),
        asst([call("toolu_builder", "Agent", description="Re-frame f02", prompt="Re-frame f02…")], 6, "m3"),
        result("toolu_builder", [{"type": "text", "text": "Async agent launched"}], 7,
               tur={"agentId": BUILDER, "status": "async_launched"}),
        asst([text("Started two agents.")], 8, "m4"),
    ])
    write_jsonl(sub / f"agent-{MINER}.jsonl", miner(MINER))
    (sub / f"agent-{MINER}.meta.json").write_text(json.dumps(
        {"agentType": "content-miner", "description": "Scout the demo footage", "toolUseId": "toolu_miner",
         "model": "sonnet"}))
    write_jsonl(sub / f"agent-{BUILDER}.jsonl", builder(BUILDER))  # no toolUseId: tied by the agentId its result names
    (sub / f"agent-{BUILDER}.meta.json").write_text(json.dumps({"agentType": "general-purpose", "description": "Re-frame f02"}))
    wf = sub / "workflows" / "wf_1d96ca69-72d"
    write_jsonl(wf / f"agent-{WF}.jsonl", workflow_agent(WF))
    (wf / f"agent-{WF}.meta.json").write_text(json.dumps({"agentType": "workflow-subagent", "model": "sonnet"}))
    return slug


def invoke(*args):
    return CliRunner().invoke(cli.cli, ["sessions", "agents", *args])


def test_every_subagent_is_a_row_tied_to_the_step_that_started_it(tree):
    data = agents.as_data(agents.load(SID[:8]))
    rows = {r["id"]: r for r in data["agents"]}
    assert list(rows) == [MINER, BUILDER, WF], "in the order they started"
    assert rows[MINER]["spawned_by"] == {"turn": 0, "step": 2}, "by the toolUseId its meta names"
    assert rows[BUILDER]["spawned_by"] == {"turn": 0, "step": 3}, "by the agentId the call's result names"
    assert rows[WF]["spawned_by"] is None and rows[WF]["workflow"] == "wf_1d96ca69-72d"
    assert rows[MINER]["calls"] == 3 and rows[MINER]["errors"] == 1 and rows[MINER]["ended"] == "reported"
    assert rows[WF]["ended"] == "reported", "a workflow's agent answers through StructuredOutput"
    assert rows[MINER]["model"] == "claude-test", "the model its messages name, not the meta's alias"
    assert rows[MINER]["seconds"] == 15.0


def test_tokens_count_once_per_model_message(tree):
    """s1 is one message written as two records, each repeating its usage: jq over the records said 3×."""
    row = next(r for r in agents.as_data(agents.load(SID))["agents"] if r["id"] == MINER)
    assert row["tokens"]["out"] == 2 * USAGE["output_tokens"]
    assert row["tokens"]["cache_read"] == 2 * USAGE["cache_read_input_tokens"]


def test_a_refused_call_is_listed_as_one_that_started_nothing(tree):
    data = agents.as_data(agents.load(SID))
    assert data["not_started"] == [{"turn": 0, "step": 1, "description": "Mine essay pages",
                                    "said": 'bad parameter — model: Invalid option: expected one of "sonnet"|"opus"'}]


def test_files_two_agents_touched_are_named(tree):
    ov = agents.as_data(agents.load(SID))["overlap"]
    assert ov["written"] == [{"path": SHARED, "agents": [BUILDER[:8], WF[:8]]}]
    assert ov["read"] == [{"path": SHARED, "agents": [MINER[:8], BUILDER[:8]]}]


def test_the_rollup_prints_types_rows_what_started_nothing_and_the_next_command(tree):
    r = invoke(SID[:8])
    assert r.exit_code == 0, r.output
    out = r.output
    assert out.startswith(f"{SID} · 3 subagents · ")
    assert "== by type, the most agent time first ==\ntype\tagents\tcalls\terrors\tagent time\tout\tslowest" in out
    assert re.search(rf"^{MINER[:8]}\t17:01:40\tcontent-miner\t000#2\t3\t1\t15s\t80\treported\tScout the demo footage$", out, re.M)
    assert re.search(rf"^{WF[:8]}\t.*\twf_1d96ca69-72d\t", out, re.M), "a workflow's agent says which run started it"
    assert "not started: 1 Agent call ran no agent — 000#1 'Mine essay pages': bad parameter — model:" in out
    assert f"changed by more than one agent: 1 — {SHARED} ({BUILDER[:8]}, {WF[:8]})" in out
    assert f"hint: {command('sessions agents')} 548c8046 <id>" in out


def test_json_is_the_same_rows(tree):
    r = invoke(SID[:8], "--json")
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert [a["id"] for a in data["agents"]] == [MINER, BUILDER, WF]
    assert data["totals"]["agents"] == 3 and data["totals"]["errors"] == 1


def test_one_agent_lists_its_steps_as_a_turn_is_listed(tree):
    r = invoke(SID[:8], MINER[:8])
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    assert lines[0] == f'{MINER} · content-miner · claude-test · "Scout the demo footage"'
    assert lines[1].startswith(f"session {SID} · started by turn 000 step 2 · 17:01:40 → 17:01:55 · 15s · 3 calls · ended: reported")
    assert "tokens: in 6 · cache read 2.0k · cache write 200 · out 80 (thinking 0) — once per model message" in r.output
    assert f"brief: Mine the demo footage for seconds 7-20. — whole: {command('trace turn')} 548c8046-000 --open 2" in r.output
    assert f"{command('sessions agents')} 548c8046 {MINER[:8]} --open 1-3 opens steps" in r.output
    assert re.search(r"^#1 17:01:43 \(\+3s\) Read .*→ 6 B ok 1s", r.output, re.M)
    assert re.search(r"^#2 17:01:43 ∥ WebFetch", r.output, re.M), "two calls of one message"
    assert re.search(r"^#3 .* Bash parse cues → 11 B ERROR", r.output, re.M)
    assert "final: Found the clip at 25:36." in r.output


def test_steps_and_open(tree):
    r = invoke(SID[:8], MINER[:8], "--steps", "3")
    assert r.exit_code == 0, r.output
    assert "#3 " in r.output and "#1 " not in r.output
    r = invoke(SID[:8], MINER[:8], "--open", "3")
    assert r.exit_code == 0, r.output
    assert r.output.startswith(f"548c8046 agent {MINER[:8]} step 3/3 · Bash · ")
    assert "python3 - <<'E' 2>/dev/null || true" in r.output
    assert f"/subagents/agent-{MINER}.jsonl:" in r.output, "source: the agent's own transcript"


def test_one_agent_as_json(tree):
    data = json.loads(invoke(SID[:8], WF[:8], "--json").output)
    assert data["id"] == WF and data["report"] == '{"badge": "prompt"}'
    assert [s["tool"] for s in data["turns"][0]["steps"]] == ["Write", "StructuredOutput"]


@pytest.mark.parametrize("args,code,said", [
    (("548c8046", "zzz"), 1, "no agent 'zzz' in 548c8046: it has 3 subagents"),
    (("548c8046", "a7c"), 2, "'a7c' starts 2 agents"),
    (("548c8046", "--open", "1"), 2, "--steps, --open and --turn read one agent"),
    (("548c8046", MINER[:8], "--steps", "1", "--open", "1"), 2, "give one of them"),
])
def test_a_miss_says_what_it_looked_for(tree, args, code, said):
    r = invoke(*args)
    assert r.exit_code == code, r.output
    assert said in r.output


def test_a_session_without_subagents_exits_1(tree):
    sid = "57e7a5bf-0000-4000-8000-000000000009"
    write_jsonl(tree / f"{sid}.jsonl", [user("hi", 1), asst([text("hello")], 2, "m1")])
    r = invoke(sid[:8])
    assert r.exit_code == 1
    assert "warn: no subagents in 57e7a5bf" in r.output


def test_the_turn_and_the_session_point_at_the_agents_verb(tree):
    """Where the 548c8046 agent met only a transcript path and went to jq."""
    out = format_turn(load_turn(f"{SID}-000"))
    step = next(ln for ln in out.splitlines() if ln.startswith("#2 "))
    assert step.endswith(f"· transcript: subagents/agent-{MINER}.jsonl · its steps: {command('sessions agents')} 548c8046 {MINER[:8]}")
    from tracer.drill import format_session, load_session

    sid = "57e7a5bf-0000-4000-8000-000000000002"  # big enough to list turns, not steps
    write_jsonl(tree / f"{sid}.jsonl", [r for n in range(4) for r in (user(f"q{n}", n * 10), asst([text("a")], n * 10 + 1, f"m{n}"))])
    (tree / sid / "subagents").mkdir(parents=True)
    assert f"subagents: {command('sessions agents')} 57e7a5bf — one row each" in format_session(load_session(sid))


def test_the_session_start_line_names_the_verb(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "hooks"))
    import session

    assert f"`{command('sessions agents')} <session>`" in session.TEACH


def test_pick_reads_a_file_name_too(tree):
    got = agents.load(SID)
    assert agents.pick(got, f"agent-{MINER}.jsonl").agent_id == MINER
    with pytest.raises(AmbiguousSession):
        agents.pick(got, "a7c")
    with pytest.raises(DrillError, match="no agent named"):
        agents.pick(got, "  ")
