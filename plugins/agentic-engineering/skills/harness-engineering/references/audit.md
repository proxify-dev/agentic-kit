# Audit: a rule that is not followed

Finding out why a rule, a hook or a skill that exists still does not change what the agent does. Elsewhere:
- [Placing a new rule](../SKILL.md)
- [Building the check that replaces a rule written as text](checks.md)

1. Write its eval case in the session where it went wrong, before any fix: the session, the person's words, what went wrong, what the case tests. Done when the case fails.
2. Read the transcripts of the mistake before building anything: what the agent had in its context window, in order, and what it did with it. With the kit CLI: `ak sessions show <session>`, `ak trace --session <session>`. Name the kind of mistake: it misread what was asked, it ignored an instruction it had, or it did more than the task (OpenHands).
3. Does the text reach the agent at all? Most failures are here, and they are the cheapest to fix. Installed is not the same as fired, and fired is not the same as followed: find which one failed. Go down this list and stop at the first failure:
   - Delivery: which channel delivers it at the right point, in what form (a hook's context, a file the agent reads, CLAUDE.md), and is that form strong enough for how strictly the rule must hold? A rule in a file the agent reads is data: move it to a hook, a skill or CLAUDE.md. Is the plugin that owns it enabled (the `enabledPlugins` key in `~/.claude/settings.json`, `.claude/settings.json`, `.claude/settings.local.json`; with the kit CLI, `ak plugin ls`)? Was the text removed: compacted away, cut at a size cap? No channel found: that is the failure.
   - Trigger: a skill that never loads never acts, so count its real loads first: `Skill` tool calls naming it in the transcripts (with the kit CLI: `ak trace --tool Skill --q <name>`). For a hook: does the matcher catch the tool call? A `Write|Edit` matcher never sees an edit made through Bash (`sed -i`, a heredoc).
   - Wording: too strict (capital MUSTs, every item mandatory, no reasons) and agents work around it; too vague (no example, no point at which to decide) and it changes nothing.
   - Timing: does the content fit what the agent is doing when it arrives? Content for several points in the work is split, so each part arrives on its own.
   A failure here is fixed in the channel: a hook at the right tool call, a narrower matcher, the plugin turned on. Not in the wording.
4. It arrives and is still ignored: fix the wording first. Still ignored: use a stronger mechanism, not a different model. Context injected as advice is the weakest; a PreToolUse deny cannot be ignored, because the action does not happen. Move the rule to the point where the agent decides and make it deterministic: [choosing the mechanism and the check](checks.md).
5. Match the problem to what to change (Anthropic):
   - it misunderstood the task: change what it can find, the structure and the docs it reads first
   - it fails the same way again: a formal rule in the tool, a check or a refusal with the right command
   - it cannot fix its own errors: [better tools](tools.md) and [a way to test its result](verification.md)
   - it gets worse as features are added: [an eval suite that runs on each change](../../evals/SKILL.md)
6. A correction made twice is a setup problem: fix it at the level where it keeps happening, or fix the code.

Done when the case from step 1 passes.
