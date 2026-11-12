"""Group 2: nine practices, 38 checks, each rewritten on the two kinds of evidence (what Claude Code writes the same way
for everyone; what the person's own files say), each with a stranger-laptop fixture that plants a trap.

Helpers named here (base.py lacked them): _jin/_call_path (a tool call's input, Windows paths normalised), _tracked,
_decl (a repo's declared steps as text), _hooks/_resolve (settings hooks and the script each runs), _artifacts (the
agent-facing things a person built: plugin, marketplace, skill, agent, command, hook script, MCP server file, each with
installed / copied / authored-by-you), _git_commits (a repo's commits in the window with files touched).
"""
import datetime
import gc
import glob
import hashlib
import json
import os
import re
import time

from base import check, epoch
from fixture import NOW, fixture

EDITS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
DAY = 86400


# ---------------------------------------------------------------------------------------------------- helpers
def _calm_gc():
    """Keep what is already loaded (the log, millions of objects) out of the garbage collector's rounds: a check that makes many
    small objects otherwise pays a full pass over all of it, over and over. Changes no answer."""
    gc.freeze()


def _cache(lt):
    return lt.__dict__.setdefault("_g2", {})


def _n(p):
    return (p or "").replace("\\", "/")


def _unesc(v):
    try:
        return json.loads('"' + v + '"')
    except ValueError:
        return v


def _jin(c):
    """A tool call's input as a dict; when the log cut it short, the string fields that survived."""
    try:
        d = json.loads(c["input"])
        if isinstance(d, dict):
            return d
    except ValueError:
        pass
    d = {}
    for k in ("file_path", "notebook_path", "path", "command", "skill", "subagent_type", "model", "name"):
        m = re.search(r'"%s":\s*"((?:[^"\\]|\\.)*)' % k, c.get("input") or "")
        if m:
            d[k] = _unesc(m.group(1))
    return d


def _is_abs(p):
    return p.startswith("/") or (len(p) > 2 and p[1:3] == ":/" and p[0] in _LETTERS)


_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _call_path(lt, c):
    d = _jin(c)
    p = d.get("file_path") or d.get("notebook_path") or d.get("path")
    if not isinstance(p, str) or not p:
        return None
    p = _n(p)
    if p.startswith("~/"):
        p = _n(lt.home) + p[1:]
    if not _is_abs(p):
        cwd = (lt.sessions.get(c["s"]) or {}).get("cwd")
        if cwd:
            p = _n(cwd).rstrip("/") + "/" + p
    return p


_CMD_OF_INPUT = {}


def _cmd(c):
    """A Bash call's command; each distinct call input is read once (the same command is run again and again)."""
    key = c["input"]
    if key not in _CMD_OF_INPUT:
        v = _jin(c).get("command")
        _CMD_OF_INPUT[key] = v if isinstance(v, str) else ""
    return _CMD_OF_INPUT[key]


def _calls(lt, names, sidechain=False, interactive=True):
    """The tool calls named `names` (kept: the same question is asked by several checks)."""
    memo = _cache(lt).setdefault("calls", {})
    names = frozenset(names)
    key = (names, sidechain, interactive)
    if key not in memo:
        memo[key] = [c for c in lt.calls
                     if c["name"] in names and (sidechain or not c["sc"]) and (not interactive or lt.interactive(c["s"]))]
    return memo[key]


def _att(lt, typ, interactive=True):
    return [a for a in lt.attachments if a["type"] == typ and (not interactive or lt.interactive(a["s"]))]


def _field(data, key):
    m = re.search(r'"%s":\s*"((?:[^"\\]|\\.)*)"' % key, data or "")
    return _unesc(m.group(1)) if m else None


def _typed_by_session(lt):
    out = {}
    for m in lt.typed:
        if lt.interactive(m["s"]):
            out.setdefault(m["s"], []).append(m)
    return out


_GIT_OCTETS = re.compile(r"(?:\\[0-7]{3})+")


def _git_unquote(line):
    """A path as `git ls-files` prints it by default, as `git -c core.quotepath=false ls-files` prints it: the octal escapes
    of non-ASCII bytes become the characters, and the quotes go unless something else (a tab, a quote) still needs them."""
    if not (len(line) >= 2 and line[0] == '"' and line[-1] == '"'):
        return line
    body = _GIT_OCTETS.sub(lambda m: bytes(int(x, 8) for x in m.group(0).split("\\")[1:]).decode("utf-8", "surrogateescape"),
                           line[1:-1])
    return line[0] + body + line[-1] if "\\" in body else body


def _tracked(lt, repo):
    """The files of a repo that are not ignored, tracked or not (whose each is: _yours_of, untracked files are theirs). Read back from
    git's quoting of unusual names; the first call asks every repo at once."""
    c = _cache(lt).setdefault("tracked", {})
    if repo not in c:
        from concurrent.futures import ThreadPoolExecutor
        lt.repos        # read before any thread starts
        todo = [r for r in dict.fromkeys([repo] + list(lt.repos)) if r not in c]
        with ThreadPoolExecutor(8) as ex:   # git runs in its own process: threads overlap the waiting
            outs = list(ex.map(lambda r: lt.git("ls-files", "--cached", "--others", "--exclude-standard", cwd=r), todo))
        for r, out in zip(todo, outs):
            c[r] = [_git_unquote(x) for x in out.splitlines() if x]
    return c[repo]


def _read(path, limit=300000):
    try:
        with open(path, errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _since(lt):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(lt.start))


def _decl(lt, repo):
    """What the repo declares as its own steps, as [(label, text)]: package.json scripts, Makefile, justfile, CI workflows,
    pre-commit config, git hook folders, tox/pyproject/setup.cfg. Text, so a caller can look for what it wants in it."""
    out = []
    pj = os.path.join(repo, "package.json")
    if os.path.exists(pj):
        try:
            scripts = json.load(open(pj)).get("scripts") or {}
        except (OSError, ValueError):
            scripts = {}
        for k, v in scripts.items():
            out.append((f"package.json script {k}", str(v)))
    for f in ("Makefile", "makefile", "GNUmakefile", "justfile", "Justfile", ".justfile", ".pre-commit-config.yaml",
              ".gitlab-ci.yml", "tox.ini", "pyproject.toml", "setup.cfg", "pytest.ini"):
        p = os.path.join(repo, f)
        if os.path.exists(p):
            out.append((f, _read(p)))
    for wf in glob.glob(os.path.join(repo, ".github", "workflows", "*.y*ml")):
        out.append(("CI workflow " + os.path.basename(wf), _read(wf)))
    for d in (".husky", ".githooks"):
        for p in glob.glob(os.path.join(repo, d, "*")):
            if os.path.isfile(p):
                out.append((f"git hook {os.path.basename(p)}", _read(p)))
    return out


SCRIPT_REF = re.compile(r"[\w@./\\-]+\.(?:sh|py|js|mjs|cjs|ts|rb|ps1|bat|cmd)\b")


def _hop(repo, text):
    """The text of the repo script files a declared step names (one hop)."""
    out = []
    for ref in set(SCRIPT_REF.findall(text)):
        p = os.path.join(repo, _n(ref).lstrip("./"))
        if os.path.isfile(p):
            out.append((ref, _read(p, 100000)))
    return out


TEST_NAME = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.(py|go|rb)|[^/]*\.(test|spec)\.[A-Za-z]+|[^/]*_spec\.rb|[^/]*Tests?\.(java|cs|php|kt|swift))$|(^|/)__tests__/|(^|/)src/test/")


_ROOTS = {}   # repo -> its declared test folders (read once; a repo folder is the same for every check)


def _test_roots(repo):
    """Test folders the repo itself declares (pytest testpaths) plus nothing else."""
    if repo not in _ROOTS:
        _ROOTS[repo] = _test_roots_read(repo) if repo else []
    return _ROOTS[repo]


def _test_roots_read(repo):
    out = []
    for f in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"):
        t = _read(os.path.join(repo, f))
        for m in re.finditer(r"testpaths\s*=\s*(\[[^\]]*\]|[^\n]*)", t):
            out += [x.strip("/ ") for x in re.findall(r"[\w./-]+", m.group(1)) if x.strip("/ ")]
    return out


def _is_test(repo, rel, roots=None):
    rel = _n(rel)
    if TEST_NAME.search(rel):
        return True
    return any(rel == r or rel.startswith(r + "/") for r in (roots if roots is not None else _test_roots(repo)))


NOT_CODE = {"md", "txt", "rst", "json", "yaml", "yml", "toml", "lock", "cfg", "ini", "csv", "svg", "png", "jpg", "jpeg",
            "gif", "gitignore", "pdf", "mdx"}


def _is_source(rel):
    ext = rel.rsplit(".", 1)[-1].lower() if "." in os.path.basename(rel) else ""
    return ext not in NOT_CODE and bool(ext)


def _git_commits(lt, repo, name_status=True):
    """The repo's non-merge commits in the window: [{h, t, author, mine, files: [(status, path, oldpath)]}]."""
    key = ("commits", repo)
    c = _cache(lt)
    if key in c:
        return c[key]
    out = []
    raw = lt.git("-c", "core.quotepath=false", "log", "--no-merges", f"--since={_since(lt)}", "-M", "--name-status",
                 "--format=@@%H|%ct|%an <%ae>", cwd=repo)
    cur = None
    for line in raw.splitlines():
        if line.startswith("@@"):
            h, ct, au = line[2:].split("|", 2)
            cur = {"h": h, "t": int(ct), "author": au, "mine": lt.is_mine(au), "files": []}
            out.append(cur)
        elif cur is not None and line.strip():
            parts = line.split("\t")
            st = parts[0][0]
            if st in "RC" and len(parts) >= 3:
                cur["files"].append((st, parts[2], parts[1]))
            elif len(parts) >= 2:
                cur["files"].append((st, parts[1], None))
    c[key] = out
    return out


def _fm(text):
    """Front matter as a flat dict; list values joined by spaces."""
    m = re.match(r"---\s*\r?\n(.*?)\r?\n---", text, re.S)
    d = {}
    if m:
        cur = None
        for line in m.group(1).splitlines():
            kv = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
            if kv:
                cur = kv.group(1); d[cur] = kv.group(2).strip()
            elif cur and line.strip().startswith("-"):
                d[cur] += " " + line.strip()[1:].strip()
    return d


# ---- hooks, and the script a hook command runs
def _tokens(cmd):
    return [t.strip("\"'") for t in re.findall(r"\"[^\"]*\"|'[^']*'|\S+", cmd or "")]


def _resolve(lt, cmd, bases, plugin_root=None):
    """The first file this shell command names that lies under the person's home (a script they or a plugin supplied)."""
    home = os.path.realpath(lt.home)
    for tok in _tokens(cmd)[:8]:
        t = tok
        for var, val in (("CLAUDE_PLUGIN_ROOT", plugin_root or ""), ("CLAUDE_PROJECT_DIR", bases[0] if bases else ""), ("HOME", lt.home)):
            t = t.replace("${%s}" % var, val).replace("$%s" % var, val)
        t = _n(t)
        if t.startswith("~/"):
            t = _n(lt.home) + t[1:]
        cands = [t] if _is_abs(t) else [os.path.join(b, t) for b in bases if b]
        for p in cands:
            if os.path.isfile(p) and os.path.realpath(p).startswith(home + os.sep):
                return os.path.realpath(p)
    return None


def _enabled_plugins(lt):
    en = {}
    for _, _, data in lt.settings_files():
        ep = data.get("enabledPlugins")
        if isinstance(ep, dict):
            for k, v in ep.items():
                en[k] = en.get(k, False) or v is True
    return en


def _plugin_root(lt, path, repo):
    d = os.path.dirname(os.path.realpath(path))
    top = repo or lt.home
    while True:
        if os.path.isfile(os.path.join(d, ".claude-plugin", "plugin.json")):
            return d
        if d == top or len(d) <= len(top) or os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)


def _plugin_name(root):
    try:
        return json.load(open(os.path.join(root, ".claude-plugin", "plugin.json"))).get("name")
    except (OSError, ValueError):
        return None


def _hooks(lt):
    """Every hook the person's Claude Code would run: settings files, and hooks/hooks.json of plugins in their repos.
    Each: {event, type, command, prompt, scope, file, script, script_yours, config_yours}."""
    c = _cache(lt)
    if "hooks" in c:
        return c["hooks"]
    out = []

    def add(groups_by_event, scope, file, bases, config_yours, plugin_root=None):
        if not isinstance(groups_by_event, dict):
            return
        for ev, groups in groups_by_event.items():
            for g in groups if isinstance(groups, list) else []:
                for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                    if not isinstance(h, dict):
                        continue
                    cmd = h.get("command") or ""
                    script = _resolve(lt, cmd, bases, plugin_root) if cmd else None
                    out.append({"event": ev, "type": h.get("type") or "command", "command": cmd, "prompt": h.get("prompt") or "",
                                "scope": scope, "file": file, "script": script, "plugin_root": plugin_root,
                                "script_yours": None, "config_yours": config_yours})

    hook_files = [os.path.join(r, rel) for r in lt.repos for rel in _tracked(lt, r) if _n(rel).endswith("hooks/hooks.json")]
    _yours_of(lt, [path for scope, path, _ in lt.settings_files() if scope != "managed"] + [p for p in hook_files if os.path.isfile(p)])
    for scope, path, data in lt.settings_files():
        # scope checks (which folder the file's relative paths mean; a managed policy is the employer's), then the one ownership rule
        base = os.path.dirname(os.path.dirname(path)) if scope.startswith("project") else lt.home
        yours = scope != "managed" and _yours_of(lt, [path])[path]
        add(data.get("hooks"), scope, path, [base, lt.home], yours)
    for r in lt.repos:
        for rel in _tracked(lt, r):
            if _n(rel).endswith("hooks/hooks.json"):
                p = os.path.join(r, rel)
                try:
                    data = json.load(open(p))
                except (OSError, ValueError):
                    continue
                root = os.path.dirname(os.path.dirname(p))
                add(data.get("hooks") or data, "plugin", p, [root], _yours_of(lt, [p])[p], plugin_root=root)
    yours = _yours_of(lt, [h["script"] for h in out if h["script"]])     # one look at each repo's history, not one per script
    for h in out:
        if h["script"]:
            h["script_yours"] = yours[h["script"]]
    c["hooks"] = out
    return out


# ---- the agent-facing things a person built
def _installed_prompt_files(base):
    """Every SKILL.md, agents/*.md, commands/*.md, .claude-plugin/plugin.json and .claude-plugin/marketplace.json below base, in
    one walk (not hidden folders, not through links: what `glob("**/...")` finds)."""
    stack = [base]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        if any(e.name == ".claude-plugin" for e in entries):
            for n in ("plugin.json", "marketplace.json"):
                f = os.path.join(d, ".claude-plugin", n)
                if os.path.lexists(f):
                    yield f
        in_prompts = os.path.basename(d) in ("agents", "commands")
        for e in entries:
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if is_dir:
                if not e.name.startswith("."):
                    stack.append(e.path)
            elif e.name == "SKILL.md" or (in_prompts and e.name.endswith(".md") and not e.name.startswith(".")):
                yield e.path


def _installed_index(lt):
    """sha1 of every skill / agent / command / manifest file in the installed plugin copies, and the skills a lock file lists."""
    c = _cache(lt)
    if "inst" in c:
        return c["inst"]
    hashes = set()
    for root in ("cache", "marketplaces"):
        for f in _installed_prompt_files(os.path.join(lt.claude, "plugins", root)):
            try:
                hashes.add(hashlib.sha1(open(f, "rb").read()).hexdigest())
            except OSError:
                pass
    locked = set()
    locks = [os.path.join(lt.home, ".agents", ".skill-lock.json")]
    for r in lt.repos:
        locks += [os.path.join(r, rel) for rel in _tracked(lt, r) if os.path.basename(rel) in ("skills-lock.json", ".skill-lock.json")]
    for p in locks:
        try:
            sk = json.load(open(p)).get("skills") or {}
            locked |= set(sk) if isinstance(sk, dict) else set()
        except (OSError, ValueError):
            pass
    c["inst"] = (hashes, locked)
    return c["inst"]


def _sha(path):
    try:
        return hashlib.sha1(open(path, "rb").read()).hexdigest()
    except OSError:
        return None


def _author_is_you(lt, author):
    """A manifest's author field against the person's identity (emails, names); None when the manifest names nobody."""
    if isinstance(author, str):
        author = {"name": author}
    if not isinstance(author, dict) or not (author.get("name") or author.get("email")):
        return None
    idn = lt.identity
    em, nm = (author.get("email") or "").strip().lower(), (author.get("name") or "").strip().lower()
    return (em in idn["emails"]) or (nm in idn["names"]) or any(nm and nm in n for n in idn["names"] if len(nm) > 3)


def _is_artifact_name(rel):
    """A tracked file that may be a plugin / marketplace manifest, skill, agent, command or MCP server list."""
    b = os.path.basename(rel)
    return b in ("plugin.json", "marketplace.json", "SKILL.md", ".mcp.json") or \
        (b.endswith(".md") and os.path.basename(os.path.dirname(_n(rel))) in ("agents", "commands"))


def _artifacts(lt):
    """The things a person built for the agent. Each: {kind, name, path, installed, reason (why it is not theirs, or None),
    yours (the one ownership rule, lt.owner), own, wired}. Told apart in order: whose it is by the one ownership rule (ruling 11-05:
    theirs unless a receipt says installed, or most commits are someone else's; untracked is theirs), then two extra "installed"
    receipts read off files (a lock file in a repo, a copy of an installed file), then the manifest's author field."""
    c = _cache(lt)
    if "art" in c:
        return c["art"]
    hashes, locked = _installed_index(lt)
    enabled = _enabled_plugins(lt)
    known_mk = set()
    try:
        known_mk |= set(json.load(open(os.path.join(lt.claude, "plugins", "known_marketplaces.json"))))
    except (OSError, ValueError):
        pass
    for _, _, data in lt.settings_files():
        if isinstance(data.get("extraKnownMarketplaces"), dict):
            known_mk |= set(data["extraKnownMarketplaces"])
    out, seen = [], set()
    _yours_of(lt, [os.path.join(r, rel) for r in lt.repos for rel in _tracked(lt, r) if _is_artifact_name(rel)]
              + glob.glob(os.path.join(lt.claude, "skills", "*", "SKILL.md")) + glob.glob(os.path.join(lt.claude, "agents", "*.md"))
              + glob.glob(os.path.join(lt.claude, "commands", "*.md")))     # every history read up front, in one go

    def plugin_on(root):
        n = _plugin_name(root) if root else None
        return bool(n) and any(k.split("@")[0] == n and v for k, v in enabled.items())

    def add(kind, name, path, wired, author=None, repo=None):
        rp = os.path.realpath(path)
        if (kind, rp) in seen:
            return
        seen.add((kind, rp))
        reason = None
        yours = _yours_of(lt, [path])[path]      # the one ownership rule: theirs, or an installed receipt, or a teammate's by git
        if not yours and lt.owner(path) == "installed":
            reason = "installed (a marketplace plugin or a skills lock, per Claude Code's records)"
        # two extra "installed" receipts, read off files: a lock file a repo holds, and a copy of an installed plugin's file
        elif kind == "skill" and name in locked:
            reason = "listed in a skills lock file"
        elif kind in ("skill", "agent", "command", "plugin", "marketplace") and _sha(rp) in hashes:
            reason = "same content as an installed copy"
        elif not yours:
            reason = "most commits are someone else's"
        # not a receipt or git: a manifest naming someone else as author marks a vendored copy the person committed. Kept as an extra
        # "not theirs" signal beside the rule; the ruling's one rule would call such a file theirs (listed in the report, not decided here)
        if reason is None and kind in ("plugin", "marketplace") and _author_is_you(lt, author) is False:
            reason = "manifest names another author"
        out.append({"kind": kind, "name": name, "path": rp, "reason": reason, "yours": yours,
                    "own": reason is None and yours, "wired": wired, "repo": repo})

    def under(path, *dirs):
        parts = _n(path).split("/")
        return any(d in parts for d in dirs)

    for r in lt.repos:
        for rel in _tracked(lt, r):
            b = os.path.basename(rel)
            if not _is_artifact_name(rel):
                continue
            p, relp = os.path.join(r, rel), _n(rel)
            if not os.path.isfile(p):
                continue
            proot = _plugin_root(lt, p, r)
            in_claude = relp.startswith(".claude/") or "/.claude/" in relp
            loaded = in_claude or plugin_on(proot)
            if b == "plugin.json" and relp.endswith(".claude-plugin/plugin.json"):
                try:
                    m = json.load(open(p))
                except (OSError, ValueError):
                    m = {}
                add("plugin", m.get("name") or os.path.basename(os.path.dirname(os.path.dirname(p))), p,
                    plugin_on(os.path.dirname(os.path.dirname(p))), m.get("author"), r)
            elif b == "marketplace.json" and relp.endswith(".claude-plugin/marketplace.json"):
                try:
                    m = json.load(open(p))
                except (OSError, ValueError):
                    m = {}
                add("marketplace", m.get("name") or "marketplace", p, (m.get("name") in known_mk), m.get("owner"), r)
            elif b == "SKILL.md":
                name = _fm(_read(p, 4000)).get("name") or os.path.basename(os.path.dirname(p))
                add("skill", os.path.basename(os.path.dirname(p)), p, loaded, repo=r)
                out[-1]["alias"] = name
            elif b.endswith(".md") and os.path.basename(os.path.dirname(relp)) == "agents":
                name = _fm(_read(p, 4000)).get("name") or b[:-3]
                add("agent", b[:-3], p, loaded, repo=r)
                out[-1]["alias"] = name
            elif b.endswith(".md") and os.path.basename(os.path.dirname(relp)) == "commands":
                add("command", b[:-3], p, loaded, repo=r)
            elif b == ".mcp.json":
                try:
                    m = json.load(open(p))
                except (OSError, ValueError):
                    continue
                servers = m.get("mcpServers") if isinstance(m.get("mcpServers"), dict) else m
                for sname, spec in (servers or {}).items():
                    if not isinstance(spec, dict):
                        continue
                    script = _resolve(lt, " ".join([str(spec.get("command") or "")] + [str(a) for a in spec.get("args") or []]),
                                      [os.path.dirname(p), r], proot or os.path.dirname(p))
                    if script:
                        add("mcp", sname, script, True, repo=r)
    for sub, kind in (("skills", "skill"), ("agents", "agent"), ("commands", "command")):
        pat = os.path.join(lt.claude, sub, "*", "SKILL.md") if kind == "skill" else os.path.join(lt.claude, sub, "*.md")
        for p in glob.glob(pat):
            nm = os.path.basename(os.path.dirname(p)) if kind == "skill" else os.path.basename(p)[:-3]
            add(kind, nm, p, True)
    for h in _hooks(lt):
        if h["script"]:
            on = True if h["scope"] != "plugin" else plugin_on(h["plugin_root"])
            add("hook", os.path.basename(h["script"]), h["script"], on)
    c["art"] = out
    return out


def _by_kind(arts):
    d = {}
    for a in arts:
        d.setdefault(a["kind"], []).append(a)
    return d


