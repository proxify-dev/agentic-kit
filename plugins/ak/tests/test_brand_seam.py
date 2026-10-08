"""The brand seam (scripts/brand.py, brand.json, scripts/BRAND.md).

Every brand word comes from brand.json through a generated file, and the copy of that file in each
component is byte-identical to what `render` would write. `check` is the drift guard, the way
scripts/copies.py's is for the copied modules; the rest proves the pieces `check` cannot: the three
languages answer the same, lint finds a literal and honours the pragma, the static spots follow the brand.

These tests read the words from brand.json and never type one, so they hold under the canary's fake brand.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "brand.py"
if not (ROOT / "brand.json").is_file():  # ak exported alone (the public kit) carries only its rendered _brand.py
    pytest.skip("the brand seam is the workspace's: no brand.json above this plugin", allow_module_level=True)


def _load():
    assert SCRIPT.is_file(), f"the brand seam is missing: {SCRIPT}"
    spec = importlib.util.spec_from_file_location("brand_seam_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


brand = _load()
CUR, PREV = brand.load(ROOT)
P, C, H = CUR["env_prefix"], CUR["cli"], CUR["home_dir"]


def _run(*args, cwd=ROOT, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True, env=env)


def test_generated_files_are_current():
    r = _run("check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_check_is_also_spelled_dash_dash_check():
    assert _run("--check").returncode == 0


def test_every_component_is_named_in_vendor():
    assert brand.needed(ROOT) and set(brand.needed(ROOT)) <= set(brand.VENDOR)
    for dest in brand.VENDOR:
        assert (ROOT / dest).is_file(), f"{dest}: run `uv run python scripts/brand.py render`"


def test_a_drifted_copy_fails_check(tmp_path):
    tree = tmp_path / "t"
    shutil.copytree(ROOT / "scripts", tree / "scripts")
    (tree / "brand.json").write_text((ROOT / "brand.json").read_text())
    dest = next(iter(brand.VENDOR))
    (tree / dest).parent.mkdir(parents=True)
    (tree / dest).write_text("# hand-edited\n")
    problems = brand.check(tree)
    assert any(p.startswith("DRIFT") and dest in p for p in problems)
    assert any(p.startswith("MISSING") for p in problems)


def test_a_new_component_is_reported_until_vendor_names_it(tmp_path):
    (tmp_path / "brand.json").write_text((ROOT / "brand.json").read_text())
    hooks = tmp_path / "plugins" / "newplug" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "go.py").write_text("print(1)\n")
    assert any(p.startswith("UNCOVERED") and "newplug/hooks/_brand.py" in p for p in brand.check(tmp_path))


def test_a_generated_name_outside_vendor_is_stray(tmp_path):
    (tmp_path / "brand.json").write_text((ROOT / "brand.json").read_text())
    (tmp_path / "somewhere").mkdir()
    (tmp_path / "somewhere" / "_brand.py").write_text("")
    assert any(p.startswith("STRAY") for p in brand.check(tmp_path))


# ── the three languages answer the same ─────────────────────────────────────────────────────────

def _fake():
    return brand.fake_brand("qq")


def _write_all(tmp_path):
    b = _fake()
    (tmp_path / "_brand.py").write_text(brand.render_py(b))
    (tmp_path / "_brand.sh").write_text(brand.render_sh(b))
    (tmp_path / "brand.ts").write_text(brand.render_ts(b))
    return b


def test_python_api(tmp_path):
    b = _write_all(tmp_path)
    code = ("import os, sys; sys.path.insert(0, %r)\n"
            "from _brand import CLI, ENV_PREFIX, HOME_DIR, env, env_name, cmd\n"
            "os.environ[env_name('PATH')] = '/v'\n"
            "print(CLI, ENV_PREFIX, HOME_DIR, env('PATH'), env('NOPE'), env('NOPE', 'd'), env_name('X'), "
            "repr(cmd()), cmd('memory new'))") % str(tmp_path)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if not k.startswith(b["env_prefix"])})
    want = f"{b['cli']} {b['env_prefix']} {b['home_dir']} /v None d {b['env_prefix']}X '{b['cli']}' {b['cli']} memory new"
    assert r.stdout.strip() == want, r.stdout + r.stderr


@pytest.mark.parametrize("shell", [s for s in ("sh", "bash", "dash") if shutil.which(s)])
def test_shell_api(tmp_path, shell):
    b = _write_all(tmp_path)
    script = (f'. "{tmp_path}/_brand.sh"; '
              'echo "$BRAND_CLI|$BRAND_ENV_PREFIX|$BRAND_HOME_DIR|$(brand_env PATH)|$(brand_env NOPE)|'
              '$(brand_env NOPE d)|$(brand_env_name X)|$(brand_cmd)|$(brand_cmd memory new)"')
    env = {k: v for k, v in os.environ.items() if not k.startswith(b["env_prefix"])}
    env[b["env_prefix"] + "PATH"] = "/v"
    r = subprocess.run([shell, "-c", script], capture_output=True, text=True, env=env)
    want = f"{b['cli']}|{b['env_prefix']}|{b['home_dir']}|/v||d|{b['env_prefix']}X|{b['cli']}|{b['cli']} memory new"
    assert r.stdout.strip() == want, r.stdout + r.stderr


def test_shell_env_refuses_a_name_that_is_not_a_name(tmp_path):
    _write_all(tmp_path)
    r = subprocess.run(["sh", "-c", f'. "{tmp_path}/_brand.sh"; brand_env "A B"; echo rc=$?'],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "rc=2"


def test_typescript_api(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH")
    b = _write_all(tmp_path)
    code = (f'import {{ CLI, ENV_PREFIX, HOME_DIR, env, envName, cmd }} from "{tmp_path}/brand.ts";'
            'console.log([CLI, ENV_PREFIX, HOME_DIR, env("PATH"), String(env("NOPE")), env("NOPE", "d"), envName("X"), '
            'JSON.stringify(cmd()), cmd("memory new")].join(" "))')
    env = {k: v for k, v in os.environ.items() if not k.startswith(b["env_prefix"])}
    env[b["env_prefix"] + "PATH"] = "/v"
    r = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", code],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this node cannot strip types")
    want = f'{b["cli"]} {b["env_prefix"]} {b["home_dir"]} /v undefined d {b["env_prefix"]}X "{b["cli"]}" {b["cli"]} memory new'
    assert r.stdout.strip() == want, r.stdout + r.stderr


def test_the_copies_are_byte_identical_per_language():
    by_kind = {}
    for dest, kind in brand.VENDOR.items():
        by_kind.setdefault(kind, set()).add((ROOT / dest).read_bytes())
    assert {k: len(v) for k, v in by_kind.items()} == {"py": 1, "sh": 1, "ts": 1}


def test_every_generated_file_carries_the_header():
    for dest in brand.VENDOR:
        assert brand.HEADER in (ROOT / dest).read_text().split("\n", 1)[0], dest


# ── lint ────────────────────────────────────────────────────────────────────────────────────────

def _tree(tmp_path, previous=None):
    (tmp_path / "brand.json").write_text(json.dumps({"current": CUR, "previous": previous or []}))
    return tmp_path


def _lines(found, rule):
    return sorted({(f.path, f.line) for f in found if f.rule == rule})


def test_lint_r2_finds_literals_in_code_strings_and_honours_the_pragma(tmp_path):
    t = _tree(tmp_path)
    (t / "pkg").mkdir()
    (t / "pkg" / "m.py").write_text("\n".join([
        f'"""The docstring names {P}PATH and {C} memory new: prose, not code."""',       # 1
        "import os",                                                                    # 2
        f'a = os.environ.get("{P}PATH")',                                                # 3  env
        f'b = "{C} memory new"',                                                         # 4  cmd
        f'c = os.path.join(h, "{H}")',                                                   # 5  home
        f'd = "x"  # {P}PATH in a comment',                                              # 6
        f'e = os.environ.get("{P}OLD")  # {brand.PRAGMA}',                               # 7  exempt
        f'f = f"{{a}} {C} tracer live"',                                                 # 8  cmd, f-string
        f'g = """',                                                                      # 9
        f'{P}IN_A_TRIPLE',                                                               # 10 env, the line inside
        '"""',                                                                           # 11
    ]) + "\n")
    (t / "pkg" / "x.ts").write_text("\n".join([
        f"// {P}PATH in a comment",                                                     # 1
        f"const a = process.env.{P}PATH",                                                # 2  env (identifier)
        f'const b = "{C} memory new"',                                                   # 3  cmd
        f"const c = `run {C} tracer live ${{a}} and ${{`{H}`}}`",                        # 4  cmd + home (nested)
        f"/* {P}BLOCK */ const d = 1",                                                   # 5
        f'const e = "{P}OLD" // {brand.PRAGMA}',                                         # 6  exempt
    ]) + "\n")
    (t / "pkg" / "x.sh").write_text("\n".join([
        f"# {P}PATH in a comment",                                                       # 1
        f'v="${{{P}PATH:-}}"',                                                           # 2  env
        f'echo "run {C} tracer live"  # {P}TRAILING',                                    # 3  cmd (the trailing comment is not)
    ]) + "\n")
    (t / "README.md").write_text(f"{P}PATH and {C} memory new in prose\n")
    found = brand.lint(t, generated=[])
    assert _lines(found, "R2") == [
        ("pkg/m.py", 3), ("pkg/m.py", 4), ("pkg/m.py", 5), ("pkg/m.py", 8), ("pkg/m.py", 10),
        ("pkg/x.sh", 2), ("pkg/x.sh", 3),
        ("pkg/x.ts", 2), ("pkg/x.ts", 3), ("pkg/x.ts", 4),
    ]
    assert not _lines(found, "R1")


