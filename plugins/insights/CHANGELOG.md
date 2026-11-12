# insights: what changed

To update, in a terminal (not inside a Claude Code chat), run these two commands, then restart Claude Code:

```
claude plugin marketplace update agentic-kit
claude plugin update insights@agentic-kit
```

You get a new version only when it is released: each entry below is one release, newest first. The numbers in
brackets are pull requests on proxify-dev/agentic-kit. Versions 0.1.1, 0.2.3 and 0.4.1 were never sent out; their
changes arrived in the version after them.

## 0.6.4 — 2026-11-11

### What changes for you

- **What you've built** is redesigned. Each kind of thing you built is a tile that says whether you used it this month. The lists are sorted by use. Each skill's steps are in plain words. Your hooks sit on a session timeline at the moment they run.
- **Change this first** on the Review tab is cut to one card and three lines.
- Each thing is counted once.
- How a level is read is the same as in 0.6.3.

### Known limits

- One run's AI part costs about 350k tokens on a large setup, against a target of about 200k. The old practice pass is still in; dropping it is pending.
- Repos are found only from where a session starts. If you start sessions in your home folder and work across many repos, you read low on "what you built" and Level 4. Fixed in 0.6.5.
- Every threshold is a guess until it is checked against levels confirmed on calls.
- The share receiver needs three new field names before the new lines can be sent.

Pull request: #10

## 0.6.3 — 2026-11-11

### What changes for you

- **Level 3 and up need the setup read.** It reads three parts: your rules, how you correct them over time, and your memory. A part counts only when the code finds the structure in your own files (what loads when, how deep it goes, whether memory is a flat log or organised) and an AI reader quotes a line the code then finds in that file. If either half is missing, or the read did not run, the page says "Level 2, could be Level 3", never Level 3.
- **Endless sessions hold the level.** If your sessions only ever compact automatically near the limit, and you never reset one yourself, the level stays at Level 2. The page tells you why.
- **Claude's defaults are no longer yours.** Built-in memory, automatic compaction and sub-agents the model starts on its own show as "Claude did this" and credit nothing.
- **The page tells one story.** Four tabs: Review (what works, quoting the line that shows it, and what to change first, with what it costs and one step), Your level, What you've built, Share with Proxify. The practice and discipline tabs are gone.
- A quoted line that looks like it holds a secret is never shown.
- The Share summary sends the same fields as before. The receiver does not accept the new ones yet.

### Known limits

- One run's AI part costs about 350k tokens on a large setup, against a target of about 200k. The old practice pass is still in; dropping it is pending.
- Repos are found only from where a session starts. If you start sessions in your home folder and work across many repos, you read low on "what you built" and Level 4. Fixed in 0.6.5.
- Every threshold is a guess until it is checked against levels confirmed on calls.
- The share receiver needs three new field names before the new lines can be sent.

Pull request: #9. It went out without the second round of page changes, which 0.6.4 carries.

## 0.6.2 — 2026-11-11

### What changes for you

- **The scan's cache cleans up after itself.** The scan keeps a speed-up cache in `~/.cache/setup-snapshot/portable/`. Each version wrote its own 1-2 GB copy there and nothing deleted the old ones. Before saving a new copy, the scan now deletes every cache file older than its 6-hour lifetime, plus half-written files from runs that stopped partway. A copy from another version that is still within its 6 hours, and files the cache did not write, are left alone.
- On your first run of 0.6.2, the copies left by earlier versions are removed.
- The skill and the README now say the cache exists and that you can delete it.

Pull request: #8

## 0.6.1 — 2026-11-11

### What changes for you

- **The page opens on Your level**: the level your laptop reads, said to you as a starting point a call confirms or raises, never lowers. Proxify's internal tabs (substrate, call sheet, internal level read) are not on your copy.
- `AGENTIC_SETUP_NO_LEVEL=1` (the old name `AGENTIC_SETUP_MEMBER=1` still works) gives the same copy with no level.

Pull request: #7

## 0.6.0 — 2026-11-11

### What changes for you

- **A new `/insights:my-agentic-setup`.** One page from the files Claude Code keeps (a scan plus one AI pass in your own session): what you do, what you built, each discipline part by part, what to try next, and the level your laptop reads, which a call confirms or raises.
- `AGENTIC_SETUP_MEMBER=1` gives a copy with no level.
- It replaces 0.5.0.

Pull request: #6

## 0.5.0 — 2026-10-26

### What changes for you

- **No level and no score.** The page shows how you work and what to try next.
- **Two tabs:** How you work, where each finding has "how we know" folded under it, and What to try next, with up to 4 small steps.
- **A brief for your own agent:** it works one step at a time, asks first, and opens pull requests it never merges.
- **Share with Proxify** is built from the same scan, so there is no second scan. It is masked, holds no grading, and is made only if you say yes.
- Removed: the level guide, the AI level readers, the counted level checks and the evidence pack.
- The wording addresses you directly, with fewer negatives.

Pull request: #3

## 0.4.0 — 2026-10-26

### What changes for you

- **AI readers decide the whole level, 1 to 4**, from one picture of how you work: who carries the context, you chat by chat (Level 2) or your setup (Level 3). Every Level 3 and up answer gets 3 readers; a judge settles splits but cannot overrule two readers without evidence.
- The evidence also carries the Level 1 and 2 numbers (planning, steering, checking, parallel work), as numbers only.
- The page leads with the readers' level. Without the Workflow tool (an older Claude Code), it shows the counted checks alone, labelled "a rough hint".

Pull request: #2

## 0.3.1 — 2026-10-26

### What changes for you

- **Chats typed in VS Code, JetBrains or the desktop app now count as yours.** These apps drive Claude Code through the Agent SDK, so the scan had set every such chat aside as a script's, and the page said "not enough to read". Script runs are still set aside.
- **The page and its files go to `~/agentic-setup/`, not `/tmp`.** Ubuntu's Firefox (a snap) has its own private `/tmp` and showed "File not found". The browser now opens a real file link.

Pull request: #1

## 0.3.0 — 2026-10-23

### What changes for you

- Level 3 is judged by AI readers from your laptop's records.
- A git identity step: the scan reads the emails git knows for you and you confirm them.
- A chat started in a parent folder belongs to the repo it worked in.
- Scheduled jobs that start an agent (LaunchAgent, cron, systemd) are read.
- What you built and what you borrowed is told apart by git authorship.

No pull request: sent before pull requests were used. Source: the public commit `ec299a67`.

## 0.2.2 — 2026-10-22

### What changes for you

- The bars scale to the days of chats you kept.
- "Below Level 1" is shown apart from "not enough to read".
- A typed `/compact` counts as your choice.
- Linked skills are judged by who wrote them, not by being links.

No pull request. Source: the public commit `099d04f4`.

## 0.2.1 — 2026-10-22

### What changes for you

- One scan and one page with tabs: Your level, How you work, How it was worked out.

No pull request. Source: the public commit `074f6250`.

## 0.2.0 — 2026-10-22

### What changes for you

- A page that suggests your level (1 to 4) from what you built, not what you installed.

No pull request. Source: the public commit `af9aea53`.

## 0.1.0 — 2026-10-20

### What changes for you

- The first version: `/insights:my-agentic-setup` shows how you really work with Claude Code, from the files it keeps on your machine.

No pull request. Source: the public commit `f9d9755b`.
