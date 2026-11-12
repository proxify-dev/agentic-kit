"""Group 4: 39 rewritten checks (10 practices) and their stranger-laptop cases.

11-05 skeptic round (FIX-BRIEF.md): each refuted check counts the person's act or says "can't tell". Shared helpers it added:
  adds_context       what Claude Code does with a hook's output: only additionalContext (or a prompt hook's text) reaches the model
  wired_by_person    the settings file a hook sits in is the person's (user / local scope, or a project file git says they wrote)
  can_block          the hook's text holds exit code 2 or a deny / block reply (a PreToolUse logger limits nothing)
  _agent_lines / _in_loop / _runner_texts   an agent run in files the person wrote, the call itself inside a loop, installs left out
  _prompt_is_own     a headless run's prompt sits whole in the person's own script (plugins' and hooks' runs write the same record)

Helpers this file adds on top of base.py (base.py lacked them):
  _raw / _atts       full attachments: base keeps only the first 600 characters of an attachment, which cuts off hook
                     commands and rule-file lists; these re-read the session files base already found (same window,
                     every copy counted once by uuid).
  hook_entries       every hook wired in a settings file or an enabled plugin, worktree copies collapsed.
  _scripts_of        the existing files a hook command runs (~, $HOME, $CLAUDE_PROJECT_DIR, $CLAUDE_PLUGIN_ROOT resolved).
  _mine              lt.authorship(path)["yours"]: the one ownership rule (ruling 11-05), theirs unless a receipt says installed
                     or most of the commits are someone else's.
  declared_runners   package.json scripts / Makefile / justfile recipes / CI steps of the person's repos, with the tracked
                     script each one runs.
"""
from __future__ import annotations

import functools
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from base import check, epoch, CHECK_ROLE, NOT_A_CHECK
from fixture import Session, fixture

DAY = 86400


def _fixture(pid, level, i):
    """fixture(), plus a real git repo around every session folder: the fixture home sits inside this checkout, and a plain
    folder would otherwise resolve to it and read its settings and .mcp.json as the stranger's."""
    def deco(fn):
        def run(fx):
            orig = fx.session

            def session(cwd="~/work/shop", **kw):
                fx.repo(cwd)
                return orig(cwd, **kw)
            fx.session = session
            return fn(fx)
        fixture(pid, level, i)(run)
        return fn
    return deco


# ====================================================================== helpers
def _cache(lt):
    return lt.__dict__.setdefault("_p4cache", {})


def _short(x, n=90):
    return str(x).replace("\n", " ")[:n]


def _norm(p):
    return re.sub(r"\\+", "/", p or "")


def _unesc(s):
    try:
        return json.loads('"' + s + '"')
    except ValueError:
        return s


def _input(c):
    """A tool call's input as a dict (base keeps the first 1500 characters; a cut-off input is read field by field)."""
    try:
        d = json.loads(c["input"])
        if isinstance(d, dict):
            return d
    except ValueError:
        pass
    d = {}
    for k in ("file_path", "command", "skill", "path", "pattern", "subagent_type"):
        m = re.search(r'"%s":\s*"((?:[^"\\]|\\.)*)' % k, c.get("input") or "")
        if m:
            d[k] = _unesc(m.group(1))
    return d


def _raw(lt, pattern):
    """Session-file records whose line matches `pattern`: [{r, s, t, sc, u}], each uuid once, inside the window.
    (11-05: one fast shared search, lt.grep, instead of reading every log line by line once per pattern.)"""
    cache = _cache(lt)
    if ("raw", pattern) in cache:
        return cache[("raw", pattern)]
    out = []
    for r in lt.grep(pattern):
        t = epoch(r.get("timestamp")) if r.get("timestamp") else None
        if t is None or t < lt.start:
            continue
        out.append({"r": r, "s": r["_sid"], "t": t, "u": r.get("uuid"), "sc": r["_sc"]})
    out.sort(key=lambda x: x["t"])
    cache[("raw", pattern)] = out
    return out


def _atts(lt, *types):
    """Full attachments of these types: the attachment's own fields plus s (session), t, sc, u."""
    rx = r'"attachment":\s*\{\s*"type":\s*"(?:%s)"' % "|".join(types)
    out = []
    for x in _raw(lt, rx):
        a = x["r"].get("attachment")
        if isinstance(a, dict) and a.get("type") in types:
            out.append({**a, "s": x["s"], "t": x["t"], "sc": x["sc"], "u": x["u"]})
    return out


def _live(lt, x):
    """Done in a session the person worked in (any interactive entrypoint), by the main thread, not a headless run."""
    return not x.get("sc") and lt.interactive(x["s"])


def _typed_sessions(lt):
    return {m["s"] for m in lt.typed if lt.interactive(m["s"])}


def _main_checkout(lt, d):
    """The main checkout of the repo around d (a worktree resolves to it), or d itself."""
    cache = _cache(lt)
    if ("mc", d) not in cache:
        top = lt.git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=d).strip() if d and os.path.isdir(d) else ""
        cache[("mc", d)] = os.path.realpath(os.path.dirname(top)) if top.endswith(".git") else d
    return cache[("mc", d)]


def _mine(lt, path):
    """The file is the person's by the one ownership rule (lt.authorship(path)["yours"] == lt.owner(path) is "theirs"): git need not
    track it. A file that does not exist is nobody's."""
    cache = _cache(lt)
    if ("mine", path) not in cache:
        cache[("mine", path)] = bool(path and os.path.exists(path) and lt.authorship(path)["yours"])
    return cache[("mine", path)]


def _read(path, cap=200_000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(cap)
    except OSError:
        return ""


def _tracked(lt, repo):
    """The repo's paths that are not ignored: tracked, then untracked (whose each is, is _mine: untracked files are theirs). `ls-files -s`
    also tells which tracked ones are symlinks (mode 120000), kept in _tracked_links."""
    cache = _cache(lt)
    if ("tracked", repo) not in cache:
        names, links = [], set()
        for line in lt.git("ls-files", "-s", cwd=repo).splitlines():
            meta, _, name = line.partition("\t")
            if not name:
                continue
            if meta.startswith("120000"):
                links.add(name)
            names.append(name)
        names += [x for x in lt.git("ls-files", "--others", "--exclude-standard", cwd=repo).splitlines() if x]
        cache[("tracked", repo)] = list(dict.fromkeys(names))
        cache[("tracked_links", repo)] = links
    return cache[("tracked", repo)]


def _tracked_links(lt, repo):
    _tracked(lt, repo)
    return _cache(lt)[("tracked_links", repo)]


def _warm_tracked(lt, repos):
    """The repos' file lists, read side by side (each `git ls-files` is an independent run)."""
    _side_by_side(lt, lambda r: _tracked(lt, r), [r for r in repos if ("tracked", r) not in _cache(lt)])


def _settings_root(path):
    """The folder a project settings file belongs to (parent of .claude)."""
    return os.path.dirname(os.path.dirname(path))


def _effective_plugins(lt):
    """{plugin key: enabled} as Claude Code reads it: later settings files override earlier ones."""
    en = {}
    for _, _, data in lt.settings_files():
        ep = data.get("enabledPlugins") if isinstance(data, dict) else None
        if isinstance(ep, dict):
            en.update({k: bool(v) for k, v in ep.items()})
    return en


def _json(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}


def plugin_roots(lt):
    """{plugin key: [folders]} of the enabled plugins: the install copy, and where it was written when its marketplace is
    a local folder or a clone (so authorship can be asked of the real source)."""
    cache = _cache(lt)
    if "plugin_roots" in cache:
        return cache["plugin_roots"]
    inst = _json(os.path.join(lt.claude, "plugins", "installed_plugins.json")).get("plugins") or {}
    known = _json(os.path.join(lt.claude, "plugins", "known_marketplaces.json"))
    en = _effective_plugins(lt)
    out = {}
    for key, entries in inst.items():
        if not en.get(key):
            continue
        name, _, market = key.partition("@")
        roots = []
        for e in entries if isinstance(entries, list) else []:
            src = None
            loc = (known.get(market) or {}).get("installLocation")
            if loc:
                mj = _json(os.path.join(loc, ".claude-plugin", "marketplace.json"))
                for p in mj.get("plugins") or []:
                    if isinstance(p, dict) and p.get("name") == name and isinstance(p.get("source"), str):
                        src = os.path.normpath(os.path.join(loc, p["source"]))
            if src and os.path.isdir(src):
                roots.append(src)
            if e.get("installPath"):
                roots.append(e["installPath"])
        out[key] = roots
    cache["plugin_roots"] = out
    return out


def _expand(lt, cmd, root, plugin_root=None):
    env = {"CLAUDE_PROJECT_DIR": root, "HOME": lt.home, "CLAUDE_CONFIG_DIR": lt.claude}
    if plugin_root:
        env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    return re.sub(r"\$\{(\w+)(?::-[^}]*)?\}|\$(\w+)",
                  lambda m: env.get(m.group(1) or m.group(2)) or m.group(0), cmd)


def _scripts_of(lt, cmd, root, plugin_roots_=()):
    """The existing files a hook command runs. root: the folder relative paths and $CLAUDE_PROJECT_DIR mean.
    11-05: one answer per distinct (command, root, plugin folders): a hook command repeats on every run it fires."""
    memo = _cache(lt).setdefault("scripts_of", {})
    key = (cmd, root, tuple(plugin_roots_))
    if key not in memo:
        memo[key] = _scripts_uncached(lt, cmd, root, plugin_roots_)
    return list(memo[key])


def _scripts_uncached(lt, cmd, root, plugin_roots_=()):
    out = []
    for pr in list(plugin_roots_) or [None]:
        s = _expand(lt, cmd or "", root or "", pr)
        try:
            toks = shlex.split(s)
        except ValueError:
            toks = s.split()
        for t in toks:
            t = t.strip("\"'")
            if t.startswith("~/"):
                t = os.path.join(lt.home, t[2:])
            cands = [t] if os.path.isabs(t) else ([os.path.join(root, t)] if root else [])
            for c in cands:
                if os.path.isfile(c) and c not in out:
                    out.append(c)
    return out


def hook_entries(lt):
    """Every hook wired in a settings file or an enabled plugin's hooks.json:
    {scope, plugin, event, matcher, command, root, files}. A project's hook seen again in a worktree copy counts once."""
    cache = _cache(lt)
    if "hooks" in cache:
        return cache["hooks"]
    out, seen = [], set()

    def add(scope, plugin, hooks, root, plugin_rs, where):
        if not isinstance(hooks, dict):
            return
        for ev, groups in hooks.items():
            for g in groups if isinstance(groups, list) else []:
                if not isinstance(g, dict):
                    continue
                for h in g.get("hooks") or []:
                    if not isinstance(h, dict) or not h.get("command"):
                        continue
                    cls = "project" if scope.startswith("project") else scope
                    key = (cls, plugin, ev, g.get("matcher"), h["command"], _main_checkout(lt, root) if cls == "project" else "")
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({"scope": scope, "plugin": plugin, "event": ev, "matcher": g.get("matcher"), "command": h["command"],
                                "root": root, "files": _scripts_of(lt, h["command"], root, plugin_rs), "where": where})

    for scope, path, data in lt.settings_files():
        if isinstance(data, dict) and data.get("hooks"):
            root = _settings_root(path) if scope.startswith("project") else lt.home
            add(scope, None, data["hooks"], root, (), path)
    for key, roots in plugin_roots(lt).items():
        for r in roots:
            hj = _json(os.path.join(r, "hooks", "hooks.json"))
            add("plugin", key, hj.get("hooks") if isinstance(hj.get("hooks"), dict) else hj, r, [r], r)
    cache["hooks"] = out
    _warm_authors(lt, {f for h in out for f in h["files"]})
    return out


def _side_by_side(lt, fn, items):
    """fn over items on a few threads (each is an independent git run); results are kept by fn's own memo."""
    items = list(items)
    lt.repos, lt.identity   # base reads these lazily and swaps HOME while it does: read them before any thread starts
    if len(items) > 1:
        with ThreadPoolExecutor(max_workers=min(16, (os.cpu_count() or 4) * 2)) as pool:
            list(pool.map(fn, items))


def _warm_authors(lt, files):
    """Read the one whole-history `git log` of every repo these files lie in, side by side, before the checks ask
    lt.authorship() file by file."""
    files = [f for f in files if os.path.exists(f)]
    _side_by_side(lt, lt.toplevel, sorted({os.path.dirname(os.path.realpath(f)) for f in files}))
    tops = {lt.toplevel(f) for f in files} - {None}
    memo = lt.__dict__.setdefault("_auth_memo", {})
    _side_by_side(lt, lt._authors, sorted(t for t in tops if t not in memo))


def own_hook(lt, h):
    """The hook runs a script that is the person's by the one ownership rule (_mine). Never a managed-policy hook."""
    return h["scope"] != "managed" and any(_mine(lt, f) for f in h["files"])


def wired_by_person(lt, h):
    """The person put this hook in a settings file that is theirs by the one ownership rule (_mine): a user, local or project file,
    committed or not. A teammate's committed settings, an installed plugin's hooks and a managed policy are not theirs. The scope
    (a settings file, not a plugin's hooks) is checked here; whose the file is, is not."""
    return h["scope"] in ("user", "user-local", "project-local", "project") and _mine(lt, h["where"])


def _json_objects(text):
    """The JSON objects a hook printed: one per line, or the whole text as one."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
    if not out and text.strip().startswith("{"):
        try:
            d = json.loads(text)
        except ValueError:
            return out
        if isinstance(d, dict):
            out.append(d)
    return out


def adds_context(a):
    """What Claude Code does with a hook's output (its documented protocol): a JSON reply puts hookSpecificOutput.additionalContext
    into the model's context and nothing else in it does (`{}`, updatedInput, a decision add none); plain text on stdout reaches
    the model only from a prompt hook (UserPromptSubmit). The skeptic, 11-05: 370 of 2,224 sampled 'outputs' were `{}` or a rewrite."""
    stdout = a.get("stdout") or ""
    objs = _json_objects(stdout)
    if objs:
        for d in objs:
            hso = d.get("hookSpecificOutput")
            ctx = hso.get("additionalContext") if isinstance(hso, dict) else None
            if (isinstance(ctx, str) and ctx.strip()) or (isinstance(ctx, list) and any(str(x).strip() for x in ctx)):
                return True
        return False
    text = ((a.get("content") or "") or stdout).strip()
    return bool(text) and a.get("hookEvent") in ("UserPromptSubmit", "UserPromptExpansion")


def placeholder_script_is_mine(lt, cmd, root):
    """A hook command with ${CLAUDE_PLUGIN_ROOT}, as the log records it: which plugin ran it is not in the record. It is the
    person's when every enabled plugin that ships that script is one whose source the person wrote (a plugin's install copy and its
    source count as one plugin) and at least one does. Two plugins shipping the same relative name are not told apart."""
    owners = []
    for key, roots in plugin_roots(lt).items():
        files = [f for r in roots for f in _scripts_of(lt, cmd, root, [r])]
        if files:
            owners.append(any(_mine(lt, f) for f in files))
    return bool(owners) and all(owners)


def hook_runs(lt, events):
    """hook_success attachments in the person's own sessions, for these hook events, whose output went into the model's context
    (adds_context), whose command runs a script the person wrote."""
    runs = []
    for a in _atts(lt, "hook_success"):
        if not _live(lt, a) or a.get("hookEvent") not in events or not adds_context(a):
            continue
        cwd = (lt.sessions.get(a["s"]) or {}).get("cwd")
        root = _main_checkout(lt, cwd) if cwd else ""
        cmd = a.get("command") or ""
        if "CLAUDE_PLUGIN_ROOT" in cmd:
            ok = placeholder_script_is_mine(lt, cmd, root)
        else:
            files = _scripts_of(lt, cmd, root, ())
            ok = any(_mine(lt, f) for f in files)
        if ok:
            runs.append(a)
    return runs


# what a PreToolUse hook needs in it to stop a step: Claude Code's own protocol, an exit code 2 or a deny / block reply
_BLOCKS = re.compile(r"""\bexit\s+2\b|\bexit\s*\(\s*2\s*\)|\bSystemExit\s*\(\s*2\s*\)|process\.exitCode\s*=\s*2\b|"""
                     r"""permissionDecision\W{1,6}(?:deny|ask)\b|["']decision["']\s*[:=]\s*["']block["']|["']continue["']\s*[:=]\s*false\b""")


def can_block(lt, h):
    """The hook's command or the script it runs holds a way to stop the step (_BLOCKS). A logger or a hint that always exits 0 has none."""
    return bool(_BLOCKS.search(_strip_comments(h["command"] + "\n" + "\n".join(_read(f) for f in h["files"]))))


def _words(text):
    return len(text.split())


# a line that starts Claude headless: `claude -p`, the package run the same way, the list form of it, the Claude Code action
RUN_CLI = re.compile(r"""\bclaude\s+(?:[^\n|;&]*\s)?(?:-p|--print)\b|@anthropic-ai/claude-code(?:@[\w.-]+)?\s+(?:[^\n|;&]*\s)?(?:-p|--print)\b|"""
                     r"""['"]claude['"]\s*,\s*\[[^\]]*['"](?:-p|--print)['"]|anthropics/claude-code-action""")
SDK_NAME = re.compile(r"claude[-_]agent[-_]sdk")
SDK_CALL = re.compile(r"\bquery\s*\(|\bClaudeSDKClient\s*\(|\bunstable_v2_\w+\s*\(")
# installing is not starting (the skeptic, 11-05: `npm i -g @anthropic-ai/claude-code`, `pip install claude-agent-sdk` counted)
INSTALL = re.compile(r"(?i)\b(?:npm|pnpm|yarn|bun)\s+(?:i|install|add|ci|update|upgrade)\b|\bpip3?\s+(?:install|download)\b|"
                     r"\buv\s+(?:add|sync|pip\s+install|tool\s+install)\b|\b(?:brew|apt(?:-get)?|apk|dnf|yum|gem)\s+install\b|"
                     r"curl\b[^\n|]*\|\s*(?:ba)?sh")
# a loop construct on the line that holds the agent call, or that opens the block around it
LOOP_HEAD = re.compile(r"(?:^\s*|[;&|({]\s*)(?:async\s+)?(?:for|while|until)\b[^\n]*(?:\bin\b|\bof\b|\bdo\b|\(|:\s*$)|"
                       r"\.forEach\(|\.map\(|\bxargs\b|\bparallel\b|\bfind\b[^\n]*-exec\b")
SHELL_LOOP = re.compile(r"^\s*(?:for|while|until)\b[^\n]*\bdo\b")
SHELL_LOOP_OPEN = re.compile(r"^\s*(?:for|while|until)\b")
SHELL_DO = re.compile(r"^\s*do\b")
SHELL_DONE = re.compile(r"^\s*done\b")
LIMIT_FLAGS = re.compile(r"--max-budget-usd|--max-turns\b|--allowed-?[Tt]ools|--disallowed-?[Tt]ools|--tools\b|"
                         r"\bmax[_-]?budget[_-]?usd\b|\bmax[_-]?turns\b|\ballowed[_-]?[Tt]ools\b|\bdisallowed[_-]?[Tt]ools\b", re.I)


def _strip_comments(text):
    return "\n".join(l for l in text.splitlines() if not re.match(r"\s*(#|//|--|/\*|\*)", l))


def declared_runners(lt):
    """Entries the person's repos declare: package.json scripts, Makefile / justfile recipes, CI run steps:
    {repo, kind, name, file, body, script_text}. script_text: the script files the entry runs, tracked or not (ignored build output
    is not listed, so it never shows up)."""
    cache = _cache(lt)
    if "runners" in cache:
        return cache["runners"]
    out = []
    for repo in lt.repos:
        tracked = set(_tracked(lt, repo))

        def entry(kind, name, file, body):
            scripts = {}   # {repo-relative path: (full path, text)} of the tracked script files the entry runs
            for tok in re.split(r"\s+", body):
                tok = tok.strip("\"';&|()")
                if tok in tracked or os.path.relpath(os.path.join(repo, tok), repo) in tracked:
                    scripts[os.path.relpath(os.path.join(repo, tok), repo)] = (os.path.join(repo, tok), _read(os.path.join(repo, tok)))
            out.append({"repo": repo, "kind": kind, "name": name, "file": os.path.join(repo, file), "body": body,
                        "scripts": scripts, "script_text": "\n".join(t for _, t in scripts.values())})

        pj = os.path.join(repo, "package.json")
        if "package.json" in tracked:
            for n, b in (_json(pj).get("scripts") or {}).items():
                if isinstance(b, str):
                    entry("package.json", n, "package.json", b)
        for mk in ("Makefile", "makefile", "GNUmakefile", "justfile", "Justfile", ".justfile"):
            if mk not in tracked:
                continue
            name, body = None, []
            for line in _read(os.path.join(repo, mk)).splitlines() + ["end:"]:
                m = re.match(r"^@?([A-Za-z0-9_.-]+)[^\n:=]*:(?!=)", line)
                if m and not line.startswith((" ", "\t")):
                    if name:
                        entry(mk, name, mk, "\n".join(body))
                    name, body = m.group(1), []
                elif name and (line.startswith((" ", "\t")) or not line.strip()):
                    body.append(line.strip())
                elif name and line.strip():
                    entry(mk, name, mk, "\n".join(body))
                    name, body = None, []
        for rel in tracked:
            if re.match(r"\.github/workflows/[^/]+\.ya?ml$", rel) or rel == ".gitlab-ci.yml":
                lines = _read(os.path.join(repo, rel)).splitlines()
                for i, line in enumerate(lines):
                    m = re.match(r"(\s*)(?:-\s*)?run:\s*(.*)$", line)
                    if not m:
                        continue
                    if m.group(2).strip() in ("|", ">", "|-", ">-"):
                        ind, blk = len(m.group(1)), []
                        for nxt in lines[i + 1:]:
                            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= ind + 1:
                                break
                            blk.append(nxt.strip())
                        entry("ci", rel, rel, "\n".join(blk))
                    else:
                        entry("ci", rel, rel, m.group(2).strip().strip("'\""))
    cache["runners"] = out
    return out


