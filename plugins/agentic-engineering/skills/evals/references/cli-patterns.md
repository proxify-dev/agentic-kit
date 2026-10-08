# CLI Patterns for `claude -p`

## Flag reference

Every performance-relevant flag for programmatic invocations. Flags are grouped by purpose.

### Core invocation

| Flag | Syntax | Purpose | Eval notes |
|---|---|---|---|
| `-p, --print` | `claude -p "prompt"` | Non-interactive mode. Required for all programmatic use. | Accepts stdin: `echo "prompt" \| claude -p` |
| `--bare` | `claude --bare -p "..."` | Skip hooks, plugins, skills, auto-memory, CLAUDE.md. Clean environment. | **Fastest mode.** Requires `ANTHROPIC_API_KEY` or `apiKeyHelper`. See auth bridge below. |
| `--model` | `--model sonnet` or `--model claude-sonnet-4-6` | Model selection by alias or full name. | Use Sonnet for high-volume evals, Opus for quality-sensitive runs. Same model on both arms for fair A/B comparison. |
| `--effort` | `--effort low\|medium\|high\|max` | Reasoning effort level. | `low` for triage passes, `high` for full evals. `max` is Opus-only. |

### Tool control

| Flag | Syntax | Purpose | Eval notes |
|---|---|---|---|
| `--tools` | `--tools ""` or `--tools "Bash,Read"` | Restrict available tools. `""` disables all. | `--tools ""` makes pure-inference evals dramatically faster — no tool-call roundtrips. |
| `--allowed-tools` | `--allowedTools "Bash(git *)" "Read"` | Auto-approve specific tools (still available, just skip permission prompts). | Use permission rule syntax. Trailing ` *` enables prefix matching. |
| `--disallowed-tools` | `--disallowedTools "Bash" "Edit"` | Remove tools from context entirely. | Reduces model decision space → faster inference. |

### Output control

| Flag | Syntax | Purpose | Eval notes |
|---|---|---|---|
| `--output-format` | `--output-format json\|stream-json\|text` | Response format. See comparison table below. | `json` for most evals. `stream-json --verbose` only for breakpoint/behavioral evals. |
| `--json-schema` | `--json-schema '{"type":"object",...}'` | Force structured output conforming to JSON Schema. | Result lands in `structured_output` field. Useful for scoring evals with structured rubrics. |
| `--verbose` | `--verbose` | Additional runtime info in stream output. | Required alongside `stream-json` for full tool trace visibility. |

### Cost and limits

| Flag | Syntax | Purpose | Eval notes |
|---|---|---|---|
| `--max-budget-usd` | `--max-budget-usd 0.50` | Hard dollar cap per invocation. Print mode only. | **Always set this in evals.** Prevents runaway costs from unexpected agentic loops. |
| `--max-turns` | `--max-turns 5` | Limit agentic turns. Exits with error at limit. | Prevents wandering. If skill content is pre-injected, 3-5 turns is usually enough. |

### Prompt injection

| Flag | Syntax | Purpose | Eval notes |
|---|---|---|---|
| `--system-prompt` | `--system-prompt "You are..."` | **Replace** entire system prompt. | Use for bare-mode evals where you inject skill content directly. |
| `--append-system-prompt` | `--append-system-prompt "Also..."` | **Append** to default system prompt. | Use for harness-mode evals where you want base behavior + extra context. |
| `--system-prompt-file` | `--system-prompt-file ./prompt.txt` | Replace system prompt from file. | Cleaner than shell-quoting long prompts. |
| `--append-system-prompt-file` | `--append-system-prompt-file ./extra.txt` | Append from file. | Combine with `--system-prompt` for layered injection. |

### Isolation and safety

| Flag | Syntax | Purpose | Eval notes |
|---|---|---|---|
| `--no-session-persistence` | `--no-session-persistence` | Don't save session to disk. Print mode only. | **Always use in evals.** Prevents polluting session history. |
| `--dangerously-skip-permissions` | `--dangerously-skip-permissions` | Skip all permission prompts. | Required for unattended eval runs. Only use in sandboxed environments. |
| `--disable-slash-commands` | `--disable-slash-commands` | Prevent skill invocation. | Use when testing raw model behavior without skill influence. |
| `--session-id` | `--session-id "uuid"` | Pin a specific session UUID. | Useful for deterministic session tracking in eval logs. |
| `--fallback-model` | `--fallback-model sonnet` | Auto-fallback on overload. Print mode only. | Improves eval reliability under load. |

