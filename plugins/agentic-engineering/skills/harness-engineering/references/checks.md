# Checks: turning a correction into a check or a block

Turning a correction that came twice into a check, or blocking an action that must never happen. Elsewhere:
- [Writing the hook that runs the check](hooks.md)
- [A rule that reaches the agent and is still ignored](audit.md)
- [Putting the rule's text in the right place first](../SKILL.md)

## The question

Ask "what capability is missing, and how do we make it both legible and enforceable for the agent?" (OpenAI), not "what do I tell it?". Fixing the bad output fixes one result; changing what produced it fixes the next ones (Fowler).

## The mechanisms, from weakest to strongest

0. Text in the right place: [which file or hook should hold the line](instruction-surfaces.md).
1. A note injected before the tool call: [context on that call](channels.md), or [in the output of the command it runs](tools.md).
2. A check: a deny, a lint, a test.
3. A design: the mistake cannot be written.

Move up one level on the second mistake of the same kind; the first mistake gets at most level 0. A piece set up "just in case" costs every session and did not work (HumanLayer). OpenAI's version: "When documentation falls short, we promote the rule into code." Done when the next mistake is caught by code, not by a person.

## Pick the check

- Deny (a PreToolUse hook): an action that cannot be undone, or a rule with no exception. The reason names the command to type instead ([writing the deny, and what it does when it errors](hooks.md)).
- Lint: a rule about what the code or a file looks like. Write each message as the fix, since the agent reads it right after the mistake (OpenAI, Fowler). Lint only what was just written, and cap the output.
- Test: a rule that must keep holding. Test the invariant (a boundary, a format, a behaviour someone relies on), not how the code happens to be written today: a test of the implementation locks in whatever was written first and slows every change. Before an agent refactors, write tests that record today's behaviour, bugs included, and fix nothing yet (sawinyh).
- Design: when a kind of mistake keeps happening despite its check, change the code's structure. Layers whose dependency directions a test checks (OpenAI), a type that cannot hold the bad value, one command that does the job correctly, one supported way to do the task with the others deleted.
- Never ask a model to do a linter's job (HumanLayer): what code can check, code checks.

## Where, and how strictly

- Cheap and certain checks first: types, lint and fast tests on every step or before a commit; the full suite before a push; an LLM review last (Fowler, HumanLayer).
- Block what cannot be undone (a push, a delete, a payment, a message sent), and only warn about what is cheap to redo. When agents produce a lot of changes, keep blocking checks few and fix mistakes in the next change; OpenAI says that would be irresponsible where few changes are made. A working principle, not measured (many-hands).

## Print nothing when a check passes, only the failures when it fails

A passing run's output takes 2-3% of the context window and tells the agent nothing; a check mark takes under 10 tokens (HumanLayer). Wrap every check the agent runs:
- On success, one line.
- On failure, only the failures, each with its fix.
- A Stop hook that runs the checks prints nothing when everything passes and exits 2 with the failures when something fails, so the agent keeps working (HumanLayer). It lets the stop through when `stop_hook_active` is set.
- Shorten output in code, never by asking the model what to drop: keep the first and last lines, and give the path to the rest.

## A baseline lint (a ratchet): rules on a repo that already breaks them

1. Count today's violations into a baseline file, one count per rule and file.
2. Fail only when a count goes up; a fix lowers the baseline in the same change.
3. A marker in the code names a deliberate exception, with its reason.
The rule holds for every new line from the first day, and the old code is fixed when it is next changed (sawinyh).

## Loops and repeated failures

- One file edited again and again (10 times or more, seen): a PostToolUse hook counts edits per file and, past a threshold, tells the agent to stop and try a different approach (LangChain).
- Errors in a row: after about 3, stop retrying; start a new session from a written handoff, or ask the person (12-factor agents).
- An impossible task: give the agent an approved way to say "this cannot be done" and stop. Without one it works around the check: rule-breaking on impossible tasks fell from 54% to 9% once there was one (ImpossibleBench, cited second-hand).

## Bad patterns spreading

Agents copy the patterns they see, bad ones included; two hundred examples in the code outweigh one line of text against them. When the code shows the opposite of a rule, change the code or add an example; a new line of text will not outweigh the code. Remove spreading patterns in small, regular cleanups: OpenAI spent a fifth of each week cleaning by hand until a recurring agent did it.

## Boundaries

- A sandbox that isolates both the filesystem and the network is safer than asking before each action, because people stop reading the prompts: sandboxing cut prompts by 84% (Anthropic).
- The danger is untrusted input and a harmful capability in the same session; a model refusing is not a reliable block (OpenHands).
- A skill, plugin, hook or rules file from someone else is untrusted input: read it before installing, and pin its version.

## Done when

The mistake has a check that fails on it (seen failing once), the check's message says the fix, and the rule's text, where still needed, links to the check.
