"""`ak ws move` against a whole fake home: a real git repo with an inside and an outside worktree,
Claude Code's chat history (with a look-alike folder that must stay put), ~/.claude.json, history,
settings, the tracer's index, the trace store, the memory's caches and a ~/.local/bin link.

The promise under test: after a move everything points at the new place, and after an undo — from
any step, even a hard crash half way — the home is byte-identical to before (the move's own record
and one lineage revert row aside). The gateway's files can never be part of a move.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parents[2]
VAULT_SRC = PLUGINS / "memory" / "src"
# ak declares no sibling (it builds alone): memory's handlers import the vault, reached by path here
SRC_PATH = os.pathsep.join(str(PLUGINS / p / "src") for p in ("memory", "vault"))
sys.path[:0] = SRC_PATH.split(os.pathsep)
# `ws move` hangs off the vault's `workspaces` group, and a move's handlers are memory's and the
# observer's: ak exported alone (the public kit) has none of them to drive.
needs_siblings = pytest.mark.skipif(not all((PLUGINS / p).is_dir() for p in ("vault", "memory", "observer")),
                                    reason="drives the vault's ws move and memory's/the observer's handlers; not beside ak")
from ak._brand import CLI, GATEWAY_DATA, HOME_DIR, cmd, env_name  # noqa: E402

B = HOME_DIR


def slug(p) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9]", "-", str(p))


def _git(*a, cwd=None, env=None):
    subprocess.run(["git", *a], cwd=cwd, env=env, check=True, capture_output=True)


def settings_of(repo, outer) -> dict:
    """A settings.json that names the folder as a whole value (additionalDirectories, an env path) and inside
    strings (the @ completion, the status line, a hook, a permission rule, an env command), beside a
    look-alike folder (`repo-wt`, which merely starts with the repo's text) that must stay as it is."""
    return {"permissions": {"additionalDirectories": [str(repo), "/else"], "allow": [f"Read(/{repo}/**)", "Bash(ls)"]},
            "env": {"X": str(repo / "app"), "LOOK_ALIKE": f"ls {outer}", "PLAIN": "1"},
            "fileSuggestion": {"type": "command", "command": f"python3 {repo}/bin/suggest"},
            "statusLine": {"type": "command", "command": f"{repo}/bin/suggest --dir={repo}"},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": f'sh -c "cd {repo} && {repo}/bin/suggest"'},
                                          {"type": "command", "command": f"ls {outer}/README.md '{repo}'"}]}]}}


