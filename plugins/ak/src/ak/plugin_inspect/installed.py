"""What is installed, and what each plugin is wired to do — read from disk, nothing is run.

Claude Code keeps the list in `<claude>/plugins/installed_plugins.json` (name@marketplace →
installPath, version) and the on/off switch in settings.json `enabledPlugins`. A plugin's
wiring is its hooks.json, its skills/, agents/, commands/, and its MCP servers.
`<claude>` is the seam's claude_home(): CLAUDE_CONFIG_DIR, else ~/.claude — Claude Code's own rule.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from ak._brand import claude_home, is_windows

HOOK_SCRIPT = re.compile(r"\.(py|sh|js|mjs|cjs|ts|rb)$")


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _obj(path: Path) -> dict:
    """PATH's JSON object; {} when it is missing, unreadable, or not an object (a hand-edited registry)."""
    d = _json(path)
    return d if isinstance(d, dict) else {}


def _words(cmd: str) -> list[str]:
    """A command line's words. Windows keeps backslashes: POSIX shlex would eat them from C:\\x\\hook.py."""
    if not is_windows():
        return shlex.split(cmd)
    return [w[1:-1] if len(w) > 1 and w[0] == w[-1] and w[0] in "\"'" else w for w in shlex.split(cmd, posix=False)]


@dataclass
class Hook:
    event: str
    matcher: str
    command: str
    root: str = ""  # what ${CLAUDE_PLUGIN_ROOT} expands to; "" for a settings.json hook

    def missing(self) -> list[str]:
        """The scripts this command runs that are not on disk: a hook that fails every time it fires."""
        cmd = self.command.replace("${CLAUDE_PLUGIN_ROOT}", self.root).replace("$CLAUDE_PLUGIN_ROOT", self.root)
        try:
            words = _words(cmd)
        except ValueError:
            return []
        out = []
        for w in words:
            if "$" in w or not os.path.isabs(w):
                continue  # only absolute paths can be checked without running anything
            if HOOK_SCRIPT.search(w) or (self.root and w.startswith(self.root)):
                if not Path(w).exists():
                    out.append(w)
        return out


@dataclass
class Plugin:
    name: str
    marketplace: str
    version: str
    path: Path
    enabled: bool
    hooks: list[Hook] = field(default_factory=list)
    skills: list[tuple[str, str]] = field(default_factory=list)  # (name, description)
    agents: list[tuple[str, str]] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    mcp: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.name}@{self.marketplace}"


def _front(path: Path) -> dict[str, str]:
    """name/description from a markdown file's frontmatter, one-line values only."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    out: dict[str, str] = {}
    key = None  # a `description: >-` block: its indented lines are the value
    for line in text.split("\n")[1:]:
        if line.strip() == "---":
            break
        if key and line[:1] in (" ", "\t"):
            out[key] = (out[key] + " " + line.strip()).strip()
            continue
        key = None
        m = re.match(r"^(name|description):\s*(.*)$", line)
        if m:
            value = m.group(2).strip()
            if value in (">", ">-", "|", "|-", ">+", "|+"):
                key, value = m.group(1), ""
            out[m.group(1)] = value.strip("\"'")
    return out


def read_hooks(doc, root: str) -> list[Hook]:
    out = []
    for event, groups in ((doc or {}).get("hooks") or {}).items():
        for g in groups or []:
            for h in g.get("hooks") or []:
                if h.get("type", "command") == "command" and h.get("command"):
                    out.append(Hook(event, g.get("matcher", "") or "", h["command"].strip(), root))
    return out


def _wiring(p: Plugin) -> None:
    manifest = _json(p.path / ".claude-plugin" / "plugin.json") or _json(p.path / "plugin.json") or {}
    root = str(p.path)
    p.hooks = read_hooks(_json(p.path / "hooks" / "hooks.json"), root)
    inline = manifest.get("hooks")
    if isinstance(inline, dict):
        p.hooks += read_hooks(inline, root)
    elif isinstance(inline, str):
        p.hooks += read_hooks(_json(p.path / inline), root)
    for f in sorted(p.path.glob("skills/*/SKILL.md")):
        fm = _front(f)
        p.skills.append((fm.get("name") or f.parent.name, fm.get("description", "")))
    for f in sorted(p.path.glob("agents/*.md")):
        fm = _front(f)
        p.agents.append((fm.get("name") or f.stem, fm.get("description", "")))
    p.commands = [f.stem for f in sorted(p.path.glob("commands/*.md"))]
    servers = (_json(p.path / ".mcp.json") or {}).get("mcpServers") or {}
    if isinstance(manifest.get("mcpServers"), dict):
        servers = {**servers, **manifest["mcpServers"]}
    p.mcp = sorted(servers)


def enabled(cwd: Path | None) -> dict[str, bool]:
    """enabledPlugins, user settings first, then the project's own (which win); {} when no file names one.
    `ak` mounts a plugin's verbs only when it is on here (cli.py `_enabled_ids`)."""
    files = [claude_home() / "settings.json"]
    if cwd:
        files += [cwd / ".claude" / "settings.json", cwd / ".claude" / "settings.local.json"]
    on: dict[str, bool] = {}
    for f in files:
        said = _obj(f).get("enabledPlugins")
        on.update(said if isinstance(said, dict) else {})
    return on


