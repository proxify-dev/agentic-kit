"""Group 3: ask-before-what-cant-be-undone, prove-it-works, work-with-the-prompt-cache, test-the-setup-code,
watch-what-runs-did-and-cost, retest-agent-setup, share-the-setup-with-the-team, step-in-on-signals,
lead-a-team-of-agents, keep-client-code-and-data-in-bounds.

Helpers this file adds because base.py does not keep them (named in the report):
  _scan(lt)            one read-only pass over the session files for what Laptop drops: assistant records (message id,
                       model, usage, effort, Agent tool_use ids), permission-mode and cost-state records (they carry no
                       timestamp, so Laptop skips them), hook attachments with their command.
  _hook_entries(lt)    every command hook (and statusLine) in every settings file, with its resolved script and owner.
  _checks_noci(...)    the commands a repo declares as checks, without reading its CI file (so a CI step is never judged
                       by itself); _ci_steps(...), _declared_paths(...), _mechanisms(...) build on it.
"""
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile

from base import builtin_agent_types, check, epoch
from fixture import fixture

AGENT_TOOLS = ("Agent", "Task")   # Claude Code's built-in tool that starts a sub-agent (Task is its older name)


# ====================================================================================== shared helpers
def _inp(c):
    """A tool call's input as a dict (Laptop keeps it as cut JSON text)."""
    try:
        d = json.loads(c["input"])
        return d if isinstance(d, dict) else {}
    except ValueError:
        d = {}
        for k in ("command", "file_path", "path", "subagent_type", "pattern"):
            m = re.search(r'"%s": "((?:[^"\\]|\\.)*)' % k, c.get("input") or "")
            if m:
                d[k] = m.group(1)
        return d


def _norm(p):
    return (p or "").replace("\\", "/")


_CMD_KEY = re.compile(r'"command": "((?:[^"\\]|\\.)*)')


def _cmd(c):
    """The command of a Bash call: the same answer as _inp(c)["command"], without hunting for the other keys when the cut JSON doesn't parse."""
    try:
        d = json.loads(c["input"])
        return (d.get("command") or "") if isinstance(d, dict) else ""
    except ValueError:
        m = _CMD_KEY.search(c.get("input") or "")
        return m.group(1) if m else ""


_FILE_KEY = re.compile(r'"file_path": "((?:[^"\\]|\\.)*)')
_FILE_KEY_PLAIN = re.compile(r'"file_path":\s*"([^"\\]+)"')


def _plain_file(c):
    """The file_path of an edit call when it is a plain string (no escapes): found without parsing the whole input, which for
    a Write is the file's content. None when it isn't plain; the caller then parses."""
    m = _FILE_KEY_PLAIN.search(c.get("input") or "")
    return m.group(1) if m else None


def _edited_file(c):
    """The file an edit call names: what _inp(c) gives for file_path / notebook_path, without hunting for the other keys when
    the cut JSON doesn't parse."""
    quick = _plain_file(c)
    if quick:
        return quick
    try:
        d = json.loads(c["input"])
        return (d.get("file_path") or d.get("notebook_path") or "") if isinstance(d, dict) else ""
    except ValueError:
        m = _FILE_KEY.search(c.get("input") or "")
        return m.group(1) if m else ""


def _file_path(c):
    """_inp(c).get("file_path"), without hunting for the other keys when the cut JSON doesn't parse."""
    quick = _plain_file(c)
    if quick:
        return quick
    try:
        d = json.loads(c["input"])
        return (d.get("file_path") or "") if isinstance(d, dict) else ""
    except ValueError:
        m = _FILE_KEY.search(c.get("input") or "")
        return m.group(1) if m else ""


def _scan(lt):
    """What Laptop's index keeps short, for these checks: assistant messages (from lt.assist), permission-mode and
    cost-state records (no timestamp), hook attachments with their command. Cached on the Laptop.
    (11-05: built from lt.assist and lt.grep, not a second line-by-line pass over every log.)"""
    got = getattr(lt, "_g3", None)
    if got is not None:
        return got
    out = {"assist": [], "pmode": {}, "cost": {}, "hooks": []}
    for r in lt.grep(rb'"type":\s*"(?:permission-mode|cost-state)"'):
        sid = r.get("sessionId") or r["_sid"]
        if r.get("type") == "permission-mode":
            out["pmode"].setdefault(sid, set()).add(r.get("permissionMode"))
        elif isinstance(r.get("totalCostUSD"), (int, float)):
            out["cost"][sid] = r["totalCostUSD"]
    for r in lt.grep(rb'"attachment":\s*\{\s*"type":\s*"hook_'):
        a = r.get("attachment") or {}
        t = epoch(r.get("timestamp")) if r.get("timestamp") else None
        if t is None or not isinstance(a, dict):
            continue
        out["hooks"].append({"s": r.get("sessionId") or r["_sid"], "t": t, "type": a.get("type"), "event": a.get("hookEvent"),
                             "command": a.get("command") or "", "sc": r["_sc"]})
    out["assist"] = sorted(lt.assist, key=lambda a: a["t"])   # the same records, not a copy of 400k dicts
    lt._g3 = out
    return out


def _main_assist(lt):
    """Main-thread assistant messages of interactive sessions, one per message id (a streamed message is several records)."""
    seen, out = set(), []
    for a in _scan(lt)["assist"]:
        if a["sc"] or not lt.interactive(a["s"]):
            continue
        if a["mid"]:
            if a["mid"] in seen:
                continue
            seen.add(a["mid"])
        out.append(a)
    return out


def _by_session(items):
    d = {}
    for x in items:
        d.setdefault(x["s"], []).append(x)
    return d


def _settings(lt):
    got = getattr(lt, "_g3_settings", None)
    if got is None:
        got = lt._g3_settings = lt.settings_files()
    return got


def _perms(lt, kinds=("deny", "ask", "allow")):
    """Distinct (kind, rule) across every settings file: a worktree copy of a project file counts once."""
    seen = {}
    for scope, path, d in _settings(lt):
        p = d.get("permissions") if isinstance(d, dict) else None
        for k in kinds:
            for r in (p.get(k) if isinstance(p, dict) and isinstance(p.get(k), list) else []):
                seen.setdefault((k, str(r)), scope)
    return seen


def _perms_mine(lt, kinds):
    """_perms over the person's own settings files only (_mine_settings)."""
    seen = {}
    for scope, path, d in _mine_settings(lt):
        p = d.get("permissions") if isinstance(d, dict) else None
        for k in kinds:
            for r in (p.get(k) if isinstance(p, dict) and isinstance(p.get(k), list) else []):
                seen.setdefault((k, str(r)), (scope, path))
    return seen


def _owned(lt, path):
    """The file is the person's by the one ownership rule (lt.owner, the owner's ruling 11-05): theirs unless a receipt says installed
    (a published marketplace plugin, a skills lock) or most of its commits are someone else's. A file git doesn't track is theirs.
    A repo whose whole log is already read (lt.repos, prewarmed) answers from it; for any other checkout (a worktree) one
    file's own commits are asked, not the whole history of that checkout."""
    real = os.path.realpath(path)
    top = lt.toplevel(real)
    if not top:
        return lt.owner(path, None, 0, 0) == "theirs"
    if top in lt.__dict__.get("_auth_memo", {}) or top in lt.repos:
        return lt.authorship(path)["yours"] is True
    rel = os.path.relpath(real, top).replace(os.sep, "/")
    log = [a for a in lt.git("log", "--no-merges", "--full-history", "--format=%an <%ae>", "--", rel, cwd=top).splitlines() if a.strip()]
    return lt.owner(path, top, len(log), sum(1 for a in log if lt.is_mine(a))) == "theirs"


def _mine_settings(lt):
    """The settings files that are the person's: theirs by the one ownership rule (_owned), after two scope checks the rule can't
    answer: managed settings are the employer's, and the home folder read as a project is the user file again (listed once, as user).
    A project, local or user file in a repo of teammates or a clone is a teammate's; one nobody committed is theirs."""
    got = getattr(lt, "_g3_mine", None)
    if got is not None:
        return got
    out = []
    for scope, path, d in _settings(lt):
        if scope == "managed":
            continue
        if scope.startswith("project") and os.path.realpath(os.path.dirname(os.path.dirname(path))) == lt.home:
            continue
        if _owned(lt, path):
            out.append((scope, path, d))
    lt._g3_mine = out
    return out


def _under_home(lt, p):
    return os.path.realpath(p).startswith(lt.home + os.sep)


# ---------------------------------------------------------------- hooks wired in settings
def _script_of(lt, command, root):
    """The script file a hook command runs: the first token (or the one after an interpreter) that is a file under the
    person's home. $CLAUDE_PROJECT_DIR, $HOME, ~ and Windows separators are resolved."""
    toks = []
    try:
        toks += shlex.split(command)
    except ValueError:
        pass
    toks += command.split()
    for tok in toks:
        if tok.startswith("-"):
            continue
        t = tok.replace("${CLAUDE_PROJECT_DIR}", root or "").replace("$CLAUDE_PROJECT_DIR", root or "")
        t = t.replace("${HOME}", lt.home).replace("$HOME", lt.home)
        if t.startswith("~"):
            t = lt.home + t[1:]
        t = _norm(t)
        cands = [t] if os.path.isabs(t) else ([os.path.join(root, t)] if root else [])
        for c in cands:
            if os.path.isfile(c) and _under_home(lt, c):
                return os.path.realpath(c)
    return None


def _hook_entries(lt):
    """Every command hook and the statusLine command in the settings files: scope, event, command, script, yours (the script, or for
    an inline command the settings file, is theirs by the one ownership rule, _owned: never by location)."""
    got = getattr(lt, "_g3_hooks", None)
    if got is not None:
        return got
    out, seen = [], set()
    for scope, path, d in _settings(lt):
        root = os.path.dirname(os.path.dirname(path)) if scope.startswith("project") else None
        pairs = []
        hooks = d.get("hooks") if isinstance(d.get("hooks"), dict) else {}
        for ev, groups in hooks.items():
            for g in groups if isinstance(groups, list) else []:
                for h in (g.get("hooks") if isinstance(g, dict) else None) or []:
                    if isinstance(h, dict) and h.get("type", "command") == "command" and h.get("command"):
                        pairs.append((ev, h["command"]))
        sl = d.get("statusLine")
        if isinstance(sl, dict) and sl.get("command"):
            pairs.append(("statusLine", sl["command"]))
        for ev, command in pairs:
            script = _script_of(lt, command, root)
            key = (ev, script or command)
            if key in seen:
                continue   # the same hook in a worktree copy
            seen.add(key)
            if script:
                yours = ("owned", script)
            elif scope == "managed":
                yours = False
            else:
                yours = ("owned", path)   # an inline command: the settings file it is written in decides
            out.append({"scope": scope, "settings": path, "event": ev, "command": command, "script": script, "yours": yours})
    lt.repos, lt.identity   # computed here, not first in a thread (identity swaps the HOME variable while it reads)
    from concurrent.futures import ThreadPoolExecutor   # each owner is a git question in its own process: ask them side by side
    paths = list(dict.fromkeys(e["yours"][1] for e in out if isinstance(e["yours"], tuple)))
    with ThreadPoolExecutor(8) as ex:
        owned = dict(zip(paths, ex.map(lambda q: _owned(lt, q), paths)))
    for e in out:
        if isinstance(e["yours"], tuple):
            e["yours"] = owned[e["yours"][1]]
    lt._g3_hooks = out
    return out


# ---------------------------------------------------------------- what a repo declares
def _ci_steps(repo):
    """(file, command text) for every run step of the repo's CI files, block scalars (run: |) included."""
    out = []
    files = glob.glob(os.path.join(repo, ".github", "workflows", "*.y*ml")) + glob.glob(os.path.join(repo, ".gitlab-ci.yml"))
    for wf in files:
        try:
            lines = open(wf, errors="replace").read().splitlines()
        except OSError:
            continue
        i = 0
        while i < len(lines):
            m = re.match(r"(\s*)(?:-\s*)?(run|script):\s*(.*)$", lines[i])
            if not m:
                i += 1
                continue
            indent, rest = len(m.group(1)), m.group(3).strip()
            if rest in ("|", ">", "|-", ">-", ""):
                j, body = i + 1, []
                while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent):
                    if not lines[j].strip().startswith("#"):
                        body.append(lines[j].strip().lstrip("- ").strip())
                    j += 1
                out.append((wf, "\n".join(b for b in body if b)))
                i = j
            else:
                out.append((wf, rest.strip("'\"")))
                i += 1
    return out


_EVERY_CHANGE = ("push", "pull_request", "pull_request_target")


