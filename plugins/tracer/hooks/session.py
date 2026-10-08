#!/usr/bin/env python3
"""tracer session — SessionStart teaches the one move.

One line of additionalContext naming this plugin's door (the wikilinks pattern:
each plugin says its own line, no plugin speaks for another): the CLI verbs and the
question each answers. The depth is in `tracer sessions <verb> -h`, `tracer trace <verb> -h` and in each output's
closing `next:` line; this only says the verbs exist and when to reach for them.

It also puts skills/index/bin first on the Bash tool's PATH, through CLAUDE_ENV_FILE: the short names
the skills write, `sessions …` and `trace …` (each runs bin/tracer with that group). Claude Code appends a
plugin's bin/ after /usr/bin, where macOS has a `trace` of its own, so only the front of PATH reaches ours.
"""
import json
import os
import shlex
import sys
from pathlib import Path

from _brand import utf8_stdio

SHORT_NAMES = Path(__file__).resolve().parent.parent / "skills" / "index" / "bin"


def bash_path(p: Path) -> str:
    """P as the Bash tool's shell reads it: Git Bash on Windows spells C:\\x as /c/x."""
    s = str(p)
    if os.name == "nt" and len(s) > 1 and s[1] == ":":
        s = "/" + s[0].lower() + s[2:].replace("\\", "/")
    return s


def put_short_names_first(env_file: str) -> None:
    """Append the PATH line to ENV_FILE, which Claude Code sources before every Bash call of the session.
    A resume, /clear or compaction runs this hook again: the line is written once."""
    line = f'export PATH={shlex.quote(bash_path(SHORT_NAMES))}:"$PATH"\n'
    try:
        with open(env_file, "a+", encoding="utf-8", newline="\n") as f:  # bash sources it: a \r would end up in PATH
            f.seek(0)
            if line not in f.read():
                f.write(line)
    except OSError:
        pass


def command(verb: str = "") -> str:
    """This plugin's own command: bin/tracer, on the Bash tool's PATH while the plugin is enabled, with or
    without the `ak` CLI (plugins/AGENTS.md §6). The hooks run on the standard library, so they spell it here."""
    return f"tracer {verb}".strip()


TEACH = (
    "[tracer] Claude Code sessions from the shell, read from ~/.claude for you: "
    f"`{command('sessions live')}` (every open session: what it does now) · "
    f"`{command('sessions')}` (this project's, oldest first: where we stand) · "
    f"`{command('sessions show')} <session>` (its turns and steps) · "
    f"`{command('sessions agents')} <session>` (its subagents: calls, time, tokens) · "
    f"`{command('sessions search')} <phrase>` (which sessions said it; --role user: where the user typed it) · "
    f"`{command('sessions replay')} <session> --role user` (the user's prompts, whole) · "
    f"`{command('sessions turns')} --tool X` (across sessions) · "
    f"`{command('trace blame')} <file|folder>` (which sessions touched it) · "
    f"`{command('trace')}` (every tool call: filter by tool/outcome/time; one whole by id). "
    "A session: its id, its first 8 characters, or latest. Verbatim, never summarised."
)


def main():
    utf8_stdio()
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("hook_event_name") == "SessionStart":
        if os.environ.get("CLAUDE_ENV_FILE"):
            put_short_names_first(os.environ["CLAUDE_ENV_FILE"])
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": TEACH}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
