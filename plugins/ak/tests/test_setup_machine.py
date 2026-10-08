"""setup/machine.py: detect() against fake binaries on PATH — versions parsed, claude run outside a session,
and every probe that fails comes back as 'not found' rather than an exception."""
import json
import os
from pathlib import Path

import pytest

from ak._brand import GATEWAY_ACCOUNTS, config_dir
from ak.setup import machine

PLUGINS = [
    {"id": "ak@ak", "version": "3.0.0", "scope": "project", "enabled": False},
    {"id": "ak@ak", "version": "3.0.0", "scope": "user", "enabled": True},
    {"id": "other@x", "version": "1", "scope": "local", "enabled": False},
]
MARKETS = [
    {"name": "ak", "source": "directory", "path": "/rel", "installLocation": "/rel"},
    {"name": "gh", "source": "github", "repo": "o/r", "installLocation": "/x"},
]

FAKE_CLAUDE = """#!/bin/sh
show() { while IFS= read -r line || [ -n "$line" ]; do echo "$line"; done < "$1"; }
echo "$(pwd -P)|${CLAUDECODE-unset}|${CC_SHIM_DISABLE-unset}" >> "$CLAUDE_LOG"
case "$*" in
  --version) echo "2.1.286 (Claude Code)" ;;
  "plugin list --json") show "$FIXTURES/plugins.json" ;;
  "plugin marketplace list --json") show "$FIXTURES/markets.json" ;;
  *) exit 2 ;;
esac
"""
FAKE_UV = """#!/bin/sh
case "$*" in
  --version) echo "uv 0.7.20 (abc123 2025-01-01)" ;;
  "tool list") printf 'ak v2.0.0\\n- ak\\nwarning: Ignoring malformed tool `x`\\nruff v0.12.3\\n- ruff\\n' ;;
esac
"""
FAKE_GIT = """#!/bin/sh
case "$*" in
  --version) echo "git version 2.50.1 (Apple Git-155)" ;;
  *"rev-parse --short HEAD"*) echo "9a86fc0f" ;;
  *"remote get-url origin"*) echo "/src/kit" ;;
  *) exit 1 ;;
esac
"""


