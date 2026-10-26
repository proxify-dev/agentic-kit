#!/usr/bin/env python3
"""scan — rebuild a person's agentic-engineering setup and habits from the files Claude Code keeps.

Reads only. Writes nothing unless --out is given. Needs python3 stdlib, nothing installed: no kit,
no plugin, no uv. It must work on a machine that has never seen Proxify's tooling.

Two layers, reported apart on purpose, because installed is not used:
  setup   what is on disk: settings, hooks, plugins, own skills/agents/commands, MCP servers,
          instruction files (CLAUDE.md / AGENTS.md) in the repos they work in, other agent tools.
  habits  what the session logs (~/.claude/projects/*/*.jsonl) show over the last --days:
          planning, subagents, skills, worktrees, parallel sessions, tests, commits, hooks firing.

Privacy contract — what this script emits:
  - names and counts only. Never prompt text, file contents, code, command lines, env values,
    MCP URLs or tokens. Bash commands are matched against fixed patterns locally; only the
    match counts leave this function.
  - --share masks what could name a client: repo paths/names become "repo-A", and names a
    person made themselves (skills, agents, commands, MCP servers) become "own-skill-1" etc.
    Names from public plugin marketplaces stay, they are public.

  python3 scan.py [--days 30] [--share] [--out FILE] [--max-repos 12] [--logs-dir DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))   # -I drops the script's own folder from the path
import limits  # noqa: E402

HOME = Path.home()

# Claude Code's own rule: CLAUDE_CONFIG_DIR, else ~/.claude; ~/.claude.json sits beside it.
CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
CLAUDE_JSON = (CLAUDE / ".claude.json") if os.environ.get("CLAUDE_CONFIG_DIR") else HOME / ".claude.json"

BUILTIN_AGENTS = {"general-purpose", "explore", "plan", "statusline-setup", "claude-code-guide", "fork", "claude", ""}
BUILTIN_COMMANDS = {
    "add-dir", "agents", "bashes", "btw", "bug", "clear", "compact", "config", "context", "cost", "doctor",
    "effort", "exit", "export", "fast", "feedback", "help", "hooks", "ide", "init", "insights", "install-github-app",
    "login", "logout", "loop", "mcp", "memory", "model", "output-style", "permissions", "plan", "plugin", "plugins",
    "pr-comments", "privacy-settings", "release-notes", "resume", "review", "rewind", "sandbox", "schedule",
    "security-review", "skills", "status", "statusline", "tasks", "terminal-setup", "theme", "todos", "upgrade",
    "usage", "vim", "workflows", "continue", "rename", "branch", "remote-control", "voice", "keybindings",
}
# Bash command families, counted never quoted.
BASH_PATTERNS = {
    "tests": re.compile(r"\b(pytest|jest|vitest|mocha|rspec|phpunit|go test|cargo test|(npm|pnpm|yarn|bun) (run )?test|mvn (-\S+ )*test|gradlew? test|dotnet test|playwright test|node --test)\b"),
    "lint_or_types": re.compile(r"\b(eslint|ruff|mypy|pyright|tsc|biome|prettier|golangci-lint|clippy|rubocop|phpstan)\b"),
    "git_commit": re.compile(r"\bgit (-C \S+ )?commit\b"),
    "git_worktree": re.compile(r"\bgit (-C \S+ )?worktree add\b"),
    "pr_create": re.compile(r"\bgh pr create\b"),
    "headless_claude": re.compile(r"\bclaude (-p|--print)\b"),
}
OTHER_AGENT_TOOLS = {
    "cursor": [HOME / ".cursor"],
    "codex": [HOME / ".codex"],
    "gemini-cli": [HOME / ".gemini"],
    "aider": [HOME / ".aider.conf.yml", HOME / ".aider.chat.history.md"],
    "continue": [HOME / ".continue"],
    "windsurf/codeium": [HOME / ".codeium", HOME / ".windsurf"],
    "github-copilot": [HOME / ".config" / "github-copilot"],
    "opencode": [HOME / ".config" / "opencode", HOME / ".local" / "share" / "opencode"],
    "amp": [HOME / ".config" / "amp"],
}


def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def md_lines(p: Path) -> int | None:
    try:
        return sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
    except OSError:
        return None


# ───────────────────────── setup ─────────────────────────

def settings_summary(p: Path) -> dict | None:
    s = load_json(p)
    if not isinstance(s, dict):
        return None
    perms = s.get("permissions") or {}
    hooks = s.get("hooks") or {}
    return {
        "keys_set": sorted(k for k in s if k not in ("env",)),
        "env_var_count": len(s.get("env") or {}),  # names stay local: a name can carry a client
        "model": s.get("model"),
        "output_style": s.get("outputStyle"),
        "permissions": {
            "default_mode": perms.get("defaultMode"),
            "allow": len(perms.get("allow") or []),
            "deny": len(perms.get("deny") or []),
            "ask": len(perms.get("ask") or []),
        },
        "hooks_by_event": {ev: sum(len(m.get("hooks") or []) for m in (ms or [])) for ev, ms in hooks.items()},
        "status_line": bool(s.get("statusLine")),
        "cleanup_period_days": s.get("cleanupPeriodDays"),
        "plugins_enabled": sorted(k for k, v in (s.get("enabledPlugins") or {}).items() if v),
    }


def plugin_wiring(path: Path) -> dict:
    def count(sub, pattern):
        d = path / sub
        return len(list(d.glob(pattern))) if d.is_dir() else 0
    hooks = load_json(path / "hooks" / "hooks.json") or {}
    hooks = hooks.get("hooks", hooks) if isinstance(hooks, dict) else {}
    mcp = load_json(path / ".mcp.json") or {}
    pj = load_json(path / ".claude-plugin" / "plugin.json") or {}
    return {
        "skills": count("skills", "*/SKILL.md"),
        "agents": count("agents", "*.md"),
        "commands": count("commands", "*.md"),
        "hook_events": sorted(hooks) if isinstance(hooks, dict) else [],
        "mcp_servers": len((mcp.get("mcpServers") or mcp) if isinstance(mcp, dict) else {}) + len(pj.get("mcpServers") or {}),
    }


def plugins_summary(enabled: set[str]) -> dict:
    reg = load_json(CLAUDE / "plugins" / "installed_plugins.json") or {}
    plugins = reg.get("plugins", reg) if isinstance(reg, dict) else {}
    out = []
    for key, installs in sorted(plugins.items()):
        inst = installs[0] if isinstance(installs, list) and installs else (installs if isinstance(installs, dict) else {})
        name, _, market = key.partition("@")
        # a marketplace that is a folder on this machine is one the person keeps themselves
        msrc = ((load_json(CLAUDE / "plugins" / "known_marketplaces.json") or {}).get(market) or {}).get("source") or {}
        row = {"plugin": key, "marketplace": market, "enabled": key in enabled, "scope": inst.get("scope"),
               "local_marketplace": msrc.get("source") == "directory"}
        ip = Path(inst.get("installPath", ""))
        if ip.is_dir():
            row.update(plugin_wiring(ip))
        out.append(row)
    markets = load_json(CLAUDE / "plugins" / "known_marketplaces.json") or {}
    msrc = {}
    for m, v in markets.items():
        src = (v or {}).get("source") or {}
        msrc[m] = src.get("repo") or src.get("url") or src.get("source")
    return {"installed": out, "marketplaces": msrc}


def own_items() -> dict:
    """Skills, agents, commands and output styles the person put in ~/.claude themselves."""
    # skills.sh (`npx skills add`) records every install, with its source repo, in ~/.agents/.skill-lock.json.
    # A skill in that list, or a symlink, was installed; one with no record was written or copied in by hand.
    lock = ((load_json(HOME / ".agents" / ".skill-lock.json") or {}).get("skills") or {})

    def skills():
        d = CLAUDE / "skills"
        rows = []
        if d.is_dir():
            for s in sorted(d.iterdir()):
                f = s / "SKILL.md"
                if f.is_file():
                    src = (lock.get(s.name) or {}).get("source")
                    rows.append({"name": s.name, "lines": md_lines(f), "has_scripts": any(s.glob("scripts/*")),
                                 "is_symlink": s.is_symlink() or bool(src), "source": src})
        return rows
    def mds(sub):
        d = CLAUDE / sub
        return [{"name": f.stem, "lines": md_lines(f)} for f in sorted(d.glob("*.md"))] if d.is_dir() else []
    return {"skills": skills(), "agents": mds("agents"), "commands": mds("commands"), "output_styles": mds("output-styles")}


def mcp_summary() -> dict:
    cj = load_json(CLAUDE_JSON) or {}
    def kinds(servers):
        return [{"name": n, "transport": (v or {}).get("type") or ("stdio" if (v or {}).get("command") else "http")}
                for n, v in sorted((servers or {}).items())]
    per_project = {p: len(v.get("mcpServers") or {}) for p, v in (cj.get("projects") or {}).items() if v.get("mcpServers")}
    return {"user_servers": kinds(cj.get("mcpServers")), "projects_with_local_servers": len(per_project)}


def global_instructions() -> dict:
    f = CLAUDE / "CLAUDE.md"
    out = {"claude_md_lines": md_lines(f) if f.is_file() else None}
    if f.is_file():
        txt = f.read_text(encoding="utf-8", errors="replace")
        out["imports"] = len(re.findall(r"(?m)^\s*@\S+", txt))
    rules = CLAUDE / "rules"
    out["rules_files"] = len(list(rules.rglob("*.md"))) if rules.is_dir() else 0
    return out


def machinery() -> dict:
    """What wraps the agent: a launcher that is not Claude Code's own binary, a model-call endpoint
    other than Anthropic's (the variable's NAME only; its value never leaves), a non-default config dir."""
    import shutil
    path = shutil.which("claude") or ""
    real = os.path.realpath(path) if path else ""
    stock = bool(re.search(r"/claude/versions/|node_modules/@anthropic-ai/claude-code|Caskroom/claude-code|/claude-code/", real))
    us = load_json(CLAUDE / "settings.json") or {}
    env_names = set((us.get("env") or {}).keys()) | set(os.environ)
    return {
        "launcher": "none found" if not path else ("stock" if stock else "wrapper"),
        "custom_endpoint": bool({"ANTHROPIC_BASE_URL", "ANTHROPIC_BEDROCK_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                                 "CLAUDE_CODE_USE_VERTEX"} & env_names) or bool(us.get("apiKeyHelper")),
        "config_dir_moved": bool(os.environ.get("CLAUDE_CONFIG_DIR")),
    }


AGENT_RUN_RE = re.compile(r"\bclaude\b|claude[-_]agent[-_]sdk|@anthropic-ai/|\banthropic\b|\bcodex\b", re.I)


def schedulers() -> dict:
    """Jobs the OS starts on its own (macOS LaunchAgents, cron, systemd user timers) and which of them start
    an agent: the command, or the script it runs, names claude / the Agent SDK / anthropic / codex.
    Claude Code's logs only show a scheduled run if it ran claude; a launchd job running a plain script that
    calls the API leaves no session, so the job itself is the evidence. Reads only; labels stay local."""
    import plistlib
    jobs = []

    def starts_agent(cmd: str) -> bool:
        if AGENT_RUN_RE.search(cmd):
            return True
        for tok in re.findall(r"(?:~|/)[\w./~-]+", cmd):
            f = Path(os.path.expanduser(tok))
            try:
                if f.is_file() and f.stat().st_size < 2_000_000 and AGENT_RUN_RE.search(f.read_text(errors="ignore")[:200_000]):
                    return True
            except OSError:
                pass
        return False

    la = HOME / "Library" / "LaunchAgents"
    for p in sorted(la.glob("*.plist")) if la.is_dir() else []:
        try:
            d = plistlib.loads(p.read_bytes())
        except Exception:
            continue
        label = str(d.get("Label") or p.stem)
        if label.startswith(("com.apple.", "com.google.keystone", "com.microsoft.", "homebrew.mxcl.")):
            continue  # the OS's or an app updater's own job, not the person's
        cmd = " ".join(map(str, d.get("ProgramArguments") or [d.get("Program") or ""]))
        timed = any(k in d for k in ("StartInterval", "StartCalendarInterval", "WatchPaths", "QueueDirectories"))
        jobs.append({"kind": "launchd", "label": label, "timed": timed, "starts_agent": starts_agent(cmd)})
    try:
        tab = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        tab = ""
    for i, line in enumerate(l for l in tab.splitlines() if l.strip() and not l.lstrip().startswith("#")):
        cmd = " ".join(line.split()[5:]) if not line.startswith("@") else " ".join(line.split()[1:])
        jobs.append({"kind": "cron", "label": f"cron-{i + 1}", "timed": True, "starts_agent": starts_agent(cmd)})
    sd = HOME / ".config" / "systemd" / "user"
    for t in sorted(sd.glob("*.timer")) if sd.is_dir() else []:
        svc = t.with_suffix(".service")
        cmd = " ".join(l.partition("=")[2] for l in (svc.read_text(errors="ignore").splitlines() if svc.is_file() else [])
                       if l.strip().startswith("ExecStart"))
        jobs.append({"kind": "systemd", "label": t.stem, "timed": True, "starts_agent": starts_agent(cmd)})
    return {"jobs": len(jobs), "timed": sum(j["timed"] for j in jobs),
            "start_an_agent": sum(j["starts_agent"] for j in jobs),
            "timed_agent_jobs": sum(j["timed"] and j["starts_agent"] for j in jobs),
            "agent_jobs": [{"kind": j["kind"], "label": j["label"], "timed": j["timed"]} for j in jobs if j["starts_agent"]]}


def other_tools() -> dict:
    """Which other agent tools left a folder here, and how many days since that folder last changed:
    present is not used, so the age is what tells a live tool from one tried once."""
    out = {}
    for name, paths in OTHER_AGENT_TOOLS.items():
        ages = [(time.time() - p.stat().st_mtime) / 86400 for p in paths if p.exists()]
        if ages:
            out[name] = {"days_since_touched": int(min(ages))}
    return out


def git_root(p: Path) -> Path | None:
    for d in [p, *p.parents]:
        if d == HOME or d == d.parent:
            break  # a dotfiles repo at ~ is not a project, and mapping all of ~ never finishes
        if (d / ".git").exists():
            return d
    return None


def git(root: Path, *args, timeout=15) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


_EMAIL = None


def repo_git(root: Path) -> dict:
    """Read-only git facts: is the instruction file shared with the team, who wrote it, how much of the
    person's own recent work came out of an agent session, and is there code that runs agents."""
    # The repo's own user.email (local config, else global): the identity this person commits with here.
    email = git(root, "config", "user.email").strip()
    tracked = [f for f in git(root, "ls-files", "--", "CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md").split("\n") if f]
    authors = {a for a in git(root, "log", "--format=%ae", "--", *tracked).split("\n") if a} if tracked else set()
    log = git(root, "log", "--all", "--since=30.days", f"--author={email}", "--format=%x1e%H%x1f%B") if email else ""
    mine = {}
    for m in log.split("\x1e"):
        if "\x1f" in m:
            h, body = m.split("\x1f", 1)
            mine[h] = bool(re.search(r"Co-Authored-By: Claude|Generated with \[Claude Code\]|🤖 Generated with", body, re.I))
    co = sum(mine.values())
    msgs = mine
    runner = git(root, "grep", "-l", "-I", "-E",
                 r"claude_agent_sdk|claude-agent-sdk|claude-code-action|claude (-p|--print)\b",
                 "--", ":!*.md", ":!*.jsonl", ":!*.lock", ":!*.json")
    files = git(root, "ls-files")
    return {
        "instructions_committed": bool(tracked),
        "instructions_authors": len(authors),
        "my_commits_30d": len(msgs),
        "my_commits_30d_agent_marked": co,
        "files_that_run_agents": len([f for f in runner.split("\n") if f]),
        "has_ci_agent": bool(re.search(r"claude-code-action", git(root, "grep", "-l", "claude-code-action", "--", ".github"))),
        "has_evals_dir": bool(re.search(r"(^|/)evals?/", files, re.M)),
        "has_devcontainer": (root / ".devcontainer").exists(),
        # private: main() de-duplicates these across worktrees of one repo, then drops them
        "_commits": mine,
        "_name": git(root, "config", "user.name").strip(),
    }


