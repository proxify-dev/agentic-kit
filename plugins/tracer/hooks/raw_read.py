#!/usr/bin/env python3
"""tracer raw-read — PostToolUse (Bash · Read · Grep · Glob): a direct read of what the
tracer's verbs read is answered with the verbs.

Why: an agent asked for a timeline of the work in a folder had the kit tools in its context and
still wrote a script over <claude_home>/projects/*.jsonl; another, asked what is running now, read
the session registry by hand. Knowing a door exists is not reaching for it; this is said at the moment of
the bypass, not only at session start.

Fires when the call's input names <claude_home>/projects outside a saved `tool-results/` file (a result
the tool-results plugin pinned and pointed the model at — reading it is the point), <claude_home>/sessions,
the registry `tracer sessions live` reads, or the tool-call trace store (`brain-traces/`, the kit's `db/traces`,
`traces.db`) that `tracer trace` reads. The same rule as the eval's `raw` count
for the tracer's half — its `_RAW` must match this one. Says one line
of additionalContext; never blocks, never raises, writes nothing. A program's session gets nothing
(plugins/HOOKS.md §3). <claude_home> is the seam's claude_home() (it honours CLAUDE_CONFIG_DIR); a Windows path's
backslashes are read as "/".
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _brand import claude_home, posix, utf8_stdio  # noqa: E402
from session_origin import automated  # noqa: E402
from prompt_text import prompt  # noqa: E402


TOOLS = {"Bash", "Read", "Grep", "Glob"}
_HOME = r"(?:\.claude|" + re.escape(posix(claude_home())) + ")"  # the default name, and wherever it really is
_PINNED = re.compile(_HOME + r"/projects/[^\s\"']*?/tool-results/[^\s\"']*")
_TRACE_STORE = re.compile(r"brain-traces/|\bdb/traces\b|traces\.db\b")
_RAW = re.compile(_HOME + r"/(?:projects|sessions)\b|brain-traces/|\bdb/traces\b|traces\.db\b")
_SUBAGENTS = re.compile(_HOME + r"/projects/[^\s\"']*?([0-9a-f]{8})[0-9a-f-]{28}/subagents\b|/subagents/[^\s\"']*agent-")


def _text(tool_input) -> str:
    """The input as JSON with each escaped backslash read as "/" — C:\\Users\\x\\.claude matches too."""
    return json.dumps(tool_input or {}).replace("\\\\", "/")

SAY = prompt("after-raw-read.session-record")
SAY_TRACES = prompt("after-raw-read.trace-store")


def say_agents(session: str) -> str:
    """A read of a subagent's transcript, answered with the verb that reads them all: an agent
    met `transcript: subagents/agent-….jsonl` under each Agent step and summed tokens with jq, double-counting
    every message that spans several records."""
    return prompt("after-raw-read.subagents", session=session)


def raw_read(tool_input) -> bool:
    return bool(_RAW.search(_PINNED.sub("", _text(tool_input))))


def main():
    utf8_stdio()
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if automated() or payload.get("tool_name") not in TOOLS:
        return 0
    clean = _PINNED.sub("", _text(payload.get("tool_input")))
    sub = _SUBAGENTS.search(clean) if _RAW.search(clean) else None
    say = SAY_TRACES if _TRACE_STORE.search(clean) else \
        say_agents(sub.group(1) or "<session>") if sub else SAY if _RAW.search(clean) else None
    if say:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": say}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
