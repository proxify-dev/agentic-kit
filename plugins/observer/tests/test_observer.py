"""The observer is a standard Claude Code plugin agent, agents/observer.md, and the summariser
runs it headless: its body is the system prompt, the message is the frame and the session.

It used to be a record of our own format under loadout/agents/ that a vault could override and
arm; loadout was archived on 2026-09-25 ("make it as an agent using the standard .agents, not
invent a standard of our own"). `--bare` skips plugin agents, so the call hands the file over
inline (`--agents`) — checked by hand: `--plugin-dir` + `--agent observer:observer`
answers "not found" under `--bare`, and without `--bare` the run loads the person's CLAUDE.md.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
from _brand import env_name  # noqa: E402
import observer_agent  # noqa: E402

SUMMARIZER = PLUGIN / "scripts" / "summarize-session.py"
UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
# The frontmatter a plugin agent may carry (code.claude.com/docs/en/plugins-reference, agents).
PLUGIN_AGENT_FIELDS = {"name", "description", "model", "effort", "maxTurns", "tools", "disallowedTools",
                       "skills", "memory", "background", "omitClaudeMd", "isolation", "color"}


def _summarizer():
    spec = importlib.util.spec_from_file_location("summarize_session", SUMMARIZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_observer_is_a_standard_plugin_agent():
    a = observer_agent.load()
    fields = set(a) - {"prompt", "path"}
    assert fields <= PLUGIN_AGENT_FIELDS, f"not a plugin-agent field: {fields - PLUGIN_AGENT_FIELDS}"
    assert a["name"] == "observer" and a["model"] == "haiku"
    assert "never delegate" in a["description"]  # it shows in every session's agent list
    assert "## The verdict comes first" in a["prompt"] and "## Corrections" in a["prompt"]
    assert "obs:<id>" in a["prompt"]  # the correction's supersedes rule the live vault had armed
    assert not (PLUGIN / "templates" / "loadout").exists()


def test_the_headless_call_runs_as_the_agent():
    argv = _summarizer().llm_argv("haiku")
    assert Path(argv[0]).stem == "claude" and argv[1] == "-p"  # the message goes on stdin, never argv
    assert argv[2].startswith("--")
    assert "--bare" in argv and "--strict-mcp-config" in argv
    assert argv[argv.index("--agent") + 1] == "observer"
    spec = json.loads(argv[argv.index("--agents") + 1])
    assert spec["observer"]["prompt"] == observer_agent.load()["prompt"]
    assert argv[argv.index("--tools") + 1] == ""  # it reads what it is sent; nothing else


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    (v / "cache" / "sessions").mkdir(parents=True)
    monkeypatch.setenv(env_name("PATH"), str(v))
    monkeypatch.setenv(env_name("STORE"), str(tmp_path / "store"))
    monkeypatch.setenv("CLAUDE_MEM_DB", str(tmp_path / "absent.db"))
    return v


def _transcript(path: Path, cwd: str) -> None:
    lines = [
        {"type": "user", "cwd": cwd, "sessionId": UUID, "timestamp": "2026-09-14T08:00:00.000Z",
         "message": {"role": "user", "content": "tell me about pelicans"}},
        {"type": "assistant", "cwd": cwd, "sessionId": UUID, "timestamp": "2026-09-14T08:00:05.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "they have pouches"}]}},
    ]
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


def test_the_message_is_the_frame_and_the_session(vault, tmp_path):
    transcript = tmp_path / f"{UUID}.jsonl"
    _transcript(transcript, str(tmp_path))
    env = {**os.environ, env_name("PATH"): str(vault), env_name("STORE"): str(tmp_path / "store"),
           "CLAUDE_MEM_DB": str(tmp_path / "absent.db")}
    r = subprocess.run([sys.executable, str(SUMMARIZER), "--session", UUID, "--transcript", str(transcript),
                        "--print-prompt"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    msg = r.stdout
    assert '"obs_type": one of [' in msg and "'correction'" in msg and "'violated'" in msg
    assert "- correction — the person overrode the agent" in msg
    assert "0 to ~20 observations" in msg and "Replace with [REDACTED]" in msg
    assert "CONVERSATION:" in msg and "tell me about pelicans" in msg
    assert "## The verdict comes first" not in msg  # the judgement is the system prompt, not the message
