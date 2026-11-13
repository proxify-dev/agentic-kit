#!/usr/bin/env python3
"""What counts as a person's memory: three questions, asked in this order, one function each. Standard library only.

A folder is found as a candidate by its name (the scan's MEM_NAME), a file called MEMORY.md, a notes vault or a folder an
instruction file names. The name only finds candidates; it never proves anything. Each candidate then gets:

  1. shape_of(path)       Is it memory? Decided by what is in it. Memory when
                            - a markdown file at the top of the folder (an index; MEMORY.md is the usual name) links to at
                              least INDEX_LINKS_MIN other markdown files inside the folder, with markdown links or [[wikilinks]]; or
                            - the folder holds at least NOTES_MIN markdown notes, and at least NOTES_LINKING_MIN of them
                              link to another note inside it.
                          A skill's folder (it holds a SKILL.md) is never a store: its markdown is the skill's references.
                          Anything else is shape "none", kept in the output with its reason and never listed or counted.
                          A lone MEMORY.md file is judged by the first rule only, over its own folder: it does not make a
                          code repo's docs a store. Why: a `src/memory/` code module and a folder of `memory` fixtures read as
                          memory by name (11-13, 0.6.8); a notes store under any other name read as none.
  2. origin_of(...)       Is it theirs? Yes unless proven otherwise, by the one ownership rule (ownership.py, through the
                          scan's classify_origin): someone else's only when git shows the repo's commits are mostly other
                          people's, none of the commits that touched the folder is theirs, and none of their sessions wrote a
                          file in it (the proof ownership.py's own skill rules use: written_in_logs). An installed copy
                          (a plugin's release folder, a second checkout) of a store they already have counts once, as the
                          original: mark_copies().
  3. store_use(...)       Do they use it? Reads and writes of a file inside the store, by their sessions. A read of a parent
                          folder is no read of the store. Sessions and sub-agents started by this tool (own_tool_run) are
                          left out. A file inside a store that sits in another store is counted for the inner one, so a
                          read is counted once.

Every answer sits on the store with its reason: shape + shape_why, origin + origin_why, copy_of, and the verdict counted +
stopped_at ("shape" | "origin" | "copy" | None), which counts() decides and every reader takes as it is.
Claude Code's own per-project memory (~/.claude/projects/*/memory) is built in, not the person's work (the 11-11 ruling):
it keeps its treatment and is never asked questions 1 and 2.

Test: test_memory_shape.py. Run: python3 -I memory_shape.py FOLDER...   prints each folder's question 1 answer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from urllib.parse import unquote

INDEX_LINKS_MIN = 2      # an index links to at least this many other notes of the store
NOTES_MIN = 3            # a store without an index holds at least this many notes
NOTES_LINKING_MIN = 2    # ... of which at least this many link to another note of it
COPY_SHARE = 0.5         # a store is a copy when more than this share of its notes are byte-identical (same path inside the store) in another
COPY_NOTES_MIN = 2       # ... and it has at least this many notes: one shared README makes no copy
MAX_NOTES = 3000
MAX_NOTE_BYTES = 512_000
WALK_BUDGET_S = 8.0
STORE_PRUNE = {".git", "node_modules", "__pycache__", ".venv", "venv"}
BUILT_IN = "claude_auto_memory"

_FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
_INLINE = re.compile(r"`[^`\n]*`")
_MD_LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^)]*[\"'])?\s*\)")
_REF_LINK = re.compile(r"(?m)^ {0,3}\[[^\]\n]+\]:\s*<?(\S+?)>?(?:\s|$)")
_WIKILINK = re.compile(r"!?\[\[([^\]\n|#][^\]\n|#]*)(?:#[^\]\n|]*)?(?:\|[^\]\n]*)?\]\]")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
_NOTE_EXT = (".md", ".markdown")


def _is_note(name):
    return name.lower().endswith(_NOTE_EXT)


def _targets(text, path, root, by_path, by_rel, by_stem):
    """The other notes of the store this note links to: markdown links and [[wikilinks]], outside code. A set of paths."""
    body = _INLINE.sub("", _FENCE.sub("", text))
    found = set()
    for t in _MD_LINK.findall(body) + _REF_LINK.findall(body):
        t = unquote(t).split("#")[0].split("?")[0].strip()
        if not t or _SCHEME.match(t):
            continue
        base = root if t.startswith("/") else os.path.dirname(path)
        cand = os.path.normpath(os.path.join(base, t.lstrip("/") if t.startswith("/") else t))
        for c in (cand, cand + ".md"):
            hit = by_path.get(c) or by_path.get(c.lower())
            if hit:
                found.add(hit)
                break
    for t in _WIKILINK.findall(body):
        t = t.strip()
        key = re.sub(r"\.(?:md|markdown)$", "", t, flags=re.I).strip("/").lower()
        hit = by_rel.get(key) or by_stem.get(os.path.basename(key))
        if hit:
            found.add(hit)
    found.discard(path)
    return found


def shape_of(path, budget_s=WALK_BUDGET_S):
    """Question 1. {"shape": "index" | "notes" | "none", "why": sentence, "notes": n markdown notes, "index": the index file's
    path inside the store (or None), "linking": notes that link another note, "_notes": {path inside the store: sha1}}.
    `path` is a folder, or a MEMORY.md file (judged by the index rule over its folder)."""
    path = os.path.normpath(os.path.expanduser(str(path)))
    lone = os.path.isfile(path)
    root = os.path.dirname(path) if lone else path
    if os.path.isfile(os.path.join(root, "SKILL.md")):   # a skill folder: its notes are the skill's references, not the person's memory
        return {"shape": "none", "why": "a skill's folder (it holds a SKILL.md): its markdown is the skill's references", "notes": 0,
                "index": None, "linking": 0, "_notes": {}}
    notes, truncated, t_end = [], False, time.monotonic() + budget_s
    for dp, dns, fns in os.walk(root, followlinks=False):
        dns[:] = sorted(d for d in dns if d not in STORE_PRUNE)
        notes += [os.path.join(dp, f) for f in sorted(fns) if _is_note(f)]
        if len(notes) >= MAX_NOTES or time.monotonic() > t_end:
            truncated = True
            break
    return judge(root, notes[:MAX_NOTES], path if lone else None, truncated)


def flat_shape(folder, note_paths):
    """The same rule on one folder's own markdown files only, sub-folders left out: for counting the folders that have the shape
    without being candidates (data only), where a parent must not count for its children. -> True/False."""
    return judge(folder, sorted(note_paths), None, False)["shape"] != "none"


def judge(root, notes, lone_file, truncated):
    """The rule itself, on the notes found under `root`. `lone_file`: the MEMORY.md file the candidate is, or None for a folder."""
    lone = lone_file is not None
    path = lone_file
    by_path, by_rel, by_stem = {}, {}, {}
    for p in notes:
        by_path[p] = p
        by_path.setdefault(p.lower(), p)
        rel = os.path.splitext(os.path.relpath(p, root).replace(os.sep, "/"))[0].lower()
        by_rel.setdefault(rel, p)
        by_stem.setdefault(os.path.basename(rel), p)
    shas, links = {}, {}
    for p in notes:
        try:
            if os.lstat(p).st_size > MAX_NOTE_BYTES:
                continue
            with open(p, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        shas[os.path.relpath(p, root).replace(os.sep, "/")] = hashlib.sha1(raw).hexdigest()
        links[p] = _targets(raw.decode("utf-8", "replace"), p, root, by_path, by_rel, by_stem)
    top = [p for p in notes if os.path.dirname(p) == root and (not lone or p == path)]
    best = max(top, key=lambda p: (len(links.get(p, ())), p), default=None)
    n_links = len(links.get(best, ())) if best else 0
    linking = sum(1 for v in links.values() if v)
    rel_of = lambda p: os.path.relpath(p, root).replace(os.sep, "/")  # noqa: E731
    plural = lambda n, w: f"{n} {w}" + ("" if n == 1 else "s")  # noqa: E731
    out = {"notes": len(notes), "index": rel_of(best) if best and n_links >= INDEX_LINKS_MIN else None, "linking": linking, "_notes": shas,
           **({"truncated": True} if truncated else {})}
    if best and n_links >= INDEX_LINKS_MIN:
        return {**out, "shape": "index", "why": f"{rel_of(best)} at the top links to {n_links} other notes inside it"}
    if not lone and len(notes) >= NOTES_MIN and linking >= NOTES_LINKING_MIN:
        return {**out, "shape": "notes", "why": f"{linking} of its {len(notes)} notes link to another note inside it"}
    if not notes:
        why = "no markdown notes in it"
    elif lone:
        why = f"the file links to {plural(n_links, 'other note')} in its folder; an index links to at least {INDEX_LINKS_MIN}"
    else:
        why = (f"{plural(len(notes), 'note')}; the top-level index links to {plural(n_links, 'other note')} "
               f"(at least {INDEX_LINKS_MIN} needed) and {plural(linking, 'note')} link to another (at least {NOTES_LINKING_MIN} of {NOTES_MIN} or more needed)")
    return {**out, "shape": "none", "why": why}


def asks_questions(rec):
    """Claude Code's own per-project memory is built in: neither question applies to it."""
    return rec.get("kind") != BUILT_IN


