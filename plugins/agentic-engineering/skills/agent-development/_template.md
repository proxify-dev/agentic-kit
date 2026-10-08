---
# Only name and description are required. Keys are camelCase and must match exactly: Claude Code ignores
# an unknown key without an error. A version below is the first Claude Code that reads that field.

# At most 256 characters; no ":" and not starting with "-". Hooks receive it as agent_type.
# The filename doesn't have to match. In a plugin, agents/review/x.md registers as <plugin>:review:x.
name: agent-name

# The requests Claude should delegate to this agent, first: [how Claude chooses an agent](./SKILL.md)
description: When Claude should delegate to this agent.

# sonnet, opus, haiku, fable, a full model ID (e.g. claude-opus-5-5), or inherit.
# Order: a model Claude passes for one call, then this field, then CLAUDE_CODE_SUBAGENT_MODEL, then the
# main conversation's model. An alias of the main conversation's family (opus while it runs Opus) gives its
# exact model, [1m] included. CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1 ignores this field.
# model: inherit

# Tools the agent can use: a YAML list or "Read, Grep, Bash". Omitted: every tool available to subagents.
# Running in the background removes some built-in tools: [which ones](./runtime.md)
# A list where no entry resolves to a tool fails to launch.
# Agent(worker, researcher) limits which agents it can start only when it runs as the session (--agent).
# tools:
#   - Read
#   - Bash

# Tools removed from the inherited or listed set, applied before tools. mcp__<server> (or mcp__<server>__*)
# grants or removes one server's tools in either field; mcp__* here removes every MCP tool.
# An entry like Bash(git push *) removes all of Bash: block one command with permissions.deny instead.
# disallowedTools:

# default (or manual), acceptEdits, auto, dontAsk, bypassPermissions, or plan.
# Omitted: the main conversation's mode. Ignored while the main conversation is in bypassPermissions,
# acceptEdits or auto: the agent runs in that mode. bypassPermissions takes effect only when the main
# conversation is already in it. Ignored for plugin agents: for a read-only plugin agent leave Edit and Write
# out of tools; for a permission mode, copy the file into .claude/agents/ or ~/.claude/agents/.
# permissionMode: default

# Turns before the agent stops: [what happens at the limit](./runtime.md)
# maxTurns: 10

# Skills loaded whole into the agent's context when it starts. It can still load other skills with the
# Skill tool; to block that, leave Skill out of tools. A disable-model-invocation skill can't be preloaded.
# skills:
#   - skill-name

# MCP servers: the name of a server the session already has, or an inline definition: [both forms](./mcp.md)
# Ignored for plugin agents.
# mcpServers:
#   - server-name

# Hooks that run only while this agent runs, as a subagent or as the session. Every hook event works; as a
# subagent, Stop runs as SubagentStop. In a project's .claude/agents/ they run only once the person has
# trusted the folder. Ignored for plugin agents: a plugin ships its hooks in hooks/hooks.json.
# hooks:

# A memory directory kept across sessions: user, project, or local: [choosing the scope](./memory.md)
# memory: project

# true keeps it in the background even when Claude asks for the foreground: [when this matters](./runtime.md)
# background: false

# true starts it without the user, project and local CLAUDE.md files; managed policy files still load.
# For an agent whose brief (the prompt Claude starts it with) carries everything it needs.
# Ignored when it runs as the session (--agent). Claude Code v2.1.271.
# omitClaudeMd: false

# low, medium, high, xhigh, or max; the levels depend on the model. Overrides the session's effort level,
# not the CLAUDE_CODE_EFFORT_LEVEL environment variable.
# effort: medium

# worktree runs it in a temporary git worktree, branched from the default branch, not the main conversation's
# HEAD. The worktree is removed when the agent changes nothing.
# isolation: worktree

# Its color in the task list and transcript: red, blue, green, yellow, purple, orange, pink, or cyan.
# color: blue

# The first user turn when this agent runs as the session (--agent or the agent setting). Commands and
# skills in it are processed; it goes before any prompt the person gives. Ignored for plugin agents.
# initialPrompt: |
#   ...

# cacheTtl: 5m or 1h sets the prompt cache lifetime of this agent's requests. Read only from agent files,
# not from --agents JSON. Claude Code v2.1.248.
# experimental:
#   cacheTtl: 1h
---

<!-- Replace this comment with the agent's system prompt: [writing the body](./body.md) -->
