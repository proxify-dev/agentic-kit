#!/usr/bin/env python3
"""The member's page says what Claude Code's own memory folder is missing, in the bar's own sentence (11-18). A folder
that fails builtin_bar (here: 6 notes, all written the same week) is judged "not shown" by code, and the memory part's
reason on the page is that bar sentence ("notes changed in 3 different weeks; yours were changed in 1"), never the old
"only the memory Claude Code keeps by itself", which stays only under BUILTIN_MEMORY = "never". The laptop is built with
the setup-read experiment's own helpers, merged by its own merge(), then rendered by frame.build(member=True).
Run: uv run --no-project --with pyyaml python -I test_builtin_memory_reason_on_page.py   (from this folder)"""
import contextlib
import html
import io
import json
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
READ = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "setup-snapshot", "experiments", "2026-11-11-setup-read"))
sys.path.insert(0, HERE)
sys.path.insert(0, READ)
import frame  # noqa: E402
import setup_read as R  # noqa: E402
from test_setup_read import BUILTIN_LINE, answers, builtin_memory, flat, run, shown  # noqa: E402

OLD = "only the memory Claude Code keeps by itself"
BAR = "notes changed in 3 different weeks; yours were changed in 1"


def member_page_text(lt):
    """The member's page for the laptop `lt` (its run folder already merged), as the text a member reads."""
    with open(os.path.join(lt.run, "v15-result.json"), "w") as f:
        json.dump({"substrate": "t", "evidence": [], "disciplines": [], "pointer": {"level": "L2", "trail": [], "reasons": []},
                   "calibration": {}, "recommendations": [], "more_recommendations": [], "content_gaps": [], "detectors": {},
                   "board": {"rows": []}}, f)
    out = os.path.join(lt.run, "page.html")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        frame.build(lt.run, out, member=True, level=True)
    with open(out, encoding="utf-8") as f:
        page = re.sub(r"<(script|style)\b.*?</\1>", " ", f.read(), flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


class BuiltInMemoryReasonOnThePage(unittest.TestCase):
    def laptop(self):
        lt, r = flat()
        index = builtin_memory(lt, r)       # six notes, all changed today: one week, the bar asks for three
        run(lt, [r])
        answers(lt, "p", r, {"memory": {**shown(index, BUILTIN_LINE), "judged": "shown", "why": "tidy"}})
        R.merge(lt.run, home=lt.home)
        return lt

    def test_a_folder_that_misses_the_bar_gets_the_bars_sentence_on_the_page(self):
        text = member_page_text(self.laptop())
        self.assertIn(BAR, text)
        self.assertNotIn(OLD, text)

    def test_switched_off_the_page_keeps_the_old_sentence(self):
        with mock.patch.object(R, "BUILTIN_MEMORY", "never"):
            text = member_page_text(self.laptop())
        self.assertIn(OLD, text)


if __name__ == "__main__":
    unittest.main()
