"""ak stands alone (the public kit's Phase 2): it plants its own home, mounts only the plugins Claude
Code has switched on, and runs with no sibling plugin beside it.

  home     ~/.ak gets the layout-2 skeleton from ak itself (ak/home.py) before any verb; never over a
           layout-1 home, which `ak doctor` names for scripts/migrate/4-home-layout.py
  enabled  a plugin present but off in enabledPlugins (user · project · local) has no verbs, no root
           Examples line, no COMMAND_GROUPS entry, and its extras (`ws move`) go with it; when the state
           cannot be read (no settings, AK_PLUGINS_DIR, pytest) present = mounted. Its id is the one
           Claude Code has for THIS folder (installed_plugins.json, known_marketplaces.json), whatever
           the marketplace — `tracer@agentic-kit` in the public kit — else any `<name>@…` key

Every case is a child process on a scratch HOME: mounting mutates the one `cli` group and rich-click's
COMMAND_GROUPS, and the enabled state is read from the settings under HOME.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from ak._brand import CLI, HOME_DIR, env_name

PLUGIN = Path(__file__).resolve().parents[1]

GIZMO = '''
import click

@click.group()
def gizmo():
    """Gizmo things."""

@gizmo.command()
def spin():
    """Spin the gizmo."""
    click.echo("spun")

@click.group()
def sessions():
    """Fake sessions."""

@sessions.command()
@click.argument("sid")
def replay(sid):
    """Replay one."""
    click.echo(sid)

@click.group()
def ws():
    """Fake workspaces."""
'''

DUMP = """
import json
from rich_click import rich_click as rc_config
from ak import cli as C
C._mount_plugin_clis(C.cli)
C._mount_move_extras(C.cli)
C._settle_root_help(C.cli)
ws = C.cli.commands.get("workspaces")
print(json.dumps({"commands": sorted(C.cli.commands), "ws_move": bool(ws and "move" in ws.commands),
                  "groups": {g["name"]: g["commands"] for g in rc_config.COMMAND_GROUPS.get(C.CLI)},
                  "epilog": getattr(C.cli.epilog, "plain", None) or str(C.cli.epilog)}))
"""


def _plugins(tmp_path: Path) -> Path:
    g = tmp_path / "plugins" / "tracer"
    (g / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (g / "scripts").mkdir(exist_ok=True)
    (g / "scripts" / "cli.py").write_text(textwrap.dedent(GIZMO))
    (g / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "tracer", "cli": [
        {"command": "gizmo", "entry": "scripts/cli.py", "attr": "gizmo"},
        {"command": "sessions", "entry": "scripts/cli.py", "attr": "sessions"},
        {"command": "workspaces", "entry": "scripts/cli.py", "attr": "ws"}]}))
    return g


def _env(tmp_path: Path, **extra) -> dict:
    """A scratch HOME (its ~/.claude is where the enabled state is read, its ~/.ak is the kit home), the
    fabricated plugins reached the way Claude Code does (CLAUDE_PLUGIN_ROOT), and none of the switches
    that make the enabled state unreadable."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "CLAUDE_PLUGIN_ROOT": str(_plugins(tmp_path)),
           env_name("OUTPUT"): "plain", env_name("NO_REEXEC"): "1", "PYTHONPATH": str(PLUGIN / "src")}
    for k in (env_name("PLUGINS_DIR"), env_name("HOME"), env_name("PATH"), env_name("STORE"),
              "PYTEST_CURRENT_TEST", "CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR"):
        env.pop(k, None)
    env.update(extra)
    return env


