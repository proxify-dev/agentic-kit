"""Which plugin a tool call, a skill or an agent came from — read off the machine, not a list.

Claude Code names a plugin's MCP tool `mcp__plugin_<P>_<K>__<tool>`: P the plugin's manifest
name, K its server key, every character outside A-Za-z0-9_- turned into `_`. The `_` between
P and K cannot be split back from the string (both may hold `_`), so attribution is a lookup:
every plugin this machine knows of — installed, cached at an old version, listed by a
marketplace — gives the exact prefix `normalise("plugin:P:K")`. Skills are `<P>:<name>` (the
Skill input may be the bare name) and plugin agents `<P>:<sub…>:<agent>`; a bare name is
resolved against the session's own skill_listing / agent_listing_delta.

Every answer says how sure it is:

    exact        the prefix, skill or agent is in the catalog under one plugin
    inferred     not in the catalog: split at the first `_` after `plugin_` (right for
                 kebab-case names), or the plugin half of `P:name` taken as given
    ambiguous    two plugins give the same prefix or own the same bare name — candidates listed
    configured   an MCP server the user configured (mcp__<K>__), a claude.ai connector or a
                 Claude Code built-in server — not a plugin
    none         not a plugin's: a built-in tool, a user skill, a built-in agent

A bare skill or agent name is the session's listing first, then the user's own
(<claude_home>/skills, commands, agents — no plugin's), then the catalog (inferred). A `P:name`
whose P has a `/` (a nested project skill) or is `anthropic-skills` (claude.ai) is no plugin's.

Where it reads (under $AK_TRACER_PLUGINS_DIR, default <claude_home>/plugins): installed_plugins.json,
every cache/<marketplace>/<plugin>/<version>/, marketplaces/*/ and directory marketplaces from
known_marketplaces.json (their marketplace.json), and plugin-catalog-cache.json (what a
marketplace lists, installed or not). Nothing about any one machine is written here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ._brand import claude_home, env

_NON_NAME = re.compile(r"[^A-Za-z0-9_-]")
_FM_NAME = re.compile(r"^name:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", re.M)


def normalise(s: str) -> str:
    return _NON_NAME.sub("_", s)


@dataclass(frozen=True)
class Attribution:
    plugin: str | None
    server: str = ""  # the server key (MCP), or the skill/agent name
    how: str = "none"  # exact · inferred · ambiguous · configured · none
    candidates: tuple[str, ...] = ()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _server_keys(doc) -> list[str]:
    """Server keys from an .mcp.json-shaped document: {"mcpServers": {...}} or flat."""
    if not isinstance(doc, dict):
        return []
    inner = doc.get("mcpServers")
    if isinstance(inner, dict):
        return list(inner)
    return [k for k, v in doc.items() if isinstance(v, dict) and ({"command", "url", "type"} & set(v))]


def _fm_name(md: Path) -> str:
    try:
        head = md.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return ""
    if not head.startswith("---"):
        return ""
    end = head.find("\n---", 3)
    m = _FM_NAME.search(head[3:end if end > 0 else None])
    return m.group(1).strip() if m else ""


@dataclass
class Catalog:
    servers: dict[str, set[tuple[str, str]]] = field(default_factory=dict)  # prefix -> {(P, K)}
    plugins: set[str] = field(default_factory=set)
    skills: dict[str, set[str]] = field(default_factory=dict)  # short name -> {P}
    agents: dict[str, set[str]] = field(default_factory=dict)  # short name -> {P}
    user_skills: set[str] = field(default_factory=set)  # <claude_home>/skills, commands: no plugin's
    user_agents: set[str] = field(default_factory=set)  # <claude_home>/agents
    read: int = 0  # plugin sources read

    def add(self, plugin: str, servers=(), skills=(), agents=()) -> None:
        if not plugin:
            return
        self.plugins.add(plugin)
        for k in servers:
            self.servers.setdefault(normalise(f"plugin:{plugin}:{k}"), set()).add((plugin, k))
        for s in skills:
            self.skills.setdefault(s.split(":")[-1], set()).add(plugin)
        for a in agents:
            self.agents.setdefault(a.split(":")[-1], set()).add(plugin)

    def add_dir(self, path: Path, fallback_name: str = "") -> None:
        """One plugin directory: its manifest name, server keys, skills, commands, agents."""
        if not path.is_dir():
            return
        manifest = _read_json(path / ".claude-plugin" / "plugin.json") or {}
        name = str(manifest.get("name") or fallback_name or path.name)
        servers = _server_keys(_read_json(path / ".mcp.json"))
        declared = manifest.get("mcpServers")
        for item in declared if isinstance(declared, list) else [declared]:
            if isinstance(item, dict):
                servers += _server_keys({"mcpServers": item})
            elif isinstance(item, str):
                servers += _server_keys(_read_json(path / item))
        skills, agents = [], []
        skill_dirs, command_dirs, agent_dirs = [path / "skills"], [path / "commands"], [path / "agents"]
        for key, dirs in (("skills", skill_dirs), ("commands", command_dirs), ("agents", agent_dirs)):
            extra = manifest.get(key)
            for rel in extra if isinstance(extra, list) else [extra] if isinstance(extra, str) else []:
                dirs.append(path / rel)
        for d in skill_dirs:
            for md in d.glob("*/SKILL.md") if d.is_dir() else ():
                skills.append(_fm_name(md) or md.parent.name)
        for d in command_dirs:
            for md in d.rglob("*.md") if d.is_dir() else ():
                skills.append(_fm_name(md) or md.stem)
        for d in agent_dirs:
            for md in d.rglob("*.md") if d.is_dir() else ():
                agents.append(_fm_name(md) or md.stem)
        self.add(name, servers, skills, agents)
        self.read += 1

    def add_marketplace(self, root: Path) -> None:
        """A marketplace checkout: its plugins with local sources, and the root itself when
        it is one plugin."""
        doc = _read_json(root / ".claude-plugin" / "marketplace.json") or {}
        for entry in doc.get("plugins") or []:
            if not isinstance(entry, dict):
                continue
            src = entry.get("source")
            if isinstance(src, str):
                self.add_dir((root / src).resolve(), str(entry.get("name") or ""))
            elif entry.get("name"):
                self.add(str(entry["name"]))  # a remote source: the name is still known
        if (root / ".claude-plugin" / "plugin.json").is_file():
            self.add_dir(root)

    @classmethod
    def load(cls, root: Path | str | None = None) -> "Catalog":
        root = Path(root or env("TRACER_PLUGINS_DIR") or claude_home() / "plugins")
        cat = cls()
        installed = _read_json(root / "installed_plugins.json") or {}
        for key, entries in (installed.get("plugins") or {}).items():
            for e in entries if isinstance(entries, list) else []:
                if isinstance(e, dict) and e.get("installPath"):
                    cat.add_dir(Path(e["installPath"]), key.split("@")[0])
        cache = root / "cache"
        if cache.is_dir():
            for version_dir in cache.glob("*/*/*"):
                cat.add_dir(version_dir, version_dir.parent.name)
        mkts = root / "marketplaces"
        if mkts.is_dir():
            for m in mkts.iterdir():
                cat.add_marketplace(m)
        known = _read_json(root / "known_marketplaces.json") or {}
        for entry in known.values() if isinstance(known, dict) else []:
            src = (entry or {}).get("source") if isinstance(entry, dict) else None
            if isinstance(src, dict) and src.get("source") == "directory" and src.get("path"):
                cat.add_marketplace(Path(src["path"]))
        # the user's own skills and agents: a bare name found here belongs to no plugin
        home = root.parent
        cat.user_skills = {_fm_name(md) or md.parent.name for md in (home / "skills").glob("*/SKILL.md")}
        cat.user_skills |= {md.stem for md in (home / "commands").rglob("*.md")} if (home / "commands").is_dir() else set()
        cat.user_agents = {_fm_name(md) or md.stem for md in (home / "agents").rglob("*.md")} if (home / "agents").is_dir() else set()
        listed = ((_read_json(root / "plugin-catalog-cache.json") or {}).get("catalog") or {}).get("plugins") or {}
        for key, entry in listed.items() if isinstance(listed, dict) else []:
            comps = (entry or {}).get("components") or {}
            names = lambda kind: [c.get("name") if isinstance(c, dict) else c for c in comps.get(kind) or []]  # noqa: E731
            cat.add(str(entry.get("plugin") or key.split("@")[0]),
                    [n for n in names("mcpServers") if n],
                    [n for n in names("skills") + names("commands") if n],
                    [n for n in names("agents") if n])
        return cat

    # ── answers ──────────────────────────────────────────────────────────────

    def mcp(self, tool_name: str) -> Attribution:
        if not tool_name.startswith("mcp__"):
            return Attribution(None, "", "none")
        server = tool_name[5:].partition("__")[0]
        if server.startswith("plugin_"):
            hits = self.servers.get(server)
            if hits and len(hits) == 1:
                p, k = next(iter(hits))
                return Attribution(p, k, "exact")
            if hits:
                return Attribution(None, server, "ambiguous", tuple(sorted(f"{p}:{k}" for p, k in hits)))
            rest = server[len("plugin_"):]
            # a known plugin whose name starts the rest is a better cut than the first `_`
            known = [p for p in self.plugins if rest.startswith(normalise(p) + "_")]
            if len(known) == 1:
                p = known[0]
                return Attribution(p, rest[len(normalise(p)) + 1:], "inferred")
            if len(known) > 1:
                return Attribution(None, server, "ambiguous", tuple(sorted(known)))
            p, _, k = rest.partition("_")
            return Attribution(p, k, "inferred")
        return Attribution(None, server, "configured")

    def _named(self, name: str, listing: frozenset[str], owners_by_short: dict[str, set[str]],
               own: set[str]) -> Attribution:
        n = name.strip().lstrip("/")
        parts = [p for p in n.split(":") if p]
        if len(parts) >= 3 and parts[0] == parts[1]:
            parts = parts[1:]  # 2.1.216–2.1.245 doubled the prefix
        if not parts:
            return Attribution(None, n, "none")
        if len(parts) >= 2:
            p = parts[0]
            if p in self.plugins:
                return Attribution(p, ":".join(parts), "exact")
            # a nested project skill (`apps/web:deploy`) or a claude.ai synced one: no plugin
            if "/" in p or p == "anthropic-skills":
                return Attribution(None, ":".join(parts), "none")
            return Attribution(p, ":".join(parts), "inferred")
        short = parts[0]
        listed = {e.split(":")[0] for e in listing if ":" in e and e.split(":")[-1] == short}
        if len(listed) == 1:
            return Attribution(listed.pop(), short, "exact")
        if len(listed) > 1:
            return Attribution(None, short, "ambiguous", tuple(sorted(listed)))
        if short in listing or short in own:
            return Attribution(None, short, "none")  # a user, project or built-in one
        owners = owners_by_short.get(short, set())
        if len(owners) == 1:
            return Attribution(next(iter(owners)), short, "inferred")
        if len(owners) > 1:
            return Attribution(None, short, "ambiguous", tuple(sorted(owners)))
        return Attribution(None, short, "none")

    def skill(self, name: str, listing: frozenset[str] = frozenset()) -> Attribution:
        return self._named(name, listing, self.skills, self.user_skills)

    def agent(self, name: str, listing: frozenset[str] = frozenset()) -> Attribution:
        return self._named(name, listing, self.agents, self.user_agents)


_LOADED: dict[str, Catalog] = {}


def machine_catalog() -> Catalog:
    """The catalog of this machine's plugins, read once per process."""
    key = env("TRACER_PLUGINS_DIR") or ""
    if key not in _LOADED:
        _LOADED[key] = Catalog.load()
    return _LOADED[key]


def session_listings(messages: list[dict]) -> tuple[frozenset[str], frozenset[str]]:
    """The skills and agent types a session was offered: its skill_listing and
    agent_listing_delta attachments, names only."""
    skills: set[str] = set()
    agents: set[str] = set()
    for msg in messages:
        if msg.get("type") != "attachment":
            continue
        a = msg.get("attachment") or {}
        if a.get("type") == "skill_listing" and isinstance(a.get("content"), str):
            for line in a["content"].splitlines():
                if line.startswith("- "):
                    skills.add(line[2:].split(": ", 1)[0].strip())
        elif a.get("type") == "agent_listing_delta":
            agents.update(t for t in a.get("addedTypes") or [] if isinstance(t, str))
    return frozenset(skills), frozenset(agents)