def origin_of(rec, classify, wrote_in):
    """Question 2, before copies: (origin, why, proof). origin "own" | "others" | "installed".
    classify(path) -> (origin, evidence): the scan's classify_origin, which is ownership.owner(): "own" | "mixed" (some of
    their commits, most are someone else's) | "third_party" (none is theirs) | "installed".
    wrote_in(folder) -> True when a Write/Edit in their own session logs touched a file under the folder.
    proof: "commits" (their commits changed notes in it), "wrote" (their sessions wrote in it), "default" (nothing says otherwise)."""
    path = rec["path"]
    origin, ev = classify(path)
    if origin == "installed":
        return "installed", ev, None
    if origin == "third_party":
        if wrote_in(path):
            return "own", "their sessions wrote a file in it, though the repo's commits are mostly someone else's", "wrote"
        return "others", ev, None
    if origin == "mixed":
        return "own", "their commits changed notes in it", "commits"
    return "own", ev, "commits" if str(ev).startswith("git:") else "default"


def _share(small, big):
    """Which share of the notes of `small` are in `big` at the same path inside the store with the same bytes."""
    return sum(1 for rel, sha in small.items() if big.get(rel) == sha) / len(small) if small else 0.0


def mark_copies(recs, notes_of, wrote_in, sessions_started=lambda rec: 0):
    """The copy rule of question 2, on stores that have a shape. recs: dicts with path, shape, origin; notes_of(rec) -> {path
    inside the store: sha1}. Sets copy_of (the original's path) on a store whose notes are mostly byte-identical to those of
    a store they have (origin "own"), when that one ranks first: the one their sessions wrote in, then the one with more
    notes (a release copy is older), then the one in the folder more of their sessions started in (sessions_started(rec), larger
    first), then the path. A store that is not theirs is a copy whenever such an original exists
    (a plugin's install folder holding their own store). Returns nothing; counts() reads copy_of."""
    shaped = [r for r in recs if r.get("shape") in ("index", "notes") and r.get("origin")]
    sig = {r["path"]: notes_of(r) for r in shaped}
    rank = lambda r: (not wrote_in(r["path"]), -len(sig[r["path"]]), -sessions_started(r), r["path"])  # noqa: E731
    theirs = sorted((r for r in shaped if r["origin"] == "own"), key=rank)
    for r in sorted(shaped, key=rank):
        mine = sig[r["path"]]
        if len(mine) < COPY_NOTES_MIN:
            continue
        for o in theirs:
            if o is r or (r["origin"] == "own" and rank(o) >= rank(r)):
                continue
            if _share(mine, sig[o["path"]]) > COPY_SHARE:
                r["copy_of"] = o["path"]
                break