def _logical_lines(text):
    """The text's lines with `\\` continuations joined and comment lines dropped (a flag on a continued line starts with `--`)."""
    return _strip_comments(text.replace("\\\n", " ")).splitlines()


@functools.lru_cache(maxsize=None)
def _agent_lines(text):
    """(lines, indexes of the lines that start Claude headless). An install line never does; an SDK call counts only in a file
    that names the SDK."""
    lines = _logical_lines(text)
    sdk = bool(SDK_NAME.search("\n".join(lines)))
    return lines, [i for i, l in enumerate(lines) if not INSTALL.search(l) and (RUN_CLI.search(l) or (sdk and SDK_CALL.search(l)))]


def _in_loop(lines, i):
    """The line sits in a loop: a loop construct on the line itself, a shell for/while ... do ... done around it, or an enclosing
    indented block (python, yaml, formatted js) whose header is a loop. A loop elsewhere in the file does not count."""
    if LOOP_HEAD.search(lines[i]):
        return True
    depth = 0
    for j, l in enumerate(lines[:i]):
        if SHELL_DONE.search(l):
            depth = max(0, depth - 1)
        elif SHELL_LOOP.search(l) and re.search(r"\bdone\b", l):
            continue   # a one-line loop opens and closes on its line
        elif SHELL_LOOP.search(l) or (SHELL_LOOP_OPEN.search(l) and j + 1 < len(lines) and SHELL_DO.search(lines[j + 1])):
            depth += 1
    if depth:
        return True
    cur = len(lines[i]) - len(lines[i].lstrip())
    for j in range(i - 1, -1, -1):
        if not lines[j].strip():
            continue
        ind = len(lines[j]) - len(lines[j].lstrip())
        if ind < cur:
            if LOOP_HEAD.search(lines[j]):
                return True
            cur = ind
    return False


def _runner_texts(lt, e, in_loop=False):
    """The texts of this declared entry that start an agent run and that the person wrote: its own body (when its file is theirs)
    and each tracked script it runs (when that script is theirs). A body line that runs a script which starts an agent counts as
    the agent line there. in_loop: only where the call sits in a loop. The skeptic, 11-05: authorship was read for the package.json
    or Makefile only, and a loop anywhere in the script counted."""
    got = []
    mine_scripts = {}
    for rel, (path, text) in e["scripts"].items():
        lines, idx = _agent_lines(text)
        if idx and _mine(lt, path):
            mine_scripts[rel] = path
            if not in_loop or any(_in_loop(lines, i) for i in idx):
                got.append(text)
    lines, idx = _agent_lines(e["body"])
    idx = set(idx) | {i for i, l in enumerate(lines) if any(rel in l for rel in mine_scripts)}
    if idx and (not in_loop or any(_in_loop(lines, i) for i in idx)) and _mine(lt, e["file"]):   # authorship last: only for entries that run an agent
        got.append(e["body"])
    return got


def _agent_runner(lt, e, in_loop=False):
    """The declared entry starts an agent headless (itself or through the tracked script it runs), in files the person wrote."""
    return bool(_runner_texts(lt, e, in_loop))


# ====================================================================== enforce-fixed-rules-in-code
def _git_hook_files(lt, repo):
    """Hook files git itself runs in this repo: core.hooksPath, else .git/hooks; the .sample files are not hooks."""
    hp = lt.git("config", "--get", "core.hooksPath", cwd=repo).strip()
    d = (os.path.join(lt.home, hp[2:]) if hp.startswith("~/") else hp if os.path.isabs(hp) else os.path.join(repo, hp)) if hp \
        else os.path.join(repo, ".git", "hooks")
    out = []
    for f in (os.listdir(d) if os.path.isdir(d) else []):
        p = os.path.join(d, f)
        if os.path.isfile(p) and not f.endswith(".sample"):
            out.append(p)
    return out


def _runs_a_check(rx, text):
    """Any line of the text (comments stripped) holds one of the declared checks (rx: a matcher, may be None)."""
    return bool(rx) and any(rx.search(l) for l in _logical_lines(text))


def _is_check(cmd):
    """A declared command is a check when its role is checking. base.py's language table also lists builds (cargo build,
    dotnet build); setting up or building is not checking the work (the skeptic, 11-05: `npm ci`, `uv sync` hooks read as a check)."""
    return not NOT_A_CHECK.search(cmd) and (CHECK_ROLE.search(cmd) or not re.search(r"(?i)\bbuild\b", cmd))


_LIT_PRE, _LIT_SUF = r"(^|[;&|]\s*|\s)", r"(\s|$)"


class _Commands:
    """Many declared checks, each `<start / separator / space><command><space / end>` (base.py lit()), matched without trying
    every one at every place: only at a place a command can start, only the commands starting with that character.
    Same answer as the one alternation pattern, which takes minutes with ~1,900 alternatives per line."""

    def __init__(self, cmds):
        self.by_first = {}
        for c in cmds:
            self.by_first.setdefault(c[:1], []).append(c)

    def search(self, line):
        n = len(line)
        for i in range(n + 1):
            if i and not (line[i - 1].isspace() or line[i - 1] in ";&|"):
                continue
            for c in self.by_first.get(line[i:i + 1], ()) if i < n else self.by_first.get("", ()):
                j = i + len(c)
                if line.startswith(c, i) and (j == n or line[j].isspace()):
                    return True
        return False


def _matcher(cmds, pats):
    cmds = list(dict.fromkeys(cmds))
    if cmds and len(cmds) == len(pats) and all(cmds):
        return _Commands(cmds)
    return re.compile("|".join("(?:%s)" % x for x in pats)) if pats else None   # a declared check of another shape: the plain pattern


def _checks_of(lt, repos):
    """One matcher over the declared checks (check role only) of these repos. Memoized per set of repos."""
    cache = _cache(lt).setdefault("checks_rx", {})
    key = tuple(repos)
    if key not in cache:
        pats = list(dict.fromkeys(p.pattern for r in repos for p in lt.declared_checks(r)))
        lits = [(re.sub(r"\\(.)", r"\1", x[len(_LIT_PRE):-len(_LIT_SUF)]) if x.startswith(_LIT_PRE) and x.endswith(_LIT_SUF) else None, x)
                for x in pats]
        keep = [(c, x) for c, x in lits if c is None or _is_check(c)]
        cache[key] = _matcher([c for c, _ in keep] if all(c is not None for c, _ in keep) else [], [x for _, x in keep]) if keep else None
    return cache[key]


def _every_declared_rx(lt):
    """All repos' declared checks as one matcher: for a hook whose repo is unknown (a user-level hook)."""
    return _checks_of(lt, tuple(lt.repos))


@check("enforce-fixed-rules-in-code", "L3", 0)
def ready_made_check_wired(lt):
    """A hook the person put in a settings file (_wired_by_person), or a git hook in their repo, whose command (or the script it
    runs) is one of the commands the repo itself declares as a check (test, lint, type check; not install or build). A hook that
    only logs, notifies or sets up does not count."""
    found, ev = [], []
    for h in hook_entries(lt):
        if h["plugin"] or not wired_by_person(lt, h):
            continue
        repo = _main_checkout(lt, h["root"])
        rx = _checks_of(lt, (repo,)) if repo in lt.repos else _every_declared_rx(lt)
        text = h["command"] + "\n" + "\n".join(_read(f) for f in h["files"])
        if _runs_a_check(rx, text):
            found.append(h); ev.append(f"{h['event']}: {_short(h['command'], 60)}")
    for r in lt.repos:
        pc = os.path.join(r, ".pre-commit-config.yaml")
        for p in _git_hook_files(lt, r):
            body = _read(p)
            # whose: the hook file (a core.hooksPath folder a teammate committed is theirs, not the person's), or for the
            # stub `pre-commit install` writes, the config it runs
            precommit = _mine(lt, pc) and re.search(r"pre[-_]commit", body)
            if (_mine(lt, p) and _runs_a_check(_checks_of(lt, (r,)), body)) or precommit:
                found.append(p); ev.append(f"git {os.path.basename(p)} in {os.path.basename(r)}")
    return {"value": len(found), "evidence": ev[:3],
            "note": "reads the hook's command and the script it runs against the repo's declared checks (check roles only); a hook wired to "
                    "a script in another language or a remote runner is not read; a settings file or git hook nobody committed is theirs (ruling 11-05); a git hook or pre-commit config a teammate committed is not"}


def _commit_settings(fx, repo, data, by="work"):
    """A project settings file committed by `by` (git is how a file is placed as someone's)."""
    fx.commit(repo, {".claude/settings.json": json.dumps(data, indent=1)}, by=by, message="settings")


@_fixture("enforce-fixed-rules-in-code", "L3", 0)
def _(fx):
    fx.repo("~/work/shop", {"package.json": json.dumps({"scripts": {"test": "jest", "build": "tsc -b"}}), "scripts/verify.sh": "#!/bin/sh\nnpm run test\n",
                            "scripts/notify.sh": "#!/bin/sh\nosascript -e 'display notification \"done\"'\n", "Cargo.toml": "[package]\nname='x'\n",
                            ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: npm ci\n      - run: npm test\n"})
    _commit_settings(fx, "~/work/shop", {"hooks": {
        "PostToolUse": [
            {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "npm test --silent"}]},                # counts
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "sh scripts/verify.sh"}]},                   # counts: script runs the check
            {"hooks": [{"type": "command", "command": "echo edited >> /tmp/edits.log"}]},                              # trap: a logger
            {"hooks": [{"type": "command", "command": "sh scripts/notify.sh"}]}],                                      # trap: a notifier
        "SessionStart": [{"hooks": [{"type": "command", "command": "npm ci"}, {"type": "command", "command": "cargo build"}]}]}})   # trap: setup, build
    fx.settings("user", {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "uv sync"}]}]}})      # trap: a sync, not a check
    fx.file("~/work/shop/.git/hooks/pre-push", "#!/bin/sh\nnpm test\n")                                            # counts: git hook runs the check
    fx.file("~/work/shop/.git/hooks/pre-commit.sample", "#!/bin/sh\n# npm test\nnpm test\n")                      # trap: a sample
    fx.file("~/work/shop/.git/hooks/post-commit", "#!/bin/sh\n# npm test\ngit log -1 >> /tmp/l\n")                 # trap: only a comment
    fx.file("~/work/shop/.git/hooks/post-merge", "#!/bin/sh\nnpm ci\n")                                            # trap: setup
    fx.repo("~/work/team", {"package.json": json.dumps({"scripts": {"test": "jest"}})}, by="teammate")
    _commit_settings(fx, "~/work/team", {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "npm test"}]}]}}, by="teammate")   # trap: a teammate's
    fx.commit("~/work/team", {".githooks/pre-push": "#!/bin/sh\nnpm test\n", ".pre-commit-config.yaml":
              "repos:\n- repo: local\n  hooks:\n  - id: t\n    entry: npm test\n"}, by="teammate")
    fx._git(fx.p("~/work/team"), "config", "core.hooksPath", ".githooks")   # trap: the team's committed git hook runs the check
    fx.file("~/work/team/.githooks/pre-commit", "#!/bin/sh\nexec pre-commit run\n")                              # trap: the stub runs a teammate's config
    fx.session("~/work/shop", entrypoint="claude-desktop").typed("hi")
    fx.session("~/work/team", entrypoint="claude-desktop").typed("hi")
    return {"value": 3}


def _block_attachments(lt):
    return [a for a in _atts(lt, "hook_blocking_error") if _live(lt, a)]


def _block_command(a):
    b = a.get("blockingError")
    return (b.get("command") if isinstance(b, dict) else None) or a.get("command") or ""


@check("enforce-fixed-rules-in-code", "L3", 2)
def hook_blocks_attachments(lt):
    """hook_blocking_error attachments (Claude Code's own record of a hook stopping a step) in the person's sessions, any
    interactive entrypoint, main thread. Counts blocks by every hook, plugins' included."""
    bl = _block_attachments(lt)
    return {"value": len(bl), "evidence": [f"{a.get('hookEvent')}: {_short(_block_command(a), 60)}" for a in bl[:3]],
            "note": "a block that came back as a tool result (see L4#2) is not an attachment; not whose hook"}


@_fixture("enforce-fixed-rules-in-code", "L3", 2)
def _(fx):
    s = fx.session("~/work/shop", entrypoint="claude-vscode")
    s.typed("ship it")
    s.attachment("hook_blocking_error", hookName="Stop", hookEvent="Stop", toolUseID="a",
                 blockingError={"blockingError": "tests not run", "command": "sh scripts/gate.sh"})
    s.attachment("hook_blocking_error", hookName="PreToolUse:Bash", hookEvent="PreToolUse", toolUseID="b",
                 blockingError={"blockingError": "no force push", "command": "sh scripts/gate.sh"})
    s.attachment("hook_non_blocking_error", hookName="Stop", hookEvent="Stop", exitCode=1, command="sh scripts/x.sh")   # trap: not a block
    s.attachment("hook_success", hookName="Stop", hookEvent="Stop", stdout="ok", command="sh scripts/x.sh")             # trap
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                                                                 # trap: headless
    h.typed("run the nightly job")
    h.attachment("hook_blocking_error", hookName="Stop", hookEvent="Stop", toolUseID="c",
                 blockingError={"blockingError": "x", "command": "sh scripts/gate.sh"})
    return {"value": 2}


@check("enforce-fixed-rules-in-code", "L4", 0)
def pretool_hooks_by_person(lt):
    """PreToolUse hooks wired in settings or an enabled plugin whose script is the person's by the one ownership rule
    (_mine: lt.owner) and that can stop the step: its command or script holds an exit code 2 or a deny / block reply. A
    logger or a hint hook of theirs limits nothing and does not count. Worktree copies of one hook count once."""
    mine = [h for h in hook_entries(lt) if h["event"] == "PreToolUse" and own_hook(lt, h) and can_block(lt, h)]
    return {"value": len(mine), "evidence": [f"{h['matcher'] or '*'}: {os.path.basename(h['files'][0])}" for h in mine[:3]],
            "note": "inline commands and plugin hooks copied into the plugin cache can't be placed as theirs (a script nobody committed is theirs, ruling 11-05); "
                    "whether the exit code 2 / deny is reached on any path is not read"}


@_fixture("enforce-fixed-rules-in-code", "L4", 0)
def _(fx):
    fx.repo("~/work/shop", {"scripts/gate.sh": "#!/bin/sh\necho 'no force push' >&2\nexit 2\n", "scripts/post.sh": "#!/bin/sh\nexit 2\n",
                            "scripts/deny.py": "import json\nprint(json.dumps({'hookSpecificOutput': {'permissionDecision': 'deny'}}))\n",
                            "scripts/log.sh": "#!/bin/sh\necho \"$@\" >> /tmp/tools.log\nexit 0\n",
                            "scripts/hint.sh": "#!/bin/sh\n# exit 2 would stop it\necho 'remember the tests'\nexit 0\n"})
    fx.commit("~/work/shop", {"scripts/team-gate.sh": "#!/bin/sh\nexit 2\n"}, by="teammate")
    for i in range(3):
        fx.commit("~/work/shop", {f"src/f{i}.ts": "x"}, by="teammate")        # the teammate is the top author of the repo
    fx.repo("~/work/team", {"hooks/x.sh": "#!/bin/sh\nexit 2\n"}, by="teammate")        # a team repo: nothing of theirs
    fx.settings("project", {"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "sh scripts/gate.sh"}]},              # counts: exit 2
                       {"matcher": "Edit", "hooks": [{"type": "command", "command": "python scripts/deny.py"}]},          # counts: a deny reply
                       {"matcher": "Edit", "hooks": [{"type": "command", "command": "sh scripts/team-gate.sh"}]},         # trap: teammate's
                       {"hooks": [{"type": "command", "command": "sh scripts/log.sh"}]},                                  # trap: a logger of theirs
                       {"hooks": [{"type": "command", "command": "sh scripts/hint.sh"}]}],                                # trap: a hint, exit 0
        "PostToolUse": [{"hooks": [{"type": "command", "command": "sh scripts/post.sh"}]}]}}, repo="~/work/shop")          # trap: not before
    fx.settings("user", {"hooks": {"PreToolUse": [
        {"hooks": [{"type": "command", "command": "sh " + fx.p("~/work/team/hooks/x.sh")}]},                              # trap: team repo, teammate wrote it
        {"hooks": [{"type": "command", "command": "echo hi; exit 2"}]}]}})                                                 # trap: inline
    fx.session("~/work/shop").typed("hi")
    fx.session("~/work/team").typed("hi")
    return {"value": 2}


@check("enforce-fixed-rules-in-code", "L4", 1)
def hook_blocks_by_own_hooks(lt):
    """Not measurable from the logs. Claude Code does not write `hook_blocking_error` attachments for PreToolUse or
    UserPromptSubmit hooks (on a real laptop all 178 such records were Stop / PostToolUse, which are "done" gates and feedback after
    a step, not a rule that must always hold). A PreToolUse block appears only as an error tool result, which L4#2 reads."""
    return {"value": None, "note": "Claude Code records PreToolUse blocks only as error tool results, read by L4#2; this record shape isn't written for rule hooks"}


@_fixture("enforce-fixed-rules-in-code", "L4", 1)
def _(fx):
    s = fx.session("~/work/guards", entrypoint="claude-desktop")
    s.typed("push it")
    s.attachment("hook_blocking_error", hookName="Stop", hookEvent="Stop",   # Stop / PostToolUse are what Claude Code writes; never counted
                 blockingError={"blockingError": "tests not run", "command": "sh ~/work/guards/no-push.sh"})
    return {"value": None}


_HOOK_ERR_HEAD = re.compile(r"^(?:Error:\s*)?(?:Pre|Post)ToolUse:[^\s:]+ hook error: \[")


def _blocked_by_own_script(lt, c, pr_all):
    """The tool result says `<Event>:<tool> hook error: [<the hook's command>]: <what it printed>`. The command is the hook's;
    true when the script it runs is the person's. A hook whose own text starts with a label instead of its command can't be placed."""
    m = _HOOK_ERR_HEAD.match(c.get("out") or "")
    if not m:
        return False
    rest = c["out"][m.end():]
    cwd = (lt.sessions.get(c["s"]) or {}).get("cwd")
    root = _main_checkout(lt, cwd) if cwd else ""
    pos = rest.find("]: ")
    while pos >= 0:
        cmd = rest[:pos]
        if "CLAUDE_PLUGIN_ROOT" in cmd:
            if placeholder_script_is_mine(lt, cmd, root):
                return True
        else:
            files = _scripts_of(lt, cmd, root, ())
            if files and all(_mine(lt, f) for f in files):
                return True
        pos = rest.find("]: ", pos + 1)
    return False


@check("enforce-fixed-rules-in-code", "L4", 2)
def hook_blocks_tool_results(lt):
    """Tool results that are errors and start with Claude Code's own 'PreToolUse:<tool> hook error: [<command>]' text (what a
    blocking hook returns to the model), main thread, interactive, where the command runs a script the person wrote."""
    pr_all = [r for rs in plugin_roots(lt).values() for r in rs]
    cand = [c for c in lt.calls if c.get("err") and _HOOK_ERR_HEAD.match(c.get("out") or "") and _live(lt, c)]
    hits = [c for c in cand if _blocked_by_own_script(lt, c, pr_all)]
    return {"value": len(hits), "evidence": [_short(c["out"], 80) for c in hits[:3]],
            "note": "the prefix is Claude Code's English text for this version (a version that words it differently reads 0, not 'none'); "
                    "a hook that prints its own label where the command goes, can't be placed as theirs"}


@_fixture("enforce-fixed-rules-in-code", "L4", 2)
def _(fx):
    fx.repo("~/work/shop", {"scripts/guard.sh": "#!/bin/sh\nexit 2\n"})
    fx.repo("~/work/vendor", {"gate.sh": "#!/bin/sh\nexit 2\n"}, by="teammate")
    mine, theirs = "sh " + fx.p("~/work/shop/scripts/guard.sh"), "sh " + fx.p("~/work/vendor/gate.sh")
    s = fx.session("~/work/shop", entrypoint="claude-vscode")
    s.typed("remove the build dir")
    s.call("Bash", {"command": "rm -rf build"}, f"PreToolUse:Bash hook error: [{mine}]: no rm -rf", is_error=True)               # counts
    s.call("Edit", {"file_path": "a.ts"}, f"PreToolUse:Edit hook error: [{mine}]: read first", is_error=True)                    # counts
    s.call("Bash", {"command": "cat notes.txt"}, f"PreToolUse:Bash hook error: [{mine}]: copied into a notes file")               # trap: not an error result
    s.call("Bash", {"command": "git push"}, f"PreToolUse:Bash hook error: [{theirs}]: no push", is_error=True)                    # trap: a teammate's script
    s.call("Bash", {"command": "x"}, "PreToolUse:Bash hook error: [agentic: ship] before this merge: say what changes", is_error=True)   # trap: a label, no command
    s.call("Bash", {"command": "y"}, "PreToolUse:Bash hook error: [sh ${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh]: plugin", is_error=True)   # trap: a plugin's
    s.call("Write", {"file_path": "x"}, "permission denied", is_error=True)                                                      # trap: another error
    s.subagent("Explore", "look", calls=[("Bash", {"command": "x"}, f"PreToolUse:Bash hook error: [{mine}]: no")])               # trap: a sub-agent's
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                                                                           # trap: headless
    h.typed("go")
    h.call("Bash", {"command": "rm -rf x"}, f"PreToolUse:Bash hook error: [{mine}]: no", is_error=True)
    fx.session("~/work/vendor").typed("hi")
    return {"value": 2}


