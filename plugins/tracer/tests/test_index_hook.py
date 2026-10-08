"""The Stop/SessionStart hook spawns its background indexers only for people's sessions.

Every `claude -p` run — observer summarizers, evals, probes; thousands on one machine —
used to start its own `tracer index` at each Stop. The next human session's
incremental pass indexes what those runs wrote. The same gate holds for the plugin's
tool-call indexer (`node traces/bin/traces.mjs index`), which the hook launches first.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tracer._brand import env_name

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "convo-index-stop.py"
# The hook finds node on PATH; the tests give it the real one (and nothing else from PATH).
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="no node on PATH")


def _run(tmp_path: Path, wait: bool = True, **env) -> bool:
    """Run the hook with a fake `uv` on PATH; True when it started the indexer. The log
    line is written before the spawn, so without ``wait`` its absence is the answer."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    marker = tmp_path / "uv-ran"
    fake = bin_dir / "uv"
    fake.write_text(f'#!/bin/sh\necho "$@" > "{marker}"\n')
    fake.chmod(0o755)
    base = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CLAUDE_PLUGIN_ROOT": str(tmp_path / "plugin"),
        "CLAUDE_PROJECT_DIR": str(tmp_path),
    }
    full = {**base, **{k: v for k, v in env.items() if v is not None}}
    proc = subprocess.run([sys.executable, str(HOOK)], env=full, capture_output=True, timeout=10)
    assert proc.returncode == 0
    if not wait:
        return (tmp_path / ".claude" / "plugins" / "data" / "conversation-index" / "stop-hook.log").exists()
    for _ in range(40):  # the indexer is backgrounded
        if marker.exists():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("entrypoint", [None, "cli", "claude-desktop", "sdk-ts"])
def test_people_index(tmp_path, entrypoint):
    assert _run(tmp_path, CLAUDE_CODE_ENTRYPOINT=entrypoint)
    assert (tmp_path / "uv-ran").read_text().split()[-2:] == ["tracer", "index"]


@pytest.mark.parametrize("entrypoint", ["sdk-cli", "sdk-py", "something-new"])
def test_programs_do_not(tmp_path, entrypoint):
    assert not _run(tmp_path, wait=False, CLAUDE_CODE_ENTRYPOINT=entrypoint)


def test_brain_capture_counts_a_program_as_a_person(tmp_path):
    assert _run(tmp_path, CLAUDE_CODE_ENTRYPOINT="sdk-cli", **{env_name("CAPTURE"): "1"})


# --- the tool-call index: traces/bin/traces.mjs, launched by the same hook ----------------
# Laid out as a session runs it: the plugin in place at <release>/plugins/tracer,
# HOME a throwaway, a fake entry that appends "<which> <args>" to a marker file.
# `plugin` is a tracer plugin root: the one the hook runs from, or $AK_CODE/plugins/tracer.
def _launcher(plugin: Path, name: str, marker: Path, sleep: float = 0) -> Path:
    exe = plugin / "traces" / "bin" / "traces.mjs"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text('import { appendFileSync } from "node:fs"\n'
                   f'setTimeout(() => appendFileSync({json.dumps(str(marker))}, '
                   f'{json.dumps(name)} + " " + process.argv.slice(2).join(" ") + "\\n"), {int(sleep * 1000)})\n',
                   encoding="utf-8")
    return exe


def _traces(tmp_path: Path, *, uv: bool = True, **env) -> tuple[subprocess.CompletedProcess, float]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if uv:
        (bin_dir / "uv").write_text("#!/bin/sh\n")
        (bin_dir / "uv").chmod(0o755)
    plugin = tmp_path / "release" / "plugins" / "tracer"
    plugin.mkdir(parents=True, exist_ok=True)  # `..` resolves only through a real directory
    base = {
        "PATH": os.pathsep.join([str(bin_dir), str(Path(NODE).parent) if NODE else "", "/usr/bin", "/bin"]),
        "HOME": str(tmp_path),
        "CLAUDE_PLUGIN_ROOT": str(plugin),
        "CLAUDE_PROJECT_DIR": str(tmp_path),
    }
    full = {**base, **{k: v for k, v in env.items() if v is not None}}
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, str(HOOK)], env=full, capture_output=True, text=True, timeout=10)
    return proc, time.monotonic() - t0


