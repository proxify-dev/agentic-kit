#!/usr/bin/env python3
"""What code alone can read in an agent instruction file (`AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md`): its lines cut into
blocks, what the repo already shows (manifests, lockfile, schema, folders, scripts), pointers and whether they are dead,
commands and whether they check the work, how the file loads (and an `AGENTS.md` Claude Code never reads), how its lines
are laid out, git's history per line. No AI. Moved here from the trial in
`experiments/2026-11-12-instruction-read/` (README.md there); setup_map.py, setup_read.py and the trial import it.

    setup_instruction.py never judges a level: setup_read.py turns its findings into advice cards.

Standard library only (Python 3.12) and git; read-only on every file it reads; no network. Never raises on a file it
cannot read: the finding says "unreadable".
"""
import difflib
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import setup_map  # noqa: E402  frontmatter, the @import pattern, the secret scrubber

IMPORT_HOPS = 4          # Claude Code's memory docs (fetched 11-12): "a maximum depth of four hops"; setup_map says 5
WALK_FILES_CAP = 20_000  # files listed per repo for near-miss names and nested instruction files
GIT_TIMEOUT = 20
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".turbo", "worktrees",
             ".cache", "target"}
INSTRUCTION_NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")
SHORT_LINE = 90          # a paragraph line shorter than this that ends a sentence was not hard-wrapped: a new rule follows

SITUATION = re.compile(r"(?i)\b(when(?:ever)?|if|before|after|once|unless|until|while|each time|every time|in case)\b")
REASON = re.compile(r"(?i)(\bwhy\b\s*:|\(why\b|\bbecause\b|\bso that\b|\botherwise\b|\bsince\b(?!\s+\d)|"
                    r"\bwhich (?:means|is why)\b|\bto avoid\b|\bto keep\b|\bfor the sake of\b|\bthe reason\b|\bit (?:breaks|fails)\b)")
ABSOLUTE = re.compile(r"(?i)\b(always|never|must|do not|don't|under no circumstances|no exceptions?|at all times)\b")
SHOUT_WORDS = re.compile(r"\b(IMPORTANT|NEVER|ALWAYS|MUST|CRITICAL|WARNING|NOTE|NB|DO NOT|DON'T|ONLY|YOU MUST)\b")
CAPS_RUN = re.compile(r"\b[A-Z]{2,}(?:\s+[A-Z]{2,})+\b")
NEGATION = re.compile(r"(?i)\b(don't|do not|never|avoid|no longer)\b")
POSITIVE = re.compile(r"(?i)\b(instead|use|prefer|rather|run|write|put)\b")
EXAMPLE = re.compile(r"(?i)(\be\.g\.|\bfor example\b|\bfor instance\b|\bexample:|\bsuch as\b|\binstead of\b)")
DONE = re.compile(r"(?i)(\bdone when\b|\bdefinition of done\b|\bdone means\b|\bis done when\b|\bbefore (?:you )?(?:say|call|report|declare)\b[^.]*\bdone\b|"
                  r"\bbefore (?:committing|you commit|finishing|you finish)\b|\bprints nothing\b|\buntil [^.]*\bpass(?:es)?\b)")
COMPACT = re.compile(r"(?i)(/compact\b|\bcompact(?:ion|ing|ed|s)?\b)")
REFS = re.compile(r"(?i)\b(skills?|hooks?|sub-?agents?|agents?|plugins?|forks?|slash commands?|SessionStart|PreToolUse|PostToolUse|Stop hook)\b")
DATED = re.compile(r"(?i)(\b20\d\d-\d\d(?:-\d\d)?\b|\bas of\b|\bcurrently\b|\bthis week\b|\bfor now\b|\bfirst months\b|\btoday\b)")
URL = re.compile(r"https?://\S+")
POINTER_VERB = re.compile(r"(?i)\b(see|read|open|consult|follow|refer to|look (?:at|in)|details? in|described in|"
                          r"documented in|lives? in|is in|are in)\b")
VERSION = re.compile(r"\b([A-Za-z][\w.\-]*[A-Za-z])\s*\(?\s*v(\d+)(?:\.\d+)*\b")
LABEL = re.compile(r"^\s*(?:[-*+]|\d+[.)])?\s*\*\*([^*]+?):?\*\*:?\s*")
WORDS = re.compile(r"`[^`\n]+`|[A-Za-z][\w.+#'-]*[\w+#]|[A-Za-z]")
RUNNERS = ("pnpm", "npm", "npx", "yarn", "bun", "bunx", "uv", "uvx", "python", "python3", "pytest", "make", "just", "cargo",
           "go", "node", "tsc", "ruff", "mypy", "eslint", "jest", "vitest", "git", "gh", "glab", "docker", "bash", "sh",
           "deno", "pip", "poetry", "bq", "gcloud", "claude", "grep", "ls", "cat", "curl", "supabase", "rails", "mvn", "gradle")
CHECK_WORDS = re.compile(r"(?i)(\btest\b|\btests\b|test:|\blint\b|\btypecheck\b|type-check|\btsc\b|--check\b|\bcheck\b|\bpytest\b|"
                         r"\bvitest\b|\bjest\b|\bruff\b|\bmypy\b|\beslint\b|\bverify\b|\be2e\b|\bvalidate\b|\bbuild\b)")
LOCKFILES = {"pnpm-lock.yaml": "pnpm", "package-lock.json": "npm", "yarn.lock": "yarn", "bun.lockb": "bun", "bun.lock": "bun",
             "uv.lock": "uv", "poetry.lock": "poetry", "Pipfile.lock": "pipenv", "Cargo.lock": "cargo", "go.sum": "go",
             "Gemfile.lock": "bundler", "composer.lock": "composer"}
XML_LINE = re.compile(r"^\s*<(/?)([A-Za-z][\w.\-]*)(?:\s[^<>]*)?>\s*$")
XML_INLINE = re.compile(r"<([A-Za-z][\w.\-]*)(?:\s[^<>]*)?>[\s\S]*?</\1>")
BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def squash(text):
    return " ".join(str(text).split())


def norm(token):
    """A name compared with the repo's names: lower case, letters and digits only, a trailing 'js' dropped."""
    t = re.sub(r"[^a-z0-9]", "", token.lower())
    return t[:-2] if t.endswith("js") and len(t) > 4 else t


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def git(root, *args, timeout=GIT_TIMEOUT):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def repo_root(path):
    d = path if os.path.isdir(path) else os.path.dirname(path)
    out = git(d, "rev-parse", "--show-toplevel")
    return os.path.realpath(out.strip()) if out else None


def common_dir(path):
    d = path if os.path.isdir(path) else os.path.dirname(path)
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    out = git(d, "rev-parse", "--path-format=absolute", "--git-common-dir") if d else None
    return os.path.realpath(out.strip()) if out else None



# ------------------------------------------------------------------ blocks

