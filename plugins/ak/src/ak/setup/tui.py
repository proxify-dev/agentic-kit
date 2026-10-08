"""The interactive front of `ak setup`: a Textual app over the same plan → apply → verify engine.

Five screens in a line — Check, Choose, Confirm, Install, Done — each one question the person
answers with Enter. Nothing here decides anything: the catalog says what can be picked and what
pulls in what, the engine says what would run and does it, backup undoes it. The app only holds
the person's choices between screens and shows what those modules return, so a test hands it fakes.

The look is the terminal's own: its colours and background (ansi_color), no borders, no boxes. Every
screen is the same frame — a top line (the kit, the five steps), a title, the body, a status line and a
keys line — and marks are plain glyphs: ● picked, ○ not, ✓ in place, – can't here, › the cursor.

Textual is imported here and only here; this module is imported only when `ak setup` opens at a
terminal, so every other verb stays as fast as it was.
"""
from __future__ import annotations

import dataclasses
import inspect
import io
import re
import shlex
import time
from typing import Callable

from rich.console import Console, Group
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult, ScreenStackError, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import OptionList, RichLog, Static
from textual.widgets.option_list import Option

from ak._brand import CLI, DISPLAY_NAME, cmd

GROUPS = ("Kit", "Plugins", "Settings", "Launcher")
GROUP_WORDS = {"Plugins": "inside Claude Code", "Settings": "your settings.json",
               "Launcher": "around the claude command you type"}  # beside each heading on Choose
CORE = "Kit"  # setup's own rows (the clone, the marketplace, the ak command): one line, never a pick
CORE_WORDS = f"the {CLI} command and the kit's source"
NOTHING_ELSE = "nothing outside Claude Code"  # a component with no setup.changes
STEPS = ("check", "choose", "confirm", "install", "done")
ACCENT, OK, WARN, BAD, MUTED = "cyan", "green", "yellow", "red", "dim"
# one meaning per colour, on every screen: green in place / done / passes · cyan your picks, what happens next, the
# main key · yellow takes long, needs a look · red failed, missing · dim the rest (not picked, waiting, words)
LEVEL_MARK = {"pass": ("✓", OK), "info": ("·", ACCENT), "warn": ("!", WARN), "fail": ("✗", BAD)}
KEY_NAMES = {"enter": "enter", "space": "space", "escape": "esc"}

CSS = """
Screen { layout: vertical; padding: 1 2 0 2; }
#top { height: 1; margin-bottom: 1; }
#heading { text-style: bold; }
#sub { color: $text-muted; margin-bottom: 1; }
#body { height: 1fr; }
#status { height: auto; margin-top: 1; }
#keys { height: 1; margin-bottom: 1; }

* { scrollbar-size-vertical: 1; scrollbar-background: transparent; scrollbar-background-hover: transparent;
    scrollbar-background-active: transparent; scrollbar-color: ansi_bright_black;
    scrollbar-color-hover: ansi_bright_black; scrollbar-color-active: ansi_white; scrollbar-corner-color: transparent; }
OptionList, OptionList:focus { border: none; background: transparent; padding: 0; height: 1fr; }
OptionList > .option-list--option,
OptionList > .option-list--option-highlighted,
OptionList:focus > .option-list--option-highlighted,
OptionList > .option-list--option-hover,
OptionList > .option-list--option-disabled { background: transparent; color: $text; text-style: none; padding: 0; }

#side { height: auto; margin-top: 1; }
#summary { height: auto; margin: 1 0 1 2; }
#words { height: auto; margin-bottom: 1; }
#preview { height: auto; max-height: 14; margin-top: 1; }
#steps-box { width: 1fr; }
#steps { text-wrap: nowrap; text-overflow: ellipsis; }
#log { width: 1fr; border: none; background: transparent; padding: 0 0 0 3; color: $text-muted;
       scrollbar-size-vertical: 0; scrollbar-size-horizontal: 0; overflow-x: hidden; }
"""


# ── the frame every screen shares ────────────────────────────────────────────────────────────────────
def trail(n: int) -> Text:
    """'✓ check  ● choose  confirm  install  done' — behind green, here cyan, ahead quiet."""
    out = Text()
    for i, name in enumerate(STEPS, 1):
        if i > 1:
            out.append("   ")
        if i < n:
            out.append(f"✓ {name}", style=OK)
        elif i == n:
            out.append(f"● {name}", style=f"bold {ACCENT}")
        else:
            out.append(name, style=MUTED)
    return out


class Step(Screen):
    """One of the five screens: draws the frame, and the keys line from its own bindings (only those on now)."""
    N = 0
    TITLE_TEXT = ""

    def frame_top(self) -> ComposeResult:
        grid = Table.grid(expand=True)
        grid.add_column()
        grid.add_column(justify="right")
        grid.add_row(Text(f"{DISPLAY_NAME} setup", style="bold"), trail(self.N))
        yield Static(grid, id="top")
        yield Static(Text(self.TITLE_TEXT), id="heading")

    def frame_bottom(self) -> ComposeResult:
        yield Static(id="status")
        yield Static(id="keys")

    def headline(self, text: str, sub: str | Text = "", tone: str = "") -> None:
        """The title; tone OK or BAD colours it, so the outcome reads before the words."""
        self.query_one("#heading", Static).update(Text(("✓ " if tone == OK else "✗ " if tone == BAD else "") + text,
                                                       style=tone))
        for w in self.query("#sub"):
            w.update(sub)

    def say(self, text: str | Text) -> None:
        self.query_one("#status", Static).update(text)

    def refresh_bindings(self) -> None:
        super().refresh_bindings()
        if self.is_mounted:
            self.draw_keys()

    def draw_keys(self) -> None:
        keys = self.query("#keys")
        if not keys:  # bindings refreshed before the frame is composed
            return
        out = Text()
        for b in self.BINDINGS:
            if not b.show or self.check_action(b.action, ()) is False:
                continue
            if out.plain:
                out.append("    ")
            name = KEY_NAMES.get(b.key, b.key)
            words = self.key_words(b)
            if not out.plain:
                out.append(f" {name} ", style=f"bold black on {ACCENT}")
                out.append(f" {words}", style=f"bold {ACCENT}")
            else:
                out.append(name, style="bold")
                out.append(f" {words}", style=MUTED)
        keys.first(Static).update(out)

    def key_words(self, b: Binding) -> str:
        """What a key does, as its line says it; a screen whose key changes meaning with its state overrides this."""
        return b.description

    def on_mount(self) -> None:
        self.draw_keys()


def size_words(n: int) -> str:
    """103_674_100 → "98.9 MB": a size a person reads."""
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" or n >= 100 else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB" if n < 100 else f"{n:.0f} GB"


