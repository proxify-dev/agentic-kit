"""The CLI's output contract (plugins/AGENTS.md): one door, three modes.

  plain  what a pipe or an agent gets — no box-drawing, no ANSI, no line folded
         through a path; `key: value` blocks and header-then-TSV tables
  json   `--json` on a read verb is one parseable document, exit 0
  rich   a terminal (or AK_RICH=1) gets colour
  help   grouped sections when piped, no boxes; the same commands in every mode

Every verb here runs as a subprocess against a throwaway vault, the way an agent
would call it, so the assertions are on the bytes that actually leave the process.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ak._brand import CLI, REPO_DIR, env_name

PLUGIN = Path(__file__).resolve().parent.parent

# These cases drive the sibling plugins' verbs (resolve, sessions, trace, the observer's band rows):
# ak copied alone (the public kit, Phase 2's standalone proof) has none of them to drive.
_ABSENT = [p for p in ("vault", "memory", "observer", "tracer") if not (PLUGIN.parent / p / "src").is_dir()]
if _ABSENT:
    pytest.skip(f"drives the sibling plugins' verbs; not beside ak: {', '.join(_ABSENT)}", allow_module_level=True)


def _fake_traces():
    """The tracer's fake tool-call engine (stdlib, by path): `ak trace` is the tracer's `calls`, hoisted."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("fake_traces", PLUGIN.parent / "tracer" / "tests" / "fake_traces.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fake_engine = _fake_traces().fake_engine
BOX = re.compile(r"[╭╮╰╯│─┃━┏┓┗┛]")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def note(path: Path, body: str = "", **fm) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + "\n".join(f"{k}: {v}" for k, v in fm.items())
                    + f"\n---\n\n# {path.stem}\n{body}\n", encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path):
    """A small vault: two linked project notes, a very long path to fold, an item."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "vault-manifest.json").write_text(json.dumps({
        "note_types": {"project": {"home": "projects/<slug>.md", "required": ["type", "status"]},
                       "item": {"home": "items/<slug>.md", "required": ["type"]}},
        "data_assets": {"patterns": ["memory/observations/**"]}}), encoding="utf-8")
    note(root / "projects" / "alpha.md", "[[beta]] [[items-one]] [[missing-note]]", type="project", status="active")
    note(root / "projects" / "beta.md", "[[alpha]]", type="project", status="paused")
    note(root / "items" / "a-very-long-item-name-that-would-fold-in-an-eighty-column-console-if-rich-wrapped-it.md",
         type="item", project="alpha")
    fake_engine(tmp_path / "code")  # `ak trace` reads through it ($AK_CODE/plugins/tracer/traces): no node, no traces.db
    return root


FAKE_TOOL = """#!/bin/sh
case "$*" in
  "--version") echo "{version}" ;;
  "plugin list --json"|"plugin marketplace list --json") echo "[]" ;;