def blocks(text):
    """The file cut into the units an agent reads as one instruction: a heading, a bullet (with its wrapped lines; a
    nested bullet is its own block), a paragraph, a fenced code block, a table row, an HTML comment (Claude Code strips
    it before loading). Each: id, kind, start / end line (1-based), text, section (the heading path), tags (the XML
    tags it sits in), judge (False for a heading that only labels, a table's header row, a comment, a tag line)."""
    lines = text.split("\n")
    _, start = setup_map.frontmatter(lines)
    out, cur, section, tags = [], None, [], []
    i = start

    def flush():
        nonlocal cur
        if cur:
            cur["text"] = "\n".join(cur.pop("lines")).strip("\n")
            out.append(cur)
        cur = None

    def new(kind, n, line, **kw):
        return {"kind": kind, "start": n + 1, "end": n + 1, "lines": [line], "section": [s for _, s in section],
                "tags": list(tags), **kw}

    while i < len(lines):
        line = lines[i]
        if setup_map.FENCE.match(line):
            flush()
            cur = new("code", i, line)
            i += 1
            while i < len(lines):
                cur["lines"].append(lines[i])
                cur["end"] = i + 1
                if setup_map.FENCE.match(lines[i]):
                    break
                i += 1
            flush()
            i += 1
            continue
        if line.lstrip().startswith("<!--"):
            flush()
            cur = new("comment", i, line)
            while "-->" not in lines[i] and i + 1 < len(lines):
                i += 1
                cur["lines"].append(lines[i])
                cur["end"] = i + 1
            flush()
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        m = setup_map.HEADING.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            section = [(lv, s) for lv, s in section if lv < level]
            cur = new("heading", i, line, level=level)
            flush()
            section.append((level, title))
            i += 1
            continue
        x = XML_LINE.match(line)
        if x:
            flush()
            closing, name = x.group(1) == "/", x.group(2)
            out.append({"kind": "tag", "start": i + 1, "end": i + 1, "text": line.strip(), "section": [s for _, s in section],
                        "tags": list(tags), "tag": name, "closing": closing})
            if closing:
                if name in tags:
                    tags = tags[:len(tags) - 1 - tags[::-1].index(name)]
            else:
                tags.append(name)
            i += 1
            continue
        if TABLE_ROW.match(line):
            if TABLE_SEP.match(line):
                if out and out[-1]["kind"] == "table-row" and cur is None:
                    out[-1]["header"] = True
                i += 1
                continue
            flush()
            cur = new("table-row", i, line)
            flush()
            i += 1
            continue
        b = BULLET.match(line)
        if b:
            flush()
            cur = new("bullet", i, line, indent=len(b.group(1)))
            i += 1
            continue
        prev = cur["lines"][-1].rstrip() if cur else ""
        if cur is not None and cur["kind"] == "para" and re.search(r"[.!?]$", prev) and len(prev) < SHORT_LINE:
            flush()   # one rule per line with no bullet: a short line that ends a sentence ends its block
        if cur is None:
            cur = new("para", i, line)
        else:
            cur["lines"].append(line)
            cur["end"] = i + 1
        i += 1
    flush()
    for k, bl in enumerate(out, 1):
        bl["id"] = f"B{k}"
        words = len(re.sub(r"[#*_`>|-]", " ", bl["text"]).split())
        if bl["kind"] in ("comment", "tag") or bl.get("header"):
            bl["judge"] = False
        elif bl["kind"] == "heading":
            title = setup_map.HEADING.match(bl["text"]).group(2)
            # a heading that is a sentence (an instruction written as a heading) is read like any other line
            bl["judge"] = len(title.split()) >= 7 or bool(re.search(r"[!.]\s*$|:\s+\S", title))
        else:
            bl["judge"] = words > 0
    return out


# ------------------------------------------------------------------ what the repo already shows

def _manifest_names(name):
    """The names one dependency is known by: 'react', '@nestjs/core' -> nestjs, core; 'platform-fastify' -> fastify."""
    out = {name}
    parts = [p for p in re.split(r"[@/]", name) if p]
    for p in parts:
        out.add(p)
        out.update(x for x in p.split("-") if len(x) >= 4)
    return out


def repo_facts(root):
    """What an agent finds by looking: dependency names and versions (package.json, pyproject.toml), the package
    manager (lockfile, packageManager), the database (prisma schema, compose images), scripts and make / just targets,
    top-level folders and files. Hook scripts are kept apart: the agent meets their text only when a hook fires."""
    f = {"root": root, "names": {}, "versions": {}, "scripts": {}, "tools": {}, "folders": [], "files": [],
         "hooks": {}, "skills": [], "agents": [], "rules": [], "manifests": []}
    if not root or not os.path.isdir(root):
        return f

    def name(n, src):
        if n:
            f["names"].setdefault(norm(n), set()).add(src)

    manifests = []
    for d in [root] + [os.path.join(root, x) for x in sorted(os.listdir(root))
                       if os.path.isdir(os.path.join(root, x)) and x not in SKIP_DIRS and not x.startswith(".")]:
        for m in ("package.json", "pyproject.toml"):
            p = os.path.join(d, m)
            if os.path.isfile(p):
                manifests.append(p)
    for p in manifests[:40]:
        rel = os.path.relpath(p, root)
        f["manifests"].append(rel)
        text = read_text(p) or ""
        if p.endswith("package.json"):
            try:
                pj = json.loads(text)
            except ValueError:
                continue
            for sect in ("dependencies", "devDependencies", "peerDependencies"):
                for dep, ver in (pj.get(sect) or {}).items():
                    for n in _manifest_names(dep):
                        name(n, f"{rel}: {dep} {ver}")
                    f["versions"].setdefault(norm(dep.split("/")[0].lstrip("@")), (dep, str(ver)))
                    f["versions"].setdefault(norm(dep.split("/")[-1]), (dep, str(ver)))
            for s, cmd in (pj.get("scripts") or {}).items():
                f["scripts"].setdefault(s, f"{rel}: {cmd}")
            pm = (pj.get("packageManager") or "").split("@")[0]
            if pm:
                f["tools"][pm] = f"{rel}: packageManager {pj['packageManager']}"
                name(pm, f["tools"][pm])
            for b in (pj.get("bin") or {}) if isinstance(pj.get("bin"), dict) else []:
                f["scripts"].setdefault(b, f"{rel}: bin")
        else:
            try:
                py = tomllib.loads(text)
            except (tomllib.TOMLDecodeError, ValueError):
                continue
            proj = py.get("project") or {}
            for dep in proj.get("dependencies") or []:
                dn = re.split(r"[\s<>=!~;\[]", dep, maxsplit=1)[0]
                for n in _manifest_names(dn):
                    name(n, f"{rel}: {dep}")
            for s in (proj.get("scripts") or {}):
                f["scripts"].setdefault(s, f"{rel}: [project.scripts]")
            if proj.get("requires-python"):
                name("python", f"{rel}: requires-python {proj['requires-python']}")
    for lock, tool in LOCKFILES.items():
        if os.path.isfile(os.path.join(root, lock)):
            f["tools"][tool] = lock
            name(tool, lock)
    for mk in ("Makefile", "makefile", "justfile", ".justfile"):
        text = read_text(os.path.join(root, mk))
        if text:
            for t in re.findall(r"^([A-Za-z][\w.-]*)\s*:(?!=)", text, re.M):
                f["scripts"].setdefault(t, mk)
    schema = read_text(os.path.join(root, "prisma", "schema.prisma"))
    if schema:
        name("prisma", "prisma/schema.prisma")
        for prov in re.findall(r'provider\s*=\s*"([\w-]+)"', schema):
            name(prov, f'prisma/schema.prisma: provider = "{prov}"')
    for comp in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        text = read_text(os.path.join(root, comp))
        for img in re.findall(r"image:\s*['\"]?([\w./-]+)", text or ""):
            name(img.split("/")[-1].split(":")[0], f"{comp}: image {img}")
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        entries = []
    for e in entries:
        p = os.path.join(root, e)
        if os.path.isdir(p) and e not in (".git",):
            f["folders"].append(e)
            name(e, f"folder {e}/")
        elif os.path.isfile(p):
            f["files"].append(e)
    hooks_dir = os.path.join(root, ".claude", "hooks")
    if os.path.isdir(hooks_dir):
        for h in sorted(os.listdir(hooks_dir)):
            text = read_text(os.path.join(hooks_dir, h))
            if text:
                f["hooks"][f".claude/hooks/{h}"] = text[:4000]
    for kind, sub in (("skills", "skills"), ("agents", "agents"), ("rules", "rules")):
        d = os.path.join(root, ".claude", sub)
        if os.path.isdir(d):
            f[kind] = sorted(x[:-3] if x.endswith(".md") else x for x in os.listdir(d) if not x.startswith("."))
    f["remotes"] = git(root, "remote", "-v") or ""
    f["names"] = {k: sorted(v) for k, v in f["names"].items()}
    return f


