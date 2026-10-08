"""hooks/raw_read.py — a raw read of the transcripts is answered with the tracer's doors.

Run the way Claude Code runs it (plugins/HOOKS.md §5): payload on stdin, CLAUDE_PLUGIN_ROOT set,
through uv as hooks.json starts it, in a throwaway HOME with uv pointed at its real cache and Pythons.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tracer._brand import HOME_DIR, env_name
from tracer.verbs import command

PLUGIN = Path(__file__).resolve().parents[1]
# how hooks.json starts a hook: uv picks a 3.10+ Python, never PATH's python3 (plugins/HOOKS.md §1)
UV_RUN = ["uv", "run", "--quiet", "--no-project", "--python", ">=3.10"]
UV_ENV = {k: subprocess.run(["uv", *argv], capture_output=True, text=True).stdout.strip()
          for k, argv in (("UV_CACHE_DIR", ["cache", "dir"]), ("UV_PYTHON_INSTALL_DIR", ["python", "dir"]))}
SCRUB = (env_name("CAPTURE"), env_name("CAPTURE_DISABLE"), "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PROJECT_DIR")


def run(home: Path, tool: str, tool_input: dict, entrypoint: str = "cli") -> str:
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    env.update(HOME=str(home), CLAUDE_PLUGIN_ROOT=str(PLUGIN), CLAUDE_CODE_ENTRYPOINT=entrypoint, **UV_ENV)
    payload = json.dumps({"hook_event_name": "PostToolUse", "session_id": "hook-0000",
                          "tool_name": tool, "tool_input": tool_input, "tool_response": {}})
    p = subprocess.run([*UV_RUN, str(PLUGIN / "hooks" / "raw_read.py")],
                       input=payload, capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr[-400:]
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    return (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.mark.parametrize("tool,inp", [
    ("Bash", {"command": "ls ~/.claude/projects/-Users-x-ak/*.jsonl | wc -l"}),
    ("Bash", {"command": "uv run python extract.py -- -Users-x-ak"}),   # not a read the hook can see
    ("Read", {"file_path": "/Users/x/.claude/projects/-Users-x-ak/0e81642b.jsonl"}),
    ("Grep", {"pattern": "entrypoint", "path": "/Users/x/.claude/projects"}),
    ("Bash", {"command": "cat ~/.claude/sessions/*.json"}),                # the registry `live` reads
])
def test_a_raw_read_is_answered_with_the_tracer_verbs(home, tool, inp):
    ctx = run(home, tool, inp)
    if ".claude/" in json.dumps(inp):
        for verb in (command("sessions live"), command("sessions"), command("sessions show"), command("sessions search")):
            assert verb in ctx, verb
        assert "convo_" not in ctx, "the MCP tools are retiring: the shell is the door"
    else:
        assert ctx == ""


@pytest.mark.parametrize("tool,inp", [
    ("Bash", {"command": "sqlite3 ~/.claude/brain-traces/traces.db 'select * from spans limit 5'"}),
    ("Bash", {"command": f"sqlite3 ~/{HOME_DIR}/db/traces/traces.db 'select outcome, count(*) from spans group by 1'"}),
    ("Read", {"file_path": f"/Users/x/{HOME_DIR}/db/traces/"}),
    ("Grep", {"pattern": "outcome", "path": "/Users/x/.claude/brain-traces/"}),
])
def test_a_raw_read_of_the_trace_store_is_answered_with_brain_trace(home, tool, inp):
    ctx = run(home, tool, inp)
    assert command("trace") in ctx
    assert command("trace get") in ctx and command("trace stats") in ctx
    assert command("sessions live") not in ctx, "the trace-store message is its own, not the transcript one"


@pytest.mark.parametrize("tool,inp,session", [
    ("Bash", {"command": "ls ~/.claude/projects/-Users-x/548c8046-3d3f-4fea-832f-e50d3e20c35c/subagents/"}, "548c8046"),
    ("Read", {"file_path": "/Users/x/.claude/projects/-Users-x/548c8046-3d3f-4fea-832f-e50d3e20c35c/subagents/agent-a7c19e5d.jsonl"},
     "548c8046"),
    ("Bash", {"command": "for f in ~/.claude/projects/-Users-x/*/subagents/agent-*.jsonl; do jq -s length $f; done"}, "<session>"),
])
def test_a_raw_read_of_a_subagent_is_answered_with_the_agents_verb(home, tool, inp, session):
    """2026-10-05, 548c8046: an agent summed subagents' tokens with jq and double-counted; the generic line
    named `sessions show`, which it had already read."""
    ctx = run(home, tool, inp)
    assert f"`{command('sessions agents')} {session}`" in ctx
    assert f"`{command('sessions agents')} {session} <agent>`" in ctx
    assert command("sessions live") not in ctx, "the subagent message is its own"


def test_a_pinned_result_a_program_and_other_tools_get_nothing(home):
    pinned = {"file_path": "/Users/x/.claude/projects/-Users-x-ak/abc/tool-results/toolu_01.Bash.txt"}
    assert run(home, "Read", pinned) == ""
    raw = {"command": "cat ~/.claude/projects/-Users-x-ak/a.jsonl"}
    assert run(home, "Bash", raw, entrypoint="sdk-cli") == ""
    assert run(home, "Edit", {"file_path": "/Users/x/.claude/projects/x/a.jsonl"}) == ""
    assert run(home, "Bash", {"command": "sqlite3 ~/.claude/brain-traces/traces.db .tables"}, entrypoint="sdk-cli") == ""
    assert not any(home.iterdir()), "the hook writes nothing"


def test_raw_read_and_score_py_trace_patterns_match():
    """The hook's _RAW and the eval scorer's _RAW must agree on what counts as a raw read of the
    trace store — evals/tool-routing/score.py's own `_RAW` docstring is the ground truth for `raw`."""
    score_path = PLUGIN / "evals" / "tool-routing" / "score.py"
    if not score_path.is_file():
        pytest.skip("the eval data stays on the owner's machine")
    added = r"brain-traces/|\bdb/traces\b|traces\.db\b"
    hook_src = (PLUGIN / "hooks" / "raw_read.py").read_text()
    score_src = score_path.read_text()
    assert added in hook_src, "hooks/raw_read.py _RAW is missing the trace-store patterns"
    assert added in score_src, "evals/tool-routing/score.py _RAW is missing the trace-store patterns"


def test_a_windows_path_and_a_moved_config_dir_are_raw_reads_too(home, monkeypatch):
    """A Windows session names C:\\Users\\x\\.claude\\projects; CLAUDE_CONFIG_DIR may move the folder."""
    assert "sessions live" in run(home, "Read", {"file_path": "C:\\Users\\x\\.claude\\projects\\p\\s.jsonl"})
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "cc-config"))
    assert "sessions live" in run(home, "Read", {"file_path": str(home / "cc-config" / "sessions" / "1.json")})
    assert run(home, "Read", {"file_path": str(home / "cc-config" / "settings.json")}) == ""