def _runs_on_change(path, text):
    """Does a CI file start on a change (a push or a pull request, not only tags, a schedule or a click)? GitHub: the `on:` block is
    read (inline, list or mapping form; a push that lists only `tags` is a release trigger). GitLab: no `rules:` / `only:` /
    manual jobs, which would need its own reading. Anything unreadable is False."""
    if path.endswith(".gitlab-ci.yml"):
        return not re.search(r"^\s*(rules|only|except):|when:\s*(manual|delayed)", text, re.M)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^['\"]?on['\"]?\s*:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).split("#")[0].strip()
        if rest:
            return bool(set(re.findall(r"[a-z_]+", rest)) & set(_EVERY_CHANGE))
        block = []
        for ln in lines[i + 1:]:
            if ln.strip() and not ln.strip().startswith("#") and not ln.startswith((" ", "\t")):
                break
            block.append(ln)
        block = [ln for ln in block if ln.strip() and not ln.strip().startswith("#")]
        if not block:
            return False
        ind = min(len(ln) - len(ln.lstrip()) for ln in block)
        events, cur = {}, None
        for ln in block:
            k = re.match(r"\s*([\w-]+)\s*:", ln)
            if k and len(ln) - len(ln.lstrip()) == ind:
                cur = events.setdefault(k.group(1), [])
            elif cur is not None:
                cur.append(ln)
            elif ln.strip().startswith("-"):   # a list of events
                events[ln.strip().lstrip("- ").strip()] = []
        for ev in _EVERY_CHANGE:
            if ev in events:
                body = "\n".join(events[ev])
                if ev == "push" and re.search(r"\btags\s*:", body) and not re.search(r"\bbranches\s*:", body):
                    continue
                return True
        return False
    return False


def _ci_every_change(lt, repo):
    """[(file, [run step texts])] of the repo's CI files that are the person's (_owned, the one ownership rule) and start on every change."""
    memo = lt.__dict__.setdefault("_g3_ci", {})
    if repo not in memo:
        by_file = {}
        for f, text in _ci_steps(repo):
            by_file.setdefault(f, []).append(text)
        out = []
        for f, steps in sorted(by_file.items()):
            if _owned(lt, f) and _runs_on_change(f, _read(f)):
                out.append((f, steps))
        memo[repo] = out
    return memo[repo]


def _checks_noci(lt, repo):
    """The repo's declared check commands without its CI file: declared_checks over a copy of the root files only,
    so a CI step is never what makes a command count as a check."""
    cache = getattr(lt, "_g3_noci", None)
    if cache is None:
        cache = lt._g3_noci = {}
    if repo in cache:
        return cache[repo]
    tmp = tempfile.mkdtemp(prefix=".g3-noci-", dir=tempfile.gettempdir())
    try:
        for name in os.listdir(repo):
            p = os.path.join(repo, name)
            if os.path.isfile(p) and os.path.getsize(p) < 2_000_000:
                os.symlink(p, os.path.join(tmp, name))   # read-only use: a link, not a copy of every root file
        cache[repo] = _only_checks(lt.declared_checks(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return cache[repo]


def _only_checks(pats):
    """base.declared_checks covers check roles; its per-language defaults still list `build` commands (go build, cargo build,
    gradle build): building is not checking (the skeptic, 11-05), so a pattern that names build and no check role is left out."""
    from base import CHECK_ROLE
    return [p for p in pats if CHECK_ROLE.search(p.pattern) or not re.search(r"\bbuild\b", p.pattern)]


_TEST_RUN = re.compile(r"(?i)(^|[\W_])(test|tests|spec|e2e|pytest|jest|vitest|rspec|phpunit|tox|bats)([\W_]|$)")


def _test_pats(lt, repo):
    """The repo's declared commands that run tests (not lint or type checks), combined; memoized."""
    memo = lt.__dict__.setdefault("_g3_test_rx", {})
    if repo not in memo:
        memo[repo] = _combine([p for p in _checks_noci(lt, repo) if _TEST_RUN.search(p.pattern)])
    return memo[repo]


_LIT_HEAD, _LIT_TAIL = r"(^|[;&|]\s*|\s)", r"(\s|$)"   # what base.declared_checks wraps around every literal command


def _combine(pats):
    """The distinct patterns as one regex (one search per text, not one per pattern); None when there are none.
    The patterns base.declared_checks makes all share one boundary before and after the command: that boundary is written
    once and the commands go in as alternatives, so a text is not re-tried at every position for every command.
    A pattern of another shape is kept whole as its own alternative. The same set of texts match."""
    lits, other = [], []
    for u in dict.fromkeys(p.pattern for p in pats):
        if u.startswith(_LIT_HEAD) and u.endswith(_LIT_TAIL):
            lits.append(u[len(_LIT_HEAD):len(u) - len(_LIT_TAIL)])
        else:
            other.append("(?:%s)" % u)
    alts = other[:]
    if lits:
        alts.append("(?:^|[;&|]\\s*|\\s)(?:%s)(?:\\s|$)" % "|".join(lits))
    return re.compile("|".join(alts)) if alts else None


def _declared_rx(lt, repo):
    """lt.declared_rx(repo), rebuilt in the faster shape; memoized per repo."""
    memo = lt.__dict__.setdefault("_g3_decl_rx", {})
    if repo not in memo:
        memo[repo] = _combine(_only_checks(lt.declared_checks(repo))) if repo else None
    return memo[repo]


def _declared_cmd(lt, cmd, repo):
    """Is `cmd` a command `repo` declares as a check (memoized per distinct command and repo)."""
    memo = lt.__dict__.setdefault("_g3_cmd_memo", {})
    k = (repo, cmd)
    if k not in memo:
        rx = _declared_rx(lt, repo)
        memo[k] = bool(rx and rx.search(cmd or ""))
    return memo[k]


def _is_check(rx, text):
    """rx: a combined pattern from _repo_pats / _all_pats."""
    return bool(rx and rx.search(text))


def _declared_files(repo):
    """(file, command text) for what the repo declares to run: package.json scripts, Makefile/justfile recipe lines (comment lines
    left out)."""
    out = []
    try:
        names = set(os.listdir(repo))    # the names as the folder spells them: on a case-insensitive disk "Justfile" would also open "justfile"
    except OSError:
        names = set()
    pj = os.path.join(repo, "package.json")
    if os.path.exists(pj):
        try:
            out += [(pj, b) for b in (json.load(open(pj)).get("scripts") or {}).values() if isinstance(b, str)]
        except (OSError, ValueError):
            pass
    for name in ("Makefile", "makefile", "GNUmakefile"):
        p = os.path.join(repo, name)
        if name in names:
            out += [(p, m.strip()) for m in re.findall(r"^\t(.+)$", open(p, errors="replace").read(), re.M) if not m.strip().startswith("#")]
    for name in ("justfile", "Justfile", ".justfile"):
        p = os.path.join(repo, name)
        if name in names:
            out += [(p, m.strip()) for m in re.findall(r"^[ ]{2,}(\S.*)$", open(p, errors="replace").read(), re.M) if not m.strip().startswith("#")]
    return out


def _declared_texts(repo):
    """Command lines the repo declares anywhere: package.json scripts, Makefile/justfile recipes, CI steps, pre-commit entries."""
    out = [(os.path.basename(f), t) for f, t in _declared_files(repo)]
    out += [("ci", t) for _, t in _ci_steps(repo)]
    pc = os.path.join(repo, ".pre-commit-config.yaml")
    if os.path.exists(pc):
        out += [("pre-commit", m.strip()) for m in re.findall(r"^\s*entry:\s*(.+)$", open(pc, errors="replace").read(), re.M)]
    return out


def _declared_paths(repo):
    """Paths in the repo that its test config or its test commands name (pytest testpaths, jest roots, `pytest qa/`): a folder, or
    a file by a test runner's naming, that a command running tests takes as an argument. A path some other declared command
    names (scripts/, src/, .env) is not a test path."""
    paths = set()
    for _, text in _declared_texts(repo):
        if not _TEST_RUN.search(text):
            continue
        for tok in re.split(r"[\s;&|]+", text):
            tok = tok.strip("'\"")
            if not tok or tok.startswith(("-", "$")) or os.path.isabs(tok) or not re.search(r"[/.]", tok):
                continue
            rel = tok[2:] if tok.startswith("./") else tok
            rel = rel.rstrip("/")
            full = os.path.join(repo, rel)
            if rel and (os.path.isdir(full) or (os.path.exists(full) and _TEST_NAME.search(rel))):
                paths.add(rel)
    for cfg in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"):
        p = os.path.join(repo, cfg)
        if os.path.exists(p):
            m = re.search(r"testpaths\s*=\s*(\[[^\]]*\]|[^\n]+)", open(p, errors="replace").read())
            if m:
                paths.update(x.strip("/") for x in re.findall(r"[\w./-]+", m.group(1).replace("testpaths", "")) if x.strip("/"))
    pj = os.path.join(repo, "package.json")
    if os.path.exists(pj):
        try:
            roots = (json.load(open(pj)).get("jest") or {}).get("roots") or []
            paths.update(_norm(r).replace("<rootDir>/", "").strip("./") for r in roots if isinstance(r, str))
        except (OSError, ValueError):
            pass
    return {p for p in paths if p}


def _tracked(lt, repo):
    """What git tracks. Only for a claim about what was committed to be shared; whose a file is, is _owned."""
    return [x for x in lt.git("ls-files", "--cached", cwd=repo).splitlines() if x]


def _repo_files(lt, repo):
    """Every file in the repo that is not ignored: tracked or not. Whose each is, is _owned (untracked files are theirs)."""
    memo = lt.__dict__.setdefault("_g3_files", {})
    if repo not in memo:
        memo[repo] = [x for x in lt.git("ls-files", "--cached", "--others", "--exclude-standard", cwd=repo).splitlines() if x]
    return memo[repo]


def _hooks_path(lt, repo):
    """`git rev-parse --git-path hooks` for every repo at once (git runs in its own process: threads overlap the waiting),
    asked once per Laptop."""
    memo = lt.__dict__.setdefault("_g3_hookpath", {})
    if repo not in memo:
        from concurrent.futures import ThreadPoolExecutor
        todo = [r for r in lt.repos if r not in memo] if repo in lt.repos else [repo]
        with ThreadPoolExecutor(8) as ex:
            for r, out in zip(todo, ex.map(lambda r: lt.git("rev-parse", "--git-path", "hooks", cwd=r).strip(), todo)):
                memo[r] = out
    return memo[repo]


def _hooks_dirs(lt, repo):
    """The folder git runs hooks from (core.hooksPath when set, else .git/hooks); husky's tracked scripts when that is its folder."""
    d = []
    p = _hooks_path(lt, repo)
    if p:
        d.append(p if os.path.isabs(p) else os.path.join(repo, p))
        if re.search(r"\.husky[/\\]_/?$", d[0]):   # husky 9 points git at its generated .husky/_, which calls the tracked scripts above it
            d.append(os.path.join(repo, ".husky"))
    return d


def _git_hook_files(lt, repo):
    out = []
    for d in _hooks_dirs(lt, repo):
        for f in sorted(glob.glob(os.path.join(d, "*"))):
            if os.path.isfile(f) and not f.endswith(".sample") and os.path.getsize(f) < 500_000:
                out.append(f)
    return out


def _read(p):
    try:
        return open(p, errors="replace").read()
    except OSError:
        return ""


def _code_lines(text):
    return "\n".join(l for l in text.splitlines() if l.strip() and not l.strip().startswith("#"))


def _mechanisms(lt, repo, pats):
    """What runs a declared check without anyone asking, set up by the person: a CI file of theirs that starts on every change
    (push or pull request) with a step running one; a git hook of theirs (_owned) that runs one;
    the repo's own pre-commit config (_owned) once its hook is installed. A hook file git doesn't track is theirs like any other
    (ruling 11-05). [(kind, file)]; a file counts once."""
    out = []
    for f, steps in _ci_every_change(lt, repo):
        if any(_is_check(pats, t) for t in steps):
            out.append(("ci", f))
    pc = os.path.join(repo, ".pre-commit-config.yaml")
    for f in _git_hook_files(lt, repo):
        body = _code_lines(_read(f))
        if _owned(lt, f) and _is_check(pats, body):
            out.append(("git-hook", f))
        elif "pre-commit" in body and os.path.exists(pc) and _owned(lt, pc) and ("git-hook", pc) not in out:
            out.append(("git-hook", pc))   # the stub `pre-commit install` wrote runs the config the person committed
    return out


def _repo_pats(lt, repo):
    memo = lt.__dict__.setdefault("_g3_repo_rx", {})
    if repo not in memo:
        memo[repo] = _combine(_checks_noci(lt, repo))
    return memo[repo]


def _all_pats(lt):
    memo = lt.__dict__.setdefault("_g3_all_rx", [])
    if not memo:
        memo.append(_combine([p for r in lt.repos for p in _checks_noci(lt, r)]))
    return memo[0]


_TEST_NAME = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.(py|go)|[^/]*\.(test|spec)\.[cm]?[jt]sx?|[^/]*\.bats|__tests__/)")
_NOT_CODE = re.compile(r"\.(md|mdx|markdown|txt|rst|adoc|json|ya?ml|toml|csv|png|jpe?g|svg|lock)$", re.I)


def _is_test_file(rel, declared):
    """A test file by the runner's own naming (pytest, jest/vitest, go, bats) or because it sits in a path a test command of the
    repo names. A document or a data file is never a test, wherever it sits."""
    rel = _norm(rel)
    if _NOT_CODE.search(rel):
        return False
    if _TEST_NAME.search(rel):
        return True
    if not declared:
        return False
    if rel in declared:
        return True
    i = rel.find("/")
    while i != -1:   # a declared path that is a folder above the file
        if rel[:i] in declared:
            return True
        i = rel.find("/", i + 1)
    return False


def _hook_scripts(lt):
    return {e["script"] for e in _hook_entries(lt) if e["script"]}


def _hook_test_files(lt):
    """[(repo, rel)] test files the person wrote (a test by its runner's naming, never a document; _owned) that name,
    as a whole word, a script wired as one of their hooks or launcher (a script that is _owned)."""
    got = lt.__dict__.get("_g3_htf")
    if got is not None:
        return got
    scripts = sorted({os.path.basename(e["script"]) for e in _hook_entries(lt) if e["script"] and e["yours"]})
    out = []
    if not scripts:
        return out

    def naming(r):
        """(repo, rel) of the repo's test files that name a script and are no larger than 300 kB. One `git grep` per repo
        (git reads the files itself, tracked or not, in parallel, as bytes) instead of Python opening every test file."""
        declared = _declared_paths(r)
        tests = [rel for rel in _repo_files(lt, r) if _is_test_file(rel, declared)]
        if not tests:
            return []
        named = set(lt.git("grep", "-l", "-a", "-z", "-F", "-w", "--no-color", "--untracked", *[x for sc in scripts for x in ("-e", sc)],
                           cwd=r, timeout=600).split("\0"))
        out = []
        for rel in tests:
            if rel in named:
                try:
                    if os.path.getsize(os.path.join(r, rel)) <= 300_000:
                        out.append((r, rel))
                except OSError:
                    pass
        return out

    from concurrent.futures import ThreadPoolExecutor   # git runs in its own process: repos side by side
    with ThreadPoolExecutor(8) as ex:
        hits = [rr for part in ex.map(naming, lt.repos) for rr in part]
    out = [(r, rel) for r, rel in hits if _owned(lt, os.path.join(r, rel))]
    lt._g3_htf = out
    return out


def _reaches(repo, rel):
    """Does a command the repo declares for running tests run this test file? By a path the command names (the file or a folder above
    it), or by the runner's own naming for a runner the repo sets up: pytest (a pytest config or a command naming pytest/unittest/
    tox/nox) for test_*.py, a package.json `test` script (not npm's empty stub) for js/ts, go.mod for *_test.go. A language's
    default alone (a pyproject.toml, nothing about tests) does not run anything."""
    rel = _norm(rel)
    texts = [t for src, t in _declared_texts(repo) if src != "ci"]
    folders = {rel} | {rel[:i] for i in range(len(rel)) if rel[i] == "/"}
    for t in texts:
        for tok in re.split(r"[\s;&|]+", t):
            if tok.strip("'\"").lstrip("./").rstrip("/") in folders:
                return True
    blob = "\n".join(texts)
    if rel.endswith(".py"):
        cfg = any(os.path.exists(os.path.join(repo, f)) for f in ("pytest.ini", "tox.ini"))
        for f, pat in (("pyproject.toml", r"\[tool\.pytest"), ("setup.cfg", r"\[tool:pytest\]")):
            cfg = cfg or bool(re.search(pat, _read(os.path.join(repo, f))))
        return cfg or bool(re.search(r"\b(pytest|py\.test|unittest|tox|nox)\b", blob))
    if rel.endswith(".go"):
        return os.path.exists(os.path.join(repo, "go.mod"))
    if re.search(r"\.[cm]?[jt]sx?$", rel):
        try:
            test = (json.load(open(os.path.join(repo, "package.json"))).get("scripts") or {}).get("test") or ""
        except (OSError, ValueError):
            test = ""
        return bool(test and "no test specified" not in test) or bool(re.search(r"\b(jest|vitest|mocha|ava)\b", blob))
    return False


def _reached_tests(lt):
    """[(repo, rel)] of _hook_test_files that the repo's own test command runs."""
    return [(r, rel) for r, rel in _hook_test_files(lt) if _reaches(r, rel)]


def _ev(items, n=3, f=str):
    return [f(x)[:100] for x in list(items)[:n]]


# ====================================================================================== ask-before-what-cant-be-undone
ASK = "ask-before-what-cant-be-undone"
_PLAN_OR_QUESTION = ("ExitPlanMode", "AskUserQuestion")   # Claude Code's own dialogs: 'rejected' there is a dismissed question
_ASKING_MODES = ("default", "plan", "acceptEdits", "auto")   # modes in which Claude Code still asks or confines
_NOT_ASKING = ("bypassPermissions", "dontAsk")


# What is risky is read from the command or the tool call itself, with the tools' own words (git, rm, kubectl, curl, an MCP
# tool's verb), the same for everyone. Quoted text is not a command: each shell segment is split into words and its first
# word is the program.
_WRAPPERS = ("sudo", "doas", "time", "nohup", "command", "exec", "env", "nice", "xargs", "uv", "uvx", "npx", "bunx", "poetry", "pipenv")
_RISKY_WORD = re.compile(r"(?i)(^|[:/_.\-])(deploy|publish|release|rollout|production|prod)([:/_.\-]|$)")
_RUNNERS = ("make", "just", "npm", "pnpm", "yarn", "bun", "task", "rake", "invoke", "fab", "bash", "sh", "zsh", "python", "python3", "node")
_SQL_WRITE = re.compile(r"(?i)\b(drop|truncate|delete\s+from|alter\s+table|insert\s+into|update\s+\S+\s+set|grant|revoke|flushall|flushdb)\b")
_SQL_CLIENTS = ("psql", "mysql", "mariadb", "sqlite3", "sqlcmd", "mongosh", "mongo", "redis-cli", "bq", "clickhouse-client",
                "cqlsh", "duckdb", "sqlplus", "usql", "pgcli", "litecli")
_SUBS = {   # program -> subcommands that change something that lives outside the working tree
    "kubectl": {"apply", "delete", "replace", "patch", "scale", "rollout", "create", "drain", "cordon", "set", "annotate", "label"},
    "oc": {"apply", "delete", "replace", "patch", "scale", "rollout", "create"},
    "terraform": {"apply", "destroy", "import", "taint"}, "tofu": {"apply", "destroy", "import", "taint"},
    "terragrunt": {"apply", "destroy"}, "helm": {"install", "upgrade", "uninstall", "delete", "rollback"},
    "pulumi": {"up", "destroy"}, "cdk": {"deploy", "destroy"}, "sls": {"deploy", "remove"}, "serverless": {"deploy", "remove"},
    "netlify": {"deploy"}, "fly": {"deploy", "destroy", "scale"}, "flyctl": {"deploy", "destroy", "scale"},
    "firebase": {"deploy"}, "wrangler": {"deploy", "publish", "delete"}, "vercel": {"deploy", "remove", "rm", "promote"},
    "npm": {"publish", "unpublish", "deprecate", "dist-tag"}, "pnpm": {"publish"}, "yarn": {"publish", "npm"}, "bun": {"publish"},
    "cargo": {"publish", "yank"}, "twine": {"upload"}, "gem": {"push", "yank"}, "poetry": {"publish"}, "uv": {"publish"},
    "docker": {"push", "rm", "rmi", "prune"}, "docker-compose": {"down"}, "podman": {"push", "rm", "rmi"},
    "prisma": {"migrate", "db"}, "alembic": {"upgrade", "downgrade"}, "flyway": {"migrate", "clean"}, "liquibase": {"update", "rollback"},
    "dropdb": {None}, "dropuser": {None}, "sendmail": {None}, "mail": {None}, "mailx": {None}, "mutt": {None}, "msmtp": {None},
    "ssmtp": {None}, "swaks": {None}, "shred": {None}, "mkfs": {None}, "dd": {None},
}
_GH_RISKY = {("pr", "merge"), ("pr", "create"), ("pr", "comment"), ("pr", "review"), ("pr", "close"), ("issue", "create"), ("issue", "comment"),
             ("issue", "close"), ("issue", "delete"), ("release", "create"), ("release", "delete"), ("repo", "delete"), ("workflow", "run"),
             ("repo", "create"), ("gist", "create"), ("secret", "set"), ("secret", "delete")}
_FORCE = {"--force", "--force-with-lease", "--force-if-includes", "-f"}
_HTTP_BODY = ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--data-ascii", "-F", "--form", "--json", "-T", "--upload-file")


def _words(seg):
    try:
        return shlex.split(seg)
    except ValueError:
        return seg.split()


def _git_risk(rest):
    i, sub = 0, None
    while i < len(rest):
        if rest[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            i += 2
        elif rest[i].startswith("-"):
            i += 1
        else:
            sub, rest = rest[i], rest[i + 1:]
            break
    if not sub:
        return None
    short_f = any(re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", t) for t in rest)
    if sub == "push":
        return "git push"
    if sub == "reset" and "--hard" in rest:
        return "git reset --hard"
    if sub == "clean" and (short_f or "--force" in rest) and not ({"-n", "--dry-run"} & set(rest)):
        return "git clean -f"
    if sub == "branch" and ("-D" in rest or (("--delete" in rest) and short_f)):
        return "git branch -D"
    if sub == "stash" and rest[:1] in (["drop"], ["clear"]):
        return "git stash drop"
    if sub in ("checkout", "restore") and ("--" in rest or "." in rest or sub == "restore") and "--staged" not in rest:
        return f"git {sub} discarding changes"
    if sub in ("filter-branch", "filter-repo", "update-ref") or (sub == "gc" and any(t.startswith("--prune") for t in rest)):
        return f"git {sub}"
    if (_FORCE & set(rest)) and sub in ("checkout", "switch", "tag", "worktree", "pull", "rebase", "branch"):
        return f"git {sub} with a force flag"
    return None


def _http_risk(prog, rest):
    low = [t.lower() for t in rest]
    if prog in ("curl", "wget"):
        for i, t in enumerate(rest):
            m = re.fullmatch(r"(?:-X|--request|--method)(?:=|\s*)?(\w*)", t)
            if m:
                meth = (m.group(1) or (rest[i + 1] if i + 1 < len(rest) else "")).upper()
                if meth and meth not in ("GET", "HEAD", "OPTIONS"):
                    return f"{prog} {meth}"
        if any(t in _HTTP_BODY or t.startswith("--data") or t in ("--post-data", "--post-file") for t in rest):
            return f"{prog} sending a body"
    if prog in ("http", "https", "xh", "xhs") and {"post", "put", "patch", "delete"} & set(low):
        return f"{prog} sending a request"
    return None


def _segment_risk(words, depth=0):
    """Why this shell segment (already split into words) is a step that can't be undone, or None."""
    while words and (re.fullmatch(r"\w+=\S*", words[0]) or os.path.basename(words[0]) in _WRAPPERS):
        w = os.path.basename(words[0])
        words = words[1:]
        if w == "uv" and words[:1] == ["run"]:
            words = words[1:]
        elif w == "uv":
            words = ["uv"] + words
            break
        elif w == "sudo" or w == "doas":
            return "sudo"
    if not words:
        return None
    prog, rest = os.path.basename(words[0]), words[1:]
    if prog in ("bash", "sh", "zsh") and "-c" in rest and depth < 2 and rest.index("-c") + 1 < len(rest):
        return _command_risk(rest[rest.index("-c") + 1], depth + 1)
    if prog == "git":
        return _git_risk(rest)
    if prog in ("rm", "unlink", "rmdir"):
        flags = [t for t in rest if t.startswith("-") and not t.startswith("--")]
        if prog == "unlink" or any(re.search(r"[rRf]", t) for t in flags) or "--recursive" in rest or "--force" in rest:
            return "rm -rf" if prog == "rm" else prog
        return None
    if prog == "find" and ("-delete" in rest or "rm" in rest):
        return "find -delete"
    if prog in ("curl", "wget", "http", "https", "xh", "xhs"):
        return _http_risk(prog, rest)
    if prog in ("scp", "rsync", "sftp", "ftp") and (any(re.match(r"^[\w.@-]+:", t) for t in rest if not t.startswith("-")) or "--delete" in rest):
        return f"{prog} to a remote"
    if prog == "gh":
        pos = [t for t in rest if not t.startswith("-")]
        if tuple(pos[:2]) in _GH_RISKY:
            return "gh " + " ".join(pos[:2])
        if pos[:1] == ["api"]:
            for i, t in enumerate(rest):
                if t in ("-X", "--method") and i + 1 < len(rest) and rest[i + 1].upper() != "GET":
                    return "gh api " + rest[i + 1].upper()
        return None
    if prog in ("aws", "gcloud", "az"):
        if any(re.match(r"^(delete|terminate|remove|deregister|destroy|purge|deploy|publish|send)\b", t) or t in ("rm", "rb") for t in rest if not t.startswith("-")):
            return f"{prog} delete/deploy"
        return None
    if prog == "supabase":
        text = " ".join(rest)
        if re.search(r"\b(db (push|reset)|functions deploy|migration up|config push|projects delete|branches delete|secrets set)\b", text):
            return "supabase " + " ".join(rest[:2])
        return None
    if prog == "heroku" and re.search(r"destroy|reset|rollback|push|release", " ".join(rest)):
        return "heroku"
    if prog in _SQL_CLIENTS:
        return f"{prog} writing data" if _SQL_WRITE.search(" ".join(rest)) else None
    if prog in ("rails", "rake") and re.search(r"db:(drop|reset|migrate|schema:load|setup)", " ".join(rest)):
        return "database task"
    if prog == "docker" and rest[:1] == ["system"] and "prune" in rest:
        return "docker prune"
    if prog == "docker" and rest[:1] == ["compose"] and rest[1:2] == ["down"]:
        return "docker compose down"
    if prog in _SUBS:
        pos = [t for t in rest if not t.startswith("-")]
        sub = pos[0] if pos else None
        if prog == "vercel" and "--prod" in rest:
            return "vercel --prod"
        if prog == "prisma" and not re.search(r"\b(migrate (deploy|reset)|db (push|execute))\b", " ".join(rest)):
            return None
        if None in _SUBS[prog] or sub in _SUBS[prog] or (prog == "docker" and any(t == "prune" for t in pos)):
            return f"{prog} {sub or ''}".strip()
    if prog in _RUNNERS or prog.startswith(".") or words[0].startswith(("./", "/")) or prog.endswith(".sh"):
        if _RISKY_WORD.search(prog) or any(_RISKY_WORD.search(os.path.basename(t)) for t in rest if not t.startswith("-")):
            return "a deploy/publish/release script"
        if prog in ("python", "python3") and re.search(r"manage\.py", " ".join(rest)) and re.search(r"\b(migrate|flush|loaddata)\b", " ".join(rest)):
            return "database task"
    return None


def _command_risk(cmd, depth=0):
    """Why a Bash command line is a step that can't be undone (push, force, reset --hard, rm -rf, deploy, publish, a data write, a
    request that sends), or None. Its segments (; && || | newline) are judged by the program each starts with."""
    for seg in _shell_segments(cmd or ""):
        why = _segment_risk(_words(seg), depth)
        if why:
            return why
    return None


def _shell_segments(cmd):
    """The command split at ; & | and line ends, but not inside quotes (a node -e script or a commit message is one word of a command)."""
    out, cur, q, i = [], [], None, 0
    while i < len(cmd):
        ch = cmd[i]
        if q:
            if ch == "\\" and q == '"' and i + 1 < len(cmd):
                cur.append(ch); i += 1; ch = cmd[i]
            elif ch == q:
                q = None
            cur.append(ch)
        elif ch in "'\"":
            q = ch; cur.append(ch)
        elif ch in ";&|\n":
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


_VERB = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
_OUTWARD = {"send", "post", "reply", "publish", "delete", "remove", "drop", "destroy", "merge", "push", "share", "invite", "revoke",
            "deploy", "truncate", "message", "comment", "upload", "transfer", "unshare", "purge", "reset"}
_LOOKING = {"get", "list", "read", "search", "fetch", "find", "describe", "query", "show", "view", "count", "check", "is", "history",
            "replies", "unreads", "status", "info", "download", "export", "whoami", "me"}


def _tool_risk(name):
    """An MCP tool whose name (the part after the server's) is built on an outward verb (send, post, reply, publish, delete, share,
    merge…) and does not begin with a looking verb (get, list, search…): a tool that sends or deletes on someone else's system."""
    if not name.startswith("mcp__"):
        return None
    words = [w.lower() for w in _VERB.findall(name.split("__")[-1].replace("_", " ").replace("-", " "))]
    if words and words[0] not in _LOOKING and _OUTWARD & set(words):
        return "tool that sends/deletes: " + name
    return None


def _repo_key(lt, path):
    """The repository (its main checkout) a path lies in, whichever linked worktree it is reached through; None outside any."""
    d = os.path.realpath(path)
    while d and not os.path.isdir(d) and os.path.dirname(d) != d:
        d = os.path.dirname(d)
    common = lt.git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=d).strip() if os.path.isdir(d) else ""
    return os.path.dirname(common) if common.endswith(".git") else None


def _keeps_scratch(lt, path):
    real = os.path.realpath(path)
    for own in (os.path.join(lt.claude, "plans"), os.path.join(lt.claude, "projects")):   # Claude Code's plan and memory folders
        own = os.path.realpath(own)
        if real == own or real.startswith(own + os.sep):
            return True
    if real.startswith(lt.home + os.sep):   # a home folder is never scratch (a fixture's sits in the temp folder)
        return False
    scratch = {os.path.realpath(p) for p in (tempfile.gettempdir(), "/tmp", "/var/folders", os.path.join(lt.claude, "plans"),
                                              os.path.join(lt.claude, "projects"))}
    return any(real == s or real.startswith(s + os.sep) for s in scratch)


def _write_outside(lt, c):
    """A refused Write/Edit whose file lies outside the repository the session worked in (temp folders and Claude Code's own plan and
    memory folders are left out)."""
    path = _edited_file(c)
    cwd = (lt.sessions.get(c["s"]) or {}).get("cwd")
    if not path or not cwd or not os.path.isabs(path) or _keeps_scratch(lt, path):
        return False
    home = _repo_key(lt, cwd)
    there = _repo_key(lt, path)
    if home:
        return there != home
    real, base_ = os.path.realpath(path), os.path.realpath(cwd)
    return not (real == base_ or real.startswith(base_ + os.sep))


def _risk_of_refusal(lt, c):
    if c["name"] in ("Bash", "PowerShell"):
        return _command_risk(_cmd(c))
    if c["name"] in EDIT_TOOLS:
        return "write outside the repo" if _write_outside(lt, c) else None
    return _tool_risk(c["name"])


@check(ASK, "L2", 0)
def ask_l2_0(lt):
    """Steps the person refused (toolDenialKind user-rejected, any interactive entrypoint, sub-agents' steps included) of a risky kind:
    a Bash command that pushes, resets --hard, force-flags, deletes (rm -rf, find -delete), deploys or publishes, drops or deletes data, or
    sends outside (curl POST, scp, mail, gh pr comment); an MCP tool built on an outward verb (send, post, delete, share, merge…); a
    Write/Edit outside the repository the session worked in. A refused edit inside the repo ("no, do X instead") is steering and is not
    counted; nor are plan approvals and dismissed questions (Claude Code's own dialogs). The command is judged by the program each
    shell segment starts with, so a word inside quotes ("git commit -m 'push'") is not a push."""
    rej = [c for c in lt.calls if c.get("denial") == "user-rejected" and lt.interactive(c["s"])]
    dialogs = [c for c in rej if c["name"] in _PLAN_OR_QUESTION]
    kept = []
    for c in rej:
        if c in dialogs:
            continue
        why = _risk_of_refusal(lt, c)
        if why:
            kept.append((c, why))
    return {"value": len(kept), "evidence": _ev(kept, f=lambda k: f"{k[0]['name']}: {k[1]}: {(_cmd(k[0]) or _edited_file(k[0]))[:60]}"),
            "note": f"{len(rej)} refusals read; {len(dialogs)} plan approvals/questions and {len(rej) - len(dialogs) - len(kept)} refusals "
                    f"of steps that can be redone (in-repo edits, tests, reads) not counted; the risk is read from the command or tool name"}


@fixture(ASK, "L2", 0)
def _(fx):
    fx.repo("~/work/shop", {"a.ts": "x\n"})
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    v.typed("clean the build")
    v.refuse("Edit", {"file_path": fx.p("~/work/shop/a.ts")})                                    # trap: steering, an edit in the repo
    v.refuse("Write", {"file_path": fx.p("~/work/shop/src/new.ts")})                             # trap: a new file in the repo
    v.refuse("Bash", {"command": "rm -rf build"})                                                # counts: delete
    v.refuse("Bash", {"command": "cd api && git push origin main"})                              # counts: push
    v.refuse("Bash", {"command": "git reset --hard HEAD~1"})                                     # counts
    v.refuse("Bash", {"command": "curl -X POST https://hooks.example/x -d '{}'"})                # counts: sends outside
    v.refuse("Bash", {"command": "psql -c \"DROP TABLE users\""})                                # counts: data
    v.refuse("mcp__chat__conversations_add_message", {"text": "hi"})                             # counts: messages someone
    v.refuse("Bash", {"command": "git commit -m 'push the deploy later'"})                       # trap: the words are inside quotes
    v.refuse("Bash", {"command": "git status && npm test"})                                      # trap: refused but redoable
    v.refuse("Bash", {"command": "rm notes.txt"})                                                # trap: one plain file
    v.refuse("Bash", {"command": "git reset --soft HEAD~1"})                                     # trap
    v.refuse("Bash", {"command": "curl https://api.example/items"})                              # trap: a GET
    v.refuse("Bash", {"command": "kubectl get pods"})                                            # trap: a read
    v.refuse("mcp__chat__channels_list", {})                                                     # trap: a read tool
    v.refuse("Write", {"file_path": "/tmp/scratch.txt"})                     # trap: temp folder
    v.refuse("ExitPlanMode", {"plan": "x"})                                                      # trap: a plan approval, not a risky step
    v.call("Bash", {"command": "git push --force"}, "exit 1", is_error=True)                     # trap: an error nobody refused
    d = fx.session("~/work/shop", entrypoint="claude-desktop")                                   # desktop writes no history.jsonl
    d.refuse("Write", {"file_path": fx.p("~/notes/b.ts")})                                       # counts: outside the repo
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                                          # trap: a headless run
    h.refuse("Bash", {"command": "git push"})
    return {"value": 7}


@check(ASK, "L2", 1)
def ask_l2_1(lt):
    """Interactive sessions in which the permission mode took 2+ values (permission-mode records and the mode on typed messages)."""
    modes = {s: set(m) for s, m in _scan(lt)["pmode"].items()}
    for m in lt.typed:
        if m.get("pm"):
            modes.setdefault(m["s"], set()).add(m["pm"])
    changed = {s: m for s, m in modes.items() if len(m) > 1 and lt.interactive(s)}
    return {"value": len(changed), "evidence": _ev(changed.items(), f=lambda kv: f"{kv[0][:8]}: {sorted(kv[1])}"),
            "note": "approving a plan flips the mode too; this does not say who flipped it"}


@fixture(ASK, "L2", 1)
def _(fx):
    a = fx.session("~/work/shop", entrypoint="claude-vscode")
    a.typed("plan it"); a.record("permission-mode", permissionMode="plan"); a.record("permission-mode", permissionMode="default")
    b = fx.session("~/work/shop", entrypoint="claude-desktop")
    b.typed("one mode only"); b.typed("still the same")
    c = fx.session("~/work/shop", entrypoint="cli")                      # a mode set once in a terminal session, never changed
    c.record("permission-mode", permissionMode="default"); c.typed("go")
    e = fx.session("~/work/shop", entrypoint="claude-desktop")           # changed only in the mode on typed messages
    e.typed("a", permission_mode="default"); e.typed("b", permission_mode="acceptEdits")
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                  # trap: a headless run flips modes
    h.record("permission-mode", permissionMode="plan"); h.record("permission-mode", permissionMode="bypassPermissions")
    return {"value": 2}


@check(ASK, "L2", 2)
def ask_l2_2(lt):
    """Typed messages (any interactive entrypoint) sent in a mode the person moved to that still asks or confines (plan, acceptEdits,
    auto). `default` is Claude Code's out-of-the-box mode, in force for everyone who never touched a setting: it is not counted;
    bypassPermissions and dontAsk ask nothing."""
    typed = [m for m in lt.typed if m.get("pm") and lt.interactive(m["s"])]
    chosen = [m for m in typed if m["pm"] in _ASKING_MODES and m["pm"] != "default"]
    share = {}
    for m in typed:
        share[m["pm"]] = share.get(m["pm"], 0) + 1
    return {"value": len(chosen), "evidence": [f"{k}: {v}" for k, v in sorted(share.items())][:3],
            "note": f"{len(typed)} typed messages carry a mode; those in default (what nobody had to choose) are not counted"}


@fixture(ASK, "L2", 2)
def _(fx):
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    for _ in range(3):
        v.typed("work", permission_mode="default")                      # trap: Claude Code's own default
    v.typed("think first", permission_mode="plan")
    v.typed("edit freely", permission_mode="acceptEdits")
    v.typed("classify", permission_mode="auto")
    for _ in range(2):
        v.typed("just do it", permission_mode="bypassPermissions")      # trap: asks nothing
    h = fx.session("~/work/shop", entrypoint="sdk-py")                  # trap: five headless messages in plan mode
    for _ in range(5):
        h.typed("go", permission_mode="plan")
    return {"value": 3}


_READ_ONLY = ("Read", "Grep", "Glob", "LS", "NotebookRead", "WebFetch", "WebSearch", "TodoWrite")   # Claude Code's tools that change nothing


def _rule_tool(rule):
    m = re.match(r"^\s*([^()\s]+)\s*(?:\((.*)\))?\s*$", rule, re.S)
    return (m.group(1), (m.group(2) or "").strip()) if m else ("", "")


@check(ASK, "L3", 0)
def ask_l3_0(lt):
    """Distinct rules, in the person's own settings files (user, local, and a project file git says is theirs; never managed
    settings or a teammate's file), that make Claude Code ask or refuse before a step that changes something: an `ask` rule on any
    tool that isn't read-only, or a `deny` rule that names what it refuses (`Bash(git push --force:*)`, not a whole tool
    switched off). Allow rules are not counted: Claude Code writes one each time the person clicks 'don't ask again'."""
    p = _perms_mine(lt, ("deny", "ask", "allow"))
    chosen = []
    for (kind, rule) in p:
        tool, spec = _rule_tool(rule)
        if kind == "allow" or not tool or tool in _READ_ONLY:
            continue
        if kind == "ask" or (spec and spec != "*"):
            chosen.append((kind, rule))
    allow = sum(1 for k in p if k[0] == "allow")
    return {"value": len(chosen), "evidence": _ev(sorted(chosen), f=lambda k: f"{k[0]} {k[1]}"),
            "note": f"{allow} allow rules, bare denies of a whole tool and rules on read-only tools not counted; employer (managed) and teammates' settings left out"}


@fixture(ASK, "L3", 0)
def _(fx):
    fx.settings("user", {"permissions": {"deny": ["Bash(rm -rf:*)", "Artifact", "NotebookEdit", "Read(.env)"], "ask": ["Bash(git push:*)", "Write"],
                                           "allow": ["Bash(ls:*)", "Bash(cat:*)", "Bash(npm test:*)"]}})
    proj = {"permissions": {"deny": ["Bash(git push --force:*)"], "allow": ["Bash(make:*)"]}}
    fx.repo("~/work/shop", {".claude/settings.json": json.dumps(proj)})
    fx._git(fx.p("~/work/shop"), "worktree", "add", "-q", fx.p("~/work/shop-wt"), "-b", "wt")   # a copy of the same file
    fx.repo("~/code/team", {".claude/settings.json": json.dumps({"permissions": {"deny": ["Bash(kubectl delete:*)"]}})}, by="teammate")   # trap: a teammate's rules
    fx.settings("managed", {"permissions": {"deny": ["Bash(curl:*)"], "ask": ["Edit"]}})                      # trap: the employer's
    fx.repo("~/code/elsewhere", {".claude/settings.json": json.dumps({"permissions": {"deny": ["Edit(.env)"]}})})
    fx.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
    fx.session("~/work/shop-wt", entrypoint="claude-desktop").typed("hi")      # elsewhere is never a session folder
    fx.session("~/code/team", entrypoint="claude-vscode").typed("hi")
    return {"value": 4}


def _default_mode(lt, cwd):
    """The defaultMode in force for a session folder: its project local, project, then user settings."""
    memo = lt.__dict__.setdefault("_g3_defmode", {})
    if cwd not in memo:
        memo[cwd] = _default_mode_of(lt, cwd)
    return memo[cwd]


def _default_mode_of(lt, cwd):
    root = lt.repo_of(cwd) or cwd
    order = []
    for scope in ("managed", "project-local", "project", "user-local", "user"):
        for sc, path, d in _settings(lt):
            if sc != scope:
                continue
            if scope.startswith("project") and os.path.realpath(os.path.dirname(os.path.dirname(path))) not in (
                    os.path.realpath(root), os.path.realpath(cwd or "")):
                continue
            order.append(d)
    for d in order:
        m = (d.get("permissions") or {}).get("defaultMode") if isinstance(d.get("permissions"), dict) else None
        if m:
            return m
    return "default"


@check(ASK, "L3", 1)
def ask_l3_1(lt):
    """Typed messages (interactive) sent in the mode the settings name as default for that folder (default when none is set):
    how much of the person's work stays in the mode they chose."""
    n, total, cfg = 0, 0, {}
    for m in lt.typed:
        if not m.get("pm") or not lt.interactive(m["s"]):
            continue
        dm = _default_mode(lt, (lt.sessions.get(m["s"]) or {}).get("cwd"))
        cfg[dm] = cfg.get(dm, 0) + 1
        total += 1
        n += m["pm"] == dm
    return {"value": n, "evidence": [f"{n} of {total} typed messages in the configured default mode", f"defaults read: {cfg}"],
            "note": "plan mode is switched on by hand, so it always reads as a departure from the default"}


@fixture(ASK, "L3", 1)
def _(fx):
    fx.settings("user", {"permissions": {"defaultMode": "acceptEdits"}})
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    for _ in range(3):
        v.typed("edit", permission_mode="acceptEdits")
    for _ in range(2):
        v.typed("fast", permission_mode="bypassPermissions")
    v.typed("think", permission_mode="plan")
    h = fx.session("~/work/shop", entrypoint="sdk-ts")                  # trap: headless messages in the default mode
    for _ in range(4):
        h.typed("go", permission_mode="acceptEdits")
    return {"value": 3}


_BLOCKS = re.compile(r"exit\s*\(?\s*2\b|permissionDecision[\"']?\s*[:=]\s*[\"'](?:deny|ask)|[\"']decision[\"']\s*:\s*[\"']block")


def _can_refuse(e):
    """Can this hook stop a step? Claude Code's own protocol: a script exits 2, or answers with permissionDecision deny/ask or
    decision block. Read from the script (any language), or from an inline command's own text. Comment lines are not code."""
    text = _read(e["script"]) if e["script"] else e["command"]
    return bool(_BLOCKS.search(_code_lines(text[:300_000])))


@check(ASK, "L4", 0)
def ask_l4_0(lt):
    """PreToolUse hooks wired in any settings file whose script (any language) is the person's own by the one ownership
    rule (_owned: a file git doesn't know is theirs unless a receipt says installed) and that can refuse a step: it exits 2 or
    answers permissionDecision deny/ask (Claude Code's hook protocol). A hook that colours a tab or logs does not ask."""
    pre = [e for e in _hook_entries(lt) if e["event"] == "PreToolUse"]
    own = [e for e in pre if e["yours"] and _can_refuse(e)]
    return {"value": len(own), "evidence": _ev(own, f=lambda e: f"{e['scope']}: {os.path.basename(e['script'] or '') or e['command']}"),
            "note": f"{len(pre) - len(own)} other PreToolUse hooks wired (a teammate's or installed, or that never refuse); plugin hooks.json files are not read"}


@fixture(ASK, "L4", 0)
def _(fx):
    block = "#!/bin/sh\n# deny risky steps\nexit 2\n"
    fx.repo("~/work/shop", {"scripts/guard.sh": block}, by="teammate", message="guard")                  # trap: a teammate's gate
    fx.commit("~/work/shop", {"scripts/guard.sh": block + "# v2\n"}, by="teammate")
    fx.commit("~/work/shop", {"scripts/confirm.ps1": "exit 2\n"}, by="work", message="confirm")           # own, not a .sh/.py/.js file
    fx.commit("~/work/shop", {"scripts/deny.py": "import json\nprint(json.dumps({'hookSpecificOutput': {'permissionDecision': 'deny'}}))\n"}, by="work")
    fx.commit("~/work/shop", {"scripts/tab.sh": "#!/bin/sh\necho colour\n# exit 2 would block\nexit 0\n"}, by="work")   # trap: own, never refuses
    fx.file("~/.claude/hooks/installed.sh", "#!/bin/sh\nexit 2\n")                                       # counts: untracked but no install receipt = theirs (ruling 11-05)
    wire = lambda cmd, ev="PreToolUse": {ev: [{"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]}]}
    both = {"hooks": {**wire('"$CLAUDE_PROJECT_DIR"/scripts/guard.sh'),
                      "PostToolUse": [{"hooks": [{"type": "command", "command": "sh scripts/confirm.ps1"}]}]}}
    both["hooks"]["PreToolUse"].append({"matcher": "Edit", "hooks": [{"type": "command", "command": "powershell -File scripts\\confirm.ps1"}]})
    both["hooks"]["PreToolUse"].append({"matcher": "Write", "hooks": [{"type": "command", "command": "python3 scripts/deny.py"}]})
    both["hooks"]["PreToolUse"].append({"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": "sh scripts/tab.sh"}]})
    fx.commit("~/work/shop", {".claude/settings.json": json.dumps(both)}, by="work")
    fx.settings("user", {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "sh ~/.claude/hooks/installed.sh"},
                                                              {"type": "command", "command": "echo checking; exit 2"}]}]}})   # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
    return {"value": 4}      # confirm.ps1, deny.py, installed.sh, the inline exit 2


