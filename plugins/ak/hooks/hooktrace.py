"""hooktrace — run one hook command, record what it did, change nothing about it.

Claude Code records hook OUTPUT (only under --debug) and hook duration never.
So a wedged startup leaves no evidence and you get a guess instead of a name.
This wrapper is the missing record: one JSONL line per hook execution, with
the timing that nothing else captures.

It sits in front of EVERY hook, so the contract is absolute:

  1. the command's stdout reaches Claude Code byte-identical
  2. its stderr does too — exit-2 stderr is fed back to the model
  3. its exit code is preserved — 2 means BLOCK, and losing that is a bug
     that silently disarms every guard hook on the machine
  4. stdin (the hook payload JSON) is passed through untouched
  5. if ANY part of the tracer fails, the command still runs

Rule 5 is why every tracing step below is its own try, falling through to
running the command. Not being traced is a nuisance; not running is a broken session.

Usage (written by `ak hooks instrument`, not by hand; src/ak/hooks_trace.py):
  uv run --quiet --no-project --python ">=3.10" hooktrace.py <source> <event> <base64-of-original-command>

The command is base64 so it survives being a JSON string inside a shell
string — the two quoting layers that make the readable form unreliable.
`ak hooks status` decodes it back for reading. It runs through the shell Claude
Code runs a hook command with: sh, and Git Bash on Windows (shell_argv).
"""
import base64
import json
import os
import re
import subprocess
import sys
import time

CAP = 64_000


def now_ms():
    return int(time.time() * 1000)


def field(raw, name):
    m = re.search(rb'"' + name + rb'"\s*:\s*"([^"]*)"', raw)
    return m.group(1).decode("utf-8", "replace") if m else ""


def text(b):
    # A hook that prints a megabyte should not put a megabyte in the trace;
    # the head of it is what tells you what happened.
    return b[:CAP].decode("utf-8", "replace") + ("\n… truncated" if len(b) > CAP else "")


def _windows():
    try:
        from _brand import is_windows
        return is_windows()
    except Exception:
        return os.name == "nt"


def git_bash():
    """The bash Claude Code runs hooks with on Windows: $CLAUDE_CODE_GIT_BASH_PATH, else a bash on PATH (never
    System32's, which is WSL's launcher), else Git for Windows where its installer puts it. None when absent."""
    import shutil
    env_bash = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if env_bash and os.path.isfile(env_bash):
        return env_bash
    hit = shutil.which("bash")
    if hit and not re.search(r"[\\/](system32|windowsapps)[\\/]", hit, re.I):
        return hit
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA") or "", "Programs") if os.environ.get("LOCALAPPDATA") else None):
        if base:
            p = os.path.join(base, "Git", "bin", "bash.exe")
            if os.path.isfile(p):
                return p
    return None


def shell_argv(cmd):
    """(args, shell) that run CMD the way Claude Code runs a hook: sh on POSIX; on Windows Git Bash — the
    command says ${CLAUDE_PLUGIN_ROOT}, ~, '…' and 2>/dev/null, which cmd.exe would mangle. cmd.exe only when
    there is no bash at all (Claude Code then uses PowerShell; cmd.exe is the nearest a stdlib run has)."""
    if not _windows():
        return cmd, True
    bash = git_bash()
    return ([bash, "-c", cmd], False) if bash else (cmd, True)


def main(argv):
    src = argv[1] if len(argv) > 1 else "unknown"
    evt = argv[2] if len(argv) > 2 else "unknown"
    b64 = argv[3] if len(argv) > 3 else ""
    # Nothing to run, or an undecodable payload: we cannot run the original, and
    # inventing a failure would be worse than being silent. Exit 0.
    try:
        cmd = base64.b64decode(b64.encode(), validate=True).decode("utf-8") if b64 else ""
    except Exception:
        cmd = ""
    if not cmd:
        return 0

    raw = sys.stdin.buffer.read()
    path = None
    try:
        from _brand import claude_home, env
        trace_dir = env("HOOKTRACE_DIR") or str(claude_home() / "hooks-trace")
        # session_id is resolved BEFORE the command runs, because the start record
        # below has to land in the right file to be seen while the hook is still going.
        path = os.path.join(trace_dir, f"{field(raw, b'session_id') or 'unknown'}.jsonl")
    except Exception:
        pass

    start = now_ms()
    run = f"{os.getpid()}-{start}"
    # The whole point of this file is watching a hook that has not come back yet.
    # A record written only on completion is invisible for exactly as long as the
    # problem lasts, so a "start" line goes down first and the reader pairs it with
    # the "end" line by run id.
    if path:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"phase": "start", "run": run, "ts": start, "source": src,
                                    "event": evt, "cmd_b64": b64}) + "\n")
        except Exception:
            pass

    try:
        args, shell = shell_argv(cmd)
    except Exception:
        args, shell = cmd, True
    p = subprocess.run(args, shell=shell, input=raw, capture_output=True)
    end = now_ms()
    code = p.returncode if p.returncode >= 0 else 128 - p.returncode  # killed by a signal: what sh reports

    # Pass-through happens BEFORE recording, so a bug in the recorder can never
    # delay or corrupt what Claude Code receives.
    for stream, data in ((sys.stdout.buffer, p.stdout), (sys.stderr.buffer, p.stderr)):
        try:
            stream.write(data)
            stream.flush()
        except Exception:
            pass

    if path:
        try:
            rec = {
                "phase": "end", "run": run,
                "ts": start, "dur_ms": max(0, end - start),
                "source": src, "event": evt, "exit": code,
                "command": cmd, "cwd": field(raw, b"cwd"),
                "stdout": text(p.stdout), "stderr": text(p.stderr),
            }
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
