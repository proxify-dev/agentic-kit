---
name: fewer-faster-tests
description: "Auditing a project's test suite for agent-driven development: how long checking one change takes, which tests can fail for a reason and which cannot, what is missing as well as what is extra, whether a change runs only the tests it affects, and whether the agent can run the app and see the result. Lays out options with their trade-offs for the person to choose; changes no file. Use when tests or CI are slow, an agent keeps adding tests, before removing tests, when deciding how agents should test a project, or when asked to audit the tests or speed up CI."
argument-hint: "[repo path]"
disable-model-invocation: true
---

Reading a project's tests and how they run, then laying out options (remove, merge, strengthen, keep, add, fix, speed up), each with what it gains and what it gives up. The person chooses. The audit changes no file: it deletes, skips and rewrites nothing.

The repo to audit: $ARGUMENTS (empty means the current directory).

## The trade-offs the audit weighs

The goal is a suite where every test can fail for a reason and checking one change stays fast. Fewer tests is not the goal, and neither is more: the project's situation (step 1) decides which way each trade-off leans.

| trade-off | leans to the first when | leans to the second when |
|---|---|---|
| spike ↔ kept test | exploring an idea, new UI, a one-off question | a bug fix (start from a test that fails on the bug), a path an unattended agent will change again |
| entry-point test ↔ unit test | behaviour a user or caller gets: a route, a command, a page | pure logic with real inputs (pricing, parsing, permissions), a library's public API |
| fewer ↔ more | tests that cannot fail, or that mock the project's own code | a risky path (money, security, data loss) with no test that fails when it breaks |
| affected tests ↔ whole suite | every change while working | a change to a lockfile, CI config, schema or fixtures; before merge |
| speed ↔ breadth | the agent's loop while it works | merge, nightly |

Two properties hold on every side: a test is **red** when what it checks breaks (it can fail for a reason), and **independent** (it passes or fails the same way alone, in any order, in parallel). Name what the project already uses; propose a new tool only when nothing there can do the job.

Why these trade-offs, with sources on both sides: [references/evidence.md](references/evidence.md). Read it when the person asks why, or before arguing for an option.

## Steps

### 1. Read the situation

- Kind: library, service, web app, CLI, data pipeline.
- Risky paths: where a break costs money, security, data or trust (payments, permissions, tenant isolation, migrations).
- How agents work here: watched by a person, or unattended (loops, background agents, CI bots).
- What agents are told about tests: rules in CLAUDE.md, AGENTS.md or similar files ("every change needs tests", "avoid mocks"), with file and line.
- What the person wants from the audit, when they said it: speed, confidence, cost.

Done when each has an answer and its evidence, or `unknown`. These answers set which way each trade-off leans in step 6.

### 2. Measure the loop

Find how the project runs its tests: its manifest scripts, task runner, test config and CI workflows.

- Count tests by level, with the runner's list command when it has one:
  - unit: one function or class, its collaborators replaced by mocks
  - integration: a real database, HTTP stack or framework container
  - end-to-end: the running app driven from outside, as a user or caller would
- Time three runs, each with a 10-minute limit:
  - affected: what the project's own tooling runs for one small recent commit. When nothing selects tests, this is the whole suite. When the selection is empty, the change ran no test at all: report that too.
  - local: the whole suite, with where the time goes per file or folder when the runner reports durations. When the run hits the limit, time one folder instead and say which.
  - CI: the median duration of the last 20 successful runs from the CI's run history (`gh run list` on GitHub), and the slowest job and step of one run.

Done when the report gives the count per level and all three times, each with the command that produced it, or `not measured:` and the reason.

### 3. Sort a sample of test files by what they protect

Read every test file when there are 30 or fewer. Otherwise read at least 30, spread across every test folder, plus the slowest files from step 2 and every test of a risky path from step 1. With more than 5 test folders, give each folder to a subagent with this step's table and checklist, and ask for each file's group, a one-line reason, two example asserts and its red note.

| group | what its asserts check | it breaks when |
|---|---|---|
| implementation | how the code works: a mock of the project's own class, a private method called, call order, an internal data shape; often one test file per source file | the code is refactored and nothing a user sees changed |
| behaviour | what a user or caller gets from an entry point: an HTTP route, a page, a CLI command, a public API | the product changes |
| boundary | a version or an outside system: a third-party API's response, a library or framework upgrade, a migration, a payment provider | the outside system changes |

Sort by the asserts, never by the file or test name. For each file write its red note: the break that should turn it red, whether another test fails on the same break, and how that is known:

- **shown**: it failed on a deliberate break. Cheapest first: run it on the parent commit of the fix it came with; apply a likely break in a throwaway git worktree and delete the worktree after; run the project's mutation tool on one module or the changed lines, never the whole project. Spend breaks where the decision matters: removal candidates and risky paths.
- **estimated**: read from the asserts only.

A test cannot fail, whatever the code does, when it:

- has no assert, or only "was called", "not null" or "is truthy"
- asserts a value that a mock was set up to return
- computes its expected value with the code under test, or copied it from the code's output
- has an assert that never runs: inside a try that swallows the error, after an early return
- mocks the module it tests
- compares to a snapshot or golden file that is regenerated with no review

Shared coverage only finds candidates: two tests can run the same lines and check different things. Note also the tests that work well, the ones that fail on a real break, run fast and enter where a user does: they are the pattern to copy.

