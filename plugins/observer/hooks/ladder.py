# COPY of plugins/vault/src/vault/engine/ladder.py: edit that file, never this one (scripts/copies.py)
"""ladder — where a [[name]] looks, nearest first: the one ladder, and the one Resolver over it.

Four callers each built their own "where to look" and disagreed (the split's phase 0 pinned how:
plugins/memory/tests/characterization/test_ladders.py). They are presets of `ladder()` now, and
every knob a preset turns is named at the call:

    scope.scopes(at)         the Read hooks, bin/suggest: every tree around a file — its nearest
                             marker root, $CLAUDE_PROJECT_DIR, its own folder, $WIKILINKS_ROOTS
    vault_root.scopes(cwd)   $AK_PATH alone; else each legacy `.ak` vault up the walk, the global kit last
    workspace.ladder(cwd)    ONE tree, the workspace (a `.git`, else $CLAUDE_PROJECT_DIR, else cwd),
                             between the legacy vaults inside it and those above it; the global kit last
    chain.Chain(at)          the workspace's ladder, through the Resolver

A rung is (path, kind). The kinds, and the knob that adds each:

    anchor · project · own   the trees (anchor=, fallback_markers=, project_dir=, own_dir=); one_tree=
                             keeps the first
    extra                    a tree named outright (extra_roots=)
    vault · agents · agent-memory
                             a tree's memory, just before it (memory_rungs=): its `.ak` when that holds
                             the manifest, `.agents`, each `.claude/agent-memory*`. A walk never enters a
                             dotfolder, so each needs a rung of its own
    legacy                   every `.ak` vault the walk up from `at` meets (legacy_ak=): the ones inside
                             the first tree go before it, the rest after
    global                   the global vault — $AK_PATH, else the kit home's vault/ — last (global_home=)
    explicit                 $AK_PATH, which returns alone (env_short_circuit=)

$HOME and everything above it is never a tree — it is where trees live; a new window opens in ~,
and walking it indexes a whole account. never_kit= adds the global kit and everything under it.

os only at import: vault_root.scopes() runs before every Bash call (its budget note), so the
Resolver imports pathlib and resolve_note when one is built, never before.
"""
import os

try:  # the package's copy is `vault.engine.ladder`; a hook's is a top-level module beside its own _brand.py
    from ._brand import HOME_DIR, env
except ImportError:
    from _brand import HOME_DIR, env

MARKERS = (".claude-plugin/plugin.json", "vault-manifest.json", ".git")
VAULT_MARKER = "vault-manifest.json"


class Rung(tuple):
    """(path, kind): a folder the ladder looks in, realpath'd, and why it is there (the module docstring)."""
    __slots__ = ()

    def __new__(cls, path, kind):
        return tuple.__new__(cls, (path, kind))

    path = property(lambda self: self[0])
    kind = property(lambda self: self[1])


def _real(p):
    return os.path.realpath(os.path.expanduser(str(p)))


def within(p, d):
    """P is D or under it (both realpath'd strings)."""
    return p == d or p.startswith(d.rstrip(os.sep) + os.sep)


def is_vault(p):
    """A directory is a vault when it holds the manifest. `.ak/` without one is
    somebody else's directory (a cache, a stub, a half-run `ak init`) and is skipped."""
    return os.path.isfile(os.path.join(str(p), VAULT_MARKER))


# ---- the kit home's layout: the only place that names a folder in it (layout 2, 2026-10-01) ----
#
#   <kit>/vault/    the global vault: its manifest, the notes, _templates, .obsidian, .git
#   <kit>/db/       what can't be rebuilt: brain.db, zvec/, the ledgers, lineage, the registries
#   <kit>/cache/    what rebuilds itself when deleted: indexes, names, cursors, capture scratch
#   <kit>/moves/    ak move's journals and undo copies
#   <kit>/layout    {"layout": 2}
#
# Layout 1 kept the notes at the kit's root and the state beside them in `.store/` and
# `.brain-state/`; scripts/migrate/4-home-layout.py moved a home from one to the other.
LAYOUT = 2
VAULT_DIR, DB_DIR, CACHE_DIR, MOVES_DIR, LAYOUT_FILE = "vault", "db", "cache", "moves", "layout"


def kit_home():
    """The kit home, realpath'd, where its state lives: $AK_HOME, else $AK_PATH (a sandbox that pins
    only the vault keeps its state beside it, sealed), else ~/.ak."""
    h = (env("HOME") or "").strip() or (env("PATH") or "").strip()
    return _real(h) if h else _real("~/" + HOME_DIR)


def global_kit():
    """The global vault, realpath'd: $AK_PATH, else the kit home's `vault/` — vault_root.home_root()'s
    answer, from any cwd. Present even when it does not exist yet."""
    p = (env("PATH") or "").strip()
    return _real(p) if p else os.path.join(kit_home(), VAULT_DIR)


