#!/usr/bin/env python3
"""agentic-engineering on-edit (PreToolUse): the block for the moment this call starts.

PreToolUse context reaches the agent making the call, main session or worker, where a start block
does not; it lands beside the call's result. Each block is prompts/<name>.txt:

  CLAUDE.md · AGENTS.md · CLAUDE.local.md · .claude/rules/*            instruction-files
  a plugin's evals/ or tests/; Bash: claude -p · claude plugin eval ·
    env … claude -p · tmux                                              evals
  hooks/hooks.json · a hook script · .claude/settings*.json             hooks
  a new SKILL.md, agents/*.md, commands/*.md · .claude-plugin/*.json    skills
  an existing one of those · skills/*/references/* · a plugin's
    prompts/* · templates/harness/*.md                                  agent-text
  Agent, Task, Workflow (not a Workflow resume)                         brief

A hook script, agents/ and commands/ count only in Claude Code's own places (a `.claude/` folder, a
plugin root, a hooks/ folder holding hooks.json): an app's hooks/ or a docs/agents/ folder is not one.

Once per agent per compaction: a block already in the agent's transcript is skipped (blocks.delivered).
Bash reaches this hook through one handler per `if` rule in hooks.json, each naming the moments it
answers for (argv: evals); no argv answers for all. The command is re-checked here,
since `if` is best effort and lets through what it cannot parse.

A program's session gets nothing here (plugins/HOOKS.md §3); an eval counts as a person's with
EVAL_AK_CAPTURE=1. Known gaps: an edit made through Bash (sed -i, a heredoc), a script that
launches claude calls nothing this hook sees.
Fails silent: any error prints nothing and exits 0.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _brand import utf8_stdio  # noqa: E402
from blocks import delivered, load, transcript_for  # noqa: E402
from session_origin import automated  # noqa: E402

TOOLS = {"Write", "Edit", "MultiEdit"}
BRIEFS = {"Agent", "Task", "Workflow"}
INSTRUCTION_FILES = {"CLAUDE.md", "AGENTS.md", "CLAUDE.local.md"}
WRAPPERS = {"time", "nice", "nohup", "command", "builtin", "noglob", "exec", "stdbuf"}
SHELLS = {"sh", "bash", "zsh", "pwsh", "powershell", "cmd"}
SHELL_RUN = {"-c", "-Command", "/c", "/C"}  # the flag after which a shell's argument is a command line
_EXE = re.compile(r"\.(exe|cmd|bat)$", re.I)
_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w.-]*)\1")
_SEPARATOR = set("();&|")


# ── a file: which block its edit gets ──────────────────────────────────────────────────────────


def _claude_place(folder: Path) -> bool:
    """A folder Claude Code reads hooks, agents and commands from: `.claude/` or a plugin root."""
    return folder.name == ".claude" or (folder / ".claude-plugin").is_dir()


def _evals(p: Path) -> bool:
    """Under a plugin's (or `.claude/`'s) evals/ or tests/."""
    return any(a.name in ("evals", "tests") and _claude_place(a.parent) for a in list(p.parents)[:8])


def surface(file_path: str) -> str | None:
    """The block for an edit of this path, or None."""
    p = Path(file_path)
    name, parent = p.name, p.parent
    if name in INSTRUCTION_FILES or "/.claude/rules/" in "/" + p.as_posix():
        return "instruction-files"
    if _evals(p):
        return "evals"
    if parent.name == "hooks" and name == "hooks.json":
        return "hooks"
    if parent.name == "hooks" and p.suffix in (".py", ".sh") and (
            (parent / "hooks.json").is_file() or _claude_place(parent.parent)):
        return "hooks"
    if parent.name == ".claude" and name.startswith("settings") and p.suffix == ".json":
        return "hooks"
    new = not p.exists()
    if name == "SKILL.md" and parent.parent.name == "skills":
        return "skills" if new else "agent-text"
    if parent.name in ("agents", "commands") and p.suffix == ".md" and _claude_place(parent.parent):
        return "skills" if new else "agent-text"
    if any(p.parts[k] == "skills" and p.parts[k + 2] == "references" for k in range(len(p.parts) - 3)):
        return "agent-text"
    if parent.name == "prompts" and p.suffix in (".txt", ".md") and _claude_place(parent.parent):
        return "agent-text"
    if parent.name == "harness" and parent.parent.name == "templates" and p.suffix == ".md":
        return "agent-text"
    if parent.name == ".claude-plugin" and p.suffix == ".json":
        return "skills"
    return None


# ── a Bash command: which moments it starts ────────────────────────────────────────────────────


def _without_heredocs(command: str) -> str:
    """The command with each heredoc's body cut: text written to a file is not a command run."""
    lines, out, i = command.split("\n"), [], 0
    while i < len(lines):
        out.append(lines[i])
        delims = [m.group(2) for m in _HEREDOC.finditer(lines[i])]
        i += 1
        for d in delims:
            while i < len(lines) and lines[i].strip() != d:
                i += 1
            i += 1
    return "\n".join(out)