## OAuth auth bridge for `--bare` mode

`--bare` skips OAuth and keychain reads. Subscription-plan users (no `ANTHROPIC_API_KEY`) need the `apiKeyHelper` bridge.

### How it works

`apiKeyHelper` is a setting that specifies a shell script to generate an auth token. The script runs on each invocation, so it handles token rotation automatically.

On macOS, the OAuth token is stored in the login keychain under the service name `Claude Code-credentials`. The extraction one-liner:

```bash
security find-generic-password -s "Claude Code-credentials" -a "$(whoami)" -w \
  | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['claudeAiOauth']['accessToken'])"
```

On Linux and Windows there is no keychain: Claude Code keeps the same JSON in `<claude home>/.credentials.json`,
where `<claude home>` is `CLAUDE_CONFIG_DIR`, else `~/.claude` (`%USERPROFILE%\.claude` on Windows):

```bash
python3 -c "import json,os; d=os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude'); print(json.load(open(os.path.join(d,'.credentials.json'),encoding='utf-8'))['claudeAiOauth']['accessToken'])"
```

```powershell
$d = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME ".claude" }
(Get-Content (Join-Path $d ".credentials.json") -Raw | ConvertFrom-Json).claudeAiOauth.accessToken
```

### Invocation with apiKeyHelper

```bash
claude --bare -p "your prompt" \
  --settings '{"apiKeyHelper": "security find-generic-password -s \"Claude Code-credentials\" -a '\"$(whoami)\"' -w | python3 -c \"import sys,json; print(json.loads(sys.stdin.read())[\\\"claudeAiOauth\\\"][\\\"accessToken\\\"])\""}' \
  --output-format json \
  --no-session-persistence
```

### If you have `ANTHROPIC_API_KEY`

No bridge needed. `--bare` works directly:

```bash
ANTHROPIC_API_KEY=sk-... claude --bare -p "your prompt" --output-format json
```

### Platform notes

- **macOS:** Keychain extraction via `security` command as shown above.
- **Linux / Windows:** No keychain. Read `<claude home>/.credentials.json` as shown above (the same one-liner works as an `apiKeyHelper`), or set `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`).
- **CI:** Set `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`) as a secret.
- **Token rotation:** The `apiKeyHelper` re-extracts on every invocation, handling expiry transparently. If the keychain entry format changes in a Claude Code update, update the JSON path in the extraction script.


## Thinking in the stream

`stream-json --verbose` carries a `thinking` content block beside `text` and `tool_use` — useful when the concept you want to verify appears in the reasoning but not in the final answer. In `-p` the block comes back **empty** by default: the model still thinks and you are still billed, but the text is redacted to a signature. Pass `--thinking-display summarized` to get the summary text. The `showThinkingSummaries` setting does not fill it in `-p`, even when on.

```json
{"type": "assistant", "message": {"content": [
  {"type": "thinking", "thinking": "Setting ball's price as x, bat as x+1.00, so 2x+1.00=1.10..."},
  {"type": "text", "text": "The ball costs $0.05."}]}}
```

Collect it beside the tool-use trace: iterate `message.content[]`, branch on `block["type"]`, and append each `block["thinking"]` to a `reasoning` list.

The list backs a "did the model consider X?" assertion — a substring check answers most. The block existing proves only that the model entered extended thinking on that turn, not that the reasoning was sound: the substring must be specific enough that only genuine consideration of the concept produces it.

Two gotchas before relying on reasoning assertions:

1. **Trivial prompts skip thinking.** The model decides per turn; `"say hello"` returns only text blocks and the assertion fails on an empty trace. Use a prompt you have verified triggers thinking.
2. **An empty `thinking` string means the flag is missing**, not that the model skipped thinking.

One-shot verification:

