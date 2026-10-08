"""The edit's two hooks: the block for the moment a call starts (on_edit.py), a lint and a note to the
person after a CLAUDE.md, AGENTS.md or CLAUDE.local.md is written (after_edit.py).

Each runs the way Claude Code runs it (conftest.run). Why before and after: an instruction surface is
edited at a moment no start block sees coming, and PreToolUse context reaches workers where
SessionStart context does not (seen 2026-09-27).
"""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest
from _brand import env_name
from blocks import PROMPTS, load
from conftest import COMPACT, PROMPT, hook_said, run, snapshot, tool_result, write_transcript

SURFACES = [
    ("CLAUDE.md", "instruction-files"),
    ("sub/AGENTS.md", "instruction-files"),
    ("CLAUDE.local.md", "instruction-files"),
    (".claude/rules/testing.md", "instruction-files"),
    ("plugins/demo/hooks/hooks.json", "hooks"),
    ("plugins/demo/hooks/gate.py", "hooks"),
    ("plugins/demo/hooks/install.sh", "hooks"),
    (".claude/hooks/check.sh", "hooks"),
    (".claude/settings.json", "hooks"),
    (".claude/settings.local.json", "hooks"),
    ("plugins/demo/skills/sweep/SKILL.md", "skills"),          # none of these exists yet: a new one
    (".claude/skills/sweep/SKILL.md", "skills"),
    (".claude/agents/reviewer.md", "skills"),
    ("plugins/demo/agents/reviewer.md", "skills"),
    ("plugins/demo/commands/go.md", "skills"),
    ("plugins/demo/.claude-plugin/plugin.json", "skills"),
    (".claude-plugin/marketplace.json", "skills"),
    ("plugins/demo/skills/old/SKILL.md", "agent-text"),        # these exist (conftest.world)
    ("plugins/demo/agents/old.md", "agent-text"),
    (".claude/commands/old.md", "agent-text"),
    ("plugins/demo/skills/sweep/references/steps.md", "agent-text"),
    (".claude/skills/sweep/references/deep/more.md", "agent-text"),
    ("plugins/demo/prompts/start.md", "agent-text"),
    ("plugins/demo/prompts/start.txt", "agent-text"),
    ("plugins/memory/templates/harness/brain-harness.md", "agent-text"),
    ("plugins/demo/tests/test_gate.py", "evals"),
    ("plugins/demo/tests/fixtures/case.json", "evals"),
    ("plugins/demo/evals/merge-waits/prompt.md", "evals"),
    ("plugins/demo/evals/merge-waits/graders/g1.py", "evals"),
]
NOT_SURFACES = [
    "src/app.py",
    "README.md",
    "src/hooks/use_cart.py",          # an app's hooks/ folder: no hooks.json, not .claude/ or a plugin
    "docs/agents/overview.md",        # a folder named agents/ that Claude Code never reads
    "prompts/system.md",              # prompts/ outside a plugin
    ".agents/memory/decisions/x.md",
    "plugins/demo/hooks/py",          # the launcher: no suffix
    "notes/CLAUDE.md.bak",
    "tests/test_app.py",              # an app's tests/: not a plugin's
    "src/evals/score.py",
    "docs/plans/roadmap.md",
    "plugins/demo/skills/sweep/scripts/run.py",
]
BLOCKS = sorted(p.stem for p in PROMPTS.glob("*.txt"))


def edit(repo, rel: str, transcript=None, tool: str = "Edit", **extra) -> dict:
    payload = {"session_id": "edit-0000", "hook_event_name": "PreToolUse", "cwd": str(repo), "tool_name": tool,
               "tool_input": {"file_path": str(repo / rel), "old_string": "a", "new_string": "b"},
               "transcript_path": str(transcript) if transcript else "", **extra}
    return payload


# ── on_edit.py: the rule for the surface, before the edit ────────────────────────────────────────