def machine_rows(m, marketplace: str, needed: set[str] | None = None) -> list[tuple[str, str]]:
    """What detect found, one (label, value) per line a person reads; plugins counted in the kit's marketplace.
    needed: the tools something in this kit runs on; a tool nothing here needs is left out, so "missing" is
    only ever said about one that matters."""
    rows: list[tuple[str, str]] = [("system", f"{m.os} {m.arch}")]
    for name, found in m.bins.items():
        if needed is None or name in needed:
            rows.append((name, (found.version or "found") if found.found else "missing"))
    release = m.release
    rows.append(("kit", f"{(release.head or '?')[:8]} at {release.path}" if release and release.exists
                 else "not downloaded yet"))
    history = getattr(m, "history", None)
    rows.append(("your history", f"{size_words(history[0])} · {history[1]:,} Claude Code sessions" if history
                 else "none yet"))
    ours = [p for p in m.plugins if p.endswith("@" + marketplace)]
    rows.append(("plugins", f"{len(ours)} installed"))
    rows.append(("shim", "installed" if m.shim else "not installed"))
    gateway = "on" if m.gateway.get("on") else "off"
    rows.append(("gateway", gateway + (", has accounts" if m.gateway.get("accounts") else "")))
    return rows


def plain(renderable) -> str:
    """What a renderable says, as text: a test reads a screen through it."""
    console = Console(width=200, file=io.StringIO(), color_system=None, record=True)
    console.print(renderable)
    return console.export_text()


def host_of(catalog) -> str | None:
    """The plugin that brings the ak command (catalog.HOST): always installed, so it is part of the always line,
    never a row to untick. None for a catalog without one."""
    host = getattr(catalog, "HOST", None)
    return host if any(c.id == host for c in catalog.COMPONENTS) else None


def is_core(catalog, cid: str) -> bool:
    """Setup's own rows and the host plugin: what the always line stands for."""
    c = next((c for c in catalog.COMPONENTS if c.id == cid), None)
    return bool(c and (c.group == CORE or c.id == host_of(catalog)))


def history_words(m) -> str:
    """'98.9 MB · 88 sessions', or '' when there is no history."""
    history = getattr(m, "history", None)
    return f"{size_words(history[0])} · {history[1]:,} sessions" if history else ""


PLAIN_ROUTE = "claude → Claude Code → Anthropic, on Claude Code's own login"  # none of the route pieces picked


def routes(catalog, picked) -> list[tuple[str, str, bool]]:
    """Every way the `claude` you type can start, from the rows with a route: (who, route, given), the plain one first.
    who names the row and the other route rows it brings; given marks the one the picks give: the last route row picked,
    in catalog order, else the plain one. A piece that comes with a row (the shim, with the gateway) is part of that row's."""
    pieces = [c for c in catalog.COMPONENTS if getattr(c, "route", "") and not getattr(c, "comes_with", None)]
    if not pieces:
        return []
    ids = {c.id for c in pieces}
    picked = set(picked)
    now = next((c.id for c in reversed(pieces) if c.id in picked), None)
    out = [("as now", PLAIN_ROUTE, now is None)]
    for c in pieces:
        who = [label_of(catalog, d) for d in catalog.with_deps([c.id]) if d in ids and d != c.id] + [c.label]
        out.append((" + ".join(who), c.route, c.id == now))
    return out


def route_line(catalog, picked) -> str:
    """The route the picks give, '' when no piece has one."""
    return next((route for _, route, given in routes(catalog, picked) if given), "")


def gives_line(line: str) -> Text:
    """'ak sessions — your past sessions' with the command bright and the words quiet."""
    head, dash, rest = line.partition(" — ")
    return Text.assemble((head, f"bold {ACCENT}"), (f" — {rest}", MUTED)) if dash else Text(line)


def command_line(step) -> str:
    """The command exactly as it will run: env words first, then argv, then where."""
    if not step.argv:
        return step.preview
    words = [f"{k}={shlex.quote(v)}" for k, v in step.env.items()] + [shlex.join(step.argv)]
    return " ".join(words) + (f"   (in {step.cwd})" if step.cwd else "")


class SetupApp(App[int]):
    CSS = CSS
    TITLE = f"{DISPLAY_NAME} setup"
    ENABLE_COMMAND_PALETTE = False  # Textual's theme menu: nothing a person installing the kit needs

    def __init__(self, detect, catalog, engine, new_run, backup):
        super().__init__(ansi_color=True)  # the terminal's own colours and background
        self.detect, self.catalog, self.engine, self.backup = detect, catalog, engine, backup
        self.new_run = new_run
        self.machine = None
        self.picked: list[str] | None = None     # catalog ids; None until the first visit to Choose
        self.plan = None
        self.run_record = None                   # the backup.Run this session applies into
        self.exit_code = 0
        self.first_runs: dict[str, tuple[str, str]] = {}  # a plugin's first run: title -> (status, detail)
        self.farewell: Text | None = None        # printed once the app has closed: what happened, what next

    def on_mount(self) -> None:
        self.push_screen(DetectScreen())

    def go(self, screen: Screen) -> None:
        self.switch_screen(screen)

    def quit_with(self, code: int | None = None) -> None:
        self.exit(self.exit_code if code is None else code)

    def run_undo(self, show: Callable[[list[dict]], None]) -> None:
        """Take back this session's run in a thread; show() gets the per-step results."""
        run = self.run_record
        if run is None:
            show([])
            return

        def work() -> None:
            results = self.backup.undo(run.id)
            self.call_from_thread(show, results)

        self.run_worker(work, thread=True, name="undo")


def undo_lines(results: list[dict]) -> Text:
    if not results:
        return Text("nothing to take back", style=MUTED)
    out = Text()
    for r in results:
        out.append(f"{'✓' if r.get('ok') else '✗'} ", style=OK if r.get("ok") else BAD)
        out.append(f"{r.get('step', '')}: {r.get('action', '')} {r.get('detail', '')}".rstrip() + "\n")
    return out


