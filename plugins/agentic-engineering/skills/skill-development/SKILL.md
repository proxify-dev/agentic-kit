---
name: skill-development
description: Procedural guidance on creating a skill.
---

Deciding whether something should be a skill, then writing, placing and testing one.

## What are you trying to do?

- Deciding whether it should be a skill at all: § 1, below
- [Choosing the frontmatter for what the skill must do: take input, be loaded by the person or by Claude, run in a subagent, use tools, inject live data](./references/use-cases.md)
- [Using arguments and environment variables in a skill's text](./references/arguments.md)
- Deciding where the skill's folder goes: § Placement
- Testing that it loads and changes what the agent does: § Testing and evals
- Only the wording of its text: [the context-engineering skill](../context-engineering/SKILL.md)
- Writing an agent file (`.claude/agents/*.md`, a plugin's `agents/`), not a skill: [the agent-development skill](../agent-development/SKILL.md)

## 1. Should this be a skill?

| | when | instead |
|---|---|---|
| YES | the contents are portable, distributable, and transferable | |
| YES | the same instructions, checklist or procedure keep getting pasted into chat | |
| YES | part of CLAUDE.md has grown into a procedure, or is reference needed only sometimes (API docs, a schema, a style guide) | |
| YES | Claude should decide how to apply the steps, or it teaches how to use a tool or service well | |
| YES | Claude, run without it on real tasks, fails in a way you can name | |
| NO | a rule that must steer at a moment (every edit under `src/api/`, every merge) | AGENTS.md / PreToolUse hook. No skill; tell the person why |
| NO | Claude should always know it: build commands, conventions, "never do X" | CLAUDE.md / AGENTS.md |
| NO | a guideline for some files, not a procedure | `.claude/rules/<name>.md` with `paths:` |
| NO | Claude needs to reach a system it can't see | a CLI whose output names the next move (an MCP server only for a reader with no shell); a skill can then teach its use |
| NO | a one-off, or a one-step task Claude already handles: it won't even consult the skill | nothing |

If NO, please communicate it to the user and decide together what to do.

## Placement
Place it in the plugin or repo whose ecosystem it serves (plugins/<plugin>/skills/<name>/, .claude/skills/<name>/); ~/.claude/skills/ only for a tool that is yours in every project. Done when it sits beside what it serves.
<!-- TODO: Not sure if we need this -->

## Writing it

Copy [`_template.md`](./_template.md): its frontmatter lists every key, and its body is the order a SKILL.md follows (what it is for, links named by the reader's task, steps with done-conditions, then reference).

For what the skill can or must do (take input, be loaded by the person or by Claude, run in a subagent, use tools, inject live data, stay portable): [the frontmatter to write for each case](./references/use-cases.md).

Before you report it done: [context-engineering § Before a text is done](../context-engineering/SKILL.md).

## Arguments

A skill receives harness-injected environment variables [enumerated here](./references/arguments.md). 

## Testing and evals

When the skill must fire reliably, or changes behaviour that matters, ask the user whether they want evals.
You can run evals either quickly in this session, or from a script. For how: load the evals skill (`agentic-engineering:evals`) with the Skill tool.

[Claude Code's skills documentation, for every frontmatter key and how skills load](https://code.claude.com/docs/en/skills)