@pytest.mark.parametrize("rel,block", SURFACES)
def test_each_surface_gets_its_block_as_context(world, home, rel, block):
    r = run("on_edit.py", edit(world, rel), home)
    assert r["rc"] == 0, r["stderr"][-400:]
    assert r["ctx"] == load(block), rel
    assert r["event"] == "PreToolUse"
    assert "systemMessage" not in r["out"], "a block is for the model: additionalContext, never systemMessage"
    assert set(r["out"]) == {"hookSpecificOutput"}


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_every_edit_tool_is_answered(world, home, tool):
    assert run("on_edit.py", edit(world, "CLAUDE.md", tool=tool), home)["ctx"] == load("instruction-files")


@pytest.mark.parametrize("rel", NOT_SURFACES)
def test_any_other_file_gets_nothing(world, home, rel):
    r = run("on_edit.py", edit(world, rel), home)
    assert r["rc"] == 0 and r["stdout"] == "", rel


@pytest.mark.parametrize("tool", ["Agent", "Task", "Workflow"])
def test_a_brief_handed_to_another_agent_gets_the_brief_block(world, home, tool):
    """0d5ed015: a workflow brief handed builders a note template, and they copied it into every prompt."""
    payload = {"session_id": "edit-0000", "hook_event_name": "PreToolUse", "cwd": str(world), "tool_name": tool,
               "tool_input": {"prompt": "build it"}, "transcript_path": ""}
    r = run("on_edit.py", payload, home)
    assert r["ctx"] == load("brief") and "context-engineering" in r["ctx"]


def test_the_brief_block_is_said_once(world, home, tmp_path):
    payload = {"session_id": "edit-0000", "hook_event_name": "PreToolUse", "cwd": str(world), "tool_name": "Agent",
               "tool_input": {"prompt": "x"}}
    transcript = write_transcript(tmp_path / "t" / "s.jsonl", [PROMPT])
    first = run("on_edit.py", {**payload, "transcript_path": str(transcript)}, home)
    write_transcript(transcript, [PROMPT, *hook_said("PreToolUse", "Agent", first["stdout"], first["ctx"])])
    assert run("on_edit.py", {**payload, "transcript_path": str(transcript)}, home)["stdout"] == ""


def test_a_relative_path_is_read_from_the_sessions_cwd(world, home):
    payload = edit(world, "x")
    payload["tool_input"]["file_path"] = ".claude/settings.json"
    assert run("on_edit.py", payload, home)["ctx"] == load("hooks")


def test_the_second_edit_in_a_session_gets_nothing(world, home, tmp_path):
    transcript = write_transcript(tmp_path / "t" / "s.jsonl", [PROMPT])
    first = run("on_edit.py", edit(world, "CLAUDE.md", transcript), home)
    assert first["ctx"] == load("instruction-files")
    write_transcript(transcript, [PROMPT, *hook_said("PreToolUse", "Edit", first["stdout"], first["ctx"])])
    again = run("on_edit.py", edit(world, "sub/AGENTS.md", transcript), home)
    assert again["rc"] == 0 and again["stdout"] == "", "one block, once: it is already in context"
    other = run("on_edit.py", edit(world, "plugins/demo/hooks/hooks.json", transcript), home)
    assert other["ctx"] == load("hooks"), "each block keeps its own once"


def test_the_record_alone_counts_either_way_claude_code_writes_it(world, home, tmp_path):
    first = run("on_edit.py", edit(world, "plugins/demo/skills/s/SKILL.md"), home)
    success, context = hook_said("PreToolUse", "Write", first["stdout"], first["ctx"])
    for only in (success, context):
        t = write_transcript(tmp_path / "t" / "s.jsonl", [PROMPT, only])
        assert run("on_edit.py", edit(world, "plugins/demo/commands/go.md", t), home)["stdout"] == ""


def test_the_block_read_as_a_file_is_not_the_block_delivered(world, home, tmp_path):
    """Reading prompts/hooks.txt puts its text in a tool result; the agent has not been told at the edit."""
    t = write_transcript(tmp_path / "t" / "s.jsonl", [PROMPT, tool_result(load("hooks"))])
    assert run("on_edit.py", edit(world, ".claude/settings.json", t), home)["ctx"] == load("hooks")


def test_after_a_compaction_the_block_is_said_again(world, home, tmp_path):
    first = run("on_edit.py", edit(world, "CLAUDE.md"), home)
    t = write_transcript(tmp_path / "t" / "s.jsonl",
                         [PROMPT, *hook_said("PreToolUse", "Edit", first["stdout"], first["ctx"]), COMPACT, PROMPT])
    assert run("on_edit.py", edit(world, "CLAUDE.md", t), home)["ctx"] == load("instruction-files")


