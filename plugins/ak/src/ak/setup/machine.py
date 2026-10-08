"""What this machine is and holds — read only, every probe bounded by a timeout.

The engine and the TUI only ever see a Machine; tests build one by hand.
"""
from __future__ import annotations

import dataclasses
import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ak._brand import CLI, GATEWAY_ACCOUNTS, claude_home, config_dir, data_dir, is_macos, is_windows, which_exe
from ak.setup import source
from ak.setup.claude_cli import outside_a_session


@dataclass(frozen=True)
class Bin:
    """One binary on PATH: where it is and the version it reports (None when it reports none)."""
    path: str | None
    version: str | None = None  # "22.13.0", "2.1.286" — digits and dots only

    @property
    def found(self) -> bool:
        return self.path is not None


@dataclass(frozen=True)
class Release:
    """The release clone (<data dir>/ak/release): the marketplace Claude Code installs from."""
    path: str
    exists: bool = False
    head: str | None = None
    origin: str | None = None


@dataclass(frozen=True)
class Machine:
    os: str                                    # "macos" | "linux" | "wsl" | "windows"
    arch: str                                  # platform.machine(): "arm64", "x86_64", "AMD64"
    in_claude: bool = False                    # CLAUDECODE is set: an agent, not a person
    tty: bool = False
    bins: dict[str, Bin] = field(default_factory=dict)  # setup's own tools and what the kit's components need (binaries())
    service_manager: str | None = None         # "launchd" | "systemd" | "schtasks" | None
    release: Release | None = None
    marketplaces: dict[str, str] = field(default_factory=dict)  # name -> source path/url
    marketplace_rows: dict[str, dict] = field(default_factory=dict)  # name -> its `marketplace list --json` row
    plugins: dict[str, dict] = field(default_factory=dict)      # "<p>@<mkt>" -> {"version", "enabled", "scope"}
    uv_tools: list[str] = field(default_factory=list)           # `uv tool list` package names
    ak_on_path: bool = False
    local_bin_on_path: bool = False            # ~/.local/bin (where uv tools land) is on PATH
    shim: dict = field(default_factory=dict)       # `cc-shim status --json`, {} when absent
    gateway: dict = field(default_factory=dict)    # {"accounts": bool, "on": bool, ...}
    settings: dict = field(default_factory=dict)   # user settings.json, parsed ({} when absent)
    uv_tool_exes: dict[str, list[str]] = field(default_factory=dict)  # uv tool -> the executables it puts on PATH
    history: tuple[int, int] | None = None     # Claude Code's transcripts (<claude home>/projects): (bytes, sessions)
    unknown: frozenset[str] = frozenset()      # probes that failed: their field says "nothing", not "nothing there"

    def bin(self, name: str) -> Bin:
        return self.bins.get(name) or Bin(None)


PROBE_TIMEOUT = 5
CLAUDE_PROBE_FACTOR = 4  # claude's JSON listings start a whole runtime: a cold start outlasts the others
SETUP_BINS = ("uv", "git", "claude")  # what setup itself runs: the clone, the marketplace, the ak tool
# How a binary is asked for its version. None: it has none to give — it is only looked for.
VERSION_ARGV = {"tmux": ["-V"], "osascript": None}  # portable: ok (notify's macOS tool)


def binaries() -> tuple[str, ...]:
    """The tools to look for: setup's own, then each one a component of this kit says it needs (the needs.bins of
    its setup block). Nothing else is probed, so nothing else is ever called missing."""
    from ak.setup import catalog  # here, not at the top: catalog imports this module
    named = [name for c in catalog.COMPONENTS for name, _ in c.needs.bins]
    return tuple(dict.fromkeys([*SETUP_BINS, *named]))
VERSION = re.compile(r"\d+(?:\.\d+)+")


class ProbeFailed(Exception):
    """The tool is there but would not answer (slow, failing, or nonsense): what it holds is unknown, not empty."""


