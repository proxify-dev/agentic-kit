#!/usr/bin/env python3
"""Render place_level's result as one private HTML page: the suggested level, every check, what the person
built (vs installed), and the proof it tells people apart (made-up profiles + ablation on this machine).

Run: python3 -I render_place.py SCAN.json PLACE.json -o place.html
"""
from __future__ import annotations

import argparse
import copy
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import place_level as pl  # noqa: E402
try:  # the made-up profiles are optional: without them the page skips that table
    import place_level_test as plt  # noqa: E402
except ImportError:
    plt = None

E = html.escape
ICON = {pl.MET: ("✓", "met"), pl.WEAK: ("~", "weak"), pl.NOT: ("✕", "not"), pl.CANT: ("?", "cant")}
# pills built outside f-strings: a backslash inside an f-string needs Python 3.12+
BLOCKS, REPORTS = '<span class="pill ok">can block</span>', '<span class="pill no">reports only</span>'
RIGHT, WRONG = '<span class="pill ok">right</span>', '<span class="pill bad">wrong</span>'
LEVEL_NAME = {0: "The Level 1 checks didn't all pass yet: see which one below", 1: "Uses agents on real work", 2: "Works with discipline", 3: "Sets agents up to know, learn and remember",
              4: "Builds the harness around the agent"}

CSS = """
:root{--p600:#5258FB;--p700:#3C3ED4;--p25:#F5F5FF;--p100:#D5D5FE;--p900:#171559;--midnight:#15172F;
--g50:#F9FAFB;--g100:#F4F5F7;--g200:#EAECF0;--g500:#667085;--g600:#475467;--g700:#344054;
--ok:#039855;--okbg:#ECFDF3;--warn:#B54708;--warnbg:#FEFBF5;--warnline:#FBDEA5;--bad:#B42318;--badbg:#FEF3F2;--mint:#73E2A3}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;font-weight:400;color:var(--midnight);background:#fff;line-height:1.55}
h1,h2,h3{font-family:Poppins,Inter,system-ui,sans-serif;font-weight:700;line-height:1.25;margin:0}
.banner{background:var(--warnbg);border-bottom:1px solid var(--warnline);color:var(--warn);font-weight:500;font-size:14px;text-align:center;padding:10px 24px}
.hero{background:var(--midnight);color:#fff;padding:56px 24px 48px}
.wrap{max-width:1040px;margin:0 auto;padding:0 24px}
.eyebrow{font-weight:600;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--p100)}
.big{display:flex;align-items:flex-end;gap:28px;flex-wrap:wrap;margin-top:12px}
.big .lv{font-family:Poppins,Inter,sans-serif;font-size:112px;line-height:.9;font-weight:700;color:var(--mint)}
.big .what{font-size:26px;font-family:Poppins,Inter,sans-serif;font-weight:600;max-width:560px}
.meta{color:var(--p100);font-size:15px;margin-top:18px}
.ladder{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:36px 0 0}
.step{border-radius:12px;padding:14px 16px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12)}
.step.pass{background:rgba(115,226,163,.12);border-color:rgba(115,226,163,.5)}
.step b{font-family:Poppins,Inter,sans-serif;font-size:20px;display:block}
.step span{font-size:13px;color:var(--p100)}
.step .tick{float:right;font-weight:700;color:var(--mint)}
section{padding:44px 0 8px}
section>h2{font-size:26px;margin-bottom:6px}
.sub{color:var(--g600);font-size:15px;margin:0 0 20px;max-width:820px}
.gate{display:inline-block;font-size:13px;font-weight:600;padding:4px 10px;border-radius:999px;margin:0 0 16px}
.gate.pass{background:var(--okbg);color:var(--ok)} .gate.fail{background:var(--badbg);color:var(--bad)}
.cards{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
.card{border:1px solid var(--g200);border-radius:14px;padding:18px 18px 14px;background:#fff}
.card .top{display:flex;gap:12px;align-items:flex-start}
.badge{flex:0 0 30px;height:30px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:16px}
.badge.met{background:var(--okbg);color:var(--ok)} .badge.weak{background:var(--warnbg);color:var(--warn)}
.badge.not{background:var(--badbg);color:var(--bad)} .badge.cant{background:var(--g100);color:var(--g600)}
.card h3{font-size:16px;font-weight:600}
.q{color:var(--g600);font-size:14px;margin-top:2px}
.val{font-size:15px;margin:12px 0 6px;color:var(--p900);font-weight:500}
.bar{font-size:13px;color:var(--g500)}
.bar b{color:var(--g700);font-weight:600}
.caveat{font-size:13px;color:var(--g600);border-top:1px dashed var(--g200);margin-top:10px;padding-top:8px}
.tdi{font-size:13px;color:var(--p700);margin-top:6px}
.tdi:before{content:"Tool did it? ";font-weight:600}
table{width:100%;border-collapse:collapse;font-size:14px;margin:6px 0 18px}
th{text-align:left;font-weight:600;color:var(--g600);font-size:12px;letter-spacing:.06em;text-transform:uppercase;padding:8px 10px;border-bottom:1px solid var(--g200)}
td{padding:9px 10px;border-bottom:1px solid var(--g100);vertical-align:top}
td.n{font-family:Poppins,Inter,sans-serif;font-weight:700;color:var(--p600);white-space:nowrap}
.pill{display:inline-block;font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px}
.pill.ok{background:var(--okbg);color:var(--ok)} .pill.no{background:var(--g100);color:var(--g600)} .pill.bad{background:var(--badbg);color:var(--bad)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:28px}
.limits li{margin:0 0 8px;font-size:15px}
.next{background:var(--p25);border:1px solid var(--p100);border-radius:14px;padding:20px 22px;margin:8px 0 56px}
.next b{color:var(--p700)}
@media(max-width:820px){.cards,.two,.ladder{grid-template-columns:1fr}.big .lv{font-size:80px}}
"""


