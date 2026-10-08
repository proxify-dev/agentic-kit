"""`tracer trace` (old spelling `tracer calls`): the tool-call index through this plugin's engine, rendered through ui.

The engine (`traces/bin/traces`) is faked here: a script under a throwaway checkout
($AK_CODE/plugins/tracer/traces/bin/) that logs its argv and answers each verb from a
fixture file, so these tests need no node and no traces.db. What they pin is this side
of the contract — the flags handed over, each engine exit said in this CLI's voice,
`--json` equal to the engine's document, payloads capped, and purge kept a person's verb.

Every run is the tracer's own console script in a child process, with no `ak` anywhere: the
group prints through this plugin's copy of ak's door, so mounted inside `ak` it prints the same
lines (test_homes.py runs the host and compares).
"""
import json
import os
import pty
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from tracer._brand import CLI, HOME_DIR, env_name
from tracer.verbs import command

PLUGIN = Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;]*m")

from fake_traces import ERR, FIXTURES, ROW, fake_engine  # noqa: E402,F401


@pytest.fixture
def engine(tmp_path):
    vault = tmp_path / "code" / "vault"  # set up already, so `ak` plants nothing and says nothing
    vault.mkdir(parents=True)
    (vault / "vault-manifest.json").write_text("{}")
    return fake_engine(tmp_path / "code")


def child_env(tmp: Path, **extra) -> dict:
    """A child with the tracer and nothing of the kit's: no ak on its path, the plain grammar."""
    base = {**os.environ, env_name("OUTPUT"): "plain", "COLUMNS": "80"}
    for k in ("PYTHONPATH", env_name("PLAIN"), env_name("RICH"), env_name("CODE"), "NO_COLOR", "CLAUDECODE",
              "CLAUDE_PLUGIN_ROOT"):
        base.pop(k, None)
    base.update(extra)
    return base


def run(engine: Path, *args: str, env: dict | None = None, stdin=subprocess.DEVNULL, verb=("trace",)):
    code = engine.parents[3]
    base = child_env(code.parent, **{env_name("CODE"): str(code), env_name("PATH"): str(code / "vault")})
    base.update(env or {})
    return subprocess.run([sys.executable, "-c", "from tracer.cli import main; main()", *verb, *args],
                          capture_output=True, text=True, env=base, cwd=str(code), stdin=stdin)


def in_child(tmp: Path, snippet: str, **env) -> subprocess.CompletedProcess:
    """SNIPPET in a child with this plugin's src first on the path."""
    return subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r)\n" % str(PLUGIN / "src") + snippet],
                          capture_output=True, text=True, env=child_env(tmp, **env), cwd=str(tmp))


