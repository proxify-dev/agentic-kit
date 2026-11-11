"""Part by part: a guided walk through the person's setup, discipline by discipline. Built on the anatomy
(v15/anatomy/anatomy.yaml) by engine/anatomy_model.tree, the single source the lab page (tools/anatomy.py) reads too.

Top to bottom:
  1. the overview: how many parts their own tools already do, a map with one square per part (click one to jump to it),
     and the next step in each discipline
  2. a stepper that stays on screen, then one chapter per discipline: a picture of that part of the setup drawn from
     the run's own files (anatomy_pic_<name>.py), then its parts grouped as done by your own tools · where to go next ·
     couldn't see. A part opens into its three steps, by hand → your setup → your own tools, each with what was seen.
  3. 'For your call': the parts no laptop can see, never counted against the person
  4. 'Every signal': each rung's techniques and signals and how each was read, folded

Why a walkthrough: the first version was one table and 39 rows of equal weight (11-09: "everything's a table ... this
should almost give users a walkthrough and overview feeling over their setup").
Needs the anatomy step's files in the run (anatomy_scan.py: anatomy-scan.json; the AI pass: anatomy-ai-*.json).
A picture that fails is left out and named on stderr; the tab still builds."""
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ENGINE)
import anatomy_model as M  # noqa: E402

ANATOMY = os.path.join(os.path.dirname(ENGINE), "anatomy", "anatomy.yaml")
HOME = os.path.expanduser("~")

ORDER = 5
TITLE = "Part by part"
QUESTION = "A guided walk through your setup: what your own tools already do, and the next step for each part."
PROXIFY_ONLY = False

# a rung as who does the work, the anatomy's own terms (L2 by hand · L3 their setup · L4 built), and a part with no
# rung seen (never a failing: 'not seen yet'; a check that couldn't run is ours, not theirs). Not 'Manual · Configured
# · Automated' (11-09: those "sound weird on the frontend", like a server's status; and the top step is tooling they
# built, which often isn't automation: skills 'tested and kept current'). Shown as a plain label beside the track, no pill.
WORD = {"L4": "Your own tools", "L3": "Your setup", "L2": "By hand", "not seen": "Not seen yet", "can't tell": "Couldn't check"}
TONE = {"L4": "hi", "L3": "hi", "L2": "mid", "not seen": "lo", "can't tell": "na"}  # badges in 'Every signal'
# the walkthrough's colour per result: one ramp, strongest = your own tools (theme.py mirrors it in dark mode, so the
# strongest stays the most visible); 'not yet' is an outline and 'couldn't check' a hatch, never a warning colour
CLS = {"L4": "a", "L3": "c", "L2": "m", "not seen": "n", "can't tell": "q"}
SOURCE = {"SCAN": "code scan", "SCAN → AI": "scan, then AI read", "PROJECT READ": "AI read of your projects", "CALL": "a call question"}
# each discipline's picture: anatomy_pic_<name>.py, render(run_dir, tree) -> html
PICTURES = {"context": "context", "harness": "harness", "loop": "loop", "orchestration": "day", "compound": "compound"}