# ====================================================================================== prove-it-works
PROVE = "prove-it-works"
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_COMMIT = re.compile(r"\bgit\s+(?:-[Cc]\s+\S+\s+)*commit\b")


def _toplevel(lt, path):
    """The checkout (a worktree too) a path or session folder is in: the nearest folder above it holding a .git (a folder
    for a repo, a file for a worktree), which is what `git rev-parse --show-toplevel` answers. Walked once per folder,
    no git process."""
    d = path if os.path.isdir(path or "") else os.path.dirname(path or "")
    if not d or not os.path.isdir(d):
        return None
    memo = lt.__dict__.setdefault("_g3_top", {})
    if d not in memo:
        cur, hit = os.path.realpath(d), None
        while hit is None:
            if os.path.exists(os.path.join(cur, ".git")):
                hit = cur
            elif os.path.dirname(cur) == cur:
                break
            else:
                cur = os.path.dirname(cur)
        memo[d] = hit
    return memo[d]


@check(PROVE, "L2", 1)
def prove_l2_1(lt):
    """Commits (made by the agent in an interactive session) that follow a run of a command the repo declares as a check
    after the last file edit since the previous commit. A check chained in the commit's own command counts."""
    n, total, ev = 0, 0, []
    by_s = {}
    inter = {}   # lt.interactive once per session, not once per call
    for c in lt.calls:
        if not c["sc"]:
            ok = inter.get(c["s"])
            if ok is None:
                ok = inter[c["s"]] = lt.interactive(c["s"])
            if ok:
                by_s.setdefault(c["s"], []).append(c)
    for sid, calls in by_s.items():
        cwd = (lt.sessions.get(sid) or {}).get("cwd")
        last, ran = None, False
        for c in calls:
            if c["name"] in EDIT_TOOLS:
                last = _toplevel(lt, _edited_file(c)) or _toplevel(lt, cwd)
                ran = False
            elif c["name"] == "Bash":
                cmd = _cmd(c)
                m = _COMMIT.search(cmd)
                if m and c.get("err") is not True:
                    if last:
                        total += 1
                        if ran or _declared_cmd(lt, cmd[:m.start()], last):
                            n += 1
                            ev.append(f"{sid[:8]}: {cmd[:60]}")
                    last, ran = None, False
                elif last and _declared_cmd(lt, cmd, last):
                    ran = True
    return {"value": n, "evidence": ev[:3], "note": f"{n} of {total} commits that followed an edit; a check no file of the repo declares is not seen"}


