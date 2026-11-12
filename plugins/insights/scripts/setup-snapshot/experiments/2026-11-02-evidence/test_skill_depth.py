#!/usr/bin/env python3
"""skill_depth_facts reads how deep a skill of theirs is used from the session events. These are the cases a
laptop's page missed on 2026-11-11: a skill typed as /name (Claude Code writes <command-message> first, then an isMeta record
"Base directory for this skill: ..."), and a Skill tool call that writes that same record (one load, not two).
Run: python3 -m unittest test_skill_depth   (from this folder; standard library only)"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inventory  # noqa: E402

SKILL_MD = """---
name: weekly-report
description: Writes the weekly report from the changes merged this week.
---

# Weekly report

1. List the changes merged since last Monday.
2. Group them by area and write one line each.
"""


class SkillDepthCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="skill-depth-")
        self.project = os.path.join(self.tmp, "repo")
        self.folder = os.path.join(self.project, ".claude", "skills", "weekly-report")
        os.makedirs(self.folder)
        with open(os.path.join(self.folder, "SKILL.md"), "w") as fh:
            fh.write(SKILL_MD)
        self.A = {"artifacts": [{"kind": "skill", "name": "weekly-report", "origin": "own", "scope": "project",
                                 "project": self.project, "path": self.folder,
                                 "entry": os.path.join(self.folder, "SKILL.md"), "files": 0, "bytes": len(SKILL_MD)}]}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def facts(self, skill_ev):
        rec = {"file": os.path.join(self.tmp, "logs", "s1.jsonl"), "helper": False, "chat": "s1", "ep": "claude-desktop",
               "t0": 1000.0, "first_prompt": "write the weekly report", "skill_ev": skill_ev}
        rows = inventory.skill_depth_facts(self.A, [rec], 0, None, [self.project], set())
        return {r["name"]: r for r in rows}["weekly-report"]

    def test_typed_skill_command_counts_as_a_load(self):
        # /weekly-report typed: no Skill call, only Claude Code's own "Base directory for this skill" record
        i = self.facts([(1000.0, "loaded", "weekly-report")])
        self.assertEqual(i["loads"], 1)

    def test_skill_call_and_its_expansion_record_are_one_load(self):
        # a Skill tool call is followed by the same isMeta record: one load, not two
        i = self.facts([(1000.0, "load", "weekly-report"), (1001.0, "loaded", "weekly-report")])
        self.assertEqual(i["loads"], 1)

    def test_two_typed_commands_are_two_loads(self):
        i = self.facts([(1000.0, "loaded", "weekly-report"), (2000.0, "loaded", "weekly-report")])
        self.assertEqual(i["loads"], 2)


if __name__ == "__main__":
    unittest.main()
