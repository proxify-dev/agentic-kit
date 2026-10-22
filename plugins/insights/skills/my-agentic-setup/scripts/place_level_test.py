#!/usr/bin/env python3
"""Does place_level tell people apart? Made-up profiles built from the shape of a real scan.

His machine is one data point (it should read L4). These profiles check the other direction: the gates must
NOT promote a weaker person, especially one who installed someone else's plugins, hooks and memory
(the "tool did it" trap the 10-22 coverage study found). Run: python3 -I place_level_test.py SCAN.json
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import place_level as pl  # noqa: E402


def profile(base, *, days, typed, code, plan=0, sub=0, wt=0, comp=0, interrupts=0, open_=1, named=0.0,
            checked=0.0, diffs=0, review=0, corr=0, corr_fixed=0, edits=None, mcp=0, integ=None,
            own_runs=0, batches=0, sched=0, bounded=0, evals=0, eval_runs=0, blocks=0,
            forks=0, blank=None, chat_moves=0, kept=None, compact_typed=0):
    s = copy.deepcopy(base)
    h = s["habits"]
    h["sessions"].update(active_days=days, with_a_person_typing=typed, max_sessions_open_at_once=open_)
    w = h["working_style"]
    w.update(sessions_using_plan_mode=f"{plan} of {typed}", sessions_starting_subagents=f"{sub} of {typed}",
             sessions_in_worktrees=f"{wt} of {typed}", sessions_compacted=f"{comp} of {typed}", interrupts=interrupts)
    e = h["evidence"]
    prompts = typed * 6
    e.update(sessions_with_code_edits=code, prompts_total=prompts, prompts_naming_a_file=int(prompts * named),
             commits_in_sessions=max(code, 1), commits_checked_after_last_edit=int(max(code, 1) * checked),
             sessions_with_git_diff=diffs, sessions_with_review_step=review, sessions_with_corrections=corr,
             sessions_editing_tooling_when_corrected=corr_fixed, prompt_words_p90=40,
             instruction_edits_by_kind=edits or {}, integrations=integ or {},
             headless_by_starter={"own code calling the Agent SDK": own_runs, "eval runs": eval_runs},
             headless_batches=batches, scheduled_fires=sched, headless_bounded_calls=bounded,
             eval_commands=evals, hook_blocks=blocks)
    h["mcp_servers_used"] = {"github": mcp} if mcp else {}
    # rows 10-11: never inherit the base machine's helpers or chat moves
    h["subagents"] = {"built-in": {"general-purpose": sub if blank is None else blank, "fork": forks}}
    h["slash_commands"] = {"built-in": {"resume": chat_moves, "compact": compact_typed}}
    h.setdefault("coverage", {})["sessions"] = {"forked": 0, "continued": 0}
    if kept is not None:  # days of chats the machine keeps (cleanupPeriodDays)
        h["coverage"]["logs"] = {"cleanup_days": kept, "oldest_days": kept, "window_days": 30}
    return s


def extra(*, own_plugins=0, installed_blocking_plugins=0, own_hooks_block=False, nested_own_repos=0,
          shared=False, store=False, default_mem=40, own_skills=0, deny=0, team_skills=0, installed_skills=0):
    plugins = [{"plugin": f"own{i}", "own": True, "hook_events": ["PreToolUse"], "hooks_can_block": True}
               for i in range(own_plugins)]
    plugins += [{"plugin": f"theirs{i}", "own": False, "hook_events": ["PreToolUse"], "hooks_can_block": True}
                for i in range(installed_blocking_plugins)]
    repos = [{"repo": f"r{i}", "nested_rule_files": 3, "by_person": 2, "other_authors": 1 if shared else 0}
             for i in range(nested_own_repos)]
    repos += [{"repo": "team", "nested_rule_files": 6, "by_person": 0, "other_authors": 4}]  # a well-run team repo they never edit
    return {"identities": ["x@y"], "plugins": plugins,
            "user_hooks": [{"event": "PreToolUse", "own": True, "can_block": own_hooks_block}] if own_hooks_block else [],
            "memory": {"own_stores": [{"path": "~/notes", "files": 40, "days_touched": 12, "has_index": True, "folders": 3}] if store else [],
                       "claude_code_default_memory_files": default_mem},
            "rules": {"repos": repos}, "own_skills": [{"name": f"s{i}", "owner": "own"} for i in range(own_skills)]
            + [{"name": f"t{i}", "is_symlink": True, "owner": "team"} for i in range(team_skills)]
            + [{"name": f"i{i}", "is_symlink": True, "owner": "installed"} for i in range(installed_skills)],
            "permissions": {"deny": deny}}


L2_HABITS = dict(days=20, typed=120, code=60, plan=12, sub=25, wt=10, open_=4, interrupts=30, named=0.08, checked=0.6,
                 diffs=50, review=20, corr=20, corr_fixed=1, mcp=40, edits={"instructions": 3}, chat_moves=8, forks=6)

CASES = [
    # name, expected level, scan profile, extra
    ("chat-window dabbler", None, dict(days=4, typed=6, code=1), extra()),
    ("L1: daily, agent edits, uses tools; no discipline", 1,
     dict(days=18, typed=60, code=25, plan=1, sub=2, interrupts=2, checked=0.2, diffs=5, mcp=40), extra()),
    ("L1 who installed blocking plugins + sits in a good team repo (the trap)", 1,
     dict(days=18, typed=60, code=25, plan=1, sub=2, interrupts=2, checked=0.2, diffs=5, mcp=40, blocks=60),
     extra(installed_blocking_plugins=5, default_mem=200)),
    ("L2: plans, steers, checks, parallel; builds nothing", 2,
     dict(days=20, typed=120, code=60, plan=12, sub=25, wt=10, open_=4, interrupts=30, named=0.08, checked=0.6,
          diffs=50, review=20, corr=20, corr_fixed=1, mcp=40, edits={"instructions": 3}, forks=6, chat_moves=6), extra()),
    ("L2 with blank helpers only, never resumes or forks (forks can't be told apart from an old Claude Code)", 2,
     dict(days=20, typed=120, code=60, plan=12, sub=25, wt=10, open_=4, interrupts=30, named=0.08, checked=0.6,
          diffs=50, review=20, corr=20, corr_fixed=1, mcp=40, edits={"instructions": 3}), extra()),
    ("L3: own folder rules, fixes corrections at source, own memory; no hooks/evals", 3,
     dict(days=22, typed=150, code=70, plan=15, sub=40, wt=20, open_=5, interrupts=40, named=0.1, checked=0.65,
          diffs=60, review=25, corr=30, corr_fixed=12, mcp=60, edits={"instructions": 25, "memory": 30, "skill": 10},
          forks=8, chat_moves=7),
     extra(nested_own_repos=3, shared=True, store=True, own_skills=4)),
    ("L3 who installed a blocking plugin and runs headless, but has no evals", 3,
     dict(days=22, typed=150, code=70, plan=15, sub=40, wt=20, open_=5, interrupts=40, named=0.1, checked=0.65,
          diffs=60, review=25, corr=30, corr_fixed=12, mcp=60, edits={"instructions": 25, "memory": 30, "skill": 10},
          own_runs=80, batches=4, blocks=40, forks=8, chat_moves=7),
     extra(nested_own_repos=3, shared=True, store=True, own_skills=4, installed_blocking_plugins=3)),
    ("L4: own blocking hook, own runs, evals after setup edits", 4,
     dict(days=22, typed=150, code=70, plan=15, sub=40, wt=20, open_=5, interrupts=40, named=0.1, checked=0.65,
          diffs=60, review=25, corr=30, corr_fixed=12, mcp=60,
          edits={"instructions": 25, "memory": 30, "skill": 30, "hooks_or_settings": 20},
          own_runs=200, batches=6, sched=5, bounded=20, evals=60, eval_runs=30, blocks=40, forks=8, chat_moves=7),
     extra(nested_own_repos=3, shared=True, store=True, own_skills=6, own_plugins=2, own_hooks_block=True, deny=3)),
    # 2026-10-22, from the first people who ran it
    ("keeps only 7 days of chats, works daily (seen on a first run): readable, not 'not enough'", 1,
     dict(days=5, typed=14, code=6, sub=1, interrupts=1, checked=0.2, diffs=2, mcp=25, kept=7), extra()),
    ("readable, but the agent never edits files: below L1, not 'not enough to read'", 0,
     dict(days=18, typed=60, code=2, mcp=40), extra()),
    ("an L2 who compacts on purpose a lot (/compact typed): compaction must not count against them", 2,
     dict(L2_HABITS, comp=50, compact_typed=50), extra()),
    ("skills linked in from a team repo they co-write count as support (seen on a first run)", 3,
     dict(L2_HABITS, corr=30, corr_fixed=12, edits={"instructions": 25, "memory": 30}),
     extra(nested_own_repos=3, store=True, team_skills=5)),
    ("skills linked from an installer folder don't count as theirs", 2,
     dict(L2_HABITS, corr=30, corr_fixed=12, edits={"instructions": 25, "memory": 30}),
     extra(nested_own_repos=3, store=True, installed_skills=9)),
]


def main():
    base = json.loads(Path(sys.argv[1]).read_text())
    fails = 0
    for name, want, prof, ex in CASES:
        got = pl.place(profile(base, **prof), ex)["suggested_level"]
        ok = got == want
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  want {want}  got {got}  {name}")
    print(f"{len(CASES) - fails}/{len(CASES)} profiles placed as expected")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
