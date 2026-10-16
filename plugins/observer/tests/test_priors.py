"""Pass 3, slice 2: the observer sees what the store already holds, and
`supersedes` can name a row from an earlier session.

Three doors, all through the script as it actually runs:
  --print-prompt          the PRIOR OBSERVATIONS block lists the store's rows as obs:<id>
  --from-json --dry-run   what the model named is resolved and printed, nothing written
  --from-json             the column holds obs:<id>; a title becomes its id; the rest is kept
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
import store_common as sc  # noqa: E402

SUMMARIZER = PLUGIN / "scripts" / "summarize-session.py"
UUID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A vault, a store with three prior rows under the label the summarizer
    will compute for `cwd` (the basename of a directory that is no git root),
    and a transcript that touched one of the prior rows' files."""
    vault = tmp_path / "vault"
    (vault / "cache" / "sessions").mkdir(parents=True)
    (vault / "projects").mkdir()
    cwd = tmp_path / "demoproj"
    cwd.mkdir()
    store_dir = tmp_path / "store"
    monkeypatch.setenv(env_name("PATH"), str(vault))
    monkeypatch.setenv(env_name("STORE"), str(store_dir))
    monkeypatch.setenv("CLAUDE_MEM_DB", str(tmp_path / "absent.db"))

    conn = sc.connect()
    sc.ensure_schema(conn)
    with conn:
        conn.execute(
            "INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source, "
            "started_at, started_at_epoch, status) VALUES(?,?,?,?,?,?,'completed')",
            ("11111111-1111-4111-8111-111111111111", "prior-session", "demoproj", "brain",  # brand: historical (platform_source)
             "2026-09-18T09:00:00.000Z", 1789981200000))
        rows = [
            ("The feed listens on :4577", "worked", '["gateway/feed.ts"]', 1789981200000),
            ("editing the shared config lost a peer's work", "dead-end", '["app/x.ts"]', 1789981260000),
            ("Tokens refresh every hour", "worked", "[]", 1789981320000),
        ]
        for title, verdict, files, epoch in rows:
            conn.execute(
                "INSERT INTO observations(memory_session_id, project, type, title, subtitle, facts, narrative, "
                "concepts, files_read, files_modified, created_at, created_at_epoch, verdict) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("prior-session", "demoproj", "observation", title, "", "[]", "", "[]", "[]", files,
                 "2026-09-18T09:00:00.000Z", epoch, verdict))
    conn.close()

    transcript = tmp_path / f"{UUID}.jsonl"
    lines = [
        {"type": "user", "cwd": str(cwd), "sessionId": UUID, "timestamp": "2026-09-21T08:00:00.000Z",
         "message": {"role": "user", "content": "move the feed port"}},
        {"type": "assistant", "cwd": str(cwd), "sessionId": UUID, "timestamp": "2026-09-21T08:00:05.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Edit", "input": {"file_path": str(cwd / "gateway" / "feed.ts")}},
             {"type": "text", "text": "moved it to 4578"}]}},
    ]
    transcript.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    env = {**os.environ, env_name("PATH"): str(vault), env_name("STORE"): str(store_dir),
           "CLAUDE_MEM_DB": str(tmp_path / "absent.db")}
    return {"vault": vault, "store": store_dir, "transcript": transcript, "env": env, "tmp": tmp_path}


def _run(world, *extra):
    r = subprocess.run([sys.executable, str(SUMMARIZER), "--session", UUID,
                        "--transcript", str(world["transcript"]), *extra],
                       capture_output=True, text=True, env=world["env"], timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_prompt_lists_the_projects_prior_rows_as_obs_ids(world):
    prompt = _run(world, "--print-prompt")
    header = "PRIOR OBSERVATIONS (this project"
    assert header in prompt
    block = prompt.split(header)[1].split("TOOL ACTIVITY:")[0]
    assert "obs:1 [worked] The feed listens on :4577" in block
    assert "obs:2 [dead-end] editing the shared config" in block
    assert "obs:3 [worked] Tokens refresh every hour" in block
    # the rule names the form
    assert '"supersedes" is obs:<id> from the PRIOR OBSERVATIONS list' in prompt
    # and the block sits above the transcript, where the model reads context
    assert prompt.index(header) < prompt.index("CONVERSATION:")


def test_print_prompt_is_a_pure_read(world):
    before = sorted(p.name for p in world["store"].iterdir())
    db = world["store"] / "brain.db"
    mtime = db.stat().st_mtime_ns
    _run(world, "--print-prompt")
    assert sorted(p.name for p in world["store"].iterdir()) == before
    assert db.stat().st_mtime_ns == mtime


def _model_output(tmp: Path) -> Path:
    out = tmp / "model.json"
    out.write_text(json.dumps({
        "observations": [
            {"obs_type": "change", "verdict": "worked", "title": "The feed moved to :4578",
             "supersedes": "obs:1", "facts": ["feed is on 4578"], "narrative": "moved", "concepts": ["feed"],
             "anchors": [":4578"], "files_read": [], "files_modified": ["gateway/feed.ts"]},
            {"obs_type": "change", "verdict": "worked", "title": "Work on a private branch instead",
             "supersedes": "editing the shared config lost a peer's work", "facts": [], "narrative": "",
             "concepts": [], "anchors": [], "files_read": [], "files_modified": []},
            {"obs_type": "discovery", "verdict": "neutral", "title": "A same-batch pointer",
             "supersedes": "The feed moved to :4578", "facts": [], "narrative": "", "concepts": [],
             "anchors": [], "files_read": [], "files_modified": []},
            {"obs_type": "discovery", "verdict": "neutral", "title": "An id the model was never shown",
             "supersedes": "obs:999", "facts": [], "narrative": "", "concepts": [],
             "anchors": [], "files_read": [], "files_modified": []},
        ],
        "summary": {"request": "move the port", "investigated": "", "learned": "", "completed": "", "next_steps": None},
    }))
    return out


def test_dry_run_resolves_and_writes_nothing(world):
    out = _run(world, "--from-json", str(_model_output(world["tmp"])), "--dry-run")
    assert "The feed moved to :4578  supersedes obs:1" in out
    assert "Work on a private branch instead  supersedes obs:2" in out
    # a same-batch title is kept as written — the legacy form
    assert "A same-batch pointer  supersedes The feed moved to :4578" in out
    # an id it was never shown is not a pointer
    assert "An id the model was never shown\n" in out
    conn = sc.connect()
    assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 3
    conn.close()


def test_write_stores_resolved_pointers(world):
    _run(world, "--from-json", str(_model_output(world["tmp"])))
    conn = sc.connect()
    got = {r[0]: r[1] for r in conn.execute("SELECT title, supersedes FROM observations WHERE memory_session_id=?", (UUID,))}
    conn.close()
    assert got["The feed moved to :4578"] == "obs:1"
    assert got["Work on a private branch instead"] == "obs:2"
    assert got["A same-batch pointer"] == "The feed moved to :4578"
    assert got["An id the model was never shown"] is None
