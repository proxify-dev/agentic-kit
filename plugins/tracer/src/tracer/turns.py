"""What one turn of a transcript is: where it opens, its steps, how it ended.

A transcript is a flat run of records. A TURN opens at a prompt the user typed and runs to
the next one; what the model did in between is its STEPS, one per tool call, numbered from
1. The indexer (parser.py) and the drill (drill.py) both walk turns with this code, so a
turn id or a step number means the same thing on every surface.

Not every user-typed record opens a turn. Three kinds ride along inside the turn they
follow, because the model never answers them:

    [Request interrupted by user]     the Esc key — it ends the turn it interrupts
    /export, /copy, /reload-plugins   a local command: a <local-command-caveat> meta
                                      record, then <command-name>, then its stdout
    a compaction summary              `isCompactSummary` — context, not a question

A `<command-name>` record is ambiguous on its own: a skill's slash command (`/tracer:trace`)
is written the same way and IS a prompt. The caveat that Claude Code writes just before a
local command is what tells them apart, so the splitter is stateful.

Pure functions over parsed records — no IO, no index.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

INTERRUPT_PREFIX = "[Request interrupted by user"
_CAVEAT = "<local-command-caveat>"
_LOCAL_OUTPUT = ("<local-command-stdout>", "<local-command-stderr>")
_COMMAND_RE = re.compile(r"^<command-(?:name|message)>")
_COMMAND_NAME_RE = re.compile(r"<command-name>([^<]*)</command-name>")
_COMMAND_ARGS_RE = re.compile(r"<command-args>([^<]*)</command-args>")
_TAG_RE = re.compile(r"<[^>]+>")


def user_text(msg: dict) -> str:
    """The text a user record carries, tags intact: string content, or its text blocks joined."""
    message = msg.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        )
    return ""


def user_kind(msg: dict) -> str:
    """What a user record is, context-free.

    prompt · command (a slash command — local or a skill; the splitter decides) ·
    interrupt · caveat · local-output · compact · meta · tool-result · empty.
    """
    message = msg.get("message")
    if not isinstance(message, dict):
        return "empty"
    content = message.get("content", "")
    if isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    ):
        return "tool-result"
    if msg.get("isCompactSummary"):
        return "compact"
    text = user_text(msg).lstrip()
    if text.startswith(_CAVEAT):
        return "caveat"
    if msg.get("isMeta"):
        return "meta"
    if text.startswith(INTERRUPT_PREFIX):
        return "interrupt"
    if text.startswith(_LOCAL_OUTPUT):
        return "local-output"
    if _COMMAND_RE.match(text):
        return "command"
    if text.strip():
        return "prompt"
    # A pasted image with no words is still something the user sent.
    if isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "image" for b in content
    ):
        return "prompt"
    return "empty"


class TurnSplitter:
    """Feed records in file order; ``kind(msg)`` says what each is, with the one bit of
    context a record cannot carry itself: whether a caveat just announced a local command.
    ``opens_turn(msg)`` is ``kind(msg) == "prompt"``."""

    def __init__(self) -> None:
        self._local_next = False

    def kind(self, msg: dict) -> str:
        mtype = msg.get("type")
        if mtype == "assistant":
            self._local_next = False
            return "assistant"
        if mtype == "system" and msg.get("subtype") == "local_command":
            return "local-output"
        if mtype != "user":
            return "other"
        k = user_kind(msg)
        if k == "caveat":
            self._local_next = True
        elif k == "command":
            k = "local-command" if self._local_next else "prompt"
            self._local_next = False
        elif k == "prompt":
            self._local_next = False
        return k

    def opens_turn(self, msg: dict) -> bool:
        return self.kind(msg) == "prompt"


def turn_starts(messages: list[dict]) -> list[int]:
    """Indexes of the records that open a turn."""
    splitter = TurnSplitter()
    return [i for i, m in enumerate(messages) if splitter.opens_turn(m)]


def strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.S)


def prompt_text(text: str) -> str:
    """What a prompt says, tags stripped. A background task's completion notice arrives as
    a user record and opens a turn; stripped bare it read as a run of ids and paths
    ("a4b75c557bd84dad1 toolu_019z… /private/tmp/…"), so it reads as its summary."""
    s = (text or "").lstrip()
    if s.startswith("<task-notification>"):
        m = _SUMMARY_RE.search(s)
        return "task notification: " + strip_tags(m.group(1) if m else s)
    return strip_tags(text or "")


_TEAMMATE = ("Another Claude session sent a message", "<teammate-message")


def sender(text: str) -> str:
    """Who wrote a prompt-shaped user record: ``user``, ``task`` (a background task's
    completion notice) or ``teammate`` (another Claude session, an agent team's member).
    All three open turns; only the first is someone typing. 44 turns of c9fdf42b held 17
    of the other two, and an agent reading them as the user's words took a teammate's
    report for a request."""
    s = (text or "").lstrip()
    if s.startswith("<task-notification>"):
        return "task"
    if s.startswith(_TEAMMATE):
        return "teammate"
    return "user"


def said(msg: dict) -> str:
    """What the user typed in a prompt record, whole: tags stripped, and a slash command
    as they typed it (``/subtask wait, before…``) rather than its three tag lines."""
    text = user_text(msg)
    name = _COMMAND_NAME_RE.search(text)
    if name and _COMMAND_RE.match(text.lstrip()):
        # the args whole, even when they hold a `<` (_COMMAND_ARGS_RE stops at one)
        start, end = text.find("<command-args>"), text.rfind("</command-args>")
        args = text[start + len("<command-args>"):end].strip() if 0 <= start < end else ""
        return f"{name.group(1).strip()} {args}".strip()
    return prompt_text(text)


def one_line(text: str, cap: int) -> str:
    """Whitespace folded to single spaces, cut at ``cap`` with an ellipsis."""
    s = " ".join((text or "").split())
    return s if len(s) <= cap else s[: max(cap - 1, 0)] + "…"


@dataclass
class Step:
    n: int
    tool: str
    tool_use_id: str
    input: dict
    ts: str
    line: int
    message_id: str
    lead: list[tuple[str, str]] = field(default_factory=list)  # (thinking|text, body) since the previous step
    gap_from: str = ""  # timestamp the model started from: the previous result, or the prompt
    result: object = None  # the tool_result block's content, as written
    result_ts: str = ""
    result_line: int = 0
    is_error: bool = False
    use_result: object = None  # the record's toolUseResult (structured)
    answered: bool = False

    @property
    def result_text(self) -> str:
        return result_text(self.result)


@dataclass
class LocalCommand:
    name: str
    args: str = ""
    output: str = ""

    def line(self) -> str:
        cmd = f"{self.name} {self.args}".strip()
        out = one_line(self.output, 160)
        return f"{cmd} (local command)" + (f" → {out}" if out else "")


@dataclass
class Turn:
    prompt: dict
    line: int
    steps: list[Step] = field(default_factory=list)
    closing: list[tuple[str, str]] = field(default_factory=list)  # thinking/text after the last step
    interrupted_ts: str = ""
    interrupted_after: int | None = None
    local_commands: list[LocalCommand] = field(default_factory=list)
    usage: dict[str, dict] = field(default_factory=dict)  # message id -> usage of its last record
    models: dict[str, str] = field(default_factory=dict)
    end_ts: str = ""

    @property
    def ts(self) -> str:
        return self.prompt.get("timestamp", "") or ""

    @property
    def prompt_text(self) -> str:
        return prompt_text(user_text(self.prompt))

    @property
    def final_text(self) -> str:
        """The closing answer: the last text block after the last step, else ""."""
        texts = [body for kind, body in self.closing if kind == "text"]
        return texts[-1] if texts else ""

    @property
    def last_words(self) -> tuple[str, str]:
        """The last thing the model said or thought anywhere in the turn: (kind, body)."""
        if self.closing:
            return self.closing[-1]
        for step in reversed(self.steps):
            if step.lead:
                return step.lead[-1]
        return ("", "")

    @property
    def ending(self) -> str:
        """answered · interrupted:<steps before it> · tool:<steps> (no closing text) · empty."""
        if self.interrupted_after is not None:
            return f"interrupted:{self.interrupted_after}"
        if self.final_text:
            return "answered"
        if self.steps:
            return f"tool:{len(self.steps)}"
        return "empty"


def result_text(content: object) -> str:
    """A tool_result block's content as text; images become ``[image]``."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for sub in content:
            if not isinstance(sub, dict):
                continue
            if sub.get("type") == "text":
                parts.append(sub.get("text", ""))
            elif sub.get("type") == "image":
                parts.append("[image]")
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def build_turn(records: list[tuple[int, dict]]) -> Turn:
    """Walk one turn's records — ``(line, record)`` pairs, the prompt first — into a Turn."""
    first_line, first = records[0]
    turn = Turn(prompt=first, line=first_line)
    by_id: dict[str, Step] = {}
    pending: list[tuple[str, str]] = []
    last_result_ts = turn.ts
    splitter = TurnSplitter()
    splitter.kind(first)

    for line, msg in records[1:]:
        kind = splitter.kind(msg)
        ts = msg.get("timestamp", "") or ""
        if kind == "assistant":
            message = msg.get("message") or {}
            mid = message.get("id", "") or msg.get("uuid", "")
            if isinstance(message.get("usage"), dict):
                turn.usage[mid] = message["usage"]
            if message.get("model"):
                turn.models[mid] = message["model"]
            content = message.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "thinking" and block.get("thinking"):
                    pending.append(("thinking", block["thinking"]))
                elif btype == "text" and block.get("text"):
                    pending.append(("text", block["text"]))
                elif btype == "tool_use":
                    step = Step(
                        n=len(turn.steps) + 1,
                        tool=block.get("name", "") or "?",
                        tool_use_id=block.get("id", "") or "",
                        input=block.get("input") if isinstance(block.get("input"), dict) else {},
                        ts=ts,
                        line=line,
                        message_id=mid,
                        lead=pending,
                        gap_from=last_result_ts,
                    )
                    pending = []
                    turn.steps.append(step)
                    by_id[step.tool_use_id] = step
            if ts:
                turn.end_ts = ts
        elif kind == "tool-result":
            content = (msg.get("message") or {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                step = by_id.get(block.get("tool_use_id", ""))
                if step is None or step.answered:
                    continue
                step.result = block.get("content", "")
                step.result_ts = ts
                step.result_line = line
                step.is_error = block.get("is_error") is True
                step.use_result = msg.get("toolUseResult")
                step.answered = True
                if ts:
                    last_result_ts = ts
                    turn.end_ts = ts
        elif kind == "interrupt":
            if turn.interrupted_after is None:
                turn.interrupted_after = len(turn.steps)
                turn.interrupted_ts = ts
                if ts:
                    turn.end_ts = ts
        elif kind == "local-command":
            text = user_text(msg)
            name = _COMMAND_NAME_RE.search(text)
            args = _COMMAND_ARGS_RE.search(text)
            turn.local_commands.append(LocalCommand(
                name=(name.group(1) if name else strip_tags(text)).strip(),
                args=(args.group(1) if args else "").strip(),
            ))
        elif kind == "local-output":
            raw = msg.get("content") if msg.get("type") == "system" else user_text(msg)
            out = strip_tags(raw if isinstance(raw, str) else "")
            if "<local-command-stderr>" in (raw or "") and out:
                out = "stderr: " + out
            open_cmd = next((c for c in reversed(turn.local_commands) if not c.output), None)
            if open_cmd is None:
                run = msg.get("commandRun") or {}
                open_cmd = LocalCommand(name="/" + str(run.get("command", "?")), args=str(run.get("args", "")))
                turn.local_commands.append(open_cmd)
            open_cmd.output = out

    turn.closing = pending
    if not turn.end_ts:
        turn.end_ts = turn.ts
    return turn


def short_tool(name: str) -> str:
    """``mcp__plugin_tracer_tracer__convo_search`` → ``tracer.convo_search``;
    ``mcp__GitHub__get_me`` → ``GitHub.get_me``; built-ins unchanged."""
    if not name.startswith("mcp__"):
        return name
    server, _, tool = name[5:].partition("__")
    if server.startswith("plugin_"):
        rest = server[7:]
        half = (len(rest) - 1) // 2
        # plugin_<plugin>_<server>; a plugin that names its one server after itself
        # spells the name twice, and either way the server is the name to show
        if len(rest) % 2 == 1 and rest[:half] == rest[half + 1:]:
            server = rest[:half]
        else:
            server = rest.partition("_")[2] or rest
    return f"{server}.{tool}" if tool else server
