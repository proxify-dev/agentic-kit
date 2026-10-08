"""A session's subagents: one row each, and one agent's steps.

    tracer sessions agents <session>             every subagent the session started: its type, what it
                                                 was asked, the step that started it, calls, time, tokens
    tracer sessions agents <session> <agent>     one agent's steps, as `tracer trace turn` lists a turn's
    tracer sessions agents <session> <agent> --open 5    a step whole

Why: on 2026-10-05 an agent asked where session 548c8046 spent its time (28 Agent calls, two
10-minute miners) read `tracer sessions show`, met one `transcript: subagents/agent-….jsonl`
pointer per Agent step, and went to jq over the raw files. Its per-agent token sums were wrong:
one model message spans several transcript records, each repeating the message's usage.

Where they are on disk, under the session's own dir:

    subagents/agent-<id>.jsonl                   an Agent or Task call's transcript, or a fork's
    subagents/agent-<id>.meta.json               agentType, description, name, model, and the
                                                 toolUseId of the call that started it
    subagents/workflows/<run>/agent-<id>.jsonl   an agent a workflow started

Tokens are counted once per model message (Turn.usage keeps a message's last record). An Agent
call that failed before an agent ran (a bad parameter) has no transcript: it is listed as one
that started nothing, so the calls and the rows add up.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .drill import (
    PREFIX_MIN,
    AmbiguousSession,
    DrillError,
    LoadedSession,
    LoadedTurn,
    _fmt_bytes,
    _fmt_count,
    _fmt_secs,
    _hms,
    _legend,
    _n,
    _secs,
    _short,
    _signals,
    _tools_mix,
    _turn_body,
    agent_verb,
    annotate,
    format_steps,
    load_session,
    load_transcript,
    parse_steps,
    subagent_transcript,
)
from .parser import extract_file_touch
from .step_label import step_label
from .turns import Turn, one_line, short_tool

AGENT_TOOLS = ("Agent", "Task")
WRITES = ("write", "edit")


@dataclass
class Agent:
    """One subagent: what its meta says, where it was started from, and what its transcript holds."""
    agent_id: str
    path: Path
    type: str = ""
    description: str = ""
    name: str = ""
    model: str = ""
    workflow: str = ""  # the workflow run that started it: wf_…
    tool_use_id: str = ""  # the call that started it, as its meta names it
    spawned_by: tuple[int, int] | None = None  # (turn, step) of the session's call that started it
    turns: list[Turn] = field(default_factory=list)

    @property
    def short(self) -> str:
        return self.agent_id[:PREFIX_MIN]

    @property
    def steps(self):
        return [s for t in self.turns for s in t.steps]

    @property
    def start(self) -> str:
        return self.turns[0].ts if self.turns else ""

    @property
    def end(self) -> str:
        return self.turns[-1].end_ts if self.turns else ""

    @property
    def seconds(self) -> float:
        return _secs(self.start, self.end) or 0.0

    @property
    def errors(self) -> int:
        return sum(1 for s in self.steps if s.is_error)

    @property
    def brief(self) -> str:
        return self.turns[0].prompt_text if self.turns else ""

    @property
    def report(self) -> str:
        """What it answered: its closing text, or a workflow agent's StructuredOutput call, as JSON."""
        if not self.turns:
            return ""
        last = self.turns[-1]
        if last.final_text:
            return last.final_text
        out = _structured(last)
        return json.dumps(out.input, ensure_ascii=False) if out else ""

    @property
    def ended(self) -> str:
        """reported · interrupted · no report (the transcript stops after a call, or before one)."""
        if not self.turns:
            return "empty"
        last = self.turns[-1]
        if last.interrupted_after is not None:
            return "interrupted"
        return "reported" if last.final_text or _structured(last) else "no report"

    @property
    def tokens(self) -> dict[str, int]:
        tot = Counter()
        for t in self.turns:
            for u in t.usage.values():
                tot["in"] += int(u.get("input_tokens") or 0)
                tot["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
                tot["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
                tot["out"] += int(u.get("output_tokens") or 0)
                tot["thinking"] += int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
        return {k: tot[k] for k in ("in", "cache_read", "cache_write", "out", "thinking")}

    @property
    def label(self) -> str:
        """What it was asked, in a few words: its description, else its brief's first words."""
        return self.description or one_line(self.brief, 60)

    @property
    def kind(self) -> str:
        return self.type or ("workflow" if self.workflow else "?")


def _structured(turn: Turn):
    """The turn's last step when it is a StructuredOutput call that went through: how a workflow's agent answers."""
    last = turn.steps[-1] if turn.steps else None
    return last if last and last.tool == "StructuredOutput" and last.answered and not last.is_error else None


@dataclass
class NotStarted:
    """An Agent call of the session that started no agent: it failed before one ran."""
    turn: int
    step: int
    description: str
    said: str


@dataclass
class SessionAgents:
    session: LoadedSession
    agents: list[Agent]
    not_started: list[NotStarted]


def _meta(path: Path) -> dict:
    try:
        got = json.loads(path.with_name(path.name[: -len(".jsonl")] + ".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def transcripts(session_dir: Path) -> list[Path]:
    """Every subagent transcript under a session's dir, a workflow's included."""
    sub = session_dir / "subagents"
    if not sub.is_dir():
        return []
    return sorted(p for p in sub.rglob("agent-*.jsonl") if p.is_file())


def _read_agent(path: Path, sid: str) -> Agent:
    meta = _meta(path)
    parts = path.parts
    workflow = parts[parts.index("workflows") + 1] if "workflows" in parts[:-1] else ""
    agent = Agent(
        agent_id=path.stem.removeprefix("agent-"), path=path, type=str(meta.get("agentType") or ""),
        description=str(meta.get("description") or ""), name=str(meta.get("name") or ""),
        model=str(meta.get("model") or ""), workflow=workflow, tool_use_id=str(meta.get("toolUseId") or ""),
    )
    agent.turns = load_transcript(path, sid).turns
    models = Counter(m for t in agent.turns for m in t.models.values() if m and not m.startswith("<"))
    if models:  # the model its messages name: "inherit" and "sonnet" in the meta say less
        agent.model = models.most_common(1)[0][0]
    return agent


def load(target: str) -> SessionAgents:
    """A session's subagents, in the order they started, each tied to the step that started it."""
    ls = load_session(target)
    agents = [_read_agent(p, ls.session_id) for p in transcripts(ls.session_dir)]
    by_call: dict[str, tuple[int, int]] = {}
    by_id: dict[str, tuple[int, int]] = {}
    by_name: dict[str, tuple[int, int]] = {}
    calls = []
    for seq, turn in enumerate(ls.turns):
        for s in turn.steps:
            if s.tool not in AGENT_TOOLS:
                continue
            at = (seq, s.n)
            calls.append((at, s))
            by_call[s.tool_use_id] = at
            aid = s.use_result.get("agentId") if isinstance(s.use_result, dict) else None
            if aid:
                by_id[str(aid)] = at
            name = s.input.get("name") if isinstance(s.input, dict) else None
            if name and not s.is_error:
                by_name[str(name)] = at
    for a in agents:
        a.spawned_by = by_call.get(a.tool_use_id) or by_id.get(a.agent_id) or (by_name.get(a.name) if a.name else None)
        if a.spawned_by is None and not a.workflow and a.start:  # a fork names no call: the last turn opened before it
            a.spawned_by = next(((seq, 0) for seq in range(len(ls.turns) - 1, -1, -1) if ls.turns[seq].ts <= a.start), None)
    started = {a.spawned_by for a in agents if a.spawned_by}
    not_started = []
    for at, s in calls:
        if at in started or not s.answered or subagent_transcript(s, ls.session_dir) is not None:
            continue
        desc = s.input.get("description") if isinstance(s.input, dict) else ""
        said = refusal(s.result_text) if s.is_error else "no transcript on disk"
        not_started.append(NotStarted(at[0], at[1], str(desc or ""), said))
    agents.sort(key=lambda a: (a.start, a.agent_id))
    return SessionAgents(ls, agents, not_started)


_TOOL_ERROR = re.compile(r"^\s*<tool_use_error>(.*?)</tool_use_error>\s*$", re.S)


def refusal(text: str) -> str:
    """Why a call was refused, in one line: an InputValidationError's own messages (`model: Invalid
    option: expected one of "sonnet"|…`) rather than the JSON they come wrapped in."""
    m = _TOOL_ERROR.match(text or "")
    body = m.group(1) if m else (text or "")
    kind, _, rest = body.partition(":")
    if kind.strip() == "InputValidationError":
        try:
            issues = json.loads(rest)
        except ValueError:
            issues = None
        if isinstance(issues, list) and issues:
            said = [f"{'.'.join(map(str, i.get('path') or [])) or 'input'}: {i.get('message', '?')}"
                    for i in issues if isinstance(i, dict)]
            return one_line("bad parameter — " + "; ".join(said), 160)
    return one_line(body, 160)


def pick(got: SessionAgents, ref: str) -> Agent:
    """The agent REF names: its id, a prefix of it, or `agent-<id>`. Ambiguous raises AmbiguousSession."""
    ref = ref.strip().removeprefix("agent-").removesuffix(".jsonl")
    if not ref:
        raise DrillError("no agent named — give its id, e.g. a7c19e5d")
    hits = [a for a in got.agents if a.agent_id.startswith(ref)]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise AmbiguousSession(f"{ref!r} starts {len(hits)} agents: " + ", ".join(a.agent_id for a in hits[:5]))
    sid = got.session.session_id
    raise DrillError(f"no agent {ref!r} in {sid[:PREFIX_MIN]}: it has {_n(len(got.agents), 'subagent')}"
                     + (f" — {agent_verb(sid)} lists them" if got.agents else ""))


# ── the rollup ───────────────────────────────────────────────────────────────


def _row(a: Agent) -> dict:
    return {
        "id": a.agent_id, "type": a.kind, "description": a.label, "name": a.name, "model": a.model,
        "workflow": a.workflow, "spawned_by": ({"turn": a.spawned_by[0], "step": a.spawned_by[1]} if a.spawned_by else None),
        "start": a.start, "end": a.end, "seconds": round(a.seconds, 1), "calls": len(a.steps), "errors": a.errors,
        "tokens": a.tokens, "ended": a.ended, "transcript": str(a.path),
    }


def _touches(a: Agent) -> dict[str, set[str]]:
    """path or URL → what the agent did with it: read, write, edit, fetch."""
    out: dict[str, set[str]] = {}
    for s in a.steps:
        ft = extract_file_touch(s.tool, s.input)
        if ft and ft.operation in ("read", *WRITES):
            out.setdefault(ft.path, set()).add(ft.operation)
        elif s.tool == "WebFetch" and isinstance(s.input.get("url"), str):
            out.setdefault(s.input["url"], set()).add("fetch")
    return out


def overlap(agents: list[Agent]) -> dict[str, list[dict]]:
    """What more than one agent touched: files two agents changed (one may undo the other), and
    files read or URLs fetched more than once (work done twice)."""
    who: dict[str, dict[str, set[str]]] = {}
    for a in agents:
        for path, ops in _touches(a).items():
            for op in ops:
                who.setdefault(path, {}).setdefault(op, set()).add(a.short)
    written, read = [], []
    for path, ops in who.items():
        changers = set().union(*(ops.get(op, set()) for op in WRITES))
        if len(changers) > 1:
            written.append({"path": path, "agents": sorted(changers)})
        readers = set().union(ops.get("read", set()), ops.get("fetch", set()))
        if len(readers) > 1:
            read.append({"path": path, "agents": sorted(readers)})
    read.sort(key=lambda r: (-len(r["agents"]), r["path"]))
    return {"written": sorted(written, key=lambda r: r["path"]), "read": read}


def _groups(agents: list[Agent]) -> list[dict]:
    """One row per agent type: how many, their calls, time and tokens, the slowest one."""
    by: dict[str, list[Agent]] = {}
    for a in agents:
        by.setdefault(a.kind, []).append(a)
    rows = []
    for kind, group in by.items():
        slow = max(group, key=lambda a: a.seconds)
        rows.append({
            "type": kind, "agents": len(group), "calls": sum(len(a.steps) for a in group),
            "errors": sum(a.errors for a in group), "seconds": round(sum(a.seconds for a in group), 1),
            "out": sum(a.tokens["out"] for a in group),
            "slowest": {"id": slow.agent_id, "seconds": round(slow.seconds, 1), "description": slow.label},
        })
    rows.sort(key=lambda r: -r["seconds"])
    return rows


def as_data(got: SessionAgents) -> dict:
    ls = got.session
    turn_secs = sum(_secs(t.ts, t.end_ts) or 0 for t in ls.turns)
    tokens = Counter()
    for a in got.agents:
        tokens.update(a.tokens)
    return {
        "session": ls.session_id,
        "title": ls.title,
        "agents": [_row(a) for a in got.agents],
        "by_type": _groups(got.agents),
        "totals": {
            "agents": len(got.agents), "calls": sum(len(a.steps) for a in got.agents),
            "errors": sum(a.errors for a in got.agents),
            "agent_seconds": round(sum(a.seconds for a in got.agents), 1),
            "session_turn_seconds": round(turn_secs, 1),
            "tokens": {k: tokens[k] for k in ("in", "cache_read", "cache_write", "out", "thinking")},
        },
        "not_started": [vars(n) for n in got.not_started],
        "overlap": overlap(got.agents),
    }


def headline(data: dict) -> str:
    t = data["totals"]
    sid = data["session"]
    title = f' · "{data["title"]}"' if data.get("title") else ""
    return (f"{sid}{title} · {_n(t['agents'], 'subagent')} · {_fmt_secs(t['agent_seconds'])} of agent time"
            f" (agents run side by side; the session's turns took {_fmt_secs(t['session_turn_seconds'])})"
            f" · {_n(t['calls'], 'call')} · {_n(t['errors'], 'error')}"
            f" · tokens out {_fmt_count(t['tokens']['out'])} · cache read {_fmt_count(t['tokens']['cache_read'])}")


def type_rows(data: dict) -> list[list[str]]:
    return [[g["type"], str(g["agents"]), str(g["calls"]), str(g["errors"]), _fmt_secs(g["seconds"]),
             _fmt_count(g["out"]),
             f'{g["slowest"]["id"][:PREFIX_MIN]} {_fmt_secs(g["slowest"]["seconds"])} {one_line(g["slowest"]["description"], 50)}']
            for g in data["by_type"]]


TYPE_COLUMNS = ("type", "agents", "calls", "errors", "agent time", "out", "slowest")
AGENT_COLUMNS = ("id", "start", "type", "started by", "calls", "errors", "time", "out", "ended", "what it was asked")


def agent_rows(data: dict) -> list[list[str]]:
    rows = []
    for a in data["agents"]:
        at = a["spawned_by"]
        rows.append([a["id"][:PREFIX_MIN], _hms(a["start"]), a["type"],
                     (f'{at["turn"]:03d}#{at["step"]}' if at["step"] else f'{at["turn"]:03d}') if at else (a["workflow"] or "-"),
                     str(a["calls"]), str(a["errors"]), _fmt_secs(a["seconds"]), _fmt_count(a["tokens"]["out"]),
                     a["ended"], one_line(a["description"], 70)])
    return rows


def notes(data: dict) -> list[str]:
    """The lines under the tables: calls that started nothing, and what two agents both touched."""
    out = []
    ns = data["not_started"]
    if ns:
        each = " · ".join(f'{n["turn"]:03d}#{n["step"]} {one_line(n["description"], 40)!r}: {one_line(n["said"], 80)}'
                          for n in ns[:6])
        out.append(f"not started: {_n(len(ns), 'Agent call')} ran no agent — {each}" + (" …" if len(ns) > 6 else ""))
    ov = data["overlap"]
    if ov["written"]:
        each = " · ".join(f'{_short(w["path"])} ({", ".join(w["agents"])})' for w in ov["written"][:5])
        out.append(f"changed by more than one agent: {len(ov['written'])} — {each}" + (" …" if len(ov["written"]) > 5 else ""))
    if ov["read"]:
        each = " · ".join(f'{_short(r["path"])} ×{len(r["agents"])}' for r in ov["read"][:5])
        out.append(f"read or fetched by more than one agent: {len(ov['read'])} — {each}" + (" …" if len(ov["read"]) > 5 else ""))
    return out


# ── one agent ────────────────────────────────────────────────────────────────


def loaded_turn(got: SessionAgents, a: Agent, seq: int = 0) -> LoadedTurn:
    """One of the agent's turns, as the drill opens a session's: its results are in the session's tool-results/."""
    sid = got.session.session_id
    return LoadedTurn(f"{sid[:PREFIX_MIN]} agent {a.short}" + (f" turn {seq:03d}" if len(a.turns) > 1 else ""),
                      sid, a.path, a.turns[seq], results_dir=got.session.session_dir)


def _turn_of(a: Agent, turn: int | None) -> int:
    if turn is None:
        if len(a.turns) > 1:
            raise DrillError(f"agent {a.short} has {len(a.turns)} turns (it was sent more messages) — name one with --turn 0…{len(a.turns) - 1}")
        return 0
    if not 0 <= turn < len(a.turns):
        raise DrillError(f"agent {a.short} has {_n(len(a.turns), 'turn')} (0…{len(a.turns) - 1})")
    return turn


def format_agent(got: SessionAgents, a: Agent, steps=None, turn: int | None = None) -> str:
    """One agent: its type, what it was asked, the step that started it, time and tokens, then its
    steps as `tracer trace turn` lists a turn's. ``steps``: list just these."""
    sid = got.session.session_id
    head = f"{a.agent_id} · {a.kind}" + (f" · {a.name}" if a.name and a.name != a.kind else "") \
        + (f" · {a.model}" if a.model else "") + (f' · "{a.label}"' if a.label else "")
    lines = [head]
    started = (f"started by turn {a.spawned_by[0]:03d}" + (f" step {a.spawned_by[1]}" if a.spawned_by[1] else "")) if a.spawned_by else \
        (f"started by workflow {a.workflow}" if a.workflow else "started by: not found in the session's calls")
    lines.append(f"session {sid} · {started} · {_hms(a.start)} → {_hms(a.end)} · {_fmt_secs(a.seconds)}"
                 f" · {_n(len(a.steps), 'call')} · ended: {a.ended}")
    tk = a.tokens
    lines.append(f"tokens: in {_fmt_count(tk['in'])} · cache read {_fmt_count(tk['cache_read'])} · cache write"
                 f" {_fmt_count(tk['cache_write'])} · out {_fmt_count(tk['out'])} (thinking {_fmt_count(tk['thinking'])})"
                 " — once per model message")
    brief_at = f" — whole: {_verb_open(sid, a.spawned_by)}" if a.spawned_by and a.spawned_by[1] else ""
    lines.append(f"brief: {one_line(a.brief, 300) or '(empty)'}{brief_at}")
    lines.append(f"transcript: {_short(a.path)}")
    lines.append(f"tools: {_tools_mix(a.steps)}")
    if not a.turns:
        return "\n".join(lines)
    seqs = [_turn_of(a, turn)] if (steps is not None or turn is not None) else list(range(len(a.turns)))
    all_notes = [annotate(t, got.session.session_dir) for t in a.turns]
    lines.append(_signals(LoadedSession(sid, a.path, a.turns), all_notes))
    opens = agent_verb(sid, a.path) + (" --turn N" if len(a.turns) > 1 else "") + " --open 1-3"
    for seq in seqs:
        t, tn = a.turns[seq], all_notes[seq]
        only = parse_steps(steps, len(t.steps)) if steps is not None else None
        if len(a.turns) > 1:
            lines.append("")
            lines.append(f"── turn {seq:03d} · {_hms(t.ts)} · {_n(len(t.steps), 'step')} · {_fmt_secs(_secs(t.ts, t.end_ts))}"
                         f" · asked: {one_line(t.prompt_text, 160)}")
        if t.steps and seq == seqs[0]:
            lines.append(f"{_n(len(t.steps), 'step')} · {_legend(tn, sid, opens=opens)}")
        lines += _turn_body(t, got.session.session_dir, tn, only)
    return "\n".join(lines)


def _verb_open(sid: str, at: tuple[int, int]) -> str:
    from .query import hint

    return hint("step", id=f"{sid[:PREFIX_MIN]}-{at[0]:03d}", n=at[1])


def open_agent_steps(got: SessionAgents, a: Agent, spec, turn: int | None = None) -> str:
    """The agent's steps whole, as `tracer trace turn --open` opens a turn's."""
    return format_steps(loaded_turn(got, a, _turn_of(a, turn)), spec)


def agent_data(got: SessionAgents, a: Agent) -> dict:
    """One agent as data: its row, its brief and report whole, and every step in one line each."""
    out = {**_row(a), "brief": a.brief, "report": a.report, "turns": []}
    for t in a.turns:
        tn = annotate(t, got.session.session_dir)
        out["turns"].append({"asked": t.prompt_text, "steps": [{
            "n": s.n, "tool": short_tool(s.tool), "tool_use_id": s.tool_use_id, "ts": s.ts,
            "what": step_label(s.tool, s.input, 200) or "",
            "seconds": _secs(s.ts, s.result_ts) if s.answered else None,
            "outcome": ("error" if s.is_error else "ok") if s.answered else "no result",
            "bytes": tn[s.n].size, "size": _fmt_bytes(tn[s.n].size), "flags": tn[s.n].flags, "gist": tn[s.n].gist,
        } for s in t.steps]})
    return out
