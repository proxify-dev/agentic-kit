"""observer types — the kinds of row the observer may write.

The table is hooks/memory_schema.py (TYPES): every type, its glyph and what it means. It is
read-only here: a new type is a line in that table, and the observer's prompt
(agents/observer.md) says how to judge it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import rich_click as click

from observer import ui  # this plugin's copy of ak's output door (plugins/AGENTS.md §6)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
import memory_schema as schema  # noqa: E402


@click.command("types")
@ui.json_option
def memory_types(as_json):
    """List the observation types, their glyphs and meanings."""
    ui.json_mode(as_json)
    reg = schema.registry()
    if ui.is_json():
        ui.emit(reg)
        return
    ui.kv([("verdicts", ", ".join(reg["verdicts"])),
           ("defaults", f"type={reg['type_default']} verdict={reg['verdict_default']}")],
          title="observation types", boxed=False)
    ui.table(["#", "", "type", "means"],
             [[i, ty["glyph"], ty["name"], ui.escape(ty["means"])] for i, ty in enumerate(reg["types"], 1)],
             justify=["right", None, None, None], box=None)
