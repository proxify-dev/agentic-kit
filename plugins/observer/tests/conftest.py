"""Every test starts as a person at a terminal would: no entrypoint, no capture override.
The suite itself often runs inside a Claude Code session (CLAUDE_CODE_ENTRYPOINT=cli) or
under `claude -p` (sdk-cli) — neither may decide what a hook under test does."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from _brand import env_name  # noqa: E402


@pytest.fixture(autouse=True)
def _no_session_origin(monkeypatch):
    # CLAUDE_CONFIG_DIR too: a test that points HOME at a tmp dir must not read the real config dir;
    # and the kit home's variable, which outranks the vault's: with it set, a test pinning only the
    # vault would still write the real kit's db/ and cache/
    for var in ("CLAUDE_CODE_ENTRYPOINT", env_name("CAPTURE"), env_name("CAPTURE_DISABLE"), "CLAUDE_CONFIG_DIR",
                env_name("HOME")):
        monkeypatch.delenv(var, raising=False)
