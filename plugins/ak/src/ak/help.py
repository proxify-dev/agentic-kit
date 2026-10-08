"""help — how `ak --help` (and every subcommand's) is laid out, and what a
mistyped command line gets back, in both modes.

rich-click draws boxed panels at the terminal's width. That is right for a person
and wrong for a pipe: an agent reading `ak --help` pays for box-drawing, gets
lines folded at 80 columns, and sees `[b]` markup rendered as bold it cannot see.
So when `ui.mode()` is not rich, `install()` swaps the help path for click's own
plain formatter — no boxes, no wrap.

One table, `COMMAND_GROUPS`, drives both renderings: rich-click reads it for its
panels, `plain_format_help` reads it for its sections. A command missing from the
table lands in the trailing "Plugins" section, which is where a sibling plugin's
mounted command belongs without this file knowing its name.

A usage error is answered with what to type, not only what broke (`explain()`):
the parameter it is about — what it takes, its default, its help — a corrected
command line when one can be built, and the command's own `-h`. And `-h` anywhere
on a line that fails to parse shows that command's help: `--mode --help` means
"what does --mode take", never "--mode is 'help'".
"""
from __future__ import annotations

import datetime
import re
import shlex

import click as base
import rich_click
from rich_click import rich_click as rc_config
from rich_click.rich_help_formatter import RichHelpFormatter

# relative, so a plugin's copy (scripts/copies.py) imports its own ui and groups beside it
from . import ui
from .groups import leaf as _leaf, nested as _nested

from ._brand import CLI

# Root-level grouping. Keys are the command names; the order here is the order
# shown. A mounted plugin command missing from it lands under "Plugins" (add_to_group).
# Hidden commands (an old spelling, `hidden_alias`) never show, listed or not.
COMMAND_GROUPS = [
    {"name": "Everyday", "commands": ["search", "sessions", "trace", "resolve"]},
    {"name": "Groups", "commands": ["memory", "observer", "vault", "plugin"]},
    {"name": "Setup & state", "commands": ["setup", "init", "doctor", "workspaces", "moves", "home"]},
    {"name": "Plumbing", "commands": ["gateway", "shim", "hooks", "sysprompt", "xray", "tasks"]},
]

PLAIN_WIDTH = 110  # wide enough that a help line never folds mid-phrase


# ── plain help ───────────────────────────────────────────────────────────────
class PlainFormatter(base.HelpFormatter):
    """click's own formatter, plus the two hooks rich-click's `main()` calls on
    an error. Accepts and ignores rich-click's constructor kwargs so it can stand
    in for `RichHelpFormatter` wherever the context builds one."""

    def __init__(self, *args, **kwargs):
        kwargs.pop("config", None)
        kwargs.pop("export_console_as", None)
        kwargs.pop("file", None)
        kwargs.setdefault("width", PLAIN_WIDTH)
        super().__init__(*args, **kwargs)

    def write_error(self, e: base.ClickException) -> None:
        render_error(e)

    def write_abort(self) -> None:
        self.write("aborted\n")


class PlainContext(rich_click.RichContext):
    formatter_class = PlainFormatter  # type: ignore[assignment]

    def make_formatter(self, error_mode: bool = False):  # noqa: ARG002 — rich-click's signature
        return PlainFormatter()


def _aliases(cmd) -> list[str]:
    return list(getattr(cmd, "aliases", None) or [])


