---
name: blame
description: >-
  Git-blame for Claude Code sessions: find which session read/wrote/edited a
  specific file, newest-first, with the intent behind each change. Use when
  the user asks "find the session that edited a file", "which session touched
  this file", "who changed X", or "when was this file last modified in a chat".
argument-hint: "<file path, basename or folder>"
---

Target file: `$ARGUMENTS`

The block below is pre-fetched at skill load (bash injection). Synthesize from
this block instead of re-querying tools.

<FILE_TRACE>
!`[ -x "${CLAUDE_PLUGIN_ROOT}/.venv/bin/python" ] || rm -rf "${CLAUDE_PLUGIN_ROOT}/.venv"; uv run --project "${CLAUDE_PLUGIN_ROOT}" python "${CLAUDE_SKILL_DIR}/bin/fetch.py" "$ARGUMENTS"`
</FILE_TRACE>

## Step 1 — Present results

One block per session, newest first: date, cwd, branch, `EDIT×n WRITE×n READ×n`;
then for each turn that changed the file, its prompt and one line per change —
`#step Edit L<line> −removed +added · `first old line` → `first new line`` or
`Write created|overwrote, N lines`. `via subagent <name>` blocks are changes a
subagent or workflow made, credited to the session that started it. `matched
paths` says which copies (checkout, worktree, release clone) matched; `same file
name, other path` is a different file — do not merge it into the answer.

A folder (`plugins/x`) comes back as one summary per session instead: the files it
changed under the folder, the turns that changed them and what each asked. For the
why behind those changes, the last line names `tracer sessions replay <session> --role user`:
the user's own prompts in that session, whole.

Answer "what did each change" from these lines. The `open:` line opens exactly
those steps if the user wants the whole diff; do not reach for git log/diff or jq.

`RAW_FALLBACK` means no Read/Write/Edit call named the path; what follows is a
text search, sessions ranked by how often they mention the file name, each with
the turn that mentions it most. Those are leads (a Bash command, a tool result,
a passing mention), not proof of an edit — drill the listed turn before claiming it.


## Anti-patterns

- Don't guess which session touched the file from memory/observations —
  always use the prefetched blame output.
- Don't grep raw JSONL for file paths; the kit index already tracks this
  via the `i_files` table.


## Step 2 — Offer deeper detail

> Want more on one of these?
> - `/tracer:trace <uuid>` for the full session recap
> - `trace turn <uuid>-<turn>` for that turn's steps — each tool call, its input and
>   what it returned; `--open 1,4` opens steps whole