def _ran(marker: Path, wait: float = 2.0) -> list[str]:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if marker.exists():
            return marker.read_text().splitlines()
        time.sleep(0.05)
    return []


def _log(tmp_path: Path) -> str:
    p = tmp_path / ".claude" / "plugins" / "data" / "conversation-index" / "stop-hook.log"
    return p.read_text() if p.exists() else ""


@pytest.mark.parametrize("entrypoint", [None, "cli", "claude-desktop", "sdk-ts"])
@needs_node
def test_a_person_launches_the_trace_indexer(tmp_path, entrypoint):
    marker = tmp_path / "traces-ran"
    _launcher(tmp_path / "code" / "plugins" / "tracer", "pinned", marker)
    proc, _ = _traces(tmp_path, **{env_name("CODE"): str(tmp_path / "code")}, CLAUDE_CODE_ENTRYPOINT=entrypoint)
    assert proc.returncode == 0 and proc.stdout == "" and proc.stderr == ""
    assert _ran(marker) == ["pinned index"]


@pytest.mark.parametrize("entrypoint", ["sdk-cli", "sdk-py"])
def test_a_program_does_not_launch_it(tmp_path, entrypoint):
    marker = tmp_path / "traces-ran"
    _launcher(tmp_path / "code" / "plugins" / "tracer", "pinned", marker)
    proc, _ = _traces(tmp_path, **{env_name("CODE"): str(tmp_path / "code")}, CLAUDE_CODE_ENTRYPOINT=entrypoint)
    assert proc.returncode == 0 and proc.stdout == ""
    assert _ran(marker, wait=0.5) == []
    assert _log(tmp_path) == ""  # a program's session writes nothing, the log included


@needs_node
def test_brain_capture_launches_it_for_a_program(tmp_path):
    marker = tmp_path / "traces-ran"
    _launcher(tmp_path / "code" / "plugins" / "tracer", "pinned", marker)
    _traces(tmp_path, **{env_name("CODE"): str(tmp_path / "code")}, CLAUDE_CODE_ENTRYPOINT="sdk-cli",
            **{env_name("CAPTURE"): "1"})
    assert _ran(marker) == ["pinned index"]


@needs_node
def test_the_indexer_is_brain_code_s_then_the_plugin_s_own(tmp_path):
    marker = tmp_path / "traces-ran"
    _launcher(tmp_path / "code" / "plugins" / "tracer", "pinned", marker)
    _launcher(tmp_path / "release" / "plugins" / "tracer", "own", marker)

    _traces(tmp_path, **{env_name("CODE"): str(tmp_path / "code")})
    assert _ran(marker) == ["pinned index"]
    marker.unlink()

    _traces(tmp_path)  # no code root set: the one this plugin ships
    assert _ran(marker) == ["own index"]
    marker.unlink()

    _traces(tmp_path, **{env_name("CODE"): str(tmp_path / "nowhere")})  # a code root without one: still its own
    assert _ran(marker) == ["own index"]


def test_no_indexer_anywhere_exits_0_silently_and_logs_one_line(tmp_path):
    proc, _ = _traces(tmp_path, **{env_name("CODE"): str(tmp_path / "nowhere")})
    assert proc.returncode == 0 and proc.stdout == "" and proc.stderr == ""
    lines = [ln for ln in _log(tmp_path).splitlines() if "traces-index" in ln]
    assert len(lines) == 1 and "no traces/bin/traces.mjs under" in lines[0], _log(tmp_path)


