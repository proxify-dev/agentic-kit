"""How a verdict was reached, shown the same way on every view (his 11-03, dev mode: "I need to see how it got
there… imagine if overfitting or words like this will only work on my own machine").

Reads a finding's `check` record (written by labs/setup-snapshot/experiments/2026-11-02-cheap/problems.py):
kind · looked_at · looked_for · matched · bar · depends_on · moments[] (or no_moments, saying why there are none).
A word search and a one-off hand read are marked as the fragile kinds: they hold on this laptop, maybe not on the next.
"""
import html
import os

esc = lambda s: html.escape("" if s is None else str(s))  # noqa: E731

KIND = {"words": "Word search in what you typed", "counts": "Counted from your logs", "hand read": "A one-off read of a sample",
        "files": "Read from your files", "git": "From your git history", "settings": "From your settings",
        "tool calls": "From the tools the agent called", "none": "No check written yet"}
FRAGILE = {"words", "hand read"}

CSS = """
.ck{margin:12px 0 0}
.ck-grid{display:grid;grid-template-columns:112px minmax(0,1fr);gap:6px 16px;margin:0;font-size:14px}
.ck-grid dt{font-size:12px;font-weight:500;color:#667085;padding-top:2px}
.ck-grid dd{margin:0;color:#15172F;overflow-wrap:anywhere}
.ck-grid dd.hold{color:#B54708}
.ck-grid dd.hold b{font-weight:600}
.ck-kind{font-weight:600}
.ck-frag{margin-left:8px;font-size:12px;font-weight:500;color:#B54708}
.ck h6{font-size:12px;font-weight:500;color:#667085;margin:16px 0 4px}
.ck ol{list-style:none;margin:0;padding:0;border-top:1px solid #EAECF0}
.ck li{display:grid;grid-template-columns:128px minmax(0,1fr) auto;gap:4px 16px;padding:10px 0;border-bottom:1px solid #EAECF0;align-items:start}
.ck .when{font-size:12px;color:#475467}
.ck .when span{display:block;color:#667085}
.ck q{quotes:none;color:#15172F;overflow-wrap:anywhere}
.ck q mark{background:#FDF1D9;color:#15172F;border-radius:2px;padding:0 2px}
.ck .open{all:unset;cursor:pointer;font:500 12px/1.5 Inter,system-ui,sans-serif;color:#3C3ED4;white-space:nowrap}
.ck .open:hover{text-decoration:underline}
.ck .open.done{color:#039855}
.ck .nomo{font-size:12px;color:#667085;margin:12px 0 0}
@media (max-width:700px){.ck li{grid-template-columns:1fr}.ck-grid{grid-template-columns:1fr}.ck-grid dt{padding-top:8px}}
"""

JS = """<script>document.addEventListener('click',function(e){var b=e.target.closest('.ck .open');if(!b)return;
var t=b.dataset.cmd;(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(function(){
b.textContent='Copied: '+t;b.classList.add('done');setTimeout(function(){b.textContent='Copy open command';b.classList.remove('done')},2400)},
function(){window.prompt('Run this in a terminal:',t)})});</script>"""


def _project(path):
    home = os.path.expanduser("~")
    p = (path or "").replace(home, "~")
    parts = p.rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) > 2 else p


def _quote(m):
    t = m.get("text") or ""
    if m.get("match"):
        a, b = m["match"]
        return f"<q>{esc(t[:a])}<mark>{esc(t[a:b])}</mark>{esc(t[b:])}{'…' if len(t) >= 160 else ''}</q>"
    return f"<q>{esc(t)}</q>"


def moments(chk):
    ms = chk.get("moments") or []
    if not ms:
        note = chk.get("no_moments") or "No moments kept."
        return f'<p class="nomo">No moments to open: {esc(note)}</p>'
    rows = "".join(
        f'<li><span class="when">{esc(m.get("when") or "date not kept")}<span>{esc(_project(m.get("project")))}</span></span>'
        f'{_quote(m)}'
        + (f'<button class="open" type="button" data-cmd="{esc(m["open"])}">Copy open command</button>' if m.get("open") else "<span></span>")
        + "</li>" for m in ms)
    return f'<h6>The moments it found ({len(ms)})</h6><ol>{rows}</ol>'


def block(row):
    """The check, then its moments. `row` is a cheap-problems row."""
    chk = row.get("check") or {}
    kind = chk.get("kind") or "none"
    if kind == "none":
        return f'<div class="ck"><p class="nomo">{esc(chk.get("no_moments") or "No check written yet.")}</p></div>'
    frag = '<span class="ck-frag">may not hold on another laptop</span>' if kind in FRAGILE else ""
    rows = [("How", f'<span class="ck-kind">{esc(KIND.get(kind, kind))}</span>{frag}'),
            ("Looked at", esc(chk.get("looked_at"))), ("Looked for", esc(chk.get("looked_for"))),
            ("Found", esc(chk.get("matched"))), ("Bar", esc(chk.get("bar")))]
    grid = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows if v)
    if chk.get("depends_on"):
        grid += f'<dt>Holds only if</dt><dd class="{"hold" if kind in FRAGILE else ""}">{esc(chk["depends_on"])}</dd>'
    return f'<div class="ck"><dl class="ck-grid">{grid}</dl>{moments(chk)}</div>'