def test_a_worker_is_told_by_its_own_transcript(world, home, tmp_path):
    """A worker's context is its own file: the parent having the block does not mean the worker has it."""
    first = run("on_edit.py", edit(world, "CLAUDE.md"), home)
    said = hook_said("PreToolUse", "Edit", first["stdout"], first["ctx"])
    parent = write_transcript(tmp_path / "p" / "sess.jsonl", [PROMPT, *said])
    worker = run("on_edit.py", edit(world, "CLAUDE.md", parent, agent_id="a1b2", agent_type="general-purpose"), home)
    assert worker["ctx"] == load("instruction-files")
    write_transcript(tmp_path / "p" / "sess" / "subagents" / "agent-a1b2.jsonl",
                     hook_said("PreToolUse", "Edit", first["stdout"], first["ctx"], agent="a1b2"))
    again = run("on_edit.py", edit(world, "CLAUDE.md", parent, agent_id="a1b2"), home)
    assert again["stdout"] == ""
    other = run("on_edit.py", edit(world, "CLAUDE.md", parent, agent_id="ffff"), home)
    assert other["ctx"] == load("instruction-files")


def test_a_workflow_worker_is_told_by_its_own_transcript(world, home, tmp_path):
    """A workflow's workers are recorded one level down, <session>/subagents/workflows/<wf>/agent-<id>.jsonl
    (seen 2026-09-27); a check that looked only in subagents/ repeated the block on every edit."""
    first = run("on_edit.py", edit(world, "CLAUDE.md"), home)
    parent = write_transcript(tmp_path / "p" / "sess.jsonl", [PROMPT])
    write_transcript(tmp_path / "p" / "sess" / "subagents" / "workflows" / "wf_1" / "agent-a6169.jsonl",
                     hook_said("PreToolUse", "Edit", first["stdout"], first["ctx"], agent="a6169"))
    again = run("on_edit.py", edit(world, "CLAUDE.md", parent, agent_id="a6169"), home)
    assert again["rc"] == 0 and again["stdout"] == "", "the worker was told already"
    other = run("on_edit.py", edit(world, "CLAUDE.md", parent, agent_id="b7270"), home)
    assert other["ctx"] == load("instruction-files"), "another worker was not"


def test_a_programs_session_gets_nothing_unless_an_eval_counts_it_a_persons(world, home, tmp_path):
    """claude -p, CI: silent on an edit (plugins/HOOKS.md §3; whether a rule about the file at hand should
    reach scripted sessions is the person's open question, 0d5ed015). `claude plugin eval` passes only
    EVAL_* variables, so each case sets the capture variable's EVAL_ twin to 1 and gets what a person's
    session gets."""
    t = write_transcript(tmp_path / "t" / "s.jsonl", [PROMPT])
    before = snapshot(tmp_path)
    for rel, _ in SURFACES:
        r = run("on_edit.py", edit(world, rel, t), home, entrypoint="sdk-cli")
        assert r["rc"] == 0 and r["stdout"] == "", rel
    for rel, block in SURFACES:
        r = run("on_edit.py", edit(world, rel, t), home, entrypoint="sdk-cli", **{"EVAL_" + env_name("CAPTURE"): "1"})
        assert r["ctx"] == load(block), rel
    assert snapshot(tmp_path) == before, "the hook reads the transcript and writes nothing"


@pytest.mark.parametrize("stdin", ["", "not json", "[]", "null", '{"tool_input": "x"}',
                                   '{"tool_name": "Edit", "tool_input": {"file_path": 7}}',
                                   '{"tool_name": "Read", "tool_input": {"file_path": "/r/CLAUDE.md"}}'])
@pytest.mark.parametrize("hook", ["on_edit.py", "after_edit.py"])
def test_a_malformed_payload_is_silent(home, hook, stdin):
    r = run(hook, stdin, home)
    assert r["rc"] == 0 and r["stdout"] == "" and r["stderr"] == "", (hook, stdin, r["stderr"][-300:])


