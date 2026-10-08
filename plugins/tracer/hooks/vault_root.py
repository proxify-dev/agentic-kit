# COPY of plugins/vault/src/vault/vault_root.py: edit that file, never this one (scripts/copies.py)
"""Which vault is this session's vault? The one resolver, for hooks and package alike.

Twelve files each carried `AK_PATH or ~/.ak`, so the vault was a machine-wide
constant: one store per user, wherever the session was launched. That is the thing
being undone here. A `.ak/` committed inside a repo is that repo's vault, and a
session started anywhere under it writes there — the plugin becomes portable without
anyone exporting an env var per project.

NEAREST WINS. The walk goes up from the session cwd and the FIRST vault it meets is
the root, because specificity beats generality the way a project note beats a global
one: work done inside `<repo>` belongs to `<repo>/.ak`, and `~/.ak` is merely
the last ancestor everyone shares. An ancestor that IS a vault counts as well as one
that CONTAINS a `.ak/` — that is what makes a session inside `~/.ak/collections`
resolve to `~/.ak/vault` rather than inventing `~/.ak/vault/.ak`.

$AK_PATH SHORT-CIRCUITS. It is the explicit answer to exactly this question, so it
returns alone: no walk, no home appended. Tests, `ak init`, the summariser's
detached child and any caller pinning a child process to the vault its parent already
resolved all depend on that being a full stop rather than a preference.

$AK_HOME MOVES THE LAST RUNG. The global vault lives at `~/.ak/vault` unless $AK_HOME
names another kit home; the walk still runs, so a project's scope and its spine stay in the
chain, and only the tail changes. `~/.ak` is the home rung or nothing — the walk never
counts it as a project scope — so a sandbox under $HOME with its own home never sees the
person's real global kit (`ak memory demo`: its store, ledger and notes stay apart).

THE LIST IS THE POINT, not just its head. `brain_root()` takes the first scope because
today's writers own one vault, but `scopes()` returns the whole chain nearest-first so
v2 recall can read ACROSS it — a note in `<repo>/.ak` and one in `~/.ak` answering
the same query, the nearer one ranked first. Anything that searches rather than writes
should consume the list.

stdlib only: hooks run under whatever bare python3 is on PATH. The walk itself is the engine's
(engine/ladder.py), which a hook's copy of this file carries beside it.
`pathlib` is imported lazily inside `brain_root()` on purpose — a PreToolUse hook may run
before EVERY Bash call on the machine and measured pathlib at ~4.5ms of its ~28ms
budget, so it calls `scopes()` and never pays for a Path it would only str() again.
"""
try:  # the src package's copy is `vault.vault_root`; a hook's is a top-level module beside its own _brand.py
    from .engine.ladder import (VAULT_MARKER, cache_dir, db_dir, is_vault, kit_home, ladder,  # noqa: F401
                                layout_file, moves_dir)  # (all but ladder: this module's API — the kit's layout)
except ImportError:  # and its own ladder.py (the vault engine's, mirrored with it)
    from ladder import (VAULT_MARKER, cache_dir, db_dir, is_vault, kit_home, ladder,  # noqa: F401
                        layout_file, moves_dir)


def scopes(cwd=None) -> list:
    """Every vault this cwd can see, nearest first, realpath'd and deduped (a preset of engine/ladder.py).
    The global kit is always last and always present — even when it does not exist yet, because
    `ak init` creates it and a caller needs somewhere to point at."""
    return [r.path for r in ladder(cwd, anchor=None, project_dir=None, own_dir=False, memory_rungs=False,
                                   legacy_ak=True, global_home=True, env_short_circuit=True, existing=False)]


def brain_root(cwd=None):
    """The vault that owns this cwd, as a Path. The head of `scopes()`."""
    from pathlib import Path  # lazy — see the module docstring's budget note
    return Path(scopes(cwd)[0])


def home_root() -> str:
    """The global vault, as a str: `$AK_PATH`, else the kit home's `vault/` — the tail of every chain, and
    the same answer from any cwd. Its notes are what every project shares. The kit's state — the
    observer's one store, the ledgers, the caches — is beside it, never in it: db_dir(), cache_dir()."""
    return scopes()[-1]
