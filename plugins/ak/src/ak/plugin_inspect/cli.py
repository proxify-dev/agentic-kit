"""ak plugin — what your plugins put in front of your agent.

    ak plugin ls                every plugin, loudest first, over recent sessions
    ak plugin inspect <name>    one plugin: each thing it is wired to do, and how it behaved
    ak plugin log [<name>]      one session, turn by turn: what each plugin said
    ak plugin rename OLD NEW    a plugin id, everywhere it is written (ak.move; joined in cli.py)

A native group of the `ak` host (cli.py adds it, and `brain_status` to the front door). Output goes
through `ak.ui`, the one door.
"""
from __future__ import annotations

import re
from pathlib import Path

import rich_click as click
from rich.markup import escape

from ak import ui
from ak._brand import cmd
from ak.groups import hidden_alias
from ak.plugin_inspect import heard as heard_mod
from ak.plugin_inspect import installed as inst
from ak.plugin_inspect import report


out = ui


def _door() -> str:
    return cmd("plugin")


def _esc(s: str) -> str:
    """Text from a transcript: its brackets are not markup. The `ak` CLI's ui reads markup in
    every mode (plain strips it), so `[tracer]` would vanish unescaped."""
    return escape(s)


def _arg(*decls, help: str, **kw):
    """click.argument with a help line: rich-click takes `help=`, plain click gets it set after."""
    def deco(f):
        f = click.argument(*decls, **kw)(f)
        f.__click_params__[-1].help = help
        return f
    return deco


def _ms(ms: int) -> str:
    return "—" if not ms else (f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms")


def _tok(n: int) -> str:
    return "—" if not n else (f"{n / 1000:.1f}k" if n >= 1000 else str(n))


def _seen(kinds: dict) -> str:
    """Who read what a hook printed: the model, the person (systemMessage), or both."""
    model = any(k in heard_mod.TO_MODEL for k in kinds)
    you = "user" in kinds
    return "both" if model and you else ("model" if model else ("you" if you else ""))


BORDER = {"context": "cyan", "user": "green", "stderr": "yellow", "deny": "red", "block": "red", "ask": "yellow",
          "error": "red", "silent": "dim"}


def _flat(text: str) -> str:
    """Every word, on one line: the plain grammar is one record a line."""
    return " ".join(text.split())


def _scan(n: int, here: bool):
    """The last n sessions (this project's, with --here), every hook run named by plugin."""
    cwd = Path.cwd()
    files = heard_mod.transcripts(n, cwd if here else None)
    installed = inst.plugins(cwd)
    owners = heard_mod.Owners(installed, inst.history_index())
    rows = []
    for f in files:
        rows += heard_mod.read(f)
    return installed, owners.name_all(rows), len(files)


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]},
             epilog="\b\nExamples:\n"
                    f"  {cmd('plugin'):<34} the ranking\n"
                    f"  {cmd('plugin inspect memory'):<34} one plugin's card\n"
                    f"  {cmd('plugin log'):<34} this session, turn by turn")