def test_a_missing_or_empty_transcript_still_delivers(world, home, tmp_path):
    empty = write_transcript(tmp_path / "t" / "empty.jsonl", [])
    empty.write_text("", encoding="utf-8")
    for t in (empty, tmp_path / "nowhere.jsonl"):
        assert run("on_edit.py", edit(world, "CLAUDE.md", t), home)["ctx"] == load("instruction-files")


@pytest.mark.parametrize("ti", [{"script": "export const meta = {name: 'x'}"}, {"scriptPath": "/x/wf.js"},
                                {"scriptPath": "/x/wf.js", "args": {"a": 1}}])
def test_a_new_workflow_run_gets_the_brief_block(world, home, ti):
    payload = {"session_id": "edit-0000", "hook_event_name": "PreToolUse", "cwd": str(world), "tool_name": "Workflow",
               "tool_input": ti, "transcript_path": ""}
    assert run("on_edit.py", payload, home)["ctx"] == load("brief"), ti


def test_a_workflow_resume_gets_nothing(world, home):
    """A resume replays briefs already handed over (tool_input carries resumeFromRunId)."""
    payload = {"session_id": "edit-0000", "hook_event_name": "PreToolUse", "cwd": str(world), "tool_name": "Workflow",
               "tool_input": {"scriptPath": "/x/wf.js", "resumeFromRunId": "wf_b9cae32a-a0e"}, "transcript_path": ""}
    r = run("on_edit.py", payload, home)
    assert r["rc"] == 0 and r["stdout"] == ""


# ── on_edit.py: a command ────────────────────────────────────────────────────────────────────────


def bash(repo, command: str, transcript=None, **extra) -> dict:
    return {"session_id": "edit-0000", "hook_event_name": "PreToolUse", "cwd": str(repo), "tool_name": "Bash",
            "tool_input": {"command": command, "description": "x"},
            "transcript_path": str(transcript) if transcript else "", **extra}


COMMANDS = [
    ("claude -p 'say hi'", "evals"),
    ("CLAUDECODE= claude --model sonnet -p hi --plugin-dir plugins/demo", "evals"),
    ("env -u CLAUDECODE -u CLAUDE_CODE_SESSION_ID claude -p hi", "evals"),
    ("claude plugin eval plugins/demo --runs 3", "evals"),
    ("tmux new-session -d -s probe 'claude --plugin-dir plugins/demo'", "evals"),
    ("timeout 120 claude --print hi", "evals"),
    ("cd plugins/demo && tmux capture-pane -p -t probe", "evals"),
    ('pwsh -Command "claude -p hi"', "evals"),                   # Windows shells
    ("cmd /c claude -p hi", "evals"),
    ("C:/Tools/claude.exe -p hi", "evals"),
]
QUIET_COMMANDS = ["git status", "ls -la", "claude --version", "claude mcp list", "echo 'claude -p hi'",
                  "git worktree add ../w", "uv run pytest -q", "git merge feature",
                  "cat > run.sh <<'EOF'\nclaude -p hi\ntmux ls\nEOF"]


@pytest.mark.parametrize("command,block", COMMANDS)
def test_a_command_that_starts_a_moment_gets_its_block(world, home, command, block):
    r = run("on_edit.py", bash(world, command), home)
    assert r["rc"] == 0 and r["ctx"] == load(block), (command, r["stderr"][-300:])
    assert set(r["out"]) == {"hookSpecificOutput"} and "permissionDecision" not in r["out"]["hookSpecificOutput"]


@pytest.mark.parametrize("command", QUIET_COMMANDS)
def test_any_other_command_gets_nothing(world, home, command):
    r = run("on_edit.py", bash(world, command), home)
    assert r["rc"] == 0 and r["stdout"] == "", command


def test_a_command_block_is_said_once_per_agent(world, home, tmp_path):
    t = write_transcript(tmp_path / "p" / "sess.jsonl", [PROMPT])
    first = run("on_edit.py", bash(world, "claude -p hi", t), home)
    assert first["ctx"] == load("evals")
    write_transcript(t, [PROMPT, *hook_said("PreToolUse", "Bash", first["stdout"], first["ctx"])])
    assert run("on_edit.py", bash(world, "tmux ls", t), home)["stdout"] == ""
    assert run("on_edit.py", bash(world, "tmux ls", t, agent_id="a1"), home)["ctx"] == load("evals"), "a worker is told apart"


