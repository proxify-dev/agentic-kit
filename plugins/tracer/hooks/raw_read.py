#!/usr/bin/env python3
"""tracer raw-read — PostToolUse (Bash · Read · Grep · Glob): a direct read of what the
tracer's verbs read is answered with the verbs.

Why: on 2026-09-24 an agent asked for "a timeline of what we've been doing in this folder" had
the kit tools in its context and still wrote a script over <claude_home>/projects/*.jsonl
(evals/tool-routing case `timeline`); another asked "what's running now" read the session registry
by hand (case `live`). Knowing a door exists is not reaching for it; this is said at the moment of
the bypass, not only at session start.

Fires when the call's input names <claude_home>/projects outside a saved `tool-results/` file (a result
the tool-results plugin pinned and pointed the model at — reading it is the point), <claude_home>/sessions,
the registry `tracer sessions live` reads, or the tool-call trace store (`brain-traces/`, the kit's `db/traces`,
`traces.db`) that `tracer trace` reads. The same rule as the eval's `raw` count
(evals/tool-routing/score.py) for the tracer's half — its `_RAW` must match this one. Says one line
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


def command(verb: str = "") -> str:
    """This plugin's own command: bin/tracer, on the Bash tool's PATH while the plugin is enabled, with or
    without the `ak` CLI (plugins/AGENTS.md §6). The hooks run on the standard library, so they spell it here."""
    return f"tracer {verb}".strip()

TOOLS = {"Bash", "Read", "Grep", "Glob"}
_HOME = r"(?:\.claude|" + re.escape(posix(claude_home())) + ")"  # the default name, and wherever it really is
_PINNED = re.compile(_HOME + r"/projects/[^\s\"']*?/tool-results/[^\s\"']*")
_TRACE_STORE = re.compile(r"brain-traces/|\bdb/traces\b|traces\.db\b")
_RAW = re.compile(_HOME + r"/(?:projects|sessions)\b|brain-traces/|\bdb/traces\b|traces\.db\b")
_SUBAGENTS = re.compile(_HOME + r"/projects/[^\s\"']*?([0-9a-f]{8})[0-9a-f-]{28}/subagents\b|/subagents/[^\s\"']*agent-")


def _text(tool_input) -> str:
    """The input as JSON with each escaped backslash read as "/" — C:\\Users\\x\\.claude matches too."""
    return json.dumps(tool_input or {}).replace("\\\\", "/")

SAY = (
    "[tracer] That read Claude Code's session record directly. The shell already reads it: "
    f"`{command('sessions live')}` (every open session, what it is doing now) · "
    f"`{command('sessions')}` (every session in this project, oldest first — a timeline, where we stand) · "
    f"`{command('sessions show')} <session>` (one session's turns and steps) · "
    f"`{command('sessions search')} <phrase>` (which sessions said it). Each says what the record does not reach."
)
SAY_TRACES = (
    "[tracer] That read the tool-call trace store directly. The shell already reads it: "
    f"`{command('trace')}` (every tool call from every session, newest first — filter by --tool/--server/"
    f"--session/--outcome/--since) · `{command('trace get')} <tool_use_id>` (one call whole: its input and "
    f"output) · `{command('trace stats')}` (totals)."
)


def say_agents(session: str) -> str:
    """A read of a subagent's transcript, answered with the verb that reads them all: on 2026-10-05 an agent
    met `transcript: subagents/agent-….jsonl` under each Agent step and summed tokens with jq, double-counting
    every message that spans several records."""
    return (
        "[tracer] That read a subagent's transcript directly. The shell already reads them: "
        f"`{command('sessions agents')} {session}` (every subagent of the session: its type, the step that "
        "started it, calls, errors, time, tokens counted once per model message, the files two agents both "
        f"touched) · `{command('sessions agents')} {session} <agent>` (one agent's steps; --open N: one whole)."
    )


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
