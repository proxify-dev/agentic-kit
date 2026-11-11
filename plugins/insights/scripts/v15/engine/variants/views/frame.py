"""The experimental views page (Proxify's own view, not the member's): one page, many views, each answering one
question. A picker at the top switches views on and off (kept in localStorage), so he can keep what he wants.

A view is a module `view_<name>.py` next to this file with:
    ORDER = 30                      # place on the page
    TITLE = "What you built"        # short, sentence case
    QUESTION = "What systems does this setup have, and do they run?"
    PROXIFY_ONLY = False            # True = never on the member's copy (level read, internal method)
    CSS = ".v-built .x{...}"        # its own CSS, every selector starts with .v-<name>
    def render(ctx) -> str          # the view's inner HTML (no <section>, no title: the frame adds those)
A badge that shows a practice's level carries `lvl_tip.attrs(practice_id, level)`; lvl_tip.py holds the one tooltip.
ctx = load(run_dir): every JSON the run wrote, plus ctx["vm"] = common.load(...) (the member wording) and helpers.

Build: uv run --no-project --with pyyaml python frame.py [RUN_DIR] [OUT.html] [--member]  (pyyaml: the substrate tab reads coverage/tooling.yaml)   (defaults: the 11-02 cheap run -> RUN_DIR/variants/views.html)
"""
import glob
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import common  # noqa: E402  the member wording: clean(), you(), RUNG_WORD
import theme   # noqa: E402  dark mode + Dark Reader handover
sys.path.insert(0, HERE)
import check_kit  # noqa: E402  how a verdict was reached, the same block on every view
import lvl_tip  # noqa: E402  the one tooltip every level badge uses (the substrate's ladder_short)
import card_kit  # noqa: E402  the one card, and the words on it
import panel_kit  # noqa: E402  the one side panel for every practice, problem and term
import finder_kit  # noqa: E402  the ⌘K finder: views, practices, problems, terms

FILES = ["v15-result.json", "anatomy.json", "inventory.json", "cheap-artifacts.json", "cheap-problems.json",
         "evidence.json", "comprehension-debt.json", "cheap-vs-lab.json", "portable-checks.json", "portable-rows.json"]


def esc(s):
    return html.escape("" if s is None else str(s))


def load(run, member=False):
    ctx = {"run": run, "member": member, "esc": esc, "clean": common.clean, "RUNG_WORD": common.RUNG_WORD}
    for f in FILES:
        p = os.path.join(run, f)
        ctx[f.rsplit(".", 1)[0].replace("-", "_")] = json.load(open(p)) if os.path.exists(p) else None
    digest = os.path.join(run, "cheap-digest.md")
    ctx["digest_md"] = open(digest).read() if os.path.exists(digest) else ""
    ctx["vm"] = common.load(os.path.join(run, "v15-result.json"))
    return ctx


