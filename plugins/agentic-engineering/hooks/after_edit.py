#!/usr/bin/env python3
"""agentic-engineering after-edit (PostToolUse Write|Edit|MultiEdit on CLAUDE.md, AGENTS.md, CLAUDE.local.md).

A folder's CLAUDE.md is the prompt every session working there obeys, so an edit to it has two readers:

  the model    additionalContext: what a check found in the file just written. Over 200 lines; an
               AGENTS.md Claude does not read (a CLAUDE.md here or above takes its place and none
               imports it: Claude Code 2.1.277+, by default); a relative link or @import that does
               not resolve. Each finding names its file. Nothing found, nothing said; a finding this
               agent was already told since the last compaction is not said again.
  the person   systemMessage: "<path> edited (+n −m)", counted from the tool's own response.

Reads the file and its neighbours; writes nothing. In a program's session the findings still answer
(plugins/HOOKS.md §3: the write-lint's findings are about the file) and the person's line is left out.
Fails silent: any error prints nothing and exits 0.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _brand import claude_home, posix, utf8_stdio  # noqa: E402
from blocks import said, transcript_for  # noqa: E402
from session_origin import automated  # noqa: E402

TOOLS = {"Write", "Edit", "MultiEdit"}
INSTRUCTION_FILES = {"CLAUDE.md", "AGENTS.md", "CLAUDE.local.md"}
MAX_LINES = 200
MAX_LINKS = 5
_LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^)]*)?\)")
_IMPORT = re.compile(r"(?:^|(?<=\s))@((?:~/|\.{1,2}/)?[\w.\-/]+\.md)\b")
_CODE = re.compile(r"`[^`]*`")
# The files that keep Claude from reading AGENTS.md when one sits in the folder, or in the working
# directory or above (memory docs, "When Claude Code reads AGENTS.md"); ~/.claude/CLAUDE.md does not count.
_CLAUDE_FILES = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:|#|/)", re.I)


def _counts(tool: str, ti: dict, resp) -> tuple[int, int] | None:
    """(+added, −removed) lines, from the tool's structuredPatch; else from its input."""
    resp = resp if isinstance(resp, dict) else {}
    patch = resp.get("structuredPatch")
    if isinstance(patch, list) and patch:
        lines = [ln for hunk in patch if isinstance(hunk, dict) for ln in hunk.get("lines") or [] if isinstance(ln, str)]
        return sum(ln.startswith("+") for ln in lines), sum(ln.startswith("-") for ln in lines)
    if tool == "Write":
        new = (ti.get("content") or "").splitlines()
        old = resp.get("originalFile")
        if not old:
            return len(new), 0
        pairs = [(old, ti.get("content") or "")]
    elif tool == "Edit":
        pairs = [(ti.get("old_string") or "", ti.get("new_string") or "")]
    else:
        pairs = [(e.get("old_string") or "", e.get("new_string") or "") for e in ti.get("edits") or [] if isinstance(e, dict)]
    add = rem = 0
    for old, new in pairs:
        for ln in difflib.ndiff(old.splitlines(), new.splitlines()):
            add += ln.startswith("+ ")
            rem += ln.startswith("- ")
    return add, rem


def _prose(text: str):
    """(line number, line) outside fenced code, inline code spans blanked."""
    fenced = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced:
            yield n, _CODE.sub("", line)


def _from_home(target: str) -> bool:
    """`~/x`, and `~\\x` where "\\" is the separator (Windows)."""
    return target.startswith("~/") or target.startswith("~" + os.sep)


def _resolves(target: str, folder: Path) -> bool:
    target = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not target:
        return True
    path = Path(os.path.expanduser(target)) if _from_home(target) else folder / target
    return path.exists()


def _shown(path: Path, cwd: str) -> str:
    """A path as the session sees it: relative to its cwd, else from ~. Always "/"-separated."""
    try:
        return posix(path.relative_to(cwd))
    except ValueError:
        home = os.path.expanduser("~")
        return posix("~" + str(path)[len(home):] if str(path).startswith(home + os.sep) else str(path))


