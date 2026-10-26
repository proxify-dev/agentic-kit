# The level guide — all four levels

Levels build on each other: each one includes the ones below. A level is decided by how the
person works, not by what they own. Level 3 is APPROVED by the owner. Level 1 and Level 2
situations and the Level 4 lines are DRAFTS written from the calls: use them, but weigh the
ladder's own words (what each level can do) and the borders above any single draft row.

## Level 1 — Agentic Explorer

**Typically:** Uses AI coding agents on real work, including tools others connected for it (MCP servers, plugins).

**Discipline:** Using agents

**Can do:**
- Uses an agent on real work, every day.
- Uses tools others set up for it, such as MCP servers and plugins.

## Level 2 — Agentic Practitioner

**Typically:** Gives the AI clear tasks, checks its work and owns the result, and scales it with discipline: parallel tasks in separate worktrees, side digging in sub-agents, a fresh chat when one goes bad.

**Discipline:** Discipline

**Can do:**
- Structures the change before the agent acts.
- Steers it while it works, and stops it when it drifts.
- Checks what comes back, and owns it.
- Runs work in parallel without it mixing: worktrees, sub-agents, a fresh chat when one goes bad.

## Level 3 — Agentic Engineer

**Typically:** The agent knows the right things at the right moment without being told again, and it stays that way as the work grows and splits.

**Discipline:** Context engineering

**Can do:**
- The agent finds its rules without being told.
- A correction sticks: the same mistake doesn’t come back.
- A fresh chat knows what happened yesterday.
- It stays sharp as the work grows.
- It gets what it needs instead of them pasting it.
- It works for the team too, not just for them.

## Level 4 — Agentic Architect

**Typically:** Also builds the tools the AI runs inside, and tests that they keep working.

**Discipline:** Harness and loop engineering

**Can do:**
- Builds what changes how the agent behaves: hooks, plugins, MCP servers, Agent SDK programs.
- Agents run without them typing.
- Proves it works, with evals the agent can’t edit.
- Keeps it safe and cheap, and can see what each run did.

## Borders between levels

- **L2 → L3:** Level 2 *carries the context*: chat by chat they choose what the agent should know, by prompting, pasting, pointing at files, compacting and renaming. They work with discipline, and may have rules files and skills of their own. Level 3 has *engineered* the context: the agent gets what it needs from where they put it, and they can say why it lives there and how they keep it right. Files and skills are not the line; who carries the context is. It is one picture across the Level 3 abilities, never a count of them.
  - Example — Strong Level 2 (an internal colleague): twenty skills of her own shared through her team’s repo, plan mode almost always, chats cleared, compacted and renamed on purpose, a scheduled job that loads data. Still Level 2: she decides chat by chat what is worth keeping, and finds old work by the chats she named. She carries the context.
  - Example — Level 3 (a member the owner approved): his rules are split by module behind an index file, so the agent reads the rules for the part it works in without him saying. Less to show than the Level 2 above, but the setup carries the context, not him.
- **L3 → L4:** Level 3 shapes *what the agent reads* and *uses* the tools others built. Level 4 *builds* what changes how the agent runs (hooks, plugins, MCP servers, the Agent SDK, loops) and *proves it with evals the agent can’t edit*. An Engineer uses the safety net; an Architect builds it. For anything someone built, ask: does it change how the agent runs or behaves?

**The fresh-chat test:** **The fresh-chat test makes every row checkable.** “Your work is proven by a fresh Claude session that never saw your conversation.” On a call, ask them to **open a fresh chat now and ask it X.**

- *Decides Level 3* (Level 3): rows 1–9, 14–16 — Showing these is what makes someone an Agentic Engineer.
- *Carried from Level 2* (Level 3): rows 10–12 — An L3 does them too, but they don’t make anyone L3.
- *Carried from Level 1* (Level 3): rows 13 — Using tools someone else connected is a basic.
- *Decides Level 2* (Level 2): rows 1–12 — Showing these is what makes someone an Agentic Practitioner. Rows 10–12 carry over into Level 3.
- *Carried from Level 1* (Level 2): rows 13 — Using the agent on real work every day is a basic.
- *Decides Level 1* (Level 1): rows 1–3 — Showing these is what makes someone an Agentic Explorer.