esac
exit 0
"""


def _setup_env(vault: Path) -> dict:
    """`ak setup` detects the machine: a tmp HOME and fake claude/uv/git answering the probes, so these
    cases never reach the real ones (nor write a run folder anywhere but the tmp kit)."""
    bin_ = vault.parent / "fakebin"
    bin_.mkdir(exist_ok=True)
    for name, version in (("claude", "2.1.286 (Claude Code)"), ("uv", "uv 0.7.20"), ("git", "git version 2.50.1")):
        (bin_ / name).write_text(FAKE_TOOL.format(version=version))
        (bin_ / name).chmod(0o755)
    (vault.parent / "home").mkdir(exist_ok=True)
    return {"PATH": f"{bin_}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(vault.parent / "home"),
            env_name("HOME"): str(vault.parent / "kit")}


def run(vault: Path, *args: str, env: dict | None = None, columns: str = "80"):
    base = {**os.environ, env_name("PATH"): str(vault), "COLUMNS": columns,
            "PYTHONPATH": str(PLUGIN / "src"), env_name("CODE"): str(vault.parent / "code")}
    if args[:1] == ("setup",):
        base.update(_setup_env(vault))
    for k in (env_name("OUTPUT"), env_name("PLAIN"), env_name("RICH"), "NO_COLOR", "CLAUDECODE"):
        base.pop(k, None)
    base.update(env or {})
    return subprocess.run([sys.executable, "-c", "from ak.cli import main; main()", *args],
                          capture_output=True, text=True, env=base, cwd=str(vault))


PLAIN = {env_name("OUTPUT"): "plain"}
RICH = {env_name("OUTPUT"): "rich"}


# --- mode detection ---------------------------------------------------------------------
def test_a_pipe_is_plain_by_default(vault):
    r = run(vault, "resolve", "alpha")
    assert r.returncode == 0, r.stderr
    assert not ANSI.search(r.stdout) and not BOX.search(r.stdout)
    assert "path: " in r.stdout and "type: project" in r.stdout


def test_claudecode_forces_plain_even_when_rich_is_asked_by_tty_only(vault):
    r = run(vault, "resolve", "alpha", env={"CLAUDECODE": "1"})
    assert not ANSI.search(r.stdout) and "path: " in r.stdout


def test_brain_rich_forces_colour_through_a_pipe(vault):
    r = run(vault, "resolve", "alpha", env=RICH)
    assert ANSI.search(r.stdout), r.stdout


def test_a_mounted_verb_is_dressed_for_a_terminal_too(vault):
    """A plugin verb prints through the same door: colour for a terminal, none for a pipe —
    the same bytes for both is the bug (`ak tracer live` shipped with a bare click.echo)."""
    rich = run(vault, "sessions", "live", env=RICH)
    plain = run(vault, "sessions", "live", env=PLAIN)
    if "open session" not in plain.stdout or "no open sessions" in plain.stdout:
        pytest.skip("no Claude Code session is open on this machine")
    assert ANSI.search(rich.stdout), rich.stdout
    assert not ANSI.search(plain.stdout) and "hint: " in plain.stdout


def test_legacy_env_aliases_still_work(vault):
    assert not ANSI.search(run(vault, "resolve", "alpha", env={env_name("PLAIN"): "1"}).stdout)
    assert ANSI.search(run(vault, "resolve", "alpha", env={env_name("RICH"): "1"}).stdout)


# --- the plain grammar -------------------------------------------------------------------
@pytest.mark.parametrize("argv", [
    ("resolve", "alpha"), ("vault", "links", "alpha"), ("vault", "backlinks", "alpha"), ("vault", "links", "--all"),
    ("vault", "refs"), ("vault", "templates", "ls"), ("vault", "templates", "show", "project"),
    ("observer", "status"), ("observer",), ("setup", "status"), ("setup", "--dry-run"), ("workspaces",),
    ("--help",), ("vault", "place", "--help"), ("resolve", "--help"), ("search", "alpha"), ("search", "--help"),
    ("sessions", "live"), ("sessions",), ("sessions", "--help"), ("observer", "timeline", "1"), ("observer", "get", "1"),
    ("sessions", "turns", "--limit", "2"), ("sessions", "turns", "--stats"), ("sessions", "turns", "--help"),
    ("sessions", "agents", "--help"),
    ("memory",), ("memory", "search", "alpha"), ("memory", "shape"), ("memory", "wrap"), ("memory", "tidy"),
    ("memory", "--help"), ("memory", "new", "--help"), ("memory", "adopt", "--help"), ("observer", "patterns"),
    ("memory", "harvest"), ("memory", "fields"), ("observer", "fold", "--help"),
    ("observer", "forget", "--query", "alpha", "--reason", "a test"),
    ("memory", "demo", "--help"), ("memory", "demo", "start", "--help"),
    ("trace",), ("trace", "stats"), ("trace", "--help"),
    ("plugin",), ("plugin", "ls"), ("plugin", "log", "--help"), ("plugin", "inspect", "--help"),
    ("moves",), ("moves", "ls"), ("moves", "--help"), ("ws", "move", "--help"), ("doctor",), ("home",),
])
def test_plain_output_has_no_boxes_ansi_or_folded_lines(vault, argv):
    r = run(vault, *argv, env=PLAIN, columns="40")
    out = r.stdout + r.stderr
    assert not BOX.search(out), out
    assert not ANSI.search(out), out
    # nothing folded: every path in the fixture survives on one line
    assert "a-very-long-item-name-that-would-fold-in-an-eighty-column-console-if-rich-wrapped-it" \
        not in out or "if-rich-wrapped-it.md" in out


def test_kv_block_is_key_colon_value(vault):
    r = run(vault, "observer", "status", env=PLAIN)
    lines = [ln for ln in r.stdout.splitlines() if ln and not ln.startswith("==")]
    assert lines and all(re.match(r"^[\w -]+:", ln) or ln.startswith("  ") for ln in lines), r.stdout


def test_table_is_header_then_tsv(vault):
    r = run(vault, "vault", "templates", "ls", env=PLAIN)
    rows = [ln for ln in r.stdout.splitlines() if "\t" in ln]
    assert rows[0].split("\t") == ["type", "home", "required", "template"]
    assert any(row.startswith("project\t") for row in rows)


def test_status_lines_carry_their_prefix(vault):
    r = run(vault, "resolve", "nope", env=PLAIN)
    assert r.returncode == 1
    assert r.stderr.startswith("error: no match for 'nope'")
    assert "hint: rg -nil" in r.stdout


def test_ambiguity_exits_2_with_the_candidates(vault):
    note(vault / "items" / "alpha.md", type="item")
    r = run(vault, "resolve", "alpha", env=PLAIN)
    assert r.returncode == 2
    assert "warn: ambiguous" in r.stdout
    assert "projects/alpha.md" in r.stdout and "items/alpha.md" in r.stdout


def test_links_report_dangling_and_resolved(vault):
    r = run(vault, "vault", "links", "alpha", env=PLAIN)
    assert "[[beta]]  → " in r.stdout and "[[missing-note]]  DANGLING" in r.stdout


# --- json -------------------------------------------------------------------------------
@pytest.mark.parametrize("argv", [
    ("resolve", "alpha"), ("vault", "links", "alpha"), ("vault", "backlinks", "alpha"), ("vault", "links", "--all"),
    ("vault", "refs"), ("vault", "templates", "ls"), ("vault", "templates", "show", "project"),
    ("observer", "status"), ("setup", "status"), ("setup", "--dry-run"), ("workspaces", "ls"), ("search", "alpha"),
    ("sessions", "live"), ("sessions",), ("observer", "timeline", "1"), ("observer", "get", "1"),
    ("trace",), ("trace", "stats"), ("memory", "fields"), ("plugin", "ls"), ("moves", "ls"), ("doctor",),
    ("observer", "forget", "--query", "alpha", "--reason", "a test"),
])
def test_json_is_one_document(vault, argv):
    r = run(vault, *argv, "--json")
    assert r.stdout.strip(), r.stderr
    json.loads(r.stdout)
    assert not ANSI.search(r.stdout)


def test_json_carries_the_same_facts_as_plain(vault):
    d = json.loads(run(vault, "resolve", "alpha", "--json").stdout)
    assert d["matches"][0]["rel"] == "projects/alpha.md"
    assert d["matches"][0]["status"] == "active"
    links = json.loads(run(vault, "links", "alpha", "--json").stdout)["links"]
    assert {l["link"]: l["state"] for l in links} == {"beta": "ok", "items-one": "dangling", "missing-note": "dangling"}


def test_json_miss_is_an_empty_list_not_a_crash(vault):
    r = run(vault, "resolve", "nope", "--json")
    assert r.returncode == 1
    assert json.loads(r.stdout) == {"query": "nope", "matches": []}


@pytest.mark.parametrize("extra", [(), ("--json",)])
def test_trace_get_miss_exits_1_with_an_error_line(vault, extra):
    """A tool_use_id the index does not hold: the engine's line on stderr, nothing on stdout."""
    r = run(vault, "trace", "get", "nope", *extra)
    assert r.returncode == 1
    assert r.stderr.startswith("error: ") and "nope" in r.stderr
    assert not r.stdout.strip().startswith("{")