@fixture(PROVE, "L2", 1)
def _(fx):
    wf = "name: x\non: push\njobs:\n  j:\n    steps:\n      - run: npm ci\n      - run: uv build\n      - run: make deploy\n"
    fx.repo("~/work/shop", {"pom.xml": "<project/>", "Makefile": "check:\n\tmvn -q verify\ndeploy:\n\t./push.sh\n",
                            "package.json": json.dumps({"scripts": {"build": "tsc -b", "dev": "vite", "ci": "npm ci"}}),
                            ".github/workflows/ci.yml": wf})
    s = fx.session("~/work/shop", entrypoint="claude-vscode")
    f = lambda n: {"file_path": fx.p(f"~/work/shop/src/{n}.java")}
    s.call("Edit", f("A")); s.call("Bash", {"command": "mvn test"}); s.call("Bash", {"command": "git commit -am a"})        # counts
    s.call("Edit", f("B")); s.call("Bash", {"command": "git commit -am b"})                                                  # no check
    s.call("Edit", f("C")); s.call("Bash", {"command": "mvn test"}); s.call("Edit", f("D"))                                  # check, then edited again
    s.call("Bash", {"command": "git commit -am cd"})
    s.call("Edit", f("E")); s.call("Bash", {"command": "./run_my_thing.sh"}); s.call("Bash", {"command": "git commit -am e"})  # not declared
    for cmd in ("npm ci", "uv build", "make deploy", "npm run build", "npm run dev"):                                          # traps: installs, builds, deploys, servers
        s.call("Edit", f("I")); s.call("Bash", {"command": cmd}); s.call("Bash", {"command": "git commit -am i"})
    d = fx.session("~/work/shop", entrypoint="claude-desktop")
    d.call("Edit", f("F")); d.call("Bash", {"command": "make check && git commit -am f"})                                    # counts, chained
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                                                                       # trap: headless
    h.call("Edit", f("G")); h.call("Bash", {"command": "mvn test"}); h.call("Bash", {"command": "git commit -am g"})
    return {"value": 2}


@check(PROVE, "L2", 2)
def prove_l2_2(lt):
    """Checks that did not start from the agent's own Bash call: lines the person typed with the shell prefix (a typed message
    holding <bash-input>) and runs of a hook the person wired (a hook attachment whose command is one of their own settings'
    hooks: _owned), each matched to the commands the repo declares as checks (test, lint, type
    check roles only: not an install, a build or a deploy). A plugin's hook is not theirs."""
    typed, hooked = [], []
    for m in lt.typed:
        mm = re.match(r"\s*<bash-input>(.*?)</bash-input>", m["text"], re.S)
        if mm and lt.interactive(m["s"]):
            top = _toplevel(lt, (lt.sessions.get(m["s"]) or {}).get("cwd"))
            if _declared_cmd(lt, mm.group(1), top):
                typed.append(mm.group(1).strip())
    own = {e["command"] for e in _hook_entries(lt) if e["yours"] and e["event"] != "statusLine"}
    for h in _scan(lt)["hooks"]:
        if h["command"] and h["command"] in own and lt.interactive(h["s"]):
            repo = _toplevel(lt, (lt.sessions.get(h["s"]) or {}).get("cwd"))   # the session's own repo, not all 89
            if _declared_cmd(lt, h["command"], repo):
                hooked.append(h["command"])
    return {"value": len(typed) + len(hooked), "evidence": _ev(typed + hooked),
            "note": f"{len(typed)} typed by the person, {len(hooked)} run by a hook of theirs; the rest of the checks are the agent's own"}


@fixture(PROVE, "L2", 2)
def _(fx):
    fx.repo("~/work/shop", {"pom.xml": "<project/>", "Makefile": "check:\n\tmvn -q verify\ndeploy:\n\t./push.sh\n",
                            ".github/workflows/ci.yml": "name: x\non: push\njobs:\n  j:\n    steps:\n      - run: npm ci\n      - run: uv build\n"})
    fx.commit("~/work/shop", {".claude/settings.json": json.dumps({"hooks": {"PostToolUse": [{"hooks": [
        {"type": "command", "command": "make check"}, {"type": "command", "command": "echo saved"}]}]}})}, by="work")
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    v.typed("<bash-input>mvn test</bash-input>")                          # the person ran a check
    v.typed("<bash-input>ls -la</bash-input>")                            # trap: shell line, not a check
    v.typed("<bash-input>npm ci</bash-input>")                            # trap: an install a CI file runs
    v.typed("<bash-input>make deploy</bash-input>")                       # trap: a deploy
    v.call("Bash", {"command": "mvn test"})                               # trap: the agent's own call
    v.attachment("hook_success", hookName="PostToolUse:Edit", hookEvent="PostToolUse", command="make check", exitCode=0)
    v.attachment("hook_success", hookName="PostToolUse:Edit", hookEvent="PostToolUse", command="echo saved", exitCode=0)
    v.attachment("hook_success", hookName="PostToolUse:Edit", hookEvent="PostToolUse", command="mvn verify", exitCode=0)   # trap: a plugin's hook
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                   # trap: a headless run's hook
    h.attachment("hook_success", hookName="Stop", hookEvent="Stop", command="make check", exitCode=0)
    return {"value": 2}


_AFTER_CHANGE = ("PostToolUse", "Stop")   # hook events that follow the agent's change or end its turn


@check(PROVE, "L3", 3)
def prove_l3_3(lt):
    """Things the person set up that run a declared check on every change: a CI file of theirs that starts on a push or a pull
    request (not only tags, a schedule or a click) with a step running one; a git hook of theirs (_owned) that runs one;
    a hook of theirs in settings (PostToolUse or Stop; its script _owned) that runs one. Read in
    the repos the sessions ran in. A file git doesn't track is theirs unless a receipt says installed (ruling 11-05)."""
    found = []
    for r in lt.repos:
        for kind, f in _mechanisms(lt, r, _repo_pats(lt, r)):
            found.append(f"{kind}: {os.path.relpath(f, r)}")
    pats = _all_pats(lt)
    for e in _hook_entries(lt):
        if e["yours"] and e["event"] in _AFTER_CHANGE and (_is_check(pats, e["command"]) or (e["script"] and _is_check(pats, _code_lines(_read(e["script"]))))):
            found.append(f"settings hook {e['event']}: {e['command'][:40]}")
    return {"value": len(found), "evidence": found[:3],
            "note": "CI runs are not on the laptop; a check hidden behind a script the repo doesn't declare is not seen"}


@fixture(PROVE, "L3", 3)
def _(fx):
    fx.repo("~/work/shop", {"pom.xml": "<project/>", "Makefile": "check:\n\tmvn -q verify\n"})
    wf = lambda cmd, on="on: push": f"name: x\n{on}\njobs:\n  j:\n    steps:\n      - run: {cmd}\n"
    fx.commit("~/work/shop", {".github/workflows/ci.yml": wf("mvn test")}, by="work")                    # counts
    fx.commit("~/work/shop", {".github/workflows/notify.yml": wf("echo hi")}, by="work")                  # trap: no check in it
    fx.commit("~/work/shop", {".github/workflows/lint.yml": wf("mvn verify")}, by="teammate")             # trap: a teammate's
    fx.commit("~/work/shop", {".github/workflows/release.yml": wf("mvn test", "on:\n  push:\n    tags: ['v*']")}, by="work")   # trap: a release trigger
    fx.commit("~/work/shop", {".github/workflows/nightly.yml": wf("mvn test", "on:\n  schedule:\n    - cron: '0 3 * * *'\n  workflow_dispatch:")}, by="work")   # trap
    fx.commit("~/work/shop", {".github/workflows/pr.yml": wf("make check", "on:\n  pull_request:\n    branches: [main]")}, by="work")   # counts: every pull request
    fx.commit("~/work/shop", {".husky/pre-push": "#!/bin/sh\n# mvn test\nmvn test\n"}, by="work")        # counts once git runs it from here
    fx._git(fx.p("~/work/shop"), "config", "core.hooksPath", ".husky")
    fx.commit("~/work/shop", {".claude/settings.json": json.dumps({"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "make check"}]}]}})}, by="work")   # counts
    fx.settings("user", {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "make check"}]}]}})   # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.repo("~/work/api", {"pom.xml": "<project/>"})
    fx.file("~/work/api/.git/hooks/pre-push", "#!/bin/sh\nmvn test\n")                                    # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.file("~/work/api/.git/hooks/pre-commit.sample", "#!/bin/sh\nmvn test\n")                           # trap: a sample
    fx.repo("~/code/unused", {"pom.xml": "<project/>", ".github/workflows/ci.yml": wf("mvn test")})       # trap: no session there
    fx.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
    fx.session("~/work/api", entrypoint="claude-vscode").typed("hi")
    return {"value": 6}      # ci.yml, pr.yml, husky pre-push, the PostToolUse settings, the user Stop, api's pre-push


@check(PROVE, "L4", 0)
def prove_l4_0(lt):
    """An unattended runner that keeps the agent going until a check a script decides passes. Claude Code's hook_blocking_error
    record names the event (Stop) but not the hook that blocked, and nothing records who started the headless run: a plugin's
    own runs (a Stop reminder to file notes) leave the same record as a person's loop, so a count would credit the plugin."""
    return {"value": None, "evidence": [], "note": "can't tell from a laptop: the blocking record doesn't name the hook, and the log doesn't say who started the headless run"}


@fixture(PROVE, "L4", 0)
def _(fx):
    h = fx.session("~/work/shop", entrypoint="sdk-cli")
    h.attachment("hook_blocking_error", hookName="Stop", hookEvent="Stop", blockingError={"blockingError": "Before you stop: file memory"})   # trap: a plugin's reminder
    return {"value": None}


@check(PROVE, "L4", 1)
def prove_l4_1(lt):
    """Stop and PreToolUse hooks (the two that can stop the agent or a step) wired in settings whose script is the person's own
    by the one ownership rule (_owned: not installed, not most of its commits someone else's; a script git doesn't track is theirs).
    Whether the script can refuse is a separate reading."""
    own = [e for e in _hook_entries(lt) if e["event"] in ("Stop", "PreToolUse") and e["yours"] and e["script"]]
    return {"value": len(own), "evidence": _ev(own, f=lambda e: f"{e['event']}: {os.path.basename(e['script'])}"),
            "note": "an inline command with no script file is not credited; plugin hook files are not read"}


@fixture(PROVE, "L4", 1)
def _(fx):
    fx.repo("~/work/shop", {".claude/hooks/stop-check.mjs": "process.exit(2)\n"}, by="work")
    fx.commit("~/work/shop", {".claude/hooks/redo.sh": "exit 2\n"}, by="teammate")                         # a teammate's script
    fx.commit("~/work/shop", {".claude/hooks/after.sh": "exit 0\n"}, by="work")                            # own, but a PostToolUse
    wire = lambda ev, cmd: {ev: [{"hooks": [{"type": "command", "command": cmd}]}]}
    hooks = {**wire("Stop", "node $CLAUDE_PROJECT_DIR/.claude/hooks/stop-check.mjs"), **wire("PostToolUse", "sh .claude/hooks/after.sh")}
    hooks["Stop"].append({"hooks": [{"type": "command", "command": "sh .claude/hooks/redo.sh"}]})
    fx.commit("~/work/shop", {".claude/settings.json": json.dumps({"hooks": hooks})}, by="work")
    fx.file("~/.claude/hooks/gate.py", "print('ok')\n")                                                     # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.file("~/.claude/hooks/third-party-stop.mjs", "process.exit(2)\n")                                    # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.repo("~/dotfiles", {"hooks/pre.sh": "exit 2\n"}, by="home")                                         # theirs: tracked, their commit
    fx.settings("user", {"hooks": {**wire("PreToolUse", "python3 ~/.claude/hooks/gate.py"), "Stop": [{"hooks": [
        {"type": "command", "command": "node ~/.claude/hooks/third-party-stop.mjs"}]}]}})
    fx.settings("user-local", {"hooks": wire("PreToolUse", "sh ~/dotfiles/hooks/pre.sh")})
    fx.session("~/work/shop", entrypoint="claude-desktop").typed("hi")
    return {"value": 4}      # stop-check.mjs, gate.py, third-party-stop.mjs, dotfiles pre.sh; redo.sh is a teammate's


def _rule_path(rule):
    m = re.match(r"^\w+\((.*)\)$", rule.strip())
    if not m:
        return ""
    a = _norm(m.group(1))
    for pre in ("~/", "./", "/", "**/"):
        if a.startswith(pre):
            a = a[len(pre):]
    return re.sub(r"(:\*|/\*\*?|\*)$", "", a).strip("/")


@check(PROVE, "L4", 2)
def prove_l4_2(lt):
    """Distinct deny/ask rules (in the person's own settings files; never managed settings or a teammate's file) on Edit, Write,
    MultiEdit, NotebookEdit or Bash whose path covers, or lies inside, a test path of a repo: what its test config
    (pytest testpaths, jest roots) or a command that runs its tests names (`pytest qa/`). A project file's rule is held against
    its own repo; a user file's against any. A path some other declared command names (scripts/, src/, .env) is not a test path."""
    hits = []
    for (kind, rule), (scope, path) in sorted(_perms_mine(lt, ("deny", "ask")).items()):
        if not re.match(r"^(Edit|Write|MultiEdit|NotebookEdit|Bash)\(", rule):
            continue
        a = _rule_path(rule)
        if not a:
            continue
        own = lt.repo_of(os.path.dirname(os.path.dirname(path))) if scope.startswith("project") else None
        for r in ([own] if own else [] if scope.startswith("project") else lt.repos):
            if any(a == d or a.startswith(d + "/") or d.startswith(a + "/") for d in _declared_paths(r)):
                hits.append(f"{kind} {rule}")
                break
    return {"value": len(hits), "evidence": hits[:3], "note": "a repo that declares no test path reads 0; a hook that guards the same path is not counted here"}


@fixture(PROVE, "L4", 2)
def _(fx):
    fx.repo("~/work/shop", {"pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["qa"]\n', "Makefile": "test:\n\tpytest qa/ -q\nlint:\n\truff check scripts/ .env\ndeploy:\n\t./scripts/push.sh\n",
                            "qa/test_a.py": "def test_a(): pass\n", "docs/a.md": "x\n", "src/a.py": "x=1\n", "scripts/push.sh": "x\n", ".env": "K=1\n"})
    fx.commit("~/work/shop", {".claude/settings.json": json.dumps({"permissions": {
        "deny": ["Edit(qa/**)", "Edit(docs/**)", "Edit(src/test_utils/**)", "Bash(rm -rf:*)", "Edit(scripts/**)", "Edit(.env)"],
        "ask": ["Write(./qa/cases/*)"]}})}, by="work")
    fx.settings("managed", {"permissions": {"deny": ["Edit(qa/test_a.py)"]}})                                   # trap: the employer's rule
    fx.repo("~/code/team", {"Makefile": "test:\n\tpytest tests/\n", "tests/test_t.py": "x=1\n"}, by="teammate")
    fx.commit("~/code/team", {".claude/settings.json": json.dumps({"permissions": {"deny": ["Edit(tests/**)"]}})}, by="teammate")   # trap: a teammate's rule
    fx.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
    fx.session("~/code/team", entrypoint="claude-vscode").typed("hi")
    return {"value": 2}


# ====================================================================================== work-with-the-prompt-cache
CACHE = "work-with-the-prompt-cache"
_TOGGLES = ("/mcp", "/plugin", "/plugins", "/reload-plugins")   # Claude Code's built-in commands that change the tool set


