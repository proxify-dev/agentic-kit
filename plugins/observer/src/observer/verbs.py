"""How every text observer prints names a verb: its own command, `observer`.

`bin/observer` runs this plugin's console script, and Claude Code puts an enabled plugin's bin/ on the Bash
tool's PATH, so `observer search` answers in every session the plugin is on, with or without the `ak` CLI
(plugins/AGENTS.md §6). Inside the kit the same group is also mounted as `ak observer`, for a person.
"""
from __future__ import annotations

COMMAND = "observer"  # this plugin's command: bin/observer, and its console script


def command(verb: str = "") -> str:
    """VERB as a shell types it: `observer search`."""
    return f"{COMMAND} {verb}".strip()
