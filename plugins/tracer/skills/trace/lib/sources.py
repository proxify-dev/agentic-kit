from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
# The tracer's interaction index (files touched per turn live here).
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
# The vault, for project bindings (projects/*.md carry `cwd:` lists) — data the vault
# plugin owns; this plugin only reads it when it is there. The resolver is the
# vault plugin's vault_root.py and the engine ladder.py it presets, copied into hooks/ (scripts/copies.py).
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
from _brand import DISPLAY_NAME, claude_home, is_windows, posix  # noqa: E402
from vault_root import brain_root, db_dir  # noqa: E402

# Where Claude Code files transcripts: one dir per project slug, one JSONL per
# session. A session can be filed under several slugs (nested cwd, worktrees).
CC_PROJECTS = claude_home() / "projects"


def _tasks_root() -> Path:
    """Where Claude Code parks background-task output: <tmp>/claude-<uid>/<slug>/<uuid>/tasks/.
    Its own rule: $CLAUDE_CODE_TMPDIR, else the fixed root /tmp on macOS and Linux (not $TMPDIR), else the
    OS temp dir on Windows, where there is no uid and it writes claude-0."""
    base = os.environ.get("CLAUDE_CODE_TMPDIR") or (
        tempfile.gettempdir() if is_windows() else "/tmp")  # portable: ok — Claude Code's POSIX root ignores $TMPDIR
    return Path(base) / f"claude-{os.getuid() if hasattr(os, 'getuid') else 0}"


TASKS_ROOT = _tasks_root()
try:
    from tracer.db import DB_PATH as INDEX_DB
except Exception:  # noqa: BLE001 — the skill must render even without the package
    INDEX_DB = claude_home() / "plugins" / "data" / "conversation-index" / "index.db"
VAULT_ROOT = brain_root()


def observer_db() -> Path:
    """The observer's store, read here and never written: brain.db in the kit's db/ ($AK_STORE,
    else <kit>/db), one store from any cwd — the vendored resolver's answer, the observer's own."""
    return Path(db_dir()) / "brain.db"

# This plugin owns the conversation engine. This file lives at
# plugins/tracer/skills/trace/lib/sources.py, so parents[3] is the plugin
# root (where pyproject.toml + src/tracer live). Invocation is self-relative
# — the skill never depends on PATH for its own plugin's code. `uv run --project
# <root> tracer` resolves jinja2 from the plugin project env.
#
# Callers speak the tracer verbs natively (replay/show/grep/blame),
# passing the session UUID / phrase / path as a POSITIONAL arg — the tracer scans
# all indexed projects by default, so there is no --session / --all-projects.
# Override the whole base command with CONVO_INDEXER_CMD (whitespace-split) for
# tests or alternate builds — `tracer sessions show --as-skill` sets it so the run re-enters
# the CLI it came from instead of paying a `uv run` cold start per section.
def split_cmd(s: str, windows: bool | None = None) -> list[str]:
    """A command line as argv. POSIX: shlex. Windows: a POSIX shlex eats every backslash of
    C:\\x\\tracer.exe, so a line that is one existing file stays whole, and otherwise it splits
    on whitespace outside quotes (shlex posix=False) with the quotes then taken off — both
    'C:\\My Proj\\ak.exe' (the CLI's shlex.quote) and "C:\\My Proj\\ak.exe"."""
    if not (is_windows() if windows is None else windows):
        return shlex.split(s)
    whole = s.strip().strip('"')
    if os.path.isfile(whole):
        return [whole]
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"" else t
            for t in shlex.split(s, posix=False)]


_OVERRIDE = os.environ.get("CONVO_INDEXER_CMD")
ENGINE_CMD = (
    split_cmd(_OVERRIDE)
    if _OVERRIDE
    else ["uv", "run", "--project", str(PLUGIN_ROOT), "tracer"]
)


@dataclass
class ReportConfig:
    dialogue_limit: int = 30
    query_limit: int = 5
    observer_limit: int = 15
    include_structure: bool = True
    include_observer: bool = True
    # Every answer whole, not only the last. The index keeps a 500-char preview
    # per answer; the last one is always expanded from the source JSONL.
    dialogue_full: bool = False
    include_delegated: bool = True
    include_artifacts: bool = True
    artifact_lines: int = 40


@dataclass
class ResolvedInput:
    mode: str
    session_uuid: str
    query: str


