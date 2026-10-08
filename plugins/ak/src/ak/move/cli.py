"""The verbs: `ak ws move`, `ak moves list|show|undo|resume|finalize`, `ak doctor`.

`ws move` plans with the whole package (plan.py), refuses unless the machine is quiet, freezes the
engine into the move's own folder and runs it there (runner.py), then checks the result from the
outside and undoes the lot if a check fails. Every move is one folder under
`<home>/moves/<id>/` — the plan, the write-ahead journal, the backups — so undo works
days later and without the kit.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys

import rich_click as click
from rich.markup import escape

from ak import help as help_mod
from ak._brand import cmd, env_name, lock_file
from ak import ui
from ak.groups import writes
from ak.move import doctor as D
from ak.move import kinds as K
from ak.move import ops
from ak.move import plan as P

ENGINE = ("runner.py", "ops.py", "lineage.py")
HERE = os.path.dirname(os.path.abspath(__file__))


def _moves_dir() -> str:
    return P.moves_dir()


def _plugins_dir():
    from ak.cli import _plugins_dir as pd  # the `ak` CLI's own answer: AK_PLUGINS_DIR, the checkout
    return pd()


def _python() -> str:
    """A bare interpreter for the frozen runner: never a venv inside the folder being moved."""
    return getattr(sys, "_base_executable", None) or sys.executable


def _lock():
    """The one-move-at-a-time lock, held while the returned file stays open (close() releases it)."""
    os.makedirs(_moves_dir(), exist_ok=True)
    fh = open(os.path.join(_moves_dir(), "LOCK"), "a+b")
    try:
        lock_file(fh, blocking=False)
    except OSError:
        fh.close()
        ui.err("another move is running")
        raise SystemExit(1)
    return fh


def _run_engine(d: str, verb: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
    p = subprocess.run([_python(), os.path.join(d, "engine", "runner.py"), verb, d], cwd=os.path.expanduser("~"),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    lines = [ln for ln in (p.stdout or "").splitlines() if ln.startswith("{")]
    if not lines:
        return {"ok": False, "error": (p.stderr or "the runner printed nothing").strip()[-600:], "rc": p.returncode}
    out = json.loads(lines[-1])
    out["rc"] = p.returncode
    return out


def _findings_rows(findings) -> list:
    icon = {"fail": "[red]✗[/]", "warn": "[yellow]![/]", "info": "[dim]·[/]", "pass": "[green]✓[/]"}
    return [[icon.get(f["level"], f["level"]) if ui.is_rich() else f["level"], f.get("check") or f.get("handler"),
             f["what"], f.get("fix") or ""] for f in findings]


def _show_plan(m: P.Move):
    by: dict = {}
    for s in m.ordered():
        by.setdefault(s["handler"], []).append(s["name"])
    if ui.is_rich():
        for h, names in by.items():
            ui.text(f"[b]{h}[/] [dim]· {len(names)} step(s)[/]")
            for n in names[:12]:
                ui.text(f"  [dim]→[/] {escape(n)}")
            if len(names) > 12:
                ui.text(f"  [dim]… {len(names) - 12} more[/]")
    else:
        ui.table(["handler", "step"], [[s["handler"], s["name"]] for s in m.ordered()])
    if m.findings:
        ui.table(["", "handler", "finding", "fix"], _findings_rows([f.as_dict() for f in m.findings]), box=None)


def verify(m: P.Move, gateway_before: bool) -> list:
    """Checked from the outside, after the runner said done. Any line here means undo."""
    bad = []
    if not os.path.isdir(m.new):
        bad.append(f"{m.new} is not there")
    if os.path.isdir(m.old) and not os.path.islink(m.old):
        bad.append(f"{m.old} is still a folder")
    for a, b in m.slugs.items():
        if os.path.isdir(a) or not os.path.isdir(b):
            bad.append(f"chat history {os.path.basename(a)} did not land at {os.path.basename(b)}")
    if os.path.isdir(os.path.join(m.new, ".git")):
        rc, out = P._git("-C", m.new, "worktree", "list", "--porcelain")
        if rc:
            bad.append("git cannot read the moved repository")
        elif any(line.startswith("prunable") for line in out.splitlines()):
            bad.append("a worktree is still unlinked after the repair")
    if gateway_before and not P.gateway_up():
        bad.append("the gateway stopped answering")  # it never should: the move never touches it
    return bad


@writes
@click.command("move", epilog=help_mod.epilog(
    f"Examples:\n  {cmd('ws move')} ~/old-dir ~/new-dir --dry-run   what would change, nothing changed\n"
    f"  {cmd('ws move')} ~/old-dir ~/new-dir             move it: folder, worktrees, chat history, indexes\n"
    f"  {cmd('ws move')} --adopt ~/old ~/new               you already ran mv: reattach everything else\n"
    f"  {cmd('moves undo')} <id>                           put it all back"))
@click.argument("old", help="the workspace folder as it is now, e.g. ~/old-dir")
@click.argument("new", help="where it goes, e.g. ~/new-dir (must not exist yet)")
@click.option("--dry-run", is_flag=True, help="print the plan and change nothing")
@click.option("--adopt", is_flag=True, help="it was already moved by hand: skip the mv, fix every pointer")
@click.option("--link", is_flag=True, help="leave a symlink at the old path instead of a note")
@click.option("--keep", is_flag=True, help="keep the result even when a check after the move fails")
@ui.json_option
def ws_move(old, new, dry_run, adopt, link, keep, as_json):
    """Move a workspace and everything that points at it.

    The folder, its git worktrees, its Claude Code chat history (so /resume and -c keep working),
    the session index, the trace store, ~/.claude.json, settings and the memory's caches. History —
    a transcript's recorded cwd, an observer row — is never rewritten: a line in lineage.jsonl lets
    every reader find it under the new name. The gateway is never touched. Runs only from a plain
    terminal with no Claude Code session open; every step is journaled and undoable.
    """
    ui.json_mode(as_json)
    m = P.build(old, new, adopt=adopt, link=link, plugins_dir=_plugins_dir())
    fails = [f for f in m.findings if f.level == "fail"]
    if dry_run or fails:
        if ui.is_json():
            ui.emit({"id": m.id, "old": m.old, "new": m.new, "adopt": adopt, "facts": m.facts,
                     "steps": m.ordered(), "findings": [f.as_dict() for f in m.findings], "ran": False})
        else:
            ui.text(f"[b]{m.old}[/] → [b]{m.new}[/]" + (" [dim](adopt: already moved)[/]" if adopt else ""))
            _show_plan(m)
            if m.facts:
                ui.kv([(k.replace("_", " "), v) for k, v in m.facts.items()], boxed=False)
        if fails:
            ui.err(f"not moving: {len(fails)} problem(s) above")
            raise SystemExit(1)
        ui.hint(f"{cmd('ws move')} {'--adopt ' if adopt else ''}{m.old} {m.new}")
        return
    quiet = P.quiesce(m)
    if quiet:
        if ui.is_json():
            ui.emit({"id": m.id, "ran": False, "findings": [f.as_dict() for f in quiet]})
        else:
            ui.table(["", "handler", "finding", "fix"], _findings_rows([f.as_dict() for f in quiet]), box=None)
        ui.err("not moving: the machine is not quiet")
        raise SystemExit(1)
    fd = _lock()
    try:
        gw = P.gateway_up()
        d = os.path.join(_moves_dir(), m.id)
        os.makedirs(os.path.join(d, "engine"))
        plan = m.as_plan()
        plan["gateway_up"] = gw
        with open(os.path.join(d, "plan.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(plan, f, indent=1, ensure_ascii=False)
        for n in ENGINE:
            shutil.copy2(os.path.join(HERE, n), os.path.join(d, "engine", n))
        shutil.copy2(os.path.join(HERE, "..", "_brand.py"), os.path.join(d, "engine", "_brand.py"))
        ui.text(f"[dim]move {m.id} · undo any time: {cmd('moves undo')} {m.id}  "
                f"(or, without the kit: python3 {d}/engine/runner.py undo {d})[/]", err=True)
        res = _run_engine(d, "apply")
        problems = verify(m, gw) if res.get("ok") else []
        undone = None
        if problems and not keep:
            undone = _run_engine(d, "undo")
    finally:
        fd.close()
    out = {"id": m.id, "old": m.old, "new": m.new, "ran": True, "ok": bool(res.get("ok")) and not problems,
           "result": res, "problems": problems, "undone": undone, "facts": m.facts}
    if ui.is_json():
        ui.emit(out)
    elif out["ok"]:
        ui.ok(f"moved {m.old} → {m.new} · {len(m.steps)} steps · "
              + " · ".join(f"{v} {k.replace('_', ' ')}" for k, v in m.facts.items()))
        ui.hint(f"{cmd('moves show')} {m.id}")
    else:
        why = res.get("error") or "; ".join(problems)
        ui.err(f"move {m.id} did not hold: {why}" + (" — undone, nothing changed" if undone and undone.get("ok")
                                                     else ""))
    raise SystemExit(0 if out["ok"] else 1)


# ── the other kinds: a plugin or marketplace rename, the kit's own home ─────────────────────
def _execute(m: P.Move, dry_run: bool, keep: bool, as_json: bool, verify_fn, again: str, what: str):
    """Show the plan, or check the machine is quiet, freeze the engine and run it; undo on a failed check."""
    ui.json_mode(as_json)
    fails = [f for f in m.findings if f.level == "fail"]
    if dry_run or fails:
        if ui.is_json():
            ui.emit({"id": m.id, "kind": m.kind, "old": m.old, "new": m.new, "facts": m.facts, "steps": m.ordered(),
                     "findings": [f.as_dict() for f in m.findings], "ran": False})
        else:
            ui.text(f"[b]{m.old}[/] → [b]{m.new}[/]")
            _show_plan(m)
            if m.facts:
                ui.kv([(k.replace("_", " "), v) for k, v in m.facts.items()], boxed=False)
        if fails:
            ui.err(f"not moving: {len(fails)} problem(s) above")
            raise SystemExit(1)
        ui.hint(again)
        return
    quiet = P.quiesce(m)
    if quiet:
        if ui.is_json():
            ui.emit({"id": m.id, "ran": False, "findings": [f.as_dict() for f in quiet]})
        else:
            ui.table(["", "handler", "finding", "fix"], _findings_rows([f.as_dict() for f in quiet]), box=None)
        ui.err("not moving: the machine is not quiet")
        raise SystemExit(1)
    fd = _lock()
    try:
        gw = P.gateway_up()
        d = os.path.join(_moves_dir(), m.id)
        os.makedirs(os.path.join(d, "engine"))
        plan = m.as_plan()
        plan["gateway_up"] = gw
        with open(os.path.join(d, "plan.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(plan, f, indent=1, ensure_ascii=False)
        for n in ENGINE:
            shutil.copy2(os.path.join(HERE, n), os.path.join(d, "engine", n))
        shutil.copy2(os.path.join(HERE, "..", "_brand.py"), os.path.join(d, "engine", "_brand.py"))
        ui.text(f"[dim]move {m.id} · undo any time: {cmd('moves undo')} {m.id}  "
                f"(or, without the kit: python3 {d}/engine/runner.py undo {d})[/]", err=True)
        res = _run_engine(d, "apply")
        problems = verify_fn(m) if res.get("ok") else []
        if res.get("ok") and gw and not P.gateway_up():
            problems.append("the gateway stopped answering")  # it never should: this move never touches it
        undone = _run_engine(d, "undo") if problems and not keep else None
    finally:
        fd.close()
    out = {"id": m.id, "kind": m.kind, "old": m.old, "new": m.new, "ran": True,
           "ok": bool(res.get("ok")) and not problems, "result": res, "problems": problems, "undone": undone,
           "facts": m.facts}
    if ui.is_json():
        ui.emit(out)
    elif out["ok"]:
        ui.ok(f"{what} {m.old} → {m.new} · {len(m.steps)} steps · "
              + " · ".join(f"{v} {k.replace('_', ' ')}" for k, v in m.facts.items()))
        ui.hint(f"{cmd('moves show')} {m.id}")
    else:
        why = res.get("error") or "; ".join(problems)
        ui.err(f"move {m.id} did not hold: {why}" + (" — undone, nothing changed" if undone and undone.get("ok")
                                                     else ""))
    raise SystemExit(0 if out["ok"] else 1)


@writes
@click.command("rename", epilog=help_mod.epilog(
    f"Examples:\n  {cmd('plugin rename')} tag@old tag@new --dry-run      what would change, nothing changed\n"
    f"  {cmd('plugin rename')} harness@old app@old                   one plugin id, its cache and data folders\n"
    f"  {cmd('plugin rename')} --marketplace old new                 a marketplace and every plugin under it\n"
    f"  {cmd('moves undo')} <id>                                     put it all back"))
@click.argument("ids", nargs=-1, metavar="[<p>@<m> <p2>@<m2>]",
                )
@click.option("--marketplace", "market", is_flag=True, help="rename a marketplace: give OLD and NEW names")
@click.option("--dry-run", is_flag=True, help="print the plan and change nothing")
@click.option("--keep", is_flag=True, help="keep the result even when a check after the move fails")
@ui.json_option
def plugin_rename(ids, market, dry_run, keep, as_json):
    """Rename a plugin id or a marketplace everywhere Claude Code keeps it.

    enabledPlugins in settings, installed_plugins.json, known_marketplaces.json, the plugin's cache and
    data folders. Runs only from a plain terminal with no Claude Code session open; every step is
    journaled and undoable.
    """
    if len(ids) != 2:
        ui.err("give the old and the new " + ("marketplace name" if market else "id (<plugin>@<marketplace>)"))
        raise SystemExit(2)
    m = K.plugin_rename(ids[0], ids[1], marketplace=market)
    _execute(m, dry_run, keep, as_json, K.verify_plugin,
             f"{cmd('plugin rename')} {'--marketplace ' if market else ''}{ids[0]} {ids[1]}", "renamed")


@click.group("home", invoke_without_command=True,
             epilog=help_mod.epilog(f"Examples:\n  {cmd('home')}                 where it is, what it holds\n"
                                    f"  {cmd('home move')} ~/.new-home --dry-run"))
@click.pass_context
def home(ctx):
    """The kit's own state home: where it is; move it.

    Bare [b]ak home[/] shows where the home is and what it holds."""
    if ctx.invoked_subcommand is not None:
        return
    from pathlib import Path
    root = Path(P.kit_home())
    held = sorted(p.name + "/" for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")) if root.is_dir() else []
    ui.kv([("home", str(root) if root.is_dir() else f"{root} (missing)"), ("holds", " ".join(held) or "—")],
          title="kit home")
    ui.hint(f"{cmd('home move')} <new> --dry-run")


@writes
@home.command("move")
@click.argument("new", help="where the home goes, e.g. ~/.new-home (must not exist yet, same disk)")
@click.option("--dry-run", is_flag=True, help="print the plan and change nothing")
@click.option("--keep", is_flag=True, help="keep the result even when a check after the move fails")
@ui.json_option
def home_move(new, dry_run, keep, as_json):
    """Move the kit's state home and everything that points at it.

    The folder is renamed on the same disk and the old path stays as a symlink until `moves finalize`,
    so a straggler running old code writes into the new home instead of planting an empty one. Settings
    (WIKILINKS_ROOTS and every other path), ~/.claude.json, chat-history folders and the registries
    inside the home follow. The gateway is not touched. Runs only from a plain terminal with no Claude
    Code session open; every step is journaled and undoable.
    """
    m = K.home_move(new, plugins_dir=_plugins_dir())
    _execute(m, dry_run, keep, as_json, K.verify_home, f"{cmd('home move')} {new}", "moved home")


# ── moves: the record of every move ─────────────────────────────────────────
def _find(move_id: str) -> str:
    hits = [d for d in glob.glob(os.path.join(_moves_dir(), f"{move_id}*")) if os.path.isdir(d)]
    if len(hits) != 1:
        ui.err(f"no move {move_id}" if not hits else f"{move_id} names {len(hits)} moves — give more of the id")
        raise SystemExit(1 if not hits else 2)
    return hits[0]


def _plan_of(d):
    with open(os.path.join(d, "plan.json"), encoding="utf-8") as f:
        return json.load(f)


@click.group("moves", invoke_without_command=True, epilog=help_mod.epilog(
    f"Examples:\n  {cmd('moves')}                 every move, newest first\n"
    f"  {cmd('moves show')} <id>       its steps and how far each got\n"
    f"  {cmd('moves undo')} <id>       put everything back\n"
    f"  {cmd('moves finalize')} <id>   drop its backups and the note at the old path"))
@click.pass_context
def moves(ctx):
    """List the moves this kit has made, and undo one."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(moves_list)


