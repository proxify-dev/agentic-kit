#!/usr/bin/env python3
"""The cheap version, code part 1: every v15 problem (challenge) decided by plain code over the inventory, no AI.

Each problem gets its probe's verdict and bar (the old detectors' thresholds), with the scoping fixes the loop-2 audit
found: per repo where work reaches others (unchecked commits), headless runs included where the problem lives there
(retry caps), the probe's own hand-read where its verdict answered a different question (goal drift). A problem with
no probe and no bar is "can't tell" when the laptop has a source for it, "not read" when it has none.

    python3 problems.py RUN_DIR [--commits comprehension-debt.json]   → RUN_DIR/cheap-problems.json
Standard library only.
"""
import argparse
import fnmatch
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SUB = os.path.normpath(os.path.join(HERE, "..", "..", "..", "v15", "engine", "substrate.json"))


def chats(run):
    with open(os.path.join(run, "chats.jsonl")) as f:
        f.readline()
        return [json.loads(x) for x in f]


def _when(t):
    import datetime
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else ""


def typed_pool(C):
    """What a word search reads: typed messages (other sessions' reports left out), first 160 characters of each."""
    n = sum(1 for c in C for m in c["msgs"] if not m["head"].lstrip().startswith(("Another Claude session", "<teammate-message")))
    return f"{n:,} messages you typed in {len(C):,} sessions (the first 160 characters of each)"


def typed_hits(C, rx):
    """Chats whose typed messages match rx, one moment per chat (the first matching message), never cut:
    the whole stored line, where the match sits, when, in which project, and how to open the session (claude --resume
    <id>, run in that project's folder: what any Claude Code user has)."""
    hits = []
    for c in C:
        for m in c["msgs"]:
            h = m["head"]
            if h.lstrip().startswith(("Another Claude session", "<teammate-message")):
                continue
            mt = rx.search(h)
            if mt:
                hits.append({"chat": c["chat"], "short": c["chat"][:8], "when": _when(m.get("t")), "project": c.get("cwd", ""),
                             "msg": m.get("i"), "text": h, "match": [mt.start(), mt.end()],
                             "open": f"claude --resume {c['chat']}"})
                break
    return hits


def chat_moment(C, short, what):
    """A whole chat as a moment (for checks that read tool calls, not words)."""
    for c in C:
        if c["chat"].startswith(short):
            return {"chat": c["chat"], "short": short, "when": _when(c.get("t0")), "project": c.get("cwd", ""),
                    "msg": None, "text": what, "match": None, "open": f"claude --resume {c['chat']}"}
    return {"chat": short, "short": short, "when": "", "project": "", "msg": None, "text": what, "match": None, "open": ""}


def check(kind, looked_at, looked_for, matched, bar, depends_on, moments=None, no_moments=""):
    """How a verdict was reached, for a person judging whether to trust it (dev mode, the 11-03 ask):
    kind = words | counts | hand read | files | git | settings | tool calls | none."""
    return {"kind": kind, "looked_at": looked_at, "looked_for": looked_for, "matched": matched, "bar": bar,
            "depends_on": depends_on, "moments": moments or [],
            "no_moments": no_moments if not moments else ""}


