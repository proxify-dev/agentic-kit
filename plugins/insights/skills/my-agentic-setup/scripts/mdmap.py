#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""mdmap — map what an agent can reach from its instructions.

Point it at a folder: a project (roots at CLAUDE.md or AGENTS.md), a skill (roots at
SKILL.md) or a plugin (roots at each skill, agent and command). It follows every
[label](path), [[wikilink]], @import and plain `dir/file.md` path an agent could Read,
and prints the files layer by layer: loaded every turn, loaded when a skill fires, one
Read away. Then a rot report: dead links, orphans, heavy always-loaded files, weak labels.
It prints addresses and sizes, never file bodies.

  mdmap <dir> [--entry FILE] [--rot] [--weak] [--backlinks FILE] [--json]

One folder at a time. Inside one file, grep a heading and Read(offset, limit). stdlib only.
A copy of the vault plugin's mdmap (plugins/vault), carried here so this skill needs no other plugin.
A [[name]] resolves through the vault's link engine (engine/resolve_note.py), the one the Read
hook opens it with. `ak vault map` (and `ak mdmap`) runs it inside the `ak` CLI (vault/cli.py: map).
"""
import argparse
import json
import os
import re
import sys
from collections import deque
from pathlib import Path

try:  # package member: `ak vault map`
    from .engine import resolve_note
except ImportError:  # run as a script — the engine sits in engine/ beside this file
    sys.path.insert(0, str(Path(__file__).resolve().parent / "engine"))
    import resolve_note

MD_LINK = re.compile(r"\[([^\]]*)\]\(\s*<?([^)>\s]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
IMPORT = re.compile(r"(?:^|[\s>(\"'])@((?:~|\.{1,2}/|/)?[A-Za-z0-9_][A-Za-z0-9_./\-]*\.md)")
# A plain path the agent can Read as written. It needs a "/": a bare "CLAUDE.md" in prose
# is a mention of a kind of file, not a pointer to one.
PLAIN_PATH = re.compile(r"(?<![\w/.<>~@:-])((?:\.{1,2}/)?[A-Za-z0-9_][A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.\-]*)+\.md)\b")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
ALWAYS_NAMES = {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md"}
HEAVY_SPINE_TOKENS = 1200      # an always-loaded file above this is depth paid for on every turn
HEAVY_IMPORT_TOKENS = 1200


def est_tokens(nchars):
    return max(1, nchars // 4)


def find_md_files(root):
    """Every .md under ROOT, realpath'd, each once: the engine's walk (resolve_note.iter_md), so the
    map sees what a [[link]] can reach — no dotfolder (version control, the harness dirs `.claude`
    and `.agents`, which load by mechanism, not by md links), no vendored tree, no dotfile. No
    taxonomy of "data" dirs: orphans are grouped by directory, so a dump shows as one line."""
    seen, out = set(), []
    for p in resolve_note.iter_md(root):
        real = os.path.realpath(str(p))
        if real not in seen:  # a symlinked note is the note it points at
            seen.add(real)
            out.append(real)
    return out


def is_external(target):
    t = target.strip()
    if not t or t.startswith("#"):
        return True
    return bool(re.match(r"^[a-z][a-z0-9+.\-]*://", t)) or t.startswith("mailto:") or t.startswith("tel:")


def resolve(target, from_file, root):
    t = target.split("#", 1)[0].strip()
    if not t:
        return None
    if t.startswith("~"):
        t = os.path.expanduser(t)
    if os.path.isabs(t) or t.startswith("/"):  # portable: ok (a link spelling, not an OS path: "/x" is root-relative on Windows too, where it is not absolute)
        # leading-slash links in a vault mean repo-root-relative, not filesystem-root
        cand = os.path.join(root, t.lstrip("/")) if not os.path.exists(t) else t
    else:
        cand = os.path.join(os.path.dirname(from_file), t)
    return os.path.realpath(cand)


def is_wikilink(target):
    """[["next", "stage"]] is a JSON array in prose, not a link."""
    return not re.search(r"[\"'\[]", target)


def read(path):
    try:
        return open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def parse_file(path):
    raw = read(path)
    unfenced = resolve_note.unfenced(raw)
    text = resolve_note.linkable(raw)   # a link/@import inside code is a mention, not a live pointer (the engine's rule)
    pulls = [(m.group(1).strip(), m.group(2).strip())
             for m in MD_LINK.finditer(text) if not is_external(m.group(2))]
    wikis = [t.strip() for t in resolve_note.LINK_RE.findall(text) if is_wikilink(t)]
    pushes = [m.group(1).strip() for m in IMPORT.finditer(text)]
    # plain paths: inline code counts here (`references/x.md` is Read as written); the spans
    # already taken by a link, a wikilink or an @import do not count twice
    bare = MD_LINK.sub(" ", resolve_note.LINK_RE.sub(" ", unfenced.replace("`", " ")))
    plains = []
    for line in bare.splitlines():
        for m in PLAIN_PATH.finditer(line):
            if "://" in line[max(0, m.start() - 12):m.start()]:
                continue
            plains.append((line.strip()[:80], m.group(1)))
    return text, pulls, wikis, pushes, plains


def description(path):
    """A skill's, agent's or command's description: the part of it always in context."""
    fm = FRONTMATTER.match(read(path))
    if not fm:
        return ""
    lines, out, on = fm.group(1).splitlines(), [], False
    for ln in lines:
        if re.match(r"^description\s*:", ln):
            on = True
            out.append(ln.split(":", 1)[1])
        elif on and re.match(r"^[A-Za-z_][\w-]*\s*:", ln):
            break
        elif on:
            out.append(ln)
    return " ".join(s.strip() for s in out).strip().strip("\"'|>").strip()


