"""The setup TUI, driven by a Textual pilot over a fake machine, catalog and engine.

The fakes are the contract in ak.setup (machine, catalog, engine, backup) and nothing more, so these
tests hold whatever bodies those modules grow. No pytest-asyncio in the repo: each test runs its own loop.
"""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from ak.setup.catalog import Component, Needs  # noqa: E402
from ak.setup.engine import Plan, Step  # noqa: E402
from ak.setup.machine import Bin, Machine  # noqa: E402
from ak.setup import tui  # noqa: E402

SETTINGS_DIFF = '''--- settings.json
+++ settings.json
@@ -1,2 +1,5 @@
 {
+  "env": {
+    "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"
+  }
 }
'''

COMPONENTS = (
    Component("release", "Release clone", "the kit on disk", "Kit"),  # Core: no row of its own, comes with ak
    Component("ak", "ak", "the ak command", "Plugins", depends_on=("release",), plugin="ak"),
    Component("vault", "vault", "notes you can link", "Plugins", depends_on=("ak",)),
    Component("memory", "memory", "what sessions learned", "Plugins", depends_on=("vault",), default_on=False),
    Component("notify", "notify", "a popup when a session is done", "Plugins", needs=Needs(os=frozenset({"macos"}))),
    Component("tasks", "tasks", "not in the marketplace", "Plugins", selectable=False, note="not in the marketplace"),
    Component("todo", "todo", "open items in TODO.md", "Plugins", depends_on=("settings.function-hooks",), default_on=False),
    Component("settings.function-hooks", "Function hooks", "a switch todo runs on", "Settings", default_on=False,
              comes_with="todo"),
)


@pytest.fixture(autouse=True)
def ready_at_once(monkeypatch):
    """Confirm ignores an Enter within READY_AFTER of arriving (a double tap from Choose); a pilot presses at once."""
    monkeypatch.setattr(tui.ReviewScreen, "READY_AFTER", 0)


def with_deps(ids):
    out = list(ids)
    for cid in out:
        for dep in next(c for c in COMPONENTS if c.id == cid).depends_on:
            if dep not in out:
                out.append(dep)
    return out


def blocked(c, m):
    if c.needs.os and m.os not in c.needs.os:
        return f"needs macOS (this is {m.os})"
    return None


def fake_catalog(defaults=()):
    return SimpleNamespace(COMPONENTS=COMPONENTS, MARKETPLACE="ak", blocked=blocked, with_deps=with_deps,
                           defaults=lambda m: list(defaults))


def fake_machine():
    return Machine(os="linux", arch="x86_64", bins={"uv": Bin("/bin/uv", "0.5.1"), "tmux": Bin(None)})


class FakeEngine:
    """plan: an exec step and a settings step; apply: replays events; verify: one pass."""

    def __init__(self, fail_first=False, interactive=False):
        self.fail_first, self.interactive = fail_first, interactive
        self.applied: list[list[str]] = []
        self.handed_over = None

    def plan(self, m, ids):
        return Plan(selected=list(ids), steps=[
            Step("plugin:ak", "ak", "install ak@ak", "exec", argv=["claude", "plugin", "install", "ak@ak"]),
            Step("settings", "ak", "enable function hooks", "settings", preview=SETTINGS_DIFF,
                 interactive=self.interactive),
        ])

    def apply(self, p, run, emit, run_interactive=None, stop_on_fail=True):
        self.applied.append([s.id for s in p.steps])
        for step in p.steps:
            emit("step_start", step=step)
            if self.fail_first:
                self.fail_first = False
                emit("step_done", step=step, status="failed", detail="boom")
                return False
            if step.interactive and run_interactive:
                self.handed_over = run_interactive(step, lambda: 7)
            emit("line", step=step, text="working")
            emit("step_done", step=step, status="done", detail="")
        return True

    def verify(self, m, ids):
        return [{"check": "ak", "level": "pass", "what": "ak installed", "fix": ""}]


