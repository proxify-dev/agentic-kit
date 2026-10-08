"""tracer trace (and its old spelling, tracer calls) — every tool call from every session, read from the tool-call index.

`traces.db` holds one row per tool_use_id, from every session and every door. Its
engine ships in this plugin (traces/, TypeScript bundled to one committed file,
dist/traces.mjs) and is kept current by this plugin's Stop hook after each turn,
whether or not the app is open. It prints exactly one JSON document per verb: run as
`node traces/bin/traces.mjs <verb>` on any OS, or through the POSIX shell door
`traces/bin/traces <verb>`. This module finds the engine and a node ≥ 22.13, runs it,
and renders the document through this plugin's copy of ak's door (`tracer.ui`); `--json`
is the document itself, unchanged. tracer/cli.py forks this group into `tracer trace`, which
adds the transcript verbs (`turn`, `blame`) and an `index` that runs both indexes; inside the
`ak` CLI the same group is `ak trace`.

The engine's exits, and what they become here:

  0    the document                        rendered, or emitted under --json
  1    not found / no index yet            error: <its line>, exit 1
  2    a session prefix matches several     the candidates, exit 2
  64   usage                                error: <its line>, exit 64
  66   no bundle (traces/dist)              error: what is missing, exit 66
  127  no node >= 22.13                     error: what is missing, exit 127

`purge` is a person's verb: it deletes history, so it refuses to run from an agent's
shell (CLAUDECODE) or without a terminal on stdin.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import rich_click as click

from tracer import ui
from tracer.groups import FuzzyGroup, writes

from ._brand import data_dir, env, env_name, is_windows
from .verbs import command

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = Path("traces") / "bin" / "traces"  # under a tracer plugin root: the POSIX shell door
ENTRY = Path("traces") / "bin" / "traces.mjs"  # the same engine, for `node` on any OS
# Seconds any one engine run may take. Its longest verb is `index`, capped at 30 s by
# the engine itself (traces-index.ts MAX_MS); a list's refresh is a smaller pass.
TIMEOUT = 60
TRACE = command("trace")  # how the epilogs and hints spell this command
LIMIT = 30  # rows on a page of `tracer trace`
CAP = 8 * 1024  # bytes of each payload `get` shows without --full
TOP_TOOLS = 15  # rows of the per-tool table `stats` shows; --json has them all

ORIGIN_HELP = ("which door the call came through:\n\n\b\n"
               "session     a Claude Code session (a terminal, claude -p)\n"
               "agent       the app's chat\n"
               "automation  an automation the app ran\n"
               "bench       the app's tool bench")
OUTCOME_HELP = ("how the call ended:\n\n\b\n"
                "ok       it ran and returned\n"
                "error    it ran and failed\n"
                "refused  the vault said no\n"
                "denied   a person or a permission rule said no first\n"
                "running  no result yet")
OUTCOME_STYLE = {"ok": "green", "error": "red", "refused": "yellow", "denied": "yellow", "running": "cyan"}


# ── node ≥ 22.13, wherever it is ─────────────────────────────────────────────
# The engine imports node:sqlite, unflagged from 22.13. PATH's node is often an old
# default, or absent from a GUI app's PATH, so the places node installers put it are
# tried too (traces/bin/find-node.sh's list, plus volta, fnm, asdf and Windows).
# hooks/convo-index-stop.py carries the same finder: it runs on uv's bare Python, before any tracer env.
NODE_MIN = (22, 13)
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def _newest_first(paths) -> list:
    """Version folders newest first, by the version parsed from each path (a string sort puts v9 above v22)."""
    def ver(p):
        found = _VERSION.findall(str(p))
        return tuple(int(x) for x in found[-1]) if found else (0, 0, 0)
    return sorted(paths, key=ver, reverse=True)


def _node_candidates():
    home = Path.home()
    yield shutil.which("node")
    if is_windows():
        for var, rel in (("ProgramFiles", "nodejs/node.exe"), ("LOCALAPPDATA", "Programs/nodejs/node.exe"),
                         ("USERPROFILE", ".volta/bin/node.exe")):
            if os.environ.get(var):
                yield Path(os.environ[var]) / rel
        for nvm in (os.environ.get("NVM_HOME"), os.environ.get("APPDATA") and Path(os.environ["APPDATA"]) / "nvm"):
            if nvm:
                yield from _newest_first(Path(nvm).glob("v*/node.exe"))
        return
    yield from _newest_first(Path(os.environ.get("NVM_DIR") or home / ".nvm").glob("versions/node/v*/bin/node"))
    fnm = [os.environ.get("FNM_DIR"), data_dir() / "fnm", home / ".fnm",
           home / "Library" / "Application Support" / "fnm"]  # portable: ok — fnm's macOS default
    for d in fnm:
        if d:
            yield from _newest_first(Path(d).glob("node-versions/v*/installation/bin/node"))
    yield home / ".volta" / "bin" / "node"
    yield Path(os.environ.get("ASDF_DATA_DIR") or home / ".asdf") / "shims" / "node"
    yield "/opt/homebrew/bin/node"  # portable: ok — Homebrew on Apple Silicon, a POSIX-only candidate
    yield "/usr/local/bin/node"
    yield "/usr/bin/node"


def _node_version(exe: str):
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, stdin=subprocess.DEVNULL,
                             creationflags=0x08000000 if is_windows() else 0).stdout  # CREATE_NO_WINDOW
    except (OSError, subprocess.SubprocessError):
        return None
    m = _VERSION.match((out or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def find_node() -> tuple:
    """(path, None) for the first node ≥ 22.13 among the candidates; else (None, why) — the why names
    the too-old node it saw, so the error does not say "not found" about a node the user can see."""
    seen, too_old = set(), None
    for c in _node_candidates():
        if not c or not os.path.isfile(c):
            continue
        key = os.path.realpath(c)
        if key in seen:
            continue
        seen.add(key)
        v = _node_version(str(c))
        if v is None:
            continue
        if v[:2] >= NODE_MIN:
            return str(c), None
        too_old = too_old or f"node {'.'.join(map(str, v))} at {c} is below the required 22.13 (node:sqlite)"
    return None, too_old or "no node >= 22.13 on PATH or in the usual install places"


# ── the engine ───────────────────────────────────────────────────────────────


def _roots() -> list:
    """Tracer plugin roots that may carry the engine, first match wins: the one in
    the checkout $AK_CODE names, then this plugin's own. Inside a worktree `ak` already
    runs that tree's plugins, so this plugin's own is the worktree's."""
    code = env("CODE")
    return [Path(code) / "plugins" / "tracer" if code else None, PLUGIN_ROOT]


