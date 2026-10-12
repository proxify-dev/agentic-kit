# Claude Code behind this gateway

What changes when Claude Code talks to a gateway instead of `api.anthropic.com`, what this gateway
does about each change, and what a user's own settings can do to it. Every claim here is either
measured on this gateway (numbers, with how) or quoted from Anthropic's docs (linked at the end).
Checked against Claude Code 2.1.280, 2026-09-23.

## The short version

Claude Code treats any `ANTHROPIC_BASE_URL` that is not Anthropic's as a proxy it cannot trust with
its newer request shapes, and quietly switches those off. A gateway that carries them should turn
them back on — alongside its own address, never globally — and pass everything else through as it
came.

```
   feature                     behind ANY gateway, by default      this gateway
   MCP tool search             off → every tool sent in full       on   (ENABLE_TOOL_SEARCH=true)
   routing hints (x-claude-*)  not sent                            on   (CLAUDE_CODE_GATEWAY_HINT_HEADERS=1), traced
   Remote Control (/rc)        off (v2.1.196+)                     off — Anthropic blocks it; nothing to do
   fine-grained tool streaming off                                 off (default kept)
   model discovery (/model)    off                                 off (default kept)
```

## MCP tool search

**What it is.** With tool search on, Claude Code sends MCP tools as names only. When Claude needs
one, it searches, and the tool's full definition joins the request (a `tool_reference` block that
the API expands). With it off, every tool's full definition goes up on every request, used or not.

**Why a gateway turns it off.** From Anthropic's settings reference: unset, Claude Code "loads them
upfront … when `ANTHROPIC_BASE_URL` points to a non-first-party host", because "most proxies don't
forward `tool_reference` blocks". `ENABLE_TOOL_SEARCH=true` "always defers and sends the beta header
… requests fail on proxies that don't support `tool_reference`". So the flag is safe only for a
gateway that forwards those blocks and the beta header untouched.

**This gateway does.** The engine forwards every request header except hop-by-hop ones and the
client's own credential (`src/engine/server.js`, the upstream-header loop), so `anthropic-beta`
reaches Anthropic as sent. Its only body edits are dropping orphaned `tool_use`/`tool_result`
pairs and patching `metadata.user_id`'s account id — `tool_reference` blocks and `defer_loading`
fields pass as they came. Proved end to end: a headless session asked for a tool's parameters had to
search, loaded the tool, and got `200` on the follow-up request.

**What it costs to leave off** — measured through this gateway, same question, same model (Haiku 4.5),
one heavily tooled setup (built-ins + GitHub + agentic kit MCP servers):

```
   tool search        tool definitions sent          prompt size        time     answer
   off (default)      89 in full · 160 KB            ~116K tokens       14.6 s   correct
   on                 12 in full, searched, +1       ~57K tokens         7.1 s   correct
```

Across two days of real traffic (5,412 requests) every request carried 80–126 full tool definitions,
40–65K tokens of them. On a 200K-context model that is a session starting more than half full, and
compacting that much sooner.

**Requirements and traps.**

- Models: Sonnet 4.5, Haiku 4.5, Opus 4.5 and later (they support `tool_reference`).
- `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` set anywhere keeps tool search off whatever
  `ENABLE_TOOL_SEARCH` says (managed settings can override that, v2.1.227+).
- `ENABLE_TOOL_SEARCH=auto` loads everything up front when all tool definitions fit in 10% of the
  context; `auto:N` sets the threshold. A middle ground for a user with few tools.
- Without tool search, Claude is not told when an MCP server failed to connect (it uses
  `WaitForMcpServers` instead of `ToolSearch`).
- `--bare` (`CLAUDE_CODE_SIMPLE=1`) gives Claude only Bash, file read and file edit, plus the tools of
  any `--mcp-config` — no `ToolSearch`, so every tool goes up in full whatever `ENABLE_TOOL_SEARCH`
  says. Seen through this gateway: a bare run with one MCP server sent all 7 of its tools, none
  deferred, while its routing hints arrived. Bare mode also reads no OAuth token or keychain login;
  behind this gateway it needs none — the routing's doorman token passes Claude Code's own gate and
  the gateway injects an account.