# ── 1. Check ─────────────────────────────────────────────────────────────────────────────────────────
class DetectScreen(Step):
    """What this machine has, held on screen until Enter: a missing tool is read here, with how to get it."""
    N, TITLE_TEXT = 1, "Looking at this machine"
    BINDINGS = [Binding("enter", "advance", "continue"), Binding("q", "quit_app", "quit")]

    def compose(self) -> ComposeResult:
        yield from self.frame_top()
        yield Static(id="sub")
        with VerticalScroll(id="body"):
            yield Static(Text("probing…", style=MUTED), id="rows")
            yield Static(id="next")
        yield from self.frame_bottom()

    def on_mount(self) -> None:
        self._rows: list[tuple[str, str]] = []
        self._shown = 0
        self._ready = False
        self._left = False
        super().on_mount()
        self.app.run_worker(self._probe, thread=True, name="detect")

    def check_action(self, action: str, parameters) -> bool | None:
        return action != "advance" or getattr(self, "_ready", False)

    def _probe(self) -> None:
        try:
            m = self.app.detect()
        except Exception as e:  # a probe that raises is a bug, but the person still gets the reason
            self.app.call_from_thread(self.app.exit, 1, message=f"could not look at this machine: {e}")
            return
        self.app.call_from_thread(self._found, m)

    def _found(self, m) -> None:
        from ak.setup.cli import NEEDED_TOOLS
        self.app.machine = m
        needed = {name for c in self.app.catalog.COMPONENTS for name, _ in c.needs.bins} | set(NEEDED_TOOLS)
        self._rows = machine_rows(m, self.app.catalog.MARKETPLACE, needed)
        self.set_interval(0.03, self._tick)

    def _tick(self) -> None:
        if self._shown < len(self._rows):
            self._shown += 1
            grid = Table.grid(padding=(0, 3))
            for label, value in self._rows[: self._shown]:
                tone = BAD if value == "missing" else MUTED if value.startswith(("not ", "off", "0 ", "none")) else OK
                grid.add_row(Text(label, style=MUTED), Text(value, style=tone))
            self.query_one("#rows", Static).update(grid)
        elif not self._ready:
            self._ready = True
            self.refresh_bindings()
            self.query_one("#next", Static).update(self._verdict())

    def _verdict(self) -> Text:
        """A missing uv, git or claude, each with where to get it; else the go-ahead."""
        from ak.setup.cli import NEEDED_TOOLS, TOOL_HINT
        missing = [t for t in NEEDED_TOOLS if not self.app.machine.bin(t).found]
        if not missing:
            return Text.assemble("\n", ("✓ ready", f"bold {OK}"), ("  everything setup needs is here", MUTED))
        out = Text("\n")
        for tool in missing:
            out.append(f"✗ {tool} is missing", style=f"bold {BAD}")
            out.append(f"  {TOOL_HINT[tool]}\n", style=MUTED)
        out.append("what needs it can't be picked next — continue anyway, or quit and install it first", style=MUTED)
        return out

    def action_advance(self) -> None:
        if self._ready and not self._left:
            self._left = True
            self.app.go(PickScreen())

    def action_quit_app(self) -> None:
        self.app.quit_with(0)