@pytest.mark.skipif(sys.platform == "win32", reason="the fake git is a #!/bin/sh script")
def test_no_command_runs_git(world, home, tmp_path):
    """The Bash hook runs on every command its `if` lets through; none of them may cost a git call."""
    bin_dir, marker = tmp_path / "bin", tmp_path / "git-ran"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {marker}\nexit 1\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    for command in ("git status", "git merge feature", "claude -p hi", "tmux ls", "ls"):
        run("on_edit.py", bash(world, command), home, PATH=path)
        assert not marker.exists(), command


def test_every_block_has_a_moment_here():
    """Each block but the start line fires somewhere in this file's tables."""
    fired = {b for _, b in SURFACES} | {b for _, b in COMMANDS} | {"brief"}
    assert fired == set(BLOCKS) - {"session"}


# ── after_edit.py: what the check found (model), what changed (person) ───────────────────────────


def written(repo, rel: str, text: str, tool: str = "Edit", response=None, tool_input=None) -> dict:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    ti = tool_input or {"file_path": str(path), "old_string": "a", "new_string": "b"}
    return {"session_id": "edit-0000", "hook_event_name": "PostToolUse", "cwd": str(repo), "tool_name": tool,
            "tool_input": ti, "tool_response": response if response is not None else {}}


PATCH = {"filePath": "x", "structuredPatch": [{"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 3,
                                               "lines": [" # Repo", "-old line", "+new line", "+another"]}]}


def test_a_clean_edit_tells_the_person_what_changed_and_the_model_nothing(world, home):
    r = run("after_edit.py", written(world, "CLAUDE.md", "# Repo\n\nnew line\nanother\n", response=PATCH), home)
    assert r["rc"] == 0, r["stderr"][-400:]
    assert "CLAUDE.md edited (+2 −1)" in r["msg"]
    assert "hookSpecificOutput" not in r["out"], "nothing found, nothing said to the model"


def test_a_new_file_counts_its_lines(world, home):
    text = "# Repo\n\nUse uv.\n"
    p = written(world, "sub/CLAUDE.md", text, tool="Write",
                response={"type": "create", "structuredPatch": [], "originalFile": None, "content": text})
    p["tool_input"] = {"file_path": str(world / "sub/CLAUDE.md"), "content": text}
    assert "sub/CLAUDE.md edited (+3 −0)" in run("after_edit.py", p, home)["msg"]


def test_without_a_patch_the_input_is_counted(world, home):
    p = written(world, "CLAUDE.md", "# Repo\none\ntwo\n")
    p["tool_input"].update(old_string="zero", new_string="one\ntwo")
    assert "(+2 −1)" in run("after_edit.py", p, home)["msg"]
    m = written(world, "AGENTS.md", "# Repo\n", tool="MultiEdit", tool_input={
        "file_path": str(world / "AGENTS.md"), "edits": [{"old_string": "a", "new_string": "b"},
                                                          {"old_string": "c\nd", "new_string": ""}]})
    assert "(+1 −3)" in run("after_edit.py", m, home)["msg"]


def test_a_file_outside_the_project_is_named_from_home(world, home):
    mine = home / ".claude" / "CLAUDE.md"
    mine.parent.mkdir()
    mine.write_text("# Me\n", encoding="utf-8")
    p = written(world, "x/CLAUDE.md", "", tool_input={"file_path": str(mine), "old_string": "a", "new_string": "b"})
    assert "~/.claude/CLAUDE.md edited (+1 −1)" in run("after_edit.py", p, home)["msg"]


def test_over_200_lines_is_a_finding(world, home):
    r = run("after_edit.py", written(world, "CLAUDE.md", "# Repo\n" + "a line\n" * 230), home)
    assert "231 lines, over 200" in r["ctx"]
    assert r["event"] == "PostToolUse"
    assert "231 lines" not in r["msg"], "findings are the model's, the person's line says what changed"


