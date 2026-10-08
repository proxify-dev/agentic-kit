"""SessionStart hook — put the `ak` CLI on PATH as an editable install of the
RELEASED plugins: plugins/ak of the marketplace this plugin was installed from,
with the vault and memory packages beside it (plugins/vault, plugins/memory) in
the same tool environment — the hooks of both run on that interpreter
(hooks/akpy.py), and the host mounts their verbs by import name. A sibling that
is not on disk beside the chosen source is left out. Editable, so a release pull
changes the CLI with no reinstall; the gate below reinstalls only when the source
path or one of the pyproject.toml files (the dependency sets) changes.

The source, first match wins:
1. AK_SRC: a machine with no marketplace tree, or a person pointing at one by hand.
2. Claude Code's clone of a git marketplace (GitHub, the public kit). There Claude Code
   runs a plugin from its versioned cache, so ${CLAUDE_PLUGIN_ROOT} is
   <claude home>/plugins/cache/<marketplace>/<plugin>/<version>/. When the clone
   <claude home>/plugins/marketplaces/<marketplace>/plugins/<plugin> has a
   pyproject.toml, install from it. Why: the cache path changes with every version
   and is deleted 14 days after it is replaced, so an editable install there breaks;
   and a session still on the old version would reinstall the old path (the
   downgrade below). The clone is one path that holds the newest tree, laid out as
   the repo is, so the gate stays quiet across version bumps.
3. ${CLAUDE_PLUGIN_ROOT}: plugins/ak inside the release clone that a local-folder
   marketplace points at (RELEASING.md, repo root), where plugins run in place; or
   the cache path itself, when its clone is not on disk and no uv tool puts `ak`
   on PATH yet. With the clone missing and an `ak` installed, change nothing:
   `claude plugin marketplace update` deletes the clone and writes the new one at
   the same path, so the clone can be missing for a moment, and an install from
   the cache then would be undone by the next session (install from the cache,
   then from the clone again).
<claude home> is claude_home() (_brand.py), which honours $CLAUDE_CONFIG_DIR.

Why not the working checkout: until 2026-09-22 this installed from ~/agentic-kit,
so an experiment in the tree was live in the global `ak` at once. Why not
the marketplace cache: that copy lagged plugin.json and force-downgraded the
CLI every session (2026-09-06, -10). The release clone, or the marketplace
clone, is one tree that is never stale and never a playground.

Contract: never block a session. Every failure path exits 0, all diagnostics
go to stderr, and stdout stays empty (SessionStart stdout is injected as
session context).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HOOKS_DIR)
# The installer runs before any ak exists: stdlib, and this plugin's own src/ for the one rule it shares.
sys.path.append(os.path.join(os.path.dirname(HOOKS_DIR), "src"))

SIBLINGS = ("vault", "memory")


def say(msg):
    print(msg, file=sys.stderr)


def manifest(src: Path, siblings) -> bytes:
    """What the gate compares: every installed package's pyproject.toml, in install order."""
    out = (src / "pyproject.toml").read_bytes()
    for sib in siblings:
        out += f"# {sib.name}\n".encode("utf-8") + (sib / "pyproject.toml").read_bytes()
    return out


def marketplace_clone(root: str, claude: Path):
    """For ROOT in Claude Code's versioned cache, CLAUDE/plugins/cache/<marketplace>/<plugin>/<version>:
    the same plugin in Claude Code's clone of that marketplace, CLAUDE/plugins/marketplaces/<marketplace>/plugins/<plugin>
    (a kit plugin's folder in the repo is its name), whether or not it is on disk. None for any other ROOT."""
    if not root:
        return None
    plugins = claude / "plugins"
    try:
        parts = Path(root).resolve().relative_to((plugins / "cache").resolve()).parts
    except (OSError, ValueError):
        return None
    if len(parts) < 2:
        return None
    return plugins / "marketplaces" / parts[0] / "plugins" / parts[1]


def project_name(pyproject: Path) -> str:
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        if line.startswith("name = "):
            return line.split('"')[1] if '"' in line else ""
    return ""


