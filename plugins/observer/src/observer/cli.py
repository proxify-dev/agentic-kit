"""observer — the shell door to the observer: its writer, its agent, its store.

One group, `observer`, reachable two ways:

    observer <verb>      this plugin's own command: bin/observer, on the Bash tool's PATH while
                         the plugin is enabled (it runs this console script in the plugin's uv env)
    ak observer <verb>   the same group, mounted into the `ak` CLI by plugins/observer/scripts/cli.py
                         (the `cli:` block in plugin.json) when ak is installed

The group itself, with show/try, lives in observer.py; status and mute/unmute (the writer's
dial, `<kit>/db/capture.json`) and search (the store from the shell) are added here, and
store (store.py) / types (memory.py) are mounted under it. Output goes through this plugin's
copies of ak's `ui`, `groups` and `help` (scripts/copies.py), mounted or not: nothing is
imported across plugins at runtime.
"""
from __future__ import annotations

import os
from pathlib import Path

import rich_click as click
from rich.markup import escape

# this plugin's copy of ak's output door, mounted or not (scripts/copies.py; plugins/AGENTS.md §6)
from observer import ui

from observer import capture_flag, obs_index
from observer.groups import writes
from observer import memory as memory_cli
from observer import observer as observer_cli
from observer import shadow as shadow_cli
from observer import store as store_cli

from ._brand import REPO_DIR, cmd, env_name, utf8_stdio
from .verbs import command

observer = observer_cli.observer


def _examples(rows, gap):
    """The `Examples:` epilog: each (verb, note) is a `<cli> observer <verb>` line, the notes
    lined up `gap` columns past the longest command — so the block stays aligned whatever the
    command is called. The leading \b keeps click from re-wrapping it."""
    cmds = [cmd(f"observer {verb}") for verb, _ in rows]
    width = max(len(c) for c in cmds) + gap
    return "\b\nExamples:\n" + "\n".join(
        f"  {c.ljust(width)}{note}" if note else f"  {c}" for c, (_, note) in zip(cmds, rows))


def _fmt(p):
    return escape(str(p))


def _switch_dir() -> Path:
    """The dial's directory, once the global vault exists (a kit `ak init` has made)."""
    vault = capture_flag.global_vault()
    if not vault.is_dir():
        ui.err(f"vault not found: {vault} (set {env_name('PATH')})")
        raise SystemExit(64)
    return capture_flag.switch_dir()


def _on(flag: bool, on: str = "on", off: str = "off") -> str:
    if not ui.is_rich():
        return on if flag else off
    return f"[green]●[/] {on}" if flag else f"[red]○[/] {off}"


def _ago(iso: str | None) -> str:
    """'2m ago' from an ISO timestamp; '—' when there is none."""
    if not iso:
        return "—"
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        s = int((datetime.now(timezone.utc) - t).total_seconds())
    except ValueError:
        return iso
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit} ago"
    return f"{s}s ago"


# ── the writer's dial (<kit>/db/capture.json) ─────────────────────────────────
def _capture_facts(root, cwd):
    """(enabled, reason, config path, global flag, per-project denies, broken?)."""
    enabled, reason = capture_flag.state(cwd, root)
    try:
        cfg, broken = capture_flag.load(root), False
    except Exception:
        cfg, broken = {}, True
    denied = [p for p in (cfg.get("disabled_paths") or []) if isinstance(p, str)]
    return (enabled, reason, capture_flag.config_path(root),
            "off" if cfg.get("enabled") is False else "on", denied, broken)