@pytest.fixture
def home(tmp_path):
    h = (tmp_path / "home").resolve()
    h.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith((env_name(""), "CLAUDE", "GIT_")) and k not in ("VIRTUAL_ENV", "PYTHONPATH")}
    env.update({"HOME": str(h), env_name("NO_REEXEC"): "1", env_name("PLUGINS_DIR"): str(PLUGINS),
                env_name("OUTPUT"): "plain", env_name("GATEWAY_PORT"): "1", "PYTHONPATH": SRC_PATH,
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    repo = h / "x" / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "README.md").write_text("hi\n")
    (repo / "bin").mkdir()
    (repo / "bin" / "suggest").write_text("#!/bin/sh\n")
    _git("init", "-q", "-b", "main", str(repo), env=env)
    _git("add", ".", cwd=repo, env=env)
    _git("commit", "-qm", "one", cwd=repo, env=env)
    inner = repo / ".claude" / "worktrees" / "a"
    outer = h / "x" / "repo-wt" / "b"
    outer.parent.mkdir(parents=True)
    _git("worktree", "add", "-q", "-b", "wa", str(inner), cwd=repo, env=env)
    _git("worktree", "add", "-q", "-b", "wb", str(outer), cwd=repo, env=env)
    venv = repo / "tool" / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (venv / "bin" / "x").write_text(f"#!{venv}/bin/python\n")

    claude = h / ".claude"
    projects = claude / "projects"
    sess = {}
    for cwd in (repo, repo / "app", inner, outer):
        d = projects / slug(cwd)
        d.mkdir(parents=True)
        sid = f"{abs(hash(str(cwd))) % 10**8:08d}-0000-0000-0000-000000000000"
        lines = [{"type": "user", "cwd": str(cwd), "sessionId": sid, "message": {"content": "hi"}}]
        if cwd == repo:
            lines.append({"type": "relocated", "sessionId": sid, "relocatedCwd": str(inner)})
        (d / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
        (d / sid / "subagents").mkdir(parents=True)
        (d / sid / "subagents" / "agent-1.jsonl").write_text("{}\n")
        sess[str(cwd)] = (d, sid)
    (claude / "sessions").mkdir()
    (claude / "settings.json").write_text(json.dumps(settings_of(repo, outer), indent=2) + "\n")
    (claude / "history.jsonl").write_text("".join(json.dumps(x) + "\n" for x in [
        {"display": f"cd {repo}", "project": str(repo)}, {"display": "x", "project": "/else"},
        {"display": "y", "project": str(inner)}]))
    (h / ".claude.json").write_text(json.dumps({
        "projects": {str(repo): {"allowedTools": ["Bash"], "hasTrustDialogAccepted": True},
                     str(inner): {"x": 1}, "/else": {"y": 2}},
        "githubRepoPaths": {"o/repo": [str(repo)]}, "numStartups": 3}, indent=2) + "\n")

    idx_dir = claude / "plugins" / "data" / "conversation-index"
    idx_dir.mkdir(parents=True)
    con = sqlite3.connect(idx_dir / "index.db")
    con.executescript("""
      CREATE TABLE interactions (id TEXT PRIMARY KEY, project_hash TEXT NOT NULL, session_id TEXT, session_file TEXT);
      CREATE TABLE manifest (project_hash TEXT, session_filename TEXT, PRIMARY KEY (project_hash, session_filename));
      CREATE TABLE project_meta (project_hash TEXT PRIMARY KEY, project_path TEXT);
      CREATE TABLE i_files (iid TEXT, path TEXT, operation TEXT);""")
    for cwd, (d, sid) in sess.items():
        con.execute("INSERT INTO interactions VALUES (?,?,?,?)", (sid + "-1", d.name, sid, str(d / f"{sid}.jsonl")))
        con.execute("INSERT INTO manifest VALUES (?,?)", (d.name, f"{sid}.jsonl"))
        con.execute("INSERT INTO project_meta VALUES (?,?)", (d.name, cwd))
        con.execute("INSERT INTO i_files VALUES (?,?,?)", (sid + "-1", f"{cwd}/README.md", "Read"))
    con.commit()
    con.close()

    tr = claude / "brain-traces"
    tr.mkdir()
    con = sqlite3.connect(tr / "traces.db")
    con.executescript("""
      CREATE TABLE files (path TEXT PRIMARY KEY, pos INTEGER NOT NULL);
      CREATE TABLE spans (id TEXT PRIMARY KEY, blobs TEXT NOT NULL, file TEXT);
      CREATE TABLE runs (runId TEXT PRIMARY KEY, cwd TEXT, recordPath TEXT, journalPath TEXT, runDir TEXT);""")
    d, sid = sess[str(repo)]
    f = str(d / f"{sid}.jsonl")
    con.execute("INSERT INTO files VALUES (?,?)", (f, 10))
    con.execute("INSERT INTO spans VALUES (?,?,?)", ("s1", json.dumps({"input": f + ":10"}), f))
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?)", ("r1", str(repo), str(d / sid / "wf.json"), None, None))
    con.commit()
    con.close()

    kit = h / HOME_DIR  # layout 2: the notes in vault/, what can't be rebuilt in db/, what can in cache/
    state, cache = kit / "db", kit / "cache"
    key = cache / "workspaces" / str(repo).replace("/", "-")
    key.mkdir(parents=True)
    state.mkdir()
    (key / "memory-index.v4.json").write_text(json.dumps({"version": 4, "scope": str(repo)}))
    (state / "workspaces.json").write_text(json.dumps({"scopes": [{"path": str(repo), "name": "repo"}]}, indent=2))
    (cache / "names.json").write_text(json.dumps({"scopes": {str(repo): {"workspace": str(repo)}}}))
    (kit / "vault").mkdir()
    (kit / "vault" / "vault-manifest.json").write_text("{}")
    (kit / "layout").write_text(json.dumps({"layout": 2}))

    release = h / ".local" / "share" / "kit" / "release"
    release.parent.mkdir(parents=True)
    _git("clone", "-q", str(repo), str(release), env=env)
    (claude / "plugins").mkdir(exist_ok=True)
    (claude / "plugins" / "known_marketplaces.json").write_text(
        json.dumps({"kit": {"installLocation": str(release)}}, indent=2) + "\n")

    (h / ".local" / "bin").mkdir(parents=True)
    os.symlink(str(repo / "tool" / "run"), h / ".local" / "bin" / "run")

    return {"home": h, "env": env, "repo": repo, "release": release, "new": h / "x" / "kit", "inner": inner, "outer": outer,
            "claude": claude, "projects": projects, "sess": sess, "state": state, "cache": cache}


def ak(fx, *args, env=None):
    e = {**fx["env"], **(env or {})}
    p = subprocess.run([sys.executable, "-m", "ak.cli", *args], env=e, capture_output=True, text=True,
                       cwd=str(fx["home"]))
    return p


def js(p):
    assert p.stdout.strip(), p.stderr
    return json.loads(p.stdout)


SKIP = ("lineage.jsonl", ".DS_Store")


def snapshot(h: Path) -> dict:
    out = {}
    for root, dirs, files in os.walk(h):
        rel = os.path.relpath(root, h)
        if rel.startswith(os.path.join(HOME_DIR, "moves")):
            dirs[:] = []
            continue
        for n in dirs:
            p = os.path.join(root, n)
            if os.path.islink(p):
                out[os.path.relpath(p, h)] = "link:" + os.readlink(p)
        out[rel + "/"] = "dir"
        for n in files:
            p = os.path.join(root, n)
            r = os.path.relpath(p, h)
            if n in SKIP or n.endswith(("-wal", "-shm")) or r.endswith(os.path.join(".git", "index")):
                continue
            if os.path.islink(p):
                out[r] = "link:" + os.readlink(p)
            elif n.endswith(".db"):
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                out[r] = "\n".join(sorted(con.iterdump()))
                con.close()
            else:
                raw = open(p, "rb").read()
                try:  # the note at an old path names its own move id and time: compare where it points
                    tomb = json.loads(raw)
                    if isinstance(tomb, dict) and "moved_to" in tomb:
                        out[r] = "tombstone:" + tomb["moved_to"]
                        continue
                except ValueError:
                    pass
                out[r] = hashlib.sha256(raw).hexdigest()
    return out


def move_id(fx) -> str:
    rows = js(ak(fx, "moves", "ls", "--json"))
    return rows[0]["id"]


# ── the plan ────────────────────────────────────────────────────────────────
@needs_siblings
def test_dry_run_changes_nothing_and_leaves_the_look_alike(home):
    before = snapshot(home["home"])
    p = ak(home, "ws", "move", str(home["repo"]), str(home["new"]), "--dry-run", "--json")
    assert p.returncode == 0, p.stderr
    plan = js(p)
    assert snapshot(home["home"]) == before
    moved = [s["args"]["src"] for s in plan["steps"] if s["handler"] == "claude_code" and s["op"] == "rename"]
    assert sorted(os.path.basename(m) for m in moved) == sorted(
        slug(c) for c in (home["repo"], home["repo"] / "app", home["inner"]))
    assert not any(slug(home["outer"]) in m for m in moved)  # -…-repo-wt-b starts like -…-repo: not ours
    assert plan["facts"]["worktrees"] == 2


# ── apply ───────────────────────────────────────────────────────────────────
@needs_siblings
def test_move_moves_every_pointer_and_no_history(home):
    repo, new, projects = home["repo"], home["new"], home["projects"]
    d0, sid0 = home["sess"][str(repo)]
    transcript_before = (d0 / f"{sid0}.jsonl").read_bytes()
    p = ak(home, "ws", "move", str(repo), str(new), "--json")
    out = js(p)
    assert p.returncode == 0 and out["ok"], out
    # the folder, and a note where it was
    assert new.is_dir() and (new / "README.md").read_text() == "hi\n"
    tomb = json.loads(repo.read_text())
    assert tomb["moved_to"] == str(new)
    # git: both worktrees re-linked, the outside one stays where it was
    wl = subprocess.run(["git", "-C", str(new), "worktree", "list", "--porcelain"], capture_output=True, text=True,
                        env=home["env"]).stdout
    assert "prunable" not in wl and str(new / ".claude" / "worktrees" / "a") in wl and str(home["outer"]) in wl
    for wt in (new / ".claude" / "worktrees" / "a", home["outer"]):
        assert subprocess.run(["git", "-C", str(wt), "status", "--short"], capture_output=True,
                              env=home["env"]).returncode == 0
    # chat history: moved, transcripts untouched but for one appended relocated record
    nd = projects / slug(new)
    assert nd.is_dir() and not d0.exists()
    body = (nd / f"{sid0}.jsonl").read_bytes()
    assert body.startswith(transcript_before)
    assert json.loads(body.splitlines()[-1])["relocatedCwd"] == str(new / ".claude" / "worktrees" / "a")
    assert (nd / sid0 / "subagents" / "agent-1.jsonl").is_file()
    assert (projects / slug(home["outer"])).is_dir()  # the look-alike never moved
    # ~/.claude.json: keys and values, the rest untouched
    cj = json.loads((home["home"] / ".claude.json").read_text())
    assert cj["projects"][str(new)] == {"allowedTools": ["Bash"], "hasTrustDialogAccepted": True}
    assert str(repo) not in cj["projects"] and cj["projects"]["/else"] == {"y": 2}
    assert cj["githubRepoPaths"]["o/repo"] == [str(new)] and cj["numStartups"] == 3
    hist = [json.loads(x) for x in (home["claude"] / "history.jsonl").read_text().splitlines()]
    assert [h["project"] for h in hist] == [str(new), "/else", str(new / ".claude" / "worktrees" / "a")]
    assert hist[0]["display"] == f"cd {repo}"  # what was typed is history
    st = json.loads((home["claude"] / "settings.json").read_text())
    assert st["permissions"]["additionalDirectories"] == [str(new), "/else"] and st["env"]["X"] == str(new / "app")
    # the tracer's index: pointers moved, i_files (what a turn touched) untouched
    con = sqlite3.connect(home["claude"] / "plugins" / "data" / "conversation-index" / "index.db")
    hashes = {r[0] for r in con.execute("SELECT project_hash FROM interactions")}
    assert slug(new) in hashes and slug(repo) not in hashes and slug(home["outer"]) in hashes
    assert all(os.path.isfile(r[0]) for r in con.execute("SELECT session_file FROM interactions"))
    assert {r[0] for r in con.execute("SELECT path FROM i_files")} >= {f"{repo}/README.md"}
    con.close()
    con = sqlite3.connect(home["claude"] / "brain-traces" / "traces.db")
    f = con.execute("SELECT path FROM files").fetchone()[0]
    assert os.path.isfile(f)
    assert json.loads(con.execute("SELECT blobs FROM spans").fetchone()[0])["input"] == f + ":10"
    assert con.execute("SELECT cwd FROM runs").fetchone()[0] == str(repo)  # history
    con.close()
    # memory: the cache set aside (it rebuilds), the registries follow
    assert not (home["cache"] / "workspaces" / str(repo).replace("/", "-")).exists()
    assert json.loads((home["state"] / "workspaces.json").read_text())["scopes"][0]["path"] == str(new)
    assert str(new) in json.loads((home["cache"] / "names.json").read_text())["scopes"]
    # a clone elsewhere (the release clone) keeps pulling from the moved repo
    url = subprocess.run(["git", "-C", str(home["release"]), "remote", "get-url", "origin"], capture_output=True,
                         text=True, env=home["env"]).stdout.strip()
    assert url == str(new)
    assert subprocess.run(["git", "-C", str(home["release"]), "fetch", "-q"], capture_output=True,
                          env=home["env"]).returncode == 0
    # the `ak` CLI's ~/agentic-kit fallback follows the recorded move
    sys.path.insert(0, str(VAULT_SRC))
    from ak.move import lineage
    assert lineage.moved(str(repo / "gateway"), str(home["state"] / "lineage.jsonl")) == str(new / "gateway")
    # links, venvs, lineage
    assert os.readlink(home["home"] / ".local" / "bin" / "run") == str(new / "tool" / "run")
    assert not (new / "tool" / ".venv").exists()
    rows = [json.loads(x) for x in (home["state"] / "lineage.jsonl").read_text().splitlines()]
    assert {(r["kind"], r["from"], r["to"]) for r in rows} == {("path", str(repo), str(new)), ("label", "repo", "kit")}


@needs_siblings
def test_undo_puts_back_every_byte(home):
    before = snapshot(home["home"])
    assert ak(home, "ws", "move", str(home["repo"]), str(home["new"])).returncode == 0
    assert snapshot(home["home"]) != before
    p = ak(home, "moves", "undo", move_id(home))
    assert p.returncode == 0, p.stdout + p.stderr
    assert snapshot(home["home"]) == before
    rows = [json.loads(x) for x in (home["state"] / "lineage.jsonl").read_text().splitlines()]
    assert rows[-1]["revert"] is True
    sys.path.insert(0, str(VAULT_SRC))
    from ak.move import lineage
    assert lineage.rows(str(home["state"] / "lineage.jsonl")) == []  # a reverted move no longer counts


# ── settings: a path as a whole value AND inside a command string ───────────
@needs_siblings
def test_the_dry_run_lists_the_settings_edits_by_key(home):
    plan = js(ak(home, "ws", "move", str(home["repo"]), str(home["new"]), "--dry-run", "--json"))
    steps = [s for s in plan["steps"] if s["op"] == "json_remap" and s["args"]["file"].endswith("settings.json")]
    assert len(steps) == 1 and steps[0]["args"]["embedded"] is True
    for key in ("permissions.additionalDirectories[0]", "permissions.allow[0]", "env.X", "fileSuggestion.command",
                "statusLine.command", "hooks.Stop[0].hooks[0].command", "hooks.Stop[0].hooks[1].command"):
        assert key in steps[0]["name"], (key, steps[0]["name"])
    assert "env.LOOK_ALIKE" not in steps[0]["name"]  # names only the look-alike: nothing to edit there
    assert plan["facts"]["settings_paths"] == 7
    plain = ak(home, "ws", "move", str(home["repo"]), str(home["new"]), "--dry-run")
    assert "fileSuggestion.command" in plain.stdout


@needs_siblings
def test_settings_commands_follow_the_move_and_the_look_alike_stays(home):
    repo, new, outer = home["repo"], home["new"], home["outer"]
    local = home["claude"] / "settings.local.json"
    local.write_text(json.dumps({"statusLine": {"command": f"{repo}/bin/suggest"}, "env": {"D": f"{outer}/y"}},
                                indent=2) + "\n")
    p = ak(home, "ws", "move", str(repo), str(new), "--json")
    assert p.returncode == 0 and js(p)["ok"], p.stdout
    st = json.loads((home["claude"] / "settings.json").read_text())
    assert st["fileSuggestion"]["command"] == f"python3 {new}/bin/suggest"
    assert st["statusLine"]["command"] == f"{new}/bin/suggest --dir={new}"
    hooks = st["hooks"]["Stop"][0]["hooks"]
    assert hooks[0]["command"] == f'sh -c "cd {new} && {new}/bin/suggest"'
    assert hooks[1]["command"] == f"ls {outer}/README.md '{new}'"  # the look-alike beside a quoted old folder
    assert st["permissions"]["allow"] == [f"Read(/{new}/**)", "Bash(ls)"]
    assert st["permissions"]["additionalDirectories"] == [str(new), "/else"]
    assert st["env"] == {"X": str(new / "app"), "LOOK_ALIKE": f"ls {outer}", "PLAIN": "1"}
    lo = json.loads(local.read_text())
    assert lo == {"statusLine": {"command": f"{new}/bin/suggest"}, "env": {"D": f"{outer}/y"}}
    assert str(repo) not in json.dumps(st).replace(str(outer), "")  # nothing of the old folder is left


@needs_siblings
def test_undo_puts_the_settings_commands_back_byte_for_byte(home):
    files = [home["claude"] / "settings.json", home["claude"] / "settings.local.json"]
    files[1].write_text(json.dumps({"statusLine": {"command": f"{home['repo']}/bin/suggest"}}, indent=2) + "\n")
    before = [f.read_bytes() for f in files]
    assert ak(home, "ws", "move", str(home["repo"]), str(home["new"])).returncode == 0
    assert [f.read_bytes() for f in files] != before
    u = ak(home, "moves", "undo", move_id(home))
    assert u.returncode == 0, u.stdout + u.stderr
    assert [f.read_bytes() for f in files] == before


@needs_siblings
def test_undo_leaves_a_command_edited_after_the_move(home):
    assert ak(home, "ws", "move", str(home["repo"]), str(home["new"])).returncode == 0
    f = home["claude"] / "settings.json"
    st = json.loads(f.read_text())
    st["fileSuggestion"]["command"] = "my-own-completion"
    st["later"] = {"command": "x"}
    f.write_text(json.dumps(st, indent=2) + "\n")
    assert ak(home, "moves", "undo", move_id(home)).returncode == 0
    back = json.loads(f.read_text())
    assert back["fileSuggestion"]["command"] == "my-own-completion" and back["later"] == {"command": "x"}
    assert back["statusLine"]["command"] == f"{home['repo']}/bin/suggest --dir={home['repo']}"


@pytest.mark.parametrize("text, want", [
    ("python3 /old/bin/x", "python3 /new/bin/x"),
    ("/old", "/new"),
    ("cd /old && make", "cd /new && make"),
    ("ls '/old' \"/old/x\" /old", "ls '/new' \"/new/x\" /new"),
    ("--dir=/old/x:/usr/bin", "--dir=/new/x:/usr/bin"),
    ("Read(//old/**)", "Read(//new/**)"),
    ("/old\nnext", "/new\nnext"),
    ("a /old-wt/x /old.bak /oldest /old_x", "a /old-wt/x /old.bak /oldest /old_x"),  # look-alikes: other folders
    ("/opt/old /x/old/y ./old ~/old /a/b/old", "/opt/old /x/old/y ./old ~/old /a/b/old"),  # `/old` deeper is not it
    ("/old/../old-wt", "/new/../old-wt"),
])
def test_the_folder_is_found_inside_a_string_at_a_path_boundary(text, want):
    sys.path.insert(0, str(VAULT_SRC))
    from ak.move import ops
    assert ops._embed_sub(text, "/old", "/new") == want
    assert ops._embed_sub(text, "/old/", "/new") == want  # a trailing slash on OLD is the same folder
    assert ops._embed_sub("x " + text, "/old", "/new\\1") == "x " + want.replace("/new", "/new\\1")  # NEW is no regex


@pytest.mark.parametrize("result_kept", [True, False], ids=["result-journaled", "crashed-mid-step"])
def test_json_remap_of_command_strings_reverts_to_the_exact_bytes(tmp_path, result_kept):
    sys.path.insert(0, str(VAULT_SRC))
    from ak.move import ops
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"a": {"command": "python3 /old/bin/x --at=/old", "n": 3}, "b": ["/old", "/old-wt/y"],
                             "ünï": "cd '/old' && ls /old-wt"}, indent=2, ensure_ascii=False) + "\n")
    before = f.read_bytes()
    a = {"file": str(f), "old": "/old", "new": "/new", "keys": False, "embedded": True}
    ctx = {"dir": str(tmp_path / "move"), "id": "t"}
    r = ops.json_remap_apply(a, ctx)
    assert json.loads(f.read_text()) == {"a": {"command": "python3 /new/bin/x --at=/new", "n": 3},
                                        "b": ["/new", "/old-wt/y"], "ünï": "cd '/new' && ls /old-wt"}
    assert [c["before"] for c in r["changes"]] == ["python3 /old/bin/x --at=/old", "/old", "cd '/old' && ls /old-wt"]
    ops.json_remap_revert(a, r if result_kept else None, ctx)
    assert f.read_bytes() == before