def _a(s, mid, model="claude-sonnet-5", inp=2, cc=0, cr=0, sidechain=False, effort=None, usage=True, agents=(), stop=None):
    """Fixture helper: one assistant record the way Claude Code writes it (message id, model, usage, effort)."""
    blocks = [{"type": "text", "text": "ok"}] + [{"type": "tool_use", "id": "toolu_" + a, "name": "Agent", "input": {"subagent_type": a, "prompt": "x"}} for a in agents]
    msg = {"model": model, "id": mid, "type": "message", "role": "assistant", "content": blocks}
    if stop:
        msg["stop_reason"] = stop
    if usage:
        msg["usage"] = {"input_tokens": inp, "cache_creation_input_tokens": cc, "cache_read_input_tokens": cr, "output_tokens": 9}
    return s.record("assistant", message=msg, isSidechain=sidechain, **({"effort": effort} if effort else {}))


@check(CACHE, "L2", 0)
def cache_l2_0(lt):
    """Interactive sessions whose assistant messages carry usage numbers (input, cache read, cache written), so the hit rate
    and the cold restarts can be worked out. A route that omits the fields reads None, not 0."""
    msgs = _main_assist(lt)
    with_use = {a["s"] for a in msgs if a["usage"]}
    if msgs and not with_use:
        return {"value": None, "evidence": [], "note": "can't tell from a laptop: the assistant messages carry no usage fields (another route or an old version)"}
    read = sum(a["usage"][2] for a in msgs if a["usage"])
    all_ = sum(sum(a["usage"]) for a in msgs if a["usage"])
    return {"value": len(with_use), "evidence": [f"{len(with_use)} sessions with usage", f"{read} of {all_} prompt tokens read from cache"],
            "note": "usage is written for everyone; this shows it can be read, not that the person looks at it"}


@fixture(CACHE, "L2", 0)
def _(fx):
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    for i in range(3):
        _a(v, f"msg_v{i}", cc=100, cr=900)
    _a(v, "msg_v2", cc=100, cr=900)                                       # trap: the same message streamed again
    d = fx.session("~/work/shop", entrypoint="claude-desktop")
    _a(d, "msg_d0", cc=50, cr=0); _a(d, "msg_d1", cc=5, cr=50)
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                   # trap: a headless run
    _a(h, "msg_h0", cc=50)
    n = fx.session("~/work/shop", entrypoint="claude-vscode")             # trap: no usage fields in this one
    _a(n, "msg_n0", usage=False)
    return {"value": 2}


@check(CACHE, "L2", 1)
def cache_l2_1(lt):
    """Changes of model between consecutive main-thread assistant messages of an interactive session (sub-agents and
    placeholder messages left out). Fewer is better; the count is returned, the level threshold is set elsewhere."""
    sw, ev = 0, []
    for sid, ms in _by_session(_main_assist(lt)).items():
        prev = None
        for a in ms:
            if not a["model"] or a["model"].startswith("<"):
                continue
            if prev and a["model"] != prev:
                sw += 1
                ev.append(f"{sid[:8]}: {prev} -> {a['model']}")
            prev = a["model"]
    return {"value": sw, "evidence": ev[:3],
            "note": "a plan-mode model or a fallback also changes the model without the person; /model typed is not required to count"}


@fixture(CACHE, "L2", 1)
def _(fx):
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    _a(v, "m1", model="claude-opus-5"); _a(v, "m2", model="claude-opus-5")
    _a(v, "mx", model="claude-haiku-5", sidechain=True)                   # trap: a sub-agent's model
    _a(v, "m3", model="<synthetic>")                                      # trap: a placeholder
    _a(v, "m4", model="claude-sonnet-5")                                  # switch 1
    _a(v, "m5", model="claude-opus-5")                                    # switch 2
    d = fx.session("~/work/shop", entrypoint="claude-desktop")
    _a(d, "d1", model="claude-sonnet-5"); _a(d, "d2", model="claude-haiku-5")   # switch 3
    h = fx.session("~/work/shop", entrypoint="sdk-ts")                    # trap: headless
    _a(h, "h1", model="claude-sonnet-5"); _a(h, "h2", model="claude-opus-5")
    return {"value": 3}


@check(CACHE, "L2", 2)
def cache_l2_2(lt):
    """Built-in commands that change the tool set (/mcp, /plugin, /plugins, /reload-plugins) typed in an interactive session
    after its first assistant reply: terminal history and transcripts, so the desktop app and IDEs count."""
    first = {}
    for a in _main_assist(lt):
        first.setdefault(a["s"], a["t"])
    hits = []
    for c in lt.all_commands():
        if c["name"] not in _TOGGLES or c["s"] not in first or not lt.interactive(c["s"]):
            continue
        if c["t"] > first[c["s"]]:
            hits.append(c)
    return {"value": len(hits), "evidence": [f"{c['name']} in {c['s'][:8]}" for c in hits[:3]],
            "note": "a server or plugin switched by editing a file is not seen; the first reply, not a time window, marks the start"}


@fixture(CACHE, "L2", 2)
def _(fx):
    c = fx.session("~/work/shop", entrypoint="cli")                       # the terminal writes history.jsonl as well
    c.command("/mcp"); _a(c, "c1"); c.command("/reload-plugins")           # the first is before the reply: not mid-task
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    _a(v, "v1"); v.command("/mcp"); v.command("/compact")                  # /compact is not a toggle
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                   # trap: headless
    _a(h, "h1"); h.command("/plugin")
    return {"value": 2}


@check(CACHE, "L2", 4)
def cache_l2_4(lt):
    """Cold restarts: a main-thread message (not the session's first) that read nothing from cache while writing to it.
    No size or ratio cut-off. The tokens written by those restarts against all written are in the evidence."""
    n, cold_cc, all_cc = 0, 0, 0
    for sid, ms in _by_session(_main_assist(lt)).items():
        ms = [a for a in ms if a["usage"]]
        for i, a in enumerate(ms):
            all_cc += a["usage"][1]
            if i and a["usage"][2] == 0 and a["usage"][1] > 0:
                n += 1
                cold_cc += a["usage"][1]
    return {"value": n, "evidence": [f"{cold_cc} of {all_cc} cache tokens written by cold restarts"],
            "note": "does not say why the cache went cold (idle time, a model change, an edit to the start of the prompt)"}


@fixture(CACHE, "L2", 4)
def _(fx):
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    _a(v, "a1", cc=30000, cr=0)                                           # first message: not a restart
    _a(v, "a2", cc=500, cr=30000)
    _a(v, "a3", cc=31000, cr=0); _a(v, "a3", cc=31000, cr=0)              # cold restart (one message streamed twice)
    _a(v, "a4", cc=100, cr=31000)
    _a(v, "a5", cc=1, cr=0)                                               # cold restart, small
    _a(v, "a6", cc=40000, cr=0, sidechain=True)                           # trap: a sub-agent starting cold
    d = fx.session("~/work/shop", entrypoint="claude-desktop")
    _a(d, "b1", cc=20000, cr=0)                                           # trap: first message of another session
    h = fx.session("~/work/shop", entrypoint="sdk-py")
    _a(h, "h1", cc=100, cr=100); _a(h, "h2", cc=9000, cr=0)               # trap: headless
    return {"value": 2}


def _chosen_model_sessions(lt):
    """Session ids in which the person chose the model or the effort: a /model or /effort command typed before the session's first
    assistant message, or a settings file of theirs (user, local, or a project file git says is theirs) naming `model` or
    `effortLevel` for the session's folder."""
    by_s = _by_session(_main_assist(lt))
    first = {sid: min(a["t"] for a in ms) for sid, ms in by_s.items()}
    chosen = {c["s"] for c in lt.all_commands() if c["name"] in ("/model", "/effort") and c["s"] in first and c["t"] <= first[c["s"]]}
    everywhere, folders = False, set()
    for scope, path, d in _mine_settings(lt):
        if any(k in d for k in ("model", "effortLevel")):
            if scope.startswith("project"):
                folders.add(os.path.realpath(os.path.dirname(os.path.dirname(path))))
            else:
                everywhere = True
    in_folders = {}   # per distinct session folder, not per session
    for sid in by_s:
        cwd = (lt.sessions.get(sid) or {}).get("cwd")
        if everywhere:
            chosen.add(sid)
        elif folders and cwd:
            if cwd not in in_folders:
                in_folders[cwd] = os.path.realpath(cwd) in folders or os.path.realpath(lt.repo_of(cwd) or "") in folders
            if in_folders[cwd]:
                chosen.add(sid)
    return chosen


@check(CACHE, "L3", 0)
def cache_l3_0(lt):
    """Interactive sessions of 2+ main-thread messages in which the model and the effort never changed, and the person chose them
    at the start (a /model or /effort before the first answer, or a model or effort they put in their settings). A session where
    nothing was ever chosen is Claude Code's default, not a decision."""
    chosen = _chosen_model_sessions(lt)
    steady, ev, unchosen = 0, [], 0
    for sid, ms in _by_session(_main_assist(lt)).items():
        ms = [a for a in ms if a["model"] and not a["model"].startswith("<")]
        if len(ms) < 2:
            continue
        if len({a["model"] for a in ms}) == 1 and len({a["effort"] for a in ms if a["effort"]}) <= 1:
            if sid in chosen:
                steady += 1
                ev.append(f"{sid[:8]}: {ms[0]['model']} {ms[0]['effort'] or ''}")
            else:
                unchosen += 1
    return {"value": steady, "evidence": ev[:3],
            "note": f"{unchosen} steady sessions where nothing was chosen not counted; the plan-mode model and fallbacks change the model without the person; a --model flag is not recorded"}


@fixture(CACHE, "L3", 0)
def _(fx):
    a = fx.session("~/work/shop", entrypoint="claude-vscode")
    a.command("/model", "opus")                                            # chosen at the start
    for i in range(3):
        _a(a, f"a{i}", model="claude-opus-5", effort="high")
    _a(a, "ax", model="claude-haiku-5", sidechain=True)                   # trap: a sub-agent on another model
    b = fx.session("~/work/shop", entrypoint="claude-vscode")
    b.command("/model", "opus")
    _a(b, "b1", model="claude-opus-5"); _a(b, "b2", model="claude-sonnet-5")   # changed
    c = fx.session("~/work/shop", entrypoint="claude-desktop")
    c.command("/effort", "high")
    _a(c, "c1", effort="high"); _a(c, "c2", effort="low")                  # changed
    d = fx.session("~/work/shop", entrypoint="sdk-cli")                   # trap: headless and steady
    d.command("/model", "opus"); _a(d, "d1"); _a(d, "d2")
    e = fx.session("~/work/shop", entrypoint="claude-vscode")
    e.command("/model", "opus"); _a(e, "e1")                              # one message: nothing to compare
    g = fx.session("~/work/shop", entrypoint="claude-vscode")             # trap: steady because nobody touched it
    _a(g, "g1"); _a(g, "g2"); _a(g, "g3")
    m = fx.session("~/work/shop", entrypoint="claude-vscode")             # trap: the model is chosen only after an answer
    _a(m, "m1"); m.command("/model", "opus"); _a(m, "m2")
    fx.repo("~/work/api", {"README.md": "x"})
    fx.commit("~/work/api", {".claude/settings.json": json.dumps({"model": "opus"})}, by="work")
    k = fx.session("~/work/api", entrypoint="claude-vscode")              # chosen in the project's own settings
    _a(k, "k1"); _a(k, "k2")
    return {"value": 2}


# ====================================================================================== test-the-setup-code
TEST = "test-the-setup-code"


def _wire(cmd, ev="PreToolUse"):
    return {ev: [{"hooks": [{"type": "command", "command": cmd}]}]}


@check(TEST, "L2", 0)
def test_l2_0(lt):
    """Edit/Write calls (interactive sessions, sub-agents included) on a file that one of the person's settings wires as a
    hook or as the status line launcher, resolved to its real path. A folder called hooks proves nothing."""
    scripts = _hook_scripts(lt)
    hits, real = [], {}   # real: realpath once per distinct path
    for c in lt.calls:
        if c["name"] not in EDIT_TOOLS or not lt.interactive(c["s"]):
            continue
        p = _norm(_file_path(c))
        if p and os.path.isabs(p):
            if p not in real:
                real[p] = os.path.realpath(p) in scripts
            if real[p]:
                hits.append(p)
    return {"value": len(hits), "evidence": _ev(hits), "note": f"{len(scripts)} wired scripts found; plugin hooks.json files are not read"}


@fixture(TEST, "L2", 0)
def _(fx):
    fx.repo("~/work/shop", {"scripts/guard.sh": "exit 0\n", ".claude/hooks/readme.md": "notes\n"})
    fx.settings("project", {"hooks": _wire("sh scripts/guard.sh")}, repo="~/work/shop")
    fx.file("~/.claude/statusline.sh", "echo hi\n")
    fx.settings("user", {"statusLine": {"type": "command", "command": "sh ~/.claude/statusline.sh"}})
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    v.call("Edit", {"file_path": fx.p("~/work/shop/scripts/../scripts/guard.sh")})        # counts, through a relative hop
    v.call("Write", {"file_path": fx.p("~/work/shop/.claude/hooks/readme.md")})           # trap: in a hooks folder, not wired
    v.call("Edit", {"file_path": fx.p("~/work/shop/scripts/guard.sh")}, sidechain=True)   # counts: a sub-agent's edit
    v.call("Edit", {"file_path": fx.p("~/.claude/statusline.sh")})                        # counts: a launcher
    h = fx.session("~/work/shop", entrypoint="sdk-cli")
    h.call("Edit", {"file_path": fx.p("~/work/shop/scripts/guard.sh")})                   # trap: headless
    return {"value": 3}


@check(TEST, "L3", 0)
def test_l3_0(lt):
    """Test files the person wrote (a test by its runner's naming, or code in a path a test command of the repo names; never a
    document; _owned) that name, as a whole word, a script wired as one of their hooks or launchers (a script that is
    _owned)."""
    found = _hook_test_files(lt)
    return {"value": len(found), "evidence": [f"{os.path.basename(r)}/{rel}" for r, rel in found[:3]],
            "note": "a test that reaches the hook without naming its file is not seen; documents that mention a script are not tests"}


@fixture(TEST, "L3", 0)
def _(fx):
    fx.repo("~/work/shop", {"scripts/guard.sh": "exit 0\n", "docs/hook.md": "guard.sh blocks pushes\n", "docs/notes.txt": "guard.sh\n",
                            "Makefile": "test:\n\tpytest tests/ docs/\n\tbats tests/\n", "notes/README.md": "guard.sh runs first\n"})
    fx.commit("~/work/shop", {"tests/test_guard.py": "run('scripts/guard.sh')\n", "tests/test_math.py": "assert 1\n",
                              "tests/test_other.py": "run('unwired.sh')\n", "tests/guard.bats": "run scripts/guard.sh\n",
                              "tests/test_ownguard.py": "run('myguard.sh')\n"}, by="work")   # trap: a different script (a whole-word match)
    fx.commit("~/work/shop", {"tests/test_guard_team.py": "run('guard.sh')\n"}, by="teammate")     # trap: a teammate's test
    fx.file("~/.claude/hooks/inst.sh", "exit 0\n")                                                   # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.commit("~/work/shop", {"tests/test_inst.py": "run('inst.sh')\n"}, by="work")
    fx.settings("project", {"hooks": {**_wire("sh scripts/guard.sh"), "PostToolUse": [{"hooks": [{"type": "command", "command": "sh ~/.claude/hooks/inst.sh"}]}]}}, repo="~/work/shop")
    fx.session("~/work/shop", entrypoint="claude-desktop").typed("hi")
    return {"value": 3}      # test_guard.py, guard.bats, test_inst.py (inst.sh is theirs: untracked, no receipt)


@check(TEST, "L3", 1)
def test_l3_1(lt):
    """Repos holding such hook tests where a command the repo itself declares for running tests runs them: it names the test's
    path, or the test follows the naming of a runner the repo sets up (a pytest config or a command naming pytest, a package.json
    `test` script, go.mod for go). A language default alone (a pyproject.toml, nothing about tests) runs nothing. Once per repo."""
    reached = _reached_tests(lt)
    repos = sorted({r for r, _ in reached})
    held = sorted({r for r, _ in _hook_test_files(lt)})
    return {"value": len(repos), "evidence": [os.path.basename(r) for r in repos[:3]],
            "note": f"{len(held)} repos have hook tests; {len(held) - len(repos)} of them declare no command that runs them"}


@fixture(TEST, "L3", 1)
def _(fx):
    cases = (("a", {"Makefile": "test:\n\tpytest -q\n"}, "tests/test_a.py"),
             ("b", {}, "tests/test_b.py"),                                                          # no command declared
             ("c", {"Makefile": "test:\n\tpytest -q\n"}, None),                                   # no hook test
             ("d", {"pyproject.toml": "[project]\nname='d'\n"}, "tests/test_d.py"),                 # trap: a language default only
             ("e", {"package.json": json.dumps({"scripts": {"build": "tsc -b"}})}, "tests/e.test.js"),   # trap: no test script
             ("f", {"package.json": json.dumps({"scripts": {"test": "jest"}})}, "tests/f.test.js"))      # counts
    for name, extra, test in cases:
        files = {f"{name}.sh": "exit 0\n", **extra}
        if test:
            files[test] = f"run('{name}.sh')\n"
        fx.repo(f"~/work/{name}", files)
        fx.settings("project", {"hooks": _wire(f"sh {name}.sh")}, repo=f"~/work/{name}")
        fx.session(f"~/work/{name}", entrypoint="claude-vscode").typed("hi")
    return {"value": 2}


@check(TEST, "L4", 0)
def test_l4_0(lt):
    """In the repos whose own test command runs the person's hook tests: CI files of theirs that start on every change (push or
    pull request) with a step running a declared test command, and git hooks of theirs (_owned, or the pre-commit config
    they own once its hook is installed) that run one. Read from the repo's files; CI runs themselves are not on the laptop."""
    n, ev = 0, []
    for r in sorted({r for r, _ in _reached_tests(lt)}):
        for kind, f in _mechanisms(lt, r, _test_pats(lt, r)):
            n += 1
            ev.append(f"{kind}: {os.path.basename(r)}/{os.path.relpath(f, r)}")
    return {"value": n, "evidence": ev[:3], "note": "does not say the CI step reaches the hook tests"}


@fixture(TEST, "L4", 0)
def _(fx):
    wf = lambda cmd, on="on: push": f"name: x\n{on}\njobs:\n  j:\n    steps:\n      - run: {cmd}\n"
    fx.repo("~/work/a", {"a.sh": "exit 0\n", "Makefile": "test:\n\tpytest -q\nlint:\n\truff check .\n", "tests/test_a.py": "run('a.sh')\n",
                         ".github/workflows/ci.yml": wf("make test"), ".github/workflows/notify.yml": wf("echo done"),
                         ".github/workflows/lint.yml": wf("make lint"),                                    # trap: a lint, not the tests
                         ".github/workflows/release.yml": wf("make test", "on:\n  push:\n    tags: ['v*']")})   # trap: runs on a release only
    fx.commit("~/work/a", {".github/workflows/team.yml": wf("make test")}, by="teammate")                   # trap: a teammate's
    fx.file("~/work/a/.git/hooks/pre-push", "#!/bin/sh\nmake test\n")                                       # counts: untracked but no install receipt = theirs (ruling 11-05)
    fx.file("~/work/a/.git/hooks/pre-rebase.sample", "#!/bin/sh\nmake test\n")                              # trap: a sample
    fx.settings("project", {"hooks": _wire("sh a.sh")}, repo="~/work/a")
    fx.repo("~/work/b", {"Makefile": "test:\n\tpytest -q\n", ".github/workflows/ci.yml": wf("make test")})   # trap: no hook tests here
    fx.repo("~/work/c", {"c.sh": "exit 0\n", "Makefile": "test:\n\tpytest -q\n", "tests/test_c.py": "run('c.sh')\n", ".husky/pre-commit": "#!/bin/sh\nmake test\n"})
    fx._git(fx.p("~/work/c"), "config", "core.hooksPath", ".husky")                                          # counts: tracked, theirs
    fx.settings("project", {"hooks": _wire("sh c.sh")}, repo="~/work/c")
    fx.session("~/work/a", entrypoint="claude-vscode").typed("hi")
    fx.session("~/work/b", entrypoint="claude-vscode").typed("hi")
    fx.session("~/work/c", entrypoint="claude-vscode").typed("hi")
    return {"value": 3}      # a's ci.yml, a's pre-push, c's husky pre-commit


