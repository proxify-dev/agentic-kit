"""A session a program started is not a person's: the observer does not record it and the
SessionStart digest does not greet it. CLAUDE_CODE_ENTRYPOINT says who started it (cli ·
claude-desktop · sdk-ts are people; sdk-cli is `claude -p`, sdk-py the Python SDK);
AK_CAPTURE=1 counts an automated session as a person's; AK_CAPTURE_DISABLE wins over all."""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN / "src"))
from _brand import HOME_DIR, env_name  # noqa: E402
import capture_common as cc  # noqa: E402
import store_common as sc  # noqa: E402
from observer import capture_flag  # noqa: E402


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    (v / "cache" / "sessions").mkdir(parents=True)
    (v / "projects").mkdir()
    monkeypatch.setenv(env_name("PATH"), str(v))
    monkeypatch.setenv(env_name("STORE"), str(tmp_path / "store"))
    monkeypatch.setenv("CLAUDE_MEM_DB", str(tmp_path / "absent.db"))
    return v


CASES = [
    # env                                                                  recorded?
    ({}, True),                                                            # cannot place it: as before
    ({"CLAUDE_CODE_ENTRYPOINT": "cli"}, True),
    ({"CLAUDE_CODE_ENTRYPOINT": "claude-desktop"}, True),
    ({"CLAUDE_CODE_ENTRYPOINT": "sdk-ts"}, True),                          # a desktop chat app
    ({"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}, False),                        # claude -p
    ({"CLAUDE_CODE_ENTRYPOINT": "sdk-py"}, False),                         # the Python SDK
    ({"CLAUDE_CODE_ENTRYPOINT": "sdk-cli", env_name("CAPTURE"): "1"}, True),   # asked to be remembered
    ({"CLAUDE_CODE_ENTRYPOINT": "cli", env_name("CAPTURE_DISABLE"): "1"}, False),
    ({"CLAUDE_CODE_ENTRYPOINT": "sdk-cli", env_name("CAPTURE"): "1", env_name("CAPTURE_DISABLE"): "1"}, False),
]


@pytest.mark.parametrize("env,recorded", CASES)
def test_the_hooks_and_the_cli_agree_on_who_is_recorded(vault, monkeypatch, env, recorded):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    hook, cli = cc.capture_state(str(vault)), capture_flag.state(str(vault))
    assert hook[0] is recorded and cli[0] is recorded
    if env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk-") and not recorded and env_name("CAPTURE_DISABLE") not in env:
        assert "automated session" in hook[1] and f"{env_name('CAPTURE')}=1" in hook[1]
    assert hook[1] == cli[1]


def test_the_hooks_and_the_cli_read_one_dial(vault, tmp_path):
    """`observer mute` writes the switch the hooks read: the kit's db/capture.json, from any cwd —
    never a file in whichever scope the CLI happened to run in."""
    repo = tmp_path / "repo"
    (repo / HOME_DIR).mkdir(parents=True)
    (repo / HOME_DIR / "vault-manifest.json").write_text("{}", encoding="utf-8")
    assert cc.capture_config_path(str(repo)) == capture_flag.config_path() == tmp_path / "store" / "capture.json"
    capture_flag.set_global(False)
    assert cc.capture_state(str(repo))[0] is False and capture_flag.state(str(repo))[0] is False


def _start(vault, monkeypatch, capsys, tmp_path):
    sc.init()
    conn = sc.connect()
    conn.execute("INSERT INTO observations(memory_session_id, project, type, title, narrative, facts, concepts, "
                 "created_at, created_at_epoch) VALUES('m','repo','discovery','a row the digest would show',"
                 "'', '[]', '[]', '2026-09-17T00:00:00Z', 1)")
    conn.commit()
    conn.close()
    cwd = tmp_path / "repo"
    cwd.mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location("session_start", PLUGIN / "hooks" / "session-start.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(cwd), "source": "startup"})))
    mod.main()
    mod.flush_stamps()
    return mod, json.loads(capsys.readouterr().out)


def test_an_automated_session_gets_the_doors_line_but_no_digest(vault, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
    mod, out = _start(vault, monkeypatch, capsys, tmp_path)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert ctx == mod.TEACH
    assert "no digest — an automated session (sdk-cli)" in out["systemMessage"]


def test_brain_capture_gives_an_automated_session_the_digest(vault, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
    monkeypatch.setenv(env_name("CAPTURE"), "1")
    _, out = _start(vault, monkeypatch, capsys, tmp_path)
    assert "a row the digest would show" in out["systemMessage"]


def test_a_person_still_gets_the_digest(vault, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    _, out = _start(vault, monkeypatch, capsys, tmp_path)
    assert "a row the digest would show" in out["systemMessage"]


def test_the_model_gets_the_doors_line_never_the_rows(vault, monkeypatch, capsys, tmp_path):
    """The observer's rows are raw and unverified: the person sees them, the model searches
    them. Its context carries the TEACH line and not one row."""
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    mod, out = _start(vault, monkeypatch, capsys, tmp_path)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert ctx == mod.TEACH
    assert "a row the digest would show" not in ctx
