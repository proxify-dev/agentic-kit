"""built — "What you built": built vs borrowed, and for each thing whether it's in daily use or abandoned.

Built = a plugin from a marketplace that is a folder on this machine (the person keeps it), a skill with no
install record, an agent file in ~/.claude/agents. Borrowed = a plugin from a remote marketplace, a skill
installed through skills.sh or linked in.

Use per item, over sessions a person typed in and headless runs:
  plugin  hook text it added ([tag] matching the plugin name) + its skills, helpers and commands (plugin:name)
  skill   Skill tool calls + slash commands with its name
  agent   helper calls (subagent_type) + sessions started as it (--agent)
"""
from __future__ import annotations

from collections import Counter, defaultdict

LOGS = "session logs (~/.claude/projects/*/*.jsonl)"


def n(x):
    return f"{x:,}"


def build(scan: dict, sessions: list, typed: list) -> dict:
    S = scan["setup"]
    plugins = S["plugins"]["installed"]
    own = S["own"]

    uses = Counter()
    days = defaultdict(set)
    last = {}

    def hit(key, count, s):
        if not count:
            return
        uses[key] += count
        if s.first:
            d = s.first.astimezone().date()
            days[key].add(d)
            last[key] = max(last.get(key, d), d)

    pnames = {p["plugin"].split("@")[0] for p in plugins}
    for s in sessions:
        for src, c in s.hook_sources.items():
            tag = src.split(" · ")[0]
            if tag in pnames:
                hit(("plugin", tag), c, s)
        for name, c in list(s.skills.items()) + list(s.subagents.items()) + list(s.commands.items()):
            if ":" in name:
                ns = name.split(":")[0]
                if ns in pnames:
                    hit(("plugin", ns), c, s)
            hit(("item", name), c, s)
        if s.agent_setting:
            hit(("item", s.agent_setting), 1, s)
            if ":" in s.agent_setting and s.agent_setting.split(":")[0] in pnames:
                hit(("plugin", s.agent_setting.split(":")[0]), 1, s)

    rows = []

    def row(name, kind, made, key, on=True):
        u = uses.get(key, 0)
        d = len(days.get(key, ()))
        state = "switched off" if not on else ("daily" if d >= 10 else ("now and then" if u else "not used"))
        rows.append({"name": name, "kind": kind, "made": made, "uses": u, "days": d, "state": state,
                     "last": last[key].strftime("%b %-d") if key in last else "–"})

    # One row per plugin name: the same plugin is often listed in several marketplaces (a dev copy, a release).
    by_name = {}
    for p in plugins:
        name = p["plugin"].split("@")[0]
        prev = by_name.get(name)
        by_name[name] = {"local": (prev or {}).get("local", False) or bool(p.get("local_marketplace")),
                         "on": (prev or {}).get("on", False) or bool(p.get("enabled"))}
    for name, p in by_name.items():
        row(name, "plugin", "built" if p["local"] else "borrowed", ("plugin", name), on=p["on"])
    for sk in own["skills"]:
        made = "borrowed" if sk.get("is_symlink") else "built?"
        row(sk["name"], "skill", made, ("item", sk["name"]))
    builtin_agents = {"general-purpose", "explore", "plan", "statusline-setup", "claude-code-guide", "fork", "claude"}
    for ag in own["agents"]:
        if ag["name"].lower() in builtin_agents:
            continue   # a file named like a built-in: its uses are the built-in's, not this person's work
        row(ag["name"], "agent", "built?", ("item", ag["name"]))

    built = [r for r in rows if r["made"].startswith("built")]
    borrowed = [r for r in rows if r["made"] == "borrowed"]
    b_daily = [r for r in built if r["state"] == "daily"]
    b_dead = [r for r in built if r["state"] == "not used"]
    b_off = [r for r in built if r["state"] == "switched off"]
    o_daily = [r for r in borrowed if r["state"] == "daily"]

    order = {"built": 0, "built?": 1, "borrowed": 2}
    table_rows = [[r["name"], r["kind"], {"built": "built by you", "built?": "no install record", "borrowed": "borrowed"}[r["made"]]
                   + (" · off" if r["state"] == "switched off" else ""), n(r["uses"]), str(r["days"]), r["last"]]
                  for r in sorted(rows, key=lambda r: (order[r["made"]], -r["uses"]))[:25]]

    def names(rs, k=8):
        return ", ".join(f"{r['name']} ({n(r['uses'])})" for r in rs[:k])

    b_daily.sort(key=lambda r: -r["uses"])
    o_daily.sort(key=lambda r: -r["uses"])
    return {
        "id": "built", "title": "What you built for yourself",
        "lead": f"You built {sum(1 for r in built if r['kind'] == 'plugin')} plugins and have {len(built) - sum(1 for r in built if r['kind'] == 'plugin')} "
                f"skills and agents with no install record; {len(b_daily)} are in daily use, {len(b_dead)} are switched on but unused, "
                f"{len(b_off)} are switched off. Of {len(borrowed)} borrowed, {len(o_daily)} are daily.",
        "how": {"source": "files on disk (plugin registry, marketplaces, ~/.claude/skills, ~/.agents/.skill-lock.json, ~/.claude/agents); " + LOGS,
                "rule": "built = plugin from a folder marketplace on this machine, skill with no install record, own agent file; "
                        "use = hook text tagged with its name + calls to its skills, helpers, commands; daily = used on 10+ days",
                "raw": f"{len(rows)} items: {len(built)} built, {len(borrowed)} borrowed",
                "confidence": "heuristic",
                "caveat": "a plugin's hooks that run silently (no text added) are not counted as use; a skill copied in by hand from "
                          "someone else shows as 'no install record'",
                "code": "built.build"},
        "points": [
            {"label": "Built, in daily use", "text": "Built and in daily use: " + (names(b_daily) or "none") + ".",
             "how": {"source": LOGS, "rule": "built items used on 10+ different days", "raw": str(len(b_daily)), "confidence": "heuristic"}},
            {"label": "Built, on but unused", "text": "Switched on but not used this month: " + (", ".join(r["name"] for r in b_dead) or "none") + ".",
             "how": {"source": LOGS, "rule": "built items, switched on, with zero uses in the window", "raw": str(len(b_dead)), "confidence": "heuristic",
                     "caveat": "a plugin whose hooks never add text looks unused even if it runs"}},
            {"label": "Built, switched off", "text": "Switched off: " + (", ".join(r["name"] for r in b_off) or "none") + ".",
             "how": {"source": "settings.json enabledPlugins", "rule": "built plugins not enabled", "raw": str(len(b_off)), "confidence": "solid"}},
            {"label": "Borrowed, in daily use", "text": "Borrowed and in daily use: " + (names(o_daily) or "none") + ".",
             "how": {"source": LOGS, "rule": "borrowed items used on 10+ different days", "raw": str(len(o_daily)), "confidence": "heuristic"}},
        ],
        "numbers": [{"value": str(len(built)), "label": "things you built (or have no install record)"},
                    {"value": str(len(b_daily)), "label": "built and used daily"},
                    {"value": str(len(b_dead)), "label": "switched on, not used"},
                    {"value": str(len(b_off)), "label": "switched off"},
                    {"value": str(len(borrowed)), "label": "borrowed"}],
        "table": {"caption": "Built vs borrowed, by use", "columns": ["Name", "Kind", "Made by", "Uses", "Days used"],
                  "rows": [r[:5] for r in table_rows]},
        "_coverage": {"read": [f"{n(len(rows))} plugins, skills and agents found on disk"]},
    }