def is_named(tok, sentence_start):
    """A token that names a thing (a tool, a library, a product) rather than an ordinary word: backticked, CamelCase,
    holding a digit or a dot, or capitalised mid-sentence. Short all-caps words (API, ORM, E2E) are kinds, not names."""
    if tok.startswith("`"):
        inner = tok.strip("`")
        return " " not in inner and "/" not in inner and len(inner) <= 40
    if re.fullmatch(r"[A-Z0-9]{2,4}", tok) or re.fullmatch(r"v\d+", tok):
        return False
    if re.search(r"[a-z][A-Z]", tok) or re.search(r"[A-Z]{2,}[a-z]", tok):
        return True
    if re.search(r"\d", tok) and re.search(r"[A-Za-z]{2}", tok):
        return True
    if "." in tok.strip("."):
        return True
    return tok[:1].isupper() and not sentence_start and tok[1:].islower() and len(tok) > 2


def named_things(text):
    """[(token, sentence_start)] for the words of a block, its bold label and link targets removed."""
    t = LABEL.sub(" ", text, count=1)
    t = setup_map.MD_LINK.sub(lambda m: " " + m.group(0).split("](")[0].lstrip("[") + " ", t)
    t = URL.sub(" ", t)
    out, start = [], True
    for m in WORDS.finditer(t):
        tok = m.group(0)
        before = t[:m.start()].rstrip()
        start = (not before) or before[-1] in ".:!?\n#*-_|>" or re.search(r"(^|\n)\s*([-*+]|\d+[.)])$", before) is not None
        out.append((tok, start))
    return out


def match_name(tok, facts):
    n = norm(tok.strip("`"))
    if len(n) < 2:
        return None
    if n in facts["names"]:
        return facts["names"][n][0]
    for k, srcs in facts["names"].items():
        if len(n) >= 5 and len(k) >= 5 and (n.startswith(k) or k.startswith(n)):
            return srcs[0]
    return None


def imperative(text):
    """A hint that the block asks for an action: a modal, or a sentence that opens on a verb the agent could do."""
    t = LABEL.sub("", text)
    if re.search(r"(?i)\b(must|should|always|never|don't|do not|avoid|prefer|make sure|remember)\b", t):
        return True
    verbs = r"(use|run|read|see|make|give|keep|put|write|add|call|ask|check|open|follow|delegate|commit|start|stop|" \
            r"search|look|load|test|create|update|edit|move|link|name|leave|treat|port|record|report|say|grep|install|" \
            r"invoke|fork|push|merge|pull|work|go|do|let|send|answer|tell|show|copy|drop|cut|split|fix|build|wait|ship)"
    for s in re.split(r"(?<=[.!?:])\s+|\n", t):
        s = re.sub(r"^[\s>*_#|`-]*(?:\d+[.)])?\s*", "", s)
        if re.match(r"(?i)" + verbs + r"\b", s):
            return True
    return False


# ------------------------------------------------------------------ pointers and commands

def all_files(root):
    """The repo's files, relative to its root: what git tracks plus untracked files it does not ignore; a folder
    outside git is walked."""
    listed = git(root, "ls-files", "-co", "--exclude-standard", timeout=GIT_TIMEOUT * 3)
    if listed is not None and repo_root(root) == os.path.realpath(root):
        return [x for x in listed.splitlines() if x and "/.claude/worktrees/" not in "/" + x][:WALK_FILES_CAP * 5]
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for x in files:
            out.append(os.path.relpath(os.path.join(d, x), root))
            if len(out) >= WALK_FILES_CAP:
                return out
    return out


def resolve(raw, base_dir, root, home):
    if raw[:1] in "\"'" or re.fullmatch(r"\.\w{1,5}", raw) or ":/" in raw:
        return None, None   # a string literal or a bare extension (`.md`) names no file
    raw = raw.rstrip(".,;:!?)\"'").replace("\\ ", " ")
    # a pattern, a placeholder or an elision names no one file; a git ref (origin/main) is no path
    if not raw or re.search(r"[<>*{}$|\[\]…]|\.\.\.", raw) or raw.startswith(("origin/", "upstream/", "refs/")):
        return None, None
    if os.path.isabs(raw) and not os.path.exists(os.sep + raw.strip(os.sep).split(os.sep)[0]):
        return None, None   # /levels/3 is a page of a site, not a file
    if raw.startswith("~/"):
        cands = [os.path.join(home, raw[2:])]
    elif os.path.isabs(raw):
        cands = [raw]
    else:
        cands = [os.path.join(base_dir, raw)] + ([os.path.join(root, raw)] if root else [])
    cands = [os.path.normpath(c) for c in cands]
    for c in cands:
        if os.path.exists(c):
            return c, True
    return cands[0], False


_VIEWS = {}