CSS = """
/* 'Every signal' (folded): the rung-by-rung detail */
.v-anatomy .px-raw .disc{background:#FFFFFF;border:1px solid #EAECF0;border-radius:14px;margin:16px 0;overflow:hidden}
.v-anatomy .px-raw .dh{padding:14px 18px;border-bottom:1px solid #EAECF0}
.v-anatomy .px-raw .dh h3{margin:0;font-size:17px}.v-anatomy .px-raw .dh p{margin:2px 0 0;color:#667085;font-size:13px}
.v-anatomy .px-raw details{border-bottom:1px solid #EAECF0}.v-anatomy .px-raw details:last-child{border-bottom:0}
.v-anatomy .px-raw summary{list-style:none;display:flex;align-items:center;gap:10px;padding:10px 18px}
.v-anatomy .px-raw summary::-webkit-details-marker{display:none}.v-anatomy .px-raw summary:hover{background:#F9FAFB}
.v-anatomy .px-raw summary .nm{flex:1;font-weight:500}
.v-anatomy .px-raw .w{font-size:12px;font-weight:600;padding:2px 9px;border-radius:99px;white-space:nowrap}
.v-anatomy .px-raw .w.hi{background:#F6FEF9;color:#027A48}.v-anatomy .px-raw .w.mid{background:#F5F5FF;color:#3C3ED4}
.v-anatomy .px-raw .w.lo{background:#FEFBF5;color:#B54708}.v-anatomy .px-raw .w.na{background:#F4F5F7;color:#667085}
.v-anatomy .px-raw .marks{display:flex;gap:4px}.v-anatomy .px-raw .mk{font-size:11px;width:30px;text-align:center;border-radius:6px;padding:1px 0}
.v-anatomy .px-raw .mk.s{background:#F6FEF9;color:#027A48}.v-anatomy .px-raw .mk.n{background:#FEFBF5;color:#B54708}.v-anatomy .px-raw .mk.q{background:#F4F5F7;color:#949BAD}
.v-anatomy .px-raw .rung{margin:4px 18px 12px 30px;border-left:3px solid #D5D5FE;padding:2px 0 2px 14px}
.v-anatomy .px-raw .rh{font-size:13.5px}.v-anatomy .px-raw .rh b{color:#3C3ED4;margin-right:6px}
.v-anatomy .px-raw .tech{margin:6px 0 0 4px;font-size:13px;color:#344054}
.v-anatomy .px-raw .sg{margin:3px 0 0 18px;font-size:12.5px;color:#475467}
.v-anatomy .px-raw .src{font-size:10.5px;font-weight:600;letter-spacing:.03em;padding:1px 6px;border-radius:5px;background:#F4F5F7;color:#475467;margin-right:6px}
.v-anatomy .px-raw .src.scan{background:#F5F5FF;color:#1570EF}.v-anatomy .px-raw .src.ai{background:#F5F5FF;color:#7A5AF8}
.v-anatomy .px-raw .ok{color:#027A48;font-weight:600}.v-anatomy .px-raw .no{color:#B54708;font-weight:600}.v-anatomy .px-raw .qq{color:#667085;font-weight:600}
.v-anatomy .px-raw .note{color:#667085}.v-anatomy .px-raw .empty{color:#667085;padding:12px 0}
.v-anatomy .px-raw .nl{display:block;font-weight:400;font-size:12.5px;color:#475467;margin-top:2px}.v-anatomy .px-raw .nl b{color:#3C3ED4;font-weight:600}
.v-anatomy .more{font-size:11px;color:#667085;border:1px dashed #D0D5DD;border-radius:99px;padding:1px 8px;white-space:nowrap}
/* the walkthrough */
.v-anatomy .px{--a:#5258FB;--c:#999AFD;--m:#DDDEFE}
.px button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer;text-align:left}
.px-kicker{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#5258FB}
.px-dim{color:#667085}
.px-hero{margin:8px 0 18px}
.px-hero h3{font-size:26px;line-height:1.25;font-weight:500;margin:6px 0 6px;letter-spacing:-.01em}
.px-hero h3 b{font-weight:700;color:#3C3ED4}
.px-hero p{margin:0;color:#475467;max-width:760px}

.px-map{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}
.px-dcard{display:flex !important;flex-direction:column;gap:6px;background:#FFFFFF;border:1px solid #EAECF0;
  border-radius:14px;padding:14px 14px 12px;transition:border-color .15s,box-shadow .15s}
.px-dcard:hover{border-color:#999AFD;box-shadow:0 2px 10px -4px rgba(82,88,251,.25)}
.px-dcard b{font-size:15px}
.px-num{font-size:11px;font-weight:600;color:#949BAD;letter-spacing:.05em}
.px-goal{font-size:12.5px;color:#667085;line-height:1.4;min-height:35px}
.px-tiles{display:flex;flex-wrap:wrap;gap:4px;margin-top:2px}
.px-score{font-size:12.5px;color:#475467;margin-top:auto;padding-top:6px}
.px-score b{font-size:15px;color:#15172F}
.px-tile{display:inline-block;width:18px;height:18px;border-radius:5px;flex:none}
.px-tiles.big .px-tile{width:22px;height:22px;border-radius:6px}
.px-tile.px-a{background:var(--a)}
.px-tile.px-c{background:var(--c)}
.px-tile.px-m{background:var(--m)}
.px-tile.px-n{background:#FFFFFF;box-shadow:inset 0 0 0 1.5px #D0D5DD}
.px-tile.px-q{background:repeating-linear-gradient(135deg,#F2F4F7 0 3px,#EAECF0 3px 6px)}
button.px-tile:hover{outline:2px solid #15172F;outline-offset:1px}
.px-legend{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;font-size:12.5px;color:#475467;margin:12px 2px 0}
.px-legend span{display:inline-flex;align-items:center;gap:6px}
.px-legend .px-tile{width:12px;height:12px;border-radius:3px}
.px-legend .px-dim{flex-basis:100%}

.px-growbox{margin:22px 0 8px;background:#F5F5FF;border:1px solid #E5E8F9;border-radius:14px;padding:14px 16px}
.px-growrow{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px}
.px-grow{display:flex !important;flex-direction:column;gap:4px;background:#FFFFFF;border:1px solid #EAECF0;
  border-radius:10px;padding:10px 12px}
.px-grow:hover{border-color:#999AFD}
.px-grow .px-gd{font-size:11.5px;color:#667085}
.px-gn{font-size:13px;color:#3C3ED4}

.px-steps{position:sticky;top:var(--px-top,110px);z-index:5;display:flex;gap:4px;margin:28px 0 8px;padding:6px;
  background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;overflow-x:auto}
.px-steps button{display:inline-flex !important;align-items:center;gap:8px;padding:6px 12px;border-radius:8px;font-size:13.5px;
  color:#475467;white-space:nowrap}
.px-steps button span{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:99px;font-size:11px;
  background:#F2F4F7;color:#667085;font-weight:600}
.px-steps button:hover{background:#F9FAFB}
.px-steps button.on{background:#F5F5FF;color:#3C3ED4;font-weight:600}
.px-steps button.on span{background:#5258FB;color:#FFFFFF}

.px-ch{padding:26px 0 10px;scroll-margin-top:calc(var(--px-top,110px) + 60px)}
.px-chh h3{font-size:22px;margin:2px 0 2px}
.px-chh .px-goal{min-height:0;font-size:14px;margin:0}
.px-verdict{font-size:14.5px;margin:8px 0 10px;color:#15172F}
.px-ch h4{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#667085;margin:22px 0 8px}
.px-ch h4 span{background:#F2F4F7;border-radius:99px;padding:1px 7px;margin-left:4px;font-size:11px}

.px-part{background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;margin:8px 0;
  scroll-margin-top:calc(var(--px-top,110px) + 70px)}
.px-part[open]{border-color:#999AFD}
.px-part.flash{animation:pxflash 1.4s ease}
@keyframes pxflash{0%,40%{box-shadow:0 0 0 4px #DDDEFE}100%{box-shadow:0 0 0 0 transparent}}
.px-part>summary{list-style:none;padding:12px 16px}
.px-part>summary::-webkit-details-marker{display:none}
.px-part>summary:hover{background:#F9FAFB;border-radius:12px}
.px-ph{display:flex;align-items:center;gap:12px}
.px-nm{flex:1;font-weight:600;font-size:14.5px}
.px-proof{margin-top:4px;font-size:13px;color:#475467;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.px-proof::before{content:"We saw  ";color:#027A48;font-weight:600;white-space:pre}
.px-file{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:#344054;
  background:#F2F4F7;border-radius:5px;padding:1px 5px}
.px q{color:#475467;font-style:italic}
.px-next{margin-top:4px;font-size:13px;color:#3C3ED4}
.px-askline{color:#667085}

.px-track{display:inline-flex;align-items:center;gap:0}
.px-track i{width:10px;height:10px;border-radius:99px;background:#EAECF0;display:block}
.px-track i+i{margin-left:14px;position:relative}
.px-track i+i::before{content:"";position:absolute;right:10px;top:4px;width:14px;height:2px;background:#EAECF0}
.px-track i.on{background:var(--a)}
.px-track i.on+i.on::before{background:var(--a)}
.px-track.px-q i{background:repeating-linear-gradient(135deg,#F2F4F7 0 2px,#D0D5DD 2px 4px)}


.px-ladder{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;padding:4px 16px 12px}
.px-rung{border-radius:10px;padding:10px 12px;background:#F9FAFB;border:1px solid #EAECF0;font-size:13px}
.px-rl{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#667085}
.px-does{font-weight:600;margin:2px 0 6px;color:#15172F}
.px-rung ul{margin:6px 0 0;padding-left:16px}.px-rung li{margin:3px 0;word-break:break-word}
.px-rung p{margin:6px 0 0}
.px-ok{color:#027A48;font-weight:600;font-size:12.5px}
.px-no{color:#B54708;font-weight:600;font-size:12.5px}
.px-ask{color:#7A5AF8;font-weight:600;font-size:12.5px}
.px-how{margin:0 16px 12px;font-size:12.5px}
.px-how summary{color:#667085;font-size:12.5px}
.px-how table{border-collapse:collapse;margin-top:6px;width:100%}
.px-how td,.px-how th{text-align:left;padding:3px 10px 3px 0;border-bottom:1px solid #F2F4F7;font-weight:400}
.px-how th{color:#667085;font-size:11.5px}

.px-nextch{margin:18px 0 6px;padding:10px 14px !important;border-radius:10px;background:#F5F5FF !important;
  color:#3C3ED4 !important;font-weight:600}
.px-nextch:hover{background:#EEEFFE !important}
.px-call{padding-left:20px}.px-call li{margin:10px 0}
.px-link{color:#3C3ED4 !important;font-weight:600}
.px-link:hover{text-decoration:underline}
.px-raw{margin:40px 0 20px;border-top:1px solid #EAECF0;padding-top:14px}
.px-raw>summary{color:#667085;font-size:13px}
@media (max-width:900px){.px-map{grid-template-columns:repeat(2,1fr)}.px-growrow,.px-ladder{grid-template-columns:1fr}}

.v-anatomy .px details{border-bottom:0}
.v-anatomy .px-part>summary{display:block;padding:12px 16px;gap:0}
.v-anatomy .px-how>summary,.v-anatomy .px-raw>summary{display:list-item;padding:0}
.px [role=button]{cursor:pointer}
span.px-tile:hover{outline:2px solid #15172F;outline-offset:1px}
.px-proof{white-space:normal;display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical}
.px-pos{display:inline-flex;align-items:center;gap:10px;flex:none}
.px-where{font-size:12.5px;font-weight:600;white-space:nowrap;min-width:104px;text-align:right}
.px-where.px-a{color:#3C3ED4}.px-where.px-c{color:#5258FB}.px-where.px-m{color:#475467}
.px-where.px-n,.px-where.px-q{color:#667085;font-weight:500}
.px-grow .px-pos{align-self:flex-start}.px-grow .px-where{min-width:0;text-align:left}
.v-anatomy .px-raw .w{background:none;padding:0}
/* the page's '.view [id]' margin would win: these sit under the header and the sticky stepper */
.v-anatomy .px-part[id]{scroll-margin-top:calc(var(--px-top,110px) + 84px)}
.v-anatomy .px-ch[id]{scroll-margin-top:calc(var(--px-top,110px) + 60px)}
.px-viz{margin:16px 0 6px;background:#FFFFFF;border:1px solid #EAECF0;border-radius:14px;padding:16px 18px}
.v-anatomy .px .more{font-size:11px;color:#667085;border:1px dashed #D0D5DD;border-radius:99px;padding:1px 8px;white-space:nowrap}
.v-anatomy .px-gn b{font-weight:600}
.v-anatomy .px-raw>.lead{color:#475467;margin:10px 0 0}
"""