# ── 2. Choose ────────────────────────────────────────────────────────────────────────────────────────
class PickScreen(Step):
    """One row per thing a person can pick, marked ● picked · ○ not · ✓ installed (locked: unticking would not
    uninstall it) · – can't run here (the reason in the row). Setup's own rows and the host plugin are one line, always
    installed. Under the list, the row under the cursor: what you get, its first run, what it puts on the machine."""
    N, TITLE_TEXT = 2, "What should be installed?"
    BINDINGS = [
        Binding("enter", "next", "continue", priority=True),  # first: the main key, drawn as the badge
        Binding("space", "toggle", "pick"),
        Binding("d", "defaults", "defaults"),
        Binding("q", "quit_app", "quit"),
    ]

    def compose(self) -> ComposeResult:
        yield from self.frame_top()
        yield Static(id="sub")
        yield OptionList(*self._options(), id="left")
        yield Static(id="side")
        yield from self.frame_bottom()

    # the model: what is picked, kept closed under 'needs'
    def _options(self) -> list[Option]:
        app, cat, m = self.app, self.app.catalog, self.app.machine
        if app.picked is None:
            app.picked = list(cat.defaults(m))
        self.model: set[str] = set(app.picked)
        self.at_row: str | None = None
        self.host = host_of(cat)
        if self.host:
            self.model.add(self.host)  # always: it is what `ak setup` installs
        by_group: dict[str, list] = {}
        for c in cat.COMPONENTS:
            # a row nobody can pick ("not in the marketplace") is left out, and so is one a plugin's pick brings
            if not is_core(cat, c.id) and c.selectable and not getattr(c, "comes_with", None):
                by_group.setdefault(c.group, []).append(c)
        self.rows: set[str] = {c.id for cs in by_group.values() for c in cs}  # the ids with a row; Core's have none
        for cid in self.rows:
            if self._installed(self._component(cid)):
                self.model.add(cid)
        self.width = max((len(c.label) for cs in by_group.values() for c in cs), default=0)
        out = [Option(self._core_row(), id="#core", disabled=True)] if any(is_core(cat, c.id) for c in cat.COMPONENTS) else []
        for group in [g for g in GROUPS if g in by_group] + [g for g in by_group if g not in GROUPS]:
            out.append(Option(Text(""), id=f"#gap-{group}", disabled=True))
            out.append(Option(Text.assemble((f"   {group.upper()}", "bold"), (f"   {GROUP_WORDS.get(group, '')}", MUTED)),
                              id=f"#{group}", disabled=True))
            for c in by_group[group]:
                out.append(Option(self._row(c), id=c.id, disabled=self._installed(c)))
        return out

    def _core_row(self) -> Text:
        """'● ak   the ak command and the kit's source — always installed', ✓ once it is in place."""
        core = [c.id for c in self.app.catalog.COMPONENTS if is_core(self.app.catalog, c.id)]
        try:
            in_place = not self.app.engine.plan(self.app.machine, core).steps
        except Exception:  # a plan that can't be made here is the engine's to explain at Confirm
            in_place = False
        self.core_in_place = in_place
        return Text.assemble(("   ✓  " if in_place else "   ●  ", OK if in_place else ACCENT),
                             (CLI.ljust(self.width), "bold"), (f"   {CORE_WORDS} — ", MUTED),
                             ("in place" if in_place else "always installed", MUTED))

    def _row(self, c, cursor: bool = False) -> Text:
        """'› ●  memory          your agent keeps notes and finds them later'."""
        reason = self._refusal(c)
        if self._installed(c):
            mark, name, aside, aside_style = "✓", OK, "installed", OK
        elif reason:
            mark, name, aside, aside_style = "–", MUTED, reason, WARN
        elif c.id in self.model:
            mark, name, aside, aside_style = "●", "", getattr(c, "summary", ""), MUTED
        else:
            mark, name, aside, aside_style = "○", MUTED, getattr(c, "summary", ""), MUTED
        mark_style = {"✓": OK, "–": WARN, "●": ACCENT}.get(mark, MUTED)
        out = Text.assemble((" › " if cursor else "   ", f"bold {ACCENT}"), (mark, mark_style), "  ")
        out.append(c.label.ljust(self.width), style=f"bold {name}".strip() if cursor else name)
        if aside:
            out.append(f"   {aside}", style=aside_style)
        return out

    def _redraw(self) -> None:
        lst = self.query_one("#left", OptionList)
        for cid in self.rows:
            lst.replace_option_prompt(cid, self._row(self._component(cid), cursor=cid == self.at_row))
        if self.at_row:
            self._describe(self.at_row)  # its "● picked" / "○ left out" follows the space

    def _refusal(self, c) -> str | None:
        """Why c can't be picked here: not selectable at all, or this machine can't run it."""
        if not c.selectable:
            return c.note or "shown for information only"
        return self.app.catalog.blocked(c, self.app.machine)

    def _installed(self, c) -> bool:
        return bool(c.plugin) and f"{c.plugin}@{self.app.catalog.MARKETPLACE}" in self.app.machine.plugins

    def _component(self, cid: str):
        return next(c for c in self.app.catalog.COMPONENTS if c.id == cid)

    def _label_of(self, cid: str) -> str:
        return self._component(cid).label

    @property
    def picked(self) -> list[str]:
        """The ids picked now, in catalog order."""
        return [c.id for c in self.app.catalog.COMPONENTS if c.id in self.model]

    @property
    def chosen(self) -> list[str]:
        """The picks a person sees as rows: the picked ids without Core's or what a plugin brings."""
        return [cid for cid in self.picked if cid in self.rows]

    def on_mount(self) -> None:
        self._armed: str | None = None   # the id whose untick waits for a second space (it drops others)
        self._said = Text()
        super().on_mount()
        lst = self.query_one("#left", OptionList)
        first = next(i for i, o in enumerate(lst.options) if not o.disabled)
        lst.highlighted = first
        lst.focus()
        self._at(lst.get_option_at_index(first).id)

    def _at(self, cid: str | None) -> None:
        if cid is None or cid.startswith("#"):
            return
        old, self.at_row = self.at_row, cid
        lst = self.query_one("#left", OptionList)
        for x in {old, cid} - {None}:
            lst.replace_option_prompt(x, self._row(self._component(x), cursor=x == cid))
        self._describe(cid)
        self._count()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._armed = None
        self._said = Text()
        self._at(event.option.id)

    def _describe(self, cid: str) -> None:
        """Under the list, the row under the cursor: whether it is picked, what you get, its first run, what it puts on
        this machine, what it brings along, a caveat."""
        c = self._component(cid)
        head = Text.assemble((c.label, "bold"), (f" — {c.summary}" if getattr(c, "summary", "") else ""))
        reason = self._refusal(c)
        if reason:
            state = Text(f"can't install here: {reason}", style=BAD)
        elif self._installed(c):
            state = Text("✓ already installed", style=OK)
        elif cid in self.model and (needers := self._drops(cid, self.model)):
            state = Text(f"● picked — {', '.join(map(self._label_of, needers))} needs it", style=ACCENT)
        elif cid in self.model:
            state = Text("● picked — space leaves it out", style=ACCENT)
        else:
            state = Text("○ left out — space picks it", style=MUTED)
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style=MUTED, no_wrap=True)
        grid.add_column(ratio=1)
        for i, line in enumerate(getattr(c, "gives", ())):
            grid.add_row("you get" if i == 0 else "", gives_line(line))
        first = getattr(c, "first_run", None)
        if first and (first.reads != "history" or history_words(self.app.machine)):
            size = f" ({history_words(self.app.machine)})" if first.reads == "history" else ""
            grid.add_row("first run", Text.assemble(first.title + size, (" — keeps going in the background", MUTED)))
        for part in self.app.catalog.COMPONENTS:  # a piece that is part of this row; a bare settings switch says nothing
            if getattr(part, "comes_with", None) == cid and getattr(part, "summary", ""):
                grid.add_row("comes with", Text.assemble((part.label, "bold"), (f" — {part.summary}", MUTED)))
        grid.add_row("on your machine", Text(getattr(c, "changes", "") or NOTHING_ELSE))
        brings = [self._label_of(d) for d in self.app.catalog.with_deps([cid]) if d != cid and d in self.rows]
        if brings:
            grid.add_row("brings along", Text(f"{', '.join(brings)} — picked with it", style=ACCENT))
        if c.note:
            grid.add_row("note", Text(c.note, style=WARN))
        if getattr(c, "route", ""):  # a piece that changes how claude starts: every way it can, the picks' one marked
            ways = Table.grid(padding=(0, 2))
            ways.add_column(no_wrap=True)
            ways.add_column(ratio=1)
            for who, route, given in routes(self.app.catalog, self.model):
                ways.add_row(Text.assemble(("● " if given else "○ ", ACCENT if given else MUTED),
                                           (who, f"bold {ACCENT}" if given else MUTED)),
                             Text(route, style="" if given else MUTED))
            grid.add_row("how claude starts", ways)
        shown = Group(Text.assemble(head, "   ", state), grid)
        self.query_one("#side", Static).update(shown)
        self.side_text = plain(shown)

    def _count(self) -> None:
        picked = [self._component(cid) for cid in self.chosen]
        have = sum(self._installed(c) for c in picked)
        out = len(self.rows) - len(picked)
        text = Text.assemble((f"● {len(picked) - have} to install", f"bold {ACCENT}"),
                             (f"   ✓ {have} already installed" if have else "", OK),
                             (f"   ○ {out} left out" if out else "", MUTED))
        if self._said.plain:
            text.append("\n")
            text.append_text(self._said)
        self.say(text)

    def _say(self, words: str, style: str = WARN) -> None:
        self._said = Text(words, style=style)
        self._count()

    def action_toggle(self) -> None:
        lst = self.query_one("#left", OptionList)
        if lst.highlighted is None:
            return
        option = lst.get_option_at_index(lst.highlighted)
        cid = option.id
        if not cid or cid.startswith("#") or option.disabled:
            return
        self._change(self.model - {cid} if cid in self.model else self.model | {cid})

    def _drops(self, cid: str, want: set[str]) -> list[str]:
        cat = self.app.catalog
        return [c.id for c in cat.COMPONENTS if c.id in want and c.id != cid and cid in cat.with_deps([c.id])]

    def _change(self, now: set[str]) -> None:
        """Keep the picks closed under 'needs': a pick brings its dependencies, an unpick drops its dependents —
        after a second space, when it would drop any."""
        cat = self.app.catalog
        added, removed = now - self.model, self.model - now
        if len(removed) == 1 and not added:
            cid = next(iter(removed))
            drops = self._drops(cid, now)
            if drops and self._armed != cid:
                self._armed = cid
                self._say(f"unpicking {self._label_of(cid)} also unpicks {', '.join(map(self._label_of, drops))}"
                          " — press space again to do it")
                return
        self._armed = None
        want = set(now)
        said: list[str] = []
        for cid in sorted(added):
            c = self._component(cid)
            reason = self._refusal(c)
            needed = []
            if not reason:
                needed = [d for d in cat.with_deps([cid]) if d != cid]
                reason = next((f"needs {self._label_of(d)}, which can't be installed here: {r}" for d in needed
                               if (r := self._refusal(self._component(d)))), None)
            if reason:
                want.discard(cid)
                said.append(f"{c.label}: {reason}")
                continue
            more = [d for d in needed if d not in want and d in self.rows]  # Core, and what a plugin brings, come silently
            want.update(needed)
            if more:
                said.append(f"{c.label} needs {', '.join(map(self._label_of, more))} — picked too")
        for cid in sorted(removed):
            dependents = self._drops(cid, want)
            want.difference_update(dependents)
            if dependents:
                said.append(f"{', '.join(map(self._label_of, dependents))} needs {self._label_of(cid)} — unpicked too")
        want -= {c.id for c in cat.COMPONENTS if getattr(c, "comes_with", None) and c.comes_with not in want}
        self.model = want
        self._said = Text("\n".join(said), style=MUTED)
        self._redraw()
        self._count()

    def action_defaults(self) -> None:
        self.model = set(self.app.catalog.defaults(self.app.machine)) | {
            cid for cid in self.rows if self._installed(self._component(cid))} | ({self.host} if self.host else set())
        self._redraw()
        self._say("back to the defaults", style=MUTED)

    def action_next(self) -> None:
        if not self.chosen and not self.host:  # with a host there is always something: the ak command
            self._say("nothing picked — space picks the row under the cursor")
            return
        self.app.picked = self.picked
        self.app.go(ReviewScreen())

    def action_quit_app(self) -> None:
        self.app.quit_with(0)


