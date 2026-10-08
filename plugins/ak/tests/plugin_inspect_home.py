"""`ak plugin`'s fake Claude Code home (CLAUDE_CONFIG_DIR): two plugins, one settings.json, one transcript.

The shapes are the ones Claude Code writes (read off real transcripts, 2026-09-27):

    alpha   SessionStart hook shared, byte for byte, with beta (`hooks/py hooks/start.py`),
            a PreToolUse:Write gate whose script is missing, and a folded-description skill
    beta    the same SessionStart command; its text opens with a tag built at runtime
    settings.json   a hook pointing at a deleted file (the stale `plugins/ak/` case)

The transcript is session 1a2f93b5's story in miniature: context at start, a prompt
written twice, a Write blocked by the missing gate, a write-lint's stderr after an Edit,
a message only the person sees.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SID = "1a2f93b5-0000-4000-8000-000000000001"
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
