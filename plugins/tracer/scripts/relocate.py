"""tracer's part of a workspace move (`ak ws move`), loaded through plugin.json "relocate".

The index keys every turn by Claude Code's session-folder name (`project_hash`) and points at the
transcript by path (`session_file`); `manifest` and `project_meta` key by the same name. When the
move renames a session folder (move.slugs: old folder → new folder), those pointers follow in one
transaction. `i_files` — the files a turn touched, as it recorded them — is history and stays.

stdlib only: the planner imports this by path, outside the tracer's own venv.
"""
import importlib.util
import os
import sqlite3


def _brand():
    """The brand's words: hooks/_brand.py, generated beside the plugin's hooks. Loaded by path
    under a name of its own — this file is itself loaded by path, outside any package, and adds
    nothing to the planner's sys.path."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks", "_brand.py")
    spec = importlib.util.spec_from_file_location("_tracer_brand", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db() -> str:
    return os.path.join(str(_brand().claude_home()), "plugins", "data", "conversation-index", "index.db")


def _existing(db: str, hashes) -> list:
    """New folder names already in the index — a collision the undo could not tell apart."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    try:
        marks = ",".join("?" * len(hashes))
        found = set()
        for t in ("project_meta", "manifest"):
            try:
                found |= {r[0] for r in con.execute(
                    f"SELECT DISTINCT project_hash FROM {t} WHERE project_hash IN ({marks})", list(hashes))}
            except sqlite3.Error:
                continue
        return sorted(found)
    finally:
        con.close()


class Handler:
    name = "tracer"

    def plan(self, m):
        db = _db()
        if not m.slugs or not os.path.isfile(db):
            return
        hashes = {os.path.basename(a): os.path.basename(b) for a, b in m.slugs.items()}
        taken = _existing(db, list(hashes.values()))
        if taken:
            m.fail("tracer", f"the index already has sessions under {', '.join(taken[:3])}",
                   f"{_brand().cmd('trace index')} --rebuild, then plan the move again")
            return
        m.step("tracer", f"session index: {len(hashes)} folder name(s) and their transcript paths", 50,
               "sql_map", db=db, updates=[
                   {"table": "interactions", "col": "project_hash", "mode": "eq", "map": hashes},
                   {"table": "manifest", "col": "project_hash", "mode": "eq", "map": hashes},
                   {"table": "project_meta", "col": "project_hash", "mode": "eq", "map": hashes},
                   {"table": "interactions", "col": "session_file", "mode": "prefix", "map": dict(m.slugs)},
                   {"table": "project_meta", "col": "project_path", "mode": "prefix", "map": {m.old: m.new}},
               ])

    def detect(self, ctx):
        """Folder names the index holds whose session folder is gone from <claude_home>/projects."""
        db = _db()
        if not os.path.isfile(db):
            return []
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            hashes = [r[0] for r in con.execute("SELECT project_hash FROM project_meta")]
            con.close()
        except sqlite3.Error:
            return []
        projects = os.path.join(ctx["claude"], "projects")
        gone = [h for h in hashes if not os.path.isdir(os.path.join(projects, h))]
        if not gone:
            return [{"level": "pass", "what": f"session index: all {len(hashes)} folders present"}]
        return [{"level": "info", "what": f"session index: {len(gone)} of {len(hashes)} folders no longer in "
                 f"{projects} (Claude Code expired or they moved); their turns stay searchable",
                 "examples": gone[:3]}]


handler = Handler()
