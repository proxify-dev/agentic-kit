---
why: >
  About 15 skills on this topic had 0 real loads: each waited for its description to match, was
  slash-only, or sat in a plugin that was switched off (e9d21e27). A skill reaches an agent when a hook
  or the start line names it at its moment, so the block first asks whether it is a skill at all.
from: [0d5ed015, d8f3d6f7, c7ca4aab]
evals: [skill-gets-its-deliverer, skill-names-its-deliverer]
---
[agentic-engineering: skills] you are making a skill, an agent, a command or a plugin listing:
  first ask whether it is a skill at all: a procedure is the thing, a skill only wraps it
  it reaches an agent only when a hook or the start line names it at its moment; its real loads are Skill calls in the transcripts, not its description
  a hook-named skill stays model-invocable; every /plugin:skill a listing names exists, and a rename updates every name
How: skill skill-development.