def test_lint_sees_env_names_in_keyword_arguments_and_labels_and_skips_the_memory_tag(tmp_path):
    t = _tree(tmp_path)
    (t / "k.py").write_text("\n".join([
        f'env.update(HOME="h", {P}NO_REEXEC="1")',                     # 1  env, a keyword argument
        f'run("x", EVAL_{P}CAPTURE="1")',                              # 2  env, prefixed
        f'x = {P}LOCAL = 3',                                           # 3  an assignment, not an env name
        f'print(f"[{C}] recent observations")',                        # 4  display
        f'tag = "[{C} memory]"',                                       # 5  the brand-free tag
        f'a = dict(y=1) == {{"k": 2}}',                                # 6  nothing
    ]) + "\n")
    assert _lines(brand.lint(t, generated=[]), "R2") == [("k.py", 1), ("k.py", 2), ("k.py", 4)]


def test_lint_ignores_the_generated_files_and_the_historical_paths(tmp_path):
    t = _tree(tmp_path)
    for rel in ("poc/a.py", ".agents/memory/research/a.py", "app/a.py", "plugins/x/_archive/a.py"):
        (t / rel).parent.mkdir(parents=True, exist_ok=True)
        (t / rel).write_text(f'a = "{P}PATH"\n')
    assert brand.lint(t, generated=[]) == []