# ── settings: where a path ends, and the ~/ form of the folder ───────────────
def _ops():
    sys.path.insert(0, str(VAULT_SRC))
    from ak.move import ops
    return ops


@pytest.mark.parametrize("text, want", [
    ("cd /old;make", "cd /new;make"),
    ("PATH=/old:/usr/bin", "PATH=/new:/usr/bin"),
    ("$(cat /old)", "$(cat /new)"),
    ("ls /old|wc", "ls /new|wc"),
    ("cd /old&make", "cd /new&make"),
    ("--roots=/old,/else", "--roots=/new,/else"),
    ("cat /old>out.log", "cat /new>out.log"),
    ("echo `ls /old`", "echo `ls /new`"),
    ("cd /old;ls /old/x:/old|/old&/old,/old>/old)/old", "cd /new;ls /new/x:/new|/new&/new,/new>/new)/new"),
    ("/old.bak;/oldest:/old-wt)/old_x", "/old.bak;/oldest:/old-wt)/old_x"),  # a longer name after it: another folder
])
def test_a_path_ends_at_a_shell_separator_too(text, want):
    assert _ops()._embed_sub(text, "/old", "/new") == want


HOMES = ["/h"]


@pytest.mark.parametrize("text, want", [
    (f"~/{B}", "~/.kit"),  # a whole value keeps its ~ (it is not expanded)
    (f"~/{B}/bin/x", "~/.kit/bin/x"),
    (f"cd ~/{B} && make", "cd ~/.kit && make"),
    (f"ls '~/{B}' \"~/{B}/x\" ~/{B}", "ls '~/.kit' \"~/.kit/x\" ~/.kit"),
    (f"ROOTS=~/{B}:/usr/bin", "ROOTS=~/.kit:/usr/bin"),
    (f"cd ~/{B};make", "cd ~/.kit;make"),
    (f"$(cat ~/{B})", "$(cat ~/.kit)"),
    (f"a|~/{B}|b", "a|~/.kit|b"),
    (f"a&~/{B}&b", "a&~/.kit&b"),
    (f"x,~/{B},y", "x,~/.kit,y"),
    (f"cat ~/{B}>out", "cat ~/.kit>out"),
    (f"echo `ls ~/{B}`", "echo `ls ~/.kit`"),
    (f"~/{B} /h/{B}", "~/.kit /h/.kit"),  # the absolute form beside it moves too
    (f"~/{B}-archive ~/{B}x ~/{B}.bak ~/{B}_x ~/{B}2/y", f"~/{B}-archive ~/{B}x ~/{B}.bak ~/{B}_x ~/{B}2/y"),
    (f"x~/{B} ~~/{B} -~/{B} a.~/{B} ~/sub/{B} ~/{B}-wt/{B}", f"x~/{B} ~~/{B} -~/{B} a.~/{B} ~/sub/{B} ~/{B}-wt/{B}"),
    (f"~ ~/ $HOME/{B}", f"~ ~/ $HOME/{B}"),  # only the ~/ form is read: not a bare ~, not $HOME
])
def test_the_home_form_of_the_folder_follows_and_keeps_its_tilde(text, want):
    ops = _ops()
    assert ops._embed_sub(text, f"/h/{B}", "/h/.kit", HOMES) == want
    assert ops._embed_sub(text, f"/h/{B}/", "/h/.kit/", HOMES) == want  # a trailing slash is the same folder
    assert ops._embed_sub(text, f"/h/{B}", "/h/.kit", ["/else", "/h/"]) == want  # the first home that holds it


