"""devmode — the builder's view: facts about the scan, the map of what we can and can't see, where the
picture is thin, and features that leave nothing in the logs (STORY-SCHEMA.md § Dev mode).

`coverage` is the inventory of every signal the story uses. When a signal is added to scan.py or
orchestration.py, it gets a row here; a row with status "not measured" is a known hole, kept on purpose so
the gap stays visible.
"""
from __future__ import annotations

from collections import Counter

import limits

LOGS = "session logs"
DISK = "files on disk"
GIT = "git, read-only"

# area · signal · source · confidence · status
COVERAGE = [
    ("Sessions", "Coming back to a session after hours", LOGS + ": gaps between the person's records", "solid", "measured"),
    ("Sessions", "Forks, branches, /subtask", LOGS + ": forkedFrom, continued-in, copied parent records, local_command", "solid", "measured"),
    ("Sessions", "Plain resume (claude -r / -c)", "reopens the same file; no marker", "guess", "partial"),
    ("Sessions", "Claude Code version and record switches", LOGS + ": version on each record; signals that start or stop inside the window", "solid", "measured"),
    ("Sessions", "Repos and worktrees as one project", "git: a worktree's .git file points to its main checkout", "solid", "measured"),
    ("Sessions", "Compact vs clear", LOGS + ": compact_boundary, isCompactSummary, /clear", "solid", "measured"),
    ("Sessions", "/goal sessions", LOGS + ": goal_status records, /goal", "solid", "measured"),
    ("Sessions", "Background sessions (/bg, --bg)", LOGS + ": sessionKind bg", "solid", "measured"),
    ("Sessions", "Sessions open at once", LOGS + ": overlapping first..last times", "heuristic", "measured"),
    ("Delegation", "Helpers by type, model, background", LOGS + ": Agent tool input", "solid", "measured"),
    ("Delegation", "What helpers were sent to do", LOGS + ": Agent description (the agent's words)", "solid", "measured"),
    ("Delegation", "Helpers started by helpers", "subagent files under <session>/subagents/: read for file reads only, helpers they start are not counted", "solid", "partial"),
    ("Delegation", "Agent teams / shared task list", LOGS + ": TaskCreate/TaskList/TaskUpdate", "solid", "measured"),
    ("Delegation", "Workflows", LOGS + ": Workflow tool", "solid", "measured"),
    ("Sessions talking", "Messages sent / received", LOGS + ": SendMessage, peer and teammate messages", "solid", "measured"),
    ("Sessions talking", "Main agents (--agent)", LOGS + ": agent-setting records", "solid", "measured"),
    ("Memory", "Memory writes through the agent", LOGS + ": Edit/Write on *memory*/ and MEMORY.md", "heuristic", "measured"),
    ("Memory", "Claude Code auto-memory", "writes files, no log record", "guess", "not measured"),
    ("Memory", "Past-session lookups", LOGS + ": Bash commands matching this kit's tools", "heuristic", "partial"),
    ("Context", "Instruction files per repo", DISK + ": CLAUDE.md / AGENTS.md / CLAUDE.local.md", "solid", "measured"),
    ("Context", "What loads every turn", DISK + ": mdmap over each repo", "heuristic", "measured"),
    ("Context", "Hooks that add context", LOGS + ": hook attachments with a [tag]", "heuristic", "measured"),
    ("Context", "Output style, modes", DISK + " settings; " + LOGS + ": mode commands", "heuristic", "measured"),
    ("Context", "Who wrote the instruction file (person vs agent)", LOGS + ": agent edits only; hand edits unseen", "guess", "partial"),
    ("Built-ins", "Tools and commands used", LOGS + " + catalogue (claude-code-guide, CC 2.1.303)", "solid", "measured"),
    ("Built-ins", "CLI flags and settings choices", "no per-use record", "guess", "not measured"),
    ("Task flow", "Explore before editing", LOGS + ": tool order in a session (Read, Grep, Glob, shell cat/sed/grep/ls after any cd, helpers' reads)", "heuristic", "measured"),
    ("Task flow", "Files read: Read tool, shell readers, @-attached, helpers", LOGS + ": Read and Bash calls (cd resolved), file attachments, subagent transcripts", "heuristic", "measured"),
    ("Task flow", "Messages typed while the agent was busy", LOGS + ": promptSource queued, queued_command attachments from a person", "solid", "measured"),
    ("Task flow", "Who sent a message (person, program, system)", LOGS + ": promptSource; the entrypoint only for logs without it", "solid", "measured"),
    ("Task flow", "Prompt shape (length, file, done-words)", LOGS + ": prompt text, regexes", "guess", "measured"),
    ("Task flow", "Commits checked after the last edit", LOGS + ": Bash order", "heuristic", "measured"),
    ("Task flow", "Corrections and whether they became a rule", LOGS + ": prompt regex + later tooling edit", "guess", "measured"),
    ("Task flow", "Tests run by CI or hooks", "not in the session's own tool calls", "solid", "not measured"),
    ("Unattended", "Runs started by own code", LOGS + ": entrypoint sdk-* + folder", "heuristic", "measured"),
    ("Unattended", "Scheduled runs", LOGS + ": scheduled_task_fire, cron", "solid", "measured"),
    ("Unattended", "Plugin-started runs vs own runners", LOGS + ": agent setting only", "heuristic", "partial"),
    ("Setup", "Plugins, skills, agents, hooks, MCP", DISK + ": ~/.claude, plugin registry, skills.sh lock", "solid", "measured"),
    ("Setup", "Other agent tools (Cursor, Codex …)", DISK + ": their folders' last-change date", "guess", "partial"),
    ("Setup", "Work on other machines, VMs, devcontainers", "not on this machine", "solid", "not measured"),
]