Done when every sampled file has a group, a reason and a red note marked shown or estimated; the report gives counts per group, estimated from N of M files; and every risky path from step 1 has the test that fails when it breaks, or `none found`.

### 4. Check whether the agent can spike

An agent checks its own change by running it. Answer each row with the means the project already has:

| capability | the question | evidence, in whatever form the project uses |
|---|---|---|
| start | does one documented command bring the app up locally? | a compose file, a dev-server script, a task-runner target, a built binary, a launch recipe agents reuse (a project skill, a section of AGENTS.md) |
| drive | can the agent act as a user or caller? | a browser driver, an HTTP client, a script against the CLI or public API, a pseudo-terminal for a terminal UI |
| see | can the agent read what a user would see? | a screenshot or the page's DOM, a response body, the terminal's screen, the app's logs |
| isolated | can two agents each run their own copy at once? | ports, database and data folder set per worktree or per run |
| fast | does one user flow, scripted, run on its own in under a minute? | the script or the end-to-end test, and its time |

An agent that cannot spike checks its work by writing more tests, or leaves the person to check it by hand. A spike counts when it leaves evidence the person can read: the command and its output, or a screenshot.

Done when each row has yes or no and the evidence: the command or the file.

### 5. Check what one change runs

| check | the question | evidence |
|---|---|---|
| affected | does the project map a change to the tests that reach it, and what can the map not see? | a dependency graph in the build or monorepo tool, the runner's changed-files or related-tests mode, a coverage map, CI path filters; its blind spots: changes to inputs that are not code (lockfile, CI config, fixtures, env), a shallow CI clone, imports the tool cannot follow |
| tiers | where does the whole suite run? | every push, before merge, after merge, nightly |
| parallel | does the suite run on more than one worker or machine? | the runner's worker setting, CI shards or a job matrix |
| independent | do tests pass in any order and at the same time? | one run of the slowest folder in random order or with workers, when the runner supports it, within the 10-minute limit |
| flaky | does red always mean broken? | retry settings, tests marked flaky, CI runs that failed and then passed on the same commit, and who owns each flaky test |
| protected | can an agent edit, skip or delete an existing test to get a pass without anyone noticing? | a deny rule or hook on test paths, review rules on test folders, a CI check that flags changes to existing tests |

Done when each row has yes, no or partial, and its evidence.

### 6. Weigh the options

Each option names its files, what it gains, what it gives up, and its evidence (shown, estimated, or a time step 2 measured). Lean each one by the situation from step 1.

- **Remove**: a test with no red of its own, because it cannot fail or another test fails on the same break (shown). Between two tests that fail on the same break, the one that runs real code beats the one with mocks, then the faster one. A test on a risky path is removed only when the option names the test that replaces it and a break showed that test failing.
- **Merge**: many example tests of one pure function, into one table-driven or property-based test.
- **Strengthen**: a test that cannot fail but guards something that matters; name the assert it needs.
- **Keep**: say what it guards.
- **Add**: a test for each risky path with none; a test that fails on a bug fixed without one; one to three tests of the main user flows (sign up, pay, export) at the entry point, through a browser only when the flow lives in one, written from what the product should do by an agent that has not read the code under test; what step 4 found missing for the agent to spike.
- **Fix**: tests that skip without saying so, no longer reach what they claim to check, are flaky, or pass only in one order.
- **Speed up**: selection of affected tests with its blind spots covered, and the whole suite at merge or nightly; parallel workers once tests are independent; then caching dependencies in CI, splitting the slowest job, reusing a running database or container.
- **Protect**: when step 5 found an agent can weaken tests unnoticed, the options from a review rule on test folders to a check that flags edited tests; adding new tests stays allowed.
- **Instructions**: when a rule from step 1 caused harm the audit saw (for example "every change needs tests" beside many tests that cannot fail), the wording that would replace it.

Done when every candidate from step 3 has an option or a keep, every option names files, gains, costs and evidence, and every risky path with no failing test has an add.

### 7. Write the report

One screen, in this order, every number followed by its source command:

```
TEST AUDIT  <repo>                              sample: <N> of <M> test files
Situation   <kind> · risky paths: <list> · agents: <watched|unattended> · test rules: <file:line or none>

Checking one change today
  affected  <time>                              <command, or "nothing selects: whole suite">
  local     <time>                              <command>
  CI        <median> per run, last 20 runs      slowest: <job> / <step>

Tests (estimated from the sample)
  by level   unit <n>   integration <n>   end-to-end <n>
  by group   implementation <n>   behaviour <n>   boundary <n>
  red        shown <n>   estimated <n>   cannot fail <n>
  works well                 <files or patterns worth copying>
  risky paths with no test   <list, or none>

Can the agent spike?   start <y|n>  drive <y|n>  see <y|n>  isolated <y|n>  fast <y|n>
What one change runs   affected <y|n|partial>  whole suite <tier>  parallel <y|n>  independent <y|n>  flaky <n>  protected <y|n|partial>

Options                          gains              gives up             evidence
  <option>  <files>              <what it gains>    <what it gives up>   <shown | estimated | time>
```

Close with one line: before removing any batch, break on purpose the behaviour those tests cover and confirm a remaining test fails.

Done when every line of the template is filled or says why not, and the person can read it in two minutes.
