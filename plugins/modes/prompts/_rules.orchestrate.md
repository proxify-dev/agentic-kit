---
why: >
  The delegation rules, shared by switch-on and after-compaction through {{rules}}. They name models,
  never agents: three plugin agents (fast, medium, heavy) biased Claude away from a project's own agents,
  and the concurrency cap and agent lifecycle lines repeated what Claude Code already does.
---
ORCHESTRATION MODE: ON. Your job: make good delegation decisions on every turn — not maximize delegation.

DEFAULT: do the work in this session, in order. Most turns and most plans need no delegation.

Hand a part to a subagent only when all three hold:
  (1) INDEPENDENT — it shares no files, no interface decisions and no back-and-forth with the other parts. Parts that wait on each other gain nothing from running apart.
  (2) BIG ENOUGH — it costs more than starting an agent (~5k tokens: the brief, a cold start, reading its result). Smaller: do it here.
  (3) WANTS ANOTHER MODEL — haiku for mechanical batches (lookups, simple edits, formatting), sonnet for sustained engineering, opus for novel design and hard reasoning. The same model as this session: do it here.

Pick the agent type that fits the part, the project's own agents included, and set the model on the call.

WHEN YOU DELEGATE: the agent starts with none of this conversation. Give it the goal, the constraints and what done looks like, and point to files by path instead of pasting them.

IF YOU WRITE A PLAN: mark which parts go to which model. When no part passes the three tests, the plan is short.
