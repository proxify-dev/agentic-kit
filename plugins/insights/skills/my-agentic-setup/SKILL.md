---
name: my-agentic-setup
description: Show this person how they really work with Claude Code — how they run sessions and helpers, what the agent knows before it starts, how they hand work over, check it and correct it, what they built for themselves — rebuilt from the files Claude Code keeps on this machine. Writes one private page for them, with tabs: their suggested level (1–4) with every check shown, how they work, and how it was worked out; only on their yes, a masked report they can choose to share with Proxify. Use when the person asks "what's my agentic setup", "how do I work with agents", "/my-agentic-setup", or before an agentic-engineering evaluation call.
allowed-tools: Bash(python3 *), Read, AskUserQuestion
---

# My agentic setup

You rebuild how this person works with coding agents from files on their own machine. You read; you never
change, install or send anything. Everything is written to `/tmp/agentic-setup/` on their machine (on Windows,
`%TEMP%\agentic-setup`): temporary files, gone at the next restart.

The scripts sit in `${CLAUDE_SKILL_DIR}/scripts/` (if that variable is empty: `scripts/` beside this file).
They need nothing but `python3` (stdlib only). On Windows use `python` or `py` where this says `python3`.

## 1. Build their page

Tell them first: one scan (1–2 minutes on a heavy machine), then one page with three tabs. Then run:

```
python3 -c "import os; os.makedirs('/tmp/agentic-setup', exist_ok=True)"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/scan.py" --days 30 --story --dev --out "/tmp/agentic-setup/story.json" --facts-out "/tmp/agentic-setup/scan.json"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/place_level.py" "/tmp/agentic-setup/scan.json" --out "/tmp/agentic-setup/level.json" > /dev/null
python3 -I "${CLAUDE_SKILL_DIR}/scripts/render_all.py" "/tmp/agentic-setup/story.json" "/tmp/agentic-setup/scan.json" "/tmp/agentic-setup/level.json" -o "/tmp/agentic-setup/my-setup.html"
python3 -m webbrowser -t "file:///tmp/agentic-setup/my-setup.html"
```

`my-setup.html` has three tabs:
- **Your level** (opens first): a suggested Level 1–4. Every check is shown with its bar and their value, and only what they built counts (by git history), not what they installed.
- **How you work**: one view per part of the work.
- **How it was worked out**: each finding with how it was counted.

**Do not Read `story.json`, `scan.json`, `level.json`, session logs, CLAUDE.md files or anything under
`~/.claude/projects/`.** They hold real project names, git emails and excerpts of their prompts. Those stay on
the page, out of this conversation. To talk about it, print only the level and the headlines:

```
python3 -I -c "import json; r=json.load(open('/tmp/agentic-setup/level.json')); print('Suggested level:', r['suggested_level'], '-', r['seen']); [print(f'L{k}:', 'passed' if g['passed'] else 'not passed', '-', g['rule']) for k, g in sorted(r['gates'].items())]"
python3 -I -c "import json; r=json.load(open('/tmp/agentic-setup/story.json')); print(r.get('coverage',{}).get('line','')); [print('-', s['title'] + ':', s['lead']) for s in r['sections']]"
```

## 2. Tell them what it says

Plain words, short. Lead with their suggested level and the one check that decided it. Then say what the page
says about how they work, not how it was counted.

Say plainly that this is an early version:
- The level is a suggestion, not a verdict; the interviewer decides.
- The bars were set on very few machines.
- "Can't tell" means the logs held too little to say, never "you don't do it".

If a check looks wrong to them, ask what they actually do. That's the feedback we need.

## 3. A report to share — only on their yes

Ask with AskUserQuestion whether they want a report they could share with Proxify. On yes:

```
python3 -c "import os; os.makedirs('/tmp/agentic-setup', exist_ok=True)"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/scan.py" --days 30 --report --out "/tmp/agentic-setup/report.json"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/render_report.py" "/tmp/agentic-setup/report.json" -o "/tmp/agentic-setup/report.html"
python3 -m webbrowser -t "file:///tmp/agentic-setup/report.html"
```

The report holds counts and plain sentences only: no project, skill, agent or MCP server names, no prompt text.
Tell them to read `report.html` before sharing it, and to copy it somewhere else if they want to keep it: it is exactly what Proxify would see. Nothing has been sent;
they choose whether and where to upload it. Never offer to send it for them.

## What it cannot see

Say so when it matters for this person:
- work on another machine, a client VM or a devcontainer;
- how Cursor, Copilot or other tools are used: only that they exist and when they were last touched;
- anything older than Claude Code's log retention (`cleanupPeriodDays`, 30 days unless changed);
- memory or notes kept outside what Claude Code reads (an MCP server, a notes app).
