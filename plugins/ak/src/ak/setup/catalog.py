"""Every installable piece of the kit, as data: what it needs, what it depends on, its default.

The rows are read from the kit on disk (tree(): the checkout this code sits in, else the release clone; with
neither, the public kit's SNAPSHOT), never kept here by name:
    Kit         setup's own: the release clone, the marketplace, the `ak` command, PATH
    Plugins     one per plugin under plugins/ — its plugin.json's name, description, visibility (missing:
                private) and dependencies; one listed by no marketplace is shown, not selectable
    Settings    the settings.json keys a present plugin runs on
    Launcher    one per module in kit/modules.json (modules.json at the root of an exported kit), installed by
                its own script at its pinned version; a module that ships a plugin is a Plugins row instead,
                left out when a plugin of that name is on disk
A plugin.json (or a module) may add a "setup" block for what the rest can't say:
    "setup": {"summary": "bookmark sessions to come back to", "depends_on": ["vault"], "default": false, "note": "parked", "group": "Launcher",
              "gives": ["ak sessions — your past sessions, searchable from the shell"],
              "changes": "an index of your history, kept beside Claude Code's own data",
              "route": "claude → shim → Claude Code → Anthropic",
              "needs": {"os": ["macos"], "bins": ["uv", "node>=22.13"], "service": true},
              "first_run": {"title": "index your Claude Code history", "run": ["bin/tracer", "trace", "index", "--all"], "reads": "history"}}
gives is what a person gets, one thing a line, led by the command that tries it; changes is what it puts on the machine
outside Claude Code's own plugin folder (missing: nothing). Choose shows both under the row, confirm lists the changes
before anything runs, done says each pick's first gives line. route is for a piece that changes how the `claude` you type
starts (the gateway): the whole path once it and what it needs are picked. Choose sets each beside the plain one, marking
the one the picks give; confirm says the one they give. comes_with names the row whose pick brings this one and whose
unpick drops it: it has no row of its own, and that row says it comes with it (the shim, with the gateway: the shim is
how the claude you type reaches the gateway). `ak setup --only <id>` still installs it alone.
first_run is the plugin's own work once it is installed, run last, from the plugin's folder in the release clone (run[0] is
a path in it). It keeps going if the person leaves setup before it ends. "reads": "history" puts the size of Claude Code's
transcripts beside its title.

A component is blocked on a machine that can't run it (blocked() says why, in words a person reads);
order() puts a selection in dependency order; with_deps() adds what a selection needs.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from ak._brand import CLI, MARKETPLACE as BRAND_MARKETPLACE
from ak.setup import source
from ak.setup.machine import Machine

OS_NAMES = {"macos": "macOS", "linux": "Linux", "wsl": "WSL", "windows": "Windows"}
ARCH_ALIASES = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}
HOST = "ak"  # the plugin whose install brings the ak command with it


@dataclass(frozen=True)
class Needs:
    os: frozenset[str] = frozenset()                 # empty: every OS
    bins: tuple[tuple[str, str | None], ...] = ()    # (name, min version | None)
    not_arch: tuple[tuple[str, str], ...] = ()       # (os, arch) pairs it can't run on
    service: bool = False                            # a service manager (launchd, systemd, Task Scheduler)


@dataclass(frozen=True)
class Module:
    repo: str        # "teocns/cc-gateway"
    version: str     # "0.1.0": the release it installs, fetched at tag v<version>
    install: str     # the installer's path in the repo: "install.sh"


@dataclass(frozen=True)
class FirstRun:
    title: str                    # "index your Claude Code history"
    run: tuple[str, ...]          # ("bin/tracer", "trace", "index", "--all"): run[0] is a path in the plugin's folder
    reads: str | None = None      # "history": Claude Code's transcripts, sized beside the title


@dataclass(frozen=True)
class Component:
    id: str                       # "ak", "notify", "settings.function-hooks", "shim"
    label: str
    why: str                      # what it gives the person
    group: str                    # "Kit" | "Plugins" | "Settings" | "Launcher"
    needs: Needs = field(default_factory=Needs)
    depends_on: tuple[str, ...] = ()
    default_on: bool = True
    plugin: str | None = None     # "<name>" when it is a marketplace plugin (installed as <name>@<marketplace>)
    selectable: bool = True       # False: shown for information only (not in the marketplace)
    note: str | None = None       # a caveat shown beside it ("parked", "untested on Windows")
    settings_key: tuple[str, ...] | None = None  # the user settings.json key it sets; left alone when already set
    private: bool = False         # its plugin.json does not say "visibility": "public": never in the public kit
    module: Module | None = None  # a module installed by its own script (kit/modules.json)
    summary: str = ""             # one line a person reads beside the name in `ak setup` ("bookmark sessions to come back to")
    comes_with: str | None = None  # the row whose pick brings it and whose unpick drops it: no row of its own in `ak setup`
    first_run: FirstRun | None = None  # the plugin's own first work once installed (its setup block's first_run)
    gives: tuple[str, ...] = ()   # what a person gets, one line each, the command first ("ak sessions — your past sessions …")
    changes: str = ""             # what it puts on the machine outside Claude Code's plugin folder; "": nothing
    route: str = ""               # the path `claude` takes once it (and what it needs) is picked: "claude → shim → Claude Code → …"


NOT_IN_MARKETPLACE = "not in the marketplace"
FUNCTION_HOOKS = ("env", "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS")


def kit_rows(marketplace: str) -> tuple[Component, ...]:
    """Setup's own rows: the source, and the command line over it."""
    return (
        Component("release", "Release clone", "A local clone of the kit: the folder Claude Code installs every plugin from.",
                  "Kit", Needs(bins=(("git", None),))),
        Component("marketplace", f"Marketplace {marketplace}", f"Tells Claude Code where the kit's plugins live, as the {marketplace} marketplace.",
                  "Kit", Needs(bins=(("claude", None),)), ("release",)),
        Component("ak-tool", f"The {CLI} command", f"The {CLI} command-line tool, installed once as a uv tool from the clone.",
                  "Kit", Needs(bins=(("uv", None),)), ("release",)),
        Component("ak-path", f"{CLI} on your PATH", f"Puts uv's tool folder on your shell PATH so a new terminal finds {CLI}. Skipped when it already is.",
                  "Kit", depends_on=("ak-tool",)),
    )


