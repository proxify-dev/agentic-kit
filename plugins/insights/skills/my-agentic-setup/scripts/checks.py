"""checks — the "Checking the work" view, rebuilt to answer "so what?", not "how often?".

The template the other views copy: what happened next · who made it happen · split out what matters ·
what it cost · the worst real case.

  1 after a failed check: fixed and re-ran green / changed the test / committed while red / left it red
  2 who made the check happen: the person asked / the repo's instructions tell the agent / the agent on its
    own / a hook (from settings on disk) / a review helper or skill
  3 committed straight after editing: under a minute after the last code edit, nothing looked at in between
  4 what unchecked commits cost: a fix or revert touching the same files within 24h (git), checked vs not
  5 code vs notes: commits that touch only docs, notes, config or memory don't need a test

Inputs: Session.step_log (scan.read_session), the repo's git log (read-only), settings files on disk.
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

LOGS = "session logs (~/.claude/projects/*/*.jsonl), sessions you typed in"
NOTES_RE = re.compile(r"\.(md|mdx|txt|rst|json|jsonl|ya?ml|toml|lock|csv|ini|cfg)$|(^|/)(\.agents|\.claude|memory|docs?)/", re.I)
TEST_FILE_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|[._-](test|spec)\.\w+$|(^|/)test_\w+\.py$", re.I)
REWORK_RE = re.compile(r"\b(fix(es|ed)?|revert|oops|hotfix|bug|broken|typo|regression|undo)\b", re.I)
CHECK_CMD_RE = re.compile(r"\b(pytest|vitest|jest|go test|cargo test|npm (run )?test|pnpm test|tsc|ruff|eslint|mypy|lint|typecheck)\b", re.I)


def pt(text, rule, raw, conf="heuristic", caveat=None, label=None, source=LOGS):
    if not text:
        return ""
    how = {"source": source, "rule": rule, "raw": raw, "confidence": conf}
    if caveat:
        how["caveat"] = caveat
    d = {"text": text, "how": how}
    if label:
        d["label"] = label
    return d


def n(x):
    return f"{x:,}"


def pct(a, b):
    return round(100 * a / b) if b else 0


def git(root, *args):
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""


def repo_commits(root: Path, days: int) -> list[dict]:
    """The person's commits in this repo, all branches, with files and lines. Read-only."""
    email = git(root, "config", "user.email").strip()
    if not email:
        return []
    out = git(root, "log", "--all", f"--since={days + 1}.days", f"--author={email}", "--numstat",
              "--format=%x1e%H%x1f%ct%x1f%s")
    commits = []
    for chunk in out.split("\x1e"):
        if "\x1f" not in chunk:
            continue
        head, _, rest = chunk.partition("\n")
        h, ct, subj = (head.split("\x1f") + ["", "", ""])[:3]
        files, lines = [], 0
        for ln in rest.splitlines():
            parts = ln.split("\t")
            if len(parts) == 3:
                files.append(parts[2])
                lines += sum(int(x) for x in parts[:2] if x.isdigit())
        full = subj
        subj = subj if len(subj) <= 160 else subj[:157].rsplit(" ", 1)[0] + " …"   # for display only
        commits.append({"hash": h, "ct": int(ct or 0), "subject": subj, "full": full, "files": files, "lines": lines,
                        "code": any(not NOTES_RE.search(f) for f in files)})
    commits.sort(key=lambda c: c["ct"])
    return commits


def reworked(c: dict, later: list[dict]) -> dict | None:
    """A later commit within 24h that touches a file this one touched and says fix / revert / …"""
    fs = set(c["files"])
    for d in later:
        if d["ct"] - c["ct"] > 86400:
            break
        if d["ct"] > c["ct"] and fs & set(d["files"]) and REWORK_RE.search(d["full"]):
            return d
    return None


