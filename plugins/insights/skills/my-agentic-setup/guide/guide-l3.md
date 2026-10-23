# The level guide — Level 3 (Agentic Engineer)

**What Level 3 is:** The agent knows the right things at the right moment without being told again, and it stays that way as the work grows and splits.

**Its discipline:** Context engineering

**What someone at Level 3 can do:**
- The agent finds its rules without being told.
- A correction sticks: the same mistake doesn’t come back.
- A fresh chat knows what happened yesterday.
- It stays sharp as the work grows.
- It gets what it needs instead of them pasting it.
- It works for the team too, not just for them.

## Where Level 3 starts and ends

- **L2 → L3:** Level 2 fixes it *in the chat*: they re-type the fact, paste the old answer, pick the files by hand every time and keep one long chat. Level 3 makes the fix *last*: the next chat already knows, without them adding anything.
- **L3 → L4:** Level 3 shapes *what the agent reads* and *uses* the tools others built. Level 4 *builds* what changes how the agent runs (hooks, plugins, MCP servers, the Agent SDK, loops) and *proves it with evals the agent can’t edit*. An Engineer uses the safety net; an Architect builds it. For anything someone built, ask: does it change how the agent runs or behaves?

**The fresh-chat test:** **The fresh-chat test makes every row checkable.** “Your work is proven by a fresh Claude session that never saw your conversation.” On a call, ask them to **open a fresh chat now and ask it X.**

## The situations

Each situation is a moment from real work: what goes wrong, what a Level 3 does about it,
and what a Level 2 does instead. `decides` = showing it is what makes someone Level 3;
`carried` = a Level 3 does it too, but it does not make anyone Level 3.

### A. It knows without being told (instructions)

**l3-s-01 — A fact it can never work out alone** (decides)
- Problem: Claude keeps getting a project fact wrong, such as the client’s name or that the staging database is shared with production, so they re-type it in every prompt.
- Level 3: After the first time, they write that one fact by hand into the file Claude always loads.
- Level 2 instead: They type it again in every prompt.
- Ask: What would Claude get wrong in your project if you opened a fresh chat right now? Where does it learn it?

**l3-s-02 — The right rules in the right place** (decides)
- Problem: The work spans several modules, countries, clients or repos, each with different rules. A rule leaks where it doesn’t belong, or everything loads everywhere.
- Level 3: They put each rule in the narrowest place that still reaches where it’s needed: the workspace, the repo, a folder or their personal file.
- Level 2 instead: They keep one file for everything, or tell Claude “this is the Finland module” every time.
- Ask: Show me where a rule that only applies to [Finland] lives, and how Claude knows to read it only there.

**l3-s-03 — Depth on demand** (decides)
- Problem: Procedures and reference docs, such as deploy steps, the test style or Confluence pages, cost tokens in every message, or never get found.
- Level 3: They keep them behind a pointer that says *when* to open them (“Before changing how data is stored, check `docs/decisions/`”), or pack a repeated procedure as a skill.
- Level 2 instead: They paste the doc in when it’s needed, or load everything with `@imports`.
- Ask: Where do your deploy steps live? How does Claude know to read them when, and only when, it deploys?

**l3-s-04 — What earns a slot (staying small as it grows)** (decides)
- Problem: The instruction file grows to hundreds of lines, and Claude starts ignoring parts of it.
- Level 3: They cut what Claude can work out alone, move detail behind pointers, and retire a rule once its cause is fixed.
- Level 2 instead: They let Claude write a file that reads like a README, and never look at it again.
- Ask: When did you last delete a line from your rules file, and why?

### B. It learns from its mistakes (corrections)

**l3-s-05 — The same correction, third time** (decides)
- Problem: They correct Claude on the same thing again and again.
- Level 3: They treat a repeated correction as a configuration bug and put the fix where it belongs: the team’s file, their own untracked `CLAUDE.local.md`, or the code that teaches Claude the bad habit.
- Level 2 instead: They correct it in the chat again.
- Ask: Tell me about the last correction you gave Claude twice. What did you do after the second time?

**l3-s-06 — When a written rule still gets ignored** (decides)
- Problem: The rule is in the file and Claude still breaks it, like a ticket filter that leaks every month.
- Level 3: They treat it as a context problem and find the cause: the rule is ambiguous, two rules contradict each other, the file is too long, the rule is worded as a “don’t”, or messy code keeps leading the agent the other way. Then they fix that cause, which sometimes means refactoring the rules, or the code itself.
- Level 2 instead: 
- Ask: Is there a rule Claude keeps breaking anyway? What did you try?

### C. It remembers (memory across time)

**l3-s-07 — Tomorrow’s chat knows today** (decides)
- Problem: A fresh chat starts from zero: yesterday’s decisions, dead ends and progress are lost.
- Level 3: They have notes that load on their own when a chat starts. Not “I tell it to open the file”, and not “I paste a summary”.
- Level 2 instead: They keep one endless chat, or explain everything again.
- Ask: Open a fresh chat on yesterday’s task right now. What does it know before you type anything?

**l3-s-08 — Memory with a shape they chose** (decides)
- Problem: An agent left to keep its own notes produces a dump nobody can use.
- Level 3: They pick a *kind* of memory on purpose (a notes file, an agent’s `memory: local`, a capture plugin, or their own layer) and design its structure, and the agent fills it in. What counts is who decides the shape, not who types.
- Level 2 instead: They tell it to “keep notes somewhere”, with no structure, or keep nothing.
- Level 4 starts: They *build* memory systems, which the program doesn’t teach.
- Ask: Show me your memory. Which parts did you design, which does Claude write, and why that kind?