def calls(engine: Path) -> list[list[str]]:
    log = engine / "argv.jsonl"
    return [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []


# --- list ---------------------------------------------------------------------------------
def test_bare_trace_lists_through_the_engine_with_refresh(engine):
    r = run(engine)
    assert r.returncode == 0, r.stderr
    assert calls(engine) == [["list", "--limit", "30", "--refresh"]]
    lines = r.stdout.splitlines()
    assert lines[0].split("\t") == ["time", "session", "tool", "outcome", "took", "what", "came back", "id"]
    row = lines[1].split("\t")
    assert row[1:5] == ["6eced009", "claude.Bash", "ok", "1.2s"] and row[7] == ROW["id"]
    assert row[5:7] == ["Run the tracer tests", "19 passed"]  # the label, not the input JSON
    old = lines[2].split("\t")  # a row the index has not re-read: no label, its input line
    assert old[5] == "query: [b]not markup[/b]"  # text we did not write is not read as markup
    assert lines[-1] == f"hint: {command('trace')} get {ROW['id']}"


def test_filters_reach_the_engine_in_its_spelling(engine):
    r = run(engine, "--tool", "Bash", "--server", "claude", "--session", "6eced009", "--origin", "session",
            "--outcome", "error", "--q", "pytest", "--limit", "5", "--cursor", "c1")
    assert r.returncode == 0, r.stderr
    assert calls(engine)[0] == ["list", "--tool", "Bash", "--server", "claude", "--session", "6eced009",
                                "--origin", "session", "--outcome", "error", "--q", "pytest",
                                "--limit", "5", "--cursor", "c1", "--refresh"]


def _flag(argv, name):
    return argv[argv.index(name) + 1]


@pytest.mark.parametrize("given, back", [("30m", timedelta(minutes=30)), ("2h", timedelta(hours=2)),
                                         ("1d", timedelta(days=1)), ("7d", timedelta(days=7))])
def test_relative_since_becomes_iso_utc(engine, given, back):
    before = datetime.now(timezone.utc)
    assert run(engine, "--since", given, "--until", given).returncode == 0
    argv = calls(engine)[0]
    for name in ("--since", "--until"):
        v = _flag(argv, name)
        assert v.endswith("Z") and len(v) == len("2026-09-24T10:14:00.000Z"), v
        at = datetime.fromisoformat(v.replace("Z", "+00:00"))
        assert abs((before - back) - at) < timedelta(seconds=30)


def test_an_iso_instant_passes_as_utc_and_a_date_is_local_midnight(engine):
    assert run(engine, "--since", "2026-09-20T10:00:00+02:00", "--until", "2026-09-21").returncode == 0
    argv = calls(engine)[0]
    assert _flag(argv, "--since") == "2026-09-20T08:00:00.000Z"
    midnight = datetime(2026, 9, 21).astimezone().astimezone(timezone.utc)
    assert _flag(argv, "--until") == midnight.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def test_a_time_that_is_not_one_is_a_usage_error(engine):
    r = run(engine, "--since", "yesterday")
    assert r.returncode == 2
    assert "'yesterday' is not a time" in r.stderr
    assert calls(engine) == []


def test_a_full_page_ends_on_the_command_for_the_next(engine):
    (engine / "list.json").write_text(json.dumps({"rows": [ROW], "next": "2026-09-24T10:14:00.000Z|toolu_x"}))
    r = run(engine, "--tool", "Bash", "--limit", "1")
    assert r.stdout.splitlines()[-1] == \
        f"hint: {command('trace')} --tool Bash --limit 1 --cursor '2026-09-24T10:14:00.000Z|toolu_x'"


def test_an_empty_page_says_so(engine):
    (engine / "list.json").write_text(json.dumps({"rows": [], "next": None}))
    r = run(engine, "--tool", "Nope")
    assert r.returncode == 0
    assert "warn: no tool calls match" in r.stdout


def test_an_ambiguous_session_prefix_lists_the_candidates_and_exits_2(engine):
    cands = ["6eced009-7527-4775-892d-53ee98721e9f", "6eced009-0000-4775-892d-53ee98721e9f"]
    (engine / "list.json").write_text(json.dumps({"candidates": cands}))
    (engine / "list.exit").write_text("2")
    r = run(engine, "--session", "3826")
    assert r.returncode == 2
    assert "warn: ambiguous: that session prefix matches 2 sessions" in r.stdout
    assert all(c in r.stdout.splitlines() for c in cands)
    assert r.stdout.splitlines()[-1] == f"hint: {command('trace')} --session {cands[0]}"
    j = run(engine, "--session", "3826", "--json")
    assert j.returncode == 2 and json.loads(j.stdout) == {"candidates": cands}


def test_no_index_yet_is_an_error_with_the_way_out(engine):
    (engine / "list.json").unlink()
    (engine / "list.err").write_text(f"traces: no index yet at ~/{HOME_DIR}/db/traces")
    (engine / "list.exit").write_text("1")
    r = run(engine)
    assert r.returncode == 1
    assert r.stderr.strip() == f"error: no index yet at ~/{HOME_DIR}/db/traces"  # one name, not two
    assert f"hint: {command('trace')} index" in r.stdout


@pytest.mark.parametrize("mode", ["plain", "rich"])
def test_the_engine_line_is_text_not_markup(engine, mode):
    """A `[b]` in the engine's line is an id or a path, not a style: printed as written."""
    (engine / "stats.err").write_text("traces: no index at /tmp/[b]x[/b]")
    (engine / "stats.exit").write_text("1")
    r = run(engine, "stats", env={env_name("OUTPUT"): mode})
    assert r.returncode == 1
    assert "no index at /tmp/[b]x[/b]" in ANSI.sub("", r.stderr), r.stderr
    assert "traces:" not in r.stderr


# --- --json is the engine's document -----------------------------------------------------
@pytest.mark.parametrize("argv, key", [((), "list"), (("stats",), "stats"), (("index",), "index"),
                                       (("get", ROW["id"]), f"get-{ROW['id']}")])
def test_json_is_the_engine_document_unchanged(engine, argv, key):
    # `ak trace index` runs both indexes (its own test below); the tool-call one alone is `ak tracer calls index`
    r = run(engine, *argv, "--json", verb=("calls",) if argv == ("index",) else ("trace",))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == FIXTURES[key]


# --- get ----------------------------------------------------------------------------------
def test_get_shows_the_row_and_both_payloads_capped(engine):
    r = run(engine, "get", ROW["id"])
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert out.startswith("== claude.Bash ==\nid: toolu_01AAAA")
    assert "outcome: ok" in out and "session: 6eced009-7527-4775-892d-53ee98721e9f" in out
    assert "== input · 40 B ==\ncommand: uv run pytest -q\ndescription: tests\n" in out
    output = out[out.index("== output"):]
    body = output.split("\n", 1)[1].split("\n-- ")[0]
    assert len(body.encode()) <= 8 * 1024 + 1  # the cap, plus the line end
    assert "-- first 8.0 KB of 19.5 KB · --full shows all" in output
    assert f"hint: {command('trace')} get {ROW['id']} --full" in out
    assert out.splitlines()[-1] == f"hint: {command('trace')} --session {ROW['identity']['session']}"


def test_get_full_shows_every_byte(engine):
    r = run(engine, "get", ROW["id"], "--full")
    assert "line\n" * 4000 in r.stdout
    assert "--full shows all" not in r.stdout


def test_get_of_an_unknown_id_is_a_miss(engine):
    r = run(engine, "get", "nope")
    assert r.returncode == 1
    assert r.stderr.strip() == "error: no tool call nope"


def test_get_says_when_a_payload_is_gone(engine):
    detail = {**FIXTURES[f"get-{ROW['id']}"], "output": None}
    (engine / f"get-{ROW['id']}.json").write_text(json.dumps(detail))
    r = run(engine, "get", ROW["id"])
    assert "warn: no output on record" in r.stdout


# --- stats · index ----------------------------------------------------------------------------
def test_stats_is_counts_then_tools(engine):
    r = run(engine, "stats", "--server", "brain")  # brand: historical (the MCP server's name in a stored row)
    assert r.returncode == 0, r.stderr
    assert calls(engine) == [["stats", "--server", "brain"]]  # brand: historical (the MCP server's name in a stored row)
    assert "== tool-call index · brain ==" in r.stdout and "calls: 150000" in r.stdout  # brand: historical (the MCP server's name in a stored row)
    assert "tool\tcalls\nBash\t60000\nRead\t40000\nrecall\t12" in r.stdout
    assert r.stdout.splitlines()[-1] == f"hint: {command('trace')} --outcome error --since 1d"


def test_index_reports_what_it_took_in_or_that_another_is_running(engine):
    calls_only = ("calls",)  # the released spelling: the tool-call index alone, as it was
    r = run(engine, "index", "--recent", verb=calls_only)
    assert calls(engine) == [["index", "--recent"]]
    assert r.stdout.startswith("ok: indexed 3 files · 50.9 KB · 180ms")
    (engine / "index.json").write_text(json.dumps({**FIXTURES["index"], "locked": True}))
    assert run(engine, "index", verb=calls_only).stdout.startswith("ok: another index is running")


def test_trace_index_runs_the_transcript_index_then_the_calls_index(engine, tmp_path):
    """`tracer trace index`: both stores, one command — the session index first, then the tool calls."""
    home = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}  # the session index this test writes, not the machine's
    r = run(engine, "index", "--recent", env=home)
    assert r.returncode == 0, r.stderr
    assert calls(engine) == [["index", "--recent"]]
    said = r.stdout.splitlines()[:2]
    # the transcript pass is timed for real (0.0s here, 0.1s on a slow runner); the engine's 180ms is the fixture's
    assert re.fullmatch(r"ok: transcripts: 0 new interactions · \d+\.\ds", said[0]), said
    assert said[1] == "ok: tool calls: indexed 3 files · 50.9 KB · 180ms"
    assert (tmp_path / "claude" / "plugins" / "data" / "conversation-index" / "index.db").is_file()
    doc = json.loads(run(engine, "index", "--json", env=home).stdout)
    assert doc["calls"] == FIXTURES["index"] and doc["transcripts"]["interactions"] == 0


