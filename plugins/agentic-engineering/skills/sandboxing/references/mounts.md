# Bringing real data into a sandbox

How a path from your machine gets in (step 3 of [the sandboxing steps](../SKILL.md)), and how to read back what changed (step 5).

## What are you trying to do?

- [Choosing the mode for a path](#three-modes)
- [Making a snapshot](#making-a-snapshot): macOS, Linux
- [Chat histories, settings, a plugin's store](#common-paths)
- [Reading what changed](#reading-what-changed)

## Three modes

| Mode | How | The plugin sees | It can write | Your original |
|---|---|---|---|---|
| **snapshot** | a copy-on-write clone, mounted writable | the data as it was at start | yes, into the copy | untouched |
| **read-only** | your folder, mounted `:ro` | live data | no | untouched |
| **live** | your folder, mounted writable | live data | yes, into the original | changed |

Use **snapshot** by default. Use **read-only** only for files nothing writes to. Never use **live** for a trial.

Read-only fails in two common cases:

- **SQLite in WAL mode** writes even when it only reads: it needs its `-wal` and `-shm` side files. A read-only mount gives `attempt to write a readonly database`. (`?immutable=1` in the URI works around it, but the plugin opens the file, not you.)
- **A folder `claude` itself writes to.** Mount your history read-only at `~/.claude/projects` and the `claude` inside the box can't save its own session.

## Making a snapshot

A copy-on-write clone shares disk blocks with the original until one side writes. It costs no extra disk, and seconds of time.

```sh
# macOS (APFS)
cp -c -R ~/.claude/projects "$TRIAL/base/projects"
cp -c -R ~/.claude/projects "$TRIAL/work/projects"

# Linux (btrfs, XFS with reflink)
cp -R --reflink=always ~/.claude/projects "$TRIAL/base/projects"
cp -R --reflink=always ~/.claude/projects "$TRIAL/work/projects"
```

Expect about 10 s for several GB of chat histories, and under a second for a single database file. On a filesystem without reflink (ext4), `--reflink=always` fails. Then use a plain copy, or an overlay inside the VM: your folder mounted read-only as the lower layer, a throwaway upper layer on top.

**Stop the writers first** for a database: a clone of a SQLite file taken mid-write may not open. Close the app that owns it, or run `sqlite3 <db> "pragma wal_checkpoint(truncate)"` on the host just before cloning.

Mount the **work** copy. Keep the **base** copy aside for the change report: your real folder keeps changing while the trial runs (your own sessions write to it), so comparing against it would mix your changes with the plugin's.

Done when `base/` and `work/` both exist and the sandbox mounts only `work/`.

## Common paths

| Path | Holds | Mode | Mount at |
|---|---|---|---|
| `~/.claude/projects/` | every session's transcript (`.jsonl`) | snapshot | `/root/.claude/projects` (or the user's home inside) |
| `~/.claude/settings.json` | your settings, hooks, enabled plugins | leave out: a trial starts from a clean one | |
| `~/.claude/` (whole) | settings, plugins, history, and on Linux the login | never whole: it is your production config | |
| a plugin's own store (a `.db`) | what the plugin has learned | snapshot, writers stopped | the path the plugin expects |
| your project folder | the code the plugin works on | snapshot, or a git worktree | any path |

Transcripts hold everything said in every session, secrets included. Pair a history snapshot with a locked network, so a plugin can read them but can't send them anywhere.

## Reading what changed

```sh
git diff --no-index --stat "$TRIAL/base" "$TRIAL/work"                 # created, changed, deleted files
git diff --no-index "$TRIAL/base/x/settings.json" "$TRIAL/work/x/settings.json"
for t in $(sqlite3 "$TRIAL/work/x.db" ".tables"); do                   # rows per table, before and after
  echo "$t $(sqlite3 "$TRIAL/base/x.db" "select count(*) from $t" 2>/dev/null) → $(sqlite3 "$TRIAL/work/x.db" "select count(*) from $t")"
done
```

That covers what changed inside the mounts. What the plugin wrote elsewhere lives only inside the box, so list it before throwing the box away: `~/.claude/settings.json`, `~/.claude/plugins/`, rc files, `~/.local/bin`, services.

Done when every changed path is either expected from the plugin's description or written down as a finding.
