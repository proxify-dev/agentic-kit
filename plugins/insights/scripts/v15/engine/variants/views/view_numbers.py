"""The numbers: signal tiles in the 10-27 style (question, big number, one line, How we know), then what we could and
could not see. Reads inventory.json and anatomy.json only. Stdlib only."""

import commit_kit  # noqa: E402

ORDER = 15
TITLE = "The numbers"
QUESTION = "The main counts from your logs, and what the scan could and couldn't see."
PROXIFY_ONLY = True  # member copy: off (11-06, a tester's "the first look on the report feels overwhelming"; By discipline carries the verdict and the step)

CSS = """
.v-numbers .tiles{border-top:1px solid #EAECF0}
.v-numbers .tile{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.7fr);column-gap:32px;padding:16px 0;border-bottom:1px solid #EAECF0}
.v-numbers .tile>*{grid-column:2}
.v-numbers .tile .q{grid-column:1;grid-row:1 / span 4;font-size:14px;font-weight:500;color:#344054;margin:0}
.v-numbers .tile .big{font-size:18px;font-weight:600;color:#15172F;line-height:1.3;margin:0}
.v-numbers .tile.warn .big{color:#B54708}
.v-numbers .tile .line{margin:2px 0 0;color:#475467;max-width:68ch}
.v-numbers .tile .cant{margin:4px 0 0;font-size:12px;color:#667085;max-width:68ch}
.v-numbers .how{margin:8px 0 0;font-size:12px;color:#475467}
.v-numbers .how>summary{color:#3C3ED4;font-weight:500;width:max-content}
.v-numbers .how dl{margin:8px 0 0;display:grid;grid-template-columns:72px 1fr;gap:4px 12px;padding:8px 12px;background:#F9FAFB;border:1px solid #EAECF0;border-radius:8px}
.v-numbers .how dt{font-weight:600;color:#15172F}
.v-numbers .how dd{margin:0;overflow-wrap:anywhere}
.v-numbers .how ul{margin:0;padding-left:16px}
.v-numbers h4{font-size:18px;font-weight:600;margin:56px 0 4px}
@media (max-width:900px){.v-numbers .tile{grid-template-columns:1fr}.v-numbers .tile>*,.v-numbers .tile .q{grid-column:1;grid-row:auto}}
.v-numbers .sub{color:#475467;margin:0 0 12px}
.v-numbers .edges{margin:32px 0 0}
.v-numbers .edges>summary{font-size:16px;font-weight:600;color:#15172F;padding:4px 0}
.v-numbers .see{border-top:1px solid #EAECF0}
.v-numbers .see{overflow-x:auto}.v-numbers .see table{border-collapse:collapse;width:100%;min-width:0}.v-numbers .see td{overflow-wrap:anywhere}
.v-numbers .see th{text-align:left;font-size:12px;font-weight:600;color:#475467;padding:4px 12px 4px 0;border-bottom:1px solid #EAECF0}
.v-numbers .see td{padding:8px 12px 8px 0;border-bottom:1px solid #EAECF0;vertical-align:top}
.v-numbers .see td.v{font-weight:600;white-space:nowrap}
.v-numbers .see td.c{color:#475467;font-size:12px}
.v-numbers .cantlist{margin:12px 0 0;padding:0;list-style:none;color:#475467}
.v-numbers .cantlist li{margin:0 0 4px}
.v-numbers .cantlist b{color:#B54708;font-weight:600}
.v-numbers .empty{color:#667085}
"""

CODE = "Code counted it; no AI read decided it."
UNCHECKED = "Not checked by a person yet."


def _n(x):
    return f"{int(x):,}"


def _how(esc, rows):
    out = ""
    for k, v in rows:
        if isinstance(v, (list, tuple)):
            v = "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in v) + "</ul>"
        else:
            v = esc(v)
        out += f"<dt>{esc(k)}</dt><dd>{v}</dd>"
    return f'<details class="how"><summary>How we know</summary><dl>{out}</dl></details>'


def _tile(esc, q, big, line, cant, where, rule, warn=False):
    return (f'<div class="tile{" warn" if warn else ""}"><p class="q">{esc(q)}</p><p class="big">{esc(big)}</p>'
            f'<p class="line">{esc(line)}</p>'
            + _how(esc, [("Can\'t tell", cant), ("Where", where), ("Rule", rule), ("Decided by", CODE), ("Checked", UNCHECKED)]) + "</div>")


def _no_log(hl):
    """Runs of their scheduled jobs that keep no session log (claude -p --no-session-persistence): launchd's own count,
    since the job was loaded, never added into headless.runs (a different unit and window)."""
    runs, jobs = hl.get("scheduled_runs_without_a_log") or 0, hl.get("scheduled_jobs_without_a_log") or 0
    if not runs:
        return ""
    return (f" Plus {_n(runs)} run{'s' if runs != 1 else ''} of your {_n(jobs)} scheduled job{'s that keep' if jobs != 1 else ' that keeps'} no "
            "session log, counted since the job was loaded (not only the last 30 days).")


