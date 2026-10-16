#!/usr/bin/env python3
"""observer store — the event store's command surface: init · status · sync · backfill.

The engine is hooks/store_common.py (dependency-free, shared with the hooks);
this module is the terminal on top of it. Imported by path from the plugin's
live tree, which is what the editable install guarantees.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import rich_click as click

from observer import ui  # this plugin's copy of ak's output door (plugins/AGENTS.md §6)

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
import store_common as sc  # noqa: E402
from capture_common import is_substantive  # noqa: E402
from observer.groups import writes  # noqa: E402

from ._brand import DISPLAY_NAME, claude_home, env, env_name
from .verbs import command

SUMMARIZER = PLUGIN_ROOT / "scripts" / "summarize-session.py"


def in_plugin_venv() -> None:
    """Re-run this command in the plugin's own venv when the dense channel's packages are
    not importable here — which is the case for `ak observer …`: the `ak` CLI loads
    these verbs by path inside the memory tool's venv, and zvec/fastembed are this
    plugin's dependencies, not the `ak` CLI's. Without the hop `store embed` fails and
    `search` quietly answers as union. Returns when nothing needs to move."""
    import importlib.util
    import shutil
    if env("OBS_IN_PLUGIN_VENV") or all(
            importlib.util.find_spec(m) for m in ("zvec", "fastembed")):
        return
    uv = shutil.which("uv")
    if not uv:
        return
    argv = sys.argv[1:]
    if "observer" in argv:  # `ak observer <verb> …` → `observer <verb> …`
        argv = argv[argv.index("observer") + 1:]
    child_env = {**os.environ, env_name("OBS_IN_PLUGIN_VENV"): "1"}
    r = subprocess.run([uv, "run", "--quiet", "--frozen", "--project", str(PLUGIN_ROOT),
                        "observer", *argv], env=child_env)
    raise SystemExit(r.returncode)


def _human(size: int) -> str:
    n = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@click.group(name="store")
def store():
    """The event store, <kit>/db/brain.db: sessions, prompts, observations, summaries.

    What the machine remembers, as rows; the vault is what you decided to keep.
    Schema is claude-mem's 1:1 (plus verdict · anchors · supersedes), so [b]init[/]
    is a copy, [b]sync[/] pulls what claude-mem wrote since, and [b]backfill[/]
    summarises any transcript no hook saw.
    """


@writes
@store.command("init")
@click.option("--force", is_flag=True, help="throw the existing store away first")
@click.option("--empty", is_flag=True, help="start empty even if claude-mem's db is present")
def store_init(force, empty):
    """Create the store: a copy of claude-mem's db when there is one, else an empty schema."""
    r = sc.init(force=force, from_claude_mem=not empty)
    if r["created"]:
        ui.ok(f"store created {r['path']}")
        if r["forked_from"]:
            ui.text(f"  forked from {r['forked_from']}")
            ui.table(["table", "claude-mem max id", "ours start at"],
                     [[t, f["fork_max"], f["floor"]] for t, f in (r.get("floors") or {}).items()],
                     justify=[None, "right", "right"], box=None)
    else:
        ui.ok(f"store present {r['path']} (schema settled; --force to recreate)")
    if r["added"]:
        ui.text("  columns added: " + ", ".join(r["added"]))


@store.command("status")
@ui.json_option
def store_status(as_json):
    """Where the store is, what it holds, how far claude-mem has run ahead of it."""
    ui.json_mode(as_json)
    s = sc.status()
    if not s["exists"]:
        if ui.is_json():
            ui.emit({"exists": False, "path": str(s["path"])})
        else:
            ui.err(f"no store at {s['path']}")
            ui.hint(command("store init"))
        raise SystemExit(1)
    if ui.is_json():
        ui.emit(s)
        return
    rows = [("path", f"{s['path']}  ({_human(s['size'])})")]
    for name, n in s["counts"].items():
        behind = s["claude_mem"]["behind"].get(name)
        tail = f"  [dim]claude-mem +{behind} since fork[/]" if behind else ""
        rows.append((name, f"{n if n is not None else '?'}{tail}"))
    rows.append(("ours", f"{s['ours']}  [dim]observations the {DISPLAY_NAME} observer wrote[/]"))
    rows.append(("last obs", s["last_observation"] or "—"))
    m = s["meta"]
    if m.get("forked_from"):
        rows.append(("forked", f"{m['forked_at']}  from {m['forked_from']}"))
    if m.get("synced_at"):
        rows.append(("synced", m["synced_at"]))
    ui.kv(rows, title="store")
    if any(s["claude_mem"]["behind"].values()):
        ui.hint(f"claude-mem has rows the store does not — {command('store sync')} pulls them in")


