"""The observer's prompt never said what day it was: `until` never fired.
`build_prompt` now states
TODAY, so "until the switch lands Friday" has something to resolve against.

TODAY is the session's own last-seen activity, not the wall clock the summarizer
happens to run on — a `ak observer store backfill` days later must still
resolve a relative day against the conversation, not against itself.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
from _brand import env_name  # noqa: E402
SUMMARIZER = PLUGIN / "scripts" / "summarize-session.py"
UUID = "12121212-3434-4565-8787-909090909090"


def _env(tmp_path):
    vault = tmp_path / "vault"
    (vault / "cache" / "sessions").mkdir(parents=True)
    (vault / "projects").mkdir()
    store_dir = tmp_path / "store"
    return {**os.environ, env_name("PATH"): str(vault), env_name("STORE"): str(store_dir),
            "CLAUDE_MEM_DB": str(tmp_path / "absent.db")}


PROMPT = "keep using the spare port until the switch lands Friday"


def _write_transcript(tmp_path, day: str, saved_body: bool = False) -> Path:
    """A two-turn session, both timestamped on `day` (YYYY-MM-DD) — the
    conversation's own day, whatever day the test (or a backfill) actually runs.
    saved_body: Claude Code 2.1.300+ also saves the prompt as it was sent, CLAUDE.md block around it."""
    cwd = tmp_path / "somewhere"
    cwd.mkdir()
    transcript = tmp_path / f"{UUID}.jsonl"
    lines = [
        {"type": "user", "cwd": str(cwd), "sessionId": UUID, "timestamp": f"{day}T08:00:00.000Z",
         "message": {"role": "user", "content": PROMPT}},
        {"type": "assistant", "cwd": str(cwd), "sessionId": UUID, "timestamp": f"{day}T08:05:00.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "ok, noted"}]}},
    ]
    if saved_body:
        lines.insert(1, {"type": "api-request-blob", "sessionId": UUID, "hash": "ab" * 32, "message": {
            "role": "user", "content": [{"type": "text", "text": "<system-reminder>Codebase and user instructions "
                                                                 "are shown below.</system-reminder>"},
                                        {"type": "text", "text": PROMPT}]}})
    transcript.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return transcript


def _print_prompt(tmp_path, transcript) -> str:
    r = subprocess.run(
        [sys.executable, str(SUMMARIZER), "--session", UUID, "--transcript", str(transcript),
         "--print-prompt"],
        capture_output=True, text=True, env=_env(tmp_path), timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_prompt_states_todays_date_from_the_sessions_own_activity(tmp_path):
    transcript = _write_transcript(tmp_path, "2026-09-21")
    prompt = _print_prompt(tmp_path, transcript)
    # The session's own last timestamp, not whatever day the test happens to run on.
    assert "TODAY: 2026-09-21" in prompt
    assert prompt.index("TODAY: 2026-09-21") < prompt.index("PROJECT:")


def test_the_observer_reads_the_prompt_once_when_claude_code_saved_what_it_sent(tmp_path):
    prompt = _print_prompt(tmp_path, _write_transcript(tmp_path, "2026-10-15", saved_body=True))
    assert prompt.count(PROMPT) == 1
    assert "Codebase and user instructions" not in prompt


def test_a_backfill_run_long_after_still_resolves_against_the_conversations_day(tmp_path):
    """The literal bug: a backfill invoked today must not stamp TODAY as today's
    wall-clock date when the transcript itself says when the session happened."""
    transcript = _write_transcript(tmp_path, "2026-01-05")
    prompt = _print_prompt(tmp_path, transcript)
    assert "TODAY: 2026-01-05" in prompt
    real_today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert real_today not in re.search(r"TODAY: (\S+)", prompt).group(1)


def test_the_until_rule_points_at_today(tmp_path):
    transcript = _write_transcript(tmp_path, "2026-09-21")
    prompt = _print_prompt(tmp_path, transcript)
    assert "TODAY" in prompt.split('"until"')[1].split("\n")[0] \
        or "TODAY" in prompt.split('"until" is a date only')[1][:400]


def test_falls_back_to_the_wall_clock_when_the_session_carries_no_timestamp(tmp_path, monkeypatch):
    """A scratch-driven run (no --transcript) with a session record that has no
    'started'/'ended' at all: TODAY must still be a real date, not blank or a
    crash — the wall clock is the only thing left to ask."""
    sys.path.insert(0, str(PLUGIN / "hooks"))
    import importlib
    capture_common = importlib.import_module("capture_common")
    env = _env(tmp_path)
    monkeypatch.setenv(env_name("PATH"), env[env_name("PATH")])
    capture_common.append_event(UUID, {"kind": "session", "session_id": UUID, "cwd": str(tmp_path),
                                       "project": "unbound"})
    r = subprocess.run([sys.executable, str(SUMMARIZER), "--session", UUID, "--print-prompt"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    m = re.search(r"TODAY: (\d{4}-\d{2}-\d{2})", r.stdout)
    assert m, r.stdout[:200]


# ── a live run was told the day the session STARTED ─────────────
def _scratch(tmp_path, env, started: str):
    sessions = Path(env[env_name("PATH")]) / "cache" / "sessions"
    (sessions / f"{UUID}.jsonl").write_text(json.dumps(
        {"kind": "session", "session_id": UUID, "cwd": str(tmp_path), "project": "unbound", "started": started}) + "\n")


def _multi_day_transcript(tmp_path) -> Path:
    t = tmp_path / f"{UUID}.jsonl"
    t.write_text("\n".join(json.dumps(l) for l in [
        {"type": "user", "cwd": str(tmp_path), "sessionId": UUID, "timestamp": "2026-09-24T06:00:00.000Z",
         "message": {"role": "user", "content": "hi"}},
        {"type": "user", "cwd": str(tmp_path), "sessionId": UUID, "timestamp": "2026-09-27T21:30:00.000Z",
         "message": {"role": "user", "content": "keep the spare port until tomorrow"}},
    ]) + "\n")
    return t


def _today_of(tmp_path, env, *args) -> str:
    r = subprocess.run([sys.executable, str(SUMMARIZER), "--session", UUID, *args, "--print-prompt"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    return re.search(r"TODAY: (\S+)", r.stdout).group(1)


def test_a_live_session_that_ran_for_days_is_told_the_day_it_ended_in_local_time(tmp_path):
    # the SessionEnd path: the scratch record holds only `started` (local); "until tomorrow" said on the last
    # day must resolve against that day — here 21:30Z, which is the 28th at +03:00
    env = {**_env(tmp_path), "TZ": "Europe/Istanbul"}
    _scratch(tmp_path, env, "2026-09-24T09:00:00")
    assert _today_of(tmp_path, env, "--transcript", str(_multi_day_transcript(tmp_path))) == "2026-09-28"


def test_a_backfill_takes_the_local_day_of_a_utc_timestamp(tmp_path):
    env, t = _env(tmp_path), str(_multi_day_transcript(tmp_path))
    assert _today_of(tmp_path, {**env, "TZ": "Europe/Istanbul"}, "--transcript", t) == "2026-09-28"
    assert _today_of(tmp_path, {**env, "TZ": "America/Los_Angeles"}, "--transcript", t) == "2026-09-27"


def test_a_live_run_with_no_transcript_is_told_the_wall_clocks_day_not_the_start(tmp_path):
    env = _env(tmp_path)
    _scratch(tmp_path, env, "2020-01-01T09:00:00")
    assert _today_of(tmp_path, env) == datetime.now().strftime("%Y-%m-%d")
