"""Builders for synthetic transcripts: the record shapes Claude Code writes, small enough to read.

`interrupted_session()` is the shape of the session these tests were written against
(57e7a5bf, "Which of our sessions discussed the sandbox profile?"): parallel tool calls, a
result persisted to tool-results/, an interrupt, then /export — and one real prompt after.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tracer._brand import env_name

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_config_dir(monkeypatch):
    """A test that points HOME at a tmp dir must not read the real $CLAUDE_CONFIG_DIR."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
UUID = "57e7a5bf-0000-4000-8000-000000000001"


def ts(sec: int) -> str:
    return f"2026-09-22T17:{sec // 60:02d}:{sec % 60:02d}.000Z"


def user(text, sec, **extra):
    return {"type": "user", "timestamp": ts(sec), "gitBranch": "main", "cwd": "/tmp/proj",
            "message": {"role": "user", "content": text}, **extra}


def asst(blocks, sec, mid, usage=None):
    msg = {"id": mid, "model": "claude-test", "role": "assistant", "content": blocks}
    if usage:
        msg["usage"] = usage
    return {"type": "assistant", "timestamp": ts(sec), "message": msg}


def think(text):
    return {"type": "thinking", "thinking": text, "signature": "sig"}


def text(t):
    return {"type": "text", "text": t}


def call(tid, name, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def result(tid, content, sec, is_error=False, tur=None):
    block = {"type": "tool_result", "tool_use_id": tid, "content": content}
    if is_error:
        block["is_error"] = True
    rec = {"type": "user", "timestamp": ts(sec), "message": {"role": "user", "content": [block]}}
    if tur is not None:
        rec["toolUseResult"] = tur
    return rec


def meta(kind, **fields):
    return {"type": kind, "sessionId": UUID, **fields}


def write_jsonl(path: Path, records) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def interrupted_session(session_dir: Path) -> list[dict]:
    """Turn 000: think → two parallel calls → a persisted Bash → a failing Bash → an Agent →
    interrupted, then /export. Turn 001: a plain answer."""
    persisted = session_dir / "tool-results" / "bxeyk8xjx.txt"
    persisted.parent.mkdir(parents=True, exist_ok=True)
    persisted.write_text("ROW\n" * 30_000)  # 120,000 bytes; the transcript keeps a preview
    (session_dir / "tool-results" / "toolu_02.mcp__plugin_observer_observer__search.json").write_text('{"result":"x"}')
    sub = session_dir / "subagents"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "agent-a1b2c3.jsonl").write_text('{"type":"assistant"}\n')
    usage = {"input_tokens": 2, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50,
             "output_tokens": 244, "output_tokens_details": {"thinking_tokens": 56}}
    return [
        meta("last-prompt", leafUuid="x"),
        meta("mode", mode="normal"),
        {"type": "attachment", "timestamp": ts(0), "attachment": {"type": "hook_success", "stdout": "SessionStart says sandbox"}},
        user("Which of our sessions discussed the sandbox profile?", 1),
        meta("ai-title", aiTitle="Sandbox profile discussions"),
        asst([think("I will search the tracer and the observer in parallel.")], 4, "m1"),
        asst([call("toolu_01", "mcp__plugin_tracer_tracer__convo_search", query="sandbox", limit=50)], 4, "m1", usage),
        asst([call("toolu_02", "mcp__plugin_observer_observer__search", query="sandbox")], 4, "m1", usage),
        result("toolu_01", '{"result":"3 sessions matched \\"sandbox\\""}', 5),
        result("toolu_02", '{"result":"{\\"count\\": 1}"}', 7),
        asst([text("Let me scan the transcripts directly.")], 10, "m2"),
        asst([call("toolu_03", "Bash", command="cat > /tmp/sbx.py <<'EOF'\nimport json\nprint('sandbox')\nEOF\nuv run /tmp/sbx.py", description="scan")], 11, "m2"),
        result("toolu_03", f"<persisted-output>\nOutput too large (117.2KB). Full output saved to: {persisted}\n\nPreview (first 2KB):\nROW\n</persisted-output>", 35,
               tur={"stdout": "ROW", "stderr": ""}),
        asst([call("toolu_04", "Bash", command="false")], 36, "m3"),
        result("toolu_04", "Exit code 1", 37, is_error=True),
        asst([call("toolu_05", "Agent", description="dig", prompt="dig into it", subagent_type="Explore")], 38, "m4"),
        result("toolu_05", [{"type": "text", "text": "Async agent launched"}], 39, tur={"agentId": "a1b2c3", "status": "async_launched"}),
        asst([think("Too many headless sessions; filter the temp dirs out.")], 40, "m5"),
        {**user([{"type": "text", "text": "[Request interrupted by user]"}], 41), "interruptedMessageId": "m5"},
        meta("file-history-snapshot", messageId="x"),
        user("<local-command-caveat>Caveat: The messages below were generated by the user while running local commands.</local-command-caveat>", 51, isMeta=True),
        user("<command-name>/export</command-name>\n            <command-message>export</command-message>\n            <command-args></command-args>", 51),
        user("<local-command-stdout>Conversation copied to clipboard</local-command-stdout>", 51),
        user("and the answer?", 60),
        asst([text("Three sessions discussed sandboxing.")], 63, "m6"),
    ]