# ====================================================================================== watch-what-runs-did-and-cost
WATCH = "watch-what-runs-did-and-cost"
_USAGE_VIEWS = ("/usage", "/cost", "/stats", "/insights")   # Claude Code's built-in views
_TRANSCRIPT = re.compile(r"\.claude/projects/[^\s'\"*?\[]+\.jsonl|\.claude/history\.jsonl", re.I)   # one named log file, no glob


@check(WATCH, "L2", 0)
def watch_l2_0(lt):
    """Built-in usage views (/usage, /cost, /stats, /insights) typed by the person: transcripts of every interactive
    entrypoint plus terminal history, each once."""
    hits = [c for c in lt.all_commands() if c["name"] in _USAGE_VIEWS and (c["src"] == "history" or lt.interactive(c["s"]))]
    return {"value": len(hits), "evidence": [f"{c['name']} ({c['src']})" for c in hits[:3]],
            "note": "a view that leaves no record in the transcript or history (some versions) is not seen"}


@fixture(WATCH, "L2", 0)
def _(fx):
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    v.command("/cost"); v.command("/compact")
    c = fx.session("~/work/shop", entrypoint="cli")                      # transcript and history.jsonl both hold it: once
    c.command("/stats")
    d = fx.session("~/work/shop", entrypoint="claude-desktop")
    d.command("/usage"); d.typed("what does /cost show?")               # trap: a word in a sentence
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                  # trap: headless
    h.command("/usage")
    return {"value": 3}


def _paths_of(c):
    d = _inp(c)
    return _norm(" ".join(str(d.get(k) or "") for k in ("file_path", "path", "command")))


@check(WATCH, "L2", 2)
def watch_l2_2(lt):
    """The person reading back a run: a Read or Grep call (interactive sessions, main thread) on one named log file (a session .jsonl
    under Claude Code's projects folder, or history.jsonl), plus typed messages that cite such a file. A shell command or a
    script that walks the projects folder is not counted: a log-reading tool (a scan, a memory test) runs that way."""
    calls = [c for c in lt.calls if c["name"] in ("Read", "Grep") and not c["sc"] and lt.interactive(c["s"])
             and _TRANSCRIPT.search(_norm(" ".join(str(_inp(c).get(k) or "") for k in ("file_path", "path"))))]
    cited = [m for m in lt.typed if lt.interactive(m["s"]) and _TRANSCRIPT.search(_norm(m["text"]))]
    return {"value": len(calls) + len(cited), "evidence": _ev([_paths_of(c) for c in calls] + [m["text"] for m in cited]),
            "note": "shows the agent read a named log or the person cited one; a command run through the shell is not counted"}


@fixture(WATCH, "L2", 2)
def _(fx):
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    v.call("Read", {"file_path": "C:\\Users\\dana\\.claude\\projects\\x\\abc.jsonl"})
    v.call("Grep", {"pattern": "error", "path": "/home/dana/.claude/projects/-w/abc.jsonl"})
    v.typed("look at ~/.claude/history.jsonl for what I did")
    v.typed("read ~/.claude/projects/-w/abc.jsonl and tell me what went wrong")
    v.call("Bash", {"command": "ls ~/.claude/projects"})                           # trap: a listing
    v.call("Bash", {"command": "python3 logreader.py ~/.claude/projects/*/*.jsonl"})   # trap: a log-reading tool run through the shell
    v.call("Grep", {"pattern": "x", "path": "/home/dana/.claude/projects"})        # trap: the whole folder, not one run
    v.typed("show me the history of the repo")                            # trap: the word, no path
    v.call("Read", {"file_path": "/home/dana/.claude/settings.json"})    # trap: not a transcript
    v.call("Read", {"file_path": "/home/dana/.claude/projects/-w/z.jsonl"}, sidechain=True)   # trap: a sub-agent's read
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                  # trap: headless
    h.call("Read", {"file_path": "/home/dana/.claude/projects/-w/z.jsonl"})
    return {"value": 4}


@check(WATCH, "L3", 2)
def watch_l3_2(lt):
    """Telemetry switched on in the person's own settings env (user, local, or a project file git says is theirs; never an employer's
    managed settings or a teammate's file), by exact name: CLAUDE_CODE_ENABLE_TELEMETRY set on, and OpenTelemetry exporter keys
    (OTEL_*EXPORTER*) set to something other than none. DISABLE_* keys are off switches and don't count."""
    on = set()
    for scope, path, d in _mine_settings(lt):
        env = d.get("env") if isinstance(d.get("env"), dict) else {}
        for k, v in env.items():
            v = str(v).strip().lower()
            if k == "CLAUDE_CODE_ENABLE_TELEMETRY" and v not in ("", "0", "false", "no"):
                on.add(k)
            elif k.startswith("OTEL_") and "EXPORTER" in k and v not in ("", "none"):
                on.add(k)
    return {"value": len(on), "evidence": sorted(on)[:3], "note": "variables exported in a shell profile are not read"}


