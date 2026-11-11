"""The ⌘K finder: one box that finds every view (tab), practice, problem and vocabulary term by name, short name or alias,
with fuzzy matching. Not a view: frame.py only loads view_*.py. The idea and the look come from the practices
pre-selection page's search (practices-proposal-2026-11-04/search.js); the data here is the page's own.

Enter on a practice, problem or term opens its side panel (`#p/`, `#c/`, `#t/`, see panel_kit); Enter on a view switches to that
tab. ⌘K or Ctrl+K opens it from anywhere, so does the "Search" button in the header. Esc closes it.
frame.py passes the views it actually renders, so the member's copy never lists an internal-only view.

Fuzzy matching, in the page's own script: a word matches a field when it is a substring of it (best at the start of a word) or, from
three letters on, when its letters appear in order close together ("ctxrot" finds "context rot"). Every word of the query must match;
fields are the name, the short name, and aliases (a term's other names; a practice's industry term, feature keywords and techniques).
Stdlib only.
"""
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import substrate_kit as S  # noqa: E402

esc = lambda s: html.escape("" if s is None else str(s))  # noqa: E731

ICON = {
    "v": '<path d="M2.5 3.5h11v9h-11zM2.5 6.5h11M6 6.5v6"/>',
    "p": '<circle cx="8" cy="8" r="5.5"/><path d="m5.6 8.2 1.7 1.7 3.2-3.6"/>',
    "c": '<path d="M8 2.2 14 12.8H2z"/><path d="M8 6.4v3M8 11.2v.1"/>',
    "t": '<path d="M3.5 3h7.5a1.5 1.5 0 0 1 1.5 1.5V13h-8A1.5 1.5 0 0 1 2.5 11.5v-7A1.5 1.5 0 0 1 3.5 3z"/><path d="M5.5 6h4M5.5 8.5h4"/>',
}
KIND_WORD = {"v": "Views", "p": "Practices", "c": "Problems", "t": "Terms"}


def index(ctx, views):
    """[[kind, id, title, meta, [aliases]], ...]. `views`: [(view id, tab title, group title)] of the views on this page."""
    sub = (S.load() or {}).get("sub") or {}
    area = {d.get("id"): d.get("name") for d in ((ctx.get("v15_result") or {}).get("disciplines") or [])}
    out = [["v", vid, title, group, []] for vid, title, group in views]
    for pid, p in (sub.get("practices") or {}).items():
        term = p.get("term")
        al = [p.get("name")] + ([term] if term and term != "none" else [])
        # what people type: the Claude Code feature names (`keywords`) and how it's done today (techniques), so
        # "dynamic workflows" or "SendMessage" finds the practice they belong to (11-06: both found nothing)
        al += list(p.get("keywords") or []) + [t.get("how") for t in p.get("techniques") or [] if t.get("how")]
        out.append(["p", pid, p.get("short_name") or p.get("name"), area.get(p.get("discipline")) or "", [a for a in al if a]])
    for cid, c in (sub.get("challenges") or {}).items():
        term = c.get("term")
        out.append(["c", cid, c.get("name"), area.get(c.get("discipline")) or "", [term] if term and term != "none" else []])
    for tid, t in (sub.get("vocabulary") or {}).items():
        out.append(["t", tid, t.get("term") or tid, " · ".join(x for x in (t.get("kind"), t.get("grade")) if x), list(t.get("also_called") or [])])
    return out


