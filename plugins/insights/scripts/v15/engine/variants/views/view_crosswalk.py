"""Where v13 went: every v13 practice and cue, and the v15 home it maps to.

Ported from the standalone substrate map (labs/v15/tools/map.py): its v13 crosswalk section. The data is map.py's own
analyse() (crosswalk entries from each substrate/<discipline>.yaml, the v13 practice list from labs/v13). Cue wording
is read from labs/v13/substrate/cues.yaml ("detects") so a cue shows as a sentence, not an id.
"""
import html
import importlib.util
import os
import re

try:
    import yaml
except ImportError:
    yaml = None

ORDER = 92
TITLE = "Where v13 went"
QUESTION = "Where did every v13 practice and cue end up in v15, and did anything get lost on the way?"
PROXIFY_ONLY = True

MAP_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tools", "map.py"))
esc = lambda s: html.escape("" if s is None else str(s))  # noqa: E731
DISC = {"context": "Context", "prompt": "Prompt", "verification": "Verification", "harness": "Harness",
        "orchestration": "Orchestration"}
SHOW = 3  # rows per v13 practice before "Show all N"

CSS = """
.v-crosswalk h3{font-size:18px;font-weight:600;line-height:1.35;margin:40px 0 4px}
.v-crosswalk h3 span{font-size:14px;font-weight:400;color:#667085;margin-left:8px}
.v-crosswalk .gq{color:#475467;margin:0 0 12px;max-width:72ch}
.v-crosswalk .lead{color:#475467;margin:0 0 8px;max-width:72ch}
.v-crosswalk .lead b{color:#15172F;font-weight:600}
.v-crosswalk .sum{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:16px 0}
.v-crosswalk .tile{background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;padding:16px;border-top:3px solid #D0D5DD}
.v-crosswalk .tile.ok{border-top-color:#039855}
.v-crosswalk .tile.warn{border-top-color:#DC6803}
.v-crosswalk .tile b{display:block;font-size:24px;font-weight:600;line-height:1.2}
.v-crosswalk .tile span{display:block;font-size:14px;font-weight:600;margin-top:4px}
.v-crosswalk .tile em{display:block;font-style:normal;font-size:12px;color:#667085;margin-top:2px}
.v-crosswalk details.rc{margin:8px 0 0}
.v-crosswalk details.rc>summary{font-size:12px;font-weight:500;color:#3C3ED4;width:max-content}
.v-crosswalk details.rc>summary:hover{text-decoration:underline}
.v-crosswalk details.rc[open]>summary{margin-bottom:8px}
.v-crosswalk dl{margin:0;display:grid;grid-template-columns:128px minmax(0,1fr);gap:6px 16px;font-size:14px;
  padding:12px 16px;background:#F9FAFB;border:1px solid #EAECF0;border-radius:8px}
.v-crosswalk dt{font-size:12px;font-weight:500;color:#667085;padding-top:2px}
.v-crosswalk dd{margin:0;color:#15172F;overflow-wrap:anywhere}
.v-crosswalk code{font:inherit;font-size:12px;background:#F4F5F7;border-radius:4px;padding:0 4px;color:#344054}
.v-crosswalk .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
.v-crosswalk .card{background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;padding:16px;min-width:0}
.v-crosswalk .card.lost{border-color:#DC6803;background:#FEFBF5}
.v-crosswalk .meta{font-size:12px;color:#667085;margin:0 0 2px}
.v-crosswalk details.card>summary{list-style:none;cursor:pointer;position:relative;padding-right:80px}
.v-crosswalk details.card>summary::-webkit-details-marker{display:none}
.v-crosswalk details.card>summary:hover h4{color:#3C3ED4}
.v-crosswalk details.card .cn{position:absolute;right:0;top:0;font-size:12px;color:#667085}
.v-crosswalk details.card[open]>summary{margin-bottom:8px}
.v-crosswalk .card{align-self:start}
.v-crosswalk .card h4{font-size:14px;font-weight:600;line-height:1.5;margin:0 0 8px}
.v-crosswalk .card h4 b{color:#B54708;font-weight:600;margin-left:8px;font-size:12px}
.v-crosswalk ol.rows{list-style:none;margin:0;padding:0;border-top:1px solid #EAECF0}
.v-crosswalk .r{padding:12px 0;border-bottom:1px solid #EAECF0}
.v-crosswalk .r:last-child{border-bottom:0;padding-bottom:0}
.v-crosswalk .r .was{margin:0;color:#475467}
.v-crosswalk .r .was i{font-style:normal;font-size:12px;color:#667085;margin-left:8px}
.v-crosswalk .r .now{margin:2px 0 0;font-weight:600}
.v-crosswalk .r .now em{font-style:normal;font-size:12px;font-weight:500;color:#3C3ED4;margin-left:8px}
.v-crosswalk .r .now em.warn{color:#B54708}
.v-crosswalk .r .why{margin:2px 0 0;font-size:12px;color:#475467}
.v-crosswalk details.more{margin-top:4px;border-top:1px solid #EAECF0}
.v-crosswalk details.more>summary{font-size:12px;font-weight:500;color:#3C3ED4;padding:8px 0;width:max-content}
.v-crosswalk details.more>summary:hover{text-decoration:underline}
.v-crosswalk details.more ol.rows{border-top:0}
@media (max-width:860px){.v-crosswalk .grid{grid-template-columns:1fr}.v-crosswalk .sum{grid-template-columns:1fr 1fr}
  .v-crosswalk dl{grid-template-columns:1fr}.v-crosswalk dt{padding-top:6px}}
"""


