"""A row's files_modified is kept only where the session's tool events show a write to that file."""
import importlib.util
from pathlib import Path

SUMMARIZER = Path(__file__).resolve().parent.parent / "scripts" / "summarize-session.py"
_spec = importlib.util.spec_from_file_location("summarize_session_gate", SUMMARIZER)
summarizer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(summarizer)

EVENTS = [
    {"kind": "session"},
    {"kind": "tool", "tool": "Read", "files_read": ["/repo/README.md"]},
    {"kind": "tool", "tool": "Edit", "files_modified": ["/repo/src/auth.py"]},
]


def test_a_file_only_read_is_dropped_and_a_written_one_gets_its_real_path():
    obs = [{"files_modified": ["auth.py", "/repo/README.md", "src/auth.py"]}]
    out = summarizer.gate_files_modified(obs, EVENTS)
    assert out[0]["files_modified"] == ["/repo/src/auth.py"]


def test_no_tool_events_means_no_evidence_and_the_list_stands():
    obs = [{"files_modified": ["/repo/x.py"]}]
    assert summarizer.gate_files_modified(obs, [{"kind": "session"}])[0]["files_modified"] == ["/repo/x.py"]