def repo_profile(root: Path) -> dict:
    def has(*rel):
        return any((root / r).exists() for r in rel)
    prof = {
        "claude_md_lines": md_lines(root / "CLAUDE.md") if (root / "CLAUDE.md").is_file() else (
            md_lines(root / ".claude" / "CLAUDE.md") if (root / ".claude" / "CLAUDE.md").is_file() else None),
        "agents_md": has("AGENTS.md"),
        "claude_local_md": has("CLAUDE.local.md"),
        "project_settings": has(".claude/settings.json"),
        "project_hooks": bool(((load_json(root / ".claude" / "settings.json") or {}).get("hooks"))),
        "project_skills": len(list((root / ".claude" / "skills").glob("*/SKILL.md"))) if (root / ".claude" / "skills").is_dir() else 0,
        "project_agents": len(list((root / ".claude" / "agents").glob("*.md"))) if (root / ".claude" / "agents").is_dir() else 0,
        "project_commands": len(list((root / ".claude" / "commands").glob("*.md"))) if (root / ".claude" / "commands").is_dir() else 0,
        "project_mcp": has(".mcp.json"),
        "cursor_rules": has(".cursor/rules", ".cursorrules"),
        "copilot_instructions": has(".github/copilot-instructions.md"),
    }
    prof.update(repo_git(root))
    # mdmap (vendored from the kit's vault plugin): how much instruction text loads every turn,
    # nested CLAUDE.md files, dead links. Sizes and counts only; paths stay here.
    if prof["claude_md_lines"] is not None or prof["agents_md"]:
        try:
            r = subprocess.run([sys.executable, "-I", str(HERE / "mdmap.py"), str(root), "--json"],
                               capture_output=True, text=True, timeout=20)
            m = json.loads(r.stdout)
            prof["instructions"] = {
                "always_loaded_files": m["counts"]["always_loaded"],
                "always_loaded_tokens": m.get("spine_tokens"),
                "reachable_docs": m["counts"]["reachable"],
                "nested_claude_md": len(m.get("nested_spines") or []),
                "dead_links": m["counts"]["dead_links"],
            }
        except Exception as e:  # mdmap is a nice-to-have; the scan stands without it
            prof["instructions"] = {"error": type(e).__name__}
    return prof


# ───────────────────────── habits ─────────────────────────

