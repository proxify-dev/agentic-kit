"""Run a vault or memory hook on the Python the installed `ak` runs on.

    uv run --quiet --no-project --python ">=3.10" <plugin>/hooks/akpy.py <plugin>/hooks/<hook>.py

That interpreter holds the packages these hooks import — ak, vault and memory, installed together
by ak's hooks/install-global.py — which no plugin's own src/ carries alone. This file runs on uv's
bare Python (stdlib only, the way every other kit hook starts: no shell, the same command on macOS,
Linux and Windows), picks that interpreter and hands the hook to it. The first that is there wins:
  1. $AK_HOOK_PYTHON                                   a test, or a machine that knows better
  2. the interpreter on the `#!` line of `ak` on PATH  the uv tool's python (macOS/Linux; an `env` or
                                                       shell line names no interpreter and is skipped,
                                                       and Windows' ak.exe has no such line)
  3. <uv tools>/ak/{bin/python,Scripts/python.exe}    ak installed, not on PATH: $UV_TOOL_DIR, else
                                                       ${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools, on
                                                       Windows %APPDATA%\\uv\\data\\tools
  4. <plugins>/ak/.venv/{bin/python,Scripts/python.exe}  a checkout: ak's own project env, which
                                                       carries vault and memory too (its dev group; made
                                                       by the first `uv run --project plugins/ak …`)
None of them: the hook is skipped, exit 0, one line on stderr. A Python that is there but predates
the packages is the hook's to notice: it skips itself the same way.

macOS/Linux: exec, so the hook IS this process (stdin, stdout, exit code and signals are its own).
Windows has no exec (os.exec* starts the child and returns at once): there the hook runs as a child
with this process's stdin/stdout/stderr, and its exit code is this one's — 2 still blocks.

Canonical in ak/hooks/akpy.py; the vault's and memory's hooks/ carry copies (scripts/copies.py
writes and checks them). Every other Python hook starts on uv's bare Python
directly (stdlib only, plugins/HOOKS.md §1). No pathlib and no imports beyond os/sys/subprocess:
this runs before every Bash call.
"""
import os
import sys

WINDOWS = os.name == "nt"
EXE = ("Scripts", "python.exe") if WINDOWS else ("bin", "python")


def _is_file(p):
    return bool(p) and os.path.isfile(p)


def _shebang_python():
    """The python on the `#!` line of `ak` on PATH, or None (Windows, a shell or `env` launcher, no ak)."""
    if WINDOWS:
        return None
    for d in os.environ.get("PATH", "").split(os.pathsep):
        ak = os.path.join(d or ".", "ak")
        if not os.path.isfile(ak):
            continue
        try:
            with open(ak, "rb") as fh:
                line = fh.readline(512).decode("utf-8", "replace").strip()
        except OSError:
            return None
        if not line.startswith("#!"):
            return None
        words = line[2:].split()
        py = words[0] if words else ""
        return py if os.path.basename(py).startswith("python") and os.access(py, os.X_OK) else None
    return None


def _tool_dir():
    if os.environ.get("UV_TOOL_DIR"):
        return os.environ["UV_TOOL_DIR"]
    if WINDOWS:
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "uv", "data", "tools")
    # uv's own layout, not the seam's data_dir(): uv keeps tools under %APPDATA% on Windows, not %LOCALAPPDATA%
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")  # portable: ok
    return os.path.join(data, "uv", "tools")


def python():
    """The interpreter the hook runs on, or None."""
    here = os.path.dirname(os.path.abspath(__file__))
    for py in (os.environ.get("AK_HOOK_PYTHON"),
               _shebang_python(),
               os.path.join(_tool_dir(), "ak", *EXE),
               os.path.join(here, "..", "..", "ak", ".venv", *EXE)):
        if _is_file(py):
            return py
    return None


def main(argv):
    if not argv:
        print("akpy: usage: akpy.py <hook.py> [args…]", file=sys.stderr)
        return 0
    py = python()
    if not py:
        print(f"ak: no ak install found (hooks/akpy.py), hook skipped: {os.path.basename(argv[0])}"
              " — enable ak@ak, which installs ak at session start", file=sys.stderr)
        return 0
    try:
        if not WINDOWS:
            os.execv(py, [py, *argv])
        import subprocess
        return subprocess.call([py, *argv])
    except OSError as e:
        print(f"ak: {py} did not start ({e}), hook skipped: {os.path.basename(argv[0])}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