## Level 3 situations — A. It knows without being told (instructions)

**l3-s-01 — A fact it can never work out alone** (decides)
- Problem: Claude keeps getting a project fact wrong, such as the client’s name or that the staging database is shared with production, so they re-type it in every prompt.
- Level 2: They type it again in every prompt.
- Level 3: After the first time, they write that one fact by hand into the file Claude always loads.
- Ask: What would Claude get wrong in your project if you opened a fresh chat right now? Where does it learn it?

**l3-s-02 — The right rules in the right place** (decides)
- Problem: The work spans several modules, countries, clients or repos, each with different rules. A rule leaks where it doesn’t belong, or everything loads everywhere.
- Level 2: They keep one file for everything, or tell Claude “this is the Finland module” every time.
- Level 3: They put each rule in the narrowest place that still reaches where it’s needed: the workspace, the repo, a folder or their personal file.
- Ask: Show me where a rule that only applies to [Finland] lives, and how Claude knows to read it only there.

**l3-s-03 — Depth on demand** (decides)
- Problem: Procedures and reference docs, such as deploy steps, the test style or Confluence pages, cost tokens in every message, or never get found.
- Level 2: They paste the doc in when it’s needed, or load everything with `@imports`.
- Level 3: They keep them behind a pointer that says *when* to open them (“Before changing how data is stored, check `docs/decisions/`”), or pack a repeated procedure as a skill.
- Ask: Where do your deploy steps live? How does Claude know to read them when, and only when, it deploys?

**l3-s-04 — What earns a slot (staying small as it grows)** (decides)
- Problem: The instruction file grows to hundreds of lines, and Claude starts ignoring parts of it.
- Level 2: They let Claude write a file that reads like a README, and never look at it again.
- Level 3: They cut what Claude can work out alone, move detail behind pointers, and retire a rule once its cause is fixed.
- Ask: When did you last delete a line from your rules file, and why?

## Level 3 situations — B. It learns from its mistakes (corrections)

**l3-s-05 — The same correction, third time** (decides)
- Problem: They correct Claude on the same thing again and again.
- Level 2: They correct it in the chat again.
- Level 3: They treat a repeated correction as a configuration bug and put the fix where it belongs: the team’s file, their own untracked `CLAUDE.local.md`, or the code that teaches Claude the bad habit.
- Ask: Tell me about the last correction you gave Claude twice. What did you do after the second time?

**l3-s-06 — When a written rule still gets ignored** (decides)
- Problem: The rule is in the file and Claude still breaks it, like a ticket filter that leaks every month.
- Level 3: They treat it as a context problem and find the cause: the rule is ambiguous, two rules contradict each other, the file is too long, the rule is worded as a “don’t”, or messy code keeps leading the agent the other way. Then they fix that cause, which sometimes means refactoring the rules, or the code itself.
- Ask: Is there a rule Claude keeps breaking anyway? What did you try?

## Level 3 situations — C. It remembers (memory across time)

**l3-s-07 — Tomorrow’s chat knows today** (decides)
- Problem: A fresh chat starts from zero: yesterday’s decisions, dead ends and progress are lost.
- Level 2: They keep one endless chat, or explain everything again.
- Level 3: They have notes that load on their own when a chat starts. Not “I tell it to open the file”, and not “I paste a summary”.
- Ask: Open a fresh chat on yesterday’s task right now. What does it know before you type anything?

**l3-s-08 — Memory with a shape they chose** (decides)
- Problem: An agent left to keep its own notes produces a dump nobody can use.
- Level 2: They tell it to “keep notes somewhere”, with no structure, or keep nothing.
- Level 3: They pick a *kind* of memory on purpose (a notes file, an agent’s `memory: local`, a capture plugin, or their own layer) and design its structure, and the agent fills it in. What counts is who decides the shape, not who types.
- Level 4 starts: They *build* memory systems, which the program doesn’t teach.
- Ask: Show me your memory. Which parts did you design, which does Claude write, and why that kind?

