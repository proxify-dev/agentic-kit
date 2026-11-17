"""The words a count meets: plurals, the thousands separator, a sentence with counts. Imports nothing from the engine, so
the board (which counts what the person has), the level read (things.py), the setup read and every view can all use it
without one depending on another. (11-12: each tab fixed its own "1 sessions", and about 75 of them stayed.)
Standard library only."""
import json
import os
import re
import string


def command_of(here, levels=6):
    """The slash command a person types to run this tool, from the plugin it ships in: the nearest
    `.claude-plugin/plugin.json` above `here` names the plugin ("insights" -> "/insights:my-agentic-setup"). Outside a
    plugin (the lab copy): "/my-agentic-setup". The page and run.py tell a member how to change what was reviewed in
    these words, never as a script call (11-17)."""
    d = os.path.dirname(os.path.abspath(here))
    for _ in range(levels):
        try:
            with open(os.path.join(d, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
                name = json.load(f).get("name")
            if isinstance(name, str) and re.fullmatch(r"[\w.-]+", name):
                return f"/{name}:my-agentic-setup"
        except (OSError, ValueError, AttributeError):
            pass
        d = os.path.dirname(d)
    return "/my-agentic-setup"


COMMAND = command_of(__file__)


def confirmed_said(n):
    """The projects --only took when they are exactly the busiest (picked_by "confirmed"): "your busiest 3, which you
    confirmed". The page and the dry run say this, never "chosen by you", for a list the person only accepted."""
    return ("your busiest project" if n == 1 else f"your busiest {n}") + ", which you confirmed"


def change_pick_said(chosen=False):
    """How to change which projects are reviewed, in the words a member types. One sentence for the page, run.py's list
    and its dry run."""
    return (f"To have other projects reviewed, run {COMMAND} again and choose them when it asks, or name them at once: "
            f"{COMMAND} --only NAME, once per project (up to 5); --skip NAME leaves one out."
            if not chosen else
            f"You chose the projects above. To review others, run {COMMAND} again and choose them when it asks, or name them "
            f"at once: {COMMAND} --only NAME, once per project (up to 5).")


# What this laptop cannot see, said once. Every tab that prints "Not seen" (or a "looked, not found") prints this sentence, so
# checks that run in CI after a push, routines on claude.ai and other machines never read as "none". The states themselves
# ("Not seen", "Couldn't check", a question for the call) keep their meanings: this is about places, they are about habits.
CANT_SEE = ("This page reads this laptop only. It can't see checks that run in CI after a push, routines on claude.ai, or work "
            "on another computer or a client's machine, so \"Not seen\" never means \"not there\". An evaluation call can add them.")


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


