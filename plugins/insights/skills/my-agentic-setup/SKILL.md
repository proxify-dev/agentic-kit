---
name: my-agentic-setup
description: Show this person how they really work with Claude Code — how they run sessions and helpers, what the agent knows before it starts, how they hand work over, check it and correct it, what they built for themselves — rebuilt from the files Claude Code keeps on this machine. Writes a private page for them, and a page suggesting their level (1–4) with every check shown; only on their yes, a masked report they can choose to share with Proxify. Use when the person asks "what's my agentic setup", "how do I work with agents", "/my-agentic-setup", or before an agentic-engineering evaluation call.
allowed-tools: Bash(python3 *), Read, AskUserQuestion
---

# My agentic setup

You rebuild how this person works with coding agents from files on their own machine. You read; you never
change, install or send anything. Everything is written to `/tmp/agentic-setup/` on their machine (on Windows,
`%TEMP%\agentic-setup`): temporary files, gone at the next restart.

The scripts sit in `${CLAUDE_SKILL_DIR}/scripts/` (if that variable is empty: `scripts/` beside this file).
They need nothing but `python3` (stdlib only). On Windows use `python` or `py` where this says `python3`.

## 1. Build their page

```
python3 -c "import os; os.makedirs('/tmp/agentic-setup', exist_ok=True)"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/scan.py" --days 30 --story --dev --out "/tmp/agentic-setup/story.json"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/render_story.py" "/tmp/agentic-setup/story.json" -o "/tmp/agentic-setup/my-setup.html"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/render_explorer.py" "/tmp/agentic-setup/story.json" -o "/tmp/agentic-setup/explorer.html"
python3 -m webbrowser -t "file:///tmp/agentic-setup/my-setup.html"
```

The scan takes 1–2 minutes on a heavy machine.

**Do not Read `story.json`, session logs, CLAUDE.md files or anything under `~/.claude/projects/`.** The page holds
real project names and excerpts of their prompts; they stay on the page, out of this conversation. To talk about
it, print only the headlines:

```
python3 -I -c "import json,os; r=json.load(open('/tmp/agentic-setup/story.json')); print(r.get('coverage',{}).get('line','')); [print('-', s['title'] + ':', s['lead']) for s in r['sections']]"
```

## 2. Tell them what it says

Plain words, short. Lead with what the page says about how they work, not how it was counted. Then point at
two pages:
- `my-setup.html`: their page, one view per part of the work.
- `explorer.html`: the same findings, each with how it was worked out (open it to check a number they doubt).

Say plainly that this is an early version: the numbers were tuned on few machines, and a view that says
"can't tell" means the logs held too little to say, never "you don't do it".

## 3. Their suggested level

A second page puts them on Level 1–4 from the same files. Every check, its bar and its value are on the
page, and it counts only what they built (git history), not what they installed:

```
python3 -I "${CLAUDE_SKILL_DIR}/scripts/scan.py" --days 30 --out "/tmp/agentic-setup/scan.json"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/place_level.py" "/tmp/agentic-setup/scan.json" --out "/tmp/agentic-setup/level.json" --md "/tmp/agentic-setup/level.md" > /dev/null
python3 -I "${CLAUDE_SKILL_DIR}/scripts/render_place.py" "/tmp/agentic-setup/scan.json" "/tmp/agentic-setup/level.json" -o "/tmp/agentic-setup/level.html"
python3 -m webbrowser -t "file:///tmp/agentic-setup/level.html"
```

The scan runs again (1–2 minutes): this one counts facts, the first one tells the story. Don't Read `level.json`
or `scan.json` (they hold project paths and git emails). To talk about it, print only the level and the gates:

```
python3 -I -c "import json; r=json.load(open('/tmp/agentic-setup/level.json')); print('Suggested level:', r['suggested_level'], '-', r['seen']); [print(f'L{k}:', 'passed' if g['passed'] else 'not passed', '-', g['rule']) for k, g in sorted(r['gates'].items())]"
```

Say it the way the page does: a suggestion, not a verdict. The bars were set on very few machines, and the
interviewer decides. If a check looks wrong to them, ask what they actually do. That's the feedback we need.

## 4. A report to share — only on their yes

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