class Session:
    """One transcript, reduced to counts and an ordered list of step kinds. Prompt text is read here
    to classify it (correction? names a file? states what done looks like?) and is never stored."""
    __slots__ = ("sid", "cwd", "entry", "first", "last", "prompts", "tools", "modes", "subagents", "skills",
                 "commands", "mcp", "bash", "models", "compactions", "hooks_fired", "interrupts", "is_sub",
                 "agent_setting", "steps", "inst_edits", "corrections", "pwords", "p_path", "p_code",
                 "p_criteria", "scheduled", "hook_blocks", "agent_model_param", "integrations", "edit_first",
                 "explored_first",
                 # detail (story.py): verbatim excerpts and names. Masked/--report output never reads these.
                 "prompt_texts", "correction_texts", "inst_paths", "test_runners", "hook_sources", "hours",
                 "commit_cmds",
                 # orchestration: how sessions start, branch, talk to each other and hand work off
                 "marks", "bg_tools", "agent_descs", "lookup_cmds", "cost", "user_ts", "titles",
                 # checks: every step with its time, and each check's pass/fail (checks.py reads these)
                 "step_log", "_pending", "_asked",
                 # coverage: Claude Code version, which record signals the session carried per version,
                 # every file read (first time each), the first code edit, instruction files edited
                 "ver", "has_src", "sig_v", "reads", "first_edit_ts", "inst_files",
                 # every folder its records were written from: a chat started in a parent folder (~/work)
                 # that moves into a repo or worktree belongs to that repo, not to no repo
                 "cwds")

    def __init__(self, sid, is_sub):
        self.sid, self.is_sub = sid, is_sub
        self.cwd = self.entry = self.agent_setting = None
        self.cwds = Counter()
        self.first = self.last = None
        self.prompts = self.compactions = self.hooks_fired = self.interrupts = 0
        self.scheduled = self.hook_blocks = self.agent_model_param = 0
        self.p_path = self.p_code = self.p_criteria = 0
        self.tools, self.modes, self.subagents, self.skills = Counter(), set(), Counter(), Counter()
        self.commands, self.mcp, self.bash, self.models = Counter(), Counter(), Counter(), Counter()
        self.integrations = Counter()
        self.steps: list[str] = []          # E code edit · T tests · V review/verify · C commit · D diff
        self.inst_edits: list[tuple] = []   # (timestamp, kind) for edits to the agent's own instructions/tooling
        self.corrections: list = []         # timestamps of prompts that correct the agent
        self.pwords: list[int] = []
        self.edit_first = None              # True: the first tool call that changed code came before any read
        self.explored_first = None
        self.prompt_texts: list[tuple] = []      # (ts, words, text[:600])
        self.correction_texts: list[tuple] = []  # (ts, text[:300])
        self.inst_paths: list[tuple] = []        # (ts, kind, path)
        self.test_runners = Counter()            # the runner word that matched: pytest, vitest, tsc …
        self.hook_sources = Counter()            # "[tracer] SessionStart" — who sent it, on which event
        self.hours = Counter()                   # local hour of each prompt
        self.commit_cmds = 0
        # forked · continued_in · titled · named_agent · peer_msg · teammate_msg · task_report · image ·
        # pasted · scheduled_fire · advisor · compact_summary — one count per marker the transcript carries
        self.marks = Counter()
        self.bg_tools = Counter()                # tool calls run in the background (Agent, Bash)
        self.agent_descs: list[tuple] = []       # (subagent_type, description[:80]) — what helpers were sent to do
        self.lookup_cmds = Counter()             # reads of past sessions or memory from the shell
        self.cost = 0.0                          # the session's own running cost (cost-state), last value
        self.user_ts: list = []                  # every user-record timestamp: gaps show coming back later
        self.titles: list[str] = []              # names given to the session (/rename, agent names)
        # (kind, ts, info): E edit (info = path) · T check (info = {id, runner, asked}) · R its result
        # (info = {"fail": bool}) · C commit · D diff looked at · V review helper or skill
        self.step_log: list[tuple] = []
        self._pending: set = set()               # tool_use ids of checks waiting for their result
        self._asked = False                      # the last prompt asked for a test, check or review
        self.ver = None                          # Claude Code version on the first record
        self.has_src = False                     # Claude Code wrote promptSource on a message in this file
        self.sig_v = defaultdict(set)            # limits.SIGNALS: signal -> versions it was seen in
        self.reads = {}                          # (absolute path, how) -> time first read; how: read · bash · attached · subagent
        self.first_edit_ts = None
        self.inst_files = set()                  # instruction/tooling files edited, as repo_file_key()


CMD_RE = re.compile(r"<command-name>/?([^<\s]+)</command-name>")

# Prompt classifiers. They run on the prompt text inside read_session; only the booleans survive.
CORRECTION_RE = re.compile(
    r"^\s*(no\b|nope\b|wrong\b|that'?s not|that is not|not what|don'?t\b|do not\b|stop\b|revert|undo\b|"
    r"actually\b|instead\b|why did you|why are you|you didn'?t|you forgot|you missed|this is wrong|"
    r"it'?s (still )?(broken|failing|wrong)|still (broken|failing|wrong|not))|\bI (said|told you|asked you)\b", re.I)
PATH_RE = re.compile(r"(^|\s)@\S+|\b[\w./-]+\.(py|ts|tsx|js|jsx|go|rs|java|kt|rb|php|cs|swift|md|json|ya?ml|sql|sh|toml)\b|(^|\s)(\.{0,2}/)?[\w-]+/[\w./-]+")
CODE_RE = re.compile(r"```|Traceback \(most recent|\b\w*(Error|Exception)\b:|^\s+at \S+\(|FAILED|panic:", re.M)
CRITERIA_RE = re.compile(r"\b(should|must|expected|acceptance|done when|so that|make sure|ensure|until (it|the|all)|"
                         r"tests? (pass|should|must)|without (breaking|changing))\b", re.I)

# What an edit touched: the agent's own instructions and tooling, or the product.
def edit_kind(path: str) -> str:
    p = path.replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    if base in ("CLAUDE.md", "AGENTS.md", "CLAUDE.local.md", "GEMINI.md", ".cursorrules") or "/.claude/rules/" in p \
            or "/.cursor/rules/" in p or base == "copilot-instructions.md":
        return "instructions"
    if base == "SKILL.md" or ("/skills/" in p and p.endswith(".md")):
        return "skill"
    if p.endswith(".md") and ("/.claude/agents/" in p or "/agents/" in p and "/.claude-plugin" not in p and "/plugins/" in p):
        return "agent"
    if p.endswith(".md") and "/commands/" in p and ("/.claude/" in p or "/plugins/" in p):
        return "command"
    if base in ("settings.json", "settings.local.json", "hooks.json") and ("/.claude" in p or "/hooks/" in p) or "/hooks/" in p:
        return "hooks_or_settings"
    if base == "MEMORY.md" or re.search(r"/[\w.-]*memory[\w.-]*/", p) and p.endswith(".md"):
        return "memory"
    if p.endswith((".md", ".mdx", ".txt", ".rst")):
        return "docs"
    return "code"


REVIEW_AGENT_RE = re.compile(r"review|verif|critic|check|qa\b|test|audit|judge|refut", re.I)
INTEGRATIONS = {
    "github-cli": r"\bgh (pr|issue|api|run|repo|release)\b", "gitlab-cli": r"\bglab\b", "docker": r"\bdocker(-compose| compose)?\b",
    "kubernetes": r"\b(kubectl|helm|k9s)\b", "aws": r"\baws \w", "gcloud": r"\b(gcloud|gsutil)\b", "bigquery": r"\bbq (query|ls|show)\b",
    "azure": r"\baz \w", "terraform": r"\b(terraform|tofu)\b", "postgres": r"\b(psql|pg_dump)\b", "mysql": r"\bmysql\b",
    "supabase": r"\bsupabase \w", "railway": r"\brailway \w", "vercel": r"\bvercel\b", "fly": r"\bfly(ctl)? \w",
    "stripe": r"\bstripe \w", "jira/linear": r"\b(jira|linear) \w", "sentry": r"\bsentry-cli\b", "playwright": r"\bplaywright\b",
}
INTEGRATIONS = {k: re.compile(v) for k, v in INTEGRATIONS.items()}
BOUNDED_HEADLESS_RE = re.compile(r"\bclaude\b[^\n|;&]*\s-p\b[^\n|;&]*--(max-turns|max-budget|allowedTools|allowed-tools|disallowedTools|permission-mode|tools)\b")
EVALS_RE = re.compile(r"\b(promptfoo|deepeval|inspect eval|claude plugin eval|ragas|braintrust)\b|\bevals?/|\bevals?\.(py|ts|yaml|json)\b")
# Looking things up across sessions from the shell: past transcripts, memory stores, session indexes.
LOOKUP_RE = re.compile(r"\b(tracer (sessions|trace)( \w+)?|observer (search|timeline|get|patterns)|ak (memory|sessions|trace|vault|resolve)( \w+)?|"
                       r"claude (--resume|-r|--continue|-c)\b|\.claude/projects/)")
# A check failed: the tool reported an error (non-zero exit), or its output says so. Pipes (`| tail`) hide the
# exit code, so the text is read too.
CHECK_FAIL_RE = re.compile(r"Exit code [1-9]|\b[1-9]\d* (failed|errors?)\b|^FAILED|\bFAILED \w|error TS\d+|AssertionError|"
                           r"Tests?:\s+[1-9]\d* failed|✗|✖|panicked at|BUILD FAILED|npm ERR!", re.M)
# The person asked for a check in their last message.
ASK_CHECK_RE = re.compile(r"\b(tests?|pytest|run the (tests|checks)|check (it|that|this|if)|lint|typecheck|verify|make sure it works|review)\b", re.I)
GIT_DIFF_RE = re.compile(r"\bgit (-C \S+ )?(diff|show)\b")
SKIP_PREFIXES = ('{"type":"api-request', '{"type":"file-history', '{"type":"api-request-blob')


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


_SEEN_UUIDS: set = set()
RUN = Counter()   # facts about the scan itself, for dev mode: files, records, skips, versions


def first_sighting(d: dict) -> bool:
    """A forked or continued session starts with a copy of its parent's records, same uuid. Count a
    record the first time it is seen across all transcripts, so a fork does not recount its parent's
    prompts, tool calls and commands (found as /subtask 164 vs 13 typed). Called once per record."""
    u = d.get("uuid")
    if not u:
        return True
    if u in _SEEN_UUIDS:
        return False
    _SEEN_UUIDS.add(u)
    return True


# ── what a message is, and who sent it (F1, F7) ──────────────────────────────────────────────────
# Claude Code writes promptSource on a message: "typed" (a person), "queued" (a person, typed while the agent
# was busy), "sdk" (a program), "system" (a hook or notification). Trust it over the entrypoint: a person
# types through the terminal, the IDE extensions and the desktop app alike, and a program can sit behind any
# of them. Only a file that never carries the field (an older Claude Code) falls back on the entrypoint and
# on how the text looks.
HUMAN_SOURCES = {"typed", "queued"}
PROGRAMMATIC_ENTRY = ("sdk",)
# Apps a person types into that run Claude Code through the Agent SDK, so Claude Code marks the person's message
# promptSource "sdk", the same as a script's. Seen 2026-10-26: VS Code extension 2.1.296 starts Claude Code with
# CLAUDE_CODE_ENTRYPOINT=claude-vscode and stream-json input, and the typed message is written as "sdk". Without
# this, every VS Code chat counted as a program's run and was left out (a member got "not enough to read").
PERSON_APPS = ("claude-vscode", "claude-desktop", "claude-jetbrains")
_SEEN_PROMPTS: set = set()


def prompt_kind(d: dict, s: "Session", text: str) -> str | None:
    """typed · queued · app (a person, typing in an app that drives Claude Code) · fallback (a person, by the old
    rule) · program · None (not a message)."""
    if d.get("isSidechain"):
        return None   # a helper's brief from the main agent, not the person
    src = d.get("promptSource")
    if src is not None:
        if src in HUMAN_SOURCES:
            return src
        if src == "sdk" and (d.get("entrypoint") or s.entry or "") in PERSON_APPS:
            return "app"
        return "program"
    if s.has_src or (s.entry or "").startswith(PROGRAMMATIC_ENTRY):
        return None   # the file carries the field elsewhere (or a program drove it): this one is machine-made
    if not text.strip() or text.lstrip().startswith("<"):
        return None
    return "fallback"