def instructions_mention_checks(root: Path) -> bool:
    for f in ("CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md", "CLAUDE.local.md"):
        p = root / f
        try:
            if p.is_file() and CHECK_CMD_RE.search(p.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            pass
    return False


def hook_checks(settings_paths: list[Path]) -> int:
    """Hooks whose command runs a test, lint or type check: the setup checks without being asked."""
    import json
    n_ = 0
    for p in settings_paths:
        try:
            hooks = (json.loads(p.read_text()) or {}).get("hooks") or {}
        except (OSError, ValueError):
            continue
        for ev in hooks.values():
            for m in ev or []:
                for h in m.get("hooks") or []:
                    if CHECK_CMD_RE.search(str(h.get("command", ""))):
                        n_ += 1
    return n_


def build(human: list, days: int, git_root, repo_name) -> dict:
    # git_root maps a session's folder to its repo; story passes scan.canonical_root, so a repo and its
    # worktrees are one root (else each worktree's `git log --all` matched the same commits again).
    # ── 1 after a failed check ───────────────────────────────────────────────
    after = Counter()
    worst_red = []
    for s in human:
        log = s.step_log
        for i, (k, ts, info) in enumerate(log):
            if k != "R" or not info["fail"]:
                continue
            outcome = "left it failing"
            for k2, ts2, info2 in log[i + 1:]:
                if k2 == "E" and TEST_FILE_RE.search(info2 or ""):
                    outcome = "changed a test file"
                    break
                if k2 == "R" and not info2["fail"]:
                    outcome = "fixed it and re-ran green"
                    break
                if k2 == "C":
                    outcome = "committed while it was failing"
                    worst_red.append((s, ts2))
                    break
            after[outcome] += 1
    fails = sum(after.values())
    checks_run = sum(1 for s in human for k, *_ in s.step_log if k == "R")

    # ── 2 who made the check happen ──────────────────────────────────────────
    who = Counter()
    rule_repos = {}
    for s in human:
        root = git_root(Path(s.cwd)) if s.cwd else None
        if root and root not in rule_repos:
            rule_repos[root] = instructions_mention_checks(root)
        for k, ts, info in s.step_log:
            if k == "T":
                who["you asked for it" if info["asked"] else
                    ("the repo's instructions say to" if root and rule_repos.get(root) else "the agent, on its own")] += 1
            elif k == "V":
                who[f"a review {info['by']}"] += 1
    settings = [Path.home() / ".claude" / "settings.json", Path.home() / ".claude" / "settings.local.json"]
    settings += [r / ".claude" / "settings.json" for r in rule_repos] + [r / ".claude" / "settings.local.json" for r in rule_repos]
    hooks = hook_checks(settings)

    # ── 3 + 4 + 5 commits, matched to git ────────────────────────────────────
    by_root = defaultdict(list)
    for s in human:
        root = git_root(Path(s.cwd)) if s.cwd else None
        if not root:
            continue
        last_e, looked = None, True
        for k, ts, info in s.step_log:
            if k == "E":
                last_e, looked = ts, False
            elif k in ("T", "V", "D"):
                looked = True
            elif k == "C" and ts:
                straight = (last_e is not None and not looked and (ts - last_e).total_seconds() < 60)
                by_root[root].append({"ts": ts, "checked": looked or last_e is None, "straight": straight, "session": s})
    rows, matched = [], []
    for root, cs in by_root.items():
        commits = repo_commits(root, days)
        used = set()
        for c in sorted(cs, key=lambda c: c["ts"]):
            t0 = c["ts"].timestamp()
            for j, g in enumerate(commits):
                if j in used:
                    continue
                if -30 <= g["ct"] - t0 <= 180:
                    used.add(j)
                    g2 = dict(g, checked=c["checked"], straight=c["straight"], root=root, session=c["session"])
                    g2["rework"] = reworked(g, commits[j + 1:])
                    matched.append(g2)
                    break
    code = [m for m in matched if m["code"]]
    notes = [m for m in matched if not m["code"]]
    unchecked_code = [m for m in code if not m["checked"]]
    checked_code = [m for m in code if m["checked"]]
    rw_un = sum(1 for m in unchecked_code if m["rework"])
    rw_ch = sum(1 for m in checked_code if m["rework"])
    straight_big = [m for m in code if m["straight"] and m["lines"] >= 200]
    straight_all = [m for m in code if m["straight"]]
    unmatched = sum(len(cs) for cs in by_root.values()) - len(matched)

    per_repo = defaultdict(lambda: [0, 0, 0, 0])
    for m in code:
        r = per_repo[repo_name(m["root"])]
        r[0] += 1
        r[1] += m["checked"]
        r[2] += bool(m["rework"])
        r[3] += m["straight"]
    for name, (c, k, rw, st) in sorted(per_repo.items(), key=lambda kv: -kv[1][0])[:8]:
        rows.append([name, n(c), f"{pct(k, c)}%", f"{pct(rw, c)}%", n(st)])
    best = max(((name, pct(k, c)) for name, (c, k, _, _) in per_repo.items() if c >= 20), key=lambda x: x[1], default=None)
    worst = min(((name, pct(k, c)) for name, (c, k, _, _) in per_repo.items() if c >= 20), key=lambda x: x[1], default=None)

    examples = []
    for m in sorted([m for m in unchecked_code if m["rework"]], key=lambda m: -m["lines"])[:2]:
        examples.append({"label": f"Unchecked, then fixed ({m['lines']} lines)",
                         "text": f"{m['subject']}\n→ {m['rework']['subject']}",
                         "meta": f"{repo_name(m['root'])} · {m['hash'][:8]} · "
                                 f"{round((m['rework']['ct'] - m['ct']) / 3600, 1)} h later"})
    for m in sorted(straight_big, key=lambda m: -m["lines"])[:1]:
        examples.append({"label": f"Committed straight after editing ({m['lines']} lines)", "text": m["subject"],
                         "meta": f"{repo_name(m['root'])} · {m['hash'][:8]}"})

    green = after.get("fixed it and re-ran green", 0)
    red = after.get("committed while it was failing", 0)
    lead = (f"When a check fails, {pct(green, fails)}% get fixed and re-run green; {red} times work was committed while still failing. "
            f"Unchecked code commits needed a fix within a day {pct(rw_un, len(unchecked_code))}% of the time, "
            f"checked ones {pct(rw_ch, len(checked_code))}%.") if fails else "No failed checks in the window."

    points = [
        pt("After a failed check: " + ", ".join(f"{k} {v}" for k, v in after.most_common()) + ".",
           "for each failed check result, the next decisive step in the same session: a passing re-run, an edit to a test file, a commit, or the end",
           f"{fails} failed of {checks_run} checks with a result", "heuristic",
           "a failure is a non-zero exit or failure words in the output; a check piped through `| tail` can hide its exit code; "
           "a re-run that passes in another session is not seen", label="After a failed check"),
        pt("Who made the checks happen: " + ", ".join(f"{k} ({v})" for k, v in who.most_common()) +
           (f"; plus {hooks} hooks in settings that run a check by themselves." if hooks else "; no hook runs a check by itself."),
           "per check: your last message asked for it → 'you asked'; else the repo's CLAUDE.md/AGENTS.md mentions a check command → "
           "'instructions'; else 'the agent, on its own'. Hooks counted from settings files on disk",
           f"{sum(who.values())} checks; {hooks} check-running hooks", "heuristic",
           "'asked' is a word match on the last message; a hook's own runs don't appear as tool calls, only the hook count does",
           label="Who made it happen"),
        pt(f"{len(straight_all)} code commits went in less than a minute after the last edit with nothing looked at in between; "
           f"{len(straight_big)} of them over 200 lines.",
           "a commit within 60 s of the last code edit, with no check, review or diff in between; size from git",
           f"{len(straight_all)} straight commits, {len(straight_big)} ≥200 lines", "heuristic",
           "reading the diff in the editor, outside the agent, is invisible", label="Committed without looking"),
        pt(f"Of {n(len(matched))} commits matched in git, {len(notes)} touched only notes, docs or config: they need no test. "
           f"Counting code only, {pct(len(checked_code), len(code))}% were checked.",
           "each commit the agent made, matched to git by repo and time (within 3 min); notes = only .md/.json/.yaml/… or memory/docs folders",
           f"{len(matched)} matched, {unmatched} not found in git (other author, amended, or outside the window)", "solid",
           label="Code vs notes"),
        pt(f"What skipping costs: unchecked code commits needed a fix or revert within 24 h {pct(rw_un, len(unchecked_code))}% of the time "
           f"({rw_un} of {len(unchecked_code)}); checked ones {pct(rw_ch, len(checked_code))}% ({rw_ch} of {len(checked_code)}).",
           "a later commit of yours, same repo, within 24 h, touching a same file, whose message says fix / revert / bug / typo …",
           f"unchecked {rw_un}/{len(unchecked_code)}, checked {rw_ch}/{len(checked_code)}", "heuristic",
           "a 'fix' commit may fix something else in the same file; a fix without those words is missed",
           label="What it cost", source="git log of each repo (read-only), matched to " + LOGS),
        pt(f"Your own best: {best[0]} is checked {best[1]}% of the time, {worst[0]} {worst[1]}%. What's different about {best[0]}?"
           if best and worst and best[1] - worst[1] >= 15 else "",
           "projects with at least 20 code commits, highest and lowest checked share", "", "solid", label="Your own best"),
    ]
    return {
        "id": "check", "title": "Checking the work", "lead": lead,
        "how": {"source": LOGS + "; git log of each repo; settings files on disk",
                "rule": "the order of steps in a session (edit → check → result → commit), matched to your git commits",
                "raw": f"{checks_run} checks with a result, {fails} failed; {len(matched)} commits matched", "confidence": "heuristic",
                "caveat": "tests run by CI, by a hook, or in another session are not in a session's own steps",
                "code": "checks.build"},
        "points": [p for p in points if p],
        "numbers": [{"value": f"{pct(green, fails)}%", "label": "failed checks fixed and re-run green"},
                    {"value": str(red), "label": "commits made while a check was failing"},
                    {"value": f"{pct(len(checked_code), len(code))}%", "label": "of code commits checked"},
                    {"value": f"{pct(rw_un, len(unchecked_code))}% vs {pct(rw_ch, len(checked_code))}%", "label": "needed a fix within a day: unchecked vs checked"}],
        "table": {"caption": "Where the checking happens, code commits only", "columns": ["Project", "Code commits", "Checked", "Fixed within a day", "Straight after editing"],
                  "rows": rows,
                  "how": {"source": "git + " + LOGS, "rule": "per repo, code commits matched to an agent session", "confidence": "heuristic"}},
        "examples": examples,
        # limits.gate / limits.coverage read these: the view's own N, and what it matched in git
        "_gate": [(checks_run, "checks with a result", "moments", False), (len(code), "code commits", "moments", False)],
        "_coverage": {"read": [f"{n(len(matched))} of the agent's commits matched to the repo's git log (same repo, within 3 minutes)"],
                      "skipped": [f"{n(unmatched)} commits not found in git (other author, amended, or outside the window)"] if unmatched else []},
    }
