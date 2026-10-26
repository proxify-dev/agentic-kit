---
name: my-agentic-setup
description: Show this person how they really work with Claude Code — how they run sessions and helpers, what the agent knows before it starts, how they hand work over, check it and correct it, what they built for themselves — rebuilt from the files Claude Code keeps on this machine. Writes one private page for them, with tabs: their suggested level (1–4) with every check shown, how they work, and how it was worked out; only on their yes, a masked report they can choose to share with Proxify. Use when the person asks "what's my agentic setup", "how do I work with agents", "/my-agentic-setup", or before an agentic-engineering evaluation call.
allowed-tools: Bash(python3 *), Read, AskUserQuestion
---

# My agentic setup

You rebuild how this person works with coding agents from files on their own machine. You read; you never
change, install or send anything. Everything is written to `agentic-setup/` in their home folder
(`~/agentic-setup/`, on Windows `%USERPROFILE%\agentic-setup`); it stays there until they delete the folder.
Not `/tmp`: on Ubuntu, Firefox installed as a snap has its own private `/tmp` and showed "File not found" for a
page written there (2026-10-26). A non-hidden folder in home is readable by every browser on every system.

The scripts sit in `${CLAUDE_SKILL_DIR}/scripts/` (if that variable is empty: `scripts/` beside this file).
They need nothing but `python3` (stdlib only). On Windows use `python` or `py` where this says `python3`.

## 1. Build their page

Tell them first: one question about who they are in git, one scan (1–2 minutes on a heavy machine), then one page with three tabs.

**Who they are in git.** Everything they built counts as theirs only if git knows their email, and many people
commit with a different email per folder. List what their git config knows:

```
python3 -c "import os; os.makedirs(os.path.expanduser('~/agentic-setup'), exist_ok=True)"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/identity.py" --out "$HOME/agentic-setup/identity.json"
```

Ask with AskUserQuestion: "Git knows you as <the emails printed>. Are these all the emails you commit with?"
Options: "Yes, that's all of them" / "One isn't me" / "I also use another one". Write the emails they confirm to
`~/agentic-setup/identity.json` as `{"emails": [...]}`. Then run:

```
python3 -I "${CLAUDE_SKILL_DIR}/scripts/scan.py" --days 30 --story --dev --identity "$HOME/agentic-setup/identity.json" --out "$HOME/agentic-setup/story.json" --facts-out "$HOME/agentic-setup/scan.json"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/place_level.py" "$HOME/agentic-setup/scan.json" --out "$HOME/agentic-setup/level.json" > /dev/null
python3 -I "${CLAUDE_SKILL_DIR}/scripts/evidence_pack.py" "$HOME/agentic-setup/story.json" "$HOME/agentic-setup/scan.json" "$HOME/agentic-setup/level.json" -o "$HOME/agentic-setup/evidence-l3.md"
python3 -I -c "import json, os; print(json.load(open(os.path.expanduser('~/agentic-setup/level.json')))['gates']['3']['passed'])"
```

Then let AI readers judge Level 3 from the evidence pack. Call the Workflow tool with
`scriptPath: "${CLAUDE_SKILL_DIR}/workflows/guide-reader.workflow.js"` (the full path) and these args:
`{"pack": "<HOME>/agentic-setup/evidence-l3.md", "guide": "<CLAUDE_SKILL_DIR>/guide/guide-l3.md", "out": "<HOME>/agentic-setup/guide-verdict.json", "count_l3": <the true/false printed above>}`,
where `<HOME>` is the absolute home folder (`python3 -c "import pathlib; print(pathlib.Path.home())"`): `~` is not expanded there.
It runs 2 to 5 readers in their own Claude Code (a few minutes) and writes `guide-verdict.json`. It returns only the
verdict. If the Workflow tool isn't there (an older Claude Code), skip this; the page still works without the readers' card.

```
python3 -I "${CLAUDE_SKILL_DIR}/scripts/render_all.py" "$HOME/agentic-setup/story.json" "$HOME/agentic-setup/scan.json" "$HOME/agentic-setup/level.json" --verdict "$HOME/agentic-setup/guide-verdict.json" -o "$HOME/agentic-setup/my-setup.html"
python3 -c "import pathlib, webbrowser; webbrowser.open_new_tab((pathlib.Path.home() / 'agentic-setup' / 'my-setup.html').as_uri())"
```

`my-setup.html` has three tabs:
- **Your level** (opens first): the readers' Level 3 answer with its reason and evidence, then a suggested Level 1–4. Every check is shown with its bar and their value, and only what they built counts (by git history), not what they installed.
- **How you work**: one view per part of the work.
- **How it was worked out**: each finding with how it was counted.

**Do not Read `story.json`, `scan.json`, `level.json`, `evidence-l3.md`, `guide-verdict.json`, session logs, CLAUDE.md files or anything under
`~/.claude/projects/`.** They hold real project names, git emails and excerpts of their prompts. Those stay on
the page, out of this conversation. To talk about it, print only the level and the headlines:

```
python3 -I -c "import json, os; r=json.load(open(os.path.expanduser('~/agentic-setup/level.json'))); print('Suggested level:', r['suggested_level'], '-', r['seen']); [print(f'L{k}:', 'passed' if g['passed'] else 'not passed', '-', g['rule']) for k, g in sorted(r['gates'].items())]"
python3 -I -c "import json, os; r=json.load(open(os.path.expanduser('~/agentic-setup/story.json'))); print(r.get('coverage',{}).get('line','')); [print('-', s['title'] + ':', s['lead']) for s in r['sections']]"
```

## 2. Tell them what it says

Plain words, short. Lead with their suggested level and the one check that decided it, and the readers' Level 3 verdict (from the Workflow's return; the reason stays on the page). Then say what the page
says about how they work, not how it was counted.

Say plainly that this is an early version:
- The level is a suggestion, not a verdict; the interviewer decides.
- The bars were set on very few machines.
- "Can't tell" means the logs held too little to say, never "you don't do it".

If a check looks wrong to them, ask what they actually do. That's the feedback we need.

## 3. A report to share — only on their yes

Ask with AskUserQuestion whether they want a report they could share with Proxify. On yes:

```
python3 -c "import os; os.makedirs(os.path.expanduser('~/agentic-setup'), exist_ok=True)"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/scan.py" --days 30 --report --out "$HOME/agentic-setup/report.json"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/render_report.py" "$HOME/agentic-setup/report.json" -o "$HOME/agentic-setup/report.html"
python3 -c "import pathlib, webbrowser; webbrowser.open_new_tab((pathlib.Path.home() / 'agentic-setup' / 'report.html').as_uri())"
```

The report holds counts and plain sentences only: no project, skill, agent or MCP server names, no prompt text.
Tell them to read `~/agentic-setup/report.html` before sharing it: it is exactly what Proxify would see. They can delete `~/agentic-setup/` whenever they like. Nothing has been sent;
they choose whether and where to upload it. Never offer to send it for them.

## What it cannot see

Say so when it matters for this person:
- work on another machine, a client VM or a devcontainer;
- how Cursor, Copilot or other tools are used: only that they exist and when they were last touched;
- anything older than Claude Code's log retention (`cleanupPeriodDays`, 30 days unless changed);
- memory or notes kept outside what Claude Code reads (an MCP server, a notes app).
