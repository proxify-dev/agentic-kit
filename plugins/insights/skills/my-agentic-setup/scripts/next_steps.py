#!/usr/bin/env python3
"""The second tab, "What to try next": a few small changes picked from what the scan did not see, and a brief
the member can hand to their own agent to make the repo-side ones as pull requests.

Why it exists: the page shows no level and no score (ruling 2026-10-26). What a member gets instead is the
next useful step. A brand review (2026-10-26) asked for exactly this, with the agent
gated to "open a PR, never merge".

Picks come from practices.build() run on the local scan: a practice the logs show ("seen") is never suggested.
"signs" (partly visible) comes before "ask" (not visible in the logs, so maybe done elsewhere; the wording
says "if you don't already").

Run: python3 -I next_steps.py FACTS.json -o next.html --brief next-steps-brief.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I drops the script's folder

import practices  # noqa: E402
from render_report import e  # noqa: E402

MAX_PICKS = 4

# practice id -> one small change. `agent` is set only when the change is a file in a repo, so the agent can
# make it on a branch and open a PR. Changes to the member's own settings stay as tips they try themselves.
# Order = priority when several are open: the cheapest, most useful first.
WINS = {
    "context-7": {
        "title": "Give your main repo an instruction file",
        "why": "Your agent starts every chat knowing the build, test and style rules, so you stop repeating them.",
        "try": "Ask your agent to write a short CLAUDE.md from the repo's README and scripts. Keep it under a page.",
        "agent": "Write a short CLAUDE.md for this repo: how to build, test and lint, the folder layout, and the rules a "
                 "new colleague would need. Use only what the README, scripts and config files say. Under 60 lines.",
    },
    "prompt-11": {
        "title": "Plan before the next bigger task",
        "why": "Agreeing on the plan first is the cheapest moment to catch a wrong turn.",
        "try": "Press Shift+Tab until you see plan mode, describe the task, and read the plan before saying go.",
    },
    "loop-17": {
        "title": "Turn a repeated correction into a rule",
        "why": "When the agent makes the same mistake twice, one line in your instruction file stops the third time.",
        "try": "Next time you correct the agent on something you've corrected before, ask it to add the rule to CLAUDE.md.",
        "agent": "Look through this repo's recent commits and review comments for a correction that came up more than "
                 "once. Propose one or two short rules for CLAUDE.md that would prevent it, and add them.",
    },
    "harness-7": {
        "title": "Let a hook run your checks",
        "why": "A hook runs your formatter or linter after every edit, so the agent sees problems straight away.",
        "try": "Ask your agent to add a project hook that runs the repo's formatter after it edits a file.",
        "agent": "Add a PostToolUse hook in .claude/settings.json that runs this repo's existing formatter or linter on "
                 "the edited file after Edit/Write. Use only tools the repo already has. Explain what it runs.",
    },
    "loop-15": {
        "title": "Get a second pair of eyes before you commit",
        "why": "A fresh helper that didn't write the change spots what the writer missed.",
        "try": "Before your next commit, ask: \"Use a subagent to review this diff for bugs and missing tests.\"",
    },
    "harness-2": {
        "title": "Turn a prompt you repeat into a skill",
        "why": "A skill saves you retyping the same instructions and gives the same result each time.",
        "try": "Pick something you ask for every week (release notes, a review checklist) and save it as a skill.",
        "agent": "Ask me which instruction I repeat most often in this repo, then save it as a skill in "
                 ".claude/skills/<name>/SKILL.md with a clear description of when to use it.",
    },
    "context-8": {
        "title": "Put folder rules next to the folder",
        "why": "Rules that only matter in one folder load only there, so the agent's context stays focused.",
        "try": "Move rules about one part of the code into a CLAUDE.md inside that folder.",
        "agent": "Find rules in this repo's root CLAUDE.md that apply to one folder only, and move each into a "
                 "CLAUDE.md inside that folder. Leave the root file shorter.",
    },
    "context-9": {
        "title": "Start fresh between unrelated tasks",
        "why": "A clean chat keeps old details from steering new work.",
        "try": "Type /clear when you switch tasks, or /compact with a short note on what to keep.",
    },
    "harness-5": {
        "title": "Hand the searching to a helper",
        "why": "A subagent reads many files and returns only what matters, so your main chat stays clear.",
        "try": "Next time you need to find something across the code, ask: \"Use a subagent to find…\"",
    },
    "context-10": {
        "title": "Keep notes between chats",
        "why": "What the agent learned yesterday is there today, without you explaining it again.",
        "try": "At the end of a useful chat, ask the agent to save what it learned to memory.",
    },
}

RULES = """\
## How to work with me on this

- Take one step at a time, and ask me before you start each one.
- Work on a new branch for each step. Never commit to the main branch.
- When a step is done, open a pull request (or, if the repo has no remote, leave the branch) and stop.
  **Never merge it.** I review and merge myself.
