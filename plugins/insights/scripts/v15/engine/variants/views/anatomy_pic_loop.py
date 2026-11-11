"""Loop engineering, drawn: one run going round, phase by phase, every Loop part placed once and coloured by who does
it (by hand, their setup, their own tools), with the person's own file that does it (taken from the highest rung we saw).
render(run_dir, tree) -> html. Stdlib only; classes prefixed pv-loop-."""
import html
import os
import re

HOME = os.path.expanduser("~")
RUNGS = ("L2", "L3", "L4")
WORD = {"L4": "Your own tools", "L3": "Your setup", "L2": "By hand", "not seen": "Not seen yet", "can't tell": "Couldn't check"}  # view_anatomy.WORD
CLS = {"L4": "a", "L3": "c", "L2": "m", "not seen": "n", "can't tell": "q"}

# the order a run goes round; any Loop part not named here lands in "Also" so every part shows exactly once
PHASES = [
    ("Start", "a run begins", ["starts-without-you", "goal"]),
    ("Work", "it works on its own", ["while-away", "list-loop"]),
    ("Check", "is it really done?", ["looks-at-output", "check-before-done", "independent-judge"]),
    ("Retry or stop", "go again, or stop", ["round-cap", "findings-carried"]),
    ("Hand back", "what you get back", ["unmet-written", "hand-back", "decision-log", "escalation"]),
]
FILE = re.compile(r"([~\w./@-]*?([\w@-]+\.(?:mjs|cjs|js|ts|py|sh|md|json|ya?ml|txt|toml)))(?::([\d\- and]+))?")
esc = lambda s: html.escape(str(s), quote=True)


def short(p):
    return p.replace(HOME + "/", "~/").replace(HOME, "~")


def fired(r):
    return [(t["name"], s) for t in (r or {}).get("techniques", []) for s in t["signals"]
            if (s.get("you") or {}).get("fired") is True]


def evidence(c):
    """The person's own thing behind a part: a file (preferred) or a count, from the highest rung seen."""
    you = c.get("you")
    if you not in RUNGS:
        return None
    r = c["rungs"].get(you) or {}
    sigs = fired(r)
    for tech, s in sigs:
        note = str((s.get("you") or {}).get("note") or "")
        m = FILE.search(note)
        if m:
            quote = ""
            q = re.search(r'—\s*"(.*)', note, re.S)
            if q:
                quote = q.group(1).split('"')[0].replace("\\", "")[:150]
            where = short(m.group(1)) + (f":{m.group(3).strip()}" if m.group(3) else "")
            return {"label": m.group(2), "file": m.group(2), "tip": where + (f"\n“{quote}…”" if quote else ""),
                    "does": r.get("does") or ""}
    for tech, s in sigs:
        y = s.get("you") or {}
        v, note = y.get("value"), str(y.get("note") or "")
        if isinstance(v, int) and v > 0:
            return {"label": f"{v}×", "file": None, "tip": note or tech, "does": r.get("does") or ""}
    return {"label": "seen", "file": None, "tip": "", "does": r.get("does") or ""}


def count_note(c):
    """A count note from the highest rung seen (e.g. '62 sessions where a Stop hook sent the run back 2+ times')."""
    if not c or c.get("you") not in RUNGS:
        return ""
    for _, s in fired(c["rungs"].get(c["you"])):
        note = str((s.get("you") or {}).get("note") or "")
        if note[:1].isdigit() and "/" not in note:
            return note
    return ""


def node(c):
    you = c.get("you")
    ev = evidence(c)
    tip = f'{c["name"]} — {WORD.get(you, you)}'
    if ev:
        tip += (f'\n{ev["does"]}' if ev["does"] else "") + (f'\n{ev["tip"]}' if ev["tip"] else "")
        kind = " pv-loop-f" if ev["file"] else ""
        chip = f'<span class="pv-loop-chip{kind}">{esc(ev["label"])}</span>'
    else:
        chip = f'<span class="pv-loop-none">{esc(WORD.get(you, you).lower())}</span>'
    attr = f' data-file="{esc(ev["file"])}"' if ev and ev["file"] else ""
    return (f'<div class="pv-loop-node" role="button" tabindex="0" data-go="px-loop-{esc(c["id"])}" title="{esc(tip)}"{attr}>'
            f'<span class="pv-loop-sw pv-loop-{CLS.get(you, "q")}"></span>'
            f'<span class="pv-loop-nm">{esc(c["name"])}</span>{chip}</div>')


ARROW = ('<svg class="pv-loop-arr" viewBox="0 0 18 12" aria-hidden="true"><path d="M1 6h13M10 1.5 15.5 6 10 10.5" '
         'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')

