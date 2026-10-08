---
name: evals
description: "Evaluating and testing an agent's behavior"
---

You have a skill or agent. Now prove it works — not just that it returns the right answer, but that it works *efficiently and correctly* under the conditions it will actually face.
## CLI invocation patterns

This file covers eval *methodology* — what to test and how to assert. How to run them:

- **Invoking** `claude -p` — flags, output formats, `--bare` auth, thinking blocks: [references/cli-patterns.md](./references/cli-patterns.md)
- **Cheap** runs — tiers, breakpoints, parallelism, gating, budgets: [references/eval-execution.md](./references/eval-execution.md)
- **A hook or plugin you edited** — run it without reinstalling, prove it fired: [references/harness-artifacts.md](./references/harness-artifacts.md)
- **A plugin you don't trust, or one tried from zero** — run the eval where its hooks can't reach your machine: [sandboxing](../sandboxing/SKILL.md)
- **Python** — drive runs with the Agent SDK, deny a call before it runs: [references/agent-sdk.md](./references/agent-sdk.md)

## Before you build an eval suite

Analyze existing conversation transcripts first. Systematic failures surface in minutes without running a single eval — look for repeated patterns like agent spawning extra subagents, re-discovering cached content, or consistently taking 10x the expected tool calls. Doing this first prevents writing tests for the wrong problem.

## The three-tier assertion model

Each tier catches failures the tier below it cannot. Most eval suites only reach tier 1, then wonder why they pass while production behavior is bad.

| Tier | What it catches | When it's sufficient | Assertion types |
|---|---|---|---|
| **Correctness** | Wrong output, missing content, bad format | Deterministic lookup tasks with no efficiency requirements | `output_format`, `source_contains`, `content_contains`, `file_exists`, `hash_valid`, `error_output` |
| **Behavioral** | Right answer, wrong path (10 tool calls vs 4; 27s vs 15s) | Any task where efficiency is a success criterion | `max_turns`, `max_tool_calls`, `tool_not_used`, `tool_order`, `bash_contains`, `max_duration_ms` |
| **Orchestration** | Agent count/order violations; parallel spawns when sequential required; 2–3x token overruns | Any multi-agent skill that specifies an orchestration pattern | `agent_count`, `spawn_order`, `no_parallel_spawns`, `agent_types_exclude`, `brief_has_angles`, `max_tokens` |
| **Communication** | Agents ignore teammate input; cross-pollination adds no value; output format not consumable by peers | Any multi-agent system where agents are designed to inform each other | See [references/communication-contracts.md](./references/communication-contracts.md) for the three-pass contract test and enrichment delta metric |

Ask before designing each test case: *Which tier does this assertion belong to? Am I missing behavioral coverage because correctness passes?*

## Measurement infrastructure

Output format is a constraint on the entire eval suite, not an implementation detail. Choose the wrong format and whole tiers of assertions become impossible.

| Format | What you can assert | What's invisible |
|---|---|---|
| `--output-format text` | Output content only | All tool calls, turn count, duration |
| `--output-format json` | Turn count, total duration | Individual tool calls, order, inputs |
| `--output-format stream-json --verbose` | Full tool call timeline, every call and input | Nothing — this is required for behavioral and orchestration tiers |

Use `stream-json --verbose` by default. The overhead is negligible and it never constrains what you can assert later.

## Test case taxonomy

Not all tests warrant the same pass threshold. Mixing deterministic and non-deterministic cases under a single pass rate hides real failures.

| Phase | What it tests | Pass requirement | Why |
|---|---|---|---|
| **Deterministic** | Known inputs with predictable outputs (cache hits, known-site resolution, alias matching) | 100% — any failure = broken logic | No variance excuse when inputs are controlled |
| **Non-deterministic** | Discovery queries, WebSearch-dependent, probabilistic research tasks | N/M (e.g., 2/3) — accept normal variance | Penalize systematic failures, not inherent search noise |
| **Edge cases** | No-args listing, raw URL pass-through, graceful failure without hallucination | 100% or near-100% | Edge cases define the contract boundary |

Define pass thresholds per phase before running. Discovering them after the fact invites rationalization.

## pass@k vs pass^k

When to use each:

| Metric | Definition | Use for |
|---|---|---|
| **pass@k** | At least 1 success in k attempts | Probabilistic capabilities not yet held to reliability bar (discovery tasks, complex research, non-deterministic synthesis) |
| **pass^k** | All k attempts succeed | Reliability requirements where the task must work every time (cache-hit resolution, known-site lookup, security-sensitive behavior) |

Default to pass^k for deterministic phases and pass@k for non-deterministic phases. A skill that sometimes works is fine for discovery; unacceptable for cache resolution.

## Cache isolation modes

For skills with caching behavior, cold/warm/shared tests are three different experiments that expose distinct failure classes.

| Mode | Setup | What it exposes |
|---|---|---|
| **Cold cache** | Empty cache directory before run | First-run discovery logic; WebSearch fallback correctness |
| **Warm cache** | Pre-populate cache before run | Cache-first resolution; regressions invisible to cold-cache tests |
| **Shared cache** | Multiple evals share one cache | Same-domain follow-up behavior; redundant re-discovery bugs |

Warm-cache regression is the most common silent failure: correctness passes, the right URL is returned, but the agent re-discovers a site it cached 15 seconds ago.

