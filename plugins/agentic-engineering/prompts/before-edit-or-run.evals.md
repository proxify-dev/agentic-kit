---
why: >
  Agents proved a fix the person sees with unit tests alone, or made tmux the rule for every check.
  The question picks the proof: a real screen for what the person sees, claude -p with and without
  the change for what the agent does. A grader the without-arm also passes proves nothing.
from: [b45aef32, 09c0a00e]
evals: [proof-sees-the-screen]
---
[agentic-engineering: evals] you are writing or running an eval or a test. Name the question first: what the person sees is proven on a real screen (claude in tmux, capture-pane); what the agent does, by claude -p with and without the change. An eval case is born from the session that went wrong (its id, the person's words, what it guards) and runs red on the release first; a grader the without-arm also passes proves nothing. Claude from a script: env -u CLAUDECODE CLAUDE_CODE_SESSION_ID … (VAR= still counts as set), at most 4 at once; --bare skips every hook.
Which eval answers which question: skill evals.