def test_lint_r1_reads_the_words_of_previous_brands_everywhere_and_skips_current_ones(tmp_path):
    old = {"cli": "oldcli", "env_prefix": "OLDX_", "home_dir": ".oldcli", "marketplace": C}
    t = _tree(tmp_path, [old])
    (t / "docs").mkdir()
    (t / "docs" / "a.md").write_text("\n".join([
        "run oldcli now",                                    # 1  word
        "the OLDX_PATH variable",                            # 2  prefix
        "see ~/.oldcli/notes",                               # 3  dotdir
        f"run oldcli now  <!-- {brand.PRAGMA} -->",          # 4  exempt
        f"and {C} is still current",                         # 5  a previous value that is also current is not a finding
        "an oldclient is a different word",                  # 6  boundary
    ]) + "\n")
    (t / "m.py").write_text("# oldcli in a comment\nx = 1\n")
    found = brand.lint(t, rules=["R1"], generated=[])
    assert _lines(found, "R1") == [("docs/a.md", 1), ("docs/a.md", 2), ("docs/a.md", 3), ("m.py", 1)]


def test_lint_with_no_previous_brand_has_no_r1(tmp_path):
    t = _tree(tmp_path)
    (t / "a.md").write_text(f"{C} {P}PATH {H}\n")
    assert brand.lint(t, rules=["R1"], generated=[]) == []


def test_lint_paths_narrow_the_scan(tmp_path):
    t = _tree(tmp_path)
    for d in ("one", "two"):
        (t / d).mkdir()
        (t / d / "m.py").write_text(f'a = "{P}PATH"\n')
    assert {f.path for f in brand.lint(t, [str(t / "one")], generated=[])} == {"one/m.py"}


def test_lint_cli_exits_1_on_findings_and_prints_file_line():
    r = _run("lint", "plugins", "--rule", "R2")
    # this tree is mid-conversion: the exit code says whether the to-do list is empty
    assert r.returncode in (0, 1)
    if r.returncode == 1:
        first = r.stdout.splitlines()[0]
        assert first.split(":")[1].isdigit() and " R2[" in first


# ── the static spots and the canary ─────────────────────────────────────────────────────────────

