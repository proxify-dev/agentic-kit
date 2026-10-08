"""setup/catalog.py: the components as data — complete against the marketplace, ordered, blocked in words."""
import json
from pathlib import Path

import pytest

from ak._brand import MARKETPLACE
from ak.setup import catalog
from ak.setup.machine import Bin, Machine

REPO = Path(__file__).resolve().parents[3]
VERSIONS = {"uv": "0.7.20", "git": "2.50.1", "claude": "2.1.286", "node": "22.22.0", "tmux": "3.5"}


def machine(os="macos", arch="arm64", bins=None, **kw) -> Machine:
    """A machine by hand: every common tool present at a good version, unless bins says otherwise."""
    found = {n: Bin(f"/bin/{n}", v) for n, v in VERSIONS.items()}
    found["osascript"] = Bin("/usr/bin/osascript")
    found.update(bins or {})
    kw.setdefault("service_manager", {"macos": "launchd", "windows": "schtasks"}.get(os, "systemd"))
    return Machine(os=os, arch=arch, bins=found, **kw)


@pytest.mark.skipif(not (REPO / ".claude-plugin" / "marketplace.json").is_file(), reason="no workspace marketplace above ak")
def test_every_marketplace_plugin_has_a_component():
    listed = {p["name"] for p in json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())["plugins"]}
    cataloged = {c.plugin for c in catalog.COMPONENTS if c.plugin}
    assert listed == cataloged


def test_plugins_outside_the_marketplace_are_shown_not_selectable():
    for cid in ("tasks", "routines"):
        c = catalog.get(cid)
        assert not c.selectable and c.plugin is None and "not in the marketplace" in c.note


def test_ids_are_unique_and_every_dependency_exists():
    ids = [c.id for c in catalog.COMPONENTS]
    assert len(ids) == len(set(ids))
    assert all(d in ids for c in catalog.COMPONENTS for d in c.depends_on)


def test_plugin_id_uses_the_brand_marketplace():
    assert catalog.plugin_id(catalog.get("ak")) == f"ak@{MARKETPLACE}"
    assert catalog.plugin_id(catalog.get("release")) is None


def test_order_puts_dependencies_first():
    ids = catalog.order(["gateway", "memory", "shim", "vault", "ak"])
    assert ids.index("ak") < ids.index("vault") < ids.index("memory")
    assert ids.index("shim") < ids.index("gateway")


def test_order_is_stable_by_catalog_position():
    everything = [c.id for c in catalog.COMPONENTS]
    assert catalog.order(list(reversed(everything))) == catalog.order(everything)
    assert catalog.order(["tag", "tracer"]) == ["tracer", "tag"]


def test_order_does_not_add_what_was_not_asked_for():
    assert catalog.order(["memory"]) == ["memory"]


def test_with_deps_of_memory_reaches_the_release():
    got = catalog.with_deps(["memory"])
    assert {"memory", "vault", "ak", "marketplace", "release"} <= set(got)
    assert got == catalog.order(got)


def test_with_deps_of_agentic_engineering_pulls_in_the_function_hooks_setting():
    assert "settings.function-hooks" in catalog.with_deps(["agentic-engineering"])


def test_unknown_id_is_a_key_error():
    with pytest.raises(KeyError):
        catalog.with_deps(["nope"])
    with pytest.raises(KeyError):
        catalog.order(["nope"])


def test_a_cycle_is_refused(monkeypatch):
    a = catalog.Component("a", "a", "", "Kit", depends_on=("b",))
    b = catalog.Component("b", "b", "", "Kit", depends_on=("a",))
    monkeypatch.setattr(catalog, "COMPONENTS", (a, b))
    monkeypatch.setattr(catalog, "BY_ID", {"a": a, "b": b})
    with pytest.raises(ValueError, match="cycle"):
        catalog.order(["a", "b"])


def test_notify_is_blocked_off_macos_with_the_os_in_the_reason():
    assert catalog.blocked(catalog.get("notify"), machine("linux")) == "needs macOS (this is linux)"
    assert catalog.blocked(catalog.get("notify"), machine("macos")) is None


def test_notify_needs_osascript_on_macos():
    m = machine("macos", bins={"osascript": Bin(None)})
    assert catalog.blocked(catalog.get("notify"), m) == "needs osascript"