@pytest.fixture
def session_tree(tmp_path):
    """projects/<slug>/<uuid>.jsonl + its session dir, from interrupted_session()."""
    slug = tmp_path / "projects" / "-tmp-proj"
    sdir = slug / UUID
    records = interrupted_session(sdir)
    return write_jsonl(slug / f"{UUID}.jsonl", records)


@pytest.fixture
def indexed(session_tree, tmp_path):
    """The session indexed into a throwaway DB; yields (connection, jsonl path)."""
    from tracer.db import get_write_connection
    from tracer.parser import parse_jsonl, segment_interactions
    from tracer.storage import IndexWriter

    con = get_write_connection(tmp_path / "index.db")
    records = segment_interactions(parse_jsonl(session_tree), UUID, str(session_tree))
    IndexWriter(con, "-tmp-proj").replace_session_records(UUID, records)
    yield con, session_tree
    con.close()


def trace_sources():
    """The trace skill's lib/sources.py, imported the way fetch.py imports it."""
    skill = PLUGIN / "skills" / "trace"
    if str(skill) not in sys.path:
        sys.path.insert(0, str(skill))
    from lib import sources

    return sources


@pytest.fixture(autouse=True)
def no_real_gateway(tmp_path, monkeypatch):
    """The drill reads the machine's gateway trace (wire.py); every test reads its own, empty
    unless the test writes rows into it."""
    monkeypatch.setenv(env_name("GATEWAY_DATA_DIR"), str(tmp_path / "gateway"))


@pytest.fixture(scope="session", autouse=True)
def help_as_the_command_installs_it():
    """The `tracer` command's main() installs the copied help before anything runs: plain help and usage
    errors for anyone not at a terminal. A CliRunner test calls the group and skips main(), so the session
    installs it once, the same way — without it rich-click draws a usage error as a coloured, wrapped box
    wherever it sees GITHUB_ACTIONS or FORCE_COLOR, and cc-tracer's CI read a different error than a shell."""
    from tracer import help as help_mod
    from tracer.groups import FuzzyGroup
    from tracer.verbs import command

    help_mod.install(command(), groups=[], extra_classes=(FuzzyGroup,))


@pytest.fixture(autouse=True)
def plain_output(monkeypatch):
    """Every test starts in the plain grammar: `--json` switches the door's mode for the rest of the
    process (ui.json_mode), so a test that ran one would otherwise leak it into the next."""
    from tracer import ui

    monkeypatch.setattr(ui, "_MODE", "plain")


def printed(capsys, show, report) -> str:
    """What one of cli's printers (print_live, print_sessions) puts on stdout for REPORT, plain."""
    capsys.readouterr()
    show(report)
    return capsys.readouterr().out
