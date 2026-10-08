# COPY of plugins/ak/src/ak/ui.py: edit that file, never this one (scripts/copies.py)
"""ui — the one output door for `ak` and every CLI mounted under it.

A verb builds ONE data shape and calls ONE renderer. The renderer decides how it
lands, from the mode chosen once at import:

  rich    a human terminal — tables, panels, colour
  plain   a pipe, an agent, `CLAUDECODE`, `NO_COLOR`, `TERM=dumb` — the compact
          LLM-friendly grammar below: no boxes, no wrapping, no padding, no ANSI
  json    `--json` on the verb — the same data as one JSON document, exit 0

Mode, first match wins:
  1. the verb's `--json` (`ui.json_mode()` at the top of the verb)
  2. AK_OUTPUT=rich|plain|json     (AK_RICH=1 / AK_PLAIN=1 are aliases)
  3. CLAUDECODE, NO_COLOR, TERM=dumb, or stdout not a TTY  → plain
  4. rich

The plain grammar (what an LLM reads back):
  kv      one `key: value` per line; multi-line values indent under the key
  table   a header line, then one TSV row per record
  tree    the root, then children indented two spaces per level
  panel   `== title ==` then the text verbatim
  ok      `ok: …`    warn  `warn: …`    on  `on: …`    hint  `hint: …`    err  `error: …` (stderr)

Rich markup is accepted in every string and stripped in plain mode, so one call
site serves both. Nothing here reads the vault; siblings may import it freely.
"""
from __future__ import annotations

import json as _json
import os
import sys
from typing import Any, Iterable, Sequence

import click
from rich.console import Console
from rich.markup import escape as _escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from ._brand import env_name

__all__ = ["mode", "is_rich", "is_plain", "is_json", "json_mode", "json_option", "console",
           "kv", "table", "tree", "panel", "ok", "warn", "err", "on", "hint", "text", "emit",
           "escape", "plain_text"]

escape = _escape


# ── mode ─────────────────────────────────────────────────────────────────────
def _detect() -> str:
    env = os.environ
    want = (env.get(env_name("OUTPUT")) or "").strip().lower()
    if want in ("rich", "plain", "json"):
        return want
    if env.get(env_name("RICH")):
        return "rich"
    if env.get(env_name("PLAIN")):
        return "plain"
    if env.get("CLAUDECODE") or env.get("NO_COLOR") or env.get("TERM") == "dumb":
        return "plain"
    try:
        return "rich" if sys.stdout.isatty() else "plain"
    except (AttributeError, ValueError):
        return "plain"


_MODE = _detect()
_FORCED_RICH = _MODE == "rich" and not (sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False)
console = Console(force_terminal=True) if _FORCED_RICH else Console()
_stderr = Console(stderr=True, force_terminal=True) if _FORCED_RICH else Console(stderr=True)


def mode() -> str:
    return _MODE


def is_rich() -> bool:
    return _MODE == "rich"


def is_plain() -> bool:
    return _MODE == "plain"


def is_json() -> bool:
    return _MODE == "json"


def json_mode(flag: bool = True) -> None:
    """Switch to json for the rest of this process — call first thing in a verb
    that took `--json`. A no-op when flag is False so the call can be unconditional."""
    global _MODE
    if flag:
        _MODE = "json"


def json_option(f):
    """`--json` for a read verb: `@ui.json_option` above the function, then
    `ui.json_mode(as_json)` as its first line."""
    return click.option("--json", "as_json", is_flag=True,
                        help="the same data as one JSON document (exit 0)")(f)


# ── helpers ──────────────────────────────────────────────────────────────────
def plain_text(s: Any) -> str:
    """Rich markup → plain characters. Safe on non-strings."""
    s = "" if s is None else str(s)
    if "[" not in s:
        return s
    try:
        return Text.from_markup(s).plain
    except Exception:  # noqa: BLE001 — a stray '[' is text, not markup
        return s


def _out(line: str = "", err: bool = False) -> None:
    click.echo(line, err=err)


def emit(data: Any) -> None:
    """The json door. One document, exit 0 is the caller's business."""
    _out(_json.dumps(data, ensure_ascii=False, indent=2, default=str))


def text(s: str, *, err: bool = False, markup: bool = True) -> None:
    """One line, as written. Rich: printed through the console (markup honoured).
    Plain: markup stripped. Json: dropped — prose is not data."""
    if _MODE == "json":
        return
    if _MODE == "rich":
        (_stderr if err else console).print(s, markup=markup, highlight=False, soft_wrap=True)
        return
    _out(plain_text(s) if markup else s, err=err)


# ── status lines ─────────────────────────────────────────────────────────────
def ok(msg: str) -> None:
    if _MODE == "json":
        return
    text(f"[green]✓[/] {msg}") if _MODE == "rich" else _out(f"ok: {plain_text(msg)}")


def warn(msg: str, *, err: bool = False) -> None:
    if _MODE == "json":
        return
    text(f"[yellow]![/] {msg}", err=err) if _MODE == "rich" else _out(f"warn: {plain_text(msg)}", err=err)