# ── 3. Confirm ───────────────────────────────────────────────────────────────────────────────────────
IN_PLACE = "already in place"  # engine.IN_PLACE: the skip that means nothing is left to do
STEP_WORDS = (("release", "download the kit"), ("marketplace", "tell Claude Code where the plugins are"),
              ("plugin:", "install {n} plugin{s}"), ("ak-tool", f"install the {CLI} command"),
              ("ak-path", f"put {CLI} on your PATH"), ("venv:", "build {n} plugin environment{s}"),
              ("settings", "change settings.json"), ("shim", "install the claude shim"),
              ("gateway:", "set up the gateway"), ("first-run:", "{n} first run{s}"))


def step_aside(step) -> str:
    """What a step costs beyond a second or two, beside its title: a browser, up to a minute, or work that goes on."""
    if step.interactive:
        return "  · you, in your browser"
    if getattr(step, "background", False):
        return "  · keeps going in the background if you leave"
    return "  · up to a minute" if step.long else ""


def label_of(catalog, cid: str) -> str:
    """A component's name as a person reads it ("ak on your PATH"); a plugin's first run is "<plugin>'s first run"."""
    if cid.endswith(".first-run"):
        return f"{cid.removesuffix('.first-run')}'s first run"
    c = getattr(catalog, "BY_ID", {}).get(cid)
    return c.label if c else cid


def plan_summary(steps, runs_dir: str) -> Text:
    """The plan in one breath: what will happen, what takes long, and the way back."""
    parts = []
    for prefix, words in STEP_WORDS:
        n = sum(s.id.startswith(prefix) for s in steps)
        if n and prefix == "first-run:":  # a plugin's own work: its title says it best ("index your Claude Code history")
            parts += [s.title.split(" (")[0] for s in steps if s.id.startswith(prefix)]
        elif n:
            parts.append(words.format(n=n, s="s" * (n != 1)))
    known = sum(any(s.id.startswith(p) for p, _ in STEP_WORDS) for s in steps)
    if known < len(steps):
        parts.append(f"{len(steps) - known} more step{'s' * (len(steps) - known != 1)}")
    out = Text()
    for i, part in enumerate(parts):
        out.append(" · " if i else "", style=MUTED)
        out.append(part, style=ACCENT)
    out.append("\n")
    slow = [s for s in steps if s.long or s.interactive]
    if slow:
        out.append(f"{len(slow)} step{'s' * (len(slow) != 1)} take{'s' * (len(slow) == 1)} longer · ", style=WARN)
    out.append(f"every file is backed up first to {runs_dir} — {cmd('setup undo')} puts it back", style=MUTED)
    return out


def ready_lines(plan, catalog, left_out=(), picked=None) -> Table:
    """The plan as a person decides on it: what you get (each with its one line), the work that follows, what it puts
    on this machine, how the claude you type will start, what asks for you, and what is left out. left_out: (label, why)
    of the rows not picked; picked: every id picked (default: the plan's), for the route."""
    by_id = {c.id: c for c in catalog.COMPONENTS}
    shown: list[str] = []
    for step in plan.steps:
        c = by_id.get(step.component)
        if step.id.startswith("first-run:") or (c and c.group == CORE) or step.component in shown:
            continue
        shown.append(step.component)
    parts = {cid: by_id[cid].comes_with for cid in shown if getattr(by_id.get(cid), "comes_with", None) in shown}
    shown = [cid for cid in shown if cid not in parts]  # the shim is said as part of the gateway, not beside it
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=MUTED, no_wrap=True)
    grid.add_column(ratio=1)

    def named(rows: list[tuple[str, Text]]) -> Table:
        inner = Table.grid(padding=(0, 2))
        inner.add_column(no_wrap=True)
        inner.add_column(ratio=1)
        width = max(len(name) for name, _ in rows)
        for name, text in rows:
            inner.add_row(Text(name.ljust(width), style=f"bold {ACCENT}"), text)
        return inner

    def summary(cid: str) -> Text:
        out = Text(getattr(by_id.get(cid), "summary", "") or "", style=MUTED)
        for part, owner in parts.items():
            if owner == cid:
                out.append(f" · comes with the {label_of(catalog, part)}", style=MUTED)
        return out

    if shown:
        grid.add_row("you get", named([(label_of(catalog, cid), summary(cid)) for cid in shown]))
    for step in plan.steps:
        if getattr(step, "background", False):
            grid.add_row("then", Text.assemble(step.title, (" — keeps going in the background if you leave", MUTED)))
    changes = [(label_of(catalog, cid), Text(by_id[cid].changes)) for cid in shown
               if getattr(by_id.get(cid), "changes", "")]
    changes += [("settings.json", Text.assemble("a change to it", (" — d shows the diff", MUTED)))
                for step in plan.steps if step.kind == "settings"]
    if changes:
        grid.add_row("on your machine", named(changes))
    route = route_line(catalog, plan.selected if picked is None else picked)
    if route:  # the kit has pieces that change how claude starts: say what the picks make of it, even when nothing
        grid.add_row("claude starts", Text(route) if route != PLAIN_ROUTE
                     else Text(f"as it does now — {PLAIN_ROUTE}", style=MUTED))
    for step in plan.steps:  # what needs the person, said before it happens
        if step.interactive:
            grid.add_row("asks you to", Text.assemble(step.title, (" — in your browser; setup waits for you", MUTED)))
    out = Text()
    for label, why in left_out:
        out.append(" · " if out.plain else "", style=MUTED)
        out.append(label, style="bold")
        out.append(f" ({why})" if why else "", style=MUTED)
    out.append(f"   {cmd('setup')} adds them any time" if left_out else "nothing — every piece is picked", style=MUTED)
    grid.add_row("left out", out)
    return grid