**l3-s-09 — Handing a big task across sessions** (decides)
- Problem: A feature spans ten PRs or several days, and each session must pick up where the last one stopped.
- Level 2: They copy the old chat’s summary into the new one.
- Level 3: They keep a goal or plan file that the next session reads, like a memory for each feature or a handoff note.
- Ask: You’re mid-feature and the chat is full. What do you do, and what does the next chat start with?

## Level 3 situations — D. It stays sharp during the work (attention)

**l3-s-10 — Noticing the chat has gone bad** (carried-from-L2)
- Problem: Long chats drift, mix topics, get compacted and start making things up.
- Level 2: They keep going, and compact again and again.
- Level 3: They keep one task per chat, notice drift, and start clean on purpose after saving what matters. They watch how much context is in use, for example in a status line. Or they pick the old chat back up (resume) when it’s still good, or fork it to try a side idea without spoiling it. No hand-written handoff notes.
- Ask: How do you know a chat has gone bad? What do you do at that moment?

**l3-s-11 — Keeping side work out of the main chat** (carried-from-L2)
- Problem: Digging through code or logs floods the main chat.
- Level 2: They ask for “analyze the whole project” in the main chat.
- Level 3: They send the digging out of the main chat, and know the two ways: a sub-agent starts blank and needs a short brief; a fork (`/subtask`) already knows the whole chat. Either way the main chat gets only the result.
- Level 4 starts: They design multi-agent pipelines and orchestration.
- Ask: When Claude needs to read a lot of code to answer one question, where does that reading happen?

**l3-s-12 — Two pieces of work at once, without them mixing** (carried-from-L2)
- Problem: They need a bug fix while a feature is half-done. Both chats share one folder and step on each other.
- Level 2: They work in one chat, one thing at a time, or run two chats that edit the same files.
- Level 3: They run parallel sessions in separate worktrees, each with its own context, and know which one holds what.
- Ask: Last time you had two tasks going at once, how did you keep Claude’s work on them apart?

## Level 3 situations — E. It can reach what it needs (tools)

**l3-s-13 — It fetches what it needs instead of them pasting it** (carried-from-L1)
- Problem: Tickets, logs, database rows and docs live outside the code, so they copy them in by hand.
- Level 2: They paste in the ticket text or the log.
- Level 3: They connect the agent to these sources with tools others built, such as MCP servers and plugins, and let it fetch.
- Level 4 starts: They *build* the MCP server or plugin themselves.
- Ask: Where did the information for your last task come from, and how did it reach Claude?

## Level 3 situations — F. It works for more than them (reach)

**l3-s-14 — A fresh agent works on a new codebase on day one** (decides)
- Problem: A new repo, a new client or a new teammate, and the agent knows nothing.
- Level 3: They set the project up so any fresh session is productive: the essentials, the pointers, and where things live.
- Ask: If a new teammate cloned the repo and opened Claude, what would it know?

**l3-s-15 — The team’s agents follow the same rules** (decides)
- Problem: Each engineer’s agent does things differently, and conventions drift.
- Level 3: The rules live in the repo, committed, and teammates actually use them, for example shared ADR files or style guides.
- Level 4 starts: They make the dangerous things impossible for everyone with hooks and guardrails, not just written down.
- Ask: Whose Claude besides yours reads your rules?

**l3-s-16 — Proving it works, not just that it exists** (decides)
- Problem: A file exists, but nobody knows whether Claude actually follows it.
- Level 3: They check in a fresh session that the rule, memory or pointer really fires, with `/memory`, `/context` or a test question.
- Level 4 starts: They automate that check as evals the agent can’t edit.
- Ask: How do you know Claude actually reads that file?

## Level 2 situations — A. Before it starts (structures the change) (DRAFT)

