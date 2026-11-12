#!/usr/bin/env python3
"""The substrate map: one page that shows what the tool can do today, where v15 stands, what's missing and whether
it hangs together.

Reads only — substrate/*.yaml, coverage/playbook.json, coverage/capabilities.yaml, STATUS.md and the latest
evidence run on this laptop (~/.cache/setup-snapshot/evidence-*/) — and writes one self-contained HTML page.
Run through the `substrate` command (.bin/substrate at the workspace root), which supplies PyYAML:
    substrate            rebuild the page and open it
    substrate status     where it stands, in the terminal
    substrate build      rebuild the page without opening it
    substrate path       print where the page is
SUBSTRATE_ROOT points it at another copy of labs/v15; SUBSTRATE_MAP at another output file.
"""
import datetime
import glob
import html
import json
import os
import re
import subprocess
import sys

import yaml

HERE = os.environ.get("SUBSTRATE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("clock", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..",
                                                                    "setup-snapshot", "experiments", "2026-11-02-evidence", "clock.py"))
clock = importlib.util.module_from_spec(_spec)   # the one place the calendar clock is read
_spec.loader.exec_module(clock)
OUT = os.environ.get("SUBSTRATE_MAP") or os.path.expanduser("~/.cache/setup-snapshot/v15-map.html")
ORDER = ["context", "prompt", "verification", "harness", "orchestration"]
ANCHORED = {"standard", "emerging"}  # 11-02 rule: only these terms may anchor a practice or challenge
PLAYBOOK = "https://agentic.proxify.io"


def grade_of(g):
    return (g or "unnamed").split(" ")[0].strip("()").lower()