def first_prompt_sighting(text: str, ts) -> bool:
    """A resumed or forked chat can copy a message under a new uuid; the same text at the same second is the
    same message. first_sighting() catches same-uuid copies, this catches the rest."""
    key = (hashlib.md5(text.strip()[:4000].encode("utf-8", "replace"), usedforsecurity=False).digest(), int(ts.timestamp()))
    if key in _SEEN_PROMPTS:
        return False
    _SEEN_PROMPTS.add(key)
    return True


# ── files: one path inside the repo, and reads through the shell (F5, F6) ────────────────────────
FOLD = str.lower if sys.platform in ("darwin", "win32") else str   # these file systems ignore case
WORKTREE_DIR_RE = re.compile(r"^(?P<repo>.+?)/\.claude/worktrees/[^/]+")   # where Claude Code puts its own
_CHECKOUTS: dict = {}
_DIR_ROOTS: dict = {}


def norm_path(p, cwd: str | None = "") -> str:
    """An absolute, normalised path ('' when a relative path has no folder to start from)."""
    p = str(p or "").strip().strip("'\"")
    if not p:
        return ""
    if "\\" in p and (re.match(r"^[A-Za-z]:", p) or p.startswith("\\\\")):
        p = p.replace("\\", "/")
    if p.startswith("~"):
        p = str(HOME) + p[1:]
    if not (p.startswith("/") or re.match(r"^[A-Za-z]:/", p)):
        if not cwd:
            return ""
        p = posixpath.join(cwd.replace("\\", "/"), p)
    return posixpath.normpath(p)


def main_checkout(root: Path) -> Path:
    """One answer for a repo's main checkout and every linked worktree of it: a worktree's .git is a file
    that points back into the main checkout's .git/worktrees/."""
    g = root / ".git"
    if g.is_file():
        try:
            m = re.match(r"gitdir:\s*(.+?)/\.git/worktrees/[^/\n]+$", g.read_text(errors="replace").strip())
        except OSError:
            return root
        if m and Path(m.group(1)).is_dir():
            return Path(m.group(1))
    return root


def canonical_root(p: Path) -> Path | None:
    """The repo a folder belongs to, the same for the main checkout and any worktree of it. None: in no repo."""
    key = str(p)
    if key not in _CHECKOUTS:
        m = WORKTREE_DIR_RE.match(key)
        root = Path(m["repo"]) if m else git_root(p)
        _CHECKOUTS[key] = main_checkout(root) if root else None
    return _CHECKOUTS[key]


def checkout_of(folder: str) -> Path | None:
    if folder not in _DIR_ROOTS:
        _DIR_ROOTS[folder] = git_root(Path(folder))
    return _DIR_ROOTS[folder]


def repo_file_key(path: str) -> str:
    """A file's identity inside its repo: the same file in the main checkout and in a worktree is one key.
    A file in no repo, or in a worktree folder that is gone and not Claude Code's own, keeps its full path."""
    p = norm_path(path)
    m = WORKTREE_DIR_RE.match(p)
    if m:
        return FOLD(m["repo"]) + "\x1f" + FOLD(p[m.end() + 1:])
    co = checkout_of(str(Path(p).parent)) if p else None
    if co is None:
        return FOLD(p)
    return FOLD(str(main_checkout(co))) + "\x1f" + FOLD(p[len(str(co)) + 1:])


READ_VERBS = {"cat", "head", "tail", "less", "more", "bat", "nl", "wc", "tac", "strings", "xxd", "type", "get-content", "gc",
              "sed", "awk", "grep", "egrep", "fgrep", "rg", "ag", "select-string", "sls"}
SEARCH_VERBS = {"find", "fd", "ls", "tree", "dir", "get-childitem", "gci"}
PATTERN_FIRST = {"sed", "awk", "grep", "egrep", "fgrep", "rg", "ag", "select-string", "sls"}   # first plain argument is not a file
OPT_TAKES_ARG = {"head": {"-n", "-c"}, "tail": {"-n", "-c", "-s"}, "sed": {"-e", "-f"}, "awk": {"-f", "-F", "-v"},
                 "grep": {"-e", "-f", "-m", "-A", "-B", "-C", "-d", "--include", "--exclude"},
                 "rg": {"-e", "-f", "-m", "-A", "-B", "-C", "-g", "--glob", "-t", "--type", "-T", "--max-depth", "--max-count"}}
OPT_TAKES_ARG["egrep"] = OPT_TAKES_ARG["fgrep"] = OPT_TAKES_ARG["grep"]
OPT_TAKES_ARG["ag"] = OPT_TAKES_ARG["rg"]
GLOB_CHARS = re.compile(r"[*?\[\]${}`<>]")


def shell_tokens(cmd: str) -> list:
    """Words of a shell command with the operators (&& || ; | newline parentheses redirects) as their own tokens,
    quoted text kept whole. Falls back on a plain split when the quoting does not balance."""
    cmd = cmd.split("<<")[0]   # a heredoc body is text, not commands
    try:
        lx = shlex.shlex(cmd, posix=not re.search(r"[A-Za-z]:\\", cmd), punctuation_chars=";|&\n()<>")
        lx.whitespace, lx.whitespace_split, lx.commenters = " \t\r", True, ""
        return [t.strip("'\"") for t in lx]
    except ValueError:
        return re.sub(r"(&&|\|\||[;|()])", r" \1 ", cmd).split()


def bash_reads(cmd: str, cwd: str | None) -> tuple[list, bool, bool]:
    """(files a shell command reads, whether it searched, whether a `cd` moved it first).

    Follows `cd X && cat Y`: a relative path is resolved against where the command has got to, not against
    where the session started. A pipe's later stages read their input, not a file."""
    files, searched, moved = [], False, False
    cur, stack, group, stage, env = cwd, [], [], 0, {}
    seps = {"&&", "||", ";", "|", "\n", "&"}
    redirects = {">", ">>", "<", ">&", "<<<", "&>", "&>>", ">|"}

    def run():
        nonlocal cur, searched, moved
        toks, i = [], 0
        while i < len(group):   # drop redirects and what they point at, and the "2" of "2>&1"
            if group[i] in redirects:
                i += 2
                continue
            if group[i].isdigit() and i + 1 < len(group) and group[i + 1] in redirects:
                i += 1
                continue
            toks.append(group[i])
            i += 1
        while toks and re.match(r"^\w+=", toks[0]):
            k, _, v = toks.pop(0).partition("=")   # VAR=/some/dir; cat $VAR/file
            if not toks:
                env[k] = v
        while toks and toks[0] in ("sudo", "time", "command", "nohup", "exec", "builtin"):
            toks.pop(0)
        if not toks:
            return
        toks = [re.sub(r"\$(\w+)|\$\{(\w+)\}", lambda m: env.get(m.group(1) or m.group(2), m.group(0)), t) for t in toks]
        verb = posixpath.basename(toks[0]).lower().removesuffix(".exe")
        args = toks[1:]
        if verb in ("cd", "pushd", "set-location", "sl"):
            tgt = args[0] if args else "~"
            cur = None if tgt in ("-",) or GLOB_CHARS.search(tgt) else (norm_path(tgt, cur) or None)
            moved = moved or cur != cwd
            return
        if stage > 0 or (verb not in READ_VERBS and verb not in SEARCH_VERBS):
            return   # a later stage of a pipe reads its input, not a file
        if verb == "sed" and any(a.startswith("-i") or a == "--in-place" for a in args):
            return   # an edit, not a read
        takes, pos, skip, pattern_given = OPT_TAKES_ARG.get(verb, set()), [], False, False
        for k, a in enumerate(args):
            if skip:
                skip = False
            elif a == "--":
                pos += args[k + 1:]
                break
            elif a.startswith("-") and len(a) > 1:
                skip = a in takes
                pattern_given = pattern_given or a.split("=")[0] in ("-e", "-f", "--regexp", "--file") or (verb == "sed" and a == "-s")
            else:
                pos.append(a)
        if verb in SEARCH_VERBS:
            searched = True
            return
        if verb in PATTERN_FIRST and not pattern_given and pos:
            pos = pos[1:]   # the pattern or script
        recursive = verb in ("rg", "ag") or any(a in ("-r", "-R", "--recursive") or re.match(r"^-[a-zA-Z]*[rR]", a) for a in args)
        if not pos:
            searched = searched or (verb in PATTERN_FIRST and recursive)
            return
        for a in pos:
            if GLOB_CHARS.search(a) or (recursive and "." not in posixpath.basename(a.rstrip("/"))):
                searched = True   # a pattern, or a folder searched recursively: not one file
                continue
            f = norm_path(a, cur)
            if f:
                files.append(f)

    for t in shell_tokens(cmd):
        if t in seps or t in ("(", ")"):
            run()
            group = []
            if t == "(":
                stack.append(cur)
            elif t == ")" and stack:
                cur = stack.pop()
            stage = stage + 1 if t == "|" else 0
        else:
            group.append(t)
    run()
    return files, searched, moved


def note_prompt(s: Session, text: str, ts, kind: str, attached: bool = False) -> None:
    """One message a person sent, counted once however many files it was copied into."""
    if ts and not first_prompt_sighting(text, ts):
        RUN["prompts_copied_by_text"] += 1
        s.marks["copied_prompts"] += 1
        return
    s.prompts += 1
    s.pwords.append(len(text.split()))
    s.p_path += bool(PATH_RE.search(text))
    s.p_code += bool(CODE_RE.search(text))
    s.p_criteria += bool(CRITERIA_RE.search(text))
    s._asked = bool(ASK_CHECK_RE.search(text))
    if kind == "queued":
        s.marks["queued_prompt"] += 1
        s.marks["queued_attached"] += attached
    elif kind == "fallback":
        s.marks["fallback_prompt"] += 1
    elif kind == "app":
        s.marks["app_prompt"] += 1
    if text.lstrip().startswith("<"):
        s.marks["tagged_prompt"] += 1
    if ts:
        s.prompt_texts.append((ts, len(text.split()), text[:600]))
        s.hours[ts.astimezone().hour] += 1
    if s.prompts > 1 and ts and CORRECTION_RE.search(text[:300]):
        s.corrections.append(ts)
        s.correction_texts.append((ts, text[:300]))


