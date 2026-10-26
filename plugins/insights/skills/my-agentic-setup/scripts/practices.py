"""practices — turn a masked scan into report.json: the v13 practices, each with a state, the evidence
behind it, and a question we may ask.

The practice ids are v13's (labs/v13/substrate/practices.yaml, v13.14). The states are this scanner's
reading of the logs. Three states (the keys stay; render_report.py shows them to members as "Seen in your
logs", "Some signs" and "Let's talk about it"):
  seen   the logs show it, and the evidence says how often
  signs  partial or indirect evidence: worth a question
  ask    the logs hold too little to say. Not a negative: a conversation can fill it in

Every evidence line is plain English with its number in it, and carries counts only, no names: the
report is built from the masked scan, and a line never quotes a repo, a skill or an MCP server.
Thresholds are first guesses from one heavy user's machine (2026-10-19); tune them on member runs.
"""
from __future__ import annotations

import re

import limits

BADGES = [
    ("harness", "Harness", "What you built around the agent."),
    ("context", "Context", "What the agent knows before it starts."),
    ("prompt", "Prompt", "How you hand work to the agent."),
    ("loop", "Loop", "How work gets checked, and whether it runs without you."),
]

# v13 practice → (badge, title). Order is the order on the page.
PRACTICES = [
    ("harness-1", "harness", "Daily agent practice"),
    ("harness-2", "harness", "Custom tooling"),
    ("harness-4", "harness", "System integration"),
    ("harness-5", "harness", "Multi-agent orchestration"),
    ("harness-6", "harness", "Model routing"),
    ("harness-7", "harness", "Hooks"),
    ("harness-8", "harness", "Agent infrastructure"),
    ("harness-9", "harness", "Limits and watching the run"),
    ("harness-10", "harness", "Machinery the agent runs inside"),
    ("context-7", "context", "Instruction layer ownership"),
    ("context-8", "context", "Instruction scoping"),
    ("context-10", "context", "Memory across sessions"),
    ("context-11", "context", "Instruction reach"),
    ("context-9", "prompt", "Session management"),       # v13 files context-9 under the prompt badge
    ("prompt-11", "prompt", "Plan-first development"),
    ("prompt-12", "prompt", "Prompt precision"),
    ("prompt-13", "prompt", "Prompt accountability"),
    ("loop-14", "loop", "Review gates"),
    ("loop-15", "loop", "Independent verification"),
    ("loop-16", "loop", "Output ownership"),
    ("loop-17", "loop", "Failure capture"),
    ("loop-18", "loop", "Bottleneck awareness"),
    ("loop-19", "loop", "Your own runner"),
    ("loop-20", "loop", "Runs that start without you"),
    ("loop-21", "loop", "Working through a set on its own"),
    ("loop-22", "loop", "Recovering and improving on its own"),
    ("loop-23", "loop", "Runs evals"),
]


def n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else str(x)


def of(text: str) -> tuple[int, int]:
    """'78 of 541' → (78, 541)."""
    m = re.match(r"(\d+) of (\d+)", str(text))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def pct(a, b) -> int:
    return round(100 * a / b) if b else 0


