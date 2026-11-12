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


# the two conditions engine.setup_read_gate appends to the L3 and L4 lists, as it words them
def gate_conds(enough, held=False):
    return [{"test": "their setup carries the context: its files show it in at least 2 of 3 parts (rules that reach the "
                     "agent, corrections that stick, memory)", "met": enough},
            {"test": "Claude Code's records show no sign of the opposite habit", "met": not held}]


SHOWN = {"structure": True, "judged": "shown", "quote_ok": True, "why": "Rules sit in three folders and load only there.",
         "quote": {"path": "app/CLAUDE.md", "line": 14, "text": "Before changing how data is stored, read docs/decisions."}}


def page(level, trail, call_questions=(), member=False, **more):
    res = {"disciplines": [{"id": "harness", "name": "Harness engineering", "practices": [{"id": "second-look", "name": "Have the work checked by a reviewer that didn't write it"}]},
                           {"id": "context", "name": "Context engineering", "practices": []}],
           "calibration": {"certainty": {"level": "high", "days_with_sessions": 30, "backed_practices": 21, "areas": 5}},
           "pointer": {"level": level, "trail": trail, "call_questions": list(call_questions),
                       "decided_by": "every practice on the L3 list (levels.md, 11-05)",
                       "best_rung_by_discipline": {"harness": "L3", "context": "L2"}, **more}}
    return view_mylevel.render({"esc": ESC, "v15_result": res, "member": member})


class YourLevel(unittest.TestCase):
    def test_no_copy_names_a_tab_it_does_not_have(self):
        # "Next steps" and "By discipline" were tabs of the practice read, which left both copies on 11-11 (frame.GROUPS)
        trail = [{"level": "L3", "conditions": PRACTICES}, {"level": "L4", "conditions": PRACTICES + [BUILT]}]
        for out in (page("L3", trail, member=True), page("L3", trail)):
            for gone in ("Next steps", "By discipline"):
                self.assertNotIn(gone, out)
            self.assertIn("The Review tab says what to change first", out)

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


class SetupRead(unittest.TestCase):
    """The setup read on the tab (11-11): what the person's files show, part by part, and a level the files don't back
    said as 'L2, could be L3' with the reason first."""

    def parts(self, **by_id):
        return [{"id": i, **by_id.get(i, {"structure": False, "judged": "not shown"})} for i in ("rules", "corrections", "memory")]

    def test_two_parts_shown_keeps_l3_and_shows_each_part(self):
        sr = {"ran": True, "parts": self.parts(rules=SHOWN, memory={**SHOWN, "next": "Prune the notes from October."}), "holds": []}
        out = html.unescape(page("L3", [{"level": "L3", "conditions": PRACTICES + gate_conds(True)}], setup_read=sr))
        self.assertIn('<span class="big">L3</span>', out)
        self.assertNotIn("Could be", out)
        self.assertIn("What your files show", out)
        self.assertEqual((out.count(">Shown<"), out.count(">Not shown yet<")), (2, 1))
        self.assertIn("Before changing how data is stored, read docs/decisions.", out)   # the line it rests on
        self.assertIn("app/CLAUDE.md:14", out)
        self.assertIn("Prune the notes from October.", out)                              # the run's own next step wins
        self.assertIn("The next time you correct the agent on the same thing twice", out)  # the fallback, by part
        # the gate's lines are their own ticks: never counted as practices
        self.assertIn("2 of 2 in place", out)
        self.assertIn("Your files show a setup that carries the context, in at least 2 of 3 parts", out)
        self.assertNotIn("their setup", out)

    def test_one_part_shown_is_l2_could_be_l3_with_the_reason_first(self):
        sr = {"ran": True, "parts": self.parts(rules=SHOWN, memory={"structure": True, "judged": "shown", "quote_ok": False}), "holds": []}
        out = html.unescape(page("L2", [{"level": "L3", "conditions": PRACTICES + gate_conds(False)}, {"level": "L2", "conditions": PRACTICES[:1]}],
                   could_be="L3", setup_read=sr))
        self.assertIn('<span class="big">L2</span>', out)
        self.assertIn('<span class="could">Could be L3</span>', out)
        self.assertIn("Your files don't yet show a setup that carries the context: of 3 parts, 1 fully shown, 1 partly.", out)
        self.assertIn("<em>Could be</em>", out)
        self.assertNotIn("never goes down", out)
        # code and agent disagree: partly, and it says which half is missing
        self.assertIn(">Partly<", out)
        self.assertIn("its quote wasn't found in your file", out)

    def test_a_quote_dropped_for_a_secret_says_so_and_shows_nothing(self):
        """setup_read.py drops a quote whose line holds a secret: the tab says why the part doesn't count, and what to do."""
        dropped = {"structure": True, "judged": "shown", "quote_ok": False, "quote": None,
                   "quote_dropped": "a secret was on that line, so the quote is not kept"}
        sr = {"ran": True, "parts": self.parts(rules=SHOWN, memory=dropped), "holds": []}
        out = html.unescape(page("L2", [{"level": "L3", "conditions": PRACTICES + gate_conds(False)}, {"level": "L2", "conditions": PRACTICES[:1]}],
                   could_be="L3", setup_read=sr))
        self.assertIn(">Partly<", out)
        self.assertIn("holds a key or a token, so we don't keep or show it", out)
        self.assertNotIn("wasn't found in your file", out)
        self.assertIn("What would take you to L3", out)
        self.assertEqual(out.count('<li class="no">'), 1)
        self.assertLess(out.index("Your files don't yet show"), out.index("Where that sits"))

    def test_a_read_that_did_not_run_says_run_it_again(self):
        out = html.unescape(page("L2", [{"level": "L3", "conditions": PRACTICES + gate_conds(False)}], could_be="L4",
                   setup_read={"ran": False, "parts": [], "holds": []}))
        self.assertIn("Could be L4", out)
        self.assertIn("The read of your files didn't run this time", out)
        self.assertEqual(out.count(">Couldn't read<"), 3)

    def test_a_fired_hold_is_a_habit_not_a_verdict(self):
        sr = {"ran": True, "parts": self.parts(rules=SHOWN, memory=SHOWN),
              "holds": [{"id": "endless-sessions", "fired": True, "why": "21 compactions, all automatic"},
                        {"id": "other", "fired": False, "why": "never shown"}]}
        out = html.unescape(page("L2", [{"level": "L3", "conditions": PRACTICES + gate_conds(True, held=True)}], could_be=None, setup_read=sr))
        self.assertNotIn("Could be", out)
        self.assertIn("A habit seen in Claude Code's records:", out)
        self.assertIn("21 compactions, all automatic", out)
        self.assertIn("It is a habit, not a score", out)
        self.assertNotIn("never shown", out)
        self.assertIn("Claude Code's records show a habit that works against your setup", out)

    def test_a_run_without_the_setup_read_renders_as_before(self):
        out = html.unescape(page("L3", [{"level": "L3", "conditions": PRACTICES}]))
        for new in ("What your files show", "Could be", "A habit seen"):
            self.assertNotIn(new, out)
        self.assertIn("It never goes down because a laptop couldn't see something.", out)


