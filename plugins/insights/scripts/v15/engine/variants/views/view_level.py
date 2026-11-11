"""Level read: which level the evidence points to, and the rule that decided it (Proxify only)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lvl_tip  # noqa: E402  one tooltip for every level badge
import panel_kit as PK  # noqa: E402  a practice name opens the shared side panel

ORDER = 50
TITLE = "Level read"
QUESTION = "The level the evidence points to, and the rule that set it."
PROXIFY_ONLY = True

CSS = """
.v-level .band{background:#F5F5FF;border:1px solid #D5D5FE;border-radius:12px;padding:16px;display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
.v-level .big{font-size:24px;font-weight:600;color:#272594}
.v-level .mean{font-size:18px;font-weight:600;color:#272594}
.v-level .sub{flex-basis:100%;margin:0;color:#475467}
.v-level .row{background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;padding:0;margin-top:8px}
.v-level .row>summary{list-style:none;cursor:pointer;padding:14px 16px}
.v-level .row>summary::-webkit-details-marker{display:none}
.v-level .row>summary:hover .rl{color:#3C3ED4}
.v-level .row>.bodyin{padding:0 16px 16px;border-top:1px solid #EAECF0}
.v-level .row>dl,.v-level .row>h3,.v-level .row>ul{margin-left:16px;margin-right:16px}.v-level .row>ul,.v-level .row>dl:last-child{margin-bottom:16px}
.v-level .cnt{font-size:12px;color:#667085;margin-left:auto}
.v-level .best{display:flex;flex-wrap:wrap;gap:6px 8px;margin:8px 0 0}
.v-level .best span{font-size:12px;color:#344054;border:1px solid #EAECF0;border-radius:999px;padding:4px 10px;background:#FFFFFF}
.v-level .best b{font-weight:600;color:#15172F;margin-left:4px}
.v-level .row.won{border-color:#999AFD}
.v-level .rh{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
.v-level .row .rule{margin:12px 0 8px}
.v-level .rl{font-size:18px;font-weight:600;color:#15172F}
.v-level .rule{color:#475467;margin:4px 0 8px}
.v-level .chip{font-size:12px;font-weight:500;border-radius:999px;padding:2px 8px;border:1px solid #EAECF0;color:#475467}
.v-level .chip.yes{color:#039855;background:#F6FEF9;border-color:#039855}
.v-level .chip.no{color:#B54708;background:#FEFBF5;border-color:#FDF1D9}
.v-level .cond{border-top:1px solid #EAECF0;padding:8px 0}
.v-level .ct{display:flex;gap:8px;align-items:baseline}
.v-level .mark{font-weight:600}.v-level .mark.yes{color:#039855}.v-level .mark.no{color:#B54708}
.v-level .by{margin:8px 0 0;padding-left:24px;display:grid;gap:8px;list-style:none}
.v-level .by b{font-weight:500}.v-level .by span{display:block;color:#475467;font-size:12px}
.v-level details>summary{cursor:pointer;color:#3C3ED4;font-size:12px}
.v-level table{border-collapse:collapse;margin-top:8px;width:100%;max-width:520px}
.v-level th,.v-level td{text-align:left;padding:8px;border-bottom:1px solid #EAECF0;font-size:14px}
.v-level th{font-size:12px;font-weight:500;color:#667085}
.v-level h3{font-size:14px;font-weight:600;margin:24px 0 0}
.v-level dl{margin:8px 0 0;display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:12px;color:#475467}
.v-level dt{font-weight:500;color:#344054}.v-level dd{margin:0;overflow-wrap:anywhere}
"""

MEANING = {"L1": "Uses agents on real work", "L2": "Works with discipline",
           "L3": "Sets agents up to know, learn and remember", "L4": "Builds the harness around the agent"}
SHOW = 3


def _plain(rule):
    return (rule or "").replace(" (levels.md, 11-05)", "").replace(" (levels.md)", "").replace(" (levels.md; 10-27)", "")


def _by(b, esc, clean, names):
    if isinstance(b, str):  # some conditions list areas, not practices
        return f'<li><b>{esc(names.get(b, b))}</b></li>'
    return (f'<li><b>{PK.link("p", b["practice"], names.get(b["practice"], b["practice"]))}</b> '
            f'<span><span{lvl_tip.attrs(b["practice"], b.get("rung"))}>{esc(b.get("rung"))}</span> · {esc(names.get(b.get("discipline"), b.get("discipline")))}. '
            f'{esc(clean(b.get("why")))}</span><span>source: {esc(b.get("evidence_row"))}</span></li>')


def _cond(c, esc, clean, names):
    ok = c.get("met")
    by = c.get("by") or []
    lis = "".join(_by(b, esc, clean, names) for b in by[:SHOW])
    rest = "".join(_by(b, esc, clean, names) for b in by[SHOW:])
    more = (f'<details><summary>Show all {len(by)}</summary><ul class="by">{rest}</ul></details>' if rest else "")
    resting = (f'<ul class="by">{lis}</ul>{more}' if by else
               '<p class="rule" style="margin-left:24px">no evidence rests on this one</p>')
    return (f'<div class="cond"><div class="ct"><span class="mark {"yes" if ok else "no"}">{"✓" if ok else "✗"}</span>'
            f'<span>{esc(c.get("test"))}</span><span class="chip {"yes" if ok else "no"}">'
            f'{"met" if ok else "not met"} · {len(by)} {"discipline" if by and isinstance(by[0], str) else "practice"}{"" if len(by) == 1 else "s"}</span></div>{resting}</div>')


def render(ctx):
    esc, clean, RW = ctx["esc"], ctx["clean"], ctx["RUNG_WORD"]
    res = ctx.get("v15_result")
    p = (res or {}).get("pointer")
    if not p:
        return '<p class="empty">not in this run</p>'
    names = {}
    for d in res.get("disciplines", []):
        names[d["id"]] = d["name"]
        for pr in d.get("practices", []):
            names[pr["id"]] = pr["name"]
    lvl = p.get("level")
    cal = (res or {}).get("calibration") or {}
    # the answer for a member who had an evaluation call: above / confirms / stands (engine.calibration); first, because
    # the laptop level is what its portable, valid checks back, not a level of its own
    ce = cal.get("certainty") or {}
    cal_html = ((f'<p class="sub"><b>Against the call: {esc(cal.get("verdict", ""))}.</b> {esc(cal.get("say", ""))}</p>'
                 if cal else "")
                + (f'<p class="sub"><b>Certainty: {esc(ce["level"])}.</b> {esc(ce.get("say", ""))} '
                   f'({esc(ce.get("days_with_sessions"))} days with sessions, {esc(ce.get("backed_practices"))} practices '
                   f'backed by a count or a file, in {esc(ce.get("areas"))} areas)</p>' if ce.get("level") else ""))
    qs = p.get("call_questions") or []
    q_html = (f'<p class="sub">For the call (no laptop can count these yet, never a minus): '
              f'{esc("; ".join(" or ".join(names.get(i, i) for i in ids) for ids in qs))}</p>' if qs else "")
    band = (f'<div class="band"><span class="big">{esc(lvl)}</span><span class="mean">{esc(MEANING.get(lvl, ""))}</span>'
            + cal_html +
            f'<p class="sub">Decided by: {esc(_plain(p.get("decided_by", "")))}'
            + (f' · could also be {esc(p["could_be"])}' if p.get("could_be") else "") + "</p>" + q_html + "</div>")
    rows = []
    for t in p.get("trail", []):
        conds = t.get("conditions", [])
        met = bool(conds) and all(c.get("met") for c in conds)
        won = t["level"] == lvl
        done = sum(bool(c.get("met")) for c in conds)
        rows.append(
            f'<details class="row{" won" if won else ""}"><summary><div class="rh"><span class="rl">{esc(t["level"])}'
            + (f' · {esc(MEANING[t["level"]])}' if t["level"] in MEANING else "")
            + f'</span><span class="chip {"yes" if met else "no"}">{"✓ all met" if met else "✗ not all met"}</span>'
            + ('<span class="chip">decided the level</span>' if won else "")
            + f'<span class="cnt">{done} of {len(conds)} conditions</span></div></summary><div class="bodyin">'
            f'<p class="rule">Rule: {esc(_plain(t.get("rule", "")))}</p>'
            + "".join(_cond(c, esc, clean, names) for c in conds) + "</div></details>")
    best = p.get("best_rung_by_discipline") or {}
    table = ('<h3>Best step reached in each discipline</h3><div class="best">'
             + "".join(f'<span{lvl_tip.attrs("", v)}>{esc(names.get(k, k))}<b>{esc(v)}</b></span>' for k, v in best.items()) + "</div>") if best else ""
    reasons = "".join(f"<li>{esc(clean(r))}</li>" for r in p.get("reasons", []))
    dl = [("Source", "v15-result.json, the level read"),
          ("How it was worked out", "The engine walks the levels from the top; the first level whose conditions all "
                                    "hold is the answer. Each condition asks whether a practice reached a step."),
          ("Who decided", "Code applied the rule. The step of each practice was read from the evidence in the cheap "
                          "run (counts by code, one pass by an AI)."),
          ("Checked by a person", "not checked by a person yet")]
    receipt = ('<details class="row"><summary>How this level was worked out</summary><dl>'
               + "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in dl) + "</dl>"
               + (f'<h3>Reasons given</h3><ul>{reasons}</ul>' if reasons else "") + "</details>")
    return band + table + "".join(rows) + receipt
