"""The user's Claude Code settings.json: set keys, keep everything else, show the diff first.

User scope only. A key is a path (("env", "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS")); setting it to the
value it already has is no change; a non-object on the way down is refused, never overwritten.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field

from ak._brand import claude_home
from ak.move.ops import atomic_write

ABSENT = object()  # a key that was not there
ABSENT_MARK = {"$absent": True}  # ABSENT as it sits in a run.json


@dataclass(frozen=True)
class SettingsEdit:
    keypath: tuple[str, ...]
    value: object


@dataclass
class SettingsPlan:
    path: str
    before_text: str              # "" when the file does not exist
    after_text: str
    changes: list[dict] = field(default_factory=list)  # {"keypath": [...], "before": value|ABSENT, "after": value, "created": n}
    diff: str = ""                # unified diff, before -> after
    skipped: list[dict] = field(default_factory=list)  # apply() left these: {"keypath", "found"} — a key set since plan()

    @property
    def empty(self) -> bool:
        return not self.changes


def settings_path() -> str:
    """<claude home>/settings.json."""
    return os.path.join(str(claude_home()), "settings.json")


def dumps(data, **kw) -> str:
    """json.dumps that writes ABSENT as a marker, so a change list round-trips through a run.json."""
    return json.dumps(data, default=lambda o: ABSENT_MARK if o is ABSENT else _refuse(o), **kw)


def loads(text: str):
    """json.loads that reads the marker back as ABSENT."""
    return json.loads(text, object_hook=lambda d: ABSENT if d == ABSENT_MARK else d)


def _refuse(o):
    raise TypeError(f"{type(o).__name__} is not JSON serialisable")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def parse_object(raw: str, path: str) -> dict:
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _indent(raw: str):
    """The file's own indent: its first indented line's whitespace; 2 spaces for a new or empty one;
    none for a one-line object."""
    for line in raw.splitlines():
        m = re.match(r"[ \t]+(?=\S)", line)
        if m:
            return m.group(0)
    s = raw.strip()
    return None if s and s != "{}" and "\n" not in s else 2


def render(data: dict, raw: str) -> str:
    return json.dumps(data, indent=_indent(raw), ensure_ascii=False) + ("\n" if raw.endswith("\n") or not raw else "")


def same_json(a, b) -> bool:
    """Equal as JSON: 1 is not True, key order does not matter."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def walk_down(data: dict, keypath, create: bool):
    """The object holding the last key, and how many objects were made to get there; None when
    create is off and the way is not there. ValueError (naming the keypath) when a non-object is in the way."""
    if not keypath:
        raise ValueError("an empty keypath")
    node, made = data, 0
    for i, key in enumerate(keypath[:-1]):
        nxt = node.get(key, ABSENT)
        if nxt is ABSENT:
            if not create:
                return None, 0
            nxt = node[key] = {}
            made += 1
        elif not isinstance(nxt, dict):
            raise ValueError(f"{'.'.join(keypath)}: {'.'.join(keypath[:i + 1])} is not an object")
        node = nxt
    return node, made


def _copy(v):
    return v if v is ABSENT else copy.deepcopy(v)  # deepcopy would make a second ABSENT


def _diff(before: str, after: str, path: str) -> str:
    def lines(s):
        out = s.splitlines(keepends=True)
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n\\ No newline at end of file\n"
        return out
    return "".join(difflib.unified_diff(lines(before), lines(after), fromfile=path, tofile=path))


def plan(edits: list[SettingsEdit], path: str | None = None) -> SettingsPlan:
    """What the edits would do to the file. Writes nothing. ValueError on a non-object on the way down."""
    path = path or settings_path()
    raw = _read(path)
    data = parse_object(raw, path)
    changes = []
    for e in edits:
        keypath = tuple(e.keypath)
        node, made = walk_down(data, keypath, create=True)
        cur = node.get(keypath[-1], ABSENT)
        if cur is not ABSENT and same_json(cur, e.value):
            continue
        changes.append({"keypath": list(keypath), "before": _copy(cur), "after": copy.deepcopy(e.value), "created": made})
        node[keypath[-1]] = copy.deepcopy(e.value)
    after = render(data, raw) if changes else raw
    return SettingsPlan(path, raw, after, changes, _diff(raw, after, path))


def apply(p: SettingsPlan) -> str:
    """Re-read the file, re-merge p.changes by key (another writer may have been there since plan()),
    write atomically, return the sha256 of what was written. Each change's "before" becomes what the
    file holds now, so an undo puts back what was really there. A key that was absent at plan() but
    holds a value now is left alone: it moves from p.changes to p.skipped, for the engine to tell the person."""
    raw = _read(p.path)
    data = parse_object(raw, p.path)
    kept, p.skipped = [], []
    for c in p.changes:
        node, _ = walk_down(data, c["keypath"], create=False)
        found = ABSENT if node is None else node.get(c["keypath"][-1], ABSENT)
        if c["before"] is ABSENT and found is not ABSENT:  # set by someone since the preview: not ours to overwrite
            p.skipped.append({"keypath": c["keypath"], "found": _copy(found)})
            continue
        node, c["created"] = walk_down(data, c["keypath"], create=True)
        c["before"] = _copy(found)
        node[c["keypath"][-1]] = copy.deepcopy(c["after"])
        kept.append(c)
    p.changes = kept
    out = render(data, raw).encode() if kept else raw.encode()
    p.after_text = out.decode()
    if out != raw.encode():
        target = os.path.realpath(p.path)  # a symlinked settings.json (stow, chezmoi) stays a link
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        atomic_write(target, out)
    return hashlib.sha256(out).hexdigest()
