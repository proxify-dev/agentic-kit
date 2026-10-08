# COPY of plugins/ak/src/ak/groups.py: edit that file, never this one (scripts/copies.py)
"""The command-group class every `ak` group uses — its own module so a verb family that lives
outside cli.py (`ak memory`, src/memory/cli.py) can build its group with it too — and the two
marks a command can carry: `writes` (✎ in every list; never run on a guess) and a hidden old
name (`hidden_alias`: the released spelling keeps working, out of every list)."""
import copy
import inspect

import click as base
import rich_click as click


def writes(cmd):
    """Mark CMD as a verb that changes something: its row in every command list starts with ✎, and
    a name typed one level too high never runs it on a guess (`FuzzyGroup.resolve_command`).
    Put it above the click decorator, so it marks the command click built."""
    cmd.writes = True
    first = inspect.cleandoc(cmd.short_help or cmd.help or "").split("\n\n")[0].replace("\n", " ").strip()
    if not first.startswith("✎"):
        cmd.short_help = f"✎ {first}"
    return cmd


def hidden_alias(group, command, name: str):
    """GROUP answers to NAME with COMMAND, kept out of help, of suggestions and of the
    one-level-too-high lookup: a released spelling (`ak tracer inspect`, `ak memory find`) that
    keeps working after its verb moved. A copy carries the hidden flag, so the visible name stays
    listed; it shares the original's callback, params and (for a group) subcommands."""
    alias = copy.copy(command)
    alias.hidden = True
    if hasattr(alias, "aliases"):  # the short names stay the visible verb's (`s` is `memory search`, not `find`)
        alias.aliases = []
    group.add_command(alias, name=name)
    return alias


def _aliases(cmd) -> list:
    return list(getattr(cmd, "aliases", None) or [])


def nested(group, name: str, depth: int = 3) -> list:
    """Where a command called NAME lives below GROUP, as token paths — so a verb typed one
    level too high (`ak replay`) is found where it lives (`ak sessions replay`). Hidden commands
    are skipped: an old spelling is never a destination."""
    found = []

    def walk(g, trail, d):
        for n, c in sorted((getattr(g, "commands", None) or {}).items()):
            if getattr(c, "hidden", False):
                continue
            if trail and (n == name or name in _aliases(c)):
                found.append([*trail, n])
            if d > 1 and isinstance(c, base.Group):
                walk(c, [*trail, n], d - 1)
    walk(group, [], depth)
    return found


def leaf(group, tokens: list):
    """The command at TOKENS below GROUP, or None."""
    c = group
    for t in tokens:
        c = (getattr(c, "commands", None) or {}).get(t)
        if c is None:
            return None
    return c


class FuzzyGroup(click.RichGroup):
    """Power-user command lookup: exact name → declared alias (rich-click's own
    `aliases=[...]`) → unique prefix (this class) → a verb typed one level too high
    (`resolve_command`). `ak rec`, `place d`, `ak replay` all resolve without listing every
    abbreviation by hand. Every subgroup inherits this automatically (RichGroup's `group_class`
    sentinel re-applies the parent's class). A prefix is matched against the listed commands
    first, so a hidden old name never makes a visible one ambiguous. Ambiguous prefixes fail
    loud with the matches, rather than silently guessing; unknown names fall through to the
    usage error (`help.explain`)."""

    def get_command(self, ctx, cmd_name):
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd
        names = [n for n in self.list_commands(ctx) if n.startswith(cmd_name)]
        shown = [n for n in names if not getattr(self.commands.get(n), "hidden", False)]
        matches = shown or names
        if len(matches) == 1:
            return super().get_command(ctx, matches[0])
        if len(matches) > 1:
            ctx.fail(f"'{cmd_name}' is ambiguous — matches: {', '.join(sorted(matches))}")
        return None

    def resolve_command(self, ctx, args):
        """A name this group lacks, that lives exactly once below it, runs from there: `ak replay x`
        prints `→ ak sessions replay x` and runs it. Only a verb that reads: one marked `writes`
        (or a name found at two places) falls through to the usage error, which names where it lives."""
        name = args[0] if args else None
        if name and not name.startswith("-") and not ctx.resilient_parsing \
                and super().get_command(ctx, name) is None \
                and not any(n.startswith(name) for n in self.list_commands(ctx)):
            hits = nested(self, name)
            if len(hits) == 1 and not getattr(leaf(self, hits[0]), "writes", False):
                path = hits[0]
                shown = " ".join([ctx.command_path, *path, *args[1:]])
                base.echo(f"→ {shown}", err=True)
                return path[0], self.commands[path[0]], [*path[1:], *args[1:]]
        return super().resolve_command(ctx, args)
