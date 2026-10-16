"""observer shadow — the issues trial: on · off · run · report (the data: hooks/shadow_issues.py).

While it is on, every session the observer reads is read a second time with the project's open issues in
front of it, and its answer (what was opened, fixed, moved on) goes only to <kit>/db/shadow/issues/.
`report` scores it: what it holds open and closed, and, in a project with memory on, against the issue
notes there (`.agents/memory/issues/`, each with the session that opened it and the one that closed it).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import rich_click as click

from observer import ui
from observer.groups import writes

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
import shadow_issues as si  # noqa: E402
from vault_root import db_dir  # noqa: E402

from ._brand import env_name  # noqa: E402
from .verbs import command  # noqa: E402

SUMMARIZER = PLUGIN_ROOT / "scripts" / "summarize-session.py"


@click.group(name="shadow", invoke_without_command=True,
             epilog="\b\nExamples:\n  observer shadow on\n  observer shadow run 1a2b3c4d\n  observer shadow report")
@click.pass_context
def shadow(ctx):
    """Trial: can the observer follow an issue to its fix?

    While [b]on[/], each session the observer reads is read again with the project's open issues
    in front of it; that run writes only to <kit>/db/shadow/issues/, never the store.
    [b]report[/] shows how it does.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(shadow_status)


@shadow.command("status")
@ui.json_option
def shadow_status(as_json):
    """Show whether the trial is on and what it holds."""
    ui.json_mode(as_json)
    all_ = si.issues()
    data = {"on": si.enabled(), "folder": str(si.shadow_dir()), "runs": len(si.runs()),
            "issues": len(all_), "open": sum(1 for v in all_.values() if not v["resolved_in"])}
    if ui.is_json():
        ui.emit(data)
        return
    ui.kv([("trial", "on" if data["on"] else "off")] + [(k, v) for k, v in data.items() if k != "on"])
    if not data["on"]:
        ui.hint(command("shadow on"))


@writes
@shadow.command("on")
def shadow_on():
    """Start the trial: every session end reads the session twice."""
    si.set_on(True)
    ui.ok(f"issues trial on · its output: {si.shadow_dir()}")
    ui.hint(command("shadow report"))


@writes
@shadow.command("off")
def shadow_off():
    """Stop the trial; what it wrote stays."""
    si.set_on(False)
    ui.ok("issues trial off · its output stays")


def _transcript(ref: str) -> Path:
    from observer.observer import _find_transcript
    return _find_transcript(ref)


@writes
@shadow.command("run", epilog="\b\nExamples:\n  observer shadow run 1a2b3c4d 5e6f7a8b\n  observer shadow run 1a2b3c4d --dry-run")
@click.argument("sessions", nargs=-1, required=True, help="session ids or prefixes, oldest first, e.g. 1a2b3c4d")
@click.option("--dry-run", is_flag=True, help="print the issues section, record nothing")
@click.option("--model", default="", help="override the observer model for this run")
def shadow_run(sessions, dry_run, model):
    """Run the trial on past SESSIONS, in the order given."""
    rows = []
    for ref in sessions:
        p = _transcript(ref)
        argv = [sys.executable, str(SUMMARIZER), "--session", p.stem, "--transcript", str(p), "--shadow"]
        argv += ["--dry-run"] if dry_run else []
        argv += ["--model", model] if model else []
        try:
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=400, env={**os.environ, env_name("CAPTURE_DISABLE"): "1"})
            out = r.stdout.strip() or r.stderr.strip()[-200:] or "no output"
        except subprocess.TimeoutExpired:
            out = "timeout"
        if dry_run:
            ui.panel(out, title=p.stem[:8])
            continue
        rows.append([p.stem[:8], out.splitlines()[-1].replace("shadow: ", "")])
    if rows:
        ui.table(["session", "result"], rows, box=None)


# ── the person's own record: issue notes in projects with memory on ──────────
_FM = re.compile(r"^---\n(.*?)\n---\n", re.S)
_CLOSED = re.compile(r"closed.*\(session ([0-9a-f]{8})\)", re.I)
_WORD = re.compile(r"[a-z0-9]{3,}")


def _frontmatter(text: str) -> dict:
    m = _FM.match(text)
    out: dict = {}
    if not m:
        return out
    key = None
    for line in m.group(1).splitlines():
        if line.startswith("  - ") and key:
            out.setdefault(key, []).append(line[4:].strip())
            continue
        k, sep, v = line.partition(":")
        if sep and not line.startswith(" "):
            key = k.strip()
            out[key] = v.strip().strip('"') if v.strip() else []
    return out


def _cites(v) -> list:
    """The paths a note cites: `cites:` as a list or as one value (`cites: "a.mjs:fn"`), the `:symbol` cut."""
    items = v if isinstance(v, list) else [v] if v else []
    return [p for p in (str(c).strip().strip('"\'').partition(":")[0] for c in items) if len(p) > 2]


