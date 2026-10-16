#!/usr/bin/env python3
# Derived from Claude-Mem v13.11.0 (Apache-2.0, Copyright 2026 Alex Newman) and modified: the digest's text and layout, ported to Python. See NOTICE.
"""observer session-start: one line naming the doors, and the "since you were last here" block for the person.

SessionStart hook (startup|resume|clear|compact). The model gets the TEACH line and nothing
else: the observer's rows are raw and unverified, so they are searched on demand (`ak
observer search`), never injected — project memory is the vault's `ak memory` spine
(memory's session-start), which the observer feeds through `ak memory harvest`.
The person still sees what the observer wrote about this place — the bound project's rows
when a project note binds the cwd, else the directory's own label — as the coloured block
claude-mem showed every morning (`systemMessage`, never read by the model). A last-seen
cursor narrows an arrival to what is new and is stamped only after the block has left
the process. A resume gets the TEACH line and no block — the person saw it when the
conversation first opened. It plants nothing: the observer's prompt is the plugin's own agent file.

Fails silent — a broken hook must never block a session.
"""
import atexit
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project_match import resolve_binding, vault_root, parse_frontmatter  # noqa: E402  (a copy; scripts/copies.py)
from capture_common import command, automated_entrypoint, labels_with_aliases, observations_dir, project_label, slugify  # noqa: E402
from pathlib import Path  # noqa: E402
from vault_root import cache_dir, home_root  # noqa: E402
from _brand import DISPLAY_NAME, env_name, posix, replace_retry, utf8_stdio  # noqa: E402
from prompt_text import prompt  # noqa: E402

TEACH = prompt("session-start.observer")
FETCH = f"`{command('get')} <IDs>`"

def project_slug(match: dict, cwd: str = "") -> str:
    """Same key session-register stamps: vault-root note → the label its stored rows carry, else note stem."""
    note = match["note"]
    try:
        if note.parent == Path(home_root()):
            return "brain"  # brand: historical — the project label every stored row of the vault root carries
    except Exception:
        pass
    return slugify(note.stem)


def labels_for(cwd: str, bound: str = "") -> list:
    """Every label under which this directory's memory may be filed: claude-mem's
    (the git-root basename — one label per repo), the bare cwd basename (what a
    session in a directory with no repo got), and the bound project slug — each
    then expanded through its aliases, so a session in the code repo `~/agentic-kit`
    also reads the `.ak` rows the vault wrote before the split."""
    out = []
    for label in (project_label(cwd), os.path.basename((cwd or "").rstrip("/\\")), bound):
        if label and label not in out:
            out.append(label)
    return labels_with_aliases(out) or ["unbound"]


# The block is claude-mem's, line for line: the shape people had read every
# morning for six months is the one to keep. Type glyphs, a stats line built
# from `discovery_tokens` (the work behind each observation, which the observer
# fills), then observations and session summaries interleaved by time and
# grouped by day, oldest first, so the last line is the latest thing that
# happened. `"` repeats the previous line's clock, as it did.
# Glyphs and the legend come from the type table (hooks/memory_schema.py).
import memory_schema as _schema  # noqa: E402
_REG = _schema.registry()
GLYPH = _schema.glyphs(_REG)
LEGEND = _schema.legend(_REG)
LEGEND_WIDE = _schema.legend_wide(_REG)


# How much of the log greets a person: the newest observations, and the sessions around them.
CONTEXT_OBSERVATIONS = 20
CONTEXT_SESSIONS = 10


def _local(epoch_ms):
    from datetime import datetime
    return datetime.fromtimestamp((epoch_ms or 0) / 1000)


def _clock(dt) -> str:
    return f"{dt.hour % 12 or 12}:{dt.minute:02d}{'a' if dt.hour < 12 else 'p'}"


def _day(dt) -> str:
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def _jlist(v) -> list:
    try:
        x = json.loads(v) if v else []
        return [str(i) for i in x] if isinstance(x, list) else []
    except Exception:
        return []


def _squash(s, n: int) -> str:
    return " ".join(str(s or "").split())[:n]