CSS = """
.pv-loop{--gap:22px}
.pv-loop-head{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:8px 20px;margin-bottom:12px}
.pv-loop-title{font-size:15.5px;font-weight:650;color:#15172F}
.pv-loop-cap{font-size:13px;color:#667085;margin-top:2px;max-width:640px}
.pv-loop-legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;color:#475467}
.pv-loop-legend span{display:inline-flex;align-items:center;gap:5px}
.pv-loop-grid{display:grid;grid-template-columns:1fr var(--gap) 1fr var(--gap) 1fr var(--gap) 1fr var(--gap) 1fr;
  grid-template-rows:auto auto auto;row-gap:0}
.pv-loop-arc{position:relative;height:30px;border:1.5px solid #999AFD;color:#5258FB}
.pv-loop-arc.top{grid-row:1;grid-column:3/8;border-bottom:0;border-radius:12px 12px 0 0;
  margin:0 calc((100% - 2 * var(--gap)) / 6);margin-top:6px}
.pv-loop-arc.bot{grid-row:3;grid-column:1/10;border-top:0;border-radius:0 0 12px 12px;
  margin:0 calc((100% - 4 * var(--gap)) / 10)}
.pv-loop-arc::after{content:"";position:absolute;left:-5.5px;border:5px solid transparent}
.pv-loop-arc.top::after{bottom:-6px;border-top:7px solid #999AFD;border-bottom:0}
.pv-loop-arc.bot::after{top:-6px;border-bottom:7px solid #999AFD;border-top:0}
.pv-loop-arc span{position:absolute;left:50%;transform:translate(-50%,-50%);white-space:nowrap;font-size:12px;
  background:#FFFFFF;padding:0 8px;color:#3C3ED4;max-width:96%;overflow:hidden;text-overflow:ellipsis}
.pv-loop-arc.top span{top:0}.pv-loop-arc.bot span{top:100%}
.pv-loop-lane{grid-row:2;background:#F9FAFB;border:1px solid #EAECF0;border-radius:12px;padding:10px 8px 8px;
  display:flex;flex-direction:column;gap:6px;min-width:0}
.pv-loop-ph{padding:0 4px 4px}
.pv-loop-ph b{display:block;font-size:13px;color:#15172F}
.pv-loop-ph b i{font-style:normal;display:inline-grid;place-items:center;width:18px;height:18px;border-radius:99px;
  background:#EEEFFE;color:#3C3ED4;font-size:10.5px;margin-right:6px;vertical-align:1px}
.pv-loop-ph span{font-size:12px;color:#667085}
.pv-loop-gapc{grid-row:2;align-self:start;margin-top:14px;display:grid;place-items:center;color:#999AFD}
.pv-loop-arr{width:18px;height:12px}
.pv-loop-node{display:grid;grid-template-columns:12px 1fr;column-gap:8px;row-gap:4px;align-items:start;cursor:pointer;
  background:#FFFFFF;border:1px solid #EAECF0;border-radius:9px;padding:8px 9px;transition:border-color .15s,box-shadow .15s}
.pv-loop-node:hover,.pv-loop-node:focus-visible{border-color:#999AFD}
.pv-loop-node.pv-loop-hl{border-color:#5258FB;box-shadow:0 0 0 3px #DDDEFE}
.pv-loop-sw{width:12px;height:12px;border-radius:3.5px;margin-top:3px}
.pv-loop-a{background:var(--a)}.pv-loop-c{background:var(--c)}.pv-loop-m{background:var(--m)}
.pv-loop-n{background:#FFFFFF;box-shadow:inset 0 0 0 1.5px #D0D5DD}
.pv-loop-q{background:repeating-linear-gradient(135deg,#F2F4F7 0 2.5px,#D0D5DD 2.5px 4px)}
.pv-loop-legend .pv-loop-sw{margin:0;width:10px;height:10px;border-radius:3px}
.pv-loop-nm{font-size:12.5px;font-weight:550;line-height:1.35;color:#15172F}
.pv-loop-chip,.pv-loop-none{grid-column:2;justify-self:start;font-size:11.5px;line-height:1.5;border-radius:5px;max-width:100%;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pv-loop-chip{padding:0 6px;background:#F2F4F7;color:#344054}
.pv-loop-chip.pv-loop-f{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;background:#F5F5FF;color:#3C3ED4}
.pv-loop-none{color:#949BAD;font-style:italic}
.pv-loop-also{margin-top:10px;display:flex;flex-wrap:wrap;gap:6px}
.pv-loop-files{margin-top:14px;display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;font-size:12.5px;color:#475467}
.pv-loop-files .pv-loop-chip{cursor:default;font-size:11.5px}
.pv-loop-files .pv-loop-chip b{font-family:Inter,system-ui,sans-serif;font-weight:600;margin-left:4px;color:#667085}
.pv-loop-files .pv-loop-chip.pv-loop-hl{background:#DDDEFE}
@media (max-width:1100px){.pv-loop{--gap:18px}.pv-loop-lane{padding:10px 6px 6px}.pv-loop-chip,.pv-loop-none{grid-column:1/-1}}
@media (max-width:820px){.pv-loop-grid{grid-template-columns:1fr}.pv-loop-arc,.pv-loop-gapc{display:none}
  .pv-loop-lane{grid-row:auto}}
"""

