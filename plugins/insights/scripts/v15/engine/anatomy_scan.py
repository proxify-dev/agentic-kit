#!/usr/bin/env python3
"""The anatomy's code-only step on this machine: its own checks, the files its AI questions need, and one brief per
most-active project for the AI pass. Read-only on the machine; writes only into RUN_DIR.

    python3 anatomy_scan.py RUN_DIR [--anatomy ANATOMY.yaml] [--days 30] [--projects 3]

Writes
  RUN_DIR/anatomy-scan.json        {meta, projects, checks: {id: {value, sessions, evidence, secs}}, files: {finder: {root: [paths]}}}
  RUN_DIR/anatomy-brief-<slug>.md  per project: every SCAN → AI and PROJECT READ question, with the files the scan found
The AI pass (one sonnet sub-agent per brief, as in the setup tool's SKILL.md) answers into RUN_DIR/anatomy-ai-<slug>.json.

The checks read only what Claude Code and git write the same way on any laptop: session logs (~/.claude/projects:
goal_status attachments, stop_hook_summary records, tool calls, typed messages), settings files, git. One pass over the
window's logs, in parallel. A SCAN → AI question whose finder found no file is answered "no" here, without an AI.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anatomy_model as M  # noqa: E402

HOME = os.path.expanduser("~")
LOGS = os.path.join(HOME, ".claude", "projects")
DEFAULT_ANATOMY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "anatomy", "anatomy.yaml")
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
TIMEBOX = re.compile(r"\b(until|overnight|tonight|morning|sleep|hours?|\d{1,2}\s?(am|pm))\b", re.I)
IMAGE = re.compile(r"\.(png|jpe?g|webp|gif)$", re.I)
EDITS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
LOOK_TOOLS = re.compile(r"screenshot|browser|playwright|chrome", re.I)
SESSIONS_DIR = re.compile(r"\.claude/projects\b")
SCRIPT = re.compile(r"(/[^\s\"']+\.(?:mjs|cjs|js|ts|py|sh))")  # an absolute script path inside a hook's command
# Where a person types: the terminal, or an app that drives Claude Code through the Agent SDK (VS Code, the desktop app,
# JetBrains; the same list as the evidence inventory's PERSON_APPS). A headless run (entrypoint sdk-*) carries a
# program's prompt, so it counts for no project (11-12: the pick took a folder with 3 terminal sessions over the repo
# with 169 desktop-app sessions, and the review and level read the wrong project).
PERSON_ENTRIES = ("cli", "claude-vscode", "claude-desktop", "claude-jetbrains")


def person_session(entry):
    """True when the session's "user" messages are the person's (see PERSON_ENTRIES)."""
    return entry in PERSON_ENTRIES


INJECTED = ("<task-notification", "<cross-session-message", "<local-command", "<command-name", "<command-message",
            "<system-reminder", "<bash-input", "<bash-stdout", "Caveat:", "[Request interrupted")


def typed_text(msg):
    """The text of a message the person typed, or None for tool results and what Claude Code injects in the user's
    place (task notifications, other sessions' messages, slash-command echoes): those carry session ids and paths the
    person never typed (11-09: 1,478 'messages pointing at another session' were mostly task notifications)."""
    c = (msg or {}).get("content")
    if isinstance(c, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            return None
        c = " ".join(x.get("text") or "" for x in c if isinstance(x, dict) and x.get("type") == "text")
    if not isinstance(c, str):
        return None
    if c.lstrip().startswith(INJECTED):
        return None
    return c


def scan_file(path):
    """One session log → its counts. Only lines that can matter are parsed."""
    s = Counter()
    s["file"] = 0
    cwd, entry, conditions, hooks, own_id, ran = None, None, [], Counter(), os.path.basename(path)[:-6], set()
    edited = False
    try:
        fh = open(path, errors="ignore")
    except OSError:
        return {}
    with fh:
        for line in fh:
            if not ('"goal_status"' in line or '"stop_hook_summary"' in line or '"type":"user"' in line
                    or '"tool_use"' in line or (cwd is None and '"cwd"' in line)):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if cwd is None and d.get("cwd"):
                cwd, entry = d.get("cwd"), d.get("entrypoint")
            t = d.get("type")
            if t == "attachment":
                a = d.get("attachment") or {}
                if a.get("type") == "goal_status":
                    if a.get("sentinel"):
                        s["goals_set"] += 1
                        conditions.append(str(a.get("condition") or "")[:200])
                    elif a.get("met"):
                        s["goals_met"] += 1
                    else:
                        s["goals_not_met"] += 1
            elif t == "system" and d.get("subtype") == "stop_hook_summary":
                errs = d.get("hookErrors") or []
                for cmd in [str(i.get("command") or "") for i in d.get("hookInfos") or [] if isinstance(i, dict)] + [str(e) for e in errs]:
                    ran.update(SCRIPT.findall(cmd))
                if errs:
                    s["stop_blocks"] += 1
                    for e in errs:
                        m = re.match(r"\[(.*?)\]", str(e))
                        hooks[(m.group(1) if m else str(e))[:120]] += 1
            elif t == "user" and not d.get("isMeta"):
                text = typed_text(d.get("message"))
                if text is None:
                    continue
                s["typed"] += 1
                edited = False
                content = (d.get("message") or {}).get("content")
                if isinstance(content, list) and any(isinstance(x, dict) and x.get("type") == "image" for x in content):
                    s["user_images"] += 1
                if any(u != own_id for u in UUID.findall(text)):
                    s["session_refs"] += 1
            elif t == "assistant":
                for x in (d.get("message") or {}).get("content") or []:
                    if not isinstance(x, dict) or x.get("type") != "tool_use":
                        continue
                    name, inp = x.get("name") or "", x.get("input") or {}
                    if name in EDITS:
                        edited = True
                    elif edited and name == "Read" and IMAGE.search(str(inp.get("file_path") or "")):
                        s["looks_after_edit"] += 1
                    elif edited and LOOK_TOOLS.search(name):
                        s["looks_after_edit"] += 1
                    if name in ("ScheduleWakeup", "Monitor"):
                        s["wakeups"] += 1
                    # the agent reading past sessions: any tool call aimed at Claude Code's own session folder, whatever
                    # tool the person uses for it (a search command, a script, a plain Read or Grep)
                    if SESSIONS_DIR.search(json.dumps(inp)):
                        s["session_search"] += 1
    return {"path": path, "cwd": cwd, "entry": entry, "counts": dict(s), "conditions": conditions, "hooks": dict(hooks),
            "ran_hooks": sorted(ran)}


def git(root, *args, timeout=20):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


_roots = {}


def repo_of(cwd):
    """The main checkout a session ran in: worktrees count for their repo; a folder outside git is its own project."""
    if not cwd:
        return None
    if cwd in _roots:
        return _roots[cwd]
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir").strip() if os.path.isdir(cwd) else ""
    root = os.path.dirname(common) if common.endswith("/.git") else (git(cwd, "rev-parse", "--show-toplevel").strip() or cwd)
    _roots[cwd] = root
    return root


def most_active(per, n):
    """(by_repo, active): sessions per main checkout (a session typed in any app counts as typed, a headless run only
    as a session), and the n repos with the most typed sessions, most first; a folder that is no git repo is skipped."""
    by_repo = defaultdict(Counter)
    for r in per:
        repo = repo_of(r["cwd"])
        if person_session(r["entry"]) and r["counts"].get("typed"):
            by_repo[repo]["typed_sessions"] += 1
        by_repo[repo]["sessions"] += 1
    active = [root for root, _ in sorted(by_repo.items(), key=lambda kv: -kv[1].get("typed_sessions", 0))
              if root and os.path.isdir(root) and os.path.isdir(os.path.join(root, ".git"))][:n]
    return by_repo, active


def ls(root):
    return [f for f in git(root, "ls-files").splitlines() if f]


def hook_scripts(root, events):
    """Scripts of the person's own hooks on these events: from settings files in ~/.claude and the project, and from
    hooks.json files tracked in the project (plugins they build). Kept only when the script lives in the project or
    ~/.claude/hooks — an installed plugin's hook is someone else's."""
    found = set()

    def from_hooks(hooks, base):
        for ev, groups in (hooks or {}).items():
            if ev not in events:
                continue
            for g in groups or []:
                for h in (g or {}).get("hooks") or []:
                    cmd = str(h.get("command") or "")
                    cmd = cmd.replace("${CLAUDE_PLUGIN_ROOT}", base).replace("${CLAUDE_PROJECT_DIR}", root).replace("~", HOME)
                    for tok in re.findall(r"[\w./${}~-]+\.(?:mjs|cjs|js|ts|py|sh)", cmd):
                        p = tok if os.path.isabs(tok) else os.path.join(base, tok)
                        if os.path.isfile(p):
                            found.add(os.path.realpath(p))
    for sf in (os.path.join(HOME, ".claude", "settings.json"), os.path.join(root, ".claude", "settings.json"),
               os.path.join(root, ".claude", "settings.local.json")):
        try:
            from_hooks(json.load(open(sf)).get("hooks"), root)
        except (OSError, ValueError, AttributeError):
            pass
    for hj in [f for f in ls(root) if f.endswith("hooks.json")]:
        p = os.path.join(root, hj)
        try:
            d = json.load(open(p))
            from_hooks(d.get("hooks", d), os.path.dirname(os.path.dirname(p)))
        except (OSError, ValueError, AttributeError):
            pass
    own = [p for p in found if p.startswith(os.path.realpath(root) + os.sep) or p.startswith(os.path.join(HOME, ".claude", "hooks"))]
    return sorted(own)


def one_copy(root, paths):
    """One path per file: a worktree's copy (.claude/worktrees/<name>/x) stands for the main checkout's x when that exists."""
    out = {}
    for p in sorted(paths):
        rel = re.sub(r"^\.claude/worktrees/[^/]+/", "", os.path.relpath(p, root))
        main = os.path.join(root, rel)
        out.setdefault(rel, main if os.path.isfile(main) else p)
    return sorted(out.values())


def finders(root, conditions, ran=()):
    files = ls(root)
    pick = lambda rx: [os.path.join(root, f) for f in files if re.search(rx, f)][:15]  # noqa: E731
    grep = lambda rx: [os.path.join(root, f) for f in git(root, "grep", "-l", "-I", "-E", rx).splitlines()][:15]  # noqa: E731
    mentioned = []
    for c in conditions:
        for m in re.findall(r"[\w./-]+\.md", c):
            p = m if os.path.isabs(m) else os.path.join(root, m)
            if os.path.isfile(p):
                mentioned.append(p)
    return {
        # declared in settings or a tracked hooks.json, or seen running in Claude Code's own Stop-hook records — a runner
        # can hand a hook to a headless run on the command line, where no settings file shows it (11-09: a real gate missed)
        "own-stop-hooks": one_copy(root, set(hook_scripts(root, {"Stop", "SubagentStop"}))
                                   | {p for p in ran if os.path.realpath(p).startswith(os.path.realpath(root) + os.sep) and os.path.isfile(p)}),
        "own-hooks": hook_scripts(root, {"Notification", "PreCompact", "SessionStart", "SessionEnd", "PostToolUse", "PreToolUse"}),
        "runner-scripts": grep(r"claude (-p|--print)\b|claude_agent_sdk|@anthropic-ai/claude-agent-sdk"),
        "goal-files": sorted(set(mentioned + pick(r"(^|/)(GOALS?|goal)[^/]*\.md$")))[:15],
        "rule-files": pick(r"(^|/)(CLAUDE|AGENTS)\.md$|(^|/)\.claude/rules/"),
        "style-files": pick(r"(^|/)\.claude/output-styles/|(^|/)output-styles/"),
        "eval-folders": sorted({os.path.join(root, f.split("/evals/")[0] + "/evals") if "/evals/" in f else os.path.join(root, f.split("bench/")[0] + "bench")
                                for f in files if re.search(r"(^|/)(evals|bench)/", f)})[:10],
        "agent-files": pick(r"(^|/)agents/[^/]+\.md$"),
        "decision-files": pick(r"(?i)(^|/)(CHANGELOG[^/]*|[^/]*decision[^/]*|ADR[^/]*|[^/]*ledger[^/]*)\.(md|jsonl|txt)$"),
        "settings-files": pick(r"(^|/)\.claude/settings[^/]*\.json$"),
    }


def git_checks(root, days):
    out = {}
    log = git(root, "log", f"--since={days}.days", "--name-only", "--format=@@%H")
    commits = [c.strip().splitlines() for c in log.split("@@") if c.strip()]
    code = lambda f: not re.search(r"\.(md|txt|json|ya?ml)$", f)  # noqa: E731
    co = [c for c in commits if any(code(f) for f in c[1:]) and any(re.search(r"(?i)(^|/)CHANGELOG|(^|/)docs/", f) for f in c[1:])]
    out["changelog_cochange"] = (len(co), len(commits))
    out["worktrees"] = max(0, git(root, "worktree", "list", "--porcelain").count("\nworktree ") + (1 if git(root, "worktree", "list") else 0) - 1)
    models = Counter()
    for f in [f for f in ls(root) if re.search(r"(^|/)agents/[^/]+\.md$", f)][:200]:
        try:
            head = open(os.path.join(root, f), errors="ignore").read(1500)
        except OSError:
            continue
        m = re.search(r"(?m)^model:\s*([\w.-]+)", head)
        if m:
            models[m.group(1)] += 1
    out["models"] = dict(models)
    out["home_override"] = len(git(root, "grep", "-l", "-I", "-E", r"\bHOME=").splitlines())
    return out


def runs_claude(text):
    """True when a scheduled entry runs Claude Code: named in it, or in the script it starts (11-09: a crontab line
    that starts a sweep script showed no 'claude' of its own)."""
    if re.search(r"\bclaude\b", text):
        return True
    for p in re.findall(r"(?:~|/)[\w./~-]+", text):
        p = os.path.expanduser(p)
        if os.path.isfile(p) and os.path.getsize(p) < 500_000:
            try:
                if re.search(r"\bclaude\b(\s+-p|\s+--print|_agent_sdk)|claude-agent-sdk", open(p, errors="ignore").read()):
                    return True
            except OSError:
                pass
    return False


def scheduler():
    lines = []
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        lines += [ln.strip()[:120] for ln in r.stdout.splitlines() if ln.strip() and not ln.lstrip().startswith("#") and runs_claude(ln)]
    except (OSError, subprocess.SubprocessError):
        pass
    for p in glob.glob(os.path.join(HOME, "Library", "LaunchAgents", "*.plist")):
        try:
            if runs_claude(open(p, errors="ignore").read()):
                lines.append(os.path.basename(p))
        except OSError:
            pass
    return lines


SESSION_READ = re.compile(r"transcript_path|\.claude/projects")


def session_readers(root):
    """Own files that read Claude Code's session logs (a hook's transcript_path, ~/.claude/projects): agents, skills and
    commands (L3: a tool of theirs reads past sessions) and hook scripts on end-of-session events (L4: every session is
    read without them). The 11-09 signal test: 0.15 s on his laptop; a laptop without such tools reads 0."""
    tools, hooks = [], []
    for f in ls(root):
        if not re.search(r"(^|/)(agents|skills|commands|hooks)/", f) or not re.search(r"\.(md|py|mjs|js|sh|ts)$", f):
            continue
        p = os.path.join(root, f)
        try:
            if os.path.getsize(p) > 300_000 or not SESSION_READ.search(open(p, errors="ignore").read()):
                continue
        except OSError:
            continue
        (hooks if "/hooks/" in "/" + f else tools).append(p)
    return tools, hooks


def slug(root):
    return re.sub(r"[^a-z0-9]+", "-", os.path.basename(root.rstrip("/")).lower()).strip("-") or "root"


BRIEF = """# Anatomy AI pass — {name}

