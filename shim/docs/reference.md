# cc-shim reference

Everything about how cc-shim works. The front page is [the README](../README.md).

cc-shim itself has **no routing policy**. With an empty `conf.d` it is a pure
passthrough.

It is one Node module, `cc-shim.mjs` (Node ≥ 22.13, no dependencies), and runs
the same on macOS, Linux and native Windows — no bash needed anywhere. The
same file is the wrapper (`node cc-shim.mjs claude …`) and the installer
(`install · uninstall · status · doctor · fragment`).

| | macOS / Linux | Windows |
|---|---|---|
| shim dir | `$XDG_DATA_HOME/cc-shim`, default `~/.local/share/cc-shim` | `%LOCALAPPDATA%\cc-shim` |
| config (conf.d, system prompt) | `$XDG_CONFIG_HOME/cc-shim`, default `~/.config/cc-shim` | `%APPDATA%\cc-shim` |
| `claude` on PATH | `claude`, a `#!/bin/sh` stub | `claude.cmd` |
| hand-over | a real `execve` (Node ≥ 22.15): claude keeps the pid | a child process, exit code passed back |
| fragments | `*.mjs` and `*.sh` | `*.mjs` |

## Install, in detail

```sh
node cc-shim.mjs install   # from a checkout; in the agentic kit: ak shim install
cc-shim doctor       # PATH order, recursion, leftovers, fragment syntax, the stub's node
cc-shim status       # what will run, in what order
cc-shim uninstall    # keeps your fragments; --purge removes them too
```

Not a Claude Code plugin: Claude Code never loads it, it is the thing that
starts Claude Code. It is installed as a **copy** — so after editing `cc-shim.mjs`, install again to
put it live (`cc-shim doctor` says when the copy is stale).

`install` copies `cc-shim.mjs` and `_brand.sh` into the shim dir and writes
two stubs beside them, `claude` and `cc-shim` (`claude.cmd` and `cc-shim.cmd`
on Windows). A stub only finds node: the absolute path of the node that ran
the install (so a launch from cron, launchd or an IDE, where nvm's node is not
on PATH, still works), else the first `node` on PATH. With no node at all the
POSIX `claude` stub still starts `$CC_SHIM_REAL` or `~/.local/bin/claude`,
without fragments — claude not starting is the one outcome the shim exists to
prevent. Re-run `install` after removing the node it names; `doctor` says so.

Then it puts the shim dir first on PATH:

- **macOS / Linux**: `. "$HOME/.local/share/cc-shim/env"  # cc-shim` into
  `~/.zshenv`, `~/.profile`, `~/.bashrc`, `~/.bash_profile` — files that exist,
  plus the canonical ones for your login shell (`.zshenv` for zsh, `.bashrc`
  and `.profile` for bash). **`~/.bash_profile` is never created**: once it
  exists a bash login shell reads it *instead of* `~/.profile`, and on
  Debian/Ubuntu `~/.profile` is what adds `~/.local/bin` and sources `~/.bashrc`.
  A `~/.bash_profile` holding nothing but the cc-shim line (an earlier install
  made it) is removed by `install` and `uninstall`, and `doctor` warns about
  one — a fish `conf.d` drop-in if fish is
  configured, and on Linux `~/.config/environment.d/cc-shim.conf`, which
  systemd user sessions read before any shell (GUI apps too, from the next
  login). **`.zshenv`, not `.zshrc`** — `.zshrc` is interactive-only, so cron,
  launchd, IDE subshells and Agent SDK subprocesses would never see the shim.
  `env` is generated: it names the shim dir as installed.
- **Windows**: the shim dir goes first in the **User** Path (the registry
  value, read and written raw so `%VARS%` in it survive, then broadcast so new
  terminals see it). The value crosses PowerShell as base64 of its UTF-16
  bytes both ways — PowerShell 5.1 prints in the console's OEM code page, which
  would turn `C:\Users\José` into `Jos?` and write that back. The PowerShell profile is not edited. Open a new
  terminal afterwards. The User Path comes after the Machine Path, so a claude
  installed for all users would still win; `doctor` names the one PATH finds.

`install` also removes teamclaude's shim and its rc lines. That is deliberate
and one-way: it prints every line it removes. And when both the gateway's
native `05-gateway.mjs` and its old `05-brain-gateway.sh` are in conf.d, it
removes the old one — it sorts first and claims, so it would take every launch.

**Inside the agentic kit**, `ak shim` is the same commands (`node cc-shim.mjs …`; status and
`doctor` rendered from `--json`; `install`/`uninstall` passed through with this
folder as the source). Bare `ak` carries one `launcher` line saying whether
`claude` on this PATH *is* the shim — nothing routes if the shim is not the
binary that launches — and when the installed one is still the old bash shim.

## The conf.d contract

Fragments live in `conf.d/` (mode 0700 on POSIX — they hold tokens), run in
lexical order, and are one of two kinds. Convention `NN-name.mjs`. **Disable one
by renaming it to `*.off`** — anything not ending in `.mjs` or `.sh` is ignored.

