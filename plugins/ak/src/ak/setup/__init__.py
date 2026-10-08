"""setup — the one door that installs the kit: `ak setup`.

Detect the machine, pick components (the ones this machine can't run greyed out, with the reason),
preview every settings and rc change as a diff, apply in dependency order, verify. Every write is
backed up first into one run folder, so `ak setup undo` puts it back.

    machine.py     detect() -> Machine: the OS, the binaries and their versions, what is installed
    catalog.py     COMPONENTS (read from the kit on disk, else kit.json), blocked(), order(), with_deps()
    kit.json       the public kit, for an ak with no kit on disk (written by scripts/kit.py render)
    source.py      where the kit comes from: the release clone, the repo URL
    claude_cli.py  the real `claude` binary, run the way the migrations run it
    settings.py    user settings.json: merge keys, keep the rest, a diff before the write
    backup.py      Run: one folder per run under <kit home>/db/setup/<id>/, the backups, undo
    engine.py      plan(machine, ids) -> Plan, apply(plan, run, emit), verify(machine)
    cli.py         the click group: the plain/json door, `status`, `undo`
    tui.py         the Textual app (imported only when a person runs it at a terminal)

Self-contained: stdlib, ak.ui, ak._brand, ak.move.{plan,ops}. Never imports vault or memory —
under `uvx --from git+…#subdirectory=plugins/ak` only this package exists.
"""
