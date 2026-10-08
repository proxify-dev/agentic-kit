# Skill use cases

Find the row for what the skill must do, read across. Keys are the ones in `_template.md`: uncomment them there.

Arguments are text. What follows "`/name`", or what Claude passes on the Skill call, is pasted into the body before the model reads it: no types, no flags, no validation. Substitution runs once; argument values and `!` output go in as plain text and are never re-scanned. [Every placeholder and what it expands to](./arguments.md).

## A. Input

| I need | I write | Watch out |
|---|---|---|
| No input: a convention or domain knowledge Claude applies | no `arguments`, no `argument-hint`, no placeholder | Arguments typed anyway are appended as `ARGUMENTS: <value>` |
| One free-text input: issue number, path, topic | `argument-hint: [issue-number]` and `$ARGUMENTS` inside a sentence | `$ARGUMENTS` is the whole string as typed. If no placeholder receives an argument, `ARGUMENTS: <value>` is appended |
| Several inputs, readable body | `arguments: [issue, branch]`, then `$issue` and `$branch`; `argument-hint: [issue] [branch]` | Names map to positions in order. A named one with no argument expands to empty |
| Several inputs, by position | `$0`, `$1` or `$ARGUMENTS[0]` (0-based) | An index with no argument stays as literal `$2` and does not count as received |
| A multi-word value | the caller quotes it: `/skill "hello world" second` makes `$0` = hello world | Shell-style quoting. `$ARGUMENTS` stays the raw string |
| An input that may be absent | a named argument, placed where empty reads fine | Named expands to empty; an index leaves `$1` in the text for the model to read |
| A literal `$1.00` in prose | `\$1.00` | One backslash only: `\\$1` still expands |
| Two skills on one input | the caller types `/a /b 123`: both get `123` | The first plus up to five more. A forked skill or `/loop` ends the run; it and the rest become every skill's argument text |

## B. Who fires it

| I need | I write | Watch out |
|---|---|---|
| A person and Claude | nothing (default) | The description is in context every turn; the body loads on use |
| Only a person: deploy, commit, send | `disable-model-invocation: true` | The description leaves context; no subagent can preload it; a scheduled task cannot fire it (v2.1.196). If Claude tries anyway it is blocked and told to suggest `/name` |
| Only Claude: background knowledge | `user-invocable: false` | Hidden from `/`; typing `/name` runs nothing; the description stays in context |
| A hook names it (this kit) | the default; the agent loads it with the Skill tool | Nobody types arguments: give the skill none, or have the hook block say what to pass |
| Its command name | folder name, or `name`; a plugin skill is `/<plugin>:<name>` | `name` sets only the last part; bare `/name` works unless taken |

## C. Where it runs

| I need | I write | Watch out |
|---|---|---|
| A heavy task, main chat stays clean | `context: fork` and `agent: Explore` (or Plan, general-purpose, a custom agent) | The subagent never sees the conversation: the body needs an explicit task, guidelines alone return nothing. Runs in the background unless `background: false` (v2.1.218). Its edits escape `/rewind` |
| A task that needs the conversation so far | no `context: fork` | It starts a fresh subagent with the skill as its prompt; it is not a fork of the conversation |
| Another model or effort | `model:` and `effort:` | Last for the turn; the session resumes on the next prompt |
| Load automatically only near some files | `paths: "src/**,docs/**"` | Limits automatic loading |
| Deeper thinking | the word `ultrathink` in the body | |

## D. Tools

| I need | I write | Watch out |
|---|---|---|
| Run a bundled script with no prompt | `allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/x.sh *)`, and the body runs that exact path | The grant lasts the invoking turn and restricts nothing; deny and ask rules win. Session-wide grants go in permission settings |
| Keep tools away while it runs | `disallowed-tools: AskUserQuestion` | Clears on the next message. For autonomous or background skills |

## E. Live data and paths

| I need | I write | Watch out |
|---|---|---|
| Command output in the prompt | `` !`gh pr diff` `` inline; several lines: a block fenced ```` ```! ```` | Runs before Claude sees the body. `!` must start a line or follow whitespace. A non-zero exit aborts the whole skill: append `\|\| true`. Exit 1 from grep, diff and the like is fine |
| That command needs permission | `allowed-tools: Bash(gh *)` | Injected commands never prompt: an unapproved one aborts the skill. `disableSkillShellExecution` turns them off; skills synced from claude.ai never run them |
| A file bundled with the skill | `${CLAUDE_SKILL_DIR}/scripts/x.sh` | In a plugin, the skill's folder, not the plugin root. Replaced in the body and in `allowed-tools` |
| A file shared by a plugin's skills | `${CLAUDE_PLUGIN_ROOT}/…`; kept across updates: `${CLAUDE_PLUGIN_DATA}` | Plugin skills only |
| A project-local script | `${CLAUDE_PROJECT_DIR}/…` | Needs v2.1.196 |
| A session-scoped file or log | `logs/${CLAUDE_SESSION_ID}.log` | |
| Behavior by effort | branch on `${CLAUDE_EFFORT}`: low, medium, high, xhigh, max | |

## F. Portable and checked

| I need | I write | Watch out |
|---|---|---|
| To ship to claude.ai or the Skills API | only `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools` | Any other key (`argument-hint`, `arguments`) fails the upload; `!` injection does not run there |
| To catch a skill that will not parse | `claude plugin validate <skills dir>` (v2.1.233) | Frontmatter counts only when `---` is line 1. Bad YAML loads the body with no fields: `/name` works, the description never matches. A misspelled key is ignored silently |
