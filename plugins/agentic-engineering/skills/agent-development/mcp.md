# An agent's MCP servers

Giving an agent MCP servers with the `mcpServers` field: an inline definition, or the name of a server the session already has. Elsewhere:
- [Choosing which tools the agent gets overall: `tools`, `disallowedTools`](./_template.md)
- [Giving an agent a command-line tool instead (§ Before you write one, step 3)](./SKILL.md)

In a plugin, ship the servers in the plugin's `.mcp.json` instead, where they apply whenever the plugin is enabled ([the field is ignored for plugin agents](./_template.md)).

## Inline definition: only this agent gets the server

```yaml
mcpServers:
  - playwright:
      type: stdio
      command: npx
      args: ["-y", "@playwright/mcp@latest"]
  - github
```

The first entry is inline; `github` is a server name (next section). One list can hold both.

- Same schema as a `.mcp.json` entry, keyed by the server name; types `stdio`, `http`, `sse` and `ws`.
- Connected when the agent starts and disconnected when it finishes, on every run.
- It does not need to be in the session's MCP configuration, and the main conversation never gets its tools or their descriptions.

Use it to keep a server with many tools (a browser, a database) out of the main conversation's context window.

An inline server in a project's `.claude/agents/`, or in an `--add-dir` directory's `.claude/agents/`, loads only after that folder is trusted; a parent folder's trust and a `claude -p` run do not count. Until then Claude Code skips every inline server in that file and writes the `~/.claude.json` key to set to the debug log (`claude --debug`). To trust the folder, ask the person to start `claude` in it and accept the trust dialog. Agents in `~/.claude/agents/`, in `--agents` JSON, in the SDK's `agents` option or in managed settings load their inline servers without this check.

## A server name: the agent uses the session's connection

- The server must already be configured in the session. No new server is started: the agent uses the main conversation's connection to it.
- No folder trust check.

## When the agent runs as the session

With `claude --agent <name>` or the `agent` setting, inline servers connect at startup next to the servers from `.mcp.json` and settings files, under the same folder-trust rule.

## Blocked servers

`--strict-mcp-config`, `--bare`, managed MCP configuration and the `allowedMcpServers` / `deniedMcpServers` policies apply to servers in agent frontmatter too: Claude Code skips a blocked server and shows a warning naming it. `--strict-mcp-config` does not filter inline servers passed through `--agents` or the SDK's `agents` option.

## With the `tools` field

A `tools` list with no MCP entry gives the agent no MCP tools. The docs do not say whether an inline server is an exception, so when you set `tools`, add `mcp__<server>` for each server in `mcpServers`:

```yaml
tools: Read, Grep, mcp__playwright
mcpServers:
  - playwright:
      type: stdio
      command: npx
      args: ["-y", "@playwright/mcp@latest"]
```