def test_the_real_plugin_carries_its_indexer():
    """The hook runs `node $root/traces/bin/traces.mjs`: in this tree that is a real entry beside its bundle."""
    root = HOOK.parents[1]
    assert (root / "traces" / "bin" / "traces.mjs").is_file()
    assert (root / "traces" / "dist" / "traces.mjs").is_file()


@needs_node
def test_it_needs_node_not_uv(tmp_path):
    marker = tmp_path / "traces-ran"
    _launcher(tmp_path / "code" / "plugins" / "tracer", "pinned", marker)
    proc, _ = _traces(tmp_path, uv=False, **{env_name("CODE"): str(tmp_path / "code")})
    assert proc.returncode == 0
    assert _ran(marker) == ["pinned index"]


@needs_node
def test_the_hook_returns_before_the_indexer_finishes(tmp_path):
    """Detached: the turn never waits on the index, and the pipes Claude Code reads
    close with the hook, not with the indexer."""
    marker = tmp_path / "traces-ran"
    _launcher(tmp_path / "code" / "plugins" / "tracer", "slow", marker, sleep=3)
    proc, took = _traces(tmp_path, **{env_name("CODE"): str(tmp_path / "code")})
    assert proc.returncode == 0 and proc.stdout == ""
    assert took < 1.5, f"the hook waited {took:.2f}s"
    assert not marker.exists()


# --- a node found off PATH -----------------------------------------------------------------
@needs_node
@pytest.mark.skipif(sys.platform == "win32", reason="nvm-windows has another layout")
def test_a_node_off_path_is_found_where_nvm_puts_it(tmp_path):
    """A GUI app's PATH has no nvm on it; the hook looks in ~/.nvm/versions, newest version first."""
    marker = tmp_path / "traces-ran"
    _launcher(tmp_path / "release" / "plugins" / "tracer", "own", marker)
    for v in ("v9.0.0", "v22.100.0"):
        d = tmp_path / ".nvm" / "versions" / "node" / v / "bin"
        d.mkdir(parents=True)
        (d / "node").symlink_to(NODE)
    proc, _ = _traces(tmp_path, PATH=str(tmp_path / "bin"), NVM_DIR=str(tmp_path / ".nvm"))
    assert proc.returncode == 0 and proc.stderr == ""
    assert _ran(marker) == ["own index"]
    assert "v22.100.0" in _log(tmp_path)  # the newest, not the v9 a string sort would put first


def _hook_module():
    import importlib.util
    sys.path.insert(0, str(HOOK.parent))
    spec = importlib.util.spec_from_file_location("convo_index_stop", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_versions_sort_by_number_not_by_string(tmp_path):
    hook = _hook_module()
    paths = [tmp_path / v / "bin" / "node" for v in ("v9.11.2", "v22.13.0", "v22.9.0", "v18.20.1")]
    assert [p.parts[-3] for p in hook._newest_first(paths)] == ["v22.13.0", "v22.9.0", "v18.20.1", "v9.11.2"]


@pytest.mark.skipif(sys.platform == "win32", reason="the fake nodes are shell scripts")
def test_a_node_below_the_floor_is_skipped_and_named(tmp_path, monkeypatch):
    hook = _hook_module()

    def fake(name, version):
        p = tmp_path / name
        p.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
        p.chmod(0o755)
        return p

    old, older_minor, new = fake("old", "v20.5.0"), fake("minor", "v22.12.9"), fake("new", "v22.13.0")
    monkeypatch.setattr(hook, "_node_candidates", lambda: iter([None, tmp_path / "absent", old, older_minor, new]))
    assert hook.find_node() == (str(new), None)
    monkeypatch.setattr(hook, "_node_candidates", lambda: iter([old, older_minor]))
    node, why = hook.find_node()
    assert node is None and why.startswith(f"node 20.5.0 at {old} is below the required 22.13")
    monkeypatch.setattr(hook, "_node_candidates", lambda: iter([]))
    assert hook.find_node() == (None, "no node >= 22.13 on PATH or in the usual install places")
