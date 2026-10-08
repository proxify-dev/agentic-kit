"""Windows spellings through the hooks, run on any OS: `~\\x`, pwsh and cmd. (mdmap's moved with it: vault/tests/test_portable.py.)"""
from __future__ import annotations

import after_edit
import on_edit
import pytest

@pytest.mark.parametrize("target, windows, want", [
    ("~/x.md", False, True),
    ("~/x.md", True, True),
    ("~\\x.md", True, True),
    ("~\\x.md", False, False),  # on POSIX "\" is a filename character, not a separator
    ("docs/x.md", True, False),
])
def test_a_home_link_is_either_separator_where_it_is_one(monkeypatch, target, windows, want):
    monkeypatch.setattr(after_edit.os, "sep", "\\" if windows else "/")
    assert after_edit._from_home(target) is want


def test_a_path_is_shown_with_slashes(tmp_path):
    assert after_edit._shown(tmp_path / "sub" / "CLAUDE.md", str(tmp_path)) == "sub/CLAUDE.md"


@pytest.mark.parametrize("command", [
    'pwsh -Command "claude -p hi"',
    'powershell.exe -Command "tmux ls"',
    "cmd /c claude -p hi",
    "cmd.exe /C claude --print hi",
    "C:/Tools/claude.exe -p hi",
    "bash -c 'claude -p hi'",
])
def test_windows_shells_and_exe_names_start_the_evals_moment(command):
    assert on_edit.moments(command) == {"evals"}


@pytest.mark.parametrize("command", ["pwsh -Command ls", "cmd /c dir", "claude.exe --version"])
def test_windows_shells_running_anything_else_are_quiet(command):
    assert on_edit.moments(command) == set()
