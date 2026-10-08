---
why: >
  A worker copies the style it is handed: a brief that pasted the old prompt-file template is how the
  wrapper spread to every block (4f9f082e). A worker starts cold, with CLAUDE.md and its brief only,
  so the brief carries the task, its done-when, the rulings and the files it owns.
from: [0d5ed015]
evals: [brief-copies-its-template]
---
[agentic-engineering: brief] a worker starts cold: it has CLAUDE.md and its brief, not this conversation, and it copies the style it is handed. Give the task and its done-when, the rulings it must honour, the files it owns, and pointers, not pasted text; a template only for the shape of its output. Send each brief once it holds this.
How: skill context-engineering.
