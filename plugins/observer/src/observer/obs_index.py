#!/usr/bin/env python3
"""The observation index behind `observer search → timeline → get` and `ak search`.

Three layers — search (cheap rows) → timeline (the neighbours of one) → get (rows whole) —
over one engine, BACKEND, picked at import: `SqliteFtsBackend` over the store (brain.db)
when it exists, else `RipgrepBackend`, which reads a vault's markdown notes directly. Both
expose the same methods (search · sessions · timeline · get), so no verb asks which answers.

Identity: a store row's id is its integer id as a string ("37078", what memdb:<id>
carries); a markdown note's is its path RELATIVE to memory/observations/ (e.g.
"ak/2026-06-16-104424-00-foo.md").
"""
import os
import re
import subprocess
import sys
from pathlib import Path

# The resolver is the vault plugin's hooks/vault_root.py, vendored into this plugin's
# hooks/ (scripts/copies.py writes and checks the copy).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
from capture_common import git_root, labels_with_aliases, project_label  # noqa: E402
from vault_root import brain_root, db_dir, home_root  # noqa: E402,F401
from _brand import env, lock_file, posix  # noqa: E402  (the hooks/ copy: this file also runs as a script, below)
from capture_common import command  # noqa: E402

_LIST_INLINE = re.compile(r"^\[(.*)\]$")


def vault_root(cwd: str | None = None) -> Path:  # noqa: ARG001
    """The global vault — where the observer's legacy markdown sink lives, from any cwd.
    The store is beside it, in the kit's db/ (store_db below)."""
    return Path(home_root())


def observations_dir() -> Path:
    """The graph-excluded data layer the observation index reads from.

    Self-contained (was `from capture_common import observations_dir`) so the
    semantic lobe ships whole in the wheel with no plugin-relative hook path.
    """
    return vault_root() / "memory" / "observations"