- Change only the files the step needs. Never touch secrets, credentials or `.env` files.
- If a step doesn't fit this repo, say so and skip it.
"""


def picks(facts: dict) -> list[dict]:
    rep = practices.build(facts)
    state = {p["id"]: p for b in rep["badges"] for p in b["practices"]}
    order = list(WINS)
    open_ = [pid for pid in order if pid in state and state[pid]["verdict"] != "seen"]
    open_.sort(key=lambda pid: (state[pid]["verdict"] != "signs", order.index(pid)))
    return [{"id": pid, "state": state[pid]["verdict"], **WINS[pid]} for pid in open_[:MAX_PICKS]]


def brief(chosen: list[dict]) -> str:
    steps = [c for c in chosen if c.get("agent")]
    out = ["# Next steps for my agentic setup", "",
           "These come from /insights:my-agentic-setup. Please help me try them in this repo.", "", RULES]
    if not steps:
        out += ["## Steps", "", "None of the suggestions need changes in a repo. Nothing to do here."]
    for i, c in enumerate(steps, 1):
        out += [f"## Step {i}: {c['title']}", "", f"Why: {c['why']}", "", c["agent"], ""]
    return "\n".join(out) + "\n"


CSS = """
:root{--p600:#5258FB;--p700:#3C3ED4;--p25:#F5F5FF;--p100:#D5D5FE;--p900:#171559;--midnight:#15172F;
  --g50:#F9FAFB;--g200:#EAECF0;--g600:#475467}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;font-weight:300;color:var(--midnight);background:#fff;line-height:1.65}
h1,h2{font-family:Poppins,Inter,system-ui,sans-serif;font-weight:700;line-height:1.3;margin:0}
.hero{background:var(--midnight);color:#fff;padding:48px 24px 40px}
.in,.wrap{max-width:720px;margin:0 auto}
.eyebrow{font-weight:500;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--p100)}
.hero h1{font-size:36px;margin:.3em 0 .4em}
.hero p{color:var(--p100);font-size:17px;margin:0}
.wrap{padding:8px 24px 48px}
.win{border:1px solid var(--g200);border-left:4px solid var(--p600);border-radius:12px;padding:18px 20px;margin:24px 0 0}
.win h2{font-size:20px}
.win .why{font-size:17px;font-weight:400;color:var(--p900);margin:.4em 0}
.win .try{font-size:15px;margin:0;color:var(--g600)}
.win .try b{font-weight:600;color:var(--midnight)}
.agent{margin:40px 0 0;padding:20px;background:var(--p25);border:1px solid var(--p100);border-radius:12px}
.agent h2{font-size:20px;margin-bottom:.4em}
.agent p{margin:.4em 0;font-size:15px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;background:#fff;border:1px solid var(--p100);border-radius:6px;padding:2px 6px}
pre{white-space:pre-wrap;background:#fff;border:1px solid var(--p100);border-radius:8px;padding:10px 12px;font-size:14px;margin:.6em 0}
.done{font-size:17px;margin-top:24px}
""".strip()


def render(chosen: list[dict], brief_path: str) -> str:
    cards = "".join(
        '<section class="win"><h2>%s</h2><p class="why">%s</p><p class="try"><b>%s</b> %s</p></section>'
        % (e(c["title"]), e(c["why"]), "Try it:" if c["state"] == "signs" else "If you don't already:", e(c["try"]))
        for c in chosen)
    if not chosen:
        cards = ('<p class="done">Your logs already show the habits we'"'"'d usually suggest here. '
                 'The "How you work" tab is the best place to look for your next idea.</p>')
    agent = ""
    if any(c.get("agent") for c in chosen):
        agent = ('<section class="agent"><h2>Let your agent help</h2>'
                 '<p>Some of these are changes to a repo. Open Claude Code in the repo you work on most and say:</p>'
                 '<pre>Read %s and walk me through it.</pre>'
                 '<p>The brief asks your agent to take one step at a time, check with you first, and open a pull request '
                 'for each change. It never merges anything: you review and decide.</p></section>' % e(brief_path))
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'><title>What to try next</title>"
            "<style>%s</style></head><body><header class='hero'><div class='in'><div class='eyebrow'>What to try next</div>"
            "<h1>A few small steps</h1><p>Picked from what your logs show so far. Each one takes minutes, "
            "and you choose which to try.</p></div></header><main class='wrap'>%s%s</main></body></html>"
            % (CSS, cards, agent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("facts")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--brief", required=True, help="where to write the brief the member hands to their agent")
    a = ap.parse_args()
    chosen = picks(json.loads(Path(a.facts).read_text(encoding="utf-8")))
    Path(a.brief).write_text(brief(chosen), encoding="utf-8")
    Path(a.out).write_text(render(chosen, a.brief), encoding="utf-8")
    print(a.out)


if __name__ == "__main__":
    main()