def _fmt_kinds(arts):
    k = _by_kind(arts)
    return ", ".join(f"{n} {kind}" for kind, n in sorted((kk, len(v)) for kk, v in k.items()))


# ================================================================================ notes-the-next-chat-reads
PID_N = "notes-the-next-chat-reads"


NOTE_PROSE = frozenset({"md", "mdx", "txt", "rst", "org", "adoc"})
LOADED_BY_CLAUDE_CODE = frozenset({"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md"})


def _is_note_path(lt, p):
    """A prose file somebody could leave for the next chat. Not code; not a file Claude Code loads on its own (CLAUDE.md,
    AGENTS.md); nothing under ~/.claude (auto memory and plan files: Claude Code writes those for everyone, on by default)."""
    p = _n(p)
    b = p.rsplit("/", 1)[-1]
    ext = b.rsplit(".", 1)[-1].lower() if "." in b else ""
    if ext not in NOTE_PROSE or b in LOADED_BY_CLAUDE_CODE:
        return False
    return not any(p.startswith(d + "/") for d in {_n(lt.claude), _n(os.path.realpath(lt.claude))})


def _session_start(lt, sid):
    return (lt.sessions.get(sid) or {}).get("t0") or 0


def _first_reads(lt):
    """{(session, path): time of its first Read}: a Write over a file the session had not read creates it (Claude Code refuses a
    Write over an existing file that was not read first)."""
    c = _cache(lt)
    if "firstreads" not in c:
        fr = {}
        for x in _calls(lt, ("Read",), sidechain=True, interactive=False):
            p = _call_path(lt, x)
            if p:
                fr[(x["s"], p)] = min(x["t"], fr.get((x["s"], p), x["t"]))
        c["firstreads"] = fr
    return c["firstreads"]


def _notes(lt):
    """{path: {created, by, edits, readers}} for the notes a session left: a prose file CREATED by a session (a Write with no
    earlier Read of that path in the same session: Claude Code only lets a Write over an existing file after a Read). edits:
    Edit/MultiEdit calls after the creation in interactive sessions; readers: Read calls by other, interactive sessions."""
    c = _cache(lt)
    if "notes" in c:
        return c["notes"]
    first_read, reads = _first_reads(lt), []
    for x in _calls(lt, ("Read",)):
        p = _call_path(lt, x)
        if p:
            reads.append((x["t"], x["s"], p))
    notes = {}
    for x in _calls(lt, ("Write",), sidechain=True, interactive=False):
        p = None if x["err"] else _call_path(lt, x)
        if not p or not _is_note_path(lt, p) or first_read.get((x["s"], p), float("inf")) < x["t"]:
            continue      # not a note, failed, or written over a file the session had read (it already existed)
        if p not in notes or x["t"] < notes[p]["created"]:
            notes[p] = {"created": x["t"], "by": x["s"], "edits": [], "readers": []}
    for x in _calls(lt, ("Edit", "MultiEdit"), sidechain=True):
        p = None if x["err"] else _call_path(lt, x)
        if p in notes and x["t"] > notes[p]["created"]:
            notes[p]["edits"].append(x["t"])
    for t, s, p in reads:
        n = notes.get(p)
        if n and s != n["by"] and _session_start(lt, s) > n["created"]:       # a chat that began after the note was written
            n["readers"].append(t)
    c["notes"] = notes
    return notes


@check(PID_N, "L2", 0)
def first_message_names_file(lt):
    """Sessions whose first typed message carries a `file` attachment (an @-mention Claude Code resolved to a file) that is a note an
    earlier session created (see _notes), not one re-attached after a compaction. An @-mentioned source file or document nobody
    left as a note does not count."""
    typed, notes = _typed_by_session(lt), _notes(lt)
    files, compacts = {}, {}
    for a in _att(lt, "file"):
        files.setdefault(a["s"], []).append(a)
    for sy in lt.systems:
        if sy["subtype"] == "compact_boundary":
            compacts.setdefault(sy["s"], []).append(sy["t"])
    n, ev = 0, []
    for sid, ms in typed.items():
        first, end = ms[0]["t"], (ms[1]["t"] if len(ms) > 1 else float("inf"))
        hit = []
        for a in files.get(sid, []):
            p = _n(_field(a["data"], "filename") or "")
            if p in notes and first - 1 <= a["t"] < end and sid != notes[p]["by"] and _session_start(lt, sid) > notes[p]["created"] \
                    and not any(first <= ct <= a["t"] for ct in compacts.get(sid, [])):
                hit.append(p)
        if hit:
            n += 1
            ev.append(os.path.basename(hit[0]))
    return {"value": n, "evidence": ev[:3], "note": "a note is a prose file an earlier session created; a file the person wrote by hand and points at is not seen, and the attachment is Claude Code's (an @-mention), not a path typed as plain text"}


@fixture(PID_N, "L2", 0)
def _(fx):
    now = NOW
    shop = "~/work/shop"
    old = fx.session(shop, entrypoint="claude-vscode", start=now - 6 * DAY)          # the chat that left the notes
    old.call("Write", {"file_path": fx.p(shop + "/docs/plan.md"), "content": "next: tests"})
    old.call("Write", {"file_path": "C:\\notes\\todo.md", "content": "todo"})
    old.call("Edit", {"file_path": fx.p(shop + "/src/app.ts"), "old_string": "a", "new_string": "b"})       # source edited, no note
    old.call("Write", {"file_path": fx.p(shop + "/CLAUDE.md"), "content": "rules"})                          # /init: Claude Code loads it itself
    old.call("Write", {"file_path": fx.p("~/.claude/projects/-home-dana-work-shop/memory/user.md"), "content": "auto memory"})   # on by default
    old.call("Read", {"file_path": fx.p(shop + "/docs/old.md")}); old.call("Write", {"file_path": fx.p(shop + "/docs/old.md"), "content": "rewritten"})   # existed already
    s = fx.session(shop, entrypoint="claude-vscode")
    s.typed("look at @docs/plan.md and tell me what is left")
    s.attachment("file", filename=fx.p(shop + "/docs/plan.md"), content={"type": "text"}, displayPath="docs/plan.md")        # counts
    d = fx.session(shop, entrypoint="claude-desktop")
    d.typed("fix the failing build")                          # no file on the first message
    d.typed("it is in @docs/plan.md")
    d.attachment("file", filename=fx.p(shop + "/docs/plan.md"), content={"type": "text"}, displayPath="docs/plan.md")   # trap: second message
    w = fx.session(shop, entrypoint="claude-vscode")
    w.typed("see C:\\notes\\todo.md and docs/guide.md")      # trap: a path in plain text, no attachment
    w2 = fx.session(shop, entrypoint="claude-vscode")
    w2.typed("check @C:\\notes\\todo.md")                     # Windows path: counts
    w2.attachment("file", filename="C:\\notes\\todo.md", content={"type": "text"}, displayPath="todo.md")
    for name, fl in (("src/app.ts", "src"), ("CLAUDE.md", "c"), ("docs/old.md", "o"), ("README.md", "r")):   # traps: not notes
        t = fx.session(shop, entrypoint="claude-vscode")
        t.typed("open @" + name)
        t.attachment("file", filename=fx.p(shop + "/" + name), content={"type": "text"}, displayPath=name)
    m = fx.session(shop, entrypoint="claude-vscode")
    m.typed("what did you remember about me? @memory/user.md")
    m.attachment("file", filename=fx.p("~/.claude/projects/-home-dana-work-shop/memory/user.md"), content={"type": "text"}, displayPath="user.md")   # trap: auto memory
    c = fx.session(shop, entrypoint="claude-vscode")
    c.typed("continue the refactor")
    c.system("compact_boundary", content="Conversation compacted")
    c.attachment("file", filename=fx.p(shop + "/docs/plan.md"), content={"type": "text"}, displayPath="docs/plan.md")   # trap: re-attached by compact
    c.typed("go on")
    h = fx.session(shop, entrypoint="sdk-cli")
    h.typed("summarise @docs/plan.md")                        # trap: headless run
    h.attachment("file", filename=fx.p(shop + "/docs/plan.md"), content={"type": "text"}, displayPath="docs/plan.md")
    return {"value": 2}


@check(PID_N, "L3", 0)
def fresh_session_reads_earlier_file(lt):
    """Sessions that, before their 3rd typed message, Read a note an earlier session created (see _notes: a prose file, not code,
    not CLAUDE.md/AGENTS.md, nothing under ~/.claude). Source files an earlier session edited do not count."""
    typed, notes = _typed_by_session(lt), _notes(lt)
    reads = {}
    for c in _calls(lt, ("Read",)):
        p = _call_path(lt, c)
        if p in notes:
            reads.setdefault(c["s"], []).append((c["t"], p))
    n, ev = 0, []
    for sid, ms in typed.items():
        third = ms[2]["t"] if len(ms) >= 3 else float("inf")
        for t, p in reads.get(sid, []):
            if t < third and sid != notes[p]["by"] and _session_start(lt, sid) > notes[p]["created"]:
                n += 1; ev.append(os.path.basename(p)); break
    return {"value": n, "evidence": ev[:3], "note": "a note is a prose file an earlier session created; a note the person wrote by hand, or one saved outside the sessions' tool calls, is not seen"}


@fixture(PID_N, "L3", 0)
def _(fx):
    now = NOW
    shop = "~/work/shop"
    note = fx.p(shop + "/notes/handoff.md")
    a = fx.session(shop, entrypoint="claude-vscode", start=now - 3 * DAY)
    a.typed("write the handoff"); a.call("Write", {"file_path": note, "content": "next: tests"})
    a.call("Write", {"file_path": "C:\\work\\notes\\plan.md", "content": "plan"})
    a.call("Edit", {"file_path": fx.p(shop + "/src/lib.ts"), "old_string": "a", "new_string": "b"})         # source, edited yesterday
    a.call("Write", {"file_path": fx.p("~/.claude/projects/-home-dana-work-shop/memory/user.md"), "content": "x"})   # auto memory
    a.call("Write", {"file_path": fx.p("~/.claude/plans/idea.md"), "content": "x"})                           # plan mode's file
    a.call("Read", {"file_path": fx.p(shop + "/docs/old.md")}); a.call("Write", {"file_path": fx.p(shop + "/docs/old.md"), "content": "x"})   # overwrote a file that existed
    b = fx.session(shop, entrypoint="claude-desktop", start=now - 2 * DAY)
    b.typed("continue"); b.call("Read", {"file_path": note})                  # counts
    c = fx.session(shop, entrypoint="claude-vscode", start=now - 1 * DAY)
    c.typed("one"); c.typed("two"); c.typed("three"); c.call("Read", {"file_path": note})   # trap: after the 3rd message
    d = fx.session(shop, entrypoint="claude-vscode", start=now - 1 * DAY)
    d.typed("read the entry"); d.call("Read", {"file_path": fx.p(shop + "/src/main.ts")})   # trap: nobody wrote it
    d.call("Read", {"file_path": fx.p(shop + "/src/lib.ts")})                                # trap: source an earlier session edited
    d.call("Read", {"file_path": fx.p("~/.claude/projects/-home-dana-work-shop/memory/user.md")})   # trap: auto memory
    d.call("Read", {"file_path": fx.p("~/.claude/plans/idea.md")}); d.call("Read", {"file_path": fx.p(shop + "/docs/old.md")})
    e = fx.session(shop, entrypoint="claude-vscode", start=now - 1 * DAY)
    e.typed("new file"); e.call("Write", {"file_path": fx.p(shop + "/x.md"), "content": "x"}); e.call("Read", {"file_path": fx.p(shop + "/x.md")})   # trap: same session
    h = fx.session(shop, entrypoint="sdk-cli", start=now - 1 * DAY)
    h.typed("read it"); h.call("Read", {"file_path": note})                     # trap: headless run
    g = fx.session(shop, entrypoint="claude-vscode", start=now - 1 * DAY)
    g.typed("resume the plan"); g.call("Read", {"file_path": "C:\\work\\notes\\plan.md"})   # Windows path, counts
    return {"value": 2}


@check(PID_N, "L3", 2)
def notes_edited_in_small_edits(lt):
    """Edit / MultiEdit calls on a note (see _notes) after a later chat had read it: the agent updating notes the next chat uses, in
    small edits. Auto memory under ~/.claude and CLAUDE.md/AGENTS.md, which Claude Code keeps for everyone, are not notes here."""
    kept = {p: n for p, n in _notes(lt).items() if n["readers"]}
    edits = sum(1 for n in kept.values() for t in n["edits"] if t > min(n["readers"]))
    return {"value": edits, "evidence": [f"{edits} small edits to {len(kept)} notes that a later chat read"],
            "note": "a note is a prose file a session created; notes kept by hand, in a store Claude Code loads on its own, or saved outside the tool calls are not seen"}


@fixture(PID_N, "L3", 2)
def _(fx):
    now = NOW
    shop = "~/work/shop"
    note, mem = fx.p(shop + "/notes/progress.md"), fx.p("~/.claude/projects/-home-dana-work-shop/memory/feedback.md")
    a = fx.session(shop, entrypoint="claude-vscode", start=now - 4 * DAY)
    a.call("Write", {"file_path": note, "content": "step 1"}); a.call("Edit", {"file_path": note, "old_string": "1", "new_string": "2"})   # trap: before any later chat read it
    a.call("Write", {"file_path": fx.p(shop + "/notes/unread.md"), "content": "x"}); a.call("Edit", {"file_path": fx.p(shop + "/notes/unread.md"), "old_string": "x", "new_string": "y"})
    a.call("Write", {"file_path": mem, "content": "m"})
    b = fx.session(shop, entrypoint="claude-desktop", start=now - 3 * DAY)
    b.typed("continue"); b.call("Read", {"file_path": note}); b.call("Read", {"file_path": mem})
    b.call("Edit", {"file_path": note, "old_string": "2", "new_string": "3"})                                       # counts
    b.call("Edit", {"file_path": mem, "old_string": "m", "new_string": "n"})                                        # trap: auto memory
    b.call("Edit", {"file_path": fx.p(shop + "/src/memory.ts"), "old_string": "a", "new_string": "b"})              # trap: name only
    v = fx.session(shop, entrypoint="claude-vscode", start=now - 2 * DAY)
    v.call("MultiEdit", {"file_path": note, "edits": []})                                                           # counts
    h = fx.session(shop, entrypoint="sdk-cli", start=now - 1 * DAY)
    h.call("Edit", {"file_path": note, "old_string": "3", "new_string": "4"})                                       # trap: headless
    return {"value": 2}


@check(PID_N, "L3", 3)
def notes_retired(lt):
    """Stores of notes (the folders of the notes sessions created, in a repo of the person's) that the person's commits in the window
    shrank: they deleted at least one note and added no more than they deleted. A rename or move is not retiring (a folder
    reorganisation renames every note), and a store that gained more than it lost is piling up."""
    roots = {}
    for p, n in _notes(lt).items():
        root = os.path.realpath(os.path.dirname(p))
        repo = lt.repo_of(root)
        if repo:
            roots.setdefault(repo, set()).add(os.path.relpath(root, repo).replace(os.sep, "/"))
    shrinking, ev = 0, []
    for repo, rels in roots.items():
        stat = {r: [0, 0] for r in rels}          # store -> [deleted, added] by the person's commits
        for cm in _git_commits(lt, repo):
            if not cm["mine"]:
                continue
            for st, path, old in cm["files"]:
                path = _n(path)
                if st not in "DA" or path.rsplit(".", 1)[-1].lower() not in NOTE_PROSE:
                    continue
                for r in rels:
                    if (r == "." and "/" not in path) or (r != "." and path.startswith(r + "/")):
                        stat[r][st == "A"] += 1
        for r, (gone, added) in stat.items():
            if gone and added <= gone:
                shrinking += 1; ev.append(f"{gone} deleted, {added} added in {r}")
    return {"value": shrinking, "evidence": ev[:3], "note": "only stores inside a git repo the person works in; notes merged by editing, and settled notes left in place, stay unseen"}


@fixture(PID_N, "L3", 3)
def _(fx):
    r = "~/work/shop"
    body = lambda n: (f"{n} " + "".join(chr(97 + (i * 7 + ord(n[0])) % 26) for i in range(300)))   # unlike each other: git sees a delete, not a rename
    files = {f"{d}/{n}.md": body(n) for d, names in (("notes", "abcd"), ("handoff", "efij"), ("archive", "gh"), ("team", "t")) for n in names}
    fx.repo(r, files | {"src/x.ts": "export const x = 1"}, by="teammate")
    s = fx.session(r, entrypoint="claude-vscode", start=NOW - 5 * DAY)
    for rel in files:
        s.call("Write", {"file_path": fx.p(r + "/" + rel), "content": "note"})
    for n in "ab":
        os.remove(fx.p(r + f"/notes/{n}.md"))
    fx.commit(r, {}, by="work", message="retire a and b")                                                          # counts: 2 deleted, 0 added
    os.remove(fx.p(r + "/handoff/e.md"))
    fx.commit(r, {f"handoff/new{k}.md": body("new" + str(k) + "x") for k in range(3)}, by="work", message="swap e for three")   # trap: piling up (1 gone, 3 added)
    for n in "gh":
        os.rename(fx.p(r + f"/archive/{n}.md"), fx.p(r + f"/archive/{n}-merged.md"))
    fx.commit(r, {}, by="work", message="reorganise archive")                                                      # trap: renames
    os.remove(fx.p(r + "/team/t.md")); fx.commit(r, {}, by="teammate", message="teammate drops t")                 # trap: not theirs
    os.remove(fx.p(r + "/src/x.ts")); fx.commit(r, {}, by="work", message="drop code")                              # trap: not a note
    return {"value": 1}


@check(PID_N, "L4", 1)
def hook_blocks(lt):
    return {"value": None, "evidence": [], "note": "can't tell from a laptop: Claude Code records that a hook blocked a step, never whether the hook held the work until a record was written; the logs show lint, security and format guards the same way"}


@fixture(PID_N, "L4", 1)
def _(fx):
    s = fx.session("~/work/shop", entrypoint="claude-vscode")
    s.attachment("hook_blocking_error", hookName="Stop", toolUseID="t1", hookEvent="Stop", blockingError={"blockingError": "write the log first", "command": "./check.sh"})
    s.attachment("hook_blocking_error", hookName="PostToolUse:Write", toolUseID="t2", hookEvent="PostToolUse", blockingError={"blockingError": "no frontmatter", "command": "./lint.sh"})
    return {"value": None}


@check(PID_N, "L4", 2)
def check_reads_instruction_files(lt):
    return {"value": None, "evidence": [], "note": "can't tell from a laptop: a declared step, script or test that names CLAUDE.md / AGENTS.md / MEMORY.md may copy, list or lint them; whether it fails when a note goes stale is in no file a laptop holds"}


@fixture(PID_N, "L4", 2)
def _(fx):
    fx.repo("~/work/docs", {"package.json": json.dumps({"scripts": {"check:docs": "node scripts/verify.js"}}), "CLAUDE.md": "rules",
                            "scripts/verify.js": "const t = require('fs').readFileSync('CLAUDE.md', 'utf8');"})
    fx.session("~/work/docs", entrypoint="claude-vscode").typed("hi")
    return {"value": None}


# ---------------------------------------------------------------------------- one stranger who built things for the agent
import contextlib


@contextlib.contextmanager
def _commit_clock(t):
    """Commits made inside this block are dated t (epoch seconds): git reads GIT_AUTHOR_DATE / GIT_COMMITTER_DATE from the environment
    the fixture passes on. The fixture's own commit() dates every commit now."""
    keys = ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")
    old = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ[k] = f"{int(t)} +0000"
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _world(fx):
    """A stranger's repos with everything the roll-call and reword checks look at. Returns the paths used.
    kit (the person, home email): plugin + marketplace manifests, skill lint-fix, agents checker/writer/planner, command ship,
    hook script stop-guard.sh (wired as a Stop hook in user settings), MCP server file db-server.mjs (+ an npx server),
    traps: skill pdf listed in skills-lock.json, skill deploy identical to an installed plugin copy, a vendored plugin whose
    manifest names another author, a skill and an agent a teammate wrote. scratch: skill idea (not loaded anywhere) and
    .claude/skills/active (loaded)."""
    kit, scratch = "~/code/kit", "~/code/scratch"
    deploy = "---\nname: deploy\ndescription: ship it\n---\nRun the deploy.\n"
    fx.file("~/.claude/plugins/cache/mkt/pl/1.0/skills/deploy/SKILL.md", deploy)
    fx.repo(kit, {
        ".claude-plugin/plugin.json": json.dumps({"name": "kit", "author": {"name": "Dana Stranger"}}),
        ".claude-plugin/marketplace.json": json.dumps({"name": "kit-market", "owner": {"name": "Dana Stranger"}, "plugins": []}),
        "skills/lint-fix/SKILL.md": "---\nname: lint-fix\ndescription: fix lint\n---\nfix lint\n",
        "agents/checker.md": "---\nname: checker\ndescription: looks at a diff\ntools: Read, Grep\nmodel: haiku\n---\ncheck\n",
        "agents/writer.md": "---\nname: writer\ndescription: writes\ntools: Read, Write\nmodel: sonnet\n---\nwrite\n",
        "agents/planner.md": "---\nname: planner\ndescription: plans\n---\nplan\n",
        "commands/ship.md": "ship it\n",
        "hooks/stop-guard.sh": "#!/bin/sh\nexit 0\n",
        "tools/db-server.mjs": "// an MCP server\n",
        ".mcp.json": json.dumps({"mcpServers": {"kit-db": {"command": "node", "args": ["tools/db-server.mjs"]},
                                                 "web": {"command": "npx", "args": ["-y", "some-mcp-server"]}}}),
        "skills/pdf/SKILL.md": "---\nname: pdf\ndescription: pdfs\n---\npdf\n",
        "skills-lock.json": json.dumps({"version": 1, "skills": {"pdf": {"source": "vendor/pdf", "computedHash": "abc123"}}}),
        "skills/deploy/SKILL.md": deploy,
        "vendor/foreign/.claude-plugin/plugin.json": json.dumps({"name": "foreign", "author": {"name": "Someone Else", "email": "se@other.example"}}),
    }, by="home")
    fx.commit(kit, {"skills/team-skill/SKILL.md": "---\nname: team-skill\n---\nx\n",
                    "agents/team-agent.md": "---\nname: team-agent\ntools: Read\nmodel: opus\n---\nx\n"}, by="teammate")
    fx.repo(scratch, {"skills/idea/SKILL.md": "---\nname: idea\n---\nx\n", ".claude/skills/active/SKILL.md": "---\nname: active\n---\nx\n"}, by="home")
    fx.settings("user", {"enabledPlugins": {"kit@kit-market": True},
                         "hooks": {"Stop": [{"hooks": [{"type": "command", "command": fx.p(kit + "/hooks/stop-guard.sh")}]}]}})
    fx.session(kit, entrypoint="claude-vscode").typed("hi")
    fx.session(scratch, entrypoint="claude-desktop").typed("hi")
    return {"kit": kit, "scratch": scratch}


