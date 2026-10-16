"""transcript_rows: which rows are chat, which Claude Code version wrote a row, and which row types are new.

Rows are written the way Claude Code writes them (compact JSON, its key order, copied from a 2.1.300
transcript): the cheap type read leans on that order, so a row built in another order would test nothing.
"""
import json

import pytest

from ak import transcript_rows as tr

SID = "5f0c1e8a-0000-4000-8000-000000000001"


def line(row: dict) -> str:
    return json.dumps(row, separators=(",", ":"), ensure_ascii=False)


def _tail(version: str) -> dict:
    return {"userType": "external", "entrypoint": "cli", "cwd": "/tmp/p", "sessionId": SID,
            "version": version, "gitBranch": "main"}


def user(text, version="2.1.300"):
    return {"parentUuid": None, "isSidechain": False, "promptId": "p1", "type": "user",
            "message": {"role": "user", "content": text}, "uuid": "u1",
            "timestamp": "2026-10-15T10:00:00.000Z", **_tail(version)}


def assistant(blocks, version="2.1.300"):
    return {"parentUuid": "u1", "isSidechain": False,
            "message": {"id": "msg_1", "type": "message", "role": "assistant", "content": blocks},
            "requestId": "req_1", "type": "assistant", "uuid": "a1",
            "timestamp": "2026-10-15T10:00:01.000Z", **_tail(version)}


def attachment(att, version="2.1.300"):
    return {"parentUuid": "u1", "isSidechain": False, "attachment": att, "type": "attachment",
            "uuid": "t1", "timestamp": "2026-10-15T10:00:00.500Z", **_tail(version)}


def system(version="2.1.300"):
    return {"parentUuid": "a1", "isSidechain": False, "type": "system", "subtype": "stop_hook_summary",
            "hookCount": 1, "timestamp": "2026-10-15T10:00:02.000Z", "uuid": "s1", **_tail(version)}


def blob(message):
    return {"type": "api-request-blob", "sessionId": SID, "hash": "ab" * 32, "message": message}


def request(version="2.1.300"):
    return {"type": "api-request", "sessionId": SID, "id": "r1", "timestamp": "2026-10-15T10:00:00.900Z",
            "version": version, "querySource": "repl_main_thread", "shapeHash": "cd" * 32,
            "params": {"model": "claude-opus-5-5"}, "keep": 0, "tail": ["ab" * 32], "cacheControl": [],
            "requestDigest": "ef" * 32}


def shape(version="2.1.300"):
    return {"type": "api-request-shape", "sessionId": SID, "shapeHash": "cd" * 32,
            "timestamp": "2026-10-15T10:00:00.900Z", "version": version,
            "shape": {"system": [{"type": "text", "text": "You are Claude Code."}], "tools": []}}


# ── parse · at_least · has ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, want", [
    ("2.1.300", (2, 1, 300)),
    ("2.1.300 (Claude Code)", (2, 1, 300)),   # `claude --version`
    ("2.1.280-dev.20260901.abc", (2, 1, 280)),
    ("3.0.0", (3, 0, 0)),
    ("", None), (None, None), ("2.1", None), ("github-mcp-server/remote-1.2", None), (42, None),
])
def test_parse_reads_the_leading_release(text, want):
    assert tr.parse(text) == want


@pytest.mark.parametrize("version, want", [
    ("2.1.299", False), ("2.1.300", True), ("2.1.301", True), ("2.2.0", True), ("3.0.0", True),
    ("2.1.300-dev.x", True), ("2.1.30", False), (None, False),
])
def test_has_request_record_from_2_1_300(version, want):
    assert tr.has("request_record", version) is want
    assert tr.has("request_record", user("hi", version=version)) is want


def test_a_row_with_no_version_has_no_feature():
    assert tr.has("request_record", blob({"role": "user", "content": "hi"})) is False


def test_an_unknown_feature_is_an_error_not_a_quiet_no():
    with pytest.raises(KeyError):
        tr.has("request_recrod", "2.1.300")


# ── is_chat_row ─────────────────────────────────────────────────────────────────────────────────────────

def test_a_saved_request_body_is_never_a_chat_row():
    assert tr.is_chat_row(user("hi")) and tr.is_chat_row(assistant([{"type": "text", "text": "ok"}]))
    for r in (blob({"role": "user", "content": "hi"}), blob({"role": "assistant", "content": []}),
              request(), shape(), attachment({"type": "hook_success"}), system()):
        assert not tr.is_chat_row(r), r["type"]


# ── row_type ────────────────────────────────────────────────────────────────────────────────────────────

def test_row_type_is_the_rows_own_never_a_nested_one():
    rows = [user("hi"), assistant([{"type": "tool_use", "id": "t", "name": "Read", "input": {}}]),
            attachment({"type": "hook_success", "hookName": "SessionStart"}), system(),
            blob({"role": "user", "content": [{"type": "text", "text": "hi"}]}), request(), shape(),
            {"type": "last-prompt", "leafUuid": "u1", "sessionId": SID}]
    for r in rows:
        assert tr.row_type(line(r)) == r["type"]
        assert tr.row_type(line(r).encode()) == r["type"]


def test_row_type_falls_back_to_a_full_read_for_an_order_it_does_not_know():
    odd = {"sessionId": SID, "weird": {"type": "inner"}, "type": "brand-new"}
    assert tr.row_type(line(odd)) == "brand-new"


def test_row_type_of_a_line_that_is_not_a_row_is_empty():
    assert tr.row_type("") == "" and tr.row_type("{cut sho") == "" and tr.row_type("[1, 2]") == ""


# ── unknown_types ───────────────────────────────────────────────────────────────────────────────────────

def test_known_row_types_hold_the_request_record():
    assert tr.RECORD_TYPES <= tr.KNOWN_ROW_TYPES and tr.CHAT_TYPES <= tr.KNOWN_ROW_TYPES


def test_a_new_row_type_is_named_with_the_version_that_wrote_it(tmp_path):
    p = tmp_path / "s.jsonl"
    rows = [user("before the upgrade", version="2.1.299"),
            user("after it", version="2.1.305"),
            {"type": "api-request-frame", "sessionId": SID, "version": "2.1.305", "frame": 1},
            {"type": "api-request-note", "sessionId": SID, "note": "no version of its own"},
            request(version="2.1.305"), blob({"role": "user", "content": "x"})]
    p.write_text("\n".join(line(r) for r in rows) + "\n")
    assert tr.unknown_types(p) == {"api-request-frame": "2.1.305", "api-request-note": "2.1.305"}


def test_the_2_1_300_rows_would_have_been_named_on_day_one(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(line(r) for r in [user("hi"), shape(), request(), blob({"role": "user", "content": "hi"})]))
    before = tr.KNOWN_ROW_TYPES - tr.RECORD_TYPES
    assert tr.unknown_types(p, known=before) == dict.fromkeys(sorted(tr.RECORD_TYPES), "2.1.300")
    assert tr.unknown_types(p) == {}


def test_a_missing_file_has_no_unknown_types(tmp_path):
    assert tr.unknown_types(tmp_path / "gone.jsonl") == {}
