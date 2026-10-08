"""setup/engine.py: what a selection becomes (plan), running it (apply), and checking it (verify).

A tmp HOME and Claude home per test, and fake git / claude / uv / node / ak on PATH that append what
they were asked (argv, and the environment the engine hands them) to one log. Nothing real is installed.
"""
import dataclasses
import json
import os

import pytest
from ak._brand import CLI, MARKETPLACE
from ak.setup import backup, catalog, engine, settings, source
from ak.setup.machine import Bin, Machine, Release

URL = "/srv/kits/agentic-kit"  # a local directory kit: setup clones it to the release clone, today's flow
FAKE = """#!/bin/sh
echo "NAME $* | AK_SRC=$AK_SRC CLAUDE_PLUGIN_DATA=$CLAUDE_PLUGIN_DATA CC_SHIM_DISABLE=$CC_SHIM_DISABLE CLAUDECODE=${CLAUDECODE-unset} PWD=$(pwd -P)" >> "$FAKE_LOG"
[ "$FAKE_FAIL" = "NAME" ] && { echo "NAME: it broke" >&2; exit 1; }
EXTRA
exit 0
"""
EXTRA = {
    "git": '[ "$1" = clone ] && mkdir -p "$3/.git"',
    "uv": '[ "$1 $2" = "tool update-shell" ] && printf \'export PATH="$HOME/.local/bin:$PATH"\\n\' >> "$HOME/.zshenv"',
    "claude": "echo working",
    "ak": '[ "$1" = --version ] && echo "ak 3.1.0"',
}


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    bin_ = tmp_path / "bin"
    for d in (home, bin_, tmp_path / "claude"):
        d.mkdir()
    for name in ("git", "claude", "uv", "node", "ak"):
        (bin_ / name).write_text(FAKE.replace("NAME", name).replace("EXTRA", EXTRA.get(name, ":")))
        (bin_ / name).chmod(0o755)
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "ZDOTDIR", "AK_REPO_URL", "FAKE_FAIL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("AK_HOME", str(tmp_path / "kit"))
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("CLAUDECODE", "1")  # setup runs as a person: the engine must not pass this on
    return {"home": home, "bin": bin_, "log": tmp_path / "calls.log", "claude": tmp_path / "claude", "tmp": tmp_path}


def calls(box):
    """The log as (command line, environment words) pairs, in order."""
    if not box["log"].exists():
        return []
    return [tuple(line.split(" | ")) for line in box["log"].read_text().splitlines()]


def commands(box):
    return [c for c, _ in calls(box)]


def machine(box, **kw):
    bins = {n: Bin(str(box["bin"] / n), v) for n, v in
            (("uv", "0.7.20"), ("git", "2.50.1"), ("claude", "2.1.286"), ("node", "22.13.1"))}
    base = dict(os="macos", arch="arm64", bins=bins, service_manager="launchd", local_bin_on_path=True,
                release=Release(str(source.release_dir())))
    return Machine(**{**base, **kw})


def installed(box, **kw):
    """A machine with the release cloned, the marketplace added and the default plugins enabled."""
    rel = str(source.release_dir())
    plugins = {f"{c.plugin}@{MARKETPLACE}": {"version": "3.0.0", "enabled": True, "scope": "user"}
               for c in catalog.COMPONENTS if c.plugin}
    have = dict(release=Release(rel, True, "9a86fc0f", URL), marketplaces={MARKETPLACE: rel}, plugins=plugins, uv_tools=[source.DIST])
    return machine(box, **{**have, **kw})


def gateway_on_disk(box):
    """cc-gateway's launcher where its install.sh puts it: a fake that logs what it was asked."""
    launcher = box["home"] / ".local" / "bin" / "cc-gateway"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(FAKE.replace("NAME", "cc-gateway").replace("EXTRA", ":"))
    launcher.chmod(0o755)
    return str(launcher)


def run_all(p):
    events = []
    ok = engine.apply(p, backup.Run.new(), lambda event, **d: events.append((event, d)))
    return ok, events


# ── plan ──────────────────────────────────────────────────────────────────────────────────────────────
def test_an_empty_machine_clones_adds_the_marketplace_installs_then_builds_the_tool(box):
    rel = str(source.release_dir())
    p = engine.plan(machine(box), ["ak", "ak-tool"], repo=URL)
    assert [s.id for s in p.steps] == ["release", "marketplace", "plugin:ak", "ak-tool"]
    ok, _ = run_all(p)
    assert ok
    seen = calls(box)
    assert [c for c, _ in seen] == [
        f"git clone {URL} {rel}.partial",  # renamed into place once it is whole
        f"claude plugin marketplace add {rel}",
        f"claude plugin install ak@{MARKETPLACE} -s user",
        f"uv run --no-project --python >=3.10 {rel}/plugins/ak/hooks/install-global.py",
    ]
    data = f"{box['claude']}/plugins/data/ak-{MARKETPLACE}"
    assert f"AK_SRC={rel}/plugins/ak CLAUDE_PLUGIN_DATA={data}" in seen[3][1]


def test_claude_is_run_from_home_past_the_shim_and_not_as_an_agent(box):
    run_all(engine.plan(machine(box), ["marketplace"], repo=URL))
    env = calls(box)[1][1]  # [0] is the clone
    assert "CC_SHIM_DISABLE=1" in env and "CLAUDECODE=unset" in env
    assert f"PWD={os.path.realpath(box['home'])}" in env