# ================================================================================ reword-the-ask
PID_R = "reword-the-ask"


def _commands(lt, names):
    return [c for c in lt.all_commands() if c["name"] in names and lt.interactive(c["s"])]


# ---- reword-the-ask L2#6
@check(PID_R, "L2", 6)
def fork_or_rewind(lt):
    """/rewind, /undo, /checkpoint and /branch typed in any interactive entrypoint (transcript record, or terminal history when the
    transcript lacks it): going back to an earlier point, or branching from it. /fork is not counted: in 2.1.318 it starts a
    background copy of the conversation for side work. The Esc-Esc rewind shortcut leaves no record: this is a floor."""
    cs = _commands(lt, ("/rewind", "/undo", "/checkpoint", "/branch"))
    per = {}
    for c in cs:
        per[c["name"]] = per.get(c["name"], 0) + 1
    return {"value": len(cs), "evidence": [f"{k} x{v}" for k, v in sorted(per.items())][:3],
            "note": "Esc-Esc rewind is not written anywhere; 'before the wrong turn' is not read, only that a rewind or branch happened"}


@fixture(PID_R, "L2", 6)
def _(fx):
    fx.session("~/work/shop", entrypoint="claude-desktop").command("/rewind")
    fx.session("~/work/shop", entrypoint="claude-vscode").command("/branch", "try-b")
    t = fx.session("~/work/shop", entrypoint="cli")                   # terminal: transcript and history.jsonl both hold it
    t.command("/checkpoint"); t.command("/undo")
    t.command("/fork")                                                # trap: a background copy for side work, not going back
    t.command("/forklift", "deploy")                                  # trap: another command with the same start
    t.typed("please /rewind the change in a.ts")                      # trap: a word in a sentence
    fx.session("~/work/shop", entrypoint="sdk-cli").command("/rewind")  # trap: headless run
    return {"value": 4}


@check(PID_R, "L3", 0)
def own_prompt_files(lt):
    """Skill, agent and command files that are the person's by the one ownership rule (lt.owner: not installed, not most of the
    commits someone else's; committed or not), left out when listed in a lock file or identical to an installed plugin's copy."""
    own = [a for a in _artifacts(lt) if a["own"] and a["kind"] in ("skill", "agent", "command")]
    return {"value": len(own), "evidence": [_fmt_kinds(own)] if own else [],
            "note": "a file nobody committed is theirs unless a receipt says installed (ruling 11-05); a vendored copy edited after copying reads as theirs"}


@fixture(PID_R, "L3", 0)
def _(fx):
    _world(fx)
    return {"value": 7}     # skills lint-fix, idea, active; agents checker, writer, planner; command ship


PROMPT_FILE = re.compile(r"(^|/)SKILL\.md$|(^|/)agents/[^/]+\.md$|(^|/)commands/[^/]+\.md$")


REWRITE_MIN_LINES = 3        # a commit rewrites a prompt file when it removes at least this many lines of it and adds at least this many
REWRITE_MAX_FILES = 5        # ... and touches at most this many prompt files (more is a sweep: a rename, a reformat, a re-template)


def _prompt_first_use(lt, own):
    """{path of an own prompt file: epoch of the first time any session used it}: a Skill call naming the skill or command, an Agent
    call naming the agent, a skill_mention attachment (the person typed /name in a message), a typed /plugin:name. A call `x:name`
    is a thing of plugin x; a bare name is a user or project thing (the same reading as built_and_used). Reading or editing the file
    is not use. Headless runs count: the file was used."""
    ids = {}
    for a in own:
        ns = _artifact_ns(lt, a) or ""
        for x in (a["name"], a.get("alias")):
            if x:
                ids.setdefault(("a" if a["kind"] == "agent" else "s", ns, x), set()).add(a["path"])
    first = {}

    def seen(key, t):
        for p in ids.get(key, ()):
            if t < first.get(p, float("inf")):
                first[p] = t
    for c in _calls(lt, ("Skill",), sidechain=True, interactive=False):
        ns, short = _split_call(_jin(c).get("skill"))
        seen(("s", ns, short), c["t"])
    for c in _calls(lt, ("Agent", "Task"), sidechain=True, interactive=False):
        ns, short = _split_call(_jin(c).get("subagent_type"))
        seen(("a", ns, short), c["t"])
    for at in _att(lt, "skill_mention", interactive=False):
        ns, short = _split_call(_field(at["data"], "skillName"))
        seen(("s", ns, short), at["t"])
    for c in lt.all_commands():
        ns, short = _split_call(c["name"].lstrip("/"))
        if ns:
            seen(("s", ns, short), c["t"])
    return first


def _prompt_numstat(lt):
    """Per repo, the window's commits that touch a prompt file, by author time, with lines added and removed per file (whitespace ignored). Asked of every
    repo at once and kept: it is the slow part of prompts_rewritten."""
    c = _cache(lt)
    if "prompt_numstat" not in c:
        lt.repos; lt.identity        # read before any thread starts (identity swaps HOME while it reads)
        from concurrent.futures import ThreadPoolExecutor

        def numstat(r):
            return lt.git("-c", "core.quotepath=false", "log", "--no-merges", "--full-history", "--no-renames", "-w", f"--since={_since(lt)}",
                          "--numstat", "--format=@@%at|%an <%ae>", "--", ":(glob)**/SKILL.md", ":(glob)**/agents/*.md", ":(glob)**/commands/*.md", cwd=r)
        with ThreadPoolExecutor(8) as ex:    # git runs in its own process: threads overlap the waiting
            c["prompt_numstat"] = list(ex.map(numstat, lt.repos))
    return c["prompt_numstat"]


@check(PID_R, "L3", 1)
def prompts_rewritten(lt):
    """Own prompt files (SKILL.md, agents/*.md, commands/*.md; _artifacts: the one ownership rule, not installed, vendored or listed in
    a lock file) whose instructions the person changed substantively after a session had used the file: one of their commits in the
    window removes >= 3 lines of it and adds >= 3 (whitespace ignored, so a re-indent is nothing), touches <= 5 prompt files (a
    sweep, a rename or a reformat across many files is not rewording one), and its AUTHOR time (%at: when the person wrote it; a rebase or cherry-pick moves the committer time, not this) comes after the first
    Skill / Agent call or skill_mention that named the file. A typo, an append and a rewrite before first use are not counted. Value = files."""
    own = [a for a in _artifacts(lt) if a["own"] and a["kind"] in ("skill", "agent", "command")]
    own_paths = {a["path"] for a in own}
    first = _prompt_first_use(lt, own)
    logs = _prompt_numstat(lt)
    hits = {}      # file -> commits that rewrote it after it was used
    sweeps = 0
    for repo, raw in zip(lt.repos, logs):
        commits, cur = [], None
        for line in raw.splitlines():
            if line.startswith("@@"):
                t, _, au = line[2:].partition("|")
                cur = {"t": int(t) if t.isdigit() else 0, "mine": lt.is_mine(au), "files": []}
                commits.append(cur)
                continue
            p = line.split("\t")
            if cur is not None and len(p) == 3 and PROMPT_FILE.search(_n(p[2])) and p[0].isdigit() and p[1].isdigit():
                cur["files"].append((os.path.realpath(os.path.join(repo, p[2])), int(p[0]), int(p[1])))
        for cm in commits:
            if not cm["mine"]:
                continue
            if len(cm["files"]) > REWRITE_MAX_FILES:
                sweeps += 1
                continue
            for rp, added, removed in cm["files"]:
                if rp in own_paths and min(added, removed) >= REWRITE_MIN_LINES and cm["t"] > first.get(rp, float("inf")):
                    hits[rp] = hits.get(rp, 0) + 1
    return {"value": len(hits), "evidence": [f"{len(hits)} of {len(own_paths)} own prompt files rewritten after a session used them",
                                             f"{sum(hits.values())} rewriting commits; {len(first)} files used in a session; {sweeps} sweeping commits left out"],
            "note": "a file's use is a Skill / Agent call, a skill_mention or a typed /plugin:name; a command typed bare (/name) is not matched, "
                    "so a command that is only ever typed reads as never used; commits made by an agent under the person's identity count as theirs"}


@fixture(PID_R, "L3", 1)
def _(fx):
    r, now = "~/work/shop", NOW
    day = lambda k: now - k * DAY
    body = lambda tag, n=6: "".join(f"{tag}{i} rule\n" for i in range(n))
    sweep = [f"skills/v{i}/SKILL.md" for i in range(6)]
    files = {"skills/s/SKILL.md": body("s"), "agents/a.md": body("a"), "skills/t/SKILL.md": body("t"), "skills/early/SKILL.md": body("e"),
             "skills/never/SKILL.md": body("n"), "skills/rb/SKILL.md": body("rb"), "skills/ws/SKILL.md": body("w"), "skills/pdf/SKILL.md": body("p"), "commands/c.md": body("c"),
             "README.md": "r1\nr2\n", "skills-lock.json": json.dumps({"version": 1, "skills": {"pdf": {"source": "o/r", "computedHash": "h"}}})}
    files.update({f: body(f.split("/")[1]) for f in sweep})
    with _commit_clock(day(25)):
        fx.repo(r, files, by="work")
    with _commit_clock(day(20)):
        fx.commit(r, {"skills/early/SKILL.md": body("early-new")}, by="work")            # trap: reworded before any session used it
    with _commit_clock(day(15)):
        os.environ["GIT_COMMITTER_DATE"] = f"{int(day(6))} +0000"      # written on day 15, before first use; "rebased" on day 6, after it
        fx.commit(r, {"skills/rb/SKILL.md": body("rb-new")}, by="work")                  # trap: a rebase moved the committer time past first use
    s = fx.session(r, entrypoint="claude-vscode", start=day(10))
    for name in ("s", "t", "early", "rb", "ws", "pdf", "c", *[f"v{i}" for i in range(6)]):      # "never" is never used
        s.call("Skill", {"skill": name}, "ok")
    s.call("Agent", {"subagent_type": "a", "description": "d", "prompt": "p"}, "done")
    with _commit_clock(day(6)):
        fx.commit(r, {"skills/s/SKILL.md": "s0 rule\ns1 rule\n" + body("s-new", 4)}, by="work")             # counts: 4 lines replaced after use
        fx.commit(r, {"agents/a.md": "a0 rule\na1 rule\na2 rule\n" + body("a-new", 3)}, by="work")           # counts: an agent file, 3 lines replaced
        fx.commit(r, {"skills/t/SKILL.md": body("t").replace("t2 rule", "t2 rulee")}, by="work")            # trap: a typo, 1 line
        fx.commit(r, {"skills/t/SKILL.md": body("t").replace("t2 rule", "t2 rulee").replace("t3 rule", "t3 rulee")}, by="work")   # trap: another one
        fx.commit(r, {"skills/never/SKILL.md": body("never-new")}, by="work")                              # trap: rewritten, never used
        fx.commit(r, {"skills/ws/SKILL.md": "".join("  " + l for l in body("w").splitlines(True))}, by="work")   # trap: re-indented, same words
        fx.commit(r, {"skills/pdf/SKILL.md": body("pdf-new")}, by="work")                                   # trap: vendored (lock file)
        fx.commit(r, {"commands/c.md": body("c-new")}, by="teammate")                                      # trap: a teammate's rewrite
        fx.commit(r, {f: body("sweep-new") for f in sweep}, by="work")                                      # trap: one reformat of 6 prompt files
        fx.commit(r, {"README.md": "r1\nnew\nmore\nlines\n"}, by="work")                                    # trap: not a prompt file
    return {"value": 2}


PROMPT_EDIT = re.compile(r"/(SKILL|CLAUDE|CLAUDE\.local|AGENTS)\.md$|/agents/[^/]+\.md$|/commands/[^/]+\.md$")


@check(PID_R, "L3", 2)
def agent_edits_prompt_files(lt):
    """Edit / MultiEdit calls, and Writes over a file the session had read, on SKILL.md, agents/*.md, commands/*.md, CLAUDE.md,
    AGENTS.md that are the person's by the one ownership rule (lt.owner, via _yours_of: committed or not; not installed per
    Claude Code's records or a skills lock, not most commits someone else's). A skills lock file a repo holds is an extra receipt. A Write that creates the file (what /init does) is Claude Code's default."""
    first = _first_reads(lt)
    _, locked = _installed_index(lt)
    cand = []
    for c in _calls(lt, ("Edit", "Write", "MultiEdit"), sidechain=True):
        p = None if c["err"] else _call_path(lt, c)
        if not p or not PROMPT_EDIT.search("/" + p.lstrip("/")):
            continue
        if c["name"] == "Write" and first.get((c["s"], p), float("inf")) >= c["t"]:
            continue
        if os.path.basename(p) == "SKILL.md" and os.path.basename(os.path.dirname(p)) in locked:
            continue
        cand.append((c, p))
    yours = _yours_of(lt, sorted({p for _, p in cand if os.path.exists(p)}))
    hit = [p for _, p in cand if yours.get(p)]
    return {"value": len(hit), "evidence": [os.path.basename(p) for p in hit[:3]],
            "note": "an agent edit of a file the person wrote; a file nobody committed is theirs unless a receipt says installed (ruling 11-05)"}


@fixture(PID_R, "L3", 2)
def _(fx):
    shop, fresh, team = "~/work/shop", "~/work/fresh", "~/work/team"
    fx.repo(shop, {"agents/rev.md": "r", "CLAUDE.md": "c", "skills/x/SKILL.md": "x", "commands/go.md": "g", "skills/y/SKILL.md": "y",
                   "skills/pdf/SKILL.md": "p", "README.md": "r", "src/agents/index.ts": "i",
                   "skills-lock.json": json.dumps({"version": 1, "skills": {"pdf": {"source": "o/r", "computedHash": "h"}}})}, by="work")
    fx.repo(team, {".claude/agents/t.md": "t"}, by="teammate")
    fx.file("~/.claude/plugins/cache/mkt/pl/1.0/skills/deploy/SKILL.md", "deploy")
    # the receipts Claude Code keeps for a plugin installed from a published marketplace: installed_plugins.json, known_marketplaces.json
    fx.file("~/.claude/plugins/installed_plugins.json", json.dumps({"version": 2, "plugins": {
        "pl@mkt": [{"scope": "user", "version": "1.0", "installPath": fx.p("~/.claude/plugins/cache/mkt/pl/1.0")}]}}))
    fx.file("~/.claude/plugins/known_marketplaces.json", json.dumps({"mkt": {"source": {"source": "github", "repo": "acme/mkt"}}}))
    s = fx.session(shop, entrypoint="claude-vscode")
    s.call("Edit", {"file_path": fx.p(shop + "/agents/rev.md"), "old_string": "a", "new_string": "b"})                 # counts
    s.call("Edit", {"file_path": fx.p(shop + "/CLAUDE.md"), "old_string": "a", "new_string": "b"})                      # counts
    s.call("Read", {"file_path": fx.p(shop + "/skills/x/SKILL.md")})
    s.call("Write", {"file_path": fx.p(shop + "/skills/x/SKILL.md"), "content": "x2"})                                  # counts: written over a file it had read
    s.call("MultiEdit", {"file_path": fx.p(shop + "/commands/go.md"), "edits": []})                                     # counts
    s.call("Read", {"file_path": fx.p(shop + "/README.md")})                                                           # trap: a read
    s.call("Edit", {"file_path": fx.p(shop + "/README.md"), "old_string": "a", "new_string": "b"})                      # trap: not a prompt file
    s.call("Edit", {"file_path": fx.p(shop + "/src/agents/index.ts"), "old_string": "a", "new_string": "b"})            # trap
    s.call("Edit", {"file_path": fx.p(shop + "/skills/pdf/SKILL.md"), "old_string": "a", "new_string": "b"})            # trap: vendored, in a lock file
    s.call("Edit", {"file_path": fx.p(team + "/.claude/agents/t.md"), "old_string": "a", "new_string": "b"})            # trap: a teammate's file
    s.call("Edit", {"file_path": fx.p("~/.claude/plugins/cache/mkt/pl/1.0/skills/deploy/SKILL.md"), "old_string": "a", "new_string": "b"})   # trap: installed (marketplace receipt above)
    s.call("Write", {"file_path": fx.p(fresh + "/CLAUDE.md"), "content": "made by /init"})                              # trap: created, not reworded
    s.subagent("general-purpose", "tidy the skill", calls=[("Edit", {"file_path": fx.p(shop + "/skills/y/SKILL.md"), "old_string": "a", "new_string": "b"}, "ok")])   # counts
    fx.repo(fresh, {"CLAUDE.md": "made by /init"}, by="work")                                                           # committed afterwards: it is theirs, still a creation
    h = fx.session(shop, entrypoint="sdk-cli")
    h.call("Edit", {"file_path": fx.p(shop + "/CLAUDE.md"), "old_string": "a", "new_string": "b"})                      # trap: headless
    return {"value": 5}


def _eval_folders(lt):
    """{repo: {evals folder: [case files]}} for tracked case.yaml files under an `evals` folder that the person wrote."""
    c = _cache(lt)
    if "evals" in c:
        return c["evals"]
    out = {}
    for r in lt.repos:
        for rel in _tracked(lt, r):
            parts = _n(rel).split("/")
            if parts[-1] in ("case.yaml", "case.yml") and "evals" in parts[:-1]:
                folder = "/".join(parts[:parts.index("evals") + 1])
                if lt.authorship(os.path.join(r, rel))["yours"]:
                    out.setdefault(r, {}).setdefault(folder, []).append(rel)
    c["evals"] = out
    return out


@check(PID_R, "L4", 0)
def eval_folder_exists(lt):
    """`evals/` folders (Claude Code's plugin-eval layout: case.yaml inside) in repos where git says the person wrote the case
    files. Counted per evals folder, any folder name of the repo's own."""
    folders = [(r, f) for r, d in _eval_folders(lt).items() for f in d]
    return {"value": len(folders), "evidence": [f"{len(folders)} evals folders in {len({r for r, _ in folders})} repos"] if folders else [],
            "note": "evals kept in another layout (no case.yaml) are not seen"}


@fixture(PID_R, "L4", 0)
def _(fx):
    fx.repo("~/code/playground", {"plugins/p1/evals/c1/case.yaml": "name: a", "plugins/p1/evals/c2/case.yaml": "name: b",
                                 "plugins/p2/evals/x/case.yaml": "name: c", "eval/notes.md": "n", "eval/a.txt": "a"}, by="home")
    fx.repo("~/work/team", {"evals/t/case.yaml": "name: t"}, by="teammate")        # trap: a teammate's
    for p in ("~/code/playground", "~/work/team"):
        fx.session(p, entrypoint="claude-vscode").typed("hi")
    return {"value": 2}


CHANGE_TRIGGER = re.compile(r"\b(push|pull_request|pull_request_target)\b")
EVAL_RUN = re.compile(r"claude\s+plugin\s+eval")


def _ci_run_lines(text):
    """The shell lines of a workflow's `run:` steps (a one-line value or an indented block), and the text under its `on:` key."""
    lines = text.splitlines()
    runs, i = [], 0
    while i < len(lines):
        m = re.match(r"^(\s*)(?:-\s*)?run:\s*(.*)$", lines[i])
        if m:
            indent, val = len(m.group(1)), m.group(2).strip()
            if val and val[0] not in "|>":
                runs.append(val)
            else:
                j = i + 1
                while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent):
                    runs.append(lines[j].strip()); j += 1
                i = j - 1
        i += 1
    on = re.search(r"(?ms)^[\"']?on[\"']?\s*:(.*?)(?=^\S)", text + "\nend:")
    return runs, on.group(1) if on else ""


def _auto_steps(lt, r):
    """[(label, command text)] of the steps a repo declares that fire by themselves when a change lands: the `run:` steps of a CI
    workflow triggered by push or pull_request, a pre-commit entry, a git hook script (comment lines left out)."""
    out = []
    for label, text in _decl(lt, r):
        if label.startswith("CI workflow "):
            runs, trigger = _ci_run_lines(text)
            if CHANGE_TRIGGER.search(trigger):
                out += [(label, x) for x in runs]
        elif label == ".pre-commit-config.yaml":
            out += [(label, m) for m in re.findall(r"^\s*entry:\s*(.+)$", text, re.M)]
        elif label.startswith("git hook "):
            out += [(label, x) for x in text.splitlines() if x.strip() and not x.lstrip().startswith("#")]
    return out


@check(PID_R, "L4", 2)
def evals_rerun_declared(lt):
    """Steps a repo declares that run by themselves on a change (CI workflow on push / pull_request, pre-commit, git hook) and run
    `claude plugin eval` or name one of the repo's own evals folders by its whole path. A package.json script or Makefile target
    is run by hand, and a name in a comment or in a workflow's name is not a step: neither counts."""
    steps, ev = 0, []
    folders = _eval_folders(lt)
    for r in lt.repos:
        refs = [re.compile(r"(?<![\w./-])" + re.escape(f) + r"(?![\w-])") for f in folders.get(r, {})]
        for label, cmd in _auto_steps(lt, r):
            if EVAL_RUN.search(cmd) or any(x.search(cmd) for x in refs):
                steps += 1; ev.append(label[:50])
    manual = sum(1 for c in _calls(lt, ("Bash",)) if EVAL_RUN.search(_cmd(c)))
    return {"value": steps, "evidence": ev[:2] + [f"{manual} manual runs in sessions"],
            "note": "a step that fires on every push shows the evals rerun when a prompt or the model changes; a paths filter naming the prompt files is not read"}


@fixture(PID_R, "L4", 2)
def _(fx):
    fx.repo("~/code/kit", {".github/workflows/evals.yml": "name: evals\non:\n  push:\n    branches: [main]\njobs:\n  e:\n    steps:\n      - run: claude plugin eval plugins/p1\n",
                           "plugins/p1/evals/x/case.yaml": "n: 1"}, by="home")                                  # counts: CI on push
    fx.repo("~/code/lab", {".husky/pre-commit": "#!/bin/sh\nnode run-evals.mjs plugins/q/evals\n", "plugins/q/evals/y/case.yaml": "n: 1"}, by="home")   # counts: a git hook
    fx.repo("~/code/manual", {"package.json": json.dumps({"scripts": {"evals": "node run-evals.mjs plugins/m/evals"}}),
                              "plugins/m/evals/y/case.yaml": "n: 1"}, by="home")                                    # trap: a script run by hand
    fx.repo("~/code/nightly", {".github/workflows/n.yml": "name: n\non:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n  e:\n    steps:\n      - run: claude plugin eval plugins/n\n"}, by="home")   # trap: not on a change
    fx.repo("~/code/chatty", {".github/workflows/c.yml": "name: run the evals\non: [push]\njobs:\n  b:\n    steps:\n      # plugins/c/evals\n      - run: npm test\n",
                              "plugins/c/evals/w/case.yaml": "n: 1"}, by="home")                                  # trap: the folder named only in a name and a comment
    fx.repo("~/code/quiet", {".github/workflows/ci.yml": "on: push\njobs:\n  b:\n    steps:\n      - run: npm test\n", "evals/z/case.yaml": "n: 1"}, by="home")   # trap: evals exist, nothing runs them
    for p in ("~/code/kit", "~/code/lab", "~/code/manual", "~/code/nightly", "~/code/chatty", "~/code/quiet"):
        s = fx.session(p, entrypoint="claude-vscode")
        s.typed("hi")
    s.call("Bash", {"command": "claude plugin eval --help"}, "usage")      # trap: a manual run
    return {"value": 2}