class Facts:
    """The masked scan, flattened into what the practice readers need."""

    def __init__(self, r: dict):
        self.r = r
        S, H = r["setup"], r["habits"]
        self.E = H["evidence"]
        self.H, self.S = H, S
        self.ws = H["working_style"]
        self.sess = H["sessions"]
        self.typed = self.sess["with_a_person_typing"]
        self.days = H["window_days"]
        us = S["settings"].get("user") or {}
        self.us = us
        own = S["own"]
        self.skills_written = sum(1 for x in own["skills"] if not x.get("is_symlink"))
        self.skills_installed = sum(1 for x in own["skills"] if x.get("is_symlink"))
        self.own_agents, self.own_commands = len(own["agents"]), len(own["commands"])
        self.own_styles = len(own["output_styles"])
        self.own_calls = sum(sum(H[k].get("own", {}).values()) for k in ("skills_called", "subagents", "slash_commands"))
        self.own_hooks = sum((us.get("hooks_by_event") or {}).values())
        inst = S["plugins"]["installed"]
        self.plugins_installed = len(inst)
        self.plugins_enabled = sum(1 for p in inst if p.get("enabled"))
        self.plugin_hook_events = sum(len(p.get("hook_events") or []) for p in inst if p.get("enabled"))
        self.own_plugins = sum(1 for p in inst if p.get("local_marketplace"))
        self.per_day = round(self.E["sessions_typed_per_active_day"])
        self.repos = S["repos_worked_in"]["top"]
        self.repo_count = S["repos_worked_in"]["count"]
        self.mcp_used = H["mcp_servers_used"]
        self.mach = S.get("machinery") or {}
        self.models = H["models"]


# ───────────────── one reader per practice: (verdict, evidence lines, ask) ─────────────────

def harness_1(f: Facts):
    active = f.sess["active_days"]
    mc = f.S.get("my_commits_30d") or {}
    commits, marked = mc.get("total", 0), mc.get("agent_marked", 0)
    ev = [f"You worked with an agent on {active} of {f.days} days, about {f.per_day} sessions a day."]
    if commits:
        ev.append(f"{n(marked)} of your {n(commits)} commits in these repos carry an agent co-author line "
                  f"(the real number may be higher, because many setups drop that line).")
    v = "seen" if active >= f.days * 0.5 else ("signs" if active >= 4 else "ask")
    return v, ev, "Is there anything you still do by hand because it's quicker than asking the agent? We'd like to hear what, and why."


def harness_2(f: Facts):
    made = f.skills_written + f.own_agents + f.own_commands
    ev = []
    if made:
        ev.append(f"You made {f.skills_written} skills, {f.own_agents} agents and {f.own_commands} commands of your own"
                  + (f", plus {f.own_styles} output styles." if f.own_styles else "."))
        ev.append(f"You called your own tools {n(f.own_calls)} times in {f.days} days.")
    if f.skills_installed:
        ev.append(f"{f.skills_installed} more skills installed from others.")
    v = "seen" if made and f.own_calls else ("signs" if made or f.skills_installed else "ask")
    return v, ev, "Which of the tools you built would you miss most, and what did it replace for you?"


def harness_4(f: Facts):
    integ = f.E["integrations"]
    servers = len(f.mcp_used)
    ev = []
    if servers:
        ev.append(f"The agent called {servers} different MCP servers, {n(sum(f.mcp_used.values()))} calls in all.")
    if integ:
        top = sorted(integ.items(), key=lambda kv: -kv[1])[:4]
        ev.append("Outside systems reached from the shell: " + ", ".join(f"{k} ({n(c)})" for k, c in top) + ".")
    v = "seen" if servers >= 2 or len(integ) >= 3 else ("signs" if servers or integ else "ask")
    return v, ev, "When the agent works with outside systems, which one needs the most care, and how do you set its limits?"


def harness_5(f: Facts):
    sub, tot = of(f.ws["sessions_starting_subagents"])
    wt, _ = of(f.ws["sessions_in_worktrees"])
    par = f.sess["max_sessions_open_at_once"]
    ev = [f"You started helper agents in {sub} of {tot} sessions ({pct(sub, tot)}%).",
          f"You had up to {par} sessions open at once; {wt} sessions worked in a separate git worktree."]
    tc = f.H["tool_calls"]
    team = tc.get("SendMessage", 0) + tc.get("Workflow", 0)
    if team:
        ev.append(f"{n(team)} messages or workflow runs between agents.")
    v = "seen" if pct(sub, tot) >= 10 or par >= 3 else ("signs" if sub else "ask")
    return v, ev, "When several agents run at once, how do you keep their work apart?"