Read-only. Answer each question below **for this project only**: {root}
For SCAN → AI questions, read only the files listed. For PROJECT READ questions, look through the project
(CLAUDE.md first, then what it points to; `git ls-files` to find things). Do not run the project's code.

For every question write one answer: found = "yes" (you saw it), "no" (you looked and it isn't there) or "unclear".
evidence = "path:line — a short quote" for yes; for no, say where you looked. Never answer yes without a quote.

Write the result with the Write tool to {out} as one JSON object:
{{"project": "{root}", "answers": [{{"q": "<question id>", "found": "yes|no|unclear", "evidence": "..."}}]}}

## Questions
{questions}
"""


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--anatomy", default=DEFAULT_ANATOMY)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--projects", type=int, default=3)
    a = ap.parse_args(argv)
    try:
        import yaml
    except ImportError:  # no PyYAML and no uv: the anatomy tab says it isn't in this run; the rest of the page builds
        print("note: PyYAML not available; the anatomy step is skipped (its tab will say it isn't in this run)")
        return 0
    A = yaml.safe_load(open(a.anatomy))
    os.makedirs(a.run_dir, exist_ok=True)
    t0 = time.time()
    cutoff = time.time() - a.days * 86400
    files = [f for f in glob.glob(os.path.join(LOGS, "*", "*.jsonl")) if os.path.getmtime(f) >= cutoff]
    with Pool(max(2, (os.cpu_count() or 4) - 1)) as pool:
        per = [r for r in pool.map(scan_file, files, chunksize=16) if r]
    t_logs = time.time() - t0

    total, sessions = Counter(), Counter()
    conditions, hooks, ran = defaultdict(list), Counter(), set()
    goal_sessions = goal_wake = rounds = 0
    for r in per:
        c = dict(r["counts"])
        if r["entry"] != "cli":  # a headless run's "user" messages are a program's prompt, not the person typing
            for k in ("session_refs", "user_images"):
                c.pop(k, None)
        total.update(c)
        for k, v in c.items():
            if v:
                sessions[k] += 1
        repo = repo_of(r["cwd"])
        conditions[repo] += r["conditions"]
        hooks.update(r["hooks"])
        ran.update(r.get("ran_hooks") or [])
        if c.get("goals_set"):
            goal_sessions += 1
            goal_wake += bool(c.get("wakeups"))
        rounds += c.get("stop_blocks", 0) >= 2
    by_repo, active = most_active(per, a.projects)

    t1 = time.time()
    found, gits, readers = {}, {}, {"tools": [], "hooks": []}
    for root in active:
        for fid, paths in finders(root, conditions[root], ran).items():
            found.setdefault(fid, {})[root] = paths
        gits[root] = git_checks(root, a.days)
        t, h = session_readers(root)
        readers["tools"] += t
        readers["hooks"] += h
    sched = scheduler()
    t_git = time.time() - t1

    all_conditions = [x for root in conditions for x in conditions[root]]
    timeboxed = [x for x in all_conditions if TIMEBOX.search(x)]
    co_n = sum(g["changelog_cochange"][0] for g in gits.values())
    co_of = sum(g["changelog_cochange"][1] for g in gits.values())
    models = Counter()
    for g in gits.values():
        models.update(g["models"])
    checks = {
        "goal_status": {"value": total["goals_set"], "sessions": sessions["goals_set"],
                        "evidence": [f"{total['goals_set']} goals set in {sessions['goals_set']} sessions; {total['goals_met']} met; "
                                     f"{sessions['goals_not_met']} sessions with not-met checks"]},
        "goal_timebox": {"value": len(timeboxed), "evidence": [f"{len(timeboxed)} of {len(all_conditions)} goals time-boxed"] + [f"“{x[:80]}”" for x in timeboxed[:2]]},
        "wakeups_in_goal_sessions": {"value": goal_wake, "sessions": goal_sessions,
                                     "evidence": [f"{goal_wake} of {goal_sessions} goal sessions call ScheduleWakeup or Monitor"]},
        "stop_hook_blocks": {"value": total["stop_blocks"], "sessions": sessions["stop_blocks"],
                             "evidence": [f"{total['stop_blocks']} Stop-hook blocks in {sessions['stop_blocks']} sessions"] + [h for h, _ in hooks.most_common(2)]},
        "stop_hook_rounds": {"value": rounds, "evidence": [f"{rounds} sessions where a Stop hook sent the run back 2+ times"]},
        "user_images": {"value": total["user_images"], "sessions": sessions["user_images"],
                        "evidence": [f"{total['user_images']} typed messages with images, in {sessions['user_images']} sessions"]},
        "looks_after_edit": {"value": total["looks_after_edit"], "sessions": sessions["looks_after_edit"],
                             "evidence": [f"the agent opened an image or a browser after its own edit {total['looks_after_edit']} times, in {sessions['looks_after_edit']} sessions"]},
        "session_refs": {"value": total["session_refs"], "evidence": [f"{total['session_refs']} typed messages point at another session"]},
        "session_search": {"value": total["session_search"], "evidence": [f"{total['session_search']} tool calls read Claude Code's session folder"]},
        "changelog_cochange": {"value": co_n, "evidence": [f"{co_n} of {co_of} commits change code and a changelog or docs together"]},
        "runner_scripts": {"value": sum(len(v) for v in (found.get("runner-scripts") or {}).values()),
                           "evidence": [os.path.relpath(p, HOME) for v in (found.get("runner-scripts") or {}).values() for p in v][:3]},
        "scheduled_claude": {"value": len(sched), "evidence": sched[:3]},
        "own_session_readers": {"value": len(readers["tools"]), "evidence": [os.path.relpath(p, HOME) for p in readers["tools"][:3]]},
        "own_session_hooks": {"value": len(readers["hooks"]), "evidence": [os.path.relpath(p, HOME) for p in readers["hooks"][:3]]},
        "worktrees": {"value": sum(g["worktrees"] for g in gits.values()),
                      "evidence": [f"{os.path.basename(r)}: {g['worktrees']}" for r, g in gits.items()]},
        "model_spread": {"value": len(models), "evidence": [", ".join(f"{m} {n}" for m, n in models.most_common(4))]},
        "home_override": {"value": sum(g["home_override"] for g in gits.values()), "evidence": ["files that point HOME at another folder"]},
    }
    meta = {"days": a.days, "logs": len(files), "secs_logs": round(t_logs, 1), "secs_git": round(t_git, 1),
            "anatomy": os.path.abspath(a.anatomy), "at": time.strftime("%Y-%m-%d %H:%M")}
    projects = [{"root": r, "slug": slug(r), "typed_sessions": 0, "sessions": 0, **by_repo[r]} for r in active]
    json.dump({"meta": meta, "projects": projects, "checks": checks, "files": found},
              open(os.path.join(a.run_dir, "anatomy-scan.json"), "w"), indent=1)

    qs = M.ai_questions(A)
    for p in projects:
        lines = []
        for q in qs:
            if q["kind"] == "scan+ai":
                fids = q["find"] if isinstance(q["find"], list) else [q["find"]]
                paths = sorted({x for f in fids for x in (found.get(f) or {}).get(p["root"]) or []})
                if not paths:
                    continue  # answered "no" by the scan
                lines.append(f"### {q['q']}\n{q['where']}\n**Question:** {q['what']}\n**Read only:** " + ", ".join(paths))
            else:
                lines.append(f"### {q['q']}\n{q['where']}\n**Question:** {q['what']}\n**Look in:** the project")
        out = os.path.join(a.run_dir, f"anatomy-ai-{p['slug']}.json")
        open(os.path.join(a.run_dir, f"anatomy-brief-{p['slug']}.md"), "w").write(
            BRIEF.format(name=p["slug"], root=p["root"], out=out, questions="\n\n".join(lines)))
    print(f"anatomy scan: {len(files)} session logs in {t_logs:.0f} s, git and files in {t_git:.0f} s; "
          f"projects: {', '.join(p['slug'] + ' (' + str(p.get('typed_sessions', 0)) + ')' for p in projects)}")
    for k, v in checks.items():
        print(f"  {k:26} {v['value']!s:>6}  {(v.get('evidence') or [''])[0]}")


if __name__ == "__main__":
    main(sys.argv[1:])