- **`*.mjs`** — every OS. Imported in a worker thread; whatever it leaves in
  `process.env` is imported (top-level `await` works). Its stdout goes to our
  stderr.
- **`*.sh`** — macOS and Linux only; ignored on Windows (`doctor` warns). Sourced
  in its own `bash -c` child (`/bin/sh` when there is no bash), stdout sent to
  stderr, and its environment imported NUL-delimited from `env -0` — or
  `printenv -0`, or node itself, where busybox's `env` has no `-0` (Alpine).
  A fragment that finished but whose environment could not be read back is
  said on stderr, never imported as nothing in silence; `doctor` runs a probe
  fragment to show which road this machine takes.

A fragment **may**:

- set anything the real claude should see (`export X=…` / `process.env.X = …`)
- set `CC_SHIM_UNSET="VAR1 VAR2"` — the shim unsets those names afterwards.
  The only way to remove a variable inherited from the parent shell.
- set `CC_SHIM_CLAIM=<name>` — the shim stops; later fragments do not run.
  Passed through to claude as provenance.
- write to stderr

Plain assignment is enough for both contract variables in a `.sh`; the shim
force-exports them in the child.

A fragment **must** finish fast and be side-effect-free beyond environment. It
runs on *every* invocation, including `claude --version` and every SDK
subprocess. Any network call needs its own timeout
(`AbortSignal.timeout(2000)`, `curl --max-time 2`); the shim's 5 s cap
(`CC_SHIM_FRAGMENT_TIMEOUT`) is a backstop, not a budget.

A fragment **cannot break the shim.** Guaranteed for: nonzero exit, `exit N`,
`process.exit()`, a throw, `set -e` plus a failing command, a syntax error (sh
or JS), an import that fails, an unreadable file, `exec`, stdout junk, a timer
left running, an await that never settles, and an infinite loop.
`tests/selftest.test.mjs` asserts each of these.

What a broken fragment contributes, worth knowing apart:

- **`exit N` / `process.exit()`, a syntax error, a throw, the 5 s cap** — nothing
  that fragment set is imported. All-or-nothing. (An `.mjs` that calls
  `process.exit(0)` also contributes nothing: that is how "not me" is said.)
- **`set -e` plus a failing command** (sh) — errexit is suppressed: the source
  runs as the left side of a `|| true` list, and on bash 3.2 (macOS's
  `/bin/bash`), which does not honour that, a `set` wrapper turns errexit back
  off. The fragment keeps going and its exports still apply. One cost: a
  fragment's own `set -- …` sets the wrapper's arguments, not its own.

`cc-shim fragment <file>` runs one fragment exactly as a launch would and prints
what it would set, unset and claim, as JSON — the gateway's tests run its
fragment through it.

## The one flag the wrapper consumes

`claude --account [NAME]` picks the gateway login for this launch (`--prefer
NAME`: start there, rotate away if it runs out). Consumed
here, never forwarded — the real claude has no such flag. Also spelled
`--acct`, and both take `=NAME` as well as a separate token.

Recognised at **any position up to a `--`**, because that is where wrappers put
it. A launcher that presets flags appends the user's arguments after its own:

```sh
exec claude --dangerously-skip-permissions --agent manager "$@"
#                                                          └─ --account lands here
```

Scanning the whole argv cannot corrupt a working invocation. The real claude
parses options in every position and rejects unknown ones, so a bare
`--account` token is already fatal wherever it sits — there is no invocation
that works today and stops working. Literal `--account` prompt text goes after
`--`, which stops the scan; everything past it is forwarded byte-for-byte.

The wrapper sets `CC_SHIM_ACCOUNT` (and `CC_SHIM_ACCOUNT_SOFT` for `--prefer`);
the gateway's fragment asks the gateway which account the name means and
exports `CC_SHIM_ROUTED` when it pinned one. A bare `--account` asks the human
through `cc-gateway account choose` when cc-gateway is on PATH, else the agentic
kit's `ak gateway account choose` (the kit CLI's name is read from `_brand.sh`
beside the module) — the only router-specific knowledge in the wrapper, and it
has to be: fragments run capped with stdout captured, which is no place for a
prompt. Everything else stays fail-open; only the routed-or-refuse check after
the fragments deliberately does not, because you named an account.

## The system prompt file

`<config>/cc-shim/system-prompt.md` — a Markdown file appended to claude's
system prompt on every launch. Missing or empty means nothing is injected, the
same as an empty conf.d. Edit it with any editor; inside the agentic kit, `ak sysprompt edit`.

```sh
claude -p hi
#  ->  claude --append-system-prompt-file ~/.config/cc-shim/system-prompt.md -p hi
```

Appended, not substituted: `--system-prompt-file` would throw Claude Code's own
prompt away with it, which a standing instruction never wants. Set
`CC_SYSPROMPT_FLAG=--system-prompt-file` if you do want that.

