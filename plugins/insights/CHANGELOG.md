# insights: what changed

To update, in a terminal (not inside a Claude Code chat), run these two commands, then restart Claude Code:

```
claude plugin marketplace update agentic-kit
claude plugin update insights@agentic-kit
```

You get a new version only when it is released: each entry below is one release, newest first. The numbers in
brackets are pull requests on proxify-dev/agentic-kit. Versions 0.1.1, 0.2.3, 0.4.1 and 0.6.7 were never sent out; their
changes arrived in the version after them.

## 0.6.10 — 2026-11-13

### What changes for you

- **The memory verdict for Claude Code's own memory no longer changes between runs** (#46). When the only memory a project has is the folder Claude Code keeps by itself, the setup read now says "not shown" every time, with the reason, instead of the AI reader's word, which varied between readers and runs. A memory quote can no longer come from that folder.
- **Correction to the 0.6.9 notes.** They said four problem checks that look at the last 30 days had no start to their window and that their counts could go down. That was wrong: those checks take their date from a record that was always right, and their counts did not change because of the fix. Only the header's date and the Review's "Period" line were wrong. The 0.6.9 entry below is corrected.

### Known limits

- A project with no memory store at all still shows the AI reader's word for memory; code already keeps it from counting.
- Everything listed under 0.6.9 still holds.

Pull requests: #46.

## 0.6.9 — 2026-11-13

### What changes for you