**l3-s-09 — Handing a big task across sessions** (decides)
- Problem: A feature spans ten PRs or several days, and each session must pick up where the last one stopped.
- Level 3: They keep a goal or plan file that the next session reads, like a memory for each feature or a handoff note.
- Level 2 instead: They copy the old chat’s summary into the new one.
- Ask: You’re mid-feature and the chat is full. What do you do, and what does the next chat start with?

### D. It stays sharp during the work (attention)

**l3-s-10 — Noticing the chat has gone bad** (carried-from-L2)
- Problem: Long chats drift, mix topics, get compacted and start making things up.
- Level 3: They keep one task per chat, notice drift, and start clean on purpose after saving what matters. They watch how much context is in use, for example in a status line. Or they pick the old chat back up (resume) when it’s still good, or fork it to try a side idea without spoiling it. No hand-written handoff notes.
- Level 2 instead: They keep going, and compact again and again.
- Ask: How do you know a chat has gone bad? What do you do at that moment?

**l3-s-11 — Keeping side work out of the main chat** (carried-from-L2)
- Problem: Digging through code or logs floods the main chat.
- Level 3: They send the digging out of the main chat, and know the two ways: a sub-agent starts blank and needs a short brief; a fork (`/subtask`) already knows the whole chat. Either way the main chat gets only the result.
- Level 2 instead: They ask for “analyze the whole project” in the main chat.
- Level 4 starts: They design multi-agent pipelines and orchestration.
- Ask: When Claude needs to read a lot of code to answer one question, where does that reading happen?

**l3-s-12 — Two pieces of work at once, without them mixing** (carried-from-L2)
- Problem: They need a bug fix while a feature is half-done. Both chats share one folder and step on each other.
- Level 3: They run parallel sessions in separate worktrees, each with its own context, and know which one holds what.
- Level 2 instead: They work in one chat, one thing at a time, or run two chats that edit the same files.
- Ask: Last time you had two tasks going at once, how did you keep Claude’s work on them apart?

### E. It can reach what it needs (tools)

**l3-s-13 — It fetches what it needs instead of them pasting it** (carried-from-L1)
- Problem: Tickets, logs, database rows and docs live outside the code, so they copy them in by hand.
- Level 3: They connect the agent to these sources with tools others built, such as MCP servers and plugins, and let it fetch.
- Level 2 instead: They paste in the ticket text or the log.
- Level 4 starts: They *build* the MCP server or plugin themselves.
- Ask: Where did the information for your last task come from, and how did it reach Claude?

### F. It works for more than them (reach)

**l3-s-14 — A fresh agent works on a new codebase on day one** (decides)
- Problem: A new repo, a new client or a new teammate, and the agent knows nothing.
- Level 3: They set the project up so any fresh session is productive: the essentials, the pointers, and where things live.
- Level 2 instead: 
- Ask: If a new teammate cloned the repo and opened Claude, what would it know?

**l3-s-15 — The team’s agents follow the same rules** (decides)
- Problem: Each engineer’s agent does things differently, and conventions drift.
- Level 3: The rules live in the repo, committed, and teammates actually use them, for example shared ADR files or style guides.
- Level 2 instead: 
- Level 4 starts: They make the dangerous things impossible for everyone with hooks and guardrails, not just written down.
- Ask: Whose Claude besides yours reads your rules?

**l3-s-16 — Proving it works, not just that it exists** (decides)
- Problem: A file exists, but nobody knows whether Claude actually follows it.
- Level 3: They check in a fresh session that the rule, memory or pointer really fires, with `/memory`, `/context` or a test question.
- Level 2 instead: 
- Level 4 starts: They automate that check as evals the agent can’t edit.
- Ask: How do you know Claude actually reads that file?

## Rulings (the owner's decisions; they override any reading above)

- **The Credly Engineer badge promises more than the Level 3 check looks for. Change it?** The badge text stays as it is: it describes everything a Level 3 does, basics included. Credly is not touched.
- **Reach (rows 14–15): Level 3 or Level 4?** Rows 14–15 decide Level 3. The Level 4 line is behaviour set by code (hooks, tooling, plugins), not who the context is for.
- **Session management (rows 10–12): does it decide Level 3?** Rows 10–12 are carried from Level 2 and don’t decide Level 3.
- **Writing skills: Level 3 or Level 4?** Writing skills is Level 3. It no longer counts as an Architect sign: Level 4 is building tools, MCP servers, plugins and hooks, and running evals.
- **A rules file Claude wrote: does it count?** It counts **if supervised**: they set tight rules on what Claude may write there and keep the file from bloating. Ask: “What rules does Claude follow when it writes to your CLAUDE.md, and what stops it from growing?”
- **Pruning (row 4): must a Level 3 show it?** Ask about it on calls; decide later.
- **Using MCP servers (row 13): Level 3 or Level 4?** Row 13 stays in the map as a Level 1 basic that a Level 3 carries. It doesn’t decide Level 3; building the server or plugin is Level 4.
- **Plan first: a Level 3 skill?** Planning first is a Level 2 habit, so it is not in the map.
