"""How far to trust this page: the cheap run set against the audited lab run (Proxify only)."""
ORDER = 60
TITLE = "How far to trust this page"
QUESTION = "How this quick run compares with the slower, audited lab run, and where they differ."
PROXIFY_ONLY = True

CSS = """
.v-trust .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px}
.v-trust .tile{background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;padding:16px}
.v-trust .tq{font-size:12px;color:#667085;margin:0}
.v-trust .tn{font-size:24px;font-weight:600;color:#15172F;margin:4px 0 0}
.v-trust .tl{font-size:12px;color:#475467;margin:0}
.v-trust h3{font-size:14px;font-weight:600;margin:24px 0 8px}
.v-trust ul.dis{list-style:none;margin:0;padding:0;background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px}
.v-trust ul.dis>li{padding:12px 16px;border-top:1px solid #EAECF0}
.v-trust ul.dis>li:first-child{border-top:0}
.v-trust .nm{font-weight:600}
.v-trust .kind{font-size:12px;color:#667085}
.v-trust .said{margin:4px 0 0;color:#344054}
.v-trust .big{font-size:12px;font-weight:500;color:#B54708;background:#FEFBF5;border:1px solid #FDF1D9;border-radius:999px;padding:2px 8px}
.v-trust details>summary{cursor:pointer;color:#3C3ED4;font-size:12px;margin-top:4px}
.v-trust .why{font-size:12px;color:#475467;margin:4px 0 0}
.v-trust .cost{margin-top:16px}
.v-trust dl{margin:8px 0 0;display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:12px;color:#475467}
.v-trust dt{font-weight:500;color:#344054}.v-trust dd{margin:0;overflow-wrap:anywhere}
"""

import re

SHOW = 6


def _pct(a, n):
    return f"{round(100 * a / n)}%" if n else ""


