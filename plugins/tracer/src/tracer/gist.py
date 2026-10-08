"""What came back, in one line: the gist of a tool result, and the shape of a JSON one.

The drill printed "→ 913 B ok" per step and nothing of what the bytes said, so an agent
inspecting a session opened five of six steps just to see each result, then ran Bash+jq
on a saved file to learn whether get_observations' rows carried a session id — a question
the result's keys answer (e740f43a). A gist is the first meaningful line of a result, or
for JSON its shape: `{count: 30, results: [30 × {id,date,obs_type,title,project}]}`.

Everything here is mechanical: no model, no guess about what a result means.
"""

from __future__ import annotations

import json
import re
from collections import Counter

GIST_CAP = 120
_TAG_LINE_RE = re.compile(r"^\s*</?[a-z_-]+>\s*$")
_READ_LINE_RE = re.compile(r"^\s*\d+[\t→]")
_WORD_RE = re.compile(r"[a-z0-9_]{2,}")


def unwrap_mcp(text: str) -> tuple[str, bool]:
    """An MCP result is stored as {"result": "<text>"} — JSON inside a JSON string, every
    quote escaped. The text it wraps reads the same at roughly half the tokens."""
    if not text.startswith('{"result":'):
        return text, False
    try:
        doc = json.loads(text)
    except ValueError:
        return text, False
    if isinstance(doc, dict) and set(doc) == {"result"} and isinstance(doc["result"], str):
        return doc["result"], True
    return text, False


def as_json(text: str):
    """The parsed document when ``text`` is one JSON object or array, else None."""
    s = text.strip()
    if not s or s[0] not in "{[":
        return None
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    return obj if isinstance(obj, (dict, list)) else None


def _keyset(rows: list[dict], cap: int = 10) -> str:
    """Keys of a list of objects, first-seen order; `key?` when only some rows have it."""
    seen: dict[str, int] = {}
    for row in rows:
        for k in row:
            seen[k] = seen.get(k, 0) + 1
    keys = [k if n == len(rows) else f"{k}?" for k, n in seen.items()]
    more = f",+{len(keys) - cap}" if len(keys) > cap else ""
    return "{" + ",".join(keys[:cap]) + more + "}"


def _scalar(v) -> str:
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False) if len(v) <= 24 else f"str({len(v):,})"
    if isinstance(v, dict):
        return "{" + ",".join(list(v)[:8]) + (",…" if len(v) > 8 else "") + "}"
    if isinstance(v, list):
        return f"[{len(v)}]"
    return json.dumps(v)


def shape(obj, cap_keys: int = 8) -> str:
    """Keys and counts, two levels deep: `{count: 30, results: [30 × {id,date,title}]}`."""
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if all(isinstance(x, dict) for x in obj):
            return f"[{len(obj)} × {_keyset(obj)}]"
        kinds = Counter(type(x).__name__ for x in obj)
        return f"[{len(obj)} × {'|'.join(kinds)}]"
    if isinstance(obj, dict):
        parts = []
        for k, v in list(obj.items())[:cap_keys]:
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                parts.append(f"{k}: [{len(v)} × {_keyset(v)}]")
            elif isinstance(v, list):
                parts.append(f"{k}: " + (f"[{len(v)} × {'|'.join(Counter(type(x).__name__ for x in v))}]" if v else "[]"))
            else:
                parts.append(f"{k}: {_scalar(v)}")
        more = f", +{len(obj) - cap_keys} keys" if len(obj) > cap_keys else ""
        return "{" + ", ".join(parts) + more + "}"
    return _scalar(obj)


def json_view(obj, rows: int = 3, row_cap: int = 240) -> list[str]:
    """The shape, then the first rows of the largest list in it — what an agent reached
    for jq to see."""
    lines = [f"shape: {shape(obj)}"]
    name, items = "", None
    if isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict):
        lists = [(k, v) for k, v in obj.items() if isinstance(v, list) and v]
        if lists:
            name, items = max(lists, key=lambda kv: len(kv[1]))
    for i, item in enumerate((items or [])[:rows]):
        row = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        row = row if len(row) <= row_cap else row[: row_cap - 1] + "…"
        lines.append(f"{name}[{i}]: {row}")
    if items and len(items) > rows:
        lines.append(f"… {len(items) - rows} more in {name or 'the list'}")
    return lines


def first_line(text: str) -> str:
    """The first line with a letter or digit in it, not a bare tag."""
    for line in text.splitlines():
        if _TAG_LINE_RE.match(line):
            continue
        s = line.strip()
        if any(c.isalnum() for c in s):
            return s
    return ""


def gist(text: str, tool: str = "", persisted_lines: int | None = None, cap: int = GIST_CAP) -> str:
    """One line of what a result said. ``text`` is the result as the transcript holds it;
    ``persisted_lines`` is the saved file's line count when the transcript kept only a
    preview of it."""
    if not text:
        return "(empty)"
    if text.lstrip().startswith("<persisted-output>"):
        preview = text.split("Preview (first 2KB):", 1)[-1]
        head = first_line(preview.replace("</persisted-output>", ""))
        tail = f" ({persisted_lines:,} lines)" if persisted_lines else ""
        return _cut(head, cap - len(tail)) + tail
    text, _ = unwrap_mcp(text)
    obj = as_json(text)
    if obj is not None:
        return _cut(shape(obj), cap)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if tool == "Read" and lines and _READ_LINE_RE.match(lines[0]):
        head = _READ_LINE_RE.sub("", next((ln for ln in lines if _READ_LINE_RE.sub("", ln).strip()), lines[0])).strip()
        return _cut(f"{len(lines):,} lines: {head}", cap)
    head = first_line(text) or "(no text)"
    more = f" (+{len(lines) - 1:,} lines)" if len(lines) > 1 else ""
    return _cut(head, cap - len(more)) + more


def _cut(s: str, cap: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= cap else s[: max(cap - 1, 0)] + "…"


def similarity(a: str, b: str, min_words: int = 20) -> float:
    """Share of distinct words two results have in common (0–1). Below ``min_words``
    either way it is 0: two "file updated" notes are alike and mean nothing by it."""
    wa, wb = set(_WORD_RE.findall(a.lower())), set(_WORD_RE.findall(b.lower()))
    if len(wa) < min_words or len(wb) < min_words:
        return 0.0
    return len(wa & wb) / len(wa | wb)
