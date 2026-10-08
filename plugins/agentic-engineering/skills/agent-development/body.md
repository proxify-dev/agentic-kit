# Writing an agent's body

The body is everything after the frontmatter: the agent's system prompt. Elsewhere:
- [Choosing the words of each line: plain names, steps then reference, the check before a text is done](../context-engineering/SKILL.md)
- [Finding what else a subagent starts with: CLAUDE.md, preloaded skills, the brief](../harness-engineering/references/channels.md)
- [Copying a format that worked: a memory file, files to read first, one stage per agent](./patterns.md)

The body replaces Claude Code's system prompt, so a subagent gets neither Claude Code's own instructions nor the person's output style. A rule from either that the agent needs goes in its body.

## Cutting it

Read the body one line at a time and ask: would another Claude, given the description, the brief (the prompt Claude starts it with) and the project's CLAUDE.md, act differently without this line? If not, cut it.

Keep what is true of this role in this project; cut what is true of Claude in general (reasoning step by step, citing evidence, writing a clear answer). What usually stays:
- The situation the agent works in, rather than a numbered routine: "the person is still choosing the design, expects to be wrong and to revise" covers cases a list of rules would miss.
- Only the decisions that are not the agent's job. Do not fix in the body what the role exists to decide: an agent that designs a memory layout gets no prescribed layout.
- The person's preferences, written as theirs ("the person prefers short reviews"), not as your opinion stated as a rule.
- A limit only when the agent would plausibly cross it without one: "do not edit the project's source files" stays, "do not keep a transcript" goes.
- Paths and names the agent cannot find by looking, such as where its memory directory is.
- A concrete example instead of a definition: the questions the agent will face ("does a missing test block the review, or only get a note?") show the job faster than a definition of it.
