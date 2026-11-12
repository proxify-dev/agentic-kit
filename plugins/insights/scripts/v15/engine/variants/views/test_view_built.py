#!/usr/bin/env python3
"""'What you've built' answers "what have I built for my agent, and does it get used?" (11-11 on the tab before:
"really hard to read"): a tile per kind they have, then each kind sorted by use with the unused folded, a skill's reach
as four plain steps, hooks pinned on a session's life, rule files counted only where the count holds. Made-up board rows
with the `list` board.py writes. Run: python3 -m unittest test_view_built   (from this folder; standard library only)"""
import html
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import view_built


HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".."))   # views -> variants -> engine -> v15 -> scripts/ (= labs/)


def _load(name, path):
    """A module of the scan or the engine, by its path (they find their neighbours by relative path)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TypedSkill(unittest.TestCase):
    """A skill of theirs the person typed as /name, from the scan's facts through the board to this tab (11-11: the row said
    "1 calls" and the same skill's card said "not used in the last 30 days"). Claude Code writes a typed skill command
    <command-message> first, then an isMeta record "Base directory for this skill: <folder>", which the log reader keeps as
    a "loaded" event; a Skill tool call is followed by that same record. A made-up skill in a temporary folder."""
    SKILL_MD = ("---\nname: weekly-report\ndescription: Writes the weekly report from the changes merged this week.\n---\n\n"
                "# Weekly report\n\n1. List the changes merged since last Monday.\n2. Group them by area and write one line each.\n")

    @classmethod
    def setUpClass(cls):
        cls.scan = _load("inventory_for_typed_skill",
                         os.path.join(SCRIPTS, "setup-snapshot", "experiments", "2026-11-02-evidence", "inventory.py"))
        cls.board = _load("board_for_typed_skill", os.path.join(SCRIPTS, "v15", "engine", "board.py"))

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="typed-skill-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        project = os.path.join(self.tmp, "repo")
        folder = os.path.join(project, ".claude", "skills", "weekly-report")
        os.makedirs(folder)
        with open(os.path.join(folder, "SKILL.md"), "w") as fh:
            fh.write(self.SKILL_MD)
        self.project = project
        self.skill = {"kind": "skill", "name": "weekly-report", "origin": "own", "scope": "project", "project": project,
                      "path": folder, "entry": os.path.join(folder, "SKILL.md"), "files": 0, "bytes": len(self.SKILL_MD),
                      "description": "Writes the weekly report from the changes merged this week."}

    def loads(self, *events):
        """How many loads the scan counts for the skill from one session's events."""
        rec = {"file": os.path.join(self.tmp, "logs", "s1.jsonl"), "helper": False, "chat": "s1", "ep": "claude-desktop",
               "t0": 1000.0, "first_prompt": "write the weekly report", "skill_ev": list(events)}
        rows = self.scan.skill_depth_facts({"artifacts": [self.skill]}, [rec], 0, None, [self.project], set())
        return {r["name"]: r for r in rows}["weekly-report"]["loads"]

    def test_a_typed_skill_command_is_a_load(self):
        # /weekly-report typed: no Skill call, only Claude Code's own "Base directory for this skill" record
        self.assertEqual(self.loads((1000.0, "loaded", "weekly-report")), 1)

    def test_a_skill_call_and_its_record_are_one_load_not_two(self):
        self.assertEqual(self.loads((1000.0, "load", "weekly-report"), (1001.0, "loaded", "weekly-report")), 1)

    def test_two_typed_commands_are_two_loads(self):
        self.assertEqual(self.loads((1000.0, "loaded", "weekly-report"), (2000.0, "loaded", "weekly-report")), 2)

    def test_typed_with_no_load_read_is_used_on_the_card_the_row_and_this_tab(self):
        # the scan read no load of it (an older log, say), yet the person typed it: used everywhere it shows, never "not used"
        depth = {"name": "weekly-report", "lines": 8, "other_files": 0, "loads": 0, "loads_opened": 0, "opened": None,
                 "script_run": None, "live_syntax": False, "eval": None, "copies": 1, "shared_name": False, "has_evals_folder": False}
        inventory = {"skills_used": [], "skill_depth": [depth],
                     "slash_commands": {"by_command": [{"command": "/weekly-report", "times": 1, "bare": 0, "chats": 1}]},
                     "slash_commands_in_chats": [{"what": "/weekly-report", "calls": 1, "chats": 1}],
                     "who_did_it": {"groups": [{"id": "you", "items": [{"id": "you.own_commands_typed", "n": 1, "chats": 1}]}]},
                     "chats": {"typed_chats": 3}, "tools": {}}
        for name, data in (("anatomy.json", {"artifacts": [self.skill]}), ("inventory.json", inventory)):
            with open(os.path.join(self.tmp, name), "w") as fh:
                json.dump(data, fh)
        r = self.board.r_skills_own(self.board.Facts(self.board.Run(self.tmp)))
        card = {x["name"]: x for x in r["skills"]}["weekly-report"]
        self.assertTrue(card["used"], card)
        self.assertIn("typed", card["why"])
        listed = {x["name"]: x for x in r["list"]}["weekly-report"]
        self.assertEqual((listed["used"], listed["uses"]), (True, 1), listed)
        self.assertIn("not used 0", r["skills_summary"])
        out = page([r])
        self.assertIn("weekly-report", out)
        self.assertNotIn("Not used in 30 days", out)


