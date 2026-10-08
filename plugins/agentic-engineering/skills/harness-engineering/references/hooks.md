# Hooks: what Claude Code does with one, and writing one that does not break

Writing a hook, or finding out why one stays silent. Start from [a hook template that handles the common failures](../assets/hook.py). Elsewhere:
- [Choosing which event carries the rule](channels.md)
- [Choosing the check a blocking hook should run](checks.md)
- Back to [placing a rule](../SKILL.md)

From the Claude Code hooks docs, unless marked "seen" (observed in this kit's sessions).

## Anatomy

- A command hook reads the event's JSON on stdin (an HTTP hook: the POST body) and may print JSON or plain text; an MCP-tool hook's text output is read like stdout.
- Output that starts with `{` and ends with `}` is parsed as JSON; anything else is plain text. JSON fields are read on every exit code.
- Exit 0 is success. Exit 2 is a blocking error whose effect depends on the event (below); a JSON `permissionDecision: "allow"` cannot override it. Any other non-zero exit is a non-blocking error the person sees as a notice while the action goes ahead, unless valid JSON on stdout decides the outcome. A policy hook exits 2 or returns a JSON deny.
- An enabled plugin's hooks fire in every session where the plugin is on, used or not, with the person's own permissions.

## Who reads each output

| output | the model | the person |
|---|---|---|
| `hookSpecificOutput.additionalContext` | yes, as a system reminder where the hook fired | no |
| plain stdout, exit 0 | on SessionStart, UserPromptSubmit, UserPromptExpansion, PostModelSwitch; elsewhere the debug log | no |
| `permissionDecisionReason` (PreToolUse) | on `deny`; the call does not run | on `allow` and `ask` |
| `decision: "block"` + `reason` | PostToolUse, next to the result; Stop, SubagentStop, as the reason to keep working | UserPromptSubmit: prompt erased, reason kept out of context |
| `systemMessage` | only from an `async: true` hook, and DirectoryAdded on `/add-dir` | from any other hook; some events discard it |
| `continue: false` + `stopReason` | if the conversation goes on | yes |
| stderr, exit 0 | no: debug log | no |

Example: a hook that put its rule for the model in `systemMessage` alone never reached the model; only the person saw it.

## Exit 2 + stderr, per event

- PreToolUse: blocks the call; Claude reads stderr as the reason.
- PostToolUse, PostToolUseFailure: the tool already ran; Claude reads stderr as a warning.
- Stop: Claude keeps working, with stderr as the reason.
- UserPromptSubmit: erases the prompt; only the person sees stderr.
- SessionStart, SubagentStart: only the person sees it, as a hook-error notice.

## Events and matchers

- PreToolUse fires before a tool call and can block it. It and PostToolUse fire inside subagents and workflow agents too, with `agent_id` and `agent_type` in the input.
- UserPromptSubmit fires on a submitted prompt, before Claude processes it.
- SessionStart fires when a session begins or resumes; only it can receive a `model` field, and not always. Matcher values: `startup`, `resume` (`--resume`, `--continue`, `/resume`), `clear`, `compact` (auto or manual), `fork` (`--fork-session`, `/fork`, `/branch`; before v2.1.214 a fork reported `resume`). `startup|resume|clear|compact` skips forks; `*`, `""` or no matcher fires on all.
- A Bash matcher with `if` rules (`"if": "Bash(claude *)"`) starts the hook only for matching commands, so every other command starts no process. The rule is best effort: the script checks the command again.

## Limits

- Each `additionalContext`, `systemMessage` and plain stdout is capped at 10,000 characters, one field at a time. Past it Claude gets a file path and a 2,000-character preview, and is not asked to read the file.
- Every hook's `additionalContext` for one event is delivered. PreToolUse decisions resolve deny > defer > ask > allow.
- On resume, hook context from the middle of the session is replayed from the transcript, not re-run; SessionStart runs again with `source: "resume"`.
- `transcript_path` is written asynchronously and can be behind the current turn.

## Write the text as facts

"This repo uses `bun test`" is followed. Text written as an out-of-band system command can trigger Claude's prompt-injection defenses: Claude shows it to the person instead of acting on it. Give the step to take and its reason.

## Inject a text once per agent

- Start each injected text with its own first line (its tag): unique, plain ASCII, no quote or backslash, so it reads the same in every transcript record.
- Before printing, look in `transcript_path` for the tag in a `hook_additional_context` record after the last `compact_boundary` record: a read, nothing written. A subagent's call (`agent_id` set) is checked in its own file, `<session>/subagents/agent-<id>.jsonl`, one folder deeper for a workflow agent (seen, not documented). Because the transcript can be behind, two quick calls may both include the text.
- A deny is stored as an error tool result ("PreToolUse:<Tool> hook error: …"), not a hook record: look there for an earlier deny (seen).

## Start it with one interpreter and no shell

- One command string, double quotes only, starting with a bare word, so it means the same under sh, cmd and PowerShell: `uv run --quiet --no-project --python ">=3.10" "${CLAUDE_PLUGIN_ROOT}/hooks/<hook>.py"`. No `.sh` launcher.
- Never rely on the `python3` on PATH: on macOS it is 3.9, and five hooks crashed on it at import, on every session start (seen).
- Standard library only, so nothing installs at start; about 5 ms a call. No network, no subprocess: the hook runs on every event it matches.

## A person or a program

- Hooks see `CLAUDE_CODE_ENTRYPOINT`: `cli`, `claude-desktop` and `sdk-ts` are a person; `sdk-cli` (`claude -p`) and `sdk-py` a program (seen, not documented). Unset counts as a person.
- A program's session (a script, an eval, CI) gets each plugin's one line that explains its tools and nothing else, and the hooks write nothing anywhere for it.
- An eval turns the hooks back on with a variable it sets; `claude plugin eval` passes only `EVAL_*` variables to a case, so the variable needs an `EVAL_` name.

## Many plugins at once

- The SessionStart lines of every installed plugin are injected together into one session. Give them one shared budget (seen here: 2,400 characters together, 800 a plugin) and one test that runs every plugin's SessionStart hooks together.
- Each plugin names its own commands in one line, never a list of the other plugins.
- A hook that creates something in the person's home folder says so once, in `systemMessage`, with how to put it elsewhere.

## How hooks break

- A Stop hook that blocks can loop forever: when the input's `stop_hook_active` is true, let the stop through.
- A slow hook delays every call it matches: set `timeout`, read files only.
- A blocking hook that never starts (a mistyped command path) or times out lets the call through; a PreModelSwitch timeout does block the switch. Check a new blocking hook's first run.
- Output fills the context window: lint only the file just written, and cap what is printed.
- A hook may tighten permissions; a hook that answers `allow` loosens them for every session.
- When the hook itself errors, what it does depends on its job. A hook that adds context exits 0 and prints one stderr line naming what is missing and the command that fixes it, so a broken hook costs the session nothing. A hook that blocks unsafe actions denies the call, with the reason, because a blocking hook that lets calls through when it errors stops protecting without any sign. Plan that case on purpose: one blocking hook denied every call when `jq` was missing (APort).

## Introduce a blocking hook in steps

1. Inject the rule as context before the tool call first.
2. Watch it fire in real transcripts; count the calls it would have refused. Run the deny against recorded calls before turning it on (ax tests hooks against recorded calls this way; with the kit CLI, `ak trace --tool <tool> --json` holds every recorded call).
3. Then deny, with the command to type instead.

## Test it the way Claude Code runs it

Pipe a real payload into the exact command from hooks.json, with `CLAUDE_PLUGIN_ROOT` set, in a throwaway `HOME`. A hook run through uv also needs `UV_CACHE_DIR` and `UV_PYTHON_INSTALL_DIR` pointed at the real ones: in a bare `HOME`, uv's own cache shows up as files the hook wrote. Then [find the text in the agent's transcript](channels.md) (§ Prove it reached the agent).
