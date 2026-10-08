# Channels: which agent each one reaches, when, and where its text appears in the request

Which channel carries a rule to which agent, and how to confirm the agent received it. Elsewhere:
- [Choosing which file or hook should hold the line](instruction-surfaces.md)
- [Writing the hook that injects it](hooks.md)
- [Giving the agent a tool to call instead](tools.md)
- Back to [placing a rule](../SKILL.md)

## Who each channel reaches

| channel | reaches | when | holds |
|---|---|---|---|
| SessionStart context | the main session and every fork; never a subagent (other than a fork) or a workflow agent | startup, resume, clear, compact, fork | a short list: each tool and the command to start with |
| SubagentStart context | that subagent or workflow agent | when it starts, before its first prompt | the same list, for subagents |
| PreToolUse context | the agent making the call, subagents included | the hook runs before the call; its text appears next to the tool result | the rule for this file or command |
| PreToolUse deny | the agent making the call: the call does not run, and the agent reads the reason | before the call | a rule that must always hold, and the command to type instead |
| PostToolUse context or block | the agent that made the call, next to the result | after the call | what the result means: a lint's findings, the next step |
| Stop block | the agent, as its reason to keep working | when it tries to end the turn | what is still missing before it may stop |
| CLI output | whoever ran the command | when the command runs | the next command |
| CLAUDE.md, AGENTS.md | every session in the folder at launch, a subfolder's when a file there is read; subagents, except Explore, Plan and an agent with `omitClaudeMd` | launch | how to work in this folder |
| skill, reference | whoever loads it | when a channel names it | detail for one task |
| `--append-system-prompt` | `system`, kept on resume until compaction and in a fork; never a subagent (other than a fork) or a workflow agent; no install puts it back | launch | one launch's role |

PreToolUse context arrives after the call ran: an action that cannot be undone needs a deny.

## Prove it reached the agent

Look at exactly what the agent received before trusting that a rule reached it.
- A hook's text is in the agent's own transcript (`transcript_path`, JSONL under `~/.claude/projects/`) as a `hook_additional_context` record; a subagent's transcript is its own file under `<session>/subagents/`. A deny appears as the call's error result instead.
- A skill reached the agent when a `Skill` tool call names it in the transcript, not when it appears in the skill listing.
- The whole request: with the kit CLI and gateway, `ak xray -o before claude -p hi`, make the change, then `ak xray --vs before claude -p hi --plugin-dir <plugin>`: only the lines you meant to add show. Drop `-p` to see a person's session.
- Then show it changes behaviour: context by a run with it against a run without it, a deny by a unit test and a retry ([the evals skill](../../evals/SKILL.md)).

## Who loads it

The model (a skill: it may never decide to load it), the person (a slash command: nothing automatic), or Claude Code at a fixed point (a hook, a SessionStart line, a command's output) (Fowler). A rule that must always apply is injected by a hook; a skill holds the detail, and a hook's text names it.

## Where text sits in the request

Seen in the API requests, Claude Code 2.1.283; measure again on a new version.

- `system` is 3 blocks: a billing header (uncached), the identity line, an instruction body of about 4.3-4.6 KB holding the core instructions and the text of launch flags (`--append-system-prompt`). It is built on the first request and kept until compaction, so text added during a session goes into `messages[]`.
- The identity line depends on the entrypoint: "You are Claude Code, Anthropic's official CLI for Claude." in an interactive session; `claude -p` adds ", running within the Claude Agent SDK."
- Everything else is in `messages[]`: CLAUDE.md, output style, auto memory, the git snapshot, the cwd and platform block, hook context, MCP instructions, skill bodies, tool results.
- CLAUDE.md and `.claude/rules/*` are inlined whole into the first user message as one `<system-reminder>` block headed "Codebase and user instructions are shown below…", with one "Contents of <path> (<kind>):" section per file. The model is told this context "may or may not be relevant" (HumanLayer, through a logging proxy): one more reason to inject a rule about one kind of tool call at that call.
- Hook context, on claude-opus-5-5, is its own system-role message starting "<Event>[:<Tool>] hook additional context:".
- A `<system-reminder>` typed into a prompt or a file reaches the model word for word, next to the real ones.

## How each channel loads

From the Claude Code docs.

- CLAUDE.md and rules files: loaded whole, up front; the model decides nothing. A rule without `paths:` loads at launch. A rule with `paths:` loads when Claude reads a matching file, not on every tool use or because of what a turn says, and again after compaction as matching files are read (not yet checked in the API requests). The InstructionsLoaded hook fires as each one loads, at start and later as files are read.
- CLAUDE.md and auto memory are context, not enforced configuration: to block an action whatever Claude decides, use a PreToolUse hook.
- A skill: its name and description are in the skill listing; the model decides whether to invoke it.
- Auto memory: the MEMORY.md index lines load; the model decides whether to Read a topic file.
- Output style: instructions for the whole session, not enforced; what must happen every time is a hook. A switch applies from the next message (since v2.1.251).
- UserPromptSubmit context appears next to the prompt, starting with the hook's name. Example: a note injected when the person asks for a draft took its eval case from 0/3 to 3/3.
- SubagentStart: the matcher is the agent type (`general-purpose`, `Explore`, `Plan`, a custom agent's name, `^my-plugin:reviewer$`). It fires again when a subagent resumes; the context is added only if the subagent does not have it yet.
- MCP server instructions are cut at 2,048 characters by default.
- A file the agent reads, any tool result: data. A rule placed there is information, and nothing makes the agent follow it.

## Placing by who it reaches

- A rule about an action goes on that action's tool event, which reaches whichever agent makes the call.
- A plugin's behaviour is injected by its own hooks. `--append-system-prompt` belongs to whoever launches Claude; for a plugin it is a hack.
- Each plugin's SessionStart hook injects one line of at most 40 tokens naming its own commands, never a list of the other plugins: installed together the lines form the full list, and alone each one is still correct.
- At the start, a short description of the environment (the folder, the tools, the commands to start with) saves the agent from searching (LangChain does this when its agent starts); list the commands, not the folder's contents.

## Subagents

What a subagent's brief (the prompt it is started with) should hold: [the context-engineering skill](../../context-engineering/SKILL.md).

- A subagent (any subagent other than a fork, and a workflow agent) starts with CLAUDE.md (not Explore or Plan, not an agent with `omitClaudeMd`), its agent file's body and the skills its `skills:` field preloads, its brief, and SubagentStart context. It never gets the conversation that started it, SessionStart context or `--append-system-prompt`.
- PreToolUse and PostToolUse fire inside subagents too, with `agent_id` and `agent_type` in the input; a subagent's transcript is its own file ([injecting a hook's text once per agent](hooks.md)).
- A rule every subagent needs goes on SubagentStart, or on the PreToolUse of the Agent, Task or Workflow call that starts it, so the text appears while the brief is being written. One harness project, completely, puts its quality rules into each subagent's start prompt rather than into a document.
- One agent by default. Delegate work that is independent and has a clear end; a subagent keeps the output of a long search out of the parent's context window and returns the answer, not the files. Subagents given roles ("the frontend agent") did not work (HumanLayer). Many may search; one builds and runs the tests (Huntley).
- More agents mostly means more tokens: token use explained 80% of the variance in Anthropic's multi-agent research system, at about 15 times a chat's cost, and it suits research that spreads wide, not coding where the parts depend on each other (Anthropic).
- Seen here: 8 full `claude` processes at once put a laptop at load 48; at most 4.