# Settings: edits to the user's Claude Code settings.json, each previewed as a diff; kept when a plugin it serves is here
SETTINGS: tuple[tuple[Component, str], ...] = (
    (Component("settings.function-hooks", "Function hooks", "Switches on Claude Code's function hooks, which /new-plugin, /new-skill and /new-agent (agentic-engineering) run on.",
               "Settings", settings_key=FUNCTION_HOOKS, comes_with="agentic-engineering",
               gives=("/new-plugin, /new-skill, /new-agent — agentic-engineering's commands, which run on function hooks",),
               changes="CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 in the env of your Claude Code settings.json"), "agentic-engineering"),
    (Component("settings.file-suggestion", "@[[ note picker", "Makes @ in the prompt complete vault notes as wikilinks.",
               "Settings", depends_on=("vault",), settings_key=("fileSuggestion",),
               summary="type @[[ in a prompt to pick one of your notes by name"), "vault"),
)


# ── reading the kit on disk ──────────────────────────────────────────────────────────────────────────
def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _is_kit(p: Path | None) -> bool:
    return bool(p) and (p / "plugins" / HOST).is_dir() and (p / ".claude-plugin" / "marketplace.json").is_file()


def tree() -> Path | None:
    """The kit the catalog is read from: the checkout this code sits in (a workspace, or the release clone ak
    runs from), the folder ak was installed from, else the release clone. None when none is on disk yet
    (`uvx --from git+…#subdirectory=plugins/ak`, before the clone): then the catalog is SNAPSHOT's."""
    installed = source._installed_from()
    for p in (Path(__file__).resolve().parents[5], Path(installed) if installed else None, source.release_dir()):
        if _is_kit(p):
            return p
    return None


def _plugin_json(folder: Path) -> dict:
    """A plugin's manifest: .claude-plugin/plugin.json, else plugin.json at its root; {} when it has none."""
    return _read(folder / ".claude-plugin" / "plugin.json") or _read(folder / "plugin.json")


def _needs(block: dict) -> Needs:
    bins = []
    for b in block.get("bins", ()):
        name, _, minimum = b.partition(">=")
        bins.append((name.strip(), minimum.strip() or None))
    return Needs(os=frozenset(block.get("os", ())), bins=tuple(bins), service=bool(block.get("service")),
                 not_arch=tuple(tuple(pair) for pair in block.get("not_arch", ())))


def _first_run(block: dict | None) -> FirstRun | None:
    if not block or not block.get("title") or not block.get("run"):
        return None
    return FirstRun(block["title"], tuple(block["run"]), block.get("reads"))


