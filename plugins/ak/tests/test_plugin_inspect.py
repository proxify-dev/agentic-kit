"""`ak plugin` against a fake Claude Code home (plugin_inspect_home.home): read, attribute, rank, render."""
from __future__ import annotations

import json

import pytest

from click.testing import CliRunner

from ak import ui
from ak._brand import cmd
from ak.plugin_inspect import cli as cli_mod
from ak.plugin_inspect import heard, installed, report
from plugin_inspect_home import SID, home  # noqa: F401 — home is the fixture


def _heard():
    return heard.Owners(installed.plugins(), installed.history_index()).name_all(
        heard.read(heard.find(SID[:8])))


# ── installed: what is wired, from disk ──────────────────────────────────────
def test_installed_reads_versions_switches_and_wiring(home):
    ps = {p.key: p for p in installed.plugins()}
    assert ps["alpha@mk"].enabled and not ps["alpha@old"].enabled
    alpha = ps["alpha@mk"]
    assert [(h.event, h.matcher) for h in alpha.hooks] == [("SessionStart", ""), ("PreToolUse", "Write"),
                                                          ("PostToolUse", "Edit|Write")]
    # a `description: >-` block is its indented lines, not the marker
    assert alpha.skills == [("sweep", "Sweep the vault, twice.")]


def test_a_hook_whose_script_is_gone_is_broken_without_running_it(home):
    alpha = next(p for p in installed.plugins() if p.key == "alpha@mk")
    broken = {h.event: h.missing() for h in alpha.hooks}
    assert broken["SessionStart"] == [] and broken["PostToolUse"] == []
    assert broken["PreToolUse"][0].endswith("/hooks/gate.py")
    # a settings.json hook: nobody owns it and no update fixes it
    assert installed.settings_hooks()[0].missing() == ["/nowhere/plugins/ak/hooks/stop.py"]


# ── heard: what was said, from the transcript ────────────────────────────────
def test_every_hook_run_is_read_with_its_kind_and_turn(home):
    rows = _heard()
    assert [(r.turn, r.hook, r.kind) for r in rows] == [
        (0, "SessionStart:startup", "context"),
        (0, "SessionStart:startup", "context"),
        (0, "SessionStart:startup", "user"),
        (1, "PreToolUse:Write", "block"),  # the prompt written twice is one turn
        (1, "PostToolUse:Edit", "stderr"),  # exit 2 after the tool ran blocks nothing
        (1, "PostToolUse:Edit", "silent"),
    ]
    assert rows[2].text == "beta: 3 open"  # colour codes are not text
    assert rows[0].ms == 200


def test_only_what_reaches_the_model_costs_tokens(home):
    rows = _heard()
    assert rows[0].tokens == len("[alpha] hello there, one move each") // 4
    assert rows[2].tokens == 0  # a systemMessage is shown to the person only


def test_two_plugins_running_the_same_command_are_told_apart_by_their_words(home):
    rows = _heard()
    assert rows[0].plugin == "alpha"  # its source holds "[alpha]"
    assert rows[1].plugin == "beta"  # tag built at runtime: "recent context," decides
    assert rows[2].plugin == "alpha|beta"  # nothing to go on: both named, never a guess
    assert rows[3].plugin == "alpha" and rows[4].plugin == "alpha"