def script(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A HOME with fake uv, git, node, tmux, claude on a PATH of their own (the scripts use only builtins)."""
    home, bin_, fixtures = tmp_path / "home", tmp_path / "bin", tmp_path / "fixtures"
    for d in (home, bin_, fixtures):
        d.mkdir()
    (fixtures / "plugins.json").write_text(json.dumps(PLUGINS))
    (fixtures / "markets.json").write_text(json.dumps(MARKETS))
    script(bin_ / "claude", FAKE_CLAUDE)
    script(bin_ / "uv", FAKE_UV)
    script(bin_ / "git", FAKE_GIT)
    script(bin_ / "node", '#!/bin/sh\necho v22.13.1\n')
    script(bin_ / "tmux", '#!/bin/sh\n[ "$1" = "-V" ] && echo "tmux next-3.6"\n')
    script(bin_ / "osascript", '#!/bin/sh\nexit 1\n')
    for k in ("CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(bin_))
    monkeypatch.setenv("FIXTURES", str(fixtures))
    monkeypatch.setenv("CLAUDE_LOG", str(tmp_path / "claude.log"))
    monkeypatch.setenv("AK_GATEWAY_PORT", "1")
    monkeypatch.setenv("CLAUDECODE", "1")
    return {"home": home, "bin": bin_, "log": tmp_path / "claude.log"}


def test_versions_are_digits_and_dots(box):
    m = machine.detect()
    assert {n: m.bin(n).version for n in ("uv", "git", "claude", "node", "tmux")} == {
        "uv": "0.7.20", "git": "2.50.1", "claude": "2.1.286", "node": "22.13.1", "tmux": "3.6"}
    assert m.bin("osascript").found and m.bin("osascript").version is None
    assert not m.bin("swiftc").found and not m.bin("obsidian").found


def test_claude_is_run_from_home_outside_a_session_past_the_shim(box):
    m = machine.detect()
    assert m.in_claude
    here = os.path.realpath(box["home"])
    rows = [line.split("|") for line in box["log"].read_text().splitlines()]
    assert rows and all(row == [here, "unset", "1"] for row in rows)


def test_plugins_and_marketplaces_come_from_claudes_json(box):
    m = machine.detect()
    assert m.plugins["ak@ak"] == {"version": "3.0.0", "enabled": True, "scope": "user"}
    assert m.plugins["other@x"]["scope"] == "local"
    assert m.marketplaces == {"ak": "/rel", "gh": "o/r"}


def test_uv_tools_are_package_names(box):
    assert machine.detect().uv_tools == ["ak", "ruff"]


def test_the_release_clone_is_read_without_a_fetch(box):
    rel = machine.source.release_dir()
    (rel / ".git").mkdir(parents=True)
    m = machine.detect()
    assert (m.release.path, m.release.exists, m.release.head, m.release.origin) == (str(rel), True, "9a86fc0f", "/src/kit")


def test_no_release_clone_is_not_an_error(box):
    r = machine.detect().release
    assert r.exists is False and r.head is None and r.origin is None


def test_path_facts(box, monkeypatch):
    m = machine.detect()
    assert not m.ak_on_path and not m.local_bin_on_path
    local_bin = box["home"] / ".local" / "bin"
    local_bin.mkdir(parents=True)
    script(local_bin / "ak", "#!/bin/sh\n")
    monkeypatch.setenv("PATH", f"{box['bin']}{os.pathsep}{local_bin}")
    m = machine.detect()
    assert m.ak_on_path and m.local_bin_on_path


def test_settings_and_gateway_accounts(box):
    claude = box["home"] / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text('{"env": {"A": "1"}, "model": "sonnet"}')
    accounts = Path(config_dir()) / GATEWAY_ACCOUNTS
    accounts.parent.mkdir(parents=True)
    accounts.write_text("{}")
    m = machine.detect()
    assert m.settings == {"env": {"A": "1"}, "model": "sonnet"}
    assert m.gateway["accounts"] is True and m.gateway["on"] is False


def test_a_broken_settings_file_is_empty_settings(box):
    (box["home"] / ".claude").mkdir()
    (box["home"] / ".claude" / "settings.json").write_text("{not json")
    assert machine.detect().settings == {}


def test_the_shim_is_asked_for_its_status(box):
    mjs = Path(machine.data_dir()) / "cc-shim" / "cc-shim.mjs"
    mjs.parent.mkdir(parents=True)
    mjs.write_text("")
    script(box["bin"] / "node", '#!/bin/sh\n[ "$2 $3" = "status --json" ] && echo \'{"present": true, "wired": [".zshenv"]}\'\n')
    assert machine.detect().shim == {"present": True, "wired": [".zshenv"]}


def test_missing_binaries_are_not_found_and_nothing_raises(box, monkeypatch):
    monkeypatch.setenv("PATH", str(box["home"]))  # an empty folder
    m = machine.detect()
    assert all(not b.found for b in m.bins.values()) and set(m.bins) == set(machine.binaries())
    assert (m.plugins, m.marketplaces, m.uv_tools, m.shim) == ({}, {}, [], {})
    assert not m.ak_on_path and m.release.exists is False


def test_a_probe_that_hangs_times_out_to_not_found(box, monkeypatch):
    script(box["bin"] / "claude", "#!/bin/sh\nexec sleep 5\n")
    monkeypatch.setenv("PATH", f"{box['bin']}:/bin:/usr/bin")
    monkeypatch.setattr(machine, "PROBE_TIMEOUT", 0.3)
    m = machine.detect()
    assert m.plugins == {} and m.marketplaces == {} and m.bin("claude").version is None


def test_a_claude_that_answers_nonsense_is_no_plugins(box):
    (Path(os.environ["FIXTURES"]) / "plugins.json").write_text("<html>")
    (Path(os.environ["FIXTURES"]) / "markets.json").write_text('{"not": "a list"}')
    m = machine.detect()
    assert m.plugins == {} and m.marketplaces == {}


@pytest.mark.parametrize("windows, macos, proc, expected", [
    (True, False, "", "windows"),
    (False, True, "", "macos"),
    (False, False, "Linux version 5.15.0-microsoft-standard-WSL2", "wsl"),
    (False, False, "Linux version 6.8.0-generic", "linux"),
])
def test_os_names(monkeypatch, windows, macos, proc, expected):
    monkeypatch.setattr(machine, "is_windows", lambda: windows)
    monkeypatch.setattr(machine, "is_macos", lambda: macos)
    monkeypatch.setattr(machine, "_proc_version", lambda: proc.lower())
    assert machine._os_name() == expected


def test_service_managers(box, monkeypatch):
    assert machine._service_manager("macos") == "launchd"
    assert machine._service_manager("windows") == "schtasks"
    assert machine._service_manager("linux") is None  # no systemctl on this PATH
    script(box["bin"] / "systemctl", "#!/bin/sh\nexit 0\n")
    assert machine._service_manager("linux") == "systemd"
    script(box["bin"] / "systemctl", "#!/bin/sh\nexit 1\n")  # "Failed to connect to bus"
    assert machine._service_manager("wsl") is None


def test_detect_never_raises_even_when_a_probe_blows_up(box, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(machine, "_plugins", boom)
    monkeypatch.setattr(machine, "_release", boom)
    m = machine.detect()
    assert m.plugins == {} and m.release.exists is False


def test_on_probe_hears_every_probe_once_and_a_raising_callback_breaks_nothing(box):
    heard = {}
    m = machine.detect(on_probe=lambda name, result: heard.setdefault(name, result))
    assert set(heard) == set(machine.binaries()) | {"service_manager", "release", "marketplaces", "plugins",
                                                  "uv_tools", "shim", "gateway", "settings", "history"}
    assert heard["plugins"] == m.plugins and heard["uv"].version == "0.7.20"

    def raises(name, result):
        raise RuntimeError(name)
    assert machine.detect(on_probe=raises).plugins == m.plugins


def test_a_claude_that_fails_or_hangs_is_unknown_not_empty(box, monkeypatch):
    script(box["bin"] / "claude", "#!/bin/sh\nexit 2\n")
    assert {"plugins", "marketplaces"} <= machine.detect().unknown
    script(box["bin"] / "claude", "#!/bin/sh\nexec sleep 5\n")
    monkeypatch.setenv("PATH", f"{box['bin']}:/bin:/usr/bin")
    monkeypatch.setattr(machine, "PROBE_TIMEOUT", 0.2)
    assert {"plugins", "marketplaces"} <= machine.detect().unknown


def test_a_claude_that_answers_nonsense_is_unknown(box):
    (Path(os.environ["FIXTURES"]) / "plugins.json").write_text("<html>")
    assert "plugins" in machine.detect().unknown


def test_a_claude_that_is_not_installed_holds_nothing_and_that_is_known(box, monkeypatch):
    (box["bin"] / "claude").unlink()
    m = machine.detect()
    assert m.plugins == {} and not ({"plugins", "marketplaces"} & m.unknown)


def test_a_good_machine_has_no_unknown_probe(box):
    assert machine.detect().unknown == frozenset()


def test_uv_tools_know_what_executables_they_provide(box):
    assert machine.detect().uv_tool_exes == {"ak": ["ak"], "ruff": ["ruff"]}


def test_a_uv_that_will_not_list_is_unknown(box):
    script(box["bin"] / "uv", '#!/bin/sh\n[ "$1" = --version ] && echo "uv 0.7.20"\n[ "$1" = tool ] && exit 1\nexit 0\n')
    m = machine.detect()
    assert "uv_tools" in m.unknown and m.uv_tools == []


def test_a_settings_file_that_is_there_but_broken_is_unknown_and_a_missing_one_is_not(box):
    assert "settings" not in machine.detect().unknown
    (box["home"] / ".claude").mkdir()
    (box["home"] / ".claude" / "settings.json").write_text("{not json")
    assert "settings" in machine.detect().unknown


def test_the_older_bash_shim_is_present_and_legacy(box):
    shim = Path(machine.data_dir()) / "cc-shim"
    shim.mkdir(parents=True)
    for name in ("claude", "cc-shim", "env", "_brand.sh"):
        (shim / name).write_text("#!/usr/bin/env bash\n")
    m = machine.detect()
    assert m.shim == {"present": True, "legacy": True} and "shim" not in m.unknown


def test_history_is_the_size_of_the_transcripts_and_their_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert machine._history() is None, "Claude Code never ran: no projects folder"
    project = tmp_path / "projects" / "-srv-app"
    (project / "a" / "subagents").mkdir(parents=True)
    (project / "a.jsonl").write_text("x" * 10)
    (project / "b.jsonl").write_text("x" * 20)
    (project / "a" / "subagents" / "s.jsonl").write_text("x" * 5)  # a subagent: bytes, not a session
    (project / "a" / "tool-results.txt").write_text("x" * 99)       # not a transcript
    assert machine._history() == (35, 2)


def test_only_what_the_kit_needs_is_looked_for():
    from ak.setup import catalog
    names = machine.binaries()
    assert names[:3] == ("uv", "git", "claude"), "setup's own tools first"
    needed = {n for c in catalog.COMPONENTS for n, _ in c.needs.bins}
    assert set(names) == {"uv", "git", "claude"} | needed
    assert "obsidian" not in names and "swiftc" not in names, "nothing in the kit runs on them: never 'missing'"