def note_read(s: Session, ts, path, cwd, how: str, after_cd: bool = False) -> None:
    """A file the agent (or a helper, or an @-attachment) took in. Headless runs are not kept."""
    if (s.entry or "").startswith(PROGRAMMATIC_ENTRY):
        return
    f = norm_path(path, cwd)
    if f:
        s.reads.setdefault((f, how), ts)
        RUN["reads_" + how] += 1
        RUN["reads_" + how + "_after_cd"] += after_cd


def lists_home_file(att: dict) -> bool:
    """Does an `instructions` attachment list a file that sits directly in the home folder (~/CLAUDE.md)?
    Which Claude Code versions do is what limits.detect_switches looks for."""
    return any(posixpath.dirname(norm_path(f.get("path") if isinstance(f, dict) else f)) == str(HOME)
               for f in att.get("files") or [])


def read_subagent(path: Path, parent: "Session | None") -> None:
    """File reads a helper made, credited to the session that started it. Nothing else in a helper's
    transcript is counted: helpers started by helpers and the helper's own messages stay out."""
    if parent is None or (parent.entry or "").startswith(PROGRAMMATIC_ENTRY):
        return
    RUN["subagent_files_read"] += 1
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"tool_use"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            ts, cwd = parse_ts(d.get("timestamp")), d.get("cwd") or parent.cwd
            for b in (d.get("message") or {}).get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                if b.get("name") == "Read":
                    files, searched, moved = [inp.get("file_path")], True, False
                elif b.get("name") == "Bash":
                    files, searched, moved = bash_reads(str(inp.get("command", "")), cwd)
                elif b.get("name") in ("Grep", "Glob"):
                    files, searched, moved = [], True, False
                else:
                    continue
                for f in files:
                    note_read(parent, ts, f, cwd, "subagent", after_cd=moved)
                if (files or searched) and parent.explored_first is not True and ts and (
                        parent.first_edit_ts is None or ts < parent.first_edit_ts):
                    parent.explored_first = True


def read_session(path: Path, is_sub: bool) -> Session:
    s = Session(path.stem, is_sub)
    RUN["files"] += 1
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith(SKIP_PREFIXES):
                RUN["records_skipped_api_blobs"] += 1
                continue
            try:
                d = json.loads(line)
            except ValueError:
                RUN["records_unreadable"] += 1
                continue
            RUN["records"] += 1
            if d.get("version"):
                RUN["cc:" + str(d["version"])] += 1
            t = d.get("type")
            ts = parse_ts(d.get("timestamp"))
            if not first_sighting(d):
                RUN["records_copied_from_parent"] += 1
                # a copy of the parent's history: it sets where this session sits, never what it did
                s.cwd = s.cwd or d.get("cwd")
                s.entry = s.entry or d.get("entrypoint")
                s.marks["copied_records"] += 1
                continue
            if ts:
                s.first = s.first or ts
                s.last = ts
            s.cwd = s.cwd or d.get("cwd")
            if d.get("cwd"):
                s.cwds[d["cwd"]] += 1
            s.entry = s.entry or d.get("entrypoint")
            ver = d.get("version")
            s.ver = s.ver or ver
            if d.get("permissionMode"):
                s.modes.add(d["permissionMode"])
            # session-shape markers Claude Code writes as their own records or fields
            if d.get("forkedFrom"):
                s.marks["forked"] += 1
            if t == "continued-in":
                s.marks["continued_in"] += 1
            if d.get("sessionKind") == "bg":
                s.marks["background_session"] = 1
            if t == "worktree-state":
                s.marks["worktree_state"] += 1
            if t == "goal_status" or d.get("subtype") == "goal_status":
                s.marks["goal_status"] += 1
            if t == "custom-title":
                s.marks["titled"] += 1
                if d.get("customTitle") and d["customTitle"] not in s.titles:
                    s.titles.append(str(d["customTitle"])[:80])
            if t == "agent-name":
                s.marks["named_agent"] += 1
            if d.get("imagePasteIds"):
                s.marks["image"] += len(d["imagePasteIds"]) if isinstance(d["imagePasteIds"], list) else 1
            if d.get("advisorModel"):
                s.marks["advisor"] += 1
            if d.get("isCompactSummary"):
                s.marks["compact_summary"] += 1
            if d.get("launchedAgentId"):
                s.marks["launched_agent"] += 1
            if t == "cost-state" and isinstance(d.get("totalCostUSD"), (int, float)):
                s.cost = float(d["totalCostUSD"])
            if t == "user" and ts:
                s.user_ts.append(ts)
                raw = line[:4000]
                if "Another Claude session sent a message" in raw or "<teammate-message" in raw:
                    s.marks["peer_msg"] += 1
                if "<task-notification>" in raw:
                    s.marks["task_report"] += 1
                if "<pasted_content" in raw:
                    s.marks["pasted"] += 1
                if d.get("scheduledTaskId"):
                    s.marks["scheduled_fire"] += 1
                # Slash commands: a user record whose message STARTS with the command tag is the person typing
                # it. Meta records count too (the prompt branch below skips them). A tag quoted inside a longer
                # message — a pasted transcript, a peer's message — is not an invocation.
                mc = (d.get("message") or {}).get("content")
                head = mc if isinstance(mc, str) else " ".join(
                    b.get("text", "") for b in (mc or []) if isinstance(b, dict) and b.get("type") == "text")
                if head.lstrip().startswith(("<command-name>", "<command-message>")):
                    m = CMD_RE.search(head)
                    if m:
                        s.commands[m.group(1)] += 1
            if t == "agent-setting":
                s.agent_setting = s.agent_setting or d.get("agentSetting")
                continue
            if t == "system":
                st = d.get("subtype")
                if st == "compact_boundary":
                    s.compactions += 1
                elif st == "stop_hook_summary":
                    s.hooks_fired += 1
                elif st == "scheduled_task_fire":
                    s.scheduled += 1
                elif st == "local_command":
                    # commands handled inside Claude Code (built-ins, mods like /ask) are system records;
                    # the one carrying <command-message> is the invocation, the other its echo
                    body = str(d.get("content") or "")
                    if "<command-message>" in body:
                        for c in CMD_RE.findall(body):
                            s.commands[c] += 1
                continue
            if t == "attachment":
                at = (d.get("attachment") or {}).get("type") or ""
                if at == "instructions":
                    s.sig_v["instructions"].add(ver)
                    if norm_path(d.get("cwd") or s.cwd).startswith(str(HOME) + "/"):
                        s.sig_v["home_eligible"].add(ver)
                        if lists_home_file(d["attachment"]):
                            s.sig_v["home_file"].add(ver)
                elif at == "queued_command":
                    # a message typed while the agent was busy, handed over mid-turn: it has no user record
                    q = d["attachment"]
                    org = q.get("origin")
                    if (isinstance(org, dict) and org.get("kind") == "human") or q.get("humanTurn") is True:
                        qt = q.get("prompt")
                        qt = qt if isinstance(qt, str) else json.dumps(qt, default=str)
                        qts = parse_ts(q.get("timestamp")) or ts
                        if qt.strip():
                            if qts:
                                s.user_ts.append(qts)
                            note_prompt(s, qt, qts, "queued", attached=True)
                elif at == "file":
                    note_read(s, ts, (d.get("attachment") or {}).get("filename"), "", "attached")
                if at.startswith("hook_"):
                    s.hooks_fired += 1
                    att = d.get("attachment") or {}
                    body = str(att.get("content") or att.get("stdout") or "")
                    tag = re.search(r"\[([\w:@.-]{2,40})\]", body[:400])
                    if at != "hook_success" or tag:   # a silent success carries nothing to attribute
                        s.hook_sources[f"{tag.group(1) if tag else '?'} · {att.get('hookEvent') or att.get('hookName') or '?'}"] += 1
                    if at in ("hook_blocking_error", "hook_cancelled") or "deny" in at or "block" in at:
                        s.hook_blocks += 1
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if t == "user" and s._pending and isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in s._pending:
                        s._pending.discard(b["tool_use_id"])
                        body = str(b.get("content") or "")[-4000:]
                        fail = bool(b.get("is_error")) or bool(CHECK_FAIL_RE.search(body))
                        s.step_log.append(("R", ts, {"fail": fail}))
            if t == "user" and not d.get("isMeta") and not d.get("toolUseResult"):
                text = content if isinstance(content, str) else " ".join(
                    b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
                if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in (content if isinstance(content, list) else [])):
                    continue
                if text.strip():
                    s.sig_v["eligible"].add(ver)
                    if d.get("promptSource") is not None:
                        s.has_src = True
                        s.sig_v["promptSource"].add(ver)
                    if d.get("entrypoint"):
                        s.sig_v["entrypoint"].add(ver)
                kind = prompt_kind(d, s, text)
                if CMD_RE.search(text):
                    pass   # counted above, from the raw record
                elif "[Request interrupted by user" in text:
                    s.interrupts += 1
                elif kind in HUMAN_SOURCES or kind in ("app", "fallback"):
                    note_prompt(s, text, ts, kind)
                elif kind == "program":
                    s.marks["program_prompt"] += 1
            elif t == "assistant":
                if msg.get("model"):
                    s.models[msg["model"]] += 1
                for b in content if isinstance(content, list) else []:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    name, inp = b.get("name", ""), b.get("input") or {}
                    if name.startswith("mcp__"):
                        server = name.split("__")[1]
                        s.mcp[server] += 1
                        s.tools["mcp"] += 1
                        continue
                    s.tools[name] += 1
                    if inp.get("run_in_background"):
                        s.bg_tools[name] += 1
                    if name in ("Read", "Grep", "Glob", "LSP") and s.explored_first is None:
                        s.explored_first = True
                    if name == "Read":
                        note_read(s, ts, inp.get("file_path"), d.get("cwd") or s.cwd, "subagent" if d.get("isSidechain") else "read")
                    if name in ("Agent", "Task"):
                        st_ = str(inp.get("subagent_type") or "general-purpose")
                        s.subagents[st_] += 1
                        s.agent_descs.append((st_, str(inp.get("description") or "")[:80], bool(inp.get("run_in_background")),
                                              str(inp.get("model") or "")))
                        if inp.get("model"):
                            s.agent_model_param += 1
                        if st_.lower() in ("explore", "plan") and s.explored_first is None:
                            s.explored_first = True
                        if REVIEW_AGENT_RE.search(st_) or REVIEW_AGENT_RE.search(str(inp.get("description", ""))[:80]):
                            s.steps.append("V")
                            s.step_log.append(("V", ts, {"by": "helper", "name": st_}))
                    elif name == "Skill":
                        sk = str(inp.get("skill", ""))
                        s.skills[sk] += 1
                        if REVIEW_AGENT_RE.search(sk):
                            s.steps.append("V")
                            s.step_log.append(("V", ts, {"by": "skill", "name": sk}))
                    elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        fp = str(inp.get("file_path") or inp.get("notebook_path") or "")
                        kind = edit_kind(fp)
                        if kind == "code":
                            s.steps.append("E")
                            s.step_log.append(("E", ts, fp))
                            s.first_edit_ts = s.first_edit_ts or ts
                            if s.explored_first is None:
                                s.explored_first = False
                        elif kind not in ("docs",) and ts:
                            s.inst_edits.append((ts, kind))
                            s.inst_paths.append((ts, kind, fp))
                    elif name == "Bash":
                        cmd = str(inp.get("command", ""))
                        files, searched, moved = bash_reads(cmd, d.get("cwd") or s.cwd)
                        if (files or searched) and s.explored_first is None:
                            s.explored_first = True   # a cat, sed, grep or ls is the agent looking around too
                        for f in files:
                            note_read(s, ts, f, "", "subagent" if d.get("isSidechain") else "bash", after_cd=moved)
                        for fam, rx in BASH_PATTERNS.items():
                            if rx.search(cmd):
                                s.bash[fam] += 1
                        for fam, rx in INTEGRATIONS.items():
                            if rx.search(cmd):
                                s.integrations[fam] += 1
                        lk = LOOKUP_RE.search(cmd)
                        if lk:
                            s.lookup_cmds[lk.group(0).strip()] += 1
                        if BOUNDED_HEADLESS_RE.search(cmd):
                            s.bash["bounded_headless"] += 1
                        if EVALS_RE.search(cmd):
                            s.bash["evals"] += 1
                        # order matters for verification: tests and reviews after the last edit, before a commit
                        tm = BASH_PATTERNS["tests"].search(cmd) or BASH_PATTERNS["lint_or_types"].search(cmd)
                        if tm:
                            s.steps.append("T")
                            s.test_runners[tm.group(1).split()[0]] += 1
                            s.step_log.append(("T", ts, {"runner": tm.group(1).split()[0], "asked": s._asked}))
                            if b.get("id"):
                                s._pending.add(b["id"])
                        if GIT_DIFF_RE.search(cmd):
                            s.steps.append("D")
                            s.step_log.append(("D", ts, None))
                        if BASH_PATTERNS["git_commit"].search(cmd):
                            s.steps.append("C")
                            s.step_log.append(("C", ts, None))
    settle_cwd(s)
    return s