@check("enforce-fixed-rules-in-code", "L4", 3)
def hooks_crashed(lt):
    """hook_non_blocking_error attachments (a hook failed but did not stop the step) of hooks wired in the person's own
    settings files, in their sessions. Plugin hooks write the same record and are left out."""
    own = {h["command"] for h in hook_entries(lt) if not h["plugin"]}
    bad = [a for a in _atts(lt, "hook_non_blocking_error") if _live(lt, a) and (a.get("command") or "") in own]
    return {"value": len(bad), "evidence": [f"{a.get('hookEvent')} exit {a.get('exitCode')}: {_short(a.get('command'), 50)}" for a in bad[:3]],
            "note": "a non-zero exit can be a deliberate warning; the record does not say which"}


@_fixture("enforce-fixed-rules-in-code", "L4", 3)
def _(fx):
    fx.settings("user", {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python ~/hooks/stop.py"}]}]}})
    s = fx.session("~/work/shop", entrypoint="claude-vscode")
    s.typed("done?")
    s.attachment("hook_non_blocking_error", hookName="Stop", hookEvent="Stop", exitCode=1, stderr="Traceback", stdout="", command="python ~/hooks/stop.py")
    s.attachment("hook_non_blocking_error", hookName="Stop", hookEvent="Stop", exitCode=2, stderr="oops", stdout="", command="python ~/hooks/stop.py")
    s.attachment("hook_non_blocking_error", hookName="SessionStart:startup", hookEvent="SessionStart", exitCode=1, command="${CLAUDE_PLUGIN_ROOT}/hooks/x.sh")   # trap
    s.attachment("hook_blocking_error", hookName="Stop", hookEvent="Stop", blockingError={"blockingError": "x", "command": "python ~/hooks/stop.py"})        # trap
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                                                     # trap: headless
    h.typed("go")
    h.attachment("hook_non_blocking_error", hookName="Stop", hookEvent="Stop", exitCode=1, command="python ~/hooks/stop.py")
    return {"value": 2}


# ====================================================================== write-down-what-it-cannot-know
@check("write-down-what-it-cannot-know", "L2", 0)
def corrected_the_agent(lt):
    """The first message typed after each refused step (Claude Code's own toolDenialKind user-rejected: the person said no to
    what the agent was about to do), in the same session, in every interactive entrypoint, with no other step of the agent
    in between. An Esc is left out: it is also how a person pauses or stops a run (the skeptic, 11-05)."""
    refusals = [(c["s"], c["t"]) for c in lt.calls if c.get("denial") == "user-rejected" and not c["sc"] and lt.interactive(c["s"])]
    by_s, calls_by_s = {}, {}
    for m in lt.typed:
        if lt.interactive(m["s"]):
            by_s.setdefault(m["s"], []).append(m)
    for c in lt.calls:
        if not c["sc"] and c.get("denial") != "user-rejected":
            calls_by_s.setdefault(c["s"], []).append(c["t"])
    hit = {}
    for s, t in refusals:
        nxt = next((m for m in by_s.get(s, []) if m["t"] >= t), None)
        if nxt and not any(t < ct < nxt["t"] for ct in calls_by_s.get(s, ())):
            hit[nxt["u"] or id(nxt)] = nxt
    return {"value": len(hit), "evidence": [m["text"][:80] for m in list(hit.values())[:3]],
            "note": "a refusal can mean 'not now' rather than 'that's wrong'; an Esc interrupt is not read at all (it is also how a run is paused)"}


@_fixture("write-down-what-it-cannot-know", "L2", 0)
def _(fx):
    s = fx.session("~/work/shop", entrypoint="claude-desktop")
    s.typed("add a login page")
    s.refuse("Edit", {"file_path": fx.p("~/work/shop/a.ts")})
    s.typed("non, utilise le composant existant")          # counts: right after a refusal, not in English
    s.refuse("Bash", {"command": "rm -rf build"})
    s.typed("never delete the build folder")                 # counts: right after a refusal
    s.interrupt()
    s.typed("stop, keep the old route")                      # trap: an Esc (the skeptic's: 'Let's see, build it in a branch worktree')
    s.interrupt()
    s.typed("Well here it is")                               # trap: an Esc to pause
    s.typed("great, now the tests")                          # trap: nothing before it
    r = fx.session("~/work/shop", entrypoint="claude-vscode")
    r.typed("fix the bug"); r.refuse("Edit", {"file_path": fx.p("~/work/shop/b.ts")})
    r.call("Bash", {"command": "npm test"})                  # trap: the agent went on; the later message answers something else
    r.typed("ok thanks")
    h = fx.session("~/work/shop", entrypoint="sdk-cli")      # trap: a headless run is not the person
    h.refuse("Bash", {"command": "rm -rf build"}); h.typed("go on")
    return {"value": 2}


@check("write-down-what-it-cannot-know", "L3", 0)
def rule_file_loaded_at_start(lt):
    """Interactive sessions whose first `instructions` attachment (what Claude Code loads at start) holds at least one rule file
    with content that is the person's by the one ownership rule (_mine: committed or not; a teammate's CLAUDE.md and a managed policy's file
    are not). Shows their own rule file is loaded; never which line is the fact."""
    first = {}
    for a in _atts(lt, "instructions"):
        if _live(lt, a) and a["s"] not in first:
            first[a["s"]] = a
    _warm_authors(lt, {f.get("path") for a in first.values() for f in a.get("files") or [] if isinstance(f, dict) and f.get("path")})
    ok, words = [], []
    for a in first.values():
        fs = [f for f in a.get("files") or [] if isinstance(f, dict) and (f.get("content") or "").strip() and f.get("type") != "Managed"
              and _mine(lt, f.get("path"))]
        if fs:
            ok.append(a); words.append(sum(_words(f["content"]) for f in fs))
    words.sort()
    med = words[len(words) // 2] if words else 0
    return {"value": len(ok), "evidence": [f"{len(ok)} sessions loaded their own rule files; median {med} words"] if ok else [],
            "note": "shows a rule file of theirs is loaded at start, not that a fact is a line in it"}


@_fixture("write-down-what-it-cannot-know", "L3", 0)
def _(fx):
    fx.repo("~/work/shop", {"CLAUDE.md": "Use the existing form component. Never edit generated files."})
    fx.repo("~/work/team", {"CLAUDE.md": "Team rules. Follow the style guide."}, by="teammate")
    fx.file("~/.claude/CLAUDE.md", "My user rules.")                                                    # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.file("~/work/loose/CLAUDE.md", "Not in git.")
    mine = {"path": fx.p("~/work/shop/CLAUDE.md"), "type": "Project", "content": "Use the existing form component. Never edit generated files."}
    for ep in ("claude-vscode", "claude-desktop"):
        s = fx.session("~/work/shop", entrypoint=ep)
        s.typed("hi"); s.attachment("instructions", files=[mine])
        s.attachment("instructions", files=[mine])                      # trap: a second load in the same session
    e = fx.session("~/work/shop", entrypoint="claude-vscode")
    e.typed("hi"); e.attachment("instructions", files=[{**mine, "content": "  "}])   # trap: empty
    t = fx.session("~/work/team", entrypoint="claude-vscode")           # counts: the user file (theirs, untracked); a teammate's project file alone would not (real 546 sessions)
    t.typed("hi"); t.attachment("instructions", files=[{"path": fx.p("~/.claude/CLAUDE.md"), "type": "User", "content": "My user rules."},
                                                       {"path": fx.p("~/work/team/CLAUDE.md"), "type": "Project", "content": "Team rules. Follow the style guide."}])
    u = fx.session("~/work/shop", entrypoint="claude-vscode")           # trap: a managed policy file
    u.typed("hi"); u.attachment("instructions", files=[{"path": fx.p("~/_system/CLAUDE.md"), "type": "Managed", "content": "Org policy."}])
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                 # trap: headless
    h.typed("go"); h.attachment("instructions", files=[mine])
    return {"value": 3}      # the two shop sessions, and the team session through the user file


@check("write-down-what-it-cannot-know", "L3", 2)
def agent_edits_own_rule_files(lt):
    """Can't tell. The claim is that the agent writes the rule file 'under tight rules they set'. The log shows an agent edit of a
    rule file (any session, any file), not the rules it was under; counting the edits measures how often the agent edits, not
    whether the person guards what it writes (the skeptic, 11-05)."""
    return {"value": None, "evidence": [],
            "note": "can't tell from a laptop: an agent's edit of a rule file is in the log, but the rules the person set for what it may "
                    "write are prose in the file, which no rule of ours reads; a question for the call"}


@_fixture("write-down-what-it-cannot-know", "L3", 2)
def _(fx):
    fx.repo("~/work/shop", {"CLAUDE.md": "rules", ".claude/rules/api.md": "api"})
    s = fx.session("~/work/shop", entrypoint="claude-vscode")
    s.typed("note that")
    s.call("Edit", {"file_path": fx.p("~/work/shop/CLAUDE.md"), "old_string": "a", "new_string": "b"})   # an agent edit: not the claim
    return {"value": None}


CONTEXT_EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "UserPromptExpansion")


@check("write-down-what-it-cannot-know", "L4", 0)
def own_hook_puts_text_in_context(lt):
    """hook_success records (Claude Code's own) for a hook that fires when a tool or a prompt comes up (not session start), whose
    output went into the model's context (a JSON reply with additionalContext; plain text from a prompt hook), and whose script
    the person wrote. Counted per run. `{}` and a rewrite of the tool's input add nothing to context and don't count."""
    runs = hook_runs(lt, CONTEXT_EVENTS)
    return {"value": len(runs), "evidence": [f"{a['hookEvent']}: {_short(a.get('command'), 60)}" for a in runs[:3]],
            "note": "that the text is a rule or a doc is not read; a plugin copied into the cache can't be traced to its author"}


def _ctx(event, text):
    """What a hook prints to put text into the model's context (Claude Code's protocol)."""
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def _plugin_world(fx):
    """A plugin the person wrote (local marketplace, cache copy) and one a teammate wrote (marketplace clone)."""
    fx.repo("~/code/mykit", {".claude-plugin/marketplace.json": json.dumps({"plugins": [{"name": "rules", "source": "./plugins/rules"}]}),
                             "plugins/rules/hooks/on_edit.py": "import sys\nprint('rule')\nsys.exit(2)\n"})
    fx.file("~/.claude/plugins/cache/mykit/rules/1.0.0/hooks/on_edit.py", "import sys\nprint('rule')\nsys.exit(2)\n")
    fx.repo("~/.claude/plugins/marketplaces/market", {".claude-plugin/marketplace.json": json.dumps({"plugins": [{"name": "other", "source": "./plugins/other"}]}),
                                                      "plugins/other/hooks/guard.py": "import sys\nsys.exit(2)\n"}, by="teammate")
    fx.file("~/.claude/plugins/cache/market/other/2.0.0/hooks/guard.py", "print('x')\n")
    fx.file("~/.claude/plugins/known_marketplaces.json", json.dumps({
        "mykit": {"source": {"source": "directory", "path": fx.p("~/code/mykit")}, "installLocation": fx.p("~/code/mykit")},
        "market": {"source": {"source": "github", "repo": "acme/market"}, "installLocation": fx.p("~/.claude/plugins/marketplaces/market")}}))
    fx.file("~/.claude/plugins/installed_plugins.json", json.dumps({"version": 2, "plugins": {
        "rules@mykit": [{"scope": "user", "installPath": fx.p("~/.claude/plugins/cache/mykit/rules/1.0.0")}],
        "other@market": [{"scope": "user", "installPath": fx.p("~/.claude/plugins/cache/market/other/2.0.0")}]}}))
    fx.settings("user", {"enabledPlugins": {"rules@mykit": True, "other@market": True}})


@_fixture("write-down-what-it-cannot-know", "L4", 0)
def _(fx):
    _plugin_world(fx)
    mine = "${CLAUDE_PLUGIN_ROOT}/hooks/on_edit.py"
    s = fx.session("~/code/mykit", entrypoint="claude-vscode")
    s.typed("edit it")
    s.attachment("hook_success", hookName="PreToolUse:Edit", hookEvent="PreToolUse", stdout=_ctx("PreToolUse", "rule: read first"), command=mine)        # counts
    s.attachment("hook_success", hookName="PreToolUse:Edit", hookEvent="PreToolUse", stdout=_ctx("PreToolUse", "rule: read first"), command=mine)        # counts
    s.attachment("hook_success", hookName="UserPromptSubmit", hookEvent="UserPromptSubmit", content="rule", stdout="rule", command=mine)                  # counts: plain text from a prompt hook
    s.attachment("hook_success", hookName="SessionStart:startup", hookEvent="SessionStart", stdout=_ctx("SessionStart", "rule"), command=mine)           # trap: session start
    s.attachment("hook_success", hookName="PreToolUse:Edit", hookEvent="PreToolUse", stdout="", command=mine)                                            # trap: no output
    s.attachment("hook_success", hookName="PostToolUse:Edit", hookEvent="PostToolUse", stdout="{}", command=mine)                                        # trap: `{}` (real: 2,953 on one laptop)
    s.attachment("hook_success", hookName="PreToolUse:Edit", hookEvent="PreToolUse", command=mine,                                                       # trap: an input rewrite, no context
                 stdout=json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"file_path": "/x"}}}))
    s.attachment("hook_success", hookName="PreToolUse:Edit", hookEvent="PreToolUse", stdout="rule: read first", command=mine)                            # trap: plain text from a tool hook is not context
    s.attachment("hook_success", hookName="PreToolUse:Edit", hookEvent="PreToolUse", stdout=_ctx("PreToolUse", "x"),
                 command="${CLAUDE_PLUGIN_ROOT}/hooks/guard.py")                                                                                         # trap: a teammate's plugin
    h = fx.session("~/code/mykit", entrypoint="sdk-cli")                                                                                                 # trap: headless
    h.typed("go"); h.attachment("hook_success", hookName="PreToolUse:Edit", hookEvent="PreToolUse", stdout=_ctx("PreToolUse", "rule"), command=mine)
    return {"value": 3}


def _test_runs_script(text, rel, name, stem, ext):
    """The test names the hook script itself: its file name or repo path as a whole word, or an import of its module (python
    `import` / `from .. import`, js `require` / `import` of a path ending in it). A bare stem found anywhere in a test (`log`,
    `index`, `main`) is not naming it (the skeptic, 11-05: `log.mjs` <- a test that calls console.log)."""
    if re.search(r"(?<![\w./-])(?:%s|%s)(?![\w-])" % (re.escape(rel), re.escape(name)), text):
        return True
    if ext == ".py" and re.search(r"(?m)^\s*(?:from\s+(?:[\w.]*\.)?%s\s+import\b|from\s+[\w.]+\s+import\s+[^\n]*\b%s\b|import\s+[^\n]*\b%s\b)"
                                  % ((re.escape(stem),) * 3), text):
        return True
    return ext in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx") and bool(re.search(
        r"""(?:require\s*\(|\bfrom\s+|\bimport\s*\(?)\s*['"][^'"\n]*[/.]%s(?:\.[cm]?[jt]sx?)?['"]""" % re.escape(stem), text))


def _names_script(texts, tp, rel, name, stem, ext):
    """_test_runs_script on the test file tp (its text read once). Every way it names the script (the repo path, the file name, an import of
    the module) holds the module name `stem` as text, so a test without it is not searched."""
    if tp not in texts:
        texts[tp] = _read(tp)
    return (not stem or stem in texts[tp]) and _test_runs_script(texts[tp], rel, name, stem, ext)


@check("write-down-what-it-cannot-know", "L4", 2)
def test_for_own_hook(lt):
    """Hook scripts the person wrote that a test file of theirs in the same repo runs (a file whose path says test or spec,
    that is the person's by the one ownership rule (_mine), and that names the script's file name or path, or imports its module), in a repo that declares a way
    to run tests."""
    seen, ev = set(), []
    texts = {}     # a test file's text, read once for all the hook scripts of its repo
    _warm_tracked(lt, {lt.toplevel(os.path.dirname(f)) for h in hook_entries(lt) for f in h["files"] if _mine(lt, f)} - {None})
    for h in hook_entries(lt):
        for f in h["files"]:
            if f in seen or not _mine(lt, f):
                continue
            seen.add(f)
            repo = _main_checkout(lt, os.path.dirname(f))
            top = lt.toplevel(os.path.dirname(f)) or repo
            if not lt.declared_checks(top):
                continue
            name = os.path.basename(f)
            stem, ext = os.path.splitext(name)
            rel = os.path.relpath(os.path.realpath(f), os.path.realpath(top)).replace(os.sep, "/")
            for trel in _tracked(lt, top):
                tp = os.path.join(top, trel)
                if re.search(r"(^|[/_.-])(tests?|spec|__tests__)([/_.-]|$)", trel.lower()) and tp != f \
                        and _names_script(texts, tp, rel, name, stem, ext) and _mine(lt, tp):
                    ev.append(f"{name} <- {trel}")
                    break
    return {"value": len(ev), "evidence": ev[:3], "note": "that the test holds the rule (not just imports the script) needs reading"}


@_fixture("write-down-what-it-cannot-know", "L4", 2)
def _(fx):
    fx.repo("~/work/shop", {"pyproject.toml": "[project]\nname='shop'\n", "scripts/rule_hook.py": "print(1)\n", "scripts/other_hook.sh": "echo\n",
                            "scripts/log.mjs": "console.log('x')\n", "scripts/gate.mjs": "process.exit(2)\n",
                            "tests/test_rule_hook.py": "from scripts import rule_hook\n\ndef test_x():\n    assert rule_hook\n",
                            "test/api.test.mjs": "import { gate } from '../scripts/gate.mjs';\nconsole.log('ran');\n"})   # log.mjs: only console.log
    fx.commit("~/work/shop", {"scripts/team_hook.py": "print(2)\n", "scripts/tested_by_team.py": "print(3)\n"}, by="teammate")
    fx.commit("~/work/shop", {"tests/test_team_made.py": "import tested_by_team\n"}, by="teammate")                      # a test they didn't write
    fx.repo("~/work/team", {"pyproject.toml": "[project]\nname='t'\n", "hooks/team_hook.py": "print(1)\n", "tests/test_team_hook.py": "import team_hook\n"}, by="teammate")
    fx.settings("project", {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "python scripts/rule_hook.py"}]},         # counts: has a test
                                                      {"hooks": [{"type": "command", "command": "node scripts/gate.mjs"}]},          # counts: a test imports it
                                                      {"hooks": [{"type": "command", "command": "node scripts/log.mjs"}]},           # trap: a bare stem in a test
                                                      {"hooks": [{"type": "command", "command": "python scripts/tested_by_team.py"}]},   # trap: a teammate's script
                                                      {"hooks": [{"type": "command", "command": "sh scripts/other_hook.sh"}]}]}},    # trap: no test
                repo="~/work/shop")
    fx.settings("user", {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "python " + fx.p("~/work/team/hooks/team_hook.py")}]}]}})   # trap: teammate's
    fx.session("~/work/shop").typed("x")
    fx.session("~/work/team").typed("x")
    return {"value": 2}


# ====================================================================== runs-start-without-you
AWAY = 600   # seconds: the person is away when they type nothing for this long after leaving a run going


AGENT_STILL_WORKING = 180   # seconds: the agent must still be working this long after the person went quiet


def _came_back(typed_times, left_at, agent_times):
    """True when the person typed in the session after the run was left going, the message before it (or the moment they left)
    was at least AWAY seconds earlier, and the agent was working during that gap: an assistant message (a reply or a tool call)
    at least AGENT_STILL_WORKING seconds after the gap started. Typing every few minutes right after /loop is staying, not walking
    away; a gap with an idle agent (it finished in a minute, the person took a coffee) is not leaving a run going."""
    before = left_at
    for t in sorted(typed_times):
        if t <= left_at:
            continue
        if t - before >= AWAY and any(before + AGENT_STILL_WORKING <= a < t for a in agent_times):
            return True
        before = t
    return False


@check("runs-start-without-you", "L2", 0)
def leaves_run_going(lt):
    """Interactive sessions in which the person left a run going and came back to it later: they sent the session to the background
    (sessionKind bg, which Claude Code sets from the session's first typed prompt: a way of starting it) or typed /loop, and typed
    again in that session 10 minutes or more after, with the agent still working at least 3 minutes into that gap (_came_back). Moving a dev server
    aside with Ctrl+B (backgroundedByUser) is the opposite of walking away and is not read. A background call the model chose
    itself (run_in_background, Monitor, ScheduleWakeup, CronCreate) is not the person's act and is not read (the skeptic, 11-05).
    Counted per session."""
    typed_at = {}
    for m in lt.typed:
        if not m.get("sc") and lt.interactive(m["s"]):
            typed_at.setdefault(m["s"], []).append(m["t"])
    left = {}   # session -> (when they left a run going, how)
    for x in _raw(lt, r'"sessionKind":\s*"bg"'):
        if x["s"] in typed_at and not x["sc"] and x["r"].get("sessionKind") == "bg":
            left.setdefault(x["s"], (x["t"], "sent to background"))
    for c in lt.all_commands():
        if c["name"] == "/loop" and c["s"] in typed_at and c["s"] not in left:
            left[c["s"]] = (c["t"], "/loop")
    agent_at = {}
    for a in lt.assist:
        if a["s"] in left:
            agent_at.setdefault(a["s"], []).append(a["t"])
    sess = {k: v[1] for k, v in left.items() if _came_back(typed_at[k], v[0], agent_at.get(k, ()))}
    ev = [f"{k[:8]}: {v}, came back later" for k, v in list(sess.items())[:3]]
    return {"value": len(sess), "evidence": ev,
            "note": "a Claude Code version without sessionKind reads 'not seen', not 0; timers and background calls the model starts on its own are left out; "
                    "a session counts only with a typed message 10+ minutes after the last one since the run was left going, and only when the agent was "
                    "still working 3+ minutes into that gap (an idle agent is a coffee break, not a run left going)"}