# --- the engine's own failures ---------------------------------------------------------------
@pytest.mark.parametrize("code, says", [(127, "error: no node ≥ 22.13"),
                                        (66, "error: the trace engine is not built: no ")])
def test_a_missing_node_or_bundle_is_named_and_keeps_its_exit(engine, code, says):
    (engine / "stats.exit").write_text(str(code))
    r = run(engine, "stats")
    assert r.returncode == code
    assert r.stderr.startswith(says), r.stderr
    if code == 66:
        assert "traces/dist/traces.mjs" in r.stderr


def test_an_engine_that_never_answers_is_stopped_and_said(tmp_path):
    exe = tmp_path / "traces" / "bin" / "traces"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\nexec sleep 30\n")
    exe.chmod(0o755)
    r = in_child(tmp_path, f"""
from pathlib import Path
from tracer import calls
calls._roots = lambda: [Path({str(tmp_path)!r})]
calls.TIMEOUT = 0.5
try:
    calls.engine("stats")
except SystemExit as e:
    print("exit", e.code)
""")
    assert r.stdout.strip().splitlines()[-1] == "exit 1", (r.stdout, r.stderr)
    assert "the trace engine gave no answer in 0.5 s and was stopped" in r.stderr


def test_no_launcher_anywhere_exits_66(tmp_path):
    r = in_child(tmp_path, f"""
from pathlib import Path
from tracer import calls
calls._roots = lambda: [None, Path({str(tmp_path)!r})]
try:
    calls.launcher()
except SystemExit as e:
    print("exit", e.code)
""")
    assert r.stdout.strip().splitlines()[-1] == "exit 66", (r.stdout, r.stderr)
    assert "no trace engine" in r.stderr


