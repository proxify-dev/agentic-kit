"""The real `claude`, asked to change plugins — the way scripts/migrate/3-split.sh's cl() asks it.

From $HOME (a project's own settings cannot answer for the user's), not as an agent (no CLAUDECODE, or it
refuses to start inside a session), and past the shim (CC_SHIM_DISABLE=1: the shim may not be installed
yet, and a half-wired one must not be what setup runs through).

Nothing here runs anything: each function says what to run, as a Call. The engine runs it, and keeps
the Call of the inverse as the step's undo.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ak._brand import claude_home, which_exe

SCOPE = "user"
PAST_THE_SHIM = {"CC_SHIM_DISABLE": "1"}


@dataclass(frozen=True)
class Call:
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)  # what to add to the environment
    cwd: str | None = None

    def undo(self) -> dict:
        """As a backup.py exec undo, run outside a session like the call itself."""
        return {"kind": "exec", "argv": self.argv, "env": self.env, "unset": ["CLAUDECODE"], "cwd": self.cwd}


def outside_a_session() -> dict:
    """The environment the real claude is run in: not an agent's (no CLAUDECODE), not through the shim."""
    return {**{k: v for k, v in os.environ.items() if k != "CLAUDECODE"}, **PAST_THE_SHIM}


def _call(exe: str | None, *args: str) -> Call:
    """exe: the claude detect() found; else the one on PATH."""
    return Call([exe or which_exe("claude") or "claude", *args], dict(PAST_THE_SHIM), str(Path.home()))


def marketplace_add(path: str, exe: str | None = None) -> Call:
    return _call(exe, "plugin", "marketplace", "add", path)


def marketplace_remove(name: str, exe: str | None = None) -> Call:
    return _call(exe, "plugin", "marketplace", "remove", name)


def install(plugin_id: str, exe: str | None = None) -> Call:
    return _call(exe, "plugin", "install", plugin_id, "-s", SCOPE)


def uninstall(plugin_id: str, exe: str | None = None) -> Call:
    """Keeps the plugin's data folder: an undo takes back the install, not what the plugin wrote since."""
    return _call(exe, "plugin", "uninstall", plugin_id, "-s", SCOPE, "--keep-data")


def install_path(plugin_id: str) -> str | None:
    """Where Claude Code runs an installed plugin from (CLAUDE_PLUGIN_ROOT for its hooks): its installPath in
    <claude home>/plugins/installed_plugins.json, the user-scope install first. None when it is not there."""
    try:
        doc = json.loads((Path(claude_home()) / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
        rows = doc.get("plugins", {}).get(plugin_id) or []
    except (OSError, ValueError, AttributeError):
        return None
    rows = [r for r in rows if isinstance(r, dict) and r.get("installPath")] if isinstance(rows, list) else []
    rows.sort(key=lambda r: r.get("scope") != SCOPE)
    return rows[0]["installPath"] if rows else None
