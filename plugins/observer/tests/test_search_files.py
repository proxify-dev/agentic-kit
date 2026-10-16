"""`ak observer search --files <path>` answers with the rows the Read hook counts.

hooks/file-context.py tells a session which rows touched the file it read and points it at
`ak observer search --files <path>` for the rest; the two must agree on what "touched"
means. The rule (`_touches`, `_repo_rel`) is copied into obs_index — the first test fails on a
byte of drift, the rest walk the rule's cases through the verb. A value that is not a path is
still the substring it always was."""
import importlib.util
import inspect
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


def _file_context(monkeypatch, tmp_path):
    monkeypatch.setenv(env_name("PATH"), str(tmp_path / "vault"))
    spec = importlib.util.spec_from_file_location("file_context_under_test", PLUGIN / "hooks" / "file-context.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_rule_is_the_read_hooks_byte_for_byte(monkeypatch, tmp_path):
    fc = _file_context(monkeypatch, tmp_path)
    for name in ("_touches", "_repo_rel"):
        assert inspect.getsource(getattr(obs_index, name)) == inspect.getsource(getattr(fc, name)), (
            f"{name} drifted from hooks/file-context.py — copy it over whole")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo `proj` with src/x.py, a worktree of it under .claude/worktrees/w, and a store
    whose rows name that file every way the observer writes paths."""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "x.py").write_text("")
    wt = root / ".claude" / "worktrees" / "w"
    (wt / "src").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere\n")
    (wt / "src" / "x.py").write_text("")
    target = str(root / "src" / "x.py")
    rows = {  # id: (project, files_read, files_modified) — the comment says whether it names src/x.py
        1: ("proj", [], [target]),                               # the file itself
        2: ("proj", ["src/x.py"], []),                           # repo-relative, same project
        3: ("proj", [], [str(wt / "src" / "x.py")]),             # its worktree copy
        4: ("other", ["src/x.py"], []),                          # relative, another repo's src/x.py: no
        5: ("proj", ["notx.py"], []),                            # a name that merely contains it: no
        6: ("proj", ["lib/src/x.py"], []),                       # a different file ending in x.py: no
    }
    d = tmp_path / "store"
    d.mkdir()
    conn = sqlite3.connect(d / "brain.db")
    conn.executescript(sc.DDL)
    for i, (project, read, modified) in rows.items():
        conn.execute("INSERT INTO observations(id, memory_session_id, project, type, title, narrative, facts, concepts, "
                     "files_read, files_modified, created_at, created_at_epoch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (i, "m1", project, "change", f"row {i}", "", "[]", "[]", json.dumps(read), json.dumps(modified),
                      "2026-09-01T00:00:00Z", 1000 + i))
    conn.commit()
    conn.close()
    monkeypatch.setenv(env_name("PATH"), str(tmp_path / "vault"))
    monkeypatch.setenv(env_name("STORE"), str(d))
    monkeypatch.setenv(env_name("OBS_SEARCH_MODE"), "union")
    monkeypatch.setattr(obs_index, "BACKEND", obs_index.SqliteFtsBackend())
    monkeypatch.setattr(cli.ui, "_MODE", "plain")
    return target


def ids(*args) -> list:
    r = CliRunner().invoke(cli.observer, ["search", *args, "--json"])
    assert r.exit_code == 0, r.output
    return sorted(int(x["id"]) for x in json.loads(r.output))


def test_a_path_names_that_file_its_checkouts_and_nothing_else(repo):
    assert ids("--files", repo) == [1, 2, 3]


def test_the_hook_and_the_verb_count_the_same_rows(repo, monkeypatch, tmp_path):
    fc = _file_context(monkeypatch, tmp_path)
    assert sorted(r[0] for r in fc.prior_observations(repo)) == ids("--files", repo)


def test_a_bare_name_is_still_a_substring(repo):
    assert ids("--files", "x.py") == [1, 2, 3, 4, 5, 6]


def test_a_path_with_a_query_and_the_plain_grammar(repo):
    r = CliRunner().invoke(cli.observer, ["search", "row", "--files", repo])
    assert r.exit_code == 0, r.output
    got = [l.split("\t")[0] for l in r.output.splitlines() if "\t" in l][1:]
    assert sorted(got) == ["1", "2", "3"], r.output
    assert r.output.splitlines()[-1].startswith(f"hint: {command('timeline')}")
