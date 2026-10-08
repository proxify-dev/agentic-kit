# Sandbox traps: what breaks, and the fix

Failures seen while sandboxing Claude Code. Each one names its symptom first; find yours by searching this file for it.

## What are you trying to do?

- [Logging in failed, or the API is unreachable](#auth-breaks-with-a-connection-error)
- [A database won't open, or says it's read-only](#attempt-to-write-a-readonly-database)
- [`claude` won't start inside](#claude-refuses-to-run-as-root)
- [Isolation looks weaker than expected](#a-different-home-is-not-isolation)
- [Back to the steps](../SKILL.md)

## A different HOME is not isolation

Setting `HOME` or `CLAUDE_CONFIG_DIR` to a temp folder gives `claude` an empty `~/.claude`, so it looks like a fresh install. But the plugin's hooks still run on your machine, as your user. They share `/tmp` with every other process, can reach your Keychain, can start a launchd or systemd service, and can write any path you can, so two runs "isolated" this way can read each other's files in `/tmp`. Use a container or VM.

## Plugin hooks run where `claude` runs

Hooks are shell commands. Whatever sandbox wraps a tool call, a hook runs on the machine that runs `claude` itself. If `claude` runs on your Mac, so do the hooks, whatever the Bash sandbox allows. Put `claude` itself inside the box.

## attempt to write a readonly database

A SQLite store mounted read-only. In WAL mode, SQLite writes its `-wal` and `-shm` files even to read. Mount a snapshot instead ([mounts.md](./mounts.md#three-modes)).

## A snapshot of a database won't open

The clone was taken while the database was being written. Stop the app that owns it, or checkpoint it first (`pragma wal_checkpoint(truncate)`), then clone again.

## claude refuses to run as root

Most images run as root, and `claude` refuses to start as root. Add a normal user to the image, or set `IS_SANDBOX=1`, which brings the next trap.

## IS_SANDBOX=1 skips some permission prompts

With `IS_SANDBOX=1`, Claude Code lets some read-only Bash commands (`pwd`, `hostname`) run without asking and without calling the SDK's `canUseTool`. Commands that write still ask. A test that expects every Bash call to prompt will be wrong; assert on a command that writes.

## Auth breaks with a connection error

- **A host address leaked in.** An `ANTHROPIC_BASE_URL` (or `ANTHROPIC_AUTH_TOKEN`) set on the host for a local relay points at `localhost`, which inside the box is the box itself. Unset both before starting the sandbox.
- **The proxy came up before the network.** A freshly created container network may not have its gateway address yet. A proxy that binds to it too early fails with `EADDRNOTAVAIL`. Wait until the address can be bound, then start the proxy.
- **A VPN kill-switch** blocks traffic from a new VM's network card. Send the VM's traffic through a proxy on the host: the host process is already allowed out.

## Logged in on the host, logged out in the box

On macOS the login is stored in Keychain, not in `~/.claude`, so nothing you copy from `~/.claude` carries it. Pass a token in ([levels.md](./levels.md#credentials)).

## The box can reach services on your host

A port your host listens on for every interface (`0.0.0.0`) may be reachable from inside, past the network allowlist. Test it: run `nc -l 0.0.0.0 9999` on the host, then try to connect from inside. Bind host services you care about to `127.0.0.1`.

## scripted runs hang or garble output

A TTY on the container (`-t`) turns stdin into a terminal, which breaks `--output-format stream-json`. Use `-i` alone for anything a program reads.