def is_plugin(root):
    return os.path.exists(os.path.join(root, ".claude-plugin", "plugin.json"))


def fires(path, root, plugin):
    """Loads when it is invoked, not every turn: a SKILL.md, or a plugin's agent or command."""
    if os.path.basename(path) == "SKILL.md":
        return True
    parent = os.path.basename(os.path.dirname(path))
    return plugin and parent in ("agents", "commands") and os.path.dirname(os.path.dirname(path)) == root


def detect_entries(root, override):
    """Where an agent starts. A plugin: every skill, agent and command it ships. A skill:
    its SKILL.md. A project: CLAUDE.md, else AGENTS.md, else .claude/CLAUDE.md."""
    if override:
        p = os.path.realpath(os.path.join(root, override)) if not os.path.isabs(override) else override
        return [p] if os.path.exists(p) else []
    if is_plugin(root):
        found = []
        for sub in ("skills", "agents", "commands"):
            d = os.path.join(root, sub)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                p = os.path.join(d, name, "SKILL.md") if sub == "skills" else os.path.join(d, name)
                if (sub == "skills" and os.path.exists(p)) or (sub != "skills" and name.endswith(".md")):
                    found.append(os.path.realpath(p))
        return found
    for name in ("SKILL.md", "CLAUDE.md", "AGENTS.md"):
        p = os.path.join(root, name)
        if os.path.exists(p):
            return [os.path.realpath(p)]
    p = os.path.join(root, ".claude", "CLAUDE.md")
    return [os.path.realpath(p)] if os.path.exists(p) else []


def vault_purpose(entry):
    """The map shows topology; an agent still needs 'what is this'. Pull the first
    real heading or prose line from the entry — skipping frontmatter, HTML comments,
    system-reminders — so the map states the subject, not just the shape."""
    text = read(entry) if entry else ""
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL)      # frontmatter
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)            # comments
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL | re.IGNORECASE)
    for raw in text.splitlines():
        line = raw.strip().lstrip("#").strip().strip(">").strip()
        line = re.sub(r"[*_`]", "", line)
        if len(line) > 12 and not line.startswith(("@", "|", "-", "<")):
            return line[:160]
    return ""


