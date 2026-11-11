"""Who did it: the raw counts from inventory.json's who_did_it block, grouped by who made each thing happen (you, your
setup, your programs, probably you, can't tell, Claude by itself).

Layout (his 11-04 "too much text, bad layout, clustered weirdly"): a strip of the groups to jump to, then per group a
grid of tiles, number first. "How we know" is a small ? on each tile; opened, the tile spans the row. A tile whose count
now sets a practice's rung (signal-rows.json, written by signals.py from the substrate's signal map) carries
an L2/L3 tag beside its number. The item ids are a contract with the signal map: read them, never rename them here. Stdlib only."""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lvl_tip  # noqa: E402  one tooltip for every level badge
import panel_kit as PK  # noqa: E402  a practice name opens the shared side panel

ORDER = 5
TITLE = "Who did it"
QUESTION = "Every count from your logs, sorted by who made it happen: you, your setup, a headless run, or Claude on its own."
PROXIFY_ONLY = False

# a colour per group, carried by one marker (the dot in the legend, the bar on the group) beside the word. No emoji:
# a tester 11-06, "Emojis vs icons. This makes UI inconsistent… you have color coding dots and markings. Both are too much
# to decode" (Proxify's brand uses Phosphor icons when a visual is needed)
GROUP = {"you": "#039855", "setup": "#5258FB", "programs": "#1570EF", "probably": "#DC6803", "cant": "#667085", "claude": "#7A5AF8"}

CSS = """
.v-who .lede{color:#475467;margin:0 0 16px;max-width:80ch}
.v-who .lede b{color:#15172F;font-weight:600}
.v-who .jump{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 24px}
.v-who .jump button{display:inline-flex;align-items:center;gap:8px;font:500 13px/1 Inter,system-ui,sans-serif;color:#344054;
  background:#FFFFFF;border:1px solid #EAECF0;border-radius:999px;padding:8px 12px;cursor:pointer}
.v-who .jump button:hover{border-color:var(--g)}
.v-who .jump i{width:8px;height:8px;border-radius:50%;background:var(--g)}
.v-who .jump span{color:#667085;font-weight:400}
.v-who .grp{margin:0 0 16px;scroll-margin-top:128px}
.v-who .grp>summary{list-style:none;cursor:pointer;display:flex;align-items:baseline;flex-wrap:wrap;column-gap:12px;row-gap:2px;padding:8px 0 8px 12px;border-left:3px solid var(--g)}
.v-who .grp>summary::-webkit-details-marker{display:none}
.v-who .grp>summary::after{content:"Show";margin-left:auto;font-size:12px;color:#3C3ED4}
.v-who .grp[open]>summary::after{content:"Hide"}
.v-who .grp>.grid{margin:8px 0 24px}
.v-who .grp h4{font-size:16px;font-weight:600;margin:0;color:#15172F}
.v-who .grp>summary p{margin:0;font-size:13px;color:#667085}
.v-who .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));grid-auto-flow:dense;gap:12px}
.v-who .t{position:relative;display:flex;flex-direction:column;gap:4px;background:#FFFFFF;border:1px solid #EAECF0;
  border-radius:10px;padding:14px 14px 12px;min-width:0}
.v-who .t .n{font-size:24px;font-weight:600;line-height:1.1;color:#15172F;letter-spacing:-.01em;padding-right:24px}
.v-who .t .l{font-size:13px;font-weight:500;line-height:1.4;color:#344054}
.v-who .t .s{font-size:12px;color:#667085}
.v-who .t .s.warn{color:#B54708}
.v-who .t .bar{height:4px;border-radius:2px;background:#F2F4F7;overflow:hidden;margin-top:2px}
.v-who .t .bar i{display:block;height:100%;background:var(--g)}
.v-who .lvl{white-space:nowrap;vertical-align:middle;font-size:11px;font-weight:600;letter-spacing:.02em;color:#3C3ED4;
  background:#EEEFFE;border-radius:4px;padding:2px 6px}
.v-who .t .n .lvl{margin-left:8px;position:relative;top:-3px}
.v-who .t.zero .n,.v-who .t.zero .l{color:#949BAD}
.v-who .how>summary{position:absolute;top:10px;right:10px;width:20px;height:20px;border-radius:50%;list-style:none;
  display:grid;place-items:center;font-size:11px;font-weight:600;color:#667085;border:1px solid #D0D5DD}
.v-who .how>summary::-webkit-details-marker{display:none}
.v-who .how>summary:hover,.v-who .how[open]>summary{color:#3C3ED4;border-color:#5258FB}
.v-who .how p{margin:8px 0 0;font-size:13px;color:#475467;max-width:90ch;overflow-wrap:anywhere}
.v-who .how p b{color:#344054;font-weight:600}
.v-who .t:has(.how[open]){grid-column:1 / -1;border-color:var(--g)}
"""


CARDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "substrate", "claude-code.yaml")
CHECK_NOTE = "Partly counted from tools on this laptop that other people won't have. Being rebuilt."


def card_status():
    """id -> status ("ok" | "check" | "rework") from substrate/claude-code.yaml `cards:` (proxify-6e's list of what
    Claude Code itself writes). His 11-04 rule: the tool counts Claude Code's own records, never his personal setup.
    A "rework" card is not shown at all; a "check" card shows with CHECK_NOTE. {} without the file or PyYAML."""
    try:
        import yaml
        cards = yaml.safe_load(open(CARDS)).get("cards") or {}
    except Exception:  # noqa: BLE001 - no list: show every card, as before the list existed
        return {}
    return {k: (v or {}).get("status", "ok") for k, v in cards.items()}


