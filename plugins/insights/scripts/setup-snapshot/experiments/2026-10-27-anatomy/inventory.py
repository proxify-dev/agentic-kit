#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""inventory.py: list the ARTIFACTS of a developer's AI-agent (Claude Code) setup, so a reader opens the right files.

Read-only and deterministic (same machine state, same output, apart from timings). Python stdlib only.
Not a log scanner: nothing here reads session transcripts. Another script owns log metrics.

    uv run --script inventory.py --out inventory.json <project> [<project> ...]

The home config (~/.claude, ~/.claude.json, ~/CLAUDE.md, the plugin store) is always included.
`python inventory.py --check-file out.json` only runs the secret self-check on an existing output.

Hard rules kept by the code
  * Only writes the --out file, and only after the secret self-check passes.
  * Runs only read-only git (GIT_OPTIONAL_LOCKS=0, no fsmonitor), du, crontab -l. Never a project script or package manager.
  * Settings, MCP and plugin JSON are parsed. `env`, `headers` and any key named like token/key/secret/pass/auth/cookie/xox
    become {name, value_len}. Free text (commands, matched lines) goes through scrub(). Commit ids are cut to 12 chars.
  * Skips node_modules, .git internals, build/dist, caches, binaries, files over 2 MB. `.env*` files are never opened.

