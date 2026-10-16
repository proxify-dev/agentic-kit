"""observer search: the candidate modes over the store — the lexical rule,
the dense channel, and the project alias the read side keeps. The store is the
plugin's own DDL on a tmp path."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN / "src"))
from _brand import env_name  # noqa: E402
import capture_common as cc  # noqa: E402
import store_common as sc  # noqa: E402
from observer import obs_index  # noqa: E402
from observer.obs_index import SqliteFtsBackend  # noqa: E402

# The project-label pair is history, not brand: the rows the vault wrote say `.ak`, the rows the
# code repo wrote say `ak`, and capture_common.PROJECT_ALIASES joins those two words whatever the kit is called.
REPO_LABEL = "brain"  # brand: historical
VAULT_LABEL = ".brain"  # brand: historical

# id, project, title, date — every title is the only searchable text, so the
# match arithmetic below can be read off this table
ROWS = [
    (1, VAULT_LABEL, "gateway failover switch landed", "2026-01-01"),
    (2, VAULT_LABEL, "failover proxy account rotation", "2026-01-02"),
    (3, "other-repo", "gateway timeout on the proxy", "2026-01-03"),
    (4, VAULT_LABEL, "wikilinks resolver rung", "2026-01-04"),
    (5, VAULT_LABEL, "gateway upstream direct takeover", "2026-01-05"),
    (6, REPO_LABEL, "observer search eval", "2026-01-06"),
    (7, VAULT_LABEL, "gateway metrics dashboard", "2026-01-07"),
    (8, VAULT_LABEL, "switch statement refactor", "2026-01-08"),
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / "store"
    d.mkdir()
    conn = sqlite3.connect(d / "brain.db")
    conn.executescript(sc.DDL)
    conn.execute("INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source, "
                 "started_at, started_at_epoch) VALUES(?,?,?,?,?,?)",
                 ("s", "m1", "demo", "claude", "2026-01-01T00:00:00Z", 1))
    for i, (rid, project, title, date) in enumerate(ROWS):
        conn.execute("INSERT INTO observations(id, memory_session_id, project, type, title, narrative, facts, "
                     "concepts, created_at, created_at_epoch) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (rid, "m1", project, "discovery", title, "", json.dumps([]), json.dumps([]),
                      f"{date}T00:00:00Z", 1000 + i))
    conn.commit()
    conn.close()
    monkeypatch.setenv(env_name("STORE"), str(d))
    monkeypatch.delenv(env_name("OBS_SEARCH_MODE"), raising=False)
    return SqliteFtsBackend()


def ids(rows):
    return [r["id"] for r in rows]


def test_the_read_side_uses_the_one_alias_table():
    """The store engine expands project labels with capture_common's own function —
    not a copy of its table. Re-mirroring the value here is what this fails on."""
    assert obs_index.labels_with_aliases is cc.labels_with_aliases
    assert not hasattr(SqliteFtsBackend, "PROJECT_ALIASES")


def test_terms_dedup_and_strip_quotes():
    assert SqliteFtsBackend._terms('app "idea" app  ') == ["app", "idea"]
    assert SqliteFtsBackend._terms('') == []


def test_and_starves_on_one_absent_word_and_union_does_not(store):
    q = "gateway failover switch nonexistentword"
    assert store.search(query=q, mode="and") == []
    assert ids(store.search(query=q, mode="union")) != []


def test_union_keeps_the_all_terms_tier_first(store):
    q = "gateway failover switch"
    assert ids(store.search(query=q, mode="and")) == ["1"]  # the only row with all three
    got = ids(store.search(query=q, mode="union"))
    assert got[0] == "1"
    assert set(got) == {"1", "2", "3", "5", "7", "8"}  # every row with any of the three


def test_union_does_not_widen_when_and_has_enough(store):
    """UNION_FLOOR rows from the strict tier is a full answer; no any-term rows follow."""
    store.UNION_FLOOR = 1
    assert ids(store.search(query="gateway failover switch", mode="union")) == ["1"]


def test_project_filter_reads_both_spellings(store):
    both = store.search(query="gateway", project=REPO_LABEL, mode="union")
    assert {r["project"] for r in both} == {VAULT_LABEL}  # `ak` has no gateway row; its alias does
    only = store.search(query="gateway", project="other-repo", mode="union")
    assert ids(only) == ["3"]


def test_hybrid_is_the_default_and_env_is_the_rollback(store, monkeypatch):
    assert SqliteFtsBackend.DEFAULT_MODE == "hybrid"
    q = "gateway failover switch nonexistentword"
    assert ids(store.search(query=q)) == ids(store.search(query=q, mode="hybrid"))
    monkeypatch.setenv(env_name("OBS_SEARCH_MODE"), "and")
    assert store.search(query=q) == []  # the original behaviour, one env var away
    monkeypatch.setenv(env_name("OBS_SEARCH_MODE"), "bogus")
    assert ids(store.search(query=q)) == ids(store.search(query=q, mode="hybrid"))  # unknown → default, not a crash


def test_empty_query_is_newest_first_in_every_mode(store):
    for mode in SqliteFtsBackend.MODES:
        assert ids(store.search(query="", mode=mode, limit=3)) == ["8", "7", "6"]


def test_dense_modes_answer_as_union_without_a_collection(store):
    """No zvec collection beside this store → vector/hybrid are union, not an error."""
    q = "gateway failover switch"
    for mode in ("vector", "hybrid"):
        assert ids(store.search(query=q, mode=mode)) == ids(store.search(query=q, mode="union"))


def test_rrf_fusion_orders_by_summed_reciprocal_rank():
    f = SqliteFtsBackend._fuse
    assert f(["a", "b", "c"], ["b", "a"]) == ["a", "b", "c"]  # a: 1/61+1/62 > b: 1/62+1/61? equal → tie
    assert f(["a", "b"], ["c", "b"])[0] == "b"                 # in both lists beats a single top rank
    assert f(["x"], None, []) == ["x"]                         # an absent channel is no votes


def test_build_vectors_then_vector_mode_answers_by_meaning(store):
    """The dense channel end to end: build the collection, then a query that shares no
    word with any row still finds the row that means the same thing."""
    pytest.importorskip("zvec")
    pytest.importorskip("fastembed")
    r = store.build_vectors()
    assert r["embedded"] == len(ROWS) and r["failed"] == 0

    q = "code restructuring cleanup"  # no row contains any of these words
    assert store.search(query=q, mode="union") == []
    assert ids(store.search(query=q, mode="vector"))[0] == "8"  # 'switch statement refactor'


def test_hybrid_gate_lets_a_full_lexical_answer_stand(store):
    """The gate: the every-term tier alone fills the floor, so the query named its rows
    and the dense channel does not get to dilute them."""
    pytest.importorskip("zvec")
    pytest.importorskip("fastembed")
    store.build_vectors()
    store.UNION_FLOOR = 1  # one every-term row is already a full answer
    q = "gateway failover switch"
    assert ids(store.search(query=q, mode="union")) == ["1"]
    assert ids(store.search(query=q, mode="hybrid")) == ["1"]


def test_hybrid_fuses_into_a_starved_query(store):
    """The other side of the gate: nothing matches every term, so dense votes and the
    answer grows past what any term reached."""
    pytest.importorskip("zvec")
    pytest.importorskip("fastembed")
    store.build_vectors()
    q = "gateway failover switch nonexistentword"
    union = set(ids(store.search(query=q, mode="union")))
    hybrid = set(ids(store.search(query=q, mode="hybrid")))
    assert union and union < hybrid


def test_build_vectors_is_incremental(store):
    pytest.importorskip("zvec")
    pytest.importorskip("fastembed")
    store.build_vectors()
    again = store.build_vectors()
    assert again["embedded"] == 0 and again["skipped"] == len(ROWS)
    fresh = store.build_vectors(rebuild=True)
    assert fresh["embedded"] == len(ROWS) and fresh["skipped"] == 0


def test_embed_text_is_title_subtitle_facts():
    row = {"title": "gateway landed", "subtitle": "step 0", "facts": json.dumps(["takeover moves tokens"]),
           "narrative": "long prose that must not be embedded"}
    assert SqliteFtsBackend.embed_text(row) == "gateway landed. step 0. takeover moves tokens"


def test_the_gate_answers_before_the_model_loads(store, monkeypatch):
    """A query the every-term tier answered never touches the dense channel — so the
    model's seconds-long first load is paid only by a starved query."""
    def boom(texts):
        raise AssertionError("the dense channel was consulted")
    monkeypatch.setattr(SqliteFtsBackend, "embed", classmethod(lambda cls, texts: boom(texts)))
    store.UNION_FLOOR = 1
    assert ids(store.search(query="gateway failover switch", mode="hybrid")) == ["1"]