JS = """<script>(function () {
  var root = document.querySelector('.v-anatomy .px'); if (!root) return;
  var top = document.querySelector('.rp-top');
  root.style.setProperty('--px-top', (top ? top.offsetHeight : 0) + 'px');
  // jump to a chapter or a part without touching the hash: the page picks its tab from the hash
  function go(b) {
    var el = document.getElementById(b.dataset.go); if (!el) return;
    if (el.tagName === 'DETAILS') { el.open = true; el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); }
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  root.addEventListener('click', function (e) {
    var b = e.target.closest('[data-go]'); if (!b) return;
    e.preventDefault(); e.stopPropagation(); go(b);
  });
  root.addEventListener('keydown', function (e) {
    var b = e.target.closest('[role=button][data-go]');
    if (b && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); go(b); }
  });
  // the stepper follows the chapter in view
  var btns = root.querySelectorAll('.px-steps button');
  var obs = new IntersectionObserver(function (ents) {
    ents.forEach(function (en) {
      if (!en.isIntersecting) return;
      btns.forEach(function (b) { b.classList.toggle('on', b.dataset.go === en.target.id); });
    });
  }, { rootMargin: '-45% 0px -50% 0px' });
  root.querySelectorAll('.px-ch').forEach(function (s) { obs.observe(s); });
})();</script>"""


