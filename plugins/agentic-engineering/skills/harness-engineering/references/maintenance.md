# Maintenance: measuring pieces and removing the ones that no longer help

Deciding whether to add a piece, and whether one still helps after a model change or when it seems unused. Elsewhere:
- [Running the with-and-without comparison](../../evals/SKILL.md)
- [Deciding whether it should be a skill at all](../../skill-development/SKILL.md)
- Back to [placing a rule](../SKILL.md)

## Every piece stops being needed at some point

"Every component in a harness encodes an assumption about what the model can't do on its own" (Anthropic). When the model improves, the assumption stops being true: Anthropic dropped sprints on Opus 4.6 and context resets on Opus 4.5. A workaround forced on a newer model can lower its results (awesome-agent-architecture). Write down with each piece what would make it unnecessary: the mistake it prevents, and the eval case that shows the mistake.

## Before adding

A piece is worth adding when one of these is true (codex-howto):
- a fixed sequence of steps whose omission caused failures you measured
- knowledge of this repo, its policy or its tools that the model cannot find
- a safety boundary
- a script that does the job deterministically
and the mistake has happened twice. Otherwise, add nothing.

## Count real use

- A skill's real loads are Skill tool calls in the transcripts, not its line in the skill listing. Seen here: a skill that relied only on its description to be loaded had 0 loads in 178,046 recorded tool calls.
- When a hook fired, and what it injected, are hook records in the transcripts.
- With the kit CLI: `ak trace --tool Skill --q <name>`, `ak plugin inspect <plugin>`.
- A check that never fails either has had nothing to catch or is not checking anything: break its rule once on purpose and confirm it fails.

## Three levels of proof

Installed (the file is there) is not the same as fired (it ran when expected), and fired is not the same as followed (the agent's behaviour changed): "a configured asset proves a mechanism exists; only linked task evidence can establish that it was used" (Better Harness). Missing data is unknown, never zero (agentplane).

## On a model change

Remove one piece at a time and run the evals; keep what the results still need, delete the rest (Anthropic, codex-howto). Also compare against no skill at all: one skill took a task from 0/10 to 10/10 while others made results worse (OpenHands), and a shorter rewrite of one skill used 31-54% fewer tokens (codex-howto).

## Delete

- What never fires, what contradicts how the work is done now, what belonged to a finished experiment.
- A whole line rather than a shortened version nobody can act on.
- The eval case that tests a deleted piece, in the same change, with the reason in the run log.

## Spreading patterns

Agents copy what they see, so outdated and bad patterns spread all the time. Look for them on a schedule: outdated docs, dead code, repeated rules, broken links (OpenAI). [A baseline lint's count only goes down](checks.md).