@writes
@store.command("sync")
@click.option("--dry-run", is_flag=True, help="count what would be pulled, write nothing")
def store_sync(dry_run):
    """Pull the rows claude-mem wrote since the fork into the store, with their own ids."""
    r = sc.sync(dry_run=dry_run)
    if r.get("error"):
        ui.err(r["error"])
        raise SystemExit(1)
    verb = "would pull" if dry_run else "pulled"
    ui.table(["table", verb], [[t, n] for t, n in r["pulled"].items()], justify=[None, "right"], box=None)


def _transcripts(days: int) -> list:
    """Top-level session transcripts, newest first. Agent sub-transcripts and
    claude-mem's own observer sessions are not sessions of ours."""
    root = claude_home() / "projects"
    cutoff = time.time() - days * 86400
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir() or "claude-mem-observer-sessions" in d.name:
            continue
        for p in d.glob("*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            out.append((st.st_mtime, p))
    out.sort(reverse=True)
    return [p for _, p in out]


@writes
@store.command("backfill")
@click.option("--days", default=7, show_default=True, help="transcripts modified within this many days")
@click.option("--limit", default=20, show_default=True, help="at most this many sessions")
@click.option("--dry-run", is_flag=True, help="show which sessions would be summarised")
@click.option("--model", default=None, help="override the observer model for this run")
def store_backfill(days, limit, dry_run, model):
    """Summarise transcripts the store has no summary for — sessions that crashed,
    ran with capture off, or predate the store. Sequential; one LLM call each."""
    if not sc.store_db().exists():
        ui.err("no store")
        ui.hint(command("store init"))
        raise SystemExit(1)
    conn = sc.connect(readonly=True)
    done = {r[0] for r in conn.execute(
        "SELECT DISTINCT s.content_session_id FROM sdk_sessions s "
        "JOIN session_summaries ss ON ss.memory_session_id = s.memory_session_id")}
    conn.close()
    todo = []
    for p in _transcripts(days):
        uuid = p.stem
        if uuid in done:
            continue
        if not is_substantive(str(p)):
            continue
        todo.append(p)
        if len(todo) >= limit:
            break
    if not todo:
        ui.ok("nothing to backfill — every substantive transcript in range has a summary")
        return
    rows = []
    for p in todo:
        proj = p.parent.name.rsplit("-", 1)[-1] or p.parent.name
        size = _human(p.stat().st_size)
        if dry_run:
            rows.append([p.stem[:8], proj, size, "[dim]would summarise[/]"])
            continue
        argv = [sys.executable, str(SUMMARIZER), "--session", p.stem, "--transcript", str(p), "--sink", "store"]
        if model:
            argv += ["--model", model]
        child_env = {**os.environ, env_name("CAPTURE_DISABLE"): "1"}
        try:
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=400, env=child_env)
            line = (r.stdout.strip().splitlines() or [r.stderr.strip()[-120:] or "no output"])[-1]
            rows.append([p.stem[:8], proj, size, ui.escape(line.replace("summarize: ", ""))])
        except subprocess.TimeoutExpired:
            rows.append([p.stem[:8], proj, size, "[red]timeout[/]"])
    ui.table(["session", "project", "size", "result"], rows, justify=[None, None, "right", None], box=None)


@writes
@store.command("embed")
@click.option("--rebuild", is_flag=True, help="throw the collection away and embed every row again")
@click.option("--batch", type=int, default=512, show_default=True, help="rows per embedding batch")
@click.option("--wait", is_flag=True, help="queue behind a running embed instead of leaving it to it")
def store_embed(rebuild, batch, wait):
    """Build the dense channel: one vector per observation, in a zvec collection beside brain.db.

    What [b]hybrid[/] (the default search mode) and [b]vector[/] read; without it those
    modes answer as [b]union[/]. Incremental — a second run embeds only the rows written
    since; SessionEnd runs it (with [b]--wait[/]) after every summary. One build at a time:
    a second run while one is going exits 0 and says so, or with [b]--wait[/] queues.
    """
    from observer.obs_index import SqliteFtsBackend
    in_plugin_venv()

    def tick(c):
        if c["embedded"] % (batch * 10) == 0:
            ui.text(f"  [dim]{c['embedded']}/{c['total'] - c['skipped']} · "
                    f"{c['embedded'] / max(c['seconds'], 1e-6):.0f} rows/s[/]")

    try:
        r = SqliteFtsBackend().build_vectors(rebuild=rebuild, batch=batch, progress=tick, wait=wait)
    except RuntimeError as e:
        ui.err(str(e))
        raise SystemExit(1)
    if r["busy"]:
        ui.ok("another embed is running — rows written after it started need a run after it (--wait)")
        return
    ui.ok(f"dense channel {r['path']}")
    ui.kv([["rows", f"{r['total']:,}"], ["embedded", f"{r['embedded']:,}"],
           ["already there", f"{r['skipped']:,}"], ["failed", f"{r['failed']:,}"],
           ["seconds", r["seconds"]]])
    if r["embedded"]:
        ui.hint(f"{command('search')} '<query>'")
