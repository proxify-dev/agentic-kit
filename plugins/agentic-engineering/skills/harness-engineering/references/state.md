# State: what is kept across sessions, compactions and subagents

Keeping work going across sessions, compactions and subagents: what an agent must find in a file. Elsewhere:
- [Writing a subagent's brief](../../context-engineering/SKILL.md)
- [What a subagent starts with](channels.md)
- Back to [placing a rule](../SKILL.md)

## The rule

"anything it can't access in-context while running effectively doesn't exist" (OpenAI). A decision made in a chat, a thread or a person's head is gone at the next session. The repo is the record: code, markdown, schemas and plans, under version control.

## What goes in a file

- The plan, the progress, and each decision with its reason, written before the context window is summarised.
- Keep the reference, drop the content: a path or a URL stays in the context window, the content goes to disk and is read again when needed (Manus).
- Keep failures visible: an error left in the transcript stops the agent repeating it (Manus).
- A list of goals rewritten as the work goes keeps them near the end of the context window, where the model pays most attention (Manus).
- Findings go to memory, instructions to CLAUDE.md: every line in CLAUDE.md is read as an instruction ([what CLAUDE.md should hold](instruction-surfaces.md)).

## Work across many sessions

From Anthropic's setup for long-running work:
1. The first session only sets up: a script that starts the environment, a list of the work with a passes flag per item, a progress file, a first commit.
2. Every later session starts the same way: the working folder, `git log --oneline -20`, the progress file, the list; then it starts the app and runs a smoke test before any new work.
3. One item per session.
4. It ends with a clean state: code ready to merge, a commit that says what changed, the progress file updated.
Without this, one agent tried to do everything at once and another said it had finished when it had not.

## When the context window fills

- Compaction continues from a summary; a new session started from a written handoff starts clean. Use the handoff when the session has gone off course, or when the model starts wrapping up early as the context window fills (Anthropic saw this on Sonnet 4.5, far less on Opus 4.5).
- Compact on purpose at natural points: write the goal, the approach, what is done and the current failure to a file, then start a new session. HumanLayer built a 35,000-line feature in 7 hours this way.
- A session that went off track: restart it with better instructions rather than correcting it step by step (HumanLayer).

## Memory that loads at session start

A small index loaded at every session start, under a hard size cap, and a larger store searched when needed (agent-afk keeps about 1,500 tokens always loaded and searches the rest). Each index line links to a note; the store holds the full text.

## Subagents

A subagent writes its output to a file and returns the path and a short answer, so the parent's context window stays small; the parent saves its plan before delegating, since its own context may be compacted (Anthropic).
