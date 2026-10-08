"""hooks/install-global.py: `ak` on PATH as one uv tool — ak, with the vault and memory beside it.

Run against a fabricated plugins/ tree and a fake `uv` that logs what it is asked, so nothing on this
machine is installed or removed: the tool environment the hook builds, the gate that skips a second
run, a sibling's pyproject that reopens it, another tool that puts `ak` on PATH taken out first, and a
program's session that installs nothing.
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
if [ "$1 $2" = "tool list" ]; then
  printf 'ak v1.0.0\\n- ak\\n- hooktrace\\nak-core v3.2.0\\n- ak\\n- hooktrace\\nruff v0.6.0\\n- ruff\\n'
fi
exit 0
"""


@pytest.fixture
def tree(tmp_path):
    plugins = tmp_path / "plugins"
    (plugins / "ak" / "hooks").mkdir(parents=True)
    for name in ("install-global.py", "_brand.py"):
        shutil.copy2(HOOKS / name, plugins / "ak" / "hooks" / name)
    (plugins / "ak" / "src" / "ak").mkdir(parents=True)
    for name in ("__init__.py", "_brand.py", "session_origin.py"):  # the one rule the installer takes from its src/
        shutil.copy2(SRC / name, plugins / "ak" / "src" / "ak" / name)
    for name in ("ak", "vault", "memory"):
        (plugins / name).mkdir(exist_ok=True)
        dist = "ak" if name == "ak" else f"ak-{name}"
        (plugins / name / "pyproject.toml").write_text(f'[project]\nname = "{dist}"\nversion = "1"\n')
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "uv").write_text(FAKE_UV)
    (bin_ / "uv").chmod(0o755)
    log = tmp_path / "uv.log"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AK_", "CLAUDE"))}
    env.update(PATH=f"{bin_}:/usr/bin:/bin", UV_LOG=str(log), CLAUDE_PLUGIN_ROOT=str(plugins / "ak"),
               CLAUDE_PLUGIN_DATA=str(tmp_path / "data"), CLAUDE_CODE_ENTRYPOINT="cli")
    return {"plugins": plugins, "log": log, "env": env}


def run(tree, **env):
    p = subprocess.run([sys.executable, str(tree["plugins"] / "ak" / "hooks" / "install-global.py")], capture_output=True,
                       text=True, env={**tree["env"], **env}, timeout=30)
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