def repo_view(root):
    """(facts, files) of one repo, read once per run: what the repo shows and every file name in it."""
    if root not in _VIEWS:
        _VIEWS[root] = (repo_facts(root), all_files(root))
    return _VIEWS[root]


def backticked_state(raw, sentence, root, facts, files):
    """What a backticked path that resolves nowhere is: ("dead", None) when the sentence sends the agent to it (a pointer
    verb: see, read, open, details in...) and nothing explains the name; ("mention", None) when the sentence only names it
    (an output, a thing to make); ("name", None) when it is a dependency, a tool or the repo's own remote (`@scope/sdk`,
    `org/repo`); ("found", relative path) when the repo has a file of that name elsewhere (shorthand the agent finds with
    one search). One rule for the map (setup_map.pointers) and for a file's blocks (pointers_in), so the two never differ.
    Known misses: a package declared in a manifest below the root, another repo's GitHub name."""
    if not POINTER_VERB.search(sentence or ""):
        return "mention", None
    if facts and (match_name(raw, facts) or raw in facts.get("remotes", "")):
        return "name", None
    r = raw.rstrip("/")
    folder = raw.endswith("/") or "/" in r
    hit = next((x for x in files or [] if ((("/" + x).endswith("/" + r) or ("/" + r + "/") in ("/" + x)) if folder
                                           else os.path.basename(x) == r)), None)
    return ("found", hit) if hit else ("dead", None)


def pointers_in(text, path, root, home, files, facts=None):
    """Every file or folder a block names: markdown links, @imports, backticked paths, and bare paths that exist.
    A link, an import or a backticked path that resolves nowhere is a dead pointer (a bare word that resolves
    nowhere is prose). A dead one gets the closest file name in the repo, when one is close."""
    out, seen = [], set()
    base = os.path.dirname(path)
    found = []
    for m in setup_map.MD_LINK.finditer(text):
        t = m.group(1).split("#")[0]
        if t and not re.match(r"^[a-zA-Z][\w+.\-]*:", t):
            label = text[m.start():m.end()].split("](")[0].lstrip("[")
            found.append((t.replace("%20", " "), "link", label, None))
    masked = setup_map.CODE_SPAN.sub(lambda m: " " * len(m.group(0)), text)
    for m in setup_map.IMPORT.finditer(masked):
        found.append((m.group(1), "import", None, None))
    for m in setup_map.CODE_SPAN.finditer(text):
        inner = m.group(0).strip("`").strip()
        if inner.startswith("@"):
            inner = inner[1:]
        if " " not in inner and not inner.startswith(("-", "http")) and (
                "/" in inner or re.search(r"\.(md|mdc|txt|json|ya?ml|toml|py|sh|ts|js|sql|prisma|csv|html)$", inner)):
            ls = text.rfind("\n", 0, m.start()) + 1   # the sentence of the line it stands on: the map reads a line the same way
            found.append((inner, "backticked", None, setup_map.sentence_at(text[ls:text.find("\n", m.start()) if "\n" in text[m.start():] else len(text)], m.start() - ls)))
    for m in re.finditer(r"(?<![\w`@/.~-])((?:~/|\.{1,2}/|/)?[\w.\-]+(?:/[\w.\-]+)+/?|[\w\-]+\.(?:md|json|ya?ml|toml|py|sh))(?![\w`])",
                         setup_map.CODE_SPAN.sub(" ", URL.sub(" ", setup_map.MD_LINK.sub(" ", text)))):
        found.append((m.group(1), "bare", None, None))
    for raw, kind, label, sentence in found:
        target, exists = resolve(raw, base, root, home)
        if target is None or (kind == "bare" and not exists):
            continue
        if not exists and kind == "backticked":
            state, hit = backticked_state(raw, sentence or text, root, facts, files)
            if state != "dead":
                if hit is None:
                    continue   # a mention, or a name the repo uses: no pointer
                target, exists = os.path.join(root, hit), True
        if not exists and kind in ("backticked", "link") and files:
            # shorthand for a file elsewhere in the repo: `build.py` for plugins/x/build.py, `bin/` inside a plugin;
            # the agent finds it with one search, so it is no dead pointer
            r = raw.rstrip("/")
            folder = raw.endswith("/") or "/" in r
            hit = next((x for x in files if ((("/" + x).endswith("/" + r) or ("/" + r + "/") in ("/" + x)) if folder
                                             else os.path.basename(x) == r)), None)
            if hit:
                target, exists = os.path.join(root, hit), True
        key = (os.path.normpath(target), kind if kind == "import" else "")
        if key in seen or os.path.normpath(target) == os.path.normpath(path):
            continue
        seen.add(key)
        p = {"raw": raw, "kind": kind, "exists": bool(exists), "target": display(target, root, home)}
        if label is not None:
            p["label"] = label
        if not exists and files:
            near = difflib.get_close_matches(os.path.basename(raw), [os.path.basename(x) for x in files], n=1, cutoff=0.6)
            if near:
                p["near"] = next(x for x in files if os.path.basename(x) == near[0])
        out.append(p)
    return out


def display(p, root, home):
    p = os.path.normpath(p)
    if root and (p == root or p.startswith(root + os.sep)):
        return os.path.relpath(p, root)
    if p == home or p.startswith(home + os.sep):
        return "~" + p[len(home):]
    return p


def commands_in(block, facts):
    """Commands a block gives the agent (backticked spans that start with a runner or a script of the repo, and lines
    of a fenced block): whether each exists in the repo and whether it checks the work (tests, types, lint, build)."""
    texts = []
    if block["kind"] == "code":
        texts = [ln.strip().lstrip("$ ") for ln in block["text"].split("\n")[1:-1] if ln.strip() and not ln.strip().startswith("#")]
    else:
        texts = [m.group(0).strip("`").strip() for m in setup_map.CODE_SPAN.finditer(block["text"])]
    out = []
    for c in texts:
        first = c.split()[0] if c.split() else ""
        own = first.startswith(("./", "bin/", ".bin/", "scripts/")) or first in facts["scripts"]
        if not (first in RUNNERS or own) or (" " not in c and not own and first not in ("make", "just")):
            continue
        exists, script = None, None
        w = c.split()
        if first in ("pnpm", "npm", "yarn", "bun") and len(w) > 1:
            script = w[2] if len(w) > 2 and w[1] == "run" else w[1]
            exists = script in facts["scripts"] if facts["manifests"] else None
            if script in ("install", "i", "add", "dlx", "exec", "x", "remove"):
                exists, script = True, None
        elif first in ("make", "just") and len(w) > 1:
            script = w[1]
            exists = script in facts["scripts"]
        elif first in ("python", "python3", "bash", "sh", "node") and len(w) > 1 and "/" in w[1]:
            exists = os.path.exists(os.path.join(facts["root"] or "", w[1])) if facts["root"] else None
        elif own and first.startswith(("./", "bin/", ".bin/", "scripts/")):
            exists = os.path.exists(os.path.join(facts["root"] or "", first)) if facts["root"] else None
        body = facts["scripts"].get(script or "", "")
        out.append({"cmd": setup_map.scrub(c)[:160], "exists": exists,
                    "checks_work": bool(CHECK_WORDS.search(c) or CHECK_WORDS.search(body or ""))})
    return out