def test_trace_is_dressed_for_a_terminal_and_plain_for_a_pipe(vault):
    """`ak trace` is the exact name, not a prefix of `tracer`, and prints through the one door."""
    rich, plain = run(vault, "trace", env=RICH, columns="160"), run(vault, "trace", env=PLAIN)
    assert rich.returncode == plain.returncode == 0, plain.stderr
    assert ANSI.search(rich.stdout), rich.stdout
    assert not ANSI.search(plain.stdout) and plain.stdout.startswith("time\tsession\ttool\t")
    assert "hint: tracer trace get " in plain.stdout  # a plugin's hints name its own command (plugins/AGENTS.md §6)


# --- help -------------------------------------------------------------------------------
def test_piped_help_is_grouped_and_boxless(vault):
    r = run(vault, "--help", env=PLAIN)
    for section in ("Everyday:", "Groups:", "Setup & state:", "Plumbing:", "Examples:"):
        assert section in r.stdout, r.stdout
    assert not BOX.search(r.stdout)
    assert "[b]" not in r.stdout and "[/]" not in r.stdout  # markup stripped, not shown


def test_rich_help_is_grouped_too(vault):
    r = run(vault, "--help", env=RICH, columns="120")
    plain = ANSI.sub("", r.stdout)
    for section in ("Everyday", "Groups", "Plumbing"):
        assert f"─ {section} ─" in plain, plain


