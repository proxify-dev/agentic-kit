"""Pass 3, slice 6: a correction the person typed reaches the observer.

hooks/prompt-flag.py runs on UserPromptSubmit with hook-style stdin, runs its
CORRECTION pattern over the prompt, and appends one line per hit to
<kit>/cache/sessions/<id>.flags.jsonl. (Until 2026-09-25 the pattern was a
behavior record under loadout/behaviors/; loadout was archived and the pattern is the
hook's own.) The observer's build_prompt lists those lines as FLAGGED PROMPTS. Nothing is denied or
injected: the hook's stdout is empty on a hit and on a miss alike.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
from _brand import env_name  # noqa: E402
HOOK = PLUGIN / "hooks" / "prompt-flag.py"
SUMMARIZER = PLUGIN / "scripts" / "summarize-session.py"
UUID = "cccccccc-dddd-eeee-ffff-000000000000"

@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    (v / "cache" / "sessions").mkdir(parents=True)
    (v / "vault-manifest.json").write_text('{"name":"t","version":"0.0.0","note_types":{}}')
    monkeypatch.setenv(env_name("PATH"), str(v))
    monkeypatch.setenv(env_name("STORE"), str(tmp_path / "store"))
    monkeypatch.setenv("CLAUDE_MEM_DB", str(tmp_path / "absent.db"))
    return v


def _fire(vault: Path, prompt: str, session: str = UUID) -> subprocess.CompletedProcess:
    payload = json.dumps({"session_id": session, "cwd": str(vault), "prompt": prompt, "hook_event_name": "UserPromptSubmit"})
    env = {**os.environ, env_name("PATH"): str(vault)}
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, env=env, timeout=30)


def _flags(vault: Path, session: str = UUID) -> list:
    p = vault / "cache" / "sessions" / f"{session}.flags.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_a_correction_is_flagged_and_nothing_is_said(vault):
    r = _fire(vault, "No — that's wrong, the port is 4578, not 4577")
    assert r.returncode == 0 and r.stdout.strip() == "" and r.stderr.strip() == ""
    flags = _flags(vault)
    assert len(flags) == 1
    assert flags[0]["flag"] == "correction"
    assert flags[0]["prompt"].startswith("No — that's wrong, the port is 4578")
    assert flags[0]["at"]


def test_case_insensitive_by_definition(vault):
    _fire(vault, "NO, NOT WHAT I MEANT")
    assert len(_flags(vault)) == 1


def test_a_plain_prompt_leaves_no_trace(vault):
    r = _fire(vault, "please continue with the next slice")
    assert r.returncode == 0 and r.stdout.strip() == ""
    assert _flags(vault) == []
    assert not (vault / "cache" / "sessions" / f"{UUID}.flags.jsonl").exists()


def test_one_prompt_is_one_flag(vault):
    _fire(vault, "no, wrong")
    assert [f["flag"] for f in _flags(vault)] == ["correction"]


def test_a_leftover_loadout_folder_changes_nothing(vault):
    (vault / "loadout" / "behaviors").mkdir(parents=True)
    (vault / "loadout" / "behaviors" / "x.md").write_text("---\nstate: armed\ndetect: prompt ^please\n---\n")
    _fire(vault, "please continue")
    assert _flags(vault) == []


def test_the_observer_lists_the_flags_above_the_transcript(vault, tmp_path):
    _fire(vault, "no — that's wrong, the port is 4578")
    transcript = tmp_path / f"{UUID}.jsonl"
    lines = [
        {"type": "user", "cwd": str(vault), "sessionId": UUID, "timestamp": "2026-09-21T08:00:00.000Z",
         "message": {"role": "user", "content": "the port is 4577 right?"}},
        {"type": "assistant", "cwd": str(vault), "sessionId": UUID, "timestamp": "2026-09-21T08:00:05.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "yes, 4577"}]}},
        {"type": "user", "cwd": str(vault), "sessionId": UUID, "timestamp": "2026-09-21T08:01:00.000Z",
         "message": {"role": "user", "content": "no — that's wrong, the port is 4578"}},
    ]
    transcript.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    env = {**os.environ, env_name("PATH"): str(vault), env_name("STORE"): str(tmp_path / "store"),
           "CLAUDE_MEM_DB": str(tmp_path / "absent.db")}
    r = subprocess.run([sys.executable, str(SUMMARIZER), "--session", UUID, "--transcript", str(transcript),
                        "--print-prompt"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    prompt = r.stdout
    assert "FLAGGED PROMPTS" in prompt
    assert '- [correction] "no — that\'s wrong, the port is 4578"' in prompt
    assert prompt.index("FLAGGED PROMPTS") < prompt.index("CONVERSATION:")
    assert "A FLAGGED PROMPT is a correction until the conversation says otherwise" in prompt


def test_brain_now_dates_the_observers_rows(vault, tmp_path, monkeypatch):
    sys.path.insert(0, str(PLUGIN / "hooks"))
    import store_common as sc
    monkeypatch.setenv(env_name("NOW"), "2026-09-24")
    from datetime import datetime
    assert datetime.fromtimestamp(sc.brain_now()).strftime("%Y-%m-%d") == "2026-09-24"
    monkeypatch.setenv(env_name("NOW"), "1790000000000")
    assert sc.brain_now() == 1790000000.0
    monkeypatch.setenv(env_name("NOW"), "day three")
    assert abs(sc.brain_now() - __import__("time").time()) < 2
