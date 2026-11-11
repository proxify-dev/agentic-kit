"""Harness chapter picture: one Claude Code session drawn left to right, with this person's hooks at each moment.

Data, all from the run folder:
  anatomy.json     hooks[] (event, matcher, command, script_path) · artifacts[] (origin) · plugins.marketplaces[] (is_own)
  chats.jsonl      last 30 days: messages typed, tool calls (helpers.calls), hook attachments
  portable-checks  enforce-fixed-rules-in-code:L4#0: own hooks before a tool call that can block it
  tree             the anatomy tree; fired notes name the scripts that proved a part (e.g. stop-check.mjs)
"""
import html
import json
import os
import re
from collections import Counter, defaultdict

HOME = os.path.expanduser("~")
esc = lambda s: html.escape(str(s), quote=True)

# the moments of a session, in order; None = the tool itself
STAGES = [
    ("SessionStart", "Session starts", "adds what the agent should know before your first message"),
    ("UserPromptSubmit", "You type", "sees each message first; can add context or block it"),
    ("PreToolUse", "Before a tool", "can allow, ask or block the call"),
    (None, "The tool runs", ""),
    ("PostToolUse", "After a tool", "sees the result; can tell the agent what's wrong"),
    ("Stop", "Agent stops", "can refuse to stop and send the agent back"),
    ("SessionEnd", "Session ends", "saves or cleans up"),
]
SIDE = {"SubagentStart": "a sub-agent starts", "SubagentStop": "a sub-agent stops", "Notification": "it waits for you",
        "PostToolUseFailure": "a tool fails", "PermissionDenied": "a step is refused", "PreCompact": "before compaction",
        "UserPromptExpansion": "a command expands", "Setup": "setup runs"}
NOT_HOOKS = {"statusLine", "fileSuggestion"}  # settings that run a command, not hook events


def _load(run_dir, name, default):
    try:
        return json.load(open(os.path.join(run_dir, name)))
    except (OSError, ValueError):
        return default


def _script(h):
    """The script a hook runs: its path, or the first file-like word of its command."""
    sp = h.get("script_path") or ""
    if sp:
        return sp
    m = re.search(r"[\w./$~{}-]+\.(?:mjs|cjs|js|ts|py|sh|rb)\b", h.get("command") or "")
    return m.group(0) if m else (h.get("command") or "").split(" ")[0]


def _key(path):
    """One script, however many copies: worktrees, temp test folders and plugin versions collapse to one."""
    p = re.sub(r"/\.claude/worktrees/[^/]+", "", path)
    m = re.search(r"/\.claude/plugins/cache/[^/]+/([^/]+)/[^/]+/(.+)$", p)
    if m:
        return f"{m.group(1)}/{os.path.basename(m.group(2))}", m.group(1)
    m = re.search(r"/plugins/([^/]+)/", p)
    owner = m.group(1) if m else os.path.basename(os.path.dirname(os.path.dirname(p))) or ""
    return f"{owner}/{os.path.basename(p)}", (m.group(1) if m else "")


def _origin_fn(inv):
    """own / installed for a hook script: a plugin cache follows its marketplace; a repo with someone else's commits
    is theirs; otherwise the setup tool's rule — no install receipt means it's yours."""
    own_mkt = {m["name"]: bool(m.get("is_own")) for m in (inv.get("plugins") or {}).get("marketplaces") or []}
    own_scripts = {a["path"] for a in inv.get("artifacts") or [] if a.get("kind") == "hook_script" and a.get("origin") == "own"}
    by_dir = defaultdict(Counter)
    for a in inv.get("artifacts") or []:
        parts = a["path"].split("/")
        for n in range(4, len(parts)):
            by_dir["/".join(parts[:n])][a.get("origin")] += 1

    def origin(path):
        m = re.search(r"/\.claude/plugins/cache/([^/]+)/", path)
        if m:
            return "own" if own_mkt.get(m.group(1)) else "installed"
        if path in own_scripts:
            return "own"
        parts = path.split("/")
        for n in range(len(parts) - 1, 3, -1):
            c = by_dir.get("/".join(parts[:n]))
            if c:
                if c.get("third_party") or (c.get("installed", 0) > c.get("own", 0)):
                    return "installed"
                return "own"
        return "own"
    return origin


def _parts_by_script(tree):
    """script basename → the anatomy parts whose evidence quotes it (what that hook earned)."""
    out = defaultdict(list)
    for d in tree.get("discs") or []:
        for c in d["components"]:
            for r in c["rungs"].values():
                for t in r["techniques"]:
                    for s in t["signals"]:
                        y = s.get("you") or {}
                        if y.get("fired") is not True:
                            continue
                        for f in set(re.findall(r"[\w.-]+\.(?:mjs|cjs|js|ts|py|sh)\b", str(y.get("note") or ""))):
                            if c["name"] not in out[f]:
                                out[f].append(c["name"])
    return out