@_fixture("runs-start-without-you", "L2", 0)
def _(fx):
    def ctrl_b(s):   # the person pressed Ctrl+B on a running command: Claude Code's result record (checked against real logs, 11-05)
        tid = s.call("Bash", {"command": "npm run dev"}, "started")
        s._write({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid + "b", "is_error": False,
                  "content": "Command was manually backgrounded by user with ID: bx1. Output is being written to: /tmp/x"}]},
                  "toolUseResult": {"stdout": "", "stderr": "", "interrupted": False, "backgroundTaskId": "bx1", "backgroundedByUser": True}})
    def bg(s, text="carry on"):   # Claude Code sets sessionKind bg from the session's first typed prompt: call this first
        s._write({"type": "user", "message": {"role": "user", "content": text}, "origin": {"kind": "human"}, "promptSource": "typed", "sessionKind": "bg"})
    s = fx.session("~/work/shop", entrypoint="claude-vscode")                                        # trap: Ctrl+B, and they came back (moved aside, not away)
    s.typed("start the dev server"); ctrl_b(s); s.t += 1800; s.typed("is it up?")
    d = fx.session("~/work/shop", entrypoint="claude-desktop")                                       # counts: /loop, typed again 20 min later
    d.typed("hi"); d.command("/loop", "5m check ci"); d.t += 400; d.call("Bash", {"command": "gh run list"}, "ok"); d.t += 800; d.typed("what did the loop find?")
    c = fx.session("~/work/shop", entrypoint="cli")                                                  # counts: sent to background, back 15 min later
    bg(c); c.t += 400; c.call("Bash", {"command": "npm test"}, "ok"); c.t += 500; c.typed("how did it go?")
    c2 = fx.session("~/work/shop", entrypoint="cli")                                                 # trap: sent to background, never came back
    bg(c2)
    c3 = fx.session("~/work/shop", entrypoint="cli")                                                 # trap: sent to background, next message 3 minutes later
    bg(c3); c3.t += 180; c3.typed("and the tests?")
    c4 = fx.session("~/work/shop", entrypoint="cli")                                                 # trap: sent to background, back after 15 minutes, but the agent finished in 1 minute
    bg(c4); c4.t += 50; c4.call("Bash", {"command": "npm test"}, "ok"); c4.t += 900; c4.typed("how did it go?")
    c5 = fx.session("~/work/shop", entrypoint="claude-vscode")                                       # trap: /loop, a coffee break (agent idle), back after 20 minutes
    c5.typed("hi"); c5.command("/loop", "5m check ci"); c5.t += 1200; c5.typed("what did the loop find?")
    l = fx.session("~/work/shop", entrypoint="claude-vscode")                                        # trap: /loop, then typing every 5 minutes (stayed)
    l.typed("hi"); l.command("/loop", "5m check ci")
    for _ in range(6):
        l.t += 300; l.typed("anything yet?")
    m = fx.session("~/work/shop", entrypoint="claude-vscode")                                        # trap: only runs the model chose
    m.typed("build it"); m.call("Bash", {"command": "docker build .", "run_in_background": True}, "started")
    m.call("ScheduleWakeup", {"delaySeconds": 120, "prompt": "check ci", "reason": "ci"}, "ok"); m.call("Monitor", {"command": "tail -f x"}, "ok")
    m.call("CronCreate", {"cron": "0 9 * * *", "prompt": "x", "recurring": False}, "ok"); m.t += 1800; m.typed("back")
    n = fx.session("~/work/shop", entrypoint="claude-vscode")                                        # trap: nothing left going
    n.typed("run tests"); n.call("Bash", {"command": "npm test"}, "ok")
    sa = fx.session("~/work/shop", entrypoint="claude-vscode")                                       # trap: only a sub-agent's Monitor
    sa.typed("look around"); sa.subagent("Explore", "watch", calls=[("Monitor", {"command": "tail -f x"}, "ok")])
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                                              # trap: headless
    h.typed("go"); h.call("Bash", {"command": "sleep 1", "run_in_background": True}, "ok"); ctrl_b(h); bg(h); h.t += 1800; h.typed("again")
    return {"value": 2}


_LAUNCH = re.compile(r"claude\w*</string>\s*<string>(?:-p|--print)</string>")


def _starts_claude(text):
    """The text starts Claude headless: a line with `claude -p` or the package run the same way, the list form, the Claude Code
    action, or a launchd argument list. An install line (`npm i -g @anthropic-ai/claude-code`, `pip install claude-agent-sdk`) does not."""
    return any(not INSTALL.search(l) and RUN_CLI.search(l) for l in _logical_lines(text)) or bool(_LAUNCH.search(_strip_comments(text)))


def _scheduled(lt):
    """[(label, text)]: what the person set up once that starts Claude headless: launchd agents, systemd units, cron lines (read when
    this is the real home), ~/.claude/scheduled_tasks.json tasks, and CI workflow files in their repos, when the person wrote the
    workflow."""
    cache = _cache(lt)
    if "scheduled" in cache:
        return cache["scheduled"]
    found = []
    import glob as _g
    for pat in ("Library/LaunchAgents/*.plist", ".config/systemd/user/*.service", ".config/systemd/user/*.timer"):
        for p in _g.glob(os.path.join(lt.home, pat)):
            text = _read(p)
            if _starts_claude(text):
                found.append((f"{pat.split('/')[-2]}: {os.path.basename(p)}", text))
    if os.path.realpath(lt.home) == os.path.realpath(os.path.expanduser("~")):
        try:
            tab = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            tab = ""
        found += [(f"cron: {_short(l, 50)}", l) for l in tab.splitlines() if not l.lstrip().startswith("#") and _starts_claude(l)]
    tasks = _json(os.path.join(lt.claude, "scheduled_tasks.json")).get("tasks")
    found += [(f"scheduled task {i + 1}", json.dumps(t)) for i, t in enumerate(tasks if isinstance(tasks, list) else [])]
    for repo in lt.repos:
        for rel in _tracked(lt, repo):
            if re.match(r"\.github/workflows/[^/]+\.ya?ml$", rel) or rel == ".gitlab-ci.yml":
                text = _read(os.path.join(repo, rel))
                if _starts_claude(text) and _mine(lt, os.path.join(repo, rel)):
                    found.append((f"ci: {rel}", text))
    cache["scheduled"] = found
    return found


@check("runs-start-without-you", "L3", 0)
def schedule_starts_agent(lt):
    """Things the person set up once that start Claude headless: launchd agents, systemd units, cron lines (read when this
    is the real home), ~/.claude/scheduled_tasks.json tasks, and CI workflow files in their repos that run `claude -p`
    or the Claude Code action, when the person wrote the workflow."""
    found = [label for label, _ in _scheduled(lt)]
    return {"value": len(found), "evidence": found[:3],
            "note": "Windows Task Scheduler and schedules on a server or a hosted runner are not visible; /loop wake-ups are not schedules"}


@_fixture("runs-start-without-you", "L3", 0)
def _(fx):
    fx.file("~/Library/LaunchAgents/com.me.nightly.plist",
            "<plist><dict><key>ProgramArguments</key><array><string>/usr/local/bin/claude</string><string>-p</string><string>triage</string></array></dict></plist>")   # counts
    fx.file("~/Library/LaunchAgents/com.me.backup.plist", "<plist><dict><key>ProgramArguments</key><array><string>/usr/bin/rsync</string></array></dict></plist>")    # trap
    fx.file("~/.config/systemd/user/review.service", "[Service]\nExecStart=/usr/bin/claude -p \"review open PRs\"\n")                                                  # counts
    fx.file("~/.claude/scheduled_tasks.json", json.dumps({"tasks": [{"id": "a", "cron": "0 9 * * *", "prompt": "digest"}]}))                                          # counts
    fx.repo("~/work/shop", {".github/workflows/triage.yml": "on: [issues]\njobs:\n  t:\n    steps:\n      - run: claude -p \"triage this issue\"\n",             # counts
                            ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: npm test\n",                                                   # trap: no agent
                            ".github/workflows/setup.yml": "jobs:\n  t:\n    steps:\n      - run: npm i -g @anthropic-ai/claude-code\n"})                      # trap: an install
    fx.repo("~/work/team", {".github/workflows/agent.yml": "jobs:\n  t:\n    steps:\n      - run: claude -p \"x\"\n"}, by="teammate")                           # trap: not theirs
    s = fx.session("~/work/shop", entrypoint="claude-vscode")
    s.typed("loop it"); s.system("scheduled_task_fire", taskKind="loop", cronKind="loop", taskId="aa")                                                            # trap: /loop
    fx.session("~/work/team").typed("x")
    return {"value": 4}


def _headless_first(lt):
    """Each headless run's first prompt. Claude Code writes those with promptSource "sdk", never "typed" (lt.headless;
    the skeptic, 11-05: reading lt.typed found ~0 on any real laptop)."""
    out = {}
    for m in lt.headless:
        if not lt.interactive(m["s"]) and m["s"] not in out:
            out[m["s"]] = m
    return out


def _own_agent_text(lt):
    """The text of everything the person wrote that starts Claude headless (what _scheduled finds, and the declared entries and tracked
    scripts that start an agent), whitespace collapsed."""
    cache = _cache(lt)
    if "own_agent_text" not in cache:
        texts = [t for _, t in _scheduled(lt)]
        for e in declared_runners(lt):
            texts += _runner_texts(lt, e)
        cache["own_agent_text"] = "\n".join(" ".join(t.split()) for t in texts)
    return cache["own_agent_text"]


def _prompt_is_own(lt, prompt):
    """The prompt of a headless run sits, quoted whole, in a file the person wrote that starts Claude headless. The log does not say
    what started a headless run: an installed plugin's or a hook's own `claude -p` writes the same record (the skeptic, 11-05: 8,590
    capped plugin runs). A prompt found in the person's own script is theirs; one found nowhere is not placed."""
    memo = _cache(lt).setdefault("own_prompt", {})
    p = " ".join((prompt or "").split())
    if p not in memo:
        text = _own_agent_text(lt)
        memo[p] = bool(p and text and re.search(r"(?<=[\"'>])" + re.escape(p) + r"(?=[\"'<])", text))
    return memo[p]


def _own_first(lt):
    """Headless runs whose first prompt is in one of the person's own scripts or schedules."""
    cache = _cache(lt)
    if "own_first" not in cache:
        cache["own_first"] = {sid: m for sid, m in _headless_first(lt).items() if _prompt_is_own(lt, m["text"])} if _own_agent_text(lt) else {}
    return cache["own_first"]


def _repeats(lt):
    """The person's own headless runs (_own_first) whose first prompt (its first 100 characters) opens at least two of them: a saved prompt run again."""
    groups = {}
    for sid, m in _own_first(lt).items():
        groups.setdefault(m["text"][:100], []).append(sid)
    return {k: v for k, v in groups.items() if len(v) >= 2}


@check("runs-start-without-you", "L3", 1)
def runs_again_and_again(lt):
    """Headless runs (entrypoint sdk-*: a program started them) of a prompt the person's own script or schedule holds, that another
    such run also began with. Runs of an installed plugin or a hook (a prompt in no file of theirs) are left out."""
    rep = _repeats(lt)
    n = sum(len(v) for v in rep.values())
    return {"value": n, "evidence": [f"{len(v)}x {_short(k, 60)}" for k, v in sorted(rep.items(), key=lambda kv: -len(kv[1]))[:3]],
            "note": "what started the run (cron, hook, an app) is not in the record; a prompt read from a file at run time, or built from "
                    "variables, can't be matched to the script and is not counted"}


def _own_runs_world(fx):
    """A package.json script of the person's that runs a saved prompt, and the runs it made."""
    fx.repo("~/work/shop", {"package.json": json.dumps({"scripts": {"triage": 'claude -p "Triage the open issues in the tracker and write a summary file"',
                                                                    "once": 'claude -p "Rename the foo module"'}})})


@_fixture("runs-start-without-you", "L3", 1)
def _(fx):
    _own_runs_world(fx)
    for i in range(3):
        h = fx.session("~/work/shop", entrypoint="sdk-cli", start=time.time() - 3 * DAY + i * 7200)
        h.typed("Triage the open issues in the tracker and write a summary file")                  # counts x3: their script's prompt
    for i in range(3):                                                                              # trap: an installed plugin's runs (the same prompt each time)
        p = fx.session("~/work/shop", entrypoint="sdk-cli", start=time.time() - 2 * DAY + i * 600)
        p.typed("Summarise this session for the memory store"); p.attachment("budget_usd", used=0, total=2, remaining=2)
    one = fx.session("~/work/shop", entrypoint="sdk-cli")                                           # trap: their prompt, but only once
    one.typed("Rename the foo module")
    for ep in ("claude-vscode", "claude-vscode"):                                                   # trap: the person types the same thing twice
        fx.session("~/work/shop", entrypoint=ep).typed("continue")
    return {"value": 3}


@check("runs-start-without-you", "L3", 2)
def saved_prompt_read_later(lt):
    """Files a repeated headless prompt (of the person's own script, as L3#1) wrote (Write/Edit) that a later interactive session opened
    (Read/Grep/Glob, or a shell command naming the path)."""
    rep_sids = {sid for v in _repeats(lt).values() for sid in v}
    wrote = {}
    for c in lt.calls:
        if c["s"] in rep_sids and c["name"] in ("Write", "Edit", "MultiEdit"):
            p = _norm(_input(c).get("file_path") or "")
            if p:
                wrote[p] = min(wrote.get(p, c["t"]), c["t"])
    read = set()
    for c in lt.calls:
        if not lt.interactive(c["s"]) or c["name"] not in ("Read", "Grep", "Glob", "Bash"):
            continue
        txt = _norm(c.get("input") or "")
        for p, t in wrote.items():
            if c["t"] > t and p in txt:
                read.add(p)
    return {"value": len(read), "evidence": [os.path.basename(p) for p in sorted(read)[:3]],
            "note": "an output sent by mail or a message instead of a file is not seen"}


@_fixture("runs-start-without-you", "L3", 2)
def _(fx):
    _own_runs_world(fx)
    out = fx.p("~/work/shop/reports/triage.md")
    other = fx.p("~/work/shop/reports/oneoff.md")
    plug = fx.p("~/work/shop/reports/plugin.md")
    for i in range(2):
        h = fx.session("~/work/shop", entrypoint="sdk-cli", start=time.time() - 3 * DAY + i * 3600)
        h.typed("Triage the open issues in the tracker and write a summary file"); h.call("Write", {"file_path": out, "content": "x"})
    for i in range(2):                                                                               # trap: a plugin's repeated runs that wrote a file
        h = fx.session("~/work/shop", entrypoint="sdk-cli", start=time.time() - 3 * DAY + i * 3600 + 60)
        h.typed("Summarise this session for the memory store"); h.call("Write", {"file_path": plug, "content": "x"})
    one = fx.session("~/work/shop", entrypoint="sdk-cli", start=time.time() - 3 * DAY)               # a one-off prompt that writes a file
    one.typed("Rename the foo module"); one.call("Write", {"file_path": other, "content": "x"})
    early = fx.session("~/work/shop", entrypoint="claude-vscode", start=time.time() - 4 * DAY)       # trap: read before it was written
    early.typed("look"); early.call("Read", {"file_path": out})
    s = fx.session("~/work/shop", entrypoint="claude-desktop", start=time.time() - 1 * DAY)
    s.typed("what did the nightly find?")
    s.call("Read", {"file_path": out})                                                                 # counts
    s.call("Read", {"file_path": other})                                                               # trap: not a saved prompt
    s.call("Read", {"file_path": plug})                                                                # trap: a plugin's output
    s.call("Read", {"file_path": fx.p("~/work/shop/README.md")})                                       # trap: not written by a run
    return {"value": 1}


@check("runs-start-without-you", "L4", 1)
def limits_on_headless(lt):
    """Things the person wrote that start Claude headless and carry a limit in the command or the SDK options: a budget, a turn cap, or
    a tool list (--max-budget-usd, --max-turns, --allowedTools, --disallowedTools, --tools; maxTurns, allowed_tools...). Read from their
    scripts, schedules and CI files. A budget_usd attachment is not read: every capped run writes it, plugins' and hooks' own runs too
    (the skeptic, 11-05: 8,590 real), and Claude Code's default permission mode is no limit."""
    got = [label for label, text in _scheduled(lt) if LIMIT_FLAGS.search("\n".join(_logical_lines(text)))]
    for e in declared_runners(lt):
        if any(LIMIT_FLAGS.search("\n".join(_logical_lines(t))) for t in _runner_texts(lt, e)):
            got.append(f"{e['kind']}: {e['name']}")
    got = list(dict.fromkeys(got))
    return {"value": len(got), "evidence": got[:3],
            "note": "a limit set in a shell alias or an app is not seen; what the limit is is not judged"}


@_fixture("runs-start-without-you", "L4", 1)
def _(fx):
    fx.repo("~/work/shop", {
        "package.json": json.dumps({"scripts": {"capped": 'claude -p "triage" --max-turns 5 --max-budget-usd 2', "open": 'claude -p "triage everything"',
                                                "sdk": "node scripts/sdk.mjs", "mode": 'claude -p "x" --permission-mode dontAsk'}}),
        "scripts/sdk.mjs": 'import { query } from "@anthropic-ai/claude-agent-sdk";\nawait query({ prompt: "p", options: { maxTurns: 3 } });\n',
        "Makefile": 'review:\n\tclaude -p "review the diff" \\\n\t  --allowedTools "Read,Grep"\n'})
    fx.file("~/Library/LaunchAgents/com.me.nightly.plist",
            "<plist><dict><key>ProgramArguments</key><array><string>claude</string><string>-p</string><string>t</string>"
            "<string>--max-turns</string><string>4</string></array></dict></plist>")
    fx.repo("~/work/team", {"package.json": json.dumps({"scripts": {"agent": 'claude -p "team" --max-turns 9'}})}, by="teammate")   # trap: a teammate's
    fx.session("~/work/shop").typed("x"); fx.session("~/work/team").typed("x")
    a = fx.session("~/work/shop", entrypoint="sdk-cli", permission_mode="bypassPermissions")           # trap: a capped run of a plugin or a hook
    a.typed("Summarise this session"); a.attachment("budget_usd", used=0, total=2, remaining=2)
    b = fx.session("~/work/shop", entrypoint="sdk-cli", permission_mode="dontAsk")                      # trap: a mode is no limit
    b.typed("run 2")
    c = fx.session("~/work/shop", entrypoint="sdk-cli"); c.typed("run 3"); c.attachment("max_turns_reached", maxTurns=1, turnCount=2)   # trap: a plugin's capped run
    return {"value": 4}   # capped, sdk (maxTurns), the Makefile's review (--allowedTools), the plist (--max-turns)


# ====================================================================== put-rules-where-they-apply
def _rule_files_by_repo(lt):
    cache = _cache(lt)
    if "rf" not in cache:
        by = {}
        for scope, p in lt.instruction_files():
            if scope in ("root", "folder"):
                by.setdefault(lt.repo_of(p) or os.path.dirname(p), set()).add(os.path.realpath(p))
        cache["rf"] = by
    return cache["rf"]


@check("put-rules-where-they-apply", "L2", 2)
def one_rule_file_for_everything(lt):
    """Interactive sessions that started with exactly one project/local rule file loaded (first `instructions` attachment)
    in a repo that holds no other rule file anywhere (no subfolder CLAUDE.md / AGENTS.md, no .claude/rules)."""
    first = {}
    for a in _atts(lt, "instructions"):
        if _live(lt, a) and a["s"] not in first:
            first[a["s"]] = a
    hit = []
    for sid, a in first.items():
        proj = [f for f in a.get("files") or [] if isinstance(f, dict) and f.get("type") in ("Project", "Local")]
        cwd = (lt.sessions.get(sid) or {}).get("cwd")
        repo = lt.repo_of(_main_checkout(lt, cwd)) if cwd else None
        if len(proj) == 1 and repo and len(_rule_files_by_repo(lt).get(repo, ())) == 1:
            hit.append((sid, repo))
    return {"value": len(hit), "evidence": sorted({os.path.basename(r) for _, r in hit})[:3],
            "note": "one file in a small repo can be right; a hint, not proof that everything is in one file"}


@_fixture("put-rules-where-they-apply", "L2", 2)
def _(fx):
    fx.repo("~/work/solo", {"CLAUDE.md": "everything"})
    fx.repo("~/work/split", {"CLAUDE.md": "root", "services/api/CLAUDE.md": "api"})
    one = [{"path": fx.p("~/work/solo/CLAUDE.md"), "type": "Project", "content": "everything"}]
    for ep in ("claude-vscode", "claude-desktop"):                                            # counts twice
        s = fx.session("~/work/solo", entrypoint=ep); s.typed("hi"); s.attachment("instructions", files=one)
    s = fx.session("~/work/split", entrypoint="claude-vscode"); s.typed("hi")                  # trap: subfolder files load later
    s.attachment("instructions", files=[{"path": fx.p("~/work/split/CLAUDE.md"), "type": "Project", "content": "root"}])
    s = fx.session("~/work/solo", entrypoint="claude-vscode"); s.typed("hi")                   # trap: two files loaded
    s.attachment("instructions", files=one + [{"path": fx.p("~/work/solo/CLAUDE.local.md"), "type": "Local", "content": "mine"}])
    h = fx.session("~/work/solo", entrypoint="sdk-cli"); h.typed("go"); h.attachment("instructions", files=one)   # trap: headless
    return {"value": 2}


