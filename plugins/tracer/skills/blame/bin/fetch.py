#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
# The engine (lib/sources.py) lives in the sibling `trace` skill; blame reuses
# its trace_file() provider so both skills share one ak-engine wiring.
# (It pointed at `replay`, the skill's old name, and every blame died on import.)
TRACE_SKILL_DIR = SKILL_DIR.parent / "trace"
if str(TRACE_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(TRACE_SKILL_DIR))

from lib.sources import trace_file  # noqa: E402


HINTS = (
    "Next: `/tracer:trace <uuid>` for the full session recap · "
    "`trace turn <uuid>-<turn>` for one turn's steps (`--open N` opens them whole)."
)


def main() -> int:
    path = " ".join(sys.argv[1:]).strip()
    if not path:
        print("RESOLVE_NEEDED: pass a file path or basename to blame.")
        return 0

    print(trace_file(path, limit=20, cwd=str(Path.cwd())))
    print()
    print(HINTS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
