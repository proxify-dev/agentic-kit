"""kinds — the two moves that are not a workspace: a plugin (or marketplace) rename, and the kit's own home.

Both plan into the same `Move` the workspace move uses and run on the same engine: journaled, undoable
from any step, refused while a Claude Code session is open, the same lineage record.

    plugin rename <p>@<m> <p2>@<m2>          one plugin id
    plugin rename --marketplace <old> <new>  a marketplace and every plugin id under it

  What a plugin id is written into, all of it re-pointed together (Claude Code keeps no other copy):
    settings.json / settings.local.json   enabledPlugins keys (and extraKnownMarketplaces keys)
    plugins/installed_plugins.json        the key, and each installPath
    plugins/known_marketplaces.json       the key and installLocation (marketplace rename)
    plugins/cache/<m>/<p>                 the install folders
    plugins/data/<p>-<m>                  the plugin's own data folder

    home move <new>                        the kit's state home (today ~/.ak)

  The folder is renamed on the same disk and a symlink is left at the old path until `moves finalize`
  (`ops.move_dir`); every pointer to it — settings env (WIKILINKS_ROOTS), ~/.claude.json, chat-history
  folders, the registries inside the home, Obsidian's vault list — follows through the workspace
  handlers, which already keep a `~/` form a `~/` form. The gateway is not part of this move.

Nothing here writes: `plugin_rename()` and `home_move()` return the Move; the CLI checks the machine is
quiet, freezes the engine and runs it (cli._execute).
"""
from __future__ import annotations

import glob
import json
import os
import re

from ak._brand import HOME_DIR, cmd, config_dir, env, is_macos
from ak.move import ops
from ak.move import plan as P


