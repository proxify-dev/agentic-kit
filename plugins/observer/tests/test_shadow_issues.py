"""The issues trial (hooks/shadow_issues.py, `observer shadow`): a second read of a session that follows
an issue from opened to fixed, written only to <kit>/db/shadow/issues/ — never the store.

Through the script as it runs: --print-prompt shows the trial's parts only with --shadow; --from-json
records what the model named; a later session sees the open issue and may resolve it; the report scores the
trial against a memory issue note.
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
A = "aaaaaaaa-1111-4111-8111-111111111111"
B = "bbbbbbbb-2222-4222-8222-222222222222"


@pytest.fixture
def world(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "cache" / "sessions").mkdir(parents=True)
    (vault / "projects").mkdir()
    cwd = tmp_path / "demoproj"
    cwd.mkdir()
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    env = {**os.environ, env_name("PATH"): str(vault), env_name("STORE"): str(store_dir),
           "CLAUDE_MEM_DB": str(tmp_path / "absent.db")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    conn = sc.connect()
    sc.ensure_schema(conn)
    conn.close()

    def transcript(uuid, said):
        p = tmp_path / f"{uuid}.jsonl"
        lines = [{"type": "user", "cwd": str(cwd), "sessionId": uuid, "timestamp": "2026-10-14T08:00:00.000Z",
                  "message": {"role": "user", "content": said}},
                 {"type": "assistant", "cwd": str(cwd), "sessionId": uuid, "timestamp": "2026-10-14T08:00:05.000Z",
                  "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}}]
        p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        return p

    return {"env": env, "tmp": tmp_path, "store": store_dir, "cwd": cwd, "transcript": transcript}


def _run(world, uuid, *extra):
    t = world["transcript"](uuid, "fix the images")
    r = subprocess.run([sys.executable, str(SUMMARIZER), "--session", uuid, "--transcript", str(t), *extra],
                       capture_output=True, text=True, env=world["env"], timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _answer(world, name, issues):
    p = world["tmp"] / f"{name}.json"
    p.write_text(json.dumps({"observations": [], "summary": {"request": "x"}, "issues": issues}))
    return str(p)


def _events(world):
    p = world["store"] / "shadow" / "issues" / "events.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_the_real_prompt_carries_no_part_of_the_trial(world):
    plain = _run(world, A, "--print-prompt")
    assert "OPEN ISSUES" not in plain and '"issues"' not in plain and "ISSUES (a trial" not in plain
    trial = _run(world, A, "--print-prompt", "--shadow")
    assert "OPEN ISSUES (this project" in trial and "(none yet)" in trial
    assert '"issues": {' in trial and "ISSUES (a trial" in trial
    import shadow_issues as si
    _, block = si.prompt_parts("demoproj")
    # the trial's three parts taken out, what is left is the real prompt, byte for byte
    assert trial.replace(si.OUTPUT, "").replace(si.RULES, "").replace(block, "") == plain


def test_a_trial_run_writes_its_folder_and_never_the_store(world):
    out = _run(world, A, "--shadow", "--from-json", _answer(world, "a", {
        "opened": [{"title": "Images load as blanks", "anchors": ["play.mjs"], "evidence": "image 1 is blank",
                    "fixed_here": False},
                   {"title": "Config never read", "anchors": [], "evidence": "fixed it", "fixed_here": True}],
        "resolved": [{"id": "iss-nothere", "evidence": "x"}], "progress": []}))
    assert "2 opened, 0 resolved" in out and "1 unknown id(s) dropped" in out
    ev = _events(world)
    assert [e["event"] for e in ev] == ["opened", "opened"]
    assert all(e["session"] == A and e["project"] == "demoproj" and e["id"].startswith("iss-") for e in ev)
    assert (world["store"] / "shadow" / "issues" / "runs" / f"{A}.json").is_file()
    conn = sc.connect(readonly=True)
    assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM session_summaries").fetchone()[0] == 0


def test_a_later_session_is_shown_the_open_issue_and_closes_it(world):
    _run(world, A, "--shadow", "--from-json", _answer(world, "a", {
        "opened": [{"title": "Images load as blanks", "anchors": ["play.mjs"], "evidence": "e", "fixed_here": False},
                   {"title": "Config never read", "anchors": [], "evidence": "e", "fixed_here": True}]}))
    still = _events(world)[0]["id"]
    prompt = _run(world, B, "--print-prompt", "--shadow")
    block = prompt.split("OPEN ISSUES (this project")[1].split("TOOL ACTIVITY:")[0]
    assert still in block and "Images load as blanks" in block
    assert "Config never read" not in block  # fixed in the session that found it: never open
    _run(world, B, "--shadow", "--from-json", _answer(world, "b", {
        "opened": [], "resolved": [{"id": still, "evidence": "frame 1 now plays"}], "progress": []}))
    import shadow_issues as si
    v = si.issues()[still]
    assert v["opened_in"] == A and v["resolved_in"] == B and v["resolved_evidence"] == "frame 1 now plays"
    assert si.open_issues("demoproj") == []


def test_the_switch_is_a_file_in_the_kits_db(world):
    import shadow_issues as si
    assert not si.enabled()
    si.set_on(True)
    assert si.enabled() and (world["store"] / "shadow" / "issues" / "ON").is_file()
    si.set_on(False)
    assert not si.enabled()


def test_the_report_scores_the_trial_against_a_memory_issue_note(world):
    ws = world["cwd"]
    note = ws / ".agents" / "memory" / "issues" / "images-load-as-blanks.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntype: issue\nstatus: done\ncites:\n  - play.mjs\ncreated_by: aaaaaaaa\n---\n\n"
                    "# Images load as blanks in a gallery\n\n## Log\n\n- 2026-10-14 opened (session aaaaaaaa)\n"
                    "- 2026-10-14 closed (done) — evidence: plays (session bbbbbbbb)\n")
    (world["store"] / "memory-projects.json").write_text(json.dumps({"on": {str(ws): {"since": "2026-10-14"}}}))
    _run(world, A, "--shadow", "--from-json", _answer(world, "a", {
        "opened": [{"title": "Images load as blanks", "anchors": ["play.mjs"], "evidence": "e", "fixed_here": False}]}))
    sid = _events(world)[0]["id"]
    _run(world, B, "--shadow", "--from-json", _answer(world, "b", {"resolved": [{"id": sid, "evidence": "plays"}]}))
    r = subprocess.run([str(PLUGIN / "bin" / "observer"), "shadow", "report", "--json"],
                       capture_output=True, text=True, env=world["env"], timeout=120)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["counts"]["closed_later"] == 1 and data["counts"]["open"] == 0
    g = data["against_memory"][0]
    assert g["shadow"] == sid and g["opened_caught"] is True and g["closed_caught"] is True


def test_a_cites_written_as_one_value_is_one_path_not_its_letters():
    from observer.shadow import _cites, match
    assert _cites('src/service/play.mjs:use') == ["src/service/play.mjs"]
    assert _cites(["src/a.mjs:f", "b.mjs"]) == ["src/a.mjs", "b.mjs"]
    note = {"project": "p", "title": "A video clip cannot go into an image gallery", "opened_in": "aaaaaaaa",
            "cites": _cites('"src/service/play.mjs:use"')}
    other = {"project": "p", "title": "Reports page has no search box", "opened_in": "aaaaaaaa-1",
             "anchors": ["src/views/Reports.tsx"]}
    same = {"project": "p", "title": "Video placed in a gallery shows as a blank image", "opened_in": "aaaaaaaa-2",
            "anchors": ["src/service/play.mjs"]}
    assert match(note, [other]) is None
    assert match(note, [other, same]) is same
