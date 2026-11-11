# v15 — where it stands (2026-11-02, end of session c39f0ed2)

Start here when picking this up. Branch `feat/v15`, worktree `w/v15`, local only, nothing pushed.

## The goal (his words, 11-02)
The setup tool is the top priority (the November priority list puts it first). To make it useful we first fix what
levels are judged on: the practices. Build the substrate on **disciplines**, with named challenges and practices,
so the same system gives **recommendations** on a call and from a laptop. "Make it v15."

## What exists, and whether it holds

| Layer | Files | Holds? |
|---|---|---|
| Entity model + rules | `README.md`, `inputs/SCOPE.md` | ✅ building blocks approved by him; ⚠️ the five discipline names are a draft awaiting his ruling |
| Substrate: 5 disciplines, 61 challenges, 33 practices, ladders, techniques, learning links, all 117 v13 practices/cues crosswalked | `substrate/*.yaml` | ✅ structure; ⚠️ never reviewed by the second reviewer |
| How a level is read + recommendations | `levels.md` | ✅ from his rulings; draft |
| Engine: evidence → rung → pointer → recommendations | `engine/engine.py` | ✅ logic |
| Page (side pane, drawer, drill-down) + member view + interviewer sheet | `engine/render.py`, `engine/call_sheet.py` | ✅ design; shows whatever evidence it gets |
| **Laptop evidence: inventory → reader → skeptic** (replaced the 10 detectors) | `../setup-snapshot/experiments/2026-11-02-evidence/`, `engine/laptop_evidence.py` | ⚠️ **2 loops, fresh Opus audits: loop 2 = 83% right or nearly, 15% wrong, 1 practice without a rung, 1 of 5 recommendations hold** (detectors: 14% hold, 46% wrong, 26 missed). Bar not met; stopped by rule. Why + next shape: `../setup-snapshot/memory/problems/v15-evidence-rebuild-audit.md` |
| Playbook coverage | `coverage/README.md` (regenerate: `tools/coverage.py`) | ✅ 90 rungs: 48 taught, 35 mentioned, 7 missing; 55 pages to write/extend; 4 places the playbook contradicts the rulings |

Why the evidence failed: `../setup-snapshot/memory/problems/v15-evidence-failed-audit.md`. One detector per problem
reads one kind of evidence; the adapter added hardcoded reasons and unchecked L4s; "not seen" mixed *not read* with
*looked, not found*. The audit itself (an AI reader per discipline across the whole machine) is the shape that works.

## The substrate is the one source of truth (his rule, 11-03; branch feat/v15-publish)
Terms, pitfalls, practices and the public pages built from them live here and nowhere else.
- `substrate/vocabulary.yaml`: all 121 terms plus the vocabulary page's wording. Challenges and practices point in with `vocab:`.
- Challenges carry `pitfall:` cards, practices carry `practice_card:`, and `substrate/pages.yaml` frames the page.
- `tools/check.py` after every edit; `tools/edit.py inputs/edits/<file>.yaml <section> --write` applies field edits.
- `tools/publish.py <playbook repo>/docs` rebuilds `reference/vocabulary.mdx` and `reference/common-pitfalls.mdx`.

## Next (proposed, waiting on his yes)
Rebuild the evidence layer: **inventory** (code: every source on the machine, every number) → **reader** per
discipline (AI: rung per practice with paths, counts, moments; marks not-read) → **skeptic** per discipline (refutes
each row) → engine and page as now. Bar: a fresh independent audit finds ≥90% of claims hold on his machine. Then
two testers' machines. Cost to watch: Sonnet readers on a compact inventory, ~150–250k tokens per person.

## Open rulings (his)
- Rebuild the evidence layer as above?
- The 4 playbook contradictions (`coverage/README.md`, first section): fix the playbook or the ruling?
- Who sees the level pointer (member never; Proxify view shows it today).

## Run it
```
cd labs/v15
uv run --with pyyaml python -I tools/build.py                       # substrate → engine/substrate.json
R=~/.cache/setup-snapshot/problems-2026-11-02
python3 engine/engine.py $R $R/v15-result.json
python3 engine/render.py $R/v15-result.json /tmp/v15.html [--member]
python3 engine/call_sheet.py /tmp/v15-call-sheet.html --laptop $R/v15-result.json
```
Raw runs (his data, never committed): `~/.cache/setup-snapshot/` — `v15-draft-*.json` (substrate drafts),
`v15-audit-*.json` (the audit), `v15-coverage-*.json` (coverage), `problems-2026-11-02/` (chats + detector outputs).
