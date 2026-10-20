#!/usr/bin/env python3
"""Render a story.json (schema: STORY-SCHEMA.md) as one self-contained, long-form HTML page.

    python3 render_story.py story.json [-o story.html]

Stdlib only, no JavaScript. Every string from the JSON is escaped. The `v13` key is never read.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I drops the script's folder

from render_report import as_list, e, pretty_date

DETAIL_BANNER = "Detail version: contains real names and prompt excerpts. Not for sharing."

CSS = """
:root{
  --p600:#5258FB; --p700:#3C3ED4; --p25:#F5F5FF; --p100:#D5D5FE; --p900:#171559;
  --midnight:#15172F; --g50:#F9FAFB; --g100:#F4F5F7; --g200:#EAECF0; --g500:#667085; --g600:#475467; --g700:#344054;
  --warn:#B54708; --ok:#039855; --amber:#EAAA08; --purple:#9333EA; --g300:#D0D5DD; --warnbg:#FEFBF5; --warnline:#FBDEA5;
}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;font-weight:300;color:var(--midnight);background:#fff;line-height:1.65}
h1,h2,h3{font-family:Poppins,Inter,system-ui,sans-serif;font-weight:700;line-height:1.3;margin:0}
.eyebrow{font-weight:500;font-size:12px;letter-spacing:.08em;text-transform:uppercase}
.banner{background:var(--warnbg);border-bottom:1px solid var(--warnline);color:var(--warn);font-weight:500;font-size:15px;text-align:center;padding:10px 24px}
.hero{background:var(--midnight);color:#fff;padding:48px 24px 56px}
.hero .in,.wrap{max-width:720px;margin:0 auto}
.hero .eyebrow{color:var(--p100)}
.hero h1{font-size:36px;margin:.3em 0 .4em}
.meta{font-size:15px;color:var(--p100);font-weight:400}
.wrap{padding:0 24px}
.glance{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:-28px auto 0;max-width:960px;padding:0 24px;position:relative}
.stat{background:#fff;border:1px solid var(--g200);border-radius:12px;padding:16px 18px}
.stat b{display:block;font-family:Poppins,Inter,sans-serif;font-size:30px;line-height:1.2;color:var(--p600);font-weight:700}
.stat span{font-size:14px;color:var(--g600);font-weight:400}
.paragraph{font-size:22px;line-height:1.6;margin:48px 0 8px}
.part{font-size:32px;margin:72px 0 0;padding-top:8px;color:var(--midnight)}
.part + .chapter{border-top:0;margin-top:16px;padding-top:8px}
.chapter{border-top:1px solid var(--g200);margin-top:48px;padding-top:32px}
.chapter .eyebrow{color:var(--p700)}
.chapter h2{font-size:24px;margin:.3em 0 .5em}
.lead{font-size:20px;font-weight:400;line-height:1.5;color:var(--p900);margin:0 0 1em}
.lead.cant{color:var(--g600)}
.cov{font-size:13px;line-height:1.5;color:var(--g600);margin:-.4em 0 1.2em;max-width:860px}
.points{margin:0 0 1.2em;padding-left:20px;font-size:17px}
.points li{margin:.45em 0}
.numbers{display:flex;flex-wrap:wrap;gap:28px;margin:0 0 1.4em;padding:14px 0;border-top:1px solid var(--g200);border-bottom:1px solid var(--g200)}
.numbers b{display:block;font-family:Poppins,Inter,sans-serif;font-size:26px;line-height:1.2;font-weight:700;color:var(--p600)}
.numbers span{font-size:14px;color:var(--g600);font-weight:400}
table{width:100%;border-collapse:collapse;font-size:15px;margin:0 0 1.4em}
tbody tr:nth-child(even){background:var(--g50)}
td{overflow-wrap:break-word}
.ladder{list-style:none;margin:0 0 1.4em;padding:0 0 0 4px}
.ladder li{position:relative;padding:0 0 18px 32px;font-size:17px;font-weight:400}
.ladder li::before{content:"";position:absolute;left:0;top:6px;width:14px;height:14px;border-radius:50%;border:2px solid var(--p600);background:#fff}
.ladder li.done::before{background:var(--p600)}
.ladder li::after{content:"";position:absolute;left:8px;top:24px;bottom:0;width:2px;background:var(--p100)}
.ladder li:last-child{padding-bottom:0}
.ladder li:last-child::after{display:none}
.ladder li.todo{color:var(--g600)}
.ladder small{display:block;font-size:14px;font-weight:300;color:var(--g600)}
caption{text-align:left;font-weight:600;color:var(--g700);padding:0 0 8px;font-size:15px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--g200);font-weight:400}
th{font-weight:600;font-size:13px;color:var(--g700);background:var(--g50)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;width:1%}
figure{margin:0 0 1.2em}
figure .label{font-weight:500;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--g500);margin:0 0 6px}
blockquote{margin:0;padding:12px 16px;background:var(--p25);border:1px solid var(--p100);border-radius:8px;
  font-size:15px;font-weight:400;white-space:pre-wrap;overflow-wrap:anywhere;
  display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:6;line-clamp:6;overflow:hidden;
  max-height:calc(6 * 1.65em + 24px)}
figcaption{font-size:13px;color:var(--g500);margin-top:6px}
footer{max-width:720px;margin:56px auto 0;padding:0 24px 40px;color:var(--g500);font-size:13px}
.devtag{display:inline-block;margin-left:10px;font-size:12px;font-weight:500;letter-spacing:.06em;text-transform:uppercase;padding:2px 10px;border-radius:999px;background:var(--p600);color:#fff;vertical-align:middle}
.run{display:flex;flex-wrap:wrap;gap:6px 24px;margin:20px 0 0;font-size:13px;color:var(--p100)}
.run b{font-weight:600;color:#fff;margin-right:4px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px;vertical-align:1px;flex:none}
.dot.solid,.chip.solid::before{background:var(--ok)}
.dot.heuristic,.chip.heuristic::before{background:var(--amber)}
.dot.guess,.chip.guess::before{background:var(--purple)}
.chip{display:inline-block;font-weight:500;font-size:12px;line-height:1;padding:4px 9px;border-radius:999px;border:1px solid;white-space:nowrap}
.chip::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}
.chip.solid{background:#F6FEF9;border-color:#C4F3D8;color:var(--ok)}
.chip.heuristic{background:#FEFBF5;border-color:var(--warnline);color:var(--warn)}
.chip.guess{background:#FCFAFF;border-color:#E1CDFF;color:#581C87}
.devbox{margin-top:48px}
.devbox h2{font-size:24px;margin:0 0 .4em}
.gaps{list-style:none;margin:0;padding:0;counter-reset:gap}
.gaps li{counter-increment:gap;padding:14px 0;border-top:1px solid var(--g200)}
.gaps li:first-child{border-top:0}
.gaps b{font-weight:600;font-size:17px;color:var(--midnight)}
.gaps b::before{content:counter(gap) ". ";color:var(--p600)}
.gaps p{margin:.2em 0 .5em;font-size:16px;font-weight:400}
.tag{display:inline-block;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;background:var(--g100);color:var(--g700);border-radius:4px;padding:1px 7px;margin:0 6px 4px 0}
details.cov>summary,details.how>summary,details.code>summary{cursor:pointer}
details.cov>summary{font-family:Poppins,Inter,sans-serif;font-weight:700;font-size:24px;list-style-position:outside;margin-bottom:.5em}
.counts{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px}
.area{font-size:13px;font-weight:600;color:var(--g700);text-transform:uppercase;letter-spacing:.04em;margin:18px 0 4px}
.cov table{margin:0 0 6px;font-size:14px;table-layout:fixed}
.cov th:nth-child(1){width:30%}.cov th:nth-child(3){width:118px}.cov th:nth-child(4){width:130px}
.st-partial{color:var(--warn)} .st-none{color:var(--g500)}
details.how{margin:.2em 0 .4em;font-size:14px}
details.how>summary{color:var(--p700);font-weight:500;font-size:13px}
.howbody{margin:6px 0 4px;padding:8px 12px;background:var(--g50);border:1px solid var(--g200);border-radius:8px;font-weight:400}
.howbody div{display:grid;grid-template-columns:68px 1fr;gap:0 10px;margin:3px 0;padding:1px 0}
.howbody i{font-style:normal;font-weight:600;color:var(--g600)}
.howbody .caveat{color:var(--warn);background:var(--warnbg);border-radius:4px;padding:2px 6px 2px 0;margin-left:0}
.howbody .caveat i{color:var(--warn);padding-left:6px}
details.code{margin-top:6px}
details.code>summary{font-size:12px;color:var(--g500)}
pre.code{margin:4px 0 0;padding:8px 10px;background:var(--midnight);color:#fff;border-radius:6px;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}
.points li .dot,.lead .dot{margin-left:-16px}
nav.side{display:none}
@media (min-width:1100px){
  body.dev{padding-left:204px}
  nav.side{display:block;position:fixed;left:16px;top:24px;width:164px;font-size:13px;line-height:1.4;max-height:calc(100vh - 48px);overflow:auto;background:#fff;border:1px solid var(--g200);border-radius:10px;padding:10px 12px;z-index:5}
  nav.side a{display:block;text-decoration:none;color:var(--g600);padding:3px 0;font-weight:400}
  nav.side a:hover{color:var(--p700)}
  nav.side .navpart{font-weight:600;color:var(--g700);margin-top:10px}
  nav.side button{margin-top:12px;font:inherit;font-size:12px;padding:4px 8px;border:1px solid var(--g300);background:#fff;border-radius:6px;cursor:pointer}
}
[id]{scroll-margin-top:16px}
@media print{
  nav.side{display:none!important}
  body.dev{padding-left:0!important}
  details.how{display:none}
  @page{margin:14mm}
  body{-webkit-print-color-adjust:exact;print-color-adjust:exact;font-size:12px}
  .hero{padding:24px}
  .paragraph{font-size:17px;margin-top:28px}
  .chapter{margin-top:24px;padding-top:16px;break-inside:avoid-page}
  figure,.stat,tr{break-inside:avoid}
  h2{break-after:avoid}
  .part{margin-top:32px;font-size:26px}
}
""".strip()


def is_number_cell(text):
    t = str(text).strip().replace(",", "").replace("%", "").replace("~", "")
    try:
        float(t)
        return True
    except ValueError:
        return False


CONFIDENCE = ("solid", "heuristic", "guess")


def conf_of(how):
    c = how.get("confidence") if isinstance(how, dict) else None
    return c if c in CONFIDENCE else "guess"


def dot(how):
    if not isinstance(how, dict):
        return ""
    c = conf_of(how)
    return '<span class="dot %s" role="img" aria-label="%s" title="%s"></span>' % (c, c, c)


def render_how(how, dev):
    if not dev or not isinstance(how, dict):
        return ""
    lines = []
    for key, label in (("source", "Source"), ("rule", "Rule"), ("raw", "Numbers")):
        if how.get(key):
            lines.append("<div><i>%s</i><span>%s</span></div>" % (label, e(how[key])))
    if how.get("caveat"):
        lines.append('<div class="caveat"><i>Caveat</i><span>%s</span></div>' % e(how["caveat"]))
    code = ""
    if how.get("code"):
        code = '<details class="code"><summary>code</summary><pre class="code">%s</pre></details>' % e(how["code"])
    return ('<details class="how"><summary>how we know (%s)</summary><div class="howbody">%s%s</div></details>'
            % (e(conf_of(how)), "".join(lines), code))


def render_stats(items):
    items = [i for i in as_list(items) if isinstance(i, dict)]
    if not items:
        return ""
    cells = "".join('<div class="stat"><b>%s</b><span>%s</span></div>' % (e(i.get("value")), e(i.get("label")))
                    for i in items)
    return '<div class="glance">%s</div>' % cells


def render_numbers(items):
    items = [i for i in as_list(items) if isinstance(i, dict)]
    if not items:
        return ""
    cells = "".join("<div><b>%s</b><span>%s</span></div>" % (e(i.get("value")), e(i.get("label")))
                    for i in items)
    return '<div class="numbers">%s</div>' % cells


def render_table(t, dev=False):
    if not isinstance(t, dict):
        return ""
    how_html = render_how(t.get("how"), dev)
    cols = as_list(t.get("columns"))
    rows = [r for r in as_list(t.get("rows")) if isinstance(r, list)]
    if not cols and not rows:
        return ""
    cap = "<caption>%s</caption>" % e(t["caption"]) if t.get("caption") else ""
    numeric = [bool(rows) and all(is_number_cell(r[i]) for r in rows if i < len(r)) and i > 0
               for i in range(len(cols))]
    head = "".join('<th scope="col"%s>%s</th>' % (' class="n"' if numeric[i] else "", e(c))
                   for i, c in enumerate(cols))
    body = "".join(
        "<tr>%s</tr>" % "".join(
            "<td%s>%s</td>" % (' class="n"' if i < len(numeric) and numeric[i] else "", e(c))
            for i, c in enumerate(r))
        for r in rows)
    return how_html + "<table>%s<thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (cap, head, body)


def render_examples(items):
    figs = []
    for x in as_list(items):
        if not isinstance(x, dict) or not x.get("text"):
            continue
        label = '<div class="label">%s</div>' % e(x["label"]) if x.get("label") else ""
        meta = "<figcaption>%s</figcaption>" % e(x["meta"]) if x.get("meta") else ""
        figs.append("<figure>%s<blockquote>%s</blockquote>%s</figure>" % (label, e(x["text"]), meta))
    return "".join(figs)


def render_ladder(steps, dev=False):
    items = []
    for x in as_list(steps):
        if not isinstance(x, dict) or not x.get("step"):
            continue
        state = "done" if x.get("done") is True else "todo"
        ev = '<small>%s</small>' % e(x["evidence"]) if x.get("evidence") else ""
        how = x.get("how") if dev else None
        items.append('<li class="%s"><span class="rung">%s%s</span>%s%s</li>'
                     % (state, dot(how), e(x["step"]), ev, render_how(how, dev)))
    return '<ol class="ladder">%s</ol>' % "".join(items) if items else ""


def render_point(p, dev):
    if isinstance(p, dict):
        how = p.get("how") if dev else None
        return "<li>%s%s%s</li>" % (dot(how), e(p.get("text")), render_how(how, dev))
    return "<li>%s</li>" % e(p)


def render_section(n, s, dev=False, sid=""):
    points = [p for p in as_list(s.get("points")) if p]
    how = s.get("how") if dev else None
    parts = [
        '<section class="chapter"%s>' % (' id="%s"' % sid if sid else ""),
        '<div class="eyebrow">%d</div>' % n,
        "<h2>%s</h2>" % e(s.get("title")),
    ]
    if s.get("lead"):
        parts.append('<p class="lead%s">%s%s</p>' % (" cant" if s.get("can_tell") is False else "", dot(how), e(s["lead"])))
    if (s.get("coverage") or {}).get("line"):
        parts.append('<p class="cov">%s</p>' % e(s["coverage"]["line"]))
    parts.append(render_how(how, dev))
    if points:
        parts.append('<ul class="points">%s</ul>' % "".join(render_point(p, dev) for p in points))
    parts += [render_numbers(s.get("numbers")), render_ladder(s.get("ladder"), dev),
              render_table(s.get("table"), dev), render_examples(s.get("examples"))]
    parts.append("</section>")
    return "".join(parts)


def render_sections(sections, dev=False, nav=None):
    out, part, n = [], None, 0
    for i, s in enumerate(sections, 1):
        sp = s.get("part")
        if sp and sp != part:
            out.append('<h2 class="part">%s</h2>' % e(sp))
            if nav is not None:
                nav.append('<div class="navpart">%s</div>' % e(sp))
        if sp != part:
            part, n = sp, 0
        n += 1
        sid = "sec-%d" % i
        if nav is not None:
            nav.append('<a href="#%s">%s</a>' % (sid, e(s.get("title"))))
        out.append(render_section(n, s, dev, sid))
    return "".join(out)


def render_run(run):
    items = [x for x in as_list(run) if isinstance(x, dict)]
    if not items:
        return ""
    return '<div class="run">%s</div>' % "".join(
        "<span><b>%s</b>%s</span>" % (e(x.get("value")), e(x.get("label"))) for x in items)


def render_gaps(gaps):
    items = [g for g in as_list(gaps) if isinstance(g, dict)]
    if not items:
        return ""
    lis = "".join(
        "<li><b>%s</b><p>%s</p>%s</li>" % (
            e(g.get("title")), e(g.get("why")),
            "".join('<span class="tag">%s</span>' % e(a) for a in as_list(g.get("affects"))))
        for g in items)
    return '<section class="devbox" id="gaps"><h2>Where the picture is thin</h2><ol class="gaps">%s</ol></section>' % lis


def render_coverage(cov):
    rows = [c for c in as_list(cov) if isinstance(c, dict)]
    if not rows:
        return ""
    counts = {c: 0 for c in CONFIDENCE}
    for c in rows:
        counts[conf_of(c)] += 1
    count_html = "".join('<span class="chip %s">%d %s</span>' % (c, counts[c], c) for c in CONFIDENCE)
    areas, order = {}, []
    for c in rows:
        a = c.get("area") or ""
        if a not in areas:
            areas[a] = []
            order.append(a)
        areas[a].append(c)
    groups = []
    for a in order:
        trs = "".join(
            '<tr><td>%s</td><td>%s</td><td><span class="chip %s">%s</span></td><td class="%s">%s</td></tr>' % (
                e(c.get("signal")), e(c.get("source")), conf_of(c), e(conf_of(c)),
                {"partial": "st-partial", "not measured": "st-none"}.get(c.get("status"), ""), e(c.get("status")))
            for c in areas[a])
        groups.append('<div class="area">%s</div><table><thead><tr><th>Signal</th><th>Source</th>'
                      '<th>Confidence</th><th>Status</th></tr></thead><tbody>%s</tbody></table>' % (e(a), trs))
    return ('<section class="devbox cov" id="coverage"><details class="cov" open><summary>Coverage map</summary>'
            '<div class="counts">%s</div>%s</details></section>' % (count_html, "".join(groups)))


def render_no_trace(items):
    items = [x for x in as_list(items) if x]
    if not items:
        return ""
    return ('<section class="devbox" id="no-trace"><h2>Leaves nothing in the logs</h2><ul class="points">%s</ul></section>'
            % "".join("<li>%s</li>" % e(x) for x in items))


EXPAND_JS = ("<script>(function(){var n=document.querySelector('nav.side');if(!n)return;var b=document.createElement('button');"
             "b.textContent='Expand all';var o=false;b.onclick=function(){o=!o;"
             "document.querySelectorAll('details.how').forEach(function(d){d.open=o});"
             "b.textContent=o?'Collapse all':'Expand all'};n.appendChild(b)})()</script>")


def render(r):
    person = (r.get("person") or "").strip()
    title = "How %s works with agents" % person if person else "How this person works with agents"
    meta = [x for x in (pretty_date(r["generated"]) if r.get("generated") else "",
                        "last %s days" % r["window_days"] if r.get("window_days") else "") if x]
    banner = '<div class="banner" role="note">%s</div>' % e(DETAIL_BANNER) if r.get("privacy") != "masked" else ""
    sections = [s for s in as_list(r.get("sections")) if isinstance(s, dict)]
    para = '<p class="paragraph">%s</p>' % e(r["in_one_paragraph"]) if r.get("in_one_paragraph") else ""
    dev = r.get("dev") if isinstance(r.get("dev"), dict) else None
    isdev = dev is not None
    tag = '<span class="devtag">Dev mode</span>' if isdev else ""
    nav = [] if isdev else None
    story = render_sections(sections, isdev, nav)
    sidenav = ""
    if isdev:
        top = [('gaps', 'Gaps') if dev.get("gaps") else None, ('coverage', 'Coverage') if dev.get("coverage") else None]
        links = "".join('<a href="#%s">%s</a>' % t for t in top if t)
        tail = '<a href="#no-trace">Leaves no trace</a>' if dev.get("no_trace") else ""
        sidenav = '<nav class="side" aria-label="Sections">%s%s%s</nav>' % (links, "".join(nav), tail)
    body = [
        banner,
        sidenav,
        '<header class="hero"><div class="in"><div class="eyebrow">Agentic setup report</div><h1>%s%s</h1>'
        '<div class="meta">%s</div>%s</div></header>'
        % (e(title), tag, e(" · ".join(meta)), render_run(dev.get("run")) if isdev else ""),
        render_stats(r.get("at_a_glance")),
        '<main class="wrap">',
        render_gaps(dev.get("gaps")) if isdev else "",
        render_coverage(dev.get("coverage")) if isdev else "",
        para,
        story,
        render_no_trace(dev.get("no_trace")) if isdev else "",
        "</main>",
        EXPAND_JS if isdev else "",
    ]
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>%s</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600'
        '&amp;family=Poppins:wght@600;700&amp;display=swap" rel="stylesheet">\n'
        "<style>\n%s\n</style>\n</head>\n<body%s>\n%s\n</body>\n</html>\n" % (e(title), CSS, ' class="dev"' if isdev else "", "\n".join(body))
    )


def main():
    ap = argparse.ArgumentParser(description="Render story.json as a long-form HTML page.")
    ap.add_argument("story")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    with open(args.story, encoding="utf-8") as f:
        out = render(json.load(f))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
