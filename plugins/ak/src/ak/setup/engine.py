"""plan → apply → verify: the one engine both doors (plain/json and the TUI) drive.

A component already in place makes no step, so a second run is a repair, not a reinstall.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from ak._brand import CLI, claude_home, cmd, config_dir, data_dir, env_name, is_windows, spawn_detached, which_exe
from ak.move.doctor import _f
from ak.setup import catalog, claude_cli, settings, source
from ak.setup.backup import Run
from ak.setup.machine import Machine


@dataclass
class Step:
    id: str                         # "clone", "marketplace", "plugin:ak", "ak-tool", "settings", "shim", ...
    component: str                  # catalog id it serves
    title: str                      # one line a person reads: "install ak@ak"
    kind: str                       # "exec" | "clone" | "settings" | "login"
    argv: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    touches: list[str] = field(default_factory=list)  # files snapshotted before it runs
    preview: str = ""               # the diff (settings) or the command line, shown before apply
    undo: dict = field(default_factory=dict)          # see backup.py
    long: bool = False              # more than a few seconds (a download, a venv build)
    interactive: bool = False       # needs the terminal (a browser login)
    background: bool = False        # a plugin's first run: started on its own, so it keeps going after setup ends
    edits: list[settings.SettingsEdit] = field(default_factory=list)  # a settings step: the keys it sets


@dataclass
class Plan:
    selected: list[str]             # catalog ids, dependency order
    steps: list[Step] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # id -> why ("already installed", a block reason)


# apply() reports progress through emit(event, **data):
#   emit("step_start", step=Step)
#   emit("line", step=Step, text=str)            one line of child output
#   emit("step_done", step=Step, status="done"|"failed"|"skipped"|"background", detail=str)
#     "background": a background step still running when setup stopped following it (LET_GO, Ctrl-C)
Emit = Callable[..., None]

# Set by the TUI when the person leaves while a background step runs: setup stops following it, and the
# step (started in its own session) keeps going.
LET_GO = threading.Event()

# A step with interactive=True needs the live terminal (a browser login). apply() hands it to
# run_interactive(step, runner): runner() runs the child attached to the terminal and returns its exit
# code. The TUI wraps that in app.suspend(); None (the plain door) just calls runner().
RunInteractive = Callable[[Step, Callable[[], int]], int]

IN_PLACE = "already in place"
# The probe of Machine whose answer a component's state is read from; when it failed, the component is left alone.
PROBE_OF = {"release": "release", "marketplace": "marketplaces", "ak-tool": "uv_tools", "shim": "shim", "gateway": "gateway"}
SHIM_DIR = "cc-shim"  # the folder the shim installs itself into, under the data dir (shim/cc-shim.mjs NAME)
# The rc files `uv tool update-shell` may append to, and the ones the shim wires (shim/cc-shim.mjs SH_RCS, ALL_RCS).
UV_RCS = (".zshenv", ".zshrc", ".bashrc", ".bash_profile", ".bash_login", ".profile")  # portable: ok (shell rc names)
SHIM_RCS = (".zshenv", ".profile", ".bashrc", ".bash_profile", ".zshrc")  # portable: ok (shell rc names)
# Steps run in this order whatever order the components came in; ties keep catalog order.
STEP_ORDER = ("release", "marketplace", "plugin:", "ak-tool", "ak-path", "venv:", "settings", "shim", "gateway:", "first-run:")


class Refusal(Exception):
    """A component that can't be put in place here, and why — a sentence a person reads."""


def _exe(m: Machine, name: str) -> str:
    return m.bin(name).path or name


def _home_file(name: str) -> str:
    return str(Path.home() / name)