@moves.command("ls")
@ui.json_option
def moves_list(as_json):
    """Every move: id, state, from → to."""
    ui.json_mode(as_json)
    rows = []
    for d in sorted(glob.glob(os.path.join(_moves_dir(), "*")), reverse=True):
        if not os.path.isfile(os.path.join(d, "plan.json")):
            continue
        p = _plan_of(d)
        rows.append({"id": p["id"], "state": D.status(d), "old": p["old"], "new": p["new"], "at": p.get("at")})
    if ui.is_json():
        ui.emit(rows)
        return
    if not rows:
        ui.text("no moves yet")
        ui.hint(f"{cmd('ws move')} <old> <new> --dry-run")
        return
    ui.table(["id", "state", "from", "to", "at"], [[r["id"], r["state"], r["old"], r["new"], r["at"]] for r in rows],
             box=None)


@moves.command("show")
@click.argument("move_id", help=f"a move id from `{cmd('moves')}`, or its first characters")
@ui.json_option
def moves_show(move_id, as_json):
    """One move's steps and how far each got."""
    ui.json_mode(as_json)
    d = _find(move_id)
    p = _plan_of(d)
    st: dict = {}
    try:
        with open(os.path.join(d, "journal.jsonl"), encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                st.setdefault(e["seq"], {}).update(e)
    except OSError:
        pass
    steps = [{"seq": i, "handler": s["handler"], "step": s["name"], "status": st.get(i, {}).get("status", "-"),
              "error": st.get(i, {}).get("error")} for i, s in enumerate(p["steps"], 1)]
    if ui.is_json():
        ui.emit({"id": p["id"], "state": D.status(d), "old": p["old"], "new": p["new"], "steps": steps})
        return
    ui.kv([("move", p["id"]), ("state", D.status(d)), ("from", p["old"]), ("to", p["new"]), ("folder", d)],
          boxed=False)
    ui.table(["#", "status", "handler", "step"], [[s["seq"], s["status"], s["handler"], s["step"]] for s in steps],
             box=None)
    for s in steps:
        if s["error"]:
            ui.warn(f"step {s['seq']}: {s['error']}")


def _as_planned(d: str) -> str:
    """The move's folder under the path it was planned with. A home move renames the folder that holds its
    own journal: the runner must keep writing through the old path (the link), which an undo puts back."""
    p = _plan_of(d)
    if p.get("kind") == "home" and ops.under(d, p["new"]):
        return ops.remap(d, p["new"], p["old"])
    return d


def _engine_verb(move_id: str, verb: str):
    d = _find(move_id)
    if D.status(d) == "finalized":  # before _as_planned: a home move's old path is gone by now
        ui.err("that move is finalized — its backups are gone, so it cannot be undone")
        raise SystemExit(1)
    d = _as_planned(d)
    m = P.Move(id="", old="", new="", claude=P.claude_dir(), home=P.kit_home())
    quiet = P.quiesce(m)
    if quiet:
        ui.table(["", "handler", "finding", "fix"], _findings_rows([f.as_dict() for f in quiet]), box=None)
        ui.err("not now: the machine is not quiet")
        raise SystemExit(1)
    fd = _lock()
    try:
        res = _run_engine(d, verb)
    finally:
        fd.close()
    if res.get("ok"):
        ui.ok(f"{verb} {os.path.basename(d)}: " + (f"{res.get('undone')} step(s) undone" if verb == "undo"
                                                    else "done"))
    else:
        ui.err(f"{verb} {os.path.basename(d)}: {res.get('error') or res.get('errors')}")
    raise SystemExit(0 if res.get("ok") else 1)


@writes
@moves.command("undo")
@click.argument("move_id", help=f"a move id from `{cmd('moves')}`, e.g. 20260928T101500-3fa9")
def moves_undo(move_id):
    """Undo a move: every step it took, newest first."""
    _engine_verb(move_id, "undo")


@writes
@moves.command("resume")
@click.argument("move_id", help=f"a move id from `{cmd('moves')}` that stopped half way")
def moves_resume(move_id):
    """Finish a move that stopped half way."""
    _engine_verb(move_id, "resume")


@writes
@moves.command("finalize")
@click.argument("move_id", help=f"a move id from `{cmd('moves')}`")
def moves_finalize(move_id):
    """Drop a move's backups and the note left at its old path.

    After this it cannot be undone. The record of the move (its plan, journal and lineage rows)
    stays, so history keeps reading under the new name.
    """
    d = _find(move_id)
    if D.status(d) != "applied":
        ui.err(f"only an applied move can be finalized (this one is {D.status(d)})")
        raise SystemExit(1)
    p = _plan_of(d)
    old = p["old"]
    tomb = P._tombstone_of(old) if p.get("kind", "workspace") in ("workspace", "home") else {}
    if tomb and (os.path.islink(old) or tomb.get("move") == p["id"]):
        if not os.path.islink(old):
            os.chmod(old, 0o644)
        os.unlink(old)
    for sub in ("backup", "stash"):
        shutil.rmtree(os.path.join(d, sub), ignore_errors=True)
    open(os.path.join(d, "FINALIZED"), "w").close()
    ui.ok(f"finalized {p['id']}: backups dropped, {old} is free")
    if p.get("kind") == "home" and P.kit_home() != os.path.realpath(p["new"]):
        ui.warn(f"the home moved to {p['new']}: point {env_name('HOME')} at it (and {env_name('PATH')}, if you "
                f"set one, at its vault/), or `moves show/undo/resume` will not find this move's journal")


# ── doctor ──────────────────────────────────────────────────────────────────
@click.command("doctor", epilog=help_mod.epilog(
    f"Examples:\n  {cmd('doctor')}          what came loose, and the command that fixes each\n"
    f"  {cmd('doctor')} --json   the same, as one document"))
@ui.json_option
def doctor(as_json):
    """Find what came loose: moved folders, stale plugin ids.

    Read-only. Chat history whose folder moved or vanished, plugin ids their marketplace no longer
    ships, ~/.claude.json and settings paths (inside a command too) that point at nothing, worktrees git lost, broken
    links, a move left half way. Each finding names its fix; a fix that moves something is a
    journaled `ak ws move`, so it can be undone. Exit 1 when anything failed.
    """
    ui.json_mode(as_json)
    findings = D.run(_plugins_dir())
    fails = sum(f["level"] == "fail" for f in findings)
    if ui.is_json():
        ui.emit(findings)
        raise SystemExit(0)
    ui.table(["", "check", "finding", "fix"], _findings_rows(findings), box=None)
    for f in findings:
        for e in f.get("examples") or []:
            ui.text(f"  [dim]{f['check']}: {e}[/]")
    if fails:
        ui.err(f"{fails} thing(s) came loose")
    else:
        ui.ok("nothing came loose")
    raise SystemExit(1 if fails else 0)
