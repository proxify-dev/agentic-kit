"""The setup page: one page, many views, each answering one question. The member's copy (--member) shows four of
them (MEMBER_GROUPS); Proxify's copy shows those four and then every other view, grouped.

A view is a module `view_<name>.py` next to this file with:
    ORDER = 30                      # place on the page
    TITLE = "What you built"        # short, sentence case
    QUESTION = "What systems does this setup have, and do they run?"
    PROXIFY_ONLY = False            # True = never on the member's copy (level read, internal method)
    CSS = ".v-built .x{...}"        # its own CSS, every selector starts with .v-<name>
    def render(ctx) -> str          # the view's inner HTML (no <section>, no title: the frame adds those)
A badge that shows a practice's level carries `lvl_tip.attrs(practice_id, level)`; lvl_tip.py holds the one tooltip.
ctx = load(run_dir): every JSON the run wrote, plus ctx["vm"] = common.load(...) (the member wording) and helpers.

Build: uv run --no-project --with pyyaml python frame.py [RUN_DIR] [OUT.html] [--member] [--no-level] [--masked]  (pyyaml: the substrate tab reads coverage/tooling.yaml)   (defaults: the 11-02 cheap run -> RUN_DIR/variants/views.html)
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
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("clock", os.path.join(HERE, "..", "..", "..", "..", "setup-snapshot", "experiments", "2026-11-02-evidence", "clock.py"))
clock = importlib.util.module_from_spec(_spec)   # the one place the calendar clock is read
_spec.loader.exec_module(clock)
sys.path.insert(0, os.path.dirname(HERE))
import common  # noqa: E402  the member wording: clean(), you(), RUNG_WORD
import theme   # noqa: E402  dark mode + Dark Reader handover
sys.path.insert(0, HERE)
import check_kit  # noqa: E402  how a verdict was reached, the same block on every view
import lvl_tip  # noqa: E402  the one tooltip every level badge uses (the substrate's ladder_short)
import card_kit  # noqa: E402  the one card, and the words on it
import panel_kit  # noqa: E402  the one side panel for every practice, problem and term
import finder_kit  # noqa: E402  the ⌘K finder: views, practices, problems, terms
import table_kit  # noqa: E402  the one list: a table whose rows open the side panel, with the strip, group heading and meter around it

FILES = ["v15-result.json", "anatomy.json", "inventory.json", "cheap-artifacts.json", "cheap-problems.json",
         "evidence.json", "comprehension-debt.json", "cheap-vs-lab.json", "portable-checks.json", "portable-rows.json",
         "setup-read.json",  # the agent's read of their files (view_review, the first tab)
         "setup-map.json",   # the code's map of those files: each memory store's shape (view_review's grid)
         "craft-read.json"]  # how well what they use most is made (view_built); advice only, nothing in the engine opens it


def esc(s):
    return html.escape("" if s is None else str(s))


# The owner's ruling (11-12): the instruction file is called AGENTS.md, CLAUDE.md is its known synonym, and a reader is told so once per
# tab. A tab whose text names AGENTS.md carries this line under its title, so the first thing a reader meets is its meaning.
# (.claude/rules/ files are a separate, extra mechanism: a view that mentions them names them itself.)
ALL_BUTTON = ('<button type="button" class="rp-all" id="rp-all" aria-pressed="false" title="Lay every tab and panel out on one page, '
              'so the browser\'s find (Cmd/Ctrl+F) searches all of it">Show everything</button>')
AGENTS_MD_NOTE = '<p class="rp-note">AGENTS.md (or CLAUDE.md) is the file the agent reads at the start of every session.</p>'


def agents_md_note(body):
    """AGENTS_MD_NOTE when the view's visible text says AGENTS.md (tags and their attributes don't count), else nothing."""
    return AGENTS_MD_NOTE if "AGENTS.md" in re.sub(r"<[^>]+>", " ", body) else ""


def load(run, member=False):
    ctx = {"run": run, "member": member, "esc": esc, "clean": common.clean, "RUNG_WORD": common.RUNG_WORD}
    for f in FILES:
        p = os.path.join(run, f)
        ctx[f.rsplit(".", 1)[0].replace("-", "_")] = json.load(open(p)) if os.path.exists(p) else None
    digest = os.path.join(run, "cheap-digest.md")
    ctx["digest_md"] = open(digest).read() if os.path.exists(digest) else ""
    ctx["vm"] = common.load(os.path.join(run, "v15-result.json"))
    return ctx


def views(page=None):
    """The view modules of one page. A module with PAGE = "<name>" belongs to that page only (the system map's views); one
    without it is a view of the setup report itself."""
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "view_*.py"))):
        spec = importlib.util.spec_from_file_location(os.path.basename(path)[:-3], path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.NAME = os.path.basename(path)[5:-3]
        if getattr(m, "PAGE", None) == page:
            out.append(m)
    return sorted(out, key=lambda m: getattr(m, "ORDER", 99))


# The report's navigation: groups as top tabs, a second row only where a group holds more than one view.
# Both copies open on the same four tabs, which tell one story: the written review, the level it adds up to, what the
# person built, and the summary they can send. They rest on the setup read and on plain things with one home level.
STORY = [("review", "Review", ["review"]),  # first: the page opens on what the setup is and why, in sentences (11-11)
         ("mylevel", "Your level", ["mylevel"]),
         ("built", "What you've built", ["built"]),
         ("share", "Share with Proxify", ["share"])]
# The member's copy is exactly STORY (11-11). A view that is not listed here never reaches a member, so a new view has
# to be added on purpose. What to do next lives in Review ("Change this first") and in Your level, not in a tab of its own.
MEMBER_GROUPS = STORY
# Proxify's copy adds the substrate, the call sheet and the internal checks after the four. The twelve views built on
# the 33 practices and their four rungs (Part by part, By discipline, Board...) are gone from both copies (11-11): the
# level no longer rests on them, on one page "Part by part" said session resets were "not seen yet" while the
# level tab ticked the same thing, and he ruled they shouldn't be on the staff page either.
# A view module listed nowhere still shows here, in a "More" group, so a new view never disappears.
GROUPS = STORY + [
    ("substrate", "Substrate", ["sub_board", "sub_levels", "sub_context", "sub_prompt", "sub_verification", "sub_harness", "sub_orchestration",
                                "vocabulary", "playbook", "crosswalk", "questions"]),
    ("calls", "Calls", ["callsheet"]),
    ("internal", "Internal", ["capabilities", "checks", "level", "trust"])]
TAB = {"review": "Review", "mylevel": "Your level", "built": "What you've built",
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
.rp-id{min-width:0;flex:1}
.rp-id b{display:block;font-size:14px;font-weight:600;line-height:1.3}
.rp-id span{display:block;font-size:12px;color:#667085;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rp-top .theme-switch{position:static;margin-left:auto;box-shadow:none;background:#F9FAFB}
.rp-top .theme-switch button{font-size:0;gap:0;padding:6px}.rp-top .theme-switch svg{width:15px;height:15px}
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
.rp-all{flex:none;font:inherit;font-size:12px;font-weight:500;line-height:1;color:#475467;background:#F9FAFB;border:1px solid #EAECF0;border-radius:8px;padding:8px 10px;cursor:pointer}
.rp-all:hover{color:#15172F}.rp-all[aria-pressed=true]{background:#F5F5FF;color:#3C3ED4;border-color:#C7C9FD}
.view,.view [id]{scroll-margin-top:136px}
.view>header{margin:0 0 32px;max-width:72ch}
.view>header.rp-sr{position:absolute;width:1px;height:1px;margin:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.view h2{font-size:24px;font-weight:600;line-height:1.3;letter-spacing:-.01em;margin:0}
.view .q{color:#475467;margin:4px 0 0}
.rp-note{font-size:12px;color:#667085;margin:8px 0 0}
.empty{color:#667085}
@media (max-width:760px){.rp-wrap{padding-left:16px;padding-right:16px}.rp-bar{gap:12px}main.rp-wrap{padding-top:24px}}
"""

JS = """
(function(){
  // A tab that is not showing is `hidden="until-found"`, never display:none: the browser's find-in-page (Cmd/Ctrl+F) searches it, and a
  // match there fires `beforematch`, which switches to that tab. Browsers without until-found (Safari, Firefox) hide it plainly:
  // for them "Show everything" lays every tab and panel out on one page. Nothing here listens for a key: find is the browser's.
  var views=[].slice.call(document.querySelectorAll('.view')), first=views[0].id.slice(2), cur=first, PANEL=/^[pctm][/]/, everything=false,
      UF='until-found', btn=document.getElementById('rp-all'), reopened=[];
  window.kpView=function(){return cur};
  function showView(raw){
    // #<view> opens a view; #<any id inside a view> opens that view and scrolls to the element (e.g. #what-you-built)
    var name=raw||first, el=document.getElementById('v-'+name), target=null;
    if(!el){var t=document.getElementById(name), v=t&&t.closest('.view');
      if(v){target=t;el=v;name=v.id.slice(2)}else{name=first;el=document.getElementById('v-'+name)}}
    cur=name;var g=el.dataset.group;
    views.forEach(function(v){v.classList.toggle('on',v===el);if(v===el||everything)v.removeAttribute('hidden');else v.setAttribute('hidden',UF)});
    document.querySelectorAll('.rp-tabs a').forEach(function(a){a.toggleAttribute('aria-current',a.dataset.group===g);if(a.dataset.group===g)a.setAttribute('aria-current','page')});
    document.querySelectorAll('.rp-sub').forEach(function(s){s.hidden=s.dataset.group!==g});
    document.querySelectorAll('.rp-sub a').forEach(function(a){a.removeAttribute('aria-current');if(a.hash==='#'+name)a.setAttribute('aria-current','page')});
    document.title=el.querySelector('h2').textContent+' · '+document.title.split(' · ').pop();
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
  function go(){var t=show();if(t==='keep')return;if(everything&&!t)t=document.getElementById('v-'+cur);if(t)t.scrollIntoView();else scrollTo(0,0)}
  addEventListener('hashchange',go);go();
  document.addEventListener('beforematch',function(e){var v=e.target.closest&&e.target.closest('.view');
    if(!v||everything||v.id.slice(2)===cur)return;showView(v.id.slice(2));if(history.replaceState)history.replaceState(null,'','#'+v.id.slice(2))});
  function setEverything(on){
    everything=on;btn.setAttribute('aria-pressed',on);btn.textContent=on?'Back to tabs':'Show everything';
    if(on){views.forEach(function(v){v.removeAttribute('hidden')});
      reopened=[].filter.call(document.querySelectorAll('details'),function(d){return !d.open});reopened.forEach(function(d){d.open=true})}
    else{reopened.forEach(function(d){d.open=false});reopened=[];showView(cur)}
    if(window.kpFlat)window.kpFlat(on)}
  btn.addEventListener('click',function(){setEverything(!everything)});
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


INSTALL_BRANCH = "origin/feat/v15"  # the branch the plugin is installed from


def built_from():
    """One line for the page header: when it was built, from which branch and commit, and how many commits the
    installed copy (INSTALL_BRANCH) doesn't have yet. The 11-05 ask "struggling to determine did we make any change or what's
    pending": the page says what it is. Only the time when the code isn't in a git checkout."""
    when = time.strftime("built %m-%d %H:%M", time.localtime(clock.now()))
    def git(*a):
        return subprocess.run(["git", "-C", HERE, *a], capture_output=True, text=True, timeout=5).stdout.strip()
    try:
        branch, commit = git("rev-parse", "--abbrev-ref", "HEAD"), git("rev-parse", "--short", "HEAD")
    except Exception:  # noqa: BLE001 - no git: the time alone
        return when
    if not commit:
        return when
    line = f"{when} from {branch} {commit}"
    ahead = git("rev-list", "--count", f"{INSTALL_BRANCH}..HEAD")
    if ahead.isdigit():
        line += f" · {ahead} change{'s' if ahead != '1' else ''} the installed copy doesn't have yet" if ahead != "0" else " · same as the installed copy"
    if git("status", "--porcelain", "--untracked-files=no"):
        line += " · plus uncommitted edits"
    return line


def window_line(meta):
    """The header's one muted line: "Last 30 days · read 11 Nov"."""
    days, to = meta.get("days", 30), str(meta.get("to") or "")
    try:
        t = time.strptime(to, "%Y-%m-%d")
        to = f"{t.tm_mday} {time.strftime('%b', t)}"
    except ValueError:
        pass
    return f"Last {days} days" + (f" · read {to}" if to else "")


def build(run, out, member=False, level=True, masked=False, page=None):
    """member=True renders the member's copy: only the views in MEMBER_GROUPS (never one marked PROXIFY_ONLY),
    no side panel and no finder (both open practice cards, which that copy no longer has a tab for). level=False drops
    every view marked LEVEL_VIEW, for the copy with no level at all. masked=True hides the names of the person's own
    things (a skill is own-skill-N): for a copy made to leave the laptop. The person's own page names them.
    page = a page of its own views instead of the setup report (the system map): {"name", "title", "line", "groups":
    [(id, tab title, [view names])], "ctx": what its views read, "panels": {key: panel spec} for the side panel}. It has no
    run, so `run` is unused, and no finder: its items are not practices, problems or terms."""
    ctx = ({"run": run, "member": False, "esc": esc, "clean": common.clean, "RUNG_WORD": common.RUNG_WORD, **page["ctx"]}
           if page else load(run, member))
    ctx["level"] = level  # a view may point at the level tab only when this page has one
    ctx["masked"] = masked
    card_kit.set_logs(ctx)  # how much log the run stands on, shown next to every "not seen"
    inv = ctx.get("inventory") or {}
    meta = inv.get("meta", {})
    stamp = built_from()
    print(stamp)
    # The header says what the page is and when it was read, nothing more (11-11 "users get lost"). Who it was read
    # for, Claude Code's version and log setting, and how this copy was built sit in the Review tab's "About this report".
    ctx["about"] = {"who": (inv.get("person", {}).get("identities") or [""])[0], "days": meta.get("days", 30),
                    "to": meta.get("to", ""), "laptop": card_kit.header_note(), "stamp": stamp}
    vs = [v for v in views(page["name"] if page else None) if not (member and getattr(v, "PROXIFY_ONLY", False)) and (level or not getattr(v, "LEVEL_VIEW", False))]
    by_name = {v.NAME: v for v in vs}
    groups = [(g, t, [n for n in names if n in by_name]) for g, t, names in (page["groups"] if page else MEMBER_GROUPS if member else GROUPS)]
    listed = {n for _, _, names in groups for n in names}
    extra = [v.NAME for v in vs if v.NAME not in listed]
    if extra and not member and not page:
        groups.append(("more", "More", extra))
    groups = [g for g in groups if g[2]]
    vs = [by_name[n] for _, _, names in groups for n in names]  # the views this copy really shows
    sections, css, tabs, subs = [], [CSS, check_kit.CSS, card_kit.CSS] + (
        [panel_kit.CSS, table_kit.CSS] if page else [] if member else [panel_kit.CSS, finder_kit.CSS, lvl_tip.CSS]), [], []
    for gid, gtitle, names in groups:
        if gid in ("substrate", "internal"):  # a rule in the tab bar: the four tabs of the story | substrate, calls | internal
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
            gloss = "" if page else agents_md_note(body)
            # HIDE_HEADER: a view that opens on its own picture keeps its title for screen readers and the window title
            head = ' class="rp-sr"' if getattr(v, "HIDE_HEADER", False) else ""
            sections.append(f'<section class="view v-{n}" id="v-{n}" data-group="{gid}" hidden="until-found"><header{head}><h2>{esc(v.TITLE)}</h2>'
                            f'<p class="q">{esc(getattr(v, "QUESTION_MEMBER", v.QUESTION) if member else v.QUESTION)}</p>{note}</header>{gloss}{body}</section>')
    finder_views = [(n, TAB.get(n, by_name[n].TITLE), gtitle) for _, gtitle, names in groups for n in names]
    # no level key above the tabs (11-11): its practice words ("L4 runs without you") sat above the Review's level, named
    # by things ("Builds the harness around the agent"), so the staff page showed two names for one level. The words stay
    # in common.RUNG_WORD for the tabs that show a practice's level.
    # the side panel (a practice's card) and the ⌘K finder (practices, problems, terms): Proxify's copy only, where the
    # substrate, call sheet and internal tabs open practice and problem cards
    lab = (f"{panel_kit.panels_html(page['panels'])}{panel_kit.JS}" if page else
           "" if member else (f"{panel_kit.data_html(ctx)}{panel_kit.JS}\n"
                             f"{finder_kit.DIALOG}{finder_kit.data_script(ctx, finder_views)}{finder_kit.JS}"))
    mark = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
            'stroke-linejoin="round" aria-hidden="true"><path d="M3 4.5h10M3 8h7M3 11.5h4"/></svg>')
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(page["title"]) if page else "Setup report"}</title><link rel=preconnect href=https://fonts.googleapis.com>
<link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap">
<style>{"".join(css)}</style></head><body>
<header class="rp-top"><div class="rp-wrap"><div class="rp-bar"><span class="rp-mark">{mark}</span>
<div class="rp-id"><b>{esc(page["title"]) if page else "Your agentic setup"}</b><span>{esc(page["line"] if page else window_line(meta))}</span></div>
{getattr(by_name.get("share"), "BUTTON", "")}{"" if member or page else finder_kit.BUTTON}{ALL_BUTTON}{theme.SWITCH}</div>
<nav class="rp-tabs" aria-label="Report sections">{"".join(tabs)}</nav></div></header>
{lab}
<main class="rp-wrap">{"".join(subs)}{"".join(sections)}</main><script>{JS}</script>{check_kit.JS}{"" if member or page else lvl_tip.json_script() + lvl_tip.JS}</body></html>"""
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
    args = [a for a in sys.argv[1:] if a not in ("--member", "--no-level", "--masked")]
    run = args[0] if args else os.path.expanduser("~/.cache/setup-snapshot/cheap-2026-11-02")
    out = args[1] if len(args) > 1 else os.path.join(run, "variants", "views.html")
    build(run, out, member="--member" in sys.argv, level="--no-level" not in sys.argv, masked="--masked" in sys.argv)