**l2-s-01 — A clear task, not a wish** (decides)
- Problem: A one-line wish, like “fix the login” or “split the monolith”, comes back as one huge change that does several things, some of them wrong, and nobody can check it.
- Level 1: They type the wish, then ask how to check the result.
- Level 2: They structure the change before handing it over: one bounded task, where it lives (file, method, endpoint, table), what done looks like, and what it must not touch. When it’s too big for one go, they split it, prove the pattern on one piece, then do the rest.
- Level 3 starts: The agent already knows where things live, so they stop typing the folder every time.
- Ask: Read me the last task you gave Claude, word for word. How did it know where to work, and when it was done?

**l2-s-02 — Thinking it through before the agent touches code** (decides)
- Problem: On a schema change or a feature that crosses services, the agent goes straight to code, and the wrong approach only shows up in a big diff.
- Level 1: They say “fix the login” and let it go, or skim the plan it wrote and say go.
- Level 2: They work the idea through with the agent in the chat until every end is covered, and only then let it change code. Plan mode helps, but isn’t needed. They skip it for a one-line fix.
- Level 3 starts: The plan is a file the next session picks up.
- Ask: On your last bigger change, what did you and Claude settle in the chat before it wrote any code?

**l2-s-03 — Show it the real thing, not a description** (decides)
- Problem: The bug reaches the agent as a sentence, like “the queue is slow”. The agent guesses, confidently, and the guess gets built.
- Level 1: They describe the problem in words and act on the first answer.
- Level 2: They put the real evidence in front of it: the stack trace, the failing output, the payload, the query, the screenshot, the files involved. They ask it for evidence back, not a theory. Pasting it or letting the agent fetch it counts the same: what matters is that the agent saw the real thing.
- Level 3 starts: The agent knows where to look on its own, so nobody has to hand it over.
- Ask: Tell me about the last bug you handed Claude. What did it actually see: your description, or the real error?

**l2-s-04 — Code they don’t know yet** (decides)
- Problem: A new repo, a new client, or a corner they’ve never touched. Asking for a change straight away gets a change that fits nothing around it.
- Level 1: They ask for the change and trust it, or do all the reading by hand and give the agent only the files they found.
- Level 2: They have the agent explain it first, in a chat of its own. They ask how things work and where they live, check the answers against the code, and only then ask for a change. The agent onboards them.
- Level 3 starts: They onboard the agent: what it learned is written down, so the next fresh chat starts knowing.
- Ask: Tell me about the last time you worked in code you didn’t know. What did you ask Claude before you let it change anything?

## Level 2 situations — B. While it works (steers it) (DRAFT)

**l2-s-05 — Stopping it when it goes off track** (decides)
- Problem: On a multi-step job the agent takes a wrong turn early and keeps building on it. By the end, the whole change rests on the mistake.
- Level 1: They let it run and look at the end.
- Level 2: They watch it work and check each step before the next. They stop it the moment it goes sideways and redirect it. They keep the steps small when the task is risky.
- Level 4 starts: The run checks itself against a test it can’t edit, so nobody has to watch.
- Ask: Tell me about the last time you stopped Claude in the middle of a task. What did you see, and what did you tell it?

**l2-s-06 — When the result comes back wrong** (decides)
- Problem: The output is wrong, or not what they meant.
- Level 1: They regenerate until it looks right, say “it hallucinated”, or quietly patch it by hand.
- Level 2: They name exactly what is wrong and what they didn’t tell it, then change the ask or send it back with the defect named. They don’t roll again hoping.
- Level 3 starts: The second time the same correction comes, it becomes a lasting fix.
- Ask: Tell me about the last time Claude gave you something wrong. What exactly was wrong, and what did you change the second time?

## Level 2 situations — C. Before it ships (checks and owns it) (DRAFT)

**l2-s-07 — Checking it as their own code** (decides)
- Problem: Claude says it’s done, and it works when clicked once. Underneath, it touched files it shouldn’t have, or the fix doesn’t fix the cause.
- Level 1: They check that the results look right and commit.
- Level 2: They read the actual diff, run it the way a user would, and can explain any part of it. If they can’t explain it, it doesn’t ship.
- Level 4 starts: The check runs by itself, as evals the agent can’t edit.
- Ask: Show me the diff of the last change Claude made that you shipped. What did you check before it went out? Pick one function: what does it do?

