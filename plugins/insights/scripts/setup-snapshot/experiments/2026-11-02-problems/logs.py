#!/usr/bin/env python3
"""Read a person's Claude Code logs once into one compact file per run: every chat they typed in, with what they
typed, what the agent called, what came back, and how full the window was. Problem detectors read this file
(`chats.jsonl`) instead of the raw logs, so each can be written and checked on its own.

One line per chat (interactive only: entrypoints starting with "sdk" are programs and are skipped):
  chat, project, cwd, ep, version, t0, t1
  msgs[]      typed messages: i, t, ctx, int, rej, corr, chars, head(160), pm (permission mode)
  calls[]     the agent's tool calls in the main chat: t, ctx, name, key, err, out(160 when err)
              key = Bash command (300) | file path | helper type + description | skill | other input (120)
  compactions[]  t, at (index of the next typed message), trigger, pre, post
  commands[]  typed slash commands: t, at, name, args(80)
  attachments {type: count}   e.g. nested_memory, edited_text_file, plan_mode, skill_listing
  helpers     {"calls": {name: n}, "bash": [first 300 chars, up to 400], "files_read": n, "n": helper files}

Standard library only. Usage: python3 logs.py [--days 30] [--root ~/.claude/projects] --out chats.jsonl
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from multiprocessing import Pool

# Apps a person types into (VS Code, desktop, JetBrains) drive Claude Code through the Agent SDK, so Claude Code
# writes the person's own message as promptSource "sdk", like a script's. In their sessions "sdk" is the person.
PERSON_APPS = ("claude-vscode", "claude-desktop", "claude-jetbrains")

UNATTENDED_PREFIX = "sdk"
TYPED = ("typed", "queued")
INJECTED = ("<command-name>", "<command-message>", "<local-command", "<task-notification>", "<teammate-message",
            "<system-reminder>", "<cross-session-message", "Caveat:", "[Request interrupted")
NOT_THE_PERSON = ("Another Claude session sent a message", "[SYSTEM NOTIFICATION")
INTERRUPT = "[Request interrupted by user"
REJECT = ("The user doesn't want to proceed", "The user doesn’t want to proceed")
CORRECTION_RE = re.compile(
    r"^[\s\W]*(no|nope|nah|don'?t|do not|dont|wrong|not what|stop|wait|why did you|you didn'?t|"
    r"that'?s not|i said|i told you|undo|revert)\b", re.I)


def epoch(ts):
    return datetime.fromisoformat(ts[:23] + "+00:00").timestamp()


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def key_of(name, inp):
    if not isinstance(inp, dict):
        return ""
    if name == "Bash":
        return (inp.get("command") or "")[:300]
    for k in ("file_path", "notebook_path", "path", "pattern"):
        if inp.get(k):
            return str(inp[k])[:300]
    if name in ("Agent", "Task"):
        return f'{inp.get("subagent_type") or "general"} | {(inp.get("description") or "")[:100]}'
    if name == "Skill":
        return str(inp.get("skill") or inp.get("command") or "")[:120]
    return json.dumps(inp, ensure_ascii=False)[:120]


def read_chat(args):
    path, start = args
    c = {"chat": os.path.basename(path)[:-6], "project": os.path.basename(os.path.dirname(path)),
         "cwd": None, "ep": None, "version": None, "t0": None, "t1": None, "msgs": [], "calls": [],
         "compactions": [], "commands": [], "attachments": Counter(), "helpers": None}
    ctx = 0
    pend_int = pend_rej = 0
    by_id = {}
    try:
        fh = open(path, "rb")
    except OSError:
        return None
    with fh:
        for n, line in enumerate(fh):
            if c["ep"] is None and n < 400 and b'"entrypoint":"' in line:
                j = line.find(b'"entrypoint":"')
                c["ep"] = line[j + 14:line.find(b'"', j + 14)].decode("ascii", "replace")
                if c["ep"].startswith(UNATTENDED_PREFIX):
                    return None
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if c["ep"] is None and r.get("entrypoint"):
                c["ep"] = r["entrypoint"]
                if c["ep"].startswith(UNATTENDED_PREFIX):
                    return None
            if r.get("isSidechain"):
                continue
            ts = r.get("timestamp")
            if not ts:
                continue
            t = epoch(ts)
            live = t >= start
            if live:
                c["t0"] = c["t0"] or t
                c["t1"] = t
                c["cwd"] = c["cwd"] or r.get("cwd")
                c["version"] = c["version"] or r.get("version")
            kind = r.get("type")
            if kind == "attachment":
                a = r.get("attachment") or {}
                if live and isinstance(a, dict):
                    c["attachments"][a.get("type") or "?"] += 1
                continue
            if kind == "assistant":
                m = r.get("message") or {}
                u = m.get("usage") or {}
                x = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + \
                    (u.get("cache_creation_input_tokens") or 0)
                if x:
                    ctx = x
                if live:
                    for b in m.get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            call = {"t": t, "ctx": ctx, "name": b.get("name"), "key": key_of(b.get("name"), b.get("input")),
                                    "err": None, "out": None, "at": len(c["msgs"])}
                            by_id[b.get("id")] = call
                            c["calls"].append(call)
                continue
            if kind == "system":
                if r.get("subtype") == "compact_boundary":
                    md = r.get("compactMetadata") or {}
                    if live:
                        c["compactions"].append({"t": t, "at": len(c["msgs"]), "trigger": md.get("trigger"),
                                                 "pre": md.get("preTokens"), "post": md.get("postTokens")})
                    ctx = md.get("postTokens") or 0
                continue
            if kind != "user":
                continue
            content = (r.get("message") or {}).get("content")
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                        continue
                    call = by_id.get(b.get("tool_use_id"))
                    body = text_of(b.get("content")) or str(b.get("content") or "")
                    if call is not None:
                        call["err"] = bool(b.get("is_error"))
                        if call["err"]:
                            call["out"] = body[:160]
                    if b.get("is_error") and any(m in body for m in REJECT):
                        pend_rej += 1
                continue
            st = text_of(content).lstrip()
            if st.startswith(INTERRUPT):
                pend_int += 1
                continue
            if st.startswith("<command-name>"):
                if live:
                    name = st[len("<command-name>"):st.find("</command-name>")]
                    a0 = st.find("<command-args>")
                    args = st[a0 + 14:st.find("</command-args>")] if a0 >= 0 else ""
                    c["commands"].append({"t": t, "at": len(c["msgs"]), "name": name, "args": args[:80]})
                continue
            if r.get("isMeta") or r.get("isCompactSummary"):
                continue
            ps = r.get("promptSource")
            if ps == "sdk" and r.get("entrypoint") in PERSON_APPS and (r.get("origin") or {}).get("kind") in (None, "human"):
                ps = "typed"   # the person, typing in VS Code / desktop / JetBrains (see PERSON_APPS)
            if ps is not None and ps not in TYPED:
                continue
            if ps is None and (any(st.startswith(m) for m in INJECTED) or
                               (r.get("origin") or {}).get("kind") not in (None, "human")):
                continue
            if not st:
                continue
            # Not typed by the person, whatever promptSource says: other sessions reporting back. Step-ins carry
            # over to their next real message. (Found 11-02: 1,051 of 5,476 "typed" messages were these.)
            if st.startswith(NOT_THE_PERSON):
                continue
            # A slash command typed as plain text (e.g. "/compact") is a command, not a message.
            if st.startswith("/") and " " not in st.strip() and len(st.strip()) < 40:
                if live:
                    c["commands"].append({"t": t, "at": len(c["msgs"]), "name": st.strip(), "args": ""})
                continue
            if live:
                c["msgs"].append({"i": len(c["msgs"]), "t": t, "ctx": ctx, "int": pend_int, "rej": pend_rej,
                                  "corr": bool(CORRECTION_RE.match(st[:80])), "chars": len(st),
                                  "head": st[:160].replace("\n", " "), "pm": r.get("permissionMode"),
                                  "uuid": r.get("uuid")})
            pend_int = pend_rej = 0
    if not c["msgs"]:
        return None
    # helpers: the chat's sub-agent files
    h = {"calls": Counter(), "bash": [], "files_read": 0, "n": 0}
    for q in glob.glob(os.path.join(path[:-6], "subagents", "*.jsonl")):
        h["n"] += 1
        try:
            with open(q, "rb") as fh:
                for line in fh:
                    if b'"tool_use"' not in line or b'"type":"assistant"' not in line:
                        continue
                    try:
                        m = json.loads(line).get("message") or {}
                    except ValueError:
                        continue
                    for b in m.get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            nm = b.get("name")
                            h["calls"][nm] += 1
                            if nm in ("Read", "Grep", "Glob"):
                                h["files_read"] += 1
                            if nm == "Bash" and len(h["bash"]) < 400:
                                h["bash"].append(key_of(nm, b.get("input")))
        except OSError:
            pass
    c["helpers"] = h
    return c


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    start = time.time() - a.days * 86400
    files = [p for p in glob.glob(os.path.join(a.root, "*", "*.jsonl")) if os.path.getmtime(p) >= start]
    with Pool(max(1, (os.cpu_count() or 2) - 1)) as pool:
        chats = [c for c in pool.map(read_chat, [(p, start) for p in files], chunksize=8) if c]
    # one copy of each typed message: resumed or forked chats can carry their parent's records
    seen = set()
    chats.sort(key=lambda c: c["t0"] or 0)
    for c in chats:
        c["msgs"] = [m for m in c["msgs"] if not (m["uuid"] and m["uuid"] in seen or seen.add(m["uuid"]))]
        for i, m in enumerate(c["msgs"]):
            m["i"] = i
    chats = [c for c in chats if c["msgs"]]
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        f.write(json.dumps({"meta": {"days": a.days, "root": a.root, "files_read": len(files), "chats": len(chats),
                                     "made": time.time()}}) + "\n")
        for c in chats:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"{len(chats)} chats, {sum(len(c['msgs']) for c in chats)} typed messages, "
          f"{sum(len(c['calls']) for c in chats)} agent calls -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
