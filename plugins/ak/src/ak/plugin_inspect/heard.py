"""What plugins actually said to the model, read back from a session's transcript.

Claude Code writes each hook run into the transcript as an `attachment` record:
hook_success (command, raw stdout, durationMs), hook_blocking_error,
hook_non_blocking_error, hook_cancelled. The model-facing text is inside stdout.
A PreToolUse hook that blocks is not an attachment: its stderr comes back as the
tool's error result, "PreToolUse:Write hook error: [<command>]: <stderr>".

A hook that prints nothing is often not recorded at all, so silence is not proof it
never ran.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

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


def read(path: Path) -> list[Heard]:
    """Every hook run in one transcript, in order, each with the prompt number it followed."""
    sid = path.stem
    rows: list[Heard] = []
    turn, prompts = 0, set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
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
            if not isinstance(att, dict) or att.get("type") == "hook_additional_context":
                continue
            be = att.get("blockingError")
            cmd = att.get("command") or (be.get("command", "") if isinstance(be, dict) else "")
            for kind, text in classify(att):
                rows.append(Heard(sid, turn, ts, att.get("hookName") or att.get("hookEvent", ""),
                                  att.get("hookEvent", ""), (cmd or "").strip(), kind,
                                  ANSI.sub("", text or ""), att.get("durationMs")))
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

    def name(self, h: Heard) -> str:
        cands = self.idx.get((h.event, h.command), set())
        if len(cands) == 1:
            return next(iter(cands))
        if len(cands) > 1:
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
            return "|".join(sorted(cands))
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