# ================================================================================ built-for-the-agent-roll-call
PID_B = "built-for-the-agent-roll-call"


@check(PID_B, "L4", 0)
def built_by_you(lt):
    """Things made for the agent that are the person's: plugin / marketplace manifests, skills, agents, commands, hook scripts
    and MCP server files. Installed or copied ones (plugin cache, lock file, same content as an installed file) are first set
    aside, then whose it is by the one ownership rule, then the manifest's author against the person's identity."""
    own = [a for a in _artifacts(lt) if a["own"]]
    return {"value": len(own), "evidence": [_fmt_kinds(own)] if own else [],
            "note": "an MCP server run through npx/uvx has no file of theirs; a server script nobody committed is theirs (ruling 11-05)"}


@fixture(PID_B, "L4", 0)
def _(fx):
    _world(fx)
    return {"value": 11}


@check(PID_B, "L4", 1)
def built_and_wired(lt):
    """Of the person's own things, those Claude Code actually loads: a plugin or marketplace that is enabled/registered in
    settings, a skill/agent/command under .claude/ or in an enabled own plugin, a hook in a settings file, an MCP server in .mcp.json."""
    own = [a for a in _artifacts(lt) if a["own"]]
    on = [a for a in own if a["wired"]]
    return {"value": len(on), "evidence": [f"{len(on)} of {len(own)} own things are loaded", _fmt_kinds([a for a in own if not a["wired"]]) + " not loaded"],
            "note": "present and loaded is all that is read; a file kept only in a plugin repo is 'loaded' only while that plugin is enabled"}


@fixture(PID_B, "L4", 1)
def _(fx):
    _world(fx)
    return {"value": 9}       # all 11 own except the marketplace (not registered) and scratch's skill idea (not under .claude, no plugin)


def _artifact_ns(lt, a):
    """The plugin namespace a skill / agent / command is called by (`plugin:name`), None when it is a user or project one (anything under
    a .claude folder, or outside a plugin)."""
    p = _n(a["path"])
    if "/.claude/" in p:
        return None
    root = _plugin_root(lt, a["path"], a.get("repo"))
    return _plugin_name(root) if root else None


def _own_ids(lt, own, kind):
    """{(namespace or '', name)} the person's things of this kind are called by: its file name and its front-matter name."""
    return {(_artifact_ns(lt, a) or "", x) for a in own if a["kind"] == kind for x in (a["name"], a.get("alias")) if x}


def _split_call(name):
    ns, _, short = (name or "").rpartition(":")
    return ns, short


@check(PID_B, "L4", 2)
def built_and_used(lt):
    """Own things that fired or were called in the window, one per thing: Skill / Agent calls, mcp__<server>__ tool calls, slash commands
    typed, hook runs (hook_success attachments naming the script). A call `x:name` counts only for a thing of plugin x: another
    plugin's `x:deploy` is not their `deploy`. A bare name counts for their user or project things only; a typed bare `/name`
    is never matched (a Claude Code built-in such as /review looks the same), a typed `/plugin:name` is."""
    own = [a for a in _artifacts(lt) if a["own"]]
    used = set()
    ids = {k: _own_ids(lt, own, k) for k in ("skill", "agent", "command")}
    mc = {a["name"] for a in own if a["kind"] == "mcp"}
    for c in _calls(lt, ("Skill",), sidechain=True):
        ns, short = _split_call(_jin(c).get("skill"))
        if (ns, short) in ids["skill"] or (ns, short) in ids["command"]:
            used.add(("skill", ns, short))
    for c in _calls(lt, ("Agent", "Task"), sidechain=True):
        ns, short = _split_call(_jin(c).get("subagent_type"))
        if (ns, short) in ids["agent"]:
            used.add(("agent", ns, short))
    for c in _calls(lt, [x["name"] for x in lt.calls if (x["name"] or "").startswith("mcp__")], sidechain=True):
        srv = c["name"].split("__")[1]
        if srv in mc:
            used.add(("mcp", "", srv))
    for c in lt.all_commands():
        ns, short = _split_call(c["name"].lstrip("/"))
        if ns and lt.interactive(c["s"]):
            if (ns, short) in ids["command"]:
                used.add(("command", ns, short))
            elif (ns, short) in ids["skill"]:
                used.add(("skill", ns, short))
    ran = "\x00".join(x["data"] for x in _att(lt, "hook_success"))    # one text of every hook run (a name never holds NUL); listed once, not per hook
    for a in own:
        if a["kind"] == "hook" and os.path.basename(a["path"]) in ran:
            used.add(("hook", "", a["name"]))
    return {"value": len(used), "evidence": [f"{k}: {(ns + ':') if ns else ''}{n}" for k, ns, n in sorted(used)][:3],
            "note": "a hook attachment cut short in the log may lose its command; plugin-provided MCP servers are matched elsewhere; their own command typed without its plugin name is not seen"}


@fixture(PID_B, "L4", 2)
def _(fx):
    w = _world(fx)
    s = fx.session(w["kit"], entrypoint="claude-vscode")
    s.call("Skill", {"skill": "kit:lint-fix"}, "ok")                     # own skill used
    s.call("Skill", {"skill": "other:lint-fix"}, "ok")                   # trap: another plugin's skill with the same short name
    s.call("Skill", {"skill": "pdf"}, "ok")                              # trap: from a lock file, not theirs
    s.call("Agent", {"subagent_type": "kit:checker", "description": "d", "prompt": "p"}, "done")   # own agent used
    s.call("Agent", {"subagent_type": "other:checker", "description": "d", "prompt": "p"}, "done") # trap: another plugin's agent
    s.call("Agent", {"subagent_type": "general-purpose", "description": "d", "prompt": "p"}, "done")   # trap
    s.call("mcp__kit-db__query", {"sql": "select 1"}, "1")               # own server used
    s.call("mcp__web__fetch", {"url": "x"}, "page")                      # trap: npx server
    d = fx.session(w["scratch"], entrypoint="claude-desktop")
    d.call("Skill", {"skill": "active"}, "ok")                           # own project skill, called by its bare name
    d.command_expansion("/kit:ship", "ship it")                          # own command used, called by its plugin's name (a plugin command is written <command-message> first)
    d.command("/lint-fix")                                               # trap: a bare name could be a built-in
    d.command("/review")                                                 # trap: a built-in
    d.command("/other:ship")                                             # trap: another plugin's command
    s.attachment("hook_success", hookName="Stop", hookEvent="Stop", content="", stdout="", stderr="", exitCode=0, command=fx.p(w["kit"] + "/hooks/stop-guard.sh"))
    h = fx.session(w["scratch"], entrypoint="sdk-cli")
    h.call("Skill", {"skill": "idea"}, "ok"); h.call("Agent", {"subagent_type": "kit:writer", "description": "d", "prompt": "p"}, "done")  # trap: headless
    return {"value": 6}


@check(PID_B, "L4", 3)
def told_apart(lt):
    """Things that are not the person's despite being in their repos or folders, and why: installed (plugin cache,
    marketplace), listed in a lock file, same content as an installed copy, most commits someone else's, manifest naming
    another author. Value = how many were set aside."""
    out = [a for a in _artifacts(lt) if a["reason"]]
    per = {}
    for a in out:
        per[a["reason"]] = per.get(a["reason"], 0) + 1
    return {"value": len(out), "evidence": [f"{v} {k}" for k, v in sorted(per.items())][:3],
            "note": "a copy edited after copying, or added whole and never touched, is not told apart"}


@fixture(PID_B, "L4", 3)
def _(fx):
    _world(fx)
    return {"value": 5}       # pdf (lock), deploy (copy), foreign plugin (author), team-skill and team-agent (git share)


@check(PID_B, "L4", 4)
def kinds_built(lt):
    """How many kinds of thing (plugin, marketplace, skill, agent, command, hook, MCP server) have at least one the person built."""
    kinds = sorted({a["kind"] for a in _artifacts(lt) if a["own"]})
    return {"value": len(kinds), "evidence": [", ".join(kinds)] if kinds else [], "note": "same copied-in test as the rows above"}


@fixture(PID_B, "L4", 4)
def _(fx):
    _world(fx)
    return {"value": 7}


MIN_REFINE_WEEKS = 3
REFINED_KINDS = ("skill", "agent", "command", "hook", "mcp", "plugin")


def _own_setup_files(lt):
    """{realpath: True} of the files in the person's repos that they built for the agent and that exist now: skills (SKILL.md and the files
    in the skill's folder), agents, commands, hook scripts a settings file runs, their own MCP server files and plugin manifests. Each is
    theirs by _artifacts (the one ownership rule, then a lock file or a copy of an installed file set aside). Not CLAUDE.md, AGENTS.md,
    rule files or settings: Claude Code writes those itself (/init, # memory, /permissions), so editing them says nothing about building.
    Built once."""
    c = _cache(lt)
    if "setup_files" in c:
        return c["setup_files"]
    arts = _artifacts(lt)
    byp = {a["path"]: a["own"] for a in arts if a["kind"] in REFINED_KINDS}
    skill_dirs = {os.path.dirname(a["path"]): a["own"] for a in arts if a["kind"] == "skill"}
    out = {}
    for r in lt.repos:
        for rel in _tracked(lt, r):
            rp = os.path.realpath(os.path.join(r, rel))
            if rp in byp:
                if byp[rp]:
                    out[rp] = True
                continue
            d = os.path.dirname(rp)
            while d.startswith(r) and len(d) > len(r):       # a file in a skill's folder belongs to the skill
                if d in skill_dirs:
                    if skill_dirs[d] and os.path.isfile(rp):
                        out[rp] = True
                    break
                if os.path.dirname(d) == d:                  # a root is its own parent
                    break
                d = os.path.dirname(d)
    c["setup_files"] = out
    return out


@check(PID_B, "L4", 5)
def setup_refined_over_time(lt):
    """Setup refined over time: the distinct ISO weeks (UTC) in the window in which the person's own commits (identity.py's emails) MODIFIED
    (git status M: the file was there before the commit) a setup file they built (_own_setup_files: skills, agents, commands, hook scripts,
    their own MCP server and plugin files; not installed, vendored, locked or a teammate's; not CLAUDE.md / AGENTS.md / rule files / settings,
    which Claude Code writes itself). Adding a new file is building, not refining. Value = weeks when there are >= MIN_REFINE_WEEKS (3),
    else 0: one burst of edits in one or two weeks is not refined over time."""
    own = _own_setup_files(lt)
    weeks, files, commits = set(), set(), 0
    for r in lt.repos:
        for cm in _git_commits(lt, r):
            if not cm["mine"]:
                continue
            hit = [os.path.realpath(os.path.join(r, p)) for st, p, _ in cm["files"] if st == "M"]
            hit = [x for x in hit if x in own]
            if hit:
                commits += 1
                files.update(hit)
                iso = datetime.datetime.fromtimestamp(cm["t"], datetime.timezone.utc).isocalendar()
                weeks.add((iso[0], iso[1]))
    value = len(weeks) if len(weeks) >= MIN_REFINE_WEEKS else 0
    return {"value": value, "evidence": [f"{len(files)} setup files they built, modified in {commits} commits",
                                          f"{len(weeks)} distinct weeks" + ("" if value else f" (under {MIN_REFINE_WEEKS}: not spread over time)")],
            "note": "only edits of a file that was already committed count; commits an agent made under the person's identity count as theirs; "
                    "a file deleted or moved since is not seen; setup files outside a git repo (~/.claude on its own) have no history to read; "
                    "whether the edits answered a miss is a question for the call"}