def _plugin_row(meta: dict, listed: bool) -> Component:
    name, setup = meta["name"], meta.get("setup") or {}
    host = ("ak-tool", "ak-path") if name == HOST else ()  # ak brings the tool with it: setup installs it now
    deps = (*(("marketplace",) if listed else ()), *host, *meta.get("dependencies", ()), *setup.get("depends_on", ()))
    return Component(name, name, meta.get("description", ""), setup.get("group", "Plugins"), _needs(setup.get("needs", {})),
                     tuple(dict.fromkeys(deps)), bool(setup.get("default", True)), name if listed else None,
                     selectable=listed, note=setup.get("note") if listed else NOT_IN_MARKETPLACE,
                     private=meta.get("visibility") != "public", summary=setup.get("summary", ""),
                     first_run=_first_run(setup.get("first_run")), gives=tuple(setup.get("gives", ())),
                     changes=setup.get("changes", ""), route=setup.get("route", ""), comes_with=setup.get("comes_with"))


def _module_row(mod: dict, listed: set[str]) -> Component:
    setup = mod.get("setup") or {}
    plugin = mod.get("plugin")
    is_plugin = bool(plugin and plugin in listed)
    deps = (*(("marketplace",) if is_plugin else ()), *setup.get("depends_on", ()))
    return Component(mod["id"], mod.get("label", mod["id"]), mod.get("description", ""),
                     setup.get("group", "Plugins" if plugin else "Launcher"), _needs(setup.get("needs", {})),
                     tuple(deps), bool(setup.get("default", True)), plugin if is_plugin else None,
                     selectable=is_plugin or bool(mod.get("install")),
                     note=setup.get("note") or (None if is_plugin or mod.get("install") else NOT_IN_MARKETPLACE),
                     module=None if plugin else Module(mod["repo"], mod["version"], mod["install"]),
                     summary=setup.get("summary", ""), gives=tuple(setup.get("gives", ())), changes=setup.get("changes", ""),
                     route=setup.get("route", ""), comes_with=setup.get("comes_with"))


def _without_missing(rows: list[Component]) -> list[Component]:
    """A row that depends on what this kit does not carry is shown, not selectable, and says what is missing."""
    have = {c.id for c in rows}
    out = []
    for c in rows:
        missing = [d for d in c.depends_on if d not in have]
        if missing:
            c = dataclasses.replace(c, depends_on=tuple(d for d in c.depends_on if d in have), selectable=False,
                                    note=f"needs {', '.join(missing)}, which this kit does not carry")
        out.append(c)
    return out


def read_kit(root: Path) -> dict:
    """The kit at root as one document: its marketplace's name, the plugins it lists, every plugin.json under
    plugins/ (listed or not), and its modules. The shape of SNAPSHOT."""
    market = _read(root / ".claude-plugin" / "marketplace.json")
    metas = [m for d in sorted((root / "plugins").iterdir()) if d.is_dir() and (m := _plugin_json(d)).get("name")]
    mods = (_read(root / "kit" / "modules.json") or _read(root / "modules.json")).get("modules", [])
    return {"marketplace": market.get("name") or BRAND_MARKETPLACE,
            "listed": [p.get("name") for p in market.get("plugins", ())], "plugins": metas, "modules": mods}


def load(kit: dict) -> tuple[str, tuple[Component, ...]]:
    """(the marketplace's name, the components) of a kit document (read_kit, or SNAPSHOT)."""
    name = kit.get("marketplace") or BRAND_MARKETPLACE
    names = kit.get("listed", [])
    listed = set(names)
    rank = {n: i for i, n in enumerate([HOST, *names])}
    metas = sorted(kit.get("plugins", []), key=lambda m: (rank.get(m["name"], len(rank)), m["name"]))
    plugins = [_plugin_row(m, m["name"] in listed) for m in metas]
    # a module's setup words reach its plugin row when the plugin.json has none: an upstream (cc-tool-results)
    # keeps the kit's words out of its own repo
    said = {m.get("plugin"): (m.get("setup") or {}).get("summary") for m in kit.get("modules", []) if m.get("plugin")}
    plugins = [dataclasses.replace(c, summary=said[c.id]) if not c.summary and said.get(c.id) else c for c in plugins]
    carried = {c.id for c in plugins}  # a module whose plugin the kit carries itself is that plugin
    mod_rows = [_module_row(m, listed) for m in kit.get("modules", [])
                if m.get("id") not in carried and m.get("plugin") not in carried]
    present = carried | {c.plugin for c in mod_rows if c.plugin}
    settings = [c for c, serves in SETTINGS if serves in present]
    rows = [*kit_rows(name), *plugins, *[c for c in mod_rows if c.plugin], *settings, *[c for c in mod_rows if not c.plugin]]
    return name, tuple(_without_missing(rows))


# The public kit as read_kit() would see it, written by scripts/kit.py render: what an ak with no kit on disk
# (`uvx --from git+…#subdirectory=plugins/ak`, before the release clone) installs from.
SNAPSHOT = Path(__file__).with_name("kit.json")
TREE = tree()
MARKETPLACE, COMPONENTS = load(read_kit(TREE) if TREE else _read(SNAPSHOT))
BY_ID = {c.id: c for c in COMPONENTS}