def _read(p: str):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _ident(s: str) -> str:
    """Claude Code's folder name for an id part under plugins/data."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", s)


def _split(pid: str):
    p, _, m = pid.partition("@")
    return (p, m) if p and m and "@" not in m else (None, None)


def _guard_steps(m: P.Move):
    for s in m.steps:
        for k in ("src", "dst", "path", "file", "db", "link"):
            if s["args"].get(k):
                try:
                    ops.guard(s["args"][k])
                except ops.Refused as e:
                    m.fail("gateway", str(e))


# ── plugin / marketplace rename ─────────────────────────────────────────────
def _under_cache(p: str, roots: dict) -> str:
    for a, b in roots.items():
        if ops.under(p, a):
            return ops.remap(p, a, b)
    return p


def plugin_rename(old: str, new: str, marketplace: bool = False) -> P.Move:
    m = P.Move(id=P.new_id(old, new), old=old, new=new, home=P.kit_home(), claude=P.claude_dir(),
               claude_json=P.claude_json(), kind="marketplace" if marketplace else "plugin")
    pl = os.path.join(m.claude, "plugins")
    installed_f = os.path.join(pl, "installed_plugins.json")
    known_f = os.path.join(pl, "known_marketplaces.json")
    settings_fs = [os.path.join(m.claude, n) for n in ("settings.json", "settings.local.json")]
    installed = _read(installed_f) or {}
    plugins = installed.get("plugins") if isinstance(installed.get("plugins"), dict) else {}
    known = _read(known_f) or {}

    # the id pairs to rename, and the folder roots that move with them
    pairs: dict = {}  # old id → new id
    dirs: list = []   # (src, dst)
    if marketplace:
        if not old or not new or "@" in old + new or old == new:
            m.fail("rename", "a marketplace name is one word, and the new one must differ")
            return m
        ids = set(k for k in plugins if k.endswith("@" + old))
        for f in settings_fs:
            ep = (_read(f) or {}).get("enabledPlugins")
            ids |= set(k for k in (ep if isinstance(ep, dict) else {}) if k.endswith("@" + old))
        pairs = {i: i[:-len(old)] + new for i in sorted(ids)}
        for sub in ("cache", "marketplaces"):
            src = os.path.join(pl, sub, old)
            if os.path.isdir(src):
                dirs.append((src, os.path.join(pl, sub, new)))
        for i in pairs:
            p, _m = _split(i)
            src = os.path.join(pl, "data", f"{_ident(p)}-{_ident(old)}")
            if os.path.isdir(src):
                dirs.append((src, os.path.join(pl, "data", f"{_ident(p)}-{_ident(new)}")))
        if old not in known and not pairs and not dirs:
            m.fail("rename", f"no marketplace {old!r}: not in known_marketplaces.json, and no plugin id ends in @{old}")
        if new in known:
            m.fail("rename", f"marketplace {new!r} already exists")
    else:
        (p, mk), (p2, mk2) = _split(old), _split(new)
        if not p or not p2:
            m.fail("rename", "both ids are <plugin>@<marketplace>", cmd("plugin rename <p>@<m> <p2>@<m2>"))
            return m
        if old == new:
            m.fail("rename", "the old and new ids are the same")
            return m
        pairs = {old: new}
        c_src = os.path.join(pl, "cache", mk, p)
        if os.path.isdir(c_src):
            dirs.append((c_src, os.path.join(pl, "cache", mk2, p2)))
        d_src = os.path.join(pl, "data", f"{_ident(p)}-{_ident(mk)}")
        if os.path.isdir(d_src):
            dirs.append((d_src, os.path.join(pl, "data", f"{_ident(p2)}-{_ident(mk2)}")))
        enabled_somewhere = any(old in ((_read(f) or {}).get("enabledPlugins") or {}) for f in settings_fs)
        if old not in plugins and not enabled_somewhere and not dirs:
            m.fail("rename", f"{old} is not installed, enabled, or on disk — nothing to rename")
        if new in plugins or any(new in ((_read(f) or {}).get("enabledPlugins") or {}) for f in settings_fs):
            m.fail("rename", f"{new} already exists")
    for src, dst in dirs:
        if os.path.lexists(dst):
            m.fail("rename", f"{dst} already exists")
    if any(f.level == "fail" for f in m.findings):
        return m

    # the folder moves (30)
    for src, dst in dirs:
        m.step("claude_code", f"{src} → {dst}", 30, "rename", src=src, dst=dst, mkparents=True)
    roots = {a: b for a, b in dirs if os.path.relpath(a, pl).split(os.sep)[0] in ("cache", "marketplaces")}

    # the registries (60): exact edits, chosen now, shown in the dry run
    def id_edits(data, keyed_path):
        block = data
        for seg in keyed_path:
            block = block.get(seg) if isinstance(block, dict) else None
        edits = []
        for k in (block if isinstance(block, dict) else {}):
            if k in pairs:
                if pairs[k] in block:
                    m.fail("rename", f"{pairs[k]} is already a key of {'.'.join(keyed_path)}")
                edits.append({"path": list(keyed_path), "key": True, "before": k, "after": pairs[k]})
        return edits

    def walk_values(node, path, out):
        if isinstance(node, dict):
            for k, v in node.items():
                walk_values(v, path + [k], out)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk_values(v, path + [i], out)
        elif isinstance(node, str) and os.path.isabs(node):
            nv = _under_cache(node, roots)
            if nv != node:
                out.append({"path": path, "key": False, "before": node, "after": nv})

    if installed:
        edits, vals = id_edits(installed, ["plugins"]), []
        walk_values(installed, [], vals)
        if edits or vals:
            m.step("claude_code", f"{installed_f}: {len(edits)} id(s), {len(vals)} path(s)", 60, "json_edit",
                   file=installed_f, edits=vals + edits)
    if marketplace and old in known:
        edits = [{"path": [], "key": True, "before": old, "after": new}]
        vals = []
        walk_values(known, [], vals)
        m.step("claude_code", f"{known_f}: {old} → {new}", 60, "json_edit", file=known_f, edits=vals + edits)
    for f in settings_fs:
        data = _read(f)
        if not isinstance(data, dict):
            continue
        edits, vals = id_edits(data, ["enabledPlugins"]), []
        if marketplace:
            ek = data.get("extraKnownMarketplaces")
            if isinstance(ek, dict) and old in ek:
                if new in ek:
                    m.fail("rename", f"{new} is already a key of extraKnownMarketplaces in {f}")
                edits.append({"path": ["extraKnownMarketplaces"], "key": True, "before": old, "after": new})
        walk_values(data, [], vals)
        if edits or vals:
            m.step("claude_code", f"{f}: {len(edits)} id(s), {len(vals)} path(s)", 60, "json_edit",
                   file=f, edits=vals + edits)

    m.facts["plugin_ids"] = len(pairs)
    m.facts["folders"] = len(dirs)
    for a, b in pairs.items():
        m.lineage("plugin", a, b)
    if marketplace:
        m.lineage("marketplace", old, new)
    m.pairs = pairs
    m.step("lineage", f"record the move ({len(m.lineage_rows)} row(s)) for readers of history", 90, "lineage",
           file=os.path.join(P.db_dir(m.home), "lineage.jsonl"), rows=m.lineage_rows)
    _guard_steps(m)
    return m


def verify_plugin(m: P.Move) -> list:
    bad = []
    pl = os.path.join(m.claude, "plugins")
    plugins = (_read(os.path.join(pl, "installed_plugins.json")) or {}).get("plugins") or {}
    for s in m.steps:
        if s["op"] == "rename" and (os.path.lexists(s["args"]["src"]) or not os.path.isdir(s["args"]["dst"])):
            bad.append(f"{s['args']['dst']} did not land")
    if any(k in plugins for k in getattr(m, "pairs", {})):
        bad.append("an old id is still in installed_plugins.json")
    return bad


# ── home move ───────────────────────────────────────────────────────────────
def obsidian_json() -> str:
    """Obsidian's vault registry: under Application Support on macOS, else the OS config folder
    (XDG config on Linux, %APPDATA% on Windows)."""
    if is_macos():
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = str(config_dir())
    return os.path.join(base, "obsidian", "obsidian.json")


def home_move(new: str, plugins_dir=None) -> P.Move:
    old = P.kit_home()
    m = P.Move(id=P.new_id(old, new), old=old, new=P._real(new), home=old, claude=P.claude_dir(),
               claude_json=P.claude_json(), kind="home")
    home = os.path.realpath(os.path.expanduser("~"))
    if m.old == m.new:
        m.fail("move", "the old and new homes are the same")
        return m
    if m.new == home or m.new == os.path.dirname(m.new) or ops.under(home, m.new):
        m.fail("move", f"{m.new} is not a place for the kit's home")
    if ops.under(m.new, m.old) or ops.under(m.old, m.new):
        m.fail("move", "one path is inside the other")
    if ops.under(m.claude, m.new) or ops.under(m.new, m.claude):
        m.fail("move", f"{m.claude} is Claude Code's own folder")
    try:
        ops.guard(m.old, m.new)
    except ops.Refused as e:
        m.fail("gateway", str(e))
    if not os.path.isdir(m.old):
        m.fail("move", f"{m.old} does not exist — nothing to move")
        return m
    if os.path.lexists(m.new):
        m.fail("move", f"{m.new} already exists")
        return m
    parent = os.path.dirname(m.new)
    if not os.path.isdir(parent):
        m.fail("move", f"{parent} does not exist", f"mkdir -p {parent}")
        return m
    if os.stat(m.old).st_dev != os.stat(parent).st_dev:
        m.fail("move", f"{m.old} and {parent} are on different disks — a move is a rename, never a copy")
        return m
    if any(f.level == "fail" for f in m.findings):
        return m

    # the folder, with the old path kept as a link (30), then the same pointer handlers a workspace uses
    m.step("fs", f"move {m.old} → {m.new}, leaving {m.old} as a link until finalize", 30, "move_dir",
           src=m.old, dst=m.new)
    for h in (P.h_fs, P.h_git, P.h_claude_code, P.h_memory, P.h_traces):
        h(m)
    # h_fs also planned the rename and a note at the old path: the link-leaving step above replaces both
    m.steps = [s for s in m.steps if not (s["op"] == "rename" and s["args"].get("src") == m.old)
               and not (s["op"] == "tombstone" and s["args"].get("path") == m.old)]
    for name, h in P.plugin_handlers(plugins_dir):
        try:
            h.plan(m)
        except Exception as e:  # noqa: BLE001
            m.fail(name, f"its relocate handler raised {type(e).__name__}: {e}")
    ob = obsidian_json()
    if os.path.isfile(ob) and P._mentions(ob, m.old):
        m.step("memory", f"{ob}: vault path", 60, "json_remap", file=ob, old=m.old, new=m.new, keys=False)
    for name in ("PATH", "HOME", "STORE"):  # the shell's own pointer is a process variable: no file to rewrite
        v = os.path.realpath(os.path.expanduser((env(name) or "").strip())) if (env(name) or "").strip() else ""
        if v and ops.under(v, m.old):  # $AK_PATH names <home>/vault: it becomes <new>/vault
            m.warn("home", f"${'{'}{name}{'}'} of your shell names the old home — set it to {ops.remap(v, m.old, m.new)} "
                   "in your profile", None)
    if P.gateway_up():
        m.warn("gateway", "the gateway is up and is not moved here: its files stay where they are, and the link "
               "at the old path keeps anything of it that points into the old home working")
    # steps after the folder move that name a file inside the home now find it at the new place
    for s in m.steps:
        if s["phase"] > 30 and s["op"] != "move_dir":
            for k in ("file", "db", "path"):
                v = s["args"].get(k)
                if v and ops.under(v, m.old):
                    s["args"][k] = ops.remap(v, m.old, m.new)
    m.lineage("path", m.old, m.new)
    m.step("lineage", f"record the move ({len(m.lineage_rows)} row(s)) for readers of history", 90, "lineage",
           file=ops.remap(os.path.join(P.db_dir(m.old), "lineage.jsonl"), m.old, m.new), rows=m.lineage_rows)
    _guard_steps(m)
    return m


def verify_home(m: P.Move) -> list:
    bad = []
    if not os.path.isdir(m.new) or os.path.islink(m.new):
        bad.append(f"{m.new} is not a folder")
    if not os.path.islink(m.old) or os.path.realpath(m.old) != os.path.realpath(m.new):
        bad.append(f"{m.old} does not lead to {m.new}")
    for a, b in m.slugs.items():
        if os.path.isdir(a) or not os.path.isdir(b):
            bad.append(f"chat history {os.path.basename(a)} did not land at {os.path.basename(b)}")
    return bad


__all__ = ["plugin_rename", "home_move", "verify_plugin", "verify_home", "HOME_DIR"]
