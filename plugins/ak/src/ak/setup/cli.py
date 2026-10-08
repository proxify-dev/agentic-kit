"""The verbs: `ak setup`, `ak setup status`, `ak setup undo`.

`ak setup` at a terminal opens the TUI. Everywhere else (a pipe, an agent, --dry-run, --json) it
prints the plan and the diffs and setup itself writes nothing (probing the real `claude` lets Claude Code
keep its own files): only `--yes` applies. Each apply is one run folder
(backup.py), so `ak setup undo` takes it back.

Imports stay cheap: machine, catalog, engine and backup load inside the verbs (ak.cli mounts this
group on every `ak`), and Textual loads only when the TUI opens.
"""
from __future__ import annotations

import os
import re
import shlex
import sys

import rich_click as click

from ak import help as help_mod
from ak import ui
from ak._brand import cmd, env_name
from ak.groups import FuzzyGroup, writes

NEEDED_TOOLS = ("uv", "git", "claude")
TOOL_HINT = {"uv": "install uv: https://docs.astral.sh/uv/",
             "git": "install git: https://git-scm.com/downloads",
             "claude": "install Claude Code first: https://claude.com/claude-code"}
STATE = {"install": "will install", "in-place": "in place", "blocked": "blocked", "skipped": "skipped",
         "off": "not selected", "absent": "not installed"}
STATE_STYLE = {"install": "green", "in-place": "dim", "blocked": "yellow", "skipped": "yellow", "off": "dim",
               "absent": "dim"}
LEVEL_MARK = {"fail": "[red]✗[/]", "warn": "[yellow]![/]", "info": "[dim]·[/]", "pass": "[green]✓[/]"}


def _tools_missing(m, ids) -> list[str]:
    """uv, git and claude that the components in ids (and what they pull in) need and this machine lacks."""
    from ak.setup import catalog
    needed = {name for cid in catalog.with_deps(ids) for name, _ in catalog.get(cid).needs.bins}
    return [t for t in NEEDED_TOOLS if t in needed and not m.bin(t).found]


def _stop_without(m, ids) -> None:
    """Exit 127 naming each tool the install needs and the machine lacks."""
    missing = _tools_missing(m, ids)
    for tool in missing:
        ui.err(f"{tool} not found on PATH — {TOOL_HINT[tool]}")
    if missing:
        raise SystemExit(127)


def _at_terminal() -> bool:
    """A person is on the other end: rich output, a TTY in and a TTY out."""
    return ui.is_rich() and sys.stdin.isatty() and sys.stdout.isatty()


def _has_terminal() -> bool:
    """stdin and stdout are both a TTY: something that needs the person (a browser login) can run."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _stop_for_interactive(steps, as_json: bool) -> None:
    """Exit 1 before anything is applied when a step needs the terminal and this run has none (a pipe, an
    agent, --json: its stdout is the one document)."""
    if _has_terminal() and not as_json:
        return
    for step in steps:
        if step.interactive:
            ui.err(f"{step.title} needs a terminal: run it by hand in one ({shlex.join(step.argv) or step.preview}), "
                   f"then run {cmd('setup --yes')} again; nothing was applied")
            raise SystemExit(1)


def _words(only: str) -> list[str]:
    return [w for w in re.split(r"[,\s]+", only) if w]


def _pick(m, only: str | None) -> tuple[list[str], list[str]]:
    """(ids to install in dependency order, the ones --only pulled in that it did not name).
    Exits 64 on an unknown id, 127 on a missing tool, 1 on a component this machine can't run."""
    from ak.setup import catalog
    if not only:
        _stop_without(m, [c.id for c in catalog.COMPONENTS if c.default_on and c.selectable])
        return catalog.with_deps(catalog.defaults(m)), []
    asked = _words(only)
    unknown = [w for w in asked if w not in catalog.BY_ID]
    if unknown:
        ui.err(f"unknown component: {', '.join(unknown)}")
        ui.text("ids: " + ", ".join(c.id for c in catalog.COMPONENTS if c.selectable), err=True, markup=False)
        raise SystemExit(64)
    _stop_without(m, asked)
    refused = [(w, catalog.get(w).note if not catalog.get(w).selectable else catalog.blocked_with_deps(catalog.get(w), m))
               for w in asked]
    refused = [(w, why) for w, why in refused if why]
    for w, why in refused:
        ui.err(f"{w}: {why}")
    if refused:
        raise SystemExit(1)
    ids = catalog.with_deps(asked)
    return ids, [i for i in ids if i not in asked]


