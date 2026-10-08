"""Where the kit comes from: the release clone on disk, and the URL it is cloned from.

The release clone is the marketplace Claude Code installs from. repo_url() answers "clone what?" without
a hard-coded address: under `uvx --from git+<url>#subdirectory=plugins/ak` the tool's own install
record (PEP 610 direct_url.json) names the repo it was built from.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ak._brand import RELEASE_DIR, REPO_DIR, data_dir, env

DIST = "ak"
GIT_TIMEOUT = 5


def release_dir() -> Path:
    """<data dir>/ak/release — where the release clone lives (and where `ak setup` puts it)."""
    return Path(data_dir()) / RELEASE_DIR


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


def repo_url(explicit: str | None = None) -> str | None:
    """What to `git clone` the release from: --repo, $AK_REPO_URL, the repo this tool was installed from,
    the release clone's own origin, the checkout at ~/agentic-kit. None when none of them says."""
    if explicit:
        return explicit
    if env("REPO_URL"):
        return env("REPO_URL")
    release = release_dir()
    installed = _installed_from()
    if installed and Path(installed) != release:  # an editable install of the clone itself says nothing new
        return installed
    origin = clone_origin(release)
    if origin:
        return origin
    checkout = Path.home() / REPO_DIR
    return str(checkout) if (checkout / ".git").exists() else None
