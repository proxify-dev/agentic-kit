"""The fake tool-call engine: a stand-in for traces/bin/traces that logs its argv and answers
each verb from a fixture file. Stdlib only, so the host's own output tests (plugins/ak/tests/
test_cli_output.py) load it by path to put an engine under `ak trace` without node.
"""
import json
import sys
from pathlib import Path

ROW = {
    "id": "toolu_01AAAAaaaaBBBBbbbbCCCCcccc", "kind": "tool", "parent": None,
    "ts": "2026-09-24T10:14:00.000Z", "durMs": 1234, "server": "claude", "tool": "Bash",
    "origin": "session",
    "identity": {"session": "6eced009-7527-4775-892d-53ee98721e9f", "prompt": None,
                 "cwd": "/Users/x/ak", "agent": None, "run": None, "item": None},
    "outcome": "ok",
    "summary": {"input": "{command:uv run pytest -q " + "x" * 200, "output": "19 passed",
                "label": "Run the tracer tests"},
    "blobs": {"input": "/t.jsonl:10", "output": "/t.jsonl:20"},
    "shape": {"bytesIn": 40, "bytesOut": 20000}, "witnesses": ["transcript"],
}
ERR = {**ROW, "id": "toolu_02DDDDddddEEEEeeeeFFFFffff", "ts": "2026-09-24T10:13:00.000Z",
       "server": "brain", "tool": "recall", "outcome": "error", "durMs": 12,  # brand: historical (the MCP server's name in a stored row)
       "summary": {"input": "query: [b]not markup[/b]", "output": "boom"}}
FIXTURES = {
    "list": {"rows": [ROW, ERR], "next": None},
    "stats": {"total": 150000, "today": 812, "errors": 97, "running": 1,
              "lastAt": "2026-09-24T10:14:00.000Z", "tools": {"Bash": 60000, "Read": 40000, "recall": 12}},
    "index": {"files": 3, "bytes": 52100, "ms": 180, "passes": 1, "locked": False},
    f"get-{ROW['id']}": {"row": ROW, "input": {"command": "uv run pytest -q", "description": "tests"},
                         "output": "line\n" * 4000},
    "purge": {"removed": 3},
}

# The fake engine. `<key>.json` is its stdout, `<key>.exit` its exit code, `<key>.err`
# its stderr line; a verb with no fixture is a miss (exit 1), the way the real one says
# "not found". Its key is the verb, or `get-<id>` for get.
FAKE = """#!{python}
import json, sys
from pathlib import Path
here = Path(__file__).resolve().parent
argv = sys.argv[1:]
with open(here / "argv.jsonl", "a") as f:
    f.write(json.dumps(argv) + "\\n")
key = f"get-{{argv[1]}}" if argv[0] == "get" else argv[0]
out, code, err = here / f"{{key}}.json", here / f"{{key}}.exit", here / f"{{key}}.err"
if err.exists():
    print(err.read_text().strip(), file=sys.stderr)
if code.exists():
    if out.exists():
        print(out.read_text())
    sys.exit(int(code.read_text()))
if not out.exists():
    print(f"traces: no tool call {{argv[-1]}}", file=sys.stderr)
    sys.exit(1)
doc = json.loads(out.read_text())
if argv[0] == "purge":
    doc["dryRun"] = "--dry-run" in argv
print(json.dumps(doc))
"""


def fake_engine(code: Path) -> Path:
    """A $AK_CODE checkout holding a fake plugins/tracer/traces/bin/traces and its fixtures; returns the bin dir."""
    bin_dir = code / "plugins" / "tracer" / "traces" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "traces"
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(0o755)
    for key, doc in FIXTURES.items():
        (bin_dir / f"{key}.json").write_text(json.dumps(doc))
    return bin_dir
