# agentic-engineering

An agent reads CLAUDE.md once, at the start, and has usually stopped using it by the moment a rule
applies. So it writes session notes into CLAUDE.md, where they become orders. It hands a worker a
note template and gets the template back, copied into every prompt. It ships a skill that nothing
ever loads, and calls a screen fixed without looking at the screen. Each of these happened here,
more than once. A longer CLAUDE.md
does not help: the rule is there, just not in front of the agent when it acts.

This plugin puts the rule there. A hook watches for the moments those mistakes happen and gives the
agent a short block (300 to 630 characters) right then: an edit to CLAUDE.md, a brief handed to a
worker, a new skill, a hook, a test or an eval. Each block names one skill.
The skill holds the depth and loads only when its moment comes.

**Parked.** It is listed in the `ak` marketplace but not enabled; the person switches it on.

## The seven skills

| skill | what it holds | named at |
|---|---|---|
| /agentic-engineering:context-engineering | what an agent's window holds, when, and in what words: any text an agent reads | an edit to CLAUDE.md, a skill, an agent or a prompt; a brief to a worker |
| /agentic-engineering:harness-engineering | everything around an agent except the model, in six parts: instructions (what reaches it before it acts: channels, files, hooks), checks (denies, lints, tests, baseline lints), tools (CLI commands), verification (how it tests its own result), state (what is kept across sessions and compactions) and maintenance (measuring pieces and removing the ones that no longer help); what a harness is on its front page, the published sources and their evidence (`references/field.md`), a hook template (`assets/hook.py`) | every start; wiring a hook or a setting |
| /agentic-engineering:skill-development | whether it should be a skill at all, then designing, delivering, proving and retiring one | a new SKILL.md, agent or command; a plugin listing |
| /agentic-engineering:agent-development | an agent file: which kind (subagent, fork, teammate, the session itself with `--agent`), its frontmatter, its body, memory, MCP servers, and how Claude Code runs it (background, nesting, limits, resuming) | no block yet: one comes with its eval case (`plugins/EVALS.md`) |
| /agentic-engineering:evals | which eval or test answers which question: unit, contract, `claude -p`, tmux, sandbox rehearsal, eval cases | writing a test or an eval; starting Claude from a script or tmux |
| /agentic-engineering:sandboxing | trying a plugin where it can't reach the real setup: the level (container, macOS VM), what data goes in and how (snapshot, read-only), reading back what it changed | no block yet: one comes with its eval case (`plugins/EVALS.md`) |
| /agentic-engineering:fewer-faster-tests | a project's test suite under an agent, on any stack: how long checking one change takes, which tests can fail for a reason and which cannot, what is missing as well as what is extra, whether the agent can run the app and see the result, whether a change runs only its affected tests; options with their trade-offs, the person chooses | no block yet: one comes with its eval case (`plugins/EVALS.md`) |

Skills link each other inside this plugin, where they always ship together; a skill from outside the plugin
or workspace may not be installed, so none is named. When two skills need one fact, it lives in one of them
and the other links it.

## The agent

`agentic-engineering:agentic-engineer` (`agents/agentic-engineer.md`) is a subagent to hand agentic-engineering
work to; `@agent-agentic-engineering:agentic-engineer` calls it by name. It starts with context-engineering and
skill-development loaded whole (`skills:` in its frontmatter) and can load the other two through the Skill tool.
The skills are its instructions; its body holds one step, the check before it reports agent text done: the
checklist in context-engineering § Before a text is done, then a review by a subagent that has not seen the
conversation, with a fixed brief (tasks walked through the links, each Claude Code claim against the docs, facts
stated twice, steps the reader cannot run, metaphors). The blocks still reach it, because the plugin's
hooks fire inside subagents too.

## /new-plugin, /new-agent and /new-skill

Three commands in `hooks/new.tsx`. `/new-plugin` makes your own plugin: one folder, tracked with git, for the
skills and agents you want in every project. `/new-agent` and `/new-skill` make a new agent or skill file, by
default in that plugin.

