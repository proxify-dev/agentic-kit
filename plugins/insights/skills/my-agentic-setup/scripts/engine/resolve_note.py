"""resolve_note — the one markdown walk, and name → path resolution over it.

This is the engine every wikilinks surface shares: the read-time hook, the
CLI, the @[[ completion. It is stdlib-only on purpose — hooks run under
whatever bare python3 is on PATH, and completion runs per keystroke.

The rule for what a walk sees lives here (parse.py) and nowhere else. Four walkers once
each carried their own rule and disagreed by ~1000 files: three pruned
dotfolders but descended into node_modules, one pruned both. Change it here
and every scan changes with it.

Resolution: `[[stem]]` matches a file by stem, case-insensitive, exact. A
path-qualified `[[a/b]]` narrows by path suffix (across a whole ladder first:
ladder.Resolver), and falls back to the bare stem only when no path ends so. Ambiguity is data, not an
error — callers get every candidate and decide. `resolve_fuzzy` adds a
substring fallback for humans typing bare names; link targets never use it.
"""
import re
from pathlib import Path

try:  # what a [[link]] is and what code hides: parse.py, pathlib-free for the hot paths that need only that
    from .parse import FENCE_RE, LINK_PARTS_RE, LINK_RE, PRUNE_DIRS, SPAN_RE, is_pruned, linkable, links, unfenced  # noqa: F401
except ImportError:
    from parse import FENCE_RE, LINK_PARTS_RE, LINK_RE, PRUNE_DIRS, SPAN_RE, is_pruned, linkable, links, unfenced  # noqa: F401

ASSET_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,10}$")


def iter_md(root, extra_prune=None):
    """Every authored .md under root. THE walk — see PRUNE_DIRS. EXTRA_PRUNE(dirpath, name) -> True
    prunes one more folder: a caller's own rule on top of the walk's, never instead of it."""
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not is_pruned(d) and not (extra_prune and extra_prune(dirpath, d)))
        for f in sorted(filenames):
            if f.endswith(".md") and not f.startswith("."):
                yield Path(dirpath) / f


def index(root):
    """Every .md file under root, as a list."""
    return list(iter_md(root))


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def frontmatter_fields(path):
    """Top-level `key: value` pairs between --- fences. No YAML parser: that is
    all a resolver needs, and hooks import nothing."""
    text = read_text(path)
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fields = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if m and m.group(2).strip():
            fields[m.group(1)] = m.group(2).strip()
    return fields


def annot(path):
    fm = frontmatter_fields(path)
    parts = [f"{k}={fm[k]}" for k in ("type", "status") if k in fm]
    return ("  " + " ".join(parts)) if parts else ""


def clean(name):
    """Normalize a bare name / [[wikilink]] / link target to a comparable stem-path."""
    name = name.strip().lstrip("!").strip()
    if name.startswith("[[") and name.endswith("]]"):
        name = name[2:-2]
    name = name.split("#", 1)[0].split("|", 1)[0].strip().strip("/")
    if name.lower().endswith(".md"):
        name = name[:-3]
    return name


def is_asset(target):
    """`[[diagram.png]]`, `[[data.json]]` — a file that is not a note. Never
    walked, never called dangling."""
    return bool(ASSET_EXT_RE.search(clean(target))) and not clean(target).lower().endswith(".md")


def resolve_qualified(name, notes):
    """A path-qualified `[[a/b]]`: the notes whose path ends in `/a/b.md`; [] for a bare name."""
    n = clean(name).lower()
    if "/" not in n:
        return []
    stem = n.rsplit("/", 1)[-1]
    return [p for p in notes if p.stem.lower() == stem and p.as_posix().lower().endswith("/" + n + ".md")]  # "/" on Windows too


def resolve_exact(name, notes):
    n = clean(name).lower()
    if not n:
        return []
    qualified = resolve_qualified(name, notes)  # path-qualified link: prefer the suffix match
    if qualified:
        return qualified
    stem = n.rsplit("/", 1)[-1]
    return [p for p in notes if p.stem.lower() == stem]


def resolve_fuzzy(name, notes):
    exact = resolve_exact(name, notes)
    if exact:
        return exact
    n = clean(name).lower().rsplit("/", 1)[-1]
    if not n:
        return []
    return [p for p in notes if n in p.stem.lower()]


def outgoing(note):
    """Unique [[link]] targets in document order, as written. Code is not scanned."""
    seen, out = set(), []
    for target in links(read_text(note)):
        target = target.strip()
        key = clean(target).lower()
        if key and key not in seen:
            seen.add(key)
            out.append(target)
    return out


def backlinks(note, notes):
    """Every note whose links name this one's stem."""
    stem = Path(note).stem.lower()
    inbound = []
    for p in notes:
        if p == note:
            continue
        for target in links(read_text(p)):
            if clean(target).lower().rsplit("/", 1)[-1] == stem:
                inbound.append(p)
                break
    return inbound
