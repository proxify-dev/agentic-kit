# insights

How you really work with Claude Code, rebuilt from the files Claude Code keeps on your machine.

```
/insights:my-agentic-setup
```

It reads your session logs, settings, plugins, skills and the instruction files in your repos, and writes
`~/agentic-setup/my-setup.html`: how you run sessions and helpers, what the agent knows before it starts, how you
hand work over, check it and correct it, what you built for yourself, and what runs when you're not there.
One page, `~/agentic-setup/my-setup.html`, with three tabs: your level, how you work, and how each finding was worked out.
The "Your level" tab suggests your level (1–4). AI readers in your own Claude Code decide it from a summary of your
records, against the same level guide and the same way levels are decided from evaluation calls; every counted check
is shown under it with its bar, your value, and what it can't be sure of. Without the Workflow tool (an older Claude
Code) the counted checks alone give the level, as a rough hint. Only what you built counts (judged by git history), not
what you installed.
It's a suggestion for an interviewer, not a verdict.

## Privacy

- It reads only. Nothing is installed, changed or sent. What it writes goes to `~/agentic-setup/` in your home folder; delete that folder whenever you like.
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

## Update

In a terminal (not inside a Claude Code chat):

```
claude plugin marketplace update agentic-kit
claude plugin update insights@agentic-kit
```

Then restart Claude Code. Inside a chat there is no `/plugin update`: typing it just opens the plugin panel.
In a chat, use `/plugin marketplace update agentic-kit`, then `/plugin` → **Installed** → insights → **Update now**,
then `/reload-plugins`.

## Limits

An early version. The numbers were tuned on few machines; a view that says "can't tell" means the logs held too
little to say, never "you don't do it". It cannot see work on other machines, how other agent tools are used, or
anything older than Claude Code's log retention (`cleanupPeriodDays`).

## Development

The scripts are stdlib Python under `skills/my-agentic-setup/scripts/`; `STORY-SCHEMA.md` and `REPORT-SCHEMA.md`
describe their output. Try it from this checkout: `claude --plugin-dir plugins/insights`.
