"""The wire in the drill: each transcript message beside the gateway call that produced it.

The transcript says what the agent did; the gateway's trace says what each call cost, how
long the model took to answer, which account served it, and what it was given. Joined, one
drill answers "which step broke the cache" — until then it was two tools and a timestamp
guess.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import asst, call, result, text, ts, user, write_jsonl

from tracer import drill, wire
from tracer._brand import env
from tracer.drill import format_session, load_session, open_steps

SID = "cccccccc-0000-4000-8000-000000000001"
U = [  # per message: input, cache read, cache write, output
    (2, 0, 5000, 10),     # msg_1: the session's first call writes the cache — no miss
    (2, 5000, 100, 20),   # msg_2
    (2, 0, 5200, 30),     # msg_3: wrote more than it read — a miss
    (2, 5200, 50, 5),     # msg_4
]


def usage(i):
    a, r, w, o = U[i]
    return {"input_tokens": a, "cache_read_input_tokens": r, "cache_creation_input_tokens": w, "output_tokens": o}


def row(sec, rid, *, mid=None, cls="main", system="s1", tools="t1", account="a", u=None, v=2, session=SID, ttfb=900):
    r = {"v": v, "id": rid, "ts": ts(sec), "path": "/v1/messages", "method": "POST", "status": 200,
         "stream": True, "stopReason": "end_turn", "durMs": 2000, "ttfbMs": ttfb,
         "account": account, "accountSource": "rotated", "model": "claude-test",
         "identity": {"session": session}, "hints": {"requestClass": cls, "agentType": None},
         "blobs": {"system": system, "tools": tools, "body": None},
         "shape": {"messages": 3, "tools": 20, "systemBlocks": 3, "bytesIn": 4096, "bytesOut": 100},
         "usage": u or {"input": 1, "output": 1, "cacheRead": 0, "cacheCreation": 0}, "policy": [], "error": None}
    if mid:
        r["messageId"] = mid
    return r


def wire_usage(i, doubled=False):
    a, r, w, o = U[i]
    k = 2 if doubled else 1
    return {"input": a * k, "cacheRead": r * k, "cacheCreation": w * k, "output": o + (8 if doubled else 0)}


@pytest.fixture
def routed(tmp_path, monkeypatch):
    """A one-turn session of three Bash calls and an answer, and its gateway trace."""
    recs = [user("go", 0)]
    for i, (tid, cmd) in enumerate((("t1", "a"), ("t2", "b"), ("t3", "c"))):
        recs += [asst([call(tid, "Bash", command=cmd)], 2 + 3 * i, f"msg_{i + 1}", usage(i)),
                 result(tid, f"out {cmd}", 3 + 3 * i)]
    recs += [asst([text("done")], 11, "msg_4", usage(3))]
    path = write_jsonl(tmp_path / "p" / f"{SID}.jsonl", recs)
    monkeypatch.setattr(drill, "find_session_files", lambda s: [path] if s == SID else [])
    monkeypatch.setattr(drill, "match_session_ids", lambda p: [SID] if SID.startswith(p) else [])

    rows = [
        row(1, "r1", mid="msg_1", u=wire_usage(0)),
        # a row from before messageId, written by the build that summed two stream events
        row(4, "r2", tools="t2", u=wire_usage(1, doubled=True), v=1),
        row(7, "r3", mid="msg_3", system="s2", tools="t2", account="b", u=wire_usage(2), ttfb=4000),
        row(7, "r4", cls="auxiliary"),
        row(8, "r5", cls="subagent"),
        row(8, "r6", session="dddddddd-0000-4000-8000-000000000001"),
        row(10, "r7", mid="msg_4", system="s2", tools="t2", account="b", u=wire_usage(3)),
    ]
    trace = Path(env("GATEWAY_DATA_DIR")) / "trace"
    trace.mkdir(parents=True)
    (trace / "2026-09-22.ndjson").write_text("".join(json.dumps(r) + "\n" for r in rows))
    blob = Path(env("GATEWAY_DATA_DIR")) / "blobs" / "s1"[:2] / "s1.json"
    blob.parent.mkdir(parents=True)
    blob.write_text('[{"type":"text","text":"You are Claude Code"}]')
    return path


def test_a_session_on_the_gateway_says_so_and_counts_what_no_transcript_holds(routed):
    out = format_session(load_session(SID))
    assert ("wire: 6 calls through the gateway, 4 matched to this transcript's messages · 1 subagent · 1 side"
            " · first byte median <1s, slowest 4s · accounts a ×4, b ×2") in out
    assert "a step opened whole shows its call and what the model was given" in out


def test_steps_carry_what_changed_before_their_call(routed):
    out = format_session(load_session(SID))
    lines = out.splitlines()
    step = {n: next(ln for ln in lines if ln.startswith(f"#{n} ")) for n in (1, 2, 3)}
    assert "[" not in step[1]  # the first call: nothing before it to differ from
    assert step[2].endswith("[toolsΔ]")
    assert step[3].endswith("[miss] [sysΔ] [acct]")
    assert ("cache misses #3 · system prompt changed #3 · tool list changed #2 · account changed #3") in out
    assert "[sysΔ] its system prompt differs from the call before (gateway)" in out


def test_an_opened_step_shows_its_call_and_where_the_prompt_it_was_given_is(routed):
    out = open_steps(SID, "1-2")
    assert "wire: row r1 · first byte <1s · 2s · a (rotated) · 20 tools · 4.0 KB sent · joined by message id" in out
    assert "given: system prompt " in out and "/blobs/s1/s1.json" in out
    # the old row, halved, still finds its message — by its counts
    assert "wire: row r2 · " in out and "joined by token counts · changed since the call before: tool list" in out


def test_a_v1_streamed_row_is_halved_before_it_is_matched():
    calls = [wire._call(row(4, "r2", u=wire_usage(1, doubled=True), v=1))]
    assert calls[0].usage == wire_usage(1) | {"output": 28}
    assert wire.join(calls, [("msg_2", usage(1))])["msg_2"].by == "counts"
    # a v2 row is taken as written: doubled numbers would not match
    assert wire.join([wire._call(row(4, "r2", u=wire_usage(1, doubled=True)))], [("msg_2", usage(1))]) == {}


def test_off_the_gateway_the_drill_is_unchanged(indexed, monkeypatch):
    from conftest import UUID
    con, jsonl = indexed
    monkeypatch.setattr(drill, "find_session_files", lambda s: [jsonl])
    out = format_session(load_session(UUID))
    assert not any(ln.startswith("wire:") for ln in out.splitlines())
    assert not any(f in out for f in ("[sysΔ]", "[toolsΔ]", "[acct]", "changed #"))


def test_account_pinned_calls_are_model_calls_too():
    assert wire._model_call("/tc-acct/foo/v1/messages?beta=true")
    assert wire._model_call("/v1/messages")
    assert not wire._model_call("/v1/messages/count_tokens")
