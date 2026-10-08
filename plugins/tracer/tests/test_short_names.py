"""`sessions …` and `trace …`: the short names the tracer's skills write (skills/index/bin).

Claude Code appends a plugin's bin/ after /usr/bin, and macOS has a `trace` there, so hooks/session.py
puts skills/index/bin first on the Bash tool's PATH through CLAUDE_ENV_FILE, which Claude Code sources
before every Bash call. The hook runs the way Claude Code runs it (plugins/HOOKS.md §5).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SHORT = PLUGIN / "skills" / "index" / "bin"
UV_RUN = ["uv", "run", "--quiet", "--no-project", "--python", ">=3.10"]
UV_ENV = {k: subprocess.run(["uv", *argv], capture_output=True, text=True).stdout.strip()
          for k, argv in (("UV_CACHE_DIR", ["cache", "dir"]), ("UV_PYTHON_INSTALL_DIR", ["python", "dir"]))}
BASH = shutil.which("bash") or ""


def start(tmp_path: Path, env_file: Path) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_ENV_FILE"}
    env.update(HOME=str(home), CLAUDE_PLUGIN_ROOT=str(PLUGIN), CLAUDE_ENV_FILE=str(env_file), **UV_ENV)
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "hook-0000", "source": "startup"})
    p = subprocess.run([*UV_RUN, str(PLUGIN / "hooks" / "session.py")],
                       input=payload, capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr[-400:]
    return json.loads(p.stdout)


def test_session_start_puts_the_short_names_first_once(tmp_path):
    """A resume, /clear or compaction starts the hook again: one PATH line, and the teaching line still says it."""
    env_file = tmp_path / "env.sh"
    out = start(tmp_path, env_file)
    start(tmp_path, env_file)
    assert out["hookSpecificOutput"]["additionalContext"].startswith("[tracer]")
    lines = env_file.read_text().splitlines()
    assert lines == [f"export PATH='{SHORT}':\"$PATH\""] or lines == [f"export PATH={SHORT}:\"$PATH\""]


@pytest.mark.skipif(not BASH, reason="the Bash tool's shell")
def test_sourced_env_file_wins_over_usr_bin(tmp_path):
    """The env file sourced as Claude Code does: `trace` and `sessions` are ours, ahead of /usr/bin/trace."""
    env_file = tmp_path / "env.sh"
    start(tmp_path, env_file)
    p = subprocess.run([BASH, "-c", f"export PATH=/usr/bin:/bin; source {env_file}; command -v trace sessions"],
                       capture_output=True, text=True, timeout=30)
    assert p.stdout.split() == [str(SHORT / "trace"), str(SHORT / "sessions")]


@pytest.mark.skipif(not BASH, reason="the launchers are bash")
@pytest.mark.parametrize("name", ["sessions", "trace"])
def test_short_name_runs_the_tracer_group(name):
    """`sessions -h` prints what `tracer sessions -h` prints: the same console script, the same group."""
    env = {**os.environ, "CLAUDECODE": "1"}
    short = subprocess.run([str(SHORT / name), "-h"], capture_output=True, text=True, env=env, timeout=120)
    long = subprocess.run([str(PLUGIN / "bin" / "tracer"), name, "-h"], capture_output=True, text=True,
                          env=env, timeout=120)
    assert short.returncode == 0, short.stderr[-400:]
    assert short.stdout == long.stdout and short.stdout


def _score():
    path = PLUGIN / "evals" / "tool-routing" / "score.py"
    if not path.is_file():
        pytest.skip("the eval data stays on the owner's machine")  # cc-tracer and the public kit leave evals/ out
    spec = importlib.util.spec_from_file_location("score", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.short


@pytest.mark.parametrize("bare,full", [
    ("sessions show latest", "tracer sessions show latest"),
    ("sessions", "tracer sessions"),
    ("trace turn abc12345-003 --open 1,4", "tracer trace turn abc12345-003 --open 1,4"),
    ("trace blame src/x.py", "tracer trace blame src/x.py"),
    ("cd /x && sessions search 'zvec lock'", "cd /x && tracer sessions search 'zvec lock'"),
])
def test_eval_scores_a_short_name_as_the_tracer(bare, full):
    """The tool-routing scorer reads `sessions …` / `trace …` as the tracer's door, the same move."""
    short = _score()
    assert short("Bash", {"command": bare}) == short("Bash", {"command": full}) != "Bash"


def test_eval_leaves_trace_as_an_argument_alone():
    """Only where a command starts: `git log --trace` and `echo sessions` are other tools' business."""
    short = _score()
    assert short("Bash", {"command": "git log --trace x"}) == "Bash"
    assert short("Bash", {"command": "echo sessions"}) == "Bash"
