"""The tracer's two groups — `tracer sessions` (the conversations) and `tracer trace` (what was done) —
and every old spelling kept working, unlisted (2026-10 CLI redesign).

    tracer sessions  ls [--all] · live · show <id> [--turn N] [--open S] [--as-skill] · replay · turns · search (s)
    tracer trace     (tool calls) get · stats · purge ✎ · turn <turn-id> · blame · index ✎

The same click objects are mounted inside the `ak` host as `ak sessions` and `ak trace`, with the root
hidden as `ak tracer`. One resolver reads every session id: the full UUID, its first 8 characters, or
`latest`. The run tests go through the host when it is beside this plugin (plugins/ak) and through the
tracer's own console script when it is not: the same lines either way, which one test compares.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import asst, call, result, text, user, write_jsonl

from tracer import cli, db, drill, query, storage
from tracer._brand import CLI, env_name
from tracer.verbs import command
from tracer.db import get_write_connection

PLUGIN = Path(__file__).resolve().parent.parent
CORE_SRC = PLUGIN.parent / CLI / "src"  # the host plugin is named for its CLI
SID = "06a45a57-32b2-40ec-b5fd-0f8cca4c5cbc"
OLD = "11111111-0000-4000-8000-000000000000"


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """The argv each verb hands the engine, instead of running it; one session on disk (SID);
    an index of its own."""
    ran = []
    monkeypatch.setattr(cli, "_run_engine", ran.append)
    monkeypatch.setattr(drill, "match_session_ids", lambda p: [s for s in (SID, OLD) if s.startswith(p)])
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "index.db")
    return ran


def _run(*args):
    return CliRunner().invoke(cli.cli, list(args))


def _index(path: Path, rows):
    con = get_write_connection(path)
    con.executemany("INSERT INTO interactions (id, project_hash, session_id, timestamp) VALUES (?, 'p', ?, ?)",
                    [(f"{sid}-{n:03d}", sid, ts) for n, (sid, ts) in enumerate(rows)])
    con.commit()
    con.close()


# ── every old spelling still runs at the root, out of every list ────────────────────────────
OLD_VERBS = ["replay", "blame", "log", "grep", "live", "inspect", "show", "index", "calls"]


@pytest.mark.parametrize("verb", OLD_VERBS)
def test_every_old_tracer_verb_is_still_there(verb):
    r = _run(verb, "-h")
    assert r.exit_code == 0, r.output
    assert cli.cli.commands[verb].hidden, "an old spelling answers, unlisted"
    assert sorted(n for n, c in cli.cli.commands.items() if not c.hidden) == ["sessions", "trace"]


@pytest.mark.parametrize("argv, handed", [
    (["replay", "06a45a57"], ["dialogue", "--session", SID, "--all-projects"]),
    (["replay", "zvec lock"], ["search", "zvec lock", "--all-projects"]),
    (["blame", "src/x.py"], ["trace", "src/x.py", "--all-projects", "--limit", "20"]),
    (["log"], ["projects"]),
    (["grep", "wedge"], ["search", "wedge", "--all-projects", "--limit", "10"]),
    (["inspect", "06a45a57-004", "--open", "1-3"], ["query", "--drill", "06a45a57-004", "--step", "1-3"]),
    (["index", "--all"], ["index", "--all"]),
])
def test_old_spellings_hand_the_engine_what_they_always_did(engine, argv, handed):
    r = _run(*argv)
    assert r.exit_code == 0, r.output
    assert engine[-1] == handed


# ── one resolver: a UUID, 8 characters, latest ──────────────────────────────────────────────
def test_show_takes_an_8_character_prefix(engine):
    """`tracer show <prefix>` handed the prefix to an exact-match filter and said "No interactions matched"."""
    assert _run("show", "06a45a57").exit_code == 0
    assert engine[-1] == ["query", "--all-projects", "--limit", "20", "--session", SID]


def test_a_miss_says_what_it_looked_for(engine):
    r = _run("show", "deadbeef")
    assert r.exit_code == 1
    assert "error: no session starts with 'deadbeef': looked for " in r.output and "deadbeef*.jsonl" in r.output
    assert not engine, "nothing reaches the engine on a miss"


def test_one_floor_of_8_characters_everywhere(engine):
    with pytest.raises(drill.DrillError, match="its first 8 characters"):
        drill.resolve_session("3283f8")
    assert drill.resolve_session("06a45a57") == SID
    assert drill.split_target("06a45a57-004") == (SID, 4)


def test_an_ambiguous_prefix_lists_the_sessions(monkeypatch):
    monkeypatch.setattr(drill, "match_session_ids", lambda p: [SID, SID[:9] + "ffff" + SID[13:]])
    with pytest.raises(drill.AmbiguousSession, match="ambiguous"):
        drill.resolve_session("06a45a57")


def test_latest_is_the_newest_session_in_the_index(engine, tmp_path):
    _index(tmp_path / "index.db", [(OLD, "2026-09-01T10:00:00Z"), (SID, "2026-10-01T17:05:40Z"),
                                   (OLD, "2026-09-02T10:00:00Z")])
    assert drill.resolve_session("latest") == SID
    assert drill.split_target("latest-002") == (SID, 2)
    assert _run("show", "latest").exit_code == 0
    assert engine[-1][-1] == SID
    assert _run("replay", "latest").exit_code == 0
    assert engine[-1] == ["dialogue", "--session", SID, "--all-projects"]


def test_latest_with_nothing_on_record_says_so(engine, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty"))
    with pytest.raises(drill.DrillError, match="no session on record for `latest`"):
        drill.resolve_session("latest")


# ── bugs the redesign fixed ─────────────────────────────────────────────────────────────────
def test_the_staleness_hint_names_a_real_command(monkeypatch, tmp_path, capsys):
    """It said `ak index`, which never existed."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    write_jsonl(tmp_path / "projects" / "-tmp-proj" / f"{SID}.jsonl", [user("hi", 0)])
    con = get_write_connection(tmp_path / "index.db")
    query.staleness_warning("-tmp-proj", con)
    said = capsys.readouterr().err
    assert f"Run `{command('trace index')}` to refresh." in said
    assert f"`{command('index')}`" not in said