class ReviewScreen(Step):
    """Are you ready? What gets installed and what follows, in words, and one key to go: Enter. The steps one by one
    (each command, each settings diff) are behind d. An Enter that lands within READY_AFTER of arriving is the one
    that left Choose, pressed twice: it is ignored, so a double tap never installs."""
    N, TITLE_TEXT = 3, "Ready to install"
    READY_AFTER = 0.5  # seconds
    BINDINGS = [
        Binding("enter", "go", "install now", priority=True),
        Binding("i", "go", "install now", show=False),
        Binding("d", "details", "see each step"),
        Binding("b", "back", "back"),
        Binding("q", "quit_app", "quit"),
    ]

    def key_words(self, b: Binding) -> str:
        plan = getattr(self, "plan", None)
        if b.action == "go" and plan is not None and not plan.steps:
            return "check what is installed"
        if b.action == "details" and getattr(self, "detailed", False):
            return "hide the steps"
        return b.description

    def compose(self) -> ComposeResult:
        yield from self.frame_top()
        yield Static(id="summary")
        yield Static(id="words")
        yield OptionList(id="left")
        yield Static(id="preview")
        yield from self.frame_bottom()

    def on_mount(self) -> None:
        app = self.app
        app.plan = self.plan = app.engine.plan(app.machine, app.picked)
        steps = self.plan.steps
        self.preview_text = ""
        self.at_row = 0
        self.detailed = False
        self.opened = time.monotonic()
        lst = self.query_one("#left", OptionList)
        for i, s in enumerate(steps):
            lst.add_option(Option(self._row(s, i == 0)))
        lst.display = False
        self.query_one("#preview", Static).display = False
        words = self.query_one("#words", Static)
        words.update(plan_summary(steps, app.backup.runs_dir()) if steps else "")
        words.display = False
        held = [cid for cid, why in self.plan.skipped.items() if why == IN_PLACE and not is_core(app.catalog, cid)]
        left = {cid: why for cid, why in self.plan.skipped.items() if why != IN_PLACE}
        note = Text(f"✓ already in place: {', '.join(label_of(app.catalog, c) for c in held)}" if held else "", style=OK)
        for cid, why in left.items():
            note.append(("\n" if note.plain else "") + f"left out: {label_of(app.catalog, cid)} — {why}", style=WARN)
        self.say(note)
        if steps:
            self.headline("Ready to install")
            shown = Group(
                ready_lines(self.plan, app.catalog, self._left_out(), app.picked), Text(""),
                Text(f"{len(steps)} steps, usually under a minute · every file is backed up first — "
                     f"{cmd('setup undo')} takes it all back", style=MUTED), Text(""),
                Text.assemble(("press ", MUTED), (" enter ", f"bold black on {ACCENT}"), (" to install", f"bold {ACCENT}"),
                              ("   ·   d to see each step first", MUTED)))
        else:
            self.headline("Nothing to install", tone=OK)
            shown = Text.assemble(("✓ ", OK), "everything you picked is already in place")
        self.query_one("#summary", Static).update(shown)
        self.summary_text = plain(shown)
        super().on_mount()

    def _left_out(self) -> list[tuple[str, str]]:
        """(label, why) of each row Choose showed and the person did not pick; why is set when it can't run here."""
        app = self.app
        picked = set(app.picked or ())
        out = []
        for c in app.catalog.COMPONENTS:
            if is_core(app.catalog, c.id) or not c.selectable or getattr(c, "comes_with", None) or c.id in picked:
                continue
            if c.plugin and f"{c.plugin}@{app.catalog.MARKETPLACE}" in app.machine.plugins:
                continue  # installed already: not left out
            out.append((c.label, app.catalog.blocked(c, app.machine) or ""))
        return out

    def _row(self, step, cursor: bool) -> Text:
        return Text.assemble((" › " if cursor else "   ", f"bold {ACCENT}"), (step.title, "bold" if cursor else ""),
                             (step_aside(step), WARN))

    def _show(self, step) -> None:
        if step.kind == "settings":
            self.preview_text = step.preview
            shown = Syntax(step.preview, "diff", theme="ansi_dark", word_wrap=True, background_color="default")
        else:
            self.preview_text = command_line(step)
            shown = Text.assemble(("$ ", MUTED), (self.preview_text, MUTED))
        self.query_one("#preview", Static).update(shown)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        lst, steps = self.query_one("#left", OptionList), self.plan.steps
        lst.replace_option_prompt_at_index(self.at_row, self._row(steps[self.at_row], False))
        self.at_row = event.option_index
        lst.replace_option_prompt_at_index(self.at_row, self._row(steps[self.at_row], True))
        self._show(steps[self.at_row])

    def action_details(self) -> None:
        """Show (or hide) every step with the command it runs or the settings diff it makes."""
        if not self.plan.steps:
            return
        self.detailed = not self.detailed
        lst = self.query_one("#left", OptionList)
        lst.display = self.detailed
        self.query_one("#preview", Static).display = self.detailed
        self.query_one("#words", Static).display = self.detailed
        if self.detailed:
            lst.highlighted = self.at_row
            self._show(self.plan.steps[self.at_row])
            lst.focus()
        self.refresh_bindings()

    def action_go(self) -> None:
        if time.monotonic() - self.opened < self.READY_AFTER:
            return  # the Enter that left Choose, pressed twice
        self.app.go(ApplyScreen() if self.plan.steps else VerifyScreen())

    def action_back(self) -> None:
        self.app.go(PickScreen())

    def action_quit_app(self) -> None:
        self.app.quit_with(0)