def harness_6(f: Facts):
    ev = [f"You used {len(f.models)} models; {f.E['sessions_multi_model']} sessions mixed more than one."]
    if f.E["subagent_calls_with_model"]:
        ev.append(f"You picked the model for a helper agent {n(f.E['subagent_calls_with_model'])} times.")
    multi = pct(f.E["sessions_multi_model"], f.typed)
    v = "seen" if f.E["subagent_calls_with_model"] >= 5 or multi >= 15 else ("signs" if len(f.models) > 1 else "ask")
    return v, ev, "How do you decide which model gets which job? Tell us about a time that choice helped."


def harness_7(f: Facts):
    fired, tot = of(f.ws["sessions_where_hooks_fired"])
    repo_hooks = sum(1 for r in f.repos if r.get("project_hooks"))
    ev = [f"{f.own_hooks} hooks in your own settings, {repo_hooks} repos with project hooks, "
          f"{f.plugin_hook_events} hook events from switched-on plugins.",
          f"Hooks fired in {fired} of {tot} sessions."]
    if f.E["hook_blocks"]:
        ev.append(f"A hook stopped or cancelled something {n(f.E['hook_blocks'])} times.")
    mine = f.own_hooks + repo_hooks
    v = "seen" if mine and fired else ("signs" if fired else "ask")
    if v == "signs":
        ev.append("So far, every hook that ran came from a plugin rather than from you.")
    return v, ev, "Would you show us a hook you wrote and tell us what it does for your work with the agent?"


def harness_8(f: Facts):
    runner_files = sum(r.get("files_that_run_agents", 0) for r in f.repos)
    agents = sum(f.E["main_agent_settings"].values())
    ev = []
    if f.own_plugins:
        ev.append(f"{f.own_plugins} plugins installed from a marketplace folder you keep on this machine.")
    if runner_files:
        ev.append(f"{runner_files} files in your repos start agents (Agent SDK, claude -p, CI action).")
    if agents:
        ev.append(f"You started {agents} sessions as a custom main agent (`--agent`).")
    v = "seen" if (f.own_plugins and runner_files) or runner_files >= 3 else ("signs" if ev else "ask")
    return v, ev, "Who else uses what you built, and what did you learn from how they use it?"


def harness_9(f: Facts):
    perms = f.us.get("permissions") or {}
    modes = f.ws["permission_modes_seen"]
    bypass = modes.get("bypassPermissions", 0)
    ev = [f"{perms.get('deny', 0)} deny rules and {perms.get('allow', 0)} allow rules; default mode "
          f"'{perms.get('default_mode') or 'default'}'.",
          f"{bypass} of {f.typed} sessions ran with all permission checks off."]
    if f.E["headless_bounded_calls"]:
        ev.append(f"You capped {n(f.E['headless_bounded_calls'])} headless runs with turn, tool or permission limits.")
    bounds = perms.get("deny", 0) + f.E["hook_blocks"] + f.E["headless_bounded_calls"]
    v = "seen" if bounds and pct(bypass, f.typed) < 50 else ("signs" if bounds else "ask")
    return v, ev, "Which risks in your setup do you think about most, and what keeps them in check?"


def harness_10(f: Facts):
    ev = []
    if f.mach.get("launcher") == "wrapper":
        ev.append("You start Claude through your own launcher instead of the standard one.")
    if f.mach.get("custom_endpoint"):
        ev.append("Model calls go through a custom endpoint or key helper.")
    dc = sum(1 for r in f.repos if r.get("has_devcontainer"))
    if dc:
        ev.append(f"{dc} repos run the agent inside a devcontainer.")
    sdk = f.E["headless_by_starter"].get("own code calling the Agent SDK", 0)
    if sdk:
        ev.append(f"{n(sdk)} runs from your own code calling the Agent SDK.")
    v = "seen" if len(ev) >= 2 else ("signs" if ev else "ask")
    return v, ev, "What wraps the agent in your setup, and how does what it sees differ from a standard install?"


