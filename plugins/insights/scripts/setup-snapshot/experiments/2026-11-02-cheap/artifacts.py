#!/usr/bin/env python3
"""The cheap version, code part 2: everything the person built, one compact digest per artifact, plus what they do by
hand, for the single AI pass to map onto v15 practices. No AI here; standard library only; read-only.

Artifacts: hooks that ran (script header + the text they put in front of the agent), their own plugins, agents,
programs that start agents (with the flags that set model, budget, tools, schedule), eval folders (cases, runs, how the
runner keeps the agent out, results kept in docs), how `claude` launches (wrapper, gateway), their own tools outside
plugins (top-level folders with a README and code), skills, output styles, instruction files, memory stores, settings
that limit the agent, git hooks. Behaviour: counts the inventory already holds (slash commands, plan refusals,
compactions, helpers, worktrees) and typed asks found by fixed patterns.

    python3 artifacts.py RUN_DIR   → RUN_DIR/cheap-digest.md (and cheap-artifacts.json)
"""
import json
import os
import re
import sys
from collections import Counter

HOME = os.path.expanduser("~")
SKIP = {"node_modules", ".git", "dist", "build", ".venv", "venv", "__pycache__", "plugins", "worktrees", "w"}


def t(p):
    return (p or "").replace(HOME, "~")


