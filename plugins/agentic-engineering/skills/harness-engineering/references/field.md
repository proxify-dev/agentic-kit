# Published reports: where these ideas come from, and how strong the evidence is

The sources behind the ideas on the harness skill's front page, how well each is proven, and what to read: for explaining a harness choice or defending one. Elsewhere:
- Back to [the harness and its six parts](../SKILL.md)
- [Turning a repeated mistake into a check](checks.md)
- [Deciding whether a piece still helps](maintenance.md)

## Where each idea comes from

- Agent = model + harness; the harness is "every piece of code, configuration, and execution logic that isn't the model itself" (LangChain).
- Claude Code itself as one harness: Anthropic calls its SDK "the agent harness that powers Claude Code"; describing what you add as a second layer around it comes from Fowler. Models are post-trained with their own harness in the loop (LangChain).
- The agent loop: "gather context -> take action -> verify work -> repeat" (Anthropic, Agent SDK).
- Who loads a piece: the model (skills), the person (slash commands) or the agent software at fixed points in its run (hooks) (Fowler).
- Instructions and checks: "anticipate the agent's behaviour and aim to steer it before it acts" (Fowler, who calls them feedforward guides and feedback sensors; you need both).
- Tools: "Agent-tool interfaces are as critical as human-computer interfaces"; rewriting tool descriptions alone cut task time 40% (Anthropic).
- Verification: agents "reliably skew positive when grading their own work" (Anthropic); "Models are biased towards their first plausible solution" (LangChain).
- State: "each new session begins with no memory of what came before" (Anthropic); "anything it can't access in-context while running effectively doesn't exist" (OpenAI).
- Maintenance: "Every component in a harness encodes an assumption about what the model can't do on its own" (Anthropic). "The space of interesting harness combinations doesn't shrink as models improve. Instead, it moves." (Anthropic)
- Fix what is missing: "what capability is missing, and how do we make it both legible and enforceable for the agent?" (OpenAI; five months, about a million lines, none written by hand).
- Every mistake an agent makes is answered with a change to its environment so that it does not happen again (Mitchell Hashimoto, quoted by HumanLayer); "it's not a model problem. It's a configuration problem." (HumanLayer)
- Fixing the output, or changing the harness that produced it: Fowler calls these working in the loop and working on the loop.
- Invariants, not implementations (OpenAI); the simplest thing that works, "simple, composable patterns rather than complex frameworks" (Anthropic, Building effective agents).

## Two ways to sort any check (Fowler)

- Computational (types, lint, tests: milliseconds, certain) or inferential (an LLM judge: slow, costly, unsure). Computational first.
- How easy a codebase is to check (Fowler's "harnessability"): types, module boundaries and frameworks decide which checks can be built at all; "the harness is most needed where it is hardest to build".

## How strong the evidence is

Measured, each by one team and not reproduced:
- Only the harness changed, same model: Terminal Bench 2.0 from 52.8% to 66.5% (LangChain).
- A game built by one agent in 20 minutes for $9 did not work; with a planner and a separate evaluator, 6 hours and $200, it played (Anthropic).
- Sandboxing cut permission prompts by 84% (Anthropic, internal use).
- One skill took a task from 0/10 to 10/10; other skills made results worse (OpenHands).
- Infrastructure settings moved coding-benchmark scores by more than many leaderboard gaps: treat a gap under 3 points as unproven (Anthropic).
- Passing test output takes 2-3% of the context window; a single check mark takes under 10 tokens (HumanLayer).

Stories and working principles, no numbers: most of the rest, including moving to a stronger mechanism step by step, blocking only what cannot be undone, instruction budgets (150-200 instructions, root files under 60 or 100 lines) and multi-agent gains for coding. Label your own claims the same way: measured, seen (once, in a transcript or in the API requests), documented (in the vendor's docs), or a working principle.

## What a harness cannot do

- "Correctness is outside any sensor's remit if the human didn't clearly specify." (Fowler)
- Tests an agent writes for its own work usually pass (sawinyh): prefer a check it did not write.
- "there are no unit tests for context engineering" (Fowler). What can be proven is that [the text reached the agent](channels.md) and that [its behaviour changed](../../evals/SKILL.md).

## Other names for the same parts in published reports

harness: scaffold · Instructions: feedforward, guides, context · Checks: sensors, gates, backpressure, guardrails · Tools: ACI (agent-computer interface) · Verification: evaluator, critic · State: memory, handoff artifacts, progress files · Maintenance: garbage collection, ablation.

## To read, when explaining harness engineering

- OpenAI, Harness engineering: https://openai.com/index/harness-engineering/
- Anthropic, Effective harnesses for long-running agents: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Anthropic, Harness design for long-running application development: https://www.anthropic.com/engineering/harness-design-long-running-apps
- LangChain, The anatomy of an agent harness: https://blog.langchain.com/the-anatomy-of-an-agent-harness/
- Thoughtworks, Harness engineering: https://martinfowler.com/articles/exploring-gen-ai/harness-engineering.html
- HumanLayer, Skill issue: https://www.humanlayer.dev/blog/skill-issue-harness-engineering-for-coding-agents
- Anthropic, Writing effective tools for agents: https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic, Demystifying evals for AI agents: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- The list they come from: https://github.com/walkinglabs/awesome-harness-engineering
