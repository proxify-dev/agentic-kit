# Tools: what an agent can call

Giving an agent a tool to reach a system, or fixing a tool it keeps misusing. Elsewhere:
- [Telling the agent the tool exists, at every session start](channels.md)
- [Proving the agent picks the right command](../../evals/SKILL.md)
- Back to [placing a rule](../SKILL.md)

## A CLI command, not an MCP server

A tool the agent calls is a CLI command whose output names the next step. The line a SessionStart hook injects names the command; the command's own output carries every instruction after that, at the point it applies. With the shell as the only way in, an agent picked the right command first as often as it picked the MCP tool (this kit's tool-routing eval).

| | CLI command | MCP tool |
|---|---|---|
| the next step | in the output, as the call returns: `hint: ak observer get 37078` | in the description, read before the call; the result is data |
| a wrong call | answers with the line to type: `try: ak observer search 'x y'` | a schema error |
| cost before first use | one SessionStart line | each tool's name or schema on every request |
| who it reaches | anyone with a shell: the person, a subagent, `claude -p`, another harness | the clients that load the server |
| code paths | one: the person and the agent run the same command | two, kept in step by hand |

- HumanLayer replaced an MCP server with long outputs by a small CLI and a few example calls in CLAUDE.md.
- MCP still fits a reader with no shell (a chat client) and a service whose sign-in the client holds.
- A tool only your plugin's own code calls is defined inside the plugin as a custom tool, so the call stays internal.

## The output names the next step

- After a result: `hint: <the next command>`, the bare command, ready to run.
- After a usage error: what is wrong, the usage line, and `try: <the line they typed, corrected>`; never a stack trace or a bare schema error.
- After a write: what it turned on, in the words the reader will see later.
- An empty result says it is empty and what to search instead.
This output is a channel: it arrives exactly when the next step is needed, so an instruction about that step belongs here, not in an always-loaded file.

## Print for the reader

- Decide once who is reading: an agent when `CLAUDECODE` is set, stdout is not a terminal, `NO_COLOR` is set or `TERM=dumb`; a person otherwise. Printing the same bytes to a terminal and a pipe is the bug (seen: one command shipped that way).
- For an agent: no colour codes, no box drawing, no padding, no line ever wrapped. `key: value` lines; a header line then one tab-separated row per record; `ok:`, `warn:` and `hint:` on stdout; `error:` on stderr.
- `--json` on every command that reads, with the same data the other modes print.
- Exit codes that each mean one thing: 0 done, 1 not found or failed, 2 ambiguous (the candidates listed), 64 bad input, 66 a needed file missing, 127 a needed tool missing.
- A command that writes is marked so in every list and never runs on a guess.
- The person and the agent run the same command: one code path.

## Design the command around the task

- One command per task the agent has, not one per endpoint: `schedule_event`, not `list_users`, `list_events` and `create_event` (Anthropic).
- Return names, not ids; keep ids for a detailed mode, and let the caller pick concise or detailed (one call: 72 tokens concise, 206 detailed) (Anthropic).
- Make the wrong call hard to make: requiring absolute paths ended a whole class of mistakes once the agent left the root folder (Anthropic, SWE-bench).
- Filter and count in code before results reach the model: one workflow fell from 150,000 tokens to 2,000 (Anthropic).
- Cap what comes back, and when you cut, say how to get the rest. Claude Code cuts a tool result at 25,000 tokens by default; a very large result goes to a file, and the agent gets its path.
- Write the help like a brief to a new hire: example calls, edge cases, the formats it takes, every argument with an example value. Rewriting tool descriptions alone cut task time by 40% (Anthropic).

## Hand out one task at a time

When a CLI hands work to an agent, one command returns one task with a clear end: the objective, the files it may write, the shape of the result it must return, and the exact command to run when done (agentplane). A pair of commands can frame the work: what to know before starting, and how to check the result before reporting (ax).

## Done when

The agent picks the right command first on real requests, and its next step is the one the output named: [a run with the tool against a run without it](../../evals/SKILL.md).