def _observer_render(root, cwd, title):
    """One board for the lobe: writer dial · agent · store. Returns writer on?"""
    w_on, why, cfg_path, glob, denied, broken = _capture_facts(root, cwd)
    p_on, p_why, p_setting = capture_flag.prompts(cwd, root)
    if ui.is_json():
        ui.emit({"writer": {"enabled": w_on, "reason": why, "global": glob == "on",
                            "disabled_paths": denied, "config": str(cfg_path), "config_broken": broken},
                 "prompts": {"stored": p_on, "reason": p_why, "setting": p_setting}})
        return w_on
    rows = [("writer", ("[green]on[/]" if w_on else "[red]off[/]") + f" [dim]({_fmt(why)})[/]"
             + (f"  [dim]({command('mute|unmute --here')})[/]" if ui.is_rich() else "")),
            ("prompts", ("copied" if p_on else "not copied") + f" [dim]({escape(p_why)})[/]"),
            ("here", _fmt(cwd))]
    if denied:
        rows.append(("muted in", "\n".join(_fmt(p) for p in denied)))
    try:
        a = observer_cli.observer_agent.load()
        rows.append(("agent", f"{a['name']} · {a.get('model', '?')}  [dim]{_fmt(a['path'])}[/]"))
    except Exception as e:  # noqa: BLE001
        rows.append(("agent", f"[red]unavailable[/] [dim]({e})[/]"))
    try:
        sst = store_cli.sc.status()
        n = sst["counts"].get("observations") if sst["exists"] else None
        rows.append(("store", f"{n:,} observations · {sst['ours']:,} ours · last {_ago(sst['last_observation'])}"
                     if n is not None else f"[yellow]no store[/]  [dim]({command('store init')})[/]"))
    except Exception as e:  # noqa: BLE001
        rows.append(("store", f"[red]unavailable[/] [dim]({e})[/]"))
    if broken:
        rows.append(("[red]note[/]", "[red]capture.json unparseable — writer treated as off until fixed or deleted[/]"))
    ui.kv(rows, title=title, border="green" if w_on else "yellow")
    return w_on


@observer.command("status")
@ui.json_option
def observer_status(as_json):
    """Is the writer recording here, which agent, how big the store (exit 0 on · 1 off)."""
    ui.json_mode(as_json)
    raise SystemExit(0 if _observer_render(_switch_dir(), os.getcwd(), "observer · status") else 1)


@writes
@observer.command("mute")
@click.option("--here", is_flag=True, help="only this directory and everything under it")
@click.option("--path", "path", default=None, help="only PATH and everything under it")
def observer_mute(here, path):
    """Silence the writer only (vault-wide; --here scopes it to one project).

    Recall, the SessionStart digest and rows already written are untouched. Reach
    for it when a second memory system is recording the same sessions.
    """
    root = _switch_dir()
    target = path or (os.getcwd() if here else None)
    if target:
        _, changed = capture_flag.set_path(target, enabled=False, root=root)
        if not changed:
            ui.hint("already muted for that path (or a parent of it)")
    else:
        capture_flag.set_global(False, root=root)
    _observer_render(root, os.getcwd(), "observer · mute")


@writes
@observer.command("unmute")
@click.option("--here", is_flag=True, help="only this directory and everything under it")
@click.option("--path", "path", default=None, help="only PATH and everything under it")
def observer_unmute(here, path):
    """Resume the writer (--here clears just this project's rule)."""
    root = _switch_dir()
    target = path or (os.getcwd() if here else None)
    if target:
        _, changed = capture_flag.set_path(target, enabled=True, root=root)
        if not changed:
            ui.hint("no per-project rule covered that path")
    else:
        capture_flag.set_global(True, root=root)
    _observer_render(root, os.getcwd(), "observer · unmute")


@writes
@observer.command("prompts", epilog=_examples([
    ("prompts auto", "copied only while the tracer is off (the default)"),
    ("prompts on", "always copy them into the store"),
    ("prompts off", "never copy them"),
], 4))
@click.argument("setting", type=click.Choice(capture_flag.PROMPTS_SETTINGS),
                help="on · off · auto, e.g. auto")
def observer_prompts(setting):
    """Copy session prompts into the store: on, off or auto.

    The store's user_prompts table is a copy of what Claude Code's transcripts already hold.
    The observer never reads it back; the brain app and memory's E4 alias rarity do. Auto
    skips the copy while the tracer plugin is on, which reads the prompts from the
    transcripts (`tracer sessions search --role user`). Rows already stored stay.
    """
    root = _switch_dir()
    capture_flag.set_prompts(setting, root=root)
    _observer_render(root, os.getcwd(), "observer · prompts")