## A/B comparison methodology

When optimizing, the baseline is often invisible without a comparison arm. Running only the new approach can't reveal where improvement actually came from or what trade-offs exist.

Structure: define N test cases, run each through arm-A (new) and arm-B (baseline), compare duration, token count, cost, and correctness per case.

The key insight: a comparison arm often reveals surprising parity zones — cases where the new approach adds no value. For documentation lookup, bare Claude without any plugin can match plugin performance for well-known frameworks whose URLs are already in training data. Only an A/B run surfaces this.

## The grader feedback loop

Graders should evaluate the eval suite itself, not just grade outputs. A grader that flags an assertion as "trivially satisfied" or "non-discriminating" provides higher-signal feedback than a passing assertion result.

Build this into the eval workflow: after running grading, check grader feedback before acting on pass/fail numbers. A suite where all assertions pass easily is more suspect than one where some fail — it may mean the assertions aren't testing what matters.

Effective graders:
- Flag surface-level compliance that doesn't require genuine work (correct filename, but empty file)
- Identify missing coverage (task completed but no test verifies the key capability)
- Suggest assertion improvements when current tests could pass via shortcut

## Breakpoint evals — test the decision, not the outcome

The cheapest behavioral eval: stop at the first tool call of interest and score the decision at that moment (~$0.02, 15-30s instead of ~$0.10-0.30, 60-90s). When the eval tests a decision (routing, explore-before-act, new file vs existing), or the call must not run at all: [references/eval-execution.md](./references/eval-execution.md#breakpoint-eval-pattern).

## Delivery ≠ compliance: two assertions for context-injecting artifacts

An artifact that injects context — a hook's `additionalContext`, a skill's reminder, an appended system prompt — has **two independent** assertions. Conflating them is the most common way an eval passes while the artifact does nothing.

- **Delivery** — the injected text reaches inference. Verify by making the model echo it verbatim, or via `--include-hook-events` (see [references/harness-artifacts.md](./references/harness-artifacts.md#verifying-a-hook-fired--and-what-it-injected)). Cheap, binary.
- **Compliance** — the model's *behavior* changes because of it. A tool-order breakpoint: did the prescribed action occur **before** the mutation it was meant to gate?

Delivery passing says nothing about compliance. An advisory nudge reaches both the floor and the representative model verbatim and still changes nothing — it loses to an active user imperative ("make the edit, don't ask").

When compliance fails, the lever is **mechanism strength, not phrasing or model**:

- advisory `<system-reminder>` / `additionalContext` — weakest; the model may ignore it
- deterministic `PreToolUse: deny` — compliance-proof; the action cannot proceed, so it is not subject to model judgement

Test the soft form first. If it is delivered but inert, do not tune wording or escalate the model — move the intervention to the decision point and make it deterministic. Attribute the change with a control arm (artifact-on vs identical-off): [A/B comparison methodology](#ab-comparison-methodology) — the change is only attributable if the control differs.

## The iterative measurement cycle

Evals are not a one-time "write 20 test cases" event. Skills and their eval suites co-evolve through iterations. Each iteration expands coverage to catch the *next* regression.

The pattern:

```
Iteration 0: Write baseline evals (correctness only) → run → identify behavioral gap
Iteration 1: Fix skill/agent instructions → re-run ALL evals → verify fix + regression check
Iteration 2: Add infrastructure (cache isolation, new assertion types) → new eval scenarios → expand coverage
Iteration 3: Debug production failure → add targeted eval that catches it → fix → verify no regression
Iteration N: A/B comparison → prototype optimization → validate across cold/warm/shared
```

Rules for iteration:
- **Always regression-check.** Every iteration runs the full suite, not just new tests. Fixing one behavior often breaks another.
- **Each iteration adds eval coverage.** If you fixed a bug, add an eval that would have caught it. The suite grows monotonically.
- **Behavioral evals follow correctness evals.** Don't add `max_tool_calls` assertions until correctness passes reliably — otherwise you're optimizing a broken skill.
- **Infrastructure unlocks new assertions.** Cache isolation (env var overrides, temp directories) enables warm-cache testing. Stream-json parsing enables tool-order assertions. Build infrastructure when the current framework can't express what you need to test.
- **Document each iteration.** A PROGRESS.md alongside evals.json captures what changed, what was learned, and what the next gap is. This prevents re-discovering the same problems across sessions.

## Testing multi-agent systems programmatically

Sub-agent orchestration is testable via `claude -p`. Agent teams are NOT — teams require interactive terminals and cannot run headless.

```bash
claude -p "Audit the agent system at {PATH}/.claude/agents/ \
  by spawning system-topology, system-contracts, system-context, \
  and system-quality as parallel sub-agents. Synthesize findings." \
  --allowedTools "Agent,Read,Grep,Glob,Bash" \
  --output-format stream-json --verbose
```

Parse stream-json for orchestration-tier assertions:
- `agent_count`: expected number spawned
- `spawn_order`: all agents spawned before synthesis
- Each agent reads files in its target directory
- Synthesis references findings from multiple dimensions

This applies to any multi-agent orchestration, not just the system-* agents. The key constraint: if a workflow uses agent teams for peer communication, it has no programmatic eval path. Design for sub-agent mode as the eval-friendly default; team mode is an interactive-only enhancement.