def test_the_engine_is_brain_code_s_then_this_plugin_s_own(tmp_path):
    pinned = tmp_path / "pinned"
    fake_engine(pinned)
    said = "from tracer import calls; print(calls.launcher())"
    r = in_child(tmp_path, said, **{env_name("CODE"): str(pinned)})
    assert r.stdout.strip() == str(pinned / "plugins" / "tracer" / "traces" / "bin" / "traces"), r.stderr
    r = in_child(tmp_path, said, **{env_name("CODE"): str(tmp_path / "nowhere")})
    assert r.stdout.strip() == str(PLUGIN.resolve() / "traces" / "bin" / "traces"), r.stderr


def test_the_node_entry_serves_without_the_shell_door(tmp_path):
    """No shell assumed (Windows), or a root with only traces/bin/traces.mjs: the entry runs through node,
    from the home directory (the store resolves from the cwd)."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("no node on PATH")
    root = tmp_path / "tracer"
    entry = root / "traces" / "bin" / "traces.mjs"
    entry.parent.mkdir(parents=True)
    entry.write_text('process.stdout.write(JSON.stringify({cwd: process.cwd(), argv: process.argv.slice(2)}))\n',
                     encoding="utf-8")
    fake_engine(tmp_path / "code")  # a root WITH the shell door: skipped on Windows
    shell_root = tmp_path / "code" / "plugins" / "tracer"
    home = tmp_path / "home"
    home.mkdir()
    r = in_child(tmp_path, f"""