def test_a_machine_with_everything_makes_no_step(box):
    rel = str(source.release_dir())
    ids = ["ak", "vault", "memory", "ak-tool", "ak-path", "settings.file-suggestion"]
    held = {"fileSuggestion": engine._suggest_command(rel)}
    p = engine.plan(installed(box, settings=held), ids)
    assert p.steps == []
    assert set(ids) <= set(p.skipped) and all(p.skipped[i] == engine.IN_PLACE for i in ids)


def test_an_existing_clone_is_never_pulled_or_cloned_again(box):
    m = machine(box, release=Release(str(source.release_dir()), True, "9a86fc0f", URL), uv_tools=[source.DIST])
    p = engine.plan(m, ["ak"], repo="/srv/kits/other")
    assert [s.id for s in p.steps] == ["marketplace", "plugin:ak"] and p.skipped["release"] == engine.IN_PLACE
    run_all(p)
    assert not any(c.startswith("git ") for c in commands(box))


def test_a_folder_that_is_not_a_clone_is_not_cloned_into(box):
    rel = source.release_dir()
    rel.mkdir(parents=True)
    (rel / "mine.txt").write_text("not the kit")
    p = engine.plan(machine(box), ["release"], repo=URL)
    assert p.steps == [] and "not a clone" in p.skipped["release"]


def test_no_repo_url_skips_the_clone_and_what_needs_it(box, monkeypatch):
    monkeypatch.setattr(source, "repo_url", lambda explicit=None: explicit)
    p = engine.plan(machine(box), ["ak"])
    assert p.steps == []
    assert "AK_REPO_URL" in p.skipped["release"] and "--repo" in p.skipped["release"]
    assert "release" in p.skipped["marketplace"] and "marketplace" in p.skipped["ak"]


def test_the_gateway_login_leaves_the_account_when_undone(box):
    gateway_on_disk(box)
    login = engine.plan(installed(box, shim={"present": True}), ["gateway"]).steps[0]
    assert login.id == "gateway:login" and login.touches == [] and login.undo["kind"] == "none"
    assert "gateway account remove" in login.undo["note"]


def test_a_probe_that_failed_is_not_nothing_installed(box):
    p = engine.plan(installed(box, plugins={}, marketplaces={}, unknown=frozenset({"plugins", "marketplaces"})), ["ak"])
    assert p.steps == []
    assert p.skipped["marketplace"] == "could not read marketplaces; not touching it"
    assert p.skipped["ak"] == "needs marketplace, which was skipped"
    p = engine.plan(installed(box, plugins={}, unknown=frozenset({"plugins"})), ["ak"])
    assert p.steps == [] and p.skipped["ak"] == "could not read plugins; not touching it"


def test_a_settings_file_that_could_not_be_read_is_left_alone(box):
    p = engine.plan(installed(box, unknown=frozenset({"settings"})), ["settings.function-hooks"])
    assert p.steps == [] and "could not read settings" in p.skipped["settings.function-hooks"]


def test_a_disabled_plugin_is_left_alone_and_verify_says_so(box):
    off = {f"vault@{MARKETPLACE}": {"version": "3", "enabled": False, "scope": "user"}}
    m = installed(box, plugins={**installed(box).plugins, **off})
    p = engine.plan(m, ["vault"])
    assert p.steps == [] and "disabled" in p.skipped["vault"] and f"claude plugin enable vault@{MARKETPLACE}" in p.skipped["vault"]
    found = {f["check"]: f for f in engine.verify(m, ["vault"])}
    assert found["vault"]["level"] == "warn"


def test_verify_warns_instead_of_failing_when_a_probe_failed(box):
    found = {f["check"]: f for f in engine.verify(installed(box, plugins={}, unknown=frozenset({"plugins"})), ["ak"])}
    assert found["ak"]["level"] == "warn" and "could not read plugins" in found["ak"]["what"]


def test_another_uv_tool_that_provides_ak_is_named_not_removed(box):
    m = installed(box, uv_tools=["old-kit", "ruff"], uv_tool_exes={"old-kit": ["ak"], "ruff": ["ruff"]})
    p = engine.plan(m, ["ak"])
    assert p.steps == []
    assert "old-kit" in p.skipped["ak-tool"] and "uv tool uninstall old-kit" in p.skipped["ak-tool"] and "ruff" not in p.skipped["ak-tool"]
    assert "ak-tool" in p.skipped["ak"]


def test_a_clone_that_fails_leaves_nothing_at_the_release(box, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "git")
    ok, _ = run_all(engine.plan(machine(box), ["release"], repo=URL))
    rel = source.release_dir()
    assert ok is False and not rel.exists() and not os.path.exists(f"{rel}.partial")


def ctrl_c_on_output(event, **d):
    if event == "line":
        raise KeyboardInterrupt


def test_a_clone_cut_short_leaves_nothing_at_the_release_and_the_step_is_recorded(box):
    (box["bin"] / "git").write_text('#!/bin/sh\nmkdir -p "$3/.git"\necho cloning\nsleep 30\n')
    run = backup.Run.new()
    with pytest.raises(KeyboardInterrupt):
        engine.apply(engine.plan(machine(box), ["release"], repo=URL), run, ctrl_c_on_output)
    rel = source.release_dir()
    assert not rel.exists() and not os.path.exists(f"{rel}.partial")
    assert [(s["id"], s["status"], s["undo"]["kind"]) for s in run.steps()] == [("release", "interrupted", "clone")]


