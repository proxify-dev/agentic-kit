"""tracer — the shell door to the session record.

Thin git-familiar adapters over the argparse engine in `__main__.py` (which keeps
its full surface: `tracer-engine query|search|trace|projects|dialogue|index`).
These verbs remap argv and hand off. `--all-projects` is forced so the record is
cwd-independent — you replay/blame/grep from anywhere, not only inside the project
that produced the session.

Two groups, the same objects wherever they run:

    tracer sessions <verb>   the conversations: what is running, what was asked, how it went
    tracer trace <verb>      what was done in them: tool calls, a turn's steps, who touched a file

`tracer` is this plugin's own command (bin/tracer, on the Bash tool's PATH while the plugin is
enabled), and every text it prints names it. Inside the `ak` CLI the same two groups are mounted as
`ak sessions` and `ak trace` by plugins/tracer/scripts/cli.py (the `cli:` list in plugin.json), with
this root hidden as `ak tracer`. Every older spelling (`tracer replay`, `tracer inspect`, …) still
runs, unlisted: the trace skill and the Stop hook call some of them.

Output goes through this plugin's copies of ak's `ui`, `groups` and `help` (scripts/copies.py),
mounted or not (plugins/AGENTS.md §6): the tracer imports nothing from another plugin at runtime.
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

import rich_click as click
from rich.markup import escape

from tracer import __main__ as engine
from tracer import ui
from tracer.groups import FuzzyGroup, hidden_alias, writes
from ._brand import CLI, REPO_DIR, utf8_stdio
from .verbs import command, spell

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def _run_engine(argv):
    """Delegate to the argparse main by remapping sys.argv."""
    old = sys.argv
    sys.argv = ["tracer-engine", *argv]
    try:
        engine.main()
    finally:
        sys.argv = old


def _run_engine_view(argv):
    """An engine view — a session, a turn, a dialogue, a search, a blame — as the engine
    prints it. Piped, forced plain or read by an agent: those exact bytes. In a terminal: the same text with its landmarks coloured (_VIEW_HL), so
    a person scans it the way the other verbs read (plugins/AGENTS.md §1, §8)."""
    if not ui.is_rich():
        _run_engine(argv)
        return
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            _run_engine(argv)
    finally:  # a miss exits non-zero after printing what it found; that still reaches the screen
        out = buf.getvalue().rstrip("\n")
        if out:
            ui.console.print(_colorize(out, _VIEW_HL), soft_wrap=True, highlight=False)


def _load_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise click.ClickException(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _arg(*decls, help: str, **kw):
    """click.argument with a help line."""
    return click.argument(*decls, help=help, **kw)


def _fail(msg: str, code: int = 1):
    """An error line on stderr, and exit CODE."""
    ui.err(ui.escape(msg))
    raise SystemExit(code)


def _resolve(target: str) -> str:
    """The full session uuid TARGET names — a UUID, its first 8 characters, or `latest` (drill.py,
    the one resolver every verb shares). A miss says what it looked for, exit 1; an ambiguous prefix
    lists the sessions it starts, exit 2."""
    from tracer.drill import AmbiguousSession, DrillError, resolve_session

    try:
        return resolve_session(target)
    except AmbiguousSession as exc:
        _fail(str(exc), 2)
    except DrillError as exc:
        _fail(str(exc), 1)


# A flag is named after the class the record already has and takes the values the output
# already prints: grep's hit counts read "user 4, assistant 31, tool-output 52", so the
# filter is --role user. One word means one thing on every verb (--outcome as on `tracer trace`).
ROLES = ("user", "assistant", "thinking", "tool-output", "notification", "teammate")
ROLE_HELP = ("only hits in one role's words, as the hit counts print them:\n\n\b\n"
             "user          what the user typed (not tool results, task notices or teammates)\n"
             "assistant     the model's replies\n"
             "thinking      the model's thinking\n"
             "tool-output   what any tool returned (--tool X: one tool's calls and results)\n"
             "notification  a background task's completion notice\n"
             "teammate      another Claude session's message")
REPLAY_ROLE_HELP = ("only one role's turns:\n\n\b\n"
                    "user  what the user typed: every turn from the first, each prompt whole; task notices\n"
                    "      and teammate messages left out, re-sends folded (with a phrase: only hits in it)")
OUTCOME_HELP = (f"only hits in tool results that ended this way (as {command('trace')} --outcome):\n\n\b\n"
                "error  the tool failed\n"
                "ok     the tool returned")
ENTRYPOINT_HELP = ("sessions by how Claude Code was started (the transcript's entrypoint); by default the\n"
                   "ones a person starts — cli, claude-desktop, sdk-ts:\n\n\b\n"
                   "all             every session, programs too (claude -p, the SDKs)\n"
                   "sdk-cli,sdk-py  only these, by the names the `hidden:` line prints")


def _entrypoint(entrypoint: str | None, include_automated: bool) -> str | None:
    """--entrypoint, with --include-automated (its released spelling) folded in."""
    if include_automated:
        if entrypoint and entrypoint != "all":
            raise click.UsageError("--include-automated is --entrypoint all; give one of them")
        return "all"
    return entrypoint


@click.command(epilog=f"""\b
Examples:
  {command('sessions replay')} 5e0c1a2b                that session's dialogue
  {command('sessions replay')} 5e0c1a2b --role user    only what the user typed there, every turn, whole
  {command('sessions replay')} latest                  the newest session's dialogue
  {command('sessions replay')} "zvec lock"             sessions that mention it, to pick one""")
@_arg("target", help="a session UUID, its first 8 characters or latest (its dialogue), or a phrase (the sessions that match it)")
@click.option("--limit", type=int, default=None, help="max interactions (dialogue) or candidates (phrase)")
@click.option("--full", is_flag=True,
              help="every answer whole — by default only the last is, earlier ones are 500-char previews")
@click.option("--role", type=click.Choice(["user"]), help=REPLAY_ROLE_HELP)
def replay(target, limit, full, role):
    """Replay a session's dialogue by UUID, or list candidate sessions for a phrase."""
    from tracer.drill import AmbiguousSession, find_session

    try:  # a session id, else a phrase
        sid = find_session(target)
    except AmbiguousSession as exc:
        raise click.UsageError(str(exc))
    if sid:
        argv = ["dialogue", "--session", sid, "--all-projects"]
        if full:
            argv.append("--full")
    else:  # a phrase → surface the candidate interactions that match
        argv = ["search", target, "--all-projects"]
    if role:
        argv += ["--role", role]
    if limit is not None:
        argv += ["--limit", str(limit)]
    _run_engine_view(argv)