def _you(y, esc):
    if not y:
        return ""
    if y.get("fired") is True:
        head = '<span class="ok">✓ seen</span>'
    elif y.get("fired") is False:
        head = '<span class="no">not seen</span>'
    else:
        head = '<span class="qq">couldn\'t check</span>'
    val = y.get("value")
    tail = f" · {esc(val)}" if val not in (None, "", "yes", "no") else ""
    note = f' <span class="note">{esc(str(y.get("note") or "")[:220])}</span>' if y.get("note") else ""
    return head + tail + note


RANK = {"not seen": 0, "L2": 1, "L3": 2}  # how far from the top step a part is: the lowest goes first


def next_step(c):
    """The next step for one part: the rung just above where they are — what it does and the technique that gets there.
    Always shown, whether or not a guide page exists (his 11-09: 'next steps should be clear to the user at all
    times, even if no real page link'). None when the part is at the top step or we couldn't check it."""
    you = c.get("you")
    if you not in RANK:
        return None
    if you == "not seen":  # start at the first rung with a concrete act: a call-only rung ('judgment') is no step
        lv = next((x for x in M.RUNGS if not (c["rungs"].get(x) or {}).get("call_only")), "L2")
    else:
        lv = M.RUNGS[M.RUNGS.index(you) + 1]
    r = c["rungs"].get(lv) or {}
    return {"rung": lv, "does": r.get("does") or "", "try": [t["name"] for t in r.get("techniques") or [] if t.get("name")]}