def _static_tree(tmp_path):
    """The three static spots, copied from the real tree: marketplace.json, the `ak` CLI's pyproject, its bin/."""
    for rel in (brand.MARKETPLACE_JSON, f"{brand.HOST_PLUGIN}/pyproject.toml"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, tmp_path / rel)
    bindir = tmp_path / brand.HOST_PLUGIN / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    launchers = [p for p in (ROOT / brand.HOST_PLUGIN / "bin").iterdir() if p.is_file() and "exec uv run" in p.read_text()]
    assert launchers, "the `ak` CLI's launcher (bin/<cli>) is a script that runs `exec uv run`"
    for p in launchers:
        shutil.copy2(p, bindir / p.name)
    return bindir


def _script_keys(tmp_path):
    text = (tmp_path / brand.HOST_PLUGIN / "pyproject.toml").read_text()
    return [m.group("key") for m in brand._SCRIPT.finditer(text)]


def test_static_spots_follow_the_brand(tmp_path):
    bindir = _static_tree(tmp_path)
    assert brand.sync_static(tmp_path, CUR, write=False, prev=PREV) == []
    fake = _fake()
    history = [CUR] + PREV
    drift = brand.sync_static(tmp_path, fake, write=False, prev=history)
    # marketplace, script key, launcher name; the launcher runs the command it is named for (scripts/seam/launcher)
    assert len(drift) == 3, drift
    assert brand.sync_static(tmp_path, fake, write=True, prev=history) == drift
    assert brand.sync_static(tmp_path, fake, write=False, prev=history) == []
    assert json.loads((tmp_path / brand.MARKETPLACE_JSON).read_text())["name"] == fake["marketplace"]
    assert (bindir / fake["cli"]).is_file()
    assert _script_keys(tmp_path).count(fake["cli"]) == 1


def test_a_second_name_for_the_cli_already_there_is_kept_then_wins_the_flip(tmp_path):
    """While a rebrand is under way the `ak` CLI answers to two names (pyproject scripts and bin/ launchers).
    A brand that is neither of them leaves the other alone; flipping TO the other drops the old one and never
    writes the same script key twice."""
    bindir = _static_tree(tmp_path)
    others = [k for k in _script_keys(tmp_path) if k != C]
    if not others:
        pytest.skip("the `ak` CLI carries a single name: no transitional pair to test")
    other = others[0]
    fake = _fake()
    brand.sync_static(tmp_path, fake, write=True, prev=[CUR])
    keys = _script_keys(tmp_path)
    assert other in keys and fake["cli"] in keys and C not in keys
    assert (bindir / other).is_file() and (bindir / fake["cli"]).is_file()
    # and from a fresh tree: the flip lands on the name that was already there
    fresh = tmp_path / "fresh"
    fresh_bin = _static_tree(fresh)
    flipped = dict(CUR, cli=other, marketplace=other)
    dropped = brand.sync_static(fresh, flipped, write=True, prev=[CUR])
    assert any(C in line for line in dropped), dropped
    assert _script_keys(fresh).count(other) == 1 and C not in _script_keys(fresh)
    assert (fresh_bin / other).is_file() and not (fresh_bin / C).exists()
    assert brand.sync_static(fresh, flipped, write=False, prev=[CUR]) == []


def test_canary_builds_a_self_consistent_copy_and_removes_it():
    r = _run("canary", "--setup-only", "--brand", "qq")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "canary: setup ok" in r.stdout


def test_the_canary_counts_only_the_brand_and_reports_the_machine_apart():
    R = brand.Result
    fake = [R("c", 10, 3, 0, "", ["FAILED t::machine", "FAILED t::brand", "ERROR t::both_err"]),
            R("d", 5, 1, 0, "did not run: boom", []),
            R("e", 7, 0, 0, "", [])]
    control = [R("c", 11, 2, 0, "", ["FAILED t::machine", "ERROR t::both_err"]),
               R("d", 5, 1, 0, "did not run: boom", []),
               R("e", 7, 0, 0, "", [])]
    rows = {r.name: r for r in brand.split_rows(fake, control)}
    c = rows["c"]
    assert (c.brand, c.control) == (1, 2) and c.brand_ids == ["FAILED t::brand"]
    assert c.control_ids == ["FAILED t::machine", "ERROR t::both_err"]
    assert (rows["d"].brand, rows["d"].control) == (0, 1)  # red before any brand: the machine's
    assert (rows["e"].brand, rows["e"].control) == (0, 0)
    alone = {r.name: r for r in brand.split_rows(fake, None)}
    assert alone["c"].brand == 3 and alone["d"].brand == 1  # no control run: every failure counts
