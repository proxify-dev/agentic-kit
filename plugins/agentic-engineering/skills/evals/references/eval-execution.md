# Eval Execution Patterns

## Two-tier eval methodology

Evals have two distinct execution profiles. Mixing them wastes time and money. Split every eval into the tier that matches each assertion.

| | Behavioral tier | Quality tier |
|---|---|---|
| **Tests** | Decisions (did it invoke the skill? read refs first? tool count?) | Output quality (is the file correct? structurally valid? right size?) |
| **Assertions** ([SKILL.md](../SKILL.md) tiers) | Behavioral (`max_turns`, `tool_order`, `tool_not_used`); orchestration (`agent_count`, `spawn_order`) | Correctness (`output_format`, `content_contains`, `file_exists`) |
| **CLI mode** | Full harness, `stream-json --verbose` | `--bare`, `--tools ""`, `json` output — parse `.result` or `.structured_output` |
| **Kill strategy** | Breakpoint — kill at first target tool call | Full completion |
| **Cost/run** | ~$0.02, 15-30s | ~$0.001, 2-5s |
| **What you score** | Tool trace up to breakpoint | Final output content |
| **When to use** | "Does Claude use the harness correctly?" | "Does Claude produce good output?" |

Orchestration assertions need the full harness (to observe agent spawning) AND full completion (to see all spawned agents): no breakpoint, no `--bare`.

A full-harness eval that runs to completion costs ~$0.10-0.30 and takes 60-90s. Most of that time is spent *after* the decision you care about was already made. Breakpoint evals capture the decision and kill immediately. Quality evals skip the harness entirely and test raw inference.

## Breakpoint eval pattern

The cheapest behavioral eval: spawn `claude -p` with `stream-json --verbose` (the behavioral template in [cli-patterns.md](cli-patterns.md#behavioral-eval-full-harness)), parse NDJSON line-by-line, and kill the process when the target tool call appears. Score the trace at that moment.

```python
for line in proc.stdout:
    obj = json.loads(line)
    if obj.get("type") == "assistant":
        for block in obj["message"]["content"]:
            if block.get("type") == "tool_use" and block["name"] in {"Edit", "Write"}:
                proc.kill()  # BREAKPOINT: score the trace so far
```

### Breakpoint types

| Breakpoint | Fires on | Score at this moment |
|---|---|---|
| **First mutation** | First Write or Edit call | Did it Read files first? Is the target path correct? Edit (existing) vs Write (new)? |
| **First agent spawn** | First Agent tool call | Does the spawn prompt specify a concrete task or a vague goal? |
| **First routing decision** | First Read after skill load | Did it read the routing table or jump straight to a reference? |
| **First skill invocation** | First Skill tool call | Which skill did it choose? Was it the expected one? |

### What makes a good breakpoint assertion

- Tests a **decision**, not an outcome — "did it explore before acting?" not "is the final output correct?"
- **Binary scoring** — pass/fail at the breakpoint moment, no subjective grading
- The failure mode it catches is **expensive if missed** — wrong file edits, unnecessary creation, blind mutations

### Breakpoint vs full completion

| Use | For |
|---|---|
| **Breakpoint** | Routing accuracy, explore-before-act, creation bias (new file vs existing) |
| **Full completion** | Output correctness, content quality, multi-step orchestration |

### When the call must not run

Killing on the `tool_use` line races the tool: by the time you kill, the Write may have landed. When the call must not run, deny it before it runs: a `PreToolUse` hook answering `permissionDecision: "deny"`, then stop the session. The Agent SDK does both in-process: [agent-sdk.md](agent-sdk.md).

## Parallel execution

Every `claude -p` run is its own process: run cases and arms side by side. Keep to about 4 at once on one machine — they share the rate limit and the CPU, and without `--bare` and a separate `HOME`, also `~/.claude` and every hook's store.

## Gating pattern

Run the behavioral tier first. If it fails, skip the quality tier — there's no point scoring output quality if Claude didn't even use the harness correctly.

```
┌───────────────────────────────────────────────────────────┐
│  Phase 1: Behavioral breakpoints (all arms, parallel)     │
│  Kill at first Write, check trace                         │
│  Cost: ~30s wall clock (N parallel × ~15-30s each)        │
│                                                           │
│  ──── GATE: if any behavioral assertion fails, stop ────  │
│                                                           │
│  Phase 2: Quality (all arms, parallel, --bare)            │
│  Full completion, score output                            │
│  Cost: ~3s wall clock (N parallel × ~2-5s each)           │
│                                                           │
│  Phase 3: Compare arms, generate report                   │
└───────────────────────────────────────────────────────────┘
```

If your eval only has quality assertions, skip Phase 1 and go straight to Phase 2. The gate only makes sense when you have both tiers.

## Model-escalation ladder and the complete-not-killed budget rule

- Run the floor model first (haiku) — cheapest signal.
- A hard *ignore* on the floor model is **inconclusive** for the model real sessions run; escalate **one** arm to the representative model (opus). A deterministic mechanism that *passes* on the floor needs only confirmation, not a full re-test.
- Size `--max-budget-usd` so the run **completes**. A budget-killed run yields no signal and still bills — a normal-mode opus arm dies in ~1 turn at $0.10; a few opus turns with full harness need ~$0.40–1.20.
- For a marker-less deny-loop gate, bound `--max-turns` low: you need the *first reaction to the first block*, not the retry thrash.