@click.command(epilog=f"""\b
Examples:
  {command('trace blame')} plugins/ak/src/ak/help.py      every session that touched it
  {command('trace blame')} help.py --operation edit,write             only the ones that changed it
  {command('trace blame')} plugins/present                            a folder: one summary per session""")
@_arg("path", help="a file's full path, or its tail (e.g. src/ak/cli.py), or a folder (e.g. plugins/present)")
@click.option("--operation", "--op", "operation",
              help="only these operations, as the output prints them: read, write, edit (comma-separated)")
@click.option("--limit", type=int, default=20, help="max results")
def blame(path, operation, limit):
    """Which sessions touched a file, and when."""
    argv = ["trace", path, "--all-projects", "--limit", str(limit)]
    if operation:
        argv += ["--op", operation]
    _run_engine_view(argv)


@click.command()
@click.option("--refresh", is_flag=True, help="re-index all projects before listing")
def log(refresh):
    """Indexed projects and their recent session activity."""
    argv = ["projects"]
    if refresh:
        argv.append("--refresh")
    _run_engine_view(argv)


@click.command(epilog=f"""\b
Examples:
  {command('sessions search')} "requires an argument"      every session where it came up
  {command('sessions search')} scratchpad --role user      only sessions where the user typed it
  {command('sessions search')} zvec --under ~/{REPO_DIR}  only sessions run under ~/{REPO_DIR}""")
@_arg("phrase", help="the words to find in past transcripts — quote a phrase")
@click.option("--limit", type=int, default=10, help="sessions shown (every session is scanned)")
@click.option("--under", help="scope to projects whose cwd is an ancestor of this path")
@click.option("--since", help="only hits from this date or time on (ISO)")
@click.option("--until", help="only hits up to this date or time, inclusive (ISO)")
@click.option("--tool", help="only hits in this tool's calls and results, by the name the hit counts print (e.g. Bash)")
@click.option("--role", type=click.Choice(ROLES), help=ROLE_HELP)
@click.option("--outcome", type=click.Choice(["error", "ok"]), help=OUTCOME_HELP)
@click.option("--errors", is_flag=True, hidden=True)  # the released spelling of --outcome error
@click.option("--include-meta", is_flag=True, help="count hits in hook output and other metadata records")
@click.option("--exclude-session", default=None, metavar="UUID",
              help="leave out this session (default: $CLAUDE_CODE_SESSION_ID; '' keeps all)")
@click.option("--entrypoint", metavar="all|NAME[,NAME]", help=ENTRYPOINT_HELP)
@click.option("--include-automated", is_flag=True, hidden=True)  # the released spelling of --entrypoint all
def grep(phrase, limit, under, since, until, tool, role, outcome, errors, include_meta, exclude_session,
         entrypoint, include_automated):
    """Which sessions talked about a phrase, ranked by hits (raw transcript scan).

    Mentions, not topics: a session that debugged a search for X says X more often than the one
    that discussed X. Which sessions were ABOUT X: [b]observer search X --by-session[/]."""
    if errors:
        if outcome and outcome != "error":
            raise click.UsageError("--errors is --outcome error; give one of them")
        outcome = "error"
    entrypoint = _entrypoint(entrypoint, include_automated)
    argv = ["search", phrase, "--all-projects", "--limit", str(limit)]
    if under:
        argv += ["--under", under]
    if since:
        argv += ["--since", since]
    if until:
        argv += ["--until", until]
    if tool:
        argv += ["--tool", tool]
    if role:
        argv += ["--role", role]
    if outcome:
        argv += ["--outcome", outcome]
    if include_meta:
        argv.append("--include-meta")
    if exclude_session is not None:
        argv += ["--exclude-session", exclude_session]
    if entrypoint:
        argv += ["--entrypoint", entrypoint]
    _run_engine_view(argv)


@click.command(epilog=f"""\b
Examples:
  {command('sessions live')}                  every open session, newest activity first
  {command('sessions show')} 97613c1a         then one of them, turn by turn""")
@click.option("--json", "as_json", is_flag=True, help="the same sessions as one JSON document")
def live(as_json):
    """Start here for what is running now: every open session, what it was asked, what it is doing.

    Exit 1 when no session is open.
    """
    from tracer import live as running

    ui.json_mode(as_json)
    raise SystemExit(print_live(running.collect()))


def print_live(got: dict) -> int:
    """`live`'s report through the door (json, plain or rich, as ui decided); the exit code: 1 when nothing is open."""
    import time

    from tracer import live as running

    code = 0 if got["sessions"] else 1
    if ui.is_json():
        ui.emit(got)
        return code
    if not got["sessions"]:
        ui.warn(f"no open sessions: nothing alive in {got['registry']}")
        return code
    look = {"name": "bold", "id": "cyan", "label": "dim", "dim": "dim"}

    def style(kind, text):
        text = escape(text)
        if kind == "status":
            colour = {"busy": "yellow", "waiting": "magenta"}.get(text.split(":")[0], "green")
            return f"[{colour}]{text}[/]"
        return f"[{look[kind]}]{text}[/]" if kind in look else text

    ui.text(f"[b]{escape(running.headline(got['sessions']))}[/]")
    ui.text(f"[dim]{escape(running.BUSY)}[/]")
    now = time.time()
    for s in got["sessions"]:
        ui.text("")
        root, kids = running.lines(s, now, style)
        ui.tree(root, kids)
    ui.text("")
    ui.hint(running.hints())
    return code


_PROJECT_HELP = "a folder, e.g. ~/code/app (default: this project; in a worktree, the repository's main folder)"