def context_data(labels: list, name: str, n_obs: int, n_sess: int):
    """Everything both renderings need, from the event store — None when this
    machine has no store, {} when the store knows nothing about the place."""
    import store_common
    if not store_common.store_db().exists():
        return None
    marks = ",".join("?" for _ in labels)

    def newest(conn, sql: str, n: int) -> list:
        # One query per label, merged here: `project IN (…) ORDER BY created_at_epoch` sorts every row of
        # every label in a temp b-tree (~27 ms on a 260 MB store), while `project = ?` walks the
        # (project, created_at_epoch DESC) index and stops at n (<1 ms). claude-mem 13.31.0 found the same.
        rows = [dict(r) for label in labels for r in conn.execute(sql, (label, n)).fetchall()]
        return sorted(rows, key=lambda r: r["epoch"] or 0, reverse=True)[:n]

    try:
        conn = store_common.connect(readonly=True)
        total_obs = conn.execute(
            f"SELECT COUNT(*) FROM observations WHERE project IN ({marks})", labels).fetchone()[0]
        total_sess = conn.execute(
            f"SELECT COUNT(*) FROM sdk_sessions WHERE project IN ({marks})", labels).fetchone()[0]
        obs = newest(conn,
            "SELECT id, created_at_epoch AS epoch, type, title, verdict, "
            "COALESCE(discovery_tokens, 0) AS work, files_modified, files_read, "
            "length(COALESCE(title,'') || COALESCE(narrative,'') || COALESCE(facts,'')) AS chars "
            "FROM observations WHERE project = ? ORDER BY created_at_epoch DESC LIMIT ?", n_obs)
        sess = newest(conn,
            "SELECT id, created_at_epoch AS epoch, request FROM session_summaries "
            "WHERE project = ? AND request IS NOT NULL ORDER BY created_at_epoch DESC LIMIT ?", n_sess)
        last = next(iter(newest(conn,
            "SELECT id, created_at_epoch AS epoch, request, investigated, learned, completed, next_steps "
            "FROM session_summaries WHERE project = ? ORDER BY created_at_epoch DESC LIMIT ?", 1)), None)
        conn.close()
    except Exception:
        return {}
    if not total_obs and not total_sess:
        return {}
    for o in obs:
        o["read"] = int(o["chars"] or 0) // 4
        files = _jlist(o.get("files_modified")) or _jlist(o.get("files_read"))
        o["file"] = files[0] if files else ""
    from datetime import datetime
    return {
        "name": name, "now": datetime.now(), "total_obs": total_obs, "total_sess": total_sess,
        "obs": obs, "sess": sess, "last": dict(last) if last else None,
        "read_t": sum(o["read"] for o in obs), "work_t": sum(int(o["work"] or 0) for o in obs),
        "items": sorted([(o["epoch"], "o", o) for o in obs] + [(s["epoch"], "s", s) for s in sess],
                        key=lambda x: x[0] or 0),
    }


def _older_line(d: dict) -> str:
    """The collapsed rows, each kind under the route that actually reaches IT — because
    there is not one route. An observation's id is what `observer get` takes,
    and once the body is gone that id is the only handle the row has left. A session
    summary's `S<id>` is a label this block prints and nothing more: no tool resolves it,
    and the integers under the `S` come from a different table than the observation ids
    beside them — tens of thousands of numbers exist in both — so a model that drops the
    prefix is handed a different record and told nothing went wrong. Sessions therefore
    collapse to a count plus the door that does open on them, `since('<day>')`, which
    lists a window of sessions off the same store. Observation ids keep the block's own
    order, oldest first, so the line runs straight into the rows below it."""
    parts = []
    if d["older_obs"]:
        parts.append(f"{' '.join(d['older_obs'])} · {FETCH} to read them")
    if d["older_sess"]:
        parts.append(f"{d['older_sess']} session{'' if d['older_sess'] == 1 else 's'} "
                     f"not fetchable by id · since('{d['older_sess_from']}') lists them")
    return f"{d['older']:,} older rows in scope: " + "; ".join(parts)


