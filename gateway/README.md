<div align="center">

# cc-gateway

**All your Claude accounts behind one `claude`.**

Rotates by quota&nbsp; ◦ &nbsp;Records every request&nbsp; ◦ &nbsp;Rules on the wire&nbsp; ◦ &nbsp;Runs on your machine

[Install](#install) • [Quick start](#quick-start) • [Commands](docs/commands.md) • [Docs](#docs) • [cc-shim](../shim/)

![node](https://img.shields.io/badge/node-%E2%89%A5%2022.7-339933?logo=node.js&logoColor=white)
![dependencies](https://img.shields.io/badge/dependencies-0-blue)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![platforms](https://img.shields.io/badge/macOS%20·%20Linux%20·%20Windows-lightgrey)

</div>

---

cc-gateway is a small proxy that runs on your machine. Claude Code talks to it instead of Anthropic.
Log in all your accounts once: when one runs out, the next request goes out on another.

```
$ ak gateway
gateway   on · 2 accounts · every new claude goes through :4747
account   alice@example.com   usable  expires 2h
account   bob@example.com     usable  expires 5h
```

## Install

This folder ships inside the [agentic kit](https://github.com/proxify-dev/agentic-kit). `ak setup`
turns it on from the kit's clone: pick `gateway` (the [shim](../shim/) comes with it).

<details>
<summary>From a checkout · Windows · uninstall</summary>

```sh
git clone https://github.com/proxify-dev/agentic-kit && cd agentic-kit/gateway
node --experimental-strip-types --no-warnings src/cli.ts enable
```

Every `ak gateway …` below is that `node … src/cli.ts …` line in a checkout. Run on its own, the
program prints its name as `cc-gateway`. To remove it, turn it off (`ak gateway disable`), then
`ak setup undo`: your accounts and config stay. The service itself runs on Windows too: see
[operations.md](docs/operations.md).
</details>

## Quick start

```sh
ak gateway account login     # browser login — once per account
ak gateway enable            # start it, now and at every login
ak gateway run -- claude     # a claude that goes through it
```

`ak gateway disable` turns it off. Claude goes back to its own login.

## Without it, and with it

| | plain `claude` | `claude` through cc-gateway |
|---|---|---|
| **Accounts** | one login at a time | a pool, used in turn |
| **Out of quota** | you wait | the next account answers |
| **What was sent** | guessed from the transcript | every request recorded: model, tokens, timing, account |
| **Rules** | none | deny a model, cap a budget, redact, add to the system prompt |

## Why cc-gateway

- 🔁 **Rotation that knows quota.** It reads each account's 5-hour and weekly limits and fails over on rate limits, server errors and expired tokens.
- 📌 **Pin when it matters.** `ak gateway run --account work -- claude` keeps one session on one account.
- 🔍 **See the real prompt.** `ak gateway xray claude -p "hi"` shows the exact system prompt, tools and messages, and sends nothing.
- 📊 **Watch it live.** `ak gateway tui` shows quota bars per account and every request in flight.
- 🔒 **Local only.** It listens on localhost and refuses web pages. Tokens live in one owner-only file.
- 📦 **Nothing to install.** Zero dependencies, Node ≥ 22.7, runs as a service on macOS, Linux and Windows.

## Docs

| | |
|---|---|
| [Commands](docs/commands.md) | every verb and flag |
| [Configuration](docs/configuration.md) | files, defaults, environment variables |
| [Accounts](docs/accounts.md) | rotation, pinning, quota checks |
| [xray](docs/xray.md) | the dry run, step by step |
| [Policy and trace](docs/policy-and-trace.md) | rules, and what gets recorded |
| [Operations](docs/operations.md) | the service, zero-gap restarts, platforms |
| [Control API and TUI](docs/control-api.md) | the local HTTP endpoints, the keys |
| [Security](docs/security.md) | the trust model, and what still goes around it |
| [Development](docs/development.md) | build, test, release |

<details>
<summary>Good to know</summary>

- Model calls go through cc-gateway. A few other Claude Code calls (telemetry, startup checks)
  still go straight to Anthropic: [security.md](docs/security.md).
- Claude Code turns off MCP tool search behind any gateway. cc-gateway turns it back on:
  [claude-code.md](docs/claude-code.md).
- A running `claude` keeps the route it started with. Only new launches follow `enable` and `disable`.
</details>

<details>
<summary>With cc-shim</summary>

With the [shim](../shim/) installed, `enable` routes every `claude` you type, no `run` needed, and
`claude --account NAME` pins a launch.
</details>

## License

[MIT](LICENSE) © 2026 Constantin Teodorescu and Proxify. The account engine in [`src/engine/`](src/engine/) comes from
[teamclaude](https://github.com/KarpelesLab/teamclaude), MIT © 2026 KarpelesLab
([its LICENSE](src/engine/LICENSE)).