def test_a_step_cut_short_keeps_its_undo_and_the_rc_lines_it_wrote(box):
    rc = box["home"] / ".zshenv"
    (box["bin"] / "uv").write_text('#!/bin/sh\nprintf "export X=1\\n" >> "$HOME/.zshenv"\necho wrote\nsleep 30\n')
    run = backup.Run.new()
    with pytest.raises(KeyboardInterrupt):
        engine.apply(engine.plan(installed(box, local_bin_on_path=False), ["ak-path"]), run, ctrl_c_on_output)
    assert rc.read_text() == "export X=1\n" and [s["status"] for s in run.steps()][-1] == "interrupted"
    backup.undo()
    assert not rc.exists()


def test_sigterm_stops_a_step_like_ctrl_c(box):
    import signal
    run = backup.Run.new()

    def terminate(event, **d):
        if event == "line":
            os.kill(os.getpid(), signal.SIGTERM)

    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(KeyboardInterrupt):
        engine.apply(engine.plan(installed(box, plugins={}), ["ak"]), run, terminate)
    assert signal.getsignal(signal.SIGTERM) is before
    assert [(s["id"], s["status"]) for s in run.steps()] == [("plugin:ak", "interrupted")]
    assert run.steps()[0]["undo"]["argv"][-3:] == ["-s", "user", "--keep-data"]


def test_a_marketplace_that_points_elsewhere_is_refused_never_repointed(box):
    p = engine.plan(machine(box, release=Release(str(source.release_dir()), True), marketplaces={MARKETPLACE: "/some/other/kit"}, uv_tools=[source.DIST]),
                    ["vault"])
    assert p.steps == []
    assert "/some/other/kit" in p.skipped["marketplace"] and "never re-points" in p.skipped["marketplace"]
    assert "skipped" in p.skipped["vault"]


def test_a_blocked_component_is_skipped_with_its_reason(box):
    p = engine.plan(installed(box, os="linux"), ["notify"])
    assert p.steps == [] and "macOS" in p.skipped["notify"]


def test_observer_and_tracer_get_their_environments_built_after_the_install(box):
    rel = str(source.release_dir())
    p = engine.plan(installed(box, plugins={}), ["observer", "tracer"])
    ids = [s.id for s in p.steps]
    # neither imports ak (plugins/AGENTS.md §6): asking for them installs them, not the host
    assert ids == ["plugin:observer", "plugin:tracer", "venv:observer", "venv:tracer"]
    warm = p.steps[2]
    assert warm.argv[1:] == ["sync", "--frozen", "--project", f"{rel}/plugins/observer"] and warm.long
    assert "embed" in p.skipped["observer.embed"]


def test_shim_and_gateway_install_from_their_modules_at_the_pinned_version(box):
    p = engine.plan(installed(box), ["gateway"])
    assert [s.id for s in p.steps] == ["shim", "gateway:install", "gateway:login", "gateway:enable"]
    shim, install, login, enable = p.steps
    for step, cid in ((shim, "shim"), (install, "gateway")):
        mod = catalog.get(cid).module
        assert step.argv[-5:] == [mod.repo, f"v{mod.version}", mod.install, "--version", mod.version]
        assert step.undo["argv"][-4:] == [mod.repo, f"v{mod.version}", mod.install, "--uninstall"]
        assert step.preview.startswith(f"sh {mod.install} --version {mod.version}")
    launcher = str(box["home"] / ".local" / "bin" / "cc-gateway")
    assert login.interactive and login.argv == [launcher, "account", "login"]
    assert enable.argv == [launcher, "enable"] and enable.undo["argv"] == [launcher, "disable"]
    gateway_on_disk(box)  # installed already: no install step
    assert [s.id for s in engine.plan(installed(box, gateway={"accounts": True}), ["gateway"]).steps] == ["shim", "gateway:enable"]


GH = """#!/bin/sh
case "$1" in
  auth) exit "${FAKE_GH_AUTH:-0}" ;;
  api) echo "gh $*" >> "$FAKE_LOG"; echo 'echo "installer $*" >> "$FAKE_LOG"' ;;
esac
"""
CURL = """#!/bin/sh
echo "curl $*" >> "$FAKE_LOG"
while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done
echo 'echo "installer $*" >> "$FAKE_LOG"' > "$out"
"""


@pytest.mark.parametrize("gh_logged_in", [True, False])
def test_a_module_installer_is_fetched_at_its_tag_through_gh_else_curl(box, monkeypatch, gh_logged_in):
    for name, body in (("gh", GH), ("curl", CURL)):
        (box["bin"] / name).write_text(body)
        (box["bin"] / name).chmod(0o755)
    monkeypatch.setenv("FAKE_GH_AUTH", "0" if gh_logged_in else "1")
    mod = catalog.get("shim").module
    ok, _ = run_all(engine.plan(installed(box), ["shim"]))
    fetched, ran = box["log"].read_text().splitlines()  # the fakes here log the command alone
    assert ok and ran == f"installer --version {mod.version}"
    if gh_logged_in:
        assert fetched.startswith("gh api") and fetched.endswith(f"repos/{mod.repo}/contents/{mod.install}?ref=v{mod.version}")
    else:
        assert fetched.startswith("curl") and f"https://raw.githubusercontent.com/{mod.repo}/v{mod.version}/{mod.install}" in fetched


