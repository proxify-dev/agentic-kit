"""Group 1: the rewritten checks for ten practices (retire-setup, compact-on-purpose, bound-the-task, isolate-side-work,
write-a-program, commit-in-small-steps, let-the-agent-fetch, run-the-agent-in-a-box, parallel-work, ask-without-leading).

Helpers local to this file (base.py lacked them): _raw (full records of a few attachment/system types from the session files,
because base keeps only 600 characters of each), _mine_file (a file is the person's by the one ownership rule, lt.owner: theirs unless a
receipt says installed or git says a teammate's; never by where it lies), _mine_settings (their own settings files), _own_artifacts (skill, agent, command files the person wrote),
_own_scripts (source files they wrote that start the agent, read as code), _turns (when each session's agent was working),
_commits_through_claude (commits whose hash a session's own Bash result printed), plugin/hook/MCP readers.
Each @fixture plants a stranger laptop (git: personal + work emails, teammate commits; VS Code/desktop sessions) and a trap.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from base import check
from fixture import fixture

RETIRE = "retire-setup-that-no-longer-earns-its-place"
COMPACT = "compact-on-purpose"
BOUND = "bound-the-task"
ISOLATE = "isolate-side-work"
PROGRAM = "write-a-program-that-runs-the-agent"
COMMIT = "commit-in-small-revertible-steps"
FETCH = "let-the-agent-fetch"
BOX = "run-the-agent-in-a-box"
PARALLEL = "parallel-work-kept-apart"
ASK = "ask-without-leading"


# ====================================================================== helpers (check side)
def _memo(lt, key, fn):
    d = lt.__dict__.setdefault("_g1", {})
    if key not in d:
        d[key] = fn()
    return d[key]


def _raw(lt, needles):
    """Full records (parsed) whose line holds one of the needles, in the window, once per uuid. Adds _sid, _sc.
    (11-05: one fast shared search, lt.grep, instead of reading every log line by line once per needle set.)"""
    return _memo(lt, ("raw",) + tuple(needles),
                 lambda: lt.grep(b"|".join(re.escape(n.encode()) for n in needles)))


def _atts(lt, type_, needle=None):
    """Full attachment records of one type from sessions the person worked in (not headless, not a sub-agent's log)."""
    recs = _raw(lt, (needle or '"%s"' % type_,))
    return [r for r in recs if isinstance(r.get("attachment"), dict) and r["attachment"].get("type") == type_
            and not r["_sc"] and lt.interactive(r["_sid"])]


def _inp(c):
    try:
        return json.loads(c["input"])
    except (ValueError, TypeError):
        return {}


def _str(c, key):
    m = re.search(r'"%s":\s*"((?:[^"\\]|\\.)*)"' % re.escape(key), c.get("input") or "")
    return m.group(1).encode().decode("unicode_escape", "ignore") if m else None


def _calls(lt, *names):
    return [c for c in lt.calls if c["name"] in names and lt.interactive(c["s"])]


def _day(t):
    return int(t // 86400)


def _norm_mcp(name):
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _pmap(fn, items, workers=32):
    """fn over items, in order, on threads: git and file reads wait on the disk, so they overlap."""
    from concurrent.futures import ThreadPoolExecutor
    items = list(items)
    if len(items) < 3:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def _real(lt, path):
    """os.path.realpath, asked once per path (each rule file's path is resolved by half a dozen helpers)."""
    memo = lt.__dict__.setdefault("_g1_real", {})
    if path not in memo:
        memo[path] = os.path.realpath(path)
    return memo[path]


def _top(lt, path):
    """lt.toplevel(path) with the same answers, but where base would run `git rev-parse --show-toplevel` (a folder outside every
    known repo, or a worktree nested in one) this walks up to the nearest .git instead, which is what git answers."""
    real = _real(lt, path)
    d = real if os.path.isdir(real) else os.path.dirname(real)
    memo = lt.__dict__.setdefault("_top_memo", {})
    if d not in memo:
        hit = None
        for r in lt.repos:
            if (d == r or d.startswith(r + os.sep)) and (hit is None or len(r) > len(hit)):
                hit = r
        if os.path.exists(os.path.join(d, ".git")):
            hit = d   # a folder holding .git (file or folder) is its own root, as in base.toplevel
        elif hit:
            cur = d
            while cur != hit and cur.startswith(hit):
                if os.path.exists(os.path.join(cur, ".git")):
                    hit = None
                    break
                cur = os.path.dirname(cur)
        if not hit and os.path.isdir(d):
            cur = d
            while True:
                if os.path.exists(os.path.join(cur, ".git")):
                    hit = cur
                    break
                nxt = os.path.dirname(cur)
                if nxt == cur:
                    break
                cur = nxt
        memo[d] = hit or None
    return memo[d]


def _prefetch_yours(lt, paths):
    """Read who wrote each file in `paths` with one `git log` per repo, limited to the files asked about, for repos base didn't read
    whole (a repo already read whole, lt._authors, is used as is). Keeps only the counts {commits, theirs}; whose the file is
    is then decided by lt.owner (the one ownership rule), in _yours."""
    memo = lt.__dict__.setdefault("_g1_yours", {})
    lt.repos, lt.identity     # read once here, before any thread: the first read of identity swaps the HOME variable
    asked = {}
    for p in paths:
        real = _real(lt, p)
        if real in memo:
            continue
        top = _top(lt, real)
        if not top:
            memo[real] = (None, 0, 0)
        elif top in lt.__dict__.get("_auth_memo", {}):
            a = lt.authorship_counts(real)       # what lt.authorship counts, without resolving the path again
            memo[real] = (top, a["commits"], a["theirs"])
        else:
            asked.setdefault(top, set()).add(real)
    def read(item):
        top, reals = item
        rels = {r: os.path.relpath(r, top).replace(os.sep, "/") for r in reals}
        idx, who = {}, None
        for line in lt._git("--literal-pathspecs", "log", "--full-history", "--format=%x00%an <%ae>", "--name-only", "--no-renames", "--",
                            *rels.values(), cwd=top, timeout=120).splitlines():
            if line.startswith("\x00"):
                who = line[1:]
            elif line and who:
                idx.setdefault(line, []).append(who)
        out = {}
        for r, rel in rels.items():
            log = idx.get(rel, [])
            out[r] = (top, len(log), sum(1 for a in log if lt.is_mine(a)))
        return out
    for part in _pmap(read, asked.items()):
        memo.update(part)


def _yours(lt, path):
    """lt.owner(path) == "theirs" for a file (the same answer as lt.authorship(path)["yours"]), without reading a whole repo's
    history when it was not read already. Untracked and unrecorded files are theirs (ruling 11-05); a receipt says installed;
    git only makes a file a teammate's."""
    _prefetch_yours(lt, [path])
    top, n, mine = lt.__dict__["_g1_yours"][_real(lt, path)]
    return lt.owner(path, top, n, mine) == "theirs"      # the link as given: a lock receipt is read on the link and its target


def _mine_file(lt, path):
    """A skill / agent / command / script / config file is the person's by the one ownership rule (lt.owner, through _yours): theirs
    unless a receipt says installed (a published marketplace plugin, a skills lock) or git says most of its commits are someone
    else's. Never by place, and git need not track the file."""
    return _yours(lt, path)


def _mine_settings(lt, path):
    """A settings file Claude Code reads is the person's by the one ownership rule (_yours). Which scope a file is (user, project,
    managed) is the caller's scope check, not this one. What is inside is not judged here: an installer may have written a hook
    into the person's own file."""
    return _yours(lt, path)


def _settings(lt):
    return _memo(lt, "settings", lambda: lt.settings_files())


def _own_settings(lt):
    return [(sc, p, d) for sc, p, d in _settings(lt) if sc != "managed" and isinstance(d, dict) and _mine_settings(lt, p)]


def _hooks_of(data):
    """(event, matcher, command) for each hook in a settings or hooks.json dict."""
    out = []
    h = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(h, dict):
        return out
    for ev, groups in h.items():
        for g in groups if isinstance(groups, list) else []:
            if not isinstance(g, dict):
                continue
            for hk in g.get("hooks") or []:
                if isinstance(hk, dict):
                    out.append((ev, g.get("matcher"), hk.get("command") or hk.get("prompt") or hk.get("type") or ""))
    return out


def _json(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _plugins(lt):
    """{plugin key: {name, path, enabled}} from installed_plugins.json and enabledPlugins in the settings files."""
    def build():
        inst = _json(os.path.join(lt.claude, "plugins", "installed_plugins.json")).get("plugins") or {}
        enabled = {}
        for _, _, d in _settings(lt):
            for k, v in (d.get("enabledPlugins") or {}).items():
                enabled[k] = bool(v)
        out = {}
        for key, entries in inst.items():
            e = (entries[0] if isinstance(entries, list) and entries else entries) or {}
            out[key] = {"name": key.split("@")[0], "path": e.get("installPath") or "", "enabled": enabled.get(key, False)}
        return out
    return _memo(lt, "plugins", build)


def _plugin_hooks(lt, only_enabled=True):
    """(plugin name, event, matcher, command) from the hooks.json of installed plugins."""
    out = []
    for p in _plugins(lt).values():
        if only_enabled and not p["enabled"]:
            continue
        for ev, m, cmd in _hooks_of(_json(os.path.join(p["path"], "hooks", "hooks.json"))):
            out.append((p["name"], ev, m, cmd))
    return out


def _mcp_servers_in(d):
    d = d.get("mcpServers") if isinstance(d.get("mcpServers"), dict) else d
    return {k: v for k, v in d.items() if isinstance(v, dict) and any(x in v for x in ("command", "url", "type", "args"))}


def _mcp_configured(lt):
    """{tool-prefix name: (where, config)} for every MCP server the laptop configures: ~/.claude.json, repo .mcp.json,
    settings files, and enabled plugins (named plugin_<plugin>_<server>)."""
    def build():
        out = {}
        cj = _json(os.path.join(lt.home, ".claude.json"))
        for n, v in _mcp_servers_in({"mcpServers": cj.get("mcpServers") or {}}).items():
            out[_norm_mcp(n)] = (os.path.join(lt.home, ".claude.json"), v)
        for proj in (cj.get("projects") or {}).values():
            for n, v in _mcp_servers_in({"mcpServers": (proj or {}).get("mcpServers") or {}}).items():
                out.setdefault(_norm_mcp(n), (os.path.join(lt.home, ".claude.json"), v))
        for d in dict.fromkeys(list(lt.repos) + lt.session_folders()):
            p = os.path.join(d, ".mcp.json")
            for n, v in _mcp_servers_in(_json(p)).items():
                out.setdefault(_norm_mcp(n), (p, v))
        for sc, p, dd in _settings(lt):
            for n, v in _mcp_servers_in({"mcpServers": dd.get("mcpServers") or {}}).items():
                out.setdefault(_norm_mcp(n), (p, v))
        for pl in _plugins(lt).values():
            if not pl["enabled"]:
                continue
            for p in (os.path.join(pl["path"], ".mcp.json"), os.path.join(pl["path"], ".claude-plugin", "plugin.json")):
                for n, v in _mcp_servers_in(_json(p)).items():
                    out.setdefault("plugin_%s_%s" % (_norm_mcp(pl["name"]), _norm_mcp(n)), (p, v))
        return out
    return _memo(lt, "mcp", build)


def _mcp_prefix(name):
    parts = name.split("__")
    return parts[1] if name.startswith("mcp__") and len(parts) > 2 else None


def _skill_uses(lt):
    """Every time a skill was used in a session the person worked in: [(name, how)]. Skill tool calls, skill_mention
    attachments, typed /name commands. Names kept whole (plugin:skill)."""
    def build():
        out = []
        for c in _calls(lt, "Skill"):
            n = _str(c, "skill")
            if n:
                out.append((n.lstrip("/"), "call", c["s"]))
        for a in lt.attachments:
            if a["type"] == "skill_mention" and lt.interactive(a["s"]) and not a["sc"]:
                m = re.search(r'"skillName":\s*"([^"]+)"', a["data"])
                if m:
                    out.append((m.group(1), "mention", a["s"]))
        for c in lt.all_commands():
            if lt.interactive(c["s"]):
                out.append((c["name"].lstrip("/"), "command", c["s"]))
        return out
    return _memo(lt, "skill_uses", build)


def _agent_launches(lt):
    """Agent / Task tool calls in interactive sessions: [(subagent_type, call)]. A blank type is the general-purpose agent."""
    return [(_str(c, "subagent_type") or "general-purpose", c) for c in _calls(lt, "Agent", "Task")]


# ---- the person's own files
ART = re.compile(r"(^|/)(skills/[^/]+/SKILL\.md|agents/[^/]+\.md|commands/[^/]+\.md)$")


def _repo_listing(lt, repo):
    return _memo(lt, ("ls", repo), lambda: lt.git("ls-files", "--cached", "--others", "--exclude-standard", cwd=repo).splitlines())


def _all_listings(lt):
    """Every repo's file list, asked on threads (untracked files make each one slow on a big working tree)."""
    def index_size(r):      # the biggest index first, so the slowest listings start together and the rest fill in behind them
        try:
            return -os.path.getsize(os.path.join(r, ".git", "index"))
        except OSError:
            return 0
    order = sorted(lt.repos, key=index_size)
    return dict(zip(order, _pmap(lambda r: _repo_listing(lt, r), order)))


def _installed_hashes(lt, sizes):
    """sha1 of the .md files of installed plugins, for those whose size is one of `sizes` (a file of another size can't be a copy of a
    candidate, so it is not read)."""
    def build():
        def one_dir(path):      # (the .md files in a folder, its sub-folders), as os.walk sees them: links to folders are not followed
            mds, subs = [], []
            try:
                with os.scandir(path) as it:
                    for e in it:
                        if e.is_dir():
                            if not e.is_symlink():
                                subs.append(e.path)
                        elif e.name.endswith(".md"):
                            mds.append(e.path)
            except OSError:
                pass
            return mds, subs
        mds, frontier = [], [p["path"] for p in _plugins(lt).values() if p["path"] and os.path.isdir(p["path"])]
        while frontier:         # one level of every plugin's tree at a time, the folders of a level read side by side
            frontier_next = []
            for found, subs in _pmap(one_dir, frontier):
                mds += found
                frontier_next += subs
            frontier = frontier_next
        def chunk(paths):
            hs = set()
            for path in paths:
                try:
                    if os.stat(path).st_size in sizes:
                        with open(path, "rb") as fh:
                            hs.add(hashlib.sha1(fh.read()).hexdigest())
                except OSError:
                    pass
            return hs
        return set().union(*_pmap(chunk, [mds[i:i + 500] for i in range(0, len(mds), 500)]))
    return _memo(lt, ("inst_hash", frozenset(sizes)), build)


def _name_of(kind, path, text):
    if kind == "skill":
        return os.path.basename(os.path.dirname(path))
    m = re.search(r"^name:\s*['\"]?([^\s'\"]+)", text.split("\n---", 1)[0], re.M) if text.startswith("---") else None
    return m.group(1) if m else os.path.splitext(os.path.basename(path))[0]


def _locked_skills(lt):
    """Names of skills a skills installer recorded in a lock file (a repo's skills-lock.json, ~/.agents/.skill-lock.json): installed,
    whoever's repo they sit in."""
    def build():
        names = set()
        paths = [os.path.join(r, "skills-lock.json") for r in lt.repos]
        paths += [os.path.join(lt.home, ".agents", ".skill-lock.json"), os.path.join(lt.claude, "skills-lock.json"),
                  os.path.join(lt.claude, ".skill-lock.json")]
        for p in paths:
            sk = _json(p).get("skills")
            if isinstance(sk, dict):
                names |= set(sk)
        return names
    return _memo(lt, "locked_skills", build)


def _own_artifacts(lt):
    """Skill / agent / command files the person wrote: [{kind, name, path, text}]. In a repo: under .claude/ or in a plugin
    (a folder with .claude-plugin/plugin.json). In ~/.claude too. In every place the same rule (_mine_file, lt.owner, ruling
    11-05): theirs unless a receipt says installed or most of its commits are someone else's. An untracked file is theirs. Two
    extra "installed" receipts stay here, read off the files: a file byte-identical to one an installed plugin ships is a copy,
    and a skill a skills lock names is installed (_locked_skills reads every lock the repos hold, not only the ones lt.owner reads)."""
    def build():
        found, seen = [], set()
        cands = []
        listings = _all_listings(lt)
        for repo in lt.repos:
            ls = None
            for rel in listings[repo]:
                m = rel.endswith(".md") and ART.search(rel)      # every file ART names ends in .md
                if not m:
                    continue
                ls = ls if ls is not None else set(listings[repo])
                prefix = rel[:m.start(2)]
                if prefix.endswith(".claude/") or prefix == ".claude/" or (prefix + ".claude-plugin/plugin.json") in ls:
                    cands.append(os.path.join(repo, rel))
        for sub in ("skills/*/SKILL.md", "agents/*.md", "commands/*.md"):
            import glob
            cands += glob.glob(os.path.join(lt.claude, sub))
        sizes = set()
        for p in cands:
            try:
                sizes.add(os.path.getsize(_real(lt, p)))
            except OSError:
                pass
        hs = _installed_hashes(lt, sizes)
        _prefetch_yours(lt, cands)
        for p in cands:
            rp = _real(lt, p)
            if rp in seen or not os.path.isfile(rp):
                continue
            seen.add(rp)
            try:
                raw = open(rp, "rb").read()
            except OSError:
                continue
            if hashlib.sha1(raw).hexdigest() in hs or not _mine_file(lt, rp):
                continue
            text = raw.decode("utf-8", "replace")
            kind = "skill" if rp.endswith("SKILL.md") else ("agent" if os.sep + "agents" + os.sep in p else "command")
            if kind == "skill" and _name_of(kind, p, text) in _locked_skills(lt):
                continue
            if kind == "agent" and _name_of(kind, p, text) in _BUILTIN_AGENTS:   # an override of Claude Code's own type
                continue
            found.append({"kind": kind, "name": _name_of(kind, p, text), "path": p, "text": text})
        return found
    return _memo(lt, "own_art", build)


from base import builtin_agent_types
_BUILTIN_AGENTS = builtin_agent_types()

SCRIPT_EXT = (".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".rb", ".pl", ".php", ".go", ".rs", ".ps1",
              ".bat", ".cmd", ".yml", ".yaml", ".toml", ".mk", ".fish", ".nu")
SCRIPT_NAMES = ("Makefile", "makefile", "justfile", "Justfile", "package.json", "Dockerfile")


CODE_EXT = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".rb", ".pl", ".php", ".go", ".rs")
DATA_EXT = (".yml", ".yaml", ".toml", ".json")
# how a program starts the agent, read as code and not as a word in text (skeptic, 11-05: a string, a comment or this scanner's own
# patterns are not a program). A command line: `claude ... -p` / `--print` standing where a command stands.
CLI_FLAG = re.compile(r"claude(?:\.exe)?[ \t][^\n|;&]*?(?<![\w-])(?:-p|--print)(?![\w-])")
# the same command inside a string or a list in a program: "claude -p ..." or ["claude", "-p", ...]
STR_CLI = re.compile(r"""["']claude(?:\.exe)?(?:["']\s*,\s*(?:["'][^"'\n]*["']\s*,\s*)*?["'](?:-p|--print)["']|[ \t][^"'\n]{0,200}?(?<![\w-])(?:-p|--print)(?![\w-]))""")
# an import of the Agent SDK, at the start of a statement
SDK_IMPORT = re.compile(r"""(?m)^[ \t]*(?:import[ \t]+claude_(?:agent|code)_sdk\b|from[ \t]+claude_(?:agent|code)_sdk\b|"""
                        r"""(?:import|export)\b[^\n;]*\bfrom[ \t]*['"]@anthropic-ai/claude-(?:agent-sdk|code)['"]|"""
                        r"""[^\n]*\brequire\([ \t]*['"]@anthropic-ai/claude-(?:agent-sdk|code)['"][ \t]*\))""")
BEFORE_CMD = re.compile(r"(?:^|[;&|(`]|\$\(|\bexec|\bxargs|\bthen|\bdo|\bsudo)[ \t]*$")
BEFORE_CMD_DATA = re.compile(r"""(?:^|[;&|(`:"'-]|\$\(|\bexec|\bxargs|\bthen|\bdo|\bsudo)[ \t]*$""")
ENV_ASSIGN = re.compile(r"(?:[A-Za-z_]\w*=\S*[ \t]+)+$")
PATH_TAIL = re.compile(r"[\w./~$-]*/$")
DOCSTRING = re.compile(r"(\"\"\"|''')[\s\S]*?\1")


def _code_view(rel, text):
    """The text of a source file without its comments: lines that start with a comment mark, and a Python file's docstrings."""
    if rel.endswith(".py"):
        text = DOCSTRING.sub("", text)
    keep = []
    for ln in text.split("\n"):
        st = ln.lstrip()
        if st.startswith(("//", "/*", "*")) or (st.startswith("#") and not st.startswith("#!")):
            continue
        keep.append(ln)
    return "\n".join(keep)


def _balanced(code, i):
    """The text from the bracket at code[i] to its partner (quotes are not parsed: enough for an options object)."""
    depth, j = 0, i
    while j < len(code):
        ch = code[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return code[i:j + 1]
        j += 1
    return code[i:]


def _statement_end(code, i):
    """Where the statement that holds code[i] ends: the first closing bracket that nothing in it opened, or a line end outside brackets."""
    depth, j = 0, i
    while j < len(code):
        ch = code[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                return j
        elif ch == "\n" and depth == 0:
            return j
        j += 1
    return j


def _agent_calls(rel, code):
    """How a source file starts the agent, as [(kind, text)]: "cli" (a command line, or a command string or list in a program) with the
    command's own line / statement, "sdk" (an Agent SDK import) with the whole code. Each kind is found where code stands: a command at the
    start of a command line, a string or list in a program, an import at the start of a statement."""
    name = rel.rpartition("/")[2]
    ext = ("." + name.rpartition(".")[2].lower()) if "." in name else ""
    out = []
    if ext in CODE_EXT:
        for m in STR_CLI.finditer(code):
            out.append(("cli", code[m.start():_statement_end(code, m.start())]))
        if SDK_IMPORT.search(code):
            out.append(("sdk", code))
        return out
    joined = code.replace("\\\n", " ")
    before_rx = BEFORE_CMD_DATA if ext in DATA_EXT else BEFORE_CMD
    for m in CLI_FLAG.finditer(joined):
        line_start = joined.rfind("\n", 0, m.start()) + 1
        before = joined[line_start:m.start()]
        before = PATH_TAIL.sub("", before)
        before = ENV_ASSIGN.sub("", before)
        if before_rx.search(before):
            end = joined.find("\n", m.end())
            out.append(("cli", joined[m.start():len(joined) if end < 0 else end]))
    return out


def _script_texts(lt):
    """[(repo, rel, code, calls)] of every source file in the person's repos that starts the agent (_agent_calls on its code, comments
    left out). Each file is read once, and a file with no 'claude' in it is skipped from its bytes before it is decoded."""
    def build():
        listings = _all_listings(lt)
        wanted = [(repo, rel) for repo in lt.repos for rel in listings[repo]
                  if rel.endswith(SCRIPT_EXT) or rel.rpartition("/")[2] in SCRIPT_NAMES]
        def scan(chunk):
            out = []
            for repo, rel in chunk:
                try:
                    with open(os.path.join(repo, rel), "rb") as fh:
                        raw = fh.read(400001)
                except OSError:
                    continue
                if len(raw) > 400000 or b"claude" not in raw:
                    continue
                code = _code_view(rel, raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n"))
                calls = _agent_calls(rel, code)
                if calls:
                    out.append((repo, rel, code, calls))
            return out
        return [x for part in _pmap(scan, [wanted[i:i + 200] for i in range(0, len(wanted), 200)]) for x in part]
    return _memo(lt, "script_texts", build)


def _own_scripts(lt):
    """[(repo, rel, code, calls)] of source files that start the agent and that the person wrote: theirs by the one ownership rule
    (_yours, lt.owner). A file nobody committed is theirs too; a teammate's or an installed one is not."""
    def build():
        hits = _script_texts(lt)
        _prefetch_yours(lt, [os.path.join(r, rel) for r, rel, _, _ in hits])
        return [x for x in hits if _yours(lt, os.path.join(x[0], x[1]))]
    return _memo(lt, "own_scripts", build)


def _commits(lt, repo):
    """[(commit time, is the person's, short hash)] from every branch of the repo in the window (plus 2 hours before it)."""
    def build():
        rows = []
        for line in lt.git("log", "--all", "--no-merges", "--format=%ct|%h|%an <%ae>", "--since=%d" % (lt.start - 7200), cwd=repo).splitlines():
            ct, h, who = line.split("|", 2)
            if int(ct) >= lt.start - 7200:
                rows.append((int(ct), lt.is_mine(who), h))
        return sorted(rows)
    return _memo(lt, ("commits", repo), build)


def _prefetch_commits(lt, repos):
    """The commit lists of several repos at once, one git process each on its own thread."""
    lt.repos, lt.identity     # read once before the threads (identity swaps the HOME variable on its first read)
    _pmap(lambda r: _commits(lt, r), repos)


def _untyped_hint():
    return None


# ====================================================================== helpers (fixture side)
def _sess(fx, cwd, ep="claude-vscode", **kw):
    return fx.session(cwd, entrypoint=ep, **kw)


def _noon(days_ago):
    return (int(time.time() // 86400) - days_ago) * 86400 + 43200


def _commit_at(fx, repo, files, by, t, message="change", delete=()):
    """Commit with a chosen date (so a commit can sit before a session's first edit)."""
    for rel in delete:
        try:
            os.remove(os.path.join(fx.p(repo), rel))
        except OSError:
            pass
    d = "%d +0000" % int(t)
    old = {k: os.environ.get(k) for k in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")}
    os.environ["GIT_AUTHOR_DATE"] = os.environ["GIT_COMMITTER_DATE"] = d
    try:
        fx.commit(repo, files, by=by, message=message)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _head(fx, repo):
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=fx.p(repo), capture_output=True, text=True).stdout.strip()


def _plugin(fx, name, market="mk", hooks=None, mcp=None, enabled=True):
    path = fx.p("~/.claude/plugins/cache/%s/%s/1.0" % (market, name))
    os.makedirs(path, exist_ok=True)
    if hooks:
        fx.file(os.path.join(path, "hooks", "hooks.json"), json.dumps({"hooks": hooks}))
    if mcp:
        fx.file(os.path.join(path, ".mcp.json"), json.dumps({"mcpServers": mcp}))
    return "%s@%s" % (name, market), path


def _install(fx, plugins):
    """plugins: [(key, path, enabled)] -> installed_plugins.json + enabledPlugins in user settings (merged by caller)."""
    fx.file("~/.claude/plugins/installed_plugins.json", json.dumps({"version": 2, "plugins": {
        k: [{"scope": "user", "installPath": p, "version": "1.0"}] for k, p, _ in plugins}}))
    return {k: bool(e) for k, _, e in plugins}


def _hook_cmd(cmd):
    return {"type": "command", "command": cmd}


# ====================================================================== retire-setup-that-no-longer-earns-its-place
# Agent setup files by name alone: SKILL.md, a plugin manifest and .mcp.json are names Claude Code gave; nothing else uses them.
SETUP_NAMED = re.compile(r"(^|/)(skills/[^/]+/SKILL\.md|\.claude-plugin/plugin\.json|\.mcp\.json)$")
# under a .claude/ folder, an agent, command or hook file is setup
SETUP_IN_DOT_CLAUDE = re.compile(r"(^|/)\.claude/(agents/[^/]+\.md|commands/[^/]+\.md|hooks/[^/]+\.(?:py|sh|mjs|js|json))$")
# agents/, commands/, hooks/ elsewhere is setup only inside a plugin (a folder holding .claude-plugin/plugin.json): the same
# names are app code (src/hooks/useAuth.js) and doc pages (docs/agents/overview.md) in other repos
SETUP_IN_PLUGIN = re.compile(r"^(?P<root>(?:.*/)?)(?:agents/[^/]+\.md|commands/[^/]+\.md|hooks/[^/]+\.(?:py|sh|mjs|js|json))$")


def _is_setup_deletion(lt, repo, commit, path):
    """A path deleted in `commit` is agent setup: named by Claude Code (SKILL.md, plugin.json, .mcp.json), under .claude/, or in
    a plugin that held a manifest in the commit before."""
    if SETUP_NAMED.search(path) or SETUP_IN_DOT_CLAUDE.search(path):
        return True
    m = SETUP_IN_PLUGIN.match(path)
    if not m:
        return False
    manifest = m.group("root") + ".claude-plugin/plugin.json"
    return bool(lt.git("ls-tree", "--name-only", commit + "^", "--", manifest, cwd=repo).strip())


@check(RETIRE, "L2", 1)
def deleted_setup_files(lt):
    """Skill, agent, hook, command, plugin-manifest or .mcp.json files deleted in a commit by one of the person's own git
    identities, in the repos their sessions ran in, inside the window. A file counts as setup by its place (see _is_setup_deletion):
    app code in a hooks/ folder and doc pages in agents/ or commands/ do not."""
    n, ev, commits = 0, [], set()
    logs = _pmap(lambda repo: lt.git("log", "--all", "--diff-filter=D", "--name-only", "--format=@@%ct|%h|%an <%ae>", "--since=%d" % lt.start, cwd=repo),
                 lt.repos)       # only the window counts: git stops at its start
    for repo, log in zip(lt.repos, logs):
        who = None
        for line in log.splitlines():
            if line.startswith("@@"):
                ct, h, a = line[2:].split("|", 2)
                who = (int(ct) >= lt.start and lt.is_mine(a)) and h
                continue
            if who and _is_setup_deletion(lt, repo, who, line):
                n += 1
                commits.add((repo, who))
                if len(ev) < 3:
                    ev.append(line)
    return {"value": n, "evidence": ev + ["%d commits" % len(commits)],
            "note": "counts deleted files; a file deleted in a rename is not seen as a deletion; repos not opened in a session are not read; "
                    "an agents/ or commands/ folder outside .claude/ and outside a plugin is not read as setup"}


@fixture(RETIRE, "L2", 1)
def _(fx):
    fx.repo("~/work/shop", {"README.md": "x", ".claude/skills/old/SKILL.md": "a", ".claude/agents/rev.md": "b", "notes/a.md": "n",
                            "plug/.claude-plugin/plugin.json": "{}", "plug/agents/helper.md": "h", "src/hooks/useAuth.js": "js",
                            "docs/agents/overview.md": "doc", "docs/commands/run.md": "doc"}, by="work")
    fx.commit("~/work/shop", {".claude/commands/ship.md": "c", ".claude/hooks/guard.sh": "d"}, by="home")
    _sess(fx, "~/work/shop").typed("clean up the setup")
    fx.commit("~/work/shop", {"keep.txt": "k"}, by="teammate")
    fx.commit("~/work/shop", {".claude/skills/team/SKILL.md": "t"}, by="teammate")
    _commit_at(fx, "~/work/shop", {}, "work", time.time() - 3600, "drop old setup",
               delete=[".claude/skills/old/SKILL.md", ".claude/agents/rev.md", "notes/a.md", ".claude/commands/ship.md", "plug/agents/helper.md",
                       "src/hooks/useAuth.js", "docs/agents/overview.md", "docs/commands/run.md"])
    # the trap: app code in a hooks/ folder and doc pages in agents/ and commands/ folders are not setup; a teammate deleting their
    # own skill is not credited; the person's home email counts too
    _commit_at(fx, "~/work/shop", {}, "teammate", time.time() - 1800, "teammate cleanup", delete=[".claude/skills/team/SKILL.md"])
    _commit_at(fx, "~/work/shop", {}, "home", time.time() - 900, "drop guard", delete=[".claude/hooks/guard.sh"])
    return {"value": 5}   # old skill, rev agent, ship command, the plugin's helper agent (work email) + guard hook (home email)


@check(RETIRE, "L2", 2)
def skills_listed_never_used(lt):
    """Skills Claude Code listed to the person's sessions (skill_listing attachments, any entrypoint but headless) that were
    never loaded, mentioned or typed. Matched on the whole name (plugin:skill). Value = listed and never used."""
    listed = set()
    for r in _atts(lt, "skill_listing"):
        for m in re.finditer(r"^- (\S+?)(?=: |\s*$)", r["attachment"].get("content") or "", re.M):
            listed.add(m.group(1))
    used = {n for n, _, _ in _skill_uses(lt)}
    own = {a["name"] for a in _own_artifacts(lt) if a["kind"] == "skill"}
    never = sorted(listed - used)
    split = "own %d listed / %d used; installed %d listed / %d used" % (
        len({n for n in listed if n in own}), len({n for n in listed & used if n in own}),
        len({n for n in listed if n not in own}), len({n for n in listed & used if n not in own}))
    return {"value": len(never), "evidence": ["%d listed, %d used" % (len(listed), len(listed & used)), split] + never[:1],
            "note": "a skill used only before the window or only in a headless run reads unused; listing lines cut by Claude Code are missed"}


@fixture(RETIRE, "L2", 2)
def _(fx):
    s = _sess(fx, "~/work/shop")
    s.attachment("skill_listing", content="- deploy: Ship it\n- lint\n- acme:review: Review code\n- other", isInitial=True)
    s.typed("deploy this")
    s.call("Skill", {"skill": "deploy"})
    s.attachment("skill_mention", skillName="acme:review")
    d = _sess(fx, "~/work/shop", ep="claude-desktop")
    d.typed("check style")
    d.command("/lint")           # a desktop session: no history.jsonl line
    h = _sess(fx, "~/work/shop", ep="sdk-cli")      # trap: a headless run's own listing and use are not the person's
    h.attachment("skill_listing", content="- ghost: Only headless\n- phantom", isInitial=True)
    h.typed("go"); h.call("Skill", {"skill": "other"})
    return {"value": 1}   # only "other" is listed and unused (ghost / phantom are headless; "other" used only in the headless run)


@check(RETIRE, "L2", 3)
def plugins_enabled_never_used(lt):
    """Enabled plugins with no sign of use: no Skill / skill_mention / typed command / sub-agent type carrying their prefix,
    no MCP tool named plugin_<plugin>_..., and none of their hooks (matched by the command text their hooks.json declares,
    when that text is declared by one plugin only) ran. Value = enabled and unseen."""
    used = set()
    names = {p["name"] for p in _plugins(lt).values()}
    for n, _, _ in _skill_uses(lt):
        if ":" in n and n.split(":")[0] in names:
            used.add(n.split(":")[0])
    for t, _ in _agent_launches(lt):
        if ":" in t and t.split(":")[0] in names:
            used.add(t.split(":")[0])
    for c in _calls(lt, *{c["name"] for c in lt.calls if c["name"].startswith("mcp__")}):
        pre = _mcp_prefix(c["name"]) or ""
        for nm in names:
            if pre.startswith("plugin_%s_" % _norm_mcp(nm)):
                used.add(nm)
    owners = {}
    for pl, ev, m, cmd in _plugin_hooks(lt, only_enabled=False):
        owners.setdefault(cmd, set()).add(pl)
    ran = {r["attachment"].get("command") for r in _atts(lt, "hook_success")}
    for r in _raw(lt, ('"stop_hook_summary"',)):
        for hi in r.get("hookInfos") or []:
            ran.add(hi.get("command"))
    for cmd in ran:
        if len(owners.get(cmd, ())) == 1:
            used |= owners[cmd]
    on = sorted(p["name"] for p in _plugins(lt).values() if p["enabled"])
    never = [n for n in on if n not in used]
    return {"value": len(never), "evidence": ["%d enabled, %d seen in use" % (len(on), len(on) - len(never))] + never[:2],
            "note": "plugins that only add a status line, LSP or rules show no record and read unused"}


@fixture(RETIRE, "L2", 3)
def _(fx):
    ka, pa = _plugin(fx, "alpha"); kb, pb = _plugin(fx, "beta")
    kg, pg = _plugin(fx, "gamma", hooks={"SessionStart": [{"hooks": [_hook_cmd('"${CLAUDE_PLUGIN_ROOT}"/hooks/start.sh')]}]})
    kd, pd = _plugin(fx, "delta", hooks={"SessionStart": [{"hooks": [_hook_cmd('"${CLAUDE_PLUGIN_ROOT}"/hooks/other.sh')]}]})
    ke, pe = _plugin(fx, "eps", enabled=False)
    en = _install(fx, [(ka, pa, 1), (kb, pb, 1), (kg, pg, 1), (kd, pd, 1), (ke, pe, 0)])
    fx.settings("user", {"enabledPlugins": en})
    s = _sess(fx, "~/work/shop")
    s.typed("go")
    s.call("Skill", {"skill": "alpha:go"})
    s.call("mcp__plugin_beta_srv__ping", {})
    s.attachment("hook_success", hookName="SessionStart:startup", hookEvent="SessionStart", content="", stdout="", stderr="",
                 exitCode=0, command='"${CLAUDE_PLUGIN_ROOT}"/hooks/start.sh', durationMs=5)
    s.call("Skill", {"skill": "delta"})     # trap: the person's own skill named like a plugin, no prefix
    return {"value": 1}   # delta; eps is off (not counted), gamma only ran as a hook


@check(RETIRE, "L2", 4)
def mcp_servers_never_called(lt):
    """MCP servers the laptop configures (~/.claude.json, repo .mcp.json, settings, enabled plugins as plugin_<plugin>_<server>)
    that no tool call in a session the person worked in used (mcp__<server>__ prefix). Value = configured and never called."""
    cfg = _mcp_configured(lt)
    called = {_mcp_prefix(c["name"]) for c in _calls(lt, *{c["name"] for c in lt.calls if c["name"].startswith("mcp__")})}
    never = sorted(n for n in cfg if n not in called)
    return {"value": len(never), "evidence": ["%d configured, %d called" % (len(cfg), len(cfg) - len(never))] + never[:2],
            "note": "servers added by a cloud connector or claude.ai are not in any file here; a server used only in a headless run reads unused"}


@fixture(RETIRE, "L2", 4)
def _(fx):
    fx.repo("~/work/shop", {".mcp.json": json.dumps({"mcpServers": {"docs": {"command": "x"}, "db-tools": {"command": "y"},
                                                                     "my.server": {"url": "http://z"}, "unused": {"command": "u"}}})})
    fx.file("~/.claude.json", json.dumps({"mcpServers": {"github": {"type": "http", "url": "https://g"}}}))
    k, p = _plugin(fx, "toolkit", mcp={"fetcher": {"command": "f"}})
    fx.settings("user", {"enabledPlugins": _install(fx, [(k, p, 1)])})
    s = _sess(fx, "~/work/shop")
    s.typed("look things up")
    for t in ("mcp__docs__search", "mcp__plugin_toolkit_fetcher__get", "mcp__github__issue", "mcp__my_server__ping"):
        s.call(t, {})
    h = _sess(fx, "~/work/shop", ep="sdk-cli")      # trap: a headless run's call
    h.typed("go"); h.call("mcp__unused__q", {})
    return {"value": 2}   # db-tools, unused


@check(RETIRE, "L2", 5)
def agent_types_never_launched(lt):
    """Sub-agent types listed to the person's sessions (agent_listing_delta addedTypes) never launched by an Agent / Task
    call in any interactive session. A call without a type is the general-purpose agent. Value = listed and never launched."""
    listed = set()
    for r in _atts(lt, "agent_listing_delta"):
        listed |= set(r["attachment"].get("addedTypes") or [])
    launched = {t for t, _ in _agent_launches(lt)}
    never = sorted(listed - launched)
    return {"value": len(never), "evidence": ["%d listed, %d launched" % (len(listed), len(listed & launched))] + never[:2],
            "note": "built-in types (Explore, Plan, general-purpose) are in the list; 'never' is for this window on this laptop"}


@fixture(RETIRE, "L2", 5)
def _(fx):
    s = _sess(fx, "~/work/shop")      # VS Code, not the terminal
    s.attachment("agent_listing_delta", addedTypes=["Explore", "reviewer", "deployer", "general-purpose"], addedLines=[], removedTypes=[])
    s.typed("review this")
    s.call("Agent", {"subagent_type": "reviewer", "prompt": "x"}); s.call("Agent", {"subagent_type": "reviewer", "prompt": "y"})
    s.call("Agent", {"prompt": "no type given"})
    h = _sess(fx, "~/work/shop", ep="sdk-cli")      # trap: a headless run launches the deployer
    h.typed("go"); h.call("Agent", {"subagent_type": "deployer", "prompt": "z"})
    return {"value": 2}   # Explore, deployer


# Claude Code's own screens that show what the person spends and how much they used. The screens for setting things up (/mcp,
# /plugin, /skills, /hooks, /agents, /memory, /permissions) and the ones about the window or health (/context, /status,
# /doctor) are not a look at cost and use: a day of installing a plugin must not read as a check day (skeptic, 11-05).
CHECK_COMMANDS = ("/usage", "/cost", "/stats")


@check(RETIRE, "L3", 0)
def setup_checks_on_a_cadence(lt):
    """Days on which the person typed one of Claude Code's own screens for cost and use (/usage, /cost, /stats; any interactive
    entrypoint). Value = distinct days: a cadence shows as many days, no cut-off."""
    days, n = {}, 0
    for c in lt.all_commands():
        if lt.interactive(c["s"]) and c["name"] in CHECK_COMMANDS:
            days.setdefault(_day(c["t"]), []).append(c["name"])
            n += 1
    return {"value": len(days), "evidence": ["%d commands on %d days" % (n, len(days))] + sorted({x for v in days.values() for x in v})[:2],
            "note": "the screen's output is not recorded: it shows they looked, not what they did about it"}


@fixture(RETIRE, "L3", 0)
def _(fx):
    a = _sess(fx, "~/work/shop", ep="claude-desktop", start=_noon(5)); a.typed("hi"); a.command("/usage"); a.command("/cost")
    b = _sess(fx, "~/work/shop", ep="cli", start=_noon(3)); b.typed("hi"); b.command("/stats")      # terminal: history.jsonl too
    c = _sess(fx, "~/work/shop", start=_noon(1)); c.typed("hi"); c.command("/usage")
    d = _sess(fx, "~/work/shop", start=_noon(2)); d.typed("hi"); d.command("/compact")           # not a check
    # the trap: a day of installing a plugin and setting up (screens for setup and for the window are not cost and use)
    e = _sess(fx, "~/work/shop", start=_noon(4)); e.typed("hi")
    for cmd in ("/plugin", "/mcp", "/context", "/memory", "/agents", "/permissions", "/status", "/skills", "/hooks", "/doctor"):
        e.command(cmd)
    h = _sess(fx, "~/work/shop", ep="sdk-cli", start=_noon(6)); h.typed("/usage"); h.command("/usage")   # headless
    return {"value": 3}


@check(RETIRE, "L3", 1)
def hooks_never_fired(lt):
    """Hooks configured (settings files of every scope, enabled plugins' hooks.json) on an event that leaves run records on this
    laptop (an event with at least one run record anywhere), whose exact command has no run record for that event.
    Value = configured, on a visible event, never run."""
    ran = set()
    for r in _atts(lt, "hook_success"):
        a = r["attachment"]
        ran.add((a.get("hookEvent"), a.get("command")))
    for r in _raw(lt, ('"stop_hook_summary"',)):
        for hi in r.get("hookInfos") or []:
            ran.add(("Stop", hi.get("command")))
    for r in _atts(lt, "hook_blocking_error"):
        a = r["attachment"]
        ran.add((a.get("hookEvent"), (a.get("blockingError") or {}).get("command")))
    visible = {e for e, _ in ran}
    conf = set()
    for _, _, d in _settings(lt):
        conf |= {(e, c) for e, _, c in _hooks_of(d)}
    conf |= {(e, c) for _, e, _, c in _plugin_hooks(lt)}
    unseen = sorted(x for x in conf if x[0] in visible and x not in ran)
    hidden = len({x for x in conf if x[0] not in visible})
    return {"value": len(unseen), "evidence": ["%d configured, %d never run" % (len(conf), len(unseen)), "%d on events with no run record" % hidden]
            + [("%s %s" % x)[:70] for x in unseen[:1]],
            "note": "events whose successful runs Claude Code never records can't be judged and are left out"}


@fixture(RETIRE, "L3", 1)
def _(fx):
    fx.repo("~/work/shop", {"README.md": "x"})
    fx.settings("user", {"hooks": {"PreToolUse": [{"hooks": [_hook_cmd("sh ~/.claude/hooks/guard.sh")]}],
                                   "Stop": [{"hooks": [_hook_cmd("bash a.sh"), _hook_cmd("bash b.sh")]}],
                                   "Notification": [{"hooks": [_hook_cmd("notify.sh")]}]}})
    fx.settings("project", {"hooks": {"PreToolUse": [{"hooks": [_hook_cmd("sh tools/guard.sh")]}]}}, repo="~/work/shop")
    s = _sess(fx, "~/work/shop")
    s.typed("go")
    s.attachment("hook_success", hookName="PreToolUse:Bash", hookEvent="PreToolUse", content="", stdout="", stderr="", exitCode=0,
                 command="sh ~/.claude/hooks/guard.sh", durationMs=3)
    s.system("stop_hook_summary", hookCount=1, hookInfos=[{"command": "bash a.sh", "durationMs": 4}], hookErrors=[],
             preventedContinuation=False)
    return {"value": 2}   # tools/guard.sh (same file name as the one that ran) and "bash b.sh"; Notification is not visible


# ====================================================================== compact-on-purpose
def _manual_compactions(lt):
    """[(session, time, preTokens)] manual compact_boundary records (Claude Code's own record of a compaction the person asked for) in
    sessions they worked in, oldest first."""
    def build():
        out = []
        for s in lt.systems:
            if s["subtype"] != "compact_boundary" or s["sc"] or not lt.interactive(s["s"]):
                continue
            if re.search(r'"trigger":\s*"manual"', s["data"]):
                m = re.search(r'"preTokens":\s*(\d+)', s["data"])
                out.append((s["s"], s["t"], int(m.group(1)) if m else 0))
        return sorted(out, key=lambda x: x[1])
    return _memo(lt, "manual_compactions", build)


def _says_what_to_keep(args):
    """A /compact argument is an instruction when it is more than a token or two: two or more words, or a run of at least 8 characters
    (a language with no spaces between words has none to count). No word list: what the words say is not read."""
    a = (args or "").strip()
    return len(a.split()) >= 2 or len(a) >= 8


@check(COMPACT, "L2", 2)
def compacted_by_hand(lt):
    """/compact typed WITH an argument that says what to keep (the practice's core: compact on purpose, saying what must survive), in a
    session the person worked in, and followed in that session by the manual compaction Claude Code recorded (compact_boundary,
    trigger manual; one boundary answers one command). A bare /compact, a compaction Claude Code made at the edge of the window, and a
    /compact that never completed are not it. Value = such commands; the argument's length and the tokens in the window at that
    moment (preTokens) go in the evidence, with no bar for 'full'."""
    cmds = sorted((c for c in lt.commands if c["name"] == "/compact" and lt.interactive(c["s"]) and not c["sc"]), key=lambda c: c["t"])
    bounds = {}
    for sid, t, pre in _manual_compactions(lt):
        bounds.setdefault(sid, []).append([t, pre, False])
    n_args = sum(1 for c in cmds if (c.get("args") or "").strip())
    got = []
    for i, c in enumerate(cmds):
        if not _says_what_to_keep(c.get("args")):
            continue
        later = [x["t"] for x in cmds[i + 1:] if x["s"] == c["s"]]
        until = later[0] if later else float("inf")        # the next /compact in the session ends this one's window
        for b in bounds.get(c["s"], []):
            if not b[2] and c["t"] - 1 <= b[0] < until:
                b[2] = True
                got.append((len((c.get("args") or "").strip()), b[1]))
                break
    return {"value": len(got), "evidence": ["%d /compact with an instruction, of %d /compact typed (%d with any argument)" % (len(got), len(cmds), n_args)]
            + (["instruction lengths (characters): " + ", ".join(str(a) for a, _ in got[:5]) + "; preTokens: " + ", ".join(str(p) for _, p in got[:3])] if got else []),
            "note": "the model's window size is not recorded, so how full that is can't be said; whether the instruction names what matters is not read"}


@fixture(COMPACT, "L2", 2)
def _(fx):
    keep = "keep the file list and the open decisions about auth"
    def boundary(s, trigger="manual"):
        s.system("compact_boundary", content="Conversation compacted", compactMetadata={"trigger": trigger, "preTokens": 120000, "postTokens": 9000})
    s = _sess(fx, "~/work/shop", ep="claude-desktop")
    s.typed("long work")
    s.command("/compact", keep); boundary(s)                       # counts: an instruction, then the manual compaction
    s.command("/compact"); boundary(s)                             # trap: a bare /compact
    s.command("/compact", "ok"); boundary(s)                       # trap: an argument that says nothing
    boundary(s)                                                    # trap: a manual compaction with no /compact typed (not this practice)
    boundary(s, "auto")                                            # trap: the automatic one
    s.command("/compact", keep)                                    # trap: typed, never completed (no boundary after it)
    c = _sess(fx, "~/work/shop", ep="cli"); c.typed("more")
    c.command("/compact", "preserve the schema decisions"); boundary(c)    # counts (terminal: also in history.jsonl, once)
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("go")
    h.command("/compact", keep); boundary(h)                       # trap: a headless run
    return {"value": 2}


@check(COMPACT, "L2", 3)
def checks_how_full(lt):
    """Evidence only, nothing counted. /context then /compact or /clear in one session is a looser subset of compacting with an instruction
    (compacted_by_hand): it cannot say the person compacted on purpose, and /context is also used to look at what MCP servers and skills
    take. How often /context was typed before an act is not shown as a count."""
    return {"value": None, "note": "shown beside L2#2 (compact saying what to keep); /context alone is also used to look at MCP/skill sizes"}


@fixture(COMPACT, "L2", 3)
def _(fx):
    def boundary(s, trigger="manual"):
        s.system("compact_boundary", content="Conversation compacted", compactMetadata={"trigger": trigger, "preTokens": 120000, "postTokens": 9000})
    a = _sess(fx, "~/work/shop", ep="claude-desktop"); a.typed("hi"); a.command("/context"); a.command("/compact", "keep the plan"); boundary(a)   # used to count: nothing counts now
    b = _sess(fx, "~/work/shop", ep="cli"); b.typed("hi"); b.command("/context"); b.command("/clear")                                             # used to count: looked, then reset
    return {"value": None}


@check(COMPACT, "L3", 1)
def reload_hook_on_compact(lt):
    """SessionStart hooks the person put in their own settings with a matcher that fires on 'compact' (a matcher that names
    it; one with no matcher is not a choice about compaction). Value = count; times they ran are in the evidence."""
    n, ev = 0, []
    for sc, p, d in _own_settings(lt):
        for e, m, cmd in _hooks_of(d):
            if e != "SessionStart" or not m:
                continue
            try:
                hit = re.fullmatch(m, "compact") is not None
            except re.error:
                hit = False
            if hit:
                n += 1
                ev.append("%s: %s" % (sc, cmd[:50]))
    ran = len({r["_sid"] for r in _atts(lt, "hook_success") if r["attachment"].get("hookName") == "SessionStart:compact"})
    return {"value": n, "evidence": ev[:2] + ["SessionStart:compact ran in %d sessions" % ran],
            "note": "what the hook reloads is not read; plugins' compact hooks are not the person's choice and are left out"}


@fixture(COMPACT, "L3", 1)
def _(fx):
    fx.repo("~/work/shop", {"README.md": "x"}, by="work")
    fx.repo("~/work/team", {"README.md": "y"}, by="teammate")
    fx.settings("user", {"hooks": {"SessionStart": [{"matcher": "compact", "hooks": [_hook_cmd("cat ~/.claude/rules.md")]},
                                                    {"matcher": "startup", "hooks": [_hook_cmd("echo hi")]},
                                                    {"hooks": [_hook_cmd("echo any")]}]}})
    fx.commit("~/work/shop", {".claude/settings.json": json.dumps({"hooks": {"SessionStart": [{"matcher": "startup|compact", "hooks": [_hook_cmd("./reload.sh")]}]}})}, by="work")
    fx.commit("~/work/team", {".claude/settings.json": json.dumps({"hooks": {"SessionStart": [{"matcher": "compact", "hooks": [_hook_cmd("./team.sh")]}]}})}, by="teammate")
    k, p = _plugin(fx, "acme", hooks={"SessionStart": [{"matcher": "compact", "hooks": [_hook_cmd("plug.sh")]}]})
    fx.settings("user-local", {"enabledPlugins": _install(fx, [(k, p, 1)])})
    _sess(fx, "~/work/shop").typed("go"); _sess(fx, "~/work/team").typed("go")
    return {"value": 2}   # their user hook and their own committed project hook; not the teammate's, not the plugin's


@check(COMPACT, "L4", 0)
def pre_post_compact_hooks(lt):
    return {"value": None, "note": "can't tell from a laptop: what a PreCompact / PostCompact hook does (saves state, reloads it, or only beeps) is in "
            "its script, and Claude Code records a hook that saved state the same as one that played a sound; a managed setting is the "
            "employer's, never the person's choice (skeptic, 11-05)"}


@fixture(COMPACT, "L4", 0)
def _(fx):
    fx.repo("~/work/shop", {"README.md": "x"}, by="work")
    fx.settings("user", {"hooks": {"PreCompact": [{"hooks": [_hook_cmd("afplay /System/Library/Sounds/Ping.aiff")]}]}})   # a beep reads as saving
    fx.settings("managed", {"hooks": {"PreCompact": [{"hooks": [_hook_cmd("org.sh")]}]}})
    _sess(fx, "~/work/shop").typed("go")
    return {"value": None}


@check(COMPACT, "L4", 1)
def autocompact_threshold(lt):
    """Settings files of the person (user, project, local, in every folder their sessions ran in) that set a
    CLAUDE_AUTOCOMPACT_* variable in env. Value = count of such settings files."""
    hits = []
    for sc, p, d in _own_settings(lt):
        env = d.get("env") or {}
        ks = [k for k in env if isinstance(k, str) and k.startswith("CLAUDE_AUTOCOMPACT")] if isinstance(env, dict) else []
        if ks:
            hits.append("%s: %s" % (sc, ",".join(ks)))
    return {"value": len(hits), "evidence": hits[:3], "note": "a variable exported in a shell profile or launcher is not in any settings file"}


@fixture(COMPACT, "L4", 1)
def _(fx):
    fx.repo("~/code/api", {"README.md": "x"}, by="home")
    fx.repo("~/work/team", {"README.md": "y"}, by="teammate")
    fx.settings("user", {"env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "60", "OTHER": "1"}})
    fx.settings("project-local", {"env": {"CLAUDE_AUTOCOMPACT_WINDOW": "200000"}}, repo="~/code/api")
    fx.commit("~/work/team", {".claude/settings.json": json.dumps({"env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "80"}})}, by="teammate")
    _sess(fx, "~/code/api").typed("go"); _sess(fx, "~/work/team").typed("go")
    return {"value": 2}   # not the teammate's committed file


# ====================================================================== bound-the-task + let-the-agent-fetch: a place and a finish line in one message
# a place in a typed message, read from the text's structure: an @-mention of a file or folder (Claude Code's own syntax for attaching one),
# or a path (dir/file.ext, ./x, ~/x, /abs/x). A URL, an email address and "and/or" are not one.
AT_PLACE = re.compile(r"(?<![\w.@])@[\w~.@/-]*(?:/|\.\w{1,6}\b)")
PATH_PLACE = re.compile(r"(?<![\w:/@.])(?:(?:~|\.{1,2})/[\w./@-]*\w|/?(?:[\w.@-]+/)+[\w@-]+\.\w{1,6}\b)")
# a finish line the person states: only the markers below, plus a command their own repo declares as a check (lt.is_declared_check) typed
# inside backticks or right after "run"
# a check word followed within three words (same sentence) by pass / green / succeed, or "done when". Not a bare "until", and not "make it pass X":
# those say when something stopped, or hand a value on, and name no check.
FINISH_MARK = re.compile(r"(?i)\bdone when\b|\b(?:tests?|build|lint|ci|checks?|typecheck)\b`?(?:[ \t]+[\w'-]+){0,3}[ \t]+(?:pass(?:es|ed|ing)?|green|succeed(?:s|ed)?)\b")


def _names_a_place(text):
    return bool(AT_PLACE.search(text) or PATH_PLACE.search(text))


def _names_a_finish_line(lt, text, repo):
    if FINISH_MARK.search(text):
        return True
    if not repo:
        return False
    # a declared check typed in the message: inside backticks, or right after "run" ("run npm test"). Not a tool's name met in a sentence
    # ("the mypy config in tests/x.py is wrong").
    pieces = re.findall(r"`([^`\n]+)`", text)
    pieces += re.findall(r"(?i)\brun(?:ning)?[ \t]+([^\n]+)", text)
    return any(lt.is_declared_check(x, repo) for x in pieces)


def _bounded_asks(lt):
    """[(session, text)] typed messages (interactive sessions, never a sub-agent's or a headless prompt) that carry a place AND a finish line."""
    def build():
        out = []
        for m in lt.typed:
            if m["sc"] or not lt.interactive(m["s"]) or m["text"].startswith("<bash-input>"):
                continue
            if not _names_a_place(m["text"]):
                continue
            repo = lt.repo_of((lt.sessions.get(m["s"]) or {}).get("cwd"))
            if _names_a_finish_line(lt, m["text"], repo):
                out.append((m["s"], m["text"]))
        return out
    return _memo(lt, "bounded_asks", build)


@check(BOUND, "L2", 1)
def asks_with_place_and_finish_line(lt):
    """Typed messages that carry a place (an @-mention of a file or a path) AND an explicit finish line (a command their own repo declares as
    a check typed in backticks or right after "run", 'done when', or a check word (tests, build, lint, CI...) with pass / green / succeed
    a few words after it). A bare 'until' and 'make it pass X' are not one. Naming a file alone is pasting, not bounding the task (nearly everyone @-mentions
    files); this counts the two parts together, in one message the person typed. Value = such messages."""
    got = _bounded_asks(lt)
    typed = sum(1 for m in lt.typed if not m["sc"] and lt.interactive(m["s"]))
    return {"value": len(got), "evidence": ["%d of %d typed messages carry a place and a finish line (in %d sessions)" % (len(got), typed, len({s for s, _ in got}))],
            "note": "the finish-line markers are a few English phrases plus the commands their repo declares as checks, typed in backticks or after 'run', so a message that states done in another "
                    "language or in other words is missed; what to leave alone, and whether the finish line is the right one, are not read"}


@check(FETCH, "L2", 1)
def _fetch_by_pointing(lt):
    """Display only. An @-mention puts the file into the session; it is not the agent fetching, so nothing is counted."""
    return {"value": None, "note": "an @-mention puts the file into the session; it is not the agent fetching"}


@fixture(BOUND, "L2", 1)
def _(fx):
    fx.repo("~/work/shop", {"package.json": json.dumps({"scripts": {"test": "jest"}}), "pyproject.toml": "[tool.mypy]\n", "src/a.ts": "x"}, by="work")
    s = _sess(fx, "~/work/shop")
    s.typed("fix the flaky retry in @src/a.ts so that npm test passes")                   # counts: a place + 'so that ... passes'
    s.typed("refactor src/util.ts, then run `npm test`")                                  # counts: a path + a command the repo declares as a check
    s.typed("rename the helper in @src/b.ts and keep going until the build is green")      # counts: an @-mention + 'build ... green'
    s.typed("look at @src/a.ts and @src/b.ts")                                            # trap: places, no finish line (the old count)
    s.typed("make it faster, done when it feels right")                                   # trap: a finish line, no place
    s.typed("write to me@example.com until Friday, see https://example.com/docs/page.html")   # trap: an email and a URL are not places
    s.typed("this worked until I touched src/a.ts")                                       # trap: a bare 'until' (used to count)
    s.typed("make it pass the token to src/api.ts")                                       # trap: 'make ... pass' with no check word (used to count)
    s.typed("the mypy config in tests/x.py is wrong")                                     # trap: a declared tool's name met in a sentence (used to count)
    s.typed("keep going until the retry loop in @src/a.ts stops")                         # trap: 'until' again
    s.typed("what does and/or mean in @rob's comment?")                                  # trap: not a path, and an @-name
    s.attachment("file", filename=fx.p("~/work/shop/src/a.ts"), content={"type": "text"})   # trap: an attachment record alone
    d = _sess(fx, "~/work/shop", ep="claude-desktop")
    d.typed("update the docs in docs/README.md, done when the lint passes")                    # counts: another entrypoint
    d.typed("update the docs in docs/README.md until the links resolve")                      # trap: 'until' (used to count)
    h = _sess(fx, "~/work/shop", ep="sdk-cli")
    h.typed("fix @src/a.ts so that npm test passes")                                     # trap: a headless run's prompt
    return {"value": 4}


@fixture(FETCH, "L2", 1)
def _(fx):
    fx.repo("~/work/shop", {"src/a.ts": "x"}, by="work")
    s = _sess(fx, "~/work/shop")
    s.typed("look at @src/a.ts")
    s.attachment("file", filename=fx.p("~/work/shop/src/a.ts"), content={"type": "text"})
    return {"value": None}


# ====================================================================== bound-the-task
@check(BOUND, "L3", 0)
def own_template_material(lt):
    """Skill, agent and command files the person wrote (under .claude/ or in a plugin, or in ~/.claude), theirs by the one ownership rule
    (lt.owner: committed or not; not installed, not most commits someone else's), leaving out a file identical to one an installed
    plugin ships. Value = count of files."""
    arts = _own_artifacts(lt)
    kinds = {k: sum(1 for a in arts if a["kind"] == k) for k in ("skill", "agent", "command")}
    return {"value": len(arts), "evidence": ["%d %ss" % (v, k) for k, v in kinds.items()],
            "note": "a file the person vendored and edited a little counts as theirs; whether the file is a good template is not read"}


def _lock_global(fx, *names):
    """The receipt a skills installer leaves: ~/.agents/.skill-lock.json lists each skill (its files sit in ~/.agents/skills/<name>, linked
    into ~/.claude/skills, or in ~/.claude/skills itself). Whatever it names is installed, not theirs (ruling 11-05)."""
    fx.file("~/.agents/.skill-lock.json", json.dumps({"version": 3, "skills": {n: {"source": "someone/skills"} for n in names}}))


@fixture(BOUND, "L3", 0)
def _(fx):
    # a skill in a git repo of their own that sits in ~/.claude (dotfiles) is theirs: by its commits, not its place
    fx.repo("~/.claude", {"skills/dot/SKILL.md": "---\nname: dot\n---\nmine"}, by="work")
    fx.repo("~/work/shop", {".claude/skills/deploy/SKILL.md": "# deploy\nship it", ".claude/agents/rev.md": "---\nname: rev\n---\nreview",
                            ".claude/commands/ship.md": "ship", "docs/agents/overview.md": "# not a setup file"}, by="work")
    vend = "---\nname: vend\n---\nvendored skill text"
    k, p = _plugin(fx, "vendor")
    fx.file(os.path.join(p, "skills", "vend", "SKILL.md"), vend)
    fx.file("~/.claude/settings.json", json.dumps({"enabledPlugins": _install(fx, [(k, p, 1)])}))
    fx.commit("~/work/shop", {".claude/skills/vend/SKILL.md": vend}, by="work")           # trap: a copy of an installed skill
    fx.commit("~/work/shop", {".claude/skills/locked/SKILL.md": "---\nname: locked\n---\ninstalled by a skills CLI",
                              "skills-lock.json": json.dumps({"version": 1, "skills": {"locked": {"source": "someone/skills"}}})}, by="work")   # trap: a lock file names it
    fx.repo("~/work/team", {".claude/skills/team/SKILL.md": "team"}, by="teammate")       # trap: a teammate's
    # ~/.claude holds installed things too: told apart by a receipt, never by the place or by git not knowing the file
    fx.file("~/.claude/agents/mine.md", "my own agent")                                    # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.file("~/.claude/skills/vendor-skill-b/SKILL.md", "---\nname: vendor-skill-b\n---\nan installed skill")   # trap: named in the skills lock
    fx.file("~/.agents/skills/linked/SKILL.md", "---\nname: linked\n---\nlinked in")
    os.symlink(fx.p("~/.agents/skills/linked"), fx.p("~/.claude/skills/linked"))          # trap: ~/.agents/skills/linked, in the skills lock, linked into ~/.claude/skills
    _lock_global(fx, "vendor-skill-b", "linked")
    _sess(fx, "~/work/shop").typed("go"); _sess(fx, "~/work/team").typed("go")
    return {"value": 5}   # deploy, dot, rev, mine, ship


@check(BOUND, "L3", 2)
def own_skills_called(lt):
    """Uses of the person's own skills: Skill tool calls, skill_mention attachments and typed /name commands whose name has no
    plugin prefix and matches a skill file they wrote. A plugin:name is an installed skill, whatever the bare name."""
    own = {a["name"] for a in _own_artifacts(lt) if a["kind"] == "skill"}
    hits = [(n, how) for n, how, _ in _skill_uses(lt) if ":" not in n and n in own]
    return {"value": len(hits), "evidence": ["%s via %s" % h for h in hits[:3]],
            "note": "a bare name that a user-level skill and a project skill share can't be told apart"}


@fixture(BOUND, "L3", 2)
def _(fx):
    fx.repo("~/work/shop", {".claude/skills/deploy/SKILL.md": "# deploy mine"}, by="work")
    vend = "---\nname: vendor-skill\n---\ntext"
    k, p = _plugin(fx, "design")
    fx.file(os.path.join(p, "skills", "vendor-skill", "SKILL.md"), vend)
    fx.settings("user", {"enabledPlugins": _install(fx, [(k, p, 1)])})
    fx.commit("~/work/shop", {".claude/skills/vendor-skill/SKILL.md": vend}, by="work")
    s = _sess(fx, "~/work/shop")
    s.typed("ship it")
    s.call("Skill", {"skill": "deploy"})                  # counts
    s.attachment("skill_mention", skillName="deploy")     # counts
    s.command("/deploy")                                  # counts
    s.call("Skill", {"skill": "acme:deploy"})             # trap: a plugin's skill with the same bare name
    s.call("Skill", {"skill": "vendor-skill"})              # trap: a vendored copy
    fx.file("~/.claude/skills/vendor-skill-b/SKILL.md", "---\nname: vendor-skill-b\n---\nan installed skill")
    _lock_global(fx, "vendor-skill-b")
    s.call("Skill", {"skill": "vendor-skill-b"}); s.command("/vendor-skill-b")      # trap: an installed skill in ~/.claude, named in the skills lock
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("go"); h.call("Skill", {"skill": "deploy"})
    return {"value": 3}


_SCRIPT_TOKEN = re.compile(r"(?<![\w./~$-])((?:\$\{?CLAUDE_PROJECT_DIR\}?|~|\.{1,2})?/?(?:[\w.@-]+/)*[\w.@-]+\.(?:sh|bash|zsh|py|js|mjs|cjs|ts|rb))(?![\w/-])")


def _runs_declared_check(lt, cmd, settings_path):
    """A hook command is a check of the work when it runs a command a repo declares as a check (package.json test script, make test,
    pytest...; lt.declared_checks), either itself or inside a script file it names (read up to 200 KB). A hook that only plays a sound,
    sets a tab title or prints a reminder runs none."""
    repos = lt.repos
    def hit(text, near=None):
        pool = [near] if near else repos
        return any(lt.is_declared_check(text, r) for r in pool)
    home_repo = lt.repo_of(os.path.dirname(os.path.dirname(settings_path))) if os.sep + ".claude" + os.sep in settings_path else None
    if hit(cmd, home_repo):
        return True
    for m in _SCRIPT_TOKEN.finditer(cmd):
        tok = m.group(1).replace("${CLAUDE_PROJECT_DIR}", "$CLAUDE_PROJECT_DIR")
        if tok.startswith("$CLAUDE_PROJECT_DIR"):
            tok = (home_repo or "") + tok[len("$CLAUDE_PROJECT_DIR"):]
        elif tok.startswith("~"):
            tok = lt.home + tok[1:]
        elif not os.path.isabs(tok):
            tok = os.path.join(home_repo or os.path.dirname(settings_path), tok)
        try:
            if not os.path.isfile(tok):
                continue
            with open(tok, errors="replace") as fh:
                text = fh.read(200000)
        except OSError:
            continue
        if hit(text, lt.repo_of(tok) or home_repo):
            return True
    return False


def _done_gates(lt):
    """[(scope, command)] Stop hooks in the person's own settings files that run a check of the work (_runs_declared_check). A
    notification, a tab title, a reminder, an installer's hook or a plugin's hooks.json is not one."""
    def build():
        out = []
        for sc, p, d in _own_settings(lt):
            for e, m, cmd in _hooks_of(d):
                if e == "Stop" and cmd and _runs_declared_check(lt, cmd, p):
                    out.append((sc, cmd))
        return out
    return _memo(lt, "done_gates", build)


@check(BOUND, "L4", 0)
def own_stop_hook(lt):
    """Stop hooks in the person's own settings files (user, project, local; a teammate's committed file, a plugin's hooks.json and
    managed settings are not theirs) whose command runs a check their repos declare (itself, or in the script it names). Value = count
    of such Stop hooks. A Stop hook that notifies, sets a tab title or reminds is not a limit on the task."""
    hs = _done_gates(lt)
    return {"value": len(hs), "evidence": ["%s: %s" % (sc, c[:50]) for sc, c in hs[:3]],
            "note": "a gate that checks the work by a command no repo declares as a check, or by a script that calls it indirectly, isn't seen"}


@fixture(BOUND, "L4", 0)
def _(fx):
    fx.repo("~/work/shop", {"package.json": json.dumps({"scripts": {"test": "jest"}}), "check.sh": "npm test\n"}, by="work")
    fx.repo("~/work/team", {"package.json": json.dumps({"scripts": {"test": "vitest"}})}, by="teammate")
    fx.file("~/.claude/hooks/gate.sh", "#!/bin/sh\nnpm test || exit 2\n")
    fx.file("~/.claude/hooks/tabtitle.sh", "#!/bin/sh\nprintf '\\033]0;done\\007'\n")
    fx.file("~/.claude/installer/check.js", "console.log('installed check of the design')\n")
    fx.settings("user", {"hooks": {"Stop": [{"hooks": [_hook_cmd("npm test"),                                          # counts: runs a declared check
                                                       _hook_cmd("sh ~/.claude/hooks/gate.sh"),                    # counts: its script runs one
                                                       _hook_cmd("afplay /System/Library/Sounds/Glass.aiff"),      # traps: a sound, a tab title, an installer's check
                                                       _hook_cmd("sh ~/.claude/hooks/tabtitle.sh"),
                                                       _hook_cmd("node ~/.claude/installer/check.js")]}],
                                   "PreToolUse": [{"hooks": [_hook_cmd("npm test")]}]}})                           # not a Stop hook
    fx.commit("~/work/shop", {".claude/settings.json": json.dumps({"hooks": {"Stop": [{"hooks": [_hook_cmd("sh check.sh")]}]}})}, by="work")   # counts: theirs, its script
    fx.commit("~/work/team", {".claude/settings.json": json.dumps({"hooks": {"Stop": [{"hooks": [_hook_cmd("npm test")]}]}})}, by="teammate")   # trap: a teammate's file
    k, p = _plugin(fx, "acme", hooks={"Stop": [{"hooks": [_hook_cmd("npm test")]}]})                               # trap: a plugin's hook
    fx.settings("user-local", {"enabledPlugins": _install(fx, [(k, p, 1)])})
    fx.settings("managed", {"hooks": {"Stop": [{"hooks": [_hook_cmd("npm test")]}]}})                              # trap: the employer's
    _sess(fx, "~/work/shop").typed("go"); _sess(fx, "~/work/team").typed("go")
    return {"value": 3}


@check(BOUND, "L4", 2)
def own_gate_fired(lt):
    """Times a Stop hook of the person's that runs a check of the work (own_stop_hook's set, exact command text) stopped Claude from
    finishing: a hook_blocking_error on Stop, or a stop_hook_summary with preventedContinuation true."""
    own = {c for _, c in _done_gates(lt)}
    n, ev = 0, []
    for r in _atts(lt, "hook_blocking_error"):
        a = r["attachment"]
        if a.get("hookEvent") == "Stop" and (a.get("blockingError") or {}).get("command") in own:
            n += 1
            ev.append("blocked: " + str((a.get("blockingError") or {}).get("command"))[:40])
    for r in _raw(lt, ('"stop_hook_summary"',)):
        if r.get("subtype") == "stop_hook_summary" and not r["_sc"] and lt.interactive(r["_sid"]) and r.get("preventedContinuation") \
                and any(h.get("command") in own for h in r.get("hookInfos") or []):
            n += 1
            ev.append("prevented continuation")
    return {"value": n, "evidence": ev[:3], "note": "a hook that blocks by printing a reason shows as a block only if Claude Code recorded the block"}


@fixture(BOUND, "L4", 2)
def _(fx):
    fx.repo("~/work/shop", {"package.json": json.dumps({"scripts": {"test": "jest"}})}, by="work")
    fx.settings("user", {"hooks": {"Stop": [{"hooks": [_hook_cmd("npm test"), _hook_cmd("sh ~/.claude/hooks/remind.sh")]}]}})
    fx.file("~/.claude/hooks/remind.sh", "echo 'Before you stop: did this turn produce a rule worth keeping?'\n")
    _sess(fx, "~/work/shop").typed("hi")
    s = _sess(fx, "~/work/shop")
    s.typed("finish")
    for _ in range(2):
        s.attachment("hook_blocking_error", hookName="Stop", hookEvent="Stop", blockingError={"blockingError": "tests failing", "command": "npm test"})
    s.system("stop_hook_summary", hookCount=1, hookInfos=[{"command": "npm test", "durationMs": 9}], hookErrors=[], preventedContinuation=True)
    s.system("stop_hook_summary", hookCount=1, hookInfos=[{"command": "npm test", "durationMs": 9}], hookErrors=[], preventedContinuation=False)
    # traps: a memory reminder that blocks the stop is not a check; a plugin's block
    s.attachment("hook_blocking_error", hookName="Stop", hookEvent="Stop", blockingError={"blockingError": "Before you stop: did this turn produce a rule", "command": "sh ~/.claude/hooks/remind.sh"})
    s.attachment("hook_blocking_error", hookName="Stop", hookEvent="Stop", blockingError={"blockingError": "plugin says no", "command": "plug.sh"})
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("go")
    h.attachment("hook_blocking_error", hookName="Stop", hookEvent="Stop", blockingError={"blockingError": "x", "command": "npm test"})
    return {"value": 3}


# ====================================================================== isolate-side-work
FORK_BRIEF_MAX = 400     # characters of the directive a fork was given; a fork given the person's whole long message is not a short brief
DIRECTIVE = "Your directive:"      # where Claude Code's own fork template puts what the fork was told


def _fork_brief(meta_file):
    """The directive a fork was given, as Claude Code wrote it into the fork's own log (the first user record, after the fork template's
    'Your directive:'), or None when the log or the marker isn't there."""
    log = meta_file[:-len(".meta.json")] + ".jsonl"
    try:
        with open(log, errors="replace") as fh:
            for _ in range(6):
                line = fh.readline()
                if not line:
                    break
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("type") == "user":
                    c = (r.get("message") or {}).get("content")
                    text = c if isinstance(c, str) else " ".join(b.get("text", "") for b in c or [] if isinstance(b, dict))
                    i = text.find(DIRECTIVE)
                    return text[i + len(DIRECTIVE):].strip() if i >= 0 else None
    except OSError:
        return None
    return None


def _returned_forks(lt):
    """{agent id: session} of background agents whose result Claude Code delivered into the session that started them: the queue-operation
    record it writes when a task-notification arrives (status completed, naming the agent's id, the id in its meta file's name). Read from
    the index's short copy of that record, so no log is searched again; a notification whose status falls past the copy's end is not counted."""
    def build():
        out = {}
        for o in lt.others:
            if o["type"] != "queue-operation" or o["sc"] or "<task-notification>" not in o["data"] or "<status>completed</status>" not in o["data"]:
                continue
            m = re.search(r"<task-id>([^<]+)</task-id>", o["data"])
            if m:
                out[m.group(1)] = o["s"]
        return out
    return _memo(lt, "returned_forks", build)


@check(ISOLATE, "L2", 0)
def forked_by_hand(lt):
    """Forks the person started with a short brief whose result came back into the main session. A fork the person started: a meta file with
    isFork true and no toolUseId (Claude's own calls carry one), no teamName and no workflowPhase, in a session they worked in (any
    interactive entrypoint). A short brief: the directive in the fork's own log is at most 400 characters (a fork handed the whole of a long
    message, as when a correction is forked, is not one). Result came back: Claude Code queued a completed task-notification for that fork
    in the session that started it. Value = such forks."""
    mine = [m for m in lt.meta if m.get("isFork") and not m.get("toolUseId") and not m.get("teamName") and not m.get("workflowPhase")
            and lt.interactive(m["_session"])]
    back = _returned_forks(lt)
    got, long_, open_, unread = [], 0, 0, 0
    for m in mine:
        aid = os.path.basename(m["_file"])[len("agent-"):-len(".meta.json")]
        if back.get(aid) != m["_session"]:
            open_ += 1
            continue
        brief = _fork_brief(m["_file"])
        if brief is None:
            unread += 1
        elif len(brief) > FORK_BRIEF_MAX:
            long_ += 1
        else:
            got.append((m, len(brief)))
    return {"value": len(got), "evidence": ["%d of %d forks started by hand: short brief and result back; %d result not delivered, %d brief over %d characters, %d brief not readable"
                                         % (len(got), len(mine), open_, long_, FORK_BRIEF_MAX, unread)],
            "note": "older Claude Code versions may not write isFork; the 400-character cut is fixed, so a short fork of the person's whole short message is not told apart "
                    "from a brief written for the fork"}


@fixture(ISOLATE, "L2", 0)
def _(fx):
    def fork(s, brief, return_=True, **kw):
        aid = s.subagent("fork", brief[:40], by_claude=False, is_fork=True, **kw)
        d = os.path.join(os.path.dirname(s.path), s.sid, "subagents")
        with open(os.path.join(d, "agent-%s.jsonl" % aid), "w") as f:
            f.write(json.dumps({"type": "fork-context-ref", "agentId": aid, "parentSessionId": s.sid}) + "\n")
            f.write(json.dumps({"type": "user", "isSidechain": True, "message": {"role": "user", "content": [{"type": "text", "text":
                    "<fork-boilerplate>\nYou are a worker fork.\n</fork-boilerplate>\n\nYour directive: " + brief}]}}) + "\n")
        if return_:
            s.record("queue-operation", operation="enqueue", content="<task-notification>\n<task-id>%s</task-id>\n<status>completed</status>\n"
                                      "<result>done</result>\n</task-notification>" % aid)
        return aid
    s = _sess(fx, "~/work/shop")        # VS Code
    s.typed("side question")
    fork(s, "check the auth flow and tell me where tokens are refreshed")                      # counts
    d = _sess(fx, "~/work/shop", ep="claude-desktop"); d.typed("hm")
    fork(d, "list the callers of parseConfig")                                                  # counts: another entrypoint
    fork(s, "Ok something is wrong here. " + "I keep seeing the same failure and I want it traced. " * 12)   # trap: the whole of a long message
    fork(s, "short brief but the result never came back", return_=False)                        # trap: no result delivered
    s.subagent("fork", "claude asked", by_claude=True, is_fork=True)                            # trap: Claude's call carries toolUseId
    fork(s, "a teammate's", team="squad")                                                       # trap: a team member
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("go"); fork(h, "headless")             # trap: a headless run
    return {"value": 2}


@check("lead-a-team-of-agents", "L2", 1)   # a dynamic workflow is multi-agent orchestration (the ruling 11: L2), re-homed 11-06 from isolate-side-work
def workflow_runs(lt):
    """Workflow runs the person asked for: a workflow_keyword_request attachment (Claude Code's record of the workflow keyword in
    their message) in a session they worked in, followed in the same turn (before their next message) by a Workflow tool call. Value =
    such requests. A Workflow call Claude chose on its own, or one in a sub-agent's log, is not the person's act and is only counted
    in the evidence."""
    typed_t = {}
    for m in lt.typed:
        typed_t.setdefault(m["s"], []).append(m["t"])
    runs = {}
    for c in _calls(lt, "Workflow"):
        if not c["sc"]:
            runs.setdefault(c["s"], []).append(c["t"])
    asked = 0
    for a in lt.attachments:
        if a["type"] != "workflow_keyword_request" or a["sc"] or not lt.interactive(a["s"]):
            continue
        nxt = min((t for t in typed_t.get(a["s"], []) if t > a["t"]), default=float("inf"))
        asked += any(a["t"] <= t < nxt for t in runs.get(a["s"], []))
    total = len(_calls(lt, "Workflow"))
    return {"value": asked, "evidence": ["%d runs asked for with the keyword" % asked, "%d Workflow calls in all" % total],
            "note": "a workflow started some other way the person asked for (a slash command that runs one) isn't seen; how many sub-agents a run used isn't read"}


@fixture("lead-a-team-of-agents", "L2", 1)
def _(fx):
    s = _sess(fx, "~/work/shop")
    s.typed("ultracode refactor this")
    s.attachment("workflow_keyword_request")
    for i in range(3):
        s.call("Workflow", {"script": "wf%d" % i})          # one request, one turn: counted once
    s.typed("ultracode and then the tests")
    s.attachment("workflow_keyword_request")
    s.call("Workflow", {"script": "wf3"})
    s.typed("what did that do?")
    s.attachment("workflow_keyword_request")                  # trap: a keyword the person typed but no run followed in its turn
    s.call("Task", {"prompt": "not a workflow"})
    c = _sess(fx, "~/work/shop", ep="claude-desktop"); c.typed("tidy the repo")
    c.call("Workflow", {"script": "chosen-by-claude"})          # trap: Claude chose it, nobody asked
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("go"); h.attachment("workflow_keyword_request"); h.call("Workflow", {"script": "x"})
    return {"value": 2}


@check(ISOLATE, "L3", 0)
def own_agents_launched(lt):
    """Launches (Agent / Task calls with a subagent_type) of an agent the person wrote: the type has no plugin prefix and
    matches an agent file in a repo or ~/.claude whose commits are mostly theirs (_own_artifacts). Value = launches."""
    own = {a["name"] for a in _own_artifacts(lt) if a["kind"] == "agent"}
    hits = [t for t, c in _agent_launches(lt) if _str(c, "subagent_type") and ":" not in t and t in own]
    return {"value": len(hits), "evidence": sorted(set(hits))[:3], "note": "launches by a sub-agent's own calls are counted like the main agent's"}


@fixture(ISOLATE, "L3", 0)
def _(fx):
    fx.repo("~/work/shop", {".claude/agents/reviewer.md": "---\nname: reviewer\n---\nreview"}, by="work")
    fx.repo("~/work/team", {".claude/agents/qa.md": "---\nname: qa\n---\nqa"}, by="teammate")
    fx.file("~/.claude/agents/installed-documenter.md", "---\nname: installed-documenter\n---\nno install receipt")     # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.file("~/.claude/agents/Explore.md", "---\nname: Explore\n---\nan override of the built-in")                            # trap: named after a built-in type, never a built thing
    s = _sess(fx, "~/work/shop")
    s.typed("go")
    s.call("Agent", {"subagent_type": "reviewer", "prompt": "a"}); s.call("Agent", {"subagent_type": "reviewer", "prompt": "b"})
    s.call("Agent", {"subagent_type": "qa", "prompt": "c"})                       # trap: a teammate's agent
    s.call("Agent", {"subagent_type": "acme:reviewer", "prompt": "d"})            # trap: a plugin's agent of the same stem
    s.call("Agent", {"subagent_type": "installed-documenter", "prompt": "e"})    # counts: their untracked agent file
    s.call("Agent", {"subagent_type": "Explore", "prompt": "f"})                  # trap: launches of a built-in type are the built-in's
    s.call("Agent", {"prompt": "no type"})
    _sess(fx, "~/work/team").typed("go")
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("go"); h.call("Agent", {"subagent_type": "reviewer", "prompt": "e"})
    return {"value": 3}   # the Explore override is a built-in's, not theirs (11-05 night)


@check(ISOLATE, "L3", 1)
def exploration_handed_by_rule(lt):
    return {"value": None, "note": "can't tell from a laptop: that a rule file hands exploration to a sub-agent is in its prose. Matching an "
            "agent's type name in the rule text also matches ordinary words (fork, Plan, Explore, interface) and says nothing about "
            "exploring (skeptic, 11-05); the launches that follow are the agent's own choice as much as the rule's"}


@fixture(ISOLATE, "L3", 1)
def _(fx):
    fx.repo("~/work/shop", {"CLAUDE.md": "# Regras\nDelegue a exploracao do codigo ao agente `scout`; use a fork interface for reviews.\n"}, by="work")
    s = _sess(fx, "~/work/shop")
    s.typed("go")
    for i in range(3):
        s.call("Agent", {"subagent_type": "scout", "prompt": "find %d" % i})
    s.call("Agent", {"subagent_type": "fork", "prompt": "an ordinary word in a rule"})
    return {"value": None}


# ====================================================================== write-a-program-that-runs-the-agent
@check(PROGRAM, "L2", 0)
def claude_p_by_hand(lt):
    return {"value": None, "note": "can't tell from a laptop: a headless run (sdk-cli) is recorded the same whether a person typed "
            "claude -p at a shell, a script started it or a plugin hook did; the record has no starter"}


@fixture(PROGRAM, "L2", 0)
def _(fx):
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("summarise this"); h.call("Read", {"file_path": fx.p("~/work/shop/a.md")})
    return {"value": None}


INTERPRETER = re.compile(r"^(?:(?:ba|z|da|k)?sh|python[\d.]*|uvx?|node|bun|deno|tsx|npx|ts-node|ruby|perl|php|pwsh|poetry|env|time|nohup|sudo)$")


def _bash_commands(lt):
    """[command text] of every Bash call in a session the person worked in (the call's input is cut at 1,500 characters)."""
    return _memo(lt, "bash_cmds", lambda: [x for x in (_str(c, "command") for c in _calls(lt, "Bash")) if x])


def _script_runs(lt, rel):
    """How many Bash calls ran the script `rel` (a path in a repo): the script's path or file name is the command, or the argument of an
    interpreter (python, bash, node, uv run...). Reading it (cat, git add, rg) is not running it."""
    base_name = rel.rpartition("/")[2]
    n = 0
    # the commands that name this file, listed once per file name (many repos hold a Makefile or a package.json)
    for cmd in _memo(lt, ("cmds_naming", base_name), lambda: [c for c in _bash_commands(lt) if base_name in c]):
        for seg in re.split(r"&&|\|\||[;|\n]", cmd):
            toks = [t.strip("'\"") for t in seg.split()]
            for i, t in enumerate(toks):
                if t == rel or t == base_name or t.endswith("/" + base_name):
                    if all(INTERPRETER.match(x.rpartition("/")[2]) or x.startswith("-") or x == "run" or "=" in x for x in toks[:i]):
                        n += 1
                    break
    return n


@check(PROGRAM, "L3", 0)
def rerun_agent_scripts(lt):
    """Source files that are the person's by the one ownership rule (_own_scripts) and start the agent as code (a `claude -p` command line,
    one in a string or list in a program, or an Agent SDK import; a comment, a docstring or a mention in a string is not one) and that
    were run in two or more Bash calls of sessions they worked in. Value = such scripts."""
    hits = [(r, rel, _script_runs(lt, rel)) for r, rel, _, _ in _own_scripts(lt)]
    hits = [h for h in hits if h[2] >= 2]
    return {"value": len(hits), "evidence": ["%s (run %d times)" % (rel, n) for _, rel, n in hits[:3]],
            "note": "a script run by cron, make or npm run, or from another tool, leaves no Bash call that names it and isn't counted"}


@fixture(PROGRAM, "L3", 0)
def _(fx):
    fx.repo("~/work/shop", {"run_digest.py": "import claude_agent_sdk\nrun()", "nightly.sh": "#!/bin/sh\nclaude -p \"summarise\" < in.txt\n",
                            "README.md": "run `claude -p` to try", "notes.py": "# call claude -p \"x\" later\nprint('hi')",
                            "scan.py": "PAT = r'(?<![\\w-])claude\\s.*(?:-p|--print)'\nprint(PAT)"}, by="work")
    fx.commit("~/work/shop", {"ci_review.sh": "claude -p review"}, by="teammate")            # trap: a teammate's script, run twice
    s = _sess(fx, "~/work/shop"); s.typed("go")
    s.call("Bash", {"command": "python run_digest.py"}, "ok"); s.call("Bash", {"command": "cd x && uv run python run_digest.py"}, "ok")   # run twice: counts
    s.call("Bash", {"command": "bash nightly.sh"}, "ok")                                      # run once
    s.call("Bash", {"command": "cat nightly.sh"}, "x"); s.call("Bash", {"command": "git add nightly.sh"}, "x")   # reading it is not running it
    for _ in range(2):
        s.call("Bash", {"command": "python notes.py"}, "ok"); s.call("Bash", {"command": "python scan.py"}, "ok"); s.call("Bash", {"command": "./ci_review.sh"}, "ok")
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("summarise"); h.call("Bash", {"command": "python run_digest.py"}, "ok")   # a headless run's own call
    return {"value": 1}


TOOL_OPT = re.compile(r"--allowedTools|--allowed-tools|--disallowedTools|--disallowed-tools|--tools(?![\w-])|(?<![\w-])allowed_tools|disallowed_tools|allowedTools|disallowedTools")
TURN_CAP = re.compile(r"--max-turns|(?<![\w-])max_turns|maxTurns")
FIXED_PROMPT_CLI = re.compile(r"""(?:-p|--print)["']?[ \t=,]+["'][^"'\n]+["']""")
FIXED_PROMPT_SDK = re.compile(r"""query\s*\(\s*(?:prompt\s*[=:]\s*)?f?["'`]|prompt\s*[=:]\s*f?["'`]""")
SDK_OPTIONS = re.compile(r"ClaudeAgentOptions\s*\(|ClaudeCodeOptions\s*\(|options\s*[=:]\s*\{|query\s*\(\s*\{")


def _sets_limits(kind, region, code):
    """One start of the agent that fixes its prompt (a quoted literal), its tool list and its turn cap in the same call. A command
    line: the three on the command's line. An SDK program: the options object has both the tool list and the turn cap, and the
    prompt of query() is a literal."""
    if kind == "cli":
        return bool(FIXED_PROMPT_CLI.search(region) and TOOL_OPT.search(region) and TURN_CAP.search(region))
    if not FIXED_PROMPT_SDK.search(code):
        return False
    for m in SDK_OPTIONS.finditer(code):
        i = m.end() - 1
        reg = _balanced(code, i)
        if TOOL_OPT.search(reg) and TURN_CAP.search(reg):
            return True
    return False


@check(PROGRAM, "L3", 1)
def agent_scripts_with_limits(lt):
    """The person's own scripts (_own_scripts: theirs by the one ownership rule) where one start of the agent fixes the prompt (a quoted
    literal), a tool list (--allowedTools, --tools, allowed_tools) and a turn cap (--max-turns, max_turns) in the same call: on its
    command line, or in the SDK options object of the program. A budget cap alone is not a turn cap. Value = scripts."""
    hits = [rel for _, rel, code, calls in _own_scripts(lt) if any(_sets_limits(k, reg, code) for k, reg in calls)]
    return {"value": len(hits), "evidence": hits[:3], "note": "a prompt built at run time, or options kept in another file, aren't read"}


@fixture(PROGRAM, "L3", 1)
def _(fx):
    fx.repo("~/work/shop", {
        "a.sh": "claude -p \"x\" --max-turns 5 --allowedTools Read",
        "b.py": "from claude_agent_sdk import ClaudeAgentOptions, query\no = ClaudeAgentOptions(allowed_tools=['Read'], max_turns=3)\nasync for m in query(prompt=\"summarise\", options=o):\n    pass\n",
        "c.sh": "claude -p \"plain\" < in",
        "d.py": "max_turns = 3  # unrelated, no agent here",
        "f.sh": "claude -p \"x\" --max-budget-usd 2 --allowedTools Read",                         # trap: a budget cap is not a turn cap
        "g.sh": "# claude -p \"x\" --max-turns 5 --allowedTools Read\necho done",                  # trap: a comment
        "h.py": "from claude_agent_sdk import ClaudeAgentOptions, query\no = ClaudeAgentOptions(max_turns=3)\nquery(prompt=\"x\", options=o)\nother = dict(allowed_tools=['Read'])\n",   # trap: the tool list is not in the call
        "i.sh": "claude -p \"review\" \\\n  --max-turns 4 \\\n  --allowedTools \"Read,Grep\"\n",
        "j.sh": "echo 'use --max-turns and --allowedTools'\nclaude -p \"x\"\n",                      # trap: options named elsewhere in the file
    }, by="work")
    fx.commit("~/work/shop", {"e.sh": "claude -p y --max-turns 2 --allowedTools Read"}, by="teammate")
    _sess(fx, "~/work/shop").typed("go")
    return {"value": 3}


SDK_SERVER = re.compile(r"(?:create_sdk_mcp_server|createSdkMcpServer)\s*\(")
SDK_CALLBACK = re.compile(r"\bcan_use_tool\s*=|\bcanUseTool\s*[:=(,}]")


@check(PROGRAM, "L4", 0)
def sdk_programs_with_own_tools(lt):
    """The person's own source files (_own_scripts rule) that import the Agent SDK and use both of its two hooks as code: they
    call create_sdk_mcp_server / createSdkMcpServer (their own tools) and pass can_use_tool / canUseTool (their own permission
    callback). One of the two is not the claim. A name in a comment, a docstring or a pattern string is not a use. Value = files."""
    hits = [rel for _, rel, code, calls in _own_scripts(lt)
            if any(k == "sdk" for k, _ in calls) and SDK_SERVER.search(code) and SDK_CALLBACK.search(code)]
    return {"value": len(hits), "evidence": hits[:3], "note": "an SDK program whose tools live in another file counts only for the file with the call"}


@fixture(PROGRAM, "L4", 0)
def _(fx):
    fx.repo("~/work/shop", {
        "obs.py": "import claude_agent_sdk\nsrv = create_sdk_mcp_server('x')\nopts = claude_agent_sdk.ClaudeAgentOptions(can_use_tool=guard)\n",
        "run.ts": "import {query} from '@anthropic-ai/claude-agent-sdk'\nconst srv = createSdkMcpServer({name: 'x'})\nconst o={canUseTool: async () => true}\n",
        "plain.py": "import claude_agent_sdk\nquery()",
        "tools_only.py": "import claude_agent_sdk\nsrv = create_sdk_mcp_server('x')\n",            # trap: the claim needs both
        "callback_only.ts": "import {query} from '@anthropic-ai/claude-agent-sdk'\nconst o={canUseTool: async () => true}\n",
        "scanner.py": "RX = r'claude_agent_sdk.*create_sdk_mcp_server.*can_use_tool'\nSDK = 'import claude_agent_sdk'\n",   # trap: this scanner's own patterns
        "docs.py": "'''\nimport claude_agent_sdk\ncreate_sdk_mcp_server(); can_use_tool=f\n'''\nprint(1)\n",             # trap: a docstring
        "NOTES.md": "create_sdk_mcp_server with claude_agent_sdk and can_use_tool=x"}, by="work")
    fx.commit("~/work/shop", {"theirs.py": "import claude_agent_sdk\ncreate_sdk_mcp_server()\nx = dict(can_use_tool=f)"}, by="teammate")
    _sess(fx, "~/work/shop").typed("go")
    return {"value": 2}


# ====================================================================== commit-in-small-revertible-steps
BRACKET = re.compile(r"\[[^\]\n]*\s([0-9a-f]{7,40})\]")


def _commits_through_claude(lt):
    """[(session, time of the Bash call, short hash, repo)] for each of the person's own commits (any identity, any branch, in the
    window) whose hash git printed ([branch hash] message) in a successful Bash result of a session they worked in: that session ran
    the commit. A commit made by hand, or by another session or worktree, is not in this session's results."""
    def build():
        _prefetch_commits(lt, lt.repos)
        by = {}
        for repo in lt.repos:
            for ct, mine, h in _commits(lt, repo):
                if mine and ct >= lt.start:
                    by[h[:7]] = repo
        out = []
        for c in _calls(lt, "Bash"):
            if c["sc"] or c.get("err") or not c.get("out"):
                continue
            for h in BRACKET.findall(c["out"]):
                if h[:7] in by:
                    out.append((c["s"], c["t"], h[:7], by[h[:7]]))
        return out, by
    return _memo(lt, "commits_through_claude", build)


def _edit_spans(lt):
    """{session: (first edit, last edit, repo of the first)}: the agent's Edit / Write / MultiEdit / NotebookEdit calls in the repo of
    its first one (main thread of a session they worked in)."""
    def build():
        out = {}
        for c in _calls(lt, "Edit", "Write", "MultiEdit", "NotebookEdit"):
            if c["sc"]:
                continue
            path = _str(c, "file_path") or _str(c, "notebook_path")
            repo = lt.repo_of(path) if path else None
            if not repo:
                continue
            cur = out.get(c["s"])
            if cur is None:
                out[c["s"]] = [c["t"], c["t"], repo]
            elif repo == cur[2]:
                cur[0], cur[1] = min(cur[0], c["t"]), max(cur[1], c["t"])
        return {k: tuple(v) for k, v in out.items()}
    return _memo(lt, "edit_spans", build)


@check(COMMIT, "L2", 0)
def commit_before_first_edit(lt):
    """Sessions with agent edits in a repo where the session itself ran one of the person's commits (git printed its hash in that
    session's Bash result) before its first Edit / Write in that repo. Value = such sessions. A commit that merely sits near the first
    edit in time, made by hand or by another session, is not this session's checkpoint, and no time window decides it."""
    spans = _edit_spans(lt)
    ran, _ = _commits_through_claude(lt)
    first_run = {}    # (session, repo) -> time of the first commit that session ran there
    for s, t, _, r in ran:
        if t < first_run.get((s, r), float("inf")):
            first_run[(s, r)] = t
    n = 0
    for sid, (t0, _, repo) in spans.items():
        n += first_run.get((sid, repo), float("inf")) < t0
    return {"value": n, "evidence": ["%d of %d edit sessions" % (n, len(spans))],
            "note": "a checkpoint committed by hand before starting the session, or by a tool that doesn't print the hash, isn't seen"}


@fixture(COMMIT, "L2", 0)
def _(fx):
    T = time.time() - 3 * 86400
    plan = [("a", "work", "before", "claude-vscode"), ("b", "work", "after", "claude-vscode"), ("c", "teammate", "before", "claude-vscode"),
            ("d", "work", "before", "sdk-cli"), ("e", "work", "byhand", "claude-vscode"), ("f", "home", "before", "claude-desktop")]
    for name, who, when, ep in plan:
        repo = "~/work/%s" % name
        fx.repo(repo, {"README.md": "x"}, by="work")
        s = _sess(fx, repo, ep=ep, start=T)
        s.typed("change a.ts")
        _commit_at(fx, repo, {"a.txt": name}, who, T + 60, "c " + name)
        h = _head(fx, repo)
        if when == "before":
            s.call("Bash", {"command": "git commit -am checkpoint"}, "[main %s] c %s\n 1 file changed" % (h, name))
        s.call("Edit", {"file_path": fx.p(repo + "/a.ts"), "old_string": "a", "new_string": "b"})
        if when == "after":
            s.call("Bash", {"command": "git commit -am done"}, "[main %s] c %s\n 1 file changed" % (h, name))
    # the person's own commit sits right before the edit but the session never ran it (by hand, or from another session): the old rule counted it
    return {"value": 2}      # a (work email), f (home email); b is after the edit, c a teammate's, d headless, e not run by this session


@check(COMMIT, "L2", 1)
def commits_during_edit_sessions(lt):
    """Sessions with agent edits in which the session itself ran one of the person's commits (git printed its hash in that session's
    Bash result) in the repo it edited, after its first edit and before its last: the agent committed between steps. Value = such
    sessions; commits per such session in the evidence. A commit by hand, by another session or worktree, or after the last edit is not."""
    ran, _ = _commits_through_claude(lt)
    per, spans = [], _edit_spans(lt)
    for sid, (t0, t1, repo) in spans.items():
        k = len({h for s, t, h, r in ran if s == sid and r == repo and t0 < t < t1})
        if k:
            per.append(k)
    return {"value": len(per), "evidence": ["%d of %d edit sessions" % (len(per), len(spans)), "commits: %s" % per[:5]],
            "note": "a commit the person made by hand between steps, or one a tool made without printing its hash, isn't seen"}


@fixture(COMMIT, "L2", 1)
def _(fx):
    T = time.time() - 3 * 86400
    plan = [("a", "work", "between", "claude-vscode"), ("b", "work", "after", "claude-vscode"), ("c", "teammate", "between", "claude-vscode"),
            ("d", "work", "between", "sdk-cli"), ("e", "work", "byhand", "claude-vscode"), ("f", "home", "between", "claude-desktop")]
    for name, who, when, ep in plan:
        repo = "~/work/%s" % name
        fx.repo(repo, {"README.md": "x"}, by="work")
        s = _sess(fx, repo, ep=ep, start=T)
        s.typed("change a.ts")
        edit = lambda: s.call("Edit", {"file_path": fx.p(repo + "/a.ts"), "old_string": "a", "new_string": "b"})
        edit()
        _commit_at(fx, repo, {"a.txt": name}, who, T + 60, "c " + name)
        h = _head(fx, repo)
        if when in ("between", "after") :
            if when == "between":
                s.call("Bash", {"command": "git commit -am step"}, "[main %s] c %s\n 1 file changed" % (h, name))
            edit()
            if when == "after":
                s.call("Bash", {"command": "git commit -am done"}, "[main %s] c %s\n 1 file changed" % (h, name))
        else:
            edit()                       # by hand: the commit sits between the edits in time but this session never ran it
    return {"value": 2}      # a (work email) and f (home email); b is after the last edit, c a teammate's, d headless, e by hand


@check(COMMIT, "L2", 3)
def rewind_used(lt):
    """Times the person typed /rewind in an interactive session (transcript, plus history.jsonl for the terminal)."""
    got = [c for c in lt.all_commands() if lt.interactive(c["s"]) and c["name"] == "/rewind"]
    return {"value": len(got), "evidence": ["/rewind in %d sessions" % len({c["s"] for c in got})],
            "note": "rewinding with Esc Esc or the checkpoint menu leaves no record of its own, so this is a lower bound"}


@fixture(COMMIT, "L2", 3)
def _(fx):
    a = _sess(fx, "~/work/shop", ep="claude-desktop"); a.typed("x"); a.command("/rewind")
    b = _sess(fx, "~/work/shop", ep="cli"); b.typed("y"); b.command("/rewind")        # also in history.jsonl: counted once
    b.command("/rename x")                                                              # not a rewind
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("z"); h.command("/rewind")
    return {"value": 2}


@check(COMMIT, "L3", 1)
def commits_by_claude(lt):
    """Commits of the person's (any of their git identities, in repos their sessions ran in, in the window) whose short hash is
    printed by git in a Bash result of an interactive session ([branch hash] message): Claude ran the commit. The rest of
    their commits were made without a Bash step. Value = commits Claude made; by hand is in the evidence."""
    ran, by = _commits_through_claude(lt)
    claude = {h for _, _, h, _ in ran}
    return {"value": len(claude), "evidence": ["%d commits by them, %d through Claude, %d without a Bash step" % (len(by), len(claude), len(by) - len(claude))],
            "note": "a commit Claude made through a tool that doesn't print the hash, or in a headless run, reads as by hand"}


@fixture(COMMIT, "L3", 1)
def _(fx):
    repo = "~/work/shop"
    fx.repo(repo, {"README.md": "x"}, by="work")                         # by hand: no Bash step
    s = _sess(fx, repo)
    s.typed("commit your work")
    fx.commit(repo, {"a.txt": "1"}, by="work", message="second"); h2 = _head(fx, repo)
    s.call("Bash", {"command": "git add -A && git commit -m second"}, "[main %s] second\n 1 file changed" % h2)
    fx.commit(repo, {"a.txt": "2"}, by="home", message="third"); h3 = _head(fx, repo)
    s.call("Bash", {"command": "git commit -am third"}, "[main %s] third\n 1 file changed" % h3)
    s.call("Bash", {"command": "git commit -m again"}, "On branch main\nnothing to commit, working tree clean", is_error=True)   # trap
    fx.commit(repo, {"b.txt": "t"}, by="teammate", message="team")
    fx.commit(repo, {"c.txt": "5"}, by="work", message="fifth"); h5 = _head(fx, repo)
    h = _sess(fx, repo, ep="sdk-cli"); h.typed("go")
    h.call("Bash", {"command": "git commit -am fifth"}, "[main %s] fifth" % h5)       # trap: a headless run's commit
    return {"value": 2}


# ====================================================================== let-the-agent-fetch
@check(FETCH, "L3", 1)
def store_read_unprompted(lt):
    return {"value": None, "note": "can't tell from a laptop: which folder a rule file means as a store of notes is in its prose. Any path-like word "
            "in a rule (src/, docs/, ../) reads as one, and ordinary reads of source folders then count as the agent searching a store "
            "(skeptic, 11-05)"}


@fixture(FETCH, "L3", 1)
def _(fx):
    fx.repo("~/work/shop", {"CLAUDE.md": "# Regras\nAs notas ficam em `docs/notes/`. Veja ../shared/ e src/.\n", "docs/notes/a.md": "n", "src/app.ts": "x"}, by="work")
    s1 = _sess(fx, "~/work/shop"); s1.typed("what did we decide on pricing?")
    s1.call("Grep", {"pattern": "pricing", "path": fx.p("~/work/shop/docs/notes")})
    s1.call("Read", {"file_path": fx.p("~/work/shop/src/app.ts")})
    return {"value": None}


@check(FETCH, "L4", 0)
def own_server_called(lt):
    """Calls to MCP servers the person made: the server's command runs a script file of theirs (the file is theirs by the one ownership rule,
    lt.owner: not installed, not most of its commits someone else's). Owning the .mcp.json that lists a server does not make
    the server theirs: a third-party server they only configured is not their connector. Matched by the mcp__<server>__ tool prefix
    (a plugin's plugin_<plugin>_<server> too). Value = calls."""
    own = set()
    for name, (cfg, v) in _mcp_configured(lt).items():
        for arg in [v.get("command")] + list(v.get("args") or []):
            if not isinstance(arg, str) or not re.search(r"[/\\]|\.(js|mjs|cjs|ts|py|sh)$", arg):
                continue
            for cand in (arg, os.path.join(os.path.dirname(cfg), arg)):
                if os.path.isfile(cand) and _mine_file(lt, cand):
                    own.add(name)
    n, seen = 0, set()
    for c in _calls(lt, *{c["name"] for c in lt.calls if c["name"].startswith("mcp__")}):
        pre = _mcp_prefix(c["name"]) or ""
        if any(pre == o or (pre.startswith("plugin_") and pre.endswith("_" + o)) for o in own):
            n += 1
            seen.add(pre)
    return {"value": n, "evidence": ["own servers: %s" % ", ".join(sorted(own)[:3]), "called: %s" % ", ".join(sorted(seen)[:3])],
            "note": "a server started by a package from a registry is not read as theirs even if they wrote the package; a server run by a "
                    "binary or a script outside a repo is not read"}


@fixture(FETCH, "L4", 0)
def _(fx):
    fx.repo("~/work/shop", {".mcp.json": json.dumps({"mcpServers": {"shopdb": {"command": "node", "args": ["tools/db.js"]},
                                                                      "docsrv": {"command": "npx", "args": ["-y", "third-party-docs-mcp"]},
                                                                      "remote": {"type": "http", "url": "https://mcp.example/x"}}}),
                            "tools/db.js": "//", "tools/local.js": "//"}, by="work")
    fx.repo("~/work/team", {".mcp.json": json.dumps({"mcpServers": {"teamdocs": {"command": "node", "args": ["x.js"]}}}), "x.js": "//"}, by="teammate")
    fx.file("~/.claude.json", json.dumps({"mcpServers": {"vendor": {"command": "npx", "args": ["-y", "vendor-mcp"]},
                                                         "local": {"command": "node", "args": [fx.p("~/work/shop/tools/local.js")]},
                                                         "scratch": {"command": "node", "args": [fx.p("~/tmp-scripts/never-committed.js")]}}}))
    fx.file("~/tmp-scripts/never-committed.js", "//")       # counts: untracked but no install receipt = theirs (ruling 11-05)
    s = _sess(fx, "~/work/shop"); s.typed("query")
    for t in ("mcp__shopdb__query",) * 3 + ("mcp__teamdocs__get",) * 2 + ("mcp__vendor__x",) * 4 + ("mcp__local__x",) \
            + ("mcp__docsrv__query",) * 5 + ("mcp__remote__q",) * 2 + ("mcp__scratch__q",):      # traps: servers in a .mcp.json they own; scratch counts (an untracked script of theirs)
        s.call(t, {})
    _sess(fx, "~/work/team").typed("go")
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("go"); h.call("mcp__shopdb__query", {})
    return {"value": 5}      # shopdb 3, local 1, scratch 1


# ====================================================================== run-the-agent-in-a-box
@check(BOX, "L2", 0)
def throwaway_worktrees(lt):
    """Worktrees made for a job: EnterWorktree calls, Bash commands running `git worktree add`, sub-agents started with their own
    worktree (meta spawnedWithWorktree, or an Agent call with isolation worktree, counted once). Value = count; worktrees found
    on disk and temp-folder paths are not read."""
    n_enter = len(_calls(lt, "EnterWorktree"))
    n_add = sum(1 for c in _calls(lt, "Bash") if re.search(r"\bgit\s+(?:-C\s+\S+\s+)?worktree\s+add\b", c.get("input") or ""))
    metas = [m for m in lt.meta if m.get("spawnedWithWorktree") and lt.interactive(m["_session"])]
    tools = {m.get("toolUseId") for m in metas}
    n_iso = sum(1 for c in _calls(lt, "Agent", "Task") if re.search(r'"isolation":\s*"worktree"', c.get("input") or "") and c["id"] not in tools)
    return {"value": n_enter + n_add + len(metas) + n_iso, "evidence": ["%d EnterWorktree, %d git worktree add, %d sub-agents in a worktree" % (n_enter, n_add, len(metas) + n_iso)],
            "note": "whether the worktree was thrown away afterwards isn't read"}


@fixture(BOX, "L2", 0)
def _(fx):
    fx.repo("~/work/shop", {"README.md": "x"})
    fx._git(fx.p("~/work/shop"), "worktree", "add", "-q", fx.p("~/work/shop-old"), "-b", "old")          # trap: a worktree on disk
    s = _sess(fx, "~/work/shop")
    s.typed("do it in a copy")
    s.call("EnterWorktree", {"name": "a"}); s.call("EnterWorktree", {"name": "b"})
    s.call("Bash", {"command": "git worktree add ../x -b y"}); s.call("Bash", {"command": "git worktree list"})
    s.subagent("general-purpose", "isolated job", worktree=True)
    s.call("Agent", {"subagent_type": "x", "isolation": "worktree", "prompt": "p"})
    tid = s.call("Agent", {"subagent_type": "y", "isolation": "worktree", "prompt": "q"})
    d = os.path.join(os.path.dirname(s.path), s.sid, "subagents"); os.makedirs(d, exist_ok=True)
    json.dump({"agentType": "y", "toolUseId": tid, "spawnedWithWorktree": True}, open(os.path.join(d, "agent-zz.meta.json"), "w"))
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("go"); h.call("EnterWorktree", {"name": "h"})
    return {"value": 6}


@check(BOX, "L3", 0)
def sandbox_switched_on(lt):
    """Settings files (user, project, local, managed; every folder their sessions ran in) with sandbox.enabled true. Value = files;
    a sandbox key without enabled true, or enabled false, is not on."""
    on = [p for sc, p, d in _settings(lt) if isinstance(d.get("sandbox"), dict) and d["sandbox"].get("enabled") is True]
    return {"value": len(on), "evidence": [os.path.basename(os.path.dirname(p)) + "/" + os.path.basename(p) for p in on[:3]],
            "note": "a sandbox started by a launcher flag or a container around Claude Code isn't in a settings file"}


@fixture(BOX, "L3", 0)
def _(fx):
    fx.repo("~/code/api", {"README.md": "x"}, by="home"); fx.repo("~/work/team", {"README.md": "y"}, by="teammate")
    fx.settings("user", {"sandbox": {"enabled": True}})
    fx.settings("project-local", {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True}}, repo="~/code/api")
    fx.commit("~/work/team", {".claude/settings.json": json.dumps({"sandbox": {"enabled": False}})}, by="teammate")
    fx.settings("managed", {"sandbox": {"network": {"allowedDomains": ["x.com"]}}})          # trap: the key without enabled
    _sess(fx, "~/code/api").typed("go"); _sess(fx, "~/work/team").typed("go")
    return {"value": 2}


DEVC = re.compile(r"(^|/)(\.devcontainer/([^/]+/)?devcontainer\.json|\.devcontainer\.json)$")


@check(BOX, "L3", 1)
def dev_container(lt):
    """Repos (from the sessions) with a devcontainer.json (.devcontainer/devcontainer.json, .devcontainer/<name>/devcontainer.json,
    .devcontainer.json, at any depth) that are the person's by the one ownership rule (lt.owner): untracked counts, a teammate's committed one does not. A Dockerfile alone is not one.
    Value = repos."""
    hits = []
    for repo in lt.repos:
        for rel in _repo_listing(lt, repo):
            if DEVC.search(rel) and _yours(lt, os.path.join(repo, rel)):
                hits.append(os.path.basename(repo))
                break
    return {"value": len(hits), "evidence": hits[:3], "note": "a dev container the person set up but never opened Claude Code in isn't seen"}


@fixture(BOX, "L3", 1)
def _(fx):
    fx.repo("~/work/a", {".devcontainer/devcontainer.json": "{}"}, by="work")
    fx.repo("~/work/b", {"Dockerfile": "FROM x", "docker-compose.yml": "services: {}"}, by="work")            # trap: not a dev container
    fx.repo("~/work/c", {".devcontainer.json": "{}"}, by="home")
    fx.repo("~/work/d", {"services/api/.devcontainer/devcontainer.json": "{}"}, by="work")
    fx.repo("~/work/team", {".devcontainer/devcontainer.json": "{}"}, by="teammate")                          # trap: a teammate's
    for n in "abcd":
        _sess(fx, "~/work/" + n).typed("go")
    _sess(fx, "~/work/team").typed("go")
    return {"value": 3}


# ====================================================================== parallel-work-kept-apart
def _turns(lt):
    """{session: [(start, end)]}: one span per message the person typed in a session they worked in. A span runs from the message to the
    last record the agent wrote (any assistant message, so a sub-agent's work too) before their next message in that session. No window
    chosen: the agent was working while it kept writing, and not after its last record."""
    def build():
        import bisect
        typed, act = {}, {}
        for m in lt.typed:
            if lt.interactive(m["s"]) and not m["sc"]:
                typed.setdefault(m["s"], []).append(m["t"])
        for a in lt.assist:
            if a["s"] in typed:
                act.setdefault(a["s"], []).append(a["t"])
        out = {}
        for sid, tt in typed.items():
            tt.sort()
            at = sorted(act.get(sid, []))
            spans = []
            for i, t0 in enumerate(tt):
                nxt = tt[i + 1] if i + 1 < len(tt) else float("inf")
                j = bisect.bisect_left(at, nxt) - 1          # the last record before the next message
                end = at[j] if j >= 0 and at[j] > t0 else t0
                spans.append((t0, end))
            out[sid] = spans
        return out
    return _memo(lt, "turns", build)


@check(PARALLEL, "L2", 0)
def typed_while_another_session(lt):
    """Messages the person typed in one session while the agent of another interactive session was still working (that session's turn
    began before the message and its agent wrote a record after it). Value = such messages. No time window: a session left idle, even a
    minute earlier, is not working. It shows two sessions in use at once, not what each was doing."""
    import heapq
    spans = sorted((a, b, sid) for sid, sp in _turns(lt).items() for a, b in sp if b > a)
    msgs = sorted((t, sid) for sid, sp in _turns(lt).items() for t, _ in sp)
    heap, active, k, n = [], {}, 0, 0
    for t, sid in msgs:
        while k < len(spans) and spans[k][0] < t:
            heapq.heappush(heap, (spans[k][1], spans[k][2])); active[spans[k][2]] = active.get(spans[k][2], 0) + 1; k += 1
        while heap and heap[0][0] <= t:
            _, x = heapq.heappop(heap)
            active[x] -= 1
            if not active[x]:
                del active[x]
        if len(active) > (1 if sid in active else 0):
            n += 1
    return {"value": n, "evidence": ["%d of %d messages" % (n, len(msgs))],
            "note": "two sessions working at once doesn't show they were on separate things; a turn's end is its last record, so a long silent wait is cut short"}


@fixture(PARALLEL, "L2", 0)
def _(fx):
    T = time.time() - 2 * 86400
    a = _sess(fx, "~/work/shop", start=T); b = _sess(fx, "~/work/shop", ep="claude-desktop", start=T)
    c = _sess(fx, "~/work/shop", start=T); h = _sess(fx, "~/work/shop", ep="sdk-cli", start=T)
    a.t = T + 10; a.typed("one")
    for _ in range(6):
        a.call("Bash", {"command": "make build"})       # a's agent works until about T + 94
    b.t = T + 40; b.typed("two")                        # counts: a's agent is still working
    b.call("Read", {"file_path": "x"})
    a.t = T + 120; a.typed("three")                     # trap: b's agent finished at about T+54 and sits idle (the old 10-minute window counted this)
    h.t = T + 130; h.typed("headless prompt")           # trap: not a person
    c.t = T + 400; c.typed("hours later, a's agent has long finished")      # trap: a idle for 5 minutes, b idle too
    return {"value": 1}


def _repo_key(lt, cwd):
    """The git common dir of a session's folder: the same for a repo's main checkout and its linked worktrees."""
    if not cwd or not os.path.isdir(cwd):
        return None
    return lt.git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd).strip() or None


@check(PARALLEL, "L2", 1)
def sessions_in_worktrees(lt):
    """Sessions the person worked in that ran in a git worktree (the session folder is a linked worktree: its git dir differs from
    the repo's common dir; or it called EnterWorktree; or Claude Code wrote a worktree-state record) while another interactive
    session in the same repo was working at the same time (their turns overlap; _turns). A worktree alone, with nothing else running in
    the repo, is not parallel work kept apart. Value = such sessions. No folder name is assumed."""
    sids = {c["s"] for c in _calls(lt, "EnterWorktree")}
    for r in _raw(lt, ('"worktree-state"',)):
        if r.get("type") == "worktree-state" and isinstance(r.get("worktreeSession"), dict) and lt.interactive(r["_sid"]):
            sids.add(r["_sid"])
    turns = _turns(lt)
    keys = {}
    for sid in turns:
        cwd = (lt.sessions.get(sid) or {}).get("cwd")
        keys[sid] = _repo_key(lt, cwd)
        if cwd and os.path.isdir(cwd):
            g = lt.git("rev-parse", "--path-format=absolute", "--git-dir", cwd=cwd).strip()
            if g and keys[sid] and os.path.realpath(g) != os.path.realpath(keys[sid]):
                sids.add(sid)
    by_repo = {}
    for sid, sp in turns.items():
        if keys.get(sid):
            by_repo.setdefault(keys[sid], []).extend((a, b, sid) for a, b in sp if b > a)
    overlapped = set()
    for spans in by_repo.values():
        spans.sort()
        live = []        # (end, sid) of spans that began earlier and have not ended
        for a, b, sid in spans:
            live = [x for x in live if x[0] > a]
            others = {x[1] for x in live if x[1] != sid}
            if others:
                overlapped.add(sid); overlapped |= others
            live.append((b, sid))
    got = sorted(s for s in sids if s in overlapped)
    return {"value": len(got), "evidence": ["%d sessions in a worktree, %d of them overlapped by another session in the repo" % (len(sids & set(turns)), len(got))],
            "note": "a worktree removed since the session ran can't be checked from its folder; whether the sessions' edits overlapped isn't read"}


@fixture(PARALLEL, "L2", 1)
def _(fx):
    T = time.time() - 2 * 86400
    fx.repo("~/code/shop", {"README.md": "x"})
    fx._git(fx.p("~/code/shop"), "worktree", "add", "-q", fx.p("~/code/shop-feature"), "-b", "feature")
    fx.repo("~/code/other", {"README.md": "x"}); fx.repo("~/code/solo", {"README.md": "x"})
    # shop: a session in a linked worktree and one in the main checkout, working at the same time
    s1 = _sess(fx, "~/code/shop-feature", start=T); s1.t = T + 10; s1.typed("work on the feature")
    s2 = _sess(fx, "~/code/shop", start=T); s2.t = T + 20; s2.typed("fix the build")
    for _ in range(4):
        s1.call("Edit", {"file_path": "a"}); s2.call("Read", {"file_path": "b"})
    # other: EnterWorktree, but nothing else runs in the repo
    s3 = _sess(fx, "~/code/other", start=T + 600); s3.typed("go"); s3.call("EnterWorktree", {"name": "x"}); s3.call("Read", {"file_path": "x"})
    # solo: a worktree-state record, and another session in the repo hours later (not at the same time)
    s4 = _sess(fx, "~/code/solo", start=T + 1200); s4.typed("go"); s4.record("worktree-state", worktreeSession={"worktreePath": "/x", "worktreeName": "x"}); s4.call("Read", {"file_path": "x"})
    s5 = _sess(fx, "~/code/solo", start=T + 7200); s5.typed("later"); s5.call("Read", {"file_path": "x"})
    # a headless run in shop at the same time
    h = _sess(fx, "~/code/shop", ep="sdk-cli", start=T + 15); h.typed("go"); h.call("EnterWorktree", {"name": "h"})
    return {"value": 1}          # s1: a worktree, s2 working beside it; s2 is not in a worktree


SHELL_RC = (".zshrc", ".bashrc", ".zprofile", ".bash_profile", ".profile", ".aliases", ".config/fish/config.fish")
WT_TEXT = re.compile(r"git\s+worktree\s+add|claude\s+(?:-w|--worktree)\b")
# a shell line that defines a command: an alias, a function (name() { or function name), or an export of a variable holding one
ALIAS_DEF = re.compile(r"^\s*(?:alias\s+[\w.-]+=|function\s+[\w.-]+|[\w.-]+\s*\(\)\s*\{?)")


ALIAS_NAME = re.compile(r"^\s*(?:alias\s+([\w.-]+)=|function\s+([\w.-]+)|([\w.-]+)\s*\(\)\s*\{?)")


def _first_words(cmd):
    """The command word of each segment of a shell command line (after ; && || | and newlines, behind env assignments)."""
    out = []
    for seg in re.split(r"&&|\|\||[;|\n]", cmd):
        for w in seg.split():
            if "=" in w and not w.startswith(("-", "/", ".")):
                continue
            out.append(w.strip("'\"`()"))
            break
    return out


@check(PARALLEL, "L3", 0)
def own_worktree_definitions(lt):
    """Launches of the person's own agents that run in their own copy: an agent file of theirs with isolation: worktree in its
    frontmatter (theirs by the one ownership rule; _own_artifacts) that an Agent / Task call launched by its name in a session they worked
    in. A definition that was never launched is not the practice; a command or skill file that only mentions worktrees is not read as
    opening each task in its own copy. Value = such agents that were launched (launch counts in the evidence). Shell aliases and functions
    in the person's startup files that run `git worktree add` or `claude --worktree`, and used as a command inside a session, are listed
    in the evidence and not counted: a shell function says nothing about what ran in the copy or that the agent was kept apart by it."""
    launched = {}
    for t, c in _agent_launches(lt):
        if _str(c, "subagent_type") and ":" not in t:
            launched[t] = launched.get(t, 0) + 1
    used = {}
    for cmd in _bash_commands(lt) + [m["text"][len("<bash-input>"):].split("</bash-input>")[0] for m in lt.typed
                                      if m["text"].startswith("<bash-input>") and lt.interactive(m["s"]) and not m["sc"]]:
        for w in _first_words(cmd):
            used[w] = used.get(w, 0) + 1
    defined, ran, ev = 0, 0, []
    for a in _own_artifacts(lt):
        fm = re.match(r"---\n(.*?)\n---", a["text"], re.S)
        if a["kind"] == "agent" and fm and re.search(r"^isolation:\s*['\"]?worktree", fm.group(1), re.M):
            defined += 1
            if launched.get(a["name"]):
                ran += 1; ev.append("agent %s launched %d times" % (a["name"], launched[a["name"]]))
    aliases = []
    for rc in SHELL_RC:
        p = os.path.join(lt.home, rc)
        if os.path.isfile(p):
            for line in open(p, errors="replace"):
                if ALIAS_DEF.match(line) and WT_TEXT.search(line) and not line.lstrip().startswith("#"):
                    name = next((g for g in ALIAS_NAME.match(line).groups() if g), None)
                    if name and used.get(name):
                        aliases.append("%s (used %d times)" % (name, used[name]))
    return {"value": ran, "evidence": ["%d own agents with isolation: worktree, %d of them launched" % (defined, ran)] + ev[:2]
            + (["shell worktree commands used in a session, not counted: " + ", ".join(aliases[:3])] if aliases else []),
            "note": "only a launch by name of the person's own agent counts; a shell alias or function is listed, not counted; whether the work in the copy "
                    "was kept apart is not read"}


@fixture(PARALLEL, "L3", 0)
def _(fx):
    fx.repo("~/work/shop", {".claude/agents/iso.md": "---\nname: iso\nisolation: worktree\n---\ndo it", ".claude/agents/plain.md": "---\nname: plain\n---\nbody",
                            ".claude/agents/note.md": "---\nname: note\n---\nthe key isolation: worktree is described here",
                            ".claude/agents/unused.md": "---\nname: unused\nisolation: worktree\n---\ndefined, never launched",   # trap: exists, never ran
                            ".claude/commands/task.md": "Run `git worktree add ../$1 -b $1` then start",          # trap: a file that only mentions it
                            ".claude/skills/wt/SKILL.md": "---\nname: wt\n---\nUse EnterWorktree when asked"}, by="work")
    fx.repo("~/work/team", {".claude/agents/theirs.md": "---\nname: theirs\nisolation: worktree\n---\nx"}, by="teammate")     # trap
    fx.file("~/.claude/agents/mine.md", "---\nname: mine\nisolation: worktree\n---\nnobody committed it")                    # counts when launched: untracked but no install receipt = theirs (ruling 11-05)
    fx.file("~/.zshrc", "# git worktree add is great\nalias newtask='git worktree add ../$1 -b $1'\nalias gs='git status'\n"
                        "echo 'git worktree add'\nnt() { git worktree add ../$1 -b $1; }\nalias neverused='claude --worktree'\n")
    s = _sess(fx, "~/work/shop")
    s.typed("go")
    s.call("Agent", {"subagent_type": "iso", "prompt": "p"}); s.call("Agent", {"subagent_type": "iso", "prompt": "q"})   # counts, once
    s.call("Agent", {"subagent_type": "mine", "prompt": "p"})                                                         # counts
    s.call("Agent", {"subagent_type": "plain", "prompt": "p"})                                                        # trap: no worktree isolation in its file
    s.call("Agent", {"subagent_type": "theirs", "prompt": "p"})                                                       # trap: a teammate's agent
    s.call("Bash", {"command": "newtask feature-x"})                                                                  # trap: the alias ran in a session (evidence only, used to count)
    s.call("Bash", {"command": "gs && echo hi"})                                                                      # trap: an alias that doesn't open a worktree
    s.typed("<bash-input>nt other-task</bash-input>")                                                                # trap: the function, typed as a ! line (evidence only, used to count)
    h = _sess(fx, "~/work/shop", ep="sdk-cli"); h.typed("go"); h.call("Agent", {"subagent_type": "unused", "prompt": "p"})   # trap: a headless run launched it
    _sess(fx, "~/work/team").typed("go")
    return {"value": 2}      # iso, mine launched. Not counted: the shell commands newtask and nt (evidence only), unused (never launched), neverused


# ====================================================================== ask-without-leading
@check(ASK, "L3", 0)
def own_standing_instructions(lt):
    return {"value": None, "note": "can't tell from a laptop: nearly everyone has a CLAUDE.md, AGENTS.md or rule file, and whether one tells the agent "
            "to disagree, ask first or review is in its prose; a reviewer agent's job is in its text too. The existence of the file is not the "
            "claim (skeptic, 11-05)"}


@fixture(ASK, "L3", 0)
def _(fx):
    fx.file("~/.claude/CLAUDE.md", "I prefer short answers.")
    fx.repo("~/work/shop", {"CLAUDE.md": "# Shop\nrules", ".claude/rules/style.md": "style", "AGENTS.md": "agents"}, by="work")
    _sess(fx, "~/work/shop").typed("go")
    return {"value": None}


# Built once before the checks split into workers (evaluate.py / run_portable.py run_prewarm_hooks): each is memoized on
# the Laptop and used by several checks, so building it in the parent spares every worker its own copy.
PREWARM = [_settings, _plugins, _mcp_configured, _skill_uses, _own_artifacts, _own_scripts, _bash_commands, _commits_through_claude, _edit_spans]