@observer.command("search", aliases=["s"], epilog=_examples([
    ('search "band flapping"', "rows about it, best first"),
    ("search zvec --type decision --limit 5", "only decisions"),
    ("search lock --since 2026-09-01 --json", "as JSON, for a tool"),
    (f"search --files ~/{REPO_DIR}/RELEASING.md", "the rows that name that file"),
    ("search", "the newest rows"),
], gap=4))
@click.argument("query", required=False, default="",
                help="words to look for — quote a phrase; omit it for the newest rows")
@click.option("--project", help="scope to one project label")
@click.option("--type", "obs_type", help="filter by obs_type (e.g. bugfix, decision)")
@click.option("--concepts", help="comma-separated concepts, all required")
@click.option("--files", help="comma-separated, all required: an absolute or ~/ path names that file "
                                "(its worktree and release-clone copies count), anything else is a path "
                                "substring (e.g. curate.py)")
@click.option("--since", "date_start", help="date >= YYYY-MM-DD")
@click.option("--until", "date_end", help="date <= YYYY-MM-DD")
@click.option("--limit", type=int, default=20, help="max results")
@click.option("--offset", type=int, default=0, help="pagination offset")
@click.option("--mode", type=click.Choice(obs_index.SqliteFtsBackend.MODES), default=None,
              show_default="hybrid",
              help="how rows are matched:\n\n"
                   "\b\n"
                   "hybrid  union, plus meaning matches if <5 have all words\n"
                   "union   all-word rows first, any-word rows if <5\n"
                   "and     all-word rows only\n"
                   "vector  meaning matches only\n\n"
                   f"Meaning matches need `{command('store embed')}`; without it hybrid and vector "
                   f"answer as union. ${env_name('OBS_SEARCH_MODE')} sets another default.")
@click.option("--by-session", is_flag=True,
              help="one row per session the topic came up in (the top 200 rows grouped); "
                   "--limit/--offset then count sessions")
@ui.json_option
def search(query, project, obs_type, concepts, files, date_start, date_end, limit, offset, mode, by_session, as_json):
    """Search what the observer wrote — the rows observer serves, from the shell.

    Exit 1 when nothing matched; --json always exits 0 with a list.
    """
    store_cli.in_plugin_venv()
    ui.json_mode(as_json)
    kw: dict = dict(query=query, project=project, obs_type=obs_type,
              concepts=[c.strip() for c in concepts.split(",")] if concepts else None,
              files=[f.strip() for f in files.split(",")] if files else None,
              date_start=date_start, date_end=date_end, limit=limit, offset=offset, mode=mode)
    if by_session:
        got = obs_index.BACKEND.sessions(**kw)
        if ui.is_json():
            ui.emit(got["sessions"])
            raise SystemExit(0)
        if not got["sessions"]:
            ui.warn(f"no session on record for '{query or '(recent)'}'")
            raise SystemExit(1)
        ui.table(["session", "started", "project", "hits", "asked"],
                 [[g["session"], g["started"], g["project"], str(g["hits"]), _fmt(g["asked"])] for g in got["sessions"]],
                 title=f"sessions · {query or 'recent'}  ({len(got['sessions'])} · {got['scanned']} rows, "
                       f"{got['unlinked']} with no session)", styles=["cyan", "dim", "magenta", None, None])
        raise SystemExit(0)
    rows = obs_index.BACKEND.search(**kw)

    if ui.is_json():  # machine surface: always exit 0, empty is [] not an error
        ui.emit(rows)
        raise SystemExit(0)
    if not rows:
        ui.warn(f"no matches for '{query or '(recent)'}'")
        raise SystemExit(1)
    ui.table(["id", "date", "type", "title", "session"],
             [[_fmt(r["id"]), r["date"], r["obs_type"], _fmt(r["title"]), (r.get("session") or "")[:8]] for r in rows],
             title=f"search · {query or 'recent'}  ({len(rows)})", styles=["cyan", "dim", "magenta", None, "dim"])
    ui.hint(f"{command('timeline')} {rows[0]['id']} (what happened around it) · "
            f"{command('get')} <id> [<id>…] (full rows, only the ones you keep)")
    raise SystemExit(0)


