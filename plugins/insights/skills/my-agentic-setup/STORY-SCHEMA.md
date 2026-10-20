# story.json — how one person works with agents, told as the flow of a task

`scan.py --story` writes it; `render_story.py story.json -o story.html` draws it. Detail version: real repo,
skill and hook names and short prompt excerpts are IN this file. It is for building the report, not for sharing:
privacy comes in a later pass that masks or drops fields, never by changing this shape.

The v13 mapping is not on the page. It rides along under `v13` for scoring tools.

```jsonc
{
  "schema": "agentic-story/1",
  "person": "teocns",
  "generated": "2026-10-19",
  "window_days": 30,
  "privacy": "detail",                       // masked | detail — the page shows the "not for sharing" banner unless this is exactly "masked"

  "at_a_glance": [                           // 4-6 big numbers, each with a plain label
    {"value": "27 of 30", "label": "days with agent work"}
  ],
  "in_one_paragraph": "Plain prose, 3-5 sentences: how this person works, start to finish.",
  "coverage": {                              // what the scan could see, once for the whole page
    "logs": {"dir": "~/.claude/projects", "state": "ok",   // ok | none (no logs at all) | stale (none inside the window)
             "files_total": 2650, "oldest_days": 79, "cleanup_days": null},
    "versions": {"first": "2.1.277", "last": "2.1.304", "switches": [   // a record Claude Code starts or stops writing inside the window
      {"signal": "home_file", "kind": "appears", "at": "2.1.300", "before": {"with": 0, "of": 80}, "after": {"with": 400, "of": 450}}]},
    "entrypoints": {"interactive": {"cli": 553}, "programmatic": {"sdk-cli": 6000}, "unlisted": []},
    "line": "Read 553 sessions a person typed in …"
  },

  "sections": [                              // in flow order; the renderer draws whatever is here
    {
      "id": "start",                         // start | handover | while | check | wrong | built | unattended | where
      "part": "How a task goes",             // optional: when it changes from the previous section, a part heading is drawn first
      "title": "Starting a task",
      "lead": "One plain sentence: the answer.",   // or "Can't tell — …" when there are no logs or too few moments (scripts/limits.py)
      "can_tell": false,                     // present, and false, only when the lead is a "can't tell"; the numbers read "can't tell"
      "coverage": {                          // every section: what it read, what it set aside and why
        "line": "Read 553 sessions a person typed in, of 7,633 in the last 30 days; 4,210 typed messages, 85 of them typed while the agent was busy. Set aside: 7,080 automated runs (no person typing). Claude Code 2.1.277–2.1.304. Logs reach back 79 days.",
        "read": ["553 sessions a person typed in, of 7,633 in the last 30 days"],
        "skipped": ["7,080 automated runs (no person typing)"]
      },
      "points": [                            // 2-6 plain sentences, each with its number
        "Lets the agent read around before it changes code in 300 of 337 sessions."
      ],
      "numbers": [                           // optional small stat row
        {"value": "1 in 7", "label": "sessions start in plan mode"}
      ],
      "ladder": [                            // optional: a progression, top to bottom; done steps filled, others hollow
        {"step": "Runs single sessions", "done": true, "evidence": "541 sessions, all started by hand."}
      ],
      "table": {                             // optional: up to 5 columns, ~25 rows: a small table that answers one question
        "caption": "Where plan mode gets used",
        "columns": ["Repo", "Sessions", "With a plan"],
        "rows": [["agentic-kit", "142", "31"]]
      },
      "examples": [                          // optional: real excerpts — prompts, commands, file names
        {"label": "A typical first message", "text": "look at the dashboard approval flow and …", "meta": "agentic-kit · Oct 14"}
      ]
    }
  ],

  "v13": {"practices": [{"id": "loop-15", "verdict": "seen"}]}   // hidden: never rendered
}
```

Every string the page shows comes from this file; the renderer adds only section chrome (headings it is given,
the detail banner, labels like "Examples"). Examples are verbatim user text: escape it, keep line breaks,
clip visually at ~6 lines with no JavaScript needed to read the start.

## Dev mode — how each finding was inferred

`scan.py --story --dev` adds provenance so the builder can see what went on under the hood and where the
picture is thin. `render_story.py` switches to the dev layout when the top-level `dev` key is present.

A **point** may be a plain string (as before) or an object:

```jsonc
{"text": "Plan mode in 78 sessions, mostly in brain (21).",
 "how": {
   "source": "session logs: permissionMode on every record; ExitPlanMode tool calls",   // where it was read
   "rule": "a session counts once if any record is in plan mode or it called ExitPlanMode", // the inference, plain words
   "raw": "78 of 496 sessions; brain 21, agentic-kit 20",                                  // the numbers behind it
   "confidence": "solid",                  // solid | heuristic | guess
   "caveat": "plan mode entered and left without a plan still counts",                    // optional: known blind spot
   "code": "orchestration.sections → planned"                                              // optional: where it lives
 }}
```

A **section** and a **ladder step** may carry the same `how` object. A **table** may carry `"how"` for its caption.

Top-level `dev`:

```jsonc
"dev": {
  "run": [{"label": "Session files read", "value": "10,291"}],          // facts about the scan itself
  "coverage": [                                                          // every signal, one row: the map of what we can and can't see
    {"area": "Sessions", "signal": "Forks and branches", "source": "forkedFrom field; copied records",
     "confidence": "solid", "status": "measured"}                         // status: measured | partial | not measured
  ],
  "gaps": [                                                              // where the picture is thin, worst first
    {"title": "Hand edits are invisible", "why": "the logs only hold what went through the agent", "affects": ["context-7", "loop-16"]}
  ],
  "no_trace": ["Keyboard shortcuts", "Voice dictation"]                  // features that leave nothing in the logs
}
```