def test_tasks_need_tmux_and_not_windows():
    assert catalog.blocked(catalog.get("tasks"), machine("windows")) == "needs macOS, Linux or WSL (this is windows)"
    assert catalog.blocked(catalog.get("tasks"), machine("linux", bins={"tmux": Bin(None)})) == "needs tmux"
    assert catalog.blocked(catalog.get("tasks"), machine("linux")) is None


def test_shim_needs_a_new_enough_node():
    old = machine(bins={"node": Bin("/bin/node", "20.11.0")})
    assert catalog.blocked(catalog.get("shim"), old) == "needs node ≥ 22.13 (found 20.11.0)"
    assert catalog.blocked(catalog.get("shim"), machine(bins={"node": Bin("/bin/node", "22.13.0")})) is None
    assert catalog.blocked(catalog.get("shim"), machine(bins={"node": Bin("/bin/node", "22.9")})) is not None
    assert catalog.blocked(catalog.get("shim"), machine(bins={"node": Bin(None)})) == "needs node ≥ 22.13 (not found)"


def test_gateway_needs_a_service_manager():
    assert catalog.blocked(catalog.get("gateway"), machine("linux", service_manager=None)).startswith("needs a service manager")
    assert catalog.blocked(catalog.get("gateway"), machine("linux")) is None


def test_gateway_is_blocked_through_the_shim_it_depends_on():
    old = machine(bins={"node": Bin("/bin/node", "20.11.0")})
    assert catalog.blocked(catalog.get("gateway"), old) == "needs node ≥ 22.7 (found 20.11.0)"
    why = catalog.blocked_with_deps(catalog.get("gateway"), machine(bins={"node": Bin("/bin/node", "22.8.0")}))
    assert why == "needs shim, which needs node ≥ 22.13 (found 22.8.0)"


def test_the_observer_is_not_blocked_on_an_intel_mac_only_its_embed_step_is():
    intel = machine("macos", "x86_64")
    assert catalog.blocked(catalog.get("observer"), intel) is None
    assert catalog.embed_blocked(intel)
    assert catalog.embed_blocked(machine("macos", "arm64")) is None
    assert catalog.embed_blocked(machine("linux", "x86_64")) is None
    assert "Intel Mac" in catalog.get("observer").note


def test_not_arch_blocks_the_pair_it_names(monkeypatch):
    c = catalog.Component("x", "x", "", "Kit", catalog.Needs(not_arch=(("macos", "x86_64"),)))
    assert catalog.blocked(c, machine("macos", "x86_64")) == "not available on macOS x86_64"
    assert catalog.blocked(c, machine("macos", "arm64")) is None
    assert catalog.blocked(c, machine("linux", "AMD64")) is None
    assert catalog.blocked(catalog.Component("y", "y", "", "Kit", catalog.Needs(not_arch=(("windows", "x86_64"),))),
                           machine("windows", "AMD64"))


def test_defaults_on_a_mac_are_the_core_kit_without_the_launcher():
    got = catalog.defaults(machine("macos"))
    for cid in ("release", "marketplace", "ak", "vault", "memory", "ak-tool", "observer", "tracer",
                "tool-results", "tag", "notify", "agentic-engineering", "settings.file-suggestion",
                "settings.function-hooks"):
        assert cid in got
    for cid in ("todo", "feed", "shim", "gateway", "tasks", "routines"):
        assert cid not in got
    assert got == catalog.order(got)


def test_defaults_on_linux_leave_notify_out():
    got = catalog.defaults(machine("linux"))
    assert "notify" not in got and "memory" in got


def test_defaults_leave_a_file_suggestion_the_person_already_set():
    m = machine(settings={"fileSuggestion": {"type": "command", "command": "mine"}})
    assert "settings.file-suggestion" not in catalog.defaults(m)


def test_defaults_drop_what_hangs_on_a_missing_tool():
    got = catalog.defaults(machine(bins={"git": Bin(None)}))
    assert "release" not in got and "memory" not in got  # the clone needs git, and the plugins hang on the clone


