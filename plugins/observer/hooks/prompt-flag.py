#!/usr/bin/env python3
"""ak prompt flag — UserPromptSubmit (memory pass 3).

The one hook that reads what the PERSON typed. CORRECTION is run over the prompt, and a
hit appends one line to the session's flags file:

    ~/.ak/cache/sessions/<session_id>.flags.jsonl   (the kit's, beside the observer's scratch)
    {"at": ..., "flag": "correction", "prompt": "no — that's wrong, the …"}

That is the whole effect. Nothing is denied, nothing is injected: a prompt is
not a tool call, and a person correcting the agent does not need to be told
they did. The reader is the observer at session end
(scripts/summarize-session.py `flags_block`), which lists the flagged prompts
above the transcript so the correction becomes a row instead of a sentence
lost in a 48k-char window — and the observer's prompt (agents/observer.md,
## Corrections) decides what to make of it. Why mechanical: an instruction to
the agent to save a correction is a hope; a regex is a fact.

The pattern is deliberately loose: a false flag costs the observer one line to dismiss,
a missed correction costs the next session the same mistake. Dependency-free and
fail-silent: a flag hook that crashes must cost the prompt nothing.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from vault_root import cache_dir  # noqa: E402
from session_origin import automated  # noqa: E402  (a program's prompt is nobody's correction)
from _brand import env, utf8_stdio  # noqa: E402

CORRECTION = re.compile(
    r"^\s*(no[,.!—–\- ]|that'?s (wrong|not (right|it|what))|not what i (said|meant|asked)|i said |wrong[,.!]"
    r"|you (misread|misunderstood))", re.I)
MAX_EXCERPT = 160


def flags_path(cache: str, session: str) -> str:
    return os.path.join(cache, "sessions", f"{session}.flags.jsonl")


def main() -> None:
    if env("GATE_SKIP") or automated():
        return
    utf8_stdio()
    payload = json.load(sys.stdin)
    prompt = payload.get("prompt") or ""
    if not isinstance(prompt, str) or not prompt.strip():
        return
    session = payload.get("session_id") or ""
    if not session or not CORRECTION.search(prompt):
        return
    path = flags_path(cache_dir(), session)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    at = time.strftime("%Y-%m-%dT%H:%M:%S")
    excerpt = " ".join(prompt.split())[:MAX_EXCERPT]
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"at": at, "flag": "correction", "prompt": excerpt}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)  # a broken flag must never cost the person a prompt