OLD_SHIM = {"present": True, "legacy": True}


def test_an_older_bash_shim_is_in_place_unless_the_person_picks_the_shim(box):
    gateway_on_disk(box)
    p = engine.plan(installed(box, shim=OLD_SHIM, gateway={"accounts": True}), ["gateway"])
    assert p.skipped["shim"] == engine.IN_PLACE and [s.id for s in p.steps] == ["gateway:enable"]
    (step,) = engine.plan(installed(box, shim=OLD_SHIM), ["shim"]).steps
    assert step.id == "shim" and "older bash" in step.title and step.argv[-2] == "--version" and "replaces the files" in step.preview


def test_verify_warns_about_an_older_shim_and_names_the_update(box):
    (found,) = [f for f in engine.verify(installed(box, shim=OLD_SHIM), ["shim"]) if f["check"] == "shim"]
    assert found["level"] == "warn" and "older bash" in found["what"] and found["fix"] == f"{CLI} setup --only shim"


def test_verify_without_dependencies_checks_only_what_it_is_given(box):
    m = installed(box, gateway={"on": True, "accounts": True})
    assert [f["check"] for f in engine.verify(m, ["gateway"])] == ["shim", "gateway"]  # the modules need no release clone
    assert [f["check"] for f in engine.verify(m, ["gateway"], with_deps=False)] == ["gateway"]


def test_ak_on_the_path_needs_no_step_but_otherwise_uv_is_asked_to_wire_it(box):
    assert engine.plan(installed(box), ["ak-path"]).steps == []
    p = engine.plan(installed(box, local_bin_on_path=False), ["ak-path"])
    assert [s.argv[1:] for s in p.steps] == [["tool", "update-shell"]]
    assert str(box["home"] / ".zshenv") in p.steps[0].touches


# ── settings ──────────────────────────────────────────────────────────────────────────────────────────
def test_the_settings_step_previews_the_diff_and_undo_gives_the_file_back_byte_for_byte(box):
    path = box["claude"] / "settings.json"
    original = '{\n  "model": "opus",\n  "env": {\n    "KEEP": "1"\n  }\n}\n'
    path.write_text(original)
    p = engine.plan(installed(box), ["settings.function-hooks"])
    (step,) = p.steps
    assert step.kind == "settings" and "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS" in step.preview and step.touches == [str(path)]
    ok, _ = run_all(p)
    assert ok
    assert settings.loads(path.read_text())["env"] == {"KEEP": "1", "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"}
    assert all(r["ok"] for r in backup.undo())
    assert path.read_text() == original


def test_a_key_set_since_the_preview_is_left_alone_and_reported(box):
    path = box["claude"] / "settings.json"
    path.write_text('{"model": "opus"}\n')
    p = engine.plan(installed(box), ["settings.function-hooks", "settings.file-suggestion"])
    path.write_text('{"model": "opus", "fileSuggestion": "mine"}\n')  # written between the preview and the apply
    run = backup.Run.new()
    events = []
    assert engine.apply(p, run, lambda e, **d: events.append((e, d)))
    done = events[-1][1]
    assert done["status"] == "done" and "1 key(s) set" in done["detail"] and "fileSuggestion (now \"mine\")" in done["detail"]
    assert settings.loads(path.read_text())["fileSuggestion"] == "mine"
    assert [c["keypath"] for c in run.steps()[0]["undo"]["changes"]] == [["env", "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"]]


def test_undo_removes_a_settings_file_the_run_made(box):
    path = box["claude"] / "settings.json"
    run_all(engine.plan(installed(box), ["settings.function-hooks"]))
    assert path.exists()
    backup.undo()
    assert not path.exists()


def test_file_suggestion_points_the_vaults_suggest_script_in_the_release(box):
    rel = str(source.release_dir())
    (step,) = engine.plan(installed(box), ["settings.file-suggestion"]).steps
    (edit,) = step.edits
    assert edit.keypath == ("fileSuggestion",)
    assert edit.value == {"type": "command", "command": f"python3 {rel}/plugins/vault/bin/suggest"}
    assert f"{rel}/plugins/vault/bin/suggest" in step.preview


def test_a_setting_the_person_already_holds_is_left_alone_and_says_so(box):
    held = {"fileSuggestion": {"type": "command", "command": "python3 /their/own/suggest"}, "env": {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "0"}}
    p = engine.plan(installed(box, settings=held), ["settings.file-suggestion", "settings.function-hooks"])
    assert p.steps == []
    assert "/their/own/suggest" in p.skipped["settings.file-suggestion"] and "left alone" in p.skipped["settings.function-hooks"]


def test_a_settings_file_that_is_not_an_object_on_the_way_down_is_reported_not_overwritten(box):
    path = box["claude"] / "settings.json"
    path.write_text('{"env": "a string"}')
    p = engine.plan(installed(box), ["settings.function-hooks"])
    assert p.steps == [] and "not an object" in p.skipped["settings.function-hooks"]
    assert path.read_text() == '{"env": "a string"}'


# ── apply ─────────────────────────────────────────────────────────────────────────────────────────────
def test_apply_streams_events_and_logs_each_steps_output(box):
    p = engine.plan(machine(box), ["release"], repo=URL)
    run = backup.Run.new()
    events = []
    assert engine.apply(p, run, lambda e, **d: events.append((e, d)))
    assert [e for e, _ in events] == ["step_start", "step_done"]
    assert events[1][1]["status"] == "done"
    assert open(run.log_path("release")).read().startswith("$ ")
    assert [s["id"] for s in run.steps()] == ["release"] and run.steps()[0]["undo"]["kind"] == "clone"


