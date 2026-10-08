"""blocks — the text every agentic-engineering hook says, read from its home in prompts/.

prompts/<name>.txt is the block itself, the whole file, verbatim: what the agent reads and nothing
else. Its why lives in the commit and the eval's story. Nothing to edit here — change the file.

delivered() and said() answer "is this already in the agent's context?" from the transcript
Claude Code hands every hook: a read, never a write (plugins/HOOKS.md §3). Standard library only.
"""
from __future__ import annotations

import json
import mmap
import re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
_COMPACT = b'"subtype":"compact_boundary"'
_HOOK_RECORDS = (b'"hook_additional_context"', b'"hook_success"')
_DENY_RECORD = (b'"tool_result"', b'"is_error":true', b"hook error: ")


def load(name: str) -> str:
    """prompts/<name>.txt, without its closing newline."""
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8").rstrip("\n")


def tag(block: str) -> str:
    """How delivered() recognises a block in a transcript: its opening `[…]` tag, ASCII
    and the same after a rewording; a block without one, its first line."""
    first = next((ln.strip() for ln in block.splitlines() if ln.strip()), "")
    m = re.match(r"\[[^\]\n]+\]", first)
    return m.group() if m else first


def transcript_for(payload: dict) -> Path | None:
    """The transcript of the agent making this call. A worker's call carries `agent_id` and the main
    session's `transcript_path`; its context is its own file under <session>/subagents/ — a block the
    parent got is not in the worker's context. A Task worker's file sits there directly
    (agent-<id>.jsonl), a workflow worker's one level down (workflows/<wf>/agent-<id>.jsonl)."""
    tp = payload.get("transcript_path")
    if not tp:
        return None
    path = Path(tp)
    agent = payload.get("agent_id")
    if not agent or path.name == f"agent-{agent}.jsonl":
        return path
    subagents = path.with_suffix("") / "subagents"
    direct = subagents / f"agent-{agent}.jsonl"
    if direct.is_file():
        return direct
    try:
        return next(subagents.glob(f"**/agent-{agent}.jsonl"), direct)
    except OSError:
        return direct


def delivered(transcript: Path | None, block: str) -> bool:
    """True when a hook's record in this transcript carries the block's tag since the last compaction."""
    return said(transcript, tag(block))


def said(transcript: Path | None, line: str) -> bool:
    """True when a hook's record in this transcript carries `line` since the last compaction (a
    compaction drops it from context, so it is said again). Claude Code writes each hook's context
    twice, as `hook_success` stdout and as `hook_additional_context`; the line is searched in the form
    the latter stores (JSON-escaped, non-ASCII kept). The agent quoting or reading the text is a tool
    result, not a hook record, and does not count."""
    return _found(transcript, line, lambda rec: b'"attachment"' in rec and any(k in rec for k in _HOOK_RECORDS))

def _found(transcript: Path | None, line: str, record) -> bool:
    """True when `line` (JSON-escaped, as a transcript stores it) sits after the last compaction in a
    transcript record that `record(bytes)` accepts."""
    needle = json.dumps(line, ensure_ascii=False)[1:-1].encode("utf-8")
    if not transcript or not needle:
        return False
    try:
        with open(transcript, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            i = mm.find(needle, mm.rfind(_COMPACT) + 1)
            while i != -1:
                start, end = mm.rfind(b"\n", 0, i) + 1, mm.find(b"\n", i)
                end = len(mm) if end == -1 else end
                if record(mm[start:end]):
                    return True
                i = mm.find(needle, end)
    except (OSError, ValueError):  # missing, unreadable, or empty (mmap of 0 bytes)
        return False
    return False
