#!/usr/bin/env python3
"""observer — the observer lobe as one verb.

This module holds the agent half: show · try. The writer's dial (mute|unmute, status), the
type table (types), the store and search are mounted under the same group by ak.cli —
one word for the writer, its rows and its readers. The observer itself is a standard Claude
Code agent, agents/observer.md (hooks/observer_agent.py reads it).
"""
import os
import subprocess
import sys
from pathlib import Path

import rich_click as click

from observer import ui  # this plugin's copy of ak's output door (plugins/AGENTS.md §6)

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
import observer_agent  # noqa: E402
import store_common as sc  # noqa: E402
from capture_common import capture_enabled, is_substantive  # noqa: E402
from observer.store import _human, _transcripts  # noqa: E402

from ._brand import claude_home, env_name
from .verbs import command

SUMMARIZER = PLUGIN_ROOT / "scripts" / "summarize-session.py"


@click.group(name="observer", invoke_without_command=True)
@click.pass_context
def observer(ctx):
    """The observer: what past sessions left behind, and the agent that writes it.

    One lobe, one verb. [b]mute|unmute[/] silences the writer, [b]--here[/] for one
    project. [b]show[/] the agent that reads each session (agents/observer.md), [b]try[/]
    it on a past session without writing. [b]types[/] are the kinds of row it may write;
    [b]store[/] is the db those rows land in; [b]search[/] reads them back; [b]fold[/] and
    [b]forget[/] tidy them, never deleting one.
    Bare [b]observer[/] is [b]status[/].
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(ctx.command.commands["status"])


# --- show: what it obeys right now ----------------------------------------------------
def _store_written() -> tuple:
    """(sessions this observer wrote, last observation), or (None, the error)."""
    try:
        conn = sc.connect(readonly=True)
        n = conn.execute("SELECT COUNT(*) FROM sdk_sessions WHERE platform_source='brain'").fetchone()[0]  # brand: historical (what stored sessions carry)
        last = conn.execute("SELECT MAX(created_at) FROM observations WHERE generated_by_model LIKE 'brain/%'").fetchone()[0]  # brand: historical
        conn.close()
        return n, last
    except Exception as exc:  # the store is someone else's concern here
        return None, str(exc)


@observer.command("show")
@click.option("--body", is_flag=True, help="print the agent's prompt too")
@ui.json_option
def observer_show(body, as_json):
    """Show the agent that reads each session, and what it wrote."""
    ui.json_mode(as_json)
    agent = observer_agent.load()
    armed = capture_enabled()
    store_n, store_last = _store_written() if sc.store_db().exists() else (None, None)
    if ui.is_json():
        ui.emit({"agent": agent["name"], "path": agent["path"], "model": agent.get("model", ""),
                 "capture": armed, "store_sessions": store_n, "store_last_observation": store_last,
                 **({"body": agent["prompt"]} if body else {})})
        return
    rows = [("agent", f"{agent['name']}  [dim]{agent['path']}[/]"), ("model", agent.get("model", ""))]
    rows.append(("writer", "[green]on[/] — runs at every session end" if armed
                 else f"[red]off[/] — nothing runs; {command('status')} says why"))
    if store_n is not None:
        rows.append(("store", f"{store_n} session(s) written by this observer"
                     + (f" · last observation {store_last}" if store_last else "")))
    elif store_last:
        rows.append(("store", f"[dim]unreadable ({store_last})[/]"))
    ui.kv(rows, title="observer")
    if body:
        sys.stdout.write("\n" + agent["prompt"] + "\n")  # prose as written, not re-wrapped


# --- try: a dry run on a past session ---------------------------------------------------
def _find_transcript(ref: str) -> Path:
    root = claude_home() / "projects"
    if ref == "latest":
        for p in _transcripts(30):
            if is_substantive(str(p)):
                return p
        raise click.ClickException("no substantive transcript in the last 30 days")
    hits = [p for p in root.glob(f"*/{ref}*.jsonl") if "claude-mem-observer-sessions" not in p.parent.name]
    exact = [p for p in hits if p.stem == ref]
    if exact:
        return exact[0]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise click.ClickException(f"no transcript matching {ref} under {root}")
    raise click.ClickException("ambiguous — " + ", ".join(p.stem[:12] for p in hits[:8]))


@observer.command("try")
@click.argument("session", help="a session uuid, a unique prefix of one, or `latest`")
@click.option("--model", default="", help="override the model for this run")
@click.option("--prompt", is_flag=True, help="print the assembled prompt instead of running the model")
def observer_try(session, model, prompt):
    """Dry-run the observer on a past SESSION (uuid, prefix, or `latest`).

    Shows what it WOULD write beside what was written for that session before —
    the way to judge an edit to agents/observer.md before it ships.
    """
    p = _find_transcript(session)
    argv = [sys.executable, str(SUMMARIZER), "--session", p.stem, "--transcript", str(p), "--sink", "store",
            "--print-prompt" if prompt else "--dry-run"]
    if model:
        argv += ["--model", model]
    child_env = {**os.environ, env_name("CAPTURE_DISABLE"): "1"}
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=400, env=child_env)
    except subprocess.TimeoutExpired:
        raise click.ClickException("the model did not answer within 400 s")
    if prompt:
        sys.stdout.write(r.stdout)
        return
    proj = p.parent.name.rsplit("-", 1)[-1] or p.parent.name
    ui.text(f"[b]session[/] {p.stem[:8]} · {proj} · {_human(p.stat().st_size)}")
    out = r.stdout.strip() or r.stderr.strip()[-600:] or "nothing produced"
    ui.panel(out, title="would write")
    if sc.store_db().exists():
        try:
            conn = sc.connect(readonly=True)
            rows = conn.execute("SELECT type, verdict, title FROM observations WHERE memory_session_id=? ORDER BY id",
                                (p.stem,)).fetchall()
            conn.close()
        except Exception:
            rows = []
        if rows:
            ui.table(["type", "verdict", "title"], [[t, v or "-", ui.escape(title)] for t, v, title in rows],
                     title=f"written before for this session ({len(rows)})", box=None)
        else:
            ui.hint("nothing in the store for this session yet")
    ui.hint("dry run — nothing was written")