def test_the_home_form_is_read_only_where_the_folder_is_under_a_home():
    ops = _ops()
    assert ops._embed_sub(f"~/{B} /else/{B}", f"/else/{B}", "/else/.kit", HOMES) == f"~/{B} /else/.kit"
    assert ops._embed_sub(f"~/{B} /h/{B}", f"/h/{B}", "/h/.kit") == f"~/{B} /h/.kit"  # no home given: as before
    assert ops._embed_sub(f"~/{B}", "/h", "/g", HOMES) == f"~/{B}"  # the home itself has no ~/ form here
    assert ops.tilde_of(f"/h/{B}/x", HOMES) == f"~/{B}/x" and ops.tilde_of("/h", HOMES) == ""


def test_a_folder_moved_out_of_home_is_written_absolute_in_place_of_its_tilde():
    ops = _ops()
    assert ops._embed_sub(f"~/{B} cd ~/{B};ls /h/{B}", f"/h/{B}", "/opt/ak", HOMES) == "/opt/ak cd /opt/ak;ls /opt/ak"
    assert ops._embed_sub(f"~/{B}", f"/h/{B}", "/h/deep/er/.kit", HOMES) == "~/deep/er/.kit"  # still under it: still a ~/


def _tilde_settings(home):
    """A settings.local.json that names the repo ONLY as ~/x/repo — no absolute form of it anywhere in the file —
    beside look-alikes that merely start with the same text. (repo-wt/b and repox exist on disk.)"""
    (home["home"] / "x" / "repox").mkdir()
    f = home["claude"] / "settings.local.json"
    f.write_text(json.dumps({
        "env": {"WIKILINKS_ROOTS": "~/x/repo", "ROOTS": "~/x/repo:~/x/repo/app:/usr/bin",
                "LOOK": "~/x/repo-wt/b", "LOOK2": "~/x/repox"},
        "statusLine": {"type": "command", "command": "cd ~/x/repo;bin/suggest $(cat ~/x/repo/README.md) `ls ~/x/repo` ~/x/repo-wt/b"},
        "permissions": {"additionalDirectories": ["~/x/repo"]}}, indent=2) + "\n")
    return f


@needs_siblings
def test_the_dry_run_lists_the_home_form_edits_by_key(home):
    _tilde_settings(home)
    plan = js(ak(home, "ws", "move", str(home["repo"]), str(home["new"]), "--dry-run", "--json"))
    steps = [s for s in plan["steps"] if s["op"] == "json_remap" and s["args"]["file"].endswith("settings.local.json")]
    assert len(steps) == 1, "a file that names only the ~/ form is still a step"
    name = steps[0]["name"]
    for key in ("env.WIKILINKS_ROOTS", "env.ROOTS", "statusLine.command", "permissions.additionalDirectories[0]"):
        assert key in name, (key, name)
    assert "env.LOOK" not in name  # env.LOOK and env.LOOK2 name only look-alikes
    assert plan["facts"]["settings_paths"] == 7 + 4


@needs_siblings
def test_the_home_form_follows_the_move_and_the_look_alikes_stay(home):
    f = _tilde_settings(home)
    p = ak(home, "ws", "move", str(home["repo"]), str(home["new"]), "--json")
    assert p.returncode == 0 and js(p)["ok"], p.stdout
    lo = json.loads(f.read_text())
    assert lo["env"] == {"WIKILINKS_ROOTS": "~/x/kit", "ROOTS": "~/x/kit:~/x/kit/app:/usr/bin",
                         "LOOK": "~/x/repo-wt/b", "LOOK2": "~/x/repox"}
    assert lo["statusLine"]["command"] == "cd ~/x/kit;bin/suggest $(cat ~/x/kit/README.md) `ls ~/x/kit` ~/x/repo-wt/b"
    assert lo["permissions"]["additionalDirectories"] == ["~/x/kit"]
    st = json.loads((home["claude"] / "settings.json").read_text())  # the absolute forms moved as before
    assert st["fileSuggestion"]["command"] == f"python3 {home['new']}/bin/suggest"


@needs_siblings
def test_a_home_form_moved_out_of_home_becomes_the_absolute_path_and_undoes_exactly(home):
    f = _tilde_settings(home)
    outside = home["home"].parent / "outside" / "kit"
    outside.parent.mkdir()
    files = [f, home["claude"] / "settings.json"]
    before = [x.read_bytes() for x in files]
    p = ak(home, "ws", "move", str(home["repo"]), str(outside), "--json")
    assert p.returncode == 0 and js(p)["ok"], p.stdout
    lo = json.loads(f.read_text())
    assert lo["env"]["WIKILINKS_ROOTS"] == str(outside) and lo["env"]["ROOTS"] == f"{outside}:{outside}/app:/usr/bin"
    assert lo["permissions"]["additionalDirectories"] == [str(outside)]
    u = ak(home, "moves", "undo", move_id(home))
    assert u.returncode == 0, u.stdout + u.stderr
    assert [x.read_bytes() for x in files] == before


@needs_siblings
def test_undo_puts_the_home_form_back_byte_for_byte(home):
    f = _tilde_settings(home)
    files = [f, home["claude"] / "settings.json"]
    before = [x.read_bytes() for x in files]
    assert ak(home, "ws", "move", str(home["repo"]), str(home["new"])).returncode == 0
    assert [x.read_bytes() for x in files] != before
    u = ak(home, "moves", "undo", move_id(home))
    assert u.returncode == 0, u.stdout + u.stderr
    assert [x.read_bytes() for x in files] == before


@needs_siblings
def test_undo_leaves_a_home_form_edited_after_the_move(home):
    f = _tilde_settings(home)
    assert ak(home, "ws", "move", str(home["repo"]), str(home["new"])).returncode == 0
    lo = json.loads(f.read_text())
    lo["env"]["WIKILINKS_ROOTS"] = "~/mine"
    lo["later"] = "~/x/kit"
    f.write_text(json.dumps(lo, indent=2) + "\n")
    assert ak(home, "moves", "undo", move_id(home)).returncode == 0
    back = json.loads(f.read_text())
    assert back["env"]["WIKILINKS_ROOTS"] == "~/mine" and back["later"] == "~/x/kit"
    assert back["env"]["ROOTS"] == "~/x/repo:~/x/repo/app:/usr/bin"  # the ones left as the move wrote them go back
    assert back["permissions"]["additionalDirectories"] == ["~/x/repo"]