@observer.command("timeline", epilog=_examples([
    ("timeline 37078", "three rows either side of it"),
    ('timeline --query "zvec"', "around the best hit for a query"),
], gap=5))
@click.argument("anchor", required=False, default=None,
                help="an observation id (37078 or memdb:37078); omit it with --query")
@click.option("--query", help="anchor on the best search hit for these words instead")
@click.option("--before", type=int, default=3, help="rows before the anchor")
@click.option("--after", type=int, default=3, help="rows after the anchor")
@click.option("--project", help="scope to one project label (default: the anchor's)")
@ui.json_option
def timeline(anchor, query, before, after, project, as_json):
    """Layer 2: the rows written just before and after one observation, oldest first.

    Exit 1 when the anchor is not on record.
    """
    ui.json_mode(as_json)
    got = obs_index.BACKEND.timeline(anchor=anchor, query=query, before=before, after=after, project=project)
    if ui.is_json():
        ui.emit(got)
        raise SystemExit(0)
    if not got["rows"]:
        ui.warn(f"no observation {anchor or query!r} on record")
        raise SystemExit(1)
    ui.table(["anchor", "id", "date", "type", "title", "session"],
             [["▶" if r.get("is_anchor") else "", _fmt(r["id"]), r["date"], r["obs_type"], _fmt(r["title"]),
               (r.get("session") or "")[:8]] for r in got["rows"]],
             title=f"timeline · around {got['anchor']}", styles=["bold", "cyan", "dim", "magenta", None, "dim"])
    ui.hint(f"{command('get')} <id> [<id>…] (full rows)")
    raise SystemExit(0)


@observer.command("get", epilog=_examples([
    ("get 37078", "one row, whole"),
    ("get 37078 37080 memdb:37091", ""),
], gap=1))
@click.argument("ids", nargs=-1, required=True,
                help="observation ids — 37078, memdb:37078, or a comma list")
@ui.json_option
def get(ids, as_json):
    """Layer 3: observations whole — facts, narrative, session. Only for ids a search kept.

    Exit 1 when none of them is on record.
    """
    ui.json_mode(as_json)
    wanted = [i.strip() for arg in ids for i in arg.split(",") if i.strip()]
    got = obs_index.BACKEND.get(wanted)
    if ui.is_json():
        ui.emit(got)
        raise SystemExit(0)
    for o in got:
        if o.get("content") is None:
            ui.warn(f"memdb:{o['id']} — {o.get('error', 'not found')}")
            continue
        ui.panel(o["content"].rstrip(), title=f"memdb:{o['id']}",
                 subtitle=f"session {o['session']}" if o.get("session") else None)
    raise SystemExit(0 if any(o.get("content") is not None for o in got) else 1)


def _patterns(project, min_rows, min_sessions, since_days) -> list:
    """curate.patterns for a project and its aliases, as one list. curate filters one label
    at a time, so each label's clusters (≥2 sessions there) are merged by anchor and the
    thresholds applied to the union — a cluster with one session under each alias is missed
    until curate.patterns takes a label list."""
    from observer import curate
    if not store_cli.sc.store_db().exists():
        return []
    merged: dict = {}
    for label in (obs_index.labels_with_aliases([project]) if project else [None]):
        for c in curate.patterns(project=label, min=2, since_days=since_days)["clusters"]:
            m = merged.setdefault(c["anchor"], {"anchor": c["anchor"], "ids": [], "session_ids": [],
                                                "titles": [], "unlinked": 0})
            m["ids"] += c["ids"]
            m["titles"] += c["titles"]
            m["unlinked"] += c.get("unlinked", 0)
            m["session_ids"] += [s for s in c["sessions"] if s not in m["session_ids"]]
    out = []
    for m in merged.values():
        sessions = len(m["session_ids"]) + m["unlinked"]
        if len(m["ids"]) >= min_rows and sessions >= min_sessions:
            out.append({"anchor": m["anchor"], "rows": len(m["ids"]), "sessions": sessions, "ids": m["ids"],
                        "title": next((t for t in m["titles"] if t), ""), "session_ids": m["session_ids"],
                        "unlinked": m["unlinked"]})
    out.sort(key=lambda c: (-c["rows"], c["anchor"]))
    return out


