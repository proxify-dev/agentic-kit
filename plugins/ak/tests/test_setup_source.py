"""setup/source.py: where the release clone is, and the URL it is cloned from (first answer wins)."""
import json
import os
from importlib import metadata
from pathlib import Path

import pytest

from ak._brand import RELEASE_DIR, REPO_DIR, data_dir, env_name
from ak.setup import source

FAKE_GIT = """#!/bin/sh
case "$*" in
  *"remote get-url origin"*) echo "https://example.test/kit.git" ;;
  *"rev-parse --short HEAD"*) echo "abc1234" ;;
  *) exit 1 ;;
esac
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("XDG_DATA_HOME", env_name("REPO_URL")):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(metadata, "distribution", _installed(None))
    return tmp_path


def _installed(record):
    """A stand-in for importlib.metadata.distribution(): ak with this direct_url.json (None: no record)."""
    class Dist:
        def read_text(self, name):
            return None if record is None else json.dumps(record)

    def distribution(name):
        if record is False:
            raise metadata.PackageNotFoundError(name)
        return Dist()
    return distribution


def _git_on_path(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "git").write_text(FAKE_GIT)
    (bin_ / "git").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_))


def test_release_dir_is_under_the_data_dir(home):
    assert source.release_dir() == Path(data_dir()) / RELEASE_DIR
    assert source.release_dir().is_relative_to(home)


def test_nothing_to_go_on_gives_none(home, monkeypatch):
    monkeypatch.setattr(metadata, "distribution", _installed(False))
    assert source.repo_url() is None


def test_explicit_beats_env_beats_the_rest(home, monkeypatch):
    monkeypatch.setattr(metadata, "distribution", _installed({"url": "https://git.test/a", "vcs_info": {"vcs": "git"}}))
    monkeypatch.setenv(env_name("REPO_URL"), "https://env.test/kit")
    assert source.repo_url("https://flag.test/kit") == "https://flag.test/kit"
    assert source.repo_url() == "https://env.test/kit"
    monkeypatch.delenv(env_name("REPO_URL"))
    assert source.repo_url() == "https://git.test/a"


def test_a_git_install_names_its_repo_without_the_subdirectory(home, monkeypatch):
    record = {"url": "https://git.test/kit#subdirectory=plugins/ak", "vcs_info": {"vcs": "git", "commit_id": "f00"}}
    monkeypatch.setattr(metadata, "distribution", _installed(record))
    assert source.repo_url() == "https://git.test/kit"


def test_a_path_install_of_plugins_ak_names_the_repo_two_up(home, monkeypatch):
    record = {"url": "file:///work/kit/plugins/ak", "dir_info": {"editable": True}}
    monkeypatch.setattr(metadata, "distribution", _installed(record))
    assert source.repo_url() == "/work/kit"


def test_a_path_install_elsewhere_says_nothing(home, monkeypatch):
    monkeypatch.setattr(metadata, "distribution", _installed({"url": "file:///work/other", "dir_info": {}}))
    assert source.repo_url() is None


def test_an_install_of_the_release_clone_itself_falls_through_to_its_origin(home, monkeypatch):
    clone = source.release_dir()
    (clone / ".git").mkdir(parents=True)
    _git_on_path(home, monkeypatch)
    monkeypatch.setattr(metadata, "distribution", _installed({"url": f"file://{clone}/plugins/ak", "dir_info": {}}))
    assert source.repo_url() == "https://example.test/kit.git"


def test_the_clones_origin_comes_before_the_local_checkout(home, monkeypatch):
    (source.release_dir() / ".git").mkdir(parents=True)
    (home / REPO_DIR / ".git").mkdir(parents=True)
    _git_on_path(home, monkeypatch)
    assert source.repo_url() == "https://example.test/kit.git"


def test_the_local_checkout_is_the_last_resort(home):
    (home / REPO_DIR / ".git").mkdir(parents=True)
    assert source.repo_url() == str(home / REPO_DIR)


def test_an_install_from_claude_codes_clone_names_the_url_it_was_cloned_from(home, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    clone = source.marketplace_clone("agentic-kit")
    (clone / ".git").mkdir(parents=True)
    _git_on_path(home, monkeypatch)
    monkeypatch.setattr(metadata, "distribution", _installed({"url": f"file://{clone}/plugins/ak", "dir_info": {"editable": True}}))
    assert source.repo_url() == "https://example.test/kit.git"


@pytest.mark.parametrize("where, by_claude, given", [
    ("https://github.com/proxify-dev/agentic-kit", True, "proxify-dev/agentic-kit"),
    ("https://github.com/proxify-dev/agentic-kit.git/", True, "proxify-dev/agentic-kit"),
    ("git+https://github.com/proxify-dev/agentic-kit", True, "proxify-dev/agentic-kit"),
    ("git@github.com:proxify-dev/agentic-kit.git", True, "proxify-dev/agentic-kit"),
    ("ssh://git@github.com/proxify-dev/agentic-kit.git", True, "proxify-dev/agentic-kit"),
    ("http://localhost:8080/kit.git", True, "http://localhost:8080/kit.git"),
    ("git+ssh://git@example.test/team/kit.git", True, "ssh://git@example.test/team/kit.git"),
    ("git@example.test:team/kit.git", True, "git@example.test:team/kit.git"),
    ("file:///srv/kit-src", False, None),       # `claude plugin marketplace add` refuses file://
    ("git+file:///srv/kit-src", False, None),
    ("/Users/me/agentic-kit", False, None),
    ("C:\\Users\\me\\agentic-kit", False, None),
    (None, False, None),
])
def test_a_url_claude_code_clones_and_what_it_is_given(where, by_claude, given):
    assert source.cloned_by_claude(where) is by_claude
    if by_claude:
        assert source.marketplace_source(where) == given


def test_a_marketplace_row_is_a_clone_of_the_url_whatever_the_spelling():
    url = "https://github.com/proxify-dev/agentic-kit"
    assert source.same_source({"source": "github", "repo": "proxify-dev/agentic-kit"}, url)
    assert source.same_source({"source": "git", "url": "https://github.com/proxify-dev/agentic-kit.git"}, url)
    assert not source.same_source({"source": "github", "repo": "someone/fork"}, url)
    assert not source.same_source({"source": "directory", "path": "/x"}, url)
    assert source.same_source({"source": "git", "url": "http://localhost:8080/kit.git"}, "http://localhost:8080/kit")
    assert source.settings_source("http://localhost:8080/kit.git") == {"source": "git", "url": "http://localhost:8080/kit.git"}
    assert source.url_of({"source": "github", "repo": "o/r"}) == "https://github.com/o/r"


def test_git_line_is_none_when_git_is_missing(home, monkeypatch):
    monkeypatch.setenv("PATH", str(home))
    assert source.git_line(home, "rev-parse", "HEAD") is None
    assert source.clone_origin(home) is None