# ── 4. Install ───────────────────────────────────────────────────────────────────────────────────────
class ApplyScreen(Step):
    """Runs the plan in a thread: every step on the left with its state, the output on the right.
    A failed step stops it; the person retries, skips it or undoes."""
    N, TITLE_TEXT = 4, "Installing"
    BINDINGS = [
        Binding("enter", "next", "check the result"),
        Binding("r", "retry", "try again"),
        Binding("s", "skip", "skip it"),
        Binding("u", "undo", "undo this run"),
        Binding("q", "quit_app", "quit"),
    ]
    # following: a background step (a plugin's first run) is running; Enter leaves it going and finishes setup
    ON = {"running": (), "following": ("next",), "failed": ("retry", "skip", "undo", "quit_app"),
          "done": ("next", "undo", "quit_app"), "undone": ("quit_app",)}
    PROGRESS = re.compile(r"\[(\d+)/(\d+)\]")  # "[12/26] …": a first run's own count, shown on its row

    def compose(self) -> ComposeResult:
        yield from self.frame_top()
        yield Static(id="sub")
        with Horizontal(id="body"):
            yield VerticalScroll(Static(id="steps"), id="steps-box")
            yield RichLog(id="log", wrap=True, markup=False)
        yield from self.frame_bottom()

    def on_mount(self) -> None:
        self.state = "running"
        self.finished: set[str] = set()   # step ids done or skipped
        self.failed = None
        self.status_of: dict[str, tuple[str, str]] = {}  # step id -> (state, detail): running, done, skipped, failed
        self.following = None             # the background step running now
        self.since = 0.0                  # when it started (time.monotonic)
        self.count = ""                   # its own "12/26", from its output
        super().on_mount()
        self._draw_steps()
        self.set_interval(1, self._tick)
        self._start(list(self.app.plan.steps))

    def check_action(self, action: str, parameters) -> bool | None:
        return action in self.ON.get(getattr(self, "state", "running"), ())

    def key_words(self, b: Binding) -> str:
        if b.action == "next" and getattr(self, "state", "") == "following":
            return "finish setup — this keeps going in the background"
        return b.description

    def _here(self) -> bool:
        """This screen is the one showing: an event from the apply thread after the person left is dropped."""
        try:
            return self.host.screen is self
        except ScreenStackError:  # the app is closing: no screen shows
            return False

    def _tick(self) -> None:
        if self.following is not None and self._here():
            self._draw_steps()

    def _set_state(self, state: str) -> None:
        self.state = state
        self.refresh_bindings()

    def _draw_steps(self) -> None:
        """Every step of the plan, one line each: ✓ done · › running · – skipped · ✗ failed (with why) · ○ waiting."""
        marks = {"done": ("✓", OK, OK), "running": ("›", f"bold {ACCENT}", f"bold {ACCENT}"), "skipped": ("–", WARN, WARN),
                 "failed": ("✗", BAD, f"bold {BAD}"), "background": ("↻", ACCENT, ACCENT)}
        out = Text()
        steps = self.app.plan.steps
        for step in steps:
            state, detail = self.status_of.get(step.id, ("waiting", ""))
            mark, mark_style, text_style = marks.get(state, ("○", MUTED, MUTED))
            out.append(f"{mark}  ", style=mark_style)
            out.append(step.title, style=text_style)
            if state == "failed" and detail:
                out.append(f"  {detail}", style=BAD)
            if state == "running" and step is self.following:
                out.append(f"  {self.count + ' · ' if self.count else ''}{int(time.monotonic() - self.since)}s", style=MUTED)
            out.append("\n")
        self.query_one("#steps", Static).update(out)
        done = sum(1 for s in steps if self.status_of.get(s.id, ("",))[0] in ("done", "skipped"))
        if self.state == "running":
            self.say(Text.assemble((f"✓ {done}", OK), (f" of {len(steps)} done", MUTED)))
        elif self.state == "following":
            self.say(Text.assemble((f"✓ {done}", OK), (f" of {len(steps)} done · ", MUTED),
                                   (f"{self.following.title} keeps going even if you finish setup now", ACCENT)))

    def _start(self, steps: list) -> None:
        # The app, held: the apply thread reaches it through this, never through self.app, which looks the app up
        # through the screen and fails once the person has left the screen while a first run still reports.
        self.host = self.app
        let_go = getattr(self.host.engine, "LET_GO", None)
        if let_go is not None:
            let_go.clear()
        self._set_state("running")
        self.failed = None
        self.host.run_worker(lambda: self._work(steps), thread=True, name="apply")

    def _work(self, steps: list) -> None:
        app = self.host
        if app.run_record is None:
            app.run_record = app.new_run()
        sub = dataclasses.replace(app.plan, steps=steps)
        extra = {"run_interactive": self._hand_over} if "run_interactive" in inspect.signature(
            app.engine.apply).parameters else {}
        ok = app.engine.apply(sub, app.run_record, self._emit, **extra)
        self._tell(self._finished, ok)

    def _emit(self, event: str, **data) -> None:
        self._tell(self._on_event, event, data)

    def _tell(self, fn, *args) -> None:
        """fn(*args) on the app's thread; dropped once the app has closed (setup finished while a first run still
        reports: the step goes on, nobody is watching)."""
        if self.host.is_running:
            try:
                self.host.call_from_thread(fn, *args)
            except RuntimeError:  # closed between the look and the call
                if self.host.is_running:
                    raise

    def _hand_over(self, step, runner: Callable[[], int]) -> int:
        """Run an interactive step (a browser login) with the terminal handed back to it."""
        return self.host.call_from_thread(self._with_terminal, step, runner)

    def _with_terminal(self, step, runner: Callable[[], int]) -> int:
        try:
            with self.app.suspend():
                hand_over_words(step)
                return runner()
        except SuspendNotSupported:
            return runner()

    def _on_event(self, event: str, data: dict) -> None:
        if not self._here():
            return
        log = self.query_one("#log", RichLog)
        step = data.get("step")
        if event == "step_start":
            self.status_of[step.id] = ("running", "")
            log.clear()  # one step's output at a time: the step running, or the one that failed
            log.write(Text(step.title, style=f"bold {ACCENT}"))
            if getattr(step, "background", False):
                self.following, self.since, self.count = step, time.monotonic(), ""
                self._set_state("following")
        elif event == "line":
            log.write(Text("  " + data["text"], style=MUTED))
            found = self.PROGRESS.search(data["text"]) if step is self.following else None
            if found:
                self.count = f"{found[1]}/{found[2]}"
        elif event == "step_done":
            status = data["status"]
            self.status_of[step.id] = (status, data.get("detail", ""))
            if getattr(step, "background", False):
                took = f"done in {int(time.monotonic() - self.since)}s" if status == "done" else data.get("detail", "")
                self.app.first_runs[step.title] = (status, took)
                self.following = None
                self._set_state("running")
            if status == "failed":
                self.failed = step
                log.write(Text(f"✗ {data.get('detail', '')}".rstrip(), style=BAD))
            else:
                self.finished.add(step.id)
        self._draw_steps()

    def _finished(self, ok: bool) -> None:
        if not self._here():
            return
        if ok:
            self.headline("Installed", tone=OK)
            self._set_state("done")
            self.say(Text("every step ran — press enter to check the result", style=MUTED))
        else:
            title = self.failed.title if self.failed else "a step"
            self.headline("Stopped", tone=BAD)
            self._set_state("failed")
            self.say(Text.assemble(("✗ ", BAD), (title, "bold"), " failed", (" — its output is on the right", MUTED)))

    def _remaining(self) -> list:
        return [s for s in self.app.plan.steps if s.id not in self.finished]

    def action_retry(self) -> None:
        self._start(self._remaining())

    def action_skip(self) -> None:
        if self.failed is not None:
            self.finished.add(self.failed.id)
            self.status_of[self.failed.id] = ("skipped", "")
            self._draw_steps()
        rest = self._remaining()
        if rest:
            self._start(rest)
        else:
            self._finished(True)

    def action_undo(self) -> None:
        self.query_one("#log", RichLog).write(Text("taking this run back…", style="bold"))
        self.app.run_undo(self._undone)

    def _undone(self, results: list[dict]) -> None:
        self.query_one("#log", RichLog).write(undo_lines(results))
        self.headline("Taken back")
        self._set_state("undone")
        self.say(Text("this run is undone", style=MUTED))

    def action_next(self) -> None:
        """Done: check the result. A first run still going: let it go on its own, and check the result."""
        if self.state == "following" and self.following is not None:
            let_go = getattr(self.app.engine, "LET_GO", None)
            if let_go is not None:
                let_go.set()
            self.app.first_runs[self.following.title] = ("background", "keeps going in the background")
        self.app.go(VerifyScreen())

    def action_quit_app(self) -> None:
        self.app.quit_with(1 if self.state == "failed" else 0)


def hand_over_words(step) -> None:
    """Printed when setup gives the terminal to a step that needs the person: what this is, and that setup comes back.
    What came before it on the terminal (the `uvx` build lines) is still there: this says where the step starts."""
    from ak import ui
    ui.console.print(Text.assemble("\n", (f"── {DISPLAY_NAME} setup · {step.title} ", f"bold {ACCENT}"), ("─" * 12, ACCENT)))
    if step.kind == "login":
        ui.console.print(Text("   a browser opens; where none can (ssh, a container), open the link below and paste the code "
                              "it gives you back here", style=MUTED))
    ui.console.print(Text("   setup comes back here when this ends\n", style=MUTED))


def next_line(picked: list[str]) -> Text:
    """What to do once setup is done: a new terminal, claude; `ak` to see what is installed."""
    out = Text.assemble(("next  ", f"bold {ACCENT}"), "open a new terminal → run ", ("claude", f"bold {ACCENT}"),
                        ("   ·   ", MUTED), (CLI, f"bold {ACCENT}"), (" shows what is installed", MUTED))
    if "memory" in picked:
        out.append(" → ")
        out.append(cmd("memory try"), style=f"bold {ACCENT}")
    return out