@observer.command("patterns", epilog=_examples([
    ("patterns --project brain", "what keeps coming up in this repo"),  # brand: historical — a stored project label
    ("patterns --since-days 14 --min-rows 4", ""),
    ("patterns --json", "every project, as JSON"),
], gap=0))
@click.option("--project", help="scope to one project label, its aliases included (e.g. agentic-kit)")
@click.option("--min-rows", type=click.IntRange(min=2), default=3, help="rows a cluster needs")
@click.option("--min-sessions", type=click.IntRange(min=2), default=2, help="distinct sessions a cluster needs")
@click.option("--since-days", type=click.IntRange(min=1), default=None,
              help="only rows from the last N days (default: the newest 5000 per label)")
@ui.json_option
def patterns(project, min_rows, min_sessions, since_days, as_json):
    """List anchors that keep coming up across sessions.

    A cluster is live rows sharing an anchor (their `anchors`, else their `concepts`) —
    raw material for one note instead of N rows. `ak memory harvest` turns a cluster no
    note stands for into a trial rule. Exit 1 when there is none; --json always exits 0.
    """
    ui.json_mode(as_json)
    got = _patterns(project, min_rows, min_sessions, since_days)
    if ui.is_json():
        ui.emit(got)
        raise SystemExit(0)
    if not got:
        ui.warn(f"no anchor in {min_rows}+ rows across {min_sessions}+ sessions"
                + (f" for {project}" if project else ""))
        ui.hint(f"{command('patterns')} --min-rows 2{f' --project {project}' if project else ''}")
        raise SystemExit(1)

    def ids(c):
        shown = ",".join(map(str, c["ids"][:6]))
        return shown + (f" +{len(c['ids']) - 6}" if len(c["ids"]) > 6 else "")

    ui.table(["anchor", "rows", "sessions", "ids", "title"],
             [[_fmt(c["anchor"]), str(c["rows"]), str(c["sessions"]), ids(c), _fmt(c["title"])] for c in got],
             title=f"patterns · {project or 'every project'}  ({len(got)})",
             styles=["cyan", None, None, "dim", None], justify=[None, "right", "right", None, None])
    ui.hint(f"{cmd('memory harvest')} (a cluster no note stands for becomes a trial rule) · "
            f"{command('get')} {','.join(map(str, got[0]['ids'][:3]))} (the rows behind one)")
    raise SystemExit(0)


# ── the curator's writes (curate.py) ──
def _obs_ids(args) -> list:
    """Observation ids as ints, in any spelling `get` takes: 37078, memdb:37078, a comma list."""
    out = []
    for i in (i.strip() for arg in args for i in arg.split(",")):
        if not i:
            continue
        n = i.removeprefix("memdb:")
        if not n.isdigit():
            ui.err(f"'{i}' is not an observation id (e.g. 37078 or memdb:37078)")
            raise SystemExit(64)
        out.append(int(n))
    return out


def _need_store() -> None:
    """A fold, or a forget by id, opens brain.db itself — and a write would create an empty one."""
    db = store_cli.sc.store_db()
    if not db.exists():
        ui.err(f"no store at {db}")
        ui.hint(command("store init"))
        raise SystemExit(66)


def _curate(fn, **kw) -> dict:
    """One curate call; a refusal is the store saying no to what was passed, with the reason."""
    from observer import curate
    try:
        return fn(**kw, actor=curate.actor_name())
    except curate.Refused as e:
        ui.err(str(e))
        raise SystemExit(64)


def _memdb(ids) -> str:
    return ", ".join(f"memdb:{i}" for i in ids)