def counts(rec):
    """The one verdict every reader takes: (counted, stopped_at). Built-in memory is not asked and stays as it was."""
    if not asks_questions(rec):
        return True, None
    if rec.get("shape") not in ("index", "notes"):
        return False, "shape"
    if rec.get("origin") not in ("own", None) and not rec.get("copy_of"):
        return False, "origin"
    if rec.get("copy_of"):
        return False, "copy"
    return True, None


def settle(rec):
    """Write the verdict onto the store record."""
    rec["counted"], rec["stopped_at"] = counts(rec)
    return rec


def own_tool_run(first_prompt):
    """True when the session or sub-agent was started by this tool: its first message names a brief file the tool writes in its
    run folder (`anatomy-brief-<project>.md`, read by the AI reader of each project), or both files of the practices pass
    (`cheap-practices.md` and `cheap-digest.md`). The tool's AI readers open memory files to judge them; counted, one run
    would inflate the next. Why this cannot catch the person's own work: those names are made by this tool's `run.py pre`; a
    message of theirs holds them only when it asks an agent to read the tool's own briefs, and that agent reads the setup
    tool's files, not their notes. Matched on the first message only: a later mention in a chat is no marker."""
    t = str(first_prompt or "")
    return bool(re.search(r"anatomy-brief-[\w.-]+\.md", t)) or ("cheap-practices.md" in t and "cheap-digest.md" in t)


def _under(path, folder):
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def store_use(stores, reads, writes):
    """Question 3. stores: the paths of the counted stores. reads / writes: {path of a file or folder the agent read / wrote:
    [count, set of sessions]}, from the sessions the person typed in and their sub-agents, never this tool's own (own_tool_run).
    -> {store: {"reads", "sessions_reading", "writes", "sessions_writing"}}. A path counts for the innermost store that holds
    it (equal to the store counts: a search of the whole folder); a path above a store counts for none."""
    ordered = sorted(set(stores), key=lambda s: -len(s))
    out = {s: {"reads": 0, "sessions_reading": set(), "writes": 0, "sessions_writing": set()} for s in ordered}

    def inner(p):
        return next((s for s in ordered if _under(p, s)), None)
    for src, n, who in ((reads, "reads", "sessions_reading"), (writes, "writes", "sessions_writing")):
        for p, (count, chats) in src.items():
            s = inner(p)
            if s:
                out[s][n] += count
                out[s][who] |= set(chats)
    return {s: {**v, "sessions_reading": len(v["sessions_reading"]), "sessions_writing": len(v["sessions_writing"])} for s, v in out.items()}


def normal(path, cwd=""):
    """A path a tool call or a command names, absolute and without `..`, `.` or a trailing slash (`~` is the home folder)."""
    p = os.path.expanduser(str(path))
    if not p.startswith("/"):
        p = os.path.join(cwd or "/", p)
    return os.path.normpath(p)


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        r = shape_of(arg)
        r.pop("_notes", None)
        print(json.dumps({"path": arg, **r}))
