# COPY of plugins/ak/src/ak/session_origin.py: edit that file, never this one (scripts/copies.py)
"""Who started this session: a person, or a program? One answer for every ak plugin's hooks.

Claude Code hands every hook CLAUDE_CODE_ENTRYPOINT in its environment. A person is `cli`
(a terminal), `claude-desktop`, or `sdk-ts` (an app on the TypeScript SDK that a person types into); `sdk-cli` is `claude -p`
and `sdk-py` the Python SDK — summarizers, evals, probes, CI. Over 6,685 transcripts on one
machine (2026-09-22) the rule kept all 617 human sessions and skipped 6,050 of the 6,068
automated ones. A missing variable is a session this cannot place, and counts as a person's,
which is how every hook behaved before the rule existed.

What a program's session gets from the kit plugins: the short lines that teach the tools,
nothing more — no digest, no project block, no hints about past work, and no file written or
moved (no vault planted, no records seeded, no index run). AK_CAPTURE=1 counts an
automated session as a person's: a `claude -p` someone wants treated like their own.
EVAL_AK_CAPTURE=1 is the same switch under the only name `claude plugin eval` passes to a case.

Canonical here (the ak package). Copies ride in the other plugins' hooks/ folders, written and checked
by scripts/copies.py — edit this file, then `uv run python scripts/copies.py render`.
"""
import os

try:  # the src package's copy is `ak.session_origin`; a hook's is a top-level module beside its own _brand.py
    from ._brand import env, env_name
except ImportError:
    from _brand import env, env_name

HUMAN_ENTRYPOINTS = frozenset({"cli", "claude-desktop", "sdk-ts"})


def automated() -> str:
    """The entrypoint when a program started this session, else "" (a person, or unknown)."""
    if "1" in (env("CAPTURE"), os.environ.get("EVAL_" + env_name("CAPTURE"))):
        return ""
    ep = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    return ep if ep and ep not in HUMAN_ENTRYPOINTS else ""