def context_7(f: Facts):
    have = sum(1 for r in f.repos if r.get("claude_md_lines") or r.get("agents_md"))
    edits = f.E["instruction_edits_by_kind"].get("instructions", 0)
    c, landed, cs = f.E["corrections"], f.E["corrections_landed_same_session"], f.E["sessions_with_corrections"]
    ev = [f"{have} of the {len(f.repos)} repos you work in most have an instruction file."]
    if edits:
        ev.append(f"The agent edited instruction files {n(edits)} times. Who decided the content isn't visible in your logs.")
    if c:
        ev.append(f"You corrected the agent {c} times; {landed} of those were followed, in the same session, by a change "
                  f"to instructions, a skill or a hook.")
    v = "seen" if have and landed else ("signs" if have else "ask")
    return v, ev, "Who decides what goes into your CLAUDE.md, you or the agent? We'd like to hear about the last line you added, and why."


def context_8(f: Facts):
    toks = [r["instructions"]["always_loaded_tokens"] for r in f.repos
            if isinstance(r.get("instructions"), dict) and r["instructions"].get("always_loaded_tokens")]
    nested = sum(1 for r in f.repos if isinstance(r.get("instructions"), dict) and r["instructions"].get("nested_claude_md"))
    skills = sum(r.get("project_skills", 0) for r in f.repos) + f.skills_written
    ev = []
    if toks:
        toks.sort()
        ev.append(f"Loads about {n(toks[len(toks) // 2])} tokens of instructions every turn (median over {len(toks)} repos).")
    if nested:
        ev.append(f"{nested} repos split instructions into folder-level files.")
    if skills:
        ev.append(f"{skills} skills hold knowledge that loads only when it's needed.")
    v = "seen" if toks and (nested or skills >= 3) else ("signs" if toks else "ask")
    return v, ev, "As a project grows, how do you keep what loads every turn small?"


def context_10(f: Facts):
    mem = f.E["instruction_edits_by_kind"].get("memory", 0)
    auto = "autoMemoryEnabled" in (f.us.get("keys_set") or [])
    ev = []
    if mem:
        ev.append(f"Your sessions wrote to memory or notes files {n(mem)} times.")
    if auto:
        ev.append("You set Claude Code's auto-memory setting yourself.")
    v = "seen" if mem >= 10 else ("signs" if mem or auto else "ask")
    return v, ev, "How do you pass what today's session learned on to tomorrow's?"


def context_11(f: Facts):
    committed = sum(1 for r in f.repos if r.get("instructions_committed"))
    shared = sum(1 for r in f.repos if r.get("instructions_authors", 0) > 1)
    ev = [f"Instruction files are committed for the team in {committed} of {len(f.repos)} repos."]
    if committed:
        ev.append(f"In {shared} of them someone else has also edited the file.")
    v = "seen" if shared else ("signs" if committed else "ask")
    return v, ev, "Does anyone else on the team work from your instruction files? We'd like to hear what they changed."


def context_9(f: Facts):
    cc = f.E["sessions_with_clear_or_compact"]
    comp, tot = of(f.ws["sessions_compacted"])
    ev = [f"A typical session runs about {f.E['session_minutes_median']} minutes and "
          f"{f.sess['median_prompts_per_session']} prompts.",
          f"You cleared or compacted the conversation on purpose in {cc} sessions; it ran out and compacted itself in {comp}."]
    v = "seen" if pct(cc, f.typed) >= 5 else "signs"
    return v, ev, "How do you decide when to start a fresh session instead of continuing, and what do you carry over?"


def prompt_11(f: Facts):
    plan, tot = of(f.ws["sessions_using_plan_mode"])
    exp, etot = of(f.E["explored_before_first_edit"])
    ev = [f"You used plan mode in {plan} of {tot} sessions ({pct(plan, tot)}%).",
          f"The agent read or searched before its first code edit in {exp} of {etot} sessions."]
    v = "seen" if pct(plan, tot) >= 10 else ("signs" if plan or pct(exp, etot) >= 70 else "ask")
    return v, ev, "Thinking of your last bigger change: what did you agree with the agent before it wrote any code?"