```
  /new-plugin my-tools                          (or /new-plugin --name my-tools --folder ~/code/my-tools, or a form)
     ├─ writes ~/my-tools-claude/: .claude-plugin/plugin.json, .claude-plugin/marketplace.json (a marketplace of
     │  this one plugin, source "./"), skills/, agents/, README.md (templates/plugin-README.md), .gitignore,
     │  AGENTS.md (templates/plugin-AGENTS.md: where things go, and /plugin-authoring and
     │  /agentic-engineering:harness-engineering for advanced customization), CLAUDE.md (@AGENTS.md: Claude Code
     │  reads CLAUDE.md, not AGENTS.md)
     ├─ runs  git init · claude plugin marketplace add ~/my-tools-claude · claude plugin install my-tools@my-tools --scope user
     ├─ keeps {name, folder} in this plugin's store, then runs /reload-plugins
     └─ you see   created your plugin my-tools at ~/my-tools-claude: /new-skill and /new-agent write there now
```

- **Live**: Claude Code reads a plugin from a directory marketplace's folder, not from a copy (`claude plugin
  update` says so on 2.1.294): an edit there loads at `/reload-plugins` or the next session.
- **Refused**: a folder with other files in it, a name another plugin or marketplace has, a bad name. A failing
  `claude plugin` step stops it with that command's last line, and no plugin is kept.
- **A folder that already holds the plugin** (a clone on a new machine, a run that stopped half-way): it is added and
  installed, and only a missing `marketplace.json` is written. Nothing else in the folder is written over.
- **Names**: Claude Code puts the plugin's name before each skill and agent in it (`/my-tools:release-notes`).

`/new-agent` and `/new-skill` make a new agent or skill file, three ways:

```
  /new-agent --name code-reviewer --description "Reviews diffs for bugs"
     ├─ writes .claude/agents/code-reviewer.md from agent-development's _template.md:
     │  name and description set, every other field commented out, the body a placeholder
     ├─ you see   agentic-engineering: created .claude/agents/code-reviewer.md
     └─ your next prompt carries one line of context for the model, which you don't see, saying what was created

  /new-agent a reviewer for pull requests
     └─ runs /agentic-engineering:agent-development a reviewer for pull requests for you:
        Claude starts at once, the whole skill loaded            (/new-skill: …:skill-development)

  /new-agent
     └─ a pane opens, the cursor in Name
          ✻ New agent
          Writes .claude/agents/<name>.md from the agent template.
          Name: code-reviewer
          Description: Reviews diffs for bugs, after each change
          [ Create file ]  [ Design with Claude ]
          Tab next field · Enter create · Esc cancel
        Create file (or Enter in a field) writes it, as the flags do; Design with Claude runs the skill
        command with the description and "(name: …)", or "a new agent" when both are empty