@pytest.mark.parametrize("result_kept", [True, False], ids=["result-journaled", "crashed-mid-step"])
def test_json_remap_of_home_forms_reverts_to_the_exact_bytes(tmp_path, result_kept):
    ops = _ops()
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"env": {"R": f"~/{B}", "S": f"cd ~/{B};make /h/{B}>x", "T": f"~/{B}-archive"},
                             "a": [f"/h/{B}", f"~/{B}x"]}, indent=2, ensure_ascii=False) + "\n")
    before = f.read_bytes()
    a = {"file": str(f), "old": f"/h/{B}", "new": "/h/.kit", "keys": False, "embedded": True, "homes": ["/h"]}
    ctx = {"dir": str(tmp_path / "move"), "id": "t"}
    r = ops.json_remap_apply(a, ctx)
    assert json.loads(f.read_text()) == {"env": {"R": "~/.kit", "S": "cd ~/.kit;make /h/.kit>x", "T": f"~/{B}-archive"},
                                        "a": ["/h/.kit", f"~/{B}x"]}
    ops.json_remap_revert(a, r if result_kept else None, ctx)
    assert f.read_bytes() == before


@needs_siblings
def test_undo_and_resume_from_a_crash_at_every_step(home):
    before = snapshot(home["home"])
    plan = js(ak(home, "ws", "move", str(home["repo"]), str(home["new"]), "--dry-run", "--json"))
    n = len(plan["steps"])
    after = None
    for k in range(1, n + 1):
        p = ak(home, "ws", "move", str(home["repo"]), str(home["new"]), env={env_name("MOVE_CRASH_AFTER"): str(k)})
        mid = move_id(home)
        if k < n:
            assert p.returncode != 0, f"crash after step {k} went unnoticed"
            r = ak(home, "moves", "resume", mid, env={env_name("MOVE_CRASH_AFTER"): ""})
            assert r.returncode == 0, (k, r.stdout, r.stderr)
        state = {k2: v for k2, v in snapshot(home["home"]).items()}
        if after is None:
            after = state
        assert state == after, f"resume after a crash at step {k} ended somewhere else"
        u = ak(home, "moves", "undo", mid)
        assert u.returncode == 0, (k, u.stdout, u.stderr)
        assert snapshot(home["home"]) == before, f"undo after a crash at step {k} left a trace"


def test_move_ids_are_unique_and_in_creation_order_within_one_second(monkeypatch):
    """The same move started twice in one second (an undo, then a re-run) once got one id — one journal
    folder, the second run skipping steps — and `moves list`, sorted by id, could name either."""
    from ak.move import plan as P
    ticks = iter([1_700_000_000.010, 1_700_000_000.020, 1_700_000_000.990])
    monkeypatch.setattr(P.time, "time", lambda: next(ticks))
    ids = [P.new_id("/a/old", "/a/new") for _ in range(3)]
    assert len(set(ids)) == 3 and ids == sorted(ids), ids
    assert len({i.split("-")[0] for i in ids}) == 1, "all three fall in the same second"


@needs_siblings
def test_a_failing_step_undoes_itself(home):
    before_settings = (home["claude"] / "settings.json").read_text()
    plugins = home["claude"] / "plugins" / "known_marketplaces.json"
    plugins.write_text(f'{{"m": {{"installLocation": "{home["repo"]}"}} BROKEN')  # mentions the path, will not parse
    before = snapshot(home["home"])
    p = ak(home, "ws", "move", str(home["repo"]), str(home["new"]), "--json")
    out = js(p)
    assert p.returncode == 1 and not out["ok"] and out["result"]["undone"]["undone"] > 0
    assert snapshot(home["home"]) == before
    assert (home["claude"] / "settings.json").read_text() == before_settings


@needs_siblings
def test_undo_keeps_what_was_written_after_the_move(home):
    assert ak(home, "ws", "move", str(home["repo"]), str(home["new"])).returncode == 0
    cj = home["home"] / ".claude.json"
    d = json.loads(cj.read_text())
    d["projects"]["/later"] = {"z": 1}
    cj.write_text(json.dumps(d, indent=2) + "\n")
    with open(home["claude"] / "history.jsonl", "a") as f:
        f.write(json.dumps({"display": "later", "project": "/later"}) + "\n")
    assert ak(home, "moves", "undo", move_id(home)).returncode == 0
    d = json.loads(cj.read_text())
    assert d["projects"]["/later"] == {"z": 1} and str(home["repo"]) in d["projects"]
    assert "later" in (home["claude"] / "history.jsonl").read_text()


# ── refusals ────────────────────────────────────────────────────────────────
@needs_siblings
@pytest.mark.parametrize("case", ["claudecode", "exists", "slug", "gateway", "inside"])
def test_refusals_change_nothing(home, case):
    env, new = {}, home["new"]
    if case == "claudecode":
        env = {"CLAUDECODE": "1"}
    elif case == "exists":
        new.mkdir()
    elif case == "slug":
        (home["projects"] / slug(new)).mkdir()
    elif case == "gateway":
        new = home["home"] / ".local" / "share" / GATEWAY_DATA
        new.parent.mkdir(parents=True, exist_ok=True)
    elif case == "inside":
        new = home["repo"] / "sub"
    before = snapshot(home["home"])
    p = ak(home, "ws", "move", str(home["repo"]), str(new), env=env)
    assert p.returncode == 1, p.stdout
    assert snapshot(home["home"]) == before


def test_a_live_session_blocks_the_move(home):
    sys.path.insert(0, str(VAULT_SRC))
    from ak.move import plan as P
    (home["claude"] / "sessions" / f"{os.getpid()}.json").write_text(json.dumps({"cwd": str(home["repo"])}))
    m = P.Move(id="t", old=str(home["repo"]), new=str(home["new"]), claude=str(home["claude"]),
               home=str(home["home"] / HOME_DIR))
    assert any("still open" in f.what for f in P.quiesce(m, is_claude=lambda pid: True))


def test_the_gateway_guard_refuses_any_op_on_its_files(tmp_path, monkeypatch):
    sys.path.insert(0, str(VAULT_SRC))
    from ak.move import ops
    monkeypatch.setenv("HOME", str(tmp_path))
    for p in (tmp_path / ".local/share" / GATEWAY_DATA / "keeper.mts", tmp_path / "Library/LaunchAgents/x.plist",
              tmp_path / ".config/cc-shim/conf.d/05-brain-gateway.sh", tmp_path / ".local"):
        with pytest.raises(ops.Refused):
            ops.guard(str(p))
    ops.guard(str(tmp_path / "x" / "repo"))


# ── adopt: moved by hand, found by doctor, reattached ──────────────────────
@needs_siblings
def test_doctor_finds_a_hand_move_and_adopt_reattaches_it(home):
    elsewhere = home["home"] / "y" / "repo"
    elsewhere.parent.mkdir()
    os.rename(home["repo"], elsewhere)
    findings = js(ak(home, "doctor", "--json"))
    fix = [f["fix"] for f in findings if f["check"] == "sessions" and f["level"] == "fail"]
    assert fix == [f"{cmd('ws move')} --adopt {home['repo']} {elsewhere}"], findings
    p = ak(home, "ws", "move", "--adopt", str(home["repo"]), str(elsewhere), "--json")
    assert p.returncode == 0 and js(p)["ok"], p.stdout + p.stderr
    assert (home["projects"] / slug(elsewhere)).is_dir()
    assert str(elsewhere) in json.loads((home["home"] / ".claude.json").read_text())["projects"]
    wl = subprocess.run(["git", "-C", str(elsewhere), "worktree", "list", "--porcelain"], capture_output=True,
                        text=True, env=home["env"]).stdout
    assert "prunable" not in wl
    after = js(ak(home, "doctor", "--json"))
    assert not [f for f in after if f["check"] == "sessions" and f["level"] == "fail"]