def settle_cwd(s) -> None:
    """A session that started outside any repo but worked inside one belongs to that repo: its most-used
    folder that resolves to a repo becomes its folder. Seen 2026-10-22 on a tester who opens every chat in a
    parent folder and works in repos and worktrees under it: 133 chats tied to no repo, 0 commits matched,
    and her git identity never learned. A temp-folder start stays as it is (it marks a throwaway)."""
    if not s.cwd or TMP_CWD.search(s.cwd) or canonical_root(Path(s.cwd)) is not None:
        return
    for folder, _ in s.cwds.most_common():
        if folder != s.cwd and not TMP_CWD.search(folder) and canonical_root(Path(folder)) is not None:
            s.marks["moved_into_repo"] += 1
            s.cwd = folder
            return


TMP_CWD = re.compile(r"^/(private/)?(tmp|var/folders)/|/T/")


def is_typed(s) -> bool:
    """THE definition of 'a session a person typed in', used by every view so their numbers agree:
    at least one message a person sent (Claude Code's promptSource, whichever app they typed in: terminal,
    IDE extension, desktop), and not a throwaway in a temp folder."""
    return s.prompts > 0 and not TMP_CWD.search(s.cwd or "")


def is_automated(s) -> bool:
    """A program drove it (an sdk entrypoint, or messages Claude Code marks as a program's) and no person
    typed in it. A session neither typed nor automated had no message at all, or was a typed throwaway."""
    return not is_typed(s) and ((s.entry or "").startswith(PROGRAMMATIC_ENTRY) or s.marks.get("program_prompt", 0) > 0)


# Entrypoint values Claude Code writes. Only cli, claude-desktop and the sdk-* ones have been seen in logs so
# far; the others are the names its docs give and are unverified. Any value not listed here is still counted
# as long as a person sent messages through it, and is listed in the coverage facts so we learn it.
KNOWN_ENTRYPOINTS = {"cli": "terminal", "claude-desktop": "desktop app", "claude-vscode": "VS Code extension",
                     "claude-jetbrains": "JetBrains plugin", "sdk-cli": "claude -p", "sdk-py": "Agent SDK (Python)",
                     "sdk-ts": "Agent SDK (TypeScript)"}


def own_code_runs(by_starter: dict) -> int:
    """THE count of headless runs their own code started: every starter except temp-folder trials."""
    return sum(v for k, v in by_starter.items() if k != "trial runs in a temp folder")


def max_parallel(sessions) -> int:
    """Most interactive sessions open at the same moment (a session = first..last record)."""
    ev = []
    for s in sessions:
        if s.first and s.last and s.last > s.first:
            ev += [(s.first, 1), (s.last, -1)]
    best = cur = 0
    for _, d in sorted(ev):
        cur += d
        best = max(best, cur)
    return best


def classify(name: str, own: set[str], kind: str, installed: set[str] = frozenset()) -> str:
    """plugin · built-in · own (written in ~/.claude) · installed (a symlinked skill, e.g. from
    skills.sh) · project-or-removed (a repo's .claude/, or gone from disk since)."""
    if ":" in name:
        return "plugin"
    low = name.lower()
    if kind == "agent" and low in BUILTIN_AGENTS:
        return "built-in"
    if kind == "command" and low in BUILTIN_COMMANDS:
        return "built-in"
    if name in installed:
        return "installed"
    if name in own:
        return "own"
    return "project-or-removed"


