"""The wire: the ak gateway's record of each model call, joined to a transcript's messages.

The gateway (`~/agentic-kit/gateway`, not a plugin) writes one row per call to
`<data>/trace/<UTC day>.ndjson`, bodies by hash under `<data>/blobs/`; the format is its
`docs/trace.md`. Read here as data — no gateway code is imported. A session that never ran
through the gateway has no rows, and everything here comes back empty.

    join by id      a row's `messageId` is the `message.id` of the transcript entry it produced
    join by counts  rows from before `messageId`: exact input + cache counts, in time order —
                    a `v` 1 streamed row is halved first (that build summed two stream events)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ._brand import GATEWAY_DATA, env
from ._brand import data_dir as _data_home

# A v1 streamed row's output carries message_start's placeholder on top of the real count.
OUTPUT_SLACK = 64


def data_dir() -> Path:
    return Path(env("GATEWAY_DATA_DIR") or _data_home() / GATEWAY_DATA)


@dataclass
class Call:
    row_id: str
    ts: str
    request_class: str  # main · subagent · compaction · auxiliary · "" (rows before hints)
    agent_type: str
    account: str
    account_source: str
    ttfb_ms: int | None
    dur_ms: int | None
    status: int | None
    error: str
    message_id: str
    usage: dict  # input · output · cacheRead · cacheCreation, as the API reported them
    system: str  # blob hashes
    tools: str
    body: str
    n_tools: int
    bytes_in: int
    by: str = ""  # how it was joined: "id" · "counts"

    def blob(self, h: str) -> Path | None:
        return data_dir() / "blobs" / h[:2] / f"{h}.json" if h else None


def _usage(row: dict) -> dict:
    u = dict(row.get("usage") or {})
    for k in ("input", "output", "cacheRead", "cacheCreation"):
        u[k] = int(u.get(k) or 0)
    if row.get("v", 1) == 1 and row.get("stream") and row.get("stopReason"):
        for k in ("input", "cacheRead", "cacheCreation"):
            u[k] //= 2
    return u


def _call(row: dict) -> Call:
    hints = row.get("hints") or {}
    blobs = row.get("blobs") or {}
    shape = row.get("shape") or {}
    return Call(
        row_id=str(row.get("id") or ""), ts=str(row.get("ts") or ""),
        request_class=str(hints.get("requestClass") or ""), agent_type=str(hints.get("agentType") or ""),
        account=str(row.get("account") or ""), account_source=str(row.get("accountSource") or ""),
        ttfb_ms=row.get("ttfbMs"), dur_ms=row.get("durMs"), status=row.get("status"),
        error=str(row.get("error") or ""), message_id=str(row.get("messageId") or ""), usage=_usage(row),
        system=str(blobs.get("system") or ""), tools=str(blobs.get("tools") or ""), body=str(blobs.get("body") or ""),
        n_tools=int(shape.get("tools") or 0), bytes_in=int(shape.get("bytesIn") or 0),
    )


def _days(first_ts: str, last_ts: str) -> list[str]:
    try:
        a, b = date.fromisoformat(first_ts[:10]), date.fromisoformat((last_ts or first_ts)[:10])
    except ValueError:
        return []
    return [(a + timedelta(days=i)).isoformat() for i in range(max((b - a).days, 0) + 1)]


def _model_call(path: str) -> bool:
    """/v1/messages, also behind an account pin (/tc-acct/X/…, /tc-prefer/X/…) and with its query."""
    return path.split("?", 1)[0].endswith("/v1/messages")  # portable: ok — a URL path, not a file path


def load(session_id: str, first_ts: str, last_ts: str) -> list[Call]:
    """Every call the gateway recorded for this session, in time order."""
    trace = data_dir() / "trace"
    calls: list[Call] = []
    for day in _days(first_ts, last_ts):
        path = trace / f"{day}.ndjson"
        if not path.is_file():
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if session_id not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (row.get("identity") or {}).get("session") == session_id and _model_call(row.get("path", "")):
                    calls.append(_call(row))
    return sorted(calls, key=lambda c: c.ts)


def _key(u: dict) -> tuple[int, int, int]:
    return int(u.get("input_tokens") or 0), int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0)


def join(calls: list[Call], messages: list[tuple[str, dict]]) -> dict[str, Call]:
    """Transcript message id → the call that produced it. ``messages``: (id, usage) in order."""
    by_id = {c.message_id: c for c in calls if c.message_id}
    out: dict[str, Call] = {}
    for mid, _ in messages:
        if mid in by_id:
            out[mid] = by_id[mid]
            out[mid].by = "id"
    # Rows from before messageId: the next unused call, in time order, with the same counts.
    rest = [c for c in calls if not c.message_id and c.usage]
    i = 0
    for mid, u in messages:
        if mid in out or not u:
            continue
        want, out_tokens = _key(u), int(u.get("output_tokens") or 0)
        for j in range(i, len(rest)):
            c = rest[j]
            if (c.usage["input"], c.usage["cacheRead"], c.usage["cacheCreation"]) == want \
                    and 0 <= c.usage["output"] - out_tokens <= OUTPUT_SLACK:
                c.by = "counts"
                out[mid] = c
                i = j + 1
                break
    return out


def changes(calls: list[Call]) -> dict[str, list[str]]:
    """Row id → what changed on the main thread since its previous call: ``sysΔ`` the system
    prompt, ``toolsΔ`` the tool list, ``acct`` the account. Any of them costs a cache miss."""
    out: dict[str, list[str]] = {}
    prev: Call | None = None
    for c in calls:
        if c.request_class not in ("main", ""):
            continue
        if prev is not None:
            f = [flag for flag, a, b in (("sysΔ", c.system, prev.system), ("toolsΔ", c.tools, prev.tools),
                                         ("acct", c.account, prev.account)) if a and b and a != b]
            if f:
                out[c.row_id] = f
        prev = c
    return out