def _state(c, m, selected: set[str], skipped: dict) -> tuple[str, str | None]:
    """(state key, the reason when there is one) for one component: blocked on this machine, left out by
    the engine (skipped: it can't be put in place, or a setting holds another value), already in place."""
    from ak.setup import catalog, engine
    why = c.note if not c.selectable else catalog.blocked_with_deps(c, m)
    if why:
        return "blocked", why
    if c.id == "shim" and m.shim.get("legacy"):
        return "in-place", "an older bash shim"
    if skipped.get(c.id) == engine.IN_PLACE:
        return "in-place", None
    if str(skipped.get(c.id, "")).startswith(engine.KEPT_BY_CLAUDE):  # the kit's clone, made by the marketplace step
        return ("install" if c.id in selected else "off"), skipped[c.id]
    if c.id in skipped:
        return "skipped", skipped[c.id]
    return ("install" if c.id in selected else "off"), None


def _components(m, selected: set[str], skipped: dict, idle: str = "off") -> list[dict]:
    from ak.setup import catalog
    rows = []
    for c in catalog.COMPONENTS:
        state, why = _state(c, m, selected, skipped)
        rows.append({"id": c.id, "group": c.group, "state": idle if state == "off" else state,
                     "blocked": why, "selected": c.id in selected, "private": c.private})
    return rows


def _show_components(rows: list[dict]) -> None:
    def cell(r):
        label = STATE[r["state"]] + (f": {r['blocked']}" if r["blocked"] else "")
        return f"[{STATE_STYLE[r['state']]}]{ui.escape(label)}[/]" if ui.is_rich() else label
    ui.table(["component", "group", "state"],
             [[r["id"], r["group"] + (" · private" if r.get("private") else ""), cell(r)] for r in rows])


def _kit_doc() -> dict:
    """The kit the components were read from (None: the public kit's snapshot), and the marketplace its plugins install from."""
    from ak.setup import catalog
    return {"tree": str(catalog.TREE) if catalog.TREE else None, "marketplace": catalog.MARKETPLACE}


def _machine_doc(m) -> dict:
    return {"os": m.os, "arch": m.arch, "in_claude": m.in_claude, "service_manager": m.service_manager,
            "bins": {n: ({"path": b.path, "version": b.version} if b.found else None) for n, b in m.bins.items()},
            "release": ({"path": m.release.path, "exists": m.release.exists, "head": m.release.head}
                        if m.release else None)}


def _step_doc(step, status: str = "planned") -> dict:
    return {"id": step.id, "title": step.title, "kind": step.kind, "preview": step.preview, "status": status}


def _show_steps(steps) -> None:
    for n, step in enumerate(steps, 1):
        shown = step.preview or (shlex.join(step.argv) if step.argv else "")
        if shown:
            ui.panel(shown, title=f"{n}. {step.title}")
        else:
            ui.text(f"[b]{n}. {ui.escape(step.title)}[/]")


def _show_findings(findings: list[dict]) -> None:
    ui.table(["", "check", "finding", "fix"],
             [[LEVEL_MARK.get(f["level"], f["level"]) if ui.is_rich() else f["level"], f.get("check", ""),
               f.get("what", ""), f.get("fix") or ""] for f in findings], box=None)


def _again(only: str | None, repo: str | None) -> str:
    return cmd("setup --yes" + (f" --only {only}" if only else "") + (f" --repo {shlex.quote(repo)}" if repo else ""))


def _apply(p, statuses: dict):
    """Run the plan into a new run folder, noting each step's outcome in statuses; (run, whether every step held)."""
    from ak.setup import backup, engine
    tails: dict[str, list[str]] = {}

    def emit(event, **d):
        step = d.get("step")
        if event == "step_start":
            tails[step.id] = []
            ui.text(f"[dim]→ {ui.escape(step.title)}[/]")
        elif event == "line":
            tails.setdefault(step.id, []).append(d.get("text", ""))
            del tails[step.id][:-8]
            if ui.is_rich():
                ui.text(f"[dim]  {ui.escape(d.get('text', ''))}[/]")
        elif event == "step_done":
            status, detail = d.get("status", "done"), d.get("detail", "")
            statuses[step.id] = status
            if status == "done":
                ui.ok(step.title)
            elif status == "background":
                ui.ok(f"{step.title} — keeps going in the background; {cmd()} shows how far it is")
            elif status == "skipped":
                ui.warn(f"skipped: {step.title}" + (f" — {detail}" if detail else ""))
            else:
                ui.err(f"{step.title}" + (f" — {detail}" if detail else ""))
                if not ui.is_rich():
                    for ln in tails.get(step.id, []):
                        ui.text(f"  {ln}", err=True, markup=False)

    run = backup.Run.new()
    return run, engine.apply(p, run, emit)


