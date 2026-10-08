---
why: >
  Agents treated CLAUDE.md and AGENTS.md as notes. They are prompts every session obeys, so a finding
  or work left written there becomes an order (4d58c292). A rule copied here from a plugin's text
  drifts from the plugin that says it (plugin-owns-the-rule).
from: [4d58c292, c7ca4aab, 0d5ed015]
evals: [block-delivered, agents-md, claude-md-is-a-prompt, routing, plugin-owns-the-rule, rename-everything]
---
[agentic-engineering: instruction-files] CLAUDE.md and AGENTS.md are prompts: every session working in this folder reads every line and obeys it, so a finding, work left or how-to steps written here become orders; they go to the session's memory tool.
Each line: an instruction and its reason, one fact in one place, under 200 lines.
A rule a plugin injects stays in the plugin: a copy here drifts.
With an AGENTS.md, shared lines go there and CLAUDE.md is `@AGENTS.md` plus Claude-only lines; where a CLAUDE.md sits here or above, Claude reads AGENTS.md only through that import.
The words: skill context-engineering.
