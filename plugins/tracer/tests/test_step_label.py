"""A call's label: the model's description first, the raw command marked "$", else what it touched.

step_label_cases.json is shared with the traces index (app's selftest:traces reads it too), so
the line `ak sessions show` draws and the `what` column `ak trace` lists cannot drift apart.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracer.step_label import step_label

CASES = json.loads((Path(__file__).parent / "step_label_cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[f"{c['tool']}:{c['label'] or 'none'}" for c in CASES])
def test_the_shared_cases(case):
    assert step_label(case["tool"], case["input"]) == case["label"]


def test_a_path_under_home_reads_from_tilde(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert step_label("Read", {"file_path": f"{tmp_path}/repo/a.py"}) == "~/repo/a.py"
    # a sibling that merely shares the prefix is not under home
    assert step_label("Read", {"file_path": f"{tmp_path}x/a.py"}) == f"{tmp_path}x/a.py"


def test_a_long_description_is_cut_at_the_cap():
    label = step_label("Bash", {"command": "ls", "description": "word " * 100}, cap=40)
    assert len(label) == 40 and label.endswith("…")
