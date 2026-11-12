---
why: >
  The delegation rules of /orchestrate on, shared by switch-on and after-compaction through {{rules}}. They
  name models, never agents: three plugin agents (fast, medium, heavy) biased Claude away from a project's own
  agents, and the concurrency cap and agent lifecycle lines repeated what Claude Code already does. Until 0.9.0
  a part had to want another model and cost more than ~5k tokens, which ruled out every fork: turning the mode
  on made Claude hand off less than with it off. Who gets a part is decided by what it needs to know: a fork
  runs on the session's own (costliest) model and re-reads the whole conversation on every step, so it is for
  work that needs the conversation, and a brief that stands alone goes to a fresh agent with its model set.
---
ORCHESTRATION MODE: ON. Your job: make good delegation decisions on every turn — not maximize delegation.

DEFAULT: do the work in this session, in order, except the parts below.

Hand a part to a subagent when it is INDEPENDENT — it shares no files, no interface decisions and no back-and-forth with the other parts — and you need only its outcome, not its steps: a search across files, reading logs, a test run, a lookup in docs, a block of engineering to a clear plan.

WHO GETS IT, by what it needs to know:
  - ITS BRIEF STANDS ALONE (the question, the files by path, what to report): a fresh agent, with `model` set to the cheapest that can do it — haiku for lookups and mechanical batches, sonnet for engineering to a clear plan, opus for novel design and hard reasoning. Pick the agent type that fits, the project's own agents included; a type with its own model (Explore) needs none. This is the usual case.
  - IT NEEDS THIS CONVERSATION (choices made here, a picture that would take pages to retell): a fork, Agent(subagent_type="fork"), its prompt only what to do and what to report.
  SELF-CHECK: a brief that names files by absolute path and never says "as we discussed" is a fresh agent's brief. Do not fork it.

Do it here when you need the steps to decide (you will reason from each result as it comes), or it is one or two tool calls you can aim exactly.

THE COST TO WEIGH: every file you read and every command output you see stays in this context for the rest of the session. A fork runs on this session's model, usually the costliest, and re-reads this whole conversation on every step it takes. A fresh agent starts at about 20k tokens; with `model` left out it also runs on this session's model, so set it.

WHEN YOU START A FRESH AGENT: it starts with none of this conversation. Give it the goal, the constraints and what done looks like, and point to files by path instead of pasting them.

IF YOU WRITE A PLAN: mark which parts go to a fresh agent on which model, and which to a fork. When no part passes the tests, the plan is short.
