#!/usr/bin/env python3
"""The interviewer's sheet, generated from the same substrate the laptop page uses: per discipline, problem-led
questions, what each rung sounds like, and how to record an answer so the engine reads it like laptop evidence.
Usage: python3 call_sheet.py OUT.html [--laptop RESULT.json]  (with --laptop, questions the laptop already
answered move to the end, so the call spends its time where the laptop is blind)"""
import html
import json
import os
import sys

E = html.escape
HERE = os.path.dirname(os.path.abspath(__file__))


def main(dst, laptop=None):
    S = json.load(open(os.path.join(HERE, "substrate.json")))
    covered = set()
    if laptop:
        r = json.load(open(laptop))
        # the laptop answered a challenge only when it settled it: shows or doesn't show. "Not read" and "can't tell"
        # rows exist for every challenge now, and those questions stay first on the call.
        covered = {e["id"] for e in r["evidence"] if e["kind"] == "challenge" and e.get("seen") is not None
                   and e.get("state") != "not read"}
    parts = []
    for d in S["disciplines"]:
        rows = []
        for cid in d["challenges"]:
            c = S["challenges"][cid]
            if c["grade"] in ("folk",):
                continue
            ladder = []
            for pid in c["all_handlers"][:3]:
                p = S["practices"].get(pid)
                if not p:
                    continue
                steps = " ".join(f"<b>{k}</b> {E(v)}" for k, v in p["ladder"].items() if v)
                ladder.append(f"<li><i>{E(p['name'])}</i> — {steps}</li>")
            known = cid in covered
            rows.append((known, f"""<div class="q{' known' if known else ''}"><div class="h">{E(c['name'])}
<span class="t">{E(c['term'])} · {E(c['grade'])}</span>{' <span class="k">laptop already answered</span>' if known else ''}</div>
<p class="ask">“{E(c['ask'])}”</p><p class="small">Listen for: {E('; '.join(c['call_signs'][:3]))}</p>
<ul class="lad">{''.join(ladder)}</ul></div>"""))
        rows.sort(key=lambda x: x[0])
        parts.append(f"<section><h2>{E(d['name'])}</h2><p class='small'>{E(d['question'])}</p>{''.join(x for _, x in rows)}</section>")
    page = f"""<!doctype html><meta charset="utf-8"><title>Interviewer sheet · v15</title>
<style>body{{font:15px/1.5 Inter,system-ui,sans-serif;color:#15172F;max-width:900px;margin:32px auto;padding:0 20px}}
h2{{font-size:19px;margin:28px 0 2px}} .q{{border:1px solid #EAECF0;border-radius:10px;padding:10px 14px;margin:10px 0}} .known{{opacity:.55}}
.h{{font-weight:600}} .t{{font-weight:400;color:#667085;font-size:12px;margin-left:6px}} .k{{font-size:11px;background:#F5F5FF;color:#5258FB;border-radius:5px;padding:1px 6px}}
.ask{{font-size:16px;margin:6px 0}} .small{{font-size:13px;color:#667085;margin:2px 0}} .lad{{font-size:13px;margin:6px 0 0;padding-left:18px}} code{{background:#F5F5FF;padding:1px 4px;border-radius:4px}}</style>
<h1>Interviewer sheet</h1>
<p class="small">Generated from substrate {E(S['version'])}: the same challenges and practices the laptop page reads. Ask about the problem,
never the tool. Record each answer as one line the engine reads like laptop evidence:
<code>{{"kind":"practice","id":"&lt;practice id&gt;","rung":"L2|L3|L4","strength":"showed|said","why":"&lt;their words&gt;"}}</code>
— "showed" when they walk through a real moment, "said" when they only claim it. A question you don't ask is never a minus.</p>
{''.join(parts)}"""
    open(dst, "w").write(page)


if __name__ == "__main__":
    lap = sys.argv[sys.argv.index("--laptop") + 1] if "--laptop" in sys.argv else None
    main(sys.argv[1], lap)