def db_dir():
    """What the kit can't rebuild — brain.db, zvec/, the ledgers: $AK_STORE, else <kit>/db."""
    s = (env("STORE") or "").strip()
    return _real(s) if s else os.path.join(kit_home(), DB_DIR)


def cache_dir():
    """What rebuilds itself when deleted: <kit>/cache."""
    return os.path.join(kit_home(), CACHE_DIR)


def moves_dir():
    """ak move's journals and undo copies: <kit>/moves."""
    return os.path.join(kit_home(), MOVES_DIR)


def layout_file():
    return os.path.join(kit_home(), LAYOUT_FILE)


def _never(kit):
    """The test for a folder that is never a tree: $HOME and above; with KIT, the global kit and below too."""
    user = _real("~")
    kits = (global_kit(), kit_home(), os.path.join(user, HOME_DIR)) if kit else ()
    return lambda d: within(user, d) or any(within(d, b) for b in kits)


def memory_of(d):
    """The dotfolders under tree D that hold notes, existing ones only: its vault, `.agents`, `.claude/agent-memory*`."""
    vault = os.path.join(d, HOME_DIR)
    out = [Rung(vault, "vault")] if is_vault(vault) else []
    agents = os.path.join(d, ".agents")
    if os.path.isdir(agents):
        out.append(Rung(agents, "agents"))
    claude = os.path.join(d, ".claude")  # portable: ok — a tree's own .claude/, not Claude Code's home
    try:
        names = sorted(n for n in os.listdir(claude) if n.startswith("agent-memory"))
    except OSError:
        names = []
    out += [Rung(os.path.join(claude, n), "agent-memory") for n in names if os.path.isdir(os.path.join(claude, n))]
    return out


def _nearest(at, marks, never):
    d = at
    while not never(d):
        if any(os.path.exists(os.path.join(d, m)) for m in marks):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return ""


def trees(at, anchor="marker", markers=MARKERS, project_dir=None, own_dir=True, one_tree=False, never_kit=False,
          fallback_markers=()):
    """The trees AT stands in, nearest first: the nearest ancestor holding a marker (anchor="git": a `.git`;
    None: no anchor) — else, with FALLBACK_MARKERS, the nearest holding one of those — then PROJECT_DIR
    (True: $CLAUDE_PROJECT_DIR) when AT is under it, then AT itself. ONE_TREE keeps the first of them:
    a workspace. AT is a realpath'd directory."""
    never = _never(never_kit)
    out = []
    if anchor:
        d = _nearest(at, (".git",) if anchor == "git" else markers, never)
        d = d or (_nearest(at, fallback_markers, never) if fallback_markers else "")
        if d:
            out.append(Rung(d, "anchor"))
    pd = os.environ.get("CLAUDE_PROJECT_DIR") if project_dir is True else project_dir
    pd = (pd or "").strip()
    if pd:  # Claude Code sets CLAUDE_PROJECT_DIR to the launch cwd — ~ for a new window
        pd = _real(pd)
        if within(at, pd) and not never(pd):
            out.append(Rung(pd, "project"))
    if own_dir and not never(at):
        out.append(Rung(at, "own"))
    return out[:1] if one_tree else out


def legacy_vaults(at):
    """Every `.ak` vault the walk up from AT meets, nearest first: a folder that CONTAINS one or that IS
    one (a session inside `~/.ak/collections` belongs to `~/.ak`, never to an invented `~/.ak/.ak`).
    The global vault is the global rung or nothing — the walk never counts it as a project scope."""
    default = {_real("~/" + HOME_DIR), _real(os.path.join("~", HOME_DIR, VAULT_DIR)), global_kit()}
    found = []
    d = at
    while True:
        nested = os.path.join(d, HOME_DIR)
        if is_vault(nested) and nested not in default:
            found.append(nested)
        elif is_vault(d) and d not in default:
            found.append(d)
        parent = os.path.dirname(d)
        if parent == d:  # filesystem root
            break
        d = parent
    return found


def _dedupe(rungs):
    seen, out = set(), []
    for r in rungs:
        if r.path not in seen:
            seen.add(r.path)
            out.append(r)
    return out