def test_child_output_reaches_emit_line_by_line(box):
    (box["bin"] / "git").write_text('#!/bin/sh\necho one\necho two >&2\nmkdir -p "$3/.git"\n')
    events = []
    engine.apply(engine.plan(machine(box), ["release"], repo=URL), backup.Run.new(), lambda e, **d: events.append((e, d)))
    assert [d["text"] for e, d in events if e == "line"] == ["one", "two"]


def test_an_interactive_step_goes_through_run_interactive_and_never_runs_unattended(box):
    gateway_on_disk(box)
    p = engine.plan(installed(box, shim={"present": True}, bins=machine(box).bins), ["gateway"])
    asked = []

    def hand_over(step, runner):
        asked.append(step.id)
        return 0  # the person did the login; the runner is not called

    ok = engine.apply(p, backup.Run.new(), lambda *a, **k: None, run_interactive=hand_over)
    assert ok and asked == ["gateway:login"]
    assert commands(box) == ["cc-gateway enable"]


def test_an_interactive_step_without_a_hand_over_runs_attached_to_the_terminal(box):
    gateway_on_disk(box)
    p = engine.plan(installed(box, shim={"present": True}), ["gateway"])
    assert engine.apply(p, backup.Run.new(), lambda *a, **k: None)
    assert [c.split()[-1] for c in commands(box)] == ["login", "enable"]


def test_apply_stops_at_the_first_failure(box, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "claude")
    run = backup.Run.new()
    events = []
    p = engine.plan(machine(box), ["ak"], repo=URL)
    assert engine.apply(p, run, lambda e, **d: events.append((e, d))) is False
    assert [c.split()[0] for c in commands(box)] == ["git", "claude"]  # the install never ran
    done = [(d["step"].id, d["status"]) for e, d in events if e == "step_done"]
    assert done == [("release", "done"), ("marketplace", "failed")]
    assert "exit 1: claude: it broke" in [d["detail"] for e, d in events if e == "step_done"][-1]
    assert [(s["id"], s["status"]) for s in run.steps()] == [("release", "done"), ("marketplace", "failed")]


def test_stop_on_fail_off_carries_on_and_still_reports_failure(box, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "claude")
    p = engine.plan(machine(box, uv_tools=[source.DIST]), ["ak"], repo=URL)
    assert engine.apply(p, backup.Run.new(), lambda *a, **k: None, stop_on_fail=False) is False
    assert [c.split()[0] for c in commands(box)] == ["git", "claude", "claude"]


def test_a_command_that_is_not_there_fails_the_step_without_a_crash(box):
    (box["bin"] / "git").unlink()
    p = engine.plan(machine(box), ["release"], repo=URL)
    ok, events = run_all(p)
    assert ok is False and events[-1][1]["status"] == "failed"


def test_undo_takes_back_the_install_with_the_inverse_commands(box):
    rel = str(source.release_dir())
    run_all(engine.plan(machine(box), ["ak", "ak-tool"], repo=URL))
    box["log"].unlink()
    results = backup.undo()
    assert all(r["ok"] for r in results), results
    assert commands(box)[:3] == [
        "uv tool uninstall ak",
        f"claude plugin uninstall ak@{MARKETPLACE} -s user --keep-data",
        f"claude plugin marketplace remove {MARKETPLACE}",
    ]
    assert all("CLAUDECODE=unset" in env for c, env in calls(box) if c.startswith("claude"))  # undone outside a session too
    assert commands(box)[3:] == [  # the clone is only removed once it is known clean, pushed and stash-free
        f"git -C {rel} status --porcelain",
        f"git -C {rel} rev-list --count HEAD --not --remotes",
        f"git -C {rel} stash list",
    ]
    assert not os.path.exists(rel)  # the clone this run made is clean, so it goes


def test_undo_takes_a_rc_line_uv_appended_and_keeps_the_rest(box):
    rc = box["home"] / ".zshenv"
    rc.write_text("export MINE=1\n")
    p = engine.plan(installed(box, local_bin_on_path=False), ["ak-path"])
    assert engine.apply(p, backup.Run.new(), lambda *a, **k: None)
    assert rc.read_text().endswith('$PATH"\n') and rc.read_text() != "export MINE=1\n"
    backup.undo()
    assert rc.read_text() == "export MINE=1\n"


def test_undo_removes_an_rc_file_the_step_created(box):
    p = engine.plan(installed(box, local_bin_on_path=False), ["ak-path"])
    engine.apply(p, backup.Run.new(), lambda *a, **k: None)
    assert (box["home"] / ".zshenv").exists()
    backup.undo()
    assert not (box["home"] / ".zshenv").exists()