import json
from pathlib import Path
from tracer import calls
calls._roots = lambda: [None, Path({str(root)!r})]
print(calls.launcher())
print(json.dumps(calls.engine("stats", "--server", "claude")))
calls.is_windows = lambda: True
calls._roots = lambda: [Path({str(shell_root)!r}), Path({str(root)!r})]
print(calls.launcher())
""", HOME=str(home), USERPROFILE=str(home))
    lines = r.stdout.strip().splitlines()
    assert lines[0] == str(entry), r.stderr
    assert json.loads(lines[1]) == {"cwd": str(home.resolve()), "argv": ["stats", "--server", "claude"]}
    assert lines[2] == str(entry), r.stderr


def test_trace_is_the_old_calls_spelling(engine):
    """One click object at two addresses (`calls`, hidden, is its released spelling): the same page from each."""
    a, b = run(engine, "--json"), run(engine, "--json", verb=("calls",))
    assert a.returncode == b.returncode == 0, (a.stderr, b.stderr)
    assert json.loads(a.stdout) == json.loads(b.stdout) == FIXTURES["list"]
    assert calls(engine) == [["list", "--limit", "30", "--refresh"]] * 2


def test_the_tracer_alone_is_whole_and_imports_no_ak(tmp_path):
    """No `ak` on the path: the two groups listed, every older spelling there unlisted, and no ak module loaded."""
    r = in_child(tmp_path, f"""
import json
from tracer.cli import cli
print(json.dumps({{"listed": sorted(n for n, c in cli.commands.items() if not c.hidden),
                  "hidden": sorted(n for n, c in cli.commands.items() if c.hidden),
                  "host": sorted(m for m in sys.modules if m == {CLI!r} or m.startswith({CLI + "."!r}))}}))
""")
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got["listed"] == ["sessions", "trace"]
    assert {"calls", "replay", "show", "grep", "blame", "index"} <= set(got["hidden"])
    assert got["host"] == []


# --- purge is a person's verb -----------------------------------------------------------------
@pytest.mark.parametrize("env", [{"CLAUDECODE": "1"}, {}])
def test_purge_refuses_an_agent_and_a_pipe(engine, env):
    """CLAUDECODE, or no terminal on stdin (DEVNULL here): refused before the engine runs."""
    r = run(engine, "purge", "--before", "90d", env=env)
    assert r.returncode == 1
    assert r.stderr.strip() == ("error: deleting trace history is a person's call — run it in your "
                                "own terminal, or use the app's Tools pane")
    assert calls(engine) == []


def _at_a_terminal(engine, *args, answer: bytes):
    """Run purge with a pty on stdin, as a person's terminal would give it."""
    master, slave = pty.openpty()
    try:
        os.write(master, answer)
        return run(engine, "purge", *args, stdin=slave)
    finally:
        os.close(slave)
        os.close(master)


def test_purge_at_a_terminal_counts_asks_then_deletes(engine):
    r = _at_a_terminal(engine, "--before", "2026-06-01", "--server", "GitHub", answer=b"y\n")
    assert r.returncode == 0, r.stderr
    argv = calls(engine)
    assert [a[-1] for a in argv] == ["--dry-run", "GitHub"]
    assert argv[1][:2] == ["purge", "--before"] and "--dry-run" not in argv[1]
    assert "Delete 3 tool calls from before 2026-06-01 00:00 on GitHub?" in r.stdout
    assert "ok: deleted 3 tool calls" in r.stdout


def test_purge_declined_deletes_nothing(engine):
    r = _at_a_terminal(engine, "--before", "90d", answer=b"n\n")
    assert r.returncode == 1
    assert [a[-1] for a in calls(engine)] == ["--dry-run"]
    assert "nothing deleted" in r.stdout


def test_purge_dry_run_only_counts(engine):
    r = _at_a_terminal(engine, "--before", "90d", "--dry-run", answer=b"")
    assert r.returncode == 0, r.stderr
    assert [a[-1] for a in calls(engine)] == ["--dry-run"]
    assert "3 tool calls from before" in r.stdout and "would be deleted" in r.stdout
