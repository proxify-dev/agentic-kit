# Agent patterns

Formats to copy into an agent's body for repeated or multi-agent work: when to use each, what to write, and why it works. Elsewhere:
- [Choosing whether an agent needs memory, and its scope](./memory.md)
- [Cutting an agent's body](./body.md)

## A memory file of what was tried, learned and comes next

Use it when the agent has `memory:` set and works against a verdict it does not produce (an eval harness, a leaderboard, a test gate). Leave it out for an agent with no memory or one that runs once.

Keep it in its own file in the memory directory, such as `experiments.md`, with one line in `MEMORY.md` pointing to it: `MEMORY.md` is the index Claude Code loads, and a table that grows every run would pass its limit ([memory.md](./memory.md)). An example for an agent that tunes a review prompt against an eval:

```markdown
## Tried
| exp | target | verdict |
|---|---|---|
| 001-negative-example | false positives on the eval set ≤ 5% | pending |
| 002-shorter-checklist | eval pass rate ≥ 90% | green |

## Learned
Most false positives are style comments on generated files.
A shorter checklist kept the pass rate; a longer one lowered it.

## Next
If 001 turns green → check the false negatives that remain.
If 001 stays red → add generated files to the list of paths to skip.
```

In the body, give the order of one run: read `experiments.md`, read the verdict, propose one change, update `experiments.md`, stop.

Why it works:
- One change per run: each row under Tried is one experiment with one verdict, where free-form notes mix several.
- Each line under Next is a condition and an action, so the next run starts from its first action without rereading the history.
- The Tried table has the same columns as the verdict source, so the agent compares the two row by row.

## Files to read before the first output

Use it when earlier runs of the agent failed in a known way (a review that flagged style instead of bugs, a summary that reworded instead of quoting), and adding rules to the body did not stop it.

Write a numbered list at the top of the body:

```
Before you write anything, read these, in order:
1. CONTRIBUTING.md (the review rules)
2. the example review at docs/review-example.md
3. your previous report at reviews/<latest>.md
Then start the review.
```

Why it works: a correct example and the previous output show the agent what a right answer looks like and what was already produced; a rule only describes it.

## One stage per agent

Use it when several agents form a pipeline and each owns one step.

Write the stage it owns, what it does, and what belongs to other stages, each paired with what to do instead:

```
You own the REVIEW stage.

You do:
- Read the diff and the lint report
- Write one finding per problem to reviews/<pr>.md
- Mark each finding blocking or not

Other stages own:
- Fixing the code: describe the fix in the finding; the FIX stage applies it
- Running the tests: name the test to run; the TEST stage runs it
- Messages to other agents: put what they need in your report
```

Why it works: an agent that can edit files and gets stuck edits another stage's files unless its body says which work is not its own and what to do instead.

## Fixed-order steps in XML tags

Use it when skipping or reordering a step is the failure: a migration, a deploy runbook, a security checklist. Leave it out where the path depends on what the agent finds (exploring, debugging, research).

```markdown
## Role
...

## Procedure
<procedure>
  <step>Read the target file in full.</step>
  <step>
    List the changes it needs.
    <if condition="it uses a deprecated pattern">
      Replace it with the current equivalent, changing as few lines as possible.
    </if>
  </step>
  <step>Apply the changes and run the checks.</step>
</procedure>

<prose for the cases the steps cannot list>
```

Decide part by part, not once for the agent: does this part have a fixed order where a skipped step is a failure? Yes: steps in tags, with an `<if>` inside the step where it is checked. No: prose. One body can hold both.

What it costs:
- The tags do not make a condition exact: the model still decides whether `<if condition="…">` holds. They ask for the steps as written, so leave them out of an agent whose job is judgement.
- An attribute the position already gives, such as `<step number="1">`, adds tokens and nothing else: leave it out.
- Tags cost more tokens than a markdown list. Use them only when the fixed order is the point.