def _same_place(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _release_path(m: Machine) -> str:
    return m.release.path if m.release else str(source.release_dir())


def _line(argv: list[str]) -> str:
    return shlex.join(argv)


def _exec(id: str, component: str, title: str, argv: list[str], **kw) -> Step:
    return Step(id, component, title, "exec", argv, preview=_line(argv), **kw)


def _from_call(id: str, component: str, title: str, call: claude_cli.Call, undo: dict) -> Step:
    return Step(id, component, title, "exec", call.argv, call.env, call.cwd, preview=_line(call.argv), undo=undo)


def _rc_files(names: tuple[str, ...]) -> list[str]:
    return [_home_file(n) for n in names]


# ── one maker per component: its steps, [] when it is in place, Refusal when it can't be ──────────────
def _release(m: Machine, rel: str, repo: str | None) -> list[Step]:
    if m.release and m.release.exists:
        return []
    if os.path.isdir(rel) and os.listdir(rel):
        raise Refusal(f"{rel} exists and is not a clone of the kit; move it away or pick another {env_name('HOME')}")
    url = source.repo_url(repo)
    if not url:
        raise Refusal(f"no repo URL: pass --repo or set {env_name('REPO_URL')}")
    argv = [_exe(m, "git"), "clone", url, rel]
    return [Step("release", "release", f"clone the kit from {url}", "clone", argv, preview=_line(argv),
                 undo={"kind": "clone", "path": rel})]


def _marketplace(m: Machine, rel: str, repo: str | None) -> list[Step]:
    market = catalog.MARKETPLACE
    here = m.marketplaces.get(market)
    if here is not None:
        if _same_place(here, rel):
            return []
        raise Refusal(f"the {market} marketplace points at {here}, not at the release clone {rel}; "
                      f"setup never re-points it (claude plugin marketplace remove {market}, then run setup again)")
    exe = m.bin("claude").path
    return [_from_call("marketplace", "marketplace", f"add the {market} marketplace", claude_cli.marketplace_add(rel, exe),
                       claude_cli.marketplace_remove(market, exe).undo())]


def _plugin(c: catalog.Component, m: Machine) -> list[Step]:
    pid = catalog.plugin_id(c)
    if pid in m.plugins:  # installed, enabled or not: a disabled one is the person's choice (verify says so)
        return []
    exe = m.bin("claude").path
    return [_from_call(f"plugin:{c.plugin}", c.id, f"install {pid}", claude_cli.install(pid, exe),
                       claude_cli.uninstall(pid, exe).undo())]


def _venv_warm(c: catalog.Component, m: Machine, rel: str) -> Step:
    argv = [_exe(m, "uv"), "sync", "--frozen", "--project", os.path.join(rel, "plugins", c.plugin)]
    return _exec(f"venv:{c.id}", c.id, f"build the {c.id} environment", argv, long=True,
                 undo={"kind": "none", "note": f"the {c.id} environment is a cache; the plugin builds it on demand"})


def _size(n: int) -> str:
    """1536 → "1.5 KB", 7_340_032_000 → "6.8 GB": a size a person reads."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" or n >= 100 else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} GB"


def _first_run(c: catalog.Component, m: Machine, rel: str) -> Step | None:
    """The plugin's first run (its setup block), run from its folder in the release clone; None when there is nothing
    for it to read yet (no Claude Code history on this machine)."""
    fr = c.first_run
    title = fr.title
    if fr.reads == "history":
        if not m.history or not m.history[1]:
            return None
        size, sessions = m.history
        title += f" ({_size(size)} · {sessions:,} session{'s' * (sessions != 1)})"
    folder = os.path.join(rel, "plugins", c.plugin)
    exe = os.path.join(folder, *PurePosixPath(fr.run[0]).parts)  # plugin.json says it the POSIX way
    if is_windows() and os.path.isfile(exe + ".cmd"):  # a plugin's bin/ launcher is a .cmd beside the sh one there
        exe += ".cmd"
    argv = [exe, *fr.run[1:]]
    return Step(f"first-run:{c.id}", c.id, title, "exec", argv, cwd=folder, preview=_line(argv), background=True,
                undo={"kind": "none", "note": f"what {c.id}'s first run wrote is its own store, rebuilt from your files"})


def _ak_tool(m: Machine, rel: str, repo: str | None) -> list[Step]:
    if source.DIST in m.uv_tools:
        return []
    others = sorted(t for t, exes in m.uv_tool_exes.items() if any(e.removesuffix(".exe") == CLI for e in exes))
    if others:  # install-global.py uninstalls them, and an undo could not bring them back
        raise Refusal(f"the uv tool {', '.join(others)} already provides {CLI}, and installing the kit's would remove it; "
                      f"remove it yourself ({' && '.join('uv tool uninstall ' + t for t in others)}) and run setup again")
    host = os.path.join(rel, "plugins", "ak")
    env = {env_name("SRC"): host,
           "CLAUDE_PLUGIN_DATA": os.path.join(str(claude_home()), "plugins", "data", f"ak-{catalog.MARKETPLACE}")}
    argv = [_exe(m, "uv"), "run", "--no-project", "--python", ">=3.10", os.path.join(host, "hooks", "install-global.py")]
    return [_exec("ak-tool", "ak-tool", f"install the {CLI} command", argv, env=env, long=True,
                  undo={"kind": "exec", "argv": [_exe(m, "uv"), "tool", "uninstall", source.DIST]})]


def _ak_path(m: Machine, rel: str, repo: str | None) -> list[Step]:
    if m.local_bin_on_path:
        return []
    files = _rc_files(UV_RCS) + [os.path.join(str(config_dir()), "fish", "conf.d", "uv.env.fish")]
    if os.environ.get("ZDOTDIR"):
        files.append(os.path.join(os.environ["ZDOTDIR"], ".zshenv"))
    return [_exec("ak-path", "ak-path", "put uv's tool folder on your PATH", [_exe(m, "uv"), "tool", "update-shell"],
                  touches=files, undo={"kind": "none", "note": "the PATH lines uv wrote are taken out file by file"})]


# A module's installer, fetched at its pinned tag the way its own README says: through gh when it is logged in (the
# repo may be private), else curl. sh -c FETCH_AND_RUN _ <repo> <tag> <path in the repo> <its arguments…>
FETCH_AND_RUN = (
    'set -eu; f=$(mktemp); trap \'rm -f "$f"\' EXIT; '
    'if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; '
    'then gh api -H "Accept: application/vnd.github.raw" "repos/$1/contents/$3?ref=$2" >"$f"; '
    'else curl -fsSL -o "$f" "https://raw.githubusercontent.com/$1/$2/$3"; fi; '
    'shift 3; sh "$f" "$@"')


def _module_argv(m: Machine, mod: catalog.Module, *args: str) -> list[str]:
    return [_exe(m, "sh"), "-c", FETCH_AND_RUN, "install", mod.repo, f"v{mod.version}", mod.install, *args]


def _module_step(id: str, cid: str, title: str, m: Machine, mod: catalog.Module, **kw) -> Step:
    """Run the module's installer at its version; the undo runs the same installer with --uninstall."""
    step = _exec(id, cid, title, _module_argv(m, mod, "--version", mod.version),
                 undo={"kind": "exec", "argv": _module_argv(m, mod, "--uninstall")}, **kw)
    step.preview = f"sh {mod.install} --version {mod.version}   # {mod.repo} at v{mod.version}, fetched through gh (logged in) or curl"
    return step


def _gateway_launcher() -> str:
    """Where cc-gateway's install.sh writes its launcher: $CC_GATEWAY_BIN_DIR, else ~/.local/bin."""
    return os.path.join(os.environ.get("CC_GATEWAY_BIN_DIR") or str(Path.home() / ".local" / "bin"), "cc-gateway")


def _shim(m: Machine, rel: str, repo: str | None) -> list[Step]:
    legacy = bool(m.shim.get("legacy"))
    if m.shim.get("present") and not legacy:
        return []
    mod = catalog.get("shim").module
    files = [] if is_windows() else _rc_files(SHIM_RCS) + [
        os.path.join(str(config_dir()), "fish", "conf.d", "cc-shim.fish"),
        os.path.join(str(config_dir()), "environment.d", "cc-shim.conf")]
    step = _module_step("shim", "shim", "update the older bash claude shim to the node one" if legacy else "install the claude shim",
                        m, mod, touches=files)
    if legacy:  # cmdInstall copies over the old files and skips rc files that already carry the shim's line
        step.preview += "\n# replaces the files of the older bash shim in place; its rc lines and conf.d fragments stay"
        step.undo = {"kind": "none", "note": "the older bash shim was replaced; `cc-shim uninstall` would remove the wrapper altogether"}
    return [step]


def _gateway(m: Machine, rel: str, repo: str | None) -> list[Step]:
    if m.gateway.get("on"):
        return []
    launcher = _gateway_launcher()
    steps = []
    if not os.path.isfile(launcher):
        mod = catalog.get("gateway").module
        steps.append(_module_step("gateway:install", "gateway", "install the gateway", m, mod))
    if not m.gateway.get("accounts"):
        steps.append(Step("gateway:login", "gateway", "sign in to your Claude account, for the gateway", "login", [launcher, "account", "login"],
                          preview=_line([launcher, "account", "login"]), interactive=True,
                          undo={"kind": "none", "note": "the gateway account stays; remove it with cc-gateway account remove <name>"}))
    steps.append(_exec("gateway:enable", "gateway", "turn the gateway on", [launcher, "enable"],
                       undo={"kind": "exec", "argv": [launcher, "disable"]}))
    return steps


MAKERS = {"release": _release, "marketplace": _marketplace, "ak-tool": _ak_tool, "ak-path": _ak_path, "shim": _shim,
          "gateway": _gateway}


# ── settings: previewed as one diff ──────────────────────────────────────────────────────────────────
def _suggest_command(rel: str) -> dict:
    """What the vault's README tells a person to put in settings: the interpreter and the release's suggest script."""
    script = os.path.join(rel, "plugins", "vault", "bin", "suggest")
    quoted = f'"{script}"' if is_windows() and " " in script else shlex.quote(script)
    return {"type": "command", "command": f"{'python' if is_windows() else 'python3'} {quoted}"}


def _setting_value(c: catalog.Component, rel: str) -> object:
    return _suggest_command(rel) if c.settings_key == ("fileSuggestion",) else "1"


def _setting_held(m: Machine, keypath: tuple[str, ...]):
    node: object = m.settings
    for k in keypath:
        if not isinstance(node, dict) or k not in node:
            return settings.ABSENT
        node = node[k]
    return node


def _settings_step(wanted: list[catalog.Component], edits: list[settings.SettingsEdit], skipped: dict[str, str]) -> Step | None:
    try:
        sp = settings.plan(edits)
    except ValueError as e:
        skipped.update({c.id: str(e) for c in wanted})
        return None
    if sp.empty:
        skipped.update({c.id: IN_PLACE for c in wanted})
        return None
    keys = ", ".join(".".join(e.keypath) for e in edits)
    return Step("settings", wanted[0].id, f"set {keys} in settings.json", "settings", touches=[sp.path], preview=sp.diff, edits=edits)


# ── plan ─────────────────────────────────────────────────────────────────────────────────────────────
def _rank(step: Step) -> int:
    return next(i for i, prefix in enumerate(STEP_ORDER) if step.id.startswith(prefix))


def _probe_of(c: catalog.Component) -> str | None:
    return "plugins" if c.plugin else "settings" if c.settings_key else PROBE_OF.get(c.id)


def _in_place_note(c: catalog.Component, m: Machine) -> str:
    row = m.plugins.get(catalog.plugin_id(c)) if c.plugin else None
    if row and not row.get("enabled"):
        return f"installed but disabled; left alone ({'claude plugin enable ' + catalog.plugin_id(c)} to switch it on)"
    return IN_PLACE


def plan(m: Machine, ids: list[str], repo: str | None = None) -> Plan:
    """The steps that put ids (and what they need) in place on m. repo: what to clone from (else source.repo_url)."""
    ordered = catalog.with_deps(ids)
    p = Plan(ordered)
    rel = _release_path(m)
    refused: set[str] = set()
    setting_components: list[catalog.Component] = []
    edits: list[settings.SettingsEdit] = []
    for cid in ordered:
        c = catalog.get(cid)
        why = None if c.selectable else (c.note or "not selectable")
        why = why or catalog.blocked_with_deps(c, m)
        why = why or next((f"needs {d}, which was skipped" for d in c.depends_on if d in refused), None)
        if not why and _probe_of(c) in m.unknown:
            why = f"could not read {_probe_of(c)}; not touching it"
        if not why and c.settings_key:
            held, want = _setting_held(m, c.settings_key), _setting_value(c, rel)
            if held is settings.ABSENT:
                setting_components.append(c)
                edits.append(settings.SettingsEdit(c.settings_key, want))
            elif settings.same_json(held, want):
                p.skipped[cid] = IN_PLACE
            else:
                p.skipped[cid] = f"{'.'.join(c.settings_key)} already holds {json.dumps(held)}; left alone"
            continue
        if not why:
            try:
                legacy_left = cid == "shim" and m.shim.get("legacy") and cid not in ids  # only there as a dependency
                if not (legacy_left or c.plugin or cid in MAKERS):
                    raise Refusal(f"setup has no steps for {cid} yet")
                made = [] if legacy_left else _plugin(c, m) if c.plugin else MAKERS[cid](m, rel, repo)
            except Refusal as e:
                why, made = str(e), []
            if not why:
                p.steps += made
                if not made:
                    p.skipped[cid] = _in_place_note(c, m)
                elif c.plugin in ("observer", "tracer"):
                    p.steps.append(_venv_warm(c, m, rel))
                if c.plugin == "observer" and made:
                    p.skipped["observer.embed"] = embed_note(m)
                if made and c.first_run:
                    first = _first_run(c, m, rel)
                    if first:
                        p.steps.append(first)
                    else:
                        p.skipped[f"{c.id}.first-run"] = "no Claude Code history yet: nothing to index"
                continue
        p.skipped[cid] = why
        refused.add(cid)
    if setting_components:
        step = _settings_step(setting_components, edits, p.skipped)
        if step:
            p.steps.append(step)
    p.steps.sort(key=_rank)
    return p


def embed_note(m: Machine) -> str:
    """Why the observer's dense-search embed is not a step: it is minutes of work, and not for every machine."""
    why = catalog.embed_blocked(m)
    return f"not run here: {why}" if why else f"run {cmd('observer store embed')} later (minutes)"


# ── apply ────────────────────────────────────────────────────────────────────────────────────────────
def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _file_undo(run: Run, path: str) -> dict | None:
    """What took back a step's change to one file: the lines it appended, or the bytes it started with. None when untouched."""
    snap = run.snapshot_of(path)
    if snap is None:
        return None
    before = b"" if snap["absent"] else run.backed_up(path)
    after = _bytes(path)
    if before is None or after == (None if snap["absent"] else before):
        return None
    if after is not None and after.startswith(before):
        try:
            lines = [l for l in after[len(before):].decode("utf-8").splitlines() if l.strip()]
        except UnicodeDecodeError:
            lines = []
        if lines:
            return {"kind": "text_lines", "file": path, "lines": lines}
    return {"kind": "restore", "file": path, "after_sha": None if after is None else _sha(after)}


def _child_env(step: Step) -> dict:
    return {**{k: v for k, v in os.environ.items() if k != "CLAUDECODE"}, **step.env}


def _argv(step: Step) -> list[str]:
    exe = step.argv[0]
    return [exe if os.path.isabs(exe) else which_exe(exe) or exe, *step.argv[1:]]


def _run_captured(step: Step, run: Run, emit: Emit) -> tuple[int, str]:
    """The child's output streamed line by line to emit and to the step's log. (exit code, last line)."""
    last: deque[str] = deque(maxlen=1)
    with open(run.log_path(step.id), "a", encoding="utf-8", newline="\n") as log:
        log.write(f"$ {command_line(step)}\n")
        try:
            child = subprocess.Popen(_argv(step), cwd=step.cwd, env=_child_env(step), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                     errors="replace")
        except OSError as e:
            log.write(f"{e}\n")
            return 127, str(e)
        try:
            for raw in child.stdout:
                text = raw.rstrip("\r\n")
                log.write(raw if raw.endswith("\n") else raw + "\n")
                emit("line", **{"step": step, "text": text})  # a literal text= reads as a text-mode subprocess to windows-review
                if text.strip():
                    last.append(text.strip())
            return child.wait(), last[0] if last else ""
        except BaseException:  # interrupted: the child must not outlive the step that started it
            child.kill()
            child.wait()
            raise


def _clone_into_place(step: Step, run: Run, emit: Emit) -> tuple[int, str]:
    """git clone into <dest>.partial, then rename: a clone that is killed half way never looks like the release."""
    dest = step.argv[-1]
    part = dest + ".partial"
    shutil.rmtree(part, ignore_errors=True)
    try:
        rc, tail = _run_captured(dataclasses.replace(step, argv=[*step.argv[:-1], part]), run, emit)
        if rc == 0:
            os.replace(part, dest)
        return rc, tail
    finally:
        shutil.rmtree(part, ignore_errors=True)


def _run_followed(step: Step, run: Run, emit: Emit) -> tuple[int | None, str]:
    """A background step: started in its own session with its output in the step's log, which is followed line by line
    until the step exits, or until LET_GO is set or Ctrl-C (setup stops following; the step keeps going).
    (exit code, last line); the code is None when setup let go of a step still running."""
    path = run.log_path(step.id)
    with open(path, "a", encoding="utf-8", newline="\n") as log:
        log.write(f"$ {command_line(step)}\n")
        start = log.tell()
    try:
        with open(path, "ab") as out:
            child = spawn_detached(_argv(step), cwd=step.cwd, env=_child_env(step), stdout=out, stderr=subprocess.STDOUT)
    except OSError as e:
        return 127, str(e)
    last, rest = "", ""
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as follow:
            follow.seek(start)
            while True:
                rc = child.poll()
                rest += follow.read()
                *lines, rest = rest.replace("\r\n", "\n").split("\n")
                for text in lines:
                    emit("line", **{"step": step, "text": text})
                    last = text.strip() or last
                if rc is not None:
                    if rest.strip():
                        emit("line", **{"step": step, "text": rest})
                        last = rest.strip()
                    return rc, last
                if LET_GO.is_set():
                    return None, last
                time.sleep(0.1)
    except KeyboardInterrupt:  # Ctrl-C at the plain door: stop watching, never stop the work
        return None, last


def _run_on_terminal(step: Step) -> int:
    try:
        return subprocess.call(_argv(step), cwd=step.cwd, env=_child_env(step))
    except OSError as e:
        print(f"{step.argv[0]}: {e}", file=sys.stderr)
        return 127


def command_line(step: Step) -> str:
    return " ".join([*(f"{k}={shlex.quote(v)}" for k, v in step.env.items()), _line(step.argv)])


def _do_settings(step: Step, run: Run) -> tuple[str, str, dict]:
    """(status, detail, undo): the keys set, with what the file held when they were. A key somebody set
    since the preview is left as it is, and the detail says so."""
    sp = settings.plan(step.edits)
    # plan() only made edits for keys that held nothing; one that holds something now was set since the preview
    taken = [{"keypath": c["keypath"], "found": c["before"]} for c in sp.changes if c["before"] is not settings.ABSENT]
    sp.changes = [c for c in sp.changes if c["before"] is settings.ABSENT]
    if sp.empty and not taken:
        return "skipped", "already set", {"kind": "none", "note": "nothing was changed"}
    after_sha = settings.apply(sp) if sp.changes else ""  # re-reads the file; each "before" is what it held just now
    detail = f"{len(sp.changes)} key(s) set"
    left = [f"{'.'.join(s['keypath'])} (now {json.dumps(s['found'])})" for s in [*taken, *sp.skipped]]
    if left:
        detail += "; left alone, set since the preview: " + ", ".join(left)
    undo = {"kind": "json_keys", "file": sp.path, "changes": sp.changes, "after_sha": after_sha} if sp.changes \
        else {"kind": "none", "note": "nothing was changed"}
    return ("done" if sp.changes else "skipped"), detail, undo


def _do_command(step: Step, run: Run, emit: Emit, run_interactive: RunInteractive | None) -> tuple[str, str, dict]:
    if step.interactive:
        runner = lambda: _run_on_terminal(step)  # noqa: E731
        rc, tail = (run_interactive(step, runner) if run_interactive else runner()), ""
    elif step.background:
        rc, tail = _run_followed(step, run, emit)
        if rc is None:
            return "background", "keeps going in the background", step.undo
    else:
        rc, tail = (_clone_into_place if step.kind == "clone" else _run_captured)(step, run, emit)
    if rc == 0:
        return "done", "", step.undo
    return "failed", f"exit {rc}" + (f": {tail}" if tail else ""), NOTHING_TO_TAKE_BACK


NOTHING_TO_TAKE_BACK = {"kind": "none", "note": "the step failed; nothing to take back"}


def _perform(step: Step, run: Run, emit: Emit, run_interactive: RunInteractive | None) -> tuple[str, str, dict]:
    """(status, detail, undo) of one step: its touches snapshotted, then run."""
    try:
        for path in step.touches:
            run.snapshot(path)
        return _do_settings(step, run) if step.kind == "settings" else _do_command(step, run, emit, run_interactive)
    except (OSError, ValueError) as e:
        return "failed", str(e), NOTHING_TO_TAKE_BACK


def _record(step: Step, run: Run, status: str, undo: dict) -> None:
    """The step's undo in run.json. A file a child wrote to is taken back by what changed in it (a settings
    step has its own keys), recorded first so it is undone last."""
    if step.kind != "settings" or status == "interrupted":
        for path in step.touches:
            changed = _file_undo(run, path)
            if changed:
                run.record(f"{step.id}:{os.path.basename(path)}", f"{step.title} ({path})", changed, status)
    run.record(step.id, step.title, undo, "done" if status == "skipped" else status)


def _stop_on_terminate(signum, frame):
    raise KeyboardInterrupt


@contextlib.contextmanager
def _terminate_is_interrupt():
    """SIGTERM stops a step the way Ctrl-C does, so its undo is still recorded. Main thread only."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    old = signal.signal(signal.SIGTERM, _stop_on_terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, old)


def apply(p: Plan, run: Run, emit: Emit, run_interactive: RunInteractive | None = None,
          stop_on_fail: bool = True) -> bool:
    """Run each step: snapshot its touches, run it, record its undo. True when every step succeeded.
    A step cut short (Ctrl-C, SIGTERM) is recorded as "interrupted" with the undo it would have had, then re-raised."""
    ok = True
    with _terminate_is_interrupt():
        for step in p.steps:
            emit("step_start", step=step)
            try:
                status, detail, undo = _perform(step, run, emit, run_interactive)
            except BaseException:
                _record(step, run, "interrupted", NOTHING_TO_TAKE_BACK if step.kind == "settings" else step.undo)
                emit("step_done", step=step, status="failed", detail="interrupted")
                raise
            _record(step, run, status, undo)
            emit("step_done", step=step, status=status, detail=detail)
            if status == "failed":
                ok = False
                if stop_on_fail:
                    break
    return ok


# ── verify ───────────────────────────────────────────────────────────────────────────────────────────
def _ak_works() -> tuple[str | None, str]:
    """(path of the command, its version line) — path None when it is not on PATH, the line empty when it won't run.
    The folder this ak runs from is passed over: under `uvx … ak setup` that is uvx's throwaway copy, not the one installed."""
    here = os.path.dirname(sys.executable)
    path = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not _same_place(d, here))
    exe = shutil.which(CLI, path=path)
    if not exe:
        return None, ""
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return exe, ""
    return exe, (out.stdout.strip().splitlines() or [""])[0] if out.returncode == 0 else ""


