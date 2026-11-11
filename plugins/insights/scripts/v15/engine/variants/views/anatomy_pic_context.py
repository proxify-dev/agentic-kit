"""Context engineering, drawn: what a session starts with, and how it fills up until a compaction.

Left: the measured context of the first reply in every session (Claude Code's records), and next to it, per busiest
project, the rule files (CLAUDE.md) that load there, as a bytes/4 estimate on the same scale. The records do not split
the start into parts, so the estimate is drawn beside the measured bar, never inside it.
Right: context tokens across four sessions, call by call, with every compaction as a drop (by you or automatic)."""
import collections
import html
import json
import os
import statistics

HOME = os.path.expanduser("~")
esc = lambda s: html.escape(str(s), quote=True)


def _k(n):
    return f"{n / 1000:.0f}k" if n < 999_500 else f"{n / 1e6:.1f}M"


def _short(p):
    return p.replace(HOME + "/", "~/").replace(HOME, "~")


def _first_ctx(r):
    pts = sorted([(c["t"], c["ctx"]) for c in r.get("calls") or [] if c.get("ctx")]
                 + [(m["t"], m["ctx"]) for m in r.get("msgs") or [] if m.get("ctx")])
    return pts[0][1] if pts else None


def _enc(path):
    return path.replace("/", "-").replace(".", "-")


def _project_dir(key, chats):
    """The folder a project key stands for: the cwd whose encoding is the key, else the most common cwd."""
    cwds = collections.Counter(r.get("cwd") for r in chats if r.get("cwd"))
    for c, _ in cwds.most_common():
        if _enc(c) == key:
            return c
    return cwds.most_common(1)[0][0] if cwds else None


def _rule_files(cwd, inv):
    """CLAUDE.md / CLAUDE.local.md in the folder and every folder above it, ~/.claude/CLAUDE.md, and their imports."""
    files = {f["path"]: f for f in inv.get("instruction_files") or []}
    anc, c = {cwd}, cwd
    while c and c != os.path.dirname(c):
        c = os.path.dirname(c)
        anc.add(c)
    home_rules = os.path.join(HOME, ".claude", "CLAUDE.md")
    out = [f for f in files.values() if f.get("kind") in ("claude", "claude_local")
           and (os.path.dirname(f["path"]) in anc or f["path"] == home_rules) and f.get("bytes")]
    seen = {f["path"] for f in out}
    for f in list(out):
        for i in f.get("imports") or []:
            g = files.get(i.get("resolved"))
            if g and g["path"] not in seen and g.get("bytes"):
                out.append(g)
                seen.add(g["path"])
    return sorted(out, key=lambda f: -f["bytes"])