**l2-s-08 — “Done, all tests pass”, and it isn’t** (decides)
- Problem: The agent reports green. It got there by deleting the failing test, testing the wrong layer, mocking the thing under test, or never running anything.
- Level 1: They take the summary’s word, or ask the same chat “does this look good?”.
- Level 2: They ask for proof they can see: a test that fails before and passes after, the actual run. They read what the tests check, spot the cut corner, and send it back. They use a second look, such as a fresh chat or the team’s review bot, and judge each comment themselves.
- Level 4 starts: They build the check: evals the agent can’t edit, a reviewer pipeline, a stop hook.
- Ask: Claude says “done, all tests pass”. What do you look at before you believe it? Tell me about a time it wasn’t true.

**l2-s-09 — What the agent doesn’t get to do** (decides)
- Problem: The agent can reach the database, a shared environment, a deploy or a push, and one wrong guess lands on something that matters. Some code, like auth, money or migrations, is too costly to get subtly wrong.
- Level 1: They give the agent the same reach for everything, or auto-approve to go faster.
- Level 2: They decide on purpose what the agent may touch and what they do by hand: no direct database access, no auto-approve on anything shared. They write the critical part themselves, or read it line by line.
- Level 4 starts: They build the guardrail so nobody’s agent can do it.
- Ask: What won’t you let Claude do on its own, and why? Tell me about the last time it asked for something you said no to.

## Level 2 situations — D. More than one thing going on (runs work in parallel) (DRAFT)

**l2-s-10 — Noticing the chat has gone bad** (decides)
- Problem: Long chats drift, mix topics, get compacted and start making things up.
- Level 1: They keep going, and compact again and again.
- Level 2: They keep one task per chat, notice drift, and start clean on purpose after saving what matters. They watch how much context is in use, for example in a status line. Or they pick the old chat back up (resume) when it’s still good, or fork it to try a side idea without spoiling it. No hand-written handoff notes.
- Level 3 starts: Nothing to carry over by hand: what matters is already saved where the fresh chat reads it.
- Ask: How do you know a chat has gone bad? What do you do at that moment?

**l2-s-11 — Keeping side work out of the main chat** (decides)
- Problem: Digging through code or logs floods the main chat.
- Level 1: They ask for “analyze the whole project” in the main chat.
- Level 2: They send the digging out of the main chat, and know the two ways: a sub-agent starts blank and needs a short brief; a fork (`/subtask`) already knows the whole chat. Either way the main chat gets only the result.
- Level 4 starts: They design multi-agent pipelines and orchestration.
- Ask: When Claude needs to read a lot of code to answer one question, where does that reading happen?

**l2-s-12 — Two pieces of work at once, without them mixing** (decides)
- Problem: They need a bug fix while a feature is half-done. Both chats share one folder and step on each other.
- Level 1: They work in one chat, one thing at a time, or run two chats that edit the same files.
- Level 2: They run parallel sessions in separate worktrees, each with its own context, and know which one holds what.
- Ask: Last time you had two tasks going at once, how did you keep Claude’s work on them apart?

## Level 2 situations — E. The basics (carried from Level 1) (DRAFT)

**l2-s-13 — The agent does real work, every day** (carried-from-L1)
- Problem: The agent is something they open now and then, and code goes back and forth between a chat window and the editor.
- Level 2: The agent runs where the code is, in the terminal or the editor, on real tickets, every day, with the tools others connected for it.
- Ask: Walk me through yesterday: which tasks went through the agent, and where did it run?

## Level 1 situations — A. It does real work (every day) (DRAFT)

**l1-s-01 — Real tickets, every day** (decides)
- Problem: The agent is something they open now and then, for a snippet or a question, and most of the work happens without it.
- Level 1: The agent works on their real tickets, every day, and they can say which tasks went through it yesterday.
- Below Level 1: They tried it a few times, or use it only for autocomplete.
- Level 2 starts: They give it one bounded task at a time and check what comes back as their own code.
- Ask: Walk me through yesterday: which tasks went through the agent, and where did it run?

