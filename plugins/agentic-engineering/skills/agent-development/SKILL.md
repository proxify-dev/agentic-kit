---
name: agent-development
description: Writing, fixing or reviewing a Claude Code agent file (.claude/agents/*.md, a plugin's agents/). Use when choosing an agent's frontmatter or writing its body, when Claude does not delegate to an agent or delegates the wrong requests, or when choosing between a subagent, a fork, a teammate and a session started with --agent.
---

Writing a Claude Code agent file: whether you need one, its frontmatter, its body, and what Claude Code does when it runs.

## What are you trying to do?

- Writing a new agent file: § Before you write one, then copy [the frontmatter template](./_template.md)
- [Choosing a frontmatter field, or finding why a field has no effect](./_template.md)
- [Writing or cutting an agent's body](./body.md)
- [Giving an agent a memory directory it keeps across sessions](./memory.md)
- [Giving an agent MCP servers](./mcp.md)
- [Choosing how it runs (foreground or background, fork, teammate, nested, resumed, as the session) or hitting a limit](./runtime.md)
- [Copying a format that worked in repeated or multi-agent work: a memory file, files to read first, one stage per agent, fixed-order steps](./patterns.md)
- Claude never runs an agent, or runs it for the wrong requests: § How Claude chooses an agent
- [Writing the brief, the prompt Claude passes when it starts an agent](../context-engineering/SKILL.md)
- [Finding what a subagent starts with: CLAUDE.md, hook text, preloaded skills](../harness-engineering/references/channels.md)
- [Running a skill in a subagent (`context: fork`) instead of writing an agent](../skill-development/references/use-cases.md)
- [Looking up a field or behaviour this skill does not cover: Claude Code's subagents documentation](https://code.claude.com/docs/en/sub-agents.md)

## Before you write one

1. **Choose the kind.** Done when one row fits; if none does, the work stays in the main conversation.

   | kind | needs an agent file | use when |
   |---|---|---|
   | subagent | yes | a task with a clear end whose output the main conversation does not need, or one that needs its own tools, model or permissions |
   | fork | no | a side task that needs the conversation so far ([forks](./runtime.md)) |
   | teammate | no (it can reuse one) | agents that share a task list and work as peers ([teammates](./runtime.md)) |
   | the session itself | yes | a session that runs with that role, tools and model from its first turn, in the terminal or in the background ([running an agent as the session](./runtime.md)) |
   | skill with `context: fork` | no | the steps are the reusable part and any agent could run them; it starts without the conversation ([skill-development](../skill-development/SKILL.md)) |

2. **Decide on memory.** Done when you chose `user`, `project`, `local` or none: [deciding whether it needs memory, and which scope](./memory.md).

3. **List its tools.** Give the fewest tools that do the job ([`tools` and `disallowedTools`](./_template.md)). A command-line tool reaches an agent in one of these ways:
   - a plugin's `bin/` folder: its files are on the Bash tool's `PATH` while the plugin is enabled, so an agent with Bash runs them by name. claude.ai and Cowork do not install a plugin that has a `bin/` folder.
   - a script inside the plugin: write `${CLAUDE_PLUGIN_ROOT}/scripts/<name>` in the agent's body. Claude Code replaces the variable with the path when it loads the body; the variable is not set in the Bash tool's environment.
   - an MCP server: [giving an agent MCP servers](./mcp.md).

   A tool written in code with the Agent SDK (`@tool`, served by `create_sdk_mcp_server`) reaches only an agent that your program starts with `query()`; an agent file cannot declare it. Done when every tool the body tells the agent to use is in its tool list.

4. **Check that it loads.** Done when all of these hold:
   - `claude plugin validate .claude/agents` (or the plugin's folder) reports nothing. It finds YAML that does not parse, not a missing `name`.
   - The file starts with `---` on its first line and has a `description`, and its `name` follows [the `name` rules](./_template.md). Otherwise Claude Code skips a file in `.claude/agents/` or `~/.claude/agents/` without telling the session; `claude --debug` logs most of these. A plugin's agent file with no `name` or YAML that does not parse still loads, under its filename.
   - The session has the current file. Claude Code reloads `.claude/agents/` and `~/.claude/agents/` within seconds of an edit, except the first file in a folder that did not exist when the session started, which needs a new session. A plugin's agents reload only when the person runs `/reload-plugins` or starts a new session. There is no `/agents` wizard: write the file.
   - Claude runs it for a request it should take, in 3 of 3 runs of `claude -p "<request>" --output-format stream-json --verbose` (add `--plugin-dir <plugin>` for a plugin's agent): the output has a `tool_use` named `Agent` whose `subagent_type` is the agent's name (`<plugin>:<name>` for a plugin's). For more runs or a script: [the evals skill](../evals/SKILL.md).

## How Claude chooses an agent

If Claude never runs the agent, first check that it loads (§ Before you write one, step 4).

Claude decides to delegate from the request, each agent's `description` and the current context. The body becomes the agent's system prompt only after Claude has chosen it, so when Claude runs an agent for the wrong requests, or never for the right ones, change the `description`, not the body.

- Put the requests it should take first, in the words a person would use. "Use proactively" in the description encourages Claude to delegate without being asked.
- Keep it short: Claude Code warns at startup when the descriptions of your agents together pass 15,000 tokens.
- A person can skip Claude's choice: `@agent-<name>` (a plugin's agent: `@agent-<plugin>:<name>`) runs that agent for one task, and `claude --agent <name>` runs the whole session as it.
- For an agent in a plugin, `claude plugin eval` measures how often Claude delegates to it over a set of prompts: [the evals skill](../evals/SKILL.md).