def check_card(h: dict) -> str:
    ch, cls = ICON[h["result"]]
    tdi = f'<div class="tdi">{E(h["tool_did_it"])}</div>' if h.get("tool_did_it") else ""
    return f"""<div class="card"><div class="top"><div class="badge {cls}" title="{E(h['result'])}">{ch}</div>
<div><h3>{E(h['guide_line'])}</h3><div class="q">{E(h['question'])}</div></div></div>
<div class="val">{E(h['value'])}</div><div class="bar"><b>Bar:</b> {E(h['bar'])}</div>
<div class="caveat">{E(h['why'])}</div>{tdi}</div>"""


def proof(scan: dict, r: dict) -> tuple[list, list]:
    profiles = []
    for name, want, prof, ex in (plt.CASES if plt else []):
        got = pl.place(plt.profile(scan, **prof), ex)["suggested_level"]
        profiles.append((name, want, got))
    ex = r["extra"]
    e1 = copy.deepcopy(ex)
    for p in e1["plugins"]:
        p["own"] = False
    e1["user_hooks"] = []
    sc2 = copy.deepcopy(scan)
    sc2["habits"]["evidence"]["eval_commands"] = 0
    sc2["habits"]["evidence"].setdefault("headless_by_starter", {})["eval runs"] = 0
    e3 = copy.deepcopy(e1)
    e3["memory"]["own_stores"] = []
    e3["rules"]["repos"] = [dict(x, by_person=0) for x in e3["rules"]["repos"]]
    ablation = [
        ("Nothing — your machine as it is", pl.place(scan, ex)["suggested_level"]),
        ("Your plugins and hooks treated as installed, not written by you", pl.place(scan, e1)["suggested_level"]),
        ("Your evals removed", pl.place(sc2, ex)["suggested_level"]),
        ("Plugins and hooks installed, and no memory or rules of your own", pl.place(scan, e3)["suggested_level"]),
    ]
    return profiles, ablation


def lv(x) -> str:
    """None: too little in the logs to read. 0: read, and the Level 1 checks didn't all pass yet."""
    return f"L{x}" if x else "Below L1" if x == 0 else "not enough to read"


