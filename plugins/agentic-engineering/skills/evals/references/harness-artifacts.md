# Testing a hook or plugin you edited

## Testing a harness artifact without reinstalling it

Plugin hooks and skills are served from the installed cache, not the workspace source: after an edit, a plain `claude -p` run tests the old code.

| To test | Run | Side effect |
|---|---|---|
| **The whole plugin, as edited** | `claude -p … --plugin-dir /abs/path/plugins/<p>` | none — loads the checkout for that one session |
| **One hook** | `--settings` injection (below) | none — exercises the real file in isolation |
| **The edit in the user's own session** | ask the user to `/reload-plugins`, or to launch claude in a new session | the change goes live in their real session |
| **A plugin you don't trust, or from zero** | the steps in [sandboxing](../../sandboxing/SKILL.md) | none on the host: it runs in a container or VM |

Register a single hook via `--settings`, pointing at the **absolute workspace path**:

```json
{ "hooks": { "PreToolUse": [ { "matcher": "Edit|Write", "hooks": [ { "type": "command",
    "command": "/abs/path/plugins/<p>/hooks/the-hook.py" } ] } ] } }
```

Tests the real script against the real hook contract, zero cache mutation, parallel-safe. Confirm the *installed* plugin does not also register the same hook or it double-fires; if the cached plugin lacks it, the no-`--settings` arm is a clean control.

## Verifying a hook fired — and what it injected

`--output-format stream-json --include-hook-events` emits `system` messages with `subtype: hook_started | hook_response`. The `hook_response` carries `hook_name`, `exit_code`, `outcome`, and the hook's raw `stdout`. Assert both:

- the hook fired on the intended tool, and
- its payload is exactly expected — `hookSpecificOutput.additionalContext`, or `permissionDecision` + reason for a gate.

This proves **delivery** — the injected text reached the model. Delivery is necessary, not sufficient: see [delivery ≠ compliance](../SKILL.md#delivery--compliance-two-assertions-for-context-injecting-artifacts).
