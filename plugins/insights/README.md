# insights

How you really work with Claude Code, rebuilt from the files Claude Code keeps on your machine.

```
/insights:my-agentic-setup
```

It reads your session logs, settings, plugins, skills, hooks and the git repos Claude Code ran in, asks one AI pass
(a subagent in your own Claude Code session) to fill in what code alone can't tell, and writes one page,
`~/agentic-setup/my-setup.html`: what you do, what you built, each discipline part by part, and what to try next.

It opens on **Your level**: the level your laptop reads, a starting point a call confirms or raises, never a final score. Set `AGENTIC_SETUP_NO_LEVEL=1` before running for a copy with no level.

## Privacy

- It reads only. Nothing is installed or changed. What it writes goes to `~/agentic-setup/`; delete that folder whenever you like.
- The page holds real project names and excerpts of your prompts. It stays on your machine.
- The page's Share tab sends Proxify a short summary (practice ids and how far each goes, no file names, no prompt
  text) only when you press Send there.

## Install

```
/plugin marketplace add proxify-dev/agentic-kit
/plugin install insights@agentic-kit
```

Needs Python 3.12+, or `uv` (it brings its own Python and PyYAML).

## Update

In a terminal (not inside a Claude Code chat):

```
claude plugin marketplace update agentic-kit
claude plugin update insights@agentic-kit
```
