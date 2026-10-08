"""The harness-engineering skill's assets/hook.py does what its docstring says, run the way Claude Code runs it.

As context: once per agent until a compaction, not for a program's session unless it opts in, quiet when it breaks.
In deny mode: a deny on every matching call, a program's session included, and a deny when it cannot check.
"""
from __future__ import annotations

from pathlib import Path

from conftest import COMPACT, PLUGIN, hook_said, run, write_transcript

TEMPLATE = PLUGIN / "skills" / "harness-engineering" / "assets" / "hook.py"
TAG = "[my-plugin: api]"


def edit(path: str, transcript: Path | None = None) -> dict:
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": path}}
    if transcript:
        payload["transcript_path"] = str(transcript)
    return payload


def deny_mode(tmp_path: Path) -> str:
    copy = tmp_path / "deny.py"
    copy.write_text(TEMPLATE.read_text(encoding="utf-8").replace('MODE = "context"', 'MODE = "deny"'), encoding="utf-8")
    return str(copy)


def said_before(tmp_path: Path, *after: str) -> Path:
    ctx = f"{TAG} a route path is a plural kebab-case noun"
    return write_transcript(tmp_path / "s.jsonl", [*hook_said("PreToolUse", "Edit", ctx, ctx), *after])


def test_context_is_said_at_its_moment(home):
    r = run(str(TEMPLATE), edit("src/api/invoices.js"), home)
    assert r["rc"] == 0 and r["event"] == "PreToolUse" and r["ctx"].startswith(TAG)


def test_context_stays_silent_on_another_file(home):
    r = run(str(TEMPLATE), edit("src/ui/page.js"), home)
    assert r["rc"] == 0 and r["stdout"] == ""


def test_context_is_said_once_until_a_compaction(home, tmp_path):
    assert run(str(TEMPLATE), edit("src/api/x.js", said_before(tmp_path)), home)["stdout"] == ""
    assert run(str(TEMPLATE), edit("src/api/x.js", said_before(tmp_path, COMPACT)), home)["ctx"].startswith(TAG)


def test_a_worker_is_checked_against_its_own_transcript(home, tmp_path):
    parent = said_before(tmp_path)
    payload = {**edit("src/api/x.js", parent), "agent_id": "w1"}
    assert run(str(TEMPLATE), payload, home)["ctx"].startswith(TAG), "the parent's block is not in the worker's window"


def test_a_programs_session_gets_no_context_unless_it_opts_in(home):
    assert run(str(TEMPLATE), edit("src/api/x.js"), home, entrypoint="sdk-cli")["stdout"] == ""
    opted = run(str(TEMPLATE), edit("src/api/x.js"), home, entrypoint="sdk-cli", EVAL_MY_PLUGIN_CAPTURE="1")
    assert opted["ctx"].startswith(TAG)


def test_context_fails_quiet(home):
    r = run(str(TEMPLATE), "not json", home)
    assert r["rc"] == 0 and r["stdout"] == "" and TAG in r["stderr"]


def test_deny_mode_denies_in_every_session(home, tmp_path):
    r = run(deny_mode(tmp_path), edit("src/api/x.js"), home, entrypoint="sdk-cli")
    hso = r["out"]["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny" and hso["permissionDecisionReason"].startswith(TAG)


def test_deny_mode_denies_when_it_cannot_check(home, tmp_path):
    r = run(deny_mode(tmp_path), "not json", home)
    assert r["out"]["hookSpecificOutput"]["permissionDecision"] == "deny"
