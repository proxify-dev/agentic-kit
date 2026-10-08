"""The tracer ships no MCP server: the shell is its one door (`ak sessions …`, `ak trace …`).

The server (`tracer`, its convo_* tools) went once a shell-only arm matched the MCP arms on every
routing case (evals/tool-routing, arm D). What an agent reads — the skills, the hooks, the
code's own hints — names verbs, never a convo_* tool. Tests and fixtures still spell
`mcp__plugin_tracer_tracer__…`: past sessions called those tools, and the tracer reads past
sessions (turns.short_tool shortens the name; step_label_cases.json labels its calls).
"""
import json
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
READ_BY_AGENTS = ("hooks", "skills", "scripts", "src")
# the arena's records and graders, not read by the agent under test: its baselines (what agents called
# back then) and its tasks, whose ground_truth names the tool that captured it (its verifiers read Bash)
RECORDED = (PLUGIN / "skills" / "index" / "arena" / "baselines", PLUGIN / "skills" / "index" / "arena" / "tasks")
# a past tool name the code reads, as data: short_tool's own example
READS_PAST_NAMES = {PLUGIN / "src" / "tracer" / "turns.py"}
TOOL = re.compile(r"mcp__plugin_(?:tracer|brain)_[a-z-]*tracer__|\bconvo_(?:query|dialogue|stats|patterns|drill"
                  r"|step|projects|search|trace)\b")


def test_no_server_is_shipped():
    assert not (PLUGIN / ".mcp.json").exists()
    assert not (PLUGIN / "src" / "tracer" / "mcp_server.py").exists()
    pyproject = (PLUGIN / "pyproject.toml").read_text()
    assert "fastmcp" not in pyproject and "tracer-mcp" not in pyproject
    assert "mcpServers" not in json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())


def test_what_an_agent_reads_names_no_tracer_tool():
    stale: list[str] = []
    for folder in READ_BY_AGENTS:
        for path in (PLUGIN / folder).rglob("*"):
            if (path.suffix not in {".py", ".yaml", ".yml", ".md", ".json", ".sh", ".j2"} or ".venv" in path.parts
                    or path in READS_PAST_NAMES or any(r in path.parents for r in RECORDED)):
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if TOOL.search(line):
                    stale.append(f"{path.relative_to(PLUGIN)}:{n}: {line.strip()[:100]}")
    assert not stale, "names a tracer MCP tool, which is gone — name the verb:\n  " + "\n  ".join(stale)
