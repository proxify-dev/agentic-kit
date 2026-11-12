# modes: what changed

To update, in a terminal (not inside a Claude Code chat), run these two commands, then restart Claude Code:

```
claude plugin marketplace update agentic-kit
claude plugin update modes@agentic-kit
```

You get a new version only when it is released: each entry below is one release, newest first. Version 0.8.0 was
never sent out; its changes are not in this plugin.

## 0.9.0 — 2026-11-12

### What changes for you

- **`/orchestrate lead`** is a second level of the mode. The main session plans and decides, keeps the briefs and the
  talk with you, and sends searches, reading, tests and planned edits to agents. `/orchestrate on` keeps Claude
  doing the work itself and handing off the parts that stand alone. The footer reads `orchestrating · lead`.
  A resumed session keeps its level, and a compaction restates the lead rules.
- **Who gets a part is decided by what it needs to know.** A part whose brief stands alone goes to a fresh agent with its
  model set (haiku to find and read, sonnet to trace or change code, opus to weigh and judge). A part that needs the
  conversation goes to a fork. Before 0.9.0 every part had to want another model, which ruled out forks, so
  turning the mode on made Claude hand off less than leaving it off.
- **Every `/orchestrate` and `/ask` answer explains itself:** what Claude does now, what you will see, and how to
  switch to the other states. `/orchestrate status` lists every state with the one in force marked, and a wrong
  argument lists them all.

### Known limits

- Nothing refuses an edit in the main session at any level of `/orchestrate`: the rules alone hold it. `/ask` is the
  mode that refuses edits.