def prompt_12(f: Facts):
    t = f.E["prompts_total"]
    ev = [f"A typical prompt is {f.E['prompt_words_median']} words; one in ten is over {f.E['prompt_words_p90']}.",
          f"{pct(f.E['prompts_naming_a_file'], t)}% name a file or path, {pct(f.E['prompts_with_code_or_error'], t)}% paste "
          f"code or an error, {pct(f.E['prompts_stating_done'], t)}% say what 'done' looks like."]
    return "signs", ev, "Could you share a prompt you'd write for a real task this week? What makes it clear to the agent?"


def prompt_13(f: Facts):
    ev = [f"You corrected the agent in {f.E['corrections']} of {n(f.E['prompts_total'])} prompts and interrupted it "
          f"{f.ws['interrupts']} times."]
    return "ask", ev, "Think of a time the agent got it wrong. What would you change in your prompt next time?"


def loop_14(f: Facts):
    diff = f.E["sessions_with_git_diff"]
    rev = f.E["sessions_with_review_step"]
    prs = f.H["bash_families"].get("pr_create", 0)
    ev = [f"You looked at the diff in {diff} of {f.typed} sessions and ran a review agent or skill in {rev}."]
    if prs:
        ev.append(f"You opened {prs} pull requests from agent sessions.")
    v = "seen" if rev or pct(diff, f.typed) >= 20 else ("signs" if diff else "ask")
    return v, ev, "What does a change go through before it reaches the client's main branch?"


def loop_15(f: Facts):
    c, k = f.E["commits_in_sessions"], f.E["commits_checked_after_last_edit"]
    es, ec = f.E["sessions_with_code_edits"], f.E["sessions_ending_checked"]
    ev = []
    if c:
        ev.append(f"{n(k)} of {n(c)} commits ({pct(k, c)}%) came after a test, lint or review run that followed the last code change.")
    if es:
        ev.append(f"{ec} of {es} sessions that changed code ended with a check after the last change.")
    v = "seen" if c and pct(k, c) >= 60 else ("signs" if k or ec else "ask")
    return v, ev, "Who or what checks the agent's work besides the agent that wrote it?"


def loop_16(f: Facts):
    c, k = f.E["commits_in_sessions"], f.E["commits_checked_after_last_edit"]
    ev = [f"For {n(c - k)} commits, a check after the last change isn't visible in your logs."] if c else []
    return "ask", ev, "Take a change the agent wrote last week. How would you walk the client through it, line by line?"


def loop_17(f: Facts):
    landed, cs = f.E["corrections_landed_same_session"], f.E["sessions_with_corrections"]
    tooling = f.E["sessions_editing_own_tooling"]
    ev = [f"After a correction, a rule, skill or hook changed in the same session {landed} times "
          f"({f.E['sessions_editing_tooling_when_corrected']} of {cs} corrected sessions).",
          f"You changed your own instructions or tooling in {tooling} of {f.typed} sessions overall."]
    v = "seen" if landed >= 3 else ("signs" if landed or tooling else "ask")
    return v, ev, "Think of a mistake the agent made twice. What did you change to help it get it right?"


def loop_18(f: Facts):
    ev = [f"You interrupted the agent {f.ws['interrupts']} times and ran up to {f.sess['max_sessions_open_at_once']} sessions at once."]
    return "ask", ev, "Where does your time go now: writing, waiting, or reviewing? What have you done to make that better?"


def loop_19(f: Facts):
    by = f.E["headless_by_starter"]
    sdk = by.get("own code calling the Agent SDK", 0)
    script = by.get("claude -p from a script or tool", 0)
    trials = by.get("trial runs in a temp folder", 0)
    runner_files = sum(r.get("files_that_run_agents", 0) for r in f.repos)
    ev = []
    if sdk or script:
        ev.append(f"{n(sdk + script)} agent runs started by code, not by you typing "
                  f"({n(sdk)} from the Agent SDK, {n(script)} from `claude -p`).")
    if runner_files:
        ev.append(f"{runner_files} files in your repos start agents.")
    if trials:
        ev.append(f"Plus {n(trials)} short trial runs in temp folders.")
    v = "seen" if (sdk + script) >= 20 and runner_files else ("signs" if sdk + script or runner_files else "ask")
    return v, ev, "Could you walk us through the runner? What starts it, what does it hand back, and who reads that?"


