"""The drill: a session at a glance, a turn as numbered steps, and steps opened in full.

    tracer sessions show <session>        the session: what was asked, how it ended, time, tokens,
                                      cost, tool mix, signals — and, when it is small, every
                                      step inline
    tracer trace turn <sess>-NNN          one turn as steps, each with a one-line gist of its result
    tracer trace turn <sess>-NNN --open 1-6   steps whole: input, result (a JSON one as its shape first)

A turn is read from its transcript, never from the index alone: the index gives the byte
offset where the turn starts (so a 50 MB transcript is not re-read to find turn 40), the
transcript gives everything else. A turn the index does not know — a session never
indexed, or an index from before this numbering — is found by walking the transcript with
the same splitter the indexer uses, so the id means the same thing either way.

Where a result's full text lives, when the transcript holds less than all of it:

    <session-dir>/tool-results/<random>.txt            Claude Code, for an output too large to
                                                       inline — the transcript keeps a 2 KB
                                                       <persisted-output> preview naming the file
    <session-dir>/tool-results/<tool_use_id>.<tool>.*  the tool-results plugin's verbatim copy
    <session-dir>/subagents/agent-<id>.jsonl           an Agent call's own transcript
"""

from __future__ import annotations

import json
import re
import shlex
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .gist import as_json, gist, json_view, similarity, unwrap_mcp
from .parser import extract_file_touch
from .step_label import step_label
from .storage import find_session_files, get_conversations_dir_for_hash, match_session_ids
from .turns import Step, Turn, TurnSplitter, build_turn, one_line, short_tool
from . import wire
from ._brand import DISPLAY_NAME, GATEWAY_DATA, HOME_DIR, claude_home, posix

RESULT_CAP = 20_000
MULTI_CAP = 4_000  # per step, when several are opened at once
PARAMS_PREVIEW = 200
DID_CAP = 400  # the did: line under a turn — what its calls were for, in the model's words
LARGE = 30_000  # bytes: a result this big is flagged
SIMILAR = 0.8  # share of words two results of one tool share before the later is flagged ≈
SMALL_TURNS, SMALL_STEPS = 3, 40  # a session this small shows every step inline
BIG_TURN, TURN_EDGE = 60, 10  # a turn longer than this lists its first and last 10, errors and large results
# Where the kit's own records live. A Bash/Read/Grep that reaches in here is reading
# what a kit tool fronts, flagged [raw].
#
# Matched against the PATHS a step actually reads (_raw_read_paths below), never the
# whole tool_input blob: a bare "/tool-results/" once matched plugins/tool-results/README.md,
# an unrelated file in this repo that happens to share a folder name (review G15). Anchored to
# where a session's own transcripts and tool-results really live — <claude_home>/projects/<project>/
# <session-id>/, including its tool-results/ subdir (the tool-results plugin's saves AND Claude
# Code's own <persisted-output> files — both ARE raw) — plus the kit's store and the
# gateway's trace/blobs. A path is matched in posix() form, so a Windows path's "\" reads as "/".
RAW_TOOLS = ("Bash", "Read", "Grep", "Glob")
_CLAUDE = r"(?:\.claude|" + re.escape(posix(claude_home())) + ")"  # the default name, and $CLAUDE_CONFIG_DIR
RAW_PATTERNS = tuple(re.compile(p) for p in (
    _CLAUDE + r"/projects\b",      # a session's own dir: its transcript, its tool-results/
    _CLAUDE + r"/sessions\b",      # the live registry `tracer sessions live` reads
    re.escape(HOME_DIR) + r"/db\b",              # the kit's store: brain.db and the trace store (traces/)
    re.escape(HOME_DIR) + r"/\.store\b",         # layout 1: historical (a session recorded before layout 2 names it so)
    r"\.brain/\.store\b",         # brand: historical (a session recorded before the rename names it so)
    r"conversation-index/index\.db\b",  # the tracer's own index
    r"(?:\.local/share|AppData/Local)/" + re.escape(GATEWAY_DATA) + r"\b",   # the gateway's trace rows and body blobs
    r"\.local/share/brain-gateway\b",   # brand: historical (the same, as an old session typed it)
))
# A path-like token: has a slash anywhere, or opens with a home-relative form (~, $HOME, ${HOME}).
_PATH_LIKE = re.compile(r"^(?:~|\$\{?HOME\}?)(?:/|$)|[/\\]")
_PERSISTED_RE = re.compile(r"Full output saved to: (\S+)")
_TOO_LARGE_RE = re.compile(r"Output too large \(([\d.]+)\s*(KB|MB)\)")


def _verb(key: str) -> str:
    from .query import verb  # query imports this module's neighbours; read the door at call time

    return verb(key)


class DrillError(Exception):
    """The turn could not be read; the message says why and what to do."""


@dataclass
class LoadedTurn:
    iid: str
    session_id: str
    session_file: Path
    turn: Turn
    note: str = ""  # set when the index could not be used as-is
    results_dir: Path | None = None  # a subagent's turn: its session's dir, where its tool-results/ are

    @property
    def session_dir(self) -> Path:
        return self.results_dir or self.session_file.with_suffix("")


class AmbiguousSession(DrillError):
    """A prefix that starts more than one session; the message lists them."""


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
PREFIX_MIN = 8  # the id a listing prints: `06a45a57`
_PREFIX_RE = re.compile(r"^[0-9a-f]{%d}[0-9a-f-]{0,28}$" % PREFIX_MIN, re.I)
LATEST = "latest"


