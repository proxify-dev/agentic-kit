"""Which rows of a Claude Code session transcript are chat, and which Claude Code version wrote a row.

A transcript (`<claude home>/projects/<project>/<session>.jsonl`, and its `subagents/*.jsonl`) is one JSON row
per line. What it holds changes with Claude Code's version, so a reader asks two questions of each row:
is it chat (`type` user or assistant, never judged by `message.role`), and does the version that wrote it
have a feature (`has("request_record", row)`). The version is the row's own top-level `version`: a session
resumed after an upgrade holds rows of both versions, and ~9,000 transcripts on one machine (2026-10-15)
were written before the newest feature here.

FEATURES says, per feature, the first version that has it. A Claude Code change a reader must follow is
one entry there and one branch in that reader; rows from older versions keep the path they always had.

    request_record   2.1.300 saves every model call it sends, as three row types: `api-request-shape`
                     (system prompt and tools, once per conversation), `api-request` (one per call:
                     settings, the messages kept and added, by hash, a sha256 of the whole request) and
                     `api-request-blob` (a message body, when what was sent differs from the chat rows).
                     A blob has `message.role` and no version, timestamp or uuid: a reader that picks rows
                     by `message.role` reads every such body a second time.

KNOWN_ROW_TYPES is every row type seen over 18,150 transcripts on one machine (2026-10-15). `ak setup`
warns when a recent transcript holds one that is not in it: the day Claude Code adds a row type, before
a reader trips on it.

Canonical here (the ak package). Copies ride in the other plugins' hooks/ folders, written and checked
by scripts/copies.py — edit this file, then `uv run python scripts/copies.py render`.
"""
from __future__ import annotations

import json
import re
from typing import Dict, Optional, Tuple, Union

FEATURES: Dict[str, Tuple[int, int, int]] = {
    "request_record": (2, 1, 300),
}

CHAT_TYPES = frozenset({"user", "assistant"})
RECORD_TYPES = frozenset({"api-request-shape", "api-request", "api-request-blob"})
KNOWN_ROW_TYPES = frozenset({
    "user", "assistant", "attachment", "system", "summary",
    "queue-operation", "last-prompt", "atis-latch", "ai-title", "mode", "permission-mode",
    "file-history-snapshot", "file-history-delta", "cost-state", "agent-setting", "agent-name",
    "custom-title", "worktree-state", "relocated", "pr-link", "frame-link", "continued-in",
    "fork-context-ref", "artifact-comment-monitor", "artifact-autoreact-ledger",
}) | RECORD_TYPES

_VERSION = re.compile(r"\s*(\d+)\.(\d+)\.(\d+)")
# A metadata or record row starts with its type. A chat, attachment or system row starts with parentUuid, and
# its own type comes right before one of these keys; a nested type (`hook_success`, `text`) never does.
# Over 127,974 lines (2026-10-15) these two answered every line, and agreed with a full parse on each.
_LEADING_TYPE = re.compile(r'\{"type":"([^"]+)"')
_ROW_TYPE = re.compile(r'"type":"([a-z][a-z-]*)","(?:uuid|message|subtype|isMeta|timestamp)"')
# The version on a chat row sits right after its sessionId; a bare `"version":"` also matches nested ones
# (an MCP server's, a package's).
_ROW_VERSION = re.compile(r'"sessionId":"[^"]*","version":"([^"]+)"')


def parse(version: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """(2, 1, 300) from "2.1.300", "2.1.300 (Claude Code)" or "2.1.300-dev.…"; None for anything else."""
    m = _VERSION.match(version) if isinstance(version, str) else None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def at_least(version: Optional[str], first: Tuple[int, int, int]) -> bool:
    v = parse(version)
    return v is not None and v >= first


def has(feature: str, row: Union[dict, str, None]) -> bool:
    """Whether the Claude Code that wrote ROW (a row, or a version string) has FEATURE. No version → False."""
    first = FEATURES[feature]
    version = row.get("version") if isinstance(row, dict) else row
    return at_least(version, first)


def is_chat_row(row: dict) -> bool:
    """A user or assistant row of the conversation: by its type, so a saved request body is never one."""
    return isinstance(row, dict) and row.get("type") in CHAT_TYPES


def row_type(line: Union[str, bytes]) -> str:
    """The row's own type, read cheaply off the line; "" when the line is not a row."""
    text = line if isinstance(line, str) else bytes(line).decode("utf-8", "replace")
    m = _LEADING_TYPE.match(text) or _ROW_TYPE.search(text)
    if m:
        return m.group(1)
    try:
        row = json.loads(text)
    except ValueError:
        return ""
    t = row.get("type") if isinstance(row, dict) else None
    return t if isinstance(t, str) else ""


def unknown_types(path, known: frozenset = KNOWN_ROW_TYPES) -> Dict[str, str]:
    """Each row type in one transcript that KNOWN does not hold, with the Claude Code version that wrote it."""
    found: Dict[str, str] = {}
    last = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                t = row_type(line)
                if not t:
                    continue
                v = _ROW_VERSION.search(line) if t in CHAT_TYPES else None
                if v:
                    last = v.group(1)
                if t in known or t in found:
                    continue
                try:
                    own = json.loads(line).get("version")
                except (ValueError, AttributeError):
                    own = None
                found[t] = own if isinstance(own, str) and own else last
    except OSError:
        return {}
    return found