def exported_kit(tmp_path: Path) -> Path:
    """The tree a stranger clones: kit/ at the root, and the public plugins (their plugin.json is all the catalog reads)."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text((REPO / "kit" / ".claude-plugin" / "marketplace.json").read_text())
    (tmp_path / "modules.json").write_text((REPO / "kit" / "modules.json").read_text())
    for name in ("ak", "tracer", "agentic-engineering"):
        (tmp_path / "plugins" / name / ".claude-plugin").mkdir(parents=True)
        (tmp_path / "plugins" / name / ".claude-plugin" / "plugin.json").write_text(
            (REPO / "plugins" / name / ".claude-plugin" / "plugin.json").read_text())
    return tmp_path


def test_the_public_kit_lists_its_public_plugins_and_the_modules(tmp_path):
    name, rows = catalog.load(catalog.read_kit(exported_kit(tmp_path)))
    assert name == "agentic-kit"
    by_group = {}
    for c in rows:
        by_group.setdefault(c.group, []).append(c.id)
    assert by_group == {"Kit": ["release", "marketplace", "ak-tool", "ak-path"],
                        "Plugins": ["ak", "agentic-engineering", "tracer"], "Launcher": ["shim", "gateway"],
                        "Settings": ["settings.function-hooks"]}  # agentic-engineering's commands run on it
    assert not any(c.private for c in rows) and all(c.selectable for c in rows)
    assert next(c for c in rows if c.id == "agentic-engineering").default_on  # public: on by default, no note
    first = next(c for c in rows if c.id == "tracer").first_run  # its setup block's first_run: the history, indexed
    assert first.run == ("bin/tracer", "trace", "index", "--all") and first.reads == "history"
    # a person picks from what each piece gives; what it changes outside Claude Code is said before it runs
    assert all(c.gives for c in rows if c.group != "Kit"), "every public piece says what you get (setup.gives)"
    assert all(c.changes for c in rows if c.group == "Launcher"), "a launcher piece always changes the machine: say how"
    assert all(c.route.startswith("claude → ") for c in rows if c.group == "Launcher"), \
        "a launcher piece changes how claude starts: setup sets its route beside the plain one"
    assert next(c for c in rows if c.id == "shim").comes_with == "gateway", \
        "the shim is how the claude you type reaches the gateway: setup shows them as one row"


def test_the_snapshot_an_ak_without_a_kit_reads_is_the_public_kit(tmp_path):
    assert catalog.load(json.loads(catalog.SNAPSHOT.read_text())) == catalog.load(catalog.read_kit(exported_kit(tmp_path)))


def test_the_workspace_lists_every_plugin_and_marks_the_private_ones():
    assert catalog.get("vault").private and catalog.get("memory").private
    assert not catalog.get("ak").private and not catalog.get("tracer").private
    assert not catalog.get("release").private and not catalog.get("shim").private


def test_a_module_whose_plugin_the_kit_carries_is_that_plugin():
    c = catalog.get("tool-results")
    assert c.plugin == "tool-results" and c.module is None and c.private  # the workspace's own folder, not the module


def test_the_launcher_modules_are_pinned_and_need_no_release_clone():
    for cid in ("shim", "gateway"):
        c = catalog.get(cid)
        assert c.module and c.module.install == "install.sh" and c.module.version[0].isdigit()
        assert "release" not in catalog.with_deps([cid])


def test_a_dependency_the_kit_does_not_carry_is_shown_not_selectable():
    _, rows = catalog.load({"marketplace": "k", "listed": ["x"], "plugins": [{"name": "x", "dependencies": ["vault"]}]})
    x = next(c for c in rows if c.id == "x")
    assert not x.selectable and "vault" in x.note and x.depends_on == ("marketplace",)
    assert "settings.file-suggestion" not in {c.id for c in rows}  # it serves the vault, which is not here


def test_a_setup_block_sets_needs_default_and_note():
    _, rows = catalog.load({"listed": ["n"], "plugins": [{"name": "n", "setup": {
        "default": False, "note": "parked", "depends_on": ["ak-tool"],
        "needs": {"os": ["macos"], "bins": ["uv", "node>=22.13"], "service": True}}}]})
    n = next(c for c in rows if c.id == "n")
    assert not n.default_on and n.note == "parked" and n.depends_on == ("marketplace", "ak-tool")
    assert n.needs == catalog.Needs(os=frozenset({"macos"}), bins=(("uv", None), ("node", "22.13")), service=True)