def launcher() -> Path:
    """The engine's door in the first tracer root that has one: the shell launcher on macOS/Linux (it also
    finds a node new enough when PATH's is not), else the .mjs entry node runs (Windows, where no shell is
    assumed)."""
    for root in _roots():
        if not root:
            continue
        if not is_windows() and os.access(Path(root) / LAUNCHER, os.X_OK):
            return Path(root) / LAUNCHER
        if (Path(root) / ENTRY).is_file():
            return Path(root) / ENTRY
    ui.err(f"no trace engine: neither ${env_name('CODE')}/plugins/tracer nor this plugin ({PLUGIN_ROOT}) "
           f"has {ENTRY} — reinstall the tracer plugin, or set {env_name('CODE')} to a checkout of the kit repo")
    raise SystemExit(66)


def _argv(exe: Path) -> list:
    if exe.suffix != ".mjs":
        return [str(exe)]
    node, why = find_node()
    if not node:
        ui.err(f"{why} — the trace engine reads traces.db through node:sqlite")
        ui.hint("install Node 22.13 or newer (nvm install 22), then run it again")
        raise SystemExit(127)
    return [node, "--no-warnings", str(exe)]


def engine(verb: str, *args: str, miss: str | None = None) -> dict:
    """Run one engine verb and return its document. Every failure ends here, said
    the way this CLI says things; MISS is the hint a not-found answer ends on."""
    exe = launcher()
    try:
        # From the home directory, as traces/bin's doors run it: the store resolves from the cwd,
        # and a project carrying its own vault would otherwise get a trace store of its own.
        p = subprocess.run([*_argv(exe), verb, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=TIMEOUT, cwd=str(Path.home()))
    except subprocess.TimeoutExpired:
        ui.err(f"the trace engine gave no answer in {TIMEOUT} s and was stopped — traces.db may be "
               "held by a long write, or a large backlog is being indexed")
        ui.hint(command("trace index"))
        raise SystemExit(1)
    lines = (p.stderr or "").strip().splitlines()
    # The engine's own line, without its "traces: " name (ours is already on the
    # line) and escaped: it is text we did not write, and a `[x]` in it is not markup.
    said = ui.escape(lines[-1].removeprefix("traces: ")) if lines else ""
    if p.returncode == 0:
        try:
            return json.loads(p.stdout)
        except ValueError:
            ui.err(f"traces {verb} printed no JSON document" + (f": {said}" if said else ""))
            raise SystemExit(1)
    if p.returncode == 2:
        _ambiguous(p.stdout, said)
    if p.returncode == 127:
        ui.err("no node ≥ 22.13 — the trace engine reads traces.db through node:sqlite"
               + (f" ({said})" if said else ""))
        ui.hint("install Node 22.13 or newer (nvm install 22), then run it again")
    elif p.returncode == 66:
        ui.err(f"the trace engine is not built: no {exe.parents[1] / 'dist' / 'traces.mjs'}")
        ui.hint(f"cd {exe.parents[1]} && npm run build — or set {env_name('CODE')} to a checkout that has it")
    else:
        ui.err(said or f"traces {verb} exited {p.returncode}")
        if p.returncode == 1 and miss:
            ui.hint(miss)
    raise SystemExit(p.returncode)


def _ambiguous(stdout: str, said: str = "") -> None:
    try:
        doc = json.loads(stdout)
    except ValueError:
        doc = {"candidates": []}
    cands = doc.get("candidates") or []
    if ui.is_json():
        ui.emit(doc)
        raise SystemExit(2)
    # The engine's line says "more than 20" when the list is capped; a count of
    # the list alone would call that twenty.
    ui.warn(f"ambiguous: {said}" if said else f"ambiguous: that session prefix matches {len(cands)} sessions")
    ui.table(["session"], [[c] for c in cands], box=None)
    if cands:
        ui.hint(f"{command('trace')} --session {cands[0]}")
    raise SystemExit(2)


def _flags(**kw) -> list[str]:
    """`--name value` for each option given, in the engine's spelling."""
    out: list[str] = []
    for k, v in kw.items():
        if v is not None:
            out += [f"--{k}", str(v)]
    return out


# ── time ─────────────────────────────────────────────────────────────────────
def iso(at: datetime) -> str:
    """UTC, milliseconds, Z: the spelling the index stores, so a string compare is a time compare."""
    return at.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class When(click.ParamType):
    """A point in time as a person types it: 30m / 2h / 1d / 7d back from now, or an
    ISO date or instant (without a zone it is local time). Handed on as ISO UTC."""
    name = "when"
    AGO = re.compile(r"(\d+)\s*([smhdw])")
    UNIT = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}

    def convert(self, value, param, ctx):
        s = str(value).strip()
        m = self.AGO.fullmatch(s)
        if m:
            return iso(datetime.now(timezone.utc) - timedelta(**{self.UNIT[m[2]]: int(m[1])}))
        try:
            at = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
        except ValueError:
            self.fail(f"{value!r} is not a time — give 30m, 2h, 1d, 7d, or an ISO date like 2026-09-20",
                      param, ctx)
        return iso(at if at.tzinfo else at.astimezone())