def render(ctx):
    esc, RW = ctx["esc"], ctx["RUNG_WORD"]
    d = ctx.get("cheap_vs_lab")
    if not d or not d.get("summary"):
        return '<p class="empty">not in this run</p>'
    s = d["summary"]
    res = ctx.get("v15_result") or {}
    names = {}
    for D in res.get("disciplines", []):
        for x in D.get("practices", []) + D.get("challenges", []):
            names[x["id"]] = x["name"]

    def word(v, practice):
        if practice and isinstance(v, str):
            cb = v.startswith("cb:")
            base = v[3:] if cb else v
            return base + (" (cb)" if cb else "")
        return v

    pr, pb, rc, lv = s["practices"], s["problems"], s["recommendations"], s["level"]
    cost = dict(res.get("compare", {}).get("lines", [])).get("cost", "")
    tiles = [
        ("How often the steps match", f'{pr["agree"]} of {pr["n"]}', f'practice steps the same ({_pct(pr["agree"], pr["n"])})'),
        ("How often they are close", f'{pr["within_one"]} of {pr["n"]}', f'within one step ({_pct(pr["within_one"], pr["n"])})'),
        ("Same verdict on problems", f'{pb["agree"]} of {pb["n"]}', f'problems with the same verdict ({_pct(pb["agree"], pb["n"])})'),
        ("Same call on whether a problem shows", f'{pb["agree_on_shows"]} of {pb["n"]}', "shows or not"),
        ("Next steps on a real problem", f'{rc["supported"]} of {rc["n"]}', "recommendations resting on a problem that shows"),
        ("Same level", "Yes" if lv["agree"] else "No", f'this run {lv["cheap"]} · lab {lv["lab"]}'),
    ]
    tiles_html = "".join(f'<div class="tile"><p class="tq">{esc(q)}</p><p class="tn">{esc(n)}</p>'
                         f'<p class="tl">{esc(l)}</p></div>' for q, n, l in tiles)

    items = []
    for kind, rows in (("practice", d.get("practice", [])), ("problem", d.get("challenge", []))):
        for x in rows:
            if not x.get("agree"):
                items.append((kind, x))
    NO_ANSWER = ("not read", "can't tell")

    def rank(x):  # 0 far apart on real answers, 1 one side had no answer, 2 one step off, 3 neither had an answer
        c, t = x.get("cheap") in NO_ANSWER, all(v in NO_ANSWER for v in x.get("truth", []))
        if c and t:
            return 3
        if c or t:
            return 1
        return 2 if x.get("within_one") else 0
    LABEL = {0: "far apart", 1: "one side had no answer", 2: "one step off", 3: "neither could tell"}
    items.sort(key=lambda kx: (rank(kx[1]), kx[0] != "practice"))

    def li(kind, x):
        rk = rank(x)
        lab = " or ".join(word(t, kind == "practice") for t in x.get("truth", []))
        what = "step" if kind == "practice" else "verdict"
        return (f'<li><span class="nm">{esc(names.get(x["id"], x["id"]))}</span> '
                f'<span class="kind">{what}</span>' + (f' <span class="{"big" if rk < 2 else "kind"}">{LABEL[rk]}</span>')
                + f'<p class="said">This run: {esc(word(x.get("cheap"), kind == "practice"))} · lab: {esc(lab)}</p>'
                f'<details><summary>What each one saw</summary>'
                f'<p class="why">This run: {esc(x.get("cheap_why"))}</p><p class="why">Lab: {esc(x.get("lab_why"))}</p>'
                f'<dl><dt>Id</dt><dd>{esc(x["id"])}</dd><dt>Source</dt><dd>cheap-vs-lab.json, {kind} list</dd>'
                f'<dt>Lab answer</dt><dd>the audited lab run</dd></dl></details></li>')

    lis = "".join(li(*it) for it in items[:SHOW])
    rest = "".join(li(*it) for it in items[SHOW:])
    more = (f'<details><summary>Show all {len(items)}</summary><ul class="dis">{rest}</ul></details>' if rest else "")
    dis = (f'<h3>Where this run and the lab disagree ({len(items)})</h3><ul class="dis">{lis}</ul>{more}'
           if items else "<h3>Where this run and the lab disagree</h3><p class='empty'>nowhere</p>")
    recs = ""
    if rc.get("cheap"):
        bad = [r for r in rc["cheap"] if not r.get("supported")]
        if bad:
            recs = ("<h3>Next steps that rest on no shown problem</h3><ul class='dis'>" + "".join(
                f'<li><span class="nm">{esc(names.get(r["challenge"], r["challenge"]))}</span> '
                f'<span class="kind">suggested practice: {esc(names.get(r["practice"], r["practice"]))}</span></li>'
                for r in bad) + "</ul>")
    receipt = ('<details class="cost"><summary>How to read this, and what it cost</summary><dl>'
               f'<dt>Cost of this run</dt><dd>{esc(cost) or "not recorded"}</dd>'
               '<dt>Source</dt><dd>cheap-vs-lab.json: each answer of this run set against the lab run\'s audited answer</dd>'
               '<dt>How it was counted</dt><dd>Code compares the two. "Same" = identical step or verdict; "within one" = '
               'at most one step apart.</dd>'
               '<dt>The lab run</dt><dd>10 agents, then audited. Its answers are the yardstick, not proof.</dd>'
               '<dt>Checked by a person</dt><dd>not checked by a person yet (the lab run was audited; this comparison was not)</dd>'
               '<dt>(cb)</dt><dd>a step written "cb:" in the data; shown here with its step word plus (cb)</dd></dl></details>')
    m = re.match(r"([\d,]+)", cost)
    tail = cost.split("tokens,", 1)[1].strip() if "tokens," in cost else ""
    cost_tile = (f'<div class="tile"><p class="tq">What this run cost</p><p class="tn">{esc(m.group(1))} tokens</p>'
                 f'<p class="tl">{esc(tail)}</p></div>') if m else ""
    return f'<div class="tiles">{tiles_html}{cost_tile}</div>{dis}{recs}{receipt}'
