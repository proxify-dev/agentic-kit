"""The copies seam (scripts/copies.py): a file more than one component needs lives once, and every other
place carries a stamped copy. `check` is the drift guard for every copy in the kit (it replaced the
per-plugin test_mirrors.py files); the rest proves each failure `check` reports, on a throwaway tree.
"""
import ast
import importlib.util
import itertools
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "copies.py"
if not SCRIPT.is_file():  # ak exported alone (the public kit) carries its copies, not the seam that writes them
    pytest.skip("the copies seam is the workspace's: no scripts/copies.py above this plugin", allow_module_level=True)


def _load():
    spec = importlib.util.spec_from_file_location("copies_seam_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


copies = _load()


def test_every_copy_is_current():
    r = subprocess.run([sys.executable, str(SCRIPT), "check"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_copy_names_a_canonical_file_that_is_not_itself_a_copy():
    pairs = copies.copies(ROOT)
    for dest, src in pairs.items():
        assert (ROOT / src).is_file(), f"{dest}: no canonical {src}"
        assert src not in pairs, f"{src} is itself a copy: point {dest} at the file it copies"


# ── each failure, on a throwaway tree ─────────────────────────────────────────────
TABLE = {
    "lib/thing.py": ["a/hooks/thing.py", "b/hooks/thing.py"],
    "lib/run": ["a/bin/run"],
    "lib/run.cmd": ["a/bin/run.cmd"],
}


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/thing.py").write_bytes(b'"""a module"""\nX = 1\n')
    run = tmp_path / "lib/run"
    run.write_bytes(b"#!/bin/sh\nexec true\n")
    run.chmod(0o755)
    (tmp_path / "lib/run.cmd").write_bytes(b"@echo off\r\nexit /b 0\r\n")
    assert copies.render(tmp_path, TABLE, quiet=True) == 0
    return tmp_path


def test_a_rendered_tree_checks_clean(tree):
    assert copies.check(tree, TABLE) == []


def test_the_header_follows_a_shebang_and_echo_off_in_the_files_own_syntax(tree):
    assert (tree / "a/hooks/thing.py").read_bytes().startswith(b"# COPY of lib/thing.py: edit that file")
    run = (tree / "a/bin/run").read_bytes().split(b"\n")
    assert run[0] == b"#!/bin/sh" and run[1].startswith(b"# COPY of lib/run:")
    cmd = (tree / "a/bin/run.cmd").read_bytes()
    assert cmd.startswith(b"@echo off\r\nrem COPY of lib/run.cmd:") and cmd.count(b"\r\n") == cmd.count(b"\n")


@pytest.mark.skipif(os.name == "nt", reason="no exec bit on Windows")
def test_the_exec_bit_follows_the_canonical_file(tree):
    assert os.access(tree / "a/bin/run", os.X_OK)
    (tree / "a/bin/run").chmod(0o644)
    assert any(p.startswith("MODE") for p in copies.check(tree, TABLE))


def test_an_edited_copy_is_drift(tree):
    p = tree / "a/hooks/thing.py"
    p.write_bytes(p.read_bytes() + b"Y = 2\n")
    assert copies.check(tree, TABLE) == [
        "DRIFT    a/hooks/thing.py  (differs from lib/thing.py: edit lib/thing.py, then render)"]


def test_an_edited_canonical_file_is_drift_at_every_copy(tree):
    (tree / "lib/thing.py").write_bytes(b'"""a module"""\nX = 2\n')
    assert sorted(p.split()[1] for p in copies.check(tree, TABLE)) == ["a/hooks/thing.py", "b/hooks/thing.py"]


def test_a_deleted_copy_is_missing(tree):
    (tree / "b/hooks/thing.py").unlink()
    assert [p.split()[0] for p in copies.check(tree, TABLE)] == ["MISSING"]


def test_a_copy_header_outside_the_map_is_a_stray(tree):
    (tree / "c").mkdir()
    (tree / "c/thing.py").write_bytes((tree / "a/hooks/thing.py").read_bytes())
    assert [p.split()[:2] for p in copies.check(tree, TABLE)] == [["STRAY", "c/thing.py"]]


def test_a_copy_that_imports_brand_needs_a_generated_brand_beside_it(tree):
    (tree / "lib/thing.py").write_bytes(b"from ._brand import env\n")
    copies.render(tree, TABLE, quiet=True)
    assert [p.split()[:2] for p in copies.check(tree, TABLE)] == [
        ["NO", "BRAND"], ["NO", "BRAND"]]


# ── rewrites that cannot be copies: they restate a rule in a few lines, so they must agree with it ──
PLUGINS = ROOT / "plugins"


def _by_path(name: str, path: Path):
    """Load PATH as module NAME with its own folder first on sys.path (its _brand.py beside it)."""
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(path.parent))


@pytest.fixture(scope="module")
def ladder():
    return _by_path("ladder_canonical", PLUGINS / "vault/src/vault/engine/ladder.py")


SETTINGS = ("HOME", "PATH", "STORE")  # the env (brand prefix) that moves the kit home, the global vault, db/


@pytest.mark.parametrize("on", list(itertools.product((False, True), repeat=3)),
                         ids=lambda on: "".join("HPS"[i] if v else "-" for i, v in enumerate(on)))
def test_the_restated_kit_paths_agree_with_the_ladder(ladder, on, tmp_path, monkeypatch):
    """tag's global_vault (a hook imports no vault) and ak's move/plan.py (ak imports no vault) say what
    vault.engine.ladder says, for every mix of the three settings: the same folder, once symlinks resolve."""
    from ak._brand import env_name

    for key, set_it in zip(SETTINGS, on):
        if set_it:
            (tmp_path / key).mkdir()
            monkeypatch.setenv(env_name(key), str(tmp_path / key))
        else:
            monkeypatch.delenv(env_name(key), raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    from ak.move import plan

    real = os.path.realpath
    for name in ("add", "list"):
        tag = _by_path(f"tag_{name}", PLUGINS / "tag/hooks" / f"{name}.py")
        assert real(tag.global_vault()) == real(ladder.global_kit()), f"tag/hooks/{name}.py global_vault"
    assert real(plan.kit_home()) == real(ladder.kit_home()), "ak/move/plan.py kit_home"
    assert real(plan.global_vault()) == real(ladder.global_kit()), "ak/move/plan.py global_vault"
    assert real(plan.db_dir()) == real(ladder.db_dir()), "ak/move/plan.py db_dir"


def test_tracer_counts_the_same_people_as_session_origin():
    """tracer's search.py applies the person-or-program rule to recorded transcripts with its own set."""
    from ak.session_origin import HUMAN_ENTRYPOINTS

    tree = ast.parse((PLUGINS / "tracer/src/tracer/search.py").read_text(encoding="utf-8"))
    value = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "HUMAN_ENTRYPOINTS" for t in n.targets))
    assert set(ast.literal_eval(value.args[0])) == set(HUMAN_ENTRYPOINTS)