Output (JSON). Every list item carries `scope` (home | ancestor | project | folder | managed) and `project`
(the target it was found under, or null for home/ancestor items shared by all targets).

  meta            {schema, generated_at, platform, python, git_version, targets[], home, total_seconds}
  summary         counts per section, for a first look
  identity        {emails[], names[], sources}: who "the user" is for own-vs-installed (git config + includes + repo config)
  instruction_files[]  {path, kind: claude|claude_local|agents|gemini|cursor|copilot|windsurf|cline|claude_rules|qwen,
                        scope, project, bytes, lines, mtime, symlink_to?, in_fixture_path,
                        imports[{raw, resolved, exists}]   (@path lines),
                        pointers[{raw, resolved, kind: file|dir}]   (existing files/folders the text names: its routing table),
                        in_fixture_path (looks like a demo/fixture/playground/backup copy, not a live rule),
                        git{in_git, repo, commits, first_commit, last_commit, user_commits, authors, ignored?}}
  memory_stores[] {path, kind: agent_memory|named_memory|claude_auto_memory|obsidian_vault|memory_md|referenced_notes,
                        scope, project, in_fixture_path, referenced_from[], gitignored, tracked_files, files, bytes, md_files, md_read,
                        md_with_frontmatter, frontmatter_share, frontmatter_types{}, index_files[], top_subdirs[{name, files}],
                        wikilinks{total, unique_targets, resolved, unresolved_unique, unresolved_sample[]},
                        oldest_mtime, newest_mtime, truncated,
                        start_folder (claude_auto_memory only: the folder its sessions started in; null when the folder name in `path`
                        can't be reversed to a known folder)}
  hooks[]         {scope, project, source_kind: settings|plugin|project_plugin|statusline, source, plugin?, event, matcher,
                        type, command|prompt_chars|url_host, timeout?, script_path, script_exists, resolves_on_path}
  artifacts[]     {kind: skill|agent|command|workflow|output_style|hook_script|plugin_manifest|marketplace_manifest,
                        name, path, entry?, scope, project, symlink_to?, description?, model?, memory?, bytes, files?, has_scripts?,
                        in_fixture_path, also_at[] (same real file reached through a symlink), frontmatter_hooks?,
                        origin: own|installed|mixed|third_party (ownership.py: theirs unless installed), origin_evidence, git{commits, last_commit, user_commits},
                        hook_script only: wired, wired_by (a settings hook, or the frontmatter hooks of an agent/skill)}
                  own = git-authored by an identity email (>= half the commits), or a symlink resolving into such a repo;
                  installed = under plugins/cache or marketplaces, listed in a skills lock file, or synced from the account;
                  third_party = in a git repo, no commit by the user; untracked = not in git and not in a lock file.
  plugins{enabled[], dir_loaded[], marketplaces[]}   enabled[]: {key, enabled, scope(s), version, install_path, marketplace,
                        marketplace_source, marketplace_is_own, contents{skills[], agents[], commands[], hooks_events[], mcp_servers[]}}
  mcp_servers[]   {name, transport, source, scope, command?, entry?, args_count, url_host?, env[{name,value_len}], headers[...]}
  settings        {files[{path, scope, bytes, mtime, top_level_keys[]}], backups[{path, mtime}], notable[{file, scope, key, value}],
                   counts[{file, permissions_allow, ...}], env_keys[{file, name, value_len}]}
  worktrees[]     one item per git repo (common dir): {repo, default_refs[], summary{linked, merged, unmerged, locked, prunable,
                        detached, total_kb, own_node_modules, linked_node_modules, own_venv, dirty}, items[{path, exists, is_main,
                        branch, head, detached, locked, prunable, tip_date, merged, merged_into, ahead_of_default, dirty_files,
                        size_kb, deps[{rel, kind, link: dir|symlink, target?, target_in_main?}], created_approx, claude_managed}]}
                  merged = tip is an ancestor of the default branch (squash merges are NOT seen as merged).
  launchers       {crontab{available, entries[{schedule, command, script_path, script_launch_kinds[], script_mentions_claude}]},
                   launchd{matching[], other_count, other[{label, program}]}, systemd_user[], claude_scheduled_tasks[],
                   ci[{path, kinds[], mentions[{line, text}]}],
                   scripts[{path, project, via, kinds[], only_in_comments, counts{}, hits[{kind, line, text, flags[], in_comment}]}]}
                  kinds: claude_cli_print (claude -p / --print, incl. spawn("claude", ["-p"..])), claude_agent_sdk_import, sdk_query_call,
                  anthropic_api_import, other_agent_cli (codex exec, gemini -p, cursor-agent...), scheduler_install (crontab / launchctl
                  / schtasks in code). A hit inside a comment is counted as <kind>_in_comment and never makes a launcher by itself.
  summary.walk[target].nested_repos  git repos found inside a target: kept = the user has commits in the last 100; the rest are
                  cloned third-party repos, and everything inside them (skills, CLAUDE.md, scripts) is left out of every list above.
  could_not_read[] {path, reason}
  timings_s       {walk, identity, settings, instruction_files, artifacts, plugins, mcp, memory, worktrees, launchers, self_check}
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

SCHEMA = "inventory/1"
HOME = Path.home()
T0 = time.monotonic()
MAX_FILE = 2_000_000
TIMINGS: dict[str, float] = {}
COULD_NOT_READ: list[dict] = []
_CNR_SEEN: set[tuple[str, str]] = set()


@contextmanager
def section(name: str):
    t = time.monotonic()
    try:
        yield
    finally:
        TIMINGS[name] = round(TIMINGS.get(name, 0.0) + time.monotonic() - t, 2)


def cnr(path, reason: str):
    key = (str(path), reason)
    if key not in _CNR_SEEN and len(COULD_NOT_READ) < 300:
        _CNR_SEEN.add(key)
        COULD_NOT_READ.append({"path": str(path), "reason": reason})


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


# ---------------------------------------------------------------- secrets: scrub and self-check

SECRET_KEY_RE = re.compile(r"token|key|secret|pass|auth|cookie|xox", re.I)
_SPECIFIC = [
    ("slack token", re.compile(r"xox[abcdpers]-[A-Za-z0-9-]{4,}")),
    ("sk- api key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("github token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("google api key", re.compile(r"AIza[0-9A-Za-z_-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]*)?")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I)),
    ("aws key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("url credentials", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]{3,}@")),
    ("auth header", re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+|basic\s+|token\s+)?[^\s'\"]{6,}")),
]
_RUN = re.compile(r"[A-Za-z0-9+/_-]{32,}")
_KV_SECRET = re.compile(r"(?i)\b([A-Za-z0-9_.-]*(?:token|secret|pass|key|auth|cookie|xox)[A-Za-z0-9_.-]*)(\s*[=:]\s*)(?!<redacted)(?:\"[^\"]*\"|'[^']*'|[^\s\"';|&]+)")
_FLAG_SECRET = re.compile(r"(?i)(--[\w-]*(?:token|secret|pass|key|auth|cookie)[\w-]*)(=|\s+)(?!<redacted|-)(\"[^\"]*\"|'[^']*'|[^\s\"';|&]+)")
_PATHLIKE = re.compile(r"(?:^|[=:'\"(])(?:/|~|\.{1,2}/|[A-Za-z]:[\\/]|\$\{?[A-Z_]+\}?/)")
_PATHWORDS = re.compile(r"[\w./@+~ -]+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _is_pathlike(tok: str) -> bool:
    return bool(_PATHLIKE.search(tok)) or (tok.count("/") >= 2 and bool(_PATHWORDS.fullmatch(tok)))


def _run_is_secret(run: str) -> bool:
    """A random-looking run: letters and digits, and no readable kebab/snake name (branch names like
    worktree-agent-a169acd100a82cf7d break into short segments; a real token has one long unbroken stretch)."""
    if len(run) < 32 or not re.search(r"\d", run) or not re.search(r"[A-Za-z]", run) or _UUID.fullmatch(run):
        return False
    return max(len(seg) for seg in re.split(r"[-_]", run)) >= 24 or ("-" not in run and "_" not in run)


def find_secrets(s: str) -> list[tuple[str, int, int]]:
    """(label, start, end) of secret-looking spans. Shared by scrub() and the self-check."""
    out: list[tuple[str, int, int]] = []
    for label, rx in _SPECIFIC:
        for m in rx.finditer(s):
            if label == "sk- api key" and not re.search(r"\d", m.group(0)):
                continue
            out.append((label, m.start(), m.end()))
    for tm in re.finditer(r"\S+", s):
        tok = tm.group(0)
        if _is_pathlike(tok):
            continue
        for m in _RUN.finditer(tok):
            if _run_is_secret(m.group(0)):
                out.append(("32+ char base64/hex run", tm.start() + m.start(), tm.start() + m.end()))
    return out


def scrub(s, limit: int = 240) -> str:
    """Free text safe to store: secret spans and KEY=value pairs for secret-looking keys replaced, then truncated."""
    s = str(s)
    merged: list[list[int]] = []
    for _, a, b in sorted(find_secrets(s), key=lambda x: (x[1], x[2])):   # known token shapes first; overlapping spans become one
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for a, b in reversed(merged):
        s = s[:a] + "<redacted>" + s[b:]
    s = _FLAG_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", s)
    s = _KV_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", s)
    s = s.replace("\n", " ").replace("\r", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def secret_names(d: dict) -> list[dict]:
    """A dict of env vars / headers as [{name, value_len}]: never the value."""
    return [{"name": str(k), "value_len": len(str(v))} for k, v in d.items()]


def self_check(obj) -> list[str]:
    """JSON paths of fields that look like a secret. Empty list = clean."""
    bad: list[str] = []

    def visit(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                for label, _a, _b in find_secrets(str(k)):
                    bad.append(f"{path}.<key {str(k)[:40]!r}> ({label})")
                visit(v, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                visit(v, f"{path}[{i}]")
        elif isinstance(x, str):
            for label, _a, _b in find_secrets(x):
                bad.append(f"{path} ({label})")
                break

    visit(obj, "$")
    return bad


# ---------------------------------------------------------------- reading files

def read_text(p: Path, limit: int = MAX_FILE, note: bool = True) -> str | None:
    """File text, or None. `note=False` for bulk source scans, where skipped big/binary files are not worth listing."""
    try:
        st = p.stat()
        if st.st_size > limit:
            if note:
                cnr(p, f"skipped: {st.st_size} bytes > {limit}")
            return None
        data = p.read_bytes()
    except FileNotFoundError:
        return None
    except PermissionError:
        cnr(p, "permission denied")
        return None
    except OSError as e:
        cnr(p, f"os error: {e.strerror or e}")
        return None
    if b"\0" in data[:4096]:
        if note:
            cnr(p, "skipped: binary")
        return None
    return data.decode("utf-8", "replace")


def read_json(p: Path):
    text = read_text(p)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError as e:
        cnr(p, f"invalid JSON: {str(e)[:80]}")
        return None


_FM = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.S)
_FM_KEY = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$")


def frontmatter(text: str) -> dict | None:
    """Top-level scalar keys of a YAML frontmatter block (no YAML lib): {key: first-line-or-folded value}."""
    m = _FM.match(text.lstrip("\ufeff"))
    if not m:
        return None
    out: dict[str, str] = {}
    key = None
    for line in m.group(1).splitlines():
        km = _FM_KEY.match(line)
        if km:
            key, v = km.group(1), km.group(2).strip()
            out[key] = "" if v in (">", "|", ">-", "|-", ">+", "|+") else v.strip("\"'")
        elif key and line[:1] in (" ", "\t") and line.strip():
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def count_lines(text: str) -> int:
    return text.count("\n") + (0 if not text or text.endswith("\n") else 1)


# ---------------------------------------------------------------- git (read-only)

GIT = shutil.which("git")
GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "LC_ALL": "C"}
GIT_BUDGET = {"left": 40.0}  # seconds of git time for per-file history lookups, all targets together


def git(args: list[str], cwd: Path | str, timeout: float = 15) -> subprocess.CompletedProcess | None:
    if not GIT:
        return None
    try:
        return subprocess.run([GIT, "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.quotepath=off", *args],
                              cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=GIT_ENV, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        cnr(cwd, f"git {args[0]} timed out after {timeout:.0f}s")
    except OSError as e:
        cnr(cwd, f"git failed: {e}")
    return None


def git_out(args, cwd, timeout: float = 15) -> str | None:
    r = git(args, cwd, timeout)
    return r.stdout if r is not None and r.returncode == 0 else None


_root_cache: dict[Path, Path | None] = {}


def git_root(path: Path) -> Path | None:
    """Nearest ancestor holding a .git (dir or file). Pure filesystem, no subprocess."""
    cur = Path(os.path.realpath(path))
    if not cur.is_dir():
        cur = cur.parent
    chain, res = [], None
    while True:
        if cur in _root_cache:
            res = _root_cache[cur]
            break
        chain.append(cur)
        if (cur / ".git").exists():
            res = cur
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    for c in chain:
        _root_cache[c] = res
    return res


class Identity:
    emails: set[str] = set()
    names: set[str] = set()
    sources: dict[str, set[str]] = defaultdict(set)

    @classmethod
    def is_user(cls, email: str, name: str) -> bool:
        e = email.strip().lower()
        if e in cls.emails:
            return True
        m = re.match(r"(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$", e)
        if m and any(m.group(1) == x.split("@")[0] for x in cls.emails):
            return True
        return len(name.strip()) > 3 and name.strip().lower() in cls.names


def _gitconfig_identity(f: Path, how: str, depth: int = 0, seen: set | None = None):
    """user.email / user.name in a git config file and (conditional) includes. Parsed as text: no git needed."""
    seen = set() if seen is None else seen
    if depth > 4 or f in seen or not f.is_file():
        return
    seen.add(f)
    text = read_text(f)
    if text is None:
        return
    section_name = ""
    for line in text.splitlines():
        line = line.strip()
        sm = re.match(r"\[\s*([A-Za-z0-9.-]+)(?:\s+\"([^\"]*)\")?\s*\]", line)
        if sm:
            section_name = sm.group(1).lower()
            continue
        kv = re.match(r"([A-Za-z]+)\s*=\s*(.*?)\s*(?:[#;].*)?$", line)
        if not kv:
            continue
        k, v = kv.group(1).lower(), kv.group(2).strip().strip('"')
        if section_name == "user" and k == "email" and "@" in v:
            Identity.emails.add(v.lower())
            Identity.sources[v.lower()].add(how)
        elif section_name == "user" and k == "name" and v:
            Identity.names.add(v.lower())
        elif section_name in ("include", "includeif") and k == "path":
            p = Path(os.path.expanduser(v))
            _gitconfig_identity(p if p.is_absolute() else f.parent / p, f"{how} > include", depth + 1, seen)


def identity_from_global_config():
    _gitconfig_identity(HOME / ".gitconfig", "global config")
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "git" / "config"
    _gitconfig_identity(xdg, "global config")


def identity_from_repos(repo_roots: list[Path]):
    for root in repo_roots[:25]:
        e = git_out(["config", "user.email"], root, 5)
        if e and "@" in e:
            Identity.emails.add(e.strip().lower())
            Identity.sources[e.strip().lower()].add("a repo's own config")
        n = git_out(["config", "user.name"], root, 5)
        if n and n.strip():
            Identity.names.add(n.strip().lower())


_hist_cache: dict[tuple[Path, str], dict | None] = {}


def history(root: Path, rel: str) -> dict | None:
    key = (root, rel)
    if key in _hist_cache:
        return _hist_cache[key]
    res = None
    if GIT_BUDGET["left"] > 0:
        t = time.monotonic()
        r = git(["log", "--format=%aI%x09%ae%x09%an", "--", rel], root, 15)
        GIT_BUDGET["left"] -= time.monotonic() - t
        if r is not None and r.returncode == 0:
            rows = [ln.split("\t") + ["", ""] for ln in r.stdout.splitlines() if ln]
            res = {
                "commits": len(rows),
                "first_commit": rows[-1][0][:10] if rows else None,
                "last_commit": rows[0][0][:10] if rows else None,
                "user_commits": sum(Identity.is_user(x[1], x[2]) for x in rows),
                "authors": len({x[1].lower() for x in rows}),
            }
    else:
        cnr(root, "git history budget used up; later files have no git block")
    _hist_cache[key] = res
    return res


def git_info(path: Path) -> dict:
    real = Path(os.path.realpath(path))
    root = git_root(real)
    if root is None:
        return {"in_git": False}
    rel = os.path.relpath(real, root)
    info: dict = {"in_git": True, "repo": str(root)}
    h = history(root, rel)
    if h is None:
        info["error"] = "git log unavailable"
        return info
    info.update(h)
    if h["commits"] == 0:
        r = git(["check-ignore", "-q", "--", rel], root, 5)
        info["ignored"] = bool(r is not None and r.returncode == 0)
    return info


# ---------------------------------------------------------------- the walk

PRUNE = {"node_modules", ".git", "build", "dist", "out", ".next", ".nuxt", ".svelte-kit", ".turbo", "target", "__pycache__",
         ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", "caches", "coverage", ".gradle",
         "Pods", ".terraform", ".parcel-cache", "bower_components", ".yarn", ".pnpm-store", ".npm", "site-packages",
         "DerivedData", ".output", ".nx", ".angular", ".thumbnails"}
MEM_NAME = re.compile(r"(?i)^(?:\.?agents?[-_]memor(?:y|ies).*|.*(?:^|[-_.])memor(?:y|ies)(?:$|[-_.].*))$|^\.?memor(?:y|ies)$")
INSTR_NAMES = {"claude.md": "claude", "claude.local.md": "claude_local", "agents.md": "agents", "agent.md": "agents",
               "gemini.md": "gemini", ".cursorrules": "cursor", ".windsurfrules": "windsurf", ".clinerules": "cline",
               "copilot-instructions.md": "copilot", "qwen.md": "qwen"}
SCRIPT_EXT = {".sh", ".bash", ".zsh", ".fish", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".rb", ".go", ".rs",
              ".plist", ".service", ".timer", ".ps1", ".bat", ".cmd", ".php", ".pl", ".lua", ".nu"}   # no yaml/toml: those are data
SCRIPT_NAMES = {"justfile", "makefile", "dockerfile", "package.json", "procfile", "taskfile.yml", "rakefile", "docker-compose.yml",
                "docker-compose.yaml", "compose.yml", "compose.yaml", "pyproject.toml"}
CI_NAMES = {".gitlab-ci.yml", ".travis.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", "jenkinsfile", "cloudbuild.yaml", "cloudbuild.yml"}
FIXTURE_RE = re.compile(r"(?i)(?:^|/)(?:fixtures?|__fixtures__|assignments|demos?|examples?|_archive|archive|\.solution|testdata|"
                        r"test-data|templates?|snapshots?|samples?|vendor|third[_-]party|playground|sandbox|\.?scratch|_experiments|\.testbed|\.?backups?|"
                        r"\.tmp-probe|[\w.-]*\.(?:bak|absorbed)[\w.-]*)(?:/|$)")
MAX_DEPTH, MAX_ENTRIES, WALK_BUDGET_S = 8, 400_000, 22.0


def instruction_kind(name: str, parent: str, grand: str) -> str | None:
    ln = name.lower()
    if ln in INSTR_NAMES:
        return INSTR_NAMES[ln]
    if grand == ".cursor" and parent == "rules" and ln.endswith((".mdc", ".md")):
        return "cursor"
    if grand == ".claude" and parent == "rules" and ln.endswith(".md"):
        return "claude_rules"
    if parent == ".clinerules" and ln.endswith(".md"):
        return "cline"
    if grand == ".github" and parent == "instructions" and ln.endswith(".md"):
        return "copilot"
    return None


TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e", "integration", "testing"}
_TEST_CONFIG = re.compile(r"(?i)^(?:(?:vitest|jest|playwright|cypress|karma|wdio|ava)\.(?:config|workspace|setup)[\w.-]*|"
                          r"jest\.setup[\w.-]*|pytest\.ini|tox\.ini|conftest\.py|noxfile\.py|\.mocharc[\w.-]*|global[-_]?(?:setup|teardown)[\w.-]*)$")
_TEST_HELPER = re.compile(r"(?i)^(?:setup|teardown|run|runner|harness|bootstrap|world|globalsetup|global[-_]setup|helpers?)(?:\.[\w-]+)+$")
_TASK_FILES = {"justfile": "justfile", ".justfile": "justfile", "makefile": "makefile", "gnumakefile": "makefile",
               "taskfile.yml": "taskfile", "taskfile.yaml": "taskfile", "package.json": "package_json", "procfile": "procfile"}
_HOOK_CONFIGS = {".pre-commit-config.yaml", ".pre-commit-config.yml", "lefthook.yml", "lefthook.yaml", ".lefthook.yml"}


def scaffold_kind(name: str, dp: Path) -> str | None:
    """What a file is in the way a project RUNS work: test harness, task runner, git attributes/hooks, container, or None."""
    ln = name.lower()
    if _TEST_CONFIG.match(name):
        return "test_config"
    if dp.name.lower() in TEST_DIRS:
        if ln.startswith("readme"):
            return "test_readme"
        if _TEST_HELPER.match(name):
            return "test_setup" if re.match(r"(?i)(?:setup|teardown|bootstrap|world|global)", name) else "test_runner"
    if ln in _TASK_FILES:
        return "task_runner:" + _TASK_FILES[ln]
    if ln == ".gitattributes":
        return "git_attributes"
    if ln in _HOOK_CONFIGS or (dp.name in (".githooks", ".husky") and not name.startswith("_") and ln != "readme.md"):
        return "git_hook"
    if re.match(r"^(?:docker-)?compose(?:\.[\w-]+)?\.ya?ml$|^dockerfile(?:\.[\w-]+)?$", ln) or \
            (ln == "devcontainer.json" and dp.name == ".devcontainer") or ln == ".devcontainer.json":
        return "container"
    return None


class Walk:
    def __init__(self, root: Path):
        self.root = root
        self.dirs = self.entries = 0
        self.truncated = False
        self.seconds = 0.0
        self.instructions: list[tuple[Path, str]] = []
        self.suppressed_in_skill = 0
        self.skills: list[Path] = []
        self.claude_dirs: list[Path] = []
        self.memory_dirs: list[Path] = []
        self.memory_md: list[Path] = []
        self.vaults: list[Path] = []
        self.mcp_files: list[Path] = []
        self.plugin_roots: list[Path] = []
        self.marketplace_files: list[Path] = []
        self.ci: list[Path] = []
        self.scripts: list[Path] = []
        self.locks: list[Path] = []
        self.git_repos: list[Path] = []
        self.linked_worktrees: list[Path] = []
        self.scaffold: list[tuple[Path, str]] = []   # (file, kind): how the project runs its work, see scaffold_kind()
        self.test_dirs: list[Path] = []
        self.nested_repos: list[dict] = []
        self.suppressed_by_nested_repos = 0


def _gitfile_is_worktree(p: Path) -> bool:
    try:
        head = (p / ".git").read_text(errors="replace")[:400]
    except OSError:
        return False
    return "gitdir:" in head and "/worktrees/" in head.replace("\\", "/")


def walk_target(root: Path) -> Walk:
    w = Walk(root)
    t0 = time.monotonic()
    deadline = t0 + WALK_BUDGET_S
    stack: list[tuple[str, int, bool]] = [(str(root), 0, False)]
    if (root / ".git").is_dir():
        w.git_repos.append(root)
    while stack:
        if time.monotonic() > deadline or w.entries > MAX_ENTRIES:
            w.truncated = True
            cnr(root, f"walk stopped early: {w.dirs} dirs, {w.entries} entries, {time.monotonic() - t0:.0f}s (budget {WALK_BUDGET_S:.0f}s)")
            break
        d, depth, in_skill = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError as e:
            cnr(d, f"cannot list: {e.strerror or e}")
            continue
        w.dirs += 1
        dp = Path(d)
        names = {e.name for e in entries}
        in_skill_here = in_skill or "SKILL.md" in names
        for e in entries:
            w.entries += 1
            name = e.name
            try:
                is_link = e.is_symlink()
                is_dir = e.is_dir(follow_symlinks=False)
                is_file = e.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_link:
                try:
                    is_file = Path(e.path).is_file()
                except OSError:
                    is_file = False
                if not is_file:
                    continue  # symlinked dirs are never followed (loops); skills dirs handle their own links
            p = Path(e.path)
            if is_dir:
                if name in PRUNE or name.endswith(".egg-info") or depth >= MAX_DEPTH:
                    continue
                if name == "worktrees" and dp.name == ".claude":
                    continue
                if name == ".claude":
                    w.claude_dirs.append(p)
                    continue
                if name == ".obsidian":
                    w.vaults.append(dp)
                    continue
                if MEM_NAME.search(name):
                    w.memory_dirs.append(p)
                    continue
                if name.lower() in TEST_DIRS:
                    w.test_dirs.append(p)
                gitp = p / ".git"
                if gitp.is_file():
                    if _gitfile_is_worktree(p):
                        w.linked_worktrees.append(p)
                        continue
                    w.git_repos.append(p)  # submodule: keep walking
                elif gitp.is_dir():
                    w.git_repos.append(p)
                stack.append((e.path, depth + 1, in_skill_here))
                continue
            if not is_file:
                continue
            ln = name.lower()
            sk = scaffold_kind(name, dp)
            if sk and not in_skill_here:
                w.scaffold.append((p, sk))
            kind = instruction_kind(name, dp.name, dp.parent.name)
            if kind:
                if in_skill_here:
                    w.suppressed_in_skill += 1
                else:
                    w.instructions.append((p, kind))
                continue
            if name == "SKILL.md":
                w.skills.append(dp)
            elif ln == "memory.md":
                w.memory_md.append(p)
            elif name == ".mcp.json" or (ln == "mcp.json" and dp.name in (".cursor", ".vscode")):
                w.mcp_files.append(p)
            elif name == "plugin.json" and dp.name == ".claude-plugin":
                w.plugin_roots.append(dp.parent)
            elif name == "marketplace.json" and dp.name == ".claude-plugin":
                w.marketplace_files.append(p)
            elif name in ("skills-lock.json", ".skill-lock.json"):
                w.locks.append(p)
            elif (dp.name == "workflows" and dp.parent.name == ".github" and ln.endswith((".yml", ".yaml"))) \
                    or ln in CI_NAMES or (ln == "config.yml" and dp.name == ".circleci") \
                    or (dp.name == ".buildkite" and ln.endswith((".yml", ".yaml"))):
                w.ci.append(p)
            else:
                ext = os.path.splitext(ln)[1]
                if ext in SCRIPT_EXT or ln in SCRIPT_NAMES:
                    w.scripts.append(p)
                elif not ext and "." not in ln:
                    try:
                        if e.stat(follow_symlinks=False).st_mode & 0o111:
                            w.scripts.append(p)
                    except OSError:
                        pass
    w.seconds = round(time.monotonic() - t0, 2)
    return w


def prune_nested(t: Path, w: Walk):
    """Drop what lives inside a nested git repo where the user has no recent commit (a cloned third-party repo): its
    skills, CLAUDE.md files and scripts are not this person's setup. A person with no git identity keeps everything."""
    nested = sorted({r for r in w.git_repos if r != t})
    if not nested or not GIT:
        return
    if not (Identity.emails or Identity.names):
        cnr(t, "no git identity found: nested repos are not filtered, own-vs-installed cannot be decided")
        return

    def mine(r: Path) -> int:
        out = git_out(["log", "-n", "100", "--format=%ae%x09%an"], r, 10) or ""
        rows = [ln.split("\t") + [""] for ln in out.splitlines() if ln]
        return sum(Identity.is_user(x[0], x[1]) for x in rows)

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        counts = list(ex.map(mine, nested))
    skip = [r for r, n in zip(nested, counts) if n == 0]
    w.nested_repos = [{"path": str(r), "user_commits_in_last_100": n, "kept": n > 0} for r, n in zip(nested, counts)]
    prefixes = tuple(str(r) + os.sep for r in skip)

    def keep(p) -> bool:
        return not (str(p) + os.sep).startswith(prefixes)

    before = len(w.instructions) + len(w.skills) + len(w.scripts) + len(w.ci) + len(w.claude_dirs) + len(w.memory_dirs)
    w.instructions = [(p, k) for p, k in w.instructions if keep(p)]
    for name in ("skills", "claude_dirs", "memory_dirs", "memory_md", "vaults", "mcp_files", "plugin_roots", "marketplace_files",
                 "ci", "scripts", "locks", "git_repos", "linked_worktrees", "test_dirs"):
        setattr(w, name, [p for p in getattr(w, name) if keep(p)])
    w.scaffold = [(p, k) for p, k in w.scaffold if keep(p)]
    after = len(w.instructions) + len(w.skills) + len(w.scripts) + len(w.ci) + len(w.claude_dirs) + len(w.memory_dirs)
    w.suppressed_by_nested_repos = before - after


# ---------------------------------------------------------------- instruction files

_FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
_INLINE = re.compile(r"`[^`\n]*`")
_IMPORT = re.compile(r"(?<![\w@/.:-])@((?:~|\.{1,2})?/?[\w][\w.\-/~]*)")


def find_imports(text: str, src: Path) -> list[dict]:
    body = _INLINE.sub("", _FENCE.sub("", text))
    out, seen = [], set()
    for m in _IMPORT.finditer(body):
        raw = m.group(1).rstrip(".,;:)]}")
        if raw in seen or not ("/" in raw or re.search(r"\.[A-Za-z]{1,6}$", raw)):
            continue
        seen.add(raw)
        p = Path(os.path.expanduser(raw))
        p = p if p.is_absolute() else src.parent / p
        try:
            exists = p.exists()
        except OSError:
            exists = False
        out.append({"raw": raw, "resolved": os.path.normpath(str(p)), "exists": exists})
        if len(out) >= 40:
            break
    return out


_PTR_FILE = re.compile(r"(?<![\w/.@~-])((?:~/|\.{1,2}/|/)?[\w@+-][\w@+.-]*(?:/[\w@+.-]+)*\.[A-Za-z][A-Za-z0-9]{0,5})(?![\w-])")
_PTR_DIR = re.compile(r"(?<![\w/.@~-])((?:~/|\.{1,2}/|/)?[\w@+-][\w@+.-]*(?:/[\w@+.-]+)*/)(?![\w-])")


def find_pointers(text: str, src: Path, roots: list[Path], cap: int = 30) -> list[dict]:
    """Files and folders an instruction file points at (its routing table) that exist on disk, in document order.
    Imports (@path) are listed separately; a mention that resolves nowhere is dropped, so prose does not add noise."""
    seen: set[str] = set()
    out: list[dict] = []
    tried = 0
    for rx in (_PTR_FILE, _PTR_DIR):
        for m in rx.finditer(text):
            raw = m.group(1).rstrip(".,;:")
            if raw in seen or tried > 600:
                continue
            seen.add(raw)
            tried += 1
            bases = [Path("/")] if raw.startswith(("~", "/")) else [src.parent, *roots]
            for base in bases:
                cand = Path(os.path.expanduser(raw)) if raw.startswith(("~", "/")) else base / raw
                try:
                    ok = cand.exists() and cand.resolve() != src.resolve()
                except OSError:
                    ok = False
                if ok:
                    out.append({"raw": raw, "resolved": os.path.normpath(str(cand)), "kind": "dir" if cand.is_dir() else "file"})
                    break
    return out[:cap]


INSTR_TEXT: dict[str, str] = {}


def instruction_scope(p: Path, target: Path) -> str:
    """"project" for an instruction file at the top of the git repo that holds it (the repo's root, or its .claude folder),
    "folder" for one inside a subfolder of that repo. The repo is the file's own, found from where the file sits, not the
    folder a session started in: a start folder that is a container of many repos (~/claude) is no project, and each repo
    below it has its own top. A file in no repo is at the top when it sits in the start folder itself (or its .claude).
    A repo around the whole home folder (dotfiles) is no repo for this."""
    here = Path(os.path.realpath(p.parent))
    top = git_root(p.parent)
    if top is not None and (top == Path(os.path.realpath(HOME)) or top in Path(os.path.realpath(HOME)).parents):
        top = None   # a dotfiles repo around the whole home folder is no project
    top = top or Path(os.path.realpath(target))
    return "project" if here == top or (here.name == ".claude" and here.parent == top) else "folder"


def instruction_record(p: Path, kind: str, scope: str, project: Path | None) -> dict:
    rec: dict = {"path": str(p), "kind": kind, "scope": scope, "project": str(project) if project else None}
    try:
        st = p.stat()
        rec["bytes"], rec["mtime"] = st.st_size, iso(st.st_mtime)
    except OSError as e:
        cnr(p, f"stat failed: {e.strerror or e}")
    if p.is_symlink():
        rec["symlink_to"] = os.path.realpath(p)
    text = read_text(p)
    if text is not None:
        rec["lines"] = count_lines(text)
        rec["imports"] = find_imports(text, p)
        gr = git_root(p)
        rec["pointers"] = find_pointers(text, p, [r for r in (project, gr) if r])
        INSTR_TEXT[str(p)] = text
    posix = p.as_posix()
    rel = posix[len(project.as_posix()):] if project and posix.startswith(project.as_posix()) else posix
    rec["in_fixture_path"] = bool(FIXTURE_RE.search(rel))
    rec["git"] = git_info(p)
    # whose it is, by the one ownership rule, like every other artifact (until 11-09 the page decided it from the AI
    # brief's top 10 always-loaded files by its own commit share, so nested and smaller files of theirs went uncounted)
    rec["origin"], rec["origin_evidence"], _ = classify_origin(p, p.name, "instruction")
    return rec


# ---------------------------------------------------------------- settings, hooks, MCP, plugins

class Store:
    """Everything collected, keyed so a file shared by two targets is listed once."""

    def __init__(self):
        self.lists: dict[str, list[dict]] = defaultdict(list)
        self.keys: dict[str, set] = defaultdict(set)

    def add_artifact(self, rec: dict) -> bool:
        """One artifact per real file: a symlinked skills folder lists the same skill twice otherwise."""
        real = os.path.realpath(rec["path"])
        for other in self.lists["artifacts"]:
            if other["_real"] == real:
                other.setdefault("also_at", []).append(rec["path"])
                return False
        rec["_real"] = real
        self.lists["artifacts"].append(rec)
        return True

    def add(self, name: str, rec: dict, key=None) -> bool:
        k = key if key is not None else rec.get("path")
        if k is not None:
            if k in self.keys[name]:
                return False
            self.keys[name].add(k)
        self.lists[name].append(rec)
        return True


S = Store()
ENABLED: dict[str, list[dict]] = defaultdict(list)   # plugin key -> [{file, scope, value}]
HOOK_SCRIPTS: set[str] = set()                        # realpaths wired from any hook / statusLine
ENV_USE: dict[str, str] = {}                          # values of env keys we need internally (never written out)
NOTABLE_KEYS = {"cleanupPeriodDays", "outputStyle", "disableBundledSkills", "model", "effortLevel", "autoMemoryEnabled",
                "enableAllProjectMcpServers", "disableAllHooks", "disableSkillShellExecution", "skipDangerousModePermissionPrompt",
                "includeCoAuthoredBy", "alwaysThinkingEnabled", "language", "autoUpdatesChannel", "forceLoginMethod",
                "defaultMode", "disableAutoMode", "teammateMode", "outputLanguage"}
SCRIPT_TOKEN_EXT = re.compile(r"\.(?:sh|bash|zsh|py|js|mjs|cjs|ts|mts|cts|tsx|rb|pl|ps1|bat|cmd|php|lua)$", re.I)
INTERPRETERS = {"env", "bash", "sh", "zsh", "fish", "node", "nodejs", "python", "python3", "uv", "uvx", "npx", "bun", "bunx", "deno",
                "ruby", "perl", "pwsh", "powershell", "cmd", "claude", "git", "echo", "cat", "jq", "cd", "exec", "sudo", "time",
                "nohup", "timeout", "open", "osascript", "curl", "wget", "true", "false", "test", "printf", "tee", "pnpm", "npm",
                "yarn", "pnpx", "just", "make"}


def expand_vars(s: str, project: Path | None, plugin_root: Path | None) -> str:
    def sub(name: str) -> str | None:
        if name == "CLAUDE_PROJECT_DIR" and project:
            return str(project)
        if name == "CLAUDE_PLUGIN_ROOT" and plugin_root:
            return str(plugin_root)
        if name == "HOME":
            return str(HOME)
        return None

    def repl(m: re.Match) -> str:
        v = sub(m.group(1) or m.group(2))
        return v if v is not None else m.group(0)

    s = re.sub(r"\$\{([A-Z_]+)\}|\$([A-Z_]+)", repl, s)
    return os.path.expanduser(s) if s.startswith("~") else s


def script_from_command(cmd: str, project: Path | None, plugin_root: Path | None) -> tuple[str | None, bool | None, bool]:
    """(script_path, script_exists, resolves_on_path). The first token that looks like a script file."""
    try:
        toks = shlex.split(cmd[:2000], posix=(os.name != "nt"))
    except ValueError:
        toks = cmd[:2000].split()
    cwd = project
    cands: list[str] = []
    for i, t in enumerate(toks):
        t = t.strip("\"'")
        if len(t) > 260 or "\n" in t or " " in t:
            continue  # an inline script (node -e '...'), not a file
        if t == "cd" and i + 1 < len(toks):
            cwd = Path(expand_vars(toks[i + 1].strip("\"'"), project, plugin_root))
            continue
        if not t or t.startswith("-") or t in ("&&", "||", ";", "|", "$(", ")"):
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            continue
        if SCRIPT_TOKEN_EXT.search(t) or t.startswith(("/", "~/", "./", "../", "$")) or ("/" in t and not t.startswith("http")):
            cands.append(expand_vars(t, project, plugin_root))
    pick = next((c for c in cands if SCRIPT_TOKEN_EXT.search(c)), None) \
        or next((c for c in cands if os.path.basename(c) not in INTERPRETERS), None)
    first = (toks[0].strip("\"'") if toks else "")
    if pick:
        p = Path(pick)
        if not p.is_absolute():
            p = (cwd or project or HOME) / p
        p = Path(os.path.normpath(str(p)))
        try:
            return str(p), p.exists(), False
        except OSError:
            return str(p), None, False
    return None, None, bool(first) and shutil.which(first) is not None


def parse_hooks(obj, source: str, source_kind: str, scope: str, project: Path | None, plugin_root: Path | None = None,
                plugin_key: str | None = None, base_dir: Path | None = None) -> list[dict]:
    if not isinstance(obj, dict):
        return []
    base = base_dir or project
    hk = obj["hooks"] if isinstance(obj.get("hooks"), dict) else obj
    out = []
    for event, groups in hk.items():
        if not isinstance(groups, list):
            continue
        for g in groups:
            if not isinstance(g, dict):
                continue
            matcher = g.get("matcher")
            defs = g["hooks"] if isinstance(g.get("hooks"), list) else [g]
            for h in defs:
                if not isinstance(h, dict):
                    continue
                typ = h.get("type", "command")
                rec: dict = {"scope": scope, "project": str(project) if project and scope == "project" else None,
                             "source_kind": source_kind, "source": source, "event": event, "matcher": matcher, "type": typ}
                if plugin_key:
                    rec["plugin"] = plugin_key
                if typ == "command" and isinstance(h.get("command"), str):
                    rec["command"] = scrub(h["command"], 200)
                    sp, exists, onpath = script_from_command(h["command"], base, plugin_root)
                    rec["script_path"], rec["script_exists"], rec["resolves_on_path"] = sp, exists, onpath
                    if sp and exists:
                        HOOK_SCRIPTS.add(os.path.realpath(sp))
                elif typ in ("prompt", "agent"):
                    rec["prompt_chars"] = len(str(h.get("prompt", "")))
                elif typ == "http":
                    rec["url_host"] = urlparse(str(h.get("url", ""))).hostname
                for k in ("timeout", "async", "once"):
                    if k in h:
                        rec[k] = h[k]
                out.append(rec)
    return out


def mcp_records(servers, source: str, scope: str, project: Path | None) -> list[dict]:
    out = []
    if not isinstance(servers, dict):
        return out
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        rec: dict = {"name": str(name), "source": source, "scope": scope, "project": str(project) if project else None,
                     "transport": cfg.get("type") or ("http" if cfg.get("url") else "stdio")}
        if cfg.get("command"):
            rec["command"] = scrub(cfg["command"], 120)
            args = [str(a) for a in cfg.get("args", []) if not isinstance(a, (dict, list))]
            rec["args_count"] = len(args)
            # the first positional arg that is not a runner verb and not the value of a secret-looking flag
            entry = next((a for i, a in enumerate(args) if not a.startswith("-") and a not in ("run", "exec", "dlx", "x", "tool", "start")
                          and not (i and args[i - 1].startswith("-") and SECRET_KEY_RE.search(args[i - 1]))), None)
            if entry and re.search(r"[\s;|&]", entry):
                rec["inline_script_chars"], entry = len(entry), None   # bash -c '...': not a file to open
            if entry:
                rec["entry"] = scrub(entry, 120)
        if cfg.get("url"):
            rec["url_host"] = urlparse(str(cfg["url"])).hostname
        for k in ("env", "headers"):
            if isinstance(cfg.get(k), dict):
                rec[k] = secret_names(cfg[k])
        out.append(rec)
    return out


def process_settings(path: Path, scope: str, project: Path | None):
    d = read_json(path)
    if not isinstance(d, dict):
        return
    try:
        st = path.stat()
    except OSError:
        return
    S.add("settings_files", {"path": str(path), "scope": scope, "project": str(project) if project else None,
                             "bytes": st.st_size, "mtime": iso(st.st_mtime), "top_level_keys": sorted(d.keys())})
    # $CLAUDE_PROJECT_DIR is the folder that holds this .claude, which may be a sub-project of the target
    base = path.parent.parent if path.parent.name == ".claude" and scope in ("project", "ancestor") else project
    for rec in parse_hooks(d.get("hooks"), str(path), "settings", scope, project, base_dir=base):
        S.lists["hooks"].append(rec)
    for key in ("statusLine", "fileSuggestion"):
        v = d.get(key)
        if isinstance(v, dict) and v.get("command"):
            sp, exists, onpath = script_from_command(str(v["command"]), base, None)
            if sp and exists:
                HOOK_SCRIPTS.add(os.path.realpath(sp))
            S.lists["hooks"].append({"scope": scope, "project": str(project) if project else None, "source_kind": "statusline",
                                     "source": str(path), "event": key, "matcher": None, "type": "command",
                                     "command": scrub(v["command"], 200), "script_path": sp, "script_exists": exists,
                                     "resolves_on_path": onpath})
    for k, v in (d.get("enabledPlugins") or {}).items() if isinstance(d.get("enabledPlugins"), dict) else []:
        ENABLED[k].append({"file": str(path), "scope": scope, "value": v})
    for k, v in (d.get("extraKnownMarketplaces") or {}).items() if isinstance(d.get("extraKnownMarketplaces"), dict) else []:
        if isinstance(v, dict):
            S.add("settings_marketplaces", {"name": k, "file": str(path), "source": v.get("source")}, key=(k, str(path)))
    for rec in mcp_records(d.get("mcpServers"), str(path), scope, project):
        S.lists["mcp_servers"].append(rec)
    perms = d.get("permissions") if isinstance(d.get("permissions"), dict) else {}
    notable, counts = [], {"file": str(path)}
    for k in NOTABLE_KEYS:
        v = d.get(k) if k in d else perms.get(k)
        if isinstance(v, (str, int, float, bool)) and (k in d or k in perms):
            notable.append({"file": str(path), "scope": scope, "key": k if k in d else f"permissions.{k}", "value": v})
    wt = d.get("worktree")
    if isinstance(wt, dict):
        notable.append({"file": str(path), "scope": scope, "key": "worktree",
                        "value": {k: v for k, v in wt.items() if isinstance(v, (bool, int, str)) or
                                  (isinstance(v, list) and all(isinstance(x, str) and not SECRET_KEY_RE.search(x) for x in v))}})
    for k in ("allow", "deny", "ask", "additionalDirectories"):
        if isinstance(perms.get(k), list):
            counts[f"permissions_{k}"] = len(perms[k])
    for k in ("skillOverrides", "deniedMcpServers", "enabledPlugins", "extraKnownMarketplaces"):
        if isinstance(d.get(k), (dict, list)):
            counts[k] = len(d[k])
    S.lists["settings_notable"].extend(notable)
    S.lists["settings_counts"].append(counts)
    env = d.get("env")
    if isinstance(env, dict):
        for item in secret_names(env):
            S.lists["settings_env_keys"].append({"file": str(path), **item})
        for k in ("WIKILINKS_ROOTS", "CLAUDE_CODE_PLUGIN_DIRS"):
            if isinstance(env.get(k), str):
                ENV_USE[k] = env[k]


def read_claude_json(targets: list[Path]):
    p = HOME / ".claude.json"
    d = read_json(p)
    if not isinstance(d, dict):
        return
    for rec in mcp_records(d.get("mcpServers"), f"{p} (user scope)", "home", None):
        S.lists["mcp_servers"].append(rec)
    projects = d.get("projects") if isinstance(d.get("projects"), dict) else {}
    others = 0
    for pp, v in projects.items():
        if not isinstance(v, dict) or not v.get("mcpServers"):
            continue
        pth = Path(pp)
        tgt = next((t for t in targets if pth == t or t in pth.parents), None)
        if tgt is None:
            others += 1
            continue
        for rec in mcp_records(v["mcpServers"], f"{p} (local scope, projects[{pp}])", "project", tgt):
            S.lists["mcp_servers"].append(rec)
    S.lists["mcp_notes"].append({"other_projects_with_local_mcp_servers": others})


# ---------------------------------------------------------------- artifacts

LOCKS: dict[str, dict] = {}   # skill name -> {source, lockfile}


def load_locks(files: list[Path]):
    for f in files:
        d = read_json(f)
        skills = d.get("skills") if isinstance(d, dict) else None
        if isinstance(skills, dict):
            for name, v in skills.items():
                if isinstance(v, dict):
                    LOCKS.setdefault(name, {"source": str(v.get("source") or v.get("sourceUrl") or "?")[:80], "lockfile": str(f)})


def _under(p: Path, base: Path) -> bool:
    try:
        p.relative_to(base)
        return True
    except ValueError:
        return False


PLUGIN_STORE = HOME / ".claude" / "plugins"
PROJECTS_DIR = HOME / ".claude" / "projects"
TMP_SLUG = re.compile(r"^-(private-)?(tmp|var-folders)(-|$)")   # a session that started in a temporary folder


def _ownership():
    """The one ownership rule (2026-11-02-evidence/ownership.py): theirs unless a receipt says installed."""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("ownership", os.path.join(here, "..", "2026-11-02-evidence", "ownership.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


OWNERSHIP = _ownership()
RECEIPTS = None   # built once, when the project roots are known (step 4)
WRITTEN_DIRS = ()   # folders the person's agents wrote a file in (--written-dirs: the evidence scan reads the logs)


def written_in_logs(folder):
    """ownership.owner()'s callback: their agent wrote a file under this skill folder, so it is not an unpacked install."""
    want = {folder, os.path.realpath(folder)}
    return any(d == w or d.startswith(w.rstrip("/") + "/") for d in WRITTEN_DIRS for w in want)


def classify_origin(path: Path, name: str, kind: str) -> tuple[str, str, dict | None]:
    """own | installed | mixed | third_party, by ownership.owner (his ruling 11-05): installed only on a receipt (a
    published marketplace's cache, a skills lock, account-synced skills), a teammate's only when git shows most commits
    are someone else's, and theirs otherwise. "Not in git" is theirs: until 11-05 it was "untracked" and the v15 scan
    dropped it, which lost a tester's own skills."""
    real = Path(os.path.realpath(path))
    link = f" (symlink to {real})" if path.is_symlink() else ""
    if "/skills/synced/" in path.as_posix():
        return "installed", "synced from the account (skills/synced)", None
    if kind == "instruction" and OWNERSHIP.claude_code_copy(path):
        return "installed", "a copy Claude Code keeps in its plugin cache or marketplaces folder", None
    # skills locks are read by ownership.py by path (the lock's own folder), not by name: a skill of theirs that shares
    # a name with an installed one stays theirs
    rc = RECEIPTS or OWNERSHIP.load_receipts(use_claude=False)
    gi_box = {}

    def auth(p):
        gi = git_info(Path(p))
        gi_box["gi"] = gi
        if gi.get("in_git") and gi.get("commits"):
            return {"commits": gi["commits"], "theirs": gi.get("user_commits") or 0}
        return None

    def blob_authorship(repo, sha):
        """The commits of repo that added or removed this version of a file, and how many are theirs (ownership.py rule 4)."""
        out = git_out(["log", "--all", f"--find-object={sha}", "--format=%ae%x09%an", "-n", "50"], Path(repo), 60)
        rows = [ln.split("\t") + [""] for ln in (out or "").splitlines() if ln]
        return {"commits": len(rows), "theirs": sum(Identity.is_user(x[0], x[1]) for x in rows)} if rows else None

    o = OWNERSHIP.owner(str(path), rc, auth, written_in_logs=written_in_logs, blob_authorship=blob_authorship)
    if o["owner"] == "installed":
        return "installed", f"{o['how']}: {o.get('source') or ''}", None
    gi = gi_box.get("gi") or {}
    src = f" (read from {o['source']})" if o.get("source") else ""
    if o["how"] == "git":
        u, n = gi.get("user_commits") or 0, gi["commits"]
        slim = {k: gi[k] for k in ("commits", "last_commit", "user_commits") if k in gi}
        ev = f"git: {u} of {n} commits by an identity email, last {gi.get('last_commit')}{link}{src}"
        return ("own" if o["owner"] == "theirs" else "mixed" if u else "third_party"), ev, slim
    where = f"in git repo {gi['repo']} but never committed" if gi.get("in_git") else "not in git"
    return "own", f"no install receipt (no marketplace, no skills lock); {where}: theirs by default{link}{src}", None


def dir_stats(d: Path, cap: int = 600) -> tuple[int, bool]:
    n, scripts = 0, False
    for dp, dns, fns in os.walk(d):
        dns[:] = [x for x in dns if x not in PRUNE]
        for fn in fns:
            n += 1
            if os.path.splitext(fn)[1].lower() in {".py", ".sh", ".js", ".mjs", ".ts", ".rb", ".cjs"}:
                scripts = True
        if n >= cap:
            break
    return n, scripts


def make_artifact(kind: str, path: Path, scope: str, project: Path | None, entry: Path | None = None, name: str | None = None) -> dict:
    name = name or (path.name if kind == "skill" else path.stem)
    rec: dict = {"kind": kind, "name": name, "path": str(path), "scope": scope, "project": str(project) if project else None}
    if entry is not None:
        rec["entry"] = str(entry)
    if path.is_symlink():
        rec["symlink_to"] = os.path.realpath(path)
    src = entry or path
    text = read_text(src) if src.is_file() else None
    try:
        rec["bytes"] = src.stat().st_size
    except OSError:
        pass
    if text:
        fm = frontmatter(text)
        if fm:
            if fm.get("description"):
                rec["description"] = scrub(fm["description"], 200)
            for k in ("model", "memory"):
                if fm.get(k):
                    rec[k] = fm[k]
            if "tools" in fm:
                rec["tools_count"] = len([t for t in re.split(r"[,\s]+", fm["tools"]) if t])
            if "hooks" in fm:
                rec["frontmatter_hooks"] = True
                m = _FM.match(text.lstrip("\ufeff"))
                rec["_fm_hooks"] = m.group(1)[:4000] if m else ""
    if kind == "skill" and path.is_dir():
        rec["files"], rec["has_scripts"] = dir_stats(path)
    rec["in_fixture_path"] = bool(FIXTURE_RE.search((os.path.relpath(path, project) if project and _under(path, project) else str(path)).replace(os.sep, "/")))
    origin, ev, slim = classify_origin(path, name, kind)
    rec["origin"], rec["origin_evidence"] = origin, scrub(ev, 220)
    if slim:
        rec["git"] = slim
    return rec


def plugin_root_of(path: str, _cache: dict = {}) -> str | None:
    """The plugin folder (the one holding .claude-plugin/plugin.json) a file sits in, else None."""
    d = os.path.dirname(path)
    seen = []
    while d and d not in ("/", str(HOME)):
        if d in _cache:
            r = _cache[d]
            break
        seen.append(d)
        if os.path.isfile(os.path.join(d, ".claude-plugin", "plugin.json")):
            r = d
            break
        d = os.path.dirname(d)
    else:
        r = None
    for x in seen:
        _cache[x] = r
    return r


def plugin_name(root: str, _cache: dict = {}) -> str:
    """A plugin's own name (plugin.json "name"), so copies kept per version (cache/<mk>/<plugin>/<version>/, a tool's
    cache/<plugin>/<version-hash>/) are one plugin; the folder name when the manifest has none."""
    if root not in _cache:
        mf = read_json(Path(root) / ".claude-plugin" / "plugin.json")
        _cache[root] = str((mf or {}).get("name") or os.path.basename(root)) if isinstance(mf, dict) else os.path.basename(root)
    return _cache[root]


def dedupe_plugin_copies(artifacts: list[dict]) -> int:
    """One plugin read from two folders (a release copy and a dev checkout, a worktree) lists each of its files twice,
    so one thing they built counted twice; a tool that caches a plugin per version (55 copies seen 11-05) counted it 55
    times. Keep one per (kind, plugin name from plugin.json, path inside the plugin): the copy git
    has the most commits on, then one outside a worktree, then the shortest path. Returns how many were dropped."""
    groups: dict = {}
    for i, a in enumerate(artifacts):
        pr = plugin_root_of(a.get("path") or "")
        if pr:
            groups.setdefault((a["kind"], plugin_name(pr), os.path.relpath(a["path"], pr)), []).append(i)
    drop = set()
    for idx in groups.values():
        if len(idx) > 1:
            best = min(idx, key=lambda i: (-((artifacts[i].get("git") or {}).get("commits") or 0),
                                           "/worktrees/" in artifacts[i]["path"], len(artifacts[i]["path"]), artifacts[i]["path"]))
            for i in idx:
                if i != best:
                    drop.add(i)
                    artifacts[best].setdefault("also_at", []).append(artifacts[i]["path"])
    artifacts[:] = [a for i, a in enumerate(artifacts) if i not in drop]
    return len(drop)


def find_skill_dirs(root: Path, max_depth: int = 3) -> list[Path]:
    out = []
    try:
        for e in sorted(os.scandir(root), key=lambda x: x.name):
            if e.name.startswith(".") or e.name in PRUNE:
                continue
            p = Path(e.path)
            if (p / "SKILL.md").is_file():
                out.append(p)
            elif max_depth > 1 and p.is_dir():
                out.extend(find_skill_dirs(p, max_depth - 1))
    except OSError as err:
        cnr(root, f"cannot list: {err.strerror or err}")
    return out


def scan_claude_dir(cdir: Path, scope: str, project: Path | None):
    """Settings are handled elsewhere; this lists what lives in a .claude folder."""
    try:
        names = sorted(e.name for e in os.scandir(cdir))
    except OSError as e:
        cnr(cdir, f"cannot list: {e.strerror or e}")
        return
    for name in names:
        p = cdir / name
        if name == "skills" and p.is_dir():
            for sd in find_skill_dirs(p):
                S.add_artifact(make_artifact("skill", sd, scope, project, sd / "SKILL.md"))
        elif name == "agents" and p.is_dir():
            for f in sorted(p.rglob("*.md")):
                S.add_artifact(make_artifact("agent", f, scope, project))
        elif name == "commands" and p.is_dir():
            for f in sorted(p.rglob("*.md")):
                rel = f.relative_to(p).with_suffix("").as_posix().replace("/", ":")
                S.add_artifact(make_artifact("command", f, scope, project, name=rel))
        elif name == "workflows" and p.is_dir():
            for f in sorted(x for x in p.iterdir() if x.is_file()):
                S.add_artifact(make_artifact("workflow", f, scope, project))
        elif name == "output-styles" and p.is_dir():
            for f in sorted(p.glob("*.md")):
                S.add_artifact(make_artifact("output_style", f, scope, project))
        elif name == "hooks" and p.is_dir():
            for f in sorted(x for x in p.iterdir() if x.is_file()):
                S.add_artifact(make_artifact("hook_script", f, scope, project))
        elif name == "rules" and p.is_dir():
            for f in sorted(p.rglob("*.md")):
                S.add("instruction_candidates", {"path": f, "kind": "claude_rules", "scope": scope, "project": project}, key=str(f))
        elif name == "CLAUDE.md" and p.is_file():
            S.add("instruction_candidates", {"path": p, "kind": "claude", "scope": scope, "project": project}, key=str(p))
        elif MEM_NAME.search(name) and p.is_dir():
            S.add("memory_candidates", {"path": p, "kind": "agent_memory", "scope": scope, "project": project}, key=str(p))
        elif re.search(r"mcp.*\.json$", name) and "cache" not in name and p.is_file() and scope == "home":
            d = read_json(p)
            if isinstance(d, dict):
                for rec in mcp_records(d.get("mcpServers", d), str(p), scope, project):
                    S.lists["mcp_servers"].append(rec)
        elif name == "scheduled_tasks.json" and p.is_file():
            d = read_json(p)
            tasks = d.get("tasks") if isinstance(d, dict) else None
            S.lists["claude_scheduled_tasks"].append({"path": str(p), "scope": scope, "count": len(tasks) if isinstance(tasks, list) else None,
                                                      "tasks": [scrub(json.dumps({k: v for k, v in t.items() if k in ("cron", "schedule", "name", "id", "prompt", "recurring")}), 200)
                                                                for t in (tasks or [])[:10] if isinstance(t, dict)]})


# ---------------------------------------------------------------- plugins

def plugin_contents(root: Path, manifest: dict | None) -> tuple[dict, dict | None]:
    """What a plugin directory carries, and its hooks object (from hooks/hooks.json or plugin.json)."""
    c: dict = {"skills": [], "agents": [], "commands": [], "hooks_events": [], "mcp_servers": [], "output_styles": []}
    for sd in find_skill_dirs(root / "skills", 2) if (root / "skills").is_dir() else []:
        c["skills"].append({"name": sd.name, "path": str(sd / "SKILL.md")})
    if (root / "agents").is_dir():
        c["agents"] = [{"name": f.stem, "path": str(f)} for f in sorted((root / "agents").glob("*.md"))]
    if (root / "commands").is_dir():
        c["commands"] = [{"name": f.relative_to(root / "commands").with_suffix("").as_posix(), "path": str(f)} for f in sorted((root / "commands").rglob("*.md"))]
    if (root / "output-styles").is_dir():
        c["output_styles"] = [{"name": f.stem, "path": str(f)} for f in sorted((root / "output-styles").glob("*.md"))]
    hooks_obj = None
    hp = root / "hooks" / "hooks.json"
    if hp.is_file():
        hooks_obj = read_json(hp)
    elif manifest and isinstance(manifest.get("hooks"), dict):
        hooks_obj = manifest["hooks"]
    elif manifest and isinstance(manifest.get("hooks"), str):
        hooks_obj = read_json((root / manifest["hooks"]).resolve())
    if isinstance(hooks_obj, dict):
        hk = hooks_obj["hooks"] if isinstance(hooks_obj.get("hooks"), dict) else hooks_obj
        c["hooks_events"] = sorted(k for k, v in hk.items() if isinstance(v, list) and any(
            isinstance(g, dict) and ("hooks" in g or "command" in g) for g in v))
    mp = root / ".mcp.json"
    servers = read_json(mp) if mp.is_file() else (manifest or {}).get("mcpServers")
    if isinstance(servers, dict):
        servers = servers.get("mcpServers", servers)
        c["mcp_servers"] = sorted(servers.keys()) if isinstance(servers, dict) else []
    for k in ("skills", "agents", "commands"):  # keep the output readable: names only past 40
        if len(c[k]) > 40:
            c[k + "_truncated_from"] = len(c[k])
            c[k] = c[k][:40]
    return c, hooks_obj if isinstance(hooks_obj, dict) else None


def source_label(src: dict) -> str | None:
    """repo / path / host+path of a marketplace source, without credentials or query."""
    if src.get("repo") or src.get("path"):
        return src.get("repo") or src.get("path")
    u = urlparse(str(src.get("url", "")))
    return f"{u.hostname}{u.path}".removesuffix(".git") if u.hostname else None


def user_share_of_repo(path: Path) -> tuple[float | None, int]:
    """Share of the last 300 commits in the repo containing `path` made by an identity email."""
    root = git_root(Path(os.path.realpath(path)))
    if root is None:
        return None, 0
    out = git_out(["log", "-n", "300", "--format=%ae%x09%an"], root, 15)
    rows = [ln.split("\t") + [""] for ln in (out or "").splitlines() if ln]
    if not rows:
        return None, 0
    return round(sum(Identity.is_user(r[0], r[1]) for r in rows) / len(rows), 2), len(rows)


def collect_plugins(targets: list[Path]) -> dict:
    installed = read_json(PLUGIN_STORE / "installed_plugins.json") or {}
    inst = installed.get("plugins", {}) if isinstance(installed, dict) else {}
    known = read_json(PLUGIN_STORE / "known_marketplaces.json") or {}
    mkts: dict[str, dict] = {}
    for name, v in (known.items() if isinstance(known, dict) else []):
        src = v.get("source", {}) if isinstance(v, dict) else {}
        mkts[name] = {"name": name, "source_type": src.get("source"), "last_updated": v.get("lastUpdated") if isinstance(v, dict) else None,
                      "install_location": v.get("installLocation") if isinstance(v, dict) else None,
                      "source": source_label(src)}
    for rec in S.lists["settings_marketplaces"]:
        src = rec.get("source") or {}
        m = mkts.setdefault(rec["name"], {"name": rec["name"], "source_type": src.get("source"), "source": source_label(src)})
        m.setdefault("declared_in", []).append(rec["file"])
    for m in mkts.values():
        loc = m.get("install_location") or (m["source"] if m.get("source_type") in ("directory", "file") else None)
        if loc and Path(loc).exists() and m.get("source_type") in ("directory", "file"):
            share, n = user_share_of_repo(Path(loc))
            m["local_repo_user_commit_share"], m["local_repo_commits_sampled"] = share, n
            m["is_own"] = bool(share is not None and share >= 0.5)
        else:
            m["is_own"] = False

    def enabled_state(key: str):
        vals = [e["value"] for e in ENABLED.get(key, [])]
        return True if any(v is True for v in vals) else False if vals else None

    enabled_out = []
    keys = sorted(set(inst) | set(ENABLED))
    for key in keys:
        entries = inst.get(key) if isinstance(inst.get(key), list) else []
        ent = entries[-1] if entries else {}
        name, _, mk = key.partition("@")
        ip = Path(ent["installPath"]) if ent.get("installPath") else None
        if ip is None or not ip.exists():
            cdir = PLUGIN_STORE / "cache" / mk / name
            vers = sorted((x for x in cdir.iterdir() if x.is_dir()), key=lambda x: x.stat().st_mtime) if cdir.is_dir() else []
            ip = vers[-1] if vers else ip
        rec: dict = {"key": key, "enabled": enabled_state(key), "enabled_in": [{"file": e["file"], "scope": e["scope"], "value": e["value"]} for e in ENABLED.get(key, [])],
                     "installed": bool(entries), "version": ent.get("version"), "install_scope": ent.get("scope"),
                     "git_commit_sha": (ent.get("gitCommitSha") or "")[:12] or None, "last_updated": ent.get("lastUpdated"),
                     "install_path": str(ip) if ip else None, "install_path_exists": bool(ip and ip.exists()), "marketplace": mk,
                     "marketplace_source": (mkts.get(mk) or {}).get("source"), "marketplace_is_own": (mkts.get(mk) or {}).get("is_own", False)}
        if rec["enabled"] and ip and ip.exists():
            manifest = read_json(ip / ".claude-plugin" / "plugin.json") if (ip / ".claude-plugin" / "plugin.json").is_file() else None
            rec["contents"], hooks_obj = plugin_contents(ip, manifest)
            if hooks_obj:
                for h in parse_hooks(hooks_obj, str(ip), "plugin", "home", None, plugin_root=ip, plugin_key=key):
                    S.lists["hooks"].append(h)
            for m in mcp_records(read_json(ip / ".mcp.json") if (ip / ".mcp.json").is_file() else (manifest or {}).get("mcpServers"),
                                 f"plugin {key}", "home", None):
                S.lists["mcp_servers"].append(m)
        enabled_out.append(rec)
    # plugin dirs loaded by env CLAUDE_CODE_PLUGIN_DIRS (value never written out) and plugins found inside the projects
    dir_loaded = []
    roots: list[tuple[Path, str]] = []
    for part in re.split(r"[:;,]" if os.name != "nt" else r"[;,]", ENV_USE.get("CLAUDE_CODE_PLUGIN_DIRS", "")):
        if part.strip():
            roots.append((Path(os.path.expanduser(part.strip())), "env CLAUDE_CODE_PLUGIN_DIRS"))
    proj_of: dict[str, Path] = {}
    for pr, how, tgt in S.lists.get("project_plugin_roots", []):
        roots.append((pr, how))
        proj_of[str(pr)] = tgt
    seen = set()
    for root, how in roots:
        real = os.path.realpath(root)
        if real in seen or not root.exists():
            continue
        seen.add(real)
        manifest = read_json(root / ".claude-plugin" / "plugin.json") if (root / ".claude-plugin" / "plugin.json").is_file() else None
        # a dir that is a marketplace of plugins: descend one level
        subs = [root] if manifest else [x for x in sorted(root.iterdir()) if (x / ".claude-plugin" / "plugin.json").is_file()] if root.is_dir() else []
        for pr in subs:
            mf = read_json(pr / ".claude-plugin" / "plugin.json") if pr != root else manifest
            contents, hooks_obj = plugin_contents(pr, mf if isinstance(mf, dict) else None)
            origin, ev, _ = classify_origin(pr, pr.name, "plugin")
            if hooks_obj:
                inside = how.startswith("inside")
                tgt = proj_of.get(str(root)) if inside else None
                for h in parse_hooks(hooks_obj, str(pr), "project_plugin" if inside else "plugin", "project" if inside else "home", tgt,
                                     plugin_root=pr, plugin_key=str(pr.name)):
                    S.lists["hooks"].append(h)
            dir_loaded.append({"path": str(pr), "name": (mf or {}).get("name", pr.name), "version": (mf or {}).get("version"),
                               "found_via": how, "origin": origin, "origin_evidence": scrub(ev, 200), "contents": contents})
    return {"enabled": enabled_out, "dir_loaded": dir_loaded, "marketplaces": sorted(mkts.values(), key=lambda m: m["name"])}


# ---------------------------------------------------------------- memory stores

WIKILINK = re.compile(r"\[\[([^\]\n|#][^\]\n|#]*)(?:#[^\]\n|]*)?(?:\|[^\]\n]*)?\]\]")
STORE_PRUNE = {".git", "node_modules", "__pycache__", ".venv", "venv"}
INDEX_NAMES = {"memory.md", "readme.md", "index.md", "_index.md", "moc.md", "home.md"}
VAULT_NAME = re.compile(r"(?i)^(?:vault|notes?|brain|wiki|kb|knowledge|second[-_ ]?brain|zettelkasten|journal|memory|memories)$")


def scan_store(root: Path, kind: str, scope: str, project: Path | None, referenced_from: list[str] | None = None,
               budget_s: float = 8.0, max_md_read: int = 3000) -> dict | None:
    t_end = time.monotonic() + budget_s
    files = total = 0
    oldest = newest = None
    sub: Counter = Counter()
    md: list[str] = []
    idx_names: set[str] = set()
    index_files: list[str] = []
    truncated = False
    root_s = str(root)
    for dp, dns, fns in os.walk(root_s, followlinks=False):
        dns[:] = [d for d in dns if d not in STORE_PRUNE]   # a store keeps its artifacts: only dependency/VCS dirs are skipped
        rel_dir = os.path.relpath(dp, root_s)
        for fn in fns:
            fp = os.path.join(dp, fn)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            files += 1
            total += st.st_size
            oldest = st.st_mtime if oldest is None or st.st_mtime < oldest else oldest
            newest = st.st_mtime if newest is None or st.st_mtime > newest else newest
            top = rel_dir.split(os.sep)[0] if rel_dir != "." else "(root)"
            sub[top] += 1
            lf = fn.lower()
            idx_names.add(lf)
            stem = os.path.splitext(lf)[0]
            idx_names.add(stem)
            relp = (os.path.join(rel_dir, fn) if rel_dir != "." else fn).replace(os.sep, "/").lower()
            idx_names.add(os.path.splitext(relp)[0])
            if lf.endswith(".md"):
                md.append(fp)
                if lf in INDEX_NAMES and len(index_files) < 20 and (rel_dir == "." or lf == "memory.md"):
                    index_files.append(fp)
        if time.monotonic() > t_end or files > 60_000:
            truncated = True
            cnr(root, f"memory store scan stopped early at {files} files")
            break
    if files == 0:
        return None
    if not md and kind in ("named_memory", "agent_memory"):
        return None
    with_fm = read_n = 0
    links_total = resolved = 0
    targets: Counter = Counter()
    unresolved: Counter = Counter()
    types: Counter = Counter()
    for fp in sorted(md)[:max_md_read]:
        try:
            if os.lstat(fp).st_size > 512_000:
                continue
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        read_n += 1
        fm = frontmatter(text)
        if fm is not None:
            with_fm += 1
            if fm.get("type"):
                types[fm["type"][:30]] += 1
        body = _INLINE.sub("", _FENCE.sub("", text))
        for m in WIKILINK.finditer(body):
            t = m.group(1).strip()
            if not t or t[0] in "-$!" or " " in t[:1]:
                continue
            links_total += 1
            key = t.lower()
            targets[key] += 1
            cand = {key, key[:-3] if key.endswith(".md") else key, os.path.basename(key), os.path.splitext(os.path.basename(key))[0]}
            if cand & idx_names:
                resolved += 1
            else:
                unresolved[key] += 1
    rec: dict = {"path": root_s, "kind": kind, "scope": scope, "project": str(project) if project else None,
                 "in_fixture_path": bool(FIXTURE_RE.search((os.path.relpath(root_s, project) if project and _under(root, project) else root_s).replace(os.sep, "/"))),
                 "referenced_from": referenced_from or [], "files": files, "bytes": total, "md_files": len(md), "md_read": read_n,
                 "md_with_frontmatter": with_fm, "frontmatter_share": round(with_fm / read_n, 3) if read_n else None,
                 "frontmatter_types": dict(types.most_common(8)), "index_files": index_files,
                 "top_subdirs": [{"name": k, "files": v} for k, v in sub.most_common(12)],
                 "wikilinks": {"total": links_total, "unique_targets": len(targets), "resolved": resolved,
                               "unresolved_unique": len(unresolved), "unresolved_sample": [k[:60] for k, _ in unresolved.most_common(8)]},
                 "oldest_mtime": iso(oldest), "newest_mtime": iso(newest), "truncated": truncated}
    gr = git_root(root)
    if gr is not None and GIT:
        rel = os.path.relpath(os.path.realpath(root), gr)
        r = git(["check-ignore", "-q", "--", rel], gr, 5)
        rec["gitignored"] = bool(r is not None and r.returncode == 0)
        ls = git_out(["ls-files", "--", rel], gr, 10)
        rec["tracked_files"] = len(ls.splitlines()) if ls is not None else None
    else:
        rec["gitignored"], rec["tracked_files"] = None, None
    return rec


def referenced_dirs(text: str, base: Path) -> list[Path]:
    out = []
    for m in re.finditer(r"(?:~|/Users/[\w.-]+|/home/[\w.-]+)(?:/[\w.@ -]+)+/?", text):
        s = m.group(0).rstrip("/")
        p = Path(os.path.expanduser(s))
        out.append(p)
    return out


def slug_of(p: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(p))


# ---------------------------------------------------------------- worktrees

def parse_worktrees(porcelain: str) -> list[dict]:
    items, cur = [], {}
    for line in porcelain.splitlines() + [""]:
        if not line.strip():
            if cur:
                items.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        if k == "worktree":
            cur["path"] = v
        elif k == "HEAD":
            cur["head"] = v
        elif k == "branch":
            cur["branch"] = v.replace("refs/heads/", "", 1)
        elif k in ("detached", "bare"):
            cur[k] = True
        elif k in ("locked", "prunable"):
            cur[k] = scrub(v, 100) or True
    return items


DEP_NAMES = {"node_modules": "node_modules", ".venv": ".venv", "venv": ".venv"}


def find_deps(wt: Path, main_root: Path, max_depth: int = 3, max_dirs: int = 500) -> list[dict]:
    """node_modules / .venv up to 3 levels down, as a real directory or a symlink (and whether it points into the main checkout)."""
    out, queue, seen = [], [(wt, 0)], 0
    while queue and seen < max_dirs:
        d, depth = queue.pop(0)
        seen += 1
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                is_link, is_dir = e.is_symlink(), e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if e.name in DEP_NAMES and (is_dir or is_link):
                rec = {"rel": os.path.relpath(e.path, wt), "kind": DEP_NAMES[e.name], "link": "symlink" if is_link else "dir"}
                if is_link:
                    tgt = os.path.realpath(e.path)
                    rec["target"] = tgt
                    rec["target_in_main"] = tgt == str(main_root) or tgt.startswith(str(main_root) + os.sep)
                out.append(rec)
            elif is_dir and not is_link and depth < max_depth and e.name not in PRUNE \
                    and not (e.name == "worktrees" and Path(d).name == ".claude") and not (Path(e.path) / ".git").exists():
                queue.append((Path(e.path), depth + 1))
    return out


def du_kb(path: Path, timeout: float) -> int | None:
    if timeout <= 0:
        return None
    if shutil.which("du"):
        try:
            r = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True, timeout=timeout)
            return int(r.stdout.split()[0]) if r.stdout.split() else None
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return None
    end, total = time.monotonic() + timeout, 0   # fallback without du (Windows)
    for dp, dns, fns in os.walk(path):
        for fn in fns:
            try:
                total += os.lstat(os.path.join(dp, fn)).st_size
            except OSError:
                pass
        if time.monotonic() > end:
            return None
    return total // 1024


def collect_worktrees(repo_candidates: list[Path], targets: list[Path], du_budget: float) -> list[dict]:
    if not GIT:
        cnr("git", "git not found: worktrees skipped")
        return []
    commons: dict[str, Path] = {}
    for cand in repo_candidates:
        if not cand.exists():
            continue
        gd = cand / ".git"
        if gd.is_dir() and not any((gd / "worktrees").glob("*")) and cand not in targets:
            continue  # a plain repo with no linked worktrees (cheap check, no subprocess)
        cd = git_out(["rev-parse", "--git-common-dir"], cand, 10)
        if not cd:
            continue
        c = Path(cd.strip())
        c = Path(os.path.realpath(c if c.is_absolute() else cand / c))
        commons.setdefault(str(c), cand)
    repos = []
    for common, cand in sorted(commons.items()):
        deadline = time.monotonic() + du_budget
        porc = git_out(["worktree", "list", "--porcelain"], cand, 20)
        if porc is None:
            continue
        wts = parse_worktrees(porc)
        if len(wts) < 2:
            continue
        main = Path(wts[0]["path"])
        repo_cwd = main if main.exists() else cand
        refs_out = git_out(["for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin"], repo_cwd, 10) or ""
        refs = set(refs_out.split())
        sym = (git_out(["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], repo_cwd, 5) or "").strip()
        default_name = sym.split("/", 1)[1] if sym.startswith("origin/") else next((b for b in ("main", "master", "trunk", "develop") if b in refs), None)
        default_refs = [r for r in (default_name, f"origin/{default_name}" if default_name else None) if r and r in refs]
        shas = [w.get("head") for w in wts if w.get("head")]
        dates: dict[str, str] = {}
        sh = git_out(["show", "-s", "--format=%H%x09%cI", *shas], repo_cwd, 30) if shas else None
        for ln in (sh or "").splitlines():
            h, _, d = ln.partition("\t")
            dates[h] = d[:10]
        items = []
        for w in wts:
            path = Path(w["path"])
            sha = w.get("head")
            it: dict = {"path": str(path), "exists": path.exists(), "is_main": path == main, "branch": w.get("branch"),
                        "head": sha[:12] if sha else None, "detached": bool(w.get("detached")), "bare": bool(w.get("bare")),
                        "locked": w.get("locked", False), "prunable": w.get("prunable", False), "tip_date": dates.get(sha or ""),
                        "claude_managed": "/.claude/worktrees/" in path.as_posix(),
                        "under_target": any(t == path or t in path.parents for t in targets)}
            gitp = path / ".git"
            if gitp.is_file():
                try:
                    it["created_approx"] = iso(gitp.stat().st_mtime)[:10]
                except OSError:
                    pass
            if sha and not it["is_main"] and default_refs:
                merged_into, rcs = None, []
                for r in default_refs:
                    rr = git(["merge-base", "--is-ancestor", sha, r], repo_cwd, 10)
                    rcs.append(rr.returncode if rr is not None else None)
                    if rr is not None and rr.returncode == 0:
                        merged_into = r
                        break
                it["merged"] = True if merged_into else (False if 1 in rcs else None)   # None: git could not tell
                it["merged_into"] = merged_into
                if it["merged"] is False:
                    n = git_out(["rev-list", "--count", f"{default_refs[0]}..{sha}"], repo_cwd, 10)
                    it["ahead_of_default"] = int(n) if n and n.strip().isdigit() else None
            items.append(it)

        def heavy(it: dict):
            p = Path(it["path"])
            if not it["exists"]:
                return it, None, None, []
            left = deadline - time.monotonic()
            # the main checkout is not sized: du would also count every worktree nested inside it
            size = du_kb(p, min(12.0, left)) if left > 0 and not it["is_main"] else None
            deps = find_deps(p, main)
            dirty = None
            if time.monotonic() < deadline + 8 and not it["bare"]:
                so = git_out(["status", "--porcelain"], p, 10)
                dirty = len(so.splitlines()) if so is not None else None
            return it, size, dirty, deps

        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            for it, size, dirty, deps in ex.map(heavy, items):
                it["size_kb"], it["dirty_files"], it["deps"] = size, dirty, deps
        skipped = sum(1 for it in items if it["exists"] and not it["is_main"] and it["size_kb"] is None)
        if skipped:
            cnr(main, f"du skipped for {skipped} worktrees (time cap {du_budget:.0f}s or timeout)")
        linked = [i for i in items if not i["is_main"]]
        summary = {
            "linked": len(linked), "merged": sum(1 for i in linked if i.get("merged")),
            "unmerged": sum(1 for i in linked if i.get("merged") is False), "merged_unknown": sum(1 for i in linked if i.get("merged") is None), "locked": sum(1 for i in linked if i["locked"]),
            "prunable": sum(1 for i in linked if i["prunable"]), "detached": sum(1 for i in linked if i["detached"]),
            "merged_with_dirty_files": sum(1 for i in linked if i.get("merged") and (i.get("dirty_files") or 0) > 0),
            "total_kb": sum(i["size_kb"] or 0 for i in linked), "size_unknown": skipped,
            "own_node_modules": sum(1 for i in linked if any(d["kind"] == "node_modules" and d["link"] == "dir" for d in i["deps"])),
            "linked_node_modules": sum(1 for i in linked if any(d["kind"] == "node_modules" and d["link"] == "symlink" for d in i["deps"])),
            "own_venv": sum(1 for i in linked if any(d["kind"] == ".venv" and d["link"] == "dir" for d in i["deps"])),
            "linked_venv": sum(1 for i in linked if any(d["kind"] == ".venv" and d["link"] == "symlink" for d in i["deps"])),
            "dirty": sum(1 for i in linked if (i.get("dirty_files") or 0) > 0),
        }
        repos.append({"repo": str(main), "common_dir": common, "default_refs": default_refs, "summary": summary, "items": items})
    return repos


# ---------------------------------------------------------------- launchers

LAUNCH_QUICK = re.compile(r"claude|anthropic|codex|gemini|cursor-agent|aider|opencode", re.I)
CLAUDE_PRINT = re.compile(r"(?<![\w-])claude(?:\.exe)?\b[^\n|;&]{0,200}?(?<![\w-])(?:-p|--print)(?![\w-])")
CLAUDE_SPAWN = re.compile(r"""["']claude(?:\.exe)?["']\s*[,\]]""")
SDK_IMPORT = re.compile(r"""(?:^|\s)(?:from|import)\s+claude_agent_sdk\b|['"]@anthropic-ai/claude-(?:agent-sdk|code)['"]""")
API_IMPORT = re.compile(r"""(?:^|\s)(?:from|import)\s+anthropic\b|['"]@anthropic-ai/sdk['"]""")
OTHER_CLI = re.compile(r"(?<![\w-])(?:codex\s+exec|gemini\s+(?:-p|--prompt)|cursor-agent\b|aider\s+--message|opencode\s+run)(?![\w-])")
QUERY_CALL = re.compile(r"\b(?:query|ClaudeSDKClient|unstable_v2_prompt)\s*\(")
FLAGS = ["--dangerously-skip-permissions", "--allowedTools", "--allowed-tools", "--max-turns", "--max-budget-usd", "--output-format",
         "--permission-mode", "--agent", "--resume", "--continue", "--model", "--append-system-prompt", "--mcp-config", "--plugin-dir", "--bare",
         "--no-session-persistence"]
KEEPS_NO_LOG = "--no-session-persistence"   # Claude Code writes no session log for a `claude -p` run with this flag


_COMMENT = re.compile(r"^\s*(?://|#|\*|/\*|--|<!--|;)")
SCHEDULER = re.compile(r"(?<![\w./-])(?:crontab|launchctl|schtasks|systemd-run)(?![\w-])")


def logical_lines(text: str, max_joined: int = 40) -> list[tuple[int, str]]:
    """(0-based number of the first physical line, the line) with shell backslash continuations joined, so the flags a
    `claude -p` call carries on the lines after it are read with it (11-06: a launch script's flags sat on later lines and
    were all missed). A comment line is never continued."""
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 0
    for i, ln in enumerate(text.splitlines()):
        if not buf:
            start = i
        cont = ln.rstrip().endswith("\\") and not _COMMENT.match(buf[0] if buf else ln) and len(buf) < max_joined
        if cont:
            buf.append(ln.rstrip()[:-1].strip() if buf else ln.rstrip()[:-1].rstrip())
            continue
        buf.append(ln.strip() if buf else ln)
        out.append((start, " ".join(buf)))
        buf = []
    if buf:
        out.append((start, " ".join(buf)))
    return out


def script_keeps_no_log(text: str) -> bool:
    """Some `claude -p` call in this script (code, not a comment) carries --no-session-persistence."""
    return any(CLAUDE_PRINT.search(ln) and KEEPS_NO_LOG in ln and not _COMMENT.match(ln) for _, ln in logical_lines(text) if len(ln) <= 3000)


def scan_launch_text(text: str) -> tuple[list[dict], dict]:
    """Lines that start an agent from code. Returns (hits, counts-by-kind); counts only code lines, comment mentions are
    kept apart (kind + '_in_comment') so a docstring saying 'claude -p' does not look like a launcher."""
    if not (LAUNCH_QUICK.search(text) or SCHEDULER.search(text)):
        return [], {}
    code: dict[str, list[dict]] = defaultdict(list)
    comment: dict[str, list[dict]] = defaultdict(list)
    counts: Counter = Counter()
    has_sdk = bool(SDK_IMPORT.search(text))
    quoted_print = bool(re.search(r"""["'](?:-p|--print)["']""", text))

    def add(kind: str, i: int, line: str, flags: list[str] | None = None):
        is_c = bool(_COMMENT.match(line))
        counts[kind + ("_in_comment" if is_c else "")] += 1
        bucket = comment if is_c else code
        if len(bucket[kind]) < 3:
            bucket[kind].append({"kind": kind, "line": i + 1, "text": scrub(line.strip(), 200), "flags": flags or [], "in_comment": is_c})

    for i, line in logical_lines(text):
        if len(line) > 3000:
            continue
        if CLAUDE_PRINT.search(line):
            add("claude_cli_print", i, line, [f for f in FLAGS if f in line])
        elif quoted_print and CLAUDE_SPAWN.search(line):
            add("claude_cli_print", i, line, ["split-args"])
        if SDK_IMPORT.search(line):
            add("claude_agent_sdk_import", i, line)
        elif API_IMPORT.search(line):
            add("anthropic_api_import", i, line)
        if OTHER_CLI.search(line):
            add("other_agent_cli", i, line)
        if has_sdk and QUERY_CALL.search(line) and not re.match(r"\s*(?:def|async def|function|const|let|var|export)\s+query\b", line):
            add("sdk_query_call", i, line)
        if SCHEDULER.search(line) and re.search(r"(?i)claude|cron|schedul|launchd|spawn|exec|run|install", line) and not line.lstrip().startswith(("echo", "man ")):
            add("scheduler_install", i, line)
    hits = []
    for kind in list(code) + [k for k in comment if k not in code]:
        hits += code[kind] or comment[kind][:1]
    return hits, dict(counts)


def scan_script(p: Path) -> tuple[list[dict], dict]:
    text = read_text(p, note=False)
    return scan_launch_text(text) if text else ([], {})


def cron_entries(targets: list[Path]) -> dict:
    if not shutil.which("crontab"):
        return {"available": False, "entries": [], "note": "crontab not installed"}
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"available": False, "entries": [], "note": str(e)[:80]}
    if r.returncode != 0:
        return {"available": True, "entries": [], "note": (r.stderr or "no crontab").strip()[:80]}
    entries = []
    for ln in r.stdout.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", s):
            continue
        if s.startswith("@"):
            sched, _, cmd = s.partition(" ")
        else:
            parts = s.split(None, 5)
            if len(parts) < 6:
                continue
            sched, cmd = " ".join(parts[:5]), parts[5]
        sp, exists, _ = script_from_command(cmd, None, None)
        rec = {"schedule": sched, "command": scrub(cmd, 200), "mentions_claude": bool(re.search(r"(?i)claude|anthropic", cmd)),
               "script_path": sp, "script_exists": exists, "in_target": bool(sp and any(t in Path(sp).parents for t in targets))}
        rec["keeps_no_log"] = bool(CLAUDE_PRINT.search(cmd) and KEEPS_NO_LOG in cmd)
        rec["runs_seen"] = rec["last_exit"] = rec["seen_by"] = None   # cron keeps no run counter
        if sp and exists:
            rec["keeps_no_log"] = rec["keeps_no_log"] or script_keeps_no_log(read_text(Path(sp), note=False) or "")
            hits, counts = scan_script(Path(sp))
            rec["script_launch_kinds"], rec["script_hits"] = sorted(k for k in counts if not k.endswith("_in_comment")), hits[:3]
            rec["script_mentions_claude"] = bool(re.search(r"(?i)claude|anthropic", read_text(Path(sp), note=False) or ""))
        entries.append(rec)
    return {"available": True, "entries": entries}


def launchctl_state(label: str) -> dict | None:
    """What launchd itself says about a loaded user agent (macOS, read-only `launchctl print gui/<uid>/<label>`): how many
    times it has run since it was loaded, and how the last run ended. None when launchctl is missing or the label isn't loaded."""
    if sys.platform != "darwin" or not label or not shutil.which("launchctl"):
        return None
    try:
        r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    runs = re.search(r"^\s*runs = (\d+)\s*$", r.stdout, re.M)
    last = re.search(r"^\s*last exit code = (\(never exited\)|-?\d+)", r.stdout, re.M)
    if not runs:
        return None
    return {"runs": int(runs.group(1)), "last_exit": None if not last or last.group(1).startswith("(") else int(last.group(1))}


def launchd_entries(targets: list[Path]) -> dict:
    """User launch agents that mention claude/anthropic, whose program file does, or that run something inside a project."""
    d = HOME / "Library" / "LaunchAgents"
    out: dict = {"dir": str(d), "matching": [], "other_count": 0, "other": []}
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.plist")):
        try:
            with open(f, "rb") as fh:
                pl = plistlib.load(fh)
        except Exception as e:  # noqa: BLE001 - plist libraries raise many types
            cnr(f, f"plist unreadable: {str(e)[:60]}")
            continue
        args = [str(a) for a in (pl.get("ProgramArguments") or [])] + ([str(pl["Program"])] if pl.get("Program") else [])
        blob = " ".join(args + [str(pl.get("Label", "")), str(pl.get("WorkingDirectory", ""))])
        launch_kinds: list[str] = []
        script_hits: list[dict] = []
        script_mentions = False
        keeps_no_log = any(KEEPS_NO_LOG == a or (CLAUDE_PRINT.search(a) and KEEPS_NO_LOG in a) for a in args)
        in_target = any(str(t) in blob for t in targets)
        for a in args:
            sp, exists, _ = script_from_command(a, None, None)
            if sp and exists and Path(sp).is_file():
                text = read_text(Path(sp), note=False)
                if text:
                    script_mentions = script_mentions or bool(re.search(r"(?i)claude|anthropic", text))
                    keeps_no_log = keeps_no_log or script_keeps_no_log(text)
                    h, c = scan_launch_text(text)
                    launch_kinds += [k for k in c if not k.endswith("_in_comment")]
                    script_hits += h[:2]
        if not (re.search(r"(?i)claude|anthropic", blob) or launch_kinds or script_mentions or in_target):
            out["other_count"] += 1
            prog = next((x for x in (script_from_command(a, None, None)[0] for a in args) if x), args[0] if args else None)
            out["other"].append({"label": str(pl.get("Label", f.stem)), "program": scrub(prog, 140) if prog else None})
            continue
        env = pl.get("EnvironmentVariables")
        # Evidence the job ran, without session logs (a run that keeps none leaves no record Claude Code wrote): launchd's own
        # run counter for the loaded agent, and when its output files were last written
        state = launchctl_state(str(pl.get("Label", "")))
        out_files = [Path(str(pl[k])) for k in ("StandardOutPath", "StandardErrorPath") if pl.get(k)]
        written = [f.stat().st_mtime for f in out_files if f.is_file()]
        out["matching"].append({
            "keeps_no_log": keeps_no_log,
            "runs_seen": state["runs"] if state else None, "last_exit": state["last_exit"] if state else None,
            "seen_by": "launchctl print (runs since the agent was loaded)" if state else None,
            "output_last_written": iso(max(written)) if written else None,
            "path": str(f), "label": pl.get("Label"), "program_arguments": [scrub(a, 140) for a in args[:8]],
            "run_at_load": pl.get("RunAtLoad"), "start_interval": pl.get("StartInterval"),
            "has_calendar_interval": "StartCalendarInterval" in pl, "keep_alive": bool(pl.get("KeepAlive")),
            "working_directory": pl.get("WorkingDirectory"), "env": secret_names(env) if isinstance(env, dict) else [],
            "in_target": in_target, "script_mentions_claude": script_mentions,
            "script_launch_kinds": sorted(set(launch_kinds)), "script_hits": script_hits[:3]})
    out["other"] = out["other"][:60]   # label + program of every other agent: the reader can spot one that wraps an AI tool
    return out


def systemd_entries() -> list[dict]:
    d = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "systemd" / "user"
    out = []
    if d.is_dir():
        for f in sorted(d.glob("*.service")) + sorted(d.glob("*.timer")):
            text = read_text(f)
            if text and re.search(r"(?i)claude|anthropic", text):
                m = [scrub(ln, 160) for ln in text.splitlines() if re.search(r"(?i)claude|anthropic|ExecStart|OnCalendar", ln)][:4]
                out.append({"path": str(f), "lines": m})
    return out


def ci_entry(p: Path) -> dict | None:
    text = read_text(p)
    if not text:
        return None
    mentions = [{"line": i + 1, "text": scrub(ln.strip(), 160)} for i, ln in enumerate(text.splitlines())
                if re.search(r"(?i)claude|anthropic", ln) and len(ln) < 2000][:6]
    hits, counts = scan_launch_text(text)
    if not mentions and not counts:
        return None
    return {"path": str(p), "kinds": sorted(counts) or ["mentions"], "mentions": mentions}


# ---------------------------------------------------------------- scaffolding: how the project runs work

_NOTABLE = [
    ("worktree", r"worktrees?\b"),
    ("tmp", r"\btmp\b|/tmp\b|tmpdir|mkdtemp|mktemp|tempdir|temp dir|\btemp\b"),
    ("port", r"\bports?\b|--port|\bPORT\b"),
    ("profile", r"\bprofiles?\b|user-data-dir"),
    ("clone", r"\bclones?\b|clonefile|reflink|FICLONE|\bAPFS\b"),
    ("cp -c", r"\bcp\s+-[A-Za-z]*[cR]|cpSync|copyFile|\brsync\b"),
    ("symlink", r"symlink|\bln\s+-s"),
    ("node_modules", r"node_modules|\.venv\b|virtualenv"),
    ("parallel", r"parallel|concurren|xargs\s+-P|\bworkers?\b|Promise\.all|p-limit|slowest first|side by side|\bat a time\b|\bat once\b|\bup to \d+\b"),
    ("cleanup", r"clean ?up|teardown|afterAll|afterEach|rm\s+-rf|rmSync|\btrap\b|\bfinally\b"),
    ("isolation", r"isolat|sandbox|hermetic|XDG_[A-Z_]+|BRAIN_HOME|\bHOME\b"),
    ("merge/filter", r"\bmerge=|\bfilter=|union merge"),
    ("hooks", r"core\.hooksPath|\bhooksPath\b|husky"),
    ("container", r"^\s*(?:image|ports|volumes|build|services|FROM|forwardPorts|postCreateCommand)\b"),
]
_NOTABLE_RX = [(n, re.compile(rx, re.I)) for n, rx in _NOTABLE]


def notable_lines(text: str, cap: int = 8, kind: str = "") -> list[dict]:
    """Up to `cap` lines that say how work is isolated, copied, ported or cleaned: first a line for each distinct topic, then fill."""
    lines = text.splitlines()
    scored = []
    for i, ln in enumerate(lines):
        if not ln.strip() or len(ln) > 500 or re.match(r"\s*(?:import\b|from\s+\S+\s+import\b|export\s.*\sfrom\b)", ln):
            continue
        topics = [n for n, rx in _NOTABLE_RX if rx.search(ln) and (n != "container" or kind == "container")]
        if topics:
            scored.append((i, topics))
    picked: list[int] = []
    seen: set[str] = set()
    for i, topics in scored:
        if len(picked) < cap and set(topics) - seen:
            picked.append(i)
            seen |= set(topics)
    for i, _ in scored:
        if len(picked) >= cap:
            break
        if i not in picked:
            picked.append(i)
    return [{"line": i + 1, "text": scrub(lines[i].strip(), 160), "topics": [n for n, rx in _NOTABLE_RX if rx.search(lines[i]) and (n != "container" or kind == "container")]} for i in sorted(picked)]


def task_entries(kind: str, text: str, path: Path) -> list[dict]:
    """[{name, text}]: each recipe/target/script with its doc comment, else its first command (120 chars)."""
    out: list[dict] = []
    lines = text.splitlines()
    if kind == "package_json":
        try:
            sc = json.loads(text).get("scripts")
        except (ValueError, AttributeError):
            return []
        return [{"name": str(k), "text": scrub(v, 120)} for k, v in sc.items()] if isinstance(sc, dict) else []
    if kind == "procfile":
        return [{"name": m.group(1), "text": scrub(m.group(2), 120)} for m in (re.match(r"^([\w-]+):\s*(.+)$", ln) for ln in lines) if m]
    if kind in ("justfile", "makefile"):
        head = re.compile(r"^(@?[A-Za-z_][\w-]*)\b[^\n#]*?:(?!=)") if kind == "justfile" else re.compile(r"^([A-Za-z0-9_][\w./-]*)\s*:(?![=])")
        for i, ln in enumerate(lines):
            m = head.match(ln)
            if not m or ln[:1] in " \t" or re.match(r"(?:set|export|alias|import|mod|unexport)\s", ln) or re.match(r"@?[\w-]+\s*:?=", ln):
                continue
            doc, j = [], i - 1
            while j >= 0 and (lines[j].lstrip().startswith("#") or lines[j].lstrip().startswith("[")):
                if lines[j].lstrip().startswith("#"):
                    doc.insert(0, lines[j].lstrip("# ").rstrip())
                j -= 1
            first = next((x.strip() for x in lines[i + 1:i + 8] if x[:1] in " \t" and x.strip() and not x.strip().startswith("#!")), "")
            out.append({"name": m.group(1).lstrip("@"), "text": scrub(" ".join(doc) or first, 120)})
        return out
    if kind == "taskfile":
        in_tasks = False
        for i, ln in enumerate(lines):
            if re.match(r"^tasks:\s*$", ln):
                in_tasks = True
            elif in_tasks and re.match(r"^\S", ln):
                in_tasks = False
            m = re.match(r"^  ([\w:.-]+):\s*$", ln) if in_tasks else None
            if m:
                d = next((re.sub(r"^\s*desc:\s*", "", x).strip("\"' ") for x in lines[i + 1:i + 8] if re.match(r"^\s+desc:", x)), "")
                out.append({"name": m.group(1), "text": scrub(d, 120)})
    return out


def scaffold_record(p: Path, kind: str, project: Path | None) -> dict | None:
    text = read_text(p)
    if text is None:
        return None
    rec: dict = {"path": str(p), "kind": kind, "project": str(project) if project else None, "bytes": len(text.encode("utf-8", "replace")),
                 "lines": count_lines(text)}
    if kind == "git_attributes":
        rules = []
        raw = text.splitlines()
        for i, ln in enumerate(raw):
            if re.search(r"\b(?:merge|filter)=", ln):
                cm, j = [], i - 1
                while j >= 0 and raw[j].lstrip().startswith("#") and len(cm) < 3:
                    cm.insert(0, raw[j].lstrip("# ").rstrip())
                    j -= 1
                rules.append({"line": i + 1, "text": scrub(ln.strip(), 160), "comment": scrub(" ".join(cm), 200)})
        if not rules:
            return None
        rec["rules"] = rules[:12]
        return rec
    if kind.startswith("task_runner:"):
        sub = kind.split(":")[1]
        ents = task_entries(sub, text, p)
        if sub == "package_json" and not ents:
            return None
        rec["kind"], rec["runner"] = "task_runner", sub
        rec["entries_total"], rec["entries"] = len(ents), ents[:40]
    rec["notable"] = notable_lines(text, 8, kind)
    if kind == "test_readme" or p.suffix.lower() in (".md", ".mdx", ".rst"):
        h = re.search(r"(?m)^#\s+(.+)$", text)
        rec["title"] = scrub(h.group(1), 100) if h else None
    return rec


_DOC_NAME = re.compile(r"(?i)^(?:readme|architecture|engine|operations|intake|contributing|releasing|versioning)[^/]*\.(?:md|mdx|rst|txt)$")


def collect_scaffolding(targets: list[Path], walks: dict[Path, Walk], instr: list[dict]) -> dict:
    cap = 40
    out: dict[str, list[dict]] = {"test_harness": [], "task_runners": [], "git_attributes": [], "git_hooks": [], "containers": [],
                                  "docs": [], "test_dirs": []}
    totals: dict[str, int] = {}
    seen: set[str] = set()
    for t, w in walks.items():
        for p, kind in sorted(w.scaffold, key=lambda x: (len(x[0].parts), str(x[0]))):
            if str(p) in seen:
                continue
            seen.add(str(p))
            cat = {"test_config": "test_harness", "test_setup": "test_harness", "test_runner": "test_harness", "test_readme": "test_harness",
                   "git_attributes": "git_attributes", "git_hook": "git_hooks", "container": "containers"}.get(kind, "task_runners")
            rec = scaffold_record(p, kind, t)
            if rec is None:
                continue
            rec["in_fixture_path"] = bool(FIXTURE_RE.search(os.path.relpath(p, t).replace(os.sep, "/")))
            totals[cat] = totals.get(cat, 0) + 1
            out[cat].append(rec)
        for d in w.test_dirs:
            if str(d) in seen:
                continue
            seen.add(str(d))
            try:
                ents = sorted(os.scandir(d), key=lambda e: e.name)
            except OSError:
                continue
            out["test_dirs"].append({"path": str(d), "project": str(t), "files": sum(1 for e in ents if e.is_file()),
                                     "subdirs": [e.name for e in ents if e.is_dir()][:12],
                                     "in_fixture_path": bool(FIXTURE_RE.search(os.path.relpath(d, t).replace(os.sep, "/")))})
    # git hooks that live in the repo's own hooks folder (or core.hooksPath), per repo
    repos = {Path(r["repo"]) for r in out.get("_worktree_repos", [])}
    for t, w in walks.items():
        repos |= {g for g in [git_root(t), *w.git_repos] if g is not None}
    for root in sorted(repos)[:25]:
        if not (root / ".git").is_dir() or not GIT:
            continue
        hp = (git_out(["config", "--get", "core.hooksPath"], root, 5) or "").strip()
        hooks_dir = (root / hp) if hp and not os.path.isabs(hp) else Path(hp) if hp else root / ".git" / "hooks"
        try:
            real = [e for e in sorted(os.scandir(hooks_dir), key=lambda e: e.name) if e.is_file() and not e.name.endswith(".sample")]
        except OSError:
            real = []
        if hp or real:
            out["git_hooks"].append({"path": str(hooks_dir), "kind": "git_hooks_dir", "repo": str(root), "core_hooksPath": hp or None,
                                     "hooks": [{"name": e.name, "bytes": e.stat().st_size} for e in real][:20], "bytes": None})
    # docs that instruction files point at (and README/ARCHITECTURE/ENGINE siblings of an instruction file)
    docs: dict[str, dict] = {}
    instr_paths = {r["path"] for r in instr}
    for r in instr:
        if r.get("in_fixture_path"):
            continue
        for ptr in r.get("pointers", []):
            q = Path(ptr["resolved"])
            if ptr["kind"] == "file" and q.suffix.lower() in (".md", ".mdx", ".rst", ".txt") and str(q) not in instr_paths:
                d = docs.setdefault(str(q), {"path": str(q), "pointed_at_by": [], "named": bool(_DOC_NAME.match(q.name))})
                if r["path"] not in d["pointed_at_by"]:
                    d["pointed_at_by"].append(r["path"])
        dirp = Path(r["path"]).parent
        if r["scope"] in ("home", "ancestor"):
            continue
        try:
            for e in os.scandir(dirp):
                if e.is_file() and _DOC_NAME.match(e.name) and str(Path(e.path)) not in docs:
                    docs[str(Path(e.path))] = {"path": str(Path(e.path)), "pointed_at_by": [], "sibling_of": r["path"], "named": True}
        except OSError:
            pass
    for t in targets:
        try:
            for e in os.scandir(t):
                if e.is_file() and _DOC_NAME.match(e.name) and e.path not in docs:
                    docs[e.path] = {"path": e.path, "pointed_at_by": [], "sibling_of": str(t), "named": True}
        except OSError:
            pass
    ranked = sorted(docs.values(), key=lambda d: (not d["named"], not d["pointed_at_by"], -len(d["pointed_at_by"]), d["path"]))
    totals["docs"] = len(ranked)
    for d in ranked[:cap]:
        q = Path(d["path"])
        text = read_text(q)
        if not text:
            continue
        d["pointed_at_by"] = d["pointed_at_by"][:4]
        d["bytes"], d["lines"] = len(text.encode("utf-8", "replace")), count_lines(text)
        h = re.search(r"(?m)^#\s+(.+)$", text)
        d["title"] = scrub(h.group(1), 100) if h else None
        d["notable"] = notable_lines(text, 6)
        out["docs"].append(d)
    for cat, items in list(out.items()):
        if cat != "docs" and len(items) > cap:
            totals[cat] = max(totals.get(cat, 0), len(items))
            out[cat] = items[:cap]
    out["totals_found"] = totals  # type: ignore[assignment]
    return out


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("projects", nargs="*", help="project directories to inventory (the home config is always included)")
    ap.add_argument("--out", help="output JSON file")
    ap.add_argument("--du-budget", type=float, default=20.0, help="seconds allowed for worktree size/status (default 20)")
    ap.add_argument("--written-dirs", help="JSON list of folders their agents wrote a file in (Write/Edit in the logs); "
                    "a skill folder they wrote is theirs, not an unpacked install")
    ap.add_argument("--check-file", help="only run the secret self-check on an existing JSON file")
    ap.add_argument("--start-folder", action="append", default=[],
                    help="a folder a session started in (repeatable); only used to name the folder a Claude Code memory store belongs to")
    args = ap.parse_args(argv)

    if args.check_file:
        bad = self_check(json.loads(Path(args.check_file).read_text()))
        print("clean" if not bad else "\n".join(bad))
        return 2 if bad else 0
    if not args.out:
        ap.error("--out is required")
    global WRITTEN_DIRS
    if args.written_dirs:
        try:
            WRITTEN_DIRS = json.loads(Path(args.written_dirs).read_text())
        except (OSError, ValueError):
            pass   # no logs for this run: a skill the person's agent wrote may read as an unpacked install

    targets: list[Path] = []
    for t in args.projects:
        p = Path(t).expanduser()
        if not p.is_dir():
            cnr(t, "target is not a directory")
            continue
        targets.append(p.resolve())

    # 1. who the user is (global git config), then walk each target once and drop cloned third-party repos
    with section("identity"):
        identity_from_global_config()
    walks: dict[Path, Walk] = {}
    with section("walk"):
        for t in targets:
            walks[t] = walk_target(t)
            prune_nested(t, walks[t])

    # 2. identity from the repos themselves (git evaluates includeIf there)
    with section("identity"):
        repo_roots = []
        for t in targets:
            gr = git_root(t)
            repo_roots += ([gr] if gr else []) + walks[t].git_repos
        identity_from_repos(list(dict.fromkeys(repo_roots)))

    # 3. settings files, hooks, claude.json
    claude_dirs: list[tuple[Path, str, Path | None]] = [(HOME / ".claude", "home", None)]
    for t in targets:
        claude_dirs += [(c, "project", t) for c in walks[t].claude_dirs]
        for a in t.parents:  # a .claude folder in a folder above the project also applies
            if (a / ".claude").is_dir() and a != HOME:
                claude_dirs.append((a / ".claude", "ancestor", None))
    seen_cd: set[str] = set()
    claude_dirs = [x for x in claude_dirs if not (str(x[0]) in seen_cd or seen_cd.add(str(x[0])))]
    with section("settings"):
        for cdir, scope, proj in claude_dirs:
            for name in ("settings.json", "settings.local.json"):
                f = cdir / name
                if f.is_file():
                    process_settings(f, scope, proj)
            try:
                for e in os.scandir(cdir):
                    if e.name.startswith("settings") and e.name not in ("settings.json", "settings.local.json") and e.is_file():
                        S.add("settings_backups", {"path": e.path, "mtime": iso(e.stat().st_mtime), "bytes": e.stat().st_size})
            except OSError:
                pass
        for managed in (Path("/Library/Application Support/ClaudeCode/managed-settings.json"), Path("/etc/claude-code/managed-settings.json"),
                        Path(os.environ.get("ProgramData", "C:/ProgramData")) / "ClaudeCode" / "managed-settings.json"):
            if managed.is_file():
                process_settings(managed, "managed", None)
        read_claude_json(targets)

    # 4. lock files -> skill origin
    lock_files = [HOME / ".agents" / ".skill-lock.json", HOME / ".claude" / "skills-lock.json"] + [f for w in walks.values() for f in w.locks]
    load_locks([f for f in lock_files if f.is_file()])
    global RECEIPTS
    RECEIPTS = OWNERSHIP.load_receipts(roots=[str(f.parent) for w in walks.values() for f in w.locks]
                                       + [str(p) for _, _, p in claude_dirs if p])

    # 5. artifacts
    with section("artifacts"):
        for cdir, scope, proj in claude_dirs:
            scan_claude_dir(cdir, scope, proj)
        for t, w in walks.items():
            in_claude = [str(c) for c in w.claude_dirs]
            for sd in w.skills:  # skills outside .claude/skills (plugins, .agents/skills, ...)
                if not any(str(sd).startswith(c + os.sep) for c in in_claude):
                    S.add_artifact(make_artifact("skill", sd, "project", t, sd / "SKILL.md"))
            for pr in w.plugin_roots:
                mf = read_json(pr / ".claude-plugin" / "plugin.json")
                a = make_artifact("plugin_manifest", pr / ".claude-plugin" / "plugin.json", "project", t, name=(mf or {}).get("name", pr.name))
                a["version"] = (mf or {}).get("version")
                S.add_artifact(a)
                S.lists["project_plugin_roots"].append((pr, f"inside project {t.name}", t))
                for sub in ("agents",):
                    if (pr / sub).is_dir():
                        for f in sorted((pr / sub).glob("*.md")):
                            S.add_artifact(make_artifact("agent", f, "project", t))
                if (pr / "commands").is_dir():
                    for f in sorted((pr / "commands").rglob("*.md")):
                        S.add_artifact(make_artifact("command", f, "project", t, name=f.relative_to(pr / "commands").with_suffix("").as_posix()))
            for mf in w.marketplace_files:
                S.add_artifact(make_artifact("marketplace_manifest", mf, "project", t, name=mf.parent.parent.name))
        # the plugins they load from a folder of their own (a "directory" marketplace, --plugin-dir, a clone of their
        # GitHub marketplace): list what each holds from that folder, so their own agents, skills, commands and hook
        # scripts count wherever the folder sits (11-05: a builder's agent in his own marketplace vanished once the
        # scan stopped reading his kit folder by name). Whose each file is: ownership.py, like every other artifact.
        seen = {os.path.realpath(a["path"]) for a in S.lists["artifacts"] if a.get("path")}
        for src in sorted({v for v in (RECEIPTS.source_dirs.values() if RECEIPTS else []) if v and os.path.isdir(v)}):
            pr = Path(src)
            if not (pr / ".claude-plugin" / "plugin.json").is_file() and not any((pr / d).is_dir() for d in ("agents", "skills", "commands")):
                continue

            def add(kind, f, **kw):
                if os.path.realpath(f) not in seen:
                    seen.add(os.path.realpath(f))
                    S.add_artifact(make_artifact(kind, f, "plugin", None, **kw))
            for f in sorted((pr / "agents").glob("*.md")) if (pr / "agents").is_dir() else []:
                add("agent", f)
            for sk in sorted((pr / "skills").iterdir()) if (pr / "skills").is_dir() else []:
                if (sk / "SKILL.md").is_file():
                    add("skill", sk, entry=sk / "SKILL.md")
            for f in sorted((pr / "commands").rglob("*.md")) if (pr / "commands").is_dir() else []:
                add("command", f, name=f.relative_to(pr / "commands").with_suffix("").as_posix())
            for f in sorted((pr / "hooks").rglob("*")) if (pr / "hooks").is_dir() else []:
                if f.is_file() and f.suffix in (".sh", ".py", ".js", ".mjs", ".ts"):
                    add("hook_script", f)
        S.lists["plugin_copies_dropped"].append({"count": dedupe_plugin_copies(S.lists["artifacts"])})
        fm_hooks = [(a["name"], a["_fm_hooks"]) for a in S.lists["artifacts"] if a.get("_fm_hooks")]
        for a in S.lists["artifacts"]:
            if a["kind"] == "hook_script":
                by = [n for n, tx in fm_hooks if os.path.basename(a["path"]) in tx]
                a["wired"] = a["_real"] in HOOK_SCRIPTS or bool(by)
                a["wired_by"] = "settings hook" if a["_real"] in HOOK_SCRIPTS else (f"frontmatter hooks of {by[0]}" if by else None)
            a.pop("_real", None)
            a.pop("_fm_hooks", None)

    # 6. instruction files: home, ancestors, project tree
    with section("instruction_files"):
        cand: list[dict] = list(S.lists["instruction_candidates"])
        home_list = [HOME / "CLAUDE.md", HOME / "AGENTS.md", HOME / "GEMINI.md", HOME / ".codex" / "AGENTS.md", HOME / ".gemini" / "GEMINI.md",
                     HOME / ".config" / "AGENTS.md", HOME / ".config" / "opencode" / "AGENTS.md", HOME / ".claude" / "CLAUDE.md"]
        for f in home_list:
            k = instruction_kind(f.name, f.parent.name, f.parent.parent.name)
            if f.is_file() and k:
                cand.append({"path": f, "kind": k, "scope": "home", "project": None})
        for rd in (HOME / ".cursor" / "rules", HOME / ".claude" / "rules"):
            if rd.is_dir():
                for f in sorted(rd.rglob("*")):
                    if f.is_file() and f.suffix in (".md", ".mdc"):
                        cand.append({"path": f, "kind": "cursor" if "cursor" in str(rd) else "claude_rules", "scope": "home", "project": None})
        above: list[dict] = []   # files in folders above a target; a folder that is a target itself claims its files as the project's first
        for t in sorted(targets, key=lambda t: -len(t.parts)):   # a repo before the folder above it: its files count as the project's own
            for a in t.parents:
                for n in os.listdir(a) if os.access(a, os.R_OK) else []:
                    k = INSTR_NAMES.get(n.lower())
                    if k and (a / n).is_file() and n.lower() != ".clinerules":
                        above.append({"path": a / n, "kind": k, "scope": "ancestor", "project": None})
            for p, kind in walks[t].instructions:
                cand.append({"path": p, "kind": kind, "scope": instruction_scope(p, t), "project": t})
        cand += above
        order = {"home": 0, "ancestor": 1, "project": 2, "folder": 3, "managed": 0}
        seen_p: set[str] = set()
        for c in cand:
            key = str(c["path"])
            if key in seen_p:
                continue
            seen_p.add(key)
            S.lists["instruction_files"].append(instruction_record(c["path"], c["kind"], c["scope"], c["project"]))
        S.lists["instruction_files"].sort(key=lambda r: (order.get(r["scope"], 9), r["project"] or "", r["path"]))

    # 7. plugins + marketplaces (also adds plugin hooks and plugin MCP servers)
    with section("plugins"):
        plugins = collect_plugins(targets)

    # 8. mcp files in the projects
    with section("mcp"):
        for t, w in walks.items():
            for f in w.mcp_files:
                d = read_json(f)
                if isinstance(d, dict):
                    for rec in mcp_records(d.get("mcpServers", d.get("servers")), str(f), "project", t):
                        S.lists["mcp_servers"].append(rec)
        for f in (HOME / ".cursor" / "mcp.json",):
            d = read_json(f) if f.is_file() else None
            if isinstance(d, dict):
                for rec in mcp_records(d.get("mcpServers"), str(f), "home", None):
                    S.lists["mcp_servers"].append(rec)

    # 9. memory stores
    with section("memory"):
        mem: dict[str, dict] = {}

        def reg(root: Path, kind: str, scope: str, project: Path | None, ref: str | None = None, slug: str | None = None,
                start_folder: Path | None = None):
            rp = os.path.realpath(root)
            if rp in mem:
                if ref and ref not in mem[rp]["referenced_from"]:
                    mem[rp]["referenced_from"].append(ref)
                return
            if not root.is_dir():
                return
            rec = scan_store(root, kind, scope, project, [ref] if ref else [])
            if rec:
                if slug is not None:   # a Claude Code store: the folder it belongs to when the slug could be reversed, else just the slug
                    rec["start_folder"] = str(start_folder) if start_folder else None   # the slug stays in rec["path"]; stored alone it reads as a token to the secret check
                    rec["in_fixture_path"] = rec["in_fixture_path"] or bool(TMP_SLUG.match(slug))
                mem[rp] = rec

        for c in S.lists["memory_candidates"]:
            reg(c["path"], c["kind"], c["scope"], c["project"])
        for t, w in walks.items():
            for md in w.memory_dirs:
                rel = md.relative_to(t).as_posix()
                reg(md, "agent_memory" if re.search(r"(?i)agent|\.agents", rel) else "named_memory", "project", t)
            for v in w.vaults:
                reg(v, "obsidian_vault", "project", t)
            for f in w.memory_md:
                if not any(str(f).startswith(k + os.sep) for k in mem):
                    mem.setdefault(os.path.realpath(f), scan_single_memory_md(f, t))
        # Claude Code's own per-project memory, one folder per start folder: ~/.claude/projects/<slug>/memory. Read from
        # that folder directly, whatever the targets are; the slug only names the start folder it belongs to.
        known: dict[str, Path] = {slug_of(HOME): HOME}
        for p0 in [*map(Path, args.start_folder), *targets, *(a for t in targets for a in t.parents if a != Path(a.anchor))]:
            known.setdefault(slug_of(p0), p0)
        for w in walks.values():
            for p0 in [*w.git_repos, *w.linked_worktrees, *(i.parent for i, _ in w.instructions)]:
                known.setdefault(slug_of(p0), p0)
        t_end = time.monotonic() + 20
        try:
            slugs = sorted(e.name for e in os.scandir(PROJECTS_DIR) if e.is_dir() and (Path(e.path) / "memory").is_dir())
        except OSError:
            slugs = []
        for slug in slugs:
            if time.monotonic() > t_end:
                cnr(PROJECTS_DIR, "Claude Code memory folders: stopped at the time budget")
                break
            folder = known.get(slug)
            reg(PROJECTS_DIR / slug / "memory", "claude_auto_memory", "home" if folder == HOME else "project",
                None if folder == HOME else folder, slug=slug, start_folder=folder)
        # notes vaults named in an instruction file or in WIKILINKS_ROOTS
        refs: list[tuple[Path, str]] = []
        for part in re.split(r"[:;,]", ENV_USE.get("WIKILINKS_ROOTS", "")):
            if part.strip():
                refs.append((Path(os.path.expanduser(part.strip())), "settings env WIKILINKS_ROOTS"))
        for ipath, text in INSTR_TEXT.items():
            for d in referenced_dirs(text, Path(ipath).parent):
                if d.is_dir() and not _under(d, HOME / ".claude" / "projects") and \
                        (VAULT_NAME.match(d.name) or MEM_NAME.search(d.name) or (d / ".obsidian").is_dir()):
                    refs.append((d, ipath))
        for d, how in refs:
            reg(d, "referenced_notes", "home", None, how)
        # who mentions each store
        for rp, rec in mem.items():
            p = Path(rp)
            needles = {str(p), str(p).replace(str(HOME), "~", 1), "/".join(p.parts[-2:])}
            rec["referenced_from"] = sorted(set(rec["referenced_from"]) | {ip for ip, tx in INSTR_TEXT.items() if any(n and n in tx for n in needles)})
        S.lists["memory_stores"] = sorted(mem.values(), key=lambda r: (r["scope"], r["path"]))

    # 10. worktrees
    with section("worktrees"):
        cands = []
        for t in targets:
            cands += [t] + walks[t].git_repos + walks[t].linked_worktrees[:3]
        worktrees = collect_worktrees(list(dict.fromkeys(cands)), targets, args.du_budget)

    # 11. scaffolding: how the projects run work (test harness, task runners, git attributes/hooks, containers, docs)
    with section("scaffolding"):
        scaffolding = collect_scaffolding(targets, walks, S.lists["instruction_files"])

    # 12. launchers
    with section("launchers"):
        launchers: dict = {"crontab": cron_entries(targets), "launchd": launchd_entries(targets), "systemd_user": systemd_entries(),
                           "claude_scheduled_tasks": S.lists["claude_scheduled_tasks"], "ci": [], "scripts": []}
        done: set[str] = set()
        t_end = time.monotonic() + 25
        for t, w in walks.items():
            for f in w.ci:
                e = ci_entry(f)
                if e and e["path"] not in done:
                    done.add(e["path"])
                    e["project"] = str(t)
                    launchers["ci"].append(e)
            for f in w.scripts:
                if str(f) in done:
                    continue
                if time.monotonic() > t_end:
                    cnr(t, "scan of scripts that start headless runs stopped at its time budget")
                    break
                done.add(str(f))
                hits, counts = scan_script(f)
                if counts:
                    launchers["scripts"].append({"path": str(f), "project": str(t), "via": "project script",
                                                 "kinds": sorted(k for k in counts if not k.endswith("_in_comment")),
                                                 "only_in_comments": all(k.endswith("_in_comment") for k in counts),
                                                 "counts": counts, "hits": hits})
        for h in S.lists["hooks"]:
            sp = h.get("script_path")
            if sp and h.get("script_exists") and sp not in done and Path(sp).is_file():
                done.add(sp)
                hits, counts = scan_script(Path(sp))
                if counts:
                    launchers["scripts"].append({"path": sp, "project": h.get("project"), "via": f"hook script ({h['source_kind']}: {h.get('plugin') or h['source']})",
                                                 "kinds": sorted(k for k in counts if not k.endswith("_in_comment")),
                                                 "only_in_comments": all(k.endswith("_in_comment") for k in counts), "counts": counts, "hits": hits})

    launchers["scripts"].sort(key=lambda x: (x["only_in_comments"], -len(x["kinds"]), x["path"]))

    # assemble
    arts = sorted(S.lists["artifacts"], key=lambda a: (a["scope"], a.get("project") or "", a["kind"], a["path"]))
    summary = {
        "instruction_files": dict(Counter(r["scope"] for r in S.lists["instruction_files"])),
        "memory_stores": len(S.lists["memory_stores"]),
        "hooks": dict(Counter(h["event"] for h in S.lists["hooks"])),
        "hooks_missing_script": sum(1 for h in S.lists["hooks"] if h.get("script_exists") is False),
        "artifacts": {f"{k}/{o}": n for (k, o), n in sorted(Counter((a["kind"], a["origin"]) for a in arts).items())},
        "plugin_copies_dropped": sum(x["count"] for x in S.lists.get("plugin_copies_dropped") or []),
        "plugins_enabled": sum(1 for p in plugins["enabled"] if p["enabled"]),
        "mcp_servers": len(S.lists["mcp_servers"]),
        "worktree_repos": [{"repo": r["repo"], **{k: r["summary"][k] for k in ("linked", "merged", "unmerged", "total_kb")}} for r in worktrees],
        "launchers": {"cron_entries": len(launchers["crontab"]["entries"]), "launchd_matching": len(launchers["launchd"]["matching"]),
                      "ci_files": len(launchers["ci"]), "scripts": len(launchers["scripts"])},
        "walk": {str(t): {"dirs": w.dirs, "entries": w.entries, "truncated": w.truncated, "seconds": w.seconds,
                          "instruction_files_suppressed_inside_skills": w.suppressed_in_skill,
                          "items_suppressed_inside_third_party_repos": w.suppressed_by_nested_repos,
                          "nested_repos": {"kept": [r for r in w.nested_repos if r["kept"]],
                                           "third_party_count": sum(1 for r in w.nested_repos if not r["kept"]),
                                           "third_party_sample": [r["path"] for r in w.nested_repos if not r["kept"]][:8]}}
                 for t, w in walks.items()},
    }
    identity = {"emails": sorted(Identity.emails), "names": sorted(Identity.names),
                "sources": {e: sorted(s) for e, s in sorted(Identity.sources.items())}}
    out = {
        "meta": {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "platform": sys.platform,
                 "python": sys.version.split()[0], "git": (git_out(["--version"], HOME, 5) or "none").strip(), "home": str(HOME),
                 "targets": [str(t) for t in targets], "total_seconds": None},
        "summary": summary, "identity": identity,
        "instruction_files": S.lists["instruction_files"], "memory_stores": S.lists["memory_stores"], "hooks": S.lists["hooks"],
        "artifacts": arts, "plugins": plugins, "mcp_servers": S.lists["mcp_servers"], "mcp_notes": S.lists["mcp_notes"],
        "settings": {"files": S.lists["settings_files"], "backups": S.lists["settings_backups"], "notable": S.lists["settings_notable"],
                     "counts": S.lists["settings_counts"], "env_keys": S.lists["settings_env_keys"]},
        "worktrees": worktrees, "scaffolding": scaffolding, "launchers": launchers, "could_not_read": COULD_NOT_READ, "timings_s": TIMINGS,
    }
    with section("self_check"):
        bad = self_check(out)
    out["meta"]["total_seconds"] = round(time.monotonic() - T0, 2)
    if bad:
        print(f"timings {TIMINGS}", file=sys.stderr)
        print("SECRET SELF-CHECK FAILED, nothing written. Fields:", file=sys.stderr)
        for b in bad[:30]:
            print("  " + b, file=sys.stderr)
        return 2
    outp = Path(args.out).expanduser()
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"wrote {outp} ({outp.stat().st_size} bytes) in {out['meta']['total_seconds']}s; timings {TIMINGS}", file=sys.stderr)
    return 0


def scan_single_memory_md(f: Path, project: Path) -> dict:
    text = read_text(f) or ""
    st = f.stat()
    n = len(WIKILINK.findall(_INLINE.sub("", _FENCE.sub("", text))))
    return {"path": str(f), "kind": "memory_md", "scope": "project", "project": str(project), "referenced_from": [], "files": 1,
            "bytes": st.st_size, "md_files": 1, "md_read": 1, "md_with_frontmatter": int(frontmatter(text) is not None),
            "frontmatter_share": float(frontmatter(text) is not None), "frontmatter_types": {}, "index_files": [str(f)], "top_subdirs": [],
            "wikilinks": {"total": n, "unique_targets": None, "resolved": None, "unresolved_unique": None, "unresolved_sample": []},
            "oldest_mtime": iso(st.st_mtime), "newest_mtime": iso(st.st_mtime), "truncated": False, "gitignored": None, "tracked_files": None}


if __name__ == "__main__":
    sys.exit(main())