def habits(days: int, own_names: set[str], installed: set[str], logs_root: Path | None = None,
           cleanup_days: int | None = None) -> tuple[dict, list[Session]]:
    root = logs_root or CLAUDE / "projects"
    now = time.time()
    cutoff = now - days * 86400
    mains, subs = [], 0
    t0 = now
    files, logs_total, oldest, newest = [], 0, None, None
    for p in root.glob("*/*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        logs_total += 1
        oldest, newest = min(oldest or st.st_mtime, st.st_mtime), max(newest or 0, st.st_mtime)
        if st.st_mtime >= cutoff:
            files.append((getattr(st, "st_birthtime", st.st_mtime), p))
    # Oldest first: a parent is read before its forks, so copied records stay credited to the parent.
    for _, p in sorted(files):
        mains.append(read_session(p, False))
    by_sid = {s.sid: s for s in mains}
    for p in root.glob("*/*/subagents/*.jsonl"):
        if p.stat().st_mtime >= cutoff:
            subs += 1
            read_subagent(p, by_sid.get(p.parent.parent.name))

    since = datetime.now(timezone.utc) - timedelta(days=days)
    sessions = [s for s in mains if s.last and s.last >= since]
    # A person typed: at least one message Claude Code marks as typed (or queued), through any app.
    # Messages marked sdk or system are a program's: `claude -p` and Agent SDK runs.
    human = [s for s in sessions if is_typed(s)]
    interactive = human
    automated = [s for s in sessions if is_automated(s)]

    def tot(attr, pool):
        c = Counter()
        for s in pool:
            c.update(getattr(s, attr))
        return c

    def share(pool, pred):
        return f"{sum(1 for s in pool if pred(s))} of {len(pool)}"

    tools, subagents, skills, commands = tot("tools", human), tot("subagents", human), tot("skills", human), tot("commands", human)
    bash = tot("bash", human)
    by_kind = defaultdict(Counter)
    for n, c in subagents.items():
        by_kind[classify(n, own_names, "agent", installed)][n] += c
    sk_kind = defaultdict(Counter)
    for n, c in skills.items():
        sk_kind[classify(n, own_names, "skill", installed)][n] += c
    cmd_kind = defaultdict(Counter)
    for n, c in commands.items():
        cmd_kind[classify(n, own_names, "command", installed)][n] += c

    active_days = len({s.first.date() for s in human if s.first})
    out = {
        "window_days": days,
        "scan_seconds": round(time.time() - t0, 1),
        "sessions": {
            "with_a_person_typing": len(human),
            "automated_or_headless": len(automated),
            "subagent_transcripts": subs,
            "active_days": active_days,
            "prompts_typed": sum(s.prompts for s in human),
            "median_prompts_per_session": sorted(s.prompts for s in human)[len(human) // 2] if human else 0,
            "max_sessions_open_at_once": max_parallel(interactive),
            "entrypoints": dict(Counter(s.entry or "unknown" for s in sessions)),
            "typed_in_entrypoints": dict(Counter(s.entry or "none recorded" for s in human)),
        },
        "working_style": {
            "sessions_using_plan_mode": share(human, lambda s: "plan" in s.modes or s.tools.get("ExitPlanMode")),
            "sessions_starting_subagents": share(human, lambda s: sum(s.subagents.values()) > 0),
            "sessions_using_skills": share(human, lambda s: sum(s.skills.values()) > 0),
            "sessions_in_worktrees": share(human, lambda s: s.tools.get("EnterWorktree") or s.bash.get("git_worktree")),
            "sessions_running_tests": share(human, lambda s: s.bash.get("tests")),
            "sessions_committing": share(human, lambda s: s.bash.get("git_commit")),
            "sessions_where_hooks_fired": share(human, lambda s: s.hooks_fired > 0),
            "sessions_compacted": share(human, lambda s: s.compactions > 0),
            "sessions_with_todo_lists": share(human, lambda s: s.tools.get("TodoWrite") or s.tools.get("TaskCreate")),
            "permission_modes_seen": dict(Counter(m for s in human for m in s.modes)),
            "interrupts": sum(s.interrupts for s in human),
        },
        "tool_calls": dict(tools.most_common(20)),
        "bash_families": dict(bash),
        "subagents": {k: dict(v.most_common(10)) for k, v in by_kind.items()},
        "skills_called": {k: dict(v.most_common(15)) for k, v in sk_kind.items()},
        "slash_commands": {k: dict(v.most_common(15)) for k, v in cmd_kind.items()},
        "mcp_servers_used": dict(tot("mcp", human).most_common(15)),
        "models": dict(tot("models", human).most_common(6)),
    }
    out["evidence"] = evidence(sessions, human, automated, days)
    out["coverage"] = coverage_facts(root, days, now, (logs_total, len(files), oldest, newest, cleanup_days), sessions, human, automated, subs)
    global ALL_SESSIONS
    ALL_SESSIONS = sessions   # story.py reads the session objects; JSON output never carries them
    return out, human


def home_file_switch_checked(switches: list, human: list) -> list:
    """A home-folder file that shows up in the logs from some version on is a version switch only if the file was
    already there before. If it was created after the last session on the old side, creating it explains the flip."""
    kept = []
    for sw in switches:
        if sw["signal"] == "home_file":
            born = [getattr(f.stat(), "st_birthtime", None) for f in (HOME / "CLAUDE.md", HOME / "AGENTS.md") if f.exists()]
            old = [s.first.timestamp() for s in human if s.first and s.ver and limits.vkey(s.ver) < limits.vkey(sw["at"])]
            if any(b and old and b > max(old) for b in born):
                continue
            if not any(born):
                sw = {**sw, "note": "This system does not record when a file was created, so a newly added file is not ruled out."}
        kept.append(sw)
    return kept


def coverage_facts(root: Path, days: int, now: float, logs: tuple, sessions: list, human: list, automated: list, subs: int) -> dict:
    """What the scan could and could not see, as counts and names (limits.coverage turns it into sentences,
    limits.gate into 'can't tell'). Nothing here quotes a message, a path from the person's work or a repo."""
    total, in_window, oldest, newest, cleanup = logs
    state = "none" if not total else ("ok" if sessions else "stale")
    versions = Counter(s.ver for s in sessions if s.ver)
    order = sorted(versions, key=limits.vkey)
    for s in human:
        s.inst_files = {repo_file_key(p) for _, _, p in s.inst_paths}
    typed_entries = Counter(s.entry or "none recorded" for s in human)
    marks = lambda pool, k: sum(s.marks.get(k, 0) for s in pool)
    return {
        "logs": {"dir": str(root).replace(str(HOME), "~"), "state": state, "window_days": days, "files_total": total,
                 "files_in_window": in_window, "cleanup_days": cleanup,
                 "oldest_days": round((now - oldest) / 86400) if oldest else None,
                 "newest_days": round((now - newest) / 86400) if newest else None,
                 # how far back the chats themselves reach: a long-lived chat keeps its file fresh, so the
                 # oldest FILE can be younger than the oldest message in the window (a tester: files 23 days, chats 30+)
                 "oldest_record_days": round((now - min(s.first.timestamp() for s in sessions if s.first)) / 86400)
                 if any(s.first for s in sessions) else None},
        "sessions": {"in_window": len(sessions), "typed": len(human), "automated": len(automated),
                     "moved_into_repo": sum(1 for s in human if s.marks.get("moved_into_repo")),
                     "temp": sum(1 for s in sessions if s.prompts and TMP_CWD.search(s.cwd or "") and not is_automated(s)),
                     "no_prompt": sum(1 for s in sessions if not s.prompts and not is_automated(s)),
                     "subagent_files": subs,
                     # chat shape (guide row 10): typed sessions that are a fork of another, or carried on in a new one
                     "forked": sum(1 for s in human if s.marks.get("forked")),
                     "continued": sum(1 for s in human if s.marks.get("continued_in"))},
        "prompts": {"counted": sum(s.prompts for s in human), "queued": marks(human, "queued_prompt"),
                    "queued_attached": marks(human, "queued_attached"), "fallback": marks(human, "fallback_prompt"),
                    "through_app": marks(human, "app_prompt"),
                    "tagged": marks(human, "tagged_prompt"), "copied": marks(sessions, "copied_prompts"),
                    "program": marks(human, "program_prompt")},
        "entrypoints": {"interactive": dict(typed_entries),
                        "programmatic": dict(Counter(s.entry or "none recorded" for s in automated)),
                        "unlisted": sorted(e for e in typed_entries if e not in KNOWN_ENTRYPOINTS and e != "none recorded")},
        "versions": {"first": order[0] if order else None, "last": order[-1] if order else None, "sessions": dict(versions),
                     # typed sessions only: headless runs sit in temp folders, where a home-folder file never applies
                     "switches": home_file_switch_checked(limits.detect_switches([dict(s.sig_v) for s in human]), human)},
        "reads": {k[6:]: v for k, v in RUN.items() if k.startswith("reads_")},
        "projects": {"as_checkouts": len({git_root(Path(c)) for c in {s.cwd for s in human if s.cwd}} - {None}),
                     "as_repos": len({canonical_root(Path(c)) for c in {s.cwd for s in human if s.cwd}} - {None})},
        "files": {"instruction_files_by_full_path": len({norm_path(p) for s in human for _, _, p in s.inst_paths}),
                  "instruction_files_by_repo_path": len({k for s in human for k in s.inst_files})},
    }


ALL_SESSIONS: list = []


def verified_commits(steps: list[str]) -> tuple[int, int]:
    """(commits, commits with a test, lint or review after the last code edit before it)."""
    commits = checked = 0
    since_edit_checked = True   # a commit with no edit before it in this session is not judged unchecked
    for k in steps:
        if k == "E":
            since_edit_checked = False
        elif k in ("T", "V"):
            since_edit_checked = True
        elif k == "C":
            commits += 1
            checked += since_edit_checked
    return commits, checked


def runner_kind(s: Session) -> str:
    """Who started a headless session. tmp-dir runs are trials; an Agent SDK entrypoint is code the
    person wrote; a --agent setting names the agent it ran as."""
    cwd = (s.cwd or "").replace("\\", "/")
    if re.search(r"/(tmp|T)/|^/tmp|/private/", cwd):
        return "trial runs in a temp folder"
    if re.search(r"evals?\b", cwd, re.I):
        return "eval runs"
    if (s.entry or "").startswith(("sdk-py", "sdk-ts")):
        return "own code calling the Agent SDK"
    if s.agent_setting:
        return "a named agent run headless"
    return "claude -p from a script or tool"


def bursts(runs: list[Session], gap_min: int = 15, size: int = 5) -> int:
    """Batches: at least SIZE headless runs in one project, each started within GAP_MIN of the last.
    The shape of walking a list of items rather than one-off calls."""
    by_cwd = defaultdict(list)
    for s in runs:
        if s.first:
            by_cwd[s.cwd].append(s.first)
    n = 0
    for starts in by_cwd.values():
        starts.sort()
        run = 1
        for a, b in zip(starts, starts[1:]):
            if (b - a).total_seconds() <= gap_min * 60:
                run += 1
            else:
                n += run >= size
                run = 1
        n += run >= size
    return n


def evidence(sessions: list[Session], human: list[Session], automated: list[Session], days: int) -> dict:
    """The practice-shaped facts: sequences across steps and sessions, not totals."""
    # Corrections that landed: a correction, then LATER IN THE SAME SESSION an edit to instructions, a
    # skill, an agent, a hook or settings. Any-session-within-48h was tried first and read 93 of 93 on a
    # heavy tooling author: their base rate of tooling edits swamps the link. Same-session is the shape
    # of "that was wrong, write it down so it doesn't happen again" (v13 c7-corrections-land, loop-17).
    inst = sorted((ts, k) for s in sessions for ts, k in s.inst_edits)
    corrections = [c for s in human for c in s.corrections]
    landed = sum(1 for s in human for c in s.corrections if any(ts > c for ts, _ in s.inst_edits))
    corrected_sessions = [s for s in human if s.corrections]
    inst_by_kind = Counter(k for _, k in inst)
    inst_sessions_human = sum(1 for s in human if s.inst_edits)

    commits = checked = 0
    edit_sessions = ended_checked = 0
    for s in human:
        c, k = verified_commits(s.steps)
        commits += c
        checked += k
        if "E" in s.steps:
            edit_sessions += 1
            last_e = len(s.steps) - 1 - s.steps[::-1].index("E")
            ended_checked += any(x in ("T", "V") for x in s.steps[last_e:])

    words = sorted(w for s in human for w in s.pwords)
    n_prompts = len(words)
    explored = [s.explored_first for s in human if s.explored_first is not None and "E" in s.steps]

    runners = Counter(runner_kind(s) for s in automated)
    durations = sorted(((s.last - s.first).total_seconds() / 60) for s in human if s.first and s.last)
    return {
        "corrections": len(corrections),
        "corrections_landed_same_session": landed,
        "sessions_with_corrections": len(corrected_sessions),
        # base rate, so a reader can tell "edits tooling after being corrected" from "edits tooling always"
        "sessions_editing_tooling_when_corrected": sum(1 for s in corrected_sessions if s.inst_edits),
        "instruction_edits_by_kind": dict(inst_by_kind),
        "sessions_editing_own_tooling": inst_sessions_human,
        "commits_in_sessions": commits,
        "commits_checked_after_last_edit": checked,
        "sessions_with_code_edits": edit_sessions,
        "sessions_ending_checked": ended_checked,
        "sessions_with_git_diff": sum(1 for s in human if "D" in s.steps),
        "sessions_with_review_step": sum(1 for s in human if "V" in s.steps),
        "explored_before_first_edit": f"{sum(explored)} of {len(explored)}",
        "prompt_words_median": words[n_prompts // 2] if words else 0,
        "prompt_words_p90": words[int(n_prompts * 0.9)] if words else 0,
        "prompts_naming_a_file": sum(s.p_path for s in human),
        "prompts_with_code_or_error": sum(s.p_code for s in human),
        "prompts_stating_done": sum(s.p_criteria for s in human),
        "prompts_total": n_prompts,
        "session_minutes_median": round(durations[len(durations) // 2]) if durations else 0,
        "sessions_with_clear_or_compact": sum(1 for s in human if s.commands.get("clear") or s.commands.get("compact")),
        "sessions_multi_model": sum(1 for s in human if len(s.models) > 1),
        "subagent_calls_with_model": sum(s.agent_model_param for s in human),
        "model_switches": sum(s.commands.get("model", 0) for s in human),
        "integrations": dict(sum((s.integrations for s in sessions), Counter()).most_common(12)),
        "headless_runs": len(automated),
        "headless_by_starter": dict(runners),
        "headless_batches": bursts([s for s in automated if runner_kind(s) != "trial runs in a temp folder"]),
        "headless_bounded_calls": sum(s.bash.get("bounded_headless", 0) for s in sessions),
        "scheduled_fires": sum(s.scheduled for s in sessions),
        "eval_commands": sum(s.bash.get("evals", 0) for s in sessions),
        "hook_blocks": sum(s.hook_blocks for s in sessions),
        "headless_editing_own_tooling": sum(1 for s in automated if s.inst_edits),
        "main_agent_settings": dict(Counter(s.agent_setting for s in human if s.agent_setting).most_common(5)),
        "sessions_typed_per_active_day": round(len(human) / max(1, len({s.first.date() for s in human if s.first})), 1),
    }


# ───────────────────────── share masking ─────────────────────────

def mask(report: dict, own_names: set[str], public_markets: set[str]) -> dict:
    """Swap what could name a client for stable labels. Names from public marketplaces stay."""
    labels: dict[str, str] = {}
    counters = Counter()

    def lab(kind, name):
        key = f"{kind}:{name}"
        if key not in labels:
            counters[kind] += 1
            labels[key] = f"own-{kind}-{counters[kind]}"
        return labels[key]

    def is_public(name):
        """name@market: public when the marketplace is on the allowlist (a GitHub marketplace can be
        a client's private repo, so "on GitHub" is not enough)."""
        _, _, market = name.partition("@")
        return market in public_markets

    # Plugin names whose own names may stay: an installed plugin from a public marketplace. A
    # `plugin:skill` or `plugin_<plugin>_<server>` from any other plugin — private, or removed
    # since and so unknown — is masked.
    public_plugins = {p["plugin"].partition("@")[0] for p in report["setup"]["plugins"]["installed"]
                      if is_public(p["plugin"])}

    def ns_public(name):
        return name.partition(":")[0] in public_plugins

    r = json.loads(json.dumps(report))
    own_styles = {row["name"] for row in r["setup"]["own"]["output_styles"]}
    for kind in ("skills", "agents", "commands", "output_styles"):
        for row in r["setup"]["own"][kind]:
            row["name"] = lab(kind.rstrip("s"), row["name"])
    for s in r["setup"]["settings"].values():
        if s and s.get("output_style") in own_styles:
            s["output_style"] = lab("output_style", s["output_style"])
    r["setup"]["mcp"]["user_servers"] = [{**s, "name": lab("mcp", s["name"])} for s in r["setup"]["mcp"]["user_servers"]]
    for row in r["setup"]["plugins"]["installed"]:
        if not is_public(row["plugin"]):
            row["plugin"] = lab("plugin", row["plugin"]); row["marketplace"] = "private"
    for s in r["setup"]["settings"].values():
        if s:
            s["plugins_enabled"] = [p if is_public(p) else lab("plugin", p) for p in s["plugins_enabled"]]
    r["setup"]["plugins"]["marketplaces"] = {
        (m if m in public_markets else lab("marketplace", m)): (v if m in public_markets else "private")
        for m, v in r["setup"]["plugins"]["marketplaces"].items()}
    h = r["habits"]
    for sect in ("subagents", "skills_called", "slash_commands"):
        for kind, names in h[sect].items():
            if kind in ("own", "project-or-removed"):
                h[sect][kind] = {lab(sect[:5], n): c for n, c in names.items()}
            elif kind == "plugin":
                h[sect][kind] = {(n if ns_public(n) else lab("plugin-item", n)): c for n, c in names.items()}

    def mcp_name(n):
        # plugin_<plugin>_<server>: the plugin name is the second segment, and may itself hold "_"
        if n.startswith("plugin_"):
            if any(n.startswith(f"plugin_{p}_") for p in public_plugins):
                return n
        return lab("mcp", n)
    h["mcp_servers_used"] = {mcp_name(n): c for n, c in h["mcp_servers_used"].items()}
    ev = h.get("evidence") or {}
    if "main_agent_settings" in ev:
        ev["main_agent_settings"] = {(a if ns_public(a) or a.lower() in BUILTIN_AGENTS else lab("agent", a)): c
                                     for a, c in ev["main_agent_settings"].items()}
    r["setup"].pop("identity", None)   # emails never leave in a shared report
    for i, j in enumerate((r["setup"].get("schedulers") or {}).get("agent_jobs", [])):
        j["label"] = f"job-{chr(65 + i)}"
    r["masked"] = True
    return r


PUBLIC_MARKETS = {"claude-plugins-official", "anthropic-agent-skills", "claude-code-plugins", "ak", "agentic-kit"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--share", action="store_true", help="mask names that could identify a client")
    ap.add_argument("--max-repos", type=int, default=12)
    ap.add_argument("--report", action="store_true",
                    help="emit report.json (REPORT-SCHEMA.md): the v13 practices with verdicts; implies masking")
    ap.add_argument("--story", action="store_true",
                    help="emit story.json (STORY-SCHEMA.md): how they work, as a task flows; DETAIL — real names and excerpts")
    ap.add_argument("--dev", action="store_true",
                    help="with --story: add how each finding was inferred, run facts, coverage and gaps (STORY-SCHEMA.md § Dev mode)")
    ap.add_argument("--out", help="write the JSON here instead of stdout")
    ap.add_argument("--identity", help="identity.json the person confirmed ({'emails': [...]}): who they are in git")
    ap.add_argument("--facts-out", help="also write the plain unmasked scan here (what place_level.py reads), "
                                        "so the story and the level page come from one scan")
    ap.add_argument("--logs-dir", help="read session logs from this folder instead of <Claude config>/projects "
                                       "(logs copied from another machine, or a test with none)")
    a = ap.parse_args()

    user_s = settings_summary(CLAUDE / "settings.json")
    local_s = settings_summary(CLAUDE / "settings.local.json")
    enabled = set((user_s or {}).get("plugins_enabled", [])) | set((local_s or {}).get("plugins_enabled", []))
    own = own_items()
    own_names = {i["name"] for k in own.values() for i in k}
    installed = {i["name"] for i in own["skills"] if i.get("is_symlink")}

    t_start = time.time()
    hab, human = habits(a.days, own_names, installed, Path(a.logs_dir).expanduser() if a.logs_dir else None,
                        (user_s or {}).get("cleanup_period_days"))

    # Repos: where the person actually worked in the window, most sessions first. A repo and every worktree
    # of it is one repo: counting each checkout apart split one project into many and matched its commits twice.
    per_root = Counter()
    for s in human:
        if s.cwd:
            r = canonical_root(Path(s.cwd))
            if r:
                per_root[r] += 1
    repos = []
    for i, (root, n) in enumerate(per_root.most_common(a.max_repos)):
        repos.append({"repo": f"repo-{chr(65 + i)}" if (a.share or a.report) else str(root).replace(str(HOME), "~"),
                      "sessions": n, **repo_profile(root)})
    # Worktrees of one repo share its history: count each commit once.
    commits, names = {}, Counter()
    for r in repos:
        commits.update(r.pop("_commits", {}))
        if r.get("_name"):
            names[r["_name"]] += r["sessions"]
        r.pop("_name", None)
    person = names.most_common(1)[0][0] if names else ""
    # who they are in git, before anything is credited to them: config (with its includes), each repo, Claude
    # commits, and the list the person confirmed (identity.py)
    sys.path.insert(0, str(HERE))
    import identity as _identity
    confirmed = (load_json(Path(a.identity)) or {}).get("emails") if a.identity else None
    skill_roots = [Path(os.path.realpath(CLAUDE / "skills" / sk["name"])) for sk in own["skills"] if sk.get("is_symlink")]
    ident = _identity.collect([root for root, _ in per_root.most_common(a.max_repos)]
                              + sorted({git_root(p) for p in skill_roots if git_root(p)}), confirmed)

    report = {
        "scanner": "setup-snapshot/0.1",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "kit_present": (HOME / ".ak").exists() or any("@ak" in p for p in enabled),
        "setup": {
            "settings": {"user": user_s, "user_local": local_s},
            "global_instructions": global_instructions(),
            "plugins": plugins_summary(enabled),
            "own": own,
            "mcp": mcp_summary(),
            "other_agent_tools": other_tools(),
            "machinery": machinery(),
            "schedulers": schedulers(),
            "identity": ident,
            "repos_worked_in": {"count": len(per_root), "top": repos},
            "my_commits_30d": {"total": len(commits), "agent_marked": sum(commits.values())},
        },
        "habits": hab,
    }
    report["scan_seconds_total"] = round(time.time() - t_start, 1)
    if a.facts_out:
        Path(a.facts_out).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    if a.story:
        # Detail version: real names and excerpts, built from the unmasked scan. Not for sharing.
        sys.path.insert(0, str(HERE))
        import practices
        import story
        v13 = practices.build(mask(report, own_names, PUBLIC_MARKETS), person=person)
        root_of = {}
        for s in ALL_SESSIONS:
            if s.cwd and s.cwd not in root_of:
                g = canonical_root(Path(s.cwd))
                root_of[s.cwd] = g.name if g else Path(s.cwd).name
        dev = None
        if a.dev:
            import devmode
            cov = hab["coverage"]
            dev = devmode.build(RUN, a.days, time.time() - t_start,
                                {"typed": cov["sessions"]["typed"], "headless": cov["sessions"]["automated"]}, cov)
        report = story.build(report, ALL_SESSIONS, root_of, person=person, v13=v13, dev=dev)
    elif a.share or a.report:
        report = mask(report, own_names, PUBLIC_MARKETS)
    if a.report and not a.story:
        # The page we collect: the v13 practices, pre-filled. Built from the masked scan, and it
        # carries counts only, so nothing in it names a repo or a thing the person made.
        sys.path.insert(0, str(HERE))
        import practices
        report = practices.build(report, person=person)
    text = json.dumps(report, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(a.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
