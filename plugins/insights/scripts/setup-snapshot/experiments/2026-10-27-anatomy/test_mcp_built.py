#!/usr/bin/env python3
"""collect_mcp_built: MCP servers the person built into their own repos, read from the files. A product's MCP endpoint
(a controller answering tools/list and tools/call, a route under /mcp, a runbook, tests) that an outside assistant calls
and no Claude Code config names was never read. A server needs two kinds of signal and code that serves (a handler, or
an SDK dependency with a route); whose it is, ownership.py decides; a folder merely named mcp, or a client of someone
else's server, is nothing.
Run: python3 -I test_mcp_built.py   (from this folder; needs git)"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inventory  # noqa: E402

ME = ("Dana Stranger", "dana@home.example")
LEE = ("Lee Teammate", "lee@work.example")

CONTROLLER = """<?php
namespace App\\Mcp;
class NotesMcpController {
    public function handle(array $message): array {
        return match ($message['method']) {
            'tools/list' => ['tools' => $this->tools->definitions()],
            'tools/call' => $this->callTool($message['params']),
        };
    }
}
"""
ROUTES = """<?php
Route::middleware(['mcp.token'])->group(function () {
    Route::post('/mcp/notes/{token?}', [NotesMcpController::class, 'handle']);
});
"""
COMPOSER = '{"name": "example/app", "require": {"laravel/framework": "^12", "php-mcp/server": "^3"}}\n'
PACKAGE = '{"name": "docs-mcp", "dependencies": {"@modelcontextprotocol/sdk": "^1.0", "zod": "^3"}}\n'
RUNBOOK = "# Notes connector\n\nAn MCP (Model Context Protocol) server built into the API; POST /api/mcp/notes.\n"


def git(repo, *args, who=ME):
    env = {**os.environ, "GIT_AUTHOR_NAME": who[0], "GIT_AUTHOR_EMAIL": who[1], "GIT_COMMITTER_NAME": who[0],
           "GIT_COMMITTER_EMAIL": who[1], "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout


def commit(repo, files, who=ME, msg="x"):
    for rel, text in files.items():
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
    git(repo, "add", "-A", who=who)
    git(repo, "commit", "-q", "-m", msg, "--allow-empty", who=who)


class McpBuiltCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mcp-built-"))   # git resolves /var -> /private/var
        inventory.Identity.emails = {ME[1]}
        inventory.Identity.names = {ME[0].lower()}
        inventory._hist_cache.clear()
        inventory._root_cache.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def repo(self, name):
        r = os.path.join(self.tmp, name)
        os.makedirs(r)
        git(r, "init", "-q")
        return r

    def built(self, root):
        t = Path(root)
        w = inventory.walk_target(t)
        return inventory.collect_mcp_built(t, w)

    def test_laravel_endpoint_with_composer_dependency_is_their_server(self):
        r = self.repo("shop")
        commit(r, {"composer.json": COMPOSER, "app/Mcp/NotesMcpController.php": CONTROLLER, "routes/api.php": ROUTES,
                   "docs/runbooks/notes-mcp-connector.md": RUNBOOK}, ME, "feat: notes MCP connector")
        out = self.built(r)
        self.assertEqual(len(out), 1, out)
        s = out[0]
        self.assertEqual(s["origin"], "own", s)
        self.assertEqual(s["routes"], ["/mcp/notes"])
        self.assertEqual(s["transport"], "http")
        self.assertEqual(sorted(s["signals"]), ["dependency", "doc", "handler", "route"])
        self.assertEqual(s["signals"]["dependency"][0]["package"], "php-mcp/server")
        self.assertIn(os.path.join(r, "app", "Mcp", "NotesMcpController.php"), s["evidence"])
        self.assertIn(os.path.join(r, "routes", "api.php"), s["evidence"])
        self.assertNotIn(os.path.join(r, "composer.json"), s["evidence"])   # a manifest is everyone's: not authorship evidence

    def test_handler_and_route_alone_are_enough_without_a_dependency(self):
        # the protocol written by hand: no MCP package in composer.json
        r = self.repo("shop")
        commit(r, {"composer.json": '{"require": {"laravel/framework": "^12"}}\n', "app/Mcp/NotesMcpController.php": CONTROLLER,
                   "modules/Orders/Routes/api.php": ROUTES})
        out = self.built(r)
        self.assertEqual(len(out), 1, out)
        self.assertEqual(sorted(out[0]["signals"]), ["handler", "route"])

    def test_node_server_with_sdk_dependency_and_handler(self):
        r = self.repo("docs-mcp")
        commit(r, {"package.json": PACKAGE,
                   "src/server.ts": 'import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";\nconst server = new McpServer({});\n'})
        out = self.built(r)
        self.assertEqual(len(out), 1, out)   # the folder is named docs-mcp: src/server.ts is a candidate through it
        self.assertEqual(out[0]["transport"], "stdio")
        self.assertEqual(sorted(out[0]["signals"]), ["dependency", "handler"])

    def test_a_folder_named_mcp_with_nothing_in_it_is_not_a_server(self):
        r = self.repo("app")
        commit(r, {"composer.json": '{"require": {"laravel/framework": "^12"}}\n', "app/Mcp/Notes.php": "<?php\nclass Notes {}\n",
                   "docs/mcp.md": "# MCP\n\nWe might add one some day.\n"})
        self.assertEqual(self.built(r), [])

    def test_one_signal_is_not_enough(self):
        r = self.repo("app")
        commit(r, {"package.json": PACKAGE, "src/index.ts": "console.log('hi')\n"})   # the SDK pulled in, nothing written against it
        self.assertEqual(self.built(r), [])
        r2 = self.repo("app2")
        commit(r2, {"docs/mcp-plans.md": "An MCP server built into the API, some day.\n", "routes/api.php": ROUTES})   # words, no code
        self.assertEqual(self.built(r2), [])

    def test_an_mcp_client_is_not_a_server(self):
        # a program that calls someone else's MCP server: the SDK's client from the same package, or the protocol by hand
        r = self.repo("chat-app")
        commit(r, {"package.json": PACKAGE,
                   "src/mcp/client.ts": 'import { Client } from "@modelcontextprotocol/sdk/client/index.js";\n'
                                        'const c = new Client({name: "app", version: "1"});\nawait c.callTool({name: "search"});\n'})
        self.assertEqual(self.built(r), [])
        r2 = self.repo("bot")
        commit(r2, {"requirements.txt": "mcp>=1.2\n",
                    "bot/mcp_client.py": 'def ask(rpc, name, args):\n    return rpc("tools/call", {"name": name, "arguments": args})\n',
                    "docs/search-mcp-tools.md": "The search service's MCP server exposes these tools.\n"})
        self.assertEqual(self.built(r2), [])

    def test_vendored_mcp_files_do_not_take_the_places_of_the_repos_own(self):
        files = {"composer.json": '{"require": {"laravel/framework": "^12"}}\n', "app/Mcp/NotesMcpController.php": CONTROLLER,
                 "routes/api.php": ROUTES}
        files.update({f"vendor/acme/mcp-kit/src/Part{i:03d}.php": "<?php\nclass Part {}\n" for i in range(inventory.MCP_CAPS["mcp_code"] + 20)})
        r = self.repo("shop")
        commit(r, files)
        out = self.built(r)
        self.assertEqual(len(out), 1, out)
        self.assertIn(os.path.join(r, "app", "Mcp", "NotesMcpController.php"), out[0]["signals"]["handler"])

    def test_a_teammates_server_is_not_theirs(self):
        r = self.repo("shop")
        commit(r, {"composer.json": COMPOSER, "app/Mcp/NotesMcpController.php": CONTROLLER, "routes/api.php": ROUTES}, LEE)
        commit(r, {"app/Mcp/NotesMcpController.php": CONTROLLER + "// tweak\n"}, LEE)
        commit(r, {"routes/api.php": ROUTES + "// note\n"}, ME)
        out = self.built(r)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["origin"], "mixed", out[0]["origin_evidence"])
        self.assertEqual({k: out[0]["git"][k] for k in ("commits", "user_commits")}, {"commits": 3, "user_commits": 1})

    def test_a_copy_of_someone_elses_server_committed_into_their_repo_is_installed(self):
        # ownership.py's rule 4: they committed a byte copy of a version of a file from another repo on this laptop whose
        # commits are someone else's (a clone of the upstream project): installed, not theirs
        server = 'import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";\nconst server = new McpServer({name: "notes"});\n'
        upstream = self.repo("notes-server")
        commit(upstream, {"package.json": PACKAGE, "src/server.ts": server}, LEE)
        r = self.repo("app")
        commit(r, {"package.json": PACKAGE, "tools/mcp/server.ts": server})
        inventory.RECEIPTS = inventory.OWNERSHIP.load_receipts(roots=[upstream, r], use_claude=False)
        try:
            out = self.built(r)
        finally:
            inventory.RECEIPTS = None
        self.assertEqual(len(out), 1, out)
        self.assertEqual(out[0]["origin"], "installed", out[0]["origin_evidence"])

    def test_fixture_and_vendor_copies_are_not_read(self):
        r = self.repo("app")
        commit(r, {"composer.json": COMPOSER, "vendor/php-mcp/server/src/McpServer.php": CONTROLLER,
                   "examples/mcp/routes.php": ROUTES})
        self.assertEqual(self.built(r), [])

    def test_route_paths_drop_parameters(self):
        self.assertEqual(inventory._route_paths("Route::post('/mcp/notes/{token?}', [X::class, 'handle']);"), ["/mcp/notes"])
        self.assertEqual(inventory._route_paths('app.post("/mcp", handler)'), ["/mcp"])
        self.assertEqual(inventory._route_paths("@app.post('/api/mcp/{server_id}')"), ["/api/mcp"])
        self.assertEqual(inventory._route_paths("const url = '/mcp/notes'"), [])   # a string, not a route


if __name__ == "__main__":
    unittest.main()
