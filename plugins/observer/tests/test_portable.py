"""The observer on any OS: a Windows session's paths match the way a Mac's do, the summariser is
started detached through the seam, and its model call carries the session on stdin — a transcript
outgrows Windows' 32,767-character command line. Windows is simulated with the seam's is_windows."""
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
import _brand  # noqa: E402
from _brand import env_name  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fc(tmp_path, monkeypatch):
    monkeypatch.setenv(env_name("PATH"), str(tmp_path / "vault"))
    return _load("file_context_portable", "hooks/file-context.py")


@pytest.mark.parametrize("entry,rel,hit", [
    ("src/x.py", "src\\x.py", True),       # Windows' relpath, the row as the model wrote it
    ("src\\x.py", "src\\x.py", True),
    ("x.py", "lib\\src\\x.py", False),     # a bare name is the repo's top-level file only
    ("src/x.py", "lib\\src\\x.py", True),  # any path ending /src/x.py, as on POSIX
    ("rc/x.py", "src\\x.py", False),       # never half a folder name
])
def test_a_windows_relative_path_names_the_file_it_did_on_posix(fc, monkeypatch, entry, rel, hit):
    monkeypatch.setattr(_brand, "is_windows", lambda: True)
    assert fc._touches(entry, "C:\\r\\src\\x.py", rel, True) is hit


def test_the_model_call_takes_the_session_on_stdin(monkeypatch):
    s = _load("summarize_portable", "scripts/summarize-session.py")
    seen = {}

    def run(argv, **kw):
        seen.update(argv=argv, input=kw.get("input"))
        return type("P", (), {"stdout": json.dumps({"result": "ok"}), "stderr": "", "returncode": 0})()

    monkeypatch.setattr(s.subprocess, "run", run)
    big = "é" * 40_000  # past Windows' command-line limit, and not ASCII
    assert s.call_llm(big, "haiku") == "ok"
    assert seen["input"] == big
    assert all(big not in a for a in seen["argv"])


def test_the_claude_binary_is_found_on_path(monkeypatch):
    s = _load("summarize_which", "scripts/summarize-session.py")
    monkeypatch.setattr("shutil.which", lambda name: "/opt/bin/claude" if name == "claude" else None)
    assert s.llm_argv("haiku")[0] == "/opt/bin/claude"
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert s.llm_argv("haiku")[0] == "claude"


def _win_path(monkeypatch, found):
    monkeypatch.setattr(_brand, "is_windows", lambda: True)
    monkeypatch.setattr("shutil.which", lambda name: found.get(name))


def test_windows_prefers_claude_exe_over_a_cmd_shim(tmp_path, monkeypatch):
    s = _load("summarize_exe", "scripts/summarize-session.py")
    _win_path(monkeypatch, {"claude.exe": "C:\\bin\\claude.exe", "claude": "C:\\npm\\claude.cmd"})
    assert s.claude_launch() == ["C:\\bin\\claude.exe"]


def test_windows_asks_the_shim_for_the_real_claude(tmp_path, monkeypatch):
    s = _load("summarize_shim", "scripts/summarize-session.py")
    (tmp_path / "cc-shim.mjs").write_text("", encoding="utf-8")
    stub = tmp_path / "claude.cmd"
    stub.write_text("@node cc-shim.mjs claude %*\r\n", encoding="utf-8")
    real = tmp_path / "claude.exe"
    real.write_text("", encoding="utf-8")
    _win_path(monkeypatch, {"claude": str(stub), "node.exe": "C:\\node\\node.exe"})
    asked = {}

    def run(argv, **kw):
        asked.update(argv=argv, env=kw["env"])
        return type("P", (), {"stdout": f"{real}\n", "returncode": 0})()

    monkeypatch.setattr(s.subprocess, "run", run)
    assert s.claude_launch() == [str(real)]
    assert asked["argv"] == ["C:\\node\\node.exe", str(tmp_path / "cc-shim.mjs"), "claude"]
    assert asked["env"]["CC_SHIM_PRINT_REAL"] == "1"


def test_windows_sees_through_npms_claude_cmd(tmp_path, monkeypatch):
    s = _load("summarize_npm", "scripts/summarize-session.py")
    cli = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("", encoding="utf-8")
    stub = tmp_path / "claude.cmd"
    stub.write_text('@IF EXIST "%dp0%\\node.exe" (\r\n  "%dp0%\\node.exe" "%dp0%\\node_modules\\@anthropic-ai'
                    '\\claude-code\\cli.js" %*\r\n)\r\n', encoding="utf-8")
    _win_path(monkeypatch, {"claude": str(stub), "node.exe": "C:\\node\\node.exe"})
    assert s.claude_launch()[0] == "C:\\node\\node.exe"
    assert s.claude_launch()[1].endswith("cli.js")


def test_windows_with_only_a_sh_stub_logs_and_skips(tmp_path, monkeypatch, capsys):
    s = _load("summarize_none", "scripts/summarize-session.py")
    stub = tmp_path / "claude"
    stub.write_text("#!/bin/sh\nexec node x\n", encoding="utf-8")
    _win_path(monkeypatch, {"claude": str(stub)})
    monkeypatch.setattr(s.subprocess, "run", lambda *a, **k: pytest.fail("must not spawn"))
    assert s.claude_launch() is None
    assert s.call_llm("hi", "haiku") == ""
    assert "summarize: skipped" in capsys.readouterr().err


