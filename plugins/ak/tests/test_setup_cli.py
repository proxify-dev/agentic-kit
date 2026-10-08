"""setup/cli.py: the plain/json door over a fake machine and a fake engine.

Nothing here detects the real machine or runs an installer: detect() and the engine's plan/apply/verify
are replaced, HOME and the kit home are a tmp folder, and the output mode is pinned per test (ui's mode is
process-global, and `--json` switches it for the rest of the process).
"""
import dataclasses
import json
import re

import pytest
from click.testing import CliRunner

from ak import ui
from ak._brand import env_name
from ak.setup import backup, engine, machine
from ak.setup.cli import setup
from ak.setup.machine import Bin, Machine, Release

ANSI = re.compile(r"\x1b\[[0-9;]*m")
VERSIONS = {"uv": "0.7.20", "git": "2.50.1", "claude": "2.1.286", "node": "22.22.0", "tmux": "3.5"}
DIFF = '--- settings.json\n+++ settings.json\n@@ -1 +1,3 @@\n {\n+  "env": {"X": "1"}\n }\n'


def fake_machine(os="macos", **missing) -> Machine:
    bins = {n: Bin(f"/bin/{n}", v) for n, v in VERSIONS.items()}
    bins["osascript"] = Bin("/usr/bin/osascript")
    for name in missing:
        bins[name] = Bin(None)
    return Machine(os=os, arch="arm64", bins=bins, service_manager="launchd", release=Release("/tmp/release"))


