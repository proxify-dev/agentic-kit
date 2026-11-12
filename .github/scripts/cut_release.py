#!/usr/bin/env python3
"""Cut a GitHub Release for every plugin whose version changed in a push to main. Standard library only.

A release is a version bump merged to main: a plugin's `.claude-plugin/plugin.json` `version` differs between
the push's BEFORE and AFTER commits. For each such plugin this makes the tag `<name>--v<version>` (what
`claude plugin tag` makes) and a GitHub Release titled `<name> <version>`. The body is that version's section of
`plugins/<folder>/CHANGELOG.md` (the lines under `## <version> — <YYYY-MM-DD>`), then the list of merged pull
requests GitHub generates since that plugin's previous tag, which is where contributors are credited.
GitHub lists the repository's merged pull requests, not only the plugin's.

    cut_release.py [--before SHA] [--after SHA] [--repo OWNER/NAME] [--root DIR] [--dry-run]

Defaults come from the environment the workflow sets: BEFORE_SHA, AFTER_SHA, GITHUB_REPOSITORY, GITHUB_TOKEN.

Order: plan every plugin first, then create. Any problem in the plan (a version with no changelog section) exits
1 before anything is created. A tag that already exists is skipped, so a re-run is safe. `--dry-run` prints the
plan and touches nothing: no network, no token.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

NO_COMMIT = "0" * 40  # the BEFORE of a push that creates the branch
MANIFEST = ".claude-plugin/plugin.json"
API = "https://api.github.com"


class Problem(Exception):
    """Something that makes the whole run fail before anything is created."""


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        raise Problem(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r


def version_at(root: Path, rev: str | None, path: str) -> str | None:
    """The `version` of a plugin.json at a commit; None when the commit or the file is not there."""
    if rev is None:
        return None
    r = git(root, "show", f"{rev}:{path}", check=False)
    if r.returncode:
        return None
    try:
        return json.loads(r.stdout).get("version")
    except json.JSONDecodeError as e:
        raise Problem(f"{path} at {rev[:10]} is not JSON: {e}")


def changed_plugins(root: Path, before: str | None, after: str) -> list[dict]:
    """Every plugin whose plugin.json version differs between before and after: name, folder, version."""
    if before is not None and git(root, "cat-file", "-e", f"{before}^{{commit}}", check=False).returncode:
        raise Problem(f"the commit before the push ({before[:10]}) is not in this clone; fetch the full history")
    listing = git(root, "ls-tree", "--name-only", f"{after}:plugins", check=False).stdout.split()
    found = []
    for folder in sorted(listing):
        path = f"plugins/{folder}/{MANIFEST}"
        now = version_at(root, after, path)
        if now is None or now == version_at(root, before, path):
            continue
        name = json.loads(git(root, "show", f"{after}:{path}").stdout).get("name") or folder
        found.append({"name": name, "folder": folder, "version": now})
    return found


def changelog_section(root: Path, after: str, folder: str, version: str) -> str:
    """The text under `## <version> — <date>` up to the next `## ` heading. Problem when there is none."""
    path = f"plugins/{folder}/CHANGELOG.md"
    r = git(root, "show", f"{after}:{path}", check=False)
    if r.returncode:
        raise Problem(f"{folder} {version}: {path} does not exist")
    heading = re.compile(rf"^## {re.escape(version)} — \d{{4}}-\d{{2}}-\d{{2}}\s*$")
    lines, body, inside = r.stdout.splitlines(), [], False
    for line in lines:
        if inside and line.startswith("## "):
            break
        if inside:
            body.append(line)
        elif heading.match(line):
            inside = True
    text = "\n".join(body).strip()
    if not inside or not text:
        raise Problem(f"{folder} {version}: {path} has no `## {version} — YYYY-MM-DD` section"
                      + ("" if not inside else " with text under it"))
    return text


def semver_key(version: str) -> tuple:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$", version)
    if not m:
        return (-1,)
    return (int(m[1]), int(m[2]), int(m[3]), m[4] is None, m[4] or "")


def previous_tag(root: Path, name: str, version: str) -> str | None:
    """The highest `<name>--v*` tag below this version, or None for a first release."""
    prefix = f"{name}--v"
    tags = [t for t in git(root, "tag", "--list", f"{prefix}*").stdout.split() if t.startswith(prefix)]
    below = [t for t in tags if semver_key(t[len(prefix):]) < semver_key(version)]
    return max(below, key=lambda t: semver_key(t[len(prefix):]), default=None)


def plan(root: Path, before: str | None, after: str) -> list[dict]:
    """What a run would do, one item per changed plugin: action 'release' or 'skip', the tag, the previous tag
    and the changelog text. Raises Problem listing every plugin whose changelog section is missing."""
    items, problems = [], []
    existing = set(git(root, "tag", "--list").stdout.split())
    for p in changed_plugins(root, before, after):
        tag = f"{p['name']}--v{p['version']}"
        item = {**p, "tag": tag, "title": f"{p['name']} {p['version']}", "previous": None, "notes": ""}
        if tag in existing:
            items.append({**item, "action": "skip", "why": "the tag already exists"})
            continue
        try:
            item["notes"] = changelog_section(root, after, p["folder"], p["version"])
        except Problem as e:
            problems.append(str(e))
            continue
        items.append({**item, "action": "release", "previous": previous_tag(root, p["name"], p["version"])})
    if problems:
        raise Problem("no release was made:\n  " + "\n  ".join(problems))
    return items


def describe(item: dict) -> str:
    if item["action"] == "skip":
        return f"skip     {item['tag']}: {item['why']}"
    since = item["previous"] or "none (first release: GitHub lists every merged pull request)"
    return (f"release  {item['tag']}  title {item['title']!r}  pull requests since {since}\n"
            f"         notes: {item['notes'].splitlines()[0]}")


def api(token: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(f"{API}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def create(item: dict, repo: str, after: str, token: str, call=api) -> str:
    """Make the Release (GitHub makes the tag at `after` with it). Returns a line saying what happened."""
    tag = item["tag"]
    status, _ = call(token, "GET", f"/repos/{repo}/git/ref/tags/{tag}")
    if status == 200:
        return f"skip     {tag}: the tag already exists on GitHub"
    if status != 404:
        raise Problem(f"{tag}: looking for the tag answered {status}")
    ask = {"tag_name": tag, "target_commitish": after}
    if item["previous"]:
        ask["previous_tag_name"] = item["previous"]
    status, generated = call(token, "POST", f"/repos/{repo}/releases/generate-notes", ask)
    if status != 200:
        raise Problem(f"{tag}: generating the pull request list answered {status}: {generated.get('message')}")
    body = f"{item['notes']}\n\n{generated.get('body', '').strip()}".strip() + "\n"
    status, made = call(token, "POST", f"/repos/{repo}/releases",
                        {**ask, "name": item["title"], "body": body, "make_latest": "false"})
    if status != 201:
        raise Problem(f"{tag}: creating the release answered {status}: {made.get('message')}")
    return f"created  {tag}: {made.get('html_url')}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="cut_release.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--before", default=os.environ.get("BEFORE_SHA"), help="the commit before the push")
    ap.add_argument("--after", default=os.environ.get("AFTER_SHA") or "HEAD", help="the commit pushed (default HEAD)")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="OWNER/NAME")
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="the clone (default: the current folder)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; create nothing, call nothing")
    args = ap.parse_args(argv)
    try:
        after = git(args.root, "rev-parse", f"{args.after}^{{commit}}").stdout.strip()
        before = None if not args.before or args.before == NO_COMMIT else args.before
        if before is None and not args.dry_run and args.before is None:
            raise Problem("no --before (BEFORE_SHA): which commit did the push start from?")
        items = plan(args.root, before, after)
        for item in items:
            print(describe(item))
        if not items:
            print("no plugin's version changed: nothing to release")
        if args.dry_run:
            print("dry run: nothing was created")
            return 0
        token = os.environ.get("GITHUB_TOKEN")
        if any(i["action"] == "release" for i in items) and not (token and args.repo):
            raise Problem("GITHUB_TOKEN and GITHUB_REPOSITORY (or --repo) are needed to create a release")
        for item in items:
            if item["action"] == "release":
                print(create(item, args.repo, after, token))
    except Problem as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
