"""`ak plugin`'s fake Claude Code home (CLAUDE_CONFIG_DIR): two plugins, one settings.json, one transcript.

The shapes are the ones Claude Code writes (read off real transcripts, 2026-09-27):

    alpha   SessionStart hook shared, byte for byte, with beta (`hooks/py hooks/start.py`),
            a PreToolUse:Write gate whose script is missing, and a folded-description skill
    beta    the same SessionStart command; its text opens with a tag built at runtime
    settings.json   a hook pointing at a deleted file (the stale `plugins/ak/` case)

The transcript is one session in miniature: context at start, a prompt
written twice, a Write blocked by the missing gate, a write-lint's stderr after an Edit,
a message only the person sees.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SID = "abcd1234-0000-4000-8000-000000000001"
START = '"${CLAUDE_PLUGIN_ROOT}/hooks/py" "${CLAUDE_PLUGIN_ROOT}/hooks/start.py"'
GATE = '"${CLAUDE_PLUGIN_ROOT}/hooks/py" "${CLAUDE_PLUGIN_ROOT}/hooks/gate.py"'
LINT = '"${CLAUDE_PLUGIN_ROOT}/hooks/py" "${CLAUDE_PLUGIN_ROOT}/hooks/lint.py"'


def _plugin(root: Path, hooks: dict, files: dict[str, str]) -> None:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": root.parent.name}), encoding="utf-8")
    (root / "hooks").mkdir()
    (root / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")


def _cmd(event, command, matcher=""):
    return {event: [{"matcher": matcher, "hooks": [{"type": "command", "command": command}]}]}


def hook(sec, event, name, command, stdout, ms=100):
    return {"type": "attachment", "timestamp": f"2026-09-27T11:00:{sec:02d}.000Z", "sessionId": SID,
            "attachment": {"type": "hook_success", "hookName": name, "hookEvent": event,
                           "command": command, "stdout": stdout, "stderr": "", "exitCode": 0,
                           "durationMs": ms}}


def ctx(event, text):
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def prompt(sec, text):
    return {"type": "user", "timestamp": f"2026-09-27T11:00:{sec:02d}.000Z",
            "message": {"role": "user", "content": text}}


def transcript() -> list[dict]:
    return [
        hook(1, "SessionStart", "SessionStart:startup", START, ctx("SessionStart", "[alpha] hello there, one move each"), 200),
        hook(1, "SessionStart", "SessionStart:startup", START, ctx("SessionStart", "[beta] recent context, 2026-09-27"), 300),
        hook(1, "SessionStart", "SessionStart:startup", START, json.dumps({"systemMessage": "\x1b[35mbeta:\x1b[0m 3 open"})),
        # what the model saw, merged: already counted from the runs above, never twice
        {"type": "attachment", "attachment": {"type": "hook_additional_context", "content": ["[alpha] hello there"]}},
        prompt(10, "why was my write refused?"),
        prompt(10, "why was my write refused?"),  # Claude Code can write a prompt twice
        {"type": "user", "timestamp": "2026-09-27T11:00:20.000Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
             "content": f"PreToolUse:Write hook error: [{GATE}]: /opt/py/Python: can't open file 'gate.py'"}]}},
        {"type": "attachment", "timestamp": "2026-09-27T11:00:30.000Z", "attachment": {
            "type": "hook_blocking_error", "hookName": "PostToolUse:Edit", "hookEvent": "PostToolUse",
            "blockingError": {"blockingError": "[alpha] lint: name must be kebab-case", "command": LINT}}},
        hook(31, "PostToolUse", "PostToolUse:Edit", LINT, ""),
        prompt(40, "thanks"),
        {"type": "assistant", "timestamp": "2026-09-27T11:00:41.000Z", "message": {"content": []}},
    ]


# ── Claude Code 2.1.300+: the same story, with every model call saved ──────────────────────────────────
# Each row carries the version that wrote it; hook text goes out as a `system` message (a saved body,
# api-request-blob), PostToolUse text inside the tool result; an api-request names the bodies it sent.
RECORD_SID = "7c3d0e11-0000-4000-8000-000000000002"
V = "2.1.300"


def stamped(row: dict, version: str = V, sid: str = RECORD_SID) -> dict:
    return {**row, "sessionId": sid, "version": version}


def attached(sec, event, parts, version=V, sid=RECORD_SID):
    return {"parentUuid": None, "isSidechain": False,
            "attachment": {"type": "hook_additional_context", "content": parts, "hookName": event,
                           "toolUseID": event, "hookEvent": event},
            "type": "attachment", "uuid": f"att-{sec}", "timestamp": f"2026-10-15T11:00:{sec:02d}.000Z",
            "sessionId": sid, "version": version}


def blob(h, role, content, sid=RECORD_SID):
    return {"type": "api-request-blob", "sessionId": sid, "hash": h, "message": {"role": role, "content": content}}


def request(sec, rid, tail, keep=0, version=V, sid=RECORD_SID):
    return {"type": "api-request", "sessionId": sid, "id": rid, "timestamp": f"2026-10-15T11:00:{sec:02d}.500Z",
            "version": version, "querySource": "repl_main_thread", "shapeHash": "5" * 64,
            "params": {"model": "claude-opus-5-5"}, "keep": keep, "tail": tail, "cacheControl": [],
            "requestDigest": "d" * 64}


def record_transcript() -> list[dict]:
    """A 2.1.300 session: two hooks at start and one text no hook printed, a lint after an Edit, and a
    second lint after the last call (its text never reached a call on record)."""
    alpha, beta, again = ("[alpha] hello there, one move each", "[beta] recent context, 2026-10-15",
                          "[alpha] hello there, again")
    return [
        stamped(hook(1, "SessionStart", "SessionStart:startup", START, ctx("SessionStart", alpha), 200)),
        stamped(hook(1, "SessionStart", "SessionStart:startup", START, ctx("SessionStart", beta), 300)),
        attached(1, "SessionStart", [alpha, beta, again, "[gamma] nobody printed this"]),
        stamped(prompt(10, "fix the redirect")),
        {"type": "api-request-shape", "sessionId": RECORD_SID, "shapeHash": "5" * 64,
         "timestamp": "2026-10-15T11:00:10.400Z", "version": V,
         "shape": {"system": [{"type": "text", "text": "You are Claude Code."}], "tools": []}},
        # gamma's text is not in what was sent
        blob("h1", "system", [{"type": "text", "text": f"SessionStart hook additional context: {alpha}\n{beta}\n{again}"}]),
        blob("h2", "user", "fix the redirect"),
        request(10, "r1", ["h1", "h2"]),
        stamped(hook(31, "PostToolUse", "PostToolUse:Edit", LINT, ctx("PostToolUse", "[alpha] lint: ok"))),
        attached(31, "PostToolUse", ["[alpha] lint: ok"]),
        blob("h3", "user", [{"type": "tool_result", "tool_use_id": "t1", "content": [
            {"type": "text", "text": "edited"}, {"type": "text", "text": "PostToolUse:Edit hook additional context: [alpha] lint: ok"}]}]),
        request(32, "r2", ["h3"], keep=2),
        stamped(prompt(40, "thanks")),
        stamped(hook(41, "PostToolUse", "PostToolUse:Edit", LINT, ctx("PostToolUse", "[alpha] lint: again"))),
        attached(41, "PostToolUse", ["[alpha] lint: again"]),
    ]


def mixed_transcript() -> list[dict]:
    """A session started on 2.1.299 and resumed on 2.1.300: each row is read by the version that wrote it."""
    old = "[alpha] hello there, one move each"
    return [
        stamped(hook(1, "SessionStart", "SessionStart:startup", START, ctx("SessionStart", old)), "2.1.299"),
        attached(1, "SessionStart", [old], version="2.1.299"),
        stamped(prompt(10, "before the upgrade"), "2.1.299"),
        stamped(prompt(20, "after it")),
        stamped(hook(21, "UserPromptSubmit", "UserPromptSubmit", START, ctx("UserPromptSubmit", "[beta] recent context, 2026-10-15"))),
        attached(21, "UserPromptSubmit", ["[beta] recent context, 2026-10-15"]),
        # the first call after the upgrade re-sends the whole conversation, the old start text included
        blob("o1", "system", [{"type": "text", "text": f"SessionStart hook additional context: {old}"}]),
        blob("n1", "system", [{"type": "text", "text": "UserPromptSubmit hook additional context: [beta] recent context, 2026-10-15"}]),
        request(21, "r1", ["o1", "n1"]),
    ]


def write(claude: Path, sid: str, rows: list[dict]) -> Path:
    """A transcript as Claude Code writes it: compact JSON, one row a line."""
    p = claude / "projects" / "-tmp-proj" / f"{sid}.jsonl"
    p.write_text("\n".join(json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    return p


@pytest.fixture
def home(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    cache = claude / "plugins" / "cache" / "mk"
    alpha, beta = cache / "alpha" / "1.0.0", cache / "beta" / "2.0.0"
    _plugin(alpha, {**_cmd("SessionStart", START), **_cmd("PreToolUse", GATE, "Write"),
                    **_cmd("PostToolUse", LINT, "Edit|Write")},
            {"hooks/py": "#!/bin/sh\n", "hooks/start.py": 'print("[alpha] hello there, one move each")\n',
             "hooks/lint.py": "# lint\n",
             "skills/sweep/SKILL.md": "---\nname: sweep\ndescription: >-\n  Sweep the vault,\n  twice.\n---\nbody\n"})
    _plugin(beta, _cmd("SessionStart", START),
            {"hooks/py": "#!/bin/sh\n", "hooks/start.py": 'print(f"[{NAME}] recent context, {day}")\n'})
    (claude / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "alpha@mk": [{"scope": "user", "installPath": str(alpha), "version": "1.0.0"}],
        "beta@mk": [{"scope": "user", "installPath": str(beta), "version": "2.0.0"}],
        "alpha@old": [{"scope": "user", "installPath": str(alpha), "version": "0.9.0"}],
    }}), encoding="utf-8")
    (claude / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"alpha@mk": True, "beta@mk": True, "alpha@old": False},
        "hooks": _cmd("Stop", 'python3 "/nowhere/plugins/ak/hooks/stop.py"')}), encoding="utf-8")
    proj = claude / "projects" / "-tmp-proj"
    proj.mkdir(parents=True)
    (proj / f"{SID}.jsonl").write_text("\n".join(json.dumps(r) for r in transcript()) + "\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.chdir(tmp_path)
    return claude
