#!/usr/bin/env python3
"""'Your level' says the member's level in their words and keeps the internal read out. Made-up pointers, the fields
engine.py writes into v15-result.json.
Run: python3 -m unittest test_view_mylevel   (from this folder; standard library only)"""
import html
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import view_mylevel

ESC = lambda s: html.escape("" if s is None else str(s))  # frame.esc: any value, as text

PRACTICES = [{"test": "Keep what the work has learned in notes the next session reads on its own: they do it, at least by hand (L2+)", "met": True},
             {"test": "Decide what the agent may do alone, what needs a person, and what's off-limits: their setup does it (L3+)", "met": True}]
BUILT = {"test": "things they built, shown at L4 in at least 3 different areas (one built thing counts for one area)", "met": False}


def page(level, trail, call_questions=()):
    res = {"disciplines": [{"id": "harness", "name": "Harness engineering", "practices": [{"id": "second-look", "name": "Have the work checked by a reviewer that didn't write it"}]},
                           {"id": "context", "name": "Context engineering", "practices": []}],
           "calibration": {"certainty": {"level": "high", "days_with_sessions": 30, "backed_practices": 21, "areas": 5}},
           "pointer": {"level": level, "trail": trail, "call_questions": list(call_questions),
                       "decided_by": "every practice on the L3 list (levels.md, 11-05)",
                       "best_rung_by_discipline": {"harness": "L3", "context": "L2"}}}
    return view_mylevel.render({"esc": ESC, "v15_result": res})


class YourLevel(unittest.TestCase):
    def test_level_meaning_and_how_sure(self):
        out = page("L3", [{"level": "L3", "conditions": PRACTICES}])
        self.assertIn('<span class="big">L3</span>', out)
        self.assertIn("Sets agents up to know, learn and remember", out)
        self.assertIn("Very sure · 30 days of sessions, 21 practices backed by a count or a file", out)
        self.assertIn("You are here", out)

    def test_said_to_the_member_with_no_internal_rule(self):
        out = page("L3", [{"level": "L4", "conditions": PRACTICES + [BUILT]}, {"level": "L3", "conditions": PRACTICES}],
                   call_questions=[["second-look"]])
        self.assertIn("you do it, at least by hand", out)
        self.assertIn("your setup does it", out)
        for internal in ("levels.md", "(L2+)", "(L3+)", "they do it", "their setup", "Decided by", "source:"):
            self.assertNotIn(internal, out)

    def test_next_level_lists_only_what_is_missing(self):
        out = page("L3", [{"level": "L4", "conditions": PRACTICES + [BUILT]}, {"level": "L3", "conditions": PRACTICES}])
        self.assertIn("What would take you to L4", out)
        self.assertIn("You built things that change how the agent runs, in at least 3 different areas", out)
        self.assertEqual(out.count('<li class="no">'), 1)

    def test_top_level_has_no_next_step_and_call_only_adds(self):
        out = page("L4", [{"level": "L4", "conditions": PRACTICES}], call_questions=[["second-look"]])
        self.assertNotIn("What would take you to", out)
        self.assertIn("One habit doesn't leave a trace", out)
        self.assertIn("never take away", out)

    def test_no_level_says_so_without_a_number(self):
        out = view_mylevel.render({"esc": ESC, "v15_result": {"pointer": {"level": "can't tell"}}})
        self.assertIn("Not enough on this laptop yet", out)


if __name__ == "__main__":
    unittest.main()