def build_graph(root, entries):
    files = set(find_md_files(root)) | set(entries)
    # [[name]] resolves across .agents/ too (the memory notes), though .agents/ is not walked as part of the map
    lookup = [Path(f) for f in sorted(files | set(find_md_files(os.path.join(root, ".agents"))))]

    nodes = {}          # path -> {tokens, lines, exists}
    pull = {}           # path -> list[(label, target_path, raw)]
    push = {}           # path -> list[target_path]
    inbound = {}        # path -> set(source)
    dead = []           # (source, label, raw_target)

    def note(p):
        if p not in nodes:
            try:
                sz = os.path.getsize(p)
                lines = sum(1 for _ in open(p, encoding="utf-8", errors="replace"))
            except OSError:
                sz, lines = 0, 0
            nodes[p] = {"tokens": est_tokens(sz), "lines": lines, "exists": os.path.exists(p)}

    for f in sorted(files):
        note(f)
        _text, pulls, wikis, pushes, plains = parse_file(f)
        pl, ph = [], []
        for label, raw in pulls:
            tgt = resolve(raw, f, root)
            if tgt and tgt.endswith(".md"):
                note(tgt)
                if not nodes[tgt]["exists"]:
                    dead.append((f, label, raw))
                    continue
                pl.append((label, tgt, raw))
                inbound.setdefault(tgt, set()).add(f)
        for w in wikis:
            # the read hook's rule, from its own file: exact name, [[a/b]] by path. No guessing —
            # [[memory-tools]] once landed on agent-notes-its-own-tools.md and hid a dead link.
            # [[diagram.png]] is a file, not a note, and [[ ]] names nothing: neither is dead.
            if not resolve_note.clean(w) or resolve_note.is_asset(w):
                continue
            hits = resolve_note.resolve_exact(w, lookup)
            tgt = str(hits[0]) if hits else None
            if tgt:
                note(tgt)
                pl.append((w, tgt, w))
                inbound.setdefault(tgt, set()).add(f)
            else:
                dead.append((f, w, "[[" + w + "]]"))
        for raw in pushes:
            tgt = resolve(raw, f, root)
            if tgt and tgt.endswith(".md"):
                note(tgt)
                if not nodes[tgt]["exists"]:
                    dead.append((f, "@import", raw))
                    continue
                ph.append(tgt)
                inbound.setdefault(tgt, set()).add(f)
        # a plain path that exists is a pointer; one that does not may be an example, so it is not reported
        for line, raw in plains:
            for tgt in (resolve(raw, f, root), os.path.realpath(os.path.join(root, raw))):
                if tgt and tgt != f and os.path.exists(tgt):
                    note(tgt)
                    pl.append((line, tgt, raw))
                    inbound.setdefault(tgt, set()).add(f)
                    break
        pull[f] = pl
        push[f] = ph
    return nodes, pull, push, inbound, dead, files


def classify(root, entries, nodes, pull, push, plugin):
    # always-loaded = the project entry chain + transitive @imports + CLAUDE.md/AGENTS.md at root;
    # on-invoke = skills, agents and commands (their description alone is always in context)
    always, invoke = set(), set()
    seeds = deque()
    if not plugin:    # a plugin's root CLAUDE.md never ships to its users
        for p in list(nodes):
            if os.path.dirname(p) == root and os.path.basename(p) in ALWAYS_NAMES:
                seeds.append(p)
    for e in entries:
        (invoke.add(e) if fires(e, root, plugin) else seeds.append(e))
    while seeds:
        p = seeds.popleft()
        if p in always or p not in nodes:
            continue
        always.add(p)
        for t in push.get(p, []):
            seeds.append(t)

    # reachable: BFS from every entry over pull+push. depth = shortest Reads,
    # parent = the node that first reached it (its shortest route from an entry).
    starts = always | invoke
    depth = {p: 0 for p in starts}
    parent: dict = {p: (None, "") for p in starts}   # p -> (parent_path, edge_label) — the "when to follow" text
    q = deque(sorted(starts))
    while q:
        p = q.popleft()
        for label, t, _raw in pull.get(p, []):
            if t not in depth:
                depth[t] = depth[p] + 1
                parent[t] = (p, label)
                q.append(t)
        for t in push.get(p, []):
            if t not in depth:
                depth[t] = depth[p] + 1
                parent[t] = (p, "@import")
                q.append(t)
    reachable = set(depth)
    agents_dir = os.path.join(root, ".agents") + os.sep    # memory notes: reached by the memory tools, not by links
    orphans = [p for p in nodes if nodes[p]["exists"] and p not in reachable
               and os.path.basename(p) not in ALWAYS_NAMES and not p.startswith(agents_dir)]
    return always, invoke, depth, parent, reachable, orphans