# ------------------------------------------------------------------ per block

def cues(block, facts, path, root, home, files):
    text = block["text"]
    prose = setup_map.CODE_SPAN.sub(lambda m: " " * len(m.group(0)), text) if block["kind"] != "code" else ""
    toks = named_things(text) if block["kind"] != "code" else []
    named = [(t, s) for t, s in toks if is_named(t, s)]
    found, unfound = {}, []
    tool_words = {norm(x) for x in LOCKFILES.values()} | {norm(x) for x in facts["tools"]}
    for t, s in toks:
        src = match_name(t, facts) if (is_named(t, s) or norm(t) in tool_words) else None
        if src:
            found.setdefault(t.strip("`"), src)
        elif is_named(t, s):
            unfound.append(t.strip("`"))
    versions = []
    for m in VERSION.finditer(text):
        nm, major = m.group(1), m.group(2)
        hit = facts["versions"].get(norm(nm))
        if hit:
            have = re.sub(r"^[^\d]*", "", hit[1]).split(".")[0]
            versions.append({"said": f"{nm} v{major}", "repo": f"{hit[0]} {hit[1]}", "same": have == major})
    ptrs = pointers_in(text, path, root, home, files, facts)
    cmds = commands_in(block, facts)
    shout = sorted(set(SHOUT_WORDS.findall(prose)) | set(CAPS_RUN.findall(prose)))
    shout = [s for s in shout if not re.fullmatch(r"[A-Z]{2,4}", s) or s in ("NB", "NOTE", "MUST", "ONLY", "NEVER")]
    c = {
        "situation": [m.group(0) for m in SITUATION.finditer(prose)][:4],
        "reason": [m.group(0) for m in REASON.finditer(prose)][:3],
        "absolute": [m.group(0) for m in ABSOLUTE.finditer(prose)][:4],
        "emphasis": shout + (["!"] if re.search(r"!(\s|$)", prose) else []),
        "negation_without_target": bool(NEGATION.search(prose)) and not POSITIVE.search(prose),
        "example": block["kind"] == "code" or bool(EXAMPLE.search(text)),
        "done": bool(DONE.search(text)),
        "compaction": bool(COMPACT.search(text)),
        "refs": sorted({m.group(1).lower() for m in REFS.finditer(prose)}),
        "dated": [m.group(0) for m in DATED.finditer(text)][:3],
        "urls": len(URL.findall(text)),
        "imperative": imperative(text) if block["kind"] != "code" else False,
    }
    # a slash command counts only when written as one (backticked), never as a piece of a path
    c["slash"] = sorted({m.group(1) for m in re.finditer(r"`(/[a-z][\w:-]*)(?:\s[^`]*)?`", text)})[:5]
    enforced = []
    for hook, body in facts["hooks"].items():
        hb = body.lower()
        for t in [t for t, s in toks if is_named(t, s)] + [m.group(0) for m in setup_map.CODE_SPAN.finditer(text)]:
            w = t.strip("`").lower()
            if len(w) >= 3 and re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", hb):
                enforced.append({"hook": hook, "word": t.strip("`")})
                break
    named_set = sorted({norm(t) for t, _ in named})
    restates = bool(found) and len(found) >= 2 and not unfound and not ptrs and not cmds and not c["situation"] \
        and not c["imperative"] and block["kind"] in ("para", "bullet", "table-row")
    return {"found": found, "named_not_found": sorted(set(unfound))[:8], "versions": versions, "pointers": ptrs,
            "commands": cmds, "cues": c, "enforced_by_hook": enforced, "restates_repo": restates, "_named": named_set}


# ------------------------------------------------------------------ the file

def agents_md_state(path, root, home):
    """How Claude Code treats one AGENTS.md (its memory docs, fetched 11-12: it reads `AGENTS.md` only when no CLAUDE.md,
    .claude/CLAUDE.md or CLAUDE.local.md sits in its folder or above it, up to the repo root): (state, the file that decides).
    "link": a CLAUDE.md that is a link to it (one file, counted once). "imported": the CLAUDE.md beside it has an `@AGENTS.md` line.
    "shadowed": a CLAUDE.md sits at or above it and imports none of it, so Claude Code reads that file only. "free": none sits
    there, so Claude Code reads it. Whether an @import of it from further away loads it is the map's to follow, not decided here."""
    d = os.path.dirname(path)
    q = d
    while True:
        for other in ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md"), "CLAUDE.local.md"):
            op = os.path.join(q, other)
            if os.path.isfile(op):
                if os.path.realpath(op) == os.path.realpath(path):
                    return "link", op
                if q == d and re.search(r"(?<![\w`])@(\./)?AGENTS\.md\b", setup_map.CODE_SPAN.sub(" ", read_text(op) or "")):
                    return "imported", op
                return "shadowed", op
        if not root or os.path.realpath(q) == os.path.realpath(root) or q in ("/", home) or os.path.dirname(q) == q:
            return "free", None
        q = os.path.dirname(q)


def loads(path, home):
    """When Claude Code loads this file (its memory docs, fetched 11-12) and the facts that decide it."""
    name, d = os.path.basename(path), os.path.dirname(path)
    claude_dir = os.path.join(home, ".claude")
    if os.path.normpath(path) == os.path.join(claude_dir, "CLAUDE.md"):
        return "every session on this laptop (user level)"
    if os.sep + os.path.join(".claude", "rules") + os.sep in path:
        fm, _ = setup_map.frontmatter((read_text(path) or "").split("\n"))
        return "when a file matching its paths: is read" if fm.get("paths") else "every session in the project (a rule with no paths:)"
    if os.path.normpath(d) == os.path.normpath(home):
        return "every session started anywhere under the home folder (an ancestor of every project)"
    root = repo_root(path)
    at_root = root and os.path.realpath(d) in (root, os.path.join(root, ".claude"))
    where = "every session in this repo" if at_root else f"when the agent works in {os.path.basename(d)}/ (or a session starts there)"
    if name == "CLAUDE.local.md":
        ignored = git(d, "check-ignore", "-q", name) is not None if root else None
        return where + ", private to this laptop" + ("" if ignored or root is None else " (not git-ignored)")
    if name == "AGENTS.md":
        state, other = agents_md_state(path, root, home)
        if state == "link":
            return where + " (CLAUDE.md is a link to it)"
        if state == "imported":
            return where + " (imported by the CLAUDE.md beside it)"
        if state == "shadowed":
            return f"not read by default: {display(other, root, home)} sits at or above it and does not import it"
        return where + " (no CLAUDE.md at or above it)"
    return where


