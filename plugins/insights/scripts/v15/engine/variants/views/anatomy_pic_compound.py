"""Compound engineering, drawn: every setup piece on this laptop as a square (yours, installed, or from someone else's
repo), kind by kind, and the hooks still to prune. The Part by part tab (view_anatomy.py) shows it on top of the chapter.
render(run_dir, tree) -> html. Stdlib only. A week-by-week 'how it grew' chart needs git history the setup tool doesn't
collect yet; it comes with that step."""
import html
import json
import os

esc = lambda s: html.escape(str(s), quote=True)
fmt = lambda n: f"{n:,}"

KINDS = [("skill", "Skills"), ("hook_script", "Hook scripts"), ("agent", "Sub-agents"), ("plugin_manifest", "Plugins"),
         ("command", "Commands"), ("marketplace_manifest", "Marketplaces"), ("workflow", "Workflows"),
         ("output_style", "Output styles")]
PLURAL = {"hook": "hooks", "skill": "skills", "plugin": "plugins", "agent": "sub-agents", "rules": "CLAUDE.md files",
          "command": "commands", "workflow": "workflows", "output style": "output styles"}
ORIGIN = [("own", "yours"), ("installed", "installed"), ("third_party", "from someone else's repo")]

CSS = """
.pv-compound-root{--pv-own:#5258FB;--pv-inst:#D0D5DD}
html[data-theme=dark] .pv-compound-root{--pv-inst:#667085}
.pv-compound-root{color:#15172F}
.pv-compound-head h4{margin:0;font-size:15px;font-weight:600;letter-spacing:0;text-transform:none;color:#15172F}
.pv-compound-head p{margin:2px 0 0;font-size:13px;color:#667085}
.pv-compound-grid{display:flex;flex-wrap:wrap;gap:28px;margin-top:14px}
.pv-compound-pane{flex:1 1 420px;min-width:0}
.pv-compound-big{font-size:20px;line-height:1.3;margin:0 0 2px;font-weight:500}
.pv-compound-big b{color:#3C3ED4;font-weight:700}
.pv-compound-sub{font-size:12.5px;color:#667085;margin:0 0 10px}
.pv-compound-legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:#475467;margin:0 0 10px}
.pv-compound-legend span{display:inline-flex;align-items:center;gap:6px}
.pv-compound-sq{display:inline-block;width:8px;height:8px;border-radius:2px;flex:none}
.pv-compound-legend .pv-compound-sq{width:10px;height:10px}
.pv-compound-sq.own{background:var(--pv-own)}
.pv-compound-sq.installed{background:var(--pv-inst)}
.pv-compound-sq.third_party{background:transparent;box-shadow:inset 0 0 0 1.25px #949BAD}
.pv-compound-row{display:grid;grid-template-columns:96px 1fr 104px;gap:10px;align-items:start;padding:5px 0;
  border-top:1px solid #F2F4F7}
.pv-compound-row:first-of-type{border-top:0}
.pv-compound-k{font-size:12.5px;font-weight:600;color:#344054;line-height:1.2;padding-top:0}
.pv-compound-sqs{display:flex;flex-wrap:wrap;gap:2px;padding-top:2px}
.pv-compound-sqs .pv-compound-sq:hover{outline:1.5px solid #15172F;outline-offset:1px}
.pv-compound-n{font-size:12px;color:#667085;text-align:right;line-height:1.2}
.pv-compound-n b{color:#15172F;font-weight:600}
.pv-compound-def{font-size:11.5px;color:#667085;margin:10px 0 0;line-height:1.5}
.pv-compound-def b{color:#475467;font-weight:600}
.pv-compound-prune{margin:12px 0 0;padding:8px 12px;border-radius:10px;background:#F9FAFB;border:1px solid #EAECF0;
  font-size:12.5px;color:#475467}
.pv-compound-prune b{color:#15172F}
"""


def _built(inv):
    arts = inv.get("artifacts") or []
    rows, tot, own = [], 0, 0
    for k, label in KINDS:
        items = [a for a in arts if a.get("kind") == k]
        if not items:
            continue
        cnt = {o: sum(a.get("origin") == o for a in items) for o, _ in ORIGIN}
        order = {o: i for i, (o, _) in enumerate(ORIGIN)}
        items.sort(key=lambda a: (order.get(a.get("origin"), 9), str(a.get("name"))))
        word = dict(ORIGIN)
        sqs = "".join(
            f'<span class="pv-compound-sq {esc(a.get("origin"))}" title="{esc(a.get("name"))} — {esc(word.get(a.get("origin"), "?"))}'
            f'{" · " + esc(os.path.basename(a["project"])) if a.get("project") else ""}"></span>' for a in items)
        tot += len(items)
        own += cnt["own"]
        rest = "<br>".join(f"{cnt[o]} {w if o != 'third_party' else 'from others'}" for o, w in ORIGIN[1:] if cnt[o])
        rows.append(f'<div class="pv-compound-row"><div class="pv-compound-k">{esc(label)}</div>'
                    f'<div class="pv-compound-sqs">{sqs}</div>'
                    f'<div class="pv-compound-n"><b>{fmt(cnt["own"])}</b> yours{("<br>" + rest) if rest else ""}</div></div>')
    return own, tot, "".join(rows)


def _prune(run_dir):
    try:
        pc = json.load(open(os.path.join(run_dir, "portable-checks.json")))
    except (OSError, ValueError):
        return ""
    for c in pc.get("checks") or []:
        if c.get("pid") == "retire-setup-that-no-longer-earns-its-place" and c.get("L") == "L3" and c.get("i") == 1:
            ev = c.get("evidence") or []
            try:
                conf, never = [int(x) for x in ev[0].replace(" configured, ", " ").replace(" never run", "").split()]
                hidden = int(ev[1].split()[0]) if len(ev) > 1 else 0
            except (ValueError, IndexError):
                return ""
            return (f'<div class="pv-compound-prune">Still to prune: <b>{never} of your {conf} switched-on hooks</b> never ran '
                    f'in your 30 days of logs. {hidden} more sit on events Claude Code doesn’t log, so we can’t tell.</div>')
    return ""


def render(run_dir, tree):
    inv = json.load(open(os.path.join(run_dir, "anatomy.json")))
    own, tot, rows = _built(inv)
    nproj = len((inv.get("meta") or {}).get("targets") or [])
    legend = "".join(f'<span><i class="pv-compound-sq {o}"></i>{esc(w)}</span>' for o, w in ORIGIN)
    built = (f'<div class="pv-compound-pane"><p class="pv-compound-big"><b>{fmt(own)} of {fmt(tot)}</b> setup pieces are yours</p>'
             f'<p class="pv-compound-sub">Every square is one piece the scan found in ~/.claude and the {nproj} projects it read. '
             f'Hover a square for its name.</p><div class="pv-compound-legend">{legend}</div>{rows}{_prune(run_dir)}'
             f'<p class="pv-compound-def"><b>Yours:</b> no install receipt, or git shows your commits. '
             f'<b>Installed:</b> came with a receipt (a skills lock, a marketplace, synced from your account) or was unpacked in one go. '
             f'<b>Someone else’s repo:</b> a cloned repo where none of the commits are yours.</p></div>')
    return (f'<style>{CSS}</style><div class="pv-compound-root"><div class="pv-compound-head"><h4>What you built yourself</h4>'
            f'<p>How much of your setup you made, kind by kind, next to what you installed.</p></div>'
            f'<div class="pv-compound-grid">{built}</div></div>')
