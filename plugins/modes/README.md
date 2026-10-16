# modes

How a session works with you and with its subagents: it asks you until the direction is clear, stops to
check with you when the work drifts, hands work to agents by model tier, and answers without changing
anything when you only want answers.

Install: `ak setup` and pick modes, or `/plugin install modes@agentic-kit`. Needs Claude Code 2.1.298 or newer:
an older one loads nothing from it, silently (`/ask` is then an unknown command; checked on 2.1.295–2.1.301).
It is a Claude Mod, so it loads only with `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` in the `env` block of `~/.claude/settings.json` (`ak setup`
sets it). Was `agentic-workflows` until 0.7.0.
To try a change in this folder: `claude --plugin-dir plugins/modes` (the engine refuses a hooks module whose
real path lies outside the plugin folder, so not through a symlink).

| what | does |
|---|---|
| `/modes:interview` | rounds of AskUserQuestion until you and Claude agree on the direction; you start it, Claude never does |
| `/modes:align [what looks off]` | mid-task: no more changes until agreed; Claude plays back the goal, what it did and what was next, names the step where the work drifted, asks with AskUserQuestion, then continues on the answer; you start it |
| `/orchestrate [on\|off\|status]` | a session mode: hand parts to subagents on another model; `orchestrating` in the prompt footer while on |
| `/ask [on\|off\|status]` | a session mode: answer only, change nothing; Edit, Write and NotebookEdit are refused while on (a subagent's too); `ask mode` in the prompt footer while on |

A session mode is for this session only, answered by the mod with no model call, and Claude is told at
each switch (below); both can be on at once. The modes are a Claude Mod: `hooks/register.ts`, the
plugin's one hooks module, registers them through `hooks/mode.ts` (the switch, the notes, the chip, for
every mode; each event hooked once, as the engine requires) from one `ModeSpec` each
(`hooks/orchestrate.ts`, `hooks/ask.ts`). It loads only with `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`. A new
mode is a `ModeSpec`, its entry in `register.ts` and its `prompts/` files.

What Claude reads is in `prompts/`, one file per moment, read when it is sent:
`prompts/<moment>.<mode>.md` (`switch-on`, `switch-off`, `each-prompt`, `after-compaction`; ask also
`before-edit`, the reason an edit is refused), its frontmatter (`why`, `from`) stripped, `{{rules}}`
filled from `prompts/_rules.<mode>.md`. Change the words there; the mod holds none.

`_rules.ask.md` names every kind of change, the shell's included: the mod refuses the file tools, but a
Bash command that writes is held back by the rules alone, since telling one from a read takes more than
its text.

`_rules.orchestrate.md` is the delegation rules: work stays in the main session unless a part is
independent, worth more than the cost of starting an agent (~5k tokens), and wants another model (haiku ·
sonnet · opus). The rules name models, never agents: Claude picks the agent type that fits, the project's
own included, and sets the model on the call.

## What Claude is told, and when

A plugin can add text to the conversation but never take it back. So each switch is written in once, where
it happens, and the history says what is in force at every point:

The same for each mode; the sizes are orchestrate's.

| moment | Claude gets | size |
|---|---|---|
| `/orchestrate on` | the command's row, and as its hidden note "Orchestration switched ON …" + the rules | ~500 tokens, once |
| each prompt while on | one reminder line, attached to the prompt | ~50 tokens |
| `/orchestrate off` | the command's row, and as its hidden note "… the rules earlier in this conversation no longer apply" | ~50 tokens, once |
| the first prompt after a compaction, while on | the rules again, in place of the reminder: a summary can drop them | ~500 tokens |

Before 0.3.0 the rules came on every prompt and `off` was never said: asked after `off`, a model still answered
that orchestration was ON.

The state is `$.state` (`modes.modes`, one entry per mode by name, contract
`types/index.d.ts`) for the running process, which the footer chip reads, and `$.store` key
`<mode>:<session id>` (`orchestrate:…`, `ask:…`), present only while on, so `claude --resume` finds it; an
entry older than 90 days is removed at session start.

## Checks

From the repo root:

```
claude plugin validate plugins/modes
npx -y -p typescript@5 tsc -p plugins/modes/tsconfig.json
cd plugins/modes && CC_SYSPROMPT_FILE=/dev/null CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude plugin test .
```

`tsc` reads the API from `.claude/types/claude-code.d.ts` (git-ignored): copy it from the plugin-authoring
skill's `types/` folder after a Claude Code update.
