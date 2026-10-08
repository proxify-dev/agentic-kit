"""hooks/install-global.py: `ak` on PATH as one uv tool — ak, with the vault and memory beside it.

Run against a fabricated plugins/ tree and a fake `uv` that logs what it is asked, so nothing on this
machine is installed or removed: the tool environment the hook builds, the gate that skips a second
run, a sibling's pyproject that reopens it, another tool that puts `ak` on PATH taken out first, a
program's session that installs nothing, and the source rule (AK_SRC, else Claude Code's marketplace
clone for a root in its versioned cache, else the root) against a fabricated CLAUDE_CONFIG_DIR.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
SRC = Path(__file__).resolve().parents[1] / "src" / "ak"
FAKE_UV = """#!/bin/sh
echo "$*" >> "$UV_LOG"
if [ "$1 $2" = "tool list" ] && [ -z "$UV_NO_TOOLS" ]; then
  printf 'ak v1.0.0\\n- ak\\n- hooktrace\\nak-core v3.2.0\\n- ak\\n- hooktrace\\nruff v0.6.0\\n- ruff\\n'
fi
exit 0
"""


def ak_plugin(root: Path):
    """ROOT as the ak plugin: the hook, what it imports, a pyproject.toml."""
    (root / "hooks").mkdir(parents=True)
    for name in ("install-global.py", "_brand.py"):
        shutil.copy2(HOOKS / name, root / "hooks" / name)
    (root / "src" / "ak").mkdir(parents=True)
    for name in ("__init__.py", "_brand.py", "session_origin.py"):  # the one rule the installer takes from its src/
        shutil.copy2(SRC / name, root / "src" / "ak" / name)
    (root / "pyproject.toml").write_text('[project]\nname = "ak"\nversion = "1"\n')
    return root


def kit(plugins: Path):
    """A plugins/ folder as the repo has it: ak, with the vault and memory beside it."""
    ak_plugin(plugins / "ak")
    for name in ("vault", "memory"):
        (plugins / name).mkdir(parents=True)
        (plugins / name / "pyproject.toml").write_text(f'[project]\nname = "ak-{name}"\nversion = "1"\n')
    return plugins


@pytest.fixture
def tree(tmp_path):
    plugins = kit(tmp_path / "plugins")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "uv").write_text(FAKE_UV)
    (bin_ / "uv").chmod(0o755)
    log = tmp_path / "uv.log"
    claude = tmp_path / "claude"  # <claude home>: no plugins/cache, no plugins/marketplaces until a test makes them
    claude.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AK_", "CLAUDE"))}
    env.update(PATH=f"{bin_}:/usr/bin:/bin", UV_LOG=str(log), CLAUDE_PLUGIN_ROOT=str(plugins / "ak"),
               CLAUDE_PLUGIN_DATA=str(tmp_path / "data"), CLAUDE_CODE_ENTRYPOINT="cli", CLAUDE_CONFIG_DIR=str(claude))
    return {"plugins": plugins, "log": log, "env": env, "claude": claude}


def run(tree, root=None, **env):
    """Run the hook from ROOT (default: the plugins/ tree's ak) with CLAUDE_PLUGIN_ROOT at it; the uv calls it made."""
    root = Path(root) if root else tree["plugins"] / "ak"
    p = subprocess.run([sys.executable, str(root / "hooks" / "install-global.py")], capture_output=True,
                       text=True, env={**tree["env"], "CLAUDE_PLUGIN_ROOT": str(root), **env}, timeout=30)
    assert p.returncode == 0 and p.stdout == "", (p.stdout, p.stderr)
    calls = tree["log"].read_text().splitlines() if tree["log"].exists() else []
    tree["log"].unlink(missing_ok=True)
    return calls


def test_ak_installs_with_the_vault_and_memory_beside_it(tree):
    calls = run(tree)
    plugins = tree["plugins"].resolve()
    install = [c for c in calls if c.startswith("tool install")]
    assert install == [f"tool install --editable --force --refresh {tree['plugins'] / 'ak'} "
                       f"--with-editable {plugins / 'vault'} --with-editable {plugins / 'memory'}"], calls


def test_another_tool_that_puts_ak_on_path_is_taken_out_first(tree):
    """ak-core, the host's name before 4.0.0, is the one a machine has: it goes, ak stays."""
    calls = run(tree)
    assert "tool uninstall ak-core" in calls and "tool uninstall ak" not in calls
    assert not any("ruff" in c for c in calls if c.startswith("tool uninstall"))
    assert calls.index("tool uninstall ak-core") < next(i for i, c in enumerate(calls) if c.startswith("tool install"))


def test_the_gate_skips_a_second_run_and_a_siblings_pyproject_reopens_it(tree):
    assert any(c.startswith("tool install") for c in run(tree))
    assert run(tree) == []
    (tree["plugins"] / "memory" / "pyproject.toml").write_text('[project]\nname = "ak-memory"\nversion = "2"\n')
    assert any(c.startswith("tool install") for c in run(tree))


def test_a_missing_sibling_is_left_out(tree):
    shutil.rmtree(tree["plugins"] / "vault")
    install = [c for c in run(tree) if c.startswith("tool install")]
    assert len(install) == 1 and "vault" not in install[0] and "--with-editable" in install[0]


def test_a_programs_session_installs_nothing(tree):
    assert run(tree, CLAUDE_CODE_ENTRYPOINT="sdk-cli") == []


# The source rule. A git marketplace (the public kit on GitHub) runs ak from Claude Code's versioned cache,
# <claude home>/plugins/cache/<marketplace>/ak/<version>; Claude Code's clone of that marketplace,
# <claude home>/plugins/marketplaces/<marketplace>, has the repo's layout and one path across versions.

def cached(tree, version="4.3.0", marketplace="agentic-kit"):
    return ak_plugin(tree["claude"] / "plugins" / "cache" / marketplace / "ak" / version)


def clone(tree, marketplace="agentic-kit"):
    return kit(tree["claude"] / "plugins" / "marketplaces" / marketplace / "plugins")


def installs(calls):
    return [c for c in calls if c.startswith("tool install")]


def test_a_cached_root_with_a_marketplace_clone_installs_from_the_clone(tree):
    plugins = clone(tree)
    sibs = plugins.resolve()
    assert installs(run(tree, root=cached(tree))) == [
        f"tool install --editable --force --refresh {plugins / 'ak'} "
        f"--with-editable {sibs / 'vault'} --with-editable {sibs / 'memory'}"]


def test_a_cached_root_with_no_clone_and_no_ak_yet_installs_from_the_root_as_before(tree):
    root = cached(tree)
    (tree["claude"] / "plugins" / "marketplaces" / "agentic-kit").mkdir(parents=True)  # a clone without plugins/ak
    assert installs(run(tree, root=root, UV_NO_TOOLS="1")) == [f"tool install --editable --force --refresh {root}"]


def test_a_cached_root_whose_clone_is_gone_keeps_the_installed_ak(tree):
    """`claude plugin marketplace update` deletes the clone and writes the new one at the same path: a session
    starting in between keeps the `ak` installed from the clone, and once the clone is back the gate is quiet."""
    plugins = clone(tree)
    assert installs(run(tree, root=cached(tree, "4.3.0")))
    hidden = plugins.parent.with_name("agentic-kit.updating")
    plugins.parent.rename(hidden)
    assert run(tree, root=cached(tree, "4.3.1")) == ["tool list"]  # looked for an ak, installed nothing
    hidden.rename(plugins.parent)
    assert run(tree, root=tree["claude"] / "plugins" / "cache" / "agentic-kit" / "ak" / "4.3.1") == []


def test_a_root_outside_the_cache_installs_from_the_root(tree):
    """A local-folder marketplace (the release clone) runs plugins in place: a clone elsewhere changes nothing."""
    clone(tree, marketplace="ak")
    plugins = tree["plugins"].resolve()
    assert installs(run(tree)) == [f"tool install --editable --force --refresh {tree['plugins'] / 'ak'} "
                                   f"--with-editable {plugins / 'vault'} --with-editable {plugins / 'memory'}"]


def test_ak_src_wins_over_the_clone(tree):
    clone(tree)
    assert installs(run(tree, root=cached(tree), AK_SRC=str(tree["plugins"] / "ak")))[0].startswith(
        f"tool install --editable --force --refresh {tree['plugins'] / 'ak'} ")


def test_a_version_bump_of_the_cached_root_does_not_reinstall(tree):
    """The source is the clone's path, the same for every version: the gate stays quiet, and a session still
    on the old version does not reinstall the old path."""
    clone(tree)
    old, new = cached(tree, "4.3.0"), cached(tree, "4.3.1")
    assert installs(run(tree, root=old))
    assert run(tree, root=new) == []
    assert run(tree, root=old) == []
