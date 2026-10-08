"""What is running now: every open Claude Code session, what it was asked, what it is doing.

Claude Code keeps a registry — ``<claude_home>/sessions/<pid>.json``, one file per running
process: session id, name, folder, status — and deletes a file when its process exits
cleanly. This joins each live entry to its transcript. Two things the registry does not
say, so every row carries them itself:

- ``busy`` means a turn is open or background work is pending, not that the model is
  calling: a session waiting on its own agents stays busy with a silent transcript. The
  row says how long ago anything under the session's files was last written.
- a crashed process leaves its file behind until another session sweeps the registry,
  so a row is listed only while its pid is alive.
"""
from __future__ import annotations

import calendar
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from ._brand import claude_home, pid_alive
from .verbs import spell
from .drill import DrillError, load_session, params_preview
from .search import calling_session, is_automated
from .step_label import step_label
from .turns import one_line, short_tool


def registry_dir() -> Path:
    return claude_home() / "sessions"


@dataclass
class Open:
    pid: int
    session_id: str
    name: str
    cwd: str
    status: str
    waiting_for: str = ""
    started: float = 0.0  # epoch seconds
    entrypoint: str = ""


def open_sessions(root: Path | None = None) -> list[Open]:
    """The registry's entries whose process is alive."""
    found = []
    for f in sorted((root or registry_dir()).glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            pid = int(r["pid"])
            sid = str(r["sessionId"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not pid_alive(pid):  # the seam probes without a signal: on Windows signal 0 terminates
            continue
        found.append(Open(pid=pid, session_id=sid, name=str(r.get("name") or ""), cwd=str(r.get("cwd") or ""),
                          status=str(r.get("status") or "?"), waiting_for=str(r.get("waitingFor") or ""),
                          started=float(r.get("startedAt") or 0) / 1000, entrypoint=str(r.get("entrypoint") or "")))
    return found


def last_write(transcript: Path) -> float:
    """The newest mtime of the transcript and everything under its session folder
    (subagent and workflow transcripts, saved tool results)."""
    newest = transcript.stat().st_mtime
    for top, _, files in os.walk(transcript.with_suffix("")):
        for name in files:
            try:
                newest = max(newest, os.stat(os.path.join(top, name)).st_mtime)
            except OSError:
                pass
    return newest


def ago(seconds: float) -> str:
    s = max(int(seconds), 0)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _epoch(ts: str) -> float | None:
    """A transcript timestamp (UTC, ISO) as epoch seconds."""
    try:
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None


def gist(answer: str, cap: int = 200) -> str:
    """An answer's opening words: its first paragraph, up to the first code block."""
    kept: list[str] = []
    for line in answer.splitlines():
        if line.lstrip().startswith("```") or (kept and not line.strip()):
            break
        if line.strip():
            kept.append(line.strip())
    return one_line(" ".join(kept).replace("**", ""), cap)


def doing(tool: str, inp: dict) -> str:
    """A call as a person would say it: its description, else the command, the file, its input."""
    return f"{short_tool(tool)} {step_label(tool, inp, 100) or params_preview(inp, 100)}"


def _home(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path


def facts(o: Open, me: str) -> dict:
    """One open session as data — what `--json` emits and both renderers draw."""
    s = {"name": o.name, "session": o.session_id, "pid": o.pid, "status": o.status,
         "waiting_for": o.waiting_for, "cwd": o.cwd, "started_at": o.started or None,
         "started_by": o.entrypoint, "program": is_automated(o.entrypoint), "this_session": o.session_id == me,
         "turns": 0, "last_write_at": None, "asked": "", "last_asked": "", "last_asked_at": None,
         "state": "nothing asked", "answer": "", "now": "", "now_at": None, "background": False}
    try:
        ls = load_session(o.session_id)
    except DrillError:
        return s
    if not ls.turns:
        return s
    first, last = ls.turns[0], ls.turns[-1]
    s.update(turns=len(ls.turns), last_write_at=last_write(ls.session_file),
             asked=one_line(first.prompt_text, 160))
    if len(ls.turns) > 1:
        s.update(last_asked=one_line(last.prompt_text, 160), last_asked_at=_epoch(last.ts))
    if last.interrupted_after is not None:
        s["state"] = "interrupted"
    elif last.final_text:
        s.update(state="answered", answer=gist(last.final_text), background=o.status == "busy")
    elif last.steps:
        step = last.steps[-1]
        s.update(state="working", now=doing(step.tool, step.input), now_at=_epoch(step.ts))
    else:
        s["state"] = "no answer"
    return s


def collect(root: Path | None = None) -> dict:
    """Every open session, newest activity first."""
    me = calling_session()
    rows = [facts(o, me) for o in open_sessions(root)]
    rows.sort(key=lambda s: s["last_write_at"] or s["started_at"] or 0, reverse=True)
    return {"registry": str(root or registry_dir()), "sessions": rows}


def headline(sessions: list[dict]) -> str:
    counts: dict[str, int] = {}
    for s in sessions:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    return (f"{len(sessions)} open session{'' if len(sessions) == 1 else 's'} — "
            + " · ".join(f"{n} {k}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
            + " · newest activity first")


BUSY = "busy = a turn is open or background work is pending; `last write` says whether anything is moving"


def hints() -> str:
    return f"{spell('show')} <id> — one session's turns and steps · {spell('replay')} <id> — its dialogue"


def lines(s: dict, now: float, style=lambda kind, text: text) -> tuple[str, list[str]]:
    """A session as a tree: the root line and its children. `style(kind, text)` dresses a
    piece for a terminal (kinds: name · id · status · label · dim); plain leaves it bare."""
    status = s["status"] + (f": {s['waiting_for']}" if s["waiting_for"] else "")
    tags = [style("name", s["name"] or "(unnamed)"), style("id", s["session"][:8]), style("status", status)]
    if s["last_write_at"]:
        tags.append(f"last write {ago(now - s['last_write_at'])}")
        tags.append(f"{s['turns']} turn{'' if s['turns'] == 1 else 's'}")
    if s["started_at"]:
        tags.append(f"started {ago(now - s['started_at'])}")
    if s["program"]:
        tags.append(style("dim", f"a program started it ({s['started_by']})"))
    if s["this_session"]:
        tags.append(style("dim", "this session"))

    def said(label: str, text: str) -> str:
        return f"{style('label', label)} {style('text', text)}"

    kids = [style("dim", _home(s["cwd"]))]
    if s["state"] == "nothing asked":
        kids.append(style("dim", "nothing asked yet"))
        return " · ".join(tags), kids
    kids.append(said("asked:", s["asked"]))
    if s["last_asked"]:
        when = f" {ago(now - s['last_asked_at'])}" if s["last_asked_at"] else ""
        kids.append(said(f"last asked{when}:", s["last_asked"]))
    if s["state"] == "interrupted":
        kids.append(style("dim", "interrupted by the user — no answer"))
    elif s["state"] == "answered":
        kids.append(said("answered:", s["answer"]))
        if s["background"]:
            kids.append(style("dim", "its turn has an answer and it is still busy: background agents or workflows are running"))
    elif s["state"] == "working":
        when = f" (called {ago(now - s['now_at'])})" if s["now_at"] else ""
        kids.append(said("now:", f"{s['now']}{when} — no answer yet"))
    else:
        kids.append(style("dim", "no answer yet"))
    return " · ".join(tags), kids
