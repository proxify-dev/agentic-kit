"""The three recall layers from the shell: `ak observer search → timeline → get`.

Each verb's plain output ends on the next command to run."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN / "src"))
from _brand import env_name  # noqa: E402
from observer.verbs import command
import store_common as sc  # noqa: E402
from observer import cli, obs_index  # noqa: E402

TITLES = ["gateway switch landed", "proxy account rotation", "gateway timeout on the proxy",
          "wikilinks resolver rung", "gateway metrics dashboard"]


@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / "store"
    d.mkdir()
    conn = sqlite3.connect(d / "brain.db")
    conn.executescript(sc.DDL)
    conn.execute("INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source, "
                 "started_at, started_at_epoch) VALUES(?,?,?,?,?,?)",
                 ("9789f0fc-0000-4000-8000-000000000001", "m1", "demo", "claude", "2026-01-01T00:00:00Z", 1))
    for i, title in enumerate(TITLES, 1):
        conn.execute("INSERT INTO observations(id, memory_session_id, project, type, title, narrative, facts, "
                     "concepts, created_at, created_at_epoch) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (i, "m1", "demo", "decision", title, f"why {i}", json.dumps([f"fact {i}"]), json.dumps([]),
                      f"2026-01-0{i}T00:00:00Z", 1000 + i))
    conn.commit()
    conn.close()
    monkeypatch.setenv(env_name("STORE"), str(d))
    monkeypatch.setenv(env_name("OBS_SEARCH_MODE"), "union")
    monkeypatch.setattr(obs_index, "BACKEND", obs_index.SqliteFtsBackend())


def run(*args):
    return CliRunner().invoke(cli.observer, list(args))


def test_search_rows_carry_the_session_and_point_at_the_next_layer(store):
    r = run("search", "gateway", "--mode", "union")
    assert r.exit_code == 0, r.output
    assert "9789f0fc" in r.output
    assert f"hint: {command('timeline')}" in r.output


def test_timeline_marks_the_anchor_among_its_neighbours(store):
    r = run("timeline", "3", "--before", "1", "--after", "1")
    assert r.exit_code == 0, r.output
    rows = [l for l in r.output.splitlines() if "\t" in l][1:]  # past the header
    assert [l.split("\t")[1] for l in rows] == ["2", "3", "4"]
    assert rows[1].startswith("▶")
    assert f"hint: {command('get')}" in r.output


def test_get_reads_ids_in_any_spelling_and_says_which_are_missing(store):
    r = run("get", "2", "memdb:3,4")
    assert r.exit_code == 0, r.output
    for i in (2, 3, 4):
        assert f"== memdb:{i} ==" in r.output and TITLES[i - 1] in r.output  # a panel, plain
    assert "fact 3" in r.output
    missing = run("get", "999")
    assert missing.exit_code == 1 and "999" in missing.output


def test_json_is_the_same_data(store):
    got = json.loads(run("get", "1", "--json").output)
    assert got[0]["id"] == "1" and "gateway switch landed" in got[0]["content"]
    tl = json.loads(run("timeline", "1", "--json").output)
    assert tl["anchor"] == "1" and tl["rows"][0]["is_anchor"]


def test_brain_search_gives_ak_search_the_rows_search_lists(store):
    """`ak search` reads every mounted plugin's `brain_search(words, limit)`: here the store's rows for the
    words, as {id, title, when, where} — the id is what `ak observer get` takes."""
    rows = cli.brain_search("gateway", 2)
    assert len(rows) == 2 and all(set(r) == {"id", "title", "when", "where"} for r in rows)
    assert all("gateway" in r["title"] and r["where"] == "demo" and r["when"].startswith("2026-01-0") for r in rows)
    assert run("get", rows[0]["id"]).exit_code == 0
    assert cli.brain_search("nothing-like-this", 5) == []


def test_the_verbs_that_write_carry_the_mark_inside_ak():
    """Mounted in `ak` (ak importable), fold, forget, mute, unmute and the store's writers are marked ✎; the readers are
    not. This venv carries no ak, so the probe puts ak's source on the path, as `ak` does."""
    import os
    import subprocess
    probe = ("from observer import cli; marked = lambda g: sorted(n for n, c in g.commands.items() if getattr(c, 'writes', False)); "
             "print(marked(cli.observer), marked(cli.observer.commands['store']))")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(PLUGIN.parent / "ak" / "src"), str(PLUGIN / "src")])}
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "['fold', 'forget', 'mute', 'unmute'] ['backfill', 'embed', 'init', 'sync']"
