#!/usr/bin/env python3
"""The cap-skills-own row: a skill the person typed as /name is used, even when the depth read found no load of it (the
11-11 page said "1 calls in the window" on the row and "not used in the last 30 days" on the same skill's card).
Run: python3 -m unittest test_board_skills   (from this folder; standard library only)"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board  # noqa: E402


class SkillsOwnRowCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="board-skills-")
        skill = os.path.join(self.dir, "repo", ".claude", "skills", "full-test")
        anatomy = {"artifacts": [{"kind": "skill", "name": "full-test", "origin": "own", "scope": "project", "path": skill,
                                  "project": os.path.join(self.dir, "repo"), "entry": os.path.join(skill, "SKILL.md"),
                                  "description": "Full UI test of a whole product.", "files": 0, "bytes": 100}]}
        inventory = {"skills_used": [],
                     "skill_depth": [{"name": "full-test", "lines": 54, "other_files": 0, "loads": 0, "loads_opened": 0,
                                      "opened": None, "script_run": None, "live_syntax": False, "eval": None, "copies": 1,
                                      "shared_name": False, "has_evals_folder": False}],
                     "slash_commands": {"by_command": [{"command": "/full-test", "times": 1, "bare": 0, "chats": 1}]},
                     "slash_commands_in_chats": [{"what": "/full-test", "calls": 1, "chats": 1}],
                     "who_did_it": {"groups": [{"id": "you", "items": [{"id": "you.own_commands_typed", "n": 1, "chats": 1}]}]},
                     "chats": {"typed_chats": 3}, "tools": {}}
        for name, data in (("anatomy.json", anatomy), ("inventory.json", inventory)):
            with open(os.path.join(self.dir, name), "w") as fh:
                json.dump(data, fh)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_typed_skill_with_no_load_read_is_used_and_says_so(self):
        r = board.r_skills_own(board.Facts(board.Run(self.dir)))
        card = {x["name"]: x for x in r["skills"]}["full-test"]
        self.assertTrue(card["used"], card)
        self.assertIn("typed", card["why"])
        row = {x["name"]: x for x in r["list"]}["full-test"]
        self.assertTrue(row["used"], row)
        self.assertEqual(row["uses"], 1)
        self.assertIn("not used 0", r["skills_summary"])


if __name__ == "__main__":
    unittest.main()
