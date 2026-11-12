#!/usr/bin/env python3
"""The browser's find-in-page (Cmd/Ctrl+F) must reach every word on every page the frame builds (11-12: the owner could not search
the system map). Two causes: a tab that is not showing was `display:none`, which find skips, and a side panel's text was JSON turned
into a panel only when clicked, so it was not in the document at all. Now every tab and every panel is in the document, hidden with
`hidden="until-found"` (find searches it and fires `beforematch`, where the page switches to that tab or opens that panel); a browser
without until-found (Safari, Firefox) gets "Show everything", one control that lays every tab and panel out on one page.

A test cannot press Cmd+F, so this checks the structure: what is in the document on load, what hides a tab, which handlers exist, and
that nothing listens for the F key. What it cannot check (the browser's own find, Safari and Firefox) is in the issue note
memory/pages/find-in-page-reaches-every-tab-and-panel.md.
Made-up run folder; standard library only. Run: python3 test_find.py   (from this folder)"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_frame_member as member  # noqa: E402

KEY_HANDLER = re.compile(r"addEventListener\(\s*['\"]keydown['\"]")


def scripts(page):
    return [m.group(1) for m in re.finditer(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>", page, re.S)]


def check_find_structure(case, page, label):
    """The structure every page the frame builds must have; `case` is the TestCase whose asserts report."""
    tags = re.findall(r'<section class="view [^"]*" id="v-[^"]*"[^>]*>', page)
    case.assertTrue(tags, f"{label}: no tab in the page")
    case.assertEqual([t for t in tags if 'hidden="until-found"' not in t], [], f"{label}: a tab is hidden some other way")
    case.assertEqual(len(tags), len(re.findall(r'<section class="view ', page)))
    css = "".join(re.findall(r"<style>(.*?)</style>", page, re.S))
    case.assertIsNone(re.search(r"\.view\s*\{[^}]*display\s*:\s*none", css), f"{label}: CSS hides a tab with display:none (find skips it)")
    js = "\n".join(scripts(page))
    # a match in a tab that is not showing switches to that tab
    handlers = [js[m.start():][:400] for m in re.finditer(r"addEventListener\('beforematch'", js)]
    case.assertTrue(any("showView(" in h for h in handlers), f"{label}: no beforematch handler that switches the tab")
    if 'id="kp"' in page:   # and a match in a panel opens that panel
        case.assertTrue(any("show(p)" in h for h in handlers), f"{label}: no beforematch handler that opens the panel")
    # one control lays everything out, for browsers without until-found
    case.assertIn('id="rp-all"', page, f"{label}: no Show everything control")
    case.assertIn("Show everything", page)
    case.assertIn("kpFlat", js) if 'id="kp"' in page else None
    # nothing listens for the F key, and no key handler stops a key it does not name
    case.assertIsNone(re.search(r"""key(?:\.toLowerCase\(\))?\s*===?\s*['"]f['"]|KeyF|keyCode\s*===?\s*70|which\s*===?\s*70""", js, re.I),
                      f"{label}: a script tests for the F key")
    for m in KEY_HANDLER.finditer(js):
        region = js[m.end():][:900]
        region = region[:region.find("addEventListener(", 1)] if "addEventListener(" in region[1:] else region
        for p in re.finditer(r"preventDefault\(\)", region):
            case.assertRegex(region[:p.start()], r"\.key\s*(===|!==)|\.key\.toLowerCase\(\)\s*===",
                             f"{label}: a keydown handler stops a key without naming it: {region[:120]!r}")


class FindInPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = {"member's page": member.build(member=True), "Proxify's page": member.build(member=False)}

    def test_every_tab_is_in_the_page_and_only_hidden_until_found(self):
        for label, page in self.pages.items():
            with self.subTest(label):
                check_find_structure(self, page, label)

    def test_the_text_of_a_tab_that_is_not_showing_is_in_the_page(self):
        for label, page in self.pages.items():
            tabs = re.findall(r'<section class="view v-[^"]*" id="v-([^"]*)"', page)
            for name in tabs[1:]:
                body = re.search(rf'id="v-{name}"[^>]*>(.*?)</section>', page, re.S)
                self.assertTrue(body and len(re.sub(r"<[^>]+>", "", body.group(1)).strip()) > 20, f"{label}: tab {name} has no text in the page")

    def test_the_members_page_has_no_panel_and_the_staff_page_holds_every_panel_as_text(self):
        self.assertNotIn('id="kp"', self.pages["member's page"])
        staff = self.pages["Proxify's page"]
        self.assertNotIn('id="kp-data"', staff, "panels are JSON built only when clicked: find never sees them")
        panels = re.findall(r'<div class="kp-p" data-panel="([^"]+)" hidden="until-found">', staff)
        self.assertGreater(len(panels), 100)
        self.assertEqual(len(panels), len(set(panels)))
        self.assertIn('<aside class="kp" id="kp" hidden="until-found"', staff)


if __name__ == "__main__":
    unittest.main()
