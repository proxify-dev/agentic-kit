"""The observer's agent file — agents/observer.md, a standard Claude Code plugin agent.

Its body is the prompt: what counts as an observation, how to decide a verdict, how densely
to cover a session. The summariser (scripts/summarize-session.py) sends it as the system
prompt of a headless run; `observer show` prints it. Dependency-free: hooks import
nothing outside this directory.
"""
from __future__ import annotations

from pathlib import Path

AGENT_FILE = Path(__file__).resolve().parent.parent / "agents" / "observer.md"


def load(path: Path = AGENT_FILE) -> dict:
    """The file as Claude Code reads it: flat `key: value` frontmatter, the body as its
    prompt. {"name", "description", "model", …, "prompt", "path"}."""
    text = path.read_text(encoding="utf-8")
    _, fm, body = text.split("---\n", 2)
    data = {}
    for line in fm.splitlines():
        key, sep, val = line.partition(":")
        if sep and key.strip():
            data[key.strip()] = val.strip()
    return {**data, "prompt": body.strip(), "path": str(path)}