def sk(name, step, uses, used=True, why="", about=""):
    return {"name": name, "kind": "skill", "step": step, "uses": uses, "used": used, "why": why, "about": about, "copies": 1}


SKILLS = [sk("deploy-secret", 3, 12, why="its script scripts/deploy-secret.sh was run after it loaded", about="Ships a secret."),
          {**sk("review", 2, 30, why="loaded 5x; the agent opened one of its own files in 2 of them (refs/review-rules.md), not in all"), "copies": 2},
          sk("bigone", 1, 4), sk("twin", None, None, used=None, why="an installed skill has the same name"),
          sk("dusty", None, 0, used=False)] + [sk(f"s{i}", 1, 1) for i in range(8)]
SKILLS.append({"name": "ship", "kind": "command", "uses": 0, "used": False, "about": "Ship it.", "copies": 2})
HOOKS = [{"name": "guard", "plugin": "", "events": ["PreToolUse"], "runs_at": {"PreToolUse": 900}, "uses": 900, "copies": 1},
         {"name": "notes", "plugin": "kit", "events": ["SessionStart", "UserPromptSubmit"], "runs_at": {"SessionStart": 40},
          "uses": 40, "copies": 1},
         {"name": "idle-check", "plugin": "", "events": ["Stop"], "runs_at": {}, "uses": 0, "copies": 1},
         {"name": "stop-log", "plugin": "", "events": ["Stop"], "runs_at": {"Stop": 20}, "uses": 20, "copies": 1}]


def row(id_, state="built and used", items=0, calls=0, unit="calls", lst=None, **more):
    r = {"id": id_, "name": id_, "kind": "build", "state": state, "items": items, "calls": calls, "unit": unit,
         "examples": [], "source": f"inventory.json: {id_}", "why": "", **more}
    if lst is not None:
        r["list"] = lst
    return r


def page(rows, member=True, masked=False, **ctx):
    return view_built.render({"esc": html.escape, "member": member, "masked": masked,
                              "v15_result": {"board": {"rows": rows}}, **ctx})


def text(out):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", out)))


RULES = row("cap-instructions", items=40, calls=129, unit="sessions", projects=3, in_read=6, read_projects=2,
            read=[{"root": "~/shop", "always": 2, "scoped": 3}, {"root": "~/ops", "always": 1, "scoped": 0}])
FULL = [row("cap-skills-own", items=14, calls=47, lst=SKILLS), row("cap-hooks", items=3, calls=940, unit="runs", lst=HOOKS),
        row("cap-subagents", "built, not used lately", items=1, lst=[{"name": "scout", "about": "Reads", "uses": 0, "model": "haiku"}]),
        row("cap-mcp", "uses others'", items=6, calls=902), row("cap-evals", "not seen"), row("cap-event-runs", "can't tell",
                                                                                             why="the logs don't say who started them")]
MAP = {"projects": [{"root": "~/shop", "typed_sessions": 120, "totals": {"always_files": 2, "scoped_files": 3, "on_demand_files": 7}},
                    {"root": "~/ops", "typed_sessions": 9, "totals": {"always_files": 1, "scoped_files": 0, "on_demand_files": 0}}],
       "user": {"totals": {"always_files": 1}}}