# ---- the lethal trifecta: private data, text from outside and a way out in one session, with nothing guarding the way out
PRIVATE = re.compile(r"\.env\b|mcp__(gdrive|slack|fireflies|supabase|typeform)", re.I)
OUTSIDE = re.compile(r"WebFetch|WebSearch|mcp__web|curl https?://", re.I)
# each way out with the words a guard has to name to cover it: (label, the call, what a blocking hook's text or matcher says).
# A hook that stops commits covers a push (what it stops from leaving is what a push would carry).
WAYS_OUT = [
    ("Slack message", re.compile(r"conversations_add_message", re.I), re.compile(r"conversations_add_message|slack|mcp__", re.I)),
    ("git push", re.compile(r"git push", re.I), re.compile(r"git push|git commit|\bcommit\b|\bpush\b", re.I)),
    ("pull request", re.compile(r"gh pr (create|merge)", re.I), re.compile(r"gh pr|git push|\bpr\b", re.I)),
    ("Drive upload or share", re.compile(r"uploadFile|shareFile", re.I), re.compile(r"uploadFile|shareFile|drive|mcp__", re.I)),
    ("POST request", re.compile(r"curl .*-X ?POST", re.I), re.compile(r"curl|\bPOST\b", re.I)),
    ("deploy", re.compile(r"\b(railway up|vercel deploy|netlify deploy|fly(ctl)? deploy|firebase deploy|kubectl apply)\b", re.I),
     re.compile(r"deploy|railway|vercel|netlify|fly|firebase|kubectl", re.I)),
]
# what makes a hook's script able to say no: exit 2, or a permission decision of deny/ask, or decision "block"
BLOCKS = re.compile(r"exit\(?\s*2\b|permissionDecision[\"']?\s*[:=]\s*[\"']?(deny|ask)|[\"']decision[\"']\s*:\s*[\"']block", re.I)
TRIFECTA_COST = ("Cost: a prompt before each send or push, or reading outside text in a separate session from the one "
                 "that holds private data.")


def _applies(scope, source, plugin, cwd):
    """A settings file or hook applies to a session when it is the person's own (home), a plugin's, or a project's whose
    folder holds the session's working folder."""
    if plugin or scope != "project" or not source:
        return True
    root = os.path.dirname(os.path.dirname(os.path.expanduser(source)))  # <project>/.claude/settings.json
    return bool(cwd) and (cwd == root or cwd.startswith(root.rstrip("/") + "/"))


def _full_rules(item):
    """The ask and deny rules of one settings file. The inventory keeps the first 20 ask and 40 deny rules; a person with
    34 ask rules has the rest cut, so the file itself is read again."""
    try:
        perms = json.load(open(os.path.expanduser(item["path"]))).get("permissions") or {}
        return [str(x) for x in perms.get("ask") or []], [str(x) for x in perms.get("deny") or []]
    except (OSError, ValueError, KeyError, AttributeError):
        return [str(x) for x in item.get("ask_rules") or []], [str(x) for x in item.get("deny_rules") or []]


def rule_covers(rule, name, command):
    """Does a permission rule (`Bash(git push:*)`, `mcp__slack__conversations_add_message`, `mcp__slack`, `Bash`) cover this
    call? `command` is the one shell sub-command being judged, or None for a tool that is not Bash."""
    m = re.fullmatch(r"\s*([^()\s]+)\s*(?:\((.*)\))?\s*", rule, re.S)
    if not m:
        return False
    tool, spec = m.group(1), m.group(2)
    if not (fnmatch.fnmatchcase(name, tool) or (tool.startswith("mcp__") and name.startswith(tool + "__"))):
        return False
    if spec is None or spec == "*":
        return True
    if command is None:
        return False
    cmd = command.strip()
    if spec.endswith(":*"):  # the older prefix form
        return cmd == spec[:-2] or cmd.startswith(spec[:-2] + " ")
    return fnmatch.fnmatchcase(cmd, spec) or (spec.endswith(" *") and cmd == spec[:-2])


def _hook_text(h):
    """What a hook's script says (its matcher too): read from the file the hook runs, for the words only; never shown."""
    parts = [h.get("matcher") or ""]
    for tok in str(h.get("script") or "").split():
        p = os.path.expanduser(tok)
        if os.path.isfile(p):
            try:
                parts.append(open(p, errors="replace").read(40000))
            except OSError:
                pass
            break
    return "\n".join(parts)


def hook_covers(h, name, words):
    """A PreToolUse hook covers a way out when its matcher takes the tool, its script can say no, and it names the way out."""
    if h.get("event") != "PreToolUse":
        return False
    matcher = h.get("matcher") or ""
    try:
        if matcher not in ("", "*") and not re.fullmatch(matcher, name):
            return False
    except re.error:
        return False
    text = _hook_text(h)
    return bool(BLOCKS.search(text) and words.search(text))


