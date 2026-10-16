#!/usr/bin/env python3
"""observer — tool event (PostToolUse, all tools).

Appends one compact record per tool use to the session scratch: tool name, files
touched, and (for Bash/Grep/Glob) the command or pattern. Deliberately does NOT
store tool outputs — those can be huge and may carry secrets; the transcript holds
full detail, this is just structured signal for the Stop-time summarizer and the
substantive gate. Cheap, fail-silent.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _brand import utf8_stdio  # noqa: E402
from capture_common import append_event, capture_enabled  # noqa: E402

MAX_CMD = 300
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def extract(tool_name: str, ti: dict):
    files_read, files_modified, extra = [], [], {}
    fp = ti.get("file_path") or ti.get("notebook_path")
    if tool_name == "Read" and fp:
        files_read.append(fp)
    elif tool_name in WRITE_TOOLS and fp:
        files_modified.append(fp)
    if tool_name == "Bash" and ti.get("command"):
        extra["command"] = str(ti["command"])[:MAX_CMD]
    if tool_name in ("Glob", "Grep") and ti.get("pattern"):
        extra["pattern"] = str(ti["pattern"])[:MAX_CMD]
    return files_read, files_modified, extra


def main() -> None:
    utf8_stdio()
    payload = json.load(sys.stdin)
    if not capture_enabled(payload.get("cwd") or os.getcwd()):
        return  # observer switched off (`observer status`), or summarizer recursion
    session_id = payload.get("session_id") or "unknown"
    tool_name = payload.get("tool_name") or "?"
    ti = payload.get("tool_input") or {}
    files_read, files_modified, extra = extract(tool_name, ti)
    rec = {"kind": "tool", "tool": tool_name, "ts": time.strftime("%H:%M:%S")}
    if files_read:
        rec["files_read"] = files_read
    if files_modified:
        rec["files_modified"] = files_modified
    rec.update(extra)
    append_event(session_id, rec)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
