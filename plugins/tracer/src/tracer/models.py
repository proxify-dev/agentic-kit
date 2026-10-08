"""Interaction record data model."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional
import json


@dataclass
class AgentSpawn:
    type: str
    prompt_preview: str = ""


@dataclass
class SkillInvocation:
    name: str
    args: str = ""
    plugin: str = ""  # the plugin it resolved to (catalog.Catalog.skill), "" when none


@dataclass
class PluginCredit:
    """One reason an interaction is credited to a plugin: which of its parts was used
    (mcp · skill · agent · file) and how sure the attribution is (catalog.Attribution.how)."""
    plugin: str
    via: str
    how: str


@dataclass
class ToolCall:
    name: str
    count: int = 1


@dataclass
class FileTouch:
    path: str
    operation: str  # read, write, edit, glob, grep


@dataclass
class ErrorSignal:
    type: str  # tool_error, user_correction
    preview: str = ""


@dataclass
class InteractionRecord:
    id: str  # session_id-sequence_number
    session_id: str
    timestamp: str  # ISO 8601
    session_file: str = ""  # absolute path to source JSONL
    byte_offset: int = 0  # byte offset of first message of this interaction
    git_branch: str = ""
    user_message_preview: str = ""
    assistant_reply_preview: str = ""
    plugins: list[str] = field(default_factory=list)
    plugin_credits: list[PluginCredit] = field(default_factory=list)
    agents_spawned: list[AgentSpawn] = field(default_factory=list)
    skills_invoked: list[SkillInvocation] = field(default_factory=list)
    tools_called: list[ToolCall] = field(default_factory=list)
    files_touched: list[FileTouch] = field(default_factory=list)
    error_signals: list[ErrorSignal] = field(default_factory=list)
    intent: Optional[str] = None
    outcome: Optional[str] = None
    tokens_estimated: int = 0
    duration_seconds: int = 0
    # How the turn ended (turns.Turn.ending): answered · interrupted:<N> · tool:<N> · empty;
    # "" on a row indexed before the column existed.
    ending: str = ""
    # Local commands the user ran inside this turn, one line each: "/export (local command) → …"
    local_commands: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict) -> InteractionRecord:
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            timestamp=d["timestamp"],
            session_file=d.get("session_file", ""),
            byte_offset=d.get("byte_offset", 0),
            git_branch=d.get("git_branch", ""),
            user_message_preview=d.get("user_message_preview", ""),
            assistant_reply_preview=d.get("assistant_reply_preview", ""),
            plugins=d.get("plugins", []),
            plugin_credits=[PluginCredit(**c) for c in d.get("plugin_credits", [])],
            agents_spawned=[AgentSpawn(**a) for a in d.get("agents_spawned", [])],
            skills_invoked=[SkillInvocation(**s) for s in d.get("skills_invoked", [])],
            tools_called=[ToolCall(**t) for t in d.get("tools_called", [])],
            files_touched=[FileTouch(**f) for f in d.get("files_touched", [])],
            error_signals=[ErrorSignal(**e) for e in d.get("error_signals", [])],
            intent=d.get("intent"),
            outcome=d.get("outcome"),
            tokens_estimated=d.get("tokens_estimated", 0),
            duration_seconds=d.get("duration_seconds", 0),
            ending=d.get("ending", ""),
            local_commands=d.get("local_commands", []),
        )
