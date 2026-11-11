"""What each level means in each practice: the substrate's one-line `ladder_short` for every practice and level, side by
side, so L2 / L3 / L4 can be compared across practices without opening anything. Click or press a cell for the full
ladder text. When the run gives this person's level on a practice, that cell is marked "you".

Reads: the substrate (engine/substrate.json: disciplines, practices, ladder, ladder_short) and, for the "you" mark,
v15-result.json disciplines[].practices[].rung. Nothing here is written by hand: a level a practice doesn't have shows
as a muted "no L3", and tools/check.py keeps every ladder level paired with its short line.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import substrate_kit as S  # noqa: E402

ORDER = 69.5
TITLE = "What each level means"
QUESTION = "What does each level mean in each practice?"
PROXIFY_ONLY = True

LEVELS = ("L2", "L3", "L4")
WHO = {"L2": "You do it", "L3": "Your setup does it", "L4": "Runs without you"}

CSS = """
.v-sub_levels .key{font-size:12px;color:#667085;margin:-16px 0 20px}
.v-sub_levels .disc{margin:0 0 28px;overflow-x:auto}
.v-sub_levels .disc h3{font-size:17px;font-weight:600;margin:0 0 2px;color:#15172F}
.v-sub_levels .disc>p{margin:0 0 10px;font-size:13px;color:#667085;max-width:70ch}
.v-sub_levels .g{display:grid;grid-template-columns:minmax(150px,1fr) repeat(3,minmax(190px,1.5fr));gap:6px 8px;min-width:820px;align-items:start}
.v-sub_levels .gh{padding:0 0 8px;border-bottom:2px solid #D0D5DD;font-size:12px;color:#667085}
.v-sub_levels .gh b{display:block;font-size:13px;font-weight:600;color:#15172F}
.v-sub_levels .pn{padding:8px 0;font-size:13px;font-weight:500;line-height:1.4;color:#15172F;border-top:1px solid #EAECF0}
.v-sub_levels .lc{background:#FFFFFF;border:1px solid #EAECF0;border-radius:8px;font-size:12px;line-height:1.45;color:#344054}
.v-sub_levels .lc>summary{display:block;padding:7px 9px;list-style:none;cursor:pointer;border-radius:8px}
.v-sub_levels .lc>summary::-webkit-details-marker{display:none}
.v-sub_levels .lc>summary:hover{background:#F5F5FF}
.v-sub_levels .lc[open]{border-color:#5258FB;background:#F5F5FF}
.v-sub_levels .lc.me{border-color:#5258FB;box-shadow:inset 3px 0 0 #5258FB}
.v-sub_levels .lc .you{display:inline-block;margin-left:6px;padding:0 6px;border-radius:4px;background:#5258FB;color:#FFFFFF;font-size:11px;font-weight:600;line-height:17px;vertical-align:1px}
.v-sub_levels .lc .full{margin:0;padding:8px 9px 9px;border-top:1px solid #DDDEFE;color:#15172F}
.v-sub_levels .no{padding:7px 9px;font-size:12px;color:#949BAD;border:1px dashed #D0D5DD;border-radius:8px}
"""


def render(ctx):
    sub = (S.load() or {}).get("sub")
    if not sub:
        return '<p class="empty">The substrate could not be loaded.</p>'
    esc = ctx["esc"]
    mine = {p["id"]: p.get("rung") for d in ((ctx.get("v15_result") or {}).get("disciplines") or []) for p in d["practices"]}
    out = ['<p class="key">One line per level, the same voice as the full ladder. Click a cell for the full wording.'
           + (' <b>you</b> marks the level this run found.' if any(mine.values()) else "") + "</p>"]
    for d in sub["disciplines"]:
        head = "".join(f'<div class="gh"><b>{lv}</b>{esc(WHO[lv])}</div>' for lv in LEVELS)
        rows = []
        for pid in d["practices"]:
            p = sub["practices"][pid]
            ladder, short = p.get("ladder") or {}, p.get("ladder_short") or {}
            cells = []
            for lv in LEVELS:
                if not ladder.get(lv):
                    cells.append(f'<div class="no">no {lv}</div>')
                    continue
                me = mine.get(pid) == lv
                cells.append(f'<details class="lc{" me" if me else ""}"><summary>{esc(short.get(lv, ""))}'
                             f'{"<span class=you>you</span>" if me else ""}</summary><p class="full">{esc(ladder[lv])}</p></details>')
            rows.append(f'<div class="pn">{esc(p["name"])}</div>' + "".join(cells))
        out.append(f'<section class="disc"><h3>{esc(d["name"])}</h3><p>{esc(d["question"])}</p>'
                   f'<div class="g"><div class="gh"></div>{head}{"".join(rows)}</div></section>')
    return "".join(out)