```bash
claude -p "A bat and a ball cost 1.10 total; the bat costs 1.00 more than the ball. Think it through, then give the ball's price." \
  --thinking-display summarized --effort high \
  --output-format stream-json --verbose --tools "" --no-session-persistence \
  | jq -r 'select(.type=="assistant") | .message.content[] | select(.type=="thinking") | .thinking'
```

## Output format comparison

| Field | `text` | `json` | `stream-json` |
|---|---|---|---|
| Final answer | stdout (plain) | `.result` | Per-message text blocks |
| Cost (USD) | No | `.total_cost_usd` | In final `result` message |
| Token usage | No | `.usage` (input, output, cache_creation, cache_read) | In final message |
| Per-model breakdown | No | `.modelUsage` | In final message |
| Duration | No | `.duration_ms`, `.duration_api_ms` | In final message |
| Num turns | No | `.num_turns` | In final message |
| Session ID | No | `.session_id` | Per message |
| Tool trace | No | No | Yes (with `--verbose`) |
| Stop reason | No | `.stop_reason` | Per message |
| Permission denials | No | `.permission_denials` | Per message |
| Partial streaming | N/A | N/A | With `--include-partial-messages` |
| Hook events | N/A | N/A | With `--include-hook-events` |
| Structured output | N/A | `.structured_output` (with `--json-schema`) | N/A |
| Thinking blocks | No | No | Per assistant message; text only with `--thinking-display summarized` (see above) |

### Decision rule

- **Use `json`** for most evals. Single JSON blob with cost, tokens, duration. Parse with `jq` or Python's `json.loads()`.
- **Use `stream-json --verbose`** only when you need real-time tool trace observation — breakpoint evals that kill at a specific tool call, or orchestration evals that assert tool order.
- **Never use `text`** for evals. You lose all metrics.

## Invocation templates

### Bare quality eval (fastest)

For testing output quality with skill content injected. No tools, no harness overhead.

```bash
claude --bare -p "$PROMPT" \
  --output-format json \                     # single JSON blob with all metrics
  --system-prompt "$SKILL_CONTENT" \         # inject skill content directly
  --model sonnet \                           # faster model for high-volume runs
  --tools "" \                               # no tools — pure inference
  --max-budget-usd 0.50 \                   # hard cost cap
  --max-turns 1 \                            # single turn, no agentic loop
  --no-session-persistence \                 # don't pollute session history
  --dangerously-skip-permissions \           # unattended execution
  --settings '{"apiKeyHelper": "..."}'       # OAuth bridge (see auth section)
```

### Behavioral eval (full harness)

For testing how Claude interacts with the harness — skill invocation, tool usage patterns, read-before-write behavior.

```bash
claude -p "$PROMPT" \
  --output-format stream-json \              # real-time tool trace
  --verbose \                                # full tool call details
  --allowedTools "Skill,Read,Glob,Grep,Write" \  # tools needed for the behavior
  --max-budget-usd 0.10 \                   # low cap — breakpoint kills early
  --max-turns 5 \                            # prevent wandering
  --no-session-persistence \                 # clean environment
  --dangerously-skip-permissions             # unattended execution
```

## Measured performance baselines

Measured 2026-04-08 on macOS, same prompt ("Say hello in exactly 5 words"), same workspace. Your results may vary.

| Metric | Normal mode | Bare mode | Delta |
|---|---|---|---|
| Wall clock | 3.57s | 2.29s | -36% |
| API duration | 2,007ms | 1,205ms | -40% |
| Input tokens | 10,573 | 364 | **-96.6%** |
| Output tokens | 11 | 10 | ~same |
| Cost | $0.0231 | $0.0012 | **-94.8%** |
| Model (default) | opus-4-6[1m] | sonnet-4-6 | bare defaults to sonnet |

The input token reduction comes from eliminating harness overhead: CLAUDE.md, plugins, auto-memory, skills, hooks — ~10,000 tokens that load on every normal invocation.

For eval-scale impact: a 6-run A/B eval at $0.023/run costs $0.14 total. With `--bare` at $0.001/run, the same eval costs $0.007 — **20x cheaper**.