def _next_line(n, esc):
    tries = (" · try: " + ", ".join(esc(x) for x in n["try"][:2])) if n["try"] else ""
    return f'<b>Next → {esc(WORD[n["rung"]][0].lower() + WORD[n["rung"]][1:])}:</b> {esc(n["does"])}{tries}'


def _mark(r):
    y = (r or {}).get("you")
    if y == "seen":
        return "s", "✓"
    if str(y).startswith("not seen"):
        return "n", "·"
    return "q", "?"


def _maybe_more(c, esc):
    """A higher rung we couldn't check (a timeout, an unanswered read) may hold more than the badge says: say so, so
    'by hand' never reads as final when the top step only ran out of time (11-09: one check took 3.3 s on one run and
    6.0 s on the next, and the part fell from the top step to the first)."""
    higher = [lv for lv in M.RUNGS if c["you"] in M.RUNGS and lv > c["you"]
              and (c["rungs"].get(lv) or {}).get("you") == "can't tell" and not (c["rungs"].get(lv) or {}).get("call_only")]
    return (f'<span class="more" title="{esc(", ".join(WORD[lv] for lv in higher))} couldn\'t be checked this run">maybe more</span>'
            if higher else "")


# ---- one part: what was seen, said the way a person reads it ----

def _short(path):
    return path.replace(HOME + "/", "~/").replace(HOME, "~")


def _fired(r):
    """(technique name, signal) for every signal that fired on this rung."""
    return [(t["name"], s) for t in (r or {}).get("techniques", []) for s in t["signals"]
            if (s.get("you") or {}).get("fired") is True]


def _asks(r):
    return [s["text"] for t in (r or {}).get("techniques", []) for s in t["signals"] if s["source"] == "CALL"]


def _proof(tech, sig, esc):
    """One fired signal as a line a person can read: the file and the quote an AI read found, or a count named by its
    technique ('own PreToolUse hooks · 8×'). The signal's own text is the inventory's, written for us, not for them."""
    y = sig.get("you") or {}
    note, v = str(y.get("note") or "").strip(), y.get("value")
    if sig["source"] in ("SCAN → AI", "PROJECT READ"):
        first = note.split(" · ")[0]
        m = re.match(r'(.+?) — "(.*)', first, re.S)
        if m:
            q = m.group(2).rstrip('"').replace("\\", "")
            return f'<span class="px-file">{esc(_short(m.group(1)))}</span> <q>{esc(q[:130])}{"…" if len(q) > 130 else ""}</q>'
        return esc(_short(first)[:160])
    if note and note[0].isdigit() and len(note) > 25 and "\n" not in note:  # already a sentence: '94 of 113 forks ...'
        return esc(_short(note)[:170])
    text = tech or re.split(r"[;(:]", sig.get("text") or "")[0].strip().rstrip(".")
    tail = f' <span class="px-dim">· {esc(_short(note)[:60])}</span>' if note and len(note) < 60 and "\n" not in note else ""
    count = f" · <b>{esc(v)}×</b>" if isinstance(v, int) and v > 1 else ""
    return f"{esc(text)}{count}{tail}"


def _track(c):
    """Three dots, by hand → your setup → your own tools, filled up to the highest rung seen."""
    top = M.RUNGS.index(c["you"]) if c["you"] in M.RUNGS else -1
    dots = "".join(f'<i class="{"on" if i <= top else ""}"></i>' for i in range(len(M.RUNGS)))
    return f'<span class="px-track px-{CLS[c["you"]]}" aria-hidden="true">{dots}</span>'