def _claude_files(folder: Path) -> list[Path]:
    user = claude_home() / "CLAUDE.md"
    return [f for f in (folder / n for n in _CLAUDE_FILES) if f.is_file() and f != user]


def _imports(f: Path, target: Path) -> bool:
    """True when `f` has an `@path` import, outside code, that resolves to `target`."""
    for _, line in _prose(f.read_text(encoding="utf-8", errors="replace")):
        for m in _IMPORT.finditer(line):
            t = m.group(1)
            p = Path(os.path.expanduser(t)) if _from_home(t) else f.parent / t
            if os.path.normpath(p) == os.path.normpath(target):
                return True
    return False


def _agents_unread(folder: Path, cwd: Path) -> str | None:
    """How to fix a folder's AGENTS.md that Claude does not read, else None. By default Claude Code
    (2.1.277+) reads AGENTS.md only where no CLAUDE.md, .claude/CLAUDE.md or CLAUDE.local.md sits in
    the folder, or in the working directory or above; where one does, AGENTS.md arrives only through
    an import."""
    agents = folder / "AGENTS.md"
    if not agents.is_file():
        return None
    top = cwd if cwd == folder or cwd in folder.parents else folder
    counted = [f for d in dict.fromkeys((folder, top, *top.parents)) for f in _claude_files(d)]
    if not counted or any(_imports(f, agents) for f in counted):
        return None
    own = _claude_files(folder)
    fix = (f"add the line `@AGENTS.md` to {_shown(own[0], str(cwd))}" if own
           else "give this folder a CLAUDE.md holding the line `@AGENTS.md`")
    return f"Claude does not read it: {_shown(counted[0], str(cwd))} takes its place and does not import it; {fix}"


def findings(path: Path, cwd: str) -> list[tuple[Path, str]]:
    """(the file it is about, what was found) for the instruction file just written."""
    text = path.read_text(encoding="utf-8", errors="replace")
    folder, found = path.parent, []
    n = len(text.splitlines())
    if n > MAX_LINES:
        found.append((path, f"{n} lines, over {MAX_LINES}: every session in this folder reads each one; "
                            "move depth into a file a line points at"))
    unread = _agents_unread(folder, Path(cwd))
    if unread:
        found.append((folder / "AGENTS.md", unread))
    broken = []
    for lineno, line in _prose(text):
        targets = [m.group(1) for m in _LINK.finditer(line)]
        targets += [m.group(1) for m in _IMPORT.finditer(line)]
        broken += [f"{t} (line {lineno})" for t in targets if not _EXTERNAL.match(t) and not _resolves(t, folder)]
    if broken:
        more = f" and {len(broken) - MAX_LINKS} more" if len(broken) > MAX_LINKS else ""
        found.append((path, "does not resolve: " + ", ".join(broken[:MAX_LINKS]) + more))
    return found


def main() -> None:
    utf8_stdio()
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or payload.get("tool_name") not in TOOLS:
        return
    ti = payload.get("tool_input") or {}
    file_path = ti.get("file_path") or ""
    if Path(file_path).name not in INSTRUCTION_FILES:
        return
    cwd = payload.get("cwd") or os.getcwd()
    path = Path(file_path if os.path.isabs(file_path) else os.path.join(cwd, file_path))
    shown = _shown(path, cwd)
    out: dict = {}
    if path.is_file():
        transcript = transcript_for(payload)
        lines = [f"  - {_shown(about, cwd)}: {what}" for about, what in findings(path, cwd)]
        lines = [ln for ln in lines if not said(transcript, ln)]
        if lines:
            ctx = "\n".join([f"[agentic-engineering] {shown}, just written:", *lines])
            out["hookSpecificOutput"] = {"hookEventName": "PostToolUse", "additionalContext": ctx}
    if not automated():
        counts = _counts(payload["tool_name"], ti, payload.get("tool_response"))
        diff = f" (+{counts[0]} −{counts[1]})" if counts else ""
        out["systemMessage"] = f"\x1b[35magentic-engineering:\x1b[0m {shown} edited{diff}"
    if out:
        sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