@fixture(PID_B, "L4", 5)
def _(fx):
    from fixture import FixtureHome
    now = NOW
    ago = lambda days: now - days * DAY        # commits are made oldest first: git stops reading a log at commits older than the window
    sk = lambda v: f"---\nname: s\n---\nv{v}\n"
    r = "~/work/shop"
    with _commit_clock(ago(29)):      # built before the window's weeks: a skill with a script, an agent, a hook script, CLAUDE.md
        fx.repo(r, {"README.md": "x", "src/app.py": "print(1)\n", "skills/s/SKILL.md": sk(0), "skills/s/scripts/run.sh": "echo 0\n",
                    "agents/rev.md": "---\nname: rev\n---\nx0\n", "hooks/guard.sh": "#!/bin/sh\nexit 0\n", "CLAUDE.md": "rule 0\n"}, by="work")
    with _commit_clock(ago(25)):
        fx.commit(r, {"AGENTS.md": "teammate rule\n", "skills/s/SKILL.md": sk("team")}, by="teammate")        # trap: a teammate's edit
    steps = ((22, {"skills/s/SKILL.md": sk(1)}),                          # week 1: the skill modified
             (15, {"skills/s/scripts/run.sh": "echo 1\n", "agents/rev.md": "---\nname: rev\n---\nx1\n", "CLAUDE.md": "rule 1\n"}),   # week 2: script + agent
             (8, {"hooks/guard.sh": "#!/bin/sh\nexit 1\n"}),             # week 3: the hook script
             (1, {"skills/s/SKILL.md": sk(2), ".claude/settings.json": "{}"}))      # week 4: the skill again
    for days, files in steps:
        with _commit_clock(ago(days)):
            fx.commit(r, files, by="work")
        if days == 15:
            with _commit_clock(ago(days) + 3600):
                fx.commit(r, {"src/app.py": "print(2)\n"}, by="work")      # trap: not a setup file
    # a week in which only CLAUDE.md, rule files and settings change is not a week of refining
    with _commit_clock(ago(18)):
        fx.commit(r, {"CLAUDE.md": "rule 1b\n", ".claude/rules/r.md": "r\n", ".claude/settings.local.json": "{}"}, by="work")
    fx.settings("user", {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": fx.p(r + "/hooks/guard.sh")}]}]}})     # the hook script is wired
    fx.session(r, entrypoint="claude-vscode").typed("hi")
    # traps, on laptops of their own
    adds = FixtureHome()
    try:
        with _commit_clock(ago(29)):
            adds.repo("~/work/shop", {"README.md": "x"}, by="work")
        for days in (22, 15, 8, 1):      # a new skill, agent or command every week, nothing ever modified
            with _commit_clock(ago(days)):
                adds.commit("~/work/shop", {f"skills/a{days}/SKILL.md": f"---\nname: a{days}\n---\nx\n", f"agents/b{days}.md": f"---\nname: b{days}\n---\nx\n",
                                            f"commands/c{days}.md": "c\n"}, by="work")
        adds.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
        got = setup_refined_over_time(adds.laptop()).get("value")
        assert got == 0, f"a new file every week, none modified: expected 0, got {got}"
    finally:
        adds.close()
    one_week = FixtureHome()
    try:
        with _commit_clock(ago(29)):
            one_week.repo("~/work/shop", {"README.md": "x", "skills/a/SKILL.md": "---\nname: a\n---\nx\n", "agents/b.md": "---\nname: b\n---\nx\n",
                                          "commands/c.md": "c\n"}, by="work")
        for days in (12, 11, 10):      # one burst: three commits modifying all of it, in one week
            with _commit_clock(ago(days)):
                one_week.commit("~/work/shop", {"skills/a/SKILL.md": f"---\nname: a\n---\n{days}\n", "agents/b.md": f"---\nname: b\n---\n{days}\n",
                                                "commands/c.md": f"{days}\n"}, by="work")
        one_week.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
        got = setup_refined_over_time(one_week.laptop()).get("value")
        assert got == 0, f"modifications in one week: expected 0, got {got}"
    finally:
        one_week.close()
    installed = FixtureHome()
    try:
        deploy = "---\nname: deploy\ndescription: ship it\n---\nRun the deploy.\n"
        installed.file("~/.claude/plugins/cache/mkt/pl/1.0/skills/deploy/SKILL.md", deploy)
        with _commit_clock(ago(29)):
            installed.repo("~/work/shop", {"README.md": "x", "skills-lock.json": json.dumps({"version": 1, "skills": {"pdf": {"source": "o/r", "computedHash": "h"}}}),
                                           "skills/pdf/SKILL.md": "pdf v0\n", "skills/deploy/SKILL.md": "old deploy\n"}, by="work")
        for days in (25, 20, 15, 10, 5):      # a commit every week to a locked skill and to a copy of an installed plugin's skill (the last one is the installed text)
            with _commit_clock(ago(days)):
                installed.commit("~/work/shop", {"skills/pdf/SKILL.md": f"pdf v{days}\n",
                                                 "skills/deploy/SKILL.md": deploy if days == 5 else f"old deploy {days}\n"}, by="work")
        installed.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
        got = setup_refined_over_time(installed.laptop()).get("value")
        assert got == 0, f"commits to installed files: expected 0, got {got}"
    finally:
        installed.close()
    return {"value": 4}      # the skill (and its script), the agent, the hook script, the skill again: four weeks; the teammate's edit, CLAUDE.md, rules, settings, src do not add


# ================================================================================ give-the-agent-tools-made-for-it
PID_T = "give-the-agent-tools-made-for-it"


@check(PID_T, "L2", 1)
def tool_fumbles(lt):
    """Tool results flagged is_error in interactive sessions, leaving out the person's refusals (toolDenialKind) and a
    failing run of a check the repo itself declares (an expected failure)."""
    pats = {}
    n = 0
    for c in lt.calls:
        if not c["err"] or c.get("denial") or not lt.interactive(c["s"]):
            continue
        if c["name"] == "Bash":
            repo = lt.repo_of((lt.sessions.get(c["s"]) or {}).get("cwd"))
            if repo:
                pats.setdefault(repo, lt.declared_checks(repo))
                if any(p.search(_cmd(c)) for p in pats[repo]):
                    continue
        n += 1
    return {"value": n, "evidence": [f"{n} errored tool results"], "note": "an error shows a tool went wrong, not that the agent was at fault; hook blocks that arrive as results are included"}


@fixture(PID_T, "L2", 1)
def _(fx):
    fx.repo("~/work/shop", {"package.json": json.dumps({"scripts": {"test": "jest"}})})
    s = fx.session("~/work/shop", entrypoint="claude-vscode")
    s.call("Bash", {"command": "foo --bar"}, "zsh: command not found: foo", is_error=True)       # counts
    s.call("Edit", {"file_path": fx.p("~/work/shop/a.ts"), "old_string": "x", "new_string": "y"}, "String to replace not found", is_error=True)   # counts
    s.call("Bash", {"command": "npm test"}, "1 failing", is_error=True)                          # trap: expected failing test
    s.refuse("Edit", {"file_path": fx.p("~/work/shop/b.ts")})                                    # trap: the person said no
    s.call("Bash", {"command": "ls"}, "a b")
    h = fx.session("~/work/shop", entrypoint="sdk-cli")
    h.call("Bash", {"command": "foo"}, "not found", is_error=True)                               # trap: headless
    return {"value": 2}


def _rc_path_dirs(lt):
    c = _cache(lt)
    if "pathdirs" not in c:
        dirs = [os.path.join(lt.home, "bin"), os.path.join(lt.home, ".local", "bin")]
        for rc in (".zshrc", ".bashrc", ".bash_profile", ".zprofile", ".profile"):
            for m in re.finditer(r"^\s*(?:export\s+)?PATH=[\"']?([^\"'\n]+)", _read(os.path.join(lt.home, rc), 50000), re.M):
                for d in m.group(1).split(":"):
                    d = d.replace("${HOME}", lt.home).replace("$HOME", lt.home)
                    d = lt.home + d[1:] if d.startswith("~") else d
                    if d and "$" not in d:
                        dirs.append(d)
        dirs += [d for d in os.environ.get("PATH", "").split(os.pathsep) if d.startswith(lt.home)]
        c["pathdirs"] = [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]
    return c["pathdirs"]


def _real(lt, path):
    """os.path.realpath, with each folder resolved once: a path that is not itself a link is its resolved folder plus its name."""
    d, name = os.path.split(path)
    if not name or name in (".", "..") or os.path.islink(path):
        return os.path.realpath(path)
    return os.path.join(_real_dir(lt, d), name)


def _real_dir(lt, d):
    """The resolved folder d, built from its parent's (memoized): one link test per new folder, not one per level of its path."""
    memo = _cache(lt).setdefault("realdir", {})
    if d not in memo:
        parent, name = os.path.split(d)
        if not name or name in (".", "..") or parent == d or os.path.islink(d):
            memo[d] = os.path.realpath(d)
        else:
            memo[d] = os.path.join(_real_dir(lt, parent), name)
    return memo[d]


def _home_prefix(lt):
    """The person's real home folder plus a separator, resolved once."""
    c = _cache(lt)
    if "homeprefix" not in c:
        c["homeprefix"] = os.path.realpath(lt.home) + os.sep
    return c["homeprefix"]


def _listing(lt, d):
    """The names in a folder, lower-cased, read once (a set; a case-insensitive disk finds names whatever their case);
    empty when it cannot be read."""
    memo = _cache(lt).setdefault("listing", {})
    if d not in memo:
        try:
            memo[d] = frozenset(n.lower() for n in os.listdir(d or "."))
        except OSError:
            memo[d] = frozenset()
    return memo[d]


def _stems(lt, d):
    """The names a word could have to be found in folder d: each listed name, and the name without a program extension."""
    memo = _cache(lt).setdefault("stems", {})
    if d not in memo:
        out = set()
        for n in _listing(lt, d):
            out.add(n)
            out.update(n[:-len(e)] for e in PROGRAM_EXTS[1:] if n.endswith(e))
        memo[d] = frozenset(out)
    return memo[d]


def _path_stems(lt):
    c = _cache(lt)
    if "pathstems" not in c:
        c["pathstems"] = frozenset().union(*[_stems(lt, d) for d in _rc_path_dirs(lt)])
    return c["pathstems"]


def _program_file(lt, tok, cwd, first):
    """The file under the person's home that `tok` names (a path, or a first word found in the working folder or a PATH
    folder), else None. Memoized per (word, folder, first); folders are listed once, so a lookup is set membership."""
    if "/" not in tok and "\\" not in tok:
        if not first:      # only a first word is looked up by name
            return None
        stem = tok.strip("\"'").lower()
        if stem not in _path_stems(lt) and stem not in _stems(lt, cwd or ""):   # no such name anywhere it would be looked for
            return None
    memo = _cache(lt).setdefault("progfile", {})
    key = (tok, cwd, first)
    if key not in memo:
        memo[key] = _find_program_file(lt, tok, cwd, first)
    return memo[key]


def _find_program_file(lt, tok, cwd, first):
    t = _n(tok.strip("\"'"))
    if not t or t.startswith("-") or "=" in t.split("/")[0] and first:
        return None
    if t.startswith("~/"):
        t = _n(lt.home) + t[1:]
    if "/" in t:
        return _first_file_in(lt, [t if _is_abs(t) else os.path.join(cwd or "", t)])
    if not first:
        return None
    # the working folder first, then the PATH folders (their answer does not depend on the folder: memoized per word)
    memo = _cache(lt).setdefault("pathfile", {})
    if t not in memo:
        memo[t] = _path_file(lt, t)
    return _in_folder(lt, cwd or "", t) or memo[t]


PROGRAM_EXTS = ("", ".sh", ".py", ".exe", ".cmd", ".ps1", ".bat")


def _in_folder(lt, d, name):
    """The first file named `name` (+ a program extension) in folder d that lies under the person's home, else None.
    The folder is listed once; only names that are in the listing cost a file check."""
    names = _listing(lt, d)
    for e in PROGRAM_EXTS:
        if (name + e).lower() in names:
            q = os.path.join(d, name + e)
            if os.path.isfile(q):
                rp = _real(lt, q)
                if rp.startswith(_home_prefix(lt)):
                    return rp
    return None


def _path_index(lt):
    """{lower-cased name: [index of each PATH folder that lists it]}: one look-up per word instead of one per folder."""
    c = _cache(lt)
    if "pathindex" not in c:
        idx = {}
        for i, d in enumerate(_rc_path_dirs(lt)):
            for n in _listing(lt, d):
                idx.setdefault(n, []).append(i)
        c["pathindex"] = idx
    return c["pathindex"]


def _path_file(lt, name):
    """The first PATH folder (in order, each trying the program extensions in order) holding a file `name` under the person's home."""
    dirs, idx = _rc_path_dirs(lt), _path_index(lt)
    hits = sorted((i, k) for k, e in enumerate(PROGRAM_EXTS) for i in idx.get((name + e).lower(), ()))
    home = _home_prefix(lt)
    for i, k in hits:
        q = os.path.join(dirs[i], name + PROGRAM_EXTS[k])
        if os.path.isfile(q):
            rp = _real(lt, q)
            if rp.startswith(home):
                return rp
    return None


def _is_folder(lt, d):
    """Is d a folder? Asked once per path; a path below something that is not a folder is not one (no stat)."""
    memo = _cache(lt).setdefault("isdir", {})
    if d not in memo:
        parent, name = os.path.split(d)
        if parent and parent != d and name not in ("", ".", "..") and not _is_folder(lt, parent):
            memo[d] = False
        else:
            memo[d] = os.path.isdir(d or ".")
    return memo[d]


def _first_file_in(lt, cands):
    """The first of these paths (tried with each program extension) that is a file under the person's home. Each distinct
    path is looked at once, and only when its folder exists."""
    memo = _cache(lt).setdefault("filehit", {})
    for p in cands:
        if p not in memo:
            memo[p] = _file_hit(lt, p)
        if memo[p]:
            return memo[p]
    return None


LISTING_AFTER = 3   # a folder asked about this many different names is read once; fewer, each name is stat'ed (the same answers, the cheaper way)


def _file_hit(lt, p):
    d = os.path.dirname(p)
    if not _is_folder(lt, d):
        return None
    asked = _cache(lt).setdefault("asked", {})
    asked[d] = asked.get(d, 0) + 1
    names = _listing(lt, d) if asked[d] >= LISTING_AFTER or d in _cache(lt).setdefault("listing", {}) else None
    for e in PROGRAM_EXTS:
        q = p + e
        if names is not None and os.path.basename(q).lower() not in names:
            continue
        if os.path.isfile(q):
            rp = _real(lt, q)
            if rp.startswith(_home_prefix(lt)):
                return rp
    return None


def _repo_around(lt, real):
    """The repo root a file lies in: the nearest folder up that holds a .git (a folder, or a file for a worktree). Memoized per
    folder, so the whole chain above a folder is walked once."""
    memo = _cache(lt).setdefault("walktop", {})
    d = real if os.path.isdir(real) else os.path.dirname(real)
    chain = []
    while d not in memo:
        chain.append(d)
        if os.path.exists(os.path.join(d, ".git")):
            memo[d] = d
            chain.pop()
            break
        parent = os.path.dirname(d)
        if parent == d:
            memo[d] = None
            chain.pop()
            break
        d = parent
    for c in chain:
        memo[c] = memo[d]
    return memo[d]


def _yours_of(lt, files):
    """{file: is it the person's} for many files, by the one ownership rule (lt.owner, ruling 11-05: what lt.authorship(f)["yours"]
    answers): theirs unless a receipt says installed or most of its commits are someone else's. A file with no commits, or in no
    repo, is theirs. Commits are read from the repo's authors when base.py has them, else from one `git log --full-history --
    <the files asked>` per repo (the files' own commits, not the repo's whole change list)."""
    c = _cache(lt)
    lt.repos; lt.identity; lt._ownership()   # read before any thread starts (identity swaps HOME while it reads)
    memo, mine = c.setdefault("yours", {}), c.setdefault("mine", {})
    counts = {}      # file -> (repo top, commits, the person's commits)

    def share(top, log):
        n = 0
        for a in log:
            if a not in mine:
                mine[a] = lt.is_mine(a)
            n += mine[a]
        return (top, len(log), n)

    by_top = {}
    for f in files:
        if f in memo:
            continue
        real = _real(lt, f)
        top = _repo_around(lt, real)
        rel = os.path.relpath(real, top).replace(os.sep, "/") if top else None
        if not top:
            counts[f] = (None, 0, 0)
        elif top in lt.repos or top in lt.__dict__.get("_auth_memo", {}):
            counts[f] = share(top, lt._authors(top).get(rel, []))
        else:
            by_top.setdefault(top, {})[f] = rel

    def read(item):
        top, rels = item
        authors, listed = {}, list(rels.items())
        for i in range(0, len(listed), 200):
            who = None
            raw = lt.git("log", "--full-history", "--no-renames", "--format=%x00%an <%ae>", "--name-only", "--",
                         *[":(literal)" + rel for _, rel in listed[i:i + 200]], cwd=top, timeout=120)
            for line in raw.splitlines():
                if line.startswith("\x00"):
                    who = line[1:]
                elif line and who:
                    authors.setdefault(line, []).append(who)
        for f, rel in listed:
            counts[f] = share(top, authors.get(rel, []))

    if len(by_top) == 1:
        read(*by_top.items())
    elif by_top:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as ex:   # git runs in its own process: threads overlap the waiting
            list(ex.map(read, by_top.items()))
    for f, (top, n, m) in counts.items():
        memo[f] = lt.owner(f, top, n, m) == "theirs"      # the rule, once the commits are in
    return memo


_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
_WORDS = re.compile(r"\"[^\"]*\"|'[^']*'|\S+")


def _segments(cmd):
    return [s.strip() for s in _SEGMENT_SPLIT.split(cmd or "") if s.strip()]


_ENV_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
# the language runtimes that run a script file of each extension (a language's own interpreters, not anyone's tools)
_SH = ("sh", "bash", "zsh", "dash", "ksh")
_JS = ("node", "nodejs", "bun", "deno")
SCRIPT_RUNTIMES = {"sh": _SH, "bash": _SH, "zsh": _SH, "py": ("python", "pypy", "uv", "pipx"), "js": _JS, "mjs": _JS, "cjs": _JS,
                   "ts": ("tsx", "ts-node", "bun", "deno", "node"), "rb": ("ruby", "bundle"), "ps1": ("pwsh", "powershell"),
                   "bat": ("cmd",), "cmd": ("cmd",)}
_INLINE_FLAGS = ("-m", "-c", "-e", "-p")      # python -m pytest x.py, node -e: the file is an argument, not the script run


def _runtime_stem(tok):
    return re.sub(r"[\d.]+$", "", re.sub(r"\.exe$", "", os.path.basename(_n(tok.strip("\"'"))).lower()))


def _is_program(lt, f):
    """A file made to be run: a script extension, a `#!` first line, or the executable bit. A config or data file (.bashrc,
    .gitattributes, a .csv) is none of these."""
    memo = _cache(lt).setdefault("isprogram", {})
    if f not in memo:
        ok = f.lower().endswith(PROGRAM_EXTS[1:])
        if not ok:
            try:
                with open(f, "rb") as fh:
                    ok = fh.read(2) == b"#!"
            except OSError:
                ok = False
            ok = ok or os.access(f, os.X_OK)
        memo[f] = ok
    return memo[f]


def _file_arg(lt, tok, cwd):
    t = _n(tok.strip("\"'"))
    if t.startswith("~/"):
        t = _n(lt.home) + t[1:]
    return _first_file_in(lt, [t if _is_abs(t) else os.path.join(cwd or "", t)])


_SCRIPT_WORD = re.compile(r"\.(?:%s)[\"']*(?:\s|$)" % "|".join(SCRIPT_RUNTIMES), re.I)    # a word that ends in a script extension


def _segment_shape(seg):
    """What a shell segment says apart from the working folder, once per distinct segment: (its first word lower-cased without quotes,
    is that word a path, the words that could be a script file run by a language runtime). A word is one when its extension has
    runtimes, no inline flag (-m -c -e -p) precedes it and a runtime of that extension is among the words before it (of the
    first five)."""
    memo = _SEGMENT_SHAPE
    if seg not in memo:
        toks = _first_words(seg, 5)      # only the first five words after the env assignments are ever looked at
        if not toks:
            memo[seg] = ("", False, ())
        else:
            t = toks[0]
            cands = []
            for j in range(1, min(5, len(toks))):
                tok = toks[j]
                fam = SCRIPT_RUNTIMES.get(_n(tok.strip("\"'")).rsplit(".", 1)[-1].lower()) if "." in tok and not tok.startswith("-") else None
                if fam and not any(x in _INLINE_FLAGS for x in toks[:j]) and any(_runtime_stem(x) in fam for x in toks[:j]):
                    cands.append(tok)
            memo[seg] = (t.strip("\"'").lower(), "/" in t or "\\" in t, tuple(cands))
    return memo[seg]


_SEGMENT_SHAPE = {}


def _first_words(seg, n):
    """The first n words of a segment after its leading VAR=value words (what _WORDS.findall gives, without splitting the rest)."""
    out, lead = [], True
    for m in _WORDS.finditer(seg):
        w = m.group()
        if lead and _ENV_ASSIGN.match(w):
            continue
        lead = False
        out.append(w)
        if len(out) == n:
            break
    return out


def _segment_file(lt, seg, cwd, pstems, cstems):
    """The program file a shell segment runs: its first word (a path, or a name found in the working folder or on PATH) when that file is
    made to be run, or a script file named after a language runtime (`bash x.sh`, `python x.py`, `uv run x.py`). A file that is only
    an argument of another program (cat, sed, grep, git diff on a config or a script) is not run."""
    # The common segment (`git status`, `cd /some/folder`, `ls x`) names no program file: answered from its first word, without reading the
    # segment into words. Only a first word that is a path or is found on PATH / in the folder, or a script file named after a runtime
    # (a word ending in a script extension), can give a file.
    w = seg.split(None, 1)
    g = w[0] if w else ""
    if g and g[0] not in "\"'" and not _ENV_ASSIGN.match(g):
        gl = g.strip("\"'").lower()
        if "/" not in g and "\\" not in g and gl not in pstems and gl not in cstems and not _SCRIPT_WORD.search(seg):
            return None
    first, slash, cands = _segment_shape(seg)
    if not first and not slash and not cands:
        return None
    if slash or first in pstems or first in cstems:
        toks = _WORDS.findall(seg)
        while toks and _ENV_ASSIGN.match(toks[0]):
            toks = toks[1:]
        f = _program_file(lt, toks[0], cwd, True)
        if f and _is_program(lt, f):
            return f
    for tok in cands:
        f = _file_arg(lt, tok, cwd)
        if f:
            return f
    return None


@check(PID_T, "L3", 0)
def agent_runs_your_scripts(lt):
    """Bash calls that run a script or program file that is the person's by the one ownership rule (committed or not): the command's first word, looked up in the working folder
    and the PATH folders under their home (shell rc files included), when that file is made to be run (script extension, `#!` line or
    executable bit); or a script file run by a language runtime (`bash scripts/x.sh`, `python x.py`). Reading a file (cat, sed,
    grep, git diff) is not running it. Windows .exe/.cmd/.ps1 tried too. Interactive sessions."""
    _calm_gc()
    seen = _cache(lt).setdefault("scriptrun", {})     # (command, folder) -> the program file each segment names, or None
    segs = _cache(lt).setdefault("segfile", {})        # (segment, folder) -> the program file it runs, or None
    calls = []
    for c in _calls(lt, ("Bash",), sidechain=True):
        cwd = (lt.sessions.get(c["s"]) or {}).get("cwd")
        key = (_cmd(c), cwd)
        if key not in seen:
            pstems, cstems = _path_stems(lt), _stems(lt, cwd or "")
            found = []
            for seg in _segments(key[0]):    # the same segment (`git status`, `ls`, `cd x`) recurs in many commands: answered once per (segment, folder)
                if (seg, cwd) not in segs:
                    segs[(seg, cwd)] = _segment_file(lt, seg, cwd, pstems, cstems)
                found.append(segs[(seg, cwd)])
            seen[key] = found
        calls.append(key)
    yours = _yours_of(lt, {f for key in set(calls) for f in seen[key] if f})
    n, names = 0, []
    for key in calls:
        for f in seen[key]:
            if f and yours[f]:
                n += 1; names.append(os.path.basename(f)); break
    return {"value": n, "evidence": sorted(set(names))[:3], "note": "a script nobody committed is theirs (ruling 11-05); a program run through a package manager (npx, uvx) is not a file here"}


@fixture(PID_T, "L3", 0)
def _(fx):
    fx.repo("~/work/shop", {"scripts/deploy.sh": "#!/bin/sh\necho deploy\n", "scripts/tool.py": "print(1)\n", ".gitattributes": "* text=auto\n",
                            "notes/data.csv": "a,b\n"}, by="work")
    fx.commit("~/work/shop", {"scripts/tm.sh": "#!/bin/sh\n"}, by="teammate")
    fx.file("~/work/shop/scripts/wip.sh", "#!/bin/sh\n")                       # never committed (untracked)
    fx.repo("~/code/dotfiles", {"bin/ship": "#!/bin/sh\necho ship\n"}, by="home")
    os.makedirs(fx.p("~/bin"), exist_ok=True); os.symlink(fx.p("~/code/dotfiles/bin/ship"), fx.p("~/bin/ship"))
    fx.file("~/.zshrc", 'export PATH="$HOME/bin:$PATH"\n')
    s = fx.session("~/work/shop", entrypoint="claude-vscode")
    s.call("Bash", {"command": "./scripts/deploy.sh --prod"}, "ok")            # counts
    s.call("Bash", {"command": "bash scripts/deploy.sh"}, "ok")                # counts: file run by a shell
    s.call("Bash", {"command": "cd api && ship now"}, "ok")                    # counts: a link on PATH to the person's file
    s.call("Bash", {"command": "API_KEY=x python3 scripts/tool.py"}, "ok")     # counts: a script run by its language
    s.call("Bash", {"command": "./scripts/tm.sh"}, "ok")                       # trap: a teammate's
    s.call("Bash", {"command": "./scripts/wip.sh"}, "ok")                      # counts: untracked but no install receipt = theirs (ruling 11-05)
    s.call("Bash", {"command": "git status && ls -la"}, "ok")                  # trap: programs of the system
    s.call("Bash", {"command": "cat scripts/deploy.sh"}, "ok")                 # trap: read, not run
    s.call("Bash", {"command": "sed -n 1,5p ./scripts/deploy.sh"}, "ok")       # trap: read, not run
    s.call("Bash", {"command": "grep -n PATH ~/.zshrc"}, "ok")                 # trap: a config file
    s.call("Bash", {"command": "git diff .gitattributes"}, "ok")               # trap: a config file
    s.call("Bash", {"command": "./notes/data.csv"}, "ok")                      # trap: a data file, not a program
    s.call("Bash", {"command": "python -m pytest scripts/tool.py"}, "ok")      # trap: an argument of a module, not the script
    h = fx.session("~/work/shop", entrypoint="sdk-cli")
    h.call("Bash", {"command": "./scripts/deploy.sh"}, "ok")                   # trap: headless
    return {"value": 5}      # deploy.sh x2, ship, tool.py, wip.sh


REGISTRY_RUNNERS = frozenset({"npx", "uvx", "bunx", "pnpx", "pipx", "deno"})     # fetch a package from a registry and run it
NOT_THEIR_CODE = frozenset({"node_modules", "site-packages", ".venv", "venv", ".npm", ".cache", "_npx", "dist-packages"})


def _server_script(lt, spec, bases, plugin_root=None):
    """The file of the person's own folders that an MCP server's command runs (`node tools/db.mjs`, `uv run server.py`,
    `${CLAUDE_PLUGIN_ROOT}/bin/serve`), or None: a server fetched from a registry (npx, uvx, bunx, pnpm dlx ...), a remote
    server with no command, a package under node_modules / site-packages, or a command that names no file. A data file
    passed to a registry package is not its code."""
    if not isinstance(spec, dict):
        return None
    toks = _tokens(" ".join([str(spec.get("command") or "")] + [str(a) for a in spec.get("args") or []]))
    if not toks:
        return None
    runner = os.path.basename(_n(toks[0])).lower().rsplit(".", 1)[0]
    if runner in REGISTRY_RUNNERS or (runner in ("npm", "pnpm", "yarn", "bun") and toks[1:2] and toks[1] in ("exec", "dlx", "x", "create")):
        return None
    if "-m" in toks[:5]:       # python -m pkg: a module, the first file named after it is data
        return None
    # an absolute path to a runtime (~/.local/bin/uv, ~/.volta/bin/node, an nvm/fnm node) is the program, not the server's
    # file: look for the script among the arguments (a reviewer found `~/.local/bin/uv --directory <clone> run x` read as theirs)
    if runner in RUNTIME_NAMES or re.match(r"python\d", runner or ""):
        toks = toks[1:]
        if not toks:
            return None
    script = _resolve(lt, " ".join(toks), bases, plugin_root)
    if not script or NOT_THEIR_CODE & set(_n(script).split("/")):
        return None
    return script


RUNTIME_NAMES = frozenset({"node", "nodejs", "python", "python3", "pypy", "pypy3", "uv", "pipx", "bun", "deno", "ruby", "sh", "bash",
                           "zsh", "go", "java", "dotnet", "php", "perl", "volta", "fnm", "tsx", "ts-node"})
BUILD_DIRS = frozenset({"dist", "build"})


def _repo_vouches(lt, script):
    """Whether the repo around a server script lets it count as the person's. A file git does not hold (untracked or ignored) has no
    history of its own, and the ownership rule calls it theirs: a cloned open-source server run from its build output
    (`node ~/clones/x-mcp/build/index.js`) would read as theirs. So an untracked or ignored script, and any script under dist/ or build/
    (output of a build, whose commits are the build's, not the author's), is judged by the repo's commits (the ownership rule over
    all of them: someone else's when most are by other people). Their own built server in their own repo stays theirs. A script
    in no repo, or tracked outside a build folder, is judged by its own commits alone."""
    real = _real(lt, script)
    top = _repo_around(lt, real)
    if not top:
        return True
    rel = os.path.relpath(real, top).replace(os.sep, "/")
    tracked = bool(lt.git("ls-files", "--cached", "--", ":(literal)" + rel, cwd=top).strip())
    if tracked and not (BUILD_DIRS & set(rel.split("/")[:-1])):
        return True
    log = [a for a in lt.git("log", "--format=%an <%ae>", "-n", "5000", cwd=top, timeout=60).splitlines() if a.strip()]
    return lt.owner(real, top, len(log), sum(1 for a in log if lt.is_mine(a))) == "theirs"


def _own_servers(lt):
    """{tool-name server id: ([is the server's code the person's own, one entry per place that defines it], its script)} for every MCP server Claude
    Code can start from files read here: .mcp.json files in the person's repos (`mcp__<server>__`, and as a plugin's server
    `mcp__plugin_<plugin>_<server>__` when a plugin folder holds the file), servers inline in a plugin.json, and servers added to
    ~/.claude.json (`claude mcp add`). Own = the command runs a file in their folders (_server_script) that the one ownership
    rule calls theirs (a script git does not hold, or built into dist/ or build/, by its repo's commits: _repo_vouches). A name defined
    in several places is theirs only when every place is."""
    c = _cache(lt)
    if "own_servers" in c:
        return c["own_servers"]
    home = os.path.realpath(lt.home)
    defs = []      # (tool-name ids, spec, bases, plugin root)

    def norm(x):       # Claude Code writes a server's name into the tool name with every odd character as _
        return re.sub(r"[^A-Za-z0-9_-]", "_", str(x))

    def add(servers, bases, proot, project_level):
        if not isinstance(servers, dict):
            return
        pn = _plugin_name(proot) if proot else None
        for sname, spec in servers.items():
            ids = ([norm(sname)] if project_level else []) + ([f"plugin_{norm(pn)}_{norm(sname)}"] if pn else [])
            if ids:
                defs.append((ids, spec, bases, proot))
    for r in lt.repos:
        for rel in _tracked(lt, r):
            p = os.path.join(r, rel)
            b = os.path.basename(rel)
            if b == ".mcp.json" and os.path.isfile(p):
                try:
                    m = json.load(open(p))
                except (OSError, ValueError):
                    continue
                add(m.get("mcpServers") if isinstance(m.get("mcpServers"), dict) else m, [os.path.dirname(p), r], _plugin_root(lt, p, r), True)
            elif b == "plugin.json" and _n(rel).endswith(".claude-plugin/plugin.json") and os.path.isfile(p):
                try:
                    m = json.load(open(p))
                except (OSError, ValueError):
                    continue
                proot = os.path.dirname(os.path.dirname(p))
                ms = m.get("mcpServers")
                if isinstance(ms, str):      # a path to a json file inside the plugin
                    try:
                        ms = json.load(open(os.path.join(proot, ms)))
                        ms = ms.get("mcpServers") if isinstance(ms.get("mcpServers"), dict) else ms
                    except (OSError, ValueError, AttributeError):
                        ms = None
                add(ms, [proot, r], proot, False)
    try:
        cj = json.load(open(os.path.join(lt.home, ".claude.json")))
    except (OSError, ValueError):
        cj = {}
    if isinstance(cj, dict):
        add(cj.get("mcpServers"), [lt.home], None, True)
        for proj, v in (cj.get("projects") or {}).items():
            if isinstance(v, dict):
                add(v.get("mcpServers"), [proj, lt.home], None, True)
    scripts = [(ids, _server_script(lt, spec, bases, proot)) for ids, spec, bases, proot in defs]
    yours = _yours_of(lt, sorted({sc for _, sc in scripts if sc}))
    vouched = {sc: _repo_vouches(lt, sc) for sc in {sc for _, sc in scripts if sc and yours.get(sc, False)}}
    out = {}
    for ids, sc in scripts:
        for i in ids:
            flags, scs = out.setdefault(i, ([], set()))
            flags.append(bool(sc) and yours.get(sc, False) and vouched.get(sc, False) and sc.startswith(home + os.sep))
            scs.add(sc)
    c["own_servers"] = {i: (f, next(iter(sc)) if len(sc) == 1 else None) for i, (f, sc) in out.items()}
    return c["own_servers"]


MIN_SERVER_DAYS = 3


@check(PID_T, "L4", 0)
def own_server_in_use(lt):
    """MCP servers whose code is the person's own, in use: the server command runs a file in their folders (not `npx <package>` or
    `uvx <package>` from a registry, not a package under node_modules; a plugin that only wraps an npx server is not theirs; a script git
    does not hold, or built into dist/ or build/, is theirs only when most of its repo's commits are by the person: a cloned open-source
    server run from its build output is not), and
    interactive sessions called its tools on >= 3 distinct days (UTC) in the window. Calls are matched by the server's tool-name id
    (mcp__<server>__*, mcp__plugin_<plugin>_<server>__*). Value = such servers."""
    servers = _own_servers(lt)
    own = {i: sc for i, (flags, sc) in servers.items() if flags and all(flags) and sc}     # tool-name id -> the server's file
    per = {}      # the server's file -> [calls, days]: one server started as a project's and as a plugin's is still one server
    for c in _calls(lt, [x["name"] for x in lt.calls if (x["name"] or "").startswith("mcp__")]):
        srv = c["name"].split("__")[1]
        if srv in own:
            e = per.setdefault(own[srv], [0, set()])
            e[0] += 1; e[1].add(int(c["t"] // DAY))
    kept = {k: v for k, v in per.items() if len(v[1]) >= MIN_SERVER_DAYS}
    return {"value": len(kept), "evidence": [f"{os.path.basename(k)}: {v[0]} calls on {len(v[1])} days" for k, v in sorted(kept.items())][:3]
            or [f"{len(own)} servers of their own are set up; {len(per)} were called, none on {MIN_SERVER_DAYS} days"],
            "note": "a server started with `python -m pkg`, `go run` or a binary with no file path is not matched to its code; a server a teammate "
                    "wrote or an installed plugin's is not theirs; a clone run from its build output is judged by the clone's commits; a registry server (npx, uvx) never counts however often it is called"}


@fixture(PID_T, "L4", 0)
def _(fx):
    w = _world(fx)
    day = lambda k: NOW - k * DAY
    for k in (2, 5):                                                     # kit-db: two days as the project's server ...
        fx.session(w["kit"], entrypoint="claude-vscode", start=day(k)).call("mcp__kit-db__query", {"sql": "select 1"}, "1")
    s = fx.session(w["kit"], entrypoint="claude-desktop", start=day(11))
    s.call("mcp__plugin_kit_kit-db__lookup", {"k": 1}, "v")              # ... and a third as the plugin's: one server, three days (counts)
    for k in (1, 3, 4, 6, 7):                                            # trap: a plugin of theirs that only wraps an npx server, called on 5 days
        fx.session(w["kit"], entrypoint="claude-vscode", start=day(k)).call("mcp__plugin_kit_web__fetch", {"url": "x"}, "page")
    for k in (1, 2, 3):
        fx.session(w["kit"], entrypoint="claude-vscode", start=day(k)).call("mcp__web__fetch", {"url": "y"}, "page")   # trap: the npx server itself
    # their own server called on two days only (trap), and one wired in ~/.claude.json (counts, 3 days)
    fx.repo("~/code/tools", {"srv/quick.mjs": "// a server\n", "srv/cli.py": "# a server\n", "data/notes.txt": "x\n",
                             ".mcp.json": json.dumps({"mcpServers": {
                                 "quick": {"command": "node", "args": ["srv/quick.mjs"]},
                                 "files": {"command": "npx", "args": ["-y", "@acme/server-files", fx.p("~/code/tools/data/notes.txt")]}}})}, by="home")
    for k in (1, 2):
        fx.session("~/code/tools", entrypoint="claude-vscode", start=day(k)).call("mcp__quick__ping", {}, "pong")
    for k in (1, 2, 3, 4):
        fx.session("~/code/tools", entrypoint="claude-vscode", start=day(k)).call("mcp__files__read", {}, "text")        # trap: a data file handed to an npx package
    # a server of theirs built into a folder git ignores (own repo: counts), and a cloned open-source server run from its untracked build output (trap)
    fx.commit("~/code/tools", {".gitignore": "build/\n"}, by="home")
    fx.file("~/code/tools/build/server.js", "// built server\n")
    fx.repo("~/clones/x-mcp", {"src/index.ts": "// upstream\n", "package.json": "{}"}, by="teammate")
    fx.commit("~/clones/x-mcp", {"src/tool.ts": "// more\n"}, by="teammate")
    fx.commit("~/clones/x-mcp", {"README.md": "x\n"}, by="home")                        # they once fixed a typo: still most commits are someone else's
    fx.file("~/clones/x-mcp/build/index.js", "// built from the clone\n")             # untracked: no history of its own
    fx.file("~/.claude.json", json.dumps({"mcpServers": {
        "pyserve": {"command": "uv", "args": ["run", fx.p("~/code/tools/srv/cli.py")]},
        "built": {"command": "node", "args": [fx.p("~/code/tools/build/server.js")]},
        "xmcp": {"command": "node", "args": [fx.p("~/clones/x-mcp/build/index.js")]},
        "absuv": {"command": fx.p("~/.local/bin/uv"), "args": ["--directory", fx.p("~/clones/x-mcp"), "run", "x-mcp"]}}}))
    fx.file("~/.local/bin/uv", "#!/bin/sh\n")                                          # a runtime binary in the home, in no repo
    for k in (1, 2, 3, 4):
        fx.session("~/code/tools", entrypoint="claude-vscode", start=day(k)).call("mcp__absuv__go", {}, "ok")      # trap: someone else's server run by an absolute uv
    for k in (1, 3, 8):
        fx.session("~/code/tools", entrypoint="claude-vscode", start=day(k)).call("mcp__pyserve__go", {}, "ok")
    for k in (2, 4, 6):
        fx.session("~/code/tools", entrypoint="claude-vscode", start=day(k)).call("mcp__built__go", {}, "ok")      # counts: their own built server, ignored by git
    for k in (1, 2, 3, 4, 5):
        fx.session("~/code/tools", entrypoint="claude-vscode", start=day(k)).call("mcp__xmcp__go", {}, "ok")       # trap: someone else's clone, 5 days
    h = fx.session(w["kit"], entrypoint="sdk-cli", start=day(12))
    h.call("mcp__kit-db__query", {"sql": "select 2"}, "2")               # trap: headless
    return {"value": 3}       # kit-db (3 days over both ids), pyserve (3 days) and built (3 days, own ignored build output)


def _tool_tests(lt, repo, tools):
    """True when a test file the person wrote in the repo names one of the repo's own tool files (by its name without extension,
    as a whole word): `git grep` finds the files once per repo."""
    memo = _cache(lt).setdefault("tooltests", {})
    if repo not in memo:
        stems = sorted({os.path.splitext(os.path.basename(p))[0] for p in tools if len(os.path.splitext(os.path.basename(p))[0]) > 1})
        hits = lt.git("-c", "core.quotepath=false", "grep", "-l", "-I", "-w", "-F", *sum((["-e", s] for s in stems), []), cwd=repo).splitlines() if stems else []
        tests = [os.path.join(repo, h) for h in hits if _is_test(repo, h) and os.path.realpath(os.path.join(repo, h)) not in {os.path.realpath(t) for t in tools}]
        yours = _yours_of(lt, tests) if tests else {}
        memo[repo] = any(yours.get(t) for t in tests)
    return memo[repo]


@check(PID_T, "L4", 2)
def tool_has_a_test(lt):
    """Runs of a check the repo declares (package.json script, Makefile/justfile target, CI step, language test command), in a repo whose
    hook script or MCP server file the person wrote AND where a test the person wrote names that file. A repo that merely holds
    the tool next to its own product's tests does not count. Interactive sessions."""
    arts = [a for a in _artifacts(lt) if a["own"] and a["kind"] in ("mcp", "hook")]
    by_repo = {}
    for a in arts:
        repo = lt.repo_of(a["path"])
        if repo:
            by_repo.setdefault(repo, []).append(a["path"])
    repos = {r for r, tools in by_repo.items() if _tool_tests(lt, r, tools)}
    n = 0
    repo_of = {}     # a session folder's repo, worked out once per folder
    declared = {}    # (command, repo) -> does the repo declare it as a check: the same command is run again and again
    for c in _calls(lt, ("Bash",), sidechain=True):
        cwd = (lt.sessions.get(c["s"]) or {}).get("cwd")
        if cwd not in repo_of:
            repo_of[cwd] = lt.repo_of(cwd)
        repo = repo_of[cwd]
        if repo in repos:
            k = (_cmd(c), repo)
            if k not in declared:
                declared[k] = lt.is_declared_check(k[0], repo)     # one combined pattern per repo
            n += declared[k]
    return {"value": n, "evidence": [f"{n} check runs in {len(repos)} repos with a test of their own tool"],
            "note": "a test that names the tool's file is taken as testing it; whether it passes, or covers what the tool does, is not read"}


@fixture(PID_T, "L4", 2)
def _(fx):
    pj = json.dumps({"scripts": {"test": "node --test"}})
    fx.repo("~/code/tools", {"tools/srv.mjs": "//server\n", "tools/srv.test.mjs": "import './srv.mjs'\n", ".mcp.json": json.dumps({"mcpServers": {"srv": {"command": "node", "args": ["tools/srv.mjs"]}}}), "package.json": pj}, by="home")
    fx.repo("~/code/product", {"hooks/guard.sh": "#!/bin/sh\n", "src/app.test.ts": "test('app', () => {})\n", "package.json": pj,        # trap: a big product repo with a hook of theirs and its own tests
                               ".claude/settings.json": json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "hooks/guard.sh"}]}]}})}, by="home")
    fx.repo("~/code/other", {"package.json": pj}, by="home")                     # trap: tests, but no tool of theirs here
    fx.repo("~/work/team-tools", {"tools/t.mjs": "//t\n", "tools/t.test.mjs": "import './t.mjs'\n", ".mcp.json": json.dumps({"mcpServers": {"t": {"command": "node", "args": ["tools/t.mjs"]}}}), "package.json": pj}, by="teammate")   # trap: not theirs
    a = fx.session("~/code/tools", entrypoint="claude-vscode"); a.call("Bash", {"command": "npm test"}, "ok"); a.call("Bash", {"command": "cd tools && npm test"}, "ok"); a.call("Bash", {"command": "ls"}, "ok")
    p = fx.session("~/code/product", entrypoint="claude-vscode")
    for _ in range(3):
        p.call("Bash", {"command": "npm test"}, "ok")
    b = fx.session("~/code/other", entrypoint="claude-vscode"); b.call("Bash", {"command": "npm test"}, "ok")
    c = fx.session("~/work/team-tools", entrypoint="claude-desktop"); c.call("Bash", {"command": "npm test"}, "ok")
    h = fx.session("~/code/tools", entrypoint="sdk-cli"); h.call("Bash", {"command": "npm test"}, "ok")
    return {"value": 2}


