"""The drill's [raw] flag — review G15: a bare "/tool-results/" marker, matched over the
whole tool_input blob, flagged `head -12 plugins/tool-results/README.md` as a raw read of
the transcript store. It is not one: `plugins/tool-results/` is this repo's own plugin
folder, not a session's `~/.claude/projects/<p>/<session>/tool-results/` directory.

`annotate()` is exercised directly on hand-built Turn/Step objects — the flag is a pure
function of one step's tool and input, and does not need a transcript on disk.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tracer._brand import GATEWAY_DATA, HOME_DIR
from tracer.drill import annotate, _is_raw_read, _raw_read_paths
from tracer.turns import Step, Turn

SDIR = Path("/nonexistent-session-dir")  # annotate() only touches this when s.answered


def _turn(tool: str, **input_) -> Turn:
    step = Step(n=1, tool=tool, tool_use_id="toolu_1", input=input_, ts="2026-09-27T00:00:00.000Z",
               line=1, message_id="m1")
    return Turn(prompt={"timestamp": "2026-09-27T00:00:00.000Z"}, line=0, steps=[step])


def _flags(tool: str, **input_) -> list[str]:
    notes = annotate(_turn(tool, **input_), SDIR)
    return notes[1].flags


# --- the reported false positive -------------------------------------------------------

def test_reading_the_tool_results_plugins_own_readme_is_not_raw():
    assert "raw" not in _flags("Bash", command="head -12 plugins/tool-results/README.md")


def test_a_grep_pattern_that_says_tool_results_is_not_raw_either():
    """The pattern is what is being searched FOR, not a path being read; only Grep's
    `path` field is a location on disk."""
    assert "raw" not in _flags("Grep", pattern="tool-results", path="/Users/x/ak/plugins")


def test_grep_over_a_repo_folder_that_happens_to_be_named_tool_results_is_not_raw():
    assert "raw" not in _flags("Grep", pattern="anything", path="plugins/tool-results")


def test_editing_a_real_transcript_path_is_not_flagged_edit_is_not_a_raw_tool():
    assert "raw" not in _flags("Edit", file_path="/Users/x/.claude/projects/-Users-x-ak/s/t.jsonl")


# --- real raw reads, one per surface + one per path form --------------------------------

@pytest.mark.parametrize("command", [
    "cat ~/.claude/projects/-Users-x-ak/57e7a5bf.jsonl",
    "head -50 ~/.claude/projects/-Users-x-ak/57e7a5bf/tool-results/toolu_01.Bash.txt",
    "sed -n '1,20p' $HOME/.claude/projects/-Users-x-ak/57e7a5bf.jsonl",
    "jq '.message' ${HOME}/.claude/projects/-Users-x-ak/57e7a5bf.jsonl",
    f"sqlite3 /Users/x/{HOME_DIR}/db/brain.db 'select count(*) from observations'",
    f"sqlite3 ~/{HOME_DIR}/db/traces/traces.db 'select outcome, count(*) from spans group by 1'",
])
def test_real_raw_reads_by_bash(command):
    assert "raw" in _flags("Bash", command=command)


def test_raw_read_by_read_tool():
    assert "raw" in _flags("Read", file_path="/Users/x/.claude/projects/-Users-x-ak/57e7a5bf.jsonl")


def test_raw_read_by_grep_tool():
    assert "raw" in _flags("Grep", pattern="entrypoint", path="/Users/x/.claude/projects")


def test_raw_read_by_glob_tool():
    assert "raw" in _flags("Glob", pattern="*.jsonl", path="~/.claude/projects/-Users-x-ak")


def test_the_tracers_own_index_and_the_gateways_blobs_are_raw_too():
    assert "raw" in _flags("Bash", command="sqlite3 ~/.claude/plugins/data/conversation-index/index.db .tables")
    assert "raw" in _flags("Bash", command=f"cat ~/.local/share/{GATEWAY_DATA}/blobs/ab/cdef0123")


@pytest.mark.parametrize("command", [
    "sqlite3 ~/.brain/.store/brain.db 'select count(*) from observations'",  # brand: historical
    "sqlite3 /Users/x/.brain/.store/traces/traces.db .tables",  # brand: historical
    "cat ~/.local/share/brain-gateway/blobs/ab/cdef0123",  # brand: historical
    f"sqlite3 ~/{HOME_DIR}/.store/brain.db 'select count(*) from observations'",  # layout 1: historical
])
def test_a_raw_read_recorded_before_the_rename_is_still_raw(command):
    """The step's command is stored data: a session run before the rename (or before layout 2) typed the old folders,
    and `inspect` on it must flag them whatever the brand is now."""
    assert "raw" in _flags("Bash", command=command)


# --- the extraction helper, in isolation ------------------------------------------------

def test_raw_read_paths_ignores_the_pattern_field_for_grep_and_glob():
    assert _raw_read_paths("Grep", {"pattern": ".claude/projects", "path": "/tmp"}) == ["/tmp"]
    assert _raw_read_paths("Glob", {"pattern": "**/.claude/projects/**"}) == []


def test_is_raw_read_is_false_for_a_tool_with_no_path_at_all():
    assert _is_raw_read("Grep", {"pattern": "x"}) is False
    assert _is_raw_read("Bash", {"command": "echo hi"}) is False