def _verify_plugin(c: catalog.Component, m: Machine) -> dict:
    pid = catalog.plugin_id(c)
    row = m.plugins.get(pid)
    if row and row.get("enabled"):
        return _f(c.id, "pass", f"{pid} is installed and enabled" + (f" ({row['version']})" if row.get("version") else ""))
    if row:
        return _f(c.id, "warn", f"{pid} is installed but disabled", f"claude plugin enable {pid}")
    return _f(c.id, "fail", f"{pid} is not installed", cmd(f"setup --only {c.id}"))


def _verify_settings(c: catalog.Component, m: Machine) -> dict:
    name = ".".join(c.settings_key)
    if _setting_held(m, c.settings_key) is not settings.ABSENT:
        return _f(c.id, "pass", f"settings.json has {name}")
    return _f(c.id, "fail", f"settings.json has no {name}", cmd(f"setup --only {c.id}"))


def _verify_component(c: catalog.Component, m: Machine) -> dict | None:
    rel = _release_path(m)
    if c.id == "release":
        if m.release and m.release.exists:
            return _f(c.id, "pass", f"the release clone is at {rel}" + (f" ({m.release.head})" if m.release.head else ""))
        return _f(c.id, "fail", f"no release clone at {rel}", cmd("setup --only release"))
    if c.id == "marketplace":
        market = catalog.MARKETPLACE
        here = m.marketplaces.get(market)
        if here is None:
            return _f(c.id, "fail", f"the {market} marketplace is not added", cmd("setup --only marketplace"))
        if not _same_place(here, rel):
            return _f(c.id, "fail", f"the {market} marketplace points at {here}, not at the release clone {rel}",
                      f"claude plugin marketplace remove {market}, then {cmd('setup --only marketplace')}")
        return _f(c.id, "pass", f"the {market} marketplace points at the release clone")
    if c.plugin:
        return _verify_plugin(c, m)
    if c.settings_key:
        return _verify_settings(c, m)
    if c.id == "ak-tool":
        exe, version = _ak_works()
        if exe and version:
            return _f(c.id, "pass", f"{CLI} runs ({version}) from {exe}")
        if exe:
            return _f(c.id, "fail", f"{exe} does not run", cmd("setup --only ak-tool"))
        if not m.local_bin_on_path:
            return _f(c.id, "info", f"{CLI} is installed — a new terminal finds it", "open a new terminal")
        return _f(c.id, "fail", f"{CLI} is not on PATH", cmd("setup --only ak-tool"))
    if c.id == "shim":
        if not m.shim.get("present"):
            return _f(c.id, "fail", "the claude shim is not installed", cmd("setup --only shim"))
        if m.shim.get("legacy"):
            return _f(c.id, "warn", f"the claude shim is an older bash one (it skips the gateway's fragment); {cmd('setup --only shim')} updates it",
                      cmd("setup --only shim"))
        if not m.shim.get("pathPos"):
            return _f(c.id, "info", "the claude shim is installed — it takes over in a new terminal", "open a new terminal")
        return _f(c.id, "pass", "the claude shim is installed and on PATH")
    if c.id == "gateway":
        if m.gateway.get("on"):
            return _f(c.id, "pass", "the gateway is on")
        if not m.gateway.get("accounts"):
            return _f(c.id, "fail", "the gateway is off and has no account", "cc-gateway account login, then cc-gateway enable")
        return _f(c.id, "fail", "the gateway is off", "cc-gateway enable")
    return None


def verify(m: Machine, ids: list[str], with_deps: bool = True) -> list[dict]:
    """Findings in doctor's shape: {"check", "level": pass|info|warn|fail, "what", "fix"}. with_deps off: only ids."""
    out = []
    for cid in catalog.with_deps(ids) if with_deps else catalog.order(ids):
        c = catalog.get(cid)
        why = None if c.selectable else (c.note or "not selectable")
        why = why or catalog.blocked_with_deps(c, m)
        if not why and _probe_of(c) in m.unknown:
            out.append(_f(cid, "warn", f"could not read {_probe_of(c)}; cannot tell whether {c.label} is in place", cmd("setup status")))
            continue
        found = _f(cid, "info", f"{c.label}: not installed here ({why})") if why else _verify_component(c, m)
        if found:
            out.append(found)
    return out