## Tools that are always loaded

Deferral is per tool. Three ways to keep one in context from the first turn:

```
   level          how                                                  who sets it
   one tool       "anthropic/alwaysLoad": true in the tool's _meta      the MCP server's author
   whole server   "alwaysLoad": true in the server's config            whoever configures the server
   by size        ENABLE_TOOL_SEARCH=auto:N                            the user's environment
```

Claude Code's own built-in tools (Read, Bash, Edit…) are its choice. An always-loaded server also
makes startup wait for its tools, up to the 5 s connect timeout.

## Routing hints

From v2.1.273 Claude Code can send per-request facts a gateway can use for scheduling, caching and
attribution. Direct to Anthropic they are on by default; to a custom base URL only with
`CLAUDE_CODE_GATEWAY_HINT_HEADERS=1`. They carry fixed vocabularies, tool names and durations —
never prompt text.

```
   header                               value
   x-claude-code-request-class          main · subagent · workflow · compaction · auxiliary
   x-claude-code-agent-type             Explore · Plan · general-purpose · custom · teammate · fork
   x-claude-code-compaction             auto · manual · reactive   (on the summarising request)
   x-claude-code-context-compacted      same values (first main request after a compaction)
   x-claude-code-prev-tool-durations    Bash=742;Read=9   (tool calls whose results this request carries)
```

This gateway turns them on and records them as parsed fields in each trace row (`hints`:
`requestClass`, `agentType`, `compaction`, `contextCompacted`, `tools[{name, ms}]`). The trace's rule
that no header is ever written stands: a value outside the expected shape is dropped, not stored
(`src/identity.ts`, `readHints`). They are forwarded upstream too — Anthropic receives them from a
direct connection anyway. What they answer: which requests were subagents or compactions (cost
attribution), and where a session's time went between model calls.

## Who wins: the routing or the user's settings

The gateway reaches a session through its **launch environment**: the terminal routing fragment
(`src/shim.ts`, a cc-shim fragment) or the app's runner sets `ANTHROPIC_BASE_URL`, the doorman token,
identity headers, and the two variables above. A user's **settings.json `env` block** is applied by
Claude Code over that environment — so it wins. Measured: a launch environment saying
`ENABLE_TOOL_SEARCH=true` and a project's `.claude/settings.local.json` saying `false` gave a session
that saw `false` and sent 89 tools with none deferred. Anthropic's settings reference agrees: an
`env` value "overwrites the same variable exported in your shell".

```
   highest   managed settings        (MDM / IT)
             --settings on the command line
             .claude/settings.local.json   (project, not committed)
             .claude/settings.json         (project, shared)
             ~/.claude/settings.json       (user)
             the shell claude starts from   ← an export here wins over the routing too: it fills only unset values
   lowest    the routing's defaults
```

What follows from it:

- **The routing never overrides a user's choice.** A user who sets `ENABLE_TOOL_SEARCH=false` gets
  `false`; one who exports `auto:5` in the shell gets `auto:5`.
- **A user's settings can silently undo the routing.** `ANTHROPIC_BASE_URL` in any settings file sends
  sessions elsewhere (another proxy's wrapper that writes `settings.local.json` does exactly this);
  `ANTHROPIC_CUSTOM_HEADERS` there replaces the identity headers the trace keys on. So
  `ak gateway` names every such value, where it is set, and what it does (`src/shadows.ts`):

  ```
  shadowed  ENABLE_TOOL_SEARCH=false in ~/.claude/settings.json (user) — keeps MCP tool search off …
  ```

- **The gateway never writes anyone's settings.json.** Settings apply to every surface (terminal, IDE,
  desktop, SDK) and are static: a base URL there has no fall-back when the gateway is down, where the
  routing fragment probes the port and steps aside. And `ENABLE_TOOL_SEARCH=true` set globally would
  break a session aimed at a proxy that does not forward `tool_reference`.