def _where(c, esc):
    """Where a part is: who does the work, as a plain label, then the track."""
    return f'<span class="px-pos"><span class="px-where px-{CLS[c["you"]]}">{esc(WORD[c["you"]])}</span>{_track(c)}</span>'


def _pid(d, c):
    return f'px-{d["id"]}-{c["id"]}'


def _rung_col(lv, r, esc):
    if not r:
        return ""
    st = r.get("you")
    if st == "seen":
        head = '<span class="px-ok">✓ seen</span>'
        body = "<ul>" + "".join(f"<li>{_proof(t, s, esc)}</li>" for t, s in _fired(r)[:2]) + "</ul>"
    elif r.get("call_only"):
        head = '<span class="px-ask">only you can tell</span>'
        body = "".join(f'<p class="px-dim">On the call: “{esc(a)}”</p>' for a in _asks(r)[:1])
    elif str(st).startswith("not seen"):
        head = '<span class="px-no">not seen yet</span>'
        body = f'<p class="px-dim">We looked for: {esc(" · ".join(t["name"] for t in r["techniques"]))}</p>'
    else:
        head = '<span class="px-dim">couldn’t check</span>'
        body = '<p class="px-dim">No reading came back for this step. Never counted against you.</p>'
    return (f'<div class="px-rung"><div class="px-rl">{esc(WORD[lv])}</div>'
            f'<div class="px-does">{esc(r["does"])}</div>{head}{body}</div>')


def _how(c, esc):
    rows = []
    for lv in M.RUNGS:
        for t in (c["rungs"].get(lv) or {}).get("techniques", []):
            for s in t["signals"]:
                y = s.get("you") or {}
                mark = "✓" if y.get("fired") is True else "·" if y.get("fired") is False else "?"
                val = "" if y.get("value") is None else _short(str(y.get("value")))
                rows.append(f'<tr><td>{esc(WORD[lv])}</td><td>{esc(t["name"])}</td><td>{esc(SOURCE.get(s["source"], s["source"]))}</td>'
                            f'<td>{mark}</td><td>{esc(val)}</td></tr>')
    return (f'<details class="px-how"><summary>How we checked</summary><table><tr><th>Step</th><th>Technique</th>'
            f'<th>Read by</th><th></th><th>Value</th></tr>{"".join(rows)}</table></details>')


def _part(d, c, esc):
    you = c["you"]
    line = ""
    if you in M.RUNGS and _fired(c["rungs"][you]):
        tech, sig = _fired(c["rungs"][you])[0]
        line = f'<div class="px-proof">{_proof(tech, sig, esc)}</div>'
    n = next_step(c)
    nxt = f'<div class="px-next">{_next_line(n, esc)}</div>' if n else ""
    if you == "can't tell":
        a = [x for lv in M.RUNGS for x in _asks(c["rungs"].get(lv))]
        nxt = ('<div class="px-next px-askline">We couldn’t see this from your laptop'
               + (f" — on the call: “{esc(a[0])}”" if a else "") + "</div>")
    cols = "".join(_rung_col(lv, c["rungs"].get(lv), esc) for lv in M.RUNGS)
    return (f'<details class="px-part" id="{_pid(d, c)}"><summary>'
            f'<div class="px-ph"><span class="px-nm">{esc(c["name"])}</span>{_maybe_more(c, esc)}{_where(c, esc)}</div>'
            f'{line}{nxt}</summary>'
            f'<div class="px-ladder">{cols}</div>{_how(c, esc)}</details>')


