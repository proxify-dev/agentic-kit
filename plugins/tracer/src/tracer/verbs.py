"""How every text the tracer prints names a verb: its own command, `tracer` (or `ak`, when mounted there).

`bin/tracer` runs this plugin's console script, and Claude Code puts an enabled plugin's bin/ on the Bash
tool's PATH, so `tracer sessions show` answers in every session the plugin is on, with or without the
`ak` CLI (plugins/AGENTS.md §6). Inside the kit the same groups are also mounted as `ak sessions` and
`ak trace`, for a person; a hint, an epilog, the SessionStart line and the skills say `tracer …`.

One table of the verbs a hint names by key:

    key       the verb
    ls        tracer sessions ls
    all       tracer sessions ls --all
    live      tracer sessions live
    show      tracer sessions show
    agents    tracer sessions agents
    replay    tracer sessions replay
    search    tracer sessions search
    turn      tracer trace turn
    blame     tracer trace blame
    index     tracer trace index
"""
from __future__ import annotations

def _typed() -> str:
    """The command this process answers to: `ak` when these groups run mounted in the kit's CLI (a person's
    terminal, where `tracer` is not on PATH), else `tracer`. A hint names what the person can type next."""
    import os
    import sys

    from ._brand import CLI
    name = os.path.basename(sys.argv[0] if sys.argv else "")
    return CLI if name in (CLI, f"{CLI}.exe") else "tracer"


COMMAND = _typed()  # this plugin's command: bin/tracer and its console script, or `ak` when mounted there

HOMES = {
    "ls": "sessions ls",
    "all": "sessions ls --all",
    "live": "sessions live",
    "show": "sessions show",
    "agents": "sessions agents",
    "replay": "sessions replay",
    "search": "sessions search",
    "turn": "trace turn",
    "blame": "trace blame",
    "index": "trace index",
}


def command(verb: str = "") -> str:
    """VERB as a shell types it: `tracer sessions show`."""
    return f"{COMMAND} {verb}".strip()


def spell(key: str) -> str:
    """The verb KEY (HOMES) as a shell types it."""
    return command(HOMES[key])