def test_an_agents_md_a_claude_md_hides_is_a_finding(world, home):
    """Claude Code 2.1.277+ reads AGENTS.md by default only where no CLAUDE.md, .claude/CLAUDE.md or
    CLAUDE.local.md sits in the folder or in the working directory or above; there it needs an import."""
    (world / "AGENTS.md").write_text("# Shared\n", encoding="utf-8")
    r = run("after_edit.py", written(world, "CLAUDE.md", "# Repo\nClaude-only line.\n"), home)
    assert "  - AGENTS.md: Claude does not read it: CLAUDE.md takes its place" in r["ctx"]
    assert "add the line `@AGENTS.md` to CLAUDE.md" in r["ctx"]
    ok = run("after_edit.py", written(world, "CLAUDE.md", "@AGENTS.md\n\nClaude-only line.\n"), home)
    assert "hookSpecificOutput" not in ok["out"]
    sub = run("after_edit.py", written(world, "sub/AGENTS.md", "# Shared\n"), home)
    assert "  - sub/AGENTS.md: Claude does not read it: CLAUDE.md takes its place" in sub["ctx"]
    assert "give this folder a CLAUDE.md holding the line `@AGENTS.md`" in sub["ctx"]
    (world / "local").mkdir()
    (world / "local" / "CLAUDE.local.md").write_text("@./AGENTS.md\n", encoding="utf-8")
    local = run("after_edit.py", written(world, "local/AGENTS.md", "# Shared\n"), home)
    assert "hookSpecificOutput" not in local["out"], "CLAUDE.local.md imports it"
    (world / "CLAUDE.md").write_text("@AGENTS.md\n@sub/AGENTS.md\n", encoding="utf-8")
    via_root = run("after_edit.py", written(world, "sub/AGENTS.md", "# Shared\n"), home)
    assert "hookSpecificOutput" not in via_root["out"], "the root CLAUDE.md imports it"
    code = run("after_edit.py", written(world, "CLAUDE.md", "`@AGENTS.md` is how\n"), home)
    assert "AGENTS.md: Claude does not read it" in code["ctx"], "an import inside code is not an import"


def test_an_agents_md_alone_is_read_and_no_finding(tmp_path, home):
    """With no CLAUDE.md here or above, Claude reads AGENTS.md itself: nothing to fix."""
    repo = tmp_path / "plain"
    (repo / ".git").mkdir(parents=True)
    alone = run("after_edit.py", written(repo, "AGENTS.md", "# Shared\n"), home)
    assert "hookSpecificOutput" not in alone["out"]
    deeper = run("after_edit.py", written(repo, "pkg/AGENTS.md", "# Shared\n"), home)
    assert "hookSpecificOutput" not in deeper["out"]
    mine = home / ".claude" / "CLAUDE.md"
    mine.parent.mkdir(exist_ok=True)
    mine.write_text("# Me\n", encoding="utf-8")
    assert "hookSpecificOutput" not in run("after_edit.py", written(repo, "AGENTS.md", "# Shared\n"), home)["out"], \
        "~/.claude/CLAUDE.md does not keep Claude from reading AGENTS.md"


def test_the_same_finding_for_another_file_is_still_said(world, home, tmp_path):
    """Said once per file, not once per wording: a told finding about a/ must not hide b/'s."""
    t = write_transcript(tmp_path / "t" / "s.jsonl", [PROMPT])

    def after(rel, text):
        p = written(world, rel, text, response=PATCH)
        p["transcript_path"] = str(t)
        return run("after_edit.py", p, home)

    for d in ("a", "b"):
        (world / d).mkdir()
        (world / d / "AGENTS.md").write_text("# Shared\n", encoding="utf-8")
    first = after("a/CLAUDE.md", "# A\n[gone](missing.md)\n")
    assert "a/AGENTS.md: Claude does not read it" in first["ctx"] and "a/CLAUDE.md: does not resolve" in first["ctx"]
    write_transcript(t, [PROMPT, *hook_said("PostToolUse", "Edit", first["stdout"], first["ctx"])])
    second = after("b/CLAUDE.md", "# B\n[gone](missing.md)\n")
    assert "b/AGENTS.md: Claude does not read it" in second["ctx"]
    assert "b/CLAUDE.md: does not resolve: missing.md (line 2)" in second["ctx"]
    assert "a/" not in second["ctx"]


