#!/usr/bin/env python3
"""The structure map of a person's agent-facing files: for each of their most active projects and for the user level
(~/.claude), which instruction files load when, how they point at each other, how the memory stores are organised,
and the shape of their skills, agents and commands. It records structure only and judges nothing: an AI reader
judges the map afterwards (README.md in this folder).

    python3 setup_map.py RUN_DIR [--projects 3]   -> RUN_DIR/setup-map.json and RUN_DIR/setup-map.md

Discovery comes from what the earlier steps wrote into RUN_DIR; this step decides nothing about whose a file is:
  anatomy-scan.json   projects (the same pick anatomy_scan.py made: most typed interactive sessions)
  chats.jsonl         the pick again when anatomy-scan.json is missing (it needs PyYAML)
  worked-in.json      the repos each session edited or committed in, wherever it started (the pick when anatomy-scan.json is missing)
  inventory.json      repos (a repo found below a folder sessions started in, `found_under`; one found only from the work
                      sessions did, `found_from_work`; `authors_here`) and
                      own_artifacts.own (skills, agents, commands: whose they are was decided by ownership.py)
  anatomy.json        instruction_files (path, kind, scope, origin = ownership.py's verdict) and memory_stores
Only files marked `own` go in. Their text is then read from disk and the pointers in it are followed.
Standard library only; read-only; a file that is missing, unreadable or over 300 KB gets an "error" and the run goes on.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

MAX_BYTES = 300_000
DOC_EXT = (".md", ".txt", ".mdc")
OUTLINE_DEPTH, OUTLINE_MAX = 3, 40
FOLLOW_DEPTH = 3     # pointer layers followed below a loaded file
IMPORT_HOPS = 5      # Claude Code stops following @imports after five hops
INDEX_MIN = 3        # a file that links to this many other notes of its store is an index
STORE_FILES_CAP = 2000
LISTED = 150  # files per load kind and project in the JSON; the totals always count every file
MD_WORDS = 6000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".obsidian", ".venv", "venv"}

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
MD_LINK = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^)]*[\"'])?\s*\)")
IMPORT = re.compile(r"(?<![\w@/.\-])@(~/[^\s`'\"<>()\[\]]+|\.{1,2}/[^\s`'\"<>()\[\]]+|/[^\s`'\"<>()\[\]]+"
                    r"|[\w.\-]+/[^\s`'\"<>()\[\]]+|[\w\-]+\.[A-Za-z0-9]{1,5}(?![\w]))")
TOKEN = re.compile(r"[\w.~/$\-{}]+")
WIKILINK = re.compile(r"\[\[([^\]|#\n]+)(?:[#|][^\]\n]*)?\]\]")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\[@(\"'])")
CODE_SPAN = re.compile(r"`[^`\n]*`")
KEY = re.compile(r"^([A-Za-z_][\w.\-]*):\s*(.*)$")


def words(text):
    return len(text.split())


# ------------------------------------------------------------------ secrets
# Every piece of TEXT this step copies out of a person's file (a heading, the sentence around a pointer, a frontmatter
# value) lands in a brief an AI reader gets and on the page, so it goes through the file inventory's secret scrubber
# first (2026-10-27-anatomy/inventory.py `scrub`: known token shapes, long random runs, KEY=value for secret-looking
# keys), the one 2026-11-02-evidence/inventory.py uses for free text. Why: on the first real run (11-11) a reader
# found session tokens in plain text in two agent-facing files tracked by git; nothing here stopped such a line from
# being copied. Paths are not scrubbed: the scrubber already leaves path-like tokens alone, and a masked path is no path.
ANATOMY_INV = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "2026-10-27-anatomy",
                                            "inventory.py"))   # scripts/ keeps labs' layout
_SCRUB = None


def _scrubber():
    global _SCRUB
    if _SCRUB is None:
        name = "setup_read_anatomy_inv"
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(name, ANATOMY_INV)
            if spec is None or spec.loader is None:
                raise ImportError(ANATOMY_INV)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod          # its dataclasses look their own module up by name
            spec.loader.exec_module(mod)
            _SCRUB = mod.scrub
        except Exception:  # noqa: BLE001 - never write unscrubbed text: without the scrubber, mask every long run
            sys.modules.pop(name, None)
            _SCRUB = lambda t, limit=240: re.sub(r"[A-Za-z0-9+/_-]{24,}", "<redacted>", t)[:limit]  # noqa: E731
    return _SCRUB


def scrub(text, limit=100_000):
    """`text` with anything secret-looking replaced by <redacted>, on one line, cut at `limit`."""
    return _scrubber()(str(text if text is not None else ""), limit)


def has_secret(text):
    """True when the scrubber would change `text`: a quote from such a line is dropped, never shown masked."""
    flat = str(text if text is not None else "").replace("\n", " ").replace("\r", " ")
    return scrub(flat, len(flat) + 100) != flat


def read(path):
    """(text, error). Never raises."""
    try:
        if not os.path.isfile(path):
            return None, "missing"
        if os.path.getsize(path) > MAX_BYTES:
            return None, "over 300 KB, skipped"
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(), None
    except OSError as e:
        return None, f"unreadable: {e.strerror or e}"


class Ctx:
    """One laptop: where home is, and the answers that never change during a run."""

    def __init__(self, home):
        self.home = os.path.normpath(home)
        self.claude = os.path.join(self.home, ".claude")
        self._root = {}

    def tilde(self, p):
        p = os.path.normpath(p)
        return "~" + p[len(self.home):] if p == self.home or p.startswith(self.home + os.sep) else p

    def repo_root(self, path):
        """The git root a folder belongs to, read from the files (a linked worktree resolves to its main checkout).
        HOME itself is never a root: a home that is a git repo would make every folder below it one project."""
        q = os.path.normpath(path)
        walked = []
        while q and q not in ("/", self.home):
            if q in self._root:
                res = self._root[q]
                break
            walked.append(q)
            g = os.path.join(q, ".git")
            if os.path.isdir(g):
                res = q
                break
            if os.path.isfile(g):
                res = q
                try:
                    with open(g, encoding="utf-8", errors="replace") as f:
                        t = f.read().split("gitdir:")[-1].strip()
                    t = t if os.path.isabs(t) else os.path.normpath(os.path.join(q, t))
                    if "/.git/" in t:
                        res = t.split("/.git/")[0]
                except OSError:
                    pass
                break
            up = os.path.dirname(q)
            q = up if up != q else None   # a root ("/", "//", "C:\\") is its own parent: stop there
        else:
            res = None
        for w in walked:
            self._root[w] = res
        return res


# ------------------------------------------------------------------ text structure

def frontmatter(lines):
    """({key: str | list}, index of the first body line). Top-level keys only; nested values are kept as text."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    end = next((i for i in range(1, min(len(lines), 300)) if lines[i].strip() in ("---", "...")), None)
    if end is None:
        return {}, 0
    out, key = {}, None
    for ln in lines[1:end]:
        m = KEY.match(ln) if ln[:1] not in (" ", "\t", "-") else None
        if m:
            key = m.group(1)
            v = m.group(2).strip()
            out[key] = [] if v == "" else ("" if v in (">", "|", ">-", "|-", ">+", "|+") else v)
        elif key is not None and ln.strip():
            item = re.match(r"^\s*-\s+(.*)$", ln)
            cur = out[key]
            if item and isinstance(cur, list):
                cur.append(item.group(1).strip())
            elif isinstance(cur, list):
                out[key] = [" ".join([*cur, ln.strip()])]
            else:
                out[key] = (cur + " " + ln.strip()).strip()
    return out, end + 1