- **A plugin cannot set a session's environment anyway.** A plugin's `settings.json` accepts only
  `agent` and `subagentStatusLine`; its `.mcp.json` `env` reaches only its own server process.
- **For an organisation**, Anthropic's rollout guide puts the base URL in *managed* settings — enforced,
  over every other layer. That trades fail-open for control; it is the org's call, not this tool's.

## Everything else a gateway must get right

From Anthropic's gateway compatibility guide, with where this gateway stands.

```
   requirement                                          here
   stream; never buffer a response                      ✓ piped through
   forward SSE pings; a stream silent 300 s is aborted  ✓ pings pass; holdSeconds (0 by default) must stay
                                                          under 300 — the hold sends nothing while it waits
   forward anthropic-* headers and body fields as an    ✓ the engine forwards all but hop-by-hop headers
   open list, never an allowlist                          and the client's credential
   keep the system prompt's first block first           ✓ `append-system` appends after the last block;
   (the attribution block; Anthropic strips it by         a rule must never prepend or merge blocks
   position)
   don't rewrite system/tools/earlier messages          ! a `redact` rule rewrites; a rewrite can fail the
   (preserved-thinking check: "bound to a different       thinking check, and Claude Code then drops earlier
   conversation")                                          thinking for the rest of the session
   forward error bodies unmodified                      ✓ upstream errors pass; the gateway's own refusals
                                                          use Anthropic's envelope
   retry-after in integer seconds; over 60 → Claude     ✓ a spent-everywhere 429 carries the real wait, so
   Code shows the error at once                           Claude Code stops retrying — correct for a plan limit
   anthropic-ratelimit-unified-* unchanged              ✓ — but with rotation they describe whichever
   (Claude Code's usage display reads them)               account served that response
   x-should-retry unchanged                             ✓
```

Two things never reach any gateway: the fast-mode availability check and the WebFetch domain safety
check call `api.anthropic.com` directly. They are not in the trace, and on a network with no direct
route to Anthropic they fail while inference through the gateway works.

## For setup and onboarding

A setup that turns the gateway on for someone else's machine follows from the above:

1. **Detect** — Claude Code version, the settings layers and the shell (`shadows`), other wrappers
   owning `claude` or `ANTHROPIC_BASE_URL`, running sessions and the base URL each was started with.
2. **Show a plan** — every file it would write, every conflict it found and what that conflict would
   do. It never edits a user's settings.json; a conflict is reported with the line to change.
3. **Apply only its own files** — the routing fragment, the service unit, the accounts file.
4. **Record** what it wrote, so an undo removes exactly that.
5. **Verify** — one real request through the gateway, and `ak gateway` with nothing shadowed.

Sessions already running keep the environment they started with; only a new `claude` picks up a
change.

## Sources

Fetched 2026-09-23:

- Claude Code gateway compatibility guide — https://code.claude.com/docs/en/llm-gateway-protocol
- MCP tool search, `alwaysLoad` — https://code.claude.com/docs/en/mcp (Scale with MCP tool search)
- `ENABLE_TOOL_SEARCH`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` —
  https://code.claude.com/docs/en/env-vars
- Settings precedence, how `env` interacts with the shell — https://code.claude.com/docs/en/settings,
  https://code.claude.com/docs/en/settings-reference
- Plugin `settings.json` keys — https://code.claude.com/docs/en/plugins
- Distributing gateway settings — https://code.claude.com/docs/en/llm-gateway-rollout

Measurements: `claude -p` runs through the live gateway with a probe identity, read back from its own
trace (`~/.local/share/ak/gateway/trace`); the settings experiment used a throwaway project with a
`.claude/settings.local.json` and a launch environment that disagreed with it.
