"""orchestration — part one of the story: how you run agents.

Sessions (how you start, branch, come back), helpers (what gets handed off, in the background or not),
sessions talking to each other, memory across sessions, the context you set up, which Claude Code
built-ins you use and which you have not used yet, and how far your setup goes.

Same rules as story.py: DETAIL version (real names and excerpts), every sentence carries its number,
a sentence the data cannot back is left out.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

# Claude Code's built-in features, in plain words. Tools are what the agent calls; commands are what the
# person types. A feature not listed here is a plugin's or the person's own and is reported elsewhere.
BUILTIN_TOOLS = {
    "Agent": "hand a piece of work to a helper agent",
    "SendMessage": "message another agent or another session",
    "ListAgents": "see which agents and sessions are running",
    "Workflow": "run a scripted, multi-agent workflow",
    "Monitor": "watch a running job until something happens",
    "TaskStop": "stop a background job or helper",
    "TaskOutput": "read what a background job printed",
    "ScheduleWakeup": "have the session wake itself up later",
    "CronCreate": "schedule a recurring job",
    "EnterWorktree": "work in a separate copy of the repo",
    "ExitPlanMode": "finish a plan and ask to go ahead",
    "EnterPlanMode": "switch to planning before changing anything",
    "AskUserQuestion": "the agent asks you a multiple-choice question",
    "ToolSearch": "load extra tools only when they're needed",
    "Skill": "the agent loads a skill on its own",
    "WebSearch": "search the web",
    "WebFetch": "read a web page",
    "Artifact": "publish a page you can share",
    "TodoWrite": "keep a to-do list for the task (older)",
    "TaskCreate": "add to a task list helpers can share",
    "TaskUpdate": "update a shared task",
    "TaskList": "read the shared task list",
    "CronList": "list scheduled jobs",
    "CronDelete": "remove a scheduled job",
    "ExitWorktree": "leave a separate repo copy",
    "PushNotification": "ping your phone or desktop",
    "LSP": "code navigation (definitions, references)",
    "NotebookEdit": "edit a Jupyter notebook",
}
# Source for the catalogue: the claude-code-guide agent's read of the docs + transcripts, Claude Code
# 2.1.303, 2026-10-19 (commands page, tools reference, sub-agents, agent teams). Re-check on a new version.
BUILTIN_COMMANDS = {
    "plan": "plan before changing anything", "compact": "shrink the conversation and keep going",
    "clear": "start the conversation over", "resume": "reopen an earlier session", "continue": "reopen the last session",
    "rewind": "roll the conversation and files back", "fork": "split the conversation into a new branch",
    "branch": "split the conversation into a new branch", "rename": "give the session a name",
    "export": "save the conversation to a file", "copy": "copy the last answer", "goal": "set a goal the session works toward",
    "loop": "repeat a prompt on a timer", "schedule": "schedule a task", "review": "review a pull request",
    "security-review": "security review of the changes", "init": "write a CLAUDE.md for the repo",
    "memory": "edit memory files", "agents": "manage helper agents", "hooks": "manage hooks", "mcp": "manage MCP servers",
    "config": "settings", "model": "switch model", "effort": "change how hard the model thinks", "fast": "fast mode",
    "add-dir": "let the session see another folder", "context": "see what fills the context", "cost": "see the cost",
    "usage": "see plan usage", "insights": "report on your own usage", "doctor": "check the install",
    "tasks": "see background jobs", "btw": "a side question that doesn't derail the task", "recap": "recap the session",
    "reload-plugins": "reload plugins without restarting", "reload-skills": "reload skills without restarting",
    "plugin": "manage plugins", "statusline": "set up the status line", "output-style": "change the output style",
    "permissions": "manage permissions", "team-onboarding": "onboard a teammate",
    "subtask": "hand a side task to a helper with this context", "bg": "move the conversation to a background session",
    "background": "move the conversation to a background session", "cd": "move the session to another folder",
    "advisor": "have a second model check key moments", "remote-control": "carry on from another device",
    "teleport": "move the session to the cloud or back", "voice": "speak instead of type",
    "workflows": "watch running workflows", "batch": "split a big change across helpers in worktrees",
    "deep-research": "parallel web research with a cited report", "autocompact": "set when it shrinks itself",
    "sandbox": "run commands in a sandbox", "simplify": "simplify the last change", "verify": "verify the change works",
    "debug": "debug a failure", "code-review": "review the change", "diff": "see the change", "status": "session status",
    "stats": "usage stats", "skills": "list skills", "ide": "connect an editor", "chrome": "connect the browser",
    "list-agents": "list helpers and teammates", "peers": "list teammates", "ultrareview": "cloud multi-agent review",
    "artifacts": "browse shared pages", "keybindings": "edit key bindings", "vim": "vim keys", "theme": "colors",
}


LOGS = "session logs (~/.claude/projects/*/*.jsonl), sessions you typed in"


def pt(text, source, rule, raw="", conf="solid", caveat=None, code=None):
    """A claim plus how it was inferred (dev mode). conf: solid = read straight from a field Claude Code
    writes; heuristic = a rule over those fields that can misfire; guess = a proxy for what we can't see."""
    if not text:
        return ""
    how = {"source": source, "rule": rule, "raw": raw, "confidence": conf}
    if caveat:
        how["caveat"] = caveat
    if code:
        how["code"] = code
    return {"text": text, "how": how}


