"""A tool call as one line a person reads: what the model said it was doing, else what it touched.

Claude Code draws every tool row from the same idea (`getToolUseSummary`): a Bash call shows
its `description` — the line the model writes before the call runs — and only falls back to
the command. That line is the intent, in the model's own words, and it was already in every
transcript; the step lines showed the raw input JSON instead, so a reader saw
`{"command":"cd <your repo>…` eighty times and had to guess what each one was for.

The rule, first match wins:

    description         the model's own line (Bash, Monitor, Agent, PowerShell)
    command             "$ " + the command — the "$" says it is raw, not intent
    a path              Read · Edit · Write · NotebookEdit
    pattern [in path]   Grep quotes it, Glob does not
    skill [args]        Skill
    url · query · subject · prompt · name · title    the first one the call has

Verbatim, never summarised: every label is text the transcript holds. The traces index
derives the same line (traces/src/traces-blobs.ts `stepLabel`, `summary.label`);
tests/step_label_cases.json holds both to one rule. Where `description` comes from, the
model-written labels Claude Code has besides it, and how to re-check them in its binary:
the plugin README, § A step's label: where it comes from.
"""

from __future__ import annotations

from pathlib import Path

from .turns import one_line

_PATH_KEYS = ("file_path", "notebook_path")
_TEXT_KEYS = ("url", "query", "subject", "prompt", "name", "title")


def _text(inp: dict, key: str) -> str:
    v = inp.get(key)
    return " ".join(v.split()) if isinstance(v, str) else ""


def _home(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home + "/") or path == home else path


def step_label(tool: str, inp: object, cap: int = 160) -> str:
    """The call's one line, or "" when it has nothing readable (the caller falls back)."""
    if not isinstance(inp, dict):
        return ""
    if desc := _text(inp, "description"):
        return one_line(desc, cap)
    if cmd := _text(inp, "command"):
        return one_line("$ " + cmd, cap)
    for key in _PATH_KEYS:
        if path := _text(inp, key):
            return one_line(_home(path), cap)
    if pattern := _text(inp, "pattern"):
        shown = f"'{pattern}'" if tool == "Grep" else pattern
        where = _text(inp, "path")
        return one_line(shown + (f" in {_home(where)}" if where else ""), cap)
    if skill := _text(inp, "skill"):
        args = _text(inp, "args")
        return one_line(skill + (f" {args}" if args else ""), cap)
    for key in _TEXT_KEYS:
        if text := _text(inp, key):
            return one_line(text, cap)
    return ""