def test_windows_hands_the_agent_over_as_a_file(tmp_path, monkeypatch):
    """cmd.exe cuts an argument at its first newline: the ~5 KB multi-line agent prompt never goes on argv."""
    s = _load("summarize_agents_file", "scripts/summarize-session.py")
    _win_path(monkeypatch, {"claude.exe": "C:\\bin\\claude.exe"})
    seen = {}

    def run(argv, **kw):
        path = argv[argv.index("--agents") + 1]
        seen.update(argv=argv, spec=json.loads(Path(path).read_text(encoding="utf-8")), path=path)
        return type("P", (), {"stdout": json.dumps({"result": "ok"}), "stderr": "", "returncode": 0})()

    monkeypatch.setattr(s.subprocess, "run", run)
    assert s.call_llm("hi", "haiku") == "ok"
    assert all("\n" not in a and len(a) < 1024 for a in seen["argv"])
    assert "observer" in seen["spec"]
    assert not Path(seen["path"]).exists()  # cleaned up


def _call(src, i):
    depth = 0
    for j in range(i, min(len(src), i + 2000)):
        depth += {"(": 1, ")": -1}.get(src[j], 0)
        if depth == 0:
            return src[i:j + 1]
    return src[i:i + 2000]


def windows_text_hazards(root: Path) -> list:
    """What turns into a CRLF file, a cp1252 decode or a lost replace on Windows: a text write
    without newline="\\n" (a Node reader or a hash sees the \\r), os.open without O_BINARY, a text
    subprocess without encoding=, and os.replace/rename onto a file a reader holds open."""
    skip = re.compile(r"(^|/)(tests?|evals?|fixtures?|vendor|node_modules|\.venv)/|(^|/)_brand\.py$|selftest")
    out = []
    for p in sorted(root.rglob("*.py")):
        if skip.search(p.relative_to(root.parent).as_posix()):
            continue
        src = p.read_text(encoding="utf-8")
        # (?<!formatter): click's HelpFormatter.write_text prints help, it writes no file
        for m in re.finditer(r"(?<!formatter)\.write_text\(|\bopen\(|subprocess\.(?:run|Popen|check_output)\(|\bos\.(?:replace|rename)\(|\.rename\(", src):
            call, name = _call(src, m.end() - 1), m.group(0)
            where = f"{p.relative_to(root.parent)}:{src.count(chr(10), 0, m.start()) + 1}"
            if name == ".write_text(" and "newline=" not in call:
                out.append(f"{where} write_text without newline=")
            elif name == "os.open(" or src[max(0, m.start() - 3):m.start()] == "os.":
                if "O_BINARY" not in call:
                    out.append(f"{where} os.open without O_BINARY")
            elif name == "open(" and re.match(r"""\([^,]+,\s*["'][wax]\+?["']""", call) and "newline=" not in call:
                out.append(f"{where} text open for write without newline=")
            elif name.startswith("subprocess") and re.search(r"text=True|universal_newlines=True", call) \
                    and "encoding=" not in call:
                out.append(f"{where} text subprocess without encoding=")
            elif name in ("os.replace(", "os.rename(", ".rename("):
                out.append(f"{where} {name} (the seam's replace_retry)")
    return out


@pytest.mark.parametrize("plugin", ["observer", "tracer", "wikilinks", "tag", "feed", "tool-results", "tasks",
                                    "agentic-engineering"])
def test_no_windows_text_hazards(plugin):
    """observer, its siblings in this port, and the satellites with no suite of their own. Skipped for a sibling not on disk."""
    root = PLUGIN.parent / plugin
    if not root.is_dir():
        pytest.skip(f"{plugin} not beside this checkout")
    assert windows_text_hazards(root) == []


def test_session_end_starts_the_summariser_through_the_seam(tmp_path, monkeypatch):
    se = _load("session_end_portable", "hooks/session-end.py")
    scratch = tmp_path / "s.jsonl"
    scratch.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(se, "capture_enabled", lambda cwd: True)
    monkeypatch.setattr(se, "is_substantive", lambda t, s: True)
    monkeypatch.setattr(se, "scratch_path", lambda sid: scratch)
    monkeypatch.setattr(se, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(se.shadow_issues, "enabled", lambda: False)  # the issues trial: its own test below
    started = []
    monkeypatch.setattr(se, "spawn_detached", lambda argv, **kw: started.append((argv, kw)))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "abc", "transcript_path": "/t.jsonl", "cwd": str(tmp_path)})))
    se.main()
    (argv, kw), = started
    assert argv[1:] == [str(se.SUMMARIZER), "--session", "abc", "--transcript", "/t.jsonl"]
    assert "start_new_session" not in kw, "the seam decides how to detach, per OS"
    assert (tmp_path / "abc.done").exists()


def test_session_end_starts_a_second_read_while_the_issues_trial_is_on(tmp_path, monkeypatch):
    se = _load("session_end_trial", "hooks/session-end.py")
    scratch = tmp_path / "s.jsonl"
    scratch.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(se, "capture_enabled", lambda cwd: True)
    monkeypatch.setattr(se, "is_substantive", lambda t, s: True)
    monkeypatch.setattr(se, "scratch_path", lambda sid: scratch)
    monkeypatch.setattr(se, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(se.shadow_issues, "enabled", lambda: True)
    started = []
    monkeypatch.setattr(se, "spawn_detached", lambda argv, **kw: started.append(argv))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "abc", "transcript_path": "/t.jsonl", "cwd": str(tmp_path)})))
    se.main()
    assert [a[1:] for a in started] == [
        [str(se.SUMMARIZER), "--session", "abc", "--transcript", "/t.jsonl"],
        [str(se.SUMMARIZER), "--session", "abc", "--transcript", "/t.jsonl", "--shadow"]]
