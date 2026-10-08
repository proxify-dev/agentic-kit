"""setup/backup.py: a run folder per `ak setup`, and undo that takes back only what the run did.

A tmp kit home and a tmp Claude home per test; the exec and clone kinds run a fake binary on PATH.
"""
import hashlib
import json
import os
import subprocess

import pytest

from ak.setup import backup, settings
from ak.setup.settings import ABSENT, SettingsEdit

HOOKS_KEY = ("env", "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("AK_HOME", str(tmp_path / "kit"))
    monkeypatch.delenv("AK_STORE", raising=False)
    (tmp_path / "claude").mkdir()
    return tmp_path


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def apply_settings(run, edits, path):
    """What the engine does for a settings step."""
    run.snapshot(path)
    p = settings.plan(edits, path)
    after_sha = settings.apply(p)
    run.record("settings", "settings", {"kind": "json_keys", "file": path, "changes": p.changes,
                                        "after_sha": after_sha})
    return p


def test_a_run_lives_under_the_kit_db_and_a_second_one_in_the_same_second_gets_a_suffix(home):
    a, b = backup.Run.new(), backup.Run.new()
    assert os.path.dirname(a.path) == backup.runs_dir() == str(home / "kit" / "db" / "setup")
    assert a.id != b.id and b.id.startswith(a.id[:15])
    for sub in ("backup", "logs"):
        assert os.path.isdir(os.path.join(a.path, sub))
    assert [r["id"] for r in backup.list_runs()][0] == max(a.id, b.id, key=lambda i: (i[:15], len(i)))


def test_snapshot_copies_a_file_once_and_notes_an_absent_one(home):
    f, gone = home / "rc", home / "nope"
    f.write_text("one\n")
    run = backup.Run.new()
    run.snapshot(str(f))
    f.write_text("two\n")
    run.snapshot(str(f))  # the second call keeps the first copy
    run.snapshot(str(gone))
    assert run.backed_up(str(f)) == b"one\n"
    assert run.snapshot_of(str(gone)) == {"absent": True, "sha": None}
    assert run.backed_up(str(gone)) is None


def test_record_appends_and_a_loaded_run_sees_it(home):
    run = backup.Run.new()
    run.record("a", "first", {"kind": "none", "note": "x"})
    run.record("b", "second", {"kind": "json_keys", "file": "f", "changes": [{"before": ABSENT}]}, status="failed")
    again = backup.Run.load(run.id[:12])  # a unique prefix is enough
    assert [s["id"] for s in again.steps()] == ["a", "b"]
    assert again.steps()[1]["undo"]["changes"][0]["before"] is ABSENT and again.steps()[1]["status"] == "failed"
    assert again.log_path("plugin:ak").endswith(os.path.join("logs", "plugin_ak.log"))


def test_settings_apply_then_undo_gives_identical_bytes(home):
    path = str(home / "claude" / "settings.json")
    original = json.dumps({"model": "opus", "env": {"FOO": "1"}, "hooks": {}}, indent=2) + "\n"
    open(path, "w").write(original)
    run = backup.Run.new()
    apply_settings(run, [SettingsEdit(HOOKS_KEY, "1"), SettingsEdit(("fileSuggestion",), {"type": "command"})], path)
    assert open(path).read() != original
    res = backup.undo()
    assert open(path, "rb").read() == original.encode()
    assert [r["ok"] for r in res] == [True]


def test_a_foreign_key_written_between_apply_and_undo_survives(home):
    path = str(home / "claude" / "settings.json")
    original = json.dumps({"env": {"FOO": "1"}}, indent=2) + "\n"
    open(path, "w").write(original)
    run = backup.Run.new()
    apply_settings(run, [SettingsEdit(HOOKS_KEY, "1"), SettingsEdit(("fileSuggestion",), "x")], path)
    data = json.load(open(path))
    data["foreign"] = {"x": 1}
    data["env"]["THEIRS"] = "y"
    open(path, "w").write(json.dumps(data, indent=2) + "\n")
    res = backup.undo()
    assert json.load(open(path)) == {"env": {"FOO": "1", "THEIRS": "y"}, "foreign": {"x": 1}}
    assert res[0]["ok"]


def test_a_value_the_person_changed_since_is_left_alone(home):
    path = str(home / "claude" / "settings.json")
    open(path, "w").write('{\n  "a": 1\n}\n')
    run = backup.Run.new()
    apply_settings(run, [SettingsEdit(("a",), 2)], path)
    open(path, "w").write('{\n  "a": 3\n}\n')
    res = backup.undo()
    assert json.load(open(path)) == {"a": 3} and "a" in res[0]["detail"]


def test_a_settings_file_the_run_made_is_removed_by_undo(home):
    path = str(home / "claude" / "settings.json")
    run = backup.Run.new()
    apply_settings(run, [SettingsEdit(HOOKS_KEY, "1")], path)
    assert os.path.exists(path)
    backup.undo()
    assert not os.path.exists(path)


def test_a_made_file_the_person_added_to_loses_only_our_keys(home):
    path = str(home / "claude" / "settings.json")
    run = backup.Run.new()
    apply_settings(run, [SettingsEdit(HOOKS_KEY, "1")], path)
    data = json.load(open(path))
    data["mine"] = True
    open(path, "w").write(json.dumps(data, indent=2) + "\n")
    backup.undo()
    assert json.load(open(path)) == {"mine": True}  # the env object we made is gone with its only key


def test_text_lines_undo_keeps_a_line_added_later(home):
    rc = home / ".zshrc"
    rc.write_text("export A=1\n")
    run = backup.Run.new()
    run.snapshot(str(rc))
    with open(rc, "a") as fh:
        fh.write('export PATH="$HOME/.local/bin:$PATH"\n')
    run.record("path", "put ~/.local/bin on PATH", {"kind": "text_lines", "file": str(rc),
                                                      "lines": ['export PATH="$HOME/.local/bin:$PATH"']})
    with open(rc, "a") as fh:
        fh.write("alias ll='ls -l'\n")
    res = backup.undo()
    assert rc.read_text() == "export A=1\nalias ll='ls -l'\n" and res[0]["ok"]


def test_text_lines_undo_of_a_line_already_gone_is_fine(home):
    rc = home / ".zshrc"
    rc.write_text("keep\n")
    run = backup.Run.new()
    run.record("p", "p", {"kind": "text_lines", "file": str(rc), "lines": ["gone"]})
    res = backup.undo()
    assert rc.read_text() == "keep\n" and res[0]["ok"]


def test_restore_puts_the_bytes_back_when_nobody_wrote_since(home):
    f = home / "conf"
    f.write_bytes(b"old\r\nbytes")
    run = backup.Run.new()
    run.snapshot(str(f))
    f.write_bytes(b"ours")
    run.record("w", "w", {"kind": "restore", "file": str(f), "after_sha": sha(f)})
    res = backup.undo()
    assert f.read_bytes() == b"old\r\nbytes" and res[0]["ok"]


def test_restore_of_a_file_we_created_deletes_it(home):
    f = home / "new"
    run = backup.Run.new()
    run.snapshot(str(f))
    f.write_text("ours")
    run.record("w", "w", {"kind": "restore", "file": str(f), "after_sha": sha(f)})
    assert backup.undo()[0]["ok"] and not f.exists()


def test_restore_refuses_when_the_sha_changed(home):
    f = home / "conf"
    f.write_text("old")
    run = backup.Run.new()
    run.snapshot(str(f))
    f.write_text("ours")
    run.record("w", "w", {"kind": "restore", "file": str(f), "after_sha": sha(f)})
    f.write_text("theirs")
    res = backup.undo()
    assert f.read_text() == "theirs"
    assert res[0]["ok"] is False and "changed since" in res[0]["detail"]


@pytest.fixture
def fake_bin(home, monkeypatch):
    b = home / "bin"
    b.mkdir()
    log = home / "calls.log"
    (b / "fake-claude").write_text('#!/bin/sh\necho "$* [$FAKE_MARK]" >> "$FAKE_LOG"\nexit ${FAKE_RC:-0}\n')
    (b / "fake-claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{b}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    return log


def test_exec_undo_runs_the_inverse_command_with_its_env(home, fake_bin):
    run = backup.Run.new()
    run.record("plugin:ak", "install ak@ak", {"kind": "exec", "argv": ["fake-claude", "plugin", "uninstall", "ak@ak"],
                                                  "env": {"FAKE_MARK": "hi"}})
    res = backup.undo()
    assert fake_bin.read_text() == "plugin uninstall ak@ak [hi]\n"
    assert res[0]["ok"] and "exit 0" in res[0]["detail"]


def test_exec_undo_removes_the_variables_it_names_from_the_environment(home, fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_MARK", "inherited")
    run = backup.Run.new()
    run.record("x", "x", {"kind": "exec", "argv": ["fake-claude", "x"], "unset": ["FAKE_MARK"]})
    backup.undo()
    assert fake_bin.read_text() == "x []\n"


def test_exec_undo_reports_a_failing_command(home, fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_RC", "3")
    run = backup.Run.new()
    run.record("x", "x", {"kind": "exec", "argv": ["fake-claude", "x"]})
    assert backup.undo()[0]["ok"] is False


def test_exec_undo_of_a_missing_binary_is_a_failure_not_a_crash(home):
    run = backup.Run.new()
    run.record("x", "x", {"kind": "exec", "argv": ["no-such-binary-ak"]})
    assert backup.undo()[0]["ok"] is False


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def make_clone(path):
    """A clone of a one-commit origin, so nothing in it is unpushed."""
    origin = path.parent / (path.name + "-origin")
    origin.mkdir()
    git("init", "-q", cwd=origin)
    (origin / "f").write_text("x")
    git("add", "f", cwd=origin)
    git("commit", "-qm", "c", cwd=origin)
    git("clone", "-q", str(origin), str(path), cwd=path.parent)


def test_clone_undo_removes_a_clean_clone(home):
    clone = home / "release"
    make_clone(clone)
    run = backup.Run.new()
    run.record("clone", "clone", {"kind": "clone", "path": str(clone)})
    assert backup.undo()[0]["ok"] and not clone.exists()


def test_clone_undo_refuses_a_dirty_clone(home):
    clone = home / "release"
    make_clone(clone)
    (clone / "mine.txt").write_text("work")
    run = backup.Run.new()
    run.record("clone", "clone", {"kind": "clone", "path": str(clone)})
    res = backup.undo()
    assert res[0]["ok"] is False and "left alone" in res[0]["detail"] and (clone / "mine.txt").exists()


def test_clone_undo_refuses_a_clone_with_a_commit_not_pushed(home):
    clone = home / "release"
    make_clone(clone)
    (clone / "f").write_text("mine")
    git("commit", "-qam", "mine", cwd=clone)
    run = backup.Run.new()
    run.record("clone", "clone", {"kind": "clone", "path": str(clone)})
    res = backup.undo()
    assert res[0]["ok"] is False and "not pushed" in res[0]["detail"] and clone.exists()


def test_clone_undo_refuses_a_clone_with_a_stash(home):
    clone = home / "release"
    make_clone(clone)
    (clone / "f").write_text("mine")
    git("stash", "-q", cwd=clone)
    run = backup.Run.new()
    run.record("clone", "clone", {"kind": "clone", "path": str(clone)})
    res = backup.undo()
    assert res[0]["ok"] is False and "stash" in res[0]["detail"] and clone.exists()


def test_a_refused_step_is_tried_again_and_the_taken_back_ones_are_not(home, fake_bin):
    clone = home / "release"
    make_clone(clone)
    (clone / "mine.txt").write_text("work")
    run = backup.Run.new()
    run.record("clone", "clone", {"kind": "clone", "path": str(clone)})
    run.record("x", "x", {"kind": "exec", "argv": ["fake-claude", "once"]})
    first = backup.undo()
    assert [(r["step"], r["ok"]) for r in first] == [("x", True), ("clone", False)]
    assert backup.list_runs()[0]["undone"] is False
    (clone / "mine.txt").unlink()  # the person cleaned up
    second = backup.undo()
    assert [(r["step"], r["ok"]) for r in second] == [("clone", True)] and not clone.exists()
    assert fake_bin.read_text().count("once") == 1
    assert backup.list_runs()[0]["undone"] is True and backup.undo() == []


def test_exec_undo_runs_in_the_cwd_it_was_given(home, monkeypatch):
    b = home / "bin"
    b.mkdir()
    (b / "where").write_text('#!/bin/sh\npwd -P >> "$FAKE_LOG"\n')
    (b / "where").chmod(0o755)
    monkeypatch.setenv("PATH", f"{b}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(home / "pwd.log"))
    (home / "elsewhere").mkdir()
    run = backup.Run.new()
    run.record("x", "x", {"kind": "exec", "argv": ["where"], "cwd": str(home / "elsewhere")})
    assert backup.undo()[0]["ok"]
    assert (home / "pwd.log").read_text().strip() == str((home / "elsewhere").resolve())


def test_a_symlinked_settings_file_stays_a_link_through_apply_and_undo(home):
    real = home / "dotfiles" / "settings.json"
    real.parent.mkdir()
    original = json.dumps({"model": "opus"}, indent=2) + "\n"
    real.write_text(original)
    link = home / "claude" / "settings.json"
    link.symlink_to(real)
    run = backup.Run.new()
    apply_settings(run, [SettingsEdit(("a",), 1)], str(link))
    assert link.is_symlink() and json.loads(real.read_text()) == {"model": "opus", "a": 1}
    # a foreign key forces the key-by-key path, which also writes through the link
    data = json.loads(real.read_text())
    data["foreign"] = 1
    real.write_text(json.dumps(data, indent=2) + "\n")
    backup.undo()
    assert link.is_symlink() and json.loads(real.read_text()) == {"model": "opus", "foreign": 1}


def test_restore_through_a_symlink_keeps_the_link(home):
    real = home / "real"
    real.write_text("old")
    link = home / "link"
    link.symlink_to(real)
    run = backup.Run.new()
    run.snapshot(str(link))
    real.write_text("ours")
    run.record("w", "w", {"kind": "restore", "file": str(link), "after_sha": sha(real)})
    assert backup.undo()[0]["ok"] and link.is_symlink() and real.read_text() == "old"


def test_text_lines_undo_through_a_symlink_keeps_the_link(home):
    real = home / "real"
    real.write_text("a\nours\n")
    link = home / "link"
    link.symlink_to(real)
    run = backup.Run.new()
    run.record("p", "p", {"kind": "text_lines", "file": str(link), "lines": ["ours"]})
    assert backup.undo()[0]["ok"] and link.is_symlink() and real.read_text() == "a\n"


def test_a_none_step_reports_its_note(home):
    run = backup.Run.new()
    run.record("venv", "warm the venv", {"kind": "none", "note": "a warmed venv stays"})
    res = backup.undo()
    assert res == [{"step": "venv", "action": "a warmed venv stays", "ok": True, "detail": "a warmed venv stays"}]


def test_steps_are_undone_newest_first(home, fake_bin):
    run = backup.Run.new()
    for n in ("one", "two", "three"):
        run.record(n, n, {"kind": "exec", "argv": ["fake-claude", n]})
    assert [r["step"] for r in backup.undo()] == ["three", "two", "one"]
    assert fake_bin.read_text().split() == ["three", "[]", "two", "[]", "one", "[]"]


def test_a_second_undo_is_a_no_op(home, fake_bin):
    run = backup.Run.new()
    run.record("x", "x", {"kind": "exec", "argv": ["fake-claude", "once"]})
    assert len(backup.undo()) == 1
    assert backup.undo() == [] and backup.undo(run.id) == []
    assert fake_bin.read_text().count("once") == 1
    assert backup.list_runs()[0]["undone"] is True


def test_undo_defaults_to_the_newest_run_not_yet_undone(home, fake_bin):
    old, new = backup.Run.new(), backup.Run.new()
    old.record("old", "old", {"kind": "exec", "argv": ["fake-claude", "old"]})
    new.record("new", "new", {"kind": "exec", "argv": ["fake-claude", "new"]})
    assert backup.undo()[0]["step"] == "new"
    assert backup.undo()[0]["step"] == "old"
    assert backup.undo() == []


def test_dry_run_reports_and_writes_nothing(home, fake_bin):
    path = str(home / "claude" / "settings.json")
    open(path, "w").write('{\n  "a": 1\n}\n')
    run = backup.Run.new()
    apply_settings(run, [SettingsEdit(("b",), 2)], path)
    run.record("x", "x", {"kind": "exec", "argv": ["fake-claude", "never"]})
    clone = home / "release"
    make_clone(clone)
    run.record("clone", "clone", {"kind": "clone", "path": str(clone)})
    before = (open(path, "rb").read(), open(run._file(), "rb").read())
    res = backup.undo(dry_run=True)
    assert [r["step"] for r in res] == ["clone", "x", "settings"] and all(r["ok"] for r in res)
    assert all(r["action"] for r in res)
    assert (open(path, "rb").read(), open(run._file(), "rb").read()) == before
    assert not fake_bin.exists() and clone.exists()
    assert backup.list_runs()[0]["undone"] is False


def test_loading_an_unknown_run_says_so(home):
    with pytest.raises(FileNotFoundError):
        backup.undo("19990101-000000")
    assert backup.undo() == []  # no runs at all is quiet
