---
name: my-agentic-setup
description: Read how this person really works with Claude Code from the files Claude Code keeps on this machine (sessions, helpers, hooks, skills, agents, evals, git) and build one local page: what they do, what they built, problems, next steps, a written review of their setup on its first tab, and the level their laptop reads on the Your level tab (a call confirms or raises it; AGENTIC_SETUP_NO_LEVEL=1 leaves it out). The page's Share tab can send Proxify a summary of practice ids and levels, only when the person presses Send there. One AI pass (a sonnet subagent) fills in the practice rungs. Read-only; the page is a local file. Use when asked "/my-agentic-setup", "what's my agentic setup", or to try the v15 setup tool.
allowed-tools: Bash(python3 *), Bash(python *), Bash(uv *), Bash(mkdir *), Bash(command -v brew), Bash(brew install uv), Bash(curl -LsSf https://astral.sh/uv/install.sh | sh), Bash(PATH="$HOME/.local/bin:$PATH" python3 *), Read, Agent, AskUserQuestion
argument-hint: "[--dry-run] [--only NAME]... [--skip NAME]..."
---

# My agentic setup (Proxify internal trial, v15)

You rebuild how this person works with coding agents from files on their own machine, then show it as one page.
This is an early trial (a beta). The page is the person's own copy: what they do, what they built, the problems it
saw and next steps, a written review of their setup (the first tab, "Review"), and the level the laptop reads (the
"Your level" tab). That level is a starting point a call
confirms or raises, never a final score. A person who wants the copy with no level sets `AGENTIC_SETUP_NO_LEVEL=1` before running.
Say it is a beta when you start.

It reads `~/.claude` and the git repos Claude Code ran in, installs nothing without asking you, and changes no repo.
The scan sends nothing anywhere. The one AI pass is a Claude Code subagent in this very session, so the digest it reads
goes through Claude Code like any chat does, nowhere else. Opening the page in a browser makes no network request (system
fonts only). The page sends one thing, and only when the person presses Send on its "Share with Proxify" tab: the
summary shown there (practice ids and levels, no file names, no text) and the email they type, to Proxify.

The code is `${CLAUDE_PLUGIN_ROOT}/scripts/` (if that is empty: `../../scripts/` from this file). It is a copy of
Proxify's labs code; `scripts/GENERATED.md` says when.

## Where things go

Every run gets a folder of its own, so a second run never overwrites the first one's answers. Make it after the
projects are chosen (section 1), just before `pre`, and never for a dry run, with
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" newrun`: it creates `~/agentic-setup/run-<YYYY-MM-DD-HHMM>/` and prints
that path, which is RUN_DIR below. Earlier runs' folders stay (each holds a copy of the scan, so they add up: they can
delete the ones they don't need). The page is `~/agentic-setup/my-setup.html`; each rebuild keeps the page it replaces in
`archive/`. Never `/tmp`:
on Ubuntu, Firefox installed as a snap has its own private `/tmp` and shows "File not found" for a page written
there. A non-hidden folder in the home directory is readable by every browser. On Windows that is
`%USERPROFILE%\agentic-setup`. Write RUN_DIR below as an **absolute** path (expand `~` yourself): the subagent's Read
tool does not expand it.

Python: the code needs Python 3.12 or newer (macOS's own `python3` is 3.9, too old). Run `run.py` with `python3` (on
Windows `python` or `py`); it re-runs itself under `uv run --with pyyaml` when `uv` is on PATH and the Python is too old
or PyYAML is missing (uv brings its own Python and fetches PyYAML once; that download is the only thing this tool
fetches, say so if it happens). A new enough Python without PyYAML works too: the substrate tabs say "not in this run"
and the person's own tabs are complete.

**Python too old and no `uv`** (`run.py` stops with "This needs Python 3.12 or newer"): do not just tell them to install
it, and do not install anything on your own. Say, in one plain line: "Your Mac's Python is 3.9 and this tool needs 3.12;
`uv` is a small tool that brings its own Python and leaves the one on your Mac alone." Then ask with AskUserQuestion
(one single-select question, "Install uv now?"; options "Yes, install uv" and "No, I'll stop here"). The command, to show
in the option's description: `command -v brew` finds Homebrew -> `brew install uv`; otherwise
`curl -LsSf https://astral.sh/uv/install.sh | sh` (the official installer). Run only those exact commands.
- Yes: run it, then go on with the same step. After the curl installer `uv` sits in `~/.local/bin`, which this shell may
  not have on PATH yet: if `uv --version` is not found, start every `run.py` command with
  `PATH="$HOME/.local/bin:$PATH" ` (Homebrew needs nothing).
- No: stop, and give the link https://docs.astral.sh/uv/ and "run /insights:my-agentic-setup again after installing".

## 1. Before the AI pass (code only)

Talk to them in short, plain sentences, "you". Every message below is what they read; say it before the wait it is about.

**Say this first**, before anything runs, in at most 3 short lines (your own words, same facts; this is a beta):
- what it reads: your Claude Code sessions, settings, skills and hooks, and the git repos you worked in; it changes none of them;
- what it writes and where: `~/agentic-setup/` (one folder per run, plus the page `my-setup.html`; the AI readers save their
  answers there as JSON files) and a speed-up cache in `~/.cache/setup-snapshot/` (up to a few GB; the next run deletes it
  once it is 6 hours old);
- nothing leaves your laptop unless you press Send on the page (the AI readers run in this Claude Code session, like any chat).

**Flags.** The skill takes the same flags as `run.py`, given after the command: `$ARGUMENTS`
- `--dry-run`: show the plan and stop (see Dry run below).
- `--only NAME` (repeatable): review exactly these projects, nothing else. `--skip NAME` (repeatable): leave a project out.
When any flag is given, ask no question: the flags are the answer.

**The list.** Say: "First I look at your sessions of the last 30 days to see which projects you work in most. About half a
minute; nothing is written." Then, with the Bash timeout at 300000 ms and every `--only`/`--skip` given, unchanged:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" candidates --json [--only NAME ...] [--skip NAME ...]
```

It reads only the session logs and writes nothing. It ranks exactly as the scan will (a session counts for the folder it
started in and for each repo it changed from there), so the busiest it shows are the ones the scan would take. Its JSON:
`default` (the keys of their busiest 3, or fewer), `pick` (the keys the review would read with the flags given),
`projects` (every git repo with sessions, busiest first: `key`, `name`, `path`, `same_name`, `typed_sessions`,
`last_active`, `in_default`), `notes` (how a typed name was read), `not_repos` (folders with sessions that are no git
repo: never reviewed themselves, their sessions count for the repos they changed). Call a project by its `name`; when
`same_name` is true, add its `path` (two folders share that name).

If it stops (exit not 0), it printed why and then every name to give. Read them the first line and the names, ask which
they meant, and run this step again with what they say. Never run the scan on a name it refused.

**Which projects.** Skip this when a flag was given. Otherwise:
- `projects` empty: say "No git repo has sessions you typed in during the last 30 days." Then `may_fill` in your own
  words (the scan may still find a repo to review, and says which). Go on with no flags.
- One project: say "The review reads <name>, your only project with sessions", and from `may_fill` that the scan may add
  one it finds. Go on with no flags.
- Two or more: ask with AskUserQuestion, one single-select question, "Which projects should the review read?": option 1
  "Your busiest <n>: A, B, C (Recommended)", its description the session counts; option 2 "Let me choose".
- On "Let me choose", ONE AskUserQuestion call, so it is one screen:
  - 4 projects or fewer: one multiSelect question, "Which should the review read?", every project.
  - more: two multiSelect questions, "Keep which of your busiest 3?" (the 3 `in_default` projects) and "Add any of
    these?" (the next up to 4 by rank, `in_default` false).
  Each option's label is the name; its description "<typed_sessions> sessions, last active <last_active>" (and the path
  when `same_name`). "Other" lets them type names: split what they type on commas.
- Kept + added + typed are the names. None at all: say "Pick at least one project" and ask again. More than 5: say "The
  review reads at most 5 projects, one AI reader each; you picked <n>" and ask again.
- They took the busiest: `default_refused` empty means the `default` keys are the names, already checked. When it holds
  a sentence (two of them share a folder name, say), read it to them and show the "Let me choose" lists.
- Check any other names by code, before anything is scanned: run `candidates --json` again with `--only NAME` for each
  (a project's `key`; a typed name as typed). It reads a typed name by a unique start
  of a folder name ("api" for api-server) and says so in `notes`: tell them. If it stops, read them its first line (it
  names the closest projects) and ask again. Never pick for them on a guess.

Then say which projects will be reviewed: "Reviewing A, B and C." The flags for every later step:
- a choice was shown, or `--only` was given: `--only KEY` for each key they confirmed (`default` when they took the
  busiest, else the last `pick`), so what they confirmed is what is read even if the logs move on;
- only `--skip` was given: `--skip NAME` for each name in the JSON's `skip`;
- neither: no flags.

**Dry run.** With `--dry-run`, run this (no RUN_DIR: do not run `newrun`), show them what it printed, and stop here: no
scan, no AI pass, nothing written.

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" pre --dry-run [--only KEY ...] [--skip NAME ...]
```

**The scan.** Say: "Now I read your setup: your sessions, settings, skills, hooks and the reviewed projects' files.
About 3 minutes on a heavy laptop, a minute on a light one. Nothing leaves your laptop." Make the run folder
(`newrun`, above), then:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" pre RUN_DIR [--only KEY ...] [--skip NAME ...]
```

Set the Bash timeout to the maximum (600000 ms). It prints each step as it goes. The anatomy step prints `projects:`
with the ones it took: the ones they confirmed (the scan opens every chosen repo). With `--only`/`--skip` it first
checks the names (about half a minute) and stops before any slow step if one can't be read (no such folder, not a git
repo), with the names to give: read that to them and ask which they meant. If a later step stops, it says which and
why: read the message to them. Nothing outside the run folder was touched. Re-running it on the same folder is fine.

## 2. The AI pass (one subagent)

Before the AI work, say: "Now <1 + number of briefs> AI readers (sonnet) in this session read what the scan found:
the practices, and one reader per project. A few minutes."

Do not read `RUN_DIR` files yourself: they hold real project names, paths and excerpts of their prompts. Only the
subagent reads them. This works without the Workflow tool: call the **Agent** tool once, with `subagent_type:
"general-purpose"`, `model: "sonnet"`, a short description, and as `prompt` the text between the markers below with
RUN_DIR replaced by the absolute run folder. Do not shorten or reword it.

<!-- brief:start -->
You give each agentic-engineering practice listed in cheap-practices.md a rung for the person who owns this laptop (never write
their name). Read exactly two files, each once, whole, and nothing else (no other file, no shell):
RUN_DIR/cheap-practices.md (the practices, their ladders, how a rung is read) and RUN_DIR/cheap-digest.md (every artifact
they built, with authorship and whether it ran in the 30 days, and what they do by hand, with counts).

For EACH practice listed there, one row:
- rung: the highest rung whose ladder text the digest's evidence matches. L2 = they do it by hand (the "by hand" counts
  at the end of the digest: only the list "What they do by hand" can show an L2, and a number under "What Claude Code or
  the model did unasked" is never the person's habit, however large); L3 = a setup of theirs does it (their files,
  skills, agents, standing instructions); L4 = they built
  something that does it and changes how the agent runs (their hook, program, plugin, gateway), with whatever that
  practice's own L4 text asks for (often a test or eval: the digest's eval folders say which ran and how the runner
  keeps the agent out). Use only artifacts marked as theirs; installed things others wrote are L1 use.
- state: 'seen' with a rung; 'looked, not found' when the digest has the sources that would show it and they show
  nothing; 'not read' when nothing in the digest covers it.
- could_be: a higher rung the evidence points to but doesn't prove ('' otherwise).
- why: one or two plain sentences for their page, with the number and its base from the digest. No level words.
  Use the industry's words: session, not chat; sub-agent, not helper; compaction;
  worktree; evals; sandbox; headless run (claude -p, Agent SDK); tool calls they rejected. Never name what we
  read or how: no 'digest', 'detector', 'probe', 'pointer', 'rung'; say 'your files' or 'your logs'.
- basis: the artifact id in [brackets] (or the by-hand line) the rung rests on.
- evidence: 1-3 items {source: artifact id or digest section, detail: the exact number or phrase}.
- rule: what you matched, in one line (e.g. "hook:memory ran 254 chats + eval with runs, sandboxed -> L4 text").
- ask: a question for the call when the digest can't settle it, else ''.
Be exact: copy numbers from the digest; never invent one. Return one row per listed practice: as many as the first line of cheap-practices.md says, no more.

Then write the result with the Write tool to RUN_DIR/ai-result.json: one JSON object {"rows": [ ...one row per practice... ]} and
nothing else in the file (no prose, no code fence). Each row has exactly these keys: id (string, the practice id as in
cheap-practices.md), state ("seen" | "looked, not found" | "not read"), rung ("L1" | "L2" | "L3" | "L4" | "" when state is
not "seen"), could_be ("L2" | "L3" | "L4" | ""), why (string), basis (string), evidence (array of {"source": string,
"detail": string}), rule (string), ask (string). When done, reply with one line: how many rows you wrote.
<!-- brief:end -->

When it returns, check that `RUN_DIR/ai-result.json` exists (`post` validates its shape and says what is wrong). If the
subagent wrote nothing or something unusable, run the pass once more; if that fails too, tell them and stop.

**The readers (the "Review" tab).** `pre` lists one `RUN_DIR/anatomy-brief-<project>.md` per reviewed project. In
the same message as the subagent above, call the **Agent** tool once per brief, all at once, with
`subagent_type: "general-purpose"`, `model: "sonnet"`, and as `prompt`, with BRIEF replaced by the brief's absolute path:

> Read the brief at BRIEF and do exactly what it says, and open no more files than it allows. Read-only on the project:
> never edit, create or delete a project file and never run its code. The only file you write is the JSON answer file
> the brief names, with the Write tool. Every quote is one exact line copied from the file; never copy a line that
> holds a key, a token or a password. Where the brief asks questions, a "yes" always needs a path:line quote, and
> "yes" always means the thing the question describes is there. Finish with one line: what you judged each part.

Each reader reviews the project's agent-facing files (AGENTS.md files, memory, skills) from an outline the scan drew, and
writes the review the page opens on: what the agent starts with there, what works, what to change first. The laptop's
L3 rests on that review, each reading backed by a line quoted from the person's own file. A member's brief asks for
that review and nothing else. On Proxify's own run (`AGENTIC_SETUP_PROXIFY=1` set before `pre`) the brief also holds
the part-by-part questions the staff page shows, and the same prompt covers them.

A reader that fails is not a stop: without any review the level reads "could be" until the tool is run again. Do not
read the briefs yourself.

## 3. After the AI pass

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" post RUN_DIR
```

It checks the rows, merges the readers' reviews with the outline the scan drew (`setup-read.json`: a reading counts
only when the code finds its quoted line in the person's own file), runs the engine (the level) and builds
`~/agentic-setup/my-setup.html`, then opens it in the browser. If the browser does not open (a remote or headless
machine), give them the path; they open it themselves.

Get what to say without reading the run files. This prints only what the page's first screen says: one line about
the person across the projects that were read, one line per project (its headline), the first thing to change with
what it costs and the step, and the level with its reason (no level line when `AGENTIC_SETUP_NO_LEVEL=1` is set):

```
python3 -I -c "
import json, os, sys
def load(n):
    try: return json.load(open(os.path.join(sys.argv[1], n)))
    except (OSError, ValueError): return {}
sr, res = load('setup-read.json'), load('v15-result.json')
ps = sr.get('projects') or []
opening = sr.get('opening') or []
print('REVIEW:', opening[0] if opening else 'the read of the files did not finish')
for p in ps:
    if p.get('headline'): print('PROJECT ' + str(p.get('label') or p.get('slug')) + ':', p['headline'])
first = ([{'saw': h.get('why'), 'cost': h.get('cost'), 'do': h.get('do')} for h in sr.get('holds') or [] if h.get('fired')]
         + (sr.get('cards') or []) + [c for p in ps for c in p.get('change') or []]
         + [{'saw': g.get('why'), 'do': g.get('next')} for g in sr.get('parts') or [] if g.get('judged') in ('partly', 'not shown')])
for k, label in (('saw', 'CHANGE FIRST:'), ('cost', 'IT COSTS:'), ('do', 'DO:')):
    if first and first[0].get(k): print(label, first[0][k])
pt = res.get('pointer') if res.get('level_model') == 'practices' else res.get('pointer_things') or res.get('pointer')
off = '1' in (os.environ.get('AGENTIC_SETUP_NO_LEVEL'), os.environ.get('AGENTIC_SETUP_MEMBER'))
if pt and pt.get('level') and not off:
    print('LEVEL:', pt['level'] + (', could be ' + pt['could_be'] if pt.get('could_be') else ''))
    print('BECAUSE:', (pt.get('reasons') or [''])[0])
" RUN_DIR
```

## 4. Tell them what it says

Plain words, short, "you". Say what the terminal just printed, in this order, so it matches the page:
1. Where the page is, and that it opens on a review of their setup (the "Review" tab). Which projects were reviewed, and
   that the Review tab lists the ones it did not read. To review others they run this command again (it asks which), or
   name them at once with `--only NAME`. A project left out is out of the review only: the rest of the page still
   counts its sessions.
2. What the review found about them (`REVIEW`): it is about the person across the projects that were read, so say it
   first. Then each project in one short sentence, by its name (`PROJECT <name>:`). If `REVIEW` says the read of the
   files did not finish, say that and offer to run the tool again; do not make a review up.
3. The first thing to change (`CHANGE FIRST`), what it costs them (`IT COSTS`) and the one step (`DO`). Say all three:
   a step without its reason is a chore.
4. The level (`LEVEL`, `BECAUSE`), in one sentence, as what the laptop shows so far, which a call confirms or raises.
   "L2, could be L3" means their files don't yet show a setup that carries the context, or the read didn't finish: say
   which, from `BECAUSE`. Never call it a score. No `LEVEL` line was printed: say nothing about a level.

Then say plainly that this is an early trial:
- It reads this machine only: work on another machine, a client VM or a devcontainer does not show.
- The review is one AI reader per project. A reading counts toward the level only when it quotes a line the code then
  found in their own file, but no second reader checked the judgement. "Couldn't read" means the read didn't finish,
  never "you don't do it".
- A habit counts only when Claude Code's records show it was their own act (typed, or started by them). What the model
  did unasked shows as "Claude did this" and counts for nothing, so a habit they have but never type may be missing.
- How much is enough (how many layers, how many weeks, how many sessions) is our first guess, not yet checked against
  anyone's evaluation call.
- What counts as theirs comes from git authorship (the top commit author of their repos, plus `git config --global`).
  Someone who commits under several emails may be undercounted. Ask what they actually do if something looks wrong.
- Claude Code keeps session logs 30 days unless `cleanupPeriodDays` says otherwise.

If something on the page looks wrong to them, ask what they really do and write it down: that is the feedback this
trial is for. Nothing has been sent unless they press Send on the page.

## 5. Sharing with Proxify

The page has a "Share with Proxify" tab (and a "Get your level confirmed" button at its top): it shows exactly what
would be sent, and sends it to Proxify, with the email they type, only when they press Send there. Proxify then emails
them the link to book their evaluation call. Say in one line that the tab exists and is optional. Never press Send for
them, never urge them to, and never send anything yourself.

If they would rather send a file by hand, this writes the same summary to `~/agentic-setup/share-with-proxify.json`:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run.py" share RUN_DIR
```

## 6. Cleaning up

Last, after section 5 (skip it after a dry run: nothing was written). Ask with AskUserQuestion, one single-select
question, "Delete this run's working files? The page stays.": option 1 "Keep everything", option 2 "Delete the run folder
and the cache" (description: RUN_DIR, which holds a copy of the scan, and `~/.cache/setup-snapshot/`; `my-setup.html` and its
`archive/` stay). Earlier runs' folders are left alone: tell them they can delete `~/agentic-setup/` and
`~/.cache/setup-snapshot/` whenever they like. On "Delete", with RUN_DIR as the absolute run folder:

```
python3 -I -c "
import os, shutil, sys
run = os.path.realpath(sys.argv[1])
root = os.path.realpath(os.path.expanduser('~/agentic-setup'))
if os.path.dirname(run) == root and os.path.basename(run).startswith('run-'): shutil.rmtree(run)
shutil.rmtree(os.path.expanduser('~/.cache/setup-snapshot'), ignore_errors=True)
" RUN_DIR
```

Say what was deleted and that the page is still at `~/agentic-setup/my-setup.html`.