def _settings(path: Path, **enabled) -> None:
    """enabledPlugins with ids written `tracer_ak` for `tracer@ak` (`tracer_agentic_kit` → `tracer@agentic-kit`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ids = {k.replace("_", "@", 1).replace("_", "-"): v for k, v in enabled.items()}
    path.write_text(json.dumps({"enabledPlugins": ids}))


def _claude_plugins(tmp_path: Path, name: str, data: dict) -> None:
    pl = tmp_path / "home" / ".claude" / "plugins"
    pl.mkdir(parents=True, exist_ok=True)
    (pl / name).write_text(json.dumps(data))


def _dump(tmp_path: Path, **extra) -> dict:
    p = subprocess.run([sys.executable, "-c", DUMP], capture_output=True, text=True,
                       env=_env(tmp_path, **extra), cwd=str(tmp_path))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def _ak(tmp_path: Path, *args, **extra):
    return subprocess.run([sys.executable, "-c", "from ak.cli import main; main()", *args],
                          capture_output=True, text=True, env=_env(tmp_path, **extra), cwd=str(tmp_path))


# --- verbs follow enabled plugins -----------------------------------------------------------------
def test_no_settings_file_means_present_is_mounted(tmp_path):
    d = _dump(tmp_path)
    assert {"gizmo", "sessions", "workspaces"} <= set(d["commands"]), d["commands"]
    assert d["ws_move"], "ws move joins the workspaces group its host mounted"
    assert f"{CLI} sessions replay" in d["epilog"] and "sessions" in d["groups"]["Everyday"]


def test_a_plugin_switched_off_has_no_verbs_no_examples_no_group_and_no_extras(tmp_path):
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_ak=False, other_ak=True)
    d = _dump(tmp_path)
    assert not {"gizmo", "sessions", "workspaces"} & set(d["commands"]), d["commands"]
    assert not d["ws_move"]
    assert f"{CLI} sessions" not in d["epilog"] and f"{CLI} search" in d["epilog"], d["epilog"]
    listed = {n for names in d["groups"].values() for n in names}
    assert "sessions" not in listed and "workspaces" not in listed and "search" in listed, d["groups"]
    assert not [g for g, names in d["groups"].items() if not names], "an emptied group is dropped"
    r = _ak(tmp_path, "spin")
    assert r.returncode != 0 and "spun" not in r.stdout
    h = _ak(tmp_path, "-h").stdout
    assert "gizmo" not in h and "sessions" not in h, h


def test_a_plugin_only_named_elsewhere_is_off_and_the_local_file_wins(tmp_path):
    _settings(tmp_path / "home" / ".claude" / "settings.json", other_ak=True)  # tracer named nowhere: not on
    assert "gizmo" not in _dump(tmp_path)["commands"]
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_ak=True)
    assert "gizmo" in _dump(tmp_path)["commands"]
    _settings(tmp_path / ".claude" / "settings.local.json", tracer_ak=False)  # the project's local file, last word
    assert "gizmo" not in _dump(tmp_path)["commands"]
    r = _ak(tmp_path, "spin")
    assert "spun" not in r.stdout


def test_an_enabled_plugin_runs(tmp_path):
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_ak=True)
    r = _ak(tmp_path, "spin")
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "spun", (r.stdout, r.stderr)


def test_a_pinned_plugins_dir_mounts_what_is_there_whatever_the_settings(tmp_path):
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_ak=False)
    d = _dump(tmp_path, **{env_name("PLUGINS_DIR"): str(tmp_path / "plugins")})
    assert "gizmo" in d["commands"]


# --- ak plants its home ---------------------------------------------------------------------------
LAYOUT2 = ("vault", "db", "cache", "moves")


def test_a_fresh_machine_gets_the_home_from_ak_alone_and_doctor_passes(tmp_path):
    empty = tmp_path / "noplugins"
    empty.mkdir()
    r = _ak(tmp_path, "doctor", **{env_name("PLUGINS_DIR"): str(empty)})  # no vault plugin anywhere
    kit = tmp_path / "home" / HOME_DIR
    assert all((kit / d).is_dir() for d in LAYOUT2), sorted(p.name for p in kit.iterdir())
    assert json.loads((kit / "layout").read_text()) == {"layout": 2}
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "pass\tlayout\t" in r.stdout, r.stdout


def test_a_layout_1_home_is_left_as_it_is_and_doctor_names_the_migration(tmp_path):
    kit = tmp_path / "home" / HOME_DIR
    (kit / ".brain-state").mkdir(parents=True)
    (kit / "vault-manifest.json").write_text("{}")
    (kit / "note.md").write_text("mine")
    before = sorted(p.name for p in kit.iterdir())
    r = _ak(tmp_path, "doctor", **{env_name("PLUGINS_DIR"): str(tmp_path / "plugins")})
    assert sorted(p.name for p in kit.iterdir()) == before, "nothing planted over a layout-1 home"
    assert r.returncode != 0 and "fail\tlayout\t" in r.stdout and "4-home-layout.py" in r.stdout, r.stdout


def test_an_existing_home_keeps_everything_and_gets_only_what_is_missing(tmp_path):
    from ak import home as H
    kit = tmp_path / "kit"
    (kit / "db").mkdir(parents=True)
    (kit / "db" / "brain.db").write_text("rows")
    (kit / "layout").write_text('{"layout": 2, "mine": true}\n')
    made = H.plant(str(kit))
    assert sorted(Path(p).name for p in made) == ["cache", "moves", "vault"]
    assert (kit / "db" / "brain.db").read_text() == "rows" and "mine" in (kit / "layout").read_text()
    assert H.plant(str(kit)) == [], "planted already: nothing to do"


def test_an_empty_home_folder_is_planted_and_a_named_vault_is_used(tmp_path):
    from ak import home as H
    kit = tmp_path / "kit"
    kit.mkdir()
    H.plant(str(kit), str(tmp_path / "elsewhere"))
    assert (tmp_path / "elsewhere").is_dir() and not (kit / "vault").exists()
    assert all((kit / d).is_dir() for d in ("db", "cache", "moves")) and (kit / "layout").is_file()


# --- the id is this folder's, whatever the marketplace (the public kit is `agentic-kit`) ---------------
def test_installed_as_tracer_at_agentic_kit_and_enabled_is_mounted(tmp_path):
    _plugins(tmp_path)
    _claude_plugins(tmp_path, "installed_plugins.json", {"version": 2, "plugins": {
        "tracer@agentic-kit": [{"scope": "user", "installPath": str(tmp_path / "plugins" / "tracer")}]}})
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_agentic_kit=True, tracer_ak=False)
    assert "gizmo" in _dump(tmp_path)["commands"], "a tracer@ak from another marketplace does not hide it"


def test_installed_as_tracer_at_agentic_kit_and_switched_off_is_hidden(tmp_path):
    _plugins(tmp_path)
    _claude_plugins(tmp_path, "installed_plugins.json", {"version": 2, "plugins": {
        "tracer@agentic-kit": [{"scope": "user", "installPath": str(tmp_path / "plugins" / "tracer")}]}})
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_agentic_kit=False, tracer_ak=True)
    assert "gizmo" not in _dump(tmp_path)["commands"], "this folder's id is the one read; tracer@ak is another plugin"


def test_a_folder_under_a_marketplace_clone_takes_that_marketplace(tmp_path):
    _claude_plugins(tmp_path, "known_marketplaces.json", {"agentic-kit": {
        "source": {"source": "github", "repo": "proxify-dev/agentic-kit"}, "installLocation": str(tmp_path)}})
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_agentic_kit=False, tracer_ak=True)
    assert "gizmo" not in _dump(tmp_path)["commands"]
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_agentic_kit=True)
    assert "gizmo" in _dump(tmp_path)["commands"]


def test_the_workspace_with_no_install_record_reads_any_marketplace(tmp_path):
    # a worktree: neither installed_plugins.json nor a marketplace points here — `tracer@ak` as before
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_ak=True)
    assert "gizmo" in _dump(tmp_path)["commands"]
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_ak=False, tracer_other=True)
    assert "gizmo" in _dump(tmp_path)["commands"], "several marketplaces list the name: on when any is true"
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_ak=False, tracer_other=False)
    assert "gizmo" not in _dump(tmp_path)["commands"]


def test_a_missing_or_malformed_registry_falls_back_to_the_name(tmp_path):
    # installed_plugins.json not an object, a marketplace record without a source, a settings file
    # whose enabledPlugins is a list: none of them crashes the mount, and the name decides
    _claude_plugins(tmp_path, "installed_plugins.json", ["not", "an", "object"])
    _claude_plugins(tmp_path, "known_marketplaces.json", {"agentic-kit": {"installLocation": None}, "x": "junk"})
    _settings(tmp_path / "home" / ".claude" / "settings.json", tracer_ak=True)
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": ["tracer@ak"]}))
    assert "gizmo" in _dump(tmp_path)["commands"]