def parse_frontmatter(text: str) -> dict:
    """Top-level scalars + inline [a, b] lists between the first --- fences."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm: dict = {}
    for line in text[3:end].splitlines():
        if not line.strip() or line[:1] in (" ", "\t") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        m = _LIST_INLINE.match(val)
        if m:
            fm[key] = [v.strip().strip("\"'") for v in m.group(1).split(",") if v.strip()]
        else:
            fm[key] = val.strip("\"'")
    return fm


def _row(path: Path, root: Path) -> dict:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except Exception:
        head = ""
    fm = parse_frontmatter(head)
    return {
        "id": str(path.relative_to(root)),
        "date": fm.get("date", ""),
        "obs_type": fm.get("obs_type", ""),
        "title": fm.get("title", "") or path.stem,
        "project": fm.get("project", ""),
        "session": fm.get("session_id", "") or fm.get("session", ""),
        "concepts": fm.get("concepts", []) if isinstance(fm.get("concepts"), list) else [],
        "_fm": fm,
    }


def group_by_session(ranked, info, limit, offset, keep) -> dict:
    """Ranked rows → one entry per session, the rows voting for their session by
    reciprocal rank (the `_fuse` arithmetic, k=60): many hits and one top hit both
    surface. `ranked` is [(session, id, title, project)] best first; `info(session)`
    is {started, asked} or {}. A row with no session is counted in `unlinked`, never
    guessed. `keep` rows are shown per session."""
    groups, unlinked = {}, 0
    for rank, (sid, rid, title, project) in enumerate(ranked, 1):
        if not sid:
            unlinked += 1
            continue
        g = groups.get(sid)
        if g is None:
            g = groups[sid] = {"session": sid, "project": project, **info(sid),
                               "hits": 0, "top": [], "_score": 0.0}
        g["hits"] += 1
        g["_score"] += 1.0 / (60 + rank)
        if len(g["top"]) < keep:
            g["top"].append({"id": rid, "title": title})
    out = sorted(groups.values(), key=lambda g: g["_score"], reverse=True)
    for g in out:
        del g["_score"]
    return {"sessions": out[offset:offset + limit], "scanned": len(ranked), "unlinked": unlinked}


class RipgrepBackend:
    def _root(self) -> Path:
        return observations_dir()

    def _scope(self, project: str | None) -> Path:
        root = self._root()
        return (root / project) if project else root

    def _all_notes(self, project: str | None) -> list:
        scope = self._scope(project)
        if not scope.exists():
            return []
        return sorted(scope.rglob("*.md"))

    def _rg_ranked(self, query: str, project: str | None) -> list:
        """Files under scope ranked by total match count (desc). Empty query → []."""
        terms = [re.escape(t) for t in query.split() if t.strip()]
        scope = self._scope(project)
        if not terms or not scope.exists():
            return []
        pattern = "|".join(terms)
        try:
            proc = subprocess.run(
                ["rg", "--count-matches", "--color", "never", "--no-messages",
                 "-i", "-g", "*.md", "-e", pattern, str(scope)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        except Exception:
            return []
        scored = []
        for line in proc.stdout.splitlines():
            fpath, _, cnt = line.rpartition(":")
            if fpath and cnt.isdigit():
                scored.append((int(cnt), Path(fpath)))
        scored.sort(key=lambda x: (-x[0], str(x[1])))
        return [p for _, p in scored]

    @staticmethod
    def _passes(row: dict, obs_type, concepts, files, date_start, date_end) -> bool:
        fm = row["_fm"]
        if obs_type and row["obs_type"] != obs_type:
            return False
        if concepts:
            have = {c.lower() for c in row["concepts"]}
            if not all(c.lower() in have for c in concepts):
                return False
        if files:
            touched = " ".join(
                (fm.get("files_read") or []) + (fm.get("files_modified") or [])).lower()
            if not all(f.lower() in touched for f in files):
                return False
        if date_start and row["date"] and row["date"] < date_start:
            return False
        if date_end and row["date"] and row["date"] > date_end:
            return False
        return True

    def search(self, query="", project=None, obs_type=None, concepts=None, files=None,
               date_start=None, date_end=None, limit=20, offset=0, mode=None) -> list:
        # `mode` is the store engine's; the markdown fallback has one candidate rule
        root = self._root()
        if query.strip():
            paths = self._rg_ranked(query, project)
        else:  # no query → recency (filename encodes date-time)
            paths = sorted(self._all_notes(project), reverse=True)
        rows = []
        for p in paths:
            row = _row(p, root)
            if self._passes(row, obs_type, concepts, files, date_start, date_end):
                rows.append(row)
        sliced = rows[offset: offset + limit]
        return [{k: v for k, v in r.items() if k != "_fm"} for r in sliced]

    def sessions(self, limit=20, offset=0, **kw) -> dict:
        rows = self.search(limit=SqliteFtsBackend.GROUP_DEPTH, offset=0, **kw)
        ranked = [(r["session"], r["id"], r["title"], r["project"]) for r in rows]
        return group_by_session(ranked, lambda sid: {}, limit, offset, SqliteFtsBackend.GROUP_KEEP)

    def timeline(self, anchor=None, query=None, before=3, after=3, project=None) -> dict:
        root = self._root()
        notes = sorted(self._all_notes(project))  # chronological by filename
        ids = [str(p.relative_to(root)) for p in notes]
        if anchor is None and query:
            hits = self.search(query=query, project=project, limit=1)
            anchor = hits[0]["id"] if hits else None
        if anchor is None or anchor not in ids:
            return {"anchor": anchor, "rows": []}
        i = ids.index(anchor)
        window = notes[max(0, i - before): i + after + 1]
        rows = []
        for p in window:
            r = _row(p, root)
            r.pop("_fm", None)
            r["is_anchor"] = (str(p.relative_to(root)) == anchor)
            rows.append(r)
        return {"anchor": anchor, "rows": rows}

    def get(self, ids: list) -> list:
        root = self._root().resolve()
        out = []
        for rid in ids:
            try:
                p = (root / rid).resolve()
                p.relative_to(root)  # guard against path escape
                out.append({"id": rid, "content": p.read_text(encoding="utf-8")})
            except Exception:
                out.append({"id": rid, "content": None, "error": "not found or unreadable"})
        return out


def store_db() -> Path:
    """The event store — mirrors hooks/store_common.py, for the same reason
    `vault_root` above mirrors capture_common: the wheel ships whole."""
    return Path(db_dir()) / "brain.db"


def _jlist(v) -> list:
    import json
    if not v:
        return []
    try:
        x = json.loads(v)
        return [str(i) for i in x] if isinstance(x, list) else []
    except Exception:
        return []


def _int_id(rid) -> int | None:
    """'37078', 'memdb:37078', 'obs:37078' → 37078. Anything else → None."""
    s = str(rid or "").strip().rsplit(":", 1)[-1]
    return int(s) if s.isdigit() else None


# ── `--files <path>`: the rows that name one file, by the Read hook's rule ─────
# `_repo_rel` and `_touches` are hooks/file-context.py's, copied whole: that hook points a
# session at `observer search --files <the path it read>`, and the rows it counted
# must be the rows this answers with. tests/test_search_files.py fails on a byte of drift.
def _repo_rel(path: str) -> str | None:
    """`path` relative to the repo holding it, or None outside a repo (or for a file whose
    repo is gone from disk)."""
    root = git_root(os.path.dirname(path))
    if not root:
        return None
    try:
        return os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    except ValueError:
        return None


def _touches(entry: str, path: str, rel: str | None, same_project: bool) -> bool:
    """Does a stored path name the file being read?

    The observer stores what the model wrote — absolute, or relative to wherever that session
    stood — so a bare `README.md` names a README in *some* repo. It used to count as a match
    for every README on the machine (and `CLAUDE.md`, `package.json` …): opening ~/agentic-kit's
    README surfaced rows about unrelated repos. Now:
      absolute  the same file, or the same repo-relative path in the same project (a checkout,
                its worktree and the release clone are one file to memory)
      relative  only a row of the same project, matched against the repo-relative path: a bare
                `README.md` is the repo's top-level README, `src/x.py` any path ending /src/x.py
    Precision over recall — this context is supplementary, and a wrong row misleads."""
    e = entry.strip().rstrip("/\\")
    if not e:
        return False
    if os.path.isabs(e) or e.startswith("~"):
        e = os.path.expanduser(e)
        if os.path.normcase(os.path.normpath(e)) == os.path.normcase(os.path.normpath(path)):
            return True
        return same_project and rel is not None and _repo_rel(e) == rel
    if not same_project or rel is None:
        return False
    e, rel = posix(os.path.normpath(e)), posix(rel)  # matched "/"-separated: Windows' relpath says "\\"
    return rel == e or ("/" in e and rel.endswith("/" + e))


def names_a_file(value: str) -> bool:
    """An absolute or `~/` value to --files is a file, matched by `_touches`; anything else
    stays the path substring it always was (`--files curate.py`)."""
    v = (value or "").strip()
    return os.path.isabs(v) or v.startswith("~")


def touching_ids(conn, path: str) -> list:
    """The ids of every row whose files_read or files_modified name PATH — file-context.py's
    `prior_observations` without its 200-row window, since a search pages on its own."""
    path = os.path.normpath(os.path.expanduser(path.strip()))
    base = os.path.basename(path)
    if not base:
        return []
    rel = _repo_rel(path)
    labels = set(labels_with_aliases([project_label(os.path.dirname(path))]))
    out = []
    for r in conn.execute("SELECT id, files_modified, files_read, project FROM observations "
                          "WHERE files_modified LIKE ? OR files_read LIKE ?", (f"%{base}%", f"%{base}%")):
        same = r[3] in labels
        if any(_touches(f, path, rel, same) for f in _jlist(r[1]) + _jlist(r[2])):
            out.append(int(r[0]))
    return out


# Pass 3 (hidden rows) columns — present on a store ensure_schema() has touched; a store
# older than that has none of them and nothing to hide. Checked once per connection.
_HIDDEN_COLS = ("forgotten_at", "folded_into", "verdict", "until", "supersedes")


class SqliteFtsBackend:
    """The store engine: FTS5 over <kit>/db/brain.db. Same three methods
    as the ripgrep backend; an id is the observation's integer id as a string —
    the number a `memdb:<id>` reference in the vault also carries.

    `search` and `timeline` HIDE what recall must not show — forgotten, folded,
    superseded, expired (curate.hidden_why, the same rule the app applies) — so
    a `forget` actually forgets on this path. `get` by id never hides: reading by
    id is the point, and `_render` says what the row is."""

    @staticmethod
    def _hidden(conn):
        """(SQL clause with one `?` for today, today) when the store can hide rows, else None."""
        have = {r[1] for r in conn.execute("PRAGMA table_info(observations)")}
        if not all(c in have for c in _HIDDEN_COLS if c != "supersedes"):
            return None
        from observer import curate
        return curate.hidden_where(), curate.today()

    @staticmethod
    def _live(conn, rows):
        """The supersedes half of the rule, in Python — a row a live child replaced."""
        from observer import curate
        sup = curate.supersedes_map(conn)
        if not sup:
            return list(rows)
        t = curate.today()
        return [r for r in rows if curate.hidden_why(r, t, sup) is None]

    def _db(self):
        import sqlite3
        p = store_db()
        if not p.exists():
            return None
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _sessions(conn, rows) -> dict:
        """memory_session_id → the session a row came from: `session`, the Claude Code
        session uuid (sdk_sessions.content_session_id — what the tracer's convo_* tools
        and /tracer:trace take), the day it `started`, and what it was `asked`. A
        row whose session the store never recorded (the claude-mem imports, ~10k rows)
        is absent: its session is unknown, not guessed."""
        mids = list({r["memory_session_id"] for r in rows if "memory_session_id" in r.keys() and r["memory_session_id"]})
        out = {}
        for i in range(0, len(mids), 500):
            chunk = mids[i:i + 500]
            for s in conn.execute("SELECT memory_session_id, content_session_id, started_at, custom_title, user_prompt "
                                  f"FROM sdk_sessions WHERE memory_session_id IN ({','.join('?' * len(chunk))})", chunk):
                if s["content_session_id"]:
                    asked = " ".join((s["custom_title"] or s["user_prompt"] or "").split())
                    out[s["memory_session_id"]] = {"session": s["content_session_id"],
                                                   "started": (s["started_at"] or "")[:10],
                                                   "asked": asked[:SqliteFtsBackend.ASKED_CHARS]}
        return out

    @staticmethod
    def _session_of(r, sess) -> dict:
        return (sess or {}).get(r["memory_session_id"] if "memory_session_id" in r.keys() else None) or {}

    @staticmethod
    def _row(r, sess=None) -> dict:
        keys = r.keys()
        return {
            "id": str(r["id"]),
            "date": (r["created_at"] or "")[:10],
            "obs_type": r["type"] or "",
            "title": r["title"] or "",
            "project": r["project"] or "",
            "session": SqliteFtsBackend._session_of(r, sess).get("session", ""),
            "concepts": _jlist(r["concepts"]),
            "verdict": (r["verdict"] if "verdict" in keys else "") or "",
            "folded_into": (r["folded_into"] if "folded_into" in keys else None),
            "forgotten_at": (r["forgotten_at"] if "forgotten_at" in keys else None) or "",
            "supersedes": (r["supersedes"] if "supersedes" in keys else None) or "",
        }

    @staticmethod
    def _render(r, sess=None) -> str:
        keys = r.keys()
        head = [f"# {r['title'] or 'untitled'}", ""]
        if r["subtitle"]:
            head += [r["subtitle"], ""]
        head += [f"- type: {r['type']}  ·  project: {r['project']}  ·  at: {r['created_at']}"]
        s = SqliteFtsBackend._session_of(r, sess)
        if s:
            turn = r["prompt_number"] if "prompt_number" in keys else None
            head += [f"- session: {s['session']}" + (f"  ·  prompt {turn}" if turn else "")]
            if s["asked"]:
                head += [f"- asked: {s['asked']}"]
        if "verdict" in keys and r["verdict"]:
            head += [f"- verdict: {r['verdict']}"]
        if "anchors" in keys and _jlist(r["anchors"]):
            head += ["- anchors: " + ", ".join(_jlist(r["anchors"]))]
        if "supersedes" in keys and r["supersedes"]:
            head += [f"- supersedes: {r['supersedes']}"]
        # Pass 3: the row's own account of why recall may not show it. A row
        # read by id is never hidden — that is the point of reading by id —
        # but it says what it is, so a reader is not told a folded row is live.
        if "until" in keys and r["until"]:
            head += [f"- until: {r['until']}"]
        if "folds" in keys and _jlist(r["folds"]):
            head += ["- folds: " + ", ".join(f"obs:{i}" for i in _jlist(r["folds"]))]
        if "folded_into" in keys and r["folded_into"]:
            head += [f"- folded into: obs:{r['folded_into']}"]
        if "forgotten_at" in keys and r["forgotten_at"]:
            head += [f"- forgotten: {(r['forgotten_at'] or '')[:10]}"
                     + (f" — {r['forget_reason']}" if "forget_reason" in keys and r["forget_reason"] else "")]
        if _jlist(r["concepts"]):
            head += ["- concepts: " + ", ".join(_jlist(r["concepts"]))]
        facts = _jlist(r["facts"])
        body = ["", "## Facts"] + ([f"- {f}" for f in facts] or ["- (none)"])
        body += ["", "## Narrative", (r["narrative"] or r["text"] or "").strip() or "(none)"]
        return "\n".join(head + body)

    # How the query becomes candidates. `hybrid` is the default; its lexical half is
    # `union`: rows matching every term first (bm25 order) and, when fewer than
    # UNION_FLOOR do, rows matching any term (prefix) after them — the any-term rule
    # every studied memory system shares; FTS5's MATCH alone is an implicit AND, which
    # is why `and`, the original behaviour, answered 66 of 127 real queries with nothing. A `refind` arm (top 3 pinned, the rest newest-first) measured
    # no better and was removed on 2026-10-16: results stay in relevance order. Env
    # AK_OBS_SEARCH_MODE overrides the default (`union` or `and` roll it back).
    # `vector` is the dense channel alone, `hybrid` is the gated fusion of it with `union`:
    # when the every-term tier by itself returns UNION_FLOOR rows the query named its rows
    # and that answer stands; only a query the lexical channel starved is fused, by rank
    # (RRF, Cormack 2009 — the Letta/Zep shape), k=FUSE_K and equal weights. The gate is
    # the measured part: fusing unconditionally lifted a wanted row into the top 5 for 108
    # of 119 real queries but lost every wanted row on 7, all exact-identifier queries
    # diluted by older semantic neighbours; gating gives 112 and 4 for the same channels.
    # The vectors live beside the store in a zvec collection written by `build_vectors`
    # (`observer store embed`); the lexical channel and every filter stay in SQL and
    # the dense channel only proposes ids. Missing packages or collection → those modes
    # answer as `union`, never crash. The gate runs before the dense channel is touched,
    # so a query the lexical channel answered never loads the model.
    #
    # zvec holds one LOCK per collection, and a read-only open takes it too: a reader
    # that kept its handle would lock the SessionEnd writer out for as long as that reader
    # lived. So a reader opens per query (~5 ms warm) and closes, a writer holds
    # the lock only to upsert, and each side waits LOCK_WAIT for the other. A reader
    # that still cannot get in answers that one query as `union`.
    MODES = ("and", "union", "vector", "hybrid")
    DEFAULT_MODE = "hybrid"
    UNION_FLOOR = 5
    VEC_DIR = "zvec/observations"
    VEC_FIELD = "emb"
    EMBED_MODEL = "BAAI/bge-small-en-v1.5"
    EMBED_DIM = 384
    # the model pads a batch to its longest text and attention is quadratic in it:
    # a 3,500-char facts list made the whole batch 4× slower. 800 chars keeps the
    # median row (834) whole and the summary — title, subtitle, first facts — always.
    EMBED_CHARS = 800
    FUSE_K = 60
    CHANNEL_DEPTH = 50
    # `sessions`: how deep the ranked rows go before they are grouped, how many of a
    # session's rows it shows, and how much of what the session was asked
    GROUP_DEPTH = 200
    GROUP_KEEP = 3
    ASKED_CHARS = 140
    DID_CHARS = 180
    LOCK_WAIT = {"reader": 1.0, "writer": 30.0}
    _embedder = None

    @staticmethod
    def _terms(query: str) -> list:
        """Distinct query tokens, quotes stripped, order kept. 'app idea app' → ['app', 'idea']."""
        return list(dict.fromkeys(t.replace('"', "") for t in query.split() if t.strip().replace('"', "")))

    @staticmethod
    def _match_and(terms: list) -> str:
        return " ".join(f'"{t}"' for t in terms)

    @staticmethod
    def _match_any(terms: list) -> str:
        return " OR ".join(f'"{t}"*' for t in terms)

    @staticmethod
    def embed_text(r) -> str:
        """What a row's vector stands for: title, subtitle, facts — not the narrative
        (SWE-Bench-CL: the summary embeds better than the body). One definition for
        the writer and the reader."""
        keys = r.keys() if hasattr(r, "keys") else r
        parts = [r["title"] or "", r["subtitle"] or "" if "subtitle" in keys else ""]
        parts += _jlist(r["facts"]) if "facts" in keys else []
        return ". ".join(p.strip() for p in parts if p and p.strip())[:SqliteFtsBackend.EMBED_CHARS]

    @classmethod
    def embed(cls, texts) -> list | None:
        """Vectors for `texts` with the store's model; None when fastembed is absent.
        The model loads once per process (seconds), on the first call. Its files live in
        ~/.cache/fastembed ($FASTEMBED_CACHE_PATH wins): fastembed's own default is the
        OS temp dir, which macOS sweeps — and a swept model is a re-download inside
        somebody's search."""
        try:
            from fastembed import TextEmbedding
        except ImportError:
            return None
        if cls._embedder is None:
            cache = os.environ.get("FASTEMBED_CACHE_PATH") or str(Path.home() / ".cache" / "fastembed")
            cls._embedder = TextEmbedding(cls.EMBED_MODEL, cache_dir=cache, providers=["CPUExecutionProvider"],
                                          threads=min(8, os.cpu_count() or 4))
        return [[float(x) for x in v] for v in cls._embedder.embed(list(texts), batch_size=64)]

    @classmethod
    def _open_vectors(cls, path: Path, role: str, schema=None):
        """The collection at `path`, opened as `role` ('reader' is read-only), waiting up
        to LOCK_WAIT[role] for whoever holds its LOCK. None when a reader finds no
        collection, zvec is absent, or the wait runs out; the caller closes it. A writer
        given `schema` creates the collection when it is missing."""
        import time
        try:
            import zvec
        except ImportError:
            return None
        if not path.exists():
            if role == "reader" or schema is None:
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            return zvec.create_and_open(str(path), schema)
        deadline = time.monotonic() + cls.LOCK_WAIT[role]
        while True:
            try:
                return zvec.open(str(path), zvec.CollectionOption(read_only=role == "reader"))
            except Exception as e:
                if "lock" not in str(e).lower() or time.monotonic() > deadline:
                    if role == "reader":
                        return None
                    raise
                time.sleep(0.05)

    def _dense_ids(self, query: str, labels: list, limit: int) -> list | None:
        """ids nearest to `query` by cosine, nearest first; None when the channel is off."""
        path = store_db().parent / self.VEC_DIR
        if not path.exists():
            return None
        vec = self.embed([query])  # before the open: the lock is held for the query alone
        if not vec:
            return None
        coll = self._open_vectors(path, "reader")
        if coll is None:
            return None
        import zvec
        flt = None
        if labels:
            flt = "project in (" + ",".join("'" + l.replace("'", "''") + "'" for l in labels) + ")"
        try:
            docs = coll.query(zvec.Query(self.VEC_FIELD, vector=vec[0]), topk=limit, filter=flt, output_fields=[])
        finally:
            coll.close()
        return [d.id for d in docs]

    @classmethod
    def _fuse(cls, *lists) -> list:
        """Reciprocal rank fusion: each list votes 1/(K+rank) for its ids; best sum first."""
        score = {}
        for ids in lists:
            for rank, i in enumerate(ids or [], 1):
                score[i] = score.get(i, 0.0) + 1.0 / (cls.FUSE_K + rank)
        return sorted(score, key=lambda i: score[i], reverse=True)

    @classmethod
    def _vec_schema(cls):
        """The collection's shape: one cosine HNSW vector, and the three fields a
        filtered query needs (the `project` filter `_dense_ids` writes is one of them)."""
        import zvec
        return zvec.CollectionSchema(
            "observations",
            fields=[zvec.FieldSchema("project", zvec.DataType.STRING, index_param=zvec.InvertIndexParam()),
                    zvec.FieldSchema("type", zvec.DataType.STRING, index_param=zvec.InvertIndexParam()),
                    zvec.FieldSchema("epoch", zvec.DataType.INT64, index_param=zvec.InvertIndexParam())],
            vectors=[zvec.VectorSchema(cls.VEC_FIELD, zvec.DataType.VECTOR_FP32, cls.EMBED_DIM,
                                       index_param=zvec.HnswIndexParam(metric_type=zvec.MetricType.COSINE))])

    # below this many new rows `build_vectors` skips optimize (the HNSW rebuild, ~0.6 s on
    # 20k rows): a fresh doc is found by a query before it, and SessionEnd adds a handful
    OPTIMIZE_MIN = 500

    def build_vectors(self, rebuild: bool = False, batch: int = 512, progress=None, wait: bool = False) -> dict:
        """Write the dense channel: one vector per observation into the zvec collection
        beside brain.db. What a row's vector stands for is `embed_text` — the same
        definition the reader uses for the query, which is why both live on this class.

        Incremental: ids already in the collection are skipped, so a second run costs
        only the new rows; `rebuild` throws the collection away first. Rows are embedded
        in order of text length because the model pads a batch to its longest member —
        one 3,500-char row made a whole batch 4× slower. Each batch is embedded before
        the collection is opened, so the LOCK is held for the upsert alone and a search
        waits milliseconds, not the length of a build. One build runs at a time, on
        `<store>/zvec/.embed.lock`: a second caller returns at once with `busy`, or with
        `wait` queues behind it and then embeds what the first one never read — how
        SessionEnd's rows survive landing mid-build. `progress(dict)` is called after
        each batch.

        Returns {path, total, embedded, skipped, failed, seconds, busy}. Raises
        RuntimeError when the store or the packages are missing.
        """
        import shutil
        import sqlite3
        import time
        try:
            import zvec
        except ImportError as e:
            raise RuntimeError("zvec is not installed — uv sync in the plugin root") from e
        db = store_db()
        if not db.exists():
            raise RuntimeError(f"no store at {db} — {command('store init')}")
        out = db.parent / self.VEC_DIR
        out_counts = {"path": str(out), "total": 0, "embedded": 0, "skipped": 0,
                      "failed": 0, "seconds": 0.0, "busy": False}
        out.parent.mkdir(parents=True, exist_ok=True)
        guard = open(out.parent / ".embed.lock", "w", encoding="utf-8", newline="\n")
        try:
            try:
                lock_file(guard, blocking=wait)  # flock, or msvcrt on Windows; released by guard.close()
            except BlockingIOError:
                out_counts["busy"] = True
                return out_counts
            if rebuild and out.exists():
                shutil.rmtree(out)

            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id, project, type, created_at_epoch, title, subtitle, facts "
                                "FROM observations ORDER BY id").fetchall()
            conn.close()
            rows.sort(key=lambda r: len(self.embed_text(r)))  # insertion order is nothing to zvec

            have = set()
            if out.exists():
                coll = self._open_vectors(out, "writer")
                try:
                    ids = [str(r["id"]) for r in rows]
                    for i in range(0, len(ids), 5000):
                        have |= set(coll.fetch(ids[i:i + 5000], output_fields=[], include_vector=False))
                finally:
                    coll.close()
            todo = [r for r in rows if str(r["id"]) not in have]
            out_counts.update(total=len(rows), skipped=len(rows) - len(todo))
            t0 = time.time()
            for i in range(0, len(todo), batch):
                chunk = todo[i:i + batch]
                vecs = self.embed([self.embed_text(r) for r in chunk])
                if vecs is None:
                    raise RuntimeError("fastembed is not installed — uv sync in the plugin root")
                coll = self._open_vectors(out, "writer", self._vec_schema())
                try:
                    st = coll.upsert([zvec.Doc(id=str(r["id"]), vectors={self.VEC_FIELD: v},
                                               fields={"project": r["project"] or "", "type": r["type"] or "",
                                                       "epoch": int(r["created_at_epoch"] or 0)})
                                      for r, v in zip(chunk, vecs)])
                    coll.flush()
                finally:
                    coll.close()
                bad = [s for s in st if not s.ok()]
                out_counts["failed"] += len(bad)
                out_counts["embedded"] += len(chunk) - len(bad)
                out_counts["seconds"] = round(time.time() - t0, 1)
                if progress:
                    progress(dict(out_counts))
            if out_counts["embedded"] >= self.OPTIMIZE_MIN:
                coll = self._open_vectors(out, "writer")
                try:
                    coll.optimize()  # the HNSW build; on a cold collection it is most of the wall time
                finally:
                    coll.close()
            out_counts["seconds"] = round(time.time() - t0, 1)
            return out_counts
        finally:
            guard.close()

    def search(self, query="", project=None, obs_type=None, concepts=None, files=None,
               date_start=None, date_end=None, limit=20, offset=0, mode=None) -> list:
        conn = self._db()
        if conn is None:
            return []
        try:
            rows = self._candidates(conn, query, project, obs_type, concepts, files,
                                    date_start, date_end, limit, offset, mode)
            sess = self._sessions(conn, rows)
        finally:
            conn.close()
        return [self._row(r, sess) for r in rows]

    def sessions(self, query="", project=None, obs_type=None, concepts=None, files=None,
                 date_start=None, date_end=None, limit=20, offset=0, mode=None) -> dict:
        """`search`, one entry per session — which sessions a topic came up in. The top
        GROUP_DEPTH ranked rows vote for their session (group_by_session). Each entry is
        {session, project, started, asked, did, hits, top: [{id, title}]} — `asked` how it began,
        `did` what its latest summary says it completed; `hits` counts the
        session's rows among the `scanned`, `unlinked` the rows no session was recorded for."""
        conn = self._db()
        if conn is None:
            return {"sessions": [], "scanned": 0, "unlinked": 0}
        try:
            rows = self._candidates(conn, query, project, obs_type, concepts, files,
                                    date_start, date_end, max(self.GROUP_DEPTH, limit + offset), 0, mode)
            sess = self._sessions(conn, rows)
            did = self._did(conn, sess)
        finally:
            conn.close()
        by_uuid = {s["session"]: s for s in sess.values()}
        ranked = [(self._session_of(r, sess).get("session", ""), str(r["id"]), r["title"] or "", r["project"] or "")
                  for r in rows]
        return group_by_session(ranked, lambda sid: {"started": by_uuid[sid]["started"], "asked": by_uuid[sid]["asked"],
                                                     "did": did.get(sid, "")},
                                limit, offset, self.GROUP_KEEP)

    @staticmethod
    def _did(conn, sess) -> dict:
        """session uuid → what the session's latest summary says it completed (else learned),
        one line. `asked` is how a session began; this is how it ended — together they say
        what it was about, which a reader otherwise opens the session's dialogue to learn
        (the tool-routing eval: 2–3 dialogue calls per "which sessions were about X")."""
        import sqlite3
        mids = list(sess)
        out = {}
        try:
            for i in range(0, len(mids), 500):
                chunk = mids[i:i + 500]
                for s in conn.execute("SELECT memory_session_id, completed, learned FROM session_summaries "
                                      f"WHERE memory_session_id IN ({','.join('?' * len(chunk))}) "
                                      "ORDER BY created_at_epoch", chunk):
                    text = " ".join((s["completed"] or s["learned"] or "").split())
                    if text:  # later rows overwrite: the latest summary with something to say wins
                        out[sess[s["memory_session_id"]]["session"]] = text[:SqliteFtsBackend.DID_CHARS]
        except sqlite3.OperationalError:
            return {}  # a store without the summaries table has nothing to add
        return out

    def _candidates(self, conn, query, project, obs_type, concepts, files,
                    date_start, date_end, limit, offset, mode) -> list:
        """The rows a query names — ranked by `mode`, filtered, hidden rows gone — as
        sqlite Rows on `conn`. `search` and `sessions` are two layouts of this."""
        import sqlite3
        mode = mode or env("OBS_SEARCH_MODE") or self.DEFAULT_MODE
        if mode not in self.MODES:
            mode = self.DEFAULT_MODE
        # the channels fetch at least CHANNEL_DEPTH, and a deeper `limit` deepens them
        # with it — a cap of CHANNEL_DEPTH quietly cut any larger limit to 50
        depth = max(self.CHANNEL_DEPTH, limit) + offset
        where, params, labels = [], [], []
        if project:
            # `ak` (the code repo) and `.ak` (the vault) are one project to memory.
            # The read side expands; capture_common owns the table, hooks and SessionStart
            # read the same one, so there is nothing here to keep in sync.
            labels = labels_with_aliases([project])
            where.append(f"o.project IN ({','.join('?' * len(labels))})"); params += labels
        if obs_type:
            where.append("o.type = ?"); params.append(obs_type)
        if date_start:
            where.append("substr(o.created_at, 1, 10) >= ?"); params.append(date_start)
        if date_end:
            where.append("substr(o.created_at, 1, 10) <= ?"); params.append(date_end)
        for c in concepts or []:
            where.append("lower(o.concepts) LIKE ?"); params.append(f'%"{c.lower()}"%')
        for f in files or []:
            if names_a_file(f):  # a path: the rows that name that file (the Read hook's rule)
                where.append(f"o.id IN ({','.join(map(str, touching_ids(conn, f))) or 'NULL'})")
            else:
                where.append("(o.files_read LIKE ? OR o.files_modified LIKE ?)"); params += [f"%{f}%"] * 2
        hidden = self._hidden(conn)
        if hidden:
            where.append(hidden[0]); params.append(hidden[1])
        extra = (" AND " + " AND ".join(where)) if where else ""

        def fts(match, lim, off):
            sql = (f"SELECT o.* FROM observations_fts f JOIN observations o ON o.id = f.rowid "
                   f"WHERE observations_fts MATCH ?{extra} ORDER BY bm25(observations_fts) LIMIT ? OFFSET ?")
            try:
                return conn.execute(sql, [match] + params + [lim, off]).fetchall()
            except sqlite3.OperationalError:
                return []  # a bad FTS expression is an empty answer, not a crash

        def strict(lim, off=0):
            """The every-term tier — FTS5's own implicit AND, bm25 order."""
            return fts(self._match_and(terms), lim, off)

        def widen(rows, lim, off=0):
            """Any-term rows after the every-term ones, when too few matched every term."""
            if len(rows) >= self.UNION_FLOOR:
                return rows[:lim]
            seen = {r["id"] for r in rows}
            return (rows + [r for r in fts(self._match_any(terms), lim, off)
                            if r["id"] not in seen])[:lim]

        def lexical(lim, off=0):
            """The union rule: every-term rows, then any-term rows when those are few."""
            return widen(strict(lim, off), lim, off)

        def by_ids(ids):
            """Rows for `ids` that pass every filter, in the order given."""
            if not ids:
                return []
            want = [int(i) for i in ids]
            sql = (f"SELECT o.* FROM observations o WHERE o.id IN ({','.join('?' * len(want))}){extra}")
            got = {r["id"]: r for r in conn.execute(sql, want + params).fetchall()}
            return [got[i] for i in want if i in got]

        terms = self._terms(query)
        if not terms:
            sql = (f"SELECT o.* FROM observations o WHERE 1=1{extra} "
                   f"ORDER BY o.created_at_epoch DESC LIMIT ? OFFSET ?")
            rows = conn.execute(sql, params + [limit, offset]).fetchall()
        elif mode == "and":
            rows = fts(self._match_and(terms), limit, offset)
        elif mode == "vector":
            dense = self._dense_ids(query, labels, depth)
            rows = lexical(limit, offset) if dense is None else by_ids(dense)[offset:offset + limit]
        elif mode == "hybrid":
            # The gate. When the every-term tier alone fills the floor, the query named
            # its rows and the lexical answer stands — fusing older semantic neighbours
            # into it is exactly how exact-identifier queries lost the row they wanted.
            # Dense only reaches a query the lexical channel starved.
            hits = strict(depth)
            if len(hits) >= self.UNION_FLOOR:
                rows = hits[offset:offset + limit]
            elif (dense := self._dense_ids(query, labels, depth)) is None:
                rows = lexical(limit, offset)
            else:
                lex = [str(r["id"]) for r in widen(hits, depth)]
                rows = by_ids(self._fuse(lex, dense))[offset:offset + limit]
        else:
            rows = lexical(limit, offset)
        # Every candidate rule ends here: the supersedes half of the hide rule is the
        # one part no WHERE clause can carry, so it runs on whatever the mode chose.
        if hidden:
            rows = self._live(conn, rows)
        return rows

    def timeline(self, anchor=None, query=None, before=3, after=3, project=None) -> dict:
        conn = self._db()
        if conn is None:
            return {"anchor": anchor, "rows": []}
        if anchor is None and query:
            hits = self.search(query=query, project=project, limit=1)
            anchor = hits[0]["id"] if hits else None
        aid = _int_id(anchor)
        cur = conn.execute("SELECT * FROM observations WHERE id = ?", (aid,)).fetchone() if aid else None
        if cur is None:
            conn.close()
            return {"anchor": anchor, "rows": []}
        scope = project or cur["project"]
        e = cur["created_at_epoch"]
        hidden = self._hidden(conn)
        hid = (" AND " + hidden[0]) if hidden else ""
        extra = [hidden[1]] if hidden else []
        # Over-fetch, then apply the supersedes half in Python and cut to the window.
        prev = conn.execute(
            "SELECT o.* FROM observations o WHERE o.project = ? AND (o.created_at_epoch < ? OR "
            f"(o.created_at_epoch = ? AND o.id < ?)){hid} ORDER BY o.created_at_epoch DESC, o.id DESC LIMIT ?",
            (scope, e, e, aid, *extra, before * 3)).fetchall()
        nxt = conn.execute(
            "SELECT o.* FROM observations o WHERE o.project = ? AND (o.created_at_epoch > ? OR "
            f"(o.created_at_epoch = ? AND o.id > ?)){hid} ORDER BY o.created_at_epoch ASC, o.id ASC LIMIT ?",
            (scope, e, e, aid, *extra, after * 3)).fetchall()
        if hidden:
            prev, nxt = self._live(conn, prev), self._live(conn, nxt)
        prev, nxt = prev[:before], nxt[:after]  # the over-fetch is cut whether or not anything was hidden
        sess = self._sessions(conn, [*prev, cur, *nxt])
        conn.close()
        rows = [self._row(r, sess) for r in [*reversed(prev), cur, *nxt]]
        for r in rows:
            r["is_anchor"] = r["id"] == str(aid)
        return {"anchor": str(aid), "rows": rows}

    def get(self, ids: list) -> list:
        conn = self._db()
        out = []
        for rid in ids:
            aid = _int_id(rid)
            r = conn.execute("SELECT * FROM observations WHERE id = ?", (aid,)).fetchone() \
                if (conn is not None and aid is not None) else None
            if r is None:
                out.append({"id": rid, "content": None, "error": "not found"})
            else:
                sess = self._sessions(conn, [r])
                out.append({"id": str(aid), "session": self._session_of(r, sess).get("session", ""),
                            "content": self._render(r, sess)})
        if conn is not None:
            conn.close()
        return out


# The store once it exists; the markdown folder for a vault that never ran
# `observer store init`. Decided at import, like everything else about the engine.
BACKEND = SqliteFtsBackend() if store_db().exists() else RipgrepBackend()


if __name__ == "__main__":  # tiny manual harness
    import json
    verb = sys.argv[1] if len(sys.argv) > 1 else "search"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if verb == "search":
        print(json.dumps(BACKEND.search(query=arg), indent=2))
    elif verb == "timeline":
        print(json.dumps(BACKEND.timeline(anchor=arg or None), indent=2))
    elif verb == "get":
        print(json.dumps(BACKEND.get([arg]), indent=2))
