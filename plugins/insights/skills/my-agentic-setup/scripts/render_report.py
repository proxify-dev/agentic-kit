#!/usr/bin/env python3
"""Render a member's report.json (schema: REPORT-SCHEMA.md) as one self-contained HTML page.

    python3 render_report.py report.json [-o report.html]

Stdlib only. Every string from the JSON is escaped; the page needs no JavaScript.

This is the "Share with Proxify" view: the same scan as the member's own page, with names masked, and no grading.
Each practice shows its facts and the question we may ask; there is no seen/signs/ask label, no tally and no
level (ruling 2026-10-26: nothing on the member's machine reads as a score).
"""
import argparse
import datetime
import html
import json
import sys

CSS = """
:root{
  --p600:#5258FB; --p700:#3C3ED4; --p25:#F5F5FF; --p100:#D5D5FE; --p900:#171559;
  --midnight:#15172F; --g50:#F9FAFB; --g100:#F4F5F7; --g200:#EAECF0; --g300:#D0D5DD; --g500:#667085; --g600:#475467; --g700:#344054;
  --ok:#039855; --okbg:#F6FEF9; --okline:#C4F3D8; --warn:#B54708; --warnbg:#FEFBF5; --warnline:#FBDEA5;
}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;font-weight:300;color:var(--midnight);background:var(--g50);line-height:1.6}
h1,h2,h3{font-family:Poppins,Inter,system-ui,sans-serif;font-weight:700;line-height:1.3;margin:0 0 .5em}
h1{font-size:32px} h2{font-size:24px} h3{font-size:18px;font-weight:600}
strong{font-weight:600}
a{color:inherit;text-decoration:none}
.eyebrow{font-weight:500;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--p100)}
.hero{background:var(--midnight);color:#fff;padding:48px 24px 56px}
.hero .in{max-width:960px;margin:0 auto}
.hero h1{margin:.3em 0 .4em}
.meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:15px;color:var(--p100);font-weight:400}
.pill{display:inline-block;font-weight:500;font-size:12px;padding:3px 10px;border-radius:999px;border:1px solid var(--p100);color:var(--p100)}
main{max-width:960px;margin:0 auto;padding:0 24px 56px}
section{background:#fff;border:1px solid var(--g200);border-radius:12px;padding:28px;margin:0 0 20px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:-28px 0 20px;position:relative}
.kpi{background:#fff;border:1px solid var(--g200);border-radius:12px;padding:18px}
.kpi b{display:block;font-family:Poppins,Inter,sans-serif;font-size:32px;line-height:1.2;color:var(--p600);font-weight:700}
.kpi span{font-size:14px;color:var(--g600);font-weight:400}
.summary{font-size:18px;margin:0;padding-left:20px}
.summary li{margin:.35em 0}
.count{font-size:14px;font-weight:500;color:var(--g600);margin:-.2em 0 .6em}
.blurb{margin:0 0 16px;color:var(--g700)}
.practice{border:1px solid var(--g200);border-left-width:4px;border-radius:10px;padding:14px 16px;margin:0 0 10px;background:#fff;break-inside:avoid}
.practice{border-left-color:var(--p600)}
.practice .none{margin:.5em 0 0;font-size:15px;color:var(--g600);font-weight:400}
.practice header{display:flex;gap:12px;justify-content:space-between;align-items:flex-start}
.practice h3{margin:0;font-size:16px;font-weight:600}
.practice ul{margin:.5em 0 0;padding-left:20px;font-size:15px;font-weight:400}
.practice li{margin:.2em 0}
.q{margin:.7em 0 0;padding:9px 12px;background:var(--p25);border:1px solid var(--p100);border-radius:8px;font-size:15px;color:var(--p900);font-weight:400}
.q b{font-weight:600;color:var(--p700)}
.rows{width:100%;border-collapse:collapse;font-size:15px}
.rows th,.rows td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--g200);vertical-align:top}
.rows th{font-weight:600;color:var(--g700);width:28%}
.rows td{font-weight:400}
.two{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media (max-width:720px){.two{grid-template-columns:1fr}}
ul.plain{margin:.3em 0;padding-left:20px;font-weight:400} ul.plain li{margin:.3em 0}
footer{color:var(--g500);font-size:13px;text-align:center;padding:0 24px 40px}
@media print{
  @page{margin:14mm}
  body{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact;font-size:12px}
  .hero{padding:24px}
  .kpis{margin-top:12px}
  section{border-radius:0;padding:16px;margin-bottom:12px}
  .practice,.kpi{break-inside:avoid}
  h2,h3{break-after:avoid}
}
""".strip()