def render_plain(d: dict) -> list:
    """The digest as plain text, claude-mem's additionalContext line for line. No model reads it: the
    model gets TEACH alone (main). It stays as the uncoloured twin of render_ansi that the tests assert on."""
    stats = f"Stats: {len(d['obs'])} obs (~{d['read_t']:,}t read)"
    if d["work_t"] > d["read_t"]:
        stats += f" | {d['work_t']:,}t work | {1 - d['read_t'] / d['work_t']:.0%} savings"
    lines = [f"# [{d['name']}] recent context, {_day(d['now'])} {_clock(d['now'])}"]
    if d.get("since"):
        lines.append(f"since you were last here ({_when(d['since'])}, {len(d['items'])} new)")
    lines += [
        "",
        LEGEND,
        "Format: ID TIME TYPE TITLE · S<id> = what a session was asked",
        f"Fetch details: `{command('search')} <query>` → `{command('timeline')} <id>` → `{command('get')} <IDs>`",
        "",
        stats,
        "",
    ]
    if d.get("older"):
        lines += [_older_line(d), ""]
    day = None
    prev_clock = None
    for epoch, kind, r in d["items"]:
        dt = _local(epoch)
        if _day(dt) != day:
            day = _day(dt)
            prev_clock = None
            lines.append(f"### {day}")
        if kind == "o":
            clock = _clock(dt)
            shown = '"' if clock == prev_clock else clock
            prev_clock = clock
            lines.append(f"{r['id']} {shown} {GLYPH.get(r['type'], '·')} {_squash(r['title'], 140)}"
                         + (" ✗" if r["verdict"] == "dead-end" else ""))
        else:
            lines.append(f"S{r['id']} {_squash(r['request'], 150)} "
                         f"({dt.strftime('%b')} {dt.day} at {dt.strftime('%-I:%M %p')})")
    lines += ["", f"{d['total_obs']:,} observations · {d['total_sess']:,} sessions on record for "
                  f"{d['name']}. Access {d.get('work_all', d['work_t']):,}t of past work via {FETCH}."]
    return lines


# ANSI, because Claude Code renders it in `systemMessage` and that is where the
# colour claude-mem's welcome screen had came from — 236 escapes in one block.
_B, _DIM, _GREY, _CY, _GRN, _YEL, _BLU, _MAG, _R = (
    "\x1b[1m", "\x1b[2m", "\x1b[90m", "\x1b[36m", "\x1b[32m", "\x1b[33m", "\x1b[34m", "\x1b[35m", "\x1b[0m")
_BUILD = {"bugfix", "feature", "refactor", "change", "decision"}  # ⚒ made · ⌕ found


def _short(path: str, cwd: str) -> str:
    path, home = posix(path), posix(os.path.expanduser("~"))  # a Windows path is matched "/"-separated
    for base in (posix(cwd).rstrip("/\\"), home):
        if base and path.startswith(base + "/"):
            return path[len(base) + 1:] if base != home else "~/" + path[len(base) + 1:]
    return path


