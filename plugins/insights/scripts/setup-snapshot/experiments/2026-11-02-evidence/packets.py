#!/usr/bin/env python3
"""One packet per discipline for the v15 readers: its practices (ladder per rung, what a laptop would show), its
challenges (what a laptop would show), and the rulings every rung must honour. Built from labs/v15/engine/substrate.json
so the readers never open the long substrate YAML (that is what keeps a reader near 20-30k tokens).

    python3 packets.py RUN_DIR      → RUN_DIR/packet-<discipline>.md
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V15 = os.path.normpath(os.path.join(HERE, "..", "..", "..", "v15"))

RULES = """## How a rung is read (v15 README, levels.md, his rulings in inputs/SCOPE.md)
The rung says WHO CARRIES the practice: L1 doesn't do it (the problem happens, unhandled) · L2 you do it, by hand, in
the moment · L3 your setup does it (the next chat, run or teammate gets it without you) · L4 you built something that
does it and changes how the agent runs. Use the practice's own ladder text below; a rung is the highest one whose text
the evidence matches. Evidence from the laptop is "did".
His rulings (quote the one you use in `why` only if it decides the rung):
- Using MCP servers, plugins or skills OTHERS wrote = L1. Writing your own skills = L3. Building an MCP server, or hooks,
  plugins or programs that set how the agent behaves = L4. Authorship comes from git (inventory: plugins[].theirs,
  own_artifacts, tools.*.by_program[].where.theirs); an installed copy counts as theirs only through its source repo.
- Session management, side work sent to helpers, two pieces of work in their own sessions = L2. Dynamic workflows
  (the Workflow tool, ultracode) = L2, not L4. Saved workflow scripts they wrote that run one agent per item = L3.
- A store they shaped that the agent searches on its own = engineered context (L3); pointing every chat at it by hand = L2.
  A pickup that followed their own pointer ("where is…", a chat id in the first message) = L2, not L3.
- L3 border: who carries it, them (L2) or a setup they engineered (L3).
- A practice's L4 is its own ladder text. When that text asks for a test or check the agent can't edit, require it (a
  ledger with runs, a guard that fired); when it doesn't, don't. (The level's own rule, that L4 needs evals on what they
  built, is applied by the engine, not by you.) Built but not used in the 30 days: say so, and give the rung it
  reached in use (`could_be` the built rung).
- A Claude-written rules file counts when its writing is supervised and it doesn't grow into rot.
- Testing is a strategy, not a tick. Plan mode is one way to plan; typing "plan this" counts the same.
- Side projects count. Plans, wishes and "we" setups are not evidence for the person.
- Nothing seen is never a minus. Two different states: `looked, not found` (you checked the sources that would show it,
  name them) and `not read` (no source in the inventory covers it; name what would). Never mix them.
Writing: plain words a non-engineer reads; lead with what the person does and the number ("in 222 of 611 chats…");
no "every/never/always" about the person; no level words (L3, Level) in `why`; no member names."""


# A problem's bar: the probe whose verdict is the default, and what crossing it means. Fresh audit 11-02: readers and
# skeptics marked 9 problems "shows" below their own detector's threshold. Thresholds are the detectors' constants
# (../2026-11-02-problems/detectors/*.py); notes say when a probe's verdict is about the fix, not the problem.
BARS = {
    "context-rot": ("context-rot", "shows when stepping in past 300k tokens is more frequent than between 50k and 200k"),
    "context-poisoning": ("context_poisoning", "symptom = streaks of 2+ step-ins within 5 messages, counted with the base "
                          "(chats); the probe's verdict compares what followed a streak (the fix), not whether streaks happen; "
                          "'shows' needs the same wrong turn repeating in most streaks read"),
    "trajectory-poisoning": ("context_poisoning", "shows only if stepping in after a streak is more frequent than the person's "
                             "usual rate (held numbers vs after_any_step_in_for_comparison); a streak alone is the precondition"),
    "starts-from-zero": ("starts-from-zero", "shows when past work reaches fewer than half of new chats: past_work_reach.either_pct "
                         "< 50 (looked up early OR notes a hook put in); first messages that ask about another session are not re-explaining"),
    "prompt-bloat": ("prompt-bloat", "shows when an always-loaded file the person wrote passes 200 lines"),
    "lost-in-the-middle": (None, "a model-attention effect inside one long context; a deliberate cut in the person's own code "
                           "is not it (credit the handling practice). Without a recall-by-position test: can't tell"),
    "goal-drift": ("goal_drift", "the probe's verdict is about whether saying 'done' up front helped (the fix); symptom = early "
                   "step-ins where the agent went off the ask (about half of early step-ins are the person adding to it), with the base"),
    "prompt-overfitting": (None, "shows when the person names overfitting about a kept prompt, or a kept prompt grows a rule for "
                           "one case; a held-out score that holds is 'doesn't show'"),
    "aggressive-prompt-language": (None, "shouting in KEPT instructions (always-loaded files, skills, agent files); frustrated "
                                   "chat messages are not it"),
    "comprehension-debt": ("comprehension-debt", "shows when 20% or more of code commits (at least 30) went in with nothing run, "
                           "shown or reviewed after the agent's last edit"),
    "vibe-coding": ("comprehension-debt", "same proxy as comprehension debt and the same bar; the agent running git diff is not the "
                    "person reading; without the person saying output goes in unread: can't tell"),
    "doom-loop": ("doom-loop", "shows when a check failed with the same error 3+ times in a row, with edits aimed at it, in 3 or "
                  "more runs; a different error each time is normal iteration"),
    "declaring-done-prematurely": ("no_backpressure", "shows when the agent's first 'done' was refused by a check (the check "
                                   "catching it is the symptom seen and handled)"),
    "babysitting": ("babysitting", "shows when agents wait on the person 10 minutes or more per agent hour (10+ agent hours), or "
                    "they watch most long runs with nothing else going on"),
    "approval-fatigue": ("babysitting", "shows when waiting on permission prompts is 10+ minutes per agent hour, or prompts are "
                         "approved without reading (can't be seen on a laptop: then can't tell)"),
    "one-at-a-time": ("one-at-a-time", "shows when 70% or more of the busy time had a single agent working"),
    "meat-proxy": (None, "needs a receiver: work another person reviewed or received with no check after the last edit; "
                   "self-merged pull requests are not it"),
    "token-maxxing": (None, "cost set against kept work; without both on the laptop: can't tell"),
    "yolo-mode": (None, "prompts off AND nothing limiting what those runs can reach (deny rules, a sandbox, per-run tool lists) "
                  "where they touch shared or outside systems"),
}
BAR_RULE = """## Problems: a bar before "shows" (fresh audit 11-02)
A problem's verdict defaults to its probe's verdict where a probe exists (listed with each problem below). Flip it only
by citing new evidence that crosses that problem's bar, and say so in `why`. Where no probe exists, "shows" needs a
count with its base that crosses the bar written there; with no bar, one moment or a rate with no base is "can't tell".
The problem's own definition decides what counts (lost in the middle is attention, not a cut; meat proxy needs a receiver)."""