def plain_format_help(self, ctx, formatter) -> None:
    """click's layout with rich markup stripped, commands in COMMAND_GROUPS order.
    rich-click stubs `RichGroup.format_commands` to `pass`, so the sections are
    written here by hand."""
    base.Command.format_usage(self, ctx, formatter)
    help_ = _plain_help(self.help or "")
    if help_:
        formatter.write_paragraph()
        with formatter.indentation():
            formatter.write_text(help_)
    al = _aliases(self)
    if al:
        formatter.write_paragraph()
        with formatter.indentation():
            formatter.write_text(f"Aliases: {', '.join(al)}")
    # Arguments — click gives them no help record; the positionals are what a
    # reader most needs explained, so each gets its line (help when it has one).
    args = [(p.human_readable_name + ("" if p.required else " (optional)"), _help_of(p))
            for p in self.get_params(ctx) if isinstance(p, base.Argument)]
    if args:
        with formatter.section("Arguments"):
            formatter.write_dl(args)
    # Options — click's own records, markup stripped from the help string BEFORE
    # click appends `[default: 20]`: stripped after, that suffix reads as a tag.
    opts = []
    for p in self.get_params(ctx):
        if isinstance(p, base.Argument):
            continue
        saved = p.help
        p.help = ui.plain_text(saved) if saved else saved
        try:
            rv = p.get_help_record(ctx)
        finally:
            p.help = saved
        if rv is not None:
            opts.append(rv)
    if opts:
        with formatter.section("Options"):
            formatter.write_dl(opts)
    if isinstance(self, base.Group):
        rows = []
        for name in self.list_commands(ctx):
            c = self.get_command(ctx, name)
            if c is None or getattr(c, "hidden", False):
                continue
            al = _aliases(c)
            label = name + (f" ({', '.join(al)})" if al else "")
            rows.append((name, label, ui.plain_text(c.get_short_help_str(90))))
        groups = groups_for(ctx)
        placed: set[str] = set()
        for g in groups:
            sel = [(label, h) for n, label, h in rows if n in g["commands"]]
            placed.update(n for n, _, _ in rows if n in g["commands"])
            if sel:
                with formatter.section(g["name"]):
                    formatter.write_dl(sel)
        rest = [(label, h) for n, label, h in rows if n not in placed]
        if rest:
            with formatter.section("Plugins" if groups else "Commands"):
                formatter.write_dl(rest)
    if self.epilog:
        # Verbatim: an Examples block is laid out by hand and must not re-wrap.
        ep = _unmark(getattr(self.epilog, "plain", None) or ui.plain_text(self.epilog))
        formatter.write_paragraph()
        for line in ep.splitlines():
            formatter.write(line.rstrip() + "\n")


def _plain_help(text: str) -> str:
    """A docstring as plain text, its `\\b` blocks kept: rich drops the marker (a control
    character) with the markup, and click then reflows a laid-out table into one paragraph."""
    return "\n\n".join("\b\n" + ui.plain_text(p[2:]) if p.startswith("\b\n") else ui.plain_text(p)
                       for p in text.split("\n\n"))


def _unmark(text: str) -> str:
    """Drop click's `\\b` keep-my-line-breaks marker: a plugin puts it on an
    Examples epilog so its standalone click help keeps the layout too; here the
    layout is kept anyway and the marker would print as a stray character."""
    return text.replace("\b\n", "").replace("\b", "")


def groups_for(ctx) -> list:
    """The grouping for this context: keyed by command path, with the root group
    matched by position too, so `python -m ak.cli` and a test's `cli()` see the
    same layout as the installed `ak`."""
    g = rc_config.COMMAND_GROUPS.get(ctx.command_path)
    if g is None and ctx.parent is None:
        g = rc_config.COMMAND_GROUPS.get(_ROOT[0])
    return g or []


_ROOT = [CLI]


# ── usage errors ─────────────────────────────────────────────────────────────
try:  # click ≥ 8.2 marks "no value given" with a sentinel rather than None
    from click.core import UNSET as _UNSET  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    _UNSET = None
_NoArgsIsHelp = getattr(base.exceptions, "NoArgsIsHelpError", ())
_NoSuchCommand = getattr(base.exceptions, "NoSuchCommand", ())


def _help_of(p) -> str:
    """A parameter's help as plain text on one line — for the Arguments list."""
    return " ".join(_help_lines(p))


