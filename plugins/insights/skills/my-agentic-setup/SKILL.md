---
name: my-agentic-setup
description: Show this person how they really work with Claude Code — how they run sessions and helpers, what the agent knows before it starts, how they hand work over, check it and correct it, what they built for themselves — rebuilt from the files Claude Code keeps on this machine. Writes one private page for them with two tabs: how they work (each finding with how it was counted) and a few small steps to try next, plus a brief their own agent can follow (pull requests only, never merged); only on their yes, a masked report they can choose to share with Proxify. It never shows a level or a score. Use when the person asks "what's my agentic setup", "how do I work with agents", "/my-agentic-setup", or before an agentic-engineering evaluation call.
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

## Never a level, never a score

This page shows no level and no score, and you never give one in the chat either (ruling 2026-10-26: levels are
set together with Proxify, never on the member's own machine). If they ask for their level, say it isn't something
this tool gives, and point them to what it does show: how they work and what to try next.

## 1. Build their page

Tell them first: one question about who they are in git, one scan (1–2 minutes on a heavy machine), then one page with two tabs.

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
python3 -I "${CLAUDE_SKILL_DIR}/scripts/scan.py" --days 30 --story --dev --identity "$HOME/agentic-setup/identity.json" --out "$HOME/agentic-setup/story.json" --facts-out "$HOME/agentic-setup/scan.json" --report-out "$HOME/agentic-setup/report.json"
python3 -I "${CLAUDE_SKILL_DIR}/scripts/render_all.py" "$HOME/agentic-setup/story.json" "$HOME/agentic-setup/scan.json" --brief "$HOME/agentic-setup/next-steps-brief.md" -o "$HOME/agentic-setup/my-setup.html"
python3 -c "import pathlib, webbrowser; webbrowser.open_new_tab((pathlib.Path.home() / 'agentic-setup' / 'my-setup.html').as_uri())"
```

`my-setup.html` has two tabs:
- **How you work** (opens first): one view per part of the work, each finding with "how we know" folded under it.
- **What to try next**: up to four small steps picked from what the logs don't show yet, and how to hand the
  repo ones to their own agent with `~/agentic-setup/next-steps-brief.md`. The brief makes the agent work one step
  at a time, ask first, and open a pull request it never merges.

**Do not Read `story.json`, `scan.json`, session logs, CLAUDE.md files or anything under
`~/.claude/projects/`.** They hold real project names, git emails and excerpts of their prompts. Those stay on
the page, out of this conversation. To talk about it, print only the headlines:

```
python3 -I -c "import json, os; r=json.load(open(os.path.expanduser('~/agentic-setup/story.json'))); print(r.get('coverage',{}).get('line','')); [print('-', s['title'] + ':', s['lead']) for s in r['sections']]"
```

## 2. Tell them what it says

Address them as "you". Plain words, short, friendly and professional. Say what the page shows about how they
work, then the first step on the "What to try next" tab. Prefer "not yet" and "not visible in your logs" to
negatives, and never give a level, a score or a ranking.

Say plainly that this is an early version:
- It reads this machine's records only, so work on other machines or tools won't show.
- "Not visible in your logs" means the logs held too little to say, never "you don't do it".

If something looks wrong to them, ask what they actually do. That's the feedback we need.

## 3. Share with Proxify — only on their yes

Ask with AskUserQuestion whether they want a version they could share with Proxify. It comes from the same scan,
so there is nothing to scan again. On yes:

```
python3 -I "${CLAUDE_SKILL_DIR}/scripts/render_report.py" "$HOME/agentic-setup/report.json" -o "$HOME/agentic-setup/report.html"
python3 -c "import pathlib, webbrowser; webbrowser.open_new_tab((pathlib.Path.home() / 'agentic-setup' / 'report.html').as_uri())"
```

It holds counts and plain sentences only: no project, skill, agent or MCP server names, no prompt text, and no
grading. Tell them to read `~/agentic-setup/report.html` before sharing it: it is exactly what Proxify would see.
They can delete `~/agentic-setup/` whenever they like. Nothing has been sent; they choose whether and where to upload
it. Never offer to send it for them.

## What it cannot see

Say so when it matters for this person:
- work on another machine, a client VM or a devcontainer;
- how Cursor, Copilot or other tools are used: only that they exist and when they were last touched;
- anything older than Claude Code's log retention (`cleanupPeriodDays`, 30 days unless changed);
- memory or notes kept outside what Claude Code reads (an MCP server, a notes app).