def _segments(command: str) -> list[list[str]]:
    """The simple commands in COMMAND, each as its words, split at ; & | ( ) and newlines."""
    text = _without_heredocs(command).replace("\\\n", " ").replace("\n", " ; ")
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:  # an unclosed quote: split on the operators alone
        return [s.split() for s in re.split(r"[;&|()]+", text) if s.split()]
    segs, cur, skip = [], [], False
    for t in tokens:
        if skip:
            skip = False
        elif t and set(t) <= _SEPARATOR:
            segs.append(cur)
            cur = []
        elif t and set(t) <= set("<>&|"):
            skip = True  # a redirection: the next word is its target
        else:
            cur.append(t)
    segs.append(cur)
    return [s for s in segs if s]


def _unwrap(argv: list[str]) -> list[str]:
    """The command a segment runs, past VAR=value, env and its options, and wrappers like nohup."""
    i = 0
    while i < len(argv):
        w = argv[i]
        if _ASSIGN.match(w):
            i += 1
        elif w == "env":
            i += 1
            while i < len(argv) and (argv[i].startswith("-") or _ASSIGN.match(argv[i])):
                i += 2 if argv[i] in ("-u", "-C", "-S", "--unset", "--chdir") else 1
        elif w == "timeout":
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                i += 1
            i += 1  # the duration
        elif w in WRAPPERS:
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                i += 1
        else:
            break
    return argv[i:]


def moments(command: str, depth: int = 0) -> set[str]:
    """The moments COMMAND starts: evals, for a claude -p, claude plugin eval or tmux run."""
    found = set()
    for argv in _segments(command):
        argv = _unwrap(argv)
        if not argv:
            continue
        prog, args = _EXE.sub("", re.split(r"[/\\]", argv[0])[-1]), argv[1:]  # C:\x\claude.exe is claude
        if prog == "claude" and ({"-p", "--print"} & set(args) or args[:2] == ["plugin", "eval"]):
            found.add("evals")
        elif prog == "tmux":
            found.add("evals")
        elif prog.lower() in SHELLS and SHELL_RUN & set(args) and depth == 0:
            i = next(i for i, a in enumerate(args) if a in SHELL_RUN)
            if i + 1 < len(args):  # cmd /c runs the rest of the line; the others, the one argument after -c
                line = shlex.join(args[i + 1:]) if prog.lower() == "cmd" else args[i + 1]
                found |= moments(line, depth + 1)
    return found


# ── the hook ───────────────────────────────────────────────────────────────────────────────────


def _context(block: str) -> None:
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": block}}))


def _bash(payload: dict, command: str, only: set[str]) -> None:
    """ONLY: the moments this handler answers for. Each Bash handler in hooks.json names its own, since
    a command two `if`s match runs the hook twice, and the block would come twice."""
    if "evals" in moments(command) & only:
        block = load("evals")
        if not delivered(transcript_for(payload), block):
            _context(block)


def main() -> None:
    utf8_stdio()
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or automated():
        return
    tool = payload.get("tool_name", "Write")
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        return
    if tool == "Bash":
        if isinstance(ti.get("command"), str):
            _bash(payload, ti["command"], set(sys.argv[1:]) or {"evals"})
        return
    if tool in BRIEFS:
        if tool == "Workflow" and (ti.get("resumeFromRunId") or ti.get("resume")):
            return
        kind = "brief"
    elif tool in TOOLS:
        file_path = ti.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return
        if not os.path.isabs(file_path):
            file_path = os.path.join(payload.get("cwd") or os.getcwd(), file_path)
        kind = surface(file_path)
        if not kind:
            return
    else:
        return
    block = load(kind)
    if not delivered(transcript_for(payload), block):
        _context(block)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
