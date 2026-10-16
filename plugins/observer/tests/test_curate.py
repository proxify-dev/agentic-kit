"""patterns · fold · forget against a throwaway store, and the hidden-row rule they rely on.

Each test builds its own vault under tmp_path (AK_PATH pins it) with three rows from
two sessions sharing one anchor, so `patterns` has a cluster to find, `fold` two rows to
fold, `forget` one to hide — and search/timeline must then stop showing what was hidden.
Then the same writes as `ak observer fold · forget`: each preview's hint is the command that writes.
"""
import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "src"))
sys.path.insert(0, str(PLUGIN / "hooks"))
from _brand import env_name  # noqa: E402


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    (v / "db").mkdir(parents=True)
    (v / "vault-manifest.json").write_text("{}")
    monkeypatch.setenv(env_name("PATH"), str(v))
    monkeypatch.delenv(env_name("STORE"), raising=False)
    import store_common as sc
    from observer import obs_index

    # BACKEND is picked at import from the machine's real store; pin it to this vault's
    monkeypatch.setattr(obs_index, "BACKEND", obs_index.SqliteFtsBackend())
    conn = sc.connect()
    sc.ensure_schema(conn)
    for sid in ("s1", "s2"):
        conn.execute("INSERT INTO sdk_sessions (content_session_id, memory_session_id, project, platform_source, "
                     "started_at, started_at_epoch) VALUES (?, ?, 'p', 'brain', ?, ?)",  # brand: historical (the platform_source our observer writes)
                     (f"c-{sid}", sid, "2026-09-01T00:00:00", 1_000))
    rows = [("s1", "first", 1_000), ("s1", "second", 2_000), ("s2", "third", 3_000)]
    for sid, title, epoch in rows:
        conn.execute(
            "INSERT INTO observations (memory_session_id, project, type, title, facts, narrative, concepts, "
            "files_read, files_modified, created_at, created_at_epoch, anchors) "
            "VALUES (?, 'p', 'discovery', ?, '[]', '', '[\"topic\"]', '[]', '[]', ?, ?, '[\"topic\"]')",
            (sid, title, "2026-09-01T00:00:00Z", epoch))
    conn.commit()
    conn.close()
    return v


def test_patterns_finds_the_cluster(vault):
    from observer import curate

    out = curate.patterns(min=3)
    assert out["kind"] == "patterns"
    assert [c["anchor"] for c in out["clusters"]] == ["topic"]
    assert sorted(out["clusters"][0]["sessions"]) == ["c-s1", "c-s2"]  # the uuids the tracer takes, not memory ids
    assert "unlinked" not in out["clusters"][0]
    assert curate.patterns(min=4)["clusters"] == []


def test_fold_is_lossless_and_undo_restores(vault):
    from observer import curate
    from observer.obs_index import BACKEND

    folded = curate.fold(ids=[1, 2], title="two rows", facts=["a", "b"])
    assert folded["kind"] == "folded" and folded["folded"] == [1, 2]
    sid = folded["summaryId"]
    shown = {r["id"] for r in BACKEND.search()}
    assert "1" not in shown and "2" not in shown and str(sid) in shown, shown
    with pytest.raises(curate.Refused):
        curate.fold(ids=[1, 3], title="again", facts=["x"])  # 1 is folded already
    restored = curate.fold(undo=sid)
    assert restored == {"kind": "unfolded", "summaryId": sid, "restored": [1, 2]}
    assert {"1", "2", "3"} <= {r["id"] for r in BACKEND.search()}
    ledger = (vault / "db" / "objects-ledger.jsonl").read_text().splitlines()
    assert [json.loads(l)["op"] for l in ledger] == ["fold", "unfold"]


def test_forget_previews_by_default_and_writes_only_with_confirm(vault):
    from observer import curate
    from observer.obs_index import BACKEND

    prev = curate.forget(ids=[3])
    assert prev["kind"] == "forget-preview" and prev["candidates"][0]["id"] == 3
    assert "3" in {r["id"] for r in BACKEND.search()}
    with pytest.raises(curate.Refused):
        curate.forget(ids=[3], confirm=True)  # no reason
    gone = curate.forget(ids=[3], reason="noise", confirm=True)
    assert gone == {"kind": "forgotten", "ids": [3], "reason": "noise"}
    assert "3" not in {r["id"] for r in BACKEND.search()}
    assert "3" not in {r["id"] for r in BACKEND.timeline(anchor="2")["rows"]}
    assert BACKEND.get(["3"])[0]["content"] and "forgotten" in BACKEND.get(["3"])[0]["content"]  # read by id is never hidden
    back = curate.forget(ids=[3], undo=True, confirm=True)
    assert back == {"kind": "unforgotten", "ids": [3]}
    assert "3" in {r["id"] for r in BACKEND.search()}


def test_forget_by_query_is_a_preview(vault):
    from observer import curate

    out = curate.forget(query="second")
    assert out["kind"] == "forget-preview" and out["candidates"] and out["candidates"][0]["id"] == 2


# ── the same writes from the shell: `ak observer fold · forget` ────────────────
def run(*args):
    from click.testing import CliRunner
    from observer import cli
    return CliRunner().invoke(cli.observer, list(args))


@pytest.fixture()
def shell(vault, monkeypatch):
    from observer import cli
    monkeypatch.setattr(cli.ui, "_MODE", "plain")  # --json is sticky for the process; each test is its own call
    return vault