def test_doctor_names_a_plugin_its_marketplace_dropped(home):
    mp = home["home"] / "mkt"
    (mp / ".claude-plugin").mkdir(parents=True)
    (mp / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"plugins": [{"name": "new-name"}]}))
    (home["claude"] / "plugins" / "known_marketplaces.json").write_text(json.dumps({"m": {"installLocation": str(mp)}}))
    st = json.loads((home["claude"] / "settings.json").read_text())
    st["enabledPlugins"] = {"old-name@m": True, "new-name@m": True}
    (home["claude"] / "settings.json").write_text(json.dumps(st))
    findings = js(ak(home, "doctor", "--json"))
    bad = [f for f in findings if f["check"] == "plugins" and f["level"] == "fail"]
    assert len(bad) == 1 and "old-name@m" in bad[0]["what"] and "still enabled" in bad[0]["what"]


def test_doctor_says_a_home_left_on_layout_1_and_names_the_migration(home):
    def layout():
        return [f for f in js(ak(home, "doctor", "--json")) if f["check"] == "layout"]
    assert [f["level"] for f in layout()] == ["pass"]
    kit = home["home"] / HOME_DIR
    left = kit / ".brain-state"  # a layout-1 folder left at the root (brand: historical)
    left.mkdir()
    bad = layout()
    assert bad[0]["level"] == "fail" and "layout 1" in bad[0]["what"] and left.name in bad[0]["what"]
    assert "scripts/migrate/4-home-layout.py" in bad[0]["fix"]
    left.rmdir()
    (kit / "layout").unlink()  # never cut over
    assert layout()[0]["level"] == "fail" and "4-home-layout.py" in layout()[0]["fix"]


def _settings_findings(home):
    return [f for f in js(ak(home, "doctor", "--json")) if f["check"] == "settings"]


def _live_settings(home):
    """The fixture's settings, with the foreign `/else` (which the move tests need) swapped for a folder that exists."""
    f = home["claude"] / "settings.json"
    f.write_text(f.read_text().replace('"/else"', '"/usr"'))


def test_doctor_passes_settings_whose_command_paths_are_live(home):
    _live_settings(home)
    f = _settings_findings(home)
    assert [x["level"] for x in f] == ["pass"], f
    # 2 directories, 2 env values, and the paths inside the 4 commands — a scan that saw none would pass too
    assert f[0]["what"] == "settings.json: 11 path(s), all present", f


def test_doctor_flags_a_dead_path_inside_a_command_with_its_key(home):
    _live_settings(home)
    gone = home["home"] / "gone"  # what a move that left the command behind looks like
    f = home["claude"] / "settings.json"
    st = json.loads(f.read_text())
    st["fileSuggestion"]["command"] = f"python3 {gone}/plugins/vault/bin/suggest"
    st["hooks"]["Stop"][0]["hooks"][0]["command"] = f'sh -c "cd {home["repo"]} && {gone}/hook.sh" > /tmp/hook.log'
    st["statusLine"]["command"] = f"cat {gone}/*.txt ${{ROOT}}/x https://example.com/{gone.name}/y"
    f.write_text(json.dumps(st, indent=2) + "\n")
    found = _settings_findings(home)
    assert [x["level"] for x in found] == ["fail"], found
    assert "3 path(s) that do not exist" in found[0]["what"]
    assert sorted(found[0]["examples"]) == sorted([
        f"fileSuggestion.command: {gone}/plugins/vault/bin/suggest",
        f"hooks.Stop[0].hooks[0].command: {gone}/hook.sh",
        f"statusLine.command: {gone}/"])
    plain = ak(home, "doctor")
    assert "settings: fileSuggestion.command: " in plain.stdout and plain.returncode == 1


def test_doctor_reads_settings_local_and_a_home_path_too(home):
    _live_settings(home)
    (home["claude"] / "settings.local.json").write_text(json.dumps(
        {"env": {"A": "~/nowhere/x", "B": "/usr/bin:/bin"}, "apiKeyHelper": "~/.nope/helper --x"}))
    found = _settings_findings(home)
    bad = [x for x in found if x["level"] == "fail"]
    assert len(bad) == 1 and bad[0]["what"].startswith("settings.local.json names 2 path(s)"), found
    assert sorted(bad[0]["examples"]) == ["apiKeyHelper: ~/.nope/helper", "env.A: ~/nowhere/x"]


def test_doctor_flags_a_dead_home_path_inside_a_command(home):
    _live_settings(home)
    (home["claude"] / "settings.local.json").write_text(json.dumps({
        "statusLine": {"type": "command", "command": "cd ~/gone;make"},
        "fileSuggestion": {"type": "command", "command": "echo $(cat ~/gone3/f) ~/x/repo/README.md"},
        "apiKeyHelper": "`~/gone4/bin/x`",
        "env": {"E": "PATH=~/gone2/bin:/usr/bin", "LIVE": "~/x/repo", "QUOTED": "cd '~/gone5/a b' && ls ~/x/repo"}}))
    bad = [x for x in _settings_findings(home) if x["level"] == "fail"]
    assert len(bad) == 1 and bad[0]["what"].startswith("settings.local.json names 5 path(s)"), bad
    assert sorted(bad[0]["examples"]) == ["apiKeyHelper: ~/gone4/bin/x", "env.E: ~/gone2/bin", "env.QUOTED: ~/gone5/a b",
                                          "fileSuggestion.command: ~/gone3/f", "statusLine.command: ~/gone"]


def test_doctor_passes_a_home_path_inside_a_command_that_is_there(home):
    _live_settings(home)
    (home["claude"] / "settings.local.json").write_text(json.dumps(
        {"statusLine": {"command": "cd ~/x/repo;make $(cat ~/x/repo/README.md) `ls ~/x/repo`"}, "env": {"W": "~/x/repo"}}))
    f = [x for x in _settings_findings(home) if "settings.local.json" in x["what"]]
    assert [x["level"] for x in f] == ["pass"] and "4 path(s), all present" in f[0]["what"], f


@needs_siblings
def test_a_move_leaves_doctor_settings_clean(home):
    _live_settings(home)
    assert ak(home, "ws", "move", str(home["repo"]), str(home["new"])).returncode == 0
    assert [x["level"] for x in _settings_findings(home)] == ["pass"]  # the commands follow to where the files are


@needs_siblings
def test_a_move_leaves_doctor_clean_of_home_forms_too(home):
    _live_settings(home)
    _tilde_settings(home)
    before = [x["level"] for x in _settings_findings(home)]
    assert before == ["pass", "pass"], _settings_findings(home)
    assert ak(home, "ws", "move", str(home["repo"]), str(home["new"])).returncode == 0
    assert [x["level"] for x in _settings_findings(home)] == ["pass", "pass"]


@needs_siblings
def test_finalize_frees_the_old_path(home):
    assert ak(home, "ws", "move", str(home["repo"]), str(home["new"])).returncode == 0
    mid = move_id(home)
    assert ak(home, "moves", "finalize", mid).returncode == 0
    assert not home["repo"].exists()
    assert ak(home, "moves", "undo", mid).returncode == 1  # finalized: no way back, and it says so


# ── lineage: the record readers follow ──────────────────────────────────────
def test_lineage_names_are_transitive_and_reverts_do_not_count(tmp_path):
    sys.path.insert(0, str(VAULT_SRC))
    from ak.move import lineage as L
    f = str(tmp_path / "l.jsonl")
    L.append({"move": "a", "kind": "label", "from": "orig", "to": "kit"}, f)
    L.append({"move": "b", "kind": "label", "from": "kit", "to": "cur"}, f)
    L.append({"move": "c", "kind": "label", "from": "cur", "to": "zz"}, f)
    L.append({"move": "c", "revert": True}, f)
    L.append({"move": "a", "kind": "path", "from": "/h/ak", "to": "/h/kit"}, f)
    assert L.names("label", "cur", f) == ["cur", "kit", "orig"]
    assert L.current("label", "orig", f) == "cur"
    assert L.path_names("/h/kit/src/x.py", f) == ["/h/kit/src/x.py", "/h/ak/src/x.py"]
    assert L.path_names("/h/kitchen", f) == ["/h/kitchen"]  # a '/' boundary, not a prefix
    assert L.rows(str(tmp_path / "none.jsonl")) == []


