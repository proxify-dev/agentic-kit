"""`ak setup` from a built wheel: what `uvx --from git+<url>#subdirectory=plugins/ak ak setup` runs.

The wheel carries only the `ak` package — no vault, no memory, no sibling plugins/ folder — so setup
must run from it alone: parse as JSON on stdout, say nothing on stderr, and never hand off to the
checkout it happens to be run from (that would build a .venv inside the release clone).

Builds and installs go through uv's cache; with no cache and no network the tests skip rather than fail.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from ak._brand import env_name

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parents[1]
REPO_URL = "https://example.invalid/kit.git"  # never fetched: a dry run only says what it would clone
UV = shutil.which("uv")
UVX = shutil.which("uvx")
NETWORK = re.compile(r"network|dns|connect|offline|resolve|timed out|tls|certificate", re.I)
FAKE_TOOL = """#!/bin/sh
case "$*" in
  "--version") echo "1.2.3" ;;
  "plugin list --json"|"plugin marketplace list --json") echo "[]" ;;
esac
exit 0
"""

pytestmark = [pytest.mark.skipif(UV is None, reason="uv is not installed"),
              pytest.mark.skipif(sys.platform == "win32", reason="the venv layout and fake tools are POSIX")]


def sh(argv, **kw):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=300, **kw)
    if p.returncode and NETWORK.search(p.stderr):
        pytest.skip(f"needs the network or a warm uv cache: {p.stderr.strip().splitlines()[-1]}")
    return p


@pytest.fixture(scope="module")
def fakes(tmp_path_factory):
    """A PATH folder holding claude, uv and git that answer the probes and do nothing else."""
    bin_ = tmp_path_factory.mktemp("fakebin")
    for name in ("claude", "uv", "git"):
        (bin_ / name).write_text(FAKE_TOOL)
        (bin_ / name).chmod(0o755)
    return bin_


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    p = sh([UV, "build", "--wheel", "--out-dir", str(out), str(CORE)])
    assert p.returncode == 0, p.stderr
    return next(out.glob("ak-*.whl"))


@pytest.fixture(scope="module")
def venv(wheel, tmp_path_factory):
    """A fresh venv holding the wheel and its dependencies, nothing else."""
    root = tmp_path_factory.mktemp("venv") / "v"
    assert sh([UV, "venv", "--python", sys.executable, str(root)]).returncode == 0
    p = sh([UV, "pip", "install", "--python", str(root / "bin" / "python"), str(wheel)])
    assert p.returncode == 0, p.stderr
    return root


def clean_env(fakes, home: Path, **extra) -> dict:
    env = {"PATH": f"{fakes}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(home), env_name("HOME"): str(home / "kit"),
           "COLUMNS": "200", **extra}
    return env


def test_the_wheel_carries_the_ak_package_only(wheel):
    names = zipfile.ZipFile(wheel).namelist()
    assert "ak/cli.py" in names and "ak/_brand.py" in names and "ak/setup/cli.py" in names and "ak/setup/kit.json" in names
    assert not [n for n in names if n.startswith(("vault/", "memory/", "tests/"))]


def test_setup_dry_run_json_parses_and_stderr_is_empty(venv, fakes, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # run from the checkout, in rich mode: the paths that would hand off to it, or warn about a missing plugins/, are the ones under test
    p = subprocess.run([str(venv / "bin" / "ak"), "setup", "--dry-run", "--json", "--repo", REPO_URL],
                       capture_output=True, text=True, cwd=REPO,
                       env=clean_env(fakes, home, **{env_name("OUTPUT"): "rich"}), timeout=60)
    assert p.returncode == 0, p.stderr
    doc = json.loads(p.stdout)
    assert {"machine", "components", "steps", "skipped", "run", "verify"} <= set(doc)
    # no kit on disk: the public kit this ak carries (setup/kit.json), never a private plugin
    assert doc["kit"] == {"tree": None, "marketplace": "agentic-kit"}
    ids = {c["id"] for c in doc["components"]}
    assert ids >= {"ak", "tracer", "shim", "gateway"} and not ids & {"vault", "memory", "observer", "tool-results"}
    assert doc["steps"][0]["title"].endswith(REPO_URL), doc["steps"][0]  # --repo reached the engine
    assert "no plugins folder" not in p.stderr and p.stderr.strip() == ""
    assert not (home / "kit" / "db" / "setup").exists()  # a dry run leaves no run folder


def test_importing_the_cli_does_not_import_vault_memory_or_textual(venv, tmp_path):
    probe = "import sys, ak.cli, ak.setup.cli; print(sorted(m for m in ('vault', 'memory', 'textual') if m in sys.modules))"
    p = subprocess.run([str(venv / "bin" / "python"), "-c", probe], capture_output=True, text=True, cwd=tmp_path,
                       env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}, timeout=60)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "[]"


def test_the_wheel_finds_no_plugins_folder_for_the_other_verbs_either(venv, fakes, tmp_path):
    """_plugins_dir() is None from a wheel: nothing to glob, so nothing from a random folder is mounted."""
    probe = "from ak import cli; print(cli._plugins_dir())"
    p = subprocess.run([str(venv / "bin" / "python"), "-c", probe], capture_output=True, text=True, cwd=tmp_path,
                       env=clean_env(fakes, tmp_path), timeout=60)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "None"


@pytest.mark.skipif(UVX is None, reason="uvx is not installed")
def test_uvx_from_the_source_folder_runs_setup(fakes, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    cache = subprocess.run([UV, "cache", "dir"], capture_output=True, text=True).stdout.strip()
    p = sh([UVX, "--python", sys.executable, "--from", str(CORE), "ak", "setup", "--dry-run", "--json", "--repo", REPO_URL], cwd=REPO,
           env={**clean_env(fakes, home), "UV_CACHE_DIR": cache})
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["components"]