def cut(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def header(path, n=400):
    """The top of a file: its docstring or leading comments, which say what it is for."""
    try:
        with open(path, errors="replace") as f:
            head = f.read(4000)
    except OSError:
        return ""
    m = re.search(r'"""(.*?)"""', head, re.S) or re.search(r"/\*\*?(.*?)\*/", head, re.S)
    if m:
        return cut(m.group(1), n)
    lines = [ln.lstrip("#/ ").strip() for ln in head.splitlines()[:30] if ln.strip().startswith(("#", "//")) and not ln.startswith("#!")]
    return cut(" ".join(lines), n)


def flag_lines(path, rx, k=6):
    out = []
    try:
        for ln in open(path, errors="replace"):
            if rx.search(ln):
                out.append(cut(ln.strip(), 110))
                if len(out) >= k:
                    break
    except OSError:
        pass
    return out


def typed(C, rx):
    n, chats, ex = 0, set(), None
    for c in C:
        for m in c["msgs"]:
            h = m["head"]
            if h.lstrip().startswith(("Another Claude session", "<teammate-message")):
                continue
            if rx.search(h):
                n += 1
                chats.add(c["chat"])
                ex = ex or f"{c['chat'][:8]}: '{cut(h, 80)}'"
    return n, len(chats), ex


def main(run):
    I = json.load(open(os.path.join(run, "inventory.json")))
    A = json.load(open(os.path.join(run, "anatomy.json")))
    with open(os.path.join(run, "chats.jsonl")) as f:
        f.readline()
        C = [json.loads(x) for x in f]
    plugins = {p["key"].split("@")[0]: p for p in I["plugins"]}
    own_plugins = {k for k, p in plugins.items() if p.get("yours")}
    L = []
    w = L.append
    arts = []

    def art(aid, kind, text, **facts):
        # own: whose it is by ownership.py (via the inventory); laptop_evidence.fact() reads a missing own as built, so
        # every built-thing id says it (11-05: an installed plugin's hook and the person's own agents had none)
        arts.append({"id": aid, "kind": kind, "text": text, **facts})
        w(f"- [{aid}] {text}")

    def reg(aid, kind, text, **facts):
        """An id the AI pass can cite for a thing in a one-line list (skills, rule files, memory stores, settings): the
        same record as art(), plus the facts laptop_evidence.fact() reads (own: git says it is theirs; reads: sessions
        that read it). Before 11-05 these had no id, so evidence resting on them read as the AI's opinion."""
        arts.append({"id": aid, "kind": kind, "text": text, **facts})
        return f"[{aid}] "

    w("# What this person built, and what they do by hand (30 days)\n")
    w("Authorship is git's: 'theirs a/b' = a of b commits on its source are theirs. 'ran' = it ran in the 30 days.\n")

    # ---- hooks that ran, grouped by script, with the text they put in front of the agent
    w("## Hooks that ran (event · set up by · runs/sessions · what it is · text it adds to Claude's context)")
    texts = {}
    for r in I["hook_context"]["by_event_and_tag_typed"]:
        texts.setdefault(r["what"][1].split(":")[0].strip(), []).append(f"{r['what'][0]} '{cut(r['texts'][0] if r.get('texts') else '', 90)}' {r['calls']}/{r['chats']}ch")
    seen = Counter()
    for blk in ("typed_chats", "headless"):
        for h in (I.get("hook_runs") or {}).get(blk) or []:
            script = h["script"]
            key = re.sub(r"/\d+\.\d+\.\d+/", "/", script)
            if seen[key] or h["chats"] < 2:
                continue
            seen[key] += 1
            who = h["set_up_by"].split("@")[0]
            owner = (f"theirs {plugins[who].get('theirs')}/{plugins[who].get('commits')}" if who in plugins and plugins[who].get("commits")
                     else (f"theirs {h.get('theirs')}/{h.get('commits')}" if h.get("commits") else "authorship unknown"))
            path = os.path.expanduser(script) if script.startswith("~") else script
            hd = header(path, 220) if os.path.isfile(path) else ""
            tx = "; ".join((texts.get(who) or [])[:3])
            mine = plugins[who].get("yours") if who in plugins else h.get("yours")
            art(f"hook:{who}:{os.path.basename(script)}", "hook", own=bool(mine), text=
                f"{h['event']} · {h['set_up_by']} ({owner}) · {h['runs']}/{h['chats']} {'interactive sessions' if blk == 'typed_chats' else 'headless runs'} · "
                f"{cut(hd, 220)}" + (f" · puts in: {cut(tx, 300)}" if tx else ""))
    bl = I["hook_context"]["blocking_errors"]
    if bl:
        w("refusals by hooks: " + "; ".join(f"{r['what'][0]} [{r['what'][1]}] {r['calls']}/{r['chats']}ch '{cut(r['texts'][0] if r.get('texts') else '', 70)}'" for r in bl[:5]))

    # ---- own plugins
    w("\n## Their own plugins (source has their commits)")
    for k in sorted(own_plugins):
        p = plugins[k]
        art(f"plugin:{k}", "plugin", own=True, text=f"{p['key']} {'on' if p['on'] else 'off'} · theirs {p.get('theirs')}/{p.get('commits')} · adds {p.get('adds')}"
            + (" · has evals" if p.get("has_evals") else ""))

    # ---- agents
    w("\n## Their own agent files (name [model] tools · description · used as a sub-agent in interactive sessions)")
    used = {x["type"].split(":")[-1]: x for x in I["helpers"]["by_type"]}
    for a in [x for x in I["own_artifacts"]["own"] if x["kind"] == "agent"][:70]:
        u = used.get(a["name"])
        art(f"agent:{a['name']}", "agent", own=True, text=f"{a['name']} [{a.get('model') or 'default'}] tools {cut(a.get('tools') or '(all)', 50)} · "
            f"{cut(a.get('description'), 90)} · @{t(os.path.dirname(a['path']))[:45]}" + (f" · sub-agent calls {u['calls']}/{u['chats']}ch" if u else ""))

    # ---- programs that start agents, schedules
    w("\n## Programs that start agents (header · lines that set model, budget, tools, schedule)")
    rx = re.compile(r"--model|max-budget|maxBudget|--allowedTools|--disallowed|--tools|--agent|max-turns|maxTurns|crontab|"
                    r"setting-sources|permission-mode|dontAsk|budget|cap\b|spend", re.I)
    shown = 0
    # repos that start the most headless runs first, at most 10 programs per repo
    runs_by_root = {r["repo"]: r.get("headless_runs", 0) for r in I["repos"]}

    def root_of(p_):
        return max((r for r in runs_by_root if p_.startswith(r + "/")), key=len, default="")
    per_root = Counter()
    for sc in sorted((A.get("launchers") or {}).get("scripts") or I["launchers"]["scripts"],
                     key=lambda x: (-runs_by_root.get(root_of(x["path"]), 0),
                                    0 if re.search(r"service|runtime|launch|cron|spend|run\.py|observe", x["path"]) else 1, x["path"])):
        if sc.get("only_in_comments"):
            continue
        pth = sc["path"]
        # build output and bundles are copies of source, not what they wrote
        if re.search(r"/\.upstreams/|/tests?/|\.test\.|/node_modules/|/dist[^/]*/|/build/|/\.next/|-[A-Za-z0-9_-]{8}\.js$|\.min\.js$", pth):
            continue
        try:
            if os.path.getsize(pth) > 300_000:
                continue
        except OSError:
            continue
        fl = flag_lines(pth, rx)
        if not fl or per_root[root_of(pth)] >= 10:
            continue
        per_root[root_of(pth)] += 1
        art(f"runner:{t(pth)}", "runner", f"{t(pth)} · {cut(header(pth, 200), 200)} · " + " | ".join(fl))
        shown += 1
        if shown >= 45:
            break
    hl = I["headless"]
    w(f"headless runs: {hl['runs']} on {hl['days_with_runs']} days; {hl.get('while_away', hl['started_with_no_typed_message_in_30_min_before'])} of them while away (nothing typed in the 30 min before, or a scheduler started them)")
    for x in hl["by_template"][:10]:
        w(f"  - {t(x['repo'])} '{cut(x['template'], 45)}' {x['runs']} runs, {x['days']} days, {x.get('while_away', x['started_with_no_typed_message_in_30_min_before'])} while away; models {list(x['models'])[:2]}")
    w("schedule logs: " + "; ".join(f"{x['path']} {x['lines']} lines, last {x['last_written']}" for x in I.get("schedule_logs") or []))
    w("crontab now: " + str([cut(x["command"], 80) for x in I["launchers"]["crontab"]]))
    # scheduled jobs that start an agent get ids the AI pass can cite (11-05: a person's 8 LaunchAgents running claude -p
    # were never shown; the line printed only the agents that start no AI tool). own: ownership.py via inventory.
    sched = [(f"schedule:{x.get('label') or os.path.basename(x.get('path') or '')}", x, " ".join(map(str, x.get("program_arguments") or []))[:90])
             for x in I["launchers"].get("launchd_matching") or []]
    sched += [(f"schedule:cron:{cut(c['command'], 40)}", c, cut(c["command"], 90))
              for c in I["launchers"]["crontab"] if c.get("starts_agents")]
    w("launchd agents and crontab lines that start an agent: " + ("; ".join(
        reg(sid, "schedule", f"{sid} runs {cmd}", own=x.get("own", True) is not False) + f"runs {cmd}"
        + (", at load" if x.get("run_at_load") else "") + (f", every {x['start_interval']}s" if x.get("start_interval") else "")
        + (", on a calendar" if x.get("has_calendar_interval") else "") + (f", {x['schedule']}" if x.get("schedule") else "")
        for sid, x, cmd in sched) or "none"))
    w("other launchd agents (start no AI tool): " + ", ".join(I["launchers"].get("launchd_other") or [])[:400])

    # ---- test files that check agent prompts, launchers, hooks (a test suite the agent runs against its own setup)
    tests = []
    for root in [r["repo"] for r in I["repos"] if r.get("theirs_in_window") and os.path.isdir(r["repo"])]:
        for d, subdirs, files in os.walk(root):
            subdirs[:] = [x for x in subdirs if (x not in SKIP and x not in ("playground", "repos", "references", "vendor")
                                                 and not x.startswith(".")
                                                 and not os.path.exists(os.path.join(d, x, ".git")))]
            if d.count("/") - root.count("/") > 5:
                subdirs[:] = []
            for f in files:
                if re.search(r"(prompt|launch|agent|hook|stop|eval|rules?)[\w-]*\.test\.(mjs|js|ts|py)$|test_(prompt|hook|agent|eval)\w*\.py$", f):
                    tests.append(os.path.join(d, f))
    if tests:
        w("\n## Their test files that check agent prompts, headless-run scripts or hooks")
        for pth in tests[:15]:
            art(f"test:{t(pth)}", "test", f"{t(pth)} · {cut(header(pth, 200), 200)}")

    # ---- evals
    w("\n## Eval folders (runs in window/all · last · cases · theirs · how the runner keeps the agent out · results kept in docs)")
    for e in I["evals"]:
        if not (e["ledger_lines"] or e["case_files"] or e.get("results_in_docs")):
            continue
        art(f"eval:{t(e['path'])}", "eval", f"{t(e['path'])} · {e['ledger_lines_in_window']}/{e['ledger_lines']} runs · last {e['last_run']} · "
            f"{e['case_files']} cases · theirs {e['theirs']}/{e['commits']} · runner: {cut(' | '.join(e.get('runner_keeps_agent_out') or []), 200)} · "
            f"results: {cut(' | '.join(e.get('results_in_docs') or []), 160)}")

    # ---- how claude launches; own tools outside plugins
    w("\n## How `claude` starts, and their own tools outside plugins")
    for x in I.get("claude_launch") or []:
        if x["kind"] == "wrapper script":
            confs = []
            for cc in x.get("config") or []:
                for f_ in cc["active"]:
                    fp = os.path.join(os.path.expanduser(cc["dir"].replace("~", HOME)), f_)
                    body = [ln.strip() for ln in open(fp, errors="replace") if ln.strip() and not ln.strip().startswith("#")][:4] if os.path.isfile(fp) else []
                    confs.append(f"{f_}: " + cut(" | ".join(body), 160))
            art("launch:claude-wrapper", "launch", f"`claude` is a wrapper script {x['path']} · active config: {'; '.join(dict.fromkeys(confs))}")
    roots = [r["repo"] for r in I["repos"] if r.get("theirs_in_window") and os.path.isdir(r["repo"])]
    for root in roots:
        for d in sorted(os.listdir(root)):
            p = os.path.join(root, d)
            if d in SKIP or d.startswith(".") or not os.path.isdir(p):
                continue
            readme = next((os.path.join(p, f) for f in ("README.md", "readme.md") if os.path.isfile(os.path.join(p, f))), None)
            code = any(os.path.exists(os.path.join(p, f)) for f in ("package.json", "pyproject.toml", "src", "go.mod", "Cargo.toml"))
            if not (readme and code):
                continue
            docs = os.listdir(os.path.join(p, "docs")) if os.path.isdir(os.path.join(p, "docs")) else []
            try:
                body = open(readme, errors="replace").read(1500)
            except OSError:
                body = ""
            art(f"tool:{t(p)}", "tool", f"{t(p)} · README: {cut(re.sub(r'[#*`>]', '', body), 260)}" + (f" · docs: {', '.join(sorted(docs)[:10])}" if docs else ""))

    # ---- skills, output styles, commands, instruction files, stores, settings, git hooks
    oa = I["own_artifacts"]["own"]
    sk_used = {r["what"].split(":")[-1]: r for r in I["skills_used"]}
    sk = [a for a in oa if a["kind"] == "skill"]
    w(f"\n## Their own skills ({len(sk)}): name (uses in interactive sessions) — description")
    # own_artifacts["own"] is the inventory's list of what git credits to them (most of a file's commits; 11-05)
    w("; ".join(reg(f"skill:{a['name']}", "skill", f"{a['name']} — {cut(a.get('description'), 60)}", own=True,
                    uses=(sk_used.get(a["name"]) or {}).get("calls", 0))
                + f"{a['name']}" + (f" ({sk_used[a['name']]['calls']})" if a["name"] in sk_used else "") + f" — {cut(a.get('description'), 60)}" for a in sk[:90]))
    for kind in ("output_style", "command", "workflow", "hook_script"):
        xs = [a for a in oa if a["kind"] == kind]
        if xs:
            w(f"{kind}s: " + "; ".join(reg(f"{kind}:{a['name']}", kind, f"{a['name']} @{t(os.path.dirname(a['path']))[:40]}", own=True)
                                      + f"{a['name']} @{t(os.path.dirname(a['path']))[:40]}" + (f" — {cut(a.get('description'), 60)}" if a.get("description") else "") for a in xs[:20]))
    w("\n## Instruction files that load every time (words now, peak, trims, theirs/commits) and rules by folder")
    for x in I["instruction_files"]["always_loaded"][:10]:
        # theirs when most of its commits are; a file git doesn't track is theirs only in their own home (never by
        # being under ~/.claude: installed copies live in the plugin cache, not here)
        own = (2 * (x.get("theirs") or 0) > x["commits"]) if x.get("commits") else str(x["path"]).startswith(os.path.expanduser("~") + os.sep)
        w("- " + reg(f"rules:{t(x['path'])}", "rules", f"{t(x['path'])}: {x['words_now']}w", own=own) + f"{t(x['path'])}: {x['words_now']}w" + (f", peak {x.get('peak_words')} on {x.get('peak_date')}, {len(x.get('trims') or [])} trims, {x.get('theirs')}/{x.get('commits')}" if x.get("commits") else ""))
    rr = I["rules_reaching"]
    w(f"subfolder rule files loaded into Claude's context in {rr['chats_where_a_subfolder_file_reached_the_agent']} sessions; agent edits to instruction/skill/agent files: "
      f"{I['instruction_edits']['edits']} in {I['instruction_edits']['chats']} sessions")
    # a store read in a session is Claude Code's own record (a file read); who wrote the notes is not the question here
    w("memory stores: " + "; ".join(reg(f"memory:{t(s['path'])}", "memory", f"{t(s['path'])} ({s['kind']}, {s['files']} files)", reads=s.get("chats_reading") or 0)
                                    + f"{t(s['path'])} ({s['kind']}, {s['files']} files, read in {s['chats_reading']} sessions)" for s in I["memory_stores"][:8]))
    w("databases: " + "; ".join(f"{t(d['path'])} {d.get('tables')} ← {d.get('referenced_by')}" for d in I["databases"][:4]))
    w("settings that limit the agent: " + "; ".join(
        # their own settings: the home file (scope "home") and a project's settings.local.json are files only they write;
        # a project's shared settings.json may be a teammate's, so it is not credited here
        reg(f"settings:{t(s['path'])}", "settings", t(s["path"]),
            own=s.get("scope") == "home" or str(s["path"]).endswith("settings.local.json"))
        + f"{t(s['path'])}: " + ", ".join(f"{k} {cut(s.get(k), 80)}" for k in ("default_mode", "deny_rules", "ask_rules", "denied_mcp_servers",
                                                                          "skill_overrides", "enabledMcpjsonServers", "disabledMcpjsonServers", "sandbox") if s.get(k))
        for s in I["settings"] if any(s.get(k) for k in ("deny_rules", "ask_rules", "denied_mcp_servers", "disabledMcpjsonServers", "sandbox"))
        and "/worktrees/" not in s["path"] and "/w/" not in s["path"])[:1500])
    w("MCP servers set up per project: " + "; ".join(f"{m['name']} ({m['scope']}{', ' + cut(' '.join(map(str, m['args'])), 50) if isinstance(m.get('args'), list) and m['args'] else ''})" for m in I["mcp_servers"] if m["scope"] == "project")[:700])
    # one id per git hook (11-05: git hooks had none, so a hook that blocks a commit couldn't carry a practice)
    w("git hooks: " + ("; ".join(
        reg(f"git_hook:{t(g['dir'])}/{h}", "git_hook", f"{t(g['dir'])}/{h} — {cut(header(os.path.join(g['dir'], h), 120), 120)}",
            own=g.get("yours", True) is not False)
        + f"{t(g['dir'])}/{h} — {cut(header(os.path.join(g['dir'], h), 120), 120)}"
        + (f" (theirs {g.get('theirs')}/{g.get('commits')} commits)" if g.get("commits") else "")
        for g in I["git_hooks"] for h in g["hooks"][:6]) or "none"))

    # ---- behaviour: two lists the AI pass must not mix (11-11). One "by hand" list used to hold the person's own acts
    # beside what Claude Code and the model do by default (the model's own sub-agents, automatic compactions, built-in
    # memory, plan files), with no minimum, and a person read "sends side work to sub-agents by hand" from 126 calls the
    # model chose. The split follows the scan's own who-did-it groups (inventory.py who_did_it): "you" = only a person
    # can produce the record; "claude" and "cant" = a default, the model's judgement, or nothing records who asked.
    who = {g["id"]: g.get("items") or [] for g in (I.get("who_did_it") or {}).get("groups") or []}

    def counted(i):
        return (f"- {i['label']}: {i['n']}" + (f" in {i['chats']} sessions" if i.get("chats") is not None else "")
                + (f" (of {i['of']})" if i.get("of") else ""))

    pd, cp = I["plan_dialogs"], I["compactions"]
    w("\n## What they do by hand (counts over 30 days)")
    w("Only the person can produce these records: Claude Code writes them when the person acts. Only this list can show a habit done by hand.")
    sc = {c["command"]: c for c in I["slash_commands"].get("by_command", [])}
    # the person's own most-typed commands, whatever they are called (not a list picked on one laptop)
    w(f"slash commands typed, as N times in M of your {I['slash_commands'].get('typed_sessions')} typed sessions (session logs; history.jsonl only for a command the logs never record): "
      + ", ".join(f"{k} {c['times']} times in {c['chats']}" for k, c in list(sc.items())[:15]))
    for i in who.get("you", []):
        w(counted(i))
    w(f"- plan dialogs the person answered: approved {pd['calls'] - pd['refused']}; rejected {pd['refused']}, in {pd['chats_with_a_refusal']} sessions")
    w(f"- typed /compact commands that said what to keep: {cp['with_focus_text']} of {cp['typed_compact_commands']}")
    # typed asks close the person's list: they typed them, but a word match can point to a habit, never prove one
    # (laptop_evidence.py WORD_LIST_LINE keys on "N messages in M sessions")
    w("typed asks found by fixed English patterns (a word match: it can point to a habit, not prove one):")
    asks = {
        "asks the agent to disagree or critique": r"prove me wrong|critique|devil'?s advocate|what am i missing|poke holes|find (the )?flaws|be (brutally )?honest|push back|change your mind",
        "says they are lost / asks for a walkthrough": r"i'?m lost|walk me through|explain (it )?like|recontextuali[sz]e|eli5",
        "asks to see the diff or try it themselves": r"show me the diff|where are the changes|let me (try|test|see)|can i (try|test|see)",
        "asks whether it was tested": r"did you (test|run|check)|have you tested|is it tested",
        "names a handoff or an earlier session": r"handoff|hand-off|previous (chat|session)|as per [0-9a-f]{8}",
        "tells it to work in a worktree": r"worktree",
    }
    for label, pat in asks.items():
        n, ch, ex = typed(C, re.compile(pat, re.I))
        w(f"- {label}: {n} messages in {ch} sessions" + (f" (e.g. {ex})" if ex else ""))

    h = I["helpers"]
    tools = {r["what"]: r for r in I["tools"]["typed_chats"]["by_tool"]}
    pw = I["past_work_reach"]
    pm = I["chats_logged"]["permission_modes_of_typed_messages"]
    auto = (cp.get("by_trigger") or {}).get("auto", 0)
    w("\n## What Claude Code or the model did unasked (counts over 30 days)")
    w("Claude Code's defaults and the model's own choices, or calls where nothing records who asked. Never the person's habit, however large the number.")
    w(f"- sub-agent calls the model made: {h['calls']} in {h['chats']} sessions, {h['calls_that_set_a_model']} set a model, "
      f"{h['background_calls']} of those {h['calls']} ran in the background (the ones the person started are in the list above: forks, @-mentions; "
      f"the ones an agent file, hook or rule of theirs caused show with that artifact)")
    w(f"- automatic compactions: {auto} of {cp['total']} compactions ({cp['by_trigger']}); median {cp['median_tokens_before']} tokens before a compaction and "
      f"{cp['chats_compacted_more_than_once']} sessions compacted more than once (automatic and typed together)")
    w(f"- plan dialogs the model opened: {pd['calls']}; plan files written in plan mode {I['plans']['written_in_window']}, "
      f"read by another session {I['plans']['chats_reading_plan_files']}")
    w(f"- past work was in Claude's context early in {pw['either']} of {pw['new_chats']} new sessions: loaded automatically {pw['loaded_automatically']}, "
      f"looked up by the model {pw['looked_it_up_early']}, put in by a hook {pw['notes_put_in_by_a_hook']} (whose hook: the hooks above)")
    w(f"- tool calls the model made: Workflow runs {I['workflows']['runs']} in {I['workflows']['chats']} sessions; EnterWorktree {tools.get('EnterWorktree', {}).get('calls', 0)}, "
      f"`git worktree add` {I['chats_logged']['worktree_adds_by_shell']}; Monitor {tools.get('Monitor', {}).get('calls', 0)}, "
      f"ScheduleWakeup {tools.get('ScheduleWakeup', {}).get('calls', 0)}; AskUserQuestion {tools.get('AskUserQuestion', {}).get('calls', 0)}")
    w(f"- permission modes of typed messages (a mode comes from the person or from a settings default; the record doesn't say which): {pm}")
    said = {"claude.auto_compactions", "claude.background_helpers", "cant.helper_calls_with_model"}  # already in the lines above
    for i in who.get("claude", []) + who.get("cant", []):
        if i.get("id") not in said:
            w(counted(i))

    out = "\n".join(L)
    open(os.path.join(run, "cheap-digest.md"), "w").write(out)
    json.dump(arts, open(os.path.join(run, "cheap-artifacts.json"), "w"), indent=1, ensure_ascii=False)
    print(f"cheap-digest.md: {len(arts)} artifacts, {len(out.split())} words, {len(out)} chars")


if __name__ == "__main__":
    main(os.path.expanduser(sys.argv[1]))