def loop_20(f: Facts):
    sched = f.E["scheduled_fires"]
    ci = sum(1 for r in f.repos if r.get("has_ci_agent"))
    tc = f.H["tool_calls"]
    timers = tc.get("CronCreate", 0) + tc.get("ScheduleWakeup", 0)
    ev = []
    if sched:
        ev.append(f"{sched} runs fired from a schedule.")
    if ci:
        ev.append(f"{ci} repos run an agent in CI.")
    if timers:
        ev.append(f"You set {timers} timers or recurring jobs from inside a session.")
    v = "seen" if sched or ci else ("signs" if timers else "ask")
    return v, ev, "What runs while you're away, and how do you find out how it went?"


def loop_21(f: Facts):
    b = f.E["headless_batches"]
    wf = f.H["tool_calls"].get("Workflow", 0)
    ev = []
    if b:
        ev.append(f"{b} batches of 5 or more automated runs back to back in one project.")
    if wf:
        ev.append(f"{wf} multi-step workflow runs.")
    v = "seen" if b >= 3 else ("signs" if b or wf else "ask")
    # v13 wording watch: parallel agents are not set-walking. The question separates them.
    return v, ev, "Where does the list come from, and who decides when it's done: you or the system?"


def loop_22(f: Facts):
    h = f.E["headless_editing_own_tooling"]
    ev = [f"{h} automated runs changed instructions, skills or hooks themselves."] if h else []
    return ("signs" if h else "ask"), ev, "If your runner hits a problem overnight, what happens next while you're away?"


def loop_23(f: Facts):
    runs = f.E["headless_by_starter"].get("eval runs", 0)
    cmds = f.E["eval_commands"]
    dirs = sum(1 for r in f.repos if r.get("has_evals_dir"))
    ev = []
    if runs or cmds:
        ev.append(f"{n(runs)} automated eval runs and {n(cmds)} eval commands.")
    if dirs:
        ev.append(f"{dirs} repos keep an evals folder.")
    v = "seen" if runs or cmds >= 5 else ("signs" if dirs else "ask")
    return v, ev, "Tell us about an eval that caught a regression. What would have shipped without it?"


READERS = {pid: globals()[pid.replace("-", "_")] for pid, _, _ in PRACTICES}


def shareable(report: dict) -> dict:
    """The report as it leaves the machine: the same facts, no grading. The seen/signs/ask call stays local
    (next_steps.py uses it to pick suggestions); what Proxify receives carries no verdict (ruling 2026-10-26)."""
    out = {**report, "badges": [{**b, "practices": [{k: v for k, v in p.items() if k != "verdict"} for p in b["practices"]]}
                                for b in report["badges"]]}
    return out


