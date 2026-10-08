#!/usr/bin/env python3
"""agentic-engineering session (SessionStart startup|resume|clear|compact|fork, and SubagentStart): the map.

Says prompts/session.txt as additionalContext: how an instruction reaches an agent, and the
skill that holds the depth. SubagentStart says it too, because a worker gets none of the session's
start context. It is the plugin's tool-teaching line, so a person's session and a program's get the
same thing (plugins/HOOKS.md §3: 800 characters a plugin, 2,400 all together; the contract test holds
both); nothing here is state, and nothing is written. The rules for one surface arrive at the edit
itself (on_edit.py).
Fails silent: a malformed payload or a missing block prints nothing and exits 0.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _brand import utf8_stdio  # noqa: E402
from blocks import load  # noqa: E402

EVENTS = {"SessionStart", "SubagentStart"}


def main() -> None:
    utf8_stdio()
    payload = json.load(sys.stdin)
    event = payload.get("hook_event_name") if isinstance(payload, dict) else None
    if event not in EVENTS:
        return
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": load("session")}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
