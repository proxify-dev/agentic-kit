# Sandbox levels: one recipe each

The recipe for the level step 2 of [the sandboxing steps](../SKILL.md) picked.

## What are you trying to do?

- [A Linux container or micro-VM](#container-or-micro-vm): any plugin that isn't macOS-only
- [A macOS VM](#macos-vm): a plugin that needs launchd, Keychain or `osascript`
- [Logging in from inside](#credentials): every level
- [Bringing data in](./mounts.md): chat histories, a plugin's store
- [It broke](./traps.md)

## Container or micro-VM

A fresh Linux with nothing on it. Three runtimes, one shape:

| Runtime | Where | Boundary |
|---|---|---|
| `container` (apple/container) | macOS on Apple silicon | one lightweight VM per run |
| Docker Sandboxes (`sbx`) | macOS, Windows, Linux | a micro-VM per agent, with a network allowlist and a host-side credential proxy built in |
| `docker run` / `podman run` | anywhere | a container: it shares the host kernel, so it is a weaker boundary than a VM |

The shape, shown with `docker run`. `container run` takes the same `-v`, `-w`, `-e`, `--rm` and `-i` flags.

```sh
docker run --rm -it \
  --network <locked-network> \
  -e CLAUDE_CODE_OAUTH_TOKEN \
  -v "$WORK/projects:/root/.claude/projects" \
  -v "$PWD:/work" -w /work \
  <image-with-node-and-claude> \
  bash -lc 'claude plugin marketplace add <source> && claude plugin install <plugin>@<marketplace> && claude'
```

- **Image**: an image with `node`, `git` and Claude Code installed (`npm i -g @anthropic-ai/claude-code`), plus whatever the plugin's `needs` lists (`uv`, `python3`). Build it once and reuse it, so each run starts warm.
- **Root**: `claude` refuses to run as root unless `IS_SANDBOX=1` is set. Either run as a normal user in the image, or set it and read [the trap it brings](./traps.md#is_sandbox1-skips-some-permission-prompts).
- **Network**: an internal network with no route out, plus a proxy on the host that lets through only the hosts in an allowlist (`api.anthropic.com` and what the plugin calls). `sbx` has this built in. With `docker` or `container`, create a network with no route out (`--internal`) and run a small CONNECT proxy on the host.
- **Mounts**: only what step 3 picked, each in the mode [mounts.md](./mounts.md) gives it. `sbx` mounts a folder only at the same path it has on the host, so a snapshot can't be placed at `/root/.claude/projects`. Use `docker` or `container` when the plugin reads histories.
- **Interactive vs scripted**: `-it` for trying it by hand. For a scripted run (`claude -p`, the Agent SDK), use `-i` without `-t`: a TTY breaks `stream-json`.

Done when `claude --version` runs inside, and `curl https://example.com` from inside fails.

## macOS VM

A whole Mac, for plugins that need launchd, Keychain, `osascript` or notifications. Tart runs one from an image:

```sh
tart clone ghcr.io/cirruslabs/macos-<release>-base:latest trial
tart run trial                        # log in, install Claude Code, install the plugin, try it
tart delete trial                     # throw it away
```

Expect tens of GB on disk and minutes to start. Clone a prepared base once (Claude Code installed, logged in), and start each trial from that clone. Tart shares no folders unless you ask (`--dir`), so the snapshot rules in [mounts.md](./mounts.md) apply unchanged.

Done when the plugin's macOS-only part (the popup, the service) works inside the VM.

## Credentials

A sandbox needs a login, and your own config folder is the wrong way to bring one in.

- **A token**: `claude setup-token` (on the host, once) prints a long-lived token. Pass it in as `CLAUDE_CODE_OAUTH_TOKEN`. Revoke it when trials end.
- **An API key**: `ANTHROPIC_API_KEY`, billed per token.
- **Strongest**: keep the credential on the host. A proxy on the host adds it to each request, so nothing inside the box ever holds it. `sbx` does this.

On macOS your normal login lives in Keychain, not in `~/.claude`, so copying `~/.claude` in does not log the sandbox in. It also hands the sandbox your settings and history.

Done when `claude -p "say ok"` answers from inside, and no file under your real `~/.claude` is mounted.