def _help_lines(p) -> list[str]:
    """A parameter's help as plain lines: a paragraph is one line, a `\\b` block
    (a table of choices, laid out by hand) keeps its own line breaks."""
    out: list[str] = []
    for para in ui.plain_text(getattr(p, "help", None) or "").split("\n\n"):
        if para.startswith("\b"):
            out += [ln.rstrip() for ln in para.strip("\b\n").splitlines() if ln.strip()]
        elif para.strip():
            out.append(" ".join(para.split()))
    return out


def _name_of(p) -> str:
    if isinstance(p, base.Argument):
        return p.human_readable_name
    return ", ".join(p.opts + p.secondary_opts)


def _default_of(p, ctx) -> str | None:
    """The default as a person would type it, or None when there is nothing to say
    (no default, an off switch, an empty tuple)."""
    shown = getattr(p, "show_default", None)
    if isinstance(shown, str):
        return shown
    try:
        v = p.get_default(ctx, call=True)
    except Exception:  # noqa: BLE001 — a default that cannot be computed is not shown
        return None
    if v is None or v is _UNSET or v is False or v == () or v == "":
        return None
    if isinstance(v, (list, tuple)):
        return " ".join(map(str, v))
    return str(v)


def _takes(p) -> str:
    """What a parameter accepts, in words: its choices, a whole number, a path…"""
    if isinstance(p, base.Option) and p.is_flag:
        return "a switch (no value)"
    t = p.type
    if isinstance(t, base.Choice):
        what = " · ".join(map(str, t.choices))
    elif isinstance(t, base.IntRange):
        what = f"a whole number {t._describe_range()}"
    elif isinstance(t, base.FloatRange):
        what = f"a number {t._describe_range()}"
    elif t is base.INT or isinstance(t, base.types.IntParamType):
        what = "a whole number"
    elif t is base.FLOAT or isinstance(t, base.types.FloatParamType):
        what = "a number"
    elif t is base.BOOL or isinstance(t, base.types.BoolParamType):
        what = "true or false"
    elif isinstance(t, base.DateTime):
        what = "a date as " + " or ".join(t.formats)
    elif isinstance(t, base.File):
        what = "a file path, or - for stdin/stdout"
    elif isinstance(t, base.Path):
        what = "a folder" if (t.dir_okay and not t.file_okay) else "a file" if (t.file_okay and not t.dir_okay) else "a path"
    elif t is base.UUID or isinstance(t, base.types.UUIDParameterType):
        what = "a UUID"
    else:
        what = "text"
    if p.nargs == -1:
        return f"any number of values ({what})"
    if p.nargs > 1:
        return f"{p.nargs} values ({what})"
    return what


def _card(p, ctx) -> dict:
    return {"name": _name_of(p), "takes": _takes(p), "default": _default_of(p, ctx),
            "env": ", ".join(p.envvar) if isinstance(p.envvar, (list, tuple)) else p.envvar,
            "help": _help_lines(p)}


def _example_value(p, ctx) -> str:
    """A value that fits P, for the corrected command line: a choice other than the
    default, a number, today's date — or a `<NAME>` placeholder when only the
    person knows."""
    t, dflt = p.type, _default_of(p, ctx)
    if isinstance(t, base.Choice):
        choices = [str(c) for c in t.choices]
        return next((c for c in choices if c != dflt), choices[0])
    if isinstance(t, (base.types.IntParamType, base.types.FloatParamType)):
        if dflt is not None:
            return dflt
        lo = getattr(t, "min", None)
        return str(lo if lo is not None else 10)
    if isinstance(t, base.DateTime):
        return datetime.date.today().strftime(t.formats[0])
    return f"<{(p.metavar or p.name or 'value').upper()}>"


def _shown(tokens: list[str]) -> str:
    """Tokens as a command line to copy: quoted where the shell needs it, a
    `<PLACEHOLDER>` left bare so it reads as one."""
    return " ".join(t if re.fullmatch(r"<[A-Z0-9_.|-]+>", t) else shlex.quote(t) for t in tokens)


