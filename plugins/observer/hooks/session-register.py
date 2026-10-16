#!/usr/bin/env python3
"""observer — session register (UserPromptSubmit).

On the first prompt of a session, stamp a `session` meta record at the head of
the per-session scratch: project binding (same resolver the awareness hooks use),
cwd, git remote, start time. The summarizer reads this to file observations under
the right project. Stamps once per session; adds no context to the prompt.

Cheap, fail-silent — a broken capture hook must never block a prompt.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _brand import replace_retry, utf8_stdio  # noqa: E402
from capture_common import append_event, capture_enabled, scratch_path, slugify, state_dir  # noqa: E402
from project_match import resolve_binding, git_remote, vault_root  # noqa: E402
from vault_root import home_root  # noqa: E402


def project_slug(cwd: str) -> str:
    """Stable folder key for memory/observations/<project>/. Bound note stem,
    except the vault's own root note (MEMORY.md) → the label its stored rows carry. Unbound → cwd basename."""
    match = resolve_binding(cwd)
    if not match:
        return slugify(Path(cwd).name) or "unbound"
    note = match["note"]
    try:
        if note.parent == Path(home_root()):
            return "brain"  # brand: historical — the project label every stored row of the vault root carries
    except Exception:
        pass
    return slugify(note.stem)


def register_cwd(cwd: str, session_id: str) -> None:
    """Stamp `<kit>/cache/sessions/<cwd-key>.json` — which session owns this
    directory right now.

    An MCP server is handed no session id: Claude Code spawns it before the
    first prompt and never tells it whose session it is. The kit tools' stdio
    server of the kit tools reads this
    file for its own `process.cwd()` at CALL time, so a note written from a
    terminal can be stamped `agent:terminal session:<uuid>` instead of losing
    half its provenance.

    Rewritten on EVERY prompt, not once per session: the reader uses the `at`
    stamp to refuse a file left behind by a session that has since ended, and a
    write-once file would go stale the moment a second session opened here.

    Deliberately ahead of the capture switch — provenance is not capture, and a
    person who turned the Observer off has not asked for unattributed notes.
    """
    # realpath, not abspath: on macOS `/var` is a symlink to its /private twin, and
    # a server calling getcwd() in the same directory would compute the other
    # spelling and find no file. The reader tries both; the writer picks one.
    key = re.sub(r"[^A-Za-z0-9]+", "-", os.path.realpath(cwd)).strip("-").lower()
    path = state_dir() / f"{key}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"session_id": session_id, "cwd": cwd, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}),
        encoding="utf-8", newline="\n",
    )
    replace_retry(tmp, path)


def main() -> None:
    utf8_stdio()
    payload = json.load(sys.stdin)
    cwd = payload.get("cwd") or os.getcwd()
    try:
        register_cwd(cwd, payload.get("session_id") or "")
    except Exception:
        pass  # provenance is a nicety; a broken stamp must never block a prompt
    if not capture_enabled(cwd):
        return  # observer switched off (`observer status`), or summarizer recursion
    session_id = payload.get("session_id") or "unknown"
    if scratch_path(session_id).exists():
        return  # already registered this session — stamp the meta record only once
    append_event(session_id, {
        "kind": "session",
        "session_id": session_id,
        "cwd": cwd,
        "repo": git_remote(cwd),
        "project": project_slug(cwd),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