def n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else str(x)


def pct(a, b) -> int:
    return round(100 * a / b) if b else 0


def day(ts) -> str:
    return ts.astimezone().strftime("%b %-d")


def sections(scan: dict, human: list, auto: list, repo, ratio) -> tuple[list, str, list]:
    H, S, E = scan["habits"], scan["setup"], scan["habits"]["evidence"]
    T = len(human)
    P = "How you orchestrate"
    out = []

    def tot(attr):
        c = Counter()
        for s in human:
            c.update(getattr(s, attr))
        return c

    marks, tools, cmds, bg = tot("marks"), tot("tools"), tot("commands"), tot("bg_tools")
    helpers = tot("subagents")
    H_N = sum(helpers.values())

    # ── 1 sessions ──────────────────────────────────────────────────────────
    spans, came_back, multi_day = [], 0, 0
    for s in human:
        ts = sorted(s.user_ts)
        if len(ts) > 1:
            spans.append((ts[-1] - ts[0]).total_seconds() / 3600)
            if any((b - a).total_seconds() > 3 * 3600 for a, b in zip(ts, ts[1:])):
                came_back += 1
            if ts[-1].astimezone().date() != ts[0].astimezone().date():
                multi_day += 1
    longest = max(spans) if spans else 0
    # a session that opens with a copy of another's history was forked, branched, split off or continued
    forked = sum(1 for s in human if s.marks.get("forked") or s.marks.get("copied_records"))
    cont = sum(1 for s in human if s.marks.get("continued_in"))
    # A title that is just the start of the first message is Claude Code's automatic one, not a name.
    titled = []
    for s in human:
        first = s.prompt_texts[0][2].strip()[:40].lower() if s.prompt_texts else ""
        for t in s.titles:
            # automatic titles are the opening words of a message (often a fork's parent's); a name is short
            if t and len(t.split()) <= 6 and not (first and first.startswith(t.strip()[:30].lower())) and t not in titled:
                titled.append(t)
    compacted = sum(1 for s in human if s.compactions)
    cleared = cmds.get("clear", 0)
    life_rows = [
        ["Picked back up after 3+ hours away", n(came_back)],
        ["Ran across more than one day", n(multi_day)],
        ["Carried on in a fresh session (continued-in)", n(cont)],
        ["Opened with another session's history (fork, branch, subtask)", n(forked)],
        ["Split off with /fork, /branch, /subtask or /bg",
         n(sum(cmds.get(k, 0) for k in ("fork", "subtask", "branch", "bg", "background")))],
        ["Run as a background session", n(sum(1 for s in human if s.marks.get("background_session")))],
        ["Worked toward a /goal", n(sum(1 for s in human if s.marks.get("goal_status") or s.commands.get("goal")))],
        ["Given a name", n(len(titled))],
        ["Shrunk with /compact or auto-compact", n(compacted)],
        ["Wiped with /clear", n(cleared)],
    ]
    out.append({
        "part": P, "id": "sessions", "title": "Sessions: how you start, run and end them",
        "lead": (f"Sessions are long-lived: you came back to {came_back} after hours away and {multi_day} ran across days; "
                 f"you shrink them ({compacted}) far more than you wipe them ({cleared})."),
        "how": {"source": LOGS, "rule": "per session: gaps between your own records, compact_boundary records, /clear commands",
                "raw": f"{T} sessions; {came_back} with a 3h+ gap; {multi_day} first and last record on different days",
                "confidence": "solid", "code": "orchestration.sections → sessions"},
        "points": [
            pt(f"Your longest stayed open for {round(longest / 24, 1)} days of on-and-off work." if longest >= 24 else
               f"Your longest stayed open for {round(longest)} hours.",
               LOGS, "first to last timestamp of your own records in one session file",
               f"longest span {round(longest, 1)} h", "solid",
               "a session left open but idle counts the whole span"),
            pt(f"{forked + cont} sessions branched off or carried on from another one, so a thread of work outlives one chat.",
               LOGS + "; forkedFrom field, continued-in records, records whose uuid already appeared in an older file",
               "a session counts if it carries a fork marker, a continued-in record, or opens with copied parent records",
               f"{forked} opened with another's history, {cont} continued-in", "solid",
               "a resume with `claude -r` reopens the same file and leaves no marker, so plain resumes are only seen as 'came back after 3h+'",
               "scan.first_sighting · orchestration → forked"),
            pt("Named sessions: " + ", ".join(f"“{t}”" for t in titled[:6]) + "." if titled else "",
               LOGS + "; custom-title records", "a title of 6 words or fewer that is not the start of the session's first message",
               f"{len(titled)} names kept", "heuristic",
               "Claude Code titles sessions automatically from the first message, so a short automatic title can pass as a name"),
        ],
        "table": {"caption": "What happens to a session", "columns": ["What happened", "Sessions"], "rows": life_rows,
                  "how": {"source": LOGS, "rule": "one row per marker; commands counted from local_command and user records starting with the command tag, each record once",
                          "raw": f"/clear {cleared}, compact_boundary in {compacted} sessions", "confidence": "solid",
                          "caveat": "slash command counts are invocations, not sessions"}},
    })

    # ── 2 helpers ───────────────────────────────────────────────────────────
    by_type = defaultdict(lambda: {"n": 0, "bg": 0, "models": Counter(), "descs": []})
    for s in human:
        for typ, desc, is_bg, model in s.agent_descs:
            g = by_type[typ]
            g["n"] += 1
            g["bg"] += is_bg
            if model:
                g["models"][model] += 1
            if desc and len(g["descs"]) < 40:
                g["descs"].append(desc)
    rows = []
    for typ, g in sorted(by_type.items(), key=lambda kv: -kv[1]["n"])[:12]:
        uniq = list(dict.fromkeys(g["descs"]))
        rows.append([typ, n(g["n"]), f"{pct(g['bg'], g['n'])}%",
                     ", ".join(f"{m} ({c})" for m, c in g["models"].most_common(2)) or "same as session",
                     " · ".join(uniq[:2])])
    picked = Counter()
    for g in by_type.values():
        picked.update(g["models"])
    sess_with = sum(1 for s in human if s.subagents)
    out.append({
        "part": P, "id": "helpers", "title": "Handing work to helpers",
        "lead": f"{n(H_N)} helpers in {days_of(H)} days, from {ratio(sess_with, T)} sessions; "
                f"{pct(bg.get('Agent', 0), H_N)}% ran in the background while you kept working.",
        "how": {"source": LOGS + "; assistant tool_use records named Agent (or Task)",
                "rule": "each Agent call is one helper; run_in_background in its input marks it background",
                "raw": f"{H_N} Agent calls in {sess_with} of {T} sessions; {bg.get('Agent', 0)} with run_in_background",
                "confidence": "solid", "caveat": "helpers started inside helpers live in subagent files, which aren't read: counts are the first level only",
                "code": "scan.read_session → agent_descs"},
        "points": [
            pt(f"Helpers reported back {n(marks.get('task_report', 0))} times; you stopped {n(tools.get('TaskStop', 0))} of them early "
               f"and watched {n(tools.get('Monitor', 0))} jobs until they finished.",
               LOGS, "<task-notification> blocks in user records; TaskStop and Monitor tool calls",
               f"task-notification {marks.get('task_report', 0)}, TaskStop {tools.get('TaskStop', 0)}, Monitor {tools.get('Monitor', 0)}", "solid",
               "a report from a background shell job also arrives as a task-notification, not only helpers"),
            pt(f"{n(bg.get('Bash', 0))} shell commands also ran in the background.", LOGS, "Bash calls with run_in_background", str(bg.get("Bash", 0))),
            pt("Models set for helpers: " + ", ".join(f"{m} ({c})" for m, c in picked.most_common(3)) + "."
               if picked else "", LOGS, "the model field in an Agent call's input; absent = the session's model",
               ", ".join(f"{m} {c}" for m, c in picked.most_common()), "solid",
               "the log doesn't show whether you or the agent chose the model"),
            pt(f"{n(tools.get('Workflow', 0))} scripted multi-agent workflow runs." if tools.get("Workflow") else "",
               LOGS, "Workflow tool calls", str(tools.get("Workflow", 0))),
        ],
        "table": {"caption": "Which helpers, for what", "columns": ["Helper", "Times", "In background", "Model", "Sent to do (examples)"],
                  "rows": rows,
                  "how": {"source": LOGS, "rule": "grouped by subagent_type (missing = general-purpose); examples are the description field the agent wrote",
                          "raw": f"{len(by_type)} helper types", "confidence": "solid",
                          "caveat": "the description is the agent's own summary, not your words"}},
    })

    # ── 3 sessions talking ──────────────────────────────────────────────────
    main_agents = Counter(s.agent_setting for s in human if s.agent_setting)
    sent, received = tools.get("SendMessage", 0), marks.get("peer_msg", 0)
    talk_sessions = sum(1 for s in human if s.tools.get("SendMessage") or s.marks.get("peer_msg"))
    out.append({
        "part": P, "id": "crosstalk", "title": "Sessions working together",
        "lead": f"Your sessions work together: {ratio(talk_sessions, T)} sent or received messages "
                f"from another agent or session ({n(sent)} sent, {n(received)} received).",
        "how": {"source": LOGS, "rule": "sent = SendMessage tool calls; received = user records holding a peer-session or teammate message",
                "raw": f"SendMessage {sent}; 'Another Claude session sent a message' or <teammate-message> {received}; {talk_sessions} sessions",
                "confidence": "solid", "caveat": "messages between a session and its own helpers count the same as between two top-level sessions",
                "code": "scan.read_session → marks['peer_msg']"},
        "points": [
            pt(f"You checked who else was running {n(tools.get('ListAgents', 0))} times before handing work over.",
               LOGS, "ListAgents tool calls", str(tools.get("ListAgents", 0)), "solid",
               "'before handing work over' is the usual reason, not checked call by call"),
            pt("Sessions started as a named main agent: " + ", ".join(f"{a} ({c})" for a, c in main_agents.most_common(6)) + "."
               if main_agents else "No session has started as a named main agent yet.",
               LOGS + "; agent-setting records", "the agentSetting a session was started with (`claude --agent x`)",
               ", ".join(f"{a} {c}" for a, c in main_agents.most_common()), "solid"),
            pt(f"{n(marks.get('launched_agent', 0))} sessions launched another top-level session.",
               LOGS, "system records carrying launchedAgentId", str(marks.get("launched_agent", 0)), "heuristic",
               "the field's meaning is inferred from its name and context; not documented"),
        ],
        "numbers": [{"value": n(sent), "label": "messages sent to other sessions"},
                    {"value": n(received), "label": "messages received"},
                    {"value": str(len(main_agents)), "label": "different main agents"}],
    })

    # ── 4 memory ────────────────────────────────────────────────────────────
    mem_paths = Counter()
    for s in human + auto:
        for _, k, p in s.inst_paths:
            if k == "memory":
                m = re.search(r"([\w.-]*memory[\w.-]*)/([\w.-]+)", p)
                mem_paths[(m.group(1) + "/" + m.group(2)) if m else p.rsplit("/", 1)[-1]] += 1
    lookups = tot("lookup_cmds")
    recall_skills = Counter({k: v for k, v in tot("skills").items() if re.search(r"recall|observer|tracer|memory|trace", k)})
    recall_agents = Counter({k: v for k, v in helpers.items() if re.search(r"tracer|observer|memory|reader", k)})
    out.append({
        "part": P, "id": "memory", "title": "Memory across sessions",
        "lead": f"You write down what a session learned ({n(sum(mem_paths.values()))} memory writes) and look back at past sessions "
                f"({n(sum(lookups.values()) + sum(recall_skills.values()) + sum(recall_agents.values()))} lookups), so a new session starts with context.",
        "how": {"source": LOGS + " and headless runs", "rule": "memory writes = Edit/Write on a file under a folder with 'memory' in its name, or MEMORY.md; "
                "lookups = shell commands that read past sessions or memory, plus recall skills and helpers",
                "raw": f"{sum(mem_paths.values())} writes; {sum(lookups.values())} shell lookups, {sum(recall_skills.values())} skills, {sum(recall_agents.values())} helpers",
                "confidence": "heuristic",
                "caveat": "only writes that went through the agent are visible; Claude Code's own auto-memory writes files without a log record; "
                          "lookup patterns (tracer, observer, ak, claude -r) are this kit's tools, so tools you built yourself may not be matched",
                "code": "scan.LOOKUP_RE · scan.edit_kind"},
        "points": [
            pt("Where memory gets written: " + ", ".join(f"{k} ({c})" for k, c in mem_paths.most_common(5)) + "." if mem_paths else "",
               LOGS, "the folder and file name of each memory write", ", ".join(f"{k} {c}" for k, c in mem_paths.most_common(8)), "solid"),
            pt("How past sessions get looked up: " + ", ".join(f"`{k}` ({c})" for k, c in (lookups + recall_skills + recall_agents).most_common(6)) + "."
               if lookups or recall_skills or recall_agents else "",
               LOGS, "LOOKUP_RE over Bash commands; skills and helpers whose name says recall, observer, tracer, memory or reader",
               ", ".join(f"{k} {c}" for k, c in (lookups + recall_skills + recall_agents).most_common(10)), "heuristic",
               "a name-based match, so a memory tool with a different name may not be counted"),
            pt(f"You carry context out by hand: /export {cmds.get('export', 0)}, /copy {cmds.get('copy', 0)}; "
               f"{marks.get('pasted', 0)} prompts paste a block of earlier material.",
               LOGS, "/export and /copy invocations; <pasted_content> blocks in user records",
               f"export {cmds.get('export', 0)}, copy {cmds.get('copy', 0)}, pasted {marks.get('pasted', 0)}", "heuristic",
               "a paste may be anything (a spec, a log), not only earlier session material"),
            pt(f"{marks.get('compact_summary', 0)} times a long session was folded into a summary and kept going.",
               LOGS, "user records flagged isCompactSummary", str(marks.get("compact_summary", 0))),
        ],
    })

    # ── 5 context setup ─────────────────────────────────────────────────────
    repos = S["repos_worked_in"]["top"]
    gi = S.get("global_instructions") or {}
    us = (S["settings"].get("user") or {})
    styles = [o["name"] for o in S["own"]["output_styles"]]
    starts = Counter()
    for s in human:
        for k, c in s.hook_sources.items():
            if "SessionStart" in k or "UserPromptSubmit" in k:
                starts[k.split(" · ")[0].replace("?", "unnamed hook")] += c
    mode_cmds = {k: v for k, v in cmds.items() if k in ("ask", "orchestrate", "align", "interview") or k.startswith("modes:")}
    for k, v in tot("skills").items():
        if k.startswith("modes:"):
            mode_cmds[k] = mode_cmds.get(k, 0) + v
    DISK = "files on disk"
    layers = [
        {"step": "Global instructions (~/.claude/CLAUDE.md)", "done": bool(gi.get("claude_md_lines")),
         "evidence": f"{gi.get('claude_md_lines') or 0} lines, {gi.get('imports') or 0} imports.",
         "how": {"source": DISK + ": ~/.claude/CLAUDE.md", "rule": "exists and has lines", "raw": f"{gi.get('claude_md_lines')} lines", "confidence": "solid"}},
        {"step": "Project instructions (CLAUDE.md / AGENTS.md)", "done": any(r.get("claude_md_lines") for r in repos),
         "evidence": f"in {sum(1 for r in repos if r.get('claude_md_lines'))} of your {len(repos)} main projects; "
                     f"{sum(1 for r in repos if r.get('claude_local_md'))} also have a private CLAUDE.local.md.",
         "how": {"source": DISK + ": the git root of each folder sessions ran in, top 12 by sessions",
                 "rule": "CLAUDE.md, .claude/CLAUDE.md or AGENTS.md at the repo root", "raw": f"{len(repos)} repos checked",
                 "confidence": "solid", "caveat": "only the 12 busiest repos are checked"}},
        {"step": "Folder-level and on-demand instructions (nested files, skills)",
         "done": any(isinstance(r.get('instructions'), dict) and r['instructions'].get('nested_claude_md') for r in repos),
         "evidence": f"{sum(r.get('project_skills', 0) for r in repos)} project skills, "
                     f"{sum(1 for r in repos if isinstance(r.get('instructions'), dict) and r['instructions'].get('nested_claude_md'))} projects with nested CLAUDE.md.",
         "how": {"source": DISK + ": mdmap (vendored from the kit's vault plugin) over each repo; .claude/skills/*/SKILL.md",
                 "rule": "CLAUDE.md files below the repo root; project skill folders", "confidence": "heuristic",
                 "caveat": "mdmap walks vendored and generated folders too: one repo showed 282 nested CLAUDE.md"}},
        {"step": "Your own voice (output style)", "done": bool(us.get("output_style")),
         "evidence": f"active: {us.get('output_style') or 'default'}; {len(styles)} of your own: {', '.join(styles) or 'none'}.",
         "how": {"source": DISK + ": settings.json outputStyle; ~/.claude/output-styles/*.md", "rule": "set and not default",
                 "confidence": "solid"}},
        {"step": "Context added by hooks at session start", "done": bool(starts),
         "evidence": ", ".join(f"{k} ({n(c)})" for k, c in starts.most_common(5)) or "none yet.",
         "how": {"source": LOGS + "; hook attachment records on SessionStart and UserPromptSubmit",
                 "rule": "the [tag] at the start of the text a hook added names who sent it", "confidence": "heuristic",
                 "caveat": "a hook that adds text without a [tag] shows as 'unnamed hook'"}},
        {"step": "Working modes you switch between", "done": bool(mode_cmds),
         "evidence": ", ".join(f"/{k} ({c})" for k, c in mode_cmds.items()) or "none yet this month.",
         "how": {"source": LOGS + "; local_command records", "rule": "commands named ask, orchestrate, align, interview, or modes:*",
                 "confidence": "heuristic", "caveat": "we match a fixed list of mode names, so modes with other names aren't counted"}},
        {"step": "Tools loaded only when needed", "done": bool(tools.get("ToolSearch")),
         "evidence": f"ToolSearch {n(tools.get('ToolSearch', 0))} times; /add-dir {cmds.get('add-dir', 0)}.",
         "how": {"source": LOGS, "rule": "ToolSearch tool calls", "confidence": "solid",
                 "caveat": "ToolSearch is used by Claude Code itself when many tools are installed; it says more about the setup than a choice"}},
    ]
    out.append({
        "part": P, "id": "context", "title": "How you set up what the agent knows",
        "lead": f"Context is layered: {sum(1 for l in layers if l['done'])} of {len(layers)} layers are in use, "
                f"from global rules down to hooks that brief every new session.",
        "points": [],
        "ladder": layers,
    })

    # ── 6 built-ins ─────────────────────────────────────────────────────────
    last = {}
    for s in human:
        for k in list(s.tools) + ["/" + c for c in s.commands]:
            if s.last and (k not in last or s.last > last[k]):
                last[k] = s.last
    used_rows, never = [], []
    for k, what in BUILTIN_TOOLS.items():
        c = tools.get(k, 0)
        (used_rows.append([k, what, n(c), day(last[k]) if k in last else "–"]) if c else never.append(k))
    for k, what in BUILTIN_COMMANDS.items():
        c = cmds.get(k, 0)
        (used_rows.append(["/" + k, what, n(c), day(last["/" + k]) if "/" + k in last else "–"]) if c else never.append("/" + k))
    used_rows.sort(key=lambda r: -int(r[2].replace(",", "")))
    images = marks.get("image", 0)
    out.append({
        "part": P, "id": "builtins", "title": "Built-in features you use",
        "lead": f"You use {len(used_rows)} of Claude Code's {len(BUILTIN_TOOLS) + len(BUILTIN_COMMANDS)} built-in features this month; "
                f"{len(never)} you haven't used yet.",
        "how": {"source": LOGS + "; catalogue of built-ins from the claude-code-guide agent (docs + transcripts, Claude Code 2.1.303)",
                "rule": "a tool counts when the agent called it; a command when you typed it",
                "raw": f"{len(BUILTIN_TOOLS)} tools + {len(BUILTIN_COMMANDS)} commands in the catalogue",
                "confidence": "solid",
                "caveat": "CLI flags (claude -r, --worktree, --agent …) and settings leave no per-use record, so they aren't in this count",
                "code": "orchestration.BUILTIN_TOOLS · BUILTIN_COMMANDS"},
        "points": [
            pt(f"You show instead of tell: {n(images)} screenshots or images pasted into prompts." if images else "",
               LOGS, "imagePasteIds on user records", str(images)),
            pt(f"You had an advisor model double-check in {sum(1 for s in human if s.marks.get('advisor'))} sessions."
               if marks.get("advisor") else "", LOGS, "assistant records carrying advisorModel",
               f"{marks.get('advisor', 0)} records", "solid"),
            pt("Not yet used this month: " + ", ".join(never) + "." if never else "",
               LOGS, "catalogue entries with zero uses in the window", f"{len(never)} of {len(BUILTIN_TOOLS) + len(BUILTIN_COMMANDS)}",
               "solid", "'not yet used' means not in these logs: use on another machine, or before this window, isn't visible"),
        ],
        "table": {"caption": "Built-ins you use, most first", "columns": ["Feature", "What it's for", "Times", "Last used"],
                  "rows": used_rows[:25]},
    })

    # ── 7 ladder ────────────────────────────────────────────────────────────
    own = S["own"]
    written = sum(1 for i in own["skills"] if not i.get("is_symlink")) + len(own["agents"])
    plugins_local = sum(1 for p in S["plugins"]["installed"] if p.get("local_marketplace"))
    by = E["headless_by_starter"]
    from scan import own_code_runs
    code_runs = own_code_runs(by)
    ladder = [
        {"step": "You chat with an agent", "done": T > 0, "evidence": f"{n(T)} sessions you typed in."},
        {"step": "You configure it", "done": bool(us.get("permissions", {}).get("allow") or us.get("permissions", {}).get("deny")
                                               or any(r.get("claude_md_lines") for r in repos)),
         "evidence": f"permission rules, instruction files in {sum(1 for r in repos if r.get('claude_md_lines'))} projects."},
        {"step": "You extend it with tools from others", "done": bool(S["plugins"]["installed"] or own["skills"]),
         "evidence": f"{len(S['plugins']['installed'])} plugins, {sum(1 for i in own['skills'] if i.get('is_symlink'))} installed skills, "
                     f"{len(H['mcp_servers_used'])} MCP servers used."},
        {"step": "You build your own", "done": bool(written or plugins_local),
         "evidence": f"{written} own skills and agents, {plugins_local} plugins from your own marketplace folder, "
                     f"{sum((us.get('hooks_by_event') or {}).values())} own hooks."},
        {"step": "You orchestrate several agents", "done": H["sessions"]["max_sessions_open_at_once"] >= 3 or H_N >= 50,
         "evidence": f"up to {H['sessions']['max_sessions_open_at_once']} sessions at once, {n(H_N)} helpers, {n(sent)} messages between sessions."},
        {"step": "You run agents while you're away", "done": code_runs >= 20,
         "evidence": f"{n(code_runs)} runs started by your own code; {E['scheduled_fires']} on a schedule; {E['headless_batches']} batches."},
    ]
    ladder_how = [
        "any session you typed in",
        "permission allow/deny rules in settings, or an instruction file in a busy repo",
        "any installed plugin or skill",
        "a skill with no install record, an agent file, a plugin from a folder marketplace on this machine",
        "3+ sessions open at the same moment, or 50+ helpers in the window",
        "20+ headless runs started by the Agent SDK, `claude -p` scripts or eval harnesses (temp-folder trials excluded)",
    ]
    for l, rule in zip(ladder, ladder_how):
        l["how"] = {"source": LOGS + " and files on disk", "rule": rule, "confidence": "heuristic",
                    "caveat": "the thresholds are early estimates from one machine"}
    reached = sum(1 for l in ladder if l["done"])
    out.append({
        "part": P, "id": "ladder", "title": "How your setup has grown",
        "lead": f"{reached} of {len(ladder)} steps: " + (ladder[reached - 1]["step"].lower() if reached else "not started yet") + ".",
        "points": [],
        "ladder": ladder,
    })

    for sec in out:
        sec["points"] = [p for p in sec.get("points", []) if p]
        label_points(sec)

    cost = sum(s.cost for s in human)
    glance = [
        {"value": f"~{round(E['sessions_typed_per_active_day'])}", "label": "sessions a day"},
        {"value": str(H["sessions"]["max_sessions_open_at_once"]), "label": "open at once, at most"},
        {"value": n(H_N), "label": "helpers started"},
        {"value": n(sent + received), "label": "messages between sessions"},
        {"value": n(code_runs), "label": "runs started by your own code"},
    ]
    if cost:
        glance.append({"value": f"${n(round(cost))}", "label": f"of sessions you typed in, at API prices ({days_of(H)} days)"})

    style = (
        f"You run several agents at once: about {round(E['sessions_typed_per_active_day'])} sessions a day, up to "
        f"{H['sessions']['max_sessions_open_at_once']} open at once, and {ratio(sess_with, T)} of them hand work to helpers – "
        f"{n(H_N)} this month, {pct(bg.get('Agent', 0), H_N)}% left running in the background. "
        f"Sessions talk to each other ({n(sent + received)} messages) and live long: {came_back} picked back up after hours away, "
        f"{forked + cont} branched or carried on. "
        f"Memory is deliberate ({n(sum(mem_paths.values()))} memory writes) and context is layered, from global rules to hooks that brief "
        f"every new session. Much of the work runs without you: {n(code_runs)} runs started by your own code."
    )
    return out, style, glance


# Short names for the explorer's tree, matched on words the claim always contains. A claim with no match
# gets a label derived by the renderer.
LABELS = [
    ("longest stayed open", "Longest session"), ("branched off or carried on", "Branched or continued"),
    ("Named sessions", "Session names"), ("reported back", "Reports, stops, watches"),
    ("shell commands also ran", "Background shell"), ("Models set for helpers", "Model per helper"),
    ("workflow runs", "Workflows"), ("checked who else", "Who's running"), ("named main agent", "Main agents"),
    ("launched another top-level", "Sessions launching sessions"), ("Where memory gets written", "Memory: where"),
    ("past sessions get looked up", "Memory: lookups"), ("carry context out", "Context carried out"),
    ("folded into a summary", "Compact summaries"), ("screenshots or images", "Images"),
    ("advisor model", "Advisor model"), ("Not yet used this month", "Not yet used"),
]


def label_points(sec: dict) -> None:
    for p in sec.get("points", []):
        if isinstance(p, dict) and "label" not in p:
            for needle, label in LABELS:
                if needle in p["text"]:
                    p["label"] = label
                    break


def days_of(H) -> int:
    return H["window_days"]
