#!/usr/bin/env python3
"""The evidence pack the Level 3 guide readers read: the laptop's version of a call transcript.

The call scorer (2026-10-22) lets AI readers decide Level 3 by judging a member's whole call
against the guide, instead of counting. On a laptop there is no call; there are the person's own records. This
writes them as one markdown file a reader can judge the same way:

  - what the scan saw, per guide area (instructions, corrections, memory, attention, tools, reach), with the
    person's own words where the logs hold them (prompt excerpts the story already shows on their page);
  - who made each thing: written by them, co-written with others, installed, or Claude Code's own default
    (the "tool did it" split), so a reader never credits a plugin or a template to the person;
  - where to look: their own rule files and memory index, so a reader can open the real thing (read-only);
  - what the logs cannot see, so "not seen" is never read as "doesn't do it".

Stays on the person's machine (~/agentic-setup). Readers run in their own Claude Code.
Run: python3 -I evidence_pack.py STORY.json FACTS.json LEVEL.json -o evidence.md
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

HOME = Path.home()

# story section id -> the guide area it speaks to (the guide's Level 3 areas A-F, guide-all.md)
AREAS = [
    ("A. It knows without being told (instructions)", ["context", "ladder"]),
    ("B. It learns from its mistakes (corrections)", ["wrong"]),
    ("C. It remembers (memory across time)", ["memory", "sessions"]),
    ("D. It stays sharp during the work (attention)", ["helpers", "builtins"]),
    ("E. It can reach what it needs (tools)", ["handover", "start"]),
    ("F. It works for more than them (reach)", ["built", "crosstalk"]),
]


def text_of(p) -> str:
    return p if isinstance(p, str) else (p.get("text") or "")


def section_md(s: dict) -> list[str]:
    out = [f"### {s['title']}", "", f"**In one line:** {s.get('lead', '')}", ""]
    for p in s.get("points") or []:
        t = text_of(p)
        if t:
            cav = (p.get("how") or {}).get("caveat") if isinstance(p, dict) else None
            out.append(f"- {t}" + (f" _(can't be sure: {cav})_" if cav else ""))
    for st in s.get("ladder") or []:
        out.append(f"- {'✓' if st.get('done') else '✗'} {st.get('step')}: {st.get('evidence', '')}")
    for n in s.get("numbers") or []:
        if isinstance(n, dict) and n.get("value") is not None:
            out.append(f"- {n.get('label', '')}: {n.get('value')}")
    ex = s.get("examples") or []
    if ex:
        out += ["", "Their own words (excerpts the logs hold):"]
        for e in ex[:6]:
            body = " ".join((e.get("text") or "").split())[:400]
            out.append(f"- [{e.get('label', 'moment')}] \"{body}\" — {e.get('meta', '')}")
    out.append("")
    return out


def where_to_look(facts: dict, level: dict) -> list[str]:
    ex = level.get("extra") or {}
    out = ["## Where to look (open these yourself, read-only)", ""]
    g = HOME / ".claude" / "CLAUDE.md"
    if g.is_file():
        out.append(f"- Their personal rules file: {g}")
    for r in facts["setup"]["repos_worked_in"].get("top", []):
        root = Path(os.path.expanduser(r["repo"]))
        for name in ("CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.md", "CLAUDE.local.md"):
            if (root / name).is_file():
                out.append(f"- {r['repo']}/{name} (repo with {r['sessions']} of their chats)")
    for x in (ex.get("rules") or {}).get("repos", []):
        if x.get("nested_rule_files"):
            out.append(f"- {x['repo']}: {x['nested_rule_files']} rule files below the root; their commits to them in 90 days: "
                       f"{x['by_person']}, other people's: {x['other_authors']}")
    for m in (ex.get("memory") or {}).get("own_stores", [])[:5]:
        idx = next((Path(os.path.expanduser(m["path"])) / n for n in ("MEMORY.md", "README.md", "INDEX.md")
                    if (Path(os.path.expanduser(m["path"])) / n).is_file()), None)
        out.append(f"- Memory store {m['path']}: {m['files']} notes, changed on {m['days_touched']} days"
                   + (f"; index: {idx}" if idx else ""))
    out.append("")
    return out


def who_made_it(level: dict) -> list[str]:
    ex = level.get("extra") or {}
    from collections import Counter
    sk = Counter(x.get("owner") or ("installed" if x.get("is_symlink") else "own") for x in ex.get("own_skills", []))
    own_p = [p["plugin"] for p in ex.get("plugins", []) if p.get("own")]
    other_p = [p["plugin"] for p in ex.get("plugins", []) if not p.get("own")]
    mem = ex.get("memory") or {}
    return ["## Who made it (git history decides; never credit the person for what a tool or someone else made)", "",
            f"- Their git identity, as far as the scan could tell: {', '.join(ex.get('identities', [])) or 'unknown'}",
            f"- Skills: {sk.get('own', 0)} they wrote, {sk.get('team', 0)} they co-wrote with others, {sk.get('installed', 0)} installed",
            f"- Plugins from their own git: {len(own_p)}; installed from others: {len(other_p)}",
            f"- Claude Code's own default memory (written by Claude Code, not designed by them): "
            f"{mem.get('claude_code_default_memory_files', 0)} notes. It counts only if the records show them shaping it.",
            ""]


def not_seen(facts: dict) -> list[str]:
    lg = facts["habits"].get("coverage", {}).get("logs", {})
    return ["## What these records cannot show (NOT SEEN IS NOT MISSING)", "",
            f"- Only this laptop, only Claude Code, {lg.get('oldest_record_days') or lg.get('window_days')} days back "
            f"(retention {lg.get('cleanup_days') or 30} days).",
            "- Work in other tools (Cursor, Copilot), other machines, devcontainers, CI and cloud jobs.",
            "- What they read in the editor, what they plan in tickets, what they agree in reviews.",
            "- Corrections are found by an English word list; other languages and polite corrections are missed.",
            ""]


def counted_lines(level: dict) -> list[str]:
    """The Level 1 and 2 lines as numbers only: what was measured and how, never the bar or met/not met, so the
    readers judge one picture of how the person works (v14.2) instead of re-reading place_level.py's verdict."""
    out = ["## How the work runs, counted (the guide's Level 1 and Level 2 lines)", "",
           "Measured over their typed chats. Numbers only: they show how the work runs, they are not a verdict.", ""]
    for lv in ("1", "2"):
        for r in (level.get("levels") or {}).get(lv) or []:
            if r.get("value"):
                out.append(f"- **{r.get('guide_line') or r.get('id')}** ({r.get('question') or ''}): {r['value']}"
                           + (f" _(how: {r['why']})_" if r.get("why") else ""))
    return out + [""]


def build(story: dict, facts: dict, level: dict) -> str:
    S = {s["id"]: s for s in story.get("sections", [])}
    h = facts["habits"]["sessions"]
    lines = ["# Evidence pack: how this person works with coding agents (from their own laptop)", "",
             "This replaces a call transcript. It was built from the files Claude Code keeps on their machine: their "
             "chats, settings, rule files, memory and git history. Their own words appear only as short excerpts.", "",
             f"Read from: {h.get('active_days')} active days, {h.get('with_a_person_typing')} chats they typed in, "
             f"{facts['habits']['evidence'].get('sessions_with_code_edits')} where the agent changed files.", ""]
    lines += who_made_it(level)
    lines += ["## What the records show, by the guide's areas", ""]
    for area, ids in AREAS:
        lines += [f"## {area}", ""]
        for i in ids:
            if i in S:
                lines += section_md(S[i])
    lines += counted_lines(level)
    lines += where_to_look(facts, level)
    lines += not_seen(facts)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("story")
    ap.add_argument("facts")
    ap.add_argument("level")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    Path(a.out).write_text(build(read(a.story), read(a.facts), read(a.level)), encoding="utf-8")
    print(a.out)


if __name__ == "__main__":
    main()
