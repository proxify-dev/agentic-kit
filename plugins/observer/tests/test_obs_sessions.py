"""observer: which session a row came from. Every row a reader gets carries the
Claude Code session uuid (sdk_sessions.content_session_id — what the tracer takes), a
row whose session was never recorded says '' rather than guessing, and
`search --by-session` answers "which sessions was X in" in one call."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN / "src"))
from _brand import env_name  # noqa: E402
import store_common as sc  # noqa: E402
from observer.obs_index import SqliteFtsBackend, group_by_session  # noqa: E402

VAULT_LABEL = ".brain"  # brand: historical

# memory id → (session uuid, started, what it was asked)
SESSIONS = {
    "m-a": ("uuid-a", "2026-09-11T10:00:00Z", "Please list options for isolating\n a build in a sandbox"),
    "m-b": ("uuid-b", "2026-07-28T09:00:00Z", "Running tests in a sandbox"),
}
# id, memory id, title — m-x has no sdk_sessions row: the claude-mem imports' shape
ROWS = [
    (1, "m-b", "sandbox sandbox seatbelt profile validated"),
    (2, "m-a", "sandbox directory added"),
    (3, "m-a", "sandbox network allowlist"),
    (4, "m-a", "sandbox wrappers compared"),
    (5, "m-x", "stale sandbox directories"),
    (6, "m-a", "gateway metrics dashboard"),
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / "store"
    d.mkdir()
    conn = sqlite3.connect(d / "brain.db")
    conn.executescript(sc.DDL)
    for mid, (uuid, started, asked) in SESSIONS.items():
        conn.execute("INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source, "
                     "user_prompt, started_at, started_at_epoch) VALUES(?,?,?,?,?,?,?)",
                     (uuid, mid, VAULT_LABEL, "claude", asked, started, 1))
    for i, (rid, mid, title) in enumerate(ROWS):
        conn.execute("INSERT INTO observations(id, memory_session_id, project, type, title, narrative, facts, "
                     "concepts, prompt_number, created_at, created_at_epoch) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, mid, VAULT_LABEL, "discovery", title, "", "[]", "[]", 2,
                      f"2026-09-0{i + 1}T00:00:00Z", 1000 + i))
    conn.commit()
    conn.close()
    monkeypatch.setenv(env_name("STORE"), str(d))
    monkeypatch.delenv(env_name("OBS_SEARCH_MODE"), raising=False)
    return SqliteFtsBackend()


def test_every_search_row_names_its_session(store):
    got = {r["id"]: r["session"] for r in store.search(query="sandbox", mode="union")}
    assert got == {"1": "uuid-b", "2": "uuid-a", "3": "uuid-a", "4": "uuid-a", "5": ""}


def test_a_note_says_which_session_and_what_it_was_asked(store):
    linked, unlinked = store.get(["2", "5"])
    assert linked["session"] == "uuid-a"
    assert "- session: uuid-a  ·  prompt 2" in linked["content"]
    assert "- asked: Please list options for isolating a build in a sandbox" in linked["content"]  # one line
    assert unlinked["session"] == "" and "- session:" not in unlinked["content"]


def test_timeline_rows_name_their_session(store):
    rows = store.timeline(anchor="3", before=5, after=5)["rows"]
    assert {r["id"]: r["session"] for r in rows}["1"] == "uuid-b"
    assert all("session" in r for r in rows)


def test_sessions_groups_ranked_rows_and_counts_the_unlinked(store):
    out = store.sessions(query="sandbox", mode="union")
    assert out["scanned"] == 5 and out["unlinked"] == 1
    by = {g["session"]: g for g in out["sessions"]}
    assert set(by) == {"uuid-a", "uuid-b"}
    assert by["uuid-a"]["hits"] == 3 and by["uuid-b"]["hits"] == 1
    assert by["uuid-a"]["started"] == "2026-09-11"
    assert by["uuid-a"]["asked"].startswith("Please list options")
    assert len(by["uuid-a"]["top"]) == SqliteFtsBackend.GROUP_KEEP
    assert out["sessions"][0]["session"] == "uuid-a"  # three hits outvote one top hit


def test_a_session_entry_says_what_the_session_did(store):
    """`did` is the latest summary that says anything: completed, else learned — so a
    reader knows what each session was about without opening its dialogue."""
    conn = sqlite3.connect(obs_index_store())
    for epoch, completed, learned in ((1, "first pass: listed options", ""),
                                      (2, "", "option B is the pick"),
                                      (3, "", "")):
        conn.execute("INSERT INTO session_summaries(memory_session_id, project, request, completed, learned, "
                     "created_at, created_at_epoch) VALUES('m-a', ?, 'r', ?, ?, '2026-09-11', ?)",
                     (VAULT_LABEL, completed, learned, epoch))
    conn.commit()
    conn.close()
    by = {g["session"]: g for g in store.sessions(query="sandbox", mode="union")["sessions"]}
    assert by["uuid-a"]["did"] == "option B is the pick"  # the empty latest row says nothing
    assert by["uuid-b"]["did"] == ""                               # no summary: nothing guessed


def obs_index_store():
    from observer import obs_index
    return obs_index.store_db()


def test_sessions_limit_and_offset_count_sessions(store):
    first = store.sessions(query="sandbox", mode="union", limit=1)
    second = store.sessions(query="sandbox", mode="union", limit=1, offset=1)
    assert [g["session"] for g in first["sessions"]] == ["uuid-a"]
    assert [g["session"] for g in second["sessions"]] == ["uuid-b"]


def test_group_by_session_votes_by_reciprocal_rank():
    # one top hit beats one low hit; many hits beat one top hit
    ranked = [("x", "1", "t", "p"), ("", "2", "t", "p"), ("y", "3", "t", "p")]
    out = group_by_session(ranked, lambda s: {}, 10, 0, 3)
    assert [g["session"] for g in out["sessions"]] == ["x", "y"] and out["unlinked"] == 1
    ranked = [("x", "1", "t", "p")] + [("y", str(i), "t", "p") for i in range(2, 5)]
    assert group_by_session(ranked, lambda s: {}, 10, 0, 3)["sessions"][0]["session"] == "y"


def test_a_limit_past_the_channel_depth_is_honoured(tmp_path, monkeypatch):
    """The channels fetched CHANNEL_DEPTH (50) rows whatever the limit, so limit=80
    answered 50 — and a grouping 200 deep saw a quarter of what it asked for."""
    d = tmp_path / "store"
    d.mkdir()
    conn = sqlite3.connect(d / "brain.db")
    conn.executescript(sc.DDL)
    for i in range(1, 91):
        conn.execute("INSERT INTO observations(id, memory_session_id, project, type, title, narrative, facts, "
                     "concepts, created_at, created_at_epoch) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (i, "m", "p", "discovery", f"gateway row {i}", "", "[]", "[]", "2026-01-01T00:00:00Z", i))
    conn.commit()
    conn.close()
    monkeypatch.setenv(env_name("STORE"), str(d))
    monkeypatch.delenv(env_name("OBS_SEARCH_MODE"), raising=False)
    store = SqliteFtsBackend()
    for mode in ("hybrid", "union", "and"):
        assert len(store.search(query="gateway", mode=mode, limit=80)) == 80
    assert len(store.search(query="gateway", limit=20)) == 20


def test_search_by_session_from_the_shell(store, monkeypatch):
    from click.testing import CliRunner
    from observer import cli, obs_index
    monkeypatch.setattr(obs_index, "BACKEND", store)
    monkeypatch.setattr(cli.ui, "_MODE", "plain")  # --json is sticky for the process
    plain = CliRunner().invoke(cli.observer, ["search", "sandbox", "--by-session"])
    assert plain.exit_code == 0, plain.output
    assert "(2 · 5 rows, 1 with no session)" in plain.stdout, plain.stdout
    rows = [l.split("\t") for l in plain.stdout.splitlines() if "\t" in l][1:]  # past the header
    assert [r[0] for r in rows] == ["uuid-a", "uuid-b"]
    got = json.loads(CliRunner().invoke(cli.observer, ["search", "sandbox", "--by-session", "--json"]).stdout)
    assert [g["session"] for g in got] == ["uuid-a", "uuid-b"]
    got = json.loads(CliRunner().invoke(cli.observer, ["search", "sandbox", "--json"]).stdout)
    assert {r["session"] for r in got} == {"uuid-a", "uuid-b", ""}
