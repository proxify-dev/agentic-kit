"""The one table every view uses for a list whose rows open the side panel, with the few pieces that sit around it: a
strip of big numbers, a heading for a group of rows, links that jump to a group, a meter. Not a view: frame.py only loads view_*.py.

Same look as the Review's "What your agent knows" grid: a white box with a quiet header line, one row per item, a chevron
at the end of a row that opens something. A row marked `pk="<kind>:<id>"` opens the shared side panel (panel_kit.py) the
way a card does (card_kit.card): a click, or Enter, on the row. A row with `more` opens in place instead: its first cell
is a button, and `more` sits under the row, in the document from the start (hidden until found, so the browser's find
reaches it and opens it). A column named in `sort` gets a header button that orders the rows by a number each row carries
(`data-<key>`). JS does those two things and nothing else. Cells are html the view has already escaped; this file only
lays them out. Stdlib only.
"""
import html
import re

esc = lambda s: html.escape("" if s is None else str(s))  # noqa: E731


def code(s):
    """Escaped text in which `code` between backticks is shown as code (a file name, a command)."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(s))

SORT = ('<svg class="kt-sorti" viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.4" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 4.5 6 2l2.5 2.5M3.5 7.5 6 10l2.5-2.5"/></svg>')
CHEVRON = ('<svg class="kt-chev" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" '
           'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5"/></svg>')


def table(heads, rows, label="", widths="minmax(0,1fr)", sort=None):
    """heads: column names (an empty one is a column with no title). rows: [{"cells": [html, ...], "pk": "m:<id>" or None,
    "more": html opened in place under the row, "attrs": extra attributes, "cls": extra class}]. widths: a CSS
    grid-template-columns for this table (the last column, the chevron, is added here), so each list sets how much room each
    column gets; one column by default. sort: {column index: key}: that header orders the rows by their `data-<key>`."""
    style = f' style="--kt-cols:{esc(widths)}"'
    sort = sort or {}
    head = "".join(f'<th scope="col" aria-sort="none"><button class="kt-sort" type="button" data-sort="{esc(sort[i])}">{esc(h)}'
                   f'{SORT}</button></th>' if i in sort else f'<th scope="col">{esc(h)}</th>' for i, h in enumerate(heads))
    body = []
    for n, r in enumerate(rows):
        pk, more = r.get("pk"), r.get("more")
        open_ = f' data-pk="{esc(pk)}" role="button" tabindex="0" aria-label="{esc(r.get("label") or "Details")}"' if pk else ""
        cells = [f"<td>{c}</td>" for c in r["cells"]]
        oid = f'kt-o-{esc(r.get("oid") or n)}'
        if more:   # the first cell opens the row; the rest of the row does too (the script)
            cells[0] = (f'<td><button class="kt-tog" type="button" aria-expanded="false" aria-controls="{oid}">{r["cells"][0]}'
                        f'</button></td>')
            open_ += ' data-open="false"'
        body.append(f'<tr class="kt-row{" " + r["cls"] if r.get("cls") else ""}" data-i="{n}"{open_}{r.get("attrs", "")}>{"".join(cells)}'
                    f'<td class="kt-go">{CHEVRON if pk or more else ""}</td></tr>')
        if more:
            body.append(f'<tr class="kt-more"><td><div class="kt-open" id="{oid}" hidden="until-found"><div class="kt-in">{more}'
                        f'</div></div></td></tr>')
    cap = f"<caption>{esc(label)}</caption>" if label else ""
    return (f'<div class="kt"{style}><table>{cap}<thead><tr>{head}<th aria-hidden="true"></th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def name(text, sub=""):
    """The first cell: the item's name, and under it one quiet line."""
    return f'<b class="kt-n">{code(text)}</b>' + (f'<span class="kt-s">{code(sub)}</span>' if sub else "")


def strip(items):
    """A row of big numbers over one line each, above a list. items: [(number, what it counts, tone)]; tone "warn" or ""."""
    return '<div class="ks">' + "".join(
        f'<div{" class=" + chr(34) + t + chr(34) if t else ""}><b>{esc(n)}</b><span>{esc(w)}</span></div>' for n, w, t in items) + "</div>"


def group(title, count, id_):
    """The heading of one group of rows: the name, how many, and an id the jump links point at."""
    return f'<h3 class="kg" id="{esc(id_)}">{esc(title)} <span>{esc(count)}</span></h3>'


def jump(items):
    """Links to the groups below. items: [(label, count, href)]."""
    return '<nav class="kj">' + "".join(
        f'<a class="kp-chip" href="{esc(h)}">{esc(l)} <b>{esc(n)}</b></a>' for l, n, h in items) + "</nav>"


def meter(pct, text):
    """A thin bar filled to pct percent, with its figure beside it (the Built tab's meter)."""
    return (f'<span class="km" role="img" aria-label="{esc(text)}"><i style="width:{esc(pct)}%"></i></span>'
            f'<span class="kt-dim">{esc(text)}</span>')