def _after_apply(m, ids, run, applied: bool, as_json: bool, doc: dict, statuses: dict) -> None:
    """Re-detect, verify, print the findings and the way back, exit 1 when anything failed."""
    from ak.setup import engine, machine
    found = engine.verify(machine.detect(), ids)
    failed = sum(f["level"] == "fail" for f in found)
    doc["run"] = run.id if run else None
    doc["verify"] = found
    doc["steps"] = [{**s, "status": statuses.get(s["id"], "not run")} for s in doc["steps"]]
    if as_json:
        ui.emit(doc)
    else:
        _show_findings(found)
        if run:
            ui.kv([("run", run.id), ("backup", run.path)], boxed=False)
            ui.hint(cmd(f"setup undo {run.id}"))
        if m.in_claude:
            ui.warn("this ran inside a Claude Code session: PATH and plugin changes show in a new terminal")
        elif applied and not failed:
            ui.hint("open a new terminal, then run claude" + (f" · then {cmd('memory try')}" if "memory" in ids else ""))
    if not applied:
        ui.err("a step failed: nothing after it ran" + (f" — take it back with {cmd('setup undo ' + run.id)}" if run else ""))
    if failed:
        ui.err(f"{failed} check(s) failed")
    raise SystemExit(0 if applied and not failed else 1)


EPILOG = (f"Examples:\n  {cmd('setup')}                          pick what to install, in a terminal\n"
          f"  {cmd('setup --dry-run')}                what would be installed, every diff shown, setup writes nothing\n"
          f"  {cmd('setup --yes')}                    install the defaults without asking\n"
          f"  {cmd('setup --yes --only memory')}      just memory, and what it needs\n"
          f"  {cmd('setup status')}                   what is installed and whether it holds\n"
          f"  {cmd('setup undo')}                     take the last run back")


@writes
@click.group("setup", cls=FuzzyGroup, invoke_without_command=True, epilog=help_mod.epilog(EPILOG))
@click.option("--yes", "-y", is_flag=True, help="install without asking (otherwise only the plan is printed)")
@click.option("--only", metavar="IDS", help="install just these components, e.g. memory,observer (what they need comes along)")
@click.option("--dry-run", is_flag=True, help="print the plan and every diff; setup itself writes nothing")
@click.option("--repo", metavar="URL", help="where the kit comes from, e.g. https://github.com/proxify-dev/agentic-kit "
              "(a URL: Claude Code clones it and keeps it current; a path or file://: setup clones it to the release clone)")
@ui.json_option
@click.pass_context
def setup(ctx, yes, only, dry_run, repo, as_json):
    """Install the kit: plugins, the ak tool, settings, the shim.

    Looks at this machine, lists what it can install (what it can't is greyed out with the reason),
    shows every settings and shell-rc change as a diff, then applies in dependency order and checks
    the result. At a terminal it opens a picker; in a pipe or an agent it prints the plan and exits 0:
    only --yes writes. Every write is backed up first into one run folder, so `setup undo` puts it back.
    """
    if ctx.invoked_subcommand:
        return
    ui.json_mode(as_json)
    if repo:
        os.environ[env_name("REPO_URL")] = repo  # source.repo_url() reads it, the engine and the TUI alike
    if _at_terminal() and not (yes or dry_run or only or as_json):
        from ak.setup import tui  # Textual loads only here
        raise SystemExit(tui.run())
    from ak.setup import engine, machine
    m = machine.detect()
    ids, pulled = _pick(m, only)
    if pulled:
        ui.warn(f"also installing {', '.join(pulled)}: {only.replace(',', ', ')} needs " + ("it" if len(pulled) == 1 else "them"))
    p = engine.plan(m, ids)
    components = _components(m, set(ids), p.skipped)
    doc = {"machine": _machine_doc(m), "kit": _kit_doc(), "components": components, "steps": [_step_doc(s) for s in p.steps],
           "skipped": p.skipped, "run": None, "verify": None}
    if not (yes and not dry_run):
        if as_json:
            ui.emit(doc)
            return
        _show_components(components)
        _show_steps(p.steps)
        if p.steps:
            ui.hint(_again(only, repo))
        else:
            ui.ok("everything picked is already in place")
        return
    if not p.steps:
        _after_apply(m, ids, None, True, as_json, doc, {})
    _stop_for_interactive(p.steps, as_json)
    statuses: dict[str, str] = {}
    if not as_json:
        _show_components(components)
    run, applied = _apply(p, statuses)
    _after_apply(m, ids, run, applied, as_json, doc, statuses)