def _map(ctx):
    """map.py imported by path, analyse() run once and cached on ctx (the questions view shares it)."""
    if "_v15_map" not in ctx:
        try:
            spec = importlib.util.spec_from_file_location("v15_map", MAP_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            ctx["_v15_map"] = (mod, mod.analyse(), None)
        except Exception as e:  # missing file, or PyYAML not installed
            ctx["_v15_map"] = (None, None, f"{type(e).__name__}: {e}")
    return ctx["_v15_map"]


def _cue_words(mod):
    """v13 cue id -> the sentence v13 gave it ("detects"), or {} if the file isn't there."""
    path = os.path.join(mod.HERE, "..", "v13", "substrate", "cues.yaml")
    if yaml is None or not os.path.exists(path):
        return {}
    try:
        return {c["id"]: c.get("detects") for c in (yaml.safe_load(open(path)) or {}).get("cues") or [] if c.get("id")}
    except Exception:
        return {}


def _home(to, data):
    """A crosswalk target -> (what v15 calls it, kind word, kind class). Targets are a practice or challenge id, or a note."""
    P, C = data["practices"], data["challenges"]
    if to in P:
        return P[to].get("name") or to, "v15 practice", ""
    if to in C:
        return C[to].get("name") or to, "v15 problem", ""
    if to == "floor":
        return "The starting line (Level 1), not a practice", "no practice needed", ""
    if to == "dropped":
        return "Dropped on purpose", "dropped", "warn"
    if to and ":" in to and not to.startswith("split"):  # "prompt: agree the plan..." or "orchestration:work that runs..."
        disc, _, name = to.partition(":")
        return name.strip(), f"{DISC.get(disc.strip(), disc.strip())} (another discipline)", ""

    def swap(m):
        i = m.group(0)
        return P[i]["name"] if i in P else C[i]["name"] if i in C else i
    rx = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")
    if to and to.startswith("split"):
        body = to.partition(":")[2] or to[len("split"):]
        parts = [p.strip() for p in body.split("·") if p.strip()]
        parts = [(p.partition(":")[2].strip() if ":" in p else
                  "the starting line (Level 1)" if p == "floor" else rx.sub(swap, p)) for p in parts]
        return " + ".join(parts) or "Split across several homes", "split", ""
    return (rx.sub(swap, to) if to else "No home named"), "other", "warn"


def _row(v13_id, label, entries, data):
    esc_id = esc(v13_id)
    was = f'<p class="was">{esc(label) if label else esc_id}<i>v13: {esc_id}</i></p>'
    if not entries:
        return (f'<li class="r lost">{was}<p class="now">Not mapped<em class="warn">went nowhere</em></p>'
                f'<p class="why">No crosswalk entry names it. Check whether v15 dropped it on purpose.</p></li>')
    out = ""
    for x in entries:
        name, kind, cls = _home(x.get("to"), data)
        out += (f'<li class="r">{was if out == "" else ""}<p class="now">{esc(name)}<em class="{cls}">{esc(kind)}</em></p>'
                f'<p class="why">{esc(x.get("why"))}</p></li>')
    return out


def _card(title, meta, rows, lost=0):
    """A card is closed: where it came from, its name, how many entries and whether any went unmapped. Open it for every row."""
    flag = f"<b>{lost} not mapped</b>" if lost else ""
    return (f'<details class="card{" lost" if lost else ""}"><summary><p class="meta">{meta}</p><h4>{esc(title)}{flag}</h4>'
            f'<span class="cn">{len(rows)} {"entry" if len(rows) == 1 else "entries"}</span></summary>'
            f'<ol class="rows">{"".join(rows)}</ol></details>')


def render(ctx):
    mod, data, why = _map(ctx)
    if mod is None:
        return f'<p class="empty">not in this run: the substrate map could not be loaded ({esc(why)}).</p>'
    v13, crosswalk = data.get("v13") or [], data.get("crosswalk") or []
    if not crosswalk:
        return '<p class="empty">not in this run: the substrate has no v13 crosswalk entries.</p>'
    cues = _cue_words(mod)
    by = {}
    for x in crosswalk:
        by.setdefault(x.get("v13"), []).append(x)

    seen, cards, lost_total, n_items = set(), [], 0, 0
    for vp in v13:
        ids = [vp["id"], *vp.get("cues", [])]
        rows, lost = [], 0
        for i in ids:
            seen.add(i)
            lost += not by.get(i)
            label = "The practice as a whole" if i == vp["id"] else cues.get(i)
            rows.append(_row(i, label, by.get(i), data))
        n_items += len(ids)
        lost_total += lost
        meta = f'{esc(DISC.get(vp.get("badge"), vp.get("badge")))} · {len(vp.get("cues", []))} cues · v13: {esc(vp["id"])}'
        cards.append(_card(vp.get("question") or vp["id"], meta, rows, lost))
    other = [i for i in by if i not in seen]
    other_html = ""
    if other:
        orows = [_row(i, cues.get(i), by[i], data) for i in other]
        other_html = (f'<h3>Other v13 ids the crosswalk names<span>{len(other)}, not a practice or cue in v13\'s practice list</span></h3>'
                      f'<div class="grid">{_card("Mapped, but not on v13\'s practice list", f"{len(other)} ids", orows)}</div>')

    kinds = {}
    for x in crosswalk:
        k = _home(x.get("to"), data)[1]
        kinds[k] = kinds.get(k, 0) + 1
    mix = "; ".join(f"{n} to {k}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
    if lost_total:
        verdict = f'<div class="tile warn"><b>{lost_total}</b><span>Went nowhere</span><em>no crosswalk entry names them</em></div>'
    else:
        verdict = '<div class="tile ok"><b>0</b><span>Went nowhere</span><em>every practice and cue has a v15 home</em></div>'
    receipt = ('<details class="rc"><summary>How this was counted</summary><dl>'
               f'<dt>Source</dt><dd>The <code>crosswalk</code> list at the end of each <code>substrate/&lt;discipline&gt;.yaml</code> '
               f'({len(crosswalk)} entries across the five); the v13 practice list from <code>labs/v13/substrate/practices.yaml</code>; '
               'cue wording from <code>labs/v13/substrate/cues.yaml</code>. Read through the substrate map\'s own loader.</dd>'
               '<dt>How</dt><dd>Every v13 practice and each of its cues is looked up in the crosswalk. One with no entry is "not mapped". '
               'Plain lookup: code, no AI read.</dd>'
               '<dt>The reasons</dt><dd>Each "why" line was written by the agent that drafted the substrate, not computed.</dd>'
               '<dt>Checked by a person</dt><dd>not checked by a person yet</dd></dl></details>')
    mix_line = f'<p class="gq">Where the {len(crosswalk)} entries point: {esc(mix)}.</p>' if mix else ""
    return (f'<p class="lead">v13 scored <b>{len(v13)}</b> practices and <b>{n_items - len(v13)}</b> cues beneath them. Each row below '
            f'is one of them and the v15 practice or problem that took it over, with the reason.</p>'
            f'<div class="sum"><div class="tile"><b>{len(v13)}</b><span>v13 practices</span><em>{n_items - len(v13)} cues under them</em></div>'
            f'<div class="tile"><b>{n_items - lost_total}</b><span>Mapped</span><em>of {n_items} practices and cues</em></div>'
            f'{verdict}<div class="tile"><b>{len(crosswalk)}</b><span>Crosswalk entries</span><em>including {len(other)} extra ids</em></div></div>'
            f'{mix_line}{receipt}<h3>By v13 practice<span>the top {SHOW} of each; open a card for the rest</span></h3>'
            f'<div class="grid">{"".join(cards)}</div>{other_html}')