def _picture(did, run, T):
    name = PICTURES.get(did)
    path = os.path.join(HERE, f"anatomy_pic_{name}.py") if name else ""
    if not name or not os.path.exists(path):
        return ""
    try:
        spec = importlib.util.spec_from_file_location(f"anatomy_pic_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return f'<div class="px-viz" id="px-viz-{name}">{mod.render(run, T)}</div>'
    except Exception as e:  # noqa: BLE001  a picture that fails is left out; the tab still builds
        print(f"PICTURE FAILED: {name}: {type(e).__name__}: {e}", file=sys.stderr)
        return ""


# ---- the page's four blocks ----

def _count(cs, k):
    return sum(c["you"] == k for c in cs)


def _tiles(d, esc):
    return "".join(f'<span role="button" tabindex="0" class="px-tile px-{CLS[c["you"]]}" data-go="{_pid(d, c)}" '
                   f'title="{esc(c["name"])} — {esc(WORD[c["you"]])}"></span>' for c in d["components"])


def _short_name(d):
    return d["name"].replace(" engineering", "")


def _overview(T, esc):
    discs = T["discs"]
    allc = [c for d in discs for c in d["components"]]
    share = lambda d: _count(d["components"], "L4") / max(1, len(d["components"]))  # noqa: E731
    strong = max(discs, key=lambda d: (share(d), len(d["components"])))
    weak = min(discs, key=lambda d: (share(d), -len(d["components"])))
    cards = "".join(
        f'<div role="button" tabindex="0" class="px-dcard" data-go="px-ch-{d["id"]}"><span class="px-num">{i:02d}</span>'
        f'<b>{esc(_short_name(d))}</b><span class="px-goal">{esc(d["goal"])}</span>'
        f'<span class="px-tiles">{_tiles(d, esc)}</span>'
        f'<span class="px-score"><b>{_count(d["components"], "L4")}</b> of {len(d["components"])} by your own tools</span></div>'
        for i, d in enumerate(discs, 1))
    # one next step per discipline: its part furthest from the top step (first in the file on a tie)
    picks = []
    for d in discs:
        cand = [c for c in d["components"] if next_step(c)]
        if cand:
            c = min(cand, key=lambda c: RANK[c["you"]])
            picks.append((d, c, next_step(c)))
    nexts = "".join(
        f'<div role="button" tabindex="0" class="px-grow" data-go="{_pid(d, c)}"><span class="px-gd">{esc(d["name"])}</span>'
        f'<b>{esc(c["name"])}</b>{_where(c, esc)}'
        f'<span class="px-gn">{_next_line(n, esc)}</span></div>' for d, c, n in picks)
    legend = "".join(f'<span><i class="px-tile px-{CLS[k]}"></i>{esc(w)}</span>' for k, w in WORD.items())
    return (f'<div class="px-hero"><div class="px-kicker">Your setup, part by part</div>'
            f'<h3><b>{_count(allc, "L4")} of {len(allc)}</b> parts of your setup are done by tools you built.</h3>'
            f'<p>Strongest: <b>{esc(strong["name"])}</b> ({_count(strong["components"], "L4")} of {len(strong["components"])}). '
            f'Most room to grow: <b>{esc(weak["name"])}</b> ({_count(weak["components"], "L4")} of {len(weak["components"])}). '
            f'Each square below is one part; click one to jump to it.</p></div>'
            f'<div class="px-map">{cards}</div>'
            f'<div class="px-legend">{legend}<span class="px-dim">Every part has three steps: by hand (you do it) → your setup '
            f'(files, settings and tools you installed do it) → your own tools (hooks, scripts and runners you built do it). Read from your session logs, your settings and git, and an AI reading '
            f'the files that scan found in your most active projects.</span></div>'
            + (f'<div class="px-growbox"><div class="px-kicker">Your next step in each discipline</div>'
               f'<div class="px-growrow">{nexts}</div></div>' if picks else ""))


def _chapter(i, d, nxt, run, T, esc):
    cs = d["components"]
    done = [c for c in cs if c["you"] == "L4"]
    todo = [c for c in cs if c["you"] in RANK]
    unk = [c for c in cs if c["you"] == "can't tell"]
    verdict = (f'{len(done)} of {len(cs)} parts done by your own tools'
               + (f', {len(todo)} {"has" if len(todo) == 1 else "have"} a next step' if todo else "")
               + (f', {len(unk)} we couldn’t see' if unk else "") + ".")
    groups = "".join(f'<h4>{title} <span>{len(g)}</span></h4>' + "".join(_part(d, c, esc) for c in g)
                     for title, g in (("Done by your own tools", done), ("Where to go next", todo),
                                      ("Couldn’t see from your laptop", unk)) if g)
    on = (f'<button class="px-nextch" data-go="px-ch-{nxt["id"]}">Next: {esc(nxt["name"])} →</button>' if nxt
          else '<button class="px-nextch" data-go="px-ch-call">Next: what to bring to your call →</button>')
    return (f'<section class="px-ch" id="px-ch-{d["id"]}"><div class="px-chh"><span class="px-num">{i:02d} / {len(T["discs"]):02d}</span>'
            f'<h3>{esc(d["name"])}</h3><p class="px-goal">{esc(d["goal"])}</p><p class="px-verdict">{verdict}</p>'
            f'<div class="px-tiles big">{_tiles(d, esc)}</div></div>{_picture(d["id"], run, T)}{groups}{on}</section>')


def _call(T, esc):
    items = []
    for d in T["discs"]:
        for c in d["components"]:
            if c["you"] != "can't tell":
                continue
            a = [x for lv in M.RUNGS for x in _asks(c["rungs"].get(lv))]
            items.append(f'<li><button class="px-link" data-go="{_pid(d, c)}">{esc(c["name"])}</button>'
                         f'<span class="px-dim"> · {esc(d["name"])}</span>'
                         + (f"<div>“{esc(a[0])}”</div>" if a else '<div class="px-dim">Show us how you handle this.</div>') + "</li>")
    body = f'<ol class="px-call">{"".join(items)}</ol>' if items else '<p class="px-dim">Nothing: every part could be checked from your laptop.</p>'
    return (f'<section class="px-ch" id="px-ch-call"><div class="px-chh"><span class="px-num">✓</span><h3>For your call</h3>'
            f'<p class="px-goal">The parts no laptop can see. They are never counted against you: bring them to the call and '
            f'show us.</p></div>{body}</section>')


def _signals(T, esc):
    """Every rung's techniques and signals, as read: the detail behind the walkthrough, for whoever wants the proof."""
    out = []
    for d in T["discs"]:
        out.append(f'<div class="disc"><div class="dh"><h3>{esc(d["name"])}</h3><p>{esc(d["goal"])}</p></div>')
        for c in d["components"]:
            marks = "".join(f'<span class="mk {_mark(c["rungs"].get(lv))[0]}" title="{lv}">{lv} {_mark(c["rungs"].get(lv))[1]}</span>'
                            for lv in M.RUNGS)
            body = []
            for lv in M.RUNGS:
                r = c["rungs"].get(lv)
                if not r:
                    continue
                techs = []
                for t in r["techniques"]:
                    sigs = "".join(
                        f'<div class="sg"><span class="src {"scan" if s["source"] == "SCAN" else "ai" if "AI" in s["source"] else ""}">'
                        f'{esc(SOURCE.get(s["source"], s["source"]))}</span>{_you(s.get("you"), esc)}</div>' for s in t["signals"])
                    techs.append(f'<div class="tech">↳ {esc(t["name"])}</div>{sigs}')
                body.append(f'<div class="rung"><div class="rh"><b>{lv} · {esc(WORD[lv])}</b>{esc(r["does"])}</div>{"".join(techs)}</div>')
            n = next_step(c)
            nxt = f'<span class="nl">{_next_line(n, esc)}</span>' if n else ""
            out.append(f'<details><summary><span class="nm">{esc(c["name"])}{nxt}</span><span class="marks">{marks}</span>'
                       f'{_maybe_more(c, esc)}<span class="w {TONE[c["you"]]}">{esc(WORD[c["you"]])}</span></summary>'
                       f'{"".join(body)}</details>')
        out.append("</div>")
    return ('<details class="px-raw"><summary>Every signal: each step\'s techniques and how each was read</summary>'
            '<p class="lead">A part we couldn\'t check is never counted against you.</p>' + "".join(out) + "</details>")


def render(ctx):
    esc, run = ctx["esc"], ctx["run"]
    if not os.path.exists(os.path.join(run, "anatomy-scan.json")):
        return '<p class="empty">This part needs the anatomy step (run.py pre runs it). It isn\'t in this run.</p>'
    try:
        import yaml
        A = yaml.safe_load(open(ANATOMY)) or {}
    except Exception as e:  # noqa: BLE001  the page must still build without this view's data
        return f'<p class="empty">The anatomy file could not be read: {esc(e)}</p>'
    T = M.tree(A, M.inventory(ANATOMY, A), M.load_run(run))
    discs = T["discs"]
    steps = ("".join(f'<button data-go="px-ch-{d["id"]}"><span>{i}</span>{esc(_short_name(d))}</button>'
                     for i, d in enumerate(discs, 1))
             + '<button data-go="px-ch-call"><span>✓</span>For your call</button>')
    chapters = "".join(_chapter(i, d, discs[i] if i < len(discs) else None, run, T, esc) for i, d in enumerate(discs, 1))
    return (f'<div class="px">{_overview(T, esc)}<nav class="px-steps" aria-label="Chapters">{steps}</nav>'
            f'{chapters}{_call(T, esc)}{_signals(T, esc)}</div>{JS}')
