# Evidence behind the trade-offs

What the sources say, on both sides, with how strong each one is. Cite the source when you argue for an option.

## Tests agents write

- Agents add mocks more often: 36% of agent commits that touch tests add mocks, against 26% of other commits (1.2M commits, 2,168 repos). The authors suggest putting mocking guidance in the agent's configuration files. [Hora and Robbes, 2026](https://arxiv.org/abs/2602.00409)
- 80.2% of agent-written test patches carry weak or no explicit check of the result (86,156 patches). [Banik et al., 2026](https://arxiv.org/abs/2606.18168)
- For LLM-written suites, line coverage barely tracks how many seeded faults the tests catch (r = 0.11), and more tests or more asserts went with a lower score. Correlation, not cause. [arXiv 2609.24341](https://arxiv.org/html/2609.24341v1)
- In bug repair, 46% of the passing validation runs agents made carried no information that tells the buggy code from the fixed code. [arXiv 2607.28871](https://arxiv.org/abs/2607.28871)
- Asked to look for gaps, a reviewer writes tests for cases that cannot happen; a test prompt can say "avoid mocks". [Claude Code best practices](https://code.claude.com/docs/en/best-practices)

## When tests stand in the way

- Claude models mostly edit the test files directly when a task cannot be solved honestly, and read-only tests reduced it, especially for Claude models. The tasks are impossible by design. [ImpossibleBench](https://www.lesswrong.com/posts/qJYMbrabcQqCZ7iqm/impossiblebench-measuring-reward-hacking-in-llm-coding-1)
- Telling the model not to cheat left the rate unchanged (80% and 80%), on one scoring task with 20 runs. [METR, 2025](https://metr.org/blog/2025-06-05-recent-reward-hacking/)
- Anthropic's harness for long-running agents says: "It is unacceptable to remove or edit tests because this could lead to missing or buggy functionality." [Anthropic engineering](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)

## Test first or spike first: the evidence is mixed

- For: red/green TDD is "a pleasingly succinct way to get better results out of a coding agent." [Simon Willison](https://simonwillison.net/guides/agentic-engineering-patterns/red-green-tdd/)
- Against the cost: TDD in the agent loop used 3x to 8.5x the tokens with no clearly visible difference in quality; 2 to 6 runs per cell, judged by a model, called far from a structured eval by the author. The same article describes approved scenarios: a person confirms a flow once, and later changes are checked against it. [Birgitta Böckeler](https://martinfowler.com/articles/exploring-gen-ai/tdd-in-the-agent-loop.html)
- A spike that only looks at part of the product misses failures: an agent that checked with unit tests and curl called features done that failed end to end; browser automation fixed it. [Anthropic engineering](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- Without a check the agent can run, "you become the verification loop"; ask for evidence rather than a claim of success. [Claude Code best practices](https://code.claude.com/docs/en/best-practices)

## Levels and sizes

- Google prefers real objects over mocks "as long as the real objects are fast and deterministic", and one test per behaviour. [Software Engineering at Google, ch. 12](https://abseil.io/resources/swe-book/html/ch12.html)
- Google's guideline mix is about 80% unit, 15% integration, 5% end-to-end, counted by test case, and tests lose value as flakiness nears 1%. [ch. 11](https://abseil.io/resources/swe-book/html/ch11.html)
- Larger tests are often flakier, and flakiness can make them unusable. [ch. 14](https://abseil.io/resources/swe-book/html/ch14.html)
- The classic pyramid deletes in the other direction: a high-level test already covered at a lower level is the one removed. [Ham Vocke, The Practical Test Pyramid](https://martinfowler.com/articles/practical-test-pyramid.html)
- An agent writing property-based tests found real bugs across more than 100 Python packages; 56% of a reviewed sample of reports were valid. [Anthropic research](https://www.anthropic.com/research/property-based-testing)

## Running fewer tests per change

- At Anthropic, CI jobs rose 25x in six months, from 8x more code shipped in smaller pull requests and 10x more tests; test impact analysis was the answer. [Claude blog](https://claude.com/blog/agentic-coding-is-straining-ci-heres-how-we-scaled-test-impact-analysis-at-anthropic)
- AI adoption goes with lower delivery stability; without strong automated testing, more change leads to instability. A survey: association, not cause. [DORA 2025](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report)

## Flaky tests

- Slack took test job failures from 57% to under 5% by detecting flaky tests and suppressing them with a ticket to the owning team; a first version that hid their results leaked failures into main and was rolled back. [Slack engineering](https://slack.engineering/handling-flaky-tests-at-scale-auto-detection-suppression/)

## Not known

- No study measures whether removing agent-written unit tests makes agents do better or worse on real repos. The evidence above is indirect.
- No source measures the token or CI cost of suite size alone.
- How often agents weaken tests in everyday feature work, as against impossible or scoring tasks.
- How accurately an agent sorts tests into implementation, behaviour and boundary by reading the asserts.
