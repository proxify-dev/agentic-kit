#!/usr/bin/env python3
"""ak — the kit's one command: the host every plugin's verbs mount into, and its own tooling.

Its own verbs are the tooling around a session (shim · gateway · xray · sysprompt · hooks)
and moving things (ws move · moves · doctor · home · plugin rename, src/ak/move/). Everything
else is a plugin's: each declares a `cli` block in its plugin.json and is mounted at start
(`_mount_plugin_clis`) when it is present AND enabled in Claude Code — the vault's verbs (`ak vault …`,
`ak resolve` hoisted), `ak memory`, `ak observer`, the tracer's `ak sessions` and `ak trace`. `ak search`
asks every plugin that can search at once. `ak --help` is the verb catalogue, grouped, of what is
mounted; an old spelling keeps working, hidden. ak plants its home, ~/.ak, before any verb (ak/home.py).

Every verb prints through `ak.ui` — the one output door — which picks the mode
once: rich for a human terminal, a compact plain grammar for a pipe or an agent
(`CLAUDECODE`, `NO_COLOR`, not a TTY), json under `--json`. `AK_OUTPUT=rich|plain|json`
forces it; the rules are in `plugins/AGENTS.md`.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import rich_click as click
from rich.markup import escape
from ak import ui
from ak import help as help_mod
from ak import home as home_mod
from ._brand import CLI, HOME_DIR, REPO_DIR, cmd, config_dir, data_dir, env, env_name, is_windows, utf8_stdio

from ak import hooks_trace
import shlex

from ak.groups import FuzzyGroup, hidden_alias, writes

def _fmt(p):
    return escape(str(p))


# ── path-load helper (only for mounting sibling plugin CLIs by path) ───────────
def _load_path(modname, path):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec — the canonical importlib pattern. Without it,
    # @dataclass in a loaded module can't resolve cls.__module__ in sys.modules.
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


# ── shared helpers ───────────────────────────────────────────────────────────
def _run_replace(argv, env=None, exe=None):
    """Hand this process over to ARGV and never return: exec on macOS/Linux. Windows has no exec
    (os.exec* starts the child and exits at once, so the shell prompt returns under a live program):
    there it runs ARGV, waits, and exits with its code."""
    exe = exe or argv[0]
    if is_windows():
        try:
            code = subprocess.call(argv, env=env, executable=exe)
        except KeyboardInterrupt:  # ctrl-c reached the child too; its exit is the answer
            code = 130
        sys.exit(code)
    if env is None:
        os.execv(exe, argv)
    os.execve(exe, argv, env)


def _home_code() -> Path:
    """The code repo's default home, ~/agentic-kit — or wherever `ak ws move` took it (lineage.jsonl)."""
    from ak.move import lineage
    return Path(lineage.moved(str(Path.home() / REPO_DIR)))


# The help layer is wired at import, before the first command is looked up: it
# sets the grouping rich-click reads and, off a terminal, the plain path. Tests
# invoke `cli()` directly, so this cannot live in main().
help_mod.install(CLI, extra_classes=(FuzzyGroup,))


# ── CLI ──────────────────────────────────────────────────────────────────────
# Lines on the bare-`ak` front door: the shim's (native, below) and those
# _mount_plugin_clis adds — [(name, brain_status)], see its docstring.
_STATUS_HOOKS = []
# What a mounted module does before any verb runs — [(name, brain_boot)], the same contract.
_BOOT_HOOKS = []
# What `ak search` asks — [(name, brain_search)], the same contract.
_SEARCH_HOOKS = []
_STATUS_HOOKS.append(("hooks", lambda: _hooks_status()))  # defined below, with the band


# Every example the root may show; `_settle_root_help` keeps the lines whose command is mounted.
ROOT_EPILOG = f"""Examples:
  {CLI} search "band flapping"             every plugin that can search, at once
  {CLI} sessions                           this project's sessions, oldest first
  {CLI} sessions replay 5e0c…              a past session's dialogue
  {CLI} trace blame src/x.py               which tool calls touched a file
  {CLI} resolve ak-app                     a note name → its path (exit 2 when ambiguous)
  {CLI} init                               a {HOME_DIR}/ scope for this folder
  {CLI} <verb> -h                          one verb: what it takes, its defaults, examples

Output: rich on a terminal; a plain LLM-friendly grammar when piped or under an
agent; --json on read verbs. Force with {env_name('OUTPUT')}=rich|plain|json."""


def example_command(group, line: str):
    """The command an Examples LINE walks to under GROUP (`  ak sessions replay 5e0c…  what it does`),
    None when a word of it is not there; True for a line that names no command (`ak <verb> -h`, prose)."""
    words = re.split(r"\s{2,}", line.strip())[0].split()  # the command, not the words explaining it
    if len(words) < 2 or words[0] != CLI or words[1].startswith("<"):
        return True
    cmd = group
    for w in words[1:]:
        if not isinstance(cmd, click.Group) or w.startswith(("-", '"')):
            break
        if w not in cmd.commands:
            return None
        cmd = cmd.commands[w]
    return cmd


def _settle_root_help(group) -> None:
    """Once every verb is mounted: the root's Examples keep only lines that run, and its grouping only
    names that are there — a plugin that is absent or switched off leaves no trace in `ak -h`."""
    group.epilog = help_mod.epilog("\n".join(ln for ln in ROOT_EPILOG.splitlines() if example_command(group, ln)))
    help_mod.keep_only(set(group.commands))


