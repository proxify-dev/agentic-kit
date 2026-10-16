"""The SessionStart digest remembers who read it: a last-seen cursor per place,
new rows in full, the rest collapsed to one line that still carries the IDs of
everything on it that has an ID — and a compaction, which is not an arrival,
neither narrows the window nor moves it.

The cursor is the newest row rendered, not the clock, and it moves only once the
block has actually been written out."""
import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
from _brand import env_name  # noqa: E402
from observer.verbs import command
import store_common as sc  # noqa: E402

BASE = time.time()
OLD = [(int((BASE - 3600) * 1000), "first row, long read"),
       (int((BASE - 1800) * 1000), "second row, long read"),
       (int((BASE - 900) * 1000), "third row, long read")]
NEW = (int((BASE + 60) * 1000), "the only thing that happened since")


@pytest.fixture
def env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "cache" / "sessions").mkdir(parents=True)
    (vault / "projects").mkdir()
    monkeypatch.setenv(env_name("PATH"), str(vault))
    monkeypatch.setenv(env_name("STORE"), str(tmp_path / "store"))
    monkeypatch.setenv("CLAUDE_MEM_DB", str(tmp_path / "absent.db"))
    sc.init()
    conn = sc.connect()
    conn.execute("INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source, "
                 "started_at, started_at_epoch) VALUES(?,?,?,?,?,?)",
                 ("c-1", "m-1", "repo", "brain", "2026-09-17T00:00:00", OLD[0][0]))  # brand: historical (platform_source)
    for epoch, title in OLD:
        _observe(conn, epoch, title)
    conn.execute("INSERT INTO session_summaries(memory_session_id, project, request, created_at, "
                 "created_at_epoch) VALUES(?,?,?,?,?)",
                 ("m-1", "repo", "an old request nobody needs twice", "2026-09-17T00:00:00", OLD[0][0]))
    conn.commit()
    conn.close()
    cwd = tmp_path / "repo"
    cwd.mkdir()
    return {"vault": vault, "cwd": str(cwd), "cursor": vault / "cache" / "digest-cursor.json"}


def _observe(conn, epoch: int, title: str) -> None:
    conn.execute("INSERT INTO observations(memory_session_id, project, type, title, narrative, facts, "
                 "concepts, discovery_tokens, created_at, created_at_epoch) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 ("m-1", "repo", "discovery", title, "narrative " * 40, json.dumps(["a fact"]),
                  json.dumps(["repo"]), 5000, "2026-09-17T00:00:00", epoch))