WHEN = When()


def _local(ts: str | None, fmt: str = "%m-%d %H:%M:%S") -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime(fmt)
    except ValueError:
        return ts


# ── shapes into text ─────────────────────────────────────────────────────────
def _dur(ms) -> str:
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms}ms"
    s = ms / 1000
    return f"{s:.1f}s" if s < 60 else f"{int(s // 60)}m{int(s % 60):02d}s"


def _size(n) -> str:
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    return f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def _what(row: dict) -> str:
    summary = row.get("summary") or {}
    return summary.get("label") or summary.get("input") or ""


def _clip(s: str | None, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _outcome(o: str) -> str:
    style = OUTCOME_STYLE.get(o)
    return f"[{style}]{o}[/]" if style else ui.escape(o)


def _readable(v) -> str:
    """A payload as a person reads it: text as it is; a tool's content blocks as
    their text; a tool's input as `key: value` lines; anything else as JSON."""
    if isinstance(v, str):
        return v
    if isinstance(v, list) and v and all(isinstance(b, dict) and "type" in b for b in v):
        return "\n".join(_block(b) for b in v)
    if isinstance(v, dict):
        lines = []
        for k, x in v.items():
            s = x if isinstance(x, str) else json.dumps(x, indent=2, ensure_ascii=False)
            if "\n" in s:
                lines += [f"{k}:", *(f"  {ln}" for ln in s.splitlines())]
            else:
                lines.append(f"{k}: {s}")
        return "\n".join(lines)
    return json.dumps(v, indent=2, ensure_ascii=False)


def _block(b: dict) -> str:
    if b.get("type") == "text":
        return str(b.get("text", ""))
    if b.get("type") == "image":
        src = b.get("source") or {}
        return f"[image {src.get('media_type', '')} · {_size(len(str(src.get('data', ''))))} base64]"
    return json.dumps(b, ensure_ascii=False)


def _payload(side: str, body, nbytes: int, full: bool, running: bool) -> bool:
    """One payload as a panel, its first CAP bytes unless FULL. True when it was cut."""
    if body is None:
        ui.warn(f"no {side} yet — the call is still running" if running and side == "output"
                else f"no {side} on record — its blob or transcript line is gone")
        return False
    text = _readable(body)
    raw = text.encode("utf-8")
    cut = not full and len(raw) > CAP
    if cut:
        text = raw[:CAP].decode("utf-8", errors="ignore")
    ui.panel(text, title=f"{side} · {_size(nbytes)}",
             subtitle=f"first {_size(CAP)} of {_size(len(raw))} · --full shows all" if cut else None)
    return cut


def _row_kv(r: dict) -> list:
    who = r.get("identity") or {}
    shape = r.get("shape") or {}
    rows = [
        ("id", r["id"]),
        ("when", f"{_local(r.get('ts'), '%Y-%m-%d %H:%M:%S')}  ({r.get('ts')})"),
        ("took", _dur(r.get("durMs"))),
        ("origin", r.get("origin")),
        ("session", who.get("session")),
        ("prompt", who.get("prompt")),
        ("cwd", who.get("cwd")),
        ("agent", who.get("agent")),
        ("parent", r.get("parent")),
        ("run", who.get("run")),
        ("item", who.get("item")),
        ("bytes", f"{_size(shape.get('bytesIn'))} in · {_size(shape.get('bytesOut'))} out"),
        ("witnesses", " · ".join(r.get("witnesses") or [])),
        ("policy", r.get("policy")),
        ("ledger", r.get("ledger")),
        ("error", r.get("error")),
    ]
    # Text this CLI did not write is escaped, so a `[x]` in a path or an error
    # survives both the rich and the plain renderer.
    out = [(k, ui.escape(str(v))) for k, v in rows if v not in (None, "")]
    out.insert(2, ("outcome", _outcome(r.get("outcome", ""))))
    return out


# ── the verbs ────────────────────────────────────────────────────────────────
@click.group(name="trace", cls=FuzzyGroup, invoke_without_command=True,
             context_settings={"help_option_names": ["-h", "--help"]},
             epilog=(
                 "\b\nExamples:\n"
                 f"  {TRACE}                                          the newest calls, every session\n"
                 f"  {TRACE} --session 6eced009 --limit 5             one session's last five\n"
                 f"  {TRACE} --tool Bash --outcome error --since 1d   a day of failed Bash calls\n"
                 f"  {TRACE} --server ak --q recall                the kit tools' recall calls\n"
                 f"  {TRACE} get toolu_01XTyh6ey36W8a97N9co8AWD       one call: its input and output\n"
                 f"  {TRACE} stats                                    totals, and calls per tool"))
@click.option("--tool", help="one tool's calls, by its name without the mcp__ prefix, e.g. Bash or recall")
@click.option("--server", help="one server's calls: claude for the built-in tools, else the MCP server, e.g. vault")
@click.option("--session", help="one session, by its id, its first characters or latest, e.g. 6eced009")
@click.option("--origin", type=click.Choice(["session", "agent", "automation", "bench"]), help=ORIGIN_HELP)
@click.option("--outcome", type=click.Choice(list(OUTCOME_STYLE)), help=OUTCOME_HELP)
@click.option("--since", type=WHEN, help="calls from this point on: 30m, 2h, 1d, 7d ago, or an ISO date/instant, e.g. 2026-09-20")
@click.option("--until", type=WHEN, help="calls before this point, in the same forms, e.g. 2h")
@click.option("--q", help="text in the tool name, the call's label or either summary line, e.g. pytest")
@click.option("--limit", type=click.IntRange(1, 500), default=LIMIT, help="rows on this page, e.g. 100")
@click.option("--cursor", help="where the next page starts: the value the last page's hint gives")
@ui.json_option
@click.pass_context
def trace(ctx, tool, server, session, origin, outcome, since, until, q, limit, cursor, as_json):
    """List every tool call from every session, newest first.

    One row per call, from every session and door (a terminal, claude -p, the app),
    out of the index the tracer Stop hook updates after each turn. Before it
    reads, a list indexes what the last hour's transcripts gained, in one short pass.
    When another index is already running, the list asks that one to go again and
    reads the store as it stands, so the newest calls can be missing for a few seconds.
    [b]tracer trace get <id>[/] shows one call whole.
    """
    from tracer.storage import indexing_note
    note = indexing_note()
    if note:  # on stderr: a pipe or --json still gets only its document
        ui.warn(note, err=True)
    if ctx.invoked_subcommand is not None:
        return
    ui.json_mode(as_json)
    if session == "latest":  # the one word every verb reads (drill.resolve_session); a prefix the engine resolves
        from .drill import latest_session

        session = latest_session() or session
    filters = dict(tool=tool, server=server, session=session, origin=origin, outcome=outcome,
                   since=since, until=until, q=q)
    page = engine("list", *_flags(**filters, limit=limit, cursor=cursor), "--refresh",
                  miss=command("trace index"))
    if ui.is_json():
        ui.emit(page)
        return
    rows = page.get("rows") or []
    if not rows:
        narrowed = any(v is not None for v in filters.values()) or cursor
        ui.warn("no tool calls match" if narrowed else "no tool calls in the index yet")
        ui.hint(command("trace --since 1d") if narrowed else command("trace index"))
        return
    # what: the call's label — its description, else `$ command`, a path (summary.label);
    # a row the index has not re-read yet has only its input line. came back: the result.
    ui.table(["time", "session", "tool", "outcome", "took", "what", "came back", "id"],
             [[_local(r.get("ts")), ((r.get("identity") or {}).get("session") or "—")[:8],
               ui.escape(f"{r.get('server')}.{r.get('tool')}"), _outcome(r.get("outcome", "")),
               _dur(r.get("durMs")), ui.escape(_clip(_what(r), 72)),
               ui.escape(_clip((r.get("summary") or {}).get("output"), 40)),
               ui.escape(r["id"])] for r in rows],
             styles=["dim", "cyan", None, None, None, None, "dim", "dim"],
             justify=[None, None, None, None, "right", None, None, None], box=None)
    ui.hint(ui.escape(f"{command('trace get')} {rows[0]['id']}"))
    if page.get("next"):
        more = [*_flags(**filters), *(["--limit", str(limit)] if limit != LIMIT else []), "--cursor", page["next"]]
        ui.hint(ui.escape(" ".join([command("trace"), *map(shlex.quote, more)])))


@trace.command(name="get", epilog=(
    "\b\nExamples:\n"
    f"  {TRACE} get toolu_01XTyh6ey36W8a97N9co8AWD          the row, input and output\n"
    f"  {TRACE} get toolu_01XTyh6ey36W8a97N9co8AWD --full   every byte of both payloads"))
@click.argument("tool_use_id", help=f"a call's id as `{command('trace')}` lists it, e.g. toolu_01XTyh6ey36W8a97N9co8AWD")
@click.option("--full", is_flag=True, help=f"the whole of each payload, not its first {CAP // 1024} KB")
@ui.json_option
def trace_get(tool_use_id, full, as_json):
    """Show one tool call: its row, input and output.

    Each payload is cut at 8 KB unless [b]--full[/]; [b]--json[/] is always whole.
    """
    ui.json_mode(as_json)
    d = engine("get", tool_use_id, miss=command("trace --q <text in the call>"))
    if ui.is_json():
        ui.emit(d)
        return
    r = d["row"]
    shape = r.get("shape") or {}
    running = r.get("outcome") == "running"
    ui.kv(_row_kv(r), title=ui.escape(f"{r.get('server')}.{r.get('tool')}"))
    cut = _payload("input", d.get("input"), shape.get("bytesIn"), full, running)
    cut = _payload("output", d.get("output"), shape.get("bytesOut"), full, running) or cut
    if cut:
        ui.hint(ui.escape(f"{command('trace get')} {r['id']} --full"))
    session = (r.get("identity") or {}).get("session")
    if session:
        ui.hint(ui.escape(f"{command('trace')} --session {session}"))


@trace.command(name="stats", epilog=(
    "\b\nExamples:\n"
    f"  {TRACE} stats                  the whole index\n"
    f"  {TRACE} stats --server vault   the kit tools only"))
@click.option("--server", help="one server's numbers: claude for the built-in tools, else the MCP server, e.g. vault")
@ui.json_option
def trace_stats(server, as_json):
    """Count the calls in the index: total, today, errors."""
    ui.json_mode(as_json)
    s = engine("stats", *_flags(server=server), miss=command("trace index"))
    if ui.is_json():
        ui.emit(s)
        return
    last = s.get("lastAt")
    ui.kv([("calls", s.get("total", 0)), ("today", s.get("today", 0)),
           ("errors", f"{s.get('errors', 0)}  (error · denied · refused)"),
           ("running", s.get("running", 0)),
           ("last", f"{_local(last, '%Y-%m-%d %H:%M:%S')}  ({last})" if last else "—")],
          title="tool-call index" + (f" · {ui.escape(server)}" if server else ""))
    tools = sorted((s.get("tools") or {}).items(), key=lambda t: -t[1])
    if tools:
        ui.table(["tool", "calls"], [[ui.escape(t), n] for t, n in tools[:TOP_TOOLS]],
                 justify=[None, "right"], box=None)
    if len(tools) > TOP_TOOLS:
        ui.text(f"{len(tools) - TOP_TOOLS} more tools — --json lists them all")
    ui.hint(command("trace --outcome error --since 1d") if s.get("errors") else command("trace --since 1d"))


@writes
@trace.command(name="index", epilog=(
    "\b\nExamples:\n"
    f"  {TRACE} index            every transcript that changed since the last pass\n"
    f"  {TRACE} index --recent   only transcripts changed in the last hour"))
@click.option("--recent", is_flag=True, help="only transcripts changed in the last hour")
@ui.json_option
def trace_index(recent, as_json):
    """Index new tool calls now, as the Stop hook does.

    Reads only the bytes each transcript gained since the last pass. When another
    index is already running it asks that one to go again and returns at once.
    """
    ui.json_mode(as_json)
    d = engine("index", *(["--recent"] if recent else []))
    if ui.is_json():
        ui.emit(d)
        return
    ui.ok(indexed(d))
    ui.hint(command("trace"))


def indexed(d: dict) -> str:
    """What one pass of the tool-call index did, in one line."""
    if d.get("locked"):
        return "another index is running — it takes in what is new"
    if not d.get("files"):
        return f"up to date · {_dur(d.get('ms'))}"
    passes = d.get("passes") or 1
    return (f"indexed {d['files']} file{'s' if d['files'] != 1 else ''} · {_size(d.get('bytes'))} · "
            f"{_dur(d.get('ms'))}" + (f" · {passes} passes" if passes > 1 else ""))


def _at_a_keyboard() -> bool:
    """A person, not an agent: no CLAUDECODE, and a terminal on stdin to answer the prompt."""
    if os.environ.get("CLAUDECODE"):
        return False
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


@writes
@trace.command(name="purge", epilog=(
    "\b\nExamples:\n"
    f"  {TRACE} purge --before 90d --dry-run                how many calls are over 90 days old\n"
    f"  {TRACE} purge --before 2026-06-01 --server GitHub   delete one server's older calls"))
@click.option("--before", required=True, type=WHEN,
              help="delete calls older than this: 90d, 30d, or an ISO date, e.g. 2026-06-01")
@click.option("--server", help="only this server's calls, e.g. GitHub")
@click.option("--dry-run", is_flag=True, help="count what would go, delete nothing")
def trace_purge(before, server, dry_run):
    """Delete old tool calls from the index (a person's verb).

    Counts first and asks before anything goes. It runs only in a terminal a person
    types in: from an agent's shell (CLAUDECODE) or without a terminal on stdin it
    refuses. The transcripts themselves are Claude Code's and are never touched.
    """
    if not _at_a_keyboard():
        ui.err("deleting trace history is a person's call — run it in your own terminal, "
               "or use the app's Tools pane")
        raise SystemExit(1)
    flags = ["--before", before, *_flags(server=server)]
    n = engine("purge", *flags, "--dry-run").get("removed", 0)
    what = (f"{n} tool call{'s' if n != 1 else ''} from before {_local(before, '%Y-%m-%d %H:%M')}"
            + (f" on {server}" if server else ""))
    if not n:
        ui.ok(f"nothing to delete: {ui.escape(what)}")
        return
    if dry_run:
        ui.text(f"{ui.escape(what)} would be deleted")
        ui.hint("the same command without --dry-run deletes them")
        return
    if not click.confirm(f"Delete {what}? This cannot be undone.", default=False):
        ui.text("nothing deleted")
        raise SystemExit(1)
    gone = engine("purge", *flags).get("removed", 0)
    ui.ok(f"deleted {gone} tool call{'s' if gone != 1 else ''}")
