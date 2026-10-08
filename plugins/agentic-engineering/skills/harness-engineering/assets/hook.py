"""A hook template that handles the common failures. Copy it into your plugin's hooks/ folder and change the three marked parts.

Start it from hooks.json with one interpreter and no shell (references/hooks.md):

  "command": "uv run --quiet --no-project --python \\">=3.10\\" \\"${CLAUDE_PLUGIN_ROOT}/hooks/<name>.py\\""

What it already does:
- reads the event's JSON on stdin; standard library only
- with MODE = "context": injects BLOCK as additionalContext (the model reads it, the person does not), once per
  agent until the next compaction, checked in that agent's own transcript (a subagent's is its own file);
  skips a program's session (claude -p, the SDK, CI) unless OPT_IN is 1; when it errors, it exits 0 and
  prints one stderr line
- with MODE = "deny" (PreToolUse only): denies every matching call with BLOCK as the reason, in every session;
  when it errors, it denies the call

Test it the way Claude Code runs it:

  echo '{"hook_event_name":"PreToolUse","tool_name":"Edit","tool_input":{"file_path":"src/api/x.js"}}' \\
    | CLAUDE_PLUGIN_ROOT=. uv run --quiet --no-project --python ">=3.10" hooks/<name>.py
"""
from __future__ import annotations

import json
import mmap
import os
import re
import sys
from pathlib import Path

# 1. The text: only what the agent reads, starting with a tag of its own (plain ASCII, no quote or backslash).
#    With MODE = "deny", it is the reason the call was refused: name the command to type instead.
BLOCK = "[my-plugin: api] a route path is a plural kebab-case noun (/line-items); JSON fields are snake_case."

# 2. "context" injects the text before the tool call; "deny" denies the tool call.
MODE = "context"

# 3. The variable that gives a program's session the text; claude plugin eval passes only EVAL_* to a case.
OPT_IN = "EVAL_MY_PLUGIN_CAPTURE"


def applies(event: dict) -> bool:
    """When the hook applies: True when this tool call is the one the text is about. Change this."""
    path = (event.get("tool_input") or {}).get("file_path") or ""
    return "/src/api/" in "/" + path.replace("\\", "/")


HUMAN_ENTRYPOINTS = frozenset({"cli", "claude-desktop", "sdk-ts"})
COMPACT = b'"subtype":"compact_boundary"'
HOOK_RECORDS = (b'"hook_additional_context"', b'"hook_success"')


def tag(block: str) -> str:
    first = block.lstrip().splitlines()[0] if block.strip() else ""
    m = re.match(r"\[[^\]\n]+\]", first)
    return m.group() if m else first


def program_session() -> bool:
    """A program started this session (claude -p, the SDK), and it has not opted in. Unset counts as a person."""
    if os.environ.get(OPT_IN) == "1":
        return False
    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    return bool(entrypoint) and entrypoint not in HUMAN_ENTRYPOINTS


def transcript(event: dict) -> Path | None:
    """The transcript of the agent making this call. A subagent's call carries agent_id and the main session's
    transcript_path; its own file sits under <session>/subagents/, a workflow agent's one folder deeper."""
    tp = event.get("transcript_path")
    if not tp:
        return None
    path = Path(tp)
    agent = event.get("agent_id")
    if not agent or path.name == f"agent-{agent}.jsonl":
        return path
    subagents = path.with_suffix("") / "subagents"
    direct = subagents / f"agent-{agent}.jsonl"
    if direct.is_file():
        return direct
    return next(subagents.glob(f"**/agent-{agent}.jsonl"), direct)


def said(path: Path | None, line: str) -> bool:
    """True when a hook record in this transcript carries `line` since the last compaction. A read, nothing written;
    the transcript is written asynchronously, so two quick calls may both inject the text."""
    needle = json.dumps(line, ensure_ascii=False)[1:-1].encode("utf-8")
    if not path or not needle:
        return False
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            i = mm.find(needle, mm.rfind(COMPACT) + 1)
            while i != -1:
                start, end = mm.rfind(b"\n", 0, i) + 1, mm.find(b"\n", i)
                end = len(mm) if end == -1 else end
                record = mm[start:end]
                if b'"attachment"' in record and any(k in record for k in HOOK_RECORDS):
                    return True
                i = mm.find(needle, end)
    except (OSError, ValueError):  # missing, unreadable, or empty
        return False
    return False


def deny(reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))
    return 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if not applies(event):
            return 0
        if MODE == "deny":
            return deny(BLOCK)
        if program_session() or said(transcript(event), tag(BLOCK)):
            return 0
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event.get("hook_event_name") or "PreToolUse", "additionalContext": BLOCK}}))
        return 0
    except Exception as e:  # a hook must never crash the session
        problem = f"{type(e).__name__}: {e}"
        if MODE == "deny":
            return deny(f"{tag(BLOCK)} could not check this call ({problem}), so it is refused until the hook works.")
        print(f"{tag(BLOCK)} skipped: {problem}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
