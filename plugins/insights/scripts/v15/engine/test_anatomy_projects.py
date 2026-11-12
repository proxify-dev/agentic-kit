#!/usr/bin/env python3
"""The most-active-project pick: a person's sessions count whatever app they typed in (the terminal, the desktop app,
VS Code, JetBrains); a headless run (entrypoint sdk-*) carries a program's prompt and counts for no project. On the
owner's laptop (11-12) the pick took a folder with 3 terminal sessions over the repo with 169 desktop-app sessions,
and the review and level read the wrong project. Run: python3 -m unittest test_anatomy_projects   (from this folder;
standard library only)"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anatomy_scan  # noqa: E402


def session(path, cwd, entry, typed=2):
    """One session log: Claude Code's first record names the folder and the app, then the person's (or program's) prompts."""
    rows = [{"type": "user", "cwd": cwd, "entrypoint": entry, "message": {"role": "user", "content": "first prompt"}}]
    rows += [{"type": "user", "cwd": cwd, "message": {"role": "user", "content": f"prompt {i}"}} for i in range(typed - 1)]
    rows.append({"type": "user", "cwd": cwd, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}})
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


class MostActiveProjectCase(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="anatomy-projects-"))  # git resolves /var -> /private/var
        self.repos = {}
        for name in ("desk", "term", "bot"):
            root = os.path.join(self.dir, name)
            os.makedirs(root)
            subprocess.run(["git", "init", "-q", root], check=True)
            self.repos[name] = root
        logs = os.path.join(self.dir, "logs")
        os.makedirs(logs)
        n = 0
        for name, entry, count in (("desk", "claude-desktop", 3), ("term", "cli", 1), ("bot", "sdk-cli", 5)):
            for _ in range(count):
                n += 1
                session(os.path.join(logs, f"{n:08x}-0000-0000-0000-000000000000.jsonl"), self.repos[name], entry)
        self.per = [anatomy_scan.scan_file(os.path.join(logs, f)) for f in sorted(os.listdir(logs))]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_desktop_app_sessions_outrank_fewer_terminal_sessions(self):
        by_repo, active = anatomy_scan.most_active(self.per, 3)
        self.assertEqual(active[:2], [self.repos["desk"], self.repos["term"]])
        self.assertEqual(by_repo[self.repos["desk"]]["typed_sessions"], 3)
        self.assertEqual(by_repo[self.repos["term"]]["typed_sessions"], 1)

    def test_headless_runs_count_for_no_project(self):
        by_repo, active = anatomy_scan.most_active(self.per, 3)
        self.assertEqual(by_repo[self.repos["bot"]]["typed_sessions"], 0)
        self.assertEqual(by_repo[self.repos["bot"]]["sessions"], 5)
        self.assertNotEqual(active[0], self.repos["bot"])

    def test_person_session_rule(self):
        for entry in ("cli", "claude-desktop", "claude-vscode", "claude-jetbrains", None):   # None: too old to name one
            self.assertTrue(anatomy_scan.person_session(entry), entry)
        for entry in ("sdk-cli", "sdk-ts", "sdk-py"):
            self.assertFalse(anatomy_scan.person_session(entry), entry)


if __name__ == "__main__":
    unittest.main()