def way_out_events(call):
    """The ways out one tool call holds: one per shell sub-command that sends, pushes or shares (`cd x && git push` is
    one), or the call itself for a tool that does. [(label, hook words, command or None)]."""
    name, key = call.get("name") or "", call.get("key") or ""
    if name == "Bash":
        subs = [x for x in re.split(r"\s*(?:&&|\|\||;|\||\n)\s*", key) if x]
        found = []
        for sub in subs:
            hit = next(((lab, words, sub) for lab, rx, words in WAYS_OUT if rx.search(sub)), None)
            if hit:
                found.append(hit)
        return found
    return [(lab, words, None) for lab, rx, words in WAYS_OUT if rx.search(name + " " + key)][:1]


def lethal_trifecta(C, settings, hooks):
    """Sessions that held private data, read outside text and had a way out nothing guards. A way out behind an ask rule, a
    deny rule or a hook that can block is not in reach; a session whose every way out is behind one does not count.
    Returns {"open": [chat ids], "guarded": [chat ids], "way_out": {chat: first unguarded way out}, "rules": (ask, deny, hooks)}."""
    files = [(it, *_full_rules(it)) for it in settings]
    n_ask, n_deny = sum(len(a) for _, a, _ in files), sum(len(d) for _, _, d in files)
    out = {"open": [], "guarded": [], "way_out": {}, "rules": (n_ask, n_deny, sum(h.get("event") == "PreToolUse" for h in hooks))}
    for c in C:
        calls = c["calls"]
        text = [(k.get("name") or "") + " " + (k.get("key") or "") for k in calls]
        if not (any(PRIVATE.search(t) for t in text) and any(OUTSIDE.search(t) for t in text)):
            continue
        cwd = c.get("cwd") or ""
        rules = [r for it, ask, deny in files if _applies(it.get("scope"), it.get("path"), None, cwd) for r in ask + deny]
        mine = [h for h in hooks if _applies(h.get("scope"), h.get("source"), h.get("plugin"), cwd)]
        first_open = None
        events = 0
        for k in calls:
            for label, words, cmd in way_out_events(k):
                events += 1
                if not (any(rule_covers(r, k.get("name") or "", cmd) for r in rules)
                        or any(hook_covers(h, k.get("name") or "", words) for h in mine)):
                    first_open = first_open or (k["name"] + (f" {k['key']}" if k.get("key") else ""))[:80]
        if first_open:
            out["open"].append(c["chat"][:8])
            out["way_out"][c["chat"][:8]] = first_open
        elif events:
            out["guarded"].append(c["chat"][:8])
    return out


