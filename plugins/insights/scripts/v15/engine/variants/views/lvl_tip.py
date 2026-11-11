"""One tooltip for every level badge on the page. The wording is the substrate's `ladder_short` (one line per level).

A view marks a badge with `lvl_tip.attrs(practice_id, level)`; frame.py embeds `lvl_tip.data()` once as JSON and adds
`CSS` and `JS`, so the badge shows "L3: ..." and "Next, L4: ..." on hover and on keyboard focus. A badge that counts
toward one level on several practices passes them all: `attrs("id-a id-b", "L3")`. The plain `title` stays as the
fallback for a page whose script doesn't run; the script takes it off so the browser's own tip doesn't double up.
"""
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import substrate_kit as S  # noqa: E402

LEVELS = ("L2", "L3", "L4")


def _practices():
    sub = (S.load() or {}).get("sub") or {}
    return sub.get("practices") or {}


def data():
    """{practice id: {n: name, L2: line, L3: line, L4: line}}, only the levels the practice has."""
    out = {}
    for pid, p in _practices().items():
        short = p.get("ladder_short") or {}
        out[pid] = {"n": p.get("name") or pid, **{lv: short[lv] for lv in LEVELS if short.get(lv)}}
    return out


def fallback(pid, lvl):
    """The same words as the tooltip, as plain text for a `title`."""
    p = _practices().get(pid)
    short = (p or {}).get("ladder_short") or {}
    order = ("L1",) + LEVELS
    nxt = order[order.index(lvl) + 1] if lvl in order[:-1] else None
    parts = ([f"{lvl}: {short[lvl]}"] if short.get(lvl) else []) + ([f"Next, {nxt}: {short[nxt]}"] if nxt and short.get(nxt) else [])
    return ". ".join(parts)


def attrs(pid, lvl, title=None):
    """The attributes that make an element a level badge: ' tabindex="0" data-lvl-pid=".." data-lvl=".." title=".."'.
    `title` overrides the fallback text; an element that has no ladder line for this level gets no title."""
    if not pid or lvl not in ("L1",) + LEVELS:
        return ""
    t = title if title is not None else (fallback(pid, lvl) if " " not in pid else "")
    return (f' tabindex="0" data-lvl-pid="{html.escape(pid)}" data-lvl="{lvl}"' + (f' title="{html.escape(t)}"' if t else ""))


def json_script():
    blob = json.dumps(data(), ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/json" id="lvl-data">{blob}</script>'


CSS = """
[data-lvl-pid]{cursor:help}
.lvl-tip{position:fixed;z-index:60;max-width:320px;padding:8px 10px;border-radius:8px;background:#15172F;color:#FFFFFF;
  font:400 12px/1.45 Inter,system-ui,sans-serif;box-shadow:0 8px 24px rgba(16,24,40,.28);pointer-events:none}
.lvl-tip[hidden]{display:none}
.lvl-tip p{margin:0}.lvl-tip p+p{margin-top:4px}
.lvl-tip b{font-weight:600}
.lvl-tip .nx{color:#D0D5DD}
"""

JS = """<script>(function(){
var D={};try{D=JSON.parse(document.getElementById('lvl-data').textContent)}catch(e){}
var N={L1:'L2',L2:'L3',L3:'L4'},tip=document.createElement('div'),cur=null;
tip.className='lvl-tip';tip.id='lvl-tip';tip.setAttribute('role','tooltip');tip.hidden=true;document.body.appendChild(tip);
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function body(el){
  var ids=el.dataset.lvlPid.split(' ').filter(function(i){return D[i]}),l=el.dataset.lvl,h='';
  if(ids.length===1){var d=ids[0],p=D[d];
    if(p[l])h+='<p><b>'+l+'</b>: '+esc(p[l])+'</p>';
    if(N[l]&&p[N[l]])h+='<p class="nx">Next, '+N[l]+': '+esc(p[N[l]])+'</p>'}
  else ids.slice(0,4).forEach(function(d){var p=D[d];if(p[l])h+='<p><b>'+esc(p.n)+'</b> · '+l+': '+esc(p[l])+'</p>'});
  if(ids.length>4)h+='<p class="nx">and '+(ids.length-4)+' more</p>';
  return h}
function place(el){
  var r=el.getBoundingClientRect(),w=tip.offsetWidth,h=tip.offsetHeight,vw=innerWidth,vh=innerHeight;
  var x=Math.max(8,Math.min(r.left,vw-w-8)),y=r.bottom+6;
  if(y+h>vh-8)y=Math.max(8,r.top-h-6);
  tip.style.left=x+'px';tip.style.top=y+'px'}
function show(el){var h=body(el);if(!h)return;cur=el;tip.innerHTML=h;tip.hidden=false;place(el);el.setAttribute('aria-describedby','lvl-tip')}
function hide(){if(cur)cur.removeAttribute('aria-describedby');cur=null;tip.hidden=true}
document.querySelectorAll('[data-lvl-pid]').forEach(function(el){
  if(el.title){el.dataset.fallback=el.title;el.removeAttribute('title')}});
function target(e){return e.target.closest&&e.target.closest('[data-lvl-pid]')}
document.addEventListener('mouseover',function(e){var t=target(e);if(t&&t!==cur)show(t);else if(!t)hide()});
document.addEventListener('focusin',function(e){var t=target(e);if(t)show(t);else hide()});
document.addEventListener('focusout',hide);
document.addEventListener('keydown',function(e){if(e.key==='Escape')hide()});
addEventListener('scroll',hide,true);addEventListener('hashchange',hide);
})();</script>"""