def _hist(starts):
    lo, hi, step = 20_000, 80_000, 2_500
    bins = [0] * ((hi - lo) // step)
    over = 0
    for s in starts:
        if s >= hi:
            over += 1
        else:
            bins[max(0, int((s - lo) // step))] += 1
    W, H, pad_b, pad_t = 470, 120, 22, 18
    n = len(bins) + 1
    bw = W / n
    top = max(bins + [over]) or 1
    med = statistics.median(starts)
    q1, q3 = (sorted(starts)[int(len(starts) * q)] for q in (0.25, 0.75))
    x_of = lambda v: (min(v, hi) - lo) / step * bw
    bars = []
    for i, b in enumerate(bins + [over]):
        h = (H - pad_b - pad_t) * b / top
        cls = "pv-context-hb over" if i == len(bins) else "pv-context-hb"
        bars.append(f'<rect class="{cls}" x="{i * bw + 1:.1f}" y="{H - pad_b - h:.1f}" width="{bw - 2:.1f}" height="{h:.1f}" rx="2">'
                    f'<title>{b} sessions</title></rect>')
    ticks = "".join(f'<text class="pv-context-ax" x="{x_of(v):.1f}" y="{H - 6}" text-anchor="middle">{_k(v)}</text>'
                    for v in (20_000, 40_000, 60_000))
    ticks += f'<text class="pv-context-ax" x="{len(bins) * bw + bw / 2:.1f}" y="{H - 6}" text-anchor="middle">80k+</text>'
    mx = x_of(med)
    band = (f'<rect class="pv-context-band" x="{x_of(q1):.1f}" y="{pad_t - 4}" width="{x_of(q3) - x_of(q1):.1f}" '
            f'height="{H - pad_b - pad_t + 4}" rx="3"/>')
    medl = (f'<line class="pv-context-med" x1="{mx:.1f}" x2="{mx:.1f}" y1="{pad_t - 6}" y2="{H - pad_b}"/>'
            f'<text class="pv-context-medt" x="{mx + 5:.1f}" y="{pad_t + 2}">median {_k(med)}</text>')
    return (f'<svg class="pv-context-svg" viewBox="0 -2 {W} {H + 2}" role="img" '
            f'aria-label="Starting context of {len(starts)} sessions, median {_k(med)} tokens">'
            f'{band}{"".join(bars)}{medl}{ticks}</svg>'), med, q1, q3


def _series(r):
    pts = sorted((c["t"], c["ctx"]) for c in r.get("calls") or [] if c.get("ctx"))
    return pts


def _pick(chats):
    """Four sessions that show the range: most compactions by hand, most automatic ones, the busiest one in another
    project, and the one that compacts earliest."""
    withc = [r for r in chats if len(r.get("compactions") or []) >= 2 and len(_series(r)) > 50]
    if not withc:
        return []
    man = lambda r: sum(c.get("trigger") == "manual" for c in r["compactions"])
    auto = lambda r: sum(c.get("trigger") == "auto" for c in r["compactions"])
    picks = []

    def add(r):
        if r and r["chat"] not in {p["chat"] for p in picks}:
            picks.append(r)

    add(max(withc, key=lambda r: (man(r), len(_series(r)))))
    add(max(withc, key=lambda r: (auto(r), len(_series(r)))))
    home = lambda r: os.path.basename(r.get("cwd") or "")
    others = [r for r in withc if home(r) not in {home(p) for p in picks}]
    add(max(others, key=lambda r: len(_series(r))) if others else None)
    early = [r for r in withc if len(r["compactions"]) >= 3]
    add(min(early or withc, key=lambda r: statistics.median(c["pre"] for c in r["compactions"])))
    for r in sorted(withc, key=lambda r: -len(_series(r))):
        if len(picks) >= 4:
            break
        add(r)
    return picks[:4]


def _mini(r, ymax, auto_level):
    pts = _series(r)
    W, H, pl, pr, pt, pb = 470, 86, 34, 8, 8, 16
    n = len(pts)
    xs = lambda i: pl + (W - pl - pr) * i / max(1, n - 1)
    ys = lambda v: pt + (H - pt - pb) * (1 - min(v, ymax) / ymax)
    step = max(1, n // 400)
    keep = set(range(0, n, step)) | {n - 1}
    times = [t for t, _ in pts]
    marks, labels, last_lx = [], [], -999
    for c in sorted(r.get("compactions") or [], key=lambda c: c["t"]):
        i = next((k for k, t in enumerate(times) if t >= c["t"]), n - 1)
        keep |= {max(0, i - 1), i}
        x = xs(max(0, i - 0.5))
        cls = "man" if c.get("trigger") == "manual" else "auto"
        marks.append(f'<line class="pv-context-drop {cls}" x1="{x:.1f}" x2="{x:.1f}" y1="{ys(c["pre"]):.1f}" y2="{ys(c["post"]):.1f}"/>'
                     f'<circle class="pv-context-dot {cls}" cx="{x:.1f}" cy="{ys(c["pre"]):.1f}" r="3.2"><title>'
                     f'{"compacted by you" if cls == "man" else "automatic compaction"}: {_k(c["pre"])} → {_k(c["post"])}</title></circle>')
        if x - last_lx > 62:
            lx = min(max(x, pl + 26), W - 30)
            labels.append(f'<text class="pv-context-dl {cls}" x="{lx:.1f}" y="{max(ys(c["pre"]) - 6, pt + 4):.1f}" '
                          f'text-anchor="middle">{_k(c["pre"])}→{_k(c["post"])}</text>')
            last_lx = x
    d = "M" + " L".join(f"{xs(i):.1f},{ys(pts[i][1]):.1f}" for i in sorted(keep))
    grid = "".join(f'<line class="pv-context-grid" x1="{pl}" x2="{W - pr}" y1="{ys(v):.1f}" y2="{ys(v):.1f}"/>'
                   f'<text class="pv-context-ax" x="{pl - 5}" y="{ys(v) + 3:.1f}" text-anchor="end">{_k(v)}</text>'
                   for v in (0, 500_000, 1_000_000) if v <= ymax)
    lvl = (f'<line class="pv-context-lvl" x1="{pl}" x2="{W - pr}" y1="{ys(auto_level):.1f}" y2="{ys(auto_level):.1f}"/>'
           if auto_level else "")
    import datetime as dt
    day = dt.datetime.fromtimestamp(pts[0][0]).strftime("%b %-d")
    hours = (pts[-1][0] - pts[0][0]) / 3600
    proj = os.path.basename(r.get("cwd") or "") or r["project"].split("-")[-1]
    nm = sum(c.get("trigger") == "manual" for c in r["compactions"])
    na = len(r["compactions"]) - nm
    who = " · ".join(x for x in (f"{nm} by you" if nm else "", f"{na} automatic" if na else "") if x)
    head = (f'<div class="pv-context-mh"><b>{esc(proj)}</b><span>{day} · {hours:.0f} h · {n:,} tool calls · '
            f'{len(r["compactions"])} compactions ({who})</span></div>')
    return (f'{head}<svg class="pv-context-svg" viewBox="0 0 {W} {H}" role="img" aria-label="Context over one session">'
            f'{grid}{lvl}<path class="pv-context-line" d="{d}"/>{"".join(marks)}{"".join(labels)}</svg>')


CSS = """
.pv-context{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:32px;color:#15172F}
.pv-context>div{min-width:0}
.pv-context h5{margin:0;font-size:15px;font-weight:600}
.pv-context .cap{margin:2px 0 10px;font-size:12.5px;color:#667085;line-height:1.45}
.pv-context .big{font-size:28px;font-weight:700;letter-spacing:-.02em;color:#3C3ED4;line-height:1.1}
.pv-context .big span{font-size:13px;font-weight:500;color:#475467;letter-spacing:0;margin-left:6px}
.pv-context-svg{display:block;width:100%;height:auto;overflow:visible}
.pv-context-hb{fill:#999AFD}.pv-context-hb.over{fill:#D0D5DD}
.pv-context-band{fill:#F5F5FF}
.pv-context-med{stroke:#15172F;stroke-width:1.5;stroke-dasharray:3 3}
.pv-context-medt{font-size:11px;font-weight:600;fill:#15172F;paint-order:stroke;stroke:#FFFFFF;stroke-width:3px;stroke-linejoin:round}
.pv-context-ax{font-size:10px;fill:#667085}
.pv-context-note{font-size:11.5px;color:#667085;margin:4px 0 0}
.pv-context-rows{margin-top:14px;display:flex;flex-direction:column;gap:9px}
.pv-context-row .nm{display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:3px}
.pv-context-row .nm b{font-weight:600}.pv-context-row .nm span{color:#667085}
.pv-context-bar{height:9px;border-radius:5px;background:#999AFD}
.pv-context-est{height:9px;border-radius:5px;margin-top:3px;background:repeating-linear-gradient(135deg,#039855 0 3px,#C4F3D8 3px 6px);min-width:3px}
.pv-context-row .files{font-size:11px;color:#667085;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pv-context-key{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:#475467;margin-top:10px}
.pv-context-key i{display:inline-block;width:14px;height:8px;border-radius:4px;margin-right:5px;vertical-align:middle}
.pv-context-key i.m{background:#999AFD}
.pv-context-key i.e{background:repeating-linear-gradient(135deg,#039855 0 3px,#C4F3D8 3px 6px)}
.pv-context-key i.man{background:#5258FB;width:8px;height:8px}
.pv-context-key i.auto{background:#DC6803;width:8px;height:8px}
.pv-context-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.pv-context-chips span{font-size:11.5px;background:#F2F4F7;color:#475467;border-radius:99px;padding:2px 9px}
.pv-context-chips b{color:#15172F;font-weight:600}
.pv-context-mh{display:flex;gap:8px;align-items:baseline;font-size:12px;margin-top:12px;white-space:nowrap;overflow:hidden}
.pv-context-mh span{overflow:hidden;text-overflow:ellipsis}
.pv-context-mh b{font-weight:600}.pv-context-mh span{color:#667085;font-size:11.5px}
.pv-context-line{fill:none;stroke:#999AFD;stroke-width:1.4;stroke-linejoin:round}
.pv-context-grid{stroke:#EAECF0;stroke-width:1}
.pv-context-lvl{stroke:#DC6803;stroke-width:1;stroke-dasharray:2 3;opacity:.7}
.pv-context-drop{stroke-width:2}.pv-context-drop.man{stroke:#5258FB}.pv-context-drop.auto{stroke:#DC6803}
.pv-context-dot.man{fill:#5258FB}.pv-context-dot.auto{fill:#DC6803}
.pv-context-dl{font-size:10px;font-weight:600;paint-order:stroke;stroke:#FFFFFF;stroke-width:3px;stroke-linejoin:round}.pv-context-dl.man{fill:#3C3ED4}.pv-context-dl.auto{fill:#B54708}
@media (max-width:900px){.pv-context{grid-template-columns:1fr}}
"""


def render(run_dir, tree):
    lines = [json.loads(x) for x in open(os.path.join(run_dir, "chats.jsonl"))]
    meta = (lines[0].get("meta") or {}) if lines and "meta" in lines[0] else {}
    chats = [r for r in lines if "chat" in r]
    days = meta.get("days") or 30
    inv = json.load(open(os.path.join(run_dir, "anatomy.json")))

    # --- left: what a session starts with
    starts = [s for s in (_first_ctx(r) for r in chats) if s]
    if not starts:
        return ""
    hist, med, q1, q3 = _hist(starts)
    by = collections.defaultdict(list)
    for r in chats:
        by[r["project"]].append(r)
    top = sorted(by, key=lambda p: -sum(len(r.get("msgs") or []) for r in by[p]))[:3]
    rows = []
    proj_meds = {p: statistics.median([s for s in (_first_ctx(r) for r in by[p]) if s] or [0]) for p in top}
    scale = max(proj_meds.values()) or 1
    for p in top:
        cwd = _project_dir(p, by[p])
        if not cwd:
            continue
        rf = _rule_files(cwd, inv)
        est = sum(f["bytes"] for f in rf) / 4
        names = ", ".join(_short(f["path"]) for f in rf)
        rows.append(
            f'<div class="pv-context-row"><div class="nm"><b>{esc(_short(cwd))}</b>'
            f'<span>starts at {_k(proj_meds[p])} · rule files ≈ {est / 1000:.1f}k</span></div>'
            f'<div class="pv-context-bar" style="width:{100 * proj_meds[p] / scale:.1f}%" title="median starting context, measured"></div>'
            f'<div class="pv-context-est" style="width:{100 * est / scale:.2f}%" title="rule files: bytes ÷ 4, an estimate"></div>'
            f'<div class="files" title="{esc(names)}">{len(rf)} rule files load here: {esc(names)}</div></div>')
    arts = inv.get("artifacts") or []
    skills = [a for a in arts if a.get("kind") == "skill"]
    own_sk = sum(a.get("origin") == "own" for a in skills)
    styles = [n.get("value") for n in (inv.get("settings") or {}).get("notable") or []
              if n.get("key") == "outputStyle" and n.get("scope") == "home"]
    mem = len(inv.get("memory_stores") or [])
    chips = [f"<span><b>{len(skills)}</b> skills found ({own_sk} yours): a session loads the names and descriptions of those in reach</span>",
             f"<span><b>{len(inv.get('mcp_servers') or [])}</b> MCP servers configured</span>"]
    if styles:
        chips.append(f"<span>output style <b>{esc(styles[0])}</b></span>")
    if mem:
        chips.append(f"<span><b>{mem}</b> memory folders found</span>")
    left = (f'<div><h5>What a session starts with</h5>'
            f'<p class="cap">The context of the first reply in each of your {len(starts)} sessions, last {days} days. '
            f'Half start between {_k(q1)} and {_k(q3)}.</p>'
            f'<div class="big">~{_k(med)}<span>tokens at the first reply</span></div>{hist}'
            f'<p class="pv-context-note">Your rule files are a small slice of that. In your three busiest projects:</p>'
            f'<div class="pv-context-rows">{"".join(rows)}</div>'
            f'<div class="pv-context-key"><span><i class="m"></i>measured: median start</span>'
            f'<span><i class="e"></i>rule files, estimate (bytes ÷ 4)</span></div>'
            f'<p class="pv-context-note">The rest is Claude Code\'s own instructions, tool definitions and what loads with them. '
            f'The records don\'t split it. Also found on this laptop:</p>'
            f'<div class="pv-context-chips">{"".join(chips)}</div></div>')

    # --- right: how a session fills up
    comps = [c for r in chats for c in r.get("compactions") or []]
    man = [c for c in comps if c.get("trigger") == "manual"]
    auto = [c for c in comps if c.get("trigger") == "auto"]
    picks = _pick(chats)
    ymax = 1_000_000 if max((v for r in picks for _, v in _series(r)), default=0) <= 1_000_000 else \
        max(v for r in picks for _, v in _series(r))
    auto_level = statistics.median(c["pre"] for c in auto) if auto else None
    minis = "".join(_mini(r, ymax, auto_level) for r in picks)
    n_with = sum(1 for r in chats if r.get("compactions"))
    head = []
    if man:
        head.append(f'<div class="big">{_k(statistics.median(c["pre"] for c in man))} → '
                    f'{_k(statistics.median(c["post"] for c in man))}<span>where you compact by hand ({len(man)} of {len(comps)})</span></div>')
    sub = (f'The {len(auto)} automatic compactions hit at ~{_k(auto_level)}.' if auto else "")
    right = (f'<div><h5>How a session fills up</h5>'
             f'<p class="cap">Context tokens as the agent works, one tool call at a time, in four of the {n_with} sessions '
             f'that compacted (last {days} days). Each drop is a compaction. {sub}</p>{"".join(head)}{minis}'
             f'<div class="pv-context-key"><span><i class="man"></i>compacted by you</span>'
             f'<span><i class="auto"></i>automatic compaction</span>'
             f'<span>dotted line: where automatic ones hit</span></div></div>')
    return f'<style>{CSS}</style><div class="pv-context">{left}{right}</div>'