def latest_session() -> str | None:
    """The session whose newest turn is the newest in the index; else the newest transcript on disk."""
    from .db import get_readonly_connection

    try:
        con = get_readonly_connection()
        try:
            row = con.execute("SELECT session_id FROM interactions WHERE session_id IS NOT NULL AND session_id != ''"
                              " ORDER BY timestamp DESC LIMIT 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        row = None
    if row:
        return row[0]
    root = get_conversations_dir_for_hash("x").parent
    files = [p for p in root.glob("*/*.jsonl") if p.is_file() and _UUID_RE.match(p.stem)]
    return max(files, key=lambda p: p.stat().st_mtime).stem if files else None


def find_session(target: str) -> str | None:
    """The session TARGET names — a full uuid, its first 8+ characters, or `latest` — or None
    when it names none (then it may be a phrase). A prefix that starts several sessions raises."""
    target = target.strip()
    if _UUID_RE.match(target):
        return target
    if target == LATEST:
        return latest_session()
    if not _PREFIX_RE.match(target):
        return None
    hits = match_session_ids(target)
    if len(hits) > 1:
        raise AmbiguousSession(f"{target!r} is ambiguous: " + ", ".join(hits[:5]) + (" …" if len(hits) > 5 else ""))
    return hits[0] if hits else None


def resolve_session(target: str) -> str:
    """A full session uuid from a uuid, its first 8+ characters, or `latest` — the one reading of a
    session id every verb shares. A miss raises DrillError saying what it looked for."""
    sid = find_session(target)
    if sid:
        return sid
    target = target.strip()
    if target == LATEST:
        raise DrillError("no session on record for `latest`: the index is empty and no transcript is on disk")
    if not _PREFIX_RE.match(target):
        raise DrillError(f"not a session id: {target!r} — want a session UUID, its first {PREFIX_MIN} characters "
                         f"(e.g. 06a45a57), latest, or a turn id <session>-NNN")
    root = get_conversations_dir_for_hash("x").parent
    raise DrillError(f"no session starts with {target!r}: looked for {posix(root)}/*/{target}*.jsonl")


def split_target(target: str) -> tuple[str, int | None]:
    """(session uuid, turn number or None). A turn id is <session>-<digits>; the session
    part may be a prefix (`57e7a5bf-000`)."""
    target = target.strip()
    if _UUID_RE.match(target):
        return target, None
    sid, _, seq = target.rpartition("-")
    if sid and seq.isdigit() and len(seq) <= 5:
        return resolve_session(sid), int(seq)
    return resolve_session(target), None


def _split_iid(iid: str) -> tuple[str, int]:
    sid, seq = split_target(iid)
    if seq is None:
        raise DrillError(f"not an interaction id: {iid!r} (want <session-uuid>-<turn>, e.g. …-003)")
    return sid, seq


def _line_of(f, offset: int) -> int:
    """1-based line number of the record starting at byte ``offset``."""
    f.seek(0)
    line, left = 1, offset
    while left > 0:
        chunk = f.read(min(1 << 20, left))
        if not chunk:
            break
        line += chunk.count(b"\n")
        left -= len(chunk)
    return line


def _read_from_offset(path: Path, offset: int, with_lines: bool = True) -> list[tuple[int, dict]] | None:
    """The turn starting at ``offset``, or None when that byte is not where a turn opens.
    ``with_lines=False`` skips counting the newlines before it (lines then start at 1)."""
    with open(path, "rb") as f:
        line = _line_of(f, offset) if with_lines else 1
        f.seek(offset)
        raw = f.readline()
        try:
            first = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None
        splitter = TurnSplitter()
        if splitter.kind(first) != "prompt":
            return None
        records = [(line, first)]
        for raw in f:
            line += 1
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if splitter.opens_turn(msg):
                break
            records.append((line, msg))
    return records


def _read_by_number(path: Path, seq: int) -> tuple[list[tuple[int, dict]] | None, int]:
    """Turn ``seq`` counted from the transcript's start; also how many turns it has."""
    splitter = TurnSplitter()
    turns: list[list[tuple[int, dict]]] = []
    with open(path, "rb") as f:
        for line, raw in enumerate(f, 1):
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if splitter.opens_turn(msg):
                if len(turns) > seq:
                    break
                turns.append([])
            if turns:
                turns[-1].append((line, msg))
    return (turns[seq] if seq < len(turns) else None), len(turns)


def load_turn(iid: str, con: sqlite3.Connection | None = None, with_lines: bool = True) -> LoadedTurn:
    """Read one turn by interaction id. Raises DrillError with the reason.
    ``with_lines=False``: the caller never prints a transcript line number (the dialogue),
    so the bytes before the turn are not read to count them."""
    sid, seq = _split_iid(iid)
    iid = f"{sid}-{seq:03d}"
    row = None
    if con is not None:
        row = con.execute(
            "SELECT session_file, byte_offset, project_hash FROM interactions WHERE id = ?",
            (iid,),
        ).fetchone()

    note = ""
    if row:
        session_file, offset, project_hash = row
        path = Path(session_file or get_conversations_dir_for_hash(project_hash) / f"{sid}.jsonl")
        if not path.is_file():
            from .query import verb

            raise DrillError(
                f"source JSONL not available: {path} — the transcript aged out; the index keeps "
                f"only previews (`{verb('replay')} {sid[:8]}` still shows them)"
            )
        try:
            records = _read_from_offset(path, offset or 0, with_lines)
        except OSError as exc:
            raise DrillError(f"failed reading {path}: {exc}") from exc
        if records is not None:
            return LoadedTurn(iid, sid, path, build_turn(records))
        note = f"the index is behind this transcript (turn found by walking it; `{_verb('index')} --session <uuid>` refreshes)"
    else:
        found = find_session_files(sid)
        if not found:
            raise DrillError(f"interaction {iid} not found: not in the index, and no {sid}.jsonl on disk")
        path = found[0]

    records, count = _read_by_number(path, seq)
    if records is None:
        raise DrillError(f"turn {seq:03d} not found: {sid} has {count} turn(s) (000–{count - 1:03d})"
                         + (f"; {note}" if note else ""))
    return LoadedTurn(iid, sid, path, build_turn(records), note=note)


# ── result locations ─────────────────────────────────────────────────────────


def is_persisted(step: Step) -> bool:
    return step.result_text.lstrip().startswith("<persisted-output>")


def saved_result(step: Step, session_dir: Path) -> Path | None:
    """Where the step's result was written to disk, if it was: Claude Code's persisted
    output first (the transcript holds only its preview), then the tool-results plugin's
    copy. The path is returned even when the file is gone; callers check."""
    text = step.result_text
    if is_persisted(step):
        m = _PERSISTED_RE.search(text[:4000])
        if m:
            return Path(m.group(1))
    tr = session_dir / "tool-results"
    if step.tool_use_id and tr.is_dir():
        pinned = sorted(tr.glob(f"{step.tool_use_id}.*"))
        if pinned:
            return pinned[0]
    return None


def result_bytes(step: Step, saved: Path | None) -> int:
    text = step.result_text
    if is_persisted(step):
        if saved is not None and saved.is_file():
            return saved.stat().st_size
        m = _TOO_LARGE_RE.search(text)
        if m:
            return int(float(m.group(1)) * (1024 if m.group(2) == "KB" else 1024 * 1024))
    return len(text.encode("utf-8"))


def agent_verb(session_id: str, transcript: Path | None = None) -> str:
    """`tracer sessions agents <session> [<agent>]`: a session's subagents, or one agent's steps."""
    from .verbs import spell

    out = f"{spell('agents')} {session_id[:PREFIX_MIN]}"
    return out + (f" {transcript.stem.removeprefix('agent-')[:PREFIX_MIN]}" if transcript else "")


def subagent_transcript(step: Step, session_dir: Path) -> Path | None:
    """An Agent/Task call's own transcript: by the agentId its result names, else by the
    teammate name it was spawned under."""
    if step.tool not in ("Agent", "Task"):
        return None
    sub = session_dir / "subagents"
    if not sub.is_dir():
        return None
    ur = step.use_result if isinstance(step.use_result, dict) else {}
    aid = ur.get("agentId")
    if aid and (sub / f"agent-{aid}.jsonl").is_file():
        return sub / f"agent-{aid}.jsonl"
    name = step.input.get("name") if isinstance(step.input, dict) else None
    if name and not step.is_error:
        hits = []
        for meta in sub.glob("agent-*.meta.json"):
            try:
                if json.loads(meta.read_text(encoding="utf-8")).get("name") == name:
                    hits.append(meta.with_name(meta.name[: -len(".meta.json")] + ".jsonl"))
            except (OSError, ValueError):
                continue
        hits = [h for h in hits if h.is_file()]
        if hits:
            return max(hits, key=lambda p: p.stat().st_mtime)
    return None


# ── formatting ───────────────────────────────────────────────────────────────


def _dt(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _secs(a: str, b: str) -> float | None:
    ta, tb = _dt(a), _dt(b)
    if ta is None or tb is None:
        return None
    return max(0.0, (tb - ta).total_seconds())


def _fmt_secs(s: float | None) -> str:
    if s is None:
        return "?"
    if s < 1:
        return "<1s"
    if s < 60:
        return f"{s:.0f}s"
    m, sec = divmod(int(s), 60)
    if m < 60:
        return f"{m}m{sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _fmt_count(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.2f}M"


def _hms(ts: str) -> str:
    return ts[11:19] if len(ts) >= 19 else "??:??:??"


def _short(path: Path | str) -> str:
    s = str(path)
    home = str(Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s


def params_preview(inp: dict, cap: int = PARAMS_PREVIEW) -> str:
    s = json.dumps(inp, ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= cap else s[: cap - 1] + "…"


def _line_count(path: Path, limit: int = 20 << 20) -> int | None:
    try:
        if path.stat().st_size > limit:
            return None
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


@dataclass
class StepNote:
    """What the drill says about a step beyond the call: size, where the whole result
    is, its gist, and the flags a machine can check."""
    size: int = 0
    saved: Path | None = None
    persisted: bool = False
    gist: str = ""
    flags: list[str] = None


# What each flag means, printed once under the steps that carry one.
FLAG_LEGEND = {
    "=": "[=#N] the same call as step N",
    "≈": f"[≈#N] a result ≥{int(SIMILAR * 100)}% the same words as step N's (same tool)",
    "large": f"[large] ≥{LARGE // 1000} KB",
    "raw": f"[raw] reads transcripts or {DISPLAY_NAME} stores directly",
    "miss": "[miss] the call wrote more to the cache than it read — a full-price call",
    "sysΔ": "[sysΔ] its system prompt differs from the call before (gateway)",
    "toolsΔ": "[toolsΔ] its tool list differs from the call before (gateway)",
    "acct": "[acct] a different account than the call before (gateway) — the cache does not follow",
}
WIRE_CHANGE = {"sysΔ": "system prompt", "toolsΔ": "tool list", "acct": "account"}
_UNSET = object()  # format_step loads the wire itself unless its caller already did


@dataclass
class WireCtx:
    """The gateway's calls for some turns, joined to their messages (wire.py)."""
    calls: list[wire.Call]
    joined: dict[str, wire.Call]
    changed: dict[str, list[str]]


def wire_for(session_id: str, turns: list[Turn]) -> WireCtx | None:
    """None when the session never ran through the gateway."""
    if not turns:
        return None
    calls = wire.load(session_id, turns[0].ts, turns[-1].end_ts or turns[-1].ts)
    if not calls:
        return None
    msgs = [(mid, u) for t in turns for mid, u in t.usage.items()]
    return WireCtx(calls, wire.join(calls, msgs), wire.changes(calls))


def _bash_path_candidates(command: str) -> list[str]:
    """The words of a shell command that could be a path: split like the shell would, so a
    quoted argument stays one token and a flag glued to a path ("2>/dev/null") is not read
    as the path itself. `shlex` on an unbalanced quote (a heredoc, an odd apostrophe) falls
    back to a whitespace split — still finds a plain `cat path` even when it cannot fully
    parse the line."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    out = []
    for t in tokens:
        t = re.sub(r"^[<>|;&]+", "", t)  # a redirect/pipe glued to the path: "2>/path", "|cat"
        if _PATH_LIKE.search(t):
            out.append(t)
    return out


def _raw_read_paths(tool: str, tool_input: dict) -> list[str]:
    """The paths a step actually reads — never a Grep/Glob PATTERN (a search regex is not a
    path; matching one against RAW_PATTERNS is the same mistake as matching the whole
    tool_input was, just moved to a different field — a grep for the word "tool-results" is
    not a raw read of anything)."""
    if tool == "Bash":
        return _bash_path_candidates(str((tool_input or {}).get("command") or ""))
    if tool == "Read":
        p = (tool_input or {}).get("file_path") or (tool_input or {}).get("notebook_path")
        return [p] if p else []
    if tool in ("Grep", "Glob"):
        p = (tool_input or {}).get("path")
        return [p] if p else []
    return []


def _is_raw_read(tool: str, tool_input: dict) -> bool:
    return any(p.search(posix(path)) for path in _raw_read_paths(tool, tool_input) for p in RAW_PATTERNS)


def annotate(turn: Turn, sdir: Path, wctx: WireCtx | None = None, first_turn: bool = False) -> dict[int, StepNote]:
    """Size, saved path, gist and flags for every step of a turn. The first step of each
    message also carries its call's: [miss] from the transcript's own usage, and — on a
    gateway session — what changed since the call before."""
    notes: dict[int, StepNote] = {}
    texts: dict[int, str] = {}
    first_mid = next(iter(turn.usage), None) if first_turn else None
    prev_mid = None
    for s in turn.steps:
        note = StepNote(flags=[])
        if s.answered:
            note.saved = saved_result(s, sdir)
            note.persisted = is_persisted(s)
            note.size = result_bytes(s, note.saved)
            lines = _line_count(note.saved) if note.persisted and note.saved and note.saved.is_file() else None
            note.gist = gist(s.result_text, s.tool, lines)
            texts[s.n] = unwrap_mcp(s.result_text)[0]
        earlier = [p for p in turn.steps[: s.n - 1] if p.tool == s.tool]
        same = next((p for p in earlier if p.input == s.input), None)
        if same is not None:
            note.flags.append(f"=#{same.n}")
        elif s.n in texts:
            alike = next((p for p in earlier if p.n in texts and similarity(texts[s.n], texts[p.n]) >= SIMILAR), None)
            if alike is not None:
                note.flags.append(f"≈#{alike.n}")
        if note.size >= LARGE:
            note.flags.append("large")
        if s.tool in RAW_TOOLS and _is_raw_read(s.tool, s.input):
            note.flags.append("raw")
        if s.message_id and s.message_id != prev_mid:
            u = turn.usage.get(s.message_id) or {}
            if s.message_id != first_mid and \
                    int(u.get("cache_creation_input_tokens") or 0) > int(u.get("cache_read_input_tokens") or 0):
                note.flags.append("miss")
            call = wctx.joined.get(s.message_id) if wctx else None
            if call is not None:
                note.flags += wctx.changed.get(call.row_id, [])
        prev_mid = s.message_id
        notes[s.n] = note
    return notes


def _legend(notes: dict[int, StepNote], iid: str, opens: str = "") -> str:
    """``opens``: the command that opens steps 1-3, when it is not `tracer trace turn <iid> --open`."""
    from .query import hint

    used = {("=" if f.startswith("=") else "≈" if f.startswith("≈") else f)
            for n in notes.values() for f in n.flags}
    parts = ["(+Δ) model time before the call, trailing = tool time", "∥ same message as the step above",
             "↳ what came back"]
    parts += [FLAG_LEGEND[k] for k in ("=", "≈", "large", "raw", "miss", "sysΔ", "toolsΔ", "acct") if k in used]
    parts.append(f'{opens or hint("step", id=iid, n="1-3")} opens steps')
    return "legend: " + " · ".join(parts)


def step_lines(s: Step, note: StepNote, parallel: bool, sdir: Path) -> list[str]:
    when = "∥" if parallel else f"(+{_fmt_secs(_secs(s.gap_from, s.ts))})"
    what = step_label(s.tool, s.input, PARAMS_PREVIEW) or params_preview(s.input)
    line = f"#{s.n} {_hms(s.ts)} {when} {short_tool(s.tool)} {what}"
    if s.answered:
        status = "ERROR" if s.is_error else "ok"
        line += f" → {_fmt_bytes(note.size)} {status} {_fmt_secs(_secs(s.ts, s.result_ts))}"
        if note.persisted and note.saved is not None:
            # only here does the transcript hold less than the whole result
            line += f" saved: {note.saved.name}" + ("" if note.saved.is_file() else " (gone)")
    else:
        line += " → no result"
    sub = subagent_transcript(s, sdir)
    if sub is not None:
        line += f" · transcript: subagents/{sub.name} · its steps: {agent_verb(sdir.name, sub)}"
    if note.flags:
        line += " " + " ".join(f"[{f}]" for f in note.flags)
    out = [line]
    if note.gist:
        out.append(f"   ↳ {note.gist}")
    return out


def _shown(turn: Turn, notes: dict[int, StepNote], only: list[int] | None) -> set[int]:
    """Which steps a turn lists: those asked for; else all, unless the turn is long — then
    its first and last TURN_EDGE, every step that failed, and every large result. Repeats
    and raw reads in between are counted on the line that stands for the run (a turn that
    polls a monitor repeats itself a hundred times)."""
    count = len(turn.steps)
    if only is not None:
        return {n for n in only if 1 <= n <= count}
    if count <= BIG_TURN:
        return set(range(1, count + 1))
    keep = set(range(1, TURN_EDGE + 1)) | set(range(count - TURN_EDGE + 1, count + 1))
    keep |= {s.n for s in turn.steps if s.is_error or "large" in notes[s.n].flags}
    return keep


def _turn_body(turn: Turn, sdir: Path, notes: dict[int, StepNote], only: list[int] | None = None) -> list[str]:
    from .query import describe_ending

    lines: list[str] = []
    shown = _shown(turn, notes, only)
    skipped: list[Step] = []

    def flush():
        if not skipped or only is not None:
            skipped.clear()
            return
        a, b = skipped[0].n, skipped[-1].n
        flags = Counter("rep" if f[0] in "=≈" else f for st in skipped for f in notes[st.n].flags)
        flag_s = ("; ⚑ " + " ".join(f"{k}{n}" for k, n in flags.items())) if flags else ""
        mix = _tools_mix(skipped)
        mix = mix if len(mix) <= 70 else mix[:69] + "…"
        lines.append(f"  … #{a}–#{b} ({_n(len(skipped), 'step')}: {mix}{flag_s}) — --steps {a}-{b}")
        skipped.clear()

    prev_mid = None
    for s in turn.steps:
        if s.n not in shown:
            skipped.append(s)
            prev_mid = s.message_id
            continue
        flush()
        if s.lead:
            kind, body = s.lead[-1]
            lines.append(f"  {'think' if kind == 'thinking' else 'said'}: {one_line(body, 160)}")
        parallel = bool(s.message_id) and s.message_id == prev_mid
        prev_mid = s.message_id
        lines += step_lines(s, notes[s.n], parallel, sdir)
    flush()
    if only is not None:
        return lines
    if turn.closing and not turn.final_text:
        kind, body = turn.closing[-1]
        lines.append(f"  {'think' if kind == 'thinking' else 'said'}: {one_line(body, 160)}")
    if turn.interrupted_after is not None:
        lines.append(f"ended: {describe_ending(turn.ending, turn.steps)} at {_hms(turn.interrupted_ts)}")
    elif turn.final_text:
        lines.append(f"final: {one_line(turn.final_text, 400)}")
    else:
        lines.append(f"ended: {describe_ending(turn.ending, turn.steps) or 'no output'}")
    for lc in turn.local_commands:
        lines.append(f"  {lc.line()}")
    return lines


def _did(steps: list[Step], cap: int = DID_CAP) -> str:
    """What a turn's calls were for, in the model's own words: each description once, in
    order, ✗ on a call that failed — the line under a turn when its steps are not listed."""
    seen: set[str] = set()
    out: list[str] = []
    for s in steps:
        desc = s.input.get("description") if isinstance(s.input, dict) else None
        if not isinstance(desc, str) or not desc.strip():
            continue
        said = one_line(desc, 80) + (" ✗" if s.is_error else "")
        if said not in seen:
            seen.add(said)
            out.append(said)
    return one_line(" · ".join(out), cap)


def _tools_mix(steps: list[Step]) -> str:
    tools = Counter(short_tool(s.tool) for s in steps)
    return ", ".join(f"{t}×{n}" if n > 1 else t for t, n in tools.most_common()) or "-"


def format_turn(loaded: LoadedTurn, only: list[int] | None = None) -> str:
    """The step list: one line per tool call and one for what came back, what the model
    thought before it, how the turn ended. ``only``: list just these steps."""
    turn, sdir = loaded.turn, loaded.session_dir
    branch = turn.prompt.get("gitBranch", "") or "-"
    ts = turn.ts
    head = f"{loaded.iid} · {ts[:10]} {_hms(ts)} UTC · {branch} · {_fmt_secs(_secs(ts, turn.end_ts))}"
    lines = [head]
    if loaded.note:
        lines.append(f"note: {loaded.note}")
    lines.append(f"prompt: {one_line(turn.prompt_text, 300) or '(empty)'}")

    files: dict[str, set[str]] = {}
    for s in turn.steps:
        ft = extract_file_touch(s.tool, s.input)
        if ft:
            files.setdefault(ft.path, set()).add(ft.operation)
    files_s = "; ".join(f"{_short(p)} ({','.join(sorted(ops))})" for p, ops in list(files.items())[:8])
    if len(files) > 8:
        files_s += f"; +{len(files) - 8} more"
    lines.append(f"tools: {_tools_mix(turn.steps)} · files: {files_s or '-'}")
    notes = annotate(turn, sdir, wire_for(loaded.session_id, [turn]), first_turn=loaded.iid.endswith("-000"))
    if turn.steps:
        lines.append(f"{_n(len(turn.steps), 'step')} · {_legend(notes, loaded.iid)}")
    lines += _turn_body(turn, sdir, notes, only)
    return "\n".join(lines)


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


# ── the session view ─────────────────────────────────────────────────────────


@dataclass
class LoadedSession:
    session_id: str
    session_file: Path
    turns: list[Turn]
    title: str = ""
    cwd: str = ""
    branch: str = ""
    version: str = ""
    entrypoint: str = ""
    cost: dict | None = None  # Claude Code's last cost-state record

    @property
    def session_dir(self) -> Path:
        return self.session_file.with_suffix("")


def load_session(target: str) -> LoadedSession:
    """Every turn of a session, from its transcript (a uuid or a unique prefix of one)."""
    sid = resolve_session(target)
    found = find_session_files(sid)
    if not found:
        raise DrillError(f"no transcript on disk for {sid}")
    return load_transcript(found[0], sid)


def load_transcript(path: Path, sid: str) -> LoadedSession:
    """Every turn of one transcript file: a session's, or a subagent's (agents.py)."""
    ls = LoadedSession(sid, path, [])
    splitter = TurnSplitter()
    current: list[tuple[int, dict]] | None = None
    ai_title = custom_title = ""
    with open(path, "rb") as f:
        for line, raw in enumerate(f, 1):
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "cost-state":
                ls.cost = msg
            elif kind == "ai-title" and msg.get("aiTitle"):
                ai_title = str(msg["aiTitle"])
            elif kind == "custom-title" and msg.get("customTitle"):
                custom_title = str(msg["customTitle"])
            ls.cwd = ls.cwd or str(msg.get("cwd") or "")
            ls.branch = ls.branch or str(msg.get("gitBranch") or "")
            ls.version = ls.version or str(msg.get("version") or "")
            ls.entrypoint = ls.entrypoint or str(msg.get("entrypoint") or "")
            if splitter.opens_turn(msg):
                if current:
                    ls.turns.append(build_turn(current))
                current = []
            if current is not None:
                current.append((line, msg))
    if current:
        ls.turns.append(build_turn(current))
    ls.title = custom_title or ai_title
    return ls


def _tool_seconds(turn: Turn) -> float:
    """Wall time some tool was running: the union of the steps' call → result spans."""
    spans = sorted(
        (a, b) for a, b in ((_dt(s.ts), _dt(s.result_ts)) for s in turn.steps if s.answered) if a and b
    )
    total, cur_a, cur_b = 0.0, None, None
    for a, b in spans:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += (cur_b - cur_a).total_seconds()
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += (cur_b - cur_a).total_seconds()
    return total


def _tokens_line(ls: LoadedSession) -> str:
    cost = ls.cost or {}
    usage = cost.get("modelUsage") if isinstance(cost.get("modelUsage"), dict) else None
    if usage:
        tot = Counter()
        for u in usage.values():
            for k in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "outputTokens", "thinkingTokens"):
                tot[k] += int(u.get(k) or 0)
        per = ""
        if len(usage) > 1:
            per = " (" + ", ".join(f"{m} ${float(u.get('costUSD') or 0):.2f}" for m, u in usage.items()) + ")"
        return (
            f"tokens: in {_fmt_count(tot['inputTokens'])} · cache read {_fmt_count(tot['cacheReadInputTokens'])}"
            f" · cache write {_fmt_count(tot['cacheCreationInputTokens'])} · out {_fmt_count(tot['outputTokens'])}"
            f" (thinking {_fmt_count(tot['thinkingTokens'])}) · ${float(cost.get('totalCostUSD') or 0):.2f}{per}"
            " — Claude Code's own count"
        )
    tot = Counter()
    models: Counter = Counter()
    for turn in ls.turns:
        for mid, u in turn.usage.items():
            tot["in"] += int(u.get("input_tokens") or 0)
            tot["read"] += int(u.get("cache_read_input_tokens") or 0)
            tot["write"] += int(u.get("cache_creation_input_tokens") or 0)
            tot["out"] += int(u.get("output_tokens") or 0)
            tot["think"] += int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
            model = turn.models.get(mid, "")
            if model and not model.startswith("<"):  # "<synthetic>": an interrupt's placeholder
                models[model] += 1
    if not tot:
        return "tokens: none recorded"
    return (
        f"tokens: in {_fmt_count(tot['in'])} · cache read {_fmt_count(tot['read'])} · cache write"
        f" {_fmt_count(tot['write'])} · out {_fmt_count(tot['out'])} (thinking {_fmt_count(tot['think'])})"
        f" · {', '.join(models) or '?'} · cost not recorded (no cost-state in the transcript)"
    )


def _wire_line(w: WireCtx) -> str:
    """The session's calls as the gateway saw them — including the ones no transcript holds."""
    cls = Counter(c.request_class or "main" for c in w.calls)
    parts = [f"wire: {_n(len(w.calls), 'call')} through the gateway, {len(w.joined)} matched to this transcript's messages"]
    extra = [f"{cls[k]} {label}" for k, label in (("subagent", "subagent"), ("auxiliary", "side"), ("compaction", "compaction")) if cls.get(k)]
    if extra:
        parts.append(" · ".join(extra))
    ttfb = sorted(c.ttfb_ms for c in w.calls if c.ttfb_ms is not None and c.request_class in ("main", ""))
    if ttfb:
        parts.append(f"first byte median {_fmt_secs(ttfb[len(ttfb) // 2] / 1000)}, slowest {_fmt_secs(ttfb[-1] / 1000)}")
    accts = Counter(c.account for c in w.calls if c.account)
    if accts:
        parts.append("accounts " + ", ".join(f"{a} ×{k}" for a, k in accts.most_common(3)))
    return " · ".join(parts) + " — a step opened whole shows its call and what the model was given"


def _signals(ls: LoadedSession, notes: list[dict[int, StepNote]]) -> str:
    one = len(ls.turns) == 1

    def ref(seq: int, n: int | None = None) -> str:
        t = "" if one else f"{seq:03d}"
        return f"{t}#{n}" if n is not None else t

    kinds: dict[str, list[str]] = {}
    for seq, (turn, tnotes) in enumerate(zip(ls.turns, notes)):
        if turn.interrupted_after is not None:
            kinds.setdefault("interrupted", []).append(f"{ref(seq)} after #{turn.interrupted_after}".strip())
        elif not turn.final_text:
            kinds.setdefault("no answer", []).append(ref(seq))
        for s in turn.steps:
            nt = tnotes[s.n]
            if s.is_error:
                kinds.setdefault("errors", []).append(f"{ref(seq, s.n)} {short_tool(s.tool)}")
            for f in nt.flags:
                if f[0] in "=≈":
                    kinds.setdefault("repeats", []).append(f"{ref(seq, s.n)}{f}")
                elif f == "large":
                    kinds.setdefault("large", []).append(f"{ref(seq, s.n)} {_fmt_bytes(nt.size)}")
                elif f == "raw":
                    kinds.setdefault("raw reads", []).append(ref(seq, s.n))
                elif f == "miss":
                    kinds.setdefault("cache misses", []).append(ref(seq, s.n))
                elif f in WIRE_CHANGE:
                    kinds.setdefault(f"{WIRE_CHANGE[f]} changed", []).append(ref(seq, s.n))
    if not kinds:
        return "signals: none — every turn answered, no errors, repeats, large results or raw reads"
    parts = []
    for k in ("interrupted", "no answer", "errors", "repeats", "large", "raw reads", "cache misses",
              "system prompt changed", "tool list changed", "account changed"):
        refs = kinds.get(k)
        if not refs:
            continue
        if len(refs) <= 6:
            parts.append(f"{k} {', '.join(refs)}")
        elif one:
            parts.append(f"{k} {', '.join(refs[:6])} +{len(refs) - 6}")
        else:  # many, across turns: how many in which turn, the turn line says the rest
            per = Counter(r[:3] for r in refs)
            parts.append(f"{k} {len(refs)} in " + ", ".join(f"{t}×{n}" for t, n in per.most_common(6)))
    return "signals: " + " · ".join(parts)


def format_session(ls: LoadedSession) -> str:
    """One session at a glance — the place to start. Small sessions carry every step."""
    from .query import describe_ending, hint

    turns = ls.turns
    if not turns:
        return f"{ls.session_id}: no turns (nothing was asked) · {_short(ls.session_file)}"
    first = turns[0]
    steps = [s for t in turns for s in t.steps]
    who = "" if not ls.entrypoint or ls.entrypoint in ("cli",) else f" · {ls.entrypoint}"
    head = [f"{ls.session_id}" + (f' · "{ls.title}"' if ls.title else "")
            + f" · {first.ts[:10]} {first.ts[11:16]} UTC · {_short(ls.cwd) or '?'} · {ls.branch or '-'}"
            + (f" · claude {ls.version}" if ls.version else "") + who]
    head.append(f"asked: {one_line(first.prompt_text, 240) or '(empty)'}")
    if len(turns) > 1:
        head.append(f"last asked ({len(turns) - 1:03d}): {one_line(turns[-1].prompt_text, 200)}")
    small = len(turns) <= SMALL_TURNS and len(steps) <= SMALL_STEPS
    last = turns[-1]
    if last.interrupted_after is not None:
        ended = f"{describe_ending(last.ending, last.steps)} — no answer"
    elif last.final_text:
        # a small session prints its final text under the steps; say it once
        ended = "answered (final text below)" if small else f"answered: {one_line(last.final_text, 200)}"
    else:
        ended = describe_ending(last.ending, last.steps) or "no output"
    head.append(f"ended: {ended}")

    wall = sum(_secs(t.ts, t.end_ts) or 0 for t in turns)
    cost = ls.cost or {}
    if cost.get("totalAPIDuration") is not None:
        time_s = (f"model {_fmt_secs(cost['totalAPIDuration'] / 1000)} · tools "
                  f"{_fmt_secs((cost.get('totalToolDuration') or 0) / 1000)}"
                  " (Claude Code's count; calls running in parallel add up)")
    else:
        tool = sum(_tool_seconds(t) for t in turns)
        time_s = f"tools {_fmt_secs(tool)} · model and the rest {_fmt_secs(max(wall - tool, 0))}"
    head.append(f"{_n(len(turns), 'turn')} · {_n(len(steps), 'step')} · "
                f"{_fmt_secs(wall)} in turns: {time_s}")
    head.append(_tokens_line(ls))
    wctx = wire_for(ls.session_id, turns)
    if wctx is not None:
        head.append(_wire_line(wctx))
    head.append(f"tools: {_tools_mix(steps)}")
    notes = [annotate(t, ls.session_dir, wctx, first_turn=seq == 0) for seq, t in enumerate(turns)]
    head.append(_signals(ls, notes))

    lines = head
    if small:
        merged = {f"{i}.{k}": v for i, tn in enumerate(notes) for k, v in tn.items()}
        if steps:
            lines.append(_legend(merged, f"{ls.session_id}-000"))
        for seq, (turn, tnotes) in enumerate(zip(turns, notes)):
            lines.append("")
            label = f"── turn {seq:03d} · {_hms(turn.ts)} · {_n(len(turn.steps), 'step')} · {_fmt_secs(_secs(turn.ts, turn.end_ts))}"
            if len(turns) > 1:
                label += f" · asked: {one_line(turn.prompt_text, 160)}"
            lines.append(label)
            lines += _turn_body(turn, ls.session_dir, tnotes)
        return "\n".join(lines)

    lines.append("")
    for seq, (turn, tnotes) in enumerate(zip(turns, notes)):
        flags = Counter()
        for s in turn.steps:
            if s.is_error:
                flags["err"] += 1
            for f in tnotes[s.n].flags:
                flags["rep" if f[0] in "=≈" else f] += 1
        how = ("answered" if turn.final_text and turn.interrupted_after is None
               else describe_ending(turn.ending, turn.steps) or "no output")
        mix = _tools_mix(turn.steps)
        mix = mix if len(mix) <= 60 else mix[:59] + "…"
        flag_s = " ".join(f"{k}{n}" for k, n in flags.items())
        lines.append(
            f"[{seq:03d}] {_hms(turn.ts)} · {_n(len(turn.steps), 'step')} · {_fmt_secs(_secs(turn.ts, turn.end_ts))}"
            f" · {how} · {mix}" + (f" · ⚑ {flag_s}" if flag_s else "")
            + f" · asked: {one_line(turn.prompt_text, 100)}"
        )
        if did := _did(turn.steps):
            lines.append(f"      did: {did}")
    lines.append("")
    lines.append(f"next: {hint('drill', id=f'{ls.session_id}-NNN')} for a turn's steps"
                 " (⚑ err = failed calls, rep = repeats, large, raw = reads transcripts/stores directly,"
                 " miss = a full-price call" + (", sysΔ · toolsΔ · acct = what changed before it" if wctx else "") + ")")
    if (ls.session_dir / "subagents").is_dir():
        lines.append(f"subagents: {agent_verb(ls.session_id)} — one row each: what it was asked, the turn that"
                     " started it, calls, time, tokens; add an agent's id for its steps")
    return "\n".join(lines)


def open_target(target: str, con: sqlite3.Connection | None = None) -> str:
    """What a target names: a session uuid (or prefix) → the session view; a turn id →
    its steps."""
    sid, seq = split_target(target)
    if seq is None:
        return "session", sid
    return "turn", f"{sid}-{seq:03d}"


def render(target: str, con: sqlite3.Connection | None = None, steps=None) -> str:
    """`tracer sessions show` / `tracer trace turn`: a session uuid (or prefix) → the session view; a
    turn id → its steps (``steps`` lists just those — "11-40", [3, 7])."""
    kind, ref = open_target(target, con)
    if kind == "session" and steps is None:
        return format_session(load_session(ref))
    if kind == "session":
        ls = load_session(ref)
        if len(ls.turns) != 1:
            raise DrillError(f"{ref} has {len(ls.turns)} turns — name one: {ref}-NNN")
        ref = f"{ref}-000"
    loaded = load_turn(ref, con=con)
    only = parse_steps(steps, len(loaded.turn.steps)) if steps is not None else None
    return format_turn(loaded, only=only)


# ── steps opened whole ───────────────────────────────────────────────────────


def parse_steps(spec, count: int) -> list[int]:
    """5 · "5" · "1-6" · "1,4,5" · [1, 4, 5] · "all" → step numbers, in order, deduped."""
    if isinstance(spec, int):
        items = [spec]
    elif isinstance(spec, (list, tuple)):
        items = list(spec)
    else:
        text = str(spec).strip().lower()
        if text in ("all", "*"):
            return list(range(1, count + 1))
        items = []
        for part in text.replace(" ", "").split(","):
            if not part:
                continue
            a, sep, b = part.partition("-")
            if not a.isdigit() or (sep and not b.isdigit()):
                raise DrillError(f"not a step list: {spec!r} (want 5, \"1-6\", \"1,4,5\" or [1,4,5])")
            items += list(range(int(a), int(b) + 1)) if sep else [int(a)]
    out: list[int] = []
    for n in items:
        n = int(n)
        if n not in out:
            out.append(n)
    return out


def _render_input(inp: dict) -> str:
    """Every field whole. A multi-line string prints as a raw block, not a JSON string
    full of \\n — a heredoc should read like the heredoc it was."""
    out = []
    for key, val in inp.items():
        if isinstance(val, str) and "\n" in val:
            out.append(f"  {key}: |")
            out.extend(f"    {ln}" for ln in val.split("\n"))
        else:
            out.append(f"  {key}: {json.dumps(val, ensure_ascii=False)}")
    return "\n".join(out) or "  (none)"


def _usage_line(turn: Turn, step: Step) -> str:
    u = turn.usage.get(step.message_id) or {}
    model = turn.models.get(step.message_id, "")
    if not u and not model:
        return ""
    parts = []
    if u:
        parts.append(
            f"in {u.get('input_tokens', 0):,} · cache read {u.get('cache_read_input_tokens', 0):,}"
            f" · cache write {u.get('cache_creation_input_tokens', 0):,} · out {u.get('output_tokens', 0):,}"
        )
        thinking = (u.get("output_tokens_details") or {}).get("thinking_tokens")
        if thinking:
            parts[-1] += f" (thinking {thinking:,})"
    shared = [s.n for s in turn.steps if s.message_id == step.message_id]
    tail = f" — one message, shared by steps {shared[0]}–{shared[-1]}" if len(shared) > 1 else ""
    return f"tokens: {' '.join(parts)}{' · ' if parts and model else ''}{model}{tail}"


def _wire_lines(call: wire.Call, changed: list[str]) -> list[str]:
    secs = lambda ms: _fmt_secs(ms / 1000) if ms is not None else "?"  # noqa: E731
    acct = f"{call.account} ({call.account_source})" if call.account_source else (call.account or "?")
    head = (f"wire: row {call.row_id} · first byte {secs(call.ttfb_ms)} · {secs(call.dur_ms)} · {acct} · "
            f"{_n(call.n_tools, 'tool')} · {_fmt_bytes(call.bytes_in)} sent · joined by "
            + ("message id" if call.by == "id" else "token counts"))
    if changed:
        head += " · changed since the call before: " + ", ".join(WIRE_CHANGE[f] for f in changed)
    given = [f"{label} {_short(p)}" for label, h in (("system prompt", call.system), ("tools", call.tools), ("messages", call.body))
             if (p := call.blob(h)) is not None and p.is_file()]
    return [head] + (["given: " + " · ".join(given)] if given else [])


def format_step(loaded: LoadedTurn, n: int, cap: int = RESULT_CAP,
                usage: bool = True, wctx=_UNSET) -> str:
    """One step whole: what the model thought before it, the full input, timing, tokens,
    and the result — a JSON one as its shape and first rows first — capped, with the path
    that holds the rest."""
    turn, sdir = loaded.turn, loaded.session_dir
    if not turn.steps:
        return f"{loaded.iid} has no steps (no tool calls)."
    if not 1 <= n <= len(turn.steps):
        return f"{loaded.iid} has {len(turn.steps)} step(s); step {n} does not exist."
    s = turn.steps[n - 1]
    status = ("ERROR" if s.is_error else "ok") if s.answered else "no result"
    took = f" → {_hms(s.result_ts)} ({_fmt_secs(_secs(s.ts, s.result_ts))})" if s.answered else ""
    lines = [f"{loaded.iid} step {n}/{len(turn.steps)} · {s.tool} · {_hms(s.ts)}{took} · {status}"]
    if loaded.note:
        lines.append(f"note: {loaded.note}")
    usage_s = _usage_line(turn, s) if usage else ""
    if usage_s:
        lines.append(usage_s)
    if usage:
        wctx = wire_for(loaded.session_id, [turn]) if wctx is _UNSET else wctx
        call = wctx.joined.get(s.message_id) if wctx else None
        if call is not None:
            lines += _wire_lines(call, wctx.changed.get(call.row_id, []))
    for kind, body in s.lead:
        lines.append("")
        lines.append(f"{'thinking' if kind == 'thinking' else 'said'} before the call:")
        lines.append(body.strip())
    lines.append("")
    lines.append("input:")
    lines.append(_render_input(s.input))

    lines.append("")
    source = f"{_short(loaded.session_file)}:{s.line}"
    if not s.answered:
        lines.append("result: none — " + (
            "the user interrupted before it came back" if turn.interrupted_after is not None
            else "the transcript ends before it came back"))
    else:
        saved = saved_result(s, sdir)
        text = s.result_text
        where = f"{_short(loaded.session_file)}:{s.result_line}"
        if is_persisted(s):
            if saved is not None and saved.is_file():
                text = saved.read_text(encoding="utf-8", errors="replace")
                where = _short(saved)
            else:
                lines.append(f"(the full output was saved to {saved or '?'}, which is gone — the transcript kept this preview)")
        elif saved is not None:
            where = _short(saved)
        size = result_bytes(s, saved)
        text, unwrapped = unwrap_mcp(text)
        kind = "result (the MCP {\"result\": …} envelope unwrapped)" if unwrapped else "result"
        obj = as_json(text)
        if obj is not None:
            view = json_view(obj)
            lines += view
            # the view already says most of what the raw text would; when several steps
            # share a call, give the raw a third of the budget
            budget = cap - sum(len(v) for v in view)
            cap = max(budget // 3 if cap <= MULTI_CAP else budget, 800)
            kind = "raw " + kind
        n_lines = len(text.splitlines())
        if len(text) > cap:
            lines.append(f"{kind} · {size:,} B · {_n(n_lines, 'line').replace(str(n_lines), f'{n_lines:,}', 1)} · first {cap:,} of {len(text):,} chars:")
            lines.append(text[:cap])
            lines.append(f"… {len(text) - cap:,} more chars — the whole result: {where}")
        else:
            lines.append(f"{kind} · {size:,} B:")
            lines.append(text)
            if saved is not None:
                lines.append(f"(saved: {_short(saved)})")
    sub = subagent_transcript(s, sdir)
    if s.tool in ("Agent", "Task"):
        lines.append("")
        if sub is not None:
            lines.append(f"subagent transcript: {_short(sub)}")
        else:
            lines.append(f"subagent transcript: none found under {_short(sdir / 'subagents')}/")
    lines.append("")
    tail = f" (call) · :{s.result_line} (result)" if s.answered else " (call)"
    lines.append(f"source: {source}{tail}")
    return "\n".join(lines)


def format_steps(loaded: LoadedTurn, spec) -> str:
    """Several steps in one call. One step gets the full cap; several get MULTI_CAP each,
    and a token line only when the message changes."""
    ns = parse_steps(spec, len(loaded.turn.steps))
    if len(ns) == 1:
        return format_step(loaded, ns[0])
    out, prev_mid = [], None
    wctx = wire_for(loaded.session_id, [loaded.turn])
    for n in ns:
        mid = loaded.turn.steps[n - 1].message_id if 1 <= n <= len(loaded.turn.steps) else None
        out.append(format_step(loaded, n, cap=MULTI_CAP, usage=mid != prev_mid, wctx=wctx))
        prev_mid = mid
    return ("\n\n" + "═" * 8 + "\n").join(out)


def open_steps(target: str, spec, con: sqlite3.Connection | None = None) -> str:
    """`tracer trace turn --open`: a turn id, or a session id when the session has one turn."""
    sid, seq = split_target(target)
    if seq is None:
        ls = load_session(sid)
        if len(ls.turns) != 1:
            raise DrillError(f"{sid} has {len(ls.turns)} turns — name one: {sid}-NNN")
        seq = 0
    return format_steps(load_turn(f"{sid}-{seq:03d}", con=con), spec)
