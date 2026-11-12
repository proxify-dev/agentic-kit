"""The words a count meets: plurals, the thousands separator, a sentence with counts. Imports nothing from the engine, so
the board (which counts what the person has), the level read (things.py), the setup read and every view can all use it
without one depending on another. (11-12: each tab fixed its own "1 sessions", and about 75 of them stayed.)
Standard library only."""
import re
import string


def number(x):
    """A count the run wrote, read as a whole number: an int, a float with nothing after the point, or digits ("12", "1,204").
    None for anything else (nothing there, a list, text such as "12k" or "n/a", a bool, nan): nothing was counted, and the
    caller says "couldn't check" or reads it as none, but never fails. (11-12: a list where a count belonged made a whole tab
    read "This view failed to build".)"""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x) if x.is_integer() else None
    if isinstance(x, str) and re.fullmatch(r"\d{1,3}(,\d{3})+|\d+", x.strip()):
        return int(x.strip().replace(",", ""))
    return None


def count_or_zero(x):
    """number(x), and 0 when there is none. For a count whose absence means "none of them"."""
    return number(x) or 0


def is_not_a_number(x):
    """True for a value that is there but is not a count: a list, "12k", "n/a". Nothing there (None) is not it."""
    return x is not None and number(x) is None


def count(x):
    return f"{x:,}" if isinstance(x, int) and not isinstance(x, bool) else str(x)


def noun(n, one, many=None):
    """The noun alone, for a page that bolds the number: "session" for 1, "sessions" otherwise."""
    return one if n == 1 else (many or one + "s")


def plural(n, one, many=None):
    """"1 session", "2 sessions", "1,204 calls": a count with its noun, in the singular only for exactly one.
    `many` is for a noun that does not take a plain "s" ("set of evals", "sets of evals")."""
    return f"{count(n)} {noun(n, one, many)}"


# the board's `unit` words (what a row's `calls` counts), with the singular each takes beside a 1
UNIT_ONE = {"calls": "call", "runs": "run", "reads": "read", "sessions": "session", "teammates": "teammate",
            "worktrees": "worktree", "launches": "launch", "uses": "use", "times opened": "time opened",
            "headless runs": "headless run", "SDK runs": "SDK run", "recorded runs": "recorded run",
            "folders changed": "folder changed", "hook runs and skill calls": "hook run or skill call"}


def unit_noun(n, unit):
    """A board row's unit beside its count: "1 call", "902 calls"; an unknown unit stays as it is."""
    return UNIT_ONE.get(unit, unit) if n == 1 else unit


class _Say(string.Formatter):
    """A format field's spec is a noun: "{n:session}" -> "3 sessions", "{n:skill or command|skills and commands}"."""

    def format_field(self, value, spec):
        if isinstance(value, int) and not isinstance(value, bool):
            one, _, many = spec.partition("|")
            return plural(value, one, many or None) if one else count(value)
        return super().format_field(value, spec)


def say(template, **fields):
    """A sentence with counts: every "{name:noun}" gets its singular or plural, every bare count its thousands."""
    return _Say().vformat(template, (), fields)