@check("put-rules-where-they-apply", "L3", 0)
def subfolder_rule_files_loaded(lt):
    """Distinct rule files Claude Code loaded in the middle of a session (nested_memory attachment: read when work reached
    that folder) that the person wrote AND that sit below their project's root (or are `.claude/rules` files). The record
    alone is not enough: a repo's root file arrives the same way when the session started above the repo, and one rule
    file per repo is not a scoped rule (lt.below_project_root, 11-11)."""
    paths = {}
    for a in _atts(lt, "nested_memory"):
        p = a.get("path")
        if p and _live(lt, a) and lt.below_project_root(p):
            paths.setdefault(p, a)
    _warm_authors(lt, paths)
    mine = [p for p in paths if _mine(lt, p)]
    return {"value": len(mine), "evidence": [os.path.relpath(p, lt.repo_of(p) or "/") for p in mine[:3]],
            "note": "files in a deleted worktree can't be placed as theirs; a rule file loaded is not a rule that applied"}


@_fixture("put-rules-where-they-apply", "L3", 0)
def _(fx):
    fx.repo("~/work/shop", {"CLAUDE.md": "root", "services/api/CLAUDE.md": "api", "services/web/CLAUDE.md": "web", "services/ops/CLAUDE.md": "ops",
                            ".claude/rules/billing.md": "---\npaths: services/billing/**\n---\nbilling"})
    fx.repo("~/work/team", {"legacy/AGENTS.md": "legacy"}, by="teammate")
    for i in range(2):                                                                         # the same file twice: once
        s = fx.session("~/work/shop", entrypoint="claude-vscode"); s.typed("hi")
        s.attachment("nested_memory", path=fx.p("~/work/shop/services/api/CLAUDE.md"), content={"path": "x", "type": "Project", "content": "api"})
    s = fx.session("~/work/shop", entrypoint="claude-desktop"); s.typed("hi")
    s.attachment("nested_memory", path=fx.p("~/work/shop/services/web/CLAUDE.md"), content={"path": "x", "type": "Project", "content": "web"})
    s.attachment("nested_memory", path=fx.p("~/work/shop/.claude/rules/billing.md"), content={"path": "x", "type": "Project", "content": "billing"})  # a rules file counts
    s.attachment("nested_memory", path=fx.p("~/work/team/legacy/AGENTS.md"), content={"path": "x", "type": "Project", "content": "legacy"})   # trap: teammate's
    s.attachment("nested_memory", path=fx.p("~/work/gone/x/CLAUDE.md"), content={"path": "x", "type": "Project", "content": "gone"})            # trap: removed
    h = fx.session("~/work/shop", entrypoint="sdk-cli"); h.typed("go")                         # trap: headless
    h.attachment("nested_memory", path=fx.p("~/work/shop/services/ops/CLAUDE.md"), content={"path": "x", "type": "Project", "content": "ops"})
    fx.session("~/work/team").typed("x")
    # trap (11-11): a session started in the home folder reaches two repos, each with only its ROOT rule file. Claude
    # Code records both as nested_memory, but one rule file per repo is that repo's always-loaded file, not a scoped rule
    fx.repo("~/work/blog", {"CLAUDE.md": "blog"}); fx.repo("~/work/tool", {"AGENTS.md": "tool", ".claude/CLAUDE.md": "tool"})
    u = Session(fx, fx.home, "claude-vscode"); u.typed("hi")                                  # no repo around the home folder
    for rel in ("blog/CLAUDE.md", "tool/AGENTS.md", "tool/.claude/CLAUDE.md"):
        u.attachment("nested_memory", path=fx.p("~/work/" + rel), content={"path": "x", "type": "Project", "content": "root"})
    fx.file("~/notes/CLAUDE.md", "notes")                                                     # trap: a plain folder's own file, no repo
    u.attachment("nested_memory", path=fx.p("~/notes/CLAUDE.md"), content={"path": "x", "type": "Project", "content": "notes"})
    return {"value": 3}


def _skill_file(lt, name, cwd):
    """Where Claude Code loads a skill of this name from: the session's project folder, else the user folder.
    A 'plugin:skill' name is a plugin's, never a file of the person's."""
    if ":" in name or not cwd:
        return None
    for base in dict.fromkeys([cwd, _main_checkout(lt, cwd), lt.claude]):
        p = os.path.join(base, "skills" if base == lt.claude else os.path.join(".claude", "skills"), name, "SKILL.md")
        if os.path.exists(p):
            return p
    return None


def _locked_skill_names(lt):
    """Names a skills installer recorded in a lock file (a repo's skills-lock.json, ~/.agents/.skill-lock.json): installed, whoever
    committed them."""
    cache = _cache(lt)
    if "locked_skills" not in cache:
        names = set()
        paths = [os.path.join(r, "skills-lock.json") for r in lt.repos]
        paths += [os.path.join(lt.home, ".agents", ".skill-lock.json"), os.path.join(lt.claude, "skills-lock.json"),
                  os.path.join(lt.claude, ".skill-lock.json")]
        for p in paths:
            sk = _json(p).get("skills")
            if isinstance(sk, dict):
                names |= set(sk)
        cache["locked_skills"] = names
    return cache["locked_skills"]


def _plugin_skill_copies(lt):
    """{skill folder name: [SKILL.md paths]} of every plugin the laptop installed or cloned (the plugin cache and the marketplaces)."""
    cache = _cache(lt)
    if "plugin_skills" not in cache:
        out = {}
        for sub in ("cache", "marketplaces"):
            for root, dirs, files in os.walk(os.path.join(lt.claude, "plugins", sub)):
                dirs[:] = [d for d in dirs if d not in ("node_modules", ".git")]
                if "SKILL.md" in files:
                    out.setdefault(os.path.basename(root), []).append(os.path.join(root, "SKILL.md"))
        cache["plugin_skills"] = out
    return cache["plugin_skills"]


def _installed_skill(lt, skill_file, name):
    """The skill is an installed one committed into a repo. Two extra "installed" receipts read off files, beside lt.owner's: a skills
    installer lists it in a lock file (any a repo holds), or a plugin or marketplace the laptop installed ships a SKILL.md with the same bytes (the skeptic, 11-05: a skill installed into the repo and committed read as his)."""
    if name in _locked_skill_names(lt):
        return True
    mine = _read(os.path.realpath(skill_file))
    return any(_read(c) == mine for c in _plugin_skill_copies(lt).get(name, ()))


MIN_OWN_SKILLS = 3      # distinct own skills the model loaded because the task matched
MIN_SKILL_SESSIONS = 3  # sessions those loads happened in
NAMED_WINDOW = 30       # seconds before the prompt record in which Claude Code may write its skill_mention for that prompt


def _named_by_person(lt):
    """{session: [(time, skill name)]}: the moments the person named a skill themselves, which Claude Code records two ways: a
    skill_mention attachment (they typed /name inside a message, and Claude Code tells the model to call the Skill tool), and a
    slash command /name."""
    cache = _cache(lt)
    if "named_skills" not in cache:
        out = {}
        for a in _atts(lt, "skill_mention"):
            if a.get("skillName"):
                out.setdefault(a["s"], []).append((a["t"], a["skillName"]))
        for c in lt.all_commands():
            out.setdefault(c["s"], []).append((c["t"], c["name"].lstrip("/")))
        cache["named_skills"] = out
    return cache["named_skills"]


def _prompt_times(lt):
    cache = _cache(lt)
    if "prompt_times" not in cache:
        out = {}
        for m in lt.typed:
            out.setdefault(m["s"], []).append(m["t"])
        for c in lt.all_commands():
            out.setdefault(c["s"], []).append(c["t"])
        cache["prompt_times"] = {k: sorted(v) for k, v in out.items()}
    return cache["prompt_times"]


def _typed_by_name(lt, c, name):
    """The Skill call c came right after the person named this skill themselves, in the same turn (their last prompt before the
    call): it loaded because they asked for it by name, not because the task matched."""
    prompts = [t for t in _prompt_times(lt).get(c["s"], ()) if t <= c["t"]]
    turn_start = (prompts[-1] if prompts else c["t"]) - NAMED_WINDOW
    return any(turn_start <= t <= c["t"] and n == name for t, n in _named_by_person(lt).get(c["s"], ()))


@check("put-rules-where-they-apply", "L3", 1)
def own_skills_called(lt):
    """Skills of their own that the model loaded because the task matched: Skill tool calls (the model's act) in interactive
    sessions whose skill file, found where Claude Code loads it for that session, is theirs by the one ownership rule (_mine;
    a link is followed; joined by file, never by bare name), leaving out a call that came in the turn where the person named the
    skill themselves (a skill_mention or a slash command /name). Positive only with at least 3 distinct own skills loaded that
    way, on at least 3 sessions: one skill called once shows a skill, not rules put where they apply. The value is the number of
    distinct skills when both bars are met, else 0 (the evidence line says how far it got)."""
    files, sessions = {}, set()
    for c in lt.calls:
        if c["name"] != "Skill" or c["sc"] or not lt.interactive(c["s"]):
            continue
        name = _input(c).get("skill") or ""
        f = _skill_file(lt, name, (lt.sessions.get(c["s"]) or {}).get("cwd"))
        if not (f and _mine(lt, f) and not _installed_skill(lt, f, name)) or _typed_by_name(lt, c, name):
            continue
        files.setdefault(os.path.realpath(f), name)
        sessions.add(c["s"])
    met = len(files) >= MIN_OWN_SKILLS and len(sessions) >= MIN_SKILL_SESSIONS
    return {"value": len(files) if met else 0,
            "evidence": [f"{len(files)} own skills loaded because the task matched, on {len(sessions)} sessions (needs {MIN_OWN_SKILLS} and {MIN_SKILL_SESSIONS})"]
                        + sorted(set(files.values()))[:3],
            "note": "a Skill call by the model counts when the person did not name the skill that turn; a skill named in plain words in a "
                    "prompt can't be told apart; a skill nobody committed is theirs, and an installed skill is left out when Claude Code's "
                    "records, a skills lock or an installed plugin's copy show it; dynamic_skill / invoked_skills attachments only list names, so they are not read"}


@_fixture("put-rules-where-they-apply", "L3", 1)
def _(fx):
    fx.repo("~/work/shop", {".claude/skills/release/SKILL.md": "release steps", ".claude/skills/frontend-design/SKILL.md": "from the registry",
                            ".claude/skills/brainstorm/SKILL.md": "brainstorm steps from a plugin",
                            "skills-lock.json": json.dumps({"version": 1, "skills": {"frontend-design": {"source": "acme/skills"}}})})
    fx.file("~/.claude/plugins/cache/market/ideas/1.0.0/skills/brainstorm/SKILL.md", "brainstorm steps from a plugin")
    fx.repo("~/work/shop", {".claude/skills/team-style/SKILL.md": "style"}, by="teammate", message="team")
    fx.repo("~/code/skillrepo", {"triage/SKILL.md": "triage steps"})
    os.makedirs(fx.p("~/.claude/skills"), exist_ok=True)
    os.symlink(fx.p("~/code/skillrepo/triage"), fx.p("~/.claude/skills/triage"))             # a user skill linked into their repo
    fx.file("~/.agents/skills/installed/SKILL.md", "from a registry")
    os.symlink(fx.p("~/.agents/skills/installed"), fx.p("~/.claude/skills/installed"))         # trap: in the skills lock, linked into ~/.claude/skills
    fx.file("~/.agents/.skill-lock.json", json.dumps({"version": 3, "skills": {"installed": {"source": "acme/skills"}}}))
    fx.file("~/.claude/skills/scratch/SKILL.md", "scratch steps")                              # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.file("~/.claude/skills/notes/SKILL.md", "notes steps")                                  # trap below: only ever typed by name
    fx.file("~/.claude/skills/audit/SKILL.md", "audit steps")                                  # trap below: only ever typed by name
    fx.file("~/.claude/skills/vendor/SKILL.md", "---\nname: vendor\nlicense: MIT\nversion: 2.1.0\n---\nvendor steps\n")   # trap below: unpacked in one burst
    fx.file("~/.claude/skills/vendor/references/x.md", "vendor reference")
    fx.repo("~/code/other", {"README.md": "x"})
    s = fx.session("~/work/shop", entrypoint="claude-vscode"); s.typed("ship")
    s.call("Skill", {"skill": "release"}); s.call("Skill", {"skill": "release"})                # counts: release
    s.call("Skill", {"skill": "triage"})                                                        # counts: linked user skill
    s.call("Skill", {"skill": "team-style"})                                                    # trap: a teammate's
    s.call("Skill", {"skill": "polish:release"})                                                # trap: a plugin's, same bare name
    s.call("Skill", {"skill": "installed"})                                                     # trap: installed per the skills lock
    s.call("Skill", {"skill": "frontend-design"})                                               # trap: installed by a skills tool, committed by them
    s.call("Skill", {"skill": "brainstorm"})                                                    # trap: a plugin's skill copied into the repo and committed
    s2 = fx.session("~/work/shop", entrypoint="claude-desktop"); s2.typed("clean up the scratch notes")
    s2.call("Skill", {"skill": "scratch"})                                                      # counts: untracked, no receipt
    s3 = fx.session("~/work/shop", entrypoint="cli"); s3.typed("sort the new issues")
    s3.call("Skill", {"skill": "triage"})                                                       # counts: a second session of triage
    s3.call("Skill", {"skill": "vendor"})                                                       # trap: a vendor skill unpacked in one burst, untracked, publishing fields
    s4 = fx.session("~/work/shop", entrypoint="claude-vscode")                                  # traps: the person typed the skill's name
    s4.command("/notes"); s4.call("Skill", {"skill": "notes"})                                  # a slash command, then the Skill call
    s4.typed("now /audit the diff"); s4.attachment("skill_mention", skillName="audit"); s4.call("Skill", {"skill": "audit"})   # /audit typed inside a message
    o = fx.session("~/code/other", entrypoint="claude-desktop"); o.typed("ship")
    o.call("Skill", {"skill": "release"})                                                       # trap: same name, not this repo's skill
    h = fx.session("~/work/shop", entrypoint="sdk-cli"); h.typed("go"); h.call("Skill", {"skill": "release"})   # trap: headless
    return {"value": 3}      # release, triage, scratch on 3 sessions; notes and audit were typed by name


