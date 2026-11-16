#!/usr/bin/env python3
"""A session a Claude Code scheduled task started: the app records it with the person's entrypoint (claude-desktop),
promptSource "system", turnOrigin "scheduled", and a first message that is a `<scheduled-task name=... file=...>` block.
Nobody typed it, so it is an unattended run that a schedule of theirs started, not a session they typed in.
Run: python3 -I test_scheduled_runs.py   (from this folder)"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inventory  # noqa: E402

SID = "11111111-2222-4333-8444-555555555555"
TASK_FILE = "/home/someone/.claude/scheduled-tasks/nightly-branch-review/SKILL.md"
BASE = {"sessionId": SID, "cwd": "/home/someone/work/shop", "version": "2.1.0", "entrypoint": "claude-desktop",
        "isSidechain": False, "userType": "external"}


def rec(kind, ts, **kw):
    # compact, as Claude Code writes its logs (the reader matches b'"type":"user"' before parsing a line)
    return json.dumps({**BASE, "type": kind, "timestamp": ts, "uuid": f"{kind}-{ts}", **kw}, separators=(",", ":"))


def scheduled_log(name="nightly-branch-review"):
    first = (f'<scheduled-task name="{name}" file="{TASK_FILE}">\nThis is an automated run of a scheduled task. '
             "The user is not present to answer questions.\n\nReview the branches the handoff email lists.\n</scheduled-task>")
    return "\n".join([
        rec("user", "2026-11-15T20:48:12.390Z", message={"role": "user", "content": first}, promptSource="system",
            turnOrigin="scheduled", origin={"kind": "task-notification", "subkind": "scheduled-trigger", "fireReason": "scheduled"},
            permissionMode="bypassPermissions"),
        rec("assistant", "2026-11-15T20:48:20.000Z", message={"role": "assistant", "model": "claude-sonnet-5-5",
                                                               "content": [{"type": "text", "text": "Looking."}]}),
    ]) + "\n"


def typed_log():
    return "\n".join([
        rec("user", "2026-11-15T20:48:12.390Z", message={"role": "user", "content": "please review the branch"}, promptSource="sdk",
            origin={"kind": "human"}, permissionMode="default"),
        rec("assistant", "2026-11-15T20:48:20.000Z", message={"role": "assistant", "model": "claude-sonnet-5-5",
                                                               "content": [{"type": "text", "text": "Looking."}]}),
    ]) + "\n"


class NoJobs:
    """A scheduler that knows no LaunchAgent and no cron line, so only the session's own records can say who started it."""
    jobs = []
    started = inventory.SchedulerStarts.started


class ScheduledRunsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sched-runs-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, text, name="main"):
        p = os.path.join(self.tmp, name, f"{SID}.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        return inventory.read_log((p, 0, False))

    def test_the_record_names_the_task_that_started_it(self):
        r = self.read(scheduled_log())
        self.assertEqual(r["scheduled_task"], "nightly-branch-review")

    def test_a_typed_session_names_no_task(self):
        r = self.read(typed_log())
        self.assertIsNone(r["scheduled_task"])

    def test_a_scheduled_run_is_an_unattended_run_not_a_typed_session(self):
        r = self.read(scheduled_log())
        # the person typed in another session a minute before: the run is no less unattended, a schedule started it
        out, _, _ = inventory.aggregate([r], set(), times=[r["t0"] - 60], scheduler=NoJobs())
        hl = out["headless"]
        self.assertEqual(hl["runs"], 1)
        self.assertEqual(hl["started_by_a_scheduled_job"], 1)
        self.assertEqual(hl["while_away"], 1)
        self.assertEqual(hl["entrypoints"], {"claude-desktop": 1})
        self.assertEqual(out["chats_logged"]["typed_chat_files"], 0)
        self.assertIn("nightly-branch-review", hl["by_template"][0]["template"])

    def test_a_typed_desktop_session_stays_typed(self):
        r = self.read(typed_log())
        out, _, _ = inventory.aggregate([r], {SID}, times=[r["t0"]], scheduler=NoJobs())
        self.assertEqual(out["headless"]["runs"], 0)
        self.assertEqual(out["chats_logged"]["typed_chat_files"], 1)


if __name__ == "__main__":
    unittest.main()