@writes
@observer.command("fold", epilog=_examples([
    ('fold 37078 37080 --title "zvec LOCK" --fact "readers hold it"', "one row for two"),
    ("fold --undo 41000", "the two rows back, the summary gone"),
], gap=2))
@click.argument("ids", nargs=-1, help="two or more observation ids — 37078, memdb:37078, or a comma list")
@click.option("--title", default=None, help='the one line the summary shows in their place (e.g. "zvec LOCK blocks the writer")')
@click.option("--fact", "facts", multiple=True,
              help="one thing the rows said, so nothing they knew is lost; repeat it, one per fact (at least one)")
@click.option("--narrative", default=None, help="a paragraph of context under the facts")
@click.option("--undo", default=None, metavar="ID", help="a summary's id (e.g. 41000): its rows come back, it is deleted")
@ui.json_option
def fold(ids, title, facts, narrative, undo, as_json):
    """Fold rows into one summary row; --undo restores them.

    The summary stands for the rows in search and timeline; they stay on record, hidden,
    and `get` still reads them by id. Each fold and undo is one line in the ledger
    (db/objects-ledger.jsonl), which a ledger view can show. Exit 64 when the store
    refuses (fewer than two ids, no title or fact, a row already folded, rows of two projects);
    66 when there is no store.
    """
    from observer import curate
    ui.json_mode(as_json)
    if undo is not None and ids:
        ui.err("--undo takes the summary's id alone, not rows to fold")
        raise SystemExit(64)
    summary = _obs_ids([undo]) if undo is not None else None
    if summary is not None and len(summary) != 1:
        ui.err("--undo takes one summary id")
        raise SystemExit(64)
    _need_store()
    got = _curate(curate.fold, ids=_obs_ids(ids), title=title or "", facts=list(facts), narrative=narrative,
                  undo=summary[0] if summary else None)
    if ui.is_json():
        ui.emit(got)
        raise SystemExit(0)
    sid = got["summaryId"]
    if got["kind"] == "unfolded":
        ui.ok(f"unfolded memdb:{sid}: {_memdb(got['restored'])} back in recall")
        ui.hint(f"{command('get')} {' '.join(map(str, got['restored']))}")
    else:
        ui.ok(f"folded {_memdb(got['folded'])} into memdb:{sid} — {_fmt(got['title'])}")
        ui.hint(f"{command('get')} {sid} (the summary) · {command('fold')} --undo {sid} (the rows back)")
    raise SystemExit(0)


@writes
@observer.command("forget", epilog=_examples([
    ('forget 37078 --reason "a probe, not a finding"', "preview the row"),
    ('forget 37078 --reason "a probe, not a finding" --confirm', "hide it"),
    ('forget --query "smoke test" --reason noise', "preview what a search finds"),
    ("forget 37078 --undo --confirm", "back in recall"),
], gap=2))
@click.argument("ids", nargs=-1, help="observation ids — 37078, memdb:37078, or a comma list; omit them with --query")
@click.option("--query", default=None, help="preview the rows these words find instead; never writes (forget them by id)")
@click.option("--reason", default=None, help='why they go — the row keeps saying it while hidden (e.g. "a probe, not a finding")')
@click.option("--confirm", is_flag=True, help="write it; without it, ids only preview")
@click.option("--undo", is_flag=True, help="bring the ids back into recall (previews too until --confirm)")
@click.option("--limit", type=click.IntRange(1, 100), default=20, help="most rows a --query preview lists")
@ui.json_option
def forget(ids, query, reason, confirm, undo, limit, as_json):
    """Hide rows from recall for a reason; previews first.

    Nothing is written without --confirm: ids preview the rows they name, and --query
    previews what a search finds and never writes. A forgotten row is kept on record with
    its reason and `get` still reads it by id. Exit 1 when a preview finds nothing; 64 when
    the store refuses (no reason, an id not on record); 66 when there is no store.
    """
    import shlex
    from observer import curate
    ui.json_mode(as_json)
    wanted = _obs_ids(ids)
    reason = (reason or "").strip()
    if wanted and query:
        ui.err("forget takes ids or --query, not both")
        raise SystemExit(64)
    if not wanted and not query:
        ui.err("forget needs ids, or --query to preview what a search finds")
        raise SystemExit(64)
    if undo and query:
        ui.err("--undo takes the ids to bring back, not --query")
        raise SystemExit(64)
    if wanted:
        _need_store()
    elif confirm:
        ui.warn("a --query only previews: confirm the rows by id")
    got = _curate(curate.forget, ids=wanted, query=query, reason=reason, undo=undo, limit=limit, confirm=confirm)
    if ui.is_json():
        ui.emit(got)
        raise SystemExit(0)
    shown = " ".join(map(str, got.get("ids") or wanted))
    if got["kind"] == "forgotten":
        ui.ok(f"forgot {_memdb(got['ids'])} — {_fmt(got['reason'])}")
        ui.hint(f"{command('forget')} {shown} --undo --confirm (back into recall)")
        raise SystemExit(0)
    if got["kind"] == "unforgotten":
        ui.ok(f"{_memdb(got['ids'])} back in recall")
        ui.hint(f"{command('get')} {shown}")
        raise SystemExit(0)
    cands = got["candidates"]
    for i in [i for i in wanted if i not in {c["id"] for c in cands}]:
        ui.warn(f"memdb:{i} is not on record")
    if not cands:
        if query:
            ui.warn(f"no row matches '{_fmt(query)}'")
        raise SystemExit(1)
    ui.table(["id", "title"], [[str(c["id"]), _fmt(c["title"])] for c in cands],
             title=f"forget{' --undo' if undo else ''} · preview · {_fmt(query or 'by id')}  ({len(cands)})",
             styles=["cyan", None])
    then = "--undo --confirm" if undo else \
        f"--reason {_fmt(shlex.quote(reason)) if reason else '<why>'} --confirm"
    ui.hint(f"{command('forget')} {' '.join(str(c['id']) for c in cands)} {then}"
            + (" (keep only the ids you mean)" if query else ""))
    raise SystemExit(0)


