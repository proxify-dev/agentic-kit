"""Every tool-routing eval case says why it exists, and STORY.md says what the data says.

The rule (plugins/EVALS.md): a case is born from a real session, keeps that session's id and the
person's words, and says what went wrong and what it guards. A case without its story is refused
here; so is a STORY.md edited by hand or left behind after cases.json or runs.jsonl changed.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "evals" / "tool-routing"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

if not (EVAL / "cases.json").is_file():
    pytest.skip("the eval data stays on the owner's machine", allow_module_level=True)

CASES = json.loads((EVAL / "cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_a_case_carries_its_story(case):
    s = case.get("story") or {}
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", s.get("born", "")), "born: the date it was added"
    assert s.get("from") and all(UUID.match(x) for x in s["from"]), "from: the session(s) it came from, full uuids"
    for key in ("asked", "went_wrong", "guards"):
        assert len(s.get(key, "")) > 10, f"{key}: say it in words"
    assert case["ideal_first"] and case["must"], "a case is scored: ideal_first and must"


def test_ids_are_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


def test_runs_log_names_known_cases():
    runs = [json.loads(line) for line in (EVAL / "runs.jsonl").read_text().splitlines() if line.strip()]
    ids = {c["id"] for c in CASES}
    for run in runs:
        assert run["date"] and run["commit"] and run["note"], run
        assert {r["case"] for r in run["rows"]} <= ids, "a logged run names a case cases.json no longer has"


def test_story_md_is_what_the_data_says():
    spec = importlib.util.spec_from_file_location("story", EVAL / "story.py")
    story = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(story)
    assert (EVAL / "STORY.md").read_text() == story.current(), \
        "STORY.md is stale or was edited by hand — run: uv run --no-project python story.py"
