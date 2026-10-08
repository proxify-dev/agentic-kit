"""Query engine: SQLite read-only filters, aggregations, drill, and formatters.

All query-path functions open read-only SQLite connections via
:func:`db.get_readonly_connection` and MUST NOT trigger indexing. Indexing
is the exclusive responsibility of ``tracer trace index`` (CLI) or an explicit
caller of :func:`storage.incremental_index`. See the
``thin-index-sqlite-migration`` change for the architectural rationale.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .verbs import command, spell
from .db import DB_PATH, get_readonly_connection
from .models import (
    AgentSpawn,
    ErrorSignal,
    FileTouch,
    InteractionRecord,
    SkillInvocation,
    ToolCall,
)
from .storage import (
    get_conversations_dir_for_hash,
    list_session_files,
)
from .turns import TurnSplitter, one_line, prompt_text, said, sender, short_tool


# How a hint names a knob: the flag or the verb a reader types next (verbs.py: `tracer trace turn`).
_HINTS = {
    "limit": "--limit {n}",
    "drill": "{turn_verb} {id}",
    "step": "{turn_verb} {id} --open {n}",
    "meta": "--include-meta",
    "keep": "--exclude-session ''",
    "automated": "--entrypoint all",
    "user": "{replay_verb} {sid} --role user",
    "session": "{show_verb} {sid}",
}


def verb(key: str) -> str:
    """A verb as a shell types it (verbs.py): `tracer trace turn`."""
    return spell(key)


def hint(key: str, **kw) -> str:
    """The spelling of one knob: a flag, or the verb that opens what a line names."""
    iid = str(kw.get("id", ""))
    sid, _, turn = iid.rpartition("-")
    fields = {"door": command(), "sid": sid, "turn": turn, "turn_verb": verb("turn"),
              "replay_verb": verb("replay"), "show_verb": verb("show"), **kw}
    return _HINTS[key].format(**fields)


@dataclass
class QueryFilters:
    project: str = ""
    # any of these project hashes: the folders `tracer sessions` counts as one project (the
    # checkout, its worktrees, the folders inside); `project` narrows to one dir instead
    projects: tuple[str, ...] = ()
    plugin: str = ""
    agent_type: str = ""
    skill_name: str = ""
    tool_name: str = ""
    has_errors: bool = False
    since: str = ""
    until: str = ""
    git_branch: str = ""
    session_id: str = ""
    limit: int | None = None


def _open_ro(con: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """Return the supplied connection, or open a fresh read-only one."""
    if con is not None:
        return con
    return get_readonly_connection()


def _has_table(con: sqlite3.Connection | None, name: str) -> bool:
    if con is None:
        return True
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _build_where(filters: QueryFilters, con: sqlite3.Connection | None = None) -> tuple[str, list]:
    """Build SQL WHERE clause and params from filters. ``con`` lets a filter check the
    index has what it needs (a read-only connection cannot migrate an older file)."""
    clauses: list[str] = []
    params: list = []

    if filters.project:
        clauses.append("i.project_hash = ?")
        params.append(filters.project)

    if filters.projects:
        clauses.append(f"i.project_hash IN ({','.join('?' * len(filters.projects))})")
        params.extend(filters.projects)

    if filters.plugin:
        clauses.append(
            "EXISTS (SELECT 1 FROM i_plugins ip WHERE ip.iid = i.id AND ip.plugin = ?)"
        )
        params.append(filters.plugin)

    if filters.agent_type:
        clauses.append(
            "EXISTS (SELECT 1 FROM i_agents ia WHERE ia.iid = i.id AND ia.type = ?)"
        )
        params.append(filters.agent_type)

    if filters.skill_name:
        if _has_table(con, "i_skills"):
            # by name: "recall" matches "observer:recall" too
            clauses.append(
                "EXISTS (SELECT 1 FROM i_skills sk WHERE sk.iid = i.id AND (sk.name = ? OR sk.name LIKE ?))"
            )
            params.extend([filters.skill_name, f"%:{filters.skill_name}"])
        else:  # an index written before skills were kept: any Skill call
            clauses.append(
                "EXISTS (SELECT 1 FROM i_tools it WHERE it.iid = i.id AND it.name = 'Skill')"
            )

    if filters.tool_name:
        clauses.append(
            "EXISTS (SELECT 1 FROM i_tools it WHERE it.iid = i.id AND it.name = ?)"
        )
        params.append(filters.tool_name)

    if filters.has_errors:
        clauses.append("EXISTS (SELECT 1 FROM i_errors ie WHERE ie.iid = i.id)")

    if filters.since:
        clauses.append("i.timestamp >= ?")
        params.append(filters.since)

    if filters.until:
        # inclusive: a bare date keeps that whole day (its timestamps sort after "2026-09-06")
        clauses.append("i.timestamp <= ?" if len(filters.until) > 10 else "substr(i.timestamp, 1, 10) <= ?")
        params.append(filters.until)

    if filters.git_branch:
        clauses.append("i.git_branch LIKE ?")
        params.append(filters.git_branch + "%")

    if filters.session_id:
        clauses.append("i.session_id = ?")
        params.append(filters.session_id)

    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


# The interactions columns an InteractionRecord is read from, in _row_to_record's order.
_REC_FIELDS = (
    "id", "session_id", "timestamp", "git_branch", "user_message_preview",
    "assistant_reply_preview", "intent", "outcome", "tokens_estimated",
    "duration_seconds", "ending", "local_commands",
)
_N_REC = len(_REC_FIELDS)


def _rec_cols(con: sqlite3.Connection, alias: str = "i") -> str:
    """The SELECT list for _row_to_record. A read-only connection cannot migrate, so a
    column an older writer never added reads as NULL instead of failing the query."""
    have = {r[1] for r in con.execute("PRAGMA table_info(interactions)")}
    return ", ".join(f"{alias}.{f}" if f in have else f"NULL AS {f}" for f in _REC_FIELDS)


def _row_to_record(row: tuple, con: sqlite3.Connection) -> InteractionRecord:
    """Convert a query row + junction data into an InteractionRecord."""
    iid = row[0]
    plugins = [
        r[0]
        for r in con.execute(
            "SELECT plugin FROM i_plugins WHERE iid = ?", (iid,)
        ).fetchall()
    ]
    tools = [
        ToolCall(name=r[0], count=r[1])
        for r in con.execute(
            "SELECT name, count FROM i_tools WHERE iid = ?", (iid,)
        ).fetchall()
    ]
    files = [
        FileTouch(path=r[0], operation=r[1])
        for r in con.execute(
            "SELECT path, operation FROM i_files WHERE iid = ?", (iid,)
        ).fetchall()
    ]
    agents = [
        AgentSpawn(type=r[0], prompt_preview=r[1])
        for r in con.execute(
            "SELECT type, prompt_preview FROM i_agents WHERE iid = ?", (iid,)
        ).fetchall()
    ]
    errors = [
        ErrorSignal(type=r[0], preview=r[1])
        for r in con.execute(
            "SELECT type, preview FROM i_errors WHERE iid = ?", (iid,)
        ).fetchall()
    ]
    skills: list[SkillInvocation] = []

    return InteractionRecord(
        id=row[0],
        session_id=row[1],
        timestamp=row[2] or "",
        git_branch=row[3] or "",
        user_message_preview=row[4] or "",
        assistant_reply_preview=row[5] or "",
        plugins=plugins,
        agents_spawned=agents,
        skills_invoked=skills,
        tools_called=tools,
        files_touched=files,
        error_signals=errors,
        intent=row[6],
        outcome=row[7],
        tokens_estimated=row[8] or 0,
        duration_seconds=row[9] or 0,
        ending=row[10] or "",
        local_commands=json.loads(row[11]) if row[11] else [],
    )


def staleness_warning(project_hash: str, con: sqlite3.Connection) -> None:
    """Emit a stderr warning if the index is behind the on-disk JSONL state.

    The index is kept fresh by a Stop/SessionStart hook that runs the
    indexer after every turn. This warning is an **invariant check**: if it
    fires in normal use, something is broken with the hook chain. See
    `plugins/tracer/hooks/convo-index-stop.py` and the conversation-index
    SKILL.md for details.
    """
    conv_dir = get_conversations_dir_for_hash(project_hash)
    current = list_session_files(conv_dir)
    if not current:
        return

    rows = con.execute(
        "SELECT session_filename, mtime FROM manifest WHERE project_hash = ?",
        (project_hash,),
    ).fetchall()
    tracked = {r[0]: r[1] for r in rows}

    n_new = sum(1 for f in current if f not in tracked)
    n_stale = sum(1 for f, m in current.items() if f in tracked and tracked[f] != m)
    behind = n_new + n_stale
    if behind > 0:
        print(
            f"warn: index is {behind} sessions behind "
            f"({n_new} new, {n_stale} modified). Run `{command('trace index')}` to refresh.",
            file=sys.stderr,
        )


def _latest(
    filters: QueryFilters, con: sqlite3.Connection, chronological: bool
) -> list[InteractionRecord]:
    """The newest ``filters.limit`` matches — in reading order when ``chronological``."""
    where, params = _build_where(filters, con)
    sql = f"""
        SELECT {_rec_cols(con)}
        FROM interactions i
        WHERE {where}
        ORDER BY i.timestamp DESC, i.id DESC
    """
    if filters.limit is not None:
        sql += " LIMIT ?"
        params.append(filters.limit)
    records = [_row_to_record(row, con) for row in con.execute(sql, params).fetchall()]
    return records[::-1] if chronological else records


def query_interactions(
    filters: QueryFilters, con: sqlite3.Connection | None = None
) -> list[InteractionRecord]:
    """Query interactions using SQL filters: the newest ``limit`` of them.

    Across sessions they list newest first; inside one session (``session_id``) they
    list in turn order, the way the session happened — the same order as the dialogue.
    """
    return _latest(filters, _open_ro(con), chronological=bool(filters.session_id))


def query_dialogue(
    filters: QueryFilters, con: sqlite3.Connection | None = None
) -> list[InteractionRecord]:
    """Query interactions for dialogue display: the newest ``limit`` turns, oldest first.

    The newest, not the first: a recap of a 100-turn session capped at 30 used to stop
    at turn 29 and never reach the answer it ended on.
    """
    return _latest(filters, _open_ro(con), chronological=True)


def resolve_assistant_preview(
    record: InteractionRecord,
    con: sqlite3.Connection | None = None,
) -> str:
    """Return assistant reply preview, lazily seeking JSONL when the index is stale."""
    from .parser import ASSISTANT_PREVIEW_CAP

    if record.assistant_reply_preview:
        return record.assistant_reply_preview
    return _seek_last_assistant_text(_open_ro(con), record.id, ASSISTANT_PREVIEW_CAP)


def resolve_assistant_full(
    record: InteractionRecord, con: sqlite3.Connection | None = None
) -> str:
    """The whole final answer of an interaction, read from the source JSONL.

    The index keeps ``ASSISTANT_PREVIEW_CAP`` characters of it — a listing's
    worth. A recap wants the answer itself (its conclusion, its table, its
    last line), which only the transcript still has. Falls back to the stored
    preview when the transcript is gone or the offset has drifted.
    """
    text = _seek_last_assistant_text(_open_ro(con), record.id, None)
    return text or record.assistant_reply_preview


def _seek_last_assistant_text(
    con: sqlite3.Connection, interaction_id: str, cap: int | None
) -> str:
    from .parser import extract_last_assistant_text

    source = _interaction_source(con, interaction_id)
    if source is None:
        return ""
    messages, drift = read_raw_segment(*source)
    if drift:
        return ""
    return extract_last_assistant_text(messages, cap=cap)


_ENDED = {
    "interrupted": "interrupted by the user",
    "tool": "stopped",
    "empty": "no output",
}


def describe_ending(ending: str, steps: list | None = None) -> str:
    """``interrupted:6`` → "interrupted by the user after step 6 (Bash)"; "" when answered."""
    kind, _, n = (ending or "").partition(":")
    if kind not in _ENDED:
        return ""
    if kind == "empty":
        return "no output"
    count = int(n or 0)
    if count == 0:
        return f"{_ENDED[kind]} before any step"
    tool = ""
    if steps and len(steps) >= count:
        tool = f" ({short_tool(steps[count - 1].tool)})"
    return f"{_ENDED[kind]} after step {count}{tool}"


def format_dialogue(
    records: list[InteractionRecord],
    filters: QueryFilters,
    con: sqlite3.Connection | None = None,
    full: str = "last",
) -> str:
    """Format prompt+answer pairs for a session transcript.

    ``full`` names which answers are expanded from the stored preview to the
    whole final text: ``"last"`` (the default — the answer a recap is really
    after), ``"all"``, or ``"none"``. A previewed answer that hit the cap ends
    in ``" […]"``, so a cut never passes for a short reply.

    A turn with no closing text says how it ended (interrupted, or stopped after
    step N) and the last thing the model said or thought. Local commands the
    user ran inside a turn (/export, /copy) print as one line under it.
    """
    from .parser import ASSISTANT_PREVIEW_CAP

    if not records:
        filter_desc = []
        if filters.session_id:
            filter_desc.append(f"session={filters.session_id}")
        if filters.since:
            filter_desc.append(f"since={filters.since}")
        if filters.project:
            filter_desc.append(f"project={filters.project}")
        return f"No interactions matched. Filters: {', '.join(filter_desc) or 'none'}"

    con = _open_ro(con)
    dates = [r.timestamp[:10] for r in records if r.timestamp]
    date_range = f"{min(dates)} to {max(dates)}" if dates else "?"

    head = f"{len(records)} turn{'s' if len(records) != 1 else ''} ({date_range})"
    total = count_interactions(dataclasses.replace(filters, limit=None), con=con)
    if total > len(records):
        head += (
            f" — the last {len(records)} of {total}; "
            f"{hint('limit', n=total)} for all"
        )
    lines = [head, ""]

    for i, r in enumerate(records):
        seq = r.id.rsplit("-", 1)[-1]
        ts = r.timestamp[:19] if r.timestamp else "?"
        steps = sum(t.count for t in r.tools_called)
        meta = [ts]
        if steps:
            meta.append(f"{steps} step{'s' if steps != 1 else ''}")
        dur = _fmt_duration(r.duration_seconds)
        if dur:
            meta.append(dur)
        user = r.user_message_preview or "(no user message)"

        ending = r.ending
        assistant = ""
        if ending in ("", "answered"):
            whole = full == "all" or (full == "last" and i == len(records) - 1)
            if whole:
                assistant = resolve_assistant_full(r, con=con)
            else:
                assistant = resolve_assistant_preview(r, con=con)
                if len(assistant) >= ASSISTANT_PREVIEW_CAP:
                    assistant += " […]"
        if not assistant:
            assistant = _no_final_text(r, con)

        lines.append(f"[{seq}] {' · '.join(meta)}")
        lines.append(f"USER: {user}")
        lines.append(f"ASST: {assistant}")
        for lc in r.local_commands:
            lines.append(f"  {lc}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# replay --role user: what the user typed, and nothing else
# ---------------------------------------------------------------------------
# Asked to write present's backstory (163e6deb), an agent needed the user's own words from
# the session that built it. The dialogue gave the newest 30 of 44 turns (the origin was in
# the first 12), each prompt cut at 200 characters, 17 of them notices and teammate reports.
# It went around the tracer to jq on the transcript and got the same noise, rawer.

# A re-send opens with the same words as the turn it replaced, often edited past them
# ("…for CSV? or Parquet?" → "…for CSV? or Parquet (even better choice?)"): it shares
# the replaced prompt's first 40 characters, or all of a shorter one.
_FOLD_AT = 40
_CUT = 200  # what the index keeps of a prompt (parser.extract_user_text)


def _norm(text: str) -> str:
    return " ".join(text.split())


def _user_turn(r: InteractionRecord, con: sqlite3.Connection) -> tuple[str, str, list[tuple[str, str]]]:
    """(who sent the turn, what the user typed — whole, prompts typed while the model worked).

    Read from the transcript: the index keeps 200 characters of a prompt. A transcript that
    is gone leaves the index's cut, marked.
    """
    source = _interaction_source(con, r.id)
    messages, drift = read_raw_segment(*source) if source else ([], "no source")
    if drift or not messages:
        text = r.user_message_preview
        who = "task" if text.startswith("task notification: ") else sender(text)
        return who, text + (" […]" if len(text) >= _CUT else ""), []
    first = messages[0]
    content = (first.get("message") or {}).get("content") if isinstance(first.get("message"), dict) else None
    raw = content if isinstance(content, str) else "\n".join(
        b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
    words = said(first)
    if not words and isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "image" for b in content):
        words = "(an image, no words)"
    typed_while = []
    for m in messages[1:]:
        a = m.get("attachment") if m.get("type") == "attachment" else None
        if isinstance(a, dict) and a.get("type") == "queued_command" and isinstance(a.get("prompt"), str) \
                and sender(a["prompt"]) == "user":
            typed_while.append(((m.get("timestamp") or "")[11:19], prompt_text(a["prompt"])))
    return sender(raw), words, typed_while


def format_user_prompts(
    records: list[InteractionRecord],
    filters: QueryFilters,
    con: sqlite3.Connection | None = None,
) -> str:
    """The user's prompts in a session, oldest first, each whole.

    Left out, and counted: turns the user did not type (a background task's notice, a
    teammate's message). A re-send — a turn that got no answer or was interrupted, then
    sent again opening with the same words — folds into the copy that was answered, which
    says how many times it went. A prompt typed while the model worked prints under the
    turn it landed in.
    """
    if not records:
        return format_dialogue(records, filters, con=con)
    con = _open_ro(con)
    sid = records[0].session_id
    turns = [(r, *_user_turn(r, con)) for r in records]

    kept: list[tuple[InteractionRecord, str, list, int]] = []
    left: dict[str, int] = {"teammate": 0, "task": 0}
    folded, carry, carry_while = 0, 0, []
    for i, (r, who, words, typed_while) in enumerate(turns):
        if who != "user":
            left[who] += 1
            continue
        nxt = turns[i + 1] if i + 1 < len(turns) else None
        unanswered = (r.ending or "").partition(":")[0] in ("empty", "interrupted")
        head = _norm(words)[:_FOLD_AT]
        if nxt and nxt[1] == "user" and unanswered and head and _norm(nxt[2])[:len(head)] == head:
            folded += 1
            carry += 1
            carry_while += typed_while
            continue
        kept.append((r, words, carry_while + typed_while, carry + 1))
        carry, carry_while = 0, []

    dates = [r.timestamp[:10] for r in records if r.timestamp]
    total = count_interactions(dataclasses.replace(filters, limit=None), con=con)
    head = f"{sid} · {len(kept)} prompt{'s' if len(kept) != 1 else ''} the user typed, in {len(records)} turns"
    head += f" ({min(dates)} to {max(dates)})" if dates else ""
    if total > len(records):
        head += f" — the last {len(records)} of {total}; {hint('limit', n=total)} for all"
    lines = [head]
    gone = [f"{left['teammate']} teammate message(s)" if left["teammate"] else "",
            f"{left['task']} task notice(s)" if left["task"] else "",
            f"{folded} re-send(s), folded into the copy that was answered" if folded else ""]
    if any(gone):
        lines.append("left out: " + " · ".join(g for g in gone if g))
    lines.append("")

    for r, words, typed_while, copies in kept:
        seq = r.id.rsplit("-", 1)[-1]
        when = r.timestamp[:19].replace("T", " ") if r.timestamp else "?"
        lines.append(f"[{seq}] {when}" + (f" · sent {copies}×" if copies > 1 else ""))
        lines += [f"  {ln}" if ln.strip() else "" for ln in (words or "(empty)").splitlines()]
        for at, text in typed_while:
            lines.append(f"  typed while it worked{f' ({at})' if at else ''}:")
            lines += [f"    {ln}" if ln.strip() else "" for ln in text.splitlines()]
        lines.append("")

    if kept:
        first = kept[0][0].id
        lines.append(f"next: {hint('drill', id=first)} (what one turn did) · "
                     f"{verb('replay')} {sid} (the answers too)")
    return "\n".join(lines).rstrip() + "\n"


def _no_final_text(r: InteractionRecord, con: sqlite3.Connection) -> str:
    """What a turn without a closing answer shows instead: how it ended, and the last
    thing the model said or thought (read from the transcript — the index keeps no
    thinking)."""
    from .drill import DrillError, load_turn

    try:
        turn = load_turn(r.id, con=con, with_lines=False).turn
    except (DrillError, OSError):
        turn = None
    ending = (turn.ending if turn else r.ending) or "empty"
    how = describe_ending(ending, turn.steps if turn else None) or "no closing text"
    kind, body = turn.last_words if turn else ("text", r.assistant_reply_preview)
    said = ""
    if body:
        label = "last thinking" if kind == "thinking" else "last said"
        said = f" {label}: {one_line(body, 300)}"
    return f"— no final text; {how}.{said}"


def count_interactions(
    filters: QueryFilters, con: sqlite3.Connection | None = None
) -> int:
    """Count interactions matching filters."""
    con = _open_ro(con)
    where, params = _build_where(filters, con)
    sql = f"SELECT count(*) FROM interactions i WHERE {where}"
    return con.execute(sql, params).fetchone()[0]


def aggregate_stats(
    filters: QueryFilters, con: sqlite3.Connection | None = None
) -> dict:
    """Compute aggregation statistics via SQL."""
    con = _open_ro(con)
    where, params = _build_where(filters, con)

    total = con.execute(
        f"SELECT count(*) FROM interactions i WHERE {where}", params
    ).fetchone()[0]

    if total == 0:
        return {
            "total_interactions": 0,
            "error_count": 0,
            "error_rate": "0%",
            "total_tokens_estimated": 0,
            "plugins": {},
            "agent_types": {},
            "tools": {},
        }

    error_count = con.execute(
        f"""SELECT count(DISTINCT i.id) FROM interactions i
            WHERE {where} AND EXISTS (SELECT 1 FROM i_errors ie WHERE ie.iid = i.id)""",
        params,
    ).fetchone()[0]

    total_tokens = con.execute(
        f"SELECT COALESCE(SUM(i.tokens_estimated), 0) FROM interactions i WHERE {where}",
        params,
    ).fetchone()[0]

    plugin_rows = con.execute(
        f"""SELECT ip.plugin, count(DISTINCT i.id)
            FROM interactions i JOIN i_plugins ip ON ip.iid = i.id
            WHERE {where}
            GROUP BY ip.plugin ORDER BY 2 DESC LIMIT 20""",
        params,
    ).fetchall()

    agent_rows = con.execute(
        f"""SELECT ia.type, count(DISTINCT i.id)
            FROM interactions i JOIN i_agents ia ON ia.iid = i.id
            WHERE {where}
            GROUP BY ia.type ORDER BY 2 DESC LIMIT 20""",
        params,
    ).fetchall()

    tool_rows = con.execute(
        f"""SELECT it.name, SUM(it.count)
            FROM interactions i JOIN i_tools it ON it.iid = i.id
            WHERE {where}
            GROUP BY it.name ORDER BY 2 DESC LIMIT 20""",
        params,
    ).fetchall()

    return {
        "total_interactions": total,
        "error_count": error_count,
        "error_rate": f"{error_count / total * 100:.1f}%" if total else "0%",
        "total_tokens_estimated": total_tokens,
        "plugins": dict(plugin_rows),
        "agent_types": dict(agent_rows),
        "tools": {r[0]: int(r[1]) for r in tool_rows},
    }


def list_projects(con: sqlite3.Connection | None = None) -> list[dict]:
    """List all indexed projects with summary stats."""
    con = _open_ro(con)
    rows = con.execute("""
        SELECT pm.project_hash, pm.project_path, pm.last_run,
               count(DISTINCT i.id) as interactions,
               count(DISTINCT i.session_id) as sessions,
               MIN(i.timestamp) as first_ts,
               MAX(i.timestamp) as last_ts,
               COALESCE(SUM(i.tokens_estimated), 0) as total_tokens
        FROM project_meta pm
        LEFT JOIN interactions i ON i.project_hash = pm.project_hash
        GROUP BY pm.project_hash, pm.project_path, pm.last_run
        ORDER BY last_ts DESC
    """).fetchall()

    return [
        {
            "project_hash": r[0],
            "project_path": r[1],
            "last_run": r[2],
            "interactions": r[3],
            "sessions": r[4],
            "first_ts": r[5],
            "last_ts": r[6],
            "total_tokens": r[7],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Drill-down via byte-offset seek
# ---------------------------------------------------------------------------


def _interaction_source(
    con: sqlite3.Connection, interaction_id: str
) -> tuple[str, int] | None:
    """(session_file, byte_offset) for an interaction, or None if no such row.

    Backfills the path for an index that predates session_file storage.
    """
    row = con.execute(
        "SELECT session_id, project_hash, session_file, byte_offset "
        "FROM interactions WHERE id = ?",
        (interaction_id,),
    ).fetchone()
    if not row:
        return None
    session_id, project_hash, session_file, byte_offset = row
    if not session_file:
        session_file = str(get_conversations_dir_for_hash(project_hash) / f"{session_id}.jsonl")
    return session_file, byte_offset or 0


def read_raw_segment(session_file: str, byte_offset: int) -> tuple[list[dict], str | None]:
    """The raw JSONL messages of one interaction: seek to its first byte, read
    until the next turn opens. Returns ``(messages, drift)`` — when
    ``drift`` is set the source could not be read and ``messages`` is empty.
    """
    splitter = TurnSplitter()

    source_path = Path(session_file)
    if not source_path.exists():
        return [], f"source JSONL not available: {session_file}"

    try:
        with open(source_path, "rb") as f:
            f.seek(byte_offset)
            first = f.readline()
            if not first or not first.lstrip().startswith(b"{"):
                return [], (
                    f"byte offset {byte_offset} in {source_path.name} does not start "
                    f"with a JSON object; run `{command('trace index')}` to refresh"
                )
            collected_lines: list[bytes] = [first]
            try:
                splitter.kind(json.loads(first.decode("utf-8", errors="replace")))
            except json.JSONDecodeError:
                pass
            while True:
                line = f.readline()
                if not line:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    msg = json.loads(stripped.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                # Stop where the next turn opens (turns.TurnSplitter)
                if splitter.opens_turn(msg):
                    break
                collected_lines.append(line)
    except OSError as exc:
        return [], f"failed reading {source_path}: {exc}"

    parsed: list[dict] = []
    for raw in collected_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            parsed.append(json.loads(stripped.decode("utf-8", errors="replace")))
        except json.JSONDecodeError:
            continue
    return parsed, None


# ---------------------------------------------------------------------------
# Formatting (unchanged interface)
# ---------------------------------------------------------------------------


def _fmt_duration(seconds: int) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s" if m else f"{s}s"


def _short_path(path: str) -> str:
    home = str(Path.home())
    return path.replace(home, "~", 1) if path.startswith(home) else path


def format_record_short(r: InteractionRecord, detail: bool = False) -> str:
    """Format a single record as a compact summary line.

    ``detail`` adds the lines a session recap keeps going back to the raw
    transcript for: which files were written or read, what the errors were,
    how long the turn took. The index has held all three since day one; the
    listing just never showed them.
    """
    ts = r.timestamp[:19] if r.timestamp else "?"
    plugins = ",".join(r.plugins) if r.plugins else "-"
    tools = ",".join(f"{short_tool(t.name)}({t.count})" for t in r.tools_called[:5])
    agents = ",".join(a.type for a in r.agents_spawned[:3])
    errors = f" ERR:{len(r.error_signals)}" if r.error_signals else ""
    preview = " ".join(r.user_message_preview.split())[:80] if r.user_message_preview else ""  # one line

    parts = [f"[{ts}]", f"id={r.id}", f"plugin={plugins}"]
    if agents:
        parts.append(f"agents=[{agents}]")
    if tools:
        parts.append(f"tools=[{tools}]")
    parts.append(f"~{r.tokens_estimated}tok")
    dur = _fmt_duration(r.duration_seconds)
    if dur:
        parts.append(f"dur={dur}")
    kind, _, n = (r.ending or "").partition(":")
    if kind in ("interrupted", "tool"):
        parts.append(f"end={kind}@{n}")
    elif kind == "empty":
        parts.append("end=empty")
    if errors:
        parts.append(errors)
    line = " ".join(parts)
    if preview:
        line += f"\n  > {preview}"
    if not detail:
        return line

    if r.files_touched:
        by_path: dict[str, dict[str, int]] = {}
        for f in r.files_touched:
            by_path.setdefault(f.path, {})
            by_path[f.path][f.operation] = by_path[f.path].get(f.operation, 0) + 1
        # writes first — they are what a recap is usually after
        rank = {"write": 0, "edit": 1, "read": 2}
        ordered = sorted(by_path.items(), key=lambda kv: min(rank.get(op, 3) for op in kv[1]))
        shown = []
        for path, ops in ordered[:6]:
            ops_s = ", ".join(f"{op}×{n}" if n > 1 else op for op, n in sorted(ops.items(), key=lambda x: rank.get(x[0], 3)))
            shown.append(f"{_short_path(path)} ({ops_s})")
        more = f"; +{len(ordered) - 6} more" if len(ordered) > 6 else ""
        line += "\n  files: " + "; ".join(shown) + more
    for lc in r.local_commands:
        line += f"\n  local: {lc}"
    for e in r.error_signals[:3]:
        preview_e = " ".join((e.preview or "").split())[:110]
        line += f"\n  err[{e.type}]: {preview_e}"
    if len(r.error_signals) > 3:
        line += f"\n  … +{len(r.error_signals) - 3} more errors"
    return line


def format_results(
    records: list[InteractionRecord],
    filters: QueryFilters,
    total_unfiltered: int,
    detail: bool | None = None,
    matched: int | None = None,
) -> str:
    """Format query results with summary + records.

    ``detail`` (files, error previews) defaults to on when the query is scoped
    to one session — a recap — and off for cross-session listings. ``matched``
    is the uncapped match count, when the caller knows it.
    """
    if detail is None:
        detail = bool(filters.session_id)
    if not records:
        # Every field of QueryFilters, so an empty result can never claim
        # "Filters: none" while a session filter was in fact applied.
        filter_desc = [
            f"{label}={value}"
            for label, value in (
                ("session", filters.session_id),
                ("project", filters.project or (f"{len(filters.projects)} folder(s)" if filters.projects else "")),
                ("plugin", filters.plugin),
                ("agent", filters.agent_type),
                ("skill", filters.skill_name),
                ("tool", filters.tool_name),
                ("branch", filters.git_branch),
                ("since", filters.since),
                ("until", filters.until),
            )
            if value
        ]
        if filters.has_errors:
            filter_desc.append("has_errors=true")
        return f"No interactions matched. Filters: {', '.join(filter_desc) or 'none'}"

    error_count = sum(1 for r in records if r.error_signals)
    dates = [r.timestamp[:10] for r in records if r.timestamp]
    date_range = f"{min(dates)} to {max(dates)}" if dates else "?"
    matched = max(matched or 0, len(records))
    order = "in turn order" if filters.session_id else "newest first"

    lines = [
        f"{matched} interaction{'s' if matched != 1 else ''} matched ({date_range}, {error_count} shown with errors)",
        f"  from {total_unfiltered} total indexed interactions",
        "",
    ]

    for r in records:
        lines.append(format_record_short(r, detail=detail))
        lines.append("")

    if matched > len(records):
        lines.append(
            f"showing the newest {len(records)} of {matched}, {order}; "
            f"{hint('limit', n=matched)} for all"
        )
    if not filters.session_id:  # across sessions: open the newest match, or the session it is in
        first = records[0].id
        lines.append(f"next: {hint('drill', id=first)} for its steps · {hint('session', id=first)} for its session")

    return "\n".join(lines).rstrip() + "\n"


def format_stats(stats: dict) -> str:
    """Format aggregation statistics."""
    lines = [
        f"Total interactions: {stats['total_interactions']}",
        f"Errors: {stats['error_count']} ({stats['error_rate']})",
        f"Estimated tokens: {stats['total_tokens_estimated']:,}",
        "",
        "Plugins:",
    ]
    for name, count in stats["plugins"].items():
        lines.append(f"  {name}: {count}")

    lines.append("")
    lines.append("Agent types:")
    for name, count in stats["agent_types"].items():
        lines.append(f"  {name}: {count}")

    lines.append("")
    lines.append("Tools:")
    for name, count in stats["tools"].items():
        lines.append(f"  {name}: {count}")

    return "\n".join(lines)
