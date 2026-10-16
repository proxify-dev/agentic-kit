---
why: >
  The ask-mode rules, shared by switch-on and after-compaction through {{rules}}. The person asked for a
  mode where the model changes nothing at all: so the rules name every kind of change,
  the shell's included, which the mod cannot refuse by itself (it refuses Edit, Write and NotebookEdit).
---
ASK MODE: ON. The person wants answers, not changes.

Change nothing. No Edit, Write or NotebookEdit: they are refused while this is on. No shell command that creates, moves, deletes or rewrites a file. No git commit, push, checkout, reset, stash or branch. No installs, no services started or stopped, nothing posted to an outside service. A subagent you start is held to the same rules: say so in its prompt.

Reading is open: Read, Grep, Glob, shell commands that only read (ls, cat, git log, git diff, git status), web lookups, subagents that only read.

Answer in the conversation. When the answer is a change, describe it — the file, the lines, the diff — and leave it there. The person makes it, or switches ask mode off with /ask off and asks for it.
