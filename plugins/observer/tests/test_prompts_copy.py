"""The prompts copy: the summarizer copies a session's prompts into user_prompts only while the
tracer plugin is off (it reads them from Claude Code's transcripts), unless capture.json pins
`"prompts": "on" | "off"` (`observer prompts on|off|auto`). The tracer counts as on when the last
of Claude Code's settings files naming it, under any marketplace, enables it."""
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN / "src"))
from _brand import env_name  # noqa: E402
import capture_common as cc  # noqa: E402
from observer import capture_flag  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / "claude").mkdir()
    (tmp_path / "repo").mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv(env_name("STORE"), str(tmp_path / "store"))
    return tmp_path


def _settings(path: Path, plugins: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabledPlugins": plugins}), encoding="utf-8")


@pytest.mark.parametrize("user, local, on", [
    (None, None, False),                                    # named nowhere
    ({"tracer@ak": True}, None, True),
    ({"tracer@agentic-kit": True}, None, True),             # any marketplace
    ({"tracer@ak": False}, None, False),
    ({"tracer@ak": True}, {"tracer@ak": False}, False),     # the project's local file wins
    (None, {"tracer@ak": True}, True),
    ({"tracer@ak": True}, {"observer@ak": True}, True),     # a file not naming it changes nothing
])
def test_the_tracer_is_on_by_the_last_settings_file_naming_it(home, user, local, on):
    repo = home / "repo"
    if user is not None:
        _settings(home / "claude" / "settings.json", user)
    if local is not None:
        _settings(repo / ".claude" / "settings.local.json", local)
    assert cc.plugin_enabled("tracer", str(repo)) is on


@pytest.mark.parametrize("cfg, tracer, stored", [
    ({}, False, True),
    ({}, True, False),
    ({"prompts": "on"}, True, True),
    ({"prompts": "off"}, False, False),
])
def test_the_setting_wins_over_the_tracer(home, cfg, tracer, stored):
    if tracer:
        _settings(home / "claude" / "settings.json", {"tracer@ak": True})
    ok, why = cc.prompts_state(cfg, str(home / "repo"))
    assert ok is stored and why


def test_observer_prompts_writes_the_dial_the_summarizer_reads(home):
    """`observer prompts off|on|auto` sets capture.json's key; auto removes it. The hooks' store_prompts
    and the CLI's prompts() read the same file through the same rule."""
    from observer import cli
    repo = str(home / "repo")
    _settings(home / "claude" / "settings.json", {"tracer@ak": True})
    run = CliRunner().invoke
    assert run(cli.observer, ["prompts", "on"]).exit_code == 0
    assert json.loads((home / "store" / "capture.json").read_text())["prompts"] == "on"
    assert cc.store_prompts(repo) is True and capture_flag.prompts(repo)[::2] == (True, "on")
    assert run(cli.observer, ["prompts", "auto"]).exit_code == 0
    assert "prompts" not in json.loads((home / "store" / "capture.json").read_text())
    assert cc.store_prompts(repo) is False and capture_flag.prompts(repo)[::2] == (False, "auto")
    assert run(cli.observer, ["prompts", "maybe"]).exit_code != 0
