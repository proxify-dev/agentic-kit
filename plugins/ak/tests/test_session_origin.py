"""ak.session_origin (src/ak/session_origin.py): the one person-or-program rule every ak plugin's hooks share (plugins/HOOKS.md §2)."""
from __future__ import annotations


import pytest

from ak.session_origin import automated
from ak._brand import ENV_PREFIX, env_name


@pytest.mark.parametrize("entrypoint,program", [("cli", ""), ("claude-desktop", ""), ("sdk-ts", ""), ("", ""),
                                                ("sdk-cli", "sdk-cli"), ("sdk-py", "sdk-py")])
def test_the_entrypoint_says_person_or_program(monkeypatch, entrypoint, program):
    for var in (env_name("CAPTURE"), f"EVAL_{ENV_PREFIX}CAPTURE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", entrypoint)
    assert automated() == program


@pytest.mark.parametrize("switch", [env_name("CAPTURE"), f"EVAL_{ENV_PREFIX}CAPTURE"])
def test_either_capture_switch_counts_a_program_as_a_person(monkeypatch, switch):
    """EVAL_AK_CAPTURE: `claude plugin eval` passes only EVAL_* variables to a case."""
    for var in (env_name("CAPTURE"), f"EVAL_{ENV_PREFIX}CAPTURE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
    monkeypatch.setenv(switch, "1")
    assert automated() == ""
