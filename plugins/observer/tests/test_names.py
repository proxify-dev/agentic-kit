"""This plugin serves no MCP tools: recall and curation are `ak observer …` verbs.

A tool literal spelled against this plugin's name, or against the older umbrella's, names a
tool nothing serves — an agent told to call it finds nothing there, so none may ship.
"""
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SPELLED_OUT = ("agents", "hooks", "skills", "scripts", "src", "tests", "templates")
TOOL_LITERAL = re.compile(r"mcp__plugin_observer_([a-z-]+)__")
OLD = re.compile("mcp__plugin_brain_" + "brain-observer__")  # brand: historical


def _texts():
    yield from ((p, p.read_text(encoding="utf-8", errors="replace"))
                for p in (PLUGIN / "README.md", PLUGIN / ".claude-plugin" / "plugin.json"))
    for folder in SPELLED_OUT:
        for p in (PLUGIN / folder).rglob("*"):
            if p.is_file() and ".venv" not in p.parts and p.suffix in {".py", ".yaml", ".yml", ".md", ".json", ".sh"}:
                yield p, p.read_text(encoding="utf-8", errors="replace")


def test_no_mcp_config_ships():
    assert not (PLUGIN / ".mcp.json").exists()


def test_no_literal_names_a_tool_of_this_plugin():
    strays = [f"{p.relative_to(PLUGIN)}: {m.group(0)}" for p, t in _texts() for m in TOOL_LITERAL.finditer(t)]
    assert not strays, "tool names this plugin does not serve:\n  " + "\n  ".join(strays)


def test_no_literal_still_spells_the_old_umbrella():
    hits = [str(p.relative_to(PLUGIN)) for p, t in _texts() if OLD.search(t)]
    assert not hits, hits