def top_level_split(s):
    """Split on commas that are not inside brackets or parentheses (a tool entry can hold commas)."""
    parts, depth, cur = [], 0, ""
    for ch in s:
        depth += ch in "([{"
        depth -= ch in ")]}"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return [p.strip().strip("\"'") for p in [*parts, cur] if p.strip()]


def as_list(v):
    if isinstance(v, list):
        return [x.strip().strip("\"'") for x in v if x.strip()]
    v = (v or "").strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    return top_level_split(v)


def headings(lines, start=0):
    """[(level, title, line_index)] outside code fences."""
    out, fence = [], False
    for i in range(start, len(lines)):
        if FENCE.match(lines[i]):
            fence = not fence
            continue
        m = None if fence else HEADING.match(lines[i])
        if m:
            out.append((len(m.group(1)), m.group(2).strip(), i))
    return out


def outline(lines, start=0):
    """The heading tree to depth 3, at most 40 headings: [{level, title, words}] and how many were cut.
    words = the words under the heading up to the next heading of the same or a higher level."""
    hs = headings(lines, start)
    items = []
    for k, (lvl, title, i) in enumerate(hs):
        end = next((hs[j][2] for j in range(k + 1, len(hs)) if hs[j][0] <= lvl), len(lines))
        items.append({"level": lvl, "title": scrub(title)[:80], "words": words(" ".join(lines[i + 1:end]))})
    shown = [x for x in items if x["level"] <= OUTLINE_DEPTH]
    return shown[:OUTLINE_MAX], len(shown) - len(shown[:OUTLINE_MAX])


def sentence_at(line, pos):
    """The sentence of one line that holds column pos, markdown bullet and heading marks removed, at most 200 chars."""
    m = re.match(r"^\s*(?:[-*+>]\s+|\d+[.)]\s+|#+\s+)*", line)
    lead = m.end() if m else 0
    text = line[lead:]
    pos = max(0, pos - lead)
    start = 0
    for m in SENTENCE_END.finditer(text):
        if m.end() > pos:
            text = text[start:m.start() + 1]
            break
        start = m.end()
    else:
        text = text[start:]
    text = scrub(" ".join(text.split()))
    return text if len(text) <= 200 else text[:199] + "…"


def resolve(raw, base_dir, roots, ctx):
    """Absolute path a pointer names, tried from the file's folder, then from each root; None when nothing is there."""
    raw = raw.rstrip(".,;:!?)\"'")
    if not raw:
        return None, None
    cands = []
    if raw.startswith("~/"):
        cands.append(os.path.join(ctx.home, raw[2:]))
    elif os.path.isabs(raw):
        cands.append(raw)
    else:
        cands += [os.path.join(base_dir, raw), *[os.path.join(r, raw) for r in roots]]
    cands = [os.path.normpath(c) for c in cands]
    for c in cands:
        if os.path.isfile(c):
            return c, True
    return cands[0], False