def _stop_rounds(tree):
    for d in tree.get("discs") or []:
        for c in d["components"]:
            for r in c["rungs"].values():
                for t in r["techniques"]:
                    for s in t["signals"]:
                        if s.get("id") == "stop_hook_rounds" and (s.get("you") or {}).get("fired"):
                            return s["you"].get("value")
    return None


def render(run_dir, tree):
    inv = _load(run_dir, "anatomy.json", {})
    origin = _origin_fn(inv)
    earned = _parts_by_script(tree)

    # distinct scripts per moment
    stage = defaultdict(dict)
    for h in inv.get("hooks") or []:
        ev = h.get("event")
        if not ev or ev in NOT_HOOKS:
            continue
        path = _script(h)
        key, plugin = _key(path)
        rec = stage[ev].setdefault(key, {"name": os.path.basename(path), "plugin": plugin, "origin": origin(path),
                                         "matchers": set()})
        if h.get("matcher"):
            rec["matchers"].add(h["matcher"])

    # own hooks before a tool call that can block it (the setup tool's portable check)
    block_n, block_names = None, set()
    for c in _load(run_dir, "portable-checks.json", {}).get("checks") or []:
        if c.get("pid") == "enforce-fixed-rules-in-code" and c.get("L") == "L4" and c.get("i") == 0 and c.get("status") == "counted":
            block_n = c.get("value")
            block_names = {e.split(": ", 1)[-1] for e in c.get("evidence") or []}

    # the last 30 days, from the session logs
    typed = calls = sessions = 0
    tools, att = Counter(), Counter()
    days = 30
    try:
        with open(os.path.join(run_dir, "chats.jsonl")) as f:
            for i, line in enumerate(f):
                r = json.loads(line)
                if "meta" in r:
                    days = r["meta"].get("days") or days
                    continue
                sessions += 1
                typed += len(r.get("msgs") or [])
                tc = (r.get("helpers") or {}).get("calls") or {}
                tools.update(tc)
                att.update({k: v for k, v in (r.get("attachments") or {}).items() if k.startswith("hook_")})
    except OSError:
        pass
    calls = sum(tools.values())
    per_msg = round(calls / typed) if typed else None
    rounds = _stop_rounds(tree)

    def rank(item):
        k, r = item
        return (-(len(earned.get(r["name"], [])) > 0), r["origin"] != "own", -(r["name"] in block_names), not r["matchers"], r["name"])

    def card(ev):
        recs = stage.get(ev) or {}
        n = len(recs)
        if not n:
            return '<div class="pv-harness-card pv-harness-empty"><span>no hooks here</span></div>'
        own = sum(r["origin"] == "own" for r in recs.values())
        items, picked, seen = [], [], set()
        for k, r in sorted(recs.items(), key=rank):
            if r["name"] in seen:
                continue
            seen.add(r["name"])
            picked.append(r)
            if len(picked) == 3:
                break
        for r in picked:
            tag = ""
            if ev == "PreToolUse" and r["name"] in block_names:
                tag = '<em class="pv-harness-block">can block</em>'
            sub = earned.get(r["name"])
            note = (f'<span class="pv-harness-earn">→ {esc(sub[0])}{f" +{len(sub) - 1}" if len(sub) > 1 else ""}</span>' if sub
                    else f'<span class="pv-harness-sub">{esc(r["plugin"] or "")}{" · " if r["plugin"] and r["matchers"] else ""}'
                         f'{esc("on " + sorted(r["matchers"])[0][:22]) if r["matchers"] else ""}</span>')
            items.append(f'<li class="pv-harness-{r["origin"]}" title="{esc(r["name"])}"><code>{esc(r["name"])}</code>{tag}{note}</li>')
        more = f'<div class="pv-harness-more">+ {n - 3} more</div>' if n > 3 else ""
        fact = ""
        if ev == "PreToolUse" and block_n:
            fact = f'<div class="pv-harness-fact"><b>{block_n}</b> of yours can block a tool call</div>'
        if ev == "Stop" and rounds:
            fact = f'<div class="pv-harness-fact">sent the agent back 2+ times in <b>{rounds}</b> sessions</div>'
        return (f'<div class="pv-harness-card"><div class="pv-harness-n"><b>{n}</b> hook{"s" if n != 1 else ""}</div>'
                f'<div class="pv-harness-bar"><i class="o" style="flex:{own}"></i><i class="t" style="flex:{n - own}"></i></div>'
                f'<div class="pv-harness-split">{own} yours · {n - own} installed</div><ul>{"".join(items)}</ul>{more}{fact}</div>')

    heads, cards = [], []
    for ev, label, hint in STAGES:
        if ev is None:
            heads.append(f'<div class="pv-harness-st pv-harness-tool"><span class="pv-harness-dot sq"></span><b>{label}</b>'
                         f'<small>Claude Code runs it</small></div>')
            top = "".join(f'<li><code>{esc(t)}</code><span>{v:,}</span></li>' for t, v in tools.most_common(4))
            cards.append(f'<div class="pv-harness-card pv-harness-tools"><div class="pv-harness-n"><b>{calls:,}</b> calls</div>'
                         f'<ul>{top}</ul></div>')
            continue
        recs = stage.get(ev) or {}
        own = any(r["origin"] == "own" for r in recs.values())
        heads.append(f'<div class="pv-harness-st"><span class="pv-harness-dot{" on" if own else ""}{" off" if not recs else ""}"></span>'
                     f'<b>{label}</b><small>{esc(hint)}</small></div>')
        cards.append(card(ev))

    side = []
    for ev, recs in sorted(stage.items(), key=lambda x: -len(x[1])):
        if ev in SIDE:
            own = sum(r["origin"] == "own" for r in recs.values())
            names = ", ".join(sorted({r["name"] for r in recs.values()})[:2])
            side.append(f'<span class="pv-harness-chip" title="{esc(names)}"><b>{len(recs)}</b> when {esc(SIDE[ev])}'
                        f'<i>{own} yours</i></span>')

    total = sum(len(v) for k, v in stage.items())
    loop = (f'<div class="pv-harness-loop"><span>≈ {per_msg} tool calls for every message you type</span></div>'
            if per_msg else '<div class="pv-harness-loop"><span>repeats for every tool call</span></div>')
    foot = []
    if att.get("hook_success"):
        foot.append(f'<b>{att["hook_success"]:,}</b> hook runs logged')
    if att.get("hook_additional_context"):
        foot.append(f'<b>{att["hook_additional_context"]:,}</b> times a hook added text the agent then read')
    if sessions:
        foot.append(f'<b>{sessions:,}</b> sessions · <b>{typed:,}</b> messages you typed')

    return f"""<style>{CSS}</style>
<div class="pv-harness">
  <div class="pv-harness-h"><div><div class="pv-harness-k">Your harness</div>
    <h4>Where your own code steps into a session</h4>
    <p>Follow one session left to right. Under each moment: the hooks that run there — <span class="pv-harness-sw o"></span>yours,
    <span class="pv-harness-sw t"></span>installed. Hooks counted once each, across all your projects ({total} in all).</p></div></div>
  <div class="pv-harness-grid">
    {loop}
    <div class="pv-harness-rail">{"".join(heads)}</div>
    {"".join(cards)}
  </div>
  {f'<div class="pv-harness-side"><span class="pv-harness-sl">Also, when…</span>{"".join(side)}</div>' if side else ""}
  {f'<div class="pv-harness-foot">Last {days} days, from your session logs: {" · ".join(foot)}</div>' if foot else ""}
</div>"""


