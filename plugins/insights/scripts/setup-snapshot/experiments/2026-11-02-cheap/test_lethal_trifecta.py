#!/usr/bin/env python3
"""lethal_trifecta: which tool calls make each of the three legs. Private data is a secrets file or a connected service,
read from the words in an MCP server's name (its tool's name when the server is an opaque id, as a claude.ai connector
is); outside text is a web fetch, a browser, a crawler or an issue read; a tool the Claude app itself mounts is neither.
Run: python3 -I test_lethal_trifecta.py   (from this folder)"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import problems  # noqa: E402

PUSH = {"name": "Bash", "key": "git push origin main"}
FETCH = {"name": "WebFetch", "key": '{"url": "https://example.com/post"}'}
ENV = {"name": "Read", "key": "/w/app/.env"}


def session(*calls, chat="abcdef1234567890"):
    return {"chat": chat, "cwd": "/w/app", "calls": [dict(c) for c in calls]}


def open_chats(*calls):
    return problems.lethal_trifecta([session(*calls)], settings=[], hooks=[])["open"]


class Legs(unittest.TestCase):
    def test_env_read_web_fetch_and_push_still_count(self):
        self.assertEqual(open_chats(ENV, FETCH, PUSH), ["abcdef12"])

    def test_two_legs_are_not_the_trifecta(self):
        self.assertEqual(open_chats(ENV, PUSH), [])
        self.assertEqual(open_chats(FETCH, PUSH), [])

    def test_a_named_database_server_is_private_data(self):
        self.assertEqual(open_chats({"name": "mcp__postgres__query", "key": "{}"}, FETCH, PUSH), ["abcdef12"])

    def test_a_connector_named_by_an_opaque_id_is_read_from_its_tool_name(self):
        mail = {"name": "mcp__70e345d2-2b14-434d-b2eb-2380ab0d4953__search_threads", "key": '{"q": "invoice"}'}
        self.assertEqual(open_chats(mail, FETCH, PUSH), ["abcdef12"])

    def test_a_tool_the_claude_app_mounts_is_not_private_data(self):
        app = {"name": "mcp__ccd_session_mgmt__send_message", "key": "{}"}
        self.assertEqual(open_chats(app, FETCH, PUSH), [])

    def test_a_credentials_file_is_private_data(self):
        for path in ("/Users/x/.aws/credentials", "/home/x/.ssh/id_ed25519", "/w/app/service-account.json", "/home/x/.netrc"):
            with self.subTest(path=path):
                self.assertEqual(open_chats({"name": "Read", "key": path}, FETCH, PUSH), ["abcdef12"])

    def test_a_browser_server_is_outside_text(self):
        nav = {"name": "mcp__playwright__browser_navigate", "key": '{"url": "https://example.com"}'}
        self.assertEqual(open_chats(ENV, nav, PUSH), ["abcdef12"])

    def test_an_issue_read_from_the_shell_is_outside_text(self):
        issue = {"name": "Bash", "key": "gh issue view 42 --repo example/app"}
        self.assertEqual(open_chats(ENV, issue, PUSH), ["abcdef12"])

    def test_a_shell_download_is_outside_text(self):
        self.assertEqual(open_chats(ENV, {"name": "Bash", "key": "wget https://example.com/a.tar.gz"}, PUSH), ["abcdef12"])

    def test_a_connected_service_is_not_outside_text_by_itself(self):
        slack = {"name": "mcp__slack__conversations_history", "key": "{}"}
        self.assertEqual(open_chats(slack, PUSH), [])


class Words(unittest.TestCase):
    def test_private_words_match_whole_words_of_the_server_name(self):
        self.assertTrue(problems.private_leg("mcp__my-notion-notes__search"))
        self.assertFalse(problems.private_leg("mcp__sandbox__run"), "box inside sandbox is not the Box service")
        self.assertFalse(problems.private_leg("mcp__pager__next"), "pg inside pager is not Postgres")

    def test_a_named_servers_tool_name_does_not_decide(self):
        self.assertFalse(problems.private_leg("mcp__ccd_turn__update_plan_message"))
        self.assertTrue(problems.outside_leg("mcp__Claude_Browser__read_page"))

    def test_a_word_in_a_command_is_not_a_secrets_file(self):
        for cmd in ('grep -rn "import.meta.env" frontend/ | head', 'git commit -q -m "agents keep secrets out of logs"',
                    'vercel env ls 2>&1 | grep -v -i "token\\|secret"'):
            with self.subTest(cmd=cmd):
                self.assertFalse(problems.private_leg("Bash " + cmd))

    def test_a_secrets_file_by_its_path_is_private_data(self):
        for cmd in ("cat .env", "cat frontend/.env.local", "[ -f env/secrets.dev.env ] || printf x > env/secrets.dev.env",
                    "python3 -c \"print(open('config/secrets.yaml').read())\"", "ls /run/secrets/"):
            with self.subTest(cmd=cmd):
                self.assertTrue(problems.private_leg("Bash " + cmd))

    def test_a_download_from_a_local_dev_server_is_not_outside_text(self):
        for cmd in ("curl -s http://localhost:3000/api/health", "curl -s -m 20 http://api.app.test/a/b", "curl http://127.0.0.1:8080/",
                    "wget -q http://0.0.0.0:5173/index.html"):
            with self.subTest(cmd=cmd):
                self.assertFalse(problems.outside_leg("Bash " + cmd))
        self.assertTrue(problems.outside_leg("Bash curl -sL https://example.com/releases.json | jq ."))

    def test_the_first_matching_call_is_named_for_the_moment(self):
        c = session({"name": "mcp__ccd_turn__wrap_up", "key": "{}"}, {"name": "mcp__grafana-prod__query_prometheus", "key": "{}"})
        self.assertEqual(problems.first_leg(c, problems.private_leg), "mcp__grafana-prod__query_prometheus {}")


if __name__ == "__main__":
    unittest.main()
