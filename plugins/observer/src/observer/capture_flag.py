#!/usr/bin/env python3
"""The session observer's off switch — CLI half.

The observer is the three capture hooks (session-register · capture-event ·
session-end) that turn each session into notes under memory/observations/. When
another memory system is already recording the same sessions, you want one of
them quiet — this is that dial, without uninstalling the plugin or hand-editing
hooks.json.

ONE FILE IS THE WHOLE CONTRACT, and it is read by code that cannot import this
module: hooks run under bare system python3, outside the installed `ak`
package. The resolver is therefore mirrored in the plugin's
hooks/capture_common.py — deliberately. Change the semantics here, change them
there; the format stays trivial so the two halves cannot drift far.

    <kit>/db/capture.json   (the kit's — one switch file for every scope)
    {"enabled": true, "disabled_paths": ["/abs/dir", ...], "updated": "<iso>"}

Resolution — first match wins:
    1. AK_CAPTURE_DISABLE in env → off (the summarizer's own recursion guard)
    1b. a program started it        → off (CLAUDE_CODE_ENTRYPOINT not cli · claude-desktop ·
                                           sdk-ts; AK_CAPTURE=1 overrides)
    2. no capture.json              → on  (default; unchanged behaviour)
    3. capture.json unparseable     → off (a present-but-broken switch means someone
                                           configured this — never write memory we
                                           may have been told not to)
    4. enabled == false             → off (globally)
    5. cwd inside a disabled path   → off (per-project)
    6. otherwise                    → on

What it does NOT touch: recall (`ak search`, `observer search`), the
SessionStart digest of past observations, the episodic index, and already
captured notes. This silences the writer only.
"""
import json
import sys
import time
from pathlib import Path

# The resolver is the vault plugin's hooks/vault_root.py, vendored into this plugin's
# hooks/ (scripts/copies.py writes and checks the copy).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
from vault_root import db_dir, home_root  # noqa: E402

from ._brand import env, env_name
from .verbs import command

CONFIG_NAME = "capture.json"


def global_vault() -> Path:
    """The global vault: the kit `ak init` made, which the dial belongs to."""
    return Path(home_root())


def switch_dir() -> Path:
    """Where capture.json lives — the kit's db/, the one the hooks read (capture_common.capture_config_path):
    the observer is one log for every project, so it has one dial."""
    return Path(db_dir())


def config_path(root: Path | None = None) -> Path:
    """ROOT is the directory holding capture.json (default: switch_dir())."""
    return (root or switch_dir()) / CONFIG_NAME


def _within(child: str, parent: str) -> bool:
    """True if `child` is `parent` or lives under it. Symlink-resolved, never raises."""
    try:
        c = Path(child).expanduser().resolve()
        p = Path(parent).expanduser().resolve()
    except Exception:
        return False
    return c == p or p in c.parents


def load(root: Path | None = None) -> dict:
    """The config as written, or {} when absent. Raises on malformed JSON — callers
    that must not fail (the hooks' mirror) treat that as 'off'."""
    path = config_path(root)
    if not path.exists():
        return {}
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("capture.json is not a JSON object")
    return cfg


def save(cfg: dict, root: Path | None = None) -> Path:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


from session_origin import automated as automated_entrypoint  # noqa: E402  (hooks/session_origin.py)


def state(cwd: str | None = None, root: Path | None = None) -> tuple[bool, str]:
    """(enabled, reason) for `cwd`. Mirrors hooks/capture_common.capture_state."""
    if env("CAPTURE_DISABLE"):
        return False, f"{env_name('CAPTURE_DISABLE')} is set in the environment"
    if ep := automated_entrypoint():
        return False, f"off — an automated session (entrypoint {ep}); {env_name('CAPTURE')}=1 records it"
    path = config_path(root)
    if not path.exists():
        return True, "on — no capture.json, default"
    try:
        cfg = load(root)
    except Exception as e:
        return False, f"off — {path} is unreadable ({e}); fix or delete it"
    if cfg.get("enabled") is False:
        return False, f"off — muted everywhere (`{command('unmute')}` to resume)"
    for entry in cfg.get("disabled_paths") or []:
        if isinstance(entry, str) and cwd and _within(cwd, entry):
            return False, f"off — muted for {entry} (`{command('unmute --here')}`)"
    return True, "on"


def set_global(enabled: bool, root: Path | None = None) -> Path:
    cfg = load(root)
    cfg["enabled"] = bool(enabled)
    return save(cfg, root)


def set_path(path: str, enabled: bool, root: Path | None = None) -> tuple[Path, bool]:
    """Add/remove one directory from the per-project deny list.
    Returns (config path, changed?) — `changed` is False when it was already so."""
    target = str(Path(path).expanduser().resolve())
    cfg = load(root)
    paths = [p for p in (cfg.get("disabled_paths") or []) if isinstance(p, str)]
    if enabled:
        # re-enable: drop this dir and any parent rule that would still shadow it,
        # otherwise `capture on --here` would silently not take effect
        kept = [p for p in paths if not _within(target, p)]
        changed = len(kept) != len(paths)
        cfg["disabled_paths"] = kept
    else:
        changed = not any(_within(target, p) for p in paths)
        if changed:
            cfg["disabled_paths"] = paths + [target]
    return save(cfg, root), changed
