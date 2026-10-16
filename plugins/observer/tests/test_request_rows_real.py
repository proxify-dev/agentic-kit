"""The observer against this machine's real 2.1.300+ transcripts — run on demand.

    AK_TEST_REAL_TRANSCRIPTS=1 uv run pytest -q tests/test_request_rows_real.py

The saved model calls must change nothing the observer reads: each transcript is read as it is, and again
with its saved-call rows taken out, and the digest and the tool count must come out the same. Before the
fix, 118 of 122 digests differed (2026-10-15). Skipped without the switch, or with no such transcript.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
import capture_common as cc  # noqa: E402
import transcript_rows as tr  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("AK_TEST_REAL_TRANSCRIPTS") != "1",
                                reason="reads this machine's transcripts: AK_TEST_REAL_TRANSCRIPTS=1")


def _summarizer():
    spec = importlib.util.spec_from_file_location("summarize_session", PLUGIN / "scripts" / "summarize-session.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _recorded(n=150):
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    files = sorted(home.glob("*/*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:n]
    return [f for f in files if b'"type":"api-request"' in f.read_bytes()]


def test_saved_calls_change_nothing_the_observer_reads(tmp_path):
    files = _recorded()
    if not files:
        pytest.skip("no transcripts from Claude Code 2.1.300 or later")
    summarizer, differ = _summarizer(), []
    for f in files:
        chat_only = tmp_path / f.name
        with f.open(encoding="utf-8", errors="replace") as src, chat_only.open("w", encoding="utf-8") as dst:
            dst.writelines(ln for ln in src if tr.row_type(ln) not in tr.RECORD_TYPES)
        if (summarizer.transcript_digest(str(f)) != summarizer.transcript_digest(str(chat_only))
                or cc.transcript_activity(str(f)) != cc.transcript_activity(str(chat_only))):
            differ.append(f.name)
    assert not differ, f"{len(differ)} of {len(files)} sessions read differently: {differ[:5]}"