def drive(script, engine=None, defaults=()):
    """Run the app under a pilot; script(app, pilot) does the pressing and asserting."""
    engine = engine or FakeEngine()
    backup = SimpleNamespace(runs_dir=lambda: "/kit/db/setup", undo=lambda run_id: [])
    app = tui.SetupApp(fake_machine, fake_catalog(defaults), engine, lambda: SimpleNamespace(id="run-1"), backup)

    async def main():
        async with app.run_test(size=(80, 24)) as pilot:
            await script(app, pilot)

    asyncio.run(main())
    return app, engine


async def until(pilot, ok, what, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if ok():
            return
        await pilot.pause(0.02)
    raise AssertionError(f"never reached: {what}")


async def on_screen(pilot, app, kind):
    await until(pilot, lambda: isinstance(app.screen, kind), kind.__name__)
    await pilot.pause()
    return app.screen


async def to_pick(pilot, app):
    """Detect holds its board until Enter: press it once the probe is in."""
    detect = await on_screen(pilot, app, tui.DetectScreen)
    await until(pilot, lambda: detect._ready, "detect ready")
    await pilot.press("enter")
    return await on_screen(pilot, app, tui.PickScreen)


def highlight(screen, cid):
    lst = screen.query_one("#left")
    lst.highlighted = next(i for i, o in enumerate(lst.options) if o.id == cid)


async def macos_only_component_is_refused_on_linux(app, pilot):
    pick = await to_pick(pilot, app)
    highlight(pick, "notify")
    await pilot.pause()
    assert "needs macOS (this is linux)" in pick.side_text
    await pilot.press("space")
    await pilot.pause()
    assert "notify" not in pick.picked


async def picking_a_component_picks_what_it_needs(app, pilot):
    pick = await to_pick(pilot, app)
    highlight(pick, "memory")
    await pilot.pause()
    await pilot.press("space")
    await until(pilot, lambda: pick.chosen == ["ak", "vault", "memory"], "memory brings vault and ak")
    assert "release" in pick.picked, "Core comes along without a row"
    highlight(pick, "ak")
    await pilot.pause()
    await pilot.press("space")
    await pilot.pause()
    assert pick.chosen == ["ak", "vault", "memory"], "one space only warns: ak would take vault and memory with it"
    await pilot.press("space")
    await until(pilot, lambda: pick.chosen == [], "the second space drops ak and its dependents")


async def a_setting_a_plugin_runs_on_comes_with_it_and_has_no_row(app, pilot):
    pick = await to_pick(pilot, app)
    assert "settings.function-hooks" not in pick.rows
    highlight(pick, "todo")
    await pilot.pause()
    assert "Function hooks" not in pick.side_text
    await pilot.press("space")
    await until(pilot, lambda: "settings.function-hooks" in pick.picked, "todo brings the switch it runs on")
    assert pick.chosen == ["todo"] and "Function hooks" not in pick._said.plain
    await pilot.press("space")
    await until(pilot, lambda: "settings.function-hooks" not in pick.picked, "unpicking todo drops the switch too")


async def review_shows_the_settings_diff(app, pilot):
    pick = await to_pick(pilot, app)
    assert pick.chosen == ["ak"]
    await pilot.press("enter")
    review = await on_screen(pilot, app, tui.ReviewScreen)
    summary = review.summary_text
    assert "Ready to install" in str(review.query_one("#heading").content) and "enter" in summary and "to install" in summary
    assert "you get" in summary and "ak" in summary
    assert "left out" in summary and "vault" in summary and "notify (needs macOS (this is linux))" in summary, \
        "what is not installed is said too, and why when it can't run here"
    assert not review.query_one("#left").display, "the steps are behind d: confirm is not a list to pick from"
    await pilot.press("d")
    await pilot.pause()
    assert review.query_one("#left").display
    assert "change settings.json" in str(review.query_one("#words").content), "d: the steps in words, then each one"
    assert "claude plugin install ak@ak" in review.preview_text
    review.query_one("#left").highlighted = 1
    await pilot.pause()
    assert '"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"' in review.preview_text
    summary = review.summary_text
    assert "backed up first" in summary and "settings.json" in summary and "d shows the diff" in summary


async def apply_then_verify_shows_the_pass_row(app, pilot):
    await to_pick(pilot, app)
    await pilot.press("enter")
    await on_screen(pilot, app, tui.ReviewScreen)
    await pilot.press("i")
    apply = await on_screen(pilot, app, tui.ApplyScreen)
    await until(pilot, lambda: apply.state == "done", "apply done")
    await pilot.press("enter")
    verify = await on_screen(pilot, app, tui.VerifyScreen)
    await until(pilot, lambda: verify.findings, "findings")
    assert [f["level"] for f in verify.findings] == ["pass"]
    assert "run claude" in str(verify.query_one("#next").content)
    assert "memory try" not in str(verify.query_one("#next").content), "memory was not picked"
    await pilot.press("q")


async def a_failed_step_can_be_retried(app, pilot):
    await to_pick(pilot, app)
    await pilot.press("enter")
    await on_screen(pilot, app, tui.ReviewScreen)
    await pilot.press("i")
    apply = await on_screen(pilot, app, tui.ApplyScreen)
    await until(pilot, lambda: apply.state == "failed", "first step fails")
    await pilot.press("r")
    await until(pilot, lambda: apply.state == "done", "retry finishes")


async def an_interactive_step_gets_the_terminal(app, pilot):
    await to_pick(pilot, app)
    await pilot.press("enter")
    await on_screen(pilot, app, tui.ReviewScreen)
    await pilot.press("i")
    apply = await on_screen(pilot, app, tui.ApplyScreen)
    await until(pilot, lambda: apply.state == "done", "apply done")


async def an_enter_carried_over_from_pick_never_installs(app, pilot):
    await to_pick(pilot, app)
    await pilot.press("enter")
    review = await on_screen(pilot, app, tui.ReviewScreen)
    await pilot.press("enter")
    await pilot.pause()
    assert app.screen is review, "Enter pressed twice from Pick must not start an install"
    await pilot.pause(tui.ReviewScreen.READY_AFTER)
    await pilot.press("enter")
    await on_screen(pilot, app, tui.ApplyScreen)


async def missing_tools_are_named_before_pick(app, pilot):
    detect = await on_screen(pilot, app, tui.DetectScreen)
    await until(pilot, lambda: detect._ready, "detect ready")
    shown = str(detect.query_one("#next").content)
    assert "git is missing" in shown and "claude is missing" in shown
    assert isinstance(app.screen, tui.DetectScreen), "detect waits for Enter"


def test_blocked_and_unselectable():
    drive(macos_only_component_is_refused_on_linux, defaults=["ak"])


def test_dependencies_follow_the_pick():
    drive(picking_a_component_picks_what_it_needs)


def test_a_plugins_setting_has_no_row():
    drive(a_setting_a_plugin_runs_on_comes_with_it_and_has_no_row)


def test_review_pane():
    drive(review_shows_the_settings_diff, defaults=["ak"])


def test_apply_to_verify():
    _, engine = drive(apply_then_verify_shows_the_pass_row, defaults=["ak"])
    assert engine.applied == [["plugin:ak", "settings"]]


def test_retry_runs_only_what_is_left():
    _, engine = drive(a_failed_step_can_be_retried, engine=FakeEngine(fail_first=True), defaults=["ak"])
    assert engine.applied == [["plugin:ak", "settings"], ["plugin:ak", "settings"]]


def test_interactive_step_runs_through_the_hand_over():
    _, engine = drive(an_interactive_step_gets_the_terminal, engine=FakeEngine(interactive=True), defaults=["ak"])
    assert engine.handed_over == 7


def test_a_double_enter_never_installs_a_deliberate_one_does(monkeypatch):
    monkeypatch.setattr(tui.ReviewScreen, "READY_AFTER", 0.5)
    _, engine = drive(an_enter_carried_over_from_pick_never_installs, defaults=["ak"])
    assert engine.applied == [["plugin:ak", "settings"]]


def test_detect_names_what_is_missing():
    drive(missing_tools_are_named_before_pick)


def test_next_step_names_memory_only_when_picked():
    assert "memory try" in tui.next_line(["ak", "memory"]).plain
    assert "memory try" not in tui.next_line(["ak"]).plain


async def an_installed_plugin_is_a_check_not_a_box(app, pilot):
    pick = await to_pick(pilot, app)
    lst = pick.query_one("#left")
    ak = next(o for o in lst.options if o.id == "ak")
    assert ak.disabled and "ak" in pick.picked, "installed: ticked and locked, so unticking can't pretend to uninstall"
    assert "ak" not in pick.chosen or pick.picked.count("ak") == 1
    highlight(pick, "vault")
    await pilot.pause()
    assert "already installed" not in pick.side_text and "ak — picked with it" in pick.side_text


async def a_failed_step_is_named_in_the_step_list(app, pilot):
    await to_pick(pilot, app)
    await pilot.press("enter")
    await on_screen(pilot, app, tui.ReviewScreen)
    await pilot.press("i")
    apply = await on_screen(pilot, app, tui.ApplyScreen)
    await until(pilot, lambda: apply.state == "failed", "first step fails")
    steps = str(apply.query_one("#steps").content)
    assert "✗  install ak@ak" in steps and "boom" in steps and "○  enable function hooks" in steps
    assert "failed" in str(apply.query_one("#status").content)


class FailingVerify(FakeEngine):
    def verify(self, m, ids):
        return [{"check": "ak", "level": "fail", "what": "ak is not installed", "fix": "ak setup --only ak"}]


async def a_failed_check_is_fixed_from_done(app, pilot):
    await to_pick(pilot, app)
    await pilot.press("enter")
    await on_screen(pilot, app, tui.ReviewScreen)
    await pilot.press("i")
    apply = await on_screen(pilot, app, tui.ApplyScreen)
    await until(pilot, lambda: apply.state == "done", "apply done")
    await pilot.press("enter")
    verify = await on_screen(pilot, app, tui.VerifyScreen)
    await until(pilot, lambda: verify.findings, "findings")
    assert "1 check failed" in str(verify.query_one("#heading").content)
    await pilot.press("r")
    await on_screen(pilot, app, tui.ReviewScreen)


def test_installed_plugin_is_locked():
    def installed():
        m = fake_machine()
        m.plugins["ak@ak"] = {"version": "1", "enabled": True}
        return m
    engine = FakeEngine()
    backup = SimpleNamespace(runs_dir=lambda: "/kit/db/setup", undo=lambda run_id: [])
    app = tui.SetupApp(installed, fake_catalog(["ak"]), engine, lambda: SimpleNamespace(id="run-1"), backup)

    async def main():
        async with app.run_test(size=(80, 24)) as pilot:
            await an_installed_plugin_is_a_check_not_a_box(app, pilot)
    asyncio.run(main())


def test_failed_step_in_the_list():
    drive(a_failed_step_is_named_in_the_step_list, engine=FakeEngine(fail_first=True), defaults=["ak"])


def test_fix_from_done():
    drive(a_failed_check_is_fixed_from_done, engine=FailingVerify(), defaults=["ak"])


def test_plan_summary_in_words():
    from ak.setup.engine import Step
    steps = [Step("release", "release", "clone", "clone"), Step("plugin:a", "a", "install a", "exec"),
             Step("plugin:b", "b", "install b", "exec"), Step("venv:a", "a", "build a", "exec", long=True),
             Step("gateway:login", "gateway", "sign in", "login", interactive=True)]
    text = tui.plan_summary(steps, "/runs").plain
    assert "download the kit · install 2 plugins · build 1 plugin environment · set up the gateway" in text
    assert "2 steps take longer" in text and "/runs" in text
    assert tui.step_aside(steps[3]) == "  · up to a minute" and "browser" in tui.step_aside(steps[4])


class FirstRunEngine(FakeEngine):
    """A plugin's first run after the install: it reports "[3/26]" and runs until setup lets go of it."""
    LET_GO = threading.Event()

    def plan(self, m, ids):
        p = super().plan(m, ids)
        p.steps.append(Step("first-run:tracer", "tracer", "index your Claude Code history (98.9 MB · 88 sessions)",
                            "exec", argv=["bin/tracer", "trace", "index", "--all"], background=True))
        return p

    @staticmethod
    def _try(errors, emit, event, **data):
        try:
            emit(event, **data)
        except Exception as e:  # noqa: BLE001 — the test asserts there was none
            errors.append(e)

    def apply(self, p, run, emit, run_interactive=None, stop_on_fail=True):
        self.applied.append([s.id for s in p.steps])
        for step in p.steps:
            emit("step_start", step=step)
            if step.background:
                emit("line", step=step, text="  [3/26] -srv-app: 12 interactions")
                self.LET_GO.wait(5)
                # the last word comes after the person left the screen, from a thread that has no app of its own:
                # reaching the app through the screen then raised NoActiveAppError and took setup down
                errors = []
                late = threading.Thread(target=lambda: (time.sleep(0.5),  # the screen is gone by now
                                                        self._try(errors, emit, "step_done", step=step,
                                                                  status="background", detail="keeps going")))
                late.start()
                late.join(5)
                self.late_errors = errors
                continue
            emit("step_done", step=step, status="done", detail="")
        return True


async def a_first_run_can_be_left_going(app, pilot):
    await to_pick(pilot, app)
    await pilot.press("enter")
    review = await on_screen(pilot, app, tui.ReviewScreen)
    assert "keeps going in the background if you leave" in str(review.query_one("#left").get_option_at_index(2).prompt)
    await pilot.press("i")
    apply = await on_screen(pilot, app, tui.ApplyScreen)
    await until(pilot, lambda: apply.state == "following" and apply.count == "3/26", "the first run reports its count")
    await pilot.pause(0.05)
    assert "3/26" in str(apply.query_one("#steps").content)
    assert "keeps going in the background" in str(apply.query_one("#keys").content)
    await pilot.press("enter")
    verify = await on_screen(pilot, app, tui.VerifyScreen)
    await until(pilot, lambda: verify.findings, "findings")
    assert app.engine.LET_GO.is_set(), "leaving tells the engine to stop following"
    shown = verify.query_one("#findings").content.renderables[-1]  # the checks, then the first runs
    assert "keeps going in the background" in shown.plain
    assert "keeps going in the background" in app.farewell.plain and "is set up" in app.farewell.plain
    await pilot.press("enter")


def test_a_first_run_keeps_going_when_the_person_finishes():
    FirstRunEngine.LET_GO.clear()
    _, engine = drive(a_first_run_can_be_left_going, engine=FirstRunEngine(), defaults=["ak"])
    assert engine.applied == [["plugin:ak", "settings", "first-run:tracer"]]
    assert engine.late_errors == [], "an event after the person left must not reach for the app through the screen"


def test_the_check_screen_names_only_the_tools_this_kit_needs():
    m = Machine(os="linux", arch="x86_64", bins={"uv": Bin("/bin/uv", "0.5.1"), "osascript": Bin(None)},
                history=(7_340_032_000, 15_185))
    rows = dict(tui.machine_rows(m, "ak", needed={"uv", "git", "claude"}))
    assert "osascript" not in rows, "nothing here needs it: never a red 'missing'"
    assert rows["uv"] == "0.5.1" and rows["your history"] == "6.8 GB · 15,185 Claude Code sessions"
    assert rows["kit"] == "not downloaded yet"


# ── what you get, what it changes, what is left out ─────────────────────────────────────────────────────
TOLD = (
    Component("release", "Release clone", "the kit on disk", "Kit"),
    Component("ak", "ak", "the ak command", "Plugins", depends_on=("release",), plugin="ak",
              summary="the ak command every other plugin plugs into", changes="the ak command in ~/.local/bin"),
    Component("tracer", "tracer", "which session did what", "Plugins", depends_on=("ak",), plugin="tracer",
              summary="find which session did what",
              gives=("ak sessions — your past sessions, searchable", "ak trace blame <file> — which session touched a file"),
              changes="an index of your history"),
    Component("shim", "claude shim", "a wrapper", "Launcher", default_on=False, comes_with="gateway",
              summary="the wrapper the claude you type goes through",
              gives=("ak sysprompt — your own instructions",), changes="the claude you type becomes a small wrapper",
              route="claude → shim → Claude Code → Anthropic"),
    Component("gateway", "gateway", "accounts", "Launcher", depends_on=("shim",), default_on=False,
              summary="your next account takes over", gives=("ak gateway — your accounts",), changes="a service",
              route="claude → shim → Claude Code → gateway → Anthropic"),
)


def told_catalog():
    """A catalog with a host (the ak plugin) and pieces that say what they give and change."""
    def deps(ids):
        out = list(ids)
        for cid in out:
            out += [d for d in next(c for c in TOLD if c.id == cid).depends_on if d not in out]
        return out
    return SimpleNamespace(COMPONENTS=TOLD, BY_ID={c.id: c for c in TOLD}, MARKETPLACE="ak", HOST="ak",
                           blocked=lambda c, m: None, with_deps=deps, defaults=lambda m: ["ak", "tracer"])


class ToldEngine(FakeEngine):
    def plan(self, m, ids):
        return Plan(selected=list(ids), steps=[Step(f"plugin:{cid}", cid, f"install {cid}@ak", "exec") for cid in ids
                                               if cid in ("ak", "tracer", "shim", "gateway")])

    def verify(self, m, ids):
        return [{"check": "ak", "level": "pass", "what": "ak@ak is installed and enabled (4.2.0)", "fix": ""},
                {"check": "tracer", "level": "pass", "what": "tracer@ak is installed and enabled (2.5.0)", "fix": ""},
                {"check": "shim", "level": "info", "what": "the claude shim is installed — it takes over in a new terminal",
                 "fix": "open a new terminal"}]


def drive_told(script):
    engine = ToldEngine()
    backup = SimpleNamespace(runs_dir=lambda: "/kit/db/setup", undo=lambda run_id: [])
    app = tui.SetupApp(fake_machine, told_catalog(), engine, lambda: SimpleNamespace(id="run-1"), backup)

    async def main():
        async with app.run_test(size=(120, 40)) as pilot:
            await script(app, pilot)
    asyncio.run(main())
    return app


async def choose_says_what_you_get_and_what_changes(app, pilot):
    pick = await to_pick(pilot, app)
    assert "ak" not in pick.rows and "ak" in pick.picked, "the host is the always line: no row to untick"
    assert "always installed" in str(pick.query_one("#left").get_option_at_index(0).prompt)
    assert "shim" not in pick.rows, "the shim comes with the gateway: no row of its own"
    highlight(pick, "gateway")
    await pilot.pause()
    side = pick.side_text
    assert "you get" in side and "ak gateway — your accounts" in side
    assert "on your machine" in side and "a service" in side
    assert "comes with" in side and "claude shim — the wrapper the claude you type goes through" in side
    assert "○ left out — space picks it" in side
    await pilot.press("space")
    await pilot.pause()
    assert "● picked — space leaves it out" in pick.side_text, "the panel follows the space"
    assert "shim" in pick.picked, "picking the gateway brings the shim"
    highlight(pick, "tracer")
    await pilot.pause()
    await pilot.press("space")
    await pilot.pause()
    highlight(pick, "gateway")
    await pilot.pause()
    await pilot.press("space")
    await until(pilot, lambda: pick.chosen == [], "every row left out")
    assert "shim" not in pick.picked, "leaving the gateway out drops the shim it brought"
    await pilot.press("enter")
    review = await on_screen(pilot, app, tui.ReviewScreen)
    assert app.picked == ["ak"], "with every row left out, enter still installs the ak command"
    summary = review.summary_text
    assert "you get" in summary and "the ak command every other plugin plugs into" in summary
    assert "on your machine" in summary and "the ak command in ~/.local/bin" in summary
    assert "left out" in summary and "tracer · gateway" in summary and "adds them any time" in summary
    assert "claude shim" not in summary, "the shim is never left out on its own: it is part of the gateway"


def test_choose_and_confirm_say_what_you_get_what_changes_and_what_is_left_out():
    drive_told(choose_says_what_you_get_and_what_changes)


async def done_says_what_you_can_do_now(app, pilot):
    await to_pick(pilot, app)
    await pilot.press("enter")
    await on_screen(pilot, app, tui.ReviewScreen)
    await pilot.press("i")
    apply = await on_screen(pilot, app, tui.ApplyScreen)
    await until(pilot, lambda: apply.state == "done", "apply done")
    await pilot.press("enter")
    verify = await on_screen(pilot, app, tui.VerifyScreen)
    await until(pilot, lambda: verify.findings, "findings")
    shown = tui.plain(verify.query_one("#findings").content)
    assert "ak sessions — your past sessions, searchable" in shown and "installed and enabled (2.5.0)" not in shown, \
        "a pick that passes says what it gives, not its package line"
    assert "ak@ak" not in shown, "the host is part of the ak line"
    assert "ak sessions — your past sessions, searchable" in app.farewell.plain, "what you got stays in the terminal"
    assert "fix:" not in shown and "takes over in a new terminal" in shown, "a new terminal is the next step, not a fix"
    assert "ak sysprompt — your own instructions" in app.farewell.plain, "installed, picked up by a new terminal: got"
    await pilot.press("enter")


def test_done_says_what_each_pick_gives_and_the_terminal_keeps_it():
    drive_told(done_says_what_you_can_do_now)


def test_confirm_names_what_asks_for_you_and_what_is_left_out():
    from ak.setup.engine import Plan as P
    plan = P(selected=["shim"], steps=[Step("gateway:login", "gateway", "sign in to your Claude account, for the gateway",
                                            "login", interactive=True)])
    text = tui.plain(tui.ready_lines(plan, told_catalog(), [("tracer", "")]))
    assert "asks you to" in text and "in your browser" in text and "left out" in text and "tracer" in text


async def choose_shows_how_claude_starts_with_and_without(app, pilot):
    pick = await to_pick(pilot, app)
    highlight(pick, "gateway")
    await pilot.pause()
    side = pick.side_text
    assert "how claude starts" in side and "● as now" in side, "nothing picked: claude as it is, marked"
    assert "○ gateway" in side and "claude → shim → Claude Code → gateway → Anthropic" in side
    assert "claude shim  " not in side.split("how claude starts")[1], "the shim is no way of its own: part of the gateway's"
    await pilot.press("space")
    await until(pilot, lambda: "● gateway" in pick.side_text, "the mark follows the pick")
    assert "shim" in pick.picked, "the gateway brings the shim: it is how the claude you type reaches it"
    await pilot.press("enter")
    review = await on_screen(pilot, app, tui.ReviewScreen)
    summary = review.summary_text
    assert "claude starts" in summary and "claude → shim → Claude Code → gateway → Anthropic" in summary
    assert "comes with the claude shim" in summary, "you get the gateway, and the shim as part of it"
    assert "becomes a small wrapper" not in summary, "the shim's changes are the gateway's to say, not a second row"


def test_choose_and_confirm_show_how_claude_starts_with_the_shim_and_the_gateway():
    drive_told(choose_shows_how_claude_starts_with_and_without)


def test_confirm_says_claude_stays_as_it_is_when_neither_is_picked():
    plan = Plan(selected=["ak", "tracer"], steps=[Step("plugin:tracer", "tracer", "install tracer@ak", "exec")])
    text = tui.plain(tui.ready_lines(plan, told_catalog(), [("claude shim", ""), ("gateway", "")]))
    assert "claude starts" in text and "as it does now" in text and "own login" in text