def imports_chain(path, home, hops=0, seen=None):
    """[(path, hops, exists)] of every @import below this file, as Claude Code expands them (four hops)."""
    seen = seen if seen is not None else {os.path.realpath(path)}
    out = []
    text = read_text(path) or ""
    body = []
    fence = False
    for ln in text.split("\n"):
        if setup_map.FENCE.match(ln):
            fence = not fence
            continue
        if not fence:
            body.append(setup_map.CODE_SPAN.sub(lambda m: " " * len(m.group(0)), ln))
    for m in setup_map.IMPORT.finditer("\n".join(body)):
        raw = m.group(1).rstrip(".,;:)")
        t, ok = resolve(raw, os.path.dirname(path), None, home)
        if t is None or os.path.realpath(t) in seen:
            continue
        seen.add(os.path.realpath(t))
        out.append({"path": t, "hops": hops + 1, "exists": bool(ok), "loads": hops + 1 <= IMPORT_HOPS})
        if ok and hops + 1 < IMPORT_HOPS + 1 and os.path.isfile(t):
            out += imports_chain(t, home, hops + 1, seen)
    return out


def every_session(path, home):
    """What sits in the context window at the start of a session in this file's folder: the CLAUDE.md /
    CLAUDE.local.md chain from the repo root (or home) down to the folder, the user-level file, unscoped rules, and
    every @import of those, with words and an estimate of tokens (characters / 4; an estimate, not a count)."""
    d = os.path.dirname(os.path.realpath(path)) if not path.endswith(os.path.join(".claude", "CLAUDE.md")) else os.path.dirname(os.path.dirname(path))
    chain, q = [], d
    while True:
        for n in ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md"), "CLAUDE.local.md"):
            p = os.path.join(q, n)
            if os.path.isfile(p):
                chain.append(p)
        if q in ("/", "") or os.path.dirname(q) == q:
            break
        q = os.path.dirname(q)
    user = os.path.join(home, ".claude", "CLAUDE.md")
    if os.path.isfile(user) and user not in chain:
        chain.append(user)
    root = repo_root(path)
    for rd in [os.path.join(home, ".claude", "rules")] + ([os.path.join(root, ".claude", "rules")] if root else []):
        if os.path.isdir(rd):
            for dd, _, fs in os.walk(rd):
                for x in fs:
                    if x.endswith(".md"):
                        p = os.path.join(dd, x)
                        if not setup_map.frontmatter((read_text(p) or "").split("\n"))[0].get("paths"):
                            chain.append(p)
    files = []
    for p in dict.fromkeys(chain):
        files.append(p)
        files += [i["path"] for i in imports_chain(p, home) if i["exists"] and i["loads"] and os.path.isfile(i["path"])]
    words = chars = 0
    for p in dict.fromkeys(files):
        t = re.sub(r"<!--[\s\S]*?-->", "", read_text(p) or "")
        words += len(t.split())
        chars += len(t)
    return {"files": [display(p, root, home) for p in dict.fromkeys(files)], "words": words, "tokens_est": chars // 4}


def nested_below(path, root, files=None):
    d = os.path.dirname(path)
    if not root or os.path.realpath(d) != root:
        return []
    out = []
    for x in all_files(root) if files is None else files:
        if os.path.basename(x) in INSTRUCTION_NAMES and os.path.dirname(x) not in ("", ".claude") and "/.claude/worktrees/" not in "/" + x:
            out.append(x)
    return out


def sibling(path):
    """The AGENTS.md beside a CLAUDE.md, or the CLAUDE.md beside an AGENTS.md: how they relate and the lines they share."""
    d, name = os.path.dirname(path), os.path.basename(path)
    other = {"CLAUDE.md": "AGENTS.md", "AGENTS.md": "CLAUDE.md"}.get(name)
    if not other or not os.path.isfile(os.path.join(d, other)):
        return None
    op = os.path.join(d, other)
    if os.path.realpath(op) == os.path.realpath(path):
        return {"file": other, "relation": "the same file (a link)"}
    a, b = read_text(path) or "", read_text(op) or ""
    claude = a if name == "CLAUDE.md" else b
    imported = bool(re.search(r"(?<![\w`])@(\./)?AGENTS\.md\b", setup_map.CODE_SPAN.sub(" ", claude)))
    la = {squash(x) for x in a.split("\n") if len(squash(x)) >= 20}
    lb = {squash(x) for x in b.split("\n") if len(squash(x)) >= 20}
    pa = set(re.findall(r"[\w./-]+\.md", a)) - {other, name}
    pb = set(re.findall(r"[\w./-]+\.md", b)) - {other, name}
    return {"file": other, "relation": "CLAUDE.md imports AGENTS.md" if imported else "both, no import: Claude Code reads CLAUDE.md only",
            "shared_lines": len(la & lb), "shared_pointers": sorted(pa & pb)}


def history(path):
    """Git's record of the file: commits, first and last date, authors (a count, never a name), lines removed over
    time, and per line the commit that last wrote it (blame), flagged when that commit is the file's first."""
    root = repo_root(path)
    if not root:
        return {"in_git": False}
    rel = os.path.relpath(os.path.realpath(path), root)
    log = git(root, "log", "--follow", "--format=@%H|%aI|%ae", "--numstat", "--", rel)
    if not log or not log.strip():
        return {"in_git": True, "tracked": False}
    commits, removed = [], 0
    for ln in log.splitlines():
        if ln.startswith("@"):
            h, when, email = ln[1:].split("|", 2)
            commits.append((h, when, email))
        else:
            cols = ln.split("\t")
            if len(cols) >= 2 and cols[1].isdigit():
                removed += int(cols[1])
    first = commits[-1][0]
    blame = {}
    out = git(root, "blame", "--line-porcelain", "-w", "--", rel, timeout=GIT_TIMEOUT * 2)
    if out:
        cur, when = None, None
        for ln in out.splitlines():
            m = re.match(r"^([0-9a-f]{40}) \d+ (\d+)", ln)
            if m:
                cur, line_no = m.group(1), int(m.group(2))
            elif ln.startswith("author-time "):
                when = int(ln.split()[1])
            elif ln.startswith("\t") and cur:
                blame[line_no] = {"commit": cur[:10], "first": cur == first, "uncommitted": set(cur) == {"0"},
                                  "when": datetime.fromtimestamp(when, timezone.utc).date().isoformat() if when else None}
    return {"in_git": True, "tracked": True, "commits": len(commits), "first": commits[-1][1][:10],
            "last": commits[0][1][:10], "authors": len({c[2] for c in commits}), "lines_removed": removed, "_blame": blame}


def block_history(bl, hist, edits, file_text):
    """Evidence for the 'earned' lens on one block: the commits that wrote its lines (later than the file's first?)
    and the session edits whose added text holds one of its sentences (with what the person typed before)."""
    ev = []
    b = hist.get("_blame") or {}
    rows = [b[n] for n in range(bl["start"], bl["end"] + 1) if n in b]
    if rows:
        later = [r for r in rows if not r["first"] and not r["uncommitted"]]
        ev.append({"id": "G", "git": ("added after the file's first commit, on " + max(r["when"] for r in later)) if later
                   else ("not committed yet" if all(r["uncommitted"] for r in rows) else f"in the file's first commit ({rows[0]['when']})")})
    sentences = [squash(s) for s in re.split(r"(?<=[.!?])\s+|\n", bl["text"]) if len(squash(s)) >= 30]
    hits = []
    for e in edits or []:
        added = squash(e.get("added") or "")
        if any(s[:120] in added for s in sentences):
            hits.append(e)
    hits.sort(key=lambda e: e.get("when") or "")
    for k, e in enumerate(hits[:2], 1):
        ev.append({"id": f"S{k}", "session": (e.get("session") or "")[:8], "when": (e.get("when") or "")[:10], "tool": e.get("tool"),
                   "by": "a sub-agent" if e.get("sidechain") else "the main session",
                   "typed_before": e.get("typed_before"), "minutes_after_typed": e.get("minutes_after_typed")})
    if hits:
        ev.append({"id": "S#", "sessions_that_wrote_it": len({e.get("session") for e in hits})})
    return ev


def structure(bls, text):
    """How the file is laid out, for the structure column. Only plus signals are reported as such; a file without
    them loses nothing. XML tags are the owner's plus (11-12); the guide (context-engineering § Order inside a text,
    § Orientation) asks for steps before reference, one idea under one heading, and an opening that says what the
    file is for and links by task."""
    kinds = [b["kind"] for b in bls]
    headings = [b for b in bls if b["kind"] == "heading"]
    tags = sorted({b["tag"] for b in bls if b["kind"] == "tag" and not b["closing"]})
    inline_tags = sorted({m.group(1) for m in XML_INLINE.finditer(setup_map.CODE_SPAN.sub(" ", text))} - {"br", "details", "summary", "kbd", "sup", "sub", "b", "i", "a", "span", "div", "p"})
    numbered = [b for b in bls if b["kind"] == "bullet" and re.match(r"^\s*\d+[.)]", b["text"])]
    first_body = next((b for b in bls if b["kind"] not in ("heading", "tag", "comment")), None)
    opening = bool(first_body and first_body["start"] <= 6 and re.search(r"(?i)\b(this (file|folder|repo|page)|holds|is for|rules for|here)\b", first_body["text"]))
    task_headings = [b["text"] for b in headings if re.search(r"\?\s*$|^#+\s*(When|If|Before|After|How to|To )", b["text"])]
    plus = []
    if tags or inline_tags:
        plus.append("XML tags: " + ", ".join(sorted(set(tags + inline_tags))[:6]))
    if numbered:
        plus.append(f"numbered steps ({len(numbered)})")
    if opening:
        plus.append("opens with what it is for")
    if task_headings:
        plus.append(f"headings named by the reader's task ({len(task_headings)})")
    order = None
    if numbered:
        first_num = numbered[0]["start"]
        ref_before = [b for b in bls if b["kind"] in ("bullet", "para", "table-row") and b["start"] < first_num and b not in numbered]
        order = "steps first" if len(ref_before) <= 2 else "steps after reference"
    return {"sections": [setup_map.HEADING.match(h["text"]).group(2)[:60] for h in headings][:20],
            "xml_tags": sorted(set(tags + inline_tags)), "tables": sum(1 for k in kinds if k == "table-row"),
            "bullets": kinds.count("bullet"), "paragraphs": kinds.count("para"), "code_blocks": kinds.count("code"),
            "comments": kinds.count("comment"), "numbered_steps": len(numbered), "order": order,
            "opens_with_purpose": opening, "task_headings": len(task_headings), "plus": plus}


def local_plus(path, root):
    """A private local instruction file beside this one, or this one (the owner, 11-12: "using CLAUDE.local.md is such
    a strong move"): a plus that can only add. It counts when the file exists, git does not track it (or ignores it),
    and it holds instructions of its own (a line that is not a heading). Whether sessions loaded it is read from
    Claude Code's records, not here (PRODUCT.md)."""
    d = os.path.dirname(path)
    lp = path if os.path.basename(path) == "CLAUDE.local.md" else os.path.join(d, "CLAUDE.local.md")
    if not os.path.isfile(lp):
        return {"exists": False}
    text = read_text(lp) or ""
    own = [b for b in blocks(text) if b.get("judge") and b["kind"] != "heading"]
    tracked = ignored = None
    if root:
        rel = os.path.relpath(os.path.realpath(lp), root)
        tracked = git(root, "ls-files", "--error-unmatch", "--", rel) is not None
        ignored = git(root, "check-ignore", "-q", "--", rel) is not None
    private = tracked is not True
    out = {"exists": True, "tracked": tracked, "ignored": ignored, "instructions": len(own)}
    if private and own:
        out["plus"] = (f"a private CLAUDE.local.md ({len(own)} instruction{'s' if len(own) != 1 else ''}"
                       + (", git-ignored)" if ignored else ", not in git)" if root else ")"))
    return out


def read_file(path, root=None, edits=None, home=None, with_history=True, view=None):
    """Code's read of one instruction file: everything the AI reader gets besides the guide's questions.
    `view` = repo_view(root), when the caller reads several files of one repo; `with_history=False` leaves out git's blame
    per line (the free read of the setup map does not need it)."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    path = os.path.abspath(path)
    text = read_text(path)
    if text is None:
        return {"path": path, "error": "unreadable"}
    root = os.path.realpath(root) if root else repo_root(path)
    if not root or os.path.dirname(path) in (home, os.path.join(home, ".claude")) or root == home:
        root = None   # a user-level file has no repo to check against (a home folder kept in git is no project)
    facts, files = (view if view and root else (repo_facts(root), all_files(root)) if root else (repo_facts(None), []))
    hist = history(path) if with_history else {"in_git": None}
    mine = [e for e in (edits or {}).get("edits", []) if os.path.realpath(e.get("target", "")) == os.path.realpath(path)]
    bls = blocks(text)
    for bl in bls:
        if bl["kind"] in ("tag", "comment"):
            continue
        bl.update(cues(bl, facts, path, root, home, files))
        bl["history"] = block_history(bl, hist, mine, text)
    named = {b["id"]: set(b.get("_named") or []) for b in bls if b.get("judge")}
    for b in bls:
        if not b.get("judge"):
            continue
        same = [o for o, s in named.items() if o != b["id"] and len(s & named[b["id"]]) >= 3]
        if same:
            b["repeats"] = same
        b.pop("_named", None)
    for b in bls:
        b.pop("_named", None)
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    chain = imports_chain(path, home)
    all_ptrs = [p for b in bls for p in b.get("pointers", [])]
    used = set()
    checks = [c for b in bls for c in b.get("commands", []) if c["checks_work"]]
    if any(p["kind"] == "import" for p in all_ptrs) or chain:
        used.add("import")
    if all_ptrs:
        used.add("pointer")
    if any(not p["exists"] and not p["raw"].endswith("/") for p in all_ptrs):
        used.add("dead-pointer")
    if any(p.get("kind") == "link" for p in all_ptrs):
        used.add("link-as-task")
    for b in bls:
        c = b.get("cues") or {}
        if b.get("restates_repo"):
            used.add("restates-repo")
        for key, tid in (("reason", "reason"), ("absolute", "absolute"), ("emphasis", "emphasis"), ("refs", "refs"),
                         ("slash", "slash-command"), ("dated", "stale")):
            if c.get(key):
                used.add(tid)
        for key, tid in (("example", "example"), ("done", "done"), ("compaction", "compaction"),
                         ("negation_without_target", "negation")):
            if c.get(key):
                used.add(tid)
        if any(not v["same"] for v in b.get("versions", [])) or any(x["exists"] is False for x in b.get("commands", [])):
            used.add("stale")
        if b.get("enforced_by_hook"):
            used.add("enforced")
        if b.get("repeats"):
            used.add("duplicate")
        if b["kind"] == "comment":
            used.add("html-comment")
    if checks:
        used.add("check-command")
    sib = sibling(path)
    if sib:
        used.add("agents-claude")
        if sib.get("shared_lines") or sib.get("shared_pointers"):
            used.add("duplicate")
    if os.path.basename(path) == "CLAUDE.local.md":
        used.add("local")
    if path.startswith(os.path.join(home, ".claude") + os.sep) or os.path.dirname(path) == home:
        used.add("user-level")
    nested = nested_below(path, root, files)
    if nested:
        used.add("nested")
    if facts["rules"]:
        used.add("path-rule")
    st = structure(bls, text)
    lp = local_plus(path, root)
    if lp.get("plus"):
        st["plus"].append(lp["plus"])
        used.add("local")
    st["local"] = lp
    if st["plus"]:
        used.add("structure")
    return {
        "schema": "instruction-read/1", "path": path, "display": display(path, root, home), "root": root and display(root, home, home),
        "lines": lines, "words": len(text.split()), "chars": len(text), "tokens_est": len(text) // 4,
        "loads": loads(path, home), "every_session": every_session(path, home), "imports": [
            {**i, "path": display(i["path"], root, home)} for i in chain],
        "sibling": sib, "nested_below": nested[:30], "nested_count": len(nested),
        "rules": facts["rules"], "skills": facts["skills"], "agents": facts["agents"], "hooks": sorted(facts["hooks"]),
        "repo": {"manifests": facts["manifests"], "tools": facts["tools"], "scripts": sorted(facts["scripts"])[:40],
                 "check_scripts": sorted(s for s, v in facts["scripts"].items() if CHECK_WORDS.search(s) or CHECK_WORDS.search(v))[:20],
                 "folders": facts["folders"][:40]},
        "git": {k: v for k, v in hist.items() if not k.startswith("_")},
        "session_edits": {"edits": len(mine), "sessions": len({e.get("session") for e in mine}),
                          "first": min((e.get("when") or "" for e in mine), default=None)},
        "structure": st, "techniques": sorted(used), "blocks": bls,
        "dead_pointers": [p for p in all_ptrs if not p["exists"] and not p["raw"].endswith("/")],
        "missing_folders": [p for p in all_ptrs if not p["exists"] and p["raw"].endswith("/")],
        "check_commands": checks,
    }


# ------------------------------------------------------------------ what the reader is asked, and the check of its quotes

KINDS = ("describes", "points", "steers", "guards", "verifies", "preference", "structure")


def _block_line(b):
    parts = []
    if b.get("found"):
        parts.append("found: " + "; ".join(f"{k} ({v})" for k, v in list(b["found"].items())[:5]))
    if b.get("named_not_found"):
        parts.append("named, not in the repo: " + ", ".join(b["named_not_found"][:5]))
    for v in b.get("versions", []):
        parts.append(f"version {v['said']} vs {v['repo']}: {'same' if v['same'] else 'DIFFERENT'}")
    for p in b.get("pointers", []):
        parts.append(f"ptr {p['raw']} ({p['kind']}): " + ("exists" if p["exists"] else "MISSING" + (f", closest {p['near']}" if p.get("near") else "")))
    for c in b.get("commands", []):
        parts.append(f"cmd `{c['cmd']}`: " + {True: "in the repo", False: "NOT in the repo", None: "can't check"}[c["exists"]]
                     + (", checks the work" if c["checks_work"] else ""))
    for e in b.get("enforced_by_hook", []):
        parts.append(f"a hook also enforces it: {e['hook']} mentions '{e['word']}'")
    if b.get("restates_repo"):
        parts.append("code: restates the repo")
    if b.get("repeats"):
        parts.append("repeats " + ", ".join(b["repeats"]))
    c = b.get("cues") or {}
    hints = [k for k in ("situation", "reason", "absolute", "emphasis", "dated") if c.get(k)]
    if hints:
        parts.append("cues: " + ", ".join(f"{k} {c[k][:3]}" for k in hints))
    for h in b.get("history", []):
        if h["id"] == "G":
            parts.append(f"hist G: {h['git']}")
        elif h["id"].startswith("S") and h["id"] != "S#":
            parts.append(f"hist {h['id']}: written by {h['tool']} in session {h['session']} on {h['when']} by {h['by']}"
                         + (f"; {h['minutes_after_typed']} min after the person typed: \"{h['typed_before'][:200]}\"" if h.get("typed_before") else ""))
    return parts


def quote_in(text, quote, start=None, end=None):
    """The quote check of setup_read.py: the quote (whitespace squashed) sits in the file, on lines that hold no
    secret; when a block's lines are given, it must sit within them. Short blocks may be quoted whole."""
    import setup_read  # noqa: PLC0415  imported here because setup_read imports this module
    if not quote or not isinstance(quote, str):
        return False, "no quote"
    want = squash(quote).strip("`\"'“”")
    if setup_map.has_secret(want):
        return False, "secret"
    lines = text.split("\n")
    scope = "\n".join(lines[start - 1:end]) if start else text
    if len(want) < setup_read.QUOTE_MIN_CHARS and squash(scope) != want and squash(re.sub(r"[*_`#>-]", " ", scope)) != squash(want):
        if len(squash(scope)) >= setup_read.QUOTE_MIN_CHARS:
            return False, "too short"
    hay = squash(scope)
    if want not in hay and want not in squash(re.sub(r"\*\*|__|`", "", scope)):
        return False, "not in the block" if start else "not in the file"
    held = setup_read.lines_holding(scope, want) or setup_read.lines_holding(re.sub(r"\*\*|__|`", "", scope), want)
    if any(setup_map.has_secret(x) for x in held):
        return False, "secret"
    return True, None