observer.add_command(store_cli.store, name="store")
observer.add_command(memory_cli.memory_types, name="types")
observer.add_command(shadow_cli.shadow, name="shadow")


def brain_status():
    """the `ak` CLI's front-door rows for this plugin (the `cli:` contract's second
    half): the writer, the agent's model, the store's size. Cache, stat and one sqlite
    COUNT — this runs on every bare `ak`."""
    rows = []
    cwd = os.getcwd()
    try:
        w_on, why = capture_flag.state(cwd, capture_flag.switch_dir())
        model = observer_cli.observer_agent.load().get("model", "?")
        rows.append(("observer", f"writer {_on(w_on)}" + (f" [dim]({_fmt(why)})[/]" if why and why != "on" else "")
                     + f"   {model}", f"{command('mute --here')} · show"))
    except Exception as e:  # noqa: BLE001
        rows.append(("observer", f"[red]unavailable[/] [dim]({e})[/]", ""))
    try:
        s = store_cli.sc.status()
        if not s["exists"]:
            rows.append(("store", "[yellow]no store[/]", command("store init")))
        else:
            n = s["counts"].get("observations")
            rows.append(("store", f"{n:,} observations · {s['ours']:,} ours · last {_ago(s['last_observation'])}"
                         if n is not None else f"present · last {_ago(s['last_observation'])}",
                         command("store status")))
    except Exception as e:  # noqa: BLE001
        rows.append(("store", f"[red]unavailable[/] [dim]({e})[/]", ""))
    return rows


def brain_search(words: str, limit: int) -> list:
    """`ak search`'s observer rows: what `observer search WORDS` lists, as {id, title, when, where}.
    Union, never the dense channel: `ak` carries no embedder, and this must not hop venvs (in_plugin_venv)."""
    return [{"id": r["id"], "title": r["title"], "when": r["date"], "where": r.get("project") or ""}
            for r in obs_index.BACKEND.search(query=words, limit=limit, mode="union")]


def main():
    """The `observer` console script: run on its own, never inside `ak` (whose help is already installed)."""
    utf8_stdio()  # before anything prints: a cp1252 console or pipe cannot encode → or a note's emoji
    from observer import help as help_mod
    help_mod.install("observer", groups=[])  # ak's help: plain for an agent, usage errors that say what to type
    observer(prog_name="observer")


if __name__ == "__main__":
    main()