# ── verify ────────────────────────────────────────────────────────────────────────────────────────────
def test_verify_on_a_good_machine_finds_nothing_to_fix(box):
    ids = ["ak", "vault", "ak-tool", "settings.function-hooks", "shim", "gateway", "notify"]
    m = installed(box, shim={"present": True, "pathPos": 1}, gateway={"on": True, "accounts": True},
                  settings={"env": {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"}}, ak_on_path=True)
    found = engine.verify(m, ids)
    assert [f["level"] for f in found if f["level"] in ("fail", "warn")] == []
    ak = next(f for f in found if f["check"] == "ak-tool")
    assert ak["level"] == "pass" and "3.1.0" in ak["what"]


def test_verify_names_what_is_missing_and_the_command_that_fixes_it(box):
    found = {f["check"]: f for f in engine.verify(machine(box, plugins={f"vault@{MARKETPLACE}": {"enabled": False}}),
                                                  ["vault", "shim", "gateway", "settings.function-hooks"])}
    assert found["release"]["level"] == "fail" and found["release"]["fix"] == f"{CLI} setup --only release"
    assert found["marketplace"]["level"] == "fail"
    assert found["vault"]["level"] == "warn" and found["vault"]["fix"] == f"claude plugin enable vault@{MARKETPLACE}"
    assert found["shim"]["level"] == "fail" and found["gateway"]["level"] == "fail"
    assert found["settings.function-hooks"]["fix"] == f"{CLI} setup --only settings.function-hooks"


def test_verify_says_a_blocked_component_is_not_a_failure(box):
    (found,) = [f for f in engine.verify(installed(box, os="linux"), ["notify"]) if f["check"] == "notify"]
    assert found["level"] == "info" and "macOS" in found["what"]


def test_verify_says_a_new_terminal_picks_up_the_shim_without_calling_it_a_problem(box):
    found = {f["check"]: f for f in engine.verify(installed(box, shim={"present": True}), ["shim"])}
    assert found["shim"]["level"] == "info", "right after setup the shim is never first on PATH: that is the next step"
    assert "new terminal" in found["shim"]["what"]


# ── a plugin's first run: last, in the background ─────────────────────────────────────────────────────
def test_a_plugins_first_run_comes_last_and_names_the_history_it_reads(box):
    rel = str(source.release_dir())
    p = engine.plan(installed(box, plugins={}, history=(103_674_100, 88)), ["tracer"])
    first = p.steps[-1]
    assert first.id == "first-run:tracer" and first.background and not first.long
    assert first.title == "index your Claude Code history (98.9 MB · 88 sessions)"
    assert first.argv == [f"{rel}/plugins/tracer/bin/tracer", "trace", "index", "--all"]
    assert first.cwd == f"{rel}/plugins/tracer"


def test_no_history_means_no_first_run_and_the_plan_says_why(box):
    p = engine.plan(installed(box, plugins={}, history=None), ["tracer"])
    assert not any(s.id.startswith("first-run:") for s in p.steps)
    assert "no Claude Code history" in p.skipped["tracer.first-run"]


def script(box, name, body):
    path = box["bin"] / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def test_a_background_step_is_followed_line_by_line_to_its_end(box):
    argv = [script(box, "index", 'echo "[1/2] a"\necho "[2/2] b"\nprintf "done"\n')]
    step = engine.Step("first-run:x", "x", "index", "exec", argv, background=True, undo={"kind": "none"})
    ok, events = run_all(engine.Plan(["x"], [step]))
    assert ok and [d["text"] for e, d in events if e == "line"] == ["[1/2] a", "[2/2] b", "done"]
    assert events[-1][1]["status"] == "done"


def test_letting_go_of_a_background_step_leaves_it_running(box):
    import time
    marker = box["tmp"] / "finished"
    argv = [script(box, "slow", 'echo started\nsleep 1\ntouch "$1"\n'), str(marker)]
    step = engine.Step("first-run:x", "x", "index", "exec", argv, background=True, undo={"kind": "none"})
    events = []

    def emit(event, **d):
        events.append((event, d))
        if event == "line" and d["text"] == "started":
            engine.LET_GO.set()  # the person pressed Enter: finish setup now
    try:
        assert engine.apply(engine.Plan(["x"], [step]), backup.Run.new(), emit)
    finally:
        engine.LET_GO.clear()
    assert events[-1][1]["status"] == "background" and not marker.exists()
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.1)
    assert marker.exists(), "the step went on after setup stopped following it"


# ── a kit at a URL: Claude Code keeps the clone ──────────────────────────────────────────────────────
GITHUB = "https://github.com/proxify-dev/agentic-kit"


def claude_clone(box):
    """Claude Code's clone of the kit, as `marketplace add` leaves it: <claude home>/plugins/marketplaces/<name>."""
    clone = box["claude"] / "plugins" / "marketplaces" / MARKETPLACE
    (clone / "plugins" / "ak").mkdir(parents=True, exist_ok=True)
    (clone / ".claude-plugin").mkdir(exist_ok=True)
    (clone / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"name": MARKETPLACE}))
    return clone


def declared(box, entry):
    """settings.json with the extraKnownMarketplaces entry `marketplace add` writes."""
    path = box["claude"] / "settings.json"
    path.write_text(json.dumps({"extraKnownMarketplaces": {MARKETPLACE: entry}}, indent=2) + "\n")
    return path


def from_github(box, **kw):
    """A machine set up from GitHub: the marketplace is Claude Code's clone, the default plugins enabled."""
    clone = claude_clone(box)
    row = {"name": MARKETPLACE, "source": "github", "repo": "proxify-dev/agentic-kit", "installLocation": str(clone)}
    held = {"extraKnownMarketplaces": {MARKETPLACE: {"source": {"source": "github", "repo": "proxify-dev/agentic-kit"},
                                                     "autoUpdate": True}}}
    plugins = {f"{c.plugin}@{MARKETPLACE}": {"version": "3.0.0", "enabled": True, "scope": "user"}
               for c in catalog.COMPONENTS if c.plugin}
    have = dict(marketplaces={MARKETPLACE: "proxify-dev/agentic-kit"}, marketplace_rows={MARKETPLACE: row},
                plugins=plugins, uv_tools=[source.DIST], settings=held)
    return machine(box, **{**have, **kw})


