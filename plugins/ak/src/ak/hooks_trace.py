#!/usr/bin/env python3
"""Hook tracing — make hook execution visible, because nothing else does.

Claude Code records hook OUTPUT only under --debug, and hook DURATION never.
So when a session wedges before init the evidence does not exist, and the
error message can only offer a guess ("startup hooks or plugins ... are the
usual cause"). This module creates the missing record.

The mechanism is a rewrite, not a listener: every configured hook command is
replaced by a call to hooks/hooktrace.py carrying the original command base64'd.
hooktrace runs it, passes stdout/stderr/exit through untouched, and appends
one JSONL line with the timing.

base64 is not obfuscation — it is the only encoding that survives being a
JSON string inside a shell string without a quoting bug. `status` decodes it.

Two files own the truth about what is instrumented:
  <claude home>/settings.json      the user's own hooks
  <plugin>/hooks/hooks.json        each ENABLED plugin's hooks

Plugin files live in the plugin cache, so a plugin update reverts them. That
is not a failure mode worth defending against — `status` reports the drift and
`instrument` is idempotent, so re-running is the fix.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import time
from pathlib import Path

from ._brand import claude_home, env, posix

CLAUDE = claude_home()
# One file per session, named by session_id — which IS the transcript UUID
# (verified against a live SessionStart payload: session_id 79ab531f… and
# transcript_path .../79ab531f….json). So a trace joins to a session by name,
# with nothing to look up.
#
# Honours the same override as hooks/hooktrace.py, so both halves can be pointed
# at a fixture directory together.
TRACE_DIR = Path(env("HOOKTRACE_DIR") or CLAUDE / "hooks-trace")
# src/ak/hooks_trace.py → parents[2] is the plugin root that owns hooks/.
HOOKTRACE = Path(__file__).resolve().parents[2] / "hooks" / "hooktrace.py"
# Started the way plugins' hooks.json start a Python hook: no shell script in between.
UV_RUN = 'uv run --quiet --no-project --python ">=3.10"'


# Session-lifecycle events: each fires once per session or per turn, so the
# measured ~36ms wrapper cost is invisible. These are also the events that
# explain a wedged startup, which is the reason this exists.
LIFECYCLE = [
    "SessionStart", "SessionEnd", "UserPromptSubmit", "Stop", "StopFailure",
    "SubagentStart", "SubagentStop", "TeammateIdle", "Notification",
    "PreCompact", "PermissionRequest",
]
# Tool events fire on EVERY tool call. At ~36ms each that is a real tax on a
# long session, so they are opt-in rather than default.
TOOL = ["PreToolUse", "PostToolUse", "PostToolUseFailure"]

MARK = "hooktrace"


def _is_wrapped(cmd: str) -> bool:
    # bin/hooktrace, the retired shell wrapper, is still recognised: `hooks off` unwraps it, `hooks on` rewraps it
    return MARK in cmd and any(w in posix(cmd) for w in ("/hooks/hooktrace.py", "/bin/hooktrace"))


def _wrap(cmd: str, source: str, event: str) -> str:
    b64 = base64.b64encode(cmd.encode()).decode()
    return f'{UV_RUN} "{HOOKTRACE}" {source} {event} {b64}'


def _unwrap(cmd: str) -> str | None:
    """Recover the original command, or None if this is not a wrapped one."""
    if not _is_wrapped(cmd):
        return None
    try:
        return base64.b64decode(cmd.rsplit(" ", 1)[1].encode()).decode()
    except Exception:
        return None


def enabled_plugins() -> dict[str, bool]:
    try:
        s = json.loads((CLAUDE / "settings.json").read_text(encoding="utf-8"))
        return s.get("enabledPlugins", {})
    except Exception:
        return {}


def hook_files() -> list[tuple[str, Path]]:
    """(source-label, path) for every file whose hooks actually run.

    A disabled plugin's hooks.json is skipped: instrumenting hooks that never
    fire would put noise in `status` and edits on disk for no trace.
    """
    out: list[tuple[str, Path]] = [("settings", CLAUDE / "settings.json")]
    for spec, on in enabled_plugins().items():
        if not on:
            continue
        name = spec.split("@")[0]
        for hj in sorted((CLAUDE / "plugins" / "cache").glob(f"*/{name}/*/hooks/hooks.json")):
            out.append((name, hj))
    return [(s, p) for s, p in out if p.is_file()]


def _walk(doc: dict):
    """Yield (event, hook-dict) for every command hook in a settings/hooks doc."""
    for event, matchers in (doc.get("hooks") or {}).items():
        for m in matchers or []:
            for h in (m.get("hooks") or []):
                if isinstance(h.get("command"), str):
                    yield event, h


def scan() -> list[dict]:
    """Every hook that runs on this machine, and whether it is traced."""
    rows = []
    for source, path in hook_files():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for event, h in _walk(doc):
            original = _unwrap(h["command"])
            rows.append({
                "source": source, "path": path, "event": event,
                "wrapped": original is not None,
                "command": original if original is not None else h["command"],
            })
    return rows


def instrument(events: list[str], dry_run: bool = False) -> tuple[int, int, list[Path]]:
    """Wrap every hook on the named events. Idempotent: already-wrapped is a skip."""
    wrapped = skipped = 0
    touched: list[Path] = []
    for source, path in hook_files():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        dirty = False
        for event, h in _walk(doc):
            if event not in events:
                continue
            if _is_wrapped(h["command"]):
                if "/bin/hooktrace" not in posix(h["command"]):
                    skipped += 1
                    continue
                h["command"] = _unwrap(h["command"]) or h["command"]  # the retired bin/hooktrace: rewrap below
            if not dry_run:
                h["command"] = _wrap(h["command"], source, event)
            dirty = True
            wrapped += 1
        if dirty and not dry_run:
            _backup(path)
            path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
            touched.append(path)
        elif dirty:
            touched.append(path)
    return wrapped, skipped, touched


def revert() -> tuple[int, list[Path]]:
    """Unwrap everything, in place.

    Unwrapping rather than restoring a backup on purpose: a backup would also
    undo whatever else changed in settings.json since instrumenting.
    """
    n = 0
    touched: list[Path] = []
    for _source, path in hook_files():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        dirty = False
        for _event, h in _walk(doc):
            original = _unwrap(h["command"])
            if original is None:
                continue
            h["command"] = original
            dirty = True
            n += 1
        if dirty:
            _backup(path)
            path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
            touched.append(path)
    return n, touched


def _backup(path: Path) -> Path:
    dest = path.with_suffix(path.suffix + f".pre-hooktrace.{int(time.time())}")
    shutil.copy2(path, dest)
    return dest


# ─────────────────────────────────────────────────────────────── reading traces
def sessions(limit: int = 40) -> list[dict]:
    """Traced sessions, newest first."""
    if not TRACE_DIR.is_dir():
        return []
    out = []
    for f in sorted(TRACE_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        recs = records(f.stem)
        if not recs:
            continue
        out.append({
            "session": f.stem,
            "count": len(recs),
            "failures": sum(1 for r in recs if not r["running"] and r["exit"] not in (0, 2)),
            "running": sum(1 for r in recs if r["running"] and not r["stale"]),
            "blocks": sum(1 for r in recs if r["exit"] == 2),
            "total_ms": sum(r["dur_ms"] for r in recs),
            "slowest_ms": max((r["dur_ms"] for r in recs), default=0),
            "start": min(r["ts"] for r in recs),
            "cwd": next((r.get("cwd") for r in recs if r.get("cwd")), ""),
        })
    return out


STALE_MS = 120_000


def records(session: str) -> list[dict]:
    """One row per hook run, pairing the start line with its end line.

    A hook writes "start" before it runs and "end" when it returns, so that it
    is visible WHILE it is still going — a record written only on completion
    is missing for exactly as long as the problem lasts. An unpaired start is
    a hook still out (or one whose process died, which STALE_MS separates).
    """
    f = TRACE_DIR / f"{session}.jsonl"
    if not f.is_file():
        return []
    now = int(time.time() * 1000)
    by_run: dict[str, dict] = {}
    loose: list[dict] = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        run = r.get("run") or ""
        if r.get("phase") == "start":
            ts = r.get("ts", 0)
            try:
                cmd = base64.b64decode(r.get("cmd_b64", "").encode()).decode()
            except Exception:
                cmd = ""
            by_run[run] = {**r, "command": cmd, "dur_ms": max(0, now - ts),
                           "exit": 0, "running": True, "stale": now - ts > STALE_MS}
        elif run:
            by_run[run] = {**r, "running": False, "stale": False}
        else:
            loose.append({**r, "running": False, "stale": False})  # pre-start-record lines
    return sorted([*by_run.values(), *loose], key=lambda r: r.get("ts", 0))