def inside(path, roots):
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def pointers(lines, start, path, roots, ctx, display):
    """Every link, @import and file path in the text that names a file inside `roots`. A link or @import to a file
    that is not there is kept with exists=false (a dead pointer is structure); a bare path counts only when it exists."""
    out, seen, fence = [], set(), False
    base = os.path.dirname(path)
    for i in range(start, len(lines)):
        line = lines[i]
        if FENCE.match(line):
            fence = not fence
            continue
        if fence or not line.strip():
            continue
        found = []   # (column, raw, kind)
        for m in MD_LINK.finditer(line):
            t = m.group(1).split("#")[0]
            if t and not re.match(r"^[a-zA-Z][\w+.\-]*:", t):
                found.append((m.start(1), t.replace("%20", " "), "link"))
        masked = CODE_SPAN.sub(lambda m: " " * len(m.group(0)), line)
        for m in IMPORT.finditer(masked):
            found.append((m.start(1), m.group(1), "import"))
        for m in TOKEN.finditer(line):
            tok = m.group(0).strip(".,;:")
            if ("/" in tok or re.search(r"\.[A-Za-z0-9]{1,5}$", tok)) and not tok.startswith(("http", "//")) and "://" not in line[max(0, m.start() - 8):m.start() + 1]:
                found.append((m.start(), tok, "path"))
        for col, raw, kind in found:
            target, exists = resolve(raw, base, roots, ctx)
            if target is None or not inside(target, roots) or (exists is False and kind == "path"):
                continue
            if exists and os.path.isdir(target) or target == path:
                continue
            key = (target, i)
            if key in seen:
                continue
            seen.add(key)
            out.append({"target": display(target), "line": i + 1, "sentence": sentence_at(line, col), "exists": bool(exists),
                        "kind": kind, "_abs": target})
    return out


def file_facts(path, roots, ctx, display):
    """Everything one markdown file contributes: words, frontmatter, outline, pointers. Never raises."""
    text, err = read(path)
    e = {"path": display(path)}
    if err or text is None:
        e["error"] = err or "unreadable"
        return e, [], {}
    lines = text.split("\n")
    fm, body = frontmatter(lines)
    ol, more = outline(lines, body)
    e.update({"words": words(text), "outline": ol, **({"outline_more": more} if more else {}),
              "pointers": pointers(lines, body, path, roots, ctx, display)})
    return e, lines, fm


# ------------------------------------------------------------------ instruction files

def load_instructions(seeds, roots, ctx, display):
    """seeds: [(path, loads, scope)] -> entries and totals.
    always / scoped files that @import another doc load with it (same kind of load, up to five hops); every other
    pointer to a doc is on_demand, followed three layers down, each doc recorded once."""
    entries, order, loaded = {}, [], []

    def add(path, loads, depth, **extra):
        e = file_facts(path, roots, ctx, display)[0]
        e.update(loads=loads, depth=depth, **extra)
        entries[path] = e
        order.append(path)
        return e

    for path, loads, scope in seeds:
        path = os.path.normpath(path)
        if path in entries:
            continue
        add(path, loads, 0, **({"scoped_to": scope} if scope else {}))
        loaded.append(path)
    # @import chains
    i = 0
    while i < len(loaded):
        p = loaded[i]
        i += 1
        parent = entries[p]
        if parent.get("_import_hops", 0) >= IMPORT_HOPS:
            continue
        for ptr in parent.get("pointers", []):
            t = ptr["_abs"]
            if ptr["kind"] == "import" and ptr["exists"] and t.endswith(DOC_EXT) and t not in entries:
                e = add(t, parent["loads"], parent["depth"] + 1, reached_from=parent["path"], via="import",
                        **({"scoped_to": parent["scoped_to"]} if "scoped_to" in parent else {}))
                e["_import_hops"] = parent.get("_import_hops", 0) + 1
                loaded.append(t)
    # on demand
    frontier = [(p, 0) for p in loaded]
    while frontier:
        nxt = []
        for p, hops in frontier:
            if hops >= FOLLOW_DEPTH:
                continue
            parent = entries[p]
            for ptr in parent.get("pointers", []):
                t = ptr["_abs"]
                if not ptr["exists"] or not t.endswith(DOC_EXT) or t in entries:
                    continue
                add(t, "on_demand", parent["depth"] + 1, reached_from=parent["path"])
                nxt.append((t, hops + 1))
        frontier = nxt
    for e in entries.values():
        for ptr in e.get("pointers", []):
            tgt = entries.get(ptr["_abs"])
            if tgt is not None and "words" in tgt:
                ptr["words"] = tgt["words"]
    result = []
    for p in order:
        e = entries[p]
        for ptr in e.get("pointers", []):
            ptr.pop("_abs", None)
        e.pop("_import_hops", None)
        result.append(e)
    return result


def totals(entries):
    t = {"files": len(entries), "always_words": 0, "scoped_words": 0, "on_demand_words": 0,
         "always_files": 0, "scoped_files": 0, "on_demand_files": 0, "max_depth": 0}
    for e in entries:
        t[e["loads"] + "_files"] += 1
        t[e["loads"] + "_words"] += e.get("words", 0)
        t["max_depth"] = max(t["max_depth"], e.get("depth", 0))
    return t


def rule_scope(fm):
    if "paths" in fm:
        g = as_list(fm["paths"])
        return g or None
    return None