JS = """(function(){var r=document.currentScript&&document.currentScript.parentNode;if(!r)return;
function hl(f,on){r.querySelectorAll('[data-file]').forEach(function(x){if(x.getAttribute('data-file')===f)x.classList.toggle('pv-loop-hl',on)})}
r.addEventListener('mouseover',function(e){var x=e.target.closest('[data-file]');if(x&&r.contains(x))hl(x.getAttribute('data-file'),true)});
r.addEventListener('mouseout',function(e){var x=e.target.closest('[data-file]');if(x&&r.contains(x))hl(x.getAttribute('data-file'),false)});
r.addEventListener('keydown',function(e){if(e.key==='Enter'&&e.target.matches('.pv-loop-node'))e.target.click()});})();"""


def render(run_dir, tree):
    d = next((d for d in tree.get("discs") or [] if d.get("id") == "loop"), None)
    if not d:
        return ""
    comps = {c["id"]: c for c in d["components"]}
    placed = set()
    lanes = []
    for i, (name, sub, ids) in enumerate(PHASES, 1):
        cs = [comps[x] for x in ids if x in comps]
        placed.update(c["id"] for c in cs)
        col = 2 * i - 1
        lanes.append(f'<div class="pv-loop-lane" style="grid-column:{col}"><div class="pv-loop-ph"><b><i>{i}</i>{esc(name)}</b>'
                     f'<span>{esc(sub)}</span></div>{"".join(node(c) for c in cs)}</div>')
        if i < len(PHASES):
            lanes.append(f'<div class="pv-loop-gapc" style="grid-column:{col + 1}">{ARROW}</div>')
    rest = [c for c in d["components"] if c["id"] not in placed]

    # the two ways back round, labelled from what we saw
    sent = count_note(comps.get("check-before-done")) or count_note(comps.get("round-cap"))
    m = re.match(r"(\d+ sessions?)", sent)
    top = "sent back to work when a check fails" + (f" · {m.group(1)}" if m else "")
    unmet = comps.get("unmet-written")
    bot = "the next run" + (" picks up what was left" if unmet and unmet.get("you") == "L4" else "")

    # the person's files behind the loop, and how many parts each one does
    files = {}
    for c in d["components"]:
        ev = evidence(c)
        if ev and ev["file"]:
            files.setdefault(ev["file"], []).append(c["name"])
    fl = "".join(f'<span class="pv-loop-chip pv-loop-f" data-file="{esc(f)}" title="{esc(chr(10).join(ns))}">{esc(f)}'
                 f'{f"<b>× {len(ns)}</b>" if len(ns) > 1 else ""}</span>'
                 for f, ns in sorted(files.items(), key=lambda kv: (-len(kv[1]), kv[0])))
    legend = "".join(f'<span><i class="pv-loop-sw pv-loop-{CLS[k]}"></i>{esc(w)}</span>' for k, w in WORD.items())
    n_auto = sum(c.get("you") == "L4" for c in d["components"])
    return (f'<style>{CSS}</style><div class="pv-loop">'
            f'<div class="pv-loop-head"><div><div class="pv-loop-title">Your run loop, drawn from your files</div>'
            f'<div class="pv-loop-cap">One run going round, in the order it happens. Each box is a part, coloured by who '
            f'does it (by hand, your setup, your own tools), with the file of yours that does it. Hover a file to see every part it does; click a part to open it.</div></div>'
            f'<div class="pv-loop-legend">{legend}</div></div>'
            f'<div class="pv-loop-grid"><div class="pv-loop-arc top"><span title="{esc(sent)}">{esc(top)}</span></div>{"".join(lanes)}'
            f'<div class="pv-loop-arc bot"><span>{esc(bot)}</span></div></div>'
            + (f'<div class="pv-loop-also">{"".join(node(c) for c in rest)}</div>' if rest else "")
            + (f'<div class="pv-loop-files">Your files in this loop:{fl}</div>' if fl else "")
            + f'<script>{JS}</script></div>')
