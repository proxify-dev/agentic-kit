---
why: >
  Said at every session start, and to every subagent at SubagentStart (a subagent gets no SessionStart line): the tracer's verbs and the question each answers, so Claude reaches for them instead of reading Claude Code's transcripts by hand. The depth is in each verb's -h.
---
[tracer] Claude Code sessions from the shell: `tracer sessions live` (open sessions, now) · `tracer sessions` (this project's, oldest first) · `tracer sessions show <session>` (its turns and steps) · `tracer sessions agents <session>` (its subagents: calls, time, tokens) · `tracer sessions search <phrase>` (which sessions said it; --role user: where the user typed it) · `tracer sessions replay <session> --role user` (the user's prompts, whole) · `tracer sessions turns --tool X` (across sessions) · `tracer trace blame <file|folder>` (which sessions touched it) · `tracer trace` (every tool call; one whole by id). A session: its id, its first 8 characters, or latest. Verbatim, never summarised.