def memory_issues() -> list:
    """[{"project", "path", "title", "status", "opened_in", "closed_in", "cites"}] for each issue note in
    each project memory is on in (<kit>/db/memory-projects.json)."""
    try:
        on = json.loads((Path(db_dir()) / "memory-projects.json").read_text(encoding="utf-8")).get("on") or {}
    except (OSError, ValueError):
        return []
    out = []
    for ws in on:
        folder = Path(ws) / ".agents" / "memory"
        for p in sorted(folder.rglob("*.md")) if folder.is_dir() else []:
            text = p.read_text(encoding="utf-8", errors="replace")
            fm = _frontmatter(text)
            if fm.get("type") != "issue":
                continue
            title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), p.stem)
            closed = _CLOSED.search(text)
            out.append({"project": Path(ws).name, "path": str(p), "title": title, "status": fm.get("status", ""),
                        "opened_in": str(fm.get("created_by") or "")[:8],
                        "closed_in": closed.group(1) if closed else "",
                        "cites": _cites(fm.get("cites"))})
    return out


def _words(s: str) -> set:
    return set(_WORD.findall(s.lower()))


def match(note: dict, shadow_issues: list) -> dict | None:
    """The trial's issue that is NOTE: one opened in the same session or naming a file NOTE cites, whose title
    shares the most words with NOTE's (a third of the shorter title at least)."""
    best, score = None, 0.0
    for s in shadow_issues:
        if s["project"] != note["project"]:
            continue
        a, b = _words(note["title"]), _words(s["title"])
        overlap = len(a & b) / max(1, min(len(a), len(b)))
        same_file = any(c and any(c in x for x in s.get("anchors") or []) for c in note["cites"])
        if overlap < 0.34 and not (same_file and s["opened_in"][:8] == note["opened_in"]):
            continue
        sc = overlap + (0.5 if same_file else 0) + (0.5 if s["opened_in"][:8] == note["opened_in"] else 0)
        if sc > score:
            best, score = s, sc
    return best


def _age(iso: str) -> str:
    return iso[:10] if iso else "-"


@shadow.command("report")
@click.option("--project", help="only this project label, e.g. my-app")
@ui.json_option
def shadow_report(project, as_json):
    """Show what the trial opened and closed, and how it scores."""
    ui.json_mode(as_json)
    all_ = [v for v in si.issues().values() if not project or v["project"] == project]
    runs = [r for r in si.runs() if not project or r.get("project") == project]
    open_ = [v for v in all_ if not v["resolved_in"]]
    later = [v for v in all_ if v["resolved_in"] and not v["fixed_here"]]
    same = [v for v in all_ if v["fixed_here"]]
    notes = [n for n in memory_issues() if not project or n["project"] == project]
    graded = []
    for n in notes:
        s = match(n, all_)
        closed_ok = None
        if n["closed_in"]:
            closed_ok = bool(s and s["resolved_in"][:8] == n["closed_in"])
        graded.append({**n, "shadow": s["id"] if s else None,
                       "opened_caught": bool(s and s["opened_in"][:8] == n["opened_in"]),
                       "closed_caught": closed_ok})
    data = {"on": si.enabled(), "runs": len(runs),
            "counts": {"open": len(open_), "closed_later": len(later), "fixed_same_session": len(same),
                       "progress_lines": sum(len(v["progress"]) for v in all_),
                       "unknown_ids_dropped": sum((r.get("counts") or {}).get("unknown", 0) for r in runs)},
            "open": open_, "closed_later": later, "against_memory": graded}
    if ui.is_json():
        ui.emit(data)
        return
    ui.kv([("trial", "on" if data["on"] else "off"), ("sessions read", len(runs))]
          + [(k.replace("_", " "), v) for k, v in data["counts"].items()])
    if later:
        ui.table(["id", "project", "opened", "closed", "title"],
                 [[v["id"], v["project"], f"{_age(v['opened_at'])} {v['opened_in'][:8]}",
                   f"{_age(v['resolved_at'])} {v['resolved_in'][:8]}", ui.escape(v["title"])] for v in later],
                 title="closed by a later session", box=None)
    if open_:
        ui.table(["id", "project", "opened", "moved", "title"],
                 [[v["id"], v["project"], f"{_age(v['opened_at'])} {v['opened_in'][:8]}", len(v["progress"]),
                   ui.escape(v["title"])] for v in sorted(open_, key=lambda v: v["opened_at"])],
                 title="still open", box=None)
    if graded:
        def mark(x):
            return "-" if x is None else ("yes" if x else "NO")
        ui.table(["memory note", "opened in", "caught", "closed in", "caught", "trial id"],
                 [[ui.escape(g["title"][:60]), g["opened_in"] or "-", mark(g["opened_caught"]),
                   g["closed_in"] or "-", mark(g["closed_caught"]), g["shadow"] or "-"] for g in graded],
                 title="against the issue notes in memory", box=None)
    if not runs:
        ui.hint(command("shadow run <session>"))
