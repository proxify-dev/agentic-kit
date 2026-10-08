"""home — ak's global home, ~/.ak: the layout-2 skeleton, planted by ak itself at boot.

The home is ak's, not any plugin's: the vault keeps its notes in <home>/vault, the observer its store
in <home>/db, and more will come. So ak plants the folders every one of them expects, from the layout
declared once in move/plan.py, before any plugin's `brain_boot` runs:

    <home>/vault/  <home>/db/  <home>/cache/  <home>/moves/  <home>/layout  {"layout": 2}

It never overwrites and never migrates. Every folder is a mkdir that keeps what is there, the stamp
is written only when absent, and a home that holds anything but no stamp (layout 1: notes and
`.brain-state/`/`.store/` at its root) is left exactly as it is: scripts/migrate/4-home-layout.py
moves it, and `ak doctor` says so. A few stats when the home is already planted — it runs on every `ak` command.
"""
from __future__ import annotations

import json
import os

from ak.move import plan as P

# What a layout-1 home held at its root beside the notes (`ak doctor` names them).
LAYOUT_1_STATE = (".store", ".brain-state")


def on_layout_1(home: str = "", vault: str = "") -> bool:
    """HOME holds something and no `layout` stamp: a home from before layout 2 (notes, `.brain-state/`,
    `.store/` at its root) or one never cut over — scripts/migrate/4-home-layout.py's, not ours to plant.
    A home that IS the vault ($AK_PATH alone, a sandbox) holds its notes at the root by design and is
    never layout 1; an empty folder holds nothing to plant over."""
    home = home or P.kit_home()
    vault = vault or _vault_of(home)
    if os.path.exists(P.layout_file(home)) or os.path.realpath(vault) == os.path.realpath(home):
        return False
    try:
        with os.scandir(home) as it:
            return any(it)
    except OSError:  # not there yet
        return False


def _vault_of(home: str) -> str:
    """The global vault of HOME: the environment's ($AK_PATH) for the kit home itself, else <home>/vault."""
    if os.path.realpath(home) == os.path.realpath(P.kit_home()):
        return P.global_vault()
    return os.path.join(home, "vault")


def plant(home: str = "", vault: str = "") -> list[str]:
    """Ensure the layout-2 skeleton at HOME (default: the kit home); VAULT names its vault folder when it
    is not <home>/vault. Returns what was created — [] when it was all there, or HOME is on layout 1."""
    home = home or P.kit_home()
    vault = vault or _vault_of(home)
    if on_layout_1(home, vault):
        return []
    own = os.path.realpath(home) == os.path.realpath(P.kit_home())
    dirs = [vault, P.db_dir(home) if own else os.path.join(home, "db"), P.cache_dir(home), P.moves_dir(home)]
    made = []
    for d in dirs:
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            made.append(d)
    stamp = P.layout_file(home)
    if not os.path.exists(stamp):
        with open(stamp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"layout": P.LAYOUT}) + "\n")
        made.append(stamp)
    return made
