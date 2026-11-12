#!/usr/bin/env python3
"""A count that is not a number must not break a tab (11-12). The run's file held a list, "12k" or "n/a" where a board row's
count belongs: `int(...)` raised, the one view said "This view failed to build" and the whole "What you've built" tab was gone (and
the level read, which counts the same row, would have stopped the run). Now that one row reads "couldn't check", nothing is
counted for it, and the rest of the tab shows. A count written as text ("1,204") is read as the number it is.
Made-up run folder; standard library only. Run: python3 test_counts.py   (from this folder)"""
import contextlib
import html
import io
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import board  # noqa: E402
import frame  # noqa: E402
import things  # noqa: E402
import wording  # noqa: E402

INSTRUCTIONS = {"id": "cap-instructions", "name": "Instruction files (CLAUDE.md, AGENTS.md)", "kind": "build", "state": "built and used",
                "items": 3, "calls": 40, "unit": "sessions", "examples": [], "source": "anatomy.json", "why": "3 instruction files of yours"}
SKILLS = {"id": "cap-skills-own", "name": "Skills and commands you wrote", "kind": "build", "state": "built and used", "items": 2,
          "calls": 5, "unit": "calls", "examples": [], "source": "anatomy.json", "why": "2 skills of yours; 5 calls in the window"}
HOOKS = {"id": "cap-hooks", "name": "Hooks", "kind": "build", "state": "not seen", "items": 0, "calls": 0, "unit": "runs",
         "examples": [], "source": "inventory.json", "why": "no hook of yours"}
NOT_NUMBERS = (["a"], "12k", "n/a", {"n": 1}, "", True)
RESULT = {"substrate": "t", "evidence": [], "disciplines": [], "pointer": {"level": "L2", "trail": [], "reasons": []},
          "calibration": {}, "recommendations": [], "more_recommendations": [], "content_gaps": [], "detectors": {}}


def run_folder(board):
    d = tempfile.mkdtemp()
    for name, data in (("v15-result.json", {**RESULT, "board": board}), ("setup-read.json", {"ran": False})):
        with open(os.path.join(d, name), "w") as f:
            json.dump(data, f)
    return d


def built_tab(rows):
    """The "What you've built" tab's text for a board of these rows, and what the build printed to stderr."""
    d = run_folder({"rows": rows})
    out, err = os.path.join(d, "page.html"), io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        frame.build(d, out, member=True)
    page = open(out, encoding="utf-8").read()
    tab = re.search(r'<section class="view v-built".*?</section>\s*(?=<section class="view v-|</main>)', page, re.S).group(0)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tab))), err.getvalue()


class Number(unittest.TestCase):
    def test_what_reads_as_a_count(self):
        self.assertEqual([wording.number(x) for x in (4, 4.0, "12", "1,204", " 7 ", 0)], [4, 4, 12, 1204, 7, 0])
        self.assertEqual([wording.number(x) for x in (None, ["a"], "12k", "n/a", "", True, 2.5, float("nan"), {"n": 1})], [None] * 9)

    def test_nothing_there_is_not_a_bad_value(self):
        self.assertFalse(wording.is_not_a_number(None))
        self.assertEqual([wording.is_not_a_number(x) for x in NOT_NUMBERS], [True] * len(NOT_NUMBERS))
        self.assertEqual([wording.count_or_zero(x) for x in (None, "n/a", ["a"], "3")], [0, 0, 0, 3])


    def test_the_board_reads_the_scans_counts_the_same_way(self):
        self.assertEqual([board._n(x) for x in (5, 2.5, "1,204", ["a"], "n/a", None, True)], [5, 2.5, 1204, 0, 0, 0, 0])


class BrokenCount(unittest.TestCase):
    def test_the_built_tab_still_builds_and_that_one_row_says_it_could_not_check(self):
        for field in ("items", "calls"):
            for bad in NOT_NUMBERS:
                with self.subTest(field=field, value=bad):
                    tab, err = built_tab([{**INSTRUCTIONS, field: bad}, SKILLS, HOOKS])
                    self.assertNotIn("failed to build", tab)
                    self.assertNotIn("VIEW FAILED", err)
                    self.assertRegex(tab, r"Instruction files \(CLAUDE\.md, AGENTS\.md\) ?: Couldn't check")   # that row, by its name
                    self.assertEqual(tab.count("Couldn't check"), 1)                                         # and no other
                    self.assertIn("2 skills you wrote", tab)                                                  # the rest of the tab shows
                    self.assertNotIn("AGENTS.md files of yours", tab)                                         # nothing was counted for it

    def test_a_count_written_as_text_is_read(self):
        tab, _ = built_tab([{**INSTRUCTIONS, "items": "1,204"}, SKILLS, HOOKS])
        self.assertIn("1,204 AGENTS.md files of yours", tab)
        self.assertNotIn("Couldn't check", tab)

    def test_a_good_run_reads_as_before(self):
        tab, err = built_tab([INSTRUCTIONS, SKILLS, HOOKS])
        self.assertIn("3 AGENTS.md files of yours", tab)
        self.assertNotIn("Couldn't check", tab)
        self.assertNotIn("VIEW FAILED", err)

    def test_the_level_read_marks_that_one_thing_and_goes_on(self):
        d = run_folder({})
        for bad in NOT_NUMBERS:
            with self.subTest(value=bad):
                found, _ = things.things(d, {"rows": [{**INSTRUCTIONS, "items": bad}, SKILLS, HOOKS]}, {})
                by = {t["id"]: t for t in found}
                self.assertEqual(by["rule-files"]["state"], things.CANT_CHECK)
                self.assertTrue(by["rule-files"]["proof"].startswith("Couldn't check"))
                self.assertIn(by["skills-commands"]["state"], things.PRESENT)                      # the others are as they were
                self.assertEqual(by["hooks"]["state"], things.NOT_SEEN)


if __name__ == "__main__":
    unittest.main()
