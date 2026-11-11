"""A day of your sessions, for the Orchestration chapter.

Top: how many sessions were making tool calls at the same minute, stacked by project. Under it, a 'You' lane: every
message typed that day, coloured by the session it went to, so hopping between sessions shows. Then one lane per
session, grouped by project and labelled by its first message: a bar where the agent was making tool calls (calls less
than 5 minutes apart join), a hairline while the session sat open, ticks above for messages typed, orange ticks below
where Esc was pressed or a step refused before typing. The time axis covers the day's active hours; quiet stretches of
2 hours or more fold into a narrow break. A switcher shows the busiest days; hovering shows the time and how many worked.

Reads chats.jsonl (labs/setup-snapshot/experiments/2026-11-02-problems/logs.py): msgs[].t/head typed messages,
msgs[].int Esc presses and msgs[].rej refused tool calls since the previous message, calls[].t/name the main session's
tool calls (name 'Agent' starts a sub-agent). Interactive sessions only: headless runs (entrypoint sdk*) are not in the
file, nor the tool calls sub-agents make."""
import datetime as dt
import html
import json
import os
import re
from collections import Counter, defaultdict

GAP = 300          # tool calls closer than this (seconds) are one stretch of work
LONE = 45          # a single tool call drawn as this many seconds
MAX_LANES = 14
DAYS = 6
HEAD = 38          # characters of the first message used as a lane's label
PROJ_CLS = ["p0", "p1", "p2", "p3"]  # indigo, green, blue, purple; the rest grey
W, L, R = 1000, 292, 12  # viewBox width, label column, right pad
P = W - L - R

# Claude Code names a project folder by its path with "/" as "-": the home part says nothing
HOME_SLUG = os.path.expanduser("~").replace("/", "-")

esc = lambda s: html.escape(str(s), quote=True)


def _proj(p):
    s = (p or "").replace(HOME_SLUG, "", 1)
    s = s.split("--claude-worktrees-")[0]
    if s.startswith("-private-") or s.startswith("-tmp"):
        return "tmp"
    s = s.lstrip("-")
    if not s:
        return "~"
    return s.replace("-claude", ".claude") if s.startswith("-claude") else s


def _hm(t):
    return dt.datetime.fromtimestamp(t).strftime("%H:%M")


