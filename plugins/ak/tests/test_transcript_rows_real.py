"""transcript_rows and `ak plugin` against this machine's real transcripts — run on demand.

    AK_TEST_REAL_TRANSCRIPTS=1 uv run pytest -q tests/test_transcript_rows_real.py

The synthetic tests prove the rules; these prove the rules still describe what Claude Code writes. Run
them after a Claude Code update: a failure here is a change in its transcripts that a reader must
follow (a FEATURES entry, a KNOWN_ROW_TYPES name, a branch in a reader). Skipped without the switch,
and when the machine has no transcripts of the kind a test needs.
"""
import json
import os

import pytest

from ak import transcript_rows as tr
from ak._brand import claude_home
from ak.plugin_inspect import heard

pytestmark = pytest.mark.skipif(os.environ.get("AK_TEST_REAL_TRANSCRIPTS") != "1",
                                reason="reads this machine's transcripts: AK_TEST_REAL_TRANSCRIPTS=1")


def _newest(n, pattern="*/*.jsonl"):
    files = sorted((claude_home() / "projects").glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        pytest.skip("no transcripts on this machine")
    return files[:n]


def _recorded(files):
    """The files whose every chat row was written by a Claude Code that saves its calls."""
    out = []
    for f in files:
        versions = {json.loads(ln).get("version") for ln in f.open(encoding="utf-8", errors="replace")
                    if tr.row_type(ln) in tr.CHAT_TYPES}
        if versions and all(tr.has("request_record", v) for v in versions):
            out.append(f)
    return out


def test_the_cheap_type_read_agrees_with_a_full_parse_on_every_line():
    wrong = []
    for f in _newest(200, "**/*.jsonl"):
        for ln in f.open(encoding="utf-8", errors="replace"):
            if not ln.strip():
                continue
            try:
                want = json.loads(ln).get("type") or ""
            except ValueError:
                continue
            if tr.row_type(ln) != want:
                wrong.append((f.name, want, ln[:80]))
    assert not wrong, wrong[:5]


def test_every_row_type_claude_code_writes_is_known():
    found = {}
    for f in _newest(50):
        found.update(tr.unknown_types(f))
    assert not found, f"new row types (type: version): {found} — add them to KNOWN_ROW_TYPES, and teach a reader"


def test_most_hook_text_is_tied_to_the_saved_call_that_carried_it():
    """Measured 2026-10-15 over 300 sessions: 1,002 of 1,034 texts (96.9%) found in the call that carried them.
    The 32 left are in no saved body at all (a body Claude Code did not save, or text it never sent)."""
    files = _recorded(_newest(300))
    if not files:
        pytest.skip("no transcripts from Claude Code 2.1.300 or later")
    rows = [h for f in files for h in heard.read(f) if h.kind == "context"]
    tied = sum(h.source == "record" for h in rows)
    assert rows and tied / len(rows) >= 0.95, f"{tied} of {len(rows)} context texts tied to a call"
