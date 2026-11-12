#!/usr/bin/env python3
"""classify_origin: whose a skill folder is. On 2026-11-12 a tester's own skill (they created it; teammates then edited
its references/*.md 14 times) read "mixed" because the folder's commits were counted as one pile, and a mixed skill
counts in no row of the page. A skill is theirs when they made its first commit or most of the commits on its
SKILL.md, however much teammates filled in around it. A skill a teammate created that they touched once stays mixed.
Run: python3 -m unittest test_classify_origin   (from this folder; needs git)"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inventory  # noqa: E402

ME = ("Dana Stranger", "dana@home.example")
LEE = ("Lee Teammate", "lee@work.example")


def git(repo, *args, who=ME):
    env = {**os.environ, "GIT_AUTHOR_NAME": who[0], "GIT_AUTHOR_EMAIL": who[1], "GIT_COMMITTER_NAME": who[0],
           "GIT_COMMITTER_EMAIL": who[1], "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout


def commit(repo, files, who, msg="x"):
    for rel, text in files.items():
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
    git(repo, "add", "-A", who=who)
    git(repo, "commit", "-q", "-m", msg, "--allow-empty", who=who)


class ClassifyOriginCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="classify-origin-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        inventory.Identity.emails = {ME[1]}
        inventory.Identity.names = {ME[0].lower()}
        inventory._hist_cache.clear()
        self.skill = os.path.join(self.repo, ".claude", "skills", "dod")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_skill_they_created_stays_theirs_when_teammates_edit_its_references(self):
        commit(self.repo, {".claude/skills/dod/SKILL.md": "---\nname: dod\n---\nbrowser test\n",
                           ".claude/skills/dod/references/a.md": "a\n"}, ME, "feat: dod skill")
        for n in range(3):
            commit(self.repo, {".claude/skills/dod/references/a.md": f"a{n}\n"}, LEE)
        commit(self.repo, {".claude/skills/dod/references/b.md": "b\n"}, ME)
        origin, evidence, slim = inventory.classify_origin(Path(self.skill), "dod", "skill")
        self.assertEqual(origin, "own", evidence)
        self.assertIn("first commit", evidence)

    def test_skill_a_teammate_created_that_they_touched_once_is_mixed(self):
        commit(self.repo, {".claude/skills/dod/SKILL.md": "---\nname: dod\n---\nbrowser test\n",
                           ".claude/skills/dod/references/a.md": "a\n"}, LEE, "feat: dod skill")
        commit(self.repo, {".claude/skills/dod/references/a.md": "a1\n"}, ME)
        for n in range(2):
            commit(self.repo, {".claude/skills/dod/references/a.md": f"a{n + 2}\n"}, LEE)
        origin, evidence, slim = inventory.classify_origin(Path(self.skill), "dod", "skill")
        self.assertEqual(origin, "mixed", evidence)

    def test_skill_whose_skill_md_is_mostly_theirs_is_theirs(self):
        commit(self.repo, {".claude/skills/dod/SKILL.md": "---\nname: dod\n---\nv0\n"}, LEE, "scaffold")
        commit(self.repo, {".claude/skills/dod/SKILL.md": "---\nname: dod\n---\nv1\n"}, ME)
        commit(self.repo, {".claude/skills/dod/SKILL.md": "---\nname: dod\n---\nv2\n"}, ME)
        for n in range(4):
            commit(self.repo, {".claude/skills/dod/references/a.md": f"a{n}\n"}, LEE)
        origin, evidence, slim = inventory.classify_origin(Path(self.skill), "dod", "skill")
        self.assertEqual(origin, "own", evidence)
        self.assertIn("SKILL.md", evidence)


if __name__ == "__main__":
    unittest.main()