def views():
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "view_*.py"))):
        spec = importlib.util.spec_from_file_location(os.path.basename(path)[:-3], path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.NAME = os.path.basename(path)[5:-3]
        out.append(m)
    return sorted(out, key=lambda m: getattr(m, "ORDER", 99))


# The report's navigation: five groups as top tabs, a second row only where a group holds more than one view.
# A view module not listed here still shows, in a "More" group, so a new view never disappears.
GROUPS = [("setup", "My setup", ["anatomy", "areas", "built", "who", "overview", "systems", "numbers"]),
          ("practices", "Practices", ["board", "found", "practices", "layers"]),
          ("problems", "Problems", ["problems"]),
          ("next", "Next steps", ["next"]),
          ("share", "Share with Proxify", ["share"]),
          ("substrate", "Substrate", ["sub_board", "sub_levels", "sub_context", "sub_prompt", "sub_verification", "sub_harness", "sub_orchestration",
                                      "vocabulary", "playbook", "crosswalk", "questions"]),
          ("calls", "Calls", ["callsheet"]),
          ("internal", "Internal", ["capabilities", "checks", "level", "trust"])]
TAB = {"anatomy": "Part by part", "board": "Board", "areas": "By discipline", "who": "Who did it", "overview": "At a glance", "systems": "Your systems", "built": "What you've built", "numbers": "Numbers", "found": "What you have",
       "practices": "Precisely", "layers": "Where it lives", "problems": "Problems", "next": "Next steps",
       "sub_board": "Overview", "sub_levels": "Levels", "sub_context": "Context", "sub_prompt": "Prompt", "sub_verification": "Verification",
       "sub_harness": "Harness", "sub_orchestration": "Orchestration", "level": "Level read", "trust": "Trust vs lab", "checks": "Checks",
       "vocabulary": "Vocabulary", "playbook": "Playbook", "crosswalk": "From v13", "questions": "Open questions",
       "callsheet": "Call sheet", "capabilities": "What it can do",
       "share": "Share with Proxify"}

CSS = """
*{box-sizing:border-box}html{-webkit-font-smoothing:antialiased}
body{margin:0;background:#F9FAFB;color:#15172F;font:400 14px/1.6 Inter,system-ui,sans-serif;font-variant-numeric:tabular-nums}
::selection{background:#D5D5FE;color:#15172F}
a{color:#3C3ED4;text-underline-offset:3px}
:focus-visible{outline:2px solid #5258FB;outline-offset:2px;border-radius:4px}
summary{cursor:pointer}
html{scrollbar-color:#D0D5DD transparent}
.rp-wrap{max-width:1120px;margin:0 auto;padding:0 32px}
.rp-top{background:#FFFFFF;border-bottom:1px solid #EAECF0;position:sticky;top:0;z-index:30}
.rp-bar{display:flex;align-items:center;gap:16px;min-height:64px}
.rp-mark{flex:none;width:28px;height:28px;border-radius:8px;background:#5258FB;display:grid;place-items:center}
.rp-mark svg{width:16px;height:16px;color:#FFFFFF}
.rp-id{min-width:0}
.rp-id b{display:block;font-size:14px;font-weight:600;line-height:1.3}
.rp-id span{display:block;font-size:12px;color:#667085;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rp-top .theme-switch{position:static;margin-left:auto;box-shadow:none;background:#F9FAFB}
.rp-tabs{display:flex;gap:24px;overflow-x:auto;scrollbar-width:none}
.rp-tabs::-webkit-scrollbar{display:none}
.rp-tabs a{flex:none;display:block;padding:12px 0 10px;border-bottom:2px solid transparent;color:#475467;text-decoration:none;font-weight:500;font-size:14px}
.rp-tabs a:hover{color:#15172F}
.rp-tabs a[aria-current=page]{color:#3C3ED4;border-bottom-color:#5258FB}
.rp-tabs .rp-sep{flex:none;width:1px;background:#EAECF0;margin:10px 0}
main.rp-wrap{padding-top:32px;padding-bottom:96px}
.rp-sub[hidden]{display:none}
.rp-sub{display:flex;gap:4px;margin:0 0 32px;padding:4px;background:#F4F5F7;border-radius:8px;width:max-content;max-width:100%;overflow-x:auto}
.rp-sub a{flex:none;padding:6px 12px;border-radius:6px;color:#475467;text-decoration:none;font-size:14px;font-weight:500}
.rp-sub a:hover{color:#15172F}
.rp-sub a[aria-current=page]{background:#FFFFFF;color:#15172F;box-shadow:0 1px 2px rgba(16,24,40,.08)}
.view{display:none}.view.on{display:block}
.view,.view [id]{scroll-margin-top:136px}
.view>header{margin:0 0 32px;max-width:72ch}
.view h2{font-size:24px;font-weight:600;line-height:1.3;letter-spacing:-.01em;margin:0}
.view .q{color:#475467;margin:4px 0 0}
.rp-note{font-size:12px;color:#667085;margin:8px 0 0}
.rp-key{font-size:12px;color:#667085;margin:-16px 0 24px}
.empty{color:#667085}
@media (max-width:760px){.rp-wrap{padding-left:16px;padding-right:16px}.rp-bar{gap:12px}.rp-id span{white-space:normal}
  .rp-top .theme-switch button{font-size:0;gap:0;padding:6px 8px}.rp-top .theme-switch svg{width:16px;height:16px}}
"""

JS = """
(function(){
  var views=[].slice.call(document.querySelectorAll('.view')), first=views[0].id.slice(2), cur=first, PANEL=/^[pct][/]/;
  window.kpView=function(){return cur};
  function showView(raw){
    // #<view> opens a view; #<any id inside a view> opens that view and scrolls to the element (e.g. #what-you-built)
    var name=raw||first, el=document.getElementById('v-'+name), target=null;
    if(!el){var t=document.getElementById(name), v=t&&t.closest('.view');
      if(v){target=t;el=v;name=v.id.slice(2)}else{name=first;el=document.getElementById('v-'+name)}}
    cur=name;var g=el.dataset.group;
    views.forEach(function(v){v.classList.toggle('on',v===el)});
    document.querySelectorAll('.rp-tabs a').forEach(function(a){a.toggleAttribute('aria-current',a.dataset.group===g);if(a.dataset.group===g)a.setAttribute('aria-current','page')});
    document.querySelectorAll('.rp-sub').forEach(function(s){s.hidden=s.dataset.group!==g});
    document.querySelectorAll('.rp-sub a').forEach(function(a){a.removeAttribute('aria-current');if(a.hash==='#'+name)a.setAttribute('aria-current','page')});
    document.title=el.querySelector('h2').textContent+' · Setup report';
    return target;
  }
  function show(){
    // #p/<id>, #c/<id>, #t/<id> open the side panel on that item and leave the tab where it is (the first tab on a fresh load)
    var raw=(location.hash||'').replace('#','');
    if(PANEL.test(raw)){
      if(!document.querySelector('.view.on'))showView('');
      var k=raw.split('/');window.kpOpen&&window.kpOpen(k[0],decodeURIComponent(k.slice(1).join('/')));return 'keep'}
    window.kpClose&&window.kpClose();
    return showView(raw);
  }
  function go(){var t=show();if(t==='keep')return;if(t)t.scrollIntoView();else scrollTo(0,0)}
  addEventListener('hashchange',go);go();
})();
"""


def archive_previous(out, page):
    """His rule (11-04): a rebuild never loses the page it replaces. Before `out` is overwritten, the old page is copied
    to archive/ beside it as <name>-v<NNN>-<YYYY-MM-DD-HHMM>.html: NNN counts up, the time is when the old page was built.
    Nothing is copied when the new page is identical, or when the old one is already the newest copy in archive/."""
    if not os.path.exists(out):
        return None
    old = open(out, encoding="utf-8").read()
    if old == page:
        return None
    folder = os.path.join(os.path.dirname(out), "archive")
    os.makedirs(folder, exist_ok=True)
    name = os.path.splitext(os.path.basename(out))[0]
    kept = sorted((int(m.group(1)), f) for f in os.listdir(folder) if (m := re.match(rf"{re.escape(name)}-v(\d+)-.*\.html$", f)))
    if kept and open(os.path.join(folder, kept[-1][1]), encoding="utf-8").read() == old:
        return None
    built = time.strftime("%Y-%m-%d-%H%M", time.localtime(os.path.getmtime(out)))
    copy = os.path.join(folder, f"{name}-v{(kept[-1][0] if kept else 0) + 1:03d}-{built}.html")
    shutil.copy2(out, copy)
    return copy


TESTERS = "origin/feat/v15"  # the branch the testers install the plugin from


def built_from():
    """One line for the page header: when it was built, from which branch and commit, and how many commits the
    testers' copy (TESTERS) doesn't have yet. His 11-05 "struggling to determine did we make any change or what's
    pending": the page says what it is. Only the time when the code isn't in a git checkout."""
    when = time.strftime("built %m-%d %H:%M")
    def git(*a):
        return subprocess.run(["git", "-C", HERE, *a], capture_output=True, text=True, timeout=5).stdout.strip()
    try:
        branch, commit = git("rev-parse", "--abbrev-ref", "HEAD"), git("rev-parse", "--short", "HEAD")
    except Exception:  # noqa: BLE001 - no git: the time alone
        return when
    if not commit:
        return when
    line = f"{when} from {branch} {commit}"
    ahead = git("rev-list", "--count", f"{TESTERS}..HEAD")
    if ahead.isdigit():
        line += f" · {ahead} change{'s' if ahead != '1' else ''} testers don't have yet" if ahead != "0" else " · same as what testers have"
    if git("status", "--porcelain", "--untracked-files=no"):
        line += " · plus uncommitted edits"
    return line


def build(run, out, member=False):
    """member=True renders the member's copy: no view marked PROXIFY_ONLY, no level key, and the views that read ctx["member"] show
    who carries a practice in words instead of a level (his 10-26 ruling)."""
    ctx = load(run, member)
    card_kit.set_logs(ctx)  # how much log the run stands on, shown next to every "not seen"
    vs = [v for v in views() if not (member and getattr(v, "PROXIFY_ONLY", False))]
    by_name = {v.NAME: v for v in vs}
    groups = [(g, t, [n for n in names if n in by_name]) for g, t, names in GROUPS]
    listed = {n for _, _, names in groups for n in names}
    extra = [v.NAME for v in vs if v.NAME not in listed]
    if extra:
        groups.append(("more", "More", extra))
    groups = [g for g in groups if g[2]]
    sections, css, tabs, subs = [], [CSS, check_kit.CSS, card_kit.CSS, panel_kit.CSS, finder_kit.CSS] + ([] if member else [lvl_tip.CSS]), [], []
    for gid, gtitle, names in groups:
        if gid == "internal":
            tabs.append('<span class="rp-sep" aria-hidden="true"></span>')
        tabs.append(f'<a href="#{names[0]}" data-group="{gid}">{esc(gtitle)}</a>')
        if len(names) > 1:
            subs.append(f'<nav class="rp-sub" data-group="{gid}" aria-label="{esc(gtitle)} views" hidden>'
                        + "".join(f'<a href="#{n}">{esc(TAB.get(n, by_name[n].TITLE))}</a>' for n in names) + "</nav>")
        for n in names:
            v = by_name[n]
            try:
                body = v.render(ctx)
            except Exception as e:  # one broken view must not take the page down
                body = f'<p class="empty">This view failed to build: {esc(type(e).__name__)}: {esc(e)}</p>'
                print(f"VIEW FAILED: {n}: {type(e).__name__}: {e}", file=sys.stderr)  # the page still builds, so say it here
            css.append(getattr(v, "CSS", ""))
            note = ('<p class="rp-note">Internal: never on the member\'s copy.</p>'
                    if getattr(v, "PROXIFY_ONLY", False) else "")
            sections.append(f'<section class="view v-{n}" id="v-{n}" data-group="{gid}"><header><h2>{esc(v.TITLE)}</h2>'
                            f'<p class="q">{esc(getattr(v, "QUESTION_MEMBER", v.QUESTION) if member else v.QUESTION)}</p>{note}</header>{body}</section>')
    finder_views = [(n, TAB.get(n, by_name[n].TITLE), gtitle) for _, gtitle, names in groups for n in names]
    inv = ctx.get("inventory") or {}
    meta = inv.get("meta", {})
    who = (inv.get("person", {}).get("identities") or [""])[0]
    level_key = "" if member else ('<p class="rp-key" title="What each practice level means">Levels: ' + " · ".join(
        f"{k} {w[0].lower() + w[1:]}" for k, w in sorted(common.RUNG_WORD.items())) + "</p>")
    stamp = built_from()
    print(stamp)
    mark = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
            'stroke-linejoin="round" aria-hidden="true"><path d="M3 4.5h10M3 8h7M3 11.5h4"/></svg>')
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Setup report</title><link rel=preconnect href=https://fonts.googleapis.com>
<link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap">
<style>{"".join(css)}</style></head><body>
<header class="rp-top"><div class="rp-wrap"><div class="rp-bar"><span class="rp-mark">{mark}</span>
<div class="rp-id"><b>Agentic setup report</b><span>{esc(who)} · last {meta.get("days", 30)} days to {esc(meta.get("to", ""))} · experimental · {esc(stamp)}</span>{"<span>" + esc(card_kit.header_note()) + "</span>" if card_kit.header_note() else ""}</div>
{getattr(by_name.get("share"), "BUTTON", "")}{finder_kit.BUTTON}{theme.SWITCH}</div>
<nav class="rp-tabs" aria-label="Report sections">{"".join(tabs)}</nav></div></header>
{panel_kit.ASIDE}{panel_kit.data_script(ctx)}{panel_kit.JS}
{finder_kit.DIALOG}{finder_kit.data_script(ctx, finder_views)}{finder_kit.JS}
<main class="rp-wrap">{level_key}{"".join(subs)}{"".join(sections)}</main><script>{JS}</script>{check_kit.JS}{"" if member else lvl_tip.json_script() + lvl_tip.JS}</body></html>"""
    if member:  # a check's text can point at another check by its level code ("see L4#2"); the member's copy never names a level
        page = re.sub(r"\bL[1-4]#\d+\b", "another check", page)
    page = theme.apply(page, switch=False)
    kept = archive_previous(out, page)
    if kept:
        print(f"kept the previous page: {kept}")
    open(out, "w", encoding="utf-8").write(page)
    sizes = sorted(set(re.findall(r"font-size:\s*([0-9.]+px)", page)))
    print(f"built {out} · {len(vs)} views in {len(groups)} sections · font sizes {sizes}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--member"]
    run = args[0] if args else os.path.expanduser("~/.cache/setup-snapshot/cheap-2026-11-02")
    out = args[1] if len(args) > 1 else os.path.join(run, "variants", "views.html")
    build(run, out, member="--member" in sys.argv)
