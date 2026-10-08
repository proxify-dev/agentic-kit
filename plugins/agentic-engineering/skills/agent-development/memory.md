# Agent memory

Giving an agent a directory it keeps across sessions, with the `memory` field. Elsewhere:
- [Choosing a memory format for an agent that works against an external verdict](./patterns.md)
- [Choosing any other frontmatter field](./_template.md)

Set it only when one run learns something the next run must read. Leave it out for an agent that runs once.

| `memory:` | directory | use when |
|---|---|---|
| `project` | `.claude/agent-memory/<agent-name>/` | the knowledge is about this project and is shared through version control; the usual choice |
| `local` | `.claude/agent-memory-local/<agent-name>/` | the knowledge is about this project but stays out of version control |
| `user` | `~/.claude/agent-memory/<agent-name>/` | the agent should remember across all projects |

When it is set:
- The agent's system prompt gets instructions for reading and writing the directory, plus the first 200 lines or 25KB of its `MEMORY.md`, whichever comes first, with an instruction to shorten `MEMORY.md` past that limit.
- Read, Write and Edit are added to its tools.
- `MEMORY.md` is the index of the directory: one line per file, saying what it holds, so the agent knows which file to read. Notes go in the other files.

It has no effect when auto memory is off (the `autoMemoryEnabled` setting, or `CLAUDE_CODE_DISABLE_AUTO_MEMORY`): the agent starts without the instructions or the tools. The main conversation's own auto memory never reaches a subagent other than a fork, so `memory` is how a subagent gets a memory of its own.

Say in the agent's body when to read memory and when to write it, for example: read it before starting, and save what you learned when the task is done.

The directory is named after the agent's `name`. When you rename an agent, move its directory to the new name in the same change, or the agent starts with an empty one. When you delete an agent, delete its directory too.