def _sessions_options(f):
    """The options `sessions` and `sessions ls` share."""
    for deco in reversed((
        click.option("--since", help="ISO date or datetime, inclusive, e.g. 2026-09-01"),
        click.option("--until", help="ISO date or datetime, inclusive (a bare date keeps that whole day)"),
        click.option("--limit", type=int, default=0, help="the newest N sessions; 0 lists all"),
        click.option("--entrypoint", metavar="all|NAME[,NAME]", help=ENTRYPOINT_HELP),
        click.option("--include-automated", is_flag=True, hidden=True),  # the released spelling of --entrypoint all
        click.option("--json", "as_json", is_flag=True, help="the same sessions as one JSON document"),
    )):
        f = deco(f)
    return f


def _no_folder(project: str | None, root: str) -> bool:
    """PROJECT names a folder that is not on disk (a typo, a verb read as a folder: `sessions ls`).
    A session-dir name (-Users-x--ak) is not a folder, and is never refused here."""
    if not project or project in (".", "") or project.startswith("-") or re.match(r"^[A-Za-z]--", project):
        return False
    return not Path(root).is_dir()


def _list_sessions(project, since, until, limit, entrypoint, include_automated, as_json):
    import sqlite3

    from tracer import sessions as record
    from tracer.db import get_readonly_connection
    from tracer.search import parse_entrypoints

    include_automated, only = parse_entrypoints(_entrypoint(entrypoint, include_automated))
    root = record.resolve_root(project)
    try:
        con = get_readonly_connection()
        try:
            report = record.collect(root, con, since=since or "", until=until or "",
                                    include_automated=include_automated, entrypoints=only)
        finally:
            con.close()
    except sqlite3.OperationalError:  # no index on this machine yet
        report = record.SessionsReport(root=root, folders=[], rows=[])
    got = record.as_data(report, limit)
    code = 0 if got["sessions"] else 1
    if not got["sessions"]:
        got["elsewhere"] = _projects_with_sessions()
    if not got["sessions"] and _no_folder(project, root):
        # a folder that was deleted keeps the sessions it had; only one with none on record is refused
        _fail(f"no folder {root}, and no session on record under it — give a project folder, e.g. ~/code/app", 64)
    ui.json_mode(as_json)
    raise SystemExit(print_sessions(got))


def _projects_with_sessions() -> int:
    """How many indexed projects hold a session: 0 also when there is no index."""
    import sqlite3

    from tracer.query import list_projects
    try:
        return sum(1 for p in list_projects() if p.get("sessions"))
    except sqlite3.Error:
        return 0


def print_sessions(got: dict) -> int:
    """`sessions ls`'s report through the door (json, plain or rich); the exit code: 1 when none is on record."""
    from tracer import sessions as record

    code = 0 if got["sessions"] else 1
    if ui.is_json():
        ui.emit(got)
        return code
    if not got["sessions"]:
        others = got.get("elsewhere", 0)
        if others:  # the index has them, just not here: say where, never "index again"
            ui.warn(f"no sessions started in {got['root']} — your history has {others} other "
                    f"project{'s' * (others != 1)}")
            ui.hint(f"{spell('all')} lists them · run {command('sessions')} inside one")
        else:
            ui.warn(f"no sessions on record for {got['root']} — `{spell('index')} --all` reads what is on disk")
        return code
    look = {"id": "cyan", "label": "dim", "dim": "dim"}

    def style(kind, text):
        text = escape(text)
        return f"[{look[kind]}]{text}[/]" if kind in look else text

    ui.text(f"[b]{escape(record.title(got))}[/]")
    ui.kv(record.header(got), boxed=False)
    for day, lines in record.days(got, style):
        ui.text("")
        ui.tree(f"[b]{day}[/]", lines)
    ui.text("")
    ui.hint(record.hints())
    return code


@click.command()
@_arg("target", help="a session UUID or its first 8+ characters, or a turn id (<session>-NNN)")
@click.option("--steps", help="list just these steps of the turn: 11-40, 3,7")
@click.option("--open", "open_", metavar="STEPS", help="open steps whole: 5, 1-6, 1,4,5 or all")
def inspect(target, steps, open_):
    """Start here: a session at a glance, or one turn's steps with what each returned."""
    _drill(target, steps, open_)


def _drill(target, steps, open_):
    argv = ["query", "--drill", target]
    if open_:
        argv += ["--step", open_]
    elif steps:
        argv += ["--steps", steps]
    _run_engine_view(argv)


@click.command()
@_arg("uuid", help=f"a session UUID, its first 8 characters, or latest — as `{command('sessions search')}` or `replay` lists it")
@click.option("--drill", help="one turn as numbered steps: an interaction ID, or just its turn number (004)")
@click.option("--open", "open_", metavar="STEPS", help="with --drill: open steps whole — 5, 1-6, 1,4,5 or all")
@click.option("--step", hidden=True)  # the released spelling of --open (inspect and every hint say --open)
@click.option("--limit", type=int, default=20, help="max results")
@click.option("--stats", is_flag=True, help="show aggregation statistics")
@click.option("--patterns", is_flag=True, help="show invocation/failure patterns")
def show(uuid, drill, open_, step, limit, stats, patterns):
    """A session's turns from the index; --drill a turn's steps; --open steps whole."""
    if step and open_ and step != open_:
        raise click.UsageError("--step is --open; give one of them")
    open_ = open_ or step
    uuid = _resolve(uuid)  # the engine filters on the whole id: a prefix used to match nothing
    argv = ["query", "--all-projects", "--limit", str(limit), "--session", uuid]
    if drill:
        argv += ["--drill", drill]
        if open_:
            argv += ["--step", open_]
    if stats:
        argv.append("--stats")
    if patterns:
        argv.append("--patterns")
    _run_engine_view(argv)


@click.command()
@click.option("--all", "index_all", is_flag=True, help="index all discovered projects")
@click.option("--rebuild", is_flag=True, help="force full re-index from JSONL")
@click.option("--session", "session", default=None, metavar="UUID",
              help="index one session's transcript if it is new or changed (what trace does first)")