class Calls:
    """What the fake engine was asked."""
    def __init__(self):
        self.planned, self.applied, self.verified = [], [], []


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(env_name("HOME"), str(tmp_path / "kit"))
    monkeypatch.delenv(env_name("REPO_URL"), raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(ui, "_MODE", "plain")
    calls = Calls()
    state = {"machine": fake_machine(), "skipped": {}, "apply_ok": True, "findings": [
        {"check": "ak-tool", "level": "pass", "what": "ak is on PATH", "fix": ""}]}

    def detect(on_probe=None):
        return state["machine"]

    def plan(m, ids):
        calls.planned.append(list(ids))
        steps = [engine.Step(f"plugin:{i}", i, f"install {i}@ak", "exec", ["claude", "plugin", "install", f"{i}@ak"],
                             preview=f"claude plugin install {i}@ak") for i in ids if i in ("ak", "vault", "memory")]
        steps.append(engine.Step("settings", "settings.file-suggestion", "set fileSuggestion", "settings", preview=DIFF))
        return engine.Plan(list(ids), steps, dict(state["skipped"]))

    def apply(p, run, emit, run_interactive=None, stop_on_fail=True):
        calls.applied.append([s.id for s in p.steps])
        for s in p.steps:
            emit("step_start", step=s)
            run.record(s.id, s.title, {"kind": "none", "note": f"nothing to take back for {s.id}"})
            emit("step_done", step=s, status="done" if state["apply_ok"] else "failed", detail="" if state["apply_ok"] else "exit 1")
            if not state["apply_ok"]:
                return False
        return True

    def verify(m, ids, with_deps=True):
        calls.verified.append(list(ids))
        state.setdefault("verify_with_deps", []).append(with_deps)
        return state["findings"]

    monkeypatch.setattr(machine, "detect", detect)
    monkeypatch.setattr(engine, "plan", plan)
    monkeypatch.setattr(engine, "apply", apply)
    monkeypatch.setattr(engine, "verify", verify)
    return {"calls": calls, "state": state, "tmp": tmp_path}


def invoke(*args):
    return CliRunner().invoke(setup, list(args), catch_exceptions=False)


def written(tmp_path):
    return sorted(str(p) for p in tmp_path.rglob("*") if p.is_file())


def test_dry_run_prints_the_plan_and_writes_nothing(world):
    r = invoke("--dry-run")
    assert r.exit_code == 0, r.output
    assert "will install" in r.output and "install ak@ak" in r.output
    assert "== 1. install ak@ak ==" in r.output and "claude plugin install ak@ak" in r.output
    assert '+  "env": {"X": "1"}' in r.output  # a settings diff round-trips verbatim
    assert "hint: ak setup --yes" in r.output
    assert world["calls"].applied == [] and written(world["tmp"]) == []


def test_the_default_run_without_yes_is_a_dry_run(world):
    r = invoke()
    assert r.exit_code == 0 and world["calls"].applied == [] and "hint: ak setup --yes" in r.output


def test_the_table_says_what_this_machine_cannot_run(world):
    world["state"]["machine"] = fake_machine(os="linux")
    rows = {ln.split("\t")[0]: ln.split("\t")[2] for ln in invoke("--dry-run").output.splitlines() if ln.count("\t") == 2}
    assert rows["notify"].startswith("blocked: needs macOS")
    assert rows["tasks"] == "blocked: not in the marketplace"
    assert rows["ak"] == "will install" and rows["todo"] == "blocked: not in the marketplace"


def test_a_component_in_place_is_marked_and_makes_no_step(world):
    world["state"]["skipped"] = {"ak": engine.IN_PLACE}
    rows = {ln.split("\t")[0]: ln.split("\t")[2] for ln in invoke("--dry-run").output.splitlines() if ln.count("\t") == 2}
    assert rows["ak"] == "in place"


def test_json_is_one_document_with_every_part(world):
    r = invoke("--json", "--only", "memory")
    assert r.exit_code == 0
    doc = json.loads(r.output)
    assert set(doc) == {"machine", "kit", "components", "steps", "skipped", "run", "verify"}
    assert doc["run"] is None and doc["verify"] is None
    by_id = {c["id"]: c for c in doc["components"]}
    assert by_id["memory"] == {"id": "memory", "group": "Plugins", "state": "install", "blocked": None, "selected": True,
                               "private": True}
    assert by_id["todo"]["selected"] is False
    assert {"id", "title", "kind", "preview", "status"} == set(doc["steps"][0]) and doc["steps"][0]["status"] == "planned"
    assert doc["machine"]["bins"]["git"]["version"] == VERSIONS["git"]
    assert written(world["tmp"]) == []


def test_only_adds_what_the_pick_needs_and_says_so(world):
    r = invoke("--dry-run", "--only", "memory")
    assert r.exit_code == 0
    ids = world["calls"].planned[0]
    assert ids[-1] == "memory" and {"vault", "ak", "marketplace", "release"} <= set(ids)
    said = next(ln for ln in r.output.splitlines() if ln.startswith("warn: also installing"))
    assert all(name in said for name in ("release", "marketplace", "ak", "vault")) and "memory" not in said.split(":")[1]


def test_only_a_name_that_is_not_there_exits_64_with_the_ids(world):
    r = CliRunner().invoke(setup, ["--only", "nope"])
    assert r.exit_code == 64
    assert "error: unknown component: nope" in r.output and "ids: " in r.output and "memory" in r.output
    assert world["calls"].planned == []


def test_a_component_this_machine_cannot_run_exits_1_with_the_reason(world):
    world["state"]["machine"] = fake_machine(os="linux")
    r = CliRunner().invoke(setup, ["--only", "notify"])
    assert r.exit_code == 1
    assert "error: notify: needs macOS (this is linux)" in r.output


def test_a_component_outside_the_marketplace_exits_1(world):
    r = CliRunner().invoke(setup, ["--only", "tasks"])
    assert r.exit_code == 1 and "tasks: not in the marketplace" in r.output


@pytest.mark.parametrize("tool", ["uv", "git", "claude"])
def test_a_missing_tool_exits_127(world, tool):
    world["state"]["machine"] = fake_machine(**{tool: None})
    r = CliRunner().invoke(setup, ["--dry-run"])
    assert r.exit_code == 127
    assert f"error: {tool} not found on PATH" in r.output
    assert world["calls"].planned == []


def test_only_asks_for_the_tools_it_needs_not_all_three(world):
    world["state"]["machine"] = fake_machine(uv=None)
    assert invoke("--dry-run", "--only", "marketplace").exit_code == 0  # marketplace → release: git, claude
    assert CliRunner().invoke(setup, ["--dry-run", "--only", "tracer"]).exit_code == 127  # tracer needs uv


def test_yes_applies_prints_the_run_and_the_way_back(world):
    r = invoke("--yes", "--only", "memory")
    assert r.exit_code == 0, r.output
    run = backup.list_runs()[0]
    assert world["calls"].applied and f"run: {run['id']}" in r.output
    assert "ok: install memory@ak" in r.output
    assert f"hint: ak setup undo {run['id']}" in r.output
    assert world["calls"].verified == [world["calls"].planned[0]]
    assert run["steps"] == len(world["calls"].applied[0])


def test_yes_with_nothing_to_do_makes_no_run(world, monkeypatch):
    monkeypatch.setattr(engine, "plan", lambda m, ids: engine.Plan(list(ids), [], {i: engine.IN_PLACE for i in ids}))
    r = invoke("--yes", "--only", "ak")
    assert r.exit_code == 0 and backup.list_runs() == [] and world["calls"].applied == []
    assert world["calls"].verified


def test_a_failed_step_exits_1_names_it_and_still_leaves_a_run(world):
    world["state"]["apply_ok"] = False
    r = CliRunner().invoke(setup, ["--yes", "--only", "ak"])
    assert r.exit_code == 1
    assert "error: install release" in r.output or "error: install ak@ak" in r.output
    assert "a step failed" in r.output and backup.list_runs()


def test_a_failing_check_exits_1(world):
    world["state"]["findings"] = [{"check": "ak-tool", "level": "fail", "what": "ak is not on PATH", "fix": "open a new terminal"}]
    r = CliRunner().invoke(setup, ["--yes", "--only", "ak"])
    assert r.exit_code == 1 and "ak is not on PATH" in r.output and "1 check(s) failed" in r.output


def test_yes_json_is_one_document_with_the_run_and_each_steps_outcome(world):
    r = invoke("--yes", "--json", "--only", "ak")
    doc = json.loads(r.output)
    assert doc["run"] == backup.list_runs()[0]["id"]
    assert doc["verify"] == world["state"]["findings"]
    assert {s["status"] for s in doc["steps"]} == {"done"}


def test_dry_run_beats_yes(world):
    r = invoke("--yes", "--dry-run")
    assert r.exit_code == 0 and world["calls"].applied == []


def test_repo_reaches_the_engine_through_the_environment(world, monkeypatch):
    seen = []
    monkeypatch.setattr(engine, "plan", lambda m, ids: seen.append(__import__("os").environ.get(env_name("REPO_URL"))) or engine.Plan(ids))
    invoke("--dry-run", "--repo", "https://example.test/kit.git")
    assert seen == ["https://example.test/kit.git"]
    monkeypatch.delenv(env_name("REPO_URL"), raising=False)


def test_status_lists_every_component_and_the_checks(world):
    world["state"]["skipped"] = {"ak": engine.IN_PLACE}
    r = invoke("status")
    assert r.exit_code == 0
    rows = {ln.split("\t")[0]: ln.split("\t")[2] for ln in r.output.splitlines() if ln.count("\t") == 2}
    assert rows["ak"] == "in place" and rows["vault"] == "not installed"
    assert "ak is on PATH" in r.output
    assert world["calls"].verified == [["ak"]]
    assert world["state"]["verify_with_deps"] == [False]  # status checks what is in place, not what that needs


def test_status_calls_an_older_bash_shim_in_place_and_does_not_fail_on_it(world):
    world["state"]["machine"] = dataclasses.replace(fake_machine(), shim={"present": True, "legacy": True})
    r = invoke("status")
    rows = {ln.split("\t")[0]: ln.split("\t")[2] for ln in r.output.splitlines() if ln.count("\t") == 2}
    assert r.exit_code == 0 and rows["shim"] == "in place: an older bash shim"


def test_status_json_exits_0_even_with_a_failing_check(world):
    world["state"]["findings"] = [{"check": "x", "level": "fail", "what": "broken", "fix": ""}]
    r = invoke("status", "--json")
    assert r.exit_code == 0
    doc = json.loads(r.output)
    assert set(doc) == {"machine", "kit", "components", "verify"} and doc["verify"][0]["level"] == "fail"


def test_status_exits_1_on_a_failing_check_in_plain(world):
    world["state"]["findings"] = [{"check": "x", "level": "fail", "what": "broken", "fix": ""}]
    assert CliRunner().invoke(setup, ["status"]).exit_code == 1


def record_run(*steps):
    run = backup.Run.new()
    for sid in steps:
        run.record(sid, f"title {sid}", {"kind": "none", "note": f"nothing for {sid}"})
    return run


def test_undo_dry_run_lists_the_steps_newest_first_and_changes_nothing(world):
    run = record_run("clone", "marketplace")
    r = invoke("undo", "--dry-run")
    assert r.exit_code == 0, r.output
    rows = [ln.split("\t") for ln in r.output.splitlines() if "\t" in ln]
    assert rows[0] == ["step", "action", "ok", "detail"]
    assert [row[0] for row in rows[1:]] == ["marketplace", "clone"] and rows[1][2] == "yes"
    assert f"hint: ak setup undo {run.id}" in r.output
    assert not backup.list_runs()[0]["undone"]


def test_undo_takes_the_newest_run_back_and_a_second_one_does_nothing(world):
    record_run("clone")
    assert invoke("undo").exit_code == 0
    assert backup.list_runs()[0]["undone"]
    again = invoke("undo")
    assert again.exit_code == 0 and "already taken back" in again.output


def test_undo_json_and_list(world):
    run = record_run("clone")
    doc = json.loads(invoke("undo", run.id[:10], "--dry-run", "--json").output)
    assert doc["run"] == run.id and doc["dry_run"] is True and doc["steps"][0]["step"] == "clone"
    listed = json.loads(invoke("undo", "--list", "--json").output)
    assert [r["id"] for r in listed] == [run.id] and listed[0]["steps"] == 1


def test_undo_with_no_runs_or_an_unknown_run_exits_1(world):
    assert CliRunner().invoke(setup, ["undo"]).exit_code == 1
    record_run("clone")
    r = CliRunner().invoke(setup, ["undo", "nope"])
    assert r.exit_code == 1 and "no setup run 'nope'" in r.output


def test_a_prefix_naming_two_runs_exits_2(world):
    a, b = backup.Run.new(), backup.Run.new()
    r = CliRunner().invoke(setup, ["undo", a.id[:8]])
    assert r.exit_code == 2 and a.id in r.output and b.id in r.output


def test_plain_output_under_claudecode_has_no_ansi(world, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv(env_name("OUTPUT"), raising=False)
    assert ui._detect() == "plain"
    world["state"]["machine"] = dataclasses.replace(world["state"]["machine"], in_claude=True)  # what detect() sees there
    r = invoke("--yes", "--only", "ak")
    assert not ANSI.search(r.output)
    assert "PATH and plugin changes show in a new terminal" in r.output


def test_a_terminal_opens_the_tui_only_when_nothing_asked_for_plain(world, monkeypatch):
    from ak.setup import cli as setup_cli, tui
    opened = []
    monkeypatch.setattr(tui, "run", lambda *a, **k: opened.append(1) or 0)
    monkeypatch.setattr(setup_cli, "_at_terminal", lambda: True)
    for flags in (["--yes"], ["--dry-run"], ["--json"], ["--only", "ak"]):
        invoke(*flags)
    assert opened == []
    assert CliRunner().invoke(setup, []).exit_code == 0
    assert opened == [1]


def test_a_component_the_engine_left_out_says_why_and_is_not_called_in_place(world):
    world["state"]["skipped"] = {"release": "no repo URL: pass --repo or set AK_REPO_URL", "ak": engine.IN_PLACE}
    out = invoke("--dry-run").output
    rows = {ln.split("\t")[0]: ln.split("\t")[2] for ln in out.splitlines() if ln.count("\t") == 2}
    assert rows["release"] == "skipped: no repo URL: pass --repo or set AK_REPO_URL" and rows["ak"] == "in place"


@pytest.fixture
def login_step(world, monkeypatch):
    """A plan whose last step needs the terminal (the gateway's browser login)."""
    login = engine.Step("gateway:login", "gateway", "log in to a gateway account", "login",
                        ["ak", "gateway", "account", "login"], interactive=True)
    monkeypatch.setattr(engine, "plan", lambda m, ids: engine.Plan(list(ids), [login], {}))
    return login


@pytest.mark.parametrize("flags,terminal", [(["--yes"], False), (["--yes", "--json"], True)])
def test_an_interactive_step_without_a_terminal_stops_before_anything_is_applied(world, login_step, monkeypatch,
                                                                                flags, terminal):
    from ak.setup import cli as setup_cli
    monkeypatch.setattr(setup_cli, "_has_terminal", lambda: terminal)
    r = CliRunner().invoke(setup, [*flags, "--only", "ak"])
    assert r.exit_code == 1
    assert "log in to a gateway account needs a terminal" in r.output and "ak gateway account login" in r.output
    assert world["calls"].applied == [] and backup.list_runs() == []
    assert not r.output.lstrip().startswith("{")  # no half document either


def test_an_interactive_step_at_a_terminal_applies(world, login_step, monkeypatch):
    from ak.setup import cli as setup_cli
    monkeypatch.setattr(setup_cli, "_has_terminal", lambda: True)
    assert invoke("--yes", "--only", "ak").exit_code == 0
    assert world["calls"].applied == [["gateway:login"]]


def test_an_interactive_step_is_only_planned_by_a_dry_run(world, login_step):
    assert invoke("--dry-run", "--only", "ak", "--json").exit_code == 0


@pytest.mark.parametrize("args", [["undo"], ["undo", "nope"]])
def test_undo_json_with_no_run_still_emits_a_document(world, args):
    record = args == ["undo", "nope"]
    if record:
        record_run("clone")
    r = CliRunner().invoke(setup, [*args, "--json", "--dry-run"])
    assert r.exit_code == 1
    assert json.loads(r.stdout) == {"run": None, "dry_run": True, "steps": []}
    assert "error: " in r.output


@pytest.mark.parametrize("tool", ["uv", "git", "claude"])
def test_status_shows_a_missing_tool_and_never_exits_127(world, tool):
    world["state"]["machine"] = fake_machine(**{tool: None})
    plain = CliRunner().invoke(setup, ["status"])
    assert plain.exit_code == 0
    rows = [ln.split("\t") for ln in plain.output.splitlines() if ln.count("\t") == 2]
    blocked = [r for r in rows if r[2].startswith("blocked: ") and tool in r[2]]
    assert blocked, plain.output
    doc = json.loads(CliRunner().invoke(setup, ["status", "--json"]).output)
    assert any(c["blocked"] and tool in c["blocked"] for c in doc["components"])