def render_ansi(d: dict, cwd: str) -> str:
    """What the person sees on the welcome screen: claude-mem's coloured block,
    section for section — header and rule, legend, column key, the help lines,
    Context Economics, days → sessions in yellow and observations grouped by
    the file they touched, the last session's summary in its four colours,
    the footer."""
    saved = max(0, d["work_t"] - d["read_t"])
    pct = f"{saved / d['work_t']:.0%}" if d["work_t"] else "0%"
    out = [
        f"{_B}{_CY}[{d['name']}] recent context, {d['now'].strftime('%Y-%m-%d %-I:%M%p').lower()}{_R}",
        f"{_GREY}{'─' * 60}{_R}",
    ]
    if d.get("since"):
        out.append(f"{_DIM}since you were last here ({_when(d['since'])}, {len(d['items'])} new){_R}")
    out += [
        "",
        f"{_DIM}{LEGEND_WIDE}{_R}",
        "",
        f"{_B}Column Key{_R}",
        f"{_DIM}  Read: Tokens to read this observation (cost to learn it now){_R}",
        f"{_DIM}  Work: Tokens of conversation the observer read to produce this record{_R}",
        "",
        f"{_DIM}Context Index: this index (titles, types, files, tokens) is usually enough to understand past work.{_R}",
        "",
        f"{_DIM}When you need implementation details, rationale, or debugging context:{_R}",
        f"{_DIM}  - Fetch by ID: {command('get')} <IDs>{_R}",
        f"{_DIM}  - Search history: {command('search')} / timeline over the store{_R}",
        f"{_DIM}  - Trust this index over re-reading code for past decisions and learnings{_R}",
        "",
        f"{_B}{_CY}Context Economics{_R}",
        f"{_DIM}  Loading: {len(d['obs'])} observations ({d['read_t']:,} tokens to read){_R}",
        f"{_DIM}  Work investment: {d['work_t']:,} tokens of sessions behind them{_R}",
        f"{_GRN}  Your savings: {saved:,} tokens ({pct} reduction from reuse){_R}",
        "",
    ]
    if d.get("older"):
        out += [f"{_DIM}{_older_line(d)}{_R}", ""]
    day = None
    file = None
    prev_clock = None
    for epoch, kind, r in d["items"]:
        dt = _local(epoch)
        if _day(dt) != day:
            day = _day(dt)
            file = None
            prev_clock = None
            out += [f"{_B}{_CY}{day}{_R}", ""]
        if kind == "s":
            out += [f"{_YEL}#S{r['id']}{_R} {_squash(r['request'], 170)} "
                    f"({dt.strftime('%b')} {dt.day} at {dt.strftime('%-I:%M %p')})", ""]
            file = None
            continue
        group = _short(r["file"], cwd) if r["file"] else "General"
        if group != file:
            file = group
            out.append(f"{_DIM}{group}{_R}")
        clock = dt.strftime("%-I:%M %p")
        when = f"{_DIM}{clock:>8}{_R}" if clock != prev_clock else " " * 8
        prev_clock = clock
        tool = "⚒" if r["type"] in _BUILD else "⌕"
        mark = " ✗" if r["verdict"] == "dead-end" else ""
        out.append(f"  {_DIM}#{r['id']}{_R}  {when}  {GLYPH.get(r['type'], '·')}  {_squash(r['title'], 120)}{mark} "
                   f"{_DIM}(~{r['read']:,}t){_R} {_DIM}({tool} {int(r['work'] or 0):,}t){_R}")
    last = d.get("last")
    if last and last.get("request"):
        dt = _local(last["epoch"])
        out += ["", f"{_YEL}#S{last['id']}{_R} {_squash(last['request'], 300)} "
                    f"({dt.strftime('%b')} {dt.day} at {dt.strftime('%-I:%M %p')})", ""]
        for key, col in (("investigated", _BLU), ("learned", _YEL), ("completed", _GRN), ("next_steps", _MAG)):
            if last.get(key):
                out += [f"{col}{key.replace('_', ' ').title()}:{_R} {_squash(last[key], 400)}", ""]
    out += ["", f"{_DIM}Access {d.get('work_all', d['work_t']) // 1000}k tokens of past research & decisions "
                f"for just {d['read_t']:,}t. {d['total_obs']:,} observations · {d['total_sess']:,} sessions "
                f"on record for {d['name']}.{_R}"]
    return "\n".join(out)


def _recent_from_md(project: str, limit: int) -> list:
    d = observations_dir() / project
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.md"), reverse=True)[:limit]:
        try:
            fm = parse_frontmatter(p.read_text(encoding="utf-8")[:4096]) or {}
        except Exception:
            fm = {}
        out.append(f"  · {fm.get('date', '?')} [{fm.get('obs_type', '?')}] {fm.get('title', p.stem)}")
    return out


# ── the last-seen cursor ─────────────────────────────────────────────────────
# `ORDER BY created_at_epoch DESC LIMIT N` has no memory of who already read the
# rows, so two sessions minutes apart were handed byte-identical blocks. One file
# keyed by place — the bound project slug, else the cwd — remembers the newest row
# this place has been shown; everything at or under that stamp collapses to one line,
# carrying the observations' IDs — which is what `observer get` takes — and a count
# for the sessions, which nothing fetches by id.
#
#   <kit>/cache/digest-cursor.json
#   {"brain": "2026-09-17T13:10:04.812", "/repo/somewhere": "..."}  # brand: historical
DIGEST_CURSOR_NAME = "digest-cursor.json"


