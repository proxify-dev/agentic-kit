#!/usr/bin/env python3
"""Render a dev-mode story.json as a two-pane explorer (tree on the left, selected item on the right).

    python3 render_explorer.py story.json [-o explorer.html]

One self-contained HTML file: inline CSS, vanilla JS, the JSON embedded. The page builds its DOM with
textContent only, so strings from the JSON are never parsed as HTML. The `v13` key is dropped before embedding.
"""
import argparse
import json
import sys

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&amp;family=Poppins:wght@600;700&amp;display=swap" rel="stylesheet">
<style>
:root{
  --p600:#5258FB; --p700:#3C3ED4; --p25:#F5F5FF; --p100:#D5D5FE; --p900:#171559;
  --midnight:#15172F; --g25:#FCFCFD; --g50:#F9FAFB; --g100:#F4F5F7; --g200:#EAECF0; --g300:#D0D5DD; --g400:#949BAD;
  --g500:#667085; --g600:#475467; --g700:#344054;
  --ok:#039855; --amber:#CA8504; --warn:#B54708; --warnbg:#FEFBF5; --warnline:#FBDEA5; --purple:#9333EA; --err:#B42318;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;font-size:13px;line-height:1.5;color:var(--midnight);background:#fff;
  display:grid;grid-template-rows:auto minmax(0,1fr);grid-template-columns:minmax(0,1fr);overflow:hidden}
