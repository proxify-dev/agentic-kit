<div align="center">

# agentic-kit

**Nothing you did with Claude gets lost.<br>Nothing it did stays a guess.**

For people who work in Claude Code all day: find any past chat, see what really happened in it,
and check an agent's work before it ships.

Find any chat&nbsp; ◦ &nbsp;See what Claude really sent&nbsp; ◦ &nbsp;Skills that check your work&nbsp; ◦ &nbsp;Web pages word for word&nbsp; ◦ &nbsp;All your accounts behind one `claude`&nbsp; ◦ &nbsp;One install, one undo

[What it does](#what-it-does-for-you) • [Install](#install) • [Measured](#measured) • [What you can pick](#what-you-can-pick) • [What changes](#what-setup-changes-on-your-machine) • [Without setup](#without-setup)

![for Claude Code](https://img.shields.io/badge/for-Claude%20Code-D97757?logo=claude&logoColor=white)
![install with uv](https://img.shields.io/badge/install%20with-uv-DE5FE9?logo=uv&logoColor=white)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![platforms](https://img.shields.io/badge/macOS%20·%20Linux-lightgrey)

</div>

---

## What it does for you

Every chat you have with Claude Code is saved on your machine, often gigabytes of it. Without help,
that history is a pile of raw files: a chat you lost stays lost, and an hour that went wrong stays a
guess. The kit turns it into answers.

**You don't type the commands on this page. Claude does.** At the start of every session the kit tells
Claude which commands it has; you ask in your own words, and Claude runs them in its Bash tool:

```
you      "Which chat last changed gateway/README.md?"
Claude   runs  ak trace blame gateway/README.md
you      get   the chat, the change, and the prompt that asked for it
```

The commands are shown so you can see where each answer comes from, and you can run them yourself
if you like. Each row below is a real question from real work, answered this way:

| You ask Claude | Claude runs | What came back |
|---|---|---|
| *"I know we did this somewhere. Where?"* | `ak sessions search "<a few words>"` | the lost chat, found across 2,386 projects in about a minute, and picked back up the same day |
| *"Which chat changed this file, and why?"* | `ak trace blame <file>` | every chat that changed it, each change, and the prompt that asked for it |
| *"That felt slow. Where did the hour go?"* | `ak sessions show <chat>` | one turn of 141 steps and 22 minutes, built in the wrong direction: the real waste, found |
| *"Five chats touched this folder. What's mine?"* | `ak trace blame <folder>` | 3 commits of this chat's own work; every other chat's changes left exactly as they were |
| *"Is that chat still running, or waiting on me?"* | `ak sessions live` | the chat thought lost: still open, waiting on an answer |
| *"What does my helper agent really get?"* | `ak xray claude -p "…"` (with the [gateway](#gateway--all-your-claude-accounts-behind-one-claude)) | a fix that quietly took a helper's Agent tool away, caught in a free dry run before anything ran |
| *"Are my tests worth keeping?"* | the fewer-faster-tests skill (or you type `/agentic-engineering:fewer-faster-tests`) | a test skipped on every run since a rename, so a whole plugin was never checked |
| *"What exactly does this page say?"* | WebFetchRaw, a tool it picks on its own | the page's own words in a file; Claude's WebFetch, asked for the same page verbatim, answered *"I can't reproduce the full page verbatim"* |

## Install

```sh
uvx --from "git+https://github.com/proxify-dev/agentic-kit#subdirectory=plugins/ak" ak setup
```

You need [uv](https://docs.astral.sh/uv/), git and [Claude Code](https://docs.claude.com/en/docs/claude-code)
(`claude` on your PATH). Setup opens a picker in your terminal: it checks your machine, you tick what
you want, it shows every change it will make, then installs.

Setup is the one thing you run yourself. After it, there is nothing new to learn: start `claude` and ask.

## Measured

The kit gives Claude three kinds of help:

| | Claude alone | Claude with the kit |
|---|---|---|
| **Find** a chat, or the chat behind a file | possible, slowly: it digs through gigabytes of raw files with grep and jq | one command, from an index the tracer updates after every turn |
| **See** the exact prompt Claude or a helper sends the model | not possible: chat files never keep the whole prompt as sent | `ak xray`, a dry run: nothing sent, nothing billed (needs the gateway, off by default) |
| **Check** tests, skills, installs, the words of a rule | whatever it remembers to check | seven skills, each with the steps for one kind of check |

The first one, raced on claude-sonnet-5-5 over 7 GB of real history, average of 2 runs:

| "Which chat last changed `gateway/README.md`?" | time | steps | cost |
|---|--:|--:|--:|
| Claude alone | 4m31s | 17 | $0.28 |
| Claude + tracer | **1m21s** | **5** | $0.12 |

- Same right answer, 3× faster. Over 12 runs on four questions: 2.4× faster overall.
- The gain is in the finding. Once Claude has a chat's id, a quick fact about that one chat (its cost,
  its model) is just as fast without the kit, and that was the one question that tied.
- Each answer cost cents, so what you get back is minutes and steps, not money.
- The tracer sees changes made with Claude's Edit and Write tools, not by a shell command. Here it
  named the right chat but missed that chat's last edit, which was made with a shell command.

## What you can pick

Setup always installs **ak**, the `ak` command the other pieces plug into. Type `ak` to see every verb you have.

Below, **Claude runs** marks commands Claude runs for you when you ask; **you type** marks the few things
only you start: slash commands, setup, and `claude` itself.

### tracer · your past sessions, searchable

Which session touched a file, and what each one did, read from Claude Code's own history files with no
AI summary in between. Ask *"which chat changed src/app.py?"* or *"what did we do in yesterday's session?"*,
and **Claude runs**:

```sh
ak sessions              # this project's sessions, oldest first
ak trace blame <file>    # which sessions changed a file, and each change they made
```

Or **you type** `/tracer:trace <phrase>` to have Claude recap a past session.

### observer · what past sessions learned

When a session ends, Claude Haiku reads it once (a headless `claude -p` on your own login) and writes down
what it found, decided, fixed or got wrong, as rows in `~/.ak/db/brain.db`. Nothing is put in Claude's
context on its own: the next session starts with one line saying the rows exist, and Claude searches them
before working something out again. When Claude reads a file past sessions worked on, it gets a one-line pointer.
**Claude runs** these on its own; ask *"stop recording here"* and it runs the last one:

```sh
ak observer search <words>    # what past sessions said about it, most relevant first
ak observer get <id>          # one row whole: facts, files, the session it came from
ak observer mute --here       # stop recording in this folder
```

Started from [Claude-Mem](https://github.com/thedotmack/claude-mem), without its MCP server or background
worker; its derived parts stay under Apache-2.0 ([`plugins/observer/NOTICE`](plugins/observer/NOTICE)).

### agentic-engineering · skills for building with agents

Seven skills (context engineering, harness engineering, skill development, agent development, evals,
sandboxing, fewer faster tests), an agentic-engineer agent, and a hook that gives Claude a short reminder
at the moment a rule applies: before it edits a CLAUDE.md, briefs a subagent, or writes a skill or a hook.
Claude loads a skill on its own when the work calls for it. **You type**:

```
/new-plugin <name>                          your own plugin: a folder tracked with git, loaded in every project
/new-skill · /new-agent                     a new skill or agent file, in your plugin by default
/agentic-engineering:harness-engineering    load one of the skills yourself
```

### modes · how this session works with you

Two switches for the session you are in, with a label in the prompt footer while on. Claude is told once when
one changes, and gets one reminder line per prompt while it is on. **You type**:

```
/ask on|off            answers only: Edit and Write are refused while on
/orchestrate on|off    parts of the work go to subagents on haiku, sonnet or opus
/modes:interview       rounds of questions until the direction is clear
/modes:align           stop mid-task: what was built, where it drifted, then ask before going on
```

### web · web pages, word for word

Claude's own WebFetch never gives it the page: a small model reads the page and Claude gets that model's answer,
even when it asks for the exact text. web adds **WebFetchRaw**, a tool beside it: it saves the page itself to a
file (the main content as markdown, or JSON, text and PDFs exactly as sent) and Claude reads that file. Claude picks it
on its own when it needs exact words, code, numbers or links; WebFetch stays for questions about a page.

### gateway · all your Claude accounts behind one `claude`

Off by default. Every model call goes through a small service on your machine; when one account runs
out, the next one answers. **You type**, in a terminal:

```sh
claude --account <name>  # start on the account you name
ak gateway               # your accounts, and what went through the gateway
ak sysprompt             # your own instructions, added to every claude you start
```

Ask *"what does my helper agent really get?"* and **Claude runs**:

```sh
ak xray claude -p "hi"   # the exact prompt Claude would send: system prompt, tools, messages
```

`ak xray` is a dry run: the gateway answers the call itself, so the prompt is not sent and nothing is billed.
The gateway comes with the **claude shim**: the `claude` you type becomes a small wrapper that adds your own
instructions to every session (the real `claude` is not touched). Claude Code's own login is not used, so
its banner says "API Usage Billing". Needs macOS, Linux or WSL, and Node 22.13 or newer.

## What setup changes on your machine

- the `agentic-kit` marketplace, added to Claude Code from GitHub with auto-update on: Claude Code keeps
  its own copy and brings you each new version, the same way it updates its official plugins
- the plugins you picked
- the `ak` command, as a uv tool in `~/.local/bin`; it runs from Claude Code's copy, so it updates with the kit
- with agentic-engineering, modes or web: `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` in your Claude Code `settings.json`,
  which `/new-plugin`, `/new-skill`, `/new-agent`, the modes and WebFetchRaw need
- only with the gateway: the claude shim on your PATH and a background service on `127.0.0.1:4747`,
  both run from Claude Code's copy of this repo (nothing is downloaded besides it)

Every file it changes is backed up first. **You type**:

```sh
ak setup --dry-run       # the plan and every change; writes nothing
ak setup status          # what is installed, and whether it works
ak setup undo            # take the last run back
```

## Without setup

<details>
<summary>The tracer, observer, modes, agentic-engineering and web also install from inside Claude Code</summary>

```
/plugin marketplace add proxify-dev/agentic-kit
/plugin install tracer@agentic-kit                 needs uv
/plugin install observer@agentic-kit               needs uv
/plugin install modes@agentic-kit                  needs CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1
/plugin install agentic-engineering@agentic-kit    /new-* also need CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1
/plugin install web@agentic-kit                    needs uv and CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1
```

For updates, turn on auto-update once: `/plugin` → Marketplaces → agentic-kit → Enable auto-update.

Without `ak`, the tracer's command is `tracer`. It is on the PATH of Claude's Bash tool, not your
terminal, so Claude runs `tracer sessions` and `tracer trace blame <file>` for you. Ask *"which chat
last changed src/app.py?"*, or type `/tracer:blame src/app.py`.
</details>

## What is in this repo

| | |
|---|---|
| [`plugins/ak/`](plugins/ak/) | the `ak` command and `ak setup` |
| [`plugins/tracer/`](plugins/tracer/) | the session index and its skills |
| [`plugins/observer/`](plugins/observer/) | the SessionEnd observer, its store and `observer search` |
| [`plugins/modes/`](plugins/modes/) | /ask, /orchestrate, /modes:interview, /modes:align |
| [`plugins/agentic-engineering/`](plugins/agentic-engineering/) | the skills, the agent, the hooks |
| [`plugins/web/`](plugins/web/) | WebFetchRaw: a web page's own content, saved to a file |
| [`gateway/`](gateway/) | the gateway service; `ak setup` runs it from Claude Code's copy of this repo |
| [`shim/`](shim/) | the claude shim, installed the same way |
| [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json) | the marketplace Claude Code reads |

Tried end to end from GitHub on a fresh Linux machine. Not yet tried on Windows or on a fresh macOS account.

## Use at your own risk

This is an independent project, not affiliated with or endorsed by Anthropic. It is provided as is (see
[LICENSE](LICENSE)). You are responsible for using it within your agreements: Anthropic's terms for your
accounts (the gateway can switch between several), and any client's rules about tools that read your
Claude Code history or hold your credentials. Everything it records stays on your machine.

## License

[MIT](LICENSE) © 2026 Constantin Teodorescu and Proxify.