def test_sessions_refuses_a_folder_that_is_not_there(engine, tmp_path):
    """`ak tracer sessions ls` read `ls` as the project folder and listed nothing, exit 1."""
    r = _run("sessions", "ls", "nope-folder")
    assert r.exit_code == 64
    assert "no folder" in r.output and "/nope-folder" in r.output
    r = _run("sessions", "ls", str(tmp_path))  # a folder that is there, with no sessions: a miss, not a refusal
    assert r.exit_code == 1 and "no folder" not in r.output
    r = _run("sessions", "ls", "--", "-Users-x-gone")  # a session-dir name is not a folder path: never refused
    assert r.exit_code == 1 and "no folder" not in r.output


# ── `ak search`'s sessions rows ─────────────────────────────────────────────────────────────
def test_brain_search_returns_the_sessions_that_said_it(monkeypatch, tmp_path):
    d = tmp_path / "projects" / "-tmp-proj"
    write_jsonl(d / f"{SID}.jsonl", [user("why does the wedge stick?", 0), asst([text("the wedge sticks")], 1, "m1")])
    write_jsonl(d / f"{OLD}.jsonl", [user("nothing here", 0)])
    monkeypatch.setattr(storage, "discover_projects", lambda: [("-tmp-proj", d)])
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    rows = cli.brain_search("wedge", 5)
    assert [r["id"] for r in rows] == [SID[:8]]
    assert rows[0]["title"] == "why does the wedge stick?"
    assert rows[0]["when"] == rows[0]["when"][:10] and rows[0]["when"].startswith("20")
    assert cli.brain_search("nowhere-said", 5) == []


