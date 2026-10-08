# Communication Contracts — Testing inter-agent protocols without live teams

Agent teams (TeamCreate) can't run in pipe mode. But inter-agent communication has two distinct concerns, and only one of them requires a live team.

## What SendMessage actually is

`SendMessage` is a **tool call** — same as Read, Edit, or Bash. In a live team, an agent decides to call `SendMessage(to: "system-quality", content: "...")`, choosing *who* to message and *what* to send. This is a behavioral decision with two parts:

1. **Content quality** — does the agent produce findings that are useful to the recipient?
2. **Routing decisions** — does the agent call SendMessage to the *right peer*, with the *right content*, at the *right time*?

These require different testing strategies because they have different infrastructure dependencies.

## What's testable without live teams

Content quality and cross-dimensional enrichment can be tested in pipe mode via simulated messages. The transport doesn't matter — what matters is whether Agent B produces better output when it has Agent A's findings.

### The three-pass contract test

**Pass 1 — Isolated baseline.** Run each agent as a subagent. Capture raw findings.

```bash
topology_out=$(claude -p "Run system-topology on $REPO" --output-format json | jq -r '.result')
contracts_out=$(claude -p "Run system-contracts on $REPO" --output-format json | jq -r '.result')
```

**Pass 2 — Simulated cross-pollination.** Re-run an agent with another's findings injected as prompt context. This simulates the *content* of a SendMessage delivery without the routing infrastructure.

```bash
contracts_enriched=$(claude -p "Run system-contracts on $REPO.
A teammate (topology specialist) sent you this message:
---
$topology_out
---
Use their findings to inform your analysis." --output-format json | jq -r '.result')
```

**Pass 3 — Enrichment evaluation.** Compare isolated output (Pass 1) against enriched output (Pass 2).

### The enrichment delta metric

| Outcome | What it means | Action |
|---|---|---|
| **No change** | Agent ignored teammate input; communication contract is broken | Fix the agent's system prompt to attend to teammate messages |
| **Surface change** | Agent acknowledges input but findings are substantively identical | Sharpen instructions on how to *use* cross-dimensional input |
| **Material enrichment** | Agent surfaces findings it couldn't have reached alone | Contract works — this is the target state |

## What's NOT testable without live teams

**SendMessage routing decisions** — whether an agent chooses to call `SendMessage(to: "system-quality")` vs `SendMessage(to: "system-topology")` vs not messaging at all. This is a tool call decision that requires:

- A live team with named teammates (SendMessage resolves by teammate name)
- The shared task list and mailbox infrastructure
- The agent to have discovered its teammates via team config

You cannot simulate this in pipe mode. SendMessage to a teammate name that doesn't exist in a running team will fail — there's no mock target.

**Multi-turn negotiation** — Agent A sends findings to Agent B, B challenges a conclusion and messages back, A revises. Each turn is a separate SendMessage tool call in a live session. Sequential prompt injection can't replicate the back-and-forth because each agent's response depends on the live state of the conversation.

**Task coordination** — claiming tasks, resolving dependencies, marking completion. These use TaskCreate/TaskUpdate with shared task list infrastructure that only exists in team mode.

## Coverage boundary

| Layer | Pipe mode testable | Requires live team |
|---|---|---|
| Agent produces consumable output | Yes — Pass 1 | |
| Agent integrates teammate input | Yes — Pass 2 | |
| Cross-pollination improves results | Yes — Pass 3 enrichment delta | |
| Agent routes SendMessage to correct peer | | Tool call requires live team with named teammates |
| Multi-turn back-and-forth negotiation | | Each turn depends on live conversation state |
| Task claiming and dependency resolution | | Shared task list only exists in team mode |

## When contract testing is sufficient

For **dimensional specialists** that analyze independently and cross-reference findings (the system-* agents), contract testing covers the critical path. Their communication pattern is broadcast-then-synthesize, not negotiation. The routing decision is simple (broadcast to all), so the untestable gap is small.

For **negotiation-heavy workflows** (competing hypotheses, adversarial review), the routing decisions and multi-turn exchanges ARE the value. Contract testing alone is insufficient — these need live team validation in interactive sessions.
