#!/usr/bin/env python3
"""observer — shared low-level for the session-capture pipeline.

Lives in hooks/ so the capture hooks import nothing outside this directory
(same discipline as project_match.py). Dependency-free.

The loop writes one JSONL scratch per Claude Code session between
UserPromptSubmit (session-register.py) and SessionEnd (session-end.py); each
PostToolUse appends a compact tool record (capture-event.py). At SessionEnd the
summarizer (scripts/summarize-session.py) distills scratch + transcript into
rows of the event store (store_common.py). The scratch dir is git-ignored
(<kit>/cache/sessions/) — derived and throwaway; the store is the durable output, and
even it is rebuildable from the transcripts (`observer store backfill`).
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _brand import env, env_name  # noqa: E402
from vault_root import brain_root, cache_dir, db_dir, home_root  # noqa: E402
from transcript_rows import is_chat_row  # noqa: E402


def command(verb: str = "") -> str:
    """This plugin's own command: bin/observer, on the Bash tool's PATH while the plugin is enabled, with or
    without the `ak` CLI (plugins/AGENTS.md §6). The hooks run on the standard library, so it is spelled here."""
    return f"observer {verb}".strip()


def vault_root(cwd: str | None = None) -> Path:
    """The vault owning `cwd` (the session's, when a hook payload carries one);
    the process cwd otherwise. See hooks/vault_root.py."""
    return brain_root(cwd)


def state_dir() -> Path:
    """The capture scratch, one set of files per session: the kit's cache, from any cwd.
    A project scope holds notes, never these."""
    d = Path(cache_dir()) / "sessions"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def observations_dir() -> Path:
    """The legacy markdown sink: notes, so the global vault's."""
    return Path(home_root()) / "memory" / "observations"


def git_root(path: str) -> str:
    """The nearest ancestor holding a `.git` (a directory, or the file a worktree
    carries), or "". A walk, not a subprocess — hooks stay cheap."""
    try:
        p = Path(path or "").expanduser().resolve()
    except Exception:
        return ""
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return str(d)
    return ""


def project_label(cwd: str) -> str:
    """claude-mem's label for a directory: the basename of its git root, else of
    the directory itself — one label per repo, so `~/agentic-kit/app` and `~/agentic-kit`
    are both `ak`. The store keeps this convention; the observer writes it
    and the SessionStart digest reads by it."""
    root = git_root(cwd) or (cwd or "").rstrip("/\\")
    return os.path.basename(root) or "unbound"


# ── the read-side alias ──────────────────────────────────────────────────────
# The 2026-09-15 split moved the code out of the vault: `~/.ak` keeps the
# data and labels its sessions `.ak`, `~/agentic-kit` holds the code and labels
# its sessions `ak`. They are ONE project to memory, and the ~37k rows
# written before that day all say `.ak`.
#
# The WRITE side is deliberately untouched — `project_label` above still files
# every session under its own git root, so no row is rewritten and no history
# is rebuilt. Only READS expand: a session in either tree asks for both labels.
# Other readers of this store do the same join.
PROJECT_ALIASES = {"brain": [".brain"], ".brain": ["brain"]}  # brand: historical — the labels the stored rows carry


# A workspace move (`ak ws move`) renames a repo without rewriting a row: it appends a
# label line to lineage.jsonl, and the expansion below follows it both ways — `agentic-kit`
# asks for `ak` and, through the alias above, `.ak`. No file: the alias alone, as before.
try:
    import lineage as _lineage  # the vendored copy beside this file (memory move/lineage.py)
except ImportError:  # pragma: no cover
    _lineage = None


def labels_with_aliases(labels: list) -> list:
    """`labels` plus every alias they imply, order-preserving and de-duplicated.

    The input order is kept and each alias lands directly after the label that
    pulled it in, so the caller's own precedence survives the expansion. Aliases
    come from PROJECT_ALIASES and from every label move in lineage.jsonl, transitively."""
    rows = _lineage.rows() if _lineage else []
    out = []
    for label in labels or []:
        todo = [label]
        while todo:
            candidate = todo.pop(0)
            if not candidate or candidate in out:
                continue
            out.append(candidate)
            todo += list(PROJECT_ALIASES.get(candidate, ()))
            if rows:
                todo += _lineage.names("label", candidate, _rows=rows)[1:]
    return out


# ── the observer's off switch ────────────────────────────────────────────────
# One file is the whole contract between the CLI (`observer mute|unmute|status`)
# and the three capture hooks. The logic is MIRRORED in
# src/vault/capture_flag.py — deliberately, not by accident: hooks run
# under bare system python3 and cannot import the installed `ak` package.
# Keep the two halves in sync; the file format is kept trivial so they can't drift far.
#
#   ~/.ak/db/capture.json   (the kit's — one switch file for every scope)
#   {"enabled": true, "disabled_paths": ["/abs/dir", ...], "updated": "<iso>"}
#
# Resolution — first match wins:
#   1. AK_CAPTURE_DISABLE in env → off (the summarizer's own recursion guard)
#   1b. a program started it        → off (automated_entrypoint; AK_CAPTURE=1 overrides)
#   2. no capture.json              → on  (default; unchanged behaviour)
#   3. capture.json unparseable     → off (a present-but-broken switch means someone
#                                          configured this — never write memory we
#                                          may have been told not to)
#   4. enabled == false             → off (globally)
#   5. cwd inside a disabled path   → off (per-project)
#   6. otherwise                    → on
CAPTURE_CONFIG_NAME = "capture.json"


def capture_config_path(cwd: str | None = None) -> Path:  # noqa: ARG001 — one switch file, from any cwd
    return Path(db_dir()) / CAPTURE_CONFIG_NAME


def _within(child: str, parent: str) -> bool:
    """True if `child` is `parent` or lives under it. Symlink-resolved, never raises."""
    try:
        c = Path(child).expanduser().resolve()
        p = Path(parent).expanduser().resolve()
    except Exception:
        return False
    return c == p or p in c.parents


# Who started the session: hooks/session_origin.py, the one rule every ak plugin's hooks
# share (canonical in ak; scripts/copies.py writes and checks this copy).
from session_origin import HUMAN_ENTRYPOINTS, automated as automated_entrypoint  # noqa: E402,F401


def capture_state(cwd: str | None = None) -> tuple:
    """(enabled: bool, reason: str) for this cwd. Never raises."""
    if env("CAPTURE_DISABLE"):
        return False, f"{env_name('CAPTURE_DISABLE')} is set in the environment"
    if ep := automated_entrypoint():
        return False, f"off — an automated session (entrypoint {ep}); {env_name('CAPTURE')}=1 records it"
    path = capture_config_path(cwd)
    if not path.exists():
        return True, "on — no capture.json, default"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise ValueError("capture.json is not a JSON object")
    except Exception as e:
        return False, f"off — {path} is unreadable ({e}); fix or delete it"
    if cfg.get("enabled") is False:
        return False, f"off — muted everywhere (`{command('unmute')}` to resume)"
    for entry in cfg.get("disabled_paths") or []:
        if isinstance(entry, str) and cwd and _within(cwd, entry):
            return False, f"off — muted for {entry} (`{command('unmute --here')}`)"
    return True, "on"


def capture_enabled(cwd: str | None = None) -> bool:
    return capture_state(cwd)[0]


def safe_id(session_id: str) -> str:
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", (session_id or "unknown").strip())
    return sid or "unknown"


def scratch_path(session_id: str) -> Path:
    return state_dir() / f"{safe_id(session_id)}.jsonl"


def append_event(session_id: str, event: dict) -> None:
    try:
        with open(scratch_path(session_id), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass  # a broken capture must never disturb the session


def load_events(session_id: str) -> list:
    p = scratch_path(session_id)
    out: list = []
    if not p.exists():
        return out
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def clear_scratch(session_id: str) -> None:
    try:
        scratch_path(session_id).unlink(missing_ok=True)
    except Exception:
        pass


def slugify(text: str, maxlen: int = 50) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:maxlen].rstrip("-")) or "note"


WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SUBSTANTIVE_MIN = 5  # tool uses that count a session substantive without a write


def transcript_activity(path: str):
    """Cheap JSONL scan: (any write-tool use, total tool uses), over the chat rows only: since
    Claude Code 2.1.300 a saved request body repeats a reply's tool calls (transcript_rows)."""
    wrote, tool_uses = False, 0
    if not path:
        return wrote, tool_uses
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"tool_use"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if not is_chat_row(entry):
                    continue
                content = (entry.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_uses += 1
                        if block.get("name") in WRITE_TOOLS:
                            wrote = True
                if wrote and tool_uses >= SUBSTANTIVE_MIN:
                    break
    except Exception:
        pass
    return wrote, tool_uses


def is_substantive(transcript_path: str, session_id: str | None = None) -> bool:
    """Substantive iff the transcript shows a write or >= SUBSTANTIVE_MIN tool uses;
    falls back to the scratch event count when the transcript is unavailable."""
    wrote, tool_uses = transcript_activity(transcript_path or "")
    if wrote or tool_uses >= SUBSTANTIVE_MIN:
        return True
    if session_id:
        events = [e for e in load_events(session_id) if e.get("kind") == "tool"]
        if any(e.get("files_modified") for e in events) or len(events) >= SUBSTANTIVE_MIN:
            return True
    return False
