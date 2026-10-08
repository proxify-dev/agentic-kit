"""JSONL conversation parser and tool extractor."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from ._brand import posix
from .catalog import Catalog, machine_catalog, session_listings
from .models import (
    AgentSpawn,
    ErrorSignal,
    FileTouch,
    InteractionRecord,
    PluginCredit,
    SkillInvocation,
    ToolCall,
)
from .turns import build_turn, turn_starts

# Bumped whenever segmentation or a derived column changes. The manifest records the
# version each session was indexed with, and a lower one counts as stale — so the next
# index pass re-derives every transcript still on disk instead of mixing two turn
# numberings. 2: interrupts, local commands and compaction summaries no longer open a
# turn; `ending` and `local_commands` columns. 3: NotebookEdit and MultiEdit are file
# edits (i_files), so `tracer trace blame` sees them. 4: plugins credited from the machine's plugin
# catalog (MCP tools, skills, agents, file paths), with how sure; skills kept by name.
PARSER_VERSION = 4

# Tools that touch files, mapped to operation name
FILE_TOOLS = {
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "Glob": "glob",
    "Grep": "grep",
}

CORRECTION_PATTERNS = re.compile(
    r"^(no[,.\s]|stop |don't |wrong |that's not |not what I)",
    re.IGNORECASE,
)

# Plugin dirs to match in file paths - matches plugins/<plugin-name>/
# The plugin name is the directory directly under plugins/
PLUGIN_PATH_RE = re.compile(r"(?:^|/)plugins/([a-zA-Z][\w-]*)/")
# Also match <claude_home>/plugins/<name>/ for data paths (paths are matched in posix() form)
CLAUDE_PLUGIN_PATH_RE = re.compile(r"\.claude/plugins/(?:data/)?([a-zA-Z][\w-]*)/")


def parse_jsonl(path: Path) -> list[tuple[int, dict]]:
    """Parse a JSONL file in binary mode, returning (byte_offset, message_dict) tuples.

    Byte offsets are the exact position of the first byte of each JSONL record,
    so callers can later ``seek(byte_offset)`` on the same file and read the
    same record via a single ``readline()``.

    Malformed lines are skipped with a stderr warning.
    """
    messages: list[tuple[int, dict]] = []
    with open(path, "rb") as f:
        lineno = 0
        while True:
            offset = f.tell()
            raw = f.readline()
            if not raw:
                break
            lineno += 1
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                msg = json.loads(stripped.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                print(
                    f"  warn: skipping malformed line {lineno} in {path.name}",
                    file=sys.stderr,
                )
                continue
            messages.append((offset, msg))
    return messages


def is_real_user_message(msg: dict) -> bool:
    """True if this is a real user input (not a tool result or meta message)."""
    if msg.get("type") != "user":
        return False
    if msg.get("isMeta"):
        return False
    message = msg.get("message", {})
    if not isinstance(message, dict):
        return False
    content = message.get("content", "")
    # Tool results have list content with tool_result blocks
    if isinstance(content, list):
        return not any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in content
        )
    # String content = real user message
    return isinstance(content, str) and len(content.strip()) > 0


def extract_user_text(msg: dict) -> str:
    """Extract text content from a user message, tags stripped (turns.prompt_text)."""
    from .turns import prompt_text

    message = msg.get("message", {})
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return prompt_text(content)[:200]
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return prompt_text(block.get("text", ""))[:200]
    return ""


# How much of the final answer the index keeps per interaction. A listing wants
# a glimpse; a recap wants the whole thing, and gets it by seeking the source
# JSONL (query.resolve_assistant_full) rather than by widening this.
ASSISTANT_PREVIEW_CAP = 500


def extract_last_assistant_text(
    segment: list[dict], cap: int | None = ASSISTANT_PREVIEW_CAP
) -> str:
    """Return the last assistant text block in an interaction segment.

    ``cap=None`` returns it whole.
    """
    last = ""
    for msg in segment:
        if msg.get("type") != "assistant":
            continue
        message = msg.get("message", {})
        if not isinstance(message, dict):
            continue
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    last = text
    if not last:
        return ""
    return re.sub(r"<[^>]+>", "", last).strip()[:cap]


def extract_tool_calls(msg: dict) -> list[dict[str, Any]]:
    """Extract tool_use blocks from an assistant message."""
    message = msg.get("message", {})
    if not isinstance(message, dict):
        return []
    content = message.get("content", [])
    if not isinstance(content, list):
        return []
    calls = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            calls.append({
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "input": block.get("input", {}),
            })
    return calls


def extract_agent_spawn(tool_input: dict) -> AgentSpawn | None:
    """Extract agent spawn info from an Agent tool call input."""
    agent_type = tool_input.get("subagent_type", "")
    prompt = tool_input.get("prompt", "")
    if not agent_type and not prompt:
        return None
    return AgentSpawn(
        type=agent_type or "general-purpose",
        prompt_preview=prompt[:200] if prompt else "",
    )


def extract_skill_invocation(tool_input: dict) -> SkillInvocation | None:
    """Extract skill info from a Skill tool call input."""
    name = tool_input.get("skill", "")
    if not name:
        return None
    return SkillInvocation(name=name, args=tool_input.get("args", ""))


def extract_file_touch(tool_name: str, tool_input: dict) -> FileTouch | None:
    """Extract file path from file-related tool calls."""
    operation = FILE_TOOLS.get(tool_name)
    if not operation:
        return None
    path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "") or tool_input.get("path", "")
    pattern = tool_input.get("pattern", "")
    if path:
        return FileTouch(path=path, operation=operation)
    if pattern and operation in ("glob", "grep"):
        return FileTouch(path=pattern, operation=operation)
    return None


# What a failed tool result looks like, anchored at the start of a line. The
# previous check was ``"error" in text.lower()`` — which flagged a WebSearch
# result whose links mentioned "error handling", and counted ERR:4 on a session
# with zero failed tool calls. Substrings do not fail; lines do.
_ERROR_LINE_RE = re.compile(
    r"^\s*(?:"
    r"(?:[A-Za-z_.]*Error|[A-Za-z_.]*Exception|error|ERROR)\s*[:\-]"
    r"|Traceback \(most recent call last\)"
    r"|fatal:|panic:"
    r"|Exit code [1-9]\d*"
    r"|(?:zsh|bash|sh):.*?(?:command not found|permission denied|no such file or directory)"
    r")",
    re.M,
)


def _tool_result_text(block: dict) -> str:
    """The text of a tool_result block, whichever shape Claude Code wrote it in."""
    result_content = block.get("content", "")
    if isinstance(result_content, str):
        return result_content
    if isinstance(result_content, list):
        return "\n".join(
            sub.get("text", "")
            for sub in result_content
            if isinstance(sub, dict) and sub.get("text")
        )
    return ""


def detect_bash_error(msg: dict) -> ErrorSignal | None:
    """Check whether a user message carries a failed tool_result.

    Two signals, in order: the ``is_error`` flag Claude Code sets on the block,
    then an error-shaped line (``Error:``, a traceback, a non-zero exit code, a
    shell's own ``command not found``). A result that merely *mentions* errors
    — search hits, documentation, a diff — is not one.
    """
    message = msg.get("message", {})
    if not isinstance(message, dict):
        return None
    content = message.get("content", [])
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        text = _tool_result_text(block)
        if block.get("is_error") is True:
            return ErrorSignal(type="tool_error", preview=text[:200])
        if _ERROR_LINE_RE.search(text):
            return ErrorSignal(type="tool_error", preview=text[:200])
    return None


_RANK = {"exact": 0, "inferred": 1}


def credit_plugins(
    calls: list[dict],
    files: list[FileTouch],
    catalog: Catalog,
    skill_listing: frozenset[str] = frozenset(),
    agent_listing: frozenset[str] = frozenset(),
) -> list[PluginCredit]:
    """Which plugins an interaction used, and how sure each credit is.

    MCP tools, skills and agents are looked up in the machine's plugin catalog (catalog.py);
    a bare skill or agent name against what the session was offered. A file path under
    `plugins/<name>/` or `.claude/plugins/<name>/` credits <name> when the catalog knows
    it — the session worked on that plugin. Ambiguous and unattributed calls credit none.
    """
    best: dict[tuple[str, str], str] = {}

    def credit(plugin: str | None, via: str, how: str) -> None:
        if not plugin or how not in _RANK:
            return
        key = (plugin, via)
        if key not in best or _RANK[how] < _RANK[best[key]]:
            best[key] = how

    for call in calls:
        name, inp = call["name"], call["input"] if isinstance(call["input"], dict) else {}
        if name.startswith("mcp__"):
            a = catalog.mcp(name)
            credit(a.plugin, "mcp", a.how)
        elif name == "Skill" and inp.get("skill"):
            a = catalog.skill(str(inp["skill"]), skill_listing)
            credit(a.plugin, "skill", a.how)
        elif name in ("Agent", "Task") and inp.get("subagent_type"):
            a = catalog.agent(str(inp["subagent_type"]), agent_listing)
            credit(a.plugin, "agent", a.how)
    for ft in files:
        if ft.operation in ("glob", "grep"):
            continue  # patterns, not files
        for regex in (PLUGIN_PATH_RE, CLAUDE_PLUGIN_PATH_RE):
            m = regex.search(posix(ft.path))
            if m and m.group(1) in catalog.plugins:
                credit(m.group(1), "file", "exact")
    return [PluginCredit(p, via, how) for (p, via), how in sorted(best.items())]


def segment_interactions(
    offset_messages: list[tuple[int, dict]],
    session_id: str,
    session_file: str = "",
    catalog: Catalog | None = None,
) -> list[InteractionRecord]:
    """Segment a conversation into interaction records.

    ``offset_messages`` is the output of :func:`parse_jsonl` — a list of
    ``(byte_offset, message_dict)`` pairs in JSONL order. ``session_file`` is
    the absolute path of the source JSONL; it is recorded on every produced
    record so the drill (``tracer trace turn``) can later ``seek(byte_offset)`` to retrieve
    full content without re-scanning the file. ``catalog``: the plugins to credit
    against (default: this machine's, catalog.machine_catalog()).
    """
    interactions: list[InteractionRecord] = []
    sequence = 0

    # Flatten to raw message list for the rest of the logic; keep offsets aligned by index.
    messages = [m for _, m in offset_messages]
    offsets = [o for o, _ in offset_messages]
    catalog = catalog if catalog is not None else machine_catalog()
    skill_listing, agent_listing = session_listings(messages)

    # Collect metadata from first message
    git_branch = ""
    for msg in messages:
        if msg.get("gitBranch"):
            git_branch = msg["gitBranch"]
            break

    # Find interaction boundaries: prompts the user typed. Interrupts, local commands
    # and compaction summaries ride inside the turn they follow (turns.py says why).
    boundaries = turn_starts(messages)

    if not boundaries:
        return interactions

    for bi, start_idx in enumerate(boundaries):
        end_idx = boundaries[bi + 1] if bi + 1 < len(boundaries) else len(messages)
        segment = messages[start_idx:end_idx]

        user_msg = segment[0]
        user_text = extract_user_text(user_msg)
        timestamp = user_msg.get("timestamp", "")
        byte_offset = offsets[start_idx]

        agents: list[AgentSpawn] = []
        skills: list[SkillInvocation] = []
        calls: list[dict] = []
        tool_counts: dict[str, int] = {}
        files: list[FileTouch] = []
        errors: list[ErrorSignal] = []

        for msg in segment:
            if msg.get("type") == "assistant":
                for call in extract_tool_calls(msg):
                    name = call["name"]
                    inp = call["input"]
                    tool_counts[name] = tool_counts.get(name, 0) + 1
                    calls.append(call)

                    if name == "Agent":
                        spawn = extract_agent_spawn(inp)
                        if spawn:
                            agents.append(spawn)
                    elif name == "Skill":
                        skill = extract_skill_invocation(inp)
                        if skill:
                            skill.plugin = catalog.skill(skill.name, skill_listing).plugin or ""
                            skills.append(skill)

                    ft = extract_file_touch(name, inp)
                    if ft:
                        files.append(ft)

            elif msg.get("type") == "user":
                err = detect_bash_error(msg)
                if err:
                    errors.append(err)

        # Detect user correction in the NEXT interaction's user message
        if bi + 1 < len(boundaries):
            next_user = messages[boundaries[bi + 1]]
            next_text = extract_user_text(next_user)
            if CORRECTION_PATTERNS.match(next_text):
                errors.append(ErrorSignal(type="user_correction", preview=next_text[:200]))

        turn = build_turn(list(enumerate(segment)))

        # Duration: prompt to the last thing the model or a tool did — not to a
        # local command the user ran afterwards, which rides in the same segment.
        duration = 0
        last_ts = turn.end_ts
        if timestamp and last_ts:
            try:
                from datetime import datetime
                t0 = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                duration = max(0, int((t1 - t0).total_seconds()))
            except (ValueError, TypeError):
                pass

        # Token estimate: rough heuristic based on message count and tool calls
        msg_count = len(segment)
        tokens_estimated = msg_count * 500 + sum(tool_counts.values()) * 1000

        credits = credit_plugins(calls, files, catalog, skill_listing, agent_listing)
        plugins = sorted({c.plugin for c in credits})

        tools = [ToolCall(name=n, count=c) for n, c in sorted(tool_counts.items())]
        assistant_reply = extract_last_assistant_text(segment)

        interactions.append(InteractionRecord(
            id=f"{session_id}-{sequence:03d}",
            session_id=session_id,
            timestamp=timestamp,
            session_file=session_file,
            byte_offset=byte_offset,
            git_branch=git_branch,
            user_message_preview=user_text,
            assistant_reply_preview=assistant_reply,
            plugins=plugins,
            plugin_credits=credits,
            agents_spawned=agents,
            skills_invoked=skills,
            tools_called=tools,
            files_touched=files,
            error_signals=errors,
            tokens_estimated=tokens_estimated,
            duration_seconds=duration,
            ending=turn.ending,
            local_commands=[c.line() for c in turn.local_commands],
        ))
        sequence += 1

    return interactions
