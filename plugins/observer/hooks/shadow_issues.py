"""shadow_issues — a trial, run beside the observer: can it follow an issue from opened to fixed?

The observer records facts and narration but does not follow an issue from opened to fixed. This trial
tests whether a second read of each session can, silently, so the result can be checked periodically.

So, while `<kit>/db/shadow/issues/ON` exists (`observer shadow on`), every session the observer reads is read
a second time (session-end.py spawns `summarize-session.py --shadow`): the same agent and the same message,
plus the project's OPEN ISSUES and an `issues` section in the output: what this session left open, what it
fixed of the open ones, what it moved on. That run writes nothing to the store (brain.db), clears no scratch
and embeds nothing: its only output is here, read by `observer shadow report`.

    <kit>/db/shadow/issues/ON                  the switch
    <kit>/db/shadow/issues/events.jsonl        append-only, one JSON object per line:
      {"at", "session", "project", "event": "opened", "id", "title", "anchors", "evidence", "fixed_here"}
      {"at", "session", "project", "event": "resolved" | "progress", "id", "evidence"}
    <kit>/db/shadow/issues/runs/<session>.json the run's whole answer and the open issues it was shown

An issue is open while it has an `opened` line and no `resolved` line. Its id is `iss-` and six hex characters
of sha1(session + title). Stdlib only: the hooks run under bare python3.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from vault_root import db_dir

OPEN_MAX = 40          # open issues shown to the model, newest first
OPEN_CHARS = 4_000


def shadow_dir() -> Path:
    return Path(db_dir()) / "shadow" / "issues"


def enabled() -> bool:
    try:
        return (shadow_dir() / "ON").exists()
    except Exception:  # noqa: BLE001 — no kit, no trial
        return False


def events_path() -> Path:
    return shadow_dir() / "events.jsonl"


def events() -> list:
    p = events_path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("event"):
            out.append(e)
    return out


def issues(evs: list | None = None) -> dict:
    """{id: {"id", "project", "title", "anchors", "opened_in", "opened_at", "fixed_here", "resolved_in",
    "resolved_at", "resolved_evidence", "progress": [...]}} from the event lines, in order."""
    out: dict = {}
    for e in evs if evs is not None else events():
        i = e.get("id")
        if not i:
            continue
        if e["event"] == "opened" and i not in out:
            out[i] = {"id": i, "project": e.get("project", ""), "title": e.get("title", ""),
                      "anchors": e.get("anchors") or [], "evidence": e.get("evidence", ""),
                      "opened_in": e.get("session", ""), "opened_at": e.get("at", ""),
                      "fixed_here": bool(e.get("fixed_here")), "resolved_in": "", "resolved_at": "",
                      "resolved_evidence": "", "progress": []}
            if out[i]["fixed_here"]:
                out[i].update(resolved_in=e.get("session", ""), resolved_at=e.get("at", ""),
                              resolved_evidence=e.get("evidence", ""))
        elif i in out and e["event"] == "resolved" and not out[i]["resolved_in"]:
            out[i].update(resolved_in=e.get("session", ""), resolved_at=e.get("at", ""),
                          resolved_evidence=e.get("evidence", ""))
        elif i in out and e["event"] == "progress":
            out[i]["progress"].append({"session": e.get("session", ""), "at": e.get("at", ""),
                                       "evidence": e.get("evidence", "")})
    return out


def open_issues(project: str) -> list:
    """The PROJECT's issues with no `resolved` line, newest first."""
    rows = [v for v in issues().values() if v["project"] == project and not v["resolved_in"]]
    return sorted(rows, key=lambda v: v["opened_at"], reverse=True)[:OPEN_MAX]


def open_block(rows: list) -> str:
    if not rows:
        return "(none yet)"
    lines, used = [], 0
    for r in rows:
        line = f"- {r['id']} · opened {r['opened_at'][:10]} · {r['title']}"
        if r.get("anchors"):
            line += f" · about: {', '.join(r['anchors'][:4])}"
        if used + len(line) + 1 > OPEN_CHARS:
            lines.append(f"(+{len(rows) - len(lines)} more, not shown)")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


