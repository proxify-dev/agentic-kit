"""Session agent-roster enumeration + live-state inference.

Stdlib-only (no jinja, no indexer) so it is cheap enough to poll in a --watch
loop. Given a session UUID or slug, it locates the parent transcript and its
sibling per-session directory, then reports every teammate and workflow agent
with an inferred run-state.

Layout it reads (as of Claude Code Jul 2026):

    <claude_home>/projects/<project>/<uuid>.jsonl    <- parent (team lead)
    <claude_home>/projects/<project>/<uuid>/
        subagents/agent-<id>.jsonl                   <- teammate transcript
        subagents/agent-<id>.meta.json               <- teammate spawn record
        subagents/workflows/<wf>/agent-<id>.jsonl    <- workflow fleet member
        subagents/workflows/<wf>/journal.jsonl       <- per-agent started/result log
        workflows/<wf>.json                          <- workflow run state

State inference (see classify_* below). mtime freshness is the only real-time
"alive" signal — there is no PID registry in the transcript files.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hooks"))
from _brand import claude_home  # noqa: E402 — the plugin's generated seam, stdlib like this file

PROJECTS = claude_home() / "projects"

# Tunables (env-overridable). A transcript touched within RUNNING_WINDOW seconds
# is treated as actively streaming; past STALL_WINDOW with no terminal marker it
# is treated as orphaned rather than merely idle.
RUNNING_WINDOW = float(os.environ.get("CC_TRACE_RUNNING_WINDOW", "45"))
STALL_WINDOW = float(os.environ.get("CC_TRACE_STALL_WINDOW", "180"))

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)

# Slug scan bound: read at most this many parent transcripts when resolving a
# non-UUID target, newest first, so a bad slug can't walk the whole history.
SLUG_SCAN_CAP = 800
_SLUG_PEEK_LINES = 6

# Run states, ordered by how much they want the human's attention.
RUNNING = "RUNNING"
STALLED = "STALLED"
FAILED = "FAILED"
IDLE = "IDLE"
DONE = "DONE"
UNKNOWN = "UNKNOWN"

STATE_ORDER = {RUNNING: 0, STALLED: 1, FAILED: 2, IDLE: 3, DONE: 4, UNKNOWN: 5}


@dataclass
class Agent:
    agent_id: str
    name: str
    kind: str  # "teammate" | "workflow"
    agent_type: str
    model: str
    team: str | None
    wf_id: str | None
    transcript: str
    lines: int
    mtime: float
    age_s: float
    state: str
    detail: str  # short human hint about *why* this state


@dataclass
class Workflow:
    wf_id: str
    name: str
    status: str
    agent_count: int
    tokens: int | None
    phase: str | None


@dataclass
class Roster:
    target: str
    session_uuid: str
    project: str
    parent_exists: bool
    parent_mtime: float
    parent_age_s: float
    team: str | None
    model: str | None
    agents: list[Agent] = field(default_factory=list)
    workflows: list[Workflow] = field(default_factory=list)
    generated_at: float = 0.0
    error: str | None = None


# ── discovery ────────────────────────────────────────────────────────────────

def find_session(target: str) -> tuple[str, Path, Path] | None:
    """Resolve target -> (session_uuid, parent_jsonl, session_dir).

    UUID targets glob directly. Anything else is treated as a slug/phrase and
    matched against the ``slug`` field near the head of each parent transcript,
    newest first, capped by SLUG_SCAN_CAP.
    """
    target = target.strip()
    m = UUID_RE.search(target)
    if m:
        return _by_glob(f"{m.group(0)}.jsonl")

    # Bare hex prefix (e.g. `3de61f5f`) — resolve by filename prefix so users can
    # paste a short id. Ambiguous prefixes pick the most recently touched.
    if re.fullmatch(r"[0-9a-f]{6,}", target, re.I):
        hit = _by_glob(f"{target}*.jsonl")
        if hit:
            return hit

    return _resolve_slug(target.lower())


def _by_glob(pattern: str) -> tuple[str, Path, Path] | None:
    hits = sorted(
        PROJECTS.glob(f"*/{pattern}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not hits:
        return None
    parent = hits[0]
    return parent.stem, parent, parent.with_suffix("")


def _resolve_slug(needle: str) -> tuple[str, Path, Path] | None:
    candidates = sorted(
        PROJECTS.glob("*/*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for parent in candidates[:SLUG_SCAN_CAP]:
        slug = _peek_slug(parent)
        if slug and (slug == needle or needle in slug):
            return parent.stem, parent, parent.with_suffix("")
    return None


def _peek_slug(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(_SLUG_PEEK_LINES):
                line = fh.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                slug = obj.get("slug")
                if slug:
                    return str(slug).lower()
    except OSError:
        return None
    return None


# ── cheap transcript probes (safe to poll) ───────────────────────────────────

def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _last_json_line(path: Path, tail_bytes: int = 65536) -> dict | None:
    """Read the final non-empty JSON object without loading the whole file."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > tail_bytes:
                fh.seek(size - tail_bytes)
                fh.readline()  # discard partial line
            chunk = fh.read()
    except OSError:
        return None
    for raw in reversed(chunk.splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return None


def _is_terminal_turn(last: dict | None) -> bool:
    """A teammate at rest ends on an assistant message that stopped naturally."""
    if not last:
        return False
    msg = last.get("message") or {}
    return (
        last.get("type") == "assistant"
        and msg.get("stop_reason") in {"end_turn", "stop_sequence"}
    )


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── state classification ─────────────────────────────────────────────────────

def classify_teammate(age_s: float, last: dict | None) -> tuple[str, str]:
    if age_s <= RUNNING_WINDOW:
        return RUNNING, f"active {human_age(age_s)} ago"
    if _is_terminal_turn(last):
        return IDLE, f"idle {human_age(age_s)}, awaiting message"
    if age_s >= STALL_WINDOW:
        return STALLED, f"cold {human_age(age_s)}, no terminal turn"
    return IDLE, f"quiet {human_age(age_s)}"


def classify_workflow_agent(
    age_s: float, journal_types: set[str], wf_done: bool
) -> tuple[str, str]:
    if "error" in journal_types or "failed" in journal_types:
        return FAILED, "journal reported error"
    if "result" in journal_types:
        return DONE, "returned result"
    # only 'started' seen from here on
    if wf_done:
        return DONE, "workflow completed"
    if age_s <= RUNNING_WINDOW:
        return RUNNING, f"active {human_age(age_s)} ago"
    if age_s >= STALL_WINDOW:
        return STALLED, f"started, cold {human_age(age_s)}"
    return RUNNING, f"started {human_age(age_s)} ago"


# ── journal + workflow reads ─────────────────────────────────────────────────

def _journal_index(journal: Path) -> dict[str, set[str]]:
    """agentId -> set of event types seen in a workflow journal."""
    index: dict[str, set[str]] = {}
    if not journal.exists():
        return index
    try:
        with journal.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                aid = obj.get("agentId")
                if aid:
                    index.setdefault(aid, set()).add(obj.get("type"))
    except OSError:
        pass
    return index


def _workflow_status(session_dir: Path, wf_id: str) -> Workflow:
    data = _load_json(session_dir / "workflows" / f"{wf_id}.json") or {}
    return Workflow(
        wf_id=wf_id,
        name=data.get("workflowName") or data.get("summary") or wf_id,
        status=data.get("status") or "unknown",
        agent_count=int(data.get("agentCount") or 0),
        tokens=data.get("totalTokens"),
        phase=_current_phase(data),
    )


def _current_phase(data: dict) -> str | None:
    prog = data.get("workflowProgress")
    if isinstance(prog, dict):
        return prog.get("currentPhase") or prog.get("phase")
    phases = data.get("phases")
    if isinstance(phases, list) and phases:
        last = phases[-1]
        if isinstance(last, dict):
            return last.get("title") or last.get("detail")
    return None


# ── main entry ───────────────────────────────────────────────────────────────

def build_roster(target: str) -> Roster:
    now = time.time()
    found = find_session(target)
    if not found:
        return Roster(
            target=target, session_uuid="", project="", parent_exists=False,
            parent_mtime=0.0, parent_age_s=0.0, team=None, model=None,
            generated_at=now, error=f"no session found for '{target}'",
        )

    uuid, parent, session_dir = found
    p_mtime = parent.stat().st_mtime if parent.exists() else 0.0
    roster = Roster(
        target=target,
        session_uuid=uuid,
        project=parent.parent.name,
        parent_exists=parent.exists(),
        parent_mtime=p_mtime,
        parent_age_s=max(0.0, now - p_mtime),
        team=None,
        model=None,
        generated_at=now,
    )

    sub = session_dir / "subagents"
    if not sub.is_dir():
        return roster  # solo session — parent only, no fleet

    # Teammates: subagents/agent-*.jsonl (+ .meta.json), excluding the workflows/ tree.
    for tp in sorted(sub.glob("agent-*.jsonl")):
        meta = _load_json(tp.with_suffix(".meta.json")) or {}
        st = tp.stat()
        age = max(0.0, now - st.st_mtime)
        state, detail = classify_teammate(age, _last_json_line(tp))
        roster.team = roster.team or meta.get("teamName")
        roster.model = roster.model or meta.get("model")
        roster.agents.append(
            Agent(
                agent_id=tp.stem.replace("agent-", "", 1),
                name=meta.get("name") or tp.stem.replace("agent-", "", 1),
                kind="teammate",
                agent_type=meta.get("customAgentType") or meta.get("agentType") or "?",
                model=meta.get("model") or "?",
                team=meta.get("teamName"),
                wf_id=None,
                transcript=str(tp),
                lines=_count_lines(tp),
                mtime=st.st_mtime,
                age_s=age,
                state=state,
                detail=detail,
            )
        )

    # Workflow fleets: subagents/workflows/<wf>/agent-*.jsonl
    wf_root = sub / "workflows"
    if wf_root.is_dir():
        for wf_dir in sorted(p for p in wf_root.iterdir() if p.is_dir()):
            wf_id = wf_dir.name
            wf = _workflow_status(session_dir, wf_id)
            roster.workflows.append(wf)
            wf_done = wf.status in {"completed", "failed", "cancelled"}
            journal = _journal_index(wf_dir / "journal.jsonl")
            for ap in sorted(wf_dir.glob("agent-*.jsonl")):
                st = ap.stat()
                age = max(0.0, now - st.st_mtime)
                aid = ap.stem.replace("agent-", "", 1)
                state, detail = classify_workflow_agent(
                    age, journal.get(aid, set()), wf_done
                )
                roster.agents.append(
                    Agent(
                        agent_id=aid,
                        name=aid,
                        kind="workflow",
                        agent_type="workflow",
                        model=wf.name,
                        team=None,
                        wf_id=wf_id,
                        transcript=str(ap),
                        lines=_count_lines(ap),
                        mtime=st.st_mtime,
                        age_s=age,
                        state=state,
                        detail=detail,
                    )
                )

    roster.agents.sort(key=lambda a: (STATE_ORDER.get(a.state, 9), a.age_s))
    return roster


# ── formatting helpers ───────────────────────────────────────────────────────

def human_age(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m" if s == 0 else f"{m}m{s}s"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m}m" if m else f"{h}h"
    d, h = divmod(h, 24)
    return f"{d}d{h}h" if h else f"{d}d"