@needs_siblings
def test_observer_and_harvest_read_a_moved_label_with_the_old_alias(tmp_path, monkeypatch):
    home = tmp_path / HOME_DIR
    (home / "db").mkdir(parents=True)
    (home / "db" / "lineage.jsonl").write_text(
        json.dumps({"v": 1, "move": "m", "kind": "label", "from": "brain", "to": "agentic-kit"}) + "\n")  # brand: historical
    monkeypatch.setenv(env_name("HOME"), str(home))
    monkeypatch.delenv(env_name("PATH"), raising=False)
    sys.path.insert(0, str(VAULT_SRC))
    from memory import harvest
    assert harvest.labels_with_aliases(["agentic-kit"]) == ["agentic-kit", "brain", ".brain"]  # brand: historical
    code = ("import sys; sys.path.insert(0, 'hooks'); import capture_common as c; "
            "print(','.join(c.labels_with_aliases(['agentic-kit'])))")
    out = subprocess.run([sys.executable, "-c", code], cwd=PLUGINS / "observer", capture_output=True, text=True,
                         env={**os.environ, env_name("HOME"): str(home)}).stdout.strip()
    assert out == "agentic-kit,brain,.brain"  # brand: historical


@needs_siblings
def test_the_lineage_reader_copy_is_listed_in_the_copies_seam():
    """observer's hooks read lineage.jsonl through a copy of ak's move/lineage.py; scripts/copies.py
    lists it, and test_copies.py fails on a byte of drift."""
    copy = PLUGINS / "observer" / "hooks" / "lineage.py"
    assert copy.is_file(), "a missing file is drift, not a skip"
    assert copy.read_bytes().startswith(b"# COPY of plugins/ak/src/ak/move/lineage.py: edit that file"), (
        "edit ak's move/lineage.py, then `uv run python scripts/copies.py render`")


# ── plugin rename: ids, registries, cache and data folders ─────────────────
def add_plugins(fx):
    """Two plugins of marketplace `kit` installed the way Claude Code writes it, one from elsewhere."""
    pl = fx["claude"] / "plugins"
    for sub in ("cache/kit/tag/1.0", "cache/kit/vault/1.7", "cache/other/x/1", "data/tag-kit", "data/vault-kit",
                "data/x-other", "marketplaces/kit"):
        (pl / sub).mkdir(parents=True)
        (pl / sub / "f.txt").write_text(sub)
    (pl / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "tag@kit": [{"scope": "user", "installPath": str(pl / "cache/kit/tag/1.0"), "version": "1.0"}],
        "vault@kit": [{"scope": "user", "installPath": str(pl / "cache/kit/vault/1.7"), "version": "1.7"}],
        "x@other": [{"scope": "user", "installPath": str(pl / "cache/other/x/1"), "version": "1"}]}}, indent=2) + "\n")
    (pl / "known_marketplaces.json").write_text(json.dumps({
        "kit": {"source": {"source": "directory", "path": "/elsewhere"}, "installLocation": str(pl / "marketplaces/kit")},
        "other": {"installLocation": str(pl / "marketplaces/other")}}, indent=2) + "\n")
    s = json.loads((fx["claude"] / "settings.json").read_text())
    s["enabledPlugins"] = {"tag@kit": True, "vault@kit": False, "x@other": True}
    s["extraKnownMarketplaces"] = {"kit": {"source": {"source": "directory", "path": "/elsewhere"}}}
    (fx["claude"] / "settings.json").write_text(json.dumps(s, indent=2) + "\n")


def test_plugin_rename_moves_every_place_an_id_lives_and_undoes_byte_identical(home):
    add_plugins(home)
    pl, before = home["claude"] / "plugins", snapshot(home["home"])
    dry = ak(home, "plugin", "rename", "tag@kit", "label@kit", "--dry-run", "--json")
    assert dry.returncode == 0, dry.stderr
    assert js(dry)["ran"] is False and snapshot(home["home"]) == before
    p = ak(home, "plugin", "rename", "tag@kit", "label@kit", "--json")
    assert p.returncode == 0, (p.stdout, p.stderr)
    inst = json.loads((pl / "installed_plugins.json").read_text())["plugins"]
    assert list(inst) == ["label@kit", "vault@kit", "x@other"]  # the key keeps its place
    assert inst["label@kit"][0]["installPath"] == str(pl / "cache/kit/label/1.0")
    assert inst["vault@kit"][0]["installPath"] == str(pl / "cache/kit/vault/1.7")  # the sibling is left alone
    assert json.loads((home["claude"] / "settings.json").read_text())["enabledPlugins"] == \
        {"label@kit": True, "vault@kit": False, "x@other": True}
    assert (pl / "cache/kit/label/1.0/f.txt").is_file() and not (pl / "cache/kit/tag").exists()
    assert (pl / "data/label-kit/f.txt").is_file() and not (pl / "data/tag-kit").exists()
    assert (pl / "data/vault-kit").is_dir()
    mid = move_id(home)
    assert ak(home, "moves", "undo", mid).returncode == 0
    assert snapshot(home["home"]) == before


def test_marketplace_rename_takes_every_plugin_under_it(home):
    add_plugins(home)
    pl, before = home["claude"] / "plugins", snapshot(home["home"])
    p = ak(home, "plugin", "rename", "--marketplace", "kit", "shop", "--json")
    assert p.returncode == 0, (p.stdout, p.stderr)
    inst = json.loads((pl / "installed_plugins.json").read_text())["plugins"]
    assert list(inst) == ["tag@shop", "vault@shop", "x@other"]
    assert inst["vault@shop"][0]["installPath"] == str(pl / "cache/shop/vault/1.7")
    known = json.loads((pl / "known_marketplaces.json").read_text())
    assert list(known) == ["shop", "other"] and known["shop"]["installLocation"] == str(pl / "marketplaces/shop")
    assert known["shop"]["source"]["path"] == "/elsewhere"  # where it is fetched from is not ours
    s = json.loads((home["claude"] / "settings.json").read_text())
    assert s["enabledPlugins"] == {"tag@shop": True, "vault@shop": False, "x@other": True}
    assert list(s["extraKnownMarketplaces"]) == ["shop"]
    assert (pl / "cache/shop/tag/1.0").is_dir() and (pl / "data/vault-shop").is_dir() and (pl / "marketplaces/shop").is_dir()
    assert (pl / "cache/other/x/1").is_dir() and (pl / "data/x-other").is_dir()
    assert ak(home, "moves", "undo", move_id(home)).returncode == 0
    assert snapshot(home["home"]) == before


def test_a_plugin_can_move_to_a_marketplace_with_no_cache_yet(home):
    add_plugins(home)
    pl, before = home["claude"] / "plugins", snapshot(home["home"])
    p = ak(home, "plugin", "rename", "tag@kit", "tag@fresh")
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert (pl / "cache/fresh/tag/1.0/f.txt").is_file() and (pl / "data/tag-fresh").is_dir()
    assert ak(home, "moves", "undo", move_id(home)).returncode == 0
    assert snapshot(home["home"]) == before and not (pl / "cache/fresh").exists()


@pytest.mark.parametrize("args", [
    ("tag@kit", "vault@kit"),           # the target id exists
    ("nothing@kit", "n2@kit"),          # the source is nowhere
    ("tag@kit", "tag"),                 # not an id
    ("tag@kit", "tag@kit"),             # the same
    ("--marketplace", "kit", "other"),  # the target marketplace exists
    ("--marketplace", "nope", "n2"),    # no such marketplace
], ids=["target-exists", "no-source", "not-an-id", "same", "market-exists", "no-market"])
def test_plugin_rename_refuses_and_changes_nothing(home, args):
    add_plugins(home)
    before = snapshot(home["home"])
    p = ak(home, "plugin", "rename", *args)
    assert p.returncode != 0, p.stdout
    assert snapshot(home["home"]) == before


def test_plugin_rename_target_folder_in_the_way_is_refused(home):
    add_plugins(home)
    (home["claude"] / "plugins" / "data" / "label-kit").mkdir()
    before = snapshot(home["home"])
    assert ak(home, "plugin", "rename", "tag@kit", "label@kit").returncode == 1
    assert snapshot(home["home"]) == before


def test_plugin_rename_is_refused_with_a_session_open_or_inside_claude(home):
    add_plugins(home)
    before = snapshot(home["home"])
    assert ak(home, "plugin", "rename", "tag@kit", "label@kit", env={"CLAUDECODE": "1"}).returncode == 1
    assert snapshot(home["home"]) == before
    from ak.move import kinds
    from ak.move import plan as P
    os.environ["HOME"] = str(home["home"])  # planning reads $HOME, as the subprocess does
    try:
        m = kinds.plugin_rename("tag@kit", "label@kit")
    finally:
        pass
    (home["claude"] / "sessions" / f"{os.getpid()}.json").write_text(json.dumps({"cwd": "/anywhere"}))
    assert any("still open" in f.what for f in P.quiesce(m, is_claude=lambda pid: True))