OUTPUT = """,
  "issues": {
    "opened": [
      {"title": "<=80 chars: what is broken or still to do", "anchors": ["literal paths, commands, ids it is about"],
       "evidence": "what in this session showed it", "fixed_here": true or false}
    ],
    "resolved": [{"id": "iss-… from OPEN ISSUES", "evidence": "what in this session shows it is fixed or done"}],
    "progress": [{"id": "iss-… from OPEN ISSUES", "evidence": "what moved, still not fixed"}]
  }"""

RULES = """
ISSUES (a trial: the "issues" section of your answer, beside the observations, which stay as they are):
- An issue is one thing that is broken or still to do: a bug seen and not fixed, a check that fails, a step
  the person or the agent said to do later, a question left for the person. Not a wish for "better" in general.
- "opened": an issue this session found. "fixed_here": true when the same session also fixed it; false when
  it was still open when the session ended. Never open one that is already in OPEN ISSUES: that is
  "progress" or "resolved" on its id.
- "resolved": an OPEN ISSUES id this session fixed or finished, and the evidence (a test that now passes, the
  person's yes, a run that worked). Only when the session SHOWS it; a fix that was only planned is "progress".
- "progress": an OPEN ISSUES id this session worked on and did not finish.
- All three lists may be empty, and usually are.
"""


def prompt_parts(project: str) -> tuple:
    """(the open issues shown, the text block for the message) for a run on PROJECT."""
    rows = open_issues(project)
    return rows, f"OPEN ISSUES (this project — opened by earlier sessions, not resolved yet):\n{open_block(rows)}\n\n"


def issue_id(session: str, title: str) -> str:
    return "iss-" + hashlib.sha1(f"{session}\n{title}".encode("utf-8")).hexdigest()[:6]


def _list(v) -> list:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def record(session: str, project: str, answer: dict, shown: list, now: str | None = None) -> dict:
    """Append the run's issue lines and save its answer. Ids the model names that were not shown to it are
    dropped (counted in `unknown`). Returns {"opened", "resolved", "progress", "unknown"}."""
    now = now or datetime.now().astimezone().isoformat(timespec="seconds")
    d = shadow_dir()
    (d / "runs").mkdir(parents=True, exist_ok=True)
    sec = answer.get("issues")
    sec = sec if isinstance(sec, dict) else {}
    known = {r["id"] for r in shown}
    lines, counts = [], {"opened": 0, "resolved": 0, "progress": 0, "unknown": 0}
    base = {"at": now, "session": session, "project": project}
    for o in _list(sec.get("opened")):
        title = " ".join(str(o.get("title") or "").split())[:120]
        if not title:
            continue
        lines.append({**base, "event": "opened", "id": issue_id(session, title), "title": title,
                      "anchors": [str(a) for a in (o.get("anchors") or []) if a][:8],
                      "evidence": str(o.get("evidence") or ""), "fixed_here": bool(o.get("fixed_here"))})
        counts["opened"] += 1
    for kind in ("resolved", "progress"):
        for o in _list(sec.get(kind)):
            i = str(o.get("id") or "").strip()
            if i not in known:
                counts["unknown"] += 1
                continue
            lines.append({**base, "event": kind, "id": i, "evidence": str(o.get("evidence") or "")})
            counts[kind] += 1
    with open(events_path(), "a", encoding="utf-8", newline="\n") as f:
        for ln in lines:
            f.write(json.dumps(ln, ensure_ascii=False) + "\n")
    run = json.dumps({"session": session, "project": project, "at": now, "shown": [r["id"] for r in shown],
                      "counts": counts, "answer": answer}, ensure_ascii=False, indent=1)
    (d / "runs" / f"{session}.json").write_text(run, encoding="utf-8", newline="\n")
    return counts


def runs() -> list:
    d = shadow_dir() / "runs"
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def set_on(on: bool) -> Path:
    d = shadow_dir()
    d.mkdir(parents=True, exist_ok=True)
    flag = d / "ON"
    if on:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds") + "\n"
        flag.write_text(stamp, encoding="utf-8", newline="\n")
    elif flag.exists():
        os.unlink(flag)
    return flag
