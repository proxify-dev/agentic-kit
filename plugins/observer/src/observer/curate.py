"""The curator's three store verbs, and the rule that hides a row from recall.

The store's write verbs and its hidden-row rule — same
sqlite, same columns (hooks/store_common.py OUR_COLUMNS already carries `until`,
`folds`, `folded_into`, `forgotten_at`, `forget_reason`), same ledger file, so any
reader of the ledger shows a terminal's folds beside other writers'. Nothing here imports another
plugin; the store helpers are this plugin's hooks/.

    patterns   READ   rows that keep coming up — ≥ min rows sharing an anchor across ≥ 2 sessions
    fold       WRITE  N rows → one summary row that stands for them (lossless: `undo` restores)
    forget     WRITE  hide rows for a reason (never deletes; `undo` restores). Preview by
                      query or by ids is the default; the write needs `confirm=True`.

Every write is one `BEGIN IMMEDIATE` transaction and one ledger line. Refusals raise
`Refused` with the reason — `observer patterns · fold · forget` (cli.py) print it as the error.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
import store_common  # noqa: E402

HIDDEN_VERDICT = {"superseded"}
LEDGER_NAME = "objects-ledger.jsonl"  # the shared ledger file other writers append to


class Refused(Exception):
    """A write the store will not make, with the reason a person needs."""


# ── time · ledger ─────────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.fromtimestamp(store_common.brain_now(), tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ledger_path() -> Path:
    return Path(store_common.db_dir()) / LEDGER_NAME


def ledger_append(op: str, stem: str, actor: str, outcome: str = "written", reason: str = "") -> None:
    """One line, the ledger line shape. The ledger is
    evidence, never a gate: losing a line must not lose a write."""
    line = {"at": now_iso(), "op": op, "kind": "observation", "stem": stem, "path": "",
            "actor": actor, "policy": "write", "outcome": outcome}
    if reason:
        line["reason"] = reason
    try:
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── the hidden-row rule (memory.ts hiddenWhy, priority order) ─────────────────
def supersedes_map(conn: sqlite3.Connection) -> dict:
    """parent id → the newest live child that says `supersedes: obs:<parent>`.
    A child that is itself dead (verdict dead-end / superseded) does not bury its parent."""
    out: dict = {}
    if "supersedes" not in store_common.columns(conn, "observations"):
        return out
    rows = conn.execute(
        "SELECT id, supersedes, verdict, created_at_epoch FROM observations "
        "WHERE supersedes LIKE 'obs:%' ORDER BY created_at_epoch ASC").fetchall()
    for r in rows:
        if (r["verdict"] or "") in ("dead-end", "superseded"):
            continue
        for tok in str(r["supersedes"]).split(","):
            tok = tok.strip()
            if tok.startswith("obs:") and tok[4:].isdigit():
                out[int(tok[4:])] = int(r["id"])
    return out


def hidden_why(row, today: str, sup: dict) -> str | None:
    keys = row.keys()
    if "forgotten_at" in keys and row["forgotten_at"]:
        return "forgotten"
    if "folded_into" in keys and row["folded_into"]:
        return "folded"
    if "verdict" in keys and (row["verdict"] or "") in HIDDEN_VERDICT:
        return "superseded"
    if int(row["id"]) in sup:
        return "superseded"
    if "until" in keys and row["until"] and str(row["until"])[:10] < today:
        return "expired"
    return None


def hidden_where() -> str:
    """The SQL half of the rule, for a WHERE clause — everything but the supersedes
    join, which `hidden_why` finishes in Python. Safe on a store older than pass 3:
    the columns are ALTERed in by ensure_schema before any read that reaches here."""
    return ("(o.forgotten_at IS NULL OR o.forgotten_at = '') AND o.folded_into IS NULL "
            "AND (o.verdict IS NULL OR o.verdict <> 'superseded') "
            "AND (o.until IS NULL OR o.until = '' OR substr(o.until, 1, 10) >= ?)")


def today() -> str:
    return datetime.fromtimestamp(store_common.brain_now()).strftime("%Y-%m-%d")


# ── patterns ──────────────────────────────────────────────────────────────────
def _jlist(v) -> list:
    try:
        x = json.loads(v) if v else []
        return [str(i) for i in x] if isinstance(x, list) else []
    except Exception:
        return []


def patterns(project: str | None = None, min: int = 3, since_days: int | None = None) -> dict:
    """Clusters of live rows sharing an anchor (o.anchors, else o.concepts; lowercased,
    longer than two characters) with ≥ `min` rows across ≥ 2 sessions. Size desc, anchor asc.
    A cluster's `sessions` are Claude Code session uuids (sdk_sessions.content_session_id,
    what tracer takes); `unlinked` counts its sessions the store has no uuid for."""
    min = max(2, int(min or 3))
    conn = store_common.connect(readonly=True)
    try:
        cols = set(store_common.columns(conn, "observations"))  # a pre-pass-3 store simply has no hidden rows
        since_epoch = 0
        if since_days:
            since_epoch = int((store_common.brain_now() - int(since_days) * 86400) * 1000)
        where = ["o.created_at_epoch >= ?"]
        params: list = [since_epoch]
        if project:
            where.append("o.project = ?")
            params.append(project)
        rows = conn.execute(
            f"SELECT o.* FROM observations o WHERE {' AND '.join(where)} "
            "ORDER BY o.created_at_epoch DESC LIMIT 5000", params).fetchall()
        sup = supersedes_map(conn)
        uuid = {m: c for m, c in conn.execute(
            "SELECT memory_session_id, content_session_id FROM sdk_sessions WHERE content_session_id != ''")}
    finally:
        conn.close()
    t = today()
    groups: dict = {}
    for r in rows:
        if hidden_why(r, t, sup):
            continue
        anchors = _jlist(r["anchors"]) if "anchors" in cols else []
        if not anchors:
            anchors = _jlist(r["concepts"])
        for a in {a.strip().lower() for a in anchors if len(a.strip()) > 2}:
            g = groups.setdefault(a, {"anchor": a, "ids": [], "sessions": set(), "titles": []})
            g["ids"].append(int(r["id"]))
            g["sessions"].add(r["memory_session_id"] or "")
            g["titles"].append(r["title"] or "")
    clusters = []
    for g in groups.values():
        mids = {s for s in g["sessions"] if s}
        if len(g["ids"]) >= min and len(mids) >= 2:
            c = {"anchor": g["anchor"], "ids": g["ids"], "sessions": sorted(uuid[m] for m in mids if m in uuid),
                 "titles": g["titles"]}
            if len(c["sessions"]) < len(mids):
                c["unlinked"] = len(mids) - len(c["sessions"])
            clusters.append(c)
    clusters.sort(key=lambda c: (-len(c["ids"]), c["anchor"]))
    return {"kind": "patterns", "clusters": clusters}


# ── fold · unfold ─────────────────────────────────────────────────────────────
def _rows_by_id(conn, ids: list) -> dict:
    marks = ",".join("?" for _ in ids)
    return {int(r["id"]): r for r in conn.execute(
        f"SELECT id, title, verdict, folded_into, forgotten_at, memory_session_id, project, folds "
        f"FROM observations WHERE id IN ({marks})", ids).fetchall()}


def fold(ids: list | None = None, title: str = "", facts: list | None = None,
         narrative: str | None = None, undo: int | None = None, actor: str = "agent:terminal") -> dict:
    if undo is not None:
        return unfold(int(undo), actor)
    ids = [int(i) for i in (ids or [])]
    if len(ids) < 2:
        raise Refused("fold needs at least two ids")
    if not (title or "").strip():
        raise Refused("fold needs a title — the one line the summary shows in their place")
    facts = [str(f) for f in (facts or []) if str(f).strip()]
    if not facts:
        raise Refused("fold needs facts — what the folded rows said, so nothing they knew leaves the pack")
    conn = store_common.connect()
    try:
        store_common.ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        have = _rows_by_id(conn, ids)
        missing = [i for i in ids if i not in have]
        if missing:
            raise Refused(f"no observation with id {', '.join(map(str, missing))}")
        already = [i for i in ids if have[i]["folded_into"]]
        if already:
            raise Refused(f"obs:{already[0]} is already folded into obs:{have[already[0]]['folded_into']} — unfold that first")
        projects = {have[i]["project"] for i in ids}
        if len(projects) > 1:
            raise Refused(f"the rows span {len(projects)} projects ({', '.join(sorted(p or '' for p in projects))}); fold within one")
        at = now_iso()
        epoch = int(store_common.brain_now() * 1000)
        session = have[ids[0]]["memory_session_id"]
        cur = conn.execute(
            "INSERT INTO observations (memory_session_id, project, type, title, subtitle, facts, narrative, "
            "concepts, files_read, files_modified, created_at, created_at_epoch, generated_by_model, verdict, folds) "
            "VALUES (?, ?, 'observation', ?, ?, ?, ?, '[]', '[]', '[]', ?, ?, 'brain/fold', 'neutral', ?)",  # brand: historical (the model label the observer's rows carry)
            (session, next(iter(projects)), title.strip(), f"folds {len(ids)}", json.dumps(facts, ensure_ascii=False),
             narrative or "", at, epoch, json.dumps(ids)))
        summary_id = int(cur.lastrowid)
        marks = ",".join("?" for _ in ids)
        conn.execute(f"UPDATE observations SET folded_into = ? WHERE id IN ({marks})", [summary_id, *ids])
        conn.commit()
    except Refused:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    ledger_append("fold", f"memdb:{summary_id}", actor)
    return {"kind": "folded", "summaryId": summary_id, "folded": ids, "title": title.strip()}


def unfold(summary_id: int, actor: str = "agent:terminal") -> dict:
    conn = store_common.connect()
    try:
        store_common.ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute("SELECT id, folds FROM observations WHERE id = ?", (summary_id,)).fetchone()
        folds = [int(i) for i in _jlist(r["folds"])] if r else []
        if not folds:
            raise Refused(f"obs:{summary_id} is not a summary row (no `folds`)")
        conn.execute("UPDATE observations SET folded_into = NULL WHERE folded_into = ?", (summary_id,))
        conn.execute("DELETE FROM observations WHERE id = ?", (summary_id,))
        conn.commit()
    except Refused:
        conn.rollback()
        raise
    finally:
        conn.close()
    ledger_append("unfold", f"memdb:{summary_id}", actor)
    return {"kind": "unfolded", "summaryId": summary_id, "restored": folds}


# ── forget · unforget ─────────────────────────────────────────────────────────
def forget(ids: list | None = None, query: str | None = None, reason: str = "", dry_run: bool | None = None,
           undo: bool = False, limit: int = 20, confirm: bool = False, actor: str = "agent:terminal") -> dict:
    ids = [int(i) for i in (ids or [])]
    limit = max(1, min(int(limit or 20), 100))
    dry = dry_run if dry_run is not None else not ids
    if dry or not confirm:
        if query and not ids:
            from observer.obs_index import BACKEND
            hits = BACKEND.search(query=query, limit=limit)
            # a store-less vault searches markdown, whose ids are paths: nothing there to forget
            cands = [{"id": int(h["id"]), "title": h["title"], "score": round(1 - i / limit, 2)}
                     for i, h in enumerate(hits) if str(h["id"]).isdigit()]
            return {"kind": "forget-preview", "query": query, "candidates": cands}
        conn = store_common.connect(readonly=True)
        try:
            have = _rows_by_id(conn, ids) if ids else {}
        finally:
            conn.close()
        cands = [{"id": i, "title": have[i]["title"], "score": 1} for i in ids if i in have]
        note = "" if dry else " — a preview until confirmed"
        return {"kind": "forget-preview", "query": f"ids {','.join(map(str, ids))}{note}", "candidates": cands}
    conn = store_common.connect()
    try:
        store_common.ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        have = _rows_by_id(conn, ids)
        missing = [i for i in ids if i not in have]
        if missing:
            raise Refused(f"no observation with id {', '.join(map(str, missing))}")
        marks = ",".join("?" for _ in ids)
        if undo:
            conn.execute(f"UPDATE observations SET forgotten_at = NULL, forget_reason = NULL WHERE id IN ({marks})", ids)
            conn.commit()
            ledger_append("unforget", f"memdb:{ids[0]}", actor)
            return {"kind": "unforgotten", "ids": ids}
        if not (reason or "").strip():
            raise Refused("forget needs a reason — the row keeps saying it after it is hidden")
        at = now_iso()
        conn.execute(f"UPDATE observations SET forgotten_at = ?, forget_reason = ? WHERE id IN ({marks})",
                     [at, reason.strip(), *ids])
        conn.commit()
    except Refused:
        conn.rollback()
        raise
    finally:
        conn.close()
    ledger_append("forget", f"memdb:{ids[0]}", actor, reason=reason.strip())
    return {"kind": "forgotten", "ids": ids, "reason": reason.strip()}


def actor_name() -> str:
    """Who is folding: the session, when a hook registered one, else a bare terminal."""
    return f"agent:terminal:{os.environ.get('CLAUDE_SESSION_ID', '')}".rstrip(":") if os.environ.get("CLAUDE_SESSION_ID") else "agent:terminal"