# ── run as a shell runs them: inside the ak host when it is here, else on their own ─────────
HOST = (CORE_SRC / CLI / "cli.py").is_file()
needs_host = pytest.mark.skipif(not HOST, reason="no ak host beside this plugin (plugins/ak)")
ENTRY = {CLI: f"from {CLI}.cli import main; main()", "tracer": "from tracer.cli import main; main()"}


def _env(tmp: Path, door: str, **extra) -> dict:
    """A child for DOOR: the ak host mounting only this plugin, or the tracer alone with nothing of the kit's."""
    base = {**os.environ, env_name("OUTPUT"): "plain", "COLUMNS": "100", "CLAUDE_CONFIG_DIR": str(tmp / "claude")}
    for k in ("PYTHONPATH", env_name("PLAIN"), env_name("RICH"), env_name("CODE"), "NO_COLOR", "CLAUDECODE",
              "CLAUDE_PLUGIN_ROOT"):
        base.pop(k, None)
    if door == CLI:
        plugins = tmp / "mounted"
        if not plugins.exists():
            plugins.mkdir()
            (plugins / "tracer").symlink_to(PLUGIN)
        vault = tmp / "vault"  # set up already, so `ak` plants nothing and says nothing
        vault.mkdir(exist_ok=True)
        (vault / "vault-manifest.json").write_text("{}")
        base.update({env_name("PLUGINS_DIR"): str(plugins), env_name("NO_REEXEC"): "1",
                     env_name("PATH"): str(vault), "PYTHONPATH": str(CORE_SRC)})
    base.update(extra)
    return base


def _cli(tmp: Path, *args: str, door: str | None = None, **extra) -> subprocess.CompletedProcess:
    """ARGS after the door (`sessions show x`): through the host when it is here, else the tracer's own script."""
    door = door or (CLI if HOST else "tracer")
    return subprocess.run([sys.executable, "-c", ENTRY[door], *args],
                          capture_output=True, text=True, env=_env(tmp, door, **extra), cwd=str(tmp))


def test_the_new_homes_are_the_old_verbs():
    from tracer import calls

    s, t, old = cli.sessions_group, cli.trace_group, cli.cli.commands
    assert s.commands["live"].callback is old["live"].callback
    assert s.commands["replay"].callback is old["replay"].callback
    assert s.commands["search"].callback is old["grep"].callback and "s" in s._alias_mapping
    assert t.commands["blame"].callback is old["blame"].callback
    assert t.commands["get"] is calls.trace.commands["get"] and t.commands["purge"] is calls.trace.commands["purge"]
    assert old["calls"].commands is calls.trace.commands and calls.trace.commands["index"] is calls.trace_index, \
        "tracer calls index: the tool-call index alone, as it was"
    assert t.commands["index"] is not calls.trace_index, "tracer trace index runs both indexes"
    assert {n for n, c in t.commands.items() if getattr(c, "writes", False)} == {"index", "purge"}
    assert getattr(old["index"], "writes", False)
    assert sorted(s.commands) == ["agents", "live", "ls", "replay", "search", "show", "turns"]
    assert sorted(t.commands) == ["blame", "get", "index", "purge", "stats", "turn"]


def test_help_lists_the_verbs_with_the_write_mark(tmp_path):
    def listed(out):
        return set(re.findall(r"^  ([a-z]+) ", out.split("Commands:", 1)[-1].split("Examples:", 1)[0], re.M))

    s = _cli(tmp_path, "sessions", "-h").stdout
    assert listed(s) == {"ls", "live", "show", "agents", "replay", "turns", "search"}
    t = _cli(tmp_path, "trace", "-h").stdout
    assert listed(t) == {"turn", "blame", "get", "stats", "index", "purge"}
    assert "✎ Index the transcripts and the tool calls" in t and "✎ Delete old tool calls" in t
    root = _cli(tmp_path, "-h", door="tracer").stdout
    assert listed(root) == {"sessions", "trace"}, "the old spellings run, out of the lists"
    assert "[b]" not in root and "\x1b[" not in root, "plain help for an agent, its markup rendered away"


