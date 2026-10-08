"""One guard over the whole run: no test leaves state in the person's real global kit.

Since session 38ad5a26 a scope's index, its harvest cursor and names.json live under the global kit
(index.state_dir), never inside the project. A test that builds one in-process without pointing HOME
at its sandbox writes into ~/.ak instead — three did (the state folder of the day), the day the index
moved, and one did again the day the home went to layout 2. So the run starts with $AK_HOME pointed at
a throwaway kit (a test that wants another sets its own), and at the end the real kit's vault/, db/
and cache/ are checked: a sandbox's entry or registry row there fails the run and is taken back out; one
of the three that appeared during the run fails it and is left for a person to look at — a live
session or a migration may have planted it, and its contents are not the tests' to delete.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from ak._brand import HOME_DIR, env_name

KIT = Path(os.path.expanduser("~")).resolve() / HOME_DIR
VAULT, DB, CACHE = KIT / "vault", KIT / "db", KIT / "cache"
REGISTRIES = (CACHE / "names.json", DB / "workspaces.json")
MARK = "pytest-of-"

SANDBOX = os.environ.setdefault(env_name("HOME"), tempfile.mkdtemp(prefix="ak-test-kit-"))


def _left() -> list:
    out = []
    for root in (VAULT, DB, CACHE):
        for d, dirs, files in os.walk(root):
            dirs[:] = [n for n in dirs if n != ".git"] if len(Path(d).relative_to(root).parts) < 3 else []
            out += [Path(d) / n for n in dirs + files if MARK in n]
    for registry in REGISTRIES:
        try:
            if MARK in registry.read_text(encoding="utf-8"):
                out.append(registry)
        except OSError:
            pass
    return out


def _planted() -> set:
    return {p for p in (VAULT, DB, CACHE) if p.exists()}


def _take_back(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
        return
    if p not in REGISTRIES:
        p.unlink(missing_ok=True)
        return
    data = json.loads(p.read_text(encoding="utf-8"))  # drop the sandboxes' scopes, keep the person's
    scopes = data.get("scopes") or {}
    if isinstance(scopes, dict):  # names.json: path → entry
        data["scopes"] = {k: v for k, v in scopes.items() if MARK not in k}
    else:  # workspaces.json: a list of {path, name}
        data["scopes"] = [x for x in scopes if MARK not in json.dumps(x)]
    p.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture(autouse=True, scope="session")
def _no_state_in_the_real_brain():
    before, there = set(_left()), _planted()
    yield
    leaked = [p for p in _left() if p not in before]
    for p in leaked:
        _take_back(p)
    appeared = sorted(str(p) for p in _planted() - there)
    if Path(SANDBOX).name.startswith("ak-test-kit-"):
        shutil.rmtree(SANDBOX, ignore_errors=True)
    assert not leaked, f"tests wrote generated state into the real global kit: {leaked}"
    assert not appeared, f"these appeared in the real global kit during the run — look before removing: {appeared}"