def test_plain_stdout_reaches_the_model_only_at_session_start_and_prompt(home):
    run = {"type": "hook_success", "stdout": "plain words"}
    assert heard.classify({**run, "hookEvent": "SessionStart"}) == [("context", "plain words")]
    assert heard.classify({**run, "hookEvent": "PostToolUse"}) == [("silent", "plain words")]
    deny = json.dumps({"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "no"}})
    assert heard.classify({"type": "hook_success", "hookEvent": "PreToolUse", "stdout": deny}) == [("deny", "no")]


# ── report: the two views as data ────────────────────────────────────────────
def test_ranking_puts_the_loudest_first_and_counts_blocks_and_broken(home):
    rows = report.ranking(installed.plugins(), _heard(), sessions=1)
    top = rows[0]
    assert top["plugin"] == "alpha" and top["blocked"] == 1 and top["broken"] == 1
    assert top["loudest"] == "PreToolUse:Write"  # the block's stderr is the longest thing it said
    assert {r["plugin"] for r in rows} >= {"alpha", "beta"}


def test_card_joins_each_wired_hook_to_what_it_said(home):
    ps = installed.plugins()
    c = report.card(next(p for p in ps if p.key == "alpha@mk"), "alpha", _heard(), sessions=1)
    by = {h["event"]: h for h in c["hooks"]}
    assert by["SessionStart"]["fires"] == 1 and by["SessionStart"]["says"].startswith("[alpha] hello")
    assert by["PreToolUse"]["kinds"] == {"block": 1} and by["PreToolUse"]["missing"]
    assert by["PostToolUse"]["kinds"] == {"stderr": 1}
    assert c["skills"] == [{"name": "sweep", "description": "Sweep the vault, twice."}]


# ── the verbs ────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _plain(monkeypatch):
    monkeypatch.setattr(ui, "_MODE", "plain")  # --json is sticky for the process; each test is its own call


def run(*args):
    return CliRunner().invoke(cli_mod.cli, list(args))


def test_bare_is_list_and_plain_is_tsv(home):
    r = run()
    assert r.exit_code == 0, r.output
    header = next(line for line in r.output.splitlines() if line.startswith("plugin\t"))
    assert header.split("\t")[:3] == ["plugin", "", "tok/session"]
    assert f"hint: {cmd('plugin')} inspect <plugin>" in r.output
    assert "settings.json Stop hook runs a missing file" in r.output


def test_the_group_is_native_to_ak_and_carries_rename(home):
    """`ak plugin` is ak's own group (no plugin mounts it): its verbs, and move's `rename`, are there unmounted."""
    from ak.cli import cli as ak_cli
    assert ak_cli.commands["plugin"] is cli_mod.cli
    assert {"ls", "inspect", "log", "rename"} <= set(cli_mod.cli.commands)


def test_inspect_finds_a_plugin_by_part_of_its_name(home):
    r = run("inspect", "alph")
    assert r.exit_code == 0, r.output
    assert "alpha@mk 1.0.0 · on" in r.output
    assert "BROKEN:" in r.output and "gate.py is missing" in r.output
    assert "[alpha] hello there" in r.output  # a tag in the text is text, not markup


def test_inspect_miss_exits_1_and_ambiguous_exits_2(home):
    assert run("inspect", "zeta").exit_code == 1
    assert run("inspect", "a").exit_code == 2  # alpha and beta


def test_log_shows_who_read_each_line(home):
    r = run("log", "-s", SID[:8])
    assert r.exit_code == 0, r.output
    lines = [line.split("\t") for line in r.output.splitlines() if "\t" in line]
    seen = {(c[2], c[4], c[5]) for c in lines[1:]}  # hook, kind, seen by
    assert ("PreToolUse:Write", "block", "model") in seen
    assert ("SessionStart:startup", "user", "you") in seen
    assert not any(c[4] == "silent" for c in lines[1:])  # silent runs only with --all
    assert "can't open file" in r.output and "/opt/py/Python" not in r.output


def test_json_is_one_document_per_verb(home):
    for args in (("ls",), ("inspect", "alpha"), ("log", "-s", SID[:8])):
        r = run(*args, "--json")
        assert r.exit_code == 0, r.output
        json.loads(r.output)


def test_ls_is_the_ranking_bare_runs_it_and_list_still_runs_hidden(home):
    """Listing is `ls` everywhere in `ak`; `list`, the released name, runs the same verb, out of help."""
    want = run("ls", "--json")
    assert want.exit_code == 0, want.output
    assert run("list", "--json").output == want.output
    assert run().output == run("ls").output  # bare `ak plugin` is the ranking
    listed = [ln.split()[0] for ln in run("--help").output.splitlines() if ln.startswith("  ") and ln.strip()]
    assert "ls" in listed and "list" not in listed


def test_log_miss_exits_1(home):
    assert run("log", "-s", "ffffffff").exit_code == 1


# ── nothing is cut: the whole text is what you run these verbs to read ───────
LONG = "[alpha] " + " ".join(f"word{i}" for i in range(80)) + "\nsecond line, the end"


def _say_long(home, times=2):
    """The alpha start hook says LONG, more often than anything else it said."""
    from plugin_inspect_home import START, ctx, hook
    t = next((home / "projects").glob("*/*.jsonl"))
    with t.open("a", encoding="utf-8") as f:
        for _ in range(times):
            f.write(json.dumps(hook(50, "SessionStart", "SessionStart:startup", START, ctx("SessionStart", LONG))) + "\n")


def test_gist_is_the_whole_text_its_newest_wording():
    assert report._gist(["[a] ctx, Monday " + "x" * 300, "[a] ctx, Monday " + "x" * 300 + " newest"]).endswith("newest")
    assert "…" not in report._gist(["y" * 500])


def test_inspect_and_log_carry_every_word_on_one_plain_line(home):
    _say_long(home)
    flat = " ".join(LONG.split())
    for args in (("inspect", "alpha"), ("log", "-s", SID[:8])):
        r = run(*args)
        assert r.exit_code == 0, r.output
        assert any(flat in line for line in r.output.splitlines()), r.output  # whole, and on one line
        assert "…" not in r.output
    card = json.loads(run("inspect", "alpha", "--json").output)
    assert next(h for h in card["hooks"] if h["event"] == "SessionStart")["says"] == LONG


class _Rich:
    """Stands in for ak.ui on a terminal: records what each renderer was handed."""

    def __init__(self):
        self.calls = []

    def json_mode(self, flag): pass
    def is_json(self): return False
    def is_rich(self): return True
    def hint(self, s): self.calls.append(("hint", s))
    def warn(self, s): self.calls.append(("warn", s))
    def text(self, s, **_): self.calls.append(("text", s))
    def panel(self, body, **kw): self.calls.append(("panel", body, kw))
    def table(self, columns, rows, **kw): self.calls.append(("table", columns, rows))


def test_a_person_gets_each_text_whole_in_a_box_and_the_silent_hooks_on_one_list(home, monkeypatch):
    _say_long(home)
    rich = _Rich()
    monkeypatch.setattr(cli_mod, "out", rich)
    assert run("inspect", "alpha").exit_code == 0
    panels = [c[1] for c in rich.calls if c[0] == "panel"]
    assert LONG in panels  # line breaks kept, nothing cut
    assert not any(c[0] == "table" and "what it says" in c[1] for c in rich.calls)  # no cramped table
    assert any(c[0] == "text" and "BROKEN" in c[1] and "gate.py" in c[1] for c in rich.calls)

    rich.calls.clear()
    assert run("log", "-s", SID[:8]).exit_code == 0
    heads = [c[1] for c in rich.calls if c[0] == "text"]
    assert any("session start" in h for h in heads) and any("turn 1" in h for h in heads)
    boxes = [c for c in rich.calls if c[0] == "panel"]
    assert LONG in [b[1] for b in boxes]
    assert any("alpha" in b[2]["title"] and "block" in b[2]["subtitle"] for b in boxes)


def test_brain_status_names_the_broken_hook(home):
    (label, line), = cli_mod.brain_status()
    assert label == "plugins" and "broken hook in alpha, settings.json" in line


# ── Windows commands, read on any OS ─────────────────────────────────────────
@pytest.mark.parametrize("command, script", [
    ('"${CLAUDE_PLUGIN_ROOT}/hooks/py" "${CLAUDE_PLUGIN_ROOT}/hooks/session.py"', "session.py"),
    ('uv run --quiet --no-project --python ">=3.10" "${CLAUDE_PLUGIN_ROOT}/hooks/session.py"', "session.py"),
    (r'uv run "C:\Users\x\.claude\plugins\cache\ak\memory\1.0\hooks\surface.py"', "surface.py"),
    ("node /opt/x/dist/brain.mjs", "brain.mjs"),
    ("echo hi", "echo"),
])
def test_script_is_named_across_either_slash(command, script):
    assert report._script(command) == script


@pytest.mark.parametrize("command, plugin", [
    ("/Users/x/.claude/plugins/cache/ak/memory/1.0/hooks/py", "memory"),
    (r"C:\Users\x\.claude\plugins\cache\ak\memory\1.0\hooks\py", "memory"),
    (r"C:\src\agentic-kit\plugins\tracer\hooks\stop.py", "tracer"),
])
def test_a_path_under_plugins_names_its_plugin_either_slash(command, plugin):
    h = heard.Heard("s", 0, "", "Stop", "Stop", command, "context", "x")
    assert heard.Owners([], {}).name(h) == plugin


def test_windows_hook_words_keep_their_backslashes(monkeypatch):
    monkeypatch.setattr(installed, "is_windows", lambda: True)
    monkeypatch.setattr(installed.os.path, "isabs", lambda w: w[1:3] in (":\\", ":/"))  # ntpath's rule, roughly
    h = installed.Hook("SessionStart", "", r'uv run "${CLAUDE_PLUGIN_ROOT}\hooks\gone.py" --quiet', r"C:\Users\x\plug")
    assert h.missing() == [r"C:\Users\x\plug\hooks\gone.py"]