@click.pass_context
def cli(ctx):
    """Show what your plugins put in front of your agent.

    What each plugin is wired to do, and what it said in recent sessions. Read from
    Claude Code's own transcripts and each plugin's files; nothing is run.
    Bare [b]ak plugin[/] is [b]ls[/].
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(ls_cmd)


# ── ls ───────────────────────────────────────────────────────────────────────
@cli.command(name="ls")
@click.option("-n", "sessions", default=20, show_default=True, help="how many recent sessions to read")
@click.option("--here", is_flag=True, help="only this project's sessions")
@click.option("--json", "as_json", is_flag=True, help="the same rows as one JSON document")
def ls_cmd(sessions, here, as_json):
    """Rank plugins by what they cost the model a session.

    Loudest first: tokens a session, what each blocked, what failed, and hooks whose
    script is missing (broken). Enabled plugins that said nothing are listed too.
    """
    out.json_mode(as_json)
    installed, rows, n = _scan(sessions, here)
    ranked = report.ranking(installed, rows, n)
    stray = [h for h in inst.settings_hooks(Path.cwd()) if h.missing()]
    if out.is_json():
        out.emit({"sessions": n, "plugins": ranked,
                  "settings_hooks_missing": [{"event": h.event, "command": h.command} for h in stray]})
        return
    if not n:
        out.warn(f"no transcripts under {inst.claude_home() / 'projects'}")
    out.text(f"[b]what plugins put in front of your agent[/] · last {n} sessions{' here' if here else ''}")
    table = []
    for r in ranked:
        state = "on" if r["enabled"] else ("off" if r["installed"] else ("?" if "|" in r["plugin"] else "gone"))
        loud = f"{r['loudest']} ×{r['loudest_fires_per_session']:g}/session" if r["loudest"] else ""
        table.append([r["plugin"], state, _tok(r["tokens_per_session"]),
                      r["blocked"] or "", r["errors"] or "",
                      f"[red]{r['broken']}[/]" if r["broken"] and out.is_rich() else (r["broken"] or ""),
                      _ms(r["slowest_ms"]), loud])
    columns = ["plugin", "", "tok/session", "blocked", "errors", "broken", "slowest", "loudest"]
    if out.is_rich():  # a person: the plugins that spoke, then the quiet ones on one line
        spoke = [row for row, r in zip(table, ranked) if r["sessions_heard"] or r["broken"]]
        quiet = [r["plugin"] for r in ranked if not (r["sessions_heard"] or r["broken"])]
        out.table(columns, spoke, justify=[None, None, "right", "right", "right", "right", "right", None])
        if quiet:
            out.text(f"[dim]said nothing in {n} sessions:[/] {', '.join(quiet)}")
    else:
        out.table(columns, table)
    for h in stray:
        out.warn(f"settings.json {h.event} hook runs a missing file: {h.command}")
    out.hint(f"{_door()} inspect <plugin>   what it is wired to do, and what it said")


hidden_alias(cli, ls_cmd, "list")  # the released spelling


# ── inspect ──────────────────────────────────────────────────────────────────
@cli.command(epilog=f"\b\nExamples:\n  {cmd('plugin inspect memory')}\n  {cmd('plugin inspect observer -n 60')}")
@_arg("name", help="a plugin name, or part of one (e.g. memory, observer)")
@click.option("-n", "sessions", default=20, show_default=True, help="how many recent sessions to read")
@click.option("--here", is_flag=True, help="only this project's sessions")
@click.option("--json", "as_json", is_flag=True, help="the card as one JSON document")
def inspect(name, sessions, here, as_json):
    """Show one plugin's hooks and what each one said.

    Every hook, skill, agent and MCP server it has, each hook next to how often it
    fired, what it cost, and what it says most, whole. Exit 1 when no plugin matches,
    2 when several do.
    """
    out.json_mode(as_json)
    installed, rows, n = _scan(sessions, here)
    names: list[str] = sorted({p.name for p in installed} | {r.plugin for r in rows if "|" not in r.plugin})
    if name not in names:
        close = [x for x in names if name.lower() in x.lower()]
        if len(close) == 1:
            name = close[0]
        elif close:
            out.err(f"{name!r} matches several plugins: {', '.join(close)}")
            raise SystemExit(2)
        else:
            out.err(f"no plugin {name!r}; installed: {', '.join(names)}")
            raise SystemExit(1)
    p = next((x for x in installed if x.name == name), None)
    c = report.card(p, name, rows, n)
    if out.is_json():
        out.emit(c)
        return
    state = "on" if c["enabled"] else ("off" if c["installed"] else "not installed")
    out.text(f"[b]{c['key']}[/] {c['version']} · {state} · ~{_tok(c['tokens_per_session'])} tokens a session"
             f" to the model · last {n} sessions")
    listed = [("skill", s["name"], s["description"]) for s in c["skills"]] + \
             [("agent", a["name"], a["description"]) for a in c["agents"]] + \
             [("command", x, "") for x in c["commands"]] + [("mcp server", x, "") for x in c["mcp"]]
    if out.is_rich():
        _card_rich(c, n, listed)
    else:
        _card_plain(c, n, listed)
    out.hint(f"{_door()} log {name}   where each one landed, turn by turn")


def _fired(h: dict, n: int) -> str:
    fired = f"{h['sessions']}/{n} sessions" if h["fires"] else "no record"
    return fired + (f" · {h['fires']}×" if h["fires"] > h["sessions"] else "")


def _card_plain(c: dict, n: int, listed: list) -> None:
    """For an agent: one TSV row a hook, every word of what it says on that row."""
    if c["hooks"]:
        table = []
        for h in c["hooks"]:
            says = _flat(h["says"]) or ("(nothing to the model)" if h["fires"] else "")
            if h["missing"]:
                says = f"BROKEN: {h['missing'][0]} is missing"
            table.append([h["event"] + (f" {h['matcher']}" if h["matcher"] else ""), h["script"], _fired(h, n),
                          _tok(h["tokens_avg"]), _seen(h["kinds"]), _esc(says)])
        out.table(["when", "runs", "fired", "~tok", "seen by", "what it says"], table, title="hooks")
        if any(not h["fires"] for h in c["hooks"]):
            out.text("no record: it printed nothing, or never fired — Claude Code keeps no record of a silent hook")
    if c["unwired"]:
        out.table(["when", "runs", "fired", "~tok", "what it said"],
                  [[h["event"], h["script"], _fired(h, n), _tok(h["tokens_avg"]), _esc(_flat(h["says"]))]
                   for h in c["unwired"]],
                  title="heard, but not wired in this version (an older version, or settings.json)")
    if listed:
        out.table(["", "name", "what the model is told"], [[k, v, _esc(_flat(d))] for k, v, d in listed],
                  title="listed to the model every session")


def _card_rich(c: dict, n: int, listed: list) -> None:
    """For a person: each hook that spoke as a heading and its whole text in a box under it; the
    silent ones gathered on a few lines, so the ones with something to say stand out."""
    spoke = [h for h in c["hooks"] if h["fires"] or h["missing"]]
    quiet = [h for h in c["hooks"] if not (h["fires"] or h["missing"])]
    if c["hooks"]:
        out.text(f"\n[b]hooks[/] · {len(c['hooks'])} wired · {len(spoke)} on record")
    for h in spoke:
        _hook_rich(h, n)
    if quiet:
        out.text("\n[dim]no record — printed nothing, or never fired (Claude Code keeps no record of a silent hook):[/]")
        for h in quiet:
            out.text(f"  [dim]·[/] {h['event']} [dim]{_esc(h['matcher'])}[/]  {h['script']}")
    if c["unwired"]:
        out.text("\n[b]heard, but not wired in this version[/] [dim](an older version, or settings.json)[/]")
        for h in c["unwired"]:
            _hook_rich(h, n)
    if listed:
        out.text("\n[b]listed to the model every session[/]")
        out.table(["", "name", "what the model is told"], [[k, f"[b]{v}[/]", _esc(d)] for k, v, d in listed],
                  box=None, styles=["dim", None, None])


def _hook_rich(h: dict, n: int) -> None:
    head = f"[b]{h['event']}[/]" + (f" [dim]{_esc(h['matcher'])}[/]" if h["matcher"] else "") + f"  {h['script']}"
    facts = [_fired(h, n)]
    if h["tokens_avg"]:
        facts.append(f"~{_tok(h['tokens_avg'])} tok")
    if _seen(h["kinds"]):
        facts.append(f"seen by {_seen(h['kinds'])}")
    if h["slowest_ms"]:
        facts.append(f"slowest {_ms(h['slowest_ms'])}")
    out.text(f"\n{head}\n  [dim]{' · '.join(facts)}[/]")
    if h["missing"]:
        out.text(f"  [red]BROKEN: {_esc(h['missing'][0])} is missing[/]")
    elif h["says"]:
        top = max(h["kinds"], key=h["kinds"].get) if h["kinds"] else "context"
        out.panel(h["says"].strip(), border=BORDER.get(top, "dim"))
    else:
        out.text("  [dim](nothing to the model)[/]")


# ── log ──────────────────────────────────────────────────────────────────────
@cli.command(epilog=f"\b\nExamples:\n  {cmd('plugin log')}\n  {cmd('plugin log tracer -s 1a2f93b5 --raw')}")
@_arg("name", required=False, help="only this plugin's lines, or part of its name (default: every plugin)")
@click.option("-s", "--session", help="a session id or its first 8 characters (default: this project's newest)")
@click.option("--raw", "--full", "raw", is_flag=True,
              help="each text exactly as printed: the interpreter's path on an error kept")
@click.option("--all", "show_all", is_flag=True, help="also the runs that said nothing")
@click.option("--json", "as_json", is_flag=True, help="the rows as one JSON document")
def log(name, session, raw, show_all, as_json):
    """Show what plugins said in one session, turn by turn.

    Everything a plugin printed, whole, under the prompt it followed, who read it (the
    model, or only you), and what it cost. Exit 1 when the session is not on record.
    """
    out.json_mode(as_json)
    if session:
        path = heard_mod.find(session)
        if path is None:
            out.err(f"no session {session!r} under {inst.claude_home() / 'projects'}")
            raise SystemExit(1)
    else:
        recent = heard_mod.transcripts(1, Path.cwd()) or heard_mod.transcripts(1)
        if not recent:
            out.err(f"no sessions under {inst.claude_home() / 'projects'}")
            raise SystemExit(1)
        path = recent[0]
    installed = inst.plugins(Path.cwd())
    rows = heard_mod.Owners(installed, inst.history_index()).name_all(heard_mod.read(path))
    if name:
        rows = [r for r in rows if name.lower() in r.plugin.lower()]
    shown = [r for r in rows if show_all or r.kind != "silent"]
    if out.is_json():
        out.emit({"session": path.stem, "rows": [{**r.__dict__, "tokens": r.tokens} for r in shown]})
        return
    tok = sum(r.tokens for r in rows)
    out.text(f"[b]session {path.stem[:8]}[/] · {max((r.turn for r in rows), default=0)} prompts · "
             f"{len(rows)} hook runs · ~{_tok(tok)} tokens to the model")
    table, turn = [], None
    for r in shown:
        text = r.text.strip()
        if not raw:  # "/opt/…/Python: can't open file …" — the interpreter's path says nothing
            text = re.sub(r"^/\S+?: ", "", text)
        who = "model" if r.kind in heard_mod.TO_MODEL else ("you" if r.kind == "user" else "—")
        if out.is_rich():  # a person: a heading a turn, each text whole in a box titled by who said it
            if r.turn != turn:
                turn = r.turn
                out.text(f"\n[b]{'session start' if not turn else f'turn {turn}'}[/] [dim]{r.ts[11:19]}[/]")
            facts = " · ".join(x for x in (f"{r.kind} → {who}", f"~{r.tokens} tok" if r.tokens else "", r.ts[11:19]) if x)
            title = f"[b]{_esc(r.plugin)}[/] · {r.hook}"
            if text:
                out.panel(text, title=title, subtitle=f"[dim]{facts}[/]", border=BORDER.get(r.kind, "dim"))
            else:
                out.text(f"  [dim]{title} · {facts} · (said nothing)[/]")
            continue
        table.append([r.turn, r.ts[11:19], r.hook, r.plugin, r.kind, who, r.tokens or "", _esc(_flat(text))])
    if not out.is_rich():
        out.table(["turn", "time", "hook", "plugin", "kind", "seen by", "~tok", "text"], table)
    out.hint(f"{_door()} inspect <plugin>   what else it is wired to do")


def brain_status():
    """the `ak` CLI's front-door row (the `cli:` contract's second half): files only, no transcripts."""
    try:
        ps = [p for p in inst.plugins() if p.enabled]
        hooks = sum(len(p.hooks) for p in ps)
        broken = [p.name for p in ps if any(h.missing() for h in p.hooks)]
        broken += ["settings.json"] if any(h.missing() for h in inst.settings_hooks(Path.cwd())) else []
    except Exception:  # noqa: BLE001 — the front door never goes down with a plugin
        return []
    line = f"{len(ps)} on · {hooks} hooks · {cmd('plugin ls')}"
    if broken:
        line = f"[red]broken hook in {', '.join(broken)}[/] · {cmd('plugin inspect')} {broken[0]} · " + line
    return [("plugins", line)]
