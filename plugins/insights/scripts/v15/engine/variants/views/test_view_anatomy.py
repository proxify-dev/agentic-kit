#!/usr/bin/env python3
"""Part by part (view_anatomy.py) and its pictures (anatomy_pic_*.py), on a small made-up run written here, so they read
the same on any laptop. Run: uv run --with pyyaml python -I test_view_anatomy.py   (from this folder)"""
import html
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import view_anatomy as V  # noqa: E402

M = V.M
HOME = os.path.expanduser("~")
T0 = 1_790_000_000  # a fixed moment; every event below is minutes after it
PICTURES = sorted(set(V.PICTURES.values()))


def esc(s):
    return html.escape("" if s is None else str(s))


def chat(cid, project, start_min, typed_at, calls_at, compactions=()):
    t = lambda m: T0 + m * 60  # noqa: E731
    return {"chat": cid, "project": project, "cwd": "/tmp/" + project, "ep": "cli", "version": "2.1.0",
            "t0": t(start_min), "t1": t(max(calls_at + typed_at)),
            "msgs": [{"i": i, "t": t(m), "ctx": 40_000 + 9_000 * i, "int": 0, "rej": 0, "corr": False, "chars": 20,
                      "head": f"please look at part {i}", "pm": None, "uuid": f"{cid}-{i}"} for i, m in enumerate(typed_at)],
            "calls": [{"t": t(m), "ctx": 41_000 + 5_000 * i, "name": "Agent" if i == 0 else "Bash", "key": "x"}
                      for i, m in enumerate(calls_at)],
            "compactions": [{"t": t(m), "at": 1, "trigger": trig, "pre": 600_000, "post": 20_000} for m, trig in compactions],
            "commands": [], "attachments": {"hook_success": 3, "hook_additional_context": 2},
            "helpers": {"calls": {"Bash": len(calls_at)}, "bash": [], "files_read": 0, "n": 1}}


def made_up_run():
    run = tempfile.mkdtemp()

    def w(name, obj):
        with open(os.path.join(run, name), "w") as f:
            json.dump(obj, f)
    w("anatomy-scan.json", {"checks": {}, "files": {}})
    with open(os.path.join(run, "chats.jsonl"), "w") as f:
        f.write(json.dumps({"meta": {"days": 30, "chats": 3}}) + "\n")
        for c in (chat("a1", "-tmp-alpha", 0, [0, 30, 95], [1, 3, 5, 31, 33, 96, 97], [(60, "manual")]),
                  chat("b2", "-tmp-beta", 10, [10, 70], [11, 12, 14, 71, 72, 74, 80], [(75, "auto")]),
                  chat("c3", "-tmp-alpha", 20, [20], [21, 22, 23, 24])):
            f.write(json.dumps(c) + "\n")
    hook = lambda event, path: {"scope": "home", "project": None, "source_kind": "settings", "source": "settings.json",  # noqa: E731
                                "event": event, "matcher": "", "type": "command", "command": "python3 " + path,
                                "script_path": path, "script_exists": True, "resolves_on_path": False}
    art = lambda kind, name, origin: {"kind": kind, "name": name, "path": f"/tmp/alpha/{kind}/{name}", "scope": "project",  # noqa: E731
                                      "project": "/tmp/alpha", "bytes": 100, "origin": origin, "origin_evidence": "made up"}
    w("anatomy.json", {
        "meta": {"targets": ["/tmp/alpha", "/tmp/beta"], "days": 30},
        "hooks": [hook("SessionStart", "/tmp/alpha/hooks/start.py"), hook("PreToolUse", "/tmp/alpha/hooks/guard.py"),
                  hook("Stop", "/tmp/alpha/hooks/done.py")],
        "artifacts": [art("skill", "one", "own"), art("skill", "two", "installed"), art("hook_script", "guard.py", "own"),
                      art("agent", "helper", "third_party")],
        "instruction_files": [{"path": "/tmp/alpha/CLAUDE.md", "kind": "claude", "scope": "project", "project": "/tmp/alpha",
                               "bytes": 800, "lines": 20, "imports": []}],
        "mcp_servers": [], "memory_stores": [], "settings": {"files": [], "notable": {}},
        "plugins": {"enabled": [], "dir_loaded": [], "marketplaces": []}, "identity": {"emails": []}})
    return run


