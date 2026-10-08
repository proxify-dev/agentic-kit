---
name: sandboxing
description: "Trying a Claude Code plugin, hook, skill or MCP server in isolation: from scratch, without touching the real ~/.claude, then seeing exactly what it changed. Use when installing a new or untrusted plugin, testing one from zero, running evals whose hooks must not run on the host, or giving a sandbox a copy of chat histories or a plugin's store."
---

Running a plugin somewhere it can't reach your real setup, and reading back what it did there.

## What are you trying to do?

- **Trying a plugin from scratch**, or one you don't trust: the steps below.
- **Checking an edit to your own plugin** works in a normal session: `claude --plugin-dir <path>` is enough, and no sandbox is needed. See [testing a hook or plugin you edited](../evals/references/harness-artifacts.md).
- **The recipe for one level** (container, macOS VM): [references/levels.md](./references/levels.md)
- **Bringing real data in** (chat histories, a plugin's database): [references/mounts.md](./references/mounts.md)
- **Something broke inside the sandbox**: [references/traps.md](./references/traps.md)

## Steps

### 1. Name what the plugin can touch

Read its `plugin.json`, `hooks/hooks.json`, `.mcp.json` and any install script, and list:

- **hooks**: they run as shell commands on whatever machine runs `claude`, with your user's rights.
- **data it reads**: transcripts under `~/.claude/projects`, settings, its own store.
- **what it writes outside its folder**: rc files, `~/.local/bin`, a launchd or systemd service, `settings.json`.
- **the OS it needs**: `osascript`, launchd or Keychain mean macOS only.
- **the network it calls**.

Done when each of the five has an answer, even if the answer is "none".

### 2. Pick the level

The level is set by the most dangerous item on that list. A plugin with hooks has run code on your machine the moment a session starts.

| Level | Isolates | Leaks | Fits |
|---|---|---|---|
| `--plugin-dir` | nothing; loads one plugin on top of your setup | everything | your own plugin, a trusted edit |
| container or micro-VM | the whole machine: a fresh OS, no tools, no `~/.claude` | only what you mount in, and the network you allow | any plugin on Linux, or cross-platform: the default |
| macOS VM | a whole Mac: Keychain, launchd, `osascript` | only what you share | a macOS-only plugin |

A different `HOME` or `CLAUDE_CONFIG_DIR` is not a level. It resets the paths that read `~`, and nothing else: the plugin's hooks still run on your machine, share `/tmp`, reach your Keychain and launchd, and write anywhere your user can.

Done when the level isolates every item from step 1 you would not want on your machine.

### 3. Decide what goes in

Start from nothing and add only what the plugin needs to show its behaviour. For each path, pick one way in from [references/mounts.md](./references/mounts.md): a **snapshot** (a copy-on-write clone mounted writable; the default), **read-only** (only for files nothing writes, which rules out SQLite and anything `claude` itself writes), or **live** (never, for a trial).

Take every snapshot twice: a *work* copy that you mount, and a *base* copy that nothing touches. The base is what the change report compares against. Your real folder keeps changing while the trial runs, so it can't be the base.

Credentials go in as a token, not as your config folder. See [references/levels.md](./references/levels.md#credentials).

Done when every mounted path is named with its mode, and nothing under your real `~/.claude` is mounted writable.

### 4. Run it

Start the sandbox, install the plugin the way a new user would (marketplace add, then install), then use it for the task it claims to do. Lock the network to what the plugin needs plus the Anthropic API.

Done when the plugin has done its job at least once: its hook fired, its command ran, or its MCP server answered.

### 5. Read what it changed

Compare each base copy with its work copy:

```sh
git diff --no-index --stat base/ work/      # which files were created, changed or deleted
sqlite3 base/x.db .dump > a.sql && sqlite3 work/x.db .dump > b.sql && diff a.sql b.sql | head   # for a database
```

Inside the sandbox, also list what was written outside the mounts: `~/.claude/settings.json`, rc files, `~/.local/bin`, services.

Done when every change is either expected from the plugin's own description or written down as a finding.

### 6. Throw it away

Delete the sandbox and the work copies. Keep the base copies and the report only if you will compare against them again.

Done when nothing from the trial is left running: no container, no VM, no proxy.