def _run(argv: list[str], cwd: str | None = None, env_: dict | None = None, timeout: float | None = None) -> str | None:
    """Stdout (stderr when stdout is empty) of a command that exits 0 within the timeout; else None."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout or PROBE_TIMEOUT, cwd=cwd, env=env_, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return (p.stdout.strip() or p.stderr.strip()) if p.returncode == 0 else None


def _claude_json(*args: str) -> list:
    """`claude <args> --json` parsed, run the way the migrations run it: from $HOME, outside a session.
    [] when claude is not installed (it holds nothing); ProbeFailed when it is but is slow or answers something else."""
    exe = which_exe("claude")
    if not exe:
        return []
    out = _run([exe, *args, "--json"], cwd=str(Path.home()), env_=outside_a_session(), timeout=PROBE_TIMEOUT * CLAUDE_PROBE_FACTOR)
    try:
        rows = json.loads(out or "")
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        raise ProbeFailed(f"claude {' '.join(args)} gave no list")
    return rows


def _bin(name: str) -> Bin:
    path = which_exe(name)
    if not path:
        return Bin(None)
    argv = VERSION_ARGV.get(name, ["--version"])
    is_claude = name == "claude"
    out = _run([path, *argv], str(Path.home()) if is_claude else None, outside_a_session() if is_claude else None) if argv else None
    found = VERSION.search(out or "")
    return Bin(path, found.group(0) if found else None)


def _proc_version() -> str:
    try:
        return Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return ""


def _os_name() -> str:
    if is_windows():
        return "windows"
    if is_macos():
        return "macos"
    return "wsl" if "microsoft" in _proc_version() else "linux"


def _service_manager(os_name: str) -> str | None:
    if os_name == "macos":
        return "launchd"
    if os_name == "windows":
        return "schtasks"
    systemctl = which_exe("systemctl")
    return "systemd" if systemctl and _run([systemctl, "--user", "show-environment"]) is not None else None


def _release() -> Release:
    path = source.release_dir()
    if not (path / ".git").exists():
        return Release(str(path))
    return Release(str(path), True, source.git_line(path, "rev-parse", "--short", "HEAD"), source.clone_origin(path))


def _marketplaces() -> dict[str, dict]:
    """name -> its `claude plugin marketplace list --json` row: {"source": "directory"|"github"|"git", "path"|"repo"|"url",
    "installLocation"}."""
    return {row["name"]: row for row in _claude_json("plugin", "marketplace", "list") if isinstance(row, dict) and row.get("name")}


def where_from(row: dict) -> str:
    """Where a marketplace comes from: a path for a directory, "owner/repo" for github, else the url."""
    return str(row.get("path") or row.get("repo") or row.get("url") or row.get("installLocation") or "")


def _plugins() -> dict[str, dict]:
    """"<plugin>@<marketplace>" -> {version, enabled, scope}. Installed at several scopes: the user's wins."""
    out: dict[str, dict] = {}
    for row in _claude_json("plugin", "list"):
        if not isinstance(row, dict) or not row.get("id"):
            continue
        if row["id"] in out and out[row["id"]]["scope"] == "user":
            continue
        out[row["id"]] = {"version": row.get("version"), "enabled": bool(row.get("enabled")), "scope": row.get("scope")}
    return out


def _uv_tools() -> dict[str, list[str]]:
    """`uv tool list` as tool name -> the executables it provides. {} without uv; ProbeFailed when uv won't list."""
    exe = which_exe("uv")
    if not exe:
        return {}
    out = _run([exe, "tool", "list"])
    if out is None:
        raise ProbeFailed("uv tool list failed")
    table: dict[str, list[str]] = {}
    tool = None
    for line in out.splitlines():
        named, provides = re.match(r"(\S+) v\d", line), re.match(r"- (\S+)", line)
        if named:
            tool = named.group(1)
            table[tool] = []
        elif provides and tool:
            table[tool].append(provides.group(1))
    return table


def _shim() -> dict:
    """The installed shim's own `status --json`; {} when it is not installed or node is missing. The older bash
    shim (a `claude` wrapper in its folder, no cc-shim.mjs) has no status of its own: {"present": True, "legacy": True}."""
    node, mjs = which_exe("node"), Path(data_dir()) / "cc-shim" / "cc-shim.mjs"
    if (mjs.parent / "claude").is_file() and not mjs.is_file():
        return {"present": True, "legacy": True}
    if not (node and mjs.is_file()):
        return {}
    try:
        d = json.loads(_run([node, str(mjs), "status", "--json"]) or "")
    except ValueError:
        d = None
    if not isinstance(d, dict):
        raise ProbeFailed("the shim would not report its status")
    return d