@fixture(WATCH, "L3", 2)
def _(fx):
    fx.settings("user", {"env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_METRICS_EXPORTER": "otlp", "DISABLE_TELEMETRY": "1",
                                 "DISABLE_ERROR_REPORTING": "1", "OTEL_LOGS_EXPORTER": "none"}})
    fx.repo("~/work/shop", {"README.md": "x"})
    fx.commit("~/work/shop", {".claude/settings.json": json.dumps({"env": {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4317", "DISABLE_TELEMETRY": "1"}})}, by="work")
    fx.settings("managed", {"env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_TRACES_EXPORTER": "otlp"}})        # trap: the employer's
    fx.repo("~/work/team", {"README.md": "x"}, by="teammate")
    fx.commit("~/work/team", {".claude/settings.json": json.dumps({"env": {"OTEL_LOGS_EXPORTER": "otlp"}})}, by="teammate")   # trap: a teammate's
    fx.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
    fx.session("~/work/team", entrypoint="claude-vscode").typed("hi")
    return {"value": 3}


@check(WATCH, "L4", 1)
def watch_l4_1(lt):
    """Cost per run is recorded. Claude Code writes a cost-state record in nearly every session for everyone, and a headless run
    carries its own budget_usd whoever started it (plugins and hooks run capped `claude -p` too): neither is the person's act, so the
    records say the cost exists, not that the person recorded or kept it."""
    return {"value": None, "evidence": [], "note": "can't tell from a laptop: cost-state is written automatically for everyone, and a capped headless run is as likely a plugin's as the person's"}


@fixture(WATCH, "L4", 1)
def _(fx):
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    v.typed("go"); v.record("cost-state", totalCostUSD=0.5, totalDuration=100); v.record("cost-state", totalCostUSD=1.5, totalDuration=300)   # written for everyone
    h = fx.session("~/work/shop", entrypoint="sdk-cli")
    h.typed("go"); h.record("cost-state", totalCostUSD=0.2, totalDuration=50)
    return {"value": None}


# ====================================================================================== retest-agent-setup
RETEST = "retest-agent-setup"
_SETUP_FILE = re.compile(r"(^|/)(CLAUDE|AGENTS)(\.local)?\.md$|/SKILL\.md$|(^|/)\.claude/(agents|commands|skills|rules)/.+\.md$", re.I)
_EVAL_RUN = re.compile(r"\bclaude\s+plugin\s+eval\b")
_CASE = re.compile(r"(^|/)evals/(.+/)?case\.ya?ml$")


@check(RETEST, "L2", 0)
def retest_l2_0(lt):
    """Edit/Write calls (interactive sessions, sub-agents included) on instruction files (CLAUDE.md, AGENTS.md, CLAUDE.local.md),
    SKILL.md, or a .claude/agents|commands|skills|rules file; either path separator."""
    hits = [c for c in lt.calls if c["name"] in EDIT_TOOLS and lt.interactive(c["s"]) and _SETUP_FILE.search(_norm(_inp(c).get("file_path")))]
    return {"value": len(hits), "evidence": _ev([_norm(_inp(c).get("file_path")) for c in hits]),
            "note": "an agent file kept in a plugin folder outside .claude/ is only seen when it is named SKILL.md"}


@fixture(RETEST, "L2", 0)
def _(fx):
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    v.call("Edit", {"file_path": fx.p("~/work/shop/CLAUDE.md")})
    v.call("Write", {"file_path": "C:\\proj\\.claude\\agents\\reviewer.md"})
    v.call("Edit", {"file_path": fx.p("~/work/shop/skills/x/SKILL.md")}, sidechain=True)
    v.call("Edit", {"file_path": fx.p("~/work/shop/README.md")})                          # trap
    v.call("Edit", {"file_path": fx.p("~/work/shop/docs/agents/overview.md")})            # trap: a folder named agents
    h = fx.session("~/work/shop", entrypoint="sdk-py")
    h.call("Edit", {"file_path": fx.p("~/work/shop/CLAUDE.md")})                          # trap: headless
    return {"value": 3}


def _case_files(lt):
    """[(repo, rel)] eval case files in Claude Code's layout that are the person's (_owned: committed or not; a cloned plugin repo's
    evals are a teammate's)."""
    memo = lt.__dict__.setdefault("_g3_cases", [])      # one answer: three checks read it
    if memo:
        return memo[0]
    rx = re.compile(_CASE.pattern, re.M)
    out = []
    for r in lt.repos:
        rels = _repo_files(lt, r)
        text = "\n".join(rels)     # every file name of the repo searched in one pass (a million names across the repos), not one search per name
        i = last = 0
        for m in rx.finditer(text):
            i += text.count("\n", last, m.start()); last = m.start()     # the name's index, counted from the previous match
            if _owned(lt, os.path.join(r, rels[i])):
                out.append((r, rels[i]))
    memo.append(out)
    return out


_CRITERION_KEY = re.compile(r"^(graders?|expected\w*|expects?|expectations?|asserts?|assertions?|rubrics?|judges?|criteria|pass_criteri\w+|pass_if|success\w*)\s*:\s*(.*)$", re.I)
_NO_VALUE = ("", "[]", "{}", "null", "~", '""', "''", "|", ">", "|-", ">-")
MIN_CASES = 3   # an eval set is a set: three cases is the least that tests more than one thing


def _states_pass_criterion(r, rel):
    """The case states what a pass looks like: Claude Code's layout keeps it in graders/*.md next to case.yaml (one file per grader, with
    its pattern, rubric or tool check), or the case.yaml itself declares a non-empty graders / expected / assert / rubric / judge /
    criteria / pass_if / success key. A folder with no grader file or an empty one only holds a prompt."""
    d = os.path.join(r, os.path.dirname(rel))
    gd = os.path.join(d, "graders")
    try:
        if os.path.isdir(gd) and any(os.path.isfile(os.path.join(gd, f)) and _read(os.path.join(gd, f)).strip() for f in os.listdir(gd)):
            return True
    except OSError:
        pass
    lines = _read(os.path.join(r, rel)).splitlines()
    for i, line in enumerate(lines):
        m = _CRITERION_KEY.match(line)
        if not m:
            continue
        if m.group(2).split(" #")[0].strip() not in _NO_VALUE:
            return True
        for nxt in lines[i + 1:]:   # a block value: the next line that holds anything is indented or a list item
            if nxt.strip() and not nxt.lstrip().startswith("#"):
                if nxt[:1] in (" ", "\t") or nxt.startswith("- "):
                    return True
                break
    return False


def _eval_sets(lt):
    """{(repo, evals folder): [graded case file, ...]} over the person's own cases (_case_files), cases that state a pass criterion only."""
    sets, tracked = {}, {}
    for r, rel in _case_files(lt):
        if r not in tracked:
            tracked[r] = set(_tracked(lt, r))
        if rel not in tracked[r]:      # L3 asks for a set anyone on the team can run: an uncommitted case is the person's draft
            continue
        m = re.search(r"(^|/)evals/", rel)
        sets.setdefault((r, rel[:m.end()]), [])
        if _states_pass_criterion(r, rel):
            sets[(r, rel[:m.end()])].append(rel)
    return sets


@check(RETEST, "L3", 0)
def retest_l3_0(lt):
    """Own eval sets: an evals folder (Claude Code's `claude plugin eval` layout, evals/<case>/case.yaml) in a repo the sessions ran in,
    whose cases are the person's by the one ownership rule (committed or not; not a clone of someone else's plugin), with at least
    three cases that each state what a pass looks like (a non-empty graders/ file beside case.yaml, or an expected / assert / rubric /
    judge field in it). One case.yaml, or cases that only hold a prompt, do not make a set."""
    sets = _eval_sets(lt)
    good = {k: v for k, v in sets.items() if len(v) >= MIN_CASES}
    return {"value": len(good), "evidence": [f"{os.path.basename(r)}/{p}: {len(v)} graded cases" for (r, p), v in sorted(good.items())[:3]],
            "note": f"{len(sets)} eval folders of theirs, {len(good)} with {MIN_CASES}+ cases that state a pass; only Claude Code's own layout is read, "
                    "cases kept in another eval tool's format are not seen; whether the cases came from real misses is not read"}


@fixture(RETEST, "L3", 0)
def _(fx):
    grader = "---\ntype: regex\n---\nlooks ok\n"
    fx.repo("~/work/shop", {"evals/login/case.yaml": "name: login\n", "evals/login/prompt.md": "x\n", "evals/login/graders/passes.md": grader,
                            "evals/search/case.yaml": "name: search\nexpected:\n  - lists three results\n",              # criterion inside case.yaml
                            "evals/checkout/case.yaml": "name: checkout\n", "evals/checkout/graders/a.md": grader,
                            "evals/draft/case.yaml": "name: draft\nprompt: only a prompt\n",                              # trap: no pass criterion
                            "evals/blank/case.yaml": "name: blank\nexpected:\n", "evals/blank/graders/empty.md": "\n",    # trap: empty criterion
                            "evals/notes.md": "x\n", "eval/prompt.md": "x\n", "tasks/case.yaml": "x: 1\n"})            # traps
    fx.file("~/work/shop/evals/scratch/case.yaml", "rubric: it works\n")                       # trap: untracked, a draft no teammate can run
    fx.repo("~/work/many", {"plugins/y/evals/a/case.yaml": "name: a\n", "plugins/y/evals/a/graders/g.md": grader,                   # a second set, in a plugin folder
                            "plugins/y/evals/b/case.yaml": "name: b\njudge: a model reads it\n",
                            "plugins/y/evals/c/case.yaml": "name: c\n", "plugins/y/evals/c/graders/g.md": grader,
                            "plugins/x/evals/a/case.yaml": "name: a\n", "plugins/x/evals/a/graders/g.md": grader,         # trap: only two graded cases
                            "plugins/x/evals/b/case.yaml": "name: b\nassert: ok\n", "plugins/x/evals/c/case.yaml": "name: c\n"})
    fx.repo("~/work/promptonly", {f"evals/{n}/case.yaml": f"name: {n}\nprompt: x\n" for n in "abc"})                              # trap: three cases, no pass criterion
    fx.repo("~/code/someone-elses-plugin", {f"evals/{n}/case.yaml": f"name: {n}\nexpected: y\n" for n in "abc"}, by="teammate")   # trap: a clone
    for r in ("shop", "many", "promptonly"):
        fx.session(f"~/work/{r}", entrypoint="claude-desktop").typed("hi")
    fx.session("~/code/someone-elses-plugin", entrypoint="claude-vscode").typed("hi")
    return {"value": 2}      # shop evals/ (login, search, checkout, scratch) and many plugins/y/evals/


@check(RETEST, "L3", 2)
def retest_l3_2(lt):
    """In repos that hold the person's eval cases: distinct commands a file of theirs (package.json script, Makefile or justfile
    recipe line; _owned; comment lines left out) declares that run `claude plugin eval`: a command anyone
    on the team can run. A line that only mentions an eval folder (an exclude flag, a cleanup) is not one."""
    cases = _case_files(lt)
    found = set()
    for r in {r for r, _ in cases}:
        for f, text in _declared_files(r):
            if _EVAL_RUN.search(text) and _owned(lt, f):
                found.add((r, text.strip()))
    return {"value": len(found), "evidence": _ev(sorted(found), f=lambda x: x[1]),
            "note": "a command written only in a README, or run only by CI, is not counted"}


@fixture(RETEST, "L3", 2)
def _(fx):
    fx.repo("~/work/shop", {"evals/a/case.yaml": "prompt: x\n", "README.md": "run: claude plugin eval evals/a\n",
                            "package.json": json.dumps({"scripts": {"eval": "claude plugin eval evals", "lint": "eslint ."}}),
                            "Makefile": "evals:\n\tclaude plugin eval evals/a --json\nclean:\n\trm -rf evals/out\npack:\n\ttar --exclude evals -cf x.tar .\n"
                                        "# \tclaude plugin eval commented\ntest:\n\tnpm test\n\t# claude plugin eval evals/old\n"})
    fx.commit("~/work/shop", {"justfile": "ev:\n    claude plugin eval evals/a --team\n"}, by="teammate")        # trap: a teammate's recipe
    fx.commit("~/work/shop", {".github/workflows/e.yml": "on: push\njobs:\n  j:\n    steps:\n      - run: claude plugin eval evals/ci\n"}, by="work")   # trap: CI's, not a command to run
    fx.repo("~/work/nocases", {"package.json": json.dumps({"scripts": {"eval": "claude plugin eval evals"}})})   # trap: no cases here
    fx.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
    fx.session("~/work/nocases", entrypoint="claude-vscode").typed("hi")
    return {"value": 2}


def _runs_eval_blocking(text):
    """A line (not a comment) runs `claude plugin eval` and does not swallow its exit status."""
    return any(_EVAL_RUN.search(l) and not l.strip().startswith("#") and not re.search(r"\|\|\s*(true|:|exit 0)\b", l) for l in text.splitlines())


@check(RETEST, "L4", 1)
def retest_l4_1(lt):
    """In repos that hold the person's eval cases: CI files of theirs that start on every change (a push or a pull request, not a
    tag, a schedule or a click) with a step that runs `claude plugin eval` and does not swallow its exit status (`|| true`,
    `continue-on-error`), and git hooks of theirs (_owned) that do. Whether a score drop makes the command fail is not read."""
    cases = _case_files(lt)
    n, ev = 0, []
    for r in sorted({r for r, _ in cases}):
        for f, steps in _ci_every_change(lt, r):
            if re.search(r"continue-on-error:\s*true|allow_failure:\s*true", _read(f)):
                continue
            if any(_runs_eval_blocking(t) for t in steps):
                n += 1
                ev.append(os.path.relpath(f, r))
        for f in _git_hook_files(lt, r):
            if _owned(lt, f) and _runs_eval_blocking(_read(f)):
                n += 1
                ev.append(os.path.relpath(f, r))
    return {"value": n, "evidence": ev[:3], "note": "CI runs themselves are not on the laptop; whether a drop in score fails the command is not read"}


@fixture(RETEST, "L4", 1)
def _(fx):
    wf = lambda cmd, on="on: push": f"name: x\n{on}\njobs:\n  j:\n    steps:\n      - run: {cmd}\n"
    fx.repo("~/work/shop", {"evals/a/case.yaml": "prompt: x\n", ".github/workflows/evals.yml": wf("claude plugin eval evals/"),
                            ".github/workflows/soft.yml": wf("claude plugin eval evals/ || true"),     # trap: never fails
                            ".github/workflows/node.yml": wf("npm test"),
                            ".github/workflows/nightly.yml": wf("claude plugin eval evals/", "on:\n  schedule:\n    - cron: '0 3 * * *'"),   # trap: not every change
                            ".github/workflows/release.yml": wf("claude plugin eval evals/", "on:\n  push:\n    tags: ['v*']"),            # trap: a release only
                            ".github/workflows/manual.yml": wf("claude plugin eval evals/", "on: workflow_dispatch"),                         # trap: a click
                            ".github/workflows/off.yml": "on: push\njobs:\n  j:\n    steps:\n      # - run: claude plugin eval evals/\n      - run: npm test\n",   # trap: commented out
                            ".github/workflows/lenient.yml": "on: push\njobs:\n  j:\n    continue-on-error: true\n    steps:\n      - run: claude plugin eval evals/\n",   # trap: failure ignored
                            ".husky/pre-push": "#!/bin/sh\nclaude plugin eval evals\n"})
    fx._git(fx.p("~/work/shop"), "config", "core.hooksPath", ".husky")                                  # counts: tracked, theirs, git runs it
    fx.commit("~/work/shop", {".github/workflows/team.yml": wf("claude plugin eval evals/")}, by="teammate")   # trap: a teammate's
    fx.repo("~/work/other", {"evals/a/case.yaml": "prompt: x\n"})
    fx.file("~/work/other/.git/hooks/pre-push", "#!/bin/sh\nclaude plugin eval evals\n")                # counts: untracked but no install receipt = theirs (ruling 11-05)
    h = fx.session("~/work/eval-runs", entrypoint="sdk-cli")             # trap: a headless run in a folder named eval
    h.typed("go")
    fx.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
    fx.session("~/work/other", entrypoint="claude-vscode").typed("hi")
    return {"value": 3}      # evals.yml, shop's husky pre-push, other's .git/hooks/pre-push


# ====================================================================================== share-the-setup-with-the-team
SHARE = "share-the-setup-with-the-team"
_SHARED = re.compile(r"(^|/)(CLAUDE\.md|AGENTS\.md|\.mcp\.json)$|(^|/)\.claude/settings\.json$")
_PERSONAL = re.compile(r"(^|/)CLAUDE\.local\.md$|(^|/)\.claude/settings\.local\.json$")
_SHARED_HINTS = ("CLAUDE.md", "AGENTS.md", ".mcp.json", "settings.json")       # what every name _SHARED matches holds
_PERSONAL_HINTS = ("CLAUDE.local.md", "settings.local.json")


def _shared_files(lt, repo):
    """The repo's tracked shared setup files (tracked is the claim: shared means committed) that are the person's by the one
    ownership rule (_owned): a clone's files and a teammate's are not."""
    return [t for t in _names_matching(_SHARED, _tracked(lt, repo), _SHARED_HINTS) if _owned(lt, os.path.join(repo, t))]


def _names_matching(rx, names, hints):
    """The names `rx` matches, in order. `hints` are strings every match must hold (a name that holds none cannot match): they are found
    in the names joined by newlines with plain substring searches (a repo lists tens of thousands of files, the repos a million), and rx
    is searched only on the names that hold one. Names hold no newline (git quotes it)."""
    text = "\n".join(names)
    at = set()
    for h in hints:
        i = text.find(h)
        while i >= 0:
            at.add(i)
            i = text.find(h, i + 1)
    out, line, last = [], 0, 0
    for pos in sorted(at):
        line += text.count("\n", last, pos); last = pos     # the name's index, counted from the previous hit
        if not out or out[-1][0] != line:
            out.append((line, names[line]))
    return [n for _, n in out if rx.search(n)]


@check(SHARE, "L3", 0)
def share_l3_0(lt):
    """Shared files git tracks (tracked is the claim: committed to be shared) in the repos the sessions ran in that are the person's by
    the one ownership rule (CLAUDE.md and AGENTS.md at any depth, .mcp.json, .claude/settings.json; not most commits a teammate's), in repos where no personal file (CLAUDE.local.md, settings.local.json)
    is tracked. A repo cloned from someone else, with its instruction files, is not the person's sharing."""
    n, ev, leaky = 0, [], 0
    for r in lt.repos:
        tracked = _tracked(lt, r)
        if _names_matching(_PERSONAL, tracked, _PERSONAL_HINTS):
            leaky += 1
            continue
        shared = _shared_files(lt, r)
        n += len(shared)
        ev += [f"{os.path.basename(r)}/{t}" for t in shared]
    return {"value": n, "evidence": ev[:3], "note": f"{leaky} repos track a personal file and are left out; a repo the sessions never ran in is not read; files by others not counted"}


@fixture(SHARE, "L3", 0)
def _(fx):
    fx.repo("~/work/shop", {"CLAUDE.md": "rules\n", "app/CLAUDE.md": "app rules\n", ".mcp.json": "{}", ".claude/settings.json": "{}",
                            ".gitignore": "CLAUDE.local.md\n.claude/settings.local.json\n"})
    fx.file("~/work/shop/CLAUDE.local.md", "mine\n")                                         # present, ignored: personal and apart
    fx.repo("~/code/leaky", {"CLAUDE.md": "rules\n", "CLAUDE.local.md": "mine\n"})           # trap: a personal file is committed
    fx.repo("~/code/other", {"CLAUDE.md": "rules\n"})                                         # trap: no session ran there
    fx.repo("~/code/cloned", {"CLAUDE.md": "oss rules\n", "AGENTS.md": "oss\n", ".mcp.json": "{}"}, by="teammate")   # trap: a clone, none of it theirs
    fx.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
    fx.session("~/code/leaky", entrypoint="claude-desktop").typed("hi")
    fx.session("~/code/cloned", entrypoint="claude-vscode").typed("hi")
    return {"value": 4}


@check(SHARE, "L3", 1)
def share_l3_1(lt):
    """Distinct people other than the person (by email) with a commit on a shared setup file of the person's (CLAUDE.md, AGENTS.md,
    .mcp.json, .claude/settings.json; _owned) in the repos the sessions ran in. Every email the person's
    git config knows is theirs. A teammate's edits to someone else's file, or a maintainer's to a clone, don't show the person shared it."""
    others = {}
    for r in lt.repos:
        for rel in _shared_files(lt, r):
            for line in lt._authors(r).get(rel, []):   # one git log per repo, not one per file
                if line.strip() and not lt.is_mine(line):
                    others[line.rpartition("<")[2].rstrip(">").lower()] = line
    return {"value": len(others), "evidence": sorted(others.values())[:3], "note": "a bot or a second address of the same colleague counts as another person"}


@fixture(SHARE, "L3", 1)
def _(fx):
    fx.repo("~/work/shop", {"CLAUDE.md": "v1\n", "src/x.ts": "1\n"}, by="work")
    fx.commit("~/work/shop", {"CLAUDE.md": "v2\n"}, by="teammate")
    fx.commit("~/work/shop", {"AGENTS.md": "a\n"}, by="teammate")                            # the colleague's own file: not the person's, not counted
    fx.commit("~/work/shop", {"app/CLAUDE.md": "b\n"}, by="work")
    fx.commit("~/work/shop", {"app/CLAUDE.md": "b2\n"}, by="sam@work.example")
    fx.commit("~/work/shop", {".mcp.json": "{}"}, by="home")                                  # the person's other address
    fx.commit("~/work/shop", {"src/x.ts": "2\n"}, by="kim@work.example")                      # trap: edited code, not the setup
    fx.repo("~/work/oss", {"CLAUDE.md": "m1\n"}, by="maint@oss.example")                      # trap: maintainers edit a clone's file
    fx.commit("~/work/oss", {"CLAUDE.md": "m2\n"}, by="other@oss.example")
    fx.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
    fx.session("~/work/oss", entrypoint="claude-vscode").typed("hi")
    return {"value": 2}


@check(SHARE, "L4", 1)
def share_l4_1(lt):
    """CI files of the person's (_owned, the one ownership rule) in the repos the sessions ran in that check out the repo and run a step
    that loads the setup from that fresh checkout: `claude mcp list|get` (reads .mcp.json) or `claude doctor` (reads settings and
    instruction files). A step that only names a setup file, runs `claude -p`, or validates a plugin does not show the clone loads."""
    pat = re.compile(r"\bclaude\s+(mcp\s+(list|get)\b|doctor\b)")
    found = []
    for r in lt.repos:
        by_file = {}
        for f, text in _ci_steps(r):
            by_file.setdefault(f, []).append(text)
        for f, steps in sorted(by_file.items()):
            body = _read(f)
            if _owned(lt, f) and re.search(r"actions/checkout|git clone", body) and any(pat.search(t) for t in steps):
                found.append(f"{os.path.basename(r)}/{os.path.relpath(f, r)}")
    return {"value": len(found), "evidence": found[:3], "note": "CI runs are not on the laptop; a step is read, not run"}


@fixture(SHARE, "L4", 1)
def _(fx):
    wf = lambda cmd, co=True: f"name: x\non: push\njobs:\n  j:\n    steps:\n" + ("      - uses: actions/checkout@v4\n" if co else "") + f"      - run: {cmd}\n"
    fx.repo("~/work/shop", {".github/workflows/a.yml": wf("claude mcp list"), ".github/workflows/e.yml": wf("claude doctor"),
                            ".github/workflows/b.yml": wf("jq . .mcp.json"), ".github/workflows/c.yml": wf("npm test"),
                            ".github/workflows/d.yml": wf("echo claude is great"), ".github/workflows/p.yml": wf("claude plugin validate ."),
                            ".github/workflows/q.yml": wf("claude -p 'review the diff' --output-format json"),   # trap: a review bot, not a load check
                            ".github/workflows/r.yml": wf("test -d .claude/"),                                   # trap: names a folder
                            ".github/workflows/n.yml": wf("claude mcp list", co=False)})                          # trap: no fresh checkout
    fx.commit("~/work/shop", {".github/workflows/t.yml": wf("claude doctor")}, by="teammate")                     # trap: a teammate's
    fx.repo("~/code/other", {".github/workflows/a.yml": wf("claude mcp list")})              # trap: no session there
    fx.repo("~/code/vendored-tool", {".github/workflows/ci.yml": wf("claude mcp list") + "      - run: make\n"}, by="teammate")   # trap: a clone with its CI (not the same bytes as a.yml: ownership rule 4 would make that one a copy of it)
    fx.session("~/work/shop", entrypoint="claude-vscode").typed("hi")
    fx.session("~/code/vendored-tool", entrypoint="claude-vscode").typed("hi")
    return {"value": 2}


# ====================================================================================== step-in-on-signals
STEP = "step-in-on-signals"


def _at(s, t):
    s.t = t - 7   # the next record is written 7 s later, exactly at t


AWAY_SECONDS = 300   # the agent ran at least this long without a message from the person in its session (and they typed elsewhere)
BACK_SECONDS = 300   # and they typed in it again within this long after it finished or asked


def _finishes(lt):
    """{session: sorted times} the main agent ended a turn (Claude Code writes stop_reason end_turn on a turn's last message: it finished,
    or it ended on a question in words). A turn that ended on a tool call, an Esc or an error has none."""
    got = lt.__dict__.get("_g3_finish")
    if got is None:
        got = {}
        for r in lt.grep(rb'"stop_reason":\s*"end_turn"'):
            m = r.get("message")
            if r.get("type") != "assistant" or r["_sc"] or not isinstance(m, dict) or m.get("stop_reason") != "end_turn":
                continue
            t = epoch(r.get("timestamp")) if r.get("timestamp") else None
            if t:
                got.setdefault(r["_sid"], []).append(t)
        for v in got.values():
            v.sort()
        lt._g3_finish = got
    return got


@check(STEP, "L2", 1)
def step_l2_1(lt):
    """Times the person went back when the agent finished or asked: a turn in an interactive session that ran 5+ minutes without a message
    from them in it, while they typed in another session (so they were not watching it), and that ended on its own (end_turn: done, or
    a question in words; not an Esc), after which they typed in it again within 5 minutes. Typing in another session alone is parallel
    work and is not counted; the return is. Someone who left the keyboard and came back is not seen (nothing in the logs says
    they were away); a question asked in a dialog (AskUserQuestion) is answered without a typed message and is not seen."""
    import bisect
    ends = _finishes(lt)
    inter = [m for m in lt.typed if lt.interactive(m["s"])]
    if len(inter) >= 20 and not any(lt.interactive(s) for s in ends):
        return {"value": None, "evidence": [], "note": f"can't tell: {len(inter)} typed messages but no end_turn record in the interactive sessions "
                                                       "(an older Claude Code, or a route that doesn't write it)"}
    times = [m["t"] for m in inter]
    stops = {}
    for o in lt.others:
        if o["type"] == "interrupt":
            stops.setdefault(o["s"], []).append(o["t"])
    long_runs = back = 0
    shown = []
    for sid, ms in _by_session(inter).items():
        fin = ends.get(sid) or []
        for i, m in enumerate(ms):
            nxt = ms[i + 1]["t"] if i + 1 < len(ms) else float("inf")
            lo = bisect.bisect_left(fin, m["t"])
            hi = bisect.bisect_left(fin, nxt)
            if hi <= lo:
                continue
            end = fin[hi - 1]
            if end - m["t"] < AWAY_SECONDS or any(m["t"] < t < nxt for t in stops.get(sid, [])):
                continue
            a, b = bisect.bisect_right(times, m["t"]), bisect.bisect_left(times, end)
            if not any(inter[j]["s"] != sid for j in range(a, b)):
                continue
            long_runs += 1
            if nxt - end <= BACK_SECONDS:
                back += 1
                if len(shown) < 3:
                    shown.append(f"{sid[:8]}: ran {int((end - m['t']) // 60)} min, typed again {int(nxt - end)} s after it finished")
    return {"value": back, "evidence": shown or [f"{long_runs} runs of {AWAY_SECONDS // 60}+ min while typing elsewhere; none answered within {BACK_SECONDS // 60} min"],
            "note": f"{back} of {long_runs} runs of {AWAY_SECONDS // 60}+ min (typed in another session meanwhile, ended on their own) were answered "
                    f"within {BACK_SECONDS // 60} min of ending; leaving the keyboard is not visible, so someone who sat elsewhere is under-counted"}


@fixture(STEP, "L2", 1)
def _(fx):
    import time as _t
    T0 = _t.time() - 2 * 86400
    def end(s, mid):   # the agent's last message of a turn: it finished, or asked in words
        _a(s, mid, stop="end_turn")
    a = fx.session("~/work/shop", entrypoint="claude-vscode")                      # counts: long run, typed elsewhere, back within 2 min
    _at(a, T0); a.typed("long task")
    _at(a, T0 + 30); _a(a, "a1", stop="tool_use"); _at(a, T0 + 600); end(a, "a2")
    b = fx.session("~/work/other", entrypoint="claude-desktop")
    _at(b, T0 + 100); b.typed("meanwhile, another thing")                          # the other work
    _at(b, T0 + 110); end(b, "b1")                                                 # B's own turn is 10 s: not a long run
    _at(a, T0 + 720); a.typed("next")                                              # back 120 s after A ended
    _at(a, T0 + 730); end(a, "a3")
    # trap (what the old rule counted): same shape, but they came back an hour later
    c = fx.session("~/work/shop", entrypoint="claude-vscode")
    _at(c, T0 + 20000); c.typed("long task two")
    _at(c, T0 + 20600); end(c, "c1")
    d = fx.session("~/work/other", entrypoint="claude-desktop")
    _at(d, T0 + 20100); d.typed("elsewhere again")
    _at(c, T0 + 24200); c.typed("back much later")
    # trap: sat in it (nobody typed elsewhere), answered a minute after it finished
    e = fx.session("~/work/shop", entrypoint="claude-vscode")
    _at(e, T0 + 40000); e.typed("watch this")
    _at(e, T0 + 40700); end(e, "e1"); _at(e, T0 + 40760); e.typed("ok")
    # trap: Esc, not a finish; typed elsewhere meanwhile, came back at once
    f = fx.session("~/work/shop", entrypoint="claude-vscode")
    _at(f, T0 + 60000); f.typed("start")
    _at(f, T0 + 60500); f.interrupt()
    g = fx.session("~/work/other", entrypoint="claude-desktop")
    _at(g, T0 + 60100); g.typed("other work")
    _at(f, T0 + 60530); f.typed("never mind")
    # trap: a 2 minute run answered at once
    h = fx.session("~/work/shop", entrypoint="claude-vscode")
    _at(h, T0 + 80000); h.typed("quick one")
    i = fx.session("~/work/other", entrypoint="claude-desktop")
    _at(i, T0 + 80050); i.typed("meanwhile")
    _at(h, T0 + 80120); end(h, "h1"); _at(h, T0 + 80150); h.typed("thanks")
    # trap: only a headless run "typed" meanwhile
    j = fx.session("~/work/shop", entrypoint="claude-vscode")
    _at(j, T0 + 100000); j.typed("long task three")
    k = fx.session("~/work/shop", entrypoint="sdk-cli")
    _at(k, T0 + 100100); k.typed("go")
    _at(j, T0 + 100600); end(j, "j1"); _at(j, T0 + 100660); j.typed("ok")
    return {"value": 1}


@check(STEP, "L2", 3)
def step_l2_3(lt):
    """Stops by hand in interactive sessions: Esc interrupts (Claude Code's '[Request interrupted' record) plus sub-agents the
    meta file marks stoppedByUser."""
    ints = [o for o in lt.others if o["type"] == "interrupt" and lt.interactive(o["s"])]
    subs = [m for m in lt.meta if m.get("stoppedByUser") and lt.interactive(m["_session"])]
    return {"value": len(ints) + len(subs), "evidence": [f"{len(ints)} interrupts", f"{len(subs)} sub-agents stopped by hand"],
            "note": "records that the person stopped it, not that it had drifted"}


@fixture(STEP, "L2", 3)
def _(fx):
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    v.typed("go"); v.interrupt(); v.interrupt(for_tool=True)
    v.subagent("Explore", "look", stopped_by_user=True)
    v.subagent("Explore", "look again")                                   # trap: finished by itself
    d = fx.session("~/work/shop", entrypoint="claude-desktop")
    d.typed("quiet one")
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                   # trap: headless
    h.interrupt(); h.subagent("Explore", "x", stopped_by_user=True)
    return {"value": 3}


@check(STEP, "L3", 1)
def step_l3_1(lt):
    """Typed messages (interactive) sent in a mode the person moved to where routine steps pass while others can still ask:
    acceptEdits or auto. `default` is Claude Code's out-of-the-box mode (and Claude Code writes allow rules itself each time the
    person clicks 'don't ask again'), so a default-mode message is never counted, whatever the allow rules; bypassPermissions and
    dontAsk ask nothing."""
    n, total = 0, 0
    for m in lt.typed:
        if not m.get("pm") or not lt.interactive(m["s"]):
            continue
        total += 1
        n += m["pm"] in ("acceptEdits", "auto")
    return {"value": n, "evidence": [f"{n} of {total} typed messages"],
            "note": "which steps would have asked is not recorded; allow rules are not read (Claude Code writes them too)"}


@fixture(STEP, "L3", 1)
def _(fx):
    fx.settings("user", {"permissions": {"allow": ["Bash(npm test:*)"]}})     # an allow rule: Claude Code writes these on "don't ask again"
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    v.typed("a", permission_mode="acceptEdits"); v.typed("b", permission_mode="acceptEdits")
    v.typed("c", permission_mode="default"); v.typed("d", permission_mode="default")   # trap: the out-of-the-box mode, with an allow rule present
    v.typed("e", permission_mode="bypassPermissions"); v.typed("f", permission_mode="plan")
    d = fx.session("~/work/shop", entrypoint="claude-desktop")
    d.typed("g", permission_mode="auto")
    for _ in range(3):
        d.typed("bypass", permission_mode="dontAsk")
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                   # trap: headless
    for _ in range(4):
        h.typed("go", permission_mode="acceptEdits")
    return {"value": 3}


# ====================================================================================== lead-a-team-of-agents
LEAD = "lead-a-team-of-agents"


@check(LEAD, "L2", 0)
def lead_l2_0(lt):
    """Occasions the person split the work: interactive sessions in which they started 2+ sub-agents themselves (the sub-agent's meta
    file carries no toolUseId, which Claude Code writes only when a model's Agent call started it: a fork, a /subtask or an
    @-mention). Sub-agents the model launched are not counted (plan mode tells it to fan out Explore agents), nor teammates of an
    agent team, which the lead model spawns."""
    mine = {}
    for m in lt.meta:
        if not m.get("toolUseId") and not m.get("teamName") and m.get("taskKind") != "in_process_teammate" and lt.interactive(m["_session"]):
            mine[m["_session"]] = mine.get(m["_session"], 0) + 1
    big = {s: n for s, n in mine.items() if n >= 2}
    return {"value": len(big), "evidence": [f"{sum(big.values())} sub-agents started by the person in {len(big)} sessions"],
            "note": "a split the person asked for in words and the model carried out is not seen: the log doesn't say whose idea it was"}


@fixture(LEAD, "L2", 0)
def _(fx):
    v = fx.session("~/work/shop", entrypoint="claude-vscode")
    v.subagent("fork", "try the first option", by_claude=False, is_fork=True)       # started by the person
    v.subagent("fork", "try the second option", by_claude=False, is_fork=True)
    _a(v, "b1", agents=("a",)); _a(v, "b1", agents=("b",))               # trap: the model fans out in one message
    _a(v, "b2", agents=("c",))
    v.call("Agent", {"subagent_type": "Explore", "prompt": "x"})
    v.subagent("Explore", "e1"); v.subagent("Explore", "e2"); v.subagent("Explore", "e3")   # trap: the model's own launches (plan mode)
    w = fx.session("~/work/shop", entrypoint="claude-vscode")
    w.subagent("fork", "only one", by_claude=False, is_fork=True)                  # one: not a split
    t = fx.session("~/work/shop", entrypoint="claude-desktop")
    t.subagent("general-purpose", "w1", by_claude=False, team="t1"); t.subagent("general-purpose", "w2", by_claude=False, team="t1")   # trap: an agent team's members
    h = fx.session("~/work/shop", entrypoint="sdk-cli")                   # trap: headless fan-out
    h.typed("go")
    h.subagent("fork", "h1", by_claude=False, is_fork=True); h.subagent("fork", "h2", by_claude=False, is_fork=True)
    return {"value": 1}


def _agent_files(lt):
    """[(type name, path)] agent definition files: the user's folder and each repo's .claude/agents."""
    out = []
    paths = glob.glob(os.path.join(lt.claude, "agents", "*.md"))
    for r in lt.repos:
        paths += glob.glob(os.path.join(r, ".claude", "agents", "*.md"))
        paths += [os.path.join(r, t) for t in _tracked(lt, r) if re.search(r"(^|/)\.claude/agents/[^/]+\.md$", t)]
    for p in sorted(set(paths)):
        m = re.search(r"^name:\s*['\"]?([\w:.-]+)", _read(p)[:2000], re.M)
        out.append((m.group(1) if m else os.path.basename(p)[:-3], p))
    return out


def _own_commands(lt):
    """Names of the slash commands and skills the person wrote: .claude/commands/**/*.md (a subfolder is a `:`) and .claude/skills/*/SKILL.md
    in their user folder, their repos and their session folders, whose file is theirs by the one ownership rule (_owned)."""
    got = lt.__dict__.get("_g3_owncmds")
    if got is None:
        got = set()
        roots = list(dict.fromkeys([lt.claude] + [os.path.join(d, ".claude") for d in list(lt.repos) + lt.session_folders()]))
        for base in roots:
            for p in glob.glob(os.path.join(base, "commands", "**", "*.md"), recursive=True):
                if _owned(lt, p):
                    got.add(os.path.relpath(p, os.path.join(base, "commands"))[:-3].replace(os.sep, ":"))
            for p in glob.glob(os.path.join(base, "skills", "*", "SKILL.md")):
                if _owned(lt, p):
                    got.add(os.path.basename(os.path.dirname(p)))
                    m = re.search(r"^name:\s*['\"]?([\w:.-]+)", _read(p)[:1500], re.M)
                    if m:
                        got.add(m.group(1))
        lt._g3_owncmds = got
    return got


def _team_sessions(lt):
    """{session: {own role: how it was started}} for launches of the person's own agent roles that they steered, in interactive sessions:
    'by hand' (a sub-agent whose meta file has no toolUseId: the person started it, an @-mention), 'by their command' (the model's Agent
    call in a turn that began with a command of theirs, or after it loaded a skill of theirs), 'by their agent' (an Agent call made inside
    the log of a sub-agent whose role is their own). A launch the model chose on its own is not here."""
    if "_g3_team" in lt.__dict__:   # PREWARM builds it once before the checks fork
        return lt._g3_team
    own = {name for name, p in _agent_files(lt) if _owned(lt, p)} - builtin_agent_types()   # an override of a built-in type is not theirs
    out = {}
    lt._g3_team = (out, own)
    if not own:
        return out, own
    def add(sid, role, how):
        if role in own and lt.interactive(sid):
            out.setdefault(sid, {}).setdefault(role, how)
    for m in lt.meta:
        if not m.get("toolUseId") and not m.get("teamName") and m.get("taskKind") != "in_process_teammate":
            add(m["_session"], m.get("agentType"), "by hand")
    cmds = _own_commands(lt)
    import bisect
    mine = [c for c in lt.calls if c["name"] in AGENT_TOOLS and not c["sc"] and lt.interactive(c["s"]) and _inp(c).get("subagent_type") in own]
    if mine and cmds:
        marks, skills = {}, {}
        for m in lt.typed:
            marks.setdefault(m["s"], []).append((m["t"], False))
        for c in lt.commands:
            marks.setdefault(c["s"], []).append((c["t"], c["name"].lstrip("/") in cmds))
        for c in lt.calls:
            if c["name"] == "Skill" and not c["sc"] and _inp(c).get("skill") in cmds:
                skills.setdefault(c["s"], []).append(c["t"])
        for v in marks.values():
            v.sort()
        for c in mine:
            ms = marks.get(c["s"]) or []
            i = bisect.bisect_right([t for t, _ in ms], c["t"]) - 1
            if i < 0:
                continue
            start, command = ms[i]
            if command or any(start <= t <= c["t"] for t in skills.get(c["s"], [])):
                add(c["s"], _inp(c).get("subagent_type"), "by their command")
    parent = {m["_file"][:-len(".meta.json")] + ".jsonl": m.get("agentType") for m in lt.meta if m.get("_file")}
    for r in lt.grep(rb'"name":\s*"(?:Agent|Task)"'):
        if r["_sc"] and parent.get(r["_f"]) in own:
            for b in (r.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in AGENT_TOOLS and isinstance(b.get("input"), dict):
                    add(r["_sid"], b["input"].get("subagent_type"), "by their agent")
    return out, own


@check(LEAD, "L3", 0)
def lead_l3_0(lt):
    """Sessions in which the person's own agent roles (agent files theirs by the one ownership rule; not an override of a built-in type)
    were used as a team: 2+ distinct own roles launched in one interactive session, each started by the person (an @-mention or fork-style
    start), by a command or skill of theirs, or by one of their own agents: and that on 2+ sessions. A role the model picked on its own
    does not count, nor one launch of one role. Value is the number of such sessions, 0 when fewer than two."""
    team, own = _team_sessions(lt)
    teams = {s: r for s, r in team.items() if len(r) >= 2}
    value = len(teams) if len(teams) >= 2 else 0
    return {"value": value, "evidence": [f"{len(own)} own agent files"] + [f"{s[:8]}: " + ", ".join(f"{r} ({h})" for r, h in sorted(rs.items()))[:90] for s, rs in list(teams.items())[:2]],
            "note": f"{len(teams)} sessions with 2+ own roles that were steered (needs 2); model-picked launches, plugin agents and sub-agents "
                    "started by sub-agents of other types are not counted; whether the roles worked on one job is taken from the one session"}


@fixture(LEAD, "L3", 0)
def _(fx):
    fx.repo("~/.claude/agents", {"planner.md": "---\nname: planner\n---\nPlan.\n"}, by="work")        # theirs: their own tracked folder
    fx.file("~/.claude/agents/reviewer.md", "---\nname: code-reviewer\ndescription: x\n---\nReview.\n")   # theirs: untracked but no install receipt (ruling 11-05)
    fx.repo("~/work/shop", {".claude/agents/deployer.md": "---\ndescription: ship\n---\nDeploy.\n", ".claude/commands/review.md": "Review it.\n"}, by="teammate")
    fx.commit("~/work/shop", {".claude/agents/qa.md": "---\ndescription: qa\n---\nTest.\n", ".claude/commands/ship.md": "Plan, build, review.\n"}, by="work")
    polish = "---\nname: polish\ndescription: Polish a finished interface, one pass at a time\n---\nLook at spacing, type and colour; change one thing per pass.\n"
    fx.repo("~/code/upstream", {"source/skills/polish/SKILL.md": polish}, by="teammate")             # the upstream project's clone, someone else's commits
    fx.session("~/code/upstream", entrypoint="claude-vscode").typed("hi")                            # a folder the sessions ran in: ownership reads it
    fx.commit("~/work/shop", {".claude/skills/polish/SKILL.md": polish}, by="work")                  # theirs by git, yet a copy of the upstream file: installed (rule 4)
    def launch(s, *roles):
        for t in roles:
            s.call("Agent", {"subagent_type": t, "prompt": "x"})
    s1 = fx.session("~/work/shop", entrypoint="claude-vscode")                      # counts: their own command, two own roles
    s1.command("ship"); launch(s1, "planner", "code-reviewer")
    s2 = fx.session("~/work/shop", entrypoint="claude-vscode")                      # counts: the person started both
    s2.typed("split it"); s2.subagent("planner", "plan", by_claude=False); s2.subagent("qa", "test", by_claude=False)
    s3 = fx.session("~/work/shop", entrypoint="claude-desktop")                     # trap (what the old rule counted): the model picked three own roles
    s3.typed("fix it"); launch(s3, "planner", "code-reviewer", "qa", "qa")
    s4 = fx.session("~/work/shop", entrypoint="claude-vscode")                      # trap: their command, but one own role and a built-in one
    s4.command("ship"); launch(s4, "planner", "planner", "Explore")
    s5 = fx.session("~/work/shop", entrypoint="claude-vscode")                      # trap: a teammate's command, not theirs
    s5.command("review"); launch(s5, "planner", "code-reviewer")
    s6 = fx.session("~/work/shop", entrypoint="claude-vscode")                      # counts: an own agent started two other own roles
    s6.typed("go")
    s6.subagent("planner", "orchestrate", calls=[("Agent", {"subagent_type": "qa", "prompt": "x"}, "ok"),
                                                  ("Agent", {"subagent_type": "code-reviewer", "prompt": "x"}, "ok")])
    s7 = fx.session("~/work/shop", entrypoint="sdk-cli")                            # trap: a headless run with the same command
    s7.command("ship"); launch(s7, "planner", "code-reviewer")
    s7.command_expansion("/ship", "Plan, build, review."); launch(s7, "planner", "code-reviewer")   # trap: and in the other record shape (a skill or plugin command)
    s9 = fx.session("~/work/shop", entrypoint="claude-vscode")                      # counts: their command in the <command-message> shape Claude Code writes for skills and plugins
    s9.command_expansion("/ship", "Plan, build, review."); launch(s9, "planner", "code-reviewer")
    s8 = fx.session("~/work/shop", entrypoint="claude-vscode")                      # trap: a skill they committed as a copy of the upstream's, not theirs
    s8.command("polish"); launch(s8, "planner", "code-reviewer")
    return {"value": 4}      # s1, s2, s6, s9


# ====================================================================================== keep-client-code-and-data-in-bounds
KEEP = "keep-client-code-and-data-in-bounds"
_TRUTHY = lambda v: str(v).strip().lower() not in ("", "0", "false", "no", "none")


_PROVIDER_FLAGS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


@check(KEEP, "L3", 0)
def keep_l3_0(lt):
    """Provider routing the person set in a project's settings: env CLAUDE_CODE_USE_BEDROCK / VERTEX / FOUNDRY switched on (a different
    provider by definition) in a project file of theirs, shared or local; and ANTHROPIC_BASE_URL (a gateway) only in a shared project
    file git says is theirs, since in a local file it can be a personal proxy as easily as a client's gateway. An apiKeyHelper is a key
    script, not a route, and is not counted. Never user-wide settings, an employer's managed settings or a teammate's file."""
    on = set()
    for scope, path, d in _mine_settings(lt):
        if not scope.startswith("project"):
            continue
        env = d.get("env") if isinstance(d.get("env"), dict) else {}
        for k, v in env.items():
            if (k in _PROVIDER_FLAGS or (k == "ANTHROPIC_BASE_URL" and scope == "project")) and _TRUTHY(v):
                on.add((k, str(v)))
    return {"value": len(on), "evidence": [k for k, _ in sorted(on)][:3], "note": "variables exported in a shell profile, and user-wide settings, are not project settings; a gateway URL in a local file is not counted"}


@fixture(KEEP, "L3", 0)
def _(fx):
    fx.settings("user", {"env": {"ANTHROPIC_BASE_URL": "https://my-own-proxy.example", "CLAUDE_CODE_USE_BEDROCK": "1"}})       # trap: user-wide, not a project's
    fx.repo("~/work/shop", {"README.md": "x"})
    fx.commit("~/work/shop", {".claude/settings.json": json.dumps({"env": {
        "ANTHROPIC_BASE_URL": "https://gw.client.example", "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "0",
        "DISABLE_TELEMETRY": "1", "ANTHROPIC_MODEL": "my-model", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4317"}})}, by="work")
    fx.settings("project-local", {"apiKeyHelper": "~/bin/get-key", "env": {"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_BASE_URL": "http://localhost:8080"}}, repo="~/work/shop")
    fx.repo("~/work/team", {"README.md": "x"}, by="teammate")
    fx.commit("~/work/team", {".claude/settings.json": json.dumps({"env": {"CLAUDE_CODE_USE_VERTEX": "1", "ANTHROPIC_BASE_URL": "https://team-gw.example"}})}, by="teammate")   # trap: a teammate's
    fx.settings("managed", {"env": {"ANTHROPIC_BASE_URL": "https://corp.example"}})                              # trap: the employer's
    fx.repo("~/work/keys", {"README.md": "x"})
    fx.settings("project-local", {"apiKeyHelper": "~/bin/get-key"}, repo="~/work/keys")                        # trap: a key script alone
    for r in ("~/work/shop", "~/work/team", "~/work/keys"):
        fx.session(r, entrypoint="claude-vscode").typed("hi")
    return {"value": 3}


@check(KEEP, "L3", 1)
def keep_l3_1(lt):
    """Folders where the person made their own connector choice, by an act and not by answering Claude Code's approval prompt (which
    writes enabledMcpjsonServers and disabledMcpjsonServers into the local project settings): a server switched off in /mcp
    (~/.claude.json `disabledMcpServers` for the folder), allowed/denied servers (allowedMcpServers, deniedMcpServers) in the person's
    project settings, and the enabled/disabled lists in a shared project file git says is theirs. The home folder is not a project."""
    folders = set()
    for scope, path, d in _mine_settings(lt):
        if not scope.startswith("project"):
            continue
        keys = ["allowedMcpServers", "deniedMcpServers"] + (["enabledMcpjsonServers", "disabledMcpjsonServers"] if scope == "project" else [])
        if any(d.get(k) for k in keys):
            folders.add(os.path.realpath(os.path.dirname(os.path.dirname(path))))
    try:
        proj = json.load(open(os.path.join(lt.home, ".claude.json"))).get("projects") or {}
    except (OSError, ValueError):
        proj = {}
    mine = {os.path.realpath(f) for f in lt.session_folders()} | set(lt.repos)
    for p, v in proj.items():
        if os.path.realpath(p) != lt.home and os.path.realpath(p) in mine and isinstance(v, dict) and v.get("disabledMcpServers"):
            folders.add(lt.repo_of(p) or os.path.realpath(p))
    return {"value": len(folders), "evidence": [os.path.basename(f) for f in sorted(folders)][:3],
            "note": "answers to Claude Code's approval prompt (enabledMcpjsonServers, disabledMcpjsonServers in a local file) are not counted; it shows a choice, not a client's rule"}


@fixture(KEEP, "L3", 1)
def _(fx):
    for r in ("shop", "api", "allall", "prompted", "team"):
        fx.repo(f"~/code/{r}", {"README.md": "x"}, by="teammate" if r == "team" else "work")
    fx.repo("~/work/home-like", {"README.md": "x"})
    fx.file("~/.claude.json", json.dumps({"projects": {fx.p("~/code/shop"): {"disabledMcpServers": ["slack"]},                  # counts: switched off in /mcp
                                                       fx.p("~/code/prompted"): {"enabledMcpjsonServers": ["db"], "disabledMcpjsonServers": ["web"]},   # trap: prompt answers
                                                       fx.home: {"disabledMcpServers": ["github"]},                              # trap: the home folder
                                                       "/somewhere/else": {"disabledMcpServers": ["x"]}}}))                      # trap: never a session folder
    fx.commit("~/code/api", {".claude/settings.json": json.dumps({"deniedMcpServers": [{"serverName": "x"}]})}, by="work")          # counts
    fx.settings("project", {"enableAllProjectMcpServers": True}, repo="~/code/allall")                       # trap: allow everything
    fx.settings("project-local", {"enabledMcpjsonServers": ["db"], "disabledMcpjsonServers": ["web"]}, repo="~/code/prompted")     # trap: prompt answers
    fx.commit("~/code/team", {".claude/settings.json": json.dumps({"deniedMcpServers": [{"serverName": "y"}]})}, by="teammate")    # trap: a teammate's
    fx.settings("user", {"deniedMcpServers": [{"serverName": "x"}]})                                           # trap: user-wide
    for r in ("shop", "api", "allall", "prompted", "team"):
        fx.session(f"~/code/{r}", entrypoint="claude-vscode").typed("hi")
    fx.session(fx.home, entrypoint="claude-vscode").typed("hi")
    return {"value": 2}


def _hook_paths_all(lt):
    """Each repo's git hooks folder, asked of all repos at once (threads) before the checks run."""
    if lt.repos:
        _hooks_path(lt, lt.repos[0])


# Built once before the checks split into workers (evaluate.py / run_portable.py run_prewarm_hooks).
PREWARM = [_case_files, _hook_paths_all, _team_sessions]