def test_bare_brain_ends_with_the_status_band(vault):
    """The dashboard: help first, then what this shell is bound to — the switches
    with their state and the command that flips each — last, nearest the prompt."""
    r = run(vault, env=PLAIN)
    tail = r.stdout[r.stdout.index("== status =="):]
    for key in ("vault:", "observer:", "store:", "hooks:"):
        assert key in tail, tail
    assert r.stdout.index("Everyday:") < r.stdout.index("== status ==")


def test_plain_help_explains_arguments_and_shows_defaults(vault):
    r = run(vault, "resolve", "--help", env=PLAIN)
    assert "Arguments:\n  NAME  a note's name or [[wikilink]] stem" in r.stdout, r.stdout
    assert f"Examples:\n  {CLI} resolve ak-app" in r.stdout
    r = run(vault, "workspaces", "scan", "--help", env=PLAIN)
    assert "ROOT (optional)" in r.stdout and "[default: 4]" in r.stdout, r.stdout


def test_root_examples_name_real_commands():
    """`ak -h` once led with `ak search` and `ak replay`, verbs that had moved
    under observer/tracer: every example line must walk to a command that exists."""
    import click
    from ak import cli as m
    m._mount_plugin_clis(m.cli)
    for line in m.ROOT_EPILOG.splitlines():
        words = re.split(r"\s{2,}", line.strip())[0].split()  # the command, not the words explaining it
        if len(words) < 2 or words[0] != CLI or words[1].startswith("<"):
            continue
        cmd = m.cli
        for w in words[1:]:
            if not isinstance(cmd, click.Group) or w.startswith(("-", '"')):
                break
            assert w in cmd.commands, f"example names no command: {line.strip()}"
            cmd = cmd.commands[w]


# --- usage errors: what to type, not only what broke ------------------------------------
def test_a_bad_command_lists_what_there_is(vault):
    r = run(vault, "nonsense", env=PLAIN)
    assert r.returncode == 2
    lines = r.stderr.splitlines()
    assert lines[0] == f"error: {CLI} has no command 'nonsense'"
    assert "commands:" in lines and any(ln.startswith("  resolve ") for ln in lines)
    assert lines[-1] == f"hint: {CLI} -h"
    assert not BOX.search(r.stderr)


def test_an_option_missing_its_value_says_what_it_takes(vault):
    """click raises this one with no context, so it used to name no command at all."""
    r = run(vault, "vault", "check", "x.md", "--max-hops", env=PLAIN)
    assert r.returncode == 2
    lines = r.stderr.splitlines()
    assert lines[0] == "error: --max-hops needs a value"
    assert f"usage: {CLI} vault check [OPTIONS] ENTRYPOINT" in lines
    assert "--max-hops: a whole number" in lines
    assert f"try: {CLI} vault check x.md --max-hops 10" in lines
    assert lines[-1] == f"hint: {CLI} vault check -h"


def test_help_wins_even_where_a_value_was_expected(vault):
    """`--max-hops --help` means "what does --max-hops take", not "--max-hops is --help"."""
    r = run(vault, "vault", "check", "x.md", "--max-hops", "--help", env=PLAIN)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith(f"Usage: {CLI} vault check")


def test_a_mistyped_option_is_corrected(vault):
    r = run(vault, "vault", "links", "--al", env=PLAIN)
    assert f"error: {CLI} vault links has no option --al — did you mean --all?" in r.stderr
    assert f"try: {CLI} vault links --all" in r.stderr


def test_a_verb_typed_one_level_too_high_runs_from_where_it_lives(vault):
    """`place derive` only reads: typed one level too high, it runs, and says where it went."""
    r = run(vault, "derive", "x.md", env=PLAIN)
    assert f"→ {CLI} vault place derive x.md" in r.stderr, r.stderr
    assert "has no command" not in r.stderr