def _opt_index(args: list[str], names) -> int | None:
    """Index of the LAST token that is one of NAMES (`--x` or `--x=value`)."""
    for i in range(len(args) - 1, -1, -1):
        a = args[i]
        if a == "--":
            continue
        if a in names or any(a.startswith(n + "=") for n in names if n.startswith("--")):
            return i
    return None


def _commands_of(group, ctx, only=None) -> list[tuple[str, str]]:
    rows = []
    for name in group.list_commands(ctx):
        if only is not None and name not in only:
            continue
        c = group.get_command(ctx, name)
        if c is None or getattr(c, "hidden", False):
            continue
        rows.append((name, ui.plain_text(c.get_short_help_str(80))))
    return rows


def _short(group, tokens: list[str]) -> str:
    c = group
    for t in tokens:
        c = (getattr(c, "commands", None) or {}).get(t)
    return ui.plain_text(c.get_short_help_str(80)) if c is not None else ""


def _examples_of(cmd, path: str) -> list[str]:
    """The lines of a command's Examples epilog that run it — at most three."""
    ep = getattr(cmd, "epilog", None)
    if not ep:
        return []
    text = _unmark(getattr(ep, "plain", None) or ui.plain_text(ep))
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith(path)][:3]


def explain(e: base.ClickException) -> dict:
    """What to tell a person whose command line did not parse, as data:

      headline   one line: what is wrong, in their words (`--mode needs a value`)
      usage      the command's usage line
      params     cards for the parameters it is about: name, takes, default, env, help
      commands   (name, short help) rows, when the mistake was a command name
      try        a corrected command line, when one can be built from what they typed
      examples   the command's own example lines, when it was missing an argument
      more       the `-h` that explains everything
    """
    ctx = getattr(e, "ctx", None)
    x = {"headline": ui.plain_text(e.format_message()), "usage": None, "params": [], "commands": [],
         "try": None, "examples": [], "more": f"{_ROOT[0]} -h"}
    if ctx is None:
        return x
    cmd, path = ctx.command, ctx.command_path
    args = list(getattr(ctx, "_brain_args", None) or [])
    x["more"] = f"{path} -h"
    x["usage"] = " ".join([path, *cmd.collect_usage_pieces(ctx)])
    by_opt = {o: p for p in cmd.get_params(ctx) if isinstance(p, base.Option) for o in p.opts + p.secondary_opts}

    def retry(tokens):
        x["try"] = _shown([*path.split(), *tokens])

    if _NoSuchCommand and isinstance(e, _NoSuchCommand):
        name = e.command_name
        commands = getattr(cmd, "commands", None) or {}  # an old spelling is never a suggestion
        near = [n for n in e.possibilities or [] if not getattr(commands.get(n), "hidden", False)]
        deeper = _nested(cmd, name)  # an exact name one level down beats a close spelling up here
        if deeper:  # `ak purge` → the verb lives one level down: `ak trace purge`
            where = " ".join(deeper[0])
            x["headline"] = f"{path} has no command '{name}' — it lives at '{path} {where}'"
            if len(deeper) == 1 and getattr(_leaf(cmd, deeper[0]), "writes", False):
                x["headline"] += " (it writes, so it does not run on a guess)"
            x["commands"] = [(" ".join(t), h) for t, h in ((t, _short(cmd, t)) for t in deeper)]
            near = [where]
        else:
            x["headline"] = f"{path} has no command '{name}'" + (f" — did you mean '{near[0]}'?" if near else "")
            x["commands"] = _commands_of(cmd, ctx, only=near or None)
        if near and name in args:
            i = args.index(name)
            retry([*args[:i], *near[0].split(), *args[i + 1:]])
    elif isinstance(e, base.NoSuchOption):
        name, near = e.option_name, list(e.possibilities or [])
        x["headline"] = f"{path} has no option {name}" + (f" — did you mean {near[0]}?" if near else "")
        if near and near[0] in by_opt:
            x["params"] = [_card(by_opt[near[0]], ctx)]
            i = _opt_index(args, [name])
            if i is not None:
                fixed = list(args)
                fixed[i] = near[0] + fixed[i][len(name):]
                retry(fixed)
        else:
            x["params"] = [_card(p, ctx) for p in cmd.get_params(ctx)
                           if isinstance(p, base.Option) and not p.hidden and p.name != "help"]
    elif isinstance(e, base.BadOptionUsage) and e.option_name in by_opt:
        p = by_opt[e.option_name]
        needs_value = "requires" in e.message
        x["headline"] = f"{e.option_name} needs a value" if needs_value else ui.plain_text(e.format_message())
        x["params"] = [_card(p, ctx)]
        i = _opt_index(args, [e.option_name])
        if needs_value and i is not None:
            retry([*args[:i + 1], _example_value(p, ctx), *args[i + 1:]])
    elif isinstance(e, base.MissingParameter) and e.param is not None:
        p = e.param
        if isinstance(p, base.Argument):
            x["headline"] = f"{path} needs {p.human_readable_name}"
            positional = [q for q in cmd.get_params(ctx) if isinstance(q, base.Argument)]
            x["params"] = [_card(q, ctx) for q in positional]
            missing = positional[positional.index(p):] if p in positional else [p]
            retry([*args, *(f"<{q.human_readable_name}>" for q in missing if q.required)])
            x["examples"] = _examples_of(cmd, path)
        else:
            x["headline"] = f"{_name_of(p)} is required"
            x["params"] = [_card(p, ctx)]
            retry([*args, p.opts[-1], _example_value(p, ctx)])
    elif isinstance(e, base.BadParameter) and e.param is not None:
        p = e.param
        x["headline"] = f"{_name_of(p).split(', ')[-1]}: {ui.plain_text(e.message)}"
        x["params"] = [_card(p, ctx)]
        i = _opt_index(args, p.opts + p.secondary_opts) if isinstance(p, base.Option) else None
        if i is not None:
            fixed = list(args)
            if "=" in fixed[i] and fixed[i].startswith("--"):
                fixed[i] = fixed[i].split("=", 1)[0] + "=" + _example_value(p, ctx)
            elif i + 1 < len(fixed):
                fixed[i + 1] = _example_value(p, ctx)
            retry(fixed)
    else:
        m = re.match(r"Got unexpected extra arguments? \((.*)\)$", e.message)
        if m:
            extra = m.group(1)
            positional = [q for q in cmd.get_params(ctx) if isinstance(q, base.Argument)]
            x["headline"] = f"{path} does not take '{extra}'"
            x["params"] = [_card(q, ctx) for q in positional]
            # The usual cause: a phrase typed without quotes. One text argument, and
            # the extras right after its value — glue them back into one.
            words = extra.split()
            if len(positional) == 1 and positional[0].nargs == 1:
                v = ctx.params.get(positional[0].name)
                if isinstance(v, str) and v in args:
                    i = args.index(v)
                    if args[i + 1:i + 1 + len(words)] == words:
                        x["headline"] += " — quote a phrase to pass it as one value"
                        retry([*args[:i], " ".join([v, *words]), *args[i + 1 + len(words):]])
        elif isinstance(cmd, base.Group) and e.message.startswith("Missing command"):
            x["headline"] = f"{path} needs a command"
            x["commands"] = _commands_of(cmd, ctx)
    return x