def farewell(failed: int, ran: bool, first_runs: dict[str, tuple[str, str]], got=()) -> Text:
    """What stays in the terminal once setup closes: the outcome, what each pick gives (got: (label, its first gives
    line)), the next command, and work still going."""
    out = Text()
    if failed:
        out.append(f"✗ {failed} check{'s' * (failed != 1)} failed", style=f"bold {BAD}")
        out.append(f" — {cmd('setup status')} shows which, {cmd('setup')} fixes them", style=MUTED)
    else:
        out.append(f"✓ {DISPLAY_NAME} is set up" if ran else f"✓ {DISPLAY_NAME} was already in place", style=f"bold {OK}")
        width = max((len(label) for label, _ in got), default=0)
        for label, line in got:
            out.append(f"\n  {label.ljust(width)}  ", style=MUTED)
            out.append_text(gives_line(line))
        out.append("\n\n  next  " if got else "\n  next  ", style=MUTED)
        out.append("open a new terminal, then run ")
        out.append("claude", style=f"bold {ACCENT}")
        out.append("\n        ", style=MUTED)
        out.append(CLI, style=f"bold {ACCENT}")
        out.append(" shows what is installed", style=MUTED)
    for title, (status, _) in first_runs.items():
        if status == "background":
            out.append("\n  ↻  ", style=ACCENT)
            out.append(title)
            out.append(f" keeps going in the background — {CLI} shows how far it is", style=MUTED)
    return out


def first_run_lines(first_runs: dict[str, tuple[str, str]]) -> Text:
    """One line per plugin first run: done, still going on its own, or failed (with how to run it again)."""
    out = Text()
    for title, (status, detail) in first_runs.items():
        if out.plain:
            out.append("\n")
        if status == "background":
            out.append("↻  ", style=ACCENT)
            out.append(title, style="bold")
            out.append(f" keeps going in the background — {CLI} shows how far it is", style=MUTED)
        elif status == "failed":
            out.append("✗  ", style=BAD)
            out.append(title, style=f"bold {BAD}")
            out.append(f"  {detail}" if detail else "", style=BAD)
        else:
            out.append("✓  ", style=OK)
            out.append(title, style=OK)
            out.append(f"  {detail}" if detail else "", style=MUTED)
    return out


# ── 5. Done ──────────────────────────────────────────────────────────────────────────────────────────
class VerifyScreen(Step):
    """Looks at the machine again and checks every pick. A failed check can be fixed from here (r: back to Confirm
    with a fresh look, so only what is still missing is planned); Enter ends setup."""
    N, TITLE_TEXT = 5, "Checking the result"
    BINDINGS = [
        Binding("enter", "quit_app", "finish"),
        Binding("r", "fix", "fix what failed"),
        Binding("u", "undo", "undo this run"),
    ]

    def compose(self) -> ComposeResult:
        yield from self.frame_top()
        yield Static(id="sub")
        with VerticalScroll(id="body"):
            yield Static(Text("looking again…", style=MUTED), id="findings")
        yield Static(id="next")
        yield from self.frame_bottom()

    def on_mount(self) -> None:
        self.findings: list[dict] = []
        super().on_mount()
        self.app.run_worker(self._check, thread=True, name="verify")

    def check_action(self, action: str, parameters) -> bool | None:
        if action == "fix":
            return any(f["level"] == "fail" for f in getattr(self, "findings", []))
        if action == "undo":
            return self.app.run_record is not None and not getattr(self, "undone", False)
        return True

    def _check(self) -> None:
        app = self.app
        m = app.detect()
        found = app.engine.verify(m, app.picked)
        app.call_from_thread(self._show, found, m)

    def _show(self, found: list[dict], m=None) -> None:
        if m is not None:
            self.app.machine = m  # what r plans from: the machine as it is now
        self.findings = found
        grid = Table.grid(padding=(0, 2))
        cat = self.app.catalog
        core = [f for f in found if is_core(cat, f["check"])]
        if core and all(f["level"] == "pass" for f in core):  # the kit, its marketplace, the ak command: one line
            grid.add_row(Text("✓", style=OK), Text(CLI, style=OK), Text(f"{CORE_WORDS}, in place", style=MUTED))
            found_rows = [f for f in found if f not in core]
        else:
            found_rows = found
        got: list[tuple[str, str]] = []  # (label, its first gives line) of each pick that passes: the farewell's
        for f in found_rows:
            mark, color = LEVEL_MARK.get(f["level"], ("?", MUTED))
            bad = f["level"] == "fail"
            gives = getattr(getattr(cat, "BY_ID", {}).get(f["check"]), "gives", ())
            label = label_of(cat, f["check"])
            if f["level"] == "pass" and gives:  # what you can do with it now, not that it installed
                what = gives_line(gives[0])
            else:
                what = Text(f["what"], style="" if bad else MUTED)
                if f.get("fix") and f["level"] != "info":  # info: already says what comes next (a new terminal)
                    what.append(f"\nfix: {f['fix']}", style=ACCENT)
            if f["level"] in ("pass", "info") and gives:  # info: installed, a new terminal picks it up
                got.append((label, gives[0]))
            grid.add_row(Text(mark, style=color), Text(label, style=f"bold {color}" if bad else color), what)
        runs = first_run_lines(self.app.first_runs)
        self.query_one("#findings", Static).update(Group(grid, Text(""), runs) if runs.plain else grid)
        failed = sum(f["level"] == "fail" for f in found)
        ran = self.app.run_record is not None
        self.app.exit_code = 1 if failed else 0
        self.headline(f"{failed} check{'s' * (failed != 1)} failed" if failed
                      else "Installed — every check passes" if ran else "Nothing changed — every check passes",
                      tone=BAD if failed else OK)
        self.say(Text("r goes back to confirm with only what still fails", style=MUTED) if failed else "")
        self.query_one("#next", Static).update(next_line(self.app.picked or []) if not failed else "")
        self.app.farewell = farewell(failed, ran, self.app.first_runs, got)
        self.refresh_bindings()

    def action_fix(self) -> None:
        self.app.go(ReviewScreen())

    def action_undo(self) -> None:
        self.say(Text("taking this run back…", style=MUTED))
        self.app.run_undo(self._undone)

    def _undone(self, results: list[dict]) -> None:
        self.undone = True
        self.say(undo_lines(results))
        self.app.exit_code = 0
        self.refresh_bindings()

    def action_quit_app(self) -> None:
        self.app.quit_with()


def run(detect=None, catalog=None, engine=None, new_run=None, backup=None) -> int:
    """Open the setup app; its exit code is the verb's. The arguments are the seams a test fakes."""
    from ak.setup import backup as backup_mod, catalog as catalog_mod, engine as engine_mod, machine
    backup = backup or backup_mod
    app = SetupApp(detect or machine.detect, catalog or catalog_mod, engine or engine_mod,
                   new_run or backup_mod.Run.new, backup)
    code = app.run() or 0
    if app.farewell is not None:  # reached the last screen: the outcome stays in the scrollback
        from ak import ui
        ui.console.print(app.farewell)
    return code