def test_a_writing_verb_typed_too_high_is_only_pointed_at(vault):
    """`moves finalize` writes (✎): typed one level too high it is named, never run on a guess."""
    r = run(vault, "finalize", "x", env=PLAIN)
    assert r.returncode == 2
    assert f"it lives at '{CLI} moves finalize'" in r.stderr and "does not run on a guess" in r.stderr, r.stderr
    assert f"try: {CLI} moves finalize x" in r.stderr


@pytest.mark.parametrize("argv", [
    ("tracer", "live"), ("tracer", "sessions"), ("tracer", "--help"), ("memory", "find", "alpha"),
    ("links", "alpha"), ("backlinks", "alpha"), ("refs",), ("verify", "--help"), ("vault", "verify", "--help"),
    ("place", "--help"), ("templates", "list"), ("mdmap", "--help"),
    ("plugin", "list"), ("moves", "list"),
])
def test_a_released_spelling_still_runs_and_no_list_shows_it(vault, argv):
    """Every name the redesign moved keeps answering — scripts, notes and open sessions say them —
    while the help shows only the new home."""
    r = run(vault, *argv, env=PLAIN)
    assert "has no command" not in r.stderr and r.returncode in (0, 1), (argv, r.stdout, r.stderr)


def test_the_root_help_lists_only_the_new_homes(vault):
    out = run(vault, "--help", env=PLAIN).stdout
    names = {ln.split()[0] for ln in out.splitlines() if ln.startswith("  ") and ln.strip()}
    assert {"search", "sessions", "trace", "resolve", "memory", "observer", "vault", "setup"} <= names, out
    assert not names & {"tracer", "links", "backlinks", "refs", "verify", "place", "watch", "templates",
                        "mdmap"}, out


def test_a_missing_argument_is_described(vault):
    r = run(vault, "vault", "backlinks", env=PLAIN)
    assert r.stderr.startswith(f"error: {CLI} vault backlinks needs NAME")
    assert "NAME: text\n  a note's name or [[wikilink]] stem" in r.stderr
    assert f"try: {CLI} vault backlinks <NAME>" in r.stderr


def test_an_unquoted_phrase_is_glued_back(vault):
    r = run(vault, "resolve", "alpha", "beta", env=PLAIN)
    assert "quote a phrase" in r.stderr
    assert f"try: {CLI} resolve 'alpha beta'" in r.stderr


def test_a_rich_error_is_a_short_block_not_a_box(vault):
    r = run(vault, "vault", "check", "x.md", "--max-hops", env=RICH, columns="120")
    err = ANSI.sub("", r.stderr)
    assert err.startswith("✗ --max-hops needs a value"), err
    assert f"try    {CLI} vault check x.md --max-hops 10" in err
    assert not BOX.search(err)


# --- run the checkout you stand in -------------------------------------------------------
def test_checkout_detection_walks_up_to_the_repo_marker(tmp_path):
    from ak.cli import _checkout_above
    repo = tmp_path / "wt"
    (repo / "plugins" / "ak" / "src" / "ak").mkdir(parents=True)
    (repo / "plugins" / "ak" / "src" / "ak" / "cli.py").write_text("")
    deep = repo / "app" / "electron" / "services"
    deep.mkdir(parents=True)
    assert _checkout_above(deep) == repo
    assert _checkout_above(tmp_path) is None


def test_gateway_runs_from_the_checkout_you_stand_in(tmp_path, monkeypatch):
    """`ak gateway` inside a worktree runs that worktree's gateway, as `ak` runs its
    plugin there; $AK_CODE still wins, and outside any checkout it is ~/agentic-kit's."""
    from ak.cli import _gateway_cli

    def tree(root):
        (root / "plugins" / "ak" / "src" / "ak").mkdir(parents=True)
        (root / "plugins" / "ak" / "src" / "ak" / "cli.py").write_text("")
        (root / "gateway" / "src").mkdir(parents=True)
        (root / "gateway" / "src" / "cli.ts").write_text("")
        return root

    home, wt, pinned = tmp_path / "home", tree(tmp_path / "wt"), tree(tmp_path / "pinned")
    tree(home / REPO_DIR)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(env_name("CODE"), raising=False)
    monkeypatch.chdir(wt / "gateway")
    assert _gateway_cli() == wt.resolve() / "gateway" / "src" / "cli.ts"
    monkeypatch.setenv(env_name("CODE"), str(pinned))
    assert _gateway_cli() == pinned / "gateway" / "src" / "cli.ts"
    monkeypatch.delenv(env_name("CODE"))
    monkeypatch.chdir(tmp_path)
    assert _gateway_cli() == home / REPO_DIR / "gateway" / "src" / "cli.ts"