def err(msg: str) -> None:
    """Always shown — json mode included — on stderr, so a failure is never
    mistaken for an empty result."""
    if _MODE == "rich":
        text(f"[red]✗[/] {msg}", err=True)
    else:
        _out(f"error: {plain_text(msg)}", err=True)


def on(msg: str) -> None:
    """What a write switched on — the moments a note will now reach (`ak memory new`: "Now at every
    session start · surfaces when src/x.py is edited"). Between `ok:` and `hint:`; an `on:` line for an agent."""
    if _MODE == "json":
        return
    text(f"[cyan]on[/] {msg}") if _MODE == "rich" else _out(f"on: {plain_text(msg)}")


def hint(msg: str, *, err: bool = False) -> None:
    """The next command worth typing. Dim for a human; a `hint:` line for an agent.
    `err` puts it on stderr: for a line said before the verb has parsed its own `--json`."""
    if _MODE == "json":
        return
    text(f"[dim]{msg}[/]", err=err) if _MODE == "rich" else _out(f"hint: {plain_text(msg)}", err=err)


# ── blocks ───────────────────────────────────────────────────────────────────
def kv(rows: Iterable[Sequence[Any]], *, title: str | None = None, border: str = "cyan",
       boxed: bool = True) -> None:
    """Key/value pairs. Rich: a two-column grid, in a Panel when `boxed`.
    Plain: `key: value`, a multi-line value indented under its key."""
    rows = [(str(k), "" if v is None else str(v)) for k, v in rows]
    if _MODE == "json":
        return
    if _MODE == "rich":
        t = Table(box=None, pad_edge=False, show_header=False)
        t.add_column(style="dim", no_wrap=True)
        t.add_column(overflow="fold")
        for k, v in rows:
            t.add_row(k, v)
        if boxed:
            console.print(Panel.fit(t, title=title, border_style=border, title_align="left"))
        else:
            if title:
                console.print(f"[b]{title}[/]")
            console.print(t)
        return
    if title:
        _out(f"== {plain_text(title)} ==")
    for k, v in rows:
        v = plain_text(v)
        if "\n" in v:
            _out(f"{k}:")
            for ln in v.splitlines():
                _out(f"  {ln}")
        else:
            _out(f"{k}: {v}" if v else f"{k}:")


def table(columns: Sequence[str], rows: Iterable[Sequence[Any]], *, title: str | None = None,
          styles: Sequence[str | None] | None = None, justify: Sequence[str | None] | None = None,
          box: Any = "default") -> None:
    """A list of records. Rich: a Table (headers bold, `styles`/`justify` per column).
    Plain: the header line, then TSV — one record per line, tabs never wrapped."""
    rows = [["" if c is None else str(c) for c in r] for r in rows]
    if _MODE == "json":
        return
    if _MODE == "rich":
        kw = {} if box == "default" else {"box": box, "pad_edge": False}
        # A boxless table's title is a line above it: Rich would fold a Table
        # title to the table's own width, and a narrow table has a narrow title.
        if title and box != "default":
            console.print(f"[b]{title}[/]", highlight=False)
            title = None
        t = Table(title=title, header_style="bold", title_justify="left", **kw)
        for i, c in enumerate(columns):
            t.add_column(c, style=(styles or [None] * len(columns))[i],
                         justify=(justify or [None] * len(columns))[i] or "left",
                         overflow="fold")
        for r in rows:
            t.add_row(*r)
        console.print(t)
        return
    if title:
        _out(f"== {plain_text(title)} ==")
    _out("\t".join(columns))
    for r in rows:
        _out("\t".join(plain_text(c).replace("\t", " ").replace("\n", " ") for c in r))


def tree(root: str, children: Iterable[Any], *, guide: str = "dim") -> None:
    """A root and its children. A child is a string, or `(label, [grandchildren])`.
    Rich: a Tree. Plain: the root, then two spaces of indent per level."""
    if _MODE == "json":
        return
    if _MODE == "rich":
        t = Tree(Text.from_markup(root), guide_style=guide)

        def grow(node, items):
            for it in items:
                if isinstance(it, (tuple, list)):
                    grow(node.add(Text.from_markup(str(it[0]))), it[1])
                else:
                    node.add(Text.from_markup(str(it)))
        grow(t, children)
        console.print(t)
        return

    _out(plain_text(root))

    def walk(items, depth):
        for it in items:
            if isinstance(it, (tuple, list)):
                _out("  " * depth + plain_text(it[0]))
                walk(it[1], depth + 1)
            else:
                _out("  " * depth + plain_text(it))
    walk(children, 1)


def panel(body: str, *, title: str | None = None, border: str = "cyan", subtitle: str | None = None,
          markup: bool = False) -> None:
    """A block of text. Rich: a Panel. Plain: `== title ==` then the text verbatim
    (never re-wrapped — a prompt, a diff, a file must survive a round trip)."""
    if _MODE == "json":
        return
    if _MODE == "rich":
        content = body if markup else _escape(body)
        console.print(Panel(content, title=title, border_style=border, title_align="left",
                            subtitle=subtitle, subtitle_align="right"))
        return
    if title:
        _out(f"== {plain_text(title)} ==")
    _out(plain_text(body) if markup else body)
    if subtitle:
        _out(f"-- {plain_text(subtitle)}")