class LevelFromThings(unittest.TestCase):
    """The trial read from plain things (engine/things.py): the tab shows it by default when the result has it."""

    THINGS = [{"id": "use-plan-mode", "name": "Use plan mode", "level": "L1", "state": "Claude did this", "proof": "7 Claude asked to switch plan mode on; none started by you"},
              {"id": "steer-mid-run", "name": "Steer the agent mid-run", "level": "L2", "state": "yours and used", "proof": "26 times in the last 30 days: interrupted, rejected or stopped by you"},
              {"id": "skills-commands", "name": "Skills and commands you wrote", "level": "L3", "state": "yours, not used in 30 days", "proof": "3 of yours, nothing in the last 30 days shows it running"},
              {"id": "hooks", "name": "Hooks you wrote", "level": "L4", "state": "not seen", "proof": "none found"}]

    def result(self, level="L2", could="L3", holds=()):
        sr = {"ran": True, "parts": [{"id": "rules", **SHOWN}, {"id": "corrections", "structure": False, "judged": "not shown"},
                                     {"id": "memory", "structure": False, "judged": "not shown"}], "holds": list(holds), "shown": ["rules"]}
        return {"disciplines": [], "calibration": {"certainty": {"level": "high", "days_with_sessions": 22, "backed_practices": 16}},
                "pointer": {"level": "L3", "could_be": "L4", "trail": [{"level": "L3", "conditions": PRACTICES}]},
                "pointer_things": {"model": "things", "level": level, "could_be": could, "days_with_sessions": 22,
                                   "reasons": ["Habits that are your own act: steer the agent mid-run.",
                                               "You have a setup of your own (skills and commands you wrote), but your files show a setup that carries the context in 1 of 3 parts, and L3 asks for 2."],
                                   "things": self.THINGS, "setup_read": sr}}

    def render(self, res, member=False, env=""):
        from unittest import mock
        import os
        with mock.patch.dict(os.environ, {"AGENTIC_SETUP_LEVEL_MODEL": env}):
            return html.unescape(view_mylevel.render({"esc": ESC, "v15_result": res, "member": member}))

    def test_level_why_files_then_things_by_level(self):
        out = self.render(self.result())
        self.assertIn('<span class="big">L2</span>', out)
        self.assertIn('<span class="could">Could be L3</span>', out)
        self.assertIn("<b>Habits that are your own act: steer the agent mid-run.</b>", out)   # what decided it, first
        self.assertIn("L3 asks for 2", out)                                                    # what would move it up
        self.assertLess(out.index("What your files show"), out.index("What your laptop shows, level by level"))
        for name, word in (("Use plan mode", "Claude did this"), ("Steer the agent mid-run", "Yours and used"),
                           ("Skills and commands you wrote", "Yours, not used in 30 days"), ("Hooks you wrote", "Not seen")):
            self.assertIn(f"<b>{name}</b><span class=\"st\">{word}</span>", out)
        self.assertIn("none started by you", out)
        # in place of the practice lists, and without their certainty line
        for old in ("cover the practices this level asks for", "practices backed", "Very sure", "What would take you to", "you do it, at least by hand"):
            self.assertNotIn(old, out)

    def test_staff_see_the_practice_read_beside_it_members_do_not(self):
        self.assertIn("For comparison, the practice read says L3, could be L4", self.render(self.result()))
        self.assertNotIn("practice read", self.render(self.result(), member=True))

    def test_the_env_switch_shows_the_practice_read(self):
        out = self.render(self.result(), env="practices")
        self.assertIn('<span class="big">L3</span>', out)
        self.assertIn("2 of 2 in place", out)
        self.assertNotIn("level by level", out)

    def test_a_hold_and_cant_tell(self):
        out = self.render(self.result(could=None, holds=[{"id": "endless-sessions", "fired": True, "why": "21 compactions, all automatic"}]))
        self.assertNotIn("Could be", out)
        self.assertIn("A habit seen in Claude Code's records:", out)
        out = self.render(self.result(level="can't tell", could=None))
        self.assertIn("Not enough on this laptop yet to show a level", out)
        self.assertNotIn("You are here", out)
        self.assertIn("level by level", out)


if __name__ == "__main__":
    unittest.main()
