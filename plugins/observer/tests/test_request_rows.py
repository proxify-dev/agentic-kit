"""Claude Code 2.1.300 saves every model call in the transcript; the observer reads only the chat.

The saved calls add `api-request-blob` rows: a message body as it was sent, with `message.role` and no
version. A user blob carries what Claude Code put around the prompt (the CLAUDE.md block, gitStatus),
an assistant blob repeats the reply's thinking and tool calls. Read by `message.role`, those bodies went
into the summary a second time — over 122 real 2.1.300 sessions (2026-10-15), 114 digests carried the
whole CLAUDE.md block — and their tool calls counted twice toward "substantive".

Rows are written in Claude Code's key order and compact JSON, copied from a 2.1.300 transcript.
"""
import importlib.util
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
import capture_common as cc  # noqa: E402

SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROMPT = "please fix the login redirect"
CLAUDE_MD = ("<system-reminder>\nAs you answer the user's questions, you can use the following context:\n"
             "Codebase and user instructions are shown below.\n</system-reminder>")


def _summarizer():
    spec = importlib.util.spec_from_file_location("summarize_session", PLUGIN / "scripts" / "summarize-session.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tail(version):
    return {"userType": "external", "entrypoint": "cli", "cwd": "/tmp/p", "sessionId": SID,
            "version": version, "gitBranch": "main"}


def _read(n):
    return {"type": "tool_use", "id": f"toolu_{n}", "name": "Read", "input": {"file_path": f"/tmp/p/f{n}.py"}}


def transcript(tmp_path, version="2.1.300", saved_calls=True) -> str:
    rows = [
        {"parentUuid": None, "isSidechain": False, "promptId": "p1", "type": "user",
         "message": {"role": "user", "content": PROMPT}, "uuid": "u1",
         "timestamp": "2026-10-15T10:00:00.000Z", **_tail(version)},
        {"parentUuid": "u1", "isSidechain": False,
         "message": {"id": "msg_1", "type": "message", "role": "assistant",
                     "content": [_read(1), {"type": "text", "text": "the redirect drops the query string"}]},
         "requestId": "req_1", "type": "assistant", "uuid": "a1",
         "timestamp": "2026-10-15T10:00:05.000Z", **_tail(version)},
    ]
    if saved_calls:
        rows[1:1] = [
            {"type": "api-request-blob", "sessionId": SID, "hash": "ab" * 32,
             "message": {"role": "user", "content": [{"type": "text", "text": CLAUDE_MD},
                                                      {"type": "text", "text": PROMPT}]}},
            {"type": "api-request", "sessionId": SID, "id": "r1", "timestamp": "2026-10-15T10:00:01.000Z",
             "version": version, "querySource": "repl_main_thread", "shapeHash": "cd" * 32,
             "params": {"model": "claude-opus-5-5"}, "keep": 0, "tail": ["ab" * 32], "cacheControl": [],
             "requestDigest": "ef" * 32},
        ]
        rows.append({"type": "api-request-blob", "sessionId": SID, "hash": "12" * 32,
                     "message": {"role": "assistant", "content": [_read(n) for n in range(1, 6)]}})
    p = tmp_path / f"{SID}.jsonl"
    p.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n")
    return str(p)


def test_the_digest_reads_each_turn_once_and_no_saved_body(tmp_path):
    summarizer = _summarizer()
    digest = summarizer.transcript_digest(transcript(tmp_path))
    assert digest.count(PROMPT) == 1, digest
    assert "Codebase and user instructions" not in digest and "<system-reminder>" not in digest
    (tmp_path / "chat-only").mkdir()
    assert digest == summarizer.transcript_digest(transcript(tmp_path / "chat-only", saved_calls=False))


def test_tool_calls_in_a_saved_body_do_not_count(tmp_path):
    p = transcript(tmp_path)
    assert cc.transcript_activity(p) == (False, 1)
    assert cc.is_substantive(p) is False


def test_a_transcript_from_before_2_1_300_reads_as_it_always_did(tmp_path):
    p = transcript(tmp_path, version="2.1.299", saved_calls=False)
    assert cc.transcript_activity(p) == (False, 1)
    digest = _summarizer().transcript_digest(p)
    assert digest.startswith(f"USER: {PROMPT}") and digest.count(PROMPT) == 1
