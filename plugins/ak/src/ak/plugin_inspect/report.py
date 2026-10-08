"""The two views, as plain data: the ranking (`list`) and one plugin's card (`inspect`).

Both join what a plugin is wired to do (installed.py) with what it said (heard.py).
The join key is (event, command): a transcript's hook record carries the command
exactly as hooks.json wrote it, ${CLAUDE_PLUGIN_ROOT} unexpanded.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from ak.plugin_inspect.heard import TO_MODEL, Heard
from ak.plugin_inspect.installed import Hook, Plugin


def _gist(texts: list[str], opening: int = 72) -> str:
    """What it says most often, whole. Built-at-runtime text varies, so texts are grouped by
    their opening, and the group's newest text speaks for it — never cut."""
    said = [t for t in texts if t.strip()]
    if not said:
        return ""
    key = lambda t: " ".join(t.split())[:opening]  # noqa: E731
    top = Counter(key(t) for t in said).most_common(1)[0][0]
    return next(t for t in reversed(said) if key(t) == top).strip()


def _script(command: str) -> str:
    """The part of a hook command a person recognises: the last script it names."""
    words = command.replace('"', " ").split()
    base = lambda w: re.split(r"[/\\]", w)[-1]  # noqa: E731  either slash: a Windows command too
    named = [w for w in words if re.search(r"[/\\]", w) and "." in base(w)]
    return base(named[-1]) if named else (words[0] if words else "")


def ranking(installed: list[Plugin], heard: list[Heard], sessions: int) -> list[dict]:
    """One row a plugin, loudest first. Quiet enabled plugins are listed too: silence is an answer."""
    by: dict[str, list[Heard]] = defaultdict(list)
    for h in heard:
        by[h.plugin].append(h)
    names = {p.name for p in installed if p.enabled} | set(by)
    meta: dict[str, Plugin] = {}
    for p in installed:  # one name from two marketplaces: the enabled copy speaks for it
        if p.name not in meta or (p.enabled and not meta[p.name].enabled):
            meta[p.name] = p
    rows = []
    for name in names:
        hs = by.get(name, [])
        tok = sum(h.tokens for h in hs)
        per_hook: dict[str, int] = defaultdict(int)
        fires: Counter = Counter()
        for h in hs:
            per_hook[h.hook] += h.tokens
            fires[h.hook] += 1
        loud = max(per_hook, key=lambda k: per_hook[k]) if per_hook and max(per_hook.values()) else ""
        p = meta.get(name)
        rows.append({
            "plugin": name,
            "enabled": bool(p and p.enabled),
            "installed": p is not None,
            "sessions_heard": len({h.session for h in hs}),
            "tokens_per_session": round(tok / sessions) if sessions else 0,
            "blocked": sum(h.kind in ("block", "deny") for h in hs),
            "errors": sum(h.kind == "error" for h in hs),
            "broken": sum(1 for hk in (p.hooks if p else []) if hk.missing()),
            "slowest_ms": max((h.ms or 0 for h in hs), default=0),
            "loudest": loud,
            "loudest_fires_per_session": round(fires[loud] / sessions, 1) if loud and sessions else 0,
        })
    return sorted(rows, key=lambda r: (-r["tokens_per_session"], -r["blocked"], -r["errors"], r["plugin"]))


def _hook_row(event: str, matcher: str, command: str, hs: list[Heard], sessions: int, missing: list[str]) -> dict:
    said = [h for h in hs if h.kind != "silent"]
    kinds = Counter(h.kind for h in said)
    tok = [h.tokens for h in said if h.kind in TO_MODEL]
    return {
        "event": event,
        "matcher": matcher,
        "script": _script(command),
        "command": command,
        "fires": len(hs),
        "sessions": len({h.session for h in hs}),
        "of_sessions": sessions,
        "tokens_avg": round(sum(tok) / len(tok)) if tok else 0,
        "tokens_total": sum(tok),
        "kinds": dict(kinds),
        "says": _gist([h.text for h in said]),
        "slowest_ms": max((h.ms or 0 for h in hs), default=0),
        "missing": missing,
    }


def card(p: Plugin | None, name: str, heard: list[Heard], sessions: int) -> dict:
    """Everything one plugin can put in front of the agent, each wire next to how it behaved."""
    mine = [h for h in heard if h.plugin == name]
    by_wire: dict[tuple[str, str], list[Heard]] = defaultdict(list)
    for h in mine:
        by_wire[(h.event, h.command)].append(h)
    hooks: list[Hook] = p.hooks if p else []
    wired = []
    for hk in hooks:
        wired.append(_hook_row(hk.event, hk.matcher, hk.command, by_wire.pop((hk.event, hk.command), []),
                               sessions, hk.missing()))
    # heard, but not wired in the installed version: an older version, or a settings.json hook
    gone = [_hook_row(ev, "", cmd, hs, sessions, []) for (ev, cmd), hs in by_wire.items()]
    return {
        "plugin": name,
        "key": p.key if p else name,
        "version": p.version if p else "",
        "enabled": bool(p and p.enabled),
        "installed": p is not None,
        "path": str(p.path) if p else "",
        "sessions": sessions,
        "hooks": wired,
        "unwired": sorted(gone, key=lambda r: -r["fires"]),
        "skills": [{"name": n, "description": d} for n, d in (p.skills if p else [])],
        "agents": [{"name": n, "description": d} for n, d in (p.agents if p else [])],
        "commands": p.commands if p else [],
        "mcp": p.mcp if p else [],
        "tokens_per_session": round(sum(h.tokens for h in mine) / sessions) if sessions else 0,
    }