def e(value):
    return html.escape("" if value is None else str(value), quote=True)


def as_list(value):
    return value if isinstance(value, list) else []


def pretty_date(raw):
    try:
        d = datetime.datetime.strptime(str(raw), "%Y-%m-%d")
        return "%s %d, %d" % (d.strftime("%B"), d.day, d.year)
    except ValueError:
        return str(raw or "")


def page_title(r):
    return "Your agentic setup, ready to share"


def render_hero(r):
    days = r.get("window_days")
    bits = []
    if r.get("generated"):
        bits.append("<span>%s</span>" % e(pretty_date(r["generated"])))
    if days:
        bits.append("<span>last %s days</span>" % e(days))
    if r.get("masked"):
        bits.append('<span class="pill">client names masked</span>')
    sep = '<span aria-hidden="true">·</span>'
    return (
        '<header class="hero"><div class="in">'
        '<div class="eyebrow">Share with Proxify</div>'
        '<h1>%s</h1><div class="meta">%s</div></div></header>' % (e(page_title(r)), sep.join(bits))
    )


def render_kpis(r):
    items = [i for i in as_list(r.get("headline")) if isinstance(i, dict)]
    if not items:
        return ""
    cells = "".join(
        '<div class="kpi"><b>%s</b><span>%s</span></div>' % (e(i.get("value")), e(i.get("label")))
        for i in items
    )
    return '<div class="kpis">%s</div>' % cells


def render_summary(r):
    lines = as_list(r.get("summary"))
    if not lines:
        return ""
    lis = "".join("<li>%s</li>" % e(s) for s in lines)
    return '<section><h2>In short</h2><ul class="summary">%s</ul></section>' % lis


def anchor(bi, pi):
    return "p-%d-%d" % (bi, pi)


def render_practice(bi, pi, p):
    ev = as_list(p.get("evidence"))
    ev_html = ("<ul>%s</ul>" % "".join("<li>%s</li>" % e(x) for x in ev) if ev
               else '<p class="none">Your logs don\'t cover this one, so we\'d like to hear about it from you.</p>')
    ask = p.get("ask")
    q = '<p class="q"><b>A question we may ask you:</b> %s</p>' % e(ask) if ask else ""
    return ('<article class="practice" id="%s"><header><h3>%s</h3></header>%s%s</article>'
            % (anchor(bi, pi), e(p.get("title")), ev_html, q))


def render_badge(bi, b):
    practices = [p for p in as_list(b.get("practices")) if isinstance(p, dict)]
    body = "".join(render_practice(bi, pi, p) for pi, p in enumerate(practices))
    return '<section><h2>%s</h2><p class="blurb">%s</p>%s</section>' % (e(b.get("title")), e(b.get("blurb")), body)


def render_setup(r):
    rows = [x for x in as_list(r.get("setup")) if isinstance(x, dict)]
    if not rows:
        return ""
    trs = "".join("<tr><th>%s</th><td>%s</td></tr>" % (e(x.get("label")), e(x.get("value"))) for x in rows)
    return '<section><h2>On this machine</h2><table class="rows">%s</table></section>' % trs


def render_lists(r):
    def box(title, key):
        items = as_list(r.get(key))
        if not items:
            return ""
        return '<div><h2>%s</h2><ul class="plain">%s</ul></div>' % (
            e(title), "".join("<li>%s</li>" % e(i) for i in items))

    inner = box("What this report can't show yet", "cannot_see") + box("What's in this file", "privacy")
    return '<section><div class="two">%s</div></section>' % inner if inner else ""


def render(r):
    badges = [b for b in as_list(r.get("badges")) if isinstance(b, dict)]
    parts = [
        render_hero(r),
        "<main>",
        render_kpis(r),
        render_summary(r),
        "".join(render_badge(bi, b) for bi, b in enumerate(badges)),
        render_setup(r),
        render_lists(r),
        "</main>",
        "<footer>Generated on your machine from Claude Code's own files. Schema %s.</footer>"
        % e(r.get("schema")),
    ]
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>%s</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600'
        '&amp;family=Poppins:wght@600;700&amp;display=swap" rel="stylesheet">\n'
        "<style>\n%s\n</style>\n</head>\n<body>\n%s\n</body>\n</html>\n"
        % (e(page_title(r)), CSS, "\n".join(parts))
    )


def main():
    ap = argparse.ArgumentParser(description="Render report.json as a self-contained HTML page.")
    ap.add_argument("report")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    with open(args.report, encoding="utf-8") as f:
        out = render(json.load(f))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
