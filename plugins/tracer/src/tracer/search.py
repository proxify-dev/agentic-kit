"""`ak sessions search`: which sessions talked about X, ranked by how much they did.

A plain-text, case-insensitive scan of the raw transcripts — no index, so a session
indexed late is still found. One ripgrep pass finds the matching lines (a pure-Python
walk when ripgrep is absent); each is parsed and asked two things:

    is the term in its TEXT?          a hit in `cwd`, a uuid or a signature is not a hit
    is that text CONVERSATION?        prompts, assistant text and thinking, tool inputs and
                                      outputs count. Hook output, attachments, titles, mode
                                      records and injected skill bodies are metadata: left
                                      out unless --include-meta. Counted, they put the asking
                                      session (its own title and last-prompt) and every
                                      session whose SessionStart hook mentions the term
                                      above the sessions that discussed it.

Sessions rank by conversation hits, the latest hit breaking ties. Only top-level session
transcripts are scanned — the files the index and the drill read. A subagent's or a
workflow's transcript (<uuid>/subagents/…) is a sidechain of its parent session, not a
session of its own; what it found reaches the parent as the Agent call's result.

Which sessions are listed at all is decided in one place, leave_out():

    the asking session       Claude Code puts CLAUDE_CODE_SESSION_ID in the environment of
                             every tool it launches; `--exclude-session` names another
                             session, or none
    automated sessions       a transcript's `entrypoint` says who started it (see
                             HUMAN_ENTRYPOINTS); programs are hidden unless --entrypoint all;
                             `--entrypoint sdk-cli,…` lists only those (parse_entrypoints)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .parser import _ERROR_LINE_RE
from .turns import (
    TurnSplitter, one_line, prompt_text, result_text, sender, short_tool, strip_tags, user_kind, user_text,
)

DEFAULT_LIMIT = 10

# Who started a session, by the `entrypoint` every transcript record carries (and hooks see
# as CLAUDE_CODE_ENTRYPOINT): a person in the terminal, the desktop app, or the TypeScript
# SDK the app drives. Anything else — sdk-cli (`claude -p`), sdk-py — is a program: observer
# summarizers, evals, headless probes, 6,068 of 6,685 transcripts where this was measured.
# A transcript with no entrypoint predates the field and counts as a person's. A temp-dir
# cwd is not a signal: people run sessions there too.
HUMAN_ENTRYPOINTS = frozenset({"cli", "claude-desktop", "sdk-ts"})
# Whether automated sessions are listed when the caller does not say. The one line to flip.
INCLUDE_AUTOMATED = False


def parse_entrypoints(value: str | None) -> tuple[bool, frozenset[str] | None]:
    """``--entrypoint`` as (list programs' sessions too, only these entrypoints). ``all``: every
    session; ``sdk-cli,sdk-py``: only theirs; nothing: the default (HUMAN_ENTRYPOINTS)."""
    if not value:
        return INCLUDE_AUTOMATED, None
    if value.strip() == "all":
        return True, None
    return True, frozenset(n.strip() for n in value.split(",") if n.strip())

# An unescaped quote before the key: the field itself, never a transcript quoted in content.
_ENTRYPOINT_RE = re.compile(r'"entrypoint"\s*:\s*"([^"]*)"')
_ENTRYPOINT_RE_B = re.compile(rb'"entrypoint"\s*:\s*"([^"]*)"')

# Fields every record carries that are about the record, not what it says.
_ENVELOPE = {
    "parentUuid", "isSidechain", "userType", "cwd", "sessionId", "session_id", "version",
    "gitBranch", "uuid", "timestamp", "entrypoint", "slug", "requestId", "promptId",
    "leafUuid", "messageId",
}
_META_USER = {
    "caveat": "local-command", "local-output": "local-command",
    "compact": "compact-summary", "meta": "injected",
}
# Which hit a session's snippet shows first: what people said beats what tools printed.
_ROLE_RANK = {"user": 0, "assistant": 1, "thinking": 2, "notification": 3, "teammate": 3, "tool-output": 5}
# A prompt-shaped record by who sent it (turns.sender): only "user" is someone typing, so
# `--role user` (and the older `--tool user`) finds what the user said and nothing an agent sent them.
_SENDER_ROLE = {"user": "user", "task": "notification", "teammate": "teammate"}


def calling_session() -> str:
    """The session this process runs for — Claude Code sets it for tools and MCP servers."""
    return (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()


@dataclass
class Hit:
    line: int
    ts: str
    role: str  # user · assistant · thinking · <tool name> · tool-output · notification · teammate · a meta kind
    meta: bool
    snippet: str
    tool_ids: tuple[str, ...] = ()
    is_error: bool = False

    @property
    def label(self) -> str:
        return short_tool(self.role)

    @property
    def rank(self) -> int:
        return 6 if self.meta else _ROLE_RANK.get(self.role, 4)


@dataclass
class SessionHits:
    session_id: str
    path: str
    hits: list[Hit] = field(default_factory=list)  # the counted ones
    meta_hits: int = 0

    @property
    def latest(self) -> str:
        return max((h.ts for h in self.hits), default="")


@dataclass
class SearchReport:
    query: str
    scope: str
    ranked: list[SessionHits]
    meta_hits: int = 0
    meta_only_sessions: int = 0
    excluded: list[str] = field(default_factory=list)  # left out by exclude_session, and matched
    automated: dict[str, int] = field(default_factory=dict)  # entrypoint -> sessions hidden that matched
    entrypoints: frozenset[str] | None = None  # --entrypoint named these: the rest were hidden
    filters: str = ""
    turns: dict[str, dict[int, tuple[int, str]]] = field(default_factory=dict)  # sid -> line -> (turn, prompt)
    info: dict[str, "SessionInfo"] = field(default_factory=dict)  # sid -> title, size, first prompt


@dataclass
class SessionInfo:
    """What a session was, for judging a hit without opening it: its title, how many turns,
    how long it spent inside them, what it began with."""
    title: str = ""
    turns: int = 0
    active: float = 0.0  # seconds inside turns: prompt to the turn's last record
    first_prompt: str = ""


def _pieces(msg: dict) -> list[tuple[str, bool, str]]:
    """(role, is_meta, text) for each piece of text a record carries."""
    mtype = msg.get("type")
    if mtype == "assistant":
        content = (msg.get("message") or {}).get("content", [])
        out = []
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                out.append(("assistant", False, b.get("text", "")))
            elif t == "thinking":
                out.append(("thinking", False, b.get("thinking", "")))
            elif t == "tool_use":
                out.append((b.get("name") or "tool", False, json.dumps(b.get("input", {}), ensure_ascii=False)))
        return out
    if mtype == "user":
        kind = user_kind(msg)
        if kind == "tool-result":
            content = (msg.get("message") or {}).get("content", [])
            text = "\n".join(
                result_text(b.get("content")) for b in content
                if isinstance(b, dict) and b.get("type") == "tool_result"
            )
            return [("tool-output", False, text)]
        text = user_text(msg)
        if kind in ("prompt", "command", "interrupt"):
            return [(_SENDER_ROLE[sender(text)], False, text)]
        return [(_META_USER.get(kind, "user-meta"), True, text)]
    prompt = _queued_prompt(msg)
    if prompt is not None:
        # typed while the model was busy: a prompt, delivered late
        return [(_SENDER_ROLE[sender(prompt)], False, prompt)]
    return [(_meta_label(msg), True, json.dumps(_content_of(msg), ensure_ascii=False))]


def _queued_prompt(msg: dict) -> str | None:
    a = msg.get("attachment") if msg.get("type") == "attachment" else None
    if isinstance(a, dict) and a.get("type") == "queued_command" and isinstance(a.get("prompt"), str):
        return a["prompt"]
    return None


def _meta_label(msg: dict) -> str:
    mtype = msg.get("type")
    if mtype == "attachment":
        return f"attachment:{(msg.get('attachment') or {}).get('type', '?')}"
    if mtype == "system" and msg.get("subtype"):
        return f"system:{msg['subtype']}"
    return str(mtype or "?")


def _content_of(msg: dict):
    if msg.get("type") == "attachment":
        return msg.get("attachment") or {}
    return {k: v for k, v in msg.items() if k not in _ENVELOPE}


def _snippet(text: str, qlow: str, width: int = 70) -> str:
    low = text.lower()
    pos = low.find(qlow)
    if pos < 0:
        return one_line(text, 2 * width)
    start, end = max(0, pos - width), min(len(text), pos + len(qlow) + width)
    body = " ".join(text[start:end].split())
    return ("…" if start > 0 else "") + body + ("…" if end < len(text) else "")


def classify(msg: dict, line: int, qlow: str, meta_snippet: bool = True) -> Hit | None:
    """The hit this record makes, or None when the term is only in its envelope."""
    if msg.get("type") not in ("user", "assistant") and _queued_prompt(msg) is None:
        # A metadata record. The line matched, so unless the term is in an envelope
        # field it is in the content — no need to re-serialize a 100 KB skill listing
        # to find out, nor to cut a snippet nobody will print.
        in_envelope = any(qlow in str(msg[k]).lower() for k in _ENVELOPE if k in msg)
        text = ""
        if in_envelope or meta_snippet:
            text = json.dumps(_content_of(msg), ensure_ascii=False)
            if qlow not in text.lower():
                return None
        return Hit(line=line, ts=msg.get("timestamp", "") or "", role=_meta_label(msg), meta=True,
                   snippet=_snippet(text, qlow) if meta_snippet else "")
    for role, meta, text in _pieces(msg):
        if text and qlow in text.lower():
            hit = Hit(line=line, ts=msg.get("timestamp", "") or "", role=role, meta=meta,
                      snippet=_snippet(strip_tags(text) if role in ("user", "notification") else text, qlow))
            if role == "tool-output":
                blocks = [b for b in (msg.get("message") or {}).get("content", [])
                          if isinstance(b, dict) and b.get("type") == "tool_result"]
                hit.tool_ids = tuple(b.get("tool_use_id", "") for b in blocks)
                hit.is_error = any(
                    b.get("is_error") is True or _ERROR_LINE_RE.search(result_text(b.get("content")))
                    for b in blocks
                )
            return hit
    return None


def _rg_matches(dirs: list[Path], query: str) -> Iterator[tuple[str, int, str]] | None:
    """(path, line, text) per matching line, streamed from one ripgrep pass; None when
    rg is unavailable. `-F` keeps the query literal; `--max-depth 1` keeps the scan to
    session transcripts (subagent ones live one level down). Plain output with a NUL
    after the path, not --json: a matched record can be 100 KB (a skill listing, a
    system-prompt snapshot), and --json re-escaped every one of them into a second
    document to parse — 650 MB for "sandbox" across all projects."""
    rg = shutil.which("rg")
    if not rg or not dirs:
        return None
    cmd = [rg, "-i", "-F", "-n", "-0", "--with-filename", "--no-heading", "--color", "never",
           "--max-depth", "1", "--glob", "*.jsonl", "--", query, *map(str, dirs)]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None

    def gen():
        try:
            for raw in proc.stdout:
                path, sep, rest = raw.partition(b"\0")
                num, colon, text = rest.partition(b":")
                if sep and colon and num.isdigit():
                    yield path.decode("utf-8", "replace"), int(num), text.decode("utf-8", "replace")
        finally:
            proc.stdout.close()
            proc.wait()

    return gen()


def _py_matches(dirs: list[Path], qlow: str) -> Iterator[tuple[str, int, str]]:
    for d in dirs:
        for f in sorted(d.glob("*.jsonl")):
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for n, line in enumerate(fh, 1):
                        if qlow in line.lower():
                            yield str(f), n, line
            except OSError:
                continue


def _tool_names(path: str) -> dict[str, str]:
    """tool_use id -> tool name, for one transcript (the `tool` filter on tool output)."""
    names: dict[str, str] = {}
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if b'"tool_use"' not in raw:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                for b in (msg.get("message") or {}).get("content", []) or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        names[b.get("id", "")] = b.get("name", "")
    except OSError:
        pass
    return names


def _tool_matches(name: str, want: str) -> bool:
    return name == want or short_tool(name) == want or name.endswith("__" + want)


def session_entrypoint(path: str, line_text: str = "") -> str:
    """Who started the session: the `entrypoint` of the matched record when it has one
    (titles and mode records do not), else the first one in the transcript's first MB."""
    m = _ENTRYPOINT_RE.search(line_text) if line_text else None
    if m:
        return m.group(1)
    try:
        with open(path, "rb") as fh:
            for _ in range(16):
                chunk = fh.read(1 << 16)
                if not chunk:
                    break
                mb = _ENTRYPOINT_RE_B.search(chunk)
                if mb:
                    return mb.group(1).decode("utf-8", "replace")
    except OSError:
        pass
    return ""


def is_automated(entrypoint: str) -> bool:
    return bool(entrypoint) and entrypoint not in HUMAN_ENTRYPOINTS


def leave_out(session_id: str, entrypoint: str, exclude: set[str], include_automated: bool,
              entrypoints: frozenset[str] | None = None) -> str:
    """The one gate a session passes to be listed: "" to list it, else why not —
    "excluded" (the asking session, or the one exclude_session names) or "automated"
    (a program started it and the caller did not ask for those, or ``entrypoints`` names
    others). Nowhere else filters sessions."""
    if session_id in exclude:
        return "excluded"
    if entrypoints is not None:
        return "" if entrypoint in entrypoints else "automated"
    if not include_automated and is_automated(entrypoint):
        return "automated"
    return ""


def search_sessions(
    query: str,
    dirs: list[Path],
    *,
    scope: str = "",
    include_meta: bool = False,
    since: str = "",
    until: str = "",
    tool: str = "",
    role: str = "",
    outcome: str = "",
    errors: bool = False,
    exclude_session: str | None = None,
    include_automated: bool = INCLUDE_AUTOMATED,
    entrypoints: frozenset[str] | None = None,
    turn_limit: int = DEFAULT_LIMIT,
) -> SearchReport:
    """Scan ``dirs`` for ``query``; every matching session, ranked.

    ``tool``: one tool's calls and results; ``role``: one role's words, as the hit counts
    print them (user · assistant · thinking · tool-output · notification · teammate);
    ``outcome``: tool results that ended in ``error`` or ``ok`` (``errors`` is the older
    spelling of ``outcome="error"``). ``exclude_session``: None leaves out the calling
    session (calling_session()), "" leaves out none, a uuid leaves out that one.
    ``include_automated``: list sessions a program started, too; ``entrypoints``: list only
    sessions with these entrypoints. ``turn_limit``: how many of the top sessions get their
    hits mapped to turns (a transcript read each).
    """
    outcome = outcome or ("error" if errors else "")
    qlow = query.lower()
    exclude = {calling_session() if exclude_session is None else exclude_session} - {""}
    existing = [d for d in dirs if d.is_dir()]
    matches = _rg_matches(existing, query)
    if matches is None:
        matches = _py_matches(existing, qlow)

    groups: dict[str, SessionHits] = {}
    verdicts: dict[str, str] = {}
    excluded: list[str] = []
    automated: Counter = Counter()
    meta_hits = 0
    names_cache: dict[str, dict[str, str]] = {}
    for path, line, text in matches:
        sid = Path(path).stem
        verdict = verdicts.get(path)
        if verdict is None:
            # decided once per session, before any of its lines is parsed — most matched
            # transcripts on a machine are automated, and skipping them is most of the speed
            ep = session_entrypoint(path, text)
            verdict = verdicts[path] = leave_out(sid, ep, exclude, include_automated, entrypoints)
            if verdict == "excluded":
                excluded.append(sid)
            elif verdict == "automated":
                automated[ep] += 1
        if verdict:
            continue
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        hit = classify(msg, line, qlow, meta_snippet=include_meta)
        if hit is None:
            continue
        if since and not (hit.ts and hit.ts >= since):
            continue
        # compared on the bound's own length, so a bare date keeps that whole day
        # ("2026-09-22" keeps 23:59), and a datetime cuts where it says
        if until and not (hit.ts and hit.ts[: len(until)] <= until):
            continue
        if outcome == "error" and not hit.is_error:
            continue
        if outcome == "ok" and (hit.role != "tool-output" or hit.is_error):
            continue
        if role and hit.role != role:
            continue
        if tool:
            if hit.role == "tool-output":
                names = names_cache.setdefault(path, _tool_names(path))
                if not any(_tool_matches(names.get(i, ""), tool) for i in hit.tool_ids):
                    continue
            elif not _tool_matches(hit.role, tool):
                continue
        g = groups.setdefault(sid, SessionHits(sid, path))
        if hit.meta and not include_meta:
            g.meta_hits += 1
            meta_hits += 1
            continue
        g.hits.append(hit)

    ranked = sorted((g for g in groups.values() if g.hits),
                    key=lambda g: (len(g.hits), g.latest), reverse=True)
    report = SearchReport(
        query=query,
        scope=scope,
        ranked=ranked,
        meta_hits=meta_hits,
        meta_only_sessions=sum(1 for g in groups.values() if not g.hits),
        excluded=excluded,
        automated=dict(automated),
        entrypoints=entrypoints,
        # each filter by its flag's own word, so the header reads back as the command
        filters=", ".join(
            f for f in (f"tool={tool}" if tool else "", f"role={role}" if role else "",
                        f"outcome={outcome}" if outcome else "",
                        f"since {since}" if since else "", f"until {until}" if until else "",
                        "metadata counted" if include_meta else "",
                        f"entrypoint={','.join(sorted(entrypoints))}" if entrypoints is not None
                        else "entrypoint=all" if include_automated else "")
            if f
        ),
    )
    for g in ranked[:turn_limit]:
        report.turns[g.session_id], report.info[g.session_id] = scan_session(g.path, {h.line for h in g.hits})
    return report


_TS_KEY = b'"timestamp":"'
_USER_B = re.compile(rb'"type":\s*"user"')
_ASSISTANT_B = re.compile(rb'"type":\s*"assistant"')
_TS_B = re.compile(rb'"timestamp":\s*"([^"]*)"')


def _ts_of(raw: bytes) -> str:
    """A record's own timestamp — the last one on the line, where the envelope keeps it."""
    i = raw.rfind(_TS_KEY)
    if i >= 0:
        j = i + len(_TS_KEY)
        return raw[j: raw.find(b'"', j)].decode("ascii", "replace")
    found = _TS_B.findall(raw)  # a writer that spaces its JSON
    return found[-1].decode("ascii", "replace") if found else ""


def _span(a: str, b: str) -> float:
    from datetime import datetime

    try:
        ta = datetime.fromisoformat(a.replace("Z", "+00:00"))
        tb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max((tb - ta).total_seconds(), 0.0)


def scan_session(path: str, lines: set[int]) -> tuple[dict[int, tuple[int, str]], SessionInfo]:
    """One pass over a transcript: line -> (turn, that turn's prompt) for the hit lines,
    and the session's title, turn count, time inside turns and first prompt. Only the
    records that can open a turn or name the session are parsed; the rest are byte checks."""
    out: dict[int, tuple[int, str]] = {}
    info = SessionInfo()
    splitter = TurnSplitter()
    seq, prompt = -1, ""
    turn_start = last_ts = ""
    ai_title = custom_title = ""
    try:
        with open(path, "rb") as fh:
            for n, raw in enumerate(fh, 1):
                msg = None
                is_assistant = False
                if (n in lines) or b'-title"' in raw or _USER_B.search(raw):
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        msg = None
                elif _ASSISTANT_B.search(raw):
                    is_assistant = True
                    splitter.kind({"type": "assistant"})  # all the splitter needs from one
                work = is_assistant  # what ends a turn: the model or a tool, not a later system note
                if isinstance(msg, dict):
                    kind = msg.get("type")
                    if kind == "ai-title" and msg.get("aiTitle"):
                        ai_title = str(msg["aiTitle"])
                    elif kind == "custom-title" and msg.get("customTitle"):
                        custom_title = str(msg["customTitle"])
                    else:
                        k = splitter.kind(msg)
                        if k == "prompt":
                            if seq >= 0:
                                info.active += _span(turn_start, last_ts)
                            seq += 1
                            prompt = prompt_text(user_text(msg))
                            turn_start = last_ts = msg.get("timestamp", "") or ""
                            if seq == 0:
                                info.first_prompt = prompt
                            if n in lines:
                                out[n] = (seq, prompt)
                            continue
                        work = k in ("assistant", "tool-result", "interrupt")
                if work and seq >= 0:
                    last_ts = _ts_of(raw) or last_ts
                if n in lines:
                    out[n] = (seq, prompt)
    except OSError:
        pass
    if seq >= 0:
        info.active += _span(turn_start, last_ts)
    info.turns = seq + 1
    info.title = (custom_title or ai_title).strip()
    return out, info


def turns_at(path: str, lines: set[int]) -> dict[int, tuple[int, str]]:
    """line -> (turn number, that turn's prompt), for the given lines of one transcript."""
    return scan_session(path, lines)[0]


def _dur(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s" if m else f"{s}s"


def format_search(report: SearchReport, limit: int = DEFAULT_LIMIT) -> str:
    """Ranked sessions, three or four lines each: the session (its title, turns, time inside
    them) and how much it said, what it began with, the turn that said most, a snippet —
    enough to judge what a session was about without opening it."""
    from .query import hint

    q = report.query
    notes = []
    if report.meta_hits:
        notes.append(
            f"not counted: {report.meta_hits} hit(s) in hook/metadata records"
            + (f" ({report.meta_only_sessions} session(s) had only those)" if report.meta_only_sessions else "")
            + f" — {hint('meta')} counts them"
        )
    if report.automated:
        # the entrypoints by name: they are the values --entrypoint takes
        kinds = ", ".join(f"{ep or 'none'} {n}" for ep, n in sorted(report.automated.items(), key=lambda kv: -kv[1]))
        who = "with another entrypoint" if report.entrypoints is not None else "a program started"
        notes.append(
            f"hidden: {sum(report.automated.values())} session(s) {who} that matched ({kinds})"
            f" — {hint('automated')} lists them"
        )
    for sid in report.excluded:
        who = "this session" if sid == calling_session() else "session"
        notes.append(f"left out: {who} {sid[:8]} — {hint('keep')} keeps it")
    where = f" in {report.scope}" if report.scope else ""
    filt = f" ({report.filters})" if report.filters else ""

    if not report.ranked:
        return "\n".join([f'No conversations matched "{q}"{where}{filt}.', *notes])

    total_hits = sum(len(g.hits) for g in report.ranked)
    lines = [
        f'"{q}"{where}{filt}: {len(report.ranked)} session(s), {total_hits} hit(s) in conversation text'
        " · ranked by hits, then the latest",
        *notes,
        "",
    ]
    shown = report.ranked[:limit]
    first_id = ""
    for i, g in enumerate(shown, 1):
        roles = Counter(h.label for h in g.hits)
        roles_s = ", ".join(f"{r} {n}" for r, n in roles.most_common())
        latest = g.latest
        info = report.info.get(g.session_id)
        head = f"{i}. {g.session_id}"
        if info and info.title:
            head += f' · "{one_line(info.title, 60)}"'
        head += f" · {latest[:10] or '?'}"
        if info and info.turns:
            head += f" · {info.turns} turn{'s' if info.turns != 1 else ''} · {_dur(info.active)}"
        lines.append(f"{head} · {len(g.hits)} hit(s): {roles_s}")

        tmap = report.turns.get(g.session_id, {})
        per_turn = Counter(tmap.get(h.line, (-1, ""))[0] for h in g.hits)
        best, best_n = per_turn.most_common(1)[0]
        in_best = [h for h in g.hits if tmap.get(h.line, (-1, ""))[0] == best]
        pick = min(in_best, key=lambda h: (h.rank, h.line))
        if best >= 0:
            iid = f"{g.session_id}-{best:03d}"
            first_id = first_id or iid
            others = sorted(t for t in per_turn if t >= 0 and t != best)
            also = ""
            if others:
                also = " (also turn" + ("s " if len(others) > 1 else " ") + ", ".join(f"{t:03d}" for t in others[:4])
                also += f" +{len(others) - 4})" if len(others) > 4 else ")"
            raw_prompt = tmap.get(pick.line, (0, ""))[1]
            first = info.first_prompt if info else ""
            if first and best != 0 and " ".join(first.split()) != " ".join(raw_prompt.split()):
                lines.append(f"   began: {one_line(first, 140)}")
            prompt = one_line(raw_prompt, 140)
            lines.append(f"   turn {best:03d} · {best_n} hit(s){also} · asked: {prompt or '(empty)'}")
        lines.append(f"   {pick.label}: {one_line(pick.snippet, 170)}")
    lines.append("")
    tail = []
    if first_id:
        tail.append(f"a turn's steps: {hint('drill', id=first_id)}")
    tail.append(f"what the user asked there: {hint('user', sid=shown[0].session_id)}")
    hidden = len(report.ranked) - len(shown)
    if hidden > 0:
        tail.append(f"{hidden} more session(s): {hint('limit', n=len(report.ranked))}")
    lines.append(" · ".join(tail))
    return "\n".join(lines).rstrip()