```

- **Flags**: `--name` and `--description` each take the words up to the next flag; quotes keep words together;
  `--name=code-reviewer` works too. `--description` alone is refused: a name is needed.
- **Name**: lowercase letters, digits and hyphens; `Code Reviewer` and `Code_Reviewer` become `code-reviewer`.
  A bad name is refused (in the pane, shown there, the pane staying open). An empty description becomes a TODO line.
- **The template** is the skill's own `_template.md`, read from the plugin at each run. Its links point inside
  the skill, so in the copy each becomes the skill's command and the file's path in it. `tests/test_new_templates.py`
  checks a template still has what the command replaces: one single-line `name:` and `description:`, and links
  that resolve.
- **Never overwrites**: an existing file is left untouched, and the command says so.
- **Where**: with a plugin of yours, in it (the same path without `.claude/`: `skills/<name>/SKILL.md`,
  `agents/<name>.md`), then `/reload-plugins` runs so it loads now; the form's Where row, or `--where project`,
  writes under the session's working directory instead. Without one, under the working directory: a
  `.claude/agents/` that did not exist when the session started is not watched, and the new agent loads in the
  next session. A plugin whose folder no longer holds its `plugin.json` is ignored. Design with Claude and free
  text add where the file goes to the words: `(put it in my plugin my-tools: <folder>/agents/<name>.md)`.
- **Headless** (`claude -p`, the SDK): the flags and free text work the same; with nothing after the command, the usage.
- **Runs the moment you type it**, even while the agent is mid-turn.

**Where the pane sits** is Claude Code's choice, not the plugin's (the pane's `placement`): in the fullscreen
renderer from 110 columns it is `dock`, a side panel beside the transcript on a grey background Claude Code draws,
and the form draws its own border; in the classic renderer (`/tui default`, or `CLAUDE_CODE_NO_FLICKER=0`) it is
`inline`, above the prompt in Claude Code's own frame. Colors are theme keys (`claude`, `subtle`, `error`), so the
form follows the user's theme.

The skill loads because its slash command runs, not because Claude chooses it: the module calls
`$.command.run` after the command has answered (`$.clock.after`), since Claude Code refuses `command.run` from
inside a `command.run` hook.

### It is a Claude Mod

A Claude Mod is a plugin's TypeScript hooks module, run inside Claude Code: `"modules"` in `hooks/hooks.json`,
beside the Python hooks. Its API is in Claude Code's built-in skill, /plugin-authoring. Its hooks (`on('command.run', …)`) call Claude Code through `$`. A skill could not do
this: a skill's text always goes to the model, and a hook that blocks a skill's expansion shows
"UserPromptExpansion operation blocked by hook" and drops its `additionalContext`.

- The module loads only with `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`; without it the commands are missing and the
  rest of the plugin works as before. `/new-agent` missing from the `/` menu means it did not load:
  `claude plugin validate plugins/agentic-engineering` lists the module and the hooks the loader sees.
- A skill or command of yours named `new-agent`, `new-skill` or `new-plugin` wins: Claude Code refuses the plugin's
  command at session start.
- Tested on Claude Code 2.1.292; `/new-plugin` on 2.1.294. The mods API is early access.

From the repo root, in order:

```
claude plugin validate plugins/agentic-engineering
npx -y -p typescript@5 tsc -p plugins/agentic-engineering/tsconfig.json
cd plugins/agentic-engineering && CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude plugin test .
```

- `tsc` reads the API from `plugins/agentic-engineering/.claude/types/` (git-ignored): `/plugin-types` writes it,
  or copy `~/.claude/plugins/marketplaces/claude-code-plugins/mods/types/claude-code.d.ts` there.
- `claude plugin test` reads its own arguments: nothing may come before `plugin test`. With the kit's cc-shim in
  front of `claude`, which adds a flag, prefix `CC_SYSPROMPT_FILE=/dev/null`.
- `tests/new.test.ts` runs on cut-down templates (a mod's test has no disk); `uv run pytest -q` checks the real
  ones.

## mdmap: what an agent can reach

mdmap moved into the vault plugin on 2026-09-30: `ak vault map <dir>` (`ak mdmap` still answers,
hidden), source `plugins/vault/src/vault/map.py`. It reads a `[[name]]` through the vault's own link
engine, so there is no resolver copy here any more.

## The seven blocks, and when each arrives

Each block is one plain-text file in `prompts/`, printed whole by a hook. It arrives once per agent
(the main session and each worker), and again after a compaction.

| block | arrives |
|---|---|
| `session.txt` | at every start (new, resume, clear, compact, fork) and in each worker (`hooks/session.py`) |
| `instruction-files.txt` | before a Write or Edit of `CLAUDE.md`, `AGENTS.md`, `CLAUDE.local.md`, `.claude/rules/*` |
| `agent-text.txt` | before an edit of an existing `SKILL.md`, a skill's `references/`, `agents/*.md`, `commands/*.md`, a plugin's `prompts/*`, `templates/harness/*.md` |
| `skills.txt` | before writing a new `SKILL.md`, agent or command; an edit of `.claude-plugin/*.json` |
| `hooks.txt` | before an edit of `hooks.json`, a hook script, `.claude/settings*.json` |
| `evals.txt` | before an edit in a plugin's `tests/` or `evals/`; a `claude -p`, `claude plugin eval`, `env … claude -p` or `tmux` command |
| `brief.txt` | before an Agent, Task or Workflow call (not a Workflow resume) |

Every block except `session.txt` comes from `hooks/on_edit.py` on PreToolUse. Bash reaches it through
one handler per `if` rule in `hooks/hooks.json` (`claude *`, `env *claude *`, `tmux *`), so every
other command starts no process. The script re-reads the command, since the rule is best effort.

**Who gets blocks.** A person's session and every worker it starts. A program's session (`claude
-p`, the SDK, CI) gets the start line and nothing else: no block on an edit. An eval opts
in with `EVAL_AK_CAPTURE=1` (`AK_CAPTURE=1` anywhere else). `claude -p --bare` skips every hook.

**After the edit.** When a `CLAUDE.md`, `AGENTS.md` or `CLAUDE.local.md` has been written, a check
tells the model what it found: over 200 lines, an `AGENTS.md` Claude does not read, a link that does
not resolve. The person sees `<path> edited (+n −m)` (`hooks/after_edit.py`).

**Not seen.** An edit made through Bash (`sed -i`, a heredoc). A script that launches claude. A tool
description.

## Try it

    claude --plugin-dir plugins/agentic-engineering

Run it as a person's session, not `-p`. Then:
- Ask it to add a line to a CLAUDE.md. The instruction-files block arrives once. Ask a subagent to do
  the same, and it gets the block too.
- Ask it to run `claude -p` on something. The evals block arrives before the run.

Headless, the way the evals run it. Unset `CLAUDECODE` when you start it from inside Claude;
`--setting-sources local` keeps your other plugins out of the run:

    EVAL_AK_CAPTURE=1 claude -p "add a line to CLAUDE.md" --plugin-dir plugins/agentic-engineering \
      --allowedTools Edit --setting-sources local

## See it land

Each block leaves a record in the transcript of the agent that got it:
`~/.claude/projects/<project>/<session>.jsonl`, and a worker's under `<session>/subagents/`. Grep it
for the block's tag, for example `[agentic-engineering: evals]`.
- A block given as context is an attachment of type `hook_additional_context`.
- A skill's real loads are Skill tool calls in the transcripts, not its line in the listing.

In the kit workspace, `ak plugin log agentic-engineering` shows the same for one session, and
`ak plugin inspect agentic-engineering` counts how often each hook fired and what it cost.

## Evals

`evals/<case>/` holds `prompt.md` (the request and its settings), `scaffold.sh` (the repo the agent
works in) and `graders/` (one check per file; the score is the share that pass). Every case is born
from a session that went wrong and keeps its story: the session, the person's words, what it guards
(`plugins/EVALS.md`). It runs red on the release before the change that makes it pass.

    claude plugin eval plugins/agentic-engineering --scaffold --allow-tools Write Edit \
      --model sonnet --runs 3 --trust-plugin --no-publish

- Each case sets `EVAL_AK_CAPTURE=1`. An eval runs as `claude -p`, a program's session, which gets
  no block without it, and `claude plugin eval` passes only `EVAL_*` variables to a case.
- Eval runs set `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, so each case copies its scaffold's CLAUDE.md into
  `append_system_prompt`. Keep the two in sync.
- `claude-md-is-a-prompt` also carries memory's start text, rendered from this checkout by
  `evals/memory_line.py --write`. Its scaffold fails a run whose copy is stale.
- Bash is left out of `--allow-tools`: on a machine where `~/.docker/bin` holds symlinks, the harness
  refuses any run that grants it. A case that needs Bash (`claude -p`, tmux) runs through
  `evals/run.py` instead.

`uv run pytest -q` runs the hooks the way Claude Code runs them.
