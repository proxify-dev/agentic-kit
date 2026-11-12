"""The words a count meets: plurals, the thousands separator, a sentence with counts. Imports nothing from the engine, so
the board (which counts what the person has), the level read (things.py), the setup read and every view can all use it
without one depending on another. (11-12: each tab fixed its own "1 sessions", and about 75 of them stayed.)
Standard library only."""
import string


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