def _version_line() -> str:
    """`ak 0.12.0 · /path/to/plugins/ak` — the version AND the checkout that
    answered, because an editable install can point at any of several trees."""
    plugin = Path(__file__).resolve().parents[2]
    try:
        import tomllib
        v = tomllib.loads((plugin / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    except Exception:  # noqa: BLE001 — a wheel install has no pyproject beside it
        from importlib.metadata import version
        v = version("ak")
    return f"{CLI} {v} · {plugin}"


def _print_version(ctx, _param, value):
    if value and not ctx.resilient_parsing:
        click.echo(_version_line())
        ctx.exit()


# show_default is inherited by every context below this one, mounted plugins included.
@click.group(cls=FuzzyGroup, invoke_without_command=True, epilog=help_mod.epilog(ROOT_EPILOG),
             context_settings={"help_option_names": ["-h", "--help"], "show_default": True})
@click.option("--version", "-V", is_flag=True, expose_value=False, is_eager=True,
              callback=_print_version, help="version, and which checkout is running")
@click.pass_context
def cli(ctx):
    """The `ak` command: its own tooling, and the verbs every enabled kit plugin mounts into it."""
    # ak's home first (ak/home.py: the layout-2 folders, never over a layout-1 home), then each
    # module's own start (the mount contract): the vault's plants its notes in <home>/vault.
    try:
        home_mod.plant()
    except OSError as e:
        ui.warn(f"{HOME_DIR}: could not plant the home ({e})", err=True)
    for name, boot in _BOOT_HOOKS:
        try:
            boot(ctx)
        except Exception as e:  # noqa: BLE001 — one module's boot must not sink the command
            ui.warn(f"{name}: boot failed ({e})")
    # Bare `ak` is a dashboard, not just a help page: the command list says
    # what you CAN do, the status band says what you are bound to right now.
    # Band last — at a prompt the bottom of the screen is what you see first.
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ui.text("")
        _front_door()
        ctx.exit()


# ── shim ─────────────────────────────────────────────────────────────────────
# The standalone cc-shim (shim/cc-shim.mjs, Node, no dependencies) stays the
# implementation on purpose: it installs and repairs the wrapper that owns
# `claude` on PATH, so it has to keep working when this CLI (uv, python, the
# vault) cannot. These commands are a front door — they read its --json
# contract and render it in house style. Its source is the repo's `shim/` (not
# a plugin: Claude Code never loads it, it is what starts Claude Code), found
# the way the gateway's CLI is. Its folders are the seam's: data_dir()/cc-shim
# for the wrapper, config_dir()/cc-shim for conf.d and the system prompt.
SHIM_HOME = Path(data_dir()) / "cc-shim"
SHIM_CFG = Path(config_dir()) / "cc-shim"
SHIM_CONFD = SHIM_CFG / "conf.d"
# The fragments cc-shim runs on this OS: *.mjs everywhere, *.sh where there is a POSIX shell.
_SHIM_KINDS = (".mjs",) if is_windows() else (".sh", ".mjs")


def _shim_src():
    from ak.setup import source  # lazy: the release clone is the last place to look
    here = _checkout_above(Path.cwd().resolve())
    for root in (env("CODE"), here, _home_code(), source.release_dir()):
        if root and (Path(root) / "shim/cc-shim.mjs").is_file():
            return Path(root) / "shim"
    return None


def _shim_exe():
    """[node, cc-shim.mjs]: the installed copy first — its behaviour is the live
    one — else the checkout's, so `ak shim install` works before anything is installed."""
    src = _shim_src()
    for mjs in (SHIM_HOME / "cc-shim.mjs", src and src / "cc-shim.mjs"):
        if mjs and mjs.is_file():
            node = shutil.which("node")
            if not node:
                ui.err("node not on PATH — cc-shim is a Node program (Node ≥ 22.13)")
                raise SystemExit(127)
            return [node, str(mjs)]
    ui.err(f"cc-shim not found — run node ~/{REPO_DIR}/shim/cc-shim.mjs install")
    raise SystemExit(127)


def _shim_line():
    """[(label, markup, command)] for bare `ak`. Stat and environ only — no subprocess."""
    wrapper = SHIM_HOME / ("claude.cmd" if is_windows() else "claude")
    if not wrapper.exists():
        return [("launcher", "[yellow]shim not installed[/] — claude skips the gateway and your system prompt",
                 cmd("shim install"))]
    if not (SHIM_HOME / "cc-shim.mjs").exists():
        return [("launcher", "[yellow]shim is the old bash one[/] — it skips the gateway's 05-gateway.mjs",
                 cmd("shim install"))]
    on_path = str(SHIM_HOME) in os.environ.get("PATH", "").split(os.pathsep)
    frags = sorted(p.name for p in SHIM_CONFD.iterdir() if p.suffix in _SHIM_KINDS) if SHIM_CONFD.is_dir() else []
    claim = os.environ.get("CC_SHIM_CLAIM")
    if not on_path:
        line = "[yellow]shim installed but not on PATH[/] — " + ("open a new terminal" if is_windows() else "reload your shell")
    elif claim:
        line = "claude [dim]→[/] shim [dim]→[/] claimed by [b]%s[/]" % claim
    else:
        line = "claude [dim]→[/] shim [dim]· %s[/]" % (
            ", ".join(Path(f).stem for f in frags) if frags else "no fragments (passthrough)")
    off = len(list(SHIM_CONFD.glob("*.off"))) if SHIM_CONFD.is_dir() else 0
    if off:
        line += " [dim]· %d disabled[/]" % off
    return [("launcher", line, cmd("shim doctor"))]


_STATUS_HOOKS.append(("shim", _shim_line))


def _shim_json(sub):
    _shim_pin_src()
    p = subprocess.run([*_shim_exe(), sub, "--json"], capture_output=True, text=True, encoding="utf-8")
    try:
        return json.loads(p.stdout), p.returncode
    except ValueError:
        ui.text(p.stdout or p.stderr or "cc-shim produced no output", markup=False)
        raise SystemExit(p.returncode or 1)


def _tilde(p):
    home = str(Path.home())
    return "~" + p[len(home):] if p and p.startswith(home) else (p or "—")


@cli.group(cls=FuzzyGroup, invoke_without_command=True,
           context_settings={"help_option_names": ["-h", "--help"]},
           epilog=help_mod.epilog(f"Examples:\n  {CLI} shim            status\n  {CLI} shim doctor     diagnose PATH and fragments"))
@click.pass_context
def shim(ctx):
    """The `claude` wrapper on PATH and the fragments it runs.

    Bare [b]ak shim[/] is [b]status[/].
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(shim_status)


@shim.command(name="status")
@ui.json_option
def shim_status(as_json):
    """Where the wrapper is, whether PATH finds it, which fragments run."""
    ui.json_mode(as_json)
    d, _ = _shim_json("status")
    if ui.is_json():
        ui.emit(d)
        return
    on_path = d.get("claude") == d.get("wrapper")
    ui.kv([
        ("wrapper", f"{_fmt(_tilde(d['wrapper']))}  " + ("[green]present[/]" if d["present"] else "[red]MISSING[/]")),
        ("PATH", f"position {d['pathPos']}" if d.get("pathPos") else "[yellow]not present — reload your shell[/]"),
        ("claude", f"{_fmt(_tilde(d.get('claude') or ''))}  " + ("[green]the shim[/]" if on_path else "[yellow]not the shim[/]")),
        ("real", _fmt(_tilde(d.get("real") or ""))),
        ("wired", " ".join(f"[cyan]{w}[/]" for w in d["wired"]) or "[yellow]nothing[/]"),
    ], title="claude shim")
    frags = d.get("fragments") or []
    if not frags:
        ui.hint(f"fragments {_tilde(d['confd'])} — empty, pure passthrough")
        return
    ui.table(["fragment", "claims"], [[f["file"], f.get("claim") or ""] for f in frags],
             title=f"fragments · {_tilde(d['confd'])}", box=None)


@shim.command(name="doctor")
@ui.json_option
def shim_doctor(as_json):
    """Diagnose PATH order, recursion, leftovers and fragment syntax."""
    ui.json_mode(as_json)
    d, rc = _shim_json("doctor")
    if ui.is_json():
        ui.emit(d)
        raise SystemExit(rc)
    style = {"PASS": "green", "FAIL": "red", "WARN": "yellow", "INFO": "dim"}
    ui.table(["level", "check"],
             [[f"[{style.get(c['level'], '')}]{c['level']}[/]", _fmt(c["msg"])] for c in d["checks"]],
             title="shim doctor", box=None)
    bad = d.get("fail", 0)
    (ui.warn if bad else ui.ok)(f"{bad} problem(s)" if bad else "healthy")
    raise SystemExit(rc)


@writes
@shim.command(name="install", context_settings={"ignore_unknown_options": True})
@click.argument("args", nargs=-1, type=click.UNPROCESSED, help="passed as-is to `cc-shim install`")
def shim_install(args):
    """Install the wrapper, wire PATH, migrate off any previous shim."""
    _shim_passthrough("install", args)


@writes
@shim.command(name="uninstall", context_settings={"ignore_unknown_options": True})
@click.argument("args", nargs=-1, type=click.UNPROCESSED,
                help="passed as-is to `cc-shim uninstall`, e.g. --purge")
def shim_uninstall(args):
    """Remove it. [b]--purge[/] also deletes your conf.d fragments."""
    _shim_passthrough("uninstall", args)


def _shim_passthrough(sub, args):
    # These print prose about what they changed; hand over so output streams live.
    argv = _shim_exe()
    # `install` runs the installer that ships with the source it installs: an
    # older installed copy may not know the layout of the folder it copies from.
    src = _shim_src()
    if sub == "install" and src:
        argv = [argv[0], str(src / "cc-shim.mjs")]
    os.environ["CC_SHIM_INVOKED_AS"] = cmd("shim")
    _shim_pin_src()
    _run_replace([*argv, sub, *args])


def _shim_pin_src():
    """Install from, and doctor against, the checkout you stand in (or ~/agentic-kit)
    — not the folder the last install recorded, which may have moved."""
    src = _shim_src()
    if src and not os.environ.get("CC_SHIM_SRC"):
        os.environ["CC_SHIM_SRC"] = str(src)


# ── gateway ──────────────────────────────────────────────────────────────────
# The wire every model call goes through. Its CLI is TypeScript beside the
# daemon (gateway/src/cli.ts) so the two cannot disagree about a port; this is
# the front door, exec'd so `tail` and `probe` stream live.
def _gateway_cli(entry="cli.ts"):
    """$AK_CODE, else the checkout you stand in — a worktree runs its own
    gateway, the way `ak` runs its own plugin there — else ~/agentic-kit, else the release clone,
    else the cc-gateway release `ak setup` installed (the public kit carries no gateway/ folder)."""
    from ak.setup import source  # lazy: the release clone is the last place to look
    here = _checkout_above(Path.cwd().resolve())
    for root in (env("CODE"), here, _home_code(), source.release_dir()):
        if root and (Path(root) / "gateway/src" / entry).is_file():
            return Path(root) / "gateway/src" / entry
    # where cc-gateway's install.sh unpacks a release: <data dir>/ak/cc-gateway/current ("ak" is the gateway's own
    # fixed state folder name, not the brand)
    installed = Path(data_dir()) / "ak" / "cc-gateway" / "current" / "src" / entry
    if installed.is_file():
        return installed
    ui.err(f"no gateway here — {cmd('setup')} installs it (pick gateway), or set {env_name('CODE')} to a checkout")
    raise SystemExit(127)


def _gateway_env():
    """The gateway is also cc-gateway on its own: tell it how it was reached, so the
    commands its hints print are ones this person has (`ak gateway enable`)."""
    return {**os.environ, env_name("GATEWAY_COMMAND"): cmd("gateway")}


def _node():
    node = shutil.which("node")
    if not node:
        ui.err("node not on PATH — the gateway is TypeScript run by Node ≥ 22.7")
        raise SystemExit(127)
    return node


@cli.command(name="gateway", aliases=["gw"],
             context_settings={"ignore_unknown_options": True, "help_option_names": []})
@click.argument("args", nargs=-1, type=click.UNPROCESSED,
                help=f"a gateway verb and its options (none = status); `{cmd('gateway help')}` lists them")
def gateway(args):
    """The model-call gateway: on or off, and what goes through it.

    [b]enable[/] routes every new claude through it; [b]disable[/] gives claude its own login
    back. Bare [b]ak gateway[/] is status; [b]ak gateway help[/] lists the rest.
    """
    node = _node()
    _run_replace([node, "--experimental-strip-types", "--no-warnings",
                  str(_gateway_cli()), *(args or ("status",))], env=_gateway_env())


# ── xray ─────────────────────────────────────────────────────────────────────
# The prompt a launch would send: gateway/src/xray.ts runs the claude command
# through the gateway with every request marked dry-run, and reads the prompt
# back out of the trace. Everything from `claude` on is that command, untouched
# — so click must not parse it: no interspersed options (the first word ends
# ours, and a later `--` survives), no -h of ours.
@cli.command(name="xray",
             context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False,
                               "help_option_names": []})
@click.argument("args", nargs=-1, type=click.UNPROCESSED,
                help="xray's flags, then the claude command exactly as you would type it")
def xray(args):
    """The exact prompt a claude launch would send, dry-run by the gateway.

    Put it in front of any claude command: [b]ak xray claude -p "hi"[/] (a program's session),
    [b]ak xray claude[/] (a person's). [b]-o NAME[/] saves it, [b]--vs NAME[/] shows what changed
    since; [b]--full[/], [b]--part system|tools|messages[/], [b]--json[/]. [b]ak xray -h[/] for more.
    """
    node = _node()
    _run_replace([node, "--experimental-strip-types", "--no-warnings",
                  str(_gateway_cli("xray.ts")), *(args or ("--help",))], env=_gateway_env())


# ── sysprompt ────────────────────────────────────────────────────────────────
# One Markdown file the shim hands to every `claude` launch. It lives beside
# conf.d, not in the vault: the shim reads it before this CLI (or uv, or python)
# is guaranteed to work, and it is machine config, not knowledge.
def _sysprompt_path():
    return Path(os.environ.get("CC_SYSPROMPT_FILE", SHIM_CFG / "system-prompt.md"))


@cli.group(cls=FuzzyGroup, invoke_without_command=True,
           context_settings={"help_option_names": ["-h", "--help"]})
@click.pass_context
def sysprompt(ctx):
    """The system prompt the shim appends to every `claude` launch.

    Bare [b]ak sysprompt[/] shows it; [b]ak sysprompt edit[/] opens it.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(sysprompt_show)


@sysprompt.command(name="show")
def sysprompt_show():
    """Print the file, byte for byte."""
    path = _sysprompt_path()
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    # Verbatim, never re-rendered as Markdown: what you must be able to check is
    # the exact bytes claude gets, and Rich's Markdown would reflow them. Plain
    # mode prints nothing but the file, so `ak sysprompt > copy.md` round-trips.
    if not ui.is_rich():
        click.echo(text, nl=False)
        return
    if not text.strip():
        ui.warn(f"no system prompt at {_tilde(str(path))}")
        ui.hint(cmd("sysprompt edit"))
        return
    flag = os.environ.get("CC_SYSPROMPT_FLAG", "--append-system-prompt-file")
    ui.panel(text.rstrip(), title=_tilde(str(path)), subtitle=f"{flag} · {len(text)} chars")


@writes
@sysprompt.command(name="edit")
def sysprompt_edit():
    """Open it in nvim ([b]$EDITOR[/] wins if set), then show the result."""
    path = _sysprompt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    click.edit(filename=str(path), editor=os.environ.get("EDITOR") or "nvim")
    click.get_current_context().invoke(sysprompt_show)


@sysprompt.command(name="path")
def sysprompt_path_cmd():
    """Print the file path — for scripts and [b]$EDITOR[/] plumbing."""
    click.echo(_sysprompt_path())



# ── hooks ────────────────────────────────────────────────────────────────────
# Hook tracing. Claude Code logs hook OUTPUT only under --debug and hook
# DURATION never, so "session wedged before init (25s)" can only ever name a
# suspect. `ak hooks on` rewrites every configured hook to run through
# hooks/hooktrace.py, which records the timing nothing else does.
@cli.group(cls=FuzzyGroup, invoke_without_command=True,
           context_settings={"help_option_names": ["-h", "--help"]},
           epilog=help_mod.epilog(f"Examples:\n  {CLI} hooks on          start recording\n  {CLI} hooks show        the newest session's runs"))
@click.pass_context
def hooks(ctx):
    """Trace hook runs — which ran, when, how long, exit code.

    Bare [b]ak hooks[/] is [b]status[/]. [b]on[/] starts recording,
    [b]off[/] stops and restores every command untouched.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(hooks_status)


@hooks.command(name="status")
@ui.json_option
def hooks_status(as_json):
    """What is traced, and what has been recorded so far."""
    ui.json_mode(as_json)
    rows = sorted(hooks_trace.scan(), key=lambda r: (r["source"], r["event"]))
    on = sum(1 for r in rows if r["wrapped"])
    sess = hooks_trace.sessions(limit=10)
    if ui.is_json():
        ui.emit({"traced": on, "hooks": rows, "sessions": sess})
        raise SystemExit(0)
    ui.table(["traced", "source", "event", "command"],
             [["[green]yes[/]" if r["wrapped"] else "[dim]no[/]", r["source"], r["event"],
               f"[dim]{_fmt(r['command'][:64])}[/]"] for r in rows], box=None)
    ui.text(f"{on} of {len(rows)} hooks traced")
    ui.hint(cmd("hooks on") if on == 0 else cmd("hooks off to stop"))
    if not sess:
        ui.hint("no traces recorded yet")
        return
    ui.table(["session", "hooks", "total", "slowest", "failed"],
             [[s["session"][:8], s["count"], f"{s['total_ms']}ms", f"{s['slowest_ms']}ms",
               f"[red]{s['failures']}[/]" if s["failures"] else "0"] for s in sess],
             justify=[None, "right", "right", "right", "right"], box=None)


@writes
@hooks.command(name="on")
@click.option("--all", "all_events", is_flag=True,
              help="also trace PreToolUse/PostToolUse — they fire on EVERY tool call")
@click.option("--dry-run", is_flag=True, help="report what would change, write nothing")
def hooks_on(all_events, dry_run):
    """Start recording: rewrite each hook to run through the tracer.

    Every command is preserved exactly and restored by [b]ak hooks off[/];
    each edited file is backed up first. Default covers the session-lifecycle
    events only — tool events fire constantly and the wrapper costs ~36ms.
    """
    events = hooks_trace.LIFECYCLE + (hooks_trace.TOOL if all_events else [])
    n, skipped, touched = hooks_trace.instrument(events, dry_run=dry_run)
    verb = "would trace" if dry_run else "tracing"
    ui.ok(f"{verb} {n} hook(s)" + (f" ({skipped} already traced)" if skipped else ""))
    for p in touched:
        ui.text(f"  [dim]{_tilde(str(p))}[/]")
    if not all_events and not dry_run:
        ui.hint("tool-call hooks left alone — add --all to include them")
    if not dry_run and n:
        ui.hint("takes effect on the next claude session")


@writes
@hooks.command(name="off")
def hooks_off():
    """Stop recording; put every hook command back as it was."""
    n, touched = hooks_trace.revert()
    ui.ok(f"restored {n} hook(s)") if n else ui.hint("nothing was traced")
    for p in touched:
        ui.text(f"  [dim]{_tilde(str(p))}[/]")


@hooks.command(name="show")
@click.argument("session", required=False, help=f"a session id as `{cmd('hooks')}` lists it (default: the newest)")
@ui.json_option
def hooks_show(session, as_json):
    """Every hook run in SESSION, in order (newest session when omitted)."""
    ui.json_mode(as_json)
    if not session:
        sess = hooks_trace.sessions(limit=1)
        if not sess:
            ui.err("no traces recorded yet")
            ui.hint(cmd("hooks on"))
            raise SystemExit(1)
        session = sess[0]["session"]
    recs = hooks_trace.records(session)
    if not recs:
        ui.err(f"no trace for {session}")
        raise SystemExit(1)
    if ui.is_json():
        ui.emit({"session": session, "runs": recs})
        return
    rows = []
    for r in recs:
        slow = r["dur_ms"] >= 1000
        if r["running"]:
            status = "[red]no exit[/]" if r["stale"] else "[yellow]running[/]"
        else:
            status = "0" if r["exit"] == 0 else f"[red]{r['exit']}[/]"
        rows.append([r["event"], r["source"], f"[red]{r['dur_ms']}[/]" if slow else r["dur_ms"],
                     status, f"[dim]{_fmt(r['command'][:56])}[/]"])
    ui.table(["event", "source", "ms", "exit", "command"], rows,
             justify=[None, None, "right", "right", None], box=None)
    live = sum(1 for r in recs if r["running"] and not r["stale"])
    ui.text(f"{len(recs)} hooks · {sum(r['dur_ms'] for r in recs)}ms total"
            + (f" · [yellow]{live} running[/]" if live else ""))


# ── search: every plugin that can search, at once ─────────────────────────────
# Each mounted module that defines `brain_search(words, limit)` answers (_SEARCH_HOOKS, filled by
# _mount_spec): memory's notes, the observer's rows, the sessions' transcripts. This verb knows none
# of them by name; it shows each one's best rows and the source's own search, which goes deeper.
@cli.command(name="search", aliases=["s"],
             epilog=help_mod.epilog(f"Examples:\n  {CLI} search wedge init         every source's best rows\n"
                                    f"  {CLI} s \"band flapping\" --limit 10   more rows from each\n"
                                    f"  {CLI} search zvec --json          one document, for tools"))
@click.argument("words", nargs=-1, required=True, help="what to look for, e.g. wedge init")
@click.option("--limit", type=click.IntRange(1, 50), default=5, help="rows from each source")
@ui.json_option
def search(words, limit, as_json):
    """Search every plugin that can search, at once.

    Each source's best rows, grouped by source; each group ends with that source's own
    [b]search[/] (the hint under it), which goes deeper. A source that fails says so on its own line rather than
    vanishing. Exit 1 when no source found anything."""
    ui.json_mode(as_json)
    q = " ".join(words)
    found = []
    for name, fn in _SEARCH_HOOKS:
        try:
            rows, error = list(fn(q, limit) or [])[:limit], None
        except Exception as e:  # noqa: BLE001 — one source failing must not hide the others
            rows, error = [], str(e) or type(e).__name__
        found.append({"source": name, "rows": rows, "error": error,
                      "more": cmd(f"{name} search {shlex.quote(q)}")})
    hits = sum(len(f["rows"]) for f in found)
    if ui.is_json():
        ui.emit({"query": q, "sources": found})
        raise SystemExit(0)
    if not found:
        ui.warn("no plugin answers search here")
        raise SystemExit(1)
    for f in found:
        if f["error"]:
            ui.warn(f"{f['source']}: search failed ({_fmt(f['error'])})")
            continue
        if not f["rows"]:
            ui.text(f"[b]{f['source']}[/] [dim]· nothing[/]")
            continue
        ui.table(["id", "when", "what"],
                 [[_fmt(r.get("id", "")), _fmt(r.get("when", "")), _fmt(r.get("title", ""))] for r in f["rows"]],
                 title=f"{f['source']} · {len(f['rows'])}", box=None)
        ui.hint(f["more"])
    raise SystemExit(0 if hits else 1)


# ── moving things: a workspace, the record of every move, the checkup (src/ak/move/) ──
from ak.move import cli as _move_cli  # noqa: E402

cli.add_command(_move_cli.moves)
hidden_alias(_move_cli.moves, _move_cli.moves_list, "list")  # the released spelling
cli.add_command(_move_cli.doctor)
cli.add_command(_move_cli.home)

# ── installing the kit: `ak setup` (src/ak/setup/) — self-contained, so it runs the same under uvx ──
from ak.setup import cli as _setup_cli  # noqa: E402

cli.add_command(_setup_cli.setup)

# ── what your plugins put in front of your agent: `ak plugin` (src/ak/plugin_inspect/), and `rename` from move ──
from ak.plugin_inspect import cli as _plugin_cli  # noqa: E402

cli.add_command(_plugin_cli.cli, name="plugin")
_plugin_cli.cli.add_command(_move_cli.plugin_rename)
_STATUS_HOOKS.append(("plugins", _plugin_cli.brain_status))


def _mount_move_extras(group):
    """`ws move` joins the vault's `workspaces` group. Runs after `_mount_plugin_clis`, so it never
    shadows a mounted group."""
    ws = group.commands.get("workspaces")
    if isinstance(ws, click.Group) and "move" not in ws.commands:
        ws.add_command(_move_cli.ws_move, name="move")


# ── extensibility seam: mount sibling plugin CLIs ──────────────────────────────
def _plugins_dir():
    """The directory holding the sibling plugins (`<repo>/plugins`), or None.

    AK_PLUGINS_DIR pins it outright. CLAUDE_PLUGIN_ROOT is only set when
    Claude Code launched us, and its parent is the plugins dir of whichever
    checkout is running. The installed `ak` is normally run from a bare shell,
    so the last answer is this file's own tree: `cli.py` sits at
    `plugins/ak/src/ak/cli.py`, so parents[3] is `plugins/`, and the
    editable install resolves the symlink back to the source checkout. Without
    it `ak shim` would exist only inside Claude Code, which is exactly where
    you do not need it. A wheel has no `pyproject.toml` beside its package, so
    there the answer is None: a built wheel never globs whatever folder it landed in."""
    pinned = env("PLUGINS_DIR")
    if pinned:
        return Path(pinned).expanduser()
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        return Path(root).resolve().parent
    if not (HOST_ROOT / "pyproject.toml").is_file():  # a wheel (uvx, a plain pip install): no checkout's plugins/ beside it
        return None
    d = Path(__file__).resolve().parents[3]
    return d if d.is_dir() else None


# This file's own plugin: `cli.py` sits at `<root>/src/ak/cli.py`. Its plugin.json `cli`
# entries are mounted at import (tests invoke `cli()` directly); the scan below skips it.
HOST_ROOT = Path(__file__).resolve().parents[2]


def _manifests(root: Path) -> list:
    """ROOT's plugin.json files — both homes: top-level (older plugins) and .claude-plugin/
    (what `claude plugin` writes). Same file, same contract."""
    return [p for p in (root / "plugin.json", root / ".claude-plugin" / "plugin.json") if p.is_file()]


def _cli_specs(pj: Path) -> list:
    """The `cli` entries of one plugin.json: an object or a list of them; [] when none or unreadable."""
    try:
        spec = (json.loads(pj.read_text(encoding="utf-8")) or {}).get("cli")
    except (OSError, ValueError, AttributeError):
        return []
    specs = spec if isinstance(spec, list) else [spec] if spec else []
    return [s for s in specs if isinstance(s, dict)]


def _add(group, command, name: str) -> None:
    """GROUP gains COMMAND as NAME, its declared aliases with it: rich-click keys an alias on the group
    it was declared in, so a command mounted into another group says them again (`ak ws`, `ak tpl`)."""
    group.add_command(command, name=name, aliases=list(getattr(command, "aliases", None) or []) or None)


def _mount_spec(group, root: Path, spec: dict) -> None:
    """Mount one `cli` entry of the plugin at ROOT: {command, entry | module, attr, hidden?, hoist?}.

    `entry` is a file path under ROOT, loaded by path; `module` an import name, imported (a package
    the host's environment carries). `hidden` mounts it out of every list (an old spelling: `ak tracer`).
    `hoist` names subcommands of the mounted group that are also added at the top level — the same
    click object, so its help and aliases are the ones it has. An entry is a name, or {command, as?,
    hidden?} to put it at the top under another name, or out of the lists (`ak links` still runs)."""
    try:
        if spec.get("module"):
            mod = importlib.import_module(spec["module"])
        else:
            entry = root / spec.get("entry", "")
            if not entry.is_file():
                return
            modname = f"_plugincli_{root.name}"
            mod = sys.modules.get(modname)
            if getattr(mod, "__file__", None) != str(entry):  # one load per file: entries that share it share its objects
                mod = _load_path(modname, entry)
        command = getattr(mod, spec.get("attr", "cli"))
    except Exception as e:  # noqa: BLE001 — one bad plugin must not sink the CLI
        ui.warn(f"skipped {root.name} cli ({e})", err=True)
        return
    name = spec.get("command", root.name)
    # A native verb of the same name wins over a mounted one. The status and boot
    # hooks below are registered either way — the other half of the contract.
    if name not in group.commands:
        _place(group, command, name, bool(spec.get("hidden")))
    for sub in spec.get("hoist") or []:
        # a name, or {command, as, hidden}: `{"command": "links", "hidden": true}` keeps `ak links` running, unlisted
        if isinstance(sub, str):
            sub, top, hide = sub, sub, False
        else:
            sub, top, hide = sub.get("command"), sub.get("as") or sub.get("command"), bool(sub.get("hidden"))
        verb = command.commands.get(sub) if isinstance(command, click.Group) else None
        if verb is None:
            ui.warn(f"{root.name} cli: no `{name} {sub}` to hoist")
        elif top not in group.commands:
            _place(group, verb, top, hide)
    # One module may back several entries (the tracer's sessions · trace · tracer): each hook once,
    # under the first name it was mounted as.
    for hooks, attr in ((_STATUS_HOOKS, "brain_status"), (_BOOT_HOOKS, "brain_boot"), (_SEARCH_HOOKS, "brain_search")):
        fn = getattr(mod, attr, None)
        if callable(fn) and all(f is not fn for _, f in hooks):
            hooks.append((name, fn))


def _place(group, command, name: str, hidden: bool) -> None:
    """GROUP gains COMMAND as NAME: listed on the root help (under Plugins when COMMAND_GROUPS does
    not place it), or hidden — it runs, and no list or suggestion shows it."""
    if hidden:
        hidden_alias(group, command, name)
        return
    _add(group, command, name)
    if not help_mod.listed(name):
        help_mod.add_to_group("Plugins", name)


def mount_plugin(group, root: Path) -> None:
    """Every `cli` entry of the plugin at ROOT, through the contract (`_mount_spec`). A test that
    invokes `cli()` in-process mounts the plugins it exercises with this, as `main()` mounts them all."""
    for pj in _manifests(root):
        for spec in _cli_specs(pj):
            _mount_spec(group, root, spec)


def _mount_host_clis(group) -> None:
    """The `cli` entries of this file's own plugin (HOST_ROOT), through the same contract."""
    mount_plugin(group, HOST_ROOT)


def _mount_plugin_clis(group):
    """Mount every sibling plugin's declared `cli` contribution as a subcommand.

    OCP/DIP seam: the `ak` CLI depends only on a CONTRACT — a plugin.json `cli`
    block exposing a click group — never on any plugin by name. The block is one
    entry or a list of them, each {command, entry | module, attr, hoist?: [names]}
    (`_mount_spec`). Adding a module means shipping a plugin that declares it; this
    host is not edited. Resilient by design: one broken module warns to stderr and
    is skipped, so the host's own commands never go down with it (fail-safe host).

    Optional halves of the contract, module-level in the mounted module:
    `brain_status()` returning [(label, markup)] rows gets that plugin a line on
    the bare-`ak` front door. It runs on every bare invocation, so it must be free
    — cache and environment only, never network. `brain_boot(ctx)` runs from the
    root callback before any verb: what the module must have in place first.
    `brain_search(words, limit)` returning [{id, title, when?, where?}] puts the
    plugin in `ak search`, under the name it was mounted as.
    """
    plugins_dir = _plugins_dir()
    if plugins_dir is None or not plugins_dir.is_dir():
        if ui.is_rich():  # a person at a terminal: say why the plugin verbs are missing (an agent's line stays clean)
            ui.warn(f"no plugins folder found — the plugin verbs are missing; set {env_name('PLUGINS_DIR')} "
                    "to the checkout's plugins/")
        return
    found = []
    for pj in sorted([*plugins_dir.glob("*/plugin.json"),
                      *plugins_dir.glob("*/.claude-plugin/plugin.json")]):
        root = pj.parent.parent if pj.parent.name == ".claude-plugin" else pj.parent
        if root.resolve() == HOST_ROOT:
            continue  # the host's own plugin: mounted at import (_mount_host_clis)
        found.append((root, pj, _cli_specs(pj)))
    _reach_sources(found)
    on = _enabled_state()
    if on is not None:
        from ak.plugin_inspect.installed import is_on
    for root, pj, specs in found:
        if on is not None and not is_on(root, _plugin_name(root, pj), on):
            continue  # present, switched off in Claude Code: none of its verbs
        for spec in specs:
            _mount_spec(group, root, spec)  # one manifest at a time: a plugin may carry both homes


def _plugin_name(root: Path, pj: Path) -> str:
    """The name Claude Code keys the plugin by (`<name>@<marketplace>`): plugin.json's name, else the folder's."""
    try:
        name = (json.loads(pj.read_text(encoding="utf-8")) or {}).get("name")
    except (OSError, ValueError, AttributeError):
        name = None
    return name or root.name


def _enabled_state() -> dict | None:
    """enabledPlugins as Claude Code reads it (user, then project, then local settings, a later file
    winning per id — plugin_inspect.installed), or None when that cannot be told: then every plugin
    that is present is mounted. Which id is THIS folder's — `tracer@ak` in the workspace,
    `tracer@agentic-kit` for the public kit — is `installed.is_on`'s to find. It cannot be told when AK_PLUGINS_DIR pins a folder of its own
    (a sandbox, a test's fabricated plugins), under pytest (a checkout under test mounts what it has),
    or when no settings file names a plugin at all."""
    if env("PLUGINS_DIR") or os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    from ak.plugin_inspect.installed import enabled
    return enabled(Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())) or None


def _reach_sources(found: list) -> None:
    """A `module` entry is an import name the ak Python carries; in a checkout whose venv does not
    (ak builds alone, so it declares no sibling), that plugin's own src/ is put LAST on sys.path —
    an installed copy still wins. Every such plugin, on or off: memory's code imports the vault's."""
    for root, _pj, specs in found:
        src = root / "src"
        if any(s.get("module") for s in specs) and src.is_dir() and str(src) not in sys.path:
            sys.path.append(str(src))


_mount_host_clis(cli)  # the host's own plugin.json `cli` entries, if it declares any — before any verb is looked up


def _on(flag: bool, on: str = "on", off: str = "off") -> str:
    if not ui.is_rich():
        return on if flag else off
    return f"[green]●[/] {on}" if flag else f"[red]○[/] {off}"


def _hooks_status() -> list:
    """The hooks row: how many hooks `ak hooks on` wraps for tracing."""
    try:
        scan = hooks_trace.scan()
        on = sum(1 for r in scan if r["wrapped"])
        v = _on(on > 0, on=f"tracing {on}/{len(scan)}", off="not traced")
    except Exception as e:  # noqa: BLE001
        v = f"[red]unavailable[/] [dim]({e})[/]"
    return [("hooks", v, cmd("hooks on|off"))]


def _status_rows() -> list:
    """The band's rows: (label, value, the command that changes it), one source
    after another — the vault's, the hooks', the shim's launcher line, then the
    mounted plugins' (_STATUS_HOOKS). Every source is wrapped — a status line that
    can fail is a front door that can fail. Cache, stat and one sqlite COUNT only:
    this runs on every bare `ak`."""
    rows = []
    for name, hook in _STATUS_HOOKS:
        try:
            for r in hook() or []:  # (label, value) or (label, value, hint)
                rows.append((r[0], r[1], r[2] if len(r) > 2 else ""))
        except Exception as e:  # noqa: BLE001
            rows.append((name, f"[red]status unavailable[/] [dim]({e})[/]", ""))
    return rows


def _front_door():
    """The status band UNDER the command list — the last thing printed is the
    first thing seen at a prompt: what this shell is bound to, each row carrying
    the command that changes it. Never fatal: a broken source costs its own line."""
    rows = _status_rows()
    if not rows:
        return
    if ui.is_rich():
        from rich.table import Table
        t = Table(box=None, pad_edge=False, show_header=False, padding=(0, 2, 0, 0))
        t.add_column(style="dim", no_wrap=True)
        t.add_column(overflow="fold")
        t.add_column(style="dim", no_wrap=True, justify="right")
        for label, value, hint in rows:
            t.add_row(label, value, hint)
        ui.console.print(t)
        ui.text(f"[dim]{_version_line()} · ak <verb> -h for one verb[/]")
        return
    ui.kv([(label, f"{value}" + (f"  → {hint}" if hint else "")) for label, value, hint in rows], title="status")


# ── run the checkout you are standing in ───────────────────────────────────────
# The installed `ak` is an editable install of ONE checkout: the release clone
# (RELEASING.md). Inside any other checkout or worktree of the repo, the code you
# are editing is the one you want to run, so `ak` hands off to that tree's
# plugin before doing anything else. Silent from the release clone, where the two
# are the same; AK_NO_REEXEC=1 pins the installed one.
INSTALLED_PLUGIN = Path(__file__).resolve().parents[2]


def _checkout_above(start: Path) -> Path | None:
    """The nearest ancestor of START that is a checkout of this repo — it carries
    `plugins/ak/src/ak/cli.py` — or None."""
    for d in (start, *start.parents):
        if (d / "plugins" / "ak" / "src" / "ak" / "cli.py").is_file():
            return d
    return None


def _reexec_into_checkout() -> None:
    if env("NO_REEXEC"):
        return
    root = _checkout_above(Path.cwd().resolve())
    if root is None:
        return
    plugin = root / "plugins" / "ak"
    if plugin.resolve() == INSTALLED_PLUGIN:
        return
    child_env = {**os.environ, env_name("NO_REEXEC"): "1"}
    if ui.is_rich():
        ui.text(f"[dim]{CLI} · running the checkout at {_fmt(root)}[/]", err=True)
    uv = shutil.which("uv")
    if uv:  # that tree's own venv, so a dependency it added is there too
        _run_replace([uv, "run", "--quiet", "--project", str(plugin), CLI, *sys.argv[1:]], child_env)
    # no uv: the checkout's packages by path — this plugin's first, then every sibling's src/ (vault, memory …)
    srcs = [str(plugin / "src"), *(str(s) for s in sorted((root / "plugins").glob("*/src")) if s != plugin / "src")]
    child_env["PYTHONPATH"] = os.pathsep.join(p for p in (*srcs, child_env.get("PYTHONPATH")) if p)
    _run_replace([sys.executable, "-m", "ak.cli", *sys.argv[1:]], child_env)


def _verb() -> str | None:
    """The first word on the command line that is not an option."""
    return next((a for a in sys.argv[1:] if not a.startswith("-")), None)


def main():
    utf8_stdio()  # before anything prints: a cp1252 console or pipe cannot encode ✓ → ⚠ or a user's emoji title
    if _verb() != "setup":  # setup is self-contained: the same under uvx and installed, and it never hands off to a checkout
        _reexec_into_checkout()
        _mount_plugin_clis(cli)
        _mount_move_extras(cli)
    _settle_root_help(cli)
    # prog_name pinned to a known name: rich-click keys its command groups on the command
    # path, and `python -m ak.cli` would otherwise print an ungrouped catalogue.
    cli(prog_name=CLI)


if __name__ == "__main__":
    main()