button,input{font:inherit}
.bar{display:flex;align-items:center;gap:16px;background:var(--midnight);color:#fff;padding:8px 14px;min-height:44px;white-space:nowrap}
.bar h1{font-family:Poppins,Inter,sans-serif;font-weight:600;font-size:15px;margin:0}
.bar h1 span{color:var(--p100);font-weight:400;font-family:Inter,sans-serif}
.facts{color:var(--p100);overflow:hidden;text-overflow:ellipsis;flex:1 1 0;min-width:0}
.facts b{color:#fff;font-weight:600}
.facts span{margin-right:14px}
.pill{font-size:11px;font-weight:500;color:#FBDEA5;border:1px solid #FBDEA5;border-radius:999px;padding:1px 8px}
.bar input{width:220px;background:#262841;border:1px solid #3C3F5E;color:#fff;border-radius:6px;padding:4px 9px}
.bar input::placeholder{color:var(--g400)}
.bar input:focus{outline:2px solid var(--p600);border-color:var(--p600)}
.chips{display:flex;gap:6px}
.chips button{background:transparent;color:var(--p100);border:1px solid #3C3F5E;border-radius:999px;padding:2px 10px;cursor:pointer;font-size:12px}
.chips button[aria-pressed=true]{background:var(--p600);border-color:var(--p600);color:#fff}
.panes{display:grid;grid-template-columns:340px minmax(0,1fr);min-height:0}
#tree{background:var(--g50);border-right:1px solid var(--g200);overflow:auto;padding:6px 0;outline:none}
#detail{overflow:auto;padding:24px 32px 48px}
.row{display:flex;align-items:center;gap:6px;padding:3px 10px 3px 8px;cursor:pointer;color:var(--g700);white-space:nowrap}
.row:hover{background:var(--g100)}
.row.sel{background:var(--p100);color:var(--p900)}
#tree:focus-within .row.sel{background:var(--p600);color:#fff}
.row .chev{width:12px;color:var(--g600);flex:none;text-align:center;font-size:12px}
.row.sel .chev{color:inherit}
.row .lab{overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}
.row.group .lab{font-weight:600}
.row.part .lab{font-family:Poppins,Inter,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.row .mk{flex:none;font-size:12px;font-weight:600}
.mk.nm{color:var(--err)} .mk.pt{color:var(--amber)}
#tree:focus-within .row.sel .mk{color:#fff}
.badge{flex:none;font-size:11px;font-weight:500;border-radius:999px;padding:0 7px;background:var(--g200);color:var(--g600)}
.badge.hot{background:var(--warnbg);color:var(--warn);border:1px solid var(--warnline)}
.cd{width:12px;flex:none;text-align:center;font-size:12px;line-height:1}
.cd.solid{color:var(--ok)} .cd.heuristic{color:var(--amber)} .cd.guess{color:var(--purple)}
#tree:focus-within .row.sel .cd{color:#fff}
.none{color:var(--g500);padding:16px;font-size:13px}
/* detail */
.crumb{color:var(--g500);font-size:12px;margin:0 0 6px}
#detail h2{font-family:Poppins,Inter,sans-serif;font-weight:700;font-size:22px;line-height:1.3;margin:0 0 10px}
.chip{display:inline-block;font-size:12px;font-weight:500;border:1px solid;border-radius:999px;padding:1px 9px;margin:0 6px 0 0;vertical-align:middle}
.chip.solid{color:var(--ok);background:#F6FEF9;border-color:#C4F3D8}
.chip.heuristic{color:var(--warn);background:var(--warnbg);border-color:var(--warnline)}
.chip.guess{color:#581C87;background:#FCFAFF;border-color:#E1CDFF}
.chip.nm{color:var(--err);background:#FFF5F6;border-color:#FDA29B}
.chip.pt{color:var(--warn);background:var(--warnbg);border-color:var(--warnline)}
.chip.ok{color:var(--g600);background:var(--g50);border-color:var(--g200)}
.text{font-size:16px;font-weight:300;line-height:1.55;margin:12px 0 18px;max-width:760px}
.text.lead{font-weight:400;color:var(--p900)}
.text.lead.cant{color:var(--warn)}
dl.how{display:grid;grid-template-columns:150px 1fr;gap:0;margin:0 0 20px;max-width:860px;border-top:1px solid var(--g200)}
dl.how dt,dl.how dd{margin:0;padding:7px 0;border-bottom:1px solid var(--g200)}
dl.how dt{font-weight:600;color:var(--g600);font-size:12px;text-transform:uppercase;letter-spacing:.04em;padding-top:9px}
dl.how dd{font-size:13.5px}
dl.how dd.cav{color:var(--warn);background:var(--warnbg)}
dl.how dt.cav{color:var(--warn);background:var(--warnbg)}
dl.how dd.code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.muted{color:var(--g500)}
.sub{font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--g600);margin:22px 0 6px}
.nums{display:flex;flex-wrap:wrap;gap:6px 28px;margin:6px 0 18px}
.nums b{display:block;font-family:Poppins,Inter,sans-serif;font-size:22px;line-height:1.2;color:var(--p600)}
.nums span{font-size:12px;color:var(--g600)}
table.t{border-collapse:collapse;font-size:13px;margin:4px 0 18px;max-width:900px;width:100%}
table.t caption{text-align:left;font-weight:600;color:var(--g700);padding-bottom:4px}
table.t th,table.t td{text-align:left;padding:5px 10px;border-bottom:1px solid var(--g200);vertical-align:top;overflow-wrap:break-word}
table.t th{font-size:12px;color:var(--g600);background:var(--g50)}
table.t .n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;width:1%}
table.t tbody tr:nth-child(even){background:var(--g25)}
blockquote{margin:0 0 4px;padding:8px 12px;background:var(--p25);border:1px solid var(--p100);border-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere;max-width:760px}
.cap{font-size:12px;color:var(--g500);margin:0 0 12px}
.links{list-style:none;margin:0;padding:0;max-width:860px}
.links li{border-bottom:1px solid var(--g200)}
.links a{display:flex;gap:8px;align-items:center;padding:6px 2px;color:var(--midnight);text-decoration:none;cursor:pointer}
.links a:hover{background:var(--g50)}
.links .r{margin-left:auto}
.tag{display:inline-block;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;background:var(--g100);color:var(--g700);border-radius:4px;padding:1px 7px;margin:0 6px 4px 0}
.ladder{margin:6px 0 16px}
kbd{font:inherit;font-size:11px;border:1px solid var(--g300);border-bottom-width:2px;border-radius:4px;padding:0 5px;background:#fff;color:var(--g600)}
.hint{color:var(--g500);font-size:12px;margin-top:28px}
@media (max-width:900px){.panes{grid-template-columns:260px 1fr}.facts{display:none}}
@media print{body{overflow:visible;display:block}.panes{display:block}#tree{display:none}.bar input,.chips{display:none}}
</style>
</head>
<body>
<header class="bar">
  <h1 id="title"></h1>
  <div class="facts" id="facts"></div>
  <span class="pill" id="priv" hidden>Detail version: not for sharing</span>
  <div class="chips" id="chips" role="group" aria-label="Filter"></div>
  <input id="q" type="search" placeholder="Search  ( / )" aria-label="Search">
</header>
<div class="panes">
  <nav id="tree" tabindex="0" aria-label="Findings"></nav>
  <main id="detail" tabindex="-1"></main>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
"use strict";
var D = JSON.parse(document.getElementById('data').textContent);
var DEV = D.dev || {};
var CONF = ['solid','heuristic','guess'];
var GLYPH = {solid:'\u25CF', heuristic:'\u25D0', guess:'\u25CB'};

function el(tag, cls, text){ var n = document.createElement(tag); if(cls) n.className = cls; if(text != null) n.textContent = String(text); return n; }
function arr(x){ return Array.isArray(x) ? x : []; }
function conf(how){ if(!how || typeof how !== 'object') return null; return CONF.indexOf(how.confidence) >= 0 ? how.confidence : 'guess'; }
function status(s){ s = String(s||'').toLowerCase(); return s === 'not measured' ? 'nm' : s === 'partial' ? 'pt' : s === 'measured' ? 'ok' : ''; }
function derive(t){
  t = String(t||'').replace(/\s+/g,' ').trim();
  var i = t.indexOf(':');
  if(i > 0 && i <= 60) return t.slice(0,i);
  var w = t.split(' ');
  return w.slice(0,6).join(' ') + (w.length > 6 ? '\u2026' : '');
}

/* ---- model ---- */
var nodes = {}, order = [];
function mk(id, parent, o){
  o.id = id; o.parent = parent; o.children = [];
  nodes[id] = o; order.push(id);
  if(parent) nodes[parent].children.push(id);
  return o;
}
mk('root', null, {kind:'root', label:''});
mk('overview', 'root', {kind:'overview', label:'Overview'});
var gapsN = arr(DEV.gaps);
if(gapsN.length){
  mk('gaps', 'root', {kind:'group', label:'Gaps', group:'gap', top:true});
  gapsN.forEach(function(g,i){ mk('g'+i, 'gaps', {kind:'gap', label:g.label || g.title, data:g, text:g.why}); });
}
var partOf = null, partId = null;
arr(D.sections).forEach(function(s,i){
  var p = s.part || '';
  if(p !== partOf || partId === null){
    partOf = p;
    partId = 'part'+i;
    mk(partId, 'root', {kind:'group', label:p || 'Story', part:true});
  }
  var sid = 's'+i;
  var sec = mk(sid, partId, {kind:'section', label:s.label || s.title, data:s, how:s.how, text:s.lead});
  if(s.lead) mk(sid+'-lead', sid, {kind:'item', sub:'lead', label:'Lead: '+derive(s.lead), title:'Lead', text:s.lead, how:s.how, data:s, parentTitle:s.title});
  arr(s.points).forEach(function(p,j){
    var o = (p && typeof p === 'object') ? p : {text:p};
    mk(sid+'-p'+j, sid, {kind:'item', sub:'point', label:o.label || derive(o.text), title:o.label || derive(o.text), text:o.text, how:o.how, parentTitle:s.title});
  });
  if(s.table && typeof s.table === 'object') mk(sid+'-t', sid, {kind:'item', sub:'table', label:'Table: '+(s.table.caption||''), title:s.table.caption||'Table', table:s.table, how:s.table.how, parentTitle:s.title});
  arr(s.ladder).forEach(function(l,j){
    mk(sid+'-l'+j, sid, {kind:'item', sub:'ladder', label:'Step: '+l.step, title:l.step, ladder:l, text:l.evidence, how:l.how, parentTitle:s.title});
  });
});
var covN = arr(DEV.coverage);
if(covN.length){
  mk('coverage', 'root', {kind:'group', label:'Coverage', top:true, group:'cov'});
  var areas = {};
  covN.forEach(function(c,k){
    var a = c.area || 'Other';
    if(!areas[a]) areas[a] = mk('cov-a-'+Object.keys(areas).length, 'coverage', {kind:'group', label:a, area:true});
    mk('c'+k, areas[a].id, {kind:'cov', label:c.signal, data:c, how:{confidence:c.confidence}, st:status(c.status), text:c.source});
  });
}
/* attach flags to leaves */
order.forEach(function(id){
  var n = nodes[id];
  n.conf = conf(n.how);
  if(n.kind === 'cov') n.conf = conf({confidence:n.data.confidence});
  n.st = n.st || status(n.how && n.how.status);
  if(n.kind === 'gap') n.st = '';
});
function isLeaf(n){ return n.children.length === 0 && n.kind !== 'group' && n.kind !== 'section'; }
function flagged(n){ return n.st === 'nm' || n.st === 'pt' || n.conf === 'guess'; }
function descLeaves(id){
  var out = [];
  (function walk(i){ var n = nodes[i]; if(n.id !== id && (n.kind === 'item' || n.kind === 'cov' || n.kind === 'gap')) out.push(n); n.children.forEach(walk); })(id);
  return out;
}
function searchText(n){
  var h = n.how || {};
  var d = n.data || {};
  return [n.label, n.text, h.source, h.rule, h.raw, h.caveat, d.source, d.why, d.title, d.signal].join(' ').toLowerCase();
}

/* ---- filter state ---- */
var filter = 'all', query = '', collapsed = {}, selected = 'overview', visible = [];
var PRED = {
  all: function(){ return true; },
  gaps: function(n){ return n.st === 'nm' || n.st === 'pt' || n.kind === 'gap'; },
  guess: function(n){ return n.conf === 'guess'; },
  heuristic: function(n){ return n.conf === 'heuristic'; }
};
function leafMatch(n){
  if(!PRED[filter](n)) return false;
  return !query || searchText(n).indexOf(query) >= 0;
}
function nodeVisible(n){
  if(n.id === 'root') return true;
  var leafish = n.kind === 'item' || n.kind === 'cov' || n.kind === 'gap' || n.kind === 'overview';
  if(leafish) return leafMatch(n);
  if(filter === 'all' && !query) return true;
  return n.children.some(function(c){ return nodeVisible(nodes[c]); });
}

/* ---- tree rendering ---- */
var treeEl = document.getElementById('tree');
function renderTree(){
  treeEl.textContent = '';
  visible = [];
  var active = filter !== 'all' || !!query;
  function walk(id, depth){
    var n = nodes[id];
    if(id !== 'root'){
      if(!nodeVisible(n)) return;
      visible.push(id);
      var hasKids = n.children.length > 0;
      var row = el('div', 'row' + (id === selected ? ' sel' : '') + (n.kind === 'group' ? ' group' : '') + (n.part ? ' part' : ''));
      row.dataset.id = id;
      row.style.paddingLeft = (8 + depth * 14) + 'px';
      row.setAttribute('role', 'treeitem');
      var chev = el('span', 'chev', hasKids ? (collapsed[id] && !active ? '\u25B8' : '\u25BE') : '');
      chev.dataset.toggle = hasKids ? id : '';
      row.appendChild(chev);
      var cd = el('span', 'cd' + (n.conf ? ' ' + n.conf : ''), n.conf ? GLYPH[n.conf] : '');
      if(n.kind === 'item' || n.kind === 'cov') row.appendChild(cd);
      row.appendChild(el('span', 'lab', n.label));
      if(n.kind === 'item' || n.kind === 'cov'){
        if(n.st === 'nm') row.appendChild(el('span', 'mk nm', '\u2717'));
        else if(n.st === 'pt') row.appendChild(el('span', 'mk pt', '~'));
      }
      if(hasKids && (n.kind === 'section' || n.kind === 'group')){
        var leaves = descLeaves(id);
        var hot = n.group === 'gap' ? 0 : leaves.filter(flagged).length;
        if(n.group === 'gap') row.appendChild(el('span', 'badge', leaves.length));
        else if(hot) row.appendChild(el('span', 'badge hot', hot));
      }
      treeEl.appendChild(row);
      if(hasKids && collapsed[id] && !active) return;
    }
    n.children.forEach(function(c){ walk(c, id === 'root' ? 0 : depth + 1); });
  }
  walk('root', 0);
  if(!visible.length) treeEl.appendChild(el('div', 'none', 'Nothing matches.'));
}

/* ---- detail rendering ---- */
var detailEl = document.getElementById('detail');
function chipFor(n){
  var f = document.createDocumentFragment();
  if(n.conf) f.appendChild(el('span', 'chip ' + n.conf, n.conf));
  if(n.st === 'nm') f.appendChild(el('span', 'chip nm', 'not measured'));
  else if(n.st === 'pt') f.appendChild(el('span', 'chip pt', 'partial'));
  else if(n.st === 'ok') f.appendChild(el('span', 'chip ok', 'measured'));
  return f;
}
function crumbs(n){
  var parts = [], p = n.parent;
  while(p && p !== 'root'){ parts.unshift(nodes[p].label); p = nodes[p].parent; }
  return parts.join(' \u203A ');
}
function head(n, title){
  detailEl.appendChild(el('div', 'crumb', crumbs(n)));
  var h = el('h2'); h.appendChild(chipFor(n)); h.appendChild(document.createTextNode(' ' + (title || n.label)));
  detailEl.appendChild(h);
}
function howGrid(how){
  if(!how || typeof how !== 'object'){
    detailEl.appendChild(el('p', 'muted', 'No provenance recorded for this item.'));
    return;
  }
  var dl = el('dl', 'how');
  [['Source','source'],['Rule','rule'],['Numbers','raw']].forEach(function(p){
    if(how[p[1]]){ dl.appendChild(el('dt', '', p[0])); dl.appendChild(el('dd', '', how[p[1]])); }
  });
  if(how.caveat){ dl.appendChild(el('dt', 'cav', 'Caveat')); dl.appendChild(el('dd', 'cav', how.caveat)); }
  if(how.code){ dl.appendChild(el('dt', '', 'Code')); dl.appendChild(el('dd', 'code', how.code)); }
  detailEl.appendChild(dl);
}
function covLine(c){
  if(!c || !c.line) return;
  var dl = el('dl', 'how');
  dl.appendChild(el('dt', '', 'Coverage'));
  dl.appendChild(el('dd', '', c.line));
  detailEl.appendChild(dl);
}
function numeric(t){ return /^[\d.,~%\s]+$/.test(String(t).trim()) && String(t).trim() !== ''; }
function tableEl(t){
  var cols = arr(t.columns), rows = arr(t.rows).filter(Array.isArray);
  var tb = el('table', 't');
  if(t.caption) tb.appendChild(el('caption', '', t.caption));
  var isn = cols.map(function(_,i){ return i > 0 && rows.length && rows.every(function(r){ return i >= r.length || numeric(r[i]); }); });
  var tr = el('tr'); cols.forEach(function(c,i){ tr.appendChild(el('th', isn[i] ? 'n' : '', c)); });
  var th = el('thead'); th.appendChild(tr); tb.appendChild(th);
  var tbd = el('tbody');
  rows.forEach(function(r){ var x = el('tr'); r.forEach(function(c,i){ x.appendChild(el('td', isn[i] ? 'n' : '', c)); }); tbd.appendChild(x); });
  tb.appendChild(tbd);
  return tb;
}
function numsEl(items){
  var d = el('div', 'nums');
  arr(items).forEach(function(x){ if(!x) return; var c = el('div'); c.appendChild(el('b', '', x.value)); c.appendChild(el('span', '', x.label)); d.appendChild(c); });
  return d;
}
function linkList(ids){
  var ul = el('ul', 'links');
  ids.forEach(function(id){
    var n = nodes[id], li = el('li'), a = el('a');
    a.dataset.go = id;
    if(n.conf) a.appendChild(el('span', 'cd ' + n.conf, GLYPH[n.conf]));
    a.appendChild(el('span', '', n.label));
    var r = el('span', 'r'); r.appendChild(chipFor(n)); a.appendChild(r);
    li.appendChild(a); ul.appendChild(li);
  });
  return ul;
}
function renderDetail(){
  var n = nodes[selected];
  detailEl.textContent = '';
  detailEl.scrollTop = 0;
  if(n.kind === 'overview') return overview();
  if(n.kind === 'gap'){
    head(n);
    var g = n.data;
    detailEl.appendChild(el('p', 'text', g.why));
    detailEl.appendChild(el('div', 'sub', 'Affects'));
    var aff = el('div'); arr(g.affects).forEach(function(a){ aff.appendChild(el('span', 'tag', a)); });
    if(!arr(g.affects).length) aff.appendChild(el('span', 'muted', 'nothing listed'));
    detailEl.appendChild(aff);
    var words = (String(g.title) + ' ' + String(g.why)).toLowerCase().match(/[a-z]{5,}/g) || [];
    var rel = order.filter(function(id){
      var c = nodes[id]; if(c.kind !== 'cov') return false;
      var s = (c.label + ' ' + c.text).toLowerCase();
      return words.some(function(w){ return s.indexOf(w) >= 0; });
    });
    detailEl.appendChild(el('div', 'sub', 'Related coverage'));
    if(rel.length) detailEl.appendChild(linkList(rel)); else detailEl.appendChild(el('p', 'muted', 'No coverage row mentions these words.'));
    return;
  }
  if(n.kind === 'cov'){
    head(n);
    var c = n.data, dl = el('dl', 'how');
    [['Area', c.area],['Signal', c.signal],['Source', c.source],['Confidence', c.confidence],['Status', c.status]].forEach(function(p){
      dl.appendChild(el('dt', '', p[0])); dl.appendChild(el('dd', '', p[1] == null ? '' : p[1]));
    });
    detailEl.appendChild(dl);
    return;
  }
  if(n.kind === 'section'){
    var s = n.data;
    head(n, s.title);
    if(s.lead) detailEl.appendChild(el('p', 'text lead' + (s.can_tell === false ? ' cant' : ''), s.lead));
    howGrid(s.how);
    covLine(s.coverage);
    if(arr(s.numbers).length) detailEl.appendChild(numsEl(s.numbers));
    if(s.table) detailEl.appendChild(tableEl(s.table));
    arr(s.examples).forEach(function(x){
      if(!x || !x.text) return;
      if(x.label) detailEl.appendChild(el('div', 'sub', x.label));
      detailEl.appendChild(el('blockquote', '', x.text));
      if(x.meta) detailEl.appendChild(el('div', 'cap', x.meta));
    });
    if(n.children.length){
      detailEl.appendChild(el('div', 'sub', 'In this section'));
      detailEl.appendChild(linkList(n.children));
    }
    return;
  }
  if(n.kind === 'group'){
    head(n);
    detailEl.appendChild(linkList(n.children));
    return;
  }
  /* item */
  head(n, n.title);
  if(n.sub === 'table'){ detailEl.appendChild(tableEl(n.table)); }
  else if(n.sub === 'ladder'){
    detailEl.appendChild(el('p', 'text', (n.ladder.done === true ? 'Done. ' : 'Not done. ') + (n.ladder.evidence || '')));
  } else if(n.text){
    detailEl.appendChild(el('p', 'text' + (n.sub === 'lead' ? ' lead' : ''), n.text));
  }
  howGrid(n.how);
}
function overview(){
  detailEl.appendChild(el('div', 'crumb', 'Overview'));
  detailEl.appendChild(el('h2', '', 'How ' + (D.person ? D.person : 'this person') + ' works with agents'));
  if(arr(D.at_a_glance).length) detailEl.appendChild(numsEl(D.at_a_glance));
  if(D.in_one_paragraph) detailEl.appendChild(el('p', 'text', D.in_one_paragraph));
  var cov = {solid:0, heuristic:0, guess:0};
  covN.forEach(function(c){ var k = conf({confidence:c.confidence}); cov[k]++; });
  var dl = el('dl', 'how');
  if(covN.length){
    dl.appendChild(el('dt', '', 'Coverage'));
    dl.appendChild(el('dd', '', covN.length + ' signals: ' + cov.solid + ' solid, ' + cov.heuristic + ' heuristic, ' + cov.guess + ' guess. ' +
      covN.filter(function(c){ return status(c.status) === 'nm'; }).length + ' not measured, ' +
      covN.filter(function(c){ return status(c.status) === 'pt'; }).length + ' partial.'));
  }
  if(gapsN.length){ dl.appendChild(el('dt', '', 'Gaps')); dl.appendChild(el('dd', '', gapsN.length + ' listed, worst first. Start there.')); }
  arr(DEV.run).forEach(function(r){ if(r){ dl.appendChild(el('dt', '', r.label)); dl.appendChild(el('dd', '', r.value)); } });
  detailEl.appendChild(el('div', 'sub', 'The scan'));
  detailEl.appendChild(dl);
  var hint = el('p', 'hint');
  [['\u2191','\u2193'],['\u2190','\u2192'],['/']].forEach(function(ks, i){
    ks.forEach(function(k){ hint.appendChild(el('kbd', '', k)); hint.appendChild(document.createTextNode(' ')); });
    hint.appendChild(document.createTextNode(['move  ', 'collapse or expand  ', 'search'][i] + '   '));
  });
  detailEl.appendChild(hint);
}

/* ---- selection ---- */
function select(id, fromHash){
  if(!nodes[id]) id = 'overview';
  selected = id;
  var p = nodes[id].parent;
  while(p && p !== 'root'){ delete collapsed[p]; p = nodes[p].parent; }
  renderTree();
  var row = treeEl.querySelector('.row.sel');
  if(row) row.scrollIntoView({block:'nearest'});
  renderDetail();
  if(!fromHash && location.hash !== '#' + id) history.replaceState(null, '', '#' + id);
}
treeEl.addEventListener('click', function(e){
  var t = e.target.closest('.row'); if(!t) return;
  var id = t.dataset.id;
  if(e.target.dataset && e.target.dataset.toggle){ collapsed[id] = !collapsed[id]; renderTree(); return; }
  select(id);
});
detailEl.addEventListener('click', function(e){
  var a = e.target.closest('[data-go]'); if(a) select(a.dataset.go);
});
window.addEventListener('hashchange', function(){ var id = location.hash.slice(1); if(id !== selected) select(id, true); });

/* ---- keyboard ---- */
document.addEventListener('keydown', function(e){
  var q = document.getElementById('q');
  if(e.key === '/' && document.activeElement !== q){ e.preventDefault(); q.focus(); q.select(); return; }
  if(document.activeElement === q){ if(e.key === 'Escape'){ q.blur(); treeEl.focus(); } else if(e.key === 'ArrowDown'){ e.preventDefault(); treeEl.focus(); } return; }
  if(e.metaKey || e.ctrlKey || e.altKey) return;
  var i = visible.indexOf(selected), n = nodes[selected];
  if(e.key === 'ArrowDown'){ e.preventDefault(); if(i < visible.length - 1) select(visible[i+1]); }
  else if(e.key === 'ArrowUp'){ e.preventDefault(); if(i > 0) select(visible[i-1]); }
  else if(e.key === 'ArrowLeft'){
    e.preventDefault();
    if(n.children.length && !collapsed[selected]){ collapsed[selected] = true; renderTree(); }
    else if(n.parent && n.parent !== 'root') select(n.parent);
  } else if(e.key === 'ArrowRight'){
    e.preventDefault();
    if(n.children.length && collapsed[selected]){ delete collapsed[selected]; renderTree(); }
    else if(n.children.length && i < visible.length - 1) select(visible[i+1]);
  }
});

/* ---- top bar ---- */
var person = (D.person || '').trim();
var t = document.getElementById('title');
t.appendChild(document.createTextNode('How ' + (person || 'this person') + ' works with agents '));
t.appendChild(el('span', '', '\u00B7 dev'));
var f = document.getElementById('facts');
var bits = [];
if(D.window_days) bits.push(el('span', '', 'last ' + D.window_days + ' days'));
var runs = arr(DEV.run).filter(function(r){ return r && /file|record|copied|scan/i.test(r.label); });
if(!runs.length) runs = arr(DEV.run).slice(0,4);
runs.forEach(function(r){ var s = el('span'); s.appendChild(el('b', '', r.value)); s.appendChild(document.createTextNode(' ' + r.label)); bits.push(s); });
bits.forEach(function(b){ f.appendChild(b); });
if(D.privacy !== 'masked') document.getElementById('priv').hidden = false;
document.title = t.textContent;

var chipsEl = document.getElementById('chips');
var counts = {
  all: 0,
  gaps: order.filter(function(i){ return PRED.gaps(nodes[i]) && (nodes[i].kind === 'item' || nodes[i].kind === 'cov' || nodes[i].kind === 'gap'); }).length,
  guess: order.filter(function(i){ return (nodes[i].kind === 'item' || nodes[i].kind === 'cov') && PRED.guess(nodes[i]); }).length,
  heuristic: order.filter(function(i){ return (nodes[i].kind === 'item' || nodes[i].kind === 'cov') && PRED.heuristic(nodes[i]); }).length
};
[['all','All'],['gaps','Gaps'],['guess','Guess'],['heuristic','Heuristic']].forEach(function(p){
  var b = el('button', '', p[0] === 'all' ? p[1] : p[1] + ' ' + counts[p[0]]);
  b.type = 'button'; b.dataset.f = p[0]; b.setAttribute('aria-pressed', p[0] === 'all' ? 'true' : 'false');
  b.addEventListener('click', function(){
    filter = p[0];
    Array.prototype.forEach.call(chipsEl.children, function(x){ x.setAttribute('aria-pressed', x.dataset.f === filter ? 'true' : 'false'); });
    renderTree();
  });
  chipsEl.appendChild(b);
});
document.getElementById('q').addEventListener('input', function(e){ query = e.target.value.trim().toLowerCase(); renderTree(); });

select(location.hash.slice(1) || 'overview', true);
})();
</script>
</body>
</html>
"""


def render(story):
    data = {k: v for k, v in story.items() if k != "v13"}
    person = (data.get("person") or "").strip()
    title = "How %s works with agents \u00b7 dev" % person if person else "How this person works with agents \u00b7 dev"
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return PAGE.replace("__TITLE__", title).replace("__DATA__", blob)


def main():
    ap = argparse.ArgumentParser(description="Render a dev-mode story.json as a two-pane explorer.")
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