GAPS = [
    {"title": "Chats from tools that keep no Claude Code log", "why": "Cursor, Copilot and other agents write nothing under ~/.claude/projects. With no log the page says can't tell, not zero; only git and files on disk are left to read.",
     "affects": []},
    {"title": "Hand edits are invisible", "why": "The logs only hold what went through the agent. Who really writes CLAUDE.md, and how much of the code is edited by hand afterwards, can't be told.",
     "affects": ["context-7", "loop-16"]},
    {"title": "Prompt quality is read by regex", "why": "Precision, corrections and 'done' words come from English word lists. A careful prompt in other words, or a polite correction, is missed.",
     "affects": ["prompt-12", "prompt-13", "loop-17"]},
    {"title": "Helpers inside helpers are not counted", "why": "Subagent transcripts live in a separate folder per session. They are read for the files they open, but helper counts are the first level of delegation only, so deep orchestration is under-counted.",
     "affects": ["harness-5"]},
    {"title": "Memory and lookup tools are matched by name", "why": "The patterns are this kit's tools (tracer, observer, ak). A member with their own memory tool would look like they have none.",
     "affects": ["context-10"]},
    {"title": "Plain resume leaves no marker", "why": "claude -r reopens the same file. It only shows up as a long gap inside a session.", "affects": ["context-9"]},
    {"title": "Thresholds come from one machine", "why": "Every 'seen' cut-off and every ladder step was tuned on the builder's own logs, the most extreme case. An ordinary laptop may read very differently.",
     "affects": []},
    {"title": "One machine, 30 days", "why": "Work on a client VM, a devcontainer or another laptop, and anything older than the window, is not here.", "affects": []},
]

NO_TRACE = [
    "CLI flags such as claude -r, --worktree, --agent, --append-system-prompt (only their effects show)",
    "Settings changed in the panel (the command shows, the value does not)",
    "Keyboard shortcuts, vim keys, theme, status-line rendering",
    "Voice dictation audio",
    "/btw answers (kept out of history by design)",
    "Claude Code auto-memory file writes",
    "Remote-control or cloud sessions on another device",
    "Background-session management from the shell (claude agents, attach, stop)",
]


def run_facts(RUN: Counter, window_days: int, seconds: float, sessions: dict, facts: dict) -> list:
    V, L, P, R = facts["versions"], facts["logs"], facts["prompts"], facts["reads"]
    E, Fl, Pr = facts["entrypoints"], facts["files"], facts["projects"]
    fmt = lambda c: ", ".join(f"{k} {v:,}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])) or "none"
    rows = [
        {"label": "Window", "value": f"last {window_days} days"},
        {"label": "Logs found", "value": f"{L['files_total']:,} session files in {L['dir']}; the oldest was last written {L['oldest_days']} days ago"
         if L["files_total"] else f"none in {L['dir']}"},
        {"label": "Session files read", "value": f"{RUN['files']:,}"},
        {"label": "Records read", "value": f"{RUN['records']:,}"},
        {"label": "Copied from a parent (counted once)", "value": f"{RUN['records_copied_from_parent']:,}"},
        {"label": "API blobs skipped", "value": f"{RUN['records_skipped_api_blobs']:,}"},
        {"label": "Sessions typed in · headless", "value": f"{sessions['typed']:,} · {sessions['headless']:,}"},
        {"label": "Typed in through", "value": fmt(E["interactive"])},
        {"label": "Entrypoints not on our list", "value": ", ".join(E["unlisted"]) or "none"},
        {"label": "Typed messages", "value": f"{P['counted']:,} counted; {P['queued']:,} typed while the agent was busy "
                                              f"({P['queued_attached']:,} of them handed over mid-turn); {P['copied']:,} copies in resumed or forked chats counted once; "
                                              f"{P['tagged']:,} start with a tag (a paste) and are kept; {P['program']:,} marked as a program's or the system's, in typed sessions, set aside"},
        {"label": "Files read", "value": f"Read tool {R.get('read', 0):,} · shell {R.get('bash', 0):,} ({R.get('bash_after_cd', 0):,} after a cd) · "
                                         f"@-attached {R.get('attached', 0):,} · helpers {R.get('subagent', 0):,} ({R.get('subagent_after_cd', 0):,} after a cd); "
                                         f"{sessions.get('subagent_files', 0):,} helper transcripts read"},
        {"label": "Own instruction and tooling files edited", "value": f"{Fl['instruction_files_by_full_path']:,} by full path → {Fl['instruction_files_by_repo_path']:,} by path inside the repo"},
        {"label": "Projects", "value": f"{Pr['as_checkouts']:,} checkouts → {Pr['as_repos']:,} repos (worktrees folded into their repo)"},
        {"label": "Claude Code versions", "value": (limits.version_text(V).rstrip(".") + f" ({len(V['sessions'])} versions)") if V["first"] else "not recorded"},
    ]
    rows += [{"label": "Version switch", "value": limits.switch_text(sw)} for sw in V["switches"]]
    rows.append({"label": "Scan time", "value": f"{round(seconds)} s"})
    return rows


def build(RUN: Counter, window_days: int, seconds: float, sessions: dict, facts: dict) -> dict:
    sessions = {**sessions, "subagent_files": facts["sessions"]["subagent_files"]}
    return {
        "run": run_facts(RUN, window_days, seconds, sessions, facts),
        "coverage": [{"area": a, "signal": s, "source": src, "confidence": c, "status": st} for a, s, src, c, st in COVERAGE],
        "gaps": GAPS,
        "no_trace": NO_TRACE,
    }