def _dur(sec):
    m = int(round(sec / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def _hour(t):
    return dt.datetime.fromtimestamp(t).replace(minute=0, second=0, microsecond=0).timestamp()


def _trim(s, n):
    s = " ".join(re.sub(r"<\\?/?[a-zA-Z_-]+[^>]*>", " ", str(s or "")).split())  # pasted-content and other tags
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _segments(calls, agents):
    segs = []
    for t in sorted(calls):
        if segs and t - segs[-1][1] <= GAP:
            segs[-1][1] = t
            segs[-1][2] += 1
        else:
            segs.append([t, t, 1, 0])
    for t in agents:
        for sg in segs:
            if sg[0] <= t <= sg[1]:
                sg[3] += 1
                break
    return [(a, max(b, a + LONE), n, k) for a, b, n, k in segs]


def _load(run_dir):
    rows = []
    with open(os.path.join(run_dir, "chats.jsonl")) as f:
        for line in f:
            r = json.loads(line)
            if "meta" not in r:
                rows.append(r)
    return rows


def _days(rows):
    """{date: [session]}: each session's tool calls and typed messages on that local day."""
    by = defaultdict(lambda: defaultdict(lambda: {"calls": [], "msgs": [], "agents": []}))
    for r in rows:
        for c in r.get("calls") or []:
            s = by[dt.datetime.fromtimestamp(c["t"]).date()][r["chat"]]
            s["calls"].append(c["t"])
            if c.get("name") == "Agent":
                s["agents"].append(c["t"])
        for m in r.get("msgs") or []:
            by[dt.datetime.fromtimestamp(m["t"]).date()][r["chat"]]["msgs"].append(m)
    meta = {r["chat"]: r for r in rows}
    out = {}
    for d, sess in by.items():
        lst = []
        for cid, s in sess.items():
            segs = _segments(s["calls"], s["agents"])
            msgs = sorted(s["msgs"], key=lambda m: m["t"])
            ts = s["calls"] + [m["t"] for m in msgs]
            if msgs:
                head, cont = msgs[0].get("head") or "", False
            else:  # only tool calls today: a session carried over from an earlier day
                first = sorted(meta[cid].get("msgs") or [], key=lambda m: m["t"])
                head, cont = (first[0].get("head") or "") if first else "", True
            lst.append({"id": cid, "proj": _proj(meta[cid].get("project")), "segs": segs, "msgs": msgs,
                        "t0": min(ts), "t1": max(ts + [b for _, b, _, _ in segs]), "head": head, "cont": cont,
                        "work": sum(b - a for a, b, _, _ in segs), "ncalls": len(s["calls"]), "nagents": len(s["agents"])})
        out[d] = lst
    return out


def _minutes(sess):
    """{minute: Counter(project → sessions making tool calls in that minute)}"""
    cnt = defaultdict(Counter)
    for s in sess:
        for a, b, _, _ in s["segs"]:
            for k in range(int(a // 60), int(b // 60) + 1):
                cnt[k][s["proj"]] += 1
    return cnt


def _stats(sess):
    cnt = _minutes(sess)
    peak, peak_t = 0, None
    for k in sorted(cnt):
        n = sum(cnt[k].values())
        if n > peak:
            peak, peak_t = n, k * 60
    best = (0, None, None, None)  # the longest stretch of tool calls with no typed message inside it
    for s in sess:
        mts = [m["t"] for m in s["msgs"]]
        for a, b, _, _ in s["segs"]:
            cuts = [a] + [t for t in mts if a < t < b] + [b]
            for x, y in zip(cuts, cuts[1:]):
                if y - x > best[0]:
                    best = (y - x, s, x, y)
    typed = sorted((m["t"], s["id"]) for s in sess for m in s["msgs"])
    switches = sum(1 for (_, a), (_, b) in zip(typed, typed[1:]) if a != b)
    return {"n": len(sess), "peak": peak, "peak_t": peak_t, "long": best, "typed": len(typed),
            "switches": switches, "cnt": cnt}


def _scale(sess):
    """The day's active hours as blocks; a quiet stretch of 2+ hours between them folds into a narrow break.
    → pieces [(t0, t1, x0, x1, quiet)] and x(t)."""
    slots = set()
    for s in sess:
        for t in [m["t"] for m in s["msgs"]]:
            slots.add(_hour(t))
        for a, b, _, _ in s["segs"]:
            h = _hour(a)
            while h <= b:
                slots.add(h)
                h += 3600
    blocks = []
    for h in sorted(slots):
        if blocks and h - blocks[-1][1] < 2 * 3600:  # one empty hour stays on the axis
            blocks[-1][1] = h + 3600
        else:
            blocks.append([h, h + 3600])
    G = 34
    on = sum(b - a for a, b in blocks)
    unit = (P - G * (len(blocks) - 1)) / on
    pieces, x0 = [], L
    for i, (a, b) in enumerate(blocks):
        x1 = x0 + (b - a) * unit
        pieces.append((a, b, x0, x1, False))
        x0 = x1
        if i + 1 < len(blocks):
            pieces.append((b, blocks[i + 1][0], x0, x0 + G, True))
            x0 += G

    def x(t):
        if t <= pieces[0][0]:
            return pieces[0][2]
        for a, b, xa, xb, _ in pieces:
            if a <= t <= b:
                return xa + (t - a) / (b - a) * (xb - xa)
        return pieces[-1][3]
    return pieces, x


def _svg(sess, st, rank):
    pieces, x = _scale(sess)
    pc = {p: (PROJ_CLS[rank.index(p)] if p in rank[:len(PROJ_CLS)] else "px") for p in {s["proj"] for s in sess}}
    order = sorted(pc, key=lambda p: rank.index(p) if p in rank else 99)

    shown = sorted(sess, key=lambda s: -s["work"])[:MAX_LANES]
    left = [s for s in sess if s not in shown]
    groups = [(p, sorted([s for s in shown if s["proj"] == p], key=lambda s: s["t0"]),
               sum(1 for s in sess if s["proj"] == p)) for p in order]
    groups = [g for g in groups if g[1]]

    AT, AH = 26, 92                      # the 'at once' area
    AB = AT + AH
    YT = AB + 16                         # the 'You' lane
    YB = YT + 22
    GH, LH = 26, 24
    y = YB + 14
    rows = []
    for p, lanes, total in groups:
        rows.append(("g", p, y, lanes, total))
        y += GH
        for s in lanes:
            rows.append(("l", s, y))
            y += LH
    BOT = y + 4
    H = BOT + 22
    o = [f'<svg class="pv-day-svg" viewBox="0 0 {W} {H}" role="img" aria-label="Sessions on one day">']

    # hour grid, labelled top and bottom; quiet stretches as a folded break
    hours_on = sum((b - a) / 3600 for a, b, _, _, q in pieces if not q)
    step = 1 if hours_on <= 10 else 2
    placed = []  # hour labels already drawn, so none collide (block edges sit next to a folded break)
    for a, b, xa, xb, quiet in pieces:
        if quiet:
            mid = (xa + xb) / 2
            o.append(f'<rect class="pv-day-quiet" x="{xa + 3:.1f}" y="{AT - 4}" width="{xb - xa - 6:.1f}" height="{BOT - AT + 4}" rx="4"/>')
            o.append(f'<text class="pv-day-qt" x="{mid:.1f}" y="{AT + AH / 2:.1f}" text-anchor="middle">'
                     f'<tspan x="{mid:.1f}">{int(round((b - a) / 3600))} h</tspan><tspan x="{mid:.1f}" dy="12">quiet</tspan></text>')
            continue
        h = a
        while h <= b:
            hh = dt.datetime.fromtimestamp(h).hour
            xx = x(h) if h < b else xb
            if (h == a or h == b or hh % step == 0) and all(abs(xx - q) >= 40 for q in placed):
                placed.append(xx)
                o.append(f'<line class="pv-day-grid" x1="{xx:.1f}" x2="{xx:.1f}" y1="{AT - 4}" y2="{BOT}"/>')
                lab = f"{hh:02d}:00"
                anc = "end" if xx > L + P - 16 else "start" if xx < L + 16 else "middle"
                o.append(f'<text class="pv-day-tick" x="{xx:.1f}" y="{AT - 10}" text-anchor="{anc}">{lab}</text>')
                o.append(f'<text class="pv-day-tick" x="{xx:.1f}" y="{BOT + 14}" text-anchor="{anc}">{lab}</text>')
            h += 3600

    # 'working at once': stacked by project, per bin the busiest minute so every stack is a real minute
    peak = max(1, st["peak"])
    yv = lambda n: AB - n / peak * (AH - 10)
    gstep = 1 if peak <= 5 else 2 if peak <= 10 else 5
    for n in range(0, peak + 1, gstep):
        o.append(f'<line class="pv-day-yg" x1="{L}" x2="{L + P}" y1="{yv(n):.1f}" y2="{yv(n):.1f}"/>')
        o.append(f'<text class="pv-day-tick" x="{L - 6}" y="{yv(n) + 3.5:.1f}" text-anchor="end">{n}</text>')
    o.append(f'<text class="pv-day-h" x="0" y="{AT + 14}">Working at once</text>')
    o.append(f'<text class="pv-day-sub" x="0" y="{AT + 30}">sessions making tool calls</text>')
    o.append(f'<text class="pv-day-sub" x="0" y="{AT + 44}">in the same minute</text>')
    cnt = st["cnt"]
    bins = []
    for a, b, xa, xb, quiet in pieces:
        if quiet:
            continue
        mins = int((b - a) // 60)
        bm = max(1, round(mins / max(1, (xb - xa) / 2.2)))
        series = []
        for k in range(int(a // 60), int(b // 60), bm):
            best = max(range(k, k + bm), key=lambda m: sum(cnt[m].values()) if m in cnt else 0)
            c = cnt.get(best, Counter())
            series.append((x(k * 60), x(min(b, (k + bm) * 60)), c))
            bins.append([round(x(k * 60), 1), round(x(min(b, (k + bm) * 60)), 1), sum(c.values()), _hm(best * 60)])
        base = [0] * len(series)
        for p in order:
            top = [base[i] + series[i][2].get(p, 0) for i in range(len(series))]
            if not any(series[i][2].get(p, 0) for i in range(len(series))):
                continue
            up = " ".join(f"{s0:.1f},{yv(t):.1f} {s1:.1f},{yv(t):.1f}" for (s0, s1, _), t in zip(series, top))
            dn = " ".join(f"{s1:.1f},{yv(t):.1f} {s0:.1f},{yv(t):.1f}" for (s0, s1, _), t in reversed(list(zip(series, base))))
            o.append(f'<polygon class="pv-day-area {pc[p]}" points="{up} {dn}"><title>{esc(p)}</title></polygon>')
            base = top
    o.append(f'<line class="pv-day-base" x1="{L}" x2="{L + P}" y1="{AB}" y2="{AB}"/>')
    if st["peak_t"]:
        px_ = x(st["peak_t"])
        right = px_ > L + P - 130
        o.append(f'<circle class="pv-day-pkdot" cx="{px_:.1f}" cy="{yv(st["peak"]):.1f}" r="3.5"/>')
        o.append(f'<text class="pv-day-pk" x="{px_ + (-8 if right else 8):.1f}" y="{yv(st["peak"]) + 4:.1f}" '
                 f'text-anchor="{"end" if right else "start"}">{st["peak"]} at once · {_hm(st["peak_t"])}</text>')

    # 'You': every message typed that day, coloured by its session's project
    proj_of = {s["id"]: s["proj"] for s in sess}
    o.append(f'<text class="pv-day-h" x="0" y="{YT + 15}">You</text>')
    o.append(f'<text class="pv-day-sub" x="34" y="{YT + 15}">{st["typed"]} messages typed</text>')
    o.append(f'<rect class="pv-day-youbg" x="{L}" y="{YT}" width="{P}" height="{YB - YT}" rx="5"/>')
    for s in sess:
        for m in s["msgs"]:
            xx = x(m["t"])
            o.append(f'<line class="pv-day-you {pc[proj_of[s["id"]]]}" x1="{xx:.1f}" x2="{xx:.1f}" y1="{YT + 4}" y2="{YB - 4}">'
                     f'<title>{_hm(m["t"])} · {esc(s["proj"])} · {esc(_trim(m.get("head"), 90))}</title></line>')

    # sessions, grouped by project
    for row in rows:
        if row[0] == "g":
            _, p, gy, lanes, total = row
            o.append(f'<line class="pv-day-sep" x1="0" x2="{W - R}" y1="{gy + 2}" y2="{gy + 2}"/>')
            o.append(f'<rect class="pv-day-chip {pc[p]}" x="0" y="{gy + 10}" width="10" height="10" rx="3"/>')
            more = f", {len(lanes)} drawn" if len(lanes) < total else ""
            o.append(f'<text class="pv-day-g" x="16" y="{gy + 19}">{esc(p)}<tspan class="pv-day-gn"> · {total} session'
                     f'{"s" if total != 1 else ""}{more}</tspan></text>')
            continue
        _, s, ly = row
        cy = ly + LH / 2 + 1
        cls = pc[s["proj"]]
        label = ("cont. · " if s["cont"] else "") + (s["head"] or "(no message typed)")
        o.append(f'<text class="pv-day-lab" x="16" y="{cy + 4:.1f}"><tspan class="pv-day-lt">{_hm(s["t0"])}</tspan>'
                 f'<tspan dx="8">{esc(_trim(label, HEAD))}</tspan>'
                 f'<title>{esc(_trim(s["head"], 200)) or "(no message typed)"}\n{esc(s["proj"])} · session {esc(s["id"][:8])}'
                 f'{" · carried over from an earlier day" if s["cont"] else ""} · {s["ncalls"]} tool calls, {len(s["msgs"])} '
                 f'messages typed, {s["nagents"]} sub-agents started</title></text>')
        o.append(f'<line class="pv-day-open {cls}" x1="{x(s["t0"]):.1f}" x2="{x(s["t1"]):.1f}" y1="{cy:.1f}" y2="{cy:.1f}"/>')
        for sa, sb, n, k in s["segs"]:
            w = max(3, x(sb) - x(sa))
            sub = f" · {k} sub-agent{'s' if k != 1 else ''} started" if k else ""
            o.append(f'<rect class="pv-day-bar {cls}" x="{x(sa):.1f}" y="{cy - 5:.1f}" width="{w:.1f}" height="10" rx="4">'
                     f'<title>{_hm(sa)}–{_hm(sb)} · the agent made {n} tool call{"s" if n != 1 else ""}{sub}</title></rect>')
        for m in s["msgs"]:
            xx = x(m["t"])
            o.append(f'<line class="pv-day-msg" x1="{xx:.1f}" x2="{xx:.1f}" y1="{cy - 11:.1f}" y2="{cy - 6.5:.1f}">'
                     f'<title>{_hm(m["t"])} · you typed: {esc(_trim(m.get("head"), 90))}</title></line>')
            if m.get("int") or m.get("rej"):
                what = []
                if m.get("int"):
                    what.append("pressed Esc" + (f" ×{m['int']}" if m["int"] > 1 else ""))
                if m.get("rej"):
                    what.append(f"refused {m['rej']} step{'s' if m['rej'] > 1 else ''}")
                o.append(f'<line class="pv-day-esc" x1="{xx:.1f}" x2="{xx:.1f}" y1="{cy + 6.5:.1f}" y2="{cy + 11:.1f}">'
                         f'<title>{_hm(m["t"])} · you {" and ".join(what)}, then typed</title></line>')

    # hover: a crosshair with the time and how many were working
    o.append(f'<g class="pv-day-hair" style="display:none"><line x1="0" x2="0" y1="{AT - 4}" y2="{BOT}"/>'
             f'<rect class="pv-day-hbg" x="0" y="{AT - 22}" width="10" height="17" rx="4"/>'
             f'<text class="pv-day-ht" x="0" y="{AT - 9.5}"></text></g>')
    o.append(f'<rect class="pv-day-hit" x="{L}" y="{AT - 4}" width="{P}" height="{BOT - AT + 4}"/>')
    o.append("</svg>")
    svg = "".join(o)
    gaps = [[round(xa, 1), round(xb, 1), f"{int(round((b - a) / 3600))} h quiet"] for a, b, xa, xb, q in pieces if q]
    svg = svg.replace('<svg class="pv-day-svg"', f'<svg class="pv-day-svg" data-bins=\'{json.dumps(bins)}\' data-gaps=\'{json.dumps(gaps)}\'', 1)

    legend = "".join(f'<span><i class="pv-day-sw {pc[p]}"></i>{esc(p)}</span>' for p in order if pc[p] != "px")
    rest = [p for p in order if pc[p] == "px"]
    if rest:
        legend += f'<span><i class="pv-day-sw px"></i>{esc(", ".join(rest)) if len(rest) <= 2 else str(len(rest)) + " other projects"}</span>'
    more = ""
    if left:
        lw = max(s["work"] for s in left)
        more = (f'<p class="pv-day-more">+{len(left)} more session{"s" if len(left) > 1 else ""} not drawn as lanes, each with '
                f'{_dur(lw) if lw >= 60 else "under a minute"} of tool calls or less; the chart on top and the You lane still count them.</p>')
    return svg, legend, more


CSS = """
.pv-day-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:6px 16px}
.pv-day-head h4{margin:0;font-size:16px;font-weight:600;letter-spacing:0;text-transform:none;color:#15172F}
.pv-day-cap{margin:2px 0 12px;font-size:13px;color:#667085;max-width:860px}
.pv-day-days{display:flex;flex-wrap:wrap;gap:6px}
.px .pv-day-days button{font-size:12px;padding:4px 10px;border-radius:99px;color:#475467;
  box-shadow:inset 0 0 0 1px #EAECF0;background:#FFFFFF}
.px .pv-day-days button:hover{box-shadow:inset 0 0 0 1px #999AFD}
.px .pv-day-days button.on{background:#5258FB;color:#FFFFFF;box-shadow:none}
.px .pv-day-days button b{font-weight:600}
.pv-day-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:4px 0 14px}
.pv-day-stat{background:#F9FAFB;border:1px solid #F2F4F7;border-radius:10px;padding:8px 12px}
.pv-day-stat b{display:block;font-size:20px;line-height:1.2;color:#15172F}
.pv-day-stat span{font-size:12px;line-height:1.35;color:#667085;display:block;margin-top:2px}
.pv-day-svg{width:100%;height:auto;display:block;font-family:inherit;user-select:none}
.pv-day-grid{stroke:#F2F4F7;stroke-width:1}
.pv-day-yg{stroke:#F2F4F7;stroke-width:1;stroke-dasharray:2 3}
.pv-day-base{stroke:#D0D5DD;stroke-width:1}
.pv-day-quiet{fill:#F9FAFB;stroke:#EAECF0;stroke-dasharray:3 3}
.pv-day-qt{font-size:10px;fill:#949BAD}
.pv-day-tick{font-size:10.5px;fill:#949BAD}
.pv-day-h{font-size:12.5px;font-weight:600;fill:#15172F}
.pv-day-sub{font-size:11px;fill:#667085}
.pv-day-pk{font-size:11.5px;font-weight:700;fill:#15172F}
.pv-day-pkdot{fill:#15172F;stroke:#FFFFFF;stroke-width:1.5}
.pv-day-area{opacity:.85}
.pv-day-youbg{fill:#F9FAFB}
.pv-day-you{stroke-width:1.6}
.pv-day-sep{stroke:#F2F4F7;stroke-width:1}
.pv-day-g{font-size:12.5px;font-weight:600;fill:#15172F}
.pv-day-gn{font-weight:400;fill:#667085}
.pv-day-lab{font-size:11.5px;fill:#344054}
.pv-day-lt{fill:#949BAD;font-variant-numeric:tabular-nums}
.pv-day-open{stroke-width:1;opacity:.5}
.pv-day-msg{stroke:#15172F;stroke-width:1.3;opacity:.6;stroke-linecap:round}
.pv-day-esc{stroke:#DC6803;stroke-width:2;stroke-linecap:round}
.pv-day-hit{fill:transparent;cursor:crosshair}
.pv-day-hair{pointer-events:none}
.pv-day-hair line{stroke:#15172F;stroke-width:1;opacity:.55}
.pv-day-hbg{fill:#15172F}
.pv-day-ht{font-size:11px;font-weight:600;fill:#FFFFFF}
.pv-day-area.p0,.pv-day-bar.p0,.pv-day-chip.p0,.pv-day-sw.p0{fill:#5258FB;background:#5258FB}
.pv-day-area.p1,.pv-day-bar.p1,.pv-day-chip.p1,.pv-day-sw.p1{fill:#039855;background:#039855}
.pv-day-area.p2,.pv-day-bar.p2,.pv-day-chip.p2,.pv-day-sw.p2{fill:#1570EF;background:#1570EF}
.pv-day-area.p3,.pv-day-bar.p3,.pv-day-chip.p3,.pv-day-sw.p3{fill:#7A5AF8;background:#7A5AF8}
.pv-day-area.px,.pv-day-bar.px,.pv-day-chip.px,.pv-day-sw.px{fill:#949BAD;background:#949BAD}
.pv-day-open.p0,.pv-day-you.p0{stroke:#5258FB}.pv-day-open.p1,.pv-day-you.p1{stroke:#039855}
.pv-day-open.p2,.pv-day-you.p2{stroke:#1570EF}.pv-day-open.p3,.pv-day-you.p3{stroke:#7A5AF8}
.pv-day-open.px,.pv-day-you.px{stroke:#949BAD}
.pv-day-legend{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:12px;color:#475467;margin-top:10px}
.pv-day-legend span{display:inline-flex;align-items:center;gap:6px}
.pv-day-sw{display:inline-block;width:14px;height:8px;border-radius:3px}
.pv-day-k svg{width:16px;height:14px;overflow:visible}
.pv-day-more,.pv-day-foot{font-size:12px;color:#667085;margin:8px 0 0}
.pv-day-panel[hidden]{display:none}
@media (max-width:900px){.pv-day-stats{grid-template-columns:repeat(2,1fr)}}
"""

JS = """<script>(function(){var r=document.getElementById('pv-day-root');if(!r)return;
r.addEventListener('click',function(e){var b=e.target.closest('.pv-day-days button');if(!b)return;e.preventDefault();e.stopPropagation();
r.querySelectorAll('.pv-day-days button').forEach(function(x){x.classList.toggle('on',x===b)});
r.querySelectorAll('.pv-day-panel').forEach(function(p){p.hidden=p.dataset.day!==b.dataset.day});});
r.querySelectorAll('.pv-day-svg').forEach(function(svg){
  var bins=JSON.parse(svg.dataset.bins||'[]'),gaps=JSON.parse(svg.dataset.gaps||'[]'),g=svg.querySelector('.pv-day-hair'),
      ln=g.querySelector('line'),bg=g.querySelector('rect'),tx=g.querySelector('text'),hit=svg.querySelector('.pv-day-hit'),
      lo=+hit.getAttribute('x'),hi=lo+(+hit.getAttribute('width'));
  function hide(){g.style.display='none'}
  svg.addEventListener('mouseleave',hide);
  svg.addEventListener('mousemove',function(e){
    var m=svg.getScreenCTM();if(!m)return;var pt=svg.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;var p=pt.matrixTransform(m.inverse());
    if(p.x<lo||p.x>hi){hide();return}
    var label=null;for(var i=0;i<gaps.length;i++){if(p.x>=gaps[i][0]&&p.x<=gaps[i][1]){label=gaps[i][2];break}}
    if(!label){for(var j=0;j<bins.length;j++){if(p.x>=bins[j][0]&&p.x<=bins[j][1]){label=bins[j][3]+' \\u00b7 '+bins[j][2]+' working';break}}}
    if(!label){hide();return}
    g.style.display='';ln.setAttribute('x1',p.x);ln.setAttribute('x2',p.x);tx.textContent=label;
    var w=tx.getComputedTextLength()+12,left=Math.min(Math.max(p.x-w/2,lo),hi-w);
    bg.setAttribute('x',left);bg.setAttribute('width',w);tx.setAttribute('x',left+6);});
});})();</script>"""


def render(run_dir, tree):
    rows = _load(run_dir)
    days = _days(rows)
    if not days:
        return '<p class="pv-day-cap">No sessions in the last 30 days of logs.</p>'
    stats = {d: _stats(days[d]) for d in days}
    ranked = sorted(days, key=lambda d: (-stats[d]["peak"], -len(days[d])))
    tot = defaultdict(float)  # colours stay fixed across days: the four projects with most work over the 30 days
    for sess in days.values():
        for s in sess:
            tot[s["proj"]] += s["work"]
    rank = sorted(tot, key=lambda p: -tot[p])
    pick = sorted(ranked[:DAYS])
    default = ranked[0]
    btns, panels = [], []
    keys = ('<span class="pv-day-k"><svg viewBox="0 0 16 14"><rect class="pv-day-bar px" x="0" y="2" width="16" height="10" rx="4"/></svg>agent making tool calls</span>'
            '<span class="pv-day-k"><svg viewBox="0 0 16 14"><line class="pv-day-open px" x1="0" x2="16" y1="7" y2="7" style="opacity:1"/></svg>session open, no tool calls</span>'
            '<span class="pv-day-k"><svg viewBox="0 0 16 14"><line class="pv-day-msg" x1="8" x2="8" y1="2" y2="8" style="opacity:1"/></svg>you typed</span>'
            '<span class="pv-day-k"><svg viewBox="0 0 16 14"><line class="pv-day-esc" x1="8" x2="8" y1="5" y2="12"/></svg>you pressed Esc or refused a step, then typed</span>')
    for d in pick:
        sess, st = days[d], stats[d]
        svg, legend, more = _svg(sess, st, rank)
        ln, ls, la, lb = st["long"]
        long_txt = (f'<b>{_dur(ln)}</b><span>longest the agent worked without a message from you ({esc(ls["proj"])}, '
                    f'{_hm(la)}–{_hm(lb)})</span>') if ls else "<b>–</b><span>no tool calls</span>"
        label = d.strftime("%a %-d %b")
        on = d == default
        btns.append(f'<button type="button" data-day="{d.isoformat()}" class="{"on" if on else ""}">'
                    f'{label} · <b>{st["peak"]}</b> at once</button>')
        panels.append(
            f'<div class="pv-day-panel" data-day="{d.isoformat()}"{"" if on else " hidden"}>'
            f'<div class="pv-day-stats">'
            f'<div class="pv-day-stat"><b>{st["n"]}</b><span>sessions on {label}</span></div>'
            f'<div class="pv-day-stat"><b>{st["peak"]} at once</b><span>most sessions making tool calls in the same minute, at '
            f'{_hm(st["peak_t"]) if st["peak_t"] else "–"}</span></div>'
            f'<div class="pv-day-stat">{long_txt}</div>'
            f'<div class="pv-day-stat"><b>{st["switches"]} switches</b><span>times your next message went to a different '
            f'session, across {st["typed"]} messages</span></div></div>'
            f'{svg}{more}<div class="pv-day-legend">{legend}</div><div class="pv-day-legend">{keys}</div></div>')
    span = f'{min(days).strftime("%-d %b")} – {max(days).strftime("%-d %b")}'
    return (f'<style>{CSS}</style><div id="pv-day-root">'
            f'<div class="pv-day-head"><h4>A day of your sessions</h4><div class="pv-day-days">{"".join(btns)}</div></div>'
            f'<p class="pv-day-cap">The top shape is how many agents worked for you at once. The You lane is every message '
            f'you typed, coloured by the session it went to. Below, each session and what it was about. Hover for the detail.</p>'
            f'{"".join(panels)}'
            f'<p class="pv-day-foot">Your {DAYS} days with the most sessions working at once, from your logs of the last 30 days '
            f'({span}), local time; quiet stretches of 2 hours or more are folded. Sessions you typed in only: headless runs and '
            f'the tool calls inside sub-agents are not in these logs. Tool calls less than 5 minutes apart make one bar. A lane is '
            f'labelled by its first message that day ("cont." when the session carried over with no new message).</p></div>{JS}')
