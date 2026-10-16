#!/usr/bin/env python3
"""observer — session end (SessionEnd, once per session).

The correct once-per-session trigger (Stop fires every turn). If the session did
substantive work, spawn the summarizer DETACHED so SessionEnd returns instantly,
then drop a done-marker. The substantive gate (shared with stop-writeback) keeps
the LLM spend to real sessions; the marker keeps it to one run per session.

Guarded by capture_enabled() — which honours both the user's switch
(`observer mute`, <kit>/db/capture.json) and AK_CAPTURE_DISABLE, so
the summarizer's own headless `claude -p` call — which re-fires the whole hook
set — never recurses back into capture.

While the issues trial is on (`observer shadow on`, hooks/shadow_issues.py), a second
summarizer is spawned beside the first with --shadow: it writes only to its own folder.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _brand import spawn_detached, utf8_stdio  # noqa: E402
from capture_common import (  # noqa: E402
    capture_enabled, is_substantive, safe_id, scratch_path, state_dir, vault_root,
)
import shadow_issues  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SUMMARIZER = PLUGIN_ROOT / "scripts" / "summarize-session.py"


def main() -> None:
    utf8_stdio()
    payload = json.load(sys.stdin)
    cwd = payload.get("cwd") or os.getcwd()
    if not capture_enabled(cwd):
        return  # writer muted, or summarizer recursion. Checked here too, not just at
        # register time, so a session already mid-flight when it was muted never
        # reaches the (expensive) LLM summarizer.
    session_id = payload.get("session_id") or "unknown"
    transcript = payload.get("transcript_path") or ""

    done = state_dir() / f"{safe_id(session_id)}.done"
    if done.exists() or not scratch_path(session_id).exists():
        return  # already summarized, or nothing was captured this session
    if not is_substantive(transcript, session_id):
        return  # trivial session — skip the LLM entirely

    try:
        log = open(state_dir() / f"{safe_id(session_id)}.log", "ab", buffering=0)
        # The summariser runs detached from PLUGIN_ROOT, and its inner `claude -p` from a
        # temp dir. The store is the kit's db/ from any cwd (vault_root.db_dir),
        # so nothing is pinned.
        spawn_detached(  # its own session on POSIX, no console on Windows: outlives SessionEnd
            [sys.executable, str(SUMMARIZER), "--session", session_id,
             "--transcript", transcript],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL, cwd=str(PLUGIN_ROOT),
        )
        done.write_text("", encoding="utf-8", newline="\n")  # mark dispatched; summarizer clears the scratch on success
        if shadow_issues.enabled():  # the issues trial: a second read of the same session, its own output
            spawn_detached([sys.executable, str(SUMMARIZER), "--session", session_id, "--transcript", transcript,
                            "--shadow"],
                           stdout=log, stderr=log, stdin=subprocess.DEVNULL, cwd=str(PLUGIN_ROOT))
    except Exception:
        pass  # a broken capture must never disturb session teardown


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
