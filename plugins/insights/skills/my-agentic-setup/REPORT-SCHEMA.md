# report.json — what a member's machine hands back

`scan.py --report` writes this; `render_report.py report.json > report.html` turns it into the page we collect.
It is organised by the v13 practices (`labs/v13/substrate/practices.yaml`): the interviewer's sheet, pre-filled.
Every string a reader sees is plain English with its number in it — the renderer adds no wording of its own
beyond headings and the legend.

```jsonc
{
  "schema": "agentic-setup-report/1",
  "person": "Ada Lovelace",                  // git config user.name; "" when unset
  "generated": "2026-10-19",
  "window_days": 30,
  "masked": true,                              // client-identifying names swapped for labels

  "headline": [                                // 4-6 big numbers across the top
    {"value": "27 of 30", "label": "days with agent work"},
    {"value": "541", "label": "sessions you typed in"},
    {"value": "7,114", "label": "runs your own scripts started"}
  ],
  "summary": [                                 // 3-5 plain sentences: how this person works, strongest first
    "Works with agents every day and runs several at once."
  ],

  "badges": [                                  // always these four, in this order
    {
      "id": "harness",                         // harness | context | prompt | loop
      "title": "Harness",
      "blurb": "What they built around the agent.",
      "practices": [
        {
          "id": "harness-7",                   // v13 practice id
          "title": "Uses hooks",               // v13 practice question
          "evidence": [                        // 1-3 lines, each with its number; [] allowed when the logs hold nothing on it
            "5 hooks of your own, plus 31 from plugins.",
            "Hooks fired in 538 of 541 sessions."
          ],
          "ask": "Which hook has saved you the most, and what did it stop?"   // always present
        }
      ]
    }
  ],

  "setup": [                                   // short "what's on this machine" list, label/value rows
    {"label": "Plugins", "value": "33 installed, 16 switched on"}
  ],
  "cannot_see": [                              // limits that matter for this person
    "Work on other machines or in devcontainers."
  ],
  "privacy": [                                 // what this file contains and does not
    "Names and counts only: no prompts, code, file contents or command lines."
  ]
}
```

No grading leaves the machine (ruling 2026-10-26): a practice carries its facts and its question, never a
verdict. practices.build() still computes seen / signs / ask locally, for next_steps.py to pick suggestions;
practices.shareable() drops it before report.json is written.