def trifecta_words(tf):
    """The verdict and the sentence for lethal_trifecta()'s result: shows from 3 sessions, the sessions held back by a rule or
    a hook named, and, when the advice is right, what following it costs (one line)."""
    tri = tf["open"]
    held_back = (f" {len(tf['guarded'])} more sessions had all three, but every way out in them sits behind an ask rule, a deny "
                 f"rule or a hook that can block, so they are not counted." if tf["guarded"] else "")
    shows = "shows" if len(tri) >= 3 else "can't tell"
    return shows, (f"In {len(tri)} sessions the agent held private data, read outside text and had a way out nothing asks about "
                   f"or blocks (a send, a push, an upload)" + (f", e.g. {', '.join(tri[:3])}" if tri else "") + "." + held_back
                   + (" " + TRIFECTA_COST if shows == "shows" else ""))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--commits", help="comprehension-debt check output with by_repo (a rerun of that check)")
    a = ap.parse_args(argv)
    run = os.path.expanduser(a.run)
    S = json.load(open(SUB))
    I = json.load(open(os.path.join(run, "inventory.json")))
    P = I["probes"]
    C = chats(run)
    if a.commits:
        nums = json.load(open(a.commits))["symptom"]["numbers"]
        old = P.get("comprehension-debt", {})
        P["comprehension-debt"] = {**old, "by_repo": nums["by_repo"],
                                   "symptom": {**(old.get("symptom") or {}), "share_with_no_check_pct": nums.get("share_with_no_check_pct")}}
    out = {}

    PROBE_GAP = "This check keeps totals only, not the sessions behind them."
    STEP = ("how often you correct, stop or refuse the agent; someone who rarely steps in reads the same as someone whose "
            "sessions never go wrong")

    def put(cid, shows, why, rule, evidence, chk=None):
        state = "not read" if shows == "not read" else "seen"
        out[cid] = {"kind": "challenge", "id": cid, "state": state, "shows": "" if shows == "not read" else shows,
                    "why": why, "rule": rule, "evidence": evidence, "derivation": "code", "verdict": "kept",
                    "rung": "", "could_be": "", "held": "", "try": "", "ask": "", "checked": [], "basis": "",
                    "discipline": S["challenges"][cid]["discipline"],
                    "check": chk or check("none", "", "", "", rule, "", no_moments="No check ran.")}

    def probe(name, part, key, default=None):
        return ((P.get(name) or {}).get(part) or {}).get(key, default)

    # ---- context
    cr = P.get("context-rot") or {}
    rot_chk = check("counts", "every interactive session, by how full it was when you typed", "step-ins (a correction, a stop, a rejected tool call)",
                    f"{probe('context-rot', 'symptom', 'step_in_past_300k')} past 300k vs {probe('context-rot', 'symptom', 'step_in_50k_200k')} at 50k-200k",
                    "more step-ins in full sessions than in mid-size ones", STEP, no_moments=PROBE_GAP)
    put("context-rot", cr.get("verdict"), cr.get("headline") or "", "check context-rot: step-ins past 300k vs 50k-200k",
        [{"source": "probes.context-rot", "detail": str(cr.get("symptom"))}], rot_chk)
    for cid in ("dumb-zone", "real-context-size"):
        put(cid, cr.get("verdict"), cr.get("headline") or "", "same reading as context rot (step-ins by fullness)",
            [{"source": "probes.context-rot", "detail": "step-ins by how full the session is"}], rot_chk)
    h = probe("context-rot", "held", "before"), probe("context-rot", "held", "after")
    if h[0] is not None and h[1] is not None:
        put("lobotomization", "shows" if h[1] > h[0] else "doesn't show",
            f"Stepping in after a compaction: {h[0]:.1%} before, {h[1]:.1%} after.", "after-compaction step-ins vs before",
            [{"source": "probes.context-rot.held", "detail": f"before {h[0]}, after {h[1]}"}],
            check("counts", "messages just before and just after each compaction", "step-ins", f"{h[0]:.1%} before, {h[1]:.1%} after",
                  "more step-ins after than before", STEP, no_moments=PROBE_GAP))
    cp = P.get("context_poisoning") or {}
    put("context-poisoning", cp.get("verdict"), cp.get("headline") or "",
        "check context_poisoning; 'shows' needs the same wrong turn in most streaks read", [{"source": "probes.context_poisoning", "detail": (cp.get("headline") or "")}],
        check("hand read", f"{probe('context_poisoning', 'symptom', 'chats_with_2_plus_messages')} sessions; streaks of two corrections within 5 messages",
              "whether each streak corrected the same wrong thing",
              f"{(probe('context_poisoning', 'symptom', 'hand_read') or {}).get('same_wrong_thing_each_time')} of {(probe('context_poisoning', 'symptom', 'hand_read') or {}).get('read')} streaks read",
              "the same wrong turn in most streaks read",
              "a one-off read of a few streaks on this laptop; it does not rerun on another machine", no_moments=PROBE_GAP))
    held = cp.get("held") or {}
    after = (held.get("after_a_response") or {}).get("pct")
    usual = (held.get("after_any_step_in_for_comparison") or {}).get("pct") if isinstance(held.get("after_any_step_in_for_comparison"), dict) else None
    if after is not None and usual is not None:
        put("trajectory-poisoning", "shows" if after > usual else "doesn't show",
            f"After a streak of corrections, stepping in ran at {after}% against {usual}% after any single step-in.",
            "after-streak step-in rate > usual rate", [{"source": "probes.context_poisoning.held", "detail": f"{after} vs {usual}"}],
            check("counts", "messages after a streak of corrections vs after any single step-in", "step-ins", f"{after}% vs {usual}%",
                  "higher after a streak", STEP, no_moments=PROBE_GAP))
    pw = I["past_work_reach"]
    put("starts-from-zero", "shows" if pw["either_pct"] < 50 else "doesn't show",
        f"Past work was in Claude's context early in {pw['either']} of {pw['new_chats']} new sessions ({pw['either_pct']}%): looked up "
        f"loaded automatically (auto-memory) in {pw['loaded_automatically']}, looked up in {pw['looked_it_up_early']}, put in by a hook in {pw['notes_put_in_by_a_hook']}.", "past_work_reach.either_pct < 50",
        [{"source": "past_work_reach", "detail": f"{pw['either']}/{pw['new_chats']}"}],
        check("counts", f"{pw['new_chats']} new sessions, their first steps", "Claude Code loading the project's auto-memory, the agent looking up past work, or a hook putting notes in",
              f"{pw['either']} of {pw['new_chats']} ({pw['either_pct']}%)", "under 50% means it shows",
              "recognising a lookup or a note hook by the tools this laptop uses (session search, memory reads, hooks that add text); a different notes tool may be missed",
              no_moments="The inventory keeps the counts, not which sessions."))
    pb = P.get("prompt-bloat") or {}
    put("prompt-bloat", pb.get("verdict"), pb.get("headline") or "", "check prompt-bloat: an always-loaded file they wrote over 200 lines",
        [{"source": "probes.prompt-bloat", "detail": (pb.get("headline") or "")}],
        check("files", "instruction files that load in every session, the ones you wrote", "length in lines and words",
              (pb.get("headline") or "")[:160], "any one over 200 lines", "which files git says you wrote; files you did not commit count as someone else's",
              no_moments="The files are named in the check's own report, not kept here."))

    # ---- prompt
    hr = probe("goal_drift", "symptom", "hand_read") or {}
    early = ((probe("goal_drift", "symptom", "by_position") or {}).get("messages_2_10") or {})
    if hr.get("read") and early.get("step_ins"):
        agent_side = (hr.get("went_off_what_he_asked_or_agreed", 0) + hr.get("misread_the_ask", 0)
                      + hr.get("result_fell_short_of_what_it_showed", 0))
        est = round(early["step_ins"] * agent_side / hr["read"])
        share = est / early["n"] if early.get("n") else 0
        put("goal-drift", "shows" if est >= 30 and share >= 0.02 else "doesn't show",
            f"About {est} early step-ins were the agent going off or misreading the ask ({agent_side} of {hr['read']} read by "
            f"hand, of {early['step_ins']} early step-ins in {early['n']} messages).",
            "agent-side early step-ins (hand-read share x early step-ins) >= 30 and >= 2% of messages 2-10",
            [{"source": "probes.goal_drift.symptom.hand_read", "detail": json.dumps(hr)}],
            check("hand read", f"{early['step_ins']} step-ins in messages 2-10 of {early['n']} messages; {hr['read']} of them read",
                  "whether the agent went off or misread the ask", f"{agent_side} of {hr['read']} read, scaled up to about {est}",
                  ">= 30 and >= 2% of messages", "a one-off read of a sample on this laptop, scaled up; it does not rerun on another machine",
                  no_moments=PROBE_GAP))
    ov = typed_hits(C, re.compile(r"over-?fit", re.I))
    put("prompt-overfitting", "shows" if len(ov) >= 2 else ("can't tell" if not ov else "can't tell"),
        f"You typed the word overfitting in {len(ov)} sessions" + (f" (e.g. {ov[0]['short']}: '{ov[0]['text'][:70]}')" if ov else "") + ".",
        "typed messages naming overfitting in >= 2 sessions", [{"source": "chats.jsonl", "detail": "; ".join(f"{m['short']}: {m['text']}" for m in ov)}],
        check("words", typed_pool(C), "the word over-fit / overfit (any case)", f"{len(ov)} sessions", "2 or more sessions",
              "you typing that English word. Someone who says 'only works on this example' is missed, and catching the agent "
              "overfitting counts the same as suffering from it; the check does not know whether a prompt was involved", ov))
    shouty, words = 0, 0
    for f in I["instruction_files"]["always_loaded"]:
        if f.get("theirs") is not None and not f.get("theirs"):
            continue
        try:
            text = open(f["path"], errors="replace").read()
        except OSError:
            continue
        words += len(text.split())
        shouty += sum(1 for ln in text.splitlines() if re.search(r"\b[A-Z]{4,}(?:\s+[A-Z]{2,}){1,}\b", ln) or "!!!" in ln)
    put("aggressive-prompt-language", "shows" if shouty >= 10 else "doesn't show",
        f"{shouty} shouted lines in {words:,} words of the instruction files they wrote that load every time.",
        "shouted lines (runs of capitals, !!!) in their always-loaded files >= 10",
        [{"source": "instruction_files", "detail": f"{shouty} lines / {words} words"}],
        check("files", "the instruction files you wrote that load in every session", "lines with runs of capital words, or !!!",
              f"{shouty} lines in {words:,} words", "10 or more lines",
              "shouting written in capitals; a prompt that pushes hard in normal case is missed", no_moments="The lines are counted, not kept."))

    # ---- verification
    br = (P.get("comprehension-debt") or {}).get("by_repo") or {}
    shared = {os.path.basename(r["repo"]) for r in I["repos"] if r.get("other_authors")}
    hot = {k: v for k, v in br.items() if k in shared and v["commits"] >= 30 and (v["pct"] or 0) >= 20}
    tot = probe("comprehension-debt", "symptom", "share_with_no_check_pct")
    put("comprehension-debt", "shows" if hot else "doesn't show",
        (f"In repos others also commit to, " + "; ".join(f"{k}: {v['without_a_check']} of {v['commits']} code commits ({v['pct']}%) "
                                                        f"had nothing run, shown or reviewed after the agent's last edit" for k, v in hot.items())
         + (f". Across all repos {tot}%." if tot is not None else ".")) if hot
        else (f"{tot}% of code commits went in with no check, under the 20% bar, in every shared repo." if tot is not None
              else "Under the 20% bar of code commits with no check, in every shared repo."),
        "unchecked code commits >= 20% of >= 30 in a repo with other authors (per repo, not pooled)",
        [{"source": "comprehension-debt.by_repo", "detail": json.dumps(br)}],
        check("git", "code commits per repo in the window, in repos others also commit to", "a test, build, run or diff after the agent's last edit",
              "; ".join(f"{k} {v['without_a_check']}/{v['commits']}" for k, v in hot.items()) or "none over the bar",
              "20% or more unchecked, in a repo with 30+ commits and other authors",
              "what counts as a check in the session log before the commit; a check run outside Claude Code (CI, a terminal) is missed",
              no_moments="The check keeps per-repo totals; comprehension-debt.json keeps a few example commits."))
    vib = typed_hits(C, re.compile(r"\b(idc|i don'?t care|whatever|without (reading|looking|checking)|didn'?t (read|check))\b.{0,40}"
                                   r"\b(merge|ship|push)|\b(merge|ship|push) it\b.{0,30}\b(idc|whatever|i don'?t care|anyway)", re.I))
    put("vibe-coding", "shows" if len(vib) >= 2 else "can't tell",
        (f"You said to ship without reading in {len(vib)} sessions (e.g. {vib[0]['short']}: '{vib[0]['text'][:70]}')." if vib
         else "The laptop can't show whether output goes in unread, and they never said so."),
        "typed 'merge it / idc' style messages in >= 2 sessions; otherwise can't tell",
        [{"source": "chats.jsonl", "detail": "; ".join(f"{m['short']}: {m['text']}" for m in vib)}],
        check("words", typed_pool(C), "'idc', 'whatever', 'without reading/checking', 'didn't read' near merge / ship / push",
              f"{len(vib)} sessions", "2 or more sessions", "you saying it in English, in these words; shipping unread without saying so is invisible", vib))
    dl = P.get("doom-loop") or {}
    cap = probe("no_backpressure", "held", "reached_the_3_try_cap_then_let_end") or 0
    typed_loops = probe("doom-loop", "symptom", "doom_loops") or 0
    put("doom-loop", "shows" if (typed_loops >= 3 or cap >= 3) else dl.get("verdict"),
        f"In interactive sessions: {typed_loops} same-error loops. In headless runs: {cap} runs hit the retry cap of a check and ended still failing.",
        "same-error loops >= 3 in interactive sessions, OR >= 3 headless runs ending at a retry cap still failing",
        [{"source": "probes.doom-loop / probes.no_backpressure.held", "detail": f"typed {typed_loops}, cap {cap}"}],
        check("counts", "interactive sessions, and headless runs that went through your checks", "the same error 3 times in a row; runs that hit a check's retry cap still failing",
              f"{typed_loops} loops in interactive sessions; {cap} capped runs", "3 or more of either",
              "error text repeating in tool results; and, for the second half, checks you built with a retry cap: without them that half can't show",
              no_moments=PROBE_GAP))
    nb = P.get("no_backpressure") or {}
    put("declaring-done-prematurely", nb.get("verdict"), nb.get("headline") or "",
        "check no_backpressure: the agent's first 'done' blocked by a check", [{"source": "probes.no_backpressure", "detail": (nb.get("headline") or "")}],
        check("counts", "runs that went through a check you built that can refuse 'done'", "how often the first 'done' was blocked",
              (nb.get("headline") or "")[:160], "the check's own verdict",
              "the person having built such checks; with none, the agent's early 'done' leaves no trace and this reads as not shown",
              no_moments=PROBE_GAP))

    # ---- harness
    bs = P.get("babysitting") or {}
    put("approval-fatigue", bs.get("verdict"), bs.get("headline") or "", "check babysitting: waiting >= 10 min per agent hour",
        [{"source": "probes.babysitting", "detail": (bs.get("headline") or "")}], check("counts", "agent hours in the window", "time an agent sat waiting on you", (bs.get("headline") or "")[:160],
              "10 or more minutes of waiting per agent hour", "permission and plan prompts in the log; waiting you do in another window is not seen",
              no_moments=PROBE_GAP))
    pm = I["chats_logged"]["permission_modes_of_typed_messages"]
    off = (pm.get("bypassPermissions", 0) + pm.get("auto", 0)) / max(1, sum(pm.values()))
    sends = [r for r in I["tools"]["typed_chats"]["by_mcp_server"] if re.search(r"slack|gdrive|github|gmail|typeform|supabase", r["what"], re.I)]
    rules = " ".join(str(s.get("deny_rules")) + str(s.get("ask_rules")) for s in I["settings"]).lower()
    unguarded = [r for r in sends if r["what"].split("-")[0].lower() not in rules]
    put("yolo-mode", "shows" if off >= 0.8 and unguarded else "doesn't show",
        f"{off:.0%} of typed messages ran with prompts off, and tools that write to outside systems ran with no deny or ask rule "
        f"naming them: " + ", ".join(f"{r['what']} ({r['calls']} calls)" for r in unguarded[:4]) + ".",
        "prompts off >= 80% AND outside-writing MCP tools used with no deny/ask rule naming them",
        [{"source": "chats_logged + tools.typed_chats.by_mcp_server + settings", "detail": f"off {off:.2f}; {len(unguarded)} unguarded"}],
        check("settings", "the permission mode of every typed message, the tools used, your deny and ask rules",
              "prompts off, and outside-writing tools with no rule naming them", f"{off:.0%} off; {len(unguarded)} tools unguarded",
              "80% or more off AND at least one unguarded outside tool",
              "a fixed list of outside tools (Slack, Google Drive, GitHub, Gmail, Typeform, Supabase); others are missed, and a hook that guards a tool is not counted as a rule",
              no_moments="Counts per tool, not the sessions."))
    # lethal trifecta: the same chat holds private data, outside text, and a way out that nothing asks about or blocks
    tf = lethal_trifecta(C, I["settings"], I.get("hooks_configured") or [])
    tri = tf["open"]
    def legs(short):
        """Which call made each leg, so the moment says what actually happened in that chat."""
        c = next(x for x in C if x["chat"].startswith(short))
        def first(rx):
            k = next((k for k in c["calls"] if rx.search(k["name"] + " " + (k.get("key") or ""))), None)
            return (k["name"] + (f" {k['key']}" if k.get("key") else ""))[:80] if k else "?"
        return f"private data: {first(PRIVATE)} · outside text: {first(OUTSIDE)} · a way out nothing guards: {tf['way_out'][short]}"
    n_ask, n_deny, n_pre = tf["rules"]
    shows, why = trifecta_words(tf)
    put("lethal-trifecta", shows, why,
        "sessions with all three legs (private data, outside text, a way out outside every ask rule, deny rule and blocking hook) >= 3",
        [{"source": "chats.jsonl calls + settings", "detail": ", ".join(tri)}],
        check("tool calls", f"the tool calls of {len(C):,} sessions, your {n_ask} ask rules, {n_deny} deny rules and {n_pre} before-tool hooks",
              "a private-data call, an outside-text call and a send/push/upload in the same session with no ask rule, deny rule or blocking hook over it",
              f"{len(tri)} sessions ({len(tf['guarded'])} more had a way out behind a rule or hook)", "3 or more sessions",
              "a fixed list of tool names (.env reads, Drive/Slack/Fireflies/Supabase/Typeform; web fetch; Slack send, git push, pull request, uploads, deploys); other tools are missed, "
              "and having all three is not proof one fed the other. A hook counts when its matcher takes the tool, its script can say no (exit 2, deny, block) and it names the way out "
              "(a commit blocker covers a push); an ask rule counts even in sessions run with prompts off",
              [chat_moment(C, t, legs(t)) for t in tri]))

    # ---- orchestration
    put("babysitting", bs.get("verdict"), bs.get("headline") or "", "check babysitting: waiting >= 10 min per agent hour, or most long runs watched",
        [{"source": "probes.babysitting", "detail": (bs.get("headline") or "")}], check("counts", "agent hours in the window", "time an agent sat waiting on you", (bs.get("headline") or "")[:160],
              "10 or more minutes of waiting per agent hour", "permission and plan prompts in the log; waiting you do in another window is not seen",
              no_moments=PROBE_GAP))
    oa = P.get("one-at-a-time") or {}
    put("one-at-a-time", oa.get("verdict"), oa.get("headline") or "", "check one-at-a-time: >= 70% single-agent time",
        [{"source": "probes.one-at-a-time", "detail": (oa.get("headline") or "")}],
        check("counts", "the minutes your agents were working", "how many worked at once", (oa.get("headline") or "")[:160],
              "70% or more of the time one agent alone", "agents run through Claude Code on this laptop; other tools and machines are not seen",
              no_moments=PROBE_GAP))

    # ---- everything else: no probe, no bar
    for cid, c in S["challenges"].items():
        if cid in out:
            continue
        if c.get("laptop_signs"):
            put(cid, "can't tell", "The laptop holds related sources, but no bar says when this problem shows.",
                "no check and no bar -> can't tell", [{"source": "substrate laptop_signs", "detail": "; ".join(c["laptop_signs"])}],
                check("none", "", "", "", "no bar written yet", "", no_moments="No check is written for this problem yet."))
        else:
            put(cid, "not read", "Nothing on this laptop shows this problem either way.", "no laptop source -> not read", [])
    rows = list(out.values())
    json.dump({"rows": rows}, open(os.path.join(run, "cheap-problems.json"), "w"), indent=1, ensure_ascii=False)
    from collections import Counter
    print(len(rows), "problems:", dict(Counter(r["shows"] or "not read" for r in rows)))
    for r in rows:
        if r["shows"] == "shows":
            print("  shows:", r["id"], "|", r["why"][:150])


if __name__ == "__main__":
    main()
