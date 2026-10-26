"""story — how you work with agents, told as the flow of a task (STORY-SCHEMA.md).

DETAIL version: real repo, skill and hook names and verbatim prompt excerpts. Built to see how much the
logs can say before privacy decides what may leave the machine; a later pass masks this file's fields
without changing its shape.

Input: the unmasked scan dict, every Session object (scan.ALL_SESSIONS), a cwd → repo-name map.
Every sentence carries its number; a sentence the data cannot back is left out, never softened.
"""
from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from datetime import timedelta

import limits


def n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else str(x)


def pct(a, b) -> int:
    return round(100 * a / b) if b else 0


def ratio(a, b) -> str:
    """Plain fraction for a reader: '2 in 3', '1 in 7', 'almost all', 'none'."""
    if not b:
        return "none"
    p = a / b
    if p == 0:
        return "none"
    if p >= 0.95:
        return "almost all"
    if p >= 0.5:
        for d in (2, 3, 4, 5):
            k = round(p * d)
            if abs(p - k / d) < 0.06:
                return f"{k} in {d}"
        return f"{round(p * 100)}%"
    return f"1 in {round(1 / p)}"


def of_(a, b) -> str:
    """ratio() that reads right before a noun: '89% of', '2 in 3'."""
    r = ratio(a, b)
    return r + " of" if r.endswith("%") else r


def day(ts) -> str:
    return ts.astimezone().strftime("%b %-d")


def clean(text: str) -> str:
    """Drop the harness's own tags from a prompt excerpt so the page shows what the person typed."""
    t = re.sub(r"<(system-reminder|command-[a-z]+|local-command-[a-z]+)>.*?</\1>", "", text, flags=re.S)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


def pick(items, k, seed):
    rnd = random.Random(seed)
    items = list(items)
    rnd.shuffle(items)
    return items[:k]


def flatten(sections: list) -> None:
    """Outside dev mode a point is plain text: drop the 'how' layer."""
    for sec in sections:
        sec.pop("how", None)
        sec["points"] = [p["text"] if isinstance(p, dict) else p for p in sec.get("points", [])]
        for l in sec.get("ladder", []) or []:
            l.pop("how", None)
        if sec.get("table"):
            sec["table"].pop("how", None)