CSS = """
.ks{display:flex;flex-wrap:wrap;gap:8px 32px;margin:0 0 24px;padding:16px 0;border-top:1px solid #EAECF0;border-bottom:1px solid #EAECF0}
.ks div{min-width:140px}.ks b{display:block;font-size:24px;font-weight:600;line-height:1.2}
.ks span{font-size:12px;color:#475467}.ks .warn b{color:#B54708}
.kg{margin:32px 0 0;font-size:18px;font-weight:600;line-height:1.3;scroll-margin-top:136px}
.kg span{font-size:12px;font-weight:500;color:#667085;margin-left:4px}
.kj{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 0}.kj b{margin-left:4px;font-weight:600}
.km{display:block;height:6px;border-radius:999px;background:#EEEFFE;overflow:hidden;margin:0 0 4px}
.km i{display:block;height:100%;border-radius:999px;background:#5258FB}
.kt{background:#FFFFFF;border:1px solid #EAECF0;border-radius:16px;overflow:hidden;margin:16px 0 0}
.kt table{width:100%;border-collapse:collapse;table-layout:fixed}
.kt caption{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.kt thead tr,.kt tbody tr{display:grid;grid-template-columns:var(--kt-cols) 20px;gap:8px;align-items:center;padding:0 20px}
.kt thead tr{background:#F9FAFB;border-bottom:1px solid #EAECF0}
.kt th{padding:12px 0;text-align:left;font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#667085}
.kt td{padding:12px 0;min-width:0;font-size:14px;line-height:1.5;color:#15172F;overflow-wrap:anywhere}
.kt tbody tr+tr{border-top:1px solid #EAECF0}
.kt tbody tr[data-pk]{cursor:pointer;transition:background-color .12s}
.kt tbody tr[data-pk]:hover{background:#F9FAFB}
.kt tbody tr[data-pk]:focus-visible{outline:2px solid #5258FB;outline-offset:-2px}
.kt .kt-n{display:block;font-weight:600;color:#15172F}
.kt .kt-s{display:block;font-size:12px;color:#667085}
.kt td.kt-go{padding:0;color:#949BAD;display:flex;justify-content:flex-end}
.kt tbody tr[data-pk]:hover .kt-chev{color:#3C3ED4}
.kt .kt-dim{font-size:12px;color:#667085}
.kt code,.ks code{font:400 12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:#15172F;background:#F2F4F7;border-radius:4px;padding:0 4px}
.kt tbody tr[hidden]{display:none}
.kt tbody tr[data-open]{cursor:pointer;transition:background-color .12s}
.kt tbody tr[data-open]:hover{background:#F9FAFB}
.kt tbody tr[data-open=true] .kt-chev{transform:rotate(90deg)}
.kt .kt-chev{transition:transform .15s}
.kt .kt-tog{all:unset;display:block;cursor:pointer;border-radius:4px;min-width:0}
.kt .kt-tog:focus-visible{outline:2px solid #5258FB;outline-offset:2px}
.kt tbody tr.kt-more{display:block;padding:0;border-top:0}
.kt tr.kt-more>td{display:block;padding:0}
.kt .kt-in{padding:0 20px 16px 20px}
.kt th[aria-sort] .kt-sort{all:unset;cursor:pointer;display:inline-flex;align-items:center;gap:4px;border-radius:4px;
  font:inherit;letter-spacing:inherit;text-transform:inherit;color:inherit}
.kt .kt-sort:hover{color:#15172F}.kt .kt-sort:focus-visible{outline:2px solid #5258FB;outline-offset:2px}
.kt th[aria-sort=descending] .kt-sort,.kt th[aria-sort=ascending] .kt-sort{color:#3C3ED4}
.kt th[aria-sort=descending] .kt-sorti{transform:scaleY(-1)}
@media (max-width:760px){.kt thead tr,.kt tbody tr{grid-template-columns:minmax(0,1fr) 20px}.kt th:not(:first-child),.kt td:not(:first-child){display:none}
  .kt th:last-child,.kt td:last-child{display:flex}}
"""

# rows that open in place, and the sortable column. Once per page, whichever view prints it first.
JS = """<script>(function(){if(window.__kt)return;window.__kt=1;
function more(r){var m=r.nextElementSibling;return m&&m.classList.contains('kt-more')?m:null}
function set(r,on){var m=more(r),d=m&&m.querySelector('.kt-open'),b=r.querySelector('.kt-tog');if(!d)return;
 if(on)d.removeAttribute('hidden');else d.setAttribute('hidden','until-found');r.dataset.open=on;if(b)b.setAttribute('aria-expanded',on)}
document.addEventListener('click',function(e){var s=e.target.closest('.kt-sort');
 if(s){var th=s.closest('th'),b=s.closest('table').tBodies[0],k=s.dataset.sort,o=th.getAttribute('aria-sort'),
   to=o==='descending'?'ascending':o==='ascending'?'none':'descending',rs=[].slice.call(b.querySelectorAll('tr.kt-row'));
  th.setAttribute('aria-sort',to);
  rs.sort(function(x,y){return to==='none'?x.dataset.i-y.dataset.i:(to==='ascending'?1:-1)*(x.dataset[k]-y.dataset[k])||x.dataset.i-y.dataset.i});
  rs.forEach(function(r){var m=more(r);b.appendChild(r);if(m)b.appendChild(m)});return}
 var t=e.target.closest('.kt-tog');if(!t&&e.target.closest('a,button,input,textarea,select,label'))return;
 var r=e.target.closest('tr[data-open]');if(r)set(r,r.dataset.open!=='true')});
document.addEventListener('beforematch',function(e){var m=e.target.closest&&e.target.closest('.kt-more'),r=m&&m.previousElementSibling;
 if(r&&r.dataset.open)set(r,true)},true);
})();</script>"""
