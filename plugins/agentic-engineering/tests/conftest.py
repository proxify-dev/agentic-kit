"""Run a hook the way Claude Code runs it (plugins/HOOKS.md §5), and write the transcript it reads.

run(): payload on stdin, CLAUDE_PLUGIN_ROOT set, through `uv run` as hooks.json starts it, in a throwaway HOME with uv pointed
at its real cache and Pythons (the contract test's run(), plugins/memory/tests/
test_session_start_contract.py). Transcript records take the shapes Claude Code writes (read off real
transcripts, 2026-09-27): each hook's context twice, as `hook_success` stdout and as
`hook_additional_context`; a compaction as a `compact_boundary` system record; a worker's calls in
<session>/subagents/agent-<id>.jsonl; a PreToolUse deny only as the call's error result (deny_record).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
# how hooks.json starts a hook: uv picks a 3.10+ Python, never PATH's python3 (plugins/HOOKS.md §1)
UV_RUN = ["uv", "run", "--quiet", "--no-project", "--python", ">=3.10"]
sys.path.insert(0, str(PLUGIN / "hooks"))

from _brand import env_name  # noqa: E402

KIT = ("context-engineering", "harness-engineering", "skill-development", "evals")

UV_ENV = {k: subprocess.run(["uv", *cmd], capture_output=True, text=True).stdout.strip()
          for k, cmd in (("UV_CACHE_DIR", ["cache", "dir"]), ("UV_PYTHON_INSTALL_DIR", ["python", "dir"]))}
SCRUB = (env_name("PATH"), env_name("STORE"), env_name("CODE"), env_name("CAPTURE"), "EVAL_" + env_name("CAPTURE"),
         env_name("CAPTURE_DISABLE"), "CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_DATA", "CLAUDE_CODE_ENTRYPOINT")


def run(hook: str, payload, home: Path, entrypoint: str = "cli", cwd: Path | None = None,
        args: tuple[str, ...] = (), **env_extra: str) -> dict:
    """One hook run. `payload` is a dict, or a raw string for a malformed one; `args` follow the script
    as a hooks.json command passes them."""
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    env.update(HOME=str(home), CLAUDE_PLUGIN_ROOT=str(PLUGIN), CLAUDE_CODE_ENTRYPOINT=entrypoint, **UV_ENV, **env_extra)
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    p = subprocess.run([*UV_RUN, str(PLUGIN / "hooks" / hook), *args], input=stdin,
                       capture_output=True, text=True, encoding="utf-8", cwd=cwd or home, env=env, timeout=60)
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    hso = out.get("hookSpecificOutput") or {}
    return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "out": out,
            "event": hso.get("hookEventName"), "ctx": hso.get("additionalContext") or "",
            "msg": out.get("systemMessage") or ""}


def _line(record: dict) -> str:
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def hook_said(event: str, tool: str, stdout: str, ctx: str, agent: str = "") -> list[str]:
    """What Claude Code records when a hook's context reaches the model: both records."""
    extra = {"agentId": agent} if agent else {}
    name = f"{event}:{tool}"
    return [
        _line({"isSidechain": bool(agent), **extra, "type": "attachment", "attachment": {
            "type": "hook_success", "hookName": name, "hookEvent": event, "content": "", "stdout": stdout,
            "stderr": "", "exitCode": 0, "command": 'uv run --quiet --no-project --python ">=3.10" "${CLAUDE_PLUGIN_ROOT}/hooks/on_edit.py"'}}),
        _line({"isSidechain": bool(agent), **extra, "type": "attachment", "attachment": {
            "type": "hook_additional_context", "content": [ctx], "hookName": name, "hookEvent": event}}),
    ]


def tool_result(text: str) -> str:
    """The agent read a file: its text is a tool result, not a hook's context."""
    return _line({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_01", "content": text}]}, "toolUseResult": {"file": {"content": text}}})


def deny_record(tool: str, reason: str, agent: str = "") -> str:
    """What Claude Code records when a PreToolUse hook denies a call: no hook record, only the call's
    error result, in the calling agent's own transcript. Keys and order as read off real transcripts
    (2.1.278-2.1.283, a main session's and a workflow worker's)."""
    text = f"PreToolUse:{tool} hook error: {reason}"
    extra = {"agentId": agent} if agent else {}
    return _line({"parentUuid": "af83eada", "isSidechain": bool(agent), "promptId": "e22e0d21", **extra,
                  "type": "user", "message": {"role": "user", "content": [
                      {"type": "tool_result", "content": text, "is_error": True, "tool_use_id": "toolu_01MZk"}]},
                  "uuid": "daafd72d", "timestamp": "2026-09-27T23:06:56.596Z", "toolUseResult": "Error: " + text,
                  "toolDenialKind": "permission-rule", "sourceToolAssistantUUID": "af83eada", "session_id": "s",
                  "userType": "external", "entrypoint": "cli", "cwd": "/r", "sessionId": "s", "version": "2.1.283",
                  "gitBranch": "main"})


COMPACT = _line({"parentUuid": None, "isSidechain": False, "type": "system", "subtype": "compact_boundary",
                 "content": "Conversation compacted", "level": "info"})
PROMPT = _line({"type": "user", "message": {"role": "user", "content": "go on"}})


def write_transcript(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def snapshot(root: Path) -> dict:
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def home(tmp_path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def world(tmp_path) -> Path:
    """A repo holding a plugin (a folder with .claude-plugin/) and a project's .claude/ folder."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    plugin = repo / "plugins" / "demo"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "demo"}', encoding="utf-8")
    (plugin / "hooks").mkdir()
    (plugin / "hooks" / "hooks.json").write_text('{"hooks": {}}', encoding="utf-8")
    (repo / ".claude").mkdir()
    (repo / "src" / "hooks").mkdir(parents=True)
    for existing in ("plugins/demo/skills/old/SKILL.md", "plugins/demo/agents/old.md", ".claude/commands/old.md"):
        (repo / existing).parent.mkdir(parents=True, exist_ok=True)
        (repo / existing).write_text("x\n", encoding="utf-8")
    return repo


def git(*args: str, cwd: Path) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def checkout(tmp_path) -> tuple[Path, Path]:
    """(the main checkout, on its default branch `main`; a linked worktree of it, on `feature`)."""
    main, linked = tmp_path / "main", tmp_path / "linked"
    main.mkdir()
    git("init", "-q", "-b", "main", cwd=main)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init", cwd=main)
    git("branch", "feature", cwd=main)
    git("branch", "other", cwd=main)
    git("worktree", "add", "-q", str(linked), "feature", cwd=main)
    return main, linked
