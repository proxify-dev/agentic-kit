String substitutions expand once over the original SKILL.md before Claude sees it; injected command output is not re-scanned.

## Argument substitutions

- `$ARGUMENTS` → full arg string as typed. Absent from body → Claude Code appends `ARGUMENTS: <value>` at end.
- `$ARGUMENTS[N]` → arg by 0-based index. Shell-style quoting; wrap multi-word values in quotes.
- `$N` → shorthand for `$ARGUMENTS[N]` (`$0` first, `$1` second).
- `$name` → Named argument declared in the arguments frontmatter list. Names map to positions in order, so with arguments: [issue, branch] the placeholder $issue expands to the first argument and $branch to the second.

## Runtime substitutions

- `${CLAUDE_SESSION_ID}` → current session ID. Session-scoped files, logging, output correlation.
- `${CLAUDE_EFFORT}` → active effort: `low` `medium` `high` `xhigh` `max`. Branch instructions on it.
- `${CLAUDE_SKILL_DIR}` → dir holding this SKILL.md. For plugin skills = skill subdir, NOT plugin root. Use for bundled script/file paths so they resolve regardless of cwd.

## Environment variables

- `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` → load CLAUDE.md from `--add-dir` dirs.
- `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` → required before frontmatter `shell: powershell` runs inline commands via PowerShell on Windows.
- `SLASH_COMMAND_TOOL_CHAR_BUDGET` → fixed char count for the skill-listing budget (alternative to `skillListingBudgetFraction`); raise when `/doctor` shows description truncation.
