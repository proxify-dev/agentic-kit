"""setup/settings.py: set keys in the user's settings.json, keep everything else, diff first.

Every test points the Claude home at a tmp folder: nothing here touches the person's real settings.
"""
import json
import os

import pytest

from ak.setup import settings
from ak.setup.settings import ABSENT, SettingsEdit

HOOKS_KEY = ("env", "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    (tmp_path / "claude").mkdir()
    return tmp_path / "claude"


def put(home, text):
    (home / "settings.json").write_text(text)
    return str(home / "settings.json")


def test_the_path_is_under_the_claude_home(home):
    assert settings.settings_path() == str(home / "settings.json")


def test_unknown_keys_and_their_order_survive(home):
    path = put(home, json.dumps({"zeta": 1, "model": "opus", "hooks": {"Stop": []}, "alpha": [1, 2]}, indent=2) + "\n")
    p = settings.plan([SettingsEdit(("fileSuggestion",), {"type": "command", "command": "x"})], path)
    settings.apply(p)
    out = json.loads(open(path).read())
    assert list(out) == ["zeta", "model", "hooks", "alpha", "fileSuggestion"]
    assert out["hooks"] == {"Stop": []} and out["alpha"] == [1, 2]


def test_env_merges_with_the_existing_env(home):
    path = put(home, json.dumps({"env": {"FOO": "1"}}, indent=2) + "\n")
    p = settings.plan([SettingsEdit(HOOKS_KEY, "1")], path)
    assert p.changes == [{"keypath": list(HOOKS_KEY), "before": ABSENT, "after": "1", "created": 0}]
    settings.apply(p)
    assert json.loads(open(path).read())["env"] == {"FOO": "1", "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"}


def test_the_value_it_already_has_is_no_change(home):
    path = put(home, json.dumps({"env": {"A": "1"}, "n": 1}, indent=2) + "\n")
    p = settings.plan([SettingsEdit(("env", "A"), "1")], path)
    assert p.empty and p.diff == "" and p.after_text == p.before_text
    assert not settings.plan([SettingsEdit(("n",), True)], path).empty  # 1 is not True


def test_a_non_object_on_the_way_down_is_refused_naming_the_keypath(home):
    path = put(home, json.dumps({"env": "oops"}) + "\n")
    with pytest.raises(ValueError, match=r"env\.CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"):
        settings.plan([SettingsEdit(HOOKS_KEY, "1")], path)


def test_a_four_space_file_stays_four_space(home):
    path = put(home, json.dumps({"a": {"b": 1}}, indent=4) + "\n")
    p = settings.plan([SettingsEdit(("c",), 2)], path)
    assert p.after_text == json.dumps({"a": {"b": 1}, "c": 2}, indent=4) + "\n"
    settings.apply(p)
    assert open(path).read() == p.after_text


def test_a_tab_indented_file_and_a_missing_final_newline_are_kept(home):
    path = put(home, json.dumps({"a": 1}, indent="\t"))
    p = settings.plan([SettingsEdit(("b",), 2)], path)
    assert p.after_text == json.dumps({"a": 1, "b": 2}, indent="\t")


def test_a_new_file_is_two_spaces_with_a_newline(home):
    p = settings.plan([SettingsEdit(HOOKS_KEY, "1")])
    assert p.before_text == "" and not os.path.exists(p.path)
    assert p.after_text == '{\n  "env": {\n    "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"\n  }\n}\n'
    assert p.changes[0]["created"] == 1
    sha = settings.apply(p)
    assert open(p.path).read() == p.after_text
    import hashlib
    assert sha == hashlib.sha256(p.after_text.encode()).hexdigest()


def test_plan_writes_nothing_and_the_diff_names_the_file(home):
    path = put(home, '{\n  "a": 1\n}\n')
    p = settings.plan([SettingsEdit(("b",), 2)], path)
    assert open(path).read() == '{\n  "a": 1\n}\n'
    assert f"--- {path}" in p.diff and f"+++ {path}" in p.diff and '+  "b": 2' in p.diff


def test_apply_keeps_a_key_another_writer_added_since_plan(home):
    path = put(home, json.dumps({"a": 1}, indent=2) + "\n")
    p = settings.plan([SettingsEdit(HOOKS_KEY, "1")], path)
    put(home, json.dumps({"a": 1, "foreign": {"x": 1}, "env": {"THEIRS": "y"}}, indent=2) + "\n")
    settings.apply(p)
    out = json.loads(open(path).read())
    assert out["foreign"] == {"x": 1}
    assert out["env"] == {"THEIRS": "y", "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"}
    assert p.changes[0]["before"] is ABSENT


def test_apply_records_what_was_really_there_as_before(home):
    path = put(home, json.dumps({"x": 1}) + "\n")
    p = settings.plan([SettingsEdit(("x",), 2)], path)
    put(home, json.dumps({"x": 5}) + "\n")
    settings.apply(p)
    assert p.changes[0]["before"] == 5


def test_the_absent_marker_round_trips_through_json():
    doc = {"changes": [{"before": ABSENT, "after": 1}, {"before": {"k": ABSENT}, "after": None}]}
    back = settings.loads(settings.dumps(doc))
    assert back["changes"][0]["before"] is ABSENT and back["changes"][1]["before"] == {"k": ABSENT}


def test_a_file_that_is_not_an_object_is_refused(home):
    path = put(home, "[1, 2]\n")
    with pytest.raises(ValueError):
        settings.plan([SettingsEdit(("a",), 1)], path)


def test_apply_leaves_a_key_someone_set_since_the_preview_and_says_so(home):
    path = put(home, json.dumps({"a": 1}, indent=2) + "\n")
    p = settings.plan([SettingsEdit(HOOKS_KEY, "1"), SettingsEdit(("b",), 2)], path)
    put(home, json.dumps({"a": 1, "env": {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "theirs"}}, indent=2) + "\n")
    settings.apply(p)
    out = json.loads(open(path).read())
    assert out["env"] == {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "theirs"} and out["b"] == 2
    assert p.skipped == [{"keypath": list(HOOKS_KEY), "found": "theirs"}]
    assert [c["keypath"] for c in p.changes] == [["b"]]


def test_a_skipped_key_does_not_leave_an_empty_object_behind(home):
    path = put(home, json.dumps({"x": {"y": "theirs"}}, indent=2) + "\n")
    p = settings.plan([SettingsEdit(("x", "y"), "ours")], path)
    os.remove(path)
    put(home, json.dumps({"x": {"y": "theirs"}}, indent=2) + "\n")
    p2 = settings.plan([SettingsEdit(("n", "m"), 1)], path)
    put(home, json.dumps({"n": {"m": 2}}, indent=2) + "\n")
    settings.apply(p2)
    assert json.loads(open(path).read()) == {"n": {"m": 2}} and p2.empty and len(p2.skipped) == 1


def test_a_key_another_step_will_write_is_context_in_the_diff_not_a_change(home):
    path = put(home, '{\n  "theme": "dark"\n}\n')
    entry = ("extraKnownMarketplaces", "agentic-kit", "source")
    key = ("extraKnownMarketplaces", "agentic-kit", "autoUpdate")
    p = settings.plan([SettingsEdit(key, True)], assume=[SettingsEdit(entry, {"source": "github", "repo": "o/r"})])
    added = [ln for ln in p.diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    assert added == ['+      },', '+      "autoUpdate": true'], p.diff
    assert [c["keypath"] for c in p.changes] == [list(key)]
    assert json.loads(open(path).read()) == {"theme": "dark"}  # a preview: nothing written
    assert not settings.holds(key[:2]) and settings.holds(("theme",))


def test_apply_writes_through_a_symlinked_settings_file(home, tmp_path):
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text('{\n  "a": 1\n}\n')
    link = home / "settings.json"
    link.symlink_to(real)
    settings.apply(settings.plan([SettingsEdit(("b",), 2)], str(link)))
    assert link.is_symlink() and json.loads(real.read_text()) == {"a": 1, "b": 2}