# ---- L3#2: deeper docs the agent opened on its own (progressive disclosure: a pointer that sends the agent to a doc)
MIN_FOLLOWED_DOCS = 3       # distinct docs opened on the agent's own, as L3#1's 3 skills
MIN_FOLLOWED_SESSIONS = 3   # sessions those docs were opened in, as L3#1's 3 sessions
DOC_SUFFIXES = (".md", ".mdx", ".txt", ".rst")
RULE_FILE_NAMES = {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md"}
FILE_WRITES = ("Edit", "Write", "MultiEdit", "NotebookEdit")
# a relative path in prose, a backtick or a markdown link target: slashes between names, or a bare name ending in a doc suffix.
# Not after @ (an import Claude Code loads itself), / . : ~ $ (an absolute path, a URL, home), nor inside a longer name.
_PATH_TOKEN = re.compile(r"(?<![\w@/.:~$%-])((?:\.{1,2}/)*[\w.-]+(?:/[\w.-]+)+|[\w-]+(?:\.[\w-]+)*\.(?:md|mdx|txt|rst))(?![\w@/-])")


def _is_pointed_doc(rel):
    """A doc the agent would fetch (rel: the path inside its repo): a doc suffix, whatever folder it is in (no list of folder names:
    that is an English word list); not another rule file (L3#0 counts those) and not a rule Claude Code loads itself."""
    parts = rel.lower().split("/")
    if parts[-1] in {n.lower() for n in RULE_FILE_NAMES} | {"skill.md"} or "/.claude/rules/" in "/" + rel.lower():
        return False
    return rel.lower().endswith(DOC_SUFFIXES)


def _pointed_files(text, bases, root):
    """{real path: as written} of the existing files inside `root` that `text` names by relative path (tried from each base)."""
    out = {}
    for m in _PATH_TOKEN.finditer(text or ""):
        tok = m.group(1).rstrip(".-")
        for b in bases:
            p = os.path.realpath(os.path.join(b, tok))
            if os.path.isfile(p) and p.startswith(root + os.sep):
                out.setdefault(p, tok)
                break
    return out


def _main_of(top):
    """The main checkout of the repo at `top`, read from its .git file when it is a linked worktree (no git run)."""
    try:
        m = re.match(r"gitdir:\s*(.+?)[\\/]\.git[\\/]worktrees[\\/]", open(os.path.join(top, ".git")).read())
    except OSError:
        m = None
    return os.path.realpath(m.group(1)) if m else top


def _doc_key(lt, real, top):
    """(key, shown): a doc's name across worktrees and checkouts (main checkout + path inside the repo `top`), and its path to show."""
    if not top:
        return real, "~/" + os.path.relpath(real, lt.home) if real.startswith(lt.home + os.sep) else real
    rel = os.path.relpath(real, top).replace(os.sep, "/")
    return _main_of(top) + "::" + rel, rel


def _said_by_person(lt):
    """{session: [(time, lowercased text, expanded command or None)]}: what the person typed, slash commands included, and what a
    slash command they typed expanded to (Claude Code's isMeta record after the command record: the person naming the doc by
    choosing that command). The third item is that command's name, so a skill being credited can leave its own expansion out."""
    cache = _cache(lt)
    if "said" not in cache:
        out = {}
        for m in lt.typed:
            out.setdefault(m["s"], []).append((m["t"], (m.get("text") or "").lower(), None))
        for c in lt.all_commands():
            out.setdefault(c["s"], []).append((c["t"], ((c.get("name") or "") + " " + (c.get("args") or "")).lower(), None))
        for e in lt.expansions:
            out.setdefault(e["s"], []).append((e["t"], (e.get("text") or "").lower(), e["cmd"]))
        cache["said"] = out
    return cache["said"]


def _names_of(lt, real, top):
    """What naming this file looks like in a message: its file name, or its path inside the repo without the suffix (an @-mention too)."""
    _, rel = _doc_key(lt, real, top)
    names = {os.path.basename(real).lower()}
    if "/" in rel:
        names.add(os.path.splitext(rel)[0].lower())
    return names


def _rule_loads(lt):
    """{(session, rule file): (time, text)}: the first time a rule file the person wrote was in Claude's context in a session
    they worked in: the start-of-session `instructions` record or the mid-session `nested_memory` record, with the text Claude Code
    recorded it with. A rule file is CLAUDE.md / CLAUDE.local.md / AGENTS.md or .claude/rules/*.md that sits in a repo."""
    seen = {}

    def add(a, path, text):
        if not path or not (os.path.basename(path) in RULE_FILE_NAMES or re.search(r"/\.claude/rules/.*\.md$", path)):
            return
        if _live(lt, a) and (a["s"], path) not in seen:
            seen[(a["s"], path)] = (a["t"], text if isinstance(text, str) else None)

    for a in _atts(lt, "instructions"):
        for f in a.get("files") or []:
            if isinstance(f, dict):
                add(a, f.get("path"), f.get("content"))
    for a in _atts(lt, "nested_memory"):
        c = a.get("content")
        add(a, a.get("path"), c.get("content") if isinstance(c, dict) else None)
    paths = {p for _, p in seen if os.path.exists(p)}
    _warm_authors(lt, paths)
    return {k: v for k, v in seen.items() if k[1] in paths and lt.toplevel(k[1]) and _mine(lt, k[1])}


def _skill_loads(lt):
    """[(session, time, skill name, SKILL.md)]: a skill of theirs entering the context, by the model's Skill call or the person's
    /name, found where Claude Code loads it for that session; their own by the one ownership rule, not an installed one."""
    cache = _cache(lt)
    found = {}

    def own(name, sid):
        cwd = (lt.sessions.get(sid) or {}).get("cwd")
        if ("own_skill", name, cwd) not in cache:
            f = _skill_file(lt, name, cwd)
            cache[("own_skill", name, cwd)] = f
        return cache[("own_skill", name, cwd)]

    events = [(c["s"], c["t"], _input(c).get("skill") or "") for c in lt.calls if c["name"] == "Skill" and lt.interactive(c["s"])]
    events += [(c["s"], c["t"], c["name"].lstrip("/")) for c in lt.all_commands() if lt.interactive(c["s"])]
    cands = [(s, t, n, own(n, s)) for s, t, n in events if n]
    cands = [x for x in cands if x[3]]
    _warm_authors(lt, {x[3] for x in cands})
    return sorted(((s, t, n, f) for s, t, n, f in cands if _mine(lt, f) and not _installed_skill(lt, f, n)), key=lambda x: x[1])


def _skill_text(folder):
    """Every text file in a skill's folder, lowercased and joined (capped): what the skill's files name."""
    out, size = [], 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git")]
        for f in files:
            if f.lower().endswith(DOC_SUFFIXES + (".py", ".sh", ".js", ".ts", ".json", ".yaml", ".yml")) and size < 3_000_000:
                t = _read(os.path.join(root, f), 300_000).lower()
                size += len(t); out.append(t)
    return "\n".join(out)


def _installed_skill_loads(lt):
    """{session: [(time, text of the skill's files)]}: an installed skill (not theirs: a receipt, a lock, a plugin's copy) entering the
    context by the model's Skill call or the person's /name, found where Claude Code loads it for that session."""
    cache = _cache(lt)
    if "installed_loads" not in cache:
        out = {}
        events = [(c["s"], c["t"], _input(c).get("skill") or "") for c in lt.calls if c["name"] == "Skill" and lt.interactive(c["s"])]
        events += [(c["s"], c["t"], c["name"].lstrip("/")) for c in lt.all_commands() if lt.interactive(c["s"])]
        for sid, t, name in events:
            f = _skill_file(lt, name, (lt.sessions.get(sid) or {}).get("cwd")) if name else None
            if not f:
                continue
            if ("inst_text", f) not in cache:
                real = os.path.realpath(f)
                cache[("inst_text", f)] = (_skill_text(os.path.dirname(real))
                                           if _installed_skill(lt, f, name) or lt.owner(real) == "installed" else None)
            if cache[("inst_text", f)] is not None:
                out.setdefault(sid, []).append((t, cache[("inst_text", f)]))
        cache["installed_loads"] = out
    return cache["installed_loads"]


def _followed_pointers(lt):
    """The pointers in the person's own rule files and skills that the agent followed on its own. Returns
    {"docs": {key: {"shown", "sessions", "from": {"rule file"|"skill"}}}, "skills": {name: [loads with references, loads where one was opened]}}."""
    cache = _cache(lt)
    if "followed" in cache:
        return cache["followed"]
    loads = []                  # (session, time, source, label, {real target path: the repo root it lies in, or None})
    memo = {}

    def rule_targets(path, text):
        if (path, text) not in memo:
            real = os.path.realpath(path)
            top = os.path.realpath(lt.toplevel(path))
            found = _pointed_files(text if text is not None else _read(real), [os.path.dirname(real), top], top)
            memo[(path, text)] = {p: top for p in found if _is_pointed_doc(os.path.relpath(p, top).replace(os.sep, "/")) and p != real}
        return memo[(path, text)]

    def skill_targets(f):
        if f not in memo:
            real = os.path.realpath(f)
            folder, top = os.path.dirname(real), lt.toplevel(real)
            top = os.path.realpath(top) if top else None
            tg = dict.fromkeys(_pointed_files(_read(real), [folder], folder), top)       # anything in the skill's own folder
            if top:
                tg.update({p: top for p in _pointed_files(_read(real), [folder, top], top)
                           if _is_pointed_doc(os.path.relpath(p, top).replace(os.sep, "/"))})
            memo[f] = {p: t_ for p, t_ in tg.items() if os.path.basename(p).lower() != "skill.md"}
        return memo[f]

    for (sid, path), (t, text) in _rule_loads(lt).items():
        tg = rule_targets(path, text)
        if tg:
            loads.append((sid, t, "rule file", path, tg))
    for sid, t, name, f in _skill_loads(lt):
        tg = skill_targets(f)
        if tg:
            loads.append((sid, t, "skill", name, tg))
    wanted = {l[0] for l in loads}
    basenames = {os.path.basename(p) for l in loads for p in l[4]}
    real_of = {}

    def real(p):
        if p not in real_of:
            real_of[p] = os.path.realpath(p)
        return real_of[p]

    reads, edited = {}, {}      # (session, real path) -> [times]; session -> {real path}
    for c in lt.calls:
        if c["s"] not in wanted or c["name"] not in ("Read",) + FILE_WRITES:
            continue
        d = _input(c)
        p = d.get("file_path") or d.get("notebook_path") or ""
        if os.path.basename(p) not in basenames:
            continue
        if c["name"] == "Read":
            if not c.get("err"):
                reads.setdefault((c["s"], real(p)), []).append(c["t"])
        else:
            edited.setdefault(c["s"], set()).add(real(p))
    docs, skills, names_of = {}, {}, {}
    ordered = sorted(loads, key=lambda l: l[1])
    for i, (sid, t, source, label, tg) in enumerate(ordered):
        until = next((l[1] for l in ordered[i + 1:] if l[0] == sid and l[2] == source and l[3] == label), float("inf"))
        opened = False
        for p, root in tg.items():
            if p in edited.get(sid, ()):
                continue                                   # read to edit it: not a doc fetched
            if p not in names_of:
                names_of[p] = _names_of(lt, p, root)
            names = names_of[p]
            said = [x for x in _said_by_person(lt).get(sid, ()) if not (source == "skill" and x[2] and x[2].lstrip("/") == label)]
            inst = _installed_skill_loads(lt).get(sid, ()) if source == "rule file" else ()
            for rt in reads.get((sid, p), ()):
                if t <= rt < until and not any(mt <= rt and any(n in txt for n in names) for mt, txt, _ in said) \
                        and not any(it <= rt and any(n in text for n in names) for it, text in inst):
                    key, shown = _doc_key(lt, p, root)
                    d = docs.setdefault(key, {"shown": shown, "sessions": set(), "from": set()})
                    d["sessions"].add(sid); d["from"].add(source)
                    opened = True
                    break
        if source == "skill":
            n = skills.setdefault(label, [0, 0])
            n[0] += 1; n[1] += opened
    cache["followed"] = {"docs": docs, "skills": skills}
    return cache["followed"]


@check("put-rules-where-they-apply", "L3", 2)
def deeper_docs_followed(lt):
    """Docs that a pointer in the person's own rule files or skills names, which the agent opened on its own.
    Pointer: a line in a CLAUDE.md / AGENTS.md / .claude/rules file of theirs (_mine; Claude Code recorded it in context) that names a
    doc (a file ending .md, .mdx, .txt or .rst; not another rule file) which exists in the repo, or a line in a skill of
    theirs (not installed) that names a file in its folder (references/, scripts/) or a doc in the repo. Each doc is keyed by its own
    repo and path, so a worktree's copy is the same doc. Followed on its own: after that rule file or skill was in the session's
    context, a Read of the target by the agent (main session or one of its sub-agents) when nothing the person said earlier in the
    session names the file (path, file name or @-mention) and the session never edits or writes it (reading to edit is not fetching a
    doc). What a slash command the person typed expanded to is the person naming (the isMeta record after the command record), except
    the expansion of the very skill being credited; and a rule file's pointer is not credited when an installed skill whose files
    name the same doc loaded earlier in that session. Positive only with at least 3 distinct docs on at least 3 sessions, as
    L3#1 asks 3 skills on 3 sessions: one doc opened once shows a doc, not pointers that work. The value is the number of distinct
    docs when both bars are met, else 0; the evidence says rule files and skills apart and, for skills that point to files, how
    often the agent opened one when the skill loaded."""
    got = _followed_pointers(lt)
    docs = got["docs"]
    sessions = set().union(*(d["sessions"] for d in docs.values())) if docs else set()
    met = len(docs) >= MIN_FOLLOWED_DOCS and len(sessions) >= MIN_FOLLOWED_SESSIONS
    by = lambda src: sum(1 for d in docs.values() if src in d["from"])
    withref = sum(n[0] for n in got["skills"].values())
    opened = sum(n[1] for n in got["skills"].values())
    top = sorted(docs.values(), key=lambda d: (-len(d["sessions"]), d["shown"]))
    return {"value": len(docs) if met else 0,
            "evidence": [f"{len(docs)} docs opened on the agent's own on {len(sessions)} sessions (needs {MIN_FOLLOWED_DOCS} and {MIN_FOLLOWED_SESSIONS}): "
                         f"{by('rule file')} from rule files, {by('skill')} from skills; skills that point to files loaded {withref}x, "
                         f"the agent opened one {opened}x"] + [d["shown"] for d in top[:2]],
            "note": "a pointer worded without a path isn't seen; a doc the agent read for another reason, or because the task is about it, looks "
                    "the same; a rule file or skill in a removed worktree can't be placed as theirs; a doc read through Bash isn't seen; "
                    "a doc named only in a skill's own expansion counts as followed"}


@_fixture("put-rules-where-they-apply", "L3", 2)
def _(fx):
    root = ("# Shop\nDeploys: see [the deploy guide](docs/deploy.md) before touching infra.\nRollbacks follow `docs/runbook.md`.\n"
            "Style rules are in docs/style.md; open it when you write UI.\nStart from docs/asked.md and docs/named.md when billing comes up.\n"
            "Billing lives in src/billing.py; docs/edit-me.md says how it is laid out.\nOld notes: docs/ghost.md. Sub rules: services/api/CLAUDE.md\n"
            "@docs/imported.md\nBefore a release read docs/nocontext.md\nShipping: docs/ship.md. Manifest: runbooks/deploy.yaml. Look: DESIGN.md\n")
    api = "API rules. Schema: docs/schema.md. Late notes: docs/late.md\n"
    fx.repo("~/work/shop", {"CLAUDE.md": root, "services/api/CLAUDE.md": api, "src/billing.py": "x = 1\n",
                            **{f"docs/{n}.md": n for n in ("deploy", "runbook", "style", "asked", "named", "edit-me", "imported", "nocontext", "ship")},
                            "runbooks/deploy.yaml": "steps: []", "DESIGN.md": "design", ".claude/commands/ship.md": "follow docs/ship.md",
                            "services/api/docs/schema.md": "schema", "services/api/docs/late.md": "late",
                            ".claude/skills/release/SKILL.md": "Run [the checklist](references/checklist.md), then `scripts/bump.sh`; hotfixes: references/hotfix.md.",
                            ".claude/skills/release/references/checklist.md": "steps", ".claude/skills/release/references/hotfix.md": "hotfix",
                            ".claude/skills/release/scripts/bump.sh": "echo",
                            ".claude/skills/frontend-design/SKILL.md": "Read references/rules.md; keep DESIGN.md current", ".claude/skills/frontend-design/references/rules.md": "rules",
                            "skills-lock.json": json.dumps({"version": 1, "skills": {"frontend-design": {"source": "acme/skills"}}})})
    fx.repo("~/work/team", {"CLAUDE.md": "See docs/guide.md\n", "docs/guide.md": "guide"}, by="teammate")             # trap: a teammate's rule file
    fx._git(fx.p("~/work/shop"), "worktree", "add", "-q", ".claude/worktrees/x", "-b", "x")                     # a linked worktree: its own CLAUDE.md and docs
    fx.file("~/.claude/skills/vendor/SKILL.md", "---\nname: vendor\nlicense: MIT\nversion: 2.1.0\n---\nRead references/x.md\n")   # trap: a vendor skill unpacked in one burst
    fx.file("~/.claude/skills/vendor/references/x.md", "vendor reference")
    shop = lambda rel: fx.p("~/work/shop/" + rel)
    inst = [{"path": shop("CLAUDE.md"), "type": "Project", "content": root}]
    rd = lambda s, rel: s.call("Read", {"file_path": shop(rel)}, "text")

    s1 = fx.session("~/work/shop", entrypoint="claude-vscode"); s1.attachment("instructions", files=inst)
    s1.typed("start with docs/asked.md"); rd(s1, "docs/asked.md")                                         # trap: the person named it by path
    s1.typed("then check named.md too"); rd(s1, "docs/named.md")                                         # trap: ... by file name
    rd(s1, "docs/deploy.md")                                                                             # counts: deploy
    rd(s1, "docs/imported.md"); rd(s1, "docs/ghost.md")                                                  # traps: an @import, a file that doesn't exist
    s1.call("Read", {"file_path": shop("docs/ghost.md")}, "File does not exist.", is_error=True)
    rd(s1, "src/billing.py"); s1.call("Edit", {"file_path": shop("src/billing.py"), "old_string": "1", "new_string": "2"})   # trap: source, edited
    rd(s1, "docs/edit-me.md"); s1.call("Edit", {"file_path": shop("docs/edit-me.md"), "old_string": "edit-me", "new_string": "x"})   # trap: a doc read to edit it
    rd(s1, "services/api/CLAUDE.md")                                                                     # trap: another rule file
    rd(s1, "services/api/docs/late.md")                                                                  # trap: read before its rule file was in context
    s1.attachment("nested_memory", path=shop("services/api/CLAUDE.md"), content={"path": "x", "type": "Project", "content": api})
    rd(s1, "services/api/docs/schema.md")                                                                # counts: schema (a subfolder's rule file)

    s2 = fx.session("~/work/shop", entrypoint="claude-desktop"); s2.attachment("instructions", files=inst); s2.typed("fix the deploy")
    rd(s2, "docs/deploy.md")                                                                             # counts: deploy again
    s2.subagent("Explore", "find the rollback steps", calls=[("Read", {"file_path": shop("docs/runbook.md")}, "text")])   # counts: runbook, by a sub-agent
    s2.call("Skill", {"skill": "release"}); rd(s2, ".claude/skills/release/references/checklist.md")      # counts: checklist (their own skill)
    s2.call("Skill", {"skill": "frontend-design"}); rd(s2, ".claude/skills/frontend-design/references/rules.md")   # trap: an installed skill
    rd(s2, "DESIGN.md")                                                                                  # trap: the rule file names it, but an installed skill that names it loaded first
    s2.call("Skill", {"skill": "vendor"}); s2.call("Read", {"file_path": fx.p("~/.claude/skills/vendor/references/x.md")}, "text")   # trap: unpacked in one burst

    s3 = fx.session("~/work/shop", entrypoint="cli"); s3.attachment("instructions", files=inst); s3.typed("update the page")
    rd(s3, "docs/style.md"); rd(s3, "docs/runbook.md")                                                   # counts: style; runbook again
    s3.call("Skill", {"skill": "release"})                                                               # a skill that loaded and nothing opened

    s6 = fx.session("~/work/shop/.claude/worktrees/x", entrypoint="claude-vscode")                      # the same docs in a worktree: one doc, not two
    wt = fx.p("~/work/shop/.claude/worktrees/x/")
    s6.attachment("instructions", files=[{"path": wt + "CLAUDE.md", "type": "Project", "content": root}]); s6.typed("deploy it")
    s6.call("Read", {"file_path": wt + "docs/deploy.md"}, "text")                                        # trap: deploy again, in the worktree's copy

    s7 = fx.session("~/work/shop", entrypoint="cli"); s7.attachment("instructions", files=inst)           # a command the person typed names the doc
    s7.command_expansion("/ship", "Ship it: follow docs/ship.md"); rd(s7, "docs/ship.md")                  # trap: the expansion is the person pointing
    s7.typed("now the manifest"); rd(s7, "runbooks/deploy.yaml")                                          # trap: not a doc suffix
    s8 = fx.session("~/work/shop", entrypoint="cli"); s8.attachment("instructions", files=inst)
    s8.command_expansion("/release", "Run [the checklist](references/checklist.md); hotfixes: references/hotfix.md.")   # the skill being credited
    rd(s8, ".claude/skills/release/references/hotfix.md")                                                 # counts: hotfix (its own skill's expansion is not the person)

    s5 = fx.session("~/work/shop", entrypoint="cli"); s5.typed("release"); rd(s5, "docs/nocontext.md")      # trap: the rule file wasn't in context
    h = fx.session("~/work/shop", entrypoint="sdk-cli"); h.attachment("instructions", files=inst); h.typed("go"); rd(h, "docs/style.md")   # trap: headless
    t = fx.session("~/work/team", entrypoint="cli"); t.attachment("instructions", files=[{"path": fx.p("~/work/team/CLAUDE.md"), "type": "Project", "content": "See docs/guide.md\n"}])
    t.typed("x"); t.call("Read", {"file_path": fx.p("~/work/team/docs/guide.md")}, "text")                 # trap: a teammate's rule file
    return {"value": 6}      # deploy, runbook, style, schema, checklist, hotfix on 4 sessions


@check("put-rules-where-they-apply", "L4", 0)
def own_hooks_bring_rules(lt):
    """Distinct hook commands, wired by the person (script they wrote), that fired on a tool use or a prompt and put text into the
    model's context (a JSON reply with additionalContext; plain text from a prompt hook), in their sessions (hook_success).
    Counted per hook, not per run. `{}` and an input rewrite put nothing in context."""
    runs = hook_runs(lt, CONTEXT_EVENTS)
    cmds = {}
    for a in runs:
        cmds.setdefault(a.get("command"), []).append(a)
    return {"value": len(cmds), "evidence": [f"{len(v)}x {_short(k, 60)}" for k, v in list(cmds.items())[:3]],
            "note": "whether the returned text is a rule or doc is not read"}


@_fixture("put-rules-where-they-apply", "L4", 0)
def _(fx):
    _plugin_world(fx)
    fx.repo("~/code/mykit", {"hooks/inject.py": "print('doc')\n", "hooks/rewrite.py": "print('{}')\n", "hooks/noop.py": "print('{}')\n"}, message="inject")
    fx.repo("~/work/team", {"hooks/g.py": "x\n"}, by="teammate")
    s = fx.session("~/code/mykit", entrypoint="claude-desktop"); s.typed("edit")
    for _ in range(3):
        s.attachment("hook_success", hookName="PreToolUse:Edit", hookEvent="PreToolUse", stdout=_ctx("PreToolUse", "rule"),
                     command="${CLAUDE_PLUGIN_ROOT}/hooks/on_edit.py")                                                                                 # one hook
    s.attachment("hook_success", hookName="PreToolUse:Read", hookEvent="PreToolUse", stdout=_ctx("PreToolUse", "doc"), command="python ~/code/mykit/hooks/inject.py")   # second hook
    s.attachment("hook_success", hookName="PreToolUse:Read", hookEvent="PreToolUse", stdout=_ctx("PreToolUse", "doc"), command="python ~/work/team/hooks/g.py")      # trap: teammate's
    s.attachment("hook_success", hookName="SessionStart:startup", hookEvent="SessionStart", stdout=_ctx("SessionStart", "doc"), command="python ~/code/mykit/hooks/inject.py")   # same hook, start
    s.attachment("hook_success", hookName="PostToolUse:Read", hookEvent="PostToolUse", stdout="{}", command="python ~/code/mykit/hooks/noop.py")                 # trap: `{}`
    s.attachment("hook_success", hookName="PreToolUse:Read", hookEvent="PreToolUse", command="python ~/code/mykit/hooks/rewrite.py",                              # trap: an input rewrite
                 stdout=json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"file_path": "/y"}}}))
    fx.session("~/work/team").typed("x")
    return {"value": 2}


# ====================================================================== settle-the-approach-first
@check("settle-the-approach-first", "L2", 0)
def plan_mode_used(lt):
    """Interactive sessions where plan mode was used: a typed message sent in permissionMode plan, a /plan command, or an
    EnterPlanMode call. Counted per session; who switched it on is not in the log."""
    sess = {}
    for m in lt.typed:
        if m.get("pm") == "plan" and lt.interactive(m["s"]):
            sess.setdefault(m["s"], "typed in plan mode")
    for c in lt.all_commands():
        if c["name"] == "/plan" and lt.interactive(c["s"]):
            sess.setdefault(c["s"], "/plan")
    for c in lt.calls:
        if c["name"] == "EnterPlanMode" and not c["sc"] and lt.interactive(c["s"]):
            sess.setdefault(c["s"], "EnterPlanMode")
    default_plan = any(isinstance(d, dict) and (d.get("permissions") or {}).get("defaultMode") == "plan" for _, _, d in lt.settings_files())
    return {"value": len(sess), "evidence": [f"{k[:8]}: {v}" for k, v in list(sess.items())[:3]],
            "note": "who started it is not in the log" + ("; settings make plan the default, so typed-in-plan is not a per-session act" if default_plan else "")}


@_fixture("settle-the-approach-first", "L2", 0)
def _(fx):
    a = fx.session("~/work/shop", entrypoint="claude-vscode", permission_mode="plan"); a.typed("how should we add sso?")      # counts
    a.typed("ok go on")
    b = fx.session("~/work/shop", entrypoint="claude-desktop"); b.typed("hi"); b.command("/plan")                           # counts
    c = fx.session("~/work/shop", entrypoint="cli"); c.typed("hi"); c.command("/plan"); c.call("EnterPlanMode", {}, "ok")   # counts once (cli: also in history)
    d = fx.session("~/work/shop", entrypoint="claude-vscode"); d.typed("just do it")                                          # trap
    h = fx.session("~/work/shop", entrypoint="sdk-cli", permission_mode="plan"); h.typed("plan the migration")              # trap: headless
    return {"value": 3}


_PLAN_FILE = re.compile(r"(^|/)\.claude/plans/[^/]+$")


@check("settle-the-approach-first", "L3", 0)
def plan_file_written(lt):
    """Can't tell. Claude Code writes the plan file itself whenever plan mode runs (a plan_mode attachment hands the model the file's
    path), so 'a plan file exists' is 'plan mode was used', which L2 already reads (the skeptic, 11-05). A plan the person kept
    elsewhere (a spec, a design note) has no name or place Claude Code gives it, and no rule of ours finds one."""
    return {"value": None, "evidence": [],
            "note": "can't tell from a laptop: the plan file Claude Code makes is not the person's act; a question for the call "
                    "('where do you write down the approach before the agent starts?')"}


@_fixture("settle-the-approach-first", "L3", 0)
def _(fx):
    s = fx.session("~/work/shop", entrypoint="claude-vscode", permission_mode="plan"); s.typed("plan it")
    s.call("Write", {"file_path": fx.p("~/.claude/plans/a-plan.md"), "content": "x"})                                      # Claude Code's own plan file
    return {"value": None}


