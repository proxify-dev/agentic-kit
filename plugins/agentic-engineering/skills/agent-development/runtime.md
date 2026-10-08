# How an agent runs

What Claude Code does with an agent once Claude starts it: foreground or background, forks, teammates, nesting, limits, resuming, the report it returns, and running an agent as the session. Elsewhere:
- [Finding what a subagent starts with: CLAUDE.md, hook text, preloaded skills, where its transcript is](../harness-engineering/references/channels.md)
- [Writing the brief, the prompt Claude passes when it starts an agent](../context-engineering/SKILL.md)
- [Choosing a frontmatter field](./_template.md)

## Foreground or background

Fork mode is a session switch, on by default in an interactive session and off in `claude -p` and the Agent SDK. It lets Claude start forks (§ Forks) and runs every agent Claude starts in the background.

- Fork mode on: every agent runs in the background, and Claude cannot ask for the foreground. Its result reaches Claude as a notification in a later turn.
- Fork mode off: Claude runs an agent in the background by default and in the foreground when it needs the result before continuing. `background: true` in the agent file keeps it in the background anyway.
- `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` runs every agent in the foreground. `CLAUDE_CODE_FORK_SUBAGENT=1` turns fork mode on in `-p` and the SDK; `0` turns it off everywhere.
- A background agent's permission prompts appear in the person's main conversation, naming the agent. An answer that lasts beyond one call applies to the whole session.

### Tools

Two filters apply to every subagent except a fork, which keeps the main conversation's exact tools:
- Removed everywhere: AskUserQuestion, EndConversation, EnterPlanMode, ScheduleWakeup, WaitForMcpServers, Workflow; ExitPlanMode unless `permissionMode: plan`; Agent once the agent is at the nesting limit (§ Nesting).
- Removed in the background: every built-in tool except Read, Grep, Glob, LSP, Bash, PowerShell, Edit, Write, NotebookEdit, WebFetch, WebSearch, TodoWrite, Skill, ToolSearch, EnterWorktree, ExitWorktree, Monitor, TaskStop, SendMessage, Artifact and SubagentHandback. Agent and ExitPlanMode follow the first filter wherever the agent runs, and every MCP tool stays.

A tool removed this way is removed even when `tools` lists it, without an error, unless nothing in `tools` is left. So one agent file can get different tools in the foreground and in the background. A resumed agent keeps the tools of its first run.

## Forks

A fork starts with the whole conversation, the main conversation's system prompt, tools and model, and reuses its prompt cache. It needs no agent file. Claude starts one with `subagent_type: "fork"` on the Agent tool, only where fork mode is on; the person can start one with `/subtask` either way. A fork cannot start another fork. `Agent(fork)` in `permissions.deny` stops Claude from starting forks.

## Teammates

With `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in an interactive session, an agent that Claude starts from the main conversation with a `name` runs as a teammate: one of a set of agents that share a task list and work as peers. A fork, or a call that passes `isolation`, stays a subagent. There are no `TeamCreate` or `TeamDelete` tools; `claude -p` and the SDK never start teammates. After the person resumes a session, its teammates are not brought back.

A teammate started with an agent's name takes only part of its definition:
- `tools` and `model` apply.
- `disallowedTools` and `effort` apply only to a teammate running in the main terminal (in-process), not to one in its own split pane.
- The body is added to an in-process teammate's default system prompt, and replaces it for a split-pane teammate.
- `skills` never apply; `mcpServers` applies only to a split-pane teammate.

## Nesting

A subagent can start its own subagents, down to three layers below the main conversation. `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` sets the number of layers; `1` turns nesting off. To keep one agent from starting others, leave `Agent` out of its `tools` or put it in `disallowedTools`. In an interactive session a subagent waits for the background subagents it started; in `-p` and the SDK it does not, and their results go to the main conversation.

## Limits

- At most 20 subagents run at once; `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` changes it. A call over the limit fails with `Concurrent subagent limit reached`. There is no limit on the total over a session.
- An agent that reaches its `maxTurns` returns its output marked partial, and Claude can resume it (§ Resuming and messaging).

## Resuming and messaging

- Each Agent call starts a new instance. To continue one, Claude calls `SendMessage` with its agent ID or name; it resumes in the background with its full history. Explore and Plan return no agent ID and cannot be resumed.
- After Claude Code restarts, the person resumes the same session (`claude --resume` or `claude --continue`), then asks Claude to continue the agent. A subagent's transcript is deleted after `cleanupPeriodDays` (30 by default), and then it cannot be resumed.
- An agent the person stopped (`x` in `/tasks`) does not resume on a message.
- A subagent follows messages from the agent that started it as task direction. No agent's message counts as the person's approval of a permission prompt.

## The report it returns

Claude Code scans a subagent's final report before Claude reads it and puts it under a header marking it as subagent output: instructions inside a report carry no authority. Write the body so the agent returns findings, not instructions for the main conversation.

## Running an agent as the session

`claude --agent <name>`, or `"agent": "<name>"` in `.claude/settings.json`, runs the whole session as that agent: its body is the system prompt ([what that leaves out](./body.md)), and its tools and model apply. A plugin's agent is found by name; `<plugin>:<name>` picks one when two plugins share a name.

For long work that should run while the person keeps working and be picked up later, the person starts it as a background session: `claude --agent <name> --bg "<prompt>"` (not with `-p`), and reopens it from `claude agents`. Running as the session, its tools are not filtered the way a background subagent's are (§ Tools), and it can start subagents when its `tools` allow `Agent`.