@needs_host
def test_ak_lists_the_two_groups_and_hides_tracer(tmp_path):
    root = _cli(tmp_path, "-h", door=CLI).stdout
    assert re.search(r"^  sessions ", root, re.M) and re.search(r"^  trace ", root, re.M)
    assert not re.search(r"^  tracer ", root, re.M), "the old spelling runs, out of the lists"


@needs_host
@pytest.mark.parametrize("args", [("sessions", "ls", "nope-folder"), ("trace", "turn", "deadbeef-001"),
                                  ("sessions", "show", "-h"), ("trace", "-h")])
def test_mounted_and_alone_print_the_same_lines(tmp_path, args):
    """The same click objects, the same copies of the door: inside `ak` or on its own, one output.
    Only a help's usage line differs — it names the command that was typed."""
    a, b = _cli(tmp_path, *args, door=CLI), _cli(tmp_path, *args, door="tracer")
    assert a.returncode == b.returncode, (a.stderr, b.stderr)
    assert a.stderr == b.stderr
    usage = re.compile(rf"^Usage: ({re.escape(CLI)}|tracer) ", re.M)
    assert usage.sub("Usage: ", a.stdout) == usage.sub("Usage: ", b.stdout)


def test_old_and_new_spellings_print_the_same_lines(tmp_path):
    """`sessions ls <x>` and the old `ak tracer sessions <x>`; `trace turn` and the old `inspect`."""
    old = ("tracer",) if HOST else ()  # inside ak the old root is `ak tracer`
    a, b = _cli(tmp_path, "sessions", "ls", "nope-folder"), _cli(tmp_path, *old, "sessions", "ls", "nope-folder")
    assert a.returncode == b.returncode == 64 and a.stderr == b.stderr and "no folder" in a.stderr
    a, b = _cli(tmp_path, "trace", "turn", "deadbeef-001"), _cli(tmp_path, *old, "inspect", "deadbeef-001")
    assert a.returncode == b.returncode == 1
    assert "no session starts with 'deadbeef'" in a.stderr and "no session starts with 'deadbeef'" in b.stderr


def test_trace_turn_wants_a_turn_and_show_says_where_it_lives(tmp_path):
    claude = tmp_path / "claude" / "projects" / "-tmp-proj"
    write_jsonl(claude / f"{SID}.jsonl", [user("hi", 0), asst([text("hello")], 1, "m1")])
    r = _cli(tmp_path, "trace", "turn", SID[:8])
    assert r.returncode == 64
    assert "is a session, not a turn" in r.stderr and f"hint: {command('sessions show')} {SID[:8]}" in r.stdout
    r = _cli(tmp_path, "sessions", "show", SID[:8])
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith(SID)
    r = _cli(tmp_path, "sessions", "show", "latest", "--turn", "0")
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith(f"{SID}-000")


@pytest.mark.parametrize("verb", [("sessions", "show"), ("trace", "turn"), ("sessions", "replay")])
def test_an_engine_view_is_coloured_for_a_person_and_the_same_bytes_piped(tmp_path, verb):
    """plugins/AGENTS.md §8: the same bytes in a terminal and a pipe is the bug. These views are the
    engine's text (what an agent reads piped); a terminal gets its landmarks coloured, nothing else."""
    claude = tmp_path / "claude" / "projects" / "-tmp-proj"
    write_jsonl(claude / f"{SID}.jsonl", [
        user("run the tests", 0),
        asst([call("t1", "Bash", command="uv run pytest -q", description="Run the tests")], 1, "m1"),
        result("t1", "19 passed", 2), asst([text("green")], 3, "m2")])
    target = f"{SID[:8]}-000" if verb == ("trace", "turn") else SID[:8]

    def run(**extra):
        return _cli(tmp_path, *verb, target, **extra)
    plain, rich = run(), run(**{env_name("OUTPUT"): "rich", "FORCE_COLOR": "1"})
    assert plain.returncode == rich.returncode == 0, rich.stderr
    assert "\x1b[" not in plain.stdout
    assert "\x1b[" in rich.stdout, "a person got the agent's plain text"
    assert re.sub(r"\x1b\[[0-9;]*m", "", rich.stdout) == plain.stdout, "colour changed the text"