def test_gateway_runs_from_the_release_setup_installed_when_there_is_no_checkout(tmp_path, monkeypatch):
    """The public kit carries no gateway/ folder: `ak setup` installs cc-gateway under <data dir>/ak/cc-gateway.
    `ak gateway` on a fresh machine said "no gateway/src/cli.ts at ~/agentic-kit" (seen in the e2e box)."""
    from ak.cli import _gateway_cli
    data = tmp_path / "data"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.delenv(env_name("CODE"), raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _gateway_cli()
    release = data / "ak" / "cc-gateway" / "0.1.0" / "src"
    release.mkdir(parents=True)
    (release / "cli.ts").write_text("")
    (data / "ak" / "cc-gateway" / "current").symlink_to("0.1.0")
    assert _gateway_cli() == data / "ak" / "cc-gateway" / "current" / "src" / "cli.ts"


@pytest.mark.skipif(shutil.which("node") is None, reason="xray is TypeScript run by node")
def test_xray_hands_the_claude_command_over_untouched(vault, tmp_path):
    """Everything from `claude` on is the launch, so click parses none of it: not `-p`,
    not `-h`, not a later `--` — and xray's own flags before it pass through too."""
    stub = tmp_path / "co" / "gateway" / "src" / "xray.ts"
    stub.parent.mkdir(parents=True)
    stub.write_text("console.log(JSON.stringify(process.argv.slice(2)))\n")
    argv = ["-o", "base", "--part", "tools", "claude", "-p", "hi", "-h", "--model", "opus", "--", "-x"]
    r = run(vault, "xray", *argv, env={env_name("CODE"): str(tmp_path / "co")})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == argv
    bare = run(vault, "xray", env={env_name("CODE"): str(tmp_path / "co")})
    assert json.loads(bare.stdout) == ["--help"], f"bare `{CLI} xray` is its usage"


def test_no_reexec_from_outside_a_checkout_or_when_pinned(vault):
    """Both must run the installed code in place: a vault dir is not a checkout, and
    AK_NO_REEXEC pins it even inside one. (The hand-off itself is exercised by
    hand: `cd ~/agentic-kit && uv run --project <worktree>/plugins/ak ak --help`.)"""
    assert run(vault, "resolve", "alpha", env=PLAIN).returncode == 0
    assert run(vault, "resolve", "alpha", env={**PLAIN, env_name("NO_REEXEC"): "1"}).returncode == 0


def test_the_cli_answers_to_ak_only(vault):
    """`ak` is the one console script and launcher; help speaks it whatever argv[0] was."""
    import tomllib
    scripts = tomllib.loads((PLUGIN / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts == {CLI: "ak.cli:main"}
    assert (PLUGIN / "bin" / CLI).stat().st_mode & 0o111, "bin/<cli> is the plugin's launcher, executable"
    assert not (PLUGIN / "bin" / "brain").exists()  # brand: historical: the old launcher is gone
    r = subprocess.run([sys.executable, "-c", "import sys; sys.argv[0] = '/x/other'; "
                        "from ak.cli import main; main()", "--help"], capture_output=True, text=True,
                       env={**os.environ, **PLAIN, env_name("NO_REEXEC"): "1", env_name("PATH"): str(vault),
                            "PYTHONPATH": str(PLUGIN / "src")}, cwd=str(vault))
    assert r.stdout.startswith(f"Usage: {CLI} "), r.stdout[:200]


def test_help_first_lines_are_short_and_not_truncated(vault):
    """Every verb's first help line fits the catalogue; a `...` means it was cut."""
    r = run(vault, "--help", env=PLAIN)
    catalogue = [ln for ln in r.stdout.splitlines() if ln.startswith("  ") and "  " in ln.strip()]
    long = [ln for ln in catalogue if ln.rstrip().endswith("...")]
    assert not long, "\n".join(long)


@pytest.mark.parametrize("argv", [("memory",), ("memory", "search", "alpha"), ("memory", "shape"),
                                  ("memory", "wrap"), ("memory", "tidy"), ("memory", "harvest"), ("observer", "patterns")])
def test_memory_read_verbs_emit_one_json_document(vault, argv):
    r = run(vault, *argv, "--json")
    assert r.returncode in (0, 1), r.stderr
    json.loads(r.stdout)