def rel(p, root):
    """p relative to root, "/"-separated on every OS (what an agent reads back and a link spells)."""
    try:
        r = os.path.relpath(p, root)
    except ValueError:  # another drive (Windows)
        r = p
    return Path(r).as_posix()


def weak_pointer(label, target, root):
    """Strict: only a genuinely non-descriptive pointer — empty, or a label that is
    just the file's name/path. A cold reader can't tell WHEN to follow those. Kept
    tight on purpose; a broad heuristic here drowns the real signal in noise."""
    l = label.strip().lower()
    if not l:
        return True
    base = os.path.basename(target).lower()
    return l in (base, base[:-3], rel(target, root).lower())


def render_layers(root, always, invoke, push, depth, parent, nodes, desc_tokens):
    """Shells outward from the entries. A vault is a graph with cycles; shortest-Read
    layers (not a DFS tree) stay readable and mirror how the window fills."""
    lines = []
    push_targets = {t for p in always for t in push.get(p, [])}
    by_depth = {}
    for p, d in depth.items():
        by_depth.setdefault(d, []).append(p)

    def row(p):
        mark = "●" if p in always else "◉" if p in invoke else "○"
        edge = "@" if p in push_targets else "→"
        par, label = parent.get(p, (None, ""))
        if par is not None:
            lbl = (label[:60] + "…") if len(label) > 61 else label
            via = f'   {edge} {rel(par, root)}  "{lbl}"' if lbl and lbl != "@import" else f"   {edge} {rel(par, root)}"
        else:
            via = f"   description ~{desc_tokens[p]}t every turn" if p in invoke else ""
        return f"  {mark} {rel(p, root)}  (~{nodes[p]['tokens']}t){via}"

    for d in sorted(by_depth):
        group = sorted(by_depth[d], key=lambda p: -nodes[p]["tokens"])
        if d == 0:
            a = [p for p in group if p in always]
            i = [p for p in group if p in invoke]
            if a:
                lines.append(f"LAYER 0 · loaded every turn (~{sum(nodes[p]['tokens'] for p in a)}t):")
                lines += [row(p) for p in a]
            if i:
                lines.append(f"LAYER 0 · loaded when it fires (~{sum(nodes[p]['tokens'] for p in i)}t; "
                             f"descriptions ~{sum(desc_tokens[p] for p in i)}t every turn):")
                lines += [row(p) for p in i]
        else:
            lines.append(f"LAYER {d} · {len(group)} file(s), {d} Read(s) from an entry:")
            cap = 30
            lines += [row(p) for p in group[:cap]]
            if len(group) > cap:
                lines.append(f"  … {len(group) - cap} more at this depth (use --json for all)")
        lines.append("")
    return lines