def _gateway() -> dict:
    from ak.move import plan as P
    return {"accounts": (Path(config_dir()) / GATEWAY_ACCOUNTS).is_file(), "on": P.gateway_up()}


def _history() -> tuple[int, int] | None:
    """(bytes, sessions) of Claude Code's transcripts: what a plugin's first run reads ("reads": "history").
    A session is a <project>/<id>.jsonl; its subagents' transcripts sit deeper and count as bytes only.
    None when there is no projects folder (Claude Code never ran here)."""
    root = Path(claude_home()) / "projects"
    if not root.is_dir():
        return None
    size = sessions = 0
    for f in root.rglob("*.jsonl"):
        size += f.stat().st_size
        sessions += f.parent.parent == root
    return size, sessions


def _settings() -> dict:
    """The user's settings.json; {} when there is none. A file that is there but unreadable raises: unknown, not empty."""
    try:
        text = (Path(claude_home()) / "settings.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    d = json.loads(text) if text.strip() else {}
    if not isinstance(d, dict):
        raise ValueError("settings.json is not an object")
    return d


def _on_path(folder: Path) -> bool:
    here = os.path.normcase(os.path.realpath(folder))
    return any(here == os.path.normcase(os.path.realpath(p)) for p in os.environ.get("PATH", "").split(os.pathsep) if p)


def _tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _or(default, fn, *args):
    """fn(*args), or default when the probe fails in any way — detect() reports "not found", never raises."""
    try:
        return fn(*args)
    except Exception:
        return default


def detect(on_probe: Callable[[str, object], None] | None = None) -> Machine:
    """Probe this machine. Never writes, never fetches; each probe times out (~5s) to a 'not found'.

    on_probe(name, result) is called as each probe finishes, from the thread that ran it: name is the
    binary's name, or service_manager, release, marketplaces, plugins, uv_tools, shim, gateway, settings, history.
    A callback that raises is ignored. A probe that raises or times out on a tool that is there lands in
    Machine.unknown: its field is then the default, and must not be read as "nothing is installed"."""
    unknown: list[str] = []

    def probe(name: str, default, fn, *args):
        try:
            result = fn(*args)
        except Exception:
            result = default
            unknown.append(name)
        if on_probe:
            _or(None, on_probe, name, result)
        return result

    os_name = _or("linux", _os_name)
    names = binaries()
    with ThreadPoolExecutor(max_workers=len(names) + 8) as pool:
        bins = {n: pool.submit(probe, n, Bin(None), _bin, n) for n in names}
        service = pool.submit(probe, "service_manager", None, _service_manager, os_name)
        release = pool.submit(probe, "release", Release(str(source.release_dir())), _release)
        marketplaces = pool.submit(probe, "marketplaces", {}, _marketplaces)
        plugins = pool.submit(probe, "plugins", {}, _plugins)
        uv_tools = pool.submit(probe, "uv_tools", {}, _uv_tools)
        shim = pool.submit(probe, "shim", {}, _shim)
        gateway = pool.submit(probe, "gateway", {}, _gateway)
        settings = pool.submit(probe, "settings", {}, _settings)
        history = pool.submit(probe, "history", None, _history)
        machine = Machine(
            os=os_name,
            arch=_or("", platform.machine),
            in_claude=bool(os.environ.get("CLAUDECODE")),
            tty=_tty(),
            bins={n: f.result() for n, f in bins.items()},
            service_manager=service.result(),
            release=release.result(),
            marketplaces={name: where_from(row) for name, row in marketplaces.result().items()},
            marketplace_rows=marketplaces.result(),
            plugins=plugins.result(),
            uv_tools=list(uv_tools.result()),
            uv_tool_exes=uv_tools.result(),
            ak_on_path=which_exe(CLI) is not None,
            local_bin_on_path=_or(False, _on_path, Path.home() / ".local" / "bin"),
            shim=shim.result(),
            gateway=gateway.result(),
            settings=settings.result(),
            history=history.result(),
        )
        return dataclasses.replace(machine, unknown=frozenset(unknown))
