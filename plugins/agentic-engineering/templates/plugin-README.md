# {{name}}

Your own Claude Code plugin: the skills and agents you want in every project, in one folder you own and track
with git.

| a new | goes in | Claude Code knows it as |
|---|---|---|
| skill | `skills/<skill>/SKILL.md` | `/{{name}}:<skill>` |
| agent | `agents/<agent>.md` | `{{name}}:<agent>` |

`/new-skill` and `/new-agent` write here; their Where row switches to the project you are in.

## How Claude Code loads it

This folder is also a marketplace of one plugin (`.claude-plugin/marketplace.json`), installed as
`{{name}}@{{name}}` for every session. Claude Code reads the plugin from this folder, not from a copy: an edit
here loads at `/reload-plugins` or at the next session.

Hooks, commands and output styles go here too, in a plugin's folders (`hooks/hooks.json`, `commands/`,
`output-styles/`); `/plugin-authoring` in Claude Code covers them.

## On another machine

Clone this repo, then in Claude Code run `/new-plugin` with the name `{{name}}` and the clone's folder: it adds
and installs what is there and writes nothing over it. Without the agentic-engineering plugin:

```
claude plugin marketplace add <the clone's folder>
claude plugin install {{name}}@{{name}} --scope user
```