def render(scan: dict, r: dict) -> str:
    level = r["suggested_level"]
    gates = {int(k): v for k, v in r["gates"].items()}
    steps = "".join(
        f'<div class="step {"pass" if gates[n]["passed"] else ""}"><span class="tick">{"✓" if gates[n]["passed"] else ""}</span>'
        f'<b>L{n}</b><span>{E(LEVEL_NAME[n])}</span></div>' for n in (1, 2, 3, 4))
    hero = f"""<div class="hero"><div class="wrap"><div class="eyebrow">Setup snapshot · suggested level</div>
<div class="big"><div class="lv">{E(lv(level))}</div><div class="what">{E(LEVEL_NAME.get(level, ''))}</div></div>
<div class="meta">Read from {E(r['seen'])} · last 30 days · this laptop only</div>
<div class="ladder">{steps}</div></div></div>"""

    sections = []
    for n in (1, 2, 3, 4):
        g = gates[n]
        hs = r["levels"][str(n)]
        met = sum(h["result"] == pl.MET for h in hs)
        sections.append(f"""<section><div class="wrap"><h2>Level {n} — {E(LEVEL_NAME[n])}</h2>
<p class="sub">{met} of {len(hs)} checks met. Passing needs: {E(g['rule'])}.</p>
<span class="gate {'pass' if g['passed'] else 'fail'}">{'Gate passed' if g['passed'] else 'Gate not passed'}</span>
<div class="cards">{''.join(check_card(h) for h in hs)}</div></div></section>""")

    ex = r["extra"]
    own_p = sorted([p for p in ex["plugins"] if p["own"]], key=lambda p: -p["commits"])
    prow = "".join(
        f"<tr><td>{E(p['plugin'])}</td><td class='n'>{p['commits']}</td><td>{E(', '.join(p['hook_events'][:3]) or '—')}</td>"
        f"<td>{BLOCKS if p['hooks_can_block'] else REPORTS}</td></tr>"
        for p in own_p)
    not_own = [p for p in ex["plugins"] if not p["own"]]
    srow = "".join(
        f"<tr><td>{E(m['path'])}</td><td class='n'>{m['files']}</td><td>{m['days_touched']} days</td>"
        f"<td>{'index + ' if m['has_index'] else ''}{m['folders']} folders</td></tr>" for m in ex["memory"]["own_stores"][:6])
    rrow = "".join(
        f"<tr><td>{E(x['repo'])}</td><td class='n'>{x['nested_rule_files']}</td><td>{x['by_person']}</td><td>{x['other_authors']}</td></tr>"
        for x in ex["rules"]["repos"] if x["nested_rule_files"])
    hooks_block = [h for h in ex["user_hooks"] if h["own"] and h["can_block"]]
    from collections import Counter
    sk = Counter(x.get("owner") or ("installed" if x.get("is_symlink") else "own") for x in ex.get("own_skills", []))
    skills_line = (f"{sk.get('own', 0)} you wrote · {sk.get('team', 0)} you co-wrote with others · {sk.get('installed', 0)} "
                   "installed from someone else. A linked skill is followed to where it really lives, and git says who wrote it.")
    built = f"""<section><div class="wrap"><h2>What you built, not installed</h2>
<p class="sub">Only things whose git history is mostly yours count toward Level 3 and Level 4.
Found as you: {E(', '.join(ex['identities']))}. Claude Code's own memory ({ex['memory']['claude_code_default_memory_files']} notes)
and {len(not_own)} plugins with too little history of yours were left out.</p>
<div class="two"><div><h3>Your plugins ({len(own_p)})</h3><table><tr><th>Plugin</th><th>Your commits</th><th>Hooks on</th><th>Hooks</th></tr>{prow}</table>
<h3>Your skills</h3><p class="sub">{E(skills_line)}</p>
<h3>Your own hooks that can block ({len(hooks_block)})</h3><table><tr><th>When</th><th>Script</th></tr>
{''.join(f"<tr><td>{E(h['event'])}</td><td>{E(', '.join(h['scripts']))}</td></tr>" for h in hooks_block)}</table></div>
<div><h3>Your memory stores</h3><table><tr><th>Where</th><th>Notes</th><th>Changed on</th><th>Shape</th></tr>{srow}</table>
<h3>Rules below the repo root</h3><table><tr><th>Repo</th><th>Folder rule files</th><th>Your commits (90d)</th><th>Others</th></tr>{rrow}</table></div></div>
</div></section>"""

    profiles, ablation = proof(scan, r)
    prof_rows = "".join(
        f"<tr><td>{E(n)}</td><td>{E(lv(w))}</td><td>{E(lv(g))}</td><td>{RIGHT if w == g else WRONG}</td></tr>"
        for n, w, g in profiles)
    abl_rows = "".join(f"<tr><td>{E(n)}</td><td class='n'>{E(lv(g))}</td></tr>" for n, g in ablation)
    right = sum(w == g for _, w, g in profiles)
    tells = f"""<section><div class="wrap"><h2>Does it tell people apart?</h2>
<p class="sub">One machine reading L4 proves little. So it was also run on made-up people built from the shape of your scan,
including the trap the coverage study found (someone who installs good tools looks like someone who builds them),
and on your own machine with parts taken away.</p>
<div class="two"><div>{f"<h3>Made-up people: {right} of {len(profiles)} placed right</h3><table><tr><th>Person</th><th>Should be</th><th>Placed</th><th></th></tr>{prof_rows}</table>" if profiles else ""}</div>
<div><h3>Your machine, with parts taken away</h3><table><tr><th>Taken away</th><th>Placed</th></tr>{abl_rows}</table>
<p class="sub">Each thing you built moves the result the way it should: no evals, or tools you only installed, and you're L3.</p></div></div>
</div></section>"""

    limits = """<section><div class="wrap"><h2>What it can't settle yet</h2><ul class="limits">
<li><b>The bars come from very few machines.</b> The made-up people show the checks are shaped right, not that the numbers are right for others.</li>
<li><b>Corrections</b> are still found with an English word list (60–76% agreement with a hand check).</li>
<li><b>"Real ticket", reading the diff, planning done elsewhere</b> leave no trace on the laptop. Those stay in the interview.</li>
<li><b>Evals</b> aren't yet matched to the item that was edited, and "eval" also matches ordinary tests.</li>
<li><b>A hook in settings with no git history</b> counts as yours. One pasted from a blog would still pass.</li>
<li><b>A memory store</b> laid out by someone else's plugin passes on shape alone.</li>
</ul>
<div class="next"><b>Early version.</b> The bars were set on very few machines. If a check looks wrong for you, say so: that is exactly what we need to hear.</div>
</div></section>"""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Suggested level — {E(lv(level))}</title><style>{CSS}</style></head><body>"
            f"<div class='banner'>Private. A suggestion for the interviewer, not a verdict. The interviewer decides.</div>"
            f"{hero}{''.join(sections)}{built}{tells}{limits}</body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("place")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    scan = json.loads(Path(a.scan).read_text())
    r = json.loads(Path(a.place).read_text())
    Path(a.out).write_text(render(scan, r))
    print(a.out)


if __name__ == "__main__":
    main()
