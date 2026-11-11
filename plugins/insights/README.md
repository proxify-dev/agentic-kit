# insights

How you really work with Claude Code, rebuilt from the files Claude Code keeps on your machine.

```
/insights:my-agentic-setup
```

It reads your session logs, settings, plugins, skills, hooks and the git repos Claude Code ran in, and writes one page,
`~/agentic-setup/my-setup.html`. AI readers (subagents in your own Claude Code session) open the rule files, memory and
skills of your most active projects and review them; one more AI pass fills in what code alone can't tell.

The page opens on **Review**: what works in your setup, quoting the line that shows it, and what to change first, with
what it costs you and the one step to take.

**Your level** shows the level your laptop reads, a starting point a call confirms or raises, never a final score.
Level 3 and up need your own files to show it: your rules, how you correct them, and your memory, each found by the
code and backed by a line quoted from your files. What Claude Code does on its own (its built-in memory, automatic
compaction, sub-agents the model starts) is shown as Claude's, not yours. Set `AGENTIC_SETUP_NO_LEVEL=1` before
running for a copy with no level.

A run spends AI tokens in your own session: on a large setup, a few hundred thousand.

## Privacy

- It reads only. Nothing is installed or changed. What it writes goes to `~/agentic-setup/`, plus a speed-up cache in `~/.cache/setup-snapshot/` that the next run clears once it is 6 hours old; delete either folder whenever you like.
- The page holds real project names, excerpts of your prompts and lines quoted from your rule and memory files. It stays on your machine. A line that looks like it holds a secret is never quoted.
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