def hint(r, which: int = 0) -> list:
    """One of the hint's ` · `-separated commands as argv past `<cli> observer`, its (note) dropped."""
    import shlex
    line = next(l for l in r.stdout.splitlines() if l.startswith("hint: "))
    words = shlex.split(line.removeprefix("hint: ").split(" · ")[which].split(" (")[0])
    return words[words.index("observer") + 1:]


def shown_ids() -> set:
    from observer.obs_index import BACKEND
    return {r["id"] for r in BACKEND.search()}


def test_fold_from_the_shell_names_its_undo(shell):
    r = run("fold", "1", "memdb:2", "--title", "two rows", "--fact", "a", "--fact", "b")
    assert r.exit_code == 0, r.output
    assert r.stdout.startswith("ok: folded memdb:1, memdb:2 into memdb:4"), r.stdout
    assert hint(r) == ["get", "4"] and hint(r, 1) == ["fold", "--undo", "4"]
    assert "1" not in shown_ids() and "4" in shown_ids()
    back = run(*hint(r, 1))
    assert back.exit_code == 0 and "memdb:1, memdb:2 back in recall" in back.stdout, back.output
    assert {"1", "2"} <= shown_ids() and "4" not in shown_ids()
    ledger = (shell / "db" / "objects-ledger.jsonl").read_text().splitlines()
    assert [json.loads(l)["op"] for l in ledger] == ["fold", "unfold"]
    assert json.loads(ledger[0])["actor"].startswith("agent:terminal")


def test_a_refused_fold_is_an_error_and_writes_nothing(shell):
    r = run("fold", "1", "--title", "one", "--fact", "x")
    assert r.exit_code == 64 and r.stderr.startswith("error: fold needs at least two ids"), r.output
    assert run("fold", "1", "2", "--title", "no facts").exit_code == 64
    assert run("fold", "1", "abc", "--title", "t", "--fact", "f").exit_code == 64
    assert run("fold", "1", "2", "--undo", "4").exit_code == 64
    assert {"1", "2", "3"} <= shown_ids()
    assert not (shell / "db" / "objects-ledger.jsonl").exists()


def test_fold_json_is_the_store_answer(shell):
    got = json.loads(run("fold", "1,2", "--title", "t", "--fact", "f", "--json").stdout)
    assert got == {"kind": "folded", "summaryId": 4, "folded": [1, 2], "title": "t"}


def test_forget_by_id_previews_and_its_hint_is_the_write(shell):
    r = run("forget", "3", "--reason", "a probe, not a finding")
    assert r.exit_code == 0, r.output
    assert "3\tthird" in r.stdout and "3" in shown_ids()  # a preview writes nothing
    assert hint(r) == ["forget", "3", "--reason", "a probe, not a finding", "--confirm"]
    done = run(*hint(r))
    assert done.exit_code == 0 and done.stdout.startswith("ok: forgot memdb:3"), done.output
    assert "3" not in shown_ids()
    assert hint(done) == ["forget", "3", "--undo", "--confirm"]
    undo_preview = run("forget", "3", "--undo")
    assert hint(undo_preview) == ["forget", "3", "--undo", "--confirm"] and "3" not in shown_ids()
    assert run(*hint(done)).exit_code == 0
    assert "3" in shown_ids()


def test_forget_without_a_reason_is_refused(shell):
    r = run("forget", "3", "--confirm")
    assert r.exit_code == 64 and r.stderr.startswith("error: forget needs a reason"), r.output
    assert "<why>" in run("forget", "3").stdout  # the preview cannot name a reason it was not given
    assert "3" in shown_ids()


def test_forget_by_query_only_previews(shell):
    r = run("forget", "--query", "second", "--reason", "noise", "--confirm")
    assert r.exit_code == 0, r.output
    assert "warn: a --query only previews" in r.stdout
    assert hint(r) == ["forget", "2", "--reason", "noise", "--confirm"]
    assert "2" in shown_ids()
    got = json.loads(run("forget", "--query", "second", "--json").stdout)
    assert got["kind"] == "forget-preview" and got["candidates"][0]["id"] == 2


def test_forget_misses_and_bad_calls(shell):
    miss = run("forget", "999", "--reason", "x")
    assert miss.exit_code == 1 and "memdb:999 is not on record" in miss.stdout, miss.output
    assert run("forget", "999", "--reason", "x", "--confirm").exit_code == 64
    assert run("forget", "--query", "nothing-like-this", "--reason", "x").exit_code == 1
    assert run("forget", "3", "--query", "second").exit_code == 64
    assert run("forget", "--reason", "x").exit_code == 64
    assert run("forget", "--query", "second", "--undo").exit_code == 64


def test_no_store_is_66_and_none_is_made(tmp_path, monkeypatch):
    from observer import cli
    monkeypatch.setattr(cli.ui, "_MODE", "plain")
    monkeypatch.setenv(env_name("STORE"), str(tmp_path / "none"))
    for argv in (("fold", "1", "2", "--title", "t", "--fact", "f"), ("forget", "1", "--reason", "x", "--confirm")):
        r = run(*argv)
        assert r.exit_code == 66 and r.stderr.startswith("error: no store at"), r.output
    assert not (tmp_path / "none").exists()
