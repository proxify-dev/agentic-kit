# Verification: the agent tests its own result

Making an agent test its result before it says done, and setting up who checks finished work. Elsewhere:
- [Measuring whether a harness piece changes the agent across runs](../../evals/SKILL.md)
- [Auditing a project's whole test suite: what to cut, keep, speed up and add](../../fewer-faster-tests/SKILL.md)
- [Writing the Stop hook that keeps the agent working](hooks.md)
- Back to [placing a rule](../SKILL.md)

## The mistake

The agent writes the code, rereads it, judges it fine and stops: "Models are biased towards their first plausible solution" (LangChain). When they grade their own work, agents "reliably skew positive" (Anthropic). Rereading the code is not the same as running it.

## Test the running thing

- A web app: the app running per worktree, driven through a browser (DevTools protocol, Playwright): the page, a screenshot, the console's errors (OpenAI, Anthropic).
- A terminal UI or a CLI: run it in tmux and read the screen back.
  `tmux new-session -d -s try -x 200 -y 50 '<command>'`
  `tmux send-keys -t try '<what the person would type>' Enter`
  `tmux capture-pane -p -t try`, again after the output stops changing.
  Seen here: unit tests passed on a screen that printed its title twice.
- A service: logs and metrics it can query, set up for the task and removed after (OpenAI).
- Its own session: the transcript, and [what was sent to the model](channels.md).
Done when the agent has looked at what a person would look at.

## Write down what "done" means before the work

- Agree the criteria first: what will be checked, and how (Anthropic's sprint contracts).
- Keep them in a file the checker reads: a list of features, each with a passes flag, all false at the start. The agent doing the work never edits the list; only the checker sets a flag to true (Anthropic).

## Three verdicts

- PASS: evidence for every criterion, what was run and what was seen.
- FAIL: the failure, shown.
- NOT PROVEN: what is missing to decide.
Every criterion starts at FAIL and changes only on evidence the checker produced itself (the completely project); a check that did not run never counts as passed (forge-harness). An agent that cannot get the evidence reports NOT PROVEN, never done.

## Who checks

- Costly work, or work at the limit of what the model does reliably alone: a separate agent that did not write it (a subagent, a second session), given the criteria and how to run the thing. Built by one agent in 20 minutes for $9, a game did not work; with a separate evaluator, in 6 hours for $200, it played (Anthropic).
- Easy work: a separate checker is overhead; Anthropic cut theirs to one pass on a newer model. Check it yourself, against the criteria.
- Improve a checker by reading its logs: where its verdict differs from yours, change its prompt, and give it examples of both (Anthropic).
- A check the agent wrote for its own work usually passes (sawinyh): prefer one it did not write.

## A check before the agent stops

A Stop hook can refuse the end of a turn until the checks ran: exit 2 with what is missing (LangChain's pre-completion checklist, HumanLayer). It lets the stop through when `stop_hook_active` is set, or it loops ([how hooks break](hooks.md)).

## Prove the check works

Break the rule the check tests, on purpose, and confirm that check fails (forge-harness). A check that has never failed may not be checking anything.