class Built(unittest.TestCase):
    def test_at_a_glance_a_tile_per_kind_they_have(self):
        out = page(FULL + [RULES], setup_map=MAP)
        tiles = re.findall(r'<a class="tile" href="#built-(\w+)"[^>]*><span class="bt-num">([\d,]+)</span><span class="lab">([^<]+)', out)
        self.assertEqual(tiles, [("skills", "13", "skills you wrote"), ("commands", "1", "command you wrote"),
                                 ("rules", "40", "AGENTS.md files of yours"), ("agents", "1", "sub-agent you defined"),
                                 ("hooks", "4", "hooks you set up")])
        self.assertIn("<b>12</b> used this month", out)    # used, and a name an installed skill also has: not idle
        self.assertIn("<b>3</b> ran this month", out)
        self.assertIn("<b>129</b> sessions ran with them", out)
        for kind in ("skills", "commands", "rules", "agents", "hooks"):   # a tile opens its section
            self.assertIn(f'<section class="kd" id="built-{kind}"', out)
        self.assertNotIn('href="#built-evals"', out)                     # nothing of theirs: no tile

    def test_a_kind_lists_its_things_by_use_and_folds_the_unused(self):
        out = page(FULL)
        skills = out[out.index('id="built-skills"'):out.index('id="built-commands"')]
        first = skills[:skills.index("Show all")]
        self.assertEqual(first.count('<li class="it">'), view_built.TOP)
        self.assertLess(first.index(">review<"), first.index(">deploy-secret<"))   # 30 uses before 12
        self.assertIn("Show all 12", skills)
        self.assertIn("Not used in 30 days (1)", skills)
        self.assertLess(skills.index("Not used in 30 days"), skills.index("dusty"))
        self.assertIn("can't tell", skills)                                    # a shared name is listed, never hidden
        self.assertIn("Ships a secret.", skills)                               # their own description, one line
        # a skill's reach in plain words, its detail in the tooltip; no "Depth N" and no legend paragraph
        self.assertIn("Runs code</span>", skills)
        self.assertIn("Opens more files when needed</span>", skills)
        self.assertIn("refs/review-rules.md", skills)
        for gone in ("Depth", "progressive disclosure", "evals seen running"):
            self.assertNotIn(gone, out)
        self.assertIn('review<small title="2 copies on this laptop, counted once">×2', out)   # copies of one skill are one

    def test_hooks_are_pinned_on_a_sessions_life(self):
        out = page(FULL)
        hooks = out[out.index('id="built-hooks"'):out.index('id="built-agents"')] if 'id="built-agents"' in out[out.index('id="built-hooks"'):] else out[out.index('id="built-hooks"'):]
        moments = re.findall(r'<span class="mo">([^<]+)', hooks)
        self.assertEqual(moments, ["Session starts", "You send a message", "Before a tool runs", "After it runs", "Agent stops", "Session ends"])
        self.assertRegex(hooks, r"Before a tool runs.*?<span>guard</span><b>900</b>")
        self.assertRegex(hooks, r"Session starts.*?<span>notes</span><b>40</b>")
        self.assertRegex(hooks, r'Agent stops</span>.*?<span>stop-log</span><b>20</b>.*?class="hk-chip idle"[^>]*><span>idle-check')
        # where the records count no runs, the hook is drawn with no number and the moment says so (never "0")
        self.assertRegex(hooks, r"You send a message<small>runs not counted</small>.*?<span>notes</span></span>")

    def test_rule_files_are_told_in_the_one_sentence_with_the_projects_read_under_it(self):
        out = page(FULL + [RULES], setup_map=MAP)
        rules = out[out.index('id="built-rules"'):out.index('id="built-agents"')]
        self.assertIn("40 AGENTS.md files of yours in 3 projects; 6 of them in the 2 projects we read. 129 sessions ran where they load.", text(rules))
        self.assertIn("2 load in every session · 3 load in their folder · 7 docs opened when needed", rules)
        self.assertIn("1 load in every session", rules)
        self.assertNotIn("busiest", rules)
        self.assertNotIn('href="#built-rules"', page(FULL, setup_map=MAP))    # no row with rule files of theirs: no tile

    def test_the_rule_files_count_does_not_need_the_map(self):
        out = page(FULL + [row("cap-instructions", items=40, calls=129, unit="sessions", projects=3)])
        self.assertIn("40 AGENTS.md files of yours in 3 projects. 129 sessions ran", text(out))

    def test_the_persons_own_page_names_their_things_the_masked_copy_does_not(self):
        own = page(FULL + [RULES], setup_map=MAP)
        for name in ("deploy-secret", "guard", "scout", "shop"):
            self.assertIn(name, own)
        masked = page(FULL + [RULES], masked=True, setup_map=MAP)
        for leaked in ("deploy-secret", "Ships a secret.", "refs/review-rules.md", "scripts/deploy-secret.sh", "guard", "scout", ">shop<"):
            self.assertNotIn(leaked, masked)
        for kept in ("own-skill-1", "own-hook-1", "own-sub-agent-1", "project-1", "Runs code</span>", "<b>900</b>"):
            self.assertIn(kept, masked)

    def test_member_mask_keeps_numbers_and_live_context(self):
        self.assertEqual(view_built._mask_why("`claude plugin eval` was run on the plugin that holds it (2x) and 1 headless run that "
                                              "loaded it was given a prompt written in an eval or test file of theirs (evals/p.md)"),
                         "`claude plugin eval` was run on the plugin that holds it (2x) and 1 headless run that "
                         "loaded it was given a prompt written in an eval or test file of theirs")
        self.assertIn("(!`...`)", view_built._mask_why("its SKILL.md runs a command when it loads (!`...`)"))
        self.assertEqual(view_built._mask_why("its script scripts/x.sh was run after it loaded"), "its script was run after it loaded")

    def test_the_rest_folds_behind_one_line_each(self):
        out = page(FULL)
        t = text(out)
        self.assertIn("In use, made by others cap-mcp · 902 calls this month", t)
        self.assertIn("Not seen on this laptop (1)", t)
        self.assertIn("They show whether a change to your setup made it better or worse.", t)   # what it would give
        self.assertIn("Couldn't tell from the logs (1)", t)
        self.assertIn("the logs don't say who started them", t)
        self.assertEqual(out.count('<details class="bt-fold none">'), 2)
        self.assertNotIn("How we know", out)                                   # the field a count rests on is Proxify's
        self.assertIn("How we know", page(FULL, member=False))

    def test_nothing_built_says_so(self):
        out = page([row("cap-mcp", "uses others'", items=2, calls=5)])
        self.assertIn("Nothing on this laptop shows a part of the setup that you built yourself yet.", out)
        self.assertNotIn('class="tile"', out)

    def test_old_runs_and_half_filled_rows_do_not_crash(self):
        # before the board's `list`: the count from the row, the skills from their depth list, no number of uses
        old = page([row("cap-skills-own", items=4, calls=9, skills=[{"name": "a", "depth": 3, "used": True, "why": "w"},
                                                                    {"name": "b", "depth": None, "used": False}]),
                    row("cap-hooks", items=2, calls=5, unit="runs", examples=["x.sh"])])
        self.assertIn('<span class="bt-num">2</span><span class="lab">skills you wrote', old)
        self.assertIn("Runs code</span>", old)
        self.assertIn('<span class="bt-num">2</span><span class="lab">hooks you set up', old)
        self.assertIn("x.sh", old)
        for junk in ([row("cap-skills-own", lst=[{}, "x", {"name": "a"}])], [row("cap-hooks", lst=[{"name": "h", "events": None}])],
                     [{"id": "cap-hooks", "state": "built and used"}]):
            out = page(junk)                                                   # renders, whatever it could count
            self.assertTrue('class="kd"' in out or "Nothing on this laptop" in out)
        self.assertIn("not in this run", view_built.render({"esc": html.escape, "v15_result": {}}))


