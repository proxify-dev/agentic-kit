#!/usr/bin/env python3
"""Place one person on Level 1-4 from what their own laptop shows (spike, 2026-10-22).

His ask: "figure ways to place someone on a level based on heuristics we can collect from a user's laptop".
This overrides, for this spike only, the page rule "never give a level": the output is a SUGGESTED level for
the interviewer, with every heuristic, its bar and its value, so the interviewer can see why.

Input: the scanner's JSON (scan.py --days 30 --out X). Extra reads here, all local and read-only:
  - who the person is in git (global + per-repo config, and the authors of commits carrying the agent trailer),
  - who wrote each thing that runs around the agent (plugins from local folders, hook scripts, rule files,
    memory stores), by git authorship - the "did the person build it, or did the tool?" split,
  - whether the person's own hooks can actually block (exit 2 / a block decision), not just print.

Levels are a ladder: Level N needs every level below it. Each level's gate is written next to it.
Thresholds are first guesses from ONE machine (his); they must be re-cut on member machines (CLAUDE.md rule 4).

Run: python3 -I place_level.py SCAN.json [--out place.json] [--md place.md]
Stdlib only, like the scanner: members run it with whatever python3 they have.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

HOME = Path.home()
MET, WEAK, NOT, CANT = "met", "weak", "not met", "can't tell"


# ---------- small helpers ----------

def git(root: Path, *args, timeout=20) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def git_root(p: Path) -> Path | None:
    p = p if p.is_dir() else p.parent
    out = git(p, "rev-parse", "--show-toplevel").strip()
    return Path(out) if out else None


def of(text) -> tuple[int, int]:
    """'76 of 560' -> (76, 560)."""
    m = re.match(r"\s*(\d+)\s+of\s+(\d+)", str(text or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def share(a, b) -> float:
    return a / b if b else 0.0


def pct(x: float) -> str:
    return f"{round(100 * x)}%"


def expand(path: str) -> Path:
    return Path(os.path.expanduser(path.replace("~/", str(HOME) + "/", 1) if path.startswith("~/") else path))


def H(id_, level, line, question, result, value, bar, why, tool_did_it=""):
    return {"id": id_, "level": level, "guide_line": line, "question": question, "result": result,
            "value": value, "bar": bar, "why": why, "tool_did_it": tool_did_it}


def grade(value: float, met: float, weak: float) -> str:
    return MET if value >= met else WEAK if value >= weak else NOT


# ---------- who is the person, and what did they build ----------

def identities(scan: dict) -> set[str]:
    """The person's git emails. Portable: global config, each worked-in repo's config, and the authors of
    commits that carry an agent trailer (whoever commits with Claude as co-author is the one at the keyboard)."""
    ids = set()
    for args in (("config", "--global", "user.email"),):
        out = git(HOME, *args).strip()
        if out:
            ids.add(out.lower())
    for r in scan["setup"]["repos_worked_in"].get("top", []):
        root = expand(r["repo"])
        if not root.exists():
            continue
        e = git(root, "config", "user.email").strip()
        if e:
            ids.add(e.lower())
        log = git(root, "log", "--since=60.days", "--format=%ae%x09%b%x1e", "-n", "400")
        for rec in log.split("\x1e"):
            if re.search(r"co-authored-by:\s*claude", rec, re.I):
                ids.add(rec.strip().split("\t", 1)[0].lower())
    return {i for i in ids if "@" in i and "noreply@anthropic" not in i}


def authorship(path: Path, ids: set[str]) -> tuple[float, int]:
    """Share of commits touching `path` made by the person, and how many commits there were."""
    root = git_root(path)
    if not root:
        return (0.0, 0)
    rel = os.path.relpath(path.resolve(), root.resolve())
    out = git(root, "log", "--format=%ae", "--", rel if rel != "." else ".")
    emails = [e.lower() for e in out.split() if e]
    if not emails:
        return (0.0, 0)
    return (share(sum(e in ids for e in emails), len(emails)), len(emails))


BLOCK_RE = re.compile(r"exit\s+2\b|sys\.exit\(\s*2\s*\)|process\.exit\(\s*2\s*\)|\"?decision\"?\s*[:=]\s*[\"']block|"
                      r"permissionDecision\"?\s*[:=]\s*[\"']deny|\"?continue\"?\s*[:=]\s*false", re.I)


def scripts_in(cmd: str, base: Path | None = None) -> list[Path]:
    """Files a hook command runs (sh x.sh, python3 y.py, a bare path)."""
    out = []
    cmd = cmd.replace("${CLAUDE_PLUGIN_ROOT}", str(base or "")).replace("$CLAUDE_PLUGIN_ROOT", str(base or ""))
    for tok in re.findall(r"[~\w./${}-]+\.(?:sh|py|mjs|js|ts|bash)|'[^']+'|\"[^\"]+\"", cmd):
        tok = tok.strip("'\"")
        p = expand(tok)
        if not p.is_absolute() and base:
            p = base / tok
        if p.is_file():
            out.append(p)
    return out


def can_block(files: list[Path]) -> bool:
    for f in files:
        try:
            if BLOCK_RE.search(f.read_text(errors="ignore")[:200_000]):
                return True
        except Exception:
            pass
    return False


def own_plugins(ids: set[str]) -> list[dict]:
    """Plugins installed from a local folder whose git history is mostly the person's."""
    km = HOME / ".claude/plugins/known_marketplaces.json"
    ip = HOME / ".claude/plugins/installed_plugins.json"
    try:
        markets = json.loads(km.read_text())
        installed = json.loads(ip.read_text()).get("plugins", {})
    except Exception:
        return []
    res = []
    for key, entries in installed.items():
        name, _, market = key.partition("@")
        src = (markets.get(market) or {}).get("source", {})
        if src.get("source") != "directory":
            continue  # installed from GitHub/git: someone else's unless their own marketplace (missed; see caveat)
        mroot = expand(src.get("path", ""))
        try:
            listed = json.loads((mroot / ".claude-plugin/marketplace.json").read_text()).get("plugins", [])
        except Exception:
            listed = []
        src_dir = next((mroot / p["source"] for p in listed if p.get("name") == name and isinstance(p.get("source"), str)), None)
        for e in entries if isinstance(entries, list) else [entries]:
            path = Path(e.get("installPath", "")).resolve()
            if not path.exists():
                continue
            # the cache copy has no git; the marketplace folder it came from does
            a, n = authorship(src_dir.resolve(), ids) if src_dir and src_dir.exists() else (0.0, 0)
            hooks_json = path / "hooks" / "hooks.json"
            hook_files, events = [], []
            if hooks_json.is_file():
                try:
                    hj = json.loads(hooks_json.read_text()).get("hooks", {})
                    for ev, arr in hj.items():
                        events.append(ev)
                        for m in arr:
                            for h in m.get("hooks", []):
                                hook_files += scripts_in(h.get("command", ""), path)
                except Exception:
                    pass
            res.append({"plugin": key, "path": str(path).replace(str(HOME), "~"), "author_share": round(a, 2),
                        "commits": n, "own": a >= 0.5 and n >= 3, "hook_events": events,
                        "hooks_can_block": can_block(hook_files), "mcp": (path / ".mcp.json").exists()})
            break
    return res