The flag is prepended, so a flag you typed yourself still wins under
last-one-wins parsing, and it is harmless in front of a subcommand — `claude
mcp list` parses it as a root option and ignores it. It does need a claude new
enough to know `--append-system-prompt-file`; an older binary rejects the flag
instead of failing open, so delete the file if you pin an old version.

## Finding the real claude

`$CC_SHIM_REAL` if it is executable, else the first `claude` on PATH that is not
the shim — split on the OS's delimiter, and on Windows every `PATHEXT`
extension in order (`claude.exe` before `claude.cmd` by default). The shim's
own dir is skipped, and so is any candidate whose realpath is one of the shim's
files (a symlink to it elsewhere). A stripped PATH falls back to the standard
installs: `~/.local/bin/claude`; on Windows `%USERPROFILE%\.local\bin\claude.exe`,
winget's `%LOCALAPPDATA%\Microsoft\WinGet\Links\claude.exe`, npm's
`%APPDATA%\npm\claude.cmd`.

A `claude.cmd` (npm's) is seen through to what it runs — `node …\cli.js` or a
native `claude.exe` — and started without cmd.exe. A batch file that cannot be
seen through goes through `cmd.exe /d /s /c` with every argument quoted and
caret-escaped (cross-spawn's rules); an argument with a line break cannot pass
through cmd.exe at all, and the shim says so instead of truncating it.

On the child-process road (Windows, or Node < 22.15) Ctrl-C is not forwarded —
the terminal already delivered it to claude — while a SIGTERM or SIGHUP sent to
the wrapper is passed on, and claude's exit code or signal is the wrapper's.

## Escape hatches

| Variable | Effect |
|---|---|
| `CC_SHIM_DISABLE=1` | skip all fragments; pure passthrough |
| `CC_SHIM_REAL=/path` | force the real claude (bypasses PATH resolution) |
| `CC_SHIM_CONFD=/path` | use a different fragment directory |
| `CC_SHIM_DEBUG=1` | trace fragment loading and the final hand-over, to stderr |
| `CC_SHIM_PRINT_REAL=1` | print the resolved real claude and exit |
| `CC_SHIM_FRAGMENT_TIMEOUT=<s>` | the per-fragment cap (default 5) |
| `CC_SHIM_NO_EXEC=1` | take the child-process road on POSIX too (what the tests use) |
| `CC_SHIM_ACCOUNT=<acct>` | what `--account` sets; usable directly, no flag parsing |
| `CC_SYSPROMPT_FILE=/path` | use a different system prompt file |
| `CC_SYSPROMPT_FLAG=<flag>` | `--system-prompt-file` to replace instead of append |

## Known ceilings

- **An SDK that sets `pathToClaudeCodeExecutable`, or prefers a bundled binary,
  bypasses PATH and therefore bypasses this shim.** Point it at the shim dir's
  `claude` if you need it covered. Solving this generally means exporting
  routing globally into every process, which is the leak this design exists to
  remove.
- **launchd jobs get no rc file**, so they never see the shim.
  `launchctl config user path` is the answer if you need it.
- **Every launch starts a node** (≈ 40 ms, plus ≈ 25 ms per `.mjs` fragment).
- **Windows**: `claude.cmd` is a batch file, so it carries batch's limits —
  Ctrl-C can end in cmd's "Terminate batch job (Y/N)?", and `%` / `^` in
  arguments typed at cmd.exe are cmd's to interpret before the shim sees them.
  The prompt has no robust fix inside a batch file (it is cmd's, asked after
  the child exits, whenever a Ctrl-C event reached the console): the
  interactive claude reads the console raw, so Ctrl-C there is a keypress and
  never asks it; `claude -p` interrupted with Ctrl-C does — answer either way,
  claude is already gone. Git Bash looks for `claude`/`claude.exe`, not
  `claude.cmd`, so it bypasses the shim.
- **Windows, a node under a non-ASCII path** (`C:\Users\José\…`): cmd reads a
  batch file in the OEM code page, so that stub switches the console to UTF-8
  (`chcp 65001`) before the line naming node and restores the saved code page
  on the way out. While claude runs the console is at 65001; a "Terminate
  batch job" answered Y skips the restore (`chcp 437`, or your own, puts it
  back). An ASCII node path — the usual case — gets the plain stub.

## Test

```sh
cd shim && node --test        # or: node --test shim/tests/*.test.mjs
```

Installs into a throwaway `HOME` and runs the real POSIX path end to end — the
generated stub, a fake claude that reports the argv and env it was handed —
including the whole fail-open matrix for both fragment kinds, the account
flags, the system prompt, doctor, status, rc wiring and uninstall. The Windows
pieces (PATHEXT lookup, cmd.exe quoting, seeing through `claude.cmd`, the User
Path edit through a fake PowerShell, the UTF-8 stub) are tested with simulated inputs; the end-to-end half
skips on Windows. `bash tests/selftest.sh` runs the same suite, printing
`ok`/`FAIL` lines, for scripts/brand.py's canary.
