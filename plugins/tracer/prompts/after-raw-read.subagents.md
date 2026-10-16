---
why: >
  Said after a call read a subagent's transcript directly: an agent summed its tokens with jq and counted every message that spans several records more than once. {{session}} is the session's first 8 characters.
---
[tracer] That read a subagent's transcript directly. The shell already reads them: `tracer sessions agents {{session}}` (every subagent of the session: its type, the step that started it, calls, errors, time, tokens counted once per model message, the files two agents both touched) · `tracer sessions agents {{session}} <agent>` (one agent's steps; --open N: one whole).