def get(cid: str) -> Component:
    return BY_ID[cid]


def plugin_id(c: Component) -> str | None:
    """"<plugin>@<marketplace>" for a marketplace plugin, None for the rest."""
    return f"{c.plugin}@{MARKETPLACE}" if c.plugin else None


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split(".") if p.isdigit())


def _at_least(found: str, minimum: str) -> bool:
    a, b = _version(found), _version(minimum)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) >= b + (0,) * (width - len(b))


def _is_arch(arch: str, wanted: str) -> bool:
    return ARCH_ALIASES.get(arch.lower(), arch.lower()) == ARCH_ALIASES.get(wanted.lower(), wanted.lower())


def blocked(c: Component, m: Machine) -> str | None:
    """Why this machine can't run c ("needs macOS (this is linux)", "needs node ≥ 22.13 (found 20.11)"), or None."""
    n = c.needs
    if n.os and m.os not in n.os:
        names = [OS_NAMES.get(o, o) for o in OS_NAMES if o in n.os]
        return f"needs {', '.join(names[:-1])} or {names[-1]} (this is {m.os})" if len(names) > 1 \
            else f"needs {names[0]} (this is {m.os})"
    for name, minimum in n.bins:
        b = m.bin(name)
        want = f"{name} ≥ {minimum}" if minimum else name
        if not b.found:
            return f"needs {want}" + (" (not found)" if minimum else "")
        if minimum and b.version and not _at_least(b.version, minimum):
            return f"needs {want} (found {b.version})"
    for os_name, arch in n.not_arch:
        if m.os == os_name and _is_arch(m.arch, arch):
            return f"not available on {OS_NAMES.get(os_name, os_name)} {m.arch}"
    if n.service and not m.service_manager:
        return "needs a service manager (launchd, systemd or Task Scheduler): none found"
    return None


def blocked_with_deps(c: Component, m: Machine) -> str | None:
    """blocked(), else the first of its dependencies that is blocked ("needs release, which needs git")."""
    own = blocked(c, m)
    if own:
        return own
    for cid in with_deps([c.id]):
        if cid != c.id and (why := blocked(get(cid), m)):
            return f"needs {cid}, which {why}"
    return None


def embed_blocked(m: Machine) -> str | None:
    """Why the observer's embed step (dense search) can't run here, or None. The plugin itself still can."""
    if m.os == "macos" and _is_arch(m.arch, "x86_64"):
        return "zvec and fastembed have no wheel for an Intel Mac: search stays keywords only"
    return None


def with_deps(ids: list[str]) -> list[str]:
    """ids plus everything they depend on, transitively, in dependency order. Unknown id: KeyError."""
    want: set[str] = set()
    todo = list(ids)
    while todo:
        cid = todo.pop()
        if cid not in want:
            want.add(cid)
            todo.extend(get(cid).depends_on)
    return order(list(want))


def order(ids: list[str]) -> list[str]:
    """ids in dependency order (dependencies first), stable by catalog order. A dependency outside ids is
    not added (that is with_deps). Unknown id: KeyError; a cycle: ValueError."""
    want = {get(cid).id for cid in ids}
    waiting = [c for c in COMPONENTS if c.id in want]
    done: list[str] = []
    while waiting:
        ready = next((c for c in waiting if all(d in done for d in c.depends_on if d in want)), None)
        if ready is None:
            raise ValueError("dependency cycle among " + ", ".join(c.id for c in waiting))
        waiting.remove(ready)
        done.append(ready.id)
    return done


def _has_key(settings: dict, keypath: tuple[str, ...]) -> bool:
    node: object = settings
    for k in keypath:
        if not isinstance(node, dict) or k not in node:
            return False
        node = node[k]
    return True


def defaults(m: Machine) -> list[str]:
    """The ids picked when the person picks nothing: default_on, selectable, not blocked on m (nor through a
    dependency), and a setting only when its key is not already there; in dependency order (a setting a plugin
    needs comes before it)."""
    return order([c.id for c in COMPONENTS
                  if c.default_on and c.selectable and not blocked_with_deps(c, m)
                  and not (c.settings_key and _has_key(m.settings, c.settings_key))])


def _check() -> None:
    """Import-time: every dependency exists and the graph has no cycle."""
    for c in COMPONENTS:
        for d in c.depends_on:
            if d not in BY_ID:
                raise ValueError(f"{c.id} depends on {d}, which is not in the catalog")
    if len(BY_ID) != len(COMPONENTS):
        raise ValueError("a component id appears twice")
    order([c.id for c in COMPONENTS])


_check()
