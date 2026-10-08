"""`ak sessions turns` honours every filter it takes, in its list, its --stats and its --patterns.

The patterns view once accepted project, plugin, agent_type, skill, tool, errors, since, until,
branch and session, and passed only project, plugin and errors on — the rest were read and
dropped. The verb hands every filter to the engine's `query` (tests/test_homes.py holds the
argv); these run that query against a small index.
"""
from __future__ import annotations

import pytest

from tracer import __main__ as engine_main
from tracer import sessions
from tracer.db import get_write_connection
from tracer.models import AgentSpawn, ErrorSignal, InteractionRecord, PluginCredit, SkillInvocation, ToolCall
from tracer.storage import IndexWriter


def _rec(iid, ts, branch="main", **kw):
    sid = iid.rsplit("-", 1)[0]
    return InteractionRecord(id=iid, session_id=sid, timestamp=ts, git_branch=branch, **kw)


@pytest.fixture
def query(tmp_path, monkeypatch, capsys):
    """Run `tracer-engine query <argv>` over a four-turn index; returns (ids the patterns read, stdout, exit)."""
    con = get_write_connection(tmp_path / "t.db")
    IndexWriter(con, "-proj-a").insert_records([
        _rec("a-000", "2026-09-01T10:00:00Z", tools_called=[ToolCall("Bash", 2), ToolCall("Skill", 1)],
             skills_invoked=[SkillInvocation("other:thing")],  # a Skill call, another skill
             plugins=["alpha"], plugin_credits=[PluginCredit("alpha", "mcp", "exact")]),
        _rec("a-001", "2026-09-05T10:00:00Z", branch="feat/x",
             agents_spawned=[AgentSpawn("Explore")], tools_called=[ToolCall("Agent", 1)]),
    ])
    IndexWriter(con, "-proj-b").insert_records([
        _rec("b-000", "2026-09-10T10:00:00Z", skills_invoked=[SkillInvocation("alpha:recall", plugin="alpha")],
             tools_called=[ToolCall("Skill", 1)]),
        _rec("b-001", "2026-09-20T10:00:00Z", tools_called=[ToolCall("Read", 1)],
             error_signals=[ErrorSignal("tool_error", "Exit code 1")]),
    ])

    class NoClose:
        def __getattr__(self, name):
            return getattr(con, name)

        def close(self):
            pass

    monkeypatch.setattr(engine_main, "get_readonly_connection", lambda: NoClose())
    monkeypatch.setattr(engine_main, "staleness_warning", lambda *a: None)
    monkeypatch.setattr(sessions, "discover_projects", lambda: [])  # the index's own project dirs only
    captured: list[list[str]] = []
    real = engine_main.extract_tool_sequences
    monkeypatch.setattr(engine_main, "extract_tool_sequences",
                        lambda records: captured.append(sorted(r.id for r in records)) or real(records))

    def run(*argv) -> tuple[list[str] | None, str, int]:
        monkeypatch.setattr("sys.argv", ["tracer-engine", "query", *argv])
        code = 0
        try:
            engine_main.main()
        except SystemExit as exc:
            code = exc.code
        return (captured[-1] if captured else None), capsys.readouterr().out, code

    yield run
    con.close()


FILTERS = [
    (["--root", "/proj/b"], ["b-000", "b-001"]),  # a folder, as `ak sessions turns <folder>` passes it
    (["--plugin", "alpha"], ["a-000"]),
    (["--agent-type", "Explore"], ["a-001"]),
    (["--skill", "recall"], ["b-000"]),
    (["--tool", "Read"], ["b-001"]),
    (["--errors"], ["b-001"]),
    (["--since", "2026-09-08"], ["b-000", "b-001"]),
    (["--until", "2026-09-06"], ["a-000", "a-001"]),
    (["--until", "2026-09-05"], ["a-000", "a-001"]),  # a bare date keeps that whole day (a-001 is 10:00)
    (["--branch", "feat"], ["a-001"]),
    (["--session", "b"], ["b-000", "b-001"]),
]


def _scoped(argv):
    return argv if "--root" in argv else [*argv, "--all-projects"]


@pytest.mark.parametrize("argv, expected", FILTERS, ids=lambda v: " ".join(v) if isinstance(v, list) and v[0].startswith("-") else "")
def test_each_filter_narrows_the_patterns(query, argv, expected):
    seen, _, _ = query(*_scoped(argv), "--patterns")
    assert seen == expected


@pytest.mark.parametrize("argv, expected", FILTERS, ids=lambda v: " ".join(v) if isinstance(v, list) and v[0].startswith("-") else "")
def test_each_filter_narrows_the_list_and_the_totals(query, argv, expected):
    _, out, code = query(*_scoped(argv))
    assert code == 0
    assert sorted(ln.split("id=")[1].split()[0] for ln in out.splitlines() if " id=" in ln) == expected
    _, out, _ = query(*_scoped(argv), "--stats")
    assert out.startswith(f"Total interactions: {len(expected)}\n")


def test_no_filter_reads_everything(query):
    seen, _, _ = query("--all-projects", "--patterns")
    assert seen == ["a-000", "a-001", "b-000", "b-001"]


def test_the_list_names_the_next_verbs_and_a_miss_exits_1(query):
    _, out, code = query("--all-projects", "--tool", "Read")
    assert out.splitlines()[0] == "1 interaction matched (2026-09-20 to 2026-09-20, 1 shown with errors)"
    assert out.rstrip().splitlines()[-1].startswith("next: ") and "b-001 for its steps" in out and " b for its session" in out
    _, out, code = query("--all-projects", "--plugin", "nobody")
    assert code == 1 and out.startswith("No interactions matched. Filters: plugin=nobody")
    _, out, code = query("--all-projects", "--plugin", "nobody", "--stats")
    assert code == 1 and out.startswith("Total interactions: 0")
    _, out, code = query("--root", "/proj/nowhere")
    assert code == 1 and "project=1 folder(s)" in out  # a folder with no sessions reads nothing, not everything