def ladder(at=None, *, anchor="marker", markers=MARKERS, fallback_markers=(), project_dir=None, own_dir=True,
           one_tree=False, never_kit=False, extra_roots=None, memory_rungs=True, legacy_ak=False, global_home=False,
           env_short_circuit=False, existing=True):
    """The rungs a place sees, nearest first, deduped (the module docstring names each knob).

    AT is a directory (default: the cwd). PROJECT_DIR: a path, or True for $CLAUDE_PROJECT_DIR.
    EXTRA_ROOTS: an os.pathsep-separated string (":"; ";" on Windows) or a list, or True for $WIKILINKS_ROOTS. EXISTING keeps
    only rungs that are directories — off, the global kit stays even before `ak init` makes it."""
    if env_short_circuit:
        explicit = (env("PATH") or "").strip()
        if explicit:
            return [Rung(_real(explicit), "explicit")]
    at = _real(at if at else os.getcwd())
    found = trees(at, anchor=anchor, markers=markers, project_dir=project_dir, own_dir=own_dir,
                  one_tree=one_tree, never_kit=never_kit, fallback_markers=fallback_markers)
    raw = os.environ.get("WIKILINKS_ROOTS", "") if extra_roots is True else (extra_roots or "")
    for r in (raw.split(os.pathsep) if isinstance(raw, str) else raw):
        if str(r).strip():
            found.append(Rung(_real(str(r).strip()), "extra"))

    tail = [Rung(p, "legacy") for p in legacy_vaults(at)] if legacy_ak else []
    if global_home:
        tail = _dedupe(tail + [Rung(global_kit(), "global")])
    body, last = (tail[:-1], tail[-1:]) if global_home else (tail, [])
    head = found[0].path if found else None
    inside = [r for r in body if head and within(r.path, head)]
    out = list(inside)
    for t in found:
        if memory_rungs:
            out += memory_of(t.path)
        out.append(t)
    out += [r for r in body if r not in inside] + last
    out = _dedupe(out)
    return [r for r in out if os.path.isdir(r.path)] if existing else out



def _rn():
    try:
        from . import resolve_note as rn
    except ImportError:
        import resolve_note as rn
    return rn


class Resolver:
    """A name across a ladder, nearest first, each scope indexed at most once.

    The first scope with ANY hit answers. A miss falls through; an ambiguity does not — two notes
    with one stem in the nearest scope is a fact about that scope, not a reason to look further. A
    path-qualified `[[a/b]]` is a path first: the first scope holding a note whose path ends in
    `/a/b.md` answers, across the whole ladder; only when none does is it the bare stem `b`, by the
    rule above. A nearer `b.md` elsewhere never shadows the path the author wrote."""

    def __init__(self, ladder):
        from pathlib import Path
        self.ladder = [Path(s.path if isinstance(s, Rung) else s) for s in ladder]
        self._notes = {}

    def _sub(self, ladder):
        """A resolver over LADDER sharing the indexes already built (a subclass keeps its type)."""
        sub = object.__new__(type(self))
        Resolver.__init__(sub, ladder)
        sub._notes = self._notes
        return sub

    @property
    def root(self):
        """The nearest scope — the one a write here belongs to."""
        return self.ladder[0]

    def notes(self, scope):
        if scope not in self._notes:
            self._notes[scope] = _rn().index(scope) if scope.is_dir() else []
        return self._notes[scope]

    def all_notes(self):
        """Every note the ladder holds, nearest scope first."""
        return [p for s in self.ladder for p in self.notes(s)]

    def scope_of(self, path):
        """The nearest scope holding PATH, or None when it is outside every scope."""
        from pathlib import Path
        path = Path(path).resolve()
        for s in self.ladder:
            if s == path or s in path.parents:
                return s
        return None

    def above(self, scope):
        """The scopes further up the ladder than SCOPE."""
        return self.ladder[self.ladder.index(scope) + 1:] if scope in self.ladder else []

    def from_note(self, note):
        """The ladder a NOTE's own links resolve against: its scope, then everything above. Links go up
        or stay inside a scope — a note in the global kit never sees a project's notes."""
        s = self.scope_of(note)
        if s is None:
            return self._sub([])
        inner = [r for r in self.ladder[:self.ladder.index(s)] if s in r.parents]  # a workspace's `.agents/` …
        return self._sub([*inner, s, *self.above(s)])

    def resolve(self, target):
        """(hits, scope): exact matches from the first scope with any; ([], None) on a miss everywhere."""
        rn = _rn()
        for s in self.ladder:
            hits = rn.resolve_qualified(target, self.notes(s))  # [] for a bare name
            if hits:
                return hits, s
        for s in self.ladder:
            hits = rn.resolve_exact(target, self.notes(s))
            if hits:
                return hits, s
        return [], None

    def resolve_fuzzy(self, name):
        """A name a person typed: exact over the ladder first, then a substring match over
        every scope (the nearest scope's candidates listed first)."""
        hits, _ = self.resolve(name)
        if hits:
            return hits
        return _rn().resolve_fuzzy(name, self.all_notes())

    def shadowed(self, note):
        """Notes further up the ladder that carry NOTE's stem — a name is unique across the
        ladder, so a new note may not hide one a scope above already answers to."""
        from pathlib import Path
        s = self.scope_of(note)
        if s is None:
            return []
        stem = Path(note).stem.lower()
        return [p for up in self.above(s) for p in self.notes(up) if p.stem.lower() == stem]