def rules_seeds(rules_dir):
    out = []
    for d, dirs, files in os.walk(rules_dir):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            if f.endswith((".md", ".mdc")):
                p = os.path.join(d, f)
                text = read(p)[0]
                globs = None
                if text:
                    globs = rule_scope(frontmatter(text.split("\n"))[0])
                out.append((p, "scoped" if globs else "always", {"globs": [scrub(g)[:120] for g in globs]} if globs else None))
    return out


def instruction_seeds(root, files, theirs, rules_dir):
    """Own CLAUDE.md / AGENTS.md / CLAUDE.local.md of a project, and its .claude/rules. The project root's files are
    always loaded for that repo, whatever folder sessions started in; a file in a folder below it loads when Claude
    works there."""
    seeds = []
    for p in sorted(files):
        d = os.path.dirname(p)
        if os.path.basename(d) == ".claude" and os.path.basename(p) in ("CLAUDE.md", "CLAUDE.local.md"):
            d = os.path.dirname(d)
        if d == root:
            seeds.append((p, "always", None))
        else:
            seeds.append((p, "scoped", {"folder": os.path.relpath(d, root)}))
    if theirs and os.path.isdir(rules_dir):
        seeds += rules_seeds(rules_dir)
    return seeds


# ------------------------------------------------------------------ memory stores

def link_targets(text, path, notes, by_stem):
    """Links of one note to other notes of its store: [(target path or None)] for markdown links, [[wikilinks]] and
    bare *.md names that are notes. Unresolved markdown links and wikilinks are kept (None); bare names only when they hit."""
    out, fence = [], False
    base = os.path.dirname(path)
    for line in text.split("\n"):
        if FENCE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        for m in MD_LINK.finditer(line):
            t = m.group(1).split("#")[0].replace("%20", " ")
            if t and not re.match(r"^[a-zA-Z][\w+.\-]*:", t):
                p = os.path.normpath(os.path.join(base, t))
                out.append(p if p in notes else None)
        for m in WIKILINK.finditer(line):
            name = m.group(1).strip()
            hit = by_stem.get(name.lower().rsplit("/", 1)[-1].removesuffix(".md"))
            out.append(hit)
        bare = WIKILINK.sub(" ", MD_LINK.sub(" ", line))   # a link's own target is not a second link
        for m in TOKEN.finditer(CODE_SPAN.sub(lambda x: x.group(0).strip("`"), bare)):
            tok = m.group(0)
            if tok.endswith(".md"):
                for cand in (os.path.normpath(os.path.join(base, tok)), os.path.normpath(os.path.join(os.path.dirname(base), tok))):
                    if cand in notes:
                        out.append(cand)
                        break
    return out


def entry_run(lines, start):
    """True when the file is a run of same-level entries: three or more headings of one level (a single leading title
    aside), or, with no headings, three or more top-level bullets."""
    hs = [lvl for lvl, _, _ in headings(lines, start)]
    if len(hs) > 1 and hs[0] == 1 and hs.count(1) == 1:
        hs = hs[1:]
    if hs:
        return len(hs) >= INDEX_MIN and len(set(hs)) == 1
    fence, bullets = False, 0
    for ln in lines[start:]:
        if FENCE.match(ln):
            fence = not fence
        elif not fence and re.match(r"^[-*+]\s+\S", ln):
            bullets += 1
    return bullets >= INDEX_MIN


def collect_notes(path):
    notes, skipped, truncated = [], 0, False
    if os.path.isfile(path):
        return [path], 0, False
    for d, dirs, files in os.walk(path):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        for f in sorted(files):
            if f.endswith(".md"):
                if len(notes) >= STORE_FILES_CAP:
                    return notes, skipped, True
                notes.append(os.path.join(d, f))
    return notes, skipped, truncated