def test_a_link_or_import_that_does_not_resolve_is_a_finding(world, home):
    (world / "docs").mkdir()
    (world / "docs" / "real.md").write_text("x", encoding="utf-8")
    text = "\n".join([
        "# Repo",
        "See [the guide](docs/real.md#setup) and [the site](https://example.com) and [top](#top).",
        "Also [gone](docs/missing.md) and ![img](img/none.png).",
        "@docs/real.md",
        "@docs/nothing.md",
        "Mail me at me@example.md, and `[not a link](nowhere.md)`.",
        "```",
        "[inside a fence](nowhere/either.md)",
        "```",
    ])
    r = run("after_edit.py", written(world, "CLAUDE.md", text), home)
    assert "docs/missing.md (line 3)" in r["ctx"] and "img/none.png (line 3)" in r["ctx"]
    assert "docs/nothing.md (line 5)" in r["ctx"]
    for fine in ("docs/real.md (", "example.com", "#top", "example.md", "nowhere.md", "either.md"):
        assert fine not in r["ctx"], fine


def test_a_finding_already_told_is_not_said_again(world, home, tmp_path):
    """Seen live (2026-09-27): two edits in a row both repeated the same AGENTS.md finding."""
    (world / "AGENTS.md").write_text("# Shared\n", encoding="utf-8")
    t = write_transcript(tmp_path / "t" / "s.jsonl", [PROMPT])

    def after(text):
        p = written(world, "CLAUDE.md", text, response=PATCH)
        p["transcript_path"] = str(t)
        return run("after_edit.py", p, home)

    first = after("# Repo\n[gone](missing.md)\n")
    assert "AGENTS.md: Claude does not read it" in first["ctx"] and "missing.md (line 2)" in first["ctx"]
    write_transcript(t, [PROMPT, *hook_said("PostToolUse", "Edit", first["stdout"], first["ctx"])])
    same = after("# Repo\n[gone](missing.md)\n")
    assert "hookSpecificOutput" not in same["out"] and "CLAUDE.md edited" in same["msg"]
    more = after("# Repo\n[gone](missing.md)\n[also](other.md)\n")
    assert "other.md (line 3)" in more["ctx"], "a finding that changed is said again"
    assert "AGENTS.md: Claude does not read it" not in more["ctx"]
    write_transcript(t, [PROMPT, *hook_said("PostToolUse", "Edit", first["stdout"], first["ctx"]), COMPACT])
    assert "AGENTS.md: Claude does not read it" in after("# Repo\n")["ctx"], "a compaction dropped it: said again"


def test_a_program_gets_the_findings_and_no_message(world, home):
    r = run("after_edit.py", written(world, "CLAUDE.md", "[gone](missing.md)\n"), home, entrypoint="sdk-cli")
    assert "missing.md" in r["ctx"]
    assert "systemMessage" not in r["out"], "nobody reads a program's systemMessage"


def test_other_files_get_nothing_after(world, home):
    for rel in ("README.md", ".claude/settings.json", "plugins/demo/skills/s/SKILL.md", ".claude/rules/x.md"):
        r = run("after_edit.py", written(world, rel, "[gone](missing.md)\n"), home)
        assert r["rc"] == 0 and r["stdout"] == "", rel


def test_after_edit_writes_nothing(world, home, tmp_path):
    p = written(world, "CLAUDE.md", "# Repo\n" + "x\n" * 300 + "[gone](missing.md)\n", response=PATCH)
    before = snapshot(tmp_path)
    run("after_edit.py", p, home)
    assert snapshot(tmp_path) == before


def test_model_facing_text_never_rides_systemMessage_alone(world, home):
    """The config-check hook put the model's rule in systemMessage, which only the person reads."""
    outs = [run("on_edit.py", edit(world, rel), home)["out"] for rel, _ in SURFACES]
    outs.append(run("after_edit.py", written(world, "CLAUDE.md", "[gone](missing.md)\n", response=PATCH), home)["out"])
    for out in outs:
        msg = out.get("systemMessage") or ""
        for name in BLOCKS:
            assert load(name).splitlines()[0] not in msg
        assert "does not resolve" not in msg
        assert out.get("hookSpecificOutput", {}).get("additionalContext"), json.dumps(out)[:200]
