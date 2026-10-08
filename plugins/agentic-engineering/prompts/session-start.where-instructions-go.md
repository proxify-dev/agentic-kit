---
why: >
  The only text this plugin puts in every session and subagent, so it holds what must be known
  before the first edit. The contradiction rule sits here, not in the CLAUDE.md block, which arrives
  only once a CLAUDE.md is being edited: too late for a code change that makes a line wrong
  (contradiction: 1.00 with the rule at every start, 0.22 without). Said as an instruction: a passive
  rewording scored 0/3 against 3/3 (74d00a00). The README sentence: with only CLAUDE.md/AGENTS.md and
  memory named, an agent put the kit's no-MCP principle in plugins/AGENTS.md; the person wanted the
  README, where people read why (5b17294d).
from: [c7ca4aab, 0d5ed015, 5b17294d]
evals: [contradiction, routing, claude-md-is-a-prompt, plugin-owns-the-rule, where-it-lands, harness-skill-fires, readme-holds-the-why]
---
[agentic-engineering] CLAUDE.md and AGENTS.md hold their folder's rules: when your change makes a line there wrong, propose fixing that line in the same turn using AskUserQuestion. A subfolder's rule goes in a CLAUDE.md there, loaded only there. Why the project is built this way: its README. What happened goes to memory. See Skill harness-engineering to learn more
