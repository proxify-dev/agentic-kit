#!/usr/bin/env python3
"""git_hooks: a hooks folder that exists but cannot be listed is reported as not read, never as "no hooks", and the
repos after it are still scanned. The folder is made unreadable by a patched os.listdir, never by real permissions.
Run: python3 -I test_git_hooks.py   (from this folder; needs git)"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inventory  # noqa: E402

ME = {"emails": {"dana@home.example"}, "names": {"dana stranger"}}
GITENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, env=GITENV, check=True, capture_output=True, text=True)


class GitHooksCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="git-hooks-"))
        self.denied = {}
        real = os.listdir

        def listdir(p="."):
            if os.fspath(p) in self.denied:
                raise self.denied[os.fspath(p)]
            return real(p)
        patcher = mock.patch.object(inventory.os, "listdir", side_effect=listdir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def repo(self, name, hooks=(), hooks_path=None):
        r = os.path.join(self.tmp, name)
        os.makedirs(r)
        git(r, "init", "-q")
        d = os.path.join(r, ".git", "hooks")
        if hooks_path:
            git(r, "config", "core.hooksPath", hooks_path)
            d = os.path.join(r, hooks_path)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for h in hooks:
            with open(os.path.join(d, h), "w") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
        return r, d

    def unreadable(self, name, error=None, **kw):
        r, d = self.repo(name, **kw)
        self.denied[d] = error or PermissionError(1, "Operation not permitted", d)
        return r, d

    def test_listable_check_passes_but_listing_fails(self):
        r, d = self.unreadable("locked")
        self.assertTrue(os.path.isdir(d))
        rows = inventory.git_hooks([r], ME)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["repo"], rows[0]["dir"]), (r, d))

    def test_later_repo_still_scanned(self):
        bad, _ = self.unreadable("locked")
        good, _ = self.repo("open", hooks=["pre-commit"])
        rows = {g["repo"]: g for g in inventory.git_hooks([bad, good], ME)}
        self.assertEqual(rows[good]["hooks"], ["pre-commit"])

    def test_diagnostics_keep_path_and_failure(self):
        bad, d = self.unreadable("locked")
        rows = inventory.git_hooks([bad], ME)
        self.assertEqual(rows[0]["error"], "permission denied")
        self.assertEqual(inventory.hooks_not_read(rows), [{"path": d, "reason": "git hooks: permission denied"}])

    def test_unreadable_is_not_zero_hooks(self):
        bad, _ = self.unreadable("locked")
        rows = inventory.git_hooks([bad], ME)
        self.assertIsNone(rows[0]["hooks"])
        self.assertNotIn("commits", rows[0])   # no ownership read for a folder never listed

    def test_other_os_error_keeps_its_reason(self):
        bad, d = self.unreadable("gone", error=NotADirectoryError(20, "Not a directory", "x"))
        rows = inventory.git_hooks([bad], ME)
        self.assertEqual((rows[0]["error"], rows[0]["hooks"]), ("os error: Not a directory", None))

    def test_accessible_empty_dir_is_left_out(self):
        bad, d = self.unreadable("locked")
        empty, _ = self.repo("empty")
        rows = inventory.git_hooks([bad, empty], ME)
        self.assertEqual([g["repo"] for g in rows], [bad])
        self.assertEqual(inventory.hooks_not_read(rows), [{"path": d, "reason": "git hooks: permission denied"}])

    def test_hooks_found_and_samples_left_out(self):
        bad, _ = self.unreadable("locked")
        good, d = self.repo("open", hooks=["pre-commit", "pre-push", "commit-msg.sample"])
        row = next(g for g in inventory.git_hooks([bad, good], ME) if g["repo"] == good)
        self.assertEqual(sorted(row["hooks"]), ["pre-commit", "pre-push"])
        self.assertEqual((row["dir"], row["hooks_path"]), (d, None))
        self.assertTrue(row["yours"])
        self.assertNotIn("error", row)

    def test_configured_hooks_path_unreadable(self):
        bad, d = self.unreadable("tracked", hooks=["pre-commit"], hooks_path=".githooks")
        good, _ = self.repo("tracked-open", hooks=["pre-commit"], hooks_path=".githooks")
        rows = {g["repo"]: g for g in inventory.git_hooks([bad, good], ME)}
        self.assertEqual((rows[bad]["hooks_path"], rows[bad]["dir"], rows[bad]["hooks"]), (".githooks", d, None))
        self.assertEqual(rows[bad]["error"], "permission denied")
        self.assertEqual(rows[good]["hooks"], ["pre-commit"])


if __name__ == "__main__":
    unittest.main()
