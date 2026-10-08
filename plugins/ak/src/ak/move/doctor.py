"""doctor — what came loose on this machine, and the one command that reattaches each piece.

Read-only. Nothing a check finds is changed here; a finding carries its fix, and a fix that moves
something is always a journaled `ak ws move …`, so it can be undone.

    sessions   chat history whose folder is gone — moved by hand (→ ws move --adopt) or deleted
    plugins    enabled or installed plugin ids their marketplace no longer ships (a rename left behind)
    projects   ~/.claude.json project entries naming a folder that is gone
    settings   settings.json / settings.local.json paths that point at nothing — a whole value
               (additionalDirectories, env) or inside a command string (fileSuggestion, statusLine, hooks)
    git        worktrees git can no longer find
    links      ~/.local/bin links into folders that are gone
    moves      a move that stopped half way (→ moves undo / resume)
    layout     the kit home is on layout 2 (vault/ · db/ · cache/ · moves/), no layout-1 state folder left
    gateway    the model-call wire answers (read only — doctor never touches it)
    + every plugin's relocate handler's detect()

Folders under the temp folder (ephemeral_roots) are scratch: counted on one line, never a failure.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess

from ak._brand import CLI, cmd, is_windows, posix, temp_dir
from ak.home import LAYOUT_1_STATE as LAYOUT_1  # layout 1 kept the state here, beside the notes (brand: historical)
from ak.move import plan as P

POSIX_SCRATCH = ("/tmp/", "/private/tmp/", "/private/var/folders/", "/var/folders/", "/workspace/")  # portable: ok (POSIX only, below)


def _dir_form(p: str) -> str:
    """P with "/" separators and one trailing "/", for prefix tests (case-folded on Windows)."""
    q = posix(p).rstrip("/") + "/"
    return q.lower() if is_windows() else q


def ephemeral_roots() -> tuple:
    """The scratch folders: the system temp folder, and on macOS/Linux POSIX_SCRATCH too."""
    t = str(temp_dir())
    roots = {_dir_form(t), _dir_form(os.path.realpath(t))}
    return tuple(sorted(roots)) + (() if is_windows() else POSIX_SCRATCH)


def _ephemeral(p: str) -> bool:
    """Scratch: under the temp folder — unless it is under $HOME, which is always a real place, and the
    temp folder is not (Windows keeps it there: %LOCALAPPDATA%\\Temp)."""
    home = _dir_form(os.path.realpath(os.path.expanduser("~")))
    q = _dir_form(p)
    return any(q.startswith(r) and (r.startswith(home) or not q.startswith(home)) for r in ephemeral_roots())


def _f(check, level, what, fix=None, examples=None):
    d = {"check": check, "level": level, "what": what, "fix": fix}
    if examples:
        d["examples"] = examples
    return d


def _home_index(depth: int = 4) -> dict:
    """basename → folders under $HOME holding a .git, depth-limited: where a moved repo may be now."""
    home = os.path.expanduser("~")
    out: dict = {}
    skip = {"Library", "node_modules", ".Trash", "Applications", ".cache", ".npm", ".cargo", ".rustup"}
    base = P._depth(home)
    for d, dirs, _files in os.walk(home):
        lvl = P._depth(d) - base
        if ".git" in dirs or os.path.isfile(os.path.join(d, ".git")):
            out.setdefault(os.path.basename(d), []).append(d)
        if lvl >= depth:
            dirs[:] = []
            continue
        dirs[:] = [n for n in dirs if n not in skip and not n.startswith(".")]
    return out


def check_sessions(claude: str) -> list:
    projects = os.path.join(claude, "projects")
    scratch, removed_worktrees, deleted, movable = 0, 0, [], []
    index = None
    for d in sorted(glob.glob(os.path.join(projects, "*"))):
        if not os.path.isdir(d):
            continue
        cwd = P._cwd_of(d)
        if not cwd or os.path.isdir(cwd):
            continue
        if _ephemeral(cwd):
            scratch += 1
            continue
        wt = posix(cwd).split("/.claude/worktrees/")
        if len(wt) > 1 and os.path.isdir(wt[0]):
            removed_worktrees += 1  # a worktree removed after its work landed: nothing to reattach
            continue
        if index is None:
            index = _home_index()
        cands = [c for c in index.get(os.path.basename(cwd), []) if not os.path.isdir(os.path.join(projects, P.slug(c)))
                 or c == cwd]
        n = len(glob.glob(os.path.join(d, "*.jsonl")))
        if len(cands) == 1:
            movable.append((cwd, cands[0], n))
        else:
            deleted.append((cwd, n, cands))
    out = []
    for cwd, new, n in movable:
        out.append(_f("sessions", "fail", f"{n} session(s) of {cwd} are detached — it now looks to be at {new}",
                      f"{cmd('ws move')} --adopt {cwd} {new}"))
    for cwd, n, cands in deleted:
        hint = (f"one of: {', '.join(cands[:3])} → {cmd('ws move')} --adopt {cwd} <new>" if cands
                else "deleted, not moved — nothing to reattach")
        out.append(_f("sessions", "warn", f"{n} session(s) of {cwd}: the folder is gone", hint))
    if scratch or removed_worktrees:
        out.append(_f("sessions", "info", f"{scratch} scratch folder(s) under the temp folder and {removed_worktrees} removed "
                      "worktree(s) left history behind — expected, nothing to do"))
    if not out:
        out.append(_f("sessions", "pass", "every session folder's workspace is where it ran"))
    return out


def _load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def check_plugins(claude: str) -> list:
    settings = _load(os.path.join(claude, "settings.json")) or {}
    installed = (_load(os.path.join(claude, "plugins", "installed_plugins.json")) or {}).get("plugins") or {}
    known = _load(os.path.join(claude, "plugins", "known_marketplaces.json")) or {}
    ids = set((settings.get("enabledPlugins") or {}).keys()) | set(installed.keys())
    ships: dict = {}
    for name, m in known.items():
        loc = (m or {}).get("installLocation") or ""
        mp = _load(os.path.join(loc, ".claude-plugin", "marketplace.json")) or {}
        ships[name] = {p.get("name") for p in mp.get("plugins") or [] if isinstance(p, dict)} if mp else None
    out = []
    for pid in sorted(ids):
        if "@" not in pid:
            continue
        name, market = pid.rsplit("@", 1)
        if market not in ships:
            out.append(_f("plugins", "fail", f"{pid}: marketplace '{market}' is not registered",
                          f"claude plugin uninstall {pid}"))
        elif ships[market] is not None and name not in ships[market]:
            enabled = (settings.get("enabledPlugins") or {}).get(pid)
            out.append(_f("plugins", "fail", f"{pid}: '{market}' no longer ships '{name}'"
                          + (" — and it is still enabled, so its cached hooks still run" if enabled else ""),
                          f"claude plugin uninstall {pid}"))
    if not out:
        out.append(_f("plugins", "pass", f"all {len(ids)} plugin ids resolve in their marketplace"))
    return out


def check_projects(cj: str) -> list:
    data = _load(cj) or {}
    keys = list((data.get("projects") or {}).keys())
    gone = [k for k in keys if not os.path.isdir(k)]
    real = [k for k in gone if not _ephemeral(k)]
    out = []
    if real:
        out.append(_f("projects", "warn", f"{len(real)} ~/.claude.json project entr(ies) name a folder that is gone "
                      "(their trust and MCP choices are orphaned)", "doctor's sessions line says where each went",
                      examples=real[:5]))
    if len(gone) > len(real):
        out.append(_f("projects", "info", f"{len(gone) - len(real)} more are scratch folders — expected"))
    if not out:
        out.append(_f("projects", "pass", f"all {len(keys)} project entries name a folder that exists"))
    return out


# A path inside a command string: it starts at `/` or `~/` where a token can start (line start, whitespace,
# `=`, `(`, `:`, `;`, `,`, `|`, `&`, a backtick) — so `${ROOT}/x`, `"$HOME"/x` and `src/x` are not paths here — either
# quoted whole (`"/a b/c"`) or bare up to whitespace, a quote or a shell character.
_CMD_PATH = re.compile(r"""(?:^|(?<=[\s=(:;,|&`]))
    (?:"(?P<dq>(?:~/|/)[^"$`]*)"
      |'(?P<sq>(?:~/|/)[^'$`]*)'
      |(?P<bare>(?:~/|/)[^\s'"`;|&<>(),:*?\[\]{}$=]*))""", re.X)
_URL = re.compile(r"[A-Za-z][\w+.-]*://\S*")
_GLOB = "*?[{$"
# Keys whose value is a command line (settings.json: hooks[].command, statusLine, fileSuggestion, the helpers)
COMMAND_KEYS = ("command", "apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "gcpAuthRefresh",
                "otelHeadersHelper")


def _command_paths(cmd: str) -> list:
    """The absolute (`/x`) and home (`~/x`) paths a command line names — what it needs on disk to run.
    Not a shell parser: URLs, redirect targets (`> /x`), $VARIABLE parts and globs are left out (a glob
    keeps its folder), so a finding is a path that is really named and really missing."""
    cmd = _URL.sub(" ", cmd)
    out = []
    for m in _CMD_PATH.finditer(cmd):
        p = m.group("dq") or m.group("sq") or m.group("bare")
        if cmd[:m.start()].rstrip().endswith(">"):
            continue  # a file the command writes: it need not exist yet
        after = cmd[m.end():m.end() + 1]
        cut = [i for i in (p.find(c) for c in _GLOB) if i >= 0]
        if cut or (m.group("bare") is not None and after and after in _GLOB):
            p = p[:min(cut)] if cut else p
            p = p[:p.rfind("/") + 1]  # /x/y* → /x/ : the folder is what must be there
        if p and p != "/":
            out.append(p)
    return out


def _settings_paths(doc, path=()) -> list:
    """(key, path) for every path a settings document names: a whole-value path anywhere but
    `permissions` rules (whose `/x` is relative to the project, not the disk), and the paths inside
    a command string or an env value. `key` is the dotted key it sits under."""
    out = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            out += _settings_paths(v, path + (k,))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            out += _settings_paths(v, path + (i,))
    elif isinstance(doc, str):
        key = P._dotted(list(path))
        whole = doc.startswith(("/", "~/"))
        if path[:2] == ("permissions", "additionalDirectories"):
            out.append((key, doc))
        elif path[:1] == ("env",) or (path and path[-1] in COMMAND_KEYS):
            if whole and os.path.exists(os.path.expanduser(doc)):
                out.append((key, doc))  # one path, spaces and all
            else:
                out += [(key, p) for p in _command_paths(doc) if not _ephemeral(p)]
        elif whole and " " not in doc and path[:1] != ("permissions",):
            out.append((key, doc))
    return out


def check_settings(claude: str) -> list:
    out = []
    for name in ("settings.json", "settings.local.json"):
        f = os.path.join(claude, name)
        if name != "settings.json" and not os.path.isfile(f):
            continue
        found = _settings_paths(_load(f) or {})
        bad = [(k, p) for k, p in found if not os.path.exists(os.path.expanduser(p))]
        if bad:
            out.append(_f("settings", "fail", f"{name} names {len(bad)} path(s) that do not exist", None,
                          [f"{k}: {p}" for k, p in bad[:5]]))
        else:
            out.append(_f("settings", "pass", f"{name}: {len(found)} path(s), all present"))
    return out


def check_git(claude: str) -> list:
    repos = set()
    for d in glob.glob(os.path.join(claude, "projects", "*")):
        cwd = P._cwd_of(d) if os.path.isdir(d) else ""
        if cwd and os.path.isdir(os.path.join(cwd, ".git")):
            repos.add(cwd)
    prunable, absolute = [], 0
    for r in sorted(repos):
        rc, out = P._git("-C", r, "worktree", "list", "--porcelain")
        if rc:
            continue
        cur = ""
        for line in out.splitlines():
            if line.startswith("worktree "):
                cur = line[9:]
            elif line.startswith("prunable"):
                prunable.append(cur)
        for g in glob.glob(os.path.join(r, ".git", "worktrees", "*", "gitdir")):
            try:
                absolute += os.path.isabs(open(g, encoding="utf-8").read().strip())
            except OSError:
                pass
    out = []
    if prunable:
        out.append(_f("git", "warn", f"{len(prunable)} worktree(s) git can no longer find", "git worktree prune "
                      "(if deleted) or git worktree repair <new path> (if moved)", prunable[:5]))
    if absolute:
        out.append(_f("git", "info", f"{absolute} worktree link(s) are absolute paths: a move of their repo needs "
                      f"`{cmd('ws move')}` (it repairs them), never a bare mv"))
    if not prunable:
        out.insert(0, _f("git", "pass", f"{len(repos)} repo(s): every worktree is where git expects it"))
    return out


def check_links() -> list:
    bad = []
    for d in P.bin_dirs():
        for link in glob.glob(os.path.join(d, "*")):
            if os.path.islink(link) and not os.path.exists(link):
                bad.append(f"{link} → {os.readlink(link)}")
    if bad:
        return [_f("links", "warn", f"{len(bad)} link(s) in ~/.local/bin point at nothing", "rm the link, or "
                   "reinstall what it was", bad[:5])]
    return [_f("links", "pass", "every ~/.local/bin link resolves")]


def check_moves(home: str) -> list:
    out = []
    for d in sorted(glob.glob(os.path.join(P.moves_dir(home), "*"))):
        st = status(d)
        if st in ("half-applied", "failed", "half-undone"):
            out.append(_f("moves", "fail", f"move {os.path.basename(d)} stopped: {st}",
                          f"{cmd('moves undo')} {os.path.basename(d)}  (or: {cmd('moves resume')} …)"))
    return out or [_f("moves", "pass", "no move left half way")]


def check_layout(home: str) -> list:
    """The kit home is on layout 2: `<home>/layout` says so and no layout-1 state folder is left at its root."""
    if not os.path.isdir(home):
        return [_f("layout", "info", f"{home} does not exist yet — the next {CLI} command plants it on layout {P.LAYOUT}")]
    said = _load(P.layout_file(home))
    left = [n for n in LAYOUT_1 if os.path.isdir(os.path.join(home, n))]
    if not (isinstance(said, dict) and said.get("layout") == P.LAYOUT) or left:
        why = f"{', '.join(left)} still at its root" if left else f'no {{"layout": {P.LAYOUT}}} in {P.layout_file(home)}'
        return [_f("layout", "fail", f"home is on layout 1: {why} — the kit reads only layout {P.LAYOUT}",
                   "run scripts/migrate/4-home-layout.py (uv run, from the agentic-kit checkout)")]
    return [_f("layout", "pass", f"{home} is on layout {P.LAYOUT} (vault/ · db/ · cache/ · moves/)")]


def check_gateway() -> list:
    if P.gateway_up():
        return [_f("gateway", "pass", "the gateway answers on its port (doctor reads it, never touches it)")]
    return [_f("gateway", "info", "no gateway answering — claude uses its own login")]


def status(d: str) -> str:
    """applied · undone · failed · half-applied · half-undone · finalized · planned"""
    try:
        with open(os.path.join(d, "plan.json"), encoding="utf-8") as f:
            n = len(json.load(f)["steps"])
    except (OSError, ValueError, KeyError):
        return "unreadable"
    if os.path.exists(os.path.join(d, "FINALIZED")):
        return "finalized"
    st: dict = {}
    failed = False
    try:
        with open(os.path.join(d, "journal.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                st[e["seq"]] = e["status"]
                failed |= e["status"] == "failed"
    except OSError:
        return "planned"
    vals = list(st.values())
    if not vals:
        return "planned"
    if all(v == "undone" for v in vals):
        return "undone (after a failure)" if failed else "undone"
    if any(v == "undone" for v in vals):
        return "half-undone"
    if len(vals) == n and all(v == "done" for v in vals):
        return "applied"
    return "failed" if failed else "half-applied"


def run(plugins_dir=None) -> list:
    claude, home = P.claude_dir(), P.kit_home()
    ctx = {"claude": claude, "home": home}
    out = (check_sessions(claude) + check_plugins(claude) + check_projects(P.claude_json())
           + check_settings(claude) + check_git(claude) + check_links() + check_moves(home) + check_layout(home)
           + check_gateway())
    for name, h in P.plugin_handlers(plugins_dir):
        try:
            for f in h.detect(ctx) or []:
                out.append(_f(name, f.get("level", "info"), f.get("what", ""), f.get("fix"), f.get("examples")))
        except Exception as e:
            out.append(_f(name, "warn", f"its detect() raised {type(e).__name__}: {e}"))
    return out
