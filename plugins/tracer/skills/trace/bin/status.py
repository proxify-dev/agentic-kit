#!/usr/bin/env python3
"""ak status — live agent roster for a Claude Code session.

    ak status <uuid|slug>            one-shot snapshot
    ak status <uuid|slug> --watch    refresh every 2s (top-style)
    ak status <uuid|slug> --json     structured roster for piping

Deterministic + stdlib-only: it reads the per-session transcript tree directly
(see lib/roster.py), so it is cheap enough to poll and needs no indexer/db.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))
sys.path.insert(0, str(SKILL_DIR.parents[1] / "hooks"))  # the plugin's generated _brand.py

from _brand import cmd  # noqa: E402
from lib.roster import (  # noqa: E402
    DONE,
    FAILED,
    IDLE,
    RUNNING,
    STALLED,
    UNKNOWN,
    Roster,
    build_roster,
    human_age,
)

CLEAR = "\x1b[2J\x1b[H"

GLYPH = {
    RUNNING: "🟢",
    STALLED: "🟠",
    FAILED: "❌",
    IDLE: "💤",
    DONE: "✅",
    UNKNOWN: "⚪",
}
COLOR = {
    RUNNING: "\x1b[32m",
    STALLED: "\x1b[33m",
    FAILED: "\x1b[31m",
    IDLE: "\x1b[2m",
    DONE: "\x1b[32m",
    UNKNOWN: "\x1b[2m",
}
RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
RULE = "─" * 60


def _paint(text: str, code: str, on: bool) -> str:
    return f"{code}{text}{RESET}" if on else text


def _row(a, color: bool, indent: str = "") -> str:
    glyph = GLYPH.get(a.state, "⚪")
    label = _paint(f"{a.state:<8}", COLOR.get(a.state, ""), color)
    name = a.name if len(a.name) <= 20 else a.name[:19] + "…"
    lines = _paint(f"{a.lines:>5} ln", DIM, color)
    return f"{indent}{glyph} {name:<20} {label} {a.detail:<28} {lines}"


def _tally(roster: Roster, color: bool) -> str:
    counts: dict[str, int] = {}
    for a in roster.agents:
        counts[a.state] = counts.get(a.state, 0) + 1
    order = [RUNNING, STALLED, FAILED, IDLE, DONE, UNKNOWN]
    parts = [
        _paint(f"{GLYPH[s]}{counts[s]} {s.lower()}", COLOR.get(s, ""), color)
        for s in order
        if counts.get(s)
    ]
    return "  ·  ".join(parts) if parts else _paint("no agents", DIM, color)


def render(roster: Roster, color: bool = False, show_all: bool = False) -> str:
    if roster.error:
        return _paint(f"{cmd('status')}: {roster.error}", COLOR[FAILED], color)

    out: list[str] = []
    team = roster.team or f"session-{roster.session_uuid[:8]}"
    live = roster.parent_age_s <= 45
    beat = _paint("● live", COLOR[RUNNING], color) if live else _paint(
        f"○ last write {human_age(roster.parent_age_s)} ago", DIM, color
    )
    head = f"{_paint(team, BOLD, color)}  ·  {len(roster.agents)} agents"
    if roster.model:
        head += f"  ·  {roster.model}"
    head += f"  ·  {roster.project}"
    out.append(head)
    out.append(_paint(f"uuid {roster.session_uuid}  ·  {beat}", DIM, color) if not color
               else f"{DIM}uuid {roster.session_uuid}{RESET}  ·  {beat}")
    out.append(_tally(roster, color))
    out.append(RULE)

    teammates = [a for a in roster.agents if a.kind == "teammate"]
    if teammates:
        out.append(_paint("TEAMMATES", BOLD, color))
        out.extend(_row(a, color) for a in teammates)
        out.append("")

    for wf in roster.workflows:
        members = [a for a in roster.agents if a.wf_id == wf.wf_id]
        toks = f"  ·  {wf.tokens/1000:.0f}k tok" if wf.tokens else ""
        phase = f"  ·  {wf.phase}" if wf.phase else ""
        reported = f" (wf reports {wf.agent_count})" if wf.agent_count and wf.agent_count != len(members) else ""
        wf_state = DONE if wf.status in {"completed", "failed"} else RUNNING
        header = (
            f"{_paint('WORKFLOW', BOLD, color)} {wf.name}  ·  "
            f"{_paint(wf.status, COLOR.get(wf_state, ''), color)}"
            f"  ·  {len(members)} agents{reported}{toks}{phase}"
        )
        out.append(header)
        # A status view surfaces what needs attention. Collapse finished agents
        # to a one-line summary unless --all; always show live/stalled/failed.
        hot = [a for a in members if a.state in {RUNNING, STALLED, FAILED}]
        rest = [a for a in members if a.state not in {RUNNING, STALLED, FAILED}]
        shown = members if show_all else hot
        out.extend(_row(a, color, indent="  ") for a in shown)
        if rest and not show_all:
            done_n = sum(1 for a in rest if a.state == DONE)
            idle_n = len(rest) - done_n
            bits = [f"{GLYPH[DONE]} {done_n} done"] if done_n else []
            if idle_n:
                bits.append(f"{GLYPH[IDLE]} {idle_n} idle")
            out.append(_paint(f"  └ {'  ·  '.join(bits)} (--all to expand)", DIM, color))
        out.append("")

    if not roster.agents:
        out.append(_paint("solo session — no sub-agents spawned.", DIM, color))

    out.append(_paint(f"generated {time.strftime('%H:%M:%S', time.localtime(roster.generated_at))}"
                      "  ·  --watch to refresh", DIM, color))
    return "\n".join(out).rstrip()


def _to_dict(roster: Roster) -> dict:
    d = dataclasses.asdict(roster)
    counts: dict[str, int] = {}
    for a in roster.agents:
        counts[a.state] = counts.get(a.state, 0) + 1
    d["state_counts"] = counts
    return d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog=cmd("status"),
        description="Live agent roster for a Claude Code session.",
    )
    ap.add_argument("target", help="session UUID or slug (required)")
    ap.add_argument("--watch", "-w", action="store_true", help="refresh continuously")
    ap.add_argument("--interval", "-n", type=float, default=2.0, help="refresh seconds (with --watch)")
    ap.add_argument("--all", "-a", action="store_true", help="list finished agents too (default collapses them)")
    ap.add_argument("--json", action="store_true", help="emit structured roster and exit")
    ap.add_argument("--no-color", action="store_true", help="disable ANSI color")
    args = ap.parse_args(argv)

    if args.json:
        print(json.dumps(_to_dict(build_roster(args.target)), indent=2, default=str))
        return 0

    color = sys.stdout.isatty() and not args.no_color

    if not args.watch:
        roster = build_roster(args.target)
        print(render(roster, color, show_all=args.all))
        return 0 if not roster.error else 1

    try:
        while True:
            roster = build_roster(args.target)
            footer = _paint(
                f"↻ every {args.interval:g}s  ·  Ctrl-C to quit", DIM, color
            )
            sys.stdout.write(CLEAR + render(roster, color, show_all=args.all) + "\n" + footer + "\n")
            sys.stdout.flush()
            time.sleep(max(0.25, args.interval))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