def build(scan: dict, person: str = "") -> dict:
    f = Facts(scan)
    # No logs, or too few sessions: the habit numbers below are not claims. A verdict stays what its reader said
    # (a setup read from disk can still be "seen"; "ask" already means the logs can't tell), but the page says why.
    logs = (scan["habits"].get("coverage") or {}).get("logs") or {}
    why = limits.no_logs_text(logs) or (limits.cant_tell(f.typed, "sessions", "sessions") if limits.too_few(f.typed, "sessions") else None)
    badges = []
    tally = {}
    for bid, btitle, blurb in BADGES:
        ps = []
        for pid, b, title in PRACTICES:
            if b != bid:
                continue
            v, ev, ask = READERS[pid](f)
            ps.append({"id": pid, "title": title, "verdict": v, "evidence": ([why] if why else []) + ev, "ask": ask})
        tally[bid] = sum(p["verdict"] == "seen" for p in ps), len(ps)
        badges.append({"id": bid, "title": btitle, "blurb": blurb, "practices": ps})

    E, sess = f.E, f.sess
    by = E["headless_by_starter"]
    code_runs = by.get("own code calling the Agent SDK", 0) + by.get("claude -p from a script or tool", 0)
    c, k = E["commits_in_sessions"], E["commits_checked_after_last_edit"]
    headline = [
        {"value": f"{sess['active_days']} of {f.days}", "label": "days with agent work"},
        {"value": n(f.typed), "label": "sessions you typed in"},
        {"value": str(sess["max_sessions_open_at_once"]), "label": "sessions open at once, at most"},
        {"value": n(code_runs), "label": "agent runs started by your own code"},
        {"value": f"{pct(k, c)}%" if not limits.too_few(c) else "not visible yet", "label": "of commits checked after the last change"},
    ]
    if why:
        headline = [{**h, "value": "not visible yet"} for h in headline]
    best = max(tally.items(), key=lambda kv: kv[1][0] / kv[1][1])
    weakest = min(tally.items(), key=lambda kv: kv[1][0] / kv[1][1])
    title = dict((b, t) for b, t, _ in BADGES)
    summary = [
        f"You work with agents on {sess['active_days']} of {f.days} days, about {f.per_day} sessions a day, "
        f"up to {sess['max_sessions_open_at_once']} at once.",
        "The areas below show what your logs reveal about your setup. Where they hold too little to say, "
        "we can talk it through together.",
    ] if not why else [why]
    if code_runs and not why:
        summary.append(f"Your own code started {n(code_runs)} agent runs, rather than you typing them: you build runners.")
    if c and not limits.too_few(c) and not why:
        summary.append(f"{pct(k, c)}% of commits came after a check that followed the last change. For the rest, a check isn't visible in your logs.")

    S = scan["setup"]
    tools = S.get("other_agent_tools") or {}
    live = [f"{k} ({v['days_since_touched']} days ago)" for k, v in sorted(tools.items(), key=lambda kv: kv[1]["days_since_touched"])
            if v["days_since_touched"] <= 30]
    setup = [
        {"label": "Plugins", "value": f"{f.plugins_installed} installed, {f.plugins_enabled} switched on"},
        {"label": "Your own skills", "value": f"{f.skills_written} written, {f.skills_installed} installed from others"},
        {"label": "Your own agents · commands · output styles", "value": f"{f.own_agents} · {f.own_commands} · {f.own_styles}"},
        {"label": "Hooks", "value": f"{f.own_hooks} of your own, {f.plugin_hook_events} hook events from plugins"},
        {"label": "MCP servers", "value": f"{len(S['mcp']['user_servers'])} configured, {len(f.mcp_used)} used"},
        {"label": "Repos worked in", "value": str(f.repo_count)},
        {"label": "Other agent tools used this month", "value": ", ".join(live) or "none"},
    ]
    cannot = [
        "Work on other machines, client VMs or devcontainers: we read only this machine's Claude Code logs.",
        f"Anything older than {f.days} days.",
        "How you use other agent tools: we see only that they're there and when you last touched them.",
        "Edits you make by hand in an editor: your logs show only what went through the agent.",
    ]
    privacy = [
        "This file holds names and counts only. It leaves out prompts, code, file contents and command lines.",
        "The scan read your prompts on this machine to count corrections and file mentions. Their text isn't in this report.",
        "Repos and anything you named yourself are left out or replaced with labels.",
        "Nothing was sent. This file goes wherever you choose to put it.",
    ]
    return {
        "schema": "agentic-setup-report/1",
        "person": person,
        "generated": scan["generated"],
        "window_days": f.days,
        "masked": True,
        "headline": headline,
        "summary": summary,
        "badges": badges,
        "setup": setup,
        "cannot_see": cannot,
        "privacy": privacy,
    }