@check("settle-the-approach-first", "L3", 1)
def plan_file_reused(lt):
    """Plan files written in one session that a later, separate interactive session read or edited. A session that started
    before the writer's last record (a fork or a parallel session) is not a later one."""
    writers, touches = {}, []
    for c in lt.calls:
        if c["sc"] or not lt.interactive(c["s"]):
            continue
        p = _norm(_input(c).get("file_path") or "")
        if not _PLAN_FILE.search(p):
            continue
        if c["name"] == "Write":
            writers.setdefault(p, set()).add(c["s"])
        if c["name"] in ("Read", "Edit", "MultiEdit"):
            touches.append((p, c["s"]))
    used = set()
    for p, s in touches:
        for w in writers.get(p, ()):
            if w != s and (lt.sessions[s]["t0"] or 0) >= (lt.sessions[w]["t1"] or 0):
                used.add(p)
    return {"value": len(used), "evidence": [os.path.basename(p) for p in sorted(used)[:3]],
            "note": "a resumed session that gets a new session id still reads as 'another session'"}


@_fixture("settle-the-approach-first", "L3", 1)
def _(fx):
    P = lambda n: fx.p(f"~/.claude/plans/{n}.md")
    a = fx.session("~/work/shop", entrypoint="claude-vscode", start=time.time() - 5 * DAY); a.typed("plan")
    for n in ("p1", "p2", "p3", "p5"):
        a.call("Write", {"file_path": P(n), "content": "x"})
    a.call("Edit", {"file_path": P("p3"), "old_string": "x", "new_string": "y"})                         # trap: edited by its own session
    e = fx.session("~/work/shop", entrypoint="claude-vscode", start=a.t - 60); e.typed("same time")      # trap: runs alongside the writer
    e.call("Read", {"file_path": P("p5")})
    a.call("Read", {"file_path": P("p3")})                                                                 # keeps the writer's span open past e's start
    b = fx.session("~/work/shop", entrypoint="claude-desktop", start=time.time() - 4 * DAY); b.typed("continue"); b.call("Read", {"file_path": P("p1")})   # counts
    c = fx.session("~/work/shop", entrypoint="cli", start=time.time() - 3 * DAY); c.typed("continue"); c.call("Edit", {"file_path": P("p2"), "old_string": "x", "new_string": "z"})   # counts
    h = fx.session("~/work/shop", entrypoint="sdk-cli", start=time.time() - 2 * DAY); h.typed("go"); h.call("Read", {"file_path": P("p3")})   # trap: headless
    return {"value": 2}


@check("settle-the-approach-first", "L4", 0)
def saved_runner_scripts(lt):
    """Entries the person's repos declare (package.json scripts, Makefile / justfile recipes, CI run steps) that start an
    agent headless, directly or through the tracked script they run, in a file the person wrote. Ignored build output
    never shows up."""
    got = [e for e in declared_runners(lt) if _agent_runner(lt, e) and _mine(lt, e["file"])]
    return {"value": len(got), "evidence": [f"{e['kind']}: {e['name']}" for e in got[:3]],
            "note": "that a plan or spec drives the runner is not read"}


def _runner_repo(fx):
    fx.repo("~/work/shop", {
        ".gitignore": "dist/\n",
        "package.json": json.dumps({"scripts": {"triage": "node scripts/triage.mjs", "old": "sh scripts/old.sh", "build": "tsc", "bundle": "node dist/bundle.js",
                                                "each": "sh scripts/each.sh", "sweep": "node scripts/sweep.mjs",
                                                "batch": "for f in tasks/*; do node scripts/triage.mjs $f; done",
                                                "mixed": "sh scripts/mixed.sh", "setup": "sh scripts/setup.sh", "team": "sh scripts/team-agent.sh"}}),
        "scripts/triage.mjs": 'import { query } from "@anthropic-ai/claude-agent-sdk";\nawait query({ prompt: "triage" });\n',
        "scripts/old.sh": '#!/bin/sh\n# claude -p "old way"\necho done\n',
        "scripts/each.sh": '#!/bin/sh\nfor f in tasks/*; do\n  claude -p "do $f"\ndone\n',
        "scripts/sweep.mjs": 'import { query } from "@anthropic-ai/claude-agent-sdk";\nfor (const t of tasks) { await query({ prompt: t }); }\n',
        "scripts/mixed.sh": '#!/bin/sh\nfor f in *.log; do rm "$f"; done\nclaude -p "summarise the repo"\n',      # a loop that is not around the call
        "scripts/setup.sh": '#!/bin/sh\nnpm i -g @anthropic-ai/claude-code\nfor p in a b; do pip install claude-agent-sdk; done\n',   # installs only
        "scripts/team-agent.sh": '#!/bin/sh\necho placeholder\n',                                                       # the teammate rewrites it below
        "Makefile": 'review:\n\tclaude -p "review the diff"\n\ninstall:\n\tpip install claude-agent-sdk\n\ntest:\n\tpytest\n',
        ".github/workflows/agent.yml": 'jobs:\n  a:\n    steps:\n      - run: claude -p "label this issue"\n',
        ".github/workflows/setup.yml": 'jobs:\n  a:\n    steps:\n      - run: npm i -g @anthropic-ai/claude-code\n',
        "dist/bundle.js": 'spawn("claude -p x")\n'})
    fx.commit("~/work/shop", {"scripts/team-agent.sh": '#!/bin/sh\nfor f in x y; do claude -p "$f"; done\n', "scripts/team-2.sh": "echo\n"}, by="teammate")
    for i in range(3):   # the teammate wrote most of that script's history
        fx.commit("~/work/shop", {"scripts/team-agent.sh": f'#!/bin/sh\nfor f in x y; do claude -p "$f $i"; done\n'}, by="teammate")
    fx.repo("~/work/team", {"Makefile": 'agent:\n\tclaude -p "team job"\n'}, by="teammate")
    fx.session("~/work/shop").typed("x"); fx.session("~/work/team").typed("x")


@_fixture("settle-the-approach-first", "L4", 0)
def _(fx):
    _runner_repo(fx)
    return {"value": 7}   # triage, each, sweep, batch, mixed (package.json), review (Makefile), the workflow step; not old (comment), bundle (ignored), installs, team's


# ====================================================================== review-past-sessions-to-fix-the-setup
_TPATH = re.compile(r"""\.claude/projects[^\s"'`)]*|history\.jsonl|\.claude/usage-data""")


def _transcript_paths(text, sid=None):
    """The places in this text that are past transcripts: a .jsonl under the projects folder (a session's or a sub-agent's), the
    projects folder itself or one project's folder, history.jsonl, usage-data. Not the other files Claude Code keeps below the
    projects folder for its own use (saved oversized tool output, automatic memory: a .txt or .md in a subfolder), and not this session's
    own files (the skeptic, 11-05: 1,915 saved-output files, 596 real Reads of them)."""
    out = []
    for m in _TPATH.findall(_norm(text)):
        if sid and sid in m:
            continue
        if m.startswith(".claude/projects"):
            below = [x for x in m[len(".claude/projects"):].split("/") if x]
            if not (m.endswith(".jsonl") or len(below) <= 1):
                continue
        out.append(m)
    return out


@check("review-past-sessions-to-fix-the-setup", "L2", 1)
def person_reads_transcript(lt):
    """Shell lines the person ran themselves in an interactive session (a ! line, which Claude Code records as <bash-input>) that name
    the projects folder, history.jsonl or usage-data and do more than list or enter it, in either path style. Pasted text and
    prompts that name a path are not reading (the skeptic, 11-05: real hits were pasted text and a 'resume' message)."""
    hits = []
    for m in lt.typed:
        t = m["text"].strip()
        if not lt.interactive(m["s"]) or not t.startswith("<bash-input>"):
            continue
        cmd = t[len("<bash-input>"):].split("</bash-input>")[0].strip()
        if _transcript_paths(cmd) and cmd.split(" ")[0] not in _LISTS_ONLY:
            hits.append(m)
    return {"value": len(hits), "evidence": [_short(m["text"], 70) for m in hits[:3]],
            "note": "a prompt that asks Claude to read transcripts is not read here (L2#2 sees what Claude opened); naming a path is not reading"}


_LISTS_ONLY = ("ls", "tree", "du", "stat", "cd", "pwd", "rm", "mv", "cp", "mkdir", "touch")   # shell commands that don't read a file's content


@_fixture("review-past-sessions-to-fix-the-setup", "L2", 1)
def _(fx):
    s = fx.session("~/work/shop", entrypoint="claude-desktop")
    s.typed("<bash-input>head -c 500 ~/.claude/projects/-Users-x/abc.jsonl</bash-input>")                   # counts: a ! line that reads
    s.typed("<bash-input>jq .message C:\\Users\\dana\\.claude\\projects\\foo\\a.jsonl</bash-input>")        # counts: Windows path
    s.typed("<bash-input>ls ~/.claude/projects</bash-input>")                                                # trap: only lists
    s.typed("<bash-input>cat ~/.claude/settings.json</bash-input>")                                          # trap: not a transcript
    s.typed("Resume the paused workflow, the transcript is in ~/.claude/projects/-x/a.jsonl")                # trap: a message that names a path (real)
    s.typed("regarde C:\\Users\\dana\\.claude\\projects\\foo\\a.jsonl et dis-moi ce qui a mal tourné")      # trap: pasted path, not reading
    s.typed("list the projects folder of the repo")                                                          # trap: generic words
    h = fx.session("~/work/shop", entrypoint="sdk-cli"); h.typed("read ~/.claude/projects/x/a.jsonl")         # trap: headless
    return {"value": 2}


def _reads_transcript(lt, c):
    """The call names a past transcript (_transcript_paths), and is not a shell command that only lists or enters the folder."""
    if not _transcript_paths(c.get("input") or "", c["s"]):
        return False
    if c["name"] == "Bash":
        first = (_input(c).get("command") or "").strip().split(" ")[0]
        if first in _LISTS_ONLY:
            return False
    return True


@check("review-past-sessions-to-fix-the-setup", "L2", 2)
def claude_reads_transcripts(lt):
    """Interactive sessions with a typed message in which the main thread read past transcripts: a Read, Grep, Glob or Bash
    call on the projects folder, a transcript in it, history.jsonl or usage-data. Claude Code's own files beside a transcript
    (saved tool output, auto memory) and this session's own folder are left out; a shell command that only lists the folder too."""
    typed = _typed_sessions(lt)
    sess = {}
    for c in lt.calls:
        if c["name"] in ("Read", "Grep", "Glob", "Bash") and not c["sc"] and c["s"] in typed and _reads_transcript(lt, c):
            sess.setdefault(c["s"], _short(_input(c).get("file_path") or _input(c).get("command") or _input(c).get("pattern"), 60))
    return {"value": len(sess), "evidence": list(sess.values())[:3], "note": "Claude reading transcripts is not the person reviewing; a setup scan does it too"}


@_fixture("review-past-sessions-to-fix-the-setup", "L2", 2)
def _(fx):
    a = fx.session("~/work/shop", entrypoint="claude-vscode"); a.typed("why did that fail?")
    a.call("Read", {"file_path": fx.p("~/.claude/projects/-x/abc.jsonl")})                                   # counts
    b = fx.session("~/work/shop", entrypoint="claude-desktop"); b.typed("find the error")
    b.call("Bash", {"command": "rg -c error ~/.claude/projects/"})                                            # counts
    c = fx.session("~/work/shop", entrypoint="claude-vscode"); c.typed("check")
    c.call("Read", {"file_path": "C:\\Users\\dana\\.claude\\projects\\a.jsonl"})                              # counts: Windows
    d = fx.session("~/work/shop", entrypoint="claude-vscode"); d.typed("check")
    d.call("Bash", {"command": "ls ~/.claude/projects"})                                                       # trap: only lists
    d.call("Read", {"file_path": fx.p("~/.claude/settings.json")})                                             # trap: another file
    d.call("Read", {"file_path": fx.p(f"~/.claude/projects/-x/{d.sid}/b1.txt")})                               # trap: Claude Code's own saved tool output
    d.call("Read", {"file_path": fx.p("~/.claude/projects/-x/0a1b2c3d-aaaa-bbbb-cccc-000000000000/b2.txt")})   # trap: saved tool output of another session
    d.call("Read", {"file_path": fx.p("~/.claude/projects/-x/memory/MEMORY.md")})                              # trap: Claude Code's auto memory
    d.call("Read", {"file_path": fx.p(f"~/.claude/projects/-x/{d.sid}.jsonl")})                                # trap: this session's own transcript
    e = fx.session("~/work/shop", entrypoint="claude-vscode"); e.typed("check")
    e.subagent("Explore", "dig", calls=[("Read", {"file_path": fx.p("~/.claude/projects/-x/a.jsonl")}, "x")])  # trap: a sub-agent's
    h = fx.session("~/work/shop", entrypoint="sdk-cli"); h.typed("go"); h.call("Read", {"file_path": fx.p("~/.claude/history.jsonl")})   # trap: headless
    return {"value": 3}


def _own_units(lt):
    """The person's own files that Claude Code runs or loads, as units [(kind, [files])]: user and project commands,
    agents and skills, and the scripts their hooks run."""
    import glob as _g
    units, seen = [], set()
    bases = [lt.claude] + [os.path.join(r, ".claude") for r in lt.repos]
    for b in bases:
        for p in _g.glob(os.path.join(b, "commands", "**", "*.md"), recursive=True) + _g.glob(os.path.join(b, "agents", "*.md")):
            units.append(("command/agent", [p]))
        for d in _g.glob(os.path.join(b, "skills", "*")):
            rd = os.path.realpath(d)
            files = [os.path.join(rt, f) for rt, _, fs in os.walk(rd) for f in fs] if os.path.isdir(rd) else []
            units.append(("skill", files[:60]))
    for h in hook_entries(lt):
        for f in h["files"]:
            units.append(("hook", [f]))
    out = []
    for k, files in units:
        key = tuple(sorted(os.path.realpath(f) for f in files))
        if key not in seen:
            seen.add(key); out.append((k, files))
    return out


_READS_SESSIONS = re.compile(r"""\.claude/projects[^\n]{0,200}\.jsonl|history\.jsonl|\.claude/usage-data""")


@check("review-past-sessions-to-fix-the-setup", "L3", 0)
def own_file_reads_sessions(lt):
    """The person's own commands, agents, skills and hook scripts (files they wrote) whose text names the session transcripts
    themselves: a .jsonl under the projects folder, history.jsonl or usage-data, in either path style. Naming the folder alone does not
    (a hook that saves tool results beside the transcripts, a rule that says never to touch it; the skeptic, 11-05)."""
    hit = []
    for kind, files in _own_units(lt):
        for f in files:
            if _READS_SESSIONS.search(_norm(_read(f, 100_000))) and _mine(lt, f):    # the text first: whose a file is needs git, and few files name the transcripts
                hit.append(f"{kind}: {os.path.basename(f)}")
                break
    return {"value": len(hit), "evidence": hit[:3], "note": "a file nobody committed is theirs (ruling 11-05); a file that names the transcripts to say "
            "'don't' reads like one that reads them"}


@_fixture("review-past-sessions-to-fix-the-setup", "L3", 0)
def _(fx):
    fx.repo("~/code/skillrepo", {"review-sessions/SKILL.md": "Read the files in ~/.claude/history.jsonl and list the retries.\n"})
    os.makedirs(fx.p("~/.claude/skills"), exist_ok=True)
    os.symlink(fx.p("~/code/skillrepo/review-sessions"), fx.p("~/.claude/skills/review-sessions"))                           # counts: linked into their repo
    fx.repo("~/work/shop", {".claude/commands/retro.md": "Open %USERPROFILE%\\.claude\\projects\\*\\*.jsonl and group the failures.\n",   # counts
                            ".claude/commands/standup.md": "Summarise the session transcripts of yesterday.\n",                        # trap: words only
                            ".claude/commands/tidy.md": "Never write under ~/.claude/projects; keep notes in docs/.\n",                # trap: names the folder, to say don't
                            "scripts/mine.py": "import glob\nglob.glob('~/.claude/projects/*/*.jsonl')\n",                            # counts: a hook's script
                            "scripts/save.py": "open('~/.claude/projects/-x/saved/out.txt', 'w').write('x')\n"})                # trap: a hook writing beside transcripts
    fx.commit("~/work/shop", {".claude/agents/miner.md": "Read ~/.claude/projects/*/a.jsonl\n"}, by="teammate", message="agent")   # trap: a teammate's
    fx.file("~/.claude/commands/loose.md", "Read ~/.claude/projects/*/a.jsonl\n")                                                  # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.settings("project", {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python scripts/mine.py"}]}],
                                       "PostToolUse": [{"hooks": [{"type": "command", "command": "python scripts/save.py"}]}]}}, repo="~/work/shop")
    fx.session("~/work/shop").typed("x")
    return {"value": 4}      # skillrepo's skill, retro.md, mine.py, and ~/.claude/commands/loose.md


@check("review-past-sessions-to-fix-the-setup", "L4", 0)
def headless_miner(lt):
    """Headless runs (sdk-*) whose first prompt names the projects folder, history.jsonl or usage-data, in either path style, and is a
    prompt that sits whole in a script or schedule the person wrote (so not an installed plugin's or a hook's own run)."""
    first = _headless_first(lt)
    hits = [m for m in first.values() if _transcript_paths(m["text"]) and _prompt_is_own(lt, m["text"])]
    return {"value": len(hits), "evidence": [_short(m["text"], 70) for m in hits[:3]],
            "note": "a prompt read from a file at run time, or built from variables, can't be matched to the script and is not counted"}


@_fixture("review-past-sessions-to-fix-the-setup", "L4", 0)
def _(fx):
    ps = ("~/.claude/projects", "C:\\Users\\dana\\.claude\\projects")
    fx.repo("~/work/shop", {"Makefile": "".join(f'mine{i}:\n\tclaude -p "Summarise failures in {p} for last night"\n\n' for i, p in enumerate(ps))})
    for p in ps:
        h = fx.session("~/work/shop", entrypoint="sdk-cli"); h.typed(f"Summarise failures in {p} for last night")        # counts x2: their Makefile's prompts
    h = fx.session("~/work/shop", entrypoint="sdk-cli"); h.typed("Fix the flaky test"); h.typed("also read ~/.claude/projects")   # trap: not the first prompt
    g = fx.session("~/work/shop", entrypoint="sdk-cli"); g.typed("Mine ~/.claude/projects/-x/a.jsonl for decisions to remember")   # trap: a plugin's run, in no file of theirs
    fx.session("~/work/shop", entrypoint="claude-vscode").typed("read ~/.claude/projects/x")                              # trap: interactive
    return {"value": 2}


