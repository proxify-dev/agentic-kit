#!/usr/bin/env python3
"""scan_claude_dir: Claude Code's scheduled tasks, in both shapes the app has written them. The older one is a single
`scheduled_tasks.json` holding a list; the newer one is a folder `scheduled-tasks/<name>/SKILL.md` per task (its
frontmatter names it; the body is the prompt the app sends on each run). Both are schedules of the person's that
start an agent, so both land in `claude_scheduled_tasks`, one task per `count`. A task folder is not a skill.
Run: python3 -I test_scheduled_tasks.py   (from this folder)"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inventory  # noqa: E402

TASK = """---
name: nightly-branch-review
description: Every day at 9:00, reviews the branches a handoff email lists. Report only.
---

Find the handoff email from the last 24 hours and review each branch it lists.
"""


class ScheduledTasksCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="sched-"))
        self.cdir = Path(self.tmp) / ".claude"
        self.cdir.mkdir()
        inventory.S = inventory.Store()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def task(self, name, text=TASK):
        d = self.cdir / "scheduled-tasks" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(text)
        return d / "SKILL.md"

    def scan(self):
        inventory.scan_claude_dir(self.cdir, "home", None)
        return inventory.S.lists["claude_scheduled_tasks"], inventory.S.lists["artifacts"]

    def test_a_task_folder_is_one_schedule(self):
        f = self.task("nightly-branch-review")
        tasks, artifacts = self.scan()
        self.assertEqual(len(tasks), 1, tasks)
        t = tasks[0]
        self.assertEqual(t["path"], str(f))
        self.assertEqual(t["scope"], "home")
        self.assertEqual(t["count"], 1)
        self.assertEqual(t["name"], "nightly-branch-review")
        self.assertIn("reviews the branches", t["tasks"][0])

    def test_each_task_folder_counts_once(self):
        self.task("nightly-branch-review")
        self.task("weekly-digest", TASK.replace("nightly-branch-review", "weekly-digest"))
        tasks, _ = self.scan()
        self.assertEqual(sorted(t["name"] for t in tasks), ["nightly-branch-review", "weekly-digest"])
        self.assertEqual(sum(t["count"] for t in tasks), 2)

    def test_a_task_folder_is_not_a_skill(self):
        self.task("nightly-branch-review")
        _, artifacts = self.scan()
        self.assertEqual([a for a in artifacts if a.get("kind") == "skill"], [])

    def test_a_folder_without_a_task_file_is_nothing(self):
        (self.cdir / "scheduled-tasks" / "empty").mkdir(parents=True)
        (self.cdir / "scheduled-tasks" / "notes.txt").write_text("x")
        tasks, _ = self.scan()
        self.assertEqual(tasks, [])

    def test_the_json_shape_still_counts(self):
        (self.cdir / "scheduled_tasks.json").write_text('{"tasks": [{"name": "a", "cron": "0 9 * * *"}, {"name": "b", "cron": "0 10 * * *"}]}')
        tasks, _ = self.scan()
        self.assertEqual([t["count"] for t in tasks], [2])


if __name__ == "__main__":
    unittest.main()
