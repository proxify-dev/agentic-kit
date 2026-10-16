"""file-context (PreToolUse · Read) says ONE line: how many past observations touched the file,
and the command that lists them. It used to inject up to ten rows on every Read; over 623
firings the agent used one of them once."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
# how hooks.json starts a hook: uv picks a 3.10+ Python, never PATH's python3 (plugins/HOOKS.md §1)
UV_RUN = ["uv", "run", "--quiet", "--no-project", "--python", ">=3.10"]
sys.path.insert(0, str(PLUGIN / "hooks"))
from _brand import env_name  # noqa: E402
from observer.verbs import command
import store_common as sc  # noqa: E402


@pytest.fixture
def world(tmp_path, monkeypatch):
    vault, repo = tmp_path / "vault", tmp_path / "repo"
    (vault / "cache").mkdir(parents=True)
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    target = repo / "src" / "runner.ts"
    target.write_text("export const x = 1\n")
    monkeypatch.setenv(env_name("PATH"), str(vault))
    monkeypatch.setenv(env_name("STORE"), str(tmp_path / "store"))
    sc.init(from_claude_mem=False)
    conn = sc.connect()
    for i, files in enumerate(([str(target)], ["src/runner.ts"], ["src/runner.ts", "README.md"], ["other/runner.ts"])):
        conn.execute("INSERT INTO observations(memory_session_id, project, type, title, narrative, facts, concepts, "
                     "files_read, files_modified, created_at, created_at_epoch) VALUES(?, 'repo', 'discovery', ?, '', "
                     "'[]', '[]', ?, '[]', '2026-09-20T00:00:00Z', ?)", (f"m{i}", f"row {i}", json.dumps(files), i))
    conn.commit()
    conn.close()
    return repo, target


def run(target: Path, cwd: Path, entrypoint: str = "cli", **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_ENTRYPOINT", env_name("CAPTURE"))}
    env.update(CLAUDE_PLUGIN_ROOT=str(PLUGIN), CLAUDE_CODE_ENTRYPOINT=entrypoint)
    p = subprocess.run([*UV_RUN, str(PLUGIN / "hooks" / "file-context.py")],
                       input=json.dumps({"session_id": "s", "cwd": str(cwd), "tool_name": "Read",
                                         "tool_input": {"file_path": str(target)}, **extra}),
                       capture_output=True, text=True, cwd=cwd, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


def test_one_line_with_the_count_and_the_command(world):
    repo, target = world
    ctx = run(target, repo)["hookSpecificOutput"]["additionalContext"]
    # the absolute row, and the two repo-relative rows of the same project; not other/runner.ts
    assert ctx == f"3 past observations on this file: {command('search')} --files {target}"
    assert "\n" not in ctx


def test_a_file_no_row_touched_says_nothing(world):
    repo, _ = world
    other = repo / "src" / "quiet.ts"
    other.write_text("")
    assert run(other, repo) == {}


def test_a_programs_session_gets_no_pointer(world):
    repo, target = world
    assert run(target, repo, entrypoint="sdk-cli") == {}


def test_a_subagent_gets_no_pointer(world):
    repo, target = world
    assert run(target, repo, agent_id="a1b2c3", agent_type="Explore") == {}