def memory_store(path, ctx, display):
    kind = "builtin" if re.fullmatch(re.escape(os.path.join(ctx.claude, "projects")) + r"/[^/]+/memory", os.path.normpath(path)) else "own"
    res = {"path": display(path), "kind": kind}
    if not os.path.exists(path):
        res["error"] = "missing"
        return res
    paths, _, truncated = collect_notes(path)
    base = path if os.path.isdir(path) else os.path.dirname(path)
    notes = {}
    skipped = 0
    for p in paths:
        text, err = read(p)
        if err:
            skipped += 1
            continue
        notes[os.path.normpath(p)] = text
    res["files"] = len(notes)
    if truncated:
        res["files_capped_at"] = STORE_FILES_CAP
    if skipped:
        res["files_skipped"] = skipped
    if not notes:
        res.update(total_words=0, depth=0, shape="empty", frontmatter_share=0, frontmatter_types={},
                   links_between_notes=0, sub_indexes=[])
        return res
    by_stem = {}
    for p in sorted(notes):
        by_stem.setdefault(os.path.splitext(os.path.basename(p))[0].lower(), p)
    info, fm_count, types = {}, 0, Counter()
    for p, text in notes.items():
        lines = text.split("\n")
        fm, body = frontmatter(lines)
        if fm:
            fm_count += 1
            if "type" in fm and isinstance(fm["type"], str) and fm["type"]:
                types[fm["type"].strip("\"'")[:30]] += 1
        links = link_targets("\n".join(lines[body:]), p, notes, by_stem)
        info[p] = {"lines": lines, "body": body, "words": words(text), "links": links,
                   "hits": {t for t in links if t and t != p}}
    rel_depth = {p: len(os.path.relpath(p, base).split(os.sep)) if os.path.isdir(path) else 1 for p in notes}
    res.update(total_words=sum(i["words"] for i in info.values()), depth=max(rel_depth.values()))
    res["frontmatter_share"] = round(fm_count / len(notes), 2)
    res["frontmatter_types"] = {scrub(k)[:60]: n for k, n in types.most_common(12)}   # a `type:` value is the person's text
    res["links_between_notes"] = sum(len(i["hits"]) for i in info.values())
    cands = sorted((p for p, i in info.items() if len(i["hits"]) >= INDEX_MIN),
                   key=lambda p: (rel_depth[p], -len(info[p]["hits"]), p))
    index = cands[0] if cands else None
    subs = [p for p in cands[1:]]
    res["links_between_notes_excluding_indexes"] = sum(len(i["hits"]) for p, i in info.items() if p not in cands)
    if index:
        ix = info[index]
        ol, more = outline(ix["lines"], ix["body"])
        res["index"] = {"path": display(index), "words": ix["words"], "links": len(ix["links"]),
                        "links_resolved": sum(1 for t in ix["links"] if t), "outline": ol,
                        **({"outline_more": more} if more else {})}
    res["sub_indexes"] = [display(p) for p in subs]
    n = len(notes)
    if index:
        res["shape"] = "layered" if (subs or res["depth"] >= 2 or res["frontmatter_share"] > 0.5) else "indexed"
    elif n == 1:
        only = next(iter(info.values()))
        res["shape"] = "flat-log" if entry_run(only["lines"], only["body"]) else "single-file"
    elif res["links_between_notes"] == 0:
        res["shape"] = "flat-log"
    else:
        res["shape"] = "linked"   # notes link each other but none links to three or more: no index, not a log
    if res["shape"] in ("single-file", "flat-log", "linked"):
        big = max(info, key=lambda p: (info[p]["words"], p))
        ol, more = outline(info[big]["lines"], info[big]["body"])
        res["largest_file"] = {"path": display(big), "words": info[big]["words"], "outline": ol,
                               **({"outline_more": more} if more else {})}
    return res


# ------------------------------------------------------------------ skills, agents, commands

def skill_entry(skill_dir, name, ctx, display):
    skill_md = next((os.path.join(skill_dir, f) for f in ("SKILL.md", "skill.md") if os.path.isfile(os.path.join(skill_dir, f))), None)
    e = {"name": name, "path": display(skill_dir)}
    if not skill_md:
        e["error"] = "no SKILL.md"
        return e
    facts, lines, fm = file_facts(skill_md, [skill_dir], ctx, lambda p: os.path.relpath(p, skill_dir))
    e["path"] = display(skill_md)
    for k in ("words", "outline", "outline_more", "error"):
        if k in facts:
            e[k] = facts[k]
    desc = fm.get("description")
    desc = " ".join(desc) if isinstance(desc, list) else (desc or "")
    e["description_words"] = words(desc)
    e["pointers"] = [{k: v for k, v in p.items() if k != "_abs"} for p in facts.get("pointers", [])]
    counts = {"references": 0, "scripts": 0, "other": 0}
    n = 0
    for d, dirs, files in os.walk(skill_dir):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            n += 1
            if n > 3000:
                break
            if d == skill_dir and f.lower() == "skill.md":
                continue
            top = os.path.relpath(d, skill_dir).split(os.sep)[0]
            counts[top if top in ("references", "scripts") else "other"] += 1
    e["files"] = counts
    return e


def agent_entry(path, name, ctx, display):
    facts, lines, fm = file_facts(path, [], ctx, display)
    e = {"name": name, "path": facts["path"]}
    for k in ("words", "outline", "outline_more", "error"):
        if k in facts:
            e[k] = facts[k]
    e["frontmatter_keys"] = list(fm.keys())
    if "model" in fm and isinstance(fm["model"], str):
        e["model"] = scrub(fm["model"].strip("\"'"))[:60]
    if "tools" in fm:
        e["tools_count"] = len(as_list(fm["tools"]))
    return e


# ------------------------------------------------------------------ discovery