def render_error(e: base.ClickException) -> None:
    """Print `explain(e)` on stderr: a short block for a person, the plain grammar
    (`error:` first, `hint:` last) for a pipe or an agent."""
    x = explain(e)
    if ui.is_rich():
        _render_rich(x)
    else:
        _render_plain(x)


def _render_plain(x: dict) -> None:
    lines = [f"error: {x['headline']}"]
    if x["usage"]:
        lines.append(f"usage: {x['usage']}")
    for c in x["params"]:
        extra = "; ".join(s for s in (c["default"] and f"default {c['default']}",
                                      c["env"] and f"env {c['env']}") if s)
        lines.append(f"{c['name']}: {c['takes']}" + (f" ({extra})" if extra else ""))
        lines += [f"  {ln}" for ln in c["help"]]
    if x["commands"]:
        lines.append("commands:")
        width = max(len(n) for n, _ in x["commands"])
        lines += [f"  {n.ljust(width)}  {h}".rstrip() for n, h in x["commands"]]
    if x["try"]:
        lines.append(f"try: {x['try']}")
    lines += [f"example: {eg}" for eg in x["examples"]]
    lines.append(f"hint: {x['more']}")
    base.echo("\n".join(lines), err=True)


def _render_rich(x: dict) -> None:
    from rich.console import Console
    from rich.padding import Padding
    from rich.table import Table
    from rich.text import Text

    con = Console(stderr=True, force_terminal=ui.console.is_terminal or None, highlight=False)
    con.print(Text.assemble(("✗ ", "red bold"), (x["headline"], "bold")))
    if x["usage"]:
        con.print(Text.assemble(("  usage  ", "dim"), x["usage"]))
    if x["params"] or x["commands"]:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="cyan", no_wrap=True)
        grid.add_column(overflow="fold")
        for c in x["params"]:
            first = Text(c["takes"], style="green")
            if c["default"]:
                first.append(f"   default {c['default']}", style="dim")
            if c["env"]:
                first.append(f"   env {c['env']}", style="dim")
            grid.add_row(c["name"], first)
            for ln in c["help"]:
                grid.add_row("", Text(ln))
        for n, h in x["commands"]:
            grid.add_row(n, Text(h))
        con.print()
        con.print(Padding(grid, (0, 0, 0, 2), expand=False))
    con.print()
    if x["try"]:
        con.print(Text.assemble(("  try    ", "dim"), (x["try"], "bold green")))
    for eg in x["examples"]:
        con.print(Text.assemble(("  e.g.   ", "dim"), eg))
    con.print(Text.assemble(("  help   ", "dim"), (x["more"], "cyan")))