def tab(run):
    err = io.StringIO()
    with redirect_stderr(err):
        out = V.render({"esc": esc, "run": run})
    return out, err.getvalue()


class Tab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rundir = made_up_run()
        cls.out, cls.err = tab(cls.rundir)
        import yaml
        with open(V.ANATOMY) as f:
            cls.A = yaml.safe_load(f)

    def test_every_part_appears_once_and_every_discipline_is_a_chapter(self):
        for d in self.A["disciplines"]:
            self.assertEqual(self.out.count(f'id="px-ch-{d["id"]}"'), 1, d["id"])
            for c in d["components"]:
                self.assertEqual(self.out.count(f'<details class="px-part" id="px-{d["id"]}-{c["id"]}"'), 1, c["id"])
        self.assertIn('id="px-ch-call"', self.out)

    def test_every_picture_builds_from_a_made_up_run(self):
        self.assertNotIn("PICTURE FAILED", self.err)
        for name in PICTURES:
            self.assertIn(f'id="px-viz-{name}"', self.out, name)

    def test_a_missing_file_leaves_the_picture_out_not_the_tab(self):
        run = made_up_run()
        os.remove(os.path.join(run, "chats.jsonl"))
        out, err = tab(run)
        self.assertIn("PICTURE FAILED: day", err)
        self.assertNotIn('id="px-viz-day"', out)
        self.assertIn('id="px-ch-orchestration"', out)

    def test_the_walkthrough_names_no_level(self):
        # the member's copy shows the field's words (Manual / Configured / Automated), never a level code;
        # only the folded 'Every signal' detail keeps the rung codes
        walk = self.out.split('<details class="px-raw">')[0]
        self.assertIsNone(re.search(r"\bL[1-4]\b", re.sub(r"<[^>]+>", " ", walk)))

    def test_the_steps_say_who_does_the_work(self):
        # 11-09: 'Automated' and 'Configured' "sound weird on the frontend"; the steps are by hand → your setup → your own tools
        walk = re.sub(r"<[^>]+>", " ", self.out.split('<details class="px-raw">')[0])
        for word in ("Automated", "Configured", "Manual"):
            self.assertNotIn(word, walk)
        self.assertIn("Your own tools", walk)

    def test_jumps_never_touch_the_hash(self):
        # the page picks its tab from the hash: a link inside the tab would switch tabs
        walk = self.out.split('<details class="px-raw">')[0]
        self.assertNotIn('href="#', walk)


class Lines(unittest.TestCase):
    def test_an_ai_read_reads_as_its_file_and_quote(self):
        sig = {"source": "SCAN → AI", "you": {"fired": True, "value": "yes",
                                              "note": f'{HOME}/work/hooks/check.py:12 — "refuses to stop until the tests pass"'}}
        line = V._proof("", sig, esc)
        self.assertIn("~/work/hooks/check.py:12", line)
        self.assertIn("refuses to stop until the tests pass", line)
        self.assertNotIn(HOME, line)

    def test_a_count_reads_by_its_technique(self):
        sig = {"source": "SCAN", "text": "Your own hooks that run before a tool call; and more", "you": {"fired": True, "value": 8, "note": ""}}
        self.assertEqual(V._proof("own PreToolUse hooks", sig, esc), "own PreToolUse hooks · <b>8×</b>")

    def test_the_track_fills_to_the_highest_rung_seen(self):
        self.assertEqual(V._track({"you": "L3"}).count('class="on"'), 2)
        self.assertEqual(V._track({"you": "not seen"}).count('class="on"'), 0)
        self.assertIn("px-q", V._track({"you": "can't tell"}))


class Day(unittest.TestCase):
    def test_a_project_is_named_without_the_home_folder(self):
        import anatomy_pic_day as D
        self.assertEqual(D._proj(D.HOME_SLUG + "-work-alpha"), "work-alpha")


if __name__ == "__main__":
    unittest.main()