- **The page shows the day of the scan again** (#43). Since 0.6.6 the header read a day in early January 1970 ("read 2 Jan") on a Mac, because the scan saved the time since the laptop started as its date. The Review's "Period" line was wrong the same way. The date now comes from the calendar clock, and a test fails if a timer value is ever used as a date again.
- **Only your own memory counts as yours** (#45). Before, any folder named "memory" counted, a code module included, and every memory store on the laptop was listed as yours, so a repo you only cloned could fill "What you've built → Memory" with other people's notes. Now a folder is memory by its shape (an index that links to its notes, or notes that link each other), it is yours unless it sits in a repo mostly by others with no commit and no session of yours writing in it, and a copy of a store you already have counts once. A laptop whose only memory was someone else's can read Level 1 where it read Level 2.
- **Memory reads are counted right** (#45). Only files opened or written inside a store count, not reads of a folder above it, and sessions this tool started to read your setup no longer count as you using your memory. The number on the page goes down.

### Known limits

- What makes a folder memory (2 links, 3 notes) and a copy (half its notes identical) are first guesses until checked on more laptops. A memory folder with one subfolder per agent, each with its own index, is read as one store.
- Each AI reader still gives its own word for Claude Code's own memory; code already keeps it from counting.
- Everything listed under 0.6.8 still holds.

Pull requests: #43, #45.

## 0.6.8 — 2026-11-13

### What changes for you

- **A copy of someone else's skill is no longer counted as yours** (#34). Before, a skill or instruction file that was a copy of someone else's work (unpacked into a cache, a backups folder or a fixtures folder, with no install receipt) was listed under what you wrote. Now it is listed as installed when three things hold: its bytes equal the file at the head of a clone whose commits are mostly other people's, the copy is newer than that file, and you never edited it in a session. A skills lock in another folder also counts as a receipt. Anything that fails a proof stays yours. Your "what you wrote" counts can go down, and a laptop whose only own thing was such a copy reads Level 1 where it read Level 2.
- **A skill you downloaded and committed once is no longer counted as yours either** (#38). A skill installed from someone else's repository and committed once into a project of yours read as yours when its original sat in a clone you keep in your home folder. The scan now also looks in the git repositories in your home folder or one folder below it, with the same proofs. A skills lock that names your own repository as the source of one of your skills is no longer read as an install of it.
- **"What you've built" shows where each thing you built stands** (#35). Each of your most used skills, sub-agents, plugins, hooks and evals gets one line: the step it sits on, in words (six steps, from a note to tested by evals; a step needs every step below it), what it did in the last 30 days counted by code, and the one change that would move it up. Code places what it can prove; the per-project AI reader places the rest, with a quoted line that code checks. A hook of your own plugin is shown from its source and counted once.
- **The scan is much faster on a laptop with many sessions** (#36, #39). The checks step went from about 28 minutes to 5, and far fewer checks run out of time (about 5 of 157, where 92 did). The file inventory and the commit check now ask git for a repository's history once, not once per file, with the same answers. On one such laptop, busy with other work, the scan went from about 38 minutes to about 8. No answer changed because of the code. Four steps nothing on the page reads (the size, the uncommitted files and the merge state of each linked worktree, and the git ignore check on a file with no commits) now run only when you switch them on, with `AGENTIC_SETUP_WORKTREE_SIZES`, `AGENTIC_SETUP_WORKTREE_DIRTY`, `AGENTIC_SETUP_WORKTREE_MERGED` or `AGENTIC_SETUP_IGNORE_CHECK` set to `1`.
- **A busy laptop no longer loses the history of your files** (#41). Before, a 40-second budget for git history lookups ran out under load and every instruction file was left without history, so the counts of your own skills and instruction files changed from one run to the next. The budget now covers only lookups made one path at a time; reading a whole repository has its own limit of 180 seconds. When files are still left without history, one line above the tabs says how many.
- **Level 4 waits for its check** (#40). The check for changes to what you built in 3 or more different weeks was read as met when it timed out, which happened in about 1 run in 10 on a busy laptop. It now has a limit 4 times longer, and when it still has no answer the level reads "L3, could be L4" with the reason said, never L4.

### Known limits

- A full run still takes several minutes on a laptop with many sessions: on the laptop above, about 8 minutes for the scan and about 4 for the AI reading. A few checks can still run out of time on a busy one.
- A repository whose whole history takes more than 180 seconds to read, or that has more than 80,000 commits, is read one file at a time, and files can be left without history; the page says how many.
- The original of a downloaded skill is looked for only in repositories in your home folder or one folder below it, and only for a skill that sets publishing fields. Only a clone's current head is matched when a copy elsewhere is checked, not its older versions, and a copy made with its original timestamps kept is not caught.
- Advice never changes a level.
- The craft reading adds AI cost: the AI reading for three large projects cost about $2.60, against about $1.80 before. One run's AI part is about 350k tokens on a large setup, against a target of about 200k.
- What a sub-agent did inside its own run is not counted yet, only its runs. A hook started through a launcher script is judged by the launcher's text.
- Folders without git are not read as projects.
- Folders of generated files that each hold a rule file still count as separate rule files.
- Share still sends the older read's lines until the receiving side accepts the new fields.
- Every threshold is a guess until it is checked against levels confirmed on calls.

Pull requests: #34, #35, #36, #38, #39, #40, #41.

## 0.6.6 — 2026-11-12

### What changes for you

- **The page calls the instruction file `AGENTS.md`** (or `CLAUDE.md`) where it said "rule file". `.claude/rules/` keeps its own name. Wording only: no number or level changes (#28).
- **The browser's find works across tabs.** The text in every tab and panel is in the page, hidden until found, and a "Show everything" control opens it all (#30).
- **A broken count no longer blanks a tab.** A count that is not a number makes its own row read "couldn't check", and the rest of the tab shows (#29).
- **Advice on your `AGENTS.md`**, for your busiest projects, as cards on the Review tab (#31): a pointer to a file that does not exist, an `AGENTS.md` that never loads, no command to check the work, lines that restate the repo, and your weakest line with a rewrite. An `AGENTS.md` that Claude Code never reads is no longer counted as loaded.

### Known limits

- A full scan still takes 20 minutes or more on a laptop with many sessions, and some of its checks run out of time on a first run. This is next.
- The weakest-line card needs the AI reader and shows "couldn't check" when its quote cannot be verified against your file.
- Advice never changes a level.
- One run's AI part costs about 350k tokens on a large setup, against a target of about 200k.
- Folders without git are not read as projects.
- Folders of generated files that each hold a rule file still count as separate rule files.
- Share still sends the older read's lines until the receiving side accepts the new fields.
- Every threshold is a guess until it is checked against levels confirmed on calls.

Pull requests: #27, #28, #29, #30, #31. (#27 changes no behaviour.)

## 0.6.5 — 2026-11-12

### What changes for you

- **Repos are found from where a session worked**, not only from the folder it started in: files edited there, `cd` or `git -C` there, commits there. A repo that was only read from does not count. When most of your sessions start outside a repo, the page says so, and a zero reads "couldn't see", not "didn't build" (#14).
- **Every tab shows the same number for the same thing** (#15).
  - Programs that start the agent show as scripts, SDK programs and schedules on both tabs.
  - Tools from others give two counts: skills from others, and MCP servers.
  - Evals read "changed in 30 days, no run recorded" when nothing ran.
  - Every project read is listed. Level 3 reads "0 fully shown, 3 partly", and each part names its project.
  - Plurals are fixed, quotes are whole sentences, and schedules count only when they are your own.
  - The Share tab marks every line that comes from the older read.
- **One rule-file count, and the same projects on every run** (#16). Copies of rule files that Claude Code keeps in its plugin cache are not counted as yours. Two projects with the same activity are always picked in the same order.
- **The scan can no longer run forever on an odd path** (#19). A session that had written to a path starting with `//` could make the scan walk up for ever. It now stops, and odd targets (`//…`, `/dev/null`, shell variables, URLs) are not treated as folders. One session log that takes more than 5 minutes is skipped and reported as "couldn't check"; the rest of the run carries on.
- **A skill you type as `/name` counts as used** (#11). A skill that is yours stays yours when teammates edited some of its files, and the code it names outside its folder is read.
- **Sessions in the desktop app or an editor count toward your busiest projects**, not only terminal sessions (#13).
- **The scan reads MCP servers built into your own repos** (#18). They are not counted toward a level yet.

### Known limits

- A full scan takes 20 minutes or more on a laptop with many sessions, and many of its checks run out of time on a first run. A fix is in progress for the next version.
- One run's AI part costs about 350k tokens on a large setup, against a target of about 200k.
- Folders without git are not read as projects.
- Folders of generated files that each hold a rule file still count as separate rule files.
- Share still sends the older read's lines until the receiving side accepts the new fields.
- Every threshold is a guess until it is checked against levels confirmed on calls.

Pull requests: #11, #13, #14, #15, #16, #17, #18, #19. (#17 changes no behaviour.)

## 0.6.4 — 2026-11-11

### What changes for you

- **What you've built** is redesigned. Each kind of thing you built is a tile that says whether you used it this month. The lists are sorted by use. Each skill's steps are in plain words. Your hooks sit on a session timeline at the moment they run.
- **Change this first** on the Review tab is cut to one card and three lines.
- Each thing is counted once.
- How a level is read is the same as in 0.6.3.

### Known limits

- One run's AI part costs about 350k tokens on a large setup, against a target of about 200k. The old practice pass is still in; dropping it is pending.
- Repos are found only from where a session starts. If you start sessions in your home folder and work across many repos, you read low on "what you built" and Level 4. Fixed in 0.6.5 (#14).
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
- Repos are found only from where a session starts. If you start sessions in your home folder and work across many repos, you read low on "what you built" and Level 4. Fixed in 0.6.5 (#14).
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
