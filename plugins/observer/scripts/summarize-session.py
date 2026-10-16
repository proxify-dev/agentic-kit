#!/usr/bin/env python3
# Derived from Claude-Mem v13.40.0 (Apache-2.0, Copyright 2026 Alex Newman) and modified: the redaction patterns. See NOTICE.
"""observer — session summarizer (the generation step).

Distills a finished session into typed observations and one session summary.
Reads the per-session scratch (tool records) + the Claude Code transcript, asks
a cheap model for JSON, redacts secrets, and writes ROWS into the event store
(`<kit>/db/brain.db`, hooks/store_common.py): a session, its prompts, its
observations, its summary — claude-mem's tables, 1:1, plus our verdict · anchors
· supersedes on each observation.

The markdown sink (`--sink md`, one note per observation under
memory/observations/<project>/) is kept for one release and is no longer the
default: a folder of events was the wrong substrate — see docs/store.md.

Trigger-agnostic: the SessionEnd hook (session-end.py) spawns this detached, but
it runs fine by hand, and `observer store backfill` runs it over any transcript a
hook never saw — the scratch is optional; everything it held can be rebuilt
from the transcript. Test paths that bypass the model:
  --from-json FILE   feed prepared output (a {"observations","summary"} object,
                     or a bare observations array) — exercises writer+redaction
  --dry-run          print what would be written, write nothing
  --print-prompt     print the message the observer is sent and exit

Who reads the session: the plugin's agent, agents/observer.md — a standard Claude Code
agent file. Its body is the JUDGEMENT (what counts, how to decide a verdict); the message
this script builds is the FRAME (the output shape, the type table, the ceiling, redaction)
and the session itself. `--bare` ignores plugin agents, so the call hands the file over
inline (`--agents`; on Windows as a file path) and runs as it (`--agent observer`).
On Windows claude starts without a shell: claude.exe, or the real binary behind the
shim's / npm's claude.cmd (claude_launch); with none, the run logs why and skips.

Recursion guard: the model call sets AK_CAPTURE_DISABLE=1 and runs in a neutral
cwd, so the capture hooks no-op inside it and stop-writeback finds no binding.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
from capture_common import (  # noqa: E402
    WRITE_TOOLS, load_events, observations_dir, slugify, clear_scratch, vault_root,
    project_label as cwd_label,
)
import store_common as store  # noqa: E402
import observer_agent  # noqa: E402
from project_match import resolve_binding  # noqa: E402
from vault_root import cache_dir, home_root  # noqa: E402
from transcript_rows import is_chat_row  # noqa: E402
import _brand  # noqa: E402  (its is_windows is looked up per call, so a test can simulate Windows)
from _brand import env, env_name, utf8_stdio, which_exe  # noqa: E402

import memory_schema as schema  # noqa: E402

# The vocabulary is one table (hooks/memory_schema.py).
REGISTRY = schema.registry()
OBS_TYPES = schema.types(REGISTRY)

# The field that separates a right answer from a wrong one. Without it a session's
# first conclusion and its own refutation land in the vault with equal standing —
# measured, not theorised: claude-mem stored both sides of a tool-routing
# question four minutes apart and nothing marks which one survived.
VERDICTS = schema.verdicts(REGISTRY)
TYPE_DEFAULT = REGISTRY["type_default"]
VERDICT_DEFAULT = REGISTRY["verdict_default"]


DEFAULT_MODEL = env("SUMMARIZE_MODEL", "haiku")
# Rows one session may yield: a ceiling the message states, not a target.
MAX_OBSERVATIONS = 20
# Chars of conversation fed to the model. 16k elided the middle of a 10-turn
# session and took a measured dead end with it — a decoy that looked like the answer
# was invisible to every arm because it was never in the prompt. Raised,
# and made configurable, because the cost of a wider window is pennies and the
# cost of a lost dead end is a whole session.
TRANSCRIPT_BUDGET = 48000
LLM_TIMEOUT_S = 240

# --- secret redaction (defense-in-depth; the prompt also instructs the model) ---
# The vendor shapes and the keyed/bearer forms follow claude-mem 13.40's src/utils/redaction.ts
# (Apache-2.0), rewritten for Python's fixed-width lookbehind: the prefix is a group kept in place.
REDACTORS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED PRIVATE KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}"), "[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "[REDACTED]"),             # OpenAI and Anthropic (sk-ant-)
    (re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{24,}\b"), "[REDACTED]"),   # Stripe
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "[REDACTED]"),          # Google API key
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
     "[REDACTED JWT]"),
]
# What a secret value may hold: stops at whitespace, quotes and brackets, so a value never swallows
# the JSON or shell around it, and `[REDACTED]` never re-matches.
_VALUE = r"[A-Za-z0-9_.\-+/=~@#$%^*!?]"
# The value right after a prefix the group keeps: `AWS_SECRET_ACCESS_KEY=…`, `Authorization: Bearer …`,
# and any `<word>_token|_secret|_api_key|password|passwd|_pwd` written as `k=v`, `k: v` or `"k": "v"`.
PREFIXED_SECRETS = [
    re.compile(r"(AWS_SECRET_ACCESS_KEY\s{0,8}[=:]\s{0,8}[\"']?)([A-Za-z0-9/+=]{40})"),
    re.compile(r"(?i)(\bauthorization[\"']?\s{0,8}:\s{0,8}[\"']?bearer\s{1,8})(" + _VALUE + r"{8,})"),
    re.compile(r"(?i)(\b(?:\w*_?(?:password|passwd|secret|api[_-]?key|access[_-]?key|token)|\w+_pwd)"
               r"\\{0,3}[\"']?\s{0,8}[=:]\s{0,8}\\{0,3}[\"']?)(" + _VALUE + r"{6,})"),
]
# A bare `Bearer <token>` with no Authorization header in front: a digit is required, so prose
# ("Bearer authentication") is left alone.
BARE_BEARER = re.compile(r"(\bBearer\s{1,8})(?=" + _VALUE + r"*\d)(" + _VALUE + r"{16,})")


def _keep_prefix(m: re.Match) -> str:
    # A value with no letter is a number (`max_tokens=4096`), not a secret.
    return m.group(0) if not re.search(r"[A-Za-z]", m.group(2)) else m.group(1) + "[REDACTED]"


def redact(text: str) -> str:
    for rx, repl in REDACTORS:
        text = rx.sub(repl, text)
    for rx in (*PREFIXED_SECRETS, BARE_BEARER):
        text = rx.sub(_keep_prefix, text)
    return text


# --- the transcript, read three ways ---------------------------------------------
def _lines(path: str):
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return


def transcript_digest(path: str, budget: int = TRANSCRIPT_BUDGET) -> str:
    parts: list[str] = []
    for e in _lines(path) or []:
        if not is_chat_row(e):  # never by message.role: a saved request body (2.1.300+) has one too
            continue
        role = e["type"]
        content = (e.get("message") or {}).get("content")
        texts = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    texts.append(b["text"])
        blob = "\n".join(t.strip() for t in texts if t and t.strip())
        if blob:
            parts.append(f"{role.upper()}: {blob[:2000]}")
    digest = "\n\n".join(parts)
    if len(digest) > budget:  # keep the head (intent) and the tail (resolution)
        head = digest[: budget // 2]
        tail = digest[-budget // 2:]
        digest = head + "\n\n[...middle elided...]\n\n" + tail
    return digest


def session_meta_from_transcript(path: str) -> dict:
    """cwd, session id and the first/last timestamps, from the transcript's own
    lines — what the scratch's `session` record holds when a hook saw the
    session, rebuilt for one that no hook saw."""
    meta: dict = {}
    first = last = ""
    for e in _lines(path) or []:
        if e.get("type") not in ("user", "assistant"):
            continue
        ts = e.get("timestamp") or ""
        if ts:
            first = first or ts
            last = ts
        if not meta.get("cwd") and e.get("cwd"):
            meta["cwd"] = e["cwd"]
        if not meta.get("session_id") and e.get("sessionId"):
            meta["session_id"] = e["sessionId"]
    meta["started"] = first
    meta["ended"] = last
    return meta


# Claude Code's own echoes arrive as user turns; none of them is a prompt.
SKIP_PROMPT_PREFIXES = ("<local-command-caveat>", "<command-name>", "<local-command-stdout>",
                        "<system-reminder>", "<task-notification>")


def transcript_prompts(path: str) -> list:
    """The person's own turns, numbered: tool results and command echoes are
    user-role lines in the transcript and not prompts."""
    out, n = [], 0
    for e in _lines(path) or []:
        if e.get("type") != "user" or e.get("isSidechain"):
            continue
        content = (e.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                continue
            text = "\n".join(b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text")
        else:
            continue
        text = text.strip()
        if not text or text.startswith(SKIP_PROMPT_PREFIXES):
            continue
        n += 1
        out.append({"n": n, "text": text, "ts": e.get("timestamp") or ""})
    return out


def transcript_tool_events(path: str) -> list:
    """The scratch's tool records, rebuilt from the transcript — same keys
    capture-event.py writes, for a session it never observed."""
    out = []
    for e in _lines(path) or []:
        if e.get("type") != "assistant":
            continue
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                continue
            name = b.get("name") or "?"
            ti = b.get("input") if isinstance(b.get("input"), dict) else {}
            rec: dict = {"kind": "tool", "tool": name}
            fp = ti.get("file_path")
            if name == "Read" and fp:
                rec["files_read"] = [fp]
            elif name in WRITE_TOOLS and fp:
                rec["files_modified"] = [fp]
            if name == "Bash" and ti.get("command"):
                rec["command"] = str(ti["command"])[:200]
            out.append(rec)
    return out


def activity_summary(events: list) -> str:
    modified, read, cmds = [], [], []
    for e in events:
        if e.get("kind") != "tool":
            continue
        modified += e.get("files_modified", [])
        read += e.get("files_read", [])
        if e.get("command"):
            cmds.append(e["command"])
    def uniq(xs, n):
        seen, out = set(), []
        for x in xs:
            if x not in seen:
                seen.add(x); out.append(x)
            if len(out) >= n:
                break
        return out
    lines = []
    if modified:
        lines.append("files modified: " + ", ".join(uniq(modified, 40)))
    if read:
        lines.append("files read: " + ", ".join(uniq(read, 40)))
    if cmds:
        lines.append("commands: " + " | ".join(uniq(cmds, 25)))
    return "\n".join(lines)


# --- prior observations (pass 3: supersedes across sessions) --------------------
PRIORS_MAX = 25
PRIORS_CHARS = 2_000


def _touched_paths(events: list) -> list:
    """Every file this session read or wrote, as the observer will see them —
    the one overlap key known BEFORE the model has said what the session was
    about. Vault-relative or absolute, whichever the event carried."""
    out, seen = [], set()
    for e in events:
        if e.get("kind") != "tool":
            continue
        for p in list(e.get("files_modified") or []) + list(e.get("files_read") or []):
            p = str(p).strip()
            if p and p not in seen:
                seen.add(p); out.append(p)
    return out[:60]


def gate_files_modified(observations: list, events: list) -> list:
    """Keep a row's files_modified only where the session really wrote that file.

    The model names files it read or only talked about as modified: 21% of stored files_modified paths
    matched no Write/Edit call (measured 2026-10-16; claude-mem gated the same field in 13.12.2, #3180).
    A named path survives when its basename matches a written file, and is stored as that file's real
    path. With no tool events at all there is no evidence either way, and the model's list stands."""
    tools = [e for e in events if e.get("kind") == "tool"]
    if not tools:
        return observations
    written: dict[str, str] = {}
    for e in tools:
        for p in e.get("files_modified") or []:
            written.setdefault(Path(str(p)).name, str(p))
    for obs in observations:
        named = [Path(str(p).strip()).name for p in obs.get("files_modified") or []]
        obs["files_modified"] = list(dict.fromkeys(written[b] for b in named if b in written))
    return observations


def _has_col(conn, table: str, col: str) -> bool:
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def prior_observations(label: str, events: list, limit: int = PRIORS_MAX) -> list:
    """What the store already holds that this session may have overturned.

    Two nets, unioned, newest first: the project's most recent rows (a session
    usually follows the last one), and any row whose files or anchors share a
    path this session touched (the same file is the strongest sign of the same
    claim). Rows already hidden — folded, forgotten — are left out: a claim
    nobody can see is not one to supersede. Read-only: this never alters the
    schema, so `--dry-run` and `--print-prompt` stay pure reads, and a store
    that predates the pass-3 columns is simply asked about the columns it has.
    Fail-silent: no store, no priors, and the observer runs as it always did."""
    try:
        conn = store.connect()
    except Exception:
        return []
    try:
        if not _has_col(conn, "observations", "verdict"):
            return []
        hidden = []
        if _has_col(conn, "observations", "folded_into"):
            hidden.append("folded_into IS NULL")
        if _has_col(conn, "observations", "forgotten_at"):
            hidden.append("forgotten_at IS NULL")
        where_hidden = (" AND " + " AND ".join(hidden)) if hidden else ""
        cols = "id, title, verdict, created_at"
        rows = {}
        for r in conn.execute(
            f"SELECT {cols} FROM observations WHERE project=?{where_hidden} "
            "ORDER BY created_at_epoch DESC LIMIT ?", (label, limit)):
            rows[int(r[0])] = r
        paths = _touched_paths(events)
        if paths:
            has_anchors = _has_col(conn, "observations", "anchors")
            for p in paths[:20]:
                # A basename is what an observation is likeliest to carry:
                # the same file spelled from a different root still matches.
                needle = f"%{os.path.basename(p)}%"
                clause = "files_modified LIKE ? OR files_read LIKE ?" + (" OR anchors LIKE ?" if has_anchors else "")
                args = (needle, needle) + ((needle,) if has_anchors else ())
                for r in conn.execute(
                    f"SELECT {cols} FROM observations WHERE project=? AND ({clause}){where_hidden} "
                    "ORDER BY created_at_epoch DESC LIMIT 8", (label,) + args):
                    rows.setdefault(int(r[0]), r)
        out = sorted(rows.values(), key=lambda r: r[3] or "", reverse=True)[:limit]
        return [{"id": int(r[0]), "title": (r[1] or "").strip(), "verdict": r[2] or "",
                 "at": (r[3] or "")[:10]} for r in out]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def priors_block(priors: list) -> str:
    """The prompt's view of the candidates: one line each, capped by chars so a
    busy project cannot crowd the transcript out of haiku's window."""
    if not priors:
        return "(none on record for this project)"
    lines, used = [], 0
    for p in priors:
        line = f"obs:{p['id']} [{p['verdict'] or '-'}] {p['title'][:110]}"
        if used + len(line) + 1 > PRIORS_CHARS:
            lines.append(f"(+{len(priors) - len(lines)} more, not shown)")
            break
        lines.append(line); used += len(line) + 1
    return "\n".join(lines)


_TITLE_NORM = re.compile(r"[^a-z0-9]+")


def _norm_title(s: str) -> str:
    return _TITLE_NORM.sub(" ", (s or "").lower()).strip()


def resolve_supersedes(raw, priors: list) -> str | None:
    """What the column stores. `obs:<id>` when the model named a prior row
    (kept only when the id is one it was shown — an id it invented is a title
    at best); a prior's exact title becomes its `obs:<id>`; anything else is
    kept as written, which is what every row before this pass carries (a
    same-batch title) and what the read side still tolerates."""
    s = (raw or "").strip() if isinstance(raw, str) else ""
    if not s:
        return None
    ids = {p["id"] for p in priors}
    m = re.match(r"^obs:(\d+)$", s)
    if m:
        return s if int(m.group(1)) in ids else None
    key = _norm_title(s)
    for p in priors:
        if key and _norm_title(p["title"]) == key:
            return f"obs:{p['id']}"
    return s


# --- flagged prompts (pass 3: the correction detector) --------------------------
FLAGS_MAX = 10


def flagged_prompts(session_id: str) -> list:
    """What hooks/prompt-flag.py wrote for this session: one line per prompt its
    correction pattern matched. Fail-silent; no file, no flags."""
    try:
        path = Path(cache_dir()) / "sessions" / f"{session_id}.flags.jsonl"
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if isinstance(d, dict) and d.get("prompt"):
                out.append({"flag": str(d.get("flag") or d.get("behavior") or ""), "prompt": str(d["prompt"])})
        return out
    except Exception:
        return []


def flags_block(flags: list) -> str:
    if not flags:
        return ""
    lines = [f'- [{f["flag"] or "flag"}] "{f["prompt"][:160]}"' for f in flags[:FLAGS_MAX]]
    if len(flags) > FLAGS_MAX:
        lines.append(f"- (+{len(flags) - FLAGS_MAX} more)")
    return ("FLAGGED PROMPTS (the correction detector matched what the person typed — a correction, "
            "usually; find the turn in the conversation and file what was corrected on its own row):\n"
            + "\n".join(lines) + "\n\n")


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def local_day(ts: str) -> str:
    """The local calendar day of an ISO timestamp: one carrying a zone (a transcript's `…Z`) is moved
    into this machine's zone first — 21:30Z is tomorrow at +03:00 — and a naive one (the scratch
    record's `started`, written in local time) is its own first ten characters. '' when TS is none."""
    ts = str(ts or "").strip()
    if not _ISO_DATE.match(ts):
        return ""
    if len(ts) > 10 and (ts.endswith("Z") or re.search(r"[+-]\d{2}:?\d{2}$", ts[10:])):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ts[:10]


def session_today(meta: dict) -> str:
    """What the observer is told is "today" — the one thing `build_prompt` never
    said, so "until the switch lands Friday" had no day to resolve against and
    `until` almost never fired.

    The local day of the session's last-seen activity ('ended', else 'started'),
    so a `observer store backfill` run days later still resolves a
    relative day against the conversation, not against the day the backfill
    happened to run. main() fills 'ended' on every path: from the transcript's
    last message when there is one, else — a live run with none — the wall
    clock at SessionEnd; a session that ran for days is told its last day, not
    its first. The wall clock here only
    when a session carries neither."""
    for key in ("ended", "started"):
        day = local_day((meta or {}).get(key) or "")
        if day:
            return day
    return datetime.now().astimezone().strftime("%Y-%m-%d")


# --- prompt ---------------------------------------------------------------------
def build_prompt(meta: dict, events: list, transcript: str,
                 priors: list | None = None, flags: list | None = None,
                 today: str | None = None, trial: dict | None = None) -> str:
    """The message: the frame the store depends on (the output shape, the type and verdict
    vocabularies, the ceiling, redaction) and the session. What the observer NOTICES is
    its agent file's body, agents/observer.md, sent as the system prompt. TRIAL, a shadow run's
    {"output", "rules", "block"} (hooks/shadow_issues.py), adds its section to the shape, its
    rules after the harness's, and its block before the tool activity."""
    today = today or session_today(meta)
    trial = trial or {}
    return f"""Distill the Claude Code session below into durable OBSERVATIONS — the things worth
recalling in a future session — and ONE SUMMARY of the session, judged as your
instructions say. The output shape is fixed.

{schema.prompt_types(REGISTRY)}

Output ONLY a JSON object (no prose, no code fences):
{{
  "observations": [
    {{
      "obs_type": one of {OBS_TYPES},
      "verdict": one of {VERDICTS},
      "anchors": ["literal world strings this is ABOUT: domain, endpoint, model id, repo path"],
      "supersedes": "obs:<id> from PRIOR OBSERVATIONS that this overturns, or a title from THIS output, or null",
      "until": "YYYY-MM-DD the claim stops being true (a deadline, a workaround 'until X lands'), or null",
      "title": "<=80 char imperative summary",
      "subtitle": "optional one-line elaboration or null",
      "about": "what this grounds — a topic/file/feature, short",
      "facts": ["atomic, standalone factual statements"],
      "narrative": "1-4 sentences of context: why it mattered, how it was resolved",
      "concepts": ["lowercase-kebab tags for retrieval"],
      "files_read": ["paths"],
      "files_modified": ["paths"]
    }}
  ],
  "summary": {{
    "request": "one line, in the person's own terms: what they asked for",
    "investigated": "what was looked at to answer it",
    "learned": "what turned out to be true",
    "completed": "what was done",
    "next_steps": "what is still open, or null"
  }}{trial.get("output", "")}
}}

Hard rules (the harness's — they hold whatever the judgement above says):
- 0 to ~{MAX_OBSERVATIONS} observations. If nothing is worth remembering, "observations" is [].
  The summary is always written — it is the one line a session list shows.
- "obs_type" is one of {OBS_TYPES}; "verdict" is one of {VERDICTS}.
- "supersedes" is obs:<id> from the PRIOR OBSERVATIONS list, a title from THIS output, or null.
  Name a prior row only when this session showed its claim is no longer true — a port that
  moved, a route that was replaced, a number that was re-measured. A prior row that still
  holds is not superseded by a row that merely mentions the same thing.
- A FLAGGED PROMPT is a correction until the conversation says otherwise: the thing that was
  corrected is worth an observation of its own (type "correction"),
  and it supersedes whatever row — prior or in this output — recorded the corrected claim.
- "until" is a date only when the session SAID when the claim ends ("until the switch
  lands Friday", "for this week", "before the demo on the 24th"); null for anything durable.
  Resolve a relative day ("Friday", "next week", "the 24th") against TODAY below.
  Recall hides the row after that day, so a date you guessed loses a fact for good.
- REDACT secrets: never emit API keys, tokens, passwords, or private keys. Replace with [REDACTED].
- Titles are imperative and at most 80 characters; facts are atomic and standalone.
{trial.get("rules", "")}
TODAY: {today}
PROJECT: {meta.get('project') or 'unbound'}
PRIOR OBSERVATIONS (this project — the newest, and any sharing a file this session touched):
{priors_block(priors or [])}

{flags_block(flags or [])}{trial.get("block", "")}TOOL ACTIVITY:
{activity_summary(events) or '(none recorded)'}

CONVERSATION:
{transcript or '(transcript unavailable — rely on tool activity above)'}
"""


_NPM_TARGET = re.compile(r'"%~?dp0%?\\([^"%]+\.(?:c?js|mjs|exe))"', re.I)
_EXE = (".exe", ".com")


def _see_through_cmd(path: str) -> list | None:
    """npm's claude.cmd is batch around one real target — a node script or claude.exe. Seeing
    through it (as shim/cc-shim.mjs seeThroughCmd does) means no cmd.exe, which cuts an argument
    at its first newline and a command line at 8191 characters."""
    try:
        body = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    hits = [h for h in _NPM_TARGET.findall(body) if h.lower() != "node.exe"]
    if not hits:
        return None
    d = os.path.dirname(path)
    target = os.path.join(d, *re.split(r"[\\/]", hits[-1]))
    if not os.path.isfile(target):
        return None
    if target.lower().endswith(".exe"):
        return [target]
    node = os.path.join(d, "node.exe")
    node = node if os.path.isfile(node) else which_exe("node")
    return [node, target] if node else None


def _ask_shim(path: str) -> str:
    """The kit's shim (cc-shim.mjs beside its claude.cmd / sh stub) names the real claude it would
    run when CC_SHIM_PRINT_REAL=1 — asked of node directly, the way the shim asks itself."""
    mjs = os.path.join(os.path.dirname(path), "cc-shim.mjs")
    node = which_exe("node")
    if not (node and os.path.isfile(mjs)):
        return ""
    try:
        r = subprocess.run([node, mjs, "claude"], env={**os.environ, "CC_SHIM_PRINT_REAL": "1"},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = (r.stdout or "").strip().splitlines()
    return lines[-1].strip() if r.returncode == 0 and lines else ""


def claude_launch() -> list | None:
    """The argv prefix that starts claude without a shell, or None when nothing spawnable exists.
    POSIX: claude on PATH. Windows: claude.exe first (which_exe); a .cmd or sh stub is the shim's
    or npm's, so ask the shim for the real binary, then see through an npm .cmd; a batch file
    nobody can see through is the last resort — every argument llm_argv passes it is one line."""
    hit = which_exe("claude")
    if not _brand.is_windows():
        return [hit or "claude"]
    if not hit:
        return None
    if hit.lower().endswith(_EXE):
        return [hit]
    real = _ask_shim(hit) or hit
    if real.lower().endswith(_EXE) and os.path.isfile(real):
        return [real]
    if real.lower().endswith((".cmd", ".bat")):
        return _see_through_cmd(real) or [real]
    return None  # an extensionless sh stub: CreateProcess cannot start it


def claude_bin() -> str:
    """The claude to run (the first word of claude_launch), or "claude" when none was found."""
    return (claude_launch() or ["claude"])[0]


def llm_argv(model: str, agent: dict | None = None, agents_file: str | None = None,
             launch: list | None = None) -> list:
    """The headless call; the message goes on stdin (call_llm), not argv — a session's
    transcript outgrows Windows' 32,767-character command line and Linux's 128 KiB per
    argument. --bare: no hooks, plugins, LSP or CLAUDE.md discovery;
    --strict-mcp-config: no MCP servers; --tools "": the observer only reads what it is
    sent. --bare also skips plugin agents, so agents/observer.md goes over inline — or, given
    agents_file, as a file (`--agents` with -p takes a path): the prompt is multi-line and
    ~5 KB, which no batch file can carry on its command line."""
    agent = agent or observer_agent.load()
    spec = {agent["name"]: {"description": agent["description"], "prompt": agent["prompt"], "model": model}}
    agents = json.dumps(spec, ensure_ascii=False)
    if agents_file:
        Path(agents_file).write_text(agents, encoding="utf-8", newline="\n")
        agents = agents_file
    return [*(launch or claude_launch() or ["claude"]), "-p", "--bare", "--strict-mcp-config",
            "--agents", agents, "--agent", agent["name"],
            "--tools", "", "--model", model, "--output-format", "json"]


def call_llm(prompt: str, model: str) -> str:
    child_env = os.environ.copy()
    child_env[env_name("CAPTURE_DISABLE")] = "1"  # the inner claude call must not re-trigger capture
    launch = claude_launch()
    if launch is None:
        sys.stderr.write("summarize: skipped — no claude this process can start without a shell "
                         "(no claude.exe on PATH, and no shim or npm claude.cmd to see through); "
                         "put claude.exe on PATH or set CC_SHIM_REAL\n")
        return ""
    agents_file = None
    if _brand.is_windows():  # the agent spec goes as a file: a batch file's command line is one short line
        fd, agents_file = tempfile.mkstemp(prefix="observer-agents-", suffix=".json")
        os.close(fd)
    try:
        cmd = llm_argv(model, agents_file=agents_file, launch=launch)
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=LLM_TIMEOUT_S, cwd=tempfile.gettempdir(), env=child_env)
    except Exception as exc:
        sys.stderr.write(f"summarize: claude call failed: {exc}\n")
        return ""
    finally:
        if agents_file:
            try:
                os.unlink(agents_file)
            except OSError:
                pass
    out = (proc.stdout or "").strip()
    if not out:
        sys.stderr.write(f"summarize: empty model output (rc={proc.returncode}); "
                         f"stderr={proc.stderr[:300]}\n")
        return ""
    try:  # --output-format json: either {"result": "..."} or a stream-json event array
        obj = json.loads(out)
    except Exception:
        return out  # not JSON — hand raw text to the lenient extractor
    if isinstance(obj, dict) and isinstance(obj.get("result"), str):
        return obj["result"]
    if isinstance(obj, list):
        for ev in reversed(obj):  # the final `result` event carries the answer text
            if isinstance(ev, dict) and ev.get("type") == "result" and isinstance(ev.get("result"), str):
                return ev["result"]
        texts = []  # fallback: stitch assistant text blocks
        for ev in obj:
            if isinstance(ev, dict) and ev.get("type") == "assistant":
                content = (ev.get("message") or {}).get("content")
                if isinstance(content, list):
                    texts += [b.get("text", "") for b in content
                              if isinstance(b, dict) and b.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return out


def _shape(v) -> tuple:
    """(observations, summary) from whatever the model handed back."""
    if isinstance(v, list):
        return [o for o in v if isinstance(o, dict)], None
    if isinstance(v, dict):
        obs = v.get("observations")
        summ = v.get("summary")
        return ([o for o in obs if isinstance(o, dict)] if isinstance(obs, list) else [],
                summ if isinstance(summ, dict) else None)
    return [], None


def extract_object(text: str):
    """The JSON the model handed back (an object, or a bare array), or None."""
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    for open_, close in (("{", "}"), ("[", "]")):
        i, j = text.find(open_), text.rfind(close)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except Exception:
                continue
    return None


def extract_json(text: str) -> tuple:
    """The {"observations", "summary"} object — or, tolerated, a bare array."""
    return _shape(extract_object(text))


def extract_json_array(text: str) -> list:
    """Kept for callers of the old name."""
    return extract_json(text)[0]


# --- the store sink ---------------------------------------------------------------
def _now_iso() -> str:
    # The one clock (store_common.brain_now, AK_NOW): a row written in a
    # story is dated in the story.
    return datetime.fromtimestamp(store.brain_now(), timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _epoch_ms(iso: str) -> int:
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return int(time.time() * 1000)


def project_label(meta: dict) -> str:
    """claude-mem's convention — the basename of the cwd's GIT ROOT (one label
    per repo: ~/agentic-kit/app and ~/agentic-kit are both `ak`, the vault is `.ak`) —
    so the app's project filter and the SessionStart digest, written against that
    convention, see our rows too. The WRITE side never aliases: the two labels are
    joined on read (capture_common.labels_with_aliases, memdb.projectLabels). The
    BOUND project slug rides along in `metadata`."""
    cwd = meta.get("cwd") or ""
    return (cwd_label(cwd) if cwd else "") or meta.get("project") or "unbound"


def bound_project(cwd: str) -> str:
    """The vault project bound to a directory (session-register's rule)."""
    match = resolve_binding(cwd) if cwd else None
    if not match:
        return slugify(Path(cwd).name) if cwd else "unbound"
    note = match["note"]
    try:
        if note.parent == Path(home_root()):
            return "brain"  # brand: historical — the project label every stored row of the vault root carries
    except Exception:
        pass
    return slugify(note.stem)


def _jl(xs) -> str:
    return json.dumps([str(x).strip() for x in (xs or []) if str(x).strip()], ensure_ascii=False)


def observation_row(obs: dict, mid: str, label: str, bound: str, model: str,
                    prompt_number: int, at: str, work_tokens: int = 0,
                    priors: list | None = None) -> dict:
    title = redact((obs.get("title") or "untitled").strip())
    narrative = redact((obs.get("narrative") or "").strip())
    return {
        "memory_session_id": mid,
        "project": label,
        "text": None,
        # claude-mem's meaning: the tokens of work behind the observation. Ours is
        # the conversation the observer read to distil it — the SessionStart
        # stats line ("Nt work | S% savings") is built from this column.
        "discovery_tokens": int(work_tokens or 0),
        "type": obs.get("obs_type") if obs.get("obs_type") in OBS_TYPES else TYPE_DEFAULT,
        "title": title,
        "subtitle": redact((obs.get("subtitle") or "").strip()) or None,
        "facts": redact(_jl(obs.get("facts"))),
        "narrative": narrative,
        "concepts": _jl(obs.get("concepts")),
        "files_read": _jl(obs.get("files_read")),
        "files_modified": _jl(obs.get("files_modified")),
        "prompt_number": prompt_number,
        "created_at": at,
        "created_at_epoch": _epoch_ms(at),
        "content_hash": hashlib.sha1(f"{title}\n{narrative}".encode("utf-8")).hexdigest()[:16],
        "generated_by_model": f"brain/{model}",  # brand: historical (the model tag every stored row carries)
        "metadata": json.dumps({"about": (obs.get("about") or "").strip(), "bound": bound,
                                "source": "observer"}, ensure_ascii=False),
        # An unknown or missing verdict reads as "neutral" rather than being
        # dropped: a row with no verdict must never be mistaken for a route that worked.
        "verdict": obs.get("verdict") if obs.get("verdict") in VERDICTS else VERDICT_DEFAULT,
        "anchors": _jl(obs.get("anchors")),
        # Resolved at write time, with the candidate list in hand: obs:<id>
        # when the model named a prior row it was shown, else what it wrote.
        "supersedes": resolve_supersedes(obs.get("supersedes"), priors or []),
        # A date or nothing. A model that writes "soon" has not said when, and
        # a row that expires on an unparseable day would never expire at all.
        "until": _iso_day(obs.get("until")),
    }


_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso_day(v) -> str | None:
    """YYYY-MM-DD exactly, or None. `until` is compared as a day by recall."""
    s = (v or "").strip() if isinstance(v, str) else ""
    return s if _DAY.match(s) else None


def write_rows(observations: list, summary: dict | None, meta: dict, session_id: str,
               transcript: str, model: str, dry_run: bool, priors: list | None = None) -> dict:
    """One transaction: the session row (reused when claude-mem already wrote
    one for this uuid, so both sides' observations hang off one session), the
    prompts not yet stored, the observations not yet stored, one summary."""
    label = project_label(meta)
    bound = meta.get("project") or bound_project(meta.get("cwd") or "")
    tmeta = session_meta_from_transcript(transcript) if transcript else {}
    prompts = transcript_prompts(transcript) if transcript else []
    now = _now_iso()
    started = tmeta.get("started") or meta.get("started") or now
    ended = tmeta.get("ended") or now
    work = len(transcript_digest(transcript)) // 4 if transcript else 0
    counts = {"session": 0, "prompts": 0, "observations": 0, "summary": 0}

    if dry_run:
        sys.stdout.write(f"\n----- store: {store.store_db()} -----\n")
        sys.stdout.write(f"session {session_id} · project {label} (bound {bound}) · "
                         f"{len(prompts)} prompt(s) · {started} → {ended}\n")
        for obs in observations:
            r = observation_row(obs, session_id, label, bound, model, len(prompts), now, work, priors)
            sup = f"  supersedes {r['supersedes']}" if r.get("supersedes") else ""
            sys.stdout.write(f"  [{r['type']}/{r['verdict']}] {r['title']}{sup}\n")
        if summary:
            sys.stdout.write(f"  summary: {(summary.get('request') or '')[:120]}\n")
        return {"session": 1, "prompts": len(prompts), "observations": len(observations),
                "summary": 1 if summary else 0}

    conn = store.connect()
    store.ensure_schema(conn)
    with conn:
        row = conn.execute(
            "SELECT id, memory_session_id FROM sdk_sessions WHERE content_session_id=? ORDER BY id LIMIT 1",
            (session_id,)).fetchone()
        if row:
            sdb_id, mid = int(row[0]), row[1] or session_id
            conn.execute(
                "UPDATE sdk_sessions SET completed_at=?, completed_at_epoch=?, status='completed', "
                "prompt_counter=MAX(COALESCE(prompt_counter, 0), ?) WHERE id=?",
                (ended, _epoch_ms(ended), len(prompts), sdb_id))
        else:
            mid = session_id
            cur = conn.execute(
                "INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source, "
                "user_prompt, started_at, started_at_epoch, completed_at, completed_at_epoch, status, "
                "prompt_counter) VALUES(?,?,?,?,?,?,?,?,?,'completed',?)",
                (session_id, mid, label, "brain", (prompts[0]["text"][:500] if prompts else None),  # brand: historical (platform_source)
                 started, _epoch_ms(started), ended, _epoch_ms(ended), len(prompts)))
            sdb_id = int(cur.lastrowid)
            counts["session"] = 1

        have = {int(r[0]) for r in conn.execute(
            "SELECT prompt_number FROM user_prompts WHERE content_session_id=?", (session_id,))}
        for p in prompts:
            if p["n"] in have:
                continue
            at = p["ts"] or now
            conn.execute(
                "INSERT INTO user_prompts(session_db_id, content_session_id, prompt_number, prompt_text, "
                "created_at, created_at_epoch) VALUES(?,?,?,?,?,?)",
                (sdb_id, session_id, p["n"], redact(p["text"]), at, _epoch_ms(at)))
            counts["prompts"] += 1

        seen = {r[0] for r in conn.execute(
            "SELECT content_hash FROM observations WHERE memory_session_id=?", (mid,))}
        for obs in observations:
            r = observation_row(obs, mid, label, bound, model, len(prompts), now, work, priors)
            if r["content_hash"] in seen:
                continue
            seen.add(r["content_hash"])
            cols = ", ".join(r)
            conn.execute(f"INSERT INTO observations({cols}) VALUES({', '.join('?' for _ in r)})",
                         tuple(r.values()))
            counts["observations"] += 1

        if summary:
            s = {k: redact(str(summary.get(k) or "").strip()) or None
                 for k in ("request", "investigated", "learned", "completed", "next_steps")}
            files_read = sorted({f for o in observations for f in (o.get("files_read") or []) if f})
            files_edited = sorted({f for o in observations for f in (o.get("files_modified") or []) if f})
            conn.execute(
                "INSERT INTO session_summaries(memory_session_id, project, request, investigated, learned, "
                "completed, next_steps, files_read, files_edited, prompt_number, created_at, created_at_epoch) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, label, s["request"], s["investigated"], s["learned"], s["completed"], s["next_steps"],
                 json.dumps(files_read), json.dumps(files_edited), len(prompts), now, _epoch_ms(now)))
            counts["summary"] = 1
    conn.close()
    return counts


# --- the markdown sink (kept one release; `--sink md`) ----------------------------
def template_field_order() -> list:
    tmpl = Path(home_root()) / "_templates" / "observation.md"
    keys = []
    try:
        text = tmpl.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("\n---", 3)
            for line in text[3:end if end != -1 else None].splitlines():
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):", line)
                if m:
                    keys.append(m.group(1))
    except Exception:
        pass
    return keys or ["type", "obs_type", "title", "subtitle", "about",
                    "verdict", "anchors", "supersedes", "until", "concepts",
                    "files_read", "files_modified", "project", "session", "date",
                    "source", "visibility"]

RAW_KEYS = {"type", "obs_type", "date", "visibility", "verdict"}  # controlled vocab → unquoted


def _q(v: str) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def emit_frontmatter(values: dict, order: list) -> str:
    lines = ["---"]
    for key in order:
        v = values.get(key, [] if key in ("concepts", "anchors", "files_read", "files_modified") else "")
        if isinstance(v, list):
            lines.append(f"{key}: [" + ", ".join(_q(x) for x in v) + "]")
        elif key in RAW_KEYS:
            lines.append(f"{key}: {v}")
        else:
            lines.append(f"{key}: {_q(v)}")
    lines.append("---")
    return "\n".join(lines)


def render_note(obs: dict, meta: dict, session_id: str, date: str, order: list) -> str:
    obs_type = obs.get("obs_type") if obs.get("obs_type") in OBS_TYPES else TYPE_DEFAULT
    title = (obs.get("title") or "untitled").strip()
    values = {
        "type": "observation",
        "obs_type": obs_type,
        "title": title,
        "subtitle": (obs.get("subtitle") or "").strip(),
        "about": (obs.get("about") or "").strip(),
        "verdict": obs.get("verdict") if obs.get("verdict") in VERDICTS else VERDICT_DEFAULT,
        "anchors": [str(a).strip() for a in (obs.get("anchors") or []) if str(a).strip()],
        "supersedes": (obs.get("supersedes") or "").strip(),
        "until": _iso_day(obs.get("until")) or "",
        "concepts": [str(c).strip() for c in (obs.get("concepts") or []) if str(c).strip()],
        "files_read": [str(c).strip() for c in (obs.get("files_read") or []) if str(c).strip()],
        "files_modified": [str(c).strip() for c in (obs.get("files_modified") or []) if str(c).strip()],
        "project": meta.get("project") or "unbound",
        "session": session_id,
        "date": date,
        "source": f"session:{session_id}",
        "visibility": "internal",
    }
    body = [emit_frontmatter(values, order), "", f"# {title}", ""]
    if values["subtitle"]:
        body += [values["subtitle"], ""]
    facts = [str(f).strip() for f in (obs.get("facts") or []) if str(f).strip()]
    body.append("## Facts")
    body += [f"- {f}" for f in facts] if facts else ["- (none)"]
    body += ["", "## Narrative", (obs.get("narrative") or "").strip() or "(none)", ""]
    return redact("\n".join(body))


def write_notes(observations: list, meta: dict, session_id: str, dry_run: bool) -> list:
    order = template_field_order()
    project = slugify(meta.get("project") or "unbound")
    out_dir = observations_dir() / project
    date = time.strftime("%Y-%m-%d")
    stamp = time.strftime("%H%M%S")
    written = []
    for i, obs in enumerate(observations):
        if not isinstance(obs, dict):
            continue
        note = render_note(obs, meta, session_id, date, order)
        fname = f"{date}-{stamp}-{i:02d}-{slugify(obs.get('title') or 'note', 40)}.md"
        path = out_dir / fname
        if dry_run:
            sys.stdout.write(f"\n----- {path} -----\n{note}\n")
            written.append(path)
            continue
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(note, encoding="utf-8", newline="\n")
            written.append(path)
        except Exception as exc:
            sys.stderr.write(f"summarize: failed to write {path}: {exc}\n")
    return written


def maybe_commit(paths: list) -> None:
    if not paths or env("CAPTURE_AUTOCOMMIT") == "0":
        return
    root = Path(home_root())  # the md sink writes under the global vault, its own git repo (capture_common.observations_dir)
    try:
        subprocess.run(["git", "-C", str(root), "add", "memory/observations"],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(root), "commit", "-m",
                        f"capture: {len(paths)} observation(s) from a session"],
                       capture_output=True, timeout=30)
    except Exception:
        pass  # commit is best-effort; the notes are already on disk