# ── parse: -h wins, and every usage error knows its command ──────────────────
def _asks_for_help(ctx, args) -> bool:
    # Not get_help_option(): mid-parse, click 8.5 counts the help option as a
    # parameter that already owns -h/--help, and answers None.
    names = set(ctx.help_option_names or ())
    if not names or not getattr(ctx.command, "add_help_option", True):
        return False
    for a in args:
        if a == "--":
            return False
        if a in names:
            return True
    return False


def _patch_parse_args() -> None:
    """Wrap `click.Command.parse_args` once for the process — every command class
    (rich-click's, a plugin's plain click one) resolves it through here. It keeps
    the tokens each command was handed (the corrected command line is rebuilt from
    them), attaches the context click's parser leaves off ("Option '--mode'
    requires an argument." arrives with none, so the error could not even name its
    command), and turns a failed parse that carries -h into that command's help."""
    orig = base.Command.parse_args
    if getattr(orig, "_brain_wrapped", False):
        return

    def parse_args(self, ctx, args):
        ctx._brain_args = tokens = list(args)  # a copy: click's parser consumes ARGS in place
        try:
            return orig(self, ctx, args)
        except _NoArgsIsHelp:
            raise
        except base.UsageError as e:
            if e.ctx is None:
                e.ctx = ctx
            if not ctx.resilient_parsing and _asks_for_help(ctx, tokens):
                base.echo(ctx.get_help(), color=ctx.color)
                ctx.exit(0)
            raise

    parse_args._brain_wrapped = True  # type: ignore[attr-defined]
    base.Command.parse_args = parse_args  # type: ignore[method-assign]