CSS = """
.pv-harness{--pv-cols:1fr 1fr 1.08fr .86fr 1.08fr 1.08fr .9fr;font-size:12.5px}
.pv-harness-k{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#5258FB}
.px .pv-harness-h h4{margin:2px 0 4px;font-size:17px;font-weight:600;color:#15172F;letter-spacing:-.005em;text-transform:none}
.pv-harness-h p{margin:0 0 14px;color:#667085;font-size:13px;max-width:820px;line-height:1.5}
.pv-harness-sw{display:inline-block;width:9px;height:9px;border-radius:3px;margin:0 4px 0 2px;vertical-align:0}
.pv-harness-sw.o,.pv-harness-bar .o{background:var(--a)}
.pv-harness-sw.t,.pv-harness-bar .t{background:#D0D5DD}
.pv-harness-grid{display:grid;grid-template-columns:var(--pv-cols);column-gap:8px;row-gap:0}
.pv-harness-rail{grid-column:1/-1;display:grid;grid-template-columns:var(--pv-cols);column-gap:8px;position:relative;padding-bottom:10px}
.pv-harness-rail::before{content:"";position:absolute;left:6px;right:6px;top:6px;height:2px;background:#D0D5DD;border-radius:2px}
.pv-harness-rail::after{content:"";position:absolute;right:0;top:2px;border:5px solid transparent;border-left:7px solid #D0D5DD;border-right:0}
.pv-harness-st{position:relative;display:flex;flex-direction:column;gap:1px;min-width:0}
.pv-harness-st b{font-size:13px;color:#15172F;margin-top:10px}
.pv-harness-st small{font-size:11px;line-height:1.35;color:#667085}
.pv-harness-dot{width:14px;height:14px;border-radius:99px;background:#FFFFFF;box-shadow:inset 0 0 0 2px #999AFD;position:relative;z-index:1}
.pv-harness-dot.on{background:var(--a);box-shadow:0 0 0 3px #FFFFFF}
.pv-harness-dot.off{box-shadow:inset 0 0 0 2px #D0D5DD}
.pv-harness-dot.sq{border-radius:4px;background:#F2F4F7;box-shadow:inset 0 0 0 2px #949BAD}
.pv-harness-loop{grid-column:3/5;height:22px;margin:22px -15px 0 6px;border:2px dashed #999AFD;border-bottom:0;
  border-radius:12px 12px 0 0;position:relative;top:6px;display:flex;justify-content:center}
.pv-harness-loop::after{content:"";position:absolute;left:-7px;bottom:-2px;border:6px solid transparent;border-top:8px solid #999AFD;border-bottom:0}
.pv-harness-loop span{position:absolute;bottom:calc(100% + 4px);color:#3C3ED4;font-weight:600;font-size:12px;white-space:nowrap}
.pv-harness-card{background:#F9FAFB;border:1px solid #EAECF0;border-radius:10px;padding:9px 10px;min-width:0;align-self:start}
.pv-harness-empty{display:grid;place-items:center;min-height:60px;color:#949BAD;font-size:11.5px;border-style:dashed;background:transparent}
.pv-harness-n{font-size:12px;color:#667085}
.pv-harness-n b{font-size:20px;font-weight:600;color:#15172F;margin-right:3px;letter-spacing:-.01em}
.pv-harness-bar{display:flex;height:5px;border-radius:3px;overflow:hidden;gap:1px;margin:5px 0 3px}
.pv-harness-split{font-size:11px;color:#667085}
.pv-harness-card ul{list-style:none;margin:7px 0 0;padding:0;display:flex;flex-direction:column;gap:6px}
.pv-harness-card li{min-width:0;display:flex;flex-direction:column;line-height:1.3}
.pv-harness-card code{font:500 11.5px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace;color:#15172F;overflow-wrap:anywhere}
.pv-harness-card li.pv-harness-own code::before{content:"";display:inline-block;width:6px;height:6px;border-radius:2px;background:var(--a);margin-right:5px;vertical-align:1px}
.pv-harness-card li.pv-harness-installed code{color:#667085}
.pv-harness-card li.pv-harness-installed code::before{content:"";display:inline-block;width:6px;height:6px;border-radius:2px;background:#D0D5DD;margin-right:5px;vertical-align:1px}
.pv-harness-sub{font-size:10.5px;color:#949BAD;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-left:11px}
.pv-harness-earn{font-size:10.5px;line-height:1.35;color:#027A48;padding-left:11px;margin-top:1px}
.pv-harness-block{align-self:flex-start;margin:2px 0 0 11px;font-style:normal;font-size:10px;font-weight:600;letter-spacing:.02em;
  color:#B54708;background:#FEFBF5;box-shadow:inset 0 0 0 1px #FDF1D9;border-radius:5px;padding:0 5px}
.pv-harness-more{font-size:11px;color:#949BAD;margin-top:6px}
.pv-harness-fact{margin-top:8px;padding-top:7px;border-top:1px solid #EAECF0;font-size:11.5px;line-height:1.35;color:#475467}
.pv-harness-fact b{color:#15172F}
.pv-harness-tools{background:transparent;border-style:dashed}
.pv-harness-tools li{flex-direction:row !important;justify-content:space-between;gap:6px;font-size:11px;color:#667085}
.pv-harness-tools li code{color:#475467}
.pv-harness-side{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:14px}
.pv-harness-sl{font-size:11.5px;color:#667085;margin-right:2px}
.pv-harness-chip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:#475467;background:#F9FAFB;
  border:1px solid #EAECF0;border-radius:99px;padding:2px 9px}
.pv-harness-chip b{color:#15172F}
.pv-harness-chip i{font-style:normal;color:#949BAD;font-size:10.5px}
.pv-harness-foot{margin-top:12px;padding-top:10px;border-top:1px solid #EAECF0;font-size:12px;color:#667085}
.pv-harness-foot b{color:#15172F;font-weight:600}
@media (max-width:900px){.pv-harness{--pv-cols:1fr 1fr}.pv-harness-loop,.pv-harness-rail::before,.pv-harness-rail::after{display:none}}
"""
