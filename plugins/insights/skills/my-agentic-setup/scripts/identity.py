#!/usr/bin/env python3
"""Who the person is in git: every email their git config can commit with, before anything is scored.

Skills, rules, plugins and commits count as theirs only when git says they wrote them, so the identity has to
be right first. `git config --global user.email` alone is not enough:
  - `--global` ignores [include] and [includeIf] files, where many people keep their email (seen 2026-10-22:
    one person had no global email at all, only includes);
  - people split work and personal by folder with [includeIf "gitdir:~/work/"] (a tester's global email was her
    personal one; her work email, on every commit and skill she made, applied only under her work folder).

So: the global config files and every file they include (conditional or not), each repo's own config, and
then the authors of commits that carry a Claude co-author line (whoever commits with Claude is the one at the
keyboard). The skill shows the list and asks the person to confirm it; the confirmed list is authoritative.

Run: python3 -I identity.py [--out identity.json]       prints the emails git config knows, with where each came from
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

HOME = Path.home()
CLAUDE_TRAILER = re.compile(r"co-authored-by:\s*claude|generated with \[claude code\]", re.I)


def git(*args, cwd: Path | None = None, timeout=15) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _clean(e: str) -> str:
    e = e.strip().lower()
    return e if "@" in e and "noreply@anthropic" not in e else ""


NAMES: set[str] = set()   # user.name values the config holds, filled by config_emails()


def config_emails() -> dict[str, set[str]]:
    """email -> where it came from, from the user-level config files and everything they include."""
    out: dict[str, set[str]] = defaultdict(set)
    seen: set[str] = set()

    def walk(f: Path, how: str, depth: int = 0):
        key = str(f.resolve()) if f.exists() else str(f)
        if depth > 4 or key in seen or not f.is_file():
            return
        seen.add(key)
        for e in git("config", "--file", str(f), "--get-all", "user.email").splitlines():
            if _clean(e):
                out[_clean(e)].add(how)
        for nm in git("config", "--file", str(f), "--get-all", "user.name").splitlines():
            if nm.strip():
                NAMES.add(nm.strip().lower())
        # git parses the includes for us (comments, quoting); a gitdir condition can't be evaluated without a repo,
        # so every conditional file is read: it is still an email this person commits with somewhere
        for line in git("config", "--file", str(f), "--get-regexp", r"^include(if\..*)?\.path$").splitlines():
            k, _, path = line.partition(" ")
            cond = k[len("includeif."):-len(".path")] if k.startswith("includeif.") else ""
            p = Path(os.path.expanduser(path.strip()))
            if not p.is_absolute():
                p = f.parent / p
            walk(p, f"included when {cond}" if cond else "included config", depth + 1)

    walk(HOME / ".gitconfig", "global config")
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "git" / "config"
    walk(xdg, "global config")
    return out


def repo_emails(roots: list[Path]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Each repo's own email (git evaluates [includeIf] here, inside the repo), and the authors of commits made
    with Claude in it, with their names. Teammates commit with Claude too (seen 2026-10-23: a colleague's work
    email came back from a shared repo), so a Claude-commit author is only theirs when the name matches."""
    out: dict[str, set[str]] = defaultdict(set)
    names: dict[str, set[str]] = defaultdict(set)
    for root in roots:
        if not root.exists():
            continue
        e = _clean(git("config", "user.email", cwd=root))
        if e:
            out[e].add("a repo's own config")
        nm = git("config", "user.name", cwd=root).strip().lower()
        if nm:
            NAMES.add(nm)
        for rec in git("log", "--all", "--since=60.days", "-n", "400", "--format=%ae%x09%an%x09%b%x1e", cwd=root).split("\x1e"):
            if CLAUDE_TRAILER.search(rec):
                parts = rec.strip().split("\t", 2)
                a = _clean(parts[0])
                if a:
                    out[a].add("commits made with Claude")
                    names[a].add((parts[1] if len(parts) > 1 else "").strip().lower())
    return out, names


def collect(roots: list[Path], confirmed: list[str] | None = None) -> dict:
    """The identity every later check uses. A confirmed list (the person said 'these are me') wins; emails found
    in Claude commits are added beside it, marked, because they're found in their own repos."""
    found = config_emails()
    repo, names = repo_emails(roots)
    for e, how in repo.items():
        found[e] |= how
    from_config = {e for e, how in found.items() if how - {"commits made with Claude"}}
    # a Claude-commit author whose name is one of theirs is them under another email; anyone else is only asked about
    same_name = {e for e in found if e not in from_config and names.get(e, set()) & NAMES}
    others = sorted(e for e in found if e not in from_config and e not in same_name)
    base = {_clean(e) for e in confirmed if _clean(e)} if confirmed else from_config
    emails = sorted(base | same_name)
    return {"emails": emails, "confirmed": bool(confirmed),
            "sources": {e: sorted(found.get(e, {"confirmed by the person"})) for e in emails},
            # seen committing with Claude in their repos, name doesn't match: shown as "is this you?", never counted
            "ask_about": others}


def authored_share(path: Path, emails: set[str]) -> tuple[float, int]:
    """Share of the commits touching `path` (a link is followed first) made by the person, and how many."""
    real = Path(os.path.realpath(path))
    top = git("rev-parse", "--show-toplevel", cwd=real if real.is_dir() else real.parent).strip()
    if not top:
        return (0.0, 0)
    rel = os.path.relpath(real, top)
    authors = [a.lower() for a in git("log", "--format=%ae", "--", rel, cwd=Path(top)).split() if a]
    return ((sum(a in emails for a in authors) / len(authors)) if authors else 0.0, len(authors))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="write the list here as {'emails': [...]} for the person to confirm")
    a = ap.parse_args()
    found = config_emails()
    for e in sorted(found):
        print(f"{e}  ({', '.join(sorted(found[e]))})")
    if not found:
        print("(no email in git config)")
    if a.out:
        Path(a.out).write_text(json.dumps({"emails": sorted(found)}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
