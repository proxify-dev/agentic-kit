#!/usr/bin/env python3
"""pick_projects without anatomy-scan.json (no PyYAML): the count from chats.jsonl takes every session the person typed
in, whatever app (ep cli, claude-desktop, claude-vscode, claude-jetbrains), and a headless run (ep sdk-*) counts for no
project; the same rule as anatomy_scan.most_active. Run: python3 -m unittest test_pick_projects   (from this folder;
standard library only)"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import setup_map  # noqa: E402


class PickFromChatsCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pick-projects-")
        self.home = os.path.join(self.dir, "home")
        self.run = os.path.join(self.dir, "run")
        os.makedirs(self.run)
        self.repos = {}
        for name in ("desk", "term", "bot"):
            root = os.path.join(self.home, name)
            os.makedirs(root)
            subprocess.run(["git", "init", "-q", root], check=True)
            self.repos[name] = root
        rows = [{"cwd": self.repos["desk"], "ep": "claude-desktop"}] * 3 + [{"cwd": self.repos["term"], "ep": "cli"}] \
            + [{"cwd": self.repos["bot"], "ep": "sdk-cli"}] * 5
        with open(os.path.join(self.run, "chats.jsonl"), "w") as fh:
            fh.write(json.dumps({"header": True}) + "\n")
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_desktop_sessions_outrank_fewer_terminal_sessions_and_headless_runs_do_not_count(self):
        picked = setup_map.pick_projects(self.run, setup_map.Ctx(self.home), 3, {}, {}, None)
        self.assertEqual(picked, [(self.repos["desk"], {"typed_sessions": 3}), (self.repos["term"], {"typed_sessions": 1})])


if __name__ == "__main__":
    unittest.main()