def index(index_all, rebuild, session):
    """Index conversation transcripts into the session record."""
    argv = ["index"]
    if session:
        argv += ["--session", session]
    if index_all:
        argv.append("--all")
    if rebuild:
        argv.append("--rebuild")
    _run_engine(argv)


# ── trace: the /tracer:trace prefetch block, previewable from a terminal ─
# The block's landmarks, styled only when a human is looking. This is a read-only
# pass over already-rendered text — the block itself is never touched, so piped
# bytes stay byte-identical to what the skill injects.
_TRACE_HL = tuple((re.compile(p, re.M), s) for p, s in (
    (r"^=== .+ ===$",                       "bold cyan"),
    (r"^(?:SESSION_UUID|RESOLVE_QUERY|TITLE|INDEX|CWD)=.*", "bold green"),
    (r"^RESOLVE_NEEDED:.*",                 "bold yellow"),
    (r"^INSTRUCTION:.*",                    "dim italic"),
    (r"^\[\d{3}\].*",                       "dim"),
    (r"^USER:",                             "bold blue"),
    (r"^ASST:",                             "bold magenta"),
    (r"^\[#\d+\]",                          "magenta"),
    (r"tools=\[[^\]]*\]",                   "yellow"),
    (r"\bERR:\d+",                          "bold red"),
    # last, so a UUID stays legible inside an already-styled header line
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-(?:[0-9a-f]{4}-){2}[0-9a-f]{12}\b", "cyan"),
))


# The engine views' landmarks (_run_engine_view): what to find first in a session, a turn,
# a dialogue, a search or a blame. Colour by meaning, as `ak setup` does: green it worked,
# red it failed, yellow look here, cyan where you are, dim what supports the line.
_VIEW_HL = tuple((re.compile(p, re.M), s) for p, s in (
    (r"^[a-z][a-z ]*(?: \(\d+\))?:(?=\s|$)",     "bold"),           # asked: · tokens: · matched paths:
    (r'(?<= · )"[^"\n]{1,160}"',                 "bold"),           # the session's title
    (r"^\[\d{3}\]",                              "bold cyan"),      # a turn in the index, the dialogue
    (r"^── turn \d{3}.*",                         "bold cyan"),
    (r"^\s+did:",                                "bold green"),     # what a turn's calls were for
    (r"^\s*#\d+",                                "bold magenta"),   # a step (indented under a blame's turn)
    (r"^\s+turn \d{3}\b",                         "cyan"),           # a blame's or a search's turn
    (r"\(\+[^)\n]{1,8}\)|(?<= )∥(?= )",           "dim"),            # model time before it · same message
    (r"(?<= )\$ [^\n]*?(?= → | · transcript|$)",  "dim"),            # a raw command: no description to show
    (r"^\s+↳ .*",                                "dim"),            # what came back
    (r"^\s+(?:said|think):.*",                   "italic dim"),
    (r"(?<=B )ok\b|· answered\b",                "green"),
    (r"\bERROR\b|✗|\berr\d+",                    "bold red"),
    (r"→ no result|interrupted[^·\n]*|⚑ [^·\n]*", "yellow"),
    (r"\[(?:[=≈]#\d+|large|raw|miss|sysΔ|toolsΔ|acct)\]", "yellow"),
    (r"^USER:",                                  "bold blue"),
    (r"^ASST:",                                  "bold magenta"),
    (r"^\d+\. ",                                 "bold"),           # a search hit's rank
    (r"\b(?:EDIT|WRITE|READ)×\d+",                "yellow"),
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-(?:[0-9a-f]{4}-){2}[0-9a-f]{12}(?:-\d{3})?\b", "cyan"),
))


def _colorize(raw, rules=_TRACE_HL):
    """Style a rendered view's landmarks. Text() rather than markup on purpose: transcript
    content is never parsed, so a literal '[bold]' quoted in a past session stays
    inert instead of colouring the rest of the output."""
    from rich.text import Text

    text = Text(raw)
    for pattern, style in rules:
        text.highlight_regex(pattern, style=style)
    return text


def _self_cmd() -> str:
    """How the trace skill's sections re-enter THIS process's CLI.

    sources.py freezes CONVO_INDEXER_CMD at import time, so it must be set before
    fetch.py loads it — otherwise each section pays a `uv run` cold start. Its sections
    call the old spellings (replay, show, grep, blame, index), hidden at the root: mounted
    under `ak` the root is `ak tracer`; on the plugin's own console script it is argv[0];
    under `python -m` argv[0] is this file, which is not executable, so re-enter through
    the interpreter.
    """
    argv0 = sys.argv[0] or ""
    if os.path.basename(argv0) == CLI:
        return f"{shlex.quote(argv0)} tracer"
    if argv0 and os.access(argv0, os.X_OK):
        return shlex.quote(argv0)
    return f"{shlex.quote(sys.executable)} -m tracer.cli"


