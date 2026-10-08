"""The mount contract (cli.py `_mount_spec`): a plugin.json `cli` block — one entry or a list — each
{command, entry | module, attr, hidden?, hoist?}, and the optional module-level brain_status() /
brain_boot(ctx) / brain_search(words, limit); plus what rides on it: a hidden old name, the ✎ mark,
`ak search`, and a verb typed one level too high.

Run in a child process against a fabricated plugins dir: mounting mutates the one `cli` group and
rich-click's COMMAND_GROUPS, which the rest of the suite must not inherit."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from ak._brand import CLI, env_name

PLUGIN = Path(__file__).resolve().parents[1]

GIZMO = '''
import click
from ak.groups import writes

@click.group()
def gizmo():
    """Gizmo things."""

@gizmo.command()
def spin():
    """Spin the gizmo."""
    click.echo("spun")

@gizmo.command()
def stop():
    """Stop the gizmo."""
    click.echo("stopped")

@writes
@gizmo.command()
def wipe():
    """Wipe the gizmo."""
    click.echo("wiped")

def brain_search(words, limit):
    return [{"id": "g1", "title": f"gizmo {words}", "when": "2026-10-02"}]

def brain_boot(ctx):
    click.echo(f"boot:{ctx.invoked_subcommand}", err=True)

def brain_status():
    return [("gizmo", "ready")]
'''
WIDGET = '''
import click

@click.command()
def widget():
    """A widget by import name."""
    click.echo("widget")

def brain_search(words, limit):
    raise RuntimeError("index missing")
'''
DUMP = """
import json, sys
from rich_click import rich_click as rc_config
from ak import cli as C
C._mount_plugin_clis(C.cli)
groups = {g["name"]: g["commands"] for g in rc_config.COMMAND_GROUPS.get(C.CLI)}
print(json.dumps({"commands": sorted(C.cli.commands), "spin": C.cli.commands.get("spin") is C.cli.commands["gizmo"].commands["spin"],
                  "halt": C.cli.commands.get("halt") is C.cli.commands["gizmo"].commands["stop"],
                  "groups": groups, "status": [n for n, _ in C._STATUS_HOOKS], "boot": [n for n, _ in C._BOOT_HOOKS],
                  "search": [n for n, _ in C._SEARCH_HOOKS],
                  "hidden": sorted(n for n, c in C.cli.commands.items() if getattr(c, "hidden", False))}))