def tools_with(uv, exe):
    """The uv tools that put EXE on PATH: `uv tool list` prints each tool as `<name> v<version>`
    and its executables under it as `- <exe>`."""
    p = subprocess.run([uv, "tool", "list"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL)
    tool, found = "", []
    for line in (p.stdout or "").splitlines():
        if line and not line.startswith((" ", "-")):
            tool = line.split()[0]
        elif line.strip() in (f"- {exe}", f"- {exe}.exe") and tool and tool not in found:
            found.append(tool)
    return sorted(found)


def others_with(uv, exe, keep):
    """The other uv tools that put EXE on PATH (an install from before the packages had these names)."""
    return [t for t in tools_with(uv, exe) if t != keep]


def main():
    from _brand import CLI, claude_home, env
    from ak.session_origin import automated

    # A program's session (claude -p, the SDKs — ak/session_origin.py is the rule) installs nothing
    # globally; the next person's session does. AK_CAPTURE=1 counts it as a person's.
    if automated():
        return
    data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    # The source rule is the docstring's: AK_SRC, else the marketplace clone for a cached root, else the root.
    src = env("SRC") or os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    clone = None if env("SRC") else marketplace_clone(src, claude_home())
    if clone and (clone / "pyproject.toml").is_file():
        src, clone = str(clone), None
    # From here on, CLONE is set only when ROOT is in the cache and its clone is not on disk.
    if not (src and (Path(src) / "pyproject.toml").is_file()):
        src = ""
    if not src or not data:
        say(f"{CLI}: no plugin source or CLAUDE_PLUGIN_DATA unset; skipping global install")
        return

    root = Path(src)
    # The packages installed together: this plugin's, then each sibling's on disk beside the chosen source.
    siblings = [(root / ".." / s).resolve() for s in SIBLINGS if (root / ".." / s / "pyproject.toml").is_file()]
    stamp = Path(data) / "pyproject.toml"
    srcstamp = Path(data) / "source"

    uv = shutil.which("uv")
    if not uv:
        say(f"{CLI}: uv not found on PATH; skipping global install")
        return

    # GATE: same source path, same manifests as last time — nothing to do.
    want = manifest(root, siblings)
    try:
        if srcstamp.read_text(encoding="utf-8").rstrip("\n") == src and stamp.read_bytes() == want:
            return
    except OSError:
        pass

    # A cached root whose clone is not on disk: `claude plugin marketplace update` swaps the clone out and back
    # at the same path, so an `ak` installed from it stays. Only with no `ak` yet does the cache path serve.
    if clone and tools_with(uv, CLI):
        say(f"{CLI}: {clone} is not on disk (a marketplace update?); keeping the installed {CLI}")
        return

    try:
        Path(data).mkdir(parents=True, exist_ok=True)
    except OSError:
        say(f"{CLI}: cannot create {data}")
        return

    # Another uv tool that puts `ak` on PATH would keep answering beside this one: take it out first.
    for old in others_with(uv, CLI, project_name(root / "pyproject.toml")):
        subprocess.run([uv, "tool", "uninstall", old], stdout=sys.stderr, stdin=subprocess.DEVNULL)

    # --refresh: uv's own build cache once served an Aug-14 wheel for a source
    # that had grown 14KB since. Editable + refresh leaves nothing to be stale.
    # Its output goes to stderr: stdout is session context.
    argv = [uv, "tool", "install", "--editable", "--force", "--refresh", src]
    for sib in siblings:
        argv += ["--with-editable", str(sib)]
    ok = subprocess.run(argv, stdout=sys.stderr, stdin=subprocess.DEVNULL).returncode == 0
    if ok:
        try:
            stamp.write_bytes(want)
            srcstamp.write_text(src + "\n", encoding="utf-8", newline="\n")
            return
        except OSError:
            pass
    for p in (stamp, srcstamp):
        try:
            p.unlink()
        except OSError:
            pass
    if not ok:
        say(f"{CLI}: uv tool install failed; will retry next session")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never block a session
        say(f"install-global: {type(e).__name__}: {e}")
    sys.exit(0)