def is_arrival(source: str) -> bool:
    """`compact` is the one SessionStart that is not an arrival: the session never
    left, so the cursor neither narrows its digest nor moves — a compaction that
    stamped would bury every row the person has not read yet. Anything else —
    startup, clear, a payload that said nothing — is someone walking in. A resume
    never gets here: `main()` prints no block for it, so its cursor never moves."""
    return (source or "startup") != "compact"


def cursor_path(cwd: str):
    return Path(cache_dir()) / DIGEST_CURSOR_NAME


def cursor_key(cwd: str, bound: str) -> str:
    """The place, not the session: the bound project slug when there is one, else
    the directory itself (realpath, so a symlinked folder and its target are one key)."""
    if bound:
        return bound
    try:
        return os.path.realpath(cwd or ".")
    except Exception:
        return cwd or "unbound"


def read_cursor(cwd: str, bound: str) -> str:
    """The stamp of the last digest rendered for this place, or "" — which is the
    first morning here and today's behaviour exactly. A cursor that will not parse
    is no cursor; a broken file must never cost a session its context."""
    try:
        return str(json.loads(cursor_path(cwd).read_text(encoding="utf-8")).get(cursor_key(cwd, bound)) or "")
    except Exception:
        return ""


def stamp_cursor(cwd: str, bound: str, epoch: int) -> None:
    """Move this place's cursor to the newest row this digest actually rendered — the row's
    own `created_at_epoch`, never the wall clock. A clock stamp sits above every row that
    exists, so anything imported afterwards carrying an older timestamp (`observer store sync`
    keeps claude-mem's own `created_at_epoch`) lands under the cursor and is never printed
    again; it also reads the hour in whatever zone the machine is in this week, while the
    rows carry epochs. Milliseconds are kept so the stamped row itself does not come back.

    Keys are the places visited, and a directory that no longer exists is one nobody will
    arrive in again, so its key goes. Every other place's stamp is kept. Fail-silent."""
    path = cursor_path(cwd)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data = {k: v for k, v in data.items() if not os.path.isabs(k) or os.path.isdir(k)}
    data[cursor_key(cwd, bound)] = _local(epoch).isoformat(timespec="milliseconds")
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
        replace_retry(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass


# The stamp waits for the print. `memory_digest` renders, `main()` writes; a hook timeout
# kill or a closed pipe in between would otherwise move the cursor for a digest nobody ever
# received, and that window never comes back. `atexit` runs after the write — but it runs
# after a raise too, and `sys.stdout.flush()` succeeds trivially on a stream nothing was
# ever written to, so the flush on its own proves nothing was delivered. Anything that
# throws between the render and the print (a store row that is not UTF-8, say) would have
# burned the window on a flush that did no work. So `main()` says it
# itself, one call after the block is on stdout, and the stamp waits on that word.
_PENDING = []
_DELIVERED = []


def defer_stamp(cwd: str, bound: str, epoch: int) -> None:
    _PENDING.append((cwd, bound, epoch))


def mark_delivered() -> None:
    """The block is written. Every path that raises before this call leaves the window
    intact for the next session; the only cost of that is one re-print."""
    _DELIVERED.append(True)


def flush_stamps() -> None:
    if not _PENDING or not _DELIVERED:
        return  # nothing to stamp, or nothing was delivered — the queue keeps, untouched
    pending, _PENDING[:] = list(_PENDING), []
    try:
        sys.stdout.flush()
    except Exception:
        return
    for cwd, bound, epoch in pending:
        stamp_cursor(cwd, bound, epoch)


atexit.register(flush_stamps)


def _when(iso: str) -> str:
    """The cursor as a person reads it — the day and clock the rows themselves carry."""
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso)
    except Exception:
        return iso
    return f"{_day(dt)} {_clock(dt)}"