def own_user_hooks(ids: set[str]) -> list[dict]:
    """Hooks in the person's own settings files, and whether the scripts they run can block."""
    res = []
    for sp in (HOME / ".claude/settings.json", HOME / ".claude/settings.local.json"):
        try:
            hooks = json.loads(sp.read_text()).get("hooks", {})
        except Exception:
            continue
        for ev, arr in hooks.items():
            for m in arr:
                for h in m.get("hooks", []):
                    cmd = h.get("command", "") or ""
                    files = scripts_in(cmd)
                    # a script under someone else's skill (e.g. an installed skill's own script) is not theirs
                    theirs = [f for f in files if "/skills/" not in str(f) or authorship(f, ids)[0] >= 0.5]
                    res.append({"event": ev, "command": cmd[:90], "scripts": [str(f).replace(str(HOME), "~") for f in files],
                                "own": bool(theirs) or (not files and bool(cmd)), "can_block": can_block(theirs)})
    return res


def skill_owners(scan: dict, ids: set[str]) -> list[dict]:
    """Who wrote each user-level skill. A link only says how it got here, not who wrote it, so follow it and ask
    git: mostly their commits -> 'own'; theirs among others' -> 'team' (works for more than them); no git under
    an installer's folder, or no commits of theirs -> 'installed'. A plain folder with no git stays 'own'."""
    out = []
    for sk in scan["setup"]["own"].get("skills", []):
        path = HOME / ".claude" / "skills" / sk["name"]
        real = Path(os.path.realpath(path)) if path.exists() else path
        a, n = authorship(real, ids) if path.exists() else (0.0, 0)
        if n:
            kind = "own" if a >= 0.5 else "team" if a > 0 else "installed"
        else:
            kind = "installed" if sk.get("is_symlink") else "own"
        out.append({**sk, "owner": kind, "author_share": round(a, 2), "commits": n,
                    "resolves_to": str(real).replace(str(HOME), "~")})
    return out