**l1-s-02 — It changes the code, not just answers questions** (decides)
- Problem: They ask in a chat window and copy pieces of the answer into the editor by hand.
- Level 1: The agent runs where the code is, in the terminal or the editor, and changes the files in the project itself. They look at what it changed.
- Below Level 1: A chat window used like a search engine, with the code copied back and forth.
- Level 2 starts: They decide the task before it starts, and steer it while it works.
- Ask: When the agent writes code, where does it end up? Does it change your files, or do you copy it over?

## Level 1 situations — B. It uses tools others set up (DRAFT)

**l1-s-03 — It fetches what it needs instead of them pasting it** (decides)
- Problem: Tickets, logs, database rows and docs live outside the code, so they copy them in by hand.
- Level 1: They use the connections someone else set up, such as an MCP server, a plugin or a command-line tool the agent can run, and let the agent fetch.
- Level 4 starts: They *build* the MCP server or plugin themselves.
- Ask: Where did the information for your last task come from, and how did it reach Claude?

## Not in the map (the owner's clarifications)

- **Pointing Claude at the right file** is Level 2, like a member telling Copilot which file to read.
- **Planning first** is a Level 2 habit, not a Level 3 skill.
- **Having a CLAUDE.md** is not Level 3 on its own. A team’s file counts only if they can say what it does and how.
- **Using** an MCP server or plugin someone else built is a **Level 1 basic**. **Writing skills** is **Level 3 work** (part of the picture, not a ticket into it). **Building** MCP servers, plugins and hooks is **Level 4**, and so are Agent SDK programs, evals, loops, tracing, guardrails and cost limits.

## Rulings (the owner's decisions; they override any reading above)

- **The Credly Engineer badge promises more than the Level 3 check looks for. Change it?** The badge text stays as it is: it describes everything a Level 3 does, basics included. Credly is not touched.
- **Reach (rows 14–15): Level 3 or Level 4?** Rows 14–15 decide Level 3. The Level 4 line is behaviour set by code (hooks, tooling, plugins), not who the context is for.
- **Session management (rows 10–12): does it decide Level 3?** Rows 10–12 are carried from Level 2 and don’t decide Level 3.
- **Writing skills: Level 3 or Level 4?** Writing skills is Level 3 work, not Level 4: it no longer counts as an Architect sign (Level 4 is building tools, MCP servers, plugins and hooks, and running evals). It is part of the Level 3 picture, not a ticket into it: skills on top of someone who still carries the context are Level 2 (the L2 → L3 border).
- **A rules file Claude wrote: does it count?** It counts **if supervised**: they set tight rules on what Claude may write there and keep the file from bloating. Ask: “What rules does Claude follow when it writes to your CLAUDE.md, and what stops it from growing?”
- **Pruning (row 4): must a Level 3 show it?** Ask about it on calls; decide later.
- **Using MCP servers (row 13): Level 3 or Level 4?** Row 13 stays in the map as a Level 1 basic that a Level 3 carries. It doesn’t decide Level 3; building the server or plugin is Level 4.
- **Plan first: a Level 3 skill?** Planning first is a Level 2 habit, so it is not in the map.
- **Side projects: do they count toward the level?** Where someone uses agents doesn’t matter. Work on their own or side projects counts exactly like client work, for every level, including “real work, every day” at Level 1. A page may say where it was shown; it never lowers the level for it.
- **A knowledge base of general best practices (e.g. Obsidian notes the agent searches): is it context engineering, or only project knowledge?** Context is any knowledge the agent works from, not only facts about the project: conventions, best practices they follow, examples, procedures. A knowledge store whose shape they chose, which the agent is set up to search on its own (“use the vault as your main source of truth, search there first”), is engineered context, whatever it holds. Telling the agent once where its knowledge lives is setting it up; pointing it at a file in every chat is Level 2.
- **Dynamic workflows (ultracode, the Workflow tool): which level?** Running Claude Code’s dynamic workflows (ultracode) is a Level 2 habit: a built-in way to run work in parallel, like sub-agents and worktrees. Using it is Level 2; it is not a Level 4 sign. Building your own orchestration program that starts agents is still Level 4.
