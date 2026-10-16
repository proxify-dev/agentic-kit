# COPY of plugins/agentic-engineering/hooks/prompt_text.py: edit that file, never this one (scripts/copies.py)
"""prompt_text — the words a hook sends Claude, read from its plugin's prompts/ folder.

prompts/<moment>.<name>.md holds one text: the file name says when Claude reads it (session-start,
before-read, after-raw-read, …) and what it is about; the frontmatter (`why`, `from`) is for people and
is stripped; {{key}} is filled from prompt()'s keyword arguments. The hook holds no words of its own.

Canonical here; each plugin whose hooks send text carries a copy in its hooks/ (scripts/copies.py).
Standard library only (plugins/HOOKS.md §1).
"""
import re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)


def prompt(file: str, /, **fill: str) -> str:
    """The body of prompts/FILE.md, {{key}} replaced by FILL[key]; any key is free, `name` and `file` too."""
    text = _FRONTMATTER.sub("", (PROMPTS / f"{file}.md").read_text(encoding="utf-8")).strip()
    for key, value in fill.items():
        text = text.replace("{{" + key + "}}", value)
    return text
