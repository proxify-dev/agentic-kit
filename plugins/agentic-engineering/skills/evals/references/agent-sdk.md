# Running evals with the Agent SDK

The SDK drives the same Claude Code as `claude -p`, from Python. Its raw messages are the lines `claude -p --output-format stream-json --verbose` prints, so graders written for one read the other. The typed messages are not those lines: in Python the raw ones come from `client._query.receive_messages()`.

## What it adds over `claude -p`

| Need | With the SDK |
|---|---|
| **A breakpoint the call must not pass** | A `PreToolUse` hook in `hooks=` returns `permissionDecision: "deny"`; then `client.interrupt()`. The denied Edit leaves the file untouched |
| **Stop a run mid-turn** | `client.interrupt()` ends the turn cleanly; the result still reports the cost, and no process is left behind |
| **Test the runner itself** | Swap the client for a fake object: no process, no cost |

## Defaults to override

Each one silently makes the run unlike a real session.

| SDK default | Set instead |
|---|---|
| Its own bundled `claude` binary: not the version you ship | `cli_path=shutil.which("claude")` |
| An empty system prompt | `system_prompt={"type": "preset", "preset": "claude_code"}`, plus the case's own as `"append"` |
| No settings files loaded (no CLAUDE.md, no project settings) | `setting_sources=["project", "local"]`: leave out `"user"` to keep your own config out |
| The runner's own environment under the `env=` you pass | Remove what a run must not inherit (`CLAUDECODE`, your plugins' variables) from `os.environ` itself, before the first session |