def _real(p) -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(p))))


def ids_for(root: Path, name: str) -> list[str]:
    """The ids Claude Code knows the plugin folder ROOT (named NAME) by, whatever the marketplace:
    an installed_plugins.json entry whose installPath IS this folder (a cache copy, CLAUDE_PLUGIN_ROOT),
    else `NAME@<m>` for every marketplace in known_marketplaces.json whose folder holds ROOT (the
    release clone a directory marketplace points at, a github one's clone). [] when nothing on disk
    says which: the caller falls back to any `NAME@…` in enabledPlugins."""
    here = _real(root)
    pl = claude_home() / "plugins"
    inst = _obj(pl / "installed_plugins.json").get("plugins")
    exact = [k for k, entries in (inst if isinstance(inst, dict) else {}).items() if isinstance(entries, list)
             and any(isinstance(e, dict) and e.get("installPath") and _real(e["installPath"]) == here for e in entries)]
    if exact:
        return sorted(exact)
    out = []
    for mkt, rec in _obj(pl / "known_marketplaces.json").items():
        if not isinstance(rec, dict):
            continue
        src = rec.get("source")
        where = {rec.get("installLocation"), src.get("path") if isinstance(src, dict) else None}
        for d in [w for w in where if isinstance(w, str) and w]:
            base = _real(d)
            if here == base or here.startswith(base.rstrip(os.sep) + os.sep):
                out.append(f"{name}@{mkt}")
                break
    return sorted(out)


def is_on(root: Path, name: str, on: dict[str, bool]) -> bool:
    """Plugin folder ROOT is switched on in ON (enabled(), every settings file merged, a later one per id
    winning): its own ids (ids_for) when disk names them, else any `NAME@<marketplace>` key. Several
    ids, several marketplaces: on when any of them is true."""
    ids = ids_for(root, name) or [k for k in on if isinstance(k, str) and k.split("@", 1)[0] == name]
    return any(on.get(i) is True for i in ids)


def plugins(cwd: Path | None = None) -> list[Plugin]:
    """Every installed plugin with its wiring, enabled ones first."""
    doc = _json(claude_home() / "plugins" / "installed_plugins.json") or {}
    on = enabled(cwd if cwd is not None else Path.cwd())
    out = []
    for key, entries in (doc.get("plugins") or {}).items():
        if not entries:
            continue
        e = next((x for x in entries if x.get("scope") == "user"), entries[0])
        name, _, mkt = key.partition("@")
        p = Plugin(name, mkt, str(e.get("version", "")), Path(e.get("installPath", "")), bool(on.get(key)))
        if p.path.is_dir():
            _wiring(p)
        out.append(p)
    return sorted(out, key=lambda p: (not p.enabled, p.name))


def settings_hooks(cwd: Path | None = None) -> list[Hook]:
    """Hooks written straight into settings.json: no plugin owns them, and nothing updates them."""
    files = [claude_home() / "settings.json"]
    if cwd:
        files += [cwd / ".claude" / "settings.json", cwd / ".claude" / "settings.local.json"]
    out = []
    for f in files:
        out += read_hooks(_json(f), "")
    return out


def history_index() -> dict[tuple[str, str], set[str]]:
    """(event, command) → plugin names, over every version in the plugin cache.

    Past sessions ran past versions: a hook renamed since still belongs to its plugin."""
    idx: dict[tuple[str, str], set[str]] = {}
    for f in (claude_home() / "plugins" / "cache").glob("*/*/*/hooks/hooks.json"):
        name = f.parents[2].name
        for h in read_hooks(_json(f), ""):
            idx.setdefault((h.event, h.command), set()).add(name)
    return idx