# ================================================================================ own-the-checks-the-agent-is-graded-by
PID_O = "own-the-checks-the-agent-is-graded-by"


def _repo_activity(lt):
    """{repo: [(first, last record time of a session that ran in it)]}: when Claude Code was working in each repo (any entrypoint)."""
    c = _cache(lt)
    if "spans" not in c:
        spans = {}
        for sid, s in lt.sessions.items():
            repo = lt.repo_of(s.get("cwd"))
            if repo and s.get("t0"):
                spans.setdefault(repo, []).append((s["t0"], s["t1"]))
        c["spans"] = spans
    return c["spans"]


def _bash_by_repo(lt):
    """{repo: [(time, raw call input)]} of Bash calls, in order, by the repo of the session's folder (the raw input: no parsing per call)."""
    c = _cache(lt)
    if "bashrepo" not in c:
        out, repo_of = {}, {}
        for x in _calls(lt, ("Bash",), sidechain=True, interactive=False):
            cwd = (lt.sessions.get(x["s"]) or {}).get("cwd")
            if cwd not in repo_of:
                repo_of[cwd] = lt.repo_of(cwd)
            if repo_of[cwd]:
                out.setdefault(repo_of[cwd], []).append((x["t"], x["input"]))
        c["bashrepo"] = out
    return c["bashrepo"]


def _edits_by_name(lt):
    """{file name: [(path, time)]} of every Edit/Write/NotebookEdit call's file (any entrypoint, sub-agents too), built once."""
    c = _cache(lt)
    if "editsbyname" not in c:
        edits = {}
        for x in _calls(lt, EDITS, sidechain=True, interactive=False):
            p = _call_path(lt, x)
            if p:
                edits.setdefault(os.path.basename(p), []).append((p, x["t"]))
        c["editsbyname"] = edits
    return c["editsbyname"]


@check(PID_O, "L2", 0)
def tests_without_claude(lt):
    """Test files the person's commits added or changed where Claude Code left no trace of writing them: no Edit/Write on the file and
    no Bash call naming it since the file's previous commit (or the start of the window), and the commit itself made while no
    session was running in the repo (an agent commits under the person's identity too). Test files = the repo's declared testpaths
    plus each runner's own naming (test_*.py, *.test.*, *_test.go, __tests__/, src/test/). Compared by the path inside the repo."""
    import bisect
    edits = _edits_by_name(lt)
    bash, spans = _bash_by_repo(lt), _repo_activity(lt)
    own = touched = 0
    for r in lt.repos:
        roots = _test_roots(r)
        calls = bash.get(r, [])
        times = [t for t, _ in calls]
        last = {}
        for cm in reversed(_git_commits(lt, r)):      # oldest first
            for st, path, old in cm["files"]:
                if cm["mine"] and st in "AMR" and _is_test(r, path, roots):
                    prev = last.get(path, 0)
                    name = os.path.basename(path)
                    hit = any(p.endswith("/" + path) and prev < t <= cm["t"] for p, t in edits.get(name, []))
                    hit = hit or any(a <= cm["t"] <= b for a, b in spans.get(r, []))
                    if not hit:
                        lo = bisect.bisect_right(times, max(prev, lt.start))
                        hit = any(name in raw for _, raw in calls[lo:bisect.bisect_right(times, cm["t"])])
                    touched += hit
                    own += (not hit)
                last[path] = cm["t"]
    return {"value": own, "evidence": [f"{own} test-file changes with no sign of Claude writing them, {touched} with one"],
            "note": "no record is not 'written by hand': another tool, a paste, a formatter or snapshot update, another laptop or a session whose call did not name the file also leave none"}


@fixture(PID_O, "L2", 0)
def _(fx):
    r = "~/work/shop"
    s = fx.session(r, entrypoint="claude-vscode")
    s.call("Edit", {"file_path": fx.p(r + "/src/a.test.ts"), "old_string": "a", "new_string": "b"})   # Claude wrote this one
    s.call("Bash", {"command": "cat > checks/guard.py <<'EOF'\nassert True\nEOF"})                      # trap: written through a shell command
    fx.repo(r, {"package.json": "{}", "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["checks"]\n', "src/a.ts": "a", "src/a.test.ts": "t",
                "src/b.spec.ts": "t1", "checks/guard.py": "g", "checks/hand.py": "h", "README.md": "r", "evals/notes.md": "n"})
    fx.commit(r, {"src/b.spec.ts": "t2"}, by="work")                       # a second untouched change to a test
    fx.commit(r, {"src/c.test.ts": "t"}, by="teammate")                    # trap: a teammate's test
    a = "~/work/agentic"                                                   # trap: the person's commit made while a session ran in the repo
    live = fx.session(a, entrypoint="claude-vscode", start=NOW - 30)
    for _ in range(12):
        live.typed("keep going")
    fx.repo(a, {"src/z.test.ts": "t"})
    return {"value": 3}      # b.spec.ts twice, checks/hand.py; a.test.ts has a Claude edit, guard.py a Bash call; evals/ is no test folder; agentic: a session was running


def _first_creates(lt):
    """{(session, path): time} of the first Write that CREATED the file (no earlier Read of it in the session), in calls that did not fail."""
    first = _first_reads(lt)
    out = {}
    for c in _calls(lt, ("Write",), sidechain=True):
        p = None if c["err"] else _call_path(lt, c)
        if p and first.get((c["s"], p), float("inf")) >= c["t"]:
            out.setdefault((c["s"], p), c["t"])
    return out


@check(PID_O, "L2", 1)
def tests_first(lt):
    """Interactive sessions that CREATED a test file (a Write with no earlier Read of the path: a new file, not a patch to an existing test)
    before their first edit of a source file (a code file that is not a test; docs, data, config excluded). Test = declared testpaths
    or the runner's own naming."""
    created = _first_creates(lt)
    per = {}
    for c in _calls(lt, ("Edit", "Write", "MultiEdit"), sidechain=True):
        p = _call_path(lt, c)
        if not p:
            continue
        repo = lt.repo_of(p)
        rel = os.path.relpath(p, repo) if repo else p
        if _is_test(repo or "", rel):
            if (c["s"], p) in created:
                per.setdefault(c["s"], {}).setdefault("test", created[(c["s"], p)])
        elif _is_source(rel):
            per.setdefault(c["s"], {}).setdefault("src", c["t"])
    both = [s for s, d in per.items() if len(d) == 2]
    first = [s for s in both if per[s]["test"] < per[s]["src"]]
    return {"value": len(first), "evidence": [f"{len(first)} of {len(both)} sessions that created a test and edited code created the test first"],
            "note": "order of first edits only; it does not show the test failed first, and a test written outside the sessions' tool calls is not seen"}


@fixture(PID_O, "L2", 1)
def _(fx):
    r = "~/work/shop"
    fx.repo(r, {"package.json": "{}", "src/e.test.ts": "old", "tests/test_old.py": "old"})
    a = fx.session(r, entrypoint="claude-desktop")
    a.call("Write", {"file_path": fx.p(r + "/src/a.test.ts"), "content": "t"}); a.call("Edit", {"file_path": fx.p(r + "/src/a.ts"), "old_string": "a", "new_string": "b"})
    b = fx.session(r, entrypoint="claude-vscode")
    b.call("Write", {"file_path": fx.p(r + "/tests/test_x.py"), "content": "t"}); b.call("Write", {"file_path": fx.p(r + "/x.py"), "content": "c"})
    c = fx.session(r, entrypoint="claude-vscode")
    c.call("Edit", {"file_path": fx.p(r + "/src/b.ts"), "old_string": "a", "new_string": "b"}); c.call("Write", {"file_path": fx.p(r + "/src/b.test.ts"), "content": "t"})   # trap: code first
    d = fx.session(r, entrypoint="claude-vscode")
    d.call("Edit", {"file_path": fx.p(r + "/README.md"), "old_string": "a", "new_string": "b"}); d.call("Write", {"file_path": fx.p(r + "/src/c.test.ts"), "content": "t"})   # trap: a doc is not source
    e = fx.session(r, entrypoint="claude-vscode")
    e.call("Read", {"file_path": fx.p(r + "/src/e.test.ts")}); e.call("Edit", {"file_path": fx.p(r + "/src/e.test.ts"), "old_string": "old", "new_string": "fixed"})   # trap: patches a stale assertion first
    e.call("Edit", {"file_path": fx.p(r + "/src/e.ts"), "old_string": "a", "new_string": "b"})
    f = fx.session(r, entrypoint="claude-vscode")
    f.call("Read", {"file_path": fx.p(r + "/tests/test_old.py")}); f.call("Write", {"file_path": fx.p(r + "/tests/test_old.py"), "content": "new"})   # trap: rewrites an existing test first
    f.call("Edit", {"file_path": fx.p(r + "/src/f.ts"), "old_string": "a", "new_string": "b"})
    h = fx.session(r, entrypoint="sdk-cli")
    h.call("Write", {"file_path": fx.p(r + "/src/d.test.ts"), "content": "t"}); h.call("Edit", {"file_path": fx.p(r + "/src/d.ts"), "old_string": "a", "new_string": "b"})   # trap: headless
    return {"value": 2}


COVERAGE = re.compile(r"\[tool\.coverage|\[coverage:|--cov\b|--cov[=-]|--coverage\b|collectCoverage|coverageThreshold|coverageProvider|"
                      r"\bcoverage\s*:|\bc8\b|\bnyc\b|llvm-cov|tarpaulin|\s-cover\b|-coverprofile|jacoco|simplecov|\bkover\b")
COVERAGE_FILES = re.compile(r"(^|/)(\.coveragerc|\.?codecov\.ya?ml|\.nycrc(\.json)?|\.c8rc(\.json)?)$")
CONFIG_WITH_COVERAGE = re.compile(r"(^|/)(jest|vitest|vite)\.config\.[a-z]+$")
CONFIG_NAMES = ("package.json", "pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "Makefile", "makefile", "GNUmakefile", "justfile",
                "Justfile", ".justfile", ".pre-commit-config.yaml", ".gitlab-ci.yml")


def _config_named(lt, repo):
    """The repo's files (_tracked) whose name is a coverage / jest-vitest-vite / mutation config: the three patterns above, searched
    once over the whole list joined by newlines (one pass in C) instead of three searches per file. Order kept."""
    rx = _cache(lt).get("configrx")
    if rx is None:   # MUTATION_FILES is defined further down: built at first use
        assert all(r.pattern.startswith("(^|/)") and r.pattern.endswith("$") for r in (COVERAGE_FILES, CONFIG_WITH_COVERAGE, MUTATION_FILES))
        rx = _cache(lt)["configrx"] = re.compile(r"^(?:.*/)?(?:%s)$" % "|".join(r.pattern[len("(^|/)"):-1] for r in (COVERAGE_FILES, CONFIG_WITH_COVERAGE, MUTATION_FILES)), re.M)
    rels = _tracked(lt, repo)
    text = "\n".join(rels).replace("\\", "/")     # _n(rel) for each; a name never holds a newline (git quotes it)
    out, i, last = [], 0, 0
    for m in rx.finditer(text):
        i += text.count("\n", last, m.start()); last = m.start()     # the line's index, counted from the previous match
        out.append(rels[i])
    return out


def _config_files(lt, repo):
    """The files of a repo where a test, coverage or mutation setting is declared: its project, build and CI files, and the tracked
    files of the extra tool configs, as absolute paths."""
    memo = _cache(lt).setdefault("configfiles", {})
    if repo not in memo:
        out = [os.path.join(repo, n) for n in CONFIG_NAMES if os.path.exists(os.path.join(repo, n))]
        out += glob.glob(os.path.join(repo, ".github", "workflows", "*.y*ml"))
        out += [os.path.join(repo, rel) for rel in _config_named(lt, repo)]
        memo[repo] = [p for p in dict.fromkeys(out) if os.path.isfile(p)]
    return memo[repo]


def _code_edited_in(lt):
    """The repos where the agent edited a source file (a code file that is not a test) in an interactive session."""
    c = _cache(lt)
    if "codeedits" not in c:
        repos, seen = set(), {}
        for x in _calls(lt, ("Edit", "Write", "MultiEdit"), sidechain=True):
            p = _call_path(lt, x)
            if not p:
                continue
            d = os.path.dirname(p)
            if d not in seen:
                seen[d] = lt.repo_of(d)
            repo = seen[d]
            if repo and _is_source(p) and not _is_test(repo, os.path.relpath(p, repo)):
                repos.add(repo)
        c["codeedits"] = repos
    return c["codeedits"]


