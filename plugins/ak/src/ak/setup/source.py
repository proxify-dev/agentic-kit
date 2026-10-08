"""Where the kit comes from: a URL Claude Code clones itself, or the release clone on disk.

repo_url() answers "the kit comes from where?" without a hard-coded address: under
`uvx --from git+<url>#subdirectory=plugins/ak` the tool's own install record (PEP 610 direct_url.json)
names the repo it was built from.

A kit at a URL Claude Code can clone (github owner/repo, https, http, ssh) is added as that marketplace:
Claude Code keeps the clone at <claude home>/plugins/marketplaces/<name> and updates it. A local path or a
file:// URL (`claude plugin marketplace add` refuses file://) is cloned to the release clone, which Claude Code
reads as a local folder and never updates.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ak._brand import RELEASE_DIR, REPO_DIR, claude_home, data_dir, env

DIST = "ak"
GIT_TIMEOUT = 5
# What `claude plugin marketplace add` clones itself: http(s), ssh, and git@host:path (git+ is uv's prefix, dropped)
CLONED_BY_CLAUDE = re.compile(r"^(?:git\+)?(?:https?|ssh)://|^[\w.-]+@[\w.-]+:(?!//)", re.I)
GITHUB = re.compile(r"^(?:(?:git\+)?(?:https?|ssh)://(?:[\w.-]+@)?(?:www\.)?github\.com/|[\w.-]+@github\.com:)"
                    r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", re.I)
GIT_SOURCES = ("github", "git")  # a marketplace listing's "source" for a clone Claude Code keeps


def release_dir() -> Path:
    """<data dir>/ak/release — where the release clone lives (and where `ak setup` puts it)."""
    return Path(data_dir()) / RELEASE_DIR


def cloned_by_claude(where: str | None) -> bool:
    """True for a URL `claude plugin marketplace add` clones itself (and keeps updated); False for a path or file://."""
    return bool(where) and bool(CLONED_BY_CLAUDE.match(where))


def git_url(url: str) -> str:
    """The URL git understands: uv's git+ prefix dropped."""
    return url[4:] if url.lower().startswith("git+") else url


def github_repo(url: str | None) -> str | None:
    """"owner/repo" for a github.com URL (https, ssh or git@github.com:…), else None."""
    found = GITHUB.match(url or "")
    return f"{found.group(1)}/{found.group(2)}" if found else None


def marketplace_source(url: str) -> str:
    """What `claude plugin marketplace add` is given: owner/repo for GitHub, else the git URL."""
    return github_repo(url) or git_url(url)


def settings_source(url: str) -> dict:
    """The source `claude plugin marketplace add` writes into extraKnownMarketplaces.<name>: github or git."""
    repo = github_repo(url)
    return {"source": "github", "repo": repo} if repo else {"source": "git", "url": git_url(url)}


def _bare(url: str) -> str:
    return re.sub(r"(?:\.git)?/*$", "", git_url(url).strip()).lower()


def same_source(row: dict, url: str) -> bool:
    """A `claude plugin marketplace list --json` row (source github: repo; git: url) is a clone of url."""
    if row.get("source") == "github":
        return (row.get("repo") or "").lower() == (github_repo(url) or "").lower()
    if row.get("source") == "git":
        theirs = row.get("url") or ""
        return _bare(theirs) == _bare(url) or (github_repo(theirs) or "x").lower() == (github_repo(url) or "y").lower()
    return False


def url_of(row: dict) -> str | None:
    """The URL a marketplace listing row was cloned from: https://github.com/<repo>, or its git URL."""
    if row.get("source") == "github" and row.get("repo"):
        return f"https://github.com/{row['repo']}"
    return row.get("url") if row.get("source") == "git" else None


def marketplace_clone(name: str) -> Path:
    """<claude home>/plugins/marketplaces/<name>: where Claude Code keeps a marketplace it cloned."""
    return Path(claude_home()) / "plugins" / "marketplaces" / name


def git_line(path: Path | str, *args: str) -> str | None:
    """First line `git -C path args` prints, or None (not a repo, git missing, slow). Never fetches."""
    try:
        p = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, encoding="utf-8",
                           timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    out = p.stdout.strip().splitlines()
    return out[0] if p.returncode == 0 and out else None


def clone_origin(path: Path | str) -> str | None:
    """The `origin` URL of the clone at path, or None."""
    return git_line(path, "remote", "get-url", "origin") if (Path(path) / ".git").exists() else None


def _installed_from() -> str | None:
    """The repo ak was installed from, read from its PEP 610 record; None for a wheel from an index."""
    from importlib import metadata
    from urllib.parse import unquote, urlparse
    try:
        record = json.loads(metadata.distribution(DIST).read_text("direct_url.json") or "")
    except (metadata.PackageNotFoundError, ValueError, OSError):
        return None
    url = record.get("url") or ""
    if "vcs_info" in record:  # a git install: the repo itself; "#subdirectory=…" is not part of the address
        return url.split("#", 1)[0] or None
    u = urlparse(url)
    if u.scheme != "file":
        return None
    here = Path(unquote(u.path))
    if len(here.parts) > 3 and here.parts[-2:] == ("plugins", "ak"):  # a path install of plugins/ak: the repo is two up
        return str(here.parent.parent)
    return None


def _in_claudes_clones(path: str) -> bool:
    try:
        return Path(path).resolve().parent == marketplace_clone("x").parent.resolve()
    except OSError:
        return False


def repo_url(explicit: str | None = None) -> str | None:
    """Where the kit comes from: --repo, $AK_REPO_URL, the repo this tool was installed from (when that is
    Claude Code's clone of a marketplace, its origin), the release clone's own origin, the checkout at
    ~/agentic-kit. None when none of them says."""
    if explicit:
        return explicit
    if env("REPO_URL"):
        return env("REPO_URL")
    release = release_dir()
    installed = _installed_from()
    if installed and _in_claudes_clones(installed):  # ak installed from Claude Code's clone: the URL it clones
        return clone_origin(installed) or installed
    if installed and Path(installed) != release:  # an editable install of the clone itself says nothing new
        return installed
    origin = clone_origin(release)
    if origin:
        return origin
    checkout = Path.home() / REPO_DIR
    return str(checkout) if (checkout / ".git").exists() else None