def live_practices(S):
    """The practice ids a reader is asked about and a page counts: the substrate's own lists, never a retired or merged
    entry (those stay in S["practices"] so old ids resolve). The one place a practice count comes from."""
    return [pid for d in S["disciplines"] for pid in d["practices"] if not S["practices"][pid].get("retired")]


def short(s, n):
    s = re.sub(r"\s+", " ", s or "").strip()
    w = s.split()
    return s if len(w) <= n else " ".join(w[:n]) + " …"


def packet(S, did, probes=None):
    probes = probes or {}
    d = next(x for x in S["disciplines"] if x["id"] == did)
    L = [f"# Packet: {d['name']} (`{did}`)", f"The question: {d['question']}", "", RULES, "", BAR_RULE, "", "## Practices"]
    for pid in d["practices"]:
        p = S["practices"][pid]
        L.append(f"\n### `{pid}`: {p['name']}")
        L.append(f"What it is: {short(p.get('what_you_do'), 35)}")
        for k in ("L1", "L2", "L3", "L4"):
            if (p.get("ladder") or {}).get(k):
                L.append(f"- {k}: {short(p['ladder'][k], 50)}")
        sig = [t.get("laptop_signal") for t in p.get("techniques") or [] if t.get("laptop_signal")]
        if p.get("laptop_evidence"):
            sig.insert(0, p["laptop_evidence"])
        if sig:
            L.append("Laptop would show: " + " · ".join(short(x, 30) for x in sig[:4]))
        L.append(f"Handles: {', '.join(p.get('handles') or [])}")
    L.append("\n## Challenges (named problems) in this discipline")
    for cid in d["challenges"]:
        c = S["challenges"][cid]
        signs = c.get("laptop_signs") or []
        L.append(f"- `{cid}`: {c['name']}."
                 + (f" Laptop: {' · '.join(short(x, 18) for x in signs[:3])}" if signs else " Laptop: nothing listed.")
                 + f" Handled by: {', '.join(c.get('handled_by') or [])}")
        if cid in BARS:
            probe, bar = BARS[cid]
            pv = probes.get(probe) or {}
            L.append(f"  Bar: {bar}." + (f" Probe `{probe}` says: {pv.get('verdict')} ({short(pv.get('headline'), 40)})" if pv else ""))
    return "\n".join(L)


def main(run):
    S = json.load(open(os.path.join(V15, "engine", "substrate.json")))
    try:
        probes = json.load(open(os.path.join(run, "inventory.json"))).get("probes") or {}
    except (OSError, ValueError):
        probes = {}
    for d in S["disciplines"]:
        t = packet(S, d["id"], probes)
        open(os.path.join(run, f"packet-{d['id']}.md"), "w").write(t)
        print(d["id"], len(t.split()), "words")


if __name__ == "__main__":
    main(os.path.expanduser(sys.argv[1]))