# ====================================================================== keep-always-loaded-small
def _git_in(lt, top, *args, input=None):
    """Read-only git in `top`, stdout as bytes ('' on any failure). Started with posix_spawn (absolute git path, no cwd=,
    close_fds off): a fork of this big process costs ~5ms per run, and a scan makes hundreds."""
    exe = _cache(lt).setdefault("git_exe", shutil.which("git") or "git")
    try:
        r = subprocess.run([exe, "-C", top, *args], input=input, capture_output=True, timeout=120, close_fds=False,
                           env={**os.environ, "HOME": lt.home, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.SubprocessError):
        return b""
    return r.stdout if r.returncode == 0 else b""


def _blob_words(lt, top, shas):
    """{blob sha: (word count, the lines as a multiset)}: one `git cat-file --batch` for all of them (not one `git show` per
    revision). A deleted file (the all-zero sha) reads as 0 words, as a failed `git show` did."""
    from collections import Counter
    out = {s: (0, Counter()) for s in shas if not s.strip("0")}
    todo = [s for s in shas if s.strip("0")]
    if not todo:
        return out
    buf, pos = _git_in(lt, top, "cat-file", "--batch", input="".join(x + "\n" for x in todo).encode()), 0
    for sha in todo:
        nl = buf.find(b"\n", pos)
        if nl < 0:
            break
        head = buf[pos:nl].split()
        pos = nl + 1
        if len(head) == 3 and head[1] == b"blob":
            size = int(head[2])
            text = buf[pos:pos + size].decode("utf-8", "replace")
            out[sha] = (len(text.split()), Counter(l.strip() for l in text.splitlines() if l.strip()))
            pos += size + 1
        else:
            out[sha] = (0, Counter())
    return out


def _top(lt, real):
    """The work tree root around a file: the nearest folder above it holding a `.git` (folder or worktree link). Looked up
    on disk, memoized per folder (a `git rev-parse --show-toplevel` per file cost 1,571 git runs)."""
    memo = _cache(lt).setdefault("top", {})
    d = os.path.dirname(real)
    chain = []
    while d not in memo:
        chain.append(d)
        if os.path.exists(os.path.join(d, ".git")):
            memo[d] = d
            break
        up = os.path.dirname(d)
        if up == d:
            memo[d] = None
            break
        d = up
    for c in chain:
        memo[c] = memo[d]
    return memo[os.path.dirname(real)]


def _revisions(lt, top, rels):
    """{rel: [(sha, author line, blob sha)] newest first, at most 300}: what `git log -300 -- <file>` gives for each file, from
    ONE `git log --raw` over all of the repo's rule files together (per-file logs cost 1,901 git runs). A merge is listed
    for a file only when it differs from every parent (-c), as `git log -- <file>` lists it."""
    memo = _cache(lt).setdefault("revs", {})
    if top not in memo:
        out = _git_in(lt, top, "-c", "core.quotePath=false", "log", "--format=%x00%H\t%an <%ae>", "--raw", "--no-abbrev",
                      "--no-renames", "-c", "--", *[":(literal)" + r for r in sorted(rels)]).decode("utf-8", "replace")
        files, cur = {}, None
        for line in out.splitlines():
            if line.startswith("\x00"):
                sha, _, who = line[1:].partition("\t")
                cur = (sha, who)
            elif line.startswith(":") and cur:
                meta, _, path = line.partition("\t")
                revs = files.setdefault(path, [])
                if len(revs) < 300:
                    revs.append((cur[0], cur[1], meta.split()[-2]))
        memo[top] = files
    return memo[top]


def _histories(lt):
    """{real path: [(sha, author line, words, lines)] oldest first} for every loaded rule file with a commit of the person's in its
    newest 300 revisions (a file without one cannot hold a trim by them); words are read for those files only."""
    from collections import Counter
    cache = _cache(lt)
    if "hists" not in cache:
        by_top = {}
        for p in _loaded_files(lt):
            top = _top(lt, p)
            if top:
                by_top.setdefault(top, []).append(p)
        rels = {top: {p: os.path.relpath(p, top).replace(os.sep, "/") for p in paths} for top, paths in by_top.items()}

        def one_repo(top):
            rel_of = rels[top]
            revs = _revisions(lt, top, set(rel_of.values()))
            mine = {p: revs.get(r, []) for p, r in rel_of.items() if any(lt.is_mine(w) for _, w, _ in revs.get(r, []))}
            words = _blob_words(lt, top, sorted({b for rv in mine.values() for _, _, b in rv}))
            return {p: [(sha, who, *words.get(b, (0, Counter()))) for sha, who, b in reversed(rv)] for p, rv in mine.items()}

        lt.repos, lt.identity   # base reads these lazily and swaps HOME while it does: read them before any thread starts
        hists = {}
        with ThreadPoolExecutor(max_workers=min(16, (os.cpu_count() or 4) * 2)) as pool:   # the repos' git runs are independent
            for part in pool.map(one_repo, by_top):
                hists.update(part)
        cache["hists"] = hists
    return cache["hists"]


def _history(lt, path):
    """[(sha, author line, words, lines)] oldest first, for a rule file the person has committed to ([] for any other: it can hold
    no trim of theirs). 11-05: all files of a repo from one `git log --raw` and one `git cat-file --batch`."""
    return _histories(lt).get(os.path.realpath(path), [])


def _loaded_files(lt):
    return sorted({os.path.realpath(p) for scope, p in lt.instruction_files() if scope in ("root", "folder", "user", "user-rule")})


def _trims(lt, path):
    """[(sha, words before, words after)]: commits by the person that only took lines out of the rule file (every line left was
    already there, and the file is shorter). A commit that also adds or rewords lines is an edit, not a trim (the skeptic, 11-05:
    'CLAUDE.md 552->545 words' was a routine edit). A file emptied or removed is not a trim either."""
    h = _history(lt, path)
    out = []
    for i in range(1, len(h)):
        sha, who, words, lines = h[i]
        if lt.is_mine(who) and 0 < words < h[i - 1][2] and not (lines - h[i - 1][3]):
            out.append((sha, h[i - 1][2], words))
    return out


@check("keep-always-loaded-small", "L2", 0)
def trims_the_file(lt):
    """Commits by the person (any email their git config knows) that took lines out of a rule file Claude loads and added or
    changed none."""
    got = []
    for p in _loaded_files(lt):
        got += [f"{os.path.basename(p)} {a}->{b} words" for _, a, b in _trims(lt, p)]
    return {"value": len(got), "evidence": got[:3],
            "note": "an agent's cleanup committed under their identity reads as theirs; files outside git are not read"}


def _trim_repo(fx):
    w = lambda n: "\n".join(f"w{i}" for i in range(n)) + "\n"
    fx.repo("~/work/shop", {"CLAUDE.md": w(100), "AGENTS.md": w(100), "services/api/CLAUDE.md": w(100), "services/web/CLAUDE.md": w(100),
                            "services/ops/CLAUDE.md": w(100), "README.md": w(100)})
    fx.commit("~/work/shop", {"CLAUDE.md": w(40), "AGENTS.md": w(40)}, message="trim")                          # two trims by the person
    fx.commit("~/work/shop", {"CLAUDE.md": w(60)}, message="grow a bit")                                         # CLAUDE.md: 60 now, peak 100
    fx.commit("~/work/shop", {"AGENTS.md": w(150)}, message="grow a lot")                                        # AGENTS.md: 150 > peak 100
    fx.commit("~/work/shop", {"services/api/CLAUDE.md": w(50)}, by="teammate", message="teammate trim")         # trap: not the person
    fx.commit("~/work/shop", {"services/web/CLAUDE.md": w(120)}, message="grow only")                            # trap: never trimmed
    fx.commit("~/work/shop", {"services/ops/CLAUDE.md": "\n".join(f"rewritten line {i}" for i in range(30)) + "\n"}, message="reword")   # trap: shorter, but every line is new (552->545)
    fx.commit("~/work/shop", {"README.md": w(10)}, message="shrink readme")                                      # trap: not a loaded file
    fx.session("~/work/shop").typed("x")


@_fixture("keep-always-loaded-small", "L2", 0)
def _(fx):
    _trim_repo(fx)
    return {"value": 2}


@check("keep-always-loaded-small", "L3", 0)
def trims_hold(lt):
    """Can't tell. 'Stays small' needs a size that counts as small; any bar would be one laptop's guess, and 'now below the all-time
    peak after a shrink' calls a 5,000-word file small because it once had 5,010 (the skeptic, 11-05). The history is readable
    (a file's size over time); the judgement is the interviewer's."""
    return {"value": None, "evidence": [],
            "note": "can't tell from a laptop: what counts as small is not in any file; the sizes over time are, for the call "
                    "('your CLAUDE.md went from A to B words and is C now: is C small for what it must hold?')"}


@_fixture("keep-always-loaded-small", "L3", 0)
def _(fx):
    _trim_repo(fx)
    return {"value": None}


@check("keep-always-loaded-small", "L4", 2)
def deny_rules_on_checks(lt):
    """Can't tell. A deny / ask rule on Edit / Write protects a hook script or a test file from the agent's edits; it does not show
    'the check runs every time' (the skeptic, 11-05). What would show it, a hook that runs the check firing before each step,
    is the enforcement practice's reading, not this one's."""
    return {"value": None, "evidence": [],
            "note": "can't tell from a laptop: a protected file is not a check that runs every time; a question for the call"}


@_fixture("keep-always-loaded-small", "L4", 2)
def _(fx):
    fx.repo("~/work/shop", {"pyproject.toml": "[project]\nname='s'\n", "scripts/guard.py": "print(1)\n", "tests/test_guard.py": "import guard\n"})
    fx.settings("project", {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "python scripts/guard.py"}]}]},
                            "permissions": {"deny": ["Edit(/scripts/guard.py)", "Edit(/tests/**)"]}}, repo="~/work/shop")   # a protected file: not the claim
    fx.session("~/work/shop").typed("x")
    return {"value": None}


# ====================================================================== limit-what-the-agent-can-reach
@check("limit-what-the-agent-can-reach", "L3", 0)
def permission_rules(lt):
    """Distinct permissions.deny and permissions.ask rules the person set: in any settings file that is theirs by the one ownership
    rule (_mine: user, local or project; committed or not). A managed policy's rules and a teammate's committed
    project settings are not theirs. The same rule in a worktree copy or a second file counts once. Allow rules are reported, not
    counted."""
    seen = {"deny": set(), "ask": set(), "allow": set()}
    for scope, path, data in lt.settings_files():
        if scope == "managed" or not _mine(lt, path):    # scope check, then the one ownership rule
            continue
        perms = data.get("permissions") if isinstance(data, dict) else None
        if isinstance(perms, dict):
            for k in seen:
                seen[k].update(str(r) for r in perms.get(k) or [])
    n = len(seen["deny"]) + len(seen["ask"])
    return {"value": n, "evidence": [f"deny {len(seen['deny'])}, ask {len(seen['ask'])}, allow {len(seen['allow'])} (not counted)"],
            "note": "allow rules often come from clicking 'don't ask again'; a rule is a limit chosen only when it is deny or ask; an "
                    "project settings file nobody committed is theirs (ruling 11-05)"}


@_fixture("limit-what-the-agent-can-reach", "L3", 0)
def _(fx):
    fx.settings("user", {"permissions": {"deny": ["Read(~/.ssh/**)", "Bash(rm -rf:*)"], "ask": ["Bash(git push:*)"], "allow": ["Bash(ls:*)", "Bash(git status)", "Read(*)"]}})
    proj = {"permissions": {"deny": ["Edit(/.env)"], "allow": ["Bash(npm test)", "Bash(npm run build)"]}}
    for r in ("shop", "shop-wt"):                                                                      # shop-wt: trap, a worktree copy of the same file
        fx.repo(f"~/work/{r}", {"README.md": "x"})
        _commit_settings(fx, f"~/work/{r}", proj)
    fx.repo("~/work/api", {"README.md": "x"}, by="teammate")
    _commit_settings(fx, "~/work/api", {"permissions": {"deny": ["Bash(curl:*)", "Bash(rm -rf:*)"]}}, by="teammate")   # trap: a teammate's committed settings
    fx.settings("project-local", {"permissions": {"deny": ["Bash(sudo:*)"]}}, repo="~/work/shop")      # counts: their local settings
    fx.settings("managed", {"permissions": {"deny": ["WebFetch"]}})                                    # trap: an org's policy
    for r in ("shop", "shop-wt", "api"):
        fx.session(f"~/work/{r}").typed("x")
    return {"value": 5}   # Read(~/.ssh/**), Bash(rm -rf:*), Bash(git push:*), Edit(/.env), Bash(sudo:*)


@check("limit-what-the-agent-can-reach", "L3", 2)
def project_scoped_connections(lt):
    """Can't tell. `claude mcp add` puts a server in ~/.claude.json under the project's folder by default, and a repo's .mcp.json
    can be a teammate's; so a server held for one project is what the tool does for everyone who doesn't pass a scope, not a limit
    the person chose (the skeptic, 11-05). Which servers each project needed is in no file."""
    return {"value": None, "evidence": [],
            "note": "can't tell from a laptop: a per-project server is the default scope, and owning a .mcp.json does not make the servers "
                    "it lists theirs; a question for the call ('which servers are on in which projects, and why?')"}


@_fixture("limit-what-the-agent-can-reach", "L3", 2)
def _(fx):
    fx.file("~/.claude.json", json.dumps({"mcpServers": {"github": {"command": "gh-mcp"}},
                                          "projects": {fx.p("~/work/shop"): {"mcpServers": {"db": {"command": "db-mcp"}}}}}))   # the default scope of `claude mcp add`
    fx.file("~/work/shop/.mcp.json", json.dumps({"mcpServers": {"slack": {"command": "slack-mcp"}}}))
    fx.session("~/work/shop").typed("x")
    return {"value": None}


check("limit-what-the-agent-can-reach", "L4", 0)(pretool_hooks_by_person)


@_fixture("limit-what-the-agent-can-reach", "L4", 0)
def _(fx):
    fx.repo("~/work/shop", {"hooks/no-push.sh": "#!/bin/sh\nexit 2\n", "hooks/audit.sh": "#!/bin/sh\nexit 2\n", "hooks/log.sh": "#!/bin/sh\necho \"$@\" >> /tmp/t\nexit 0\n"})
    fx.repo("~/work/vendor", {"hooks/vendor-gate.sh": "#!/bin/sh\nexit 2\n"}, by="teammate")
    _plugin_world(fx)                                                                                  # plugin hooks: a copy in the cache, a clone
    fx.file("~/.claude/plugins/cache/market/other/2.0.0/hooks/hooks.json",
            json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "python ${CLAUDE_PLUGIN_ROOT}/hooks/guard.py"}]}]}}))   # trap: installed
    fx.settings("user", {"enabledPlugins": {"rules@mykit": True, "other@market": True},
                         "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "sh " + fx.p("~/work/shop/hooks/no-push.sh")}]},   # counts
                                                  {"hooks": [{"type": "command", "command": "sh " + fx.p("~/work/vendor/hooks/vendor-gate.sh")}]},         # trap
                                                  {"hooks": [{"type": "command", "command": "sh " + fx.p("~/work/shop/hooks/log.sh")}]}],                  # trap: a logger of theirs, limits nothing
                                   "PostToolUse": [{"hooks": [{"type": "command", "command": "sh " + fx.p("~/work/shop/hooks/audit.sh")}]}]}})            # trap: after
    fx.file("~/code/mykit/plugins/rules/hooks/hooks.json",
            json.dumps({"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "python ${CLAUDE_PLUGIN_ROOT}/hooks/on_edit.py"}]}]}}))
    fx.commit("~/code/mykit", {"plugins/rules/hooks/hooks.json": json.dumps({"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "python ${CLAUDE_PLUGIN_ROOT}/hooks/on_edit.py"}]}]}})}, message="hooks")
    fx.session("~/work/shop").typed("x"); fx.session("~/work/vendor").typed("x"); fx.session("~/code/mykit").typed("x")
    return {"value": 2}   # no-push.sh, and the person's own plugin's on_edit.py (its source is their repo)


# ====================================================================== fresh-run-per-task
@check("fresh-run-per-task", "L2", 1)
def starts_from_progress_file(lt):
    """Can't tell. A progress file looks like any file a session wrote and a later one read and edited: the real hits were code and
    config files, not progress notes (the skeptic, 11-05). No rule of ours (a file's kind is not in the log; its name is a word
    list) tells a progress file from any shared note, and 'updated after every task' is not in the log either."""
    return {"value": None, "evidence": [],
            "note": "can't tell from a laptop: which file is a progress file is not recorded; a question for the call "
                    "('what file does a new session start from, and who updates it?')"}


@_fixture("fresh-run-per-task", "L2", 1)
def _(fx):
    pf = fx.p("~/work/shop/src/space.yaml")
    a = fx.session("~/work/shop", entrypoint="claude-vscode", start=time.time() - 6 * DAY); a.typed("start the work")
    a.call("Write", {"file_path": pf, "content": "x: 1"})
    s = fx.session("~/work/shop", entrypoint="claude-desktop", start=time.time() - 5 * DAY)
    s.typed("continue"); s.call("Read", {"file_path": pf}); s.call("Edit", {"file_path": pf, "old_string": "1", "new_string": "2"})   # a config file, not a progress file
    return {"value": None}


@check("fresh-run-per-task", "L2", 2)
def loop_repeats_prompt(lt):
    """Interactive sessions where the person typed /loop (transcript or terminal history): the built-in command that repeats a
    prompt. A ScheduleWakeup or CronCreate call is Claude's own choice, one-shot timers included, and is not read (the skeptic,
    11-05). Counted per session."""
    sess = {}
    for c in lt.all_commands():
        if c["name"] == "/loop" and lt.interactive(c["s"]):
            sess.setdefault(c["s"], "/loop")
    return {"value": len(sess), "evidence": [f"{k[:8]}: {v}" for k, v in list(sess.items())[:3]],
            "note": "/loop repeats one prompt on a timer; that it walks a list is not in the log"}


@_fixture("fresh-run-per-task", "L2", 2)
def _(fx):
    fx.session("~/work/shop", entrypoint="claude-desktop").command("/loop", "5m check the deploy")        # counts
    c = fx.session("~/work/shop", entrypoint="cli"); c.typed("hi"); c.command("/loop", "10m x"); c.call("ScheduleWakeup", {"delaySeconds": 60, "prompt": "x"})   # counts once
    v = fx.session("~/work/shop", entrypoint="claude-vscode"); v.typed("hi"); v.call("CronCreate", {"cron": "0 9 * * *", "prompt": "x", "recurring": False})     # trap: Claude's one-shot timer
    w = fx.session("~/work/shop", entrypoint="claude-vscode"); w.typed("hi"); w.call("ScheduleWakeup", {"delaySeconds": 270, "prompt": "check"})                # trap: Claude's own wake-up
    n = fx.session("~/work/shop", entrypoint="claude-vscode"); n.typed("hi"); n.command("/compact")                                                            # trap
    h = fx.session("~/work/shop", entrypoint="sdk-cli"); h.typed("go"); h.command("/loop", "1m x")                                                             # trap: headless
    return {"value": 2}


@check("fresh-run-per-task", "L4", 0)
def script_loops_agent_runs(lt):
    """Entries the person's repos declare (package.json scripts, Makefile / justfile recipes, CI run steps), in files they wrote,
    that start an agent headless with the call inside a loop: a loop on its line, a shell for / while ... done or an indented block
    around it, or a loop in the entry's body around the script that starts the agent. A loop elsewhere in the file, or an install
    line, does not count."""
    got = [e for e in declared_runners(lt) if _agent_runner(lt, e, in_loop=True)]
    return {"value": len(got), "evidence": [f"{e['kind']}: {e['name']}" for e in got[:3]], "note": "a loop over 'the list' is any loop around the call"}


@_fixture("fresh-run-per-task", "L4", 0)
def _(fx):
    _runner_repo(fx)
    return {"value": 3}   # each.sh (shell for-loop), sweep.mjs (for-of with the SDK), batch (a body loop around triage.mjs)


# ====================================================================== add-machinery-only-when-needed
@check("add-machinery-only-when-needed", "L2", 0)
def sessions_using_agents(lt):
    """Interactive sessions with a typed message in which the main thread called the Agent (older name: Task) tool, with how
    many typed messages came before the first call."""
    typed = _typed_sessions(lt)
    first = {}
    for c in lt.calls:
        if c["name"] in ("Agent", "Task") and not c["sc"] and c["s"] in typed:
            first.setdefault(c["s"], c["t"])
    before = sorted(sum(1 for m in lt.typed if m["s"] == s and m["t"] < t) for s, t in first.items())
    med = before[len(before) // 2] if before else 0
    return {"value": len(first), "evidence": [f"{len(first)} of {len(typed)} typed sessions; median {med} messages before the first"] if first else [],
            "note": "shows use, not need"}


@_fixture("add-machinery-only-when-needed", "L2", 0)
def _(fx):
    a = fx.session("~/work/shop", entrypoint="claude-vscode"); a.typed("hi"); a.subagent("Explore", "find auth")                      # counts
    b = fx.session("~/work/shop", entrypoint="claude-desktop"); b.typed("hi"); b.call("Agent", {"subagent_type": "x", "prompt": "y"}); b.call("Agent", {"subagent_type": "x", "prompt": "z"})   # counts once
    c = fx.session("~/work/shop", entrypoint="cli"); c.typed("hi"); c.call("Task", {"subagent_type": "x", "prompt": "y"})              # counts: older name
    d = fx.session("~/work/shop", entrypoint="claude-vscode"); d.typed("hi"); d.call("Bash", {"command": "ls"})                         # trap: none
    e = fx.session("~/work/shop", entrypoint="claude-vscode"); e.typed("hi"); e.subagent("Explore", "x", by_claude=False, calls=[("Agent", {"prompt": "nested"}, "ok")])   # trap: only inside a sub-agent
    h = fx.session("~/work/shop", entrypoint="sdk-cli"); h.typed("go"); h.call("Agent", {"subagent_type": "x", "prompt": "y"})          # trap: headless
    return {"value": 3}


@check("add-machinery-only-when-needed", "L2", 2)
def plugins_installed_then_off(lt):
    """Can't tell. 'Frameworks tried then switched off' needs two things no file holds: which installed plugin is a framework (a
    plugin is not one by being a plugin), and that it was tried. A switched-off entry in enabledPlugins also comes from a teammate's
    committed project settings, for a plugin never installed here (the skeptic, 11-05)."""
    return {"value": None, "evidence": [],
            "note": "can't tell from a laptop: which plugins are frameworks is not recorded, and neither is whether one was tried before it "
                    "was switched off; a question for the call ('which setups did you try and drop, and why?')"}


@_fixture("add-machinery-only-when-needed", "L2", 2)
def _(fx):
    ent = lambda: [{"scope": "user", "installPath": "/x", "installedAt": "2026-10-01T00:00:00Z"}]
    fx.file("~/.claude/plugins/installed_plugins.json", json.dumps({"version": 2, "plugins": {"a@m": ent(), "b@m": ent(), "c@m": ent()}}))
    fx.settings("user", {"enabledPlugins": {"a@m": False, "b@m": True}})
    fx.repo("~/work/shop", {"README.md": "x"}, by="teammate")
    fx.commit("~/work/shop", {".claude/settings.json": json.dumps({"enabledPlugins": {"c@m": False}})}, by="teammate")   # a teammate's switch-off, never tried here
    fx.session("~/work/shop").typed("x")
    return {"value": None}


# Built once before the checks split into workers (evaluate.py / run_portable.py run_prewarm_hooks); each is memoized on
# the Laptop and used by several checks.
def _rule_file_authors(lt):
    """Whose each rule file Claude Code loaded mid-session is (nested_memory): the git history of every repo they lie in,
    read once before the checks fork. Read inside a check, each worker paid one `git log` per worktree (11-06: 33s for
    put-rules-where-they-apply L3#0 under the pool, 4.4s alone)."""
    _warm_authors(lt, {a.get("path") for a in _atts(lt, "nested_memory") if a.get("path")})


def _hooks_and_checks(lt):
    """The declared-check matchers (each repo's, and all repos' as one) and every repo's git hook files with whose they are,
    built once before the checks fork. Built inside a check, every worker compiled the all-repos matcher again (3.5s on
    one laptop) and asked git for each repo's hooks folder (11-06: enforce-fixed-rules-in-code L3#0 44s under the pool)."""
    _every_declared_rx(lt)
    for r in lt.repos:
        _checks_of(lt, (r,))
    with ThreadPoolExecutor(max_workers=min(16, (os.cpu_count() or 4) * 2)) as pool:   # git runs in its own process
        files = [f for fs in pool.map(lambda r: _git_hook_files(lt, r), lt.repos) for f in fs]
    _warm_authors(lt, files + [os.path.join(r, ".pre-commit-config.yaml") for r in lt.repos])


PREWARM = [_typed_sessions, _headless_first, _own_first, _repeats, _rule_file_authors, _hooks_and_checks]