def git(*args):
    try:
        return subprocess.run(["git", "-C", HERE, *args], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def question_kind(q):
    head = q.split(":", 1)[0].lower()
    for key, kind in (("ruling", "Ruling needed"), ("level guide", "Level guide conflict"),
                      ("playbook", "Playbook conflict"), ("content gap", "Content gap"), ("rule 1", "Ruling needed")):
        if key in head:
            return kind
    return "Other"


def read_status():
    """STATUS.md's layer table + its Next / Open rulings sections, so the page follows whatever the v15 session writes."""
    path = os.path.join(HERE, "STATUS.md")
    if not os.path.exists(path):
        return {"layers": [], "sections": {}}
    text = open(path).read()
    layers, sections, current = [], {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            continue
        if current and current.startswith("What exists") and line.startswith("|") and not line.startswith("|---"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 3 and cells[0] != "Layer":
                holds = cells[2]
                state = "bad" if "❌" in holds else "warn" if "⚠" in holds else "good"
                layers.append({"layer": cells[0], "files": cells[1], "holds": holds, "state": state})
        elif current:
            sections[current].append(line)
    keep = {k: "\n".join(v).strip() for k, v in sections.items() if k.startswith(("Next", "Open rulings"))}
    return {"layers": layers, "sections": keep}


def load():
    discs, challenges, practices, crosswalk, questions, checks = [], {}, {}, [], [], []
    for d in ORDER:
        s = yaml.safe_load(open(os.path.join(HERE, "substrate", d + ".yaml")))
        discs.append({**s["discipline"], "challenges": [c["id"] for c in s["challenges"]],
                      "practices": [p["id"] for p in s["practices"]]})
        for c in s["challenges"]:
            cross = [r.split(":", 1)[1] for r in c.get("also_handled_in") or [] if ":" in r]
            challenges[c["id"]] = {**c, "discipline": d, "all_handlers": list(c.get("handled_by") or []) + cross}
        for p in s["practices"]:
            cross = [r.split(":", 1)[1] for r in p.get("also_handles") or [] if ":" in r]
            practices[p["id"]] = {**p, "discipline": d, "all_handles": list(p.get("handles") or []) + cross}
        crosswalk += [{**x, "discipline": d} for x in s.get("crosswalk") or []]
        questions += [{"text": q, "discipline": d, "kind": question_kind(q)} for q in s.get("open_questions") or []]
        checks += [{"text": q, "discipline": d} for q in s.get("checker_changes") or []]
    for pid, p in practices.items():  # same symmetry rule as tools/build.py
        for cid in p["all_handles"]:
            c = challenges.get(cid)
            if c is not None and pid not in c["all_handlers"]:
                c["all_handlers"].append(pid)
    v13_path = os.path.join(HERE, "..", "v13", "substrate", "practices.yaml")
    v13 = []
    if os.path.exists(v13_path):
        for vp in (yaml.safe_load(open(v13_path)) or {}).get("practices") or []:
            v13.append({"id": vp["id"], "question": vp.get("question", ""), "badge": vp.get("badge", ""),
                        "cues": list(vp.get("cues") or [])})
    cov_path = os.path.join(HERE, "coverage", "playbook.json")
    coverage = json.load(open(cov_path)) if os.path.exists(cov_path) else []
    return discs, challenges, practices, crosswalk, questions, checks, coverage, v13


def load_tooling(practices):
    """coverage/tooling.yaml (verdict + cost per practice, a judgement) and the raw audit it rests on (his data, not committed)."""
    t_path = os.path.join(HERE, "coverage", "tooling.yaml")
    tooling = (yaml.safe_load(open(t_path)) or {}).get("practices", {}) if os.path.exists(t_path) else {}
    audits = sorted(glob.glob(os.path.expanduser("~/.cache/setup-snapshot/v15-audit-*.json")), key=os.path.getmtime)
    meta, claims, missed = {}, [], []
    if audits:
        a = json.load(open(audits[-1]))
        meta = {"file": audits[-1], "tokens": a.get("totalTokens"), "agents": a.get("agentCount")}
        for r in a.get("result") or []:
            claims += [{**c, "discipline": r.get("discipline")} for c in r.get("claims") or [] if c.get("kind") == "rung"]
            missed += [m for m in r.get("missed_practices") or [] if isinstance(m, dict)]
    for pid, p in practices.items():
        keys = [pid.lower(), (p.get("name") or "").lower()]
        p["tool"] = tooling.get(pid)
        p["audit"] = {"claims": [c for c in claims if any(k and k in (c.get("claim") or "").lower() for k in keys)],
                      "missed": [m for m in missed if (m.get("practice") or "").lower().split(" ")[0] == pid.lower()
                                 or keys[1] in (m.get("practice") or "").lower()]}
    return meta


def load_capabilities():
    """coverage/capabilities.yaml: each job the tool must do and how far it is (hand-kept, dated)."""
    path = os.path.join(HERE, "coverage", "capabilities.yaml")
    return (yaml.safe_load(open(path)) or {}) if os.path.exists(path) else {}


def load_live():
    """The newest evidence run on this laptop (his data, never committed): the level read, rungs, skeptic tally,
    recommendations. Shown as 'live' so the page says what the tool reads right now, not what a note claims."""
    runs = sorted(glob.glob(os.path.expanduser("~/.cache/setup-snapshot/evidence-*/v15-result.json")),
                  key=os.path.getmtime)
    if not runs:
        return {}
    run = os.path.dirname(runs[-1])
    r = json.load(open(runs[-1]))
    ev = os.path.join(run, "evidence.json")
    tally = (json.load(open(ev)).get("tally") or {}) if os.path.exists(ev) else {}
    rungs = {d["id"]: [{"id": p["id"], "rung": p.get("rung"), "state": p.get("state"),
                        "could_be": p.get("could_be_rung") or p.get("could_be")} for p in d.get("practices") or []]
             for d in r.get("disciplines") or []}
    recs = [{k: x.get(k) for k in ("challenge_name", "term", "practice", "practice_name", "now", "next", "next_step")}
            for x in r.get("recommendations") or []]
    return {"run": run.replace(os.path.expanduser("~"), "~"),
            "built": datetime.datetime.fromtimestamp(os.path.getmtime(runs[-1])).strftime("%Y-%m-%d %H:%M"),
            "pointer": r.get("pointer") or {}, "rungs": rungs, "tally": tally, "recommendations": recs,
            "more": len(r.get("more_recommendations") or [])}


def rung_state(entries):
    st = {e.get("status") for e in entries or []}
    return "taught" if "teaches" in st else "mentioned" if "mentions" in st else "missing"


def analyse():
    discs, challenges, practices, crosswalk, questions, checks, coverage, v13 = load()
    audit_meta = load_tooling(practices)
    mapped = {x.get("v13") for x in crosswalk}
    unmapped = [i for vp in v13 for i in [vp["id"], *vp["cues"]] if i not in mapped]
    cov_p = {p["id"]: p for d in coverage for p in d.get("practices", [])}
    cov_c = {c["id"]: c for d in coverage for c in d.get("challenges", [])}
    pages = [{**pg, "discipline": d.get("discipline")} for d in coverage for pg in d.get("missing_pages", [])]

    for pid, p in practices.items():
        ladder = p.get("ladder") or {}
        cp = cov_p.get(pid, {})
        p["health"] = {
            "term": grade_of(p.get("grade")),
            "rungs": {r: bool((ladder.get(r) or "").strip()) for r in ("L2", "L3", "L4")},
            "call": bool((p.get("call_evidence") or "").strip()),
            "laptop": any(t.get("laptop_signal") for t in p.get("techniques") or []),
            "playbook": {r: (rung_state(cp.get(r)) if (ladder.get(r) or "").strip() else "n/a") for r in ("L2", "L3", "L4")},
            "learn": len(p.get("learn") or []),
            "questions": sum(1 for q in questions if pid in q["text"]),
            "from_v13": sum(1 for x in crosswalk if x.get("to") == pid),
        }
        p["coverage"] = {r: cp.get(r) or [] for r in ("L2", "L3", "L4")}
        p["coverage_note"] = cp.get("note", "")
        p["v13"] = [x for x in crosswalk if x.get("to") == pid]
        h = p["health"]
        p["reds"] = (h["term"] not in ANCHORED) + (not h["call"]) + (not h["laptop"]) + \
            sum(1 for v in h["playbook"].values() if v == "missing")
    for cid, c in challenges.items():
        cc = cov_c.get(cid, {})
        c["explained_in"], c["how_to_in"] = cc.get("explained_in", []), cc.get("how_to_in", [])
        c["v13"] = [x for x in crosswalk if x.get("to") == cid]

    rungs = [(pid, r, s) for pid, p in practices.items() for r, s in p["health"]["playbook"].items() if s != "n/a"]
    taught = sum(1 for *_, s in rungs if s == "taught")
    mentioned = sum(1 for *_, s in rungs if s == "mentioned")
    dangling = sorted({f"{pid} → {cid}" for pid, p in practices.items() for cid in p["all_handles"] if cid not in challenges} |
                      {f"{cid} ← {pid}" for cid, c in challenges.items() for pid in c["all_handlers"] if pid not in practices})
    flags = [
        ("unanchored", "Practices not named by the field",
         "The 11-02 rule: only standard or emerging terms anchor the substrate. These have no name in the field, or one source.",
         [pid for pid, p in practices.items() if p["health"]["term"] not in ANCHORED]),
        ("unanchored-c", "Challenges not named by the field", "Same rule, for the things that go wrong.",
         [cid for cid, c in challenges.items() if grade_of(c.get("grade")) not in ANCHORED]),
        ("orphan", "Challenges nothing fixes", "A challenge with no practice can never produce a recommendation.",
         [cid for cid, c in challenges.items() if not c["all_handlers"]]),
        ("no-laptop", "Practices the laptop can't see", "No technique carries a laptop signal, so the tool can only ask.",
         [pid for pid, p in practices.items() if not p["health"]["laptop"]]),
        ("no-detector", "Challenges with no detector", "The tool can't spot this going wrong on someone's machine yet.",
         [cid for cid, c in challenges.items() if not c.get("detector")]),
        ("no-call", "Practices with no call question", "An interviewer has nothing to ask about these.",
         [pid for pid, p in practices.items() if not p["health"]["call"]]),
        ("no-howto", "Challenges with no how-to in the playbook", "Explained maybe, but nobody shows the fix.",
         [cid for cid, c in challenges.items() if not c["how_to_in"]]),
        ("rung-missing", "Practice levels the playbook doesn't teach at all", "Recommended next steps with nowhere to learn them.",
         [f"{pid} {r}" for pid, r, s in rungs if s == "missing"]),
        ("v13-lost", "v13 practices or cues that went nowhere",
         "Not in any crosswalk, so v15 may have silently dropped something v13 scored.", unmapped),
        ("untooled", "Practices with no tool reading classified",
         "Missing from coverage/tooling.yaml, so the 'Can the tool see it?' view can't place them.",
         [pid for pid, p in practices.items() if not p.get("tool")]),
        ("dangling", "Broken links in the data", "A practice or challenge points at an id that doesn't exist.", dangling),
    ]
    flags = [{"id": i, "title": t, "why": w, "items": items} for i, t, w, items in flags]
    head = git("log", "-1", "--format=%h %cs")
    subs = git("log", "-1", "--format=%h %cs", "--", "substrate")
    return {
        "built": datetime.datetime.fromtimestamp(clock.now()).strftime("%Y-%m-%d %H:%M"),
        "root": HERE, "branch": git("rev-parse", "--abbrev-ref", "HEAD"), "head": head, "substrate_commit": subs,
        "version": "v15.0-draft", "status": read_status(),
        "disciplines": discs, "challenges": challenges, "practices": practices,
        "crosswalk": crosswalk, "v13": v13, "questions": questions, "checks": checks, "pages": pages, "flags": flags,
        "totals": {"rungs": len(rungs), "taught": taught, "mentioned": mentioned, "missing": len(rungs) - taught - mentioned},
        "playbook": PLAYBOOK, "audit": audit_meta, "can": load_capabilities(), "live": load_live(),
        "vocab": load_vocab(challenges, practices),
    }


def load_vocab(challenges, practices):
    """substrate/vocabulary.yaml: the one home of every term (tools/check.py keeps the copies honest)."""
    path = os.path.join(HERE, "substrate", "vocabulary.yaml")
    if not os.path.exists(path):
        return {}
    terms = (yaml.safe_load(open(path)) or {}).get("terms") or []
    used = {v for x in list(challenges.values()) + list(practices.values()) for v in x.get("vocab") or []}
    return {"terms": len(terms), "used": sum(t["id"] in used for t in terms)}


def terminal(data):
    t, P, C = data["totals"], data["practices"], data["challenges"]
    sym = {"good": "✅", "warn": "⚠️ ", "bad": "❌"}
    print(f"Substrate {data['version']} · {data['branch']} · {data['head']}")
    print(f"{len(data['disciplines'])} disciplines · {len(C)} challenges · {len(P)} practices · "
          f"{len(data['crosswalk'])} v13 items mapped · {len(data['questions'])} open questions")
    v = data.get("vocab") or {}
    if v:
        print(f"Vocabulary: {v['terms']} named terms live in the substrate · {v['used']} anchor a challenge or practice")
    print()
    can, live = data.get("can") or {}, data.get("live") or {}
    caps = can.get("capabilities") or []
    if caps:
        cs = {"proven": ("✅", "proven"), "ran": ("🟡", "ran, not checked"), "paper": ("📄", "built, never run"),
              "missing": ("❌", "not built")}
        print(f"WHAT IT CAN DO  (coverage/capabilities.yaml, checked {can.get('checked', '?')})")
        print("  " + " · ".join(f"{sum(c['state'] == s for c in caps)} {lbl}" for s, (_, lbl) in cs.items()))
        for g in can.get("groups") or []:
            print(f"  {g['name']}")
            for c in caps:
                if c["group"] == g["id"]:
                    print(f"    {cs[c['state']][0]} {c['name']}")
        pt = live.get("pointer") or {}
        if pt:
            could = f", could be {pt['could_be']}" if pt.get("could_be") else ""
            print(f"  → your level as the tool reads it now: {pt.get('level')}{could}  (run {live['run']}, {live['built']};"
                  " never compared with a level a person decided)")
        print()
    order =[("holds", "reads it right"), ("partly", "partly"), ("wrong", "wrong / missed"), ("cant-see", "laptop can't see")]
    costs = [("free", "free"), ("cheap-ai", "cheap AI"), ("call", "call only")]
    tool = [p["tool"] for p in P.values() if p.get("tool")]
    print("CAN THE TOOL SEE IT?  one laptop (yours), judged by the AI audit — not checked by a person")
    print("  " + " " * 18 + "".join(f"{c:>12}" for _, c in costs))
    for v, label in order:
        print(f"  {label:<18}" + "".join(f"{sum(1 for t in tool if t['verdict'] == v and t['cost'] == c):>12}" for c, _ in costs))
    fix = sum(1 for t in tool if t["verdict"] == "wrong" and t["cost"] == "free")
    print(f"  → fix first: {fix} practices the tool gets wrong that a plain script could read\n")
    print("WHERE IT STANDS")
    for row in data["status"]["layers"]:
        layer = re.sub(r"[*`]", "", row["layer"]).split(":")[0][:44]
        print(f"  {sym[row['state']]} {layer:<44} {re.sub(r'[*`]', '', row['holds'])[:90]}")
    print("\nBY DISCIPLINE                 practices  challenges  playbook taught  open questions")
    for d in data["disciplines"]:
        rs = [s for pid in d["practices"] for s in P[pid]["health"]["playbook"].values() if s != "n/a"]
        q = sum(1 for x in data["questions"] if x["discipline"] == d["id"])
        print(f"  {d['name']:<28}{len(d['practices']):>9}{len(d['challenges']):>12}"
              f"{sum(s == 'taught' for s in rs):>10}/{len(rs):<6}{q:>14}")
    print("\nWHAT'S MISSING")
    for f in data["flags"]:
        if f["items"]:
            print(f"  {len(f['items']):>3}  {f['title']}")
    print(f"\nPlaybook: {t['taught']} of {t['rungs']} practice levels taught · {t['mentioned']} only mentioned · "
          f"{t['missing']} missing · {len(data['pages'])} pages to write")
    print(f"\nPage: {OUT}   (open it: substrate)")


def page(data):
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", blob).replace("__TITLE__", html.escape(f"Substrate {data['version']} — map"))


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--ink:#15172F;--ink2:#344054;--mute:#667085;--line:#EAECF0;--soft:#F4F5F7;--bg:#FCFCFD;--card:#fff;
--p:#5258FB;--p25:#F5F5FF;--p50:#E5E8F9;--p100:#D5D5FE;--ok:#039855;--ok25:#ECFDF3;--warn:#DC6803;--warn25:#FFFAEB;
--bad:#D92D20;--bad25:#FEF3F2;--na:#D0D5DD}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;font:400 14px/1.5 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink);background:var(--bg)}
a{color:var(--p);text-decoration:none}a:hover{text-decoration:underline}
code{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--soft);padding:1px 5px;border-radius:4px}
header{background:var(--ink);color:#fff;padding:18px 28px 0;position:sticky;top:0;z-index:20}
.top{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.eyebrow{font:500 11px/1 Inter,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:#B1B8FF}
h1{font:600 22px/1.2 Inter,sans-serif;margin:4px 0 0}
.meta{color:#949BAD;font-size:12px;margin-left:auto;text-align:right}
.search{position:relative;flex:0 1 340px}
.search input{width:100%;padding:9px 12px;border-radius:8px;border:1px solid #344054;background:#262841;color:#fff;font:inherit}
.search input::placeholder{color:#949BAD}
#results{position:absolute;top:42px;left:0;right:0;background:#fff;color:var(--ink);border-radius:8px;box-shadow:0 12px 32px rgba(16,24,40,.18);max-height:60vh;overflow:auto;display:none}
#results a{display:block;padding:8px 12px;border-bottom:1px solid var(--line);color:var(--ink)}#results a:hover,#results a.on{background:var(--p25);text-decoration:none}
#results small{color:var(--mute)}
nav{display:flex;gap:4px;margin-top:14px;overflow-x:auto}
nav a{color:#D0D5DD;padding:10px 14px;border-radius:8px 8px 0 0;font-weight:500;white-space:nowrap}
nav a:hover{text-decoration:none;color:#fff}nav a.on{background:var(--bg);color:var(--ink)}
nav .n{font-size:11px;background:#344054;color:#fff;border-radius:10px;padding:1px 7px;margin-left:6px}nav a.on .n{background:var(--p);}
main{padding:24px 28px 80px;max-width:1500px}
section.tab{display:none}section.tab.on{display:block}
h2{font:600 20px/1.3 Inter,sans-serif;margin:28px 0 6px}h2:first-child{margin-top:0}
h3{font:600 16px/1.3 Inter,sans-serif;margin:0 0 4px}
.lead{color:var(--ink2);margin:0 0 14px;max-width:900px}
.grid{display:grid;gap:12px}.g3{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}.g5{grid-template-columns:repeat(auto-fill,minmax(170px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.state{border-left:4px solid var(--na)}.state.good{border-left-color:var(--ok)}.state.warn{border-left-color:var(--warn)}.state.bad{border-left-color:var(--bad);background:var(--bad25)}
.state b{display:block;margin-bottom:4px}.state .h{color:var(--ink2);font-size:13px}.state .f{color:var(--mute);font-size:12px;margin-top:6px}
.num{font:600 28px/1.1 Inter,sans-serif}.num small{font-size:13px;color:var(--mute);font-weight:400;display:block;margin-top:4px}
.flag{display:flex;gap:12px;align-items:flex-start;cursor:pointer}.flag:hover{border-color:var(--p100)}
.flag .c{font:600 22px/1 Inter,sans-serif;min-width:44px;text-align:right;color:var(--bad)}.flag .c.zero{color:var(--ok)}
.flag p{margin:2px 0 0;color:var(--mute);font-size:13px}
.flagitems{margin-top:8px;font-size:13px}.flag:not(.open) .flagitems{display:none}.flag .more{font-size:12px;color:var(--p);margin-top:4px}.flag.open .more{display:none}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:#fff;border-radius:999px;padding:3px 10px;font-size:12.5px;color:var(--ink);cursor:pointer}
.chip:hover{border-color:var(--p);text-decoration:none}
.g{font:500 10.5px/1 Inter,sans-serif;letter-spacing:.04em;text-transform:uppercase;border-radius:4px;padding:3px 6px}
.g.standard{background:var(--ok25);color:var(--ok)}.g.emerging{background:var(--p50);color:var(--p)}
.g.single-source,.g.folk{background:var(--warn25);color:var(--warn)}.g.unnamed{background:var(--bad25);color:var(--bad)}
.bar{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--soft);min-width:120px}
.bar i{display:block}.bar .t{background:var(--ok)}.bar .m{background:#FBDEA5}.bar .x{background:var(--bad)}
table{border-collapse:separate;border-spacing:0;width:100%;background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font:500 11px/1.2 Inter,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);background:var(--soft);position:sticky;top:0}
tr.dh td{background:var(--p25);font-weight:600}
td.cell{text-align:center;width:62px}
.dot{display:inline-block;width:12px;height:12px;border-radius:50%;background:var(--na)}
.dot.good,.dot.taught{background:var(--ok)}.dot.warn,.dot.mentioned{background:#F8C869}.dot.bad,.dot.missing{background:var(--bad)}.dot.na{background:transparent;border:1px dashed var(--na)}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--mute);font-size:12.5px;margin:6px 0 12px}.legend span{display:inline-flex;gap:6px;align-items:center}
.disc{margin-bottom:28px}.dhead{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.dq{color:var(--ink2);margin:2px 0 12px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.two{grid-template-columns:1fr}}
.col h4{font:500 11px/1 Inter,sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--mute);margin:0 0 8px}
.item{display:flex;gap:8px;align-items:flex-start;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:#fff;margin-bottom:6px;cursor:pointer;transition:opacity .12s}
.item:hover{border-color:var(--p)}.item .nm{flex:1}.item .sub{color:var(--mute);font-size:12px}
.item .dots{display:flex;gap:3px;margin-top:4px}.item .dots .dot{width:9px;height:9px}
.dim .item{opacity:.28}.dim .item.hl{opacity:1;border-color:var(--p);box-shadow:0 0 0 2px var(--p50)}
.xref{font-size:11px;color:var(--p);border:1px dashed var(--p100);border-radius:4px;padding:1px 5px;white-space:nowrap}
#drawer{position:fixed;top:var(--hh,130px);right:0;bottom:0;width:var(--dw);border-left:1px solid var(--line);background:#fff;box-shadow:-12px 0 40px rgba(16,24,40,.18);transform:translateX(105%);transition:transform .18s ease;z-index:30;overflow:auto;padding:22px 26px 60px}
#drawer.on{transform:none}
:root{--dw:min(560px,42vw)}body.dopen main{margin-right:var(--dw)}main{transition:margin-right .18s ease}
@media(max-width:900px){:root{--dw:100vw}body.dopen main{margin-right:0}}
.disc{scroll-margin-top:calc(var(--hh,130px) + 12px)}
.item.sel{border-color:var(--p);background:var(--p25);box-shadow:inset 3px 0 0 var(--p)}
tr.v13h td{background:var(--soft);font-weight:600}tr.lost td{background:var(--bad25)}
.chip.on{background:var(--ink);color:#fff;border-color:var(--ink)}
#drawer .x{position:sticky;top:0;float:right;z-index:2;border:0;background:var(--soft);border-radius:8px;padding:6px 10px;cursor:pointer;font:inherit}
#drawer h2{margin-top:6px}#drawer h5{font:500 11px/1 Inter,sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--mute);margin:20px 0 8px}
.ladder>div{display:grid;grid-template-columns:44px 1fr;gap:10px;padding:8px 0;border-bottom:1px solid var(--line)}.ladder ul{margin:6px 0 0;padding-left:18px;font-size:12px;color:var(--mute)}
.ladder b{font-weight:600;color:var(--p)}
.tech{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin-bottom:6px;font-size:13px}.tech .ls{color:var(--mute);font-size:12px;margin-top:4px}
.q{padding:10px 14px;border:1px solid var(--line);border-radius:10px;background:#fff;margin-bottom:8px}
.kind{font:500 10.5px/1 Inter,sans-serif;letter-spacing:.05em;text-transform:uppercase;border-radius:4px;padding:3px 6px;background:var(--p50);color:var(--p);margin-right:8px}
.kind.Playbook{background:var(--warn25);color:var(--warn)}.kind.Level{background:var(--bad25);color:var(--bad)}.kind.Content{background:var(--soft);color:var(--ink2)}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}
.filters button{border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 12px;font:inherit;font-size:13px;cursor:pointer}
.filters button.on{background:var(--ink);color:#fff;border-color:var(--ink)}
input.f{padding:8px 12px;border:1px solid var(--line);border-radius:8px;font:inherit;width:min(420px,100%)}
.md p{margin:6px 0}.md ul{margin:6px 0;padding-left:20px}
.muted{color:var(--mute)}.small{font-size:12.5px}
details summary{cursor:pointer;color:var(--p);margin:8px 0}
.banner{background:var(--warn25);border:1px solid #FBDEA5;border-radius:10px;padding:10px 14px;color:var(--ink2);font-size:13px;margin:0 0 14px;max-width:1100px}
.headline{font:500 17px/1.45 Inter,sans-serif;max-width:1100px;margin:0 0 18px}
.headline b{font-weight:600}
.vb,.cb{display:inline-block;font:500 11px/1 Inter,sans-serif;border-radius:4px;padding:4px 7px;white-space:nowrap}
.vb.holds{background:var(--ok25);color:var(--ok)}.vb.partly{background:var(--warn25);color:var(--warn)}.vb.wrong{background:var(--bad25);color:var(--bad)}.vb.cant-see{background:var(--soft);color:var(--ink2)}
.cb.free{background:var(--ok25);color:var(--ok)}.cb.cheap-ai{background:var(--warn25);color:var(--warn)}.cb.call{background:var(--bad);color:#fff}
table.tg{table-layout:fixed}table.tg td{height:1px}
table.tg th.call{background:var(--bad25);color:var(--bad)}table.tg td.call{background:#FFFBFA}
table.tg td.first{background:var(--p25);box-shadow:inset 0 0 0 2px var(--p)}
.cellhead{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}.cellhead b{font:600 20px/1 Inter,sans-serif}
.act{font:500 11px/1 Inter,sans-serif;letter-spacing:.04em;text-transform:uppercase;color:var(--mute)}td.first .act{color:var(--p)}
.rowlabel{font-weight:600}.rowlabel small{display:block;font-weight:400;color:var(--mute)}
.gbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 10px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:999px;overflow:hidden;background:#fff}
.seg button{border:0;background:none;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer;color:var(--ink2)}.seg button.on{background:var(--ink);color:#fff}
.gbar label{font-size:13px;display:inline-flex;gap:6px;align-items:center;cursor:pointer;color:var(--ink2)}
.gbtn{border:1px solid var(--line);background:#fff;border-radius:999px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer}.gbtn:hover{border-color:var(--p)}
.gwrap{position:relative;height:calc(100vh - var(--hh,130px) - 120px);min-height:440px;border:1px solid var(--line);border-radius:12px;background:#fff;overflow:hidden}
#gc{width:100%;height:100%;display:block;cursor:grab;touch-action:none}#gc.drag{cursor:grabbing}#gc.pt{cursor:pointer}
#gtip{position:absolute;pointer-events:none;background:var(--ink);color:#fff;font-size:12px;line-height:1.35;padding:6px 9px;border-radius:6px;display:none;max-width:320px;z-index:3}
#gtip small{color:#B1B8FF;display:block}
#glegend{position:absolute;left:12px;bottom:12px;background:rgba(255,255,255,.94);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12px;display:flex;flex-direction:column;gap:5px;z-index:2}
#glegend span{display:inline-flex;align-items:center;gap:7px}#glegend i{display:inline-block;width:11px;height:11px}
#ghelp{position:absolute;right:12px;bottom:10px;z-index:2;background:rgba(255,255,255,.9);padding:2px 6px;border-radius:6px}
#ginfo{position:absolute;right:12px;top:12px;max-width:360px;background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:13px;box-shadow:0 8px 24px rgba(16,24,40,.12);display:none;z-index:2}
.audit{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:6px 0;font-size:12.5px}.audit i{color:var(--mute)}
.cap{border-left:4px solid var(--na);display:flex;flex-direction:column;gap:7px}
.cap.proven{border-left-color:var(--ok)}.cap.ran{border-left-color:#F8C869}.cap.paper{border-left-color:#98A2B3}.cap.missing{border-left-color:var(--bad)}
.cap b{font-size:14.5px;line-height:1.35}.cap p{margin:0;font-size:13px;color:var(--ink2)}
.cap .lbl{font:500 10.5px/1 Inter,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin-right:6px}
.cs{align-self:flex-start;font:500 11px/1 Inter,sans-serif;border-radius:4px;padding:4px 7px;white-space:nowrap}
.cs.proven{background:var(--ok25);color:var(--ok)}.cs.ran{background:var(--warn25);color:var(--warn)}.cs.paper{background:var(--soft);color:var(--ink2)}.cs.missing{background:var(--bad25);color:var(--bad)}
.live{background:var(--p25);border:1px solid var(--p100);border-radius:8px;padding:6px 10px;font-size:12.5px;color:var(--ink2)}
.sbar{display:flex;height:12px;border-radius:6px;overflow:hidden;max-width:760px;margin:0 0 6px}.sbar i{display:block}
.cgroup{margin-top:26px}.cgroup h3{margin:0}.cgroup .gq{color:var(--mute);margin:2px 0 10px;font-size:13px}
.lvl{display:grid;grid-template-columns:190px 1fr;gap:22px;align-items:start}@media(max-width:900px){.lvl{grid-template-columns:1fr}}
.big{font:700 60px/1 Inter,sans-serif;color:var(--p)}.big small{display:block;font:500 13px/1.4 Inter,sans-serif;color:var(--mute);margin-top:8px}
.reasons{margin:0 0 12px;padding-left:18px}.reasons li{margin:4px 0}
.rg{display:grid;grid-template-columns:150px 1fr;gap:8px 12px;align-items:start;margin-top:6px}.rg .dn{font-weight:600;font-size:13px;padding-top:4px}
.rc{display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:3px 9px;font-size:12px;border:1px solid transparent;margin:0 4px 4px 0;color:var(--ink)}
.rc:hover{text-decoration:none;border-color:var(--ink)}.rc i{font:600 10.5px/1 Inter,sans-serif;font-style:normal}
.rc.L4{background:var(--p);color:#fff}.rc.L3{background:var(--p100)}.rc.L2{background:var(--soft)}.rc.seen{background:#fff;border-color:var(--p100)}
.rc.lnf{background:var(--bad25);color:var(--bad)}.rc.nr{background:#fff;border:1px dashed var(--na);color:var(--mute)}
.recs{display:grid;gap:8px;margin-top:6px}.recs>div{border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:#fff;font-size:13px}
</style></head><body>
<header><div class="top">
<div><div class="eyebrow">Agentic substrate</div><h1 id="title"></h1></div>
<div class="search"><input id="q" placeholder="Find a practice, challenge, v13 id…  ( / )" autocomplete="off"><div id="results"></div></div>
<div class="meta" id="meta"></div></div>
<nav id="nav"></nav></header>
<main>
<section class="tab" id="t-can"></section>
<section class="tab" id="t-overview"></section>
<section class="tab" id="t-map"></section>
<section class="tab" id="t-graph"></section>
<section class="tab" id="t-tool"></section>
<section class="tab" id="t-questions"></section>
<section class="tab" id="t-v13"></section>
<section class="tab" id="t-pages"></section>
</main>
<aside id="drawer" aria-hidden="true"></aside>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const P=D.practices,C=D.challenges,DISC=Object.fromEntries(D.disciplines.map(d=>[d.id,d]));
const ANCH=new Set(['standard','emerging']);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const md=s=>esc(s).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/`([^`]+)`/g,'<code>$1</code>');
const grade=g=>String(g||'unnamed').split(' ')[0].replace(/[()]/g,'').toLowerCase();
const gb=g=>`<span class="g ${grade(g)}" title="${esc(g||'unnamed')}">${esc(grade(g))}</span>`;
const pchip=id=>P[id]?`<a class="chip" href="#p/${id}">${gb(P[id].grade)}${esc(P[id].name)}${P[id].discipline?'':''}</a>`:`<span class="chip">⚠ ${esc(id)}</span>`;
const cchip=id=>C[id]?`<a class="chip" href="#c/${id}">${gb(C[id].grade)}${esc(C[id].term&&C[id].term!=='none'?C[id].term:C[id].name)}</a>`:`<span class="chip">⚠ ${esc(id)}</span>`;
const link=ref=>ref&&ref.startsWith('/')?`<a href="${D.playbook}${esc(ref)}" target="_blank">${esc(ref)}</a>`:ref&&ref.startsWith('http')?`<a href="${esc(ref)}" target="_blank">${esc(ref.replace(/^https?:\/\//,'').slice(0,60))}</a>`:esc(ref);
const dotsFor=p=>{const h=p.health;return [ANCH.has(h.term)?'good':h.term==='unnamed'?'bad':'warn',h.call?'good':'bad',h.laptop?'good':'bad',...['L2','L3','L4'].map(r=>h.playbook[r]==='n/a'?'na':h.playbook[r])]};
const DOTS=['Named by the field','Call question','Laptop can see it','Playbook teaches L2','Playbook teaches L3','Playbook teaches L4'];

const TABS=[['can','What it can do'],['tool','Can the tool see it?'],['overview','Where it stands'],['map','Map',Object.keys(P).length],['graph','Graph'],['questions','Open questions',D.questions.length],['v13','v13 → v15',D.crosswalk.length],['pages','Pages to write',D.pages.length]];
document.getElementById('title').textContent=`Substrate ${D.version}`;
document.getElementById('meta').innerHTML=`<div>${esc(D.branch)} · ${esc(D.head)}</div><div>substrate last changed ${esc(D.substrate_commit)} · page built ${esc(D.built)}</div><div>refresh: <code style="background:#262841;color:#fff">substrate</code></div>`;
document.getElementById('nav').innerHTML=TABS.map(([k,l,n])=>`<a href="#${k}" data-t="${k}">${l}${n!=null?`<span class="n">${n}</span>`:''}</a>`).join('');

/* ---------- What it can do ---------- */
const CST={proven:['Proven','ran on real input and passed an independent check'],ran:['Ran, not checked','ran on real input; no independent check yet'],paper:['Built, never run','exists, never used on real input'],missing:['Not built','nothing yet']};
const CCOLS={proven:'var(--ok)',ran:'#F8C869',paper:'#98A2B3',missing:'var(--bad)'};
(function(){
  const el=document.getElementById('t-can'),K=D.can||{},caps=K.capabilities||[],L=D.live||{},pt=L.pointer||{};
  if(!caps.length){el.innerHTML='<p class="muted">No <code>coverage/capabilities.yaml</code> found.</p>';return}
  const n=s=>caps.filter(c=>c.state===s).length;
  const step=r=>r.next==='spread'?`already ${esc(r.now)}: take it to more places`:`${esc(r.now)} → ${esc(r.next)}`;
  const T=Object.values(L.tally||{}),sum=f=>T.reduce((a,t)=>a+(f(t)||0),0);
  const rows=sum(t=>t.reader_rows),sk=k=>sum(t=>(t.skeptic||{})[k]);
  const live={
    tally:T.length?`${rows} practice and challenge rows · the skeptic fixed ${sk('fixed')}, kept ${sk('kept')} as written, threw out ${sk('killed')}`:'',
    states:T.length?`${sum(t=>t.practices_seen)} practices seen · ${sum(t=>t.practices_looked_not_found)} looked for, not found · ${sum(t=>t.practices_not_read)} never looked at`:'',
    pointer:pt.level?`reads you as <b>${esc(pt.level)}</b>${pt.could_be?`, could be ${esc(pt.could_be)}`:''} — the reasons are at the top of this tab`:'',
    rungs:pt.level?'every practice and its rung is in the grid at the top of this tab':'',
    recommendations:(L.recommendations||[]).length?`${L.recommendations.length} shown + ${L.more} more; first: ${esc(L.recommendations[0].challenge_name)} → ${esc(L.recommendations[0].practice_name)} (${step(L.recommendations[0])})`:''};
  const rungOf={};Object.values(L.rungs||{}).flat().forEach(p=>rungOf[p.id]=p);
  const short=s=>{s=String(s||'');return s.length>40?s.slice(0,38)+'…':s};
  const rchip=id=>{const r=rungOf[id]||{},p=P[id],cls=r.state==='looked, not found'?'lnf':r.state==='not read'?'nr':r.rung||'seen';
    const tag=r.rung?r.rung:r.state==='seen'?'seen, below its rung':r.state==='looked, not found'?'not found':r.state==='not read'?'not read':'?';
    return `<a class="rc ${cls}" href="#p/${id}" title="${esc(p?p.name:id)} — ${esc(tag)}${r.could_be?' · could be '+esc(r.could_be):''}"><i>${esc(tag)}</i>${esc(short(p?p.name:id))}</a>`};
  const reason=s=>{const m=String(s).match(/^(.*?):\s*([a-z0-9-]+(?:,\s*[a-z0-9-]+)*)\s*$/);
    if(m&&m[2].split(/,\s*/).every(id=>P[id]))return `${esc(m[1])}:<div style="margin-top:4px">${m[2].split(/,\s*/).map(rchip).join('')}</div>`;return linkify(s)};
  const lvl=pt.level?`<div class="card" style="margin-bottom:8px"><div class="lvl">
    <div><div class="big">${esc(pt.level)}<small>your level, as the tool reads your laptop right now${pt.could_be?` · could be <b>${esc(pt.could_be)}</b>`:''}</small></div>
      <div class="small muted" style="margin-top:10px">from <code>${esc(L.run)}</code>, ${esc(L.built)}. The member's own page never shows this.</div></div>
    <div><div class="banner" style="margin-bottom:10px">⚠️ <b>Never compared with a level a person decided</b>, and it rests on rows that haven't passed their audit. Read it as how the tool reasons, not as your level.</div>
      <h3>How it got there</h3><p class="small muted" style="margin:2px 0 6px">The rule (<code>levels.md</code>): L3 = their setup carries the context, plus everyday challenges handled by hand across the disciplines. L4 = they built things that change how the agent runs <b>and</b> an eval the agent can't edit. ✓ found · ✗ missing.</p>
      <ul class="reasons" style="list-style:none;padding-left:0">${(pt.reasons||[]).map(r=>`<li>${/not shown|not seen|not found|missing/i.test(r)?'<b style="color:var(--bad)">✗</b>':'<b style="color:var(--ok)">✓</b>'} ${reason(r)}</li>`).join('')}</ul>
      <h3>Every practice, by discipline</h3><div class="legend"><span><span class="rc L4"><i>L4</i></span>runs without you</span><span><span class="rc L3"><i>L3</i></span>your setup does it</span><span><span class="rc L2"><i>L2</i></span>you do it</span><span><span class="rc seen"><i>seen</i></span>seen, below the only rung it has</span><span><span class="rc lnf"><i>not found</i></span>looked, not found</span><span><span class="rc nr"><i>not read</i></span>never looked at</span></div>
      <div class="rg">${D.disciplines.map(d=>`<div class="dn">${esc(d.name)}${pt.best_rung_by_discipline&&pt.best_rung_by_discipline[d.id]?`<div class="small muted">best ${esc(pt.best_rung_by_discipline[d.id])}</div>`:''}</div><div>${(L.rungs[d.id]||[]).map(p=>rchip(p.id)).join('')||'<span class="muted small">nothing read</span>'}</div>`).join('')}</div>
      ${(L.recommendations||[]).length?`<h3 style="margin-top:16px">What it would recommend you</h3><div class="recs">${L.recommendations.map(r=>`<div><b>${esc(r.challenge_name)}</b> → <a href="#p/${esc(r.practice)}">${esc(r.practice_name)}</a> <span class="muted">(${step(r)})</span><div class="small muted" style="margin-top:4px">${esc(r.next_step)}</div></div>`).join('')}</div>`:''}
    </div></div></div>`:`<div class="banner">No evidence run on this laptop yet (<code>~/.cache/setup-snapshot/evidence-*/</code>), so there's no level to show.</div>`;
  const card=c=>`<div class="card cap ${esc(c.state)}"><span class="cs ${esc(c.state)}" title="${esc((CST[c.state]||['',''])[1])}">${esc((CST[c.state]||[c.state])[0])}</span><b>${esc(c.name)}</b>
    <p><span class="lbl">Today</span>${esc(c.today)}</p>${c.proof&&c.proof!=='—'?`<p><span class="lbl">Proof</span>${esc(c.proof)}</p>`:''}<p><span class="lbl">Missing</span>${esc(c.missing)}</p>
    ${c.live&&live[c.live]?`<div class="live"><span class="lbl">Live</span>${live[c.live]}</div>`:''}${c.where&&c.where!=='—'?`<div class="small muted"><code>${esc(c.where)}</code></div>`:''}</div>`;
  const groups=(K.groups||[]).map(g=>{const cs=caps.filter(c=>c.group===g.id);if(!cs.length)return'';
    return `<div class="cgroup"><h3>${esc(g.name)} <span class="muted small">${cs.map(c=>`<i class="dot" style="background:${CCOLS[c.state]};width:9px;height:9px"></i>`).join(' ')}</span></h3><p class="gq">${esc(g.question)}</p><div class="grid g3">${cs.map(card).join('')}</div></div>`}).join('');
  el.innerHTML=`<h2>What can the tool do today?</h2>
  <p class="headline">Of the <b>${caps.length}</b> things the tool has to do: <b>${n('proven')}</b> proven · <b>${n('ran')}</b> ran for real, not checked yet · <b>${n('paper')}</b> built but never run for real · <b>${n('missing')}</b> not built.</p>
  <div class="sbar">${Object.keys(CST).map(s=>`<i style="width:${100*n(s)/caps.length}%;background:${CCOLS[s]}" title="${esc(CST[s][0])}: ${n(s)}"></i>`).join('')}</div>
  <div class="legend">${Object.entries(CST).map(([k,[l,w]])=>`<span><span class="cs ${k}">${l}</span>${w}</span>`).join('')}</div>
  ${lvl}${groups}
  <p class="small muted" style="margin-top:22px">Hand-kept in <code>coverage/capabilities.yaml</code>, last checked ${esc(K.checked||'?')}; "Live" lines are read from the newest evidence run every time the page is built.</p>`;
})();

/* ---------- Where it stands ---------- */
(function(){
  const t=D.totals,L=D.status.layers;
  const st=L.length?`<div class="grid g3">${L.map(r=>`<div class="card state ${r.state}"><b>${md(r.layer)}</b><div class="h">${md(r.holds)}</div><div class="f">${md(r.files)}</div></div>`).join('')}</div>`:'<p class="muted">No STATUS.md table found.</p>';
  const nums=[[D.disciplines.length,'disciplines'],[Object.keys(C).length,'challenges · things that go wrong'],[Object.keys(P).length,'practices · what someone does about them'],[D.crosswalk.length,'v13 practices and cues mapped'],[D.questions.length,'open questions waiting on a ruling'],[`${t.taught}/${t.rungs}`,'practice levels the playbook teaches']];
  const bd=D.disciplines.map(d=>{const rs=d.practices.flatMap(id=>Object.values(P[id].health.playbook)).filter(s=>s!=='n/a');const n=rs.length||1,c=s=>rs.filter(x=>x===s).length;
    const un=d.practices.filter(id=>!ANCH.has(P[id].health.term)).length,q=D.questions.filter(x=>x.discipline===d.id).length;
    return `<tr><td><a href="#d/${d.id}"><b>${esc(d.name)}</b></a><div class="muted small">${esc(d.question)}</div></td><td>${d.practices.length}</td><td>${d.challenges.length}</td>
    <td><div class="bar"><i class="t" style="width:${100*c('taught')/n}%"></i><i class="m" style="width:${100*c('mentioned')/n}%"></i><i class="x" style="width:${100*c('missing')/n}%"></i></div><div class="small muted">${c('taught')} taught · ${c('mentioned')} mentioned · ${c('missing')} missing</div></td>
    <td>${un?`<span style="color:var(--bad)">${un}</span>`:'0'} / ${d.practices.length}</td><td>${q}</td></tr>`}).join('');
  const fl=D.flags.map(f=>`<div class="card flag" data-f="${f.id}"><div class="c ${f.items.length?'':'zero'}">${f.items.length}</div><div style="flex:1"><b>${esc(f.title)}</b><p>${esc(f.why)}</p>${f.items.length?'<div class="more">Show which ▸</div>':''}<div class="flagitems chips">${f.items.map(x=>{const id=x.split(' ')[0];return P[id]?pchip(id)+(x.includes(' L')?`<span class="small muted">${esc(x.split(' ')[1])}</span>`:''):C[id]?cchip(id):`<span class="chip">${esc(x)}</span>`}).join('')}</div></div></div>`).join('');
  const sec=D.status.sections,S=Object.entries(sec).map(([k,v])=>`<div class="card"><h3>${esc(k)}</h3><div class="md">${mdBlock(v)}</div></div>`).join('');
  document.getElementById('t-overview').innerHTML=`
  <h2>Where it stands</h2><p class="lead">Each layer of v15 and whether it holds, read live from <code>STATUS.md</code>. Red means it doesn't work yet.</p>${st}
  <div class="grid g5" style="margin-top:14px">${nums.map(([n,l])=>`<div class="card num">${n}<small>${esc(l)}</small></div>`).join('')}</div>
  <h2>What's missing or doesn't add up</h2><p class="lead">Checks run on the substrate itself every time the page is built. Click one to see which items.</p><div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(420px,1fr))">${fl}</div>
  <h2>By discipline</h2><p class="lead">How full each discipline is, and how much of it the playbook can teach.</p>
  <table><thead><tr><th>Discipline</th><th>Practices</th><th>Challenges</th><th>Playbook teaches its levels</th><th>Practices not named by the field</th><th>Open questions</th></tr></thead><tbody>${bd}</tbody></table>
  ${S?`<h2>What's next, and what only you can decide</h2><div class="grid g3">${S}</div>`:''}`;
  document.querySelectorAll('.flag').forEach(el=>el.addEventListener('click',e=>{if(e.target.closest('a'))return;el.classList.toggle('open')}));
})();
function mdBlock(s){const out=[];let ul=false;for(const raw of s.split('\n')){const l=raw.trim();if(!l){if(ul){out.push('</ul>');ul=false}continue}
  if(/^[-*] /.test(l)){if(!ul){out.push('<ul>');ul=true}out.push('<li>'+md(l.slice(2))+'</li>')}else if(l.startsWith('```')){continue}else{if(ul){out.push('</ul>');ul=false}out.push('<p>'+md(l)+'</p>')}}
  if(ul)out.push('</ul>');return out.join('')}

/* ---------- Map ---------- */
(function(){
  const legend=`<div class="legend">Each practice's six dots: ${DOTS.map((l,i)=>`<span>${i+1}. ${l}</span>`).join('')}<span><i class="dot good"></i>yes</span><span><i class="dot mentioned"></i>partly</span><span><i class="dot bad"></i>no</span><span><i class="dot na"></i>no such level</span></div>`;
  const body=D.disciplines.map(d=>{
    const ch=d.challenges.map(id=>{const c=C[id];const hs=c.all_handlers.filter(x=>P[x]);return `<div class="item" data-c="${id}" data-links="${hs.join(' ')}"><div class="nm">${gb(c.grade)} <b>${esc(c.term&&c.term!=='none'?c.term:c.name)}</b><div class="sub">${esc(c.name)}</div></div><div class="sub" title="practices that fix it">${hs.length?hs.length+(hs.length===1?' fix':' fixes'):'<span style="color:var(--bad)">no fix</span>'}${c.detector?' · 🔎':''}</div></div>`}).join('');
    const pr=d.practices.map(id=>{const p=P[id];const hs=p.all_handles.filter(x=>C[x]);const ext=hs.filter(x=>C[x].discipline!==d.id);
      return `<div class="item" data-p="${id}" data-links="${hs.join(' ')}"><div class="nm">${gb(p.grade)} <b>${esc(p.name)}</b><div class="sub">${p.audience==='builds-agents'?'for people who build agents · ':''}fixes ${hs.length} ${ext.length?`<span class="xref">+${ext.length} elsewhere</span>`:''}</div><div class="dots">${dotsFor(p).map((s,i)=>`<i class="dot ${s}" title="${DOTS[i]}"></i>`).join('')}</div></div></div>`}).join('');
    return `<div class="disc" id="d-${d.id}"><div class="dhead"><h2>${esc(d.name)}</h2><span class="muted">${d.practices.length} practices · ${d.challenges.length} challenges</span></div><p class="dq">${esc(d.question)}</p>
    <details><summary>What's in and out of this discipline</summary><p class="small" style="max-width:900px">${esc(d.scope_note)}</p></details>
    <div class="two" data-disc="${d.id}"><div class="col"><h4>What goes wrong</h4>${ch}</div><div class="col"><h4>What someone does about it</h4>${pr}</div></div></div>`}).join('');
  document.getElementById('t-map').innerHTML=`<h2>The map</h2><p class="lead">Each discipline: the things that go wrong on the left, the practices that fix them on the right. Hover one to light up what it connects to; click it to open the detail.</p>${legend}<div class="chips dchips" style="margin-bottom:18px"><a class="chip on" data-d="" href="#map">All five</a>${D.disciplines.map(d=>`<a class="chip" data-d="${d.id}" href="#d/${d.id}">${esc(d.name)} <span class="muted small">${d.practices.length}·${d.challenges.length}</span></a>`).join('')}</div>${body}`;
  document.querySelectorAll('#t-map .two').forEach(box=>{
    box.addEventListener('mouseover',e=>{const it=e.target.closest('.item');if(!it)return;const ids=new Set(it.dataset.links.split(' '));box.classList.add('dim');
      box.querySelectorAll('.item').forEach(x=>x.classList.toggle('hl',x===it||ids.has(x.dataset.c||x.dataset.p)))});
    box.addEventListener('mouseleave',()=>{box.classList.remove('dim');box.querySelectorAll('.hl').forEach(x=>x.classList.remove('hl'))});
    box.addEventListener('click',e=>{const it=e.target.closest('.item');if(it)location.hash=it.dataset.p?'p/'+it.dataset.p:'c/'+it.dataset.c});
  });
})();

/* ---------- Can the tool see it? ---------- */
const VERD={holds:['Reads it right','what the tool said held up'],partly:['Partly','right practice, wrong level or reason'],wrong:['Wrong or missed','said "not seen" or the wrong level for something you do'],'cant-see':["Laptop can't see it",'nothing on a laptop shows it']};
const COST={free:['Free','a script reads files, settings, git; seconds, no AI'],'cheap-ai':['Cheap AI','a small model reads one short extract'],call:['Call only','the interviewer has to ask: the expensive one']};
const actionOf=t=>!t?'Not classified':t.cost==='call'?'Ask on the call':t.cost==='cheap-ai'?'Add a small AI read':t.verdict==='wrong'?'Fix first':t.verdict==='partly'?'Fix next':'Keep';
const ACTS=['Fix first','Fix next','Add a small AI read','Ask on the call','Keep','Not classified'];
(function(){
  const el=document.getElementById('t-tool'),ps=Object.values(P),T=ps.filter(p=>p.tool),n=(v,c)=>T.filter(p=>p.tool.verdict===v&&(!c||p.tool.cost===c)).length;
  const fixFirst=T.filter(p=>actionOf(p.tool)==='Fix first').length,free=T.filter(p=>p.tool.cost==='free').length;
  const A=D.audit||{},tok=A.tokens?Math.round(A.tokens/1000)+'k tokens':'';
  const cell=(v,c)=>{const xs=T.filter(p=>p.tool.verdict===v&&p.tool.cost===c);const act=xs.length?actionOf(xs[0].tool):'';
    return `<td class="${c==='call'?'call':''} ${act==='Fix first'?'first':''}">${xs.length?`<div class="cellhead"><b>${xs.length}</b><span class="act">${esc(act)}</span></div><div class="chips">${xs.map(p=>`<a class="chip" href="#p/${p.id}" title="${esc(p.tool.note)}">${esc(p.name)}</a>`).join('')}</div>`:'<span class="muted">—</span>'}</td>`};
  const grid=`<table class="tg"><colgroup><col style="width:190px"><col><col><col></colgroup><thead><tr><th></th>${Object.entries(COST).map(([c,[l,w]])=>`<th class="${c}">${l}<div style="text-transform:none;letter-spacing:0;font-weight:400">${w}</div></th>`).join('')}</tr></thead>
    <tbody>${Object.entries(VERD).map(([v,[l,w]])=>`<tr><td class="rowlabel">${l}<small>${w}</small></td>${Object.keys(COST).map(c=>cell(v,c)).join('')}</tr>`).join('')}</tbody></table>`;
  const sorted=[...ps].sort((x,y)=>ACTS.indexOf(actionOf(x.tool))-ACTS.indexOf(actionOf(y.tool)));
  const rows=sorted.map(p=>{const t=p.tool||{};return `<tr data-a="${esc(actionOf(p.tool))}"><td><a href="#p/${p.id}">${esc(p.name)}</a><div class="small muted">${esc(DISC[p.discipline].name)}${p.audience==='builds-agents'?' · builds agents':''}</div></td>
    <td><span class="vb ${t.verdict||''}">${esc((VERD[t.verdict]||['?'])[0])}</span></td><td class="small">${esc(t.note||'')}</td><td><span class="cb ${t.cost||''}">${esc((COST[t.cost]||['?'])[0])}</span></td><td class="small">${esc(t.reads||'')}</td><td class="small"><b>${esc(actionOf(p.tool))}</b></td></tr>`}).join('');
  el.innerHTML=`<h2>Can the tool see each practice, and what would it cost?</h2>
  <div class="banner">⚠️ <b>Nothing here is proven yet.</b> "On your laptop" is one machine (yours), judged by an AI audit${tok?` (${A.agents} large-model readers, ${tok})`:''}; no person has checked the audit. "Cost" is the cheapest way that should work, taken from the audit's suggested fix for each practice: a judgement, not built or measured. Source: <code>coverage/tooling.yaml</code>.</div>
  <p class="headline">Today the tool reads <b>${n('holds')} of ${ps.length}</b> practices right, <b>${n('partly')}</b> partly, and gets <b>${n('wrong')}</b> wrong. But <b>${free} of ${ps.length}</b> could be read for free, by a script with no AI. Most of what's broken is scripts not reading files that are already on the laptop: <b>${fixFirst} to fix first.</b></p>
  ${grid}
  <h2>Practice by practice</h2><p class="lead">Sorted by what to do next. Click a practice for the audit's evidence and its suggested fix.</p>
  <div class="filters"><button class="on" data-a="">All ${ps.length}</button>${ACTS.map(a=>{const k=ps.filter(p=>actionOf(p.tool)===a).length;return k?`<button data-a="${esc(a)}">${esc(a)} ${k}</button>`:''}).join('')}</div>
  <table id="tt"><thead><tr><th style="width:24%">Practice</th><th>On your laptop</th><th style="width:28%">What the audit found</th><th>Cost to read</th><th style="width:24%">What it needs to read</th><th>Next</th></tr></thead><tbody>${rows}</tbody></table>`;
  el.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{el.querySelectorAll('.filters button').forEach(x=>x.classList.toggle('on',x===b));
    el.querySelectorAll('#tt tbody tr').forEach(tr=>tr.style.display=!b.dataset.a||tr.dataset.a===b.dataset.a?'':'none')});
})();
function toolBlock(p){const t=p.tool;if(!t)return '<h5>Can the tool see it?</h5><p class="muted">Not classified yet (coverage/tooling.yaml).</p>';
  const au=p.audit||{claims:[],missed:[]};
  const ev=[...au.claims.map(c=>`<div class="audit"><b>${esc(c.verdict)}</b> — ${esc(c.claim)}${c.evidence_for?`<div><i>for:</i> ${esc(c.evidence_for)}</div>`:''}${c.evidence_against?`<div><i>against:</i> ${esc(c.evidence_against)}</div>`:''}</div>`),
    ...au.missed.map(m=>`<div class="audit"><b>missed</b> — should be ${esc(m.rung||'?')}<div><i>evidence:</i> ${esc(m.evidence||'')}</div>${m.detector_fix?`<div><i>suggested fix:</i> ${esc(m.detector_fix)}</div>`:''}</div>`)].join('');
  return `<h5>Can the tool see it? <span class="muted" style="text-transform:none;letter-spacing:0">one laptop, AI audit</span></h5>
  <p><span class="vb ${t.verdict}">${esc(VERD[t.verdict][0])}</span> <span class="cb ${t.cost}">${esc(COST[t.cost][0])}</span> → <b>${esc(actionOf(t))}</b></p>
  <p>${esc(t.note)}</p><p class="small"><b>Needs to read:</b> ${esc(t.reads)}</p>
  ${ev?`<details><summary>The audit's evidence (${au.claims.length+au.missed.length})</summary>${ev}</details>`:''}`}

/* ---------- Open questions ---------- */
(function(){
  const kinds=[...new Set(D.questions.map(q=>q.kind))];
  const render=k=>D.disciplines.map(d=>{const qs=D.questions.filter(q=>q.discipline===d.id&&(!k||q.kind===k));if(!qs.length)return'';
    return `<h3 style="margin-top:20px">${esc(d.name)} <span class="muted small">${qs.length}</span></h3>`+qs.map(q=>`<div class="q"><span class="kind ${q.kind.split(' ')[0]}">${esc(q.kind)}</span>${linkify(q.text)}</div>`).join('')}).join('');
  const el=document.getElementById('t-questions');
  el.innerHTML=`<h2>Open questions</h2><p class="lead">What the drafters couldn't settle from your rulings. These are yours (and the second reviewer's) to decide; nothing here is applied until you do.</p>
  <div class="filters"><button class="on" data-k="">All ${D.questions.length}</button>${kinds.map(k=>`<button data-k="${esc(k)}">${esc(k)} ${D.questions.filter(q=>q.kind===k).length}</button>`).join('')}</div><div id="ql">${render('')}</div>
  <details><summary>What the checker changed in each draft (${D.checks.length})</summary>${D.checks.map(c=>`<div class="q small"><span class="kind">${esc(DISC[c.discipline].name)}</span>${linkify(c.text)}</div>`).join('')}</details>`;
  el.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{el.querySelectorAll('.filters button').forEach(x=>x.classList.toggle('on',x===b));document.getElementById('ql').innerHTML=render(b.dataset.k)});
})();
function linkify(t){let s=esc(t);const ids=[...Object.keys(P),...Object.keys(C)].sort((a,b)=>b.length-a.length);
  for(const id of ids){if(!s.includes(id))continue;s=s.replace(new RegExp('(^|[^\\w/#-])('+id.replace(/[-]/g,'\\-')+')(?![\\w-])','g'),(m,a,b)=>`${a}<a href="#${P[b]?'p':'c'}/${b}">${b}</a>`)}return s}

/* ---------- v13 → v15 ---------- */
(function(){
  const el=document.getElementById('t-v13'),by={};D.crosswalk.forEach(x=>(by[x.v13]=by[x.v13]||[]).push(x));
  const seen=new Set(),tgt=to=>P[to]?pchip(to):C[to]?cchip(to):`<span class="chip">${esc(to||'dropped')}</span>`;
  const rows=(id,g)=>{seen.add(id);const xs=by[id]||[];if(!xs.length)return `<tr class="lost" data-g="${g}" data-s="${esc(id.toLowerCase())}"><td><code>${esc(id)}</code></td><td><b style="color:var(--bad)">not mapped</b></td><td class="small muted">No crosswalk entry: check whether v15 dropped it on purpose.</td><td></td></tr>`;
    return xs.map(x=>`<tr data-g="${g}" data-s="${esc((id+' '+(x.to||'')+' '+(x.why||'')).toLowerCase())}"><td><code>${esc(id)}</code></td><td>${tgt(x.to)}</td><td class="small">${esc(x.why||'')}</td><td class="small muted">${esc(DISC[x.discipline].name)}</td></tr>`).join('')};
  let body='',lost=0;
  for(const vp of D.v13){const ids=[vp.id,...vp.cues],miss=ids.filter(i=>!by[i]).length;lost+=miss;
    body+=`<tr class="v13h" data-g="${vp.id}" data-s="${esc((vp.id+' '+vp.question+' '+vp.badge).toLowerCase())}"><td colspan="4"><code>${esc(vp.id)}</code> ${esc(vp.question)} <span class="muted small">· ${esc(vp.badge)} · ${vp.cues.length} cues</span>${miss?` <b style="color:var(--bad)">· ${miss} not mapped</b>`:''}</td></tr>`+ids.map(i=>rows(i,vp.id)).join('')}
  const rest=Object.keys(by).filter(i=>!seen.has(i));
  if(rest.length)body+=`<tr class="v13h" data-g="_other" data-s="other"><td colspan="4">Other v13 ids in the crosswalk <span class="muted small">(not a practice or cue in v13's practices.yaml)</span></td></tr>`+rest.map(i=>rows(i,'_other')).join('');
  el.innerHTML=`<h2>Where every v13 practice and cue went</h2><p class="lead">Grouped by the old ${D.v13.length} practices. Each row is one v13 practice or cue, and the v15 practice or challenge that took it over, with the reason. Red rows went nowhere.</p>
  <div class="filters"><input class="f" id="vf" placeholder="Filter: a v13 practice (context-9), a cue (ctx-one-thread) or a word">${lost?`<button id="vlost">Only what went nowhere (${lost})</button>`:'<span class="chip" style="cursor:default;color:var(--ok)">✓ every v13 practice and cue is mapped</span>'}</div><p class="small muted" id="vc"></p>
  <table><thead><tr><th>v13</th><th>Now in v15</th><th>Why</th><th>Discipline</th></tr></thead><tbody id="vt">${body}</tbody></table>`;
  const f=document.getElementById('vf'),lb=document.getElementById('vlost');let onlyLost=false;
  const apply=()=>{const q=f.value.toLowerCase().trim();const heads=[...document.querySelectorAll('#vt tr.v13h')];const hit=new Set(heads.filter(h=>q&&h.dataset.s.includes(q)).map(h=>h.dataset.g));let n=0;
    document.querySelectorAll('#vt tr:not(.v13h)').forEach(r=>{const ok=(!q||hit.has(r.dataset.g)||r.dataset.s.includes(q))&&(!onlyLost||r.classList.contains('lost'));r.style.display=ok?'':'none';if(ok)n++});
    heads.forEach(h=>h.style.display=[...document.querySelectorAll(`#vt tr[data-g="${h.dataset.g}"]:not(.v13h)`)].some(r=>r.style.display!=='none')?'':'none');
    document.getElementById('vc').textContent=`${n} of ${D.crosswalk.length+lost} rows`};
  f.oninput=apply;if(lb)lb.onclick=()=>{onlyLost=!onlyLost;lb.classList.toggle('on',onlyLost);apply()};apply();
})();

/* ---------- Pages to write ---------- */
(function(){
  const el=document.getElementById('t-pages');
  el.innerHTML=`<h2>Playbook pages to write or extend</h2><p class="lead">From the coverage read of every playbook page: what a recommendation would point to but doesn't exist yet. Within each discipline, the most-needed come first.</p>`+
  D.disciplines.map(d=>{const ps=D.pages.filter(p=>p.discipline===d.id);if(!ps.length)return'';return `<h3 style="margin-top:22px">${esc(d.name)} <span class="muted small">${ps.length}</span></h3>`+ps.map(p=>`<div class="card" style="margin-bottom:10px"><b>${esc(p.title)}</b>
    <p class="small" style="margin:6px 0">${esc(p.what_it_teaches)}</p><p class="small muted" style="margin:6px 0"><b>Why:</b> ${esc(p.why_it_matters)}</p>
    <div class="chips">${(p.practices||[]).map(pchip).join('')}${(p.rungs||[]).map(r=>`<span class="chip">${esc(r)}</span>`).join('')}</div>
    <p class="small muted" style="margin:8px 0 0"><b>Closest today:</b> ${esc(p.closest_existing||'')}</p></div>`).join('')}).join('');
})();

/* ---------- Graph explorer ---------- */
(function(){
  const el=document.getElementById('t-graph');
  el.innerHTML=`<div class="gbar"><input id="gq" class="f" style="width:210px" placeholder="Find a node… (Enter)" autocomplete="off">
    <span class="small muted">Colour</span><span class="seg" id="gcol"><button data-c="disc" class="on">Discipline</button><button data-c="verdict">Tool result</button><button data-c="cost">Cost</button></span>
    <label><input type="checkbox" id="gpages"> Learning pages</label><label><input type="checkbox" id="gv13"> v13 items</label>
    <button class="gbtn" id="gfit">Reset view</button><span class="small muted" id="gcount"></span></div>
    <div class="gwrap" id="gwrap"><canvas id="gc"></canvas><div id="gtip"></div><div id="ginfo"></div><div id="glegend"></div>
    <div id="ghelp" class="small muted">drag empty space to pan · scroll to zoom · drag a node to pin it · click a node to focus · click empty space to clear</div></div>`;
  const DCOL={context:'#5258FB',prompt:'#9333EA',verification:'#039855',harness:'#CA8504',orchestration:'#E31B54'};
  const VCOL={holds:'#12B76A',partly:'#EAAA08',wrong:'#F04438','cant-see':'#98A2B3'},CCOL={free:'#12B76A','cheap-ai':'#EAAA08',call:'#F04438'};
  const mix=(h,t)=>{const n=parseInt(h.slice(1),16),r=n>>16,g=n>>8&255,b=n&255,m=c=>Math.round(c+(255-c)*t);return `rgb(${m(r)},${m(g)},${m(b)})`};
  const nodes=[],byId={},edges=[],add=n=>(byId[n.id]=n,nodes.push(n),n);
  D.disciplines.forEach(d=>add({id:'d:'+d.id,type:'disc',label:d.name,disc:d.id}));
  Object.values(C).forEach(c=>add({id:'c:'+c.id,type:'ch',key:c.id,disc:c.discipline,label:c.term&&c.term!=='none'?c.term:c.name,sub:c.name}));
  Object.values(P).forEach(p=>add({id:'p:'+p.id,type:'pr',key:p.id,disc:p.discipline,label:p.name}));
  D.disciplines.forEach(d=>{d.challenges.forEach(c=>edges.push({a:'d:'+d.id,b:'c:'+c,type:'member'}));d.practices.forEach(x=>edges.push({a:'d:'+d.id,b:'p:'+x,type:'member'}))});
  const seen=new Set(),fix=(pid,cid)=>{if(!P[pid]||!C[cid]||seen.has(pid+'|'+cid))return;seen.add(pid+'|'+cid);edges.push({a:'p:'+pid,b:'c:'+cid,type:'fixes'})};
  Object.values(P).forEach(p=>p.all_handles.forEach(c=>fix(p.id,c)));Object.values(C).forEach(c=>c.all_handlers.forEach(x=>fix(x,c.id)));
  Object.values(P).forEach(p=>(p.learn||[]).forEach(l=>{if(!l.ref)return;const id='l:'+l.ref;if(!byId[id])add({id,type:'page',opt:'pages',label:l.title||l.ref,ref:l.ref,kind:l.kind});edges.push({a:'p:'+p.id,b:id,type:'taught',opt:'pages'})}));
  D.crosswalk.forEach(x=>{const to=P[x.to]?'p:'+x.to:C[x.to]?'c:'+x.to:null;if(!to)return;const id='v:'+x.v13;if(!byId[id])add({id,type:'v13',opt:'v13',label:x.v13,why:x.why});edges.push({a:id,b:to,type:'v13',opt:'v13'})});
  const show={pages:false,v13:false};let colour='disc',focus=null,hover=null,matches=null,running=false,alpha=1,drag=null,pan=null,moved=0;
  const act=n=>!n.opt||show[n.opt],actE=()=>edges.filter(e=>(!e.opt||show[e.opt])&&act(byId[e.a])&&act(byId[e.b]));
  let AE=actE(),AN=nodes.filter(act),nb={};
  function rebuild(){AE=actE();AN=nodes.filter(act).sort((a,b)=>(a.type==='disc')-(b.type==='disc'));nb={};AN.forEach(n=>{nb[n.id]=new Set();n.deg=0});AE.forEach(e=>{nb[e.a].add(e.b);nb[e.b].add(e.a);if(e.type!=='member'){byId[e.a].deg++;byId[e.b].deg++}});
    AN.forEach(n=>n.r=n.type==='disc'?20:n.type==='pr'?6+Math.min(7,n.deg*.55):n.type==='ch'?4.5+Math.min(6,n.deg*.5):n.type==='page'?4+Math.min(4,n.deg*.4):3);
    document.getElementById('gcount').textContent=`${AN.length} nodes · ${AE.length} links`;legend()}
  const R0=540;D.disciplines.forEach((d,i)=>{const a=-Math.PI/2+i*2*Math.PI/D.disciplines.length,n=byId['d:'+d.id];n.x=n.fx=Math.cos(a)*R0;n.y=n.fy=Math.sin(a)*R0});
  let seed=7;const rnd=()=>(seed=(seed*16807)%2147483647)/2147483647-.5;
  nodes.forEach(n=>{if(n.type==='disc')return;const d=byId['d:'+n.disc];n.x=(d?d.x*.7:0)+rnd()*220;n.y=(d?d.y*.7:0)+rnd()*220;n.vx=n.vy=0});
  function placeOpt(){nodes.forEach(n=>{if(!n.opt||n.placed)return;const e=edges.find(e=>e.a===n.id||e.b===n.id),o=e&&byId[e.a===n.id?e.b:e.a];n.x=(o?o.x:0)+rnd()*60;n.y=(o?o.y:0)+rnd()*60;n.vx=n.vy=0;n.placed=true})}
  const LEN={member:150,fixes:75,cross:170,taught:45,v13:35},STR={member:.04,fixes:.2,cross:.03,taught:.35,v13:.4};
  const kind=e=>e.type==='fixes'&&byId[e.a].disc!==byId[e.b].disc?'cross':e.type;
  function tick(){const A=AN;for(let i=0;i<A.length;i++){const a=A[i];for(let j=i+1;j<A.length;j++){const b=A[j];let dx=b.x-a.x,dy=b.y-a.y,d2=dx*dx+dy*dy;if(d2>250000)continue;if(d2<1){dx=1;d2=1}
      const d=Math.sqrt(d2),s=(a.type==='disc'||b.type==='disc'?3000:a.opt||b.opt?160:700)*alpha/d2;let px=dx*s,py=dy*s;
      const min=a.r+b.r+(a.type==='disc'||b.type==='disc'?26:9);if(d<min){const o=(min-d)/d*.5;px+=dx*o;py+=dy*o}
      a.vx-=px;a.vy-=py;b.vx+=px;b.vy+=py}}
    for(const e of AE){const a=byId[e.a],b=byId[e.b],t=kind(e);let dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||1;const f=(d-LEN[t])/d*STR[t]*alpha*.5;a.vx+=dx*f;a.vy+=dy*f;b.vx-=dx*f;b.vy-=dy*f}
    for(const n of A){if(n.fx!=null){n.x=n.fx;n.y=n.fy;n.vx=n.vy=0;continue}n.vx+=-n.x*.001*alpha;n.vy+=-n.y*.001*alpha;n.vx*=.6;n.vy*=.6;n.x+=n.vx;n.y+=n.vy}
    alpha=Math.max(alpha*.985,.001)}
  const cv=document.getElementById('gc'),ctx=cv.getContext('2d'),tip=document.getElementById('gtip'),info=document.getElementById('ginfo');let W=0,H=0,dpr=1;const view={x:0,y:0,k:.85};
  function size(){const r=cv.getBoundingClientRect();if(!r.width)return;dpr=devicePixelRatio||1;W=r.width;H=r.height;cv.width=W*dpr;cv.height=H*dpr}
  new ResizeObserver(size).observe(document.getElementById('gwrap'));
  const toS=n=>[W/2+view.x+n.x*view.k,H/2+view.y+n.y*view.k],toW=(sx,sy)=>[(sx-W/2-view.x)/view.k,(sy-H/2-view.y)/view.k];
  function colOf(n){if(n.type==='disc')return DCOL[n.disc];if(n.type==='page')return '#667085';if(n.type==='v13')return '#B9BFCB';
    if(colour==='disc')return n.type==='ch'?mix(DCOL[n.disc],.45):DCOL[n.disc];
    if(n.type==='ch')return '#D0D5DD';const t=P[n.key].tool;return t?(colour==='verdict'?VCOL[t.verdict]:CCOL[t.cost]):'#D0D5DD'}
  function lit(){const id=focus||hover;if(id&&nb[id])return new Set([id,...nb[id]]);return matches}
  function draw(){if(!W)size();ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,W,H);const L=lit();
    ctx.save();ctx.translate(W/2+view.x,H/2+view.y);ctx.scale(view.k,view.k);
    for(const e of AE){const a=byId[e.a],b=byId[e.b],on=!L||(L.has(e.a)&&L.has(e.b)&&(!(focus||hover)||e.a===(focus||hover)||e.b===(focus||hover)));
      ctx.globalAlpha=on?(e.type==='member'?.16:e.type==='fixes'?(!L&&kind(e)==='cross'?.13:.5):.35):.035;ctx.strokeStyle=e.type==='fixes'?'#5258FB':'#98A2B3';ctx.lineWidth=(e.type==='fixes'?1.3:.9)/view.k;
      ctx.setLineDash(e.type==='taught'||e.type==='v13'?[4/view.k,3/view.k]:[]);ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke()}
    ctx.setLineDash([]);
    for(const n of AN){const on=!L||L.has(n.id);ctx.globalAlpha=on?1:.13;ctx.fillStyle=colOf(n);const r=n.r;ctx.beginPath();
      if(n.type==='pr'){const s=r*1.7;ctx.roundRect?ctx.roundRect(n.x-s/2,n.y-s/2,s,s,3):ctx.rect(n.x-s/2,n.y-s/2,s,s)}
      else if(n.type==='page'){ctx.moveTo(n.x,n.y-r*1.3);ctx.lineTo(n.x+r*1.3,n.y);ctx.lineTo(n.x,n.y+r*1.3);ctx.lineTo(n.x-r*1.3,n.y);ctx.closePath()}
      else ctx.arc(n.x,n.y,r,0,7);
      ctx.fill();ctx.lineWidth=(n.type==='disc'?3:1.2)/view.k;ctx.strokeStyle='#fff';ctx.stroke();
      if(n.id===focus){ctx.lineWidth=3/view.k;ctx.strokeStyle='#15172F';ctx.stroke()}}
    ctx.restore();ctx.globalAlpha=1;
    const showAll=view.k>=1.6,placed=[],fid=focus||hover;
    const cand=AN.filter(n=>{const isD=n.type==='disc',on=L?L.has(n.id):false;if(L&&!on&&!isD)return false;return isD||on||(showAll&&n.type!=='v13')||n.id===hover})
      .sort((a,b)=>(b.id===fid)-(a.id===fid)||(b.type==='disc')-(a.type==='disc')||b.deg-a.deg);
    ctx.textBaseline='middle';
    for(const n of cand){const isD=n.type==='disc',[sx,sy]=toS(n),t=n.label.length>44?n.label.slice(0,42)+'…':n.label;
      ctx.font=(isD||n.id===fid?'600 13px':'500 11.5px')+' Inter,-apple-system,sans-serif';const w=ctx.measureText(t).width,g=n.r*view.k+5;
      const opts=[[sx+g,sy],[sx-g-w,sy],[sx-w/2,sy-g-8],[sx-w/2,sy+g+8]];let pos=null;
      for(const [x,y] of opts){const r=[x-2,y-8,w+4,16];if(x<4||x+w>W-4)continue;if(!placed.some(q=>r[0]<q[0]+q[2]&&q[0]<r[0]+r[2]&&r[1]<q[1]+q[3]&&q[1]<r[1]+r[3])){pos=[x,y];placed.push(r);break}}
      if(!pos){if(n.id!==fid&&!isD)continue;pos=opts.find(([x])=>x>=4&&x+w<=W-4)||opts[0]}
      ctx.globalAlpha=L&&!L.has(n.id)?.35:1;ctx.lineWidth=4;ctx.strokeStyle='rgba(255,255,255,.95)';ctx.strokeText(t,pos[0],pos[1]);ctx.fillStyle=isD?'#15172F':n.id===fid?'#15172F':'#344054';ctx.fillText(t,pos[0],pos[1])}
    ctx.globalAlpha=1}
  function legend(){const sh=(c,shape)=>`<i style="background:${c};${shape==='sq'?'border-radius:2px':shape==='dia'?'transform:rotate(45deg) scale(.8)':'border-radius:50%'}"></i>`;
    let h=`<b class="small">Shapes</b><span>${sh('#344054','sq')} practice (what someone does)</span><span>${sh('#98A2B3')} challenge (what goes wrong)</span><span>${sh('#5258FB')} discipline</span>${show.pages?`<span>${sh('#667085','dia')} learning page</span>`:''}${show.v13?`<span>${sh('#B9BFCB')} v13 item</span>`:''}<span><i style="height:2px;background:#5258FB"></i> fixes</span>`;
    h+=`<b class="small" style="margin-top:4px">Colour</b>`+(colour==='disc'?D.disciplines.map(d=>`<span>${sh(DCOL[d.id])} ${esc(d.name)}</span>`).join('')+'<span class="muted">lighter = challenge</span>':
      colour==='verdict'?Object.entries(VCOL).map(([k,c])=>`<span>${sh(c,'sq')} ${esc(VERD[k][0])}</span>`).join('')+'<span class="muted">practices only · one laptop, AI audit</span>':
      Object.entries(CCOL).map(([k,c])=>`<span>${sh(c,'sq')} ${esc(COST[k][0])}</span>`).join('')+'<span class="muted">practices only · a judgement, not measured</span>');
    document.getElementById('glegend').innerHTML=h}
  function hit(sx,sy){const [x,y]=toW(sx,sy);for(let i=AN.length-1;i>=0;i--){const n=AN[i],r=n.r*(n.type==='pr'?1.1:1)+4/view.k;if((n.x-x)**2+(n.y-y)**2<=r*r)return n}return null}
  const pos=e=>{const r=cv.getBoundingClientRect();return [e.clientX-r.left,e.clientY-r.top]};
  cv.addEventListener('pointerdown',e=>{anim=0;const [sx,sy]=pos(e),n=hit(sx,sy);moved=0;cv.setPointerCapture(e.pointerId);if(n){drag=n;n.wasFixed=n.fx!=null}else pan=[sx,sy,view.x,view.y];cv.classList.add('drag')});
  cv.addEventListener('pointermove',e=>{const [sx,sy]=pos(e);if(drag){const [x,y]=toW(sx,sy);moved++;drag.fx=x;drag.fy=y;alpha=Math.max(alpha,.25);return}
    if(pan){moved+=Math.abs(sx-pan[0])+Math.abs(sy-pan[1])>3?1:0;view.x=pan[2]+sx-pan[0];view.y=pan[3]+sy-pan[1];return}
    const n=hit(sx,sy);hover=n?n.id:null;cv.classList.toggle('pt',!!n);
    if(n){tip.style.display='block';tip.innerHTML=`${esc(n.label)}<small>${{disc:'discipline',ch:'challenge',pr:'practice',page:'learning page',v13:'v13 item'}[n.type]}${n.type==='pr'&&P[n.key].tool?' · '+esc(VERD[P[n.key].tool.verdict][0])+' · '+esc(COST[P[n.key].tool.cost][0]):''} · ${nb[n.id].size} links</small>`;
      tip.style.left=Math.min(sx+14,W-330)+'px';tip.style.top=(sy+14)+'px'}else tip.style.display='none'});
  cv.addEventListener('pointerup',e=>{cv.classList.remove('drag');const n=drag,wasPan=pan;drag=null;pan=null;if(moved>2){if(n&&!n.wasFixed&&n.type!=='disc'){/* stays pinned where dropped */}return}
    if(n){if(!n.wasFixed&&n.type!=='disc'){n.fx=n.fy=null}select(n)}else if(wasPan){focus=null;info.style.display='none';if(document.getElementById('drawer').classList.contains('on'))location.hash='graph'}});
  cv.addEventListener('pointerleave',()=>{hover=null;tip.style.display='none'});
  cv.addEventListener('wheel',e=>{e.preventDefault();anim=0;const [sx,sy]=pos(e),[wx,wy]=toW(sx,sy),k=Math.min(5,Math.max(.15,view.k*Math.exp(-e.deltaY*.0015)));view.k=k;view.x=sx-W/2-wx*k;view.y=sy-H/2-wy*k},{passive:false});
  let anim=0;function glide(){anim=50}
  function select(n){focus=n.id;glide();if(n.type==='pr'||n.type==='ch'){info.style.display='none';location.hash=(n.type==='pr'?'p/':'c/')+n.key;return}
    if(document.getElementById('drawer').classList.contains('on'))location.hash='graph';
    const ns=[...nb[n.id]].map(i=>byId[i]);let h=`<b>${esc(n.label)}</b><div class="small muted">${{disc:'discipline',page:'learning page',v13:'v13 item'}[n.type]} · ${ns.length} links</div>`;
    if(n.type==='disc'){const d=DISC[n.disc];h+=`<p class="small" style="margin:6px 0">${esc(d.question)}</p><div class="small">${d.practices.length} practices · ${d.challenges.length} challenges</div>`}
    if(n.type==='page')h+=`<p class="small" style="margin:6px 0">Taught to: ${ns.map(x=>esc(x.label)).join(' · ')}</p>${n.ref.startsWith('/')?`<a href="${D.playbook}${esc(n.ref)}" target="_blank">Open ${esc(n.ref)} ↗</a>`:`<span class="small muted">${esc(n.kind)} · ${esc(n.ref)}</span>`}`;
    if(n.type==='v13')h+=`<p class="small" style="margin:6px 0">Now: ${ns.map(x=>esc(x.label)).join(' · ')}</p><p class="small muted">${esc(n.why||'')}</p>`;
    info.innerHTML=h;info.style.display='block'}
  window.graphFocus=id=>{if(byId[id]&&act(byId[id])){if(focus!==id)glide();focus=id}};
  window.graphNodeAt=id=>byId[id]&&toS(byId[id]); // screen position of a node: used by the click test
  function fit(){if(!W)size();const xs=AN.map(n=>n.x),ys=AN.map(n=>n.y),x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
    view.k=Math.min(2,.9*Math.min(W/(x1-x0+80),H/(y1-y0+80)));view.x=-(x0+x1)/2*view.k;view.y=-(y0+y1)/2*view.k}
  document.getElementById('gfit').onclick=()=>{AN.forEach(n=>{if(n.type!=='disc')n.fx=n.fy=null});focus=null;matches=null;info.style.display='none';alpha=.6;setTimeout(fit,700)};
  el.querySelectorAll('#gcol button').forEach(b=>b.onclick=()=>{el.querySelectorAll('#gcol button').forEach(x=>x.classList.toggle('on',x===b));colour=b.dataset.c;legend()});
  ['pages','v13'].forEach(k=>document.getElementById('g'+k).onchange=e=>{show[k]=e.target.checked;placeOpt();rebuild();alpha=Math.max(alpha,.7)});
  const gq=document.getElementById('gq');gq.oninput=()=>{const q=gq.value.toLowerCase().trim();matches=q?new Set(AN.filter(n=>n.label.toLowerCase().includes(q)||(n.key||'').includes(q)).map(n=>n.id)):null};
  gq.onkeydown=e=>{if(e.key==='Enter'&&matches&&matches.size){const n=byId[[...matches][0]];graphFocus(n.id);select(n)}if(e.key==='Escape'){gq.value='';matches=null}};
  let fitted=false;
  function step(){if(!anim||!focus||!nb[focus])return;anim--;const ns=[focus,...nb[focus]].map(i=>byId[i]),xs=ns.map(n=>n.x),ys=ns.map(n=>n.y),x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
    const k=Math.max(.35,Math.min(1.7,.85*Math.min(W/(x1-x0+260),H/(y1-y0+120)))),tx=-(x0+x1)/2*k,ty=-(y0+y1)/2*k;view.k+=(k-view.k)*.15;view.x+=(tx-view.x)*.15;view.y+=(ty-view.y)*.15}
  function loop(){if(!running)return;if(alpha>.002){tick();tick()}step();draw();requestAnimationFrame(loop)}
  window.onTabShown=k=>{if(k==='graph'){if(!running){running=true;size();if(!fitted){for(let i=0;i<260;i++)tick();fit();fitted=true}requestAnimationFrame(loop)}}else running=false};
  rebuild();
})();

/* ---------- Drawer ---------- */
const dr=document.getElementById('drawer');
function openPractice(id){const p=P[id];if(!p)return;const h=p.health;
  const lad=['L2','L3','L4'].map(r=>`<div><b>${r}</b><span>${p.ladder&&p.ladder[r]?esc(p.ladder[r]):'<span class="muted">no such level for this practice</span>'}${h.playbook[r]==='n/a'?'':`<div class="small" style="margin-top:6px">Playbook: <b class="${h.playbook[r]}" style="color:${h.playbook[r]==='taught'?'var(--ok)':h.playbook[r]==='missing'?'var(--bad)':'var(--warn)'}">${h.playbook[r]}</b></div><ul>${(p.coverage[r]||[]).map(e=>`<li>${String(e.page||'').startsWith('/')?link(e.page):esc(e.page)} · ${esc(e.section||'')} <i>(${esc(e.status)})</i></li>`).join('')}</ul>`}</span></div>`).join('');
  dr.innerHTML=`<button class="x" onclick="closeDrawer()">Close ✕ <span class="muted small">Esc</span></button><div class="eyebrow" style="color:var(--p)">${esc(DISC[p.discipline].name)} · practice${p.audience==='builds-agents'?' · for people who build agents':''}</div>
  <h2>${esc(p.name)}</h2><div>${gb(p.grade)} ${p.term&&p.term!=='none'?`<b>${esc(p.term)}</b>`:'<span class="muted">no name in the field</span>'} ${p.source?' · '+link(p.source):''}</div>
  ${toolBlock(p)}
  <h5>What they do</h5><p>${esc(p.what_you_do)}</p>
  <h5>Fixes</h5><div class="chips">${p.all_handles.map(cchip).join('')||'<span class="muted">nothing</span>'}</div>
  <h5>The ladder</h5><div class="ladder"><div><b>L1</b><span class="muted">doesn't do it: the challenge happens and isn't handled</span></div>${lad}</div>
  ${p.coverage_note?`<p class="small muted">${esc(p.coverage_note)}</p>`:''}
  <h5>On a call</h5><p>${esc(p.call_evidence)||'<span style="color:var(--bad)">no question yet</span>'}</p>
  <h5>On the laptop</h5><p>${esc(p.laptop_evidence)||'<span style="color:var(--bad)">nothing read</span>'}</p>
  <h5>How it's done today (techniques — these change monthly)</h5>${(p.techniques||[]).map(t=>`<div class="tech"><b>${esc(t.tool)}</b> — ${esc(t.how)}<div class="ls">${t.laptop_signal?'🔎 '+esc(t.laptop_signal):'not visible on the laptop'}</div></div>`).join('')}
  <h5>Where to learn it</h5><div>${(p.learn||[]).map(l=>`<div class="small">${esc(l.kind)} · ${link(l.ref)} ${esc(l.title||'')}</div>`).join('')||'<span class="muted">nothing yet</span>'}</div>
  <h5>Your rulings it rests on</h5>${(p.rulings||[]).map(r=>`<div class="q small">${esc(r)}</div>`).join('')||'<span class="muted">none cited</span>'}
  <h5>Took over from v13</h5>${p.v13.map(x=>`<div class="small"><code>${esc(x.v13)}</code> — ${esc(x.why||'')}</div>`).join('')||'<span class="muted">nothing (new in v15)</span>'}
  ${openQs(id)}`;show()}
function openChallenge(id){const c=C[id];if(!c)return;
  dr.innerHTML=`<button class="x" onclick="closeDrawer()">Close ✕ <span class="muted small">Esc</span></button><div class="eyebrow" style="color:var(--p)">${esc(DISC[c.discipline].name)} · challenge</div>
  <h2>${esc(c.term&&c.term!=='none'?c.term:c.name)}</h2><div>${gb(c.grade)} ${esc(c.name)} ${c.source?' · '+link(c.source):''}</div>
  <h5>What goes wrong</h5><p>${esc(c.what_goes_wrong)}</p>
  <h5>Fixed by</h5><div class="chips">${c.all_handlers.map(pchip).join('')||'<span style="color:var(--bad)">nothing — no practice fixes this</span>'}</div>
  <h5>Ask on a call</h5><p>${esc(c.ask)||'<span class="muted">no question</span>'}</p>
  <h5>Signs on a call</h5><ul>${(c.call_signs||[]).map(s=>`<li>${esc(s)}</li>`).join('')}</ul>
  <h5>Signs on the laptop</h5><ul>${(c.laptop_signs||[]).map(s=>`<li>${esc(s)}</li>`).join('')}</ul><p class="small muted">Detector: ${c.detector?`<code>${esc(c.detector)}</code>`:'none yet'}</p>
  <h5>Playbook</h5><p class="small">Explained in: ${(c.explained_in||[]).map(link).join(', ')||'<span style="color:var(--bad)">nowhere</span>'}<br>How-to in: ${(c.how_to_in||[]).map(link).join(', ')||'<span style="color:var(--bad)">nowhere</span>'}</p>
  ${c.v13.length?`<h5>Took over from v13</h5>${c.v13.map(x=>`<div class="small"><code>${esc(x.v13)}</code> — ${esc(x.why||'')}</div>`).join('')}`:''}
  ${openQs(id)}`;show()}
function openQs(id){const qs=D.questions.filter(q=>q.text.includes(id));return qs.length?`<h5>Open questions about it</h5>${qs.map(q=>`<div class="q small"><span class="kind ${q.kind.split(' ')[0]}">${esc(q.kind)}</span>${linkify(q.text)}</div>`).join('')}`:''}
function show(){dr.classList.add('on');document.body.classList.add('dopen');dr.setAttribute('aria-hidden','false');dr.scrollTop=0;markSel()}
function hide(){dr.classList.remove('on');document.body.classList.remove('dopen');dr.setAttribute('aria-hidden','true');markSel()}
function closeDrawer(){location.hash=lastTab==='map'&&mapDisc?'d/'+mapDisc:lastTab}
function markSel(){const h=decodeURIComponent(location.hash.slice(1));document.querySelectorAll('.item.sel').forEach(x=>x.classList.remove('sel'));
  const [k,id]=h.split('/');if(dr.classList.contains('on')&&id)document.querySelectorAll(`.item[data-${k}="${id}"]`).forEach(x=>x.classList.add('sel'))}
const setHH=()=>document.documentElement.style.setProperty('--hh',document.querySelector('header').offsetHeight+'px');setHH();addEventListener('resize',setHH);

/* ---------- Routing ---------- */
let lastTab='can',mapDisc='';
function route(){const h=decodeURIComponent(location.hash.slice(1))||lastTab;const [k,id]=h.split('/');
  if(k==='p'||k==='c'){(k==='p'?openPractice:openChallenge)(id);if(!document.querySelector('section.tab.on'))tab(lastTab);if(lastTab==='graph'&&window.graphFocus)graphFocus(k+':'+id);return}
  hide();if(k==='d'){tab('map');showDisc(id);return}
  if(k==='map')showDisc('');
  tab(TABS.some(t=>t[0]===k)?k:'can')}
function showDisc(id){const changed=mapDisc!==id;mapDisc=id||'';document.querySelectorAll('#t-map .disc').forEach(d=>d.style.display=!mapDisc||d.id==='d-'+mapDisc?'':'none');
  document.querySelectorAll('#t-map .dchips .chip').forEach(c=>c.classList.toggle('on',(c.dataset.d||'')===mapDisc));if(changed)scrollTo(0,0)}
function tab(k){if(k!==lastTab)scrollTo(0,0);lastTab=k;setTimeout(()=>window.onTabShown&&onTabShown(k),0);document.querySelectorAll('section.tab').forEach(s=>s.classList.toggle('on',s.id==='t-'+k));document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('on',a.dataset.t===k))}
window.addEventListener('hashchange',route);route();
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&dr.classList.contains('on'))location.hash=lastTab;
  if(e.key==='/'&&document.activeElement!==qi){e.preventDefault();qi.focus()}});

/* ---------- Search ---------- */
const qi=document.getElementById('q'),res=document.getElementById('results');
const IDX=[...Object.values(P).map(p=>({k:'p',id:p.id,t:p.name,s:[p.id,p.name,p.term,p.what_you_do,...(p.v13||[]).map(x=>x.v13)].join(' ').toLowerCase(),d:'practice · '+DISC[p.discipline].name})),
  ...Object.values(C).map(c=>({k:'c',id:c.id,t:c.term&&c.term!=='none'?c.term+' — '+c.name:c.name,s:[c.id,c.name,c.term,c.what_goes_wrong,...(c.v13||[]).map(x=>x.v13)].join(' ').toLowerCase(),d:'challenge · '+DISC[c.discipline].name}))];
qi.addEventListener('input',()=>{const q=qi.value.toLowerCase().trim();if(!q){res.style.display='none';return}
  const hits=IDX.map(x=>({...x,score:(x.id.includes(q)||x.t.toLowerCase().includes(q))?2:x.s.includes(q)?1:0})).filter(x=>x.score).sort((a,b)=>b.score-a.score).slice(0,14);
  res.innerHTML=hits.map((x,i)=>`<a href="#${x.k}/${x.id}" class="${i?'':'on'}">${esc(x.t)}<br><small>${esc(x.d)}</small></a>`).join('')||'<a class="muted">Nothing found</a>';res.style.display='block'});
qi.addEventListener('keydown',e=>{if(e.key==='Enter'){const a=res.querySelector('a[href]');if(a){location.hash=a.getAttribute('href').slice(1);qi.blur()}}if(e.key==='Escape')qi.blur()});
qi.addEventListener('blur',()=>setTimeout(()=>res.style.display='none',150));
res.addEventListener('click',()=>{res.style.display='none'});
</script></body></html>
"""


def main():
    cmd = (sys.argv[1:] or ["open"])[0]
    if cmd in ("-h", "--help", "help"):
        print(__doc__.strip())
        return
    if cmd == "path":
        print(OUT)
        return
    data = analyse()
    if cmd == "status":
        terminal(data)
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(page(data))
    print(f"{OUT}  ({len(data['practices'])} practices, {len(data['challenges'])} challenges, built {data['built']})")
    if cmd == "open" and sys.platform == "darwin":
        subprocess.run(["open", OUT])


if __name__ == "__main__":
    main()
