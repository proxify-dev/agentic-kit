# Derived from Claude-Mem v13.11.0 (Apache-2.0, Copyright 2026 Alex Newman) and modified: the type names and glyphs. See NOTICE.
"""The observation types — what a row in the store may be, and how it is drawn.

A row carries a `type` and a `verdict`. The words allowed there, their glyphs and what each
means are one table, TYPES, below. It used to be a folder of records a vault could switch
on and off (`loadout/memory/episodic/`); that experiment was archived on 2026-09-25. A new
type is a line here, and the observer's prompt (agents/observer.md) says how to judge it.

Dependency-free and cheap: SessionStart, the summariser and `observer types` all import it.
"""
from __future__ import annotations

# name, glyph, emoji (the Read hook's variant), what it means. The order is the legend's.
TYPES = [
    ("bugfix", "●", "🔴", "something broken was made to work"),
    ("feature", "◆", "🟣", "something new was built"),
    ("refactor", "↻", "🔄", "the shape changed, the behaviour did not"),
    ("change", "✓", "✅", "a change that is none of the others"),
    ("discovery", "○", "🔵", "something turned out to be true"),
    ("decision", "⚖", "⚖️", "a fork was taken, and the reason"),
    ("security_alert", "⚠", "⚠️", "a live risk was found"),
    ("security_note", "⚷", "🔒", "a security fact, not urgent"),
    ("correction", "✋", "✋", "the person overrode the agent — the rule, in their own words"),
]
# `violated` is the verdict a correction carries: the action may have worked, the person said no.
VERDICTS = ["worked", "dead-end", "superseded", "neutral", "violated"]
# What a row gets when the model writes a word outside the table: a row with no verdict must
# never be mistaken for a route that worked.
TYPE_DEFAULT = "discovery"
VERDICT_DEFAULT = "neutral"


def registry() -> dict:
    """The table in the shape the callers read: {"types": [{name, glyph, emoji, means}],
    "verdicts", "type_default", "verdict_default"}."""
    return {
        "types": [{"name": n, "glyph": g, "emoji": e, "means": m} for n, g, e, m in TYPES],
        "verdicts": list(VERDICTS),
        "type_default": TYPE_DEFAULT,
        "verdict_default": VERDICT_DEFAULT,
    }


def types(reg: dict | None = None) -> list[str]:
    reg = reg or registry()
    return [t["name"] for t in reg["types"]]


def verdicts(reg: dict | None = None) -> list[str]:
    reg = reg or registry()
    return list(reg["verdicts"])


def glyphs(reg: dict | None = None, emoji: bool = False) -> dict[str, str]:
    reg = reg or registry()
    key = "emoji" if emoji else "glyph"
    return {t["name"]: t[key] for t in reg["types"]}


def legend(reg: dict | None = None) -> str:
    """The welcome block's first line, claude-mem's shape: `🎯session ●bugfix ◆feature …`."""
    reg = reg or registry()
    return ("Legend: 🎯session " + " ".join(f"{t['glyph']}{t['name']}" for t in reg["types"])
            + "  (✗ = a route that did not work)")


def legend_wide(reg: dict | None = None) -> str:
    """The terminal variant: `session-request | ● bugfix | ◆ feature | … | ✗ dead-end`."""
    reg = reg or registry()
    return ("Legend: session-request | " + " | ".join(f"{t['glyph']} {t['name']}" for t in reg["types"])
            + " | ✗ dead-end")


def choice(reg: dict | None = None) -> str:
    """`decision|bugfix|…` — the shape a tool description quotes."""
    return "|".join(types(reg))


def prompt_types(reg: dict | None = None) -> str:
    """The TYPES block the observer's message carries: one line per type (`name — means`)."""
    reg = reg or registry()
    return ("TYPES (the obs_type vocabulary — what each one is for):\n"
            + "\n".join(f"- {t['name']} — {t['means']}" for t in reg["types"]))
