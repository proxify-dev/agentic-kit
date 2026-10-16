"""What plugins actually said to the model, read back from a session's transcript.

Claude Code writes each hook run into the transcript as an `attachment` record:
hook_success (command, raw stdout, durationMs), hook_blocking_error,
hook_non_blocking_error, hook_cancelled. The model-facing text is inside stdout.
A PreToolUse hook that blocks is not an attachment: its stderr comes back as the
tool's error result, "PreToolUse:Write hook error: [<command>]: <stderr>".

A hook that prints nothing is often not recorded at all, so silence is not proof it
never ran.

Since Claude Code 2.1.300 the transcript also saves every model call (transcript_rows,
`request_record`). Then the context the hooks of one moment added is recorded once more, as one
`hook_additional_context` row (its `content`, one text a hook), and goes out as a `system` message —
a PostToolUse one inside the tool result — saved as an `api-request-blob` and named by the
`api-request` that sent it. For a row written by 2.1.300 or later each such text is tied to its hook
run and to the call that first carried it (`source="record"`, `call` = that api-request's number in
the file); a text no hook run printed gets a row of its own; a text no call carried stays
`source="hooks"`. Rows from older versions are read as they always were, so a session resumed across
the upgrade is read row by row. A text counts its tokens once: every later call re-sends it, from
the prompt cache.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ak import transcript_rows
from ak._brand import claude_home, project_dir
from ak.plugin_inspect.installed import Plugin

# kinds the model reads; `user` is shown to the person only, `error`/`silent` to no one
TO_MODEL = {"context", "deny", "ask", "block", "stderr"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")
HOOK_ERR = re.compile(r"^(\w+:\w+) hook error: \[(.*?)\]: (.*)", re.S)


@dataclass
class Heard:
    session: str
    turn: int
    ts: str
    hook: str  # "PreToolUse:Read", "SessionStart:startup"
    event: str  # "PreToolUse"
    command: str
    kind: str  # context · deny · ask · block · stderr · user · error · silent
    text: str
    ms: int | None = None
    plugin: str = "?"
    source: str = "hooks"  # hooks: read off the hook runs · record: also found in a saved model call
    call: int | None = None  # the api-request, counted from 1 in its file, that first carried the text

    @property
    def tokens(self) -> int:
        """Rough: four characters a token. Only what reaches the model counts."""
        return len(self.text) // 4 if self.kind in TO_MODEL else 0


# ── transcripts ──────────────────────────────────────────────────────────────
def transcripts(n: int | None = None, cwd: Path | None = None) -> list[Path]:
    """Newest first: this project's sessions when cwd is given, else every project's."""
    base = project_dir(cwd) if cwd else claude_home() / "projects"
    pattern = "*.jsonl" if cwd else "*/*.jsonl"
    files = sorted(base.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[:n] if n else files


def find(session: str) -> Path | None:
    """A transcript by session id, its first 8+ characters, or a path."""
    p = Path(session).expanduser()
    if p.is_file():
        return p
    hits = list((claude_home() / "projects").glob(f"*/{session}*.jsonl"))
    return max(hits, key=lambda f: f.stat().st_mtime) if hits else None


# ── one hook record → what it said ───────────────────────────────────────────
def classify(att: dict) -> list[tuple[str, str]]:
    t, event = att.get("type"), att.get("hookEvent", "")
    if t == "hook_blocking_error":
        be = att.get("blockingError")
        text = be.get("blockingError", "") if isinstance(be, dict) else str(be or "")
        # exit 2 after the tool ran blocks nothing: its stderr is just shown to the model
        return [("stderr" if event == "PostToolUse" else "block", text)]
    if t == "hook_non_blocking_error":
        return [("error", att.get("stderr", ""))]
    if t == "hook_cancelled":
        return [("error", "cancelled (timed out or interrupted)")]
    if t != "hook_success":
        return []
    out = (att.get("stdout") or "").strip()
    if not out:
        return [("silent", "")]
    try:
        d = json.loads(out)
    except ValueError:
        # plain stdout reaches the model on SessionStart and UserPromptSubmit only
        return [("context" if event in ("SessionStart", "UserPromptSubmit") else "silent", out)]
    if not isinstance(d, dict):
        return [("silent", out)]
    said = []
    hso = d.get("hookSpecificOutput") or {}
    if hso.get("additionalContext"):
        said.append(("context", hso["additionalContext"]))
    if hso.get("permissionDecision") in ("deny", "ask"):
        said.append((hso["permissionDecision"], hso.get("permissionDecisionReason", "")))
    if d.get("decision") == "block":
        said.append(("block", d.get("reason", "")))
    if d.get("systemMessage"):
        said.append(("user", d["systemMessage"]))
    return said or [("silent", "")]


def _prompt(d: dict) -> str | None:
    """The text of a prompt the person typed, else None."""
    if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
        return None
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return None if c.startswith("<") else c
    if isinstance(c, list) and not any(b.get("type") == "tool_result" for b in c):
        texts = [b.get("text", "") for b in c if b.get("type") == "text"]
        return "\n".join(texts) if texts else None
    return None


def _tool_blocks(d: dict):
    c = (d.get("message") or {}).get("content") if d.get("type") == "user" else None
    for b in c if isinstance(c, list) else []:
        if b.get("type") != "tool_result" or not b.get("is_error"):
            continue
        body = b.get("content")
        if isinstance(body, list):
            body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
        m = HOOK_ERR.match(body or "")
        if m:
            yield m.group(1), m.group(2).replace('\\"', '"'), m.group(3).strip()


def _sent_text(message: dict) -> str:
    """Every text in a saved message body, those inside tool results included."""
    c = message.get("content") if isinstance(message, dict) else None
    if isinstance(c, str):
        return c
    out = []
    for b in c if isinstance(c, list) else []:
        if not isinstance(b, dict):
            continue
        if isinstance(b.get("text"), str):
            out.append(b["text"])
        inner = b.get("content") if b.get("type") == "tool_result" else None
        if isinstance(inner, str):
            out.append(inner)
        elif isinstance(inner, list):
            out.extend(x["text"] for x in inner if isinstance(x, dict) and isinstance(x.get("text"), str))
    return "\n".join(out)


class _Calls:
    """The saved calls of one transcript, read in order: which call first carried a hook's text.

    A text is looked for in the bodies each call added (its `tail`), from the call just before its
    `hook_additional_context` row on: after tools run side by side, Claude Code writes that row after
    the call that sent it (188 of 1,034 texts over 300 real sessions, 2026-10-15)."""

    def __init__(self):
        self.bodies: dict[str, str] = {}  # api-request-blob hash → its text
        self.pending: list[Heard] = []
        self.n = 0
        self.last = ""  # what the newest call added

    def add(self, h: Heard) -> None:
        if self.n and h.text.strip() and h.text.strip() in self.last:
            h.source, h.call = "record", self.n
        else:
            self.pending.append(h)

    def row(self, d: dict) -> None:
        t = d.get("type")
        if t == "api-request-blob":
            self.bodies[str(d.get("hash"))] = _sent_text(d.get("message") or {})
        elif t == "api-request":
            self.n += 1
            self.last = "\n".join(self.bodies.get(h, "") for h in d.get("tail") or [])
            left = []
            for h in self.pending:
                if h.text.strip() in self.last:
                    h.source, h.call = "record", self.n
                else:
                    left.append(h)
            self.pending = left


def read(path: Path) -> list[Heard]:
    """Every hook run in one transcript, in order, each with the prompt number it followed."""
    sid = path.stem
    rows: list[Heard] = []
    turn, prompts = 0, set()
    calls = _Calls()
    waiting: dict[str, list[Heard]] = defaultdict(list)  # context from 2.1.300+ hook runs, by event
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"api-request' in line[:40]:  # the saved calls (2.1.300+): their own rows, never a hook's
                try:
                    calls.row(json.loads(line))
                except ValueError:
                    pass
                continue
            # most lines are neither: skip the JSON parse for them
            is_hook = '"hook_' in line
            is_user = '"user"' in line and ('"tool_result"' not in line or "hook error:" in line)
            if not (is_hook or is_user):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = str(d.get("timestamp", ""))
            p = _prompt(d)
            if p is not None:
                if p[:500] not in prompts:  # a prompt can be written twice
                    prompts.add(p[:500])
                    turn += 1
                continue
            for hook, cmd, text in _tool_blocks(d):
                event = hook.partition(":")[0]
                rows.append(Heard(sid, turn, ts, hook, event, cmd,
                                  "block" if event == "PreToolUse" else "stderr", text))
            att = d.get("attachment")
            if not isinstance(att, dict):
                continue
            recorded = transcript_rows.has("request_record", d)
            event = att.get("hookEvent", "")
            if att.get("type") == "hook_additional_context":
                if recorded:  # before 2.1.300 it only repeats the hook runs' own text: skipped
                    for part in att.get("content") or []:
                        text = ANSI.sub("", str(part))
                        h = next((w for w in waiting[event] if w.text.strip() == text.strip()), None)
                        if h is not None:
                            waiting[event].remove(h)
                        else:  # no hook run on record printed it
                            h = Heard(sid, turn, ts, att.get("hookName") or event, event, "", "context", text)
                            rows.append(h)
                        calls.add(h)
                continue
            be = att.get("blockingError")
            cmd = att.get("command") or (be.get("command", "") if isinstance(be, dict) else "")
            for kind, text in classify(att):
                h = Heard(sid, turn, ts, att.get("hookName") or event, event, (cmd or "").strip(), kind,
                          ANSI.sub("", text or ""), att.get("durationMs"))
                rows.append(h)
                if recorded and kind == "context":
                    waiting[event].append(h)
    return rows


# ── whose was it ─────────────────────────────────────────────────────────────
class Owners:
    """Names the plugin behind each hook run.

    1. The (event, command) pair, looked up in every installed plugin and every cached
       version. Usually one plugin owns it.
    2. Two plugins can run the very same command line (`uv run … hooks/session-start.py`).
       Then the text's opening words decide: the candidate whose source holds them said it.
    3. An absolute path under `.../plugins/<name>/` (either slash) names its plugin; any other command
       outside a plugin root is a settings.json hook.
    """

    def __init__(self, installed: list[Plugin], history: dict[tuple[str, str], set[str]]):
        self.idx: dict[tuple[str, str], set[str]] = {k: set(v) for k, v in history.items()}
        self.dirs: dict[str, list[Path]] = {}
        for p in installed:
            self.dirs.setdefault(p.name, []).append(p.path)
            for h in p.hooks:
                self.idx.setdefault((h.event, h.command), set()).add(p.name)
        self._src: dict[str, str] = {}

    def _source(self, name: str) -> str:
        if name not in self._src:
            # the installed copy and every cached version: a past session ran a past version
            dirs = [*self.dirs.get(name, []), *(claude_home() / "plugins" / "cache").glob(f"*/{name}/*")]
            parts = []
            for d in dirs:
                for f in [*d.glob("hooks/**/*"), *d.glob("src/**/*.py"), *d.glob("scripts/**/*")]:
                    if f.is_file() and f.suffix in (".py", ".sh", ".js", ".mjs", ".ts", ""):
                        try:
                            parts.append(f.read_text(encoding="utf-8", errors="replace"))
                        except OSError:
                            pass
            self._src[name] = "\n".join(parts)
        return self._src[name]

    def _by_words(self, h: Heard, cands: set[str]) -> str | None:
        """The one candidate whose source holds the text's opening words, else None."""
        words = h.text.split()
        # from the first word, then past it: a tag like `[{name}]` is built at runtime
        for start, least in ((0, 1), (1, 2)):
            for k in range(least, 6):
                if start + k > len(words):
                    break
                lit = " ".join(words[start:start + k])
                hits = {c for c in cands if lit in self._source(c)}
                if len(hits) == 1:
                    return hits.pop()
                if not hits:
                    break
        return None

    def name(self, h: Heard) -> str:
        if not h.command and h.kind == "context":
            # context no hook run on record printed (2.1.300+): only its words can name it, among the plugins
            # with a hook on its event; never a joined guess, a settings.json hook may have said it
            cands = {n for (event, _), names in self.idx.items() if event == h.event for n in names}
            return self._by_words(h, cands) or "?"
        cands = self.idx.get((h.event, h.command), set())
        if len(cands) == 1:
            return next(iter(cands))
        if len(cands) > 1:
            return self._by_words(h, cands) or "|".join(sorted(cands))
        m = re.search(r"[/\\]plugins[/\\](?:cache[/\\][^/\\]+[/\\])?([\w.\-]+)[/\\]", h.command)
        if m:
            return m.group(1)
        if h.command and "CLAUDE_PLUGIN_ROOT" not in h.command:
            return "settings.json"
        return "?"

    def name_all(self, rows: list[Heard]) -> list[Heard]:
        for r in rows:
            r.plugin = self.name(r)
        return rows
