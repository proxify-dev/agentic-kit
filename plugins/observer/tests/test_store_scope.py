"""A fresh kit has no db/ until its first writer creates it.

Found 2026-09-14 by the portable E2E: the observer ran against a repo-local
`.ak/` that had never seen `ak observer store init` and died with
`unable to open database file`, because connect() opened the path without
creating its parent. The first writer must create the directory; a read-only
open must not.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
from _brand import HOME_DIR, env_name  # noqa: E402
import store_common as sc  # noqa: E402


def test_writer_creates_missing_store_dir(tmp_path):
    db = tmp_path / "scope" / HOME_DIR / "db" / "brain.db"
    assert not db.parent.exists()
    conn = sc.connect(db)
    sc.ensure_schema(conn)
    conn.close()
    assert db.is_file()
    assert db.parent.is_dir()


def test_readonly_open_never_creates_anything(tmp_path):
    db = tmp_path / "scope" / HOME_DIR / "db" / "brain.db"
    with pytest.raises(sqlite3.OperationalError):
        sc.connect(db, readonly=True)
    assert not db.parent.exists()


def test_a_pinned_vault_keeps_its_store_beside_it(tmp_path, monkeypatch):
    """$AK_PATH alone is a sandbox: its kit home is the vault itself, so its db/ is sealed inside."""
    scope = tmp_path / "repo" / HOME_DIR
    scope.mkdir(parents=True)
    (scope / "vault-manifest.json").write_text("{}")
    monkeypatch.setenv(env_name("PATH"), str(scope))
    monkeypatch.delenv(env_name("STORE"), raising=False)
    assert sc.store_db() == scope / "db" / "brain.db"
    conn = sc.connect()
    sc.ensure_schema(conn)
    conn.close()
    assert (scope / "db" / "brain.db").is_file()


def test_a_project_scope_writes_to_the_one_global_store(tmp_path, monkeypatch):
    """A repo with its own `.ak/` keeps its notes there, but its sessions are rows in the
    global kit's store: one log for every project, so a split vault never splits it."""
    home = tmp_path / "home"
    (home / HOME_DIR / "vault").mkdir(parents=True)
    (home / HOME_DIR / "vault" / "vault-manifest.json").write_text("{}")
    repo = home / "code" / "repo"
    (repo / HOME_DIR).mkdir(parents=True)
    (repo / HOME_DIR / "vault-manifest.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(env_name("PATH"), raising=False)
    monkeypatch.delenv(env_name("STORE"), raising=False)
    monkeypatch.chdir(repo)
    assert sc.vault_root() == (repo / HOME_DIR).resolve()  # the notes' scope: nearest
    assert sc.store_db() == (home / HOME_DIR).resolve() / "db" / "brain.db"  # the log: the kit's


def test_the_kit_home_and_the_store_override_move_the_store(tmp_path, monkeypatch):
    """$AK_HOME moves the kit home, db/ with it; $AK_STORE moves db/ alone and wins."""
    monkeypatch.delenv(env_name("PATH"), raising=False)
    monkeypatch.delenv(env_name("STORE"), raising=False)
    monkeypatch.setenv(env_name("HOME"), str(tmp_path / "kit"))
    assert sc.store_db() == (tmp_path / "kit").resolve() / "db" / "brain.db"
    monkeypatch.setenv(env_name("STORE"), str(tmp_path / "elsewhere"))
    assert sc.store_db() == (tmp_path / "elsewhere").resolve() / "brain.db"
