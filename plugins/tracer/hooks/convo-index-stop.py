"""Stop / SessionStart hook — refresh the conversation index for the current project.

Closes the staleness loop by running the indexer at the end of every assistant
turn (and at session start as belt-and-suspenders). Query paths never write;
this is the only writer in the normal hot path.

Behavior:
- Skip automated sessions (session_origin.automated(): CLAUDE_CODE_ENTRYPOINT not
  cli/claude-desktop/sdk-ts), unless AK_CAPTURE=1
- Resolve project via $CLAUDE_PROJECT_DIR (set by Claude Code), fallback to cwd
- Launch this plugin's tool-call indexer (`node traces/bin/traces.mjs index`; under
  $AK_CODE, that checkout's plugins/tracer/traces/), detached and at lower priority,
  before anything that needs uv; node >= 22.13 is looked for on PATH, then where nvm,
  fnm, volta, asdf, Homebrew and Windows put it
- Run this plugin's own CLI through `uv run --project "$CLAUDE_PLUGIN_ROOT"`,
  detached — no global install, no PATH assumption; the plugin's .venv is
  created on first use, the same one the trace skill runs in
- Append all output to a log file under <claude home>/plugins/data/conversation-index/
- Return immediately so session termination / startup is never blocked
- Always exit 0 — indexing failures must never break the session

We start the indexer in the project instead of passing a flag: cwd is the one input
every entrypoint agrees on.

Concurrency safety: the indexer holds a bounded lock that serializes writers, so it's
safe to invoke concurrently with other hook runs or a manual `tracer index` invocation.
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from _brand import claude_home, data_dir, env, env_name, is_windows, spawn_detached
    from session_origin import automated
except Exception:  # a partial copy must not break the session
    sys.exit(0)


def log(fh, line):
    fh.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {line}\n".encode("utf-8"))
    fh.flush()


def spawn(argv, fh, cwd):
    kw = {} if is_windows() else {"preexec_fn": lambda: os.nice(10)}  # portable: ok — Windows has no nice
    spawn_detached(argv, stdout=fh, stderr=fh, cwd=cwd, **kw)


# ── node ≥ 22.13, wherever it is ──────────────────────────────────────────────────────────
# The indexer imports node:sqlite, unflagged from 22.13. A hook's PATH is often a GUI app's PATH,
# without nvm's or volta's node on it, so the places node installers put it are tried too
# (traces/bin/find-node.sh's list, plus volta, fnm, asdf and Windows). src/tracer/calls.py carries the
# same finder: this hook runs on uv's bare Python, before any tracer env exists to import it from.
NODE_MIN = (22, 13)
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def _newest_first(paths):
    """Version folders newest first, by the version parsed from each path (a string sort puts v9 above v22)."""
    def ver(p):
        found = _VERSION.findall(str(p))
        return tuple(int(x) for x in found[-1]) if found else (0, 0, 0)
    return sorted(paths, key=ver, reverse=True)


def _node_candidates():
    home = Path.home()
    yield shutil.which("node")
    if is_windows():
        for var, rel in (("ProgramFiles", "nodejs/node.exe"), ("LOCALAPPDATA", "Programs/nodejs/node.exe"),
                         ("USERPROFILE", ".volta/bin/node.exe")):
            if os.environ.get(var):
                yield Path(os.environ[var]) / rel
        for nvm in (os.environ.get("NVM_HOME"), os.environ.get("APPDATA") and Path(os.environ["APPDATA"]) / "nvm"):
            if nvm:
                yield from _newest_first(Path(nvm).glob("v*/node.exe"))
        return
    yield from _newest_first(Path(os.environ.get("NVM_DIR") or home / ".nvm").glob("versions/node/v*/bin/node"))
    fnm = [os.environ.get("FNM_DIR"), data_dir() / "fnm", home / ".fnm",
           home / "Library" / "Application Support" / "fnm"]  # portable: ok — fnm's macOS default
    for d in fnm:
        if d:
            yield from _newest_first(Path(d).glob("node-versions/v*/installation/bin/node"))
    yield home / ".volta" / "bin" / "node"
    yield Path(os.environ.get("ASDF_DATA_DIR") or home / ".asdf") / "shims" / "node"
    yield "/opt/homebrew/bin/node"  # portable: ok — Homebrew on Apple Silicon, a POSIX-only candidate
    yield "/usr/local/bin/node"
    yield "/usr/bin/node"


def _node_version(exe):
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, stdin=subprocess.DEVNULL,
                             creationflags=0x08000000 if is_windows() else 0).stdout  # CREATE_NO_WINDOW
    except (OSError, subprocess.SubprocessError):
        return None
    m = _VERSION.match((out or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def find_node():
    """(path, None) for the first node ≥ 22.13 among the candidates; else (None, why) — the why names
    the too-old node it saw, so the log does not say "not found" about a node the user can see."""
    seen, too_old = set(), None
    for c in _node_candidates():
        if not c or not os.path.isfile(c):
            continue
        key = os.path.realpath(c)
        if key in seen:
            continue
        seen.add(key)
        v = _node_version(str(c))
        if v is None:
            continue
        if v[:2] >= NODE_MIN:
            return str(c), None
        too_old = too_old or f"node {'.'.join(map(str, v))} at {c} is below the required 22.13 (node:sqlite)"
    return None, too_old or "no node >= 22.13 on PATH or in the usual install places"


def traces_index(root: Path, fh):
    """The tool-call index (traces.db, what `tracer trace` reads) has its own one-shot
    indexer, shipped in this plugin under traces/ (the committed bundle traces/dist/traces.mjs, run as
    `node traces/bin/traces.mjs index`), so it stays current with the app closed and a tracer installed
    on its own has it. It needs node, not uv, so it goes first. $AK_CODE, when set, names a checkout
    whose indexer runs instead (its plugins/tracer/traces/); else this plugin's own. The indexer takes a
    lock and reads only new bytes, so a launch per turn is cheap and two at once are safe. It runs from
    the home directory: the store resolves from the cwd."""
    rel = Path("traces") / "bin" / "traces.mjs"
    dirs = [Path(env("CODE")) / "plugins" / "tracer" if env("CODE") else None, root]
    entry = next((d / rel for d in dirs if d and (d / rel).is_file()), None)
    if not entry:
        log(fh, f"traces-index: no {rel} under {root}"
                + (f" or ${env_name('CODE')}/plugins/tracer" if env("CODE") else "") + " — skipped")
        return
    node, why = find_node()
    if not node:
        log(fh, f"traces-index: {entry}: {why} — skipped")
    else:
        log(fh, f"traces-index: {entry} · node {node}")
        spawn([node, "--no-warnings", str(entry.resolve()), "index"], fh, Path.home())


def convo_index(root: Path, project: Path, fh):
    uv = shutil.which("uv")
    if not uv or not project.is_dir():
        return
    # A corpse .venv (its python gone) is removed first, the way a broken env heals itself
    # instead of failing every turn. The interpreter's place in a venv differs per OS.
    venv = root / ".venv"
    if venv.is_dir() and not any((venv / p).exists() for p in ("bin/python", "Scripts/python.exe")):
        shutil.rmtree(venv, ignore_errors=True)
    log(fh, f"convo-index-stop: project={project} plugin={root}")
    spawn([uv, "run", "--quiet", "--project", str(root), "tracer", "index"], fh, project)


def main():
    if automated():
        return
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[1])
    log_dir = claude_home() / "plugins" / "data" / "conversation-index"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = open(log_dir / "stop-hook.log", "ab")
    except OSError:
        return
    with fh:
        for step in (lambda: traces_index(root, fh), lambda: convo_index(root, project, fh)):
            try:
                step()
            except Exception as e:  # indexing failures must never break the session
                log(fh, f"convo-index-stop: {type(e).__name__}: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