class StartedOutsideAProject(unittest.TestCase):
    """Sessions that start in the home folder and work across 6 repos read "built 0" (11-11): the page says where the work was found."""
    ROWS = [row("cap-mcp", "uses others'", items=2, calls=5)]

    def view(self, work_found):
        return page(self.ROWS, inventory={"work_found": work_found})

    def test_most_sessions_outside_a_project_and_repos_found_from_the_work(self):
        out = text(self.view({"typed_sessions": 40, "start_outside_repo": 31, "repos_worked_in": 6}))
        self.assertIn("This page follows the work, not the folder: it found 6 projects from the files your agent edited and the "
                      "commits it made there, because most of your sessions start in a folder that isn't a project (31 of 40).", out)

    def test_none_found_reads_as_could_not_see_not_did_not_build(self):
        out = text(self.view({"typed_sessions": 40, "start_outside_repo": 31, "repos_worked_in": 0}))
        self.assertIn("A zero below means we couldn't see it, not that you built nothing", out)

    def test_sessions_that_start_in_projects_get_no_line(self):
        self.assertNotIn("note start", self.view({"typed_sessions": 40, "start_outside_repo": 10, "repos_worked_in": 3}))
        self.assertNotIn("note start", self.view({"typed_sessions": 40, "start_outside_repo": 20, "repos_worked_in": 3}))   # half is not most
        self.assertNotIn("note start", self.view({}))                                                                      # an older run
        self.assertNotIn("note start", page(self.ROWS))


if __name__ == "__main__":
    unittest.main()