def since_window(d: dict, cursor: str) -> dict:
    """Trim a digest to what arrived after the cursor. The new rows stay whole, the
    rest become `older` and one line; the token counts follow the rows that
    survive, because a stats line over rows nobody printed is the dishonest half
    of a cursor. `work_all` keeps the footer's reach true. Collapsed observations
    keep their IDs — dropping those would leave the line naming rows nothing can
    fetch; collapsed sessions keep a count and the day their oldest one falls on,
    which is all `since` needs and all an `S<id>` was ever worth.
    The person's pane is untouched on purpose: `last` is the previous session's
    four paragraphs, and a person walking back in is who that block is for.
    No cursor → untouched."""
    try:
        from datetime import datetime
        edge = int(datetime.fromisoformat(cursor).timestamp() * 1000)
    except Exception:
        return d
    fresh = [it for it in d["items"] if (it[0] or 0) > edge]
    obs = [r for _, kind, r in fresh if kind == "o"]
    older = [it for it in d["items"] if (it[0] or 0) <= edge]
    older_sess = [it for it in older if it[1] == "s"]
    return {**d, "items": fresh, "obs": obs, "sess": [r for _, kind, r in fresh if kind == "s"],
            "read_t": sum(o["read"] for o in obs), "work_t": sum(int(o["work"] or 0) for o in obs),
            "work_all": d["work_t"], "since": cursor, "older": len(older),
            "older_obs": [str(r["id"]) for _, kind, r in older if kind == "o"],
            "older_sess": len(older_sess),
            "older_sess_from": (_local(min(it[0] or 0 for it in older_sess)).strftime("%Y-%m-%d")
                                if older_sess else "")}


def memory_digest(cwd: str, bound: str = "", limit: int = 8, source: str = "") -> tuple:
    """(what Claude reads, what the person sees) for EVERY directory — bound to
    a project note or not. claude-mem injected this for any cwd by its label;
    the store keeps that label, so a checkout with no project note still opens
    on what happened there last. Pointers only; bodies come from the kit
    tools (`recall` → `get`) or observer on demand. The markdown folder is
    the fallback only for a bound project on a vault that never ran
    `observer store init`.

    On an arrival the last-seen cursor narrows the block to what is new and the
    cursor is stamped AFTER the block has been written out — what this session was
    shown is exactly what the next one skips. An arrival with nothing new leaves the
    cursor where it is; there is no newer row to move it to."""
    labels = labels_for(cwd, bound)
    d = context_data(labels, bound or labels[0], CONTEXT_OBSERVATIONS, CONTEXT_SESSIONS)
    if d is None and bound:
        lines = _recent_from_md(bound, limit)
        text = "\n".join([f"[{DISPLAY_NAME}] recent observations · {bound}:"] + lines) if lines else ""
        return text, text
    if not d:
        return "", ""
    if is_arrival(source):
        d = since_window(d, read_cursor(cwd, bound))
    rendered = ("\n".join(render_plain(d)), render_ansi(d, cwd))
    if is_arrival(source) and d["items"]:
        defer_stamp(cwd, bound, max((it[0] or 0) for it in d["items"]))
    return rendered


def main() -> None:
    utf8_stdio()  # the digest is non-ASCII (·, —, box rules); Windows' console default is cp1252
    payload = json.load(sys.stdin)
    cwd = payload.get("cwd") or os.getcwd()
    # A session a program started gets the doors line, not the digest: an eval or a
    # probe that opens on "since you were last here" is measuring the digest, not the
    # task (it reached 216 of 239 e2e runs). AK_CAPTURE=1 counts it as a person's
    # (session_origin.py).
    source = payload.get("source") or ""
    if source == "resume":
        # A resume is the conversation picked back up, not someone walking in: the block was
        # on screen when it first opened, and printing it again only buries the prompt. The
        # model keeps its doors line; nothing renders, so the cursor stays for the next start.
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": TEACH}}))
        return
    automated = automated_entrypoint()
    match = resolve_binding(cwd)
    bound = project_slug(match, cwd) if match else ""
    _digest, shown = memory_digest(cwd, bound, source=source) if not automated else ("", "")
    ctx = TEACH  # the rows are for the person's eyes; the model searches them (module docstring)
    if automated:
        shown = f"{_DIM}[observer] no digest — an automated session ({automated}); {env_name('CAPTURE')}=1 shows it{_R}"
    shown = shown or (
        f"{_DIM}[observer] memory · {labels_for(cwd)[0]} — nothing captured here yet; "
        f"the observer writes rows when this session ends{_R}"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ctx,
        },
        "systemMessage": shown,
    }))
    mark_delivered()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