def load_json(run, name):
    try:
        with open(os.path.join(run, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def pick_projects(run, ctx, n, inv, anat, scan):
    """[(root, {typed_sessions, ...})]: anatomy_scan's pick; when it is missing, the same count from chats.jsonl; sessions
    started in a folder above the repos (a start folder that is not a repo) leave the rest to the repos the inventory
    found below it; a session started in HOME names no repo, so the repos it worked in (worked-in.json: files it edited,
    commits it made, wherever it started) rank by sessions, and only then the repos whose own root file was edited last
    (those carry `picked_by: "rule-files"`: the pages say "busiest" only for a list ranked by sessions or work)."""
    picked = []
    if scan and scan.get("projects"):
        picked = [(p["root"], {k: p[k] for k in ("typed_sessions", "sessions") if k in p}) for p in scan["projects"]]
    else:
        counts, home_start = Counter(), False
        worked = (load_json(run, "worked-in.json") or {}).get("sessions") or {}
        try:
            with open(os.path.join(run, "chats.jsonl"), encoding="utf-8") as f:
                f.readline()
                for line in f:
                    try:
                        c = json.loads(line)
                    except ValueError:
                        continue
                    cwd = c.get("cwd")
                    if cwd and os.path.normpath(cwd) == ctx.home:
                        home_start = True
                    r = ctx.repo_root(cwd) if cwd and os.path.isdir(cwd) else None
                    if r and c.get("ep") == "cli":
                        counts[r] += 1
                    if c.get("ep") == "cli":
                        for w in worked.get(c.get("chat"), {}):
                            if w != r and os.path.isdir(w) and ctx.repo_root(w) == w:
                                counts[w] += 1
        except OSError:
            pass
        picked = [(r, {"typed_sessions": k}) for r, k in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]   # a tie ends by path, not by read order
    picked = [(r, m) for r, m in picked if os.path.isdir(r)][:n]
    have = {r for r, _ in picked}
    for row in sorted((inv or {}).get("repos") or [], key=lambda x: (-(x.get("worked_chats") or 0), str(x.get("repo") or ""))):
        r = row.get("repo")
        if len(picked) >= n:
            break
        if not r or r in have or not os.path.isdir(r) or row.get("not_a_repo"):
            continue
        if "found_under" in row:
            picked.append((r, {"found_under": ctx.tilde(row["found_under"])}))
            have.add(r)
        elif row.get("found_from_work") and row.get("worked_chats"):
            picked.append((r, {"typed_sessions": row["worked_chats"]}))
            have.add(r)
    if len(picked) < n and anat:
        # newest root file first: the only order that needs no word list or threshold
        roots = {}
        for x in anat.get("instruction_files") or []:
            p, proj = x.get("path", ""), x.get("project")
            if x.get("scope") == "project" and x.get("origin") == "own" and not x.get("in_fixture_path") and proj \
                    and os.path.dirname(p) == proj and proj not in have and ctx.repo_root(proj) == proj:
                roots[proj] = max(roots.get(proj, ""), x.get("mtime") or "")
        if _home_start(run, ctx):
            for r, _ in sorted(sorted(roots.items()), key=lambda kv: kv[1], reverse=True)[:n - len(picked)]:   # newest first; equal times keep path order (reverse keeps ties stable)
                picked.append((r, {"found_under": "~", "picked_by": "rule-files"}))   # not ranked by sessions: the pages say so
    return picked


def _home_start(run, ctx):
    try:
        with open(os.path.join(run, "chats.jsonl"), encoding="utf-8") as f:
            f.readline()
            for line in f:
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd and os.path.normpath(cwd) == ctx.home:
                    return True
    except OSError:
        pass
    return False


def claude_slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def build(run_dir, projects=3, home=None):
    ctx = Ctx(home or os.path.expanduser("~"))
    inv = load_json(run_dir, "inventory.json") or {}
    anat = load_json(run_dir, "anatomy.json") or {}
    scan = load_json(run_dir, "anatomy-scan.json")
    read_files = [f for f, d in (("inventory.json", inv), ("anatomy.json", anat), ("anatomy-scan.json", scan)) if d]
    picked = pick_projects(run_dir, ctx, projects, inv, anat, scan)
    roots = {os.path.normpath(r): m for r, m in picked}
    repo_rows = {r.get("repo"): r for r in inv.get("repos") or []}

    def owner(path):
        """The mapped place a file belongs to: its git root when that is one of the projects, '~/.claude' for the
        user level; None when it belongs to another repo (a nested one, a worktree, someone else's clone)."""
        path = os.path.normpath(path)
        if path.startswith(ctx.claude + os.sep):
            return "user"
        if "/.claude/worktrees/" in path:
            return None
        g = ctx.repo_root(os.path.dirname(path))
        if g is None:
            return next((r for r in sorted(roots, key=len, reverse=True) if path.startswith(r + os.sep)), None)
        return g if g in roots else None

    # discovery from the inventory
    instr = {r: [] for r in [*roots, "user"]}
    for x in anat.get("instruction_files") or []:
        p = x.get("path")
        if not p or x.get("origin") != "own" or x.get("in_fixture_path") or x.get("kind") not in ("claude", "claude_local", "agents"):
            continue
        p = os.path.normpath(p)
        if x.get("scope") == "home":
            if p == os.path.join(ctx.home, "CLAUDE.md") or p.startswith(ctx.claude + os.sep):
                instr["user"].append(p)
            continue
        o = owner(p)
        if o and o != "user":
            instr[o].append(p)
    arts = {r: {"skill": [], "agent": [], "command": []} for r in [*roots, "user"]}
    seen_art = set()
    for a in (inv.get("own_artifacts") or {}).get("own") or []:
        if a.get("kind") in ("skill", "agent", "command") and a.get("path"):
            real = os.path.realpath(a["path"])
            o = owner(a["path"])
            if o and (real, a["kind"]) not in seen_art:
                seen_art.add((real, a["kind"]))
                arts[o][a["kind"]].append(a)
    stores = {r: [] for r in [*roots, "user"]}
    for m in anat.get("memory_stores") or []:
        p = m.get("path")
        if not p or m.get("in_fixture_path") or "/skills/" in p:
            continue
        p = os.path.normpath(p)
        if p.startswith(os.path.join(ctx.claude, "projects") + os.sep):
            continue   # Claude Code's own memory folders: matched to a project by name below, whatever the inventory listed
        o = owner(p)
        if o is None and m.get("scope") == "home":
            o = "user"
        if o:
            stores[o].append(p)
    # Claude Code's own memory folder per project: found on disk, whatever the inventory listed
    for r in [*roots, ctx.home]:
        b = os.path.join(ctx.claude, "projects", claude_slug(r), "memory")
        if os.path.isdir(b):
            stores["user" if r == ctx.home else r].append(b)

    def place(key, root, claude_level):
        display = (lambda p: os.path.relpath(p, root) if inside(os.path.normpath(p), [root]) else ctx.tilde(p))
        if claude_level:
            seeds = [(p, "always", None) for p in sorted(set(instr[key]))] + rules_seeds(os.path.join(ctx.claude, "rules"))
        else:
            # a repo the inventory says is someone else's contributes no rule files (they carry no ownership mark)
            theirs = repo_rows.get(root, {}).get("authors_here") is not False
            seeds = instruction_seeds(root, instr[key], theirs, os.path.join(root, ".claude", "rules"))
        ptr_roots = [root] if claude_level else [root, ctx.claude]
        out = {}
        try:
            ents = load_instructions(seeds, ptr_roots, ctx, display)
        except Exception as e:  # noqa: BLE001 - the map goes on without this part
            ents, out["error"] = [], f"instruction files: {e}"
        tot = totals(ents)
        # the totals count every file; the list keeps the first (shallowest, then biggest) of the long kinds
        keep, not_listed = [], {}
        for kind in ("always", "scoped", "on_demand"):
            group = sorted((e for e in ents if e["loads"] == kind), key=lambda e: (e["depth"], -e.get("words", 0), e["path"]))
            keep += group[:LISTED]
            if len(group) > LISTED:
                not_listed[kind] = len(group) - LISTED
        out["instruction_files"] = keep
        if not_listed:
            out["not_listed"] = not_listed
        out["totals"] = tot
        out["memory"] = [memory_store(p, ctx, display) for p in dict.fromkeys(stores[key])]
        out["memory"].sort(key=lambda s: (s["kind"] != "builtin", -(s.get("total_words") or 0), s["path"]))
        out["skills"] = [skill_entry(a["path"], a.get("name") or os.path.basename(a["path"]), ctx, display)
                         for a in sorted(arts[key]["skill"], key=lambda a: a["path"])]
        out["agents"] = [agent_entry(a["path"], a.get("name") or os.path.basename(a["path"]), ctx, display)
                         for a in sorted(arts[key]["agent"], key=lambda a: a["path"])]
        out["commands"] = [agent_entry(a["path"], a.get("name") or os.path.basename(a["path"]), ctx, display)
                           for a in sorted(arts[key]["command"], key=lambda a: a["path"])]
        return out

    result = {"schema": "setup-map/1", "home": "~", "read": read_files, "projects": []}
    for r, meta in roots.items():
        try:
            p = {"root": ctx.tilde(r), **meta, **place(r, r, False)}
        except Exception as e:  # noqa: BLE001
            p = {"root": ctx.tilde(r), **meta, "error": str(e)}
        result["projects"].append(p)
    try:
        result["user"] = {"root": "~/.claude", **place("user", ctx.claude, True)}
    except Exception as e:  # noqa: BLE001
        result["user"] = {"root": "~/.claude", "error": str(e)}
    return result


# ------------------------------------------------------------------ the markdown tree

LEVELS = [  # (outline lines per file, pointers per file, files per section, memory outline, skills/agents listed)
    (10, 5, 40, 10, 30), (6, 3, 25, 6, 20), (4, 2, 15, 4, 12), (2, 1, 10, 2, 8), (0, 0, 6, 0, 4),
]


def render(m, cap):
    ol, pt, fs, mo, sk = cap
    L = []

    def outline_lines(items, more, ind, n):
        shown = items[:n]
        for h in shown:
            L.append(f"{ind}{'  ' * (h['level'] - 1)}{'#' * h['level']} {h['title']} ({h['words']}w)")
        cut = len(items) - len(shown) + (more or 0)
        if cut > 0 and n:
            L.append(f"{ind}… {cut} more")

    def section(title, entries, line_of):
        if not entries:
            return
        L.append(f"### {title}")
        for e in entries[:fs]:
            line_of(e)
        if len(entries) > fs:
            L.append(f"- … {len(entries) - fs} more")

    def file_line(e):
        loads = e["loads"]
        tag = ""
        if "scoped_to" in e:
            s = e["scoped_to"]
            tag = f" [folder {s['folder']}]" if "folder" in s else f" [paths {', '.join(s['globs'][:4])}]"
        if loads == "on_demand":
            tag = f" ← {e['reached_from']}, depth {e['depth']}"
        elif e.get("via") == "import":
            tag = f"{tag} ← @import from {e['reached_from']}, depth {e['depth']}"
        if "error" in e:
            L.append(f"- {e['path']}{tag}: {e['error']}")
            return
        L.append(f"- {e['path']} {e['words']}w{tag}")
        # the reader opens loaded files first: on-demand docs get a thinner sketch
        o_n, p_n = (min(ol, 3), min(pt, 1)) if loads == "on_demand" else (ol, pt)
        outline_lines(e.get("outline", []), e.get("outline_more"), "    ", o_n)
        ps = e.get("pointers", [])
        pt_ = p_n
        for p in ps[:pt_]:
            w = f" ({p['words']}w)" if "words" in p else ("" if p["exists"] else " (missing)")
            L.append(f"    → {p['target']}{w} line {p['line']}: \"{p['sentence']}\"")
        if len(ps) > pt_ and pt_:
            L.append(f"    … {len(ps) - pt_} more pointers")

    def memory_line(s):
        if "error" in s:
            L.append(f"- {s['path']} ({s['kind']}): {s['error']}")
            return
        head = (f"- {s['path']} ({s['kind']}) {s['shape']}: {s['files']} files, {s['total_words']}w, depth {s['depth']}, "
                f"frontmatter {int(s['frontmatter_share'] * 100)}%, {s['links_between_notes']} links between notes")
        if s.get("frontmatter_types"):
            head += ", types " + ", ".join(f"{k} {v}" for k, v in list(s["frontmatter_types"].items())[:6])
        L.append(head)
        if "index" in s:
            ix = s["index"]
            L.append(f"    index {ix['path']} {ix['words']}w, {ix['links_resolved']} of {ix['links']} links resolve"
                     + (f", sub-indexes {len(s['sub_indexes'])}" if s["sub_indexes"] else ""))
            outline_lines(ix["outline"], ix.get("outline_more"), "      ", mo)
        elif "largest_file" in s:
            lf = s["largest_file"]
            L.append(f"    largest {lf['path']} {lf['words']}w")
            outline_lines(lf["outline"], lf.get("outline_more"), "      ", mo)

    def skill_line(s):
        if "error" in s:
            L.append(f"- {s['name']}: {s['error']}")
            return
        f = s["files"]
        L.append(f"- {s['name']} {s['words']}w, description {s['description_words']}w, references {f['references']}, "
                 f"scripts {f['scripts']}, other {f['other']}")
        outline_lines(s.get("outline", []), s.get("outline_more"), "    ", ol)
        ps = s.get("pointers", [])
        for p in ps[:pt]:
            L.append(f"    → {p['target']}{'' if p['exists'] else ' (missing)'} line {p['line']}: \"{p['sentence']}\"")
        if len(ps) > pt and pt:
            L.append(f"    … {len(ps) - pt} more pointers")

    def agent_line(a):
        if "error" in a:
            L.append(f"- {a['name']}: {a['error']}")
            return
        extra = (f", model {a['model']}" if "model" in a else "") + (f", {a['tools_count']} tools" if "tools_count" in a else "")
        L.append(f"- {a['name']} {a['words']}w, keys {', '.join(a['frontmatter_keys']) or 'none'}{extra}")
        outline_lines(a.get("outline", []), a.get("outline_more"), "    ", min(ol, 4))

    L.append("# Setup map\n")
    L.append("Structure of one person's agent-facing files, drawn by code with no judgment. `w` = words. Always loaded = "
             "read at the start of every session; Scoped = a rule file below the project root or with `paths:`; On demand = "
             "a doc reached by a pointer. Pointers are `→ target (words) line N: \"sentence\"`.\n")
    for p in [*m["projects"], m["user"]]:
        label = "User level" if p["root"] == "~/.claude" else "Project"
        L.append(f"## {label}: {p['root']}")
        if "error" in p and "instruction_files" not in p:
            L.append(f"error: {p['error']}\n")
            continue
        t = p["totals"]
        L.append(f"{t['files']} instruction files; always {t['always_words']}w, scoped {t['scoped_words']}w, "
                 f"on demand {t['on_demand_words']}w; deepest pointer layer {t['max_depth']}"
                 + (f"; typed sessions {p['typed_sessions']}" if "typed_sessions" in p else "") + "\n")
        for title, kind in (("Always loaded", "always"), ("Scoped", "scoped"), ("On demand", "on_demand")):
            section(title, [e for e in p["instruction_files"] if e["loads"] == kind], file_line)
        if p.get("not_listed"):
            L.append("- not listed, but counted in the totals: " + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in p["not_listed"].items()))
        section("Memory", p["memory"], memory_line)
        section("Skills", p["skills"][:sk], skill_line)
        if len(p["skills"]) > sk:
            L.append(f"- … {len(p['skills']) - sk} more skills")
        ac = [*p["agents"], *[{**c, "name": "/" + c["name"].lstrip("/")} for c in p["commands"]]]
        section("Agents and commands", ac[:sk], agent_line)
        if len(ac) > sk:
            L.append(f"- … {len(ac) - sk} more")
        L.append("")
    return "\n".join(L)


def to_markdown(m):
    text = ""
    for cap in LEVELS:
        text = render(m, cap)
        if words(text) <= MD_WORDS:
            break
    return text


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--projects", type=int, default=3)
    a = ap.parse_args(argv)
    run = os.path.abspath(os.path.expanduser(a.run_dir))
    m = build(run, a.projects)
    os.makedirs(run, exist_ok=True)
    with open(os.path.join(run, "setup-map.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1, ensure_ascii=False)
    md = to_markdown(m)
    with open(os.path.join(run, "setup-map.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
    t = [p["totals"] for p in [*m["projects"], m["user"]] if "totals" in p]
    print(f"setup-map: {len(m['projects'])} projects + user level, {sum(x['files'] for x in t)} instruction files, "
          f"{words(md)} words in setup-map.md")


if __name__ == "__main__":
    main(sys.argv[1:])