def _mod():
    spec = importlib.util.spec_from_file_location("session_start", PLUGIN / "hooks" / "session-start.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _obs_index():
    """observer's own backend, picked the way `ak observer get` picks it — at import, against
    whatever store the environment names — so `get(IDs)` in a test is the call a model
    reading the block would actually make. Loaded fresh so the choice is made after the
    fixture's env, never off another test's import."""
    spec = importlib.util.spec_from_file_location(
        "obs_index_under_test", PLUGIN / "src" / "observer" / "obs_index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _digest(mod, *a, **kw):
    """One arrival end to end: render, then let the stamp land the way main() does —
    the print, and the word after it that says the print happened."""
    out = mod.memory_digest(*a, **kw)
    mod.mark_delivered()
    mod.flush_stamps()
    return out


def _main(mod, env, monkeypatch, source="startup", print_raises=False):
    """The hook the way Claude Code runs it: payload on stdin, one JSON block on stdout,
    then atexit. `print_raises` is the window under test — a render that finished and a
    write that never landed."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": env["cwd"], "source": source})))

    def closed_pipe(*a, **kw):
        raise BrokenPipeError("the receiving end went away between render and write")
    monkeypatch.setattr(mod, "print", closed_pipe if print_raises else print, raising=False)
    try:
        mod.main()
    except BrokenPipeError:
        pass
    mod.flush_stamps()


# The observation IDs the temp store hands out, oldest row first — 1, 2, 3, in the order
# the block itself prints them. The session summary sharing obs 1's epoch is NOT here: it
# collapses to a count, because `S1` is a label and not a handle.
OLD_OBS = "1 2 3"


def _older_line(mod, n: int, obs: str = OLD_OBS, sessions: int = 1) -> str:
    day = mod._local(OLD[0][0]).strftime("%Y-%m-%d")
    return (f"{n} older rows in scope: {obs} · `{command('get')} <IDs>` to read them; "
            f"{sessions} session{'' if sessions == 1 else 's'} not fetchable by id "
            f"· since('{day}') lists them")


def test_second_arrival_lists_only_what_is_new(env):
    mod = _mod()

    plain, shown = _digest(mod, env["cwd"], source="startup")
    assert all(t in plain for _, t in OLD)  # first morning here: today's behaviour exactly
    assert "older rows in scope" not in plain and "since you were last here" not in plain
    assert json.loads(env["cursor"].read_text())[os.path.realpath(env["cwd"])]

    conn = sc.connect()
    _observe(conn, *NEW)
    conn.commit()
    conn.close()

    plain, shown = _digest(mod, env["cwd"], source="startup")
    assert NEW[1] in plain
    assert not any(t in plain for _, t in OLD)
    assert "since you were last here" in plain and ", 1 new)" in plain
    assert "Stats: 1 obs" in plain  # the tokens line counts what was printed, not what was skipped
    assert "observations · 1 sessions on record for repo" in plain
    assert "since you were last here" in shown


def test_the_collapsed_line_names_a_route_that_reaches_the_rows(env):
    """Every ID the line prints must come back out of the tool the line names, and the rows
    that have no such tool must not be printed as IDs at all. The old line mixed observation
    ids with `S`-prefixed session-summary ids under one `get <IDs>` route;
    `get` resolves the observations table and nothing else, and the two tables
    share tens of thousands of integers, so a model that dropped the `S` was handed a
    different record and told nothing had gone wrong."""
    mod = _mod()
    index = _obs_index().BACKEND
    _digest(mod, env["cwd"], source="startup")
    conn = sc.connect()
    _observe(conn, *NEW)
    conn.commit()
    conn.close()

    plain, shown = _digest(mod, env["cwd"], source="startup")
    line = next(l for l in plain.splitlines() if "older rows in scope" in l)

    # The claim, not the string: every id the line hands to `ak observer get` comes back
    # out of the backend it reads (the same BACKEND.get), with a body.
    ids = line.split("older rows in scope: ")[1].split(f" · `{command('get')}")[0].split()
    fetched = index.get(ids)
    assert ids and [r["id"] for r in fetched] == ids
    assert all(r["content"] for r in fetched)

    # And the reason the session row is a count instead: `S1` resolves to nothing here, while
    # the bare `1` under it resolves to an OBSERVATION — the silently wrong record.
    assert "S1" not in line
    assert index.get(["S1"])[0]["content"] is None
    assert "1" in ids and index.get(["1"])[0]["content"]

    assert line == _older_line(mod, 4)  # 3 observations + 1 session summary
    assert line in shown
    assert plain.split(line)[1].startswith("\n\n###")  # a blank line, not flush against the day


def test_zero_new_arrival_says_so_and_leaves_the_cursor(env):
    """The measured case: two arrivals minutes apart with nothing written between them."""
    mod = _mod()
    _digest(mod, env["cwd"], source="startup")
    stamped = env["cursor"].read_text()

    plain, shown = _digest(mod, env["cwd"], source="startup")
    assert ", 0 new)" in plain and "Stats: 0 obs" in plain
    assert _older_line(mod, 4) in plain
    assert not any(t in plain for _, t in OLD)
    assert "(0% reduction from reuse)" in shown  # no rows, no division
    assert env["cursor"].read_text() == stamped  # there is no newer row to move to


def test_the_stamp_waits_for_the_block_to_leave_the_process(env):
    """A hook timeout or a closed pipe between rendering and printing must not move a
    cursor for a digest nobody received."""
    mod = _mod()
    mod.memory_digest(env["cwd"], source="startup")
    assert not env["cursor"].exists()
    mod.flush_stamps()
    assert not env["cursor"].exists()  # a flush is not a delivery
    mod.mark_delivered()
    mod.flush_stamps()
    assert json.loads(env["cursor"].read_text())[os.path.realpath(env["cwd"])]


def test_a_render_that_never_reached_stdout_leaves_the_cursor(env, monkeypatch):
    """`sys.stdout.flush()` succeeds on a stream nothing was written to, so the flush could
    never tell a delivered block from a raise between the render and the print — and
    `main()` can throw in exactly that gap (the print itself, on a closed pipe). The window
    must survive it."""
    mod = _mod()
    _main(mod, env, monkeypatch, print_raises=True)
    assert not env["cursor"].exists()

    _main(mod, env, monkeypatch)  # same process, same rows: the block lands, the cursor moves
    assert json.loads(env["cursor"].read_text())[os.path.realpath(env["cwd"])]


def test_main_delivers_the_block_and_stamps(env, monkeypatch, capsys):
    """The whole hook once through: one JSON object on stdout carrying the digest for the
    person (never in the model's context), and the cursor moved to the newest row it printed."""
    mod = _mod()
    _main(mod, env, monkeypatch)
    payload = json.loads(capsys.readouterr().out)
    assert all(t in payload["systemMessage"] for _, t in OLD)
    assert not any(t in payload["hookSpecificOutput"]["additionalContext"] for _, t in OLD)
    cursor = json.loads(env["cursor"].read_text())[os.path.realpath(env["cwd"])]
    assert cursor == mod._local(OLD[2][0]).isoformat(timespec="milliseconds")


def test_a_resume_prints_nothing_and_leaves_the_window(env, monkeypatch, capsys):
    """A resume is the conversation picked back up — the person saw the block when it first
    opened. The model keeps its doors line, the screen gets nothing, and the cursor stays
    put, so a row that landed meanwhile is still new at the next fresh start."""
    mod = _mod()
    _main(mod, env, monkeypatch)
    stamped = env["cursor"].read_text()
    capsys.readouterr()
    conn = sc.connect()
    _observe(conn, *NEW)
    conn.commit()
    conn.close()

    _main(mod, env, monkeypatch, source="resume")
    payload = json.loads(capsys.readouterr().out)
    assert payload["hookSpecificOutput"]["additionalContext"] == mod.TEACH
    assert "systemMessage" not in payload
    assert env["cursor"].read_text() == stamped

    _main(mod, env, monkeypatch, source="startup")
    assert NEW[1] in json.loads(capsys.readouterr().out)["systemMessage"]


def test_the_stamp_is_the_newest_row_rendered_not_the_clock(env):
    mod = _mod()
    _digest(mod, env["cwd"], source="startup")
    cursor = json.loads(env["cursor"].read_text())[os.path.realpath(env["cwd"])]
    assert cursor == mod._local(OLD[2][0]).isoformat(timespec="milliseconds")


def test_a_row_that_lands_under_the_cursor_is_still_reachable(env):
    """`ak observer store sync` imports claude-mem rows carrying their original epoch, so a row
    can arrive already older than the stamp. It is never printed; the line's IDs are the
    whole of its reachability."""
    mod = _mod()
    _digest(mod, env["cwd"], source="startup")
    conn = sc.connect()
    _observe(conn, int((BASE - 2700) * 1000), "an import carrying yesterday's epoch")
    conn.commit()
    conn.close()

    plain, _ = _digest(mod, env["cwd"], source="startup")
    assert "an import carrying yesterday's epoch" not in plain
    assert _older_line(mod, 5, obs="1 4 2 3") in plain


def test_a_corrupt_cursor_costs_the_session_nothing(env):
    mod = _mod()
    env["cursor"].write_text("{not json")
    plain, _ = _digest(mod, env["cwd"], source="startup")
    assert all(t in plain for _, t in OLD)
    assert "older rows in scope" not in plain
    assert json.loads(env["cursor"].read_text())[os.path.realpath(env["cwd"])]


def test_compact_neither_narrows_nor_moves_the_window(env):
    mod = _mod()
    _digest(mod, env["cwd"], source="startup")
    stamped = env["cursor"].read_text()

    plain, _ = _digest(mod, env["cwd"], source="compact")
    assert all(t in plain for _, t in OLD)  # a compaction is not an arrival
    assert "older rows in scope" not in plain
    assert env["cursor"].read_text() == stamped


def test_cursor_is_keyed_by_place(env):
    mod = _mod()
    _digest(mod, env["cwd"], bound="my-app", source="startup")
    _digest(mod, env["cwd"], source="startup")
    keys = json.loads(env["cursor"].read_text())
    assert set(keys) == {"my-app", os.path.realpath(env["cwd"])}


def test_a_key_for_a_directory_that_is_gone_is_pruned(env, tmp_path):
    """One key per place ever visited, and temp checkouts are places. A bound slug is not
    a path and is never pruned; the failure direction either way is one re-print."""
    mod = _mod()
    env["cursor"].write_text(json.dumps({str(tmp_path / "vanished"): "2026-01-01T00:00:00.000",
                                         "my-app": "2026-01-01T00:00:00.000"}))
    _digest(mod, env["cwd"], source="startup")
    keys = json.loads(env["cursor"].read_text())
    assert str(tmp_path / "vanished") not in keys
    assert set(keys) == {"my-app", os.path.realpath(env["cwd"])}
