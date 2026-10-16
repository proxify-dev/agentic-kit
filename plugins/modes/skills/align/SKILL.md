---
name: align
description: Stop building and check with the user that the work still matches what they want, before any more changes. The user runs it mid-task when they see the work drifting.
argument-hint: "[what looks off, e.g. \"we're adding a retry loop, but I wanted one request\"]"
disable-model-invocation: true
---

The user stopped the work: they think it has drifted from what they want. Until you and the user agree on
the direction, change nothing: no Edit, no Write, no Bash command that changes a file or any state.
A read-only lookup is fine when an answer depends on it.

What looks off to the user: $ARGUMENTS
When that is empty, find it in their recent messages.

1. **Play back**, in a few lines, before any question:
   - the goal, in the user's own words where they gave them
   - what you have done toward it: the files changed, the choices made
   - what you were about to do next
2. **Name the drift**: the step where the work left the goal. Something added that they did not ask for,
   an approach they did not agree to, a different problem solved. Quote the user's words it went against.
   If you find no such step, say so.
3. **Ask** with AskUserQuestion: the decisions that set what happens next. What to keep, undo or redo,
   which direction to take. Each option's description says what picking it changes, not a synonym of its
   label. Your recommended option first.
4. **Restate** the agreed direction in one or two lines, then continue on it. When the answers leave the
   direction open, ask one more round. When the whole task is open again, say so and suggest
   /modes:interview.
