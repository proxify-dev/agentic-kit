#!/usr/bin/env python3
"""'What you've built' shows the per-skill depth under the skills row. Made-up board rows, the fields board.skill_depth writes.
Run: python3 -m unittest test_view_built   (from this folder; standard library only)"""
import html
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import view_built

SUMMARY = "4 skills: depth 4 0 · 3 1 · 2 1 · 1 1; not used 1; evals not seen running 0; 1 flagged"


def sk(name, depth, why, used=True, big=False, lines=40):
    return {"name": name, "depth": depth, "used": used, "why": why, "lines": lines, "too_big_one_file": big,
            "try_next": "split the parts only some tasks need into references" if big else "",
            "live_context_syntax": False, "has_evals_not_seen_running": False, "evals_note": ""}


SKILLS = [
    sk("deploy-secret", 3, "its script scripts/deploy-secret.sh was run after it loaded"),
    sk("review", 2, "loaded 5x; the agent opened one of its own files in 2 of them (refs/review-rules.md), not in all"),
    sk("bigone", 1, "one file, read whole in all 3 loads", big=True, lines=482),
    sk("dusty", None, "not used in the last 30 days", used=False),
]


def page(row_extra, member=False):
    row = {"id": "cap-skills-own", "name": "Skills and commands you wrote", "kind": "build", "state": "built and used",
           "items": 4, "calls": 9, "unit": "calls", "examples": ["deploy-secret"], "source": "anatomy.json", "why": "4 skills", **row_extra}
    return view_built.render({"esc": html.escape, "member": member, "v15_result": {"board": {"rows": [row]}}})


class SkillDepth(unittest.TestCase):
    def test_summary_and_used_skills_deepest_first(self):
        out = page({"skills": SKILLS, "skills_summary": SUMMARY})
        # plain words counted from the list, not the board's code-like summary (his 11-06)
        self.assertIn("4 skills; 3 used in the last 30 days: 1 run code · 1 use progressive disclosure · 1 read one file; "
                      "1 too big for one file.", out)
        self.assertNotIn("depth 4 0", out)
        self.assertIn("Depth 3 · runs code", out)
        self.assertIn("Depth 2 · progressive disclosure", out)
        self.assertLess(out.index("deploy-secret</span>"), out.index("review</span>"))

    def test_one_file_skills_fold_away_and_the_count_says_how_many_are_commands(self):
        flat = [{"name": f"s{i}", "depth": 1, "used": True, "why": "one file, read whole in all 2 loads"} for i in range(5)]
        out = page({"skills": SKILLS + flat, "skills_summary": SUMMARY, "items": 12})
        self.assertIn("Show 5 that read one file", out)
        self.assertLess(out.index("Show 5 that read one file"), out.index("s0</span>"))
        self.assertIn("(the rest of the 12 are commands)", out)

    def test_a_name_shared_with_an_installed_skill_is_neither_used_nor_one_file(self):
        shared = [{"name": "twin", "depth": None, "used": None, "why": "an installed skill has the same name"}]
        out = page({"skills": SKILLS + shared, "skills_summary": SUMMARY})
        self.assertIn("Show 1 we can't tell apart", out)
        self.assertIn(html.escape("1 can't tell apart from an installed skill with the same name"), out)
        self.assertIn("3 used in the last 30 days", out)   # the shared name is not counted as used

    def test_flag_shows_with_next_step_and_is_not_a_depth(self):
        out = page({"skills": SKILLS, "skills_summary": SUMMARY})
        self.assertIn("482 lines in one file", out)
        self.assertIn("split the parts only some tasks need into references", out)
        self.assertIn("Depth 1 · reads one file", out)  # the flagged skill keeps its depth

    def test_unused_collapsed_behind_a_toggle(self):
        out = page({"skills": SKILLS, "skills_summary": SUMMARY})
        self.assertIn("Show 1 not used", out)
        self.assertIn('<details class="sknu">', out)
        self.assertNotIn("<details class=\"sknu\" open", out)
        self.assertLess(out.index("review</span>"), out.index("Show 1 not used"))
        self.assertLess(out.index("Show 1 not used"), out.index("dusty"))

    def test_member_copy_masks_skill_and_file_names(self):
        out = page({"skills": SKILLS, "skills_summary": SUMMARY}, member=True)
        for leaked in ("deploy-secret", "review-rules", "bigone", "dusty", "scripts/", "refs/", ".md", ".sh"):
            self.assertNotIn(leaked, out)
        self.assertIn("own-skill-1", out)
        self.assertIn("own-skill-4", out)
        self.assertIn("its script was run after it loaded", out)
        self.assertIn("the agent opened one of its own files in 2 of them, not in all", out)
        self.assertIn("Depth 3 · runs code", out)  # depth and the flag stay for the member

    def test_member_mask_keeps_numbers_and_live_context(self):
        self.assertEqual(view_built._mask_why("`claude plugin eval` was run on the plugin that holds it (2x) and 1 headless run that "
                                              "loaded it was given a prompt written in an eval or test file of theirs (evals/p.md)"),
                         "`claude plugin eval` was run on the plugin that holds it (2x) and 1 headless run that "
                         "loaded it was given a prompt written in an eval or test file of theirs")
        self.assertIn("(!`...`)", view_built._mask_why("its SKILL.md runs a command when it loads (!`...`)"))

    def test_skill_called_like_a_word_does_not_damage_the_sentence(self):
        out = page({"skills": [sk("file", 1, "one file, read whole in all 3 loads")], "skills_summary": "1 skill"}, member=True)
        self.assertIn("one file, read whole in all 3 loads", out)

    def test_old_run_without_the_fields_does_not_crash(self):
        out = page({})
        self.assertIn("Skills and commands you wrote", out)
        self.assertNotIn("Depth", out)
        self.assertIn("Skills and commands you wrote", page({}, member=True))

    def test_old_run_summary_only(self):
        out = page({"skills": [], "skills_summary": "depth not read in this run"})
        self.assertIn("depth not read in this run", out)
        self.assertNotIn("Show", out)

    def test_skill_with_unknown_use_is_listed_not_hidden(self):
        s = sk("twin", None, "an installed skill has the same name, so its uses can't be told apart", used=None)
        out = page({"skills": [s], "skills_summary": "1 skill"})
        self.assertIn("Depth not told", out)
        self.assertNotIn("not used</summary>", out)


if __name__ == "__main__":
    unittest.main()
