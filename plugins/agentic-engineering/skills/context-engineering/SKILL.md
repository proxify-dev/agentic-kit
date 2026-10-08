---
name: context-engineering
description: Deciding what an agent's context window holds, when, and in what words. Use when writing .md files for agents - a skill, AGENTS.md, a hook's message or a prompt, or a brief for a subagent. 
---

What an agent's context window holds, when each text gets there, and in what words.

## What are you trying to do?

- Writing or editing a skill, a reference, CLAUDE.md or a hook's text: § Order inside a text and § Use the right wording, then § Before a text is done
- Linking one file to another: § Context pointers and § Orientation
- Writing a brief for a subagent: the last paragraph of § The context window
- Shortening a text that has grown too long or repeats itself: § Pruning
- Deciding where a rule goes (a hook, CLAUDE.md, a skill) rather than its words: [the harness-engineering skill](../harness-engineering/SKILL.md)
- Deciding whether something should be a skill at all: [the skill-development skill](../skill-development/SKILL.md)

## The context window

An agent knows only what is in its context window when it acts. It starts with no memory of the conversation that wrote its instructions. It reads everything in the context window and does what the words say, not what their author meant. What is neither loaded nor linked does not exist for it, or gets discovered by accident.

The agent's attention is limited, and every line in the context window competes with every other. So a new line costs more than its own tokens: it makes the agent follow every other line a little less reliably. Add one rule to a file of two hundred, and the agent follows all two hundred a little worse. Your text also shares the context window with the system prompt, other instruction files, skill descriptions, hook messages, tool results and the code the agent has opened. The agent usually follows the code: two hundred examples in the code outweigh one sentence that says otherwise. When a rule fights the codebase, change the code or show an example.

Context engineering is deciding what the context window holds, when it gets there, and in what words: the fewest lines that make the agent follow the same process every run. Every section below follows from that. Whether a text works is a fact about the model, not about your prose, so when in doubt, run it.

## Progressive disclosure

The agent fills its context window one tool call at a time. It starts with what is always loaded: the system prompt, the `CLAUDE.md` chain, each skill's name and description, and anything `@`-imported, all sent with every request. When a file it has read links to another, it can follow the link with `Read(path)`, and that file comes back as a tool result. Your docs are files linked to each other, and the agent reads them one `Read` at a time, deciding each time from the link text alone.

**Progressive disclosure** is designing for that. Each file says just enough for the agent to decide whether to follow a link further, and each link narrows the scope: from the root file, to a folder's file, to the one reference the task needs. A linked file costs only its link line until it is read, so detail belongs behind links. The always-loaded root keeps two kinds of line: one that tells the agent when to open something, and a rule it must follow before it could have opened anything. Place a fact by how often it is needed, not by how important it feels. Every line in the root must also matter for every task: Claude Code loads `CLAUDE.md` into every session in its folder, whatever the task, so a line that fits only one task is noise in every other session and teaches the agent to skim the whole file. A rule that must hold every time is cheaper as a check than as words, because a hook or a test takes no place in the context window: [placing a rule that must always hold](../harness-engineering/SKILL.md).

**Cross-references** make sure the agent reaches the fact it needs. The agent meets a need in whatever file it is reading: the root, a skill, a hook's message, a file it opened for another reason, a grep hit. From there it can follow only the links that file contains. A fact linked from one file cannot be reached from any other, so the agent never reads it, and either reinvents it or goes on without it. Link a fact from every file where the need for it comes up, even if the same link then appears in many files. That is not duplication: the content still lives in one file, and each extra link costs one line. To check, trace the `Read` path to the fact from every file where an agent could start that task, and wherever a file has no link onward, add one. A file nothing links to depends on a person remembering it exists and sending the agent there.

The context window also changes during a session. Load material when it is needed (a link, a path, a command that finds it) rather than all up front. A large tool result stays in the context window and competes with your instructions like any other text. Long sessions get summarised, and what only the conversation held may be lost, so what must last goes in a file. A **subagent** starts with an empty context window: give it the long searches whose output you do not need, and only its answer comes back. Its brief is all it knows, since it has none of your conversation, so the brief carries the goal, what is already known, when it is done and what shape the answer takes. When the context window is already full of output, more text does not help, since it competes too: start a new session from a written hand-off.


## Context pointers

A **context pointer** is a line in the context window that names something outside it, like a line `AGENTS.md` linking to a doc. Whether the agent `Read`s the target depends on what the pointer's intent transmits. Pointers need to transmit intent:

```markdown
See [adding migrations](./migrations.md)
```
A pointer says what the target is and names each separate case that should open it, one trigger per case. Put the key word first, and place the pointer next to each decision that creates the need; a "see also" list at the end of a file is a catalogue, not a route. A pointer in an always-loaded file is sent with every request, so cut it hardest.

The link text is the task the agent will be doing, "adding migrations" rather than just "migrations", and the path works as written with `Read(path)`. This skill's own description is a pointer too.

## Orientation

An agent rarely starts reading at the top of the docs. It opens a file partway down: from a link in another file, a grep hit, a path in an error or a hook's message, a brief that names a file. It arrives with a task but can see only the files it has read, so it does not know which files sit beside this one, what lies further down the links, or whether this is even the right file. An agent in the wrong file tends to stay and work with whatever is in front of it. So every document an agent can open first starts by orienting it: one line on what the file is for, then links onward, each labelled with a task the reader might have.

