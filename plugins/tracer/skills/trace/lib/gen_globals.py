#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    skill_dir = Path(__file__).resolve().parents[1]
    output = skill_dir / "schema" / "report.globals.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "mode": "uuid",
        "session_uuid": "d18a749f-9df6-4869-b63a-1f120ce4ed99",
        "query": "webhook retry backoff",
        "dialogue_limit": 30,
        "query_limit": 5,
        "observer_limit": 15,
        "include_structure": True,
        "include_observer": True,
        "dialogue_full": False,
        "include_delegated": True,
        "include_artifacts": True,
        "resolve_needed_message": "RESOLVE_NEEDED: pass a session UUID, slug, date, or topic phrase.",
        "search_instruction": (
            "INSTRUCTION: If one candidate clearly matches, use its session UUID. "
            "If multiple, list top 3 with date + user preview and ask the user."
        ),
        "title_text": "",
        "index_text": "",
        "header_text": "",
        "dialogue_text": "",
        "structure_text": "",
        "delegated_text": "",
        "artifacts_text": "",
        "observer_text": "",
        "search_text": "",
    }

    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