def build(scan: dict, sessions: list, root_of: dict, person: str = "", v13: dict | None = None,
          dev: dict | None = None) -> dict:
    H, S = scan["habits"], scan["setup"]
    E = H["evidence"]
    days = H["window_days"]
    from scan import is_automated, is_typed
    human = [s for s in sessions if is_typed(s)]
    auto = [s for s in sessions if is_automated(s)]
    # A session started in a temp folder is a throwaway, not a project: it never names a row in a table.
    tmp_cwd = re.compile(r"^/(private/)?(tmp|var/folders)/|/T/")
    human = [s for s in human if not tmp_cwd.search(s.cwd or "")]
    repo = lambda s: root_of.get(s.cwd, "?")
    T = len(human)
    sections = []

    # ── where ────────────────────────────────────────────────────────────────
    by_repo = Counter(repo(s) for s in human)
    hours = Counter()
    for s in human:
        hours.update(s.hours)
    busiest = sorted(hours.items(), key=lambda kv: -kv[1])[:3]
    weekday = Counter(s.first.astimezone().strftime("%a") for s in human if s.first)
    per_day = Counter(s.first.astimezone().date() for s in human if s.first)
    top_day = max(per_day.items(), key=lambda kv: kv[1]) if per_day else None
    where_rows = []
    for r, c in by_repo.most_common(8):
        rs = [s for s in human if repo(s) == r]
        mins = sorted(((s.last - s.first).total_seconds() / 60) for s in rs if s.first and s.last)
        where_rows.append([r, n(c), f"{round(mins[len(mins) // 2])} min" if mins else "–",
                           n(sum(s.prompts for s in rs))])
    sections.append({
        "id": "where", "title": "Where and when you work",
        "lead": f"You work across {len(by_repo)} projects, and {by_repo.most_common(1)[0][0]} takes "
                f"{ratio(by_repo.most_common(1)[0][1], T)} sessions." if by_repo else "No sessions with you typing yet.",
        "points": [
            f"You worked with an agent on {H['sessions']['active_days']} of {days} days; your busiest day had "
            f"{top_day[1]} sessions on {top_day[0].strftime('%b %-d')}." if top_day else "",
            "You type most prompts around " + ", ".join(f"{h}:00" for h, _ in busiest) + "." if busiest else "",
            "Your busiest weekdays: " + ", ".join(f"{d} ({c})" for d, c in weekday.most_common(3)) + ".",
        ],
        "table": {"caption": "Which projects get the agent's time", "columns": ["Project", "Sessions", "Typical length", "Prompts"],
                  "rows": where_rows},
    })

    # ── start ────────────────────────────────────────────────────────────────
    # only sessions that changed code get an explore-first verdict: same set as "sessions that changed code"
    explored = [s for s in human if s.explored_first is not None and "E" in s.steps]
    expl_yes = sum(1 for s in explored if s.explored_first)
    planned = [s for s in human if "plan" in s.modes or s.tools.get("ExitPlanMode")]
    plan_by_repo = Counter(repo(s) for s in planned)
    firsts = [(s, s.prompt_texts[0]) for s in human if s.prompt_texts]
    first_words = sorted(w for _, (_, w, _) in firsts)
    long_first = [x for x in firsts if x[1][1] >= 80]
    short_first = [x for x in firsts if 6 <= x[1][1] <= 40]
    ex = []
    for s, (ts, w, t) in pick(short_first, 2, 1) + pick(long_first, 1, 2):
        ex.append({"label": f"First message ({w} words)", "text": clean(t), "meta": f"{repo(s)} · {day(ts)}"})
    sections.append({
        "id": "start", "title": "Starting a task",
        "lead": f"You let the agent look around before it changes code in {of_(expl_yes, len(explored))} sessions; "
                f"you start in plan mode in {ratio(len(planned), T)}.",
        "points": [
            f"In {expl_yes} of {len(explored)} sessions that changed code, the agent read or searched files first.",
            f"You used plan mode in {len(planned)} sessions, mostly in " + ", ".join(f"{r} ({c})" for r, c in plan_by_repo.most_common(3)) + "."
            if planned else "Plan mode hasn't come up in your chats yet.",
            f"Your first message in a session is usually {first_words[len(first_words) // 2]} words; "
            f"{len(long_first)} sessions open with a brief of 80 words or more." if first_words else "",
        ],
        "numbers": [{"value": f"{pct(expl_yes, len(explored))}%", "label": "read before changing code"},
                    {"value": ratio(len(planned), T), "label": "sessions start with a plan"}],
        "examples": ex,
    })

    # ── handover ─────────────────────────────────────────────────────────────
    prompts = [(s, p) for s in human for p in s.prompt_texts]
    words = sorted(w for _, (_, w, _) in prompts)
    P = len(words)
    sk = Counter()
    for s in human:
        sk.update(s.skills)
        for c, k in s.commands.items():
            sk["/" + c] += k
    mid = [x for x in prompts if 15 <= x[1][1] <= 60 and not x[1][2].lstrip().startswith("<")]
    ex = [{"label": f"A typical ask ({w} words)", "text": clean(t), "meta": f"{repo(s)} · {day(ts)}"}
          for s, (ts, w, t) in pick(mid, 3, 3)]
    sections.append({
        "id": "handover", "title": "Handing work to the agent",
        "lead": f"You ask in short messages, about {words[P // 2]} words, and you name a file in {of_(E['prompts_naming_a_file'], P)} cases."
        if P else "No prompts yet.",
        "points": [
            f"You wrote {n(P)} prompts in {days} days; one in ten is over {words[int(P * .9)]} words (a pasted brief or spec)." if P else "",
            f"{pct(E['prompts_stating_done'], P)}% say what done looks like (should / must / make sure …); "
            f"{pct(E['prompts_with_code_or_error'], P)}% paste code or an error.",
            "You call skills and commands by name: " + ", ".join(f"{k} ({c})" for k, c in sk.most_common(6)) + "." if sk else "",
        ],
        "examples": ex,
    })

    # ── while it works ───────────────────────────────────────────────────────
    sub = Counter()
    for s in human:
        sub.update(s.subagents)
    with_sub = sum(1 for s in human if s.subagents)
    wt = sum(1 for s in human if s.tools.get("EnterWorktree") or s.bash.get("git_worktree"))
    models = Counter()
    for s in human:
        models.update(s.models)
    interrupts = sum(s.interrupts for s in human)
    sections.append({
        "id": "while", "title": "While the agent works",
        "lead": f"You run up to {H['sessions']['max_sessions_open_at_once']} sessions at once and hand parts to helper agents in "
                f"{of_(with_sub, T)} sessions.",
        "points": [
            "Helper agents used most: " + ", ".join(f"{k} ({c})" for k, c in sub.most_common(6)) + "." if sub else "",
            f"You picked a model for a helper {n(E['subagent_calls_with_model'])} times; {E['sessions_multi_model']} sessions mixed models.",
            "Models: " + ", ".join(f"{k.replace('claude-', '')} ({pct(c, sum(models.values()))}%)" for k, c in models.most_common(4)) + ".",
            f"You worked in a separate git worktree in {wt} sessions.",
            f"You stopped the agent mid-step {interrupts} times, about once every {round(T / interrupts) if interrupts else '–'} sessions.",
        ],
        "numbers": [{"value": str(H["sessions"]["max_sessions_open_at_once"]), "label": "sessions at once, at most"},
                    {"value": n(sum(sub.values())), "label": "helper agents started"}],
    })

    # ── check ────────────────────────────────────────────────────────────────
    import scan as _scan
    rows = []
    rep_c = defaultdict(lambda: [0, 0])
    for s in human:
        c, k = _scan.verified_commits(s.steps)
        rep_c[repo(s)][0] += c
        rep_c[repo(s)][1] += k
    for r, (c, k) in sorted(rep_c.items(), key=lambda kv: -kv[1][0])[:8]:
        if c:
            rows.append([r, n(c), f"{pct(k, c)}%"])
    runners = Counter()
    for s in human:
        runners.update(s.test_runners)
    reviewers = Counter()
    review_name = re.compile(r"review|verif|critic|audit|judge|refut|\bqa\b", re.I)   # not "test": test-writing helpers are not reviewers
    for s in human:
        for a, c in list(s.subagents.items()) + list(s.skills.items()):
            if review_name.search(a):
                reviewers[a] += c
    C, K = E["commits_in_sessions"], E["commits_checked_after_last_edit"]
    sections.append({
        "id": "check", "title": "Checking the work",
        "lead": f"{ratio(K, C).capitalize()} commits come after a test or review that followed the last change; "
                f"for {n(C - K)}, a check isn't visible in your logs.",
        "points": [
            "Checks with: " + ", ".join(f"{k} ({c})" for k, c in runners.most_common(6)) + "." if runners else "No test or lint runs in your logs yet.",
            "Review helpers: " + ", ".join(f"{k} ({c})" for k, c in reviewers.most_common(4)) + "." if reviewers else "",
            f"You looked at the diff in {E['sessions_with_git_diff']} sessions and opened {H['bash_families'].get('pr_create', 0)} pull requests.",
            f"{E['sessions_ending_checked']} of {E['sessions_with_code_edits']} sessions that changed code ended with a check after the last change.",
        ],
        "table": {"caption": "Which projects ship checked work", "columns": ["Project", "Commits", "Checked after last change"], "rows": rows},
    })

    # ── wrong ────────────────────────────────────────────────────────────────
    corr = [(s, c) for s in human for c in s.correction_texts]
    landed_ex, lands = [], Counter()
    for s, (ts, t) in corr:
        after = [(t2, k, p) for t2, k, p in s.inst_paths if t2 > ts]
        if after:
            t2, k, p = after[0]
            lands[k] += 1
            landed_ex.append((s, ts, t, p))
    ex = [{"label": "A correction", "text": clean(t), "meta": f"{repo(s)} · {day(ts)}"} for s, (ts, t) in pick(corr, 2, 4)]
    ex += [{"label": "A correction, then a rule changed later in the same chat (the two may be unrelated)", "text": clean(t),
            "meta": f"{repo(s)} · {day(ts)} → then changed {p.split('/')[-2] + '/' if '/' in p else ''}{p.split('/')[-1]}"}
           for s, ts, t, p in pick(landed_ex, 2, 5)]
    cs = sum(1 for s in human if s.corrections)
    sections.append({
        "id": "wrong", "title": "When the agent gets it wrong",
        "lead": f"You correct the agent in about 1 session in {round(T / cs) if cs else '–'}; after "
                f"{of_(len(landed_ex), len(corr))} corrections a rule, skill or hook changes later in the same session "
                f"(though not always because of the correction).",
        "points": [
            f"{len(corr)} corrections in {cs} sessions ('no', 'that's wrong', 'revert', 'I said …').",
            "What changed after a correction: " + ", ".join(f"{k.replace('_', ' ')} ({c})" for k, c in lands.most_common()) + "." if lands else "",
            f"Apart from corrections, you changed your own instructions or tooling in {E['sessions_editing_own_tooling']} sessions.",
        ],
        "examples": ex,
    })

    # ── built ────────────────────────────────────────────────────────────────
    own = S["own"]
    used = Counter()
    for k in ("skills_called", "subagents", "slash_commands"):
        for kind in ("own", "installed"):
            used.update(H[k].get(kind, {}))
    sk_rows = []
    for it in own["skills"]:
        src = f"installed from {it['source']}" if it.get("source") else ("installed (linked)" if it.get("is_symlink")
                                                                        else "written or copied in (no install record)")
        sk_rows.append([it["name"], src, n(used.get(it["name"], 0))])
    for it in own["agents"]:
        sk_rows.append([it["name"] + " (agent)", "your own agent file", n(used.get(it["name"], 0))])
    sk_rows.sort(key=lambda r: -int(r[2].replace(",", "")))
    unused = [r[0] for r in sk_rows if r[2] == "0"]
    hooks = Counter()
    for s in sessions:
        hooks.update(s.hook_sources)
    plugins_on = [p["plugin"].split("@")[0] for p in S["plugins"]["installed"] if p.get("enabled")]
    sections.append({
        "id": "built", "title": "What you built for yourself",
        "lead": f"{len(own['skills'])} skills in your setup ({sum(1 for i in own['skills'] if not i.get('is_symlink'))} without an install "
                f"record) and {len(own['agents'])} agents of your own; {len(sk_rows) - len(unused)} got used this month, {len(unused)} not yet.",
        "points": [
            "Hooks that actually add something to the conversation: "
            + ", ".join(f"{k.replace('? · ', 'unnamed · ')} ({n(c)})" for k, c in hooks.most_common(6)) + "." if hooks else "",
            f"Plugins switched on ({len(plugins_on)}): " + ", ".join(plugins_on) + ".",
            "Not yet used in the last 30 days: " + ", ".join(unused[:12]) + ("…" if len(unused) > 12 else "") + "." if unused else "",
        ],
        "table": {"caption": "Your own skills and agents, by use", "columns": ["Name", "Source", "Times used"], "rows": sk_rows[:15]},
    })

    # ── unattended ───────────────────────────────────────────────────────────
    from scan import runner_kind
    rk = defaultdict(lambda: {"runs": 0, "first": None, "last": None, "kinds": Counter()})
    for s in auto:
        k = runner_kind(s)
        if k == "trial runs in a temp folder":
            continue
        g = rk[repo(s)]
        g["runs"] += 1
        g["kinds"][k] += 1
        if s.first:
            g["first"] = min(g["first"] or s.first, s.first)
            g["last"] = max(g["last"] or s.first, s.first)
    run_rows = [[r, n(g["runs"]), g["kinds"].most_common(1)[0][0], f"{day(g['first'])} – {day(g['last'])}" if g["first"] else "–"]
                for r, g in sorted(rk.items(), key=lambda kv: -kv[1]["runs"])[:8]]
    by = E["headless_by_starter"]
    total_real = sum(g["runs"] for g in rk.values())
    sections.append({
        "id": "unattended", "title": "When you're away from the keyboard",
        "lead": f"Your own code started the agent {n(total_real)} times in {days} days, in {len(rk)} projects.",
        "points": [
            f"{n(by.get('own code calling the Agent SDK', 0))} runs from Python or TypeScript code using the Agent SDK; "
            f"{n(by.get('claude -p from a script or tool', 0))} from `claude -p` in scripts; {n(by.get('eval runs', 0))} eval runs.",
            f"{E['headless_batches']} times it worked through a batch of 5 or more runs back to back.",
            f"{E['scheduled_fires']} runs fired from a schedule.",
            f"Plus {n(by.get('trial runs in a temp folder', 0))} short trial runs in temp folders, not counted above.",
        ],
        "table": {"caption": "What runs the agent without you", "columns": ["Project", "Runs", "Started by", "Active"], "rows": run_rows},
    })

    LOGS = "session logs (~/.claude/projects/*/*.jsonl), sessions you typed in"
    FLOW_HOW = {
        "where": {"source": LOGS + "; git root of each session's folder", "rule": "sessions grouped by repo; prompt hours in local time; length = first to last record",
                  "confidence": "solid", "caveat": "temp-folder sessions are left out of the tables"},
        "start": {"source": LOGS, "rule": "explored first = a Read, Grep, Glob or LSP call, a shell cat/head/sed/grep/ls (after any cd), an Explore/Plan helper, or a read by any helper came before the first code edit; plan = plan permission mode or ExitPlanMode",
                  "raw": f"{expl_yes} of {len(explored)} sessions that edited code; {len(planned)} planned", "confidence": "heuristic",
                  "caveat": "a session that only edits docs or config gets no 'explored first' reading; examples are picked at random"},
        "handover": {"source": LOGS + "; prompt text read on this machine", "rule": "words per message Claude Code marks as typed or queued (messages from a peer session, a hook or the system aren't counted); regexes for a file path, pasted code or error, and 'done' words (should, must, make sure …)",
                     "raw": f"{P} prompts", "confidence": "guess",
                     "caveat": "the word lists are English-only and coarse: judging how precise a prompt is takes a reader, not a pattern match"},
        "check": {"source": LOGS + "; Bash commands", "rule": "a commit is checked when a test, lint or review step came after the last code edit in that session",
                  "raw": f"{K} of {C} commits", "confidence": "heuristic",
                  "caveat": "tests run by a hook, CI or another session aren't visible here, and neither are commits made outside the agent"},
        "wrong": {"source": LOGS + "; prompt text read on this machine", "rule": "a correction = a later prompt that starts with no / wrong / revert / actually / I said …; it 'became a rule' when instructions, a skill, an agent, a hook or settings changed later in the same session",
                  "raw": f"{len(corr)} corrections, {len(landed_ex)} followed by a tooling change", "confidence": "guess",
                  "caveat": "the patterns are English-only; a polite correction ('could you instead…') can slip past; the tooling change may be unrelated to the correction"},
        "built": {"source": "files on disk: ~/.claude/skills, ~/.agents/.skill-lock.json, ~/.claude/agents, plugins/installed_plugins.json; " + LOGS,
                  "rule": "installed = in the skills.sh lock or a symlink; uses = Skill tool calls, helper types and slash commands with that name",
                  "confidence": "solid", "caveat": "a skill you copied in by hand counts as 'no install record'"},
        "unattended": {"source": LOGS + "; headless sessions (entrypoint sdk-*)",
                       "rule": "who started it: temp folder → trial; 'eval' in the path → eval; sdk-py/sdk-ts → Agent SDK code; agent setting → named agent; else claude -p",
                       "confidence": "heuristic", "caveat": "runs started by a plugin you didn't write (for example an observer hook) can land in 'claude -p from a script'",
                       "code": "scan.runner_kind"},
    }
    # The rebuilt views (what happened next, who made it happen, what it cost): they replace the count-only ones.
    import built as _built
    import checks as _checks
    from scan import canonical_root as _git_root
    rebuilt = {"check": _checks.build(human, days, _git_root, lambda p: p.name),
               "built": _built.build(scan, sessions, human)}
    sections = [rebuilt.get(s["id"], s) for s in sections]
    for sec in sections:
        sec["points"] = [p for p in sec.get("points", []) if p]
        sec["part"] = "How a task goes"
        if sec["id"] in FLOW_HOW and "how" not in sec:
            sec["how"] = FLOW_HOW[sec["id"]]
    # Part one, how they orchestrate, leads; "while it works" is covered there by helpers and sessions.
    import orchestration
    orch, style, orch_glance = orchestration.sections(scan, human, auto, repo, ratio)
    sections = orch + [s for s in sections if s["id"] != "while"]

    # What each view may claim (limits.py): no logs or too few moments → "can't tell"; and what it read.
    F = H["coverage"]
    sess = lambda n_, what: (n_, what, "sessions", False)
    default_reqs = [sess(T, "sessions")]
    reqs = {
        "helpers": default_reqs + [(sum(sub.values()), "helpers", "moments", True)],
        "start": default_reqs + [sess(len(explored), "sessions that changed code")],
        "handover": [(P, "prompts", "moments", False)],
        "wrong": default_reqs + [(len(corr), "corrections", "moments", False)],
        "unattended": [],
    }
    extra = {
        "context": {"read": [f"instruction files in the {len(S['repos_worked_in']['top'])} busiest of {S['repos_worked_in']['count']} repos"]},
        "handover": {"read": [f"{n(P)} typed messages, for their length and what they name"]},
        "wrong": {"read": [f"{n(P)} typed messages checked for corrections"]},
        "unattended": {"skipped": [f"{n(by.get('trial runs in a temp folder', 0))} trial runs in temp folders"]},
    }
    for sec in sections:
        own_reqs = sec.pop("_gate", None)
        limits.gate(sec, own_reqs if own_reqs is not None else reqs.get(sec["id"], default_reqs), F["logs"])
        sec["coverage"] = limits.coverage(sec["id"], F, {**extra.get(sec["id"], {}), **(sec.pop("_coverage", None) or {})})
    if not dev:
        flatten(sections)

    mc = S.get("my_commits_30d") or {}
    glance = [
        {"value": f"{H['sessions']['active_days']} of {days}", "label": "days with agent work"},
        {"value": f"~{round(E['sessions_typed_per_active_day'])}", "label": "sessions a day"},
        {"value": str(H["sessions"]["max_sessions_open_at_once"]), "label": "sessions at once, at most"},
        {"value": ratio(K, C), "label": "commits checked after the last change"},
        {"value": n(total_real), "label": "agent runs started by your own code"},
    ]
    top = by_repo.most_common(1)[0][0] if by_repo else "?"
    para = (f"You work with agents on {H['sessions']['active_days']} of {days} days, about {round(E['sessions_typed_per_active_day'])} "
            f"sessions a day, mostly in {top}. You let the agent look around before it changes anything "
            f"({pct(expl_yes, len(explored))}% of sessions) and hand work over in short asks of about {words[P // 2] if P else 0} words. "
            f"{ratio(K, C).capitalize()} commits are checked after the last change. "
            f"When the agent gets it wrong, {ratio(len(landed_ex), len(corr))} times a rule or skill changes in the same session. "
            f"Your own code started the agent {n(total_real)} times without you typing.")
    if mc.get("total"):
        glance.append({"value": f"{n(mc['agent_marked'])} of {n(mc['total'])}", "label": "of your commits marked as agent co-written"})

    why = limits.no_logs_text(F["logs"]) or (limits.cant_tell(T, "sessions", "sessions") if limits.too_few(T, "sessions") else None)
    if why:
        orch_glance = [{**g, "value": "not visible yet"} for g in orch_glance]
        style = para = why
    return {
        "schema": "agentic-story/1",
        "person": person,
        "generated": scan["generated"],
        "window_days": days,
        "privacy": "detail",
        "at_a_glance": orch_glance,
        "in_one_paragraph": style,
        "task_paragraph": para,   # the task-flow summary, kept for part two
        "coverage": {"logs": F["logs"], "versions": F["versions"], "entrypoints": F["entrypoints"],
                     "line": limits.coverage("sessions", F)["line"]},
        "sections": sections,
        "v13": {"practices": [{"id": p["id"], "verdict": p["verdict"]} for b in (v13 or {}).get("badges", []) for p in b["practices"]]},
        **({"dev": dev} if dev else {}),
    }