```markdown
- [Adding a migration](./adding.md)
- [A migration failed on deploy](./rollback.md)
- [Only querying orders](../queries/README.md)
```

These few lines load nothing extra, yet the agent now knows which file it is in, what exists next to it, and which link its task follows. The heading asks about the reader's task, not the file's contents. A table of contents makes the agent translate its task into your topics; the question makes it name its task and pick the matching link. Among the links in a file, the agent `Read`s the one whose text is closest to its task, so write each link text as the task itself, in the words the agent will be thinking. The last link is the way out: an agent that opened the wrong file makes one extra `Read` instead of doing the whole task in the wrong place. The same block belongs in every file an agent may open first, sized to that file: the root, a folder's README, a reference, a skill with several uses, a heading in a long file where a grep hit or a `Read` with an offset starts halfway down. This is progressive disclosure, seen from inside the file the agent just read.


## Order inside a text

Once a text is in the context window, its layout decides what the agent pays attention to. Content is either **steps**, what the agent does in order, or **reference**, rules and facts it looks up when needed. Steps come first and reference after. The test for moving something out: keep inline what every case needs, and move what only some cases reach to its own file behind a link. Reference mixed in among steps buries them, and whether the agent notices a step becomes luck. Keep one idea's definition, rules and exceptions under one heading, so reading one part brings the rest.

Two kinds of text follow their own rules. A record of current work, what is in progress and what is still open, is rewritten to the present rather than appended to: a line goes once its work is done, and whatever outlives the work moves to its own file. A finished report that a person reads once can use tables and headings, because a person scans. Everything an agent rereads is read in full on every load, so layout meant for a human eye is weight it carries for nothing.

## When a step is done

Every step ends on a **done-condition**: what tells the agent the step is finished. A vague one ("once you understand the module") makes the agent stop early, to get to the steps it can see are still waiting. A demanding one ("every changed model accounted for") makes it do the full job without you listing the work, and "every rule applied" does the same for a list of rules. The best done-conditions can be checked and cover everything. Sharpen a vague one first. If the agent still rushes, move the later steps out of its context window, into a subagent or a hand-off; calling them inline keeps them in view.

## Use the right wording

Name the real thing, in the words of its tool or its docs: `Read`, `file_path`, `Grep`, a tool result, a `PreToolUse` hook, a subagent, a test. The model knows these words from training and from its own context window, where its system prompt and tool definitions list them on every request, so a sentence written in them points straight at a call it can make. "Follow the link with `Read(path)`" names the call; "explore the docs" leaves it to chance, and each run guesses differently.

A word the model knows helps only when it means the thing literally. A metaphor ("door" for a tool, "eyes" for verification, "rung" for a level, a text that "lands") makes the reader guess what it stands for. Use the established technical term, not a phrase that describes it: "state", not "saved progress". An everyday word given a meaning of your own ("worker" for a subagent, "start line" for what a SessionStart hook injects, "weight" for how strictly a rule must hold) is an invented word: use the real name, or define it in the first file where a reader can meet it. The same word used across your prompts, docs and code helps the agent connect them, so look for repeated explanations one word could replace.

Every command you write is one the reader can run with its own tools. A slash command (`/hooks`, `/plugin`) is typed by the person: give the agent the file or CLI command that shows the same thing, or tell it to ask the person to run the slash command.

Prefer writing what to do, not what to avoid. Keep a ban only as a hard guardrail, paired with what to do instead. Tie the instruction to the reader's own action.

Capitals, IMPORTANT and exclamation marks take attention from every other line, and the agent overreacts, applying the shouted rule where it does not fit. Write the line plainly and tie it to its moment. If plain words do not hold, the rule needs a check, not more volume.

## Pruning

Each idea lives in one file, with as many links to it as there are paths that need it. Before writing a fact, search the folder for it with `Grep`; if a file already holds it, link to that file. A copy goes out of date, and the agent follows whichever copy it reads first; a link cannot go out of date. The repo is a source of truth too: scripts, config, `--help`, the folder layout. A line that restates it is a copy, and earns its place only for what looking cannot find: an unwritten convention, the reason behind a choice, a trap no config reveals. A line the model would follow anyway is a no-op: keep a line only if it changes what the agent does, and delete one that changes nothing whole rather than trimming it. Stale lines pile up, because adding feels safe and deleting feels risky. A text can also be too long even when every line is useful; the fix is moving reference behind links. Short is not vague: a line cut down until nobody can act on it fails as badly as a line that is too long. Add lines for failures you have actually seen, and write the general rule behind each one. To check, swap the incident's specific names for placeholders; if the rule stops making sense, it was a patch.

## Before a text is done

Read the text as an agent that has only this file and the files it links to. Check every line:
- Every term is a name the model already knows (a tool, a file, a hook event, a command) or is defined in the first file a reader can meet it in. No metaphor: "hook", not "door".
- Every pointer is a markdown link whose text is the reader's task.
- Every command is one the agent can run; a slash command is the person's.
- The file opens with what it is for and links named by the reader's task, then steps, then reference.
- Every fact is in one file: `Grep` the folder for it, and link to the file that already holds it.
- Every claim about a tool or a system matches its docs, or says it was seen and on which version.
- Nothing the agent would not act on: no source names, dates, URLs or stories. Those go in the commit.
Then hand the files to a subagent that has not seen your conversation, with two or three tasks a person would bring, and fix every sentence it could not act on. Done when every line passes and the subagent reaches the right file for each task.