def data_script(ctx, views):
    blob = json.dumps(index(ctx, views), ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/json" id="kf-data">{blob}</script>'


SEARCH_ICON = ('<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
               'stroke-linejoin="round" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/></svg>')
BUTTON = f'<button class="rp-search" type="button" aria-haspopup="dialog" title="Find a practice, problem, term or view">{SEARCH_ICON}<span>Search</span><kbd>⌘K</kbd></button>'

DIALOG = ('<div class="kf-scrim" id="kf-scrim" hidden></div>'
          '<div class="kf" id="kf" hidden role="dialog" aria-label="Search"><div class="kf-top">' + SEARCH_ICON.replace('width="14" height="14"', 'width="16" height="16"')
          + '<input id="kf-q" type="text" role="combobox" aria-expanded="true" aria-controls="kf-res" aria-autocomplete="list" '
            'placeholder="Find a practice, problem, term or view" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div>'
            '<div class="kf-res" id="kf-res" role="listbox"></div>'
            '<div class="kf-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span></div></div>')

CSS = """
.rp-search{margin-left:auto;display:inline-flex;align-items:center;gap:8px;height:32px;padding:0 8px 0 10px;border:0;border-radius:8px;background:#F9FAFB;
  box-shadow:inset 0 0 0 1px #EAECF0;color:#475467;font:500 13px Inter,system-ui,sans-serif;cursor:pointer;transition:background-color .12s,box-shadow .12s,color .12s}
.rp-search:hover{background:#FFFFFF;color:#15172F;box-shadow:inset 0 0 0 1px #D0D5DD}
.rp-search:focus-visible{outline:2px solid #5258FB;outline-offset:2px}
.rp-search:active{transform:scale(.96)}
.rp-search+.theme-switch{margin-left:0!important}
.rp-search kbd,.kf kbd{font:500 11px Inter,system-ui,sans-serif;color:#475467;background:#FFFFFF;border-radius:5px;padding:2px 6px;box-shadow:inset 0 0 0 1px #D0D5DD}
.kf-scrim{position:fixed;inset:0;z-index:80;background:rgba(16,24,40,.3);transition:opacity .15s ease-out}
.kf-scrim[hidden]{display:block!important;opacity:0;visibility:hidden;pointer-events:none;transition:opacity .15s ease-out,visibility 0s .15s}
.kf{position:fixed;z-index:81;left:50%;top:12vh;width:min(640px,calc(100vw - 24px));max-height:min(560px,76vh);display:flex;flex-direction:column;
  transform:translateX(-50%);background:#FFFFFF;border-radius:16px;overflow:hidden;
  box-shadow:0 0 0 1px #EAECF0,0 12px 32px -4px rgba(16,24,40,.16),0 32px 72px -12px rgba(16,24,40,.28);
  transition:opacity .15s ease-out,transform .18s cubic-bezier(.2,.8,.2,1)}
.kf[hidden]{display:flex!important;opacity:0;visibility:hidden;transform:translateX(-50%) translateY(-6px) scale(.98);transition:opacity .12s ease-out,transform .12s ease-out,visibility 0s .12s}
.kf-top{display:flex;align-items:center;gap:12px;padding:0 16px;height:56px;flex:none;color:#667085;border-bottom:1px solid #F2F4F7}
.kf-top input{flex:1;min-width:0;height:100%;border:0;outline:0;background:transparent;font:400 16px Inter,system-ui,sans-serif;color:#15172F}
.kf-top input::placeholder{color:#667085}
.kf-res{flex:1 1 auto;overflow-y:auto;overscroll-behavior:contain;padding:8px}
.kf-g{margin:0 0 8px}
.kf-gh{display:flex;gap:8px;align-items:baseline;padding:8px 12px 4px;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#667085}
.kf-gh span{font-weight:500;letter-spacing:0;text-transform:none}
.kf-i{display:grid;grid-template-columns:20px minmax(0,1fr) auto;gap:12px;align-items:center;padding:8px 12px;border-radius:10px;cursor:pointer;color:#15172F}
.kf-i svg{color:#667085}
.kf-i.on{background:#F5F5FF;box-shadow:inset 0 0 0 1px #E5E8F9}
.kf-i.on svg{color:#3C3ED4}
.kf-t{min-width:0;font-size:14px;line-height:1.4;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kf-m{font-size:12px;color:#667085;white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis}
.kf mark{background:transparent;color:#3C3ED4;font-weight:600}
.kf-more{padding:4px 12px 4px 44px;font-size:12px;color:#667085}
.kf-empty{padding:32px 16px;text-align:center;font-size:13px;color:#667085}
.kf-foot{display:flex;gap:16px;flex:none;padding:10px 16px;border-top:1px solid #F2F4F7;font-size:12px;color:#667085}
.kf-foot kbd{margin-right:4px}
@media (max-width:560px){.rp-search span,.rp-search kbd{display:none}.rp-search{padding:0 10px}.kf-m{display:none}}
@media (prefers-reduced-motion:reduce){.kf,.kf[hidden],.kf-scrim,.kf-scrim[hidden]{transition:none}}
"""

JS = """<script>(function(){
var D=[];try{D=JSON.parse(document.getElementById('kf-data').textContent)}catch(e){}
var box=document.getElementById('kf'),scrim=document.getElementById('kf-scrim'),Q=document.getElementById('kf-q'),R=document.getElementById('kf-res'),
 btn=document.querySelector('.rp-search'),mac=/Mac|iPhone|iPad/.test(navigator.platform),from=null,items=[],sel=0;
var ICON=%s,WORD={v:'Views',p:'Practices',c:'Problems',t:'Terms'},CAP=6;
function svg(k){return '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+ICON[k]+'</svg>'}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
document.querySelectorAll('.rp-search kbd,.kf-foot kbd').forEach(function(k){if(k.textContent==='\\u2318K')k.textContent=mac?'\\u2318K':'Ctrl K'});
// one word against one text: the positions that matched, and how well; null when it does not match
function word(w,text){
  var lo=text.toLowerCase(),i=lo.indexOf(w);
  if(i>=0){var start=i===0||!/[a-z0-9]/.test(lo[i-1]);return{s:60+(start?40:0)-Math.min(i,30)+(i===0?10:0),pos:range(i,w.length)}}
  if(w.length<3)return null;
  var pos=[],j=0,last=-1,gaps=0;
  for(var k=0;k<w.length;k++){var f=lo.indexOf(w[k],j);if(f<0)return null;if(last>=0)gaps+=f-last-1;pos.push(f);last=f;j=f+1}
  if(gaps>w.length*2)return null;
  return{s:30-gaps,pos:pos}}
function range(a,n){var r=[];for(var i=0;i<n;i++)r.push(a+i);return r}
function score(it,words){
  var fields=[[it[2],1]].concat(it[4].map(function(a){return[a,.7]})),total=0,pos=null,alias=null;
  for(var i=0;i<words.length;i++){var best=null;
    for(var f=0;f<fields.length;f++){var m=word(words[i],fields[f][0]);if(!m)continue;var s=m.s*fields[f][1];
      if(!best||s>best.s)best={s:s,pos:m.pos,f:f}}
    if(!best)return null;total+=best.s;if(best.f===0)pos=(pos||[]).concat(best.pos);else alias=alias||fields[best.f][0]}
  return{s:total-it[2].length*.05,pos:pos,alias:alias}}
function mark(text,pos){if(!pos||!pos.length)return esc(text);var set={},out='',open=false;pos.forEach(function(p){set[p]=1});
  for(var i=0;i<text.length;i++){var on=!!set[i];if(on&&!open){out+='<mark>';open=true}if(!on&&open){out+='</mark>';open=false}out+=esc(text[i])}
  return out+(open?'</mark>':'')}
function run(){
  var raw=Q.value.trim().toLowerCase(),words=raw.split(/\\s+/).filter(Boolean),groups={v:[],p:[],c:[],t:[]},html='';items=[];sel=0;
  if(!words.length){D.forEach(function(it){if(it[0]==='v')groups.v.push({it:it,pos:null})})}
  else D.forEach(function(it){var m=score(it,words);if(m)groups[it[0]].push({it:it,pos:m.pos,alias:m.alias,s:m.s})});
  ['v','p','c','t'].forEach(function(k){var g=groups[k];if(!g.length)return;if(words.length)g.sort(function(a,b){return b.s-a.s});
    var shown=g.slice(0,words.length?CAP:g.length);
    html+='<div class="kf-g" role="group" aria-label="'+WORD[k]+'"><div class="kf-gh">'+WORD[k]+'<span>'+g.length+'</span></div>';
    shown.forEach(function(r){var n=items.length;items.push(r.it);
      html+='<div class="kf-i" role="option" id="kf-o'+n+'" data-n="'+n+'">'+svg(k)+'<span class="kf-t">'+mark(r.it[2],r.pos)+'</span><span class="kf-m">'+esc(r.alias?'also: '+r.alias:r.it[3])+'</span></div>'});
    if(g.length>shown.length)html+='<div class="kf-more">+'+(g.length-shown.length)+' more: keep typing to narrow it</div>';
    html+='</div>'});
  R.innerHTML=html||'<div class="kf-empty">Nothing matches &ldquo;'+esc(Q.value.trim())+'&rdquo;.</div>';
  if(!words.length)R.insertAdjacentHTML('beforeend','<div class="kf-empty">Type to find a practice, a problem or a term.</div>');
  paint()}
function paint(){R.querySelectorAll('.kf-i').forEach(function(e,i){e.classList.toggle('on',i===sel);e.setAttribute('aria-selected',i===sel)});
  var on=R.querySelector('.kf-i.on');Q.setAttribute('aria-activedescendant',on?on.id:'');if(on)on.scrollIntoView({block:'nearest'})}
function pick(n){var it=items[n];if(!it)return;close(true);location.hash='#'+(it[0]==='v'?it[1]:it[0]+'/'+it[1])}
function open(){if(!box.hidden)return;var a=document.activeElement;if(a&&a!==document.body)from=a;box.hidden=false;scrim.hidden=false;Q.value='';run();Q.focus()}
function close(keepFocus){if(box.hidden)return;box.hidden=true;scrim.hidden=true;
  if(!keepFocus&&from&&document.contains(from)){try{from.focus({preventScroll:true})}catch(e){}}from=null}
window.kfOpen=open;
if(btn)btn.addEventListener('click',open);
scrim.addEventListener('click',function(){close()});
Q.addEventListener('input',run);
Q.addEventListener('keydown',function(e){
  if(e.key==='ArrowDown'){e.preventDefault();sel=Math.min(items.length-1,sel+1);paint()}
  else if(e.key==='ArrowUp'){e.preventDefault();sel=Math.max(0,sel-1);paint()}
  else if(e.key==='Enter'){e.preventDefault();pick(sel)}
  else if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close()}
  else if(e.key==='Tab'){e.preventDefault()}});
R.addEventListener('mousemove',function(e){var i=e.target.closest('.kf-i');if(i&&+i.dataset.n!==sel){sel=+i.dataset.n;paint()}});
R.addEventListener('click',function(e){var i=e.target.closest('.kf-i');if(i)pick(+i.dataset.n)});
document.addEventListener('keydown',function(e){if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();box.hidden?open():close()}},true);
})();</script>""" % json.dumps(ICON)