def resolve_args(raw: str) -> ResolvedInput:
    raw = raw.strip()
    if not raw:
        return ResolvedInput(mode="empty", session_uuid="", query="")

    match = UUID_RE.search(raw)
    if match:
        return ResolvedInput(mode="uuid", session_uuid=match.group(0), query="")

    return ResolvedInput(mode="search", session_uuid="", query=raw)


def run_engine(verb: str, *args: str, cwd: str) -> str:
    cmd = [*ENGINE_CMD, verb, *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
    except FileNotFoundError:
        return f"ERROR: {ENGINE_CMD[0]} not found (cannot launch the {DISPLAY_NAME} engine)"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"ERROR ({proc.returncode}): {err or out or DISPLAY_NAME + ' engine failed'}"
    return out or "(no output)"


def dialogue(session_uuid: str, limit: int, cwd: str, full: bool = False) -> str:
    # `ak replay <uuid>` — prompt+answer dialogue. UUID is positional and
    # ak scans all indexed projects, so it resolves regardless of cwd. The
    # last answer comes whole by default; --full expands every one.
    args = ["replay", session_uuid, "--limit", str(limit)]
    if full:
        args.append("--full")
    return run_engine(*args, cwd=cwd)


def ensure_indexed(session_uuid: str, cwd: str) -> str:
    # `tracer trace index --session <uuid>` — the self-heal. Three of the last
    # seventeen traces rendered an empty block for a session that was sitting
    # on disk un-indexed, and the model spent up to 34 calls finding out why.
    return run_engine("index", "--session", session_uuid, cwd=cwd)


def _session_file(session_uuid: str) -> Path | None:
    found = [p for p in CC_PROJECTS.glob(f"*/{session_uuid}.jsonl") if p.is_file()]
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


def _short(path: Path | str) -> str:
    s_ = str(path)
    home = str(Path.home())
    return "~" + s_[len(home):] if s_.startswith(home) else s_


def _frontmatter_cwds(note: Path) -> list[str]:
    """The `cwd:` entries of a project note's frontmatter, expanded."""
    try:
        text = note.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    fm = text[3:end if end > 0 else None]
    out: list[str] = []
    in_cwd = False
    for line in fm.splitlines():
        if re.match(r"^cwd:\s*$", line):
            in_cwd = True
            continue
        m = re.match(r"^cwd:\s*(\S.*)$", line)
        if m:
            in_cwd = False
            val = m.group(1).strip().strip("[]")
            out += [v.strip().strip("\"'") for v in val.split(",") if v.strip()]
            continue
        if in_cwd:
            m2 = re.match(r"^\s+-\s*(.+)$", line)
            if m2:
                out.append(m2.group(1).strip().strip("\"'"))
                continue
            in_cwd = False
    return [os.path.expanduser(v) for v in out]


def project_binding(cwd: str) -> str:
    """The vault project note bound to a cwd (longest matching `cwd:` wins), or ""."""
    if not cwd:
        return ""
    best = ("", 0)
    for note in sorted((VAULT_ROOT / "projects").glob("*.md")):
        for bound in _frontmatter_cwds(note):
            bound = posix(bound).rstrip("/\\")
            if posix(cwd) == bound or posix(cwd).startswith(bound + "/"):
                if len(bound) > best[1]:
                    best = (note.stem, len(bound))
    return best[0]


def session_header(session_uuid: str) -> str:
    """CWD / BRANCH / PROJECT for a session — the first record of its
    transcript carries cwd and gitBranch; the vault says which project note
    that cwd is bound to. Empty when there is no transcript on disk."""
    path = _session_file(session_uuid)
    if path is None:
        return ""
    cwd = branch = ""
    try:
        with open(path, "rb") as f:
            for i, raw in enumerate(f):
                if i > 200:
                    break
                if b'"cwd"' not in raw:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                cwd = cwd or str(msg.get("cwd") or "")
                branch = branch or str(msg.get("gitBranch") or "")
                if cwd and branch:
                    break
    except OSError:
        return ""
    if not cwd:
        return ""
    bound = project_binding(cwd)
    project = f"{bound} (projects/{bound}.md)" if bound else "(no project note bound to this cwd)"
    parts = [f"CWD={_short(cwd)}"]
    if branch:
        parts.append(f"BRANCH={branch}")
    parts.append(f"PROJECT={project}")
    return "  ".join(parts)


def _last_assistant_text(jsonl: Path, cap: int = 600) -> str:
    last = ""
    try:
        with open(jsonl, "rb") as f:
            for raw in f:
                if b'"assistant"' not in raw:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") != "assistant":
                    continue
                content = (msg.get("message") or {}).get("content", [])
                if isinstance(content, list):
                    text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                    if text.strip():
                        last = text
    except OSError:
        return ""
    last = " ".join(last.split())
    return last if len(last) <= cap else last[: cap - 1] + "…"


def delegated(session_uuid: str, max_agents: int = 8, max_tasks: int = 6) -> str:
    """What the session handed off, and what came back: subagent transcripts
    (`<session>/subagents/agent-*.jsonl` + `.meta.json`), workflow journals,
    and background-task output files. Three of the last seventeen traces went
    hunting for exactly these by hand. Empty string when the session ran
    nothing in the background."""
    path = _session_file(session_uuid)
    if path is None:
        return ""
    lines: list[str] = []
    sub = path.parent / session_uuid / "subagents"
    agents = sorted(sub.glob("agent-*.jsonl"), key=lambda p: p.stat().st_mtime) if sub.is_dir() else []
    if agents:
        lines.append(f"subagents: {len(agents)} (transcripts under {_short(sub)})")
        for a in agents[:max_agents]:
            meta = {}
            mp = a.with_suffix(".meta.json")
            if mp.is_file():
                try:
                    meta = json.loads(mp.read_text(encoding="utf-8"))
                except ValueError:
                    meta = {}
            who = meta.get("agentType") or "agent"
            model = meta.get("model")
            desc = meta.get("description") or ""
            head = f"  [{a.stem}] {who}" + (f" ({model})" if model else "") + (f": {desc}" if desc else "")
            lines.append(head)
            final = _last_assistant_text(a)
            lines.append(f"    result: {final}" if final else "    result: (no final text — did not finish?)")
        if len(agents) > max_agents:
            lines.append(f"  … +{len(agents) - max_agents} more subagents")
    wfs = sorted((sub / "workflows").glob("wf_*")) if (sub / "workflows").is_dir() else []
    if wfs:
        lines.append(f"workflows: {len(wfs)}")
        for w in wfs[:4]:
            journal = w / "journal.jsonl"
            n_agents = len(list(w.glob("agent-*.jsonl")))
            n_results = 0
            if journal.is_file():
                try:
                    n_results = sum(1 for l in open(journal, "rb") if b'"result"' in l)
                except OSError:
                    pass
            lines.append(f"  [{w.name}] {n_agents} agents, {n_results} results — journal: {_short(journal)}")
    # tasks/<id>.output is not only background work: Claude Code also streams a large
    # foreground Bash result there before persisting it to tool-results/ (the
    # <persisted-output> the transcript shows). A task is what the session LAUNCHED in
    # the background — a result naming a backgroundTaskId (Bash) or taskId (Monitor).
    tasks_dir = TASKS_ROOT / path.parent.name / session_uuid / "tasks"
    launched = _background_launches(path)
    if launched:
        lines.append(f"background tasks: {len(launched)} (output under {_short(tasks_dir)})")
        for tid, label in list(launched.items())[:max_tasks]:
            o = tasks_dir / f"{tid}.output"
            try:
                size = o.stat().st_size
                with open(o, "rb") as f:
                    if size > 600:
                        f.seek(size - 600)
                    tail = f.read().decode("utf-8", errors="replace")
            except OSError:
                lines.append(f"  [{tid}] {label} — output gone")
                continue
            tail = " ".join(tail.split())[-400:]
            lines.append(f"  [{tid}] {label} — {size} bytes — tail: {tail or '(empty)'}")
        if len(launched) > max_tasks:
            lines.append(f"  … +{len(launched) - max_tasks} more tasks")
    return "\n".join(lines)


def _background_launches(jsonl: Path) -> dict[str, str]:
    """Task id -> what launched it ("Bash: npm run dev"), for every background task the
    session started. Agents are not here: they have transcripts, listed as subagents."""
    uses: dict[str, tuple[str, dict]] = {}
    out: dict[str, str] = {}
    try:
        with open(jsonl, "rb") as f:
            for raw in f:
                is_use = b'"tool_use"' in raw
                is_launch = b"backgroundTaskId" in raw or b'"taskId"' in raw
                if not (is_use or is_launch):
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                content = (msg.get("message") or {}).get("content")
                blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
                for b in blocks:
                    if b.get("type") == "tool_use":
                        uses[b.get("id", "")] = (b.get("name", "?"), b.get("input") or {})
                tur = msg.get("toolUseResult")
                if not isinstance(tur, dict):
                    continue
                tid = tur.get("backgroundTaskId") or tur.get("taskId")
                if not tid:
                    continue
                use_id = next((b.get("tool_use_id") for b in blocks if b.get("type") == "tool_result"), "")
                name, inp = uses.get(use_id, ("?", {}))
                what = inp.get("description") or inp.get("command") or ""
                out[str(tid)] = " ".join(f"{name}: {what}".split())[:100]
    except OSError:
        return {}
    return out


def artifacts(session_uuid: str, max_lines: int = 40, max_inline: int = 3) -> str:
    """Files the session wrote or edited, from the index, with the head of
    each markdown file that still exists. When a recap needs the deliverable
    a session produced, this is it — the model used to Read it by hand."""
    if not INDEX_DB.exists():
        return ""
    try:
        con = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True)
    except sqlite3.Error:
        return ""
    try:
        rows = con.execute(
            """
            SELECT f.path, f.operation, i.id
            FROM i_files f JOIN interactions i ON i.id = f.iid
            WHERE i.session_id = ? AND f.operation IN ('write', 'edit')
            ORDER BY i.timestamp
            """,
            (session_uuid,),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        con.close()
    if not rows:
        return ""
    order: list[str] = []
    ops: dict[str, dict[str, int]] = {}
    first_turn: dict[str, str] = {}
    for fpath, op, iid in rows:
        if fpath not in ops:
            order.append(fpath)
            ops[fpath] = {}
            first_turn[fpath] = iid.rsplit("-", 1)[-1]
        ops[fpath][op] = ops[fpath].get(op, 0) + 1
    rank = {"write": 0, "edit": 1, "read": 2}
    lines = [f"{len(order)} file(s) written or edited:"]
    for fpath in order[:12]:
        p = Path(fpath)
        state = f"{p.stat().st_size} bytes" if p.is_file() else "GONE"
        ops_s = ", ".join(
            f"{k}×{v}" if v > 1 else k
            for k, v in sorted(ops[fpath].items(), key=lambda kv: rank.get(kv[0], 3))
        )
        lines.append(f"  {_short(fpath)}  [{ops_s}; first at turn {first_turn[fpath]}; now {state}]")
    if len(order) > 12:
        lines.append(f"  … +{len(order) - 12} more")
    inlined = 0
    for fpath in order:
        if inlined >= max_inline:
            break
        p = Path(fpath)
        if p.suffix.lower() not in (".md", ".markdown", ".txt") or not p.is_file():
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                head = [next(f) for _ in range(max_lines)]
        except StopIteration:
            head = open(p, "r", encoding="utf-8", errors="replace").readlines()
        except OSError:
            continue
        body = "".join(head)
        if len(body) > 2500:
            body = body[:2500] + "…"
        lines.append("")
        lines.append(f"--- {_short(fpath)} (first {min(len(head), max_lines)} lines) ---")
        lines.append(body.rstrip())
        inlined += 1
    return "\n".join(lines)


def session_title(session_uuid: str) -> str:
    """The session's title, or "" when nothing ever named it.

    Claude Code writes ``ai-title`` lines into the transcript as the session
    grows (last one wins) and a ``custom-title`` line when the user renames it
    (which wins outright). The observer store's ``custom_title`` is the fallback.
    """
    ai_title = custom_title = ""
    for path in CC_PROJECTS.glob(f"*/{session_uuid}.jsonl"):
        try:
            with open(path, "rb") as f:
                for raw in f:
                    if b"-title" not in raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    kind = msg.get("type", "")
                    if kind == "ai-title" and msg.get("aiTitle"):
                        ai_title = str(msg["aiTitle"])
                    elif kind == "custom-title" and msg.get("customTitle"):
                        custom_title = str(msg["customTitle"])
        except OSError:
            continue
        if ai_title or custom_title:
            break
    title = custom_title or ai_title
    db = observer_db()
    if title or not db.is_file():
        return title.strip()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return ""
    try:
        row = con.execute(
            "SELECT custom_title FROM sdk_sessions WHERE content_session_id = ? LIMIT 1",
            (session_uuid,),
        ).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        con.close()
    return (row[0] or "").strip() if row else ""


def structure(session_uuid: str, limit: int, cwd: str) -> str:
    # `ak show <uuid>` — interaction index (tools, errors) for the session.
    return run_engine("show", session_uuid, "--limit", str(limit), cwd=cwd)


def search_candidates(query: str, limit: int, cwd: str) -> str:
    # `ak grep <phrase>` — content search grouped by session; the candidate
    # list the agent picks a UUID from to re-run /tracer:trace.
    return run_engine("grep", query, "--limit", str(limit), cwd=cwd)


def trace_file(path: str, limit: int, cwd: str, op: str | None = None) -> str:
    """Structured trace (Read/Write/Edit/Glob/Grep) with a raw-text fallback.

    The structured pass only sees tool calls with a real `file_path` field —
    it's blind to files touched via Bash (heredocs, cp/mv, curl -o, shell
    loop variables). When it finds nothing, fall back to a raw full-text
    scan for the basename across all sessions. Raw hits aren't confirmed
    file edits (could be a Bash command, a tool result, unrelated chatter) —
    surfaced as leads for the agent to drill into (e.g.
    `tracer trace turn <uuid>-<turn>`), not as verified provenance.
    """
    args = ["blame", path, "--limit", str(limit)]
    if op:
        args += ["--op", op]
    structured = run_engine(*args, cwd=cwd)

    if not structured.startswith("No interactions found"):
        return structured

    basename = Path(path).name
    if not basename or basename == path:
        return structured

    # Two-tier raw fallback. Tier 1 scopes to the file's owning project(s) —
    # a file is almost always touched by a session running in (or above) its
    # own directory, and scanning one project (~0.06s) beats the full
    # cross-project scan (~0.75s) by ~10x. Tier 2 only fires if the scoped
    # pass finds nothing, catching the rare cross-project absolute-path edit.
    # --limit is the number of sessions shown (every session is scanned either way):
    # five leads, ranked by how often each mentions the name.
    scoped = run_engine("grep", basename, "--under", path, "--limit", "5", cwd=cwd)
    scoped_empty = scoped.startswith("No conversations matched") or scoped.startswith(
        "No indexed project owns"
    )

    if not scoped_empty:
        raw = scoped
        scope_note = (
            "scoped to the file's owning project — rerun an all-sessions search "
            "if the session you expect isn't listed"
        )
    else:
        raw = run_engine("grep", basename, "--limit", "5", cwd=cwd)
        scope_note = "all sessions (exhaustive — no owning-project match)"

    return (
        f"{structured}\n\n"
        f"RAW_FALLBACK ({scope_note}): no Read/Write/Edit/Glob/Grep tool call "
        f"touched this path.\n"
        f'Text search for "{basename}" (may be a Bash command, tool result, or '
        f"unrelated mention — NOT a confirmed edit; drill in before asserting "
        f"this session touched the file):\n\n{raw}"
    )


def observer_rows(session_uuid: str, cap: int = 15) -> str:
    """What observer kept from this session: `#id · type · title` per live row.

    Joined sdk_sessions.content_session_id (the uuid) → memory_session_id →
    observations; a forgotten row (forgotten_at set) or one folded into a summary row
    (folded_into set) is not live. Read-only; one line when there is nothing to show.
    """
    db = observer_db()
    if not db.is_file():
        return f"no observer store at {_short(db)}"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return f"observer store unreadable: {exc}"
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(observations)")}
        live = []
        if "forgotten_at" in cols:
            live.append("COALESCE(o.forgotten_at, '') = ''")
        if "folded_into" in cols:
            live.append("o.folded_into IS NULL")
        where = " AND ".join(["s.content_session_id = ?", *live])
        linked = con.execute(
            "SELECT count(*) FROM sdk_sessions WHERE content_session_id = ?", (session_uuid,)
        ).fetchone()[0]
        rows = con.execute(
            f"""SELECT o.id, o.type, o.title FROM observations o
                JOIN sdk_sessions s ON s.memory_session_id = o.memory_session_id
                WHERE {where}
                ORDER BY o.created_at_epoch, o.id""",
            (session_uuid,),
        ).fetchall()
    except sqlite3.Error as exc:
        return f"observer store unreadable: {exc}"
    finally:
        con.close()

    if not linked:
        return "the observer has no record of this session (it writes at session end)"
    if not rows:
        return "the observer kept no live rows from this session"
    lines = [f"{len(rows)} observation(s) — #id · type · title"]
    for oid, otype, title in rows[:cap]:
        lines.append(f"  #{oid} · {otype} · {' '.join((title or '(untitled)').split())[:140]}")
    if len(rows) > cap:
        lines.append(f"  … +{len(rows) - cap} more")
    lines.append("whole rows: observer get <id> …")
    return "\n".join(lines)