def test_a_kit_at_a_github_url_is_added_by_owner_repo_and_never_cloned_by_setup(box):
    clone = f"{box['claude']}/plugins/marketplaces/{MARKETPLACE}"
    p = engine.plan(machine(box), ["ak", "ak-tool"], repo=GITHUB)
    assert [s.id for s in p.steps] == ["marketplace", "marketplace:auto-update", "plugin:ak", "ak-tool"]
    add = p.steps[0]
    assert add.argv[1:] == ["plugin", "marketplace", "add", "proxify-dev/agentic-kit"]
    assert p.skipped["release"].startswith(engine.KEPT_BY_CLAUDE) and clone in p.skipped["release"]
    # the undo removes the marketplace, and says what Claude Code takes with it
    assert add.undo["argv"][1:] == ["plugin", "marketplace", "remove", MARKETPLACE]
    assert "uninstalls every plugin" in add.undo["note"] and "conversation-index" in add.undo["note"]
    assert "uninstalls every plugin" in add.preview and f"clones it to {clone}" in add.preview
    tool = p.steps[-1]
    assert tool.env["AK_SRC"] == f"{clone}/plugins/ak"  # the ak command runs from Claude Code's clone
    assert tool.argv[-1] == f"{clone}/plugins/ak/hooks/install-global.py"
    run_all(p)
    assert not any(c.startswith("git ") for c in commands(box))


def test_asking_for_the_kit_clone_alone_brings_the_marketplace_that_makes_it(box):
    p = engine.plan(machine(box), ["ak-tool"], repo=GITHUB)
    assert [s.id for s in p.steps] == ["marketplace", "marketplace:auto-update", "ak-tool"]


def test_a_kit_at_another_url_is_added_as_that_git_url(box):
    for url in ("http://localhost:8080/kit.git", "git@git.example.test:team/kit.git", "ssh://git@example.test/kit.git"):
        add = engine.plan(machine(box), ["marketplace"], repo=url).steps[0]
        assert add.id == "marketplace" and add.argv[-1] == url
    add = engine.plan(machine(box), ["marketplace"], repo="git+https://github.com/proxify-dev/agentic-kit.git").steps[0]
    assert add.argv[-1] == "proxify-dev/agentic-kit"  # uv's git+ prefix, GitHub: owner/repo


def test_a_file_url_or_a_path_keeps_the_release_clone(box):
    rel = str(source.release_dir())
    for where in ("file:///srv/kits/agentic-kit", "/srv/kits/agentic-kit"):
        p = engine.plan(machine(box), ["marketplace"], repo=where)
        assert [s.id for s in p.steps] == ["release", "marketplace"]  # `marketplace add` refuses file://
        assert p.steps[0].argv[1:] == ["clone", where, rel] and p.steps[1].argv[-1] == rel


