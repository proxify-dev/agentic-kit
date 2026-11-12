#!/usr/bin/env python3
"""The one place the scan reads the calendar clock. Every window ("the last 30 days"), every session or file age and every
"built at" stamp the scan, the page and the made-up laptops of the tests compute comes from `now()`; nothing else in what
ships calls `time.time()` for a date. Elapsed time (timings, deadlines) uses `time.monotonic()`, which is no date.

    AGENTIC_SETUP_NOW   unset: the real clock. Set to epoch seconds (1762344000) or an ISO time (2026-11-05T12:00:00Z; no
                        zone means UTC): every step of a run, started as its own process, reads that moment as "now".
                        The tests set it so a made-up laptop and the scan of it agree on one moment on any day.
                        A member never sets it.

`real()` is the machine's clock whatever the variable says, for the one thing that is about the real world: how old a cache
file we wrote is (its modification time is real).
Why: the stranger-shaped test dated its sessions from the real clock, so a count that read 20 days when its snapshot was frozen
read 21 once the clock passed the hour that moved one session across midnight (11-12), and the release check failed by the date."""
import os
import time
from datetime import datetime, timezone

ENV = "AGENTIC_SETUP_NOW"


def real():
    return time.time()


def now():
    """Seconds since the epoch, as `time.time()` returns them, or the moment AGENTIC_SETUP_NOW names."""
    v = os.environ.get(ENV, "").strip()
    if not v:
        return real()
    try:
        return float(v)
    except ValueError:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()
