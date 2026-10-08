"""Each block has one home, prompts/<name>.txt, and the hooks deploy the whole file verbatim at its moment.

The file is the text the agent reads and nothing else: no frontmatter, no headings, no why (that lives
in the commit and the eval's story). This test holds that shape, the budgets and the wiring.
The session line is also held by plugins/memory/tests/test_session_start_contract.py (800
characters a plugin and 2,400 together in a program's session, with a vault planted; nothing written).
"""
from __future__ import annotations

import json
import re

import pytest
from blocks import PROMPTS, load, tag
from conftest import PLUGIN, PROMPT, run, snapshot, write_transcript

# session: HOOKS.md §3 gives all start lines 2,400 together in a program's session; with a vault the
# five plugins print 2,399 (2026-09-28). The contract test holds the sum, this the plugin's own share.
# The others: the kit's design sized each block; a block over its budget is cut, not raised.
BUDGET = {"session": 370, "instruction-files": 628, "agent-text": 307, "brief": 388, "hooks": 376,
          "skills": 486, "evals": 617}
# The one skill each block names: the block sends the agent there, it never waits to be picked.
DEPTH = {"session": "harness-engineering", "hooks": "harness-engineering",
         "instruction-files": "context-engineering", "agent-text": "context-engineering", "brief": "context-engineering",
         "skills": "skill-development", "evals": "evals"}
ON_EDIT = sorted(set(BUDGET) - {"session"})
LAUNCH = 'uv run --quiet --no-project --python ">=3.10" "${CLAUDE_PLUGIN_ROOT}/hooks/%s"'
BASH = {"Bash(claude *)": "evals", "Bash(env *claude *)": "evals", "Bash(tmux *)": "evals"}


@pytest.mark.parametrize("name", sorted(BUDGET))
def test_each_block_is_only_the_text_the_agent_reads(name):
    text = (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")
    assert not text.startswith("---"), f"{name}: frontmatter is for a reader who never sees this file"
    assert not re.search(r"^#{1,6} |^```|\*\*", text, re.M), f"{name}: markdown the agent pays for and never needs"


def test_prompts_holds_only_blocks_with_a_budget():
    assert sorted(p.name for p in PROMPTS.iterdir()) == sorted(f"{n}.txt" for n in BUDGET)


@pytest.mark.parametrize("name,limit", sorted(BUDGET.items()))
def test_each_block_is_within_its_budget(name, limit):
    assert len(load(name)) <= limit, (name, len(load(name)), "every character is paid each time it fires")


@pytest.mark.parametrize("name", sorted(BUDGET))
def test_each_block_names_the_skill_that_holds_the_depth(name):
    assert DEPTH[name] in load(name), "the block sends the agent to the skill; it never waits to be picked"


def test_each_on_edit_block_opens_on_its_own_tag():
    """on_edit.py knows a block was said, or a merge held, by its opening tag in the transcript."""
    tags = [tag(load(n)) for n in ON_EDIT]
    assert len(set(tags)) == len(tags)
    for name, t in zip(ON_EDIT, tags):
        assert re.fullmatch(r"\[agentic-engineering: [a-z -]+\]", t) and load(name).startswith(t + " "), t
        assert t.isascii() and '"' not in t and "\\" not in t, "a tag must read the same in every record Claude Code writes"


@pytest.mark.parametrize("event", ["SessionStart", "SubagentStart"])
@pytest.mark.parametrize("entrypoint", ["cli", "sdk-cli"])
def test_session_says_the_map_and_nothing_else(home, event, entrypoint):
    payload = {"session_id": "s-0000", "hook_event_name": event, "source": "startup", "cwd": str(home),
               "transcript_path": "/dev/null", "agent_id": "a1" if event == "SubagentStart" else None}
    before = snapshot(home)
    r = run("session.py", payload, home, entrypoint=entrypoint)
    assert r["rc"] == 0, r["stderr"][-400:]
    assert r["out"] == {"hookSpecificOutput": {"hookEventName": event, "additionalContext": load("session")}}
    assert len(r["ctx"]) <= 800, "plugins/HOOKS.md §3: a plugin's line in a program's session"
    assert snapshot(home) == before, "the start hook writes nothing"


@pytest.mark.parametrize("stdin", ["", "garbage", "[]", '{"hook_event_name": "PreToolUse"}', "{}"])
def test_session_is_silent_on_anything_else(home, stdin):
    r = run("session.py", stdin, home)
    assert r["rc"] == 0 and r["stdout"] == "" and r["stderr"] == "", stdin


def _wired() -> set[tuple[str, str, str, str]]:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    return {(event, entry.get("matcher", ""), h.get("if", ""), h["command"])
            for event, entries in hooks.items() for entry in entries for h in entry["hooks"]}


def test_hooks_json_wires_each_hook_at_its_moment_through_uv():
    """Bash gets one handler per `if` rule (a handler holds exactly one), each naming its moments."""
    on_edit = LAUNCH % "on_edit.py"
    assert _wired() == {
        ("SessionStart", "startup|resume|clear|compact|fork", "", LAUNCH % "session.py"),
        ("SubagentStart", "", "", LAUNCH % "session.py"),
        ("PreToolUse", "Write|Edit|MultiEdit|Agent|Task|Workflow", "", on_edit),
        *{("PreToolUse", "Bash", rule, f"{on_edit} {moments}") for rule, moments in BASH.items()},
        ("PostToolUse", "Write|Edit|MultiEdit", "", LAUNCH % "after_edit.py"),
    }
    for _, _, _, command in _wired():
        script = command.split("/hooks/")[-1].split('"')[0]
        assert (PLUGIN / "hooks" / script).is_file(), script


@pytest.mark.parametrize("rule,command,block", [
    ("Bash(claude *)", "claude -p hi", "evals"),
    ("Bash(env *claude *)", "env -u CLAUDECODE claude -p hi", "evals"),
    ("Bash(tmux *)", "tmux capture-pane -p -t s", "evals"),
])
def test_each_bash_handler_answers_its_command(home, tmp_path, rule, command, block):
    payload = {"hook_event_name": "PreToolUse", "cwd": str(tmp_path), "tool_name": "Bash",
               "tool_input": {"command": command}, "transcript_path": ""}
    assert run("on_edit.py", payload, home, args=tuple(BASH[rule].split()))["ctx"] == load(block)


def test_print_prompts_is_gone():
    """It printed every block into every session, claude -p included: 1,612 characters, over §3's 800."""
    assert not (PLUGIN / "scripts" / "print-prompts.sh").exists()
    assert "print-prompts" not in (PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8")
