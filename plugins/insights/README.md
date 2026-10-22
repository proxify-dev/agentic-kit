# insights

How you really work with Claude Code, rebuilt from the files Claude Code keeps on your machine.

```
/insights:my-agentic-setup
```

It reads your session logs, settings, plugins, skills and the instruction files in your repos, and writes
`/tmp/agentic-setup/my-setup.html`: how you run sessions and helpers, what the agent knows before it starts, how you
hand work over, check it and correct it, what you built for yourself, and what runs when you're not there.
`/tmp/agentic-setup/explorer.html` shows the same findings with how each one was worked out.
`/tmp/agentic-setup/level.html` suggests your level (1–4) and shows every check behind it: its bar, your value,
and what it can't be sure of. Only what you built counts (judged by git history), not what you installed.
It's a suggestion for an interviewer, not a verdict.

## Privacy

- It reads only. Nothing is installed, changed or sent. What it writes goes to `/tmp/agentic-setup/` and is gone at the next restart.
- Your page holds real project names and excerpts of your prompts. It stays on your machine; the skill keeps it
  out of the conversation.
- A report to share is written only if you say yes. It holds counts and plain sentences, no names and no prompt
  text. Read it before you share it: it is exactly what the other side would see.

## Install

```
/plugin marketplace add proxify-dev/agentic-kit
/plugin install insights@agentic-kit
```

Needs `python3` (stdlib only). No other plugin.

## Limits

An early version. The numbers were tuned on few machines; a view that says "can't tell" means the logs held too
little to say, never "you don't do it". It cannot see work on other machines, how other agent tools are used, or
anything older than Claude Code's log retention (`cleanupPeriodDays`).

## Development

The scripts are stdlib Python under `skills/my-agentic-setup/scripts/`; `STORY-SCHEMA.md` and `REPORT-SCHEMA.md`
describe their output. Try it from this checkout: `claude --plugin-dir plugins/insights`.
