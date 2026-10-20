"""parse — what a [[link]] is, and what a walk never enters. The one copy of each.

Standard library, no pathlib: the index behind memory-gate.py (before every Bash call) needs the
prune rule and the link, and nothing that resolves (resolve_note.py re-exports all of this).
"""
import re

LINK_RE = re.compile(r"!?\[\[([^\]\[|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
# The same link, with every part captured: 1 = "!" or "", 2 = target, 3 = heading, 4 = alias.
LINK_PARTS_RE = re.compile(r"(!?)\[\[([^\]\[|#\n]+)(?:#([^\]|\n]*))?(?:\|([^\]\n]*))?\]\]")

# Non-dot directories worth skipping: vendored or generated trees whose
# markdown nobody authored (a bare `venv/`, Rust's `target/`). Dot-directories are
# pruned by prefix. Not pruned, on purpose: `_archive/` and every `_` folder (archived
# notes stay resolvable) and a repo nested inside the tree (a link may cross into it).
PRUNE_DIRS = {"node_modules", "__pycache__", "dist", "build", "site-packages", "venv", "target"}


def is_pruned(name):
    """True for a directory name no scan should descend into."""
    return name.startswith(".") or name in PRUNE_DIRS


# Code is literal. A [[link]] inside a fenced block or a backtick span is an example of a
# link, not a link — the format's own rule, and the only way a document ABOUT links can
# exist without every example dangling. Fences: a run of three or more ` or ~ at line start,
# to the first line that is a run of the SAME character at least as long, and nothing after it
# but blanks (CommonMark's rule: ```` holds a ``` example, and ```python never closes), or to
# the end of text. Spans: a backtick run to the same run on the same line.
FENCE_RE = re.compile(r"^(?P<fence>(?P<ch>[`~])(?P=ch){2,})[^\n]*\n.*?(?:^(?P=fence)(?P=ch)*[ \t]*$|\Z)", re.S | re.M)
SPAN_RE = re.compile(r"(`+)[^`\n]*?\1")


def unfenced(text):
    """`text` with every fenced block blanked (same length); code spans kept."""
    return FENCE_RE.sub(lambda m: " " * len(m.group(0)), text)


def linkable(text):
    """`text` with every fenced block and code span blanked (same length, so positions hold)."""
    return SPAN_RE.sub(lambda m: " " * len(m.group(0)), unfenced(text))


def links(text):
    """Every [[link]] target in TEXT, as written and in order, code left out: THE extraction —
    a note's body, a frontmatter value, a line of prose alike."""
    return [m.group(1) for m in LINK_RE.finditer(linkable(text))]
