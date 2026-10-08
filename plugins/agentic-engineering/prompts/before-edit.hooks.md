---
why: >
  The first hook built for a CLAUDE.md edit told the model its rule through systemMessage, which only
  the person sees: the model never got it. A traceback in a hook costs the session, so a failing hook
  exits 0. A hook is proven by its text in the transcript and a with/without eval, not by reading it.
from: [0d5ed015, c7ca4aab]
evals: [hook-text-reaches-the-model]
---
[agentic-engineering: hooks] you are wiring a hook:
  text for the model goes in additionalContext (permissionDecisionReason on a deny); systemMessage reaches the person only
  a failing hook exits 0, silent
  proof: the text is in the transcript as the hook's context record, and a with/without eval shows the agent acts on it
Channels and moments: skill harness-engineering.
