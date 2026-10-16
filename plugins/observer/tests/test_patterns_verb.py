"""`ak observer patterns` — curate.patterns from the shell.

A throwaway store: `zvec lock` in three rows of `ak` across two sessions and two rows of
`.ak` across two more (one project to memory), `lone` in two rows of one session. The verb
must show the one cluster across both labels, honour its thresholds, and end on the command
that turns a cluster into memory."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN / "src"))
from _brand import cmd, env_name  # noqa: E402
from observer.verbs import command
import store_common as sc  # noqa: E402
from observer import cli  # noqa: E402

# The project-label pair is history, not brand: the rows the vault wrote say `.ak`, the rows the
# code repo wrote say `ak`, and capture_common.PROJECT_ALIASES joins those two words whatever the kit is called.
REPO_LABEL = "brain"  # brand: historical
VAULT_LABEL = ".brain"  # brand: historical

ROWS = [  # (memory session, project, title, anchors)
    ("m1", REPO_LABEL, "readers hold the zvec LOCK", ["zvec LOCK"]),
    ("m1", REPO_LABEL, "embed waits on the zvec LOCK", ["zvec LOCK"]),
    ("m2", REPO_LABEL, "search died on the zvec LOCK", ["zvec LOCK"]),
    ("m3", VAULT_LABEL, "store embed blocked by the zvec LOCK", ["zvec LOCK"]),
    ("m4", VAULT_LABEL, "zvec LOCK held by a dead reader", ["zvec LOCK"]),
    ("m5", REPO_LABEL, "lone one", ["lone"]),
    ("m5", REPO_LABEL, "lone two", ["lone"]),
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    d = tmp_path / "store"
    d.mkdir()
    conn = sqlite3.connect(d / "brain.db")
    conn.executescript(sc.DDL)
    for col, kind in sc.OUR_COLUMNS["observations"]:
        conn.execute(f"ALTER TABLE observations ADD COLUMN {col} {kind}")
    for i, mid in enumerate(sorted({r[0] for r in ROWS}), 1):
        conn.execute("INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source, "
                     "started_at, started_at_epoch) VALUES(?,?,?,?,?,?)",
                     (f"c0ffee0{i}-0000-4000-8000-000000000000", mid, REPO_LABEL, "claude", "2026-09-01T00:00:00Z", i))
    for i, (mid, project, title, anchors) in enumerate(ROWS, 1):
        conn.execute("INSERT INTO observations(id, memory_session_id, project, type, title, narrative, facts, concepts, "
                     "files_read, files_modified, created_at, created_at_epoch, anchors) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (i, mid, project, "bugfix", title, "", "[]", "[]", "[]", "[]", "2026-09-01T00:00:00Z",
                      1000 + i, json.dumps(anchors)))
    conn.commit()
    conn.close()
    monkeypatch.setenv(env_name("PATH"), str(vault))
    monkeypatch.setenv(env_name("STORE"), str(d))
    monkeypatch.setattr(cli.ui, "_MODE", "plain")  # --json is sticky for the process; each test is its own call


def run(*args):
    return CliRunner().invoke(cli.observer, ["patterns", *args])


def test_one_cluster_across_the_project_and_its_alias(store):
    r = run("--project", REPO_LABEL)
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    rows = [l.split("\t") for l in lines if "\t" in l][1:]  # past the header
    assert [(a, n, s) for a, n, s, *_ in rows] == [("zvec lock", "5", "4")], r.output
    assert lines[-1].startswith(f"hint: {cmd('memory harvest')}"), r.output


def test_json_is_the_same_clusters_with_every_id(store):
    r = run("--project", REPO_LABEL, "--json")
    assert r.exit_code == 0, r.output
    got = json.loads(r.output)
    assert len(got) == 1
    c = got[0]
    assert c["anchor"] == "zvec lock" and c["rows"] == 5 and c["sessions"] == 4
    assert sorted(c["ids"]) == [1, 2, 3, 4, 5]
    assert len(c["session_ids"]) == 4 and c["title"]


def test_thresholds_filter_clusters(store):
    assert json.loads(run("--project", REPO_LABEL, "--min-sessions", "5", "--json").output) == []
    assert json.loads(run("--project", REPO_LABEL, "--min-rows", "6", "--json").output) == []
    assert json.loads(run("--project", "nothing-here", "--json").output) == []


def test_no_cluster_is_exit_1_with_a_hint(store):
    r = run("--project", REPO_LABEL, "--min-rows", "9")
    assert r.exit_code == 1
    assert r.output.splitlines()[-1].startswith(f"hint: {command('patterns')}"), r.output


def test_min_sessions_below_two_is_a_usage_error(store):
    assert run("--min-sessions", "1").exit_code == 2  # curate's floor: a pattern spans sessions