# --- main -----------------------------------------------------------------------
def embed_new_rows() -> str:
    """Give the rows just written their vectors — the dense channel of `hybrid`, the
    default search mode. This script is stdlib-only (the hooks' python3), so the embed
    runs in the plugin's own venv, where zvec and fastembed are dependencies. It is
    incremental and one-at-a-time; `--wait` queues behind a running build, which never
    read these rows. A failure costs nothing but those rows' vectors until the next run."""
    import shutil
    uv = shutil.which("uv")
    if not uv:
        return "embed: skipped (no uv on PATH)"
    try:
        r = subprocess.run([uv, "run", "--quiet", "--frozen", "--project", str(PLUGIN_ROOT),
                            "observer", "store", "embed", "--wait"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "embed: timeout"
    tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
    return f"embed: exit {r.returncode}" + "".join(f"\n  {l}" for l in tail)


def run_shadow(args, meta: dict, events: list, priors: list, flags: list, model: str, budget: int) -> None:
    """The issues trial: the observer's message plus OPEN ISSUES and an `issues` section, its answer written
    only to <kit>/db/shadow/issues/ (hooks/shadow_issues.py). The store, the scratch and the embeddings are
    the real run's: this one reads, never writes them."""
    import shadow_issues as shadow
    project = project_label(meta)
    shown, block = shadow.prompt_parts(project)
    prompt = build_prompt(meta, events, transcript_digest(args.transcript, budget), priors, flags,
                          trial={"output": shadow.OUTPUT, "rules": shadow.RULES, "block": block})
    if args.print_prompt:
        sys.stdout.write(prompt)
        return
    if args.from_json:
        answer = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
    else:
        answer = extract_object(call_llm(prompt, model))
    if not isinstance(answer, dict):
        sys.stderr.write("shadow: nothing produced\n")
        return
    if args.dry_run:
        sys.stdout.write(json.dumps(answer.get("issues"), ensure_ascii=False, indent=1) + "\n")
        return
    c = shadow.record(args.session, project, answer, shown)
    sys.stdout.write(f"shadow: {c['opened']} opened, {c['resolved']} resolved, {c['progress']} progress"
                     + (f", {c['unknown']} unknown id(s) dropped" if c["unknown"] else "") + f" · {project}\n")


def main() -> None:
    utf8_stdio()  # the prompt, the log and --dry-run carry non-ASCII; Windows' default is cp1252
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--transcript", default="")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--sink", choices=["store", "md"], default=env("OBS_SINK", "store"))
    ap.add_argument("--from-json", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--print-prompt", action="store_true")
    ap.add_argument("--shadow", action="store_true",
                    help="the issues trial (hooks/shadow_issues.py): its own output, nothing in the store")
    args = ap.parse_args()

    events = load_events(args.session)
    meta = next((e for e in events if e.get("kind") == "session"), {})
    t = session_meta_from_transcript(args.transcript) if args.transcript else {}
    if not meta and args.transcript:  # no hook saw this session — backfill
        meta = {"kind": "session", "session_id": args.session, "cwd": t.get("cwd", ""),
                "project": bound_project(t.get("cwd", "")), "started": t.get("started", ""),
                "ended": t.get("ended", "")}
    elif meta:  # a live run: the scratch holds only `started` — the day it ended is the transcript's last line, or now
        meta = {**meta, "ended": t.get("ended") or meta.get("ended") or datetime.now().astimezone().isoformat(timespec="seconds")}
    if not any(e.get("kind") == "tool" for e in events) and args.transcript:
        events = [meta] + transcript_tool_events(args.transcript)

    model = args.model
    budget = TRANSCRIPT_BUDGET

    # What the store already says about this project — fetched once, shown to
    # the model as candidates for `supersedes`, and used again at write time
    # to resolve what it named. A read; nothing here alters the store.
    priors = prior_observations(project_label(meta), events)
    flags = flagged_prompts(args.session)

    if args.shadow:
        run_shadow(args, meta, events, priors, flags, model, budget)
        return

    if args.print_prompt:
        sys.stdout.write(build_prompt(meta, events, transcript_digest(args.transcript, budget), priors, flags))
        return

    if args.from_json:
        observations, summary = _shape(json.loads(Path(args.from_json).read_text(encoding="utf-8")))
    else:
        prompt = build_prompt(meta, events, transcript_digest(args.transcript, budget), priors, flags)
        observations, summary = extract_json(call_llm(prompt, model))

    if not observations and not summary:
        sys.stderr.write("summarize: nothing produced\n")
        return
    observations = gate_files_modified(observations, events)

    if args.sink == "md":
        if args.no_commit:
            os.environ[env_name("CAPTURE_AUTOCOMMIT")] = "0"
        written = write_notes(observations, meta, args.session, args.dry_run)
        if written and not args.dry_run:
            maybe_commit(written)
            clear_scratch(args.session)
        sys.stdout.write(f"summarize: {len(written)} observation note(s)"
                         + (" [dry-run]" if args.dry_run else "") + "\n")
        return

    counts = write_rows(observations, summary, meta, args.session, args.transcript, model, args.dry_run, priors)
    if not args.dry_run:
        clear_scratch(args.session)
    sys.stdout.write(f"summarize: {counts['observations']} observation(s), "
                     f"{counts['prompts']} prompt(s), {counts['summary']} summary → store"
                     + (" [dry-run]" if args.dry_run else "") + "\n")
    if counts["observations"] and not args.dry_run and env("OBS_EMBED", "1") != "0":
        sys.stdout.write(embed_new_rows() + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write(f"summarize: fatal {exc}\n")
        sys.exit(0)