def test_a_search_holds_no_lock_so_session_end_can_write(store, monkeypatch):
    """zvec's LOCK is taken by read-only opens too. A reader that kept its handle locked
    the SessionEnd writer out for as long as the reader lived (reproduced: "Can't lock
    read-write collection"). Search, then write a new row and embed it: the writer gets
    in, and the next search — a fresh reader — sees the new vector."""
    pytest.importorskip("zvec")
    pytest.importorskip("fastembed")
    store.build_vectors()
    assert store.search(query="code restructuring cleanup", mode="vector")
    conn = sqlite3.connect(Path(obs_index.store_db()))
    conn.execute("INSERT INTO observations(id, memory_session_id, project, type, title, narrative, facts, "
                 "concepts, created_at, created_at_epoch) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (9, "m1", VAULT_LABEL, "discovery", "kitchen recipe for sourdough bread", "", "[]", "[]",
                  "2026-01-09T00:00:00Z", 2000))
    conn.commit()
    conn.close()
    monkeypatch.setattr(SqliteFtsBackend, "LOCK_WAIT", {"reader": 1.0, "writer": 2.0})
    again = store.build_vectors()
    assert again["embedded"] == 1 and again["failed"] == 0
    assert ids(store.search(query="baking loaves", mode="vector"))[0] == "9"


def test_one_embed_at_a_time(store):
    """Two sessions ending together must not both embed the store: the second sees the
    guard held and returns `busy` (or, with wait, would queue)."""
    import fcntl
    pytest.importorskip("zvec")
    d = obs_index.store_db().parent / "zvec"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".embed.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        r = store.build_vectors()
    assert r["busy"] and r["embedded"] == 0