def cli(argv=None):
    ap = argparse.ArgumentParser(prog="mdmap <dir>", description="Map what an agent can reach from its instructions.")
    ap.add_argument("dir", nargs="?", default=".", help="a project, skill or plugin folder, e.g. plugins/agentic-engineering")
    ap.add_argument("--entry", help="start from this file instead, relative to dir, e.g. SKILL.md")
    ap.add_argument("--json", action="store_true", help="print the map as one JSON document")
    ap.add_argument("--rot", action="store_true", help="print the rot report only")
    ap.add_argument("--backlinks", metavar="FILE", help="list the files that point to FILE, relative to dir")
    ap.add_argument("--weak", action="store_true",
                    help="also report weak pointers (empty or filename-only labels); off by default, style-dependent")
    args = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):  # the map prints · ● → ; a Windows console is cp1252
        if (getattr(s, "encoding", None) or "").lower().replace("-", "") != "utf8":
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass

    root = os.path.realpath(args.dir)
    if os.path.isfile(root):
        print("error: mdmap maps a folder. Inside one file: grep a heading, then Read(offset, limit).", file=sys.stderr)
        sys.exit(64)
    if not os.path.isdir(root):
        print(f"error: not a folder: {root}", file=sys.stderr)
        sys.exit(66)
    plugin = is_plugin(root) and not args.entry
    entries = detect_entries(root, args.entry)
    if args.entry and not entries:
        print(f"error: no such entry: {args.entry}", file=sys.stderr)
        sys.exit(66)

    nodes, pull, push, inbound, dead, _files = build_graph(root, entries)
    always, invoke, depth, parent, reachable, orphans = classify(root, entries, nodes, pull, push, plugin)
    desc_tokens = {p: est_tokens(len(description(p))) for p in invoke}

    if args.backlinks:
        tgt = os.path.realpath(os.path.join(root, args.backlinks))
        srcs = sorted(inbound.get(tgt, set()))
        print(f"backlinks → {rel(tgt, root)}  ({len(srcs)} inbound)")
        for s in srcs:
            labels = [lab for lab, t, _ in pull.get(s, []) if t == tgt] or ["@import"]
            print(f"  {rel(s, root)}   \"{labels[0]}\"")
        return

    spine_tokens = sum(nodes[p]["tokens"] for p in always)
    listing_tokens = sum(desc_tokens.values())
    heavy_spine = sorted([p for p in always if nodes[p]["tokens"] > HEAVY_SPINE_TOKENS],
                         key=lambda p: -nodes[p]["tokens"])
    heavy_import = [t for p in always for t in push.get(p, [])
                    if nodes[t]["tokens"] > HEAVY_IMPORT_TOKENS and t not in always]
    # nested CLAUDE.md/AGENTS.md that aren't the root spine are sub-spines (always-loaded when
    # working inside their subtree), not orphans — pull them out of the orphan bucket.
    nested_spines = sorted([p for p in nodes if nodes[p]["exists"] and p not in always
                            and os.path.basename(p) in ALWAYS_NAMES])
    weak = []
    if args.weak:
        for p in reachable:
            for label, t, _raw in pull.get(p, []):
                if weak_pointer(label, t, root):
                    weak.append((p, label or "(empty)", t))
    max_depth = max(depth.values()) if depth else 0
    mode = "plugin" if plugin else "skill" if invoke else "project"
    md_count = len([p for p in nodes if nodes[p]["exists"]])

    if args.json:
        print(json.dumps({
            "root": root, "mode": mode, "entries": [rel(e, root) for e in entries],
            "counts": {"md_files": md_count, "always_loaded": len(always), "on_invoke": len(invoke),
                       "reachable": len(reachable), "orphans": len(orphans), "dead_links": len(dead),
                       "max_depth": max_depth},
            "spine_tokens": spine_tokens, "description_tokens": listing_tokens,
            "always_loaded": [rel(p, root) for p in sorted(always)],
            "on_invoke": [{"file": rel(p, root), "tokens": nodes[p]["tokens"], "description_tokens": desc_tokens[p]}
                          for p in sorted(invoke)],
            "reachable": sorted(rel(p, root) for p in reachable),
            "orphans": [rel(p, root) for p in sorted(orphans)],
            "dead_links": [{"from": rel(s, root), "label": lab, "target": raw} for s, lab, raw in dead],
            "heavy_spine": [{"file": rel(p, root), "tokens": nodes[p]["tokens"]} for p in heavy_spine],
            "nested_spines": [rel(p, root) for p in nested_spines],
            "weak_pointers": [{"from": rel(s, root), "label": lab, "target": rel(t, root)} for s, lab, t in weak],
        }, indent=2))
        return

    if not args.rot:
        print(f"MAP    {root}")
        if len(entries) == 1:
            print(f"entry  {rel(entries[0], root)}")
            purpose = vault_purpose(entries[0])
            if purpose:
                print(f"what   {purpose}")
        else:
            print(f"entry  {len(entries)} ({mode}: its skills, agents and commands)" if entries else "entry  (none found)")
        print(f"       {md_count} md files · {len(always)} loaded every turn (~{spine_tokens}t) · "
              f"{len(invoke)} load when they fire (descriptions ~{listing_tokens}t every turn) · "
              f"{len(reachable)} reachable · {len(orphans)} orphan · max depth {max_depth}\n")
        print("ROUTED STRUCTURE  (● every turn  ◉ when it fires  ○ on Read  @ import-parent  → link-parent)")
        print("\n".join(render_layers(root, always, invoke, push, depth, parent, nodes, desc_tokens)))

    print("ROT REPORT")
    if dead:
        # group by source dir so one dumping directory is one line, not 200
        dgroups = {}
        for s, lab, raw in dead:
            top = rel(s, root).split("/", 1)[0] if "/" in rel(s, root) else "(root)"
            dgroups.setdefault(top, []).append((s, lab, raw))
        print(f"  DEAD LINKS ({len(dead)}) — pointer targets that don't exist:")
        for top in sorted(dgroups, key=lambda k: -len(dgroups[k])):
            ds = dgroups[top]
            for s, lab, raw in ds[:6]:
                lab = (lab[:60] + "…") if len(lab) > 61 else lab
                print(f"    {rel(s, root)}  \"{lab}\" → {raw}")
            if len(ds) > 6:
                print(f"    … {len(ds) - 6} more in {top}/")

    if orphans:
        # group by top-level dir — a directory that dumps 300 files is ONE line the reader
        # judges; a stray instruction .md at the root is the finding that matters
        groups = {}
        for p in orphans:
            r = rel(p, root)
            top = r.split("/", 1)[0] if "/" in r else "(root)"
            groups.setdefault(top, []).append(p)
        print(f"  ORPHANS ({len(orphans)}) — .md files no pointer reaches from an entry, by area:")
        for top in sorted(groups, key=lambda k: -len(groups[k])):
            ps = sorted(groups[top], key=lambda p: -nodes[p]["tokens"])
            tot = sum(nodes[p]["tokens"] for p in ps)
            head = ", ".join(os.path.basename(p) for p in ps[:4])
            more = f" +{len(ps) - 4} more" if len(ps) > 4 else ""
            print(f"    {top}/  ({len(ps)} files, ~{tot}t):  {head}{more}")

    if nested_spines:
        why = ("loaded when working in that folder of this repo; never shipped to the plugin's users"
               if plugin else "loaded when working in that folder")
        print(f"  NESTED SPINES ({len(nested_spines)}) — CLAUDE.md/AGENTS.md below the root, {why}:")
        for p in nested_spines:
            print(f"    {rel(p, root)}  (~{nodes[p]['tokens']}t)")
    if heavy_spine:
        print(f"  HEAVY SPINE ({len(heavy_spine)}) — large files loaded every turn (>{HEAVY_SPINE_TOKENS}t); confirm intentional or move the detail behind a link:")
        for p in heavy_spine:
            print(f"    {rel(p, root)}  (~{nodes[p]['tokens']}t)")
    if heavy_import:
        print(f"  HEAVY IMPORTS ({len(set(heavy_import))}) — large @-imported files paid every load:")
        for p in sorted(set(heavy_import), key=lambda p: -nodes[p]["tokens"]):
            print(f"    {rel(p, root)}  (~{nodes[p]['tokens']}t)")
    if weak:
        print(f"  WEAK POINTERS ({len(weak)}) — empty or filename-only labels; may be deliberate house style (adjacent prose can carry the trigger):")
        for s, lab, t in weak[:15]:
            print(f"    {rel(s, root)}  \"{lab}\" → {rel(t, root)}")
        if len(weak) > 15:
            print(f"    … {len(weak) - 15} more")
    if not (dead or orphans or nested_spines or heavy_spine or heavy_import or weak):
        print("  clean — no dead links, orphans, or heavy always-loaded files")


if __name__ == "__main__":
    cli()