def shown_groups(wd):
    """who_did_it groups with the rework cards left out, and how many were left out."""
    st, out, hidden = card_status(), [], 0
    for g in wd.get("groups") or []:
        keep = [i for i in g["items"] if st.get(i["id"]) != "rework"]
        hidden += len(g["items"]) - len(keep)
        if keep:
            out.append({**g, "items": keep})
    return out, hidden


def _n(x):
    return f"{int(x):,}"


def _counted(run):
    """signal id -> (highest rung it sets, the practices it sets it on) and signal id -> {practice id: rung there}, from signal-rows.json; empty when the run has none."""
    path = os.path.join(run or "", "signal-rows.json")
    if not os.path.exists(path):
        return {}, {}
    out, at = {}, collections.defaultdict(dict)  # at: signal id -> {practice id: the rung it sets there}
    for pid, row in (json.load(open(path)).get("practices") or {}).items():
        for s in row.get("signals") or []:
            rung, names = out.get(s["id"], ("", []))
            out[s["id"]] = (max(rung, s.get("rung") or ""), names + [pid])
            at[s["id"]][pid] = s.get("rung") or ""
    return out, at


def _practice_names(ctx):
    res = ctx.get("v15_result") or {}
    return {p["id"]: p.get("name", p["id"]) for d in res.get("disciplines") or [] for p in d.get("practices") or []}


def render(ctx):
    esc = ctx["esc"]
    inv = ctx.get("inventory") or {}
    wd = inv.get("who_did_it")
    if not wd:
        return '<p class="empty">Not in this run: inventory.json has no who_did_it block (a scan from before 11-04).</p>'
    counted, at = _counted(ctx.get("run"))
    names = _practice_names(ctx)
    groups, hidden = shown_groups(wd)
    st = card_status()
    wd = {**wd, "groups": groups}
    shown = sum(1 for g in wd["groups"] for i in g["items"] if i["id"] in counted)
    member = bool(ctx.get("member"))
    lede = f'Counts from your logs, last {esc(wd.get("days"))} days. Click <b>?</b> on a tile to see how we know.'
    if shown and not member:
        lede += f' <b>{shown}</b> count toward a practice\'s level (tagged <span class="lvl">L2</span> or <span class="lvl">L3</span>).'
    if hidden:
        lede += f" {hidden} more are left out while they're rebuilt to read only what Claude Code itself records."
    out = [f'<p class="lede">{lede}</p><div class="jump">']
    for g in wd["groups"]:
        colour = GROUP.get(g["id"], "#667085")
        out.append(f'<button type="button" style="--g:{colour}" '
                   f'onclick="var e=document.getElementById(\'who-{esc(g["id"])}\');e.open=true;e.scrollIntoView({{behavior:\'smooth\'}})">'
                   f'<i></i>{esc(g["title"])} <span>{len(g["items"])}</span></button>')
    out.append("</div>")
    for g in wd["groups"]:
        colour = GROUP.get(g["id"], "#667085")
        # you, your setup and your headless runs are open; the groups that are inferred or can't be told start closed
        out.append(f'<details class="grp" id="who-{esc(g["id"])}" style="--g:{colour}"{" open" if g["id"] in ("you", "setup", "programs") else ""}>'
                   f'<summary><h4>{esc(g["title"])} <span style="font-weight:400;color:#667085">{len(g["items"])}</span></h4><p>{esc(g["means"])}</p></summary><div class="grid">')
        for i in g["items"]:
            chats, of = i.get("chats"), i.get("of")
            sub = []
            if of:
                sub.append(f"of {_n(of)} · {round(100 * i['n'] / of)}%")
            if chats and chats != i["n"]:
                sub.append(f"in {_n(chats)} sessions")
            bar = f'<div class="bar"><i style="width:{min(100, round(100 * i["n"] / of))}%"></i></div>' if of else ""
            rung, pids = counted.get(i["id"], ("", []))
            top = " ".join(p for p in pids if at.get(i["id"], {}).get(p) == rung)  # the practices this count sets at the tag's level
            lvl = (f'<span class="lvl"{lvl_tip.attrs(top, rung, title=f"Counts toward {rung} on a practice")}>{esc(rung)}</span>'
                   if rung and not member else "")
            sets = (f'<p><b>Counts for {esc(rung)} on:</b> {"; ".join(PK.link("p", p, names.get(p, p)) for p in pids)}</p>' if rung and not member else "")
            out.append(f'<div class="t{" zero" if not i["n"] else ""}"><div class="n">{_n(i["n"])}{lvl}</div>'
                       f'<div class="l">{esc(i["label"])}</div>'
                       + (f'<div class="s warn">{esc(CHECK_NOTE)}</div>' if st.get(i["id"]) == "check" else "")
                       + (f'<div class="s">{" · ".join(sub)}</div>' if sub else "") + bar +
                       f'<details class="how"><summary title="How we know" aria-label="How we know">?</summary>'
                       f'<p><b>How we know:</b> {esc(i["proof"])}</p>{sets}</details></div>')
        out.append("</div></details>")
    return "".join(out)