@setup.command("status", epilog=help_mod.epilog(
    f"Examples:\n  {cmd('setup status')}          what is installed, and the checks on it\n"
    f"  {cmd('setup status --json')}   the same, as one document"))
@ui.json_option
def status(as_json):
    """Show what is installed and whether it holds.

    Read-only. Every component with its state on this machine (in place, not installed, or blocked with
    the reason), then the checks on what is in place. Exit 1 when a check failed.
    """
    ui.json_mode(as_json)
    from ak.setup import catalog, engine, machine
    m = machine.detect()
    p = engine.plan(m, catalog.with_deps([c.id for c in catalog.COMPONENTS
                                          if c.selectable and not catalog.blocked_with_deps(c, m)]))
    components = _components(m, set(), p.skipped, idle="absent")
    found = engine.verify(m, [r["id"] for r in components if r["state"] == "in-place"], with_deps=False)
    if as_json:
        ui.emit({"machine": _machine_doc(m), "kit": _kit_doc(), "components": components, "verify": found})
        return
    _show_components(components)
    _show_findings(found)
    failed = sum(f["level"] == "fail" for f in found)
    if failed:
        ui.err(f"{failed} check(s) failed")
        raise SystemExit(1)
    ui.hint(cmd("setup"))


class Miss(Exception):
    """No run to act on: the message and the exit code."""
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def _target(run: str | None, runs: list[dict]) -> dict | None:
    """The run `undo` acts on: RUN (a unique prefix of an id), else the newest not yet undone; None when
    every run is already taken back. Miss (exit 1) when there is none to find, (exit 2) when RUN names several."""
    if run is None:
        if not runs:
            raise Miss("no setup run to take back", 1)
        return next((r for r in runs if not r["undone"]), None)
    near = [r for r in runs if r["id"] == run] or [r for r in runs if r["id"].startswith(run)]
    if len(near) != 1:
        raise Miss(f"no setup run {run!r}" if not near else f"{run!r} names {len(near)} setup runs: "
                   + ", ".join(r["id"] for r in near), 1 if not near else 2)
    return near[0]


@writes
@setup.command("undo", epilog=help_mod.epilog(
    f"Examples:\n  {cmd('setup undo')}                   take the newest run back\n"
    f"  {cmd('setup undo --dry-run')}         what it would do, nothing changed\n"
    f"  {cmd('setup undo')} 20261001-101500   a run by id (a prefix is enough)\n"
    f"  {cmd('setup undo --list')}            every run, newest first"))
@click.argument("run", required=False, help="the run to take back, e.g. 20261001-101500 (default: the newest)")
@click.option("--dry-run", is_flag=True, help="print what it would do, change nothing")
@click.option("--list", "list_runs", is_flag=True, help="list the runs instead")
@ui.json_option
def undo(run, dry_run, list_runs, as_json):
    """Take a setup run back.

    Newest step first, and only what that run did: settings keys whose value is still ours, rc lines
    it added, a file nobody wrote since, what it installed. A second undo does nothing. Works while
    Claude Code sessions are open.
    """
    ui.json_mode(as_json)
    from ak.setup import backup
    runs = backup.list_runs()
    if list_runs:
        if as_json:
            ui.emit(runs)
            return
        ui.table(["run", "started", "steps", "undone"],
                 [[r["id"], r["started"], r["steps"], "yes" if r["undone"] else ""] for r in runs])
        return
    try:
        target = _target(run, runs)
    except Miss as e:
        ui.err(str(e))
        if as_json:
            ui.emit({"run": None, "dry_run": dry_run, "steps": []})
        raise SystemExit(e.code)
    results = backup.undo(target["id"], dry_run=dry_run) if target and not target["undone"] else []
    bad = [r for r in results if not r["ok"]]
    if as_json:
        ui.emit({"run": target["id"] if target else None, "dry_run": dry_run, "steps": results})
    elif not results:
        ui.ok("every setup run is already taken back" if target is None else f"{target['id']} is already taken back")
    else:
        ui.table(["step", "action", "ok", "detail"],
                 [[r["step"], r["action"], "yes" if r["ok"] else "no", r["detail"]] for r in results])
        if bad:
            ui.err(f"{len(bad)} step(s) could not be taken back")
        elif dry_run:
            ui.hint(cmd(f"setup undo {target['id']}"))
        else:
            ui.ok(f"took {target['id']} back")
    raise SystemExit(1 if bad and not dry_run else 0)
