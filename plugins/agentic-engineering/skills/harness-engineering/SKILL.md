---
name: harness-engineering
description: Everything around an agent except the model - the instructions it gets before it acts, the checks that block or report its mistakes, the tools it can call, how it tests its own result, what is kept across sessions, and when a piece should be removed. Use when placing a rule, wiring a hook or a setting, giving an agent a tool, when the same correction comes twice, when a rule is ignored, or when asking whether a hook, skill or rule still helps. For the wording of a text, context-engineering.
---

This page explains what a harness is and its six parts, with links for each part's tasks; then the rules that apply to every part; then how to place a rule, step by step.

## The harness

Agent = model + harness: everything that is not the model is the harness. Claude Code itself is one harness (the agent loop, the tools, compaction, permissions), and models are trained with it, so build on how it works rather than against it. What you add on top is a second layer: hooks, skills, a CLI, CLAUDE.md, settings, evals. Each piece supports one step of the agent loop: gathering context, taking an action, or checking the result.

Who loads a piece matters as much as what it says. The model loads a skill only if it decides to; the person types a slash command; Claude Code runs a hook at a fixed point (session start, before a tool call). A rule that must always apply is injected by a hook; a skill holds the detail, and a hook's text names it. Each way a text can reach the agent (a hook's injected context, a deny reason, CLAUDE.md, a skill, a command's output) is called a channel below.

## Its six parts

- Instructions: what the agent is told before it acts (the text a SessionStart hook injects, a note injected before a tool call, CLAUDE.md, a skill). An instruction fails when the right text reaches the wrong agent, or reaches the right agent at the wrong time, so place it by who reads it and when, not by how important it is.
  - Get a rule in front of an agent when it applies: § Placing a rule, below
  - [Check which agent each channel reaches, and when](references/channels.md)
  - [Decide which file or hook should hold a line, or fix one that has gone wrong](references/instruction-surfaces.md)
  - [Find out why a rule that exists is still ignored](references/audit.md)
- Checks: what blocks or reports a mistake during or after a tool call (a deny, a lint, a test, a baseline lint). Instructions alone never show whether they worked, checks alone let a mistake happen again, and a rule that must always hold but exists only as text is a check that has not been built yet.
  - [Turn a correction that came twice into a check, or block an action](references/checks.md)
  - [Write the hook that runs it](references/hooks.md), starting from [a hook template that handles the common failures](assets/hook.py)
- Tools: what the agent can call. A tool is an interface designed for the agent, and its output is also a place for instructions: it arrives exactly when the agent needs the next step.
  - [Give an agent a tool to reach a system](references/tools.md)
- Verification: how the agent tests its own result. Agents rate their own work too highly, and rereading the code is not the same as running it.
  - [Make an agent test its result before it says done](references/verification.md)
- State: what is kept across sessions, compactions and subagents. Each session starts with no memory, and anything not in the context window or the repo does not exist for the agent.
  - [Keep work going across sessions, compactions and subagents](references/state.md)
- Maintenance: measuring pieces and removing the ones that no longer help. Every piece assumes a weakness the current model has, takes up context every time it loads, and stops being needed when the model improves.
  - [Decide whether to add a piece, or whether one still helps](references/maintenance.md)

## Rules for every part

1. Fix what is missing, not the sentence: when an agent goes wrong, ask what is missing around it and how to make that visible and enforced; what to tell it comes after.
2. On the second miss, move to the next stronger mechanism: text in the right place, then a note injected before the tool call, then a check, then a design where the mistake cannot be made. Fixing the bad output fixes one result; changing what produced it fixes the next ones. Done when the next miss is caught by code, not by a person. [Choosing the mechanism and the check](references/checks.md).
3. If it was not run, it did not pass: show that the piece reached the agent, then that the agent's behaviour changed. [Seeing exactly what the agent received](references/channels.md) (§ Prove it reached the agent); [measuring the change](../evals/SKILL.md).
4. Whoever did costly work does not approve it: a separate agent checks it against criteria written before the work. [Setting up that check](references/verification.md).
5. Enforce invariants, not implementations, with the simplest piece that works; write in its commit or its eval's story what would make it unnecessary. [Removing a piece](references/maintenance.md).

## Placing a rule

1. Find out what is already active: read the `hooks` key in `~/.claude/settings.json`, `.claude/settings.json` and `.claude/settings.local.json`, the `enabledPlugins` key in those files, and each enabled plugin's `hooks/hooks.json`; the transcript's hook records show what each hook injected. With the kit CLI: `ak plugin ls · inspect <plugin> · log`. Done when you can name every hook that already injects text at this point.
2. Name who reads the rule (the main session, a fork, a subagent, the person), when it applies (the tool call or event it is about; none means it is detail for a skill), the channel that fires then and reaches that reader, and the one file it lives in.
3. How strictly the rule must hold picks the channel:
   - must always hold: a hook that refuses the tool call and names the command to type instead
   - applies to one kind of tool call: PreToolUse context on that call
   - applies to every session: one line injected by a SessionStart hook, and by a SubagentStart hook for subagents
   - how to work in a folder: that folder's CLAUDE.md
   - what to do after a command: that command's output
   - detail: a skill or reference that one of the above names
4. Use the narrowest scope that covers every case: a hook that checks the file path, a subfolder's CLAUDE.md. Write it once.
5. Prove it reached the agent: the agent's own transcript holds the text as a `hook_additional_context` record; with the kit CLI and gateway, `ak xray -o before claude -p hi`, make the change, then `ak xray --vs before claude -p hi --plugin-dir <plugin>` shows only the lines you meant to add. Done when the text reaches that agent at that point and nothing else changed.

Common mistakes: SessionStart context never reaches a subagent. PreToolUse context arrives after the tool call ran, so an action that cannot be undone needs a deny. `systemMessage` is shown to the person, not the model.

Elsewhere: [where these ideas come from, and how strong the evidence is](references/field.md) · only the wording of a text: [context-engineering](../context-engineering/SKILL.md)