MEM_DIR_RE = re.compile(r"(^|/)(\.agents/memory|\.claude/agent-memory[^/]*|memory|memories|\.memory|notes)$", re.I)


def memory_stores(scan: dict, ids: set[str]) -> dict:
    """Memory the PERSON set up vs Claude Code's own default memory (~/.claude/projects/*/memory)."""
    default_files = list((HOME / ".claude/projects").glob("*/memory/*.md"))
    stores = []
    seen = set()
    roots = [expand(r["repo"]) for r in scan["setup"]["repos_worked_in"].get("top", [])]
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git", ".venv", "dist", "build", "w", "worktrees")]
            rel = os.path.relpath(dirpath, root)
            if rel.count(os.sep) > 4:
                dirnames[:] = []
                continue
            is_code = (Path(dirpath) / ".claude-plugin").exists() or "templates" in rel.split(os.sep)  # a memory TOOL's source, not a store
            if MEM_DIR_RE.search(rel.replace(os.sep, "/")) and not is_code and os.path.realpath(dirpath) not in seen:
                seen.add(os.path.realpath(dirpath))
                mds = list(Path(dirpath).rglob("*.md"))[:3000]
                if len(mds) < 5:
                    continue
                days = {int(p.stat().st_mtime // 86400) for p in mds}
                has_index = any(p.name.upper() in ("MEMORY.MD", "README.MD", "INDEX.MD") for p in Path(dirpath).glob("*.md"))
                subdirs = sum(1 for p in Path(dirpath).iterdir() if p.is_dir())
                stores.append({"path": dirpath.replace(str(HOME), "~"), "files": len(mds), "days_touched": len(days),
                               "has_index": has_index, "folders": subdirs})
    stores.sort(key=lambda s: -s["files"])
    return {"own_stores": stores[:8], "claude_code_default_memory_files": len(default_files)}


def rule_placement(scan: dict, ids: set[str]) -> dict:
    """Rule files below the repo root that the person wrote (git authorship), per repo."""
    out = []
    for r in scan["setup"]["repos_worked_in"].get("top", []):
        root = expand(r["repo"])
        if not root.exists():
            continue
        ls = git(root, "ls-files", "*CLAUDE.md", "*AGENTS.md", ".claude/rules/*", "*/.claude/rules/*")
        files = [f for f in ls.split("\n") if f and not f.startswith("node_modules")]
        nested = [f for f in files if "/" in f.strip("/") and not f.startswith(".claude/rules")]
        rules = [f for f in files if "/rules/" in f or f.startswith(".claude/rules")]
        log = git(root, "log", "--since=90.days", "--format=%ae", "--", *([f for f in nested[:200]] or ["CLAUDE.md"]))
        emails = [e.lower() for e in log.split() if e]
        mine = sum(e in ids for e in emails)
        out.append({"repo": r["repo"], "nested_rule_files": len(nested), "path_rules": len(rules),
                    "commits_to_them_90d": len(emails), "by_person": mine,
                    "other_authors": len({e for e in emails if e not in ids})})
    return {"repos": out}


# ---------- the heuristics, level by level ----------

def days_kept(s) -> int:
    """Days of chats this machine can show: the scan window, cut by Claude Code's retention (cleanupPeriodDays,
    30 by default) and by the oldest log left. Volume bars scale to it, so someone keeping 7 days isn't read
    against a 30-day bar (a first tester keeps 7 days, 2026-10-22)."""
    lg = s.get("_logs", {})
    win = lg.get("window_days") or 30
    vals = [win] + [v for v in (lg.get("cleanup_days"), lg.get("oldest_days")) if isinstance(v, (int, float)) and v > 0]
    return max(1, int(min(vals)))


def scaled(n_for_30: float, kept: int, floor: int) -> int:
    """A count bar written for 30 days, scaled to the days kept, never below `floor`."""
    return max(floor, -(-int(n_for_30 * kept) // 30))


def level1(s, ev, ws):
    typed = s["with_a_person_typing"]
    code = ev["sessions_with_code_edits"]
    days = s["active_days"]
    win = days_kept(s)
    d_met, d_weak, c_met = scaled(12, win, 3), scaled(6, win, 2), scaled(10, win, 3)
    ints = ev.get("integrations", {})
    real_tools = {k: v for k, v in ints.items() if k not in ("docker", "playwright")}  # skeptic: not outside systems
    mcp_calls = sum(v for k, v in s.get("_mcp_used", {}).items() if not k.startswith("plugin_"))
    return [
        H("l1-daily", 1, "It does real work, every day", "Does the agent work on their real code most days?",
          MET if days >= d_met and code >= c_met else WEAK if days >= d_weak else NOT,
          f"{days} active days of the {win} days of chats kept; {code} typed sessions changed files",
          f">= {d_met} days and >= {c_met} code-changing sessions (scaled to {win} days kept)",
          "Calendar days with a session the person typed in; 'real ticket' is only known by asking."),
        H("l1-changes", 1, "It changes the code, not just answers", "Does it edit their files, or do they copy answers over?",
          grade(share(code, typed), 0.20, 0.08), f"{code} of {typed} typed sessions edited files ({pct(share(code, typed))})",
          ">= 20% of typed sessions", "Edits by edit tools; shell-only edits are missed (undercounts)."),
        H("l1-tools", 1, "It fetches what it needs (tools others set up)",
          "Does the agent reach outside systems itself (tickets, repo host, databases)?",
          MET if (mcp_calls >= 20 or sum(real_tools.values()) >= 50) else WEAK if (mcp_calls or real_tools) else NOT,
          f"{mcp_calls} calls to connected servers (plugins' own excluded); outside CLIs: "
          + ", ".join(f"{k} {v}" for k, v in sorted(real_tools.items(), key=lambda x: -x[1])[:5]),
          ">= 20 server calls or >= 50 outside-CLI calls", "Docker and playwright don't count as outside systems.",
          "Plugin servers that run by themselves were left out."),
    ]


def level2(s, ev, ws):
    typed = s["with_a_person_typing"]
    code = ev["sessions_with_code_edits"] or 1
    plan, _ = of(ws["sessions_using_plan_mode"])
    sub, _ = of(ws["sessions_starting_subagents"])
    wt, _ = of(ws["sessions_in_worktrees"])
    comp_all, _ = of(ws["sessions_compacted"])
    typed_compact = sum(g.get("compact", 0) for g in s.get("_slash", {}).values())
    comp = max(0, comp_all - typed_compact)  # automatic compaction only (approx.: typed /compact counted per call)
    named = share(ev["prompts_naming_a_file"], ev["prompts_total"])
    checked = share(ev["commits_checked_after_last_edit"], ev["commits_in_sessions"])
    diff = share(ev["sessions_with_git_diff"], typed)
    review = share(ev["sessions_with_review_step"], code)
    inter = share(ws["interrupts"], typed)
    out = [
        H("l2-structures", 2, "Structures the change before the agent acts",
          "Before it touches code, do they plan or point it at the exact place?",
          MET if share(plan, typed) >= 0.05 and named >= 0.05 else WEAK if share(plan, typed) >= 0.02 or named >= 0.05 else NOT,
          f"plan mode in {plan} of {typed} sessions ({pct(share(plan, typed))}); {pct(named)} of prompts name a file; "
          f"long prompts p90 {ev['prompt_words_p90']} words", ">= 5% sessions planned and >= 5% prompts naming a file",
          "Planning in a ticket or another chat leaves no trace here."),
        H("l2-steers", 2, "Steers it while it works, and stops it when it drifts", "Do they stop a run that drifts?",
          grade(inter, 0.10, 0.03), f"{ws['interrupts']} stops (Esc) over {typed} typed sessions ({inter:.2f} per session)",
          ">= 0.10 stops per typed session", "Counts the stop, not whether the redirect was right."),
        H("l2-checks", 2, "Checks what comes back, and owns it", "Is the code checked after the last edit, before it ships?",
          MET if checked >= 0.5 and diff >= 0.3 else WEAK if checked >= 0.3 or diff >= 0.2 else NOT,
          f"{pct(checked)} of {ev['commits_in_sessions']} commits checked after the last edit; diff looked at in "
          f"{pct(diff)} of typed sessions; review step in {pct(review)}", ">= 50% commits checked and diff in >= 30%",
          "Checks the agent runs on its own because a rule file says so count too (template effect).",
          "A CLAUDE.md that tells the agent to run tests makes this look like the person checking."),
        H("l2-parallel", 2, "Parallel tasks in separate working copies (row 12)",
          "Do parallel tasks stay apart, and does side digging go to helpers?",
          MET if (share(wt, typed) >= 0.05 or s["max_sessions_open_at_once"] >= 3) and share(sub, typed) >= 0.10
          and share(comp, typed) <= 0.25 else WEAK if share(sub, typed) >= 0.05 or share(wt, typed) >= 0.02 else NOT,
          f"worktrees in {pct(share(wt, typed))} of sessions; up to {s['max_sessions_open_at_once']} open at once; "
          f"helpers in {pct(share(sub, typed))}; compacted on its own {pct(share(comp, typed))}",
          "worktrees >= 5% or 3+ open, helpers >= 10%, automatic compaction <= 25%",
          "Helpers the agent chose itself count; the person's choice is only seen when they ask for one."),
    ]
    out += chat_shape(s, typed, comp)
    return out


def chat_shape(s, typed, comp):
    """Guide rows 10 and 11 (L2 draft, carried into L3): what they do with a chat, and which kind of helper.

    Row 10: when a chat goes bad they start fresh, resume an old one, or fork a good one to try a side idea.
    Row 11: a blank helper (general-purpose, Explore, Plan, a named agent) needs a brief; a fork (`/subtask`, the
    built-in fork helper) already knows the whole chat. Knowing which to use = using both.
    Fork records depend on the Claude Code version, so no fork seen reads "can't tell", never "doesn't do it".
    """
    sub = s.get("_subagents", {})
    slash = {k: v for grp in s.get("_slash", {}).values() for k, v in grp.items()}
    cov = s.get("_cov", {})
    fork_helpers = sub.get("built-in", {}).get("fork", 0) + slash.get("subtask", 0)
    blank = sum(v for k, v in sub.get("built-in", {}).items() if k != "fork")
    named = sum(sum(g.values()) for k, g in sub.items() if k != "built-in")
    moves = {"resume": slash.get("resume", 0), "branch": slash.get("branch", 0), "fork": slash.get("fork", 0),
             "clear": slash.get("clear", 0), "compact they typed": slash.get("compact", 0),
             "continued in a new chat": cov.get("continued", 0),
             "forked sessions": cov.get("forked", 0)}
    n_moves = sum(moves.values())
    r10 = (CANT if n_moves == 0 else MET if n_moves >= 5 and share(comp, typed) <= 0.25 else WEAK)
    r11 = (NOT if fork_helpers == 0 and blank + named == 0 else
           MET if fork_helpers >= 5 and blank + named >= 10 else
           WEAK)
    return [
        H("l2-fresh-chat", 2, "Notices a chat has gone bad: fresh start, resume, or fork it (row 10)",
          "When a chat drifts or fills up, do they start clean, pick an old one back up, or fork a good one?",
          r10, "; ".join(f"{k} {v}" for k, v in moves.items() if v) or "none seen",
          ">= 5 resume/fork/branch/clear/compact moves they chose, automatic compaction <= 25% of sessions",
          "A fresh chat opened by hand after a bad one isn't linked to it yet; none seen = can't tell.",
          "Claude Code's own auto-compaction is not the person deciding."),
        H("l2-helpers", 2, "Side digging goes to helpers, and they pick fork vs blank (row 11)",
          "Do they use both kinds: a blank helper with a brief, and a fork that already knows the chat?",
          r11, f"forks that know the chat: {fork_helpers} (fork helper {sub.get('built-in', {}).get('fork', 0)}, "
               f"/subtask {slash.get('subtask', 0)}); blank helpers {blank}; named agents {named}",
          ">= 5 forks and >= 10 blank or named helpers",
          "Helpers the agent starts on its own count too. No fork seen may be an older Claude Code: can't tell, ask.",
          "The agent, not the person, often picks the helper type."),
    ]


def level3(s, ev, ws, extra):
    typed = s["with_a_person_typing"]
    # A. Rules: written by the person, below the repo root, in more than one repo
    rp = extra["rules"]["repos"]
    repos_with_own_nested = [r for r in rp if r["nested_rule_files"] >= 1 and r["by_person"] >= 1]
    shared = [r for r in rp if r["by_person"] >= 1 and r["other_authors"] >= 1]
    edits = ev["instruction_edits_by_kind"]
    a = H("l3-rules", 3, "Rules in the right place: it knows without being told",
          "Did THEY write rules below the repo root, so the right rule reaches the right place?",
          MET if len(repos_with_own_nested) >= 2 and edits.get("instructions", 0) >= 10 else
          WEAK if repos_with_own_nested or edits.get("instructions", 0) >= 10 else NOT,
          f"{len(repos_with_own_nested)} repos with folder rule files they committed; {edits.get('instructions', 0)} "
          f"rule-file edits in sessions; skills {edits.get('skill', 0)} edits", ">= 2 repos with own folder rules and >= 10 rule edits",
          "Authorship by git; loading alone proves the tool works, not the person.",
          "Claude Code loads folder files on its own; only who wrote them counts.")
    # B. Corrections fixed where they belong
    sc = ev["sessions_with_corrections"]
    fixed = ev["sessions_editing_tooling_when_corrected"]
    b = H("l3-corrections", 3, "It learns from its mistakes: a correction gets fixed at the source",
          "After correcting the agent, do they change a rule, skill or hook so it doesn't happen again?",
          (MET if share(fixed, sc) >= 0.30 else WEAK if share(fixed, sc) >= 0.10 else NOT) if sc >= 10 else CANT,
          f"{fixed} of {sc} sessions with a correction also edited a rule, skill, hook or memory ({pct(share(fixed, sc))})",
          ">= 30% of corrected sessions, with >= 10 corrected sessions",
          "Corrections found by an English word list (misses other languages; 60-76% agreement with a hand check).")
    # C. Memory the person set up
    mem = extra["memory"]
    own = [m for m in mem["own_stores"] if m["days_touched"] >= 5 and (m["has_index"] or m["folders"] >= 2)]
    c = H("l3-memory", 3, "It remembers: memory with a shape they chose",
          "Is there a memory they built (structure, index) that keeps changing over days?",
          MET if own and edits.get("memory", 0) >= 10 else WEAK if mem["own_stores"] else NOT,
          f"{len(own)} structured stores changed on 5+ days (largest: "
          + (f"{own[0]['path']}, {own[0]['files']} notes, {own[0]['days_touched']} days" if own else "none")
          + f"); {edits.get('memory', 0)} memory edits in sessions", ">= 1 own structured store touched on >= 5 days",
          "Folder found by name and shape, not by our tool names.",
          f"Claude Code's own default memory ({mem['claude_code_default_memory_files']} notes) is NOT counted.")
    owners = [x.get("owner") or ("installed" if x.get("is_symlink") else "own") for x in extra["own_skills"]]
    own_sk = [o for o in owners if o == "own"]
    team_sk = [o for o in owners if o == "team"]
    sup = H("l3-support", 3, "Support: packs repeated procedures, works for more than them",
            "Do they write their own skills, and edit rule files others also use?",
            MET if len(own_sk) + len(team_sk) >= 3 and (shared or team_sk) else WEAK if own_sk or team_sk or shared else NOT,
            f"{len(own_sk)} skills they wrote, {len(team_sk)} they co-wrote with others (links followed to their repo, "
            f"authorship by git); {len(shared)} repos where they and others edit rules",
            ">= 3 own or co-written skills, and something shared with others", "Support only; it can't make L3 on its own.")
    return [a, b, c, sup]


def level4(s, ev, ws, extra):
    plugins = extra["plugins"]
    own_p = [p for p in plugins if p["own"]]
    own_p_hooks = [p for p in own_p if p["hook_events"]]
    blocking = [p for p in own_p_hooks if p["hooks_can_block"]]
    uh = [h for h in extra["user_hooks"] if h["own"]]
    uh_block = [h for h in uh if h["can_block"]]
    hb = ev.get("hook_blocks", 0)
    build = H("l4-build", 4, "Builds code that runs around the agent (hooks, plugins, servers)",
              "Did they write something that runs around the agent and changes what it does?",
              MET if (blocking or uh_block) and hb >= 10 else WEAK if own_p_hooks or uh else NOT,
              f"{len(own_p)} plugins from their own git ({len(own_p_hooks)} with hooks, {len(blocking)} can block); "
              f"{len(uh)} own hooks in settings ({len(uh_block)} can block); {hb} blocks seen in sessions",
              ">= 1 own hook that can block, and >= 10 blocks seen", "Ownership by git authorship of the plugin folder.",
              "Plugins installed from someone else's repo are not counted.")
    hs = ev.get("headless_by_starter", {})
    own_runs = hs.get("own code calling the Agent SDK", 0) + hs.get("claude -p from a script or tool", 0)
    runs = H("l4-runs", 4, "Runs agents without sitting there (scripts, schedules, lists of items)",
             "Do agents run from their own code, on a schedule or over a list?",
             MET if own_runs >= 50 and (ev.get("headless_batches", 0) >= 3 or ev.get("scheduled_fires", 0) >= 3) else
             WEAK if own_runs >= 5 else NOT,
             f"{own_runs} runs started by their own code; {ev.get('headless_batches', 0)} batches; "
             f"{ev.get('scheduled_fires', 0)} scheduled fires; {ev.get('headless_bounded_calls', 0)} with a cost or turn cap",
             ">= 50 own-code runs and >= 3 batches or schedules",
             "What started a run is inferred from its shape; plugins also start runs.")
    edits = ev["instruction_edits_by_kind"]
    setup_edits = edits.get("skill", 0) + edits.get("agent", 0) + edits.get("hooks_or_settings", 0)
    evals = H("l4-evals", 4, "Proves the setup works: re-runs evals after changing it",
              "After changing a skill, agent or hook, do they run evals on it?",
              MET if ev.get("eval_commands", 0) >= 20 and hs.get("eval runs", 0) >= 10 and setup_edits >= 20 else
              WEAK if ev.get("eval_commands", 0) >= 5 else NOT,
              f"{ev.get('eval_commands', 0)} eval commands; {hs.get('eval runs', 0)} eval runs of agents; "
              f"{setup_edits} edits to skills, agents and hooks", ">= 20 eval commands, >= 10 agent eval runs, >= 20 setup edits",
              "Not yet joined edit -> eval on the same item (next fix); 'eval' also matches ordinary tests named eval.")
    perms = extra["permissions"]
    safe = H("l4-safe", 4, "Keeps it safe and cheap (guardrails that really stop things, caps)",
             "Is there something that really stops a dangerous action, and a cap on runaway runs?",
             MET if (uh_block or blocking or perms.get("deny", 0)) and ev.get("headless_bounded_calls", 0) >= 5 else
             WEAK if perms.get("deny", 0) or uh_block or blocking else NOT,
             f"{perms.get('deny', 0)} deny rules; {len(uh_block) + len(blocking)} own hooks that can block; "
             f"{ev.get('headless_bounded_calls', 0)} runs with a cap", "a guardrail + >= 5 capped runs",
             "A deny rule doesn't stop shell writes; Claude Code's own safety check is not theirs.")
    return [build, runs, evals, safe]


def l2_gate(hs) -> bool:
    """L2's four ladder lines. The parallel line is guide group L2-D: rows 10 (fresh chat), 11 (helpers), 12 (worktrees)."""
    by = {h["id"]: h["result"] for h in hs}
    rows = [by["l2-fresh-chat"], by["l2-helpers"], by["l2-parallel"]]
    parallel = (MET if rows.count(MET) >= 2 and NOT not in rows else
                NOT if all(r in (NOT, CANT) for r in rows) and NOT in rows else WEAK)
    lines = [by["l2-structures"], by["l2-steers"], by["l2-checks"], parallel]
    return lines.count(MET) >= 3 and NOT not in lines


GATES = {
    1: ("all 3 met", lambda hs: all(h["result"] == MET for h in hs)),
    2: ("3 of its 4 lines met, none 'not met'; the parallel line = rows 10-12, met when 2 of the 3 are met "
        "(L2 is still a draft standard)", lambda hs: l2_gate(hs)),
    3: ("2 of the 3 chains (rules, corrections, memory) met, plus support at least weak",
        lambda hs: sum(h["result"] == MET for h in hs[:3]) >= 2 and hs[3]["result"] in (MET, WEAK)),
    4: ("build met AND evals met, runs or safety at least weak",
        lambda hs: hs[0]["result"] == MET and hs[2]["result"] == MET and (MET in (hs[1]["result"], hs[3]["result"])
                                                                     or WEAK in (hs[1]["result"], hs[3]["result"]))),
}


def enough_to_read(s, ev) -> tuple[bool, str]:
    kept = days_kept(s)
    # enough to read = enough chats they typed; whether the agent edits code is a Level 1 check, not a readability bar
    ok = s["active_days"] >= scaled(10, kept, 3) and s["with_a_person_typing"] >= scaled(15, kept, 5)
    return ok, (f"{s['active_days']} active days of the {kept} days of chats kept, {s['with_a_person_typing']} typed sessions, "
                f"{ev['sessions_with_code_edits']} that changed code")


def place(scan: dict, extra: dict | None = None) -> dict:
    h = scan["habits"]
    s = dict(h["sessions"])
    s["_mcp_used"] = h.get("mcp_servers_used", {})
    s["_subagents"] = h.get("subagents", {})
    s["_slash"] = h.get("slash_commands", {})
    s["_cov"] = h.get("coverage", {}).get("sessions", {})
    s["_logs"] = h.get("coverage", {}).get("logs", {})
    ev, ws = h["evidence"], h["working_style"]
    if extra is None:
        extra = read_extra(scan)
    levels = {1: level1(s, ev, ws), 2: level2(s, ev, ws), 3: level3(s, ev, ws, extra), 4: level4(s, ev, ws, extra)}
    return decide(s, ev, h, levels, extra)


def read_extra(scan: dict) -> dict:
    """Everything this machine shows beyond the scan: ownership, hooks, memory, rule placement."""
    ids = identities(scan)
    return {
        "identities": sorted(ids),
        "plugins": own_plugins(ids),
        "user_hooks": own_user_hooks(ids),
        "memory": memory_stores(scan, ids),
        "rules": rule_placement(scan, ids),
        "own_skills": skill_owners(scan, ids),
        "permissions": scan["setup"]["settings"].get("user", {}).get("permissions", {}),
    }


def decide(s, ev, h, levels, extra) -> dict:
    ok, seen = enough_to_read(s, ev)
    placed, gates = 0, {}
    for n in (1, 2, 3, 4):
        rule, fn = GATES[n]
        passed = fn(levels[n])
        gates[n] = {"rule": rule, "passed": passed}
        if passed and placed == n - 1:
            placed = n
    # how sure: the share of the placed level's heuristics that are 'met', and whether the next one is close
    nxt = placed + 1 if placed < 4 else None
    near = nxt and sum(x["result"] == MET for x in levels[nxt]) >= len(levels[nxt]) - 2
    return {
        "suggested_level": placed if ok else None,
        "enough_to_read": ok, "seen": seen,
        "next_level_close": bool(near),
        "gates": gates, "levels": {str(k): v for k, v in levels.items()}, "extra": extra,
        "note": "A suggestion for the interviewer, not a verdict. Thresholds are from one machine.",
    }


def to_md(r: dict) -> str:
    icon = {MET: "✅", WEAK: "🟡", NOT: "❌", CANT: "❔"}
    lv = r["suggested_level"]
    L = [f"# Suggested level: {'L' + str(lv) if lv else 'below L1' if lv == 0 else 'not enough to read'}", "",
         f"Read from: {r['seen']}. {r['note']}", ""]
    for n in ("1", "2", "3", "4"):
        g = r["gates"][int(n)] if int(n) in r["gates"] else r["gates"][n]
        L += [f"## Level {n} — gate: {g['rule']} → {'PASSED' if g['passed'] else 'not passed'}", "",
              "| | Guide line | What we measured | Bar | Can't be sure because |", "|---|---|---|---|---|"]
        for h in r["levels"][n]:
            caveat = h["why"] + (f" Tool-did-it: {h['tool_did_it']}" if h["tool_did_it"] else "")
            L.append(f"| {icon[h['result']]} | {h['guide_line']} | {h['value']} | {h['bar']} | {caveat} |")
        L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("--out")
    ap.add_argument("--md")
    a = ap.parse_args()
    r = place(json.loads(Path(a.scan).read_text()))
    r["gates"] = {int(k): v for k, v in r["gates"].items()}
    if a.out:
        Path(a.out).write_text(json.dumps(r, indent=1, default=str))
    md = to_md(r)
    if a.md:
        Path(a.md).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