def _fetch_in_plugin_env(fetch: Path, target) -> str:
    """The block from the plugin's own environment, the one the skill runs in: the `ak` CLI's
    carries no jinja2 (the skill's template engine). One `uv run` start; its sections still
    re-enter this CLI through CONVO_INDEXER_CMD."""
    import subprocess

    uv = shutil.which("uv")
    if not uv:
        raise click.ClickException("the trace block needs jinja2, which this ak lacks, or uv to run the "
                                   "tracer's own environment — install uv (https://docs.astral.sh/uv/)")
    p = subprocess.run([uv, "run", "--quiet", "--project", str(PLUGIN_ROOT), "python", str(fetch), *target],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        said = (p.stderr or "").strip().splitlines()
        raise click.ClickException(f"the trace block failed: {said[-1] if said else f'exit {p.returncode}'}")
    return p.stdout


def _prefetch(target, limit=None, full=False):
    fetch = PLUGIN_ROOT / "skills/trace/bin/fetch.py"
    if not fetch.is_file():
        raise click.ClickException(f"trace skill not found at {fetch}")
    os.environ.setdefault("CONVO_INDEXER_CMD", _self_cmd())
    if limit is not None:
        os.environ["REF_DIALOGUE_LIMIT"] = str(limit)
    if full:
        os.environ["REF_DIALOGUE_FULL"] = "1"
    if importlib.util.find_spec("jinja2") is None:
        out = _fetch_in_plugin_env(fetch, target)
    else:
        old = sys.argv  # same argv-remap handoff as _run_engine above
        sys.argv = ["ak-trace", *target]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                _load_path("_trace_fetch", fetch).main()
        finally:
            sys.argv = old
        out = buf.getvalue()
    out = out.rstrip("\n")
    if not ui.is_rich():  # piped, forced plain, or read by an agent — emit the skill's exact bytes
        click.echo(out)
        return
    # soft_wrap keeps the coloured view's line breaks identical to the plain
    # one; highlight=False stops rich's number/string auto-highlighter from
    # adding colour on top of the landmarks chosen above.
    ui.console.print(_colorize(out), soft_wrap=True, highlight=False)


# ── the two groups: `tracer sessions` and `tracer trace` ─────────────────────────
# Each verb is the same click object as its old spelling (live, replay, blame, grep) or a thin
# door onto the same body (ls, show, turn), so an old spelling and its home print the same lines.
# Inside `ak` the same objects are mounted as `ak sessions` and `ak trace` (plugin.json `cli:`).
#
#   tracer sessions  ls [--all] · live · show <id> [--as-skill] · replay · turns · search (s)
#   tracer trace     (the tool calls) get · stats · purge ✎ · turn <turn-id> · blame · index ✎
#
# `trace` is a fork of the tool-call group (calls.py), whose `index` runs both indexes; the
# old `calls` spelling stays the tool-call group as it was.
from tracer import calls as _calls_mod  # noqa: E402
from tracer.calls import trace as _calls  # noqa: E402

writes(index)


@click.group(name="sessions", cls=FuzzyGroup, invoke_without_command=True,
             context_settings={"help_option_names": ["-h", "--help"]},
             epilog=f"""\b
Examples:
  {command('sessions')}                               this project's sessions, oldest first
  {command('sessions live')}                          what is running now
  {command('sessions show')} latest                   the newest session at a glance
  {command('sessions replay')} 06a45a57 --role user   what the user typed there
  {command('sessions turns')} --tool Bash --errors    turns across sessions that ran Bash and hit an error
  {command('sessions search')} "zvec lock"            which sessions said it""")
@_sessions_options
@click.pass_context
def sessions_group(ctx, since, until, limit, entrypoint, include_automated, as_json):
    """The conversations: what is running, what was asked, how it went.

    A bare [b]sessions[/] lists this project's, as [b]ls[/] does (its options work here too).
    Any session id works: the full UUID, its first 8 characters, or [b]latest[/]. What was
    done inside a session — tool calls, a turn's steps, the files touched — is [b]tracer trace[/]."""
    from tracer.storage import indexing_note
    note = indexing_note()
    if note:  # on stderr: a pipe or --json still gets only its document
        ui.warn(note, err=True)
    if ctx.invoked_subcommand is None:
        _list_sessions(None, since, until, limit, entrypoint, include_automated, as_json)

@sessions_group.command(name="ls", epilog=f"""\b
Examples:
  {command('sessions ls')}                          this project's sessions, oldest first
  {command('sessions ls')} ~/code/app --since 2026-09-01
  {command('sessions ls')} --limit 20               the newest 20
  {command('sessions ls')} --all                    every indexed project, with its activity""")
@_arg("project", required=False, help=_PROJECT_HELP)
@click.option("--all", "all_projects", is_flag=True, help="every indexed project instead: its sessions, turns, last activity")
@click.option("--refresh", is_flag=True, help="with --all: index every project first")
@_sessions_options
def sessions_ls(project=None, all_projects=False, refresh=False, since=None, until=None, limit=0,
                entrypoint=None, include_automated=False, as_json=False):
    """List every session in a project, oldest first.

    One line a session — what it began with, where it ended up — under a band that says what
    the record covers and what it does not reach. [b]--all[/] lists the indexed projects
    instead. Exit 1 when the project has no session on record."""
    if all_projects:
        if project:
            raise click.UsageError("--all lists every project; give a folder or --all, not both")
        log.callback(refresh)
        return
    if refresh:
        raise click.UsageError("--refresh goes with --all")
    _list_sessions(project, since, until, limit, entrypoint, include_automated, as_json)

_live = copy.copy(live)
_live.short_help = "Every open session: what it was asked, what it is doing."
sessions_group.add_command(_live, name="live")

@sessions_group.command(name="show", epilog=f"""\b
Examples:
  {command('sessions show')} 06a45a57                 asked · ended · time · tokens · cost · its turns
  {command('sessions show')} latest --turn 4          turn 004 of the newest session, step by step
  {command('sessions show')} 06a45a57 --turn 4 --open 1-3   steps 1 to 3 whole
  {command('sessions show')} 06a45a57 --stats         its totals: interactions, errors, tools
  {command('sessions show')} 06a45a57 --as-skill      the /tracer:trace block the skill reads
  {command('sessions show')} plugin split --as-skill  the block for the session that best matches""")
@_arg("target", nargs=-1, required=True,
      help="a session: its UUID, its first 8 characters (e.g. 06a45a57), or latest; "
           "with --as-skill also words that describe one, e.g. plugin split")
@click.option("--turn", metavar="N", help="one turn's steps instead: its number, e.g. 004")
@click.option("--open", "open_", metavar="STEPS", help="open steps whole: 5, 1-6, 1,4,5 or all")
@click.option("--as-skill", is_flag=True, help="the /tracer:trace prefetch block instead, byte for byte as the skill reads it")
@click.option("--stats", is_flag=True, help="the index's totals for it instead: interactions, errors, tools, plugins")
@click.option("--patterns", is_flag=True, help="its tool sequences and failure clusters instead, from the index")
def sessions_show(target, turn, open_, as_skill, stats, patterns):
    """Show a session at a glance: asked, ended, time, cost, turns.

    Small sessions show every step inline. [b]--turn[/] lists one turn's steps with what each
    returned; [b]--open[/] opens steps whole. A turn id (06a45a57-004) works here too, and
    is [b]tracer trace turn[/]'s own argument. [b]--stats[/] and [b]--patterns[/] read the index instead.

    [b]--as-skill[/] prints the /tracer:trace block, and takes what the skill takes: a session id,
    or words that describe one (unquoted is fine) — the skill finds the session that best matches,
    or lists candidates to pick from. Without it, [b]show[/] takes one session id."""
    from tracer.drill import AmbiguousSession, DrillError, find_session, split_target

    if as_skill:  # the skill's own reading: a session id, else words that describe one
        if turn is not None or open_ or stats or patterns:
            raise click.UsageError("--as-skill is the whole session's block; leave out --turn, --open, --stats and --patterns")
        sid = None
        if len(target) == 1:  # `latest` and a prefix become the full id; anything else goes to the skill as typed
            try:
                sid = find_session(target[0])
            except AmbiguousSession as exc:
                _fail(str(exc), 2)
        _prefetch((sid,) if sid else target)
        return
    if len(target) > 1:
        raise click.UsageError(f"show takes one session id, not {len(target)} words — words that describe a "
                               f"session go with --as-skill, or to {command('sessions search')}")
    target = target[0]
    try:
        sid, seq = split_target(target)
    except AmbiguousSession as exc:
        _fail(str(exc), 2)
    except DrillError as exc:
        _fail(str(exc), 1)
    if turn is not None:
        if not str(turn).isdigit():
            raise click.BadParameter(f"{turn!r} is not a turn number — give one like 004", param_hint="--turn")
        seq = int(turn)
    if stats or patterns:
        if seq is not None or open_:
            raise click.UsageError("--stats and --patterns are the whole session's; leave out --turn and --open")
        show.callback(sid, None, None, None, 20, stats, patterns)
        return
    _drill(sid if seq is None else f"{sid}-{seq:03d}", None, open_)

@sessions_group.command(name="agents", epilog=f"""\b
Examples:
  {command('sessions agents')} 06a45a57                         every subagent: what it was asked, calls, time, tokens
  {command('sessions agents')} 06a45a57 a7c19e5d                one agent's steps, with what each returned
  {command('sessions agents')} 06a45a57 a7c19e5d --steps 11-40  only these steps
  {command('sessions agents')} 06a45a57 a7c19e5d --open 5       step 5 whole
  {command('sessions agents')} latest --json                    the same rows as one JSON document""")
@_arg("session", help="a session: its UUID, its first 8 characters (e.g. 06a45a57), or latest")
@_arg("agent", required=False, help="one agent: its id or its first characters, as the list prints it, e.g. a7c19e5d")
@click.option("--steps", help="with an agent: list just these steps: 11-40, 3,7")
@click.option("--open", "open_", metavar="STEPS", help="with an agent: open steps whole: 5, 1-6, 1,4,5 or all")
@click.option("--turn", type=click.IntRange(min=0), metavar="N",
              help="with an agent sent more than one message: which of its turns, from 0")
@ui.json_option
def sessions_agents(session, agent, steps, open_, turn, as_json):
    """List a session's subagents, or one agent's steps.

    One row per subagent the session started (an Agent or Task call's, a fork's, a workflow's): its type,
    the step that started it (turn#step), calls, errors, time, output tokens, whether it reported, what it
    was asked. Tokens count once per model message. Above the rows, the totals per type; under them, the
    Agent calls that ran no agent and the files more than one agent touched. Exit 1 when it started none.

    With an agent's id: its steps, as [b]tracer trace turn[/] lists a turn's; [b]--open[/] opens them whole."""
    from tracer import agents as subagents
    from tracer.drill import AmbiguousSession, DrillError

    ui.json_mode(as_json)
    if not agent and (steps or open_ or turn is not None):
        raise click.UsageError("--steps, --open and --turn read one agent: give its id after the session, e.g. a7c19e5d")
    if steps and open_:
        raise click.UsageError("--steps lists steps and --open opens them whole; give one of them")
    try:
        got = subagents.load(session)
        if agent is None:
            raise SystemExit(print_agents(subagents.as_data(got)))
        one = subagents.pick(got, agent)
        if ui.is_json():
            ui.emit(subagents.agent_data(got, one))
            return
        out = subagents.open_agent_steps(got, one, open_, turn) if open_ else \
            subagents.format_agent(got, one, steps, turn)
    except AmbiguousSession as exc:
        _fail(str(exc), 2)
    except DrillError as exc:
        _fail(str(exc), 1)
    _print_view(out)


def print_agents(data: dict) -> int:
    """`sessions agents`'s rollup through the door (json, plain or rich); the exit code: 1 when the session started none."""
    from tracer import agents as subagents
    from tracer.drill import agent_verb

    code = 0 if data["agents"] else 1
    if ui.is_json():
        ui.emit(data)
        return code
    notes = subagents.notes(data)
    if not data["agents"]:
        ui.warn(f"no subagents in {data['session'][:8]}: no transcript under its subagents/ folder")
        for line in notes:
            ui.text(line, markup=False)
        return code
    ui.text(subagents.headline(data), markup=False)
    ui.text("")
    num = ("left", "right", "right", "right", "right", "right", "left")
    ui.table(subagents.TYPE_COLUMNS, subagents.type_rows(data), title="by type, the most agent time first",
             justify=num, box=None)
    ui.text("")
    ui.table(subagents.AGENT_COLUMNS, subagents.agent_rows(data), title="agents, in the order they started",
             styles=("cyan", "dim", None, "dim", None, None, None, None, None, None),
             justify=(None, None, None, None, "right", "right", "right", "right", None, None), box=None)
    if notes:
        ui.text("")
    for line in notes:
        ui.text(line, markup=False)
    ui.text("")
    ui.hint(ui.escape(f"{agent_verb(data['session'])} <id>   one agent's steps (--open N: one whole)"))
    return code


def _print_view(out: str) -> None:
    """A drill-shaped view (a turn's steps, a step whole) through the door: plain as written, coloured
    in a terminal by the same landmarks as the engine's views (_VIEW_HL)."""
    if ui.is_rich():
        ui.console.print(_colorize(out, _VIEW_HL), soft_wrap=True, highlight=False)
    else:
        ui.text(out, markup=False)

sessions_group.add_command(replay)

@sessions_group.command(name="turns", epilog=f"""\b
Examples:
  {command('sessions turns')} --tool Bash --errors              this project's turns that ran Bash and hit an error
  {command('sessions turns')} --skill recall --all              every project's turns that ran a recall skill
  {command('sessions turns')} --plugin observer --stats         the totals over the turns observer was in
  {command('sessions turns')} --agent-type Explore --patterns   their tool sequences and failure clusters
  {command('sessions turns')} ~/code/app --branch feat/          another project's turns on feat/ branches""")
@_arg("project", required=False, help=_PROJECT_HELP)
@click.option("--plugin", help="only turns credited to a plugin — its MCP tools, a skill, an agent or a file it ships, e.g. observer")
@click.option("--agent-type", help="only turns that spawned this subagent type, e.g. Explore")
@click.option("--skill", help="only turns that ran this skill, by its name or plugin:name, e.g. recall")
@click.option("--tool", help="only turns that called this tool, by its full name, e.g. Bash or mcp__GitHub__get_me")
@click.option("--errors", is_flag=True, help="only turns with an error: a failed tool call, an API error")
@click.option("--since", help="ISO date or datetime, inclusive, e.g. 2026-09-01")
@click.option("--until", help="ISO date or datetime, inclusive (a bare date keeps that whole day)")
@click.option("--branch", help="only turns on a git branch that starts with this, e.g. feat/")
@click.option("--limit", type=click.IntRange(min=1), default=20, help="the newest N turns")
@click.option("--all", "all_projects", is_flag=True, help="every indexed project, not only this one")
@click.option("--stats", is_flag=True, help="the totals instead: turns, errors, tokens, top plugins, agent types, tools")
@click.option("--patterns", is_flag=True,
              help="tool sequences, failure clusters and agent mixes per plugin instead, over the newest 500 matches")
def sessions_turns(project, plugin, agent_type, skill, tool, errors, since, until, branch, limit, all_projects, stats, patterns):
    """List turns across sessions that match filters.

    One line a turn, newest first: its id, plugins, tools, agents, tokens, how it ended, its
    errors and what it asked. Read from the index ([b]tracer trace index[/] refreshes it). By default
    this project — the folder, its worktrees and the folders inside, as [b]ls[/] counts them.
    [b]--stats[/] and [b]--patterns[/] read the same matches as totals and as patterns.
    Exit 1 when no turn matches."""
    from tracer.sessions import resolve_root

    if project and all_projects:
        raise click.UsageError("--all reads every project; give a folder or --all, not both")
    if stats and patterns:
        raise click.UsageError("--stats and --patterns are two views; give one of them")
    root = resolve_root(project)
    if not all_projects and _no_folder(project, root):
        _fail(f"no folder {root} — give a project folder, e.g. ~/code/app, or --all", 64)
    flags = []
    for name, value in (("--plugin", plugin), ("--agent-type", agent_type), ("--skill", skill), ("--tool", tool),
                        ("--since", since), ("--until", until), ("--branch", branch)):
        if value:
            flags += [name, value]
    if errors:
        flags.append("--errors")
    argv = ["query", *flags, "--limit", str(limit)]
    argv += ["--all-projects"] if all_projects else ["--root", root]
    if stats:
        argv.append("--stats")
    if patterns:
        argv.append("--patterns")
    _run_engine_view(argv)
    if stats or patterns:  # the turns behind the totals: the same question, as a list
        again = [*([project] if project else []), *flags, *(["--all"] if all_projects else [])]
        ui.hint(ui.escape(" ".join([command("sessions turns"), *(shlex.quote(a) for a in again)])))

_search = copy.copy(grep)
_search.aliases = ["s"]  # what the help lists beside the name
_search.short_help = "Find the sessions that talked about a phrase, ranked by hits."
sessions_group.add_command(_search, name="search", aliases=["s"])

# ── tracer trace: the tool-call group, forked, plus the transcript's turn, blame and both indexes
trace_group = copy.copy(_calls)
trace_group.commands = dict(_calls.commands)
for _attr in ("_alias_mapping", "_panel_command_mapping"):
    if hasattr(_calls, _attr):
        setattr(trace_group, _attr, copy.copy(getattr(_calls, _attr)))
trace_group.help = (_calls.help or "") + (
    "\n\n[b]turn[/] opens one turn of a session step by step, [b]blame[/] finds the sessions that "
    "touched a file, [b]index[/] refreshes both indexes.")

@trace_group.command(name="turn", epilog=f"""\b
Examples:
  {command('trace turn')} 06a45a57-004                 the turn's steps, with what each returned
  {command('trace turn')} 06a45a57-004 --steps 11-40   only these steps
  {command('trace turn')} 06a45a57-004 --open 1,4,5    those steps whole""")
@_arg("turn_id", help="a turn: <session>-NNN, the session as its UUID, first 8 characters or latest, e.g. 06a45a57-004")
@click.option("--steps", help="list just these steps of the turn: 11-40, 3,7")
@click.option("--open", "open_", metavar="STEPS", help="open steps whole: 5, 1-6, 1,4,5 or all")
def trace_turn(turn_id, steps, open_):
    """Show one turn's steps, with what each returned."""
    from tracer.drill import AmbiguousSession, DrillError, split_target

    try:
        sid, seq = split_target(turn_id)
    except AmbiguousSession as exc:
        _fail(str(exc), 2)
    except DrillError as exc:
        _fail(str(exc), 1)
    if seq is None:
        ui.err(ui.escape(f"{turn_id!r} is a session, not a turn — a turn id is <session>-NNN, e.g. {sid[:8]}-000"))
        ui.hint(f"{command('sessions show')} {sid[:8]}")
        raise SystemExit(64)
    _drill(f"{sid}-{seq:03d}", steps, open_)

trace_group.add_command(blame)

@writes
@trace_group.command(name="index", epilog=f"""\b
Examples:
  {command('trace index')}                    this project's transcripts, then every new tool call
  {command('trace index')} --all              every project's transcripts
  {command('trace index')} --session 06a45a57 one session's transcript, if new or changed""")
@click.option("--all", "index_all", is_flag=True, help="transcripts of every discovered project, not only this one")
@click.option("--rebuild", is_flag=True, help="re-read every transcript into the session index")
@click.option("--session", metavar="ID", help="only this session's transcript, if new or changed (UUID, 8 characters or latest)")
@click.option("--recent", is_flag=True, help="tool calls only from transcripts changed in the last hour")
@ui.json_option
def trace_index_both(index_all, rebuild, session, recent, as_json):
    """Index the transcripts and the tool calls, as the Stop hook does.

    Two stores, one pass each: the session index ([b]tracer sessions[/], [b]show[/], [b]blame[/]) and
    the tool-call index (bare [b]tracer trace[/]). Each reads only what changed since its last pass,
    unless [b]--rebuild[/]."""
    import time

    from tracer import storage

    ui.json_mode(as_json)
    t0 = time.time()
    if session:
        sid = _resolve(session)
        got = storage.ensure_session_indexed(sid)
        said = storage.describe_index_status(got, sid)
        transcripts = {**got, "said": said}
    else:
        n = storage.rebuild_from_jsonl() if rebuild else \
            storage.incremental_index(os.getcwd(), quiet=True, index_all=index_all)
        said = f"{n} new interaction{'s' if n != 1 else ''}" + (" (rebuilt)" if rebuild else "") \
            + (" · every project" if index_all and not rebuild else "")
        transcripts = {"interactions": n, "rebuild": rebuild, "all": index_all, "said": said}
    transcripts["seconds"] = round(time.time() - t0, 1)
    if not ui.is_json():  # the tool-call pass prints only when it ends: say it began, so a long one is not silence
        ui.text("indexing the tool calls…", err=True)
    progress = storage.progress_path()
    storage.say_progress(progress, "tool calls")
    try:
        tool_calls = _calls_mod.engine("index", *(["--recent"] if recent else []))
    finally:
        progress.unlink(missing_ok=True)
    if ui.is_json():
        ui.emit({"transcripts": transcripts, "calls": tool_calls})
        return
    ui.ok(f"transcripts: {ui.escape(said)} · {transcripts['seconds']}s")
    ui.ok(f"tool calls: {_calls_mod.indexed(tool_calls)}")
    ui.hint(f"{command('sessions')} · {command('trace')}")


# ── the root, `tracer`: the two groups, and every older spelling unlisted ────────
@click.group(name=command(), cls=FuzzyGroup,
             context_settings={"help_option_names": ["-h", "--help"], "show_default": True},
             epilog=f"""\b
Examples:
  {command('sessions live')}                     what is running now
  {command('sessions')}                          this project's sessions, oldest first
  {command('sessions show')} latest              the newest session at a glance
  {command('sessions search')} "zvec lock"       which sessions said it
  {command('trace blame')} src/app.py            which sessions touched a file
  {command('trace')} --outcome error --since 1d  the tool calls that failed today""")
def cli():
    """Which session did what, verbatim from Claude Code's transcripts.

    [b]sessions[/] are the conversations: what is running, what was asked, how it went. [b]trace[/] is
    what was done in them: every tool call, a turn's steps, the sessions that touched a file. A session
    is its UUID, its first 8 characters, or [b]latest[/]."""


cli.add_command(sessions_group, name="sessions")
cli.add_command(trace_group, name="trace")
# Every older spelling still runs, out of every list: the trace skill (skills/trace/lib/sources.py) calls
# replay, show, grep, blame and index; the Stop hook calls index; `ak tracer <verb>` is their released form.
for _name, _verb in (("replay", replay), ("blame", blame), ("log", log), ("grep", grep), ("live", live),
                     ("inspect", inspect), ("show", show), ("index", index), ("calls", _calls)):
    hidden_alias(cli, _verb, _name)


def brain_status():
    """the `ak` CLI's front-door row for this plugin (the `cli:` contract's second
    half): cheap, cache and filesystem only. A person reads it in a terminal, where `tracer` is not on
    PATH (only the Bash tool's), so its hints say the host's spelling: `ak sessions`."""
    from tracer._brand import CLI
    from tracer.db import DB_PATH
    from tracer.storage import progress_words, read_progress

    going = read_progress()
    if going:
        return [("tracer", f"[yellow]↻ indexing your history · {progress_words(going)}[/] [dim]— runs in the background[/]")]
    try:
        size = DB_PATH.stat().st_size
        return [("tracer", f"index {size // 1024} KB · {CLI} sessions")]
    except OSError:
        return [("tracer", f"no index yet · {CLI} trace index --all")]


def brain_search(words: str, limit: int) -> list[dict]:
    """`ak search`'s sessions rows (the `cli:` contract): the sessions that said WORDS, ranked by
    hits as `tracer sessions search` ranks them — every project's transcripts, this session left out.
    A row is the session's 8-character id, its title (else its first prompt, else the best hit)
    and the day of its latest hit."""
    from tracer.search import search_sessions
    from tracer.storage import discover_projects
    from tracer.turns import one_line

    dirs = [d for _hash, d in discover_projects()]
    report = search_sessions(words, dirs, scope="all projects", turn_limit=limit)
    rows = []
    for g in report.ranked[:limit]:
        info = report.info.get(g.session_id)
        best = min(g.hits, key=lambda h: (h.rank, h.line)).snippet if g.hits else ""
        title = (info.title or info.first_prompt) if info else ""
        rows.append({"id": g.session_id[:8], "title": one_line(title or best, 100),
                     "when": g.latest[:10], "where": f"{len(g.hits)} hit{'s' if len(g.hits) != 1 else ''}"})
    return rows


def main():
    """The `tracer` console script (bin/tracer): run on its own, never inside `ak`, whose help is installed already."""
    utf8_stdio()  # before anything prints: a cp1252 console or pipe cannot encode → or a session title's emoji
    from tracer import help as help_mod
    help_mod.install(command(), groups=[], extra_classes=(FuzzyGroup,))  # plain help for an agent, usage errors that say what to type
    cli(prog_name=command())


if __name__ == "__main__":
    main()