def _keep_epilog_layout() -> None:
    """rich-click folds a str epilog into one paragraph, so an Examples block
    becomes a blob. A multi-line str epilog is shown as laid out — what `epilog()`
    does for this file's own verbs, applied to a mounted plugin's too (a plugin
    cannot import this module when it runs standalone)."""
    orig = rich_click.RichCommand.format_epilog
    if getattr(orig, "_brain_wrapped", False):
        return

    def format_epilog(self, ctx, formatter):
        if isinstance(self.epilog, str) and "\n" in self.epilog.strip():
            self.epilog = epilog(_unmark(self.epilog))
        orig(self, ctx, formatter)

    format_epilog._brain_wrapped = True  # type: ignore[attr-defined]
    rich_click.RichCommand.format_epilog = format_epilog  # type: ignore[method-assign]


# ── install ──────────────────────────────────────────────────────────────────
def install(root_name: str = CLI, extra_classes: tuple = (), groups: list | None = None) -> None:
    """Wire the grouping into rich-click, route every usage error through
    `render_error`, and, when the mode is not rich, swap the help path on every
    rich-click class this process uses. Class-level on purpose: a sibling plugin's
    own `RichGroup` subclass inherits all of it the moment it is mounted, without
    importing this module. A plugin's CLI run on its own calls its copy of this
    from its `main()` with its own root name and GROUPS (`[]`: one Commands panel)."""
    _ROOT[0] = root_name
    table = COMMAND_GROUPS if groups is None else groups
    rc_config.COMMAND_GROUPS[root_name] = [dict(g, commands=list(g["commands"])) for g in table]
    rc_config.USE_RICH_MARKUP = True
    rc_config.SHOW_ARGUMENTS = True
    rc_config.COMMANDS_BEFORE_OPTIONS = True
    # A sub-group's verbs are "Commands"; the root's unlisted ones are mounted
    # plugins, and add_to_group() files each under an explicit "Plugins" group.
    rc_config.COMMANDS_PANEL_TITLE = "Commands"
    _patch_parse_args()
    RichHelpFormatter.write_error = lambda self, e: render_error(e)  # type: ignore[method-assign]
    if ui.is_rich():
        _keep_epilog_layout()
        return
    for k in (rich_click.RichCommand, rich_click.RichGroup, *extra_classes):
        k.context_class = PlainContext
        k.format_help = plain_format_help


def listed(command_name: str) -> bool:
    """COMMAND_NAME already sits in a group on the root help (COMMAND_GROUPS, or one added since)."""
    return any(command_name in g["commands"] for g in rc_config.COMMAND_GROUPS.get(_ROOT[0], []))


def add_to_group(group_name: str, command_name: str) -> None:
    """Put a command mounted at runtime (a sibling plugin's) under GROUP_NAME on
    the root help, creating the group at the end of the list if it is new."""
    groups = rc_config.COMMAND_GROUPS.setdefault(_ROOT[0], [])
    for g in groups:
        if g["name"] == group_name:
            if command_name not in g["commands"]:
                g["commands"].append(command_name)
            return
    groups.append({"name": group_name, "commands": [command_name]})


def keep_only(names: set) -> None:
    """Drop from the root's grouping every command not in NAMES, and a group left empty: called once
    every verb is mounted, so COMMAND_GROUPS lists only what this `ak` has (a plugin off, or absent)."""
    groups = rc_config.COMMAND_GROUPS.get(_ROOT[0], [])
    for g in groups:
        g["commands"] = [n for n in g["commands"] if n in names]
    groups[:] = [g for g in groups if g["commands"]]


def epilog(text: str):
    """An Examples block for a command's `epilog=`. rich-click reflows a str
    epilog into one paragraph; a rich Text is rendered as laid out."""
    if ui.is_rich():
        from rich.text import Text
        return Text(text)
    return text