@check(PID_O, "L3", 1)
def coverage_set(lt):
    """Repos where the agent edited code and a coverage setting sits in a file the person wrote (the one ownership rule, _yours_of): [tool.coverage] / --cov
    in pyproject, setup.cfg, tox, Makefile or a CI step; a jest/vitest coverage setting in package.json or its config file; or
    a coverage config file. A team's or a cloned library's coverage config, in a repo the agent never changed code in, is not counted."""
    hit = []
    for r in lt.repos:
        if r not in _code_edited_in(lt):
            continue
        found = [p for p in _config_files(lt, r)
                 if (COVERAGE_FILES.search(_n(p)) or COVERAGE.search(_read(p)) or (CONFIG_WITH_COVERAGE.search(_n(p)) and "coverage" in _read(p, 50000)))
                 and not MUTATION_FILES.search(_n(p))]
        if found:
            yours = _yours_of(lt, found)
            if any(yours.get(p) for p in found):
                hit.append(r)
    return {"value": len(hit), "evidence": [f"{len(hit)} of {len(lt.repos)} repos"], "note": "coverage is read from a file the person wrote, in a repo the agent changed code in; that it covers the lines the agent changed is not read"}


@fixture(PID_O, "L3", 1)
def _(fx):
    fx.repo("~/code/a", {"pyproject.toml": '[tool.pytest.ini_options]\naddopts = "--cov=src"\n'})
    fx.repo("~/code/b", {"package.json": json.dumps({"name": "b", "jest": {"collectCoverage": True}})})
    fx.repo("~/code/c", {".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: go test -cover ./...\n"})
    fx.repo("~/code/d", {"pyproject.toml": "[tool.pytest.ini_options]\ntestpaths = ['tests']\n", "README.md": "we care about coverage"})   # trap: pytest only
    fx.repo("~/code/e", {".coveragerc": "[run]\nsource = src\n"})
    fx.repo("~/code/f", {"vite.config.ts": "export default {}", "package.json": json.dumps({"scripts": {"test": "vitest"}})})        # trap: config without coverage
    fx.repo("~/code/g", {"pyproject.toml": '[tool.pytest.ini_options]\naddopts = "--cov=src"\n'}, by="teammate")                       # trap: a team's coverage config
    fx.repo("~/code/h", {"pyproject.toml": '[tool.pytest.ini_options]\naddopts = "--cov=src"\n'})                                       # trap: the agent never changed code here
    for x in "abcdefg":
        s = fx.session("~/code/" + x, entrypoint="claude-vscode")
        s.typed("hi"); s.call("Edit", {"file_path": fx.p(f"~/code/{x}/src/main.py"), "old_string": "a", "new_string": "b"})
    fx.session("~/code/h", entrypoint="claude-vscode").typed("hi")
    return {"value": 4}


MUTATION = re.compile(r"\b(stryker|mutmut|pitest|cosmic-ray|cargo[ -]mutants)\b|vendor/bin/infection|infection/infection")
MUTATION_FILES = re.compile(r"(^|/)(stryker\.conf\.[a-z]+|\.mutmut[\w.-]*|mutmut_config\.py|cosmic-ray\.toml|infection\.json5?)$")


def _without_comments(text):
    return "\n".join(x for x in text.splitlines() if not x.lstrip().startswith(("#", "//", ";")))


@check(PID_O, "L4", 0)
def mutation_tests(lt):
    """Repos the person works in where a file the person wrote (the one ownership rule, _yours_of) names a mutation-testing tool in a setting, not a comment:
    package.json, pyproject/tox/setup.cfg, Makefile/justfile, CI workflow, pre-commit, or the tool's own config file (Stryker, mutmut,
    PIT, cosmic-ray, cargo-mutants, Infection). A README wish or a TODO comment is not a setting."""
    hit = []
    for r in lt.repos:
        found = [p for p in _config_files(lt, r) if MUTATION_FILES.search(_n(p)) or MUTATION.search(_without_comments(_read(p)))]
        if found:
            yours = _yours_of(lt, found)
            if any(yours.get(p) for p in found):
                hit.append(r)
    return {"value": len(hit), "evidence": [f"{len(hit)} of {len(lt.repos)} repos"], "note": "a tool outside the ones named in its ecosystem's own config is not seen"}


@fixture(PID_O, "L4", 0)
def _(fx):
    fx.repo("~/code/a", {"package.json": json.dumps({"devDependencies": {"@stryker-mutator/core": "^8"}, "scripts": {"test:mutation": "stryker run"}})})
    fx.repo("~/code/b", {"tox.ini": "[testenv:mut]\ncommands = mutmut run\n"})
    fx.repo("~/code/c", {"package.json": json.dumps({"scripts": {"codegen:mutations": "graphql-codegen"}}), "src/mutations.ts": "export const m = 1"})   # trap: GraphQL mutations
    fx.repo("~/code/d", {"README.md": "We should try mutation testing", "pyproject.toml": "[project]\nname='d'\n# TODO: mutation testing with mutmut and infection\n"})   # trap: a wish and a TODO
    fx.repo("~/code/e", {"stryker.conf.json": "{}"})
    fx.repo("~/code/f", {"stryker.conf.json": "{}", "package.json": json.dumps({"devDependencies": {"@stryker-mutator/core": "^8"}})}, by="teammate")   # trap: a team's setup
    fx.repo("~/code/g", {"Makefile": "# run mutmut run before release, one day\ntest:\n\tpytest\n"})                                              # trap: a comment in a Makefile
    fx.repo("~/code/h", {"Makefile": "mutation:\n\tvendor/bin/infection --threads=4\n"})
    for x in "abcdefgh":
        fx.session("~/code/" + x, entrypoint="claude-vscode").typed("hi")
    return {"value": 4}


# ================================================================================ second-look
PID_S = "second-look"


@check(PID_S, "L2", 2)
def forks_and_side_asks(lt):
    """/subtask, /btw, /fork typed in any interactive entrypoint (transcript, or terminal history when the transcript lacks it)."""
    cs = _commands(lt, ("/subtask", "/btw", "/fork"))
    per = {}
    for c in cs:
        per[c["name"]] = per.get(c["name"], 0) + 1
    return {"value": len(cs), "evidence": [f"{k} x{v}" for k, v in sorted(per.items())], "note": "that a fork was used instead of a fresh look is not read"}


@fixture(PID_S, "L2", 2)
def _(fx):
    fx.session("~/work/shop", entrypoint="claude-desktop").command("/btw", "what is x")
    fx.session("~/work/shop", entrypoint="claude-vscode").command("/subtask", "check the diff")
    fx.session("~/work/shop", entrypoint="cli").command("/fork")               # terminal: transcript + history, counted once
    fx.session("~/work/shop", entrypoint="cli").command("/forklift")           # trap: a different command
    fx.session("~/work/shop", entrypoint="sdk-cli").command("/btw", "x")       # trap: headless
    return {"value": 3}


READ_ONLY_BLOCK = re.compile(r"\b(Write|Edit|MultiEdit|NotebookEdit|Bash)\b")


def _read_only_agent(path):
    """An agent that cannot change anything: a tools list without Write / Edit / MultiEdit / NotebookEdit / Bash (a shell can write), or no
    tools list and those tools disallowed (all five: an agent without a list inherits every tool)."""
    fm = _fm(_read(path, 6000))
    tools, dis = fm.get("tools"), fm.get("disallowedTools") or fm.get("disallowed-tools")
    if tools:
        return not READ_ONLY_BLOCK.search(tools)
    return bool(dis and {m.group(1) for m in READ_ONLY_BLOCK.finditer(dis)} >= {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"})


def _agent_reads(path, cwd):
    """The files a sub-agent's own log shows it Read, as absolute paths (the log is the agent's jsonl beside its meta file)."""
    out = set()
    try:
        fh = open(path, errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            if '"name":"Read"' not in line:
                continue
            try:
                blocks = (json.loads(line).get("message") or {}).get("content") or []
            except ValueError:
                continue
            for b in blocks if isinstance(blocks, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Read":
                    p = _n((b.get("input") or {}).get("file_path") or "")
                    if p:
                        out.add(p if _is_abs(p) else _n(cwd or "").rstrip("/") + "/" + p)
    return out


@check(PID_S, "L3", 0)
def read_only_agent_of_your_own(lt):
    """Runs of an agent the person wrote (the one ownership rule, not installed or copied) that cannot change anything (see _read_only_agent) and that,
    in the run, Read a file its session had already written or edited: a look at the agent's work, not a planner or explorer working
    before the edits. A run is matched to the agent by its meta file (agentType, plugin namespace) and its Agent call."""
    ro = [a for a in _artifacts(lt) if a["own"] and a["kind"] == "agent" and _read_only_agent(a["path"])]
    ids = _own_ids(lt, ro, "agent")
    if not ids:
        return {"value": 0, "evidence": [], "note": "no read-only agent of their own"}
    by_id = {c["id"]: c for c in _calls(lt, ("Agent", "Task"), sidechain=True)}
    edits = {}
    for c in _calls(lt, EDITS, sidechain=True):
        p = _call_path(lt, c)
        if p:
            edits.setdefault(c["s"], []).append((c["t"], p))
    runs, names = 0, set()
    for m in lt.meta:
        ns, short = _split_call(m.get("agentType"))
        call = by_id.get(m.get("toolUseId"))
        if (ns, short) not in ids or not call or not lt.interactive(m["_session"]):
            continue
        written = {p for t, p in edits.get(m["_session"], []) if t < call["t"]}
        if written and written & _agent_reads(m["_file"][:-len(".meta.json")] + ".jsonl", (lt.sessions.get(m["_session"]) or {}).get("cwd")):
            runs += 1; names.add(short)
    return {"value": runs, "evidence": [f"{runs} runs of {len(names)} own read-only agents on files their session had edited"],
            "note": "a reviewer by what it did (read what was just written, changed nothing); a skill they wrote, or a review done by Bash, is not read"}


@fixture(PID_S, "L3", 0)
def _(fx):
    w = _world(fx)
    fx.commit(w["kit"], {"agents/lister.md": "---\nname: lister\ndisallowedTools: Write, Edit, MultiEdit, NotebookEdit, Bash\n---\nx\n",
                         "agents/sneaky.md": "---\nname: sneaky\ntools: Read, Bash\n---\nx\n",
                         "agents/half.md": "---\nname: half\ndisallowedTools: Write, Edit\n---\nx\n"}, by="home")
    app = fx.p(w["kit"] + "/src/app.ts")
    s = fx.session(w["kit"], entrypoint="claude-vscode")
    s.call("Edit", {"file_path": app, "old_string": "a", "new_string": "b"})
    s.subagent("kit:checker", "look at the change", calls=[("Read", {"file_path": app}, "code")])          # counts: reads what the session just edited
    s.subagent("kit:lister", "look elsewhere", calls=[("Read", {"file_path": fx.p(w["kit"] + "/src/other.ts")}, "code")])   # trap: reads a file nobody edited
    s.subagent("kit:sneaky", "look at the change", calls=[("Read", {"file_path": app}, "code")])          # trap: has a shell
    s.subagent("kit:half", "look at the change", calls=[("Read", {"file_path": app}, "code")])            # trap: Bash not disallowed
    s.subagent("other:checker", "look at the change", calls=[("Read", {"file_path": app}, "code")])       # trap: another plugin's agent
    s.subagent("kit:writer", "look at the change", calls=[("Read", {"file_path": app}, "code")])          # trap: can write
    e = fx.session(w["kit"], entrypoint="claude-vscode")
    e.subagent("kit:checker", "explore before editing", calls=[("Read", {"file_path": fx.p(w["kit"] + "/src/new.ts")}, "code")])   # trap: before any edit
    e.call("Edit", {"file_path": fx.p(w["kit"] + "/src/new.ts"), "old_string": "a", "new_string": "b"})
    h = fx.session(w["kit"], entrypoint="sdk-cli")
    h.call("Edit", {"file_path": app, "old_string": "a", "new_string": "b"})
    h.subagent("kit:checker", "look at the change", calls=[("Read", {"file_path": app}, "code")])         # trap: headless
    return {"value": 1}


REVIEW_EVENTS = ("Stop", "SubagentStop")
CLAUDE_P = re.compile(r"\bclaude\s+(-p|--print)\b")


def _hook_active(lt, h):
    """A hook Claude Code runs: one in a settings file, or in a plugin's hooks.json only while that plugin is enabled."""
    if h["scope"] != "plugin":
        return True
    name = _plugin_name(h["plugin_root"]) if h["plugin_root"] else None
    return bool(name) and any(k.split("@")[0] == name and v for k, v in _enabled_plugins(lt).items())


def _starts_second_model(h):
    """A hook command that runs `claude -p` itself or through a script, or a prompt / agent hook; with who wrote it: their settings file
    or a script git says is theirs. Returns True / False."""
    if h["type"] in ("agent", "prompt"):
        return bool(h["config_yours"])
    if CLAUDE_P.search(h["command"]):
        return bool(h["config_yours"]) or bool(h["script"] and h["script_yours"])
    return bool(h["script"] and h["script_yours"] and CLAUDE_P.search(_read(h["script"], 100000)))


@check(PID_S, "L4", 0)
def stop_hook_runs_a_reviewer(lt):
    """Stop / SubagentStop blocks that Claude Code recorded (hook_blocking_error) from a hook the person wrote and that runs `claude -p`
    (in its command or its script): the agent was held at the end until that run let it through. A hook that only ran, a plugin's
    hook that is not enabled, and a Stop hook that never blocked are not counted."""
    own = {h["command"].strip() for h in _hooks(lt) if h["event"] in REVIEW_EVENTS and h["type"] == "command" and h["command"]
           and _hook_active(lt, h) and _starts_second_model(h)}
    n = 0
    for r in lt.grep('"hook_blocking_error"'):
        a = r.get("attachment") or {}
        be = a.get("blockingError")
        if a.get("type") == "hook_blocking_error" and a.get("hookEvent") in REVIEW_EVENTS and isinstance(be, dict) and (be.get("command") or "").strip() in own:
            n += 1
    return {"value": n, "evidence": [f"{n} Stop blocks from {len(own)} reviewer hooks of their own"],
            "note": "a prompt or agent hook (no command in the block record) is not matched to its block; that the second model reviewed rather than summarised is not read"}


@fixture(PID_S, "L4", 0)
def _(fx):
    r = "~/work/shop"
    cmd, script = "claude -p 'review the diff and say PASS'", fx.p("~/code/dotfiles/hooks/review.sh")
    fx.repo(r, {".claude/settings.json": json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": cmd}]}]}})})
    fx.repo("~/code/dotfiles", {"hooks/review.sh": "#!/bin/sh\nclaude -p \"review\"\n", "hooks/notify.sh": "#!/bin/sh\necho done\n"}, by="home")
    fx.repo("~/code/plug", {".claude-plugin/plugin.json": json.dumps({"name": "plug"}), "hooks/hooks.json": json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "claude -p 'x'"}]}]}})}, by="home")
    fx.settings("user", {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": script}, {"type": "command", "command": fx.p("~/code/dotfiles/hooks/notify.sh")},
                                                        {"type": "command", "command": "echo done"}, {"type": "command", "command": "claude -p 'summarise the session' > /tmp/s.txt"}]}]}})
    s = fx.session(r, entrypoint="claude-vscode"); s.typed("hi")
    block = lambda ev, c: s.attachment("hook_blocking_error", hookName=ev, toolUseID="t", hookEvent=ev, blockingError={"blockingError": "not yet", "command": c})
    block("Stop", cmd)                                      # counts: a claude -p reviewer held the stop
    block("Stop", script)                                   # counts: a script of theirs that runs claude -p
    block("Stop", fx.p("~/code/dotfiles/hooks/notify.sh"))  # trap: a script that does not call a model
    block("Stop", "echo done")                              # trap
    block("PostToolUse", cmd)                               # trap: not the end of the work
    block("Stop", "claude -p 'x'")                          # trap: the hook sits in a plugin that is not enabled
    h = fx.session(r, entrypoint="sdk-cli"); h.typed("go")
    return {"value": 2}


@check(PID_S, "L4", 2)
def stop_hook_you_wrote(lt):
    """Stop / SubagentStop hooks the person wrote that start a second model: an agent / prompt hook in their own settings file, or a command that
    runs `claude -p` (inline, or in a script git says is theirs). A hook that notifies, logs or guards a commit is not a reviewer. Hooks of
    a plugin count only while it is enabled."""
    hs = [h for h in _hooks(lt) if h["event"] in REVIEW_EVENTS and _hook_active(lt, h) and _starts_second_model(h)]
    return {"value": len(hs), "evidence": [os.path.basename(h["script"]) if h["script"] else h["type"] + " hook" for h in hs[:3]],
            "note": "a hook that calls a binary (not a file in a repo) is not matched to an author; that it blocks until the second model passes is read only by stop_hook_runs_a_reviewer"}


@fixture(PID_S, "L4", 2)
def _(fx):
    fx.repo("~/code/dotfiles", {"hooks/stop-check.sh": "#!/bin/sh\nclaude -p 'review the diff'\n", "hooks/pre.sh": "#!/bin/sh\nclaude -p x\n", "hooks/notify.sh": "#!/bin/sh\necho done\n"}, by="home")
    fx.repo("~/work/team", {"hooks/team-stop.sh": "#!/bin/sh\nclaude -p 'review'\n"}, by="teammate")
    fx.repo("~/code/plug", {".claude-plugin/plugin.json": json.dumps({"name": "plug"}), "hooks/stop.sh": "#!/bin/sh\nclaude -p 'review'\n",
                            "hooks/hooks.json": json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/stop.sh"}]}]}})}, by="home")
    fx.settings("user", {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": fx.p("~/code/dotfiles/hooks/stop-check.sh")},     # theirs, runs claude -p
                                                     {"type": "command", "command": fx.p("~/work/team/hooks/team-stop.sh")},        # trap: a teammate's
                                                     {"type": "command", "command": fx.p("~/code/dotfiles/hooks/notify.sh")},        # trap: a notification
                                                     {"type": "command", "command": "echo done"},                                    # trap: no script
                                                     {"type": "prompt", "prompt": "did you finish"}]}],                              # theirs: a prompt in their settings
                                   "PreToolUse": [{"hooks": [{"type": "command", "command": fx.p("~/code/dotfiles/hooks/pre.sh")}]}]}})   # trap: not a Stop hook
    for p in ("~/work/team", "~/code/dotfiles", "~/code/plug"):
        fx.session(p, entrypoint="claude-vscode").typed("hi")
    return {"value": 2}


# ================================================================================ reset-with-a-handoff
PID_H = "reset-with-a-handoff"


def _hook_starts(lt, source):
    """SessionStart hook runs for one source (clear, fork, resume, startup), from hook_success attachments of interactive sessions."""
    return [a for a in _att(lt, "hook_success") if _field(a["data"], "hookName") == "SessionStart:" + source]


def _cmds_or_starts(lt, names, source):
    cmds = _commands(lt, names)
    starts = [a for a in _hook_starts(lt, source) if not any(abs(a["t"] - c["t"]) <= 30 for c in cmds)]   # right after a command: the same event
    return cmds, starts


@check(PID_H, "L2", 0)
def started_clean(lt):
    """/clear typed (any interactive entrypoint; transcript or terminal history), plus sessions a SessionStart hook reports as
    started by 'clear' that no /clear record accounts for."""
    cmds, starts = _cmds_or_starts(lt, ("/clear",), "clear")
    typed = {t["s"] for t in lt.typed if lt.interactive(t["s"])}
    seen = len({c["s"] for c in cmds} | {a["s"] for a in starts})
    return {"value": len(cmds) + len(starts), "evidence": [f"{len(cmds) + len(starts)} times in {seen} of your {len(typed)} typed sessions",
                                                           f"{len(cmds)} /clear commands in the session logs", f"{len(starts)} more from SessionStart:clear"],
            "note": "a new chat started by closing and reopening is not a /clear and leaves no mark"}


@fixture(PID_H, "L2", 0)
def _(fx):
    now = NOW
    fx.session("~/work/shop", entrypoint="claude-desktop").command("/clear")
    fx.session("~/work/shop", entrypoint="cli").command("/clear")                       # terminal: transcript + history, once
    v = fx.session("~/work/shop", entrypoint="claude-vscode", start=now - 2 * DAY)       # a clear only a SessionStart hook saw
    v.attachment("hook_success", hookName="SessionStart:clear", hookEvent="SessionStart", content="", stdout="", stderr="", exitCode=0)
    v.typed("go")
    s = fx.session("~/work/shop", entrypoint="claude-vscode", start=now - 1 * DAY)
    s.attachment("hook_success", hookName="SessionStart:startup", hookEvent="SessionStart", content="", stdout="", stderr="", exitCode=0)   # trap: a plain start
    s.command("/clear-cache")                                                             # trap: another command
    fx.session("~/work/shop", entrypoint="sdk-cli").command("/clear")                     # trap: headless
    # 11-06: a terminal /clear is written in the NEW session, while history.jsonl keeps the session left behind, 40 s earlier
    # here (past any time window): the two records of one /clear were counted as two
    old = fx.session("~/work/shop", entrypoint="cli", start=now - 4 * DAY)
    old.typed("before")
    old._history("/clear")                                                                  # history keeps the session it left
    new = fx.session("~/work/shop", entrypoint="cli", start=old.t + 40)
    new._write({"type": "user", "message": {"role": "user", "content": "<command-name>/clear</command-name>\n            <command-message>clear</command-message>\n            <command-args></command-args>"}})
    new.typed("after the clear")
    return {"value": 4}


@check(PID_H, "L2", 3)
def rewind_or_branch(lt):
    """/rewind, /fork, /branch typed (any interactive entrypoint), plus sessions a SessionStart hook reports as started by 'fork'
    that no command accounts for."""
    cmds, starts = _cmds_or_starts(lt, ("/rewind", "/fork", "/branch"), "fork")
    return {"value": len(cmds) + len(starts), "evidence": [f"{len(cmds)} commands", f"{len(starts)} fork starts seen by a hook"],
            "note": "Esc-Esc rewind leaves no record: a floor"}


@fixture(PID_H, "L2", 3)
def _(fx):
    now = NOW
    fx.session("~/work/shop", entrypoint="claude-desktop").command("/rewind")
    fx.session("~/work/shop", entrypoint="claude-vscode").command("/branch", "x")
    t = fx.session("~/work/shop", entrypoint="cli", start=now - 3 * DAY); t.command("/fork")
    f2 = fx.session("~/work/shop", entrypoint="cli", start=t.t + 2)                       # the session that /fork started: the same event
    f2.attachment("hook_success", hookName="SessionStart:fork", hookEvent="SessionStart", content="", stdout="", stderr="", exitCode=0)
    f3 = fx.session("~/work/shop", entrypoint="claude-vscode", start=now - 2 * DAY)       # a fork nothing else accounts for
    f3.attachment("hook_success", hookName="SessionStart:fork", hookEvent="SessionStart", content="", stdout="", stderr="", exitCode=0)
    fx.session("~/work/shop", entrypoint="claude-vscode", start=now - 1 * DAY).command("/rewindx")   # trap
    fx.session("~/work/shop", entrypoint="sdk-cli", start=now - 1 * DAY).command("/fork")            # trap: headless
    return {"value": 4}