def test_the_auto_update_step_previews_one_key_in_the_entry_marketplace_add_writes(box):
    step = engine.plan(machine(box), ["marketplace"], repo=GITHUB).steps[1]
    assert step.kind == "settings" and step.needs_key == ("extraKnownMarketplaces", MARKETPLACE)
    added = [ln for ln in step.preview.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    assert any('"autoUpdate": true' in ln for ln in added)
    assert not any("source" in ln or "repo" in ln for ln in added), step.preview  # the entry itself is marketplace add's
    assert '"repo": "proxify-dev/agentic-kit"' in step.preview  # shown as context: what the add will have written
    assert not (box["claude"] / "settings.json").exists()


def test_auto_update_is_added_to_the_entry_and_undo_takes_back_only_that_key(box):
    p = engine.plan(machine(box), ["marketplace"], repo=GITHUB)
    entry = {"source": {"source": "github", "repo": "proxify-dev/agentic-kit"}}
    path = declared(box, entry)  # what the real `claude plugin marketplace add` writes
    ok, events = run_all(p)
    assert ok and json.loads(path.read_text())["extraKnownMarketplaces"][MARKETPLACE] == {**entry, "autoUpdate": True}
    box["log"].unlink()
    results = backup.undo()
    assert all(r["ok"] for r in results), results
    assert json.loads(path.read_text())["extraKnownMarketplaces"][MARKETPLACE] == entry
    assert commands(box) == [f"claude plugin marketplace remove {MARKETPLACE}"]
    removed = next(r for r in results if "marketplace remove" in r["action"])
    assert "uninstalls every plugin" in removed["detail"]


def test_auto_update_never_makes_an_entry_marketplace_add_did_not_write(box):
    p = engine.plan(machine(box), ["marketplace"], repo=GITHUB)
    ok, events = run_all(p)
    done = [d for e, d in events if e == "step_done" and d["step"].id == "marketplace:auto-update"]
    assert ok and done[0]["status"] == "skipped" and "extraKnownMarketplaces" in done[0]["detail"]
    assert not (box["claude"] / "settings.json").exists()


def test_a_second_run_repairs_auto_update_that_a_marketplace_add_dropped(box):
    m = from_github(box)
    assert engine.plan(m, ["ak", "marketplace"]).steps == []
    entry = {"source": {"source": "github", "repo": "proxify-dev/agentic-kit"}}
    p = engine.plan(dataclasses.replace(m, settings={"extraKnownMarketplaces": {MARKETPLACE: entry}}), ["ak"])
    assert [s.id for s in p.steps] == ["marketplace:auto-update"]
    assert p.skipped["release"] == engine.IN_PLACE  # Claude Code's clone is on disk


def test_a_person_who_turned_auto_update_off_is_left_alone_and_verify_says_so(box):
    m = from_github(box, settings={"extraKnownMarketplaces": {MARKETPLACE: {"source": {}, "autoUpdate": False}}})
    assert engine.plan(m, ["marketplace"]).steps == []
    (found,) = engine.verify(m, ["marketplace"], with_deps=False)
    assert found["level"] == "warn" and "false" in found["what"]


def test_tracer_warms_and_first_runs_in_its_cached_copy_read_when_the_step_runs(box):
    tracer = catalog.get("tracer")
    p = engine.plan(from_github(box, plugins={}, history=(1024, 3)), ["tracer"])
    assert [s.id for s in p.steps] == ["plugin:tracer", "venv:tracer", "first-run:tracer"]
    planned = f"{box['claude']}/plugins/cache/{MARKETPLACE}/tracer/{tracer.version}"
    warm, first = p.steps[1], p.steps[2]
    assert warm.argv[-1] == planned and first.cwd == planned and first.argv[0] == f"{planned}/bin/tracer"
    # the install writes the real folder to installed_plugins.json; each step reads it when it runs
    real = box["tmp"] / "cache-real" / "tracer" / "9.9.9"
    (real / "bin").mkdir(parents=True)
    (real / "bin" / "tracer").write_text(FAKE.replace("NAME", "tracer").replace("EXTRA", ":"))
    (real / "bin" / "tracer").chmod(0o755)
    (box["claude"] / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {f"tracer@{MARKETPLACE}": [{"scope": "user", "installPath": str(real)}]}}))
    ok, _ = run_all(p)
    assert ok
    seen = calls(box)
    assert seen[1][0] == f"uv sync --frozen --project {real}"
    assert seen[2][0] == "tracer trace index --all" and f"PWD={os.path.realpath(real)}" in seen[2][1]


def test_a_local_folder_marketplace_from_an_older_setup_is_refused_with_the_way_over(box, monkeypatch):
    monkeypatch.setattr(source, "repo_url", lambda explicit=None: explicit or GITHUB)  # the old release clone's origin
    old = str(source.release_dir())
    m = machine(box, release=Release(old, True, "9a86fc0f", GITHUB), marketplaces={MARKETPLACE: old},
                marketplace_rows={MARKETPLACE: {"source": "directory", "path": old, "installLocation": old}},
                uv_tools=[source.DIST])
    p = engine.plan(m, ["ak"], repo=GITHUB)
    assert p.steps == []
    why = p.skipped["marketplace"]
    assert "local folder" in why and old in why and "never updates" in why
    assert f"claude plugin marketplace remove {MARKETPLACE}" in why and f"setup --repo {GITHUB}" in why
    assert p.skipped["ak"] == "needs marketplace, which was skipped"
    (found,) = engine.verify(m, ["marketplace"], with_deps=False)
    assert found["level"] == "fail" and "local folder" in found["what"]


def test_a_marketplace_cloned_from_another_repo_is_refused_never_repointed(box):
    m = from_github(box, marketplaces={MARKETPLACE: "someone/fork"},
                    marketplace_rows={MARKETPLACE: {"source": "github", "repo": "someone/fork"}})
    p = engine.plan(m, ["marketplace"], repo=GITHUB)
    assert p.steps == [] and "someone/fork" in p.skipped["marketplace"] and "never re-points" in p.skipped["marketplace"]


def test_verify_a_kit_from_github(box):
    clone = f"{box['claude']}/plugins/marketplaces/{MARKETPLACE}"
    m = from_github(box)
    found = {f["check"]: f for f in engine.verify(m, ["release", "marketplace"], with_deps=False)}
    assert found["release"]["level"] == "pass" and clone in found["release"]["what"]
    assert found["marketplace"]["what"] == \
        f"the {MARKETPLACE} marketplace is Claude Code's clone of proxify-dev/agentic-kit, auto-update on"
    (miss,) = engine.verify(dataclasses.replace(m, settings={}), ["marketplace"], with_deps=False)
    assert miss["level"] == "fail" and "auto-update is off" in miss["what"] and miss["fix"] == f"{CLI} setup --only marketplace"


def test_status_of_a_kit_from_github(box, monkeypatch):
    from click.testing import CliRunner
    from ak import ui
    from ak.setup import machine as machine_mod
    from ak.setup.cli import setup
    monkeypatch.setattr(machine_mod, "detect", lambda on_probe=None: from_github(box, ak_on_path=True))
    monkeypatch.setattr(ui, "_MODE", "plain")
    out = CliRunner().invoke(setup, ["status"]).output
    assert "pass\trelease\tClaude Code keeps the kit's clone at " in out, out
    assert f"pass\tmarketplace\tthe {MARKETPLACE} marketplace is Claude Code's clone of proxify-dev/agentic-kit, auto-update on" in out
    assert "release clone" not in out
