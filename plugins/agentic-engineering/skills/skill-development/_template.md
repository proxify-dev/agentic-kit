---
name: skill-name
# What the skill does and when to use it. Claude reads this to decide when to auto-apply.
# Combined with when_to_use, capped at 1,536 chars in the skill listing — front-load triggers.
description: What this skill does and when to use it.

# Additional invocation context appended to description: trigger phrases, example requests.
# when_to_use: |
#   ...

# Autocomplete hint for arguments, e.g. "[issue-number]" or "[filename] [format]".
# argument-hint: "[arg]"

# Named positional arguments for $name substitution. Space-separated string or YAML list.
# $name / $ARGUMENTS / ${CLAUDE_*} expansion + env vars: [using them in the text](./references/arguments.md)
# arguments: arg1 arg2

# Set true to prevent Claude auto-loading this skill. Manual /name invocation only. Default: false.
# disable-model-invocation: false

# Set false to hide from the / menu — Claude-only background knowledge. Default: true.
# user-invocable: true

# Tools Claude may use without a permission prompt while this skill is active.
# Space-separated string or YAML list. Inherits session tools if omitted.
# allowed-tools: Read Bash

# Model override for this skill: sonnet, opus, haiku, a full model ID, or inherit.
# model: inherit

# Effort level while this skill is active: low, medium, high, xhigh, max.
# effort: medium

# Set to fork to run this skill in an isolated subagent context.
# context: fork

# Subagent type when context: fork — Explore, Plan, general-purpose, or a custom .claude/agents/ type.
# agent: general-purpose

# Glob patterns limiting when the skill auto-loads. Comma-separated string or YAML list.
# paths: "src/**,docs/**"

# Shell for !`command` and ```! blocks: bash or powershell. Default: bash.
# shell: bash

# Hooks scoped to this skill's lifecycle.
# hooks:
---

<!-- Replace this comment with the skill's text: what it is for, links named by the reader's task, steps with done-conditions, then reference -->