"""


def _plugins(tmp_path: Path) -> Path:
    plugins = tmp_path / "plugins"
    g = plugins / "gizmo"
    (g / ".claude-plugin").mkdir(parents=True)
    (g / "scripts").mkdir()
    (g / "scripts" / "cli.py").write_text(textwrap.dedent(GIZMO))
    (g / "lib").mkdir()
    (g / "lib" / "widget_cli.py").write_text(textwrap.dedent(WIDGET))
    (g / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "gizmo", "cli": [
        {"command": "gizmo", "entry": "scripts/cli.py", "attr": "gizmo",
         "hoist": ["spin", "nope", {"command": "stop", "as": "halt"}, {"command": "stop", "as": "quiet", "hidden": True}]},
        {"command": "oldgizmo", "entry": "scripts/cli.py", "attr": "gizmo", "hidden": True},
        {"command": "widget", "module": "widget_cli", "attr": "widget"}]}))
    old = plugins / "oldstyle"
    old.mkdir()
    (old / "cli.py").write_text("import click\n\n@click.command()\ndef old():\n    click.echo('old')\n")
    (old / "plugin.json").write_text(json.dumps({"name": "oldstyle", "cli": {"command": "old", "entry": "cli.py", "attr": "old"}}))
    # a stranger's plugin named like the host is mounted like any other: only the host's own root is skipped;
    # a mounted command the root help already lists (`memory`) keeps its group
    m = plugins / "ak" / ".claude-plugin"
    m.mkdir(parents=True)
    (m / "plugin.json").write_text(json.dumps({"name": "ak", "cli": [{"command": "stranger", "module": "widget_cli", "attr": "widget"},
                                                                       {"command": "memory", "module": "widget_cli", "attr": "widget"}]}))
    return plugins


def _env(tmp_path: Path) -> dict:
    env = {**os.environ, "HOME": str(tmp_path / "home"), env_name("PLUGINS_DIR"): str(_plugins(tmp_path)),
           env_name("OUTPUT"): "plain", env_name("NO_REEXEC"): "1",
           "PYTHONPATH": os.pathsep.join([str(PLUGIN / "src"), str(tmp_path / "plugins" / "gizmo" / "lib")])}
    for k in (env_name("PATH"), env_name("HOME"), "CLAUDE_PLUGIN_ROOT"):
        env.pop(k, None)
    return env


def test_a_cli_block_may_be_a_list_load_by_import_name_and_hoist(tmp_path):
    p = subprocess.run([sys.executable, "-c", DUMP], capture_output=True, text=True, env=_env(tmp_path), cwd=str(tmp_path))
    assert p.returncode == 0, p.stderr
    *said, last = p.stdout.strip().splitlines()  # warn: is on stdout (plugins/AGENTS.md §2)
    d = json.loads(last)
    assert {"gizmo", "widget", "old", "spin", "stranger", "memory"} <= set(d["commands"]), d["commands"]
    assert "stop" not in d["commands"] and "nope" not in d["commands"]
    assert d["spin"], "a hoisted verb is the same click object as the group's"
    assert d["halt"], "a hoist may rename: {command, as} puts `gizmo stop` at the top as `halt`"
    assert "gizmo spin" not in d["groups"]["Plugins"] and d["groups"]["Plugins"].count("spin") == 1
    assert "memory" in d["groups"]["Groups"] and "memory" not in d["groups"]["Plugins"], \
        "a mounted command the help lists keeps its group, once"
    assert "warn: gizmo cli: no `gizmo nope` to hoist" in said
    assert d["status"] == ["hooks", "shim", "plugins", "gizmo"] and d["boot"] == ["gizmo"], \
        "one module behind two entries registers each hook once, under the first name"
    assert d["search"] == ["stranger", "gizmo"]
    assert d["hidden"] == ["oldgizmo", "quiet"], "hidden: true mounts it out of the lists"
    assert not {"oldgizmo", "quiet"} & {n for names in d["groups"].values() for n in names}


def test_a_hoisted_verb_runs_at_the_top_and_boot_runs_before_it(tmp_path):
    env = _env(tmp_path)
    for args, out in ((["spin"], "spun"), (["gizmo", "spin"], "spun"), (["halt"], "stopped"), (["widget"], "widget"), (["old"], "old")):
        p = subprocess.run([sys.executable, "-c", "from ak.cli import main; main()", *args],
                           capture_output=True, text=True, env=env, cwd=str(tmp_path))
        assert p.returncode == 0 and p.stdout.strip().splitlines()[-1] == out, (args, p.stdout, p.stderr)
        assert f"boot:{args[0]}" in p.stderr
    h = subprocess.run([sys.executable, "-c", "from ak.cli import main; main()", "spin", "-h"],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert "Spin the gizmo." in h.stdout, h.stdout
    assert CLI in h.stdout


def _ak(env, tmp_path, *args):
    return subprocess.run([sys.executable, "-c", "from ak.cli import main; main()", *args],
                          capture_output=True, text=True, env=env, cwd=str(tmp_path))


def test_a_hidden_name_runs_and_no_list_shows_it(tmp_path):
    env = _env(tmp_path)
    for args, out in ((["oldgizmo", "spin"], "spun"), (["quiet"], "stopped")):
        p = _ak(env, tmp_path, *args)
        assert p.returncode == 0 and p.stdout.strip().splitlines()[-1] == out, (args, p.stdout, p.stderr)
    h = _ak(env, tmp_path, "-h").stdout
    assert "oldgizmo" not in h and "quiet" not in h, h


def test_a_verb_typed_one_level_too_high_runs_when_it_only_reads(tmp_path):
    env = _env(tmp_path)
    p = _ak(env, tmp_path, "stop")
    assert p.returncode == 0 and p.stdout.strip().splitlines()[-1] == "stopped", (p.stdout, p.stderr)
    assert f"→ {CLI} gizmo stop" in p.stderr, "it says where it went, on stderr"
    w = _ak(env, tmp_path, "wipe")
    assert w.returncode == 2 and "wiped" not in w.stdout, (w.stdout, w.stderr)
    assert f"it lives at '{CLI} gizmo wipe'" in w.stderr and "does not run on a guess" in w.stderr, w.stderr


def test_a_writing_verb_carries_the_mark_in_every_list(tmp_path):
    h = _ak(_env(tmp_path), tmp_path, "gizmo", "-h").stdout
    assert "✎ Wipe the gizmo." in h and "✎ Spin" not in h, h


def test_search_asks_every_source_and_a_failing_one_says_so(tmp_path):
    env = _env(tmp_path)
    p = _ak(env, tmp_path, "search", "thing")
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert "gizmo thing" in p.stdout and f"hint: {CLI} gizmo search thing" in p.stdout, p.stdout
    assert "warn: stranger: search failed (index missing)" in p.stdout, p.stdout
    j = _ak(env, tmp_path, "s", "thing", "--json")
    d = json.loads(j.stdout[j.stdout.index("{"):])  # after the fabricated plugin's own warn: line
    assert [s["source"] for s in d["sources"]] == ["stranger", "gizmo"]
    assert d["sources"][1]["rows"][0]["id"] == "g1" and d["sources"][0]["error"] == "index missing"
