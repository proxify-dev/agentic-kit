#!/usr/bin/env python3
"""skill_depth_facts reads how deep a skill of theirs is used from the session events. These are the cases a tester's
laptop missed on 2026-11-12: a skill typed as /name (Claude Code writes <command-message> first, then an isMeta record
"Base directory for this skill: ..."), a Skill tool call that writes that same record (one load, not two), and a skill
whose code lives outside its folder (SKILL.md says "Code: `tests/full-test/`"; the runner ran 400 times, unseen).
Run: python3 -m unittest test_skill_depth   (from this folder; standard library only)"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inventory  # noqa: E402

SKILL_MD = """---
name: full-test
description: Full UI test of a whole product.
---

# Full UI test

Code: `tests/full-test/`. Feature lists: `docs/testing/scribing-feature-inventory.md`.

1. Run `node bin/full-test.js run <product> <env>` from `tests/full-test/`.
"""


class SkillDepthCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="skill-depth-")
        self.project = os.path.join(self.tmp, "repo")
        self.folder = os.path.join(self.project, ".claude", "skills", "full-test")
        os.makedirs(self.folder)
        with open(os.path.join(self.folder, "SKILL.md"), "w") as fh:
            fh.write(SKILL_MD)
        os.makedirs(os.path.join(self.project, "tests", "full-test", "bin"))
        self.runner = os.path.join(self.project, "tests", "full-test", "bin", "full-test.js")
        with open(self.runner, "w") as fh:
            fh.write("console.log('run')\n")
        self.A = {"artifacts": [{"kind": "skill", "name": "full-test", "origin": "own", "scope": "project",
                                 "project": self.project, "path": self.folder,
                                 "entry": os.path.join(self.folder, "SKILL.md"), "files": 0, "bytes": len(SKILL_MD)}]}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def facts(self, skill_ev):
        rec = {"file": os.path.join(self.tmp, "logs", "s1.jsonl"), "helper": False, "chat": "s1", "ep": "claude-desktop",
               "t0": 1000.0, "first_prompt": "run the full test", "skill_ev": skill_ev}
        rows = inventory.skill_depth_facts(self.A, [rec], 0, None, [self.project], set())
        return {r["name"]: r for r in rows}["full-test"]

    def test_typed_skill_command_counts_as_a_load(self):
        # /full-test typed: no Skill call, only Claude Code's own "Base directory for this skill" record
        i = self.facts([(1000.0, "loaded", "full-test")])
        self.assertEqual(i["loads"], 1)

    def test_skill_call_and_its_expansion_record_are_one_load(self):
        # a Skill tool call is followed by the same isMeta record: one load, not two
        i = self.facts([(1000.0, "load", "full-test"), (1001.0, "loaded", "full-test")])
        self.assertEqual(i["loads"], 1)

    def test_two_typed_commands_are_two_loads(self):
        i = self.facts([(1000.0, "loaded", "full-test"), (2000.0, "loaded", "full-test")])
        self.assertEqual(i["loads"], 2)

    def test_runner_outside_the_folder_named_by_skill_md_counts_as_its_script(self):
        # the skill loaded, then the session ran the runner SKILL.md points at (tests/full-test/), outside .claude/skills/
        i = self.facts([(1000.0, "loaded", "full-test"), (1050.0, "script", (self.runner,))])
        self.assertEqual(i["script_run"], "tests/full-test/bin/full-test.js")

    def test_runner_run_before_any_load_is_not_its_script(self):
        i = self.facts([(900.0, "script", (self.runner,)), (1000.0, "loaded", "full-test")])
        self.assertIsNone(i["script_run"])

    def test_a_doc_file_the_skill_names_is_not_its_script(self):
        # dod-browser-test's SKILL.md names `docs/bug-log.md`; a script call that also named that file ran no code of the skill
        os.makedirs(os.path.join(self.project, "docs"))
        doc = os.path.join(self.project, "docs", "bug-log.md")
        with open(doc, "w") as fh:
            fh.write("# bugs\n")
        with open(os.path.join(self.folder, "SKILL.md"), "a") as fh:
            fh.write("\nLog every bug in `docs/bug-log.md`.\n")
        i = self.facts([(1000.0, "loaded", "full-test"), (1050.0, "script", (doc,))])
        self.assertIsNone(i["script_run"])

    def test_a_product_folder_the_skill_names_is_not_its_code(self):
        # dod-browser-test's SKILL.md says "`app/Domains/<X>/` -> product X": the product's code, not the skill's
        src = os.path.join(self.project, "app", "Domains", "Shared", "Auth")
        os.makedirs(src)
        php = os.path.join(src, "BuildsAuthPayload.php")
        with open(php, "w") as fh:
            fh.write("<?php\n")
        with open(os.path.join(self.folder, "SKILL.md"), "a") as fh:
            fh.write("\n- `app/Domains/Shared/` -> Shared code ships in every product.\n")
        i = self.facts([(1000.0, "loaded", "full-test"), (1050.0, "script", (php,))])
        self.assertIsNone(i["script_run"])

    def test_a_code_file_the_skill_names_outright_is_its_script(self):
        os.makedirs(os.path.join(self.project, "scripts"))
        seed = os.path.join(self.project, "scripts", "seed.sh")
        with open(seed, "w") as fh:
            fh.write("#!/bin/sh\n")
        with open(os.path.join(self.folder, "SKILL.md"), "a") as fh:
            fh.write("\nSeed first: `scripts/seed.sh`.\n")
        i = self.facts([(1000.0, "loaded", "full-test"), (1050.0, "script", (seed,))])
        self.assertEqual(i["script_run"], "scripts/seed.sh")

    def test_a_path_that_is_not_on_disk_is_not_its_script(self):
        # `node -e "require('./core/pw')"` run from tests/full-test/ leaves a path-shaped word that is no file
        junk = os.path.join(self.project, "tests", "full-test", "require('./core/pw')")
        i = self.facts([(1000.0, "loaded", "full-test"), (1050.0, "script", (junk,)), (1060.0, "script", (self.runner,))])
        self.assertEqual(i["script_run"], "tests/full-test/bin/full-test.js")


if __name__ == "__main__":
    unittest.main()
