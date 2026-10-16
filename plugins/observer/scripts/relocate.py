"""observer's part of a workspace move (`ak ws move`), loaded through plugin.json "relocate".

The store files each row under the basename of the session's git root (capture_common.project_label)
and never rewrites a row. So a move that renames a repo adds ONE line to lineage.jsonl — label old →
new — and every read (the SessionStart digest, file-context, `observer search --project`) asks for
both names through capture_common.labels_with_aliases. The rows, their paths and their embeddings stay
exactly as written.

stdlib only, plus the planner's own ak.move.plan: the planner imports this by path, inside `ak`'s
Python and outside the observer's own venv.
"""
import os


class Handler:
    name = "observer"

    def plan(self, m):
        a, b = os.path.basename(m.old.rstrip("/\\")), os.path.basename(m.new.rstrip("/\\"))
        if a and b and a != b:
            m.lineage("label", a, b)
            m.info("observer", f"past observations filed under '{a}' stay as written; reads of '{b}' include them")

    def detect(self, ctx):
        """Rows filed under a false root — $HOME's own basename — because a path's repo was gone."""
        import sqlite3
        from ak.move.plan import db_dir  # the kit's db/, as the planner names it
        db = os.path.join(db_dir(ctx["home"]), "brain.db")
        if not os.path.isfile(db):
            return []
        bad = os.path.basename(os.path.realpath(os.path.expanduser("~")))
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            n = con.execute("SELECT count(*) FROM observations WHERE project = ?", (bad,)).fetchone()[0]
            con.close()
        except sqlite3.Error:
            return []
        if not n:
            return [{"level": "pass", "what": "observer: no rows filed under $HOME's name"}]
        return [{"level": "warn", "what": f"observer: {n} row(s) filed under '{bad}' — $HOME is a git repo, so a "
                 "session whose folder was gone got its label", "fix": None}]


handler = Handler()
