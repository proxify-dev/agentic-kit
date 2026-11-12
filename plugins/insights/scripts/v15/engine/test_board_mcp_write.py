#!/usr/bin/env python3
"""r_mcp_write: "MCP servers you wrote" counts the servers inside plugins of theirs (set up for Claude Code, calls in the
logs) and, since 2026-11-12, the servers built into a repo of theirs that no Claude Code config names (anatomy.json
mcp_built): a product's endpoint an external client calls. Those count with 0 calls and the state "built, not used
lately", their evidence files named. A server a teammate wrote (origin mixed or third_party) is not theirs.
Run: python3 -m unittest test_board_mcp_write   (from this folder; standard library only)"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board  # noqa: E402

REPO = "/home/dana/code/shop"
SERVER = {"name": "shop", "repo": REPO, "project": REPO, "routes": ["/mcp/notes"], "transport": "http",
          "signals": {"handler": [REPO + "/app/Domains/Shared/Controllers/Mcp/NotesMcpController.php"],
                      "route": [REPO + "/app/Domains/Shared/Routes/api.php"], "doc": [REPO + "/docs/runbooks/notes-mcp-connector.md"]},
          "evidence": [REPO + "/app/Domains/Shared/Controllers/Mcp/NotesMcpController.php", REPO + "/app/Domains/Shared/Routes/api.php",
                       REPO + "/docs/runbooks/notes-mcp-connector.md"],
          "origin": "own", "origin_evidence": "git: 5 of 5 commits that touched the evidence files are by an identity email",
          "git": {"commits": 5, "user_commits": 5}}


class FakeRun:
    def __init__(self, anatomy, inv=None):
        self.anatomy, self.inv, self.cheap, self.missing, self.logs_ok, self.dir = anatomy, inv or {}, [], [], bool(inv), "."


def facts(anatomy, inv=None):
    return board.Facts(FakeRun(anatomy, inv))


class McpWriteCase(unittest.TestCase):
    def test_a_server_built_into_their_product_counts_with_no_calls(self):
        r = board.r_mcp_write(facts({"mcp_built": [SERVER], "mcp_servers": [], "plugins": {}}))
        self.assertEqual(r["state"], board.BUILT_IDLE)
        self.assertEqual((r["items"], r["calls"]), (1, 0))
        self.assertEqual(r["examples"], ["shop /mcp/notes"])
        self.assertIn("1 MCP server built into your product (shop: /mcp/notes); Claude Code never called it", r["why"])
        self.assertEqual(r["projects"], 1)
        item = r["list"][0]
        self.assertEqual((item["name"], item["kind"], item["uses"]), ("shop /mcp/notes", "product", 0))
        self.assertIn("app/Domains/Shared/Controllers/Mcp/NotesMcpController.php", item["about"])
        self.assertIn("Claude Code never called it", item["about"])
        self.assertEqual(item["evidence"], SERVER["evidence"])
        self.assertIn("mcp_built", r["source"])

    def test_a_teammates_server_is_not_theirs(self):
        r = board.r_mcp_write(facts({"mcp_built": [dict(SERVER, origin="mixed")], "mcp_servers": [], "plugins": {}}))
        self.assertEqual(r["state"], board.NOT_SEEN)
        self.assertEqual(r["items"], 0)

    def test_two_checkouts_of_one_repo_are_one_server(self):
        wt = REPO + "/.claude/worktrees/x"
        copy = {**SERVER, "repo": wt, "evidence": [e.replace(REPO, wt) for e in SERVER["evidence"]]}
        r = board.r_mcp_write(facts({"mcp_built": [SERVER, copy], "mcp_servers": [], "plugins": {}}))
        self.assertEqual(r["items"], 1)

    def test_plugin_servers_and_product_servers_sit_side_by_side(self):
        plugin = "/home/dana/code/kit/plugins/docs"
        anatomy = {"mcp_built": [SERVER], "artifacts": [],
                   "plugins": {"dir_loaded": [{"name": "docs", "path": plugin, "origin": "own", "contents": {"mcp_servers": ["docs"]}}]},
                   "mcp_servers": [{"name": "docs", "transport": "stdio", "entry": plugin + "/server.py"}]}
        inv = {"tools": {"typed_chats": {"by_mcp_server": [{"what": "docs", "calls": 7}]}}}
        r = board.r_mcp_write(facts(anatomy, inv))
        self.assertEqual(r["state"], board.BUILT_USED)
        self.assertEqual((r["items"], r["calls"]), (2, 7))
        self.assertEqual([x["name"] for x in r["list"]], ["docs", "shop /mcp/notes"])
        self.assertIn("1 MCP server inside plugins of yours; 7 calls in the window; 1 MCP server built into your product", r["why"])

    def test_a_stdio_server_in_a_repo_is_named_by_the_repo(self):
        s = {**SERVER, "routes": [], "transport": "stdio"}
        r = board.r_mcp_write(facts({"mcp_built": [s], "mcp_servers": [], "plugins": {}}))
        self.assertEqual(r["examples"], ["shop"])
        self.assertIn("(shop); Claude Code never called it", r["why"])


if __name__ == "__main__":
    unittest.main()