def render(ctx):
    esc = ctx["esc"]
    inv, ana = ctx.get("inventory"), ctx.get("anatomy")
    if not inv:
        return '<p class="empty">Not in this run: inventory.json is missing.</p>'
    days = inv["meta"].get("days", 30)
    t = []
    typed, prog = inv["chats"]["typed_chats"], inv["headless"]["runs"]
    msgs = inv["chats"].get("typed_messages")
    t.append(_tile(esc, "Sessions you typed vs. headless runs", f"{_n(typed)} vs {_n(prog)}",
                   f"{_n(typed)} sessions you typed in, {_n(prog)} headless runs"
                   + (f" ({_n(msgs)} typed messages)" if msgs else "") + ".",
                   "Work on other machines or in other tools is not in these logs.",
                   "inventory.json: chats.typed_chats, chats.typed_messages, headless.runs.",
                   f"Typed = a Claude Code log with a message you typed, in the last {days} days. Headless run = a log "
                   "whose entry point is the SDK or `claude -p` (headless.entrypoints: "
                   + ", ".join(f"{k} {_n(v)}" for k, v in inv["headless"].get("entrypoints", {}).items()) + ")."))
    hl = inv["headless"]
    quiet = hl.get("while_away", hl.get("started_with_no_typed_message_in_30_min_before"))
    if quiet is not None:
        sched = hl.get("started_by_a_scheduled_job")
        t.append(_tile(esc, "Headless runs while you were away", f"{_n(quiet)} of {_n(prog)}",
                       f"of your {_n(prog)} headless runs started with nothing typed by you in the 30 minutes before"
                       + (f", or were started by a scheduled job of yours ({_n(sched)} traced to one)." if sched is not None else ".")
                       + _no_log(hl),
                       "The other runs may follow a command you typed, or a hook or plugin that runs when you do. A headless run "
                       "and a session you typed are different units (one batch job can start thousands of runs), so they are never "
                       "added up into a share.",
                       "inventory.json: headless.runs, headless.while_away, headless.started_by_a_scheduled_job.",
                       "A run counts as headless by its entry point (Agent SDK or claude -p), not by who wrote the script. "
                       "While away = no message from you in any session in the 30 minutes before the run began (only that window, "
                       "never after), or a LaunchAgent or cron line is its parent: the run's prompt is written in the job, or the "
                       "job's calendar entry fires in the minute the run began."))
    cs = commit_kit.commit_share(inv)
    mine, allc, top_other = cs["mine"], cs["all"], cs["top_other"]
    t.append(_tile(esc, "Commits under your name", f"{_n(mine)} of {_n(allc)}",
                   f"commits in the last {days} days that git shows under your name, across the {cs['repos']} repos you write in."
                   + (f" The biggest other author in them is {top_other[0][0]} with {_n(top_other[0][1])}." if top_other else "")
                   + (f" Not counted: {cs['open_repos']} other repos you have open ({_n(cs['open_mine'])} of their "
                      f"{_n(cs['open_commits'])} commits are yours)." if cs["open_repos"] else ""),
                   "Commits made by a tool under another name (a bot, a service account) count as someone else's.",
                   "inventory.json: repos[].theirs_in_window, commits_in_window and authors_here; person.identities.",
                   f"{cs['rule']} A repo reached by two paths (a symlink, say) is one repo. Yours = the commit "
                   f"author matches {', '.join(inv['person']['identities'])}. The pile of temporary folders is left out.",
                   warn=mine * 3 < allc))
    h = inv["helpers"]
    top_h = (h.get("by_type") or [{}])[0]
    t.append(_tile(esc, "Sub-agent calls", _n(h["calls"]),
                   f"sub-agent calls in {_n(h['chats'])} sessions; {_n(h['calls_that_set_a_model'])} of the {_n(h['calls'])} calls chose a model, "
                   f"{_n(h['background_calls'])} of the {_n(h['calls'])} calls ran in the background"
                   + (f"; most often \"{top_h['type']}\" ({_n(top_h['calls'])})." if top_h.get("type") else "."),
                   "Whether the sub-agent's answer was used is not in this count.",
                   "inventory.json: helpers.calls, chats, calls_that_set_a_model, background_calls, by_type[0].",
                   f"One call = one sub-agent started from a session; {_n(h['helper_files_in_window'])} sub-agent log files in the window."))
    c = inv.get("compactions")
    if c:
        by = c.get("by_trigger", {})
        t.append(_tile(esc, "Compactions: by hand vs. automatic", f"{_n(by.get('manual', 0))} by hand, {_n(by.get('auto', 0))} automatic",
                       f"{_n(c['total'])} compactions in {_n(c['chats_compacted'])} sessions; the median session held "
                       f"{_n(c['median_tokens_before'])} tokens before it, {_n(c.get('with_focus_text', 0))} came with a note on what to keep.",
                       "How full the session was is the logged token count, not what the agent still remembered.",
                       "inventory.json: compactions.total, by_trigger.manual and .auto, chats_compacted, median_tokens_before, with_focus_text.",
                       "A compaction is a summary-boundary entry in a session log; its trigger field says manual or auto."))
    s = (ana or {}).get("summary")
    if s:
        i = {k: 0 for k in ("home", "project", "folder")} | s["instruction_files"]  # Counter output: empty scopes are absent
        targets = (ana.get("meta") or {}).get("targets") or []
        t.append(_tile(esc, "Instruction files (CLAUDE.md, AGENTS.md)", _n(sum(i.values())),
                       f"{_n(i['home'])} for you everywhere, {_n(i['project'])} at a project's top, {_n(i['folder'])} inside folders.",
                       "How many of these reach a given session depends on where it runs.",
                       "anatomy.json: summary.instruction_files.home, .project, .folder (the list is instruction_files[]).",
                       f"CLAUDE.md and AGENTS.md style files found in the {_n(len(targets))} folders walked (anatomy.json meta.targets), "
                       "sorted by scope."))
        t.append(_tile(esc, "Memory stores", _n(s["memory_stores"]),
                       "memory stores: places notes are kept for the next session to read.",
                       "A store existing does not mean it is read; that is a separate count.",
                       "anatomy.json: summary.memory_stores (the list is memory_stores[], with file counts).",
                       "Each folder of notes, a memory index file or an agent's memory folder found on disk is one store."))
        hk = s["hooks"]
        total = sum(hk.values())
        top = sorted(hk.items(), key=lambda kv: -kv[1])[:3]
        miss = s.get("hooks_missing_script", 0)
        t.append(_tile(esc, "Hooks set up", _n(total),
                       "hooks set up, across " + f"{len(hk)} moments; most at " + ", ".join(f"{k} ({v})" for k, v in top) + ".",
                       f"{_n(miss)} point at a script that is missing on disk." if miss else "",
                       "anatomy.json: summary.hooks (by event), summary.hooks_missing_script (the list is hooks[]).",
                       "Every hook entry in a settings file or an enabled plugin counts once; status line and file "
                       "suggestion entries count as hooks here.", warn=bool(miss)))
    tiles = f'<div class="tiles">{"".join(t)}</div>'

    # ---- what we could and couldn't see ------------------------------------------------------------------------
    m = inv["meta"]
    cant = [("Window", f"{m.get('days', 30)} days to {m.get('to', '?')}",
             "Anything older than the logs Claude Code keeps, or done elsewhere, is not in here."),
            ("Session log files", f"{_n(m['main_log_files'])} sessions, {_n(m['helper_log_files'])} sub-agents",
             f"{_n(inv['chats_logged']['typed_chat_files'])} of the session files hold a typed session; "
             f"{_n(typed)} are counted as sessions in the numbers above." if inv.get("chats_logged") else "")]
    vs = m.get("versions") or []
    cant.append(("Claude Code versions", ", ".join(vs) if vs else "none", "Older versions may log fewer details; no version was dropped."))
    cr_inv, cr_ana = m.get("could_not_read") or [], (ana or {}).get("could_not_read") or []
    cr = {x["path"]: x["reason"] for x in cr_inv + cr_ana}
    cant.append(("Could not read", f"{len(cr)} folder{'s' if len(cr) != 1 else ''}",
                 "; ".join(f"{p}: {r}" for p, r in cr.items()) if cr else "Everything asked for was read."))
    if ana:
        cant.append(("Setup scan", f"{len(ana['meta'].get('targets', []))} folders walked",
                     f"in {ana['meta'].get('total_seconds', '?')} s; {_n(ana['summary'].get('mcp_servers', 0))} tool servers and "
                     f"{_n(ana['summary'].get('plugins_enabled', 0))} enabled plugins listed."))
    rows = "".join(f'<tr><td>{esc(a)}</td><td class="v">{esc(b)}</td><td class="c">{esc(c)}</td></tr>' for a, b, c in cant)
    see = ('<details class="edges"><summary>What we could see, and what we couldn\'t</summary><p class="sub">The edges of every number above.</p>'
           f'<div class="see"><table><tr><th>What</th><th>Value</th><th>What it means</th></tr>{rows}</table>'
           '<ul class="cantlist"><li><b>Can\'t tell:</b> work done in other tools or on other machines, web sessions, and what '
           'was said out loud.</li><li><b>Can\'t tell:</b> whether a result was good; these counts say what happened, not '
           'whether it was right.</li></ul>'
           + _how(esc, [("Where", ["inventory.json: meta.days, to, main_log_files, helper_log_files, versions, could_not_read; "
                                   "chats_logged.typed_chat_files.",
                                   "anatomy.json: meta.targets, meta.total_seconds, could_not_read."]),
                        ("Rule", "Read straight from the scan's own record of what it opened. A folder is listed as could "
                                 "not read when the scan stopped at its time budget or was rejected. The two scans' lists are merged by path."),
                        ("Decided by", CODE), ("Checked", UNCHECKED)])
           + "</div></details>")
    return tiles + see