def _hook_text(att):
    """Did a hook print text for the model? Its stdout is text, or JSON with a non-empty additionalContext; empty output and `{}` are not."""
    out = (att.get("stdout") or "").strip()
    if not out:
        return False
    try:
        d = json.loads(out)
    except ValueError:
        return True
    if isinstance(d, dict):
        ctx = (d.get("hookSpecificOutput") or {}).get("additionalContext") if isinstance(d.get("hookSpecificOutput"), dict) else None
        return bool(isinstance(ctx, str) and ctx.strip())
    return bool(d)


def _is_command_of(cmd, hooks, home):
    """Is this hook command (as Claude Code recorded it) one of these hooks: the same command line, or one naming the same script file?"""
    cmd = (cmd or "").strip()
    for h in hooks:
        if cmd == h["command"].strip():
            return True
        if h["script"] and (h["script"] in cmd or h["script"].replace(home, "~", 1) in cmd):
            return True
    return False


@check(PID_H, "L3", 1)
def session_start_hook_brings_text(lt):
    """Interactive sessions that began (startup or /clear) with text printed by a SessionStart hook the person wrote (a script git says
    is theirs, or an inline command in their own settings), before their first typed message. Tied to the hook by the command Claude
    Code records on the run; a plugin's hook that injects text into every session, a hook that printed nothing or `{}`, and a
    resumed session are not counted."""
    own = [h for h in _hooks(lt) if h["event"] == "SessionStart" and ((h["script"] and h["script_yours"]) or (not h["script"] and h["command"] and h["config_yours"]))]
    if not own:
        return {"value": 0, "evidence": [], "note": "no SessionStart hook of their own in the settings files read"}
    first = {s: ms[0]["t"] for s, ms in _typed_by_session(lt).items()}
    home = os.path.realpath(lt.home)
    sess = set()
    for r in lt.grep('"hook_success"'):
        a = r.get("attachment") or {}
        sid = r["_sid"]
        if a.get("type") != "hook_success" or not str(a.get("hookName")).startswith("SessionStart:") or r["_sc"] or sid not in first or not lt.interactive(sid):
            continue
        t = epoch(r.get("timestamp"))
        if a.get("hookName") in ("SessionStart:startup", "SessionStart:clear") and t is not None and t < first[sid] \
                and _hook_text(a) and _is_command_of(a.get("command"), own, home):
            sess.add(sid)
    return {"value": len(sess), "evidence": [f"{len(sess)} sessions began with text from their own hook", f"{len(own)} own SessionStart hooks"],
            "note": "that the text is a handoff, rather than any text the hook prints, is not read"}


@fixture(PID_H, "L3", 1)
def _(fx):
    now = NOW
    own = fx.p("~/code/dotfiles/hooks/handoff.sh")
    fx.repo("~/code/dotfiles", {"hooks/handoff.sh": "#!/bin/sh\ncat HANDOFF.md\n"}, by="home")
    fx.settings("user", {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": own}]}]}})
    ok = lambda s, name="SessionStart:startup", out="resume at step 3", cmd=own: s.attachment(
        "hook_success", hookName=name, hookEvent="SessionStart", content="", stdout=out, stderr="", exitCode=0, command=cmd)
    a = fx.session("~/work/shop", entrypoint="claude-vscode", start=now - 3 * DAY)
    ok(a, out=json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "Last time: tests were failing in a.ts"}})); a.typed("continue")   # counts
    b = fx.session("~/work/shop", entrypoint="claude-desktop", start=now - 3 * DAY + 100)
    ok(b); b.typed("continue")                                                               # counts: plain text
    c = fx.session("~/work/shop", entrypoint="claude-vscode", start=now - 3 * DAY + 200)       # two runs, one session
    ok(c, name="SessionStart:clear"); ok(c, out="more"); c.typed("go")
    d = fx.session("~/work/shop", entrypoint="claude-vscode", start=now - 2 * DAY)              # trap: nothing printed
    ok(d, out=""); d.typed("go")
    d2 = fx.session("~/work/shop", entrypoint="claude-vscode", start=now - 2 * DAY + 50)        # trap: `{}`
    ok(d2, out="{}"); ok(d2, out=json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ""}})); d2.typed("go")
    e = fx.session("~/work/shop", entrypoint="claude-vscode", start=now - 2 * DAY + 100)        # trap: a plugin's hook that injects text into every session
    ok(e, cmd="${CLAUDE_PLUGIN_ROOT}/hooks/inject.py", out=json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "[plugin] use the tool"}}))
    e.attachment("hook_additional_context", content=["[plugin] use the tool"], hookName="SessionStart", toolUseID="t3", hookEvent="SessionStart"); e.typed("go")
    f = fx.session("~/work/shop", entrypoint="claude-vscode", start=now - 2 * DAY + 200)        # trap: after the first message
    f.typed("go"); ok(f)
    g = fx.session("~/work/shop", entrypoint="claude-vscode", start=now - 2 * DAY + 300)        # trap: a resumed session, not a fresh one
    ok(g, name="SessionStart:resume"); g.typed("go")
    h = fx.session("~/work/shop", entrypoint="sdk-cli", start=now - 2 * DAY + 400)              # trap: headless
    ok(h); h.typed("go")
    return {"value": 3}


# ================================================================================ vet-what-gets-in
PID_V = "vet-what-gets-in"

LOCKS = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb", "uv.lock", "poetry.lock",
         "pdm.lock", "Pipfile.lock", "Cargo.lock", "go.sum", "Gemfile.lock", "composer.lock", "pubspec.lock", "mix.lock",
         "Package.resolved", "packages.lock.json", "gradle.lockfile"}
DIRECT = LOCKS | {"go.mod"}
DIFFED = {"package.json", "pyproject.toml", "Cargo.toml", "composer.json", "Gemfile", "Pipfile", "pom.xml", "build.gradle", "build.gradle.kts", "pubspec.yaml"}
DEP_LINE = re.compile(r'^[+-]\s*(?!"?(?:version|edition|requires-python|python_requires|name)"?\s*[:=])'
                      r'(?:"[^"]+"\s*:\s*"[\^~<>=*]*\d[^"]*"|[\w.-]+\s*=\s*"?[\^~<>=*]*\d|"?[\w.\-\[\]]+"?\s*(?:==|>=|<=|~=|!=)\s*\d|'
                      r"gem\s+['\"]|<artifactId>|(?:implementation|api|compileOnly|testImplementation)\b|\"[\w.-]+[<>=~!]+\d)")


@check(PID_V, "L2", 0)
def dependency_commits(lt):
    """The person's commits in the window that change a lock file, go.mod, requirements*.txt, or the dependency lines of a manifest
    (package.json, pyproject, Cargo.toml, Gemfile...). Counted from git, whichever package manager ran."""
    n, per = 0, {}
    for r in lt.repos:
        for cm in _git_commits(lt, r):
            if not cm["mine"]:
                continue
            kind = None
            for st, path, old in cm["files"]:
                b = os.path.basename(path)
                if b in DIRECT or re.match(r"requirements[\w.-]*\.txt$", b):
                    kind = b; break
                if b in DIFFED:
                    diff = lt.git("show", "-U0", "--format=", "--no-renames", cm["h"], "--", path, cwd=r)
                    if any(DEP_LINE.match(l) for l in diff.splitlines() if not l.startswith(("+++", "---"))):
                        kind = b; break
            if kind:
                n += 1; per[kind] = per.get(kind, 0) + 1
    return {"value": n, "evidence": [f"{k} x{v}" for k, v in sorted(per.items(), key=lambda x: -x[1])][:3],
            "note": "a commit by an agent under the person's identity counts; a package installed and never committed is not seen"}


@fixture(PID_V, "L2", 0)
def _(fx):
    r = "~/work/shop"
    fx.repo(r, {"package.json": json.dumps({"name": "shop", "version": "1.0.0", "scripts": {"build": "tsc"}, "dependencies": {"express": "^4.0.0"}}, indent=1), "package-lock.json": "{}"})   # 1
    fx.session(r, entrypoint="claude-vscode").typed("hi")
    fx.commit(r, {"src/a.ts": "a"}, by="work")                                                                                      # no
    fx.commit(r, {"package.json": json.dumps({"name": "shop", "version": "1.0.0", "scripts": {"build": "tsc -p ."}, "dependencies": {"express": "^4.0.0"}}, indent=1)}, by="work")   # trap: a script
    fx.commit(r, {"package.json": json.dumps({"name": "shop", "version": "1.0.1", "scripts": {"build": "tsc -p ."}, "dependencies": {"express": "^4.0.0"}}, indent=1)}, by="work")   # trap: version bump
    fx.commit(r, {"Cargo.lock": "# lock\n"}, by="work")                                                                             # 2
    fx.commit(r, {"package-lock.json": '{"a": 1}'}, by="teammate")                                                                  # trap: a teammate's
    fx.commit(r, {"requirements.txt": "flask==2.0\n"}, by="work")                                                                   # 3
    fx.commit(r, {"package.json": json.dumps({"name": "shop", "version": "1.0.1", "scripts": {"build": "tsc -p ."}, "dependencies": {"express": "^4.0.0", "lodash": "^4.17.0"}}, indent=1)}, by="work")   # 4
    return {"value": 4}


EXACT_NPM = re.compile(r"^=?v?\d+(\.\d+)*([-+][\w.+-]+)?$")        # 1.2.3, not ^1.2.3 ~1.2 >=1 * latest x
PIN_PATTERNS = {
    "requirements.txt": re.compile(r"^\s*[A-Za-z0-9][\w.\-]*(\[[\w,.\-]+\])?\s*==\s*\d", re.M),
    "pyproject.toml": re.compile(r"""["'][A-Za-z0-9][\w.\-]*(\[[\w,.\-]+\])?\s*==\s*\d"""),
    "Cargo.toml": re.compile(r"""^\s*[\w-]+\s*=\s*(\{[^}\n]*version\s*=\s*)?"=\d""", re.M),
    "Gemfile": re.compile(r"""^\s*gem\s+['"][^'"]+['"]\s*,\s*['"]=?\s*\d""", re.M),
    "workflow": re.compile(r"uses:\s*[^\s@]+@[0-9a-f]{40}\b"),
}


def _pins_in(rel, text):
    """How many exact version pins a manifest file declares: package.json dependencies without a range, `==` in requirements and pyproject,
    `=` in Cargo.toml, a gem with a bare version, a workflow action pinned to a full commit."""
    b = os.path.basename(rel)
    if b == "package.json":
        try:
            m = json.loads(text)
        except ValueError:
            return 0
        return sum(1 for k in ("dependencies", "devDependencies", "optionalDependencies") for v in (m.get(k) or {}).values()
                   if isinstance(v, str) and EXACT_NPM.match(v.strip()))
    key = "requirements.txt" if re.match(r"requirements[\w.-]*\.txt$", b) else ("workflow" if "/.github/workflows/" in "/" + _n(rel) else b)
    rx = PIN_PATTERNS.get(key)
    return len(rx.findall(text)) if rx else 0


_PIN_CANDIDATE = re.compile(r"^.*(?:package\.json|pyproject\.toml|Cargo\.toml|Gemfile|requirements|\.github/workflows/).*$", re.M)


@check(PID_V, "L3", 0)
def versions_pinned(lt):
    """Exact version pins in the manifest files the person wrote (the one ownership rule, _yours_of), in the repos they work in: a
    package.json dependency without a range, `==` in requirements*.txt or pyproject.toml, `=` in Cargo.toml, a gem with a bare version, a
    workflow action pinned to a full commit. Default writes are not pins: a lock file, the gitCommitSha Claude Code records for every
    plugin it installs from a git marketplace, and a skills installer's hash are left out."""
    files = []
    for r in lt.repos:
        rels = _tracked(lt, r)
        # one pass in C over the repo's whole file list (a million names across the repos), then the exact test on the few that pass:
        # a file passes only if its name holds one of these words (the manifest names, `requirements...txt`) or it sits under .github/workflows/
        text = "\n".join(rels).replace("\\", "/")
        i = last = 0
        for m in _PIN_CANDIDATE.finditer(text):
            i += text.count("\n", last, m.start()); last = m.start()     # the line's index, counted from the previous match
            rel = rels[i]
            b = os.path.basename(rel)
            if b in ("package.json", "pyproject.toml", "Cargo.toml", "Gemfile") or re.match(r"requirements[\w.-]*\.txt$", b) \
                    or ("/" + _n(rel)).startswith("/.github/workflows/") and b.endswith((".yml", ".yaml")):
                files.append((r, rel))
    yours = _yours_of(lt, [os.path.join(r, rel) for r, rel in files])
    total, per = 0, {}
    for r, rel in files:
        p = os.path.join(r, rel)
        if yours.get(p):
            n = _pins_in(rel, _read(p, 400000))
            if n:
                total += n; per[os.path.basename(rel)] = per.get(os.path.basename(rel), 0) + n
    return {"value": total, "evidence": [f"{v} pins in {k}" for k, v in sorted(per.items(), key=lambda x: -x[1])][:3],
            "note": "exact pins in files they wrote; a lock file is a default of the package manager and is not read as a choice to pin"}


@fixture(PID_V, "L3", 0)
def _(fx):
    sha = "a" * 40
    fx.file("~/.claude/plugins/installed_plugins.json", json.dumps({"version": 2, "plugins": {          # trap: Claude Code writes a commit sha for every plugin
        "a@m": [{"scope": "user", "version": "1.0.0", "gitCommitSha": "abc123"}], "b@m": [{"scope": "user", "version": "2.0.0", "gitCommitSha": "def456"}]}}))
    fx.repo("~/work/shop", {"package.json": json.dumps({"name": "shop", "version": "1.0.0", "dependencies": {"express": "4.18.2", "lodash": "^4.17.0", "left-pad": "~1.0.0", "any": "*"},
                                                        "devDependencies": {"jest": "29.7.0"}}),
                            "requirements.txt": "flask==2.0.1\nrequests>=2\n# numpy==1.0\n", "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["rich==13.0.0", "click>=8"]\n',
                            ".github/workflows/ci.yml": f"jobs:\n  b:\n    steps:\n      - uses: actions/checkout@{sha}\n      - uses: actions/setup-node@v4\n",
                            "skills-lock.json": json.dumps({"version": 1, "skills": {"x": {"source": "o/r", "computedHash": "h1"}}}),   # trap: an installer's hash
                            "package-lock.json": "{}", "uv.lock": "x\n"})                                                                # trap: default lock files
    fx.repo("~/work/team", {"package.json": json.dumps({"dependencies": {"a": "1.0.0", "b": "2.0.0"}}), "requirements.txt": "x==1\n"}, by="teammate")   # trap: a team's pins
    for p in ("~/work/shop", "~/work/team"):
        fx.session(p, entrypoint="claude-vscode").typed("hi")
    return {"value": 5}      # express, jest, flask, rich, the checkout action


@check(PID_V, "L3", 2)
def lockfile_tracked(lt):
    return {"value": None, "evidence": [], "note": "can't tell from a laptop: npm, cargo, go and uv write a lock file by default and committing it is the convention; whether a reviewer sees each new package in it is on the pull request, not on the laptop"}


@fixture(PID_V, "L3", 2)
def _(fx):
    fx.repo("~/code/node", {"package.json": json.dumps({"dependencies": {"a": "^1"}}), "package-lock.json": "{}"})
    fx.repo("~/code/node2", {"package.json": json.dumps({"dependencies": {"b": "^1"}})})
    for x in ("node", "node2"):
        fx.session("~/code/" + x, entrypoint="claude-vscode").typed("hi")
    return {"value": None}


# ================================================================================ model-and-budget-per-step
PID_M = "model-and-budget-per-step"


@check(PID_M, "L2", 0)
def switches_model(lt):
    """/model, /fast, /effort, /advisor typed in any interactive entrypoint (transcript, or terminal history when the transcript lacks it)."""
    cs = _commands(lt, ("/model", "/fast", "/effort", "/advisor"))
    per = {}
    for c in cs:
        per[c["name"]] = per.get(c["name"], 0) + 1
    return {"value": len(cs), "evidence": [f"{k} x{v}" for k, v in sorted(per.items())], "note": "a model chosen at launch (--model) or in settings is not a switch"}


@fixture(PID_M, "L2", 0)
def _(fx):
    fx.session("~/work/shop", entrypoint="claude-desktop").command("/model", "sonnet")
    fx.session("~/work/shop", entrypoint="claude-vscode").command("/effort", "high")
    t = fx.session("~/work/shop", entrypoint="cli"); t.command("/fast"); t.command("/advisor")
    fx.session("~/work/shop", entrypoint="claude-vscode").command("/modelx")        # trap
    fx.session("~/work/shop", entrypoint="sdk-cli").command("/model", "opus")       # trap: headless
    return {"value": 4}


def _model_family(m):
    """The model a front-matter `model:` names, by family (opus, sonnet, haiku) so `sonnet` and `claude-sonnet-5` are one model; any other
    name stays as written. None for blank or `inherit` (Claude Code's word for 'use the caller's')."""
    m = (m or "").strip().strip("\"'").strip().lower()
    if m in ("", "inherit"):
        return None
    for fam in ("opus", "sonnet", "haiku"):
        if fam in m:
            return fam
    return m


@check(PID_M, "L3", 0)
def own_agents_name_a_model(lt):
    """A deliberate split by role: at least two agent files the person wrote (the one ownership rule, not copied) that name DIFFERENT
    models in their front matter (`model:` a name or an id; `inherit` names none; `sonnet` and `claude-sonnet-5` are one model).
    One agent with a model, or several naming the same one, is a preference, not a choice per role. Value = own agent files that
    name a model when two or more models are named, else 0. Agent tool calls that set a model are in the evidence."""
    ag = [a for a in _artifacts(lt) if a["own"] and a["kind"] == "agent"]
    fam = {a["path"]: _model_family(_fm(_read(a["path"], 6000)).get("model")) for a in ag}
    withm = [a for a in ag if fam[a["path"]]]
    models = sorted({fam[a["path"]] for a in withm})
    calls = list(_calls(lt, ("Agent", "Task"), sidechain=True))
    setm = sum(1 for c in calls if _jin(c).get("model"))
    split = len(models) >= 2
    return {"value": len(withm) if split else 0,
            "evidence": [f"{len(withm)} of {len(ag)} own agent files set a model: {', '.join(models) or 'none'}" + ("" if split else " (fewer than two different models: no split by role)"),
                         f"{setm} of {len(calls)} Agent calls set one"],
            "note": "two agents on different models read as a choice per role; why each was chosen is a question for the call; a model set on a call may be Claude's own pick, not the person's"}


@fixture(PID_M, "L3", 0)
def _(fx):
    w = _world(fx)
    fx.commit(w["kit"], {"agents/follower.md": "---\nname: follower\ntools: Read\nmodel: inherit\n---\nx\n",
                         "agents/quoted.md": '---\nname: quoted\nmodel: "Inherit"\n---\nx\n',
                         "agents/scout.md": "---\nname: scout\ntools: Read\nmodel: claude-haiku-4-5\n---\nx\n"}, by="home")   # inherit: no model; scout: the same model as checker
    s = fx.session(w["kit"], entrypoint="claude-vscode")
    s.call("Agent", {"subagent_type": "checker", "description": "d", "prompt": "p", "model": "haiku"}, "done")
    s.call("Agent", {"subagent_type": "writer", "description": "d", "prompt": "p"}, "done")
    # trap, on laptops of their own: one agent with a model; several agents on one model (a teammate's opus agent does not make a split either)
    from fixture import FixtureHome
    for files, by_mate, what in (({"agents/only.md": "---\nname: only\nmodel: opus\n---\nx\n"}, None, "one agent with a model"),
                                 ({"agents/a.md": "---\nname: a\nmodel: sonnet\n---\nx\n", "agents/b.md": "---\nname: b\nmodel: claude-sonnet-5\n---\nx\n"}, None, "two agents, one model"),
                                 ({"agents/a.md": "---\nname: a\nmodel: sonnet\n---\nx\n"}, {"agents/t.md": "---\nname: t\nmodel: opus\n---\nx\n"}, "a teammate's agent on another model")):
        fx2 = FixtureHome()
        try:
            fx2.repo("~/work/shop", {"README.md": "x", **files}, by="work")
            if by_mate:
                fx2.commit("~/work/shop", by_mate, by="teammate")
            fx2.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
            got = own_agents_name_a_model(fx2.laptop()).get("value")
            assert got == 0, f"{what}: expected 0, got {got}"
        finally:
            fx2.close()
    return {"value": 3}      # checker (haiku), writer (sonnet), scout (haiku again): 3 files, 2 models. planner sets none; follower and quoted inherit; team-agent is a teammate's


@check(PID_M, "L4", 1)
def watches_cost(lt):
    return {"value": None, "evidence": [], "note": "can't tell from a laptop: tracking cost against what each run left needs the spend beside the work it produced; the logs hold /cost and /usage (a glance), a cost record Claude Code writes for every session, and a spend cap on any headless run, including plugins' and hooks' own"}


@fixture(PID_M, "L4", 1)
def _(fx):
    fx.session("~/work/shop", entrypoint="claude-desktop").command("/cost")
    h = fx.session("~/work/shop", entrypoint="sdk-cli"); h.attachment("budget_usd", used=0.1, total=1.0, remaining=0.9); h.typed("go")
    s = fx.session("~/work/shop", entrypoint="claude-vscode"); s.typed("x"); s.record("cost-state", totalCostUSD=0.5, modelUsage={})
    return {"value": None}


# Built once before the checks split into workers (evaluate.py / run_portable.py run_prewarm_hooks). _artifacts is the
# "what you built" list (it also builds _hooks and _installed_index): 8 checks read it, and each worker used to rebuild it.
def _bash_commands_all(lt):
    """Each Bash call's command pulled out of its raw input once (_cmd keeps it per distinct input): several checks read them all."""
    for c in _calls(lt, ("Bash",), sidechain=True):
        _cmd(c)


def _commits_all(lt):
    """Every repo's commits in the window (_git_commits), git asked for all repos at once: several checks read them and the first
    one used to pay for all 88 repos, one after the other."""
    from concurrent.futures import ThreadPoolExecutor
    lt.repos; lt.identity   # read before any thread starts
    with ThreadPoolExecutor(8) as ex:   # git runs in its own process: threads overlap the waiting
        list(ex.map(lambda r: _git_commits(lt, r), lt.repos))


def _own_checks_shared(lt):
    """What the own-the-checks checks all read: every repo's file list, who edited which file, Bash calls per repo."""
    if lt.repos:
        _tracked(lt, lt.repos[0])
    _edits_by_name(lt); _bash_by_repo(lt); _repo_activity(lt)


PREWARM = [_commits_all, _prompt_numstat, _own_setup_files, _own_checks_shared, _bash_commands_all, _artifacts, _typed_by_session, _eval_folders, _rc_path_dirs, _home_prefix, _path_stems, _path_index]