def test_show_as_skill_takes_an_id_or_words_and_show_alone_one_id(tmp_path):
    """`ak tracer trace` took TARGET...: a UUID, or words that describe the session. `sessions show
    --as-skill` keeps that; an id (8 characters, latest) is made whole first, words go to the skill as typed."""
    snippet = """
import sys; sys.path.insert(0, %r)
from click.testing import CliRunner
from tracer import cli as m, drill
got = []
m._prefetch = lambda target, *a: got.append(target)
drill.match_session_ids = lambda p: [%r] if %r.startswith(p) else []
run = lambda *a: CliRunner().invoke(m.sessions_group, ["show", *a])
assert run("06a45a57", "--as-skill").exit_code == 0 and got[-1] == (%r,)
assert run("plugin", "split", "--as-skill").exit_code == 0 and got[-1] == ("plugin", "split")
assert run("plugin split", "--as-skill").exit_code == 0 and got[-1] == ("plugin split",)
r = run("plugin", "split")
assert r.exit_code == 2 and "show takes one session id, not 2 words" in r.output, r.output
assert run("06a45a57", "--as-skill", "--turn", "1").exit_code == 2
print("ok")
""" % (str(PLUGIN / "src"), SID, SID, SID)
    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True, text=True, env=_env(tmp_path, "tracer"))
    assert r.stdout.strip() == "ok", r.stderr


def test_sessions_turns_hands_the_engine_every_filter_and_hints_the_list(tmp_path):
    """`ak sessions turns`: turns across sessions by plugin, agent, skill, tool, errors, dates and
    branch — what the MCP server's convo_query, convo_stats and convo_patterns answered, as a verb.
    Its --stats and --patterns end on the same question as a list."""
    folder = tmp_path / "proj"
    folder.mkdir()
    snippet = """
import sys; sys.path.insert(0, %r)
from pathlib import Path
from click.testing import CliRunner
from tracer import cli as m
from tracer.verbs import command
got = []
m._run_engine = got.append
run = lambda *a: CliRunner().invoke(m.sessions_group, ["turns", *a])
r = run("--plugin", "observer", "--agent-type", "Explore", "--skill", "recall", "--tool", "Bash", "--errors",
        "--since", "2026-09-01", "--until", "2026-09-30", "--branch", "feat/", "--limit", "5", "--all")
assert r.exit_code == 0, r.output
assert got[-1] == ["query", "--plugin", "observer", "--agent-type", "Explore", "--skill", "recall", "--tool", "Bash",
                   "--since", "2026-09-01", "--until", "2026-09-30", "--branch", "feat/", "--errors",
                   "--limit", "5", "--all-projects"], got[-1]
assert "hint:" not in r.output  # the list names its own next verbs
r = run(%r, "--tool", "Bash", "--stats")
assert r.exit_code == 0, r.output
assert got[-1] == ["query", "--tool", "Bash", "--limit", "20", "--root", str(Path(%r).resolve()), "--stats"], got[-1]
assert r.output.strip().splitlines()[-1] == "hint: " + command("sessions turns") + " " + %r + " --tool Bash", r.output
r = run("--patterns", "--all")
assert got[-1][-2:] == ["--all-projects", "--patterns"], got[-1]
assert r.output.strip().splitlines()[-1] == "hint: " + command("sessions turns") + " --all", r.output
assert run("--stats", "--patterns").exit_code == 2
assert run(%r, "--all").exit_code == 2
assert run("--limit", "0").exit_code == 2
r = run("nope-folder")
assert r.exit_code == 64 and "no folder" in r.output, r.output
print("ok")
""" % (str(PLUGIN / "src"), str(folder), str(folder), str(folder), str(folder))
    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True, text=True, env=_env(tmp_path, "tracer"))
    assert r.stdout.strip().endswith("ok"), r.stdout + r.stderr
