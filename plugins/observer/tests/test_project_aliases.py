"""The project-label alias: the vault and the code repo are one project to memory.

The 2026-09-15 split moved the app and the plugins out of `~/.ak` into
`~/agentic-kit`. claude-mem labels a session by its git-root basename, so sessions in
the vault are filed `.ak` and sessions in the code repo are filed `ak` —
two labels over one body of work, with ~37k rows already written under the old
one.

The design under test is asymmetric on purpose:

  WRITE — untouched. `project_label` still answers with the session's own
          git-root basename. No row is rewritten, no history is rebuilt.
  READ  — expanded. `labels_with_aliases` joins the pair, so a session in
          either tree queries both.
"""
import os
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
from _brand import env_name  # noqa: E402
import capture_common as cc  # noqa: E402

# The project-label pair is history, not brand: the rows the vault wrote say `.ak`, the rows the
# code repo wrote say `ak`, and capture_common.PROJECT_ALIASES joins those two words whatever the kit is called.
REPO_LABEL = "brain"  # brand: historical
VAULT_LABEL = ".brain"  # brand: historical


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """A home of its own: the machine's lineage.jsonl (a real ~/agentic-kit → ~/agentic-kit move) would add its alias."""
    monkeypatch.delenv(env_name("PATH"), raising=False)
    monkeypatch.setenv(env_name("HOME"), str(tmp_path / "home"))


# ── the read-side expansion ──────────────────────────────────────────────────

def test_brain_pulls_in_the_vault_label():
    """A session in the code repo must see the rows the vault wrote."""
    assert cc.labels_with_aliases([REPO_LABEL]) == [REPO_LABEL, VAULT_LABEL]


def test_vault_pulls_in_the_repo_label():
    """…and the reverse, so the vault sees rows written from the code repo."""
    assert cc.labels_with_aliases([VAULT_LABEL]) == [VAULT_LABEL, REPO_LABEL]


def test_an_alias_lands_beside_the_label_that_pulled_it_in():
    """Order-preserving: the caller's precedence survives the expansion, and the
    alias follows its own label rather than being appended at the end."""
    assert cc.labels_with_aliases([REPO_LABEL, "proxify"]) == [REPO_LABEL, VAULT_LABEL, "proxify"]
    assert cc.labels_with_aliases(["proxify", REPO_LABEL]) == ["proxify", REPO_LABEL, VAULT_LABEL]


def test_the_pair_de_duplicates():
    """Both spellings already present is the common case for a bound session; it
    must not produce a label twice or reorder what the caller asked for."""
    assert cc.labels_with_aliases([VAULT_LABEL, REPO_LABEL]) == [VAULT_LABEL, REPO_LABEL]
    assert cc.labels_with_aliases([REPO_LABEL, VAULT_LABEL]) == [REPO_LABEL, VAULT_LABEL]


def test_unaliased_labels_pass_through_unchanged():
    assert cc.labels_with_aliases(["proxify", "some-repo"]) == ["proxify", "some-repo"]


def test_empty_and_falsy_input():
    assert cc.labels_with_aliases([]) == []
    assert cc.labels_with_aliases(None) == []
    assert cc.labels_with_aliases(["", REPO_LABEL]) == [REPO_LABEL, VAULT_LABEL]


# ── the write side stays put ─────────────────────────────────────────────────

def test_the_write_side_never_aliases(tmp_path):
    """`project_label` is the writer's answer and it is one string, unexpanded —
    the whole point of the design is that no row changes."""
    repo = tmp_path / REPO_LABEL
    (repo / ".git").mkdir(parents=True)
    (repo / "app").mkdir()
    assert cc.project_label(str(repo / "app")) == REPO_LABEL
    assert isinstance(cc.project_label(str(repo)), str)


# ── labels_for: the SessionStart digest's query ──────────────────────────────

@pytest.fixture
def labels_for(monkeypatch, tmp_path):
    """session-start.py imports sibling hooks at module scope; load it the same
    way the hook runs and hand back just the function under test."""
    import importlib.util

    monkeypatch.setenv(env_name("PATH"), str(tmp_path / "vault"))
    spec = importlib.util.spec_from_file_location(
        "session_start_under_test", PLUGIN / "hooks" / "session-start.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.labels_for


def test_labels_for_a_code_repo_session_reaches_the_vault_rows(labels_for, tmp_path):
    """A session in `~/agentic-kit`-shaped cwd: its own label first, the vault's next."""
    repo = tmp_path / REPO_LABEL
    (repo / ".git").mkdir(parents=True)
    out = labels_for(str(repo))
    assert out[0] == REPO_LABEL
    assert VAULT_LABEL in out


def test_labels_for_a_vault_session_reaches_the_repo_rows(labels_for, tmp_path):
    repo = tmp_path / VAULT_LABEL
    (repo / ".git").mkdir(parents=True)
    out = labels_for(str(repo))
    assert out[0] == VAULT_LABEL
    assert REPO_LABEL in out


def test_labels_for_a_nested_dir_uses_the_git_root(labels_for, tmp_path):
    """`~/agentic-kit/plugins/ak` is one repo, so the label is the root's, not the
    leaf's — and the alias still rides along."""
    repo = tmp_path / REPO_LABEL
    (repo / ".git").mkdir(parents=True)
    nested = repo / "plugins" / REPO_LABEL
    nested.mkdir(parents=True)
    out = labels_for(str(nested))
    assert out[0] == REPO_LABEL
    assert VAULT_LABEL in out


def test_labels_for_an_unrelated_repo_is_not_aliased(labels_for, tmp_path):
    repo = tmp_path / "proxify"
    (repo / ".git").mkdir(parents=True)
    assert REPO_LABEL not in labels_for(str(repo))
    assert VAULT_LABEL not in labels_for(str(repo))


def test_labels_for_keeps_the_bound_slug(labels_for, tmp_path):
    """The bound project slug is a third label and must survive the expansion."""
    repo = tmp_path / REPO_LABEL
    (repo / ".git").mkdir(parents=True)
    out = labels_for(str(repo), "my-app")
    assert "my-app" in out
    assert out[0] == REPO_LABEL


def test_labels_for_never_returns_empty(labels_for, tmp_path, monkeypatch):
    """The digest always has something to query. An empty cwd is not a hole: the
    resolver falls back to the process cwd, so the list is non-empty either way
    and the `["unbound"]` floor is the last resort, never the normal answer."""
    monkeypatch.chdir(tmp_path)
    out = labels_for("")
    assert out and all(isinstance(x, str) and x for x in out)