def _crash_every_step_then_undo(home, args):
    before = snapshot(home["home"])
    plan = js(ak(home, *args, "--dry-run", "--json"))
    n = len(plan["steps"])
    assert n >= 2
    for k in range(1, n + 1):
        p = ak(home, *args, env={env_name("MOVE_CRASH_AFTER"): str(k)})
        if k < n:
            assert p.returncode != 0, f"crash after step {k} went unnoticed"
        u = ak(home, "moves", "undo", move_id(home))
        assert u.returncode == 0, (k, u.stdout, u.stderr)
        assert snapshot(home["home"]) == before, f"undo after a crash at step {k} left a difference"


def test_plugin_rename_undoes_from_a_crash_at_every_step(home):
    add_plugins(home)
    _crash_every_step_then_undo(home, ("plugin", "rename", "--marketplace", "kit", "shop"))


def test_plugin_rename_resumes_after_a_crash(home):
    add_plugins(home)
    args = ("plugin", "rename", "tag@kit", "label@kit")
    ak(home, *args, env={env_name("MOVE_CRASH_AFTER"): "1"})
    r = ak(home, "moves", "resume", move_id(home), env={env_name("MOVE_CRASH_AFTER"): ""})
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "label@kit" in json.loads((home["claude"] / "plugins" / "installed_plugins.json").read_text())["plugins"]


def test_the_plugin_rename_verb_sits_beside_the_mounted_plugin_verbs():
    out = subprocess.run([sys.executable, "-m", "ak.cli", "plugin", "-h"], capture_output=True, text=True,
                         env={**os.environ, env_name("OUTPUT"): "plain", "PYTHONPATH": SRC_PATH,
                              env_name("PLUGINS_DIR"): str(PLUGINS), env_name("NO_REEXEC"): "1"}).stdout
    assert "rename" in out and "ls" in out  # plugins' own verbs are still there


# ── home move: the kit's own state folder ──────────────────────────────────
def prep_home(fx):
    """Something in the settings and ~/.claude.json that names the home, a chat history run inside it."""
    old = fx["home"] / HOME_DIR
    s = json.loads((fx["claude"] / "settings.json").read_text())
    s["env"]["WIKILINKS_ROOTS"] = f"~/{B}:/usr/bin"
    s["env"]["KIT_STATE"] = str(old / "db")
    s["statusLine"] = {"type": "command", "command": f"python3 {old}/bin/line --root ~/{B}"}
    (fx["claude"] / "settings.json").write_text(json.dumps(s, indent=2) + "\n")
    cj = json.loads((fx["home"] / ".claude.json").read_text())
    cj["projects"][str(old)] = {"allowedTools": []}
    (fx["home"] / ".claude.json").write_text(json.dumps(cj, indent=2) + "\n")
    d = fx["projects"] / slug(old)
    d.mkdir()
    (d / "aaaaaaaa-0000-0000-0000-000000000000.jsonl").write_text(
        json.dumps({"type": "user", "cwd": str(old), "sessionId": "a", "message": {"content": "hi"}}) + "\n")
    (old / "bin").mkdir()
    (old / "bin" / "line").write_text("#!/bin/sh\n")
    return old


def test_home_move_applies_and_leaves_a_link_that_a_straggler_writes_through(home):
    old = prep_home(home)
    new = home["home"] / ".kit"
    dry = ak(home, "home", "move", str(new), "--dry-run", "--json")
    assert dry.returncode == 0, dry.stderr
    plan = js(dry)
    assert plan["ran"] is False and not new.exists() and not old.is_symlink()
    p = ak(home, "home", "move", str(new), "--json")
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert old.is_symlink() and os.readlink(old) == str(new) and (new / "db").is_dir()
    s = json.loads((home["claude"] / "settings.json").read_text())
    assert s["env"]["WIKILINKS_ROOTS"] == "~/.kit:/usr/bin"  # a ~/ form stays a ~/ form
    assert s["env"]["KIT_STATE"] == str(new / "db")
    assert s["statusLine"]["command"] == f"python3 {new}/bin/line --root ~/.kit"
    cj = json.loads((home["home"] / ".claude.json").read_text())
    assert str(new) in cj["projects"] and str(old) not in cj["projects"]
    assert (home["projects"] / slug(new) / "aaaaaaaa-0000-0000-0000-000000000000.jsonl").is_file()
    assert not (home["projects"] / slug(old)).exists()
    rows = [json.loads(x) for x in (new / "db" / "lineage.jsonl").read_text().splitlines()]
    assert {"kind": "path", "from": str(old), "to": str(new)}.items() <= rows[-1].items()
    (old / "late.txt").write_text("written by old code")  # a straggler at the old path lands in the new home
    assert (new / "late.txt").is_file()
    (old / "late.txt").unlink()
    assert (old / "cache" / "names.json").is_file()  # still resolves through the link


def test_home_move_undoes_byte_identical(home):
    prep_home(home)
    before = snapshot(home["home"])
    new = home["home"] / ".kit"
    assert ak(home, "home", "move", str(new)).returncode == 0
    assert snapshot(home["home"]) != before
    u = ak(home, "moves", "undo", move_id(home))
    assert u.returncode == 0, (u.stdout, u.stderr)
    assert snapshot(home["home"]) == before and not new.exists()
    assert not (home["home"] / HOME_DIR).is_symlink()


def test_home_move_undoes_from_a_crash_at_every_step(home):
    prep_home(home)
    _crash_every_step_then_undo(home, ("home", "move", str(home["home"] / ".kit")))


def test_home_move_resumes_after_a_crash_and_finalize_frees_the_old_path(home):
    old, new = prep_home(home), home["home"] / ".kit"
    args = ("home", "move", str(new))
    ak(home, *args, env={env_name("MOVE_CRASH_AFTER"): "1"})
    r = ak(home, "moves", "resume", move_id(home), env={env_name("MOVE_CRASH_AFTER"): ""})
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert old.is_symlink()
    f = ak(home, "moves", "finalize", move_id(home))
    assert f.returncode == 0, (f.stdout, f.stderr)
    assert not os.path.lexists(old) and new.is_dir()


def test_home_move_finalize_warns_and_moves_show_works_with_the_env_at_the_new_home(home):
    prep_home(home)
    new = home["home"] / ".kit"
    assert ak(home, "home", "move", str(new)).returncode == 0
    mid = move_id(home)
    f = ak(home, "moves", "finalize", mid)
    assert f.returncode == 0, (f.stdout, f.stderr)
    assert "point" in (f.stdout + f.stderr) and env_name("PATH") in (f.stdout + f.stderr)
    e = {env_name("HOME"): str(new)}
    s = ak(home, "moves", "show", mid, env=e)
    assert s.returncode == 0, (s.stdout, s.stderr)
    u = ak(home, "moves", "undo", mid, env=e)
    assert u.returncode == 1 and "finalized" in (u.stdout + u.stderr)


@pytest.mark.parametrize("case", ["exists", "inside", "home", "same", "no-parent", "claude"])
def test_home_move_refusals_change_nothing(home, case):
    prep_home(home)
    h = home["home"]
    target = {"exists": h / "x", "inside": h / HOME_DIR / "sub", "home": h, "same": h / HOME_DIR,
              "no-parent": h / "no" / "such" / "dir", "claude": home["claude"] / "kit"}[case]
    before = snapshot(h)
    p = ak(home, "home", "move", str(target))
    assert p.returncode == 1, p.stdout
    assert snapshot(h) == before


def test_home_move_is_refused_inside_claude_or_with_a_session_open(home):
    prep_home(home)
    before = snapshot(home["home"])
    new = str(home["home"] / ".kit")
    assert ak(home, "home", "move", new, env={"CLAUDECODE": "1"}).returncode == 1
    assert snapshot(home["home"]) == before
    from ak.move import plan as P
    m = P.Move(id="t", old="a", new="b", claude=str(home["claude"]), home=str(home["home"] / HOME_DIR), kind="home")
    (home["claude"] / "sessions" / f"{os.getpid()}.json").write_text(json.dumps({"cwd": "/anywhere"}))
    assert any("still open" in f.what for f in P.quiesce(m, is_claude=lambda pid: True))


def test_home_move_never_plans_a_step_on_the_gateway(home):
    prep_home(home)
    gw = home["home"] / ".local" / "share" / GATEWAY_DATA
    gw.mkdir(parents=True)
    plan = js(ak(home, "home", "move", str(home["home"] / ".kit"), "--dry-run", "--json"))
    assert not [s for s in plan["steps"] if str(gw) in json.dumps(s["args"])]
