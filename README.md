<div align="center">

# agentic-kit

**Claude can already do almost anything.<br>The kit makes the answer quicker and cheaper to get.**

Tools for people who work in Claude Code all day.

Search your past sessions&nbsp; ◦ &nbsp;Skills for building with agents&nbsp; ◦ &nbsp;All your accounts behind one `claude`&nbsp; ◦ &nbsp;One install, one undo

[Install](#install) • [Why](#why) • [What you can pick](#what-you-can-pick) • [What changes](#what-setup-changes-on-your-machine) • [Without setup](#without-setup)

![for Claude Code](https://img.shields.io/badge/for-Claude%20Code-D97757?logo=claude&logoColor=white)
![install with uv](https://img.shields.io/badge/install%20with-uv-DE5FE9?logo=uv&logoColor=white)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![platforms](https://img.shields.io/badge/macOS%20·%20Linux-lightgrey)

</div>

---

## Install

```sh
uvx --from "git+https://github.com/proxify-dev/agentic-kit#subdirectory=plugins/ak" ak setup
```

You need [uv](https://docs.astral.sh/uv/), git and [Claude Code](https://docs.claude.com/en/docs/claude-code)
(`claude` on your PATH). Setup opens a picker in your terminal: it checks your machine, you tick what
you want, it shows every change it will make, then installs.

## Why

Claude can already answer "which chat changed this file?" by searching its own history files with grep
and jq, but that takes many steps and minutes. The tracer indexes those files after every turn, so the
same question becomes one command. Measured on claude-sonnet-5-5 over 7 GB of real history, average of 2 runs:

| "Which chat last changed `gateway/README.md`?" | time | steps | cost |
|---|--:|--:|--:|
| Claude alone | 4m31s | 17 | $0.28 |
| Claude + tracer | **1m21s** | **5** | $0.12 |

- Every answer cost cents, so the gain is time and steps, not money.
- Over 12 runs on four questions, Claude with the tracer was 2.4× faster overall. One question was a tie.
- When you already give Claude the session id, there is nothing to gain.
- The tracer sees edits made with Claude's Edit and Write tools, not through a shell command. Here it
  named the right chat but missed that chat's last edit, which was made with a shell command.

## What you can pick

Setup always installs **ak**, the `ak` command the other pieces plug into. Type `ak` to see every verb you have.

### tracer · your past sessions, searchable

Which session touched a file, and what each one did, read from Claude Code's own history files with no
AI summary in between. Inside Claude Code, `/tracer:trace <phrase>` recaps a past session.

```sh
ak sessions              # this project's sessions, oldest first
ak trace blame <file>    # which sessions changed a file, and each change they made
```

### agentic-engineering · skills for building with agents

Seven skills (context engineering, harness engineering, skill development, agent development, evals,
sandboxing, fewer faster tests), an agentic-engineer agent, and a hook that gives Claude a short reminder
at the moment a rule applies: before it edits a CLAUDE.md, briefs a subagent, or writes a skill or a hook.

```
/new-plugin <name>                          your own plugin: a folder tracked with git, loaded in every project
/new-skill · /new-agent                     a new skill or agent file, in your plugin by default
/agentic-engineering:harness-engineering    load one of the skills yourself
```

### gateway · all your Claude accounts behind one `claude`

Off by default. Every model call goes through a small service on your machine; when one account runs
out, the next one answers.

```sh
ak xray claude -p "hi"   # the exact prompt Claude would send: system prompt, tools, messages
claude --account <name>  # start on the account you name
ak gateway               # your accounts, and what went through the gateway
ak sysprompt             # your own instructions, added to every claude you start
```

`ak xray` is a dry run: the gateway answers the call itself, so the prompt is not sent and nothing is billed.
The gateway comes with the **claude shim**: the `claude` you type becomes a small wrapper that adds your own
instructions to every session (the real `claude` is not touched). Claude Code's own login is not used, so
its banner says "API Usage Billing". Needs macOS, Linux or WSL, and Node 22.13 or newer.

## What setup changes on your machine

- a copy of this repo in `~/.local/share/ak/release`, added to Claude Code as the `agentic-kit` marketplace
- the plugins you picked, installed from that copy
- the `ak` command, as a uv tool in `~/.local/bin`
- with agentic-engineering: `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` in your Claude Code `settings.json`,
  which `/new-plugin`, `/new-skill` and `/new-agent` need
- only with the gateway: the claude shim on your PATH and a background service on `127.0.0.1:4747`

Every file it changes is backed up first.

```sh
ak setup --dry-run       # the plan and every change; writes nothing
ak setup status          # what is installed, and whether it works
ak setup undo            # take the last run back
```

## Without setup

<details>
<summary>The tracer and agentic-engineering also install from inside Claude Code</summary>

```
/plugin marketplace add proxify-dev/agentic-kit
/plugin install tracer@agentic-kit                 needs uv
/plugin install agentic-engineering@agentic-kit    /new-* also need CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1
```

Without `ak`, the tracer's command is `tracer`. It is on the PATH of Claude's Bash tool, not your
terminal, so Claude runs `tracer sessions` and `tracer trace blame <file>` for you. Ask *"which chat
last changed src/app.py?"*, or type `/tracer:blame src/app.py`.
</details>

## What is in this repo

| | |
|---|---|
| [`plugins/ak/`](plugins/ak/) | the `ak` command and `ak setup` |
| [`plugins/tracer/`](plugins/tracer/) | the session index and its skills; also its own repo, [teocns/cc-tracer](https://github.com/teocns/cc-tracer) |
| [`plugins/agentic-engineering/`](plugins/agentic-engineering/) | the skills, the agent, the hooks |
| [`modules.json`](modules.json) | the gateway and the shim, pinned to v0.1.1 of their own repos, [teocns/cc-gateway](https://github.com/teocns/cc-gateway) and [teocns/cc-shim](https://github.com/teocns/cc-shim) |
| [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json) | the marketplace Claude Code reads |

Tried end to end from GitHub on a fresh Linux machine. Not yet tried on Windows or on a fresh macOS account.

## License

[MIT](LICENSE) © 2026 Proxify.
