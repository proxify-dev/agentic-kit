# fewer-faster-tests

An agent skill that audits a project's test suite for agent-driven development, then lays out options (remove, merge, strengthen, keep, add, fix, speed up), each with what it gains and what it gives up. You choose. It changes no file.

Agents tend to write many small tests that mock the project's own code and check little; they also skip tests a risky path needs. The goal is not fewer tests but a suite where every test can fail for a reason, and where checking one change stays fast. This skill measures how long checking one change takes, reads what each test protects, and gives you one screen of options.

## Install

```bash
npx skills add proxify-dev/agentic-kit --skill fewer-faster-tests
```

Then, in Claude Code: `/fewer-faster-tests` (the current repo) or `/fewer-faster-tests path/to/repo`. Other agents load it when you ask them to audit the tests or speed up CI.

## The trade-offs it weighs

It works on any stack: it asks what the project can do, and names the project's own tools as the evidence. Your project's situation decides which way each one leans.

| trade-off | leans to the first when | leans to the second when |
|---|---|---|
| spike ↔ kept test | exploring, new UI | a bug fix, a path an unattended agent will change again |
| entry-point test ↔ unit test | what a user or caller gets | pure logic, a library's public API |
| fewer ↔ more | tests that cannot fail, or mock your own code | a risky path with no test that fails when it breaks |
| affected tests ↔ whole suite | every change while working | a lockfile or CI change, before merge |
| speed ↔ breadth | the agent's loop | merge, nightly |

## What it does

1. **Reads the situation**: what kind of project it is, its risky paths (money, security, data), whether agents work watched or unattended, and what agents are told about tests.
2. **Measures the loop**: tests counted by level, and three times: the tests one change runs, the whole suite locally, and the median CI run.
3. **Sorts the tests** by their asserts into implementation, behaviour and boundary, and notes for each what break makes it fail, whether that was shown by a deliberate break or only estimated, and which tests cannot fail at all. It also notes the tests that work well.
4. **Checks whether the agent can spike**: start the app, drive it, see what a user would see, run its own copy beside another agent's, and run one flow in under a minute.
5. **Checks what one change runs**: only the affected tests or the whole suite, in parallel or not, whether tests are independent and stable, and whether an agent could weaken a test to get a pass.
6. **Weighs the options**, each with its files, gains, costs and evidence. It is as ready to propose adding a test to an unguarded risky path as removing one that cannot fail.
7. **Reports on one screen**, with every number next to the command that produced it.

The sources behind each trade-off, on both sides, are in [references/evidence.md](references/evidence.md).

## Part of

The agentic-engineering skills of the [agentic kit](https://github.com/proxify-dev/agentic-kit).

## License

MIT
